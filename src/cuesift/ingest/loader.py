"""자막 파일을 세그먼트 리스트로 만든다 (요구사항정의서 FR-1.1·1.3·1.5).

**이 모듈이 pysubs2를 아는 유일한 곳이다** (§7.2 모듈 경계).
외부 라이브러리의 표현이 여기서 멈추고 아래로는 순수한 `Segment`만 흐른다.
경계가 흐려지면 `spec`·`risk` 같은 순수 모듈의 테스트가 파일 I/O에 묶인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pysubs2
from pysubs2.exceptions import Pysubs2Error

from cuesift.segment import Segment

# 영상·오디오를 명시적으로 거른다 (FR-1.3, 설계 §7.2).
# 이 목록이 없으면 mp4가 텍스트로 열려 UnicodeDecodeError가 나고,
# 사용자에게 "utf-8로 변환하라"는 **틀린 조언**이 간다.
#
# 자막 확장자 화이트리스트는 두지 않는다 — pysubs2가 내용으로 판별하므로
# 확장자가 없거나 `.vtt`인데 SRT 내용이어도 제대로 읽는다(실측 설계 §12).
_MEDIA_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav"}
)


class IngestError(Exception):
    """인제스트 실패 (설계 §5).

    **`reason`이 계약이고 메시지는 사람용이다.** 테스트는 `reason`만 단언한다 —
    문구를 고정하면 메시지를 개선할 때 회귀 테스트가 함께 실패해 개선을 방해한다.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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
    _reject_non_subtitle(path)
    subs = _load(path)
    events = _keep_displayed(subs)
    if not events:
        raise IngestError(
            "empty",
            f"{path}: 표시할 자막 큐가 0개다 (포맷 {subs.format}). "
            "0개 수집은 통과가 아니라 입력 오류다.",
        )
    segments, event_index = _to_segments(events, path)
    return IngestResult(
        segments=segments,
        source_path=path,
        format=subs.format,
        source_lang=source_lang,
        subs=subs,
        event_index=event_index,
    )


def _load(path: Path) -> pysubs2.SSAFile:
    """파일을 읽어 pysubs2 표현으로 만들고, 실패를 `IngestError`로 번역한다.

    번역하지 않으면 호출자가 pysubs2 예외 계층을 알아야 하고,
    그 순간 §7.2의 "외부 의존을 인터페이스 뒤로 격리"가 무너진다.

    **호출자가 예외를 열거하지 않아도 되게 하는 것이 계약이다** (`spec/profile.py`가
    내용 오류를 `ValueError`로 모은 것과 같은 판단). 열거는 계약이 아니라 관찰이라
    피호출자가 새 예외를 낼 때마다 뒤처지고, 뒤처진 쪽으로 샌 예외는 미처리
    traceback이 되어 종료 코드 1로 나간다 — 이 저장소에서 1은 "규격 위반 발견"이다.
    """
    try:
        return pysubs2.load(path, encoding="utf-8")
    except UnicodeDecodeError as exc:
        # UnicodeDecodeError는 ValueError의 하위다 — 아래 절보다 먼저 와야 한다.
        # 순서를 바꾸면 cp949 파일이 decode가 아니라 parse로 보고되고,
        # 사용자는 "인코딩을 바꿔라"라는 조언을 못 받는다.
        raise IngestError(
            "decode",
            f"{path}: utf-8로 읽을 수 없다 (바이트 {exc.start}). "
            "파일을 utf-8로 변환한 뒤 다시 시도한다.",
        ) from exc
    except OSError as exc:
        # `_reject_non_subtitle`의 `is_file()`은 **존재만 보고 읽기 권한은 보지 않는다.**
        # 여기서 잡지 않으면 `PermissionError`가 호출자의 `except IngestError`를 그대로
        # 통과해 미처리 traceback이 되고 종료 코드 1이 된다 — 1은 "규격 위반 발견"이라
        # **잠긴 파일이 자막 결함으로 오보된다.** Windows에서는 편집기·트랜스코더·
        # OneDrive가 자막을 잡고 있는 것이 흔하고, Linux에서는 mode 000이 같은 결과다.
        #
        # 검사와 열기 사이에 파일이 사라지면 `FileNotFoundError`도 여기로 온다.
        # `not_found`로 되돌리지 않는 것은 그 경합에서 참인 진술이 "없다"가 아니라
        # "읽을 수 없다"이기 때문이다 — 진단이 원인을 좁히지 못하는 편이 틀리는 것보다 낫다.
        #
        # OSError는 `Pysubs2Error`·`ValueError`와 서로 겹치지 않으므로(실측)
        # 아래 절과 순서를 바꿔도 결과가 같다. 읽기 실패를 먼저 두는 것은 읽기가
        # 파싱보다 먼저 일어나기 때문이다.
        raise IngestError(
            "unreadable",
            f"{path}: 파일을 읽을 수 없다 ({exc.strerror or exc}). "
            "다른 프로그램이 파일을 잡고 있는지, 읽기 권한이 있는지 확인한다.",
        ) from exc
    except (Pysubs2Error, ValueError) as exc:
        raise IngestError("parse", f"{path}: 자막으로 해석할 수 없다 - {exc}") from exc


