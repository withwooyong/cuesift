"""위험도 융합 테스트 (요구사항정의서 FR-6.1, FR-6.2, FR-6.4)."""

import pytest

from cuesift.risk import DEFAULT_WEIGHTS, fuse
from cuesift.segment import Signal


def _sig(name: str, score: float, hard: bool = False) -> Signal:
    return Signal(name=name, tier=0, score=score, hard_fail=hard)


def test_no_signals_means_zero_risk():
    r = fuse("s1", [])
    assert r.risk_score == 0.0
    assert r.hard_fail is False
    assert r.reasons == []


def test_single_signal_score_passes_through():
    assert fuse("s1", [_sig("spec.violation", 0.6)]).risk_score == pytest.approx(0.6)


def test_weighted_average_of_multiple_signals():
    """가중 평균이지 합이 아니다. 합을 쓰면 신호가 많은 세그먼트가
    각 신호의 점수와 무관하게 상위로 올라간다."""
    r = fuse("s1", [_sig("a", 0.2), _sig("b", 0.8)], weights={"a": 1.0, "b": 1.0})
    assert r.risk_score == pytest.approx(0.5)


def test_weights_shift_the_result():
    r = fuse("s1", [_sig("a", 0.0), _sig("b", 1.0)], weights={"a": 1.0, "b": 3.0})
    assert r.risk_score == pytest.approx(0.75)


def test_risk_score_stays_normalized():
    """FR-6.1 — 0~1을 벗어나면 triage의 정렬·임계 비교가 깨진다."""
    r = fuse("s1", [_sig(f"n{i}", 1.0) for i in range(10)])
    assert 0.0 <= r.risk_score <= 1.0


def test_hard_fail_forces_max_risk():
    """FR-6.2 — hard fail은 가중합을 우회한다. 우회하지 않으면
    다른 신호가 전부 0인 세그먼트의 hard fail이 희석돼 예산 밖으로 밀린다."""
    r = fuse("s1", [_sig("struct.empty", 1.0, hard=True), _sig("x", 0.0), _sig("y", 0.0)])
    assert r.hard_fail is True
    assert r.risk_score == 1.0


def test_reasons_name_every_contributing_signal():
    """FR-6.4 — 왜 선별되었는지 설명 가능해야 한다."""
    r = fuse("s1", [_sig("spec.violation", 0.5), _sig("glossary.miss", 1.0)])
    assert sorted(r.reasons) == ["glossary.miss", "spec.violation"]


def test_zero_score_signal_is_not_a_reason():
    """0점 신호를 사유에 넣으면 리포트가 '이것 때문에 뽑혔다'고
    거짓말한다."""
    assert fuse("s1", [_sig("a", 0.0), _sig("b", 0.7)]).reasons == ["b"]


def test_unknown_signal_uses_default_weight():
    """v0.2에서 새 신호가 꽂혀도 가중치 설정 없이 동작해야 한다(FR-6.5)."""
    assert (
        fuse("s1", [_sig("qe.cometkiwi", 1.0)], weights={"spec.violation": 2.0}).risk_score == 1.0
    )


def test_negative_weight_is_rejected():
    """음수 가중치는 '위험할수록 안전'을 뜻하게 되어 정렬이 뒤집힌다."""
    with pytest.raises(ValueError, match="가중치"):
        fuse("s1", [_sig("a", 0.5)], weights={"a": -1.0})


def test_default_weights_cover_all_nine_signals():
    """등록된 신호가 기본 가중치 표에서 빠지면 조용히 1.0이 되는데,
    그 자체는 문제가 아니지만 '튜닝하지 않았다'는 기록이 사라진다."""
    from cuesift.signals import registry

    assert set(DEFAULT_WEIGHTS) == set(registry())


def test_all_default_weights_are_equal():
    """스펙 §6.3 — 첫 측정은 무튜닝이다. 같은 데이터로 맞춘 가중치는
    새 데이터에서 재현되지 않는다."""
    assert len(set(DEFAULT_WEIGHTS.values())) == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_weight_is_rejected(bad):
    """nan은 `nan < 0`이 False라 음수 검사를 통과하고, 이후
    `max(0.0, nan)`이 NaN을 전파하지 않고 0.0을 반환해
    **최대 위험이 최소 위험으로 뒤집힌다.**

    YAML이 `.nan`·`.inf`를 파싱하므로 설정 파일 오타로 도달한다.
    """
    with pytest.raises(ValueError, match="가중치"):
        fuse("s1", [_sig("a", 1.0)], weights={"a": bad})


def test_all_zero_weights_is_a_configuration_error():
    """신호가 있는데 가중치가 전부 0이면 전체 세그먼트가 안전 판정된다.

    신호가 아예 없는 경우(정당한 0.0)와 구분해야 한다.
    """
    with pytest.raises(ValueError, match="가중치 총합"):
        fuse("s1", [_sig("a", 0.9), _sig("b", 0.9)], weights={"a": 0.0, "b": 0.0})


def test_zero_weight_on_one_signal_is_allowed():
    """일부 신호만 0으로 끄는 것은 정상적인 ablation 사용법이다."""
    r = fuse("s1", [_sig("a", 1.0), _sig("b", 0.0)], weights={"a": 0.0, "b": 2.0})
    assert r.risk_score == 0.0


def test_reasons_preserve_signal_order():
    """`reasons`는 §8.4 review.json에 그대로 실린다.

    기존 테스트가 `sorted()`로 비교해 순서를 전혀 검증하지 않았다.
    순서가 흔들리면 같은 입력이 다른 리포트를 낸다(NFR-3).
    """
    assert fuse("s1", [_sig("b", 0.7), _sig("a", 0.7)]).reasons == ["b", "a"]
