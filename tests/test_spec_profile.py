"""규격 프로파일 로더 테스트 (요구사항정의서 FR-5.1, FR-5.3, §8.3.1)."""

import pytest

from cuesift.spec import CharCounting, SpecProfile, load_builtin, load_profile


def test_builtin_ko_matches_documented_values():
    """§8.3.1의 표를 그대로 옮겼는지 확인한다. 이 값이 바뀌면
    규격 검사 결과 전체가 바뀌므로 테스트로 고정한다."""
    p = load_builtin("ko")
    assert p.max_chars_per_line == 16
    assert p.char_counting is CharCounting.latin_half
    assert p.max_cps == 12
    assert p.max_lines == 2
    assert p.min_duration_ms == 833
    assert p.max_duration_ms == 7000


def test_builtin_en_matches_documented_values():
    p = load_builtin("en")
    assert p.max_chars_per_line == 42
    assert p.char_counting is CharCounting.grapheme
    assert p.max_cps == 20


def test_builtin_ja_uses_language_specific_min_duration():
    """§8.3.1의 선례 규칙 — 언어별 가이드가 일반 요건을 덮어쓴다.
    일본어 500ms가 일반 요건 833ms를 대체한다."""
    p = load_builtin("ja")
    assert p.min_duration_ms == 500
    assert p.char_counting is CharCounting.fullwidth


def test_every_builtin_profile_declares_a_source():
    """§11 R8 — 출처 없는 수치를 기본값으로 넣지 않는다."""
    for name in ["ko", "en", "ja", "ted-ko", "ted-en", "ted-ja"]:
        assert load_builtin(name).source.startswith("http")


def test_ted_profile_is_separate_from_netflix():
    """§8.3.1 — TED2020을 Netflix 프로파일로 검사하면 위반이 대량
    발생해 트리아지 성능 측정이 오염된다."""
    assert load_builtin("ted-en").max_cps != load_builtin("en").max_cps


def test_ted_cjk_profiles_keep_the_researched_values():
    """ko·ja의 21자/10 CPS는 원문 URL이 죽은 출처에서 얻은 값이다.
    다시 확인할 수 없으므로 테스트가 유일한 보존 수단이다.

    라틴 기준(42자/21 CPS)의 환산치가 아니라 TED가 두 언어에 별도로
    정한 값이며, 두 언어 포털이 독립적으로 같은 수치를 말한다."""
    for name in ["ted-ko", "ted-ja"]:
        p = load_builtin(name)
        assert p.max_chars_per_line == 21
        assert p.max_cps == 10
        # TED는 지속시간을 명시하지 않는다. 언어별 차등의 근거가 없으므로
        # TED 프로파일 3종이 같은 값을 쓴다.
        assert p.min_duration_ms == load_builtin("ted-en").min_duration_ms


def test_unknown_builtin_raises_with_available_names():
    with pytest.raises(FileNotFoundError, match="ko"):
        load_builtin("nonexistent")


def test_load_profile_rejects_missing_required_field(tmp_path):
    """필드가 빠지면 기본값으로 조용히 채우지 않는다. 검사하지 않고
    통과하는 게이트는 없는 게이트보다 나쁘다."""
    path = tmp_path / "broken.yaml"
    path.write_text("name: broken\nmax_cps: 12\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_chars_per_line"):
        load_profile(path)


def test_load_profile_rejects_unknown_char_counting(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "name: bad\nsource: http://x\nmax_chars_per_line: 16\n"
        "char_counting: cjk_width\nmax_cps: 12\nmax_lines: 2\n"
        "min_duration_ms: 833\nmax_duration_ms: 7000\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="char_counting"):
        load_profile(path)


def test_load_profile_rejects_nonpositive_cps(tmp_path):
    """max_cps가 0이면 모든 세그먼트가 위반이 되어 신호가 무의미해진다."""
    path = tmp_path / "zero.yaml"
    path.write_text(
        "name: zero\nsource: http://x\nmax_chars_per_line: 16\n"
        "char_counting: latin_half\nmax_cps: 0\nmax_lines: 2\n"
        "min_duration_ms: 833\nmax_duration_ms: 7000\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max_cps"):
        load_profile(path)


def test_user_profile_can_override_builtin(tmp_path):
    """FR-5.3 — 사용자가 덮어쓸 수 있다."""
    path = tmp_path / "custom.yaml"
    path.write_text(
        "name: custom\nsource: http://internal\nmax_chars_per_line: 20\n"
        "char_counting: latin_half\nmax_cps: 15\nmax_lines: 3\n"
        "min_duration_ms: 600\nmax_duration_ms: 8000\n",
        encoding="utf-8",
    )
    p = load_profile(path)
    assert isinstance(p, SpecProfile)
    assert p.max_chars_per_line == 20
    assert p.max_lines == 3
