"""종료 코드 계약 (`cli.py` 모듈 독스트링이 단일 출처).

**여기가 계약을 지키는 유일한 자리다.** 값 자체를 단언하는 테스트가 흩어져
있으면 하나를 고칠 때 나머지가 조용히 남는다.
"""

from __future__ import annotations

import pytest

from cuesift.cli import (
    EXIT_BAD_INPUT,
    EXIT_SOFTWARE,
    EXIT_TRANSLATION_FAILURE,
    EXIT_UNAVAILABLE,
    _combine_exit_codes,
)


def test_번역_실패는_sysexits_밖의_3이다() -> None:
    """값이 바뀌면 CI 스크립트가 조용히 어긋난다. 리터럴로 못 박는다.

    **후보였던 75(EX_TEMPFAIL)를 버린 이유가 이 단언에 실려 있다** - "다시
    시도하라"는 뜻인데 캐시가 실패 응답을 보존해 재실행이 호출 0회로 같은
    실패를 낸다(실측). CI가 재시도로 읽으면 무한 루프가 된다.
    """
    assert EXIT_TRANSLATION_FAILURE == 3


def test_일곱_코드가_서로_겹치지_않는다() -> None:
    """**모듈 독스트링이 "일곱"이라고 말하므로 일곱을 전수로 센다.**

    `EXIT_` 상수 넷만 보면 `EXIT_TRANSLATION_FAILURE = 1`(규격 위반과 충돌)이나
    `= 2`(명령줄 오류와 충돌) 같은 회귀가 통과한다 - 겹치는 상대가 상수가
    아니라 **리터럴로만 존재하는 0·1·2**이기 때문이다.
    """
    codes = [
        0,  # 성공
        1,  # 규격 위반 발견 (`check`)
        2,  # 명령줄이 틀림 (typer)
        EXIT_TRANSLATION_FAILURE,
        EXIT_BAD_INPUT,
        EXIT_UNAVAILABLE,
        EXIT_SOFTWARE,
    ]
    assert len(set(codes)) == 7


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ((), 0),
        ((0, 0), 0),
        ((0, EXIT_TRANSLATION_FAILURE), EXIT_TRANSLATION_FAILURE),
        # 3이 어느 코드에도 지는 것을 고정한다. **`max` 변이로는 안 죽는다** -
        # 3이 최솟값이라 `max`가 같은 답을 낸다. 이 세 줄이 지키는 것은
        # "`max`와 다르다"가 아니라 **"번역 실패는 다른 실패보다 약하다"**는
        # 계약 자체다. 값을 바꾸다 3이 66보다 커지면 여기가 죽는다.
        ((EXIT_TRANSLATION_FAILURE, EXIT_UNAVAILABLE), EXIT_UNAVAILABLE),
        ((EXIT_TRANSLATION_FAILURE, EXIT_SOFTWARE), EXIT_SOFTWARE),
        ((EXIT_TRANSLATION_FAILURE, EXIT_BAD_INPUT), EXIT_BAD_INPUT),
        ((EXIT_BAD_INPUT, EXIT_UNAVAILABLE), EXIT_UNAVAILABLE),
        ((EXIT_BAD_INPUT, EXIT_SOFTWARE), EXIT_SOFTWARE),
        # **이것이 이 표의 핵심 단언이다.** 70(우리 쪽 결함)이 69(서비스 거부)를
        # 이긴다 - 69는 설정을 고치면 사라지지만 70은 안 사라진다. 게다가 69는
        # 조기 break를 걸어 다음 언어를 건너뛰므로, 여기서 69가 이기면 70이
        # 다음 실행까지 숨는다. **값 크기로는 맞는 쌍이라 `max` 변이로는 안
        # 죽는다**(70 > 69) - `_EXIT_PRIORITY`를 뒤집는 변이만 이것을 죽인다.
        ((EXIT_SOFTWARE, EXIT_UNAVAILABLE), EXIT_SOFTWARE),
        # **오늘 `max()`와 이 함수를 가르는 것은 이 두 줄뿐이다.**
        # `_EXIT_PRIORITY`가 `(70, 69, 66, 3)`이라 값의 내림차순과 정확히
        # 같아, 등록된 넷만 넣으면 `max()`가 **같은 답을 낸다** - 실측으로
        # `max(codes, default=0)` 변이에서 위 여덟 줄이 전부 살아남는다.
        # 미등록 코드가 오는 순간 둘이 갈라진다: 표에 있는 것이 이긴다.
        #
        # 지우면 `_EXIT_PRIORITY`가 **반증 불가능한 게이트**가 된다 - 표를
        # 값 크기로 되돌려도 아무 테스트가 안 죽으니, 나중에 값 순서와
        # 어긋나는 코드를 추가할 때 회귀가 조용히 들어온다.
        ((99, EXIT_UNAVAILABLE), EXIT_UNAVAILABLE),
        # 미등록끼리는 결정적으로 가장 작은 것. 0으로 삼키지 않는 것이 요점이다.
        ((99, 98), 98),
    ],
)
def test_더_근본적인_실패가_이긴다(codes: tuple[int, ...], expected: int) -> None:
    """순서가 값의 크기가 아니라는 것을 고정한다.

    **두 변이가 서로 다른 줄을 죽인다.** `max`로 되돌리면 미등록 코드가 든
    두 줄(`99`가 낀 것)만 죽는다 - 나머지는 `_EXIT_PRIORITY`가 값의
    내림차순과 일치해 `max`와 답이 같기 때문이다. `_EXIT_PRIORITY`를
    뒤집으면 순서를 주장하는 줄들이 죽고 그 둘은 산다.

    **어느 하나만으로는 이 표를 다 지키지 못한다.** 실측으로 확인한 것이고,
    처음 쓴 판은 "`max` 변이로 3이 얽힌 세 쌍이 죽는다"고 적었는데 거짓이었다
    (리뷰 2라운드에서 표 11줄을 재평가해 잡았다).
    """
    assert _combine_exit_codes(codes) == expected


def test_순서가_인자_배치에_좌우되지_않는다() -> None:
    # 언어 순서가 `--to en,ja`냐 `ja,en`이냐로 CI 판정이 갈리면 안 된다.
    a = _combine_exit_codes((EXIT_TRANSLATION_FAILURE, EXIT_UNAVAILABLE))
    b = _combine_exit_codes((EXIT_UNAVAILABLE, EXIT_TRANSLATION_FAILURE))
    assert a == b == EXIT_UNAVAILABLE
