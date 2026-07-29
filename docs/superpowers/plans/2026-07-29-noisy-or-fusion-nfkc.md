# noisy-or 융합과 숫자 NFKC 정규화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tier 0 위험도 융합을 가중 평균에서 noisy-or로 바꾸고 `struct.number_missing`의 전각 숫자 미탐을 고친 뒤, TED2020 벤치를 2단계로 재측정해 README 숫자를 갱신한다.

**Architecture:** 제품 코드 두 파일만 바꾼다. `bench/`는 손대지 않는다 — `bench/measure.py`의 `_risks()`가 `fuse()`를 그대로 호출하므로 산식 교체가 벤치에 자동 반영된다. A/B는 `--fusion` 플래그가 아니라 **두 커밋에서 각각 측정**하는 것으로 대신한다.

**Tech Stack:** Python 3.11+ · pytest · ruff · 표준 라이브러리 `unicodedata`

**근거 스펙:** [2026-07-29-noisy-or-fusion-nfkc-design.md](../specs/2026-07-29-noisy-or-fusion-nfkc-design.md)

## Global Constraints

- **Python 실행은 반드시 `.venv/Scripts/python.exe`**. 시스템 Python은 3.14라 다르다.
- 모든 모듈 첫 줄에 `from __future__ import annotations`.
- 독스트링·주석은 **한국어**, 근거 FR·§ 번호를 병기한다.
- 주석에는 "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"** 를 적는다.
- ruff: `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`.
- 커밋 메시지는 **한국어**. **푸시하지 않는다** — 사용자가 명시적으로 요청할 때만.
- **의존성을 추가하지 않는다.** `unicodedata`는 표준 라이브러리다.
- `DEFAULT_WEIGHTS`는 전부 1.0을 유지한다. **가중치를 튜닝하지 않는다.**
- **모든 새 테스트는 수정 전 코드에서 실제로 실패시켜 본 뒤 붙인다.** 확인 없이 넘긴 회귀 테스트 3개가 직전 세션에서 전부 결함이었다.
- 게이트 출력의 **수집 개수를 읽는다.** `pytest`의 collected N, markdownlint의 `Linting: N files`, 링크 체커의 `상대 링크 N개`. 0개 수집은 통과가 아니라 설정 오류다.

---

## 스펙 정정 (착수 전 반영)

스펙 §5의 테스트 표에 **한 행이 틀렸다.** 계획을 쓰며 값을 실제로 계산해 발견했다.

| 스펙의 주장 | 실제 | 왜 |
| --- | --- | --- |
| 테스트 3 "`w=0`이 신호를 무력화한다" → 현재 코드에서 **FAIL** | **PASS** | 가중 평균에서 `w=0`인 신호는 분자에 0, 분모에 0을 더해 **평균에서 제외**된다. noisy-or에서도 `(1-s)^0 = 1`로 곱에서 제외된다. **두 산식의 결과가 일치한다** |

그리고 스펙이 언급하지 않은 것이 있다 — **기존 테스트 2건이 가중 평균을 고정하고 있어 반드시 교체해야 한다.**

| 기존 테스트 | 현재 기대값 | noisy-or에서 |
| --- | --- | --- |
| `test_weighted_average_of_multiple_signals` | 0.5 | **0.84** |
| `test_weights_shift_the_result` | 0.75 | **1.0** (포화되어 판별력 없음 → 입력도 바꾼다) |

Task 3 Step 1에서 이 둘을 교체한다. 스펙 파일도 Task 0에서 함께 고친다.

---

## 파일 구조

| 파일 | 책임 | 변경 |
| --- | --- | --- |
| `src/cuesift/risk/fuse.py` | 신호 → 위험도 합성 (FR-6.1·6.2·6.4) | 산식 교체 · 주석 정정 |
| `src/cuesift/signals/structural.py` | 구조 신호 5종 (FR-3.1~3.5) | `_numbers()` 한 줄 |
| `tests/test_risk_fuse.py` | 융합 계약 | 2건 교체 · 4건 추가 |
| `tests/test_signals_structural.py` | 구조 신호 계약 | 픽스처 1개 · 2건 추가 |
| `docs/superpowers/specs/2026-07-29-noisy-or-fusion-nfkc-design.md` | 설계 | §5 표 정정 |
| `bench/results/{pair}-2026-07-29.{md,json}` | 실측 리포트 | 재생성 |
| `README.md` · `CHANGELOG.md` · `HANDOFF.md` · `docs/WBS.md` | 기록 | 갱신 |

---

## Task 0: 스펙 §5 표 정정

**Files:**

- Modify: `docs/superpowers/specs/2026-07-29-noisy-or-fusion-nfkc-design.md`

**Interfaces:**

- Consumes: 없음
- Produces: 이후 태스크가 참조하는 정확한 테스트 목록

- [ ] **Step 1: `w=0` 행을 실제로 확인**

Run:

```bash
.venv/Scripts/python.exe -c "from cuesift.risk import fuse; from cuesift.segment import Signal; s=lambda n,v: Signal(name=n,tier=0,score=v,hard_fail=False); print(fuse('x',[s('a',1.0),s('b',0.6)],weights={'a':0.0,'b':1.0}).risk_score)"
```

