"""Tier 0 -> Tier 1 2라운드 트리아지 (설계 §7 · FR-4.1 · FR-4.3).

**noisy-or가 이 구조를 성립시킨다.** `1 - ∏(1 - sᵢ)^wᵢ`는 신호가 붙을수록
점수가 올라가기만 하므로, 회색지대에만 Tier 1을 적용해도 적용받은 쪽이
부당하게 낮아지지 않는다. 가중 평균이었다면 낮은 Tier 1 점수가 기존
위험도를 희석해 오히려 큐에서 밀어냈을 것이다.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from cuesift.progress import ProgressCallback
from cuesift.risk.fuse import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals.backtranslation import BackTranslation
from cuesift.signals.base import (
    SignalContext,
    Tier1Context,
    collect_all,
    collect_tier1,
    registry,
)
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
    excluded_ids: Collection[str] = (),
    weights: Mapping[str, float] | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[SegmentRisk]:
    """Tier 0로 좁히고 회색지대에만 Tier 1을 적용한 뒤 다시 선별한다.

    **`excluded_ids`를 뺀 전체 목록을 반환한다.** `select_by_budget`과 같은
    계약이며, 선별된 것만 반환하면 `review_ratio`가 언제나 1.0이 되어
    README 배수의 분모가 무너진다. 제외분이 반환에서 빠진다는 뜻이 무엇인지는
    아래 `excluded_ids` 절의 "반환 길이" 문단이 단일 출처다.

    `temperature`의 기본값이 1.0인 것은 OpenAI Chat Completions API 명세의
    기본값이라 **출처가 있기 때문이다**(§11 R8 - 출처 없는 수치를 기본값으로
    넣지 않는다). 0.0이면 재번역이 전부 같아 신호가 죽는데, 그 방어는
    `Tier1Context`가 한다.

    **`weights`는 두 `fuse` 호출에 모두 간다**(FR-8.4 · 설계 §4.3 ②).
    ②만 넘기고 ⑥을 두면 사용자 가중치로 고른 후보를 기본 가중치로 다시
    세우게 되어, 가중치를 설정한 사용자에게만 순위가 어긋난다. `None`이면
    `fuse`가 `DEFAULT_WEIGHTS`를 쓴다.

    ## `excluded_ids` - 수집과 융합의 입력이 다르다

    근거는 `docs/superpowers/specs/2026-08-25-tier1-cli-design.md`의 **D5**다.
    수집·융합을 나눠야 하는 이유 자체는 그보다 앞선
    `docs/superpowers/specs/2026-08-18-triage-cli-design.md`의 **D12**에 있다.
    **문서명 없이 `D5`·`D12`만 적으면 안 된다** - 리포 안에 같은 번호의 다른
    결정이 있어(`2026-07-31-ingest-design.md`의 D5 = "모듈은 단일 진입점",
    `2026-08-03-check-cli-design.md`의 D12 = "`--config`는 경고하고 무시한다")
    6개월 뒤 독자가 틀린 근거에 도달한다.

    **수집(`collect_all`)은 전량을 본다. 융합(`fuse`)은 `excluded_ids`를 뺀
    것만 본다.** 둘은 서로 다른 층의 요구다.

    | 단계 | 입력 | 이 입력이 아니면 무엇이 깨지나 |
    | --- | --- | --- |
    | 수집 | 전량 | 이웃을 봐야 판정되는 배치 신호가 이웃을 못 본다 |
    | 융합·선별 | 뺀 것 | 실패분의 hard fail이 예산 quota를 먹는다 |

    **융합에 넣으면** 실패분의 hard fail이 예산을 우회해 quota를 소진하고
    진짜 오류가 큐에서 밀린다 - 200큐·진짜 오류 20건·예산 10%에서 실패
    20건이면 **Recall@10%가 0%** 가 된다. 이 실측의 리포 안 출처는
    `cli.py`의 `_run_triage` 독스트링이다(30건일 때의 수치도 거기 있다).

    **수집에서 빼면** `spec.overlap`이 실패분과 겹친 **성공한** 큐의
    겹침까지 놓친다 - 실측으로 같은 2큐 파일에서 실패 1건이면 미출력이
    됐다. **요약도 종료 코드도 침묵하는** 조용한 실패라 더 나쁘다.

    ### 반환 길이 - `review_ratio`의 분모는 성공분이다

    반환 길이 = `len(segments) - len(excluded ∩ ids(segments))`다. 따라서
    `review_ratio`가 내는 비율의 분모는 트랙 전체가 아니라 **성공분**이다.
    실측 - 10큐 중 2건이 번역 실패이고 예산 10%일 때:

    | 호출 | n | selected | `review_ratio` |
    | --- | --- | --- | --- |
    | `excluded_ids` 없이 | 10 | 2 | 0.200 |
    | `excluded_ids` 2건 | 8 | 1 | 0.125 |

    **실제 사람 부하는 3/10 = 0.300이다** - 실패 2건도 사람이 봐야 하기
    때문이다. 즉 이 함수의 반환값만으로는 트랙 전체 대비 비율을 만들 수
    없고, 여기서 되돌리지도 않는다(제외 여부를 아는 것은 호출자다).

    **호출자는 이미 필요한 셋을 갖고 있다.** `report/models.py`의
    `TriageOutcome`이 `total_segments`·`triaged_segments`·`excluded_failures`
    로 나눠 싣는다 - 트랙 전체 대비 비율이 필요하면 제외 건수를 분모에
    되돌려야 하고, 그 재료가 거기 있다. **`review_ratio` 하나만 보고 README
    배수를 내면 분모가 성공분이라 배수가 부풀려진다.**

    **기본값이 빈 튜플이라 이 인자를 주지 않는 호출부는 거동이 완전히
    불변이다** - 그 성질이 이 인자를 하위 호환으로 만든다. 이 주장을 지키는
    게이트는 **이 인자를 주지 않고 이 함수를 부르는 기존 테스트 전량**이다
    (재리뷰 축2 - 전용 테스트를 따로 두면 동어반복이 되고, 실제로 1라운드에서
    그 이유로 하나 지웠다). 기본값이 `()`가 아니게 되면 그것들이 먼저 깨진다.

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

    원인은 여섯이다 - `_diagnose_empty_candidates`가 구분한다.

    | 원인 | 의미 |
    | --- | --- |
    | 세그먼트 0건 | 입력 자체가 비었다 |
    | 전량이 `excluded_ids`로 빠짐 | 입력은 있는데 `kept`가 비었다 - 번역 전량 실패 |
    | `max_ratio=0.0` | 사용자가 Tier 1을 껐다 - 정상 |
    | 후보가 전부 번역 실패분 | `target_text`가 없거나 공백이라 걸러졌다 |
    | 상한이 0으로 내려감 | `select_tier1_candidates`의 내림(`floor`) 때문에 상한이 0이다 |
    | 회색지대가 빔 | 전부 hard_fail이거나 이미 선별됐다 |

    **서수로 가리키지 않는다.** 위 표·아래 분기·테스트 셋이 각각 다른 순서를
    갖게 되면(재리뷰 축2 실측: 같은 사유가 세 곳에서 넷째·다섯째·여섯째로
    적혀 있었다) 참조가 조용히 어긋난다. 이름으로 가리킨다.

    **「전량이 `excluded_ids`로 빠짐」과 「세그먼트 0건」을 뭉치면 안 된다.**
    전량 제외는 `scored`가 비어 후자와 똑같이 보이는데 원인은 정반대다 -
    후자는 파서가 자막을 못 읽은 것이고 전자는 **번역이 전량 실패한 것**이다.
    호출자가 실패분 id를 `excluded_ids`로 넘기면 프로바이더가 죽었을 때 바로
    이 경로이고, 그때 "입력 자체를 봐야 한다"고 보고하면 사람을 정확히
    반대쪽으로 보낸다. **그 호출자는 아직 리포에 없다** - `cli.py`의
    `_run_triage`는 지금 이 함수를 부르지 않고 호출 전에 걸러 낸다.
    배선은 WP8b Task 6이 한다(재리뷰 축2 - 현재형으로 단정하면 6개월 뒤
    독자가 `grep excluded_ids`로 아무것도 못 찾는다).

    **「후보가 전부 번역 실패분」은 도달한다.** `struct.empty`가
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
    입력 문장 길이 L의 선형함수라 L을 함께 밝혀야 재현되므로, L을 고정하지
    않는 이 문서에서는 배수만 인용한다). 이 함수는 후보를 좁혀 총 호출
    수를 줄이지만 세그먼트 단위 프로토콜 자체는 바꾸지 않는다 - 그 변경은
    Task 2의 파일과 이 오케스트레이션을 함께 건드리는 설계 변경이라 WP8b
    비범위다.

    **결론 - `max_ratio`를 얼마로 두면 §4의 한도를 넘는가.** 기본
    `samples=3`·`Tier1Collector` 1종에서 세그먼트당 Tier 1 호출은
    `3 × max_ratio × n`이고, 번역 기준선(배치 10)은 `n / 10`이므로
    **`기준선 대비 배수 = 30 × max_ratio`**다(n=100·500·1000에서 동일 -
    구조적이라 트랙 크기에 무관하다). 요구사항정의서 §4가 "3배는 감당
    불가"라 적은 한도에 `max_ratio=0.10`이 **정확히 걸린다** - 이 저장소의
    표준 예산값(10%, README)과 대칭인 값을 그대로 기본값으로 고르면
    §4가 막으려던 비용에 부딪힌다는 뜻이다. 실효 절감을 얻으려면
    `max_ratio ≤ 0.05`(1.5배)가 필요하고, 이 제약은 세그먼트 단위
    프로토콜을 배치로 되돌리면 사라진다(WP8b 비범위, 위 문단 참고).

    **토큰 사용량이 유실된다.** `collect_tier1`은 `Signal`만 반환하고
    `TranslationResult.usage`를 실어 나를 통로가 없다 - NFR-2·FR-7.4가
    요구하는 비용 숫자에서 가장 비싼 계층(Tier 1)만 리포트에서 빠진다는
    뜻이다. 여기서 고치지 않는 이유는 `collect_tier1`의 반환형을 바꾸는
    프로토콜 변경이 되기 때문이다 - 누적은 리포트 계층(WP8b)의 일이다.
    **두 한계는 곱해진다** - 배치 무력화가 비용을 키우고(위 문단) 토큰
    미집계가 그것을 숨긴다. **문자 배수는 여기 적지 않는다** - 위 문단이
    이미 세운 규칙(L을 밝히지 않은 문자 수치는 인용하지 않는다)을 24줄
    아래에서 스스로 어긴 적이 있다(최종 브랜치 리뷰 - L을 스윕하면
    n=1000·`max_ratio=0.10` 고정에서도 배수가 1.9(L=3)에서 1.0 아래
    (L=40)까지 움직여 "더 쓴다"는 부호 자체가 뒤집혔다). 결론은 문자
    배수 없이도 선다 - `result.usage`를 통째로 버리므로 Tier 1이 실제로
    몇 자를 더 쓰든 **그중 한 글자도 리포트에 안 잡힌다**는 사실 하나로
    충분하다. `--dry-run`도 이 유실을 **토큰에서는** 그대로 물려받는다 -
    **재번역 요청** 수만 `floor(n × max_ratio) × samples` 상한으로 화면에
    낸다(설계 D10 - `cli._TIER1_BOUND_PREFIX`). 요청 하나가 재시도·개별
    폴백으로 여러 호출이 되므로 그 화면도 프로바이더 **호출** 수의 상한은
    아니다 - 재시도 횟수는 백엔드 사정이라 산식이 내는 수가 아니다(§11 R8).

    `on_progress`는 **⑤ Tier 1 수집에만 간다**(FR-8.5 · 설계 D1). 이 함수가
    자기 진행을 따로 만들지 않는 이유는 ①~④(수집·융합·후보 선정)가 LLM
    호출이 없어 순식간에 끝나기 때문이다 - 섞으면 분모가 두 겹이 되고
    사용자는 "무엇의 진행인지"를 잃는다.
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

    # **`set`으로 정규화한다.** 호출자가 list를 넘기면 아래 `in`이 O(n)이 되어
    # 전체가 O(n^2)가 된다 - 1000큐 트랙에서 실제로 느려진다. 인자 타입을
    # `Collection`으로 넓게 받고 여기서 좁히는 것이 호출부에 set을 강요하지
    # 않으면서 성능을 지키는 방법이다.
    #
    # **`str`은 막는다.** `str`도 타입상 유효한 `Collection[str]`이라
    # `set("10")`이 `{"1", "0"}`으로 쪼개지는데 이 게이트에는 mypy가 없어
    # 안 걸린다(실측: 12큐에 `excluded_ids="10"`을 주면 "10"은 남고 "0"·"1"이
    # 사라진다). 이 저장소에는 정수 id 계약 사고(커밋 817ed64)가 이미 있다.
    #
    # `bytes`도 같이 막는다. `set(b"10")`은 `{49, 48}`으로 **정수**를 내므로
    # 어떤 id와도 안 맞아 제외가 통째로 무음 실패한다 - `str`보다 나쁘다
    # (재리뷰 축2). 반대로 `frozenset`·`dict.keys()`·제너레이터는 통과해야
    # 하므로 이 둘만 지목해 막는다.
    if isinstance(excluded_ids, str | bytes):
        raise TypeError(
            f"excluded_ids에 {type(excluded_ids).__name__}을 그대로 넘겼다"
            f"({excluded_ids!r}) - 원소 단위로 쪼개진다. 집합이나 리스트로 감싸라"
        )
    excluded = set(excluded_ids)
    kept = [seg for seg in segments if seg.id not in excluded]

    # ② 1차 융합 - **`kept`만** 본다(설계 D5). `segments`를 그대로 두면
    # 번역 실패분이 hard fail로 예산 quota를 먹어 진짜 오류가 큐에서 밀린다.
    risks = [fuse(seg.id, tier0[seg.id], weights) for seg in kept]

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
    #
    # **`segments`가 아니라 `kept`를 순회한다.** 오늘은
    # `candidate_ids ⊆ ids(kept)`라 결과가 같지만, `select_tier1_candidates`가
    # `scored` 외의 출처를 보게 되면 제외분이 LLM 호출로 되살아난다 -
    # 제외분에 돈을 쓰는 것은 이 인자의 존재 이유와 정면으로 어긋난다.
    candidates = [
        s for s in kept if s.id in candidate_ids and s.target_text and s.target_text.strip()
    ]

    if not candidates:
        # LLM을 부르지 않는 경로. 원인이 여섯 있으므로 조용히 넘어가지 않고
        # 사유를 구분해 알린다 - 위 "Tier 1 후보가 0건일 때" 절 참고.
        #
        # `excluded_count`를 `len(excluded)`가 아니라 차집합 크기로 내는 것은
        # **오늘 관측되지 않는다**(재리뷰 두 축이 각각 실측: 바꿔도 1174건이
        # 전부 통과한다). `not scored` ⟺ `kept == []` ⟺ 전량 제외라 저 분기에
        # 닿은 시점에는 두 식이 같은 판정을 낸다 - 판정을 실제로 가르는 것은
        # `total`뿐이고, 위 P12 정당화도 `total`에만 성립한다.
        # 그럼에도 차집합을 쓰는 이유는 `not scored` 조건이 나중에 완화되면
        # (예: "거의 다 빠졌다"로) `len(excluded)`가 미지의 id까지 세어
        # 판정을 부풀리기 때문이다. **측정할 수 없는 근거라고 밝힌 채 남긴다.**
        warn(
            _diagnose_empty_candidates(
                scored,
                candidate_ids,
                max_ratio,
                total=len(segments),
                excluded_count=len(segments) - len(kept),
            )
        )
        return scored

    # ⑤ Tier 1 - 후보에만
    #
    # **기본 집합을 여기서 직접 계산한다.** `collect_tier1(enabled=None)`은
    # 등록된 tier=1 신호 전부를 돈다(`signals/base.py`의 `tier == 1` 분기) -
    # `llm.backtranslation`(FR-4.2)이 등록되자 이 경로의 유일한 호출자인
    # 여기가 임베딩 백엔드를 강제로 요구하게 됐다. **이 필터가 없으면**
    # 임베딩 백엔드가 없는(= 아직 `embedder`를 안 쓴) 호출자의
    # `llm.self_consistency`(FR-4.1)까지 `ValueError`로 함께 죽는다 -
    # FR-4.2 하나가 FR-4.1의 기존 사용자를 깬다.
    #
    # `tier1_ctx.embedder is None`으로 판단하는 이유는 이것 하나다: Task 4가
    # `triage_with_tier1`에 `embedder` 인자를 더해 `Tier1Context`에 실어 주는
    # 순간 이 조건이 저절로 꺼지고 `llm.backtranslation`이 자동으로 켜진다 -
    # 이 필터를 다시 손볼 필요가 없다.
    #
    # **`set`이 아니라 리스트 컴프리헨션으로 순서를 보존한다.** `collect_tier1`의
    # 기본 경로(`enabled=None`, `signals/base.py:249`)는 `_REGISTRY` 삽입 순서를
    # 그대로 쓰는데, 여기서 `set`을 거치면 그 순서가 해시 시드에 종속된다 -
    # `collect_tier1`의 `for name in names` 루프(`:268`)가 LLM 호출 순서와
    # `SegmentRisk.signals` 배열 순서를 그대로 결정하므로, 신호가 둘 이상이 되는
    # 날(Task 4) `review.json`의 신호 순서·캐시 기록 순서가 실행마다 갈려
    # NFR-3(재현성)을 어긴다. 오늘 신호가 하나뿐이라 관측되지 않을 뿐이다.
    default_tier1 = [name for name, c in registry().items() if c.tier == 1]
    if tier1_ctx.embedder is None:
        # 문자열을 하드코딩하지 않는다 - `BackTranslation.name`이 바뀌면
        # 이 조건도 같이 바뀐다. `signals.backtranslation`은 `tier1.py`를
        # import하지 않으므로 순환이 생기지 않는다(신호 등록 시점에
        # `signals/__init__.py`가 이미 이 모듈을 당겨 온 뒤다).
        default_tier1 = [name for name in default_tier1 if name != BackTranslation.name]
    tier1 = collect_tier1(candidates, tier1_ctx, enabled=default_tier1, on_progress=on_progress)

    # ⑥ 재융합 - Tier 0 신호에 Tier 1 신호를 더해 다시 계산한다.
    # 이름은 "re-scored"다 - 두 신호 출처를 **더한다**(신호 소실 없음)는
    # 것이 노이즈-오 융합의 전제다 - 한쪽만 남기면 이미 확보한 Tier 0
    # 신호(예: struct.number_missing)가 조용히 사라진다.
    #
    # **여기도 `kept`다.** ②만 고치고 여기를 두면 Tier 1이 실제로 도는
    # 경로에서만 실패분이 되살아난다 - 후보 0건일 때는 조기 반환이라
    # 드러나지 않아, Tier 1을 켰을 때와 안 켰을 때의 분모가 갈라진다.
    rescored = [fuse(seg.id, tier0[seg.id] + tier1.get(seg.id, []), weights) for seg in kept]

    # ⑦ 예산 재적용
    return select_by_budget(rescored, budget_ratio)


