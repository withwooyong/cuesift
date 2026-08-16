# WP7b 번역 영속화·CLI 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cuesift translate`가 자막을 번역해 파일로 내놓고, 중단된 작업을 캐시로 재개한다.

**Architecture:** `CachingProvider`가 `Provider` 프로토콜을 구현해 기존 프로바이더 앞에 끼어든다. 엔진 입장에서는 그냥 또 하나의 프로바이더이므로 **`translate/`를 한 줄도 고치지 않는다.** 재개는 별도 상태 파일이 아니라 캐시 히트로 이루어진다 — `iter_batches`가 연속 구간을 전제하는 이상 재개해도 전체 트랙을 넘겨야 하고, 그러면 호출을 줄이는 유일한 수단이 캐시다.

**Tech Stack:** Python 3.11+ · typer · pysubs2 · pyyaml · httpx · pytest · ruff

**Spec:** [`docs/superpowers/specs/2026-08-17-translate-cli-design.md`](../specs/2026-08-17-translate-cli-design.md)

## Global Constraints

이 절의 요구는 **모든 태스크에 암묵적으로 포함된다.**

- **Python 실행은 반드시 `.venv/Scripts/python.exe`.** 시스템 Python은 3.14라 다르다
- 모든 모듈 첫 줄에 `from __future__ import annotations`
- **독스트링과 주석은 한국어.** 근거 FR·§ 번호를 병기한다 (예: `FR-2.7`, `설계 §3.1`)
- **주석은 "왜 이 값인가"가 아니라 "이 값이 아니면 무엇이 깨지는가"를 적는다**
- ruff: `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`
- **의존성을 추가하지 않는다.** 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개(`pytest`·`pytest-cov`·`ruff`)
- 커밋 메시지는 **한국어**
- **푸시하지 않는다.** 사용자가 명시적으로 요청할 때만
- **로컬 게이트는 CI와 대상이 같아야 한다.** `ruff check .` / `ruff format --check .` — `src tests`로 좁히지 않는다
- 테스트 이름은 한국어를 쓴다 (기존 관례: `test_개수_불일치는_개별_폴백을_탄다`)
- **CI는 3.11·3.12, 로컬 venv는 3.14다.** 3.11에 없는 문법을 쓰지 않는다

**게이트 수치를 읽는 법**: `pytest`의 마지막 줄은 `N passed, M deselected`이고 **두 수를 같이 읽는다.** 착수 시점은 **868 passed, 1 deselected**다. 0개 수집은 통과가 아니라 설정 오류다.

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `src/cuesift/store/__init__.py` | 공개 API 재수출 | 1 |
| `src/cuesift/store/cache.py` | 캐시 키 계산 · 원자적 저장 · 대조 후 로드 | 1 |
| `src/cuesift/store/provider.py` | `CachingProvider` — `Provider` 프로토콜 구현 | 2 |
| `src/cuesift/translate/openai_compat.py` | `cache_identity` 속성 **추가만** | 2 |
| `src/cuesift/ingest/writer.py` | 번역된 세그먼트를 원본 자막 구조에 얹어 쓴다 | 3 |
| `src/cuesift/cli.py` | `translate()` 본문 · 설정 해결 · 종료 코드 | 4·5·6 |
| `docs/요구사항정의서.md` | §7.2 `ingest` 책임 한 문장 | 3 |
| `README.md` · `CHANGELOG.md` · `docs/WBS.md` · `HANDOFF.md` | 문서 | 7 |

**`store/`를 `cache.py`와 `provider.py`로 나눈 이유**: 전자는 파일시스템만 아는 순수 함수이고 후자는 `Provider` 계약을 안다. 합치면 캐시 형식을 테스트하는 데 프로바이더 더블이 필요해진다.

---

### Task 1: 캐시 저장소

**Files:**

- Create: `src/cuesift/store/__init__.py`
- Create: `src/cuesift/store/cache.py`
- Test: `tests/test_store_cache.py`

**Interfaces:**

- Consumes: `cuesift.translate.provider`의 `ChatMessage`·`Completion`·`TokenUsage` (기존)
- Produces:
  - `CacheRequest(identity: str, temperature: float, max_tokens: int | None, messages: tuple[ChatMessage, ...])` — frozen dataclass. 프로퍼티 `key: str`, `messages_sha: str`
  - `load(cache_dir: Path, request: CacheRequest) -> Completion | None`
  - `store(cache_dir: Path, request: CacheRequest, completion: Completion) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_store_cache.py`를 만든다.

```python
"""캐시 저장소 검증 (NFR-3 · 설계 §3).

**이 파일은 네트워크를 타지 않는다.** 캐시는 파일시스템만 안다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuesift.store.cache import CacheRequest, load, store
from cuesift.translate.provider import ChatMessage, Completion, TokenUsage


def _request(*, identity: str = "openai-compatible|http://h/v1|m1", text: str = "안녕") -> CacheRequest:
    return CacheRequest(
        identity=identity,
        temperature=0.0,
        max_tokens=None,
        messages=(ChatMessage(role="system", content="지시"), ChatMessage(role="user", content=text)),
    )


def _completion(text: str = '{"translations": []}') -> Completion:
    return Completion(text=text, usage=TokenUsage(prompt_tokens=7, completion_tokens=11, calls=1))


def test_저장한_것을_그대로_읽는다(tmp_path: Path) -> None:
    request = _request()
    store(tmp_path, request, _completion())

    got = load(tmp_path, request)

    assert got is not None
    assert got.text == '{"translations": []}'
    assert got.usage == TokenUsage(prompt_tokens=7, completion_tokens=11, calls=1)


def test_없으면_None이다(tmp_path: Path) -> None:
    assert load(tmp_path, _request()) is None


def test_identity가_다르면_키가_다르다() -> None:
    # 이것이 없으면 qwen2.5:3b로 채운 캐시가 gpt-4o 실행에서 히트한다.
    a = _request(identity="openai-compatible|http://h/v1|qwen2.5:3b")
    b = _request(identity="openai-compatible|http://h/v1|gpt-4o")

    assert a.key != b.key


def test_메시지가_다르면_키가_다르다() -> None:
    assert _request(text="안녕").key != _request(text="잘가").key


def test_키는_재실행에서_같다() -> None:
    # 재개가 성립하는 유일한 근거다. 키가 흔들리면 캐시가 영원히 미스다.
    assert _request().key == _request().key


def test_역할과_내용의_경계가_모호하지_않다() -> None:
    # "system"+"지시" 와 "system지시"+"" 가 같은 키를 내면 안 된다.
    a = CacheRequest(
        identity="i", temperature=0.0, max_tokens=None,
        messages=(ChatMessage(role="system", content="지시"),),
    )
    b = CacheRequest(
        identity="i", temperature=0.0, max_tokens=None,
        messages=(ChatMessage(role="system", content=""), ChatMessage(role="user", content="지시")),
    )

    assert a.key != b.key


def test_손상된_파일은_예외가_아니라_미스다(tmp_path: Path) -> None:
    # 손상된 캐시 파일 하나가 실행 전체를 죽이면 안 된다 (설계 §3.3).
    request = _request()
    store(tmp_path, request, _completion())
    (tmp_path / f"{request.key}.json").write_text("{깨진 JSON", encoding="utf-8")

    assert load(tmp_path, request) is None


def test_identity가_어긋난_파일은_미스다(tmp_path: Path) -> None:
    # 해시 충돌·수동 편집 방어. 파일명만 믿으면 손상된 캐시가 번역문으로 둔갑한다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["identity"] = "다른-모델"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_messages_sha가_어긋난_파일은_미스다(tmp_path: Path) -> None:
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["messages_sha"] = "0" * 64
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_음수_토큰은_미스다(tmp_path: Path) -> None:
    # TokenUsage가 ValueError를 던지는데, 그것이 load 밖으로 새면
    # 손상 파일 하나가 실행을 죽인다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["usage"]["prompt_tokens"] = -1
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_디렉터리가_없어도_저장이_만든다(tmp_path: Path) -> None:
    target = tmp_path / "없는" / "깊은" / "경로"
    request = _request()

    store(target, request, _completion())

    assert load(target, request) is not None


def test_임시_파일을_남기지_않는다(tmp_path: Path) -> None:
    # os.replace가 안 돌면 .tmp가 남는다. 남으면 캐시 디렉터리가 쓰레기로 찬다.
    store(tmp_path, _request(), _completion())

    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_저장_실패는_예외를_내지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 디스크가 차거나 읽기 전용이어도 번역 자체가 실패할 이유는 없다.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("디스크 없음")

    monkeypatch.setattr("cuesift.store.cache.os.replace", boom)

    with pytest.raises(OSError):
        store(tmp_path, _request(), _completion())
```

**마지막 테스트가 `pytest.raises`인 것에 주의한다.** `store`는 `OSError`를 그대로 던지고, **삼키는 것은 호출자(`CachingProvider`)의 몫**이다. 저장소가 삼키면 `CachingProvider`가 경고를 낼 재료를 잃는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.store'`

- [ ] **Step 3: `store/cache.py`를 쓴다**

