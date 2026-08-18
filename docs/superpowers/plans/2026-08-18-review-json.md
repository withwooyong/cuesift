# `review.json` 검수 리포트 구현 계획 (FR-7.2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 트리아지가 고른 검수 큐를 `review.json` 파일로 내보내 검수자와 도구가 읽을 수 있게 한다 (FR-7.2, FR-6.4).

**Architecture:** `_run_triage`가 `list[str]` 대신 `TriageOutcome` 객체를 반환하고, 화면 요약 포매터와 JSON 직렬화가 **같은 객체**를 읽는다. 직렬화는 신설 패키지 `src/cuesift/report/`가 맡고 `cli.py`는 옵션 해석·경로 결정·예외를 종료 코드로 바꾸는 일만 한다. 기존 라이브러리(`signals`·`risk`·`triage`·`segment`)는 한 줄도 건드리지 않는다.

**Tech Stack:** Python 3.11+ · typer · pysubs2 · pyyaml · httpx · pytest · ruff. 표준 라이브러리 `json`으로 직렬화한다.

**Spec:** [설계 스펙](../specs/2026-08-18-review-json-design.md)

## Global Constraints

이 절의 요구는 **모든 태스크에 암묵적으로 포함된다.**

| 제약 | 값 |
| --- | --- |
| Python 실행 | **반드시 `.venv/Scripts/python.exe`** — 시스템 Python은 3.14라 다르다 |
| 모듈 첫 줄 | `from __future__ import annotations` |
| 독스트링·주석 | **한국어.** 근거 FR·§ 번호를 병기한다 (예: `FR-7.2`, `설계 §6.2`) |
| 주석 내용 | "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"** |
| ruff | `line-length = 100`, 규칙 `E,F,I,UP,B,SIM` |
| 커밋 메시지 | **한국어** |
| 푸시 | **사용자가 명시적으로 요청할 때만.** 커밋과 푸시를 한 명령에 묶지 않는다 |
| 의존성 | **추가 금지.** 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개 |
| `--help` 문자열 | **em dash(U+2014) 금지** — cp949가 인코딩하지 못한다. `·`(U+00B7)는 쓸 수 있다 |
| 게이트 대상 | `.venv/Scripts/python.exe -m ruff check .` — **`src tests`로 좁히지 않는다.** CI가 `.`을 돌린다 |
| 브랜치 | `feat/review-json` (이미 생성됨). **`main`에 직접 푸시하지 않는다** |

**게이트 전량(각 태스크의 마지막 커밋 전에 돌린다):**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**"통과했나"가 아니라 "무엇을 대상으로 통과했나"를 읽는다.** pytest의 수집 개수, markdownlint의 `Linting: N files`, 링크 체커의 상대 링크 수를 매번 확인한다. **0개 수집은 통과가 아니라 설정 오류다.**

착수 시점 기준선: **1096 passed, 3 deselected** · 커버리지 99% · 마크다운 28 files · 상대 링크 131개.

**이 계획의 수집 개수 예고값은 산문이 아니라 기계적 집계로 재산출된 것이다.** 초판은 손으로 세어 Task 1에서 8(실제 7), Task 6에서 10(실제 9)을 적었고 오차가 후속 태스크에 누적됐다 — Task 1 구현자가 `grep -c "^def test_"`로 잡았다. 예고값이 실제와 어긋나면 **멈추고 먼저 세어라**:

```bash
grep -c "^def test_" <브리프 또는 테스트 파일>
```

이 저장소는 같은 교훈을 이미 한 번 치렀다 — 요구사항정의서의 `✅`를 손으로 세면 6이 아니라 7이 나왔고, 지금은 스크립트로 재현된다.

---

## 파일 구조

| 파일 | 책임 |
| --- | --- |
| `src/cuesift/report/__init__.py` | 공개 API 재수출 (`TriageOutcome`·`build_review`·`write_review`) |
| `src/cuesift/report/models.py` | `TriageOutcome` — 트리아지 1회의 결과와 **파생 수치의 단일 출처** |
| `src/cuesift/report/json_report.py` | `build_review()`(dict 생성) · `write_review()`(파일 쓰기) |
| `src/cuesift/cli.py` | `_run_triage` 반환 타입 교체 · `--review-out` 옵션 · `_review_path` · 배선 |
| `tests/test_report_models.py` | `TriageOutcome` 파생 수치 단위 테스트 |
| `tests/test_report_json.py` | 스키마 직렬화·파일 쓰기 단위 테스트 |
| `tests/test_cli_review_out.py` | CLI 표면·조합 검증·통합 게이트 |
| `tests/test_cli_triage.py` | **기존 파일 수정** — `_format_triage_summary` 호출 2곳 |

`report/`를 새 패키지로 여는 이유는 `cli.py`가 1686줄이고 FR-7.3 `report.html`이 같은 자리를 다시 요구하기 때문이다(설계 D9).

---

## Task 1: `TriageOutcome` 모델

**Files:**

- Create: `src/cuesift/report/__init__.py`
- Create: `src/cuesift/report/models.py`
- Test: `tests/test_report_models.py`

**Interfaces:**

- Consumes: `cuesift.segment.SegmentRisk` · `cuesift.segment.Segment` · `cuesift.translate.provider.TokenUsage` · `cuesift.triage.review_ratio`
- Produces: `TriageOutcome` dataclass. 필드 `source_lang`·`target_lang`·`profile_name`·`policy_label`·`policy_kind`·`policy_value`·`risks`·`segments`·`excluded_failures`·`usage`. 프로퍼티 `triaged_segments`·`total_segments`·`selected`·`selected_for_review`·`hard_fail_count`·`signal_hits`·`review_ratio`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_report_models.py`를 만든다.

```python
"""`TriageOutcome`의 파생 수치 (FR-7.2 · 설계 §7.1)."""

from __future__ import annotations

import pytest

from cuesift.report import TriageOutcome
from cuesift.segment import Segment, SegmentRisk, Signal


def _risk(
    seg_id: str,
    *,
    selected: bool = False,
    hard_fail: bool = False,
    reasons: list[str] | None = None,
    score: float = 0.5,
    signals: list[Signal] | None = None,
) -> SegmentRisk:
    return SegmentRisk(
        segment_id=seg_id,
        signals=[] if signals is None else signals,
        risk_score=score,
        hard_fail=hard_fail,
        selected=selected,
        reasons=[] if reasons is None else reasons,
    )


def _segment(seg_id: str, *, index: int = 0) -> Segment:
    return Segment(
        id=seg_id,
        index=index,
        start_ms=0,
        end_ms=1000,
        source_text="원문",
        target_text="target",
    )


def _outcome(
    *,
    risks: tuple[SegmentRisk, ...],
    excluded_failures: int = 0,
) -> TriageOutcome:
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label="예산 10%",
        policy_kind="budget",
        policy_value=0.1,
        risks=risks,
        segments=tuple(_segment(r.segment_id, index=i) for i, r in enumerate(risks)),
        excluded_failures=excluded_failures,
        usage=None,
    )


def test_total은_triaged와_excluded의_합이다() -> None:
    """설계 §6.2 — 이 산수가 파일 안에서 검산된다.

    셋을 하나로 합치면 `review_ratio`의 분모가 무엇인지 소비자가 알 수 없고,
    배수의 분모가 조용히 틀린다.
    """
    outcome = _outcome(risks=(_risk("00000"),), excluded_failures=3)

    assert outcome.triaged_segments == 1
    assert outcome.excluded_failures == 3
    assert outcome.total_segments == 4


def test_selected는_selected_플래그가_참인_것만_낸다() -> None:
    outcome = _outcome(risks=(_risk("00000", selected=True), _risk("00001")))

    assert outcome.selected_for_review == 1
    assert [r.segment_id for r in outcome.selected] == ["00000"]


def test_signal_hits는_선별되지_않은_것도_센다() -> None:
    """집계는 `risks` **전체**를 본다.

    선별분으로 좁히면 "예산 밖으로 밀린 위험"이 사라져 사용자가 다음 예산을
    정할 근거를 잃는다. 화면 요약이 이미 같은 규칙을 따른다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", selected=True, reasons=["spec.violation"]),
            _risk("00001", selected=False, reasons=["struct.empty"]),
        )
    )

    assert outcome.signal_hits == {"spec.violation": 1, "struct.empty": 1}


def test_signal_hits는_이름순으로_정렬된다() -> None:
    """NFR-3 재현성 — `Counter`의 순서는 삽입 순이라 세그먼트 순서가 바뀌면 흔들린다."""
    outcome = _outcome(
        risks=(
            _risk("00000", reasons=["struct.empty"]),
            _risk("00001", reasons=["glossary.miss"]),
        )
    )

    assert list(outcome.signal_hits) == ["glossary.miss", "struct.empty"]


def test_hard_fail_count는_전체에서_센다() -> None:
    outcome = _outcome(risks=(_risk("00000", hard_fail=True), _risk("00001")))

    assert outcome.hard_fail_count == 1


def test_review_ratio는_triaged를_분모로_쓴다() -> None:
    """실패분을 분모에 넣으면 README 배수가 무너진다 (설계 §6.2)."""
    outcome = _outcome(
        risks=(_risk("00000", selected=True), _risk("00001"), _risk("00002"), _risk("00003")),
        excluded_failures=6,
    )

    # 분모가 triaged(4)면 0.25, total(10)이면 0.1이다.
    assert outcome.review_ratio == pytest.approx(0.25)


def test_risks가_비면_review_ratio는_0이다() -> None:
    """전량 번역 실패 경로. `ZeroDivisionError`가 아니라 0.0이어야 한다."""
    outcome = _outcome(risks=(), excluded_failures=10)

    assert outcome.review_ratio == 0.0
    assert outcome.total_segments == 10
    assert outcome.selected_for_review == 0
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_report_models.py -v
```

기대: `ModuleNotFoundError: No module named 'cuesift.report'` — 전 테스트 수집 실패.

- [ ] **Step 3: `models.py`를 쓴다**

```python
"""트리아지 결과 모델 (요구사항정의서 FR-7.2 · 설계 §7.1).

