"""번역 계층의 공개 표면 (요구사항정의서 §5.2 FR-2.1~2.8).

**이 파일이 지키는 것은 `__all__`이라는 선언 하나다.** 선언에 테스트가 없으면
그것은 계약이 아니라 주석이다 - `__all__`에서 이름이 하나 빠져도 하위 모듈
경로(`cuesift.translate.engine`)로는 여전히 임포트되므로, 아무 테스트도 죽지
않는다. 깨지는 것은 호출자가 `from cuesift.translate import *`를 쓴 그날이다.

그래서 검사를 **양방향**으로 건다.

- `__all__` -> 실제 속성: 오타를 잡는다
- 필수 이름 -> `__all__`: **`__all__`에서 지운 것**을 잡는다
- 하위 모듈의 공개 심볼 -> `__all__`: 새 심볼을 재수출하지 않은 것을 잡는다

`hasattr`만 쓰면 세 방향이 전부 무력해진다. `__init__.py`가 하위 모듈을
임포트하는 순간 `cuesift.translate.batch`도 속성이 되어, 이름을 `__all__`에서
지워도 `hasattr`은 계속 True다.

파일 끝의 `live` 마커 검사도 같은 성격이다 - 그쪽은 `__all__`이 아니라
**게이트 설정**이라는 선언을 지킨다.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import pkgutil
import tomllib
from types import ModuleType

import httpx
import pytest

import cuesift.translate as t
from conftest import pytest_configure


def _submodules() -> tuple[ModuleType, ...]:
    """`cuesift.translate`의 하위 모듈을 **전부** 찾는다.

    **손으로 적은 목록을 쓰면 안 된다.** 새 모듈이 생기고 여기 추가하지
    않으면 그 모듈의 공개 심볼만 검사 없이 지나가는데, 그것이 이 저장소가
    반복해서 물린 "복제된 구조의 두 번째 사본이 검사에서 빠진다" 자리다.
    실측으로 확인했다 - 손 관리 시절에는 새 모듈을 추가해도 **0건이 죽었다.**

    `pkgutil`은 표준 라이브러리라 의존성 고정 규율(런타임 4개·dev 3개)에
    걸리지 않는다.
    """
    return tuple(
        importlib.import_module(f"{t.__name__}.{info.name}")
        for info in pkgutil.iter_modules(t.__path__)
    )


_MODULES: tuple[ModuleType, ...] = _submodules()

# 호출자가 이름으로 부르는 것들. `__all__`과 **일부러 중복해서** 적는다 -
# `__all__`을 훑어서 검사하면 `__all__`에서 지운 이름은 검사 대상에서도 같이
# 사라져 변이가 통과한다. 지금의 호출자는 WP7b(CLI 배선)와 WP8(자가일관성)이다.
_REQUIRED = (
    "translate_segments",
    "TranslationResult",
    "SegmentFailure",
    "TokenUsage",
    "Provider",
    "ProviderError",
    "RetryableProviderError",
    "FatalProviderError",
    "OpenAICompatibleProvider",
    "InvalidResponseError",
    "build_messages",
)


def _public_toplevel_names(module: ModuleType) -> set[str]:
    """모듈이 **직접 정의한** 공개 최상위 이름.

    `dir(module)`로는 안 된다 - 그 모듈이 임포트한 이름(`Segment`·`httpx`·
    `TokenUsage`)까지 섞여 들어와 "재수출되지 않았다"는 거짓 실패를 낸다.
    ast로 정의문(class·def·대입)만 고르면 임포트가 원천적으로 빠진다.

    클래스 본문은 보지 않는다. `tree.body`만 순회하므로 `ChatMessage._ROLES`
    같은 클래스 속성은 애초에 후보가 아니다.
    """
    source = pathlib.Path(module.__file__ or "").read_text(encoding="utf-8")
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


@pytest.mark.parametrize("name", _REQUIRED)
def test_필수_공개_이름이_all에_있다(name: str) -> None:
    """`hasattr`이 아니라 `__all__` 멤버십을 본다.

    `hasattr`로 검사하면 이 테스트가 `__all__`에서의 삭제를 **잡지 못한다** -
    이름은 모듈 속성으로 계속 남기 때문이다.
    """
    assert name in t.__all__, f"{name}이 공개 API에서 빠졌다"


def test_all의_모든_이름이_실제_속성이다() -> None:
    """오타 하나가 `from cuesift.translate import *`를 통째로 죽인다."""
    missing = [name for name in t.__all__ if not hasattr(t, name)]
    assert missing == [], f"__all__에 있으나 속성이 없다: {missing}"


def test_스타_임포트가_실제로_동작한다() -> None:
    """`__all__`을 실제로 소비하는 유일한 문법을 직접 실행한다.

    앞의 두 테스트는 `__all__`을 **읽기만** 한다. 파이썬이 그 목록을 어떻게
    쓰는지는 검증하지 않으므로, 실제 문법을 한 번 돌려 둔다.
    """
    namespace: dict[str, object] = {}
    exec("from cuesift.translate import *", namespace)

    exported = {name for name in namespace if not name.startswith("__")}
    assert exported == set(t.__all__)


def test_재수출된_객체가_하위_모듈의_그것과_동일하다() -> None:
    """이름이 같아도 **객체가 다르면** 계약이 깨진다.

    `except ProviderError`가 안 잡히거나 `isinstance` 검사가 어긋나는 형태로
    드러나는데, 이름만 보는 검사는 그것을 통과시킨다.
    """
    for name in t.__all__:
        owners = [m for m in _MODULES if name in _public_toplevel_names(m)]
        assert len(owners) == 1, f"{name}을 정의한 모듈이 정확히 하나여야 한다: {owners}"
        assert getattr(t, name) is getattr(owners[0], name), name


def test_하위_모듈의_공개_심볼이_빠짐없이_재수출된다() -> None:
    """공개 표면의 정의를 코드에 못 박는다.

    정책: **하위 모듈의 밑줄 없는 최상위 이름은 전부 `__all__`에 있어야 한다.**
    감추고 싶으면 이름 앞에 `_`를 붙인다 - 그것이 이 저장소에서 "비공개"를
    표시하는 유일한 수단이고, 이 테스트가 그 규약을 강제한다.

    이 정책이 없으면 새 공개 심볼이 재수출 없이 조용히 늘어나고, 호출자는
    하위 모듈 경로를 직접 파고들어 결국 파사드가 무의미해진다.
    """
    # 0개 순회는 통과가 아니라 설정 오류다. `pkgutil`이 빈 목록을 주면
    # 아래 루프가 한 번도 돌지 않은 채 초록이 된다.
    assert len(_MODULES) >= 5, f"하위 모듈을 못 찾았다: {[m.__name__ for m in _MODULES]}"

    for module in _MODULES:
        unexported = sorted(_public_toplevel_names(module) - set(t.__all__))
        assert unexported == [], f"{module.__name__}의 공개 심볼이 재수출되지 않았다: {unexported}"


def test_all에_중복도_비공개_이름도_없다() -> None:
    """중복은 병합 사고의 흔적이고, 밑줄 이름은 공개 정책의 위반이다."""
    assert len(t.__all__) == len(set(t.__all__)), "__all__에 중복이 있다"
    private = [name for name in t.__all__ if name.startswith("_")]
    assert private == [], f"비공개 이름이 공개되었다: {private}"


def test_설정_오류는_ProviderError로_잡히지_않는다() -> None:
    """생성자의 `ValueError`는 **호출 실패가 아니라 설정 오류**다 (설계 §4.2).

    `ProviderError`는 "프로바이더 **호출** 실패의 최상위"다. 설정 오류를 그
    아래 넣으면 exit 2("명령줄이 틀림")와 exit 66("파일 내용이 틀림")을 가른
    축이 무너진다.

    **이 단언이 없으면 그 구분은 독스트링에만 있는 주장이다.** 지금은
    `ProviderError`가 `Exception` 직속이라 자동으로 성립하지만, 누군가
    `class ConfigError(ValueError, ProviderError)`를 만드는 순간 조용히
    깨지고 기존 `pytest.raises(ValueError)` 테스트는 전부 통과한다.
    """
    bad_configs = (
        # `httpx.InvalidURL`을 감싼 자리. 감싸지 않으면 `InvalidURL`은
        # `ValueError`도 `ProviderError`도 아니라 이 계약이 통째로 깨진다.
        {"base_url": "http://[::1", "model": "m"},  # URL로 읽히지 않음
        {"base_url": "localhost:11434/v1", "model": "m"},  # 스킴 없음
        {"base_url": "https:///v1", "model": "m"},  # 호스트 없음
        {"base_url": "https://h/v1?k=1", "model": "m"},  # 쿼리 포함
        {"base_url": "https://h/v1#f", "model": "m"},  # 프래그먼트 포함
        {"base_url": "https://h/v1", "model": "m", "api_key": "키"},  # 비-ASCII 키
    )
    for config in bad_configs:
        with pytest.raises(ValueError) as exc:
            t.OpenAICompatibleProvider(**config)
        assert not isinstance(exc.value, t.ProviderError), config

    # timeout+client 동시 지정도 같은 축이다. 따로 쓰는 것은 이 인자만
    # httpx.Client를 필요로 해서다. 생성자가 던지면 provider가 클라이언트를
    # 넘겨받지 못하므로 여기서 직접 닫는다.
    with httpx.Client() as client, pytest.raises(ValueError) as exc:
        t.OpenAICompatibleProvider(base_url="https://h/v1", model="m", timeout=1.0, client=client)
    assert not isinstance(exc.value, t.ProviderError)


# ---------------------------------------------------------------------------
# `live` 마커 게이트 (설계 §9.2)
#
# **역할 분담이 이 절의 요점이다.**
#
# | 무엇을 지키나 | 어디서 | 왜 거기인가 |
# | --- | --- | --- |
# | ini 파일·`--strict-markers`·`-m` 개수 | `conftest` 훅 | **deselect될 수 없다** |
# | 훅이 실제로 거부하는가 | 여기 | 훅을 지워도 죽는 것이 있어야 한다 |
# | `markers` 등록 · live 파일의 `pytestmark` | 여기 | 훅이 보지 않는 것들 |
#
# 설정 검사를 테스트로 두면 **자기를 무력화하는 변이에 같이 쓸려 나간다** -
# `addopts`에 `-m live`를 덧붙이면 감시자 자신이 832개와 함께 deselect되고
# exit 0이 나온다(훅을 넣기 전 실측). 그래서 그 검사만 훅으로 옮겼다.
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class _FakeConfig:
    """훅이 읽는 것만 흉내 낸다 - `inipath`·`getoption`·`getini` 셋뿐이다.

    **가짜가 진짜와 다른 모양이면 훅 테스트가 통째로 무의미해진다.** 그래서
    `test_가짜_Config가_진짜와_같은_모양이다`가 진짜 `Config`로 세 접근자의
    타입을 확인한다. 이 저장소는 "가짜가 진짜가 하는 일을 아예 안 함"에
    이미 물린 적이 있다.
    """

    def __init__(self, *, inipath: pathlib.Path, strict: bool, addopts: list[str]) -> None:
        self.inipath = inipath
        self._strict = strict
        self._addopts = addopts

    def getoption(self, name: str) -> bool:
        assert name == "strict_markers", name
        return self._strict

    def getini(self, name: str) -> list[str]:
        assert name == "addopts", name
        return self._addopts


_GOOD = {
    "inipath": _REPO_ROOT / "pyproject.toml",
    "strict": True,
    "addopts": ["-ra", "--strict-markers", "-m", "not live"],
}


def test_가짜_Config가_진짜와_같은_모양이다(pytestconfig: pytest.Config) -> None:
    """`_FakeConfig`가 흉내 내는 세 접근자가 진짜에서도 같은 타입을 낸다."""
    assert isinstance(pytestconfig.inipath, pathlib.Path)
    assert isinstance(pytestconfig.getoption("strict_markers"), bool)
    assert isinstance(pytestconfig.getini("addopts"), list)


def test_게이트_훅이_정상_설정을_통과시킨다(pytestconfig: pytest.Config) -> None:
    """진짜 `Config`로 훅을 직접 불러 **오작동하지 않는 것**을 고정한다.

    초판이 `getini("addopts")`를 다시 `shlex.split`해 `"not live"`를 두
    토큰으로 쪼갰고, 그 결과 **정상 설정에서도 훅이 전체 실행을 막았다**(실측).
    이 테스트가 그 회귀를 잡는다.
    """
    pytest_configure(pytestconfig)
    pytest_configure(_FakeConfig(**_GOOD))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("변경", "기대"),
    [
        ({"inipath": _REPO_ROOT / "pytest.ini"}, "우리 pyproject가 아니다"),
        ({"strict": False}, "strict-markers"),
        ({"addopts": ["-m", "not live", "-m", "live"]}, "하나가 아니다"),
        ({"addopts": ["-ra", "--strict-markers"]}, "하나가 아니다"),
        ({"addopts": ["-m", "live"]}, "not live"),
    ],
)
def test_게이트_훅이_설정_이탈을_실제로_거부한다(변경: dict, 기대: str) -> None:
    """**훅을 지우거나 무르게 만들면 여기가 죽는다.**

    훅이 없으면 네 가지 우회가 전부 되살아나는데, 그 우회들은 하나같이
    **조용하다**(exit 0에 초록). 훅의 존재 자체를 지키는 것이 이 테스트다.
    """
    with pytest.raises(pytest.UsageError, match=기대):
        pytest_configure(_FakeConfig(**{**_GOOD, **변경}))  # type: ignore[arg-type]


def test_live_마커가_등록되고_기본_제외된다() -> None:
    """`markers`에 `live`가 정확히 하나 등록돼 있는가.

    **이것은 훅이 보지 않는다.** 훅은 `--strict-markers`가 켜졌는지만 보고,
    등록 자체가 빠지면 수집이 에러로 중단되므로 요란하게 죽는다. 그러나
    등록이 **둘**이 되는 것(오타 섞인 중복 등록)은 어느 쪽도 잡지 않아
    여기서 본다.
    """
    config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ini = config["tool"]["pytest"]["ini_options"]

    registered = [m for m in ini["markers"] if m.split(":")[0].strip() == "live"]
    assert len(registered) == 1, f"live 마커 등록이 정확히 하나여야 한다: {ini['markers']}"

    # `addopts`의 `-m`은 **여기서 보지 않는다.** 훅이 수집 전에 보고 거부하므로
    # 여기 같은 단언을 두면 절대 실패할 수 없는 죽은 코드가 된다 - 검사하지
    # 않으면서 검사하는 척하는 것은 없는 게이트보다 나쁘다.


def test_live_테스트_모듈이_마커를_단다() -> None:
    """`pytestmark`가 없으면 위의 `-m "not live"`가 아무것도 거르지 못한다.

    설정과 표식은 **둘 다 있어야** 동작하는 한 쌍이라, 한쪽만 검사하면
    나머지 한쪽을 지우는 변이가 그대로 통과한다.

    **파일명 하나에 못 박으면 안 된다.** 초판은 `test_translate_live.py`만
    봤고, 마커 없는 두 번째 live 파일을 넣으니 **0건이 죽은 채 그 파일이
    기본 수집에 들어와 그대로 실행됐다**(이 검사를 고치기 전 실측:
    `833 passed, 1 deselected` — 새 파일이 833번째로 조용히 늘었다).
    게이트가 막으려던 피해가 정확히 그 형태로 재현된다.

    **이것은 가정이 아니라 예정된 경로다** - WBS가 다음 순위로 못 박은
    WP7b가 `cuesift translate` CLI의 live 테스트를 추가한다.
    """
    live_files = sorted(_REPO_ROOT.glob("tests/test_*live*.py"))
    # **빈 목록을 실패로 못 박는다.** 없으면 파일명 규칙이 바뀌는 날 이
    # 테스트가 0개를 검사하며 초록이 된다 - 0개 수집은 통과가 아니다.
    assert live_files, "live 테스트 파일을 하나도 못 찾았다"

    for path in live_files:
        marks = [
            ast.unparse(node.value)
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.Assign)
            and any(tgt.id == "pytestmark" for tgt in node.targets if isinstance(tgt, ast.Name))
        ]
        assert marks == ["pytest.mark.live"], f"{path.name}의 pytestmark가 어긋났다: {marks}"
