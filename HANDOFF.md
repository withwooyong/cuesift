# Session Handoff

> Last updated: 2026-08-29 (KST)
> **브랜치 `fix/cache-discard-invalid`에 파킹 #13(캐시가 실패 응답을 보존한다) 구현이
> 끝나 있다. 게이트 전부 통과 · 작업트리 clean · 아직 푸시하지 않았다.**
> 커밋 수는 `git rev-list --count main..HEAD`로 센다 — 여기 숫자를 적으면
> 그 문장을 고치는 커밋이 자기 자신을 틀리게 만든다(실측으로 한 번 겪었다).
> **브랜치가 `origin`에 있는지, PR이 있는지, CI가 돌았는지는 아래 "배포 절차"의 명령으로 직접 재라.**
> 값이 아니라 명령이다.
> 진척은 [WBS](docs/WBS.md), FR 번호의 출처는 [요구사항정의서](docs/요구사항정의서.md)다

## Current Status

**파킹 #13이 닫혔다.** FR 개수는 움직이지 않는다 — FR-2.7(재개)의 **동작 수정**이고
새 요구를 구현한 것이 아니다.

| 단계 | 상태 | 산출물 |
| --- | --- | --- |
| 착수 조사 | ✅ | 파킹 노트 **한 줄이 거짓**임을 코드로 확인(아래 ⓐ) |
| [구현 계획](docs/superpowers/plans/2026-08-29-cache-discard-invalid.md) | ✅ 커밋 `47087dd` | 태스크 3개 · 게이트 수치 고정 |
| 구현 (T1·T2) | ✅ `a198b4c`·`1fa80b6` | 캐시 폐기 표면 · engine 배선 |
| 문서 (T3) | ✅ `d11864a` | 종료 코드 3의 근거 정정 · README · CHANGELOG |
| 변이 증명 | ✅ 8종 | store 3종 · engine 5종, 전부 사망 확인 |
| 런타임 스모크 | ✅ | 스텁 서버로 재현이 닫힌 것을 실물 확인 |
| 푸시 · PR · CI | ⬜ **아래 "배포 절차"로 직접 재라** | — |

**`main` 머지는 사용자 승인 항목이다.**

## 무엇이 바뀌었나

| 부분 | 무엇 |
| --- | --- |
| `store/cache.py` | `_entry_path()`(키→경로 규칙의 단일 출처) · `discard()` 신설. `load`·`store`가 같은 헬퍼를 쓴다 |
| `store/provider.py` | `CachingProvider.discard()`(공개) · `_discard_or_warn()` · `_request()`(조회·저장·폐기가 **같은 키**를 보게 하는 단일 출처) |
| `translate/engine.py` | `InvalidResponseError` 분기 **2곳**에서 폐기를 시킨다 · `_MAX_TOKENS` 상수화 |
| 사용자에게 보이는 변화 | 파싱 실패한 배치는 **재실행 시 실제로 다시 호출된다**. 성공분·빈 번역은 캐시에 남는다 |

### 범위의 못 — 사유 3종 중 하나만 뺀다

| 사유 | 캐시되나 | 근거 |
| --- | --- | --- |
| `provider_error` | ❌ (전부터) | 예외가 저장 코드에 도달하지 못한다. **구조**로 보장됨 |
| `invalid_response` | ❌ **(이번 작업)** | 모델이 계약을 어긴 응답. 재호출이 실제로 성공할 수 있다 |
| `empty_translation` | ✅ **유지** | 개수도 번호도 맞은 **계약을 지킨 응답**이다. 폐기하면 같은 배치의 성공분까지 재결제한다 |

## 이번 세션이 배운 것

### ⓐ 파킹 노트가 "무엇이 안 되는가"를 실제보다 넓게 적었다

직전 세션의 ⓐ와 **반대 방향의 같은 결함**이다 — 그때는 조건을 좁게 적어 우선순위를
낮게 보이게 했고, 이번엔 넓게 적어 피해를 크게 보이게 했다.

| 노트가 적은 것 | 실측 |
| --- | --- |
| "**모델을 바꿔도** 캐시를 지우기 전까지 같은 실패가 영구 재생된다" | **거짓.** 캐시 키에 `identity`가 들어가고 `identity = base_url\|model`이다(`translate/openai_compat.py:129`) — 모델 교체는 이미 캐시를 우회했다 |
| "`complete()`가 성공·실패를 가리지 않고 저장한다" | 참 |
| "`provider_error`는 저장되지 않는다" | 참. 조건문이 아니라 **구조**로 그렇다 |