**화면 요약과 `review.json`의 공통 출처다.** 두 소비자가 수치를 각자 세면
조용히 갈라지는데, 그때 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도
0이라 어떤 게이트에도 걸리지 않는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from cuesift.segment import Segment, SegmentRisk
from cuesift.translate.provider import TokenUsage
from cuesift.triage import review_ratio as _review_ratio


@dataclass(frozen=True, slots=True)
class TriageOutcome:
    """대상 언어 하나의 트리아지 결과.

    **`risks`는 `select_by_*`가 돌려준 전체 목록이다.** 선별분만 담으면
    `review_ratio`가 언제나 1.0이 되고, 그 값이 스펙 §6.2의 "실제 검수 비율"이자
    README 배수의 분모라 조용히 틀리면 프로젝트의 핵심 주장이 무너진다.

    **`policy_label`과 `policy_kind`/`policy_value`는 중복이 아니다.** 라벨은
    사용자가 친 원본 문자열(`"10%"`)을 보존해 화면에 그대로 되돌려 주고,
    kind/value는 정규화된 값이라 `review.json`이 쓴다. 라벨을 kind/value에서
    재생성하면 화면 출력이 `"예산 10.0%"`로 바뀐다.

    **`segments`는 `risks`와 같은 집합이다**(번역 실패분이 빠진 것). `SegmentRisk`는
    `segment_id`만 갖고 타임코드·원문·번역문은 `Segment`에 있어 FR-7.2가 요구한
    필드를 채우려면 둘이 함께 필요하다.
    """

    source_lang: str
    target_lang: str
    profile_name: str
    policy_label: str
    policy_kind: str  # "budget" | "threshold"
    policy_value: float
    risks: tuple[SegmentRisk, ...]
    segments: tuple[Segment, ...]
    excluded_failures: int
    usage: TokenUsage | None

    @property
    def triaged_segments(self) -> int:
        """트리아지 대상 수. **`review_ratio`의 분모다** (설계 §6.2)."""
        return len(self.risks)

    @property
    def total_segments(self) -> int:
        """트랙 전체. `triaged + excluded`가 이 값이 되어야 파일 안에서 검산된다."""
        return len(self.risks) + self.excluded_failures

    @property
    def selected(self) -> tuple[SegmentRisk, ...]:
        """검수 큐에 담긴 것. `review.json`의 `segments[]`가 이것이다 (설계 D3)."""
        return tuple(r for r in self.risks if r.selected)

    @property
    def selected_for_review(self) -> int:
        return len(self.selected)

    @property
    def hard_fail_count(self) -> int:
        return sum(1 for r in self.risks if r.hard_fail)

    @property
    def signal_hits(self) -> dict[str, int]:
        """신호별 적발 건수. **전체를 본다 - 선별분이 아니다.**

        선별분으로 좁히면 예산 밖으로 밀린 위험이 사라져 사용자가 다음 예산을
        정할 근거를 잃는다. 정렬은 NFR-3(재현성)이다 - `Counter`의 순서는 삽입
        순이라 세그먼트 순서가 바뀌면 출력이 달라진다.
        """
        counts: Counter[str] = Counter()
        for risk in self.risks:
            counts.update(risk.reasons)
        return dict(sorted(counts.items()))

    @property
    def review_ratio(self) -> float:
        """실제 검수 비율 (0.0~1.0). 라이브러리 함수를 그대로 쓴다."""
        return _review_ratio(self.risks)
```

- [ ] **Step 4: `__init__.py`를 쓴다**

```python
"""검수 리포트 산출물 (요구사항정의서 §7 · FR-7.2)."""

from __future__ import annotations

from cuesift.report.models import TriageOutcome

__all__ = ["TriageOutcome"]
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_report_models.py -v
```

기대: **11 passed.**

- [ ] **Step 6: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/report/ tests/test_report_models.py
git commit -m "기능: TriageOutcome 모델 - 트리아지 수치의 단일 출처 (FR-7.2)"
```

pytest는 **1110 collected · 1107 passed · 3 deselected**여야 한다(착수 시점 1096 passed + 11).
**`collected`와 `passed`를 구분해 읽어라** — 이 리포는 `deselected 3`이 고정이라 두 수가 항상 3만큼 다르다.
아래 태스크의 예고값은 전부 **passed 기준**이다.

### Task 1 — 구현 중 바뀐 결정

**위 코드 블록은 착수 시점 판이다. 최신은 `tests/test_report_models.py`와 `src/cuesift/report/models.py` 자체다.** 이 저장소의 규약을 따른다 — "구현 중 바뀐 결정" 절이 있으면 본문 코드 블록보다 그쪽이 최신이다.

리뷰가 변이 28종을 실행해 **6종이 살아남는 것**을 잡았다. 원인은 전부 위 코드 블록이 지정한 **픽스처의 대칭성**이었다.

| 무엇이 바뀌었나 | 왜 |
| --- | --- |
| `hard_fail_count` 픽스처를 2개 중 1개 → **3개 중 2개** | 대칭이면 여집합 변이(`if not r.hard_fail`)가 **같은 값 1**을 내며 통과한다 |
| `triaged_segments` 픽스처를 risks 1개 → **2개(선별 1)·excluded 3** | risks 1개·selected 0개면 `len(risks)`·여집합·`len(segments)`가 **전부 1**이다 |
| `signal_hits` 픽스처에 **중복 사유** 추가 | 모든 reason이 1회씩이면 `{k: 1 for k in ...}`로 바꿔도 통과한다. 지금 테스트는 `signal_hits`를 `set`으로 바꿔도 통과했다 |
| `selected` 순서 단언 추가 (선별 2개) | `tuple(reversed(...))`가 살아남았다. 이 순서가 `review.json`의 `segments[]` 순서다(NFR-3) |
| `__post_init__` 불변식 **2건** 신설 | `segments`와 `risks`의 세그먼트 **집합** 일치 · `excluded_failures >= 0`. 후자는 `-5`가 통과해 `total_segments == -5`를 냈다 |
| `total_segments`가 `triaged_segments`를 재사용 | `len(self.risks)`가 두 곳에 있어 정의가 2벌이었다 |
| `cli.py:1208-1210`의 근거 주석을 `signal_hits` 독스트링으로 이관 | Task 4가 원본을 폐기하면 근거가 사라진다 |

**세그먼트 집합 검증은 순서를 고정하지 않는다.** `_run_triage`가 `_outcome(tuple(scored), tuple(kept))`를 부르는데 `scored`는 **위험도 내림차순**이고 `kept`는 **트랙 원본 순서**라 둘의 순서는 정상적으로 다르다. 순서까지 비교하면 Task 4가 `ValueError`로 죽는다.

**위 표를 되돌리지 마라** — 각 항목은 실제로 살아남던 변이를 격추한 것이고, 되돌리면 그 구멍이 그대로 다시 열린다.

---

## Task 2: `build_review()` — 스키마 직렬화

**Files:**

- Create: `src/cuesift/report/json_report.py`
- Modify: `src/cuesift/report/__init__.py`
- Test: `tests/test_report_json.py`

**Interfaces:**

- Consumes: `TriageOutcome` (Task 1)
- Produces: `build_review(outcome: TriageOutcome) -> dict` — 설계 §6.1 스키마의 dict

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_report_json.py`를 만든다. Task 1의 헬퍼를 반복한다 — 태스크를 순서대로 읽지 않는 사람이 있다.

```python
"""`review.json` 스키마 직렬화 (FR-7.2 · 설계 §6)."""

from __future__ import annotations

import json

import pytest

from cuesift.report import TriageOutcome, build_review
from cuesift.segment import Segment, SegmentRisk, Signal, Span
from cuesift.translate.provider import TokenUsage


def _risk(
    seg_id: str,
    *,
    selected: bool = False,
    hard_fail: bool = False,
    reasons: list[str] | None = None,
    score: float = 0.5,
    signals: list[Signal] | None = None,
) -> SegmentRisk:
    return SegmentRisk(
        segment_id=seg_id,
        signals=[] if signals is None else signals,
        risk_score=score,
        hard_fail=hard_fail,
        selected=selected,
        reasons=[] if reasons is None else reasons,
    )


def _segment(seg_id: str, *, index: int = 0) -> Segment:
    return Segment(
        id=seg_id,
        index=index,
        start_ms=12000,
        end_ms=14500,
        source_text="원문",
        target_text="translated text",
    )


def _outcome(
    *,
    risks: tuple[SegmentRisk, ...],
    excluded_failures: int = 0,
    usage: TokenUsage | None = None,
) -> TriageOutcome:
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label="예산 10%",
        policy_kind="budget",
        policy_value=0.1,
        risks=risks,
        segments=tuple(_segment(r.segment_id, index=i) for i, r in enumerate(risks)),
        excluded_failures=excluded_failures,
        usage=usage,
    )


def test_summary가_재현성_필드를_전부_낸다() -> None:
    """설계 §3.5 — 파일만 보고 "무엇을 어느 규격으로 어떤 정책에서 걸렀나"를 알아야 한다.

    프로파일 이름이 없으면 `profiles[target]`에 **다른 언어의** 프로파일이
    들어가도 파일에서 잡을 수 없다(Task 2 리뷰가 같은 변이로 전 스위트를 통과시켰다).
    """
    doc = build_review(_outcome(risks=(_risk("00000", selected=True),)))

    summary = doc["summary"]
    assert summary["source_lang"] == "ko"
    assert summary["target_lang"] == "en"
    assert summary["profile"] == "en"
    assert summary["policy"] == {"kind": "budget", "value": 0.1}


