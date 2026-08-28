"""용어집 로드와 위반 판정 (요구사항정의서 FR-3.7, FR-2.3).

**판정 규칙**: 원문에 용어집 키가 있는데 번역문에 등재된 대응어가 하나도
없으면 위반이다. 원문에 없는 용어는 검사하지 않는다 — 이걸 어기면 용어집이
커질수록 오탐이 선형으로 늘어 용어집을 키울 수 없게 된다.

대응어가 여러 개면 **하나만 나와도 통과**다. 전부 요구하면 정상 번역이
대량 오탐이 된다("AI"와 "artificial intelligence"를 한 문장에 둘 다 쓰지 않는다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# 대응어가 다른 단어 안에 우연히 포함되는 것을 막는다.
#
# `\b` 대신 라틴 문자·숫자 룩어라운드를 쓰는 이유는 CJK 때문이다.
# CJK 문자는 전부 `\w`라 `\b气候变動\b`는 「これは気候変動です」에서
# 매칭되지 않는다 — 조사가 붙으면 경계가 생기지 않기 때문이다.
# 룩어라운드는 라틴 문자·숫자만 보므로 CJK 이웃에 영향받지 않는다.
_BOUNDARY = r"(?<![a-zA-Z0-9]){}(?![a-zA-Z0-9])"


def _contains_term(text: str, term: str) -> bool:
    """`text`에 `term`이 단어 경계를 지켜 등장하는지."""
    return re.search(_BOUNDARY.format(re.escape(term)), text) is not None


def term_offsets(text: str, term: str) -> list[tuple[int, int]]:
    """`text`에서 `term`이 등장하는 모든 구간. FR-7.3 하이라이트의 입력이다.

    **`_contains_term`과 같은 `_BOUNDARY`를 쓴다.** 규칙이 갈리면 위반으로
    잡은 용어의 위치를 못 찾아 하이라이트가 조용히 빈다 — 검수자는 칠해지지
    않은 것을 "문제 없음"으로 읽는다.

    **`lower()`한 문자열이 아니라 원본에 `IGNORECASE`를 건다.** `str.lower()`는
    길이를 보존하지 않는 경우가 있고(실측: `len('İ')==1`, `len('İ'.lower())==2`)
    그 뒤 모든 오프셋이 밀린다. 판정(`violations`)은 `lower()`를 써도 되지만
    **오프셋은 원본 기준이어야 한다**(설계 D7).

    반환은 **위치 오름차순**이다 — `review.json`에 배열로 직렬화되므로 순서가
    비결정적이면 같은 입력이 다른 파일을 낸다(NFR-3 · 설계 D9).
    """
    pattern = _BOUNDARY.format(re.escape(term))
    return [(m.start(), m.end()) for m in re.finditer(pattern, text, re.IGNORECASE)]


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """용어 하나와 그 대응어들."""

    source: str
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Glossary:
    """대상 언어 하나에 대한 용어집."""

    entries: tuple[GlossaryEntry, ...] = ()

    @property
    def is_empty(self) -> bool:
        """비어 있으면 '위반 0건'이 '검사하지 않음'을 뜻한다.

        호출자가 이 둘을 구분할 수 있어야 한다. 구분하지 않으면
        용어집을 못 읽은 실행이 만점으로 보고된다.
        """
        return not self.entries

    def terms_in(self, source_text: str) -> list[GlossaryEntry]:
        """원문에 등장하는 용어들. 프롬프트 주입용 (FR-2.3).

        `violations()`와 **같은 판정 규칙**(`_contains_term`)을 쓰는 것이
        요점이다. 규칙이 갈리면 프롬프트에 넣은 용어와 위반으로 잡는 용어가
        어긋나, 주입하지 않은 용어를 안 썼다고 위반 처리하게 된다.

        전체 용어집을 매번 프롬프트에 넣지 않기 위해 있다. 용어집이 500개인데
        배치에 3개만 나오면 나머지 497개는 매 호출 낭비다.

        반환 순서는 **용어집 등재 순**이다(매치 위치 순이 아니다). 위치 순으로
        바꾸면 같은 용어 집합이라도 배치 내용에 따라 용어 블록이 다르게
        직렬화되어, 프롬프트 프리픽스 캐시가 무효화되고 "동일 입력에 동일
        결과"라는 재현성(NFR-3)이 깨진다. 등재 순은 어떤 배치에서도 상대
        순서가 같다.
        """
        lowered_source = source_text.lower()
        return [
            entry for entry in self.entries if _contains_term(lowered_source, entry.source.lower())
        ]

    def violations(self, source_text: str, target_text: str) -> list[GlossaryEntry]:
        """원문에 등장하는 용어 중 번역문에 대응어가 없는 것들."""
        lowered_target = target_text.lower()
        lowered_source = source_text.lower()
        return [
            entry
            for entry in self.entries
            if _contains_term(lowered_source, entry.source.lower())
            and not any(_contains_term(lowered_target, t.lower()) for t in entry.targets)
        ]


def load_glossary(path: Path, target_lang: str) -> Glossary:
    """YAML 용어집에서 `target_lang` 대응어만 골라 로드한다."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "entries" not in raw:
        raise ValueError(f"{path}: 최상위에 'entries' 키가 없다")

    entries: list[GlossaryEntry] = []
    for idx, item in enumerate(raw["entries"] or []):
        try:
            if not isinstance(item, dict):
                raise ValueError("항목이 dict가 아니다")
            if "source" not in item:
                raise ValueError("'source' 키가 없다")
            source = str(item["source"])
        except (TypeError, KeyError, ValueError) as e:
            raise ValueError(f"{path}: 항목 {idx}: {e}") from None

        targets = (item.get("targets") or {}).get(target_lang)
        # 대상 언어 대응어가 없는 항목은 버린다. 남겨 두면 대응어가
        # 빈 채로 항상 위반 판정이 나온다.
        if not targets:
            continue

        if not isinstance(targets, list):
            # 문자열이 오면 tuple()이 글자 단위로 쪼개고, 알파벳 한 글자는
            # 거의 모든 텍스트에 있으므로 그 항목이 영원히 통과한다.
            raise ValueError(
                f"{path}: '{source}'의 {target_lang} 대응어가 리스트가 아니다 "
                f"(YAML 대괄호 누락?). 받은 값: {targets!r}"
            )

        entries.append(GlossaryEntry(source=source, targets=tuple(targets)))

    return Glossary(entries=tuple(entries))
