"""`bench/run.py`의 파서 테스트 (컨트롤러 판정 A · 태스크7 브리프 Step 1).

**`test_bench_report.py`가 아니라 이 파일에 둔다.** `build_arg_parser`는
`bench/run.py`의 심볼이고 `test_bench_report.py`는 `render_markdown`·
`write_report`(둘 다 `bench/report.py`)를 검사하는 파일이다 — 브리프의
"Test: tests/test_bench_report.py"는 Task 7 착수 전에 잘린 것이라 이
모듈 경계를 몰랐다. 컨트롤러 노트가 "브리프의 코드 블록이 그대로 동작하지
않는다"고 이미 밝혔으므로, 테스트 배치도 실제 모듈 경계를 따른다.
"""

from __future__ import annotations

import io
import json
import sys

import pytest
from bench.classify_negation import CLEAN
from bench.inject import Label
from bench.report import render_tier1_comparison
from bench.run import (
    _candidate_counts,
    _collect_raw,
    _dump_raw,
    _make_stdout_lossy,
    _negation_in_gray_zone,
    _recall_scores,
    _resolve_embed_key,
    build_arg_parser,
)

from cuesift.segment import Segment, SegmentRisk, Signal
from cuesift.signals.backtranslation import BackTranslation
from cuesift.tier1 import CandidateReport


def test_tier1_없이는_흐름이_같다():
    """`--tier1`·`--embed-model`의 기본값이 꺼짐이다 (설계 2026-09-05 D9).

    **이 테스트가 검사하는 것은 파서 기본값 두 개뿐이다** (이월 22번 M-4).
    이름과 옛 독스트링은 "흐름이 한 줄도 다르지 않다"를 주장했지만 그것을
    직접 재지는 않는다 - 그 계약을 실제로 지키는 것은 `main()`에서 Tier 1
    코드가 전부 `if args.tier1:` 아래에 있다는 **구조**이고, 여기서 고정하는
    것은 그 구조의 입구다. 구조 자체를 재려면 `--tier1` 없는 실행과 있는
    실행의 Tier 0 산출물을 대조해야 하는데, 그것은 `data/`를 요구해 CI에서
    돌지 않는다.

    입구를 고정하는 것만으로도 값이 있다 - 기본값이 켜짐으로 바뀌면 CI가
    LLM 백엔드를 요구하게 된다. 벤치 테스트는 data/가 .gitignore라 CI에서
    이미 skip되므로, 그 변화는 로컬에서만 조용히 다른 것을 재는 상태를 만든다.
    """
    parser_defaults = build_arg_parser().parse_args(["--pair", "en-ko"])
    assert parser_defaults.tier1 is False
    assert parser_defaults.embed_model is None


def test_tier1_인자_기본값이_전부_꺼짐이다():
    """새로 더한 인자 다섯 개(브리프 Step 4)가 전부 `None`·꺼짐이어야
    `--tier1` 없는 기존 호출이 이 인자들의 영향을 받지 않는다."""
    args = build_arg_parser().parse_args(["--pair", "ja-ko"])
    assert args.base_url is None
    assert args.model is None
    assert args.embed_base_url is None
    assert args.cache_dir is None


def test_파서는_pair_없이_거부한다():
    """`--pair`는 필수다 — 회귀 시 기존 계약(en-ko/ja-ko 둘만 허용)이 깨진다."""
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([])


# --- 리뷰 지적 2: 원자료 형식 게이트 ----------------------------------------
#
# 리뷰어가 `cosine` 삭제·`negation_class` 삭제·메타 `commit` 삭제 세 변이를
# 넣었더니 전체 스위트(1877건)가 전부 생존했다 — 원자료 형식을 검사하는
# 테스트가 하나도 없었기 때문이다. 아래 두 테스트가 그 게이트다.