```python
"""LLM 응답 캐시 (요구사항정의서 NFR-3 재현성 · §7.1 `store/`).

**캐시는 최적화이지 정확성의 근거가 아니다.** 그래서 읽기가 조금이라도
미심쩍으면 예외가 아니라 **미스**로 떨어뜨린다 - 손상된 파일 하나가 실행
전체를 죽이면 안 되고, 못 믿을 때는 다시 부르면 된다 (설계 §3.3).

**키를 파일명에 넣고 재료를 파일 안에 또 쓴다.** 파일명만 믿으면 손상된
캐시가 조용히 번역문으로 둔갑한다 - 이 저장소가 1급으로 금지한
"검사하지 않고 통과하는 게이트"가 정확히 그 형태다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cuesift.translate.provider import ChatMessage, Completion, TokenUsage

# 키 재료를 잇는 구분자. **실제 제어문자 U+001F(UNIT SEPARATOR)여야 한다.**
# 자막이나 프롬프트에 나타날 수 있는 문자("|"·":")를 쓰면 재료의 경계가
# 모호해진다 - model="a|b"와 (model="a", base_url="b")가 같은 키를 만든다.
_SEP = "\x1f"


@dataclass(frozen=True, slots=True)
class CacheRequest:
    """캐시 조회·저장의 재료 한 묶음 (설계 §3.1).

    **키와 대조 재료가 같은 곳에서 나오는 것이 요점이다.** 둘을 따로
    계산하면 한쪽만 고쳐졌을 때 저장한 것을 자기가 못 읽는다.
    """

    identity: str
    temperature: float
    max_tokens: int | None
    messages: tuple[ChatMessage, ...]

    @property
    def messages_sha(self) -> str:
        return _sha256(_messages_material(self.messages))

    @property
    def key(self) -> str:
        """설계 §3.1의 `(원문, 맥락 원문, 용어집, 모델, 설정)`을 전부 덮는다.

        메시지를 **재조립하지 않고 그대로** 넣는 것이 핵심이다. 재조립하면
        프롬프트 조립 규칙이 바뀔 때 키가 따라가지 못한다 - 실제로
        2026-08-17에 시스템 프롬프트가 바뀌었고(정수 id 계약), 키를 손으로
        관리했다면 **바뀐 프롬프트가 옛 캐시에 히트했을 것이다.**

        `float(...)`로 정규화하는 이유는 `0`(int)과 `0.0`(float)이 같은
        온도인데 `repr`이 다르기 때문이다. 정규화가 없으면 호출부의 타입
        차이 하나로 캐시가 전량 미스가 된다.
        """
        material = _SEP.join(
            (
                self.identity,
                repr(float(self.temperature)),
                "none" if self.max_tokens is None else str(self.max_tokens),
                self.messages_sha,
            )
        )
        return _sha256(material)


def load(cache_dir: Path, request: CacheRequest) -> Completion | None:
    """캐시에서 읽는다. 조금이라도 미심쩍으면 `None`이다.

    **잡는 예외가 넓은 것이 의도다.** `json.JSONDecodeError`와
    `UnicodeDecodeError`는 둘 다 `ValueError`의 하위이고, `TokenUsage`의
    음수 검사도 `ValueError`를 낸다. 좁히면 손상된 파일의 어느 한 형태가
    호출부로 새어 나가는데, 그 자리는 번역 루프 한가운데다.
    """
    path = cache_dir / f"{request.key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not _matches(raw, request):
        return None

    try:
        usage = raw["usage"]
        return Completion(
            text=raw["text"],
            usage=TokenUsage(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                calls=usage["calls"],
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def store(cache_dir: Path, request: CacheRequest, completion: Completion) -> None:
    """캐시에 쓴다. **`OSError`를 삼키지 않는다** - 호출자가 경고를 낸다.

    임시 파일에 쓰고 `os.replace`로 옮기는 이유는 중간에 죽어도 반쪽짜리
    JSON이 남지 않게 하기 위해서다. `os.replace`는 Windows에서도 원자적이다.

    임시 파일명에 pid를 넣는 것은 같은 키를 두 프로세스가 동시에 쓸 때
    서로의 임시 파일을 덮지 않게 하기 위해서다. 최종 파일에 대한 경쟁은
    마지막 쓰기가 이기고, 온도 0.0에서는 내용이 같으므로 무해하다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "identity": request.identity,
        "temperature": float(request.temperature),
        "max_tokens": request.max_tokens,
        "messages_sha": request.messages_sha,
        "text": completion.text,
        "usage": {
            "prompt_tokens": completion.usage.prompt_tokens,
            "completion_tokens": completion.usage.completion_tokens,
            "calls": completion.usage.calls,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    tmp = cache_dir / f"{request.key}.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache_dir / f"{request.key}.json")


def _matches(raw: object, request: CacheRequest) -> bool:
    """파일 안의 재료가 현재 요청과 같은지 본다.

    **키가 이미 이 넷을 덮으므로 정상 경로에서는 항상 참이다.** 이 검사가
    잡는 것은 해시 충돌·파일 손상·수동 편집이다. 그래서 어긋남을 예외가
    아니라 미스로 다룬다 - 캐시를 못 믿을 뿐 실행이 틀린 것은 아니다.
    """
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("identity") == request.identity
        and raw.get("temperature") == float(request.temperature)
        and raw.get("max_tokens") == request.max_tokens
        and raw.get("messages_sha") == request.messages_sha
    )


def _messages_material(messages: Sequence[ChatMessage]) -> str:
    """역할과 내용 사이에도 구분자를 넣는다.

    `f"{role}:{content}"`로 이으면 `("system", "지시")` 하나와
    `("system지시", "")`가 같은 문자열을 낸다. 실제로는 안 나오는 조합이지만,
    **키의 단사성은 입력 분포가 아니라 구조로 보장해야 한다.**
    """
    return _SEP.join(f"{m.role}{_SEP}{m.content}" for m in messages)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

`src/cuesift/store/__init__.py`:

```python
"""캐시와 재개 상태 (요구사항정의서 §7.1 `store/` · NFR-3 · FR-2.7).

**재개는 별도 상태 파일이 아니라 캐시다** (설계 §4.2). `iter_batches`가
연속 구간을 전제하므로 재개해도 전체 트랙을 넘겨야 하고, 그러면 호출을
줄이는 유일한 수단이 캐시다.
"""

from __future__ import annotations

from cuesift.store.cache import CacheRequest, load, store

__all__ = ["CacheRequest", "load", "store"]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -v`

Expected: PASS — 13개

- [ ] **Step 5: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest -q
git add src/cuesift/store tests/test_store_cache.py
git commit -m "기능: LLM 응답 캐시 저장소 (NFR-3)"
```

`pytest`의 마지막 줄에서 **passed와 deselected를 같이 읽는다.** 착수 시 868 + 1.

---

### Task 2: `CachingProvider`

**Files:**

- Create: `src/cuesift/store/provider.py`
- Modify: `src/cuesift/store/__init__.py`
- Modify: `src/cuesift/translate/openai_compat.py` (`cache_identity` 속성 추가)
- Modify: `src/cuesift/translate/__init__.py` (재수출 없음 — `cache_identity`는 속성이라 `__all__` 대상이 아니다)
- Test: `tests/test_store_provider.py`

**Interfaces:**

- Consumes: Task 1의 `CacheRequest`·`load`·`store`
- Produces:
  - `CachingProvider(inner: Provider, *, identity: str, cache_dir: Path, warn: Callable[[str], None] = ...)`
  - 속성 `name = "cached"`, `hits: int`, `misses: int`
  - `OpenAICompatibleProvider.cache_identity -> str` — `f"{name}|{base_url}|{model}"`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_store_provider.py`:

```python
"""`CachingProvider` 검증 (NFR-3 · FR-2.7 · 설계 §3.2·§3.5).

**재개 게이트가 여기 있다.** "캐시가 있다"가 아니라 **"2회차 호출 수가
1회차보다 정확히 N만큼 적다"** 를 단언한다 - 숫자를 세지 않으면 캐시를
한 번도 안 읽고도 통과한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuesift.store.provider import CachingProvider
from cuesift.translate.openai_compat import OpenAICompatibleProvider
from cuesift.translate.provider import (
    ChatMessage,
    FatalProviderError,
    RetryableProviderError,
)
from tests.fakes.provider import ScriptedProvider

_MESSAGES = (ChatMessage(role="system", content="지시"), ChatMessage(role="user", content="안녕"))


def _cached(inner: object, tmp_path: Path, *, identity: str = "i|u|m") -> CachingProvider:
    return CachingProvider(inner, identity=identity, cache_dir=tmp_path)


def test_히트하면_안쪽을_부르지_않는다(tmp_path: Path) -> None:
    inner = ScriptedProvider(["응답1"])
    provider = _cached(inner, tmp_path)

    first = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert len(inner.calls) == 1  # 두 번 불렀는데 안쪽은 한 번
    assert first.text == second.text == "응답1"
    assert (provider.hits, provider.misses) == (1, 1)


def test_새_프로세스가_남긴_캐시를_읽는다(tmp_path: Path) -> None:
    # 재개의 실체다. 1회차 객체를 버리고 2회차 객체가 읽는다.
    first_run = _cached(ScriptedProvider(["응답1"]), tmp_path)
    first_run.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    inner = ScriptedProvider([])  # 대본이 비었다 - 부르면 AssertionError
    second_run = _cached(inner, tmp_path)
    got = second_run.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "응답1"
    assert inner.calls == []
    assert (second_run.hits, second_run.misses) == (1, 0)


def test_identity가_다르면_히트하지_않는다(tmp_path: Path) -> None:
    # 이것이 깨지면 qwen2.5:3b 캐시가 gpt-4o 실행에서 히트한다.
    _cached(ScriptedProvider(["3b응답"]), tmp_path, identity="i|u|qwen2.5:3b").complete(
        _MESSAGES, temperature=0.0, max_tokens=None
    )

    inner = ScriptedProvider(["4o응답"])
    other = _cached(inner, tmp_path, identity="i|u|gpt-4o")
    got = other.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "4o응답"
    assert len(inner.calls) == 1


def test_온도가_다르면_히트하지_않는다(tmp_path: Path) -> None:
    provider = _cached(ScriptedProvider(["a", "b"]), tmp_path)

    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    provider.complete(_MESSAGES, temperature=0.7, max_tokens=None)

    assert (provider.hits, provider.misses) == (0, 2)


def test_재시도_가능_실패는_캐시하지_않는다(tmp_path: Path) -> None:
    # 캐시하면 일시적 429가 영구 재생된다.
    inner = ScriptedProvider([RetryableProviderError("429"), "성공"])
    provider = _cached(inner, tmp_path)

    with pytest.raises(RetryableProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "성공"
    assert len(inner.calls) == 2


def test_치명적_실패는_캐시하지_않는다(tmp_path: Path) -> None:
    # 키를 고치고 재실행하면 즉시 통해야 한다.
    inner = ScriptedProvider([FatalProviderError("401"), "성공"])
    provider = _cached(inner, tmp_path)

    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "성공"
    assert len(inner.calls) == 2


def test_형식을_어긴_응답도_캐시한다(tmp_path: Path) -> None:
    # NFR-3은 "같은 입력에 같은 결과"이지 "더 나은 결과"가 아니다 (설계 §3.6).
    # 이것이 함정인 것을 알고 채택했다 - 탈출구는 --no-cache다.
    inner = ScriptedProvider(["죄송합니다, 번역할 수 없습니다."])
    provider = _cached(inner, tmp_path)

    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert second.text == "죄송합니다, 번역할 수 없습니다."
    assert len(inner.calls) == 1


def test_히트해도_저장된_사용량을_그대로_낸다(tmp_path: Path) -> None:
    # 0으로 만들면 calls가 0이 되어 "호출당 토큰"이 계산 불가다 (설계 §3.5.1).
    inner = ScriptedProvider(["응답1"])
    provider = _cached(inner, tmp_path)

    first = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert second.usage == first.usage
    assert second.usage.calls == 1


def test_빈_identity를_거부한다(tmp_path: Path) -> None:
    # 빠뜨리면 캐시가 모델을 구분하지 못한다. 조용히 통과하면 안 된다.
    with pytest.raises(ValueError, match="identity"):
        CachingProvider(ScriptedProvider([]), identity="   ", cache_dir=tmp_path)


def test_저장_실패는_번역을_죽이지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("디스크 없음")

    monkeypatch.setattr("cuesift.store.provider.store", boom)
    warnings: list[str] = []
    provider = CachingProvider(
        ScriptedProvider(["응답1", "응답2"]),
        identity="i|u|m",
        cache_dir=tmp_path,
        warn=warnings.append,
    )

    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == "응답1"
    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == "응답2"
    # 경고는 한 번만. 수백 번 반복하면 진짜 출력이 묻힌다.
    assert len(warnings) == 1


def test_손상된_캐시는_다시_부른다(tmp_path: Path) -> None:
    inner = ScriptedProvider(["응답1", "응답2"])
    provider = _cached(inner, tmp_path)
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    for path in tmp_path.glob("*.json"):
        path.write_text("{깨짐", encoding="utf-8")

    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "응답2"
    assert len(inner.calls) == 2


def test_시그니처가_프로토콜과_같다() -> None:
    # Provider는 runtime_checkable이 아니고 CI에 타입 검사기도 없다.
    # 이 단언이 이탈을 잡는 유일한 수단이다 (tests/fakes/provider.py와 같은 이유).
    import inspect

    from cuesift.translate.provider import Provider

    assert inspect.signature(CachingProvider.complete) == inspect.signature(Provider.complete)


def test_cache_identity가_모델을_구분한다() -> None:
    a = OpenAICompatibleProvider(base_url="http://h/v1", model="qwen2.5:3b")
    b = OpenAICompatibleProvider(base_url="http://h/v1", model="gpt-4o")
    c = OpenAICompatibleProvider(base_url="http://other/v1", model="qwen2.5:3b")

    assert a.cache_identity != b.cache_identity
    assert a.cache_identity != c.cache_identity
    assert "qwen2.5:3b" in a.cache_identity


def test_캐시_파일에_사람이_읽을_재료가_남는다(tmp_path: Path) -> None:
    provider = _cached(ScriptedProvider(["응답1"]), tmp_path, identity="i|u|qwen2.5:3b")
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    raw = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))

    assert raw["identity"] == "i|u|qwen2.5:3b"
    assert raw["created_at"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_provider.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.store.provider'`

- [ ] **Step 3: `store/provider.py`를 쓴다**

```python
"""캐시를 끼운 프로바이더 (NFR-3 · FR-2.7 · 설계 §2.2).

