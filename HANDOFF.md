# Session Handoff

> Last updated: 2026-08-16 (KST)
> Branch: **`fix/check-output-and-negative-timecode`** — 커밋 3개, `main`에 아직 안 올라갔다.
> **로컬 게이트 5종 전부 통과**했으나 **CI는 아직 돌지 않았다** — PR을 만들어야 돈다.
> **다음 세션은 WP7(번역 계층)부터 시작한다** — `check` 표면은 닫혔다.
> 진척은 [WBS](docs/WBS.md), FR 번호의 출처는 [요구사항정의서](docs/요구사항정의서.md)다

## Current Status

**`check` 표면을 마무리했다.** 직전 세션이 파킹 1순위로 남긴 두 항목(U11 출력 억제 ·
음수 타임코드 exit 0)을 닫았고, 리뷰 2축이 찾은 지적 5건을 반영했다.

| | 이전 세션 | 이번 세션 |
| --- | --- | --- |
| v0.1 FR 완료 | 21/42 (50%) | **21/42 (50%)** — 새 FR 없음. 표면 확장과 결함 수정이다 |
| 테스트 | 486 | **499** |
| 인제스트 픽스처 | 13종 | **15종** (`starts_at_zero.srt` · `negative_timecode.ass`) |
| 음수 타임코드 | **exit 0 · "위반 없음"** | exit 66 |
| 출력 억제 | 없음 | `--limit N` (기본 0 = 무제한) |

```bash
cuesift check dist/episode01.ja.srt --spec ja --limit 50
```

종료 코드 5종은 그대로다. **`--limit`은 종료 코드를 보지 않는다.**

| 코드 | 뜻 |
| --- | --- |
| `0` | 위반 없음 (또는 `--fail-on none`) |
| `1` | **규격 위반 발견** |
| `2` | 명령줄이 틀림 — 파일 없음·디렉터리·프로파일 해석 실패·**`--limit` 음수/비정수** |
| `66` | 파일 내용이 틀림 — 자막 아님·utf-8 아님·읽기 불가·파싱 실패·큐 0개·타임코드 역전/**음수**/타입 오류 |
| `70` | 미구현 (`translate`·`transcribe`) |

저장소: <https://github.com/withwooyong/cuesift> (Public).

## Completed This Session

| 커밋 | 내용 |
| --- | --- |
| `49fab82` | 음수 타임코드를 인제스트 경계에서 거부 (exit 66) · `_format_timecode` 부호 보존 |
| `fb0949d` | `--limit N`과 요약 이중 출력 |
| `b0a76ec` | 리뷰 지적 5건 반영 — 거짓 전제 둘, 한쪽만 막던 게이트 하나, 근거 문장 하나, 설계 문서 하나 |

## 🔴 즉시 해야 할 것 — PR을 만들어야 CI가 돈다

**브랜치가 origin에 올라갔지만 PR이 없으면 CI가 한 번도 돌지 않는다.**
`.github/workflows/ci.yml`의 `push` 트리거는 `branches: [main]`뿐이다.
직전 세션이 **35커밋을 CI 없이 쌓았다가 마지막에 `rich` 렌더링 실패로 터진** 전례가 있다.

```bash
gh pr create --base main
gh pr checks --watch      # test 3.11 · test 3.12 · docs
gh pr merge --squash
```

**로컬 venv는 Python 3.14, CI는 3.11/3.12다.** 로컬 통과가 CI 통과를 뜻하지 않는다.

## In Progress / Pending

| # | 작업 | 상태 | 비고 |
| --- | --- | --- | --- |
| 0 | **PR 생성 · CI 통과 · 머지** | ⬜ **최우선** | 위 참조 |
| 1 | WP7 번역 → WP8 Tier 1 | ⬜ **다음 후보** | **Q4(자가일관성 유사도)가 여기서 닫힌다** — 남은 미결정 하나 |
| 2 | WP5 나머지 (FR-7.1~7.4) | ⬜ | `review.json`·`report.html`·요약 통계. **WP7 뒤가 낫다** — 리포트에 실을 신호가 번역 계층에서 나온다 |
| 3 | WP6 나머지 (FR-8.1·8.3~8.5) | ⬜ | `translate`·`transcribe` 배선 · `cuesift.yaml` 로더. **`--config`는 지금 경고만 낸다**(D12) |
| 4 | WP9 STT | ⬜ | 런타임 의존성 4개 고정과 충돌 — 호출 방식 미결 |

**WP7 착수 시 `cli.py`의 스트림 배관 ~161줄을 `cuesift/console.py`로 뽑을 때다.**
`_harden_output_streams` 독스트링이 스스로 "`translate`·`transcribe`가 구현되면 같은
문제를 각자 다시 풀어야 한다"를 근거로 든다 — 그 근거가 곧 분리 근거다.

