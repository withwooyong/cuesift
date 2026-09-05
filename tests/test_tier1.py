"""2라운드 트리아지 (설계 §7)."""

from __future__ import annotations

import inspect
import json

import pytest
from tests.fakes.provider import EchoProvider

from cuesift.progress import ProgressUpdate
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals.base import SignalContext
from cuesift.spec import load_builtin
from cuesift.tier1 import (
    _ZERO_BY_SWITCH,
    _diagnose_empty_candidates,
    explain_zero_bound,
    triage_with_tier1,
)
from cuesift.triage import review_ratio


@pytest.fixture
def signal_ctx() -> SignalContext:
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def _ignore(_message: str) -> None:
    """`warn`이 필수 키워드가 된 뒤(2라운드 리뷰 Ruling P12), 사유에 관심
    없는 테스트가 매번 람다를 새로 쓰지 않도록 하는 공용 자리표시자."""


class _VaryingProvider(EchoProvider):
    """호출마다 다른 번역을 내 자가일관성 점수가 0이 아니게 한다.

    `EchoProvider`(결정론적 - 매번 같은 `EN:` 접두만 붙인다)로는 재번역
    N개가 항상 동일해 `llm.self_consistency`가 늘 0.0을 내므로, ⑥ 재융합이
    Tier 1 신호를 실제로 반영하는지 구분할 수 없다(2라운드 리뷰 A2 - M3·
    M4·M5가 이 이유로 생존했다). 호출마다 다른 접미사를 붙여 진짜 분산을
    만든다.
    """

    def __init__(self) -> None:
        self.n = 0
        super().__init__(transform=self._t)

    def _t(self, s: str) -> str:
        self.n += 1
        return f"EN{self.n}:{s}" + ("x" * (self.n % 7))


def _plain_segments(n: int) -> list[Segment]:
    """Tier 0 신호가 하나도 붙지 않는 세그먼트 n개 (id는 "0".."n-1")."""
    return [
        Segment(
            id=str(i),
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text=f"원문{i}",
            target_text=f"Target {i}",
        )
        for i in range(n)
    ]


def _겹치는_두_세그먼트() -> list[Segment]:
    """시간이 겹쳐 `spec.overlap`(배치 신호)이 발화하는 두 개.

    `check_overlaps`는 겹침을 **뒤에 오는 세그먼트**에 기록하므로
    (`spec/check.py`의 "겹침은 뒤에 오는 세그먼트에 기록한다"), id="1"에만
    신호가 붙는다. 앞의 id="0"을 수집 입력에서 빼면 id="1"의 겹침 신호가
    같이 사라진다 - `excluded_ids`가 수집까지 좁히면 안 되는 이유다(D5).

    `end_ms`가 `start_ms`보다 커야 하고 겹침 구간이 0보다 커야 한다.
    1000ms 겹침이 아니라 경계가 맞닿기만 하면(`end == start`)
    `check_overlaps`가 "겹침이 아니다"로 넘겨 이 픽스처가 죽는다.
    """
    return [
        Segment(
            id="0", index=0, start_ms=0, end_ms=2000, source_text="원문0", target_text="Target 0"
        ),
        Segment(
            id="1", index=1, start_ms=1000, end_ms=3000, source_text="원문1", target_text="Target 1"
        ),
    ]


def test_tier1은_후보에만_불린다(signal_ctx):
    """**비용 통제의 핵심 게이트다** (FR-4.3).

    전량에 불리면 요구사항정의서 §4가 '감당 불가'라고 적은 비용이 난다.
    """
    segments = _plain_segments(10)
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=_ignore,
    )

    # 후보 2건(10 × 0.2) × 3회 재번역 = 6회. 전량이면 30회다.
    assert len(provider.calls) == 6


def test_번역_실패분은_후보에서_빠진다(signal_ctx):
    """target_text가 None이면 재번역할 대상이 없다 (설계 §5).

    **실측(2026-08-17): 호출 2회다.** id=1은 `struct.empty`가 빈
    target_text를 hard_fail로 잡아 `select_tier1_candidates`의 hard_fail
    제외에서 애초에 빠진다. `budget_ratio=0.5`에서 quota=ceil(2×0.5)=1을
    id=1의 hard_fail이 전부 소진해(remaining=max(0, 1-1)=0) id=2는 예산에
    들지 못하지만, **선별되지 않은 것과 회색지대 후보 자격은 별개다** -
    id=2는 hard_fail도 아니고 selected도 아니므로 여전히 회색지대다.
    남는 후보는 id=2 하나 = `samples=2`회 호출. 이 시나리오에서
    `triage_with_tier1`의 target_text 필터(설계 §5)는 아무것도 더 거르지
    않는다 - id=1이 hard_fail 제외에서 이미 빠졌기 때문이다.

    **그 필터가 실제로 무언가를 거르는 경로는 따로 있다** -
    `test_공백_원문은_회색지대를_거쳐_target_text_필터에_걸린다`가
    재현한다(2라운드 리뷰 A1 - 이 테스트의 이전 버전은 "현재 신호로는
    도달 불가"라고 잘못 적었다).
    """
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="원문", target_text=None),
        Segment(
            id="2", index=1, start_ms=1000, end_ms=2000, source_text="원문", target_text="Target"
        ),
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.5,
        provider=provider,
        max_ratio=1.0,
        samples=2,
        warn=_ignore,
    )

    assert len(provider.calls) == 2


