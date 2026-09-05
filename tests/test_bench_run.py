"""`bench/run.py`의 파서 테스트 (컨트롤러 판정 A · 태스크7 브리프 Step 1).

**`test_bench_report.py`가 아니라 이 파일에 둔다.** `build_arg_parser`는
`bench/run.py`의 심볼이고 `test_bench_report.py`는 `render_markdown`·
`write_report`(둘 다 `bench/report.py`)를 검사하는 파일이다 — 브리프의
"Test: tests/test_bench_report.py"는 Task 7 착수 전에 잘린 것이라 이
모듈 경계를 몰랐다. 컨트롤러 노트가 "브리프의 코드 블록이 그대로 동작하지
않는다"고 이미 밝혔으므로, 테스트 배치도 실제 모듈 경계를 따른다.
"""

from __future__ import annotations

import json

import pytest
from bench.classify_negation import CLEAN
from bench.inject import Label
from bench.run import _collect_raw, _dump_raw, _resolve_embed_key, build_arg_parser

from cuesift.segment import Segment, SegmentRisk, Signal
from cuesift.signals.backtranslation import BackTranslation


def test_tier1_없이는_흐름이_같다():
    """`--tier1`이 꺼져 있으면 지금과 한 줄도 다르지 않다 (설계 D9).

    **켜져 있으면 CI가 LLM 백엔드를 요구하게 된다.** 벤치 테스트는
    data/가 .gitignore라 CI에서 이미 skip되는데, 기본값이 바뀌면
    로컬에서만 조용히 다른 것을 재게 된다.
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
