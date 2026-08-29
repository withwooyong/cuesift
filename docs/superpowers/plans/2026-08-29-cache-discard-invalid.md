# 파싱 실패 응답을 캐시에서 폐기한다 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 형식을 어겨 파싱조차 되지 않은 응답(`invalid_response`)을 캐시에 남기지 않아, 같은 명령을 다시 치면 **그 배치만 실제로 재호출**되고 성공분은 캐시에 그대로 남게 한다.

**Architecture:** 새 개념은 없다. 캐시 계층은 응답이 쓸모 있는지 **모르고**(판정은 `translate/batch.py::parse_translations`에 있다), 앞으로도 모르게 둔다. 대신 `CachingProvider`에 `complete()`와 대칭인 `discard()`를 열고, 판정을 아는 engine이 `InvalidResponseError` 분기 **두 곳**에서 그것을 시킨다. 캐시가 스스로 판정하려 들면 판정 로직이 두 곳으로 갈라진다.

**Tech Stack:** Python 3.11+ · pytest. 의존성 추가 없음.

**근거:** [HANDOFF.md](../../../HANDOFF.md) 파킹 #13 · [요구사항정의서](../../요구사항정의서.md) FR-2.7(재개)·FR-2.6(실패 분류) · `src/cuesift/store/provider.py` 모듈 독스트링(캐시 계층의 계약)

## Global Constraints

- 모든 모듈 첫 줄에 `from __future__ import annotations`
- 독스트링·주석은 **한국어**, 근거 FR·§ 번호 병기
- 주석은 "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"**
- ruff `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`
- **사용자에게 나가는 문자열에 em dash(U+2014)를 쓰지 않는다** — cp949가 인코딩하지 못한다(실측). `·`(U+00B7)는 쓴다
- 게이트는 CI와 같은 대상 `.`으로 돌린다. `src tests`로 좁히지 않는다
- 의존성 추가 금지 — 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개
- Python 실행은 `.venv/Scripts/python.exe`
- 커밋 메시지는 **한국어**

## 착수 기준선 — 못 박는다

2026-08-29 `main`(`782c4b3`)에서 실측한 값이다.

| 항목 | 착수 시점 값 |
| --- | --- |
| `pytest --cov=cuesift` | **1571 passed · 3 deselected** · 커버리지 **99%**(2484문 중 31 미도달) |
| `ruff check .` | All checks passed! |
| `ruff format --check .` | **114 files** already formatted |
| `scripts/check_links.py` | 마크다운 **38개** · 상대 링크 **187개** · 깨진 링크 **0** |
| `npx markdownlint-cli2` | **38 files** · **0 issues** |
| CI 기대값 | **1570 passed · 1 skipped · 3 deselected**(`data/`가 `.gitignore`라 벤치 트랙이 없어 `tests/test_bench_glossary.py`가 1건 skip) |

**완료 시 `pytest` 수치가 이 값보다 작으면 게이트를 지운 것이다.** 계획서 1개 · 테스트 파일 1개가 늘므로 마크다운은 38 → 39, ruff format 대상은 114 → 115가 된다.

## 착수 조사 — 파킹 노트를 정정했다

파킹 #13 노트 세 줄 중 **하나가 거짓**이다. 구현 전에 이것을 알아야 범위가 정해진다.

| 노트가 적은 것 | 실측 |
| --- | --- |
| "**모델을 바꿔도** 캐시를 지우기 전까지 같은 실패가 무료로 영구 재생된다" | **거짓.** 캐시 키에 `identity`가 들어가고 `identity = base_url\|model`이다(`translate/openai_compat.py:129`). 모델을 바꾸면 키가 달라져 미스가 난다 |
| "`complete()`가 성공·실패를 가리지 않고 저장한다" | 참(`store/provider.py:133`) |
| "`provider_error`는 저장되지 않는다" | 참. 예외가 저장 코드에 **도달하지 못한다** — 조건문이 아니라 구조로 보장된다 |

확인 방법(그대로 돌릴 수 있다):

```bash
.venv/Scripts/python.exe -c "
from cuesift.store.cache import CacheRequest
from cuesift.translate.provider import ChatMessage
m=(ChatMessage(role='user',content='hi'),)
a=CacheRequest(identity='http://h/v1|qwen2.5:3b',temperature=0.0,max_tokens=None,messages=m)
b=CacheRequest(identity='http://h/v1|gpt-4o',temperature=0.0,max_tokens=None,messages=m)
print('모델 교체 키 일치:', a.key==b.key)   # False
"
```

**그래서 실제로 남는 피해는 하나다** — *같은 모델·같은 설정으로 다시 돌릴 때*, 즉 "그냥 한 번 더 돌려본다"는 가장 흔한 복구 수단이 무력화된다. 모델·엔드포인트·프롬프트·용어집·온도를 바꾸는 복구 경로는 **이미 캐시를 우회한다**. 기존 탈출구 `--no-cache`는 전부/전무라 실패 2건을 재시도하려면 성공한 3998건도 다시 결제해야 한다.

## 범위의 못 — 사유 3종 중 하나만 뺀다

| 사유 | 캐시되나 | 근거 |
| --- | --- | --- |
| `provider_error` | ❌ (오늘도) | 예외가 저장 코드에 도달하지 못한다. **구조**로 보장됨 |
| `invalid_response` | ❌ **(이 작업)** | 모델이 계약을 어긴 응답. 재호출이 실제로 성공할 수 있다 |
| `empty_translation` | ✅ **유지** | 개수도 번호도 맞은 **계약을 지킨 응답**이다. 폐기하면 같은 배치에서 성공한 나머지까지 다시 결제한다 |