실제 피해는 좁았다: *같은 모델·같은 설정으로 다시 돌릴 때*, 즉 **"그냥 한 번 더 돌려본다"**
가 무력화되는 것. 모델·엔드포인트·프롬프트·용어집·온도를 바꾸는 복구 경로는 전부
키가 달라져 이미 미스가 났다. **범위를 좁히고 나서야 `empty_translation`을 남기는 결정이
가능해졌다** — "전부 망가졌다"로 읽었으면 전부 지웠을 것이다.

### ⓑ 한 숫자가 셋을 가르는 게이트를 만들 수 있다

세그먼트 2개·`batch_size=2`로 두 번 돌렸을 때 **2회차의 실제 호출 수**가 게이트다.

| 시나리오 | 기대 | 폐기 없음 | 배치 호출부만 빠짐 | 개별 호출부만 빠짐 |
| --- | --- | --- | --- | --- |
| 전부 파싱 실패 | **3** | 0 | 0 | 1 |
| 배치만 실패, 개별 폴백 성공 | **1** | 0 | 0 | 1 |
| 빈 번역(형식은 맞음) | **0** | 0 | 0 | 0 |

세 열의 값이 전부 달라 **표 자체가 변이 증명**이 된다. "캐시가 비었다"를 파일로 확인하는
방식을 택하지 않은 이유는 그러면 테스트가 경로 규칙을 재구현하게 되고, 그 재구현이
틀려도 통과하기 때문이다.

### ⓒ 실패 방향이 "오늘과 같음"인 설계를 골랐다

지연 커밋(`accept()`)이 이론상 더 정확하다 — 쓰레기가 애초에 안 써진다. 버린 이유는
**모든 호출자가 opt-in해야 하기 때문**이다. Tier 1 자가일관성은 파싱 검증이 없어
`accept()`를 부를 자리가 없고, 안 부르면 **Tier 1 캐시가 조용히 전부 꺼진다.**

명시적 폐기(`discard()`)는 반대다. 호출부가 빠뜨리면 **옛날 동작**(쓸모없는 응답이 캐시에
남음)이고 회귀가 아니다. 이 저장소가 반복해 겪은 결함은 전부 "조용한 열화" 쪽이었다.

### ⓓ 수정이 다른 문서의 근거를 거짓으로 만들 수 있다

종료 코드 `3`을 고른 근거가 **바로 이 결함이었다.** `cli.py` 머리말과 README가
*"재실행하면 캐시 히트 3 · 실제 호출 0"* 이라는 실측을 인용하고 있었고, 이 수정으로
`invalid_response`에 대해 거짓이 됐다.

**값은 바꾸지 않았다.** 75(`EX_TEMPFAIL`)를 거부하는 이유가 남기 때문이다 — 사유 3종의
처방이 서로 달라(`provider_error`는 재실행 · `invalid_response`는 모델 교체 ·
`empty_translation`은 원문 확인) 코드 하나가 "재시도"를 대표할 수 없고,
`empty_translation`은 여전히 캐시가 보존한다. **바꾼 것은 근거 문장이다.**

`git grep`으로 인용처를 먼저 찾지 않았으면 저장소의 종료 코드 계약 출처가 거짓말을
계속했을 것이다. **결함을 고칠 때 그 결함을 근거로 쓴 문서를 찾는다.**

## 배포 절차 — **"푸시했다"와 "CI가 돌았다"는 다르다**

```bash
git rev-list --count main..HEAD          # 이 브랜치의 커밋 수
git ls-remote --heads origin fix/cache-discard-invalid   # 비면 아직 푸시 안 됨
gh pr list --head fix/cache-discard-invalid              # 비면 PR 없음
gh pr checks --watch                                     # CI 통과 대기
```

**`main`에 직접 푸시하지 않는다.** CI의 `push` 트리거가 `branches: [main]`뿐이라
직접 푸시하면 머지된 **뒤에야** CI가 돈다 — 게이트가 아니라 사후 통보다.

## 게이트 실행 기록

