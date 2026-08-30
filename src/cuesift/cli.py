"""cuesift CLI 진입점.

요구사항정의서 §8.1(FR-8.1~8.5)의 커맨드 표면을 정의한다.
`check`·`translate`는 배선이 끝나 실제로 동작한다. `transcribe`는 아직
인자 스키마만 확정한 골격이라 EXIT_NOT_IMPLEMENTED로 종료한다.

**종료 코드 일곱이 서로 겹치지 않는 것이 이 파일의 계약이다.**

| 코드 | 언제 | 근거 |
| --- | --- | --- |
| 0 | 위반 없음, 또는 `--fail-on none`, 또는 전량 번역 성공 | |
| 1 | 규격 위반 발견 (`check`만) | FR-7.5 |
| 2 | 명령줄이 틀림 (파일 없음·디렉터리·프로파일 해석 실패·출력 경로 충돌) | typer 관행 |
| 3 | 번역되지 않은 세그먼트가 남음, 원문 유지 (`translate`만) | 이 파일이 정한다 |
| 66 | 파일 사정 (자막·용어집 파싱 실패, utf-8 아님, 읽거나 쓰지 못함) | `sysexits.h` EX_NOINPUT |
| 69 | 외부 서비스(LLM 프로바이더)가 요청을 거부함 | `sysexits.h` EX_UNAVAILABLE |
| 70 | 미구현(`transcribe`), 또는 산출물의 **내용** 결함 | `sysexits.h` EX_SOFTWARE |

**1을 진단 실패에 쓰지 않는 것이 핵심이다.** 1은 "규격 위반 발견"이므로
파일을 못 읽은 것을 1로 내면 CI가 "자막이 깨졌다"와 "경로가 틀렸다"에
같은 대응을 하게 되고, 사용자는 멀쩡한 자막을 고치려 든다.

**번역 실패도 1에서 뺐다.** 예전 표는 1을 `check`의 위반과 `translate`의
실패가 겸하게 두고 근거로 FR-7.5·FR-2.6을 댔는데, **FR-7.5는 `check`만
말하고 FR-2.6은 종료 코드를 언급하지 않는다.** 요구가 아니라 구현 선택이었고,
겸하는 동안 CI는 두 원인에 같은 대응을 했다.

**3이 `sysexits.h` 밖의 값인 것은 의도다.** 거기에는 "산출물은 나왔는데 일부가
비었다"가 없고, 뜻이 가장 가깝던 75(EX_TEMPFAIL, "다시 시도하라")는 실측으로
거짓이었다 - 캐시가 실패 응답을 보존해 재실행이 호출 0회로 같은 실패를 낸다.
"""

from __future__ import annotations

import errno
import math
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Annotated

import typer

from cuesift import __version__
from cuesift.config import Config, load_config
from cuesift.glossary import Glossary, load_glossary
from cuesift.ingest import IngestError, IngestResult, load_subtitle, write_subtitle
from cuesift.progress import ProgressReporter, clear_active, env_flag, install, resolve_style
from cuesift.report import (
    TriageOutcome,
    layer_tokens_reported,
    resolve_cost_scope,
    write_html,
    write_review,
)
from cuesift.risk import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals import SignalContext, collect_all
from cuesift.spec import (
    SpecProfile,
    SpecViolation,
    TrackViolation,
    check_track,
    load_builtin,
    load_profile,
)
from cuesift.store import CacheRequest, CachingProvider
from cuesift.tier1 import explain_zero_bound, triage_with_tier1
from cuesift.translate import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONTEXT_WINDOW,
    CountingProvider,
    FatalProviderError,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    SegmentFailure,
    TokenUsage,
    TranslationResult,
    build_messages,
    iter_batches,
    translate_segments,
)
from cuesift.triage import select_by_budget, select_by_threshold

# CI가 "미구현"과 "검수 실패"를 구분할 수 있도록 종료 코드를 분리한다.
#
# **발신처가 셋이다.** `transcribe`(미구현), `_translate_one`이 `write_review`의
# 직렬화 실패를 받는 자리(review.json 배선 FR-7.2가 더했다), 그리고 같은 함수가
# `write_subtitle`의 **내용** 실패를 받는 자리다. 따라서 CI는 셋을 **종료
# 코드만으로는 구별하지 못한다**. 구별이 필요하면 stderr 메시지를 봐야 한다 -
# 셋 다 예외 타입명을 병기한다. 이 사실이 여기 없으면 아무 데도 없다.
#
# **뒤의 둘은 `TypeError`·`OSError` 하나로 좁힐 수 없다.** `json.dumps`의 실패도
# `subs.save`의 실패도 열린 집합이라 두 자리 모두 `except Exception`으로 받는다 -
# 근거는 각 주석에 있다.
#
# **셋이 같은 코드인 것이 옳다.** `sysexits.h`의 EX_SOFTWARE는 "internal
# software error"라 셋을 함께 덮는다 - 사용자가 입력이나 설정을 고쳐도 사라지지
# 않는 우리 쪽 결함이라는 점이 같다. 66(EX_NOINPUT)으로 보내면 "사용자가 고칠
# 수 있는 문제"가 되어 **"자막이 깨졌다"로 오독되고 멀쩡한 자막을 고치려 든다** -
# 1을 진단 실패에 쓰지 않는 이유(파일 머리말)와 같은 사고다.
#
# **이름이 좁아졌다.** `EXIT_NOT_IMPLEMENTED`는 이제 발신처 셋 중 하나만 말한다.
# 바꾸려면 `transcribe`와 그 테스트를 함께 건드려야 해 배선 태스크의 범위를
# 넘는다 - 이 문단이 그때까지 이름을 대신한다.
EXIT_NOT_IMPLEMENTED = 70

# sysexits.h EX_NOINPUT — 파일 내용이 틀렸다는 뜻이다. 명령줄이 틀린 2와 구분한다.
# CI가 둘을 구분하지 못하면 "경로 오타"와 "자막이 깨졌다"에 같은 대응을 하게 된다.
EXIT_BAD_INPUT = 66

# sysexits.h EX_UNAVAILABLE — 외부 서비스가 요청을 거부했다는 뜻이다.
# 70("미구현·내부 오류")과 나누는 이유는 CI가 "아직 안 만든 기능"과
# "LLM 서버가 401을 냈다"에 같은 대응을 하면 안 되기 때문이다.
EXIT_UNAVAILABLE = 69

# "산출물은 나갔는데 번역되지 않은 세그먼트가 남았다". **그 이상은 주장하지
# 않는다** - 다시 돌리면 되는지는 사유에 달렸고, 사유는 화면이 말한다
# (`_format_failure_lines`).
#
# **1에서 갈라져 나온 값이다.** 이전에는 `check`의 "규격 위반 발견"과 같은 1이라
# CI가 "자막이 규격을 어겼다"와 "번역이 실패했다"에 같은 대응을 했다. 요구가
# 그것을 시킨 적은 없다 - FR-7.5는 `check`만 말하고 FR-2.6은 "실패 표시 후
# 진행"만 요구하며 종료 코드를 언급하지 않는다.
#
# **`sysexits.h`를 쓰지 않는다.** 거기에는 "산출물은 나왔는데 일부가 비었다"에
# 해당하는 값이 없다. 후보였던 EX_TEMPFAIL(75)은 "다시 시도하면 된다"를 뜻하는데
# 이 명령은 그것을 알 수 없다 - 사유 3종의 처방이 서로 다르기 때문이다
# (`provider_error`는 재실행, `invalid_response`는 모델 교체,
# `empty_translation`은 원문 확인). 하나의 코드가 그중 "재시도"만 주장하면
# 나머지 둘에서 거짓이다.
#
# **재시도가 실제로 아무것도 바꾸지 않는 사유가 남아 있다** -
# `empty_translation`은 개수도 번호도 맞은 계약을 지킨 응답이라 캐시가 보존하고
# (`translate/engine.py`의 `_discard_cached`: 폐기 대상은 `invalid_response`뿐이다),
# 같은 모델·같은 설정의 재실행은 캐시 히트로 같은 결과를 낸다. 75였다면 CI가
# 재시도로 읽어 아무것도 달라지지 않는 루프가 된다.
#
# **`invalid_response`에 대해서는 2026-08-29에 사정이 바뀌었다** - 파싱조차 안 된
# 응답은 이제 캐시에서 폐기되므로(파킹 #13) 재실행이 실제로 다시 호출한다. 그래도
# 75로 바꾸지 않는 이유는 위와 같다: 같은 모델이 같은 지시를 다시 어길 것이라는
# 쪽에 걸 근거가 없고, 코드 하나가 세 사유를 대표할 수 없다.
#
# **69와 나누는 축은 "산출물이 나갔는가"다.** 69는 프로바이더가 요청을 거부해
# 그 언어가 통째로 죽은 것이고, 3은 파일이 나왔는데 일부 줄이 원문으로 남은
# 것이다.
#
# **이름이 "partial"이 아닌 이유**: 판정이 `if translated.failures`라 전량
# 실패도 이 코드로 나간다. `EXIT_PARTIAL_FAILURE`였다면 이름이 거짓이었다.
EXIT_TRANSLATION_FAILURE = 3

# **값의 크기가 아니라 이 순서가 우선순위다.** `max()`로 합치면 코드 값의 크기가
# 우선순위가 되는데 그것은 우연히만 맞는다. 이 튜플이 없으면 그 회귀가 조용히
# 들어온다.
#
# **70이 맨 앞인 것이 이 순서의 핵심이다.** 70은 출력 자막·검수 리포트·HTML을
# 쓰지 못한 것이라 **사용자가 LLM 설정을 고쳐도 사라지지 않는다.** 69는 고치면
# 사라진다. 한 언어가 프로바이더 거부이고 다른 언어가 직렬화 실패라면 보고할
# 것은 우리 쪽 결함이다.
#
# **69가 조기 break를 걸기 때문에 더 그렇다.** en에서 70이 난 뒤 ja에서 401이
# 나면 호출부가 남은 언어를 건너뛰며 끝나는데, 여기서 69가 이기면 70은 다음
# 실행까지 숨는다 - 그 다음 실행도 401이면 영영 안 보인다.
#
# 앞에 있을수록 근본적이다: 우리 쪽 결함 > 서비스가 죽은 것 > 파일 사정 >
# 번역 실패. `_translate_one`이 내는 값은 이 넷뿐이다(2는 파싱 단계에서
# `typer.Exit`으로 먼저 나가므로 여기 오지 않는다).
_EXIT_PRIORITY = (
    EXIT_NOT_IMPLEMENTED,
    EXIT_UNAVAILABLE,
    EXIT_BAD_INPUT,
    EXIT_TRANSLATION_FAILURE,
)


def _combine_exit_codes(codes: Iterable[int]) -> int:
    """대상 언어별 종료 코드를 하나로 합친다.

    **0은 "아무 일 없음"이라 언제나 진다.** 하나라도 실패가 있으면 그것이 나간다.
    **`_EXIT_PRIORITY`에 없는 값도 0으로 만들지 않는다** - 새 코드를 넣고 표에
    등록하지 않은 것이 조용히 성공으로 나가면 CI가 실패를 통과로 읽는다.
    미등록 값이 둘 이상이면 그중 **가장 작은 값**을 낸다(임의 선택이지만
    결정적이라 같은 실행이 같은 코드를 낸다). 표에 넣어 순서를 정하는 것이
    옳고, 이 갈래는 그때까지의 그물이다.

    **집합으로 받으므로 인자 순서에 좌우되지 않는다.** `--to en,ja`와 `ja,en`이
    다른 종료 코드를 내면 CI 판정이 언어 나열 순서로 갈린다.
    """
    seen = {c for c in codes if c}
    if not seen:
        return 0
    for code in _EXIT_PRIORITY:
        if code in seen:
            return code
    return sorted(seen)[0]


# 자동 탐색은 현재 디렉터리 한 칸뿐이다(설계 D2). 상위로 올라가면 사용자가
# 존재를 모르는 파일이 검수 기준을 바꾸고, 가중치와 hard fail 임계가 실린
# 파일에서 그것은 Recall@Budget 수치를 조용히 오염시킨다.
_DEFAULT_CONFIG_NAME = "cuesift.yaml"

# 기본 캐시 위치. 프로젝트 디렉터리 안에 두는 것은 `.gitignore`에 한 줄로
# 넣을 수 있고 작업물과 함께 옮겨지기 때문이다.
DEFAULT_CACHE_DIR = Path(".cuesift/cache")

# BCP 47의 실용적 부분집합. `--to` 값은 검증 없이 `_output_path`를 거쳐
# 그대로 파일 시스템 경로 조각이 된다 - 실측(WP7b Task 4 리뷰 라운드 1):
# `--to 'e:n'`은 Windows NTFS 대체 데이터 스트림으로 해석돼 디렉터리엔
# `minimal.e`(0바이트)만 보이고 내용은 숨은 `:n.srt` 스트림에 실리는데
# 도구는 "성공"을 보고한다. `--to '../pwned'`는 `--out` 밖에 쓰거나
# `FileNotFoundError`가 새어 exit 1로 오보된다. 완전한 BCP 47 파서 대신
# "영문자로 시작하고 경로 구분자·콜론을 포함하지 않는다"는 최소 계약만
# 강제한다 - Q2가 초기 언어쌍을 ko→en/ja로 정했지만 §8.1 예시 호출 형태는
# en,ja,th,vi이고 `zh-Hans` 같은 서브태그도 받아야 하므로 2~3자 1개로
# 좁히지 않는다.
_LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]+)*$")

# Tier 1 기본값과 비용 한도 (FR-4.3 · 설계 D3·D4 · §11 R8).
#
# **셋 다 출처가 있다.** R8이 출처 없는 수치를 기본값으로 넣는 것을 금지한다.
# 여기 값이 라이브러리 기본값과 갈라지면 `--tier1-samples`를 주지 않은 실행과
# 준 실행의 비용 산식이 서로 다른 수를 쓰게 된다 - 아래 한도 검사가 실제
# 호출 비용과 어긋나는 값을 검사하게 되는 것이다.
_TIER1_DEFAULT_MAX_RATIO = 0.05
_TIER1_DEFAULT_SAMPLES = 3  # `triage_with_tier1`의 현행 기본값 (tier1.py:30)
_TIER1_DEFAULT_TEMPERATURE = 1.0  # OpenAI Chat Completions API 명세의 기본값

# 기준선 대비 배수 = samples x max_ratio x DEFAULT_BATCH_SIZE 이고, 요구사항정의서
# §4가 "3배는 감당 불가"라 했다. 10(DEFAULT_BATCH_SIZE)으로 나눈 값이 이 한도다.
#
# **이 값을 넘기면 Tier 1 호출이 번역 기준선의 3배를 넘는다** - §4가 막으려던
# 바로 그 영역이다. 반대로 samples 단독 상한으로 바꾸면
# `--tier1-samples 10 --tier1-max-ratio 0.5`(배수 50)가 통과한다 - 상한이
# 잘못된 축에 걸리기 때문이다(설계 D4).
#
# **배치 프로토콜로 바뀌면 이 상수를 다시 계산해야 한다**(설계 §6.2) -
# 그때는 DEFAULT_BATCH_SIZE가 산식에서 빠져 한도가 3.0이 된다.
_TIER1_COST_LIMIT = 0.3

# Tier 1 후보가 0건일 때 사유를 내는 줄의 **접두 문구**(Ruling P12).
#
# **리터럴로 두면 안 된다.** 테스트 `_assert_tier1_ran`이 "이 줄이 없다"는
# **부재**로 후보 유무를 판정하는데, 접두를 여기서만 바꾸면 그 단언이 조용히
# 항상 참이 되어 무연산 게이트가 된다(리뷰 축2 실측: `"Tier 1: "`를
# `"Tier 1 - "`로 바꾸는 것만으로 사망 0건, 도달성 프로브 사망도 8건에서
# 4건으로 반토막). 상수를 공유하면 문구와 게이트가 함께 움직인다.
#
# **출력 문자열이라 em dash를 쓰지 않는다**(전역 제약, cp949 미인코딩).
_TIER1_WARN_PREFIX = "Tier 1: "

# `--dry-run`이 내는 Tier 1 **재번역 요청 상한** 줄의 접두 문구(설계 D10).
#
# **낱말이 "호출"이면 무엇이 깨지는가.** 이 수는 `floor(n x max_ratio) x
# samples`가 내는 **재번역 요청** 수의 상한이지 **프로바이더 호출** 수의
# 상한이 아니다 - 요청 하나가 `translate_segments`를 타고 재시도
# (`translate/engine.py`의 `DEFAULT_MAX_RETRIES`)와 개별 폴백까지 쓰므로
# 실제 호출은 이 수를 넘길 수 있다. "호출"로 적으면 두 줄 위의 형제 줄
# (`호출 필요 N개 이상`)과 **한 화면에서 정면으로 모순된다** - 같은 낱말이
# 한쪽에서는 하한, 다른 쪽에서는 상한을 말하고, 429를 되돌려 주는 백엔드에서
# 화면은 그 몇 배를 감춘다(리뷰 라운드 1 Important A). 그래서 낱말로 층을
# 가르고 괄호에 `재시도·폴백 제외`를 명시한다.
#
# **"예상"이 아니라 "최대"다.** 상한은 산식에서 나오므로 요구사항정의서
# §11 R8이 금지하는 출처 없는 **추정**이 아니다 - "예상"으로 적는 순간 이
# 줄이 그 금지에 걸린다. 반대로 재시도 상수를 곱한 "최대 120회" 같은 수를
# 여기 싣는 것도 R8 위반이다 - 재시도 횟수는 백엔드 사정이라 산식이 내는
# 수가 아니다.
#
# 상수로 두는 이유는 `_TIER1_WARN_PREFIX`와 같다 - 테스트가 "`--tier1`이
# 꺼져 있으면 이 줄이 없다"는 **부재**로도 단언하므로, 문구가 리터럴이면
# 그 단언이 조용히 항상 참이 된다. **다만 상수만 공유하면 필터와 기대값이
# 함께 움직여 문구 변이가 안 잡힌다**(리뷰 라운드 1 Important B 실측: 사망
# 0건) - `test_상한_줄의_문구를_리터럴로_못_박는다` 한 건이 이 문자열을
# 상수를 거치지 않고 리터럴로 못 박는 것은 그 때문이다.
#
# **출력 문자열이라 em dash를 쓰지 않는다**(전역 제약, cp949 미인코딩).
_TIER1_BOUND_PREFIX = "Tier 1 재번역 요청 최대 "

app = typer.Typer(
    name="cuesift",
    # em dash(U+2014)를 쓰지 않는다. 이 문자열은 `--help`로 출력되는데
    # cp949는 U+2014를 인코딩하지 못한다(실측). `·`(U+00B7)는 인코딩되므로 남긴다.
    help="AI 자막 번역·검수 트리아지 엔진. 사람이 정말 봐야 할 자막만 걸러냅니다.",
    no_args_is_help=True,
    add_completion=False,
)


class FailOn(StrEnum):
    """FR-7.5 — 어느 심각도부터 CI를 실패시킬지.

    **v0.1에서 `hard`와 `any`는 같은 결과를 낸다.** 규격 위반 7종이 전부 같은
    등급이기 때문이다(설계 §5.1). 등급을 나누려면 배정의 출처가 필요한데
    1차 출처인 Netflix TTSG에 위반 등급 구분이 없고, 요구사항정의서 §11 R8이
    "출처 없는 수치를 기본값으로 넣지 않음"을 명시한다.

    이름을 `soft`·`never`에서 바꾼 것은 요구사항정의서가 단일 진실 원천이기
    때문이다. `soft`는 v0.1에 존재하지 않는 등급을 가리킨다.
    """

    hard = "hard"
    any = "any"
    none = "none"


class ReviewFormat(StrEnum):
    """FR-7.3 - `--review-out`이 무엇을 내는지 (설계 D1).

    **기본이 `JSON`이 아니면 기존 실행의 산출물이 조용히 늘어난다.**
    `HTML`이나 `BOTH`가 기본이면 `--review-format`을 준 적 없는 CI 파이프라인의
    리포트 디렉터리에 `.report.html`이 새로 쌓이는데, 종료 코드는 그대로 0이라
    아무 신호도 나지 않는다. 산출물 집합이 바뀌는 것은 **옵션을 새로 준
    실행에서만** 일어나야 한다.

    `HTML`은 JSON을 **대체한다 - 곁들이지 않는다.** 곁들이면 `BOTH`가 가리킬
    것이 없어져 세 값이 두 동작으로 무너진다.

    `StrEnum`인 것은 `FailOn`과 같은 이유다 - typer가 `--review-format` 값을
    Enum 멤버로 검증해 세 값 밖의 문자열이 우리 코드에 닿기 전에 exit 2로
    끝난다. 우리가 문자열을 파싱하면 그 검증이 사라진다.
    """

    JSON = "json"
    HTML = "html"
    BOTH = "both"


def _not_implemented(command: str) -> None:
    # `typer.secho`는 `_echo`를 지나지 않는다. 진입점의 `_TolerantOutput`이 이 경로도
    # 덮지만, 닫힌 파이프에서 여기서 예외가 새면 아래 `typer.Exit(70)`에 도달하지 못해
    # **70이 조용한 0이 된다**(실측된 회귀). 방어를 쓰기 지점에 함께 둔다.
    try:
        typer.secho(
            f"'{command}'는 아직 구현되지 않았습니다 (골격 단계). "
            f"진행 상황: https://github.com/withwooyong/cuesift/issues",
            fg=typer.colors.YELLOW,
            err=True,
        )
    except OSError as exc:
        if not _is_closed_output(exc):
            raise
        _discard_stream(sys.stderr)
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cuesift {__version__}")
        raise typer.Exit(0)


