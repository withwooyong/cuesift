"""진행 표시와 비대화형 감지 (FR-8.5 · 설계 §4~§6).

**이 모듈은 `cli.py`를 임포트하지 않는다.** 반대 방향이면 라이브러리가
CLI에 의존하게 되고, `translate/engine.py`가 CLI를 끌고 들어온다 (설계 §4.1).

**`rich`를 쓰지 않는다**(설계 D6). `rich`는 typer의 전이 의존이라 typer가
그것을 떼면(`typer-slim`이 이미 있다) 조용한 `ImportError`가 된다. 더 큰
이유는 실측 전례다 - rich가 `FORCE_COLOR`로 **비TTY인 CI에서 색을 켜**
`--help` 출력의 옵션 이름을 쪼갠 사고가 있었다.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Literal

ProgressStyle = Literal["interactive", "plain", "off"]

# plain 모드의 이정표 간격(%p). 이 값이 크면 수십 분짜리 CI 작업에서 침묵이
# 길어져 "멈춤"과 "느림"이 구분되지 않고, 작으면 세그먼트 4000개·배치 10에서
# 언어당 400줄이 된다 (설계 D12).
_PLAIN_STEP_PCT = 10

# 단계 이름 뒤 점선이 끝나는 열. **여기서는 CJK 폭 보정을 하지 않는다** -
# 한글은 터미널에서 두 칸을 먹어 점선 길이가 어긋나지만, 어긋나도 잃는
# 정보가 없다. 점선은 눈으로 열을 맞추기 위한 장식이다.
#
# **`_display_width`를 쓰는 자리(`_paint`·`clear`)와 판단이 다른 이유가
# 바로 이것이다.** 거기서 폭을 적게 세면 지우거나 밀어 낼 칸이 모자라
# **이전 줄의 글자가 화면에 남는다** - 그것은 장식이 아니라 정보 손실이다.
_LABEL_WIDTH = 22

# 거짓으로 읽는 값들. 나머지는 전부 참이다.
_FALSE_WORDS = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    """진척 이벤트. **단계 이름을 싣지 않는다** (설계 D2).

    단계는 표현 계층의 개념이고 호출자(CLI)가 이미 안다. 라이브러리가
    "번역 중"이라는 문자열을 알면 출력 문구를 바꿀 때 라이브러리를 고치게
    된다. `(done, total)` 둘뿐이라 나중에 다른 계층(QE, v0.2)이 붙어도
    타입이 바뀌지 않는다 (설계 §9 R4).
    """

    done: int
    total: int


ProgressCallback = Callable[[ProgressUpdate], None]


def env_flag(name: str) -> bool | None:
    """환경변수를 3상으로 읽는다. 세우지 않았으면 `None` (설계 §5).

    **판독 규칙을 여기 하나만 둔다.** `cli._prefer_env`는 문자열 전용이라
    재사용할 수 없고, 두 곳에 규칙이 생기면 `CUESIFT_PROGRESS=false`가
    참이 되는 날이 온다.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() not in _FALSE_WORDS


def detect_style(stream: IO[str] | None = None) -> ProgressStyle:
    """대화형이면 `interactive`, 아니면 `plain`. **`off`는 내지 않는다.**

    끄고 켜는 것은 `resolve_style`의 일이다 - 감지는 *어떻게* 그릴지만
    정한다 (설계 D7).

    셋 중 **하나라도** 해당하면 비대화형이다. `isatty`만 보면 TTY를
    할당하는 CI 셸(`docker run -t`, 일부 self-hosted 러너)에서 `\\r`이 로그
    파일에 그대로 남는다 (설계 D8).

    `NO_COLOR`는 신호가 아니다 - 색에 관한 규격인데 이 렌더러는 색을 쓰지
    않으므로, 넣으면 규격을 넘어 해석하는 것이 된다.
    """
    target = sys.stderr if stream is None else stream
    if os.environ.get("CI"):
        return "plain"
    if os.environ.get("TERM") == "dumb":
        return "plain"
    try:
        interactive = bool(target.isatty())
    except (AttributeError, ValueError):
        # 닫힌 스트림의 `isatty()`는 `ValueError`를 낸다. 판정할 수 없으면
        # 제어문자를 쓰지 않는 쪽이 안전하다.
        interactive = False
    return "interactive" if interactive else "plain"