## 파킹 목록 — 후속 작업 후보

**직전 세션의 1순위 U11과 음수 타임코드는 닫혔다.** 리뷰가 선존 결함 둘을 새로 찾았다.

| # | 항목 | 왜 미뤘나 | 틀렸을 때 비용 |
| --- | --- | --- | --- |
| **N1** | **stdout을 완전히 닫으면(`1>&-`) 모든 종료 코드가 120** | 부모 커밋 `de938e1`에서도 재현되는 **선존 결함**이라 이번 브랜치 범위 밖 | `EBADF`(errno 9)가 `_CLOSED_OUTPUT_ERRNOS = {EPIPE, EINVAL}`에 없다. `run()` 독스트링 표 3행("아래 `finally`가 120을 만들지 못하게 한다")이 이 경우 **거짓**이다. 현실적 호출인 `\| head`는 전부 정상이라 실사용 위험은 낮다 |
| **N2** | `ascii`·`cp1252` 로케일에서 그룹 `--help`가 exit 1 | 선존. `cp949`(대상 로케일)와 utf-8은 정상 | 그룹 help의 한글 자체가 원인이고 `_harden_output_streams`가 eager 옵션에 닿지 않는다는 기존 문서와 일치한다 |
| **N3** | **음수 타임코드 한 큐가 파일 전체를 죽인다** | 대안이 전부 더 나쁘다(아래 Key Decisions 참조) | 실측: 800큐 ASS에서 `-10ms` 하나가 실제 규격 위반 **17건**을 통째로 가렸다. SAMI가 지원 선언 포맷이라 사정거리가 있다 |
| U3 | `overlap`이 상대 큐를 안 알려준다 | `SpecViolation`에 필드가 늘어 계약 변경 | 긴 큐가 여럿을 덮을 때 직전 큐를 헛되이 확인한다 |
| B4 | VTT `&nbsp;` 엔티티가 리터럴로 남아 CPS를 부풀린다 | 인제스트 계층이라 범위 밖 | 해당 큐의 CPS가 과대 계산된다. 빈도 미상 |
| m4 | `cli.py`의 스트림 배관 161줄을 `console.py`로 | 동작에 문제가 없다 | 없음. **WP7 착수 시 후보** |
| M7~M9 | exit 2 메시지가 영어(typer 기본) · 파일 간 구분선 없음 | 표면 확장이거나 typer 기본값 | 낮음 |
| — | ENOSPC의 120이 CPython flush 실패와 같은 코드 | 구분할 수단이 없다 | 디스크 가득 참과 파이프 사고가 같은 코드로 보인다 |

## Known Issues — 다음 세션이 반드시 알아야 할 것

### 🔴 pysubs2는 음수를 **읽을 때 보존하고 쓸 때 클램프한다**

이번 세션에서 가장 값진 발견이고, **다른 라이브러리를 감쌀 때도 같은 질문을 해야 한다.**

| 방향 | 동작 |
| --- | --- |
| **읽기** | ASS·SAMI(`.smi`)·MPL2에서 음수를 **의도적으로 파싱**한다 (`substation.py`의 `# handle negative timestamps`, `mpl2.py` 정규식의 `(-?\d+)`, `sami.py`의 `int()`) |
| 읽기 (부호 무시) | SRT·VTT·MicroDVD·TMP는 부호 자리가 없어 앞의 `-`를 **조용히 무시**한다 |
| **쓰기** | **전부 클램프한다** (`substation.py`·`subrip.py`·`tmp.py`·`ttml.py`·`microdvd.py`) |

**그래서 "우리 도구로 왕복시켜 보니 괜찮더라"는 검증이 통과하고, 진짜 위험(외부 도구가
만든 파일)은 그 검증을 통째로 비켜 간다.** 라이브러리 경계는 read 경로와 write 경로를
따로 봐야 한다.

### 🔴 방법론 교훈 — 이 세션이 실제로 지불한 값

**① 거짓 전제를 정정하는 커밋이 같은 종류의 거짓 전제를 두 개 새로 들여왔다.**

`_format_timecode` 독스트링의 "음수는 상류가 이미 막는다"를 정정하면서,
그 자리를 메운 새 코드가 ⓐ "음수는 json으로만 표현할 수 있다" ⓑ "`minimal.srt`를
비롯한 픽스처가 `0`을 실제로 쓴다"를 새로 적었다. **둘 다 거짓이었고 둘 다 리뷰가
찾았다.** 교훈을 알고 있다는 것과 그 교훈에 걸리지 않는 것은 다르다.

**② 리뷰 2축이 독립적으로 같은 결함을 찾은 것이 축을 나눈 값어치다.**

