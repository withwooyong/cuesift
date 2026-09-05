"""Tier 1 신호 — 역번역 유사도 (FR-4.2 · 설계 §5).

**Tier 0가 원리적으로 못 잡는 것을 노린다.** 문법적으로 완벽한 문장의
의미가 뒤집혔는지는 결정론적 신호로 판단할 수 없다 - 2026-09-04 실측에서
`negation` Recall이 예산 10%에 1.41%로 무작위 기준선(10.28%)보다 낮았다.

**원리적 상한이 실측돼 있다.** 역번역이 제거된 부정을 문맥으로 되살리는
비율이 en 21.8% · ja 17.9%이고, 그 부류에서는 오류 문장과 정상 문장의
유사도 차이가 사실상 0이다(en -0.005 · ja +0.011). **점수 스케일을 어떻게
바꿔도 그 20%는 잡히지 않는다** - 이 신호의 Recall 목표는 80% 언저리가
상한이다.
"""

from __future__ import annotations

from dataclasses import replace

from cuesift.embed import cosine
from cuesift.segment import Segment, Signal
from cuesift.signals.base import Tier1Context, register
from cuesift.translate import translate_segments

# **0.0이 아니면 무엇이 깨지는가.** 온도를 올리면 같은 오류가 실행마다 다른
# 역번역문을 받아 점수가 흔들리고 NFR-3(재현성)이 성립하지 않는다.
# `Tier1Context.temperature`를 쓰지 않는 이유도 이것이다 - 그 필드는
# 자가일관성 전용이라 `__post_init__`이 0보다 클 것을 강제한다.
_BACKTRANSLATION_TEMPERATURE = 0.0

# 역번역은 시도를 가르지 않는다. 캐시 격리는 attempt가 아니라 **번역 방향**이
# 만든다 - 정방향은 ko->en, 역번역은 en->ko라 messages_sha가 다르다 (설계 §6).
_BACKTRANSLATION_ATTEMPT = 0


class BackTranslation:
    """FR-4.2 — 번역문을 원문 언어로 되돌려 원문과의 의미 유사도를 잰다."""

    name = "llm.backtranslation"
    tier = 1

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        # 번역 실패분은 검수 대상이 아니라 재실행 대상이다.
        if not seg.target_text:
            return None
        if ctx.embedder is None:
            # **None을 내면 무음 열화다** (Q3 · 설계 D6). 신호가 전 구간
            # 0건으로 끝나고 그것이 "판정했고 안전하다"로 읽힌다.
            raise ValueError(
                f"{self.name}은 Tier1Context.embedder를 요구한다. "
                "CLI는 --embed-model로, 라이브러리 호출자는 인자로 배선하라"
            )

        back = self._backtranslate(seg, ctx)
        # 역번역이 실패했거나 빈 문자열이면 판정 불가다. 빈 문자열의
        # 임베딩은 영벡터가 될 수 있고 `cosine`이 거기서 예외를 낸다.
        if not back:
            return None

        vector_source, vector_back = ctx.embedder.embed([seg.source_text, back])
        similarity = cosine(vector_source, vector_back)

        return Signal(
            name=self.name,
            tier=1,
            # **이 clamp가 이번에 처음으로 실제 값을 자른다.** 코사인의
            # 치역이 [-1, 1]이라 원문과 역번역이 의미상 반대인 극단에서
            # `1.0 - similarity`가 2.0까지 간다. 문자 단위 similarity는
            # [0, 1]이라 clamp가 발동한 적이 없었다.
            score=min(1.0, max(0.0, 1.0 - similarity)),
            # hard fail로 두지 않는다. 의미 판단은 결정론적이지 않고,
            # hard fail 오탐은 실제 검수 비율을 부풀려 Recall@Budget 지표
            # 자체를 파괴한다 (FR-6.2).
            hard_fail=False,
            detail={
                # FR-6.4 - review.json이 "왜 선별되었는지"를 이것으로 쓴다.
                # 역번역문 자체를 싣는 이유는 검수자가 점수만 보고는
                # 판정을 재현할 수 없기 때문이다.
                "back_translation": back,
                "cosine": similarity,
                "temperature": _BACKTRANSLATION_TEMPERATURE,
            },
        )

    def _backtranslate(self, seg: Segment, ctx: Tier1Context) -> str | None:
        """번역문을 원문 언어로 되돌린다 (설계 §5.1).

        **`translate_segments`를 방향만 뒤집어 재사용한다.** 재사용되는 것은
        재시도와 실패 분류다 - `RetryableProviderError`는 이미 삼켜져
        `target_text=None`으로 오고, `FatalProviderError`(401 등)는 일부러
        전파된다. 여기서 포괄 `except`로 둘을 함께 삼키면 401이 이 신호를
        전 구간 0건으로 조용히 만든다.

        **용어집을 넘기지 않는다** (설계 D2). 용어집이 원문 어휘를 강제하면
        오류 문장의 역번역도 원문에 가까워져 유사도 격차가 줄어든다.

        **`index=0`으로 재번호한다.** 약한 모델이 항목 하나짜리 요청에서
        프롬프트 예시의 `{"id": 0}`을 그대로 베끼는 것이 실측돼 있고
        (`signals/llm.py`의 Ruling P13), 그러면 `parse_translations`가
        "id가 누락됐다"로 거부한다.
        """
        local_seg = replace(seg, index=0, source_text=seg.target_text, target_text=None)
        result = translate_segments(
            [local_seg],
            provider=ctx.provider_for(_BACKTRANSLATION_ATTEMPT),
            # **방향이 뒤집힌다.** 이것이 캐시 격리의 근거이기도 하다.
            source_lang=ctx.signal.target_lang,
            target_lang=ctx.signal.source_lang,
            glossary=None,
            temperature=_BACKTRANSLATION_TEMPERATURE,
        )
        for translated in result.segments:
            if translated.target_text:
                return translated.target_text
        return None


register(BackTranslation())