def test_collect_raw는_아홉_필드와_selected를_전부_담는다():
    """`_collect_raw`가 브리프 Step 5의 필드 아홉 개 + `selected`(리뷰 지적 6 —
    원자료만으로 Recall@Budget을 되계산하려면 필요)를 정확히 담는지 고정한다.

    **`dict` 전체를 비교한다.** 개별 필드만 골라 `assert record["cosine"]
    == 0.58`처럼 쓰면 그 필드가 통째로 빠져도(`KeyError`가 아니라) 다른
    필드 검사만으로 통과할 여지가 남는다 — 딕셔너리 동등 비교는 필드
    삭제·추가 어느 쪽도 놓치지 않는다.
    """
    seg = Segment(
        id="en-00001",
        index=0,
        start_ms=0,
        end_ms=1000,
        source_text="원문",
        target_text="번역문",
    )
    risk = SegmentRisk(
        segment_id="en-00001",
        signals=[
            Signal(
                name=BackTranslation.name,
                tier=1,
                score=0.42,
                detail={"back_translation": "역번역문", "cosine": 0.58},
            )
        ],
        risk_score=0.3,
        hard_fail=False,
        selected=True,
    )
    labels = [Label(segment_id="en-00001", kind="negation", detail={})]
    negation_classes = {"en-00001": CLEAN}

    records = _collect_raw([risk], [seg], labels, negation_classes, budget=0.10)

    assert records == [
        {
            "segment_id": "en-00001",
            "source_text": "원문",
            "target_text": "번역문",
            "back_translation": "역번역문",
            "cosine": 0.58,
            "score": 0.42,
            "label_kind": "negation",
            "negation_class": CLEAN,
            "budget_ratio": 0.10,
            "selected": True,
        }
    ]


def test_collect_raw는_backtranslation_신호가_없으면_건너뛴다():
    """회색지대 밖이라 Tier 1 후보가 아니었던 세그먼트는 레코드를 남기지 않는다."""
    seg = Segment(
        id="en-00002", index=0, start_ms=0, end_ms=1000, source_text="원문", target_text="번역문"
    )
    risk = SegmentRisk(segment_id="en-00002", signals=[], risk_score=0.1, hard_fail=False)

    records = _collect_raw([risk], [seg], [], {}, budget=0.10)
    assert records == []


