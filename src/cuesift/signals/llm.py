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

    **길이 편향이 있다 - 지금은 완화하지 않고 기록만 한다(Ruling P10).**
    문자 단위 `similarity`는 편집 위치를 세그먼트 길이로 나누지 않으므로,
    같은 정도의 변이(동사 하나 교체)가 짧은 문장에서는 점수를 크게,
    긴 문장에서는 작게 만든다. 아래는 `samples=2`로 재현 가능한 실측이다
    - 점수는 `SelfConsistency().collect_tier1()`이 실제로 내는 값이고,
    표본 문장을 전부 적어 **누구나 재계산해 일치를 확인할 수 있다**:

    | 언어 | 길이(a/b) | score |
    | --- | --- | --- |
    | en 짧음 | 7/7 | 0.4286 |
    | en 긺 | 85/85 | 0.0353 |
    | ja 짧음 | 4/5 | 0.3333 |
    | ja 긺 | 30/30 | 0.0667 |

    표본 문장 (a → b, 동사 하나만 교체):

    - en 짧음: "He came" → "He left"
    - en 긺: "She said that she would arrive at the station
      before the train departed for the coast" → 위에서
      arrive를 travel로 교체
    - ja 짧음: "彼は来た" → "彼は行った"
    - ja 긺: "彼女は明日の朝に駅まで歩いて向かうつもりだと友人に
      話していた" → 위에서 歩いて를 走って로 교체

    en 12.1배, ja 5.0배 차이다(둘 다 `similarity()`와 `collect_tier1()`
    양쪽 경로로 재확인 - 값이 정확히 일치한다). **배수는 예문에 크게
    의존한다** - 독립 측정 4회가 en 8~12배, ja 5~7배로 흩어졌다. 표의
    값은 위에 명시한 예문에 대한 것이고, 재현되는 것은 편향의 방향
    (짧을수록 점수가 높다)이지 배수의 크기가 아니다. Recall@Budget은 순위로
    정해지므로, 길이가 점수를 지배하면 후보 큐가 "어려운 구간"이 아니라
    "짧은 구간"으로 채워질 위험이 있다. 길이 정규화 상수는
    요구사항정의서 §11 R8(출처 없는 수치 금지)에 걸리고, 판정은
    벤치마크의 일인데 "벤치마크에 Tier 1 태우기"는 이 작업 패키지의
    명시적 비범위다 - 그래서 코드는 바꾸지 않는다. 이 실측표는 §12
    Q4(유사도 측정 수단 미결)의 근거에 설계 §3.2의 7쌍 실측과 같은
    방식으로 더해진다.
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

        # combinations는 samples의 등장 순서를 유지한 채 인덱스쌍을 사전식
        # 순서로 낸다 - 3개면 (0,1)·(0,2)·(1,2). detail["pairwise"]를
        # review.json에서 되읽을 때 이 순서를 알아야 몇 번째 재번역들이
        # 흩어졌는지 되짚을 수 있다.
        pairwise = [similarity(a, b) for a, b in combinations(samples, 2)]
        # **평균이다** - 최솟값(가장 가까운 한 쌍)도 최댓값(가장 흩어진 한
        # 쌍)도 아니다. "가장 흔들린 한 쌍"과 "평균적 흔들림"은 트리아지
        # 의미가 다르고, 이 신호가 재는 것은 후자다(설계 §6.1).
        score = 1.0 - sum(pairwise) / len(pairwise)

        return Signal(
            name=self.name,
            tier=1,
            # 지금은 이 clamp가 발동하지 않는다 - `similarity`의 치역이
            # [0,1]이라 score도 항상 [0,1]이다(무작위 20만 회 실측 0건).
            # 남겨 두는 이유는 §12 Q4가 열려 있어 유사도 측정 수단이
            # 코사인 유사도([-1,1]) 같은 것으로 바뀔 수 있기 때문이다 -
            # 그때는 이 줄이 실제로 값을 잘라 후보 전원을 최고 위험으로
            # 만든다. `tests/test_similarity.py`의 [0,1] 범위 테스트가
            # 교체 시점에 먼저 걸린다.
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
                # len(samples)는 성공분 개수라 "samples=2로 설정했다"와
                # "samples=3인데 1개 실패했다"를 detail만 보고 구분할 수
                # 없다(FR-6.4). 요청값을 따로 남긴다.
                "requested_samples": ctx.samples,
            },
        )

    def _retranslate(self, seg: Segment, ctx: Tier1Context) -> list[str]:
        """N회 재번역해 성공분만 낸다.

        `translate_segments`를 그대로 재사용하지만, 재사용되는 것은
        **재시도·실패 분류뿐이다.** `[seg]` 하나만 넘기므로 배치는 항상
        크기 1이고, 프롬프트에 컨텍스트 윈도우 절("## 맥락")도 실리지
        않는다 - 그 절은 `iter_batches`가 여러 세그먼트를 함께 볼 때만
        만든다. 실측 프롬프트에는 "## 번역 대상" 절만 있다.

        **비용은 세그먼트당 `ctx.samples`회다.** 후보 N개면 총 N ×
        samples회 호출이고, 배치가 개입하지 않으므로 여러 세그먼트를 한
        호출에 묶을 수 없다. 실측(세그먼트 10개·samples=3): 이 구현은
        30회 호출·10320자를 쓰는데, 세그먼트를 한 호출에 묶었다면
        3회·1599자였다 - 호출 10배, 프롬프트 6.5배(시스템 프롬프트가
        매 호출 반복되므로 실물 차이는 더 크다). 이것은 설계 §4.1이
        `collect_tier1(seg, ctx)` 세그먼트 단위 프로토콜을 확정한 결과다
        - 설계 §6.3이 적었던 "배치를 재사용한다"는 그와 모순돼 정정했다
        (§12 문서 정정 표). 지금 프로토콜을 바꾸지 않는 이유는
        `Tier1Collector`에 배치 메서드를 추가하는 것이 Task 2의 파일과
        Task 6의 오케스트레이션을 함께 건드리는 설계 변경이고, 이 작업
        패키지는 라이브러리 계층까지로 범위가 정해져 있기 때문이다.

        **토큰 사용량이 유실된다.** `translate_segments`가 채워 주는
        `result.usage`를 여기서 버린다 - NFR-2·FR-7.4가 요구하는 비용
        숫자에서 가장 비싼 계층(Tier 1)만 빠진다는 뜻이다. 지금 고치지
        않는 이유는 `collect_tier1`의 반환형(`Signal | None`)에 사용량을
        실어 나를 통로가 없고, 누적은 리포트 계층(WP8b)의 일이기
        때문이다. `engine.py`가 "실패한 호출의 토큰은 세지 않는다"를
        남긴 것과 같은 방식으로 여기 한계를 적어 둔다.

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
        열화다. 전파가 옳은 이유는 실행이 시끄럽게 멈추고, **캐시가 켜진
        실행에서는** Tier 1이 `CachingProvider`를 거치므로 이미 지불한
        호출이 캐시에 남아 자격증명을 고친 뒤 재실행이 싸기 때문이다 -
        날아간 게 아니다. 설계 §8의 `provider_for`는 `cache_dir`이
        `None`이면 캐시 없이 그대로 통과하므로, 그런 실행에서는 이 이점이
        적용되지 않는다 - 그래도 전파가 옳은 것은 무음 열화를 막는 쪽이
        우선이기 때문이다.
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
