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

# 그리고 최소 이만큼은 남아 있어야 한다.
#
# 비율만 쓰면 짧은 세그먼트에서 분모가 작아 한 글자에도 임계를 넘는다 —
# "Hi 가"는 1/4 = 25%다. 자막은 감탄사·짧은 응답이 매우 흔하고 거기에
# 고유명사 음역 한 글자가 섞이는 일도 흔하다. hard fail이므로
# 미탐이 오탐보다 낫다.
_UNTRANSLATED_MIN_CHARS = 2

# 반복 붕괴로 볼 최소 연속 횟수. 2회는 'very very good' 같은 자연스러운
# 강조라 제외한다.
_DEGENERATION_MIN_REPEAT = 3

# 반복 단위로 볼 최대 어절 수. LLM degeneration은 단일 어절뿐 아니라
# 짧은 구를 통째로 되풀이한다("I don't know I don't know I don't know").
_DEGENERATION_MAX_UNIT = 4

# 반복 검사에 쓸 최대 어절 수.
#
# `_longest_consecutive_repeat`는 O(n²)이고, **이 신호가 잡으려는 입력이
# 곧 최악 케이스다** — 디코딩 루프에 빠진 LLM은 같은 토큰을 수천 번 뱉는다.
# 연속 반복은 앞쪽에서 이미 드러나므로 앞부분만 봐도 탐지력이 그대로다.
# 자막 한 줄이 이 길이를 넘는다는 것 자체가 이미 비정상이다.
_DEGENERATION_MAX_TOKENS = 200

# 천 단위 구분자와 소수점을 숫자의 일부로 본다.
#
# `\d+`만 쓰면 "1,000"이 ['1', '000']으로, "3.14"가 ['3', '14']로 쪼개진다.
# 원문은 콤마를 쓰고 번역문은 안 쓰는 일이 흔해(영어 자막 관행) 온전히
# 보존된 숫자가 누락으로 잡힌다.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 태그명과 여는/닫는 여부만 뽑는다. 속성은 무시한다.
#
# **속성을 비교에 넣으면 표기 차이가 전부 손실로 잡힌다** — 따옴표 스타일
# (color='red' vs color="red"), 속성 내부 공백, <br>과 <br/>의 자기닫힘 표기.
# 자막 파이프라인은 편집기·파서 라운드트립에서 태그를 재직렬화하므로
# 이런 차이는 흔하고, FR-3.5가 말하는 "마크업 소실"이 아니다.
#
# **한계**: 속성 값이 실제로 달라진 경우(color red -> blue)는 잡지 못한다.
# hard fail 신호에서는 미탐이 오탐보다 낫다 — 오탐은 예산을 우회해
# 지표를 오염시킨다.
_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*?/?>")


def _longest_consecutive_repeat(tokens: list[str]) -> tuple[int, str | None]:
    """가장 길게 **연속** 반복된 단위와 그 횟수.

    **전체 빈도가 아니라 연속성을 본다.** 최빈 토큰을 세면
    "the cat sat on the mat with the dog"처럼 관사가 세 번 나오는
    평범한 문장이 전부 발화한다 — hard fail이라 검수 예산을 우회하므로
    지표가 통째로 망가진다.
    """
    best_count, best_unit = 0, None
    for size in range(1, _DEGENERATION_MAX_UNIT + 1):
        i = 0
        while i + size <= len(tokens):
            unit = tokens[i : i + size]
            count, j = 1, i + size
            while j + size <= len(tokens) and tokens[j : j + size] == unit:
                count += 1
                j += size
            if count >= _DEGENERATION_MIN_REPEAT and count > best_count:
                best_count, best_unit = count, " ".join(unit)
            i += 1
    return best_count, best_unit


def _numbers(text: str) -> list[str]:
    """텍스트의 숫자를 천 단위 구분자를 제거한 형태로 뽑는다."""
    return [m.group().replace(",", "") for m in _NUMBER.finditer(text)]


def _tag_names(text: str) -> Counter[str]:
    """텍스트의 태그를 이름 기준으로 센다. 닫는 태그는 `/` 접두어로 구분한다."""
    return Counter(
        ("/" if m.group(0).startswith("</") else "") + m.group(1).lower()
        for m in _TAG.finditer(text)
    )


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
        if ratio < _UNTRANSLATED_RATIO or hits < _UNTRANSLATED_MIN_CHARS:
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
        tokens = seg.target_text.split()[:_DEGENERATION_MAX_TOKENS]
        if len(tokens) < _DEGENERATION_MIN_REPEAT:
            return None

        count, unit = _longest_consecutive_repeat(tokens)
        if count == 0:
            return None

        return Signal(
            name=self.name,
            tier=0,
            score=1.0,
            hard_fail=True,
            detail={"unit": unit, "count": count},
        )


class NumberMissing:
    """FR-3.4 — 원문의 숫자가 번역문에 없다."""

    name = "struct.number_missing"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        source_numbers = _numbers(seg.source_text)
        if not source_numbers:
            return None

        target_numbers = set(_numbers(seg.target_text or ""))
        missing = [n for n in source_numbers if n not in target_numbers]
        if not missing:
            return None

        # 누락된 것이 전부 한 자리 수면 hard fail을 해제한다.
        #
        # 영어 자막 스타일가이드는 한 자리 수를 단어로 적게 한다("three").
        # 이것을 hard fail로 두면 정상 번역이 검수 예산을 우회해 큐에 쌓여
        # §9.1의 배수가 무의미해진다. 신호 자체는 남겨 소프트 위험으로 둔다.
        #
        # 두 자리 이상(연도·금액·시각)은 단어로 적는 일이 거의 없으므로
        # hard fail을 유지한다.
        multi_digit = any(len(n) > 1 for n in missing)
        return Signal(
            name=self.name,
            tier=0,
            score=1.0 if multi_digit else 0.5,
            hard_fail=multi_digit,
            detail={"missing": missing},
        )


class TagLost:
    """FR-3.5 — 원문의 마크업이 소실·불일치한다."""

    name = "struct.tag_lost"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        source_tags = _tag_names(seg.source_text)
        target_tags = _tag_names(seg.target_text or "")
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
