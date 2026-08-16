"""캐시 저장소 검증 (NFR-3 · 설계 §3).

**이 파일은 네트워크를 타지 않는다.** 캐시는 파일시스템만 안다.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
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


def test_temperature가_int로_저장돼도_float_요청이_히트한다(tmp_path: Path) -> None:
    # `key`의 `float(...)` 정규화가 없으면 repr(0)="0"과 repr(0.0)="0.0"이
    # 달라 `0`(int)으로 부른 caller의 캐시를 `0.0`(float)으로 부른 재실행이
    # 못 읽는다 - 호출부의 타입 차이 하나로 캐시가 전량 미스가 된다.
    stored = CacheRequest(identity="i", temperature=0, max_tokens=None, messages=())
    reloaded = CacheRequest(identity="i", temperature=0.0, max_tokens=None, messages=())
    store(tmp_path, stored, _completion())

    assert load(tmp_path, reloaded) is not None


def test_temperature는_저장_시_float로_정규화된다(tmp_path: Path) -> None:
    # `store`가 페이로드를 만들 때 `float(...)`를 빼면 `int`가 그대로
    # JSON에 실린다 - 값 비교(`==`)는 int/float를 자동으로 값으로
    # 비교해 겉으로는 안 드러나지만, 재현성(NFR-3)의 근거는 "디스크에
    # 실제로 적힌 재료가 항상 같은 타입"이라는 계약이므로 타입까지 본다.
    request = CacheRequest(identity="i", temperature=0, max_tokens=None, messages=())

    store(tmp_path, request, _completion())

    raw = json.loads((tmp_path / f"{request.key}.json").read_text(encoding="utf-8"))
    assert isinstance(raw["temperature"], float)


def test_matches의_온도_비교는_float로_정규화한다(tmp_path: Path) -> None:
    # `key`와 `store`는 이미 `float(...)`로 정규화하는데 `_matches`만
    # 안 하면 **비대칭**이 생긴다 - 예를 들어 한쪽만 걸리면 `0`(int)으로
    # 저장한 캐시를 `0.0`(float)으로도 못 읽는 형태가 재현된다.
    # 실측: `Decimal("0.1") == 0.1`은 False, `float(Decimal("0.1")) == 0.1`은
    # True. 다만 이 경로가 성립하려면 캐시가 **미스**여서는 안 된다 -
    # `Decimal` 온도는 `openai_compat.py`의 `isinstance(temperature, int | float)`
    # 가드에 걸려 inner 호출 전에 `FatalProviderError`로 죽으므로, 이
    # 정규화는 **전 구간이 캐시 히트인 재실행**에서만 관찰된다(현재
    # `Decimal`을 만드는 호출자도 없다 - 모든 어노테이션이 `float`이고
    # PyYAML도 `Decimal`을 만들지 않는다). 그래도 `_matches`만 예외로
    # 두면 비대칭이 코드에 남으므로 정규화 자체는 유지한다.
    stored = CacheRequest(identity="i", temperature=Decimal("0.1"), max_tokens=None, messages=())
    store(tmp_path, stored, _completion())

    reloaded = CacheRequest(identity="i", temperature=Decimal("0.1"), max_tokens=None, messages=())
    assert load(tmp_path, reloaded) is not None


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


def test_content_안의_구분자_문자가_메시지_경계를_흐리지_않는다() -> None:
    # 리뷰 실측: content에 U+001F(내부 구분자와 같은 문자)가 그대로 들어가면
    # 메시지 한 개짜리 [("system", "a\x1fuser\x1fb")]와 메시지 두 개짜리
    # [("system","a"),("user","b")]가 같은 키를 냈다. 단사적 직렬화라면
    # 두 시퀀스는 구조가 다르므로 같은 키를 낼 수 없어야 한다.
    merged = CacheRequest(
        identity="i",
        temperature=0.0,
        max_tokens=None,
        messages=(ChatMessage(role="system", content="a\x1fuser\x1fb"),),
    )
    split = CacheRequest(
        identity="i",
        temperature=0.0,
        max_tokens=None,
        messages=(
            ChatMessage(role="system", content="a"),
            ChatMessage(role="user", content="b"),
        ),
    )

    assert merged.key != split.key


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


def test_text가_문자열이_아니면_미스다(tmp_path: Path) -> None:
    # 리뷰 실측: `text`를 12345로 손상시키면 load가 None이 아니라
    # `Completion(text=12345, ...)`를 돌려준다. 그 값이 하류 파서에 가면
    # `TypeError`가 나는데, `TypeError`는 `ProviderError` 계열이 아니라
    # 번역 루프 밖으로 샌다 - 모듈 독스트링이 막겠다고 선언한 실패 모드다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["text"] = 12345
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_prompt_tokens가_정수가_아니면_미스다(tmp_path: Path) -> None:
    # 리뷰 실측: `prompt_tokens: 7.5`도 통과했다. TokenUsage는 값이 음수인지만
    # 보고 타입은 안 본다 - NFR-2 비용 리포트가 실수로 오염된다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["usage"]["prompt_tokens"] = 7.5
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_calls가_bool이면_미스다(tmp_path: Path) -> None:
    # 리뷰 실측: `calls: true`도 통과했다. `bool`은 `int`의 하위형이라
    # `isinstance(x, int)`만으로는 못 막는다 - `isinstance(x, int) and not
    # isinstance(x, bool)`이 필요하다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["usage"]["calls"] = True
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_max_tokens_필드가_없으면_미스다(tmp_path: Path) -> None:
    # 리뷰 실측: `_matches`가 `raw.get("max_tokens")`를 쓰므로 필드가
    # 통째로 없어도 `.get()`의 기본값 None이 request.max_tokens(None)과
    # 우연히 일치해 통과했다 - "필드 없음"과 "값이 None"이 구별되지 않는다.
    request = _request()
    store(tmp_path, request, _completion())
    path = tmp_path / f"{request.key}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["max_tokens"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert load(tmp_path, request) is None


def test_디렉터리가_없어도_저장이_만든다(tmp_path: Path) -> None:
    target = tmp_path / "없는" / "깊은" / "경로"
    request = _request()

    store(target, request, _completion())

    assert load(target, request) is not None


def test_임시_파일을_남기지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 리뷰 실측: "tmp 파일이 없다"만 보면, 애초에 tmp를 거치지 않고 최종
    # 파일에 직접 쓰는 구현도 이 단언을 통과한다(임시 파일을 안 만드니
    # "안 남았다"가 동어반복이 된다) - `os.replace`가 실제로 호출되는지까지
    # 봐야 "임시 파일 경유 → 원자적 교체"라는 설계 자체를 잰다.
    calls: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def spy(src: object, dst: object) -> None:
        calls.append((Path(src), Path(dst)))
        original_replace(src, dst)

    monkeypatch.setattr("cuesift.store.cache.os.replace", spy)

    request = _request()
    store(tmp_path, request, _completion())

    assert len(calls) == 1
    src, dst = calls[0]
    assert src.suffix == ".tmp"
    assert dst == tmp_path / f"{request.key}.json"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_저장_실패_시_임시_파일이_남지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 리뷰 실측: `os.replace`가 실패하면 `<key>.json.<pid>.tmp`가 실제로
    # 남는다(디스크 여유·권한 문제가 대표 사례) - pid가 매 실행 달라지므로
    # 잔해가 실행마다 쌓인다. `store`가 `OSError`를 그대로 재던지는 성질은
    # 유지해야 하므로(호출자가 경고를 내야 한다), 정리만 얹는다.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("디스크 없음")

    monkeypatch.setattr("cuesift.store.cache.os.replace", boom)

    with pytest.raises(OSError):
        store(tmp_path, _request(), _completion())

    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_저장_실패는_예외를_내지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 디스크가 차거나 읽기 전용이어도 번역 자체가 실패할 이유는 없다.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("디스크 없음")

    monkeypatch.setattr("cuesift.store.cache.os.replace", boom)

    with pytest.raises(OSError):
        store(tmp_path, _request(), _completion())


def test_OSError가_아닌_저장_실패도_임시_파일을_남기지_않는다(tmp_path: Path) -> None:
    # WP7b Task 2 리뷰 라운드 2 실측: content에 짝 없는 서러게이트(U+D800)가
    # 있으면 json.dumps는 통과하지만 tmp.write_text(encoding="utf-8")가
    # UnicodeEncodeError(ValueError의 하위)를 낸다. `except OSError`만
    # 걸려 있으면 이것을 못 잡아 tmp가 그대로 남는다 - pid가 매 실행
    # 달라지므로 같은 서러게이트 콘텐츠가 반복되는 실행에서 잔해가 쌓인다.
    request = _request()
    completion = _completion(text="\ud800broken")

    with pytest.raises(ValueError):
        store(tmp_path, request, completion)

    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_KeyboardInterrupt에도_임시_파일을_남기지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WP7b Task 2 리뷰 라운드 3 실측: `CACHE_IO_ERRORS`(OSError·ValueError·
    # TypeError)는 `CachingProvider`가 "캐시 자신의 잘못만" 흡수하는 데는
    # 맞는 범위이지만, `store()`의 tmp 정리는 그보다 **넓어야** 한다 -
    # write_text~os.replace 사이의 어떤 중단이든 tmp를 남기면 안 된다.
    # KeyboardInterrupt(BaseException, Exception도 CACHE_IO_ERRORS도 아님)가
    # 대표 사례다 - 긴 번역 도중 Ctrl+C가 그 경로다(FR-2.7 재개의 전형적
    # 트리거). **이 예외는 전파돼야 한다** - CachingProvider가 삼키면 Ctrl+C가
    # 안 먹힌다. `store()`가 `finally`로 tmp만 지우고 예외는 그대로 새어
    # 나가게 두는지를 함께 본다.
    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("cuesift.store.cache.os.replace", boom)

    with pytest.raises(KeyboardInterrupt):
        store(tmp_path, _request(), _completion())

    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_정리_실패가_원래_예외를_가리지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WP7b Task 2 리뷰 라운드 4 실측: `os.replace`가 KeyboardInterrupt를
    # 던지는 **동시에** `tmp.unlink`가 PermissionError(파일이 잠긴 경우 등)를
    # 던지면, `finally` 블록이 새 예외로 원래 예외를 **대체**한다는 파이썬의
    # 규칙 때문에 전파되는 것은 PermissionError였다. 그 값은 CACHE_IO_ERRORS에
    # 걸려 CachingProvider가 흡수한다 - Ctrl+C가 조용히 사라진다. 정리
    # 자체의 실패는 원래 예외보다 부차적이어야 한다.
    #
    # tmp가 남는지는 여기서 보지 않는다 - `unlink` 자체를 항상 실패하도록
    # 몽키패치했으므로 이 테스트에서는 지워질 수가 없다. 이 테스트가 재는
    # 것은 "무엇이 전파되는가"뿐이고, "tmp가 지워지는가"는
    # `test_KeyboardInterrupt에도_임시_파일을_남기지_않는다`의 몫이다.
    def replace_boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    def unlink_boom(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("잠김")

    monkeypatch.setattr("cuesift.store.cache.os.replace", replace_boom)
    monkeypatch.setattr(Path, "unlink", unlink_boom)

    with pytest.raises(KeyboardInterrupt):
        store(tmp_path, _request(), _completion())
