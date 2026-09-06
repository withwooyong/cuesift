# Tier 1 후보 선정 재설계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tier 1 후보를 Tier 0 위험도 대신 극성 표지 보유 여부로 우선 선정해, 역번역 신호가 의미 반전을 볼 기회를 만든다.

**Architecture:** 극성 표지 판정은 새 최상위 모듈 `cuesift/polarity.py` 가 맡고, `triage/` 는 언어와 자막 본문을 모르는 채로 남는다. `tier1.py` 가 둘을 연결해 후보 ID 집합을 `select_tier1_candidates` 의 새 `priority_ids` 인자로 넘긴다. 극성 표지는 `risk_score` 에 기여하지 않는다.

**Tech Stack:** Python 3.11+ · `re`(표준 라이브러리) · `pytest` · `ruff`

**Spec:** [`docs/superpowers/specs/2026-09-06-tier1-candidate-selection-design.md`](../specs/2026-09-06-tier1-candidate-selection-design.md)

## Global Constraints

- Python 실행은 반드시 `.venv/Scripts/python.exe` 를 쓴다. 시스템 Python 은 3.14 라 다르다.
- 모든 모듈 첫 줄에 `from __future__ import annotations` 를 넣는다.
- 독스트링과 주석은 **한국어**이고 근거 FR·§ 번호를 병기한다.
- 주석에는 "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"** 를 적는다.
- ruff: `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`.
- 커밋 메시지는 **한국어**. 커밋과 푸시를 한 명령에 묶지 않는다.
- 의존성을 추가하지 않는다. 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개(`pytest`·`pytest-cov`·`ruff`) 고정이다.
- 출력 문자열에 em dash(`—`)를 쓰지 않는다. cp949 로 인코딩되지 않는다. `-` 를 쓴다.
- `DEFAULT_WEIGHTS` 를 건드리지 않는다. 점수 스케일로 몰래 가중을 넣지 않는다.
- 로컬 게이트 5종은 CI 와 대상이 같아야 한다. **`src tests` 로 좁히지 않는다.**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

- 문서를 추가했으면 **`git add` 뒤에** 링크 체커를 돌리고, `check_links.py` 와 markdownlint 의 파일 개수가 같은지 확인한다. 현재 양쪽 53개다.

## 파일 구조

| 파일 | 책임 | 상태 |
| --- | --- | --- |
| `src/cuesift/polarity.py` | 언어별 극성 표지 정규식과 판정 술어. 점수를 내지 않는다 | 신설 |
| `src/cuesift/triage/policy.py` | `select_tier1_candidates` 에 `priority_ids` 통로 추가. 언어를 계속 모른다 | 수정 |
| `src/cuesift/triage/__init__.py` | 공개 API 재수출 | 확인만 |
| `src/cuesift/tier1.py` | 극성 판정 호출 · 미지원 언어 경고 · `CandidateReport` 콜백 | 수정 |
| `bench/report.py` | 후보 구성 표 렌더러 | 수정 |
| `bench/run.py` | `on_candidates` 배선과 라벨 대조 | 수정 |
| `tests/test_polarity.py` | 표지 판정 게이트 | 신설 |
| `tests/test_triage_policy.py` | `priority_ids` 게이트와 기존 회귀 | 수정 |
| `tests/test_tier1.py` | 배선과 경고 게이트 | 수정 |
| `tests/test_bench_report.py` | 후보 구성 표 게이트 | 수정 |

---

### Task 1: 극성 표지 판정 모듈

**Files:**

- Create: `src/cuesift/polarity.py`
- Test: `tests/test_polarity.py`

**Interfaces:**

- Consumes: 없음 (표준 라이브러리 `re` 만 쓴다)
- Produces:
  - `has_polarity_marker(text: str | None, lang: str) -> bool`
  - `supported_languages() -> frozenset[str]`

**어휘 목록은 설계 §3.3 이 실측한 것과 같아야 한다.** 아래 패턴은 농축 배수 3.3~4.6x 를 낸 바로 그 목록이다. **벤치 수치를 보고 조정하지 않는다**(설계 D3).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_polarity.py` 를 새로 만든다.

```python
"""극성 표지 판정 테스트 (설계 2026-09-06 §4.1 · D4)."""

import pytest

from cuesift.polarity import has_polarity_marker, supported_languages


def test_지원_언어는_셋이다():
    assert supported_languages() == frozenset({"ko", "en", "ja"})


def test_None과_빈_문자열은_표지가_없다():
    assert not has_polarity_marker(None, "ko")
    assert not has_polarity_marker("", "ko")
    assert not has_polarity_marker("   ", "ko")


def test_미지원_언어는_항상_False다():
    # 프랑스어 부정문이지만 목록이 없으므로 판정하지 않는다 (D5).
    assert not has_polarity_marker("Je ne sais pas", "fr")


@pytest.mark.parametrize(
    "text",
    ["그렇지 않습니다", "아무도 없다", "그건 아니라고 봅니다", "안 갔어요", "못 했습니다"],
)
def test_한국어_표지를_잡는다(text):
    assert has_polarity_marker(text, "ko")


def test_못은_띄어쓰기_변이를_모두_잡는다():
    # `못하` 와 `못\s` 가 각각 한쪽을 맡는다. 하나만 있으면 반쪽이 샌다.
    assert has_polarity_marker("못했습니다", "ko")
    assert has_polarity_marker("못 했습니다", "ko")


@pytest.mark.parametrize(
    "text",
    ["I can't do that", "It is NOT true", "there is no way", "never mind", "without doubt"],
)
def test_영어_표지를_잡는다(text):
    assert has_polarity_marker(text, "en")


def test_영어는_대소문자를_가리지_않는다():
    assert has_polarity_marker("NEVER", "en")


def test_영어_단어_경계가_있다():
    # `no` 가 `nostalgia` 를 물면 안 된다.
    assert not has_polarity_marker("nostalgia", "en")


@pytest.mark.parametrize("text", ["わかりません", "行かなかった", "それはない", "行かずに"])
def test_일본어_표지를_잡는다(text):
    assert has_polarity_marker(text, "ja")


def test_しか는_부정_호응이라_표지다():
    # 다른 표지가 섞이지 않은 문장이어야 `しか` 자체를 검사한다.
    assert has_polarity_marker("残りは一つしか", "ja")


def test_접속사_しかし는_표지가_아니다():
    # CJK 는 단어 경계가 없어 `しか` 가 `しかし` 를 문다 (D4 · CLAUDE.md 실측 전례).
    assert not has_polarity_marker("しかし、それは違います", "ja")


def test_개행이_표지를_숨기지_못한다():
    # 자막은 어절 중간에서 줄바꿈된다 (D4 · 이월 19번 실측).
    assert has_polarity_marker("かもし\nれません", "ja")
    assert has_polarity_marker("그렇지\n않습니다", "ko")


