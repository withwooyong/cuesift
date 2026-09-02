"""백오프 정책 (설계 §4.1).

**이 파일이 있는 이유는 공유다.** 정책이 `translate/engine.py`에만 있으면
STT 쪽이 자기 판본을 만들고, 두 판본은 한쪽만 상한을 고쳤을 때 갈린다.
"""

from __future__ import annotations

from cuesift.retry import MAX_BACKOFF_S, backoff_delay


def test_서버_힌트가_지수_백오프를_이긴다() -> None:
    assert backoff_delay(0, 5.0) == 5.0


def test_힌트_0은_None과_다르다() -> None:
    # 참·거짓으로 보면 0이 None과 뭉뚱그려져 1.0으로 떨어진다. 0은
    # "쓸 수 있는 힌트가 없음"이 아니라 "지금 다시 걸어도 된다"는 유효한
    # 힌트이고, `RetryableProviderError`의 정규화도 0을 통과시킨다.
    assert backoff_delay(0, 0.0) == 0.0


def test_힌트가_없으면_지수로_자란다() -> None:
    assert [backoff_delay(i, None) for i in range(4)] == [1.0, 2.0, 4.0, 8.0]


def test_상한이_서버_힌트에_걸린다() -> None:
    # `Retry-After: 86400`은 일일 할당량 리셋을 알리는 실서비스의 흔한 값이다.
    # 그대로 자면 CLI가 하루 동안 무출력으로 멈춘다.
    assert backoff_delay(0, 86400.0) == MAX_BACKOFF_S


def test_상한이_지수_백오프에도_걸린다() -> None:
    # **상한을 힌트 경로에만 걸면 이쪽이 무한정 자란다.** 두 경로 모두에
    # 걸어야 한다는 것이 이 함수의 계약이다.
    assert backoff_delay(20, None) == MAX_BACKOFF_S
