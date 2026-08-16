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
from conftest import _check_addopts, pytest_configure


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
# 방어는 **세 겹**이고, 각 겹이 다른 것에 무력하다.
#
# | 겹 | 어디 | 무엇에 무력한가 |
# | --- | --- | --- |
# | ① 임포트 시점 | `conftest._check_on_import` | `config`가 없어 **어느 ini를 골랐는지** 모른다 |
# | ② 훅 | `conftest.pytest_configure` | 함수 개명으로 **배선이 끊긴다** |
# | ③ 테스트 | 이 절 | **deselect되면 안 돈다** |
#
# 설정 검사를 테스트에만 두면 자기를 무력화하는 변이에 같이 쓸려 나간다 -
# `addopts`에 `-m live`를 덧붙이면 감시자 자신이 832개와 함께 deselect되고
# exit 0이 나온다(훅 이전 실측). 그래서 ②로 옮겼고, ②의 배선마저 끊는
# 조합(M9) 때문에 ①이 생겼다.
#
# **①과 ②는 판정 술어를 공유하지 않는다.** 공유했더니 한 함수 무력화로 둘이
# 동시에 눈이 멀었다(실측 N9: exit 0, 0건 사망). `test_두_층이_판정_술어를_
# 공유하지_않는다`가 그 상태로 되돌아가는 것을 막는다.
#
# 이 절이 지키는 것은 ①②의 **배선과 로직**, 그리고 둘 다 보지 않는
# `markers` 등록과 live 파일들의 `pytestmark`다.
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
    """**훅을 무르게 만들면 여기가 죽는다.**

    다만 이것은 **로직만** 검사한다. 배선은 아래 테스트가 본다.
    """
    with pytest.raises(pytest.UsageError, match=기대):
        pytest_configure(_FakeConfig(**{**_GOOD, **변경}))  # type: ignore[arg-type]


def test_게이트_훅이_pytest에_실제로_등록돼_있다(pytestconfig: pytest.Config) -> None:
    """**로직이 옳아도 배선이 끊기면 훅은 아무것도 막지 못한다.**

    위 두 테스트는 함수를 **직접 부른다.** 그래서 함수 이름에서 `pytest_`
    접두사를 떼면 pluggy가 더 이상 훅으로 인식하지 않는데도 **한 건도 죽지
    않는다.** 실측(2026-08-16):

    | 변이 | 결과 |
    | --- | --- |
    | `pytest_configure` -> `_check_gate_config` | `839 passed`, exit 0. **0건 사망** |
    | 위 + `addopts`에 `-m live` | `1 skipped, 839 deselected`, exit 0. **우회 부활** |

    접두사를 **유지하는** 개명은 pluggy가 `PluginValidationError`(exit 3)로
    막지만, 접두사를 **버리면** 그 검증을 그냥 지나간다. 즉 지금까지 배선은
    pluggy의 명명 규칙이라는 **우연한** 보호에만 기대고 있었다.

    "게이트는 실패시켜 본 뒤에야 게이트다"는 훅의 로직뿐 아니라 **배선에도**
    걸린다.
    """
    impls = pytestconfig.pluginmanager.hook.pytest_configure.get_hookimpls()
    ours = [impl for impl in impls if impl.function is pytest_configure]
    assert len(ours) == 1, (
        "conftest의 게이트 훅이 pytest에 등록되지 않았다. "
        f"등록된 pytest_configure 구현 {len(impls)}개 중 우리 것은 {len(ours)}개다"
    )