Expected: `0.6` — `w=0`인 `a`가 평균에서 빠졌다. noisy-or도 같은 값을 낸다.

- [ ] **Step 2: 스펙 §5 표의 3행을 고친다**

`| 3 | \`w=0\`이 신호를 무력화한다 | **FAIL** | ablation과의 의미 일치 |` 를 다음으로 교체:

```markdown
| 3 | `w=0`이 신호를 무력화한다 | PASS | 두 산식이 일치하는 지점. 교체 후에도 유지되는지 확인한다 |
```

- [ ] **Step 3: 같은 표 아래에 교체 대상 문단을 추가**

```markdown
### 5.1 교체해야 하는 기존 테스트

`tests/test_risk_fuse.py`의 2건이 가중 평균을 값으로 고정하고 있다.
산식을 바꾸면 실패하므로 **함께 교체한다** — 실패를 보고 기대값만 고치면
"무엇을 계약으로 삼는지"가 기록되지 않는다.

| 테스트 | 현재 | noisy-or | 조치 |
| --- | --- | --- | --- |
| `test_weighted_average_of_multiple_signals` | 0.5 | 0.84 | 이름·독스트링·기대값 교체 |
| `test_weights_shift_the_result` | 0.75 | 1.0 (포화) | 입력을 판별력 있는 값으로 바꾼다 |
```

- [ ] **Step 4: 문서 게이트 실행**

Run:

```bash
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

Expected: `깨진 링크 없음` · `Summary: 0 issues`. 파일 개수가 14인지 확인한다.

- [ ] **Step 5: 커밋**

```bash
git add docs/superpowers/specs/2026-07-29-noisy-or-fusion-nfkc-design.md
git commit -m "문서: 스펙 §5 테스트 표 정정 — w=0은 두 산식이 일치한다

계획을 쓰며 값을 실제로 계산해 발견했다. 가중 평균에서 w=0인 신호는
분자에 0, 분모에 0을 더해 평균에서 제외되고, noisy-or에서는
(1-s)^0 = 1로 곱에서 제외된다 — 결과가 같다.

교체해야 하는 기존 테스트 2건도 함께 적었다. 스펙이 신규 테스트만
세고 기존 테스트가 옛 산식을 고정하고 있다는 것을 놓쳤다."
```

---

## Task 1: A2 — 숫자 NFKC 정규화

**Files:**

- Modify: `src/cuesift/signals/structural.py:97-99`
- Test: `tests/test_signals_structural.py`

**Interfaces:**

- Consumes: 없음
- Produces: `_numbers(text: str) -> list[str]` — 시그니처 불변. 반환값이 NFKC 정규화된다

- [ ] **Step 1: ja 컨텍스트 픽스처와 실패 테스트 2건을 쓴다**

`tests/test_signals_structural.py`의 기존 `ctx` 픽스처 아래에 추가:

```python
@pytest.fixture
def ctx_ja():
    """ko→ja. NumberMissing은 profile을 보지 않지만, 전각 숫자가
    일본어 자막의 현상이라는 것을 테스트가 스스로 설명하게 둔다."""
    return SignalContext(
        profile=load_builtin("ja"), glossary=None, source_lang="ko", target_lang="ja"
    )
```

파일 끝에 테스트 2건 추가:

```python
def test_number_missing_silent_on_fullwidth_digits(ctx_ja):
    """전각 숫자는 반각과 같은 수다. NFKC 정규화 없이 집합 비교하면
    '５０' != '50'이라 누락 판정되고, 두 자리라 multi_digit → **hard fail**이다.

    hard fail은 검수 예산을 우회하므로 이 오탐 하나가 실제 검수 비율을
    부풀려 Recall@Budget의 배수를 파괴한다. ja-ko 자연 오탐 41건 중
    13건(31.7%)이 이 경로였다.
    """
    sig = NumberMissing().collect(
        _seg("지금은 하루 50센트 이하입니다.", "今では一日５０セント以下になりました"), ctx_ja
    )
    assert sig is None


def test_number_missing_still_hard_when_number_truly_absent(ctx_ja):
    """정규화가 오탐을 없애면서 미탐을 만들면 안 된다.

    이 테스트가 없으면 위 테스트는 '검사를 껐다'로도 통과한다 —
    `_numbers`가 빈 리스트를 반환하게 만들어도 녹색이 된다.
    """
    sig = NumberMissing().collect(_seg("2023년 매출", "売上は好調でした"), ctx_ja)
    assert sig is not None
    assert sig.hard_fail is True
    assert sig.detail["missing"] == ["2023"]
```

- [ ] **Step 2: 실패를 확인한다**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_signals_structural.py -k fullwidth_digits -v
```

Expected: **FAIL** — `assert <Signal ...> is None`. 신호가 발화한다.

이어서 두 번째가 지금은 통과하는지 확인한다(수정 후에도 통과해야 의미가 있다):

```bash
.venv/Scripts/python.exe -m pytest tests/test_signals_structural.py -k truly_absent -v
```

Expected: **PASS**