def test_공백_원문은_회색지대를_거쳐_target_text_필터에_걸린다(signal_ctx):
    """target_text 필터가 실제로 걸러내는 경로 (2라운드 리뷰 A1).

    `struct.empty`는 `source_text`가 있는데 `target_text`가 비면
    hard_fail을 낸다 - 그러나 **`source_text` 자체가 공백뿐이면 `None`을
    낸다**(`structural.py`: "원문이 비었으면 번역문이 빈 것은 오류가
    아니다"). id=2는 그래서 hard_fail이 아니라 회색지대로 들어오고,
    `select_tier1_candidates`가 후보로 뽑는다 - 그런데 `target_text`가
    없어 재번역할 원문 대응이 없으므로 `triage_with_tier1`의 필터에서
    걸린다. `loader.py`가 "텍스트가 빈 큐는 남긴다"고 명시하므로 실제
    자막 파일에서 발생 가능하다.
    """
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="원문", target_text=None),
        Segment(id="2", index=1, start_ms=1000, end_ms=2000, source_text="  ", target_text=None),
    ]
    provider = EchoProvider()
    messages: list[str] = []

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=1.0,
        samples=2,
        warn=messages.append,
    )

    assert provider.calls == []
    assert len(messages) == 1
    assert "번역 실패분" in messages[0]
    # id=1은 hard_fail(예산 우회로 선별), id=2는 회색지대에 남아 있었을
    # 뿐 선별되지 않는다 - 필터가 "선별에서 뺀다"가 아니라 "LLM을 안
    # 부른다"만 한다는 것을 같이 확인한다.
    by_id = {r.segment_id: r for r in risks}
    assert by_id["1"].hard_fail is True
    assert by_id["1"].selected is True
    assert by_id["2"].selected is False


def test_Tier1_신호가_최종_점수와_신호목록에_반영된다(signal_ctx):
    """⑤⑥⑦의 **효과**를 직접 단언한다 (2라운드 리뷰 A2).

    브리프 원안의 4개 테스트는 `EchoProvider`(결정론적)와 Tier 0 신호가
    하나도 없는 세그먼트만 썼다 - 그러면 `llm.self_consistency`가 항상
    0.0이라 `rescored`가 `risks`와 수치적으로 동일해지고, **Tier 1이
    돈만 쓰는 순수 부작용이어도 전 테스트가 통과했다**(변이 실측: ⑥⑦을
    건너뛰고 `scored`를 그대로 반환하는 M3, ⑥에서 Tier 0 신호를 버리는
    M4, Tier 1 신호를 버리는 M5가 전부 생존).

    id="0"은 `struct.number_missing`(Tier 0, score=0.5, hard_fail=False -
    누락이 한 자리 수라 hard_fail이 해제된다)이 붙어 예산(quota=1)을
    독점하도록 설계했다 - 회색지대 후보는 id="1"·"2"로 고정된다(실측
    확인, 컨트롤러). `_VaryingProvider`로 재번역을 흩어 두 후보의
    `llm.self_consistency`가 확실히 0보다 크게 만든다.
    """
    segments = [
        Segment(
            id="0",
            index=0,
            start_ms=0,
            end_ms=1000,
            source_text="3개 있다",
            target_text="There are some",
        ),
        *(
            Segment(
                id=str(i),
                index=i,
                start_ms=i * 1000,
                end_ms=(i + 1) * 1000,
                source_text=f"원문{i}",
                target_text=f"Target {i}",
            )
            for i in range(1, 10)
        ),
    ]
    provider = _VaryingProvider()
    messages: list[str] = []

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=messages.append,
    )

    assert len(provider.calls) == 6  # 후보 2건(id=1,2) × samples=3
    assert messages == []  # 후보가 있었으니 진단 메시지는 안 나간다
    by_id = {r.segment_id: r for r in risks}

    # id="0"은 Tier 1 후보가 아니었다 - ⑥이 Tier 0 신호를 버리면(M4) 이
    # 세그먼트의 struct.number_missing이 조용히 사라진다.
    assert [s.name for s in by_id["0"].signals] == ["struct.number_missing"]
    assert by_id["0"].risk_score == pytest.approx(0.5)

    # id="1"·"2"는 Tier 1 후보였다 - llm.self_consistency가 최종 신호
    # 목록에 있고 점수가 0보다 커야 한다. ⑤⑥⑦을 건너뛰면(M3) 또는 ⑥이
    # Tier 1 신호를 버리면(M5) 둘 다 사라진다.
    for seg_id in ("1", "2"):
        names = [s.name for s in by_id[seg_id].signals]
        assert "llm.self_consistency" in names
        assert by_id[seg_id].risk_score > 0.0


