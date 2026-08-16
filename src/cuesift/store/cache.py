"""LLM 응답 캐시 (요구사항정의서 NFR-3 재현성 · §7.1 `store/`).

**캐시는 최적화이지 정확성의 근거가 아니다.** 그래서 읽기가 조금이라도
미심쩍으면 예외가 아니라 **미스**로 떨어뜨린다 - 손상된 파일 하나가 실행
전체를 죽이면 안 되고, 못 믿을 때는 다시 부르면 된다 (설계 §3.3).

**키를 파일명에 넣고 재료를 파일 안에 또 쓴다.** 파일명만 믿으면 손상된
캐시가 조용히 번역문으로 둔갑한다 - 이 저장소가 1급으로 금지한
"검사하지 않고 통과하는 게이트"가 정확히 그 형태다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cuesift.translate.provider import ChatMessage, Completion, TokenUsage

# 키 재료를 잇는 구분자. **실제 제어문자 U+001F(UNIT SEPARATOR)여야 한다.**
# 자막이나 프롬프트에 나타날 수 있는 문자("|"·":")를 쓰면 재료의 경계가
# 모호해진다 - model="a|b"와 (model="a", base_url="b")가 같은 키를 만든다.
_SEP = "\x1f"


@dataclass(frozen=True, slots=True)
class CacheRequest:
    """캐시 조회·저장의 재료 한 묶음 (설계 §3.1).

    **키와 대조 재료가 같은 곳에서 나오는 것이 요점이다.** 둘을 따로
    계산하면 한쪽만 고쳐졌을 때 저장한 것을 자기가 못 읽는다.
    """

    identity: str
    temperature: float
    max_tokens: int | None
    messages: tuple[ChatMessage, ...]

    @property
    def messages_sha(self) -> str:
        return _sha256(_messages_material(self.messages))

    @property
    def key(self) -> str:
        """설계 §3.1의 `(원문, 맥락 원문, 용어집, 모델, 설정)`을 전부 덮는다.

        메시지를 **재조립하지 않고 그대로** 넣는 것이 핵심이다. 재조립하면
        프롬프트 조립 규칙이 바뀔 때 키가 따라가지 못한다 - 실제로
        2026-08-17에 시스템 프롬프트가 바뀌었고(정수 id 계약), 키를 손으로
        관리했다면 **바뀐 프롬프트가 옛 캐시에 히트했을 것이다.**

        `float(...)`로 정규화하는 이유는 `0`(int)과 `0.0`(float)이 같은
        온도인데 `repr`이 다르기 때문이다. 정규화가 없으면 호출부의 타입
        차이 하나로 캐시가 전량 미스가 된다.
        """
        material = _SEP.join(
            (
                self.identity,
                repr(float(self.temperature)),
                "none" if self.max_tokens is None else str(self.max_tokens),
                self.messages_sha,
            )
        )
        return _sha256(material)


def load(cache_dir: Path, request: CacheRequest) -> Completion | None:
    """캐시에서 읽는다. 조금이라도 미심쩍으면 `None`이다.

    **잡는 예외가 넓은 것이 의도다.** `json.JSONDecodeError`와
    `UnicodeDecodeError`는 둘 다 `ValueError`의 하위이고, `TokenUsage`의
    음수 검사도 `ValueError`를 낸다. 좁히면 손상된 파일의 어느 한 형태가
    호출부로 새어 나가는데, 그 자리는 번역 루프 한가운데다.
    """
    path = cache_dir / f"{request.key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not _matches(raw, request):
        return None

    try:
        usage = raw["usage"]
        return Completion(
            text=raw["text"],
            usage=TokenUsage(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                calls=usage["calls"],
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def store(cache_dir: Path, request: CacheRequest, completion: Completion) -> None:
    """캐시에 쓴다. **`OSError`를 삼키지 않는다** - 호출자가 경고를 낸다.

    임시 파일에 쓰고 `os.replace`로 옮기는 이유는 중간에 죽어도 반쪽짜리
    JSON이 남지 않게 하기 위해서다. `os.replace`는 Windows에서도 원자적이다.

    임시 파일명에 pid를 넣는 것은 같은 키를 두 프로세스가 동시에 쓸 때
    서로의 임시 파일을 덮지 않게 하기 위해서다. 최종 파일에 대한 경쟁은
    마지막 쓰기가 이기고, 온도 0.0에서는 내용이 같으므로 무해하다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "identity": request.identity,
        "temperature": float(request.temperature),
        "max_tokens": request.max_tokens,
        "messages_sha": request.messages_sha,
        "text": completion.text,
        "usage": {
            "prompt_tokens": completion.usage.prompt_tokens,
            "completion_tokens": completion.usage.completion_tokens,
            "calls": completion.usage.calls,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    tmp = cache_dir / f"{request.key}.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache_dir / f"{request.key}.json")


def _matches(raw: object, request: CacheRequest) -> bool:
    """파일 안의 재료가 현재 요청과 같은지 본다.

    **키가 이미 이 넷을 덮으므로 정상 경로에서는 항상 참이다.** 이 검사가
    잡는 것은 해시 충돌·파일 손상·수동 편집이다. 그래서 어긋남을 예외가
    아니라 미스로 다룬다 - 캐시를 못 믿을 뿐 실행이 틀린 것은 아니다.
    """
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("identity") == request.identity
        and raw.get("temperature") == float(request.temperature)
        and raw.get("max_tokens") == request.max_tokens
        and raw.get("messages_sha") == request.messages_sha
    )


def _messages_material(messages: Sequence[ChatMessage]) -> str:
    """역할과 내용 사이에도 구분자를 넣는다.

    `f"{role}:{content}"`로 이으면 `("system", "지시")` 하나와
    `("system지시", "")`가 같은 문자열을 낸다. 실제로는 안 나오는 조합이지만,
    **키의 단사성은 입력 분포가 아니라 구조로 보장해야 한다.**
    """
    return _SEP.join(f"{m.role}{_SEP}{m.content}" for m in messages)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
