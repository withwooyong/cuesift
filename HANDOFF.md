# Session Handoff

> Last updated: 2026-07-27 19:59 (KST)
> Branch: `main`
> Latest commit: `852cb68` — markdownlint: .gitignore를 따르도록 설정

## Current Status

빈 폴더(마크다운 문서 3개)에서 시작해 **공개 저장소와 개발 골격을 갖춘 상태**다.
CI 3잡(`test 3.11`·`test 3.12`·`docs`)이 모두 통과하며, 작업트리는 깨끗하고 원격과 동기화돼 있다.
CLI는 인자 스키마만 확정된 **스텁 단계**로, 모든 서브커맨드가 종료 코드 `70`(미구현)을 반환한다.

저장소: <https://github.com/withwooyong/cuesift> (Public)

## Completed This Session

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | 프로젝트 골격 초기화 — 라이선스·패키징·CLI 스텁·테스트·CI | `66cf564` | 13개 파일 신규 |
| 2 | CI 액션을 Node 24 기반 최신 메이저로 상향 | `48d127c` | `.github/workflows/ci.yml` |
| 3 | 미결정 사항 Q2·Q3·Q6 해결 | `9e9ee83` | `요구사항정의서.md` |
| 4 | 미결정 사항 Q5 해결 + markdownlint 경고 604건 정리 | `7af39a7` | 문서 4개, `.markdownlint-cli2.jsonc` |
| 5 | CI에 markdownlint 잡 추가 | `7956a0a` | `.github/workflows/ci.yml` |
| 6 | markdownlint 검사 대상을 로컬/CI 간 일치 | `852cb68` | `.markdownlint-cli2.jsonc` |

## In Progress / Pending

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | **Q4 — 자가일관성 유사도 측정 수단** | 차단됨 | 벤치마크 데이터 없이는 결정 불가. 편집거리(무료) vs 임베딩 모델(추가 의존) 실측 필요 |
| 2 | TED2020에서 ko-en·ko-ja 병렬 코퍼스 확보 | 미착수 | Q4의 선행 조건. Q2가 ko→en/ja로 확정돼 범위가 정해짐 |
| 3 | `specs/ko.yaml`·`en.yaml`·`ja.yaml`·`ted.yaml` 생성 | 미착수 | **조사 불필요.** 요구사항정의서 §8.3.1에 값과 출처가 확정돼 있어 옮기기만 하면 됨 |
| 4 | CLI 스텁 3종(`translate`·`check`·`transcribe`) 구현 | 미착수 | 인자 스키마와 종료 코드 계약은 테스트 6건이 고정하고 있음 |

## Key Decisions Made

- **초기 대상 언어쌍은 ko→en/ja** (Q2) — 공개 병렬 코퍼스가 풍부해 Recall@Budget 측정 신뢰도를 먼저 확보한다. ko→th/vi/id는 방법론 검증 후 확장.
- **로컬 LLM은 OpenAI 호환 엔드포인트로 일원화** (Q3) — 전용 어댑터는 불필요하다. 다만 호환은 전송 규약에 한정되며 능력은 균일하지 않다(Ollama는 `logprobs`·`n` 미지원, vLLM은 `logprobs` 지원). 어댑터가 능력을 탐지하고 미지원 신호는 리포트에 명시해야 한다 — **무음 열화 금지**.
- **규격 프로파일 1차 출처는 Netflix Timed Text Style Guide** (Q5) — 공개 접근 가능하고 언어별로 분리돼 있어 프로파일 구조와 대응한다. BBC 가이드라인은 도메인이 크롤러를 차단하고 GitHub 미러도 없어 인용 불가.
- **벤치마크용 `specs/ted.yaml`을 분리** — TED2020 코퍼스는 TED 자체 기준(42자, 21 CPS)으로 제작돼, Netflix 프로파일로 검사하면 규격 위반이 대량 발생해 트리아지 성능 측정이 오염된다.
- **LLM 연동은 자체 얇은 어댑터** (Q6) — PySubtrans는 프로바이더 클라이언트가 자체 자막 도메인 모델에 결합돼 있어 클라이언트만 재사용할 수 없다. `Providers/Clients/CustomClient.py`는 구현 참고 자료로만 활용.
- **스타일가이드 인용은 수치와 URL만** — 가이드 본문(산문)은 저작물이므로 복제하지 않는다.
- **markdownlint 규칙 중 MD013·MD036은 끄고 이유를 설정 파일에 주석으로 남김** — 회피가 아니라 문서 관례를 명시하는 것. 규칙을 끄지 않으면 다음 사람이 같은 604건을 다시 마주한다.

## Known Issues

