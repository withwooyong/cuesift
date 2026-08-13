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
    events = _keep_displayed(subs, path)
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
    except Exception as exc:
        # **`except Exception`이 여기 있는 이유는 이 줄이 외부 라이브러리 경계이기 때문이다.**
        # 우리 코드에 쓰면 프로그래밍 오류를 숨기지만, `pysubs2.load` **한 줄**에 쓰면
        # "남의 파서가 무엇을 던지든 그것은 파싱 실패다"라는 정확한 계약이 된다.
        #
        # 열거로는 못 닫힌다는 것이 실측됐다 — pysubs2의 JSON 포맷은 내용으로 판별되는데
        # (`{"` 로 시작하고 `"info":` 포함) 스키마가 어긋나면 `KeyError`·`TypeError`·
        # `AttributeError`를 낸다. 셋 다 `Pysubs2Error`도 `ValueError`도 아니다.
        # `{"info": {}}` **12바이트**면 충분하고 `.srt` 이름을 붙여도 같다.
        # 위 절들을 남겨 둔 것은 reason과 메시지가 다르기 때문이지 그것들로 충분해서가 아니다.
        #
        # **`try` 범위를 넓히면 안 된다.** `_to_segments` 같은 우리 코드가 이 안에 들어오면
        # 진짜 버그가 `parse` 오류로 뭉개져 영원히 안 보인다.
        raise IngestError(
            "parse",
            f"{path}: 자막으로 해석할 수 없다 - {type(exc).__name__}: {exc}",
        ) from exc


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


def _keep_displayed(subs: pysubs2.SSAFile, path: Path) -> list[tuple[int, pysubs2.SSAEvent]]:
    """화면에 나오는 이벤트만 남기고 원본 위치를 함께 돌려준다 (설계 §4).

    `is_comment`는 ASS의 `Comment:` 줄, `is_drawing`은 벡터 드로잉이다.
    드로잉을 남기면 `m 0 0 l 100 0`이 **자막 문자로 세어져** CPS를 부풀리고,
    그 오탐은 hard fail이라 FR-6.2에 따라 검수 예산을 우회한다 —
    실제 검수 비율이 부풀면 Recall@Budget 지표 자체가 무너진다.

    둘 다 SRT·VTT에서는 항상 False이므로(실측 §12) 포맷 분기 없이 적용한다.
    **텍스트가 빈 큐는 남긴다** — FR-3.2가 hard fail로 잡을 대상이다.

    **`text` 타입 검사가 여기 있는 이유**는 `is_drawing`이 `parse_tags(self.text)`를
    부르기 때문이다. json 포맷은 `"text": null`을 그대로 통과시키고, 그때 `TypeError`가
    나면서 `IngestError`를 우회해 **종료 코드 1**이 된다 — 1은 "규격 위반 발견"이다.
    `_to_segments`의 타임코드 검사로는 못 막는다. **이 함수가 그보다 먼저 돌기 때문이다.**
    검사는 우회되지 않는 위치에 둬야 한다는 규칙이 여기서 한 단계 더 앞으로 밀린다.

    **필터 전에, 모든 이벤트를 검사한다.** `is_comment`·`is_drawing`을 부르려면 이미
    타입이 성립해야 하므로 주석·드로잉이라고 건너뛸 수 없다.

    `type`·`style`·`name`·`effect`는 검사하지 않는다 — 넷 다 문자열이 아니어도
    예외를 내지 않는 것을 실측했다(우리 파이프라인은 넷을 읽지 않는다). 읽지도 않는
    필드를 거절하면 실제로 동작하는 파일을 막게 된다.
    """
    kept: list[tuple[int, pysubs2.SSAEvent]] = []
    for index, event in enumerate(subs):
        _require_text(event, index, path)
        if event.is_comment or event.is_drawing:
            continue
        kept.append((index, event))
    return kept