- [ ] **Step 3: `unicodedata` 임포트를 추가한다**

`src/cuesift/signals/structural.py`의 임포트 블록(13~14행 `import re` 아래):

```python
import re
import unicodedata
from collections import Counter
```

- [ ] **Step 4: `_numbers()`를 고친다**

97~99행을 다음으로 교체:

```python
def _numbers(text: str) -> list[str]:
    """텍스트의 숫자를 천 단위 구분자를 제거하고 NFKC 정규화해 뽑는다.

    **정규화하지 않으면 전각과 반각이 다른 수가 된다** — `'５０' != '50'`이라
    일본어 자막의 정상 번역이 누락으로 판정되고, 두 자리 이상이라
    `multi_digit`에 걸려 hard fail이 난다. hard fail은 검수 예산을
    우회하므로(FR-6.2) 이 오탐이 실제 검수 비율을 부풀려 §9.1의 배수를
    파괴한다.

    **추출한 뒤에 정규화한다.** 텍스트 전체를 먼저 정규화하면 `½`(U+00BD,
    카테고리 No)가 `1⁄2`가 되어 **원문에 없던 숫자 1과 2가 생긴다.**
    `\\d`는 카테고리 Nd만 잡으므로 추출 후 정규화는 그 경로를 열지 않는다.

    **한계**: 한자 수사(`十代`)는 NFKC의 대상이 아니라 여전히 미탐이다.
    아라비아 매핑에는 파서가 필요하고 `十分に`(≠ 10분)·`万一`(≠ 10001) 같은
    관용구에서 hard fail 신호에 새 오탐을 만든다.
    """
    return [
        unicodedata.normalize("NFKC", m.group()).replace(",", "") for m in _NUMBER.finditer(text)
    ]
```

- [ ] **Step 5: 두 테스트가 모두 통과하는지 확인한다**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_signals_structural.py -v
```

Expected: 전부 PASS. 수집 개수가 이전보다 **2건 늘었는지** 확인한다.

- [ ] **Step 6: 전체 테스트와 린트**

Run:

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
```

Expected: `279 passed` (277 + 2) · ruff 통과

**기준은 277이다.** `HANDOFF.md`에 적힌 271은 낡았다 — 이후 커밋에서 6건이 늘었다. 착수 시점에 `pytest -q`를 먼저 돌려 기준을 확인한다.

- [ ] **Step 7: 커밋**

```bash
git add src/cuesift/signals/structural.py tests/test_signals_structural.py
git commit -m "수정: struct.number_missing이 전각 숫자를 놓치던 것 (A2)

_numbers()가 NFKC 정규화를 하지 않아 '５０' != '50'이었다. 집합 비교에서
누락 판정 → 두 자리라 multi_digit → hard fail. hard fail은 검수 예산을
우회하므로 이 오탐이 실제 검수 비율을 부풀려 Recall@Budget의 배수를
파괴한다. ja-ko 자연 오탐 41건 중 13건(31.7%)이 이 경로였다.

추출한 뒤에 정규화한다. 텍스트 전체를 먼저 정규화하면 ½가 1⁄2가 되어
원문에 없던 숫자가 생긴다.

회귀 테스트 2건. 두 번째(진짜 누락은 여전히 hard fail)가 없으면
첫 번째는 '검사를 껐다'로도 통과한다.

한자 수사(十代)는 여전히 미탐이다 — 주석에 한계로 남겼다."
```

---

## Task 2: A2 단독 측정

**Files:**

- Create: `data/bench/tmp/` (gitignore 대상 — 커밋하지 않는다)

**Interfaces:**

- Consumes: Task 1의 `src/cuesift/signals/structural.py`
- Produces: 중간 측정 숫자 (Task 5의 CHANGELOG 귀속 표에 실린다)

- [ ] **Step 1: 작업 트리가 깨끗한지 확인한다**

Run:

```bash
git status --short
```

Expected: 출력 없음. 미커밋 변경이 있으면 측정이 무엇을 잰 것인지 알 수 없다.

- [ ] **Step 2: en-ko 중간 측정**

Run:

```bash
.venv/Scripts/python.exe -m bench.run --pair en-ko --out-dir data/bench/tmp
```

**`--out-dir`를 반드시 준다.** 기본값이 `bench/results`라 그대로 돌리면 **커밋된 리포트를 덮어쓴다** — 직전 세션에서 실제로 일어난 사고다.

Expected: 예외 없이 완료. `리포트 -> data/bench/tmp/en-ko-....md`

`check_invariants`가 예외를 던지면 **거기서 멈춘다.** 숫자를 맞추려 불변식을 완화하지 않는다. 특히 불변식 1(Recall ≤ 오라클 상한)은 A2가 hard fail을 줄여 오라클 상한이 내려가므로 걸릴 수 있는 구조다.

- [ ] **Step 3: ja-ko 중간 측정**

Run:

```bash
.venv/Scripts/python.exe -m bench.run --pair ja-ko --out-dir data/bench/tmp
```

- [ ] **Step 4: 숫자를 기록한다**

두 리포트에서 다음 값을 스크래치패드에 옮겨 적는다. **파일은 커밋하지 않는다.**