**`Provider` 프로토콜을 구현하므로 엔진 입장에서는 그냥 또 하나의
프로바이더다.** 그래서 `translate/`를 한 줄도 고치지 않고 재개가 붙는다 -
재시도·백오프·배치 폴백·예외 분류가 전부 그대로 유효하고, 개별 폴백
호출도 각각 캐시된다.

**예외를 캐시하지 않는 것은 구조적으로 보장된다.** 안쪽 `complete()`가
던지면 아래 저장 코드에 도달하지 못한다. 조건문으로 거르는 것이 아니라서
새 예외 종류가 생겨도 규칙이 깨지지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from cuesift.store.cache import CacheRequest, load, store
from cuesift.translate.provider import ChatMessage, Completion, Provider


def _ignore(_message: str) -> None:
    """기본 경고 싱크. 라이브러리 사용자가 stderr를 강요받지 않게 한다."""


class CachingProvider:
    """`inner` 앞에 캐시를 끼운다."""

    name = "cached"

    def __init__(
        self,
        inner: Provider,
        *,
        identity: str,
        cache_dir: Path,
        warn: Callable[[str], None] = _ignore,
    ) -> None:
        """`identity`는 **키워드 필수**다.

        `Provider` 프로토콜에 넣지 않은 이유는 `Protocol`이 런타임 검사를
        하지 않기 때문이다 - 서드파티 구현이 빠뜨려도 조용히 통과하므로
        강제한 것이 아니다. 필수 키워드 인자로 두면 **빠뜨릴 때
        `TypeError`로 즉시 죽는다.** 검사되는 계약이 검사되지 않는 선언보다 낫다.

        빈 문자열을 거부하는 이유는 `Provider.name`이 클래스 상수라
        (`"openai-compatible"`) 모델을 구분하지 못하기 때문이다. identity가
        비면 **`qwen2.5:3b`로 채운 캐시가 `gpt-4o` 실행에서 히트한다.**
        """
        if not identity.strip():
            raise ValueError("identity가 비었다. 캐시 키가 모델을 구분하지 못한다")
        self._inner = inner
        self._identity = identity
        self._cache_dir = cache_dir
        self._warn = warn
        self._warned = False
        self.hits = 0
        self.misses = 0

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """캐시를 보고, 없으면 안쪽을 부르고 저장한다."""
        request = CacheRequest(
            identity=self._identity,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=tuple(messages),
        )
        cached = load(self._cache_dir, request)
        if cached is not None:
            self.hits += 1
            # **저장된 usage를 그대로 낸다** (설계 §3.5.1). 0으로 만들면
            # calls가 0이 되어 "호출당 토큰"을 영영 계산할 수 없다.
            # 실제 네트워크 호출 수는 self.misses가 따로 센다.
            return cached

        self.misses += 1
        completion = self._inner.complete(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        try:
            store(self._cache_dir, request, completion)
        except OSError as exc:
            # 디스크가 차거나 읽기 전용이어도 번역이 실패할 이유는 없다.
            # **다만 조용히 삼키지는 않는다** - 사용자는 재개가 되는 줄 안다.
            # 한 번만 내는 것은 수백 번 반복하면 진짜 출력이 묻히기 때문이다.
            self._warn_once(f"캐시를 쓰지 못했다(재개가 동작하지 않는다): {exc}")
        return completion

    def _warn_once(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        self._warn(message)
```

`src/cuesift/store/__init__.py`에 추가:

```python
from cuesift.store.cache import CacheRequest, load, store
from cuesift.store.provider import CachingProvider

__all__ = ["CacheRequest", "CachingProvider", "load", "store"]
```

- [ ] **Step 4: `OpenAICompatibleProvider.cache_identity`를 추가한다**

`src/cuesift/translate/openai_compat.py`의 `__init__` 바로 뒤에 넣는다.

```python
    @property
    def cache_identity(self) -> str:
        """캐시 키에 넣을 신원 (NFR-3 · WP7b 설계 §3.2).

        **`name`만으로는 안 된다.** 그것은 클래스 상수
        (`"openai-compatible"`)라 모델을 구분하지 못하고, 그대로 키에 넣으면
        `qwen2.5:3b`로 채운 캐시가 `gpt-4o` 실행에서 히트한다 - 캐시가
        조용히 다른 모델의 응답을 돌려주고 NFR-3이 정면으로 깨진다.

        `base_url`이 들어가는 이유는 같은 모델명이 서버마다 다른 것을
        가리킬 수 있기 때문이다(`llama-3.1-70b`를 서로 다른 양자화로 서빙하는
        두 엔드포인트). `api_key`는 **넣지 않는다** - 키를 교체해도 같은
        모델이면 결과가 같고, 넣으면 캐시 파일에 키의 존재가 새어 나간다.
        """
        return f"{self.name}|{self._base_url}|{self._model}"
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_provider.py -v`

Expected: PASS — 14개

- [ ] **Step 6: 게이트가 실제로 실패하는지 확인한다**

**이 저장소의 규율이다 — 게이트를 만들면 반드시 실패시켜 봐야 한다.**
캐시를 무력화한 변이를 손으로 넣고 재개 테스트가 **죽는 것**을 본다.

```bash
# `load`가 항상 None을 내도록 임시 변이
```

`store/provider.py`의 `cached = load(...)`를 `cached = None`으로 바꾸고:

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_provider.py -v`

Expected: **FAIL 최소 4개** — `test_히트하면_안쪽을_부르지_않는다`,
`test_새_프로세스가_남긴_캐시를_읽는다`, `test_형식을_어긴_응답도_캐시한다`,
`test_히트해도_저장된_사용량을_그대로_낸다`

**죽는 개수를 기록한다.** 죽지 않으면 그 테스트는 게이트가 아니라 장식이다.
확인 후 변이를 되돌린다.

- [ ] **Step 7: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest -q
git add src/cuesift/store src/cuesift/translate/openai_compat.py tests/test_store_provider.py
git commit -m "기능: 캐시를 끼운 프로바이더 - 재개의 실체 (FR-2.7, NFR-3)"
```

---

### Task 3: 자막 쓰기

**Files:**

- Create: `src/cuesift/ingest/writer.py`
- Modify: `src/cuesift/ingest/__init__.py`
- Modify: `docs/요구사항정의서.md` (§7.2 `ingest` 행)
- Test: `tests/test_ingest_writer.py`

**Interfaces:**

- Consumes: `IngestResult`(기존, `subs`·`event_index`·`format` 보유), `Segment`(기존, `target_text` 보유)
- Produces: `write_subtitle(result: IngestResult, segments: Sequence[Segment], out_path: Path) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_ingest_writer.py`:

```python
"""번역된 자막 쓰기 검증 (FR-7.1 · 설계 §5.2).

**라운드트립이 이 파일의 주제다.** 읽고 → 갈아끼우고 → 쓰고 → 다시 읽어
대조한다. 한 방향만 보면 두 방향이 어긋나도 드러나지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuesift.ingest import load_subtitle, write_subtitle

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


def _translated(result: object, prefix: str = "EN:") -> list:
    """모든 세그먼트에 번역문을 채운 사본을 만든다."""
    for segment in result.segments:
        segment.target_text = f"{prefix}{segment.source_text}"
    return result.segments


def test_번역문이_실제로_쓰인다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")
    out = tmp_path / "out.srt"

    write_subtitle(result, _translated(result), out)

    reread = load_subtitle(out)
    assert [s.source_text for s in reread.segments] == [
        f"EN:{s.source_text}" for s in load_subtitle(_FIXTURES / "minimal.srt").segments
    ]


def test_타임코드가_보존된다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")
    before = [(s.start_ms, s.end_ms) for s in result.segments]
    out = tmp_path / "out.srt"

    write_subtitle(result, _translated(result), out)

    assert [(s.start_ms, s.end_ms) for s in load_subtitle(out).segments] == before


def test_여러_줄이_보존된다(tmp_path: Path) -> None:
    # plaintext setter가 \n을 \N으로 바꾸는 데 기대고 있다.
    result = load_subtitle(_FIXTURES / "multiline.vtt")
    for segment in result.segments:
        segment.target_text = "첫\n둘\n셋"
    out = tmp_path / "out.vtt"

    write_subtitle(result, result.segments, out)

    assert load_subtitle(out).segments[0].source_text == "첫\n둘\n셋"


def test_선행_태그_블록이_되붙는다(tmp_path: Path) -> None:
    # {\an8}은 화면 위쪽 자막이라는 뜻이다. 잃으면 자막이 아래로 내려온다.
    # pysubs2의 plaintext setter는 태그를 전부 지우므로 보정이 필요하다 [실측].
    import pysubs2

    result = load_subtitle(_FIXTURES / "tags.ass")
    out = tmp_path / "out.ass"

    write_subtitle(result, _translated(result), out)

    written = pysubs2.load(out, encoding="utf-8")
    dialogues = [e for e in written.events if e.type == "Dialogue"]
    assert dialogues[0].text.startswith("{\\an8}")


def test_주석_이벤트는_건드리지_않는다(tmp_path: Path) -> None:
    # `_keep_displayed`가 걸러낸 이벤트다. 위치로 짝지으면 전부 밀린다.
    import pysubs2

    result = load_subtitle(_FIXTURES / "tags.ass")
    out = tmp_path / "out.ass"

    write_subtitle(result, _translated(result), out)

    written = pysubs2.load(out, encoding="utf-8")
    comments = [e for e in written.events if e.type == "Comment"]
    assert comments and all("EN:" not in e.text for e in comments)


def test_실패_세그먼트는_원문을_남긴다(tmp_path: Path) -> None:
    # 빈 문자열로 두면 화면에서 사라져 발견이 더 어렵다 (설계 §5.3).
    result = load_subtitle(_FIXTURES / "minimal.srt")
    original = result.segments[0].source_text
    for segment in result.segments[1:]:
        segment.target_text = f"EN:{segment.source_text}"
    out = tmp_path / "out.srt"

    write_subtitle(result, result.segments, out)

    assert load_subtitle(out).segments[0].source_text == original


def test_원본_결과를_변형하지_않는다(tmp_path: Path) -> None:
    # deepcopy가 없으면 --to en,ja에서 두 번째 언어가 첫 번째 위에 덮인다.
    result = load_subtitle(_FIXTURES / "minimal.srt")
    before = [e.text for e in result.subs.events]

    write_subtitle(result, _translated(result), tmp_path / "en.srt")

    assert [e.text for e in result.subs.events] == before


def test_두_언어를_연달아_써도_섞이지_않는다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")

    write_subtitle(result, _translated(result, "EN:"), tmp_path / "a.srt")
    write_subtitle(result, _translated(result, "JA:"), tmp_path / "b.srt")

    assert all(s.source_text.startswith("EN:") for s in load_subtitle(tmp_path / "a.srt").segments)
    assert all(s.source_text.startswith("JA:") for s in load_subtitle(tmp_path / "b.srt").segments)


def test_없는_디렉터리를_만든다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")
    out = tmp_path / "없는" / "깊은" / "out.srt"

    write_subtitle(result, _translated(result), out)

    assert out.exists()


@pytest.mark.parametrize("fixture", ["minimal.srt", "multiline.vtt", "basic.ssa", "crlf_bom.srt"])
def test_픽스처_라운드트립(fixture: str, tmp_path: Path) -> None:
    # 큐 개수가 유지되는지가 최소 계약이다. 하나라도 사라지면 타임코드가 밀린다.
    result = load_subtitle(_FIXTURES / fixture)
    out = tmp_path / f"out{Path(fixture).suffix}"

    write_subtitle(result, _translated(result), out)

    assert len(load_subtitle(out).segments) == len(result.segments)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingest_writer.py -v`

Expected: FAIL — `ImportError: cannot import name 'write_subtitle'`

- [ ] **Step 3: `ingest/writer.py`를 쓴다**

```python
"""번역된 세그먼트를 자막 파일로 쓴다 (FR-7.1 · 설계 §5.2).

**`ingest`가 pysubs2를 아는 유일한 곳이라는 §7.2의 경계를 지킨다.**
`report`는 순수 모듈이라 이것을 담을 수 없고, `output/`을 새로 만들면
pysubs2를 아는 곳이 둘로 늘어난다.

읽기와 쓰기가 같은 디렉터리에 있는 실질 이득도 있다 - 라운드트립이 깨지면
한 곳에서 드러난다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from pathlib import Path

from cuesift.ingest.loader import IngestResult
from cuesift.segment.models import Segment

# 텍스트 맨 앞의 오버라이드 블록들. `{\an8}{\i1}` 같은 연속도 한 번에 잡는다.
#
# **이 보정이 없으면 `\an8`(화면 위쪽)이 사라져 자막이 아래로 내려온다.**
# pysubs2의 `plaintext` setter가 태그를 전부 지우기 때문이다 [실측 2026-08-17].
# 중간·후행 태그는 되살릴 수 없다 - 원문 "기울임" 3글자에 걸린 강조가
# 번역문 "italic"의 어디에 걸리는지 결정할 근거가 없다 (설계 §5.2.1).
_LEADING_OVERRIDES = re.compile(r"^(?:\{[^}]*\})*")


def write_subtitle(
    result: IngestResult,
    segments: Sequence[Segment],
    out_path: Path,
) -> None:
    """`segments`의 `target_text`를 원본 자막 구조에 얹어 `out_path`에 쓴다.

    **`target_text`가 `None`인 세그먼트는 원문을 그대로 둔다** (FR-2.6 부분
    실패). 빈 문자열로 두면 화면에서 자막이 사라지는데, 그것은 "번역이
    안 됐다"보다 발견하기 어렵다 (설계 §5.3).

    **`result.subs`를 `deepcopy`한다.** 직접 고치면 `--to en,ja`에서 두 번째
    언어가 첫 번째 번역 위에 덮인다 - 같은 `IngestResult`를 두 번 쓰기
    때문이고, 예외도 경고도 없이 조용히 틀린다.

    `event_index`로 짝짓는 이유는 인제스트가 **표시되지 않는 이벤트를
    걸러냈기** 때문이다(`_keep_displayed`). 위치로 짝지으면 주석 이벤트가
    하나만 있어도 그 뒤가 전부 밀린다.
    """
    subs = copy.deepcopy(result.subs)

    for segment in segments:
        if segment.target_text is None:
            continue
        raw_index = result.event_index[segment.id]
        event = subs.events[raw_index]
        prefix = _LEADING_OVERRIDES.match(event.text).group(0)
        # setter를 먼저 부르는 순서가 중요하다. 이것이 `\n`을 SSA의 `\N`으로
        # 바꿔 주고, 그 다음에 접두를 붙여야 접두가 변환 대상이 되지 않는다.
        event.plaintext = segment.target_text
        event.text = prefix + event.text

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # `format_`을 넘기지 않으면 pysubs2가 확장자로 판별하는데, 확장자가 없는
    # 경로에서 예외가 난다. 원본 포맷을 명시하는 것이 FR-7.1의
    # "입력과 동일 포맷 기본"과도 맞는다.
    subs.save(str(out_path), format_=result.format)
```

`src/cuesift/ingest/__init__.py`:

```python
"""자막 파일 인제스트 (요구사항정의서 FR-1.1·1.3·1.5)와 출력 (FR-7.1)."""

from __future__ import annotations

from cuesift.ingest.loader import IngestError, IngestResult, load_subtitle
from cuesift.ingest.writer import write_subtitle

__all__ = ["IngestError", "IngestResult", "load_subtitle", "write_subtitle"]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingest_writer.py -v`

Expected: PASS — 13개 (parametrize 4개 포함)

- [ ] **Step 5: 게이트가 실제로 실패하는지 확인한다**

`writer.py`의 `subs = copy.deepcopy(result.subs)`를 `subs = result.subs`로 바꾸고:

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingest_writer.py -v`

Expected: **FAIL 2개** — `test_원본_결과를_변형하지_않는다`,
`test_두_언어를_연달아_써도_섞이지_않는다`

이어서 `prefix` 보정을 지우고(`event.text = prefix + event.text` 삭제):

Expected: **FAIL 1개** — `test_선행_태그_블록이_되붙는다`

**죽는 개수를 기록한다.** 확인 후 되돌린다.

- [ ] **Step 6: 요구사항정의서 §7.2를 고친다**

`docs/요구사항정의서.md`의 §7.2 표에서 `ingest` 행을 고친다.

```markdown
| `ingest` | 영상/자막을 세그먼트 리스트로 만들고, 번역된 세그먼트를 다시 자막으로 쓴다 | 외부: pysubs2, WhisperX |
```

**이 문서가 단일 진실 원천이므로 여기를 먼저 고친다.** WBS와 설계 스펙은
파생물이다.

- [ ] **Step 7: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
git add src/cuesift/ingest tests/test_ingest_writer.py docs/요구사항정의서.md
git commit -m "기능: 번역된 자막 파일 쓰기 (FR-7.1)"
```

---

### Task 4: CLI 배선 — 단일 언어

**Files:**

- Modify: `src/cuesift/cli.py`
- Modify: `tests/fakes/provider.py` (`cache_identity` 추가)
- Test: `tests/test_cli_translate.py`

**Interfaces:**

- Consumes: Task 2의 `CachingProvider`, Task 3의 `write_subtitle`, 기존 `translate_segments`·`load_subtitle`·`load_glossary`
- Produces:
  - `EXIT_UNAVAILABLE = 69` (모듈 상수)
  - `_build_provider(base_url: str, model: str, api_key: str | None) -> Provider` — **테스트가 monkeypatch하는 지점**
  - `_resolve_llm(base_url: str | None, model: str | None) -> tuple[str, str, str | None]`
  - `_output_path(input_path: Path, out_dir: Path | None, source_lang: str, target_lang: str) -> Path`
  - `_cache_identity(provider: Provider) -> str | None`

- [ ] **Step 0: 가짜 프로바이더에 `cache_identity`를 더한다**

**이것이 없으면 CLI 테스트에서 캐시가 조용히 꺼진다** — `_cache_identity`가
`None`을 돌려주고 경고만 낸 뒤 캐시 없이 도는데, 재개 테스트는 그것을
"캐시가 안 먹었다"로만 보고 원인을 말하지 않는다.

`tests/fakes/provider.py`의 `ScriptedProvider`와 `EchoProvider` 양쪽에
`name` 바로 아래 한 줄씩 더한다.

```python
class ScriptedProvider:
    name = "scripted"
    # WP7b 캐시가 프로바이더에게 신원을 묻는다(설계 §3.2). 없으면 CLI가
    # 캐시를 끄므로 재개 경로가 테스트에서 한 번도 실행되지 않는다.
    cache_identity = "scripted|fake|v1"
```

```python
class EchoProvider:
    name = "echo"
    cache_identity = "echo|fake|v1"
```

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_translate.py`:

```python
"""`cuesift translate` 배선 검증 (FR-8.1 · 설계 §6).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuesift.cli import app
from cuesift.translate.provider import FatalProviderError
from tests.fakes.provider import EchoProvider

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다."""
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "translate",
        str(_FIXTURES / "minimal.srt"),
        "--to", "en",
        "--out", str(tmp_path),
        "--base-url", "http://h/v1",
        "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
        *extra,
    ]


def test_번역해서_파일을_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").exists()


def test_원문_언어_태그를_치환한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "ep01.ko.srt"
    source.write_bytes((_FIXTURES / "minimal.srt").read_bytes())
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(source), "--to", "en", "--out", str(tmp_path),
        "--source-lang", "ko", "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "ep01.en.srt").exists()
    assert not (tmp_path / "ep01.ko.en.srt").exists()


def test_설정이_없으면_exit_2다(tmp_path: Path) -> None:
    # 기본값을 넣지 않는다. localhost를 기본값으로 넣으면 Ollama가 없는
    # 사람이 연결 실패를 받는데, 그것은 "설정을 안 했다"보다 진단이 어렵다.
    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en", "--out", str(tmp_path),
    ])

    assert result.exit_code == 2


def test_환경변수를_읽는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUESIFT_BASE_URL", "http://h/v1")
    monkeypatch.setenv("CUESIFT_MODEL", "m1")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en",
        "--out", str(tmp_path), "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 0, result.output


def test_없는_파일은_exit_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, [
        "translate", str(tmp_path / "없다.srt"), "--to", "en",
        "--base-url", "http://h/v1", "--model", "m1",
    ])

    assert result.exit_code == 2


def test_자막이_아니면_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "not_subtitle.txt"), "--to", "en",
        "--out", str(tmp_path), "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 66


def test_치명적_프로바이더_실패는_exit_69다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 안 잡으면 traceback이 되어 exit 1("부분 실패")로 오보된다 (설계 §8).
    class Dead:
        name = "dead"

        def complete(self, messages, *, temperature, max_tokens):  # noqa: ANN001, ANN202
            raise FatalProviderError("401 Unauthorized")

    _patch_provider(monkeypatch, Dead())

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 69
    assert "401" in result.output


def test_부분_실패는_exit_1이고_원문이_남는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # garbage=True면 배치도 개별 폴백도 전부 파싱 실패한다.
    _patch_provider(monkeypatch, EchoProvider(garbage=True))

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 1
    out = tmp_path / "minimal.en.srt"
    assert out.exists()  # 실패해도 파일은 나온다
    assert "00000" in result.output  # 실패한 세그먼트 ID를 나열한다


def test_잘못된_base_url은_exit_2다(tmp_path: Path) -> None:
    # 설정 오류는 명령줄이 틀린 것이다. ProviderError가 아니다.
    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en", "--out", str(tmp_path),
        "--base-url", "http://[::1", "--model", "m1",
    ])

    assert result.exit_code == 2


def test_출력이_입력을_덮으면_거부한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 이것이 없으면 원본 자막이 번역문으로 덮여 되돌릴 수 없다.
    source = tmp_path / "ep01.en.srt"
    source.write_bytes((_FIXTURES / "minimal.srt").read_bytes())
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(source), "--to", "en", "--out", str(tmp_path),
        "--source-lang", "en", "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 2


def test_두_번째_실행은_호출하지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # **재개 게이트다.** 호출 수를 센다.
    first = EchoProvider()
    _patch_provider(monkeypatch, first)
    assert runner.invoke(app, _args(tmp_path)).exit_code == 0
    calls_1 = len(first.calls)

    second = EchoProvider()
    _patch_provider(monkeypatch, second)
    assert runner.invoke(app, _args(tmp_path)).exit_code == 0

    assert calls_1 > 0
    assert len(second.calls) == 0


def test_no_cache는_매번_호출한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = EchoProvider()
    _patch_provider(monkeypatch, first)
    runner.invoke(app, [*_args(tmp_path), "--no-cache"])
    calls_1 = len(first.calls)

    second = EchoProvider()
    _patch_provider(monkeypatch, second)
    runner.invoke(app, [*_args(tmp_path), "--no-cache"])

    assert len(second.calls) == calls_1


def test_no_cache와_cache_dir을_함께_주면_exit_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, [*_args(tmp_path), "--no-cache"])

    assert result.exit_code == 2


def test_review_budget은_경고한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 조용한 무시는 이 저장소가 1급으로 금지한 것이다 (--config 선례).
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--review-budget", "10%"])

    assert result.exit_code == 0, result.output
    assert "review-budget" in result.output


def test_용어집을_읽는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # FR-2.3이 CLI에서 도달 가능해지는 것을 고정한다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text(
        "entries:\n  - source: 안녕\n    targets:\n      en: [Hello]\n",
        encoding="utf-8",
    )
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 0, result.output


def test_망가진_용어집은_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries가 없다\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 66
```

**`test_no_cache와_cache_dir을_함께_주면_exit_2다`는 `_args`가 이미
`--cache-dir`을 넣으므로 성립한다.**

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py -v`

Expected: FAIL — 대부분 exit 70 (`_not_implemented`)

- [ ] **Step 3: `cli.py`에 헬퍼를 추가한다**

상단 import에 더한다. **기존 `from cuesift.ingest import IngestError, load_subtitle`
줄을 갈아끼우는 것**에 주의한다.

```python
from cuesift.glossary import load_glossary
from cuesift.ingest import IngestError, IngestResult, load_subtitle, write_subtitle
from cuesift.store import CachingProvider
from cuesift.translate import (
    DEFAULT_CONTEXT_WINDOW,
    FatalProviderError,
    OpenAICompatibleProvider,
    Provider,
    TranslationResult,
    translate_segments,
)
```

**이 태스크에서 쓰지 않는 이름을 미리 넣지 않는다.** ruff의 F401이 잡는다 —
`Glossary`·`CacheRequest`·`build_messages`·`iter_batches`·`DEFAULT_BATCH_SIZE`는
Task 6에서 더한다.

상수를 더한다.

```python
# sysexits.h EX_UNAVAILABLE — 외부 서비스가 요청을 거부했다는 뜻이다.
# 70("미구현·내부 오류")과 나누는 이유는 CI가 "아직 안 만든 기능"과
# "LLM 서버가 401을 냈다"에 같은 대응을 하면 안 되기 때문이다.
EXIT_UNAVAILABLE = 69

# 기본 캐시 위치. 프로젝트 디렉터리 안에 두는 것은 `.gitignore`에 한 줄로
# 넣을 수 있고 작업물과 함께 옮겨지기 때문이다.
DEFAULT_CACHE_DIR = Path(".cuesift/cache")
```

`EXIT_NOT_IMPLEMENTED` 위 주석도 고친다 — "남은 두 골격 커맨드"가 거짓이 된다.

```python
# CI가 "미구현"과 "검수 실패"를 구분할 수 있도록 종료 코드를 분리한다.
# `translate`가 배선된 뒤로 70은 `transcribe` 하나의 표식이다.
EXIT_NOT_IMPLEMENTED = 70
```

헬퍼를 더한다.

```python
def _resolve_llm(base_url: str | None, model: str | None) -> tuple[str, str, str | None]:
    """LLM 접속 설정을 해결한다 (설계 §6.3).

    우선순위는 **CLI 옵션 > 환경변수**다. FR-8.4(`cuesift.yaml`)가 오면
    환경변수 아래에 한 칸이 더 낀다.

    **기본값을 넣지 않는다.** `localhost:11434`를 기본으로 두면 Ollama가
    없는 사람이 연결 실패를 받는데, 그것은 "설정을 안 했다"보다 진단이
    훨씬 어렵다.

    `api_key`를 명령줄로 받지 않는 이유는 셸 히스토리와 `ps` 출력에
    남기 때문이다.

    환경변수 이름에 `CUESIFT_LIVE_` 접두사를 쓰지 않는 것이 중요하다 —
    그것은 테스트 전용으로 예약돼 있고 `tests/test_translate_api.py`의
    게이트가 그 문자열로 live 마커 누락을 판정한다.
    """
    resolved_base = base_url or os.environ.get("CUESIFT_BASE_URL")
    resolved_model = model or os.environ.get("CUESIFT_MODEL")
    missing = [
        name
        for name, value in (("--base-url", resolved_base), ("--model", resolved_model))
        if not value
    ]
    if missing:
        _echo(
            f"{', '.join(missing)}가 없다. 옵션으로 주거나 "
            f"CUESIFT_BASE_URL·CUESIFT_MODEL 환경변수를 설정한다.",
            err=True,
        )
        raise typer.Exit(2)
    return resolved_base, resolved_model, os.environ.get("CUESIFT_API_KEY")


def _build_provider(*, base_url: str, model: str, api_key: str | None) -> Provider:
    """프로바이더를 만든다. **테스트가 monkeypatch하는 지점이다.**

    본문에서 `OpenAICompatibleProvider(...)`를 직접 만들면 CLI 테스트가
    네트워크를 타거나 `httpx` 내부를 패치해야 한다. 함수 하나로 빼면
    가짜를 꽂는 것이 한 줄이 된다.
    """
    return OpenAICompatibleProvider(base_url=base_url, model=model, api_key=api_key)


def _output_path(
    input_path: Path, out_dir: Path | None, source_lang: str, target_lang: str
) -> Path:
    """출력 경로를 정한다 (FR-7.1 · 설계 §5.1).

    stem이 `.{source_lang}`으로 끝나면 **치환**하고 아니면 **덧붙인다.**
    치환하지 않으면 `ep01.ko.srt`가 `ep01.ko.en.srt`가 되어 언어 태그가
    둘이 된다.
    """
    stem = input_path.stem
    suffix = f".{source_lang}"
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    directory = out_dir if out_dir is not None else input_path.parent
    return directory / f"{stem}.{target_lang}{input_path.suffix}"
```

- [ ] **Step 4: `translate()` 본문을 쓴다**

기존 `translate()`를 통째로 갈아끼운다.

```python
@app.command()
def translate(
    input: Annotated[
        Path,
        # `readable=False`는 `check`와 같은 이유다 — 읽기 가능 판정을
        # 인제스트 한 곳으로 모아 플랫폼마다 다른 코드가 나오지 않게 한다.
        typer.Argument(exists=True, dir_okay=False, readable=False, help="번역할 자막 파일"),
    ],
    to: Annotated[str, typer.Option("--to", help="대상 언어 (쉼표 구분, 예: en,ja)")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="출력 디렉터리. 기본은 입력 파일과 같은 곳"),
    ] = None,
    source_lang: Annotated[str, typer.Option("--source-lang", help="원문 언어")] = "ko",
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="OpenAI 호환 엔드포인트. 없으면 CUESIFT_BASE_URL"),
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="모델 이름. 없으면 CUESIFT_MODEL")
    ] = None,
    glossary: Annotated[
        Path | None, typer.Option("--glossary", help="용어집 YAML (FR-2.3)")
    ] = None,
    work_context: Annotated[
        str | None, typer.Option("--work-context", help="작품 맥락 (FR-2.8)")
    ] = None,
    context_window: Annotated[
        int, typer.Option("--context-window", min=0, help="앞뒤 맥락 세그먼트 수")
    ] = DEFAULT_CONTEXT_WINDOW,
    cache_dir: Annotated[
        Path | None, typer.Option("--cache-dir", help="캐시 디렉터리. 기본 .cuesift/cache")
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="캐시를 읽지도 쓰지도 않는다")
    ] = False,
    review_budget: Annotated[
        str | None,
        typer.Option("--review-budget", help="사람이 검수할 상위 비율. 아직 구현되지 않았다"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="실행하지 않고 호출 수만 추정합니다.")
    ] = False,
) -> None:
    """FR-8.1: 자막을 번역해 언어별 파일로 냅니다."""
    if no_cache and cache_dir is not None:
        _echo("--no-cache와 --cache-dir을 함께 줄 수 없다", err=True)
        raise typer.Exit(2)
    if review_budget is not None:
        # 설계 D12와 같은 판단 — 조용한 무시는 사용자가 트리아지가 됐다고
        # 믿게 만든다. `--config`가 이미 같은 방식이다.
        _echo(
            f"경고: --review-budget은 아직 구현되지 않았습니다 (WP5). "
            f"지정한 '{review_budget}'는 무시됩니다.",
            err=True,
        )

    resolved_base, resolved_model, api_key = _resolve_llm(base_url, model)
    targets = [lang.strip() for lang in to.split(",") if lang.strip()]
    if not targets:
        _echo("--to에 대상 언어가 없다", err=True)
        raise typer.Exit(2)

    try:
        result = load_subtitle(input, source_lang=source_lang)
    except IngestError as exc:
        _echo(str(exc), err=True)
        raise typer.Exit(EXIT_BAD_INPUT) from exc

    for target in targets:
        out_path = _output_path(input, out, source_lang, target)
        if out_path.resolve() == input.resolve():
            # 이것이 없으면 원본이 번역문으로 덮여 되돌릴 수 없다.
            _echo(f"출력 경로가 입력과 같다: {out_path}", err=True)
            raise typer.Exit(2)

    try:
        provider = _build_provider(
            base_url=resolved_base, model=resolved_model, api_key=api_key
        )
    except ValueError as exc:
        # 생성자의 ValueError는 ProviderError가 **아니다** — 설정 오류이지
        # 호출 실패가 아니다. 명령줄이 틀린 것이므로 2다.
        _echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    worst = 0
    for target in targets:
        worst = max(worst, _translate_one(
            result=result,
            input_path=input,
            out_dir=out,
            source_lang=source_lang,
            target_lang=target,
            provider=provider,
            glossary_path=glossary,
            work_context=work_context,
            context_window=context_window,
            cache_dir=None if no_cache else (cache_dir or DEFAULT_CACHE_DIR),
        ))
    if worst:
        raise typer.Exit(worst)
