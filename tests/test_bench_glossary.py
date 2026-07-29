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


def test_most_glossary_terms_appear_in_the_track():
    """용어가 트랙에 없으면 주입 기회가 없다.

    트랙은 전체 코퍼스의 5,000건 표본이라 저빈도 용어는 빠질 수 있다.
    전부를 요구하면 표본이 바뀔 때마다 흔들리는 테스트가 되므로, 절반을 기준으로 둔다.
    **주입 자격이 정말 충분한지는 Task 6의 "실주입 0건이면 실패" 가드가 잡는다.**
    """
    import pytest
    from bench.track_io import load_track

    track = Path("data/bench/en-ko.clean.json")
    if not track.exists():
        pytest.skip("트랙이 없다 — python -m bench.build_track --pair en-ko 를 먼저 실행할 것")

    corpus = "\n".join(seg.source_text for seg in load_track(track))
    entries = load_glossary(GLOSSARY, "en").entries
    present = [e.source for e in entries if e.source in corpus]
    missing = sorted({e.source for e in entries} - set(present))
    assert len(present) >= len(entries) // 2, f"트랙에 없는 용어: {missing}"