def test_embedder가_없으면_backtranslation은_기본_집합에서_빠진다(signal_ctx):
    """FR-4.2가 FR-4.1의 기존 사용자를 깨면 안 된다 (컨트롤러 판정).

    `llm.backtranslation`이 `signals/__init__.py`에 등록되자 `collect_tier1`의
    기본 집합(`enabled=None` -> tier==1 전부)에 들어갔고, 이 파일의 유일한
    호출자인 `triage_with_tier1`은 `Tier1Context.embedder`를 배선할 방법이
    아직 없다(Task 4 전). **이 필터가 없으면** 임베딩 백엔드가 없는 호출자의
    `llm.self_consistency`(FR-4.1, 임베딩과 무관)까지 `ValueError`로 함께
    죽는다 - 그래서 여기서는 예외 없이 끝나야 하고, 신호 목록에
    `llm.self_consistency`는 있어야 하며 `llm.backtranslation`은 없어야 한다.
    """
    segments = [
        Segment(
            id="0",
            index=0,
            start_ms=0,
            end_ms=1000,
            source_text="3개 있다",
            target_text="There are some",
        ),
        *(
            Segment(
                id=str(i),
                index=i,
                start_ms=i * 1000,
                end_ms=(i + 1) * 1000,
                source_text=f"원문{i}",
                target_text=f"Target {i}",
            )
            for i in range(1, 10)
        ),
    ]
    provider = _VaryingProvider()
    messages: list[str] = []

    # embedder를 넘길 방법이 아직 없다 - Task 4 전이므로 항상 None이다.
    # 이 호출이 예외 없이 끝나는 것 자체가 이 회귀의 본체다.
    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=messages.append,
    )

    by_id = {r.segment_id: r for r in risks}
    for seg_id in ("1", "2"):
        names = [s.name for s in by_id[seg_id].signals]
        assert "llm.self_consistency" in names
        assert "llm.backtranslation" not in names


def test_max_ratio가_0이면_LLM을_안_부른다(signal_ctx):
    """비용을 완전히 끄는 경로가 있어야 한다."""
    segments = _plain_segments(10)
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.0,
        samples=3,
        warn=_ignore,
    )

    assert provider.calls == []
    assert len(risks) == 10


def test_전체_목록을_반환한다(signal_ctx):
    """select_by_budget과 같은 계약이다 - 선별된 것만 반환하면
    review_ratio가 언제나 1.0이 되어 §9.1 배수의 분모가 무너진다."""
    segments = _plain_segments(10)
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=_ignore,
    )

    assert len(risks) == 10
    assert any(r.selected for r in risks)


def test_세그먼트_id가_중복되면_거부한다(signal_ctx):
    """중복 id는 ④의 `set()` 집합화에서 조용히 뭉개져 FR-4.3 상한을
    초과시킨다(2라운드 리뷰 C6). `triage_with_tier1`은 임의
    `Sequence[Segment]`를 받는 공개 함수라 로더의 유일성 보장에 기댈 수
    없다 - 여기서 직접 막는다."""
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="a", target_text="A"),
        Segment(id="1", index=1, start_ms=1000, end_ms=2000, source_text="b", target_text="B"),
    ]
    with pytest.raises(ValueError, match="중복"):
        triage_with_tier1(
            segments,
            signal_ctx,
            budget_ratio=0.5,
            provider=EchoProvider(),
            max_ratio=1.0,
            warn=_ignore,
        )


