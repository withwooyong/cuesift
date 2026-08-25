"""트리아지 결과 모델 (요구사항정의서 FR-7.2 · 설계 §7.1).

**화면 요약과 `review.json`의 공통 출처다.** 두 소비자가 수치를 각자 세면
조용히 갈라지는데, 그때 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도
0이라 어떤 게이트에도 걸리지 않는다.
"""

from __future__ import annotations

from collections import Counter
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
        `__post_init__`이 미등록 계층을 막으므로 `KeyError`는 도달 불가다.
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

        **`calls == 0`은 참인 0이다.** 전량 캐시 적중이나 dry-run이 여기 걸리면
        정상 실행 대부분이 "비용 불명"으로 찍혀 이 신호가 무시된다 - 무시되는
        경고는 없는 경고와 같다.

        **부분 열화는 잡지 못한다.** 번역 계층은 토큰을 내고 Tier 1만 못 내는
        실행은 합이 0이 아니라 `True`가 된다. `usage`가 계층별로 나뉘어 있지
        않기 때문이고, 여기서 더 조이려면 `TriageOutcome`이 계층별 usage를
        따로 실어야 한다 - 그것은 스키마 변경이라 §8.4를 먼저 고쳐야 한다.
        """
        if self.usage is None or self.usage.calls == 0:
            return True
        return self.usage.prompt_tokens + self.usage.completion_tokens > 0

    @property
    def review_ratio(self) -> float:
        """실제 검수 비율 (0.0~1.0). 라이브러리 함수를 그대로 쓴다."""
        return _review_ratio(self.risks)
