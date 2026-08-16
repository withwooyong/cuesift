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
from typing import Any

from cuesift.segment.models import Segment

# 배치가 깨졌을 때 개별 호출로 강등하는 대가가 배치 크기에 비례한다.
# 크면 약한 모델이 개수를 어겨 폴백이 잦아져 오히려 호출이 늘고,
# 작으면 앞뒤 맥락이 매 호출 중복 전송돼 토큰이 낭비된다.
DEFAULT_BATCH_SIZE = 10

# 요구사항정의서 §8.2 `cuesift.yaml` 예시값과 맞춘다. 0이면 FR-2.2가
# 무효가 되고, 크면 배치당 토큰이 선형으로 는다.
DEFAULT_CONTEXT_WINDOW = 3

_FENCE = "```"

# 한 번 만들어 재사용한다. raw_decode는 상태를 갖지 않는다.
_DECODER = json.JSONDecoder()


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

    ## 계약: `segments`는 트랙의 **연속 구간**이어야 한다

    맥락 윈도우를 **인접 슬라이스**로 잡으므로(`segments[start-cw:start]`),
    비연속 부분집합을 넘기면 **맥락이 조용히 틀린다.** 예외도 경고도 없다.
    실측(12세그먼트 · `size=3` · `context_window=3`):

    | 무엇을 넘겼나 | 세그먼트 5의 맥락 |
    | --- | --- |
    | 전체 트랙 | 앞 `[0][1][2]` · 대상 `[3][4][5]` · 뒤 `[6][7][8]` |
    | 부분집합 `[2][5][9]` | **맥락 없음** · 대상 `[2][5][9]` |

    세그먼트 6의 유저 프롬프트 SHA-256도 전체 실행과 `track[6:]`만 넘긴
    재개 실행에서 **서로 다르다**(`27077083` vs `8cb1100c`).

    **다음 두 태스크가 정면으로 걸린다.**

    - **WP7b 재개(FR-2.7)**: 중단 지점부터 넘기면 같은 세그먼트에 다른
      프롬프트가 나가 NFR-3 재현성과 캐시 키가 동시에 깨진다. 설계 §5.2가
      캐시 키를 `(원문, 맥락 원문, 용어집, 모델, 설정)`으로 이미 적었다.
    - **WP8 Tier 1(FR-4.3, 적용 상한 25%)**: 부분집합을 넘기면 이웃이
      맥락이 아니라 **번역 대상**이 되어, 자가일관성이 재는 것이 모델
      분산이 아니라 **프롬프트 차이**가 된다. Q4 판정이 이 측정에 걸려 있다.

    **처방**: 전체 트랙을 넘긴 뒤 결과를 거르거나, 맥락을 직접 지정해
    `build_messages`를 부른다.

    ## 인자 검사가 여기 있는 이유

    제너레이터 함수의 본문은 **첫 `next()`까지 실행되지 않는다.** 본문에 두면
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


def _fence_blocks(text: str) -> list[str]:
    """펜스로 감싼 블록의 내용을 **전부** 순서대로 모은다.

    여는 줄도 닫는 줄도 "줄 **머리**가 ```"로 판정한다. 줄 중간의 ```는
    JSON 문자열의 내용일 수 있어 구분자로 보지 않는다.

    닫는 줄을 `== "```"`로 좁히면 CommonMark가 허용하는 **4백틱 펜스**와
    닫는 줄이 ```json인 응답에서 블록 경계를 놓쳐 **인접한 두 블록이 하나로
    합쳐진다.** 합쳐진 텍스트는 앞 블록의 JSON으로 시작하므로 뒤 블록은
    잡담으로 버려지고, 계약을 만족하는 후보가 둘인데 하나로 보여 모호성이
    거부가 아니라 **임의 채택**이 된다 - `parse_translations`가 없애려는
    바로 그 실패 양식이다. (블록이 하나뿐이면 남은 백틱을 raw_decode가
    잡담으로 넘기므로 좁혀도 결과가 같다. 그래서 블록 **둘**로 시험해야
    이 차이가 드러난다.)

    반대로 넓혀도 오인이 없는 것은, 본문 줄이 ```로 시작하려면 JSON 문자열
    안에 생개행이 있어야 하는데 그것은 이미 유효한 JSON이 아니기 때문이다.
    """
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith(_FENCE):
            i += 1
            continue
        i += 1
        body: list[str] = []
        while i < len(lines) and not lines[i].strip().startswith(_FENCE):
            body.append(lines[i])
            i += 1
        blocks.append("\n".join(body).strip())
        i += 1  # 닫는 펜스를 건너뛴다. 없으면(모델이 응답을 끊은 경우) 루프가 끝난다
    return blocks