def _reject_non_subtitle(path: Path) -> None:
    """읽기 전에 걸러야 하는 입력 (FR-1.3).

    FR-1.3의 문구는 "자막과 영상이 모두 주어지면 자막 우선"이지만 v0.1의 CLI는
    입력을 하나만 받는다(설계 §7.1). 여기서는 **입력이 영상이면 자막 경로가
    아님을 알린다**로 구현하고, 진짜 "둘 다 주어짐"은 WP9에서 다시 본다.
    """
    if not path.is_file():
        raise IngestError("not_found", f"{path}: 파일이 없다")
    if path.suffix.lower() in _MEDIA_SUFFIXES:
        raise IngestError(
            "video_input",
            f"{path}: 영상·오디오 입력이다. STT는 v0.1에 없다(WBS WP9). "
            "FR-1.3에 따라 자막 파일이 있으면 그것을 넣는다.",
        )


def _keep_displayed(subs: pysubs2.SSAFile) -> list[tuple[int, pysubs2.SSAEvent]]:
    """화면에 나오는 이벤트만 남기고 원본 위치를 함께 돌려준다 (설계 §4).

    `is_comment`는 ASS의 `Comment:` 줄, `is_drawing`은 벡터 드로잉이다.
    드로잉을 남기면 `m 0 0 l 100 0`이 **자막 문자로 세어져** CPS를 부풀리고,
    그 오탐은 hard fail이라 FR-6.2에 따라 검수 예산을 우회한다 —
    실제 검수 비율이 부풀면 Recall@Budget 지표 자체가 무너진다.

    둘 다 SRT·VTT에서는 항상 False이므로(실측 §12) 포맷 분기 없이 적용한다.
    **텍스트가 빈 큐는 남긴다** — FR-3.2가 hard fail로 잡을 대상이다.
    """
    return [(i, e) for i, e in enumerate(subs) if not e.is_comment and not e.is_drawing]


def _to_segments(
    events: list[tuple[int, pysubs2.SSAEvent]], path: Path
) -> tuple[list[Segment], dict[str, int]]:
    """이벤트를 `Segment`로 바꾸고 원본 위치 대응표를 함께 만든다 (설계 §6).

    `index`는 **필터 후 0부터 연속 재부여**한다. 구멍이 있으면 리포트와
    정렬이 혼란스러워진다. 원본 위치는 `event_index`가 보존하므로
    라운드트립에 필요한 정보는 잃지 않는다.

    역전 타임코드는 여기서 잡는다. `Segment`에 맡기면 `ValueError`가 나지만
    **몇 번째 큐인지가 메시지에 없어** 사람이 파일에서 찾을 수 없다.
    """
    segments: list[Segment] = []
    event_index: dict[str, int] = {}
    for index, (raw_index, event) in enumerate(events):
        if event.end < event.start:
            raise IngestError(
                "bad_timecode",
                f"{path}: {raw_index + 1}번째 큐의 타임코드가 역전됐다 "
                f"(start={event.start}ms > end={event.end}ms)",
            )
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
