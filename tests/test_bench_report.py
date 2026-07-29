"""리포트 테스트 (설계 스펙 §7).

브리프 기본 6종(재현 헤더·3열 표·미튜닝 명시·미측정 신호·실주입 건수·JSON 라운드트립)에
"정직한 한계" 5건(컨트롤러 추가 요구)이 낳은 6개 요구 사항 테스트를 더한다. 항목 번호는
컨트롤러 브리프의 ①~⑥에 대응한다 — 문구가 리포트에서 조용히 사라지면 독자가 숫자를
오독하는데, 그 사라짐은 테스트 없이는 드러나지 않는다.
"""

from __future__ import annotations

import dataclasses
import json

from bench.measure import BudgetResult
from bench.report import RunMeta, render_markdown, write_report

# 실측값(Task 7 리포트) — en-ko. ja-ko는 0.9111%.
_HARD_FAIL_FP_RATE = 0.009556

META = RunMeta(
    pair="en-ko",
    seed=20260729,
    manifest_sha256="a" * 64,
    commit="deadbeef",
    sample_size=5000,
    excluded={"unfittable": 12},
    injected={"untranslated": 71, "negation": 71},
    unmeasured=("struct.tag_lost",),
    hard_fail_false_positive_rate=_HARD_FAIL_FP_RATE,
)
RESULTS = [
    BudgetResult(
        budget=0.05,
        review_ratio=0.062,
        recall=0.55,
        lift=8.9,
        oracle=0.62,
        by_kind={"negation": 0.0},
    ),
    BudgetResult(
        budget=0.10,
        review_ratio=0.104,
        recall=0.73,
        lift=7.0,
        oracle=1.0,
        by_kind={"negation": 0.0},
    ),
]
DROPS = {"struct.untranslated": 0.21, "spec.overlap": 0.0}
BASELINE = {0.05: (0.05, 0.006), 0.10: (0.10, 0.009)}


# --- 브리프 기본 6종 ---------------------------------------------------------


def test_report_contains_reproduction_header():
    """**헤더가 없으면 몇 달 뒤 자기 자신조차 검증할 수 없다.**"""
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    for needed in ("20260729", "a" * 16, "deadbeef", "5,000"):
        assert needed in md


def test_report_shows_all_three_columns_together():
    """요청 예산·실제 검수 비율·Recall을 항상 함께 낸다(스펙 §6.2).

    실제 비율을 빼면 배수가 어떻게 계산됐는지 독자가 검증할 수 없다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "요청 예산" in md and "실제 검수" in md and "Recall" in md


def test_report_states_weights_are_untuned():
    """스펙 §6.3 — 첫 리포트에 '가중치 미튜닝'을 명시한다."""
    assert "미튜닝" in render_markdown(META, RESULTS, DROPS, BASELINE)


def test_report_lists_unmeasured_signals():
    """FR-3.5는 이번 측정에서 빠진다. 표기하지 않으면 '검출 실패'로 읽힌다."""
    assert "struct.tag_lost" in render_markdown(META, RESULTS, DROPS, BASELINE)


def test_report_records_actual_injection_counts():
    """'용어 위반 Recall 100%'가 실은 '1건도 주입 못 했음'일 수 있다."""
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "71" in md


def test_write_report_emits_markdown_and_json(tmp_path):
    md_path, json_path = write_report(META, RESULTS, DROPS, BASELINE, tmp_path)
    assert md_path.exists() and json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["meta"]["pair"] == "en-ko"
    assert len(payload["results"]) == 2


# --- 정직한 한계 6종(컨트롤러 추가 요구 ①~⑥) --------------------------------


def test_report_states_hard_fail_false_positive_rate():
    """① hard fail 자연 오탐률을 실측치로 명시한다(스펙 §6.4 불변식 4).

    검출기 버그가 아니라 만/억·billion 표기 체계 차이 때문이라는 설명이
    없으면 독자가 '검출기가 오작동한다'로 오독한다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "hard fail 자연 오탐률" in md
    assert "0.96%" in md  # 0.009556 -> :.2%
    assert "billion" in md


