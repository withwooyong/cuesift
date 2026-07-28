"""텍스트만으로 판정되는 Tier 0 신호 (요구사항정의서 FR-3.1~FR-3.5).

다섯 개 모두 `hard_fail`이다(§5.3 각주). 검수 예산과 무관하게 항상 검수
큐에 들어간다(FR-6.2).

**이들이 이 프로젝트의 비용 논리를 떠받친다**(§4). 실무 LLM 번역 사고의
상당수 — 미번역 잔존·빈 출력·반복 붕괴·숫자 누락 — 는 LLM 없이 코드만으로
잡힌다. 값비싼 신호는 코드로 못 잡는 의미 오류에만 써야 한다.
"""

from __future__ import annotations

import re
from collections import Counter

from cuesift.segment import Segment, Signal
from cuesift.signals.base import SignalContext, register

# 언어별 고유 문자 범위. 미번역 잔존 판정에 쓴다.
_SCRIPT_RANGES = {
    "ko": re.compile(r"[가-힣ᄀ-ᇿ]"),  # 한글 음절 + 자모
    "ja": re.compile(r"[぀-ゟ゠-ヿ]"),  # 히라가나 + 가타카나
}

# 원문 언어 문자가 이 비율 이상 남으면 미번역으로 본다.
# FR-3.1의 "유의미하게"를 수치화한 것이다. 한 글자만 섞여도 발화하면
# 고유명사 표기 때문에 오탐이 쏟아진다.
_UNTRANSLATED_RATIO = 0.15

# 같은 어절이 이 횟수 이상 반복되면 붕괴로 본다.
# 2회는 'very very good' 같은 자연스러운 강조라 제외한다.
_DEGENERATION_MIN_REPEAT = 3

_NUMBER = re.compile(r"\d+")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


class Untranslated:
    """FR-3.1 — 번역문에 원문 언어 문자가 유의미하게 남아 있다."""

    name = "struct.untranslated"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        # ko→ko(원문 검수 경로)에서 한글이 남는 것은 정상이다.
        if ctx.source_lang == ctx.target_lang:
            return None
        pattern = _SCRIPT_RANGES.get(ctx.source_lang)
        if pattern is None or not seg.target_text:
            return None

        stripped = seg.target_text.strip()
        if not stripped:
            return None

        hits = len(pattern.findall(stripped))
        ratio = hits / len(stripped)
        if ratio < _UNTRANSLATED_RATIO:
            return None

        return Signal(
            name=self.name,
            tier=0,
            score=1.0,
            hard_fail=True,
            detail={"ratio": round(ratio, 3), "chars": hits},
        )


class Empty:
    """FR-3.2 — 번역 결과가 비었거나 공백뿐이다."""

    name = "struct.empty"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        # 원문이 비었으면 번역문이 빈 것은 오류가 아니다.
        if not seg.source_text.strip():
            return None
        if seg.target_text and seg.target_text.strip():
            return None
        return Signal(name=self.name, tier=0, score=1.0, hard_fail=True)


class Degeneration:
    """FR-3.3 — 동일 어절·구가 비정상 반복된다."""

    name = "struct.degeneration"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        if not seg.target_text:
            return None
        tokens = seg.target_text.split()
        if len(tokens) < _DEGENERATION_MIN_REPEAT:
            return None

        token, count = Counter(tokens).most_common(1)[0]
        if count < _DEGENERATION_MIN_REPEAT:
            return None

        return Signal(
            name=self.name,
            tier=0,
            score=1.0,
            hard_fail=True,
            detail={"token": token, "count": count},
        )


class NumberMissing:
    """FR-3.4 — 원문의 숫자가 번역문에 없다."""

    name = "struct.number_missing"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        source_numbers = _NUMBER.findall(seg.source_text)
        if not source_numbers:
            return None

        target_numbers = set(_NUMBER.findall(seg.target_text or ""))
        missing = [n for n in source_numbers if n not in target_numbers]
        if not missing:
            return None

        return Signal(
            name=self.name,
            tier=0,
            score=1.0,
            hard_fail=True,
            detail={"missing": missing},
        )


class TagLost:
    """FR-3.5 — 원문의 마크업이 소실·불일치한다."""

    name = "struct.tag_lost"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        source_tags = Counter(t.lower() for t in _TAG.findall(seg.source_text))
        target_tags = Counter(t.lower() for t in _TAG.findall(seg.target_text or ""))
        if source_tags == target_tags:
            return None

        # 없던 태그가 생긴 것도 불일치다. LLM이 서식을 지어내는 사고가 있다.
        return Signal(
            name=self.name,
            tier=0,
            score=1.0,
            hard_fail=True,
            detail={"source": dict(source_tags), "target": dict(target_tags)},
        )


for _collector in (Untranslated(), Empty(), Degeneration(), NumberMissing(), TagLost()):
    register(_collector)
