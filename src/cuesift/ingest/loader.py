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
from cuesift.stt.provider import SttProvider, Transcript

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


def load_media(path: Path, provider: SttProvider, *, source_lang: str = "ko") -> IngestResult:
    """영상·오디오를 STT로 전사해 `IngestResult`로 만든다 (FR-1.2 · 설계 §4.4).

    **`IngestResult`의 필수 필드 6개를 전부 채운다.** 셋이 실패 지점이고
    그중 하나는 조용하다 (전부 실측):

    | 안 채운 필드 | 결과 |
    | --- | --- |
    | `format` | `UnknownFileExtensionError: '.tmp'` - `writer.py`가 `save(format_=None)`을 부른다 |
    | `event_index` | `writer.py`가 `KeyError`, `cli.py`의 큐 번호 폭이 **조용히** 1이 된다 |
    | `subs` | 필수 필드라 `TypeError` |

    **프로바이더의 예외를 `IngestError`로 감싸지 않는다.** 감싸면 CLI가
    "자막 파일이 잘못됐다"로 보고하는데 실제 원인은 STT 백엔드다 - 호출부는
    둘을 다른 종료 코드로 바꾼다.

    **`source_lang`은 호출자가 선언한 값을 그대로 쓴다** (FR-1.5). `transcript.language`로
    덮으면 안 되는 이유는 그 값의 **도메인이 정의돼 있지 않기** 때문이다 - 백엔드는
    `"korean"`·`"ko"`·`"Korean"`을 제각각 내고, §12 Q3가 "능력이 균일하지 않다"를
    전제로 둔다. `signals/structural.py`의 `_SCRIPT_RANGES` 키는 정확히 `ko`·`ja`
    둘뿐이라 `"korean"`이 들어오면 `.get()`이 `None`을 내고 그 자리의 `return None`이
    **미번역 신호를 예외 없이 통째로 끈다**(실측). 미탐이 늘어 Recall@Budget이
    조용히 내려간다 - 크래시가 아니라 지표가 틀리는 부류다.

    `load_subtitle`의 같은 필드가 **"호출자가 선언한 ISO 코드"**를 불변식으로 갖고
    있으므로 두 경로가 같아야 한다. 탐지된 언어가 필요해지면 `Transcript.language`에
    그대로 남아 있으니 그때 별도 필드로 싣는다.

    `_reject_non_subtitle`을 부르지 않는다 - 그 함수는 영상 입력을 **거절**하는데
    여기서는 영상이 정상 입력이다.
    """
    if not path.is_file():
        raise IngestError("not_found", f"{path}: 파일이 없다")

    transcript = provider.transcribe(path, language=source_lang)
    segments, subs, event_index = _from_transcript(transcript, path)
    if not segments:
        raise IngestError(
            "empty",
            f"{path}: 표시할 큐가 0개다 (프로바이더 {provider.name}, 모델 {transcript.model}). "
            "0개 수집은 통과가 아니라 입력 오류다.",
        )
    return IngestResult(
        segments=segments,
        source_path=path,
        # **`subs.format`이 아니다.** 합성한 SSAFile의 `.format`은 이벤트를
        # 넣어도 `None`으로 남는다(실측). 그 `None`이 `writer.py`의
        # `save(format_=)`로 흘러가 `.tmp` 확장자 판별에서 죽는다.
        format="srt",
        source_lang=source_lang,
        subs=subs,
        event_index=event_index,
    )


