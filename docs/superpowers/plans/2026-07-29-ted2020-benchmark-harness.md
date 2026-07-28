# TED2020 벤치마크 하네스 구현 계획 (계획 B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요구사항정의서 §9.1의 **Recall @ Budget**과 무작위 베이스라인 대비 배수의 첫 실측치를 만든다. 이 숫자가 README 최상단에 들어가며 §11 R4("OpenAI 래퍼로 인식됨")에 대한 유일한 반증 자료다.

**Architecture:** 5단계 파이프라인 중 ④ measure만 제품 코드(`src/cuesift/`)이고 나머지는 `bench/`·`scripts/`에 둔다. **벤치는 제품 모듈의 첫 소비자일 뿐 자체 판정 로직을 갖지 않는다** — 벤치가 자기 신호 계산을 가지는 순간 "측정한 것"과 "출시하는 것"이 갈라진다. 계획 A(Tier 0 엔진)가 ④를 이미 완성했으므로 이 계획은 ①②③⑤와 문서 반영만 만든다.

```text
  ① fetch     OPUS TED2020 -> data/ted2020/ + manifest(SHA-256)   scripts/   [네트워크]
  ② build     문장 쌍 -> 타임코드 합성 -> 깨끗한 트랙              bench/
  ③ inject    오류 7종 주입 + 정답 라벨                            bench/
  ④ measure   collect_all -> fuse -> select_by_budget            src/cuesift/  <- 완성됨
  ⑤ report    Recall@Budget · 실제 검수 비율 · 배수                bench/
```

**Tech Stack:** Python 3.11+ · 표준 라이브러리(`urllib`·`zipfile`·`hashlib`·`json`·`statistics`·`random`) · 기존 런타임 의존성 `pyyaml`뿐. **새 의존성을 추가하지 않는다.**

## Global Constraints

이 절의 값은 스펙에서 그대로 옮긴 것이다. **모든 태스크의 요구사항에 암묵적으로 포함된다.**

| 항목 | 값 | 근거 |
| --- | --- | --- |
| Python 실행 | **`.venv/Scripts/python.exe`** (시스템 Python은 3.14라 다르다) | CLAUDE.md |
| 의존성 | 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개. **추가 금지** | CLAUDE.md |
| `scripts/` 구현 제약 | **표준 라이브러리만.** `scripts/check_links.py`의 선례 | 스펙 §3.4 |
| 모듈 첫 줄 | `from __future__ import annotations` | CLAUDE.md |
| 독스트링·주석 | **한국어**, 근거 FR·§ 번호 병기 | CLAUDE.md |
| 주석 내용 | "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"** | CLAUDE.md |
| ruff | `line-length = 100`, 규칙 `E,F,I,UP,B,SIM` | pyproject.toml |
| 커밋 메시지 | **한국어** | CLAUDE.md |
| 푸시 | **사용자가 명시적으로 요청할 때만.** 커밋과 푸시를 한 명령에 묶지 않는다 | CLAUDE.md |
| 언어쌍 | **ko→en/ja** (Q2 확정). 모든 판정이 CJK에서 동작해야 한다 | 요구사항정의서 §12 |
| 표본 | 언어쌍당 **5000 세그먼트**, 시드 고정 | 스펙 §4.1 |
| 주입률 | **10%**, 유형 7종 균등 배분, 세그먼트당 최대 1개 | 스펙 §5.5 |
| 예산 스윕 | **1 · 2 · 5 · 10 · 20 · 30%** | 스펙 §6.1 |
| 무작위 베이스라인 | 시드 **100회** 반복 → 평균·표준편차 | 스펙 §6.1 |
| 가중치 | **균등(무튜닝) 고정.** 첫 리포트에 "가중치 미튜닝" 명시 | 스펙 §6.3 |
| 배수 계산 | **요청 예산이 아니라 `review_ratio()`가 낸 실제 검수 비율로 나눈다** | 스펙 §6.2 |
| 라이선스 | TED2020은 **CC BY-NC-ND 4.0**. 원본도 가공물도 **커밋 금지**. 리포에 커밋하는 것은 `bench/manifest.json`뿐 | 스펙 §3.2 |

### 착수 시점에 확인한 사실 (2026-07-29)

**외부 URL은 링크 체커가 검사하지 않으므로 사람이 직접 확인해야 한다.** 이 계획을 쓰기 전에 확인했다.

| 항목 | 확인 결과 |
| --- | --- |
| `https://object.pouta.csc.fi/OPUS-TED2020/v1/moses/en-ko.txt.zip` | **200 OK**, 30,153,726 바이트 |
| `https://object.pouta.csc.fi/OPUS-TED2020/v1/moses/ja-ko.txt.zip` | **200 OK**, 28,575,670 바이트 |
| 언어쌍 디렉터리명 | 스펙 예측대로 **`en-ko`·`ja-ko`** (알파벳순, `ko-en`이 아니다) |
| zip 멤버 (en-ko) | `README`(955) · `LICENSE`(165) · **`TED2020.en-ko.en`(38,145,802)** · **`TED2020.en-ko.ko`(47,946,283)** · `TED2020.en-ko.xml`(11,780,376) |

**이 확인이 스코프를 줄였다.** moses 배포본은 **줄 단위로 정렬된 평문 두 파일**이다. 따라서 HANDOFF가 "빠진 조각"으로 적은 **자막 파일 로더(`pysubs2`)와 원문·번역 정렬은 이 계획에 필요 없다** — ②가 문장 쌍에서 타임코드를 합성해 `Segment`를 직접 만들고 JSON으로 넘긴다. `ingest` 모듈은 제품 과제로 남고 벤치의 선행 조건이 아니다.

## File Structure

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `scripts/fetch_ted2020.py` | 코퍼스 획득·SHA-256 검증. **표준 라이브러리만** | 1 |
| `bench/__init__.py` | 패키지 표식 (빈 파일 아님 — 목적 독스트링) | 1 |
| `bench/manifest.json` | 획득 기록. **리포에 커밋하는 유일한 데이터** | 1 |
| `bench/corpus.py` | moses 두 파일 → 문장 쌍, 필터와 사유별 집계 | 2 |
| `bench/timing.py` | 타임코드 합성·줄바꿈·제외 판정 | 3 |
| `bench/track_io.py` | `Segment` ↔ JSON 직렬화 | 4 |
| `bench/build_track.py` | ② 오케스트레이션 + 깨끗함 자기검증 | 4 |
| `bench/glossary.ted.yaml` | 용어집 확정본 (30~50개) | 5 |
| `scripts/glossary_candidates.py` | 후보 추출 보조 (커밋하되 산출물은 커밋 안 함) | 5 |
| `bench/inject.py` | ③ 주입 레지스트리 7종 + 라벨 | 6 |
| `bench/measure.py` | ④ 지표 6종 + 불변식 4종 | 7 |
| `bench/report.py` | ⑤ md·json 리포트 | 8 |
| `tests/test_bench_*.py` | 각 모듈 단위·라운드트립·실패경로 | 1~8 |

**`bench/`를 `src/`에 두지 않는 이유**: `pyproject.toml`의 `packages = ["src/cuesift"]`가 휠 포함 범위를 이미 제한하므로 벤치 코드가 배포물에 섞이지 않는다(스펙 §2.2).

---

## Task 1: 벤치 패키지 배선과 코퍼스 획득

**Files:**

- Create: `bench/__init__.py`
- Create: `scripts/fetch_ted2020.py`
- Create: `tests/test_scripts_fetch.py`
- Modify: `pyproject.toml:58-60` (pytest `pythonpath`), `pyproject.toml:64` (ruff `src`)
- Modify: `.gitignore` (말미에 `data/` 추가)

**Interfaces:**

- Produces:
  - `scripts/fetch_ted2020.py` 모듈 함수 `sha256_of(path: Path) -> str`
  - `load_manifest(path: Path) -> dict[str, dict]` — 언어쌍별 기록. 파일 없으면 `{}`
  - `verify_or_record(pair: str, archive: Path, url: str, manifest: dict[str, dict]) -> tuple[str, bool]` — `(sha256, 신규기록여부)`. 불일치면 `ValueError`
  - `extract_pair(archive: Path, pair: str, dest: Path) -> tuple[Path, Path]` — `(ko파일, 대상언어파일)`
- Consumes: 없음 (첫 태스크)

- [ ] **Step 1: pytest가 `bench`를 임포트할 수 있게 배선한다**

`pyproject.toml`의 `[tool.pytest.ini_options]`를 다음으로 바꾼다.

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
# bench/ 와 scripts/ 는 휠에 들어가지 않으므로 설치 경로로 임포트되지 않는다.
# 리포 루트를 sys.path에 넣지 않으면 tests/test_bench_*.py가 전부 수집 오류가 된다.
pythonpath = ["."]
```

같은 파일의 `[tool.ruff]` 절 `src`를 넓힌다. 넓히지 않으면 ruff의 isort가 `bench`를 서드파티로 보고 임포트 순서를 잘못 정렬한다.

```toml
[tool.ruff]
line-length = 100
src = ["src", "tests", "bench", "scripts"]
```

- [ ] **Step 2: `.gitignore`에 데이터 경로를 넣는다**

말미에 추가한다. **이 줄이 없으면 CC BY-NC-ND 자료가 커밋된다.**

```gitignore
# 벤치마크 코퍼스와 가공물 (CC BY-NC-ND 4.0 — 파생물 배포 금지, 스펙 §3.2)
data/
```

- [ ] **Step 3: `bench/__init__.py`를 만든다**

```python
"""TED2020 벤치마크 하네스 (설계 스펙 2026-07-28).

**벤치는 제품 모듈의 첫 소비자일 뿐 자체 판정 로직을 갖지 않는다.**
벤치가 자기만의 신호 계산을 가지는 순간 "측정한 것"과 "출시하는 것"이
갈라지고, 리포트의 숫자가 사용자가 받는 결과와 무관해진다.
"""

from __future__ import annotations
```

- [ ] **Step 4: 실패하는 테스트를 쓴다**

`tests/test_scripts_fetch.py`를 만든다. **네트워크를 쓰지 않는다** — 로컬에서 zip을 만들어 검증한다.

```python
"""코퍼스 획득 스크립트 테스트 (설계 스펙 §3)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.fetch_ted2020 import (
    extract_pair,
    load_manifest,
    sha256_of,
    verify_or_record,
)


def _make_archive(tmp_path: Path, pair: str, ko_lines: list[str], other_lines: list[str]) -> Path:
    """OPUS moses 배포본과 같은 구조의 zip을 만든다.

    실제 멤버명은 2026-07-29 확인 결과 `TED2020.en-ko.en`·`TED2020.en-ko.ko` 형식이다.
    """
    other = pair.split("-")[0]
    archive = tmp_path / f"{pair}.txt.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README", "corpus readme")
        zf.writestr("LICENSE", "CC BY-NC-ND 4.0")
        zf.writestr(f"TED2020.{pair}.ko", "\n".join(ko_lines) + "\n")
        zf.writestr(f"TED2020.{pair}.{other}", "\n".join(other_lines) + "\n")
    return archive


def test_sha256_is_stable(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"cuesift")
    assert sha256_of(f) == sha256_of(f)
    assert len(sha256_of(f)) == 64


def test_missing_manifest_is_an_empty_record_not_an_error(tmp_path):
    """첫 실행은 기록 모드다. 파일이 없다고 실패하면 최초 획득이 불가능하다."""
    assert load_manifest(tmp_path / "none.json") == {}


def test_first_run_records_the_hash(tmp_path):
    archive = _make_archive(tmp_path, "en-ko", ["안녕"], ["Hello"])
    digest, recorded = verify_or_record("en-ko", archive, "http://x/en-ko.txt.zip", {})
    assert recorded is True
    assert digest == sha256_of(archive)


def test_second_run_with_matching_hash_is_verification_not_record(tmp_path):
    archive = _make_archive(tmp_path, "en-ko", ["안녕"], ["Hello"])
    manifest = {"en-ko": {"sha256": sha256_of(archive), "url": "http://x/en-ko.txt.zip"}}
    _, recorded = verify_or_record("en-ko", archive, "http://x/en-ko.txt.zip", manifest)
    assert recorded is False


def test_hash_mismatch_is_fatal(tmp_path):
    """조용히 다른 데이터로 측정하면 리포트의 재현 정보가 거짓이 된다.

    스펙 §7 — 재현 불가능한 벤치마크 숫자는 없는 것보다 나쁘다. 인용되기 때문이다.
    """
    archive = _make_archive(tmp_path, "en-ko", ["안녕"], ["Hello"])
    manifest = {"en-ko": {"sha256": "0" * 64, "url": "http://x/en-ko.txt.zip"}}
    with pytest.raises(ValueError, match="sha256"):
        verify_or_record("en-ko", archive, "http://x/en-ko.txt.zip", manifest)


def test_extract_returns_ko_and_target_files(tmp_path):
    archive = _make_archive(tmp_path, "ja-ko", ["안녕", "세계"], ["こんにちは", "世界"])
    ko_path, other_path = extract_pair(archive, "ja-ko", tmp_path / "out")
    assert ko_path.read_text(encoding="utf-8").splitlines() == ["안녕", "세계"]
    assert other_path.read_text(encoding="utf-8").splitlines() == ["こんにちは", "世界"]


def test_extract_rejects_archive_without_expected_members(tmp_path):
    """멤버명이 바뀌면(스펙 리스크 B1) 조용히 빈 코퍼스로 측정하는 대신 실패해야 한다."""
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README", "only readme")
    with pytest.raises(ValueError, match="멤버"):
        extract_pair(archive, "en-ko", tmp_path / "out")


def test_manifest_roundtrip(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"en-ko": {"sha256": "a" * 64}}), encoding="utf-8")
    assert load_manifest(path)["en-ko"]["sha256"] == "a" * 64
```

- [ ] **Step 5: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scripts_fetch.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts'` 또는 수집 오류 8건.

**수집 개수를 읽는다.** 0개 수집은 통과가 아니라 설정 오류다(CLAUDE.md).

- [ ] **Step 6: `scripts/__init__.py`와 `scripts/fetch_ted2020.py`를 만든다**

`scripts/__init__.py`는 한 줄이다. 없으면 `from scripts.fetch_ted2020 import ...`가 실패한다.

```python
"""리포지토리 보조 스크립트. 표준 라이브러리만 쓴다."""
```

`scripts/fetch_ted2020.py`:

```python
"""OPUS TED2020 코퍼스 획득 (설계 스펙 §3).

