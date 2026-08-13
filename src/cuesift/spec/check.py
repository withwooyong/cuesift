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
    `duration_long`·`overlap`·`empty_cue` 중 하나다.
    """

    kind: str
    measured: float
    limit: float
    line_index: int | None = None


@dataclass(frozen=True, slots=True)
class TrackViolation:
    """트랙 안에서 위치가 확정된 규격 위반 (설계 §4).

    **큐 번호를 담지 않는 것이 계약이다.** 큐 번호는 `IngestResult.event_index`가
    있어야 계산되는데(필터가 세그먼트 인덱스를 재부여하므로 `segment.index + 1`은
    원본 파일의 이벤트 순번이 아니다), 그것을 받으면 이 모듈이 인제스트 결과 구조에
    묶여 첫 줄의 "이 모듈은 순수하다"가 깨진다. 큐 번호는 `cli.py`가 붙인다.

    `event_index`가 주는 것도 **이벤트 순번이지 SRT에 인쇄된 번호가 아니다** —
    pysubs2가 인쇄 번호를 버린다. 자세한 근거는 `cli.py`의 `_format_report`에 있다.
    """

    segment_id: str
    start_ms: int
    violation: SpecViolation


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
    한 자막이 여러 번 겹칠 때 어느 쪽이 문제인지 알 수 없다.

    **직전 항목이 아니라 지금까지 본 최대 end_ms와 비교한다.** 인접 쌍만
    보면 긴 세그먼트가 여러 개를 덮을 때 중간에 끼지 않은 것을 놓친다 —
    A(0~10000)가 C(5000~6000)를 덮어도 사이의 B(100~200)와 C가 안 겹치면
    C가 검사에서 빠진다. 검사하지 않고 통과하는 게이트는 없는 게이트보다 나쁘다.
    """
    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
    result: dict[str, SpecViolation] = {}
    run_end: int | None = None

    for seg in ordered:
        # end == start는 겹침이 아니다. 경계를 위반으로 보면
        # 연속된 자막 전체가 오탐이 된다.
        if run_end is not None and seg.start_ms < run_end:
            # 포함 관계에서는 앞 세그먼트의 끝이 아니라 이 세그먼트의 끝이
            # 겹침의 경계다. run_end만 쓰면 겹침량이 과대 보고된다.
            overlap = min(run_end, seg.end_ms) - seg.start_ms
            result[seg.id] = SpecViolation("overlap", float(overlap), 0.0)

        if run_end is None or seg.end_ms > run_end:
            run_end = seg.end_ms

    return result


def check_empty_cues(segments: Sequence[Segment]) -> dict[str, SpecViolation]:
    """텍스트가 없거나 공백뿐인 세그먼트를 찾는다 (FR-5.1·설계 §2.1).

    **`check_text`가 아니라 별도 함수인 것이 계약이다.** `check_text` 안에 넣으면
    `translate` 경로에서 `struct.empty`와 이중 계산되고, 그 부풀림이 검수 비율을
    밀어 올린다(설계 §4.2). `translate` 경로는 이 함수를 부르지 않는다.

    `measured`·`limit`이 둘 다 `0.0`인 것은 이 판정에 잴 수치가 없기 때문이다 —
    빈 큐는 임계값을 넘은 것이 아니라 있으면 안 되는 것이다.

    `str.strip()`이 경계다. `str.isspace()` 기준이라 `U+3000`(전각 공백)·`U+00A0`(NBSP)·
    `U+2028`은 이미 잡히고, 놓치는 것은 `Cf`(format) 계열뿐이다.

    **`Cf`를 포함하도록 넓히지 않는다.** TED2020 151만 줄에서 `Cf`만으로 이뤄진 줄은
    0건이라 실측 이득이 없는 반면, 넓히면 `U+2800`(점자 공백)과 `U+115F`(한글 필러)가
    오탐이 된다. 이 프로젝트에서 오탐은 검수 비율을 부풀려 Recall@Budget 지표 자체를
    파괴하므로 미탐보다 비싸다.
    """
    return {
        seg.id: SpecViolation("empty_cue", 0.0, 0.0)
        for seg in segments
        if not seg.source_text.strip()
    }


