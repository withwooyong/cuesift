# Session Handoff

> Last updated: 2026-08-29 (KST)
> **브랜치 `feat/progress-display`에 FR-8.5 구현이 끝나 있다. 게이트 5종 전부 통과.**
> 커밋 수는 `git rev-list --count main..HEAD`로 센다 — 여기 숫자를 적으면
> 그 문장을 고치는 커밋이 자기 자신을 틀리게 만든다(실측으로 한 번 겪었다).
> **브랜치가 `origin`에 있는지, PR이 있는지, CI가 돌았는지는 아래 "배포 절차"의 명령으로 직접 재라.**
> 값이 아니라 명령이다.
> 진척은 [WBS](docs/WBS.md), FR 번호의 출처는 [요구사항정의서](docs/요구사항정의서.md)다

## Current Status

**FR-8.5(진행 표시와 비대화형 감지)가 닫혔다. v0.1 완료 개수 36 → 37.**

| 단계 | 상태 | 산출물 |
| --- | --- | --- |
| 브레인스토밍 (입도·CI 출력·렌더러·스위치·분모·감지 신호·충돌 처리) | ✅ 사용자 승인 7건 | 아래 "확정된 결정" |
| [설계 스펙](docs/superpowers/specs/2026-08-29-progress-display-design.md) | ✅ 커밋 `580d3bb` | 결정 13건 · 착수 조사 6건 · 위험 4건 |
| [구현 계획](docs/superpowers/plans/2026-08-29-progress-display.md) | ✅ | 태스크 7개 · 게이트 수치 고정 |
| 구현 (T1~T6) | ✅ | `progress.py` 신규 · 이음매 3곳 · CLI 배선 8곳 |
| 이중 리뷰 (품질 축 · 계약 축) | ✅ 2라운드 | HIGH 1건 · MEDIUM 다수 수정 |
| 검증 5관문 | ✅ 전부 통과 | 아래 "게이트 실행 기록" |
| 문서 (T7) | ✅ | 요구사항정의서 · WBS · 스펙 정정 · CHANGELOG · 이 파일 |
| 푸시 · PR · CI | ⬜ **아래 "배포 절차"로 직접 재라** | — |

**`main` 머지는 사용자 승인 항목이다.** PR을 여는 것과 `main`을 바꾸는 것은 되돌리는 비용이 다르다.

## 무엇이 생겼나

| 부분 | 무엇 | 어디 |
| --- | --- | --- |
| A. 이음매 | 라이브러리가 진척을 바깥에 알린다 | `translate/engine.py` · `signals/base.py` · `tier1.py` — 셋 다 `on_progress=None` 선택 인자 |
| B. 감지와 우선순위 | CLI 인자 > `CUESIFT_PROGRESS` > `output.progress` > 자동 감지 | `progress.py` · `cli.py` |
| C. 렌더러 | stderr에 `\r`로 갱신(대화형) / 10%p 이정표 누적(plain) | `progress.py` |

