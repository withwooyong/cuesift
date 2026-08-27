"""트리아지 결과 모델 (요구사항정의서 FR-7.2 · 설계 §7.1).

**화면 요약과 `review.json`의 공통 출처다.** 두 소비자가 수치를 각자 세면
조용히 갈라지는데, 그때 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도
0이라 어떤 게이트에도 걸리지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from cuesift.segment import Segment, SegmentRisk
from cuesift.translate.provider import TokenUsage
from cuesift.triage import review_ratio as _review_ratio

# 집계 범위를 **결과 객체가 들고 다닌다** (설계 D8 · 이월 5번).
#
# 이 값이 상수로 고정돼 있으면 Tier 1을 켠 실행에서 `cost`가 번역 토큰만
# 세면서 전체인 척하고, 그 사실을 소비자가 알 수단이 없다. 필드로 두면
# **집계를 늘린 자리가 이 값도 함께 바꾸게 된다** - 갈라질 자리가 구조적으로
# 없어진다(NFR-2 · FR-7.4).
COST_INCLUDES_TRANSLATION: tuple[str, ...] = ("translation",)

# 계층별 **계측 규약**. `includes`는 "무엇을 셌나"(범위)만 말하고 "어떻게 셌나"는
# 말하지 않는데, 이 프로젝트에서 두 계층의 규약이 **서로 반대다**(Task 3 리뷰 이월).
#
# | 계층 | 캐시 적중을 | 근거 |
# | --- | --- | --- |
# | translation | 포함해서 센다 | `store/provider.py`가 저장된 usage를 그대로 낸다 |
# | tier1 | 제외하고 센다 | `CountingProvider`가 캐시 **안쪽**에 놓인다 (D7) |
#
# 양쪽 다 의도된 것이라 일치시킬 수 없다. 번역 쪽에서 캐시 적중을 0으로 만들면
# `calls`가 0이 되어 "호출당 토큰"을 영영 계산할 수 없고(설계 §3.5.1), Tier 1 쪽을
# 캐시 바깥으로 옮기면 *요청한* 호출을 세어 `cost`가 청구서와 어긋난다(설계 D7).
#
# 둘이 하나의 `prompt_tokens`로 합쳐지므로 **범위만 밝히면 그 숫자가 청구서와 왜
# 다른지 알 방법이 없다.** 여기에 없는 계층을 `cost_includes`에 넣으면 생성 시점에
# 거부된다 - 그래야 범위를 넓힌 사람이 규약도 함께 선언한다.
COST_BASIS: dict[str, str] = {
    "translation": "cached-included",
    "tier1": "sent-only",
}

# 규약 어휘는 **닫힌 집합이고 단일 출처는 §8.4다.**
#
# 키를 파생시킨 것만으로는 절반만 닫힌다 - `"tier2": "cached-incuded"`(오타)나
# `"estimated"`(§8.4에 없는 제3의 어휘)를 등록해도 그대로 JSON에 실리고, 소비자는
# 그 낱말이 무엇을 뜻하는지 물어볼 데가 없다. 기존 두 값은 리포트 테스트의 등식
# 단언이 지키지만 **새로 등록되는 계층은 무방비**다(mypy가 없어 `Literal`은
# 강제되지 않는다). `tests/test_report_models.py`가 이 집합과 §8.4 본문이
# 일치하는지 따로 검사한다 - 코드만 고치고 문서를 안 고치면 거기서 걸린다.
COST_BASIS_VOCABULARY: frozenset[str] = frozenset({"cached-included", "sent-only"})


def _validate_basis_vocabulary(basis: dict[str, str]) -> None:
    """`COST_BASIS`의 **값**이 닫힌 어휘 안에 있나. 모듈 적재 시점에 돈다.

    `__post_init__`에서 보면 **실제로 쓰인 계층만** 검사돼 등록만 해 둔 오타가
    통과한다. 여기서 던지면 잘못된 등록이 import를 막아 첫 테스트에서 드러난다.
    """
    strays = sorted(v for v in basis.values() if v not in COST_BASIS_VOCABULARY)
    if strays:
        raise ValueError(
            f"COST_BASIS에 §8.4에 없는 규약 어휘가 있다: {strays} - "
            f"허용: {sorted(COST_BASIS_VOCABULARY)}"
        )


_validate_basis_vocabulary(COST_BASIS)


def layer_tokens_reported(usage: TokenUsage | None) -> bool:
    """계층 하나가 토큰 수치를 실었나 (요구사항정의서 §12 Q3 · NFR-2).

    **판별식을 여기 한 곳에만 둔다.** 화면(`cli.py`)과 파일(`review.json`)이
    각자 판별하면 같은 실행에서 한쪽만 경고하게 되고, 그것은 경고가 없는 것보다
    나쁘다 - 사용자는 둘 중 하나를 오작동으로 읽는다.

    | 입력 | 결과 | 근거 |
    | --- | --- | --- |
    | `None` | `False` | **"모른다"는 "믿을 수 있다"가 아니다.** 수치가 없으면 뒷받침할 것도 없다 |
    | `calls == 0` | `True` | 성공 호출이 0이면 토큰 0이 **참이다** |
    | `calls > 0`, 토큰 합 0 | `False` | 백엔드가 usage를 안 냈다 |

    **`calls == 0`은 불변식으로 읽는다 - 경로 목록으로 읽지 않는다.** 이 문단은
    전에 "도달 경로는 A와 B뿐"이라는 열거였고 두 번 낡았다(한 번은 근거로 쓰면
    안 되는 경로를 근거로 적어서, 한 번은 WP8b가 Tier 1 계측을 캐시 **안쪽**에
    놓으면서 - 그러면 Tier 1 전량 캐시 적중이 `calls == 0`으로 온다). 새 계층이
    생길 때마다 조용히 낡는 형태라 열거를 버린다.

    불변식은 하나다 - **`calls`는 그 계층의 계측기를 실제로 통과한 성공 응답
    수다.** 따라서 `calls == 0`은 "그 계층이 이번 실행에서 유료 응답을 한 건도
    받지 않았다"이고, 그때 토큰 0은 관측이 아니라 **정의상 참**이다. 계측기가
    어디에 놓였느냐(캐시 안/밖)에 따라 같은 실행도 값이 달라지는 것이 정상이며,
    그 배치는 계층마다 다르다 - `COST_BASIS` 표가 단일 출처다.

    **"이 계층이 안 돌았다"를 여기서 표현하려 들면 안 된다.** 그것은
    `resolve_cost_scope`에 `None`을 넘겨 `includes`에서 빼는 방식으로 말한다.
    둘을 섞으면 꺼진 계층과 무음 계층이 한 낱말이 된다.

    전량 실패는 "믿을 수 있다"보다 "아무것도 성공 못 했다"에 가깝지만 `False`로
    내지 않는다. **그 사실은 `total_segments`·`triaged_segments`·`excluded_failures`가
    이미 말한다** - 여기서 겹쳐 말하면 이 신호가 "실패했다"와 "계측이 죽었다"를
    한 낱말로 섞어 어느 쪽인지 알 수 없게 된다.
    """
    if usage is None:
        return False
    if usage.calls == 0:
        # `TokenUsage.__post_init__`이 "calls 0인데 토큰 > 0"을 거부하므로
        # 여기서 토큰이 0임이 보장된다. 그 방어가 사라지면 이 줄이 **토큰을
        # 쓴 실행을 "계측 정상"으로 통과시킨다.**
        return True
    return usage.prompt_tokens + usage.completion_tokens > 0


@dataclass(frozen=True, slots=True)
class CostScope:
    """`cost` 블록에 필요한 셋을 **한 번에** 낸다 (설계 D8 · 리뷰 라운드 2).

    **왜 합계까지 여기서 내는가.** 범위·판정만 내면 호출자가 usage를 손으로
    합치게 되고, 그러면 합친 값을 키 하나로 다시 넘기는 경로가 열린다.
    실측이 그 함정이다.

    ```python
    resolve_cost_scope({"translation": translated.usage + counting.usage})
    # -> includes=("translation",) · unreported=()
    ```

    **Tier 1이 범위에서 조용히 사라지고 판정도 통과한다** - `cost_unreported`가
    막으려던 바로 그 사각이 되돌아온다. 그리고 함수는 호출자가 무엇을 합쳤는지
    알 수 없으므로 **검증으로는 막을 수 없다.** 합치는 일 자체를 여기로
    가져오면 손으로 합칠 이유가 없어져 그 경로가 애초에 생기지 않는다.

    **세 값을 튜플이 아니라 필드로 낸다.** `includes`와 `unreported`는 둘 다
    `tuple[str, ...]`이라 위치가 바뀌어도 타입으로는 드러나지 않는다 - 그때
    "범위"와 "무음 계층"이 통째로 뒤바뀌어 파일에 실린다. 이름으로 받으면
    그 실수가 성립하지 않는다.
    """

    usage: TokenUsage
    includes: tuple[str, ...]
    unreported: tuple[str, ...]


def resolve_cost_scope(usages: Mapping[str, TokenUsage | None]) -> CostScope:
    """계층별 usage에서 `cost` 블록의 입력 셋을 만든다.

    ```python
    scope = resolve_cost_scope(
        {"translation": translated.usage, "tier1": counting_usage_or_none}
    )
    TriageOutcome(
        ...,
        usage=scope.usage,
        cost_includes=scope.includes,
        cost_unreported=scope.unreported,
    )
    ```

    **값이 `None`이면 그 계층은 이 실행에서 돌지 않았다** - 범위에서 빠지고
    합계에도 안 들어간다. Tier 1을 끈 실행이 그 모양이고, 호출자가 dict를
    조건부로 조립하지 않아도 되게 하려는 것이다. 계층이 **돌았는데** 토큰을
    못 낸 경우는 `None`이 아니라 `TokenUsage(0, 0, calls=N)`으로 오고, 그때는
    범위에 들어가면서 `unreported`에 실린다. 둘을 섞으면 "안 돌았다"가
    "돌았는데 계측이 죽었다"로 보고돼 사용자가 없는 비용을 의심한다.

    **`TriageOutcome.usage`의 `None`과는 다른 질문이다.** 그쪽은 "합계가 있느냐"를
    묻고 여기 키는 후보 계층의 이름일 뿐이다. 두 곳의 `None`이 같은 뜻이라고
    읽으면 꺼진 계층이 무음 계층으로 둔갑한다.

    입력 순서를 그대로 보존한다(NFR-3 재현성). `dict`의 삽입 순서가 곧
    `includes`의 순서이고 그것이 파일에 나간다.
    """
    돈_계층 = {name: usage for name, usage in usages.items() if usage is not None}
    # 전부 `None`이면 `cost`가 무엇을 덮는지 말할 수 없다. `TriageOutcome`도
    # 빈 범위를 거부하지만 그 시점의 메시지는 **생성부**를 가리켜 원인이
    # 여기(잘못 조립한 매핑)라는 것을 감춘다.
    if not 돈_계층:
        raise ValueError(f"usages에 실제로 돈 계층이 없다: {sorted(usages)}")

    usage = TokenUsage()
    for layer_usage in 돈_계층.values():
        usage = usage + layer_usage

    return CostScope(
        usage=usage,
        includes=tuple(돈_계층),
        unreported=tuple(
            name for name, layer_usage in 돈_계층.items() if not layer_tokens_reported(layer_usage)
        ),
    )


@dataclass(frozen=True, slots=True)
class TriageOutcome:
    """대상 언어 하나의 트리아지 결과.

    **`risks`는 `select_by_*`가 돌려준 전체 목록이다.** 선별분만 담으면
    `review_ratio`가 언제나 1.0이 되고, 그 값이 스펙 §6.2의 "실제 검수 비율"이자
    README 배수의 분모라 조용히 틀리면 프로젝트의 핵심 주장이 무너진다.

    **`policy_label`과 `policy_kind`/`policy_value`는 중복이 아니다.** 라벨은
    사용자가 친 원본 문자열(`"10%"`)을 보존해 화면에 그대로 되돌려 주고,
    kind/value는 정규화된 값이라 `review.json`이 쓴다. 라벨을 kind/value에서
    재생성하면 화면 출력이 `"예산 10.0%"`로 바뀐다.

    **`segments`는 `risks`와 같은 집합이다**(번역 실패분이 빠진 것). `SegmentRisk`는
    `segment_id`만 갖고 타임코드·원문·번역문은 `Segment`에 있어 FR-7.2가 요구한
    필드를 채우려면 둘이 함께 필요하다.
    """

    source_lang: str
    target_lang: str
    profile_name: str
    policy_label: str
    policy_kind: str  # "budget" | "threshold"
    policy_value: float
    risks: tuple[SegmentRisk, ...]
    segments: tuple[Segment, ...]
    excluded_failures: int
    usage: TokenUsage | None
    cost_includes: tuple[str, ...] = COST_INCLUDES_TRANSLATION
    # `cost_includes` 중 **토큰 수치를 못 낸** 계층. 기본은 빈 튜플이다.
    #
    # **계층별 usage를 싣지 않고 판정 bool만 계층별로 남긴다.** `usage` 슬롯은
    # 하나뿐이라 배선부가 `+`로 합치는 순간 어느 계층이 0을 냈는지 복원할 수
    # 없는데(실측: `TokenUsage(1, 0, calls=1000)`은 합이 0이 아니라 통과한다),
    # 원천에서는 이미 나뉘어 있다(`translated.usage` vs `CountingProvider.usage`).
    # 판정만 여기로 올리면 **1토큰이 999회 무음 호출을 가리는 것**이 막히면서
    # `review.json`의 키 개수는 그대로다.
    cost_unreported: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # 형제 모델 넷(`Span`·`Segment`·`Signal`·`SegmentRisk`)과 같은 자리의 방어다.
        #
        # **아래 두 검사는 서로를 대신하지 못한다.** 길이만 보면 개수가 같고 id만
        # 어긋난 입력(`['00000','00001']` vs `['00000','99999']`)이 통과하고,
        # 집합만 보면 id가 중복될 때 개수 차이를 놓친다(`risks` 2개가 같은 id면
        # 집합은 1개라 `segments` 1개와 같아진다). 후자를 놓치면
        # `triaged_segments`와 `len(segments)`가 갈라져 §6.2의 검산식이 깨진다.
        if len(self.segments) != len(self.risks):
            raise ValueError(
                f"segments({len(self.segments)})와 risks({len(self.risks)})의 길이가 다르다"
            )
        # 리포트 생성기는 `{s.id: s for s in segments}` 표를 `risk.segment_id`로 찾는다.
        # id가 어긋나면 그 조회가 KeyError로 죽는데, 그 시점은 파일을 쓰는 도중이라
        # **어느 조합이 어긋났는지 스택에 남지 않는다.** 여기서 막아야 실패가 생성
        # 시점으로 앞당겨진다.
        #
        # **순서는 보장하지 않는다 - 집합으로만 본다.** `risks`는 위험도 내림차순이고
        # (`policy.py`의 `_sorted_desc`) `segments`는 트랙 원본 순서라 둘의 순서가
        # 다른 것이 정상이다. 순서까지 고정하면 그 정상 입력이 거부돼 트리아지 경로가
        # 통째로 죽는다.
        seg_ids = {s.id for s in self.segments}
        risk_ids = {r.segment_id for r in self.risks}
        if seg_ids != risk_ids:
            raise ValueError(
                f"segments와 risks의 segment_id 집합이 다르다 - "
                f"segments에만: {sorted(seg_ids - risk_ids)} · "
                f"risks에만: {sorted(risk_ids - seg_ids)}"
            )
        # 음수면 `total_segments`가 `triaged_segments`보다 작아져 화면이 "2개 중
        # 5개 검수"라는 불가능한 요약을 낸다. 프로그램은 정상 종료하고 종료 코드도
        # 0이라 어떤 게이트에도 걸리지 않는다.
        if self.excluded_failures < 0:
            raise ValueError(f"excluded_failures({self.excluded_failures})가 음수다")
        # **문자열을 그대로 넘긴 것을 먼저 가른다.** `cost_includes="translation"`은
        # 문자 단위로 쪼개져 `['t','r','a',...]`가 되는데, 아래 검사가 우연히
        # 거부하더라도 메시지가 원인을 가리키지 못한다. `Segment` 계열이 같은
        # 자리에서 같은 방식으로 가른다.
        for name, value in (
            ("cost_includes", self.cost_includes),
            ("cost_unreported", self.cost_unreported),
        ):
            if isinstance(value, str):
                raise ValueError(f"{name}에 문자열이 왔다({value!r}). 튜플이어야 한다")
        # **빈 범위는 정당한 경우가 없다.** `includes`는 "무엇이 돌았나"가 아니라
        # "이 숫자가 무엇을 덮나"이고, 트리아지가 도는 실행에 번역 계층은 언제나
        # 있다. 비어 있으면 `{"includes": [], "prompt_tokens": 1234}`가 종료 코드
        # 0으로 나가 **NFR-2 비용 투명성이 정확히 뒤집힌다.** "호출자가 그러지
        # 않는다"에 기대지 않는 것은 `triage_with_tier1`이 중복 id를 검사하는
        # 이유와 같다 - `TriageOutcome`도 공개 데이터클래스다.
        if not self.cost_includes:
            raise ValueError("cost_includes가 비었다. 비용 수치가 무엇을 덮는지 말하지 못한다")
        # 중복은 `includes`와 `basis`의 길이를 갈라놓는다 - `cost_basis`가 dict라
        # 조용히 dedupe되어 `includes` 2개 / `basis` 1개가 나간다. 파일만 보는
        # 소비자에게는 둘 중 무엇이 맞는지 판정할 근거가 없다.
        if len(set(self.cost_includes)) != len(self.cost_includes):
            raise ValueError(f"cost_includes에 중복 계층이 있다: {list(self.cost_includes)}")
        # **범위를 넓히려면 규약도 함께 선언해야 한다.** 이 검사가 없으면
        # `cost_includes=("translation", "tier2")`가 그대로 파일에 실리고, 그
        # 계층이 캐시 적중을 세는지 아닌지는 아무 데도 적히지 않은 채 소비자가
        # 추측한다 - `cost`의 세 수치는 계층별로 나뉘어 있지 않아 파일 안에서
        # 되짚을 수도 없다. 여기서 던지면 계층을 늘린 사람이 `COST_BASIS`에
        # 한 줄을 더할 수밖에 없다.
        unknown = [layer for layer in self.cost_includes if layer not in COST_BASIS]
        if unknown:
            raise ValueError(
                f"cost_includes에 계측 규약이 없는 계층이 있다: {unknown} - "
                f"COST_BASIS에 등록하라(알려진 계층: {sorted(COST_BASIS)})"
            )
        # 범위 밖 계층의 판정은 파일에 실릴 자리가 없다 - 실리지 않는 판정을
        # 받아 두면 배선부는 "신고했다"고 믿는데 리포트는 아무 말도 하지 않는다.
        outside = [layer for layer in self.cost_unreported if layer not in self.cost_includes]
        if outside:
            raise ValueError(
                f"cost_unreported에 cost_includes 밖의 계층이 있다: {outside} - "
                f"범위: {list(self.cost_includes)}"
            )

    @property
    def triaged_segments(self) -> int:
        """트리아지 대상 수. **`review_ratio`의 분모다** (설계 §6.2)."""
        return len(self.risks)

    @property
    def total_segments(self) -> int:
        """트랙 전체. `triaged + excluded`가 이 값이 되어야 파일 안에서 검산된다.

        **`triaged_segments`를 재사용한다.** `len(self.risks)`로 따로 세면 분모의
        정의가 2곳이 되고, 그때 `triaged`의 의미만 바뀌면 위 검산식이 조용히
        거짓이 된다 — 두 값이 지금 같다는 것은 우연이지 보장이 아니다.
        """
        return self.triaged_segments + self.excluded_failures

    @property
    def selected(self) -> tuple[SegmentRisk, ...]:
        """검수 큐에 담긴 것. `review.json`의 `segments[]`가 이것이다 (설계 D3)."""
        return tuple(r for r in self.risks if r.selected)

    @property
    def selected_for_review(self) -> int:
        return len(self.selected)

    @property
    def hard_fail_count(self) -> int:
        return sum(1 for r in self.risks if r.hard_fail)

    @property
    def signal_hits(self) -> dict[str, int]:
        """신호별 적발 건수. **전체를 본다 - 선별분이 아니다.**

        선별분으로 좁히면 예산 밖으로 밀린 위험이 사라져 사용자가 다음 예산을
        정할 근거를 잃는다. 정렬은 NFR-3(재현성)이다 - `Counter`의 순서는 삽입
        순이라 세그먼트 순서가 바뀌면 출력이 달라진다.

        **`reasons`는 0점 신호를 담지 않는다**(`fuse.py:73` - "0점 신호를 사유에
        넣으면 리포트가 '이것 때문에 뽑혔다'고 거짓말한다"). 따라서 이 집계가 곧
        "적발 **건수**"이지 "신호가 달린 횟수"가 아니다. 이 근거가 없으면 뒷사람이
        `signals`를 세도록 고쳐 놓고 이름은 그대로 두게 된다.
        """
        counts: Counter[str] = Counter()
        for risk in self.risks:
            counts.update(risk.reasons)
        return dict(sorted(counts.items()))

    @property
    def cost_basis(self) -> dict[str, str]:
        """`cost_includes`의 각 계층이 **어떻게** 세어졌나. 순서는 `cost_includes`를 따른다.

        **손으로 쓰지 않고 파생시킨다.** 범위와 규약을 따로 실으면 한쪽만
        고쳐져 갈라지는데, 갈라져도 파일은 정상이고 종료 코드도 0이다.
        **정상 생성 경로에서는** `__post_init__`이 미등록 계층을 막으므로
        `KeyError`가 나지 않는다 - `object.__setattr__`로 frozen을 우회해
        필드를 갈아 끼우면 그 보장은 깨진다(실측: `KeyError: 'tier9'`).
        frozen+slots의 한계이지 이 프로퍼티의 결함이 아니다.
        """
        return {layer: COST_BASIS[layer] for layer in self.cost_includes}

    @property
    def token_counts_reported(self) -> bool:
        """`cost`의 토큰 수치를 믿을 수 있나 (요구사항정의서 §12 Q3 · NFR-2).

        OpenAI 호환 엔드포인트라도 **능력은 균일하지 않다.** `_extract_usage`
        (`translate/openai_compat.py`)는 `usage`가 없거나 키 이름이 다르거나
        값이 문자열인 응답을 전부 `(0, 0)`으로 떨어뜨린다 - 실측으로
        `{"usage": {"total_tokens": 99}}`도 `(0, 0, calls=1)`이 된다. 이 판별이
        없으면 그 실행의 `cost`가 **"토큰 0개를 썼다"** 는 사실 주장으로 읽힌다.

        **두 층으로 본다. 순서가 중요하다.**

        1. `cost_unreported`가 비지 않았으면 `False`. 계층별 신고는 합계가
           숨기는 것을 본다 - `TokenUsage(1, 0, calls=1000)`은 합이 0이 아니라
           2번을 통과하지만, **1토큰이 999회 무음 호출을 가린 것**이다. Q3가
           경고한 "번역=상용 API · Tier 1=로컬"이 정확히 이 모양이고 그것이
           가장 흔한 구성이다.
        2. 그 다음 합계를 본다. 배선부가 계층별 신고를 아직 안 하는 동안에도
           전량 무음은 잡힌다 - **1번만 두면 미배선이 곧 "이상 없음"이 된다.**

        2번은 1번이 채워져도 남긴다. 판정을 **좁히지는 못하고 넓히기만** 하므로
        중복이 해가 되지 않는다.
        """
        if self.cost_unreported:
            return False
        return layer_tokens_reported(self.usage)

    @property
    def review_ratio(self) -> float:
        """실제 검수 비율 (0.0~1.0). 라이브러리 함수를 그대로 쓴다."""
        return _review_ratio(self.risks)
