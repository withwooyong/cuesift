# cuesift

**AI 자막 번역·검수 트리아지 엔진**

> *"Sift the cues that actually need a human."* — 사람이 정말 봐야 할 자막만 걸러냅니다.

[![CI](https://github.com/withwooyong/cuesift/actions/workflows/ci.yml/badge.svg)](https://github.com/withwooyong/cuesift/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

> [!WARNING]
> **개발 이전 단계(pre-alpha)입니다.** 현재 리포에는 요구사항 정의서와 CLI 골격만 있습니다.
> 모든 서브커맨드는 미구현 상태로 종료 코드 `70`을 반환합니다. 아직 사용할 수 없습니다.

---

## 문제

다국어 OTT 자막 현지화에서 **번역가 검수가 비용·병목·품질편차의 단일 원인**입니다.
20개 언어 × 16부작이면 검수 공수가 20배로 늘고, 언어 하나가 늦으면 동시 출시가 깨집니다.

기존 도구는 양극단입니다 — TMS는 워크플로만 관리하고 품질 판단은 사람에게 넘기며,
품질추정(QE) 모델은 존재하지만 **자막 파이프라인에 배선되어 있지 않습니다.**

## 접근

전량 검수를 **위험도 순 부분 검수**로 바꿉니다.
번역 결과 전체에 위험 점수를 매기고, 상위 N%만 사람에게 올립니다.

```
자막/영상 → (STT) → 번역 → 위험도 채점 → 상위 N% 트리아지 → 리포트
```

위험 신호는 단일 지표가 아니라 규격 위반·자가일관성·품질추정 등을 결합해 산출합니다.
자세한 설계는 [요구사항정의서](요구사항정의서.md)를 참고하세요.

## CLI (설계 확정, 구현 예정)

```bash
# 전 파이프라인 — 상위 10%만 검수 대상으로 선별
cuesift translate episode01.ko.srt --to en,ja,th,vi --review-budget 10%

# 영상 입력 (자막 없음) — STT로 원문 생성 후 번역
cuesift translate episode02.mp4 --source-lang ko --to en,ja

# 규격 검사만 (CI 게이트) — 치명 오류 시 exit code ≠ 0
cuesift check dist/episode01.th.srt --spec th --fail-on hard

# 비용 추정
cuesift translate episode01.ko.srt --to en,ja --dry-run
```

모든 옵션은 `cuesift.yaml`로도 지정할 수 있으며, **CLI 인자가 설정 파일보다 우선**합니다.

## 개발 환경

PyPI 배포 전이므로 소스에서 설치합니다.

```bash
git clone https://github.com/withwooyong/cuesift.git
cd cuesift
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest
ruff check .
```

> STT(`whisperx`)와 품질추정(`unbabel-comet`)은 `torch`를 끌고 오므로 **선택 의존성**으로 분리했습니다.
> 필요할 때만 `pip install -e ".[dev,stt,qe]"` 로 설치하세요.
> 개발은 Python **3.11 / 3.12** 를 권장합니다 — CI가 검증하는 범위이며, 그 이상은 torch 휠이 없을 수 있습니다.

## 문서

| 문서 | 내용 |
|---|---|
| [요구사항정의서.md](요구사항정의서.md) | 배경·요구사항·아키텍처·인터페이스 명세 |
| [번역관리_TMS_솔루션_비교.md](번역관리_TMS_솔루션_비교.md) | 기존 TMS 솔루션 조사 |
| [AI_자막검수_오픈소스_비교.md](AI_자막검수_오픈소스_비교.md) | 자막 검수 오픈소스 조사 |

## 라이선스

[Apache-2.0](LICENSE) · 저작권 있는 자막·영상은 이 리포에 포함하지 않습니다.
벤치마크는 공개 다국어 병렬 코퍼스(TED2020/OPUS 계열)로 구성합니다.