`src/cuesift/progress.py` 하나가 신규이고 **103문 · 커버리지 100%**다.
런타임 의존성은 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`) 그대로 — `rich`를 넣지 않았다(D6).

**라이브러리로 쓰는 쪽의 동작은 이 변경 전후로 같다.** `on_progress`의 기본값이 `None`이고
`None`이면 호출 자체가 없다(D3).

## 이번 세션이 배운 것

### ⓐ "렌더러 하나"인 줄 알았던 것이 이음매까지였다 — grep이 범위를 두 배로 늘렸다

착수 조사 첫 질문이 "진행 훅이 이미 있나"였고 답이 **0건**이었다.

```bash
grep -rn "callback\|on_progress\|progress" src/cuesift/translate/ src/cuesift/tier1.py
# → 무출력
```

**FR-7.3의 `Span` 사건과 같은 구조다.** 그때는 스키마·검증·직렬화·주석이 다 있어 "구현됐다"는
착각이 만들어졌고 채우는 코드가 0건이었다. 이번엔 "진행 표시"라는 말이 렌더링만 떠올리게 해서
라이브러리 쪽이 비어 있다는 사실이 가려졌다. **두 번 다 grep 한 줄이 범위를 정정했다.**

### ⓑ 배선에는 게이트가 붙지 않는다 — 리뷰가 실제로 잡았다

이번 세션에서 가장 비싼 발견이다. 구현이 끝나고 테스트가 **1497건 전부 통과**하는 상태에서,
리뷰어가 리포터를 아무것도 하지 않는 널 객체로 바꾸는 변이를 넣었더니 **죽는 테스트가 0건**이었다.
`on_progress=` 2곳과 `phase()`/`done()` 6곳, **배선 8곳을 통째로 지워도 초록이었다.**

원인이 셋 겹쳤다.

| 겹친 원인 | 무엇 |
| --- | --- |
| `conftest.py`가 진행을 전역으로 껐다 | R2 대응으로 넣은 픽스처가 진행 경로 자체를 안 타게 만든다 |
| `--progress`를 켜는 테스트가 `--help`뿐이었다 | 문자열 검사라 실행 경로를 안 지난다 |
| 나머지 하나가 `--dry-run`이었다 | **리포터를 설치하기 전에 return한다** — stderr가 0바이트다 |

**"진행 표시를 검사하는 테스트가 있다"와 "진행 배선이 게이트를 받는다"는 다르다.**
지금은 `tests/test_cli_progress.py`가 실제로 stderr를 읽고, `tests/test_cli_tier1.py`의
`test_진행_표시가_Tier_1과_리포트_단계까지_덮는다`가 **배선 8곳 중 5곳**을 혼자 잡는다
(`--tier1`과 `--review-out`을 같이 줘야 Tier 1·리포트 단계를 지난다).

### ⓒ 계획서의 코드 블록이 틀릴 수 있다 — TDD가 셋을 잡았다

구현 계획을 그대로 옮겨 적었으면 조용히 틀렸을 것 셋이다.

| 무엇 | 계획 | 실제 |
| --- | --- | --- |
| `_last_pct` 초기값 | `-1` | **`0`**. `-1`이면 이정표가 9·19·…·99 + 100%로 **11줄**이 되어 계획 자신의 "10줄" 단언과 어긋난다 |
| `_FakeTTY.isatty` | `return self._tty` | 닫힌 스트림에서 `ValueError`를 **안 낸다** — `detect_style`의 `except ValueError` 갈래를 이름만 그 이름인 테스트가 검사하지 않았다 |
| 참조한 테스트 이름 | `test_후보만_재번역한다` | **없는 이름이다.** 실제는 `test_tier1은_후보에만_불린다`(`tests/test_tier1.py:86`) |

**계획서는 실행되지 않는다.** 실행되는 것은 테스트뿐이고, 그래서 계획의 코드 블록은
근거가 아니라 초안이다.

### ⓓ 진행 표시는 출력이 아니라 stderr에 **상태**를 만드는 일이다

지금까지 stderr에 나가던 것은 전부 완결된 줄이었다. `\r`은 커서 위치라는 상태를 남기므로
**그 자원을 쓰는 다른 모든 코드가 이 상태를 알아야 한다.**

그래서 `_echo`가 쓰기 **전에** `clear_active()`를 부른다(D11 — 스펙은 stderr만 말했지만
구현은 stdout 갈래에서도 부른다). 그리고 `Ctrl+C`는 `_echo`를 **지나지 않으므로** 그것만으로는
부족했다 — 실측으로 stderr가 `'\r[en] 번역 ... 20/45 (44%)'`로 개행 없이 끝나 셸 프롬프트가
같은 줄에 얹혔다. `finally`가 `clear()`까지 하도록 고쳤다.

### ⓔ 관측할 수 없는 결정은 회귀를 못 잡는다 — Tier 1 분모

`collect_tier1`은 `for name in names: for seg in segments:` 이중 루프인데 **오늘은 수집기가
하나뿐이라 두 정의의 값이 같다.** `len(candidates)`로 잘못 써도 전 스위트가 통과한다.
그래서 **가짜 두 번째 수집기를 등록하는 테스트**로 게이트를 세웠다.

### ⓕ 파이썬 `write_text`가 문서 3개를 통째로 CRLF로 바꿔 놨다

T7에서 스크립트로 문서를 고치다 겪었다. `Path.write_text(..., encoding="utf-8")`은
`newline=None`이라 Windows에서 `\n`을 **`\r\n`으로 번역한다.** 2줄짜리 수정이
`git diff --stat`에서 **1967줄 변경**으로 보였다. `newline=""`을 주면 막힌다.
**진짜 변경이 노이즈에 묻히는 것이 위험이다** — 리뷰가 통째로 무의미해진다.

같은 자리에서 하나 더: 마크다운 표 셀 안의 `` `a|b` ``는 백틱 안이어도 **셀 구분자로 세어진다.**
`markdownlint`의 MD056이 잡았다(`\|`로 탈출).

## 확정된 결정 (사용자 승인 7건 + 스펙 D1~D13)

| 축 | 결정 | 근거 |
| --- | --- | --- |
| 입도 | 단계 표시 + 세그먼트 진행률. `translate`만 | `check`는 LLM 호출이 없어 표시할 진행이 없다 |
| CI 출력 | 이정표 줄 누적. **10%p 이상 늘었을 때만** + 100%는 항상 | 배치마다면 4000세그먼트에서 언어당 400줄. 단계만이면 수십 분 침묵 |
| 렌더러 | **자체 구현.** `rich`를 쓰지 않는다 | typer의 전이 의존이고, rich가 `FORCE_COLOR`로 비TTY CI에서 색을 켜 `--help`를 깨뜨린 실측 전례가 있다 |
| 스위치 | `--progress/--no-progress`(3상) + `output.progress` | 감지가 틀린 환경의 탈출로. 플래그는 켜고 끄기만 정하고 **스타일은 언제나 감지가 정한다**(D7) |
| 이음매 | 콜백 주입. `ProgressUpdate(done, total)` 둘뿐 | 단계 이름은 CLI가 안다. 라이브러리가 문구를 알면 문구를 바꿀 때 라이브러리를 고친다 |
| Tier 1 분모 | 후보 수 × 수집기 수 | 위 ⓔ |
| 감지 신호 | `stderr.isatty()` · `CI` · `TERM=dumb` 셋 | `isatty`만 보면 TTY를 주는 CI 셸에서 `\r`이 로그에 남는다. `NO_COLOR`는 넣지 않는다 — 색 규격인데 이 렌더러는 색을 쓰지 않는다 |
| 충돌 처리 | `_echo`가 먼저 `clear()` | 위 ⓓ |
| 실패 격리 | 쓰기 `OSError`·`ValueError`는 리포터를 영구 비활성화, 예외 미전파 | 닫힌 파이프에서 종료 코드 계약이 깨진다 |

나머지 결정과 "이 결정이 아니면 무엇이 깨지는가"는
[설계 스펙 §2](docs/superpowers/specs/2026-08-29-progress-display-design.md)에 있다.

## 배포 절차 — **"푸시했다"와 "CI가 돌았다"는 다르다**

```bash
git rev-list --count main..HEAD          # 이 브랜치의 커밋 수
git ls-remote --heads origin feat/progress-display   # 비면 아직 푸시 안 됨
gh pr list --head feat/progress-display              # 비면 PR 없음
gh pr checks --watch                                  # CI 통과 대기
```

**`main`에 직접 푸시하지 않는다.** CI의 `push` 트리거가 `branches: [main]`뿐이라
직접 푸시하면 머지된 **뒤에야** CI가 돈다 — 게이트가 아니라 사후 통보다.

## 게이트 실행 기록

| 게이트 | 이 세션 | 비고 |
| --- | --- | --- |
| `ruff check .` | **All checks passed!** | 대상은 `.` — `src tests`로 좁히지 않는다 |
| `ruff format --check .` | **112 files already formatted** | |
| `pytest --cov=cuesift` | **1547 passed · 3 deselected** · 커버리지 **99%**(2447문 중 31 미도달) | 착수 기준선 1480 → **+67** |
| ↳ `src/cuesift/progress.py` | **103문 · 0 미도달 · 100%** | 신규 모듈 |
| `python scripts/check_links.py` | 마크다운 **37개** 파일 · 상대 링크 **186개** · 깨진 링크 **0** | 계획서 추가로 36 → 37 |
| `npx markdownlint-cli2` | Linting: **37 files** · **0 issues** | 두 도구의 파일 수가 **일치**한다 |
| CLI 옵션 수 | **24개** (23 → +1) | `test_config_schema.py`의 상등 게이트가 이 수를 고정한다 |

**두 도구가 같은 파일 수를 세는지 본다.** 어긋나면 새 `.md`가 아직 `git add`되지 않은 것이고,
그 문서는 링크 검사를 **아예 받지 않는다**. 실측으로 32 vs 31로 갈린 전례가 있다.

**CI는 1건 적게 센다.** `data/`가 `.gitignore`에 있어 깨끗한 체크아웃에는 벤치 트랙이 없고
`tests/test_bench_glossary.py`가 1건을 skip한다 — CI의 기대값은
**1546 passed · 1 skipped · 3 deselected**다.

## 파킹된 finding — **원장이 아니라 여기가 영구 기록이다**

| # | 무엇 | 왜 지금 안 했나 | 다시 열 조건 |
| --- | --- | --- | --- |
| 1 | **번역 전량 실패가 `exit 1`** | `main`에 이미 있는 기존 동작이다. `cli.py` 독스트링의 "1을 진단 실패에 쓰지 않는다"와 충돌하지만 이 브랜치가 만든 것이 아니다 | 종료 코드 체계를 손보는 작업이 열릴 때 |
| 2 | README 권장 모델(`qwen2.5:3b`)로 **번역이 대량 실패 + 실패 사유 0줄** | 진단 부재는 별도 작업. §12 Q3이 "로컬 LLM 능력은 균일하지 않아 **탐지·명시가 필요**"라 이미 말한다 | 진단 출력 작업. **문서가 권하는 모델이 실패하는 것 자체가 사용자 신뢰 문제** |
| 3 | `--dry-run`의 `최대 0회`에 **사유가 없다** | 실주행엔 `_diagnose_empty_candidates`가 있는데 dry-run엔 없다 | 2번과 같은 작업에서 함께 |
| 4 | **`COLUMNS=88` 아래에서 옵션 이름이 잘린다** — 이 브랜치가 **악화시켰다** | rich의 표 렌더링이라 FR-8.5의 범위 밖이다. 대신 폭 88을 게이트로 못 박았다 | 옵션을 더 붙여 88이 깨지는 날, 또는 도움말 렌더링을 손볼 때 |
| 5 | 화면 토큰(번역만) vs `review.json` `cost`(합계)가 **다른 객체** | 화면에 합계 줄을 넣을지는 설계 결정이다 | 차이를 설명할지 없앨지 정하는 일 |
| 6 | `inf` 메시지가 원인을 틀리게 말한다 | 문구 문제, 작지만 별건 | 조합 검증은 **7규칙 / 8종 문자열**이다 |
| 7 | `cli.py`의 줄번호 인용이 조용히 낡을 수 있다 | 이 저장소 `src/`에 같은 형태가 **15건**이라 관행이다 | 관행 전체를 바꾸는 작업 |
| 8 | 종료 코드 69 단축이 **Tier 1 실패에도** 걸린다 | 단축은 `EXIT_UNAVAILABLE`에서만 걸리고 건너뛴 언어를 stderr로 명시한다 — 손실이 없다 | 언어별로 다른 프로바이더를 쓸 수 있게 되는 날 |
| 9 | **`default_map` 값의 타입 변환에 게이트가 없다** | 게이트를 만들면 click 내부 의존이 한 겹 더 는다. [설정 파일 스펙 §9.1](docs/superpowers/specs/2026-08-28-config-file-design.md)에 적어 뒀다 | `Path` 전용 메서드를 쓰게 되는 날 |
| 11 | **`output.progress`에 스칼라가 아닌 YAML을 주면 트레이스백 + `exit 1`** | **기존 결함 부류의 세 번째 사례다** — `dry_run: [1]`·`signals.tier1.enabled: [1]`이 똑같이 행동한다. 이 브랜치가 만든 것이 아니고, 한 옵션만 고치면 부류가 남는다 | 설정 파일 값 검증을 **부류 전체**로 손보는 작업 |
| 12 | **닫힌 파이프 + 실제 번역 + 진행 켬** 조합이 미검증 | 리뷰어가 방법(`--dry-run`을 빼고 죽은 포트 `http://127.0.0.1:9/v1`)과 비용(1회 ~15초, CI에 행당 ~+30초)까지 냈다. 위험이 낮다고 판단했다 — `_TolerantOutput`이 한 층 아래에서 `EPIPE`를 삼킨다 | 파이프 계약을 다시 손볼 때, 또는 실제로 깨진 보고가 올 때 |

