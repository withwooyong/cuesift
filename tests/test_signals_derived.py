"""파생 신호 테스트 (요구사항정의서 FR-3.6~FR-3.8)."""

import pytest

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment import Segment
from cuesift.signals import SignalContext
from cuesift.signals.derived import GlossaryMiss, LengthRatio, SpecViolationSignal
from cuesift.spec import load_builtin


@pytest.fixture
def ctx():
    return SignalContext(
        profile=load_builtin("ko"), glossary=None, source_lang="ko", target_lang="ko"
    )


def _seg(sid: str, source: str, target: str, start: int = 0, end: int = 2000) -> Segment:
    return Segment(
        id=sid, index=0, start_ms=start, end_ms=end, source_text=source, target_text=target
    )


# --- FR-3.8 규격 위반 ---


def test_spec_signal_silent_on_conforming_segment(ctx):
    assert SpecViolationSignal().collect(_seg("s1", "가나", "안녕하세요"), ctx) is None


def test_spec_signal_fires_on_cps_violation(ctx):
    # 폭 12.0 / 500ms = 24 CPS > ko 한도 12
    sig = SpecViolationSignal().collect(_seg("s1", "가", "가나다라마바사아자차카타", 0, 500), ctx)
    assert sig is not None
    assert sig.hard_fail is False
    assert "cps" in sig.detail["kinds"]


def test_spec_signal_score_grows_with_violation_count(ctx):
    """위반이 많을수록 위험하다. 한 건이든 세 건이든 같은 점수면
    가중합에서 심각도가 사라진다."""
    one = SpecViolationSignal().collect(_seg("s1", "가", "가나다라마바사아자차카타", 0, 500), ctx)
    three = SpecViolationSignal().collect(
        _seg("s2", "가", "가나다라마바사아자차카타파하거너더\n둘\n셋", 0, 400), ctx
    )
    assert three.score > one.score


def test_spec_signal_judges_target_text_not_source(ctx):
    """검사 대상은 화면에 나가는 번역문이다. 원문을 재면 번역 품질과
    무관한 위반이 잡힌다."""
    assert SpecViolationSignal().collect(_seg("s1", "가" * 40, "짧다", 0, 3000), ctx) is None


# --- FR-3.7 용어집 위반 ---


def test_glossary_signal_silent_without_a_glossary(ctx):
    """용어집이 없으면 판정하지 않는다. 0점 신호를 내면 '검사했고
    통과'로 읽혀 용어집 누락이 숨는다."""
    assert GlossaryMiss().collect(_seg("s1", "기후변화", "weather"), ctx) is None


def test_glossary_signal_fires_on_missing_equivalent():
    g = Glossary(entries=(GlossaryEntry("기후변화", ("climate change",)),))
    ctx = SignalContext(load_builtin("en"), g, "ko", "en")
    sig = GlossaryMiss().collect(_seg("s1", "기후변화 문제", "A weather problem"), ctx)
    assert sig is not None
    assert sig.hard_fail is False
    assert sig.detail["terms"] == ["기후변화"]


def test_glossary_signal_silent_when_equivalent_present():
    g = Glossary(entries=(GlossaryEntry("기후변화", ("climate change",)),))
    ctx = SignalContext(load_builtin("en"), g, "ko", "en")
    assert GlossaryMiss().collect(_seg("s1", "기후변화", "Climate change"), ctx) is None


# --- FR-3.6 길이비 이상치 ---


def _varied_normals() -> list[Segment]:
    """길이비가 0.8~1.2로 흩어진 정상 세그먼트 9건.

    전부 같은 길이로 만들면 MAD가 0이 되어 척도 계산이 다른 경로를 탄다.
    그 경로는 `test_length_ratio_falls_back_when_mad_is_zero`가 따로 검증한다.
    """
    targets = [
        "가나다라마",
        "가나다라마바",
        "가나다라",
        "가나다라마",
        "가나다라마바",
        "가나다라마",
        "가나다라",
        "가나다라마바",
        "가나다라마",
    ]
    return [_seg(f"n{i}", "가나다라마", t) for i, t in enumerate(targets)]


def test_length_ratio_flags_the_outlier(ctx):
    """정상 9건(비율 0.8~1.2) 사이에 비율 12.0인 극단값 1건을 넣는다."""
    segs = [*_varied_normals(), _seg("odd", "가나다라마", "가" * 60)]
    result = LengthRatio().collect_batch(segs, ctx)
    assert set(result) == {"odd"}
    assert result["odd"].hard_fail is False


def test_length_ratio_silent_on_uniform_track(ctx):
    """전부 같은 비율이면 이상치가 정의되지 않는다."""
    segs = [_seg(f"n{i}", "가나다라마", "가나다라마") for i in range(10)]
    assert LengthRatio().collect_batch(segs, ctx) == {}


def test_length_ratio_falls_back_when_mad_is_zero(ctx):
    """정상군이 완전히 균일하면 MAD가 0이 된다. 이때 척도를 못 구한다고
    빈손으로 돌아가면 **가장 명백한 이상치를 놓친다.**

    합성 벤치마크에서 이 상황은 예외가 아니라 기본이다 — 정상 세그먼트가
    같은 길이로 생성되고 주입된 오류만 튀기 때문이다.
    """
    segs = [_seg(f"n{i}", "가나다라마", "가나다라마") for i in range(10)]
    segs.append(_seg("x1", "가나다라마", "가" * 80))
    segs.append(_seg("x2", "가나다라마", "가" * 90))
    result = LengthRatio().collect_batch(segs, ctx)
    assert set(result) == {"x1", "x2"}


def test_length_ratio_needs_enough_samples(ctx):
    """표본이 적으면 분포를 말할 수 없다. 2건짜리 트랙에서 '이상치'를
    판정하면 근거 없는 신호가 위험도에 섞인다."""
    segs = [_seg("a", "가", "가"), _seg("b", "가", "가" * 50)]
    assert LengthRatio().collect_batch(segs, ctx) == {}


def test_length_ratio_skips_empty_targets(ctx):
    """빈 번역은 FR-3.2가 hard fail로 잡는다. 길이비 분포에 0을 넣으면
    중앙값이 끌려가 정상 세그먼트가 이상치로 뒤집힌다."""
    segs = [_seg(f"n{i}", "가나다라마", "가나다라마") for i in range(8)]
    segs.append(_seg("blank", "가나다라마", ""))
    segs.append(_seg("mild", "가나다라마", "가" * 15))
    result = LengthRatio().collect_batch(segs, ctx)
    assert "blank" not in result
    assert "mild" in result
