# 번역 실패 진단 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 번역이 실패했을 때 CI(종료 코드)와 사람(stderr) 둘 다에게 **무엇이 왜 실패했는지**를 말하게 한다.

**Architecture:** 새 모듈도 새 개념도 없다. 이미 생산되고 버려지던 `SegmentFailure.reason`·`attempts`에 소비자를 붙이고(B), `translate`가 `check`와 공유하던 종료 코드 1을 전용 코드로 가른다(A). dry-run은 실행 경로의 진단 문구를 **복제하지 않고 공유**한다(C).

**Tech Stack:** Python 3.11+ · typer · pytest. 의존성 추가 없음.

**근거:** [HANDOFF.md](../../../HANDOFF.md) 파킹 #1·#2·#3 · [요구사항정의서](../../요구사항정의서.md) FR-2.6 · `src/cuesift/cli.py` 모듈 독스트링(종료 코드 계약의 단일 출처)

## Global Constraints

- 모든 모듈 첫 줄에 `from __future__ import annotations`
- 독스트링·주석은 **한국어**, 근거 FR·§ 번호 병기
- 주석은 "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"**
- ruff `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`
- **사용자에게 나가는 문자열에 em dash(U+2014)를 쓰지 않는다** — cp949가 인코딩하지 못한다(실측). `·`(U+00B7)는 쓴다
- 게이트는 CI와 같은 대상 `.`으로 돌린다. `src tests`로 좁히지 않는다
- 의존성 추가 금지 — 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개
- Python 실행은 `.venv/Scripts/python.exe`

## 착수 기준선 — 못 박는다

| 항목 | 착수 시점 값 |
| --- | --- |
| `pytest --cov=cuesift` | **1547 passed · 3 deselected** · 커버리지 **99%**(2447문 중 31 미도달) |
| `ruff check .` / `format --check .` | 통과 · **112 files** |
| `scripts/check_links.py` | 마크다운 **37개** · 상대 링크 **188개** · 깨진 링크 0 |
| `npx markdownlint-cli2` | **37 files** · 0 issues |
| CLI 옵션 수 | **24개** |
| CI 기대값 | **1546 passed · 1 skipped · 3 deselected** |

**완료 시 `pytest` 수치가 이 값보다 작으면 게이트를 지운 것이다.**

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `src/cuesift/cli.py` | 종료 코드 상수·합산 규칙·머리말 표 · 실패 요약 렌더링 · dry-run 줄 | T1·T2·T3 |
| `src/cuesift/tier1.py` | 후보 0건 진단 문구의 **단일 출처** | T3 |
| `README.md` | 종료 코드 표(`cli.py` 독스트링의 파생물) | T1 |
| `tests/test_cli_exit_codes.py` (신규) | 종료 코드 계약과 합산 우선순위 | T1 |
| `tests/test_cli_translate.py` 외 4개 | 기존 `exit_code == 1` 단언 이관 | T1 |
| `tests/test_cli_failure_reasons.py` (신규) | 실패 사유 렌더링 | T2 |
| `tests/test_cli_dry_run.py` 또는 기존 dry-run 테스트 | dry-run 진단 | T3 |
| `CHANGELOG.md` · `HANDOFF.md` | 기록 | T4 |

---

## Task 1: 종료 코드를 가른다 — `EXIT_PARTIAL_FAILURE`

**Files:**

- Modify: `src/cuesift/cli.py` (머리말 표 12행 · 상수 블록 ~100 · `_combine_exit_codes` 신설 · `1458` · `2192`)
- Modify: `README.md` (§"종료 코드" 표)
- Create: `tests/test_cli_exit_codes.py`
- Modify: `tests/test_cli_translate.py:225,1133,1244` · `tests/test_cli_review_out.py:399,513,872` · `tests/test_cli_triage.py:509,529,612` · `tests/test_cli_config.py:77,170` · `tests/test_cli_tier1.py:889`

**Interfaces:**

- Produces: `EXIT_PARTIAL_FAILURE: int = 75` · `_combine_exit_codes(codes: Iterable[int]) -> int`
- Consumes: 기존 `EXIT_BAD_INPUT`(66) · `EXIT_UNAVAILABLE`(69) · `EXIT_NOT_IMPLEMENTED`(70)