`< 0` → `<= 0` 변이가 497건 전체 스위트를 통과하는 것을 **두 리뷰어가 각각** 발견했다.
한 명에게 전부 맡겼다면 "계획대로 구현됨"으로 승인됐을 것이다.

**③ 변이 실험은 무엇을 임포트하는지 먼저 확인해야 한다.**

리뷰어가 변이 13종을 사본에 심었는데 **전부 "생존"으로 나왔다.** 원인은 `.venv`의
`_editable_impl_cuesift.pth`(`C:\Users\aeby\vscode\cuesift\src`)가 사본이 아니라
**리포 원본을 임포트**시킨 것이었다. `PYTHONPATH`를 사본 `src`로 명시하자 12종이
killed로 뒤집혔다. 다음에 변이 실험을 하려면:

```bash
git archive HEAD | tar -x -C <사본>
PYTHONPATH=<사본>/src .venv/Scripts/python.exe -c "import cuesift; print(cuesift.__file__)"
```

**임포트 경로를 먼저 찍어 보라.** "무엇을 대상으로 통과했나"가 리뷰 절차 자체에서 발동했다.

### 🔴 측정 환경이 cp949 문제를 가린다 (이전 세션에서 계속)

**`PYTHONIOENCODING=utf-8`이 설정된 환경에서는 cp949 결함이 재현되지 않는다.**
바닐라 Windows를 재현하려면 `PYTHONIOENCODING=cp949`로 **명시**하거나 변수를 지운다.
이 저장소에서 exit 1은 "규격 위반 발견"이므로, 인코딩 실패가 exit 1을 내면
**위반 0건인 깨끗한 자막이 CI에서 실패로 읽힌다.**

이번 세션에서 추가한 출력 문자열(생략 고지 줄 · 음수 메시지 · `--limit` help)에는
em dash가 없다. 리뷰가 AST로 소스 전체 문자열 리터럴을 cp949 인코딩 시도해
**cp949 불가 리터럴은 전부 독스트링이고 출력 경로에 실리는 것은 0건**임을 확인했다.

### 🔴 `rich`는 호스트 플랫폼에 따라 다른 문자를 그린다 (이전 세션에서 계속)

**`rich`가 렌더한 stderr에 긴 문자열·경로를 통째로 단언하지 않는다.** 강제 개행 위치가
임시 디렉터리 경로 길이에 좌우돼 로컬과 CI에서 다른 곳에서 끊긴다.
정규화 헬퍼가 `tests/conftest.py`에 있다 — `strip_rich_decoration`·`normalize_rich_message`.

로컬에서 CI 렌더링을 재현하려면:

```python
import rich.console as rc
rc.detect_legacy_windows = lambda: False   # pytest 플러그인으로 -p 로 주입
```

### 문서 게이트 — 두 게이트의 파일 개수 대조가 유일한 탐지 수단

**링크 체커는 `git ls-files` 기준이라 미추적 파일을 건너뛴다.** 새 문서를 만들면
`git add` 후에 돌려야 검사된다. markdownlint의 `Linting: N files`와 대조하는 것이
누락을 잡는 유일한 방법이다 (이번 세션 **양쪽 19개 일치**).

앵커와 외부 URL은 **아무도 검사하지 않는다** — 절 번호를 바꾸면 조용히 깨진다.

### 이전 세션에서 이어지는 이슈 (여전히 유효)

- `logprobs`는 백엔드에 따라 조용히 사라진다(Ollama 미지원)
- 자가일관성 샘플링은 `n>1` 단일 호출이 아니라 **N회 개별 호출**이어야 이식성이 유지된다
- **`spec.overlap` 기여도 `+0.0%`는 아직 "측정하지 못했다"** — 벤치 하네스가 여전히
  겹침 0건인 합성 트랙을 쓴다
- **WP5가 알아야 할 것**: `subs` + `event_index`로 4포맷 라운드트립이 성립하지만
  **태그 보존은 안 된다** — `plaintext` setter가 텍스트를 통째로 대체한다
- `IngestError.reason`이 자유 `str`이다. 타입 체커가 없어 `Literal`은 장식이 된다

## Key Decisions Made

- **음수 타임코드는 규격 위반(1)이 아니라 파일 결함(66)이다.** 축은 "호출이 틀렸나,
  파일이 틀렸나"다. CPS·줄길이는 검수자가 그 큐의 텍스트를 고치면 되지만 음수 좌표는
  싱크·변환 파이프라인의 사고라 자막을 들여다봐도 고칠 수 없다
- **한 큐가 파일 전체를 죽이는 대가를 감수한다**(N3). 대안이 전부 더 나쁘다 —
  허용 임계값은 출처가 없어 §11 R8("출처 없는 수치를 기본값으로 넣지 않음")에 걸리고,
  해당 큐만 빼면 `검사 큐 N개` 헤더가 거짓이 되며, **0으로 클램프하는 것은 이번에
  폐기한 "그럴듯한 거짓"과 같아진다.** 역전 타임코드가 이미 같은 동작이라 일관되기도 하다