| 항목 | 어디서 |
| --- | --- |
| 예산 10%의 실제 검수 비율 · Recall · 배수 | 예산 스윕 표 |
| hard fail 자연 오탐률 | "hard fail 오탐" 절 |
| `negation` Recall | 유형별 Recall 표 |
| ablation의 `length.ratio` 기여도 | 신호별 기여도 표 |

Task 5에서 이 값들이 CHANGELOG의 귀속 표가 된다.

- [ ] **Step 5: 커밋 없음**

이 태스크는 커밋을 만들지 않는다. `data/`는 gitignore 대상이므로 `git status`가 여전히 비어 있어야 한다.

Run:

```bash
git status --short
```

Expected: 출력 없음

---

## Task 3: A1 — noisy-or 융합

**Files:**

- Modify: `src/cuesift/risk/fuse.py:1-105`
- Test: `tests/test_risk_fuse.py`

**Interfaces:**

- Consumes: 없음
- Produces: `fuse(segment_id: str, signals: Sequence[Signal], weights: Mapping[str, float] | None = None) -> SegmentRisk` — **시그니처 불변.** `risk_score` 계산만 바뀐다

- [ ] **Step 1: 가중 평균을 고정하는 기존 테스트 2건을 교체한다**

`tests/test_risk_fuse.py`의 24~33행을 다음으로 교체:

```python
def test_multiple_signals_combine_by_noisy_or():
    """1 - ∏(1 - sᵢ)^wᵢ. 평균이 아니다.

    평균을 쓰면 **문제를 하나 더 찾을 때 위험도가 내려간다** —
    0.8짜리 신호에 0.2짜리가 붙으면 0.5가 되어 뒤로 밀린다.
    트리아지는 '평균적으로 얼마나 나쁜가'가 아니라
    '적어도 하나가 진짜일 가능성'을 원한다.
    """
    r = fuse("s1", [_sig("a", 0.2), _sig("b", 0.8)], weights={"a": 1.0, "b": 1.0})
    assert r.risk_score == pytest.approx(0.84)


def test_weights_enter_as_exponents():
    """가중치는 지수다 — `(1 - s)^w`는 '이 신호를 w번 관측했다'로 읽힌다.

    점수 스케일 가중(`1 - ∏(1 - w·s)`)을 쓰면 `w·s > 1`에서
    `(1 - w·s)`가 음수가 되어 곱의 부호가 뒤집힌다. `w ≤ 1` clamp를
    강제해야 하고 그러면 **신호 강화를 표현할 수 없다.**

    두 신호 모두 0.5일 때 균등 가중이면 1 - 0.5·0.5 = 0.75인데,
    b에 3을 주면 1 - 0.5·0.5³ = 0.9375가 된다.
    """
    r = fuse("s1", [_sig("a", 0.5), _sig("b", 0.5)], weights={"a": 1.0, "b": 3.0})
    assert r.risk_score == pytest.approx(0.9375)
```

- [ ] **Step 2: 실패를 확인한다**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_risk_fuse.py -k "noisy_or or exponents" -v
```

Expected: **2 failed** — `0.5 != 0.84`, `0.75 != 0.9375`

- [ ] **Step 3: 신규 테스트 3건을 추가한다**

`tests/test_risk_fuse.py` 파일 끝에 추가:

```python
def test_adding_a_signal_never_lowers_the_score():
    """**이 프로젝트에서 가중 평균을 버린 이유다.**

    문제를 하나 더 찾았는데 위험도가 내려가면 트리아지가 거꾸로 간다.
    규격 위반 3건(1.0)에 용어 위반 1건(0.5)을 더하면 평균은 0.75가 된다.
    """
    one = fuse("s1", [_sig("spec.violation", 1.0)]).risk_score
    two = fuse("s1", [_sig("spec.violation", 1.0), _sig("glossary.miss", 0.5)]).risk_score
    assert two >= one
    assert two == pytest.approx(1.0)


def test_a_single_certain_signal_saturates():
    """s=1.0이면 `(1-1.0)^w = 0`이라 다른 신호와 무관하게 1.0이다.

    확정 위반 하나를 다른 신호가 희석하지 못한다는 것이 산식의 요점이다.
    """
    r = fuse("s1", [_sig("a", 1.0), _sig("b", 0.1), _sig("c", 0.0)])
    assert r.risk_score == pytest.approx(1.0)


def test_weight_above_one_strengthens_a_single_signal():
    """`w>1`은 밑을 더 작게 만들어 점수를 올린다 — `1 - 0.4² = 0.84`.

    가중 평균에서는 단일 신호의 가중치가 분자와 분모에서 약분돼
    **어떤 w를 줘도 0.6이었다.** 즉 이 테스트는 지수 가중이 아니면
    통과할 수 없다.
    """
    r = fuse("s1", [_sig("a", 0.6)], weights={"a": 2.0})
    assert r.risk_score == pytest.approx(0.84)
```

- [ ] **Step 4: 3건 모두 실패하는지 확인한다**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_risk_fuse.py -k "never_lowers or saturates or strengthens" -v
```