### 왜 75인가 — 그리고 왜 `max`를 못 쓰는가

`sysexits.h`의 `EX_TEMPFAIL`(75)은 "실제로 오류는 아니고, 요청을 다시 시도해야 한다"는 뜻이다.
번역 실패 3종(`provider_error`·`invalid_response`·`empty_translation`)이 전부 재실행 여지가 있고,
**캐시가 성공분을 보존**하므로 재실행이 실제로 싸다. 65(`EX_DATAERR`, "입력이 틀렸다")는
입력 자막이 멀쩡하므로 거짓이고, 70(`EX_SOFTWARE`)은 이미 다른 뜻으로 쓰인다.

**합산이 문제다.** `cli.py:1458`의 `worst = max(worst, code)`는 **값이 클수록 심각하다**를 전제하는데
`sysexits.h` 값은 심각도 순서가 아니다. 지금은 우연히 맞는다(`69 > 66 > 2 > 1`).
75를 넣는 순간 **`75 > 69`라서 "일부 세그먼트 실패"가 "프로바이더가 인증을 거부했다"를 이긴다** —
en이 부분 실패하고 ja에서 401이 나면 CI가 75를 받고 진짜 원인 69가 사라진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_cli_exit_codes.py` 신규)

```python
"""종료 코드 계약 (`cli.py` 모듈 독스트링이 단일 출처).

**여기가 계약을 지키는 유일한 자리다.** 값 자체를 단언하는 테스트가 흩어져
있으면 하나를 고칠 때 나머지가 조용히 남는다.
"""

from __future__ import annotations

import pytest

from cuesift.cli import (
    EXIT_BAD_INPUT,
    EXIT_NOT_IMPLEMENTED,
    EXIT_PARTIAL_FAILURE,
    EXIT_UNAVAILABLE,
    _combine_exit_codes,
)


def test_전용_코드는_sysexits의_EX_TEMPFAIL이다() -> None:
    # 값이 바뀌면 CI 스크립트가 조용히 어긋난다. 리터럴로 못 박는다.
    assert EXIT_PARTIAL_FAILURE == 75


def test_네_코드가_서로_겹치지_않는다() -> None:
    codes = [EXIT_BAD_INPUT, EXIT_UNAVAILABLE, EXIT_NOT_IMPLEMENTED, EXIT_PARTIAL_FAILURE]
    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ((), 0),
        ((0, 0), 0),
        ((0, EXIT_PARTIAL_FAILURE), EXIT_PARTIAL_FAILURE),
        # **이것이 이 태스크의 핵심 단언이다.** `max`였다면 75가 이긴다.
        ((EXIT_PARTIAL_FAILURE, EXIT_UNAVAILABLE), EXIT_UNAVAILABLE),
        ((EXIT_PARTIAL_FAILURE, EXIT_NOT_IMPLEMENTED), EXIT_NOT_IMPLEMENTED),
        ((EXIT_PARTIAL_FAILURE, EXIT_BAD_INPUT), EXIT_BAD_INPUT),
        ((EXIT_BAD_INPUT, EXIT_UNAVAILABLE), EXIT_UNAVAILABLE),
        ((EXIT_NOT_IMPLEMENTED, EXIT_UNAVAILABLE), EXIT_UNAVAILABLE),
        ((EXIT_BAD_INPUT, EXIT_NOT_IMPLEMENTED), EXIT_NOT_IMPLEMENTED),
    ],
)
def test_더_근본적인_실패가_이긴다(codes: tuple[int, ...], expected: int) -> None:
    """순서가 값의 크기가 아니라는 것을 고정한다.

    `max`로 되돌리면 `(75, 69) -> 69` 한 줄이 죽는다 - 변이로 확인할 것.
    """
    assert _combine_exit_codes(codes) == expected


