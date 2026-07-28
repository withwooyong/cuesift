"""자막 규격 판정 (요구사항정의서 FR-5.1, FR-3.8).

**이 모듈은 순수하다** — 세그먼트 하나의 텍스트와 지속시간만 보고 판정한다.
겹침만 트랙 전체를 봐야 하므로 별도 함수로 뺐다.

규격을 LLM이 아니라 코드로 판정하는 이유는 §5.5에 있다. "42자 넘지 마"를
프롬프트로 지시하면 대체로 지키고 가끔 어긴다. 자막 규격은 100%가 아니면
의미가 없다 — 한 편에 한 줄만 넘쳐도 사고다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cuesift.segment import Segment
from cuesift.spec.counting import text_width
from cuesift.spec.profile import SpecProfile


@dataclass(frozen=True, slots=True)
class SpecViolation:
    """규격 위반 한 건.

    `kind`는 `line_length`·`line_count`·`cps`·`duration_short`·
    `duration_long`·`overlap` 중 하나다.
    """

    kind: str
    measured: float
    limit: float
    line_index: int | None = None


def check_text(text: str, duration_ms: int, profile: SpecProfile) -> list[SpecViolation]:
    """텍스트 하나가 프로파일을 만족하는지 판정한다."""
    violations: list[SpecViolation] = []

    # 빈 값은 FR-3.2가 hard fail로 따로 잡는다. 여기서 중복 보고하면
    # 같은 문제가 두 신호로 세어져 위험도가 부풀려진다.
    if text.strip():
        lines = text.split("\n")

        if len(lines) > profile.max_lines:
            violations.append(
                SpecViolation("line_count", float(len(lines)), float(profile.max_lines))
            )

        for i, line in enumerate(lines):
            width = text_width(line, profile.char_counting)
            if width > profile.max_chars_per_line:
                violations.append(
                    SpecViolation("line_length", width, profile.max_chars_per_line, line_index=i)
                )

        # CPS는 줄이 아니라 전체 기준이다. 두 줄은 화면에 동시에 보이므로
        # 줄마다 따로 재면 2줄 자막의 읽기 속도가 절반으로 과소평가된다.
        # 줄바꿈 자체는 읽을 문자가 아니므로 제외한다.
        if duration_ms > 0:
            width = text_width(text.replace("\n", ""), profile.char_counting)
            cps = width / (duration_ms / 1000)
            if cps > profile.max_cps:
                violations.append(SpecViolation("cps", round(cps, 3), profile.max_cps))

    if duration_ms < profile.min_duration_ms:
        violations.append(
            SpecViolation("duration_short", float(duration_ms), float(profile.min_duration_ms))
        )
    elif duration_ms > profile.max_duration_ms:
        violations.append(
            SpecViolation("duration_long", float(duration_ms), float(profile.max_duration_ms))
        )

    return violations


def check_overlaps(segments: Sequence[Segment]) -> dict[str, SpecViolation]:
    """시간이 겹치는 세그먼트를 찾는다 (FR-5.1 세그먼트 중첩 금지).

    겹침은 **뒤에 오는 세그먼트**에 기록한다. 앞 세그먼트에 붙이면
    한 자막이 여러 번 겹칠 때 어느 쌍이 문제인지 알 수 없다.

    입력 순서에 의존하지 않도록 시간순으로 정렬한 뒤 판정한다.
    """
    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
    result: dict[str, SpecViolation] = {}

    for prev, curr in zip(ordered, ordered[1:], strict=False):
        # end == start는 겹침이 아니다. 경계를 위반으로 보면
        # 연속된 자막 전체가 오탐이 된다.
        gap = curr.start_ms - prev.end_ms
        if gap < 0:
            result[curr.id] = SpecViolation("overlap", float(-gap), 0.0)

    return result