Expected: **3 failed**

현재 코드의 실측값은 다음과 같다(계획 작성 시 확인). 다르게 나오면 입력을 다시 본다.

| 테스트 | 현재 코드 | 기대 (noisy-or) |
| --- | --- | --- |
| `test_multiple_signals_combine_by_noisy_or` | `0.5` | `0.84` |
| `test_weights_enter_as_exponents` | `0.5` | `0.9375` |
| `test_adding_a_signal_never_lowers_the_score` | `1.0` → `0.75` (감소) | `1.0` → `1.0` |
| `test_a_single_certain_signal_saturates` | `0.3666666666666667` | `1.0` |
| `test_weight_above_one_strengthens_a_single_signal` | `0.6` | `0.84` |

- [ ] **Step 5: 산식을 교체한다**

`src/cuesift/risk/fuse.py`의 모듈 독스트링(1~10행)을 교체:

```python
"""신호 융합 (요구사항정의서 FR-6.1, FR-6.2, FR-6.4).

**noisy-or다.** `1 - ∏(1 - sᵢ)^wᵢ`로 "적어도 하나가 진짜일 가능성"을 낸다.

가중 평균을 쓰면 **문제를 하나 더 찾을 때 위험도가 내려간다** —
규격 위반(1.0) 하나는 1.0인데 용어 위반(0.5)이 붙으면 0.75가 되어
검수 큐에서 밀려난다. 실측으로 그 비용을 쟀다: 오라클 대비 달성률이
예산 10%에서만 69.6%로 꺼졌고(1~5% 86.4%, 20~30% 86~89%), 그 구간이
바로 이 프로젝트의 기본 운영점이다.

**가중치는 지수로 들어간다.** `(1 - s)^w`는 "이 신호를 w번 독립적으로
관측했다"로 읽히므로 `w=0`이 "관측하지 않음"이 되어 ablation의 신호 끄기와
의미가 일치한다. 점수 스케일 가중(`1 - ∏(1 - w·s)`)은 `w·s > 1`에서
`(1 - w·s)`가 음수가 되어 곱의 부호를 뒤집으므로 쓰지 않는다.

**가중치는 튜닝하지 않는다**(스펙 §6.3). 같은 데이터에서 맞춘 값은
새 데이터에서 재현되지 않는다.
"""
```

`total_weight` 계산부터 `score` 산출까지(71~97행)를 교체:

```python
    # 산식이 총합을 쓰지 않으므로 이 값은 **검증 전용**이다.
    total_weight = sum(table.get(s.name, _FALLBACK_WEIGHT) for s in signals)

    # 개별 값이 전부 유한해도 합계는 넘칠 수 있다(`1e308` 두 개).
    #
    # **noisy-or에서는 이 경로로 점수가 뒤집히지 않는다** — 곱셈이라
    # `inf`가 산식에 들어오지 않는다. 가중 평균 시절의 D-22가 막던 실패
    # 모드는 사라졌다. 그럼에도 막는 것은 `inf` 총합이 설정 오타의
    # 신호이기 때문이다. 조용히 삼키면 사용자가 오타를 모른다.
    if not math.isfinite(total_weight):
        raise ValueError(f"가중치 총합이 유한하지 않다: {total_weight}")

    if total_weight <= 0:
        # 신호가 없으면 위험도 0은 옳다 — 판단할 것이 없다.
        # 그러나 신호가 있는데 총합이 0이면 설정이 모든 신호를 죽인 것이고,
        # 모든 `w=0`은 `(1-s)^0 = 1`이라 곱이 1, 점수가 0.0이 된다.
        # 조용히 0.0을 내면 **전체 세그먼트가 안전 판정된다.**
        if signals:
            raise ValueError(
                f"가중치 총합이 0이다. 설정이 이 세그먼트의 신호를 전부 무효화했다: "
                f"{sorted(s.name for s in signals)}"
            )
        score = 0.0
    else:
        # 1 - ∏(1 - sᵢ)^wᵢ
        #
        # **범위 정합성이 `Signal.score ∈ [0, 1]`에 의존한다.** 밑 `(1 - sᵢ)`가
        # [0, 1]이고 지수 `wᵢ ≥ 0`이면 거듭제곱도 [0, 1]이고 그 곱도 [0, 1]이다.
        # 점수 범위 검증이 `Signal`에서 사라지면 여기가 조용히 깨진다 —
        # 가중 평균 시절에는 아래 clamp가 마지막 방어선이었지만 이제는
        # 산식 자신이 보장하고, 그 보장이 상류 모델을 전제로 한다.
        product = 1.0
        for s in signals:
            product *= (1.0 - s.score) ** table.get(s.name, _FALLBACK_WEIGHT)
        score = 1.0 - product
```

`min(1.0, max(0.0, score))` clamp는 **그대로 둔다** — 부동소수점 여유이며 비용이 없다.