def test_attempt별로_캐시가_갈린다(signal_ctx, tmp_path):
    """설계 §8 - `attempt`로 캐시 키가 갈리지 않으면 2회차부터 캐시 히트가
    나서 재번역 N개가 전부 동일해지고 자가일관성 분산이 항상 0이 된다
    (2라운드 리뷰 A3 - `attempt=attempt` -> `attempt=0` 변이가 이 게이트
    없이는 70개 테스트를 통과해 생존했다).

    캐시 파일이 요청 재료를 그대로 담으므로(`cache.py`의 `store()`)
    `attempt` 필드를 직접 읽어 분포를 확인한다.
    """
    segments = _plain_segments(10)
    provider = EchoProvider()

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        cache_dir=tmp_path,
        identity="echo|fake|v1",
        warn=_ignore,
    )

    files = list(tmp_path.glob("*.json"))
    attempts = sorted(json.loads(f.read_text(encoding="utf-8"))["attempt"] for f in files)
    # 후보 2건 × samples=3 - 각 후보가 attempt=0,1,2를 한 번씩 쓴다.
    assert attempts == [0, 0, 1, 1, 2, 2]

    # 재개(NFR-3): 캐시가 있으므로 2회차는 LLM을 다시 부르지 않는다.
    provider2 = EchoProvider()
    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider2,
        max_ratio=0.2,
        samples=3,
        cache_dir=tmp_path,
        identity="echo|fake|v1",
        warn=_ignore,
    )
    assert provider2.calls == []


# --- 후보 0건의 네 가지 원인 관측 가능성 (컨트롤러 요구 ①) ---
#
# Task 4 리뷰어가 "select_tier1_candidates의 내림(floor) 상한이 조용히
# 0건을 낼 수 있다"를 지적하며 "오케스트레이션이 0건을 관측 가능하게
# 내는 것"을 조건으로 달았다. 아래 통합 테스트가 그 조건이 요구하는
# 원인들을 재현하고, `test_diagnose_empty_candidates가_다섯_사유를_구분한다`가
# `_diagnose_empty_candidates`를 직접 단위 테스트해 나머지(빈 입력)를 덮는다.


def test_max_ratio가_0이면_사유를_warn하고_예산이_적용된_scored를_반환한다(signal_ctx):
    """원인 중 하나 - 사용자가 껐다 (정상).

    **반환값도 함께 확인한다** (2라운드 리뷰 A2, M7) - 조기 반환이
    예산 적용 전 `risks`를 내면(`selected`가 전부 False) `review_ratio`가
    0.0이 되는데, `max_ratio=0`은 "Tier 1을 끈 기본 실행"이라 가장 흔한
    경로다. 여기서 무단언이면 그 경로의 예산 계약이 안 지켜져도 아무
    테스트도 죽지 않는다. 10건·budget_ratio=0.1 -> quota=ceil(1.0)=1
    -> review_ratio는 정확히 0.1이어야 한다(실측 확인).
    """
    segments = _plain_segments(10)
    provider = EchoProvider()
    messages: list[str] = []

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.0,
        samples=3,
        warn=messages.append,
    )

    assert len(messages) == 1
    assert "껐다" in messages[0]
    assert review_ratio(risks) == pytest.approx(0.1)


def test_세그먼트_수가_적어_상한이_0이면_사유를_warn한다(signal_ctx):
    """원인 중 하나 - `select_tier1_candidates`의 내림 상한이 0이 됐다.

    n=3, max_ratio=0.2 -> cap=floor(0.6)=0. 회색지대 자체는 비지 않는다
    (budget_ratio=0.1이 1건만 선별하므로 나머지 2건이 회색지대에 남는다) -
    "회색지대가 빔"과 구분되는 것을 확인하는 것이 이 테스트의 요점이다.
    """
    segments = _plain_segments(3)
    provider = EchoProvider()
    messages: list[str] = []

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=messages.append,
    )

    assert provider.calls == []
    assert len(messages) == 1
    assert "상한" in messages[0]


def test_회색지대가_비면_사유를_warn한다(signal_ctx):
    """원인 중 하나 - 전부 hard_fail이거나 이미 선별돼 회색지대가 빈다.

    id=1은 target_text가 없어 hard_fail이고, budget_ratio=1.0(전량 예산)이
    id=2까지 선별한다 - 남는 회색지대가 없다.
    """
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="원문", target_text=None),
        Segment(
            id="2", index=1, start_ms=1000, end_ms=2000, source_text="원문", target_text="Target"
        ),
    ]
    provider = EchoProvider()
    messages: list[str] = []

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=1.0,
        provider=provider,
        max_ratio=1.0,
        samples=2,
        warn=messages.append,
    )

    assert provider.calls == []
    assert len(messages) == 1
    assert "회색지대" in messages[0]