# **두 곳이 같은 문장을 써야 한다** - 실행 경로의 `_diagnose_empty_candidates`와
# dry-run의 상한 줄이다. 복제하면 한쪽만 고쳐져 dry-run이 조용히 다른 원인을
# 말하게 된다(`gray_zone()`을 공유한 것과 같은 이유, 2라운드 리뷰 C3).
_ZERO_BY_SWITCH = "max_ratio=0.0 - Tier 1을 껐다 (정상)"


def _zero_by_floor(total: int, max_ratio: float, *, noun: str) -> str:
    """상한이 내림으로 0이 된 사정을 적는다.

    **수 둘을 문장에 넣지 않으면 사용자가 무엇을 올려야 하는지 모른다** -
    `max_ratio`를 키울지 세그먼트를 늘릴지가 이 두 수의 관계로만 정해진다.

    **`noun`을 호출자가 주는 이유: `total`이 호출자마다 다른 수다.** dry-run은
    자막 전체(`len(result.segments)`)를 넘기고 실행 경로는 번역 실패분이 빠진
    수(`len(scored)`)를 넘긴다. 양쪽이 "세그먼트 수(N)"라고 말하면 20컷 중
    16건이 실패한 실행에서 화면이 "세그먼트 수(4)"라고 말하고, 사용자는 파일이
    4컷이라 읽어 처방이 어긋난다.

    **기본값을 두지 않는 것은 `warn`·`total`과 같은 이유다**(Ruling P12) -
    기본값이 있으면 새 호출부가 넘기지 않고도 멀쩡한 문자열을 받아, 틀린 명사가
    조용히 화면에 나간다.
    """
    return (
        f"{noun}({total})에 비해 max_ratio({max_ratio})가 작아 "
        "Tier 1 상한이 내림(floor)으로 0이 됐다 "
        "(select_tier1_candidates 독스트링 - n < 1/max_ratio)"
    )