**#4의 귀책이 바뀌었다.** 이전 판은 "`COLUMNS=70`에서 잘린다 · 색 활성 재현 안 됨 `[의심]`"이었는데
**실측 결과 색과 무관한 순수 폭 문제다**(`NO_COLOR=1`에서도 같다). 잘리는 옵션은 2개가 아니라
**6개**(폭 70 기준)이고, 안전한 폭은 76이 아니라 **88**이다.

| COLUMNS | 60 | 70 | 76 | 80 | 84 | 88 |
| --- | --- | --- | --- | --- | --- | --- |
| 이전(`4cfdc28`) | 5 | 2 | 0 | 0 | 0 | 0 |
| 지금 | 7 | 6 | 4 | 3 | 1 | 0 |

**#10은 닫혔다.** 요구사항정의서 §0.1의 "수혜자 규칙이 FR-4.3을 🟡로 붙들고 있다"가 낡은
문장이었고 이 세션에서 고쳤다.

**1·2·3이 한 덩어리다** — 전부 "실패했는데 왜인지 안 말한다"이고, 2·3은 같은 작업에서 닫힌다.

## 남은 관측 하나 — R3

설계 스펙 §9 R3(Windows 콘솔의 `\r`)은 **구조는 확인됐고 육안 관측이 남아 있다.**
렌더러가 내는 원문을 `repr()`로 뜨면 프레임 사이에 개행이 하나도 없고 전부 `\r`로 시작한다.
남은 것은 **실제 Windows Terminal에서 커서가 같은 줄 위에 겹쳐 그려지는 것 자체**이고,
검증 에이전트는 라이브 화면을 볼 수 없어 관측하지 못했다.