def _json_values(text: str) -> list[Any]:
    """텍스트 **머리**에서 시작하는 최상위 JSON 값들을 순서대로 뽑는다.

    `raw_decode`는 위치 0에 고정한다. 산문 안을 뒤져 첫 `{`를 찾지 않는
    이유가 이것이다 - 산문에 중괄호가 하나라도 있으면 엉뚱한 자리를 자르고,
    그 실패는 예외가 아니라 **잘못된 파싱 성공**으로 나타나 다른 세그먼트의
    번역문이 조용히 섞인다. 머리에 고정하면 추측이 없다.

    값 하나를 읽은 뒤 남은 텍스트에서 다시 시도하는 것은, 이어 붙은 JSON을
    **후보로 드러내기 위해서다.** 첫 값만 읽고 말면 `{...}{...}` 응답에서
    뒤의 것이 조용히 사라진다 - 이 모듈이 없애려는 바로 그 실패 양식이다.
    뒤가 JSON이 아니면(모델의 잡담) 거기서 멈추고 무시한다.

    RecursionError를 함께 잡지 않으면 퇴화 응답(중첩 2만 겹)이 폴백 경로
    **밖으로** 나가 실행 전체가 트레이스백으로 끝난다. `struct.degeneration`
    (`signals/structural.py`)을 1급 신호로 두는 프로젝트에서 퇴화 응답은
    가상의 입력이 아니다.
    """
    values: list[Any] = []
    rest = text.strip()
    while rest:
        try:
            value, end = _DECODER.raw_decode(rest)
        except (json.JSONDecodeError, RecursionError):
            break
        values.append(value)
        rest = rest[end:].lstrip()
    return values


def _candidate_payloads(raw: str) -> list[Any]:
    """응답에서 "번역 결과일 수 있는 것"을 전부 후보로 만든다.

    후보는 두 갈래다 - **통째 텍스트의 머리에서 시작하는 JSON**과
    **각 펜스 블록**. 어느 하나를 미리 고르지 않는 것이 요점이다.
    """
    text = raw.strip()
    values = _json_values(text)
    for block in _fence_blocks(text):
        values.extend(_json_values(block))
    return values


def parse_translations(raw: str, expected_ids: Sequence[int]) -> dict[int, str]:
    """응답을 파싱하고 계약을 검사한다.

    **후보를 여럿 만들고 계약을 만족하는 것이 정확히 하나일 때만 채택한다.**

    | 응답 형태 | 판정 |
    | --- | --- |
    | 순수 JSON (뒤에 잡담이 붙어도 된다) | 채택 |
    | 펜스로 감싼 JSON (```·````·```json 무관) | 채택 |
    | 산문 머리말·꼬리말 + 펜스 블록 | 채택 |
    | 정상 JSON + 무관한 펜스 블록(예: ```bash) | 채택 - 계약을 만족하는 후보가 하나뿐 |
    | 계약을 만족하는 후보가 **둘 이상** | 거부 |
    | 펜스 없는 산문 **속** JSON (머리에서 시작하지 않는다) | 거부 |
    | 후보 0개 | 거부 |

    둘 이상을 거부하는 것이 이 함수의 성격을 정한다. 초안을 낸 뒤 수정본을
    내거나 형식을 먼저 복창하는 모델에서 계약을 만족하는 블록이 둘 나온다.
    앞의 것을 택하면 **예외도 폴백도 없이** 초안이 채택되고 진짜 답이
    버려진다. **모델이 만든 모호성은 추측해서 풀지 않고 폴백으로 보낸다** -
    산문 안에서 첫 `{`를 찾지 않기로 한 것과 같은 원칙이고, 폴백은 비싸지만
    관측 가능한 반면 잘못 고른 후보는 조용하다.

    빈 문자열 번역은 **여기서 거르지 않는다.** 그것은 배치 폐기 사유가
    아니라 그 세그먼트만의 실패이고(`empty_translation`), 판정은 engine이 한다.
    여기서 거르면 같은 배치에서 성공한 나머지 9개까지 함께 버린다.

    번역문의 개행도 손대지 않는다. 프롬프트가 여러 줄 자막을 두 글자
    `\\n`으로 표기하게 하고 `json.loads`가 그것을 진짜 개행으로 푸는데,
    그 개행이 규격 검사(줄 수·줄당 문자)의 입력이다.
    """
    accepted: list[dict[int, str]] = []
    first_error: InvalidResponseError | None = None
    for payload in _candidate_payloads(raw):
        try:
            accepted.append(_check_contract(payload, expected_ids))
        except InvalidResponseError as e:
            if first_error is None:
                # 후보가 하나뿐인 흔한 경우에 "왜 거부됐는가"를 보존한다.
                # 뭉뚱그리면 폴백 로그가 원인을 잃는다.
                first_error = e

    if len(accepted) > 1:
        raise InvalidResponseError(
            f"계약을 만족하는 JSON이 {len(accepted)}개다. 어느 것이 답인지 추측하지 않는다"
        )
    if accepted:
        return accepted[0]
    if first_error is not None:
        raise first_error
    raise InvalidResponseError("JSON을 찾지 못했다 (펜스 블록과 텍스트 머리 모두)")


def _check_contract(parsed: Any, expected_ids: Sequence[int]) -> dict[int, str]:
    """후보 하나가 응답 계약을 지키는지 본다 (FR-2.4)."""
    # 두 조건의 **순서가 방어의 전부다.** 뒤집으면 dict가 아닌 최상위값에
    # 먼저 `in`을 걸어 None·숫자·불리언에서 TypeError가 나고, 그 예외는
    # InvalidResponseError가 아니라서 폴백이 받지 못한다. 문자열은 `in`이
    # 부분 문자열 검사로 성립해 버려 이 사고를 가려 준다.
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