def test_임포트_시점_검사가_인자_없이_최상위에서_호출된다() -> None:
    """**둘째 방어선의 배선도 검사한다.** 위 테스트와 대칭이다.

    `_check_on_import()`는 모듈 최상위에서 불려야 의미가 있다. 호출을 지우면
    **훅 개명 + `-m live` 덧붙임** 조합이 되살아나는데, 그 조합에서는 위 등록
    테스트마저 deselect되어 아무것도 죽지 않는다.

    호출문이 최상위에 있는지는 실행으로 확인할 수 없다(이미 임포트가 끝났고,
    통과했다는 사실이 호출 여부를 알려주지 않는다). 그래서 ast로 본다.

    ## 인자가 없어야 한다

    **이름만 보면 안 된다.** `_check_on_import`에 `pyproject` 인자를 붙인 것은
    로직을 시험하기 위해서인데(그전에는 본문 무력화에 0건이 죽었다), 그것이
    동시에 **배선 검사를 무력화하는 통로**를 열었다. 실측:

    | 변이 | 결과 |
    | --- | --- |
    | 최상위 호출에 decoy 경로 인자 | `848 passed`, exit 0. **0건 사망** |
    | 위 + 훅 개명 + `-m live` | `1 skipped, 848 deselected`, exit 0. **우회 부활** |

    호출은 그대로 있고 이름도 맞는데 **다른 파일을 검사한다.** 그래서 이름이
    아니라 **호출 노드**를 보고 인자가 비었는지까지 단언한다.

    **한 라운드의 수정이 다른 라운드의 보호를 깎은 사례다** - 검사를
    테스트 가능하게 만드는 변경이 그 검사의 배선을 무르게 했다.
    """
    source = (_REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    calls = [
        node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_check_on_import"
    ]
    assert calls, "conftest 최상위에 _check_on_import() 호출이 없다"
    # 인자를 주면 실제 pyproject가 아닌 파일을 검사하게 되어 ①이 조용히 죽는다.
    붙은_인자 = [ast.unparse(call) for call in calls if call.args or call.keywords]
    assert 붙은_인자 == [], f"최상위 호출에 인자가 붙었다: {붙은_인자}"


def _write_pyproject(tmp_path: pathlib.Path, addopts: str) -> pathlib.Path:
    """`_check_on_import`가 읽을 최소 pyproject를 만든다.

    `addopts`는 **TOML 조각 그대로** 받는다. 문자열 표기와 배열 표기를 둘 다
    시험해야 하는데, 파이썬 값을 받아 우리가 직렬화하면 그 차이가 사라진다.
    """
    path = tmp_path / "pyproject.toml"
    path.write_text(
        f"[tool.pytest.ini_options]\naddopts = {addopts}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("addopts", "기대"),
    [
        # 문자열 표기와 배열 표기 **둘 다** 정상으로 받아야 한다. pytest의
        # `addopts`는 `args` 타입이라 배열도 유효한데, 초판은 무조건
        # `shlex.split`해 배열에서 AttributeError로 터졌다(실측).
        ("'-ra --strict-markers -m \"not live\"'", None),
        ('["-ra", "--strict-markers", "-m", "not live"]', None),
        # 이탈 4종.
        ("'-ra --strict-markers -m \"not live\" -m live'", "하나가 아니다"),
        ("'-ra --strict-markers'", "하나가 아니다"),
        ("'-ra -m live'", "not live"),
        ('["-m", "not live", "-m", "live"]', "하나가 아니다"),
    ],
)
def test_임포트_시점_검사의_로직(tmp_path: pathlib.Path, addopts: str, 기대: str | None) -> None:
    """**본문에 테스트가 없으면 배선만 지킨 것이다.**

    앞 테스트는 "호출문이 최상위에 있는가"만 본다. 그래서 함수 **본문**을
    `return` 한 줄로 바꿔도 **0건이 죽었다**(실측: exit 0, 전량 통과).
    훅 쪽은 `_FakeConfig`가 5종 이탈을 검사하는데 이쪽은 0건이라 비대칭이었다.

    `pyproject` 인자가 그 비대칭을 푸는 통로다 - 실제 파일을 읽는 함수라
    주입 지점이 없으면 로직을 시험할 방법이 없다.
    """
    path = _write_pyproject(tmp_path, addopts)
    if 기대 is None:
        _check_addopts(path)
        return
    with pytest.raises(pytest.UsageError, match=기대):
        _check_addopts(path)


def test_두_층이_판정_술어를_공유하지_않는다() -> None:
    """**공유하면 한 함수 무력화로 두 층이 동시에 눈이 먼다.**

    실측(N9): 공유 헬퍼 `_markexpr_problems` 첫 줄에 `return []`을 넣으면
    테스트 3건이 죽지만(N8), 거기에 `-m live` 덧붙이기를 더하면 그 테스트들이
    deselect되어 **0건이 죽는다**(exit 0, 전량 deselect).
    세 겹이 하나의 술어에 매달려 있었다.

    그래서 두 층이 판정 조건을 **각자** 갖는다. 이 저장소가 `_REQUIRED`를
    `__all__`과 일부러 중복해 적는 것과 같은 이유다 - 검사와 검사 대상이
    같은 출처를 쓰면 그것은 검사가 아니다.

    ast로 본다. 두 함수 본문에 `-m` 판정이 **각각** 있어야 한다.
    """
    source = (_REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    함수별 = {
        node.name: ast.unparse(node)
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }
    for 이름 in ("_check_addopts", "pytest_configure"):
        assert 이름 in 함수별, f"{이름}이 conftest에 없다"
        본문 = 함수별[이름]
        assert "count('-m')" in 본문, f"{이름}에 -m 개수 판정이 없다(공유로 되돌아갔나)"
        assert "'not live'" in 본문, f"{이름}에 기본 제외식 판정이 없다"


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

    **파일명으로 찾으면 안 된다.** 판정 기준을 두 번 좁혀 왔고 두 번 다
    뚫렸다(전부 실측).

    | 판정 기준 | 뚫는 변이 | 결과 |
    | --- | --- | --- |
    | `test_translate_live.py` 하나 | 마커 없는 두 번째 live 파일 | `833 passed`. **0건 사망** |
    | `test_*live*.py` 파일명 | 이름을 `test_ollama_roundtrip.py`로 | `840 passed`. **0건 사망** |

    그래서 지금은 **소스에 엔드포인트 환경변수가 있는가**로 본다. 파일명보다
    넓지만 **완전하지 않다.**

    **사각지대: URL을 하드코딩하면 못 잡는다.** 실측 - `url =
    "http://localhost:11434/v1"`을 박아 넣은 마커 없는 live 파일은 이 검사를
    그냥 지나간다(**exit 0, 0건 사망** - 그 파일이 그대로 실행됐다).
    그리고 이것은 억지 사례가 아니다 -
    **로컬 Ollama는 키가 필요 없어 하드코딩이 오히려 자연스럽고, WP7b가 겨누는
    것이 정확히 Ollama다.**

    **기준을 네 번째로 좁히지 않는다.** "실 엔드포인트를 치는 파일"을 정적으로
    완전히 판정하는 방법은 없다 - 좁힐수록 다음 우회가 생길 뿐이다. 남은
    보호는 사람이고, 그래서 한계를 여기 적어 둔다. 적어 두지 않으면 다음
    사람이 이 검사를 완전한 것으로 믿는다.

    **WP7b가 이 자리를 지난다** - WBS가 다음 순위로 못 박은 그 태스크가
    `cuesift translate` CLI의 live 테스트를 추가한다.
    """
    # **이 파일 자신은 제외한다.** 아래 리터럴 때문에 스스로가 live 파일로
    # 잡혀 항상 실패한다. 대가는 "이 파일 안에 live 테스트를 넣으면 못
    # 잡는다"인데, 이 파일은 게이트 검사 전용이라 그럴 이유가 없다.
    myself = pathlib.Path(__file__).resolve()
    live_files = sorted(
        path
        for path in _REPO_ROOT.glob("tests/test_*.py")
        if path.resolve() != myself and "CUESIFT_LIVE" in path.read_text(encoding="utf-8")
    )
    # **빈 목록을 실패로 못 박는다.** 없으면 판정 기준이 바뀌는 날 이 테스트가
    # 0개를 검사하며 초록이 된다 - 0개 수집은 통과가 아니라 설정 오류다.
    assert live_files, "live 테스트 파일을 하나도 못 찾았다"

    for path in live_files:
        marks = [
            ast.unparse(node.value)
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.Assign)
            and any(tgt.id == "pytestmark" for tgt in node.targets if isinstance(tgt, ast.Name))
        ]
        assert marks == ["pytest.mark.live"], f"{path.name}의 pytestmark가 어긋났다: {marks}"