def test_diagnose_empty_candidates가_여섯_사유를_구분한다():
    """`_diagnose_empty_candidates`를 직접 단위 테스트한다.

    「후보로 뽑혔지만 전부 번역 실패분」은
    `test_공백_원문은_회색지대를_거쳐_target_text_필터에_걸린다`가 통합
    시나리오로 이미 재현했다 - 여기서는 순수 함수의 각 분기를 직접
    겨냥한다(빈 입력은 통합 테스트로 재현할 이유가 없는 사소한 경계라
    단위 테스트로만 덮는다).
    """
    hard = SegmentRisk(segment_id="h", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    picked = SegmentRisk(segment_id="p", signals=[], risk_score=0.9, hard_fail=False, selected=True)
    gray = SegmentRisk(segment_id="g", signals=[], risk_score=0.1, hard_fail=False, selected=False)

    # ① 빈 입력(total=0 - 파서가 하나도 못 읽었다) - "전부 hard_fail이거나
    # 선별됨"으로 오진하면(2라운드 리뷰 C2) 그 사고가 "전량 hard_fail"로 보인다.
    assert "0건" in _diagnose_empty_candidates([], set(), 0.5, total=0, excluded_count=0)

    # ② max_ratio=0.0 - candidate_ids·scored 내용과 무관하게 최우선이다.
    assert "껐다" in _diagnose_empty_candidates([gray], set(), 0.0, total=1, excluded_count=0)

    # ③ candidate_ids가 비지 않았는데 후보가 0건 -> target_text 필터가
    # 전부 걸렀다. **이 인자 조합 자체는 파이프라인에서 안 나온다** -
    # select_tier1_candidates([gray], 0.2)는 cap=floor(0.2)=0이라 []를 낸다
    # (재리뷰 축2 실측). 순수 함수의 분기를 직접 겨냥한 것이고, 분기 ③의
    # 실제 도달성은 test_공백_원문은_회색지대를_거쳐...가 따로 지킨다.
    assert "번역 실패분" in _diagnose_empty_candidates(
        [gray], {"g"}, 0.2, total=1, excluded_count=0
    )

    # ④ candidate_ids가 비었고 회색지대(비-hard_fail·비-selected)가 남아
    # 있다 -> 상한이 내림으로 0이 됐다.
    #
    # **명사가 "번역 성공 세그먼트 수"인 것까지 본다.** 이 경로가 넘기는
    # `len(scored)`는 `excluded_ids`(번역 실패분)가 빠진 수라, dry-run과 같은
    # "세그먼트 수"로 부르면 20컷 중 16건이 실패한 실행에서 화면이 "세그먼트
    # 수(4)"라고 말한다 - 사용자는 파일이 4컷이라 읽는다. 두 호출자의 명사를
    # 같은 값으로 되돌리는 변이를 이 단언과 `test_cli_tier1.py`의 dry-run
    # 단언이 **양쪽에서** 잡는다.
    assert "번역 성공 세그먼트 수(2)에 비해" in _diagnose_empty_candidates(
        [hard, gray], set(), 0.01, total=2, excluded_count=0
    )

    # ⑤ candidate_ids가 비었고 회색지대도 비었다(전부 hard_fail 또는 selected).
    assert "회색지대" in _diagnose_empty_candidates(
        [hard, picked], set(), 0.5, total=2, excluded_count=0
    )

    # ⑥ 입력은 있는데 전량이 excluded_ids로 빠졌다 - ①과 **원인이 정반대**다.
    # ①은 파서가 자막을 못 읽은 것이고 ⑥은 번역이 전량 실패한 것이라,
    # 뭉쳐서 "입력 자체를 봐라"로 보고하면 사람을 반대쪽으로 보낸다.
    전량제외 = _diagnose_empty_candidates([], set(), 0.5, total=5, excluded_count=5)
    assert "번역이 전량 실패" in 전량제외
    assert "입력 자체" not in 전량제외


# --- 게이트 3개 (3라운드 재리뷰 A1·A2·A3) ---
#
# 재리뷰가 P12(warn 필수화)·C5(Tier1Context 조기 생성)·C1(strip 기반 필터)를
# 스크래치에서 각각 되돌려 봤는데 113 passed로 셋 다 생존했다 - 수정은
# 옳았지만 그것을 지키는 게이트가 없었다는 뜻이다. "게이트를 만들면 반드시
# 실패시켜 봐야 한다"는 이 저장소의 규율에 따라 아래 세 테스트를 추가한다.


def test_warn은_기본값이_없다():
    """P12 게이트 - `warn`에 기본값을 다시 붙이면(리뷰 재현: 조용히
    `_ignore`류 기본값을 되살리는 변이) 이 테스트가 죽는다. 침묵 기본값이
    돌아오면 유료 계층이 안 돌아도 아무도 모른다는 관측 가능성 판정
    전체가 증발하므로, "기본값이 없다"는 계약 자체를 직접 검사한다.

    `tests/fakes/provider.py`의 독스트링이 "`inspect.signature` 단언이 이
    저장소에서 그 이탈을 잡는 유일한 수단"이라고 적은 것과 같은 장치다
    (`test_cli.py::test_...`가 `fail_on` 기본값을 같은 방식으로 고정한다).
    """
    default = inspect.signature(triage_with_tier1).parameters["warn"].default
    assert default is inspect.Parameter.empty


def test_samples가_1이면_max_ratio와_무관하게_즉시_거부된다(signal_ctx):
    """C5 게이트 - `Tier1Context` 생성이 조기 반환(`if not candidates`)
    아래로 되돌아가면 `samples=1`처럼 잘못된 설정이 `max_ratio=0.0`(조기
    반환 경로)에서는 조용히 통과한다(리뷰 재현: 되돌린 코드에서 실제로
    통과 확인됨). **`max_ratio=0.0`을 써야 한다** - 그것이 조기 반환이
    발동하는 조건이라, `Tier1Context`가 그 반환보다 위에 있어야만 여기서
    검증이 돈다는 것을 정확히 겨냥한다.
    """
    segments = _plain_segments(3)
    with pytest.raises(ValueError, match="samples"):
        triage_with_tier1(
            segments,
            signal_ctx,
            budget_ratio=0.1,
            provider=EchoProvider(),
            max_ratio=0.0,
            samples=1,
            warn=_ignore,
        )


def test_공백뿐인_번역은_후보에서_빠져_호출을_아낀다(signal_ctx):
    """C1 게이트 - `.strip()` 없이 진리값만 보면(리뷰 재현: 되돌린 코드에서
    24 -> 27회로 실측) 원문·번역이 둘 다 공백인 세그먼트가 "번역 있음"으로
    통과해 낭비 호출이 샌다. id="5" 하나만 공백(source="   ",
    target="   ")으로 두고 나머지 9건은 평범하게 둔다.

    실측(컨트롤러, 현재 코드): budget_ratio=0.1 -> quota=1 -> id="0" 선별.
    나머지 9건이 회색지대이고 max_ratio=1.0(cap=10, 회색지대 크기로 제한돼
    9)이라 전부 후보가 된다 - 그중 id="5"만 `target_text.strip()`이 비어
    필터에서 빠지므로 **8건 × samples=3 = 24회**가 정확한 값이다.
    """
    segments = []
    for i in range(10):
        if i == 5:
            segments.append(
                Segment(
                    id=str(i),
                    index=i,
                    start_ms=i * 1000,
                    end_ms=(i + 1) * 1000,
                    source_text="   ",
                    target_text="   ",
                )
            )
        else:
            segments.append(
                Segment(
                    id=str(i),
                    index=i,
                    start_ms=i * 1000,
                    end_ms=(i + 1) * 1000,
                    source_text=f"원문{i}",
                    target_text=f"Target {i}",
                )
            )
    provider = EchoProvider()

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=1.0,
        samples=3,
        warn=_ignore,
    )

    assert len(provider.calls) == 24