```

`_translate_one`을 더한다.

```python
def _translate_one(
    *,
    result: IngestResult,
    input_path: Path,
    out_dir: Path | None,
    source_lang: str,
    target_lang: str,
    provider: Provider,
    glossary_path: Path | None,
    work_context: str | None,
    context_window: int,
    cache_dir: Path | None,
) -> int:
    """대상 언어 하나를 번역해 파일로 낸다. 종료 코드 후보를 돌려준다.

    **예외를 여기서 잡아 코드로 바꾼다.** 새어 나가면 미처리 traceback이
    되어 exit 1("부분 실패")로 오보된다 (설계 §8).

    **`result.segments`를 사본 없이 그대로 넘긴다.** `engine.py`가
    `replace(s, target_text=...)`로 **새 튜플**을 만들어 돌려주므로 원본은
    변형되지 않는다 - 여러 언어를 돌아도 앞 언어의 번역문이 남지 않는다.
    방어적 사본을 넣으면 그 사실이 코드에서 사라져 나중에 엔진 쪽 계약이
    깨져도 드러나지 않는다.
    """
    glossary = None
    if glossary_path is not None:
        try:
            glossary = load_glossary(glossary_path, target_lang)
        except (OSError, ValueError) as exc:
            _echo(f"{glossary_path}: 용어집을 읽지 못했다 - {exc}", err=True)
            return EXIT_BAD_INPUT

    if cache_dir is not None:
        identity = _cache_identity(provider)
        if identity is None:
            # 신원을 모르는 프로바이더에 캐시를 걸면 다른 모델의 응답이
            # 히트한다. 끄는 쪽이 안전하고, **조용히 끄지는 않는다** —
            # 사용자는 재개가 되는 줄 안다.
            _echo(
                f"경고: {provider.name}이 cache_identity를 제공하지 않아 캐시를 끈다",
                err=True,
            )
        else:
            provider = CachingProvider(
                provider,
                identity=identity,
                cache_dir=cache_dir,
                warn=lambda message: _echo(message, err=True),
            )

    try:
        translated = translate_segments(
            result.segments,
            provider=provider,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary=glossary,
            work_context=work_context,
            context_window=context_window,
        )
    except FatalProviderError as exc:
        _echo(f"프로바이더가 요청을 거부했다: {exc}", err=True)
        return EXIT_UNAVAILABLE
    except ValueError as exc:
        # 배치·맥락 조립이 틀린 것이므로 명령줄 오류다.
        _echo(str(exc), err=True)
        return 2

    out_path = _output_path(input_path, out_dir, source_lang, target_lang)
    write_subtitle(result, translated.segments, out_path)

    hits = getattr(provider, "hits", 0)
    misses = getattr(provider, "misses", 0)
    for line in _format_translate_summary(
        target_lang=target_lang,
        out_path=out_path,
        result=translated,
        hits=hits,
        misses=misses,
    ):
        _echo(line)

    return 1 if translated.failures else 0