**표준 라이브러리만 쓴다.** `scripts/check_links.py`가 세운 선례이며,
런타임 의존성 4개를 유지하기 위함이다 — 벤치 스크립트 하나 때문에
`pip install cuesift`가 무거워지면 안 된다.

**코퍼스는 CC BY-NC-ND 4.0이라 리포에 커밋하지 않는다**(스펙 §3.2).
커밋하는 것은 `bench/manifest.json`뿐이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

# 2026-07-29 확인: 두 URL 모두 200 OK. 언어쌍 디렉터리명은 알파벳순이라
# `ko-en`이 아니라 `en-ko`다. 이 순서를 뒤집으면 404가 조용히 빈 파일이 된다.
_BASE = "https://object.pouta.csc.fi/OPUS-TED2020/v1/moses"
PAIRS = ("en-ko", "ja-ko")

_LICENSE = "CC BY-NC-ND 4.0"

# 한 번에 읽는 크기. 코퍼스가 30MB대라 통째로 읽어도 되지만,
# 해시 계산과 다운로드가 같은 상수를 쓰면 메모리 상한이 명시적으로 남는다.
_CHUNK = 1 << 20


def archive_url(pair: str) -> str:
    return f"{_BASE}/{pair}.txt.zip"


def sha256_of(path: Path) -> str:
    """파일의 SHA-256. manifest 검증의 단일 근거다."""
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, dict]:
    """manifest를 읽는다. **없으면 빈 dict** — 첫 실행은 기록 모드다.

    여기서 실패하면 최초 획득 자체가 불가능해진다.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 최상위가 매핑이 아니다")
    return raw


def verify_or_record(
    pair: str, archive: Path, url: str, manifest: dict[str, dict]
) -> tuple[str, bool]:
    """해시를 검증하거나 최초 기록한다. `(sha256, 신규기록여부)`.

    **불일치는 치명적이다.** 조용히 다른 데이터로 측정하면 리포트 헤더의
    재현 정보가 거짓이 되고, 그 리포트는 인용된 뒤에야 틀린 것이 드러난다.
    """
    digest = sha256_of(archive)
    known = manifest.get(pair)
    if known is None:
        manifest[pair] = {
            "pair": pair,
            "url": url,
            "sha256": digest,
            "bytes": archive.stat().st_size,
            "retrieved": date.today().isoformat(),
            "license": _LICENSE,
        }
        return digest, True

    if known.get("sha256") != digest:
        raise ValueError(
            f"{pair}: sha256 불일치. manifest={known.get('sha256')} 로컬={digest}\n"
            f"코퍼스가 바뀌었거나 다운로드가 손상됐다. "
            f"의도한 교체라면 manifest에서 해당 항목을 지우고 다시 실행할 것."
        )
    return digest, False


def extract_pair(archive: Path, pair: str, dest: Path) -> tuple[Path, Path]:
    """zip에서 ko 파일과 대상 언어 파일을 꺼낸다. `(ko, other)`.

    멤버명은 `TED2020.{pair}.{lang}` 형식이다(2026-07-29 확인).
    **멤버가 없으면 실패시킨다** — 없는 파일을 건너뛰면 빈 코퍼스로
    "측정 성공"이 나오고, 그때 Recall은 0이 아니라 정의되지 않는다.
    """
    other = pair.split("-")[0]
    ko_member = f"TED2020.{pair}.ko"
    other_member = f"TED2020.{pair}.{other}"

    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        missing = [m for m in (ko_member, other_member) if m not in names]
        if missing:
            raise ValueError(
                f"{archive.name}: 기대한 멤버가 없다 — {', '.join(missing)}. "
                f"실제 멤버: {', '.join(sorted(names))}"
            )
        zf.extract(ko_member, dest)
        zf.extract(other_member, dest)

    return dest / ko_member, dest / other_member


def download(url: str, dest: Path) -> None:
    """이미 있으면 건너뛴다. 해시 검증은 호출자가 한다."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:  # noqa: S310
        while chunk := resp.read(_CHUNK):
            out.write(chunk)
    # 부분 파일을 최종 이름으로 두지 않는다. 중단된 다운로드가 다음 실행에서
    # "이미 있음"으로 통과하면 해시 불일치의 원인을 찾기 어렵다.
    tmp.replace(dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OPUS TED2020 코퍼스 획득")
    parser.add_argument("--data-dir", type=Path, default=Path("data/ted2020"))
    parser.add_argument("--manifest", type=Path, default=Path("bench/manifest.json"))
    parser.add_argument("--pairs", nargs="*", default=list(PAIRS))
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    changed = False

    for pair in args.pairs:
        url = archive_url(pair)
        archive = args.data_dir / f"{pair}.txt.zip"
        print(f"[{pair}] {url}")
        download(url, archive)
        digest, recorded = verify_or_record(pair, archive, url, manifest)
        changed = changed or recorded
        ko_path, other_path = extract_pair(archive, pair, args.data_dir / pair)
        ko_lines = sum(1 for _ in ko_path.open(encoding="utf-8"))
        manifest[pair]["lines"] = ko_lines
        print(f"[{pair}] sha256={digest[:16]}… lines={ko_lines:,} {'기록' if recorded else '검증'}")

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"manifest -> {args.manifest} ({'갱신' if changed else '검증만'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scripts_fetch.py -q`
Expected: **8 passed**

전체도 돌린다. Run: `.venv/Scripts/python.exe -m pytest -q` → **189 passed** (기존 181 + 8)

- [ ] **Step 8: 실제로 코퍼스를 받는다 (네트워크 필요)**

Run: `.venv/Scripts/python.exe scripts/fetch_ted2020.py`

`bench/manifest.json`이 생기고 `data/ted2020/`에 두 zip과 추출본이 놓인다.
`git status`로 **`data/`가 추적되지 않는지 반드시 확인한다.**

- [ ] **Step 9: 린트와 커밋**

```bash
.venv/Scripts/python.exe -m ruff check src tests bench scripts
.venv/Scripts/python.exe -m ruff format --check src tests bench scripts
git status --short
git add pyproject.toml .gitignore bench/__init__.py bench/manifest.json scripts/__init__.py scripts/fetch_ted2020.py tests/test_scripts_fetch.py
git commit -m "기능: TED2020 코퍼스 획득 스크립트 추가 (스펙 §3)"
```

---

## Task 2: 코퍼스 로더와 필터

**Files:**

- Create: `bench/corpus.py`
- Create: `tests/test_bench_corpus.py`

**Interfaces:**

- Consumes: Task 1의 `extract_pair` 산출물(줄 정렬된 평문 두 파일)
- Produces:
  - `@dataclass(frozen=True) SentencePair(source: str, target: str)`
  - `@dataclass(frozen=True) FilterStats(total: int, kept: int, dropped: dict[str, int])`
  - `load_pairs(ko_path: Path, other_path: Path) -> list[SentencePair]` — 줄 수 불일치면 `ValueError`
  - `filter_pairs(pairs: Sequence[SentencePair], *, max_ratio: float = 6.0, min_ratio: float = 0.15) -> tuple[list[SentencePair], FilterStats]`
  - `sample(pairs: Sequence[SentencePair], n: int, seed: int) -> list[SentencePair]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
"""코퍼스 로더·필터 테스트 (설계 스펙 §4.3)."""

from __future__ import annotations

import pytest

from bench.corpus import SentencePair, filter_pairs, load_pairs, sample


def _write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_load_pairs_aligns_line_by_line(tmp_path):
    ko = _write(tmp_path, "a.ko", ["안녕하세요", "반갑습니다"])
    en = _write(tmp_path, "a.en", ["Hello", "Nice to meet you"])
    pairs = load_pairs(ko, en)
    assert pairs == [
        SentencePair("안녕하세요", "Hello"),
        SentencePair("반갑습니다", "Nice to meet you"),
    ]


def test_line_count_mismatch_is_fatal(tmp_path):
    """정렬이 어긋난 코퍼스로 측정하면 모든 세그먼트가 오역으로 잡힌다.

    그 결과는 Recall 100%처럼 보이지만 실제로는 측정 자체가 무의미하다.
    """
    ko = _write(tmp_path, "b.ko", ["하나", "둘"])
    en = _write(tmp_path, "b.en", ["One"])
    with pytest.raises(ValueError, match="줄 수"):
        load_pairs(ko, en)


def test_filter_drops_empty_and_duplicate_and_extreme_ratio():
    pairs = [
        SentencePair("정상적인 한국어 문장입니다", "A normal Korean sentence here"),
        SentencePair("", "Empty source"),
        SentencePair("빈 번역", "   "),
        SentencePair("정상적인 한국어 문장입니다", "A normal Korean sentence here"),  # 중복
        SentencePair("짧다", "This target is absurdly long compared to its tiny source" * 4),
    ]
    kept, stats = filter_pairs(pairs)
    assert [p.source for p in kept] == ["정상적인 한국어 문장입니다"]
    assert stats.total == 5
    assert stats.kept == 1
    assert stats.dropped["empty"] == 2
    assert stats.dropped["duplicate"] == 1
    assert stats.dropped["ratio"] == 1


def test_filter_stats_account_for_every_input():
    """제거 건수 합 + 남은 건수 = 전체. 어긋나면 조용히 사라진 표본이 있다."""
    pairs = [SentencePair(f"문장 번호 {i} 입니다", f"Sentence number {i} here") for i in range(10)]
    pairs.append(SentencePair("", ""))
    kept, stats = filter_pairs(pairs)
    assert stats.kept + sum(stats.dropped.values()) == stats.total == len(pairs)
    assert len(kept) == stats.kept


def test_sample_is_deterministic_for_a_seed():
    """NFR-3 재현성 — 같은 시드가 다른 표본을 내면 리포트를 재현할 수 없다."""
    pairs = [SentencePair(f"원문 {i} 입니다", f"Source {i} here") for i in range(100)]
    assert sample(pairs, 10, seed=42) == sample(pairs, 10, seed=42)
    assert sample(pairs, 10, seed=42) != sample(pairs, 10, seed=43)


def test_sample_does_not_mutate_input():
    """예산 스윕처럼 같은 목록을 여러 번 쓰는 호출이 오염되면 안 된다."""
    pairs = [SentencePair(f"원문 {i} 입니다", f"Source {i} here") for i in range(20)]
    before = list(pairs)
    sample(pairs, 5, seed=1)
    assert pairs == before


def test_sample_larger_than_population_returns_all():
    pairs = [SentencePair("가나다라", "abcd")]
    assert len(sample(pairs, 100, seed=1)) == 1
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_corpus.py -q`
Expected: 수집 오류 (`No module named 'bench.corpus'`)

- [ ] **Step 3: `bench/corpus.py`를 구현한다**

```python
"""코퍼스 로더와 필터 (설계 스펙 §4.1, §4.3).

**제거 건수 자체가 결과다**(§4.4). 몇 %가 왜 빠졌는지를 리포트에 실어야
표본이 편향됐는지 독자가 판단할 수 있다.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SentencePair:
    """줄 정렬된 문장 한 쌍. 원문은 항상 ko다 (Q2: ko→en/ja)."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class FilterStats:
    """무엇을 왜 뺐는지. 합이 맞지 않으면 조용히 사라진 표본이 있다."""

    total: int
    kept: int
    dropped: dict[str, int] = field(default_factory=dict)


def load_pairs(ko_path: Path, other_path: Path) -> list[SentencePair]:
    """moses 평문 두 파일을 줄 단위로 짝짓는다.

    **줄 수가 다르면 실패시킨다.** 한 줄이라도 밀리면 그 뒤 전부가 오정렬이
    되는데, 그 상태의 측정은 "전부 오역"이라 Recall이 100%처럼 보인다 —
    가장 그럴듯해 보이는 틀린 숫자다.
    """
    ko_lines = ko_path.read_text(encoding="utf-8").splitlines()
    other_lines = other_path.read_text(encoding="utf-8").splitlines()
    if len(ko_lines) != len(other_lines):
        raise ValueError(
            f"줄 수가 다르다: {ko_path.name}={len(ko_lines):,} "
            f"{other_path.name}={len(other_lines):,}"
        )
    return [SentencePair(k, o) for k, o in zip(ko_lines, other_lines, strict=True)]


def filter_pairs(
    pairs: Sequence[SentencePair],
    *,
    max_ratio: float = 6.0,
    min_ratio: float = 0.15,
) -> tuple[list[SentencePair], FilterStats]:
    """빈 문자열·중복·극단 길이비를 제거한다.

    길이비 한도는 **주입 전에 이미 망가진 쌍**을 빼기 위한 것이다. 깨끗한
    트랙이 아니면 `length.ratio` 신호의 오탐과 주입분을 구분할 수 없다.
    6.0/0.15는 ko→en에서 관용적으로 나올 수 있는 범위 밖이다 — 이보다
    좁히면 정상 번역이 표본에서 빠져 코퍼스가 인위적으로 균질해진다.
    """
    dropped = {"empty": 0, "duplicate": 0, "ratio": 0}
    seen: set[tuple[str, str]] = set()
    kept: list[SentencePair] = []

    for pair in pairs:
        src, tgt = pair.source.strip(), pair.target.strip()
        if not src or not tgt:
            dropped["empty"] += 1
            continue
        key = (src, tgt)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        ratio = len(tgt) / len(src)
        if ratio > max_ratio or ratio < min_ratio:
            dropped["ratio"] += 1
            continue
        seen.add(key)
        kept.append(SentencePair(src, tgt))

    return kept, FilterStats(total=len(pairs), kept=len(kept), dropped=dropped)


def sample(pairs: Sequence[SentencePair], n: int, seed: int) -> list[SentencePair]:
    """시드 고정 표본. **입력을 변형하지 않는다.**

    `random.shuffle`을 원본에 걸면 같은 목록으로 두 번째 표본을 뽑을 때
    결과가 달라져 NFR-3(재현성)이 깨진다.
    """
    if n >= len(pairs):
        return list(pairs)
    return random.Random(seed).sample(list(pairs), n)
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_corpus.py -q`
Expected: **7 passed**

- [ ] **Step 5: 커밋**

```bash
git add bench/corpus.py tests/test_bench_corpus.py
git commit -m "기능: 코퍼스 로더와 필터 추가 (스펙 §4.3)"
```

---

## Task 3: 타임코드 합성

**Files:**

- Create: `bench/timing.py`
- Create: `tests/test_bench_timing.py`

**Interfaces:**

- Consumes: `bench.corpus.SentencePair`, `cuesift.spec.{SpecProfile, load_builtin, text_width}`
- Produces:
  - `wrap_text(text: str, profile: SpecProfile) -> str | None` — 줄바꿈을 넣은 문자열. 담을 수 없으면 `None`
  - `required_duration_ms(texts: Mapping[str, str], profiles: Mapping[str, SpecProfile]) -> int`
  - `plan_segment(pair, target_lang, profiles) -> TimedText | None` — 제외 대상이면 `None`
  - `@dataclass(frozen=True) TimedText(source_text: str, target_text: str, duration_ms: int)`
  - 상수 `SAFETY = 1.10`, `GAP_MS = 120`

**핵심 설계 — 왜 세 언어를 함께 보는가**

FR-2.4가 "번역이 타임코드를 보존한다"고 규정하므로 시간은 ko에 붙고 en·ja가 물려받는다. 그런데 같은 시간에 en은 42자, ja는 13자 제한을 받는다. **ko 기준으로만 duration을 정하면 번역 쪽에 규격 위반이 무작위로 섞여 들어와 측정이 오염된다** — 그 위반이 주입분인지 합성 실패인지 구분할 수 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
"""타임코드 합성 테스트 (설계 스펙 §4.2)."""

from __future__ import annotations

from cuesift.spec import check_text, load_builtin, text_width

from bench.corpus import SentencePair
from bench.timing import GAP_MS, SAFETY, plan_segment, required_duration_ms, wrap_text

PROFILES = {"ko": load_builtin("ted-ko"), "en": load_builtin("ted-en"), "ja": load_builtin("ted-ja")}


def test_wrap_keeps_short_text_on_one_line():
    assert wrap_text("짧은 문장", PROFILES["ko"]) == "짧은 문장"


def test_wrap_splits_on_spaces_when_available():
    p = PROFILES["en"]
    long_en = "word " * 20
    wrapped = wrap_text(long_en.strip(), p)
    assert wrapped is not None
    lines = wrapped.split("\n")
    assert len(lines) <= p.max_lines
    assert all(text_width(ln, p.char_counting) <= p.max_chars_per_line for ln in lines)


def test_wrap_falls_back_to_character_split_without_spaces():
    """일본어는 어절 사이에 공백이 없다.

    공백 분할만 쓰면 ja 텍스트가 통째로 한 줄이 되어 전량 줄길이 위반이 되고,
    깨끗한 트랙이라는 전제가 무너진다.
    """
    p = PROFILES["ja"]
    no_space = "あ" * int(p.max_chars_per_line + 3)
    wrapped = wrap_text(no_space, p)
    assert wrapped is not None
    assert "\n" in wrapped
    assert all(text_width(ln, p.char_counting) <= p.max_chars_per_line for ln in wrapped.split("\n"))


def test_wrap_returns_none_when_two_lines_cannot_hold_it():
    """담을 수 없는 것을 억지로 담으면 그 세그먼트가 영구 오탐이 된다."""
    p = PROFILES["ja"]
    too_long = "あ" * int(p.max_chars_per_line * p.max_lines + 5)
    assert wrap_text(too_long, p) is None


def test_required_duration_takes_the_strictest_language():
    """가장 빡빡한 언어가 duration을 정한다. 하나라도 CPS를 넘으면 오염이다."""
    texts = {"ko": "가" * 10, "en": "a" * 60, "ja": "あ" * 10}
    got = required_duration_ms(texts, PROFILES)
    for lang, text in texts.items():
        p = PROFILES[lang]
        cps = text_width(text, p.char_counting) / (got / 1000)
        assert cps <= p.max_cps


def test_planned_segment_is_clean_under_every_profile():
    """**이 테스트가 합성의 존재 이유다.**

    합성 결과가 규격을 위반하면 이후 검출되는 위반이 주입분인지
    합성 실패인지 구분할 수 없다. 스펙 §4.2가 "규격 위반 0건인 깨끗한 트랙"을
    요구하는 이유다.
    """
    pair = SentencePair("기후 변화는 우리 시대의 가장 큰 도전입니다", "Climate change is the greatest challenge of our time")
    planned = plan_segment(pair, "en", PROFILES)
    assert planned is not None
    for lang, text in (("ko", planned.source_text), ("en", planned.target_text)):
        violations = check_text(text, planned.duration_ms, PROFILES[lang])
        assert violations == [], f"{lang}: {violations}"


def test_impossible_segment_is_excluded_not_forced():
    """max_duration으로도 세 언어를 만족시킬 수 없으면 표본에서 뺀다."""
    pair = SentencePair("가" * 400, "a" * 2000)
    assert plan_segment(pair, "en", PROFILES) is None


def test_gap_is_fixed_and_positive():
    """세그먼트 간 간격이 0이면 경계에서 겹침 판정이 흔들린다(FR-5.1)."""
    assert GAP_MS > 0
    assert SAFETY > 1.0
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_timing.py -q`
Expected: 수집 오류 (`No module named 'bench.timing'`)

- [ ] **Step 3: `bench/timing.py`를 구현한다**

```python
"""타임코드 합성 (설계 스펙 §4.2).

FR-2.4가 "번역이 타임코드를 보존한다"고 규정하므로 시간은 원문(ko)에 붙고
en·ja가 물려받는다. 그런데 같은 시간에 en은 42자, ja는 13자 제한을 받으므로
**ko 기준으로만 정하면 번역 쪽에 규격 위반이 무작위로 섞여 들어온다.**
그러면 이후 검출되는 위반이 주입분인지 합성 실패인지 구분할 수 없다.

그래서 duration을 **세 언어가 모두 만족하는 값**으로 잡는다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from cuesift.spec import SpecProfile, text_width

from bench.corpus import SentencePair

# CPS 한도에 정확히 붙이면 부동소수 반올림 한 번으로 위반이 된다.
# 1.10이면 여유가 충분하면서도 duration이 비현실적으로 길어지지 않는다.
# 이 값이 너무 작으면 깨끗함 불변식(Task 4)이 실패로 잡아낸다.
SAFETY = 1.10

# 세그먼트 사이 고정 간격. 0이면 `end == start` 경계가 되는데,
# check_overlaps는 그것을 겹침으로 보지 않지만 duration 반올림이
# 한 번만 어긋나도 겹침이 생긴다 (FR-5.1).
GAP_MS = 120


@dataclass(frozen=True, slots=True)
class TimedText:
    """줄바꿈까지 확정된 텍스트와 그것을 담을 수 있는 duration."""

    source_text: str
    target_text: str
    duration_ms: int


def wrap_text(text: str, profile: SpecProfile) -> str | None:
    """`profile`의 줄 수·줄 길이 안에 담기도록 줄바꿈을 넣는다.

    담을 수 없으면 `None`. **억지로 담지 않는다** — 한도를 넘긴 채 넣으면
    그 세그먼트가 트랙 내내 규격 위반으로 잡히는 영구 오탐이 된다.

    공백이 있으면 어절 단위로, 없으면 문자 단위로 나눈다. **일본어는 어절
    사이에 공백이 없어** 공백 분할만 쓰면 ja 텍스트가 통째로 한 줄이 되고
    전량이 줄길이 위반이 된다.
    """
    limit = profile.max_chars_per_line
    mode = profile.char_counting

    if text_width(text, mode) <= limit:
        return text

    lines: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    tokens = text.split(" ")
    has_spaces = len(tokens) > 1

    units = [t + " " for t in tokens[:-1]] + [tokens[-1]] if has_spaces else list(text)

    for unit in units:
        candidate = current + unit
        if text_width(candidate.rstrip(), mode) <= limit:
            current = candidate
            continue
        flush()
        # 단위 하나가 한 줄을 넘으면(긴 URL, 공백 없는 긴 어절) 담을 수 없다.
        if text_width(unit.rstrip(), mode) > limit:
            return None
        current = unit
    flush()

    if len(lines) > profile.max_lines:
        return None
    return "\n".join(ln.rstrip() for ln in lines)


def required_duration_ms(
    texts: Mapping[str, str], profiles: Mapping[str, SpecProfile]
) -> int:
    """세 언어의 CPS 한도를 **모두** 만족하는 최소 duration.

    가장 빡빡한 언어가 값을 정한다. 하나라도 넘으면 그 언어의 트랙이
    규격 위반으로 오염되고, 깨끗한 트랙 전제가 무너진다.
    """
    needed = 0.0
    for lang, text in texts.items():
        profile = profiles[lang]
        # 줄바꿈은 표시 폭이 아니다. CPS 계산에서 빼지 않으면 2줄 세그먼트가
        # 실제보다 길게 계산돼 duration이 불필요하게 늘어난다.
        width = text_width(text.replace("\n", ""), profile.char_counting)
        needed = max(needed, width / profile.max_cps * 1000.0)

    duration = math.ceil(needed * SAFETY)
    floor = max(p.min_duration_ms for p in profiles.values())
    return max(duration, floor)


def plan_segment(
    pair: SentencePair,
    target_lang: str,
    profiles: Mapping[str, SpecProfile],
) -> TimedText | None:
    """문장 쌍 하나를 타임코드가 붙을 수 있는 형태로 만든다.

    담을 수 없으면 `None`을 돌려 **표본에서 제외**한다. 제외 건수 자체가
    결과다(§4.4) — "ko 자막을 그대로 en·ja로 옮겼을 때 몇 %가 물리적으로
    규격을 만족시킬 수 없는가"는 FR-5.4(규격 자동 교정)를 정량적으로
    정당화하는 숫자다.
    """
    active = {"ko": profiles["ko"], target_lang: profiles[target_lang]}

    wrapped_source = wrap_text(pair.source, active["ko"])
    wrapped_target = wrap_text(pair.target, active[target_lang])
    if wrapped_source is None or wrapped_target is None:
        return None

    texts = {"ko": wrapped_source, target_lang: wrapped_target}
    duration = required_duration_ms(texts, active)

    ceiling = min(p.max_duration_ms for p in active.values())
    if duration > ceiling:
        return None

    return TimedText(
        source_text=wrapped_source,
        target_text=wrapped_target,
        duration_ms=duration,
    )
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_timing.py -q`
Expected: **8 passed**

실패하면 `SAFETY`를 올리기 전에 **어느 언어의 어느 검사가 걸렸는지** 출력해 확인한다. 원인이 CPS면 `SAFETY`, 줄 길이면 `wrap_text`다.

- [ ] **Step 5: 커밋**

```bash
git add bench/timing.py tests/test_bench_timing.py
git commit -m "기능: 타임코드 합성 추가 (스펙 §4.2)"
```

---

## Task 4: 트랙 직렬화와 빌더

**Files:**

- Create: `bench/track_io.py`
- Create: `bench/build_track.py`
- Create: `tests/test_bench_track_io.py`

**Interfaces:**

- Consumes: Task 2·3 전체, `cuesift.segment.Segment`, `cuesift.spec.{check_text, check_overlaps}`
- Produces:
  - `dump_track(segments: Sequence[Segment], path: Path) -> None`
  - `load_track(path: Path) -> list[Segment]`
  - `build(pairs, target_lang, profiles) -> tuple[list[Segment], dict[str, int]]` — `(트랙, 제외사유별 건수)`
  - `assert_clean(segments, profiles, target_lang) -> None` — 위반이 있으면 `AssertionError`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
"""트랙 직렬화·빌더 테스트 (설계 스펙 §4.2, §4.4)."""

from __future__ import annotations

import pytest
from cuesift.segment import Segment
from cuesift.spec import load_builtin

from bench.build_track import assert_clean, build
from bench.corpus import SentencePair
from bench.track_io import dump_track, load_track

PROFILES = {"ko": load_builtin("ted-ko"), "en": load_builtin("ted-en"), "ja": load_builtin("ted-ja")}


def test_track_roundtrips_through_json(tmp_path):
    segs = [
        Segment(id="s0", index=0, start_ms=0, end_ms=1500, source_text="안녕", target_text="Hi"),
        Segment(id="s1", index=1, start_ms=1620, end_ms=3000, source_text="세계", target_text="World"),
    ]
    path = tmp_path / "t.json"
    dump_track(segs, path)
    assert load_track(path) == segs


def test_roundtrip_preserves_newlines_in_text(tmp_path):
    """2줄 세그먼트의 줄바꿈이 유실되면 줄 길이 검사가 통째로 달라진다."""
    segs = [
        Segment(id="s0", index=0, start_ms=0, end_ms=2000, source_text="첫 줄\n둘째 줄", target_text="a\nb")
    ]
    path = tmp_path / "t.json"
    dump_track(segs, path)
    assert load_track(path)[0].source_text == "첫 줄\n둘째 줄"


def test_build_produces_monotonic_non_overlapping_timecodes():
    pairs = [SentencePair(f"문장 번호 {i} 입니다", f"Sentence number {i} here") for i in range(20)]
    segs, _ = build(pairs, "en", PROFILES)
    assert len(segs) > 0
    for prev, curr in zip(segs, segs[1:], strict=False):
        assert curr.start_ms > prev.end_ms, "간격이 없으면 FR-5.1 겹침 금지가 깨진다"


def test_build_track_is_clean_under_both_profiles():
    """**깨끗한 트랙이 이 계획 전체의 전제다.**

    여기서 위반이 나오면 이후 검출되는 규격 위반이 주입분인지 합성 실패인지
    구분할 수 없고, 오탐이 원리적으로 0이라는 §4.2의 보장이 사라진다.
    """
    pairs = [
        SentencePair("기후 변화는 우리 시대의 도전입니다", "Climate change is our challenge"),
        SentencePair("교육은 사회를 바꿉니다", "Education transforms society"),
        SentencePair("인공지능이 빠르게 발전합니다", "AI advances rapidly"),
    ]
    segs, _ = build(pairs, "en", PROFILES)
    assert_clean(segs, PROFILES, "en")


def test_build_reports_exclusion_reasons():
    """제외 건수 자체가 결과다(§4.4). 사유가 없으면 편향을 판단할 수 없다."""
    pairs = [
        SentencePair("짧은 문장입니다", "A short sentence"),
        SentencePair("가" * 400, "a" * 2000),
    ]
    segs, excluded = build(pairs, "en", PROFILES)
    assert len(segs) == 1
    assert excluded["unfittable"] == 1


def test_assert_clean_actually_fails_on_a_dirty_track():
    """**게이트를 만들면 반드시 실패시켜 본다.**

    통과만 확인한 불변식은 통과하지 않는 상황을 못 잡을 수 있다.
    """
    dirty = [
        Segment(id="x", index=0, start_ms=0, end_ms=200, source_text="가" * 50, target_text="a" * 200)
    ]
    with pytest.raises(AssertionError):
        assert_clean(dirty, PROFILES, "en")


def test_assert_clean_catches_overlap():
    overlapping = [
        Segment(id="a", index=0, start_ms=0, end_ms=2000, source_text="안녕", target_text="Hi"),
        Segment(id="b", index=1, start_ms=1500, end_ms=3000, source_text="세계", target_text="World"),
    ]
    with pytest.raises(AssertionError, match="겹침"):
        assert_clean(overlapping, PROFILES, "en")
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_track_io.py -q`
Expected: 수집 오류

- [ ] **Step 3: `bench/track_io.py`를 구현한다**

```python
"""트랙 직렬화 (설계 스펙 §5.7).

가공물은 `data/bench/`에만 둔다 — CC BY-NC-ND 4.0이라 리포에 커밋하지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from cuesift.segment import Segment

_FIELDS = ("id", "index", "start_ms", "end_ms", "source_text", "target_text")


def dump_track(segments: Sequence[Segment], path: Path) -> None:
    """트랙을 JSON으로 쓴다. `ensure_ascii=False` — ko·ja가 읽을 수 있어야 디버깅이 된다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{f: getattr(seg, f) for f in _FIELDS} for seg in segments]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_track(path: Path) -> list[Segment]:
    """JSON에서 트랙을 읽는다.

    `Segment.__post_init__`이 타임코드를 검증하므로 손상된 파일은
    여기서 `ValueError`로 드러난다 — 조용히 음수 duration을 만들지 않는다.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Segment(**{f: item[f] for f in _FIELDS}) for item in raw]
