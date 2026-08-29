"""테스트 공용 — `live` 마커 게이트 + rich 렌더링 정규화.

**이 파일은 성격이 다른 둘을 담고 있다.** 헤드라인이 뒤엣것만 말하면 파일을
여는 사람이 앞엣것을 통째로 놓친다.

| 무엇 | 어디 | 왜 여기 있나 |
| --- | --- | --- |
| **`live` 마커 게이트** (`_check_on_import` · `pytest_configure`) | 위쪽 | 아래 참조 |
| rich 렌더링 정규화 (`strip_rich_decoration` · `normalize_rich_message`) | 가운데 | 원래의 용도 |
| 번역 대본 헬퍼 (`scripted_at` · `blank_at`) | 아래쪽 | 두 파일이 공유한다 |

**게이트를 별도 모듈로 빼지 않는다.** 뺀다면 conftest가 그것을 임포트해야
하는데, **그 임포트문이 곧 새로운 무력화 지점**이 된다 - 지우면 게이트가
조용히 사라지고, 그것이 바로 이 게이트가 막으려는 실패 형태다. 지금은
`conftest.py` 자체가 사라지면 `test_cli.py`·`test_cli_check.py`·
`test_translate_api.py` 셋이 임포트에 실패해 exit 2로 요란하게 죽는다 -
**배선을 이미 세 곳이 지킨다.**

---

## rich 렌더링 정규화

**왜 필요한가.** `typer`는 `--help`와 사용법 오류를 `rich`로 그린다. 그 출력에는 우리가
쓴 적 없는 **박스 테두리**가 섞이고 **강제 개행**이 들어간다. 둘 다 우리 계약이 아닌데
테스트가 그것까지 검사하면 **호스트 플랫폼에 따라 결과가 갈린다.**

실측 (2026-08-13):

| 환경 | `rich`의 모서리 문자 | cp949 |
| --- | --- | --- |
| Windows (`legacy_windows=True`) | `┌┐└┘` U+250C·2510·2514·2518 | **인코딩 된다** |
| Linux CI (`legacy_windows=False`) | `╭╮╰╯` U+256D~2570 | **인코딩 안 된다** |
| 실제 실행 + 리다이렉트 | **박스를 아예 안 그린다** | 무관 |

**세 번째 줄이 핵심이다** — 사용자가 `cuesift --help > help.txt`를 하면 `rich`는 박스를
그리지 않는다(터미널이 아니라서). 즉 박스 문자는 **`CliRunner`가 만들어 낸 산물**이지
사용자가 만나는 것이 아니다. 그런데 그것을 검사하면 **Windows에서는 통과하고 Linux CI에서만
실패한다.** 실제로 그렇게 됐다.

같은 이유로 **긴 문자열·경로를 통째로 단언하면 안 된다.** `rich`가 박스 폭에 맞춰 개행을
넣는데, 그 위치는 임시 디렉터리 경로 길이에 따라 달라져 **Windows와 Linux에서 다른 곳에서
끊긴다.** 실측: 로컬에서는 `cp949-spec.yaml`이 살아남고 `utf-8로 읽을 수 없다`가 끊겼는데,
CI에서는 정확히 반대였다.
"""

from __future__ import annotations

import json
import pathlib
import re
import shlex
import tomllib
from collections.abc import Iterable, Mapping

import pytest
from tests.fakes.provider import ScriptedProvider

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

_GATE_HINT = (
    " || 이 검사는 `live` 마커 게이트를 지킨다(설계 §9.2). "
    "`-c`·`--override-ini`로 임시로 덮는 것도 여기 걸린다 - "
    "설정을 바꾸려면 pyproject.toml을 직접 고쳐라."
)