def _require_text(event: pysubs2.SSAEvent, raw_index: int, path: Path) -> None:
    """`text`가 문자열임을 보증한다 (설계 §4·§6).

    `_require_int_timecodes`와 같은 판단이다 — `@dataclass`의 타입 힌트는 런타임에
    아무것도 막지 않고, json 포맷만 파일의 값을 그대로 넣는다.
    """
    if not isinstance(event.text, str):
        raise IngestError(
            "text_type",
            f"{path}: {raw_index + 1}번째 큐의 text가 문자열이 아니다 "
            f"(형 {type(event.text).__name__}). 자막 본문은 문자열이어야 한다.",
        )


def _require_int_timecodes(event: pysubs2.SSAEvent, raw_index: int, path: Path) -> None:
    """타임코드가 **정수임을 런타임에 보증한다** (설계 §6).

    `Segment.start_ms: int`는 `@dataclass`의 타입 힌트라 런타임에 아무것도 막지 않는다.
    `Span.__post_init__`이 `side`를 검사하며 적어 둔 이유와 같은데 타임코드에는 없었다.

    **진입로는 json 포맷 하나다.** srt·vtt·ass·ssa·microdvd·tmp·mpl2는
    `times_to_ms`·`make_time`·`frames_to_ms`가 전부 int를 반환하지만, json만
    `SSAEvent(**fields)`로 파일의 값을 그대로 넣는다. 그래서 스키마가 **정상인** 파일이
    `1000.0`을 담을 수 있고, 그때 인제스트와 규격 판정은 통과한 뒤 리포트에서 죽었다.

    **`Segment.__post_init__`이 아니라 여기서 막는 것이 핵심이다.** `load_subtitle`은
    `_to_segments`를 `try` **밖에서** 부르므로 `Segment`가 던지는 `ValueError`는
    `IngestError`를 우회해 미처리 traceback이 되고 **종료 코드 1**이 된다 —
    1은 "규격 위반 발견"이다. 같은 검사라도 위치가 틀리면 아무것도 고쳐지지 않는다.

    **`type(v) is not int`인 것은 `bool`을 막기 위해서다.** `isinstance(True, int)`가
    참이라 `start: true`가 통과하는데, 그때는 크래시조차 나지 않고 **길이 0짜리 큐로
    조용히 틀린 리포트**가 나온다(실측). `profile.py`의 `_require_positive`가 bool을
    먼저 막는 것과 같은 판단이다 — 이 저장소에서 조용히 틀린 답은 크래시보다 나쁘다.
    """
    for field in ("start", "end"):
        value = getattr(event, field)
        if type(value) is not int:
            raise IngestError(
                "timecode_type",
                f"{path}: {raw_index + 1}번째 큐의 {field} 타임코드가 정수가 아니다 "
                f"(받은 값: {value!r}, 형 {type(value).__name__}). "
                "타임코드는 밀리초 정수여야 한다.",
            )


def _to_segments(
    events: list[tuple[int, pysubs2.SSAEvent]], path: Path
) -> tuple[list[Segment], dict[str, int]]:
    """이벤트를 `Segment`로 바꾸고 원본 위치 대응표를 함께 만든다 (설계 §6).

    `index`는 **필터 후 0부터 연속 재부여**한다. 구멍이 있으면 리포트와
    정렬이 혼란스러워진다. 원본 위치는 `event_index`가 보존하므로
    라운드트립에 필요한 정보는 잃지 않는다.

    타임코드의 **타입과 역전**을 둘 다 여기서 잡는다. `Segment`에 맡기면 `ValueError`가
    나는데 몇 번째 큐인지가 메시지에 없고, 무엇보다 이 함수가 `try` 밖에서 불리므로
    그 예외는 `IngestError`를 우회한다(위 `_require_int_timecodes` 참조).
    """
    segments: list[Segment] = []
    event_index: dict[str, int] = {}
    for index, (raw_index, event) in enumerate(events):
        # 타입 검사가 **먼저다.** 아래 `event.end < event.start`는 str끼리면 TypeError를
        # 내고 그것이 `IngestError`를 우회한다 — 순서를 바꾸면 str 경로가 그대로 뚫린다.
        _require_int_timecodes(event, raw_index, path)
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
