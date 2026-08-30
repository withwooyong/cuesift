# Session Handoff

> Last updated: 2026-08-30 (KST)
> **WP9(STT 어댑터)에 착수해 설계 스펙까지 냈다.** 코드는 한 줄도 쓰지 않았다 —
> 브레인스토밍으로 결정 10개를 확정하고 `docs/superpowers/specs/2026-08-30-stt-adapter-design.md`를
> 커밋한 것이 전부다. 다음 단계는 구현 계획이다.
> **브랜치 `feat/stt-adapter`는 푸시되지 않았고 PR도 없다.**
> 상태 값은 여기 적힌 숫자가 아니라 아래 "현재 상태 재는 법"의 명령으로 직접 재라.
> 진척은 [WBS](docs/WBS.md), FR 번호의 출처는 [요구사항정의서](docs/요구사항정의서.md)다

## Current Status

| 단계 | 상태 | 산출물 |
| --- | --- | --- |
| 직전 세션 인수인계 PR [#18](https://github.com/withwooyong/cuesift/pull/18) | ✅ 머지됨 | `48bf9aa` |
| 직전 세션 이월분 — README `--progress` 절 | ✅ **닫혔다** | PR [#19](https://github.com/withwooyong/cuesift/pull/19) · `3c46d93` |
| WP9 착수 조사 | ✅ | 아래 "이번 세션이 배운 것" ⓐ·ⓑ |
| WP9 설계 브레인스토밍 | ✅ | 결정 D1~D10 |
| **WP9 설계 스펙** | ✅ 커밋 | `034f963` · 247줄 |
| 사용자의 스펙 검토 | ⬜ **대기 중** | 세션이 여기서 끝났다 |
| 구현 계획 | ⬜ 미착수 | `docs/superpowers/plans/` |
| 구현 | ⬜ 미착수 | — |
| 푸시·PR | ⬜ **하지 않았다** | `feat/stt-adapter`는 로컬에만 있다 |

**FR 완료 개수는 움직이지 않았다 — 여전히 37이다.** 설계 문서는 FR을 닫지 않는다.

## 현재 상태 재는 법

```bash
git branch --show-current        # feat/stt-adapter
git status --short               # clean 이어야 한다
git log --oneline -3             # 최상단이 이 문서의 커밋, 그 아래가 034f963
git log --oneline main..HEAD     # 이 브랜치에만 있는 커밋
gh pr list --state open          # 열린 PR이 없어야 한다
```

## 이번 세션이 배운 것

### ⓐ `원문 검수 필요` 플래그는 문서 3곳에 있고 코드에는 0건이었다

```text
"원문 검수 필요"  →  docs/WBS.md:189
                     docs/요구사항정의서.md:238  (용어 정의)
                     docs/요구사항정의서.md:436  (FR-1.4)
                     src/  tests/  ................ 0건
```

**FR-7.3의 `Span` 사건과 같은 구조다.** 문서 여러 곳에 같은 문장이 있는 것은 독립 확인이
여러 번이라는 뜻이 아니라 한 번 쓰이고 복사됐다는 뜻이고, `CLAUDE.md`가 이미 그렇게 적어
두었다. 이 발견이 WP9의 범위를 "어댑터 하나"에서 **모델·인제스트·리포트 3층**으로 바꿨다.

**착수 조사에서 `grep`을 돌리지 않았다면 설계가 통째로 틀렸을 자리다.**

### ⓑ 인제스트 공개 API는 `load_segments`가 아니라 `load_subtitle`이다

브레인스토밍 중 사용자에게 던진 범위 질문에 **존재하지 않는 함수 이름**을 썼고, 코드를
읽고서야 정정했다. `IngestResult.subs`가 `pysubs2.SSAFile` **필수 필드**라는 것도 같은
자리에서 나왔고, 그것이 설계 D6(SSAFile 합성)의 존재 이유다.

기억으로 API를 부르면 **설계 문서가 존재하지 않는 함수를 가리킨 채 굳는다.**

### ⓒ 인수인계에 적힌 게이트 수치는 이미 낡아 있었다

직전 HANDOFF는 테스트 1547건을 적고 있었으나 실측은 **1582**였다. PR #16~#19가 그 사이에
올려 놓은 것이다. 스펙에 수치를 적기 전에 `pytest`를 직접 돌려 `collected`가 아니라
`passed`를 읽었다 — **`1582/1585 collected`와 `1582 passed`는 다른 문장이다.**

## 게이트 실행 기록

이 세션은 문서만 바꿨다. 코드 게이트는 **기준선 확인용**으로 돌렸고 값이 움직이지 않았다.

| 게이트 | 결과 |
| --- | --- |
| `pytest` | **1582 passed · 3 deselected** (코드 변경 없음) |
| `scripts/check_links.py` | 마크다운 **40개** · 상대 링크 **197개** · 깨진 링크 **0** |
| `npx markdownlint-cli2` | **Linting: 40 files** · **0 issues** |
| `ruff` | 코드 변경이 없어 돌리지 않았다 |

**두 문서 도구의 파일 개수가 40개로 일치한다.** 직전 세션의 39개에서 하나 늘었고 그것이
이번 스펙이다. 새 문서를 `git add`한 **뒤에** 링크 체커를 돌렸다 — 추적되기 전이었다면
링크 검사를 아예 받지 않고도 "깨진 링크 없음"으로 보였을 자리다.

## README/문서 갱신 필요 — **구현 PR에서 함께 고칠 것**

| 무엇 | 왜 낡았나 | 확인할 진실원 |
| --- | --- | --- |
| **WBS §189의 "Whisper 계열 어댑터"** | 설계 D1이 백엔드를 **OpenAI 호환 HTTP 어댑터**로 확정했다. `faster-whisper` 같은 파이썬 패키지를 넣지 않으므로 "Whisper 계열 어댑터"는 구현과 어긋난 서술이 된다 | [설계 스펙](docs/superpowers/specs/2026-08-30-stt-adapter-design.md) D1 · `docs/WBS.md:189` |

**지금 고치지 않은 이유는 순서다.** 구현이 없는 상태에서 WBS만 앞서 가면 "무엇이 되어
있는가"를 WBS가 잘못 말한다. 이 리포는 WBS 행에 커밋 해시와 함께 완료를 기록하는 관행이므로,
구현 PR이 그 행을 통째로 갱신할 때 문구도 같이 고친다.

**직전 세션의 이월분(README `--progress`)은 닫혔다.** PR #19가 절을 신설했고
`grep -c "progress|진행 표시" README.md`가 **10건**이다(이번 세션 실측). 두 세션 연속으로
이월됐던 항목이라 여기에 명시해 둔다.

## 확정된 설계 결정 — 스펙 전문을 읽기 전에 이것부터

| # | 결정 | 이것이 아니면 |
| --- | --- | --- |
| **D1** | OpenAI 호환 `/v1/audio/transcriptions`를 `httpx`로 호출 | 런타임 의존성 4개 고정 규율이 깨진다 |
| **D2** | 예외 계층은 `translate/provider.py`의 것을 재사용 | CLI가 `except`를 두 벌 갖고, 빠뜨린 쪽은 재시도도 폴백도 없이 샌다 |
| **D3** | 프로바이더는 `Transcript`를 내고 `Segment`는 인제스트가 만든다 | 프로바이더가 인제스트 정책(id·index·플래그)을 알게 된다 |
| **D4** | `verbose_json` 미지원은 `FatalProviderError`로 즉시 실패 | 전 세그먼트가 `0ms~0ms`가 되어 CPS 검사가 무의미해진다 |
| **D5** | 초 → 밀리초는 양쪽 다 `round()` | 한쪽만 내리고 한쪽만 올리면 원본에 없던 겹침을 우리가 만든다 |
| **D6** | `IngestResult.subs`를 합성한다 (`format="srt"`) | `\| None` 완화가 WP5 전역에 죽은 분기를 만든다 |
| **D7** | `Segment.source_from_stt: bool = False` 전용 필드 | `meta` 딕셔너리는 키 오타를 런타임에 못 막는다 |
| **D8** | **점수에도 hard fail에도 넣지 않는다** | 전량이 예산을 우회해 `review_ratio()`가 1.0이 되고 README 배수가 죽는다 |
| **D9** | 오디오 분할을 넣지 않는다 | 겹침 병합·오프셋 보정은 별도 작업 단위다 |
| **D10** | live 오디오는 `CUESIFT_LIVE_AUDIO`로 받는다 | 리포에 바이너리가 들어오고 어떤 게이트도 그것을 보지 않는다 |

**D8이 이 설계에서 가장 되돌리기 어려운 결정이다.** 스펙 §5가 세 갈래를 표로 비교해 두었다.

## 다음 작업

```mermaid
flowchart LR
    A["스펙 검토<br/>(사용자)"] --> B["구현 계획<br/>writing-plans"]
    B --> C["구현<br/>TDD"]
    C --> D["PR · CI · 머지"]
    D --> E["FR-8.3 transcribe 배선<br/>(WP6 마지막 조각)"]
    style A fill:#fef7e0,stroke:#f9ab00
    style B fill:#f1f3f4,stroke:#5f6368
    style E fill:#e8eaed,stroke:#5f6368
```

| 순위 | 작업 | 규모 |
| --- | --- | --- |
| **1** | 사용자의 스펙 검토를 받는다. 고칠 것이 있으면 반영한다 | S |
| **2** | 구현 계획을 `docs/superpowers/plans/2026-08-30-stt-adapter.md`에 쓴다 | M |
| **3** | 구현 — `stt/` 신규 · `Segment` 필드 · 인제스트 통합 · 리포트 2종 | L |
| **4** | FR-8.3(`transcribe` CLI 배선). **WP6의 마지막 조각이고 WP9가 아니다** | M |

### 구현에서 가장 먼저 확인할 것

**`write_subtitle`이 합성 `SSAFile`을 받는지 실행해서 확인하라.** 설계 D6은
`format="srt"`를 규약으로 정했지만 `ingest/writer.py`를 읽지 않은 채 정한 것이다.
스펙 §9 R3에 위험으로 적어 두었다 — **확인하지 않은 것을 확인했다고 적지 않는다.**

### 구현 시 반드시 실패시켜 볼 게이트

스펙 §8.1의 표가 여섯 개를 지정한다. 그중 하나가 다른 다섯보다 중요하다.

> **STT 입력에서 `review_ratio()`가 1.0이 아니다.**

플래그가 hard fail로 새면 이것이 조용히 1.0이 되고, README 최상단의 배수가 산출 불가가
된다. **버그 버전을 만들어 빨간 것을 본 뒤에 초록으로 만든다** — 이 리포에서 길이비 회귀
테스트가 버그 버전에서도 통과해 데이터를 다시 짠 전례가 있다.

## 파킹된 finding

| # | 무엇 | 왜 지금 안 했나 | 다시 열 조건 |
| --- | --- | --- | --- |
| 2 | **권장 모델 `qwen2.5:3b`가 3큐 중 2큐 실패** | 모델 품질 문제라 코드로 닫을 수 없다 | README 권장을 바꾸거나 프롬프트를 손볼 때. **WP9의 live 테스트에서 STT 모델을 고를 때 같은 종류의 판단이 필요하다** |
| 4 | **`COLUMNS=88` 아래에서 옵션 이름이 잘린다** | rich의 표 렌더링이라 범위 밖이다. 대신 폭 88을 게이트로 못 박았다 | 옵션을 더 붙여 88이 깨지는 날 |

## 남은 관측 하나 — FR-8.5의 R3 (승계)

설계 스펙 §9 R3(Windows 콘솔의 `\r`)은 **구조는 확인됐고 육안 관측이 남아 있다.**
`!`로 돌린 실행은 stderr가 파이프라 `plain` 경로를 탔다. 대화형 갱신은 **진짜 콘솔 창**이
있어야 한다.

```powershell
cd C:\Users\aeby\vscode\cuesift
.venv\Scripts\python.exe -m cuesift translate tests\fixtures\ingest\ten_cues.srt --to en --out . --base-url http://h/v1 --model m1 --progress
```

## 승계 항목 — 아무도 건드리지 않았다

| 항목 | 상태 |
| --- | --- |
| **Q4**(자가일관성 유사도 측정 수단) | 여전히 열려 있다. 판정은 벤치마크에 Tier 1을 태우는 별도 작업의 몫 |
| **FR-4.2**(역번역) | 구현 안 함. 문자 단위 유사도로는 `llm.retranslation_gap`이 **역방향으로 작동**한다 |
| **FR-1.5**(원문 언어 자동 감지) | STT 응답의 `language`를 기록만 한다. 자막 파일 입력에도 적용돼야 하는 요구라 STT 경로만 닫으면 반쪽이다 (스펙 §1.3) |
| **FR-8.3**(`transcribe`) | STT 어댑터(WP9)가 선행이지만 **FR-8.3 자신은 WP6에 남는다** — §5.8("CLI") 소속이다 |
| **`engine.py::_run_single`의 전역 index** | 확인됐고 안 고쳤다. `main`에 있다 |
| `segments[].reasons`의 순서 미검증 | NFR-3 재현성 문제. 열려 있다 |

## 개발 환경 메모 (승계)

**Python 실행은 반드시 `.venv/Scripts/python.exe`를 쓴다.** 시스템 Python은 3.14라 다르다.
게이트는 CI와 같은 대상 `.`으로 돌린다 — **`src tests`로 좁히면 안 된다**(그 차이로
CI가 5회 연속 실패한 전례가 있다).

**리포 루트에 `cuesift.yaml`을 만들지 마라.** 자동 탐색이 그것을 읽는다.
`conftest.py`의 autouse가 인프로세스 테스트는 막지만 **서브프로세스는 못 막는다**.

**변이 실험은 스크래치 사본에서 하고 `PYTHONPATH`를 강제한다.** editable install의 `.pth`가
사본을 가려 "생존" 오탐을 낸다. 리포에서 직접 할 때는 **파일 복사본으로 복원하라** —
`git checkout --`가 미커밋 작업을 날린 전례가 있다.

**콘솔에서 한글 출력이 깨져 보이는 것은 표시 문제이지 버그가 아니다.** 판정이 필요하면
파일로 받아 `read_bytes()` 후 utf-8·cp949 순으로 디코드해 읽는다 — 실행 로그가
**cp949로 떨어진다**(스모크 실측). 이번 세션에도 `scripts/check_links.py`와 삽입 스크립트의
콘솔 출력이 깨져 보였으나 파일 내용으로 결과를 확인했다.

**파이썬 스크립트로 문서를 고칠 때 `newline=""`을 준다.** `Path.write_text`의 기본값이
`newline=None`이라 Windows에서 `\n`을 `\r\n`으로 번역해 **2줄 수정이 1967줄 변경으로** 찍힌다.
이번 세션의 CHANGELOG 삽입은 `splitlines(keepends=True)`로 원본 줄바꿈을 보존해 피했다.

**긴 한글 문서는 heredoc이 아니라 `Write` 도구로 쓴다.** 여러 줄 커밋 메시지는
`git commit -F <파일>`로 넘긴다 — heredoc은 조용히 깨진다.

**Bash heredoc 안의 파이썬에 Windows 경로를 넣지 마라.** 역슬래시가 한 겹 먹혀
`tests\\fixtures`가 `tests\fixtures`(폼피드)로 바뀐다. 경로가 섞인 편집은 `Edit` 도구로 한다.

Ollama는 트레이 앱 겸 백그라운드 서비스로 자동 기동해 `127.0.0.1:11434`를 듣는다.
PATH에 없으면 `$env:LOCALAPPDATA\Programs\Ollama\ollama.exe`를 직접 부른다.

| 모델 | 크기 | 용도 |
| --- | --- | --- |
| `qwen2.5:3b` | 1.9GB | 번역·Tier 1 신호용. **실측: 3큐 중 2큐 실패**(파킹 2번) |
| `qwen2.5:1.5b` | 986MB | 폴백 관찰용. 번역기로는 못 쓴다(실측 5/15) |

**STT 백엔드는 아직 정하지 않았다.** WP9 live 테스트를 돌리려면 OpenAI 호환
`/v1/audio/transcriptions`를 내는 서버가 필요하고, **Ollama는 그것을 제공하지 않는다.**
`verbose_json`을 내는지가 관문이다(설계 D4) — 후보를 고를 때 그것부터 확인하라.

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

**첫 일은 이 브랜치가 어디까지 갔는지 확인하는 것이다.** 이번 세션은 푸시도 PR도 하지
않았으므로 `feat/stt-adapter`는 로컬에만 있다.

```bash
gh pr list --state open                  # 비어 있어야 한다
git branch --show-current                # feat/stt-adapter
git log --oneline main..HEAD             # 034f963 + 이 문서의 커밋
git status --short                       # clean

# 스펙을 읽고 구현 계획으로 넘어간다
cat docs/superpowers/specs/2026-08-30-stt-adapter-design.md
```

**`main`에 직접 푸시하지 않는다.** CI의 `push` 트리거가 `branches: [main]`뿐이라
직접 푸시하면 머지된 **뒤에야** CI가 돈다 — 게이트가 아니라 사후 통보다.
PR 절차는 [CLAUDE.md](CLAUDE.md)의 "PR 절차"에 있다.
