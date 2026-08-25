"""`CachingProvider` 검증 (NFR-3 · FR-2.7 · 설계 §3.2·§3.5).

**재개 게이트가 여기 있다.** "캐시가 있다"가 아니라 **"2회차 호출 수가
1회차보다 정확히 N만큼 적다"** 를 단언한다 - 숫자를 세지 않으면 캐시를
한 번도 안 읽고도 통과한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fakes.provider import ScriptedProvider

from cuesift.store.provider import CachingProvider
from cuesift.translate.openai_compat import OpenAICompatibleProvider
from cuesift.translate.provider import (
    ChatMessage,
    Completion,
    FatalProviderError,
    RetryableProviderError,
    TokenUsage,
)

_MESSAGES = (ChatMessage(role="system", content="지시"), ChatMessage(role="user", content="안녕"))


def _cached(inner: object, tmp_path: Path, *, identity: str = "i|u|m") -> CachingProvider:
    return CachingProvider(inner, identity=identity, cache_dir=tmp_path)


def test_히트하면_안쪽을_부르지_않는다(tmp_path: Path) -> None:
    inner = ScriptedProvider(["응답1"])
    provider = _cached(inner, tmp_path)

    first = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert len(inner.calls) == 1  # 두 번 불렀는데 안쪽은 한 번
    assert first.text == second.text == "응답1"
    assert (provider.hits, provider.misses) == (1, 1)


def test_새_프로세스가_남긴_캐시를_읽는다(tmp_path: Path) -> None:
    # 재개의 실체다. 1회차 객체를 버리고 2회차 객체가 읽는다.
    first_run = _cached(ScriptedProvider(["응답1"]), tmp_path)
    first_run.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    inner = ScriptedProvider([])  # 대본이 비었다 - 부르면 AssertionError
    second_run = _cached(inner, tmp_path)
    got = second_run.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "응답1"
    assert inner.calls == []
    assert (second_run.hits, second_run.misses) == (1, 0)


def test_identity가_다르면_히트하지_않는다(tmp_path: Path) -> None:
    # 이것이 깨지면 qwen2.5:3b 캐시가 gpt-4o 실행에서 히트한다.
    _cached(ScriptedProvider(["3b응답"]), tmp_path, identity="i|u|qwen2.5:3b").complete(
        _MESSAGES, temperature=0.0, max_tokens=None
    )

    inner = ScriptedProvider(["4o응답"])
    other = _cached(inner, tmp_path, identity="i|u|gpt-4o")
    got = other.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "4o응답"
    assert len(inner.calls) == 1


def test_온도가_다르면_히트하지_않는다(tmp_path: Path) -> None:
    provider = _cached(ScriptedProvider(["a", "b"]), tmp_path)

    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    provider.complete(_MESSAGES, temperature=0.7, max_tokens=None)

    assert (provider.hits, provider.misses) == (0, 2)


def test_재시도_가능_실패는_캐시하지_않는다(tmp_path: Path) -> None:
    # 캐시하면 일시적 429가 영구 재생된다.
    inner = ScriptedProvider([RetryableProviderError("429"), "성공"])
    provider = _cached(inner, tmp_path)

    with pytest.raises(RetryableProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "성공"
    assert len(inner.calls) == 2


def test_치명적_실패는_캐시하지_않는다(tmp_path: Path) -> None:
    # 키를 고치고 재실행하면 즉시 통해야 한다.
    inner = ScriptedProvider([FatalProviderError("401"), "성공"])
    provider = _cached(inner, tmp_path)

    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "성공"
    assert len(inner.calls) == 2


def test_형식을_어긴_응답도_캐시한다(tmp_path: Path) -> None:
    # NFR-3은 "같은 입력에 같은 결과"이지 "더 나은 결과"가 아니다 (설계 §3.6).
    # 이것이 함정인 것을 알고 채택했다 - 탈출구는 --no-cache다.
    inner = ScriptedProvider(["죄송합니다, 번역할 수 없습니다."])
    provider = _cached(inner, tmp_path)

    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert second.text == "죄송합니다, 번역할 수 없습니다."
    assert len(inner.calls) == 1


def test_히트해도_저장된_사용량을_그대로_낸다(tmp_path: Path) -> None:
    # 0으로 만들면 calls가 0이 되어 "호출당 토큰"이 계산 불가다 (설계 §3.5.1).
    inner = ScriptedProvider(["응답1"])
    provider = _cached(inner, tmp_path)

    first = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    second = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert second.usage == first.usage
    assert second.usage.calls == 1


def test_빈_identity를_거부한다(tmp_path: Path) -> None:
    # 빠뜨리면 캐시가 모델을 구분하지 못한다. 조용히 통과하면 안 된다.
    with pytest.raises(ValueError, match="identity"):
        CachingProvider(ScriptedProvider([]), identity="   ", cache_dir=tmp_path)


def test_저장_실패는_번역을_죽이지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("디스크 없음")

    monkeypatch.setattr("cuesift.store.provider.store", boom)
    warnings: list[str] = []
    provider = CachingProvider(
        ScriptedProvider(["응답1", "응답2"]),
        identity="i|u|m",
        cache_dir=tmp_path,
        warn=warnings.append,
    )

    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == "응답1"
    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == "응답2"
    # 경고는 한 번만. 수백 번 반복하면 진짜 출력이 묻힌다.
    assert len(warnings) == 1


def test_store가_KeyboardInterrupt를_던지면_그대로_전파된다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WP7b Task 2 리뷰 라운드 4 실측: `_store_or_warn`의
    # `except CACHE_IO_ERRORS`를 `except BaseException`으로 넓혀도 죽는
    # 테스트가 0개였다 - "정리는 최대, 흡수는 한정"(cache.py `store()` 참고)
    # 중 흡수 쪽에는 게이트가 없었다는 뜻이다. 이 테스트가 그 짝이다.
    # 긴 번역 도중 Ctrl+C(FR-2.7 재개의 전형적 트리거)가 캐시 계층에서
    # 조용히 삼켜지면 안 된다 - 삼키면 Ctrl+C가 안 먹힌다.
    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("cuesift.store.provider.store", boom)
    provider = CachingProvider(ScriptedProvider(["응답1"]), identity="i|u|m", cache_dir=tmp_path)

    with pytest.raises(KeyboardInterrupt):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


def test_저장이_새는_예외를_내도_inner_결과가_그대로_나간다(tmp_path: Path) -> None:
    # 리뷰 실측: content에 짝 없는 서러게이트(U+D800)가 섞이면 store()의
    # json.dumps는 통과하지만 tmp.write_text(encoding="utf-8")가
    # UnicodeEncodeError(ValueError의 하위)를 낸다. `except OSError`만
    # 걸려 있으면 이것이 그대로 새어 ProviderError 밖으로 나가고, engine의
    # 배치 폴백(FR-2.6)이 받지 못해 완주하던 실행이 중단된다.
    warnings: list[str] = []
    inner = ScriptedProvider(["\ud800broken"])
    provider = CachingProvider(inner, identity="i|u|m", cache_dir=tmp_path, warn=warnings.append)

    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "\ud800broken"
    assert len(warnings) == 1


@pytest.mark.parametrize("temperature", [None, "hot"])
def test_비수치형_temperature도_inner를_부른다(tmp_path: Path, temperature: object) -> None:
    # 리뷰 실측: request.key가 float(temperature)를 계산하는데, load() 안에서
    # 이 계산이 try 밖에 있다. temperature가 None이면 TypeError, 문자열이면
    # ValueError가 캐시 계층에서 그대로 샌다 - 캐시가 없으면 openai_compat의
    # 가드가 FatalProviderError로 분류해 죽이는 것과 대비된다. 캐시 조회
    # 실패는 미스로 떨어져야 하고, 그러면 inner가 불려야 한다 - 그 다음
    # 무슨 일이 나든(성공이든 FatalProviderError든) 그건 inner의 몫이다.
    inner = ScriptedProvider(["ok"])
    provider = _cached(inner, tmp_path)

    got = provider.complete(_MESSAGES, temperature=temperature, max_tokens=None)  # type: ignore[arg-type]

    assert got.text == "ok"
    assert len(inner.calls) == 1


@pytest.mark.parametrize("temperature", [float("nan"), float("inf")])
def test_nan_inf_temperature는_여전히_미스로_inner를_거친다(
    tmp_path: Path, temperature: float
) -> None:
    # 폭을 넓히는 수정이 nan·inf 경로를 바꾸지 않았음을 고정한다. nan·inf는
    # float이라 request.key 계산(float(temperature))에서 죽지 않는다 - 미스로
    # 떨어져 inner가 불리고, 실제 값 판정(FatalProviderError)은 여전히
    # inner(openai_compat)의 몫이라는 것을 확인한다.
    inner = ScriptedProvider([FatalProviderError("temperature가 유한한 수가 아니다")])
    provider = _cached(inner, tmp_path)

    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=temperature, max_tokens=None)

    assert len(inner.calls) == 1


def test_손상된_캐시는_다시_부른다(tmp_path: Path) -> None:
    inner = ScriptedProvider(["응답1", "응답2"])
    provider = _cached(inner, tmp_path)
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    for path in tmp_path.glob("*.json"):
        path.write_text("{깨짐", encoding="utf-8")

    got = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    assert got.text == "응답2"
    assert len(inner.calls) == 2


def test_시그니처가_프로토콜과_같다() -> None:
    # Provider는 runtime_checkable이 아니고 CI에 타입 검사기도 없다.
    # 이 단언이 이탈을 잡는 유일한 수단이다 (tests/fakes/provider.py와 같은 이유).
    import inspect

    from cuesift.translate.provider import Provider

    assert inspect.signature(CachingProvider.complete) == inspect.signature(Provider.complete)


def test_cache_identity가_모델을_구분한다() -> None:
    a = OpenAICompatibleProvider(base_url="http://h/v1", model="qwen2.5:3b")
    b = OpenAICompatibleProvider(base_url="http://h/v1", model="gpt-4o")
    c = OpenAICompatibleProvider(base_url="http://other/v1", model="qwen2.5:3b")

    assert a.cache_identity != b.cache_identity
    assert a.cache_identity != c.cache_identity
    assert "qwen2.5:3b" in a.cache_identity


def test_캐시_파일에_사람이_읽을_재료가_남는다(tmp_path: Path) -> None:
    provider = _cached(ScriptedProvider(["응답1"]), tmp_path, identity="i|u|qwen2.5:3b")
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)

    raw = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))

    assert raw["identity"] == "i|u|qwen2.5:3b"
    assert raw["created_at"]


# --- 위임 사슬 (재리뷰 1번) ---


class _닫히는프로바이더:
    """`close`를 가진 가짜. 진짜 프로바이더는 여기서 httpx 커넥션 풀을 정리한다."""

    name = "closable"

    def __init__(self) -> None:
        self.closed = 0

    def complete(self, messages, *, temperature, max_tokens) -> Completion:
        return Completion(text="ok", usage=TokenUsage())

    def close(self) -> None:
        self.closed += 1


def test_close를_안쪽으로_위임한다(tmp_path: Path) -> None:
    """위임하지 않으면 **사슬이 절반만 이어진다.**

    D7 정답 배치는 `CachingProvider(CountingProvider(raw))`인데, 안쪽
    `CountingProvider`만 `close`를 넘기고 바깥이 안 넘기면 `cli`의
    `getattr(provider, "close", None)`이 다시 `None`을 받아 raw의 커넥션 풀이
    정리되지 않는다. 절반만 이어진 사슬은 양극단보다 나쁘다 - 안쪽이 위임하는
    것을 본 독자가 사슬 전체가 통한다고 가정한다.
    """
    inner = _닫히는프로바이더()
    _cached(inner, tmp_path).close()
    assert inner.closed == 1


def test_close가_없는_안쪽에서도_조용히_끝난다(tmp_path: Path) -> None:
    """`close`를 안 가진 가짜가 흔하다(`ScriptedProvider`). 여기서 터지면 dry-run이 죽는다."""
    _cached(ScriptedProvider(["응답1"]), tmp_path).close()


def test_cache_identity는_의도적으로_없다() -> None:
    """**이것을 더하면 이중 래핑이 조용히 가능해진다** - 어느 identity가 이기는지 불명확해진다.

    `CachingProvider`는 identity를 **생성자 인자로 받는다**. 그것이 설계다.
    `inner`에서 파생시켜 노출하면 `CachingProvider`를 또 감쌀 때 바깥이 안쪽의
    identity를 물려받아, 호출자가 지정한 값과 파생된 값 중 무엇이 키에 들어가는지
    코드를 파야 알 수 있게 된다. `close`를 위임하면서 이것만 뺀 이유가 여기 있다 -
    "캐시 래퍼를 닫으면 inner를 닫는다"는 해석이 하나뿐인 것과 대조된다.

    호출자는 **raw provider에서 identity를 먼저 뽑고 그 다음에 감싼다**
    (`cli.py`의 `_cache_identity(provider)` → `CachingProvider(provider, identity=...)`).
    """
    assert not hasattr(CachingProvider, "cache_identity")
