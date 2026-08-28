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


def test_도메인_키가_커맨드별로_접힌다(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "llm:\n  model: m1\nspec:\n  profile: ko\n  limit: 5\n",
    )
    dm = load_config(path).to_default_map()
    assert dm["translate"]["model"] == "m1"
    assert dm["check"]["spec"] == "ko"
    assert dm["check"]["limit"] == 5


def test_source_lang은_두_커맨드에_뿌려진다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "source_lang: ja\n")).to_default_map()
    assert dm["translate"]["source_lang"] == "ja"
    assert dm["transcribe"]["source_lang"] == "ja"


def test_targets_목록이_쉼표_문자열이_된다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "targets: [en, ja]\n")).to_default_map()
    assert dm["translate"]["to"] == "en,ja"


def test_targets가_문자열이면_그대로_쓴다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "targets: en,ja\n")).to_default_map()
    assert dm["translate"]["to"] == "en,ja"


def test_targets가_매핑이면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="targets는 목록이거나"):
        load_config(_write(tmp_path, "targets:\n  en: 1\n")).to_default_map()


def test_cache_enabled가_no_cache로_뒤집힌다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "cache:\n  enabled: false\n")).to_default_map()
    assert dm["translate"]["no_cache"] is True
    dm = load_config(_write(tmp_path, "cache:\n  enabled: true\n")).to_default_map()
    assert dm["translate"]["no_cache"] is False


def test_weights는_default_map에_들어가지_않는다(tmp_path: Path) -> None:
    # CLI 옵션이 아니다(설계 D6). 여기 들어가면 click이 모르는 파라미터로
    # 죽는다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: 0.3\n")
    cfg = load_config(path)
    dm = cfg.to_default_map()
    assert dm == {}
    assert cfg.weights is not None


def test_llm_provider는_default_map에_들어가지_않는다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "llm:\n  provider: openai-compatible\n")).to_default_map()
    assert dm == {}


def test_지정하지_않은_키는_비어_있다(tmp_path: Path) -> None:
    # 없는 키를 None으로 채우면 click이 그것을 "설정이 준 값"으로 보고
    # 기본값을 덮는다.
    dm = load_config(_write(tmp_path, "source_lang: ko\n")).to_default_map()
    assert dm["translate"] == {"source_lang": "ko"}


def test_변환_오류에_파일_경로가_실린다(tmp_path: Path) -> None:
    path = _write(tmp_path, "targets:\n  en: 1\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path).to_default_map()
    assert str(path) in str(excinfo.value)


# 매핑 전수 - 24행 각각 1건 (설계 §8). 상등 게이트(Task 1)는 "행이 있는가"만
# 보고 "값이 실제로 도착하는가"는 보지 않는다. 오타 난 파라미터명이 상등
# 게이트를 통과한 뒤 click에서 조용히 무시되는 경로가 여기서 막힌다.
@pytest.mark.parametrize(
    ("yaml_text", "command", "param", "expected"),
    [
        ("source_lang: ja\n", "translate", "source_lang", "ja"),
        ("source_lang: ja\n", "transcribe", "source_lang", "ja"),
        ("targets: [en]\n", "translate", "to", "en"),
        ("llm:\n  base_url: http://h/v1\n", "translate", "base_url", "http://h/v1"),
        ("llm:\n  model: m1\n", "translate", "model", "m1"),
        ("llm:\n  context_window: 5\n", "translate", "context_window", 5),
        ("glossary: ./g.yaml\n", "translate", "glossary", "./g.yaml"),
        ("work_context: 다큐\n", "translate", "work_context", "다큐"),
        ("output:\n  dir: ./out\n", "translate", "out", "./out"),
        ("cache:\n  dir: ./c\n", "translate", "cache_dir", "./c"),
        ("cache:\n  enabled: false\n", "translate", "no_cache", True),
        ("dry_run: true\n", "translate", "dry_run", True),
        ("signals:\n  tier1:\n    enabled: true\n", "translate", "tier1", True),
        ("signals:\n  tier1:\n    max_ratio: 0.2\n", "translate", "tier1_max_ratio", 0.2),
        ("signals:\n  tier1:\n    samples: 2\n", "translate", "tier1_samples", 2),
        (
            "signals:\n  tier1:\n    temperature: 0.7\n",
            "translate",
            "tier1_temperature",
            0.7,
        ),
        ('triage:\n  review_budget: "10%"\n', "translate", "review_budget", "10%"),
        ("triage:\n  review_threshold: 0.7\n", "translate", "review_threshold", 0.7),
        ("review:\n  out: ./r\n", "translate", "review_out", "./r"),
        ("review:\n  format: html\n", "translate", "review_format", "html"),
        ("spec:\n  profile: ko\n", "check", "spec", "ko"),
        ("spec:\n  fail_on: none\n", "check", "fail_on", "none"),
        ("spec:\n  limit: 3\n", "check", "limit", 3),
    ],
)
def test_매핑_전수가_도착한다(
    tmp_path: Path, yaml_text: str, command: str, param: str, expected: object
) -> None:
    dm = load_config(_write(tmp_path, yaml_text)).to_default_map()
    assert dm[command][param] == expected