def test_어휘_목록을_테스트가_지어_넘기지_않는다():
    # 실제 패턴을 임포트해 검사한다. 테스트 안에서 만든 정규식을 검사하면
    # 코드와 갈라져도 통과한다 (리포트 caveat 전례).
    from cuesift.polarity import _PATTERNS

    assert set(_PATTERNS) == supported_languages()
    assert _PATTERNS["ja"].search("残りは一つしか") is not None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_polarity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.polarity'`

- [ ] **Step 3: 모듈을 구현한다**

`src/cuesift/polarity.py` 를 새로 만든다.

```python
"""극성 표지 판정 (설계 2026-09-06 §4 · FR-4.3).

**이 모듈은 점수를 내지 않는다.** 반환형이 `bool` 인 것은 그 계약이다 -
실수를 내면 그 값이 언젠가 `risk_score` 에 흘러들어 "가중치는 튜닝하지
않는다" 규율을 우회하게 된다. 여기가 정하는 것은 **누가 비싼 Tier 1
검사를 받는가**이지 **얼마나 위험한가**가 아니다 (설계 D9).

**`signals/` 가 아니라 최상위에 있는 이유**는 레지스트리 수집기로 오인되지
않기 위해서다 - `signals/` 에 두면 `collect_all` 이 돌리려 하고, 점수를
내지 않는 술어라는 성질이 흐려진다 (설계 D1).
"""

from __future__ import annotations

import re

# **여기 적힌 목록이 설계 §3.3 의 농축 배수 3.3~4.6x 를 낸 바로 그 목록이다.**
# 벤치 수치를 보고 조정하면 같은 데이터에서 맞춘 가중치와 성질이 같아진다
# (설계 D3). 목록을 바꾸려면 근거가 언어학적 사실이어야 하고, 바꾼 뒤에는
# §3.3 의 표를 다시 재야 한다.
#
# **외부 YAML 로 빼지 않는다** (설계 §4.2). 용어집은 사용자가 프로젝트마다
# 바꾸는 자료지만 극성 표지는 언어의 성질이다. 파일로 두면 D3 을 어기는
# 문턱이 낮아진다.
_PATTERNS: dict[str, re.Pattern[str]] = {
    # 한국어는 이 프로젝트에서 언제나 원문 언어다 (§12 Q2).
    #
    # **`못`·`안` 을 단독으로 넣으면 무엇이 깨지는가.** 두 글자는 각각
    # 명사 「못」(nail)·「안」(inside)과 같은 형태라 「못을 박다」·「집 안에」가
    # 전부 표지로 잡힌다. 그래서 `못하`(못하다)와 `못\s`(못 했다)로 쪼개고,
    # `안` 은 뒤에 공백이 오는 부사 용법만 받는다.
    "ko": re.compile(r"않|없|아니|못하|못\s|안\s|지\s*말|말고|아냐|아닙"),
    # **`\b` 단어 경계가 없으면 무엇이 깨지는가.** `no` 가 `nostalgia`·`note` 를
    # 물어 영문 자막의 상당수가 표지 보유로 잡힌다. 영어는 CJK 와 달리
    # `\b` 가 정상 동작하므로 여기서는 쓴다 - 같은 것을 ja 에 쓰면 전부 깨진다.
    "en": re.compile(
        r"\bnot\b|n't\b|\bno\b|\bnever\b|\bnothing\b|\bnone\b|\bneither\b|\bnor\b"
        r"|\bwithout\b|\bunable\b|\bfail|\bhardly\b|\brarely\b|\bseldom\b|\bcannot\b",
        re.IGNORECASE,
    ),
    # **CJK 에는 단어 경계가 없다.** `\b` 를 쓰면 전부 깨지므로, 대신
    # **뒤따르는 글자를 배제**한다 - 이것이 이 언어에서 쓸 수 있는 경계다
    # (CLAUDE.md 실측: 경계 없는 「しか」가 접속사 「しかし」를 5건 물었다).
    #
    # `無[^限]` 은 「無限」(무한)을, `不[^思]` 는 「不思議」(신기하다)를 뺀다.
    # 둘 다 부정이 아니라 어휘의 일부다.
    #
    # **`非`·`未` 가 잡는 오탐은 알려진 비용이다** - 「非常に」(매우)·
    # 「未来」(미래)가 걸린다. 후보 선정용 술어라 정밀도보다 재현율이
    # 중요하므로 남긴다. 점수에 기여하지 않으므로 오탐의 대가는
    # Tier 1 호출 몇 건이지 검수 큐 오염이 아니다.
    "ja": re.compile(
        r"ない|なかっ|なく|ません|ませんでし|ずに|ないで|ではな|じゃな|しか(?!し)"
        r"|決して|全く|一度も|無[^限]|不[^思]|非|未"
    ),
}


def supported_languages() -> frozenset[str]:
    """극성 표지 목록이 있는 언어 (설계 D5).

    **판정을 다 돌린 뒤 "전부 False 였다"로 알면 안 되기 때문에 따로 있다** -
    미지원 언어와 "표지가 정말 없다"가 구분되지 않으면 무음 열화가 된다(§12 Q3).
    호출자는 이 집합으로 **판정 전에** 경고를 낼 수 있다.
    """
    return frozenset(_PATTERNS)


def has_polarity_marker(text: str | None, lang: str) -> bool:
    """`text` 에 `lang` 의 부정 극성 표지가 있는가 (설계 §4.1).

    **판정은 개행을 지운 사본에 한다** (설계 D4). 자막은 화면 폭에 맞춰
    어절 중간에서 줄바꿈되므로, 원본에 그대로 걸면 `かもし`+개행+`れません`
    이 통과한다 (이월 19번 실측: 표기 변이까지 합쳐 4건이 정답지에 들어갔다).
    판정은 boolean 이라 위치가 어긋날 걱정이 없다.

    미지원 언어는 예외가 아니라 `False` 다 - 호출자가 `supported_languages()`
    로 미리 경고를 내고 나면, 여기서 다시 터뜨리는 것은 같은 사실을 두 번
    말하면서 파이프라인만 세운다.
    """
    if not text:
        return False
    pattern = _PATTERNS.get(lang)
    if pattern is None:
        return False
    return pattern.search(text.replace("\n", "").replace("\r", "")) is not None
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_polarity.py -v`
Expected: PASS (수집 개수 20건 이상. **0건 수집은 통과가 아니라 설정 오류다**)

- [ ] **Step 5: 각 게이트가 실제로 실패하는지 확인한다**

게이트는 만들었다고 게이트가 아니다. 아래 스크립트를 스크래치 디렉터리에 두고 돌린다. **복원은 `finally` 에 둔다** - 파괴 실험은 정의상 리포를 망가뜨린 채 도는 코드라, 정상 경로에만 복원을 두면 실패한 실험이 작업트리를 변이 상태로 남긴다.