def _check_addopts(pyproject: pathlib.Path) -> None:
    """**임포트되는 것만으로 돈다.** 훅도 아니고 테스트도 아니다.

    아래 `pytest_configure`가 이미 같은 것을 보는데 왜 또 보는가 - **두
    방어선의 실패 모드가 다르기 때문이다.** 실측(2026-08-16):

    | 변이 | 훅 | 이 검사 |
    | --- | --- | --- |
    | `addopts`에 `-m live` 덧붙임 | 잡는다 (exit 4) | 잡는다 |
    | 훅 함수 개명(`pytest_` 접두사 제거) | **무력화** — pluggy가 등록하지 않는다 | 무관 |
    | 위 **둘을 동시에** (M9) | 무력화 | **잡는다** |

    셋째 행이 이 함수의 존재 이유다. 훅이 끊기면 `-m live` 덧붙임이 되살아나고,
    그것을 감시하는 테스트마저 840개와 함께 deselect되어 **exit 0으로 초록이
    난다**(실측). 모듈 최상위 코드는 **개명할 이름도 없고 deselect의 대상도
    아니다.**

    ## 무엇을 못 막는가

    `config`가 없어 pytest가 **실제로 읽은** ini가 무엇인지는 모른다 - 그것은
    훅만 볼 수 있다. 따라서 **훅 개명 + `pytest.ini` 추가** 조합은 이 검사를
    지나간다(실측: **exit 0, 0건 사망** - 전량 deselect). pyproject의 `addopts`는
    멀쩡한 채로 pytest가 다른 파일을 읽기 때문이다. "모든 조합에서 살아남는다"가
    아니라 **"pyproject를 건드리는 조합에서 살아남는다"** 가 정확한 서술이다.

    **닫지 않기로 한 것이지 못 닫는 것이 아니다.** 임포트 시점에도
    `_REPO_ROOT.iterdir()`로 경쟁 ini의 존재는 보이고 `sys.argv`도 이미 차 있다
    (실측: ①에 4줄을 넣으니 그 조합이 exit 4로 잡혔다). 넣지 않는 이유는
    비용 대비 이득이다 - 두 파일을 동시에 고쳐야 성립하는 조합이고,
    `setup.cfg`를 다른 목적으로 두는 정상 사용에서 거짓 실패가 난다.
    자세한 근거는 설계 §9.2.

    `pyproject`를 **인자로 받는 것**은 테스트가 로직을 검사하는 통로다. 없으면
    본문에 테스트를 걸 수 없어 **`return` 한 줄로 바꿔도 0건이 죽는다**(실측 N7).

    그러나 이 인자를 **최상위 호출까지 노출하면 안 된다.** 아래
    `_check_on_import`가 인자를 받지 않는 이유가 그것이다.
    """
    ini = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    raw = ini["tool"]["pytest"]["ini_options"]["addopts"]
    # pytest의 `addopts`는 `args` 타입이라 **TOML 배열도 완전히 유효한 표기**다.
    # 무조건 `shlex.split`하면 리스트에서 `AttributeError: 'list' object has no
    # attribute 'read'`가 난다(실측). 훅 초판이 "리스트를 다시 쪼갠" 것의 거울상이다.
    addopts = list(raw) if isinstance(raw, list) else shlex.split(raw)

    # **아래 두 조건은 `pytest_configure`와 일부러 중복해서 적는다.**
    #
    # 공유 헬퍼로 묶었더니 세 겹이 **한 술어에 매달렸다** - 그 함수 첫 줄에
    # `return []`을 넣으면 이 검사와 훅이 **동시에** 눈이 멀고, 그 상태에서
    # `-m live`를 덧붙이면 테스트는 deselect로 사라져 **0건이 죽었다**(실측 N9:
    # exit 0, 0건 사망 - 전량 deselect). 방어를 나눈 목적이 통째로 무너진다.
    #
    # `tests/test_translate_api.py`의 `_REQUIRED`가 `__all__`을 일부러 중복하는
    # 것과 같은 이유다 - 검사와 검사 대상이 같은 출처를 쓰면 검사가 아니다.
    problems: list[str] = []
    if addopts.count("-m") != 1:
        problems.append(f"addopts의 -m이 하나가 아니다(뒤가 이긴다): {addopts}")
    elif addopts[addopts.index("-m") + 1] != "not live":
        problems.append(f"addopts의 기본 제외식이 'not live'가 아니다: {addopts}")

    if problems:
        raise pytest.UsageError("게이트 설정이 어긋났다: " + " / ".join(problems) + _GATE_HINT)


