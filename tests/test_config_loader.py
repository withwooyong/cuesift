"""`cuesift.yaml` 로더 (FR-8.4 · 설계 §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuesift.config import load_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cuesift.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_도메인_중첩을_평평한_경로로_읽는다(tmp_path: Path) -> None:
    path = _write(tmp_path, "source_lang: ko\nllm:\n  model: qwen2.5:3b\n")
    cfg = load_config(path)
    assert cfg.values[("source_lang",)] == "ko"
    assert cfg.values[("llm", "model")] == "qwen2.5:3b"
    assert cfg.source == path


def test_빈_파일은_빈_설정이다(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.values == {}
    assert cfg.weights is None


def test_최상위가_매핑이_아니면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="최상위가 매핑이 아니다"):
        load_config(_write(tmp_path, "- a\n- b\n"))


def test_YAML_문법_오류를_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="YAML을 읽을 수 없다"):
        load_config(_write(tmp_path, "a: [1, 2\n"))


def test_utf8이_아니면_거부한다(tmp_path: Path) -> None:
    path = tmp_path / "cuesift.yaml"
    path.write_bytes("source_lang: 한국어\n".encode("cp949"))
    with pytest.raises(ValueError, match="utf-8로 읽을 수 없다"):
        load_config(path)


def test_모르는_키를_거부하고_후보를_제시한다(tmp_path: Path) -> None:
    path = _write(tmp_path, "triage:\n  review_budgt: 10%\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "모르는 키 'triage.review_budgt'" in message
    assert "가까운 키: triage.review_budget" in message


def test_후보가_없으면_후보절을_붙이지_않는다(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as excinfo:
        load_config(_write(tmp_path, "zzzzzzzz: 1\n"))
    assert "가까운 키" not in str(excinfo.value)


def test_지원하지_않는_provider를_거부한다(tmp_path: Path) -> None:
    path = _write(tmp_path, "llm:\n  provider: anthropic\n")
    with pytest.raises(ValueError, match="llm.provider"):
        load_config(path)


def test_openai_compatible은_통과한다(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "llm:\n  provider: openai-compatible\n"))
    assert cfg.values[("llm", "provider")] == "openai-compatible"


def test_weights는_기본값_위에_얹힌다(tmp_path: Path) -> None:
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: 0.3\n")
    cfg = load_config(path)
    assert cfg.weights is not None
    assert cfg.weights["spec.violation"] == 0.3
    # 명시하지 않은 신호는 1.0을 유지한다(설계 §5.1). 전량 지정을 요구하면
    # v0.2에서 신호가 늘 때 기존 설정 파일이 전부 거부된다(FR-6.5).
    assert cfg.weights["glossary.miss"] == 1.0
    assert len(cfg.weights) == 10


def test_모르는_신호_이름을_거부한다(tmp_path: Path) -> None:
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violaton: 0.3\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path)
    assert "가까운 키: spec.violation" in str(excinfo.value)


def test_숫자가_아닌_가중치를_거부한다(tmp_path: Path) -> None:
    # 이 검사가 없으면 fuse()의 math.isfinite가 TypeError를 내고, 미처리
    # traceback은 종료 코드 1("규격 위반 발견")로 오보된다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: 높음\n")
    with pytest.raises(ValueError, match="숫자가 아니다"):
        load_config(path)


def test_불리언_가중치를_거부한다(tmp_path: Path) -> None:
    # bool은 int의 하위형이라 float()에 통과한다. True가 1.0이 되면
    # "가중치를 껐다"고 믿은 사용자가 1.0으로 검수받는다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: true\n")
    with pytest.raises(ValueError, match="숫자가 아니다"):
        load_config(path)


def test_weights가_매핑이_아니면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="매핑이 아니다"):
        load_config(_write(tmp_path, "signals:\n  weights: 0.3\n"))