```python
import subprocess
from pathlib import Path

TARGET = Path("src/cuesift/polarity.py")
MUTATIONS = [
    ('しか(?!し)', 'しか', "test_접속사_しかし는_표지가_아니다"),
    ('.replace("\\n", "")', "", "test_개행이_표지를_숨기지_못한다"),
    ("|못하|못\\s", "|못하", "test_못은_띄어쓰기_변이를_모두_잡는다"),
    ("\\bno\\b", "no", "test_영어_단어_경계가_있다"),
]

original = TARGET.read_text(encoding="utf-8")
try:
    for old, new, testname in MUTATIONS:
        # **치환 전에 유일성을 단언한다.** 같은 패턴이 파일 앞쪽의 다른
        # 자리에 먼저 걸리면 엉뚱한 곳을 때려 전부 통과한다.
        assert original.count(old) == 1, f"{old!r} 가 {original.count(old)}회 나온다"
        TARGET.write_text(original.replace(old, new), encoding="utf-8")
        r = subprocess.run(
            [".venv/Scripts/python.exe", "-m", "pytest",
             f"tests/test_polarity.py::{testname}", "-q"],
            capture_output=True, text=True,
        )
        print(f"{testname}: {'죽었다(정상)' if r.returncode != 0 else '생존(게이트 없음)'}")
finally:
    TARGET.write_text(original, encoding="utf-8")
```

Expected: 네 줄 모두 `죽었다(정상)`. 하나라도 `생존` 이면 그 테스트는 회귀 테스트가 아니다.

- [ ] **Step 6: 포섭된 대안 두 개를 지워도 결과가 같은지 확인한다**

`ませんでし` 는 `ません` 에, `ないで` 는 `ない` 에 문자열로 포섭된다. 지우는 것은 **동작 보존**이므로 D3 위반이 아니지만, 실제로 보존되는지는 확인해야 한다.

```python
import re
from cuesift.polarity import _PATTERNS
from bench.track_io import load_track
from pathlib import Path

slim = re.compile(
    r"ない|なかっ|なく|ません|ではな|じゃな|しか(?!し)"
    r"|決して|全く|一度も|無[^限]|不[^思]|非|未"
)
full = _PATTERNS["ja"]
segs = load_track(Path("data/bench/ja-ko.injected.json"))
diff = [
    s.id for s in segs
    if bool(full.search((s.target_text or "").replace("\n", "")))
    != bool(slim.search((s.target_text or "").replace("\n", "")))
]
print(f"판정이 갈린 세그먼트: {len(diff)}건")
```

Run: `PYTHONPATH=. .venv/Scripts/python.exe <위 스크립트>`
Expected: `판정이 갈린 세그먼트: 0건`. 0건이면 두 대안을 지우고 `ませんでし`·`ないで` 를 뺀 `slim` 을 `_PATTERNS["ja"]` 로 삼는다. **0건이 아니면 지우지 않는다.**

- [ ] **Step 7: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/polarity.py tests/test_polarity.py
git commit -m "구현: 극성 표지 판정 술어를 추가한다 (설계 D1~D5)"
```

---

### Task 2: `select_tier1_candidates` 의 우선순위 통로

**Files:**

- Modify: `src/cuesift/triage/policy.py` (`select_tier1_candidates`)
- Test: `tests/test_triage_policy.py`

**Interfaces:**

- Consumes: Task 1 의 어떤 것도 쓰지 않는다. **이 함수는 언어와 자막 본문을 계속 모른다**(설계 §4).
- Produces: `select_tier1_candidates(risks, max_ratio, *, priority_ids: Collection[str] = ()) -> list[str]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_triage_policy.py` 맨 끝에 덧붙인다. `_t1_risk` 헬퍼가 이미 파일에 있다.

```python
def _gray(n: int) -> list[SegmentRisk]:
    """회색지대만 있는 목록. 위험도 내림차순이 ID 오름차순과 같도록 만든다."""
    return [_t1_risk(f"s{i:03d}", 0.9 - i * 0.001) for i in range(n)]


def test_우선_집합이_먼저_들어간다():
    risks = _gray(100)          # cap = floor(100 * 0.05) = 5
    # 위험도로는 꼴찌인 다섯을 우선 집합으로 준다.
    priority = {"s095", "s096", "s097", "s098", "s099"}
    got = select_tier1_candidates(risks, 0.05, priority_ids=priority)
    assert set(got) == priority


def test_우선_집합이_cap보다_작으면_나머지로_채운다():
    risks = _gray(100)
    got = select_tier1_candidates(risks, 0.05, priority_ids={"s099"})
    # 우선 1건 + 회색지대 순서대로 4건
    assert got == ["s099", "s000", "s001", "s002", "s003"]


def test_후보_개수는_우선_집합과_무관하다():
    """설계 D6 - 개수가 오늘과 같아야 빈 후보 진단의 여섯 갈래가 유효하다."""
    risks = _gray(100)
    base = select_tier1_candidates(risks, 0.05)
    for priority in ({}, {"s099"}, {f"s{i:03d}" for i in range(50)}):
        assert len(select_tier1_candidates(risks, 0.05, priority_ids=priority)) == len(base)


def test_상한을_넘는_우선_집합은_앞에서_자른다():
    risks = _gray(100)
    priority = {f"s{i:03d}" for i in range(50)}
    got = select_tier1_candidates(risks, 0.05, priority_ids=priority)
    assert len(got) == 5
    assert got == ["s000", "s001", "s002", "s003", "s004"]


def test_회색지대_밖_ID는_무시된다():
    """hard_fail 과 이미 선별된 것을 우선 집합으로 되살리면 안 된다 (§5.2)."""
    risks = [
        _t1_risk("hard", 1.0, hard_fail=True),
        _t1_risk("picked", 0.8, selected=True),
        *_gray(98),
    ]
    got = select_tier1_candidates(risks, 0.05, priority_ids={"hard", "picked"})
    assert "hard" not in got
    assert "picked" not in got


def test_priority_ids에_str을_넘기면_거부한다():
    """`excluded_ids` 와 같은 함정 - 원소 단위로 쪼개져 조용히 돈다."""
    with pytest.raises(ValueError, match="priority_ids"):
        select_tier1_candidates(_gray(100), 0.05, priority_ids="s000")


def test_priority_ids에_bytes를_넘기면_거부한다():
    with pytest.raises(ValueError, match="priority_ids"):
        select_tier1_candidates(_gray(100), 0.05, priority_ids=b"s000")