def _check_on_import() -> None:
    """배선 전용. **인자를 받지 않는다.**

    `_check_addopts`가 경로를 받아야 테스트가 가능한데, 그 인자가 최상위
    호출에까지 열려 있으면 **호출에 decoy 경로를 붙이는 것만으로 이 층이
    조용히 죽는다.** 실측:

    | 변이 | 결과 |
    | --- | --- |
    | 최상위 호출에 decoy 경로 인자 | `848 passed`, exit 0. **0건 사망** |
    | 위 + 훅 개명 + `-m live` | `1 skipped, 848 deselected`, exit 0. **우회 부활** |

    호출은 그대로 있고 이름도 맞는데 **다른 파일을 검사한다.** ast 배선
    검사가 "호출이 있는가"만 보면 이것을 통과시킨다.

    **인자를 0개로 두면 그 변이가 테스트 없이 죽는다** - 파이썬이 임포트
    시점에 `TypeError`를 내기 때문이다. deselect도 개명도 우회할 수 없는
    자리에서 **문법이 직접 막는 것**이 검사보다 강하다.

    **이 seam은 로직 테스트를 가능하게 만드는 변경이 들여왔다.** 한 라운드의
    수정이 다른 라운드의 보호를 깎을 수 있다는 실례다.
    """
    _check_addopts(_REPO_ROOT / "pyproject.toml")


_check_on_import()


def pytest_configure(config: pytest.Config) -> None:
    """게이트 설정이 바뀌었으면 **수집 전에** 실행을 멈춘다 (설계 §9.2).

    이 검사가 `tests/test_translate_api.py`가 아니라 여기 있는 이유는 하나다 -
    **테스트로 두면 자기를 무력화하는 변이에 같이 쓸려 나간다.** 실측:
    `addopts` 끝에 `-m live`를 덧붙이면 뒤의 `-m`이 이겨 `1 skipped,
    832 deselected`가 되는데(훅을 넣기 전 실측), 그 832개에 **감시자 자신이
    들어 있다.** exit 0이라 CI는 초록이고 832개가 한 번도 돌지 않은 것은
    아무도 모른다.

    이 저장소가 CI 5회 연속 실패를 못 본 것이 정확히 이 형태다 -
    "통과했나"는 초록인데 **"무엇을 대상으로 통과했나"가 통째로 바뀌었다.**

    `pytest_configure`는 마커 필터링보다 **먼저** 돌고 deselect의 대상이
    아니라서 이 자리만이 안전하다. 명령줄의 `-m live`는 여기 걸리지 않는다 -
    보는 것은 `addopts`(기본값)이지 이번 실행의 마커식이 아니다.
    """
    problems: list[str] = []

    # pytest.ini·tox.ini가 생기면 pyproject보다 그쪽이 이긴다. 우리 설정이
    # 통째로 무시된 채 초록이 나는 경로다.
    #
    # `is None` 가드는 **실측 근거가 있다** - `inipath`는 설정 파일을 못 찾으면
    # `None`이고, 그 자리에 `.resolve()`를 붙이면 게이트가 막으려던 상황에서
    # 오히려 `AttributeError`라는 새 크래시가 된다.
    #
    # `.resolve()`는 **방어적 조치이고 재현된 실패 사례가 없다.** `_REPO_ROOT`가
    # `Path(__file__).resolve()`라 링크가 풀린 반면 `config.inipath`는 pytest의
    # `absolutepath()`에서 와 풀리지 않으므로 이론상 어긋날 수 있다. 그러나
    # junction(`mklink /J`) 경유로 실행해 본 결과 **거짓 실패가 나지 않았다** -
    # pytest가 이미 실경로로 정규화해 넘긴다. 비용이 0이라 남기지만,
    # **"맞추지 않으면 exit 4가 난다"는 관찰된 사실이 아니다.**
    if config.inipath is None or config.inipath.resolve() != (_REPO_ROOT / "pyproject.toml"):
        problems.append(f"pytest가 읽은 설정이 우리 pyproject가 아니다: {config.inipath}")

    # 미등록 마커를 에러로 만드는 플래그. 꺼지면 설계 §9.2의 전제가 무너진다.
    if not config.getoption("strict_markers"):
        problems.append("--strict-markers가 꺼졌다")

    # **다시 shlex로 쪼개면 안 된다.** `getini("addopts")`는 pytest가 이미
    # 분리해 둔 리스트라, 합쳤다 다시 나누면 `"not live"`가 `"not"`과
    # `"live"` 두 토큰이 되어 아래 비교가 정상 설정에서도 실패한다(실측).
    addopts = list(config.getini("addopts"))

    # **`_check_on_import`와 일부러 중복한다.** 공유 헬퍼로 묶었을 때 그 함수
    # 하나를 무력화하면 두 층이 동시에 눈이 멀었다(실측 N9). 근거는 그쪽 주석에.
    if addopts.count("-m") != 1:
        problems.append(f"addopts의 -m이 하나가 아니다(뒤가 이긴다): {addopts}")
    elif addopts[addopts.index("-m") + 1] != "not live":
        problems.append(f"addopts의 기본 제외식이 'not live'가 아니다: {addopts}")

    if problems:
        # 힌트를 함께 낸다. `-c other.ini`나 `--override-ini=addopts=`도 여기
        # 걸리는데(설계 의도다), 그 사실을 모르는 사람은 "게이트 설정이
        # 어긋났다"만 보고 **자기가 뭔가 깼다고 오해한다.**
        raise pytest.UsageError("게이트 설정이 어긋났다: " + " / ".join(problems) + _GATE_HINT)