def test_excluded_ids는_융합에서_빠진다(signal_ctx):
    """번역 실패분이 hard fail로 예산 quota를 먹으면 진짜 오류가 큐에서 밀린다.

    실측(트리아지 CLI 설계 D12): 200큐·진짜 오류 20건·예산 10%에서
    실패 20건이면 **Recall@10%가 0%** 가 된다.

    `max_ratio=0.0`인 것은 이 테스트가 융합·선별 입력만 본다는 뜻이다 -
    Tier 1을 실제로 태우면 LLM 호출이 섞여 무엇이 결과를 바꿨는지 흐려진다.
    """
    segments = _plain_segments(10)
    빠질_id = segments[0].id

    전체 = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.5,
        provider=EchoProvider(),
        max_ratio=0.0,
        warn=_ignore,
    )
    일부 = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.5,
        provider=EchoProvider(),
        max_ratio=0.0,
        warn=_ignore,
        excluded_ids={빠질_id},
    )

    assert 빠질_id in {r.segment_id for r in 전체}
    assert 빠질_id not in {r.segment_id for r in 일부}
    assert len(일부) == len(전체) - 1


def test_excluded_ids여도_수집은_전량을_본다(signal_ctx):
    """**이것이 반대 방향의 게이트다.**

    수집에서 실패분을 빼면 그와 겹치는 **성공한** 큐의 겹침까지 사라진다
    (실측: 같은 2큐 파일에서 실패 1건이면 `spec.overlap` 미출력).
    요약도 종료 코드도 침묵하는 조용한 실패다.
    """
    # 이웃을 봐야 판정되는 배치 신호(`spec.overlap`)가 잡히도록 시간이 겹치는
    # 두 세그먼트를 만든다. 앞의 것을 excluded_ids로 빼도, 뒤의 것에서
    # 겹침 신호가 **여전히** 나와야 한다.
    a, b = _겹치는_두_세그먼트()

    결과 = triage_with_tier1(
        [a, b],
        signal_ctx,
        budget_ratio=1.0,
        provider=EchoProvider(),
        max_ratio=0.0,
        warn=_ignore,
        excluded_ids={a.id},
    )

    (남은,) = 결과
    assert 남은.segment_id == b.id
    assert any("overlap" in name for name in 남은.reasons)


