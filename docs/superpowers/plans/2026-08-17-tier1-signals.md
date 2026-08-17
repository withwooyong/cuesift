# WP8a Tier 1 신호 라이브러리 계층 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tier 0가 원리상 못 보는 것을 보기 위한 LLM 기반 신호 계층을 세우고,
자가일관성(FR-4.1) 신호 1종과 적용 상한(FR-4.3)을 라이브러리로 낸다.

**Architecture:** 신호 레지스트리는 공유하되 **실행 경로를 tier로 가른다** —
`collect_all()`은 tier 0만, `collect_tier1()`은 tier 1만 돈다. Tier 0 수집기가
받는 `SignalContext`에는 프로바이더가 없어 **LLM에 닿을 수 없다는 것이 타입으로
보장**된다. 파이프라인은 2라운드다: Tier 0 융합 → 예산 적용 → 컷라인 아래
회색지대에서 후보 선별 → 후보에만 LLM 호출 → 재융합 → 예산 재적용.

**Tech Stack:** Python 3.11+ · 표준 라이브러리(`difflib`·`unicodedata`·`math`) ·
기존 `translate/`(`translate_segments`·`Provider`) · 기존 `store/`(`CachingProvider`) ·
pytest

**Spec:** [`docs/superpowers/specs/2026-08-17-tier1-signals-design.md`](../specs/2026-08-17-tier1-signals-design.md)

## Global Constraints

이 절의 값은 모든 태스크의 요구사항에 암묵적으로 포함된다.

| 제약 | 값 |
| --- | --- |
| Python 실행 | **반드시 `.venv/Scripts/python.exe`** — 시스템 Python은 3.14라 다르다 |
| CI 인터프리터 | 3.11 · 3.12 (로컬 venv는 3.14 — **다른 버전에서 테스트한다**) |
| 런타임 의존성 | `typer` · `pysubs2` · `pyyaml` · `httpx` **4개 고정. 추가 금지** |
| dev 의존성 | `pytest` · `pytest-cov` · `ruff` **3개 고정. 추가 금지** |
| 모듈 첫 줄 | `from __future__ import annotations` |
| 독스트링·주석 | **한국어.** 근거 FR·§ 번호를 병기한다 (예: `FR-4.1`, `설계 §6.2`) |
| 주석 내용 | "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"** |
| ruff | `line-length = 100` · 규칙 `E,F,I,UP,B,SIM` |
| 커밋 메시지 | **한국어** |
| 푸시 | **하지 않는다.** 사용자가 명시적으로 요청할 때만 |

**게이트는 CI와 같은 명령·같은 대상으로 돌린다. `src tests`로 좁히지 않는다.**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
python scripts/check_links.py
npx --yes markdownlint-cli2
```

**수치를 읽는다.** `pytest`의 수집 개수, markdownlint의 `Linting: N files`,
링크 체커의 상대 링크 개수를 매번 확인한다 — 0개 수집은 통과가 아니라 설정
오류다. 착수 시점 기준값: **978 passed, 2 deselected** · 커버리지 99% ·
마크다운 25개 파일 · markdownlint 25 files.

> **`check_links.py`는 `git ls-files`만 본다.** 새 마크다운 파일은 `git add`
> 전까지 검사 대상에서 빠지고, 그 상태로 "깨진 링크 없음"이 나온다. 문서를
> 새로 만들면 **`git add` 후에 게이트를 돌린다.**

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `src/cuesift/signals/similarity.py` | 문자 단위 유사도 하나. 교체 지점을 한 곳으로 모은다 | 1 |
| `src/cuesift/signals/base.py` | 프로토콜·레지스트리·실행 경로 분리 | 2 |
| `src/cuesift/store/cache.py` | 캐시 키에 시도 번호 | 3 |
| `src/cuesift/store/provider.py` | `CachingProvider`가 시도를 고정 | 3 |
| `src/cuesift/triage/policy.py` | 후보 선별(순수 함수) | 4 |
| `src/cuesift/signals/llm.py` | 자가일관성 수집기 | 5 |
| `src/cuesift/tier1.py` | 2라운드 오케스트레이션·프로바이더 팩토리 | 6 |
| `src/cuesift/risk/fuse.py` | 가중치 표에 신호 등록 | 5 |

## 태스크 의존 관계

```text
Task 1 (similarity)  ─┐
Task 2 (tier 격리)   ─┼─→ Task 5 (자가일관성) ─┐
Task 3 (캐시 attempt)─┤                        ├─→ Task 6 (오케스트레이션) → Task 7 (live·문서)
Task 4 (후보 선별)   ─┘────────────────────────┘
```

**Task 1~4는 서로 독립이다.** 병렬로 진행할 수 있다.

---

### Task 1: 문자 단위 유사도

**Files:**

- Create: `src/cuesift/signals/similarity.py`
- Test: `tests/test_similarity.py`

**Interfaces:**

- Consumes: 없음 (표준 라이브러리만)
- Produces: `similarity(a: str, b: str) -> float` — 0.0~1.0

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_similarity.py`:

```python
"""문자 단위 유사도 (설계 §9 · §3.2)."""

from __future__ import annotations

from cuesift.signals.similarity import similarity

# 설계 §3.2의 실측 7쌍.
#
# **정확한 소수점이 아니라 순서 관계를 검사한다.** difflib 구현이 바뀌면
# 소수점은 흔들리지만 "negation과 paraphrase가 분리되지 않는다"는 주장은
# 그대로여야 한다. 그 주장이 §12 Q4가 열려 있는 근거다.
NEGATION = [
    ("I cannot agree with you", "I can agree with you"),
    ("それはできません", "それはできます"),
    ("He did not come to the party", "He came to the party"),
    ("彼は来なかった", "彼は来た"),
]
PARAPHRASE = [
    ("彼は来なかった", "彼は現れなかった"),
    ("He did not come to the party", "He didn't show up at the party"),
]
UNRELATED = [
    ("He did not come to the party", "The weather is nice today"),
]


def test_같은_문자열은_1이다():
    assert similarity("안녕하세요", "안녕하세요") == 1.0


def test_빈_문자열_쌍은_1이다():
    assert similarity("", "") == 1.0


def test_한쪽만_비면_0이다():
    assert similarity("", "안녕") == 0.0
    assert similarity("안녕", "") == 0.0


def test_전각과_반각을_같게_본다():
    """`struct.number_missing`의 전각 숫자 미탐과 같은 부류를 막는다."""
    assert similarity("１２３", "123") == 1.0


def test_무관한_문장이_가장_낮다():
    lowest_related = min(similarity(a, b) for a, b in NEGATION + PARAPHRASE)
    for a, b in UNRELATED:
        assert similarity(a, b) < lowest_related


def test_negation과_paraphrase가_분리되지_않는다():
    """설계 §3.2 — **이 테스트가 실패하면 Q4가 닫힌 것이다.**

    문자 단위 유사도로 의미 반전과 정상 변이가 갈린다면 임계값 하나로 두
    집단이 나뉜다. 착수 시점 실측은 갈리지 않음을 보였고(negation 0.727~0.930,
    paraphrase 0.759~0.800), 유사도 구현을 바꿀 때 이 테스트가 다시 물어본다.

    실패하면 지우지 말고 요구사항정의서 §12 Q4를 갱신할 것.
    """
    neg = [similarity(a, b) for a, b in NEGATION]
    para = [similarity(a, b) for a, b in PARAPHRASE]
    # 두 집단의 범위가 겹친다 = 어떤 임계값으로도 분리 불가.
    assert min(neg) < max(para)
    assert min(para) < max(neg)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_similarity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.signals.similarity'`

- [ ] **Step 3: 최소 구현**

`src/cuesift/signals/similarity.py`:

