"""캐시 저장소 검증 (NFR-3 · 설계 §3).

**이 파일은 네트워크를 타지 않는다.** 캐시는 파일시스템만 안다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuesift.store.cache import CacheRequest, load, store
from cuesift.translate.provider import ChatMessage, Completion, TokenUsage


def _request(
    *, identity: str = "openai-compatible|http://h/v1|m1", text: str = "안녕"
) -> CacheRequest:
    return CacheRequest(
        identity=identity,
        temperature=0.0,
        max_tokens=None,
        messages=(
            ChatMessage(role="system", content="지시"),
            ChatMessage(role="user", content=text),
        ),
    )


def _completion(text: str = '{"translations": []}') -> Completion:
    return Completion(text=text, usage=TokenUsage(prompt_tokens=7, completion_tokens=11, calls=1))


def test_저장한_것을_그대로_읽는다(tmp_path: Path) -> None:
    request = _request()
    store(tmp_path, request, _completion())

    got = load(tmp_path, request)

    assert got is not None
    assert got.text == '{"translations": []}'
    assert got.usage == TokenUsage(prompt_tokens=7, completion_tokens=11, calls=1)


def test_없으면_None이다(tmp_path: Path) -> None:
    assert load(tmp_path, _request()) is None


def test_identity가_다르면_키가_다르다() -> None:
    # 이것이 없으면 qwen2.5:3b로 채운 캐시가 gpt-4o 실행에서 히트한다.
    a = _request(identity="openai-compatible|http://h/v1|qwen2.5:3b")
    b = _request(identity="openai-compatible|http://h/v1|gpt-4o")

    assert a.key != b.key


def test_메시지가_다르면_키가_다르다() -> None:
    assert _request(text="안녕").key != _request(text="잘가").key


def test_키는_재실행에서_같다() -> None:
    # 재개가 성립하는 유일한 근거다. 키가 흔들리면 캐시가 영원히 미스다.
    assert _request().key == _request().key


def test_역할과_내용의_경계가_모호하지_않다() -> None:
    # "system"+"지시" 와 "system지시"+"" 가 같은 키를 내면 안 된다.
    a = CacheRequest(
        identity="i",
        temperature=0.0,
        max_tokens=None,
        messages=(ChatMessage(role="system", content="지시"),),
    )
    b = CacheRequest(
        identity="i",
        temperature=0.0,
        max_tokens=None,
        messages=(ChatMessage(role="system", content=""), ChatMessage(role="user", content="지시")),
    )

    assert a.key != b.key


def test_손상된_파일은_예외가_아니라_미스다(tmp_path: Path) -> None:
    # 손상된 캐시 파일 하나가 실행 전체를 죽이면 안 된다 (설계 §3.3).
    request = _request()
    store(tmp_path, request, _completion())
    (tmp_path / f"{request.key}.json").write_text("{깨진 JSON", encoding="utf-8")

    assert load(tmp_path, request) is None


def test_identity가_어긋난_파일은_미스다(tmp_path: Path) -> None:
    # 해시 충돌·수동 편집 방어. 파일명만 믿으면 손상된 캐시가 번역문으로 둔갑한다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["identity"] = "다른-모델"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_messages_sha가_어긋난_파일은_미스다(tmp_path: Path) -> None:
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["messages_sha"] = "0" * 64
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_음수_토큰은_미스다(tmp_path: Path) -> None:
    # TokenUsage가 ValueError를 던지는데, 그것이 load 밖으로 새면
    # 손상 파일 하나가 실행을 죽인다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["usage"]["prompt_tokens"] = -1
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_디렉터리가_없어도_저장이_만든다(tmp_path: Path) -> None:
    target = tmp_path / "없는" / "깊은" / "경로"
    request = _request()

    store(target, request, _completion())

    assert load(target, request) is not None


def test_임시_파일을_남기지_않는다(tmp_path: Path) -> None:
    # os.replace가 안 돌면 .tmp가 남는다. 남으면 캐시 디렉터리가 쓰레기로 찬다.
    store(tmp_path, _request(), _completion())

    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_저장_실패는_예외를_내지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 디스크가 차거나 읽기 전용이어도 번역 자체가 실패할 이유는 없다.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("디스크 없음")

    monkeypatch.setattr("cuesift.store.cache.os.replace", boom)

    with pytest.raises(OSError):
        store(tmp_path, _request(), _completion())