| 게이트 | 이 세션 | 비고 |
| --- | --- | --- |
| `python -m compileall src tests` | 통과 | |
| `ruff check .` | **All checks passed!** | 대상은 `.` — `src tests`로 좁히지 않는다 |
| `ruff format --check .` | **115 files already formatted** | 신규 테스트 1개로 114 → 115 |
| `pytest --cov=cuesift` | **1582 passed · 3 deselected** · 커버리지 **99%**(2505문 중 31 미도달) | 착수 기준선 1571 → **+11** |
| 런타임 스모크 | 아래 표 | 스텁 서버는 **리포 밖**에 두었다 |
| `python scripts/check_links.py` | 마크다운 **39개** · 상대 링크 **189개** · 깨진 링크 **0** | 계획서 추가로 38 → 39 |
| `npx markdownlint-cli2` | **39 files** · **0 issues** | 두 도구의 파일 수가 **일치**한다 |

**CI는 1건 적게 센다.** `data/`가 `.gitignore`에 있어 깨끗한 체크아웃에는 벤치 트랙이 없고
`tests/test_bench_glossary.py`가 1건을 skip한다 — CI의 기대값은
**1581 passed · 1 skipped · 3 deselected**다.

### 런타임 스모크 (스텁이 200 + 잡문만 냄, 10큐)

10큐는 배치 1회 + 개별 폴백 10회 = 호출 11회다.

| 실행 | 결과 |
| --- | --- |
| 1회차 | `exit 3` · **캐시 히트 0 · 실제 호출 11** · `invalid_response 10건` |
| 2회차 (같은 `--cache-dir`) | `exit 3` · **캐시 히트 0 · 실제 호출 11** · `invalid_response 10건` |
| 캐시 디렉터리 | 실행 후 **파일 0개** — 11개가 저장됐다가 전부 폐기됐다 |
| 3회차 (서버를 죽이고) | `exit 3` · **캐시 히트 0 · 실제 호출 4** · **`provider_error` 10건** |

**2회차의 "실제 호출 11"이 재현이 닫힌 증거다** — 폐기 전에는 `캐시 히트 11 · 실제 호출 0`
이었다. 3회차는 사유가 `invalid_response`에서 `provider_error`로 **바뀌는 것**을 보인다.

### 변이 증명

| 변이 | 사망 |
| --- | --- |
| `cache.discard` 본문을 `pass`로 | **3건** |
| `CachingProvider.discard`가 `temperature`를 하드코딩 | **1건** |
| `_discard_or_warn`의 `except`를 무력화 | **1건** |
| `_discard_cached` 본문을 `return`으로 | **2건** |
| `_run_window`의 호출만 제거 | **2건** |
| `_run_single`의 호출만 제거 | **1건** |
| `_run_window`가 `empty_translation`에도 폐기를 걸게 확대 | **1건** (범위의 못이 진짜 게이트다) |
| `_MAX_TOKENS`를 `_discard_cached`에서만 `16`으로 | **2건** |

**마지막 셋이 서로 다른 것을 지킨다** — 호출부 하나만 빠지는 경우, 범위가 넓어지는 경우,
키가 어긋나는 경우가 각각 다른 테스트로 잡힌다.

## README/문서 갱신 필요 — **이번 세션에서 고치지 않았다**

| 무엇 | 왜 낡았나 | 확인할 진실원 |
| --- | --- | --- |
| **README에 `--progress`·`CUESIFT_PROGRESS`가 한 글자도 없다** | FR-8.5(직전 세션, `a6ecb4a`)가 기능을 넣었는데 대외 문서에 반영되지 않았다. 표에 행 하나 더하는 일이 아니라 **절 신설**이다 | `src/cuesift/cli.py:1504`(`_prefer_env_bool(..., "CUESIFT_PROGRESS")`) · `src/cuesift/progress.py` · `docs/superpowers/specs/2026-08-29-progress-display-design.md` |

세션 끝(컨텍스트가 가장 얕은 시점)에 기능 설명을 쓰면 "그럴듯하지만 틀린 README"가 된다.
**온전한 컨텍스트를 가진 다음 세션이 하는 편이 낫다.**

## 다음 작업

**1순위는 FR-8.3(`transcribe`)이다** — WP6의 마지막 조각이고 STT 어댑터(WP9)가 선행이다.

