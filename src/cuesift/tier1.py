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
from cuesift.triage.policy import gray_zone, select_by_budget, select_tier1_candidates


def triage_with_tier1(
    segments: Sequence[Segment],
    ctx: SignalContext,
    *,
    budget_ratio: float,
    provider: Provider,
    max_ratio: float,
    warn: Callable[[str], None],
    samples: int = 3,
    temperature: float = 1.0,
    cache_dir: Path | None = None,
    identity: str | None = None,
) -> list[SegmentRisk]:
    """Tier 0로 좁히고 회색지대에만 Tier 1을 적용한 뒤 다시 선별한다.

    **전체 목록을 반환한다.** `select_by_budget`과 같은 계약이며, 선별된
    것만 반환하면 `review_ratio`가 언제나 1.0이 되어 README 배수의 분모가
    무너진다.

    `temperature`의 기본값이 1.0인 것은 OpenAI Chat Completions API 명세의
    기본값이라 **출처가 있기 때문이다**(§11 R8 - 출처 없는 수치를 기본값으로
    넣지 않는다). 0.0이면 재번역이 전부 같아 신호가 죽는데, 그 방어는
    `Tier1Context`가 한다.

    ## `warn`은 기본값이 없다 (Ruling P12)

    `CachingProvider._warn`은 침묵해도 "다음 실행이 조금 느리다"로 끝나고
    실행 시간이라는 다른 관측 수단이 남는다. 여기서 침묵하면 **유료 계층이
    통째로 안 돌아도 반환값의 형태(길이·`selected`)가 완전히 같다** - 알아챌
    다른 수단이 없다. Task 4 리뷰가 내림(`floor`) 상한을 승인한 조건도
    "오케스트레이션이 후보 0건을 관측 가능하게 **낼 것**"이었다 - 기본값이
    있으면 "낼 수 **있다**"에 그친다. 조용히 하고 싶으면 `warn=lambda _:
    None`을 **명시**하라 - 그 명시 자체가 "이 계층이 안 돌 수 있음을 안다"는
    기록이 된다.

    ## Tier 1 후보가 0건일 때

    원인은 넷이다 - `_diagnose_empty_candidates`가 구분한다.

    | 원인 | 의미 |
    | --- | --- |
    | 세그먼트 0건 | 입력 자체가 비었다 |
    | `max_ratio=0.0` | 사용자가 Tier 1을 껐다 - 정상 |
    | 상한이 0으로 내려감 | `select_tier1_candidates`의 내림(`floor`) 때문에 상한이 0이다 |
    | 회색지대가 빔 | 전부 hard_fail이거나 이미 선별됐다 |

    **넷째(후보로 뽑혔지만 전부 번역 실패분)는 도달한다.** `struct.empty`가
    `source_text`가 있는 세그먼트의 빈 `target_text`는 hard_fail로 잡지만,
    **`source_text` 자체가 공백뿐이면 그 신호는 `None`을 낸다**
    (`structural.py`의 "원문이 비었으면 번역문이 빈 것은 오류가 아니다") -
    그 세그먼트는 hard_fail이 아니므로 회색지대를 거쳐 후보로 뽑히고, 여기
    아래 `target_text` 필터에서 걸린다. 로더(`loader.py`)가 "텍스트가 빈
    큐는 남긴다"고 명시하므로 실제 자막 파일에서 발생한다.

    ## 비용

    총 LLM 호출 수는 `len(candidates) × (등록된 Tier 1 수집기 수) × samples`다.
    `Tier1Collector`가 세그먼트 단위 프로토콜이라 배치가 없다 - 호출 수는
    묶었을 때의 정확히 배수만큼 나온다(실측 배수와 구체적 문자 수는
    `signals/llm.py`의 `_retranslate` 독스트링이 단일 출처다 - 문자 수는
    입력 문장에 의존해 여기 별도로 적으면 그 문서와 갈라진다). 이 함수는
    후보를 좁혀 총 호출 수를 줄이지만 세그먼트 단위 프로토콜 자체는 바꾸지
    않는다 - 그 변경은 Task 2의 파일과 이 오케스트레이션을 함께 건드리는
    설계 변경이라 WP8b 비범위다.

    **토큰 사용량이 유실된다.** `collect_tier1`은 `Signal`만 반환하고
    `TranslationResult.usage`를 실어 나를 통로가 없다 - NFR-2·FR-7.4가
    요구하는 비용 숫자에서 가장 비싼 계층(Tier 1)만 리포트에서 빠진다는
    뜻이다. 여기서 고치지 않는 이유는 `collect_tier1`의 반환형을 바꾸는
    프로토콜 변경이 되기 때문이다 - 누적은 리포트 계층(WP8b)의 일이다.
    """
    # 중복 id는 ④의 candidate_ids 집합화에서 조용히 뭉개져 FR-4.3 상한을
    # 초과시킨다(2라운드 리뷰 C6 실측: 중복 4건 포함 10건·cap=3에서 실제
    # 12회 호출). `register()`가 중복 이름을 거절하는 것과 같은 취향이다 -
    # 호출자가 이 함수를 임의 Sequence[Segment]로 부르는 공개 API라서
    # 로더의 유일성 보장(`f"{index:05d}"`)에 기댈 수 없다.
    ids = [seg.id for seg in segments]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"세그먼트 id가 중복됐다: {', '.join(dupes)}")

    # `Tier1Context`를 여기서 만든다 - 이후 "후보 0건" 조기 반환보다 위다.
    # **구성 자체는 LLM을 부르지 않으므로 비용 0이다.** 조기 반환 아래에
    # 두면 `samples`·`temperature`의 `__post_init__` 검증(2 이상·0 초과)이
    # 세그먼트 수·max_ratio에 따라 조건부로만 발동한다(2라운드 리뷰 C5
    # 실측: `samples=1`이 `max_ratio=0.0`에서는 통과하고 `0.2`에서는
    # `ValueError`). "나중에 안 터지게" 두려던 생성 시점 검증의 취지가
    # 입력 크기에 좌우되면 안 된다.
    tier1_ctx = Tier1Context(
        signal=ctx,
        provider_for=_provider_factory(provider, cache_dir=cache_dir, identity=identity),
        samples=samples,
        temperature=temperature,
    )

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
    #
    # **진리값이 아니라 `struct.empty`와 같은 술어를 쓴다** (2라운드 리뷰
    # C1) - `bool(target_text)`만 보면 공백뿐인 번역("   ")이 "번역 있음"
    # 으로 통과해 LLM을 부른다. `SelfConsistency.collect_tier1`의 내부
    # 가드(`if not seg.target_text`)도 같은 진리값이라 공백을 걸러내지
    # 못하므로, 여기서 막지 않으면 실제로 낭비 호출이 나간다(실측:
    # `target_text="   "`가 옛 필터를 통과해 2회 호출됨). hard_fail 판정과
    # 같은 기준(`struct.empty`의 `text and text.strip()`)으로 통일해 "번역
    # 실패"의 정의가 신호 계층과 트리아지 계층에서 갈라지지 않게 한다.
    candidates = [
        s for s in segments if s.id in candidate_ids and s.target_text and s.target_text.strip()
    ]

    if not candidates:
        # LLM을 부르지 않는 경로. 원인이 넷 있으므로 조용히 넘어가지 않고
        # 사유를 구분해 알린다 - 위 "Tier 1 후보가 0건일 때" 절 참고.
        warn(_diagnose_empty_candidates(scored, candidate_ids, max_ratio))
        return scored

    # ⑤ Tier 1 - 후보에만
    tier1 = collect_tier1(candidates, tier1_ctx)

    # ⑥ 재융합 - Tier 0 신호에 Tier 1 신호를 더해 다시 계산한다.
    # 이름은 "re-scored"다 - 두 신호 출처를 **더한다**(신호 소실 없음)는
    # 것이 노이즈-오 융합의 전제다 - 한쪽만 남기면 이미 확보한 Tier 0
    # 신호(예: struct.number_missing)가 조용히 사라진다.
    rescored = [fuse(seg.id, tier0[seg.id] + tier1.get(seg.id, [])) for seg in segments]

    # ⑦ 예산 재적용
    return select_by_budget(rescored, budget_ratio)