```python
"""문자 단위 유사도 (설계 §9 · FR-4.1).

**의미가 아니라 형태를 잰다.** 의미 반전과 정상 변이를 분리하지 못한다
(설계 §3.2의 실측 7쌍). 요구사항정의서 §12 Q4가 열려 있는 이유이며,
교체할 때는 이 함수 하나만 갈아 끼우면 된다.
"""

from __future__ import annotations

import difflib
import unicodedata


def similarity(a: str, b: str) -> float:
    """문자 단위 유사도 0.0~1.0 (FR-4.1).

    **단어로 나누지 않는 이유는 ja에 공백이 없기 때문이다.** 단어 경계
    분할이 CJK를 전부 깨뜨린 전례가 이 저장소에 있다.

    NFKC로 정규화하지 않으면 전각·반각이 다른 문자가 되어, 같은 번역이
    표기 폭 하나로 "흔들렸다"고 판정된다 - `struct.number_missing`의 전각
    숫자 미탐과 같은 부류다.

    `autojunk=False`가 아니면 difflib이 200자 이상 입력에서 빈출 요소를
    junk로 취급해 유사도를 실제보다 낮게 낸다. 자막 한 줄은 짧지만
    `detail`에 담기는 문자열은 길어질 수 있다.

    엄밀히는 편집거리(Levenshtein)가 아니라 Ratcliff-Obershelp다. 직접
    구현하지 않는 것은 표준 라이브러리에 검증된 것이 있는데 새로 쓰면
    버그 위험만 늘기 때문이다.
    """
    na = unicodedata.normalize("NFKC", a)
    nb = unicodedata.normalize("NFKC", b)
    if na == nb:
        # 빈 문자열 쌍도 여기서 1.0이 된다 - 둘 다 "같다"가 맞다.
        return 1.0
    if not na or not nb:
        # 한쪽만 비면 공통 부분이 없다. difflib도 0.0을 내지만 명시하는
        # 편이 경계 조건을 코드에 남긴다.
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_similarity.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 게이트와 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/signals/similarity.py tests/test_similarity.py
git commit -m "기능: 문자 단위 유사도 — Tier 1 신호의 측정 수단 (FR-4.1)"
```

---

### Task 2: Tier 1 실행 격리

**Files:**

- Modify: `src/cuesift/signals/base.py`
- Test: `tests/test_signals_tier_isolation.py`

**Interfaces:**

- Consumes: 기존 `SignalContext` · `Segment` · `Signal` · `_REGISTRY` · `register()`
- Produces:
  - `Tier1Context(signal: SignalContext, provider_for: Callable[[int], Provider], samples: int, temperature: float)`
  - `Tier1Collector` 프로토콜 — `collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None`
  - `collect_tier1(segments: Sequence[Segment], ctx: Tier1Context, enabled: Iterable[str] | None = None) -> dict[str, list[Signal]]`
  - `collect_all`은 시그니처 불변, 동작만 tier 0으로 제한

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_signals_tier_isolation.py`:

```python
"""Tier 0/1 실행 경로 분리 (설계 §4.1 · 요구사항정의서 §4)."""

from __future__ import annotations

import pytest

from cuesift.segment import Segment, Signal
from cuesift.signals.base import (
    SignalContext,
    Tier1Context,
    collect_all,
    collect_tier1,
    register,
    registry,
)
from cuesift.spec import load_builtin
from tests.fakes.provider import ScriptedProvider


class _SpyTier1:
    """어느 경로로 불렸는지 세는 tier 1 수집기."""

    name = "test.tier1_spy"
    tier = 1

    def __init__(self) -> None:
        self.tier0_calls = 0
        self.tier1_calls = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        """**일부러 구현해 둔다.**

        없으면 `collect_all`이 tier를 안 볼 때 `AttributeError`로 죽는데,
        그러면 "누가 불렀는가"가 "왜 죽었는가"에 가려진다. 세는 편이
        변이의 정체를 정확히 드러낸다.
        """
        self.tier0_calls += 1
        return None

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        self.tier1_calls += 1
        # 실제 수집기와 같은 자리에서 프로바이더를 만진다.
        ctx.provider_for(0).complete([], temperature=1.0, max_tokens=None)
        return Signal(name=self.name, tier=1, score=0.5)


@pytest.fixture
def spy_registered():
    """레지스트리를 저장·복원한다. 전역이라 오염되면 다른 테스트가 깨진다."""
    saved = dict(registry())
    collector = _SpyTier1()
    register(collector)
    yield collector
    registry().clear()
    registry().update(saved)


@pytest.fixture
def signal_ctx() -> SignalContext:
    # 기존 신호 테스트와 같은 방식이다 (tests/test_signals_derived.py).
    # conftest.py에는 SpecProfile fixture가 없다 - 각 파일이 load_builtin을
    # 직접 부른다.
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def _segments() -> list[Segment]:
    return [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="안녕", target_text="Hi"),
        Segment(id="2", index=1, start_ms=1000, end_ms=2000, source_text="잘가", target_text="Bye"),
    ]


def test_collect_all은_tier1을_부르지_않는다(spy_registered, signal_ctx):
    """**이 작업의 최우선 게이트다.**

    Tier 1이 collect_all에서 실행되면 전량 LLM 호출이 일어난다 -
    요구사항정의서 §4가 "16부작 × 20개 언어에서 3배는 감당 불가"라고
    적은 바로 그 사고다.
    """
    collect_all(_segments(), signal_ctx)
    assert spy_registered.tier0_calls == 0
    assert spy_registered.tier1_calls == 0


def test_collect_all은_enabled에_tier1을_넣으면_거부한다(spy_registered, signal_ctx):
    """조용히 건너뛰지 않는다. 말없이 빠지면 ablation에서 '기여도 0'으로 읽힌다."""
    with pytest.raises(ValueError, match="tier 0만"):
        collect_all(_segments(), signal_ctx, enabled=["test.tier1_spy"])


def test_collect_tier1은_tier1만_부른다(spy_registered, signal_ctx):
    provider = ScriptedProvider(["a", "b"])
    t1 = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: provider,
        samples=3,
        temperature=1.0,
    )
    result = collect_tier1(_segments()[:1], t1)
    assert len(provider.calls) == 1
    assert [s.name for s in result["1"]] == ["test.tier1_spy"]


def test_collect_tier1은_넘긴_세그먼트에만_돈다(spy_registered, signal_ctx):
    """상한은 select_tier1_candidates의 일이다. 여기서 또 자르지 않는다."""
    provider = ScriptedProvider(["a"])
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: provider, samples=3, temperature=1.0
    )
    result = collect_tier1(_segments()[:1], t1)
    assert set(result) == {"1"}


def test_temperature가_0이면_거부한다(signal_ctx):
    """0이면 재번역이 전부 동일해 점수가 항상 0.0이 된다 - 신호가 죽었는데
    '안전'으로 보고된다(Q3 무음 열화 금지)."""
    with pytest.raises(ValueError, match="temperature"):
        Tier1Context(
            signal=signal_ctx, provider_for=lambda attempt: None, samples=3, temperature=0.0
        )


def test_samples가_2_미만이면_거부한다(signal_ctx):
    with pytest.raises(ValueError, match="samples"):
        Tier1Context(
            signal=signal_ctx, provider_for=lambda attempt: None, samples=1, temperature=1.0
        )
```

`profile` fixture가 기존 `tests/conftest.py`에 없으면 이 파일 안에 만든다 —
기존 테스트가 `SpecProfile`을 어떻게 얻는지 `tests/test_signals_derived.py`에서
확인하고 같은 방식을 쓴다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_tier_isolation.py -v`
Expected: FAIL — `ImportError: cannot import name 'Tier1Context'`

- [ ] **Step 3: 최소 구현**

`src/cuesift/signals/base.py` 상단 import에 추가:

```python
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # 런타임 import를 피한다. `from __future__ import annotations`가 있어
    # 애노테이션이 문자열이므로 실행에 필요 없고, signals -> translate 방향
    # 의존을 실제로 만들지 않는다.
    from cuesift.translate.provider import Provider
```

