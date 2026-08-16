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
    FatalProviderError,
    RetryableProviderError,
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