def test_순서가_인자_배치에_좌우되지_않는다() -> None:
    # 언어 순서가 `--to en,ja`냐 `ja,en`이냐로 CI 판정이 갈리면 안 된다.
    a = _combine_exit_codes((EXIT_PARTIAL_FAILURE, EXIT_UNAVAILABLE))
    b = _combine_exit_codes((EXIT_UNAVAILABLE, EXIT_PARTIAL_FAILURE))
    assert a == b == EXIT_UNAVAILABLE
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_exit_codes.py -v`
Expected: FAIL — `ImportError: cannot import name 'EXIT_PARTIAL_FAILURE'`

- [ ] **Step 3: 상수와 합산 규칙을 넣는다** (`src/cuesift/cli.py`)

`EXIT_UNAVAILABLE = 69` 블록 **다음**에 이어 붙인다.

```python
# sysexits.h EX_TEMPFAIL - "실제로 오류는 아니고, 요청을 다시 시도해야 한다".
# 일부 세그먼트만 번역에 실패했고 **산출물은 나갔다**는 뜻이다.
#
# **1에서 갈라져 나온 값이다.** 이전에는 `check`의 "규격 위반 발견"과 같은 1이라
# CI가 "자막이 규격을 어겼다"와 "번역이 실패했다"에 같은 대응을 했다. 요구가
# 그것을 시킨 적은 없다 - FR-7.5는 `check`만 말하고 FR-2.6은 "실패 표시 후
# 진행"만 요구하며 종료 코드를 언급하지 않는다.
#
# **69와 나누는 축은 "산출물이 나갔는가"다.** 69는 프로바이더가 요청을 거부해
# 그 언어가 통째로 죽은 것이고, 75는 파일이 나왔는데 일부 줄이 원문으로 남은
# 것이다. 전자는 설정을 고쳐야 하고 후자는 다시 돌리면 캐시가 성공분을 건너뛴다.
EXIT_PARTIAL_FAILURE = 75

# **값의 크기가 아니라 이 순서가 우선순위다.** `sysexits.h` 값은 심각도 순이
# 아니다 - `max()`로 합치면 `75 > 69`라서 "일부 세그먼트 실패"가 "프로바이더가
# 인증을 거부했다"를 이기고, en이 부분 실패한 뒤 ja에서 401이 났을 때 CI가
# 진짜 원인을 잃는다. 이 튜플이 없으면 그 회귀가 조용히 들어온다.
#
# 앞에 있을수록 근본적이다: 서비스가 죽은 것 > 우리 쪽 결함 > 파일 사정 >
# 일부 실패. `_translate_one`이 내는 값은 이 다섯뿐이다(2는 파싱 단계에서
# `typer.Exit`으로 먼저 나가므로 여기 오지 않는다).
_EXIT_PRIORITY = (
    EXIT_UNAVAILABLE,
    EXIT_NOT_IMPLEMENTED,
    EXIT_BAD_INPUT,
    EXIT_PARTIAL_FAILURE,
)


def _combine_exit_codes(codes: Iterable[int]) -> int:
    """대상 언어별 종료 코드를 하나로 합친다.

    **0은 "아무 일 없음"이라 언제나 진다.** 하나라도 실패가 있으면 그것이 나간다.
    `_EXIT_PRIORITY`에 없는 값이 오면 그대로 돌려준다 - 새 코드를 넣고 표에
    등록하지 않은 것을 조용히 0으로 만들지 않기 위해서다.
    """
    seen = {c for c in codes if c}
    if not seen:
        return 0
    for code in _EXIT_PRIORITY:
        if code in seen:
            return code
    return sorted(seen)[0]
```

`Iterable`을 `collections.abc` import에 더한다 (`from collections.abc import Iterable, Mapping, Sequence`).

- [ ] **Step 4: 호출부 두 곳을 바꾼다**

`cli.py:1401`·`1458`·`1485`:

```python
    codes: list[int] = []
    ...
            codes.append(code)
            if code == EXIT_UNAVAILABLE:
    ...
    worst = _combine_exit_codes(codes)
    if worst:
        raise typer.Exit(worst)
```

`cli.py:2192`:

```python
    return EXIT_PARTIAL_FAILURE if translated.failures else 0
```

- [ ] **Step 5: 테스트가 통과하는지 본다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_exit_codes.py -v`
Expected: PASS 12건

- [ ] **Step 6: 기존 12곳을 이관한다**

`exit_code == 1`을 `exit_code == EXIT_PARTIAL_FAILURE`로 바꾼다. **`check`의 7건은 건드리지 않는다.**

