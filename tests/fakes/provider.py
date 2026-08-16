"""가짜 프로바이더 - 네트워크 없이 engine을 검증한다 (NFR-7).

`src/`가 아니라 `tests/`에 있는 이유는 배포물에 테스트 더블을 섞지 않기
위해서다. WP8(Tier 1 자가일관성)도 같은 가짜를 쓴다.

**`complete`의 시그니처는 `Provider` 프로토콜과 글자 그대로 같아야 한다.**
`max_tokens`에 기본값을 붙이는 것까지 이탈이다 - 프로토콜에는 없다.
`Provider`가 `@runtime_checkable`이 아니고 CI에 타입 검사기도 없어서,
어긋난 가짜도 엔진의 키워드 호출에서는 정상 동작해 조용히 통과한다.
`test_translate_engine.py`의 `inspect.signature` 단언이 이 저장소에서
그 이탈을 잡는 유일한 수단이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from cuesift.translate.provider import ChatMessage, Completion, ProviderError, TokenUsage


class ScriptedProvider:
    """미리 정한 응답을 순서대로 돌려준다.

    응답 자리에 예외 인스턴스를 넣으면 그것을 던진다. 재시도·폴백 경로를
    시나리오로 적을 수 있게 하는 것이 목적이다.
    """

    name = "scripted"

    def __init__(self, responses: Sequence[str | ProviderError]) -> None:
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError(f"대본이 소진됐는데 {len(self.calls)}번째 호출이 왔다")
        item = self._responses.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return _completion(item)


class EchoProvider:
    """요청받은 id를 그대로 채워 정상 JSON을 낸다.

    `transform`으로 번역문을 바꿀 수 있고, `drop_last`로 개수 불일치를,
    `garbage`로 파싱 실패를 만들 수 있다.
    """

    name = "echo"

    def __init__(
        self,
        *,
        transform: Callable[[str], str] = lambda s: f"EN:{s}",
        drop_last: bool = False,
        garbage: bool = False,
        fail_batches_of_size: int | None = None,
    ) -> None:
        self._transform = transform
        self._drop_last = drop_last
        self._garbage = garbage
        self._fail_batches_of_size = fail_batches_of_size
        self.calls: list[list[ChatMessage]] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        self.calls.append(list(messages))
        pairs = _parse_targets(messages[-1].content)

        if self._garbage:
            return _completion("죄송합니다, 번역할 수 없습니다.")
        # 배치일 때만 깨뜨리고 개별 폴백은 성공시키기 위한 장치다.
        if self._fail_batches_of_size is not None and len(pairs) >= self._fail_batches_of_size:
            return _completion("죄송합니다, 번역할 수 없습니다.")

        items = [{"id": i, "text": self._transform(t)} for i, t in pairs]
        if self._drop_last and len(items) > 1:
            items = items[:-1]
        return _completion(json.dumps({"translations": items}, ensure_ascii=False))


def _completion(text: str) -> Completion:
    return Completion(text=text, usage=TokenUsage(prompt_tokens=1, completion_tokens=1, calls=1))


def _parse_targets(user_content: str) -> list[tuple[int, str]]:
    """유저 메시지에서 '## 번역 대상' 절의 [id] 텍스트만 뽑는다.

    **두 글자 `\\n`을 진짜 개행으로 되돌린다.** 프롬프트(`prompt.py`)가 자막
    안의 줄바꿈을 두 글자로 이스케이프해 내보내므로, 되읽은 그대로 쓰면
    `json.dumps`가 역슬래시를 한 번 더 escape해 엔진의 `json.loads`가 두
    글자를 그대로 돌려준다. 진짜 모델은 JSON 문자열 안에 두 글자 `\\n`을
    쓰고 그것이 **진짜 개행**으로 풀리므로, 되돌리지 않으면 이 가짜가
    여러 줄 자막을 한 줄로 만들어 낸다 - 규격 검사(줄 수·줄당 문자)를
    쓰는 뒷단(WP7b) 테스트가 실제와 다른 것을 보게 된다.

    한 줄 = 세그먼트 하나라는 전제도 여기 걸려 있다. 이스케이프가 없던
    때에는 둘째 줄이 번호 없이 나와 이 파서가 조용히 버렸고, 둘째 줄이
    `[음악]`처럼 `[`로 시작하면 `int("음악")`에서 죽었다.
    """
    lines = user_content.splitlines()
    out: list[tuple[int, str]] = []
    in_target = False
    for line in lines:
        if line.startswith("## "):
            in_target = line.startswith("## 번역 대상")
            continue
        if in_target and line.startswith("["):
            head, _, text = line.partition("] ")
            out.append((int(head[1:]), _unescape_newlines(text)))
    return out


def _unescape_newlines(text: str) -> str:
    """`prompt.py`의 `_escape_newlines`를 되돌린다. 둘은 짝이다."""
    return text.replace("\\n", "\n")