def test_세그먼트_수가_셋으로_나뉘고_검산된다() -> None:
    doc = build_review(
        _outcome(risks=(_risk("00000", selected=True), _risk("00001")), excluded_failures=3)
    )

    summary = doc["summary"]
    assert summary["triaged_segments"] == 2
    assert summary["excluded_failures"] == 3
    assert summary["total_segments"] == 5
    assert summary["total_segments"] == summary["triaged_segments"] + summary["excluded_failures"]


def test_segments에는_선별된_것만_담긴다() -> None:
    """설계 D3 — FR-7.2가 "검수 **대상** 세그먼트 목록"이다."""
    doc = build_review(_outcome(risks=(_risk("00000", selected=True), _risk("00001"))))

    assert [s["id"] for s in doc["segments"]] == ["00000"]


def test_세그먼트_본문이_Segment에서_조인된다() -> None:
    """`SegmentRisk`에는 타임코드·원문·번역문이 없다 (설계 §7.1)."""
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, score=0.87),)))

    seg = doc["segments"][0]
    assert seg["start_ms"] == 12000
    assert seg["end_ms"] == 14500
    assert seg["source_text"] == "원문"
    assert seg["target_text"] == "translated text"
    assert seg["risk_score"] == pytest.approx(0.87)


def test_신호가_detail을_통째로_싣는다() -> None:
    """설계 D4 — `signals/llm.py:110`이 review.json을 소비자로 명시했다.

    잘라내면 FR-6.4의 "왜 선별되었는지"가 반쪽이 된다.
    """
    signal = Signal(
        name="length.ratio",
        tier=0,
        score=0.6,
        detail={"ratio": 2.1, "median": 1.0, "z": 3.2, "deviation": 1.1},
    )
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, signals=[signal]),)))

    assert doc["segments"][0]["signals"][0]["detail"] == {
        "ratio": 2.1,
        "median": 1.0,
        "z": 3.2,
        "deviation": 1.1,
    }


def test_spans가_side와_함께_실린다() -> None:
    """`side`가 없으면 FR-7.3 리포트가 원문과 번역문 중 어느 쪽을 칠할지 모른다."""
    signal = Signal(
        name="spec.violation",
        tier=0,
        score=1.0,
        spans=(Span(start=0, end=12, side="target"),),
        detail={"kinds": ["cps"], "count": 1},
    )
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, signals=[signal]),)))

    assert doc["segments"][0]["signals"][0]["spans"] == [
        {"start": 0, "end": 12, "side": "target"}
    ]


def test_cost가_범위를_명시하고_estimated_usd는_없다() -> None:
    """설계 D6 — NFR-2가 통화 환산을 v0.1 범위 밖으로 못 박았다.

    `includes`가 없으면 WP8b가 Tier 1을 붙인 뒤 같은 코드가 **과소 보고를
    시작하는데 그것을 알릴 수단이 없다.**
    """
    usage = TokenUsage(prompt_tokens=1234, completion_tokens=567, calls=8)
    doc = build_review(_outcome(risks=(_risk("00000", selected=True),), usage=usage))

    cost = doc["summary"]["cost"]
    assert cost["prompt_tokens"] == 1234
    assert cost["completion_tokens"] == 567
    assert cost["calls"] == 8
    assert cost["includes"] == ["translation"]
    assert "estimated_usd" not in cost, "NFR-2가 금지한 값이다"
    assert "tokens" not in cost, "정수 스칼라 tokens는 §8.4 정정으로 사라졌다"


def test_usage가_없으면_cost가_0을_낸다() -> None:
    """`--dry-run`이 아닌 경로에서 usage가 None인 것은 캐시 전량 히트 등이다.

    키를 빼면 소비자가 "집계 안 함"과 "0회 호출"을 구분하지 못한다.
    """
    doc = build_review(_outcome(risks=(_risk("00000", selected=True),), usage=None))

    assert doc["summary"]["cost"]["calls"] == 0
    assert doc["summary"]["cost"]["includes"] == ["translation"]


def test_signal_hits는_선별되지_않은_것도_센다() -> None:
    doc = build_review(
        _outcome(
            risks=(
                _risk("00000", selected=True, reasons=["spec.violation"]),
                _risk("00001", selected=False, reasons=["struct.empty"]),
            )
        )
    )

    assert doc["summary"]["signal_hits"] == {"spec.violation": 1, "struct.empty": 1}


def test_전량_실패해도_문서가_사실을_말한다() -> None:
    """`risks`가 비어도 파일을 낸다. 소비자가 "왜 비었나"를 알아야 한다."""
    doc = build_review(_outcome(risks=(), excluded_failures=10))

    assert doc["segments"] == []
    assert doc["summary"]["triaged_segments"] == 0
    assert doc["summary"]["excluded_failures"] == 10
    assert doc["summary"]["total_segments"] == 10
    assert doc["summary"]["review_ratio"] == 0.0


def test_dict가_json_직렬화_가능하다() -> None:
    """설계 §3.2 — 지금은 신호 10종의 detail이 전부 원시값이다."""
    signal = Signal(name="glossary.miss", tier=0, score=0.5, detail={"terms": ["용어"]})
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, signals=[signal]),)))

    text = json.dumps(doc, ensure_ascii=False)
    assert json.loads(text) == doc
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_report_json.py -v
```

기대: `ImportError: cannot import name 'build_review'`.

- [ ] **Step 3: `json_report.py`를 쓴다**

```python
"""`review.json` 직렬화 (요구사항정의서 §8.4 · FR-7.2).

**스키마는 이 파일이 정하지 않는다.** 요구사항정의서 §8.4가 계약이고 여기는
그것을 채운다. 필드를 늘리거나 이름을 바꾸려면 §8.4를 먼저 고친다 - 파일을
읽는 스크립트가 이미 밖에 있을 수 있다.
"""

from __future__ import annotations

from typing import Any

from cuesift.report.models import TriageOutcome
from cuesift.segment import Segment, SegmentRisk, Signal

# 지금 집계되는 비용 계층. **WP8b가 Tier 1을 CLI에 붙이면 `"tier1"`을 더한다.**
#
# 이 목록이 없으면 Tier 1을 켠 실행에서 `cost`가 번역 토큰만 세면서 전체인 척
# 하고, 그 사실을 알릴 수단이 없다 - `collect_tier1`이 `TranslationResult.usage`를
# 올려 보낼 통로를 아직 갖고 있지 않기 때문이다(NFR-2 · FR-7.4).
_COST_INCLUDES = ["translation"]


def build_review(outcome: TriageOutcome) -> dict[str, Any]:
    """트리아지 결과를 §8.4 스키마의 dict로 만든다."""
    by_id = {seg.id: seg for seg in outcome.segments}
    usage = outcome.usage

    return {
        "summary": {
            # 재현성 필드 - 파일만 보고 "무엇을 어느 규격으로 어떤 정책에서
            # 걸렀나"를 알 수 있어야 한다(설계 §3.5). 리포트 파일은 옮겨지고
            # 첨부되고 며칠 뒤에 열린다.
            "source_lang": outcome.source_lang,
            "target_lang": outcome.target_lang,
            "profile": outcome.profile_name,
            # 화면 라벨(`예산 10%`)이 아니라 정규화된 값이다. **`value`는 언제나
            # 비율이지 퍼센트가 아니다** - 퍼센트를 넣으면 소비자가 100배 틀린다.
            "policy": {"kind": outcome.policy_kind, "value": outcome.policy_value},
            # 셋을 함께 낸다 - `total = triaged + excluded`가 파일 안에서
            # 검산된다(설계 §6.2). 하나로 합치면 `review_ratio`의 분모가
            # 무엇인지 알 수 없어 README 배수가 조용히 틀린다.
            "total_segments": outcome.total_segments,
            "triaged_segments": outcome.triaged_segments,
            "excluded_failures": outcome.excluded_failures,
            "selected_for_review": outcome.selected_for_review,
            "review_ratio": outcome.review_ratio,
            "hard_fail_count": outcome.hard_fail_count,
            "signal_hits": outcome.signal_hits,
            "cost": {
                "prompt_tokens": 0 if usage is None else usage.prompt_tokens,
                "completion_tokens": 0 if usage is None else usage.completion_tokens,
                "calls": 0 if usage is None else usage.calls,
                "includes": list(_COST_INCLUDES),
            },
        },
        # 선별된 것만 담는다(설계 D3) - FR-7.2가 "검수 **대상** 세그먼트
        # 목록"이다. 분모는 위 `summary`가 이미 냈다.
        "segments": [_segment_doc(risk, by_id[risk.segment_id]) for risk in outcome.selected],
    }


def _segment_doc(risk: SegmentRisk, segment: Segment) -> dict[str, Any]:
    """세그먼트 하나. `SegmentRisk`와 `Segment`를 조인한다."""
    return {
        "id": segment.id,
        # 타임코드가 이미 정수 밀리초라 변환이 없다 - §8.4가 내부 자료구조를
        # 거꾸로 규정했기 때문이다(`segment/models.py` 모듈 독스트링).
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "source_text": segment.source_text,
        "target_text": segment.target_text,
        "risk_score": risk.risk_score,
        "hard_fail": risk.hard_fail,
        "reasons": list(risk.reasons),
        "signals": [_signal_doc(s) for s in risk.signals],
    }