def load_input(
    *,
    subtitle: Path | None = None,
    media: Path | None = None,
    provider: SttProvider | None = None,
    source_lang: str = "ko",
) -> IngestResult:
    """자막과 영상 중에서 고른다 (FR-1.3).

    **둘 다 주어지면 자막을 채택하고 STT를 부르지 않는다.** 부르고 버리면
    사용자가 쓰지도 않을 전사에 돈과 시간을 낸다. 요구사항정의서 §11 R1
    ("원문이 틀리면 N개 언어로 복제된다")의 대응이 바로 이 우선순위다 -
    사람이 만든 자막이 STT보다 신뢰도가 높다.

    **분기 순서가 계약이다.** `media` 분기를 먼저 두면 자막이 있어도 전사가
    먼저 일어나고, 결과를 버려도 요금과 대기 시간은 이미 나갔다. 테스트의
    `provider.calls == []`가 그 순서를 지키는 유일한 게이트다.

    **`source_lang`을 양쪽에 그대로 넘긴다.** 한쪽에서만 빠지면 그 경로의
    `IngestResult.source_lang`이 호출자의 선언이 아니라 기본값 `"ko"`가 되어
    **두 경로의 값 도메인이 갈린다.** 지금 이 필드를 읽는 소비처가 없다는 것이
    (실측: `grep -rn "[.]source_lang" src/cuesift` - 신호도 리포트도 CLI가 따로
    넘긴 값을 쓴다) 안전이 아니라 **위험**이다: 틀린 값이 증상 없이 기록돼 있다가
    WP6이 이 필드를 배선하는 순간 `signals/structural.py`의 `_SCRIPT_RANGES`가
    `ja` 원문에 한글 패턴을 물려 미번역 신호가 미탐으로 굳는다 - 크래시가
    아니라 Recall@Budget이 조용히 내려가는 부류다 (`load_media` 독스트링 참조).

    **영상을 무시했다는 사실을 사용자에게 알리는 것은 CLI(WP6)의 몫이다.**
    라이브러리에 경고 채널을 새로 파면 이번 범위에서 쓸 곳이 없는 표면이 생긴다.

    **이 함수를 부르는 것은 지금 테스트뿐이다.** CLI 배선이 FR-8.3(WP6)이라
    그렇고, 그럼에도 만드는 것은 FR-1.3을 반쪽으로 남기지 않기 위해서다.
    """
    if subtitle is not None:
        return load_subtitle(subtitle, source_lang=source_lang)
    if media is not None:
        if provider is None:
            # `_reject_non_subtitle`과 **같은 reason을 쓴다.** `reason`은
            # 계약이고 메시지는 사람용이다(`IngestError` 독스트링) - 지금
            # `cli.py`는 `str(exc)`만 찍고 reason으로 분기하지 않으므로(실측),
            # 새 reason을 만들면 **소비처 없는 계약 항목**이 하나 늘 뿐이다.
            # 같은 상황(영상을 자막 자리에 넣었다)에 두 이름이 붙으면 나중에
            # reason으로 분기하는 호출부가 한쪽만 처리하고 다른 쪽을 흘린다.
            raise IngestError(
                "video_input",
                f"{media}: 영상 입력에는 STT 프로바이더가 필요하다. "
                "--base-url과 --model을 주거나 자막 파일을 입력하라.",
            )
        return load_media(media, provider, source_lang=source_lang)
    raise IngestError("no_input", "자막 파일이나 영상 파일 중 하나는 주어야 한다")


def _from_transcript(
    transcript: Transcript, path: Path
) -> tuple[list[Segment], pysubs2.SSAFile, dict[str, int]]:
    """전사 큐를 `Segment`·`SSAFile`·대응표 셋으로 동시에 만든다 (설계 D5·D6).

    셋을 **한 루프에서** 만드는 것이 중요하다. 나눠서 만들면 빈 큐를 거른 뒤
    한쪽만 index가 밀려 `event_index`가 엉뚱한 이벤트를 가리키는데,
    그것은 예외가 아니라 **번역문이 다른 큐에 얹히는** 형태로 드러난다.

    `TranscriptCue.__post_init__`이 `nan`·`inf`·역전·음수·비수치를 이미 막았으므로
    이 함수는 그것을 다시 검사하지 않는다(결정 P3). 검사하면 아무도 실행하지
    않는 분기가 생긴다. **`_to_ms`의 방어는 그 목록에 없는 것이다** - 곱셈이
    만들어 내는 값이라 큐 하나만 봐서는 알 수 없다.
    """
    segments: list[Segment] = []
    subs = pysubs2.SSAFile()
    event_index: dict[str, int] = {}
    for position, cue in enumerate(transcript.cues):
        text = cue.text.strip()
        if not text:
            # 공백만 있는 큐는 화면에 아무것도 안 띄운다. 남기면 CPS가 0으로
            # 계산돼 규격 검사가 무의미한 세그먼트가 검수 큐에 낀다.
            # 자막 경로의 `_keep_displayed`와 같은 판단이다.
            #
            # **태그만 있는 큐(`{music}`)는 여기서 안 걸린다** - 아래 `plaintext`가
            # 태그를 지우므로 `source_text`가 빈 채로 남는다. 자막 경로도 빈 큐를
            # 남기고 FR-3.2가 hard fail로 잡는 것과 같다(`_keep_displayed`).
            continue
        start_ms = _to_ms(cue.start_s, field="start_s", position=position, path=path)
        end_ms = _to_ms(cue.end_s, field="end_s", position=position, path=path)
        index = len(segments)
        seg_id = f"{index:05d}"
        # **`text=`로 직접 넣지 않고 `plaintext` setter를 지난다.** 그래야 자막
        # 경로와 **같은 함수**를 통과한다 - `load_subtitle`은 `source_text`를
        # `event.plaintext`에서 받으므로 오버라이드 블록이 빠진 상태가 불변식이다.
        # 직접 넣으면 셋이 갈린다 (전부 실측).
        #
        # 1. `{music}안녕`이 `source_text`에 그대로 남아 길이가 **9 vs 2**가 된다.
        #    CPS가 4.5배로 부풀고 **그 오탐은 hard fail이라 FR-6.2에 따라 검수
        #    예산을 우회해** 실제 검수 비율을 부풀린다 - Recall@Budget이 무너진다.
        # 2. `writer.py`의 `_LEADING_OVERRIDES`가 그 `{music}`을 위치 태그로 오인해
        #    번역문 앞에 다시 붙인다. SRT 저장 때 pysubs2가 지워 출력 파일은
        #    멀쩡하므로 **예외도 경고도 없다.**
        # 3. 실제 개행이 `SSAEvent.text`에 담긴다. SSA 규약은 `\N`이라 ass로
        #    저장하면 `Dialogue:` 줄이 물리적으로 쪼개진다.
        event = pysubs2.SSAEvent(start=start_ms, end=end_ms)
        event.plaintext = text
        subs.append(event)
        segments.append(
            Segment(
                id=seg_id,
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                source_text=event.plaintext,
                # FR-1.4. 이 경로로 들어온 원문은 **전부** 표시 대상이다.
                source_from_stt=True,
            )
        )
        # 필터 뒤에도 순서가 곧 원본 위치다 - `subs`를 같은 루프에서 채우므로
        # 걸러진 큐는 양쪽에서 함께 빠진다.
        event_index[seg_id] = index
    return segments, subs, event_index


