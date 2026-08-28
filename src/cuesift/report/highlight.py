"""위험 구간 분할 (FR-7.3 · 설계 D6).

**HTML을 모른다.** 문자열과 구간만 안다 - 그래야 겹침·교차·인접·경계값을
문자열 조립 없이 단언할 수 있다(설계 §6.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cuesift.segment import Span


@dataclass(frozen=True, slots=True)
class Fragment:
    """텍스트 조각 하나와 그 조각을 덮는 신호 이름들.

    `signals`가 비면 평문이고, 하나 이상이면 하이라이트 대상이다.
    이름이 여럿인 것은 그 구간에서 신호가 겹쳤다는 뜻이다.
    """

    text: str
    signals: tuple[str, ...]


def split_spans(text: str, spans: Sequence[tuple[str, Span]]) -> list[Fragment]:
    """겹치는 구간을 경계점으로 쪼개 **평평한** 조각 목록으로 만든다.

    중첩 태그를 쓰지 않는 이유는 구간이 **교차**할 수 있기 때문이다 -
    `A=[0,5)`와 `B=[3,8)`은 중첩으로 유효한 HTML을 만들 수 없다.
    분할은 언제나 형제 조각만 낸다(설계 D6).

    **범위를 벗어나거나 빈 구간은 버린다.** 수집기가 잘못된 오프셋을 내도
    리포트 전체를 잃는 것보다 그 구간만 빠지는 편이 낫다.

    `signals`는 **정렬해서** 담는다 - 같은 입력이 다른 HTML을 내면
    재현성(NFR-3)이 깨진다.
    """
    # `0 <= span.start`를 빼면 음수 오프셋이 경계점에 섞여 `text[-3:0]`이
    # **빈 조각**을 낸다. 빈 `<mark>`는 화면에 아무것도 그리지 않으므로
    # 실물 확인으로는 잡히지 않는다. `Span`은 `end < start`만 막는다.
    valid = [(name, span) for name, span in spans if 0 <= span.start < span.end <= len(text)]

    # 빈 텍스트와 유효 구간 0개는 분기가 필요 없다 - 전자는 경계점이 `{0}`
    # 하나뿐이라 짝이 없어 `[]`가 되고, 후자는 `{0, len}`만 남아 신호 없는
    # 조각 하나가 된다. 분기로 적으면 어떤 테스트로도 구별되지 않는 코드가
    # 생긴다(변이가 반드시 생존하는 자리다).
    points = sorted({0, len(text)} | {p for _, span in valid for p in (span.start, span.end)})

    return [
        Fragment(
            text=text[start:end],
            signals=tuple(
                sorted({name for name, span in valid if span.start <= start and end <= span.end})
            ),
        )
        for start, end in zip(points, points[1:], strict=False)
    ]