def test_priority_ids_기본값은_오늘과_같다():
    """기존 호출부와 테스트가 손대지 않은 채 통과해야 한다 (T10)."""
    risks = _gray(100)
    assert select_tier1_candidates(risks, 0.05) == select_tier1_candidates(
        risks, 0.05, priority_ids=()
    )
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_triage_policy.py -k "우선 or priority or 상한을_넘는 or 회색지대_밖" -v`
Expected: FAIL — `TypeError: select_tier1_candidates() got an unexpected keyword argument 'priority_ids'`

- [ ] **Step 3: 최소 구현을 넣는다**

`src/cuesift/triage/policy.py` 의 `select_tier1_candidates` 를 고친다. `Collection` 임포트를 상단에 더한다.

```python
from collections.abc import Collection, Sequence
```

시그니처와 마지막 반환부를 바꾼다.

```python
def select_tier1_candidates(
    risks: Sequence[SegmentRisk],
    max_ratio: float,
    *,
    priority_ids: Collection[str] = (),
) -> list[str]:
```

독스트링에 아래 절을 더한다.

```text
    ## `priority_ids` - 무엇을 바꾸고 무엇을 안 바꾸나

    회색지대 안에서 **순서만** 바꾼다. `max_ratio` 의 의미도 분모도 그대로이며
    (FR-4.3 - 전체 세그먼트 중 Tier 1 을 적용할 최대 비율), **후보 개수가
    우선 집합과 무관하게 같다**(설계 D6). 개수가 흔들리면 `tier1.py` 의
    `_diagnose_empty_candidates` 가 구분하는 여섯 갈래에 일곱 번째가 생긴다.

    하드 필터가 아니라 **우선순위**인 것이 그 이유다 - 우선 집합이 상한보다
    작으면 나머지를 기존 회색지대 순서로 채운다.

    회색지대 밖의 ID(hard fail · 이미 선별됨)는 무시된다. 교집합만 보므로
    "컷라인 아래"라는 개념이 유지된다.

    **이 함수는 우선 집합을 어떻게 만드는지 모른다.** 그 판정은 언어와 자막
    본문을 알아야 하는데, 그것을 여기 끌어들이면 `triage/` 가 `segment/`
    본문에 결합된다 - `target_text is None` 제외를 호출자에게 맡긴 것과
    같은 이유다 (설계 §4).
```

`if not risks:` 아래의 `max_ratio` 검증들 다음에 타입 방어를 넣는다.

```python
    # `excluded_ids` 와 같은 방어다. 문자열을 그대로 넘기면 `set("s000")` 이
    # 글자 단위로 쪼개져 `{"s", "0"}` 이 되고, 교집합이 늘 비어 **조용히
    # 오늘과 같은 동작**을 한다 - 게이트가 없으면 영영 드러나지 않는다.
    if isinstance(priority_ids, str | bytes):
        raise ValueError(
            f"priority_ids에 {type(priority_ids).__name__}을 그대로 넘겼다"
            f"({priority_ids!r}) - 원소 단위로 쪼개진다. 집합이나 리스트로 감싸라"
        )
```

마지막 `return` 을 바꾼다.

```python
    # **우선 구간과 나머지를 이어 붙인 뒤 자른다.** 우선 집합이 비면
    # `first` 가 비고 `rest` 가 회색지대 전부라 오늘과 **문자 그대로 같은
    # 결과**가 나온다 - 그래서 빈 집합을 위한 분기를 따로 두지 않는다.
    # 분기를 두면 두 경로가 갈라질 자리가 하나 생긴다.
    priority = set(priority_ids)
    ordered = gray_zone(risks)
    first = [r.segment_id for r in ordered if r.segment_id in priority]
    rest = [r.segment_id for r in ordered if r.segment_id not in priority]
    return (first + rest)[:cap]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_triage_policy.py -v`
Expected: PASS. **기존 테스트 12건이 손대지 않은 채 함께 통과해야 한다**(T10).

- [ ] **Step 5: 게이트가 실제로 실패하는지 확인한다**

Task 1 Step 5 와 같은 형태의 스크립트를 쓴다. `finally` 복원과 `count(old) == 1` 단언을 그대로 유지한다.

| 변이 | 죽어야 하는 테스트 |
| --- | --- |
| `(first + rest)[:cap]` → `(rest + first)[:cap]` | `test_우선_집합이_먼저_들어간다` |
| `(first + rest)[:cap]` → `first[:cap]` | `test_후보_개수는_우선_집합과_무관하다` |
| `if r.segment_id in priority` → `if True` | `test_회색지대_밖_ID는_무시된다` |
| `isinstance(priority_ids, str \| bytes)` 블록 삭제 | `test_priority_ids에_str을_넘기면_거부한다` |

Expected: 네 줄 모두 `죽었다(정상)`.

- [ ] **Step 6: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/triage/policy.py tests/test_triage_policy.py
git commit -m "구현: 후보 선정에 우선순위 통로를 연다 (설계 D6~D8)"
```

---

### Task 3: `tier1.py` 배선과 미지원 언어 경고

**Files:**

- Modify: `src/cuesift/tier1.py`
- Test: `tests/test_tier1.py`

**Interfaces:**

- Consumes: `cuesift.polarity.has_polarity_marker` · `supported_languages` (Task 1) · `select_tier1_candidates(..., priority_ids=...)` (Task 2)
- Produces:
  - `CandidateReport` (frozen dataclass): `candidate_ids: tuple[str, ...]` · `priority_ids: frozenset[str]` · `gray_zone_size: int` · `cap: int`
  - `triage_with_tier1(..., on_candidates: Callable[[CandidateReport], None] | None = None)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tier1.py` 맨 끝에 덧붙인다. **아래 이름들은 실제 파일을 확인해 맞춰 둔 것이므로 그대로 쓴다.** `signal_ctx` 는 `@pytest.fixture`(25행)이므로 반드시 **테스트 함수의 인자로 받아야** 하고, 언어를 바꾸는 테스트는 `dataclasses.replace` 로 파생시킨다(`SignalContext` 는 `frozen=True` 데이터클래스다). 파일 상단에 `from dataclasses import replace` 를 더한다.