def _to_ms(seconds: float, *, field: str, position: int, path: Path) -> int:
    """초를 밀리초 정수로 바꾼다 (설계 D5).

    **양쪽 타임코드에 같은 함수를 쓴다.** 같은 방향으로 움직여야 인접 큐의
    맞물린 경계가 그대로 붙어 있다 - 한쪽만 내리고 한쪽만 올리면 원본에 없던
    겹침을 우리가 만든다. `round()`가 **half-up이 아니라 짝수 반올림**이라는
    것은 여기서 중요하지 않다(양쪽이 같으면 되므로). 테스트의 기대값을 적을
    때만 중요하다 - `1.2345 * 1000 = 1234.5`는 1235가 아니라 **1234**다.

    **`try`가 없으면 `OverflowError`가 `IngestError` 밖으로 샌다**(실측).
    `TranscriptCue`는 `1e308`을 통과시킨다 - 유한하고 음수가 아니며 역전도
    아니다. 그런데 `1e308 * 1000`은 `inf`가 되고 `round(inf)`가
    `OverflowError: cannot convert float infinity to integer`를 낸다.
    호출부가 잡는 것은 `IngestError`와 `ProviderError`뿐이라 그 예외는
    미처리 traceback이 되고 **종료 코드 1**이 되는데, 이 저장소에서 1은
    "규격 위반 발견"이라 **STT 백엔드 결함이 자막 결함으로 오보된다.**
    `TranscriptCue.__post_init__`이 `math.isfinite(10**400)`의 `OverflowError`를
    `ValueError`로 번역한 것과 같은 부류다 - 방어의 다음 한 걸음이 새는 자리.

    **`math.isfinite(seconds * 1000)`으로 대신하면 안 된다.** JSON은 소수점
    없는 리터럴을 `int`로 파싱하는데, `10**306`은 `TranscriptCue`를 통과하고
    `10**306 * 1000`은 `float`로 변환되지 않을 만큼 커서 `isfinite` **자신이**
    `OverflowError`를 낸다(실측). 막으려던 예외를 방어가 다시 낸다.

    **거대 정수는 여기서 걸리지 않는다.** `10**306`은 곱셈도 `round()`도 예외
    없이 지나가 310자리 `start_ms`가 된다. 그 값은 `writer.py`의 저장 시점에
    pysubs2가 `RuntimeWarning`과 함께 `99:59:59,999`로 클램프한다(실측) -
    **조용하지 않으므로** 출처 없는 상한을 발명하지 않는다(§11 R8).
    """
    try:
        return round(seconds * 1000)
    except OverflowError as exc:
        raise IngestError(
            "bad_timecode",
            f"{path}: {position + 1}번째 큐의 {field}({seconds!r})가 "
            "밀리초 정수로 변환되지 않는다. 전사 응답의 타임코드를 확인한다.",
        ) from exc


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

    FR-1.3의 문구는 "자막과 영상이 모두 주어지면 자막 우선"이고, 그 판정은
    이제 `load_input`이 한다. 이 함수는 **자막 경로에 영상이 들어온 경우**만
    막는다 - `load_subtitle`이 자막 전용이라는 이름값을 지키게 하는 것이
    여기 남은 역할이다. FR-1.3과 무관한 존재 검사(`not_found`)가 함께 있는 것은
    읽기 전에 걸러야 할 입력이 그 둘뿐이기 때문이다.
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
    `SSAEvent(**fields)`로 파일의 값을 그대로 넣는다.

    **증상이 타입마다 다르고, 그중 둘은 조용하다**(전부 실측).

    | 값 | 증상 |
    | --- | --- |
    | `1000.0` (위반 있는 파일) | `_format_timecode`의 `{hours:02d}`에서 `ValueError` -> exit 1 |
    | `1000.0` (**위반 0건 파일**) | **크래시 없음. exit 0 · "위반 없음"으로 조용히 통과** |
    | `"1000"` | `duration_ms` 뺄셈에서 `TypeError` -> exit 1 (위반 유무와 무관) |
    | `true`/`true` | 크래시 없음. 길이 **0ms**짜리 큐가 되어 `duration_short`가 붙는다 |
    | `false`/`true` | 크래시 없음. 길이 1ms라 **`cps 24500.0 > 12.0`** 이라는 수치를 날조한다 |

    (bool 두 행의 수치는 테스트의 `_VIOLATING_LINE` 본문 기준이다. 본문을 바꾸면 값이
    달라지므로 **수치를 인용할 때 어느 본문인지 함께 봐야 한다** — 실제로 이 표가 한 번
    삭제된 옛 본문의 값을 실측으로 인용한 적이 있다.)

    **`type(v) is not int`인 것은 `bool`을 막기 위해서다.** `isinstance(True, int)`가
    참이라 `isinstance`로 "완화"하면 위 두 bool 케이스가 그대로 통과한다.
    `false`/`true`가 특히 나쁘다 — 날조된 CPS는 자릿수만 클 뿐 형식이 정상 위반과 같아
    검수자가 의심하지 않는다. `profile.py`의 `_require_positive`가 bool을 먼저 막는 것과
    같은 판단이다: **이 저장소에서 조용히 틀린 답은 크래시보다 나쁘다.**

    **`Segment.__post_init__`이 아니라 여기서 막는 것이 핵심이다.** `load_subtitle`은
    `_to_segments`를 `try` **밖에서** 부르므로 `Segment`가 던지는 `ValueError`는
    `IngestError`를 우회해 미처리 traceback이 되고 **종료 코드 1**이 된다 —
    1은 "규격 위반 발견"이다. 같은 검사라도 위치가 틀리면 아무것도 고쳐지지 않는다.
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