def _signal_doc(signal: Signal) -> dict[str, Any]:
    """신호 하나. `detail`은 통째로 싣는다(설계 D4).

    잘라내면 FR-6.4가 요구한 "왜 선별되었는지"의 증거가 사라진다 -
    `self_consistency.samples`는 "왜 이 번역이 불안정하다고 봤는가"의 유일한
    자료다. 크기는 selected만 담는 것(D3)과 Tier 1이 후보에만 도는 것
    (`max_ratio`)으로 이중 축소된다.
    """
    return {
        "name": signal.name,
        # 계층을 키가 아니라 **값**으로 둔다 - 그래서 Tier 1·Tier 2가 붙어도
        # 스키마가 깨지지 않는다(설계 §1.3).
        "tier": signal.tier,
        "score": signal.score,
        # `side`를 뺄 수 없다 - FR-7.3 리포트가 원문과 번역문 중 어느 쪽을
        # 칠할지 가르는 유일한 판별자다.
        "spans": [{"start": s.start, "end": s.end, "side": s.side} for s in signal.spans],
        "detail": dict(signal.detail),
    }
```

`SegmentRisk`를 직접 import해도 **순환이 생기지 않는다** — `cuesift/segment/models.py`는 `dataclasses`와 `typing`만 import하고 `cuesift.report`를 참조하지 않는다(착수 시점 실측).

- [ ] **Step 4: `__init__.py`에 재수출을 더한다**

```python
"""검수 리포트 산출물 (요구사항정의서 §7 · FR-7.2)."""

from __future__ import annotations

from cuesift.report.json_report import build_review
from cuesift.report.models import TriageOutcome

__all__ = ["TriageOutcome", "build_review"]
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_report_json.py -v
```

기대: **11 passed.**

- [ ] **Step 6: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/report/ tests/test_report_json.py
git commit -m "기능: review.json 스키마 직렬화 - §8.4 계약 구현 (FR-7.2)"
```

**1118 passed**(1107 + 11).

---

## Task 3: `write_review()` — 파일 쓰기

**Files:**

- Modify: `src/cuesift/report/json_report.py`
- Modify: `src/cuesift/report/__init__.py`
- Test: `tests/test_report_json.py` (같은 파일에 추가)

**Interfaces:**

- Consumes: `build_review` (Task 2)
- Produces: `write_review(outcome: TriageOutcome, path: Path) -> None` — 부모 디렉터리를 만들고 UTF-8로 쓴다. `OSError`·`TypeError`를 **잡지 않고 전파한다**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_report_json.py` 끝에 추가한다. import에 `from pathlib import Path`와 `write_review`를 더한다.

```python
def test_파일을_utf8로_쓴다(tmp_path: Path) -> None:
    """한국어 원문이 `\\uXXXX`로 이스케이프되면 사람이 못 읽는다."""
    out = tmp_path / "nested" / "ep01.en.review.json"

    write_review(_outcome(risks=(_risk("00000", selected=True),)), out)

    assert out.exists(), "부모 디렉터리를 만들지 않았다"
    text = out.read_text(encoding="utf-8")
    assert "원문" in text, "ensure_ascii를 끄지 않았다"
    assert json.loads(text)["summary"]["target_lang"] == "en"


def test_쓰기_실패는_OSError로_전파된다(tmp_path: Path) -> None:
    """호출자(CLI)가 exit 66으로 바꾼다. 여기서 삼키면 종료 코드를 잃는다."""
    blocker = tmp_path / "blocked"
    blocker.write_text("파일이다", encoding="utf-8")

    with pytest.raises(OSError):
        write_review(_outcome(risks=(_risk("00000", selected=True),)), blocker / "x.json")