- [ ] **Step 6: 신규·교체 테스트가 전부 통과하는지 확인한다**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_risk_fuse.py -v
```

Expected: 전부 PASS. 특히 **손대지 않은 다음 6건이 여전히 통과해야 한다** — 이들이 기존 계약이다.

| 테스트 | 왜 통과해야 하나 |
| --- | --- |
| `test_hard_fail_forces_max_risk` | FR-6.2 우회는 산식 밖이다 |
| `test_zero_weight_on_one_signal_is_allowed` | `w=0`은 두 산식이 일치한다 |
| `test_all_zero_weights_is_a_configuration_error` | 총합 0 방어 유지 |
| `test_weight_sum_overflow_is_rejected` | D-22 검사 유지 |
| `test_large_but_finite_weight_sum_is_allowed` | `0.0 ** 1e307 = 0.0` |
| `test_risk_score_stays_normalized` | 산식이 [0,1]을 보장 |

- [ ] **Step 7: 전체 테스트와 린트**

Run:

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
```

Expected: `282 passed` (279 + 3) · ruff 통과

**벤치 테스트가 깨지면 멈춘다.** `tests/test_bench_measure.py:66`이 `fuse()`를 직접 호출한다(계획 작성 시 확인). 융합 결과에 의존하는 기대값을 갖고 있을 수 있다 — 있다면 그 테스트가 무엇을 계약으로 삼는지 읽고, 산식과 무관한 계약이면 입력을 고치고 산식에 의존하는 계약이면 교체한다. **기대값만 새 숫자로 바꾸지 않는다.**

- [ ] **Step 8: 커밋**

```bash
git add src/cuesift/risk/fuse.py tests/test_risk_fuse.py
git commit -m "변경: 위험도 융합을 가중 평균에서 noisy-or로 (A1)

가중 평균은 문제를 하나 더 찾을 때 위험도를 내렸다 — 규격 위반(1.0)
하나는 1.0인데 용어 위반(0.5)이 붙으면 0.75가 되어 검수 큐에서
밀려난다. 벤치가 그 비용을 쟀다: 오라클 대비 달성률이 예산 10%에서만
69.6%로 꺼지고, 그 구간이 기본 운영점이다.

가중치는 지수로 들어간다. (1-s)^w는 '이 신호를 w번 관측했다'로
읽혀 w=0이 ablation의 신호 끄기와 의미가 일치한다. 점수 스케일
가중은 w·s > 1에서 곱의 부호가 뒤집혀 w ≤ 1 clamp를 강제하고,
그러면 신호 강화를 표현할 수 없다.

FR-6.1은 '설정 가능한 가중치'만 요구하고 가중 평균을 명시하지
않으므로 요구사항 변경이 없다. hard fail 우회(FR-6.2)와 reasons
산출(FR-6.4)도 그대로다.

D-22(총합 유한성) 검사는 남기되 주석을 정정했다. noisy-or는 곱셈이라
그 실패 모드가 없지만 inf 총합은 설정 오타의 신호다.

가중 평균을 값으로 고정하던 기존 테스트 2건을 교체하고 신규 3건을
추가했다. 5건 전부 수정 전 코드에서 실패를 확인했다."
```

---

## Task 4: 최종 측정과 리포트 갱신

**Files:**

- Modify: `bench/results/en-ko-2026-07-29.{md,json}` · `bench/results/ja-ko-2026-07-29.{md,json}`

**Interfaces:**

- Consumes: Task 1·3의 제품 코드
- Produces: 최종 실측 숫자 (Task 5의 README·CHANGELOG에 실린다)

- [ ] **Step 1: 작업 트리가 깨끗한지 확인한다**

Run:

```bash
git status --short
```

Expected: 출력 없음

- [ ] **Step 2: 두 언어쌍을 기본 출력 경로로 측정한다**

Run:

```bash
.venv/Scripts/python.exe -m bench.run --pair en-ko
.venv/Scripts/python.exe -m bench.run --pair ja-ko
```

이번에는 `--out-dir`를 주지 **않는다** — 커밋될 리포트를 만드는 것이 목적이다.

Expected: `리포트 -> bench/results/en-ko-2026-07-29.md` (날짜가 오늘이 아니면 Step 5를 본다)

- [ ] **Step 3: 변화를 읽는다**

Run:

```bash
git diff --stat bench/results/
git diff bench/results/en-ko-2026-07-29.md
```

**숫자가 하나도 안 바뀌었다면 의심한다.** 두 변경 모두 예산 10%의 결과에 도달하는 경로가 있으므로 완전 무변화는 측정이 옛 코드를 돌렸다는 신호일 수 있다.

- [ ] **Step 4: 리포트의 서술이 새 숫자와 어긋나는지 확인한다**

리포트에는 자동 생성되지 않는 서술이 있다. 특히 다음 둘을 읽는다.

| 절 | 무엇을 확인 |
| --- | --- |
| hard fail 오탐 원인 목록 | A2가 NFKC 경로를 제거했으므로 **원인 구성이 바뀐다.** 한자 수사(ja-ko 4건)가 이제 목록에 있어야 한다 |
| ablation 음수값 설명 | `length.ratio`가 여전히 음수인지, 값이 얼마나 바뀌었는지 |

서술이 `bench/report.py`의 상수라면 그 파일을 고치고 **리포트를 다시 생성한 뒤** Step 5로 간다.