def _require_non_negative_timecodes(event: pysubs2.SSAEvent, raw_index: int, path: Path) -> None:
    """타임코드가 **음수가 아님을 보증한다** (FR-1.1).

    **타입 검사도 역전 검사도 이것을 잡지 못한다**(실측). `(-5000, -1000)`은 둘 다
    int이고 `end >= start`도 만족한다. 그대로 통과하면 `_format_timecode`가 부호를
    살려 `-00:00:01.000`을 찍는데, 그 앞 판정이 **규격 위반을 하나도 못 찾으면
    `check`가 exit 0 · "위반 없음"을 낸다** — 재생 불가능한 파일이 CI를 통과한다.

    | 입력 | 이 검사가 없으면 |
    | --- | --- |
    | `(-5000, -1000)` | **exit 0 · "위반 없음"** (규격 위반이 없는 트랙일 때) |
    | `(-3000, 1000)` | exit 1은 나지만 목록의 좌표가 실제 위치와 다르다 |

    **`< 0`이지 `<= 0`이 아니다.** `0`은 영상 첫 프레임을 가리키는 정상 값이고
    `00:00:00,000`으로 시작하는 자막은 흔하다 — 여기서 `0`을 막으면 그 자막이 전부 죽는다.
    바로 옆 `profile.py`의 `_require_positive`가 `<= 0`을 쓰므로 그 오독은 그럴듯하고,
    실제로 **`<= 0` 변이가 497건 전체 스위트를 통과했다**(리뷰 실측). 지금은
    `starts_at_zero.srt` 픽스처와 `test_a_cue_starting_at_zero_loads_normally`가 막는다 —
    그 픽스처를 **새로 만들어야 했다.** 기존 13종 중 `0`을 지나가는 것이 하나도 없었다.

    **진입로는 json 하나가 아니다.** pysubs2가 ASS·SAMI(`.smi`)·MPL2에서 음수를
    **의도적으로** 파싱한다(`substation.py`의 `# handle negative timestamps`,
    `mpl2.py` 정규식의 부호, `sami.py`의 `int()`). SRT·VTT·MicroDVD·TMP는 부호 자리가
    없어 앞의 `-`를 조용히 무시한다. **쓰기 경로는 pysubs2가 전부 클램프하므로**
    "우리 도구로 왕복시켜 보니 괜찮더라"는 검증은 이 위험을 통째로 비켜 간다 —
    출처는 **외부 도구가 만든 파일**이다.

    **대가를 적어 둔다.** 한 큐가 걸리면 파일 전체가 66이라 나머지 큐의 규격 위반이
    통째로 가려진다(실측: 800큐 ASS에서 `-10ms` 하나가 위반 17건을 덮었다). 그럼에도
    이 판정을 고른 것은 대안이 전부 더 나쁘기 때문이다 — 허용 임계값은 출처가 없어
    §11 R8("출처 없는 수치를 기본값으로 넣지 않음")에 걸리고, 해당 큐만 빼는 것은
    `검사 큐 N개` 헤더를 거짓으로 만든다. **0으로 클램프하는 것은 특히 안 된다** —
    `_format_timecode`에서 방금 폐기한 "그럴듯한 거짓"과 같은 것이 된다.

    규격 위반(exit 1)이 아니라 `IngestError`(exit 66)인 것은 고칠 대상이 다르기
    때문이다. CPS·줄길이는 검수자가 그 큐의 텍스트를 고치면 되지만 음수 좌표는
    싱크·변환 파이프라인의 사고다. 섞으면 CI가 두 사고에 같은 대응을 한다.
    """
    for field in ("start", "end"):
        value = getattr(event, field)
        if value < 0:
            raise IngestError(
                "negative_timecode",
                f"{path}: {raw_index + 1}번째 큐의 {field} 타임코드가 음수다 "
                f"(받은 값: {value}ms). 타임코드는 0 이상이어야 한다.",
            )


