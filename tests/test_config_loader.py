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


@pytest.mark.parametrize(
    ("yaml_text", "dotted", "child"),
    [
        ("spec: ko\n", "spec", "spec.profile"),
        ("signals:\n  tier1: true\n", "signals.tier1", "signals.tier1.enabled"),
        ("llm: http://h/v1\n", "llm", "llm.base_url"),
    ],
)
def test_가지_키에_스칼라를_주면_원인을_말한다(
    tmp_path: Path, yaml_text: str, dotted: str, child: str
) -> None:
    """**진단이 자기 자신을 제안하면 안 된다.**

    이전 판은 `spec: ko`에 `모르는 키 'spec'. 가까운 키: spec`을 냈다 -
    `difflib`이 자기 자신을 가장 가까운 후보로 골랐기 때문이다. 설계 D11이
    `profiles_dir`을 `spec.profile`로 바꾼 직후라 `spec: ko`는 실제로 나올
    오타인데, 사용자는 이미 맞게 쓴 키를 노려보게 된다.
    """
    with pytest.raises(ValueError) as excinfo:
        load_config(_write(tmp_path, yaml_text))
    message = str(excinfo.value)
    assert f"'{dotted}'의 값이 매핑이 아니다" in message
    assert child in message
    assert "모르는 키" not in message


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
    # DEFAULT_WEIGHTS가 11종(Tier 0 9종 + Tier 1 2종: self_consistency·
    # backtranslation)이므로 이 개수도 함께 늘어난다 (FR-4.2).
    assert len(cfg.weights) == 11


def test_모르는_신호_이름을_거부한다(tmp_path: Path) -> None:
    # **경로 접두사를 잃으면 최상위 키처럼 읽힌다.** 이전 판은 1-튜플을
    # 넘겨 `모르는 키 'spec.violaton'`을 냈는데, 신호 이름 자체에 점이 있어
    # 사용자는 그것을 `spec` 아래의 키로 읽는다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violaton: 0.3\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "모르는 키 'signals.weights.spec.violaton'" in message
    assert "가까운 키: signals.weights.spec.violation" in message


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


@pytest.mark.parametrize("literal", ["[en, null]", "[en, 1]", "[en, [ja]]"])
def test_targets의_원소가_문자열이_아니면_거부한다(tmp_path: Path, literal: str) -> None:
    # `str(item)`은 무엇이든 받아 `to="en,None"`을 만든다. `--to`는 검증 없이
    # `_output_path`를 거쳐 **파일 이름 조각**이 되므로, 언어 코드 `None`으로
    # 파일이 나가고 종료 코드는 0이다.
    with pytest.raises(ValueError, match="targets의 원소가 문자열이 아니다"):
        load_config(_write(tmp_path, f"targets: {literal}\n")).to_default_map()


def test_cache_enabled가_no_cache로_뒤집힌다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "cache:\n  enabled: false\n")).to_default_map()
    assert dm["translate"]["no_cache"] is True
    dm = load_config(_write(tmp_path, "cache:\n  enabled: true\n")).to_default_map()
    assert dm["translate"]["no_cache"] is False


@pytest.mark.parametrize("literal", ['"false"', "[1]", "1", "{}"])
def test_참거짓이_아닌_cache_enabled를_거부한다(tmp_path: Path, literal: str) -> None:
    """`negate()`가 **무엇이든 bool로 만들어 click이 볼 값이 남지 않는다.**

    `signals.weights`와 같은 종류의 구멍이 하나 더 있었던 것이다(설계 D5).
    실측: `"false"`는 참이라 `--no-cache`가 **꺼지고**(사용자 의도의 정반대),
    `[1]`도 마찬가지로 exit 0으로 조용히 통과했다.
    """
    with pytest.raises(ValueError, match="cache.enabled가 참·거짓이 아니다"):
        load_config(_write(tmp_path, f"cache:\n  enabled: {literal}\n"))


def test_cache_enabled의_null은_부재로_본다(tmp_path: Path) -> None:
    """`null`의 뜻을 다른 21개 키와 **일치시킨다.**

    click은 `default_map`의 `None`을 "값 없음"으로 읽어 옵션 기본값으로
    흘려보낸다. 여기만 `negate(None) is True`라 **캐시가 꺼졌다** - 같은
    문법이 키마다 다른 뜻이 되면 사용자가 문서를 못 믿는다.
    """
    cfg = load_config(_write(tmp_path, "cache:\n  enabled: null\n"))
    assert ("cache", "enabled") not in cfg.values
    assert "no_cache" not in cfg.to_default_map().get("translate", {})


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