```python
def test_극성_표지를_가진_세그먼트가_후보로_먼저_간다(signal_ctx):
    """설계 D2 - 원문 또는 번역문에 표지가 있으면 우선 집합이다."""
    reports: list[CandidateReport] = []
    segments = _segments_with_texts(
        # (source_text, target_text) 20건 중 뒤 두 건만 부정을 담는다.
        [("맑은 날입니다", "It is sunny")] * 18
        + [("가지 않았습니다", "did not go"), ("아무도 없습니다", "there is none")]
    )
    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=EchoProvider(),
        max_ratio=0.1,
        warn=_ignore,
        on_candidates=reports.append,
        embedder=_FakeEmbedder(),
    )
    assert len(reports) == 1
    # cap = floor(20 * 0.1) = 2 이므로 표지 보유 두 건이 그대로 후보다.
    assert set(reports[0].candidate_ids) == set(reports[0].priority_ids)
    assert len(reports[0].candidate_ids) == 2


def test_미지원_언어면_경고가_나가고_우선_집합이_빈다(signal_ctx):
    """설계 D5 - 조용히 되돌아가면 무음 열화다 (Q3)."""
    warnings: list[str] = []
    reports: list[CandidateReport] = []
    triage_with_tier1(
        _segments_with_texts([("맑은 날입니다", "Il fait beau")] * 20),
        replace(signal_ctx, source_lang="fr", target_lang="de"),
        budget_ratio=0.1,
        provider=EchoProvider(),
        max_ratio=0.1,
        warn=warnings.append,
        on_candidates=reports.append,
        embedder=_FakeEmbedder(),
    )
    assert any(_POLARITY_UNSUPPORTED in w for w in warnings)
    assert reports[0].priority_ids == frozenset()
    # 후보 개수는 오늘과 같다 (D6).
    assert len(reports[0].candidate_ids) == 2


def test_한쪽만_미지원이면_지원되는_쪽으로_판정한다(signal_ctx):
    warnings: list[str] = []
    reports: list[CandidateReport] = []
    triage_with_tier1(
        _segments_with_texts(
            [("맑은 날입니다", "Il fait beau")] * 18
            + [("가지 않았습니다", "x"), ("아무도 없습니다", "y")]
        ),
        replace(signal_ctx, target_lang="de"),
        budget_ratio=0.1,
        provider=EchoProvider(),
        max_ratio=0.1,
        warn=warnings.append,
        on_candidates=reports.append,
        embedder=_FakeEmbedder(),
    )
    assert any(_POLARITY_UNSUPPORTED in w for w in warnings)
    # ko 는 지원되므로 원문 표지로 두 건이 잡힌다.
    assert len(reports[0].priority_ids) == 2


def test_경고_문구를_테스트가_지어_넘기지_않는다():
    """상수를 임포트해 검사한다. 리터럴로 두면 문구가 바뀌어도 통과한다."""
    assert "극성" in _POLARITY_UNSUPPORTED
    assert "—" not in _POLARITY_UNSUPPORTED  # cp949 에 없는 em dash 금지


def test_on_candidates가_없으면_아무것도_안_부른다(signal_ctx):
    """콜백은 선택이다. 기존 호출부가 손대지 않은 채 돌아야 한다."""
    triage_with_tier1(
        _segments_with_texts([("맑은 날입니다", "It is sunny")] * 20),
        signal_ctx,
        budget_ratio=0.1,
        provider=EchoProvider(),
        max_ratio=0.1,
        warn=_ignore,
        embedder=_FakeEmbedder(),
    )
```

**새로 만드는 헬퍼는 `_segments_with_texts(pairs)` 하나뿐이다.** `(source_text, target_text)` 쌍 목록으로 `Segment` 를 만들고, `id` 는 `f"s{i:03d}"`, `index` 는 `i`, `start_ms` 는 `i * 2000`, `end_ms` 는 `i * 2000 + 1500` 으로 겹치지 않게 둔다.

**나머지 넷은 파일에 이미 있으므로 만들지 않는다.** 계획서를 처음 쓸 때 지어낸 이름들이 실제와 달랐고, 아래가 실측으로 확인한 이름이다.

| 쓸 것 | 위치와 성질 |
| --- | --- |
| `signal_ctx` | 25행의 `@pytest.fixture`. `load_builtin("en")` · ko->en 고정이고 **인자를 받지 않는다** |
| `EchoProvider()` | `from tests.fakes.provider import EchoProvider` 로 이미 임포트돼 있다 |
| `_FakeEmbedder()` | 같은 파일 320행 |
| `_ignore` | 31행. 경고 사유에 관심 없는 테스트가 쓰는 공용 자리표시자다 |

**이 테스트를 쓰기 전에 두 세그먼트가 회색지대에 있는지부터 확인한다.** 부정을 담은 문장이 `spec.violation`(길이 초과)이나 `length.ratio` 를 건드리면 hard fail 이나 컷라인 위로 올라가 우선 집합에서 빠지고, 그러면 테스트가 **극성 판정이 아니라 Tier 0 신호를 재게 된다.** 아래 한 줄을 먼저 돌려 확인하고, 신호가 걸리면 문장을 짧고 규격에 맞게 고친다.

```python
from cuesift.risk.fuse import fuse
from cuesift.signals.base import collect_all
sig = collect_all(segments, ctx)
assert all(not sig[s.id] for s in segments), {s.id: sig[s.id] for s in segments if sig[s.id]}
assert all(not fuse(s.id, sig[s.id]).hard_fail for s in segments)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -k "극성 or 미지원 or on_candidates or 경고_문구" -v`
Expected: FAIL — `ImportError: cannot import name 'CandidateReport'`

- [ ] **Step 3: 구현한다**

`src/cuesift/tier1.py` 상단 임포트에 더한다.

```python
from dataclasses import dataclass

from cuesift.polarity import has_polarity_marker, supported_languages
```

`_ZERO_BY_SWITCH` 옆에 상수를 둔다.

```python
# 극성 표지 목록이 없는 언어에서 내는 경고 (설계 D5).
#
# **리터럴로 두면 안 된다.** 테스트가 이 문자열을 자기 안에서 다시 지어
# 넘기면 문구가 바뀌어도 계속 통과해 화면과 갈라진다 - `_TIER1_WARN_PREFIX`
# 와 같은 이유이고, 리포트 caveat 두 건이 실제로 이렇게 갈렸다.
#
# **출력 문자열이라 em dash 를 쓰지 않는다**(전역 제약, cp949 미인코딩).
_POLARITY_UNSUPPORTED = (
    "극성 표지 목록이 없는 언어다 - Tier 1 후보를 위험도 순서로만 고른다"
)
```

`CandidateReport` 를 모듈 상단(함수들보다 앞)에 둔다.

```python
@dataclass(frozen=True)
class CandidateReport:
    """Tier 1 후보가 어떻게 구성됐는지 (설계 D10).

    **벤치 리포트가 이 값을 싣지 않으면 Recall 이 왜 움직였는지 갈리지
    않는다.** 후보 안의 negation 건수를 무작위 기대값과 나란히 놔야
    "적지만 있긴 하다"가 아니라 "선정이 무작위와 구별되는가"를 읽을 수 있다
    (이월 21번의 교훈).

    `frozen=True` 인 것은 콜백이 받은 뒤 고쳐도 파이프라인이 모르기
    때문이다 - 관측용 값이 조용히 바뀌면 리포트가 실제와 갈라진다.
    """

    candidate_ids: tuple[str, ...]
    priority_ids: frozenset[str]
    gray_zone_size: int
    cap: int
```

우선 집합 계산 헬퍼를 `_diagnose_empty_candidates` 근처에 둔다.

