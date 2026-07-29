"""트랙 직렬화·빌더 테스트 (설계 스펙 §4.2, §4.4, §5.7)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from bench.build_track import assert_clean, build
from bench.corpus import SentencePair, sample
from bench.inject import Label
from bench.timing import plan_segment
from bench.track_io import dump_audit, dump_track, load_labels, load_track

from cuesift.segment import Segment
from cuesift.spec import load_builtin

PROFILES = {
    "ko": load_builtin("ted-ko"),
    "en": load_builtin("ted-en"),
    "ja": load_builtin("ted-ja"),
}


def test_track_roundtrips_through_json(tmp_path):
    segs = [
        Segment(id="s0", index=0, start_ms=0, end_ms=1500, source_text="안녕", target_text="Hi"),
        Segment(
            id="s1", index=1, start_ms=1620, end_ms=3000, source_text="세계", target_text="World"
        ),
    ]
    path = tmp_path / "t.json"
    dump_track(segs, path)
    assert load_track(path) == segs


def test_roundtrip_preserves_newlines_in_text(tmp_path):
    """2줄 세그먼트의 줄바꿈이 유실되면 줄 길이 검사가 통째로 달라진다."""
    segs = [
        Segment(
            id="s0",
            index=0,
            start_ms=0,
            end_ms=2000,
            source_text="첫 줄\n둘째 줄",
            target_text="a\nb",
        )
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
        Segment(
            id="x", index=0, start_ms=0, end_ms=200, source_text="가" * 50, target_text="a" * 200
        )
    ]
    with pytest.raises(AssertionError):
        assert_clean(dirty, PROFILES, "en")


def test_assert_clean_catches_overlap():
    overlapping = [
        Segment(id="a", index=0, start_ms=0, end_ms=2000, source_text="안녕", target_text="Hi"),
        Segment(
            id="b", index=1, start_ms=1500, end_ms=3000, source_text="세계", target_text="World"
        ),
    ]
    with pytest.raises(AssertionError, match="겹침"):
        assert_clean(overlapping, PROFILES, "en")


def test_sampling_from_the_feasible_pool_yields_the_requested_size():
    """**표본을 먼저 뽑으면 트랙이 요청 크기의 절반이 된다.**

    plan_segment 통과율이 약 50%라, sample -> build 순서로는 5000을 요청해도
    2,500건이 나온다. 스펙 §4.1이 "언어쌍당 5000"을 요구하고 그 위에서
    표준오차를 계산했으므로, 순서가 뒤집히면 그 약속이 깨진다.
    """
    # 절반은 담기고 절반은 담기지 않는 풀을 만든다.
    good = [SentencePair(f"짧은 문장 {i} 입니다", f"Short sentence {i} here") for i in range(40)]
    bad = [SentencePair("가" * 400, "a" * 2000) for _ in range(40)]
    pool = [p for pair in zip(good, bad, strict=True) for p in pair]

    feasible = [p for p in pool if plan_segment(p, "en", PROFILES) is not None]
    assert len(feasible) == 40, "픽스처 전제: 절반만 담긴다"

    chosen = sample(feasible, 20, seed=1)
    segments, excluded = build(chosen, "en", PROFILES)
    assert len(segments) == 20
    assert excluded["unfittable"] == 0


# --- 주입 감사 산출물 (스펙 §5.7) --------------------------------------------

_MUTATED = [
    Segment(id="s0", index=0, start_ms=0, end_ms=1500, source_text="안녕", target_text="안녕"),
    Segment(id="s1", index=1, start_ms=1620, end_ms=3000, source_text="세계", target_text="World"),
]
_LABELS = [Label(segment_id="s0", kind="untranslated", detail={"replaced_with": "source"})]


def test_dump_audit_writes_the_track_and_the_labels_together(tmp_path):
    """스펙 §5.7 — 변조 트랙과 정답은 **짝일 때만** 감사에 쓸모가 있다.

    둘을 각각 쓰는 함수 두 개로 두면 한쪽만 호출하는 실수가 구조적으로
    가능하다. 한 번의 호출이 두 파일을 함께 낸다.
    """
    injected_path, labels_path = dump_audit(
        _MUTATED, _LABELS, tmp_path, "en-ko", seed=20260729, commit="deadbeef"
    )
    assert injected_path.name == "en-ko.injected.json"
    assert labels_path.name == "en-ko.labels.json"
    assert injected_path.exists() and labels_path.exists()


def test_injected_track_stays_readable_by_load_track(tmp_path):
    """감사자가 새 파서를 짜야 한다면 감사 수단이 아니다.

    변조 트랙은 `dump_track`과 같은 형식이라 기존 도구로 그대로 읽힌다.
    """
    injected_path, _ = dump_audit(_MUTATED, _LABELS, tmp_path, "en-ko", seed=1, commit="abc1234")
    assert load_track(injected_path) == _MUTATED


def test_labels_carry_reproduction_metadata(tmp_path):
    """시드·커밋이 없으면 "어느 실행의 정답인가"에 답할 수 없다.

    리포트 헤더(`bench/report.py`)가 같은 이유로 재현 정보를 박는다 —
    라벨 파일만 따로 건네받은 사람도 같은 질문에 답할 수 있어야 한다.
    """
    _, labels_path = dump_audit(
        _MUTATED, _LABELS, tmp_path, "en-ko", seed=20260729, commit="deadbeef"
    )
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    assert payload["pair"] == "en-ko"
    assert payload["seed"] == 20260729
    assert payload["commit"] == "deadbeef"
    assert payload["segment_count"] == 2


def test_labels_preserve_kind_and_detail(tmp_path):
    """`detail`이 유실되면 "무엇을 어떻게 변조했는가"를 검증할 수 없다."""
    _, labels_path = dump_audit(_MUTATED, _LABELS, tmp_path, "en-ko", seed=1, commit="abc1234")
    assert load_labels(labels_path) == _LABELS


def test_labels_record_the_hash_of_the_track_they_describe(tmp_path):
    """라벨만 있고 어느 트랙의 것인지 모르면 감사가 아니다.

    `bench/manifest.json`이 코퍼스에 대해 하는 것과 같은 관용구다.
    """
    injected_path, labels_path = dump_audit(
        _MUTATED, _LABELS, tmp_path, "en-ko", seed=1, commit="abc1234"
    )
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    expected = hashlib.sha256(injected_path.read_bytes()).hexdigest()
    assert payload["injected_track_sha256"] == expected
    assert payload["injected_track"] == "en-ko.injected.json"


def test_load_labels_rejects_a_track_that_does_not_match_the_hash(tmp_path):
    """**게이트를 만들면 반드시 실패시켜 본다.**

    해시를 적기만 하고 아무도 대조하지 않으면 없는 게이트와 같다.
    감사자가 손댄 트랙에 원본 라벨을 붙여 읽으면 여기서 드러나야 한다.
    """
    injected_path, labels_path = dump_audit(
        _MUTATED, _LABELS, tmp_path, "en-ko", seed=1, commit="abc1234"
    )
    tampered = [replace(_MUTATED[0], target_text="변조됨"), _MUTATED[1]]
    dump_track(tampered, injected_path)

    with pytest.raises(ValueError, match="SHA-256"):
        load_labels(labels_path)