# `cuesift.yaml` 자동 탐색 차단 (FR-8.4 · 설계 D2).
#
# **자동 탐색은 cwd에 의존하므로 테스트 실행 환경이 CLI 기본값을 바꾼다.**
# 리포 루트에 `cuesift.yaml`을 한 줄 두면(설계 문서가 그렇게 하라고 읽히는
# 주석이 `.gitignore`에 있었다) CI에는 없는 파일이 로컬에서만 22개 옵션의
# 기본값을 갈아치운다 - 실측으로 `dry_run: true` 한 줄이 **81 failed**를
# 냈다. 이 저장소는 로컬과 CI의 게이트가 갈려 CI 5회 연속 실패가 숨은
# 전례가 있어, 반대 방향(로컬만 빨강)이어도 같은 부채다.
#
# **cwd를 바꾸지 않는다.** 전역 `monkeypatch.chdir(tmp_path)`는 1456건
# 전체의 cwd를 흔들어 상대 경로를 쓰는 기존 테스트를 깬다. 환경변수 탈출구도
# 두지 않는다 - 설계 §1.3이 "환경변수 층 추가"를 범위 밖에 뒀고, 그것은
# 프로덕션 코드에 테스트 전용 통로를 내는 일이다. **탐색 지점 하나만** 끈다.
_AUTO_DISCOVERY_FIXTURE = "설정_자동_탐색"


@pytest.fixture
def 설정_자동_탐색() -> None:
    """자동 탐색을 **진짜로** 켠다 (opt-in).

    이 fixture를 요청한 테스트에서만 아래 autouse가 손을 뗀다. 자동 탐색
    자체를 재는 테스트(`test_cli_config.py`의 3건)가 여기 해당한다 - 그것들이
    없으면 이 차단이 기능을 통째로 덮어 **아무도 D2를 검사하지 않는다.**
    """
    return None