```python
def _polarity_priority(
    segments: Sequence[Segment],
    ctx: SignalContext,
    warn: Callable[[str], None],
) -> frozenset[str]:
    """극성 표지를 가진 세그먼트 ID (설계 D2 · D5).

    **원문 또는 번역문 어느 쪽이든** 표지가 있으면 우선 집합이다. 「원문에
    있고 번역문에 없음」으로 좁히면 농축이 10~12x 로 오르지만, 그 조건은
    `bench/inject.py` 가 오류를 만드는 방식(원문 유지 · 번역문에서 제거)을
    그대로 베낀 것이라 측정이 자기 충족적이 된다 (설계 D2 · §3.3).

    **미지원 언어를 조용히 넘기지 않는다.** 경고 없이 빈 집합을 내면
    사용자에게는 "표지가 하나도 없었다"와 구별되지 않는다 (§12 Q3 무음 열화).
    """
    supported = supported_languages()
    missing = [lang for lang in (ctx.source_lang, ctx.target_lang) if lang not in supported]
    if missing:
        warn(f"{_POLARITY_UNSUPPORTED} ({', '.join(missing)})")
    if len(missing) == 2:
        return frozenset()

    src_ok = ctx.source_lang in supported
    tgt_ok = ctx.target_lang in supported
    return frozenset(
        seg.id
        for seg in segments
        if (src_ok and has_polarity_marker(seg.source_text, ctx.source_lang))
        or (tgt_ok and has_polarity_marker(seg.target_text, ctx.target_lang))
    )
```

`triage_with_tier1` 의 시그니처에 키워드 인자를 더한다.

```python
    on_candidates: Callable[[CandidateReport], None] | None = None,
```

④ 후보 선별 블록을 바꾼다.

```python
    # ④ 후보 선별 - 극성 표지를 가진 것을 먼저 본다 (설계 D2 · D6).
    #
    # **`kept` 를 넘긴다.** `segments` 를 넘기면 번역 실패분까지 우선 집합에
    # 들어가고, 그것들은 바로 아래에서 다시 걸러지므로 우선 구간의 자리만
    # 먹는다 - 상한이 후보를 자르는 이 구조에서는 자리를 먹는 것이 곧
    # 진짜 후보를 밀어내는 것이다.
    priority_ids = _polarity_priority(kept, ctx, warn)
    ordered_candidates = select_tier1_candidates(scored, max_ratio, priority_ids=priority_ids)
    candidate_ids = set(ordered_candidates)

    if on_candidates is not None:
        # `gray_zone` 을 여기서만 부른다 - 5,000건 정렬이라 콜백이 없을 때는
        # 값을 치르지 않는다.
        on_candidates(
            CandidateReport(
                candidate_ids=tuple(ordered_candidates),
                priority_ids=priority_ids,
                gray_zone_size=len(gray_zone(scored)),
                cap=math.floor(len(scored) * max_ratio),
            )
        )
```

`Segment` 임포트가 없으면 더한다(`from cuesift.segment import Segment, SegmentRisk`).

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -v`
Expected: PASS. 기존 테스트가 손대지 않은 채 함께 통과해야 한다.

- [ ] **Step 5: 게이트가 실제로 실패하는지 확인한다**

| 변이 | 죽어야 하는 테스트 |
| --- | --- |
| `warn(f"{_POLARITY_UNSUPPORTED} ...")` 줄 삭제 | `test_미지원_언어면_경고가_나가고_우선_집합이_빈다` |
| `if len(missing) == 2` → `if len(missing) >= 1` | `test_한쪽만_미지원이면_지원되는_쪽으로_판정한다` |
| `priority_ids=priority_ids` → `priority_ids=()` | `test_극성_표지를_가진_세그먼트가_후보로_먼저_간다` |
| `or (tgt_ok and has_polarity_marker(seg.target_text, ...))` 삭제 | `test_극성_표지를_가진_세그먼트가_후보로_먼저_간다` |

Expected: 네 줄 모두 `죽었다(정상)`.

- [ ] **Step 6: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/tier1.py tests/test_tier1.py
git commit -m "구현: Tier 1 후보에 극성 표지 우선순위를 배선한다 (설계 D2 · D5 · D10)"
```

---

### Task 4: 벤치 리포트의 후보 구성 표

**Files:**

- Modify: `bench/report.py` (`render_tier1_candidates` 신설 · `write_report` 확장)
- Modify: `bench/run.py` (`on_candidates` 배선)
- Test: `tests/test_bench_report.py`

**Interfaces:**

- Consumes: `CandidateReport` (Task 3)
- Produces: `render_tier1_candidates(*, budget: float, cap: int, gray_zone_size: int, candidates: int, from_priority: int, negation_hits: int, negation_in_gray_zone: int) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_bench_report.py` 맨 끝에 덧붙인다.

```python
def test_후보_구성표가_무작위_기대값을_함께_낸다():
    """이월 21번의 교훈 - 적중만 보면 '적지만 있긴 하다'로 읽힌다."""
    block = render_tier1_candidates(
        budget=0.10,
        cap=250,
        gray_zone_size=4500,
        candidates=250,
        from_priority=250,
        negation_hits=18,
        negation_in_gray_zone=70,
    )
    assert "3.89" in block          # 70 * 250 / 4500
    assert "18" in block
    assert "4.63" in block          # 18 / 3.89


def test_후보_구성표가_채움_경로를_구분한다():
    """설계 D6 - 우선 풀이 cap 보다 작으면 나머지가 무작위 표본이다."""
    block = render_tier1_candidates(
        budget=0.10,
        cap=250,
        gray_zone_size=4500,
        candidates=250,
        from_priority=90,
        negation_hits=12,
        negation_in_gray_zone=70,
    )
    assert "90" in block
    assert "160" in block           # 채움분


def test_회색지대가_비면_0으로_나눈다고_죽지_않는다():
    block = render_tier1_candidates(
        budget=0.10,
        cap=0,
        gray_zone_size=0,
        candidates=0,
        from_priority=0,
        negation_hits=0,
        negation_in_gray_zone=0,
    )
    assert "0" in block
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_report.py -k 후보_구성 -v`
Expected: FAIL — `ImportError: cannot import name 'render_tier1_candidates'`

- [ ] **Step 3: 렌더러를 구현한다**

`bench/report.py` 에 `render_tier1_comparison` 바로 아래에 넣는다.

```python
def render_tier1_candidates(
    *,
    budget: float,
    cap: int,
    gray_zone_size: int,
    candidates: int,
    from_priority: int,
    negation_hits: int,
    negation_in_gray_zone: int,
) -> str:
    """Tier 1 후보가 어떻게 구성됐는지 (설계 D10).

    **무작위 기대값을 함께 내는 것이 이 표의 존재 이유다.** 적중 건수만
    적으면 "적지만 있긴 하다"로 읽히는데, 기대값과 나란히 놔야 선정이
    무작위와 구별되는지가 드러난다 (이월 21번).

    **배수가 1.0 근처면 후보 선정이 여전히 무작위다** - Recall 이 올랐더라도
    그것은 다른 이유이므로 이 표가 먼저다.
    """
    expected = (
        negation_in_gray_zone * min(cap, gray_zone_size) / gray_zone_size
        if gray_zone_size
        else 0.0
    )
    ratio = negation_hits / expected if expected else 0.0
    return "\n".join(
        [
            f"### Tier 1 후보 구성 (예산 {budget:.0%})",
            "",
            "| 항목 | 값 |",
            "| --- | ---: |",
            f"| 회색지대 | {gray_zone_size} |",
            f"| 상한(cap) | {cap} |",
            f"| 후보 | {candidates} |",
            f"| 그중 극성 표지 보유 | {from_priority} |",
            f"| 그중 채움분(무작위 표본) | {candidates - from_priority} |",
            f"| 후보 안 negation | **{negation_hits}** |",
            f"| 무작위 기대값 | {expected:.2f} |",
            f"| **배수** | **{ratio:.2f}x** |",
            "",
            "**배수가 1.0 근처면 후보 선정이 여전히 무작위다.** 적중 건수만"
            " 보면 판단할 수 없으므로 기대값을 함께 싣는다 (이월 21번).",
        ]
    )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_report.py -v`
