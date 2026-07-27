# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/).

## [Unreleased]

### Added

- (없음)

### Changed

- (없음)

### Fixed

- (없음)

### Removed

- (없음)

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
