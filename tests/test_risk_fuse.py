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


def test_multiple_signals_combine_by_noisy_or():
    """`1 - ∏(1 - sᵢ)^wᵢ`. 평균이 아니다.

    평균을 쓰면 **문제를 하나 더 찾을 때 위험도가 내려간다** —
    0.8짜리 신호에 0.2짜리가 붙으면 0.5가 되어 뒤로 밀린다.
    트리아지는 "평균적으로 얼마나 나쁜가"가 아니라
    "적어도 하나가 진짜일 가능성"을 원한다.
    """
    r = fuse("s1", [_sig("a", 0.2), _sig("b", 0.8)], weights={"a": 1.0, "b": 1.0})
    assert r.risk_score == pytest.approx(0.84)


def test_weights_enter_as_exponents():
    """가중치는 지수다 — `(1 - s)^w`는 "이 신호를 w번 관측했다"로 읽힌다.

    점수 스케일 가중(`1 - ∏(1 - w·s)`)을 쓰면 `w·s > 1`에서
    `(1 - w·s)`가 음수가 되어 곱의 부호가 뒤집힌다. `w ≤ 1` clamp를
    강제해야 하고 그러면 **신호 강화를 표현할 수 없다.**

    두 신호 모두 0.5일 때 균등 가중이면 `1 - 0.5·0.5 = 0.75`인데,
    b에 3을 주면 `1 - 0.5·0.5³ = 0.9375`가 된다.

    **입력이 `b=1.0`이면 안 된다** — `(1-1.0)^w = 0`이라 어떤 `w`를 줘도
    1.0이 나와 가중치 검증력이 사라진다(교체 전 테스트가 그랬다).
    """
    r = fuse("s1", [_sig("a", 0.5), _sig("b", 0.5)], weights={"a": 1.0, "b": 3.0})
    assert r.risk_score == pytest.approx(0.9375)


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


def test_default_weights_cover_all_ten_signals():
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


def test_weight_sum_overflow_is_rejected():
    """개별 가중치가 유한해도 **합계**가 넘치면 같은 역전이 일어난다.

    `1e308 + 1e308 = inf`, `weighted`도 inf, `inf / inf = nan`이 되고
    `min(1.0, max(0.0, nan))`이 0.0을 반환한다 — 최대 위험(1.0)의 신호가
    **예외도 로그도 없이 최소 위험으로 뒤집힌다.** 개별 값만 검사하면
    `test_non_finite_weight_is_rejected`가 막았다고 선언한 실패 모드가
    합계 경로로 그대로 재현된다.
    """
    with pytest.raises(ValueError, match="가중치"):
        fuse("s1", [_sig("a", 1.0), _sig("b", 1.0)], weights={"a": 1e308, "b": 1e308})


def test_large_but_finite_weight_sum_is_allowed():
    """오버플로 검사가 정상적으로 큰 가중치까지 막으면 안 된다.

    `1e307 * 2`는 유한하므로 통과해야 한다 — 이 경계가 없으면
    위 검사가 ablation용 큰 가중치를 함께 죽인다.
    """
    r = fuse("s1", [_sig("a", 1.0), _sig("b", 1.0)], weights={"a": 1e307, "b": 1e307})
    assert r.risk_score == 1.0


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


def test_adding_a_signal_never_lowers_the_score():
    """**이 프로젝트에서 가중 평균을 버린 이유다.**

    문제를 하나 더 찾았는데 위험도가 내려가면 트리아지가 거꾸로 간다.
    규격 위반(1.0)에 용어 위반(0.5)을 더하면 평균은 0.75가 된다.

    실측으로 그 비용을 쟀다 — 오라클 대비 달성률이 예산 10%에서만
    69.6%로 꺼졌고(1~5% 86.4%, 20~30% 86~89%), 그 구간이 기본 운영점이다.
    """
    one = fuse("s1", [_sig("spec.violation", 1.0)]).risk_score
    two = fuse("s1", [_sig("spec.violation", 1.0), _sig("glossary.miss", 0.5)]).risk_score
    assert two >= one
    assert two == pytest.approx(1.0)


def test_a_single_certain_signal_saturates():
    """`s=1.0`이면 `(1-1.0)^w = 0`이라 다른 신호와 무관하게 1.0이다.

    확정 위반 하나를 다른 신호가 희석하지 못한다는 것이 산식의 요점이다.
    가중 평균에서는 이 입력이 0.367이었다.
    """
    r = fuse("s1", [_sig("a", 1.0), _sig("b", 0.1), _sig("c", 0.0)])
    assert r.risk_score == pytest.approx(1.0)


def test_weight_above_one_strengthens_a_single_signal():
    """`w>1`은 밑을 더 작게 만들어 점수를 올린다 — `1 - 0.4² = 0.84`.

    가중 평균에서는 단일 신호의 가중치가 분자와 분모에서 약분돼
    **어떤 w를 줘도 0.6이었다.** 즉 이 테스트는 지수 가중이 아니면
    통과할 수 없다.
    """
    r = fuse("s1", [_sig("a", 0.6)], weights={"a": 2.0})
    assert r.risk_score == pytest.approx(0.84)