@pytest.fixture(autouse=True)
def _설정_자동_탐색_차단(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cuesift.cli._discover_config`를 "없음"으로 고정한다.

    `request.fixturenames`는 opt-in fixture가 실제로 만들어지기 전에도
    이름을 갖고 있어, 순서에 기대지 않고 판정할 수 있다.
    """
    if _AUTO_DISCOVERY_FIXTURE in request.fixturenames:
        return
    monkeypatch.setattr("cuesift.cli._discover_config", lambda: None)


@pytest.fixture(autouse=True)
def _진행_표시_차단(monkeypatch: pytest.MonkeyPatch) -> None:
    """진행 표시를 **기본으로 끈다** (FR-8.5 · 설계 §9 R2).

    테스트 실행은 비TTY라 자동 감지가 `plain`을 고르고, 그러면 진행 줄이
    기존 stderr 단언에 섞인다. 착수 시점 1480건 중 다수가 한꺼번에 죽는다.

    **환경변수여야 한다.** `monkeypatch.setattr`로 모듈 속성을 고정하면
    인프로세스 테스트만 막히고 `test_cli_pipe.py`가 띄우는 **서브프로세스**는
    그대로 진행을 낸다 - 위 `_설정_자동_탐색_차단`이 이미 같은 한계를 갖는다.

    진행을 재는 테스트는 `--progress`로 켠다. **CLI가 환경변수를 이긴다**
    (설계 D5).
    """
    monkeypatch.setenv("CUESIFT_PROGRESS", "0")


# 유니코드 Box Drawing 블록. `rich`의 패널 테두리가 전부 여기 있다.
_BOX_DRAWING = re.compile(r"[─-╿]")

# ANSI 이스케이프(색·커서). `CliRunner`는 보통 색을 끄지만 환경에 따라 샌다.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_rich_decoration(text: str) -> str:
    """`rich`가 **그린** 것만 지운다. 우리가 **쓴** 문자열은 그대로 둔다.

    인코딩 계약(`cp949`에서 출력이 깨지지 않는가)을 검사할 때 쓴다. 테두리를 남기면
    호스트 플랫폼의 `legacy_windows` 값을 검사하는 셈이 되고, 그것은 우리 계약이 아니다.
    """
    return _ANSI.sub("", _BOX_DRAWING.sub("", text))


def normalize_rich_message(text: str) -> str:
    """부분 문자열 단언용 — 테두리·ANSI를 지우고 **공백을 전부 없앤다.**

    `rich`의 강제 개행이 어디에 떨어지든 같은 결과를 내게 만든다. 단언하는 쪽도 같은
    함수를 통과시켜야 하므로 `assert normalize(needle) in normalize(haystack)` 형태로 쓴다.

    **공백을 지우는 것이 단언을 약하게 만들지 않는다** — 확인하려는 것은 "이 정보가
    메시지에 들어 있는가"이지 "줄바꿈이 어디 있는가"가 아니다. 오히려 개행 위치에
    의존하던 이전 단언이 **내용이 맞아도 실패**해서 계약을 검증하지 못했다.
    """
    return re.sub(r"\s+", "", strip_rich_decoration(text))


def scripted_at(texts: Mapping[int, str], count: int) -> ScriptedProvider:
    """인덱스별 번역문을 지정한 배치 응답 하나를 내는 가짜.

    지정하지 않은 인덱스는 `EN{i}`로 답한다 - 한글이 남지 않고 짧아
    Tier 0 신호를 하나도 내지 않는 "깨끗한" 번역문이다. 그래서 신호를
    **일부러 심은 인덱스만** 걸린다.

    **응답이 하나뿐인 것은 배치 1회로 끝난다는 전제다.** `DEFAULT_BATCH_SIZE`가
    10이므로 `count <= 10`에서만 성립한다 - 넘기면 `ScriptedProvider`가
    "대본이 소진됐다"로 죽는다(조용히 통과하지 않는다).
    """
    items = [{"id": i, "text": texts.get(i, f"EN{i}")} for i in range(count)]
    return ScriptedProvider([json.dumps({"translations": items}, ensure_ascii=False)])


def blank_at(indices: Iterable[int], count: int) -> ScriptedProvider:
    """지정한 인덱스만 **공백 번역**으로 답하는 가짜.

    공백 번역은 `engine.py:419`가 `reason="empty_translation"`으로 실패
    처리한다 - 응답 형식은 올바르므로 개별 폴백이 개입하지 않아 호출이
    배치 1회로 끝난다. `EchoProvider(drop_last=True)`는 이 목적에 쓸 수
    없다: 배치가 개수 불일치로 실패하면 폴백이 개별 호출로 재시도하고
    거기서는 `len(items) > 1`이 거짓이라 **전부 성공한다**.

    **`test_cli_triage.py`와 `test_cli_review_out.py`가 이것을 공유한다.**
    두 파일에 복제돼 있던 것을 여기로 모았다 - 한쪽만 고쳐지면 두 테스트가
    조용히 다른 것을 재게 된다. 실제로 복제된 상태였고 리뷰가 잡았다.
    """
    return scripted_at(dict.fromkeys(indices, "   "), count)