```

- [ ] **Step 4: `bench/build_track.py`를 구현한다**

```python
"""② build — 깨끗한 자막 트랙 합성 (설계 스펙 §4).

결과는 **규격 위반 0건인 트랙**이다. 이후 검출되는 규격 위반은 100%
주입분이므로 오탐이 원리적으로 0이 된다. 이 전제가 깨지면 Recall 숫자의
분모와 분자가 둘 다 오염된다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from cuesift.segment import Segment
from cuesift.spec import SpecProfile, check_overlaps, check_text, load_builtin

from bench.corpus import filter_pairs, load_pairs, sample
from bench.timing import GAP_MS, SentencePair, plan_segment
from bench.track_io import dump_track

# 스펙 §4.1 — 언어쌍당 5000. Tier 0는 LLM 호출이 없어 실행이 수 초라
# 표본을 키우는 비용이 사실상 0이다. 주입률 10%면 오류 500건이고
# Recall 추정 표준오차가 약 2%p다.
SAMPLE_SIZE = 5000
DEFAULT_SEED = 20260729


def build(
    pairs: Sequence[SentencePair],
    target_lang: str,
    profiles: Mapping[str, SpecProfile],
) -> tuple[list[Segment], dict[str, int]]:
    """문장 쌍을 타임코드가 붙은 트랙으로 만든다. `(트랙, 제외사유별 건수)`."""
    excluded = {"unfittable": 0}
    segments: list[Segment] = []
    cursor = 0

    for pair in pairs:
        planned = plan_segment(pair, target_lang, profiles)
        if planned is None:
            excluded["unfittable"] += 1
            continue
        start = cursor
        end = start + planned.duration_ms
        segments.append(
            Segment(
                id=f"{target_lang}-{len(segments):05d}",
                index=len(segments),
                start_ms=start,
                end_ms=end,
                source_text=planned.source_text,
                target_text=planned.target_text,
            )
        )
        cursor = end + GAP_MS

    return segments, excluded


def assert_clean(
    segments: Sequence[Segment],
    profiles: Mapping[str, SpecProfile],
    target_lang: str,
) -> None:
    """트랙이 규격 위반 0건인지 확인한다. **위반이 있으면 진행하지 않는다.**

    `검사하지 않고 통과하는 게이트는 없는 게이트보다 나쁘다` — 여기서
    통과시키면 그 위반이 리포트에서 "검출 성공"으로 집계된다.
    """
    problems: list[str] = []

    for seg in segments:
        for lang, text in (("ko", seg.source_text), (target_lang, seg.target_text or "")):
            for violation in check_text(text, seg.duration_ms, profiles[lang]):
                problems.append(f"{seg.id}[{lang}] {violation.kind} {violation.measured}")

    for seg_id, violation in check_overlaps(segments).items():
        problems.append(f"{seg_id} 겹침 {violation.measured}ms")

    if problems:
        shown = "\n  ".join(problems[:10])
        raise AssertionError(
            f"합성 트랙에 규격 위반 {len(problems)}건이 있다. 깨끗한 트랙 전제가 깨졌다:\n  {shown}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="깨끗한 벤치마크 트랙 합성")
    parser.add_argument("--pair", required=True, choices=["en-ko", "ja-ko"])
    parser.add_argument("--data-dir", type=Path, default=Path("data/ted2020"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/bench"))
    parser.add_argument("--size", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    target_lang = args.pair.split("-")[0]
    profiles = {
        "ko": load_builtin("ted-ko"),
        target_lang: load_builtin(f"ted-{target_lang}"),
    }

    src = args.data_dir / args.pair
    pairs = load_pairs(src / f"TED2020.{args.pair}.ko", src / f"TED2020.{args.pair}.{target_lang}")
    kept, stats = filter_pairs(pairs)
    chosen = sample(kept, args.size, args.seed)
    segments, excluded = build(chosen, target_lang, profiles)
    assert_clean(segments, profiles, target_lang)

    out = args.out_dir / f"{args.pair}.clean.json"
    dump_track(segments, out)

    print(f"원본 {stats.total:,}쌍 -> 필터 후 {stats.kept:,}쌍")
    for reason, count in sorted(stats.dropped.items()):
        print(f"  제거 {reason}: {count:,}")
    print(f"표본 {len(chosen):,} -> 트랙 {len(segments):,} (규격 미충족 제외 {excluded['unfittable']:,})")
    print(f"트랙 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_track_io.py -q`
Expected: **7 passed**

- [ ] **Step 6: 실데이터로 트랙을 만든다**

```bash
.venv/Scripts/python.exe bench/build_track.py --pair en-ko
.venv/Scripts/python.exe bench/build_track.py --pair ja-ko
```

**제외율을 읽는다.** 이 숫자가 §4.4의 부수 산출물이고 리포트에 실린다. `assert_clean`이 실패하면 진행하지 말고 원인을 찾는다.

- [ ] **Step 7: 커밋**

```bash
git add bench/track_io.py bench/build_track.py tests/test_bench_track_io.py
git commit -m "기능: 깨끗한 벤치마크 트랙 빌더 추가 (스펙 §4)"
```

---

## Task 5: 벤치 용어집 확정

**Files:**

- Create: `scripts/glossary_candidates.py`
- Create: `bench/glossary.ted.yaml`
- Create: `tests/test_bench_glossary.py`

**Interfaces:**

- Consumes: Task 4의 `data/bench/{pair}.clean.json`, `cuesift.glossary.load_glossary`
- Produces: `bench/glossary.ted.yaml` — `entries: [{source: <ko>, targets: {en: [...], ja: [...]}}]`

**왜 전자동이 아닌가**

통계 정렬만 쓰면 오대응이 섞이고, 그러면 **"용어집이 틀려서 생긴 오탐"과 "검출기 성능"을 구분할 수 없다.** 반대로 코퍼스를 보지 않고 손으로만 적으면 해당 용어가 코퍼스에 몇 번 등장하는지 보장되지 않아 주입 건수가 부족해진다(스펙 §5.6).

- [ ] **Step 1: 후보 추출 스크립트를 만든다**

```python
"""용어집 후보 추출 (설계 스펙 §5.6 1단계).