def _cache_identity(provider: Provider) -> str | None:
    """프로바이더가 자기 신원을 말하게 한다 (설계 §3.2). 없으면 `None`.

    `getattr`로 읽는 이유는 `Provider` 프로토콜에 이 속성이 **없기**
    때문이다 — 표면을 최소로 두는 [번역 엔진 설계] §4.1의 결정을 유지한다.

    **예외를 던지지 않고 `None`을 돌려주는 것이 요점이다.** 캐시를 못 켜는
    것은 실행이 불가능한 상태가 아니다 — 호출자가 경고하고 캐시 없이 돈다.
    """
    identity = getattr(provider, "cache_identity", None)
    return str(identity) if identity else None
```

- [ ] **Step 5: 요약 포매터를 쓴다**

```python
def _format_translate_summary(
    *,
    target_lang: str,
    out_path: Path,
    result: TranslationResult,
    hits: int,
    misses: int,
) -> list[str]:
    """언어 하나의 결과를 요약한다 (설계 §4.3·§5.3).

    **캐시 히트를 항상 낸다.** 캐시가 곧 재개이므로 이 숫자가 "재개됐다"의
    유일한 증거다. 없으면 사용자는 네트워크를 탔는지 알 수 없다.

    **실패는 개수만이 아니라 ID를 나열한다.** 원문이 남은 자막은 겉보기에
    정상인 파일이라, 개수만 보고 넘기면 미번역 자막이 그대로 배포된다.
    """
    total = len(result.segments)
    failed = len(result.failures)
    lines = [
        f"[{target_lang}] {out_path}",
        f"  세그먼트 {total}개 · 성공 {total - failed}개 · 실패 {failed}개",
        f"  캐시 히트 {hits}개 · 실제 호출 {misses}개",
        f"  토큰 prompt {result.usage.prompt_tokens} · completion "
        f"{result.usage.completion_tokens} · calls {result.usage.calls}",
    ]
    if result.failures:
        ids = ", ".join(f.segment_id for f in result.failures)
        lines.append(f"  실패 세그먼트(원문 유지): {ids}")
    return lines
