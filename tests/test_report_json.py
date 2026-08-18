"""`review.json` 스키마 직렬화 (FR-7.2 · 설계 §6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuesift.report import TriageOutcome, build_review, write_review
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
    source_lang: str = "ko",
    target_lang: str = "en",
    profile_name: str = "netflix-en",
    policy_label: str = "예산 10%",
    policy_kind: str = "budget",
    policy_value: float = 0.1,
) -> TriageOutcome:
    """**요약 필드를 전부 인자로 받는다. 기본값도 서로 구별되게 둔다.**

    단일값 픽스처는 필드를 **바꿔치기하는** 변이를 통과시킨다 - 예전 기본값은
    `profile_name`과 `target_lang`이 둘 다 `"en"`이라
    `"profile": outcome.target_lang`으로 바꿔도 전 스위트가 통과했다.
    `test_summary가_재현성_필드를_전부_낸다`가 막겠다고 **독스트링에 선언한
    바로 그 변이**다. 그래서 `profile_name`을 언어 코드와 겹치지 않는 값으로
    둔다 - 실재하는 프로파일 이름일 필요는 없고 구별만 되면 된다.

    `segments`를 따로 받는 이유는 **순서를 `risks`와 다르게** 둘 수 있어야
    하기 때문이다. 기본값(`risks` 순서 그대로)은 실제 파이프라인과 달라
    조인 회귀를 재지 못한다.
    """
    return TriageOutcome(
        source_lang=source_lang,
        target_lang=target_lang,
        profile_name=profile_name,
        policy_label=policy_label,
        policy_kind=policy_kind,
        policy_value=policy_value,
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
    # **`profile`은 `target_lang`과 다른 값이어야 한다.** 둘이 같은 픽스처에서는
    # 이 단언이 두 출처를 구별하지 못해 `"profile": outcome.target_lang` 변이가
    # 살아남는다 - 위 독스트링이 막겠다고 선언한 바로 그 변이다.
    assert summary["profile"] == "netflix-en"
    assert summary["profile"] != summary["target_lang"]
    assert summary["policy"] == {"kind": "budget", "value": 0.1}


def test_다른_언어쌍과_정책도_그대로_실린다() -> None:
    """**두 번째 케이스가 없으면 상수 고정 변이가 살아남는다.**

    값을 흩는 것만으로는 `"kind": "budget"` 같은 하드코딩을 못 잡는다 - 픽스처가
    한 벌뿐이면 그 상수가 언제나 맞기 때문이다. 실제로 `policy_kind`는
    `"threshold"`가 될 수 있고(FR-6.3 ②), `source_lang`·`target_lang`도
    ko→en 하나가 아니다.
    """
    doc = build_review(
        _outcome(
            risks=(_risk("00000", selected=True),),
            source_lang="en",
            target_lang="ja",
            profile_name="ted-ja",
            policy_label="임계값 0.7",
            policy_kind="threshold",
            policy_value=0.7,
        )
    )

    summary = doc["summary"]
    assert summary["source_lang"] == "en"
    assert summary["target_lang"] == "ja"
    assert summary["profile"] == "ted-ja"
    assert summary["policy"] == {"kind": "threshold", "value": 0.7}


def test_세그먼트_수가_셋으로_나뉘고_검산된다() -> None:
    doc = build_review(
        _outcome(risks=(_risk("00000", selected=True), _risk("00001")), excluded_failures=3)
    )

    summary = doc["summary"]
    assert summary["triaged_segments"] == 2
    assert summary["excluded_failures"] == 3
    assert summary["total_segments"] == 5
    assert summary["total_segments"] == summary["triaged_segments"] + summary["excluded_failures"]


def test_review_ratio는_트리아지_대상을_분모로_쓴다() -> None:
    """**0.0도 1.0도 아닌 비율이 필요하다.** CLAUDE.md가 1급으로 못 박은 값이다.

    "배수는 요청 예산이 아니라 실제 검수 비율로 나눈다 - 여기서 부풀리면
    프로젝트의 핵심 주장이 무너진다." 분모는 `triaged_segments`(실패분을 뺀
    수)이지 `total_segments`가 아니다(설계 §6.2 · D12).

    빈 입력의 `0.0`만으로는 이 계약을 재지 못한다 - **상수 `0.0` 고정과
    구별되지 않기 때문이다.** 실패분을 분모에 넣으면 비율이 낮아지고 README
    배수가 그만큼 **부풀려진다**, 그런데 프로그램은 정상 종료한다.
    """
    doc = build_review(
        _outcome(
            risks=(
                _risk("00000", selected=True),
                _risk("00001"),
                _risk("00002"),
                _risk("00003"),
            ),
            excluded_failures=3,
        )
    )

    summary = doc["summary"]
    assert summary["triaged_segments"] == 4
    assert summary["total_segments"] == 7
    # **`selected_for_review`가 `triaged_segments`와 다르다.** 같은 픽스처에서는
    # `selected_for_review ← triaged_segments` 바꿔치기가 살아남는다.
    assert summary["selected_for_review"] == 1
    assert summary["review_ratio"] == pytest.approx(0.25)
    # 분모가 total(7)이면 0.142...가 된다. 그 갈림을 명시적으로 못 박는다.
    assert summary["review_ratio"] != pytest.approx(1 / 7)


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


def test_hard_fail이_집계되고_세그먼트마다_실린다() -> None:
    """**hard_fail은 예산을 우회하므로 파일이 그 수를 말해야 한다** (FR-6.2 · D8).

    참·거짓을 **둘 다** 담는다. 전부 False인 픽스처에서는 상수 `False` 고정도
    부정(`not risk.hard_fail`)도 살아남고, 집계 쪽은 여집합으로 바꿔도 통과한다.

    **네 수가 전부 서로 달라야 한다** - 트리아지 5 · hard_fail 2 · 선별 4 ·
    여집합 3. 둘이라도 같으면 그 둘을 맞바꾸는 변이가 살아남는다. 실제로
    이 픽스처는 hard_fail과 선별이 둘 다 2였던 판에서 `hard_fail_count ←
    selected_for_review`를 통과시켰다. 수를 조금만 옮겨도 다른 등치가 생기니
    (5·2·3·2처럼) **넷을 한꺼번에 놓고 골라야 한다.**

    hard_fail은 **선별된 것과 안 된 것을 하나씩** 둔다 - 전부 선별분이면
    집계를 `outcome.selected`로 좁히는 변이가 살아남는다(FR-6.2에서 hard fail은
    예산을 우회하므로 미선별 hard fail이 실재한다).
    """
    doc = build_review(
        _outcome(
            risks=(
                _risk("00000", selected=True, hard_fail=True, reasons=["struct.empty"]),
                _risk("00001", selected=True, hard_fail=False, reasons=["length.ratio"]),
                _risk("00002", selected=False, hard_fail=True, reasons=["struct.empty"]),
                _risk("00003", selected=True, hard_fail=False, reasons=["glossary.miss"]),
                _risk("00004", selected=True, hard_fail=False, reasons=["spec.violation"]),
            )
        )
    )

    summary = doc["summary"]
    # 트리아지 5 · hard_fail 2 · 선별 4 · 여집합 3 - 넷이 전부 다르다.
    assert summary["hard_fail_count"] == 2
    assert summary["selected_for_review"] == 4
    assert summary["triaged_segments"] == 5

    by_id = {s["id"]: s for s in doc["segments"]}
    assert by_id["00000"]["hard_fail"] is True
    assert by_id["00001"]["hard_fail"] is False
    # `reasons`가 비어 있지 않아야 FR-6.4의 "왜 선별되었는지"가 파일에 남는다.
    assert by_id["00000"]["reasons"] == ["struct.empty"]
    assert by_id["00001"]["reasons"] == ["length.ratio"]


def test_신호의_이름과_계층과_점수가_실린다() -> None:
    """**Tier를 섞는다.** `tier`는 가상이 아니다 - `signals/llm.py`가 실제로 1을 낸다.

    전부 `tier=0`인 픽스처에서는 상수 `0` 고정도, `score ← signal.tier`
    바꿔치기도 살아남는다. 신호 이름·점수도 서로 달라야 상수 고정이 갈린다.
    계층을 키가 아니라 **값**으로 두는 것이 설계 §1.3이므로, Tier 1이 붙어도
    스키마가 그대로라는 것을 여기서 고정한다.
    """
    signals = [
        Signal(name="length.ratio", tier=0, score=0.6, detail={"ratio": 2.1}),
        Signal(name="self_consistency", tier=1, score=0.83, detail={"samples": ["a", "b"]}),
    ]
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, signals=signals),)))

    docs = doc["segments"][0]["signals"]
    assert [s["name"] for s in docs] == ["length.ratio", "self_consistency"]
    assert [s["tier"] for s in docs] == [0, 1]
    assert [s["score"] for s in docs] == pytest.approx([0.6, 0.83])


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
    """`side`가 없으면 FR-7.3 리포트가 원문과 번역문 중 어느 쪽을 칠할지 모른다.

    **양쪽을 담는다.** `side`가 전부 `"target"`인 픽스처에서는 상수 `"target"`
    고정이 살아남아, 그 유일한 판별자가 죽어도 게이트가 울리지 않는다.
    """
    signal = Signal(
        name="spec.violation",
        tier=0,
        score=1.0,
        spans=(Span(start=0, end=12, side="target"), Span(start=3, end=7, side="source")),
        detail={"kinds": ["cps"], "count": 1},
    )
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, signals=[signal]),)))

    assert doc["segments"][0]["signals"][0]["spans"] == [
        {"start": 0, "end": 12, "side": "target"},
        {"start": 3, "end": 7, "side": "source"},
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

    **세 필드를 전부 단언한다.** `calls` 하나만 보면 나머지 둘의 `usage is None`
    분기를 아무도 보지 않는다 - `test_cost가_범위를_명시하고`는 usage가 **있는**
    경우만 보기 때문이다. 세 필드가 같은 3항 연산이라 하나만 망가지는 회귀는
    드물지만, 드문 것과 게이트가 있는 것은 다르다.

    usage가 있는 경로는 `1234 · 567 · 8`을 내므로 여기의 `0 · 0 · 0`과 갈린다 -
    그래서 `usage is None` 분기를 통째로 지우는 회귀도(항상 `usage`를 읽으면
    `None`에서 죽는다) 이 테스트가 잡는다.
    """
    doc = build_review(_outcome(risks=(_risk("00000", selected=True),), usage=None))

    cost = doc["summary"]["cost"]
    assert cost["prompt_tokens"] == 0
    assert cost["completion_tokens"] == 0
    assert cost["calls"] == 0
    assert cost["includes"] == ["translation"]


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
    """설계 §3.2 — 문서 전체가 `json.dumps`를 통과해야 한다.

    **`detail`의 중첩 컨테이너는 원본 `Signal`과 공유된다.** `_signal_doc`의
    `dict(signal.detail)`이 얕은 복사라 최상위 dict만 새것이고 안의 리스트·dict는
    같은 객체다 - 중첩은 가상이 아니라 `signals/derived.py`·`llm.py`가 실제로
    리스트를 담는다(여기 `terms`가 그 형태다).

    따라서 **이 dict는 읽고 직렬화하는 용도다.** 반환된 문서의
    `detail["terms"].append(...)`처럼 제자리 수정을 하면 원본 신호가 오염된다.
    현재 호출 경로는 반환 직후 직렬화하므로 실피해가 없고, 그래서 `deepcopy`로
    바꾸지 않았다 - 계약을 여기 적어 두는 쪽이 값싸다.
    """
    signal = Signal(name="glossary.miss", tier=0, score=0.5, detail={"terms": ["용어"]})
    doc = build_review(_outcome(risks=(_risk("00000", selected=True, signals=[signal]),)))

    text = json.dumps(doc, ensure_ascii=False)
    assert json.loads(text) == doc