`SegmentCollector`/`BatchCollector` 정의 아래에 추가:

```python
@dataclass(frozen=True, slots=True)
class Tier1Context:
    """Tier 1 수집기가 LLM을 부르는 데 필요한 것 (설계 §4.1).

    `SignalContext`를 상속하지 않고 **담는다** - 상속하면 Tier 0 수집기가
    `Tier1Context`를 받아도 타입 검사를 통과해, 이 분리가 노리는 격리가
    사라진다.

    **프로바이더를 직접 담지 않고 팩토리로 받는다.** 자가일관성은 시도마다
    다른 `attempt`로 캐시를 갈라야 하는데(설계 §8), 프로바이더를 그대로
    담으면 수집기가 `identity`·`cache_dir`을 알아야 한다 - 신호 수집기가
    캐시 구조에 결합된다.
    """

    signal: SignalContext
    provider_for: Callable[[int], Provider]
    samples: int
    temperature: float

    def __post_init__(self) -> None:
        # 0이면 재번역이 전부 동일해 자가일관성 점수가 **항상 0.0**이 된다.
        # 신호가 죽었는데 "안전"으로 보고되는 무음 열화다(Q3).
        if not self.temperature > 0.0:
            raise ValueError(f"temperature는 0보다 커야 한다 (받은 값: {self.temperature})")
        # 2개 미만이면 비교할 쌍이 만들어지지 않는다.
        if self.samples < 2:
            raise ValueError(f"samples는 2 이상이어야 한다 (받은 값: {self.samples})")


@runtime_checkable
class Tier1Collector(Protocol):
    """LLM을 불러 판정하는 수집기. **후보 세그먼트에만** 실행된다."""

    name: str
    tier: int

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        """신호를 내거나, 해당 없으면 None을 낸다.

        `SegmentCollector.collect`와 같은 계약이다 - None과 score=0.0은
        다르다.
        """
        ...
```

`_REGISTRY` 타입을 넓힌다:

```python
_Collector = SegmentCollector | BatchCollector | Tier1Collector

_REGISTRY: dict[str, _Collector] = {}


def registry() -> dict[str, _Collector]:
    """등록된 수집기 사전. 테스트가 저장·복원할 수 있도록 노출한다."""
    return _REGISTRY


def register(collector: _Collector) -> None:
    """수집기를 등록한다."""
    if collector.name in _REGISTRY:
        # 조용히 덮어쓰면 앞선 신호가 사라지고, 그 신호가 잡던 오류가
        # 리포트에서 통째로 빠진다. 원인을 역추적하기 매우 어렵다.
        raise ValueError(f"신호 이름이 중복됐다: {collector.name}")
    _REGISTRY[collector.name] = collector
```

`collect_all`의 이름 선택부를 바꾼다 (기존 `if enabled is None:` 블록 교체):

```python
    if enabled is None:
        # **tier 0만 돈다.** Tier 1이 여기서 실행되면 전량 LLM 호출이
        # 일어난다 - 요구사항정의서 §4가 "감당 불가"라고 적은 사고다.
        names = [n for n, c in _REGISTRY.items() if c.tier == 0]
    else:
        names = list(enabled)
        # 오타로 신호를 껐는데 "기여도 0"으로 읽히면 잘못된 결론이 나온다.
        unknown = [n for n in names if n not in _REGISTRY]
        if unknown:
            raise ValueError(f"등록되지 않은 신호: {', '.join(sorted(unknown))}")
        # 조용히 건너뛰지 않는 것도 같은 이유다. tier 1 이름을 넣었는데
        # 말없이 빠지면 ablation이 그 신호를 "기여 0"으로 집계한다.
        higher = [n for n in names if _REGISTRY[n].tier != 0]
        if higher:
            raise ValueError(
                f"collect_all은 tier 0만 실행한다. collect_tier1을 쓸 것: "
                f"{', '.join(sorted(higher))}"
            )
```

파일 끝에 `collect_tier1`을 추가한다:

```python
def collect_tier1(
    segments: Sequence[Segment],
    ctx: Tier1Context,
    enabled: Iterable[str] | None = None,
) -> dict[str, list[Signal]]:
    """tier 1 수집기를 **주어진 세그먼트에만** 돌린다 (FR-4.1 · 설계 §4.1).

    **호출자가 후보를 이미 좁혀서 넘긴다.** 이 함수는 상한(FR-4.3)을
    강제하지 않는다 - 상한은 `select_tier1_candidates`의 일이고, 두 곳이
    같은 정책을 나눠 가지면 어긋난다.
    """
    if enabled is None:
        names = [n for n, c in _REGISTRY.items() if c.tier == 1]
    else:
        names = list(enabled)
        unknown = [n for n in names if n not in _REGISTRY]
        if unknown:
            raise ValueError(f"등록되지 않은 신호: {', '.join(sorted(unknown))}")
        others = [n for n in names if _REGISTRY[n].tier != 1]
        if others:
            raise ValueError(
                f"collect_tier1은 tier 1만 실행한다: {', '.join(sorted(others))}"
            )

    # 신호가 하나도 없는 세그먼트도 키를 갖는다. 빠진 키는 KeyError를 부른다.
    result: dict[str, list[Signal]] = {seg.id: [] for seg in segments}

    for name in names:
        collector = _REGISTRY[name]
        for seg in segments:
            signal = collector.collect_tier1(seg, ctx)
            if signal is not None:
                result[seg.id].append(signal)

    return result
```

`signals/__init__.py`가 공개 표면을 정의하고 있으면 `Tier1Context`·
`Tier1Collector`·`collect_tier1`을 함께 내보낸다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_tier_isolation.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 게이트를 실패시켜 본다 (필수)**

**게이트를 만들면 반드시 실패시켜 봐야 한다.** 이 저장소가 여러 번 발동시킨
규율이며, 길이비 회귀 테스트가 버그 버전에서도 통과해 데이터를 다시 짠
전례가 있다.

`collect_all`의 tier 필터를 임시로 되돌린다:

```python
    if enabled is None:
        names = list(_REGISTRY)      # 변이: tier 필터 제거
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_tier_isolation.py -v`
Expected: **FAIL** — `test_collect_all은_tier1을_부르지_않는다`가 죽어야 한다.

죽는 것을 확인한 뒤 **되돌린다.** `git diff`로 원상태를 확인한다.

- [ ] **Step 6: 전체 테스트와 커밋**

기존 테스트가 `collect_all`의 동작 변경에 영향받는지 본다.

Run: `.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing`
Expected: 기존 978건 + 신규 6건이 모두 통과. **수집 개수를 읽는다.**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/signals/base.py tests/test_signals_tier_isolation.py
git commit -m "기능: Tier 1 실행 격리 — collect_all은 tier 0만 돈다 (설계 §4.1)"
```

---

### Task 3: 캐시에 시도 번호

**Files:**

- Modify: `src/cuesift/store/cache.py` (`CacheRequest`)
- Modify: `src/cuesift/store/provider.py` (`CachingProvider`)
- Test: `tests/test_store_cache.py` (기존 파일에 추가)

**Interfaces:**

- Consumes: 기존 `CacheRequest(identity, temperature, max_tokens, messages)` · `CachingProvider(inner, *, identity, cache_dir, warn)`
- Produces:
  - `CacheRequest(..., attempt: int = 0)` — `attempt=0`이면 키 문자열 불변
  - `CachingProvider(..., attempt: int = 0)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_store_cache.py`에 추가:

```python
def test_attempt_0은_키를_바꾸지_않는다():
    """**하위 호환 회귀 테스트다** (설계 §8).

    키에 attempt를 무조건 넣으면 기존에 쌓인 번역 캐시가 전량 미스가 되고,
    WP7b가 실물로 증명한 재개(2회차 실제 호출 0개)가 한 번 헛돈다.
    """
    messages = (ChatMessage(role="user", content="안녕"),)
    without = CacheRequest(
        identity="m|v1", temperature=0.0, max_tokens=None, messages=messages
    )
    explicit_zero = CacheRequest(
        identity="m|v1", temperature=0.0, max_tokens=None, messages=messages, attempt=0
    )
    assert without.key == explicit_zero.key


