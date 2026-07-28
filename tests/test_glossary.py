"""용어집 테스트 (요구사항정의서 FR-3.7, FR-2.3)."""

import pytest

from cuesift.glossary import Glossary, GlossaryEntry, load_glossary

SAMPLE = """
entries:
  - source: 기후변화
    targets:
      en: [climate change, global warming]
      ja: [気候変動]
  - source: 인공지능
    targets:
      en: [artificial intelligence, AI]
      ja: [人工知能]
"""


@pytest.fixture
def glossary_file(tmp_path):
    path = tmp_path / "glossary.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_load_selects_only_the_target_language(glossary_file):
    g = load_glossary(glossary_file, "en")
    assert len(g.entries) == 2
    assert g.entries[0].targets == ("climate change", "global warming")


def test_entry_without_the_target_language_is_dropped(tmp_path):
    """ja 항목이 없는 용어를 en 용어집에 남기면 대응어가 빈 채로
    항상 위반 판정이 나온다."""
    path = tmp_path / "g.yaml"
    path.write_text(
        "entries:\n  - source: 가\n    targets:\n      ja: [ア]\n",
        encoding="utf-8",
    )
    assert load_glossary(path, "en").entries == ()


def test_no_violation_when_target_contains_a_listed_equivalent(glossary_file):
    g = load_glossary(glossary_file, "en")
    assert g.violations("기후변화는 심각하다", "Climate change is serious") == []


def test_violation_when_no_equivalent_appears(glossary_file):
    g = load_glossary(glossary_file, "en")
    hits = g.violations("기후변화는 심각하다", "The weather is bad")
    assert [e.source for e in hits] == ["기후변화"]


def test_any_one_of_multiple_equivalents_satisfies(glossary_file):
    """대응어가 여러 개면 하나만 나와도 통과다. 전부 요구하면
    정상 번역이 대량 오탐이 된다."""
    g = load_glossary(glossary_file, "en")
    assert g.violations("인공지능 연구", "AI research") == []


def test_matching_is_case_insensitive(glossary_file):
    g = load_glossary(glossary_file, "en")
    assert g.violations("기후변화", "CLIMATE CHANGE") == []


def test_term_absent_from_source_is_not_checked(glossary_file):
    """원문에 없는 용어는 판정 대상이 아니다 (FR-3.7의 정의).
    이걸 어기면 용어집이 커질수록 오탐이 선형으로 는다."""
    assert load_glossary(glossary_file, "en").violations("날씨 얘기", "Weather talk") == []


def test_multiple_violations_are_all_reported(glossary_file):
    g = load_glossary(glossary_file, "en")
    hits = g.violations("기후변화와 인공지능", "Two topics")
    assert sorted(e.source for e in hits) == ["기후변화", "인공지능"]


def test_empty_glossary_reports_itself(tmp_path):
    """비어 있는 용어집으로 측정하면 '용어 위반 0건'이 나오는데,
    이건 '위반이 없다'가 아니라 '검사하지 않았다'다. 호출자가
    이 둘을 구분할 수 있어야 한다."""
    path = tmp_path / "empty.yaml"
    path.write_text("entries: []\n", encoding="utf-8")
    g = load_glossary(path, "en")
    assert g.is_empty is True
    assert Glossary(entries=(GlossaryEntry("가", ("a",)),)).is_empty is False


def test_missing_entries_key_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("terms: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="entries"):
        load_glossary(path, "en")