| 파일 | 줄 |
| --- | --- |
| `tests/test_cli_translate.py` | 225 · 1133 · 1244 |
| `tests/test_cli_review_out.py` | 399 · 513 · 872 |
| `tests/test_cli_triage.py` | 509 · 529 · 612 |
| `tests/test_cli_config.py` | 77 · 170 |
| `tests/test_cli_tier1.py` | 889 |

`test_cli_triage.py:509`·`612`의 주석 `# 번역 실패가 있으면 1이다 (FR-2.6)`도 함께 고친다 —
**FR-2.6은 종료 코드를 요구한 적이 없다.** 주석이 근거를 잘못 대고 있었다.

- [ ] **Step 7: 머리말 표를 고친다** (`src/cuesift/cli.py:7~20`)

```text
**종료 코드 일곱이 서로 겹치지 않는 것이 이 파일의 계약이다.**

| 코드 | 언제 | 근거 |
| --- | --- | --- |
| 0 | 위반 없음, 또는 `--fail-on none`, 또는 전량 번역 성공 | |
| 1 | 규격 위반 발견 (`check`만) | FR-7.5 |
| 2 | 명령줄이 틀림 (파일 없음·디렉터리·프로파일 해석 실패·출력 경로 충돌) | typer 관행 |
| 66 | 파일 사정 (자막·용어집 파싱 실패, utf-8 아님, 읽거나 쓰지 못함) | `sysexits.h` EX_NOINPUT |
| 69 | 외부 서비스(LLM 프로바이더)가 요청을 거부함 | `sysexits.h` EX_UNAVAILABLE |
| 70 | 미구현(`transcribe`), 또는 산출물의 **내용** 결함 | `sysexits.h` EX_SOFTWARE |
| 75 | 번역 일부 세그먼트 실패, 원문 유지 (`translate`만) | `sysexits.h` EX_TEMPFAIL |

**1을 진단 실패에 쓰지 않는 것이 핵심이다.** 1은 "규격 위반 발견"이므로
파일을 못 읽은 것을 1로 내면 CI가 "자막이 깨졌다"와 "경로가 틀렸다"에
같은 대응을 하게 되고, 사용자는 멀쩡한 자막을 고치려 든다.

**번역 실패도 1에서 뺐다.** 예전 표는 1을 `check`의 위반과 `translate`의
부분 실패가 겸하게 두고 근거로 FR-7.5·FR-2.6을 댔는데, **FR-7.5는 `check`만
말하고 FR-2.6은 종료 코드를 언급하지 않는다.** 요구가 아니라 구현 선택이었고,
겸하는 동안 CI는 두 원인에 같은 대응을 했다.
```

- [ ] **Step 8: README 표를 고친다** (`README.md` §"종료 코드")

`0`·`1` 행을 위 표와 같게 고치고 `75` 행을 `70` 다음에 넣는다.
마지막 문단의 `` `66`은 `sysexits.h`의 `EX_NOINPUT`... `` 줄에 `75`는 `EX_TEMPFAIL`을 더한다.

- [ ] **Step 9: 전체 게이트**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
```

Expected: **1559 passed · 3 deselected** (1547 + 신규 12), 커버리지 99% 이상

- [ ] **Step 10: 변이로 게이트를 증명한다**

`_combine_exit_codes`의 본문을 `return max(codes, default=0)`으로 바꾸고 돌린다.
Expected: `test_더_근본적인_실패가_이긴다[codes4-69]`가 **죽는다**. 죽지 않으면 게이트가 아니다.
확인 후 되돌린다.

- [ ] **Step 11: 커밋**

```bash
git add -A
git commit -F <메시지 파일>   # 여러 줄 한국어는 -F로. -m은 조용히 깨진다
```

---

## Task 2: 실패 사유를 화면에 낸다

**Files:**

- Modify: `src/cuesift/cli.py:2260-2269` (`lines` 조립부)
- Create: `tests/test_cli_failure_reasons.py`

**Interfaces:**

- Consumes: `SegmentFailure.reason`(`"provider_error"|"invalid_response"|"empty_translation"`) · `.attempts` · `.segment_id`
- Produces: 화면 문자열. 다른 태스크가 의존하지 않는다

### 지금 무엇이 버려지나

`engine.py:78`이 **"`reason`을 남기지 않으면 '실패 800건'에서 원인이 서버인지
모델인지 구분할 수 없다"**고 적어 두고 4곳에서 `reason`을 채운다. 그런데
`grep -rn "\.reason" src/`에 **소비자가 0건**이고 `cli.py:2267`은 ID만 낸다.
**만들어 놓고 경계에서 버리는 구조다.**

**그리고 그 ID 줄조차 테스트가 0건이다**(착수 조사 실측 — `grep -rn "실패 세그먼트\|원문 유지" tests/`가
무출력). `cli.py:2238`이 **"개수만 보고 넘기면 미번역 자막이 그대로 배포된다"**고 근거까지 적어 놓았는데
그 줄을 통째로 지워도 1547건이 전부 통과한다. 화면에 단언이 걸린 것은 개수 줄(`실패 2개`,
`test_cli_translate.py:1249`)뿐이다. **FR-8.5에서 배선 8곳이 게이트 없이 통과한 것과 같은 함정이고,
이 태스크는 사유를 더하는 동시에 그 구멍을 막는다.**

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_cli_failure_reasons.py` 신규)