def test_attempt가_다르면_키가_갈린다():
    """자가일관성은 같은 입력을 N회 부른다 - 키가 같으면 2회차부터 캐시
    히트가 나서 **분산이 항상 0**으로 나온다 (FR-4.1)."""
    messages = (ChatMessage(role="user", content="안녕"),)
    keys = {
        CacheRequest(
            identity="m|v1",
            temperature=1.0,
            max_tokens=None,
            messages=messages,
            attempt=k,
        ).key
        for k in range(3)
    }
    assert len(keys) == 3


def test_온도가_다르면_키가_갈린다():
    """설계 §8 - Tier 1(temperature>0)이 기존 번역(0.0)의 캐시를 건드리지
    않는 것은 이 성질 덕이다."""
    messages = (ChatMessage(role="user", content="안녕"),)
    cold = CacheRequest(identity="m|v1", temperature=0.0, max_tokens=None, messages=messages)
    hot = CacheRequest(identity="m|v1", temperature=1.0, max_tokens=None, messages=messages)
    assert cold.key != hot.key


def test_CachingProvider가_attempt를_고정한다(tmp_path):
    inner = ScriptedProvider(["첫째", "둘째"])
    a = CachingProvider(inner, identity="m|v1", cache_dir=tmp_path, attempt=0)
    b = CachingProvider(inner, identity="m|v1", cache_dir=tmp_path, attempt=1)
    messages = [ChatMessage(role="user", content="안녕")]

    first = a.complete(messages, temperature=1.0, max_tokens=None)
    second = b.complete(messages, temperature=1.0, max_tokens=None)

    # attempt가 다르므로 캐시가 갈리고 안쪽이 두 번 불린다.
    assert len(inner.calls) == 2
    assert first.text != second.text

    # 같은 attempt를 다시 부르면 캐시가 맞는다.
    again = a.complete(messages, temperature=1.0, max_tokens=None)
    assert len(inner.calls) == 2
    assert again.text == first.text
```

기존 파일의 import에 `CachingProvider`·`ScriptedProvider`가 없으면 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -v -k attempt`
Expected: FAIL — `TypeError: CacheRequest.__init__() got an unexpected keyword argument 'attempt'`

- [ ] **Step 3: 최소 구현**

`src/cuesift/store/cache.py`의 `CacheRequest`에 필드를 더하고 `key`를 고친다:

```python
    identity: str
    temperature: float
    max_tokens: int | None
    messages: tuple[ChatMessage, ...]
    attempt: int = 0
    """자가일관성의 시도 번호 (FR-4.1 · 설계 §8).

    **0은 키 문자열에 넣지 않는다** - 아래 `key` 참고.
    """
```

`key` property의 `material` 조립부를 교체한다:

```python
        parts = [
            self.identity,
            repr(float(self.temperature)),
            "none" if self.max_tokens is None else str(self.max_tokens),
            self.messages_sha,
        ]
        # **0이면 생략한다.** 넣으면 기존에 쌓인 캐시가 전량 미스가 되어
        # WP7b가 실물로 증명한 재개(2회차 실제 호출 0개)가 한 번 헛돈다.
        # 자가일관성만 시도를 가르면 되고, 나머지 경로는 0이다.
        if self.attempt:
            parts.append(f"attempt={self.attempt}")
        material = _SEP.join(parts)
```

`src/cuesift/store/provider.py`의 `CachingProvider.__init__`에 인자를 더한다:

```python
    def __init__(
        self,
        inner: Provider,
        *,
        identity: str,
        cache_dir: Path,
        warn: Callable[[str], None] = _ignore,
        attempt: int = 0,
    ) -> None:
```

기존 본문 끝에 저장한다:

```python
        # 시도 번호는 **감싸는 시점에 고정된다.** complete()마다 받으면
        # Provider 프로토콜이 달라져 translate_segments를 고쳐야 한다.
        self._attempt = attempt
```

`complete()`의 `CacheRequest` 조립에 넘긴다:

```python
        request = CacheRequest(
            identity=self._identity,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=tuple(messages),
            attempt=self._attempt,
        )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -v`
Expected: PASS

- [ ] **Step 5: 전체 테스트와 커밋**

Run: `.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing`
Expected: 기존 캐시 테스트가 **전부 그대로 통과해야 한다** — 하나라도 깨지면
키가 바뀐 것이므로 Step 3을 다시 본다.

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/store/cache.py src/cuesift/store/provider.py tests/test_store_cache.py
git commit -m "기능: 캐시 키에 시도 번호 — attempt=0은 기존 키를 보존한다 (FR-4.1)"
```

---

### Task 4: Tier 1 후보 선별

**Files:**

- Modify: `src/cuesift/triage/policy.py`
- Test: `tests/test_triage_policy.py` (기존 파일에 추가)

**Interfaces:**

- Consumes: 기존 `SegmentRisk` · `_sorted_desc`
- Produces: `select_tier1_candidates(risks: Sequence[SegmentRisk], max_ratio: float) -> list[str]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_triage_policy.py`에 추가:

```python
def _risk(seg_id: str, score: float, *, hard_fail: bool = False, selected: bool = False):
    return SegmentRisk(
        segment_id=seg_id,
        signals=[],
        risk_score=score,
        hard_fail=hard_fail,
        selected=selected,
    )


def test_hard_fail은_후보에서_빠진다():
    """risk_score가 1.0으로 고정돼 신호를 더해도 순위가 안 바뀐다 -
    낭비가 아니라 **무의미**하다 (설계 §5)."""
    risks = [
        _risk("a", 1.0, hard_fail=True),
        _risk("b", 0.4),
        _risk("c", 0.3),
        _risk("d", 0.2),
    ]
    assert "a" not in select_tier1_candidates(risks, 1.0)


def test_이미_선별된_것은_후보에서_빠진다():
    """예산을 여기 쓰면 그만큼 회색지대를 못 본다 (설계 §5)."""
    risks = [_risk("a", 0.9, selected=True), _risk("b", 0.4), _risk("c", 0.3)]
    assert select_tier1_candidates(risks, 1.0) == ["b", "c"]


def test_상한의_분모는_전체다():
    """FR-4.3이 '전체 세그먼트 중 최대 비율'이라고 적혀 있다. 후보 집합을
    분모로 삼으면 회색지대가 좁은 트랙에서 상한이 사실상 사라진다."""
    risks = [_risk("a", 0.9, selected=True)] + [
        _risk(str(i), 0.5 - i * 0.01) for i in range(9)
    ]
    # 전체 10건 × 0.2 = 2건 (올림)
    assert len(select_tier1_candidates(risks, 0.2)) == 2


def test_회색지대가_상한보다_작으면_있는_만큼만():
    """상한이지 할당량이 아니다 (설계 §5)."""
    risks = [_risk("a", 0.9, selected=True), _risk("b", 0.4)] + [
        _risk(f"h{i}", 1.0, hard_fail=True) for i in range(8)
    ]
    assert select_tier1_candidates(risks, 1.0) == ["b"]


def test_위험도_내림차순으로_고른다():
    risks = [_risk("low", 0.1), _risk("high", 0.8), _risk("mid", 0.5)]
    assert select_tier1_candidates(risks, 0.7) == ["high", "mid"]


def test_동점은_세그먼트_ID로_깨뜨린다():
    """NFR-3 - 순서가 흔들리면 같은 입력에 같은 LLM 호출이 나가지 않는다."""
    risks = [_risk("b", 0.5), _risk("a", 0.5), _risk("c", 0.5)]
    assert select_tier1_candidates(risks, 0.7) == ["a", "b"]