```

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py -v`

Expected: PASS — 16개

- [ ] **Step 7: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8'), feature_version=(3,11)) for p in list(pathlib.Path('src').rglob('*.py'))+list(pathlib.Path('tests').rglob('*.py'))]; print('3.11 OK')"
git add src/cuesift/cli.py tests/test_cli_translate.py
git commit -m "기능: cuesift translate 배선 - 번역·재개·자막 출력 (FR-8.1, FR-7.1)"
```

**3.11 문법 검사를 잊지 않는다.** 로컬은 3.14이고 CI는 3.11·3.12다.

---

### Task 5: 여러 대상 언어

**Files:**

- Modify: `src/cuesift/cli.py`
- Test: `tests/test_cli_translate.py` (추가)

**Interfaces:**

- Consumes: Task 4의 `_translate_one`·`_format_translate_summary`
- Produces: 변경 없음 — Task 4의 루프가 이미 여러 언어를 돌지만, **Fatal에서 멈추는 것과 종료 코드 합성이 검증되지 않았다**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_translate.py`에 더한다.

```python
def test_두_언어를_모두_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en,ja",
        "--out", str(tmp_path), "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").exists()
    assert (tmp_path / "minimal.ja.srt").exists()


def test_언어별로_요약을_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 뭉뚱그리면 "ja만 전부 실패"가 "2개 언어 6건 중 3건 실패"로 보인다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en,ja",
        "--out", str(tmp_path), "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert "[en]" in result.output
    assert "[ja]" in result.output


def test_치명적_실패는_다음_언어를_돌지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 401을 언어 수만큼 반복하면 진짜 원인이 실패 더미 아래 묻힌다.
    class Dead:
        name = "dead"
        cache_identity = "dead|u|m"

        def __init__(self) -> None:
            self.calls: list[object] = []

        def complete(self, messages, *, temperature, max_tokens):  # noqa: ANN001, ANN202
            self.calls.append(messages)
            raise FatalProviderError("401 Unauthorized")

    provider = Dead()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en,ja,th",
        "--out", str(tmp_path), "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 69
    # 한 언어에서만 시도했다. 세 언어를 다 돌면 호출이 3배가 된다.
    assert len(provider.calls) == 1


def test_한_언어가_실패해도_나머지를_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 부분 실패는 FR-2.6의 정신대로 계속 진행한다.
    _patch_provider(monkeypatch, EchoProvider(garbage=True))

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en,ja",
        "--out", str(tmp_path), "--base-url", "http://h/v1", "--model", "m1",
        "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 1
    assert (tmp_path / "minimal.en.srt").exists()
    assert (tmp_path / "minimal.ja.srt").exists()


def test_종료_코드는_가장_나쁜_것이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # en은 성공, ja는 용어집 오류(66). 66이 1보다 나쁘다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries가 없다\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en,ja",
        "--out", str(tmp_path), "--base-url", "http://h/v1", "--model", "m1",
        "--glossary", str(glossary), "--cache-dir", str(tmp_path / "cache"),
    ])

    assert result.exit_code == 66
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py -k "언어" -v`