```python
"""번역 실패 사유 렌더링 (파킹 #2 · FR-2.6).

`SegmentFailure.reason`은 4곳에서 생산되는데 소비자가 0건이었다 -
`engine.py:78`이 명시한 "서버인지 모델인지"를 화면이 말하지 않았다.
"""

from __future__ import annotations

from cuesift.cli import _format_failure_lines
from cuesift.translate import SegmentFailure


def _f(seg_id: str, reason: str, attempts: int) -> SegmentFailure:
    return SegmentFailure(segment_id=seg_id, reason=reason, attempts=attempts)


def test_실패가_없으면_줄을_내지_않는다() -> None:
    assert _format_failure_lines(()) == []


def test_사유별로_묶고_ID를_전부_나열한다() -> None:
    lines = _format_failure_lines(
        (
            _f("00003", "provider_error", 4),
            _f("00007", "provider_error", 4),
            _f("00012", "empty_translation", 1),
        )
    )
    # **ID 나열을 유지한다.** 원문이 남은 자막은 겉보기에 정상이라 개수만
    # 보고 넘기면 미번역 자막이 그대로 배포된다(기존 독스트링의 근거).
    assert lines == [
        "  실패 세그먼트(원문 유지) 3건:",
        "    provider_error 2건 (시도 4회): 00003, 00007",
        "    empty_translation 1건 (시도 1회): 00012",
    ]


def test_같은_사유에_시도_횟수가_다르면_범위로_적는다() -> None:
    lines = _format_failure_lines(
        (_f("00001", "invalid_response", 1), _f("00002", "invalid_response", 4))
    )
    assert lines == [
        "  실패 세그먼트(원문 유지) 2건:",
        "    invalid_response 2건 (시도 1~4회): 00001, 00002",
    ]


def test_사유_순서가_입력_순서에_좌우되지_않는다() -> None:
    """**같은 실행을 두 번 돌리면 같은 화면이 나와야 한다** (NFR-3).

    dict 삽입 순서를 그대로 쓰면 배치 스케줄에 따라 줄 순서가 바뀌고,
    로그를 diff하는 CI가 매번 변경을 본다.
    """
    a = _format_failure_lines((_f("1", "empty_translation", 1), _f("2", "provider_error", 1)))
    b = _format_failure_lines((_f("2", "provider_error", 1), _f("1", "empty_translation", 1)))
    assert a[1:] == b[1:]


def test_알_수_없는_사유도_그대로_낸다() -> None:
    """**화이트리스트로 거르지 않는다.** `engine.py`가 사유를 하나 더 넣었을 때
    화면에서 조용히 사라지면 그것이 정확히 이 태스크가 고치는 결함이다."""
    lines = _format_failure_lines((_f("00001", "새_사유", 2),))
    assert "새_사유 1건 (시도 2회): 00001" in lines[1]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_failure_reasons.py -v`
Expected: FAIL — `ImportError: cannot import name '_format_failure_lines'`

- [ ] **Step 3: 구현한다** (`src/cuesift/cli.py`, `_format_report` 계열 함수 근처)