def test_dump_raw는_메타_네_종을_담는다(tmp_path):
    """브리프 Step 5 — 메타에 역번역 모델·임베딩 모델·커밋·실행 시각을 담는다."""
    path = _dump_raw(
        [{"segment_id": "en-00001"}],
        tmp_path,
        "en-ko",
        model="qwen2.5:3b",
        embed_model="bge-m3",
        commit="deadbeef",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["translate_model"] == "qwen2.5:3b"
    assert payload["embed_model"] == "bge-m3"
    assert payload["commit"] == "deadbeef"
    assert "generated_at" in payload
    assert payload["record_count"] == 1
    assert payload["records"] == [{"segment_id": "en-00001"}]


def test_dump_raw는_빈_목록도_쓴다(tmp_path):
    """예산 루프가 첫 예산에서 죽어도(리뷰 지적 3) 빈 목록으로라도 파일을
    남긴다 — "시도했으나 0건"과 "아예 안 돌았다"를 파일 존재로 구분한다."""
    path = _dump_raw([], tmp_path, "en-ko", model=None, embed_model=None, commit="deadbeef")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["record_count"] == 0
    assert payload["records"] == []


# --- 리뷰 지적 4: API 키 폴백 ------------------------------------------------


def test_임베딩_키가_빈_문자열이면_번역_키로_폴백하지_않는다(monkeypatch):
    """`CUESIFT_EMBED_API_KEY=""`는 "명시적으로 비웠다"는 뜻이지 "설정
    안 함"이 아니다 — `or`로 폴백하면 번역용 키가 다른 호스트일 수 있는
    임베딩 엔드포인트의 `Authorization` 헤더에 실린다(리뷰어 실측)."""
    monkeypatch.setenv("CUESIFT_API_KEY", "TRANSLATE-SECRET")
    monkeypatch.setenv("CUESIFT_EMBED_API_KEY", "")
    assert _resolve_embed_key() is None


def test_임베딩_키가_없으면_번역_키로_폴백한다(monkeypatch):
    """설정 자체를 안 했을 때만 폴백한다 — 이 경로는 `cli._resolve_embed_key`와 같다."""
    monkeypatch.setenv("CUESIFT_API_KEY", "TRANSLATE-SECRET")
    monkeypatch.delenv("CUESIFT_EMBED_API_KEY", raising=False)
    assert _resolve_embed_key() == "TRANSLATE-SECRET"


def test_좁은_인코딩_콘솔에서도_비교표를_찍을_수_있다():
    """cp949 stdout이 리포트 문자열을 만나도 `print`가 죽으면 안 된다.

    **2026-09-05 실행에서 실제로 죽었다.** 벤치가 한 시간 48분 동안 LLM을
    부른 뒤, 비교표의 엠대시(U+2014) 하나가 Windows 기본 콘솔 인코딩인
    cp949를 넘지 못해 `UnicodeEncodeError`로 exit 1이 됐다. 그 지점이
    `tier1_comparisons.append`보다 앞이라 리포트 재작성까지 함께 날아갔다.

    아래 첫 단언이 파괴 실험을 겸한다 - 비교표에서 좁은 인코딩이 못 내는
    문자가 사라지면 이 테스트가 먼저 실패해 알려 준다. 문자를 없애는 것이
    해법이 아니라는 뜻은 아니지만, **없앴다는 사실이 조용히 묻히면 안 된다.**
    """
    rendered = render_tier1_comparison(
        tier0={
            "negation_recall": 0.1972,
            "clean_recall": 0.20,
            "clean_total": 35,
            "overall_recall": 0.880,
            "overall_hits": 440,
            "error_total": 500,
            "review_ratio": 0.3042,
        },
        tier1={
            "negation_recall": 0.4507,
            "clean_recall": 0.60,
            "clean_total": 35,
            "overall_recall": 0.912,
            "overall_hits": 456,
            "error_total": 500,
            "review_ratio": 0.3042,
        },
        budget=0.30,
    )

    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp949", newline="")
    with pytest.raises(UnicodeEncodeError):
        print(rendered, file=narrow)

    # `_make_stdout_lossy`가 하는 일이 이것이다 - 인코딩은 그대로 두고
    # 오류 처리만 바꾼다.
    narrow.reconfigure(errors="replace")
    print(rendered, file=narrow)


def test_stdout을_손실_허용으로_바꾼다(capsys):
    """`_make_stdout_lossy`가 실제로 `errors`를 바꾸는지 본다.

    **이름만 있고 아무것도 안 하는 함수가 되지 않게 한다.** 위 테스트는
    `reconfigure`를 직접 부르므로 이 함수가 비어 있어도 통과한다.
    """
    with capsys.disabled():
        before = sys.stdout.errors
        _make_stdout_lossy()
        assert sys.stdout.errors == "replace"
        # 다른 테스트에 영향을 주지 않도록 되돌린다. pytest가 stdout을
        # 가로채므로 원래 값이 "replace"였을 수도 있다 - 그때도 무해하다.
        sys.stdout.reconfigure(errors=before)


# --- 수정 라운드 1 I-1: 후보 구성 배선 게이트 --------------------------------
#
# 렌더러(`render_tier1_candidates`)는 `test_bench_report.py`가 검사하지만,
# `main()` 안에서 그 렌더러에 넘기는 인자를 계산하는 코드는 어떤 테스트도
# 보지 않았다 - 리뷰어가 `_negation_in_gray_zone`을 `return 0`으로, `negation_hits`
# 계산을 `0`으로 바꿔도 전체 스위트(1934건)가 그대로 통과했다. 아래 두 테스트가
# 그 배선(글루 코드)의 게이트다. `_collect_raw`가 같은 파일에서 이미 단위
# 테스트되는 선례(위 68·120행)를 따른다.


def test_negation_in_gray_zone은_hard_fail과_selected를_제외한다():
    """`_negation_in_gray_zone`은 회색지대(hard_fail도 아니고 이미 선별되지도
    않은 것) 안의 negation만 센다. hard_fail·selected 제외가 실제로 결과에
    영향을 주는 데이터로 확인한다 - 그래야 `return 0`류 변이와 "제외 없이
    전부 센" 변이를 둘 다 잡는다."""
    risks = [
        SegmentRisk(segment_id=f"s{i}", signals=[], risk_score=(10 - i) / 10, hard_fail=(i < 2))
        for i in range(10)
    ]
    # budget=0.3 -> quota=floor(10*0.3)=3. hard_fail 2건(s0,s1)이 quota를
    # 우회해 무조건 선별되고, 남은 1자리는 risk_score 순으로 s2가 채운다.
    # 즉 selected={s0,s1,s2}, 회색지대(hard_fail도 selected도 아님)={s3..s9}.
    negation_ids = {"s1", "s2", "s5", "s9"}  # s1은 hard_fail, s2는 selected라 회색지대 밖
    assert _negation_in_gray_zone(risks, 0.3, negation_ids) == 2  # s5, s9만 회색지대 안


def test_candidate_counts는_우선_집합과_negation_교집합을_각각_센다():
    """`main()` 인라인 계산을 뽑아낸 함수 - 이 테스트가 깨지면 리포트에
    실리는 두 수(`from_priority`·`negation_hits`)가 조용히 0이 되는 회귀를
    잡는다(M7: `negation_hits=0`으로 바꾸는 변이가 이 테스트로 죽어야 한다)."""
    report = CandidateReport(
        candidate_ids=("a", "b", "c", "d"),
        priority_ids=frozenset({"a", "c"}),
        gray_zone_size=10,
        cap=4,
    )
    negation_ids = {"b", "d", "z"}  # "z"는 후보 밖 - 세면 안 된다
    assert _candidate_counts(report, negation_ids) == (2, 2)


# --- 이월 22번: 밀어냄 산출물 --------------------------------------------
#
# **`import bench.run`만으로는 이 경로가 검사되지 않는다.** `date.today()`가
# 임포트되지 않은 채로도 모듈 임포트는 성공하고, NameError는 실제로 파일을
# 쓸 때에야 난다 — 실측으로 그 상태가 한 번 만들어졌다.


def _movements_for_dump():
    from bench.pushout import analyze_movement

    from cuesift.triage import select_by_budget

    def risks(scores):
        return [
            SegmentRisk(segment_id=k, signals=[], risk_score=v, hard_fail=False)
            for k, v in scores.items()
        ]

    tier0 = select_by_budget(risks({"A": 0.9, "B": 0.8, "C": 0.3}), 0.5)
    tier01 = select_by_budget(risks({"A": 0.9, "B": 0.8, "C": 0.85}), 0.5)
    kinds = {"B": "negation"}
    before = analyze_movement(
        tier0, tier01, candidate_ids={"B"}, priority_ids=set(), label_kinds=kinds
    )
    after = analyze_movement(
        tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds=kinds
    )
    return before, after


def test_밀어냄_리포트가_실제로_파일로_써진다(tmp_path):
    """`date.today()` 같은 누락 임포트는 **파일을 쓸 때에야** 드러난다."""
    from bench.pushout import render_pushout
    from bench.run import _write_pushout

    before, after = _movements_for_dump()
    block = render_pushout(budget=0.1, before=before, after=after)

    path = _write_pushout(
        [block], tmp_path, "en-ko", commit="abc1234", model="qwen2.5:3b", embed_model="bge-m3"
    )

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "abc1234" in text and "qwen2.5:3b" in text
    assert "밀어냄 분해" in text


def test_밀어냄_원자료에_자막_본문이_없다(tmp_path):
    """**자막 본문이 실리면 CC BY-NC-ND 4.0에 걸려 커밋할 수 없게 된다.**

    `_dump_raw`는 원문·번역문을 담아 audit-dir에만 두는데, 이쪽은 담지
    않는다는 것이 설계다 — 필드가 늘어나며 조용히 섞이면 그 구분이 무너진다.
    """
    from bench.run import _dump_pushout

    before, after = _movements_for_dump()
    path = _dump_pushout({0.1: (before, after)}, tmp_path, "en-ko", commit="abc1234")

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["budgets"]["0.10"]["before"]
    assert rows and set(rows[0]) == {
        "segment_id",
        "rank_tier0",
        "rank_tier01",
        "score_tier0",
        "score_tier01",
        "selected_tier0",
        "selected_tier01",
        "is_candidate",
        "is_priority",
        "hard_fail",
        "label_kind",
        "delta_rank",
    }


# --- 이월 23번: 비교표에 넘길 점수 한 벌 -------------------------------------


def _risk(sid: str, *, score: float, selected: bool) -> SegmentRisk:
    return SegmentRisk(
        segment_id=sid, signals=[], risk_score=score, hard_fail=False, selected=selected
    )


def test_recall_scores가_전체_recall을_함께_낸다():
    """**전체 Recall 을 여기서 내지 않으면 비교표가 그릴 자료가 없다** (이월 23번).

    2026-09-06 실측이 예산 10%의 전체 오류 하락(366 -> 364건)을 놓친 것은
    표의 문제이기 전에 이 함수가 negation 축만 냈기 때문이다.
    """
    labels = [
        Label(segment_id="s1", kind="negation"),
        Label(segment_id="s2", kind="glossary"),
        Label(segment_id="s3", kind="number"),
        Label(segment_id="s4", kind="spec"),
    ]
    # 정답 4건 중 s1·s2 만 큐에 담긴다. s5 는 오류가 아닌데 담겼다.
    scored = [
        _risk("s1", score=0.9, selected=True),
        _risk("s2", score=0.8, selected=True),
        _risk("s5", score=0.7, selected=True),
        _risk("s3", score=0.2, selected=False),
        _risk("s4", score=0.1, selected=False),
    ]
    scores = _recall_scores(scored, labels, {"s1": CLEAN})

    assert scores["overall_hits"] == 2
    assert scores["error_total"] == 4
    assert scores["overall_recall"] == 0.5
    # 검수 비율의 분모는 오류 건수가 아니라 **전체 세그먼트 수**다 - 3/5.
    assert scores["review_ratio"] == 0.6
    assert scores["negation_recall"] == 1.0
    assert scores["clean_total"] == 1


def test_recall_scores의_출력이_비교표의_필수_키를_모두_채운다():
    """**두 모듈을 각자 검사하면 그 사이가 빈다** (이월 23번).

    비교표는 필수 키가 없으면 `KeyError` 로 서는데, 그 키 목록을 테스트가
    자기 안에서 지어 넘기면 `_recall_scores` 가 키 하나를 빠뜨려도 아무
    게이트도 죽지 않는다 - 실제 산출물을 그대로 넘겨야 그 사이가 닫힌다.
    """
    labels = [Label(segment_id="s1", kind="negation")]
    scored = [_risk("s1", score=0.9, selected=True), _risk("s2", score=0.1, selected=False)]
    scores = _recall_scores(scored, labels, {"s1": CLEAN})

    rendered = render_tier1_comparison(tier0=scores, tier1=scores, budget=0.10)
    assert "전체 Recall" in rendered
    assert "순손실" not in rendered, "같은 값끼리 비교하면 순증이 0이라 순손실이 아니다"
