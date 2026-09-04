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
    injection_skipped={"glossary": 4041, "negation": 237},
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

    값 없이 "hard-fail 오탐 ≈ 0" 목표만 적으면 독자가 실측인지 주장인지
    구분할 수 없다. **값은 `hard_fail_false_positive_rate`에서 직접 뽑는다**
    — 문자열을 하드코딩하면 재측정 때 조용히 거짓이 된다.

    최종 리뷰 지적(I-2) — 옛 테스트는 `assert "billion" in md`로 "검출기
    버그가 아니라 한국어 만·억 vs 영어 billion 표기 차이 때문"이라는 특정
    문구를 회귀 방지선으로 고정했는데, 그 문구 자체가 틀렸다(ja-ko 자연
    오탐 41건 중 0건이 그 원인이었고, "검출기 버그가 아니다"도 거짓이었다).
    특정 언어쌍에 묶인 문구가 아니라 **값 자체**가 실렸는지로 검증해야
    이 절이 다시 언어쌍 무관한 사실로 바뀌어도 테스트가 안전하다.

    **그 판단이 실제로 값을 했다.** 당시 지목된 검출기 결함(NFKC 미정규화)은
    이후 수정됐고 원인 목록이 한자 수사로 교체됐는데, 이 테스트는 한 줄도
    고칠 필요가 없었다. `assert "NFKC" in md`로 고정했다면 수정과 함께
    실패했을 것이다 — **회귀 방지선은 사실이 아니라 계약을 고정해야 한다.**

    **픽스처가 하나면 "값에서 뽑은 것"과 "그 값을 박은 것"이 구분되지 않는다.**
    옛 버전은 `META` 하나로만 렌더해 `render_markdown`이 `"0.96%"`를 리터럴로
    하드코딩해도 통과했다(뮤테이션으로 실증). 서로 다른 두 값으로 렌더해야
    비로소 게이트가 된다 — 값 연동만으로는 부족하고 **값이 두 가지 이상**이어야
    한다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "hard fail 자연 오탐률" in md
    assert f"{META.hard_fail_false_positive_rate:.2%}" in md
    # 검출기의 알려진 한계(현재는 한자 수사)를 인정하고, "검출기 버그가
    # 아니라"는 부인은 더 이상 하지 않는다. 어느 한계인지는 수정에 따라
    # 바뀌므로 **한계를 인정한다는 사실**만 고정한다.
    assert "검출기의 알려진 한계" in md
    assert "검출기 버그가 아니라" not in md

    # 두 번째 값 — `report.py`가 실측값을 하드코딩할 리 없는 수치를 골랐다.
    other = dataclasses.replace(META, hard_fail_false_positive_rate=0.0123)
    other_md = render_markdown(other, RESULTS, DROPS, BASELINE)
    assert "1.23%" in other_md
    assert f"{META.hard_fail_false_positive_rate:.2%}" not in other_md


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


def test_report_carries_the_caveats_that_run_actually_uses():
    """**caveat 문구를 테스트 안에서 만들면 코드와 갈라져도 통과한다.**

    위아래의 두 caveat 테스트는 문자열을 스스로 지어 넘기므로 렌더링만 검사한다.
    그래서 주입기가 삽입 전용에서 제거 전용으로 바뀐 뒤에도 `bench.run`의
    negation caveat은 "부정어 삽입이 CPS를 넘기면"이라는 **일어나지 않는 실패**를
    편향의 원인으로 제시한 채 남았고, 전 스위트가 통과했다.

    이 테스트만 `bench.run.CAVEATS`를 실제로 임포트해 그 연결을 만든다.
    """
    from bench.run import CAVEATS

    md = render_markdown(dataclasses.replace(META, caveats=CAVEATS), RESULTS, DROPS, BASELINE)
    for note in CAVEATS:
        assert note in md, note[:40]

    negation_caveat = next(c for c in CAVEATS if "`negation`" in c)
    assert "제거" in negation_caveat, "주입기는 부정 표현을 제거한다 — 삽입 시절 서술이 남았다"
    assert "삽입하지 않는다" in negation_caveat


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


# --- fix 라운드 1: §4.4 코퍼스 제외 통계, 필드명 정정 -----------------------


