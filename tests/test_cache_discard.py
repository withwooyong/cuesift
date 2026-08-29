"""파싱 실패 응답을 캐시에 남기지 않는다 (FR-2.7 · FR-2.6 · 파킹 #13).

**engine과 캐시의 교차 계약이라 여기 있다.** 판정은 engine(`parse_translations`)이
하고 저장은 `store/`가 하므로, 어느 한쪽의 단위 테스트만으로는
"engine이 폐기를 시키는가"가 드러나지 않는다.

단언은 전부 **2회차의 실제 호출 수**다. "캐시가 비었다"를 파일로 확인하면
경로 규칙을 테스트가 재구현하게 되고, 그 재구현이 틀려도 통과한다.
"""

from __future__ import annotations

from pathlib import Path

from tests.fakes.provider import EchoProvider

from cuesift.segment.models import Segment
from cuesift.store.provider import CachingProvider
from cuesift.translate.engine import translate_segments


def _segs(n: int) -> list[Segment]:
    return [
        Segment(
            id=f"s{i}", index=i, start_ms=i * 1000, end_ms=i * 1000 + 900, source_text=f"문장{i}"
        )
        for i in range(n)
    ]


def _run(inner: EchoProvider, cache_dir: Path) -> int:
    """한 번 돌리고 **이번 실행에서 안쪽을 몇 번 불렀는지** 낸다."""
    before = len(inner.calls)
    translate_segments(
        _segs(2),
        provider=CachingProvider(inner, identity="i|u|m", cache_dir=cache_dir),
        source_lang="ko",
        target_lang="en",
        batch_size=2,
    )
    return len(inner.calls) - before


def test_파싱_실패_응답은_캐시에_남지_않는다(tmp_path: Path) -> None:
    # 파킹 #13. 오늘은 2회차가 0이다 - 실패가 무료로 영구 재생된다.
    inner = EchoProvider(garbage=True)

    first = _run(inner, tmp_path)
    second = _run(inner, tmp_path)

    assert first == 3  # 배치 1회 + 개별 폴백 2회
    assert second == 3  # 셋 다 폐기됐으므로 그대로 다시 부른다


def test_폴백에서_성공한_것은_캐시에_남는다(tmp_path: Path) -> None:
    # `--no-cache`와 갈리는 지점이다. 실패한 배치만 재호출하고 성공분은
    # 재결제하지 않는다.
    inner = EchoProvider(fail_batches_of_size=2)

    first = _run(inner, tmp_path)
    second = _run(inner, tmp_path)

    assert first == 3
    assert second == 1  # 깨진 배치 호출만 다시. 개별 2건은 캐시 히트


def test_빈_번역은_캐시에서_빼지_않는다(tmp_path: Path) -> None:
    # 범위의 못. 개수도 번호도 맞은 응답은 계약을 지킨 것이고, 폐기하면
    # 같은 배치에서 성공한 나머지까지 다시 결제한다.
    inner = EchoProvider(transform=lambda _s: "")

    first = _run(inner, tmp_path)
    second = _run(inner, tmp_path)

    assert first == 1
    assert second == 0


def test_캐시를_끼우지_않은_프로바이더에서도_동작한다(tmp_path: Path) -> None:
    # `--no-cache`와 raw 프로바이더에는 `discard`가 없다. 없는 것은 오류가
    # 아니라 할 일이 없는 것이다 - 여기서 AttributeError가 나면 번역이 죽는다.
    inner = EchoProvider(garbage=True)

    result = translate_segments(
        _segs(2), provider=inner, source_lang="ko", target_lang="en", batch_size=2
    )

    assert [f.reason for f in result.failures] == ["invalid_response", "invalid_response"]
