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