def test_이_트랙에_없는_id는_조용히_무시된다(signal_ctx):
    """미지의 id는 **거부하지 않는다**는 판정을 테스트로 고정한다 (리뷰 C2).

    집합 여집합 의미론에서 없는 원소는 무해하다. 중복 id를 `ValueError`로
    거절하는 이유는 그것이 **결과를 조용히 틀리게** 만들기 때문인데(cap
    초과 실측 12회 호출), 미지의 id는 결과를 틀리게 하지 않는다. 거부하면
    "이 트랙에 없을 수도 있는 id 목록"이라는 합당한 사용이 막힌다.

    **이전 버전은 동어반복이었다** - `excluded_ids=()`가 시그니처 기본값이라
    "안 주기"와 "빈 값 주기"는 구성상 같은 호출이고, 실측으로 ①②⑥ 세 변이
    전부에서 생존했다(함수의 결정성만 쟀다). 미지의 id와 비교하면 **제외
    로직을 실제로 통과**하고, `max_ratio=0.2`라 ⑥까지 간다.
    """
    segments = _plain_segments(10)
    공통 = {"budget_ratio": 0.1, "max_ratio": 0.2, "samples": 3, "warn": _ignore}

    빈값 = triage_with_tier1(
        segments, signal_ctx, provider=_VaryingProvider(), excluded_ids=(), **공통
    )
    미지 = triage_with_tier1(
        segments, signal_ctx, provider=_VaryingProvider(), excluded_ids={"이_트랙에_없음"}, **공통
    )

    # 아무것도 안 빠졌으므로 10건이 그대로다 - 미지의 id를 "빼야 할 것"으로
    # 잘못 세면 여기서 9건이 된다.
    assert len(빈값) == 10
    assert [(r.segment_id, r.selected) for r in 빈값] == [(r.segment_id, r.selected) for r in 미지]
    # 점수까지 같아야 ⑥ 재융합 경로가 동일했다는 뜻이다.
    assert [r.risk_score for r in 빈값] == [r.risk_score for r in 미지]


@pytest.mark.parametrize("나쁜_값", ["10", b"10"])
def test_excluded_ids에_str이나_bytes를_그대로_주면_거부한다(signal_ctx, 나쁜_값):
    """둘 다 타입상 유효한 `Collection[str]`이라 조용히 원소 단위로 쪼개진다.

    실측 - 12큐에 `excluded_ids="10"`을 주면 `set("10") == {"1", "0"}`이라
    **"10"은 남고 "0"·"1"이 사라진다.** 이 게이트에는 mypy가 없어 타입으로는
    안 걸리고, 이 저장소에는 정수 id 계약 사고(커밋 817ed64)가 이미 있다.

    `bytes`는 더 나쁘다 - `set(b"10") == {49, 48}`으로 **정수**를 내므로
    어떤 id와도 안 맞아 제외가 통째로 무음 실패한다(재리뷰 축2). str은
    일부라도 맞아 티가 나는데 bytes는 전혀 안 난다.

    `frozenset`·`dict.keys()`·제너레이터가 막히면 안 되므로 이 둘만 지목한다 -
    `test_excluded_ids는_집합연산이면_충분하다` 계열이 그 통과를 지킨다.
    """
    segments = _plain_segments(12)

    with pytest.raises(TypeError, match="원소 단위"):
        triage_with_tier1(
            segments,
            signal_ctx,
            budget_ratio=0.1,
            provider=EchoProvider(),
            max_ratio=0.0,
            warn=_ignore,
            excluded_ids=나쁜_값,
        )