사람이 한 번 돌리면 닫힌다.

```powershell
.venv/Scripts/python.exe -m cuesift translate tests/fixtures/ingest/ten_cues.srt --to en --out . --base-url http://h/v1 --model m1 --progress
```

**확인하지 않은 것을 확인했다고 적지 않는다.**

## 승계 항목 — 이 브랜치가 건드리지 않았다

| 항목 | 상태 |
| --- | --- |
| **Q4**(자가일관성 유사도 측정 수단) | 여전히 열려 있다. 문자 단위 유사도는 **형태**를 재고 **의미**는 못 잰다. 판정은 벤치마크에 Tier 1을 태우는 별도 작업의 몫 |
| **FR-4.2**(역번역) | 구현 안 함. 문자 단위 유사도로는 `llm.retranslation_gap`이 **역방향으로 작동**한다 |
| **FR-8.3**(`transcribe`) | STT 어댑터(WP9)가 선행이지만 **FR-8.3 자신은 WP6에 남는다** — §5.8("CLI") 소속이라 STT 로직이 아니라 CLI다. WP9는 어댑터(FR-1.2·1.4)만 낸다. **그래서 FR-8.5가 닫혀도 WP6은 ✅가 아니라 🟡다**(5개 중 4개) |
| **`engine.py::_run_single`의 전역 index** | 확인됐고 안 고쳤다. `main`에도 들어가 있다 |
| **`cli.py` 크기** (2900줄대) | FR-8.5는 새 코드를 `progress.py`로 뺐다 — `config/`를 뺀 것과 같은 방향이다. 배선만 늘었다 |
| `segments[].reasons`의 순서 미검증 | NFR-3 재현성 문제. 열려 있다 |

