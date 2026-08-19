"""`review.json` 직렬화 (요구사항정의서 §8.4 · FR-7.2).

**스키마는 이 파일이 정하지 않는다.** 요구사항정의서 §8.4가 계약이고 여기는
그것을 채운다. 필드를 늘리거나 이름을 바꾸려면 §8.4를 먼저 고친다 - 파일을
읽는 스크립트가 이미 밖에 있을 수 있다.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from cuesift.report.models import TriageOutcome
from cuesift.segment import Segment, SegmentRisk, Signal

# 지금 집계되는 비용 계층. **WP8b가 Tier 1을 CLI에 붙이면 `"tier1"`을 더한다.**
#
# 이 목록이 없으면 Tier 1을 켠 실행에서 `cost`가 번역 토큰만 세면서 전체인 척
# 하고, 그 사실을 알릴 수단이 없다 - `collect_tier1`이 `TranslationResult.usage`를
# 올려 보낼 통로를 아직 갖고 있지 않기 때문이다(NFR-2 · FR-7.4).
_COST_INCLUDES = ["translation"]


def build_review(outcome: TriageOutcome) -> dict[str, Any]:
    """트리아지 결과를 §8.4 스키마의 dict로 만든다."""
    by_id = {seg.id: seg for seg in outcome.segments}
    usage = outcome.usage

    return {
        "summary": {
            # 재현성 필드 - 파일만 보고 "무엇을 어느 규격으로 어떤 정책에서
            # 걸렀나"를 알 수 있어야 한다(설계 §3.5). 리포트 파일은 옮겨지고
            # 첨부되고 며칠 뒤에 열린다.
            "source_lang": outcome.source_lang,
            "target_lang": outcome.target_lang,
            "profile": outcome.profile_name,
            # 화면 라벨(`예산 10%`)이 아니라 정규화된 값이다. **`value`는 언제나
            # 비율이지 퍼센트가 아니다** - 퍼센트를 넣으면 소비자가 100배 틀린다.
            "policy": {"kind": outcome.policy_kind, "value": outcome.policy_value},
            # 셋을 함께 낸다 - `total = triaged + excluded`가 파일 안에서
            # 검산된다(설계 §6.2). 하나로 합치면 `review_ratio`의 분모가
            # 무엇인지 알 수 없어 README 배수가 조용히 틀린다.
            "total_segments": outcome.total_segments,
            "triaged_segments": outcome.triaged_segments,
            "excluded_failures": outcome.excluded_failures,
            "selected_for_review": outcome.selected_for_review,
            "review_ratio": outcome.review_ratio,
            "hard_fail_count": outcome.hard_fail_count,
            "signal_hits": outcome.signal_hits,
            "cost": {
                "prompt_tokens": 0 if usage is None else usage.prompt_tokens,
                "completion_tokens": 0 if usage is None else usage.completion_tokens,
                "calls": 0 if usage is None else usage.calls,
                "includes": list(_COST_INCLUDES),
            },
        },
        # 선별된 것만 담는다(설계 D3) - FR-7.2가 "검수 **대상** 세그먼트
        # 목록"이다. 분모는 위 `summary`가 이미 냈다.
        "segments": [_segment_doc(risk, by_id[risk.segment_id]) for risk in outcome.selected],
    }


def write_review(outcome: TriageOutcome, path: Path) -> None:
    """`review.json`을 쓴다 (FR-7.2 · 설계 §5.2).

    **예외를 잡지 않는다.** `OSError`는 디스크 상태의 문제이고 직렬화 실패는
    내부 결함이라 성격이 다르다 - **호출자가 각각을 종료 코드로 바꾼다.**
    여기서 삼키면 파일이 없는데 종료 코드가 0이 되어 다음 단계(배포 스크립트·CI)가
    빈손으로 진행한다.

    **직렬화 실패를 `TypeError` 하나로 적으면 안 된다** (실측, Task 6 리뷰 계약 축).
    `json.dumps`가 내는 것은 열린 집합이다.

    | 입력 | 예외 |
    | --- | --- |
    | 직렬화 불가 객체 · `set` · tuple 키 | `TypeError` |
    | **순환 참조** | **`ValueError`** ("Circular reference detected") |
    | 깊은 중첩 | `RecursionError` |
    | **`NaN`·`Infinity`** | **`ValueError`** (`allow_nan=False`가 만든다. 아래 참고) |
    | 서로게이트(`\\ud800`)가 섞인 문자열 | `UnicodeEncodeError` (아래 `write_text`에서) |

    이 표가 이전 판에 없어서 호출자가 `except TypeError` 하나로 그물을 짰고,
    나머지 셋이 미처리 traceback으로 샜다. **호출자는 넓게 잡아야 한다** -
    어느 타입이 나올지는 `detail`에 실리는 값에 달렸고 그것은 신호 구현이 정한다.

    **표의 마지막 행만 `json.dumps` 밖에서 난다.** 그 하나 때문에 아래 쓰기가
    임시 파일 + `os.replace`여야 한다 - 자세한 근거는 본문 주석에 있다.

    **종료 코드 숫자는 여기 적지 않는다.** 이 모듈은 라이브러리이고 코드
    정책은 `cli.py`가 갖는다 - 숫자를 양쪽에 적으면 CLI가 정책을 바꿀 때
    갈라지고 어느 쪽이 계약인지 알 수 없어진다.

    **`ensure_ascii=False`가 필수다.** 이 프로젝트의 원문은 한국어이고
    `\\uc6d0\\ubb38`로 이스케이프되면 사람이 파일을 열어 읽을 수 없다 -
    FR-7.2의 수혜자가 검수자라는 사실이 이 인자 하나에 걸린다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # **직렬화를 끝내고 나서 연다.** `json.dump(fp)`로 스트리밍하면 `TypeError`가
    # 난 시점에 이미 열린 파일에 절반이 쓰여 있어 깨진 JSON이 남는다 - 소비자는
    # 그것을 "파일이 있으니 성공"으로 읽는다.
    #
    # **이 순서만으로는 위 표의 다섯 예외 중 넷만 막는다.** 마지막 행
    # (`UnicodeEncodeError`)은 `json.dumps`가 아니라 `write_text` **안에서**
    # 나는데, `write_text`는 먼저 truncate하며 열고 **그 다음에** 인코딩한다 -
    # 이 순서가 방어선의 바깥이다. 그래서 아래를 임시 파일로 쓴다.
    #
    # `indent=2`는 사람이 읽는 산출물이기 때문이다. diff도 줄 단위로 난다.
    #
    # **`allow_nan=False`가 없으면 규격 위반 JSON이 예외 없이 나간다.** 기본값
    # (`True`)은 `NaN`·`Infinity`·`-Infinity`를 그 이름 그대로 써 넣는데 그것은
    # JSON 규격에 없는 리터럴이라 `jq`와 JS `JSON.parse`가 거부한다 - **파이썬
    # 파서만 관대해서 통과시키므로** 이 리포의 왕복 단언(`json.loads(text) == doc`)
    # 으로는 영영 잡히지 않는다. 오늘 도달 경로는 0이다(`Signal.__post_init__`의
    # 범위 검사·`_parse_review_budget`·`cli.py`의 `math.isnan` 가드·`review_ratio`가
    # 정수 나눗셈인 것, 넷이 막는다 - 실측). **그 4중이 유지된다는 보장은
    # `detail`에 없다** - `detail`은 신호 구현이 무엇이든 담는 자유 dict이고
    # 어느 검사도 그 안을 보지 않는다. 여기서 `ValueError`로 만들면 호출자의
    # 넓은 그물이 종료 코드 70으로 바꾼다(`cli.py`).
    text = json.dumps(build_review(outcome), ensure_ascii=False, indent=2, allow_nan=False)
    # **임시 파일에 쓰고 `os.replace`로 갈아 끼운다 - 실패해도 기존 파일이
    # 손도 대지 않은 채 남는다.** 이것이 없으면 재실행에서 인코딩이 실패할 때
    # 지난 실행의 정상 리포트가 **0바이트로 파괴된다**(실측: 정상 1071바이트를
    # 쓴 뒤 서로게이트 섞인 outcome으로 재호출 → 실패 후 0바이트·기존 내용
    # 보존 실패). 그 0바이트 파일이야말로 위 "파일이 있으니 성공"의 최악형이다.
    # `store/cache.py`의 `store()`가 같은 패턴을 쓰고 `cli.py`의 dry-run
    # 독스트링이 그 원자성을 인용한다.
    #
    # **같은 디렉터리에 둬야 한다.** `os.replace`는 같은 파일시스템 안에서만
    # 원자적이고, `tempfile.gettempdir()`은 다른 볼륨일 수 있다.
    #
    # **PID를 이름에 넣는 것은 동시 실행 때문이다.** 고정 이름이면 두 프로세스가
    # 같은 임시 파일을 밟아 반쯤 쓰인 것이 `os.replace`로 나간다.
    #
    # **`newline="\n"`이 없으면 줄바꿈이 플랫폼마다 갈린다.** 텍스트 모드의
    # 기본값은 `\n`을 `os.linesep`으로 번역하므로 Windows에서는 CRLF가, Linux
    # CI에서는 LF가 나간다 - 같은 입력이 다른 바이트를 낸다(실측: Windows에서
    # CRLF 39개·순수 LF 0개). 위 줄이 "diff도 줄 단위로 난다"고 적은 의도가
    # 그때 깨진다. `.gitattributes`가 `* text=auto eol=lf`로 소스에 이미 건
    # 규율을 산출물에도 적용하는 것이다.
    #
    # **`finally`의 `unlink`를 `contextlib.suppress(OSError)`로 감싼다.** 감싸지
    # 않으면 정리 실패가 진행 중이던 예외를 **대체**해, 호출자가
    # `UnicodeEncodeError` 대신 `PermissionError`를 보고 70이 아니라 66으로
    # 분류한다 - `store/cache.py`의 같은 자리에 이 함정의 상세한 기록이 있다.
    tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(text + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _segment_doc(risk: SegmentRisk, segment: Segment) -> dict[str, Any]:
    """세그먼트 하나. `SegmentRisk`와 `Segment`를 조인한다."""
    return {
        "id": segment.id,
        # 타임코드가 이미 정수 밀리초라 변환이 없다 - §8.4가 내부 자료구조를
        # 거꾸로 규정했기 때문이다(`segment/models.py` 모듈 독스트링).
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "source_text": segment.source_text,
        "target_text": segment.target_text,
        "risk_score": risk.risk_score,
        "hard_fail": risk.hard_fail,
        "reasons": list(risk.reasons),
        "signals": [_signal_doc(s) for s in risk.signals],
    }


def _signal_doc(signal: Signal) -> dict[str, Any]:
    """신호 하나. `detail`은 통째로 싣는다(설계 D4).

    잘라내면 FR-6.4가 요구한 "왜 선별되었는지"의 증거가 사라진다 -
    `self_consistency.samples`는 "왜 이 번역이 불안정하다고 봤는가"의 유일한
    자료다. 크기는 selected만 담는 것(D3)과 Tier 1이 후보에만 도는 것
    (`max_ratio`)으로 이중 축소된다.

    **`dict(signal.detail)`은 얕은 복사다.** 최상위 dict만 새것이고 안의
    리스트·dict는 원본 `Signal`과 **같은 객체**다 - 중첩은 가상이 아니라
    `signals/derived.py`·`signals/llm.py`가 실제로 리스트를 담는다.
    따라서 `build_review`의 반환 문서는 **읽고 직렬화하는 용도**다.
    `detail["terms"].append(...)`처럼 제자리 수정을 하면 원본 신호가 오염된다.
    복제가 필요하면 호출자가 `copy.deepcopy`를 하라 - 여기서 하지 않는 이유는
    현재 경로가 반환 직후 직렬화라 `samples` 같은 큰 리스트를 통째로 복제할
    이유가 없기 때문이다.
    """
    return {
        "name": signal.name,
        # 계층을 키가 아니라 **값**으로 둔다 - 그래서 Tier 1·Tier 2가 붙어도
        # 스키마가 깨지지 않는다(설계 §1.3).
        "tier": signal.tier,
        "score": signal.score,
        # `side`를 뺄 수 없다 - FR-7.3 리포트가 원문과 번역문 중 어느 쪽을
        # 칠할지 가르는 유일한 판별자다.
        "spans": [{"start": s.start, "end": s.end, "side": s.side} for s in signal.spans],
        "detail": dict(signal.detail),
    }