**이 스크립트의 출력은 후보일 뿐이다.** 사람이 확정한 것만 커밋한다 —
오대응이 섞이면 "용어집이 틀려서 생긴 오탐"과 "검출기 성능"을 구분할 수 없다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from bench.track_io import load_track

# 한글 2~6자 덩어리. 조사가 붙은 형태가 섞이지만 후보 목록이므로 사람이 거른다.
_KO_WORD = re.compile(r"[가-힣]{2,6}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="용어집 후보 추출")
    parser.add_argument("--track", type=Path, required=True)
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--min-count", type=int, default=20)
    args = parser.parse_args(argv)

    counter: Counter[str] = Counter()
    for seg in load_track(args.track):
        counter.update(_KO_WORD.findall(seg.source_text))

    print(f"# 후보 (등장 {args.min_count}회 이상, 상위 {args.top})")
    for word, count in counter.most_common(args.top):
        if count < args.min_count:
            break
        print(f"{count:>6}  {word}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 후보를 뽑아 사람이 확정한다**

```bash
.venv/Scripts/python.exe scripts/glossary_candidates.py --track data/bench/en-ko.clean.json
```

출력에서 **사전적으로 대응이 명확한 30~50개**를 고른다. 고르는 기준:

| 채택 | 기각 |
| --- | --- |
| 개념어·고유명사 (`기후`·`인공지능`·`유전자`) | 조사가 붙은 형태 (`우리는`·`그것이`) |
| en·ja 대응이 1:1에 가까운 것 | 문맥에 따라 대응이 갈리는 것 (`문제`) |
| 코퍼스 등장 20회 이상 | 등장 빈도가 낮은 것 — 주입 건수가 부족해진다 |

- [ ] **Step 3: `bench/glossary.ted.yaml`을 쓴다**

아래는 **형식 예시이자 시작점**이다. 실제 항목은 Step 2의 후보에서 확정하며, 최소 30개를 채운다.

```yaml
# TED2020 벤치마크 용어집 (설계 스펙 §5.6)
#
# 자동 후보 추출 + 수동 확정. 전자동 통계 정렬을 쓰지 않는 이유는 오대응이
# 섞이면 "용어집이 틀려서 생긴 오탐"과 "검출기 성능"을 구분할 수 없기 때문이다.
#
# 대응어가 여러 개면 하나만 나와도 통과다(cuesift.glossary의 판정 규칙).
# 그래서 흔한 표기 변형을 함께 적어야 정상 번역이 오탐으로 잡히지 않는다.
entries:
  - source: 기후 변화
    targets:
      en: [climate change, global warming]
      ja: [気候変動, 地球温暖化]
  - source: 인공지능
    targets:
      en: [artificial intelligence, AI]
      ja: [人工知能, AI]
  - source: 유전자
    targets:
      en: [gene, genes, genetic]
      ja: [遺伝子]
  - source: 알고리즘
    targets:
      en: [algorithm, algorithms]
      ja: [アルゴリズム]
  - source: 민주주의
    targets:
      en: [democracy, democratic]
      ja: [民主主義]
```

- [ ] **Step 4: 테스트를 쓴다**

```python
"""벤치 용어집 테스트 (설계 스펙 §5.6)."""

from __future__ import annotations

from pathlib import Path

from cuesift.glossary import load_glossary

GLOSSARY = Path("bench/glossary.ted.yaml")


def test_glossary_loads_for_both_target_languages():
    """대상 언어별로 대응어가 있어야 한다. 없으면 그 언어의 주입이 0건이 된다."""
    for lang in ("en", "ja"):
        g = load_glossary(GLOSSARY, lang)
        assert not g.is_empty, f"{lang} 대응어가 하나도 없다"


def test_glossary_has_enough_entries():
    """스펙 §5.6 — 30~50개.

    적으면 주입 건수가 부족해 "용어 위반 Recall 100%"가 실은
    "1건도 주입 못 했음"이 된다.
    """
    assert len(load_glossary(GLOSSARY, "en").entries) >= 30


def test_every_entry_has_both_languages():
    """한쪽 언어만 있으면 그 언어쌍에서만 조용히 항목이 줄어든다."""
    en_sources = {e.source for e in load_glossary(GLOSSARY, "en").entries}
    ja_sources = {e.source for e in load_glossary(GLOSSARY, "ja").entries}
    assert en_sources == ja_sources


def test_glossary_terms_actually_appear_in_the_corpus():
    """**코퍼스에 없는 용어는 주입 기회가 없다.**

    이 테스트는 트랙이 있을 때만 의미가 있으므로, 없으면 건너뛴다.
    """
    import pytest

    from bench.track_io import load_track

    track = Path("data/bench/en-ko.clean.json")
    if not track.exists():
        pytest.skip("트랙이 없다 — bench/build_track.py를 먼저 실행할 것")

    corpus = "\n".join(seg.source_text for seg in load_track(track))
    missing = [e.source for e in load_glossary(GLOSSARY, "en").entries if e.source not in corpus]
    assert not missing, f"코퍼스에 없는 용어: {missing}"
```

- [ ] **Step 5: 테스트를 실행한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_glossary.py -q`
Expected: **4 passed** (트랙이 없으면 1 skipped — `-ra`가 사유를 출력하므로 읽는다)

- [ ] **Step 6: 커밋**

```bash
git add scripts/glossary_candidates.py bench/glossary.ted.yaml tests/test_bench_glossary.py
git commit -m "기능: 벤치 용어집 확정 (스펙 §5.6)"
```

---

## Task 6: 오류 주입기

**Files:**

- Create: `bench/inject.py`
- Create: `tests/test_bench_inject.py`

**Interfaces:**

- Consumes: Task 4의 트랙, Task 5의 용어집, `cuesift.spec.text_width`
- Produces:
  - `@dataclass(frozen=True) Label(segment_id: str, kind: str, detail: dict)`
  - `INJECTORS: dict[str, Injector]` — 7종 레지스트리
  - `inject(segments, glossary, profile, *, rate=0.10, seed=...) -> tuple[list[Segment], list[Label], dict[str, int]]`
  - 반환 3번째는 자격 미달로 건너뛴 건수

**주입 유형 7종 (스펙 §5.2)**

| # | 키 | 방법 | 검출 담당 | hard fail |
| --- | --- | --- | --- | --- |
| 1 | `untranslated` | 번역문을 ko 원문으로 치환 | FR-3.1 | ✔ |
| 2 | `empty` | 번역문을 빈 문자열·공백으로 치환 | FR-3.2 | ✔ |
| 3 | `degeneration` | 마지막 어절을 3~8회 반복 | FR-3.3 | ✔ |
| 4 | `number` | 번역문의 숫자 1개를 변경·삭제 | FR-3.4 | ✔ |
| 5 | `glossary` | 용어집 대응어를 비등재 표현으로 치환 | FR-3.7 | ✘ |
| 6 | `spec` | duration 축소(CPS 초과) | FR-3.8 | ✘ |
| 7 | `negation` | 부정 표현 삽입·삭제 | **없음** | — |

**7번에 검출 담당이 없는 것은 오류가 아니다.** 부정어 하나가 뒤집힌 문장은 결정론적 코드로 원리상 구분되지 않는다. **이 유형의 Recall이 0에 수렴하는 것이 Tier 1·QE 투자를 정당화하는 근거 숫자**이며 이 측정의 부수 산출물이다(스펙 §5.4).

**제외 2건**: FR-3.5(태그 손실)는 TED2020이 평문이라 태그를 인위적으로 심으면 주입과 검출이 같은 가정을 공유해 자기 충족적이 된다 — 검출기는 켜 두고 **리포트에 미측정으로 표기**한다. FR-3.6(길이비)은 전용 주입 유형이 아니라 다른 오류의 부수 효과다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
"""오류 주입기 테스트 (설계 스펙 §5).

**라운드트립이 핵심이다** — 라벨이 틀리면 모든 숫자가 틀린다(스펙 §8).
"""

from __future__ import annotations

import pytest
from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment import Segment
from cuesift.spec import load_builtin

from bench.inject import INJECTORS, inject

PROFILE = load_builtin("ted-en")
GLOSSARY = Glossary(entries=(GlossaryEntry(source="기후", targets=("climate",)),))


def _track(n: int = 70) -> list[Segment]:
    segs = []
    for i in range(n):
        start = i * 4000
        segs.append(
            Segment(
                id=f"s{i:03d}",
                index=i,
                start_ms=start,
                end_ms=start + 3500,
                source_text=f"기후 변화 문제 {i} 번입니다",
                target_text=f"Climate issue number {i} is here",
            )
        )
    return segs


def test_registry_has_all_seven_types():
    """유형이 빠지면 그 유형의 Recall이 정의되지 않는데, 리포트는 조용히 넘어간다."""
    assert set(INJECTORS) == {
        "untranslated",
        "empty",
        "degeneration",
        "number",
        "glossary",
        "spec",
        "negation",
    }


def test_injection_does_not_mutate_the_input_track():
    """원본이 오염되면 '깨끗한 트랙 대비' 비교가 불가능해진다."""
    original = _track()
    before = [(s.target_text, s.end_ms) for s in original]
    inject(original, GLOSSARY, PROFILE, rate=0.10, seed=1)
    assert [(s.target_text, s.end_ms) for s in original] == before


def test_labels_are_exclusive_one_error_per_segment():
    """유형별 Recall이 정의되려면 라벨이 배타적이어야 한다(스펙 §5.5)."""
    _, labels, _ = inject(_track(), GLOSSARY, PROFILE, rate=0.10, seed=1)
    ids = [lb.segment_id for lb in labels]
    assert len(ids) == len(set(ids))


def test_injection_rate_is_respected():
    segs = _track(100)
    _, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=1)
    assert 5 <= len(labels) <= 15


def test_same_seed_gives_the_same_errors():
    """NFR-3 재현성 — 시드가 같은데 결과가 다르면 리포트를 재현할 수 없다."""
    a = inject(_track(), GLOSSARY, PROFILE, rate=0.10, seed=7)[1]
    b = inject(_track(), GLOSSARY, PROFILE, rate=0.10, seed=7)[1]
    assert [(x.segment_id, x.kind) for x in a] == [(y.segment_id, y.kind) for y in b]


@pytest.mark.parametrize("kind", sorted(INJECTORS))
def test_every_injector_actually_changes_the_segment(kind):
    """**라운드트립** — 라벨이 붙었는데 텍스트가 그대로면 그 라벨은 거짓이다.

    거짓 라벨은 분모를 부풀려 Recall을 낮추고, 원인이 검출기로 오인된다.
    """
    seg = Segment(
        id="s0",
        index=0,
        start_ms=0,
        end_ms=4000,
        source_text="기후 변화 문제 3 번입니다",
        target_text="Climate issue number 3 is here",
    )
    import random

    result = INJECTORS[kind](seg, GLOSSARY, PROFILE, random.Random(0))
    assert result is not None, f"{kind}: 자격을 갖춘 세그먼트인데 주입되지 않았다"
    mutated, detail = result
    changed = (mutated.target_text != seg.target_text) or (mutated.end_ms != seg.end_ms)
    assert changed, f"{kind}: 라벨만 붙고 실제 변화가 없다"
    assert isinstance(detail, dict)


def test_zero_actual_injection_is_a_failure():
    """**링크 체커에서 얻은 교훈의 직접 적용.**

    "0 broken"이 통과로 읽혔던 것처럼, "용어 위반 Recall 100%"가 실은
    "용어 위반을 1건도 주입 못 했음"일 수 있다. 용어집이 비면 정확히 그렇게 된다.
    """
    empty_glossary = Glossary(entries=())
    with pytest.raises(ValueError, match="glossary"):
        inject(_track(), empty_glossary, PROFILE, rate=0.10, seed=1)


def test_ineligible_segments_are_counted_not_silently_skipped():
    """자격 미달 건수를 세지 않으면 주입 부족이 드러나지 않는다."""
    no_numbers = [
        Segment(id=f"s{i}", index=i, start_ms=i * 4000, end_ms=i * 4000 + 3500,
                source_text="기후 변화 문제입니다", target_text="Climate issue is here")
        for i in range(70)
    ]
    _, _, skipped = inject(no_numbers, GLOSSARY, PROFILE, rate=0.10, seed=1)
    assert skipped.get("number", 0) > 0
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_inject.py -q`
Expected: 수집 오류

- [ ] **Step 3: `bench/inject.py`를 구현한다**

```python
"""③ inject — 오류 주입과 정답 라벨 (설계 스펙 §5).

**세그먼트당 최대 1개 오류.** 유형별 Recall이 정의되려면 라벨이 배타적이어야 한다.

**어떤 유형이든 실주입이 0건이면 실패시킨다.** 링크 체커에서 얻은 교훈의
직접 적용이다 — "0 broken"이 통과로 읽혔던 것처럼 "용어 위반 Recall 100%"가
실은 "1건도 주입 못 했음"일 수 있다.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from cuesift.glossary import Glossary
from cuesift.segment import Segment
from cuesift.spec import SpecProfile

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 부정 표현. 삽입·삭제 양방향으로 쓴다.
_NEGATIONS_EN = (" not ", " never ")

Injector = Callable[
    [Segment, Glossary, SpecProfile, random.Random], "tuple[Segment, dict] | None"
]


@dataclass(frozen=True, slots=True)
class Label:
    """정답 한 건. `detail`은 라운드트립 검증에 쓴다."""

    segment_id: str
    kind: str
    detail: dict = field(default_factory=dict)


def _untranslated(seg, glossary, profile, rng):
    """FR-3.1 — 번역문을 ko 원문으로 되돌린다."""
    return replace(seg, target_text=seg.source_text), {"replaced_with": "source"}


def _empty(seg, glossary, profile, rng):
    """FR-3.2 — 빈 값. 공백만 남기는 경우도 섞는다."""
    value = rng.choice(["", "   "])
    return replace(seg, target_text=value), {"value": value}


def _degeneration(seg, glossary, profile, rng):
    """FR-3.3 — 마지막 어절을 3~8회 반복한다."""
    tokens = (seg.target_text or "").split()
    if not tokens:
        return None
    times = rng.randint(3, 8)
    return replace(seg, target_text=" ".join(tokens + [tokens[-1]] * times)), {"repeats": times}


def _number(seg, glossary, profile, rng):
    """FR-3.4 — 숫자 1개를 변경하거나 삭제한다. **숫자가 없으면 자격 미달.**"""
    text = seg.target_text or ""
    matches = list(_NUMBER.finditer(text))
    if not matches:
        return None
    m = rng.choice(matches)
    original = m.group()
    if rng.random() < 0.5:
        digits = original.replace(",", "")
        try:
            changed = str(int(digits) + rng.randint(1, 9))
        except ValueError:
            changed = ""
    else:
        changed = ""
    mutated = text[: m.start()] + changed + text[m.end() :]
    return replace(seg, target_text=mutated), {"from": original, "to": changed}


def _glossary(seg, glossary, profile, rng):
    """FR-3.7 — 대응어를 비등재 표현으로 치환한다.

    **원문에 용어집 키가 있고 번역문에 대응어가 있는 세그먼트만 자격이 있다.**
    둘 중 하나만 있으면 치환할 대상이 없거나 이미 위반 상태다.
    """
    text = seg.target_text or ""
    for entry in glossary.entries:
        if entry.source not in seg.source_text:
            continue
        for target in entry.targets:
            idx = text.lower().find(target.lower())
            if idx < 0:
                continue
            bogus = "thingamajig"
            mutated = text[:idx] + bogus + text[idx + len(target) :]
            return replace(seg, target_text=mutated), {"term": entry.source, "from": target}
    return None


def _spec(seg, glossary, profile, rng):
    """FR-3.8 — duration을 줄여 CPS를 넘긴다.

    텍스트 확장이 아니라 duration 축소를 쓰는 이유는, 확장하면 길이비 신호가
    함께 발화해 **라벨의 배타성이 흐려지기** 때문이다.
    """
    shrunk = max(profile.min_duration_ms // 2, int(seg.duration_ms * 0.25))
    if shrunk >= seg.duration_ms:
        return None
    return replace(seg, end_ms=seg.start_ms + shrunk), {
        "from_ms": seg.duration_ms,
        "to_ms": shrunk,
    }


def _negation(seg, glossary, profile, rng):
    """의미 반전. **검출 담당이 없다** — 이 유형의 Recall 0이 Tier 1 투자 근거다."""
    text = seg.target_text or ""
    for neg in _NEGATIONS_EN:
        if neg in text:
            return replace(seg, target_text=text.replace(neg, " ", 1)), {"removed": neg.strip()}
    tokens = text.split()
    if len(tokens) < 2:
        return None
    tokens.insert(1, "not")
    return replace(seg, target_text=" ".join(tokens)), {"inserted": "not"}


INJECTORS: dict[str, Injector] = {
    "untranslated": _untranslated,
    "empty": _empty,
    "degeneration": _degeneration,
    "number": _number,
    "glossary": _glossary,
    "spec": _spec,
    "negation": _negation,
}


def inject(
    segments: Sequence[Segment],
    glossary: Glossary,
    profile: SpecProfile,
    *,
    rate: float = 0.10,
    seed: int = 20260729,
) -> tuple[list[Segment], list[Label], dict[str, int]]:
    """오류를 주입하고 정답 라벨을 만든다.

    **입력을 변형하지 않는다** — 원본이 오염되면 "깨끗한 트랙 대비" 비교가
    불가능해진다.
    """
    if not 0.0 < rate <= 1.0:
        raise ValueError(f"rate는 0보다 크고 1 이하여야 한다 (받은 값: {rate})")

    rng = random.Random(seed)
    kinds = sorted(INJECTORS)
    target_total = round(len(segments) * rate)

    order = list(range(len(segments)))
    rng.shuffle(order)

    mutated = list(segments)
    labels: list[Label] = []
    skipped: dict[str, int] = {k: 0 for k in kinds}
    achieved: dict[str, int] = {k: 0 for k in kinds}

    quota = {k: target_total // len(kinds) for k in kinds}
    for i in range(target_total - sum(quota.values())):
        quota[kinds[i % len(kinds)]] += 1

    for idx in order:
        remaining = [k for k in kinds if achieved[k] < quota[k]]
        if not remaining:
            break
        kind = remaining[0]
        result = INJECTORS[kind](mutated[idx], glossary, profile, rng)
        if result is None:
            skipped[kind] += 1
            continue
        mutated[idx], detail = result
        labels.append(Label(segment_id=mutated[idx].id, kind=kind, detail=detail))
        achieved[kind] += 1

    empty_kinds = [k for k in kinds if achieved[k] == 0]
    if empty_kinds:
        raise ValueError(
            f"실주입 0건인 유형이 있다: {', '.join(empty_kinds)}. "
            f"그대로 두면 '해당 유형 Recall 100%'가 실은 '1건도 주입 못 했음'이 된다. "
            f"자격 미달 건수: { {k: skipped[k] for k in empty_kinds} }"
        )

    return mutated, labels, skipped
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_inject.py -q`
Expected: **15 passed** (파라미터화 7건 포함)

- [ ] **Step 5: 커밋**

```bash
git add bench/inject.py tests/test_bench_inject.py
git commit -m "기능: 오류 주입기 7종 추가 (스펙 §5)"
```

---

## Task 7: 측정과 불변식

**Files:**

- Create: `bench/measure.py`
- Create: `tests/test_bench_measure.py`

**Interfaces:**

- Consumes: Task 6의 `(mutated, labels)`, `cuesift.signals.collect_all`, `cuesift.risk.fuse`, `cuesift.triage.{select_by_budget, review_ratio}`
- Produces:
  - `@dataclass(frozen=True) BudgetResult(budget, review_ratio, recall, lift, oracle, by_kind: dict[str, float])`
  - `measure(segments, labels, ctx, budgets, *, enabled=None) -> list[BudgetResult]`
  - `random_baseline(n, ratio, seed_count=100) -> tuple[float, float]` — `(평균, 표준편차)`
  - `check_invariants(results, labels, segments, risks) -> None` — 위반 시 `ValueError`
  - `ablation(segments, labels, ctx, budget) -> dict[str, float]` — 신호별 Recall 하락폭

**측정 코드의 진짜 위험은 틀린 숫자가 그럴듯해 보인다는 것이다.** 그래서 매 실행마다 불변식 4개를 검사하고, 위반 시 결과를 내지 않고 실패시킨다(스펙 §6.4).

| # | 불변식 | 위반이 뜻하는 것 |
| --- | --- | --- |
| 1 | Recall ≤ 오라클 상한 | 라벨 누수 — 검출기가 정답 파일을 보고 있다 |
| 2 | Recall(무작위) ≈ 실제 비율 | 선별·집계 로직 버그 |
| 3 | 예산 증가 시 Recall 비감소 | 정렬·절단 로직 버그 |
| 4 | 주입 안 된 세그먼트의 hard fail = 0 | §9.1의 "hard-fail 오탐 ≈ 0"이 깨졌다 |

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
"""측정과 불변식 테스트 (설계 스펙 §6)."""

from __future__ import annotations

import pytest
from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals import SignalContext
from cuesift.spec import load_builtin

from bench.inject import Label, inject
from bench.measure import BudgetResult, ablation, check_invariants, measure, random_baseline

PROFILE = load_builtin("ted-en")
GLOSSARY = Glossary(entries=(GlossaryEntry(source="기후", targets=("climate",)),))
CTX = SignalContext(profile=PROFILE, glossary=GLOSSARY, source_lang="ko", target_lang="en")


def _clean_track(n: int = 200) -> list[Segment]:
    segs = []
    for i in range(n):
        start = i * 5000
        segs.append(
            Segment(
                id=f"s{i:03d}",
                index=i,
                start_ms=start,
                end_ms=start + 4500,
                source_text=f"기후 변화 문제 {i} 번을 봅니다",
                target_text=f"We look at climate issue {i} today",
            )
        )
    return segs


def test_random_baseline_matches_the_ratio():
    """기댓값은 비율이지만 실측한다. 어긋나면 집계 로직이 틀렸다."""
    mean, stdev = random_baseline(n=1000, ratio=0.10, seed_count=100)
    assert abs(mean - 0.10) < 0.03
    assert stdev >= 0.0


def test_recall_is_monotonic_in_budget():
    """예산을 늘렸는데 Recall이 떨어지면 정렬·절단 로직 버그다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    results = measure(mutated, labels, CTX, [0.01, 0.05, 0.10, 0.20, 0.30])
    recalls = [r.recall for r in results]
    assert recalls == sorted(recalls)


def test_recall_never_exceeds_the_oracle():
    """초과하면 라벨 누수다 — 검출기가 정답을 보고 있다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    for r in measure(mutated, labels, CTX, [0.01, 0.05, 0.10, 0.20]):
        assert r.recall <= r.oracle + 1e-9


def test_lift_uses_actual_review_ratio_not_requested_budget():
    """**여기서 부풀리면 프로젝트의 핵심 주장이 무너진다**(스펙 §6.2).

    hard fail이 예산을 우회하므로 요청 예산과 실제 비율은 다르다.
    """
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    r = measure(mutated, labels, CTX, [0.01])[0]
    assert r.review_ratio >= 0.01
    assert r.lift == pytest.approx(r.recall / r.review_ratio)


def test_invariant_violation_raises_instead_of_reporting():
    """**게이트를 만들면 반드시 실패시켜 본다.**

    불변식이 통과만 하는 것을 확인해서는 그것이 무엇을 잡는지 알 수 없다.
    """
    bogus = [
        BudgetResult(budget=0.10, review_ratio=0.10, recall=0.95, lift=9.5, oracle=0.5, by_kind={}),
    ]
    with pytest.raises(ValueError, match="오라클"):
        check_invariants(bogus, labels=[], segments=[], risks=[])


def test_invariant_catches_non_monotonic_recall():
    results = [
        BudgetResult(budget=0.05, review_ratio=0.05, recall=0.60, lift=12.0, oracle=1.0, by_kind={}),
        BudgetResult(budget=0.10, review_ratio=0.10, recall=0.40, lift=4.0, oracle=1.0, by_kind={}),
    ]
    with pytest.raises(ValueError, match="단조"):
        check_invariants(results, labels=[], segments=[], risks=[])


def test_invariant_catches_hard_fail_on_clean_segments():
    """깨끗한 트랙에서 주입하지 않은 세그먼트가 hard fail이면 정의상 오탐이다."""
    segments = [
        Segment(id="clean", index=0, start_ms=0, end_ms=4000, source_text="가나다", target_text="abc")
    ]
    risks = [SegmentRisk(segment_id="clean", signals=[], risk_score=1.0, hard_fail=True)]
    with pytest.raises(ValueError, match="hard fail"):
        check_invariants([], labels=[], segments=segments, risks=risks)


def test_by_kind_recall_covers_every_injected_type():
    """유형별 Recall이 빠지면 negation의 0이 보이지 않는다 — 그게 Tier 1 근거 숫자다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    r = measure(mutated, labels, CTX, [0.20])[0]
    assert set(r.by_kind) == {lb.kind for lb in labels}


def test_ablation_reports_a_number_for_every_signal():
    """신호별 기여도. 오타로 신호를 껐는데 '기여도 0'으로 읽히면 안 된다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    from cuesift.signals import registry

    drops = ablation(mutated, labels, CTX, budget=0.10)
    assert set(drops) == set(registry())
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_measure.py -q`
Expected: 수집 오류

- [ ] **Step 3: `bench/measure.py`를 구현한다**

```python
"""④ measure — Recall@Budget 측정 (설계 스펙 §6).

**제품 모듈을 호출할 뿐 자체 판정 로직을 갖지 않는다.** 벤치가 자기 신호
계산을 가지면 "측정한 것"과 "출시하는 것"이 갈라진다.

**배수는 요청 예산이 아니라 `review_ratio()`가 낸 실제 검수 비율로 나눈다**
(§6.2). hard fail이 예산을 우회하므로 둘은 다르고, 요청 예산으로 나누면
README 최상단 숫자가 부풀려진다.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from cuesift.risk import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals import SignalContext, collect_all, registry
from cuesift.triage import review_ratio, select_by_budget

from bench.inject import Label

# 스펙 §6.1 — 무작위 베이스라인은 기댓값이 b지만 실측한다.
BASELINE_SEEDS = 100


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """예산 하나에서의 결과. 세 열(요청·실제·Recall)을 **항상 함께** 낸다."""

    budget: float
    review_ratio: float
    recall: float
    lift: float
    oracle: float
    by_kind: dict[str, float] = field(default_factory=dict)


def _risks(
    segments: Sequence[Segment], ctx: SignalContext, enabled: Iterable[str] | None
) -> list[SegmentRisk]:
    signals = collect_all(segments, ctx, enabled=enabled)
    return [fuse(seg.id, signals[seg.id]) for seg in segments]


def random_baseline(n: int, ratio: float, seed_count: int = BASELINE_SEEDS) -> tuple[float, float]:
    """같은 비율을 무작위로 뽑았을 때의 포착률. `(평균, 표준편차)`.

    기댓값은 `ratio`지만 **실측한다** — 계산과 실측이 어긋나면 집계 로직이
    틀린 것이고, 그 경우 배수의 분모가 통째로 의심스러워진다.
    """
    if n <= 0:
        return 0.0, 0.0
    take = round(n * ratio)
    hits = []
    for seed in range(seed_count):
        rng = random.Random(seed)
        chosen = set(rng.sample(range(n), take)) if take else set()
        hits.append(len(chosen) / n)
    return statistics.fmean(hits), statistics.pstdev(hits)


def measure(
    segments: Sequence[Segment],
    labels: Sequence[Label],
    ctx: SignalContext,
    budgets: Sequence[float],
    *,
    enabled: Iterable[str] | None = None,
) -> list[BudgetResult]:
    """예산 스윕. 같은 위험도 목록에 여러 예산을 적용한다."""
    risks = _risks(segments, ctx, enabled)
    error_ids = {lb.segment_id for lb in labels}
    kinds = {lb.segment_id: lb.kind for lb in labels}
    total_errors = len(error_ids)

    results: list[BudgetResult] = []
    for budget in budgets:
        selected = select_by_budget(risks, budget)
        actual = review_ratio(selected)
        caught = {r.segment_id for r in selected if r.selected} & error_ids

        recall = len(caught) / total_errors if total_errors else 0.0
        error_rate = total_errors / len(segments) if segments else 0.0
        oracle = min(1.0, actual / error_rate) if error_rate else 0.0

        by_kind: dict[str, float] = {}
        for kind in sorted(set(kinds.values())):
            of_kind = {sid for sid, k in kinds.items() if k == kind}
            by_kind[kind] = len(caught & of_kind) / len(of_kind) if of_kind else 0.0

        results.append(
            BudgetResult(
                budget=budget,
                review_ratio=actual,
                recall=recall,
                # 실제 비율로 나눈다. 요청 예산으로 나누면 숫자가 부풀려진다.
                lift=recall / actual if actual else 0.0,
                oracle=oracle,
                by_kind=by_kind,
            )
        )
    return results


def ablation(
    segments: Sequence[Segment],
    labels: Sequence[Label],
    ctx: SignalContext,
    budget: float,
) -> dict[str, float]:
    """신호를 하나씩 빼고 Recall 하락폭을 잰다.

    `spec.overlap`도 포함된다 — 재리뷰가 이 신호의 캐스케이드(단일 타임코드
    오타가 트랙 절반 이상을 flag)와 가중평균 희석(soft 신호를 최대 0.25 끌어내림)을
    실측했으므로, **A/B 대상 목록에서 빠지면 안 된다.**
    """
    names = sorted(registry())
    full = measure(segments, labels, ctx, [budget])[0].recall
    drops: dict[str, float] = {}
    for name in names:
        without = [n for n in names if n != name]
        drops[name] = full - measure(segments, labels, ctx, [budget], enabled=without)[0].recall
    return drops


def check_invariants(
    results: Sequence[BudgetResult],
    labels: Sequence[Label],
    segments: Sequence[Segment],
    risks: Sequence[SegmentRisk],
) -> None:
    """스펙 §6.4의 불변식 4개. **위반이면 결과를 내지 않는다.**

    측정 코드의 진짜 위험은 틀린 숫자가 그럴듯해 보인다는 것이다.
    """
    for r in results:
        if r.recall > r.oracle + 1e-9:
            raise ValueError(
                f"불변식 1 위반 — 예산 {r.budget}에서 Recall({r.recall:.4f})이 "
                f"오라클 상한({r.oracle:.4f})을 넘었다. 라벨 누수를 의심할 것."
            )

    for prev, curr in zip(results, results[1:], strict=False):
        if curr.recall < prev.recall - 1e-9:
            raise ValueError(
                f"불변식 3 위반 — 단조성이 깨졌다. 예산 {prev.budget}에서 {prev.recall:.4f}, "
                f"{curr.budget}에서 {curr.recall:.4f}. 정렬·절단 로직을 볼 것."
            )

    error_ids = {lb.segment_id for lb in labels}
    false_hard = [r.segment_id for r in risks if r.hard_fail and r.segment_id not in error_ids]
    if false_hard:
        raise ValueError(
            f"불변식 4 위반 — 주입하지 않은 세그먼트 {len(false_hard)}건이 hard fail이다. "
            f"정의상 오탐이며 실제 검수 비율을 부풀려 배수를 파괴한다. "
            f"예: {false_hard[:5]}"
        )

    for r in results:
        mean, stdev = random_baseline(len(segments), r.review_ratio)
        # 3σ 밴드. 표준편차가 0인 경우(표본이 작아 항상 같은 수를 뽑음)를 위해 하한을 둔다.
        band = max(3 * stdev, 0.02)
        if abs(mean - r.review_ratio) > band:
            raise ValueError(
                f"불변식 2 위반 — 무작위 베이스라인({mean:.4f})이 실제 비율"
                f"({r.review_ratio:.4f})과 다르다. 선별·집계 로직을 볼 것."
            )
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_measure.py -q`
Expected: **10 passed**

- [ ] **Step 5: 커밋**

```bash
git add bench/measure.py tests/test_bench_measure.py
git commit -m "기능: Recall@Budget 측정과 불변식 4종 추가 (스펙 §6)"
```

---

## Task 8: 리포트

**Files:**

- Create: `bench/report.py`
- Create: `tests/test_bench_report.py`

**Interfaces:**

- Consumes: Task 7의 `BudgetResult`·`ablation`, Task 1의 manifest
- Produces:
  - `@dataclass(frozen=True) RunMeta(pair, seed, manifest_sha256, commit, sample_size, excluded, injected, unmeasured)`
  - `render_markdown(meta: RunMeta, results, drops, baseline) -> str`
  - `write_report(meta, results, drops, baseline, out_dir: Path) -> tuple[Path, Path]`

**재현 정보를 헤더에 박는다.** `Recall@10% = 0.62`만 적힌 리포트는 몇 달 뒤 자기 자신조차 검증할 수 없다. **재현 불가능한 벤치마크 숫자는 없는 것보다 나쁘다 — 인용되기 때문이다**(스펙 §7).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
"""리포트 테스트 (설계 스펙 §7)."""

from __future__ import annotations

import json

from bench.measure import BudgetResult
from bench.report import RunMeta, render_markdown, write_report

META = RunMeta(
    pair="en-ko",
    seed=20260729,
    manifest_sha256="a" * 64,
    commit="deadbeef",
    sample_size=5000,
    excluded={"unfittable": 12},
    injected={"untranslated": 71, "negation": 71},
    unmeasured=("struct.tag_lost",),
)
RESULTS = [
    BudgetResult(budget=0.05, review_ratio=0.062, recall=0.55, lift=8.9, oracle=0.62, by_kind={"negation": 0.0}),
    BudgetResult(budget=0.10, review_ratio=0.104, recall=0.73, lift=7.0, oracle=1.0, by_kind={"negation": 0.0}),
]
DROPS = {"struct.untranslated": 0.21, "spec.overlap": 0.0}
BASELINE = {0.05: (0.05, 0.006), 0.10: (0.10, 0.009)}


def test_report_contains_reproduction_header():
    """**헤더가 없으면 몇 달 뒤 자기 자신조차 검증할 수 없다.**"""
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    for needed in ("20260729", "a" * 16, "deadbeef", "5,000"):
        assert needed in md


def test_report_shows_all_three_columns_together():
    """요청 예산·실제 검수 비율·Recall을 항상 함께 낸다(스펙 §6.2).

    실제 비율을 빼면 배수가 어떻게 계산됐는지 독자가 검증할 수 없다.
    """
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "요청 예산" in md and "실제 검수" in md and "Recall" in md


def test_report_states_weights_are_untuned():
    """스펙 §6.3 — 첫 리포트에 '가중치 미튜닝'을 명시한다."""
    assert "미튜닝" in render_markdown(META, RESULTS, DROPS, BASELINE)


def test_report_lists_unmeasured_signals():
    """FR-3.5는 이번 측정에서 빠진다. 표기하지 않으면 '검출 실패'로 읽힌다."""
    assert "struct.tag_lost" in render_markdown(META, RESULTS, DROPS, BASELINE)


def test_report_records_actual_injection_counts():
    """'용어 위반 Recall 100%'가 실은 '1건도 주입 못 했음'일 수 있다."""
    md = render_markdown(META, RESULTS, DROPS, BASELINE)
    assert "71" in md


def test_write_report_emits_markdown_and_json(tmp_path):
    md_path, json_path = write_report(META, RESULTS, DROPS, BASELINE, tmp_path)
    assert md_path.exists() and json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["meta"]["pair"] == "en-ko"
    assert len(payload["results"]) == 2
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_report.py -q`
Expected: 수집 오류

- [ ] **Step 3: `bench/report.py`를 구현한다**

```python
"""⑤ report — 결과 리포트 (설계 스펙 §7).

**재현 정보를 헤더에 박는다.** `Recall@10% = 0.62`만 적힌 리포트는 몇 달 뒤
자기 자신조차 검증할 수 없다. 재현 불가능한 벤치마크 숫자는 없는 것보다
나쁘다 — 인용되기 때문이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from bench.measure import BudgetResult


@dataclass(frozen=True, slots=True)
class RunMeta:
    """이 숫자를 재현하는 데 필요한 전부."""

    pair: str
    seed: int
    manifest_sha256: str
    commit: str
    sample_size: int
    excluded: dict[str, int]
    injected: dict[str, int]
    unmeasured: tuple[str, ...]


def render_markdown(
    meta: RunMeta,
    results: Sequence[BudgetResult],
    drops: Mapping[str, float],
    baseline: Mapping[float, tuple[float, float]],
) -> str:
    lines: list[str] = [
        f"# 벤치마크 결과 — {meta.pair}",
        "",
        f"> 측정일: {date.today().isoformat()}",
        "",
        "## 재현 정보",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 언어쌍 | `{meta.pair}` |",
        f"| 시드 | `{meta.seed}` |",
        f"| 코퍼스 SHA-256 | `{meta.manifest_sha256[:16]}…` |",
        f"| 코드 커밋 | `{meta.commit}` |",
        f"| 표본 수 | {meta.sample_size:,} |",
        "",
        "**가중치 미튜닝** — 스펙 §6.3에 따라 균등 가중으로 첫 숫자를 낸다. "
        "같은 데이터에서 맞춘 가중치는 새 데이터에서 재현되지 않는다.",
        "",
        "### 제외 건수",
        "",
        "| 사유 | 건수 |",
        "| --- | --- |",
    ]
    for reason, count in sorted(meta.excluded.items()):
        lines.append(f"| {reason} | {count:,} |")

    lines += ["", "### 유형별 실주입 건수", "", "| 유형 | 건수 |", "| --- | --- |"]
    for kind, count in sorted(meta.injected.items()):
        lines.append(f"| `{kind}` | {count:,} |")

    if meta.unmeasured:
        lines += [
            "",
            "### 미측정 신호",
            "",
            "TED2020은 평문이라 마크업이 없다. 태그를 인위적으로 심으면 주입과 검출이 "
            "같은 가정을 공유해 측정이 자기 충족적이 된다(스펙 §5.3). "
            "**검출기는 구현돼 있으나 이번 측정에서 빠졌다 — '검출 실패'가 아니다.**",
            "",
        ]
        for name in meta.unmeasured:
            lines.append(f"- `{name}`")

    lines += [
        "",
        "## Recall @ Budget",
        "",
        "**배수는 요청 예산이 아니라 실제 검수 비율로 나눈다.** hard fail이 예산을 "
        "우회하므로 둘은 다르고, 요청 예산으로 나누면 숫자가 부풀려진다(스펙 §6.2).",
        "",
        "| 요청 예산 | 실제 검수 | Recall | 무작위 기준 | 오라클 상한 | **배수** |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        mean, stdev = baseline.get(r.budget, (r.review_ratio, 0.0))
        lines.append(
            f"| {r.budget:.0%} | {r.review_ratio:.1%} | **{r.recall:.1%}** | "
            f"{mean:.1%} ±{stdev:.1%} | {r.oracle:.1%} | **{r.lift:.1f}x** |"
        )

    kinds = sorted({k for r in results for k in r.by_kind})
    if kinds:
        lines += ["", "## 유형별 Recall", "", "| 예산 | " + " | ".join(f"`{k}`" for k in kinds) + " |"]
        lines.append("| --- |" + " --- |" * len(kinds))
        for r in results:
            cells = " | ".join(f"{r.by_kind.get(k, 0.0):.1%}" for k in kinds)
            lines.append(f"| {r.budget:.0%} | {cells} |")
        lines += [
            "",
            "`negation`에 검출 담당이 없는 것은 오류가 아니다. 부정어 하나가 뒤집힌 문장은 "
            "결정론적 코드로 원리상 구분되지 않는다. **이 유형의 Recall이 0에 수렴하는 것이 "
            "Tier 1·QE 투자를 정당화하는 근거 숫자**다(스펙 §5.4).",
        ]

    lines += ["", "## 신호별 기여도 (ablation)", "", "| 신호 | Recall 하락폭 |", "| --- | --- |"]
    for name, drop in sorted(drops.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{name}` | {drop:+.1%} |")

    lines.append("")
    return "\n".join(lines)


def write_report(
    meta: RunMeta,
    results: Sequence[BudgetResult],
    drops: Mapping[str, float],
    baseline: Mapping[float, tuple[float, float]],
    out_dir: Path,
) -> tuple[Path, Path]:
    """`{pair}-{date}.md`와 같은 이름의 `.json`을 낸다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta.pair}-{date.today().isoformat()}"

    md_path = out_dir / f"{stem}.md"
    md_path.write_text(render_markdown(meta, results, drops, baseline), encoding="utf-8")

    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(
            {
                "meta": asdict(meta),
                "results": [asdict(r) for r in results],
                "ablation": dict(drops),
                "baseline": {str(k): list(v) for k, v in baseline.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_report.py -q`
Expected: **6 passed**

- [ ] **Step 5: 커밋**

```bash
git add bench/report.py tests/test_bench_report.py
git commit -m "기능: 벤치마크 리포트 생성 추가 (스펙 §7)"
```

---

## Task 9: 실측과 문서 반영

**Files:**

- Create: `bench/run.py` (①~⑤ 오케스트레이션)
- Create: `bench/results/{pair}-{date}.md` · `.json` (**결과는 커밋한다** — 가공 코퍼스가 아니라 숫자다)
- Modify: `README.md` (최상단 배수 숫자)
- Modify: `docs/요구사항정의서.md` §12 Q4
- Modify: `docs/AI_자막검수_오픈소스_비교.md` §5 #2
- Modify: `CHANGELOG.md`

- [ ] **Step 1: `bench/run.py`를 만든다**

```python
"""벤치마크 전체 실행 (설계 스펙 §10 4~7단계)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from cuesift.glossary import load_glossary
from cuesift.signals import SignalContext
from cuesift.spec import load_builtin

from bench.inject import inject
from bench.measure import ablation, check_invariants, measure, random_baseline
from bench.report import RunMeta, write_report
from bench.track_io import load_track
from scripts.fetch_ted2020 import load_manifest

BUDGETS = (0.01, 0.02, 0.05, 0.10, 0.20, 0.30)
# FR-3.5는 이번 측정에서 빠진다(스펙 §5.3). 리포트에 미측정으로 표기한다.
UNMEASURED = ("struct.tag_lost",)


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="벤치마크 전체 실행")
    parser.add_argument("--pair", required=True, choices=["en-ko", "ja-ko"])
    parser.add_argument("--track", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--out-dir", type=Path, default=Path("bench/results"))
    args = parser.parse_args(argv)

    target_lang = args.pair.split("-")[0]
    track_path = args.track or Path(f"data/bench/{args.pair}.clean.json")
    profile = load_builtin(f"ted-{target_lang}")
    glossary = load_glossary(Path("bench/glossary.ted.yaml"), target_lang)
    ctx = SignalContext(
        profile=profile, glossary=glossary, source_lang="ko", target_lang=target_lang
    )

    segments = load_track(track_path)
    mutated, labels, skipped = inject(
        segments, glossary, profile, rate=args.rate, seed=args.seed
    )

    results = measure(mutated, labels, ctx, list(BUDGETS))
    from cuesift.risk import fuse
    from cuesift.signals import collect_all

    signals = collect_all(mutated, ctx)
    risks = [fuse(s.id, signals[s.id]) for s in mutated]
    check_invariants(results, labels, mutated, risks)

    drops = ablation(mutated, labels, ctx, budget=0.10)
    baseline = {r.budget: random_baseline(len(mutated), r.review_ratio) for r in results}

    injected: dict[str, int] = {}
    for lb in labels:
        injected[lb.kind] = injected.get(lb.kind, 0) + 1

    manifest = load_manifest(Path("bench/manifest.json"))
    meta = RunMeta(
        pair=args.pair,
        seed=args.seed,
        manifest_sha256=manifest.get(args.pair, {}).get("sha256", "unknown"),
        commit=_commit(),
        sample_size=len(segments),
        excluded=dict(skipped),
        injected=injected,
        unmeasured=UNMEASURED,
    )
    md_path, json_path = write_report(meta, results, drops, baseline, args.out_dir)
    print(f"리포트 -> {md_path}\n         {json_path}")
    for r in results:
        print(f"  예산 {r.budget:.0%}  실제 {r.review_ratio:.1%}  Recall {r.recall:.1%}  배수 {r.lift:.1f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 전 게이트를 돌린다**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests bench scripts
.venv/Scripts/python.exe -m ruff format --check src tests bench scripts
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**"통과했나"가 아니라 "무엇을 대상으로 통과했나"를 읽는다.** pytest 수집 개수, markdownlint의 `Linting: N files`, 링크 체커의 상대 링크 개수를 매번 확인한다.

- [ ] **Step 3: 두 언어쌍을 실측한다**

```bash
.venv/Scripts/python.exe bench/run.py --pair en-ko
.venv/Scripts/python.exe bench/run.py --pair ja-ko
```

불변식이 걸리면 **결과를 쓰지 말고 원인을 찾는다.** 특히 불변식 4(주입 안 된 세그먼트의 hard fail)가 걸리면 Task 4의 `assert_clean`이 통과했는데도 hard fail이 났다는 뜻이므로, 구조 신호(FR-3.1~3.5) 중 하나가 정상 텍스트에서 발화한 것이다.

- [ ] **Step 4: 결과를 문서에 반영한다**

| 문서 | 반영 내용 |
| --- | --- |
| `README.md` 최상단 | **배수 숫자.** 실제 검수 비율로 나눈 값이며 요청 예산이 아니다 |
| `docs/요구사항정의서.md` §12 Q4 | Tier 0 측정 결과를 근거로 상태 갱신 |
| `docs/AI_자막검수_오픈소스_비교.md` §5 #2 | 같은 결과. **두 문서가 서로를 링크하므로 한쪽만 갱신하면 독자가 상반된 두 주장을 만난다** |
| `CHANGELOG.md` | Keep a Changelog 형식으로 Unreleased에 추가 |
| `HANDOFF.md` | 세션 인수인계 갱신 |

- [ ] **Step 5: 커밋**

```bash
git add bench/run.py bench/results README.md CHANGELOG.md HANDOFF.md docs/
git commit -m "측정: Tier 0 Recall@Budget 첫 실측치와 문서 반영 (스펙 §10 7단계)"
```

---

## 벤치가 판정할 항목 (계획 A에서 넘어온 것)

계획 A의 재리뷰가 남긴 미결 항목이다. **이 목록이 이 계획의 존재 이유 중 하나다.**

| 항목 | 무엇을 A/B할 것인가 | 왜 지금 판정할 수 없었나 |
| --- | --- | --- |
| **`spec.overlap` 캐스케이드** | `ablation()`이 이 신호 on/off를 이미 낸다. 하락폭이 음수(= 끄는 편이 낫다)로 나오는지 본다 | 단일 `end_ms` 오타가 트랙 50~97%를 flag하고, 가중평균 분모를 늘려 soft 신호를 최대 0.25 끌어내린다. **결백한 세그먼트를 올리고 진짜 오역을 내리는 양방향 오염**이다 |
| **가중평균 vs noisy-or** | 융합 규칙을 바꾼 브랜치와 Recall 비교 | 계획 A가 "20시드 평균 예산 10%에서 5.2%p 손실"을 측정했으나 **그 값은 `spec.overlap` 연결 이전이다.** 낡은 기준선을 유효한 것으로 읽으면 안 된다 |
| **`_RATIO_MIN_SOURCE_WIDTH` = 4.0** | 상수를 스윕해 오탐·미탐 곡선을 그린다 | 근거 수치("400건 트랙 33건→0건")가 **현재 저장소에서 재현 불가능하다** — 오류 주입기가 없어 일회성 스크립트로 측정했다. 이 계획이 그 주입기를 만든다 |
| **D-24 완전 사각지대** | 하한 미만 원문에서 놓치는 오류의 정량 | 표시시간이 길면 CPS도 통과해 9종 어느 것도 발화하지 않는 조합이 있다 |

---

## Self-Review

**1. 스펙 coverage**

| 스펙 절 | 담당 태스크 |
| --- | --- |
| §3 fetch (대상·라이선스·manifest·stdlib) | Task 1 |
| §4.1 표본 5000·시드 | Task 2 `sample`, Task 4 `SAMPLE_SIZE` |
| §4.2 타임코드 합성·2줄 분할·제외 | Task 3 |
| §4.3 필터 | Task 2 `filter_pairs` |
| §4.4 제외 건수 산출물 | Task 4 `build` 반환값 → Task 8 리포트 |
| §5.2 주입 7종 | Task 6 `INJECTORS` |
| §5.3 제외 2건 (FR-3.5·3.6) | Task 9 `UNMEASURED` → 리포트 표기 |
| §5.4 negation의 Recall 0 | Task 8 리포트 문구 |
| §5.5 주입 규칙 (배타·10%·시드·자격·0건 실패) | Task 6 `inject` |
| §5.6 용어집 | Task 5 |
| §5.7 산출물 | Task 4 `track_io`, Task 6 라벨 |
| §6.1 지표 6종·예산 스윕 | Task 7 |
| §6.2 요청 예산 ≠ 실제 비율 | Task 7 `lift`, Task 8 3열 표 |
| §6.3 무튜닝 | Task 8 리포트 문구 |
| §6.4 불변식 4종 | Task 7 `check_invariants` |
| §7 리포트 재현 헤더 | Task 8 `RunMeta` |
| §8 검증 계층 4종 | 단위=각 태스크, 라운드트립=Task 6, 실패경로=Task 4·7, 벤치마크=Task 9 |

**빠진 것 없음.** §9(산출물 목록)의 `specs/ted.yaml`은 이미 `specs/ted-ko.yaml`·`ted-en.yaml`·`ted-ja.yaml`로 분리 구현돼 있어(Q5 확정) 이 계획의 작업 대상이 아니다.

**2. Placeholder 스캔**

TODO·TBD·"적절히 처리"·"위 내용의 테스트" 없음. 모든 코드 단계에 실제 코드 블록이 있다. Task 5의 YAML만 "형식 예시이자 시작점"인데, 이는 **코퍼스를 보고 사람이 확정해야 하는 항목**이라 계획이 값을 확정할 수 없다 — Step 2에 선택 기준 표를 두어 판단 근거를 남겼다.

**3. 타입 일관성**

| 이름 | 정의 | 소비 |
| --- | --- | --- |
| `SentencePair(source, target)` | Task 2 | Task 3 `plan_segment`, Task 4 `build` |
| `TimedText(source_text, target_text, duration_ms)` | Task 3 | Task 4 `build` |
| `Label(segment_id, kind, detail)` | Task 6 | Task 7 `measure`, Task 9 |
| `BudgetResult(budget, review_ratio, recall, lift, oracle, by_kind)` | Task 7 | Task 8 `render_markdown` |
| `RunMeta(...)` | Task 8 | Task 9 `run.py` |
| `load_manifest` | Task 1 | Task 9 `run.py` |
| `load_track` / `dump_track` | Task 4 | Task 5 후보추출, Task 9 |

`GAP_MS`·`SAFETY`는 Task 3에서 정의하고 Task 4가 임포트한다. Task 4의 `from bench.timing import GAP_MS, SentencePair, plan_segment`는 `SentencePair`를 `bench.timing` 경유로 가져오는데, **`bench.timing`이 이미 `bench.corpus`에서 임포트하므로 유효하다** — 다만 구현자는 ruff의 `F401` 경고를 피하려 `bench.corpus`에서 직접 가져와도 된다.