## 개발 환경 메모 (승계)

**Python 실행은 반드시 `.venv/Scripts/python.exe`를 쓴다.** 시스템 Python은 3.14라 다르다.
게이트는 CI와 같은 대상 `.`으로 돌린다 — **`src tests`로 좁히면 안 된다**(그 차이로
CI가 5회 연속 실패한 전례가 있다).

**리포 루트에 `cuesift.yaml`을 만들지 마라.** 자동 탐색이 그것을 읽는다.
`conftest.py`의 autouse가 인프로세스 테스트는 막지만 **서브프로세스는 못 막는다**.

**같은 이유로 진행 표시 차단 픽스처는 환경변수를 쓴다.** `CUESIFT_PROGRESS=0`을
`monkeypatch.setenv`로 넣어야 서브프로세스까지 닿는다 — 모듈 전역만 건드리면
서브프로세스 테스트의 stderr에 진행 줄이 섞인다.

Ollama는 트레이 앱 겸 백그라운드 서비스로 자동 기동해 `127.0.0.1:11434`를 듣는다 —
`ollama serve`를 따로 칠 필요가 없다. PATH에 없으면
`$env:LOCALAPPDATA\Programs\Ollama\ollama.exe`를 직접 부른다.

| 모델 | 크기 | 용도 |
| --- | --- | --- |
| `qwen2.5:3b` | 1.9GB | 번역·Tier 1 신호용. **실측: 3큐 중 2큐 실패**(파킹 2번) |
| `qwen2.5:1.5b` | 986MB | 폴백 관찰용. 번역기로는 못 쓴다(실측 5/15) |

**결정론이 필요하면 스텁 서버를 쓴다.** OpenAI 호환 `/v1/chat/completions`에
`{"translations":[{"id":N,"text":…}]}`를 돌려주면 된다.
**리포 밖에 두어야 게이트를 오염시키지 않는다.**

live 실행 명령:

```powershell
$env:CUESIFT_LIVE_BASE_URL="http://localhost:11434/v1"
$env:CUESIFT_LIVE_MODEL="qwen2.5:3b"
.venv/Scripts/python.exe -m pytest -m live -v -s
```

## 다음 세션 시작 절차

```bash
git checkout feat/progress-display
git log --oneline | head -3
git status --short                        # clean이어야 한다
git ls-remote --heads origin feat/progress-display   # 푸시됐나
gh pr list --head feat/progress-display              # PR이 있나
```

PR이 아직 머지되지 않았으면 **CI 결과를 먼저 보고** 머지 승인을 사용자에게 요청한다.
머지가 끝났으면 다음 작업은 **WP6의 마지막 조각 FR-8.3**이거나, 파킹 1·2·3 덩어리
(**"실패했는데 왜인지 안 말한다"**)다. 후자가 사용자 신뢰에 더 직접 닿는다.
