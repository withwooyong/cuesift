# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/).

## [Unreleased]

### Added

- `scripts/check_links.py` — 마크다운 상대 링크가 실제 파일을 가리키는지 검사한다. 표준 라이브러리만 사용하며 대상은 `git ls-files` 기준(markdownlint의 `gitignore: true`와 동일 집합). 외부 URL과 앵커는 검사하지 않는다 — 외부 URL은 남의 서버 사정으로 CI가 간헐 실패하면 게이트가 무시되고, 앵커 생성 규칙은 렌더러마다 달라 한국어 제목에서 갈린다
- CI `docs` 잡에 링크 검사 스텝 추가 — 검사 대상 0개면 통과가 아니라 실패로 처리하고, 깨진 링크는 `::error file=…,line=…::` 애노테이션으로 표시한다

### Changed

- (없음)

### Fixed

- `docs/번역관리_TMS_솔루션_비교.md:5`의 깨진 링크 처리 — `글로벌_OTT_플랫폼.md`는 이 저장소에 존재한 적이 없다. 링크에서 일반 텍스트로 강등하고 외부 문서임을 명시했다

### Removed

- (없음)

---

## [2026-07-28] 문서 구조 정리 · 용어 풀이 · 조사 질문 동기화

### Added

- `docs/AI_자막검수_오픈소스_비교.md` §0 용어 풀이 — QE·트리아지 두 축 심층 설명 + 용어 28개(음성·자막 생성 5 / 품질 측정 9 / 자막 규격·포맷 10 / 파이프라인 운영 4). 각 그룹을 §2의 계층(C·D·E)에 대응시켰다 (`cc8f109`)
- `docs/요구사항정의서.md` §0 용어 풀이 — 문서 표기 규약(`FR-`·`NFR-`·`必`·`Qn`·`★` 상자), 척추 4개념(세그먼트→신호→위험도→트리아지), 고유 용어 14개, 측정 용어 6개, 공통 용어 포인터 9개. 각 용어에 근거 FR·Q 번호를 병기해 요구사항 변경 시 역추적되게 했다 (`cc8f109`)

### Changed

- 조사 문서 3종을 `docs/`로 이동 — 최상위를 README·CHANGELOG·HANDOFF·라이선스로 정리. 세 문서는 형제 상대경로로 상호 참조하므로 함께 옮겨 링크가 유지된다. README·HANDOFF의 유입 링크만 수정 (`5ffda8d`)
- 용어 정의를 단일 출처로 통일 — 공통 용어(QE·MQM·CPS·화자분리·자막 포맷)는 AI 문서 §0에만 두고, 요구사항정의서 §0.5는 한 줄 요약과 포인터만 갖는다. 같은 용어를 두 문서에서 각각 정의하면 한쪽만 고쳐져 갈라진다 (`cc8f109`)
- 검증 수준 표기에 색상 이름 명시 — `docs/AI_자막검수_오픈소스_비교.md` 최상단의 🟢 → 🟢 **초록**, 🟡 → 🟡 **노랑** (`cc8f109`)
- 문서 버전 0.3 → 0.4 (`cc8f109`)
- `docs/AI_자막검수_오픈소스_비교.md` §5 조사 질문 6개에 상태와 근거를 부여 — #3은 ✅ 닫힘(Q6), #1·#4·#5는 ⏸ 연기(Q2·`FR-1.6`·`FR-5.5` = v0.3), #6은 ⚪ 불필요(§4에서 채택 후보 아님), #2는 🔬 측정 필요(Q4와 동일 실험)로 재분류. 상태 기호는 문서 전반의 검증 수준 🟢·🟡과 의미가 달라 재사용하지 않고 범례에 명시 (`11dbfe4`)
- §5 한계 목록의 각 항목에 대응처를 명시 — 모델 크기 → `NFR-1`, 평가 데이터 → §9.2, 샷 체인지 → `FR-5.5`(v0.3) (`11dbfe4`)

### Fixed

- §5 한계 항목의 "CometKiwi·xCOMET의 저자원 언어(th·vi·id) 성능이 **최우선 검증 대상**" 서술 정정 — Q2가 초기 언어쌍을 ko→en/ja로 확정한 이후 사실이 아니다. 조사 문서가 요구사항 결정 이전 상태로 굳어 있던 사례 (`11dbfe4`)

---

## [2026-07-27] 저장소 구축 및 미결정 사항 정리

### Added

- 프로젝트 골격 초기화 — Apache-2.0 라이선스·NOTICE, `pyproject.toml`(Typer·hatchling·src 레이아웃), `src/cuesift/cli.py`, 테스트 6건, `.gitignore`, `.gitattributes` (`66cf564`)
- GitHub Actions CI — Python 3.11/3.12 매트릭스에서 ruff·pytest 실행 (`66cf564`)
- CI에 `docs` 잡 추가 — markdownlint 게이트. 버전 고정, `set -eo pipefail`, `Linting: 0 files` 가드 포함 (`7956a0a`)
- `.markdownlint-cli2.jsonc` — 프로젝트 마크다운 관례를 명시 (`7af39a7`)
- 요구사항정의서 §8.3.1 — ko·en·ja 규격 프로파일 기본값과 1차 출처 (`7af39a7`)

### Changed

- CI 액션을 Node 24 기반 최신 메이저로 상향 — `actions/checkout` v4 → v7, `actions/setup-python` v5 → v7 (`48d127c`)
- 미결정 사항 Q2 해결 — 초기 대상 언어쌍을 ko→en/ja로 확정 (`9e9ee83`)
- 미결정 사항 Q3 해결 — 로컬 LLM은 OpenAI 호환 엔드포인트로 일원화하되 프로바이더 능력 탐지를 요구사항에 추가 (`9e9ee83`)
- 미결정 사항 Q5 해결 — Netflix Timed Text Style Guide를 규격 프로파일 1차 출처로 확정 (`7af39a7`)
- 미결정 사항 Q6 해결 — PySubtrans 대신 자체 얇은 어댑터로 결정 (`9e9ee83`)
- `char_counting` 스키마 재정의 — `grapheme`\|`cjk_width` → `grapheme`\|`latin_half`\|`fullwidth`. 기존 두 값으로는 한국어를 표현할 수 없었다 (`7af39a7`)
- 문서 버전 0.2 → 0.3 (`9e9ee83`)
- markdownlint가 `.gitignore`를 따르도록 설정 — 로컬 5개 / CI 4개로 어긋나던 검사 대상을 일치시킴 (`852cb68`)

### Fixed

- §7.4의 PySubtrans 배제 근거 정정 — "GUI가 딸려온다"는 사실이 아니다. `pyside6`는 `gui` extra로 분리돼 있으며, 실제 사유는 도메인 모델 동반이다 (`9e9ee83`)
- markdownlint 경고 76건 수정 — bare URL 42건, 목록·제목·코드펜스 주변 공백, 코드펜스 언어 지정, 인용문 병합, 제목 레벨 (`7af39a7`)