def _to_segments(
    events: list[tuple[int, pysubs2.SSAEvent]], path: Path
) -> tuple[list[Segment], dict[str, int]]:
    """이벤트를 `Segment`로 바꾸고 원본 위치 대응표를 함께 만든다 (설계 §6).

    `index`는 **필터 후 0부터 연속 재부여**한다. 구멍이 있으면 리포트와
    정렬이 혼란스러워진다. 원본 위치는 `event_index`가 보존하므로
    라운드트립에 필요한 정보는 잃지 않는다.

    타임코드의 **타입·부호·역전**을 셋 다 여기서 잡는다. `Segment`에 맡기면 `ValueError`가
    나는데 몇 번째 큐인지가 메시지에 없고, 무엇보다 이 함수가 `try` 밖에서 불리므로
    그 예외는 `IngestError`를 우회한다(위 `_require_int_timecodes` 참조).
    **부호는 `Segment`도 안 본다** — `__post_init__`은 역전만 검사한다.
    """
    segments: list[Segment] = []
    event_index: dict[str, int] = {}
    for index, (raw_index, event) in enumerate(events):
        # **타입 검사가 역전 검사보다 먼저여야 한다.** 아래 `event.end < event.start`는
        # **두 필드의 타입이 서로 다를 때** TypeError를 내고 그것이 `IngestError`를
        # 우회한다 — `"1000" < 4000` · `None < 1000` · `1000.0 < "4000"`이 그렇다.
        #
        # **str끼리는 트리거가 아니다**(실측). `"4000" < "1000"`은 사전순으로 조용히
        # `False`를 낸다. 그래서 두 필드가 **같은** 잘못된 타입인 케이스로는 순서를
        # 뒤집어도 아무것도 드러나지 않는다 — 순서를 지키는 게이트는 반드시
        # **혼합 타입** 입력이어야 한다(`test_mixed_type_timecodes_...`).
        _require_int_timecodes(event, raw_index, path)
        # **부호 검사가 역전 검사보다 먼저여야 한다.** `(1000, -3000)`은 둘 다 해당하는데
        # 역전 메시지는 `start=1000ms > end=-3000ms`만 말하고 음수를 숨긴다 — 사용자는
        # 역전만 고치고 여전히 재생 불가능한 파일을 얻는다(`test_negative_check_runs_
        # before_the_reversal_test`가 고정한다).
        _require_non_negative_timecodes(event, raw_index, path)
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