**파킹 2번(README 권장 모델 `qwen2.5:3b`가 3큐 중 2큐 실패)은 여전히 열려 있다.**
같은 뿌리로 #13과 묶여 있었지만 같은 것이 아니다 — #13은 "실패를 캐시가 굳힌다"였고
2번은 **"권장 모델이 애초에 실패한다"**다. 폐기가 재실행을 가능하게 만들었을 뿐,
같은 모델이 같은 지시를 다시 어기는 문제는 그대로다. **이제는 재실행이 실제 호출이 되므로
그 문제를 실측하기가 쉬워졌다.**

## 파킹된 finding

**#1·#2·#3·#13이 닫혔다.** 앞의 셋은 한 덩어리("실패했는데 왜인지 안 말한다")였고,
`fix/cache-discard-invalid`가 #13을 냈다.

| # | 무엇 | 왜 지금 안 했나 | 다시 열 조건 |
| --- | --- | --- | --- |
| 2 | **권장 모델 `qwen2.5:3b`가 3큐 중 2큐 실패** | 모델 품질 문제라 코드로 닫을 수 없다 | README 권장을 바꾸거나 프롬프트를 손볼 때 |
| 4 | **`COLUMNS=88` 아래에서 옵션 이름이 잘린다** | rich의 표 렌더링이라 범위 밖이다. 대신 폭 88을 게이트로 못 박았다 | 옵션을 더 붙여 88이 깨지는 날 |
| 5 | 화면 토큰(번역만) vs `review.json` `cost`(합계)가 **다른 객체** | 화면에 합계 줄을 넣을지는 설계 결정이다 | 차이를 설명할지 없앨지 정하는 일 |
| 6 | `inf` 메시지가 원인을 틀리게 말한다 | 문구 문제, 작지만 별건 | 조합 검증은 **7규칙 / 8종 문자열**이다 |
| 7 | `cli.py`의 줄번호 인용이 조용히 낡을 수 있다 | 이 저장소 `src/`에 같은 형태가 **15건**이라 관행이다 | 관행 전체를 바꾸는 작업 |
| 8 | 종료 코드 69 단축이 **Tier 1 실패에도** 걸린다 | 단축은 `EXIT_UNAVAILABLE`에서만 걸리고 건너뛴 언어를 stderr로 명시한다 | 언어별로 다른 프로바이더를 쓸 수 있게 되는 날 |
| 9 | **`default_map` 값의 타입 변환에 게이트가 없다** | 게이트를 만들면 click 내부 의존이 한 겹 더 는다 | `Path` 전용 메서드를 쓰게 되는 날 |
| 11 | **`output.progress`에 스칼라가 아닌 YAML을 주면 트레이스백 + `exit 1`** | **기존 결함 부류의 세 번째 사례다** — `dry_run: [1]`·`signals.tier1.enabled: [1]`이 똑같이 행동한다 | 설정 파일 값 검증을 **부류 전체**로 손보는 작업 |
| 12 | **닫힌 파이프 + 실제 번역 + 진행 켬** 조합이 미검증 | `_TolerantOutput`이 한 층 아래에서 `EPIPE`를 삼켜 위험이 낮다 | 파이프 계약을 다시 손볼 때 |

## 남은 관측 하나 — FR-8.5의 R3