def test_빈_입력은_빈_목록():
    assert select_tier1_candidates([], 0.5) == []


def test_상한이_0이면_아무도_안_고른다():
    risks = [_risk("a", 0.5), _risk("b", 0.4)]
    assert select_tier1_candidates(risks, 0.0) == []


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_잘못된_상한을_거부한다(bad):
    """select_by_budget과 같은 방어다. NaN을 비교 연산의 우연에 맡기면
    리팩터링 한 번에 조용히 깨진다."""
    with pytest.raises(ValueError):
        select_tier1_candidates([_risk("a", 0.5)], bad)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_triage_policy.py -v -k tier1`
Expected: FAIL — `ImportError: cannot import name 'select_tier1_candidates'`

- [ ] **Step 3: 최소 구현**

`src/cuesift/triage/policy.py`의 `review_ratio` 앞에 추가:

```python
def select_tier1_candidates(
    risks: Sequence[SegmentRisk],
    max_ratio: float,
) -> list[str]:
    """Tier 1을 적용할 세그먼트 ID (FR-4.3 · 설계 §5).

    `select_by_budget`이 `selected`를 채운 **전체 목록**을 받는다. 선별분만
    받으면 "컷라인 아래"라는 개념 자체가 성립하지 않는다.

    ## 왜 컷라인 위가 아니라 아래인가

    요구사항정의서 §4의 도식은 "Tier 0 -> 의심 후보 -> Tier 1"이라고 적혀
    있으나, 그 도식은 벤치마크(2026-07-29)보다 먼저 쓰였다. 실측은 Tier 0가
    의미 반전을 큐에서 **밀어낸다**고 말한다 - 예산 10%에서 `negation`
    Recall이 1.41%로 무작위 기준선 9.61%보다 낮다. 위험도 상위를 후보로
    삼으면 Tier 1은 **이미 잡힌 것만 다시 본다.**

    ## 제외 대상

    - `hard_fail`: `fuse()`가 risk_score를 1.0으로 고정하므로 신호를 더해도
      순위가 바뀌지 않는다. 낭비가 아니라 무의미하다
    - `selected`: 이미 검수 큐행이다. 상한을 여기 쓰면 그만큼 회색지대를
      못 본다

    `target_text is None`(번역 실패분) 제외는 **호출자의 일이다** -
    `SegmentRisk`가 텍스트를 갖지 않으므로 여기서 판정할 수 없고, 끌어들이면
    `triage/`가 `segment/` 본문에 결합된다.

    상한은 **할당량이 아니다.** 회색지대가 상한보다 작으면 있는 만큼만 낸다.
    """
    # select_by_budget과 같은 방어다. NaN을 비교 연산의 방향에 맡기면
    # 훗날 리팩터링 한 번에 조용히 깨진다.
    if math.isnan(max_ratio):
        raise ValueError(f"max_ratio는 NaN일 수 없다 (받은 값: {max_ratio})")
    if not 0.0 <= max_ratio <= 1.0:
        raise ValueError(f"max_ratio는 0.0~1.0이어야 한다 (받은 값: {max_ratio})")
    if not risks:
        return []

    # **분모가 후보 집합이 아니라 전체다.** FR-4.3이 "전체 세그먼트 중
    # Tier 1을 적용할 최대 비율"이라고 적혀 있고, 후보 집합을 분모로 삼으면
    # 회색지대가 좁은 트랙에서 상한이 사실상 사라진다.
    cap = math.ceil(len(risks) * max_ratio)
    if cap <= 0:
        return []

    # _sorted_desc를 그대로 쓴다 - 동점을 세그먼트 ID로 깨뜨리는 규칙이
    # 검수 큐와 같아야 NFR-3(재현성)이 성립한다.
    gray = [r for r in _sorted_desc(risks) if not r.hard_fail and not r.selected]
    return [r.segment_id for r in gray[:cap]]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_triage_policy.py -v`
Expected: PASS

- [ ] **Step 5: 게이트와 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/triage/policy.py tests/test_triage_policy.py
git commit -m "기능: Tier 1 후보 선별 — 컷라인 아래 회색지대 (FR-4.3)"
```

---

### Task 5: 자가일관성 수집기

**Files:**

- Create: `src/cuesift/signals/llm.py`
- Modify: `src/cuesift/risk/fuse.py` (`DEFAULT_WEIGHTS`)
- Test: `tests/test_signals_llm.py`

**Interfaces:**

- Consumes:
  - Task 1: `similarity(a: str, b: str) -> float`
  - Task 2: `Tier1Context(signal, provider_for, samples, temperature)` · `register()`
  - 기존: `translate_segments(...)` · `TranslationResult(target_lang, segments, failures)`
- Produces: `SelfConsistency` — `name = "llm.self_consistency"` · `tier = 1` ·
  `collect_tier1(seg, ctx) -> Signal | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_signals_llm.py`:

```python
"""자가일관성 신호 (FR-4.1 · 설계 §6)."""

from __future__ import annotations

from cuesift.segment import Segment
from cuesift.signals.base import SignalContext, Tier1Context
from cuesift.signals.llm import SelfConsistency
from tests.fakes.provider import EchoProvider


def _seg() -> Segment:
    return Segment(
        id="1", index=0, start_ms=0, end_ms=2000, source_text="그는 오지 않았다",
        target_text="He did not come",
    )


def _ctx(signal_ctx: SignalContext, texts: list[str]) -> Tier1Context:
    """시도마다 정해진 번역문을 내는 컨텍스트.

    **`EchoProvider`를 쓰는 이유는 재시도를 없애기 위해서다.** 이 가짜는
    요청받은 id를 그대로 채워 정상 JSON을 내므로 파싱이 실패하지 않고,
    호출 횟수가 정확히 `samples`와 같아진다. 응답 문자열을 손으로 조립하면
    `parse_translations`의 정수 id 계약(커밋 `817ed64`)을 다시 구현하는
    셈이고, 그 계약이 바뀌면 이 테스트가 조용히 재시도 경로를 타게 된다.

    시도마다 다른 프로바이더를 주는 것이 실제 배선과 같다(설계 §8) -
    캐시가 attempt로 갈리므로 각 시도가 자기 응답을 받는다.
    """
    # 기본 인자로 캡처한다. 루프 변수를 클로저로 잡으면 전부 마지막 값이
    # 되고, ruff의 B023이 그것을 잡는다.
    providers = [EchoProvider(transform=lambda _src, t=t: t) for t in texts]
    return Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=len(texts),
        temperature=1.0,
    )


def test_재번역이_모두_같으면_점수가_0이다(signal_ctx):
    """흔들리지 않았다 = 이 구간은 번역하기 쉽다."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert signal.score == 0.0


def test_재번역이_흩어지면_점수가_높다(signal_ctx):
    scattered = [
        "He did not come",
        "완전히 다른 문장이 나왔다",
        "Something else entirely here",
    ]
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, scattered))
    assert signal is not None
    assert signal.score > 0.5


def test_신호에_근거가_담긴다(signal_ctx):
    """FR-6.4 - review.json이 '왜 선별되었는지'를 이것으로 쓴다."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert len(signal.detail["samples"]) == 3
    assert len(signal.detail["pairwise"]) == 3  # 3개에서 나오는 쌍의 수


def test_hard_fail이_아니다(signal_ctx):
    """의미 판단은 결정론적이지 않다. hard fail은 오탐이 곧 지표 파괴다
    (FR-6.2 · 이 저장소의 '미탐이 오탐보다 낫다')."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert signal.hard_fail is False
    assert signal.tier == 1


def test_성공분이_2개_미만이면_None이다(signal_ctx):
    """**score=0.0이 아니다.** 0.0은 '판정했고 안전하다'이고 None은
    '판정 대상이 아니다'다 - 0점 신호를 내면 review.json이 무의미한
    항목으로 채워진다(signals/base.py의 계약)."""
    # garbage=True면 파싱이 실패해 재시도 뒤에도 target_text가 안 채워진다.
    # _ctx를 쓰지 않는 이유는 그쪽이 transform으로 **정상** 응답을 내기
    # 때문이다 - "망가진 문자열"을 transform에 넣어도 그냥 번역문이 된다.
    providers = [EchoProvider(garbage=True) for _ in range(3)]
    ctx = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=3,
        temperature=1.0,
    )
    assert SelfConsistency().collect_tier1(_seg(), ctx) is None


def test_번역문이_없으면_None이다(signal_ctx):
    """번역 실패분은 검수 대상이 아니라 재실행 대상이다
    (TranslationResult 독스트링)."""
    seg = Segment(
        id="1", index=0, start_ms=0, end_ms=2000, source_text="그는 오지 않았다",
        target_text=None,
    )
    same = "He did not come"
    assert SelfConsistency().collect_tier1(seg, _ctx(signal_ctx, [same, same, same])) is None


def test_시도마다_다른_프로바이더를_받는다(signal_ctx):
    """설계 §8 - attempt별로 캐시가 갈려야 분산이 관측된다."""
    seen: list[int] = []
    providers = [EchoProvider() for _ in range(3)]

    def provider_for(attempt: int):
        seen.append(attempt)
        return providers[attempt]

    ctx = Tier1Context(
        signal=signal_ctx, provider_for=provider_for, samples=3, temperature=1.0
    )
    SelfConsistency().collect_tier1(_seg(), ctx)
    assert seen == [0, 1, 2]
```