def _harden_output_streams() -> None:
    """인코딩할 수 없는 문자가 프로세스를 죽이지 못하게 한다.

    **출력에 실리는 것은 우리 리터럴만이 아니다** — 사용자가 준 파일 경로와 `--spec`
    경로가 그대로 들어간다. Windows 기본 로케일(cp949)에서 인코딩할 수 없는 문자가
    그 안에 있으면 리다이렉트 시 `UnicodeEncodeError`로 프로세스가 죽고 **종료 코드 1**이
    나간다. 이 저장소에서 1은 "규격 위반 발견"이므로 **위반 0건인 깨끗한 파일이 CI에서
    실패로 읽힌다.** 실측된 사례: `Amélie.srt`(U+00E9) · `S01E01 – ko.srt`(U+2013).
    이모지·간체 한자·NBSP도 같다. 자막 파일명에 흔한 문자들이다.

    **em dash 금지는 우리가 쓰는 리터럴만 통제하고 사용자 입력이 흐르는 이 경로는
    못 막는다.** 그래서 규칙이 아니라 스트림 설정으로 닫는다.

    **하중을 받는 것은 stdout 하나뿐이다.** 4방향 변이로 실측한 결과, 이 함수를 통째로
    꺼도 깨지는 것은 `typer.echo`가 stdout에 쓰는 경로뿐이었다. stderr 경로(exit 66의
    `IngestError` 메시지)와 click의 오류 렌더(exit 2)는 **click이 자기 스트림에 이미
    `backslashreplace`를 걸어** 하드닝 없이도 통과한다.

    그럼에도 stderr까지 걸고 `check()`가 아니라 그룹 콜백에서 부르는 이유는 둘이다.

    1. click 내부 동작에 기대는 것은 이 저장소가 반복해 지적한 "열거는 계약이 아니라
       관찰"과 같은 형태다. click이 stderr를 언제까지 감싸 줄지는 우리 계약이 아니다.
    2. `translate`·`transcribe`가 구현되면 같은 문제를 각자 다시 풀어야 한다.

    그룹 콜백은 서브커맨드 인자 검증보다 먼저 돈다(실측: 콜백 → 인자 검증 → 본문).
    **다만 `--help`·`--version`은 eager 옵션이라 콜백보다도 먼저 렌더되므로 여기가
    닿지 않는다** — 그쪽은 리터럴에서 em dash를 빼는 것으로만 막을 수 있고,
    `test_help_output_is_encodable_in_the_cp949_locale`이 그것을 고정한다.

    `reconfigure`가 없는 스트림은 건너뛴다. `io.StringIO`로 stdout을 갈아 끼우고
    `app()`을 부르는 호출자가 있으면 `AttributeError`로 죽는데, 그것이야말로 이
    함수가 막으려던 종류의 사고다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


# 하류가 파이프를 먼저 닫았을 때 나오는 errno. **플랫폼마다 다른 것이 함정이다.**
# POSIX는 `BrokenPipeError`(EPIPE)지만 **Windows는 평범한 `OSError` errno 22(EINVAL)**이고
# `isinstance(exc, BrokenPipeError)`가 False다(실측). `except BrokenPipeError`만 다는 해법은
# Linux에서만 동작하고 개발 플랫폼에서는 조용히 안 먹는다.
#
# **`OSError`를 통째로 삼키지 않는 이유**는 디스크 가득 참(ENOSPC)이다. 리다이렉트 중
# ENOSPC를 삼키면 잘린 출력이 종료 코드 0으로 나가 "검사하지 않고 통과하는 게이트"가 된다.
_CLOSED_OUTPUT_ERRNOS = frozenset({errno.EPIPE, errno.EINVAL})


def _is_closed_output(exc: OSError) -> bool:
    """하류가 먼저 닫은 파이프인가. 그것은 오류가 아니라 `head`·`less`의 정상 동작이다."""
    return exc.errno in _CLOSED_OUTPUT_ERRNOS


def _discard_stream(stream: IO[str]) -> None:
    """스트림의 fd를 `os.devnull`로 갈아 끼워 이후 쓰기를 무해하게 만든다.

    **이것이 없으면 종료 코드가 120으로 덮인다.** 방출 지점에서 예외를 삼켜도
    인터프리터가 종료할 때 `sys.stdout`을 다시 flush하고, 그 flush가 터지면 CPython이
    "Exception ignored"를 찍고 **120으로 끝낸다**(실측: 그대로 두면 120, dup2하면 0).
    파이썬 객체를 바꾸는 것으로는 부족하고 **fd 자체**를 갈아 끼워야 하는 이유가 그것이다.

    **실패한 스트림만 넘겨야 한다.** stdout이 파이프이고 stderr가 터미널인
    `cuesift check bad.srt --spec ko | head -1`에서 stderr까지 죽이면
    사용자가 진단 메시지를 잃는다.

    fd가 없는 스트림(`CliRunner`의 인메모리 래퍼, `io.StringIO`)은 건너뛴다 —
    `fileno()`가 `io.UnsupportedOperation`을 내는데 그것은 `OSError`이자 `ValueError`다.
    """
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return

    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:  # pragma: no cover - devnull을 못 여는 환경은 재현 수단이 없다
        return
    try:
        os.dup2(devnull, fileno)
    finally:
        os.close(devnull)


class _TolerantOutput:
    """닫힌 파이프에 쓰는 것을 무해하게 만드는 프록시 (설계 §7.1).

    **종료 코드를 지키는 유일하게 균일한 방법이다.** 예외를 나중에 잡는 방식은
    "누가 썼는가"에 따라 구멍이 난다 — 종료 코드 2는 click의 `UsageError.show()`가,
    70은 `typer.secho`가 쓰므로 커맨드 본문의 방어가 닿지 않는다. 실측된 회귀:
    `cuesift check nope.srt --spec ko 2>&1 | head -0`이 2가 아니라 **0**으로 나갔고,
    `transcribe`도 70이 아니라 0이었다. **120은 시끄럽지만 0은 조용히 CI를 통과시킨다.**
    쓰기 지점 자체를 무해하게 만들면 어느 코드 경로가 쓰든 같은 결과가 된다.

    **프록시 패턴은 click의 선례다** — `PacifyFlushWrapper`가 같은 목적으로
    `__getattr__` 위임을 쓴다. `isatty`·`encoding`·`fileno`·`buffer` 같은 기능 탐지가
    그대로 통과해야 rich와 click이 정상 동작한다.

    **부수 효과로 플랫폼 차이도 사라진다.** click의 `_main`은 `errno == EPIPE`일 때만
    `sys.exit(1)`을 하는데(typer/core.py) POSIX는 EPIPE, Windows는 EINVAL이라
    같은 사고가 Linux에서는 1, Windows에서는 우리 처리로 갔다. 여기서 막으면
    click의 그 분기에 애초에 도달하지 않는다.

    `ENOSPC`는 그대로 올린다 — 삼키면 잘린 출력이 성공으로 보고된다.
    """

    def __init__(self, wrapped: IO[str]) -> None:
        self.wrapped = wrapped
        self.downstream_closed = False

    def write(self, data: str) -> int:
        if self.downstream_closed:
            return len(data)
        try:
            return self.wrapped.write(data)
        except OSError as exc:
            if not _is_closed_output(exc):
                raise
            self._give_up()
            return len(data)

    def flush(self) -> None:
        if self.downstream_closed:
            return
        try:
            self.wrapped.flush()
        except OSError as exc:
            if not _is_closed_output(exc):
                raise
            self._give_up()

    def _give_up(self) -> None:
        """이 스트림만 포기한다. **다른 스트림은 건드리지 않는다.**

        stdout이 파이프이고 stderr가 터미널인 `check bad.srt --spec ko | head -1`에서
        stderr까지 버리면 사용자가 진단 메시지를 잃는다.
        """
        self.downstream_closed = True
        _discard_stream(self.wrapped)

    def __getattr__(self, attr: str) -> object:
        return getattr(self.wrapped, attr)


def _echo(message: str = "", *, err: bool = False) -> None:
    """커맨드 본문의 출력. 닫힌 파이프에서도 **종료 코드를 지킨다.**

    `_TolerantOutput`이 설치되면 여기까지 예외가 오지 않지만, 이 방어를 남겨 두는 것은
    `app()`을 직접 부르는 호출자(테스트·라이브러리 사용)가 프록시를 못 받기 때문이다.
    그때 예외가 본문을 빠져나가면 `check()`가 `typer.Exit(1)`에 도달하지 못해
    **위반을 찾고도 종료 코드가 1이 아니게 된다.**

    **쓰기 전에 진행 줄을 지운다**(FR-8.5 · 설계 D11). `\\r`이 떠 있는 중에
    메시지가 나가면 두 문장이 한 줄에 겹친다. `err` 여부와 무관하게 지우는
    것은 대화형 터미널에서 stdout과 stderr가 **같은 tty**이기 때문이다 -
    `_tier1_warn`은 의도적으로 stdout으로 나간다. stdout이 리다이렉트된
    경우 `clear_active()`는 stderr만 건드리므로 손해가 없다.
    """
    clear_active()
    stream = sys.stderr if err else sys.stdout
    try:
        typer.echo(message, err=err)
    except OSError as exc:
        if not _is_closed_output(exc):
            raise
        _discard_stream(stream)


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="버전을 출력하고 종료합니다.",
        ),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            # `--help`로 출력되는 문자열이므로 em dash를 쓰지 않는다(전역 제약).
            #
            # **이제는 현재형이 참이다.** 자동 탐색과 우선순위 해결이 아래
            # `_apply_config`에 있다. 미구현 문구를 남겨 두면 이번에는
            # 반대 방향의 거짓말이 된다(설계 §4.3 함정 ③).
            help="설정 파일 경로 (FR-8.4). 기본은 현재 디렉터리의 ./cuesift.yaml입니다. "
            "CLI 인자가 설정 파일보다 우선합니다.",
        ),
    ] = None,
) -> None:
    """공통 옵션."""
    _harden_output_streams()
    _apply_config(ctx, config)


def _discover_config() -> Path | None:
    """`--config`가 없을 때 현재 디렉터리 한 칸을 본다 (FR-8.4 · 설계 D2).

    **함수로 분리한 이유는 테스트가 이 한 지점만 무력화하기 위해서다.**
    자동 탐색은 cwd에 의존하므로, 리포 루트에 `cuesift.yaml`을 한 줄 둔
    개발자의 로컬에서 CLI 기본값이 통째로 바뀌어 **CI에는 없는 실패**가
    난다(실측: `dry_run: true` 한 줄로 81 failed). 이 저장소는 로컬과 CI의
    게이트가 갈려 CI 5회 연속 실패가 숨은 전례가 있어, 반대 방향이어도 같은
    부채다. `tests/conftest.py`의 autouse fixture가 여기를 껐다가, 자동
    탐색 자체를 재는 테스트에서만 opt-in fixture로 되살린다.

    상위로 올라가지 않는다 - 사용자가 존재를 모르는 파일이 검수 기준을
    바꾸면 Recall@Budget 수치가 조용히 오염된다.

    `Path`가 상대라서 **매 호출의 cwd**를 본다. 모듈 임포트 시점이 아니다.
    """
    candidate = Path(_DEFAULT_CONFIG_NAME)
    return candidate if candidate.is_file() else None


def _apply_config(ctx: typer.Context, config: Path | None) -> None:
    """설정 파일을 읽어 `ctx`에 싣는다 (FR-8.4 · 설계 §4.2).

    **우선순위 해결 코드를 쓰지 않는다.** click이 파라미터 **단위로**
    `COMMANDLINE > DEFAULT_MAP > DEFAULT`를 해결한다(설계 D1 · P2). 손으로
    병합하면 22개 옵션의 기본값을 전부 `None` 센티널로 옮겨야 하고, 그러면
    `--help`의 기본값 표시가 사라진다.

    **`_harden_output_streams()` 뒤여야 한다.** 출처 줄에 사용자가 준 경로가
    그대로 실리므로, 하드닝 전에 쓰면 cp949로 인코딩할 수 없는 경로에서
    `UnicodeEncodeError`가 나고 종료 코드 1("규격 위반 발견")로 오보된다.

    **`typer.BadParameter`로 던지는 이유**는 `--spec`의 선례를 따르기
    때문이다(설계 D10) - 설정 파일은 명령줄의 연장이므로 종료 코드가 2다.
    66으로 보내면 "자막 파일이 깨졌다"로 오독된다.
    """
    if config is None:
        source_or_none = _discover_config()
        if source_or_none is None:
            # 없으면 조용히 넘어간다. **이것이 정상 경로다** - 여기서
            # 경고를 내면 설정 파일을 쓰지 않는 사용자가 매 실행마다
            # stderr 한 줄을 받는다.
            return
        source = source_or_none
    elif config.is_dir():
        # `is_file()` 하나로 묶으면 **존재하는 디렉터리에 "없다"고 답한다.**
        # 사용자는 있는 경로를 노려보며 오타를 찾게 된다.
        raise typer.BadParameter(
            f"{config}: 디렉터리다. 설정 파일 경로를 준다", param_hint="--config"
        )
    elif not config.is_file():
        raise typer.BadParameter(f"{config}: 설정 파일이 없다", param_hint="--config")
    else:
        source = config

    try:
        cfg: Config = load_config(source)
    except OSError as exc:
        # `load_config`는 내용 오류를 전부 `ValueError`로 정규화하지만 읽기
        # 자체의 실패(권한·잠금)는 `OSError`로 샌다. 여기서 받지 않으면
        # 미처리 traceback이 종료 코드 1로 오보된다.
        raise typer.BadParameter(f"{source}: {exc}", param_hint="--config") from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc

    try:
        ctx.default_map = cfg.to_default_map()
    except ValueError as exc:
        # `targets`의 list->str 변환만이 여기서 실패할 수 있다(설계 §5 2행).
        raise typer.BadParameter(str(exc), param_hint="--config") from exc

    # `signals.weights`는 CLI 옵션이 아니라 `ctx.obj`로 간다(설계 D6).
    # `_config_weights`가 여기서 꺼내 `fuse(weights=)`까지 내려보낸다.
    ctx.obj = cfg

    # **출처를 낸다**(설계 D7). click의 오류 메시지가 `Invalid value for
    # '--review-format'`이라 그 옵션을 친 적 없는 사용자가 명령줄을
    # 노려보게 된다. 자동 탐색(D2)이 있으면 특히 필요하다.
    #
    # **stderr다.** 산출물이 아니라 실행 조건 보고이므로
    # `cuesift check ... > violations.txt`를 오염시키면 안 된다(설계 §7.1).
    _echo(f"설정을 읽었다: {source}", err=True)


def _config_weights(ctx: typer.Context | None) -> Mapping[str, float] | None:
    """설정 파일의 신호 가중치를 꺼낸다 (FR-8.4 · FR-6.1 · 설계 D6).

    `ctx.obj`는 `_apply_config`가 심는다. 설정이 없으면 `None`이고 그때
    `fuse`가 `DEFAULT_WEIGHTS`를 쓴다 - **빈 딕셔너리를 내면 안 된다**.
    `fuse`의 `total_weight <= 0` 경로로 흘러 전량이 같은 점수가 되고,
    그러면 예산 선별이 id 순서로 뽑는다.

    CLI 옵션을 두지 않는 이유는 D6이다 - 10개 실수를 명령줄에 쓰는 것은
    쓸모가 없고, 두면 "설정 파일 전용 값"이라는 범주가 사라진다.
    """
    cfg = getattr(ctx, "obj", None)
    return getattr(cfg, "weights", None)


def _from_config(ctx: typer.Context | None, name: str) -> bool:
    """이 파라미터의 값이 설정 파일에서 왔는가 (FR-8.4 · 설계 D3).

    **`typer._click`을 임포트하지 않는다.** 벤더링된 private 경로라 typer
    업그레이드가 위치를 바꾸고, 그러면 임포트가 죽는 것이 아니라 이 판정이
    조용히 거짓이 되어 설정 파일이 환경변수를 다시 이긴다.
    `ParameterSource`는 이름 문자열로 본다.
    """
    if ctx is None:
        return False
    try:
        source = ctx.get_parameter_source(name)
    except (AttributeError, KeyError):
        return False
    return getattr(source, "name", "") == "DEFAULT_MAP"


def _resolve_exclusive(ctx: typer.Context | None, message: str, first: str, second: str) -> str:
    """상호배타 두 파라미터 중 **버릴 쪽**의 이름을 낸다 (FR-8.4 · 설계 D3).

    **값의 존재만 보는 상호배타 검사는 설정 파일을 이길 방법을 없앤다.**
    `cuesift.yaml`에 `triage.review_threshold`가 있으면 `--review-budget`을 친
    사람이 exit 2를 받는데, 그는 `--review-threshold`를 쓴 적이 없다. 그래서
    이 쌍에서만 FR-8.4 본문의 후반절이 통째로 뒤집힌다.

    **양보는 한쪽만 설정에서 왔을 때뿐이다.** 둘 다 설정에서 왔으면 설정
    파일 자체가 모순이고, 어느 쪽을 버려도 사용자가 적은 정책 하나가 조용히
    사라진다 - 그것이 D4가 막는 실패다. 둘 다 명령줄이면 원래의 사용법
    오류다. 두 경우 모두 여기서 exit 2로 끝내므로 반환은 늘 이름 하나다.
    """
    from_first = _from_config(ctx, first)
    from_second = _from_config(ctx, second)
    if from_first != from_second:
        return first if from_first else second
    if from_first:
        # 출처를 밝힌다(설계 D7). 사용자는 이 옵션들을 친 적이 없다.
        message = f"{message} (설정 파일에 둘 다 있다)"
    _echo(message, err=True)
    raise typer.Exit(2)


def _prefer_env(
    ctx: typer.Context | None, name: str, value: str | None, env_name: str
) -> str | None:
    """우선순위를 적용한다 - CLI > 환경변수 > 설정 파일 (설계 D3).

    **`value or os.environ.get(...)`만 쓰면 설정 파일이 환경변수를 이긴다.**
    `default_map`이 채운 값도 `or`의 왼쪽에서 참이기 때문이다. `value`가
    어디서 왔는지는 `ctx`만 안다 - 값만 봐서는 구별할 수 없다.

    **두 파라미터가 같은 헬퍼를 쓰는 것이 중요하다.** `base_url`만 고치면
    `model`이 반대 순서로 남고, 그 어긋남은 값이 양쪽 다 나오므로
    종료 코드로 드러나지 않는다.
    """
    env = os.environ.get(env_name)
    if env and _from_config(ctx, name):
        return env
    return value or env


def _prefer_env_bool(
    ctx: typer.Context | None, name: str, value: bool | None, env_name: str
) -> bool | None:
    """불리언 3상에 우선순위를 적용한다 - CLI > 환경변수 > 설정 파일 (설계 D5).

    위 `_prefer_env`의 불리언 형제다. 문자열 판본을 그대로 쓸 수 없는 이유는
    `False`가 falsy라 `value or env`가 `--no-progress`를 조용히 무시하기
    때문이다 - 그것이 "감지가 틀린 환경의 탈출로"라는 플래그의 존재 이유를
    없앤다. `test_진행_False가_falsy라서_삼켜지지_않는다`가 이 한 줄을 건다.

    환경변수 판독은 `progress.env_flag` 하나만 쓴다. 규칙이 두 곳에 생기면
    `CUESIFT_PROGRESS=false`가 참이 되는 날이 온다.
    """
    env = env_flag(env_name)
    if env is not None and _from_config(ctx, name):
        return env
    return env if value is None else value


def _resolve_llm(
    ctx: typer.Context | None, base_url: str | None, model: str | None
) -> tuple[str, str, str | None]:
    """LLM 접속 설정을 해결한다 (설계 §6.3).

    우선순위는 **CLI 옵션 > 환경변수 > 설정 파일**이다(FR-8.4 · 설계 D3).
    마지막 한 칸은 `ctx.default_map`이 채우고, 그것을 환경변수 아래로
    내리는 것이 `_prefer_env`다.

    **`ctx`가 `None`이면 설정에서 온 값이 아니라고 본다.** 라이브러리
    사용자가 직접 부르는 경로에서 `CLI > 환경변수`의 옛 계약이 그대로
    유지된다.

    **기본값을 넣지 않는다.** `localhost:11434`를 기본으로 두면 Ollama가
    없는 사람이 연결 실패를 받는데, 그것은 "설정을 안 했다"보다 진단이
    훨씬 어렵다.

    `api_key`를 명령줄로 받지 않는 이유는 셸 히스토리와 `ps` 출력에
    남기 때문이다.

    환경변수 이름에 `CUESIFT_LIVE_` 접두사를 쓰지 않는 것이 중요하다 —
    그것은 테스트 전용으로 예약돼 있고 `tests/test_translate_api.py`의
    게이트가 그 문자열로 live 마커 누락을 판정한다.
    """
    resolved_base = _prefer_env(ctx, "base_url", base_url, "CUESIFT_BASE_URL")
    resolved_model = _prefer_env(ctx, "model", model, "CUESIFT_MODEL")
    missing = [
        name
        for name, value in (("--base-url", resolved_base), ("--model", resolved_model))
        if not value
    ]
    if missing:
        _echo(
            f"{', '.join(missing)}가 없다. 옵션으로 주거나 "
            f"CUESIFT_BASE_URL·CUESIFT_MODEL 환경변수를 설정한다.",
            err=True,
        )
        raise typer.Exit(2)
    return resolved_base, resolved_model, os.environ.get("CUESIFT_API_KEY")


def _build_provider(*, base_url: str, model: str, api_key: str | None) -> Provider:
    """프로바이더를 만든다. **테스트가 monkeypatch하는 지점이다.**

    본문에서 `OpenAICompatibleProvider(...)`를 직접 만들면 CLI 테스트가
    네트워크를 타거나 `httpx` 내부를 패치해야 한다. 함수 하나로 빼면
    가짜를 꽂는 것이 한 줄이 된다.
    """
    return OpenAICompatibleProvider(base_url=base_url, model=model, api_key=api_key)


def _output_path(
    input_path: Path, out_dir: Path | None, source_lang: str, target_lang: str
) -> Path:
    """출력 경로를 정한다 (FR-7.1 · 설계 §5.1).

    stem이 `.{source_lang}`으로 끝나면 **치환**하고 아니면 **덧붙인다.**
    치환하지 않으면 `ep01.ko.srt`가 `ep01.ko.en.srt`가 되어 언어 태그가
    둘이 된다.

    **판정만 `casefold()`하고 원본 stem은 그대로 쓴다.** Windows는 파일명
    대소문자를 구분하지 않아 `ep01.KO.srt`가 정상인 파일명이고 이 프로젝트의
    개발 플랫폼이 Windows인데, `endswith`는 대소문자를 구분해 `ep01.KO.srt
    --source-lang ko`가 치환되지 않고 `ep01.KO.en.srt`라는 이중 태그를
    낸다(실측: WP7b Task 4 리뷰 라운드 1) - 이 함수가 막겠다고 선언한 바로
    그 사고다. `_resolve_profile`이 `--spec`의 확장자를 가를 때 같은 이유로
    같은 처리를 한다.
    """
    stem = input_path.stem
    suffix = f".{source_lang}"
    if stem.casefold().endswith(suffix.casefold()):
        stem = stem[: -len(suffix)]
    directory = out_dir if out_dir is not None else input_path.parent
    return directory / f"{stem}.{target_lang}{input_path.suffix}"


def _review_path(input_path: Path, review_dir: Path, source_lang: str, target_lang: str) -> Path:
    """검수 리포트 경로를 정한다 (FR-7.2 · review.json 설계 D2).

    stem 규칙은 바로 위 `_output_path`와 같다 - `.{source_lang}`으로 끝나면
    치환하고 아니면 덧붙인다. 판정만 `casefold()`한다. **함께 바뀌는 것이라
    함께 둔다** - 두 규칙이 갈라지면 같은 입력이 `ep01.en.srt`와
    `ep01.ko.en.review.json`을 내 짝을 눈으로 못 맞춘다.

    **고정 이름(`review.{lang}.json`)을 쓰지 않는 이유는 덮어쓰기다.** `ep01`과
    `ep02`를 같은 `--review-out`으로 돌리면 뒤엣것이 앞엣것을 조용히 지우고,
    종료 코드는 0이며 경고도 없다.

    **`casefold()`가 양쪽에 걸려야 한다.** 파일명 쪽만 접으면 `--source-lang KO`
    (`--source-lang`은 CLI 어디에서도 접히지 않고 여기까지 온다)가 치환에
    실패해 `ep01.ko.en.review.json`이라는 이중 태그를 낸다 - `_output_path`가
    겪은 사고의 거울상이다.
    """
    stem = input_path.stem
    suffix = f".{source_lang}"
    if stem.casefold().endswith(suffix.casefold()):
        stem = stem[: -len(suffix)]
    return review_dir / f"{stem}.{target_lang}.review.json"


def _report_path(input_path: Path, review_dir: Path, source_lang: str, target_lang: str) -> Path:
    """HTML 검수 리포트 경로를 정한다 (FR-7.3 · 설계 D1).

    **stem 규칙이 바로 위 `_review_path`와 같아야 한다.** 갈라지면 같은 입력이
    `ep01.en.review.json`과 `ep01.ko.en.report.html`을 내고, 소비자는 두 파일이
    한 실행의 산출물임을 눈으로 맞추지 못한다. `--review-format both`가 정확히
    그 두 파일을 나란히 내는 형식이라 이 규칙은 장식이 아니다.

    **`casefold()`가 양쪽에 걸려야 한다.** 파일명 쪽만 접으면 `--source-lang KO`
    (`--source-lang`은 CLI 어디에서도 접히지 않고 여기까지 온다)가 치환에
    실패해 `ep01.KO.en.report.html`이라는 이중 태그를 낸다 - `_output_path`와
    `_review_path`가 겪은 사고의 거울상이다.

    고정 이름을 쓰지 않는 이유도 `_review_path`와 같다 - `ep01`과 `ep02`를 같은
    `--review-out`으로 돌리면 뒤엣것이 앞엣것을 조용히 지우고 종료 코드는 0이다.
    """
    stem = input_path.stem
    suffix = f".{source_lang}"
    if stem.casefold().endswith(suffix.casefold()):
        stem = stem[: -len(suffix)]
    return review_dir / f"{stem}.{target_lang}.report.html"


def _review_artifact_paths(
    input_path: Path,
    review_dir: Path,
    source_lang: str,
    target_lang: str,
    review_format: ReviewFormat,
) -> list[Path]:
    """형식이 실제로 낼 산출물 경로를 **나갈 순서대로** 낸다 (FR-7.3 · 설계 D1·D7).

    **분기 조건이 두 곳에 있으면 dry-run이 거짓말을 한다.** `--review-format`이
    들어오기 전에는 산출물이 `review.json` 하나뿐이라 dry-run이 `_review_path`를
    직접 불러도 본 실행과 어긋날 수 없었다. 형식이 생긴 지금은 **어느 파일이
    나가는가**가 판단이고, 그 판단을 dry-run과 본 실행이 각자 하면 갈라진다 -
    실제로 `--dry-run --review-format html`이 나오지도 않을 `.review.json`을
    예고했다(리뷰 라운드 1 실측, 두 리뷰어가 독립으로 지목).

    그래서 **경로 조립이 아니라 판단 자체를 여기 모은다.** `_translate_one`은
    산출물마다 쓰는 함수와 실패 코드가 달라 이 목록을 그대로 돌지 못하지만,
    같은 순서·같은 조건을 쓰는지 `test_dry_run이_예고한_파일과_본_실행이_내는_파일이_같다`
    가 두 출력을 맞대어 잰다 - 갈라지면 그 테스트가 죽는다.
    """
    paths: list[Path] = []
    if review_format in (ReviewFormat.JSON, ReviewFormat.BOTH):
        paths.append(_review_path(input_path, review_dir, source_lang, target_lang))
    if review_format in (ReviewFormat.HTML, ReviewFormat.BOTH):
        paths.append(_report_path(input_path, review_dir, source_lang, target_lang))
    return paths


@app.command()
def translate(
    # **파라미터가 아니다.** typer가 `Context`를 알아보고 click 옵션 목록에서
    # 빼므로 `--help`도 매핑표 상등 게이트도 그대로다. `_resolve_llm`이
    # 값의 출처를 물어보려면 이것이 있어야 한다(FR-8.4 · 설계 D3).
    ctx: typer.Context,
    input: Annotated[
        Path,
        # `readable=False`는 `check`와 같은 이유다 — 읽기 가능 판정을
        # 인제스트 한 곳으로 모아 플랫폼마다 다른 코드가 나오지 않게 한다.
        typer.Argument(exists=True, dir_okay=False, readable=False, help="번역할 자막 파일"),
    ],
    to: Annotated[str, typer.Option("--to", help="대상 언어 (쉼표 구분, 예: en,ja)")],
    out: Annotated[
        Path | None,
        # `file_okay=False`는 `--out`이 이미 존재하는 **파일**을 가리키는 흔한
        # 사고(디렉터리 자리에 파일 경로를 잘못 줌)를 본문 전에 exit 2로 거른다
        # (실측: WP7b Task 4 리뷰 라운드 1 - 이전에는 `write_subtitle`의
        # `mkdir(parents=True, exist_ok=True)`가 `FileExistsError`를 그대로
        # 흘려 exit 1로 오보됐다). `exists=True`는 걸지 않는다 - `--out`은
        # 보통 아직 없는 디렉터리이고, 없으면 `write_subtitle`이 만든다.
        typer.Option("--out", file_okay=False, help="출력 디렉터리. 기본은 입력 파일과 같은 곳"),
    ] = None,
    source_lang: Annotated[str, typer.Option("--source-lang", help="원문 언어")] = "ko",
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="OpenAI 호환 엔드포인트. 없으면 CUESIFT_BASE_URL"),
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="모델 이름. 없으면 CUESIFT_MODEL")
    ] = None,
    glossary: Annotated[
        Path | None, typer.Option("--glossary", help="용어집 YAML (FR-2.3)")
    ] = None,
    work_context: Annotated[
        str | None, typer.Option("--work-context", help="작품 맥락 (FR-2.8)")
    ] = None,
    context_window: Annotated[
        int, typer.Option("--context-window", min=0, help="앞뒤 맥락 세그먼트 수")
    ] = DEFAULT_CONTEXT_WINDOW,
    cache_dir: Annotated[
        Path | None, typer.Option("--cache-dir", help="캐시 디렉터리. 기본 .cuesift/cache")
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            # em dash(U+2014)를 쓰지 않는다(전역 제약, cp949 미인코딩).
            help="캐시를 읽지도 쓰지도 않는다. 형식을 어긴 응답도 캐시되므로 "
            "같은 명령을 다시 쳐도 안 나아지면 이걸 쓴다",
        ),
    ] = False,
    review_budget: Annotated[
        str | None,
        typer.Option(
            "--review-budget",
            # em dash(U+2014)를 쓰지 않는다(전역 제약, cp949 미인코딩).
            help="사람이 검수할 상위 비율 (예: 10% 또는 0.1). --review-threshold와 함께 쓸 수 없다",
        ),
    ] = None,
    review_threshold: Annotated[
        float | None,
        typer.Option(
            "--review-threshold",
            min=0.0,
            max=1.0,
            # 라이브러리(`policy.py`)도 범위를 검사하지만 여기서 막으면 오류
            # 메시지가 옵션 이름을 말한다 - `--context-window`·`--limit`이
            # 이미 같은 패턴이다.
            help="이 위험도 이상을 검수 큐에 담는다 (0.0~1.0). --review-budget과 함께 쓸 수 없다",
        ),
    ] = None,
    review_out: Annotated[
        Path | None,
        typer.Option(
            "--review-out",
            # `file_okay=False`는 `--out`과 같은 이유다 - 출력 디렉터리 자리에
            # 이미 파일이 있으면 `FileExistsError`가 새어 exit 1로 오보된다.
            # 1은 이 CLI에서 "규격 위반 발견"이라 설정 실수가 자막 결함이 된다.
            file_okay=False,
            # em dash(U+2014)를 쓰지 않는다(전역 제약, cp949 미인코딩).
            #
            # **형식을 문구에 못박지 않는다.** `--review-format`이 생긴 뒤로 이
            # 디렉터리에는 `.report.html`도 나가므로, `review.json`이라고 쓰면
            # 바로 아래 줄에 붙어 렌더되는 `--review-format`과 모순된 화면이
            # 된다(리뷰 라운드 1 실측, `--help` 41~45행).
            help="검수 리포트 출력 디렉터리. --review-budget 또는 "
            "--review-threshold와 함께 써야 한다",
        ),
    ] = None,
    review_format: Annotated[
        ReviewFormat,
        typer.Option(
            "--review-format",
            # 문구를 늘이지 않는다. 색이 켜진 CI에서 rich 하이라이터가 긴 help의
            # 옵션 이름을 줄바꿈으로 쪼개 `--help` 출력 테스트가 깨진 전례가 있다.
            help="검수 리포트 형식. --review-out과 함께 써야 한다",
        ),
    ] = ReviewFormat.JSON,
    tier1: Annotated[
        bool,
        typer.Option(
            "--tier1",
            # 기본이 꺼짐인 이유는 Q4가 열려 있어서다(설계 D2) - 자가일관성의
            # 판정력이 아직 검증되지 않았다. 검증 안 된 신호가 기본 경로에
            # 섞이면 Recall@Budget 지표 자체가 오염되는데, 그 숫자가 이
            # 프로젝트의 유일한 증명 자료다(§9.1 · §11 R4).
            # em dash(U+2014)를 쓰지 않는다(전역 제약, cp949 미인코딩).
            help="Tier 1 신호(자가일관성)를 켭니다. 기본은 꺼짐입니다 (FR-4.3).",
        ),
    ] = False,
    tier1_max_ratio: Annotated[
        float | None,
        typer.Option(
            "--tier1-max-ratio",
            min=0.0,
            max=1.0,
            # **기본값이 None인 것은 "명시했나"를 알기 위해서다.** 실제 값을
            # 기본으로 두면 사용자가 친 0.05와 기본 0.05를 구별할 수 없어
            # `--tier1` 없이 준 것을 잡지 못한다. 사용자에게 보일 기본값은
            # 아래 help 문구가 대신 말한다.
            # **기본값을 리터럴로 쓰지 않는다.** 리터럴이면 상수를 고쳤을 때
            # 도움말만 옛 값을 말하는 조용한 거짓말이 남는다 - 사용자는 화면에
            # 적힌 값을 믿고 그 값으로 비용을 계산한다.
            # **도움말이 파서 범위보다 좁은 것을 말한다.** click이 찍는
            # `[0.0<=x<=1.0]`은 파서가 받는 범위이고, `0`은 아래 조합 검증이
            # exit 2로 거부한다(Tier 1을 끄는 값이라 스위치와 모순이다).
            # 범위 표기만 믿으면 사용자는 0을 허용값으로 읽는다 - 최종 리뷰
            # 축B가 실행으로 찾았다. **`min`을 올려 닫을 수는 없다** - click의
            # 범위는 경계를 포함해서 "0보다 큰"을 표현하지 못한다.
            help=(
                f"Tier 1을 태울 회색지대 후보 상한 비율 (기본 {_TIER1_DEFAULT_MAX_RATIO})."
                " --tier1과 함께 씁니다. 실제 허용은 0 < x <= 1.0으로 0은 거부됩니다."
            ),
        ),
    ] = None,
    tier1_samples: Annotated[
        int | None,
        typer.Option(
            "--tier1-samples",
            min=2,
            # 1이면 비교할 쌍이 0개라 유사도 계산 자체가 성립하지 않는다.
            # `Tier1Context.__post_init__`도 같은 경계를 검사하지만 여기서
            # 막아야 오류 메시지가 옵션 이름을 말한다.
            help=(
                f"재번역 샘플 수 (기본 {_TIER1_DEFAULT_SAMPLES})."
                " 2 미만이면 비교할 쌍이 만들어지지 않습니다."
            ),
        ),
    ] = None,
    tier1_temperature: Annotated[
        float | None,
        typer.Option(
            "--tier1-temperature",
            min=0.0,
            # `min`은 경계를 포함하므로 0.0이 본문까지 온다. 거부는 아래
            # 조합 검증이 한다 - 여기서 `min`을 올리면 도움말의 범위 표기가
            # "0보다 큰 실수"를 표현하지 못한다.
            # 도움말이 파서 범위(`[x>=0.0]`)보다 좁은 것을 말하는 이유는 위
            # `--tier1-max-ratio`와 같다. "0이면 신호가 죽습니다"만 적으면
            # **허용값으로 읽힌다** - 실제로는 exit 2다.
            help=(
                f"재번역 온도 (기본 {_TIER1_DEFAULT_TEMPERATURE})."
                " 실제 허용은 0 < x로 0은 거부됩니다 (0이면 샘플이 전부 같아 신호가 죽습니다)."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        # Task 4까지는 "경고 후 무시"(--review-budget과 같은 임시 처리)였다
        # (실측: WP7b Task 4 리뷰 라운드 1 - 본문이 `dry_run`을 한 번도 읽지
        # 않아 프로바이더를 그대로 호출하고 파일을 그대로 썼다). Task 6이
        # `_dry_run_report`로 그 자리를 교체했다 - 지금은 실제로 아무 것도
        # 호출·기록하지 않는다.
        typer.Option(
            "--dry-run",
            help="실행하지 않고 배치 수·캐시 히트·호출 필요 수를 추정합니다 (NFR-2).",
        ),
    ] = False,
    progress: Annotated[
        bool | None,
        # **3상이다.** 기본값 `None`이 "지정 안 함"이고, 그때 자동 감지가
        # 정한다. `False`를 기본으로 두면 감지가 영영 안 돈다.
        # 플래그는 **켜고 끄기만** 정한다 - 스타일은 언제나 감지가 정한다
        # (설계 D7). `--progress`를 CI에서 줘도 `\r`이 아니라 이정표 줄이
        # 나온다.
        typer.Option(
            "--progress/--no-progress",
            help="진행 표시를 켜거나 끕니다. 기본은 자동 감지 (FR-8.5).",
        ),
    ] = None,
) -> None:
    """FR-8.1: 자막을 번역해 언어별 파일로 냅니다."""
    if no_cache and cache_dir is not None:
        # **명령줄이 이긴다**(FR-8.4 후반절). `_resolve_exclusive`가 설정에서
        # 온 쪽을 골라 주고, 둘 다 같은 출처면 거기서 exit 2로 끝난다.
        exclusive_loser = _resolve_exclusive(
            ctx, "--no-cache와 --cache-dir을 함께 줄 수 없다", "no_cache", "cache_dir"
        )
        if exclusive_loser == "no_cache":
            no_cache = False
        else:
            cache_dir = None
    if review_budget is not None and review_threshold is not None:
        # FR-6.3은 "두 방식으로 지정할 수 있다"이지 "동시에"가 아니다.
        # 합성하면 어느 쪽이 이겼는지가 출력에서 사라진다(설계 D4).
        # 버리는 쪽은 위와 같은 규칙으로 고른다.
        if (
            _resolve_exclusive(
                ctx,
                "--review-budget과 --review-threshold는 함께 쓸 수 없다",
                "review_budget",
                "review_threshold",
            )
            == "review_budget"
        ):
            review_budget = None
        else:
            review_threshold = None
    if review_threshold is not None and math.isnan(review_threshold):
        # **`min`/`max`는 NaN을 통과시킨다.** click의 범위 검사가
        # `lt(nan, 0.0)`·`gt(nan, 1.0)`으로 판정하는데 **둘 다 False**라
        # 무력하다. `inf`는 `gt(inf, 1.0)`이 True라 click이 잡으므로 여기
        # 오지 않는다(실측). `_parse_review_budget`은 `not 0.0 <= nan <= 1.0`이
        # True가 되어 거부한다 - **극성이 반대다.**
        #
        # 이 가드가 없으면 `select_by_threshold`가 ValueError를 던지고,
        # 잡히지 않으면 미처리 traceback이 **exit 1**이 된다. 이 파일 머리말의
        # 표에서 1은 "규격 위반 발견"이라 **설정 실수가 자막 결함으로
        # 오보되고**, 사용자는 멀쩡한 자막을 고치려 든다.
        _echo("--review-threshold를 숫자로 읽지 못했다: nan", err=True)
        raise typer.Exit(2)
    if review_out is not None and review_budget is None and review_threshold is None:
        # 리포트를 낼 트리아지 정책이 없다. 조용히 무시하면 사용자는 파일이
        # 없다는 사실을 다음 단계(배포 스크립트·CI)에서야 만난다(설계 D10).
        #
        # **`--dry-run` 분기보다 앞에 둔다**(D11). 뒤로 미루면 dry-run으로
        # 확인한 명령이 본 실행에서 처음 실패한다. 프로파일 전량 검사가 이미
        # 같은 규칙을 따른다.
        #
        # **세 항을 모두 본다.** `review_out is not None` 하나로 줄이면 정상
        # 조합까지 거부하고, 뒤의 두 항 중 하나만 보면 나머지 한 방식이
        # 사용법 오류로 막힌다 - FR-6.3은 두 방식을 대등하게 둔다.
        #
        # **뒤의 두 항은 아래 `triage_requested`의 부정과 동치다.** 그것을
        # 재사용하지 않고 여기서 다시 쓰는 이유는 순서다 - 이 검사는
        # `triage_requested`가 만들어지기 **전에** 끝나야 하고(D11: 조합 오류는
        # 파싱·프로파일 조달보다 앞이다), 변수를 앞으로 끌어올리면 그 정의가
        # 예산 파싱과 멀어져 무엇을 근거로 만든 값인지 읽기 어려워진다.
        # 동치라는 사실을 여기 적어 두는 것으로 중복을 감수한다 - 한쪽만
        # 고치면 갈라지므로, 고칠 때 둘을 함께 본다.
        _echo(
            "--review-out은 --review-budget 또는 --review-threshold와 함께 써야 한다",
            err=True,
        )
        raise typer.Exit(2)
    if review_format is not ReviewFormat.JSON and review_out is None:
        # **`review_format is not None`으로 쓸 수 없다.** 이 옵션은 Enum에
        # 기본값(`ReviewFormat.JSON`)이 있어 사용자가 주지 않아도 언제나 값이
        # 들어온다 - `None` 검사로는 "주지 않은 실행"을 영원히 잡지 못해
        # 가드가 항상 참이 되고 `--review-out` 없는 정상 실행까지 exit 2로
        # 막는다. 그래서 "기본에서 벗어났는가"를 본다.
        #
        # **조용히 무시하면 안 된다.** 낼 곳이 없는 형식 지정을 넘기면 사용자는
        # 나오지 않는 파일을 찾아 헤매고, 종료 코드가 0이라 스크립트는 성공으로
        # 읽는다 - 바로 위 `--review-out` 가드가 막는 것과 같은 사고다(D10).
        #
        # 위 가드와 같은 자리에 두는 것은 순서 때문이다(D11) - 조합 오류는
        # 파싱·프로파일 조달보다 앞에서 끝나야 dry-run으로 확인한 명령이 본
        # 실행에서 처음 실패하는 일이 없다.
        _echo("--review-format은 --review-out과 함께 써야 한다", err=True)
        raise typer.Exit(2)

    tier1_options_given = (
        tier1_max_ratio is not None or tier1_samples is not None or tier1_temperature is not None
    )
    if tier1_options_given and not tier1:
        # 조용히 무시하면 "켰다고 믿는" 실행이 생긴다. 그 실행은 종료 코드도
        # 0이고 review.json도 정상이라 어떤 게이트에도 걸리지 않는다.
        _echo("--tier1-* 옵션은 --tier1과 함께 써야 한다", err=True)
        raise typer.Exit(2)
    if tier1:
        if review_threshold is not None:
            # `triage_with_tier1`은 `select_by_budget`을 고정으로 쓴다(설계 D9).
            # 회색지대 개념 자체가 예산 선별의 부산물이라, 임계값 정책에서는
            # "후보로 뽑을 회색지대"의 정의가 서지 않는다.
            _echo(
                "--tier1은 --review-threshold와 함께 쓸 수 없다 (--review-budget을 쓴다)",
                err=True,
            )
            raise typer.Exit(2)
        if review_budget is None:
            # `triage_with_tier1`이 `budget_ratio`를 기본값 없는 필수 인자로
            # 요구한다. 정책 없이 켜면 조달할 값이 없다.
            _echo("--tier1은 --review-budget을 요구한다", err=True)
            raise typer.Exit(2)
        for _option_name, _option_value in (
            ("--tier1-max-ratio", tier1_max_ratio),
            ("--tier1-temperature", tier1_temperature),
        ):
            if _option_value is not None and not math.isfinite(_option_value):
                # **click의 `min`/`max`는 NaN을 통과시킨다** - 위
                # `--review-threshold`의 `math.isnan` 주석과 같은 구멍이다
                # (`lt(nan, 0.0)`·`gt(nan, 1.0)`이 **둘 다 False**라 무력하다).
                #
                # 여기서 막지 않으면 아래 비용 한도 검사가 `nan > 0.3`을
                # False로 읽어 **조용히 통과**한다 - 검사하지 않고 통과하는
                # 게이트는 없는 게이트보다 나쁘다. `--tier1-temperature`는
                # 상한이 없어 `inf`도 click을 빠져나오므로 `isnan`이 아니라
                # `isfinite`로 본다(`--tier1-max-ratio`의 `inf`는 `max=1.0`이
                # 이미 막지만 두 옵션을 같은 규칙으로 둔다).
                _echo(f"{_option_name}를 숫자로 읽지 못했다: {_option_value}", err=True)
                raise typer.Exit(2)
        if tier1_max_ratio is not None and tier1_max_ratio == 0.0:
            # 라이브러리가 `max_ratio=0.0`을 "사용자가 Tier 1을 껐다 - 정상"으로
            # 정의한다(`tier1.py`의 후보 0건 진단). 스위치를 켜면서 0을 주는 것은
            # 정면으로 모순이고, 통과시키면 "켰는데 안 도는" 실행이 된다.
            _echo(
                "--tier1-max-ratio 0은 Tier 1을 끄는 값이라 --tier1과 함께 줄 수 없다",
                err=True,
            )
            raise typer.Exit(2)
        if tier1_temperature is not None and tier1_temperature <= 0.0:
            # click의 `min=0.0`은 **경계를 포함**하므로 0.0이 여기까지 온다.
            # 막지 않으면 `Tier1Context.__post_init__`이 나중에 던지고, 그때는
            # 오류 메시지가 옵션 이름을 말하지 못한다.
            _echo("--tier1-temperature는 0보다 커야 한다 (0이면 샘플이 전부 같아진다)", err=True)
            raise typer.Exit(2)
        effective_max_ratio = (
            _TIER1_DEFAULT_MAX_RATIO if tier1_max_ratio is None else tier1_max_ratio
        )
        effective_samples = _TIER1_DEFAULT_SAMPLES if tier1_samples is None else tier1_samples
        # 비용 검사에는 안 쓰이지만 **여기서 함께 정한다** - 셋이 흩어지면
        # 한도 검사가 본 값과 실제로 넘어가는 값이 갈라질 수 있고, 그때
        # 화면은 한도를 통과했다고 말하면서 다른 수로 돈다.
        effective_temperature = (
            _TIER1_DEFAULT_TEMPERATURE if tier1_temperature is None else tier1_temperature
        )
        cost_factor = effective_samples * effective_max_ratio
        # **한도는 경계를 포함한다 - `>`가 아니라 "닿거나 넘으면"이다.** 설계
        # D3과 `tier1.py`(단일 출처)가 "`max_ratio=0.10`이 한도에 **정확히**
        # 걸린다"고 못 박았고, 그 3.0배가 §4의 "감당 불가"다.
        #
        # **`>=`만으로는 부족하다.** 곱이 이진 부동소수로 정확히 떨어지지 않아
        # 같은 3.0배가 표현에 따라 갈린다(실측): `3 * 0.1`은
        # `0.30000000000000004`라 `>`로도 걸리지만 `2 * 0.15`와 `30 * 0.01`은
        # 정확히 `0.3`이라 빠져나간다. 이 가드가 없으면 세그먼트당 30회를
        # 부르는 조합이 통과하고, 거부 메시지는 `0.30 > 0.3`이라는 거짓말을
        # 찍는다. `isclose`의 기본 상대 허용오차(1e-09)는 이 오차(1e-16)보다
        # 훨씬 크다.
        #
        # **`rel_tol`을 키우면 무엇이 깨지는가.** `max_ratio`는 연속값이라
        # "구별해야 하는 이웃 값"이라는 근거가 성립하지 않는다 - 한도 바로
        # 아래에 오거부 대역이 실제로 존재한다. 폭은 `rel_tol x 0.3`이고
        # 기본값에서 **3e-10**이다(실측: `--tier1-samples 3
        # --tier1-max-ratio 0.09999999995`는 곱이 0.29999999985로 엄격히
        # 0.3 미만인데 거부되고 메시지는 "닿거나 넘는다"고 찍는다). 지금
        # 무해한 이유는 둘이다 - 그 폭에 실사용자가 닿을 수 없고, 틀리는
        # 방향이 비용을 **과대**평가하는 안전한 쪽이다. `rel_tol=1e-6`으로
        # 키우면 대역이 **3e-7**이 되어 `0.0999999`처럼 사람이 칠 수 있는
        # 값이 거부되기 시작한다 - 그때는 화면 메시지가 사실이 아니게 된다.
        at_or_over_limit = cost_factor > _TIER1_COST_LIMIT or math.isclose(
            cost_factor, _TIER1_COST_LIMIT
        )
        if at_or_over_limit:
            # **곱과 한도를 둘 다 적는다.** 어느 쪽을 줄여야 하는지 알 수 없으면
            # 사용자는 임의로 고르고, 그 선택이 다시 한도에 부딪힌다.
            _echo(
                f"--tier1-samples({effective_samples}) x"
                f" --tier1-max-ratio({effective_max_ratio})"
                f" = {cost_factor:.2f} 가 한도 {_TIER1_COST_LIMIT}에 닿거나 넘는다."
                " 둘 중 하나를 줄인다 (요구사항정의서 §4)",
                err=True,
            )
            raise typer.Exit(2)

    triage_requested = review_budget is not None or review_threshold is not None

    # **파싱을 여기서 한다 - `_translate_one` 안이 아니다.** 예산 문자열이
    # 틀렸으면 LLM을 부르기 전에 exit 2로 끝나야 한다. 루프 안에서 파싱하면
    # 첫 언어의 번역 비용을 쓴 뒤에야 사용법 오류를 알린다.
    budget_ratio: float | None = None
    policy_label: str | None = None
    if review_budget is not None:
        try:
            budget_ratio = _parse_review_budget(review_budget)
        except ValueError as exc:
            _echo(str(exc), err=True)
            raise typer.Exit(2) from exc

    if triage_requested:
        # **`triage_requested` 안에서 만든다.** 밖에서 만들면 정책이 하나도
        # 없을 때 `"임계값 None"`이라는 사실이 아닌 라벨이 생기고, 요약에
        # 그것이 찍히면 트리아지를 요청하지 않은 사용자가 임계값을 준 것으로
        # 읽는다. 타입을 `str | None`으로 둔 것이 그 오용을 막는다 -
        # `_translate_one`이 `None` 처리를 강제로 마주한다.
        #
        # 사용자가 준 원문을 라벨에 쓴다 - 파싱 결과(`0.1`)를 찍으면 `10%`라고
        # 쓴 사람이 자기 입력을 화면에서 못 찾는다. 이해가 맞았는지는 별도로
        # 출력되는 "실제 N%"가 말한다.
        policy_label = (
            f"예산 {review_budget}" if review_budget is not None else f"임계값 {review_threshold}"
        )

    resolved_base, resolved_model, api_key = _resolve_llm(ctx, base_url, model)
    targets = [lang.strip() for lang in to.split(",") if lang.strip()]
    if not targets:
        _echo("--to에 대상 언어가 없다", err=True)
        raise typer.Exit(2)
    invalid = [t for t in targets if not _LANG_TAG_RE.match(t)]
    if invalid:
        # `_LANG_TAG_RE` 주석 참고 - 여기서 막지 않으면 `_output_path`가
        # 검증되지 않은 문자열을 파일 경로 조각으로 그대로 쓴다.
        _echo(f"--to에 유효하지 않은 언어 태그가 있다: {', '.join(invalid)}", err=True)
        raise typer.Exit(2)

    profiles: dict[str, SpecProfile] = {}
    if triage_requested:
        # **모든 대상 언어를 여기서 검사한다 - 루프 안에서 하지 않는다.**
        # 루프 안에서만 보면 `--to en,ja,fr`이 en·ja의 LLM 비용을 실제로 쓴
        # 뒤 fr에서 exit 2를 낸다(설계 D13). `--dry-run`의 용어집 검사가
        # 이미 같은 이유로 `targets[0]`이 아니라 전량을 본다(아래 주석 참고).
        #
        # 프로파일은 **대상 언어**의 규격이다 - `check --spec ko`(검사 대상
        # 자막의 규격)와 이름이 같아도 다른 것이다(설계 §3.2). 신호 2종
        # (`spec.violation`·`length.ratio`)이 번역문에 이것을 적용한다.
        for target in targets:
            try:
                # **조회만 소문자로 본다.** `_LANG_TAG_RE`(위)가 `[A-Za-z]`로
                # 대문자를 허용하는데 `load_builtin`은 `specs/<name>.yaml`을
                # 파일로 찾는다 - Windows는 파일명 대소문자를 구분하지 않아
                # `--to EN`이 `en.yaml`을 찾아내지만 **CI의 Linux는 구분해
                # 프로파일 없음이 된다.** 접지 않으면 같은 명령의 종료 코드가
                # 플랫폼마다 갈린다(Windows 0 · Linux 2). `_resolve_profile`이
                # `--spec`의 확장자를 가를 때 같은 이유로 같은 처리를 한다.
                # **출력 파일명은 접지 않는다** - `_output_path`가 원본 태그를
                # 써서 `minimal.EN.srt`가 그대로 유지된다.
                profiles[target] = load_builtin(target.lower())
            except (OSError, ValueError) as exc:
                # **경고하고 그 언어만 건너뛴다 - 전량 거부하지 않는다**(D7).
                # 전량 거부는 프로파일이 **있는** 언어의 트리아지까지 잃게 하고,
                # 요구사항정의서 §8.1 S3의 문서화된 호출
                # (`--to en,ja,th,vi --review-budget 10%`)을 깨뜨린다 -
                # th·vi 프로파일이 없고 `tests/test_cli.py:57-73`이 그것을
                # exit 0으로 고정하고 있다. 선례는 `cli.py`의 캐시 처리다:
                # 프로바이더가 `cache_identity`를 주지 않으면 경고하고 캐시를
                # 끈다("끄는 쪽이 안전하고, 조용히 끄지는 않는다").
                #
                # **건너뛰는 것은 트리아지이지 번역이 아니다.** 이 언어의
                # 번역은 그대로 나간다 - `profiles`에 없다는 사실만 뒤에서
                # 트리아지를 거르는 데 쓴다.
                #
                # `load_builtin`의 메시지가 이미 사용 가능 목록을 담으므로
                # (`spec/profile.py:177-180`) 새로 쓰지 않고 전달한다.
                # `[target]` 라벨만 붙인다: 이 함수의 다른 `_echo`들이 전부
                # 그렇게 하고, 언어가 여러 개일 때 어느 언어인지 구별해야 한다.
                _echo(
                    f"[{target}] 경고: 규격 프로파일이 없어 트리아지를 건너뛴다 - {exc}", err=True
                )

        if not profiles:
            # **한 언어도 못 돌면 요청이 통째로 무시된 것이다.** 경고만 내고
            # exit 0으로 끝나면 CI가 "트리아지했다"로 읽는다. 하나라도 돌면
            # 부분 적용이고 어느 언어가 빠졌는지는 위 경고가 말한다.
            #
            # **이 경우는 번역도 나가지 않는다** - 위 경고의 "번역은 그대로
            # 나간다"는 부분 적용일 때의 이야기다. 요청이 통째로 무시되므로
            # 여기서는 사용법 오류로 다뤄 LLM을 부르기 전에 끝낸다.
            _echo("트리아지를 적용할 수 있는 대상 언어가 없다", err=True)
            raise typer.Exit(2)

    try:
        result = load_subtitle(input, source_lang=source_lang)
    except IngestError as exc:
        _echo(str(exc), err=True)
        raise typer.Exit(EXIT_BAD_INPUT) from exc

    for target in targets:
        out_path = _output_path(input, out, source_lang, target)
        if out_path.resolve() == input.resolve():
            # 이것이 없으면 원본이 번역문으로 덮여 되돌릴 수 없다.
            _echo(f"출력 경로가 입력과 같다: {out_path}", err=True)
            raise typer.Exit(2)

    try:
        provider = _build_provider(base_url=resolved_base, model=resolved_model, api_key=api_key)
    except ValueError as exc:
        # 생성자의 ValueError는 ProviderError가 **아니다** — 설정 오류이지
        # 호출 실패가 아니다. 명령줄이 틀린 것이므로 2다.
        _echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if dry_run:
        if glossary is not None:
            # **모든 대상 언어에 대해 검사한다 - `targets[0]` 하나만 보지
            # 않는다.** `load_glossary`는 대응어 값의 **타입 검사**를
            # target_lang별로 한다(`(item.get("targets") or {}).get(target_lang)`
            # 이 리스트가 아니면 거부) - `targets: {en: [Hi], ja: "문자열"}`처럼
            # 언어마다 값의 타입이 다르면 en으로는 통과하고 ja로는 실패한다
            # (실측: WP7b Task 6 리뷰 라운드 1). `targets[0]`만 보면
            # `--to en,ja`는 통과하고 `--to ja,en`은 실패해 **종료 코드가
            # `--to`에 쓴 순서에 좌우되는** 사고가 난다 - 이 저장소가 1급으로
            # 금지한 "검사하지 않고 통과하는 게이트"다. `_translate_one`과
            # 같은 이유로 `except Exception`까지 넓힌다 - `entries: 5`
            # (TypeError)·`targets: Hello`(item 자체가 dict가 아닌 경우,
            # AttributeError)는 언어 무관 실패라 `(OSError, ValueError)`를
            # 지나쳐 그대로 샌다(실측: WP7b Task 4 리뷰 라운드 1). 이 upfront
            # 검사를 좁게 두면 실제 번역(`_translate_one`)은 exit 66으로
            # 막는 바로 그 입력을 dry-run만 통과(또는 트레이스백으로 죽음)
            # 시킨다.
            for target in targets:
                try:
                    load_glossary(glossary, target)
                except Exception as exc:
                    _echo(
                        f"{glossary}: 용어집을 읽지 못했다 - {type(exc).__name__}: {exc}",
                        err=True,
                    )
                    raise typer.Exit(EXIT_BAD_INPUT) from exc

        # identity는 `_cache_identity(provider)`로 얻는다 — **손으로 다시
        # 조립하지 않는다.** `_build_provider()`가 이미 `provider`를 만들었고
        # (바로 위), `_translate_one`도 캐시를 켤 때 같은 함수로 같은 값을
        # 얻는다 - 실행 경로 하나를 공유하므로 "dry-run의 identity 계산이
        # 실제 실행과 어긋난다"는 실패 모드 자체가 구조적으로 성립하지
        # 않는다(WP7b Task 6 리뷰가 지목한 위험 - 상세 근거는
        # `_dry_run_report`의 독스트링).
        identity = _cache_identity(provider)
        if identity is None and not no_cache:
            # 실제 실행(`_translate_one`)의 같은 경고와 짝을 맞춘다 - 없으면
            # 사용자는 "캐시 히트 0개"만 보고 캐시가 꺼진 이유(신원 모름)를
            # 실행 후에야 안다. `[target_lang]` 라벨을 붙이지 않는 것은
            # `_translate_one`의 경고와 달리 **언어별로 다시 계산되는 값이
            # 아니라서**다 - provider 하나에서 한 번만 얻으므로 모든 대상
            # 언어에 똑같이 적용된다.
            _echo(f"경고: {provider.name}이 cache_identity를 제공하지 않아 캐시를 끈다", err=True)
        # dry-run은 `provider.complete()`를 부르지 않으므로 연결 풀이
        # 쓰이지 않은 채로 남는다 - 정리해도 실행에는 영향이 없다.
        # `getattr`로 읽는 것은 `Provider` 프로토콜에 `close`가 없어서다
        # (테스트 가짜에는 없는 경우가 흔하다).
        close = getattr(provider, "close", None)
        if close is not None:
            close()

        for line in _dry_run_report(
            result=result,
            input_path=input,
            out_dir=out,
            source_lang=source_lang,
            targets=targets,
            # `rstrip("/")`한다 - `OpenAICompatibleProvider.__init__`이
            # 실제로 쓰는 값과 같게 보여야 한다. 안 하면 `--base-url
            # http://h/v1/`을 줬을 때 헤더는 끝 슬래시가 붙은 채로 보이는데
            # identity와 실제 엔드포인트는 슬래시 없는 값이라 화면이 서로
            # 다른 URL을 말한다(실측: WP7b Task 6 리뷰 라운드 1 Minor d).
            base_url=resolved_base.rstrip("/"),
            model=resolved_model,
            identity=None if no_cache else identity,
            glossary_path=None if glossary is None else glossary,
            work_context=work_context,
            context_window=context_window,
            cache_dir=None if no_cache else (cache_dir or DEFAULT_CACHE_DIR),
            # **본 실행과 같은 규칙으로 여기서 조립한다** (브랜치 리뷰 코드 축 m1).
            # 두 조건이 실제 실행의 두 가드와 하나씩 대응한다 - `review_out is
            # not None`은 `_translate_one`의 `if review_out is not None`이고,
            # `target in profiles`는 그 함수에 `triage_profile`이 넘어가는
            # 조건(`profiles.get(target)`)이다. 프로파일이 없는 언어를 넣으면
            # **dry-run이 나오지도 않을 파일을 예고한다**(D7).
            #
            # 경로도 형식 판단도 `_review_artifact_paths` - 본 실행이 부르는
            # **같은 함수**다. 손으로 조립하면 stem 규칙이 갈라져도, 형식 분기가
            # 갈라져도 드러나지 않는다(후자는 실제로 한 번 갈라졌다 - 리뷰
            # 라운드 1).
            review_paths=(
                {}
                if review_out is None
                else {
                    target: _review_artifact_paths(
                        input, review_out, source_lang, target, review_format
                    )
                    for target in targets
                    if target in profiles
                }
            ),
            # **`tier1`일 때만 조립한다.** `effective_*`는 조합 검증 블록
            # 안에서만 대입되므로 꺼진 실행에서 읽으면 UnboundLocalError다.
            # 값 자체도 본 실행이 `_Tier1Settings`에 싣는 것과 **같은 변수**라
            # 화면과 실행이 다른 수로 갈라질 수 없다.
            tier1_bound=(effective_max_ratio, effective_samples) if tier1 else None,
        ):
            _echo(line)
        return

    # **번역과 Tier 1이 같은 디렉터리를 봐야 한다.** 두 자리에서 각자 조립하면
    # 한쪽만 `--no-cache`를 반영하는 갈라짐이 조용히 생긴다 - 그때 Tier 1은
    # 캐시가 켜진 줄 알고 매 실행 새 호출을 내면서 화면은 "캐시 꺼짐"을 말한다.
    resolved_cache_dir = None if no_cache else (cache_dir or DEFAULT_CACHE_DIR)

    # **숫자 크기로 합치지 않는다.** 우선순위는 `_EXIT_PRIORITY`가 갖는다.
    #
    # **오늘은 `max()`와 같은 답을 낸다.** `(70, 69, 66, 3)`이 값의 내림차순과
    # 정확히 일치하기 때문이다(등록된 넷에 한해. 미등록 코드가 오면 갈라진다).
    # 그럼에도 튜플을 두는 이유는 **다음에 추가되는 코드가 그 정렬을 깨뜨릴 때
    # 조용히 틀리지 않기 위해서**다 - 이 브랜치의 초안이 실제로 그랬다.
    # 부분 실패에 75(EX_TEMPFAIL)를 골랐을 때 `max()`는 75가 69(프로바이더
    # 거부)를 이기게 했고, en이 부분 실패한 뒤 ja에서 401이 나면 CI가 진짜
    # 원인을 잃었다. 값이 3으로 바뀌며 그 증상은 사라졌지만 구조는 남아 있다.
    codes: list[int] = []
    # **설치·해제를 이 커맨드 하나로 한정한다** (FR-8.5 · 설계 §9 R1).
    # 전역 상태의 수명이 커맨드 경계를 넘으면 테스트가 서로 오염된다.
    #
    # **플래그는 켜고 끄기만 정한다.** `interactive`인지 `plain`인지는
    # 언제나 `resolve_style`의 감지가 정한다(설계 D7) - CI에서 `--progress`를
    # 줘도 캐리지 리턴 갱신이 아니라 이정표 줄이 나온다.
    reporter = ProgressReporter(
        resolve_style(_prefer_env_bool(ctx, "progress", progress, "CUESIFT_PROGRESS"))
    )
    install(reporter)
    try:
        for i, target in enumerate(targets):
            # **대상 언어마다 새로 만든다.** `CountingProvider`는 누적기라 한
            # 인스턴스를 공유하면 뒤 언어의 `review.json`이 앞 언어의 토큰까지 실어
            # Tier 1 비용이 언어 수만큼 부풀어 보인다.
            #
            # **`provider`는 여기서 아직 raw다.** `CachingProvider`로 감싸는 것은
            # `_translate_one` 안의 지역 변수 재바인딩이라 여기까지 오지 않는다.
            # 그 성질에 기대는 것이 D7의 배치(`CachingProvider(CountingProvider(raw))`)를
            # 성립시킨다 - `triage_with_tier1`이 이것을 다시 감싸므로 계측이 캐시
            # 안쪽에 놓이고, 캐시 히트는 세지 않는다(실제로 쓰지 않은 토큰이다).
            #
            # **`identity`도 raw에서 뽑는다.** `CachingProvider`는 `cache_identity`를
            # 위임하지 **않으므로**(Ruling R40 - 위임하면 이중 래핑이 조용히 켜진다)
            # 감싼 뒤에 물으면 `None`이 나와 Tier 1 캐시가 통째로 꺼진다.
            tier1_settings = None
            if tier1:
                tier1_settings = _Tier1Settings(
                    counting=CountingProvider(provider),
                    max_ratio=effective_max_ratio,
                    samples=effective_samples,
                    temperature=effective_temperature,
                    cache_dir=resolved_cache_dir,
                    identity=_cache_identity(provider),
                )
            code = _translate_one(
                result=result,
                input_path=input,
                out_dir=out,
                source_lang=source_lang,
                target_lang=target,
                provider=provider,
                glossary_path=glossary,
                work_context=work_context,
                context_window=context_window,
                cache_dir=resolved_cache_dir,
                triage_profile=profiles.get(target),
                budget_ratio=budget_ratio,
                threshold=review_threshold,
                policy_label=policy_label,
                review_out=review_out,
                review_format=review_format,
                tier1=tier1_settings,
                reporter=reporter,
                weights=_config_weights(ctx),
            )
            codes.append(code)
            if code == EXIT_UNAVAILABLE:
                # 인증·모델 오류는 다음 언어에서도 같다. 반복하면 진짜 원인이
                # 실패 더미 아래 묻히고 호출만 언어 수만큼 는다 (설계 §6.4).
                remaining = targets[i + 1 :]
                if remaining:
                    # **여기서 말하지 않으면 "중단됐다"와 "애초에 안 시켰다"가
                    # 화면에서 구별되지 않는다.** `--to en,ja,th`에서 en만 성공하고
                    # 멈추면 디렉터리엔 `en.srt`만 남는데, 그것은 `--to en`만 친
                    # 결과와 바이트 단위로 같다. 어느 언어까지 됐는지는 위
                    # `_translate_one`의 `[target_lang]` 라벨이 말하고, 그 뒤로
                    # 뭐가 안 됐는지는 이 줄이 말한다(리뷰 라운드 1 Important 2).
                    _echo(
                        f"중단: 남은 대상 언어 {', '.join(remaining)}는 시도하지 않았다",
                        err=True,
                    )
                break
    finally:
        # **해제보다 먼저 지운다.** `Ctrl+C`나 예상 못 한 예외는 `_echo`를
        # 지나지 않아 `clear_active()`가 불리지 않는다 - 그러면 떠 있던
        # `\r` 줄이 개행 없이 남아 셸 프롬프트가 `20/45 (44%)` 위에 겹쳐
        # 찍힌다(실측: KeyboardInterrupt, exit 130). 정상 종료 경로에서는
        # `done()`이 이미 `_line_len`을 0으로 만들어 두므로 무해하다.
        reporter.clear()
        # **`finally`여야 한다.** 예외로 빠져나가면 다음 커맨드가 남의
        # 리포터를 쓴다 - 전역 상태의 수명이 커맨드 경계를 넘는다.
        install(None)
    worst = _combine_exit_codes(codes)
    if worst:
        raise typer.Exit(worst)


def _dry_run_report(
    *,
    result: IngestResult,
    input_path: Path,
    out_dir: Path | None,
    source_lang: str,
    targets: Sequence[str],
    base_url: str,
    model: str,
    identity: str | None,
    glossary_path: Path | None,
    work_context: str | None,
    context_window: int,
    cache_dir: Path | None,
    # **대상 하나가 파일 여러 개를 낸다**(`--review-format both`). `Path` 하나로
    # 두면 형식이 늘 때마다 이 시그니처가 "무엇을 담는 자리인가"를 다시 정해야
    # 하고, 그 사이에 dry-run이 절반만 예고한다.
    review_paths: Mapping[str, Sequence[Path]],
    # `--tier1`이 꺼져 있으면 `None`이라 상한 줄을 한 줄도 내지 않는다.
    #
    # **기본값을 주지 않는다.** 주면 호출자가 빠뜨렸을 때 Tier 1이 화면에서
    # 조용히 사라지는데, 그것이 이 인자가 닫으려는 바로 그 결함이다.
    #
    # **`_Tier1Settings`를 통째로 받지 않는다.** dry-run은 `provider.complete()`를
    # 부르지 않으므로 `CountingProvider`나 캐시 신원을 요구할 이유가 없고,
    # 요구하면 "dry-run이 계측기를 만든다"는 오해가 시그니처에 굳는다. 필요한
    # 둘을 낱개가 아니라 튜플 하나로 받는 것은 이 함수의 인자가 이미 많아
    # 낱개로 더하면 그 문제를 키우기 때문이다.
    tier1_bound: tuple[float, int] | None,
) -> list[str]:
    """실행하지 않고 추정치를 낸다 (NFR-2 · 설계 §7).

    **실측할 수 있는 것만 낸다.** 배치 수와 문자 수는 `build_messages`를 실제로
    불러 정확히 세고, 캐시 히트는 키를 계산해 파일 존재만 확인한다. 토큰과
    비용은 내지 않는다 - 문자에서 토큰으로 가는 계수가 모델마다 다르고
    우리에게 출처가 없다(요구사항정의서 §11 R8).

    **Tier 1 상한만은 실측이 아니라 산식이다**(설계 D10). `floor(n x
    max_ratio) x samples`는 출처 없는 계수를 하나도 쓰지 않고 명령줄에 적힌
    두 수와 세그먼트 수만으로 정해지는 **상한**이라 위 금지에 걸리지 않는다 -
    실제 후보가 회색지대 크기에 눌려 이보다 적을 수는 있어도 많을 수는 없다.
    이 줄이 없으면 `--tier1 --dry-run`이 exit 0과 함께 **가장 비싼 계층이
    통째로 빠진 화면**을 낸다 - `--dry-run`의 존재 이유(비용 추정)와
    `_TIER1_COST_LIMIT`의 존재 이유(비용 통제)가 서로 말을 하지 않는 상태다.
    **이 줄이 채우는 것은 재번역 요청의 상한**이지 프로바이더 호출 수가
    아니다 - 그쪽은 재시도·폴백이 얹혀 여전히 아무도 말하지 않는다(위
    `_TIER1_BOUND_PREFIX` 주석과 같은 구분이다).

    **네트워크를 타지 않는다.** 이 함수는 프로바이더를 참조하지 않는다 -
    호출자(`translate()`)가 `_cache_identity(provider)`로 이미 뽑아 둔
    `identity` 문자열만 받는다. `build_messages`·`iter_batches`·
    `load_glossary` 셋 다 순수 함수이거나 로컬 파일만 읽으므로 이 함수
    자체는 구조적으로 네트워크에 닿을 수 없다.

    ## identity는 손으로 다시 조립하지 않는다

    `OpenAICompatibleProvider.cache_identity`(translate/openai_compat.py)와
    글자 그대로 같아야 할 값이라 손으로 베끼면 어긋날 위험이 있다 - 실제로
    "호출자가 `_cache_identity(provider)`를 한 번 부르고 그 결과를 넘긴다"는
    계약으로 이 위험을 없앴다(호출부 `translate()` 참고). 프로바이더를
    만들면(`OpenAICompatibleProvider(...)`) 생성자가 `httpx.Client()`를 실제로
    여는 것은 확인했지만(openai_compat.py:124), **생성 자체는 네트워크 I/O가
    아니다** - httpx의 연결 풀은 지연 생성이라 소켓은 첫 요청까지 열리지
    않는다(실측: 존재하지 않는 IP로 만들어도 생성이 0.1초 미만). 위험한 것은
    `provider.complete()`를 부르는 것이고, 이 함수도 호출자도 그것을 부르지
    않는다.

    ## 용어집은 대상 언어마다 다시 읽는다

    `load_glossary`가 대상 언어의 대응어만 걸러 담으므로(용어집 모듈), en으로
    채운 `Glossary`를 ja 배치 조립에 그대로 쓰면 프롬프트 문자 수뿐 아니라
    캐시 재료(`messages_sha`)까지 실제 실행과 달라진다 - `_translate_one`도
    언어 루프 안에서 매번 `load_glossary(glossary_path, target_lang)`를 새로
    부르는 것과 같은 이유다. 호출자(`translate()`)가 **대상 언어 전부**에
    대해 한 번씩 미리 읽어 성공을 확인해 두므로(§WP7b Task 6 리뷰 라운드 1
    Important 1 - `load_glossary`의 대응어 타입 검사는 `target_lang`마다
    다른 값을 보므로 언어별로 성패가 갈릴 수 있다. `targets[0]`만 검사하면
    `--to en,ja`는 통과하고 `--to ja,en`은 실패하는, 종료 코드가 명령줄
    순서에 좌우되는 사고가 실측됐다) 여기서 다시 실패할 일은 없지만, 그
    전제가 깨져도 트레이스백 대신 이 언어의 보고만 생략한다.

    ## "호출 필요 N개"는 하한이다 (WP7b Task 6 리뷰 라운드 1 Important 2)

    FR-2.6 배치 폴백(응답이 형식을 어기면 세그먼트별 개별 재호출로 강등)과
    재시도가 발동하면 실제 호출은 배치 수보다 몇 배로 는다(실측: 12세그먼트·
    2배치에서 "호출 필요 2개"였지만 실제 실행은 14회를 불렀다). 이 함수는
    배치가 **한 번에 성공한다고 가정한 하한**만 낼 수 있다 - 모델이 형식을
    지킬지는 실행 전에 알 방법이 없다(정확한 수를 내려 들면 그 자체가
    출처 없는 추정이 되어 요구사항정의서 §11 R8을 어긴다). 그래서 아래
    출력은 "N개"가 아니라 "N개 이상"이라고 정직하게 말한다.

    ## temperature·max_tokens는 여전히 손으로 맞춘다 (남은 한계)

    아래 `CacheRequest`의 `temperature=0.0`·`max_tokens=None`은 각각
    `translate_segments`의 기본값과 `_call_with_retry`의 리터럴을 손으로
    베낀 것이다. `translate_segments`의 `temperature` 기본값은 이름 있는
    상수가 아니라 함수 시그니처의 기본 인자값이고, `max_tokens=None`은
    `translate/engine.py`의 `_call_with_retry` 본문에 직접 박혀 있어(참조할
    수 있는 이름조차 없다) 이 태스크가 손대지 않는 `translate/` 밖에서는
    가져올 방법이 없다. **어긋나면 dry-run이 "호출 필요 82개 이상"이라 해
    놓고 실행은 0개를 부른다.** 이 어긋남을 직접 겨냥한 단위 테스트는 없지만
    **간접 게이트는 실측으로 작동을 확인했다** - `temperature`를 0.0→0.7로,
    `max_tokens`를 None→4096으로 각각 변이하면 캐시 파일 이름이 달라져
    `test_dry_run이_캐시_히트를_센다`·`test_dry_run의_identity가_실제와_같다`·
    `test_dry_run이_다배치_다국어_용어집_맥락에서_실제_실행과_일치한다`
    (`tests/test_cli_translate.py`) 3개가 매번 죽는다(WP7b Task 6 리뷰
    라운드 1에서 2개로 보고됐으나 라운드 1 수정에서 추가된 세 번째 테스트로
    재확인하면 3개다).

    ## 리포트 경로는 **계산하지 않고 받는다** (브랜치 전체 리뷰 코드 축 m1)

    이 함수는 자막 경로를 언어마다 찍으면서 `--review-out`의 산출물은 한 줄도
    말하지 않았다 - `test_dry_run은_파일을_쓰지_않는다`가 "쓰지 **않는다**"는
    **음성 방향만** 보고 "무엇을 낼 것인지 말한다"는 양성 방향은 아무도 안
    봤기 때문이다. 위 D11 주석이 "dry-run으로 확인한 명령이 본 실행에서 처음
    실패한다"를 막겠다고 선언했으므로 의도는 dry-run/본실행 정합인데, 산출물
    목록만 그 정합에서 빠져 있었다.

    **`review_out`과 `profiles`를 받아 여기서 조립하지 않는다.** 리포트를 내는
    언어는 "규격 프로파일이 있는 언어"뿐이고(D7 - 없는 언어에 빈 파일을 내면
    소비자가 "검수했고 걸린 것이 없다"로 읽는다), 그 규칙은 실제 실행에서
    **호출자가** `profiles.get(target)`으로 판정한다. 여기서 규칙을 다시 쓰면
    두 곳이 갈라져 **dry-run이 나오지도 않을 파일을 예고한다** - 이 함수가
    닫으려는 바로 그 불일치다. 그래서 호출자가 `_review_path`(본 실행과 **같은
    함수**)로 만든 완성된 매핑만 받는다. 빈 매핑이면 한 줄도 내지 않는다.

    ## 캐시 손상은 감지하지 못한다 (WP7b Task 6 리뷰 라운드 1 Minor b)

    아래 캐시 히트 판정은 `(cache_dir / f"{key}.json").exists()`뿐이다.
    실제 실행이 캐시를 읽을 때 쓰는 `cache.load()`는 파일 존재를 넘어
    JSON 파싱·필드 타입·`_matches()`까지 확인해 손상된 캐시를 **미스**로
    떨어뜨린다(`store/cache.py`: "캐시는 최적화이지 정확성의 근거가
    아니다"). 이 함수는 그 깊이까지 확인하지 않으므로, 캐시 파일이 손상돼
    있으면 dry-run은 "히트"라 하고 실제 실행은 미스로 다시 부른다 - 설계가
    "파일 존재만 확인"을 택한 결과이지 이 함수의 스펙 위반은 아니다. 정상
    운영에서 캐시 파일은 이 프로세스가 원자적으로 쓰므로(`cache.store()`의
    `os.replace`) 손상될 경로가 거의 없다.
    """
    lines = [
        f"입력   {input_path} ({result.format}) · {len(result.segments)} 세그먼트",
        f"모델   {model} @ {base_url}",
    ]
    for target in targets:
        glossary: Glossary | None = None
        if glossary_path is not None:
            try:
                glossary = load_glossary(glossary_path, target)
            except Exception as exc:
                lines.extend(
                    [
                        "",
                        f"[{target}] 용어집을 다시 읽지 못했다 - {type(exc).__name__}: {exc}",
                    ]
                )
                continue

        batches = 0
        hits = 0
        system_chars = 0
        user_chars = 0
        for window in iter_batches(
            result.segments, size=DEFAULT_BATCH_SIZE, context_window=context_window
        ):
            batches += 1
            # `build_messages`는 `BatchWindow`를 받지 않는다. batch·before·after를
            # 따로 받는다(`prompt.py`) - 통째로 넘기면 TypeError다.
            messages = build_messages(
                window.batch,
                source_lang=source_lang,
                target_lang=target,
                before=window.before,
                after=window.after,
                glossary=glossary,
                work_context=work_context,
            )
            system_chars += sum(len(m.content) for m in messages if m.role == "system")
            user_chars += sum(len(m.content) for m in messages if m.role == "user")
            if cache_dir is not None and identity is not None:
                request = CacheRequest(
                    identity=identity,
                    # 위 독스트링 "temperature·max_tokens는 여전히 손으로
                    # 맞춘다" 참고 - 엔진이 실제로 쓰는 값과 같아야 한다.
                    temperature=0.0,
                    max_tokens=None,
                    messages=tuple(messages),
                )
                if (cache_dir / f"{request.key}.json").exists():
                    hits += 1
        lines.extend(
            [
                "",
                f"[{target}] {_output_path(input_path, out_dir, source_lang, target)}",
                f"  배치 {batches}개 (size={DEFAULT_BATCH_SIZE}, context_window={context_window})",
                # "이상"을 뺄 수 없다 - 위 독스트링 참고. 배치 폴백·재시도가
                # 발동하면 실제 호출은 이 수의 몇 배가 될 수 있다.
                f"  캐시 히트 {hits}개 · 호출 필요 {batches - hits}개 이상",
                f"  프롬프트 문자 system {system_chars:,} + user {user_chars:,}",
            ]
        )
        if tier1_bound is not None:
            max_ratio, samples = tier1_bound
            # **상한이지 추정이 아니다**(설계 D10 · §11 R8). 실제 요청은 회색지대
            # 크기에 눌려 이보다 적을 수 있지만 많을 수는 없다 - 그래서 화면
            # 문구가 "예상"이 아니라 "최대"다.
            #
            # **세는 것은 재번역 요청이지 프로바이더 호출이 아니다.** 요청 하나가
            # 재시도·개별 폴백을 쓰면 호출 수는 이 수를 넘는다. 괄호의
            # `재시도·폴백 제외`를 빼면 형제 줄 `호출 필요 N개 이상`과 같은
            # 낱말이 한 화면에서 하한과 상한을 동시에 말한다(접두 상수 주석).
            #
            # **`floor`가 아니면 무엇이 깨지는가.** `select_tier1_candidates`가
            # 내림으로 상한을 잡으므로 여기서 올림하면 dry-run이 실행보다 **큰**
            # 수를 말한다 - 상한이 상한이 아니게 되고, 사용자는 부풀린 비용을
            # 보고 실행을 접는다.
            #
            # **`samples`를 곱하지 않으면** 후보 수를 요청 수로 오보한다. Tier 1은
            # 후보 하나마다 N회를 **개별 요청**으로 낸다(§12 Q3 - `n>1` 단일
            # 호출은 백엔드에 따라 조용히 사라져 이식성이 없다).
            #
            # **분모가 `len(result.segments)`이면 무엇이 어긋나는가.** 실행
            # 경로가 쓰는 분모는 `len(scored)` - 세그먼트에서 **번역 실패분**을
            # 뺀 수다. 둘이 갈라지는 조건은 번역 실패 그 하나뿐이고, 실패가
            # 없는 실행에서는 정확히 같은 수가 나온다. 실패가 있으면 이 줄이
            # 그만큼 크게 말하지만 방향이 상한 쪽이라 안전하다 - 실패분을 빼려
            # 들면 이 함수가 번역을 실제로 돌려야 하고, 그러면 dry-run이 아니다.
            bound = math.floor(len(result.segments) * max_ratio) * samples
            lines.append(
                f"  {_TIER1_BOUND_PREFIX}{bound}회 (재시도·폴백 제외"
                f" · 후보 상한 비율 {max_ratio} · 샘플 {samples})"
            )
            # **0회는 파라미터만 보여 주면 원인을 말한 것이 아니다**(파킹 #3).
            # 실주행에는 `_diagnose_empty_candidates`가 원인 6개를 구분하는데
            # dry-run에는 아무 설명이 없어, 사용자가 `--tier1`이 안 먹는다고 읽었다.
            #
            # **`explain_zero_bound`가 `None`을 낼 수 있다.** 0회의 원인 여섯 중
            # 둘만 번역 없이 계산되므로, 모르는 것을 말하지 않고 줄을 붙이지 않는다.
            if bound == 0:
                why = explain_zero_bound(len(result.segments), max_ratio)
                if why is not None:
                    # **`lines`의 불변식은 "1 원소 = 1 줄"이다.** 한 원소에
                    # 개행을 넣으면 화면은 같아 보여도 줄을 세거나 잘라 쓰는
                    # 호출자(테스트의 `_bound_lines`가 그렇다)가 두 줄을 하나로
                    # 세어 조용히 어긋난다.
                    lines.append(f"    사유: {why}")
        for review_path in review_paths.get(target, ()):
            # **실제 실행의 문구와 같은 형태로 낸다**(`_translate_one`의
            # `f"  리포트 {review_path}"`). 다르게 쓰면 두 출력을 눈으로
            # 맞추려는 사용자가 서로 다른 것으로 읽는다.
            #
            # **"낸다"가 아니라 "낼 것이다"로 적는다.** dry-run은 파일을 쓰지
            # 않고 디렉터리도 만들지 않는다(README의 조합 표) - 현재형으로
            # 적으면 이미 있는 줄 알고 다음 단계가 빈손으로 진행한다.
            #
            # **한 줄이 아니라 전부 돈다.** `both`는 두 파일을 내는데 첫 줄만
            # 내면 절반을 예고하는 셈이고, 빠지는 쪽이 화면에 없으므로 사용자는
            # 안 나온다고 읽는다.
            lines.append(f"  리포트 {review_path} (아직 쓰지 않음)")
    lines.append("")
    lines.append("(토큰·비용은 내지 않는다 - 문자에서 토큰으로 가는 계수의 출처가 없다)")
    return lines


def _translate_one(
    *,
    result: IngestResult,
    input_path: Path,
    out_dir: Path | None,
    source_lang: str,
    target_lang: str,
    provider: Provider,
    glossary_path: Path | None,
    work_context: str | None,
    context_window: int,
    cache_dir: Path | None,
    triage_profile: SpecProfile | None,
    budget_ratio: float | None,
    threshold: float | None,
    policy_label: str | None,
    review_out: Path | None,
    review_format: ReviewFormat,
    tier1: _Tier1Settings | None,
    reporter: ProgressReporter,
    weights: Mapping[str, float] | None = None,
) -> int:
    """대상 언어 하나를 번역해 파일로 낸다. 종료 코드 후보를 돌려준다.

    **예외를 여기서 잡아 코드로 바꾼다.** 새어 나가면 미처리 traceback이
    되어 exit 1("부분 실패")로 오보된다 (설계 §8).

    **`result.segments`를 사본 없이 그대로 넘긴다.** `engine.py`가
    `replace(s, target_text=...)`로 **새 튜플**을 만들어 돌려주므로 원본은
    변형되지 않는다 - 여러 언어를 돌아도 앞 언어의 번역문이 남지 않는다.
    방어적 사본을 넣으면 그 사실이 코드에서 사라져 나중에 엔진 쪽 계약이
    깨져도 드러나지 않는다.
    """
    glossary = None
    if glossary_path is not None:
        # **이 try는 호출 하나(`load_glossary`)로 유지해야 한다.** 아래
        # `except Exception`은 `typer.Exit`까지 삼킨다 - `issubclass(typer.Exit,
        # Exception)`이 `True`다(`RuntimeError` 경유, 실측: WP7b Task 4 리뷰
        # 라운드 3). 오늘은 try 안에 호출이 하나뿐이라 도달 불가하지만, 누가
        # 여기 줄을 보태 그 안에서 `typer.Exit(2)`를 던지면 **조용히 66으로
        # 바뀐다.** `KeyboardInterrupt`·`SystemExit`은 `BaseException`이라
        # 이 catch가 삼키지 않는다 - Ctrl+C는 정상 동작한다.
        try:
            glossary = load_glossary(glossary_path, target_lang)
        except Exception as exc:
            # **`Exception`까지 넓힌다.** `load_glossary`는 자기 실패를
            # 정규화하지 않는다 - `yaml.safe_load`의 `yaml.YAMLError`
            # (`ValueError`도 `OSError`도 아니다. 미종료 스칼라·탭 들여쓰기가
            # 여기로 온다)와 형식이 어긋난 YAML의 `TypeError`(`entries: 5` →
            # `enumerate(5)`)·`AttributeError`(`targets: "Hello"` → 문자열에
            # `.get`)가 전부 `(OSError, ValueError)`를 지나쳐 그대로 샜다
            # (실측: WP7b Task 4 리뷰 라운드 1, 넷 다 exit 1). 용어집은
            # 사용자가 준 파일이고 어떤 실패든 "파일 내용이 틀림"(66)이지
            # 이 프로세스가 잘못 짜인 것(1, traceback)이 아니다.
            #
            # **"모든 실패가 66이 된다"는 열린 집합에 대한 단언이라 테스트로
            # 완결할 수 없다** - 넓은 catch가 유일하게 구성상(by construction)
            # 참인 선택이다(리뷰어 판정, WP7b Task 4 리뷰 라운드 3). `glossary/`
            # 자체는 고치지 않는다 - 이번 태스크 범위 밖이고, `ingest.load_subtitle`
            # 처럼 자기 실패를 `GlossaryError`로 모으게 바꾸면 이 태스크가 모르는
            # 다른 호출부(WP5)의 계약이 바뀐다. 이 사실을 여기 남겨 두는 것은,
            # 나중에 `glossary/`가 정규화를 갖추면 "CLI가 이미 넓게 잡고 있으니
            # 좁혀도 안전하다"는 근거가 되게 하기 위해서다.
            #
            # **예외 타입명을 메시지에 넣는다.** `{exc}`만 찍으면
            # `ParserError`(YAML을 고쳐야 함)와 `NameError`(버그를 신고해야 함)가
            # 사용자에게 같은 모양으로 보인다(실측: WP7b Task 4 리뷰 라운드 3 -
            # `NameError`를 주입하면 "name 'entrise' is not defined"만 찍히고
            # 타입이 안 보였다). 넓은 catch를 택한 대가를 이 한 줄이 줄인다.
            _echo(
                f"[{target_lang}] {glossary_path}: 용어집을 읽지 못했다 - "
                f"{type(exc).__name__}: {exc}",
                err=True,
            )
            return EXIT_BAD_INPUT

    if cache_dir is not None:
        identity = _cache_identity(provider)
        if identity is None:
            # 신원을 모르는 프로바이더에 캐시를 걸면 다른 모델의 응답이
            # 히트한다. 끄는 쪽이 안전하고, **조용히 끄지는 않는다** —
            # 사용자는 재개가 되는 줄 안다.
            _echo(
                f"[{target_lang}] 경고: {provider.name}이 cache_identity를 제공하지 "
                f"않아 캐시를 끈다",
                err=True,
            )
        else:
            provider = CachingProvider(
                provider,
                identity=identity,
                cache_dir=cache_dir,
                # **라벨을 붙인다.** 이 함수의 다른 `_echo` 다섯 곳이 전부
                # `[target_lang]`을 붙이는데 여기만 빠져 있었다 - 캐시 쓰기
                # 실패 경고가 언어 수만큼 무라벨로 반복돼 어느 언어의 경고인지
                # 구별할 수 없었다(리뷰어 실측 3회, WP7b Task 5 리뷰가 넘김).
                # `CachingProvider`는 언어마다 새로 만들어지므로 인스턴스당
                # 1회로 막는 `_warn_once`도 이 반복을 막지 못한다.
                warn=lambda message: _echo(f"[{target_lang}] {message}", err=True),
            )

    # ① 번역 (FR-8.5 · 설계 §4.3). **예외 경로에서는 `done()`을 부르지
    # 않는다** - 실패는 아래 `_echo(err=True)`가 말하고 그것이
    # `clear_active()`로 진행 줄을 지운다. 실패 뒤에 "완료"를 찍으면
    # 화면이 거짓말을 한다.
    reporter.phase(f"[{target_lang}] 번역")
    try:
        translated = translate_segments(
            result.segments,
            provider=provider,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary=glossary,
            work_context=work_context,
            context_window=context_window,
            on_progress=reporter.update,
        )
    except FatalProviderError as exc:
        _echo(f"[{target_lang}] 프로바이더가 요청을 거부했다: {exc}", err=True)
        return EXIT_UNAVAILABLE
    except ProviderError as exc:
        # **마지막 그물이다.** 오늘은 도달 불가하다 - `openai_compat.py`는
        # `FatalProviderError`·`RetryableProviderError` 둘 중 하나만 던지고
        # engine의 재시도 루프도 그 둘만 잡는다(실측: WP7b Task 4 리뷰
        # 라운드 1). 그래도 §12 Q3가 "로컬 LLM 백엔드 능력이 균일하지 않다"를
        # 전제하고 NFR-5가 "코드 수정 없이 프로바이더 추가"를 요구하므로,
        # 계약(`Provider.complete`의 "맨 `ProviderError`를 던지면 안 된다")을
        # 어기는 서드파티 구현이 traceback으로 파이프라인을 죽이는 것보다는
        # 69로 막고 원인을 알리는 편이 낫다. `FatalProviderError` 절보다
        # **뒤에** 와야 한다 - 그 절이 자손을 먼저 잡지 않으면 이 절이
        # 죽은 코드가 된다.
        _echo(f"[{target_lang}] 프로바이더가 요청을 거부했다: {exc}", err=True)
        return EXIT_UNAVAILABLE
    except ValueError as exc:
        # 배치·맥락 조립이 틀린 것이므로 명령줄 오류다.
        _echo(f"[{target_lang}] {exc}", err=True)
        return 2
    reporter.done(f"완료 (실패 {len(translated.failures)})")

    out_path = _output_path(input_path, out_dir, source_lang, target_lang)
    try:
        write_subtitle(result, translated.segments, out_path)
    except OSError as exc:
        # `write_subtitle`의 `mkdir(parents=True, exist_ok=True)`와
        # `subs.save(...)`는 자기 방어가 없다(Task 3 리뷰가 넘긴 경고) - 출력
        # 경로 자리에 이미 파일이 있으면 `FileExistsError`, 이미 디렉터리가
        # 있으면 `PermissionError`가 그대로 샌다(실측: WP7b Task 4 리뷰
        # 라운드 1, 둘 다 exit 1). 디스크 상태의 문제이지 명령줄이 틀린 게
        # 아니므로 66이다. `--out`에 `file_okay=False`를 걸어 `--out` 자체가
        # 파일인 흔한 사고는 typer가 먼저 exit 2로 거르지만, 언어별 파일
        # 이름(`_output_path`가 만든 것)이 우연히 기존 디렉터리와 겹치는
        # 경우까지는 명령줄 시점에 알 수 없어 이 try가 마지막 방어선이다.
        _echo(f"{out_path}: 출력 파일을 쓰지 못했다 - {exc}", err=True)
        return EXIT_BAD_INPUT
    except Exception as exc:
        # **`OSError` 하나로는 그물이 샌다 - 아래 `write_review`의 형제다.**
        # LLM이 낸 문자열에 고립 서로게이트가 있으면 `subs.save`가
        # `UnicodeEncodeError`(= `ValueError` 하위, `OSError`가 **아니다**)를
        # 내고 그것이 미처리 traceback으로 새어 **exit 1**이 됐다(실측:
        # 10큐 중 5번째만 오염시킨 실행에서 exit 1 + traceback). 도달 경로는
        # 가정이 아니다 - `openai_compat.py`의 `response.json()`이 서로게이트를
        # 그대로 통과시키고 `isinstance(content, str)`도 지나간다(§12 Q3가
        # 백엔드 능력의 불균일을 전제한다).
        #
        # **exit 1이 최악인 이유는 여기서도 "구별되지 않는다"이다.** 1은 이
        # CLI에서 "규격 위반 발견 또는 번역 일부 실패"라, 번역 실패가 함께
        # 있는 흔한 실행에서는 **정상 종료와 종료 코드가 완전히 같다**(실측:
        # 대조군도 1). CI는 자막을 그대로 쓰고 넘어간다.
        #
        # **70이지 66이 아니다 - 같은 오염 문자열이 두 산출물에 함께 간다.**
        # 아래 `write_review`의 그물이 같은 `UnicodeEncodeError`를 70으로 받는데
        # (근거는 그 주석), 그 문자열의 출처는 `translated.segments`의
        # `target_text` 하나로 같다. 여기만 66이면 **같은 원인이 어느 쓰기가
        # 먼저 죽느냐에 따라 다른 코드를 낸다.** 게다가 자막 쓰기가 먼저라
        # (`write_review`는 트리아지 뒤다) 66이면 70은 영영 관측되지 않는다.
        # 66은 "사용자가 준 파일이 틀렸다"인데 사용자의 자막은 멀쩡했다 -
        # 틀린 것은 프로바이더의 응답을 그대로 파일로 보낸 **우리 쪽**이다.
        # `OSError` 절이 66을 유지하는 것은 그쪽이 진짜 디스크 사정이기
        # 때문이고, 이 절보다 **앞에** 와야 한다(뒤에 오면 죽은 코드가 된다).
        #
        # **"모든 실패가 70이 된다"는 열린 집합에 대한 단언이라 테스트로
        # 완결할 수 없다** - 넓은 catch가 유일하게 구성상(by construction) 참인
        # 선택이다. 이 파일의 `load_glossary` 그물과 `write_review` 그물이 같은
        # 판정을 이미 내렸고 여기는 그 셋째다. `KeyboardInterrupt`·`SystemExit`은
        # `BaseException`이라 삼키지 않는다. `typer.Exit`은 `Exception` 하위지만
        # `try` 안의 호출이 `write_subtitle` 하나뿐이고 `ingest/writer.py`는
        # typer를 import하지 않는다(`contextlib`·`copy`·`os`·`re`·`pathlib`·
        # `cuesift.*`뿐) - **여기 줄을 보태면 그 전제가 깨진다.**
        #
        # **예외 타입명을 병기한다.** `{exc}`만 찍으면 `UnicodeEncodeError`
        # (모델 출력이 오염됐다)와 `AttributeError`(버그를 신고해야 한다)가
        # 사용자에게 같은 모양으로 보인다.
        #
        # **잘린 파일은 `write_subtitle`이 원자적 쓰기로 막는다.** 여기서
        # 지우려 들면 지난 실행의 정상 자막까지 함께 지운다.
        _echo(
            f"{out_path}: 출력 파일을 쓰지 못했다 - {type(exc).__name__}: {exc}",
            err=True,
        )
        return EXIT_NOT_IMPLEMENTED

    # `cache_dir`이 `None`이면(--no-cache 또는 신원 없음 경고) provider는
    # CachingProvider로 감싸이지 않아 hits·misses 속성 자체가 없다. 그때
    # `getattr(..., 0)`으로 읽으면 "0개"가 되는데, `_format_translate_summary`가
    # 요구 정정 3에 따라 그것을 "0개 호출"과 구별해야 하므로 캐시가 실제로
    # 붙었는지 여부를 별도 플래그로 넘긴다.
    cached = isinstance(provider, CachingProvider)
    hits = provider.hits if cached else 0
    misses = provider.misses if cached else 0
    for line in _format_translate_summary(
        target_lang=target_lang,
        out_path=out_path,
        result=translated,
        cache_enabled=cached,
        hits=hits,
        misses=misses,
    ):
        _echo(line)

    if triage_profile is not None and policy_label is not None:
        # **두 조건을 함께 본다.** 둘은 `translate` 본문에서 같은 가드
        # (`if triage_requested`) 안에 함께 설정되므로 실제로는 항상 같이
        # 있거나 같이 없다. 그럼에도 둘 다 검사하는 것은 `_run_triage`가
        # `policy_label: str`(None 불가)을 받기 때문이다 - 여기가 타입을
        # 좁히는 유일한 지점이고, 한쪽만 보면 None이 함수 경계를 넘는다.
        #
        # 요약 출력 **뒤**에 온다 - `_format_translate_summary`가 실패 ID를
        # 먼저 나열하고, 트리아지 요약은 그것이 분모에서 빠졌다고 말한다.
        # 순서가 뒤집히면 "3건 제외"가 무엇을 가리키는지 알 수 없다.
        try:
            outcome = _run_triage(
                target_lang=target_lang,
                profile=triage_profile,
                glossary=glossary,
                source_lang=source_lang,
                translated=translated,
                budget_ratio=budget_ratio,
                threshold=threshold,
                policy_label=policy_label,
                tier1=tier1,
                reporter=reporter,
                weights=weights,
            )
        except FatalProviderError as exc:
            # **Tier 1이 이 함수에서 LLM을 부르는 유일한 자리다**(설계 D14 · §3.4).
            # 그물이 없으면 401이 미처리 traceback이 되어 exit 1이 되는데, 이 파일
            # 머리말의 표에서 1은 "규격 위반 발견"이라 **설정 실수가 자막 결함으로
            # 오보되고** 사용자는 멀쩡한 자막을 고치려 든다. 번역 경로(위쪽
            # `translate_segments` 호출부)가 이미 같은 둘을 69로 낸다 - 대칭을 맞춘다.
            #
            # **문구에 `Tier 1`을 넣는 것이 계약이다.** 종료 코드만으로는 번역
            # 경로의 69와 구별되지 않아, 사용자도 테스트도 어느 층이 죽었는지
            # 모른다. 번역 파일은 이미 나갔고 트리아지만 못 돈 상태다.
            _echo(f"[{target_lang}] Tier 1 프로바이더가 요청을 거부했다: {exc}", err=True)
            return EXIT_UNAVAILABLE
        except ProviderError as exc:
            # **마지막 그물이고, 오늘은 도달 불가하다.** `signals/llm.py:162-173`이
            # Tier 1의 전파 계약을 못 박는다 - `RetryableProviderError`는
            # `translate_segments`가 이미 삼켜 `SegmentFailure`가 되고 위 절이 잡는
            # `FatalProviderError`만 여기까지 올라온다. 그래도 절을 남기는 것은,
            # 계약(`Provider.complete`의 "맨 `ProviderError`를 던지면 안 된다")을
            # 어기는 서드파티 구현이 traceback으로 파이프라인을 죽이는 것보다
            # 69로 막고 원인을 알리는 편이 낫기 때문이다(NFR-5 · §12 Q3).
            # **그래서 위 절과 합치지 않는다** - 위는 실재하는 경로고 여기는
            # 계약 위반 전용 그물이다. 번역 경로의 같은 이름 절과 짝이다.
            #
            # **절 순서는 지금 관측 불가하다.** 두 절의 본문이 글자 그대로 같아서
            # 맞바꿔도 죽는 테스트가 **0건**이다(축2 리뷰 실측: 전량 생존). 자손이
            # 뒤로 가면 이 절이 위를 가려 죽은 코드가 되지만, 가려진 결과가
            # 원본과 바이트 동일이라 아무도 알아채지 못한다. 형제인 아래
            # `except ValueError`와의 상대 순서는 의미조차 없다.
            #
            # 순서가 실제로 의미를 갖는 것은 **두 문구를 가르는 날**이고, 그때
            # 틀린 순서를 잡을 게이트는 이 저장소에 없다 - 문구를 가르는 변경은
            # 순서 회귀 테스트를 함께 들고 와야 한다.
            _echo(f"[{target_lang}] Tier 1 프로바이더가 요청을 거부했다: {exc}", err=True)
            return EXIT_UNAVAILABLE
        except ValueError as exc:
            # **정책 오류가 exit 1로 새는 것을 막는다.** 이 파일 머리말의 표에서
            # 1은 "규격 위반 발견"이라, 잡지 않으면 미처리 traceback이 exit 1이
            # 되어 **설정 실수가 자막 결함으로 오보되고** 사용자는 멀쩡한 자막을
            # 고치려 든다. `--review-threshold nan` 가드가 정확히 같은 이유로
            # 앞단에 있다 - 여기는 그 가드가 놓친 경로를 받는 두 번째 그물이다.
            #
            # 여기로 오는 것: `select_by_*`의 범위·NaN 검사, `fuse`의 가중치
            # 검사, `_run_triage`의 "budget·threshold가 둘 다 None" 불변식.
            # 셋 다 **설정이 틀린 것**이지 자막이 틀린 것이 아니므로 2다.
            #
            # 넷째로 `TriageOutcome`의 생성자 불변식(id 집합 불일치·음수
            # `excluded_failures`)도 `ValueError`라 이 그물에 걸린다. 그것만은
            # 설정이 아니라 **내부 결함**이라 2가 정확한 표식은 아니지만,
            # `_run_triage`가 `risks`와 `segments`를 같은 `kept`에서 만들므로
            # 도달 경로가 없다. 도달한다면 그때는 코드가 틀린 것이다.
            #
            # **review.json 배선(FR-7.2)이 이 판정을 다시 했다.** 배선은
            # `outcome`을 읽어 파일로 쓰기만 하고 생성에는 관여하지 않으므로
            # 도달 불가가 유지된다 - 따라서 여기를 쪼개지 않았다. 나중에
            # `TriageOutcome`을 다른 재료로 만드는 경로가 생기면 그때는 이
            # `except`를 분리해 내부 결함을 70으로 내야 한다. 지금 미리
            # 쪼개면 어떤 테스트도 밟지 못하는 가지가 생긴다.
            #
            # 번역 파일은 이미 나갔다 - 트리아지만 못 돌린 것이고, 그 사실을
            # 말하지 않으면 사용자는 번역까지 실패한 줄 안다.
            _echo(f"[{target_lang}] 트리아지를 돌리지 못했다: {exc}", err=True)
            return 2

        if not outcome.risks:
            # **전량 실패를 화면에서 구별한다.** `review_ratio`는 이때 0.0을
            # 내지만(빈 목록 가드, `policy.py:194-195`) "검수 대상 0개"는
            # "볼 것이 없다"로 읽힌다 - 실제로는 **판정 자체를 못 한 것**이다.
            # `_run_triage`가 아니라 여기서 만드는 것은, 결과 객체는 전량
            # 실패에서도 나와야 `review.json`이 "왜 비었나"를 남길 수 있기
            # 때문이다(설계 D8).
            _echo(
                f"[{target_lang}] 트리아지: 번역된 세그먼트가 없어 건너뛴다 "
                f"(전량 {outcome.excluded_failures}건 실패)"
            )
        else:
            for line in _format_triage_summary(outcome):
                _echo(line)

        if review_out is not None:
            # ③ 리포트 (FR-8.5). **`ReviewFormat`이 세 값뿐이라** 이 블록에
            # 들어온 이상 JSON·HTML 중 최소 하나는 반드시 나간다 - "리포트
            # 기록 완료"가 아무것도 안 쓰고 찍히는 조합이 없다.
            reporter.phase(f"[{target_lang}] 리포트")
            # **요약 출력 뒤에 온다.** 화면이 먼저 수치를 말하고 그 다음
            # 줄이 "그 수치가 어느 파일에 들어갔다"를 말한다. 순서를
            # 뒤집으면 경로가 수치보다 먼저 나와 어느 실행의 산출물인지
            # 눈으로 못 맞춘다.
            #
            # **전량 실패도 파일을 낸다 - 조건을 달지 않는다.** 그때
            # `segments`가 비고 `excluded_failures`가 사실을 말한다. 파일이
            # 아예 없으면 소비자는 "실행이 안 됐다"와 "번역이 전량
            # 실패했다"를 구분하지 못한다(설계 D8).
            #
            # `outcome.risks or outcome.excluded_failures`로 감싸는 형태를 쓰지
            # 않은 이유는 그 조건이 **여기서 언제나 참**이기 때문이다. 항상 참인
            # `if`는 거짓 가지를 어떤 테스트도 밟지 못하고, `pyproject.toml`에
            # branch coverage 설정이 없어 커버리지에도 안 잡혀 "검사하지 않고
            # 통과하는 게이트"가 된다.
            #
            # **증명 사슬은 네 고리다. 하나라도 끊기면 이 주석이 거짓이 된다** -
            # 넷 중 셋이 이 파일 밖에 있으므로 그쪽을 고치는 사람이 여기가
            # 자기 변경의 영향권임을 알아야 한다(실측: Task 6 리뷰 계약 축).
            #
            # 1. `select_by_*`가 **전체 목록**을 반환한다(`triage/policy.py:95`·`:114`).
            #    선별분만 반환하도록 바뀌면 `risks`가 비고 `excluded_failures`가
            #    0인 조합이 생긴다.
            # 2. `translate_segments`가 **길이를 보존**한다(`translate/engine.py:197`).
            #    실패분을 `segments`에서 빼도록 바뀌면 `translated.segments`가 빈다.
            # 3. 1·2에서 `risks`가 빈다 ⟺ `kept`가 빈다 ⟺ 전량 실패이거나 트랙이 0개다.
            # 4. 트랙 0개는 `load_subtitle`이 `IngestError("empty")`로 거부한다
            #    (`ingest/loader.py:68-73`). 이것이 없으면 0큐 트랙이 여기까지 온다.
            #
            # 사슬이 끊겨도 **동작은 안전하다** - 조건이 거짓이어도 리포트를 내는
            # 것이 설계 의도다. 깨지는 것은 이 주석의 주장이지 동작이 아니다.
            #
            # **트리아지를 돌린 언어만 여기 온다.** 프로파일이 없어 건너뛴
            # 언어는 바깥 `if triage_profile is not None`에서 이미 걸러졌다 -
            # 그 언어에 빈 리포트를 내면 소비자가 "검수했고 걸린 것이 없다"로
            # 읽는데 실제로는 판정 자체를 못 한 것이다(D7).
            # **형식으로 가른다 - `json`과 `both`만 JSON을 낸다** (FR-7.3 · 설계 D1).
            # 위 주석들(요약 뒤 순서 · 전량 실패에도 파일을 낸다는 D8 · 그 증명
            # 사슬)은 형식과 무관하게 두 산출물 모두에 걸린다. 그래서 이 분기는
            # 그것들보다 **안쪽**에 있다 - 밖으로 끌어내면 HTML 쪽이 같은 근거를
            # 잃고, 잃은 줄 모른 채 '비면 내지 말자'로 되돌아간다.
            if review_format in (ReviewFormat.JSON, ReviewFormat.BOTH):
                review_path = _review_path(input_path, review_out, source_lang, target_lang)
                try:
                    write_review(outcome, review_path)
                except OSError as exc:
                    # 디스크 상태의 문제다. 번역 파일은 이미 나갔다(설계 §3.4) -
                    # 그 사실을 말하지 않으면 사용자는 번역까지 실패한 줄 알고
                    # LLM 호출을 통째로 다시 쓴다.
                    _echo(f"{review_path}: 검수 리포트를 쓰지 못했다 - {exc}", err=True)
                    return EXIT_BAD_INPUT
                except Exception as exc:
                    # **`Exception`까지 넓힌다 - `TypeError` 하나로는 그물이 샌다.**
                    # `json.dumps`의 실패는 열린 집합이다(실측, Task 6 리뷰 계약 축 I1):
                    # 순환 참조는 **`ValueError`**("Circular reference detected")이고
                    # 깊은 중첩은 `RecursionError`, `write_text`의 서로게이트는
                    # `UnicodeEncodeError`(= `ValueError` 하위)다. `TypeError`만 잡으면
                    # 셋 다 미처리 traceback이 되어 **exit 1**로 나간다.
                    #
                    # **exit 1이 최악인 이유가 바로 여기 있다.** 이 CLI에서 1은 "규격
                    # 위반 발견 또는 번역 일부 실패"라, 번역 실패가 함께 있는 흔한
                    # 실행에서는 **정상 종료와 종료 코드가 완전히 같아진다**(실측:
                    # 대조군도 exit 1). CI는 번역을 재시도하고 리포트는 영영 안 나온다.
                    # 66도 안 된다 - 66은 "사용자가 준 파일이 틀렸다"이고 이것은 우리
                    # 코드가 틀린 것이다.
                    #
                    # **"모든 직렬화 실패가 70이 된다"는 열린 집합에 대한 단언이라
                    # 테스트로 완결할 수 없다** - 넓은 catch가 유일하게 구성상
                    # (by construction) 참인 선택이다. 이 파일의 `load_glossary` 그물
                    # (위쪽 `except Exception`)이 같은 판정을 이미 내려 두었고 여기는
                    # 그 형제다.
                    #
                    # **삼킬 위험 둘을 확인했다.** `KeyboardInterrupt`·`SystemExit`은
                    # `BaseException`이라 이 catch가 잡지 않는다 - Ctrl+C는 정상
                    # 동작한다. `typer.Exit`은 `Exception` 하위라 삼킬 수 있지만
                    # `try` 안의 호출이 `write_review` 하나뿐이고 `json_report.py`는
                    # typer를 import하지 않는다(`json`·`pathlib`·`typing`·`cuesift.*`뿐).
                    # **여기 줄을 보태면 그 전제가 깨진다.**
                    #
                    # **예외 타입명을 병기한다.** `{exc}`만 찍으면 `ValueError`(리포트
                    # 구조에 순환이 생겼다)와 `NameError`(버그를 신고해야 한다)가
                    # 사용자에게 같은 모양으로 보인다. 넓은 catch를 택한 대가를 이 한
                    # 줄이 줄인다 - `load_glossary` 그물이 같은 이유로 같은 형식을 쓴다.
                    _echo(
                        f"{review_path}: 검수 리포트를 직렬화하지 못했다 - "
                        f"{type(exc).__name__}: {exc}",
                        err=True,
                    )
                    return EXIT_NOT_IMPLEMENTED
                _echo(f"  리포트 {review_path}")

            if review_format in (ReviewFormat.HTML, ReviewFormat.BOTH):
                # **JSON 뒤에 온다.** `both`에서 하나가 실패하면 거기서 멈추고
                # 이미 나간 JSON은 지우지 않는다 - 부분 산출물이 남는 편이
                # 낫다. 되돌리려면 성공한 파일을 지워야 하는데, 그 삭제가
                # 실패하면 무엇이 남았는지 아무도 모르는 상태가 된다.
                html_path = _report_path(input_path, review_out, source_lang, target_lang)
                try:
                    write_html(outcome, html_path)
                except OSError as exc:
                    # 디스크 상태의 문제다. 번역 파일은 이미 나갔고 `both`라면
                    # JSON도 나갔다 - 그 사실을 말하지 않으면 사용자는 번역까지
                    # 실패한 줄 알고 LLM 호출을 통째로 다시 쓴다.
                    _echo(f"{html_path}: HTML 리포트를 쓰지 못했다 - {exc}", err=True)
                    return EXIT_BAD_INPUT
                except Exception as exc:
                    # **`Exception`까지 넓힌다 - 실패 집합이 열려 있다.**
                    # `Template.substitute`는 자리표시자가 빠지면 `KeyError`를,
                    # `write_text`는 서로게이트가 섞이면 `UnicodeEncodeError`를
                    # 낸다. 좁히면 나머지가 미처리 traceback이 되어 **exit 1**로
                    # 나가는데, 1은 이 CLI에서 "규격 위반 발견 또는 번역 일부
                    # 실패"라 번역 실패가 함께 있는 흔한 실행에서는 **정상 종료와
                    # 종료 코드가 완전히 같아진다**. 바로 위 `write_review` 그물이
                    # 같은 판정을 이미 내려 두었고 여기는 그 형제다.
                    #
                    # **예외 타입명을 병기한다.** `{exc}`만 찍으면 `KeyError`
                    # (템플릿이 틀렸다 - 우리가 고친다)와 `NameError`(버그를
                    # 신고해야 한다)가 사용자에게 같은 모양으로 보인다.
                    _echo(
                        f"{html_path}: HTML 리포트를 만들지 못했다 - {type(exc).__name__}: {exc}",
                        err=True,
                    )
                    return EXIT_NOT_IMPLEMENTED
                _echo(f"  리포트 {html_path}")

            # 실패 경로는 위에서 전부 `return`으로 빠졌다 - 여기 오면
            # 산출물이 실제로 나갔다는 뜻이다.
            reporter.done("기록 완료")

    return EXIT_TRANSLATION_FAILURE if translated.failures else 0


def _cache_identity(provider: Provider) -> str | None:
    """프로바이더가 자기 신원을 말하게 한다 (설계 §3.2). 없으면 `None`.

    `getattr`로 읽는 이유는 `Provider` 프로토콜에 이 속성이 **없기**
    때문이다 — 표면을 최소로 두는 [번역 엔진 설계] §4.1의 결정을 유지한다.

    **예외를 던지지 않고 `None`을 돌려주는 것이 요점이다.** 캐시를 못 켜는
    것은 실행이 불가능한 상태가 아니다 — 호출자가 경고하고 캐시 없이 돈다.
    """
    identity = getattr(provider, "cache_identity", None)
    return str(identity) if identity else None


# 계측 불능을 알리는 **공용 문구.** 번역 요약과 트리아지 요약이 각자 적으면
# 같은 실행에서 두 문장이 다른 말을 하게 되고, 사용자는 그것을 서로 다른 두
# 문제로 읽는다. 한쪽만 고쳐지는 것도 같은 결과다.
#
# **출력 문자열이라 em dash를 쓰지 않는다**(`test_help_output_has_no_em_dash`와
# 같은 규율). 마침표로 끊는다.
_UNREPORTED_TOKENS_NOTE = "백엔드가 토큰 수치를 내지 않았다. 0은 실제 사용량이 아니다"


def _format_translate_summary(
    *,
    target_lang: str,
    out_path: Path,
    result: TranslationResult,
    cache_enabled: bool,
    hits: int,
    misses: int,
) -> list[str]:
    """언어 하나의 결과를 요약한다 (설계 §4.3·§5.3).

    **캐시 히트를 항상 낸다.** 캐시가 곧 재개이므로 이 숫자가 "재개됐다"의
    유일한 증거다. 없으면 사용자는 네트워크를 탔는지 알 수 없다.

    **"0개"와 "꺼져 있음"을 구별한다.** `--no-cache`이거나 프로바이더가
    `cache_identity`를 제공하지 않으면 `CachingProvider`가 끼지 않아
    hits·misses를 셀 수단 자체가 없다. 그것을 "실제 호출 0개"로 찍으면
    네트워크를 여러 번 타고도 화면은 "안 탔다"고 거짓말한다 — 설계 §4.3이
    캐시 히트 수를 재개의 유일한 증거로 선언한 바로 그 줄이 거짓을 말하게
    되는 것이다.

    **실패는 개수만이 아니라 ID를 나열한다.** 원문이 남은 자막은 겉보기에
    정상인 파일이라, 개수만 보고 넘기면 미번역 자막이 그대로 배포된다.
    """
    total = len(result.segments)
    failed = len(result.failures)
    cache_line = f"  캐시 히트 {hits}개 · 실제 호출 {misses}개" if cache_enabled else "  캐시 꺼짐"
    # **화면은 침묵하지 않고 0을 사실로 주장한다.** usage를 안 내는 백엔드에서
    # 이 줄은 `토큰 prompt 0 · completion 0 · calls 8`이 되는데, 한 줄 안에
    # 모순이 있는데도 아무도 지적하지 않는다(§12 Q3의 "탐지·명시").
    #
    # **`review.json`으로는 이 사람을 못 구한다** - 그 파일은 `--review-out`이
    # 있어야 생기므로 기본 경로 사용자는 신호를 전혀 보지 못한다.
    #
    # 판별식을 여기 두지 않고 `layer_tokens_reported`를 부르는 이유는 화면과
    # 파일이 같은 실행에서 다른 말을 하면 사용자가 둘 중 하나를 오작동으로
    # 읽기 때문이다.
    token_line = (
        f"  토큰 prompt {result.usage.prompt_tokens} · completion "
        f"{result.usage.completion_tokens} · calls {result.usage.calls}"
    )
    if not layer_tokens_reported(result.usage):
        token_line += f" ({_UNREPORTED_TOKENS_NOTE})"
    lines = [
        f"[{target_lang}] {out_path}",
        f"  세그먼트 {total}개 · 성공 {total - failed}개 · 실패 {failed}개",
        cache_line,
        token_line,
    ]
    lines.extend(_format_failure_lines(result.failures))
    return lines


def _format_failure_lines(failures: Sequence[SegmentFailure]) -> list[str]:
    """실패 세그먼트를 사유별로 묶어 낸다 (FR-2.6 · 파킹 #2).

    **개수만 내면 안 되는 이유는 이미 이 파일에 있었다** - 원문이 남은 자막은
    겉보기에 정상인 파일이라 미번역 자막이 그대로 배포된다. 여기에 사유를
    더하는 이유는 그 다음 질문이 "그래서 다시 돌리면 되나"이기 때문이다:
    `provider_error`는 서버·설정을, `empty_translation`은 모델을 가리킨다.

    **많은 사유부터, 같으면 이름순으로 낸다.** 삽입 순서를 그대로 쓰면 배치
    스케줄에 따라 줄 순서가 바뀌어 같은 입력이 다른 화면을 낸다(NFR-3).
    이름순만으로 정렬하면 1건짜리 사유가 800건짜리 위에 올라와, 사용자가
    가장 먼저 손대야 할 원인이 화면 아래로 밀린다.

    **시도 횟수를 범위로 낸다.** 같은 사유라도 배치 경로와 개별 폴백 경로의
    `attempts`가 다르다 - 하나로 뭉치면 "4번 버텼다"와 "한 번에 죽었다"가
    같은 줄로 보인다.

    **사유를 화이트리스트로 거르지 않는다.** `engine.py`가 사유를 하나 더
    넣었을 때 화면에서 조용히 사라지면 그것이 정확히 이 함수가 고치는 결함이다.
    """
    if not failures:
        return []
    grouped: dict[str, list[SegmentFailure]] = {}
    for failure in failures:
        grouped.setdefault(failure.reason, []).append(failure)
    lines = [f"  실패 세그먼트(원문 유지) {len(failures)}건:"]
    for reason in sorted(grouped, key=lambda r: (-len(grouped[r]), r)):
        group = grouped[reason]
        attempts = sorted({f.attempts for f in group})
        span = f"{attempts[0]}" if len(attempts) == 1 else f"{attempts[0]}~{attempts[-1]}"
        ids = ", ".join(f.segment_id for f in group)
        lines.append(f"    {reason} {len(group)}건 (시도 {span}회): {ids}")
    return lines


def _format_triage_summary(outcome: TriageOutcome) -> list[str]:
    """트리아지 결과를 요약한다 (FR-7.4 · 설계 §7.1).

    **수치를 여기서 세지 않는다.** `TriageOutcome`의 프로퍼티를 읽는다 -
    `review.json`이 같은 수치를 내는데 두 곳에서 각자 세면 화면과 파일이
    갈라지고, 갈라져도 프로그램은 정상 종료하고 파일도 정상이라 종료 코드로는
    알 수 없다(review.json 설계 D8). 이 함수가 세던 넷(대상 수·검수 대상
    수·hard fail 수·신호별 집계)이 전부 그쪽 프로퍼티로 옮겨졌고, **"`risks`는
    `select_by_*`가 돌려준 전체 목록이다"**라는 계약도 함께 옮겨졌다
    (`report/models.py`의 `TriageOutcome` 독스트링 · `triage/policy.py`
    모듈 독스트링). 선별분만 담으면 `review_ratio`가 언제나 1.0이 되고, 그
    값이 스펙 §6.2의 "실제 검수 비율"이자 README 배수의 분모다.

    **요청 예산과 실제 비율을 함께 낸다.** hard fail이 quota를 소진하므로
    (`policy.py:92`) 둘은 정기적으로 어긋나고, `ceil` 하나로도 어긋난다
    (26건에 10%면 실제 11.5%). 요청만 찍으면 사용자가 배수를 틀린 분모로
    재계산한다.

    `excluded_failures`가 0이면 괄호를 내지 않는다 - 실패가 없는 정상
    실행에서 "(번역 실패 0건 제외)"는 없는 문제를 있는 것처럼 보이게 한다.
    실패 ID 자체는 `_format_translate_summary`가 바로 위에서 나열했으므로
    여기서 반복하지 않는다(설계 §7.1).
    """
    total = outcome.triaged_segments
    selected = outcome.selected_for_review
    hard = outcome.hard_fail_count
    # `reasons`가 0점 신호를 담지 않으므로 이 집계가 곧 "적발 건수"라는 근거는
    # `signal_hits` 프로퍼티의 독스트링에 있다(`report/models.py`).
    counts = outcome.signal_hits

    scope = f"  대상 세그먼트 {total}개"
    if outcome.excluded_failures:
        scope += f" (번역 실패 {outcome.excluded_failures}건 제외)"

    lines = [
        # **프로파일 이름을 낸다.** 이것이 없으면 `profiles[target]`에 **다른 언어의**
        # 프로파일이 들어가도 어떤 테스트도 잡지 못한다 - Task 2 리뷰(축A I4)가
        # `profiles[target] = load_builtin("ko")` 변이로 실측했다: 키 집합만 검증되고
        # 값은 검증되지 않아 전 스위트가 통과한다. 사용자에게도 "어느 규격으로
        # 검사했는가"가 필요한 정보다(FR-5.1이 규격을 언어별로 정의한다).
        f"[{outcome.target_lang}] 트리아지 "
        f"({outcome.policy_label}, 프로파일 {outcome.profile_name})",
        scope,
        # **`_format_ratio`를 재사용한다 - `f"{x:.1%}"`로 직접 찍지 않는다.**
        # 직접 찍으면 세그먼트 2001개 중 검수 대상 1개(0.05%)가 `"0.0%"`가 되는데,
        # `_format_ratio`의 독스트링이 그것을 이 저장소의 **1급 결함**으로 이미 적어
        # 두었다 - "검수자가 위반 목록을 눈앞에 두고 요약만 보면 '0%니까 통과'로
        # 읽는다." 트리아지 요약에도 같은 위험이 있다: 검수 대상이 하나라도 있으면
        # 0%로 보여선 안 된다. `_format_ratio`는 그 경우 `"<0.1%"`를 낸다.
        #
        # **`* 100`이 필수다.** `_format_ratio`는 0~100 **퍼센트**를 받고
        # (`percent < 0.05`가 0.05%를 뜻한다) `review_ratio`는 0~1 **비율**을 낸다.
        # 빼먹으면 실제 10%가 `"0.1%"`로 찍히고 프로그램은 정상 종료한다 -
        # 종료 코드로는 알 수 없는 조용한 100배 축소다.
        f"  검수 대상 {selected}개 (실제 {_format_ratio(outcome.review_ratio * 100)})",
        f"  hard fail {hard}개",
    ]
    if counts:
        lines.append("  신호별 적발")
        # **정렬을 여기서 다시 하지 않는다.** `signal_hits`가 이미 정렬해
        # 반환한다(NFR-3 재현성 - Counter의 순서는 삽입 순이라 세그먼트 순서가
        # 바뀌면 화면이 달라지고 테스트가 흔들린다). 정렬이 두 곳에 있으면
        # 한쪽만 고쳐지고, 그때 화면과 `review.json`의 신호 순서가 갈라진다.
        lines.extend(f"    {name} {count}개" for name, count in counts.items())
    # **번역 요약의 경고만으로는 이 사람을 구하지 못한다.** 그쪽은
    # `result.usage`, 즉 **번역 계층만** 본다. "번역은 상용 API라 토큰을 내고
    # Tier 1만 로컬이라 못 내는" 구성에서는 번역 줄이 정상으로 보이고,
    # `review.json`은 `--review-out`이 있어야 생기므로 **기본 경로 사용자는
    # Tier 1의 무음을 어디서도 못 본다**(§12 Q3 · NFR-2).
    #
    # **비어 있으면 아무 줄도 내지 않는다.** 언제나 붙는 경고는 읽히지 않는다.
    if outcome.cost_unreported:
        층 = ", ".join(outcome.cost_unreported)
        lines.append(f"  토큰 수치를 못 받은 계층: {층} ({_UNREPORTED_TOKENS_NOTE})")
    return lines


@dataclass(frozen=True, slots=True)
class _Tier1Settings:
    """`--tier1-*`가 조립한 Tier 1 실행 설정 (FR-4.3 · 설계 §5).

    **낱개 파라미터로 풀지 않는다.** `_run_triage`는 이미 키워드 인자 8개를
    받고 같은 파일의 `translate`·`_translate_one`이 각각 15개가 넘는다 - 여섯을
    더 풀면 파라미터 폭발을 키운다.

    `counting`을 `Provider`가 아니라 `CountingProvider`로 좁혀 받는 이유는
    **호출자가 실행 후 `.usage`를 읽어야 하기 때문이다**(FR-7.4). 넓게 받으면
    그 자리에서 `hasattr` 검사를 하게 된다.

    **`counting`은 대상 언어마다 새로 만들어야 한다.** 누적기이므로 여러 언어가
    한 인스턴스를 공유하면 뒤 언어의 `review.json`이 앞 언어의 토큰까지 실어
    Tier 1 비용이 언어 수만큼 부풀어 보인다.

    **`counting.inner`는 캐시로 감싸지 않은 raw 프로바이더여야 한다**(설계 D7).
    `triage_with_tier1`의 `_provider_factory`가 이것을 `CachingProvider`로 다시
    감싸므로 계측이 캐시 **안쪽**에 놓인다 - 캐시 히트는 토큰을 쓰지 않으므로
    세면 안 된다. 이미 감싼 것을 넣으면 히트까지 세어 `cost`가 부풀고 이중
    캐시가 된다.

    **`identity`도 raw에서 뽑아야 한다.** `CachingProvider`는 `cache_identity`를
    위임하지 **않으므로**(Ruling R40 - 위임하면 이중 래핑이 조용히 켜진다)
    감싼 뒤에 물으면 `None`이 나와 Tier 1 캐시가 통째로 꺼진다.
    """

    counting: CountingProvider
    max_ratio: float
    samples: int
    temperature: float
    cache_dir: Path | None
    identity: str | None


def _run_triage(
    *,
    target_lang: str,
    profile: SpecProfile,
    glossary: Glossary | None,
    source_lang: str,
    translated: TranslationResult,
    budget_ratio: float | None,
    threshold: float | None,
    policy_label: str,
    reporter: ProgressReporter,
    tier1: _Tier1Settings | None = None,
    weights: Mapping[str, float] | None = None,
) -> TriageOutcome:
    """번역 결과를 트리아지해 결과 객체를 낸다 (FR-6.1~6.3 · 설계 §4).

    **번역 실패분을 입력에서 뺀다.** `TranslationResult`가 독스트링으로
    요구하는 계약이다 - 실패분은 `segments`에 `target_text=None`으로 남아
    `struct.empty`가 `hard_fail=True`를 내고(`structural.py:166-172`), hard
    fail은 예산 quota를 소진해 진짜 오류를 큐에서 밀어낸다. 실측(200큐·진짜
    오류 20건·예산 10%): 실패 20건에서 **Recall@10%가 0%**가 되고 30건에서는
    실제 비율이 15%로 부풀어 배수의 분모까지 망가진다. 번역 안 된 자막은
    검수 대상이 아니라 **재실행 대상**이다.

    **그러나 빼는 자리는 융합이지 수집이 아니다.** `collect_all`에는 트랙
    전체를 넘긴다 - `spec.overlap`이 `BatchCollector`라 이웃을 봐야 판정되고,
    실패분을 수집 단계에서 빼면 그것과 겹치는 **성공한** 큐의 겹침까지 함께
    사라진다. 본문의 두 층 주석을 참고할 것.

    **전량 실패에서도 객체를 낸다.** 요약 문자열을 조기 반환하면 `review.json`이
    "왜 비었나"를 말할 수 없다 - `risks=()`와 `excluded_failures=N`이 그 사실을
    파일에 남긴다. 화면 문구는 호출자가 만든다(설계 D8).

    **`tier1`이 있으면 선별을 `triage_with_tier1`에 통째로 맡긴다**(FR-4.3).
    수집·융합·예산 적용을 그쪽이 다시 하므로 여기서 `collect_all`을 부르지
    않는다 - 부르면 전량 Tier 0 수집이 두 번 돈다. `tier1`이 `None`이면 기존
    경로는 한 줄도 바뀌지 않는다(설계 D2 - 기본 꺼짐).

    **`weights`는 설정 파일에서만 온다**(FR-8.4 · FR-6.1 · 설계 D6). `None`이면
    `fuse`가 `DEFAULT_WEIGHTS`를 쓴다. **세 `fuse` 호출에 모두 가야 한다** -
    여기 하나와 `triage_with_tier1` 안의 둘이며, 하나라도 빠지면 `--tier1`
    유무로 순위가 갈린다(설계 §4.3 ②).
    """
    failed_ids = {f.segment_id for f in translated.failures}
    kept = [seg for seg in translated.segments if seg.id not in failed_ids]

    # **정책 판정이 전량 실패 검사보다 앞에 온다.** `TriageOutcome`은 전량
    # 실패에서도 `policy_kind`/`policy_value`를 요구한다 - `review.json`이
    # "무슨 정책으로 돌렸는데 비었나"를 말해야 하기 때문이다. 순서를 되돌리면
    # 전량 실패 경로가 두 값이 정해지지 않은 채 객체를 만들게 된다.
    if budget_ratio is not None:
        policy_kind, policy_value = "budget", budget_ratio
    elif threshold is not None:
        policy_kind, policy_value = "threshold", threshold
    else:
        # 호출자가 트리아지를 요청하지 않았는데 여기 도달한 것이다.
        # 조용히 빈 결과를 내면 "트리아지가 돌았고 아무것도 안 걸렸다"로
        # 읽혀 미배선을 정상으로 오인한다.
        raise ValueError("budget_ratio와 threshold가 둘 다 None이다")

    def _outcome(
        risks: tuple[SegmentRisk, ...],
        segments: tuple[Segment, ...],
        *,
        tier1_usage: TokenUsage | None = None,
    ) -> TriageOutcome:
        """세 반환 지점이 같은 필드 조합을 쓰게 묶는다.

        전량 실패 경로와 정상 경로가 각자 생성자를 부르면 필드 하나가 한쪽에서만
        채워져도 타입 검사와 화면 테스트를 모두 통과한다 - `usage`가 정확히 그런
        필드다(화면은 읽지 않고 `review.json`만 읽는다).

        **`tier1_usage`는 `--tier1` 여부가 아니라 "실제로 돌았나"다.** 스위치가
        켜져 있어도 Tier 1이 한 번도 안 도는 경로가 **둘** 있다.

        | 경로 | 어디서 갈라지나 |
        | --- | --- |
        | 번역 전량 실패 | 아래 조기 반환이 `triage_with_tier1`보다 먼저 나간다 |
        | **후보 0건** | `triage_with_tier1`의 `if not candidates:`가 `warn`을 부르고 반환한다 |

        둘 다 `tier1.counting.usage`를 읽으면 `TokenUsage(0, 0, 0)`이 나가
        `includes`가 "Tier 1을 셌다"고 거짓말한다(`calls == 0`이라 `unreported`
        에는 안 실려 수치는 안 부푼다 - **화면 어디에도 신호가 없는 종류의
        거짓말이다**). 기본값을 `None`으로 둔 것이 첫 경로를, 호출부의
        `안_돈_사유`가 둘째 경로를 옳게 만든다. **둘째는 최종 리뷰 축B가
        실주행으로 찾았다** - 첫째만 막았을 때 이 자리가 여전히 거짓말했다.
        """
        # **`None`과 `TokenUsage(0, 0, calls=N)`은 다르다.** 전자는 "그 계층이
        # 안 돌았다"(범위·합계에서 제외), 후자는 "돌았는데 무음"(`unreported`에
        # 실린다). Tier 1이 안 돌았으면 `None`이다.
        scope = resolve_cost_scope({"translation": translated.usage, "tier1": tier1_usage})
        return TriageOutcome(
            source_lang=source_lang,
            target_lang=target_lang,
            profile_name=profile.name,
            policy_label=policy_label,
            policy_kind=policy_kind,
            policy_value=policy_value,
            risks=risks,
            segments=segments,
            excluded_failures=len(failed_ids),
            # **범위·판정·합계를 한 곳에서 받는다.** 셋을 손으로 적으면 계층을
            # 늘린 쪽이 판정을 빠뜨리고, 그 계층의 무음 열화는 `tokens_reported`의
            # `True`에 가려진다(실측: 합쳐 넘긴 `{"translation": tr + t1}`은
            # `includes=("translation",)`·`unreported=()`를 내 Tier 1을 통째로
            # 지운다). Tier 1을 켜는 배선은 이 매핑에 `"tier1"` 한 줄을 더한다 -
            # 다른 곳은 건드릴 필요가 없다.
            usage=scope.usage,
            cost_includes=scope.includes,
            cost_unreported=scope.unreported,
        )

    if not kept:
        # 전량 실패에서 `review_ratio`는 0.0을 내지만(빈 목록 가드,
        # `policy.py:194-195`) "검수 대상 0개"는 "볼 것이 없다"로 읽힌다.
        # 실제로는 **판정 자체를 못 한 것**이라 화면은 그것을 구별해 말해야
        # 하는데, 그 문구는 이제 호출자가 만든다 - 여기서는 `risks=()`로
        # 사실만 남긴다.
        return _outcome((), ())

    ctx = SignalContext(
        profile=profile,
        glossary=glossary,
        source_lang=source_lang,
        target_lang=target_lang,
    )

    if tier1 is not None:
        # Tier 1 경로. **`policy_kind`가 "budget"인 것은 CLI가 이미 보장했다**
        # (설계 D9 - threshold 조합은 exit 2로 거부된다). 여기서 다시 분기하면
        # 도달 불가한 가지가 생겨 어떤 테스트도 밟지 못한다.
        #
        # **`excluded_ids`로 실패분을 넘긴다**(설계 D5). 넘기지 않으면 융합에
        # 실패분이 들어가 hard fail이 예산 quota를 먹는다 - 실측 Recall@10% 0%.
        # 게다가 반환 목록이 `kept`보다 길어져 `TriageOutcome`의 id 집합
        # 불변식이 `ValueError`를 던지고 트리아지가 통째로 exit 2가 된다.
        # **후보 0건이면 Tier 1은 한 번도 안 돈다 - 그 사실을 `warn`이 말한다**
        # (최종 리뷰 축B). `triage_with_tier1`은 `if not candidates:` 한 자리에서만
        # `warn`을 부르고 **즉시 반환한다.** 그 경로에서 `tier1.counting.usage`를
        # 그대로 넘기면 `TokenUsage(0, 0, 0)`이 `resolve_cost_scope`의
        # `usage is not None`을 통과해 **안 돈 계층이 비용 범위에 실린다** -
        # 화면은 `Tier 1: 회색지대가 비었다`인데 파일은 `["translation", "tier1"]`
        # 이라 말하는 상태다(실측: 5큐 실주행에서 `calls: 6`이 전부 번역이었다).
        #
        # **호출 수로 판정하면 안 된다.** `CountingProvider`는 캐시 **안쪽**이라
        # 재실행에서 샘플이 전부 캐시 히트면 `calls == 0`인데 그때 Tier 1은
        # **돌았다.** `calls > 0`을 기준으로 삼으면 같은 입력의 1회차와 2회차가
        # 서로 다른 `includes`를 내 NFR-3 재현성이 깨진다.
        안_돈_사유: list[str] = []

        def _tier1_warn(message: str) -> None:
            """후보 0건의 사유를 화면에 내고, 안 돌았다는 사실을 기록한다.

            **`warn`을 침묵시키지 않는다**(Ruling P12). 여기서 조용하면 유료
            계층이 통째로 안 돌아도 반환값의 형태가 완전히 같아 알아챌 수단이
            없다. 후보 0건의 사유 6종이 화면에 나가야 한다.

            **`err=True`를 주지 않는다.** 이것은 실패가 아니라 "무엇이
            일어났는가"의 보고이고, 같은 함수의 트리아지 요약과 나란히 읽혀야
            한다 - 한쪽만 stderr로 가면 리다이렉트한 로그에서 순서가 섞인다.

            **`warn`이 다른 사유로도 불리게 되는 날 이 기록은 거짓이 된다.**
            그때는 `triage_with_tier1`이 "돌았나"를 직접 돌려주게 바꿔야 한다 -
            `test_후보가_0건이면_cost_includes에_tier1이_안_실린다`와
            `test_tier1을_켜면_cost_includes에_tier1이_실린다`가 양방향으로 건다.
            """
            안_돈_사유.append(message)
            _echo(f"[{target_lang}] {_TIER1_WARN_PREFIX}{message}")

        # ② Tier 1 (FR-8.5 · 설계 D1). **`tier1`이 None이면 단계 자체가
        # 없다** - 이 블록 밖에서는 LLM 호출이 없어 진척을 잴 대상이 없다.
        reporter.phase(f"[{target_lang}] Tier 1")
        scored = triage_with_tier1(
            translated.segments,
            ctx,
            budget_ratio=policy_value,
            provider=tier1.counting,
            max_ratio=tier1.max_ratio,
            warn=_tier1_warn,
            samples=tier1.samples,
            temperature=tier1.temperature,
            cache_dir=tier1.cache_dir,
            identity=tier1.identity,
            excluded_ids=failed_ids,
            weights=weights,
            on_progress=reporter.update,
        )
        reporter.done()
        return _outcome(
            tuple(scored),
            tuple(kept),
            tier1_usage=None if 안_돈_사유 else tier1.counting.usage,
        )

    # **수집과 융합의 입력이 다르다. 서로 다른 층의 요구이기 때문이다.**
    #
    # 수집(`collect_all`)은 **트랙 전체**를 본다 - 배치 신호가 이웃을 봐야
    # 판정되기 때문이다. 융합(`fuse`)은 **`kept`만** 본다 - 실패분이 hard
    # fail로 quota를 먹으면 안 되기 때문이다(D12).
    #
    # 둘을 `kept` 하나로 묶으면 **번역 실패한 큐와 겹치는 성공한 큐의 겹침이
    # 사라진다.** 그 겹침은 산출 파일에 그대로 남아 출고되는데 요약은 침묵하고
    # exit도 정상이다 - 종료 코드로는 알 수 없는 조용한 실패다(실측: 같은 2큐
    # 파일에서 실패 1건이면 `spec.overlap` 미출력, 실패 0건이면 1개 출력).
    #
    # D12는 그대로 유지된다 - 실패분의 신호는 수집되기만 하고 `fuse`에
    # 도달하지 않아 위험도가 되지 않는다. `length.ratio`는 빈 번역을 분포에서
    # 이미 제외하므로(`signals/derived.py:145-148`) 분포도 흔들리지 않는다.
    signals = collect_all(translated.segments, ctx)
    # `collect_all`은 신호가 없는 세그먼트도 빈 리스트로 키를 갖는다
    # (`signals/base.py`) - KeyError 없이 전량을 돌 수 있다는 보장이다.
    risks = [fuse(seg.id, signals[seg.id], weights) for seg in kept]

    # **위에서 정한 `policy_kind`로 분기한다 - `budget_ratio`를 다시 보지
    # 않는다.** 두 번 판정하면 `review.json`이 "budget으로 돌렸다"고 적어 둔
    # 채 실제로는 임계값 선별이 도는 조합이 생길 수 있고, 그때 파일은 문법상
    # 정상이라 어떤 게이트에도 걸리지 않는다.
    if policy_kind == "budget":
        scored = select_by_budget(risks, policy_value)
    else:
        scored = select_by_threshold(risks, policy_value)

    # `scored`는 위험도 내림차순이고 `kept`는 트랙 원본 순서다. 둘의 순서가
    # 다른 것이 정상이라 `TriageOutcome`은 id 집합으로만 검증한다
    # (`report/models.py`) - 순서까지 고정하면 이 정상 입력이 거부돼 트리아지
    # 경로가 통째로 죽는다.
    return _outcome(tuple(scored), tuple(kept))


def _parse_review_budget(raw: str) -> float:
    """`--review-budget` 값을 비율로 바꾼다 (FR-6.3 ① · 설계 §5.2).

    `10%`와 `0.1`을 모두 받는다. **개수 지정(`50`)은 범위 밖으로 거부된다** -
    라이브러리에 개수 기반 선별 함수가 없고, `k/n`으로 환산하면 `ceil`과 hard
    fail 소진 때문에 정확히 K개가 나오지 않아 옵션이 거짓말을 한다(설계 D5).

    **`1`은 100%다.** `%` 유무만 다르고 나머지는 `0.0 <= x <= 1.0` 한 규칙이라
    그 결과다. 규칙을 좁혀(`%` 없는 값에 소수점을 요구해) `1`을 거부하면 `0`도
    함께 막혀 "hard fail만 보기"가 사라진다.

    **NaN·inf는 범위 검사가 거부한다** - `nan <= 1.0`이 False이기 때문이다.
    이것이 우연이 아니라 의도임을 `test_NaN과_inf는_범위_검사가_거부한다`가
    **오류 메시지로** 못 박고 있다 - 예외 타입만 단언하면 앞에 `math.isnan`
    분기를 끼워 넣어 "숫자로 읽지 못했다"로 바꿔도 통과해, 이 문단이 약속한
    경로가 사라진 것을 게이트가 못 잡는다. 이 방어가 없으면
    `select_by_budget`이 `math.isnan`으로 다시 막아 주지만, 그때는 오류
    메시지가 옵션 이름을 말하지 못한다.
    """
    text = raw.strip()
    percent = text.endswith("%")
    number = text[:-1].strip() if percent else text
    try:
        value = float(number) / 100.0 if percent else float(number)
    except ValueError as exc:
        raise ValueError(f"--review-budget을 숫자로 읽지 못했다: {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"--review-budget이 0~100% 범위를 벗어났다: {raw!r}. "
            "개수 지정은 v0.1 범위 밖이다 - 비율로 지정하라 (예: 10%)"
        )
    return value


def _resolve_profile(spec: str) -> tuple[SpecProfile, str]:
    """`--spec` 값을 프로파일과 표시용 label로 바꾼다 (FR-5.3·설계 §8·§7.2).

    **존재 여부가 아니라 확장자로 가른다.** 존재 여부로 가르면 오타 난 파일
    경로가 "내장 이름이 없다"는 틀린 진단을 받는다 — 인제스트가 mp4를
    `decode` 오류로 보고하지 않으려고 확장자를 먼저 본 것과 같은 판단이다.
    **라우팅만 소문자로 본다.** Windows는 파일명 대소문자를 구분하지 않아
    `my-spec.YAML`이 정상인 파일명이고 이 프로젝트의 개발 플랫폼이 Windows인데,
    `load_profile`에는 원본을 넘겨야 한다 — CI의 Linux는 구분한다.

    **label을 여기서 만드는 이유**는 설계 §7.2의 헤더가 "엉뚱한 프로파일로
    통과한 것을 알 수 없다"를 막기 때문이다. `name: ko`인 사용자 파일은 규격
    이름만으로는 내장 `ko`와 구별되지 않아 하필 FR-5.3 경로에서 헤더가 죽는다.
    출처를 label에 실어 구별하되, **확장자 판정을 이 함수 밖으로 복제하지
    않으려고** 호출자가 아니라 여기서 만든다.

    예외는 열거하지 않는다. 열거는 계약이 아니라 관찰이라 로더가 새 예외를 낼
    때마다 뒤처지고, 뒤처진 쪽으로 샌 예외는 종료 코드 1("규격 위반 발견")이 된다.
    실제로 이 튜플이 두 번 넓어지고도 세 번째 누락이 남아 있었다.

    **대신 `load_profile`이 내용 오류를 전부 `ValueError`로 정규화한다는 계약에
    기댄다.** 이 두 줄이 짧은 것은 그 계약 덕분이지 안전해서가 아니다 —
    `spec/profile.py`의 정규화가 느슨해지면 여기가 조용히 무방비가 된다.
    """
    try:
        if spec.lower().endswith((".yaml", ".yml")):
            profile = load_profile(Path(spec))
            return profile, f"{profile.name} ({spec})"
        profile = load_builtin(spec)
        return profile, profile.name
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--spec") from exc


# duration_short가 14자로 가장 길다. 한 칸을 더 둬야 수치와 붙지 않는다.
#
# **이 폭이 정렬을 지탱하는 것은 kind 7종이 전부 ASCII이기 때문이다.** `f"{kind:<15}"`는
# **글자 수**로 패딩하는데 터미널은 **표시 폭**으로 그린다. 한글 kind(예: `줄길이초과`)를
# 추가하면 5글자가 10칸을 차지해 그 줄만 5칸 밀리고, **부분 문자열 단언은 밀린 줄도
# 통과시키므로 어느 테스트도 울리지 않는다.** 새 kind는 ASCII로 짓거나, 그럴 수 없다면
# `spec/counting.py`의 `text_width`로 폭을 재서 패딩해야 한다.
_KIND_WIDTH = 15


def _format_timecode(ms: int) -> str:
    """`00:01:23.400`으로 고정한다 (설계 §7.3).

    SRT는 쉼표(`,400`), VTT는 마침표(`.400`)를 쓰므로 입력 포맷을 따라가면
    같은 도구의 출력이 파일마다 달라진다. 1차 좌표는 큐 번호이고 타임코드는
    보조이므로 표기를 하나로 고정하는 편이 낫다.

    **음수는 부호를 살린다.** 이전 판의 `max(ms, 0)`은 `-3000`을 `00:00:00.000`으로
    만들었고, 그것을 남긴 근거는 "음수는 `Segment.__post_init__`과 인제스트 경계가 이미
    막는다"였다 — **그 전제가 거짓이었다.** 둘 다 역전(`end < start`)만 봤고 부호는
    아무도 안 봤다. 그 결과 `(-5000, -1000)`짜리 트랙이 **exit 0 · "위반 없음"으로
    통과했다**(실측).

    지금은 `_require_non_negative_timecodes`가 인제스트 경계에서 66으로 막으므로 이
    함수에 음수가 도달하는 것 자체가 상류의 결함이다. 그래도 클램프를 되살리지 않는 것은
    클램프가 **적극적으로 거짓을 만들기** 때문이다 — 검수자는 `00:00:00.000`을 믿고
    찾아가고 거기엔 아무것도 없다. `loader.py`가 못 박은 "조용히 틀린 답은 크래시보다
    나쁘다"가 여기에도 적용된다.

    `abs`로 자릿수를 만들고 부호를 따로 붙이는 이유는 `divmod`에 음수를 그대로 흘리면
    파이썬의 바닥 나눗셈이 `divmod(-3000, 1000) == (-3, 0)`을 내어 `-1:59:57.000`이
    되기 때문이다. 부호만 앞에 붙이면 나머지 자릿수는 양수와 같은 규칙으로 읽힌다.
    """
    sign = "-" if ms < 0 else ""
    seconds, milliseconds = divmod(abs(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _format_ratio(percent: float) -> str:
    """위반 큐 비율을 적는다. **0이 아닌데 `0.0%`로 보이면 안 된다.**

    `f"{x:.1f}%"`는 2001큐 중 1개(0.049%)를 `0.0%`로 떨어뜨린다. 이 저장소는 "0으로 보이는
    수치"를 1급 결함으로 취급한다 — 검수자가 위반 목록을 눈앞에 두고 요약만 보면
    "0%니까 통과"로 읽는다. 자릿수를 늘리는 대신 `<0.1%`로 적어 **0이 아님을 말한다.**

    반올림이 아니라 절단으로 판정하는 이유는 0.04%와 0.06%가 모두 0이 아니기 때문이다.
    """
    if percent > 0 and percent < 0.05:
        return "<0.1%"
    return f"{percent:.1f}%"


def _format_detail(violation: SpecViolation) -> str:
    """위반 한 건의 수치 부분을 만든다.

    `line_index`는 **0-based**다. 사람이 읽는 좌표는 1부터 세므로 `+1`한다 —
    빼먹어도 테스트 없이는 드러나지 않는 종류의 오차다.
    """
    kind = violation.kind
    if kind == "empty_cue":
        return "텍스트 없음"
    if kind == "overlap":
        return f"{violation.measured:.0f}ms"
    if kind in ("duration_short", "duration_long"):
        sign = "<" if kind == "duration_short" else ">"
        return f"{violation.measured:.0f}ms {sign} {violation.limit:.0f}ms"

    detail = f"{violation.measured} > {violation.limit}"
    if violation.line_index is not None:
        detail = f"{detail}  ({violation.line_index + 1}번째 줄)"
    return detail


def _format_report(
    *,
    source_name: str,
    fmt: str,
    profile_label: str,
    cue_total: int,
    violations: Sequence[TrackViolation],
    event_index: Mapping[str, int],
    limit: int = 0,
) -> list[str]:
    """콘솔 산출물 전체를 만든다 (설계 §7).

    **순수 함수인 것이 요점이다.** `CliRunner` 없이 문자열 입출력으로 직접
    시험할 수 있어야 정렬·자릿수·큐 번호 부여 같은 포맷 결함이 CLI 통합
    테스트에 묻히지 않는다(설계 §7.4).

    `profile_label`은 규격 이름이 아니라 **표시용 label**이다. `_resolve_profile`이
    내장은 `ko`, 사용자 파일은 `ko (./our-spec.yaml)`로 만든다 — 이름만 실으면
    `name: ko`인 사용자 파일이 내장 `ko`와 헤더까지 같아져 구별되지 않는다.

    위반이 없을 때도 검사 대상 개수와 프로파일 이름을 낸다 — 그것이 없으면
    사용자는 엉뚱한 파일이나 엉뚱한 프로파일로 통과한 것을 알 수 없다.

    **요약을 머리와 끝 양쪽에 낸다.** 이전 판은 맨 아래 한 줄이었는데, 위반 682건이면
    686줄이 나가고 26화 × 3언어 매트릭스에서 프로파일을 잘못 물리면 약 5만 줄이 쌓인다.
    로그를 앞에서 남기고 뒤를 자르는 CI에서는 **가장 중요한 한 줄이 가장 먼저** 사라진다.
    양쪽에 두면 절단 방향과 무관하게 살아남고, 중복 2줄은 1204줄의 0.17%다.

    **`limit`은 여기서 적용해야 한다.** 호출부에서 `lines[:N]`으로 자르면 요약 줄까지
    함께 잘려 위 목적이 정확히 무너진다 — 무엇을 자르고 무엇을 남길지는 산출물의
    구조를 아는 이 함수만 판단할 수 있다. `0`은 무제한이고 그것이 기본값인 이유는
    상한을 기본으로 켜면 전체 목록을 파이프로 받던 쓰임이 조용히 잘리기 때문이다.
    """
    # `검사 큐`인 이유: `cue_total`은 필터 **후** 개수라 아래의 `#N`(원본 큐 번호)이
    # 이 수보다 클 수 있다 — `큐 2개` 아래 `#4`가 찍히면 자기모순처럼 읽힌다.
    # 분모를 원본 이벤트 수로 되돌리면 안 된다: 검사 대상이 아닌 주석·드로잉까지 세어
    # 위반 비율이 과소평가되고, 그것은 Recall@Budget 지표를 건드린다.
    head = f"{source_name} ({fmt} · 검사 큐 {cue_total}개 · 프로파일 {profile_label})"
    if not violations:
        # em dash(U+2014)를 쓰지 않는다. cp949 로케일에서 stdout을 리다이렉트하면
        # UnicodeEncodeError로 exit 1이 나고, 이 저장소에서 exit 1은 "규격 위반 발견"이다.
        # 깨끗한 파일이 CI에서 위반으로 읽힌다.
        return [f"{head} - 위반 없음"]

    # **요약을 목록보다 먼저 계산한다.** 절단은 목록만 자르고 요약은 언제나 전체
    # 기준이어야 한다 — 자른 뒤에 세면 `--limit 3`이 "위반 3건"이라는 거짓말을 내고,
    # 그것은 CI 로그를 읽는 사람에게 종료 코드와 모순되는 수치를 준다.
    flagged = len({tv.segment_id for tv in violations})
    ratio = flagged / cue_total * 100 if cue_total else 0.0
    summary = f"위반 {len(violations)}건 · 위반 큐 {flagged}/{cue_total}개 ({_format_ratio(ratio)})"

    lines = [head, summary, ""]
    # 큐 번호 폭을 `cue_total`이 아니라 `event_index`에서 구한다. `cue_total`은
    # 필터 **후** 개수라 주석이 있는 파일에서는 원본 큐 번호의 최대값보다 작고,
    # 폭이 모자라면 자릿수가 큰 줄부터 뒤 열이 통째로 오른쪽으로 밀린다.
    cue_width = len(str(max(event_index.values(), default=0) + 1))
    # **`limit <= 0`이지 `limit == 0`이 아니다.** 음수를 그대로 흘리면 `violations[:-1]`이
    # 되어 **마지막 위반이 조용히 사라진다.** CLI 경로는 typer의 `min=0`이 본문 전에 막으므로
    # (실측: `--limit -1`·`-1` 등호형·`--limit 2 --limit -1` 모두 exit 2) 이 방어가 실제로
    # 필요한 호출자는 **이 함수를 직접 부르는 테스트**다 — 프로덕션 호출자는 `check()` 하나뿐이다.
    shown = violations if limit <= 0 else violations[:limit]
    for track_violation in shown:
        # **원본 파일의 "이벤트 순번"이지 SRT에 인쇄된 번호가 아니다.**
        # `segment.index + 1`이 아닌 것은 맞다 — 필터가 인덱스를 재부여하므로 주석이 있는
        # 파일에서 둘이 갈라진다(설계 §4.1). 다만 거기까지다: pysubs2가 SRT의 인쇄 번호를
        # 버리므로 번호가 `1,2,4,5`인 파일(3번이 지워진 파일)에서는 파일의 `4`를 `#3`으로
        # 부른다. 진짜 대응은 인쇄 번호를 보존해야 하고 v0.1 범위 밖이다.
        cue = event_index[track_violation.segment_id] + 1
        stamp = _format_timecode(track_violation.start_ms)
        kind = f"{track_violation.violation.kind:<{_KIND_WIDTH}}"
        lines.append(
            f"  #{cue:<{cue_width}}  {stamp}  {kind}{_format_detail(track_violation.violation)}"
        )

    # 잘렸다는 사실을 숨기지 않는다. 고지가 없으면 사용자는 목록이 전부라고 읽고,
    # 그것은 이 저장소가 1급 결함으로 취급하는 "조용한 손실"이다. 상한이 위반 수보다
    # 클 때 고지를 내지 않는 것도 같은 이유다 — `0건 생략`은 그 자체로 거짓말이다.
    omitted = len(violations) - len(shown)
    if omitted:
        lines.append(f"  ... {omitted}건 생략 (전체는 --limit 0)")

    lines.append("")
    lines.append(summary)
    return lines


# **아래 독스트링은 `cuesift check --help`의 첫 화면에 그대로 뜬다** — typer가 커맨드
# 독스트링 **전체**를 help로 만든다. 그래서 설계 근거는 독스트링이 아니라 여기 둔다.
# 사용자에게 `collect_all`·`fuse`·`triage`·`설계 D3`를 보여 줄 이유가 없다.
#
# **`check`는 신호 엔진을 통과하지 않는다**(설계 D3). `collect_all`→`fuse`→`triage`가
# 얹는 넷(점수화·hard_fail·융합·트리아지)을 이 명령이 하나도 쓰지 않기 때문이다.
# 심각도가 단일 등급이고 예산도 순위도 없다. 규격 판정의 원천은 `spec/check.py` 하나이고
# translate 경로와 여기가 양쪽 다 그것을 쓴다.
@app.command()
def check(
    input: Annotated[
        Path,
        # `readable=False`는 typer의 기본 `readable=True`를 끈다. 켜져 있으면 typer가
        # 본문에 닿기 전에 `os.access(path, os.R_OK)`를 보고(`typer/models.py`)
        # **읽을 수 없는 파일을 종료 코드 2로 낸다.** POSIX의 mode 000은 거기서 걸리고
        # Windows의 배타 잠금은 `os.access`를 통과해 66이 되므로, 켜 두면 **같은 사고가
        # 플랫폼마다 다른 코드**를 낸다. 위 표가 "읽을 수 없음 = 66"이라고 단언하므로
        # 판정을 인제스트 한 곳으로 모은다.
        typer.Argument(exists=True, dir_okay=False, readable=False, help="검사할 자막 파일"),
    ],
    spec: Annotated[
        str,
        typer.Option("--spec", help="규격 프로파일 이름(예: ko) 또는 .yaml 파일 경로"),
    ],
    fail_on: Annotated[
        FailOn,
        # help 문자열은 `--help`로 출력되므로 em dash를 쓰지 않는다(전역 제약).
        typer.Option(
            "--fail-on",
            help="hard와 any는 v0.1에서 같다. 위반 1건이면 종료 코드 1. none은 보고만 하고 항상 0",
        ),
    ] = FailOn.hard,
    limit: Annotated[
        int,
        # `min=0`은 typer가 **본문에 닿기 전에** 종료 코드 2로 거른다. 음수를 본문까지
        # 흘리면 `violations[:-1]`로 마지막 위반이 조용히 사라진다(설계상 `_format_report`도
        # 따로 막지만, 잘못된 명령줄은 명령줄 오류로 보고되는 편이 진단이 정확하다).
        # help 문자열은 `--help`로 나가므로 em dash를 쓰지 않는다(전역 제약).
        typer.Option("--limit", min=0, help="위반 목록을 N건까지만 출력한다. 0은 무제한(기본)"),
    ] = 0,
) -> None:
    """FR-8.2: 자막 규격 검사만 수행합니다 (CI 게이트)."""
    # `_resolve_profile`은 프로파일과 **표시용 라벨**을 함께 낸다. 라벨이 따로 필요한 것은
    # `profile.name`이 YAML의 `name` 필드라서, `--spec ./our-spec.yaml`인데 그 파일이
    # `name: ko`면 헤더가 내장 `ko`로 검사한 것과 **바이트 단위로 같아지기** 때문이다.
    # 설계 §7.2가 헤더를 둔 이유("엉뚱한 프로파일로 통과한 것을 알 수 없다")가 FR-5.3
    # 경로에서 정확히 무효화된다.
    profile, profile_label = _resolve_profile(spec)

    try:
        result = load_subtitle(input)
    except IngestError as exc:
        # 진단 실패는 산출물이 아니라 실행 실패 보고다. stderr로 낸다(설계 §7.1).
        # `IngestError` 하나만 잡으면 되는 것은 `loader.py`가 자기 실패를 전부 이
        # 타입으로 모으기 때문이다 — `OSError`까지 포함한다. 여기서 예외를 열거하기
        # 시작하면 로더가 새 실패를 낼 때마다 뒤처지고, 샌 예외는 미처리 traceback으로
        # 종료 코드 1이 되어 "규격 위반 발견"으로 오보된다.
        _echo(str(exc), err=True)
        raise typer.Exit(EXIT_BAD_INPUT) from exc

    violations = check_track(result.segments, profile)

    # 위반 목록은 이 명령의 정상 산출물이므로 stdout이다(설계 D9).
    # 사용자 경로가 이 줄에 실리지만 인코딩 사고는 `_harden_output_streams`가 막는다.
    for line in _format_report(
        # 이름이 아니라 **경로 전체**를 넘긴다. `input.name`만 넘기면 디렉터리를 순회하며
        # 로그를 합치는 스크립트에서 `ko/ep01.srt`와 `ja/ep01.srt`가 같은 줄로 보이고,
        # 헤더의 목적("엉뚱한 파일로 통과한 것을 알 수 있게")이 정확히 무너진다.
        # `IngestError` 메시지도 전체 경로를 쓰므로 표기가 일관된다.
        source_name=str(input),
        fmt=result.format,
        profile_label=profile_label,
        cue_total=len(result.segments),
        violations=violations,
        event_index=result.event_index,
        limit=limit,
    ):
        _echo(line)

    # **종료 코드는 `limit`을 보지 않는다.** 판정의 결과이지 출력의 결과가 아니다 —
    # 3건만 보여준다고 위반이 3건인 것이 아니고, 여기가 흔들리면 CI 게이트가
    # 출력 옵션에 좌우된다(`test_limit_does_not_change_the_exit_code`가 고정한다).
    if violations and fail_on is not FailOn.none:
        raise typer.Exit(1)


@app.command()
def transcribe(
    input: Annotated[Path, typer.Argument(help="영상 또는 오디오 파일")],
    source_lang: Annotated[str | None, typer.Option("--source-lang", help="원문 언어")] = None,
) -> None:
    """FR-8.3: STT로 원문 자막만 생성합니다."""
    _not_implemented("transcribe")


def run() -> None:
    """콘솔 스크립트 진입점 (`pyproject.toml`의 `[project.scripts]`).

    **`app`을 직접 진입점으로 두면 `cuesift --help | less`가 종료 코드 120을 낸다.**
    `--help`·`--version`·사용법 오류(2)·미구현(70)의 출력은 커맨드 본문 밖에서 일어나
    `_echo`가 닿지 않는다.

    **종료 코드를 여기서 바꾸지 않는 것이 계약이다.** 이전 판은 닫힌 파이프를 잡아
    `SystemExit(0)`으로 바꿨는데, 그것이 **exit 2와 exit 70을 조용한 0으로 만들었다**
    (실측). 지금은 출력 지점을 무해하게 만들어 각 커맨드가 고른 코드가 그대로 나가게 한다.

    | 층 | 무엇 | 지키는 것 |
    | --- | --- | --- |
    | 1 | `_TolerantOutput` (여기서 설치) | 어느 코드 경로가 쓰든 쓰기가 실패하지 않는다 |
    | 2 | `_echo`·`_not_implemented` (커맨드 본문) | `app()`을 직접 부르는 호출자용 **부분** 방어 |
    | 3 | 아래 `finally` | 종료 flush가 120을 만들지 못하게 한다 |

    **2층은 0·1·66(`_echo`)과 70(`_not_implemented`)만 덮는다.** 종료 코드 2는 click의
    `UsageError.show()`가 쓰므로 본문에 방어할 지점이 없다 — **1층 없이는 못 막는다.**
    `run()`을 거치는 배포 경로는 1층이 전부 덮으므로 실사용 위험은 없고,
    `app()`을 직접 부르는 테스트·라이브러리 호출자에게만 해당한다.

    `ENOSPC`는 **위 세 층 중 어느 곳도** 삼키지 않는다 — 잘린 출력이 성공으로
    보고되면 안 된다. 예외가 하나 있다: `progress.ProgressReporter._raw`는 자기
    쓰기의 `ENOSPC`를 삼키고 리포터를 영구 비활성화한다(FR-8.5 · 설계 D10).
    **종료 코드에는 영향이 없다** — 진행 표시는 부수적이고, 같은 디스크 상태를
    본문의 `_echo`가 곧 다시 만나 그쪽에서 올린다. 근거는 `progress.py`의
    `_raw` 주석에 있다. **"어느 층도"를 문자 그대로 읽으면 안 되는 이유가
    이것이고, 그 사실을 여기 적지 않으면 종료 코드 계약을 확인하러 오는
    독자가 틀린 결론에 도달한다.**
    """
    sys.stdout = _TolerantOutput(sys.stdout)  # type: ignore[assignment]
    sys.stderr = _TolerantOutput(sys.stderr)  # type: ignore[assignment]
    try:
        app()
    finally:
        # 버퍼에 남은 것을 여기서 흘려보낸다. 프록시가 닫힌 파이프를 이미 삼키므로
        # 여기서 터지는 것은 진짜 I/O 오류뿐이고, 그때는 **올라가야 한다.**
        sys.stdout.flush()
        sys.stderr.flush()


if __name__ == "__main__":  # pragma: no cover
    run()