```python
def _format_failure_lines(failures: Sequence[SegmentFailure]) -> list[str]:
    """실패 세그먼트를 사유별로 묶어 낸다 (FR-2.6 · 파킹 #2).

    **개수만 내면 안 되는 이유는 이미 이 파일에 있었다** - 원문이 남은 자막은
    겉보기에 정상인 파일이라 미번역 자막이 그대로 배포된다. 여기에 사유를
    더하는 이유는 그 다음 질문이 "그래서 다시 돌리면 되나"이기 때문이다:
    `provider_error`는 서버·설정을, `empty_translation`은 모델을 가리킨다.

    **사유를 정렬한다.** 삽입 순서를 그대로 쓰면 배치 스케줄에 따라 줄 순서가
    바뀌어 같은 입력이 다른 화면을 낸다(NFR-3).

    **시도 횟수를 범위로 낸다.** 같은 사유라도 배치 경로와 개별 폴백 경로의
    `attempts`가 다르다 - 하나로 뭉치면 "4번 버텼다"와 "한 번에 죽었다"가
    같은 줄로 보인다.
    """
    if not failures:
        return []
    grouped: dict[str, list[SegmentFailure]] = {}
    for f in failures:
        grouped.setdefault(f.reason, []).append(f)
    lines = [f"  실패 세그먼트(원문 유지) {len(failures)}건:"]
    for reason in sorted(grouped):
        group = grouped[reason]
        attempts = sorted({f.attempts for f in group})
        span = f"{attempts[0]}" if len(attempts) == 1 else f"{attempts[0]}~{attempts[-1]}"
        ids = ", ".join(f.segment_id for f in group)
        lines.append(f"    {reason} {len(group)}건 (시도 {span}회): {ids}")
    return lines
```

`SegmentFailure`를 `cuesift.translate` import 목록에 더한다.

- [ ] **Step 4: 호출부를 바꾼다** (`cli.py:2266-2268`)

```python
    lines.extend(_format_failure_lines(result.failures))
    return lines
```

`if result.failures:` 블록을 통째로 위 두 줄로 갈음한다.

- [ ] **Step 5: 기존 단언을 확인한다 — 없는 것이 정상이다**

`grep -rn "실패 세그먼트\|원문 유지" tests/`는 **무출력이다**(착수 시점 실측).
고칠 기존 단언이 없다 — 그것이 이 태스크의 존재 이유다.

**개수 줄은 그대로 살아 있어야 한다.** `tests/test_cli_translate.py:1249`의
`assert "실패 2개" in _summary_line(result.output, "en")`은 형식을 바꾸지 않았으므로
계속 통과해야 한다. **여기가 죽으면 요약 줄을 건드린 것이고, 그것은 이 태스크의 범위 밖이다.**

- [ ] **Step 6: 통과 확인 + 전체 게이트**

Run: `.venv/Scripts/python.exe -m pytest --cov=cuesift`
Expected: PASS · 커버리지 99% 이상

- [ ] **Step 7: 변이로 증명한다**

`_format_failure_lines`가 `return [f"  실패 세그먼트(원문 유지) {len(failures)}건:"]`만 내게 바꾼다
(사유 줄을 통째로 지운다). Expected: `test_사유별로_묶고_ID를_전부_나열한다` 포함 3건 이상이 죽는다.
**0건이면 화면을 읽는 테스트가 없다는 뜻이다** — FR-8.5에서 배선 8곳이 게이트 없이 통과한 것과 같은 함정이다.

- [ ] **Step 8: 커밋**

---

## Task 3: dry-run이 "왜 0회인가"를 말한다

**Files:**

- Modify: `src/cuesift/tier1.py` (진단 문구 2개를 순수 함수로 추출)
- Modify: `src/cuesift/cli.py:1714-1718`
- Modify: 기존 dry-run 테스트 (`grep -rln "_TIER1_BOUND_PREFIX\|재번역 요청 최대" tests/`)

**Interfaces:**

- Produces: `tier1.explain_zero_bound(total: int, max_ratio: float) -> str | None`
- Consumes: 없음(순수 함수)

### 무엇을 할 수 있고 무엇을 못 하나

실행 경로의 `_diagnose_empty_candidates`(`tier1.py:322`)는 원인을 **6개** 구분하지만
그 입력 `scored`는 **번역이 끝난 뒤에만** 존재한다. dry-run은 번역을 안 한다.