`signal_ctx` fixture는 Task 2에서 만든 것과 같은 정의를 이 파일에도 둔다.
세 번째 파일(Task 6)에서도 쓰므로, 그때 `tests/conftest.py`로 올린다 —
지금 올리면 Task 2·5가 서로를 기다리게 된다.

**응답 문자열을 손으로 조립하지 않는다.** `EchoProvider`가 요청받은 id를 보고
정상 JSON을 만들어 주므로 `parse_translations`의 정수 id 계약(커밋
`817ed64`)을 테스트가 다시 구현할 필요가 없다. 손으로 조립하면 그 계약이
바뀔 때 테스트가 **조용히 재시도 경로를 타고** 호출 횟수가 어긋난다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.signals.llm'`

- [ ] **Step 3: 최소 구현**

`src/cuesift/signals/llm.py`:

```python
"""Tier 1 신호 — LLM을 불러 판정한다 (FR-4.1 · 설계 §6).

**후보 세그먼트에만 실행된다.** 전량에 적용하면 LLM 비용이 N배가 되고,
요구사항정의서 §4가 "16부작 × 20개 언어에서 3배는 감당 불가"라고 적었다.
실행 경로 분리는 `signals/base.py`의 `collect_tier1`이 맡는다.
"""

from __future__ import annotations

from itertools import combinations

from cuesift.segment import Segment, Signal
from cuesift.signals.base import Tier1Context
from cuesift.signals.similarity import similarity
from cuesift.translate import translate_segments


class SelfConsistency:
    """FR-4.1 — 같은 원문을 N회 재번역해 결과가 흩어지는 정도를 잰다.

    **"이 번역이 틀렸나"가 아니라 "이 구간이 번역하기 어려운가"다.**
    재번역들이 형태적으로 흩어졌다면 실제로 모델이 흔들린 것이다.

    의미 반전(`negation`)은 이 신호로 잡히지 않는다 - 원문에 부정이 살아
    있으므로 재번역 N개가 모두 부정을 제대로 살려 서로 비슷하게 나온다.
    그쪽은 기존 번역을 비교 집합에 넣어야 보이는데, 착수 시점 실측(설계
    §3.2)이 문자 단위 유사도로는 그 비교가 역방향으로 작동함을 보였다.
    """

    name = "llm.self_consistency"
    tier = 1

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        # 번역 실패분은 검수 대상이 아니라 재실행 대상이다
        # (TranslationResult 독스트링의 계약).
        if not seg.target_text:
            return None

        samples = self._retranslate(seg, ctx)

        # **None과 score=0.0은 다르다.** 성공분이 2개 미만이면 쌍이 없어
        # 판정 자체가 불가능하다. 0.0을 내면 "판정했고 안전하다"가 되어
        # 프로바이더 장애가 '안전'으로 보고된다.
        if len(samples) < 2:
            return None

        pairwise = [similarity(a, b) for a, b in combinations(samples, 2)]
        score = 1.0 - sum(pairwise) / len(pairwise)

        return Signal(
            name=self.name,
            tier=1,
            # 부동소수 오차로 -1e-16 같은 값이 나오면 Signal이 범위 검증에서
            # 죽는다. fuse의 noisy-or도 밑이 1을 넘으면 깨진다.
            score=min(1.0, max(0.0, score)),
            # hard fail로 두지 않는다. 의미 판단은 결정론적이지 않고,
            # hard fail 오탐은 실제 검수 비율을 부풀려 Recall@Budget 지표
            # 자체를 파괴한다 (FR-6.2).
            hard_fail=False,
            detail={
                # FR-6.4 - review.json이 "왜 선별되었는지"를 이것으로 쓴다.
                "samples": samples,
                "pairwise": pairwise,
                "temperature": ctx.temperature,
            },
        )

    def _retranslate(self, seg: Segment, ctx: Tier1Context) -> list[str]:
        """N회 재번역해 성공분만 낸다.

        `translate_segments`를 그대로 재사용한다 - 배치·컨텍스트 윈도우·
        재시도가 이미 구현돼 있고 다시 만들 이유가 없다.

        **시도마다 다른 프로바이더를 받는다.** 캐시가 `attempt`로 갈려야
        같은 입력에 다른 응답이 저장되고, 그래야 분산이 관측된다(설계 §8).
        같은 프로바이더를 N번 쓰면 2회차부터 캐시 히트가 나서 **분산이
        항상 0**이 된다.
        """
        out: list[str] = []
        for attempt in range(ctx.samples):
            result = translate_segments(
                [seg],
                provider=ctx.provider_for(attempt),
                source_lang=ctx.signal.source_lang,
                target_lang=ctx.signal.target_lang,
                glossary=ctx.signal.glossary,
                temperature=ctx.temperature,
            )
            # 실패분은 target_text=None으로 들어온다. 조용히 빈 문자열로
            # 세면 "모두 같다"가 되어 점수가 0.0으로 떨어진다.
            for translated in result.segments:
                if translated.target_text:
                    out.append(translated.target_text)
        return out
```

`signals/__init__.py`(또는 수집기를 등록하는 곳)에 등록을 추가한다.
기존 `structural.py`·`derived.py`가 `register`를 어디서 부르는지 확인해
**같은 자리에** 넣는다.

```python
register(SelfConsistency())
```

`src/cuesift/risk/fuse.py`의 `DEFAULT_WEIGHTS`에 한 줄 더한다:

```python
    "spec.overlap": 1.0,
    # Tier 1 (FR-4.1). **가중치는 튜닝하지 않는다**(스펙 §6.3) - 같은
    # 데이터에서 맞춘 값은 새 데이터에서 재현되지 않는다.
    "llm.self_consistency": 1.0,
```

주석의 "등록된 신호 9종"을 **10종**으로 고친다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_llm.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: 전체 테스트와 커밋**

