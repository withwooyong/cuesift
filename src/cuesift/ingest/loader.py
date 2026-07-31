"""자막 파일을 세그먼트 리스트로 만든다 (요구사항정의서 FR-1.1·1.3·1.5).

**이 모듈이 pysubs2를 아는 유일한 곳이다** (§7.2 모듈 경계).
외부 라이브러리의 표현이 여기서 멈추고 아래로는 순수한 `Segment`만 흐른다.
경계가 흐려지면 `spec`·`risk` 같은 순수 모듈의 테스트가 파일 I/O에 묶인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pysubs2

from cuesift.segment import Segment


@dataclass(frozen=True, slots=True)
class IngestResult:
    """인제스트 산출물 (설계 §2).

    `subs`와 `event_index`는 **WP5(FR-7.1) 라운드트립 전용**이다.
    FR-7.1이 "입력과 동일 포맷으로 출력"을 요구하는데 배치 태그(`{\\an8}`)와
    VTT cue settings는 `Segment`에 없다. 여기서 버리면 WP5가 원본을 다시
    파싱해야 하고, 그 순간 같은 파일의 두 표현이 갈릴 수 있다.
    """

    segments: list[Segment]
    source_path: Path
    format: str
    source_lang: str
    subs: pysubs2.SSAFile
    event_index: dict[str, int]


def load_subtitle(path: Path, *, source_lang: str = "ko") -> IngestResult:
    """자막 파일 하나를 읽어 `IngestResult`로 만든다 (FR-1.1).

    `source_lang`은 값을 받아 기록만 한다 (FR-1.5). CLI·설정 파일의
    우선순위 해결은 WP6의 몫이며 이 모듈은 둘 다 읽지 않는다.
    """
    subs = pysubs2.load(path, encoding="utf-8")
    segments, event_index = _to_segments(list(enumerate(subs)))
    return IngestResult(
        segments=segments,
        source_path=path,
        format=subs.format,
        source_lang=source_lang,
        subs=subs,
        event_index=event_index,
    )


def _to_segments(
    events: list[tuple[int, pysubs2.SSAEvent]],
) -> tuple[list[Segment], dict[str, int]]:
    """이벤트를 `Segment`로 바꾸고 원본 위치 대응표를 함께 만든다 (설계 §6).

    `index`는 **필터 후 0부터 연속 재부여**한다. 구멍이 있으면 리포트와
    정렬이 혼란스러워진다. 원본 위치는 `event_index`가 보존하므로
    라운드트립에 필요한 정보는 잃지 않는다.
    """
    segments: list[Segment] = []
    event_index: dict[str, int] = {}
    for index, (raw_index, event) in enumerate(events):
        seg_id = f"{index:05d}"
        segments.append(
            Segment(
                id=seg_id,
                index=index,
                start_ms=event.start,
                end_ms=event.end,
                source_text=event.plaintext,
            )
        )
        event_index[seg_id] = raw_index
    return segments, event_index