- **`_format_timecode`는 부호를 살린다.** 상류가 막아 도달 불가가 됐지만 클램프를
  되살리지 않는 것은 클램프가 **적극적으로 거짓을 만들기** 때문이다
- **`--limit` 기본값은 0(무제한)이다.** 상한을 기본으로 켜면 전체 목록을 파이프로 받아
  grep하던 쓰임이 조용히 잘린다
- **종료 코드는 `--limit`을 보지 않고 요약은 언제나 전체 기준이다.** 자른 뒤에 세면
  `--limit 3`이 "위반 3건"이라는, 종료 코드와 모순되지 않아 **사용자가 검증할 수 없는**
  거짓말을 낸다
- **절단은 `_format_report` 안에서 한다.** 호출부에서 `lines[:N]`으로 자르면 요약 줄까지
  함께 잘려 목적이 정확히 무너진다

결정의 전체 로그 12건은 [`check` 배선 설계 스펙](docs/superpowers/specs/2026-08-03-check-cli-design.md) §13에 있고,
태스크 분해와 실측 정정 기록은 [구현 계획](docs/superpowers/plans/2026-08-13-check-cli.md)에 있다.

### 이전 세션에서 이어지는 결정 (변경 없음)

초기 언어쌍 **ko→en/ja**(Q2) · 로컬 LLM은 **OpenAI 호환 엔드포인트로 일원화**(Q3) ·
규격 1차 출처는 **Netflix TTSG**(Q5) · LLM 연동은 **자체 얇은 어댑터**(Q6) ·
**`check`는 신호 엔진을 통과하지 않는다**(D3) · **심각도는 단일 등급**(등급 배정의
출처가 없다) · **가중치는 튜닝하지 않는다** · 인제스트는 **단일 진입점**.

## 컨트롤러가 겪은 것 — 절차 규율

- **리뷰어에게 도구가 없으면 보고 경로를 따로 못 박아야 한다.** `reviewer` 에이전트에
  `SendMessage`가 없어 "최종 응답이 곧 보고서"라고만 적었더니 **한 명이 보고 없이
  idle로 끝났다.** 재요청해서 받았다. 다음부터는 브리프에 `SendMessage`로 `main`에
  보내라고 명시한다
- **리뷰가 도는 동안 작업트리를 얼린다.** 이번엔 지켰다 — 두 리뷰어 모두 `fb0949d`
  고정 트리에서 재고 `git status --porcelain`이 비어 있음을 보고했다
- **브리프에 "확인했으나 문제 없던 것"을 요구하라.** 그것이 없으면 "발견 0건"이 참인지
  못 찾은 것인지 구분할 수 없다. 실제로 종료 코드 축이 곱집합 27조합·인자 파싱 16케이스를
  표로 내서 **누수 0건이 참임을 확인**할 수 있었다
- **`Bash` 도구는 Git Bash다.** 여러 줄 커밋 메시지는 heredoc(`git commit --file=- <<'MSG'`)을
  쓴다. PowerShell here-string(`@'…'@`)을 쓰면 메시지 앞뒤에 `@`가 남는다

## 게이트 실행 기록

전부 `.` 대상이다. **`src tests`로 좁히면 안 된다** — 그것이 CI 5회 연속 실패를 숨겼다.

| 게이트 | 결과 |
| --- | --- |
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `58 files already formatted` |
| `pytest --cov=cuesift` | **499 passed** · TOTAL 99% (`cli.py`·`ingest/loader.py`·`spec/check.py` 100%) |
| `scripts/check_links.py` | 마크다운 **19개** · 상대 링크 **69개** · 깨진 링크 **0** |
| `markdownlint-cli2` | **`Linting: 19 files`** · 0 issues |

**CI는 아직 돌지 않았다.** PR을 만들어야 한다.

## Files Modified This Session

```text
src/cuesift/ingest/loader.py    _require_non_negative_timecodes · 검사 순서 타입→부호→역전
src/cuesift/cli.py              _format_timecode 부호 보존 · _format_report의 limit·요약 이중 · --limit 옵션
tests/fixtures/ingest/starts_at_zero.srt        신규 — `0` 경계를 지나가는 유일한 픽스처
tests/fixtures/ingest/negative_timecode.ass     신규 — 음수 진입로가 json만이 아님을 고정
tests/test_ingest.py · test_ingest_fixtures.py · test_cli_check.py
README.md · CHANGELOG.md · docs/WBS.md · docs/superpowers/specs/2026-08-03-check-cli-design.md
```