def _diagnose_empty_candidates(
    scored: Sequence[SegmentRisk], candidate_ids: set[str], max_ratio: float
) -> str:
    """Tier 1 후보가 0건인 이유를 구분한다 (Task 4 리뷰 조건).

    구분하지 않으면 "세그먼트가 없다"·"사용자가 껐다"·"상한이 내림으로
    0이 됐다"·"회색지대가 비었다"·"후보가 전부 번역 실패분"이 전부 같은
    무음 침묵으로 보여 무음 열화(Q3)가 된다. `gray_zone()`을
    `select_tier1_candidates`와 공유해 회색지대 정의가 두 곳에서 갈라지지
    않게 한다(2라운드 리뷰 C3) - 복제했다면 그 함수가 제외 조건을 하나
    더 넣을 때 이 진단이 조용히 틀린 원인을 말하게 됐을 것이다.
    """
    # 빈 입력을 "전부 hard_fail이거나 선별됨"으로 잘못 읽으면(2라운드 리뷰
    # C2) 파서가 자막을 하나도 못 읽은 사고가 "전량 hard_fail"로 보인다 -
    # 원인 구분이라는 이 함수의 존재 이유와 정면으로 어긋난다.
    if not scored:
        return "세그먼트가 0건이다 - Tier 1이 아니라 입력 자체를 봐야 한다"
    if max_ratio == 0.0:
        return "max_ratio=0.0 - Tier 1을 껐다 (정상)"
    if candidate_ids:
        # cap>0이고 선별도 됐지만 전부 번역 실패분(target_text 없음 또는
        # 공백)이라 걸러졌다. **도달한다** - `source_text`가 공백뿐이면
        # `struct.empty`가 hard_fail을 내지 않아 회색지대를 그대로
        # 통과한다(모듈 독스트링 "넷째" 절 참고).
        return "후보로 뽑혔지만 전부 번역 실패분(target_text 없음 또는 공백)이라 제외됐다"
    if gray_zone(scored):
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