Expected: PASS

- [ ] **Step 5: `bench/run.py` 에 배선한다**

`--tier1` 분기(`bench/run.py:432` 부근)의 예산 루프 안, `triage_with_tier1` 호출을 고친다.

```python
                candidate_reports: list[CandidateReport] = []
                tier1_risks = triage_with_tier1(
                    ...,                       # 기존 인자를 그대로 둔다
                    on_candidates=candidate_reports.append,
                )
```

`render_tier1_comparison` 을 부르는 자리 옆에서 후보 구성 블록을 만든다.

```python
                # **`negation_in_gray_zone` 은 라벨에서 센다.** 후보 안 건수만
                # 세면 분모가 없어 무작위 기대값을 낼 수 없다.
                report = candidate_reports[0]
                candidate_id_set = set(report.candidate_ids)
                tier1_comparisons.append(
                    render_tier1_candidates(
                        budget=budget,
                        cap=report.cap,
                        gray_zone_size=report.gray_zone_size,
                        candidates=len(report.candidate_ids),
                        from_priority=sum(
                            1 for sid in report.candidate_ids if sid in report.priority_ids
                        ),
                        negation_hits=len(candidate_id_set & negation_ids),
                        negation_in_gray_zone=_negation_in_gray_zone(
                            tier0_risks, budget, negation_ids
                        ),
                    )
                )
```

`negation_ids` 는 `{lb.segment_id for lb in labels if lb.kind == "negation"}` 이다. 이미 있으면 재사용한다.

`_negation_in_gray_zone` 헬퍼를 `bench/run.py` 에 만든다.

```python
def _negation_in_gray_zone(
    risks: Sequence[SegmentRisk], budget: float, negation_ids: set[str]
) -> int:
    """회색지대 안 negation 건수 - 무작위 기대값의 분자다.

    **`gray_zone` 을 직접 부른다.** 술어를 여기서 복제하면 `triage/` 가
    제외 조건을 하나 더 넣을 때 이 수가 조용히 틀린다 (2라운드 리뷰 C3 전례).
    """
    scored = select_by_budget(risks, budget)
    return sum(1 for r in gray_zone(scored) if r.segment_id in negation_ids)
```

임포트를 더한다.

```python
from bench.report import render_tier1_candidates
from cuesift.tier1 import CandidateReport, triage_with_tier1
from cuesift.triage.policy import gray_zone, select_by_budget
```

`triage_with_tier1` 은 이미 임포트돼 있으므로 `CandidateReport` 만 그 줄에 더한다.

- [ ] **Step 6: 통과를 확인하고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add bench/report.py bench/run.py tests/test_bench_report.py
git commit -m "구현: 벤치 리포트에 Tier 1 후보 구성 표를 싣는다 (설계 D10)"
```

---

### Task 5: en-ko 벤치 재측정과 기록

**Files:**

- Create: `bench/results/en-ko-2026-09-06.md` (벤치가 생성)
- Modify: `docs/요구사항정의서.md` (FR-4.2 행)
- Modify: `HANDOFF.md` (이월 21번 · 승계 항목)
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes: Task 1~4 전부

- [ ] **Step 1: Tier 0 회귀를 먼저 확인한다**

`--tier1` 없는 실행이 2026-09-05 리포트와 같은 수치를 내야 한다. 다르면 Tier 0 경로에 손을 댄 것이므로 여기서 멈춘다.

```bash
.venv/Scripts/python.exe -m bench.run --pair en-ko --seed 20260729
```

Expected: Recall @ Budget 표가 2026-09-05 리포트와 소수점까지 같다.

- [ ] **Step 2: Tier 1 을 켜서 돌린다**

**`--cache-dir` 를 반드시 준다.** 역번역과 임베딩은 캐시가 없으면 매번 다시 부른다.

곱 게이트(`samples × max_ratio < 0.3`) 한계까지 올려야 후보 상한이 내림으로 0 이 되지 않는다. 로컬 `qwen2.5:3b` 는 배치 번역 형식을 자주 어겨(26건 중 19건 `invalid_response`) 표본이 작아진다.

```bash
.venv/Scripts/python.exe -m bench.run --pair en-ko --seed 20260729 \
  --tier1 --tier1-samples 2 --tier1-max-ratio 0.149 \
  --cache-dir data/bench/cache --audit-dir data/bench
```

- [ ] **Step 3: 후보 구성 표를 읽고 판정한다**

리포트의 「Tier 1 후보 구성」 표에서 **배수**를 본다.

| 배수 | 판정 |
| --- | --- |
| 1.0 근처 | 후보 선정이 여전히 무작위다. **완료 판정 3번 미달이므로 여기서 멈추고 원인을 찾는다** |
| 3.0 이상 | 설계 §3.3 의 예측(3.3~4.6x)과 맞는다. 진행한다 |
| 1.0~3.0 | 예측과 어긋난다. 우선 풀 크기와 cap 을 리포트에서 확인하고 원인을 적는다 |

**Recall 이 올랐는지는 판정 기준이 아니다**(설계 §9 완료 판정 3번). 결함 ②(비대칭 밀어냄)가 남아 있으므로 최종 Recall 이 오르지 않을 수 있다.

- [ ] **Step 4: 결과를 문서에 옮긴다**

| 문서 | 무엇을 |
| --- | --- |
| `docs/요구사항정의서.md` FR-4.2 행 | 2026-09-06 실측을 더한다. **2026-09-05 서술("후보 선정이 막는다")을 지우지 않고 남긴다** - 사료다 |
| `HANDOFF.md` 이월 21번 | 「다시 열 조건」이 충족됐는지 판정하고, 결함 ②를 새 이월 항목으로 연다 |
| `HANDOFF.md` 승계 항목 FR-4.2 행 | 상태와 다음 착수 조건을 갱신한다 |
| `CHANGELOG.md` | Keep a Changelog 형식으로 더한다 |

**파생 문서에서 읽은 사실은 원본에서 읽은 것보다 약하다.** 위 네 자리에 같은 수치를 복사하는 것이므로, 각 자리에서 **어느 것이 단일 출처인지**를 명시한다. 2026-09-05 판은 이월 21번이 단일 출처였다.

- [ ] **Step 5: 문서 게이트를 돌린다**

**`git add` 를 먼저 한다.** `scripts/check_links.py` 는 `git ls-files` 를 대상으로 삼아 추적되기 전의 새 문서는 링크 검사를 아예 받지 않는다.

```bash
git add bench/results/en-ko-2026-09-06.md docs/요구사항정의서.md HANDOFF.md CHANGELOG.md
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

