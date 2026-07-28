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
