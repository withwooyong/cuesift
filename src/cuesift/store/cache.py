"""LLM 응답 캐시 (요구사항정의서 NFR-3 재현성 · §7.1 `store/`).

**캐시는 최적화이지 정확성의 근거가 아니다.** 그래서 읽기가 조금이라도
미심쩍으면 예외가 아니라 **미스**로 떨어뜨린다 - 손상된 파일 하나가 실행
전체를 죽이면 안 되고, 못 믿을 때는 다시 부르면 된다 (설계 §3.3).

**키를 파일명에 넣고 재료를 파일 안에 또 쓴다.** 파일명만 믿으면 손상된
캐시가 조용히 번역문으로 둔갑한다 - 이 저장소가 1급으로 금지한
"검사하지 않고 통과하는 게이트"가 정확히 그 형태다.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cuesift.translate.provider import ChatMessage, Completion, TokenUsage

# `key`의 필수 필드들(identity·temperature·max_tokens·messages_sha)을 잇는
# 구분자. **안전한 이유: (1) 필수 필드는 고정폭 또는 라벨이 있어 경계가 고정되고,
# (2) 옵션 파트(attempt)에는 "attempt=N" 라벨이 있어 파트가 명확히 구분되며,
# (3) 라벨 없는 여섯 번째 파트를 나중에 추가하면 이 안전성이 깨진다.**
# identity·temperature·max_tokens·messages_sha는 이 문자를 담을 수 없다:
# temperature(숫자의 repr)·max_tokens(숫자 또는 "none")·messages_sha(64자리 hex)·
# identity(CLI가 base_url, model로 조립). `content`는 자막 원문이라 담을 수 있으므로
# `_messages_material`은 이 구분자를 쓰지 않는다.
_SEP = "\x1f"

# `_matches`에서 "필드가 없다"를 "값이 None이다"와 구별하려고 쓰는 전용
# 센티넬. `.get(key, None)`을 쓰면 `max_tokens`처럼 유효값이 None인
# 필드에서 "필드 자체가 빠짐"과 "None으로 채워짐"이 같은 결과로 뭉개진다.
_MISSING = object()

# `CachingProvider`(store/provider.py)가 캐시 자신의 잘못으로 흡수해도 되는
# 예외 셋. `OSError`(디스크 I/O)만으로는 부족하다 - 실측(WP7b Task 2 리뷰
# 라운드 2): 자막에 U+D800(짝 없는 서러게이트) 같은, 파이썬 문자열로는
# 유효하지만 UTF-8로 인코딩할 수 없는 문자가 들어오면 `store()`의
# `json.dumps`는 통과하고 `Path.write_text(encoding="utf-8")`가
# `UnicodeEncodeError`(`ValueError`의 하위)를 낸다. `CacheRequest.key`가
# 비수치형 `temperature`에서 `float(...)`를 계산하다 내는 `TypeError`도
# 같은 부류라 함께 잡는다.
#
# **밑줄 없이 둔다.** `CachingProvider`가 이 값을 그대로 임포트해 쓰는 순간
# 모듈 밖 심볼이고, 밑줄은 그 사실을 가린다. 다만 `store/__init__.py`의
# `__all__`에는 넣지 않는다 - 패키지 **외부**에 공개할 표면이 아니라
# `store/` 안의 두 모듈(`cache.py`·`provider.py`)이 나눠 쓰는 내부 계약이다.
#
# **`store()`의 tmp 정리에는 이 상수를 쓰지 않는다** (아래 `store()` 참고) -
# 정리가 막아야 할 범위(어떤 중단이든)와 `CachingProvider`가 흡수해도 되는
# 범위(캐시 자신의 잘못만)가 서로 다르기 때문이다(실측, 라운드 3). 이 값이
# 정의하는 것은 어디까지나 **흡수** 쪽 계약이다.
CACHE_IO_ERRORS = (OSError, ValueError, TypeError)


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
    attempt: int = 0
    """자가일관성의 시도 번호 (FR-4.1 · 설계 §8).

    **0은 키 문자열에 넣지 않는다** - 아래 `key` 참고.
    """

    def __post_init__(self) -> None:
        """`attempt`의 도메인을 생성 시점에 고정한다 (FR-4.1 · 설계 D11).

        **`None`이 들어오면 무음 열화가 된다.** 아래 `key`가 falsy를 만나
        `attempt` 조각을 붙이지 않으므로 `key(None) == key(0)`이다. 그러면
        자가일관성의 N회 호출이 한 캐시 항목으로 뭉쳐 샘플이 1개가 되고,
        `SelfConsistency`의 `len(samples) < 2 -> None` 가드는 **우회된다** -
        그 가드가 보는 것은 "샘플이 몇 개인가"이지 "몇 번 불렀나"가 아니다.
        결과는 "판정 불가"가 아니라 **점수 0.0**, 곧 "안전"이다.

        아래 수치는 WP8b 착수 시점(2026-08-25)에 자가일관성 신호를 실제로
        돌려 얻은 것이다. **자가일관성 구현이 바뀌면 score는 달라진다** -
        고정 계약이 아니라 열화의 모양을 보이는 예시로 읽어라. 계약인 것은
        "호출 수"뿐이다.

        | 값 | 호출 수 | score(당시 실측) | 무엇으로 보이나 |
        | --- | --- | --- | --- |
        | `attempt=i` (정상) | 3회 | 0.0286 | 판정됨 |
        | `attempt=None` | 1회 | 0.0000 | **"판정했고 안전"** |

        bool은 int의 서브클래스라 `attempt=True`가 이 검사를 통과한다.
        그래도 **뭉침은 일어나지 않는다** - 아래 `key`의 `f"attempt={...}"`가
        빈 포맷 스펙이라 `bool.__str__`을 타 `"attempt=True"`라는 별개
        문자열을 만들고, 그래서 `key(True) != key(1)`이다(실측). 위험한
        방향은 키가 뭉치는 쪽이고 갈리는 쪽은 최악이라야 캐시 미스 하나다.
        `attempt=False`는 falsy라 `key(False) == key(0)`인데(실측) 의미가
        같으므로 이 역시 무해하다. 별도로 막지 않는다.
        """
        if not isinstance(self.attempt, int):
            raise ValueError(f"attempt는 int여야 한다 (받은 타입: {type(self.attempt).__name__})")
        # 음수는 `attempt=-1`로 키에 그대로 실려 **유효한 키를 만든다** -
        # 뭉치지는 않지만 시도 번호의 도메인 밖이라 호출부가 `range()`를 잘못
        # 조립했다는 신호다. 통과시키면 그 실수가 캐시 파일로 굳는다.
        if self.attempt < 0:
            raise ValueError(f"attempt는 0 이상이어야 한다 (받은 값: {self.attempt})")

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
        parts = [
            self.identity,
            repr(float(self.temperature)),
            "none" if self.max_tokens is None else str(self.max_tokens),
            self.messages_sha,
        ]
        # **0이면 생략한다.** 넣으면 기존에 쌓인 캐시가 전량 미스가 되어
        # WP7b가 실물로 증명한 재개(2회차 실제 호출 0개)가 한 번 헛돈다.
        # 자가일관성만 시도를 가르면 되고, 나머지 경로는 0이다.
        # 첫 샘플의 attempt=0이 기존 번역(Tier 0)과 섞이지 않는 것은 온도가
        # 다르기 때문이다(일반 번역은 0.0, 자가일관성은 >0). Tier 1이
        # temperature=0.0으로 불리면 이 성질이 깨진다 (설계 §8, 요구사항 FR-4.1).
        if self.attempt:
            parts.append(f"attempt={self.attempt}")
        material = _SEP.join(parts)
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
    """캐시에 쓴다. **어떤 실패든 삼키지 않는다** - 호출자가 경고를 낸다.

    임시 파일에 쓰고 `os.replace`로 옮기는 이유는 중간에 죽어도 반쪽짜리
    JSON이 남지 않게 하기 위해서다. `os.replace`는 Windows에서도 원자적이다.

    임시 파일명에 pid를 넣는 것은 같은 키를 두 프로세스가 동시에 쓸 때
    서로의 임시 파일을 덮지 않게 하기 위해서다. 최종 파일에 대한 경쟁은
    마지막 쓰기가 이기고, 온도 0.0에서는 내용이 같으므로 무해하다.

    **임시 파일 정리는 `finally`다 - `except`로 좁히지 않는다.** 이 자리의
    "옳은 예외 집합"은 `CachingProvider`가 흡수하는 집합(`CACHE_IO_ERRORS`)과
    **다르다.** `write_text`~`os.replace` 사이에서 무엇이 중단시키든(디스크
    오류든, `KeyboardInterrupt`든, `MemoryError`든) tmp는 지워져야 한다 -
    정리는 **최대**여야 하는 반면 흡수는 캐시 자신의 잘못으로 **한정**돼야
    한다(리뷰 실측, WP7b Task 2 라운드 3). `KeyboardInterrupt`가 대표
    사례다 - 긴 번역 도중 Ctrl+C가 그 경로이고(FR-2.7 재개의 전형적
    트리거), 그 예외는 삼키지 않고 그대로 전파돼야 한다(삼키면 Ctrl+C가
    안 먹힌다). `finally`는 정리만 하고 예외는 그대로 다시 새어 나가게
    두므로 이 둘(넓은 정리 vs 좁은 흡수)을 동시에 만족한다.

    지우지 않으면 실패가 반복될 때마다 `<key>.json.<pid>.tmp`가 쌓인다 -
    pid가 매 실행 달라지므로 잔해가 실행마다 누적돼 캐시 디렉터리가
    쓰레기로 찬다(실측: 재현됨). 성공 경로에서는 `os.replace`가 이미 tmp를
    최종 경로로 옮겨 그 자리에 파일이 없으므로 `unlink(missing_ok=True)`가
    조용한 무연산이다 - `missing_ok=True`가 이미 그 분기를 흡수하므로
    `finally`를 피할 이유가 없다.

    **정리 자신의 실패는 `OSError`만 삼킨다 - 원래 예외를 가리면 안
    된다.** 실측(WP7b Task 2 리뷰 라운드 4): `os.replace`가
    `KeyboardInterrupt`를 던지는 **동시에** `unlink`가 `PermissionError`
    (파일이 잠긴 경우 등)를 던지면, 파이썬은 `finally` 블록이 새로 던진
    예외로 진행 중이던 예외를 **대체**한다 - 전파되는 것이
    `KeyboardInterrupt`가 아니라 `PermissionError`가 됐고, 그 값은
    `CachingProvider`의 `CACHE_IO_ERRORS`에 걸려 흡수된다 - Ctrl+C가
    조용히 사라진다. `contextlib.suppress(OSError)`로 `unlink`만 감싸
    정리 실패를 원래 예외보다 부차적으로 만든다. `OSError`로 좁힌 이유는
    `unlink`가 낼 수 있는 실패가 파일시스템 계열
    (`PermissionError`·`IsADirectoryError` 등, 전부 `OSError`의 하위)뿐이기
    때문이다 - `missing_ok=True`가 이미 "없어서 못 지움"
    (`FileNotFoundError`)을 흡수하므로 여기 남는 것은 "있는데 못 지움"류의
    나머지 `OSError`뿐이다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "identity": request.identity,
        "temperature": float(request.temperature),
        "max_tokens": request.max_tokens,
        "messages_sha": request.messages_sha,
        "attempt": request.attempt,
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
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _matches(raw: object, request: CacheRequest) -> bool:
    """파일 안의 재료가 현재 요청과 같은지 본다.

    **키가 이미 이 다섯 개를 덮으므로 정상 경로에서는 항상 참이다.** 이 검사가
    잡는 것은 해시 충돌·파일 손상·수동 편집이다. 그래서 어긋남을 예외가
    아니라 미스로 다룬다 - 캐시를 못 믿을 뿐 실행이 틀린 것은 아니다.

    **`.get(key)`가 아니라 `.get(key, _MISSING)`을 쓴다.** `max_tokens`는
    유효값 자체가 `None`이라, 필드가 통째로 빠진 파일에서 `.get()`의
    기본값도 `None`이면 "필드 없음"과 "값이 None"이 구별되지 않는다 -
    실측: `max_tokens` 필드를 지운 파일이 `request.max_tokens is None`인
    요청과 우연히 일치해 통과했다.

    **`attempt` 필드는 기본값이 0이다.** 옛 파일(a433cbe~1)에는 이 필드가
    없고, 그것은 `attempt=0`을 의미한다 - 기본값을 `_MISSING`으로 하면
    기존 캐시가 전량 미스가 되어 이 태스크가 막으려던 바로 그 일이 일어난다.
    """
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("identity", _MISSING) == request.identity
        and raw.get("temperature", _MISSING) == float(request.temperature)
        and raw.get("max_tokens", _MISSING) == request.max_tokens
        and raw.get("messages_sha", _MISSING) == request.messages_sha
        and raw.get("attempt", 0) == request.attempt
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