def test_report_renames_excluded_section_to_injection_skipped():
    """`excluded` → `injection_skipped`. 제목·설명 문구가 의미를 바로잡는다.

    옛 제목 "제외 건수"는 `glossary 4,041`을 "5,000건 트랙에서 4,041건이
    빠졌다"로 읽히게 한다 — 실은 "용어집 주입 자격이 없었다"이다. 표본에서
    빠진 게 아니라는 것을 절 제목과 설명 둘 다에 못박는다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "주입 자격 미달 건수" in md
    assert "표본에서 제외된 것이 아니다" in md
    assert "glossary" in md and "4,041" in md
    # 옛 제목이 신규 제목의 부분 문자열이 아니므로 남아 있으면 잘못 고친 것이다.
    assert "### 제외 건수" not in md


def test_report_does_not_overclaim_other_kinds_absorbed_the_skips():
    """fix 라운드 2(리뷰어) — "다른 유형으로는 정상 주입됐다"는 산술적으로 틀렸다.

    en-ko에서 glossary가 스킵한 4,041건 중 다른 유형이 흡수할 수 있는 최대치는
    (전체 주입 500건 − glossary quota 72건) = 428건뿐이다. 나머지 3,613건 이상은
    끝까지 어느 유형에도 배정되지 않아 **오류 없이 깨끗한 채로 트랙에 남는다** —
    "다른 유형으로는 정상 주입됐다"라고 말하면 안 된다. 옛 문구가 조용히 되돌아오는
    것을 잡기 위해 부재도 함께 단언한다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "표본에서 제외된 것이 아니다" in md
    assert "다른 유형으로는 정상 주입됐다" not in md
    assert "오류 없이 깨끗한 상태로 트랙에 남는다" in md


def test_report_omits_corpus_stats_section_when_absent():
    """`corpus_stats=None`(기본값)이면 §4.4 절을 조용히 생략한다.

    기존 12건 픽스처(`META`)가 `corpus_stats`를 넘기지 않으므로, 이 테스트가
    통과해야 그 12건도 계속 통과한다.
    """
    assert META.corpus_stats is None
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "코퍼스 제외" not in md


def test_report_renders_corpus_stats_section_when_present():
    """§4.4 — ko 자막의 몇 %가 물리적으로 규격에 담기지 않는지는 그 자체가 결과다.

    비율은 `corpus_stats`에서 **계산**해야 한다(하드코딩 금지) — 그래야
    재측정 때마다 자동으로 맞는다. 이 테스트는 렌더링 결과에서 그 계산값을
    직접 검증해 하드코딩 회귀를 잡는다.
    """
    corpus_stats = {
        "total_pairs": 400_000,
        "filtered_out": {"empty": 100, "duplicate": 200, "ratio": 300},
        "kept_after_filter": 389_598,
        "unfittable": 194_463,
        "feasible": 195_135,
        "track_size": 5000,
    }
    meta = dataclasses.replace(META, corpus_stats=corpus_stats)
    md = render_markdown(meta, RESULTS, DROPS, BASELINE)

    assert "코퍼스 제외" in md
    assert "FR-5.4" in md
    assert "194,463" in md and "195,135" in md and "389,598" in md
    expected_ratio = corpus_stats["unfittable"] / corpus_stats["kept_after_filter"]
    assert f"{expected_ratio:.2%}" in md


# --- 리뷰어 Important: negation 기준선 표에 방법론·구분불가 판정 누락 -------


def test_negation_baseline_explains_review_ratio_sampling_methodology():
    """무작위 기준선이 **요청 예산이 아니라 실제 검수 비율**로 샘플링됐다는 것을

    본문에 명시한다. 없으면 예산 1%·2%·5% 행이 왜 전부 같은 값인지(hard fail로
    채워진 동일한 실제 검수 비율) 독자가 알 길이 없다 — 위 'Recall @ Budget'
    표에는 같은 설명이 이미 있는데 이 표에만 짝이 없으면 설명 수준이 비대칭이다.
    """
    by_kind_baseline = {"negation": {0.05: (0.05, 0.01), 0.10: (0.10, 0.02)}}
    md = render_markdown(META, RESULTS, DROPS, BASELINE, by_kind_baseline=by_kind_baseline)
    assert "요청 예산이 아니라 실제 검수 비율로 샘플링한다" in md


