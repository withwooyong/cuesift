"""배치 분할과 응답 검증 (FR-2.4 경계 보존).

**`parse_translations`가 FR-2.4의 실체다.** 개수와 번호가 어긋나면
InvalidResponseError를 던지고, 그것이 개별 폴백의 방아쇠가 된다.

다만 이 검증에는 한계가 있다 - 개수와 번호가 맞아도 모델이 [10]의 내용을
[11]에 넣는 것은 탐지할 수 없다. 그것은 Tier 0의 길이비 신호가 잡는다.
**FR-2.4는 translate 혼자 지키는 요구사항이 아니라 translate(구조)와
signals(탐지)가 나눠 지킨다** (설계 §7.2).

이 모듈은 순수하다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from cuesift.segment.models import Segment

# 배치가 깨졌을 때 개별 호출로 강등하는 대가가 배치 크기에 비례한다.
# 크면 약한 모델이 개수를 어겨 폴백이 잦아져 오히려 호출이 늘고,
# 작으면 앞뒤 맥락이 매 호출 중복 전송돼 토큰이 낭비된다.
DEFAULT_BATCH_SIZE = 10

# 요구사항정의서 §8.2 `cuesift.yaml` 예시값과 맞춘다. 0이면 FR-2.2가
# 무효가 되고, 크면 배치당 토큰이 선형으로 는다.
DEFAULT_CONTEXT_WINDOW = 3

_FENCE = "```"


@dataclass(frozen=True, slots=True)
class BatchWindow:
    """번역할 배치와 그 앞뒤 맥락. 맥락은 번역 대상이 아니다."""

    batch: tuple[Segment, ...]
    before: tuple[Segment, ...]
    after: tuple[Segment, ...]


class InvalidResponseError(ValueError):
    """응답이 계약을 어겼다. 개별 폴백의 방아쇠다."""


def iter_batches(
    segments: Sequence[Segment],
    *,
    size: int = DEFAULT_BATCH_SIZE,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
) -> Iterator[BatchWindow]:
    """세그먼트를 배치로 자르고 각 배치에 앞뒤 맥락을 붙인다 (FR-2.2).

    인자 검사를 제너레이터 본문이 아니라 여기서 하는 이유는, 제너레이터
    함수의 본문이 **첫 next()까지 실행되지 않기** 때문이다. 본문에 두면
    `iter_batches(segments, size=0)`이 예외 없이 제너레이터를 돌려주고,
    설정(`cuesift.yaml`)의 오타가 호출 지점에서 멀리 떨어진 반복문 안에서
    터진다.
    """
    if size < 1:
        raise ValueError(f"size({size})는 1 이상이어야 한다")
    if context_window < 0:
        raise ValueError(f"context_window({context_window})는 0 이상이어야 한다")
    return _iter_batches(segments, size, context_window)


def _iter_batches(
    segments: Sequence[Segment], size: int, context_window: int
) -> Iterator[BatchWindow]:
    for start in range(0, len(segments), size):
        end = min(start + size, len(segments))
        # max(0, ...)가 없으면 start-context_window가 음수가 되어 슬라이스가
        # 뒤에서부터 센다. 첫 배치(start=0)는 그래도 빈 튜플이 나온다 -
        # 음수 시작이 stop=0보다 뒤라 결과가 비기 때문이다. 실제로 깨지는
        # 곳은 **context_window가 전체 세그먼트 수보다 클 때의 두 번째
        # 이후 배치**이고, 거기서는 앞 맥락이 조용히 잘린다: n=4·size=2·
        # context_window=5에서 [0,1]이어야 할 것이 [1]이 된다.
        before_start = max(0, start - context_window)
        yield BatchWindow(
            batch=tuple(segments[start:end]),
            before=tuple(segments[before_start:start]) if context_window else (),
            after=tuple(segments[end : end + context_window]) if context_window else (),
        )


def _strip_fence(raw: str) -> str:
    """코드 펜스를 벗기고, 펜스 바깥의 산문을 버린다.

    **관대함의 경계는 "모델이 스스로 그은 구분자"까지다.**

    | 응답 형태 | 처리 |
    | --- | --- |
    | 순수 JSON | 그대로 넘긴다 |
    | 펜스로 감싼 JSON | 펜스 줄을 벗긴다 |
    | 산문 + 펜스 블록 (+ 산문) | 첫 펜스 블록의 내용만 남긴다 |
    | 펜스 **없는** 산문 속 JSON | 손대지 않는다 -> InvalidResponseError |

    마지막 줄이 경계다. 펜스가 없으면 JSON의 시작을 첫 `{`로 추측하는
    수밖에 없는데, 산문에 중괄호가 하나라도 있으면 엉뚱한 자리를 자른다.
    그 실패는 예외가 아니라 **잘못된 파싱 성공**으로 나타나 다른
    세그먼트의 번역문이 조용히 섞인다 - 폴백조차 발동하지 않는다.
    반면 펜스는 모델이 명시한 경계라 추측이 아니다.

    산문 머리말을 벗기지 않으면 그 응답은 전부 폴백으로 간다. 머리말은
    모델의 습관이라 매 호출 재현되므로, 폴백이 다시 물어도 같은 머리말이
    돌아온다 - 배치 하나가 N번의 실패한 개별 호출로 바뀔 뿐이다.
    """
    text = raw.strip()
    lines = text.splitlines()
    # 여는 펜스는 ``` 또는 ```json처럼 줄 **머리**에 온다. 줄 중간의 ```는
    # JSON 문자열 안의 내용일 수 있으므로 구분자로 보지 않는다.
    opener = next((i for i, line in enumerate(lines) if line.strip().startswith(_FENCE)), None)
    if opener is None:
        return text
    body = lines[opener + 1 :]
    # 닫는 펜스는 그 줄에 ```만 있는 경우다. 없으면(모델이 응답을 끊은
    # 경우) 남은 전부를 본문으로 본다.
    closer = next((i for i, line in enumerate(body) if line.strip() == _FENCE), None)
    if closer is not None:
        body = body[:closer]
    return "\n".join(body).strip()


def parse_translations(raw: str, expected_ids: Sequence[int]) -> dict[int, str]:
    """응답을 파싱하고 계약을 검사한다.

    빈 문자열 번역은 **여기서 거르지 않는다.** 그것은 배치 폐기 사유가
    아니라 그 세그먼트만의 실패이고(`empty_translation`), 판정은 engine이 한다.
    여기서 거르면 같은 배치에서 성공한 나머지 9개까지 함께 버린다.

    번역문의 개행도 손대지 않는다. 프롬프트가 여러 줄 자막을 두 글자
    `\\n`으로 표기하게 하고 `json.loads`가 그것을 진짜 개행으로 푸는데,
    그 개행이 규격 검사(줄 수·줄당 문자)의 입력이다.
    """
    try:
        parsed = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as e:
        raise InvalidResponseError(f"JSON이 아니다: {e}") from None

    if not isinstance(parsed, dict) or "translations" not in parsed:
        raise InvalidResponseError("최상위에 'translations' 키가 없다")

    items = parsed["translations"]
    if not isinstance(items, list):
        raise InvalidResponseError("'translations'가 배열이 아니다")

    result: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            # 번호 없는 문자열 배열을 위치로 짝지어 주면 안 된다. 순서가
            # 밀린 응답이 개수만 맞으면 통과해 다른 세그먼트에 붙는다.
            raise InvalidResponseError(f"항목이 객체가 아니다: {item!r}")
        item_id = item.get("id")
        # bool은 int의 하위 타입이라 isinstance(True, int)가 참이다.
        # 걸러 내지 않으면 {"id": true}가 1번 세그먼트로 접힌다.
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise InvalidResponseError(f"id가 정수가 아니다: {item_id!r}")
        text = item.get("text")
        if not isinstance(text, str):
            raise InvalidResponseError(f"text가 문자열이 아니다: {text!r}")
        if item_id in result:
            # dict로 접으면 마지막 것이 조용히 이겨 개수 검증을 통과한다.
            raise InvalidResponseError(f"id가 중복됐다: {item_id}")
        result[item_id] = text

    expected = set(expected_ids)
    got = set(result)
    if missing := expected - got:
        raise InvalidResponseError(f"id가 누락됐다: {sorted(missing)}")
    if extra := got - expected:
        raise InvalidResponseError(f"여분의 id가 있다: {sorted(extra)}")

    return result