def check_track(segments: Sequence[Segment], profile: SpecProfile) -> list[TrackViolation]:
    """트랙 전체를 검사해 위반을 리스트 순서로 반환한다 (FR-5.1·설계 §4).

    **정렬 기준이 리스트 순서인 것은 사람이 파일을 위에서 아래로 읽기 때문이다.**
    심각도 순으로 정렬하면 같은 큐의 위반들이 흩어져 파일에서 찾기 어려워진다.
    v0.1에는 심각도 등급 자체가 없기도 하다(설계 §5.1).

    새 규격 판정이 생기면 여기에 함께 넣는 것이 규약이다 — `check` 경로가
    신호 엔진을 통과하지 않으므로(설계 D3) 이 함수가 규격 판정의 집결지다.

    전제 둘을 명시한다.

    **`seg.id`가 트랙 안에서 유일하다고 전제한다.** `check_overlaps`·`check_empty_cues`가
    id로 키잉한 dict를 돌려주므로, id가 중복되면 한 큐의 위반이 같은 id를 가진 **모든**
    큐에 복제된다 — 위반이 없는 큐에도 붙고 그쪽 타임코드는 틀린 값이 된다.
    인제스트는 `f"{index:05d}"`로 부여해 안전하지만 `bench/`는 `Segment`를 직접 만든다.

    **리스트 순서가 시간 순서와 다를 수 있다.** 인제스트가 정렬하지 않으므로 리스트 순서가
    곧 파일 순서다. 따라서 출력의 타임코드는 비단조일 수 있고 **그것이 옳다** —
    `start_ms`로 정렬해 "고치면" 리포트가 파일과 다른 순서로 나와 검수자가 큐를 못 찾는다.

    **`start_ms`·`end_ms`가 `int`라고 전제한다.** `@dataclass`는 이 전제를 강제하지 않고
    이 함수도 검사하지 않는다. 어기면 **종료 코드 1**(= "규격 위반 발견")로 오보되거나,
    더 나쁘게는 **조용히 틀린 답**이 된다(전부 실측).

    | 값 | 결과 |
    | --- | --- |
    | `float` · 위반이 있는 트랙 | `cli.py` `_format_timecode`의 `{hours:02d}`에서 `ValueError` |
    | `float` · **위반 0건 트랙** | 크래시 없음. `exit 0 · "위반 없음"`으로 **조용히 통과** |
    | `str` | `duration_ms` 뺄셈에서 `TypeError` |
    | `bool` | 크래시 없음. `false`/`true`는 **`cps 6500`** 같은 수치를 날조한다 |
    보증은 `ingest/loader.py`의 `_require_int_timecodes`가 경계에서 한다.
    `bench/`처럼 `Segment`를 직접 만드는 쪽은 그 보증을 받지 못하므로 스스로 지켜야 한다.
    """
    # 입력을 3회 순회한다(겹침·빈 큐·본체 루프). 제너레이터를 그대로 쓰면 첫 순회에서
    # 소진돼 본체 루프가 빈 채로 돌고, 위반 0건이 "깨끗한 파일"로 읽혀 종료 코드가 0이 된다.
    segments = list(segments)

    overlaps = check_overlaps(segments)
    empties = check_empty_cues(segments)

    found: list[TrackViolation] = []
    for seg in segments:
        for violation in check_text(seg.source_text, seg.duration_ms, profile):
            found.append(TrackViolation(seg.id, seg.start_ms, violation))
        # 두 dict를 `{**overlaps, **empties}`로 합치면 안 된다 — 둘 다 `seg.id`로
        # 키잉하므로 빈 큐이면서 겹치는 큐에서 overlap이 조용히 소실된다.
        if seg.id in overlaps:
            found.append(TrackViolation(seg.id, seg.start_ms, overlaps[seg.id]))
        if seg.id in empties:
            found.append(TrackViolation(seg.id, seg.start_ms, empties[seg.id]))
    return found