- **`logprobs`를 위험 신호로 쓰면 백엔드에 따라 조용히 사라진다.** Ollama는 미지원, vLLM은 지원. 능력 탐지와 리포트 명시가 구현될 때까지 이 신호를 도입하면 안 된다.
- **자가일관성 샘플링은 `n>1` 단일 호출로 구현하면 안 된다.** Ollama가 `n`을 지원하지 않으므로 N회 개별 호출이어야 이식성이 유지된다. (Q4 비고에 기록됨)
- **로컬 Python이 3.14.6이라 STT·QE 확장 설치가 막힐 수 있다.** `requires-python`은 `>=3.11`이지만 CI가 검증하는 범위는 3.11/3.12다. `torch` 계열 휠이 3.14에 없을 가능성이 높으므로, `[stt]`·`[qe]` 작업 시 3.11 또는 3.12 가상환경을 별도로 만들 것.
- **요구사항정의서 §12에 Q4만 남아 있다.** 나머지 5개는 해결 표시됨.

## Context for Next Session

**사용자의 원래 의도** — "본격적인 작업을 시작하려고" 저장소를 만들어 달라는 것이었다. 즉 이 세션의 산출물은 최종 결과물이 아니라 **개발을 시작할 수 있는 상태**다.

**선택한 접근과 이유** — 골격을 만들 때 기능을 구현하는 대신 **인터페이스 계약을 먼저 고정**했다. CLI 서브커맨드는 요구사항정의서 §8.1의 인자 스키마를 그대로 반영한 스텁이고, 종료 코드 `70`(미구현)을 반환한다. 검수 실패를 뜻하는 `1`, 사용법 오류인 `2`와 구분되므로 CI가 세 상태를 혼동하지 않는다. 테스트 6건이 이 계약을 지키므로, 구현을 채워 넣으며 스텁을 하나씩 실제 동작으로 바꾸면 된다.

**사용자가 표현한 제약과 선호**

- **푸시는 매번 명시적 요청이 있을 때만 실행한다.** 커밋과 푸시를 한 명령에 묶지 않는다 (전역 CLAUDE.md 규칙). 이 세션에서도 커밋 후 매번 확인을 받고 푸시했다.
- 커밋 메시지는 한글로 작성한다.
- 미결정 사항을 닫을 때 **조사로 닫을 수 있는 것과 판단이 필요한 것을 구분**하는 방식을 선호했다. Q3·Q6은 "조사해서 닫는다"로 위임했고, Q2는 직접 결정했다.

**반복해서 부딪힌 실패 유형 — 다음 세션에서도 주의할 것**

이 세션에서 서로 다른 작업인데 **같은 유형의 문제가 네 번** 나왔다. 전부 "성공처럼 보이는 무동작"이다.

1. markdownlint 첫 실행이 `0 issues`로 통과 → 실은 `Linting: 0 files`(미실행)
2. `tee` 파이프가 lint 실패의 종료 코드를 삼킴 → 로그엔 에러, 결과는 초록불
3. `logprobs` 미지원 백엔드에서 위험 신호가 사라짐 → 품질 저하인데 에러 없음
4. 로컬 5 files vs CI 4 files → 초록불인데 검사 대상이 다름

따라서 검증할 때 "통과했나"가 아니라 **"무엇을 대상으로 통과했나"** 를 확인해야 한다. 초록불 옆의 숫자(검사한 파일 수, 애노테이션 개수, 잡 개수)를 봐야 한다. 특히 제품의 핵심 기능인 `cuesift check` CI 게이트를 구현할 때 이 기준을 그대로 적용할 것 — **검사하지 않고 통과하는 게이트는 없는 게이트보다 나쁘다.**

**2차 출처를 믿지 말 것** — Q5 조사에서 여러 블로그가 일제히 Netflix 영어 CPS를 "17/13"으로 적고 있었으나, 원문은 "20/17"이었다. 옛 스펙이 복사되어 돌고 있었다. 규격 수치는 반드시 1차 출처에서 확인한다.

## Files Modified This Session

세션 시작 시점에는 마크다운 문서 3개뿐이었고, 현재 커밋된 파일은 다음과 같다.

```text
.github/workflows/ci.yml       CI — test(3.11/3.12) + docs 잡
.gitattributes                 LF 고정 (Windows 개발 / Linux CI)
.gitignore                     저작권 자막·영상·모델 가중치 차단
.markdownlint-cli2.jsonc       마크다운 관례 명시
CHANGELOG.md                   (이번 handoff에서 생성)
HANDOFF.md                     (이번 handoff에서 생성)
LICENSE / NOTICE               Apache-2.0
README.md
pyproject.toml                 Typer·hatchling. stt/qe는 선택 의존성
src/cuesift/__init__.py
src/cuesift/cli.py             translate·check·transcribe 스텁
tests/test_cli.py              인자 스키마·종료 코드 계약 6건
docs/AI_자막검수_오픈소스_비교.md   (기존)
docs/번역관리_TMS_솔루션_비교.md    (기존)
docs/요구사항정의서.md              (문서 버전 0.3)
```