| 원인 | dry-run에서 계산되나 |
| --- | --- |
| `max_ratio == 0.0` (사용자가 껐다) | ✅ |
| `floor(n × max_ratio) == 0` (내림으로 0) | ✅ |
| 세그먼트 0건 | (파싱 단계에서 이미 66으로 죽는다) |
| 전량 `excluded_ids` | ❌ 번역 필요 |
| 후보가 전부 번역 실패분 | ❌ 번역 필요 |
| 회색지대가 비었다 | ❌ 채점 필요 |

**둘까지만 낸다.** "실주행과 같은 진단"을 약속하면 거짓이 된다.

**문구를 복제하지 않는다.** `tier1.py`가 이미 두 문장을 갖고 있으므로(`367`·`375-380`)
거기서 순수 함수로 빼서 양쪽이 같은 것을 부른다 — 복제하면 한쪽만 고쳐져 갈라진다
(`gray_zone()`을 공유한 것과 같은 이유, 2라운드 리뷰 C3).

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_tier1.py`에 이어 붙인다)

```python
def test_상한이_0인_두_원인을_구분한다() -> None:
    """dry-run이 계산할 수 있는 것은 이 둘뿐이다 (파킹 #3)."""
    assert explain_zero_bound(10, 0.0) == "max_ratio=0.0 - Tier 1을 껐다 (정상)"
    msg = explain_zero_bound(10, 0.05)
    assert msg is not None
    assert "내림(floor)으로 0이 됐다" in msg
    assert "10" in msg and "0.05" in msg


def test_상한이_0이_아니면_None이다() -> None:
    # 설명할 것이 없는데 문장을 내면 화면이 늘 시끄럽다.
    assert explain_zero_bound(100, 0.05) is None


def test_실행_경로와_같은_문자열을_쓴다() -> None:
    """**복제 금지의 게이트다.** 한쪽만 고치면 여기가 죽는다."""
    assert explain_zero_bound(10, 0.0) == _ZERO_BY_SWITCH
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -k zero_bound -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: `tier1.py`에서 문구를 뺀다**

**`import math`를 먼저 더한다.** `tier1.py`는 지금 `math`를 임포트하지 않는다(실측:
`from __future__` 다음이 `collections.abc`다). 빠뜨리면 `explain_zero_bound`가 `NameError`로 죽는다.

```python
# **두 곳이 같은 문장을 써야 한다** - 실행 경로의 `_diagnose_empty_candidates`와
# dry-run의 상한 줄이다. 복제하면 한쪽만 고쳐져 dry-run이 조용히 다른 원인을
# 말하게 된다(`gray_zone()`을 공유한 것과 같은 이유, 2라운드 리뷰 C3).
_ZERO_BY_SWITCH = "max_ratio=0.0 - Tier 1을 껐다 (정상)"


def _zero_by_floor(total: int, max_ratio: float) -> str:
    return (
        f"세그먼트 수({total})에 비해 max_ratio({max_ratio})가 작아 "
        "Tier 1 상한이 내림(floor)으로 0이 됐다 "
        "(select_tier1_candidates 독스트링 - n < 1/max_ratio)"
    )


def explain_zero_bound(total: int, max_ratio: float) -> str | None:
    """Tier 1 상한이 0이 되는 **번역 없이 계산 가능한** 두 원인만 구분한다.

    나머지 넷(전량 excluded · 후보가 전부 실패분 · 회색지대 공백 · 세그먼트 0건)은
    채점된 `SegmentRisk`가 있어야 판정되므로 dry-run에서는 알 수 없다 -
    `_diagnose_empty_candidates`가 그것을 한다. **모르는 것을 말하지 않는 것이
    이 함수의 계약이다.**
    """
    if max_ratio == 0.0:
        return _ZERO_BY_SWITCH
    if math.floor(total * max_ratio) == 0:
        return _zero_by_floor(total, max_ratio)
    return None
```

`_diagnose_empty_candidates`의 해당 두 `return`을 `_ZERO_BY_SWITCH`·`_zero_by_floor(len(scored), max_ratio)`로 바꾼다.
**기존 문자열과 한 글자도 달라지면 안 된다** — 그 문자열을 단언하는 테스트가 이미 있다.