Expected: `test_치명적_실패는_다음_언어를_돌지_않는다`가 FAIL —
Task 4의 루프는 Fatal에서도 다음 언어를 돈다.

- [ ] **Step 3: 루프를 고친다**

`translate()`의 루프를 고친다.

```python
    worst = 0
    for target in targets:
        code = _translate_one(...)
        worst = max(worst, code)
        if code == EXIT_UNAVAILABLE:
            # 인증·모델 오류는 다음 언어에서도 같다. 반복하면 진짜 원인이
            # 실패 더미 아래 묻히고 호출만 언어 수만큼 는다 (설계 §6.4).
            break
    if worst:
        raise typer.Exit(worst)
```

**`max()`가 "가장 나쁜 것"을 내는 것은 코드가 우연히 크기 순이기
때문이다**(0 < 1 < 2 < 66 < 69). 우연에 기대지 않도록 주석으로 못 박는다.

```python
    # 종료 코드의 숫자 크기가 심각도 순과 일치한다: 0 < 1 < 2 < 66 < 69.
    # 이 성질이 깨지면 max()가 틀린 코드를 낸다 — 새 코드를 추가할 때
    # 반드시 확인한다.
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py -v`

Expected: PASS — 21개

- [ ] **Step 5: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest -q
git add src/cuesift/cli.py tests/test_cli_translate.py
git commit -m "기능: 여러 대상 언어 순차 번역과 종료 코드 합성 (FR-2.1)"
```

---

### Task 6: `--dry-run`

**Files:**

- Modify: `src/cuesift/cli.py`
- Test: `tests/test_cli_translate.py` (추가)

**Interfaces:**

- Consumes: `iter_batches`·`build_messages`(기존 공개 API), Task 1의 `CacheRequest`
- Produces: `_dry_run_report(...) -> list[str]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_dry_run은_파일을_만들지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "minimal.en.srt").exists()


def test_dry_run은_네트워크를_타지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # "실행 전 추정"이라는 NFR-2의 전제가 여기 걸려 있다.
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert provider.calls == []


def test_dry_run이_배치_수를_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "배치" in result.output
    assert "호출 필요" in result.output


def test_dry_run이_캐시_히트를_센다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 실사용에서 가장 쓸모있는 정보다 — "몇 번 더 불러야 하나".
    _patch_provider(monkeypatch, EchoProvider())
    runner.invoke(app, _args(tmp_path))  # 먼저 실제로 돌려 캐시를 채운다

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "캐시 히트 1개" in result.output
    assert "호출 필요 0개" in result.output


def test_dry_run은_토큰을_추정하지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 계수 출처가 없다 (§11 R8). 틀린 수치는 수치가 없는 것보다 나쁘다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "토큰 추정" not in result.output
    assert "$" not in result.output


def test_dry_run_no_cache는_히트를_0으로_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())
    runner.invoke(app, _args(tmp_path))

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en", "--out", str(tmp_path),
        "--base-url", "http://h/v1", "--model", "m1", "--no-cache", "--dry-run",
    ])

    assert "캐시 히트 0개" in result.output
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py -k dry -v`

Expected: FAIL — 파일이 만들어지고 호출이 일어난다

- [ ] **Step 3: `_dry_run_report`를 쓴다**

`cli.py`에 더한다. import 세 줄을 **기존 줄에 합쳐서** 고친다.

```python
from cuesift.glossary import Glossary, load_glossary
from cuesift.store import CacheRequest, CachingProvider
from cuesift.translate import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONTEXT_WINDOW,
    FatalProviderError,
    OpenAICompatibleProvider,
    Provider,
    TranslationResult,
    build_messages,
    iter_batches,
    translate_segments,
)
```

```python
def _dry_run_report(
    *,
    result: IngestResult,
    input_path: Path,
    out_dir: Path | None,
    source_lang: str,
    targets: Sequence[str],
    base_url: str,
    model: str,
    identity: str | None,
    glossary: Glossary | None,
    work_context: str | None,
    context_window: int,
    cache_dir: Path | None,
) -> list[str]:
    """실행하지 않고 추정치를 낸다 (NFR-2 · 설계 §7).

    **실측할 수 있는 것만 낸다.** 배치 수와 문자 수는 `build_messages`를
    실제로 불러 **정확히** 세고, 캐시 히트는 키를 계산해 **파일 존재만**
    확인한다. 토큰과 비용은 내지 않는다 - 문자에서 토큰으로 가는 계수가
    모델마다 다르고 우리에게 출처가 없다(요구사항정의서 §11 R8).

    **네트워크를 타지 않는다.** 그래야 "실행 전 추정"이라는 전제가 선다.
    """
    lines = [
        f"입력   {input_path} ({result.format}) · {len(result.segments)} 세그먼트",
        f"모델   {model} @ {base_url}",
    ]
    for target in targets:
        batches = 0
        hits = 0
        system_chars = 0
        user_chars = 0
        for window in iter_batches(
            result.segments, size=DEFAULT_BATCH_SIZE, context_window=context_window
        ):
            batches += 1
            # **`build_messages`는 `BatchWindow`를 받지 않는다.** batch·before·after를
            # 따로 받는다(`prompt.py`). 통째로 넘기면 TypeError다.
            messages = build_messages(
                window.batch,
                source_lang=source_lang,
                target_lang=target,
                before=window.before,
                after=window.after,
                glossary=glossary,
                work_context=work_context,
            )
            system_chars += sum(len(m.content) for m in messages if m.role == "system")
            user_chars += sum(len(m.content) for m in messages if m.role == "user")
            if cache_dir is not None and identity is not None:
                request = CacheRequest(
                    identity=identity,
                    temperature=0.0,
                    max_tokens=None,
                    messages=tuple(messages),
                )
                if (cache_dir / f"{request.key}.json").exists():
                    hits += 1
        lines.extend(
            [
                "",
                f"[{target}] {_output_path(input_path, out_dir, source_lang, target)}",
                f"  배치 {batches}개 (size={DEFAULT_BATCH_SIZE}, context_window={context_window})",
                f"  캐시 히트 {hits}개 · 호출 필요 {batches - hits}개",
                f"  프롬프트 문자 system {system_chars:,} + user {user_chars:,}",
            ]
        )
    lines.append("")
    lines.append("(토큰·비용은 내지 않는다 — 문자에서 토큰으로 가는 계수의 출처가 없다)")
    return lines
```

**`temperature=0.0`·`max_tokens=None`을 손으로 쓰는 것이 취약점이다.**
엔진의 기본값이 바뀌면 dry-run의 캐시 판정이 조용히 틀린다. 주석으로
못 박는다.

```python
                # 엔진이 실제로 쓰는 값과 같아야 한다. 어긋나면 dry-run이
                # "호출 필요 82개"라 해 놓고 실행은 0개를 부른다.
                # `translate_segments`의 기본값(temperature=0.0)과
                # `_call_with_retry`의 `max_tokens=None`을 따라간다.
```

- [ ] **Step 4: `translate()`에서 분기한다**

`_resolve_llm`과 `load_subtitle` 뒤, 번역 루프 앞에 넣는다.

```python
    if dry_run:
        glossary_obj = None
        if glossary is not None:
            try:
                glossary_obj = load_glossary(glossary, targets[0])
            except (OSError, ValueError) as exc:
                _echo(f"{glossary}: 용어집을 읽지 못했다 - {exc}", err=True)
                raise typer.Exit(EXIT_BAD_INPUT) from exc
        # 프로바이더를 만들지 않는다 — 네트워크 클라이언트를 여는 것 자체가
        # "실행 전 추정"의 전제를 흐린다. identity는 손으로 조립한다.
        identity = f"{OpenAICompatibleProvider.name}|{resolved_base.rstrip('/')}|{resolved_model}"
        for line in _dry_run_report(
            result=result,
            input_path=input,
            out_dir=out,
            source_lang=source_lang,
            targets=targets,
            base_url=resolved_base,
            model=resolved_model,
            identity=None if no_cache else identity,
            glossary=glossary_obj,
            work_context=work_context,
            context_window=context_window,
            cache_dir=None if no_cache else (cache_dir or DEFAULT_CACHE_DIR),
        ):
            _echo(line)
        return
```

**`identity`를 손으로 조립하는 것이 `cache_identity`와 어긋날 위험이 있다.**
Task 2가 `f"{name}|{base_url}|{model}"`로 정했고 `base_url`은 `rstrip("/")`
된다. 이 중복을 테스트로 고정한다 — Step 5에 추가한다.

- [ ] **Step 5: 중복 정의를 고정하는 테스트를 더한다**

```python
def test_dry_run의_identity가_실제와_같다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 손으로 조립한 identity가 cache_identity와 어긋나면 dry-run이
    # "호출 필요 N개"라 해 놓고 실행은 0개를 부른다.
    _patch_provider(monkeypatch, EchoProvider())
    runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en", "--out", str(tmp_path),
        "--base-url", "http://h/v1/", "--model", "m1", "--cache-dir", str(tmp_path / "cache"),
    ])

    result = runner.invoke(app, [
        "translate", str(_FIXTURES / "minimal.srt"), "--to", "en", "--out", str(tmp_path),
        "--base-url", "http://h/v1/", "--model", "m1", "--cache-dir", str(tmp_path / "cache"),
        "--dry-run",
    ])

    assert "캐시 히트 1개" in result.output
```

**끝의 슬래시를 일부러 넣었다.** `OpenAICompatibleProvider`가 `rstrip("/")`
하므로, dry-run이 그것을 흉내 내지 않으면 이 테스트가 죽는다.

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py -v`

Expected: PASS — 28개