def test_직렬화_불가값은_TypeError로_전파된다(tmp_path: Path) -> None:
    """설계 §8 — v0.2 QE 플러그인이 `detail`에 비원시값을 넣을 수 있다.

    조용히 빈 파일을 남기는 것보다 시끄럽게 죽는 편이 낫다. 호출자가 exit 70
    (내부 오류)으로 바꾼다 - exit 1("규격 위반 발견")로 새면 플러그인 결함이
    자막 결함으로 오보된다.
    """
    signal = Signal(name="qe.dummy", tier=2, score=0.5, detail={"model": object()})

    with pytest.raises(TypeError):
        write_review(
            _outcome(risks=(_risk("00000", selected=True, signals=[signal]),)),
            tmp_path / "x.json",
        )
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_report_json.py -k "utf8 or OSError or TypeError" -v
```

기대: `ImportError: cannot import name 'write_review'`.

- [ ] **Step 3: `write_review`를 쓴다**

`json_report.py`에 추가한다. import에 `import json`·`from pathlib import Path`를 더한다.

```python
def write_review(outcome: TriageOutcome, path: Path) -> None:
    """`review.json`을 쓴다 (FR-7.2 · 설계 §5.2).

    **예외를 잡지 않는다.** `OSError`는 호출자가 exit 66으로, `TypeError`
    (직렬화 불가)는 exit 70으로 바꾼다. 여기서 삼키면 파일이 없는데 종료
    코드가 0이 되어 다음 단계(배포 스크립트·CI)가 빈손으로 진행한다.

    **`ensure_ascii=False`가 필수다.** 이 프로젝트의 원문은 한국어이고
    `\\uc6d0\\ubb38`로 이스케이프되면 사람이 파일을 열어 읽을 수 없다 -
    FR-7.2의 수혜자가 검수자라는 사실이 이 인자 하나에 걸린다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # `indent=2`는 사람이 읽는 산출물이기 때문이다. diff도 줄 단위로 난다.
    text = json.dumps(build_review(outcome), ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
```

`json.dumps`를 먼저 끝내고 나서 쓰는 것이 요점이다 — `TypeError`가 나면 **파일이 만들어지지 않는다.** `json.dump(fp)`로 스트리밍하면 반쯤 쓰인 깨진 파일이 남는다.

- [ ] **Step 4: `__init__.py`에 재수출을 더한다**

```python
from cuesift.report.json_report import build_review, write_review
from cuesift.report.models import TriageOutcome

__all__ = ["TriageOutcome", "build_review", "write_review"]
```

- [ ] **Step 5: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_report_json.py -v
```

기대: **14 passed.**

- [ ] **Step 6: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/report/ tests/test_report_json.py
git commit -m "기능: review.json 파일 쓰기 - 예외를 전파해 종료 코드를 살린다 (FR-7.2)"
```

**1121 passed**(1118 + 3).

---

## Task 4: `_run_triage`가 `TriageOutcome`을 반환한다

**동작을 바꾸지 않는 리팩터링이다.** 화면 출력이 한 글자도 달라지면 안 된다 — 기존 테스트 1096개가 안전망이다.

**Files:**

- Modify: `src/cuesift/cli.py:1175-1244` (`_format_triage_summary`) · `:1245-1325` (`_run_triage`) · `:1092` (호출부)
- Modify: `tests/test_cli_triage.py:742` · `:767` (직접 호출 2곳)

**Interfaces:**

- Consumes: `TriageOutcome` (Task 1)
- Produces: `_run_triage(...) -> TriageOutcome | None` — 전량 실패도 `TriageOutcome`으로 낸다(`risks=()`). `_format_triage_summary(outcome: TriageOutcome) -> list[str]`

- [ ] **Step 1: 기존 테스트를 새 시그니처로 고친다**

`tests/test_cli_triage.py`의 두 호출을 바꾼다. 먼저 파일 상단 import에 `TriageOutcome`을 더한다.

```python
from cuesift.report import TriageOutcome
```

그리고 헬퍼를 하나 추가한다(두 테스트가 공유한다).

```python
def _outcome(risks: list[SegmentRisk], *, policy_label: str, excluded: int = 0) -> TriageOutcome:
    """`_format_triage_summary`가 받는 객체를 만든다.

    포매터는 `risks`·`policy_label`·`profile_name`·`excluded_failures`만 읽으므로
    나머지 필드는 이 테스트의 판정에 관여하지 않는다.
    """
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label=policy_label,
        policy_kind="budget",
        policy_value=0.1,
        risks=tuple(risks),
        # **`segments=()`를 쓸 수 없다.** 포매터는 이 필드를 읽지 않지만
        # `__post_init__`이 `risks`와 같은 세그먼트 집합을 요구하므로
        # (Task 1 fix, `models.py`) 빈 튜플은 `ValueError`로 거부된다.
        # 대응하는 더미를 만든다 — 값은 판정에 관여하지 않는다.
        segments=tuple(
            Segment(id=r.segment_id, index=i, start_ms=0, end_ms=1000, source_text="원문")
            for i, r in enumerate(risks)
        ),
        excluded_failures=excluded,
        usage=None,
    )
```

`Segment`를 import에 더한다 — `tests/test_cli_triage.py`는 이미 `SegmentRisk`를 갖고 있다.

```python
from cuesift.segment import Segment, SegmentRisk
```

`test_비율이_0_1퍼센트_미만이어도_0으로_보이지_않는다`(742줄 부근)의 호출을 바꾼다.

```python
    lines = _format_triage_summary(_outcome(risks, policy_label="예산 0.1%"))
```

`test_신호별_적발은_선별되지_않은_것도_센다`(767줄 부근)도 같다.

```python
    lines = _format_triage_summary(_outcome(risks, policy_label="예산 50%"))
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -k "0_1퍼센트 or 신호별_적발" -v
```

기대: `TypeError: _format_triage_summary() takes 0 positional arguments` 또는 키워드 인자 누락 오류. **두 테스트만** 실패해야 한다.

- [ ] **Step 3: `_format_triage_summary`의 시그니처를 바꾼다**

`cli.py`에서 함수 머리와 수치 계산부만 바꾼다. **본문의 출력 문자열은 한 글자도 건드리지 않는다.**

```python
def _format_triage_summary(outcome: TriageOutcome) -> list[str]:
    """트리아지 결과를 요약한다 (FR-7.4 · 설계 §7.1).

    **수치를 여기서 세지 않는다.** `TriageOutcome`의 프로퍼티를 읽는다 -
    `review.json`이 같은 수치를 내는데 두 곳에서 각자 세면 화면과 파일이
    갈라지고, 갈라져도 프로그램은 정상 종료하므로 종료 코드로는 알 수 없다
    (review.json 설계 D8).

    `excluded`가 0이면 괄호를 내지 않는다 - 실패가 없는 정상 실행에서
    "(번역 실패 0건 제외)"는 없는 문제를 있는 것처럼 보이게 한다. 실패 ID
    자체는 `_format_translate_summary`가 바로 위에서 나열했으므로 여기서
    반복하지 않는다(설계 §7.1).
    """
    total = outcome.triaged_segments
    selected = outcome.selected_for_review
    hard = outcome.hard_fail_count
    counts = outcome.signal_hits

    scope = f"  대상 세그먼트 {total}개"
    if outcome.excluded_failures:
        scope += f" (번역 실패 {outcome.excluded_failures}건 제외)"

    lines = [
        f"[{outcome.target_lang}] 트리아지 ({outcome.policy_label}, 프로파일 {outcome.profile_name})",
        scope,
        f"  검수 대상 {selected}개 (실제 {_format_ratio(outcome.review_ratio * 100)})",
        f"  hard fail {hard}개",
    ]
    if counts:
        lines.append("  신호별 적발")
        lines.extend(f"    {name} {count}개" for name, count in counts.items())
    return lines
```

**기존 독스트링에서 살릴 것**: "`risks`는 `select_by_*`가 돌려준 전체 목록이다" 문단과 "요청 예산과 실제 비율을 함께 낸다" 문단은 `TriageOutcome` 독스트링과 `_run_triage`로 옮긴다. 이 저장소는 근거 주석을 지우지 않는다.

`counts.items()`에 `sorted()`가 없는 것은 `signal_hits` 프로퍼티가 이미 정렬해 반환하기 때문이다. **정렬이 두 곳에 있으면 한쪽만 고쳐진다.**

- [ ] **Step 4: `_run_triage`가 `TriageOutcome`을 반환하게 한다**

```python
def _run_triage(
    *,
    target_lang: str,
    profile: SpecProfile,
    glossary: Glossary | None,
    source_lang: str,
    translated: TranslationResult,
    budget_ratio: float | None,
    threshold: float | None,
    policy_label: str,
) -> TriageOutcome:
    """번역 결과를 트리아지해 결과 객체를 낸다 (FR-6.1~6.3 · 설계 §4).

    **번역 실패분을 입력에서 뺀다.** (기존 독스트링 전문을 그대로 유지한다)

    **그러나 빼는 자리는 융합이지 수집이 아니다.** (기존 독스트링 전문을 그대로 유지한다)

    **전량 실패에서도 객체를 낸다.** 문자열을 조기 반환하면 `review.json`이
    "왜 비었나"를 말할 수 없다 - `risks=()`·`excluded_failures=N`이 그 사실을
    파일에 남긴다.
    """
    failed_ids = {f.segment_id for f in translated.failures}
    kept = [seg for seg in translated.segments if seg.id not in failed_ids]

    if budget_ratio is not None:
        policy_kind, policy_value = "budget", budget_ratio
    elif threshold is not None:
        policy_kind, policy_value = "threshold", threshold
    else:
        # 호출자가 트리아지를 요청하지 않았는데 여기 도달한 것이다.
        # 조용히 빈 결과를 내면 "트리아지가 돌았고 아무것도 안 걸렸다"로
        # 읽혀 미배선을 정상으로 오인한다.
        raise ValueError("budget_ratio와 threshold가 둘 다 None이다")

    def _outcome(risks: tuple[SegmentRisk, ...], segments: tuple[Segment, ...]) -> TriageOutcome:
        return TriageOutcome(
            source_lang=source_lang,
            target_lang=target_lang,
            profile_name=profile.name,
            policy_label=policy_label,
            policy_kind=policy_kind,
            policy_value=policy_value,
            risks=risks,
            segments=segments,
            excluded_failures=len(failed_ids),
            usage=translated.usage,
        )

    if not kept:
        return _outcome((), ())

    ctx = SignalContext(
        profile=profile,
        glossary=glossary,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    # (수집과 융합의 입력이 다른 이유를 적은 기존 주석 블록 전문을 그대로 유지한다)
    signals = collect_all(translated.segments, ctx)
    risks = [fuse(seg.id, signals[seg.id]) for seg in kept]

    if budget_ratio is not None:
        scored = select_by_budget(risks, budget_ratio)
    else:
        scored = select_by_threshold(risks, threshold)  # type: ignore[arg-type]

    return _outcome(tuple(scored), tuple(kept))
```

`select_by_threshold` 호출의 `type: ignore`는 위 분기에서 `threshold`가 `None`이 아님이 보장되지만 타입 검사기가 그것을 모르기 때문이다. **`assert threshold is not None`으로 대신할 수 있으면 그쪽이 낫다.**

- [ ] **Step 5: 호출부를 고친다**

`cli.py:1092` 부근. 전량 실패 메시지를 여기서 만든다.

```python
        try:
            outcome = _run_triage(
                target_lang=target_lang,
                profile=triage_profile,
                glossary=glossary,
                source_lang=source_lang,
                translated=translated,
                budget_ratio=budget_ratio,
                threshold=threshold,
                policy_label=policy_label,
            )
        except ValueError as exc:
            # (기존 주석 블록 전문을 그대로 유지한다)
            _echo(f"[{target_lang}] 트리아지를 돌리지 못했다: {exc}", err=True)
            return 2

        if not outcome.risks:
            # 전량 실패에서 `review_ratio`는 0.0을 내지만 "검수 대상 0개"는
            # "볼 것이 없다"로 읽힌다. 실제로는 **판정 자체를 못 한 것**이므로
            # 구별해 말한다.
            _echo(
                f"[{target_lang}] 트리아지: 번역된 세그먼트가 없어 건너뛴다 "
                f"(전량 {outcome.excluded_failures}건 실패)"
            )
        else:
            for line in _format_triage_summary(outcome):
                _echo(line)
```

`_run_triage`의 `ValueError`를 잡는 위치가 바뀌었다 — 이제 `select_by_*`의 범위·NaN 검사와 `fuse`의 가중치 검사가 `_run_triage` 안에서 나므로 **try 블록이 `_run_triage` 호출만 감싸면 된다.**

- [ ] **Step 6: import를 더한다**

`cli.py` 상단에 `from cuesift.report import TriageOutcome`을 더한다. `Segment`가 이미 import돼 있는지 확인하고, 없으면 `from cuesift.segment import Segment, SegmentRisk`로 넓힌다.

- [ ] **Step 7: 전 스위트가 통과하는지 확인한다 — 이것이 이 태스크의 게이트다**

```bash
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
```

기대: **1121 passed, 3 deselected.** 동작을 바꾸지 않는 리팩터링이므로 **개수가 늘지도 줄지도 않는다.** 하나라도 실패하면 출력이 달라진 것이다 — 문자열을 원상 복구한다.

- [ ] **Step 7b: 산식이 실제로 한 벌이 됐는지 확인한다 — 이 태스크의 존재 이유다**

**전 스위트 통과는 이것을 재지 못한다.** 스위트는 "출력이 같은가"만 보고, `_format_triage_summary`가 프로퍼티를 읽든 자기가 다시 세든 출력은 같다. Task 1 리뷰가 지적한 자리다(I4) — 그때는 2벌이 과도기라 이월했고, **여기가 그 과도기를 닫는 지점이다.**

```bash
sed -n '/^def _format_triage_summary/,/^def /p' src/cuesift/cli.py | grep -nE "sum\(|Counter\(|len\(risks\)|sorted\("
```

기대: **0건.** 하나라도 나오면 그 수치는 여전히 두 곳에서 계산되고 있다. `_format_triage_summary`는 `outcome`의 프로퍼티만 읽어야 한다.

교체 전 `cli.py`가 갖고 있던 산식은 넷이다 — `total = len(risks)` · `selected = sum(...)` · `hard = sum(...)` · `Counter` 루프와 `sorted(counts.items())`. 넷 다 `TriageOutcome`의 프로퍼티로 대체된다.

**`cli.py:1208-1210`의 근거 주석**(`reasons`는 0점 신호를 담지 않으므로 이 집계가 곧 적발 건수다)은 Task 1이 이미 `models.py`의 `signal_hits` 독스트링으로 옮겼다. 여기서 원본을 지울 때 **근거가 사라지지 않았는지 확인**하고, 옮겨져 있지 않으면 지우지 말고 보고하라.

- [ ] **Step 8: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/cli.py tests/test_cli_triage.py
git commit -m "리팩터링: _run_triage가 TriageOutcome을 반환한다 - 수치를 한 곳에서 센다"
```

---

## Task 5: `--review-out` 옵션과 조합 검증

**Files:**

- Modify: `src/cuesift/cli.py` (옵션 정의 `:490` 부근 · 검증 `:507` 부근 · `_review_path` 신설)
- Test: `tests/test_cli_review_out.py` (신설)

**Interfaces:**

- Produces: `_review_path(input_path: Path, review_dir: Path, source_lang: str, target_lang: str) -> Path` — `{stem}.{target}.review.json`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_review_out.py`를 만든다.

```python
"""`--review-out` CLI 표면 (FR-7.2 · 설계 §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from cuesift.cli import _review_path, app

runner = CliRunner()

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, fixture: str, *extra: str) -> list[str]:
    return [
        "translate",
        str(_FIXTURES / fixture),
        "--to",
        "en",
        "--out",
        str(tmp_path / "subs"),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--no-cache",
        *extra,
    ]