- [ ] **Step 4: `cli.py`의 dry-run 줄에 붙인다** (`1714-1718`)

```python
            bound = math.floor(len(result.segments) * max_ratio) * samples
            line = (
                f"  {_TIER1_BOUND_PREFIX}{bound}회 (재시도·폴백 제외"
                f" · 후보 상한 비율 {max_ratio} · 샘플 {samples})"
            )
            # **0회는 파라미터만 보여 주면 원인을 말한 것이 아니다**(파킹 #3).
            # 실주행에는 `_diagnose_empty_candidates`가 원인 6개를 구분하는데
            # dry-run에는 아무 설명이 없어, 사용자가 `--tier1`이 안 먹는다고 읽었다.
            if bound == 0:
                why = explain_zero_bound(len(result.segments), max_ratio)
                if why is not None:
                    line += f"\n    사유: {why}"
            lines.append(line)
```

- [ ] **Step 5: 통과 확인 + 기존 dry-run 테스트 갱신**

Run: `.venv/Scripts/python.exe -m pytest --cov=cuesift`
`_TIER1_BOUND_PREFIX`를 단언하는 테스트가 새 줄에 걸리면 함께 고친다.

- [ ] **Step 6: 변이로 증명한다**

`explain_zero_bound`가 언제나 `None`을 돌려주게 바꾼다.
Expected: dry-run 사유 단언과 `test_상한이_0인_두_원인을_구분한다`가 죽는다.

- [ ] **Step 7: 커밋**

---

## Task 4: 기록

**Files:**

- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Changed`에 종료 코드, `### Added`에 사유 렌더링·dry-run 진단)
- Modify: `HANDOFF.md` (파킹 #1·#2·#3 **닫힘** 표시, 게이트 수치 갱신)

- [ ] **Step 1: CHANGELOG**

**`### Changed`에 넣는다 — 파괴적 변경이다.** `translate`의 부분 실패 종료 코드가 `1` → `75`로 바뀌므로
`!= 0`이 아니라 `== 1`로 판정하던 CI 스크립트가 영향을 받는다. 근거로 **FR-7.5·FR-2.6이 이것을 요구한 적이 없다**는
사실을 적는다.

- [ ] **Step 2: HANDOFF**

파킹 표에서 #1·#2·#3을 지우고 **닫힘** 기록으로 옮긴다. #1의 서술이 "전량 실패"였는데
실제로는 "한 건만 실패해도"였다는 정정을 함께 남긴다 — 파킹 노트가 조건을 좁게 적으면
다음 사람이 우선순위를 낮게 본다.

- [ ] **Step 3: 문서 게이트**

```bash
python scripts/check_links.py          # 마크다운 38개(계획서 +1) · 두 도구 파일 수 일치 확인
npx --yes markdownlint-cli2
```

- [ ] **Step 4: 커밋**

---

## 완료 조건

| 게이트 | 기대값 |
| --- | --- |
| `ruff check .` · `format --check .` | 통과 · 112 files |
| `pytest --cov=cuesift` | **1547보다 크다** · 커버리지 **99% 이상** |
| `scripts/check_links.py` · `markdownlint-cli2` | 둘 다 **38개** (계획서 1개 추가) · 깨진 링크 0 · 0 issues |
| 변이 증명 | T1·T2·T3 각각 지정 변이에서 **죽는 테스트 1건 이상** |
| CLI 옵션 수 | **24개 그대로** (이 작업은 옵션을 늘리지 않는다) |

## Self-Review 기록

| 점검 | 결과 |
| --- | --- |
| 파킹 #1·#2·#3 전부 태스크가 있나 | T1(#1) · T2(#2) · T3(#3) |
| 플레이스홀더 | 없음 — 모든 코드 블록이 실제 코드다 |
| 타입 일관성 | `_combine_exit_codes(Iterable[int]) -> int` · `_format_failure_lines(Sequence[SegmentFailure]) -> list[str]` · `explain_zero_bound(int, float) -> str \| None` — 태스크 간 참조가 일치한다 |
| 놓친 것 | `worst = max(...)` 회귀는 스펙 단계에 없었고 계획 작성 중 발견해 T1에 넣었다 |
