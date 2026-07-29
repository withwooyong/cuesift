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