이 표가 `--no-cache`와 갈리는 지점이다. **Task 2의 호출 수 행렬이 이 표를 게이트로 만든다.**

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `src/cuesift/store/cache.py` | 키→경로 규칙의 **단일 출처**. `load`·`store` 옆에 `discard` | T1 |
| `src/cuesift/store/provider.py` | `complete()`와 대칭인 `discard()` 표면. 요청 조립을 `_request()`로 단일화 | T1 |
| `src/cuesift/translate/engine.py` | `InvalidResponseError` 분기 2곳에서 폐기를 시킨다. `max_tokens` 상수화 | T2 |
| `tests/test_store_cache.py` | `store`가 쓴 것을 `discard`가 지우는가(경로 규칙 일치 게이트) | T1 |
| `tests/test_store_provider.py` | 폐기 표면의 단위 계약 · 삭제 실패 시 경고 | T1 |
| `tests/test_cache_discard.py` (신규) | **engine × 캐시 교차 계약.** 2회차 호출 수 행렬 | T2 |
| `src/cuesift/cli.py` | 종료 코드 3의 근거 주석 — 실측 문장이 **부분적으로 거짓이 된다** | T3 |
| `README.md` | 캐시 설명 2곳(§종료 코드 근거 · §translate) | T3 |
| `CHANGELOG.md` · `HANDOFF.md` | 기록 · 파킹 #13 닫기와 노트 정정 | T3 |

---

## Task 1: 캐시 계층에 폐기 표면을 연다

**Files:**

- Modify: `src/cuesift/store/cache.py` (`load` 위에 `_entry_path` 신설 · `load`·`store` 경로 조립 교체 · `store` 뒤에 `discard` 신설)
- Modify: `src/cuesift/store/provider.py` (임포트 1줄 · `complete` 안의 요청 조립을 `_request`로 추출 · `discard`·`_discard_or_warn` 신설)
- Modify: `tests/test_store_cache.py` (테스트 2건 추가)
- Modify: `tests/test_store_provider.py` (테스트 5건 추가)

**Interfaces:**

- Produces: `cuesift.store.cache.discard(cache_dir: Path, request: CacheRequest) -> None` · `CachingProvider.discard(messages: Sequence[ChatMessage], *, temperature: float, max_tokens: int | None) -> None`
- Consumes: 기존 `CacheRequest` · `CACHE_IO_ERRORS` · `CachingProvider._warn_once`

### 왜 경로 조립을 먼저 합치는가

`cache_dir / f"{request.key}.json"`이 지금 **세 곳**에 있다(`load` 1회, `store` 2회 — 최종 경로와 tmp 접두). 네 번째를 추가하면 규칙이 갈라질 자리가 하나 더 는다. 갈라짐이 특히 나쁜 이유는 **실패가 조용하기 때문**이다 — 폐기가 다른 경로를 지우면 지울 것이 없어 `missing_ok=True`가 성공하고, 캐시는 그대로 남는다.

tmp 이름은 **문자열이 바뀌지 않는다**: 최종 경로가 `<key>.json`이므로 `f"{final}.{os.getpid()}.tmp"`는 기존 `f"{request.key}.json.{os.getpid()}.tmp"`와 같은 값이다.

- [ ] **Step 1: tmp 이름을 단언하는 기존 테스트가 있는지 확인한다**

```bash
grep -rn "tmp" tests/test_store_cache.py tests/test_store_provider.py
```

기대: `.tmp` 잔해가 남지 않는다는 단언은 있어도 **이름 문자열을 값으로 고정하는 단언은 없다.** 있으면 그 테스트가 Step 3 이후에도 통과해야 하고, 통과하지 않으면 이름이 실제로 바뀐 것이므로 되돌린다.

- [ ] **Step 2: 실패하는 테스트를 쓴다 — `store`가 쓴 것을 `discard`가 지운다**

`tests/test_store_cache.py` 끝에 추가한다.

```python
def test_store가_쓴_것을_discard가_지운다(tmp_path: Path) -> None:
    # 경로 규칙이 갈라지면 여기서만 드러난다. discard가 엉뚱한 경로를 지우면
    # 지울 것이 없어 `missing_ok=True`가 조용히 성공하기 때문이다.
    request = _request()
    store(tmp_path, request, _completion())
    assert load(tmp_path, request) is not None

    discard(tmp_path, request)

    assert load(tmp_path, request) is None


def test_없는_항목을_지우는_것은_무연산이다(tmp_path: Path) -> None:
    # 폐기는 실패 경로에서 불린다. 거기서 예외를 내면 번역이 죽는다.
    discard(tmp_path, _request())
```

`_request()`·`_completion()`은 이 파일 상단에 **이미 있다**(22·34행). 임포트만 고친다.

```python
from cuesift.store.cache import CacheRequest, discard, load, store
```

- [ ] **Step 3: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -k discard -v
```

기대: `ImportError` 또는 `NameError: name 'discard' is not defined`. **여기서 통과하면 이미 구현돼 있다는 뜻이므로 멈추고 원인을 찾는다.**

- [ ] **Step 4: `cache.py`에 경로 헬퍼와 `discard`를 넣는다**

`load` 정의 **바로 위**에 헬퍼를 둔다.

```python
def _entry_path(cache_dir: Path, request: CacheRequest) -> Path:
    """키에서 파일 경로를 만든다. **이 규칙의 단일 출처다.**

    `load`·`store`·`discard` 셋이 각자 조립하면 한 곳만 바뀔 때 서로가 쓴
    것을 못 읽거나 못 지운다. 특히 폐기는 실패가 조용하다 - 엉뚱한 경로를
    지우면 지울 것이 없어 `unlink(missing_ok=True)`가 성공한다.
    """
    return cache_dir / f"{request.key}.json"
