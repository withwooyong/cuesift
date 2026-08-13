# Session Handoff

> Last updated: 2026-08-13 (KST)
> Branch: **`feat/check-cli`** (작업 트리 깨끗, stash 없음)
> Latest commit: `8199887` 최종 리뷰 fix (D12 구현 · 진단 정규화)
> **원격은 `092a399`에 멈춰 있다 — 로컬이 35커밋 앞선다.** [PR #2](https://github.com/withwooyong/cuesift/pull/2)가
> **DRAFT로 열려 있고 커밋 2개만 담고 있다**(제목도 "설계 확정" 시점 그대로다).
> **다음 세션은 푸시 승인을 받는 것부터 시작한다** — 코드는 끝났다

## Current Status

**`cuesift check`가 실제로 동작한다.** WBS가 지목한 **"가장 짧은 쓸 수 있는 제품" 경로**에
도달했다 — 번역(WP7)도 STT(WP9)도 없이 완결되는 유일한 지점이었고, v0.1 전체를 기다리지
않고 나온 첫 중간 산출물이다.

| | 이전 세션 | 이번 세션 |
| --- | --- | --- |
| v0.1 FR 완료 | 19/42 (45%) | **21/42 (50%)** — FR-8.2 · FR-7.5 |
| 테스트 | 316 | **486** |
| 제품 상태 | 모든 서브커맨드가 `70` | **`check`는 동작 · `translate`·`transcribe`만 `70`** |
| 산출물 | 설계 스펙 1건 | 구현 계획 1건 + 코드 + 테스트 170건 + 문서 정정 |

```bash
cuesift check dist/episode01.ja.srt --spec ja --fail-on hard
```

종료 코드 5종이 **실측으로 갈린다.**

| 코드 | 뜻 |
| --- | --- |
| `0` | 위반 없음 (또는 `--fail-on none`) |
| `1` | **규격 위반 발견** |
| `2` | 명령줄이 틀림 — 파일 없음·디렉터리·프로파일 해석 실패 |
| `66` | 파일 내용이 틀림 — 자막 아님·utf-8 아님·읽기 불가·파싱 실패·큐 0개·타임코드 역전/타입 오류 |
| `70` | 미구현 (`translate`·`transcribe`) |

저장소: <https://github.com/withwooyong/cuesift> (Public).

## Completed This Session

7개 태스크 전부. 브랜치는 `main` 기준 **37커밋**이다.

| # | 태스크 | 핵심 커밋 |
| --- | --- | --- |
| 0 | [구현 계획](docs/superpowers/plans/2026-08-13-check-cli.md) (7태스크 분해 · 실측으로 설계 5건 정정) | `5c07fc0` |
| 1 | `--fail-on`을 `hard\|any\|none`으로 | `90c6a7d` |
| 2 | `check_empty_cues` — 어느 경로로도 안 잡히던 사각지대 | `a580a66` |
| 3 | `TrackViolation` · `check_track` | `5c5dd5a`·`95e182a` |
| 4 | `_resolve_profile` — `--spec`을 확장자로 가른다 (FR-5.3 도달) | `f16bb0f`·`8845d5a`·`70f5aef` |
| 5 | `_format_report` 순수 함수 | `800d17c`·`ca29ef5`·`7d3e5f6` |
| 6 | `check()` 배선 · 종료 코드 5종 (fix 6라운드) | `4899fec`·`59c7b51`·`955b4cd`·`6b45f95`·`c61a067`·`819b861` |
| 7 | 문서 정정과 진척 기록 | `a205bd4`·`c228dbf` |
| — | 최종 브랜치 리뷰 fix (Important 2 · Minor 2) | `8199887` |

**요구사항정의서 §3.2 S3 정정은 닫혔다** — `--spec th` → `--spec ja`, **사용자 승인 2026-08-13**.
파일명도 함께 바꿨다(`--spec`만 고치면 "태국어 파일을 일본어 규격으로 검사"가 된다).

## In Progress / Pending

| # | 작업 | 상태 | 비고 |
| --- | --- | --- | --- |
| 1 | **푸시 → PR #2 갱신 → CI 통과 대기** | ⬜ **다음** | **푸시는 사용자 승인 후.** PR은 **새로 만들지 말 것** — [#2](https://github.com/withwooyong/cuesift/pull/2)가 이미 열려 있고 푸시하면 35커밋이 거기 들어간다. 제목·본문을 "설계 확정"에서 **구현 완료**로 고치고 DRAFT를 해제한다. `main` 직접 푸시 금지 — CI가 게이트가 아니라 사후 통보가 된다 |
| 2 | WP5 나머지 (FR-7.1~7.4) | ⬜ | `review.json`·`report.html`·요약 통계. **WP7 뒤가 낫다** — 리포트에 실을 신호가 번역 계층에서 나온다 |
| 3 | WP7 번역 → WP8 Tier 1 | ⬜ | **Q4(자가일관성 유사도)가 여기서 닫힌다** — 남은 미결정 하나 |
| 4 | WP6 나머지 (FR-8.1·8.3~8.5) | ⬜ | `translate`·`transcribe` 배선 · `cuesift.yaml` 로더 |
| 5 | WP9 STT | ⬜ | 런타임 의존성 4개 고정과 충돌 — 호출 방식 미결 |

**CI는 로컬과 다른 것을 검증한다** — 로컬 venv는 Python 3.14인데 CI는 3.11/3.12다.
`docs` 잡(markdownlint + 링크 검사)도 함께 돈다.

## 파킹 목록 — 후속 작업 후보

**하나만 순위가 매겨져 있다.**

| # | 항목 | 왜 미뤘나 | 틀렸을 때 비용 |
| --- | --- | --- | --- |
| **U11** | **출력 억제 수단이 없다 (`--summary`·`--limit N`)** — **후속 1순위** | 새 플래그라 FR-8.2 표면 확장이다 | 26화 × 3언어에서 프로파일을 잘못 물리면 약 5만 줄이 쌓이고, **요약 줄이 맨 아래라 로그를 절단하는 CI에서 가장 중요한 한 줄이 먼저 사라진다** |
| U3 | `overlap`이 상대 큐를 안 알려준다 | `SpecViolation`에 필드가 늘어 Task 3 계약 변경 | 긴 큐가 여럿을 덮을 때 직전 큐를 헛되이 확인한다. 인접 겹침(대다수)은 타임코드로 복구된다 |
| — | **음수 타임코드가 exit 0** | 타입·역전 사이에 "범위" 검사가 비어 있다 | `_format_timecode`가 `max(ms, 0)`으로 `00:00:00.000`을 찍어 **검수자가 그 자리에 가면 아무것도 없다** |
| B4 | VTT `&nbsp;` 엔티티가 리터럴로 남아 CPS를 부풀린다 | 인제스트 계층이라 범위 밖 | 해당 큐의 CPS가 과대 계산된다. 빈도 미상 |
| m4 | **`cli.py`의 스트림 배관 161줄(전체 606줄의 27%)을 `console.py`로** | 동작에 문제가 없다 | 없음. **WP7 착수 시 후보** — `translate`가 같은 배관을 물려받는다 |
| M7~M9 | exit 2 메시지가 영어(typer 기본) · 파일 간 구분선 없음 · 출력 상한 없음 | 표면 확장이거나 typer 기본값 | 낮음 |
| — | `{"events":[{}]}` → exit 1 | FR-3.2의 **설계된 동작**이다(빈 값은 hard fail) | 없음 |
| — | ENOSPC의 120이 CPython flush 실패와 같은 코드 | 구분할 수단이 없다 | 디스크 가득 참과 파이프 사고가 같은 코드로 보인다 |
| — | `isinstance(proxy, io.TextIOBase)`가 False | rich·typer에 그 검사가 없다(A/B 바이트 동일) | 그 검사를 하는 라이브러리가 들어오면 깨진다 |

## Known Issues — 다음 세션이 반드시 알아야 할 것

### 🔴 측정 환경이 cp949 문제를 가린다

**`PYTHONIOENCODING=utf-8:surrogateescape`가 설정된 환경에서는 cp949 결함이 재현되지 않는다.**
모르고 재검증하면 **"문제 없다"는 틀린 결론**에 도달한다. 바닐라 Windows를 재현하려면
`PYTHONIOENCODING=cp949`로 명시하거나 변수를 지운다.

이것이 왜 중요한가 — 이 저장소에서 **exit 1은 "규격 위반 발견"**이다. cp949로 인코딩할 수
없는 문자(파일명의 `é`·`–`, 출력 리터럴의 em dash)가 리다이렉트 시 `UnicodeEncodeError`를
내면 프로세스가 exit 1로 죽고, **위반 0건인 깨끗한 자막이 CI에서 실패로 읽힌다.**

방어는 두 층이다. `_harden_output_streams()`(그룹 콜백, `errors="backslashreplace"`)가
사용자 입력이 흐르는 경로를 덮고, **`--help`·`--version`은 eager 옵션이라 콜백보다 먼저
렌더되므로 거기가 닿지 않는다** — 그쪽은 리터럴에서 em dash를 빼는 것으로만 막히고
`test_help_output_is_encodable_in_the_cp949_locale`이 고정한다.

### 방법론 교훈 둘 — 이 세션이 실제로 지불한 값

**① 방어는 "아는 경로"가 아니라 모든 경로가 반드시 지나는 지점에 둔다.**

Critical 5건 중 2건(C1 파이프·C3 타임코드)이 같은 형태였다 — 구현자가 아는 경로에만 방어를
두었고 열거에서 빠진 경로로 샜다. 해법은 경로를 더 열거하는 것이 아니라 **길목**에 두는
것이었다: 스트림 객체(`_TolerantOutput`)와 인제스트 경계(`_to_segments`).

**fix round 1이 상황을 악화시킨 것이 이 교훈의 증거다** — 닫힌 파이프를 잡아
`SystemExit(0)`으로 바꿨더니 **exit 2와 70이 조용한 0**이 됐다. 120은 시끄럽지만
0은 조용히 CI를 통과시킨다.

**② 주석·계획서의 "무엇이 깨지는가"는 주장이므로 테스트와 같은 기준으로 검증한다.**

**이 세션에서 네 번 틀렸고 넷 다 한 줄 실행이면 반증됐다.** 고치기는 쉬운데 가장 자주
재발했다. 마지막 사례가 가장 비쌌다 — **`--config`를 "구현하지 않는다"고 룰링한 근거가
거짓이었다**: `check x.srt --config c.yaml` → exit 2를 보고 "옵션이 애초에 없다"고 결론
냈는데, `--config`는 **그룹 옵션이라 서브커맨드 앞**에 와야 하고 거기서는 조용히 exit 0이었다.
**관찰은 맞았고 추론이 틀렸다 — 옵션이 없는 위치에서만 시험했다.**

### 🔴 `rich`는 호스트 플랫폼에 따라 다른 문자를 그린다 — 테스트가 그것에 의존하면 안 된다

**로컬 485 passed인데 CI가 실패했다.** 이 브랜치의 35커밋이 그때까지 **CI를 한 번도 거치지
않았기** 때문에 마지막에 한꺼번에 드러났다.

| 환경 | `rich`의 모서리 | cp949 |
| --- | --- | --- |
| Windows (`legacy_windows=True`) | `┌┐└┘` U+250C·2510·2514·2518 | **인코딩 된다** |
| Linux CI (`legacy_windows=False`) | `╭╮╰╯` U+256D~2570 | **인코딩 안 된다** |
| **실제 실행 + 리다이렉트** | **박스를 아예 안 그린다** | 무관 |

**세 번째 줄이 핵심이다** — 박스 문자는 `CliRunner`가 만들어 낸 산물이지 사용자가 만나는
것이 아니다. 그것을 검사하면 Windows에서 통과하고 Linux에서만 실패한다.

**로컬에서 CI 렌더링을 재현하는 방법**(다음 세션이 다시 필요할 것이다):

```python
import rich.console as rc
rc.detect_legacy_windows = lambda: False   # pytest 플러그인으로 -p 로 주입
```

같은 이유로 **`rich`가 렌더한 stderr에 긴 문자열·경로를 통째로 단언하지 않는다.** 강제 개행
위치가 임시 디렉터리 경로 길이에 좌우돼 **로컬과 CI에서 다른 곳에서 끊긴다**(실측: 로컬은
`utf-8로 읽을 수 없다`가, CI는 `cp949-spec.yaml`이 끊겼다). 정규화 헬퍼가
**`tests/conftest.py`**(이번에 신설)에 있다 — `strip_rich_decoration`·`normalize_rich_message`.

### 문서 게이트 — 두 게이트의 파일 개수 대조가 유일한 탐지 수단

**링크 체커는 `git ls-files` 기준이라 미추적 파일을 건너뛴다.** 새 문서를 만들면
`git add` 후에 돌려야 검사된다. markdownlint의 `Linting: N files`와 대조하는 것이
누락을 잡는 유일한 방법이고, 이번 세션에도 실제로 썼다(**양쪽 19개 일치**).

앵커와 외부 URL은 **아무도 검사하지 않는다** — 절 번호를 바꾸면 조용히 깨진다.

### 이전 세션에서 이어지는 이슈 (여전히 유효)

- `logprobs`는 백엔드에 따라 조용히 사라진다(Ollama 미지원)
- 자가일관성 샘플링은 `n>1` 단일 호출이 아니라 **N회 개별 호출**이어야 이식성이 유지된다
- **`spec.overlap` 기여도 `+0.0%`는 아직 "측정하지 못했다"** — `overlap.vtt` → `check_overlaps`
  경로는 열렸으나 벤치 하네스가 여전히 겹침 0건인 합성 트랙을 쓴다
- **WP5가 알아야 할 것**: `subs` + `event_index`로 SRT·VTT·ASS·SSA 4포맷 라운드트립이
  성립하지만 **태그 보존은 안 된다** — `plaintext` setter가 텍스트를 통째로 대체해
  번역을 되쓰면 `{\an8}`이 사라지고, VTT cue settings와 화자 태그는 로드 시점에 버려진다
- `IngestError.reason`이 자유 `str`이다. 타입 체커가 없어 `Literal`은 장식이 된다

## Key Decisions Made

[설계 스펙](docs/superpowers/specs/2026-08-03-check-cli-design.md) §13에 결정 로그 12건이
있다. 이번 세션에 **뒤집힌 것 하나**를 포함해 요약한다.

- **`check`는 신호 엔진을 통과하지 않는다**(D3). `collect_all`→`fuse`→`triage`가 얹는 넷
  (점수화·`hard_fail`·융합·트리아지)을 하나도 쓰지 않기 때문이다. 직접 호출이 HANDOFF가
  경고한 함정을 **회피가 아니라 소멸**시켰다 — `struct.*` 수집기가 아예 실행되지 않는다
- **심각도는 단일 등급**이고 `--fail-on hard`와 `any`가 v0.1에서 같은 결과를 낸다.
  등급을 발명하지 않은 것은 배정의 출처가 없기 때문이다(Netflix TTSG에 등급 구분이 없다)
- **종료 코드 `2`와 `66`을 분리한다**(D8). 축은 "호출이 틀렸나, 파일이 틀렸나"다.
  **`1`은 위반에만 쓴다** — 진단 실패를 1로 내면 CI가 "규격 위반"과 "파일을 못 읽음"에
  같은 대응을 한다
- **`--spec`은 확장자로 경로와 이름을 가른다**(D10, `.yaml`/`.yml`). 존재 여부로 가르면
  오타 난 경로가 "내장 이름이 없다"는 **틀린 진단**을 받는다
- **D12 `--config`는 경고하고 무시한다 — ❌ "구현하지 않는다"는 판단이 뒤집혔다.**
  근거가 거짓 전제 위에 있었다(위 교훈 ②). 최종 리뷰에서 설계대로 구현했다

### 이전 세션에서 이어지는 결정 (변경 없음)

초기 언어쌍 **ko→en/ja**(Q2) · 로컬 LLM은 **OpenAI 호환 엔드포인트로 일원화**(Q3) ·
규격 1차 출처는 **Netflix TTSG**(Q5) · LLM 연동은 **자체 얇은 어댑터**(Q6) ·
배수 헤드라인은 **예산 10%**에서 뽑는다 · **가중치는 튜닝하지 않는다** ·
인제스트는 **단일 진입점** · 파싱한 파일은 항상 **`source_text`**에 들어간다.

## 컨트롤러가 겪은 것 — 절차 규율

- **리뷰가 도는 동안 구현자에게 지시를 보내지 않는다.** 실제로 작업트리가 움직이는 중에
  변이 측정이 들어가 수집이 497 → 481로 줄었고, 재검토어가 **합계가 안 닫히는 것**
  (`6+475=481 ≠ 497`)으로 붙잡았다. **이번엔 잡혔지만 다음엔 안 잡힌다.**
  리뷰어에게는 커밋 해시를 주고 고정 트리에서 재게 한다
- **수치를 적을 때 합계가 닫히는지 확인한다.** "5 failed, 488 passed"가 스위트 크기 497과
  맞지 않았다(실제 7 failed). "0개 수집은 통과가 아니라 설정 오류다"와 같은 자리다
- **`TaskUpdate`로 소유자를 기록하면 구현자에게 새 할당 신호로 전달된다**
- **`Bash` 도구는 Git Bash다.** PowerShell here-string(`@'…'@`)을 쓰면 메시지 앞뒤에 `@`가
  남는다. 여러 줄 커밋 메시지는 heredoc(`git commit --file=- <<'MSG'`)을 쓴다
- **Git Bash는 `/no/such/path`를 `C:\Program Files\Git\no\such\path`로 변환한다** —
  절대 경로를 인자로 넘기는 실측에서 출력이 예상과 달라 보일 수 있다

## 게이트 실행 기록

전부 `.` 대상이다. **`src tests`로 좁히면 안 된다** — 그것이 CI 5회 연속 실패를 숨겼다.

| 게이트 | 결과 |
| --- | --- |
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `57 files already formatted` |
| `pytest --cov=cuesift` | **486 passed** · TOTAL 99% (`cli.py`·`spec/check.py`·`loader.py` 100%) |
| `scripts/check_links.py` | 마크다운 **19개** · 상대 링크 **69개** · 깨진 링크 **0** |
| `markdownlint-cli2` | **`Linting: 19 files`** · 0 issues |

## Files Modified This Session

```text
src/cuesift/cli.py              check() 배선 · _resolve_profile · _format_report · 스트림 배관
src/cuesift/spec/check.py       TrackViolation · check_empty_cues · check_track
src/cuesift/spec/profile.py     내용 오류를 ValueError로 정규화 (utf-8 디코드 포함)
src/cuesift/ingest/loader.py    타임코드·텍스트 타입을 경계에서 보증 · OSError 정규화
tests/test_cli_check.py         신규 · tests/test_cli.py 확장 · check_violations.ass 픽스처
docs/superpowers/plans/2026-08-13-check-cli.md   구현 계획 (신규)
README.md · CHANGELOG.md · docs/WBS.md · docs/요구사항정의서.md · 설계 스펙 2건
```