Expected: 두 도구의 파일 개수가 **같아야 한다**(54개). `깨진 링크 없음` 만 읽으면 통과로 보인다.

- [ ] **Step 6: 전체 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git commit -m "측정: 극성 표지 후보 선정의 en-ko 실측을 기록한다"
```

- [ ] **Step 7: PR 을 올린다**

**푸시는 사용자 승인을 받은 뒤에 한다.**

```bash
git push -u origin feat/tier1-candidate-selection
gh pr create --base main
gh pr checks --watch
```

PR 본문에는 **무엇을 · 근거 문서 · 게이트 수치**를 담는다. 게이트 수치는 개수를 그대로 적는다(pytest 수집 개수 · markdownlint 파일 개수 · 링크 체커 파일 개수).

**`main` 머지는 별도 승인을 받는다.**

---

## 구현 중 바뀐 결정

**이 절이 본문 코드 블록보다 최신이다.** 아래 항목에서 본문과 어긋나면 여기를 따른다.

| # | 태스크 | 무엇이 바뀌었나 | 왜 |
| --- | --- | --- | --- |
| C1 | 1 | T3 의 `못했습니다` → `못하다` | `못했`는 `못하`의 부분 문자열이 아니다(`했` U+D588 vs `하` U+D558). 계획서가 실행 없이 단언한 거짓이었다. 대체안으로 낸 `못합니다`도 같은 이유로 틀렸다(`합` U+D569) |
| C2 | 1 | 어휘 목록은 그대로 둔다 | `못한\|못할\|못함\|못합\|못했\|못해` 를 더하면 표지 보유가 en 751 → 782 · ja 685 → 717 로 늘지만 그중 negation 라벨은 **양쪽 다 2건**이다. 71건 중 2건을 얻자고 설계 §3.3 표를 무효화하지 않는다 |
| C3 | 1 | 개행 테스트 문자열을 표지가 **쪼개지는** 것으로 교체 | `かもし\nれません` 은 `ません` 이 통째로 남아 개행을 안 지워도 잡힌다. 통과하지만 아무것도 검사하지 않는 **가짜 게이트**였고, 파괴 실험이 잡았다 |
| C4 | 1 | D4 를 「ko·en 은 원본과 제거본 양쪽, ja 는 제거본만」으로 개정 | C3 을 추적하다 설계 결함이 드러났다. 제거본만 보면 영어 단어가 붙어 `\b` 가 깨지고(en 번역문 52건), 원본도 보면 개행이 배제 룩어헤드를 가린다(`しか\nし`). 상세는 설계 스펙 §3.5 가 단일 출처다 |
| C5 | 1 | Step 6 의 비교 정규식이 버그였다 | 포섭된 둘(`ませんでし`·`ないで`) 말고 `ずに` 까지 함께 뺐다. 올바른 비교는 차이 0건이다. 결론(패턴 유지)은 보수적으로 옳았다 |
| C6 | 3 | `("없습니다", "there is none")` → `("아무도 없습니다", "there is none")` | 4자 대 13자는 `length.ratio` 를 건드려 위험도가 오르고, 그러면 그 세그먼트가 **컷라인 위로 선별돼 회색지대에서 빠진다.** 우선 집합에 들어가야 할 것이 후보 대상에서 사라진다 |
| C7 | 4 | `tier0_risks` → `risks`, `negation_ids` 를 새로 만든다, `select_by_budget` 은 이미 임포트돼 있다, 테스트에 렌더러 임포트를 더한다 | 계획서를 쓸 때 `bench/run.py` 의 실제 변수명을 확인하지 않았다 |
| C8 | 2 | 변이 매핑 표만 고치고 **코드와 테스트는 그대로 둔다** | 계획서는 `if r.segment_id in priority` → `if True` 가 `test_회색지대_밖_ID는_무시된다` 를 죽인다고 적었으나, `hard`·`picked` 는 `gray_zone()` 이 먼저 걸러 그 필터에 닿지 않으므로 변이가 생존한다. 그 테스트를 실제로 죽이는 변이는 `ordered = gray_zone(risks)` → `ordered = _sorted_desc(risks)` 이고, 돌려서 확인했다(1 failed). **테스트는 진짜 게이트이며 틀린 것은 서술뿐이었다** |
| C9 | 3 | 테스트 헬퍼 이름 네 건을 실제 이름으로 바꾸고, `signal_ctx` 를 함수 인자로 받게 한다 | 계획서가 지어낸 `_ctx(source_lang=..., target_lang=...)`·`_stub_provider()`·`_stub_embedder()`·`warn=lambda _: None` 은 파일에 없다. 실제는 픽스처 `signal_ctx`(인자 없음)·`EchoProvider()`·`_FakeEmbedder()`·`_ignore` 이며, 픽스처는 인자로 받지 않으면 `NameError` 가 난다. 언어를 바꾸는 두 테스트는 `dataclasses.replace` 로 파생시킨다 |

**공통 원인은 하나다.** 계획서에 한글 문자열·정규식·변수명을 **실행해 보지 않고**
적었다. C1·C3·C6·C7 이 전부 그 부류다. 이 리포에 문자열을 박을 때는 실행으로
확인한다.

## 자체 검토

**스펙 대응표** — 설계 문서의 각 결정이 어느 태스크에 담겼는지다.

| 설계 항목 | 태스크 |
| --- | --- |
| D1 모듈 위치 | Task 1 Step 3 |
| D2 P1 판정 조건 | Task 1 Step 3 · Task 3 `_polarity_priority` |
| D3 목록을 튜닝하지 않는다 | Task 1 Step 3 주석 · Step 6 동작 보존 확인 |
| D4 개행 · CJK 경계 | Task 1 Step 1 T1~T3 · Step 5 변이 |
| D5 미지원 언어 경고 | Task 3 Step 1 T9 · Step 3 |
| D6 채움 경로 | Task 2 Step 1 T4·T5 |
| D7 우선 구간 내 순서 | Task 2 Step 3 (`gray_zone` 순서 유지) |
| D8 `max_ratio` 불변 | Task 2 Step 1 T6 |
| D9 점수 미기여 | Task 1 반환형 `bool` · Task 2 가 `risk_score` 를 안 건드림 |
| D10 리포트 후보 구성 | Task 4 전체 |
| §5.2 경계 조건 | Task 2 Step 1 T7·T8 |
| §8.2 측정 한계 서술 | Task 5 Step 4 |
| §9 완료 판정 1~7 | Task 2 Step 4(1) · 각 Step 5(2) · Task 5 Step 3(3) · Step 4(4·5) · Step 5~6(6) · Step 7(7) |

**미해결로 남기는 것**: 설계 §10 의 다섯 항목은 의도적으로 태스크가 없다.
