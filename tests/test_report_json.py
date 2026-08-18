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
    """**본문과 타임코드를 id에서 파생시킨다.**

    전부 같은 값을 내면 조인이 어긋나도 실린 값이 같아서 순서 회귀를 재지
    못한다 - `test_순서가_어긋나도_id로_조인된다`가 정확히 그 차이에 기댄다.
    """
    n = int(seg_id)
    return Segment(
        id=seg_id,
        index=index,
        start_ms=12000 + n * 1000,
        end_ms=14500 + n * 1000,
        source_text=f"원문 {seg_id}",
        target_text=f"translated text {seg_id}",
    )


def _outcome(
    *,
    risks: tuple[SegmentRisk, ...],
    segments: tuple[Segment, ...] | None = None,
    excluded_failures: int = 0,
    usage: TokenUsage | None = None,
) -> TriageOutcome:
    # `segments`를 받는 이유는 **순서를 `risks`와 다르게 둘 수 있어야** 하기
    # 때문이다. 기본값(`risks` 순서 그대로)은 실제 파이프라인과 달라서
    # 조인 회귀를 재지 못한다.
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label="예산 10%",
        policy_kind="budget",
        policy_value=0.1,
        risks=risks,
        segments=(
            tuple(_segment(r.segment_id, index=i) for i, r in enumerate(risks))
            if segments is None
            else segments
        ),
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
    assert seg["source_text"] == "원문 00000"
    assert seg["target_text"] == "translated text 00000"
    assert seg["risk_score"] == pytest.approx(0.87)


def test_순서가_어긋나도_id로_조인된다() -> None:
    """**`risks`와 `segments`의 순서는 실제 파이프라인에서 다르다.**

    `risks`는 `select_by_*`가 낸 위험도 내림차순이고(`policy.py`의 `_sorted_desc`)
    `segments`는 트랙 원본 순서다 - Task 1 리뷰가 실측으로 확정했다
    (kept `['00000','00001','00002','00003']` vs
    scored `['00001','00003','00002','00000']`).

    `TriageOutcome.__post_init__`은 **집합과 길이만** 보고 순서는 일부러 보지
    않는다 - 순서까지 고정하면 위의 정상 입력이 거부돼 트리아지 경로가 통째로
    죽기 때문이다. 그래서 조인이 `by_id`에서 `zip`이나 인덱스 접근으로
    퇴화해도 **예외가 나지 않는다.** `review.json`에 다른 세그먼트의 원문·
    번역문이 실린 채 프로그램은 정상 종료하고, FR-7.2가 열거한 핵심 필드가
    통째로 틀리는데 어떤 게이트도 울리지 않는다.
    """
    # 위험도 내림차순 - 트랙 순서(00000..00003)와 **일부러** 어긋나게 둔다.
    risks = (
        _risk("00001", selected=True, score=0.91),
        _risk("00003", selected=True, score=0.72),
        _risk("00002", score=0.40),
        _risk("00000", selected=True, score=0.12),
    )
    track_order = ("00000", "00001", "00002", "00003")
    doc = build_review(
        _outcome(
            risks=risks,
            segments=tuple(_segment(sid, index=i) for i, sid in enumerate(track_order)),
        )
    )

    # 선별분만 담기므로 00002가 빠지고, 순서는 `risks`를 따른다(설계 D3).
    assert [s["id"] for s in doc["segments"]] == ["00001", "00003", "00000"]
    for seg in doc["segments"]:
        # **문서마다 자기 id의 본문을 들고 있어야 한다.** `_segment`가 id에서
        # 본문·타임코드를 파생시키므로 조인이 어긋나면 여기서 값이 갈린다.
        assert seg["source_text"] == f"원문 {seg['id']}"
        assert seg["target_text"] == f"translated text {seg['id']}"
        assert seg["start_ms"] == 12000 + int(seg["id"]) * 1000
    # 위험도도 id를 따라와야 한다 - 본문만 보면 `risk` 쪽이 어긋난 경우를 놓친다.
    assert {s["id"]: s["risk_score"] for s in doc["segments"]} == pytest.approx(
        {"00001": 0.91, "00003": 0.72, "00000": 0.12}
    )


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

    assert doc["segments"][0]["signals"][0]["spans"] == [{"start": 0, "end": 12, "side": "target"}]


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