- [ ] **Step 7: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest -q
git add src/cuesift/cli.py tests/test_cli_translate.py
git commit -m "기능: translate --dry-run - 배치 수와 캐시 히트를 실측한다 (NFR-2)"
```

---

### Task 7: live 검증과 문서

**Files:**

- Modify: `tests/test_translate_live.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/WBS.md`
- Modify: `HANDOFF.md`
- Modify: `.gitignore` (`.cuesift/`)

**Interfaces:**

- Consumes: 앞선 모든 태스크
- Produces: 없음 (검증과 기록)

- [ ] **Step 1: live 테스트를 더한다**

`tests/test_translate_live.py`에 더한다.

```python
@pytest.mark.live
def test_cli가_실제_프로세스로_동작한다(tmp_path: Path) -> None:
    """`typer.Exit`이 실제 프로세스 종료 코드가 되는지는 CliRunner가
    완전히 증명하지 못한다. 진짜로 돌려 본다.

    **`-m live`로만 돈다.** CI에는 엔드포인트가 없다.
    """
    base_url = os.environ.get("CUESIFT_LIVE_BASE_URL")
    model = os.environ.get("CUESIFT_LIVE_MODEL")
    if not base_url or not model:
        pytest.skip("CUESIFT_LIVE_BASE_URL / CUESIFT_LIVE_MODEL이 없다")

    fixture = Path(__file__).parent / "fixtures" / "ingest" / "minimal.srt"
    env = {
        **os.environ,
        "CUESIFT_BASE_URL": base_url,
        "CUESIFT_MODEL": model,
    }
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable, "-m", "cuesift", "translate", str(fixture),
            "--to", "en", "--out", str(tmp_path),
            "--cache-dir", str(tmp_path / "cache"),
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
    )

    assert proc.returncode in (0, 1), proc.stderr
    assert (tmp_path / "minimal.en.srt").exists()

    # 2회차는 캐시 히트라 실제 호출이 0이어야 한다 — 재개의 실물 증거다.
    again = subprocess.run(  # noqa: S603
        [
            sys.executable, "-m", "cuesift", "translate", str(fixture),
            "--to", "en", "--out", str(tmp_path),
            "--cache-dir", str(tmp_path / "cache"),
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
    )

    assert "실제 호출 0개" in again.stdout
```

**`python -m cuesift`가 동작하는지 확인이 필요하다.** `src/cuesift/__main__.py`가
없으면 `cuesift` 콘솔 스크립트를 쓰거나 `__main__.py`를 만든다.

Run: `.venv/Scripts/python.exe -m cuesift --version`

없으면 `src/cuesift/__main__.py`를 만든다.

```python
"""`python -m cuesift` 진입점.

콘솔 스크립트(`cuesift`)는 설치 위치에 따라 PATH에 없을 수 있다.
`-m`은 항상 동작하므로 서브프로세스 테스트가 이쪽을 쓴다.
"""

from __future__ import annotations

from cuesift.cli import run

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: live를 돌린다**

```powershell
$env:CUESIFT_LIVE_BASE_URL = "http://127.0.0.1:11434/v1"
$env:CUESIFT_LIVE_MODEL = "qwen2.5:3b"
.venv/Scripts/python.exe -m pytest -m live -v
```

Expected: PASS. **수치를 기록한다** — 종료 코드, 실제 호출 수, 2회차 호출 수.

Ollama는 트레이 앱으로 자동 기동돼 `127.0.0.1:11434`를 듣는다.
`ollama serve`를 따로 칠 필요가 없다.

- [ ] **Step 3: `.gitignore`에 캐시를 더한다**

```gitignore
# LLM 응답 캐시 (NFR-3). 작업물과 함께 옮겨지지만 리포에는 넣지 않는다.
.cuesift/
```

- [ ] **Step 4: README를 고친다**

"개발 환경 > 실제 LLM 엔드포인트 테스트" 절 근처에 사용법을 더한다.

````markdown
### 번역 (FR-8.1)

```bash
export CUESIFT_BASE_URL=http://127.0.0.1:11434/v1
export CUESIFT_MODEL=qwen2.5:3b
cuesift translate ep01.ko.srt --to en,ja --out dist
```

**같은 명령을 다시 치면 재개된다.** 성공한 호출은 `.cuesift/cache/`에
남아 두 번째 실행에서 네트워크를 타지 않는다.

**형식을 어긴 응답도 캐시된다.** 재실행으로는 결과가 나아지지 않는다 —
모델을 바꾸거나 `--no-cache`를 쓰거나 `.cuesift/cache/`를 지운다.

```bash
cuesift translate ep01.ko.srt --to en --dry-run   # 몇 번 더 불러야 하나
```
````

- [ ] **Step 5: CHANGELOG를 고친다**

Keep a Changelog 형식으로 `Added`에 더한다.

```markdown
### Added

- `cuesift translate` 배선 — 자막을 번역해 언어별 파일로 낸다 (FR-8.1, FR-7.1)
- LLM 응답 캐시와 재개 — 같은 명령을 다시 치면 성공한 호출을 재사용한다 (FR-2.7, NFR-3)
- `--dry-run` — 배치 수와 캐시 히트를 실측해 낸다. 토큰·비용은 추정하지 않는다 (NFR-2)
- 종료 코드 69(`EX_UNAVAILABLE`) — LLM 서버가 요청을 거부한 경우
```

- [ ] **Step 6: WBS를 고친다**

WP7b 행을 ⬜에서 ✅로 바꾸고 **근거 커밋을 함께 적는다.** 진척 막대와
완료 개수도 갱신한다. FR-2.7·FR-8.1·FR-7.1 셋이 닫히므로
**28/42 → 31/42 (74%)** 다.

WP5·WP6 행의 "남은 FR" 서술도 고친다 — FR-7.1과 FR-8.1이 빠진다.

"다음 작업 순서" 표에서 WP7b 행을 취소선 처리하고 WP8을 1순위로 올린다.

- [ ] **Step 7: HANDOFF를 다시 쓴다**

세션 인수인계를 갱신한다. **게이트 실행 기록 표의 수치를 실측으로 채운다.**

- [ ] **Step 8: 전체 게이트를 돌린다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**두 문서 게이트의 파일 수가 일치하는지 확인한다.** 갈라지면 추적되지 않는
문서가 있다는 뜻이다.

- [ ] **Step 9: 커밋하고 PR을 만든다**

```bash
git add -A
git commit -m "문서: WP7b 완료 기록과 사용법"
git push -u origin feat/translate-cli
gh pr create --base main
gh pr checks --watch
```

**PR 본문에는 무엇을·근거 문서·게이트 수치를 담는다.** 게이트 수치는
개수를 그대로 적는다.

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | 태스크 |
| --- | --- |
| §2.1 모듈 배치 | 1·2·3 |
| §2.2 `translate/` 무변경 | 2 (`cache_identity` 추가만) |
| §2.3 `ingest/writer.py` | 3 |
| §3.1 키 | 1 |
| §3.2 identity | 2 |
| §3.3 저장 형식 | 1 |
| §3.4 원자성 | 1 |
| §3.5 무엇을 캐시하나 | 2 |
| §3.5.1 히트의 usage | 2 |
| §3.6 함정 | 2 (테스트) · 7 (README) |
| §4 재개 | 2 (`test_새_프로세스가_남긴_캐시를_읽는다`) · 4 (`test_두_번째_실행은_호출하지_않는다`) |
| §5.1 파일명 | 4 (`_output_path`) |
| §5.2 라운드트립 | 3 |
| §5.2.1 태그 | 3 |
| §5.3 실패 세그먼트 | 3 · 4 |
| §6.1 옵션 | 4 |
| §6.3 우선순위 | 4 (`_resolve_llm`) |
| §6.4 여러 언어 | 5 |
| §6.5 옵션 조합 | 4 (`--no-cache`+`--cache-dir`) · 6 (dry-run 조합) |
| §6.6 종료 코드 | 4 · 5 |
| §7 dry-run | 6 |
| §8 오류→코드 | 4 |
| §9 테스트 전략 | 전부 |

**2. 계획 작성 중 실측이 잡은 것 3건**

계획을 쓰는 동안 실제 코드와 대조해 고친 것이다. **적어 두는 이유는
"계획대로 구현했는데 안 된다"의 원인이 대개 이런 어긋남이기 때문이다.**

| 무엇 | 계획 초안 | 실제 |
| --- | --- | --- |
| `build_messages` 시그니처 | `build_messages(window, ...)` | `(batch, *, before=, after=, ...)` — `BatchWindow`를 받지 않는다 |
| 세그먼트 사본 | `[replace(s) for s in ...]`로 방어 | 불필요 — `engine.py`가 `replace(s, target_text=...)`로 새 튜플을 만든다 |
| 가짜 프로바이더 | 그대로 쓸 수 있다고 가정 | `cache_identity`가 없어 **CLI 테스트에서 캐시가 조용히 꺼진다** |

세 번째가 가장 위험했다 — 재개 테스트가 실패하는데 원인이 "캐시 로직이
틀렸다"로 보이고 실제로는 테스트 더블이 신원을 안 말한 것이다.

**3. 미해결로 남기는 것**

`_dry_run_report`가 `temperature=0.0`·`max_tokens=None`·`DEFAULT_BATCH_SIZE`를
손으로 반복한다. 엔진 기본값이 바뀌면 조용히 어긋난다 — dry-run이
"호출 필요 82개"라 해 놓고 실행은 0개를 부른다.
`test_dry_run의_identity가_실제와_같다`가 `base_url` 정규화만 고정하고
나머지 셋은 **고정하지 못한다.** Task 6의 주석에 한계로 적었다.

**이것을 지금 고치지 않는 이유**는 고치려면 엔진이 "이 설정으로 부를
메시지 목록"을 내주는 함수를 새로 공개해야 하고, 그것은 WP7a의 공개 API
변경이라 되돌리기 단위가 커지기 때문이다. WP8이 같은 것을 필요로 하면
그때 함께 낸다.

**4. 타입 일관성**

- `CacheRequest`·`load`·`store` — Task 1 정의, Task 2·6에서 소비. 일치
- `CachingProvider(inner, *, identity, cache_dir, warn)` — Task 2 정의, Task 4에서 소비. 일치
- `write_subtitle(result, segments, out_path)` — Task 3 정의, Task 4에서 소비. 일치
- `_output_path(input_path, out_dir, source_lang, target_lang)` — Task 4 정의, Task 6에서 소비. 일치
- `_build_provider(*, base_url, model, api_key)` — Task 4 정의, 테스트가 `lambda **_`로 patch. 키워드 전용이라 일치
