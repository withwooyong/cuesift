"""Tier 1 신호 — LLM을 불러 판정한다 (FR-4.1 · 설계 §6).

**후보 세그먼트에만 실행된다.** 전량에 적용하면 LLM 비용이 N배가 되고,
요구사항정의서 §4가 "16부작 × 20개 언어에서 3배는 감당 불가"라고 적었다.
실행 경로 분리는 `signals/base.py`의 `collect_tier1`이 맡는다.
"""

from __future__ import annotations

from itertools import combinations

from cuesift.segment import Segment, Signal
from cuesift.signals.base import Tier1Context, register
from cuesift.signals.similarity import similarity
from cuesift.translate import translate_segments


class SelfConsistency:
    """FR-4.1 — 같은 원문을 N회 재번역해 결과가 흩어지는 정도를 잰다.

    **"이 번역이 틀렸나"가 아니라 "이 구간이 번역하기 어려운가"다.**
    재번역들이 형태적으로 흩어졌다면 실제로 모델이 흔들린 것이다.

    의미 반전(`negation`)은 이 신호로 잡히지 않는다 - 원문에 부정이 살아
    있으므로 재번역 N개가 모두 부정을 제대로 살려 서로 비슷하게 나온다.
    그쪽은 기존 번역을 비교 집합에 넣어야 보이는데, 착수 시점 실측(설계
    §3.2)이 문자 단위 유사도로는 그 비교가 역방향으로 작동함을 보였다.
    """

    name = "llm.self_consistency"
    tier = 1

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        # 번역 실패분은 검수 대상이 아니라 재실행 대상이다
        # (TranslationResult 독스트링의 계약).
        if not seg.target_text:
            return None

        samples = self._retranslate(seg, ctx)

        # **None과 score=0.0은 다르다.** 성공분이 2개 미만이면 쌍이 없어
        # 판정 자체가 불가능하다. 0.0을 내면 "판정했고 안전하다"가 되어
        # 프로바이더 장애가 '안전'으로 보고된다.
        if len(samples) < 2:
            return None

        pairwise = [similarity(a, b) for a, b in combinations(samples, 2)]
        score = 1.0 - sum(pairwise) / len(pairwise)

        return Signal(
            name=self.name,
            tier=1,
            # 부동소수 오차로 -1e-16 같은 값이 나오면 Signal이 범위 검증에서
            # 죽는다. fuse의 noisy-or도 밑이 1을 넘으면 깨진다.
            score=min(1.0, max(0.0, score)),
            # hard fail로 두지 않는다. 의미 판단은 결정론적이지 않고,
            # hard fail 오탐은 실제 검수 비율을 부풀려 Recall@Budget 지표
            # 자체를 파괴한다 (FR-6.2).
            hard_fail=False,
            detail={
                # FR-6.4 - review.json이 "왜 선별되었는지"를 이것으로 쓴다.
                "samples": samples,
                "pairwise": pairwise,
                "temperature": ctx.temperature,
            },
        )

    def _retranslate(self, seg: Segment, ctx: Tier1Context) -> list[str]:
        """N회 재번역해 성공분만 낸다.

        `translate_segments`를 그대로 재사용한다 - 배치·컨텍스트 윈도우·
        재시도가 이미 구현돼 있고 다시 만들 이유가 없다.

        **시도마다 다른 프로바이더를 받는다.** 캐시가 `attempt`로 갈려야
        같은 입력에 다른 응답이 저장되고, 그래야 분산이 관측된다(설계 §8).
        같은 프로바이더를 N번 쓰면 2회차부터 캐시 히트가 나서 **분산이
        항상 0**이 된다.

        **여기서 예외를 잡지 않는다.** `translate_segments`가 이미 프로바이더
        예외 두 종류를 다르게 다룬다.

        - `RetryableProviderError`(503·타임아웃)는 재시도까지 소진하면
          이미 삼켜 `SegmentFailure`로 만든다 - `result.segments`에
          `target_text=None`으로 들어오고, 아래 `if translated.target_text:`가
          이미 거른다. 여기서 따로 잡을 것이 없다.
        - `FatalProviderError`(401 등)는 일부러 다시 던진다
          (`engine.py:468-469`). Retryable과 형제라 바깥
          `except RetryableProviderError`가 잡지 않는다 - 둘을 형제로
          두는 것 자체가 계약이다(`engine.py:439-452`). 여기서도 잡지
          않고 그대로 전파시킨다.

        **포괄 `except`를 달아 둘을 같이 삼키면 안 된다.** 401을 삼키면 이
        신호가 전 구간에서 **0건으로 조용히** 끝난다 - Q3가 금지한 무음
        열화다. 전파가 옳은 이유는 실행이 시끄럽게 멈추고, Tier 1은
        `CachingProvider`를 거치므로 이미 지불한 호출이 캐시에 남아
        자격증명을 고친 뒤 재실행이 싸기 때문이다 - 날아간 게 아니다.
        """
        out: list[str] = []
        for attempt in range(ctx.samples):
            result = translate_segments(
                [seg],
                provider=ctx.provider_for(attempt),
                source_lang=ctx.signal.source_lang,
                target_lang=ctx.signal.target_lang,
                glossary=ctx.signal.glossary,
                temperature=ctx.temperature,
            )
            # 실패분은 target_text=None으로 들어온다. 조용히 빈 문자열로
            # 세면 "모두 같다"가 되어 점수가 0.0으로 떨어진다.
            for translated in result.segments:
                if translated.target_text:
                    out.append(translated.target_text)
        return out


register(SelfConsistency())