```

`load`의 첫 줄을 바꾼다.

```python
    path = _entry_path(cache_dir, request)
```

`store`의 tmp·최종 경로를 바꾼다. **문자열 값은 그대로다**(`<key>.json.<pid>.tmp`).

```python
    final = _entry_path(cache_dir, request)
    tmp = Path(f"{final}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, final)
```

`store` 아래에 `discard`를 넣는다.

```python
def discard(cache_dir: Path, request: CacheRequest) -> None:
    """이 요청의 캐시 항목을 지운다 (FR-2.7 · 파킹 #13).

    **`store()`와 마찬가지로 실패를 삼키지 않는다** - 호출자가 경고를 낸다.
    여기서 삼키면 "다시 돌리면 재시도된다"가 거짓이 된 것을 아무도 모른다.

    **판정은 이 계층의 일이 아니다.** 무엇이 쓸모없는 응답인지 아는 것은
    `translate/batch.py::parse_translations`이고, 캐시는 그 판정을 재현할
    재료(기대 id)를 갖고 있지 않다. 여기서 흉내 내면 판정이 두 곳으로
    갈라져 한쪽만 고쳐진다.

    없는 항목을 지우는 것은 무연산이다 - 폐기는 실패 경로에서 불리므로
    여기서 `FileNotFoundError`를 내면 번역이 그 자리에서 죽는다.
    """
    _entry_path(cache_dir, request).unlink(missing_ok=True)
```

- [ ] **Step 5: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -v
```

기대: 전부 PASS. 기존 테스트가 하나라도 죽으면 경로 규칙이 실제로 바뀐 것이다.

- [ ] **Step 6: 실패하는 테스트를 쓴다 — `CachingProvider.discard`**

`tests/test_store_provider.py` 끝에 추가한다.

```python
def test_폐기하면_다음_호출이_안쪽을_부른다(tmp_path: Path) -> None:
    # 파킹 #13의 핵심. 폐기가 없으면 2회차가 inner를 부르지 않는다.
    inner = ScriptedProvider(["응답1", "응답2"])
    provider = _cached(inner, tmp_path)

    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    provider.discard(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert len(inner.calls) == 2
    assert second.text == "응답2"


def test_폐기는_다른_항목을_건드리지_않는다(tmp_path: Path) -> None:
    # 키를 무시하고 디렉터리를 비우는 구현이면 여기서 죽는다.
    other = (ChatMessage(role="system", content="지시"), ChatMessage(role="user", content="다른"))
    inner = ScriptedProvider(["A", "B"])
    provider = _cached(inner, tmp_path)
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    provider.complete(other, temperature=0.0, max_tokens=None)

    provider.discard(_MESSAGES, temperature=0.0, max_tokens=None)

    assert provider.complete(other, temperature=0.0, max_tokens=None).text == "B"
    assert len(inner.calls) == 2  # other는 캐시에서 나왔다


def test_저장한_적_없는_것을_폐기해도_죽지_않는다(tmp_path: Path) -> None:
    warnings: list[str] = []
    provider = CachingProvider(
        ScriptedProvider([]), identity="i|u|m", cache_dir=tmp_path, warn=warnings.append
    )

    provider.discard(_MESSAGES, temperature=0.0, max_tokens=None)

    assert warnings == []


def test_폐기_실패는_경고하고_진행한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 지우지 못했다는 것은 "다시 돌려도 같은 실패가 재생된다"는 뜻이다.
    # 조용히 넘어가면 사용자는 재시도가 된 줄 안다. `_load_or_none`의 조용한
    # 미스와 다른 이유는 결과가 다르기 때문이다 - 그쪽은 이번 호출이 느릴
    # 뿐이다.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("권한 없음")

    monkeypatch.setattr("cuesift.store.provider.discard", boom)
    warnings: list[str] = []
    provider = CachingProvider(
        ScriptedProvider(["응답1"]), identity="i|u|m", cache_dir=tmp_path, warn=warnings.append
    )

    provider.discard(_MESSAGES, temperature=0.0, max_tokens=None)
    provider.discard(_MESSAGES, temperature=0.0, max_tokens=None)

    assert len(warnings) == 1  # 경고는 한 번만. 수백 번이면 진짜 출력이 묻힌다


def test_폐기는_호출과_같은_온도의_항목을_지운다(tmp_path: Path) -> None:
    # `discard`가 `_request`를 쓰지 않고 인자를 하나라도 하드코딩하면 여기서
    # 죽는다. 하드코딩된 값과 우연히 같은 0.0만 쓰는 테스트로는 드러나지 않는다.
    inner = ScriptedProvider(["뜨거운1", "뜨거운2"])
    provider = _cached(inner, tmp_path)

    provider.complete(_MESSAGES, temperature=0.7, max_tokens=None)
    provider.discard(_MESSAGES, temperature=0.7, max_tokens=None)
    provider.complete(_MESSAGES, temperature=0.7, max_tokens=None)

    assert len(inner.calls) == 2
```

- [ ] **Step 7: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_provider.py -k 폐기 -v
```

기대: 5건 전부 `AttributeError: 'CachingProvider' object has no attribute 'discard'`.

- [ ] **Step 8: `CachingProvider`에 표면을 넣는다**

임포트를 고친다.

```python
from cuesift.store.cache import CACHE_IO_ERRORS, CacheRequest, discard, load, store
```

`complete()`의 요청 조립을 헬퍼로 뽑는다. **`complete`와 `discard`가 같은 재료로 키를 만드는 것이 계약이므로 조립을 복제하지 않는다.**

```python
    def _request(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> CacheRequest:
        """조회·저장·폐기가 **같은 키**를 보게 하는 단일 출처.

        조립을 복제하면 `complete`가 쓴 것을 `discard`가 못 지운다 - 그리고
        그 어긋남은 조용하다(지울 것이 없으니 성공한다). 필드가 하나 늘 때
        한쪽만 고치는 실수가 구조적으로 불가능해야 한다.
        """
        return CacheRequest(
            identity=self._identity,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=tuple(messages),
            attempt=self._attempt,
        )
```

`complete()` 본문의 `request = CacheRequest(...)` 블록을 아래로 바꾼다.

```python
        request = self._request(messages, temperature=temperature, max_tokens=max_tokens)
```

`close()` 아래(비공개 메서드들 위)에 공개 표면을 넣는다.

```python
    def discard(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> None:
        """호출자가 "이 응답은 쓸모없었다"고 알릴 때 그 항목을 지운다 (파킹 #13).

        **판정은 여기서 하지 않는다.** 이 계층은 응답이 파싱되는지 모른다 -
        아는 것은 engine이고(`translate/batch.py::parse_translations`), 그래서
        폐기는 engine이 시킨다. 캐시가 스스로 판정하려 들면 기대 id를 모르는
        채로 흉내 내게 되고, 판정이 두 곳으로 갈라진다.

        **인자가 `complete()`와 글자 그대로 같아야 한다.** 하나라도 어긋나면
        키가 달라져 **엉뚱한 항목을 지우고**, 지울 것이 없으니
        `unlink(missing_ok=True)`가 조용히 성공한다 - 어긋남이 실패로
        드러나지 않는다. 이것을 지키는 것은 이 주석이 아니라
        `tests/test_store_provider.py::test_폐기하면_다음_호출이_안쪽을_부른다`이고,
        인자를 하나라도 바꾸면 그쪽이 죽는다.

        **`empty_translation`에는 쓰지 않는다** (호출부 참고) - 그것은 개수도
        번호도 맞은, 계약을 지킨 응답이다.
        """
        self._discard_or_warn(
            self._request(messages, temperature=temperature, max_tokens=max_tokens)
        )

    def _discard_or_warn(self, request: CacheRequest) -> None:
        try:
            discard(self._cache_dir, request)
        except CACHE_IO_ERRORS as exc:
            # 지우지 못했다는 것은 **"다시 돌려도 같은 실패가 재생된다"**는
            # 뜻이다. 조용히 넘어가면 사용자는 재시도가 된 줄 안다.
            # `_store_or_warn`과 경고 깃발을 공유하는 것은 둘이 사용자에게
            # 같은 한 가지 사실을 말하기 때문이다 - 캐시가 제대로 동작하지
            # 않는다.
            self._warn_once(f"캐시 항목을 지우지 못했다(재실행이 같은 실패를 낸다): {exc}")
```

- [ ] **Step 9: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_provider.py tests/test_store_cache.py -v
```

기대: 전부 PASS. 기존 건수 + 7건(캐시 2 · 프로바이더 5).

- [ ] **Step 10: 변이를 걸어 게이트가 진짜인지 본다**

각 변이를 넣고 `pytest tests/test_store_provider.py tests/test_store_cache.py`를 돌린 뒤 **되돌린다.** 파일 복사본으로 복원한다(`git checkout --`는 미커밋 작업을 날린다).

| 변이 | 기대 |
| --- | --- |
| `cache.discard` 본문을 `pass`로 | ≥ 2건 사망 |
| `CachingProvider.discard`가 `_request`를 안 쓰고 `temperature=0.0`을 하드코딩 | 1건 사망(`test_폐기는_호출과_같은_온도의_항목을_지운다`) |
| `_discard_or_warn`의 `except`를 지움 | 1건 사망(`test_폐기_실패는_경고하고_진행한다`) |

**표에 적은 숫자는 실제로 관측한 값으로 바꿔 적는다.** 예상치를 남기면 다음 사람이 그것을 믿는다.

- [ ] **Step 11: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift -q
git add src/cuesift/store/cache.py src/cuesift/store/provider.py tests/test_store_cache.py tests/test_store_provider.py
git commit -m "기능: 캐시 항목 폐기 표면(discard)을 연다"
```

---

## Task 2: engine이 파싱 실패 응답을 폐기시킨다

**Files:**

- Modify: `src/cuesift/translate/engine.py` (상수 `_MAX_TOKENS` 신설 · `_discard_cached` 신설 · `_run_window`의 `except InvalidResponseError`(현재 272행) · `_run_single`의 `except InvalidResponseError`(현재 404행) · `_call_with_retry`의 `max_tokens=None`(현재 486행))
- Create: `tests/test_cache_discard.py`

**Interfaces:**

- Consumes: Task 1의 `CachingProvider.discard(messages, *, temperature, max_tokens)`
- Produces: `_MAX_TOKENS: int | None` · `_discard_cached(provider: Provider, messages: Sequence[ChatMessage], *, temperature: float) -> None`

### 호출 수 행렬 — 한 숫자가 셋을 가른다

세그먼트 2개, `batch_size=2`, 같은 `cache_dir`로 2회 실행했을 때 **2회차의 `provider.calls` 길이**다.

| 시나리오 | 1회차 | 2회차 (기대) | 폐기 없음(오늘) | 배치 호출부만 빠짐 | 개별 호출부만 빠짐 |
| --- | --- | --- | --- | --- | --- |
| `EchoProvider(garbage=True)` — 전부 파싱 실패 | 3 | **3** | 0 | 0 | 1 |
| `EchoProvider(fail_batches_of_size=2)` — 배치만 실패, 개별은 성공 | 3 | **1** | 0 | 0 | 1 |
| `EchoProvider(transform=lambda s: "")` — 형식은 맞고 내용이 빔 | 1 | **0** | 0 | 0 | 0 |

1회차가 3인 이유: 배치 1회가 파싱 실패 → 개별 폴백 2회. 세 열의 값이 전부 다르므로 **이 표가 곧 변이 증명이다.** 셋째 줄이 `empty_translation`을 폐기하지 않는다는 범위의 못이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cache_discard.py`를 만든다.

```python
"""파싱 실패 응답을 캐시에 남기지 않는다 (FR-2.7 · FR-2.6 · 파킹 #13).

**engine과 캐시의 교차 계약이라 여기 있다.** 판정은 engine(`parse_translations`)이
하고 저장은 `store/`가 하므로, 어느 한쪽의 단위 테스트만으로는
"engine이 폐기를 시키는가"가 드러나지 않는다.

단언은 전부 **2회차의 실제 호출 수**다. "캐시가 비었다"를 파일로 확인하면
경로 규칙을 테스트가 재구현하게 되고, 그 재구현이 틀려도 통과한다.
"""

from __future__ import annotations

from pathlib import Path

from tests.fakes.provider import EchoProvider

from cuesift.segment.models import Segment
from cuesift.store.provider import CachingProvider
from cuesift.translate.engine import translate_segments


def _segs(n: int) -> list[Segment]:
    return [
        Segment(
            id=f"s{i}", index=i, start_ms=i * 1000, end_ms=i * 1000 + 900, source_text=f"문장{i}"
        )
        for i in range(n)
    ]


def _run(inner: EchoProvider, cache_dir: Path) -> int:
    """한 번 돌리고 **이번 실행에서 안쪽을 몇 번 불렀는지** 낸다."""
    before = len(inner.calls)
    translate_segments(
        _segs(2),
        provider=CachingProvider(inner, identity="i|u|m", cache_dir=cache_dir),
        source_lang="ko",
        target_lang="en",
        batch_size=2,
    )
    return len(inner.calls) - before


def test_파싱_실패_응답은_캐시에_남지_않는다(tmp_path: Path) -> None:
    # 파킹 #13. 오늘은 2회차가 0이다 - 실패가 무료로 영구 재생된다.
    inner = EchoProvider(garbage=True)

    first = _run(inner, tmp_path)
    second = _run(inner, tmp_path)

    assert first == 3  # 배치 1회 + 개별 폴백 2회
    assert second == 3  # 셋 다 폐기됐으므로 그대로 다시 부른다


def test_폴백에서_성공한_것은_캐시에_남는다(tmp_path: Path) -> None:
    # `--no-cache`와 갈리는 지점이다. 실패한 배치만 재호출하고 성공분은
    # 재결제하지 않는다.
    inner = EchoProvider(fail_batches_of_size=2)

    first = _run(inner, tmp_path)
    second = _run(inner, tmp_path)

    assert first == 3
    assert second == 1  # 깨진 배치 호출만 다시. 개별 2건은 캐시 히트


def test_빈_번역은_캐시에서_빼지_않는다(tmp_path: Path) -> None:
    # 범위의 못. 개수도 번호도 맞은 응답은 계약을 지킨 것이고, 폐기하면
    # 같은 배치에서 성공한 나머지까지 다시 결제한다.
    inner = EchoProvider(transform=lambda _s: "")

    first = _run(inner, tmp_path)
    second = _run(inner, tmp_path)

    assert first == 1
    assert second == 0


def test_캐시를_끼우지_않은_프로바이더에서도_동작한다(tmp_path: Path) -> None:
    # `--no-cache`와 raw 프로바이더에는 `discard`가 없다. 없는 것은 오류가
    # 아니라 할 일이 없는 것이다 - 여기서 AttributeError가 나면 번역이 죽는다.
    inner = EchoProvider(garbage=True)

    result = translate_segments(
        _segs(2), provider=inner, source_lang="ko", target_lang="en", batch_size=2
    )

    assert [f.reason for f in result.failures] == ["invalid_response", "invalid_response"]
```

- [ ] **Step 2: 실패를 확인한다 — 이것이 버그 재현이다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cache_discard.py -v
```

기대: `test_파싱_실패_응답은_캐시에_남지_않는다`가 `assert 0 == 3`으로, `test_폴백에서_성공한_것은_캐시에_남는다`가 `assert 0 == 1`으로 FAIL. 나머지 2건은 PASS(오늘도 성립한다).

**두 건이 FAIL하지 않으면 재현이 안 된 것이다.** 그 상태로 구현하면 회귀 테스트가 아니라 장식이 된다.

- [ ] **Step 3: engine에 상수와 헬퍼를 넣는다**

`_MAX_BACKOFF_S` 상수들 아래에 둔다.

```python
# 프로바이더에 넘기는 `max_tokens`다. **상수인 이유는 폐기 때문이다** -
# 호출과 폐기가 서로 다른 값을 쓰면 `CacheRequest.key`가 달라져 엉뚱한 항목을
# 지우고, 지울 것이 없으니 조용히 성공한다. 두 자리에 각각 적으면 한쪽만
# 바뀌는 실수가 가능해진다.
_MAX_TOKENS: int | None = None
```

`_call_with_retry`의 호출을 바꾼다.

```python
            completion = provider.complete(
                messages, temperature=temperature, max_tokens=_MAX_TOKENS
            )
```

`_collect` 아래(또는 `_call_with_retry` 위)에 헬퍼를 둔다.

```python
def _discard_cached(
    provider: Provider,
    messages: Sequence[ChatMessage],
    *,
    temperature: float,
) -> None:
    """파싱조차 되지 않은 응답을 캐시에서 뺀다 (FR-2.7 · 파킹 #13).

    **`getattr`인 이유는 `Provider` 프로토콜에 없는 표면이기 때문이다.**
    캐시를 끼우지 않은 프로바이더(`--no-cache`·raw)에는 없고, 그때는 할 일이
    없는 것이지 오류가 아니다 - 여기서 `AttributeError`가 나면 실패 경로에서
    번역이 통째로 죽는다. `cli.py`의 `getattr(provider, "close", None)`·
    `getattr(provider, "cache_identity", None)`과 같은 관행이다.

    **`empty_translation`에는 걸지 않는다.** 그것은 개수도 번호도 맞은,
    계약을 지킨 응답이다(`_collect` 참고) - 폐기하면 같은 배치에서 성공한
    나머지까지 다시 결제한다. `provider_error`는 애초에 저장되지 않는다
    (예외가 저장 코드에 도달하지 못한다).

    **판정을 여기서 하는 것이 계약이다.** 캐시 계층은 기대 id를 몰라
    `parse_translations`와 같은 판정을 할 수 없다.
    """
    discard = getattr(provider, "discard", None)
    if discard is not None:
        discard(messages, temperature=temperature, max_tokens=_MAX_TOKENS)
```

- [ ] **Step 4: 호출부 두 곳을 배선한다**

`_run_window`의 `except InvalidResponseError:` 첫 줄에 넣는다(폴백을 부르기 **전**이다 — 폴백이 실패해도 깨진 배치 응답은 이미 지워져 있어야 한다).

```python
    except InvalidResponseError:
        _discard_cached(provider, messages, temperature=temperature)
        fallback_usage, texts, fallback_failures = _fallback_individually(
```

`_run_single`의 `except InvalidResponseError:` 첫 줄에 넣는다.

```python
    except InvalidResponseError:
        _discard_cached(provider, messages, temperature=temperature)
        return (
            usage,
            {},
            [SegmentFailure(segment_id=segment.id, reason="invalid_response", attempts=attempts)],
        )
```

- [ ] **Step 5: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cache_discard.py -v
```

기대: 4건 전부 PASS.

- [ ] **Step 6: 변이로 행렬을 증명한다**

각 변이 후 `pytest tests/test_cache_discard.py`를 돌리고 **되돌린다.**

| 변이 | 기대 사망 |
| --- | --- |
| `_discard_cached` 본문을 `pass`로 | 2건 |
| `_run_window`의 호출만 제거 | 2건 |
| `_run_single`의 호출만 제거 | 1건 (`test_파싱_실패_응답은…`) |
| `_collect`의 `empty_translation` 자리에 `_discard_cached` 추가 | 1건 (`test_빈_번역은…`) — **범위의 못이 진짜인지 본다** |
| `_MAX_TOKENS`를 `_discard_cached`에서만 `16`으로 | 2건 |

**관측값을 표에 적어 넣는다.** 마지막 두 줄이 0건이면 게이트가 없는 것이므로 테스트를 보강한다.

- [ ] **Step 7: 전체 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift
git add src/cuesift/translate/engine.py tests/test_cache_discard.py
git commit -m "수정: 파싱 실패 응답을 캐시에서 폐기한다"
```

기대: `pytest`가 착수 기준선 1571보다 **11건 늘어난 1582**(Task 1의 7건 + Task 2의 4건). 다르면 어느 테스트가 사라졌는지 찾는다.

---

## Task 3: 문서 — 근거가 부분적으로 거짓이 된다

**Files:**

- Modify: `src/cuesift/cli.py:130-135`(종료 코드 3의 근거 주석)
- Modify: `README.md`(§종료 코드 근거 ~261행 · §`cuesift translate` ~298행)
- Modify: `src/cuesift/store/provider.py`(모듈 독스트링 끝)
- Modify: `CHANGELOG.md`(`### Fixed` 절 끝, 현재 ~277행)
- Modify: `HANDOFF.md`(파킹 표 · 재현 절)

### 이 태스크가 없으면 저장소가 거짓말을 한다

종료 코드 `3`을 고른 근거가 **바로 이 결함이었다.** `cli.py`와 `README.md`가 같은 실측을 인용한다:

> 후보였던 `EX_TEMPFAIL`(75)은 "다시 시도하면 된다"를 뜻하는데 **실측으로 거짓이다** — 재실행하면 `캐시 히트 3 · 실제 호출 0 · 실패 2건`이 그대로 나온다.

이 문장은 `invalid_response`에 대해 **더 이상 참이 아니다.** 그렇다고 종료 코드가 바뀌지는 않는다 — 근거가 하나 줄어들 뿐이고, 75를 거부하는 이유는 남는다. 새 근거를 아래에 적는다.

- [ ] **Step 1: `cli.py`의 근거 주석을 고친다**

`EXIT_TRANSLATION_FAILURE`의 "`sysexits.h`를 쓰지 않는다" 문단을 통째로 교체한다.

```python
# **`sysexits.h`를 쓰지 않는다.** 거기에는 "산출물은 나왔는데 일부가 비었다"에
# 해당하는 값이 없다. 후보였던 EX_TEMPFAIL(75)은 "다시 시도하면 된다"를 뜻하는데
# 이 명령은 그것을 알 수 없다 - 사유 3종의 처방이 서로 다르기 때문이다
# (`provider_error`는 재실행, `invalid_response`는 모델 교체,
# `empty_translation`은 원문 확인). 하나의 코드가 그중 "재시도"만 주장하면
# 나머지 둘에서 거짓이다.
#
# **그리고 재시도가 실제로 아무것도 바꾸지 않는 사유가 남아 있다** -
# `empty_translation`은 개수도 번호도 맞은 계약을 지킨 응답이라 캐시가 보존하고
# (`store/provider.py`의 `discard` 참고: 폐기 대상은 `invalid_response`뿐이다),
# 같은 모델·같은 설정의 재실행은 캐시 히트로 같은 결과를 낸다. 75였다면 CI가
# 재시도로 읽어 아무것도 달라지지 않는 루프가 된다.
#
# **`invalid_response`에 대해서는 2026-08-29에 사정이 바뀌었다** - 파싱조차 안 된
# 응답은 이제 캐시에서 폐기되므로 재실행이 실제로 다시 호출한다. 그래도 75로
# 바꾸지 않는 이유는 위와 같다: 같은 모델이 같은 지시를 다시 어길 것이라는 쪽에
# 걸 근거가 없고, 코드 하나가 세 사유를 대표할 수 없다.
```

- [ ] **Step 2: `README.md` 두 곳을 고친다**

§종료 코드(현재 261행 부근)의 문단을 교체한다.

```markdown
**`3`만 `sysexits.h` 밖의 값입니다.** 거기에는 "산출물은 나왔는데 일부가 비었다"에 해당하는
값이 없습니다. 뜻이 가장 가까운 `75`(`EX_TEMPFAIL`, "다시 시도하라")를 쓰지 않은 것은
**사유 3종의 처방이 서로 다르기 때문입니다** — `provider_error`는 재실행, `invalid_response`는
모델 교체, `empty_translation`은 원문 확인입니다. 코드 하나가 그중 "재시도"만 주장하면
나머지 둘에서 거짓이 됩니다. 실제로 `empty_translation`은 계약을 지킨 응답이라 캐시가
보존하고, 같은 설정의 재실행은 캐시 히트로 같은 결과를 냅니다.
이 코드는 "번역되지 않은 세그먼트가 남았다"만 말하고 **재시도 여부는 주장하지 않습니다** —
그 판단의 근거인 사유는 화면의 실패 세그먼트 줄에 나갑니다.
```

§`cuesift translate`의 "형식을 어긴 응답도 캐시됩니다" 문단을 교체한다.

```markdown
**형식을 어긴 응답은 캐시에 남지 않습니다.** 파싱조차 되지 않은 응답(`invalid_response`)은
지워지므로, 같은 명령을 다시 치면 **그 배치만 실제로 다시 호출됩니다.** 성공한 호출과
같은 배치에서 폴백으로 건진 번역은 그대로 남아 재결제하지 않습니다.

**빈 번역(`empty_translation`)은 캐시에 남습니다.** 개수도 번호도 맞은, 계약을 지킨
응답이기 때문입니다 — 폐기하면 같은 배치에서 성공한 나머지까지 다시 결제합니다. 이쪽은
모델을 바꾸거나 `--no-cache`를 쓰거나 `.cuesift/cache/`를 지웁니다.
```

- [ ] **Step 3: `store/provider.py` 모듈 독스트링에 한 문단을 더한다**

"**예외를 캐시하지 않는 것은 구조적으로 보장된다**" 문단 **바로 뒤**에 넣는다. 두 보장의 강도가 다르다는 것이 요점이다.

```python
"""
**쓸모없는 응답을 캐시하지 않는 것은 구조가 아니라 호출자가 한다.** 위의
보장(예외)과 강도가 다르다 - 예외는 저장 코드에 도달하지 못하지만, 파싱조차
안 되는 응답은 이 계층에서 정상 `Completion`이라 그냥 저장된다. 그것을 아는
것은 engine뿐이므로(`translate/batch.py::parse_translations`) engine이
`discard()`로 지운다. 그래서 이 규칙은 호출부가 빠뜨리면 깨진다 - 그때의
동작은 "옛날 동작"(쓸모없는 응답이 캐시에 남음)이고, 그것을 지키는 것은
`tests/test_cache_discard.py`다.
"""
```

- [ ] **Step 4: `CHANGELOG.md`의 `### Fixed` 절 끝에 항목을 더한다**

```markdown
- **파싱 실패 응답을 캐시에서 폐기한다** (FR-2.7 · 파킹 #13). 형식을 어겨 파싱조차 되지 않은 응답(`invalid_response`)이 캐시에 보존돼 **재실행이 실제 호출 0개로 같은 실패를 영구 반복**하던 것을 고쳤다. `store/cache.py`에 `discard()`가, `store/provider.py`에 `complete()`와 대칭인 `CachingProvider.discard()`가 생겼고, 판정을 아는 `translate/engine.py`가 `InvalidResponseError` 분기 두 곳에서 그것을 시킨다. **판정은 캐시로 내리지 않았다** — 캐시 계층은 기대 id를 몰라 `parse_translations`와 같은 판정을 할 수 없고, 흉내 내면 판정이 두 곳으로 갈라진다. **`empty_translation`은 의도적으로 남긴다**: 개수도 번호도 맞은 계약을 지킨 응답이라, 폐기하면 같은 배치에서 성공한 나머지까지 다시 결제한다. 착수 조사가 파킹 노트의 한 줄을 정정했다 — "모델을 바꿔도 재생된다"는 **거짓**이고(`identity = base_url|model`이 캐시 키에 들어간다), 실제 피해는 "같은 설정으로 다시 돌린다"는 가장 흔한 복구 수단이 무력화되는 것이었다. 종료 코드 `3`의 근거 문서(`cli.py` 머리말·`README.md`)도 함께 고쳤다 — 75를 거부한 근거 중 하나가 이 수정으로 사라졌기 때문이다(코드 값은 바뀌지 않는다)
```

- [ ] **Step 5: `HANDOFF.md`를 고친다**

세 곳이다.

**①** "파킹된 finding" 표에서 **#13 행을 지운다.** 표 위 문장을 "**#1·#2·#3이 닫혔다**"에서 "**#1·#2·#3·#13이 닫혔다**"로 고친다.
**②** "### #13 재현" 절을 **폐기 후의 사실로 교체한다.**

```markdown
### #13은 닫혔다 — 그리고 노트 한 줄이 거짓이었다

`invalid_response` 응답은 이제 캐시에서 폐기된다. 재실행하면 그 배치만 실제로 다시
호출되고, 같은 배치에서 폴백으로 건진 번역은 캐시에 남아 재결제하지 않는다.

**노트가 "모델을 바꿔도 재생된다"고 적은 것은 거짓이었다** — 캐시 키에 `identity`가
들어가고 `identity = base_url|model`이라(`translate/openai_compat.py:129`) 모델 교체는
이미 캐시를 우회했다. 실제 피해는 좁았다: *같은 모델·같은 설정으로 다시 돌릴 때*,
즉 "그냥 한 번 더 돌려본다"가 무력화되는 것.

**`empty_translation`은 남긴다.** 개수도 번호도 맞은 계약을 지킨 응답이라, 폐기하면
같은 배치에서 성공한 나머지까지 다시 결제한다. 이 범위를 지키는 것이
`tests/test_cache_discard.py::test_빈_번역은_캐시에서_빼지_않는다`다.
```

**③** "다음 세션 시작 절차" 끝 문단에서 다음 작업 지목을 고친다 — "**다음 작업은 파킹 #13이 1순위다**"를 "**다음 작업은 FR-8.3(WP6의 마지막 조각, STT 어댑터가 선행)이다**"로 바꾼다. 파킹 2번(권장 모델의 대량 실패)이 여전히 열려 있다는 사실은 남긴다.

- [ ] **Step 6: 문서 게이트를 돌린다 — 두 도구의 파일 수가 같아야 한다**

```bash
git add -A
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

기대: 링크 체커 **마크다운 39개**(계획서 1개 추가) · 깨진 링크 0 · markdownlint **39 files** · 0 issues. **두 수가 어긋나면 새 `.md`가 아직 `git add`되지 않은 것이고, 그 문서는 링크 검사를 아예 받지 않는다.**

- [ ] **Step 7: 전체 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift
git add -A
git commit -m "문서: 파킹 #13을 닫고 종료 코드 3의 근거를 고친다"
```

---

## 완료 기준

| 게이트 | 기대값 |
| --- | --- |
| `python -m compileall src tests` | 통과 |
| `ruff check .` | All checks passed! |
| `ruff format --check .` | **115 files**(신규 테스트 1개로 114 → 115) |
| `pytest --cov=cuesift` | **1582 passed · 3 deselected**(착수 1571 + 11) · 커버리지 99% 유지 |
| `scripts/check_links.py` | 마크다운 **39개** · 깨진 링크 0 |
| `npx markdownlint-cli2` | **39 files** · 0 issues · 링크 체커와 **같은 수** |
| CI 기대값 | **1581 passed · 1 skipped · 3 deselected** |

### 런타임 스모크 — 실물로 재현이 닫혔는지 본다

리포 **밖**에 스텁 서버를 두고(리포 안에 두면 게이트를 오염시킨다) 잡문을 돌려주게 한 뒤:

```powershell
.venv\Scripts\python.exe -m cuesift translate tests\fixtures\ingest\ten_cues.srt --to en --out $env:TEMP\cs1 --base-url http://127.0.0.1:8765/v1 --model stub --cache-dir $env:TEMP\cs-cache
# → exit 3 · invalid_response
# 서버를 죽이지 말고 같은 --cache-dir로 재실행
# → 화면의 "실제 호출"이 0이 아니어야 한다. 이것이 파킹 #13의 재현이 닫힌 증거다
```

**서버를 죽이고 재실행하면** 이제 `provider_error`가 나온다(캐시가 비었으므로 실제로 호출하고 실패한다) — 폐기 전에는 `invalid_response`가 캐시에서 그대로 재생됐다. 이 차이가 육안으로 확인할 수 있는 가장 짧은 증거다.

## 하지 않는 것

| 항목 | 왜 |
| --- | --- |
| `empty_translation` 폐기 | 계약을 지킨 응답이다. 폐기하면 같은 배치의 성공분을 재결제한다 |
| 종료 코드 값 변경 | 근거 하나가 줄었을 뿐 결론은 그대로다(Task 3 Step 1 참고). 값을 바꾸면 파괴적 변경이 두 번 연속 난다 |
| `--refresh-failed` 같은 새 옵션 | 자동으로 옳게 동작하면 옵션이 필요 없다. CLI 옵션 수는 24개 그대로다 |
| 캐시 계층의 자체 검증 | 기대 id를 모른다. 흉내 내면 판정이 두 곳으로 갈라진다 |
| `Provider` 프로토콜에 `discard` 추가 | `Protocol`은 런타임 검사를 하지 않아 서드파티가 빠뜨려도 조용히 통과한다. `getattr`이 그 사실을 코드에 드러낸다 |