def test_report_notes_budget_floor_indistinguishable():
    """② 예산 스윕 표 아래에 '요청 예산 < 실제 검수 비율' 구간 설명을 낸다(스펙 §6.2).

    hard fail이 예산을 우회해 낮은 예산 구간이 같은 행 값을 내는 것은 버그가
    아니라 스펙이 예측한 결과다. 설명이 없으면 버그로 오독한다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "구분되지 않는다" in md
    assert "FR-6.2" in md


def test_report_lists_unmeasurable_signals_separately_from_unmeasured():
    """③ `spec.overlap`은 '측정하지 못했다'이지 '검출 실패'가 아니다.

    FR-3.5의 `unmeasured`(일부러 뺐다)와 다른 절에 실려야 한다 — 같은 절에
    섞으면 "왜 뺐는지"의 사유가 뭉개진다.
    """
    meta = dataclasses.replace(META, unmeasurable=("spec.overlap",))
    md = render_markdown(meta, RESULTS, DROPS, BASELINE)
    assert "판정 불가 신호" in md
    assert "spec.overlap" in md
    assert "측정하지 못했다" in md
    # FR-3.5 절(미측정 신호)과 문구가 겹치지 않아야 사유 구분이 유지된다.
    unmeasurable_section = md.split("### 판정 불가 신호", 1)[1]
    assert "검출기는 구현돼 있으나" not in unmeasurable_section.split("## Recall", 1)[0]


def test_report_includes_negation_sample_bias_caveat():
    """④ `negation` 표본이 CPS 여유가 큰 짧은 세그먼트로 기운다는 사실을 싣는다.

    싣지 않으면 낮은 Recall이 '검출기가 약하다'로만 읽히고, 표본 자체가
    편향된 조건에서 잰 값이라는 것이 드러나지 않는다.
    """
    caveat = (
        "`negation`은 주입 결과가 규격을 위반하지 않는 세그먼트에만 넣는다. 부정어 삽입이 "
        "CPS를 넘기면 `spec.violation`이 발화해 **의미 반전이 아니라 길이 증가를 잡은 것**이 "
        "Recall로 집계되기 때문이다(정정 전 실측: en 79.7% / ja 93.7%가 `spec.violation`으로 "
        "발화). 그 결과 이 유형의 표본은 CPS 여유가 큰 짧은 세그먼트로 기운다."
    )
    meta = dataclasses.replace(META, caveats=(caveat,))
    md = render_markdown(meta, RESULTS, DROPS, BASELINE)
    assert "79.7%" in md
    assert "CPS 여유가 큰 짧은 세그먼트로 기운다" in md


def test_report_includes_glossary_tradeoff_caveat():
    """⑤ 용어집 대응률 79.8% 채택 기준이 남기는 대가를 싣는다.

    대가를 안 적으면 '용어집 위반 = 항상 진짜 오류'로 오독해 glossary.miss의
    기여도를 과신하게 된다.
    """
    caveat = (
        "용어집 30개는 en·ja 양쪽에서 대응률 79.8% 이상인 것만 채택했다. 그래도 **용어를 "
        "포함한 세그먼트의 약 20%는 깨끗한 트랙에서도 위반으로 잡힌다** — 대응어 목록이 실제 "
        "번역의 다양성을 다 담지 못하기 때문이다. `glossary.miss`는 hard fail이 아니라 예산을 "
        "우회하지 않는다."
    )
    meta = dataclasses.replace(META, caveats=(caveat,))
    md = render_markdown(meta, RESULTS, DROPS, BASELINE)
    assert "79.8%" in md
    assert "glossary.miss" in md


def test_report_states_measured_negation_recall():
    """⑥ `negation` Recall의 실측값을 예산과 함께 유형별 Recall 절에 적는다.

    정정(팀장, Task 7 리뷰어 재측정) — 예산 없이 숫자만 적으면 위 표(예산별로
    다른 값)와 어긋나 오독한다. 리포트 문구는 `results`에서 직접 값을 뽑아야
    재측정할 때마다 자동으로 맞는다 — 상수를 하드코딩하면 다음 실행에서
    조용히 거짓이 된다. 이 테스트는 그 값을 문자열로 재입력하지 않고
    `results`에서 계산한 값으로 검증해 그 회귀를 잡는다.
    """
    results = [
        BudgetResult(
            budget=0.05,
            review_ratio=0.062,
            recall=0.55,
            lift=8.9,
            oracle=0.62,
            by_kind={"negation": 0.0},
        ),
        BudgetResult(
            budget=0.10,
            review_ratio=0.104,
            recall=0.73,
            lift=7.0,
            oracle=1.0,
            by_kind={"negation": 0.0141},
        ),
        BudgetResult(
            budget=0.20,
            review_ratio=0.20,
            recall=0.866,
            lift=4.3,
            oracle=1.0,
            by_kind={"negation": 0.0986},
        ),
    ]
    md = render_markdown(META, results, DROPS, BASELINE)

    ref = next(r for r in results if r.budget == 0.10).by_kind["negation"]
    cmp = next(r for r in results if r.budget == 0.20).by_kind["negation"]
    assert f"{ref:.2%}" in md
    assert f"{cmp:.2%}" in md
    assert "예산 10%" in md
    assert "예산을 20%로 늘려도" in md
    assert "Tier 1" in md