def test_negation_baseline_flags_statistically_indistinguishable_budget():
    """예산이 크면 Recall이 무작위 기준선과 구분되지 않는다 — **하드코딩 금지**,

    `|recall - mean| <= stdev`로 판정해야 한다. 이 테스트는 예산 10%(크게 벌어짐)와
    30%(기준선 표준편차 이내)를 함께 넣어, 판정이 문구가 아니라 실제 값에서
    계산됐는지 확인한다.
    """
    results = [
        *RESULTS,
        BudgetResult(
            budget=0.30,
            review_ratio=0.30,
            recall=0.894,
            lift=2.98,
            oracle=1.0,
            by_kind={"negation": 0.2958},
        ),
    ]
    by_kind_baseline = {
        "negation": {
            0.05: (0.02, 0.01),
            0.10: (0.10, 0.02),  # recall 0% vs 기준선 10% — 크게 벌어짐
            0.30: (0.2968, 0.0592),  # recall 29.58% vs 기준선 29.68%±5.92% — 표준편차 이내
        }
    }
    md = render_markdown(META, results, DROPS, BASELINE, by_kind_baseline=by_kind_baseline)

    assert "통계적으로 구분되지 않는다" in md
    assert "예산 30%(29.58% vs 29.68% ±5.92%)" in md
    # 예산 10%는 크게 벌어져 있으므로 구분 불가 목록에 실리면 안 된다.
    assert "예산 10%(0.00%" not in md
    # if/else는 배타적이어야 한다 — 구분되는 예산이 있으면 "전 구간 미만" 문구는 없다.
    assert "무작위 기준선 미만으로 유지된다" not in md


def test_negation_baseline_states_stronger_result_when_no_budget_converges():
    """리뷰어 Important — `indistinguishable`가 비면(ja-ko처럼 전 구간에서 무작위

    미만 유지) 문단을 통째로 생략하던 것을 고쳤다. 생략하면 독자가 "구분 불가
    절이 없네, 뭔가 덜 확인됐나"로 정반대로 읽는다 — 실제로는 **더 강한
    결과**인데도. `else` 분기가 그 사실을 명시적으로 말해야 한다.
    """
    by_kind_baseline = {"negation": {0.05: (0.05, 0.005), 0.10: (0.20, 0.01)}}
    md = render_markdown(META, RESULTS, DROPS, BASELINE, by_kind_baseline=by_kind_baseline)

    assert "측정한 모든 예산에서 negation Recall이 무작위 기준선 미만으로 유지된다" in md
    # if/else는 배타적이어야 한다 — "구분되지 않는다" 문단이 함께 나오면 안 된다.
    assert "통계적으로 구분되지 않는다" not in md


def test_write_report_round_trips_corpus_stats(tmp_path):
    """JSON 산출물에 `corpus_stats`가 그대로 실려야 §4.4 숫자가 재현된다."""
    corpus_stats = {
        "total_pairs": 100,
        "filtered_out": {"empty": 1},
        "kept_after_filter": 99,
        "unfittable": 49,
        "feasible": 50,
        "track_size": 10,
    }
    meta = dataclasses.replace(META, corpus_stats=corpus_stats)
    _, json_path = write_report(meta, RESULTS, DROPS, BASELINE, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["meta"]["corpus_stats"] == corpus_stats


# --- I-3(최종 리뷰): ablation 음수값 설명 -----------------------------------


def test_ablation_explains_negative_drops_as_harmful_not_just_unhelpful():
    """음수 하락폭은 "그 신호를 끄면 Recall이 올라간다"는 뜻이다.

    표가 내림차순이라 음수 항목이 맨 아래에 몰려 "가장 덜 유용함"으로 읽히지만
    실제로는 "가장 해로움"이다. `+0.0%`(`spec.overlap`)에는 이미 오독 방지 절이
    있는데 음수에는 짝이 없었다(최종 리뷰 I-3) — 이 절이 그 짝이다. 신호 이름은
    `drops`에서 뽑아야 한다(하드코딩 금지).

    **실재하는 신호 이름을 쓰면 하드코딩 회귀를 잡지 못한다.** 옛 버전은
    `length.ratio`로 검증했는데 그것은 실측에서 실제로 음수가 나오는 신호라
    `report.py`가 그대로 박아 두어도 통과했다(뮤테이션으로 실증). 제품 코드가
    **알 리 없는 이름**(`zz.synthetic.probe`)이어야 "`drops`에서 뽑았다"가
    증명된다.

    **잘라내는 지점도 같이 고쳐야 한다.** `## 신호별 기여도` 이후를 보면
    ablation **표**가 포함되는데, 표는 `drops`를 순회해 렌더하므로 어떤 이름을
    넣어도 거기 나타난다 — 검증 대상은 표가 아니라 그 아래 문단의 신호 이름
    목록이다. 이름만 바꾸고 분할 지점을 그대로 두면 뮤테이션이 여전히 통과한다.
    """
    drops = {"struct.untranslated": 0.21, "spec.overlap": 0.0, "zz.synthetic.probe": -0.06}
    md = render_markdown(META, RESULTS, drops, BASELINE)
    assert "가장 해로움" in md
    assert "`zz.synthetic.probe`" in md.split("**음수는", 1)[1]


def test_ablation_omits_harmful_paragraph_when_no_negative_drops():
    """음수 하락폭이 없으면(모든 신호가 도움이 됨) 문단을 생략한다."""
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "가장 해로움" not in md