- [ ] **Step 5: 파일명이 오늘 날짜인지 확인한다**

리포트 파일명은 `date.today()`로 만들어진다. 자정을 넘겨 새 파일이 생겼다면 **옛 파일을 지우고** 문서 참조를 새 이름으로 갱신한다. 같은 측정의 리포트를 두 개 남기지 않는다.

Run:

```bash
ls bench/results/
```

Expected: 파일 4개 (`{en-ko,ja-ko}-2026-07-29.{md,json}`)

- [ ] **Step 6: 리포트만 커밋한다**

코드 변경과 분리한다 — `report.py`를 고치는 커밋에서 리포트를 함께 커밋하면 **스탬프가 직전 커밋을 가리키고, 그 커밋의 `report.py`는 방금 고친 옛 서술을 렌더링한다**(I-1).

```bash
git add bench/results/
git commit -m "측정: noisy-or·NFKC 적용 후 TED2020 재측정

A2(NFKC)와 A1(noisy-or)을 적용한 뒤의 실측. 시드·코퍼스·트랙은
이전 측정과 동일하다(seed=20260729).

채택은 이 결과에 걸려 있지 않았다 — 스펙 §1.1이 정한 대로 재측정의
목적은 판정이 아니라 기록이다."
```

---

## Task 5: 문서 갱신

**Files:**

- Modify: `README.md` · `CHANGELOG.md` · `HANDOFF.md` · `docs/WBS.md` · `docs/요구사항정의서.md`

**Interfaces:**

- Consumes: Task 2의 중간 숫자, Task 4의 최종 숫자
- Produces: 없음 (마지막 태스크)

- [ ] **Step 1: README 최상단 실측 절을 갱신한다**

배수·Recall·실제 검수 비율을 Task 4의 값으로 바꾼다. **헤드라인은 예산 10%에서 뽑는다** — 요청 예산과 실제 검수 비율이 일치하는 첫 지점이다. 더 낮은 예산의 배수가 더 커도 인용하지 않는다.

배수가 **내려갔다면 그 숫자를 그대로 싣고** 한계 절에 이유를 적는다. 스펙 §1.1의 결정이다.

- [ ] **Step 2: CHANGELOG에 귀속 표를 넣는다**

`### Changed` 아래에 A1·A2 항목을 쓰고, 다음 표를 포함한다:

```markdown
| 구성 | en-ko 예산 10% Recall | ja-ko | hard fail 오탐률 (en/ja) |
| --- | --- | --- | --- |
| 기준 (가중 평균 · 정규화 없음) | 69.60% | 70.60% | 0.96% / 0.91% |
| + A2 (NFKC) | (Task 2 값) | (Task 2 값) | (Task 2 값) |
| + A1 (noisy-or) | (Task 4 값) | (Task 4 값) | (Task 4 값) |
```

**두 변경의 효과가 반대 방향일 수 있으므로 중간 행이 없으면 "변화 없음"으로 오독된다.** 이 표가 2단계 측정의 산출물이다.

- [ ] **Step 3: HANDOFF의 미결 항목을 닫는다**

| 항목 | 조치 |
| --- | --- |
| "In Progress / Pending" 5번 (`_NUMBER` NFKC) | 완료로 표시 |
| "In Progress / Pending" 6번 (융합 방식) | 완료로 표시 |
| Known Issues "전각 숫자" 절 | 취소선 + **해결** |
| Known Issues "한자 수사가 리포트 원인 목록에 없다" | Task 4 Step 4에서 처리됐다면 **해결** |
| Known Issues "가중 평균 융합의 구조적 문제" | 취소선 + **해결**, 남은 `length.ratio` −6.0%p는 별도 항목으로 분리 |

- [ ] **Step 4: WBS의 WP3을 완료로 바꾼다**

`docs/WBS.md`에서:

- 진척 막대 `WP3 융합·검출 정정  ████░░░░ 🔨` → `████████████████████ ✅`
- 작업 패키지 표의 WP3 상태 → ✅, 산출물 열에 실제 경로
- "현재 위치"의 완료 개수와 백분율 — WP3은 FR-6.1·3.4의 **정정**이지 신규 FR이 아니므로 **분모·분자는 바뀌지 않는다.** 16/42(38%) 유지
- "다음 작업 순서" 표에서 WP3 행 제거, WP4가 1순위

- [ ] **Step 5: 요구사항정의서 §12 Q4의 인용 수치를 확인한다**

Q4의 근거 문장이 `negation` Recall 1.41%와 무작위 기준선 9.61%를 인용한다. **두 값 모두 이번 변경의 영향을 받는다.**

Run:

```bash
grep -n "1.41\|9.61\|9.86\|11.27\|19.49\|19.85" docs/요구사항정의서.md
```

Task 4의 리포트 값과 다르면 갱신한다. **갱신하면 조사 문서 §5도 함께 본다** — 두 문서가 서로를 링크하므로 한쪽만 고치면 독자가 상반된 두 주장을 만난다.

- [ ] **Step 6: 문서 게이트**

Run:

