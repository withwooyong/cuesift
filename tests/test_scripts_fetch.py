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