def test_파일을_utf8로_쓴다(tmp_path: Path) -> None:
    """한국어 원문이 `\\uXXXX`로 이스케이프되면 사람이 못 읽는다.

    **부모를 두 단계 깊게 둔다.** 한 단계(`tmp_path/nested/f.json`)로는
    `parents=True`를 `False`로 바꿔도 통과한다 - `tmp_path`가 이미 있어 한
    단계는 `parents`와 무관하게 만들어지기 때문이다(실측: 변이 생존). CLI가
    받는 `--out`은 `out/ep01/review.json`처럼 여러 단계다.
    """
    out = tmp_path / "out" / "nested" / "ep01.en.review.json"

    write_review(_outcome(risks=(_risk("00000", selected=True),)), out)

    assert out.exists(), "부모 디렉터리를 만들지 않았다"
    text = out.read_text(encoding="utf-8")
    assert "원문" in text, "ensure_ascii를 끄지 않았다"
    assert json.loads(text)["summary"]["target_lang"] == "en"
    # `indent=2`와 끝 개행이 사라지면 산출물이 한 줄이 되어 diff가 줄 단위로
    # 나지 않는다 - FR-7.2의 수혜자가 사람이라는 사실이 여기 걸린다. 왕복
    # 단언(`json.loads`)은 둘 다 통과시키므로 본문 형태를 직접 본다.
    assert text.startswith('{\n  "summary"'), "indent=2가 사라졌다"
    assert text.endswith("\n"), "끝 개행이 없다"


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
    out = tmp_path / "x.json"

    with pytest.raises(TypeError):
        write_review(_outcome(risks=(_risk("00000", selected=True, signals=[signal]),)), out)

    # **예외만 보면 순서를 재지 못한다.** `json.dump(fp)`로 스트리밍해도 TypeError는
    # 똑같이 나므로 위 `raises`는 두 구현을 구별하지 못한다(실측: 변이 생존).
    # 스트리밍은 예외 시점에 파일이 이미 열려 절반이 쓰여 있고, 소비자는 그것을
    # "파일이 있으니 성공"으로 읽는다. 파일이 **없어야** 한다는 것이 계약이다.
    assert not out.exists(), "직렬화 실패인데 반쯤 쓰인 파일이 남았다"
