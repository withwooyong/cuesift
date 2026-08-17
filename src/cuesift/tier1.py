"""Tier 0 -> Tier 1 2라운드 트리아지 (설계 §7 · FR-4.1 · FR-4.3).

**noisy-or가 이 구조를 성립시킨다.** `1 - ∏(1 - sᵢ)^wᵢ`는 신호가 붙을수록
점수가 올라가기만 하므로, 회색지대에만 Tier 1을 적용해도 적용받은 쪽이
부당하게 낮아지지 않는다. 가중 평균이었다면 낮은 Tier 1 점수가 기존
위험도를 희석해 오히려 큐에서 밀어냈을 것이다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from cuesift.risk.fuse import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals.base import SignalContext, Tier1Context, collect_all, collect_tier1
from cuesift.store.provider import CachingProvider
from cuesift.translate.provider import Provider
from cuesift.triage.policy import select_by_budget, select_tier1_candidates


def _ignore(_message: str) -> None:
    """기본 경고 싱크. `store/provider.py`의 `CachingProvider`와 같은 패턴이다 -
    라이브러리 사용자가 stderr를 강요받지 않게 한다."""


def triage_with_tier1(
    segments: Sequence[Segment],
    ctx: SignalContext,
    *,
    budget_ratio: float,
    provider: Provider,
    max_ratio: float,
    samples: int = 3,
    temperature: float = 1.0,
    cache_dir: Path | None = None,
    identity: str | None = None,
    warn: Callable[[str], None] = _ignore,
) -> list[SegmentRisk]:
    """Tier 0로 좁히고 회색지대에만 Tier 1을 적용한 뒤 다시 선별한다.

    **전체 목록을 반환한다.** `select_by_budget`과 같은 계약이며, 선별된
    것만 반환하면 `review_ratio`가 언제나 1.0이 되어 README 배수의 분모가
    무너진다.

    `temperature`의 기본값이 1.0인 것은 OpenAI Chat Completions API 명세의
    기본값이라 **출처가 있기 때문이다**(§11 R8 - 출처 없는 수치를 기본값으로
    넣지 않는다). 0.0이면 재번역이 전부 같아 신호가 죽는데, 그 방어는
    `Tier1Context`가 한다.

    ## Tier 1 후보가 0건일 때 (`warn`)

    후보가 0건이 되는 원인은 셋이다 (Task 4 리뷰의 조건 - 구분하지 않으면
    무음 열화(Q3)다).

    | 원인 | 의미 |
    | --- | --- |
    | `max_ratio=0.0` | 사용자가 Tier 1을 껐다 - 정상 |
    | 상한이 0으로 내려감 | `select_tier1_candidates`의 내림(`floor`) 때문에 상한이 0이다 |
    | 회색지대가 빔 | 전부 hard_fail이거나 이미 선별됐다 |

    이 함수는 반환형(`list[SegmentRisk]`)을 그대로 두고 대신 `warn`으로
    사유를 알린다 - 기본값은 아무 것도 하지 않는 `_ignore`이므로 호출자가
    원치 않으면 표면적으로 아무 변화가 없다. `_diagnose_empty_candidates`가
    실제 구분을 한다.

    ## 비용

    총 LLM 호출 수는 `len(candidates) × (등록된 Tier 1 수집기 수) × samples`다
    (`collect_tier1`의 독스트링과 같은 공식). `Tier1Collector`가 세그먼트
    단위 프로토콜이라 배치가 없다 - 실측(세그먼트 10개 · samples=3 · 수집기
    1개, `signals/llm.py`의 `_retranslate` 독스트링): 30회 호출 · 10320자
    (배치로 묶었다면 3회 · 1599자, 호출 10배). 이 함수는 후보를 좁혀 총
    호출 수를 줄이지만 세그먼트 단위 프로토콜 자체는 바꾸지 않는다 - 그
    변경은 Task 2의 파일과 이 오케스트레이션을 함께 건드리는 설계 변경이라
    WP8b 비범위다.

    **토큰 사용량이 유실된다.** `collect_tier1`은 `Signal`만 반환하고
    `TranslationResult.usage`를 실어 나를 통로가 없다 - NFR-2·FR-7.4가
    요구하는 비용 숫자에서 가장 비싼 계층(Tier 1)만 리포트에서 빠진다는
    뜻이다. 여기서 고치지 않는 이유는 `collect_tier1`의 반환형을 바꾸는
    프로토콜 변경이 되기 때문이다 - 누적은 리포트 계층(WP8b)의 일이다.
    """
    # ① Tier 0 - 비용 0, 전량
    tier0 = collect_all(segments, ctx)

    # ② 1차 융합
    risks = [fuse(seg.id, tier0[seg.id]) for seg in segments]

    # ③ 예산 적용 - ④가 "이미 큐에 든 것"을 알아야 한다
    scored = select_by_budget(risks, budget_ratio)

    # ④ 후보 선별
    candidate_ids = set(select_tier1_candidates(scored, max_ratio))

    # 번역 실패분을 여기서 뺀다. SegmentRisk가 텍스트를 갖지 않아
    # select_tier1_candidates가 판정할 수 없다(설계 §5).
    candidates = [s for s in segments if s.id in candidate_ids and s.target_text]

    if not candidates:
        # LLM을 부르지 않는 경로. 원인이 셋(+1) 있으므로 조용히 넘어가지
        # 않고 사유를 구분해 알린다 - 위 "비용이 0건일 때" 절 참고.
        warn(_diagnose_empty_candidates(scored, candidate_ids, max_ratio))
        return scored

    # ⑤ Tier 1 - 후보에만
    tier1 = collect_tier1(
        candidates,
        Tier1Context(
            signal=ctx,
            provider_for=_provider_factory(provider, cache_dir=cache_dir, identity=identity),
            samples=samples,
            temperature=temperature,
        ),
    )

    # ⑥ 재융합 - Tier 0 신호에 Tier 1 신호를 더해 다시 계산한다.
    # 이름은 "re-scored"다 - 브리프 원안의 `refused`는 영어로 "거부됨"으로
    # 오독되는데 뜻은 정반대(재융합)라 이름을 바꿨다.
    rescored = [fuse(seg.id, tier0[seg.id] + tier1.get(seg.id, [])) for seg in segments]

    # ⑦ 예산 재적용
    return select_by_budget(rescored, budget_ratio)


def _diagnose_empty_candidates(
    scored: Sequence[SegmentRisk], candidate_ids: set[str], max_ratio: float
) -> str:
    """Tier 1 후보가 0건인 이유를 구분한다 (Task 4 리뷰 조건).

    구분하지 않으면 "사용자가 껐다"·"상한이 내림으로 0이 됐다"·"회색지대가
    비었다"가 전부 같은 무음 침묵으로 보여 무음 열화(Q3)가 된다. 넷째
    경우(후보는 뽑혔지만 전부 번역 실패분)는 현재 등록된 신호로는 도달하지
    않는다 - `struct.empty`가 빈 target_text를 항상 hard_fail로 잡아
    회색지대에 들어오기 전에 걸러지기 때문이다. 그래도 남겨 두는 이유는
    `select_tier1_candidates`의 독스트링이 이 필터를 "호출자의 일"로 명시해
    구조적으로 결합돼 있지 않고, 미래에 hard_fail이 아닌 방식으로 실패를
    표시하는 신호가 추가되면 실제로 도달하기 때문이다.
    """
    if max_ratio == 0.0:
        return "max_ratio=0.0 - Tier 1을 껐다 (정상)"
    if candidate_ids:
        return "후보로 뽑혔지만 전부 번역 실패분(target_text 없음)이라 제외됐다"
    if any(not r.hard_fail and not r.selected for r in scored):
        return (
            f"세그먼트 수({len(scored)})에 비해 max_ratio({max_ratio})가 작아 "
            "Tier 1 상한이 내림(floor)으로 0이 됐다 "
            "(select_tier1_candidates 독스트링 - n < 1/max_ratio)"
        )
    return "회색지대가 비었다 (전부 hard_fail이거나 이미 선별됨)"


def _provider_factory(
    inner: Provider,
    *,
    cache_dir: Path | None,
    identity: str | None,
):
    """시도 번호별 프로바이더를 만든다 (설계 §8).

    **캐시를 켤지 말지가 여기서 끝난다.** 신호 수집기는 `identity`도
    `cache_dir`도 모른 채 `provider_for(attempt)`만 부른다.
    """

    def provider_for(attempt: int) -> Provider:
        if cache_dir is None or identity is None:
            # 캐시 없이 그대로. 이 경로에서는 같은 입력에 매번 새 호출이
            # 나가므로 NFR-3(재현성)이 성립하지 않는다 - 호출자가 캐시를
            # 끈 것은 그 대가를 받아들인 것이다.
            return inner
        return CachingProvider(inner, identity=identity, cache_dir=cache_dir, attempt=attempt)

    return provider_for
