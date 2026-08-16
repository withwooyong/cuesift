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

# `key`의 최상위 네 필드(identity·temperature·max_tokens·messages_sha)를
# 잇는 구분자. **여기서만 안전하다.** 네 필드는 **개수가 고정**이라
# `_messages_material`이 겪었던 "요소 개수가 늘거나 줄어 경계가 옮겨가는"
# 재분할 모호성 자체가 성립하지 않는다 - 남는 유일한 경로는 필드 값
# 자체에 이 문자가 박히는 것인데, temperature(숫자의 repr)·max_tokens
# (숫자 또는 "none")·messages_sha(64자리 hex)는 구조상 이 문자를 담을 수
# 없고, identity는 CLI가 (base_url, model)로 조립하는 값이라 자막 원문
# 같은 임의 텍스트가 여기로 들어올 경로가 없다. `content`는 자막 원문이라
# 사정이 다르므로 `_messages_material`은 이 구분자를 쓰지 않는다.
_SEP = "\x1f"

# `_matches`에서 "필드가 없다"를 "값이 None이다"와 구별하려고 쓰는 전용
# 센티넬. `.get(key, None)`을 쓰면 `max_tokens`처럼 유효값이 None인
# 필드에서 "필드 자체가 빠짐"과 "None으로 채워짐"이 같은 결과로 뭉개진다.
_MISSING = object()


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

    **`text`·토큰 세 값의 타입도 여기서 본다.** `raw["text"]`가 문자열이
    아니면 (예: 수동 편집으로 `12345`가 들어오면) `Completion(text=12345)`가
    그대로 나가고, 그 값이 하류 JSON 파서에 들어가면 `TypeError`가 난다.
    `TypeError`는 `ProviderError`의 자손이 아니라 번역 루프 밖으로 샌다 -
    이 모듈이 막겠다고 선언한 실패 모드와 정확히 같은 형태다.
    """
    path = cache_dir / f"{request.key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not _matches(raw, request):
        return None

    try:
        text = raw["text"]
        usage = raw["usage"]
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
        calls = usage["calls"]
    except (KeyError, TypeError):
        return None

    # `bool`은 `int`의 하위형이라 `isinstance(x, int)`만으로는 `calls: true`가
    # 그대로 통과한다 - 두 조건을 함께 걸어야 진짜 정수만 남는다.
    if not isinstance(text, str):
        return None
    if any(
        not isinstance(v, int) or isinstance(v, bool)
        for v in (prompt_tokens, completion_tokens, calls)
    ):
        return None

    try:
        return Completion(
            text=text,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                calls=calls,
            ),
        )
    except ValueError:
        return None


def store(cache_dir: Path, request: CacheRequest, completion: Completion) -> None:
    """캐시에 쓴다. **`OSError`를 삼키지 않는다** - 호출자가 경고를 낸다.

    임시 파일에 쓰고 `os.replace`로 옮기는 이유는 중간에 죽어도 반쪽짜리
    JSON이 남지 않게 하기 위해서다. `os.replace`는 Windows에서도 원자적이다.

    임시 파일명에 pid를 넣는 것은 같은 키를 두 프로세스가 동시에 쓸 때
    서로의 임시 파일을 덮지 않게 하기 위해서다. 최종 파일에 대한 경쟁은
    마지막 쓰기가 이기고, 온도 0.0에서는 내용이 같으므로 무해하다.

    **`OSError`가 나면 임시 파일을 지우고 나서 다시 던진다.** 지우지
    않으면 디스크가 차거나 권한이 없어 `os.replace`가 실패할 때마다
    `<key>.json.<pid>.tmp`가 그대로 남는다 - pid가 매 실행 달라지므로
    잔해가 실행마다 누적돼 캐시 디렉터리가 쓰레기로 찬다(실측: 재현됨).
    `except`로만 잡고 `finally`를 쓰지 않는 이유는, 성공 경로에서는
    `os.replace`가 이미 tmp를 최종 경로로 옮겨 그 자리에 파일이 없으므로
    지울 것이 없어서다 - `finally`에 넣으면 성공 뒤에도 `unlink`를
    호출하게 되어 `missing_ok=True`로 무해화해야 하는 불필요한 분기가
    생긴다.
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
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cache_dir / f"{request.key}.json")
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _matches(raw: object, request: CacheRequest) -> bool:
    """파일 안의 재료가 현재 요청과 같은지 본다.

    **키가 이미 이 넷을 덮으므로 정상 경로에서는 항상 참이다.** 이 검사가
    잡는 것은 해시 충돌·파일 손상·수동 편집이다. 그래서 어긋남을 예외가
    아니라 미스로 다룬다 - 캐시를 못 믿을 뿐 실행이 틀린 것은 아니다.

    **`.get(key)`가 아니라 `.get(key, _MISSING)`을 쓴다.** `max_tokens`는
    유효값 자체가 `None`이라, 필드가 통째로 빠진 파일에서 `.get()`의
    기본값도 `None`이면 "필드 없음"과 "값이 None"이 구별되지 않는다 -
    실측: `max_tokens` 필드를 지운 파일이 `request.max_tokens is None`인
    요청과 우연히 일치해 통과했다. 네 필드 모두 같은 위험이라 전부에
    적용한다.
    """
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("identity", _MISSING) == request.identity
        and raw.get("temperature", _MISSING) == float(request.temperature)
        and raw.get("max_tokens", _MISSING) == request.max_tokens
        and raw.get("messages_sha", _MISSING) == request.messages_sha
    )


def _messages_material(messages: Sequence[ChatMessage]) -> str:
    """메시지 시퀀스를 단사적으로 직렬화한다.

    **이전 구현은 구분자로 이었는데, 그것으로는 단사성이 안 선다.**
    `_SEP.join(f"{role}{_SEP}{content}" ...)`는 `content` 안에 구분자와
    같은 문자(U+001F)가 그대로 들어오면 경계가 재분할된다 - 실측:
    `[("system", "a\x1fuser\x1fb")]`(메시지 1개)와
    `[("system","a"),("user","b")]`(메시지 2개)가 같은 문자열을 냈다.
    `content`는 자막 원문이라 임의 문자를 배제할 수 없다.

    `json.dumps`는 문자열 안의 모든 문자를 이스케이프하므로 이 재분할이
    성립하지 않는다 - 배열 원소 개수와 각 문자열의 경계가 구분자 문자의
    부재가 아니라 JSON 문법 자체로 고정된다.
    """
    return json.dumps([[m.role, m.content] for m in messages], ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