def test_stem_규칙이_자막_출력과_같다() -> None:
    """설계 D2 — 고정 이름은 입력 파일 여럿을 같은 디렉터리로 낼 때 서로를 지운다."""
    got = _review_path(Path("a/ep01.ko.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_source_태그가_없으면_덧붙인다() -> None:
    got = _review_path(Path("a/ep01.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_대문자_source_태그도_치환된다() -> None:
    """Windows는 파일명 대소문자를 구분하지 않아 `ep01.KO.srt`가 정상인 파일명이다.

    `endswith`가 대소문자를 구분해 치환에 실패하면 `ep01.KO.en.review.json`이라는
    이중 태그가 난다 - `_output_path`가 같은 사고를 이미 겪었다.
    """
    got = _review_path(Path("a/ep01.KO.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_review_out_단독은_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 D10 — 리포트를 기대했는데 조용히 안 나오는 것이 최악이다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app, _args(tmp_path, "minimal.srt", "--review-out", str(tmp_path / "reports"))
    )

    assert result.exit_code == 2, result.output
    assert "--review-out" in result.output


def test_review_out_단독은_dry_run에서도_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설계 D11 — 조합 오류는 실행 전에 알아야 한다.

    `and not dry_run`으로 미루면 사용자가 dry-run으로 확인하고 본 실행에서야
    오류를 만난다. 프로파일 전량 검사가 이미 같은 규칙을 따른다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(tmp_path, "minimal.srt", "--review-out", str(tmp_path / "reports"), "--dry-run"),
    )

    assert result.exit_code == 2, result.output


def test_예산만_주면_파일을_쓰지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--review-out` 없이 트리아지만 요청한 기존 사용법이 그대로 돈다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "10%"))

    assert result.exit_code == 0, result.output
    assert list(tmp_path.rglob("*.review.json")) == []


def test_review_out이_파일이면_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """typer `file_okay=False`가 먼저 거른다 - `--out`과 같은 방어다."""
    blocker = tmp_path / "notadir"
    blocker.write_text("파일이다", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(blocker)),
    )

    assert result.exit_code == 2, result.output
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_review_out.py -v
```

기대: `ImportError: cannot import name '_review_path'`.

- [ ] **Step 3: `_review_path`를 쓴다**

`cli.py`의 `_output_path` 바로 아래에 둔다 — **함께 바뀌는 것은 함께 있어야 한다.**

```python
def _review_path(
    input_path: Path, review_dir: Path, source_lang: str, target_lang: str
) -> Path:
    """검수 리포트 경로를 정한다 (FR-7.2 · review.json 설계 D2).

    stem 규칙은 `_output_path`와 같다 - `.{source_lang}`으로 끝나면 치환하고
    아니면 덧붙인다. 판정만 `casefold()`한다.

    **고정 이름(`review.{lang}.json`)을 쓰지 않는 이유는 덮어쓰기다.** `ep01`과
    `ep02`를 같은 `--review-out`으로 돌리면 뒤엣것이 앞엣것을 조용히 지우고,
    종료 코드는 0이며 경고도 없다.
    """
    stem = input_path.stem
    suffix = f".{source_lang}"
    if stem.casefold().endswith(suffix.casefold()):
        stem = stem[: -len(suffix)]
    return review_dir / f"{stem}.{target_lang}.review.json"
```

- [ ] **Step 4: 옵션을 더한다**

`dry_run` 바로 앞에 둔다.

```python
    review_out: Annotated[
        Path | None,
        typer.Option(
            "--review-out",
            # `file_okay=False`는 `--out`과 같은 이유다 - 출력 디렉터리 자리에
            # 이미 파일이 있으면 `FileExistsError`가 새어 exit 1로 오보된다.
            file_okay=False,
            # em dash(U+2014)를 쓰지 않는다(전역 제약, cp949 미인코딩).
            help="검수 리포트(review.json) 출력 디렉터리. --review-budget 또는 "
            "--review-threshold와 함께 써야 한다",
        ),
    ] = None,
```

- [ ] **Step 5: 조합 검증을 더한다**

`review_threshold` NaN 가드 **바로 뒤**에 둔다. `triage_requested` 계산보다 앞이어도 되지만, 읽는 순서를 검증 → 파싱으로 유지한다.

```python
    if review_out is not None and review_budget is None and review_threshold is None:
        # 리포트를 낼 트리아지 정책이 없다. 조용히 무시하면 사용자는 파일이
        # 없다는 사실을 다음 단계(배포 스크립트·CI)에서야 만난다.
        #
        # **`--dry-run`보다 앞에 둔다**(D11). 뒤로 미루면 dry-run으로 확인한
        # 명령이 본 실행에서 처음 실패한다.
        _echo(
            "--review-out은 --review-budget 또는 --review-threshold와 함께 써야 한다",
            err=True,
        )
        raise typer.Exit(2)
```

- [ ] **Step 6: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_review_out.py -v
```

기대: **7 passed.**

- [ ] **Step 7: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
git add src/cuesift/cli.py tests/test_cli_review_out.py
git commit -m "기능: --review-out 옵션과 조합 검증 (FR-7.2 · D2 · D10 · D11)"
```

**1128 passed**(1121 + 7).

---

## Task 6: 배선 — 파일을 실제로 쓴다

**Files:**

- Modify: `src/cuesift/cli.py` (`_translate_one` 시그니처 · 호출부 · 트리아지 블록)
- Test: `tests/test_cli_review_out.py` (추가)

**Interfaces:**

- Consumes: `write_review` (Task 3) · `_review_path` (Task 5) · `TriageOutcome` (Task 4)
- Produces: `_translate_one(..., review_out: Path | None)` — 종료 코드에 **70이 추가된다**

- [ ] **Step 1: 종료 코드 순서 성질을 확인한다 — 코드를 쓰기 전에 한다**

`cli.py:716` 부근에 이런 주석이 있다.

```text
종료 코드의 숫자 크기가 심각도 순과 일치한다: 0 < 1 < 2 < 66 < 69
(설계 §6.6 표의 6종 중 70을 뺀 5종 - `_translate_one`은 70을 내지 않는다).
이 성질이 깨지면 아래 max()가 틀린 코드를 낸다 - 새 코드를 추가할 때 반드시 확인한다.
```

**이 태스크가 정확히 그 조건을 깬다.** `_translate_one`이 70을 내기 시작하므로 주석을 갱신하고, `worst = max(worst, code)`에서 70이 69를 이기는 것이 옳은지 판단한다.

판단: **옳다.** 70은 "내부 오류"이고 69는 "외부 서비스 거부"다. 한 언어가 프로바이더 거부(69)이고 다른 언어가 직렬화 실패(70)라면, 보고해야 할 것은 우리 쪽 결함이다 — 사용자가 LLM 설정을 고쳐도 70은 사라지지 않는다. 주석을 이렇게 바꾼다.

```python
    # 종료 코드의 숫자 크기가 심각도 순과 일치한다: 0 < 1 < 2 < 66 < 69 < 70.
    # 70(내부 오류)이 69(외부 서비스 거부)를 이기는 것이 옳다 - 한 언어가
    # 프로바이더 거부이고 다른 언어가 직렬화 실패라면 보고할 것은 우리 쪽
    # 결함이다. 사용자가 LLM 설정을 고쳐도 70은 사라지지 않는다.
    # **이 성질이 깨지면 아래 max()가 틀린 코드를 낸다** - 새 코드를 추가할 때
    # 반드시 확인한다. review.json 배선(FR-7.2)이 70을 추가하며 이 주석을 갱신했다.
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/test_cli_review_out.py`에 추가한다. 먼저 import를 넓힌다 — `_blank_at`이 `ScriptedProvider`를 쓴다.

```python
from tests.fakes.provider import EchoProvider, ScriptedProvider
```

최우선 게이트가 첫 번째다.

```python
def _read_review(tmp_path: Path, name: str = "minimal.en.review.json") -> dict:
    return json.loads((tmp_path / "reports" / name).read_text(encoding="utf-8"))


def _blank_at(indices: set[int], count: int) -> ScriptedProvider:
    """지정한 인덱스만 **공백 번역**으로 답하는 가짜 (`test_cli_triage.py:458`에서 옮김).

    공백 번역은 `engine.py:419`가 `reason="empty_translation"`으로 실패 처리한다 -
    응답 형식은 올바르므로 개별 폴백이 개입하지 않아 호출이 배치 1회로 끝난다.
    `EchoProvider(drop_last=True)`는 이 목적에 쓸 수 없다: 배치가 개수 불일치로
    실패하면 폴백이 개별 호출로 재시도하고 거기서는 `len(items) > 1`이 거짓이라
    **전부 성공한다.**
    """
    items = [{"id": i, "text": "   " if i in indices else f"EN{i}"} for i in range(count)]
    return ScriptedProvider([json.dumps({"translations": items}, ensure_ascii=False)])


def test_화면_요약과_파일_수치가_일치한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**이 설계에서 가장 조용한 실패다** (D8 · 게이트 10.1).

    갈라져도 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도 0이다.
    화면에서 파싱한 값과 `summary`를 대조하는 것만이 이것을 잡는다.

    **`_blank_at`을 쓰는 것이 이 게이트의 핵심이다** (사전 스캔 발견 A).
    기본 `EchoProvider()`는 한글 원문을 남겨 `struct.untranslated`가 **전량
    hard fail**을 내고, 그러면 `selected == triaged`가 되어 두 값을 뒤바꾸는
    변이가 **같은 값을 내며 통과한다** - 게이트가 통과하면서 아무것도 재지
    못하는 상태다. `_blank_at({2,5,9}, 10)`이면 `triaged=7`이고 예산 10%에서
    `quota=ceil(7*0.1)=1`이라 `selected=1 != triaged=7`로 갈린다.

    실패 3건이 있으므로 종료 코드는 **1**이다(FR-2.6). 리포트는 그보다 먼저 나간다.
    """
    _patch_provider(monkeypatch, _blank_at({2, 5, 9}, 10))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "ten_cues.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 1, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]

    assert summary["triaged_segments"] == 7
    assert summary["selected_for_review"] != summary["triaged_segments"], (
        "두 값이 같으면 이 게이트가 아무것도 재지 못한다"
    )
    assert f"  대상 세그먼트 {summary['triaged_segments']}개" in result.output
    assert f"  검수 대상 {summary['selected_for_review']}개" in result.output
    assert f"  hard fail {summary['hard_fail_count']}개" in result.output


def test_신호별_적발_집계가_화면과_일치한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.1의 나머지 절반 — `signal_hits` 대조는 집계가 비지 않아야 한다.

    **여기서는 기본 `EchoProvider()`를 일부러 쓴다.** 한글 원문이 남아
    `struct.untranslated`가 전량 hard fail을 내므로 `signal_hits`가 채워진다.
    위 테스트의 `_blank_at`은 번역문이 `EN0`·`EN1`이라 신호가 적거나 없을 수
    있고, 집계가 비면 아래 루프가 **한 번도 돌지 않아 아무것도 재지 못한다.**
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "ten_cues.srt",
            "--review-budget",
            "30%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 0, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]

    assert summary["signal_hits"], "집계가 비면 아래 루프가 아무것도 재지 못한다"
    for name, count in summary["signal_hits"].items():
        assert f"    {name} {count}개" in result.output


def test_total이_triaged와_excluded의_합이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.2 — 분모가 조용히 바뀌면 README 배수가 무너진다.

    **`excluded`가 0이면 `total == triaged + 0`이 항등식이 되어 아무것도 재지
    못한다** (사전 스캔 발견 B). `_blank_at`으로 실패 3건을 만들어야 검산이
    성립한다.
    """
    _patch_provider(monkeypatch, _blank_at({2, 5, 9}, 10))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "ten_cues.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 1, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]
    assert summary["excluded_failures"] == 3, "실패가 0이면 아래 검산이 항등식이 된다"
    assert summary["total_segments"] == 10
    assert summary["total_segments"] == summary["triaged_segments"] + summary["excluded_failures"]


def test_입력이_둘이면_파일이_서로를_지우지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.3 — 덮어쓰기는 종료 코드가 0이고 경고도 없다."""
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    for fixture in ("minimal.srt", "ten_cues.srt"):
        result = runner.invoke(
            app, _args(tmp_path, fixture, "--review-budget", "50%", "--review-out", str(reports))
        )
        assert result.exit_code == 0, result.output

    assert sorted(p.name for p in reports.glob("*.review.json")) == [
        "minimal.en.review.json",
        "ten_cues.en.review.json",
    ]


def test_dry_run은_파일을_쓰지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D11 — dry-run은 트리아지를 돌리지 않으므로 낼 것이 없다."""
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(reports),
            "--dry-run",
        ),
    )

    assert result.exit_code == 0, result.output
    assert not reports.exists() or list(reports.glob("*.review.json")) == []


def test_쓰기_실패는_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D12 — 디스크 상태의 문제이지 명령줄 오류가 아니다.

    번역 파일은 이미 나갔다는 것도 함께 고정한다 - 리포트만 못 쓴 것이지
    번역이 실패한 것이 아니다(설계 §3.4).
    """
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("디스크가 가득 찼다")

    monkeypatch.setattr("cuesift.cli.write_review", boom)

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 66, result.output
    assert (tmp_path / "subs" / "minimal.en.srt").exists(), "번역까지 잃었다"


def test_직렬화_실패는_exit_70이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 §8 — exit 1("규격 위반 발견")로 새면 내부 결함이 자막 결함으로 오보된다."""
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise TypeError("Object of type object is not JSON serializable")

    monkeypatch.setattr("cuesift.cli.write_review", boom)

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 70, result.output


def test_언어별로_파일이_나오고_프로파일이_각각_다르다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.4-4 — 프로파일 이름이 값 검증의 유일한 수단이다.

    `profiles[target] = load_builtin("ko")` 변이가 전 스위트를 통과한 전례가 있다
    (Task 2 리뷰 축A I4) - 키 집합만 검증되고 값은 검증되지 않았다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    args = _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(reports))
    args[args.index("--to") + 1] = "en,ja"
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    en = json.loads((reports / "minimal.en.review.json").read_text(encoding="utf-8"))
    ja = json.loads((reports / "minimal.ja.review.json").read_text(encoding="utf-8"))

    assert en["summary"]["profile"] == "en"
    assert ja["summary"]["profile"] == "ja"
    assert en["summary"]["target_lang"] == "en"
    assert ja["summary"]["target_lang"] == "ja"


def test_전량_실패해도_파일이_사실을_말한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """번역이 전량 실패해도 리포트를 낸다. 소비자가 "왜 비었나"를 알아야 한다.

    파일이 아예 없으면 "실행이 안 됐다"와 "번역이 전량 실패했다"를 구분하지 못한다.

    `garbage=True`면 **배치도 개별 폴백도 전부 파싱 실패한다**
    (`tests/test_cli_translate.py:215`의 주석과 같은 성질). 그때 종료 코드는
    **1**(실패한 세그먼트가 있다)이고 리포트는 그보다 먼저 나간다.
    """
    _patch_provider(monkeypatch, EchoProvider(garbage=True))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 1, result.output
    doc = _read_review(tmp_path)
    assert doc["summary"]["triaged_segments"] == 0
    assert doc["summary"]["excluded_failures"] > 0
    assert doc["summary"]["total_segments"] == doc["summary"]["excluded_failures"]
    assert doc["segments"] == []
```

- [ ] **Step 3: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_review_out.py -v
```

기대: 새 테스트 9개가 전부 실패(파일이 만들어지지 않는다).

- [ ] **Step 4: `_translate_one`에 `review_out`을 더한다**

시그니처에 인자를 더한다.

```python
    review_out: Path | None,
```

트리아지 블록의 요약 출력 **뒤**에 파일 쓰기를 둔다.

```python
        if outcome.risks or outcome.excluded_failures:
            # 전량 실패도 파일을 낸다 - `segments`가 비고 `excluded_failures`가
            # 사실을 말한다. 파일이 아예 없으면 소비자는 "실행이 안 됐다"와
            # "번역이 전량 실패했다"를 구분하지 못한다.
            if review_out is not None:
                review_path = _review_path(input_path, review_out, source_lang, target_lang)
                try:
                    write_review(outcome, review_path)
                except OSError as exc:
                    # 디스크 상태의 문제다. 번역 파일은 이미 나갔다(설계 §3.4).
                    _echo(f"{review_path}: 검수 리포트를 쓰지 못했다 - {exc}", err=True)
                    return EXIT_BAD_INPUT
                except TypeError as exc:
                    # `detail`에 직렬화 불가값이 들어왔다. **exit 1로 새면
                    # 내부 결함이 "규격 위반 발견"으로 오보된다.**
                    _echo(f"{review_path}: 검수 리포트를 직렬화하지 못했다 - {exc}", err=True)
                    return EXIT_NOT_IMPLEMENTED
                _echo(f"  리포트 {review_path}")
```

- [ ] **Step 5: 호출부에 인자를 넘긴다**

`cli.py:697` 부근의 `_translate_one(...)` 호출에 `review_out=review_out,`을 더한다.

- [ ] **Step 6: import를 더한다**

`from cuesift.report import TriageOutcome, write_review`로 넓힌다.

- [ ] **Step 7: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_review_out.py -v
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
```

기대: `test_cli_review_out.py` **16 passed**(5의 7 + 6의 9), 전체 **1137 passed, 3 deselected**.

- [ ] **Step 8: 실물로 확인한다**

Ollama가 `127.0.0.1:11434`를 이미 듣고 있다(트레이 앱이 자동 기동한다).

```bash
.venv/Scripts/python.exe -m cuesift translate tests/fixtures/ingest/ten_cues.srt \
  --to en --source-lang ko \
  --base-url http://localhost:11434/v1 --model qwen2.5:3b \
  --out /tmp/subs --review-budget 30% --review-out /tmp/reports
cat /tmp/reports/ten_cues.en.review.json
```

**파일을 눈으로 연다.** 한국어가 이스케이프되지 않았는지, `estimated_usd`가 없는지, `includes`가 `["translation"]`인지 확인한다. 경로는 이 환경에 맞게 바꾼다(Windows에서는 `$env:TEMP` 아래).

- [ ] **Step 9: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/cli.py tests/test_cli_review_out.py
git commit -m "기능: review.json 배선 - translate가 검수 리포트를 낸다 (FR-7.2 · FR-6.4)"
```

---

## Task 7: 게이트를 버그 버전에서 실패시켜 확인한다

**이 저장소의 규율이다** — 회귀 테스트는 버그 코드에서 실제로 실패하는 것을 본 뒤에야 회귀 테스트다. 길이비 회귀 테스트가 버그 버전에서도 통과해 데이터를 다시 짠 전례가 있다.

**Files:** 없음(변이를 넣고 되돌린다). 기록만 커밋 메시지에 남긴다.

- [ ] **Step 1: 변이 12종을 하나씩 넣고 해당 테스트만 죽는지 확인한다**

| # | 변이 | 죽어야 할 테스트 |
| --- | --- | --- |
| 1 | `build_review`에서 `selected_for_review`를 `len(outcome.risks)`로 | `test_화면_요약과_파일_수치가_일치한다` (`_blank_at`이라 selected=1 · triaged=7로 갈린다) |
| 2 | `TriageOutcome.total_segments`를 `len(self.risks)`로 | `test_total은_triaged와_excluded의_합이다` · `test_세그먼트_수가_셋으로_나뉘고_검산된다` · `test_total이_triaged와_excluded의_합이다`(CLI) |
| 2b | `signal_hits`를 `self.selected`에서 세도록 | `test_signal_hits는_선별되지_않은_것도_센다` |
| 3 | `review_ratio` 프로퍼티의 인자를 `self.selected`로 | `test_review_ratio는_triaged를_분모로_쓴다` |
| 4 | `_review_path`를 `review_dir / f"review.{target_lang}.json"`으로 | `test_stem_규칙이_자막_출력과_같다` · `test_입력이_둘이면_파일이_서로를_지우지_않는다` |
| 5 | `build_review`의 `segments`를 `outcome.risks`로 | `test_segments에는_선별된_것만_담긴다` |
| 6 | 조합 검증(`--review-out` 단독)을 지운다 | `test_review_out_단독은_exit_2다` |
| 7 | 조합 검증에 `and not dry_run`을 더한다 | `test_review_out_단독은_dry_run에서도_exit_2다` |
| 8 | `_signal_doc`의 `detail`을 `{}`로 | `test_신호가_detail을_통째로_싣는다` |
| 9 | `except OSError` 블록을 지운다 | `test_쓰기_실패는_exit_66이다` |
| 10 | `cost`에 `"estimated_usd": 0.0`을 되살린다 | `test_cost가_범위를_명시하고_estimated_usd는_없다` |
| 11 | `_signal_doc`의 `spans`에서 `side`를 뺀다 | `test_spans가_side와_함께_실린다` |
| 12 | `profile_name`을 `"ko"` 고정으로 | `test_언어별로_파일이_나오고_프로파일이_각각_다르다` |

각 변이마다:

```bash
# 변이를 넣는다
.venv/Scripts/python.exe -m pytest tests/ -x -q 2>&1 | tail -20
# 죽은 테스트 이름을 기록하고 되돌린다
git checkout -- src/cuesift/
```

- [ ] **Step 2: 변이가 통과해 버리면 테스트를 고친다**

**"통과했다"는 그 테스트가 아무것도 재고 있지 않다는 뜻이다.** 데이터를 바꾸거나 단언을 좁힌다. 실제 사례: 프로파일 이름 변이가 전 스위트를 통과했고(키 집합만 검증), `--dry-run` 트리아지에 게이트가 0건이었다.

- [ ] **Step 3: 되돌린 상태에서 전 스위트를 다시 돌린다**

```bash
git status --short   # 변경 0건이어야 한다
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
```

기대: **1137 passed, 3 deselected**(Task 7은 코드를 남기지 않으므로 Task 6과 같다). `git status`가 깨끗하지 않으면 변이가 남아 있다.

- [ ] **Step 4: 확인 기록을 커밋한다**

변이 검증은 코드를 남기지 않으므로 **기록이 유일한 산출물이다.** 어느 변이에서 어느 테스트가 어떤 값으로 죽었는지 커밋 메시지에 적는다.

```bash
git commit --allow-empty -m "검증: review.json 게이트 12종을 버그 버전에서 실패시켜 확인했다

(변이별 죽은 테스트와 실측값을 여기 적는다)"
```

---

## Task 8: 문서 정정

**Files:**

- Modify: `docs/요구사항정의서.md` (§8.4 · §5.6 · §5.7)
- Modify: `docs/WBS.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `HANDOFF.md`

- [ ] **Step 1: 요구사항정의서 §8.4를 정정한다**

설계 §6.1의 JSON 전문으로 교체한다. 변경점 넷을 절 아래에 표로 남긴다 — **무엇이 왜 바뀌었는지가 없으면 다음 사람이 원상 복구한다.**

| 변경 | 근거 |
| --- | --- |
| `estimated_usd` 제거 | NFR-2가 통화 환산을 v0.1 범위 밖으로 못 박았다 |
| `cost`를 `prompt_tokens`·`completion_tokens`·`calls`·`includes`로 | 화면이 이미 세 값을 낸다. `includes`가 집계 범위를 밝힌다 |
| `summary`에 재현성 필드 5개 | 파일만 보고 "무엇을 어느 규격으로 어떤 정책에서 걸렀나"를 알아야 한다 |
| 세그먼트 수 3분할 | `review_ratio`의 분모가 파일 안에서 검산된다 |

- [ ] **Step 2: §5.7 FR-7.2와 §5.6 FR-6.4의 상태를 올린다**

FR-7.2를 ⬜ → ✅, FR-6.4를 🟡 → ✅로 바꾸고 각각 근거를 상태 칸에 적는다. FR-7.4는 🟡 유지하되 근거를 갱신한다(`cost`가 파일에 실리지만 Tier 1분이 여전히 빠진다).

- [ ] **Step 3: 판정 규칙에 전수 대조한다 — 표기를 고치기 전에 한다**

요구사항정의서 §0.1이 요구한다. 상태 열이 있는 FR **13개**를 규칙(축 1: 층 / 축 2: 완전성)에 하나씩 대조한다.

기대: 완료 개수가 **32**. 다른 값이 나오면 **표기가 아니라 규칙을 먼저 의심한다** — 지금까지 네 번 다 틀린 쪽은 규칙이었다.

- [ ] **Step 4: WBS를 고친다**

| 무엇 | 어떻게 |
| --- | --- |
| 완료 개수 | 30 → **32**. 산수(`30 + FR-7.2 + FR-6.4`)를 표로 남긴다 |
| 작업 순서 | **WP8b를 WP5보다 앞에 둔 근거가 소멸했다는 사실을 남긴다**(설계 §1.3). 근거만 지우면 다음 사람이 같은 순서를 다시 세운다 |
| WP5 진척 | FR-7.2 완료 반영. 남은 것은 FR-7.3·FR-7.4 |
| 다음 1순위 | WP8b(Tier 1 CLI). 착수 전 정리 4건은 `HANDOFF.md`에 있다 |

- [ ] **Step 5: README에 사용법을 더한다**

`#### 검수 트리아지` 절에 `--review-out`을 더한다. 실제로 돌려 본 출력을 그대로 싣는다 — **문서가 약속한 동작에 게이트가 없는 상태를 만들지 않는다.** 세그먼트 목록 화면 출력 예시는 넣지 않는다(설계 D9가 금지했다. FR-7.3과 갈라진다).

- [ ] **Step 6: CHANGELOG와 HANDOFF를 갱신한다**

`[Unreleased]`에 Added(`--review-out`·`review.json`·`report` 패키지)와 Changed(`_run_triage` 반환 타입·§8.4 스키마·종료 코드 70 추가)를 적는다.

- [ ] **Step 7: 문서 게이트를 돌린다**

```bash
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**두 게이트의 파일 수가 일치해야 한다.** 갈라지면 추적 안 된 문서가 있다는 뜻이다. 링크 체커는 `git ls-files` 기준이므로 **새 파일은 `git add` 후에야 세어진다.**

- [ ] **Step 8: 전체 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
git add -A
git commit -m "문서: review.json 반영 - §8.4 스키마 정정·FR 상태·WBS 순서 근거 (FR-7.2)"
```

- [ ] **Step 9: PR을 만든다**

```bash
git push -u origin feat/review-json
gh pr create --base main
gh pr checks --watch
```

**푸시는 사용자가 명시적으로 요청할 때만 한다.** PR 본문에는 무엇을 · 근거 문서 · **게이트 수치**(개수를 그대로)를 담는다.

---

## 자체 검토 기록

**스펙 대조** — 설계의 13개 절을 태스크에 대응시켰다.

| 스펙 절 | 태스크 |
| --- | --- |
| §2 D1·D10·D11 (표면·조합 검증) | Task 5 |
| §2 D2 (파일명) | Task 5 |
| §2 D3·D4 (selected만·detail 통째) | Task 2 |
| §2 D5·D6·D7 (수 3분할·cost·정책) | Task 1·2 |
| §2 D8·D9 (결과 객체·패키지) | Task 1·4 |
| §2 D12 (exit 66) | Task 6 |
| §2 D13 (라이브러리 불변) | 전 태스크 — `signals`·`risk`·`triage`·`segment`를 수정 대상에 넣지 않았다 |
| §6 (스키마) | Task 2 |
| §7 (코드 구조) | Task 1·2·3·4 |
| §8·§8.1 (종료 코드) | Task 6 Step 1 — `EXIT_NOT_IMPLEMENTED = 70`이 이미 있어 **새 코드를 정의하지 않는다** |
| §9 (FR 대응) | Task 8 Step 2·3 |
| §10 (게이트) | Task 7 |
| §12 (문서 정정) | Task 8 |

**스펙과 달라진 것 둘 — 구현 중 결정이다.**

| 항목 | 스펙 | 계획 | 왜 |
| --- | --- | --- | --- |
| `TriageOutcome`의 정책 필드 | `policy_kind`·`policy_value` | **`policy_label`을 더한다** | 화면 라벨은 사용자가 친 원본 문자열(`"10%"`)을 보존한다. kind/value에서 재생성하면 출력이 `"예산 10.0%"`로 바뀌어 기존 테스트가 깨진다 |
| 직렬화 실패 종료 코드 | "구현 시점에 정한다"(§8.1) | **70** (`EXIT_NOT_IMPLEMENTED`, 기존 상수) | §8.1이 제시한 순서 1번 - "기존 코드 중 내부 결함에 해당하는 것이 있으면 그것을 쓴다". 대신 `max()` 심각도 순서 성질을 Task 6 Step 1에서 확인한다 |