def resolve_style(enabled: bool | None, stream: IO[str] | None = None) -> ProgressStyle:
    """켜고 끄기(`enabled`)와 스타일(감지)을 합친다.

    `enabled`가 `None`이면 **켠다.** 진행 표시의 기본은 on이고, 자동 감지가
    정하는 것은 `interactive`인지 `plain`인지뿐이다 (설계 §5 흐름도).
    """
    if enabled is False:
        return "off"
    return detect_style(stream)


def _display_width(text: str) -> int:
    """터미널이 실제로 먹는 칸 수. 광폭(`W`)·전각(`F`)은 두 칸이다.

    **문자 수가 아니어야 한다.** 라벨이 `[en] 번역`·`[en] 리포트`라
    `len()`으로 세면 한글 한 글자마다 한 칸씩 모자라고, `clear()`가
    그만큼 덜 지워 `%)` 같은 꼬리가 화면에 남는다 (리뷰 라운드 2 F4).
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _decorate(label: str) -> str:
    """`[en] 번역 ............ ` - 점선으로 열을 맞춘다 (설계 §6)."""
    pad = max(1, _LABEL_WIDTH - len(label))
    return f"{label} {'.' * pad} "


class ProgressReporter:
    """stderr에 진행을 그린다. **stdout은 쓰지 않는다** (설계 D9).

    `interactive`는 `\\r`로 한 줄을 덮어쓰고 단계가 끝나면 개행해 확정한다.
    `plain`은 갱신 없이 이정표를 누적하며 제어문자를 전혀 쓰지 않는다.

    **`\\r`은 커서 위치라는 상태를 남긴다.** 그래서 같은 자원을 쓰는 다른
    코드가 이 상태를 알아야 하고, 그것이 `clear()`와 `install()`의 존재
    이유다 (설계 §4.3 ②).
    """

    def __init__(self, style: ProgressStyle, stream: IO[str] | None = None) -> None:
        self._style = style
        self._stream = sys.stderr if stream is None else stream
        self._label = ""
        # 지금 떠 있는 `\r` 줄의 **표시 폭**. 0이면 떠 있는 줄이 없다.
        # **문자 수가 아니다** - 한글 라벨에서 둘이 갈리고, 적게 세면
        # `clear()`가 덜 지워 이전 줄의 꼬리가 화면에 남는다.
        self._line_len = 0
        # 마지막으로 plain 이정표를 낸 퍼센트. **시작값은 0이어야 한다.**
        # -1이면 첫 이정표가 `_PLAIN_STEP_PCT - 1`(9%)에서 나고 이후 전부
        # 한 칸씩 밀려 19·29·...·99가 되며, 99 뒤에 100이 또 나가 이정표가
        # 하나 더 생긴다. 0이면 10·20·...·100으로 딱 떨어진다.
        self._last_pct = 0
        self._disabled = style == "off"

    @property
    def disabled(self) -> bool:
        """쓰기 실패로 영구 비활성화됐는지 (설계 D10)."""
        return self._disabled

    def phase(self, label: str) -> None:
        """새 단계를 연다. 출력은 하지 않는다.

        **`total`을 받지 않는다.** 총량은 `ProgressUpdate`가 싣고 다니므로
        (D2) 리포터가 따로 알면 두 곳의 총량이 갈라진다 - Tier 1은 총량이
        `collect_tier1` 안에서 정해져 호출자가 미리 알 수도 없다.
        """
        self.clear()
        self._label = label
        self._last_pct = 0

    def update(self, update: ProgressUpdate) -> None:
        """진척을 그린다. `on_progress` 콜백으로 그대로 넘기는 자리다."""
        if self._disabled or update.total <= 0:
            # 세그먼트 0개짜리 자막은 실재한다(빈 파일). 여기서 나누면
            # 번역이 아니라 진행 표시가 파이프라인을 무너뜨린다.
            return
        pct = min(100, update.done * 100 // update.total)
        body = f"{update.done}/{update.total} ({pct}%)"
        if self._style == "interactive":
            self._paint(f"{_decorate(self._label)}{body}")
            return
        # 100%는 항상 낸다 - 10%p 규칙만 두면 마지막 조각이 10%p에 못
        # 미칠 때 진행이 97%에서 끝난 것처럼 보인다 (설계 D13).
        if update.done < update.total and pct < self._last_pct + _PLAIN_STEP_PCT:
            return
        self._last_pct = pct
        self._emit(f"{self._label} {body}")

    def done(self, note: str = "완료") -> None:
        """단계를 확정한다. `interactive`에서는 여기서 개행이 나간다."""
        if self._disabled:
            return
        if self._style == "interactive":
            self._paint(f"{_decorate(self._label)}{note}")
            self._raw("\n")
        else:
            self._emit(f"{self._label} {note}")
        self._line_len = 0

    def clear(self) -> None:
        """떠 있는 `\\r` 줄을 지운다 (설계 D11).

        `_echo`가 쓰기 **전에** 부른다. 이것이 없으면 진행 줄과 경고가
        한 줄에 겹친다 - `_translate_one`은 용어집 실패·캐시 경고를 실제로
        그 자리에서 낸다.
        """
        if self._disabled or self._style != "interactive" or self._line_len == 0:
            return
        self._raw("\r" + " " * self._line_len + "\r")
        self._line_len = 0

    def _paint(self, text: str) -> None:
        # 이전 줄보다 짧아지면 꼬리가 남는다 - `1000/4120` 뒤 `340/412`가
        # `340/412 (82%)0`으로 보인다. 공백으로 밀어 낸다.
        #
        # **`_display_width`여야 한다.** `len(text)`로 세면 한글 라벨에서
        # 패딩이 글자 수만큼 모자라 3연속 갱신(긴 줄 → 짧은 줄 → 중간 줄)에서
        # 꼬리가 남는다.
        width = _display_width(text)
        pad = max(0, self._line_len - width)
        self._raw("\r" + text + " " * pad)
        self._line_len = width + pad

    def _emit(self, text: str) -> None:
        self._raw(text + "\n")

    def _raw(self, text: str) -> None:
        try:
            self._stream.write(text)
            self._stream.flush()
        except (OSError, ValueError):
            # **영구 비활성화한다** (설계 D10). 진행 표시는 부수적이고,
            # 닫힌 파이프에서 예외가 새면 `_TolerantOutput`과 `_echo`가
            # 지켜 온 종료 코드 계약이 깨진다 - `2>&1 | head -1`로 잘라
            # 읽는 사용자에게 종료 코드가 흐려진다.
            #
            # **`ValueError`도 여기 있어야 한다.** 닫힌 스트림에 쓰면
            # `OSError`가 아니라 `ValueError: I/O operation on closed file`이
            # 난다. 같은 모듈의 `detect_style`이 `isatty()`의 `ValueError`를
            # 이미 명시적으로 방어하므로, 여기서만 빼면 한 모듈 안에서
            # 닫힌 스트림의 취급이 갈린다 (리뷰 라운드 2 F5).
            #
            # **`cli.run()`의 "ENOSPC는 어느 층도 삼키지 않는다"와 어긋나는
            # 층이 여기다.** 리포터는 `sys.stderr`(= `_TolerantOutput` 프록시)에
            # 쓰므로 EPIPE는 프록시가 먼저 삼키고, 프록시가 일부러 재전파하는
            # ENOSPC(errno 28)는 이 `except`가 삼킨다. **그래도 삼키는 쪽을
            # 고른다** - 진행 줄은 부수적이고 실제 출력은 `_echo`가 내므로
            # 잘린 진행 줄이 종료 코드를 바꾸지 않는다. 디스크가 찼다면
            # `_echo`의 쓰기가 같은 ENOSPC를 만나 그쪽에서 보고된다.
            self._disabled = True
            self._line_len = 0


# 활성 리포터. **전역 상태다** (설계 §9 R1). 이것을 두는 이유는 `_echo`
# 호출부가 49곳이라 리포터를 인자로 흘리려면 전부를 고쳐야 하기 때문이다.
# 설치·해제 자리를 `translate` 커맨드 하나로 한정하고, `conftest.py`가
# 테스트마다 초기화한다.
_active: ProgressReporter | None = None


def install(reporter: ProgressReporter | None) -> None:
    """활성 리포터를 세운다. `None`이면 해제한다.

    **`translate` 커맨드에서만 부른다.** 다른 곳에서 부르면 전역 상태의
    수명이 커맨드 경계를 넘어 테스트가 서로 오염된다.
    """
    global _active
    _active = reporter


def active() -> ProgressReporter | None:
    """활성 리포터. 테스트가 상태를 확인할 수 있게 노출한다."""
    return _active


def clear_active() -> None:
    """활성 리포터가 있으면 떠 있는 줄을 지운다 (설계 D11).

    **`_echo`가 쓰기 전에 부른다.** 리포터가 없거나 `plain`·`off`면
    아무 일도 하지 않으므로 호출 비용이 사실상 0이다.
    """
    if _active is not None:
        _active.clear()