Run: `.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing`
Expected: 전부 통과. **`DEFAULT_WEIGHTS` 개수를 세는 기존 테스트가 있으면
9 → 10으로 함께 고친다.**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/signals/llm.py src/cuesift/risk/fuse.py tests/test_signals_llm.py
git commit -m "기능: 자가일관성 신호 — N회 재번역의 상호 유사도 (FR-4.1)"
```

---

### Task 6: 2라운드 오케스트레이션

**Files:**

- Create: `src/cuesift/tier1.py`
- Test: `tests/test_tier1.py`

**Interfaces:**

- Consumes:
  - Task 2: `Tier1Context` · `collect_tier1` · `collect_all`
  - Task 3: `CachingProvider(..., attempt=k)`
  - Task 4: `select_tier1_candidates(risks, max_ratio) -> list[str]`
  - 기존: `fuse` · `select_by_budget` · `SegmentRisk` · `Provider`
- Produces:

```python
def triage_with_tier1(
    segments: Sequence[Segment],
    ctx: SignalContext,
    *,
    budget_ratio: float,
    provider: Provider,
    max_ratio: float,
    samples: int = 3,
    temperature: float = 1.0,
    cache_dir: Path | None = None,
    identity: str | None = None,
) -> list[SegmentRisk]
```

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tier1.py`:

```python
"""2라운드 트리아지 (설계 §7)."""

from __future__ import annotations

import pytest

from cuesift.segment import Segment
from cuesift.signals.base import SignalContext
from cuesift.spec import load_builtin
from cuesift.tier1 import triage_with_tier1
from tests.fakes.provider import EchoProvider


@pytest.fixture
def signal_ctx() -> SignalContext:
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def test_tier1은_후보에만_불린다(signal_ctx):
    """**비용 통제의 핵심 게이트다** (FR-4.3).

    전량에 불리면 요구사항정의서 §4가 '감당 불가'라고 적은 비용이 난다.
    """
    segments = [
        Segment(id=str(i), index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000,
                source_text=f"원문{i}", target_text=f"Target {i}")
        for i in range(10)
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    triage_with_tier1(
        segments, signal_ctx, budget_ratio=0.1, provider=provider,
        max_ratio=0.2, samples=3,
    )

    # 후보 2건(10 × 0.2) × 3회 재번역 = 6회. 전량이면 30회다.
    assert len(provider.calls) == 6


def test_번역_실패분은_후보에서_빠진다(signal_ctx):
    """target_text가 None이면 재번역할 대상이 없다 (설계 §5)."""
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000,
                source_text="원문", target_text=None),
        Segment(id="2", index=1, start_ms=1000, end_ms=2000,
                source_text="원문", target_text="Target"),
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    triage_with_tier1(
        segments, signal_ctx, budget_ratio=0.5, provider=provider,
        max_ratio=1.0, samples=2,
    )

    # id=1은 struct.empty로 hard fail이라 애초에 제외되고, 실패분 제외로
    # 한 번 더 걸린다. 남는 후보는 id=2 하나 = 2회 호출.
    assert len(provider.calls) <= 2


def test_max_ratio가_0이면_LLM을_안_부른다(signal_ctx):
    """비용을 완전히 끄는 경로가 있어야 한다."""
    segments = [
        Segment(id=str(i), index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000,
                source_text=f"원문{i}", target_text=f"Target {i}")
        for i in range(10)
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    risks = triage_with_tier1(
        segments, signal_ctx, budget_ratio=0.1, provider=provider,
        max_ratio=0.0, samples=3,
    )

    assert provider.calls == []
    assert len(risks) == 10


def test_전체_목록을_반환한다(signal_ctx):
    """select_by_budget과 같은 계약이다 - 선별된 것만 반환하면
    review_ratio가 언제나 1.0이 되어 §9.1 배수의 분모가 무너진다."""
    segments = [
        Segment(id=str(i), index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000,
                source_text=f"원문{i}", target_text=f"Target {i}")
        for i in range(10)
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    risks = triage_with_tier1(
        segments, signal_ctx, budget_ratio=0.1, provider=provider,
        max_ratio=0.2, samples=3,
    )

    assert len(risks) == 10
    assert any(r.selected for r in risks)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.tier1'`

- [ ] **Step 3: 최소 구현**

`src/cuesift/tier1.py`:

```python
"""Tier 0 -> Tier 1 2라운드 트리아지 (설계 §7 · FR-4.1 · FR-4.3).

**noisy-or가 이 구조를 성립시킨다.** `1 - ∏(1 - sᵢ)^wᵢ`는 신호가 붙을수록
점수가 올라가기만 하므로, 회색지대에만 Tier 1을 적용해도 적용받은 쪽이
부당하게 낮아지지 않는다. 가중 평균이었다면 낮은 Tier 1 점수가 기존
위험도를 희석해 오히려 큐에서 밀어냈을 것이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cuesift.risk.fuse import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals.base import SignalContext, Tier1Context, collect_all, collect_tier1
from cuesift.store.provider import CachingProvider
from cuesift.translate.provider import Provider
from cuesift.triage.policy import select_by_budget, select_tier1_candidates


def triage_with_tier1(
    segments: Sequence[Segment],
    ctx: SignalContext,
    *,
    budget_ratio: float,
    provider: Provider,
    max_ratio: float,
    samples: int = 3,
    temperature: float = 1.0,
    cache_dir: Path | None = None,
    identity: str | None = None,
) -> list[SegmentRisk]:
    """Tier 0로 좁히고 회색지대에만 Tier 1을 적용한 뒤 다시 선별한다.

    **전체 목록을 반환한다.** `select_by_budget`과 같은 계약이며, 선별된
    것만 반환하면 `review_ratio`가 언제나 1.0이 되어 README 배수의 분모가
    무너진다.

    `temperature`의 기본값이 1.0인 것은 OpenAI Chat Completions API 명세의
    기본값이라 **출처가 있기 때문이다**(§11 R8 - 출처 없는 수치를 기본값으로
    넣지 않는다). 0.0이면 재번역이 전부 같아 신호가 죽는데, 그 방어는
    `Tier1Context`가 한다.
    """
    # ① Tier 0 - 비용 0, 전량
    tier0 = collect_all(segments, ctx)

    # ② 1차 융합
    risks = [fuse(seg.id, tier0[seg.id]) for seg in segments]

    # ③ 예산 적용 - ④가 "이미 큐에 든 것"을 알아야 한다
    scored = select_by_budget(risks, budget_ratio)

    # ④ 후보 선별
    candidate_ids = set(select_tier1_candidates(scored, max_ratio))

    # 번역 실패분을 여기서 뺀다. SegmentRisk가 텍스트를 갖지 않아
    # select_tier1_candidates가 판정할 수 없다(설계 §5).
    candidates = [s for s in segments if s.id in candidate_ids and s.target_text]

    if not candidates:
        # LLM을 부르지 않는 경로. max_ratio=0이나 회색지대가 빈 경우다.
        return scored

    # ⑤ Tier 1 - 후보에만
    tier1 = collect_tier1(
        candidates,
        Tier1Context(
            signal=signal_ctx,
            provider_for=_provider_factory(
                provider, cache_dir=cache_dir, identity=identity
            ),
            samples=samples,
            temperature=temperature,
        ),
    )

    # ⑥ 재융합 - Tier 0 신호에 Tier 1 신호를 더해 다시 계산한다
    refused = [fuse(seg.id, tier0[seg.id] + tier1.get(seg.id, [])) for seg in segments]

    # ⑦ 예산 재적용
    return select_by_budget(refused, budget_ratio)


def _provider_factory(
    inner: Provider,
    *,
    cache_dir: Path | None,
    identity: str | None,
):
    """시도 번호별 프로바이더를 만든다 (설계 §8).

    **캐시를 켤지 말지가 여기서 끝난다.** 신호 수집기는 `identity`도
    `cache_dir`도 모른 채 `provider_for(attempt)`만 부른다.
    """

    def provider_for(attempt: int) -> Provider:
        if cache_dir is None or identity is None:
            # 캐시 없이 그대로. 이 경로에서는 같은 입력에 매번 새 호출이
            # 나가므로 NFR-3(재현성)이 성립하지 않는다 - 호출자가 캐시를
            # 끈 것은 그 대가를 받아들인 것이다.
            return inner
        return CachingProvider(
            inner, identity=identity, cache_dir=cache_dir, attempt=attempt
        )

    return provider_for
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: 게이트와 커밋**

Run: `.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing`

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/tier1.py tests/test_tier1.py
git commit -m "기능: 2라운드 트리아지 오케스트레이션 (FR-4.3 · 설계 §7)"
```