def explain_zero_bound(total: int, max_ratio: float) -> str | None:
    """Tier 1 상한이 0이 되는 **번역 없이 계산 가능한** 원인을 말한다.

    나머지 넷(전량 excluded · 후보가 전부 실패분 · 회색지대 공백 · 세그먼트 0건)은
    채점된 `SegmentRisk`가 있어야 판정되므로 dry-run에서는 알 수 없다 -
    `_diagnose_empty_candidates`가 그것을 한다. **모르는 것을 말하지 않는 것이
    이 함수의 계약이다.**

    **지금 dry-run이 실제로 내는 갈래는 내림 하나뿐이다.** `max_ratio == 0.0`은
    CLI가 `--tier1`과 함께 오면 "스위치와 모순"으로 exit 2에서 끊어 dry-run
    분기에 닿지 못한다. 그런데도 그 갈래를 남기는 이유는 **문구를
    `_diagnose_empty_candidates`와 공유하는 것이 `_ZERO_BY_SWITCH`의 존재
    이유**여서다 - 여기서 떼면 상수의 소비자가 실행 경로 하나가 되어, 복제
    금지를 지키던 테스트(`test_실행_경로와_같은_문자열을_쓴다`)가 무엇을
    지키는지 알 수 없게 된다. CLI가 그 조합을 허용하도록 바뀌면 여기가 이미
    맞는 답을 갖고 있다.

    **상한이 0이 아니면 `None`이어야 한다.** 아무 때나 문장을 내면 화면이 늘
    시끄러워져 진짜 0회일 때의 사유가 묻힌다.
    """
    if max_ratio == 0.0:
        return _ZERO_BY_SWITCH
    if math.floor(total * max_ratio) == 0:
        # dry-run은 번역을 안 하므로 `total`이 자막 전체다. 실행 경로가 쓰는
        # 명사("번역 성공 세그먼트 수")를 여기 쓰면 아직 번역하지도 않은 수를
        # 성공분이라 부르게 된다.
        return _zero_by_floor(total, max_ratio, noun="세그먼트 수")
    return None