설계 스펙 §9 R3(Windows 콘솔의 `\r`)은 **구조는 확인됐고 육안 관측이 남아 있다.**
`!`로 돌린 실행은 stderr가 파이프라 `plain` 경로를 탔다(그것대로 "비대화형에서 `\r`이 새지
않는다"는 계약은 확인됐다). 대화형 갱신은 **진짜 콘솔 창**이 있어야 한다.

```powershell
cd C:\Users\aeby\vscode\cuesift
.venv\Scripts\python.exe -m cuesift translate tests\fixtures\ingest\ten_cues.srt --to en --out . --base-url http://h/v1 --model m1 --progress
```

**확인하지 않은 것을 확인했다고 적지 않는다.**

## 승계 항목 — 이 브랜치가 건드리지 않았다

| 항목 | 상태 |
| --- | --- |
| **Q4**(자가일관성 유사도 측정 수단) | 여전히 열려 있다. 판정은 벤치마크에 Tier 1을 태우는 별도 작업의 몫 |
| **FR-4.2**(역번역) | 구현 안 함. 문자 단위 유사도로는 `llm.retranslation_gap`이 **역방향으로 작동**한다 |
| **FR-8.3**(`transcribe`) | STT 어댑터(WP9)가 선행이지만 **FR-8.3 자신은 WP6에 남는다** — §5.8("CLI") 소속이다. **그래서 WP6은 🟡다**(5개 중 4개) |
| **`engine.py::_run_single`의 전역 index** | 확인됐고 안 고쳤다. `main`에도 있다 |
| `segments[].reasons`의 순서 미검증 | NFR-3 재현성 문제. 열려 있다 |

## 개발 환경 메모 (승계)

**Python 실행은 반드시 `.venv/Scripts/python.exe`를 쓴다.** 시스템 Python은 3.14라 다르다.
게이트는 CI와 같은 대상 `.`으로 돌린다 — **`src tests`로 좁히면 안 된다**(그 차이로
CI가 5회 연속 실패한 전례가 있다).

**리포 루트에 `cuesift.yaml`을 만들지 마라.** 자동 탐색이 그것을 읽는다.
`conftest.py`의 autouse가 인프로세스 테스트는 막지만 **서브프로세스는 못 막는다**.

**변이 실험은 스크래치 사본에서 하고 `PYTHONPATH`를 강제한다.** editable install의 `.pth`가
사본을 가려 "생존" 오탐을 낸다. 리포에서 직접 할 때는 **파일 복사본으로 복원하라** —
`git checkout --`가 미커밋 작업을 날린 전례가 있다(이번 세션도 변이 8종을 전부
`cp <파일>.bak` 복원으로 돌렸다).

**콘솔에서 한글 출력이 깨져 보이는 것은 표시 문제이지 버그가 아니다.** 판정이 필요하면
파일로 받아 `read_bytes()` 후 utf-8·cp949 순으로 디코드해 읽는다 — 실행 로그가
**cp949로 떨어진다**(이번 세션 스모크 실측).

**파이썬 스크립트로 문서를 고칠 때 `newline=""`을 준다.** `Path.write_text`의 기본값이
`newline=None`이라 Windows에서 `\n`을 `\r\n`으로 번역해 **2줄 수정이 1967줄 변경으로** 찍힌다.

**Bash heredoc 안의 파이썬에 Windows 경로를 넣지 마라.** 역슬래시가 한 겹 먹혀
`tests\\fixtures`가 `tests\fixtures`(폼피드)로 바뀐다 — 이번 세션에 문자열 치환이
그것으로 실패했다. 경로가 섞인 편집은 `Edit` 도구로 한다.

Ollama는 트레이 앱 겸 백그라운드 서비스로 자동 기동해 `127.0.0.1:11434`를 듣는다.
PATH에 없으면 `$env:LOCALAPPDATA\Programs\Ollama\ollama.exe`를 직접 부른다.

| 모델 | 크기 | 용도 |
| --- | --- | --- |
| `qwen2.5:3b` | 1.9GB | 번역·Tier 1 신호용. **실측: 3큐 중 2큐 실패**(파킹 2번) |
| `qwen2.5:1.5b` | 986MB | 폴백 관찰용. 번역기로는 못 쓴다(실측 5/15) |

**결정론이 필요하면 스텁 서버를 쓴다.** OpenAI 호환 `/v1/chat/completions`에
`{"translations":[{"id":N,"text":…}]}`를 돌려주면 되고, 지시를 어긴 응답을 만들려면
아무 문자열이나 `choices[0].message.content`에 담으면 된다.
**리포 밖에 두어야 게이트를 오염시키지 않는다.**

live 실행 명령:

```powershell
$env:CUESIFT_LIVE_BASE_URL="http://localhost:11434/v1"
$env:CUESIFT_LIVE_MODEL="qwen2.5:3b"
.venv/Scripts/python.exe -m pytest -m live -v -s
```

## 다음 세션 시작 절차

```bash
git checkout fix/cache-discard-invalid
git log --oneline | head -5
git status --short                                       # clean이어야 한다
git ls-remote --heads origin fix/cache-discard-invalid   # 푸시됐나
gh pr list --head fix/cache-discard-invalid              # PR이 있나
```

**푸시·PR이 아직이면 그것부터다.** 이 저장소에서 PR은 리뷰가 아니라 **게이트**다 —
`test 3.11`~`3.14`·`docs`가 다른 OS·다른 환경변수에서 도는 것을 머지 **전에** 확인한다.