```bash
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

Expected: 두 도구의 **파일 개수가 일치**하는지 확인한다. 링크 체커는 `git ls-files` 기준이라 미추적 파일을 건너뛴다 — 새 파일을 만들었다면 `git add` 후 다시 돌린다.

- [ ] **Step 7: 최종 확인**

Run:

```bash
.venv/Scripts/python.exe -m pytest -q
git status --short
```

Expected: `282 passed` · 문서 파일만 미커밋

- [ ] **Step 8: 커밋**

```bash
git add README.md CHANGELOG.md HANDOFF.md docs/WBS.md docs/요구사항정의서.md
git commit -m "문서: noisy-or·NFKC 재측정 결과 반영

README 최상단 배수를 갱신하고 CHANGELOG에 A2 단독 / A1+A2 귀속 표를
넣었다. 두 변경의 효과가 반대 방향일 수 있어 중간 행이 없으면
'변화 없음'으로 오독된다.

HANDOFF의 미결 항목 둘(NFKC 재측정, 융합 방식)을 닫고 WBS의 WP3을
완료로 표시했다. WP3은 기존 FR의 정정이라 완료 개수는 16/42 그대로다.

요구사항정의서 §12 Q4의 인용 수치를 리포트 실측과 동기화했다."
```

**푸시하지 않는다.**

---

## 실행 방식

| 태스크 | 분할 | 이유 |
| --- | --- | --- |
| 0 | 컨트롤러 | 문서 한 곳 수정 |
| 1 · 3 | **병렬 구현자 2명** | 파일이 겹치지 않는다(`structural.py` / `fuse.py`) |
| 2 · 4 | **컨트롤러** | 측정은 순서가 있고 작업 트리 상태에 민감하다 |
| 5 | 컨트롤러 | 두 측정의 숫자를 다 가진 쪽이 쓴다 |

Task 1·3을 병렬로 돌릴 경우 **커밋 순서를 강제해야 한다** — Task 2의 중간 측정은 A2만 적용된 상태를 재야 하므로, Task 3의 커밋이 Task 2보다 먼저 들어가면 A/B가 무너진다. 병렬로 구현하되 **Task 3은 Task 2가 끝난 뒤에 커밋**한다. 순서가 헷갈릴 것 같으면 순차로 돌린다 — 두 태스크 합쳐 30줄이라 병렬 이득이 작다.

### 리뷰 축

한 명에게 전부 맡기면 "계획대로 구현됨"으로 승인한다. **축을 나누고 검증 방법까지 지정한다.**

| 리뷰어 | 축 | 반드시 실행할 것 |
| --- | --- | --- |
| R1 | **산식 정합성** | 신규 테스트 5건을 수정 전 코드(`git stash` 금지 — `git show HEAD~1:src/...`로 읽는다)에서 실제로 실패시켜 재현. 통과하는 것이 있으면 그 테스트는 게이트가 아니다 |
| R2 | **기존 계약 파괴** | `fuse()`를 호출하는 모든 지점을 찾아(`grep -rn "fuse(" src bench tests`) 각 호출자의 기대가 유지되는지 확인. 특히 `bench/measure.py`와 `bench/run.py` |
| R3 | **측정 무결성** | 중간·최종 리포트의 시드·커밋 스탬프·코퍼스 SHA-256이 이전 측정과 같은지 대조. 다르면 A/B가 성립하지 않는다 |

**리뷰어에게 금지할 것**: 코드를 수정하지 마라 · `git stash`를 쓰지 마라 · `bench.run`을 기본 `--out-dir`로 돌리지 마라. 앞 두 개는 다른 사람의 작업을 가져가고, 마지막은 커밋된 리포트를 덮어쓴다. **셋 다 직전 세션에서 실제로 일어났다.**

리뷰어는 **실측과 추측을 구분해 표기한다.** "코드를 읽어 보니 괜찮다"는 검증이 아니다.

## 자체 검토 결과

| 검사 | 결과 |
| --- | --- |
| 스펙 커버리지 | §3(A1)→Task 3 · §4(A2)→Task 1 · §5(테스트)→Task 1·3 · §6(측정)→Task 2·4 · §6.3(문서)→Task 5 · §7(비목표)→각 태스크에 반영 |
| 플레이스홀더 | 없음. CHANGELOG 표의 `(Task N 값)`은 측정 전에 알 수 없는 값이며 출처를 명시했다 |
| 타입 일관성 | `fuse()`·`_numbers()` 시그니처 불변. `ctx_ja` 픽스처는 Task 1에서 정의하고 같은 태스크에서만 쓴다 |
| 스펙과의 불일치 | **1건 발견** — §5 표의 `w=0` 행. Task 0에서 정정한다 |
| 기대값 실측 | 신규·교체 테스트 5건의 현재 코드 반환값을 **실제로 실행해 확인했다.** 5건 모두 예측대로 실패한다 |
| 기준 테스트 수 | **277** (`HANDOFF.md`의 271은 낡음). 착수 시 재확인 |
| `fuse()` 호출자 | `bench/measure.py:52` · `bench/run.py:128` · `tests/test_bench_measure.py:66` — 셋 다 R2 리뷰 대상 |