---

### Task 7: live 검증과 문서 정정

**Files:**

- Modify: `tests/test_translate_live.py`
- Modify: `docs/요구사항정의서.md` (§4 · §5.4 · §12 Q4)
- Modify: `docs/WBS.md`
- Modify: `HANDOFF.md`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes: Task 6의 `triage_with_tier1`
- Produces: 없음 (검증과 기록)

- [ ] **Step 1: live 테스트를 추가한다**

`tests/test_translate_live.py`에 추가한다. 기존 live 테스트가 환경변수를
읽는 방식(`CUESIFT_LIVE_*`)을 그대로 따른다 — 접두사를 잘못 쓰면 CLI가
조용히 기본값으로 돈다.

기존 `_provider()`(84행)를 그대로 쓴다 — 환경변수가 없으면 **skip이지 fail이
아니다.** 직접 만든 클라이언트라 `finally`에서 닫는다.

```python
@pytest.mark.live
def test_자가일관성이_실제_엔드포인트에서_신호를_낸다(tmp_path: Path) -> None:
    """설계 §11 A4 - 가짜가 아니라 실제 모델에서 신호가 나오는지 본다.

    **점수의 크기를 단정하지 않는다.** temperature=1.0이라 재번역이 흔들리는
    정도는 모델마다 다르고, 특정 수치를 기대하면 모델을 바꿀 때마다 빨개진다.
    이 파일이 호출 횟수를 단정하지 않는 것과 같은 이유다.
    """
    segments = [
        Segment(
            id="s0", index=0, start_ms=0, end_ms=2000,
            source_text="그는 끝내 오지 않았다.", target_text="He never came.",
        ),
        Segment(
            id="s1", index=1, start_ms=2000, end_ms=4000,
            source_text="비가 그치기를 기다렸다.",
            target_text="We waited for the rain to stop.",
        ),
    ]
    ctx = SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )

    provider = _provider()
    try:
        risks = triage_with_tier1(
            segments,
            ctx,
            budget_ratio=0.5,
            provider=provider,
            max_ratio=1.0,
            samples=3,
            temperature=1.0,
            cache_dir=tmp_path,
            identity=provider.cache_identity,
        )
    finally:
        # 직접 만든 클라이언트라 우리가 닫는다. 안 닫으면 세션 끝까지
        # 소켓이 남는다(기존 live 테스트와 같은 이유).
        provider.close()

    names = {s.name for r in risks for s in r.signals}
    # 이 파일의 관례대로 `-s`로 읽는다. 통과한 실행에서도 값이 보여야 한다.
    print(f"\n신호: {sorted(names)}")
    assert "llm.self_consistency" in names
```

**세그먼트 2건에 `budget_ratio=0.5`면 quota가 1이다.** Tier 0 신호가 없어
둘 다 위험도 0.0이므로 동점이 되고 세그먼트 ID 순으로 `s0`이 선별된다.
남은 `s1`이 회색지대이고 거기에 Tier 1이 붙는다.

- [ ] **Step 2: live 테스트를 실제로 돌린다**

Ollama는 트레이 앱으로 자동 기동해 `127.0.0.1:11434`를 듣는다. PATH에 없으면
`$env:LOCALAPPDATA\Programs\Ollama\ollama.exe`를 직접 부른다. 모델은
`qwen2.5:3b`를 쓴다 — `qwen2.5:1.5b`는 번역기로 못 쓴다(이전 세션 실측 5/15).

Run: `.venv/Scripts/python.exe -m pytest -m live -v -s`
Expected: PASS. **실행 시간과 실제 호출 횟수를 기록한다.**

- [ ] **Step 3: 요구사항정의서를 정정한다**

| 절 | 무엇 |
| --- | --- |
| §4 | 도식 아래에 각주 — 도식은 "Tier 0 → 의심 후보"이나 **구현은 컷라인 아래 회색지대**다. 도식이 벤치마크(7/29)보다 먼저 쓰였고, 실측은 Tier 0가 `negation`을 큐에서 밀어낸다고 말한다. 근거는 설계 §5 |
| §5.4 | FR-4.1은 자가일관성만 구현됨 · **FR-4.2 역번역은 미구현**을 상태로 표시 |
| §12 Q4 | 설계 §3.2의 실측 7쌍을 추가. "문자 단위 유사도는 의미 반전과 정상 변이를 분리하지 못한다(negation 0.727~0.930 · paraphrase 0.759~0.800, 범위가 겹친다)". **Q4는 계속 열려 있음을 명시** — 벤치마크를 돌리지 않았으므로 판정이 아니다 |

- [ ] **Step 4: WBS를 정정한다**

- WP8을 **8a(라이브러리) / 8b(CLI 배선)** 로 나눈다
- 8a를 ✅로, 8b를 ⬜로 표시한다
- **"Q4가 여기서 닫힌다"를 고친다** — 벤치마크 미실시로 열린 채 남는다
- 다음 작업 순서 표에서 8b와 WP5의 우선순위를 정한다

- [ ] **Step 5: HANDOFF.md와 CHANGELOG.md를 갱신한다**

`HANDOFF.md`는 **현재 내용이 사실과 다르다** — 브랜치가
`feat/translate-cli`이고 "main에 아직 안 올라갔다"고 적혀 있으나 그 작업은
`48f9133`으로 이미 squash 머지됐다. 테스트 수치도 975가 아니라 978이다.

`CHANGELOG.md`는 Keep a Changelog 형식을 따른다.

- [ ] **Step 6: 전체 게이트**

```bash
git add -A                      # check_links.py는 git ls-files만 본다
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
python scripts/check_links.py
npx --yes markdownlint-cli2
```

**두 문서 게이트의 파일 수가 일치하는지 확인한다** — 갈라지면 추적 안 된
문서가 있다는 뜻이다.

- [ ] **Step 7: 커밋**

```bash
git commit -m "문서: WP8a 완료 기록과 Q4 상태 정정"
```

---

## 완료 판정 (설계 §11)

| # | 조건 | 확인 |
| --- | --- | --- |
| A1 | Tier 0 경로에서 LLM 호출이 0이다 | Task 2 Step 5에서 변이를 넣어 죽는 것을 확인했다 |
| A2 | 기존 캐시가 유효하다 | Task 3의 `test_attempt_0은_키를_바꾸지_않는다` |
| A3 | `temperature=0`이 조용히 통과하지 않는다 | Task 2의 `test_temperature가_0이면_거부한다` |
| A4 | 실제 엔드포인트로 신호가 나온다 | Task 7 Step 2 |
| A5 | 기존 테스트가 전부 통과한다 | 각 태스크 마지막 · **수집 개수를 읽는다** |
| A6 | 문서 정정 6건이 반영됐다 | Task 7 Step 3~5 |
| A7 | 게이트 5종 통과 | Task 7 Step 6 |

## 이 계획이 하지 않는 것

| 항목 | 어디로 |
| --- | --- |
| `--tier1-max-ratio` 등 CLI 옵션 | **WP8b** |
| 벤치마크에 Tier 1 태우기 | 별도 작업. 이것 없이는 Q4가 안 닫힌다 |
| FR-4.2 역번역 · `llm.retranslation_gap` | 보류 (설계 §3.2) |
| `_dry_run_report`의 손으로 맞춘 기본값 | WP7b가 남긴 것. WP8b가 같은 것을 필요로 하면 그때 함께 |