def _diagnose_empty_candidates(
    scored: Sequence[SegmentRisk],
    candidate_ids: set[str],
    max_ratio: float,
    *,
    total: int,
    excluded_count: int,
) -> str:
    """Tier 1 후보가 0건인 이유를 구분한다 (Task 4 리뷰 조건).

    구분하지 않으면 "세그먼트가 없다"·"전량이 excluded_ids로 빠졌다"·
    "사용자가 껐다"·"상한이 내림으로 0이 됐다"·"회색지대가 비었다"·
    "후보가 전부 번역 실패분"이 전부 같은 무음 침묵으로 보여 무음
    열화(Q3)가 된다.

    `total`·`excluded_count`에 기본값이 없는 것은 위 `warn`과 같은 이유다
    (Ruling P12) - 기본값을 주면 넘기지 않은 호출부도 멀쩡한 문자열을
    돌려받아 **반환 형태가 같은 채로** 번역 전량 실패가 "입력 자체를 봐야
    한다"로 오진된다. 실측 - **호출부(`warn(...)` 안)의 `total=`·
    `excluded_count=` 두 줄을 지우면** 이 파일의 테스트 8건이 `TypeError`로
    죽는다. **재현 방법을 함께 적는 이유**는 kwarg를 전역에서 떼는 계측으로
    재면 9가 나오기 때문이다 - 그 방식은 이 함수를 **직접** 부르는 단위
    테스트의 인자까지 떼어 프로덕션 호출부와 무관한 1건을 덤으로 죽인다
    (재리뷰 축2가 9를 보고했고 직접 변이로 8을 재확인했다). 무음 열화를
    시끄러운 실패로 바꾸는 것이
    기본값을 빼는 목적이다. `gray_zone()`을
    `select_tier1_candidates`와 공유해 회색지대 정의가 두 곳에서 갈라지지
    않게 한다(2라운드 리뷰 C3) - 복제했다면 그 함수가 제외 조건을 하나
    더 넣을 때 이 진단이 조용히 틀린 원인을 말하게 됐을 것이다.
    """
    # 빈 입력을 "전부 hard_fail이거나 선별됨"으로 잘못 읽으면(2라운드 리뷰
    # C2) 파서가 자막을 하나도 못 읽은 사고가 "전량 hard_fail"로 보인다 -
    # 원인 구분이라는 이 함수의 존재 이유와 정면으로 어긋난다.
    if not scored:
        # 입력은 있는데 `scored`가 비었다면 전량이 `excluded_ids`로 빠진
        # 것이다 - 원인이 **정반대**라 뭉치면 안 된다. 파서 사고가 아니라
        # 번역 전량 실패이고, "입력 자체를 봐라"는 사람을 반대쪽으로 보낸다.
        # 호출자가 실패분 id를 넘기면 프로바이더가 죽었을 때 이 경로다.
        # **그 호출자는 아직 없다** - 배선은 WP8b Task 6이 한다.
        if total > 0 and excluded_count >= total:
            return (
                f"세그먼트 {total}건이 전부 excluded_ids로 빠졌다 - "
                "입력이 아니라 번역이 전량 실패했는지 봐야 한다"
            )
        return "세그먼트가 0건이다 - Tier 1이 아니라 입력 자체를 봐야 한다"
    if max_ratio == 0.0:
        return _ZERO_BY_SWITCH
    if candidate_ids:
        # cap>0이고 선별도 됐지만 전부 번역 실패분(target_text 없음 또는
        # 공백)이라 걸러졌다. **도달한다** - `source_text`가 공백뿐이면
        # `struct.empty`가 hard_fail을 내지 않아 회색지대를 그대로
        # 통과한다(모듈 독스트링 「후보가 전부 번역 실패분」 절 참고).
        return "후보로 뽑혔지만 전부 번역 실패분(target_text 없음 또는 공백)이라 제외됐다"
    if gray_zone(scored):
        # **`scored`는 자막 전체가 아니다** - `excluded_ids`(번역 실패분)가
        # 빠진 뒤 채점된 것이다. "세그먼트 수"라고 부르면 전량의 80%가 실패한
        # 실행에서 사용자가 파일 크기를 오독한다.
        return _zero_by_floor(len(scored), max_ratio, noun="번역 성공 세그먼트 수")
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