def test_전량이_excluded_ids로_빠지면_번역_실패를_가리킨다(signal_ctx):
    """진단이 **정반대 원인**을 말하면 안 된다 (리뷰 Important 1).

    실측(리뷰어) - 5큐를 전부 제외하면 `scored`가 비어 "세그먼트가 0건이다 -
    입력 자체를 봐야 한다"가 나갔다. 그런데 이 경로의 진짜 원인은 파서 사고가
    아니라 **번역 전량 실패**다. 호출자가 실패분 id를 넘기면 프로바이더가
    죽었을 때 정확히 여기로 오고, 그때 "입력을 봐라"는 사람을 반대쪽으로
    보낸다(그 배선은 WP8b Task 6이 한다 - 아직 리포에 없다).

    `_diagnose_empty_candidates`의 존재 이유가 원인 구분이고, 그 함수의 주석은
    바로 이 실수의 **반대 방향**(빈 입력을 전량 hard_fail로 오진)을 막으려고
    쓰여 있다 - 지금 그 방향이 뒤집힌 것이다.

    진단 문구는 `warn`으로만 나가고 반환값·종료 코드에는 흔적이 없으므로
    **이 테스트가 없으면 다시 썩는다.**
    """
    segments = _plain_segments(5)
    provider = EchoProvider()
    messages: list[str] = []

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=messages.append,
        excluded_ids={s.id for s in segments},
    )

    assert risks == []
    assert provider.calls == []
    assert len(messages) == 1
    # 방향이 맞아야 한다 - "번역"을 가리키고 "입력 자체"를 가리키면 안 된다.
    assert "번역이 전량 실패" in messages[0]
    assert "입력 자체" not in messages[0]


def test_Tier1이_실제로_도는_경로에서도_excluded_ids가_유지된다(signal_ctx):
    """⑥ 재융합의 게이트다 (G12 - 변이로 확인함).

    **위의 세 테스트는 전부 `max_ratio=0.0`이라 후보 0건 조기 반환을 타서
    ⑥에 도달하지 않는다.** 실측(변이): ⑥의 `kept`를 `segments`로 되돌려도
    `tests/test_tier1.py` 18건이 전부 통과했다 - 게이트가 없었다는 뜻이다.
    ②만 고치고 ⑥을 두면 Tier 1을 **켰을 때만** 실패분이 되살아나 켰을
    때와 안 켰을 때의 분모(`review_ratio`)가 갈라진다.

    `max_ratio=0.2`가 필수다. 0.0이면 조기 반환이라 ⑥을 지나지 않아
    이 테스트가 무엇도 잡지 못한다 - `provider.calls`를 단언하는 것은
    "정말 ⑥까지 갔는가"를 확인하기 위해서다.
    """
    segments = _plain_segments(10)
    provider = _VaryingProvider()
    messages: list[str] = []

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=messages.append,
        excluded_ids={"9"},
    )

    # kept 9건 -> cap=floor(9×0.2)=1 -> 후보 1건 × samples=3 = 3회.
    # 0회면 조기 반환을 탄 것이라 ⑥을 검증하지 못한다.
    assert len(provider.calls) == 3
    assert messages == []
    assert any("llm.self_consistency" in [s.name for s in r.signals] for r in risks)

    # ⑥이 `segments`를 쓰면 여기서 "9"가 되살아나 10건이 된다.
    # **순서로 단언하지 않는다** - `select_by_budget`이 위험도순으로 재정렬해
    # 입력 순서가 보존되지 않는다(실측: id="1"이 맨 앞).
    ids = [r.segment_id for r in risks]
    assert len(ids) == 9
    assert "9" not in ids


def test_진행_콜백이_Tier1_수집까지_흐른다(signal_ctx):
    """`triage_with_tier1`은 진행을 **만들지 않고 넘기기만** 한다 (설계 D1).

    만들면 ①~④(수집·융합·후보 선정)까지 진행에 섞여 분모가 두 겹이 되고,
    사용자는 "무엇의 진행인지"를 잃는다.
    """
    provider = _VaryingProvider()
    events: list[ProgressUpdate] = []

    triage_with_tier1(
        _plain_segments(10),
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=_ignore,
        on_progress=events.append,
    )

    # 후보 2건(10 × 0.2) × tier 1 수집기 1종 = 2. 같은 후보 수를
    # `test_tier1은_후보에만_불린다`가 호출 6회(2 × samples 3)로 이미 고정한다.
    assert events[-1] == ProgressUpdate(2, 2)


def test_상한이_0인_두_원인을_구분한다() -> None:
    """dry-run이 계산할 수 있는 것은 이 둘뿐이다 (파킹 #3)."""
    assert explain_zero_bound(10, 0.0) == "max_ratio=0.0 - Tier 1을 껐다 (정상)"
    msg = explain_zero_bound(10, 0.05)
    assert msg is not None
    assert "내림(floor)으로 0이 됐다" in msg
    assert "10" in msg and "0.05" in msg


def test_상한이_0이_아니면_None이다() -> None:
    # 설명할 것이 없는데 문장을 내면 화면이 늘 시끄럽다.
    assert explain_zero_bound(100, 0.05) is None


def test_실행_경로와_같은_문자열을_쓴다() -> None:
    """**복제 금지의 게이트다.** 한쪽만 고치면 여기가 죽는다."""
    assert explain_zero_bound(10, 0.0) == _ZERO_BY_SWITCH
