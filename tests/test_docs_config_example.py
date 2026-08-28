"""요구사항정의서 §8.2의 예시 YAML이 실제로 로드되는지 본다 (FR-8.4 · 설계 §7).

**문서가 표류하면 D4가 즉시 하드 에러를 낸다.** 모르는 키는 종료 코드 2로
거부되므로, §8.2를 읽고 그대로 붙여 넣은 사용자는 첫 실행에서 죽는다. 그런데
이 저장소의 어떤 게이트도 그 블록을 실행해 본 적이 없었다 - 실제로 개명
2건(`consistency_n`·`risk_threshold`)과 폐기 1건(`profiles_dir`)이 문서에
남아 있었다.

**게이트를 만들면 반드시 실패시켜 봐야 한다.** §8.2에 `signals.tier1.consistency_n`을
되돌려 넣으면 `test_요구사항정의서_82의_예시가_로드된다`가
`ValueError: 모르는 키 'signals.tier1.consistency_n'. 가까운 키: signals.tier1.samples`
로 죽는 것을 확인했다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuesift.config import load_config
from cuesift.risk.fuse import DEFAULT_WEIGHTS

_REQUIREMENTS = Path(__file__).resolve().parents[1] / "docs" / "요구사항정의서.md"
_SECTION = "### 8.2 설정 파일"


def _section_yaml_blocks() -> list[str]:
    """§8.2 절 안의 ```yaml 코드 블록만 뽑는다.

    절 경계로 자르는 것이 요점이다. 문서 전체에서 `yaml` 블록을 긁으면
    §8.3(규격 프로파일)·§8.4가 함께 딸려 와 이 게이트가 무엇을 검사하는지
    흐려진다.
    """
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    start = text.index(_SECTION)
    end = text.index("\n### ", start + len(_SECTION))
    blocks: list[str] = []
    inside = False
    buffer: list[str] = []
    for line in text[start:end].splitlines():
        if line.strip() == "```yaml":
            inside = True
            buffer = []
            continue
        if inside and line.strip() == "```":
            inside = False
            blocks.append("\n".join(buffer))
            continue
        if inside:
            buffer.append(line)
    return blocks


def test_82에_yaml_블록이_정확히_하나_있다() -> None:
    # 0개면 절 제목이 바뀌었거나 펜스 언어 태그가 빠진 것이고, 그 경우
    # 아래 테스트들이 "검사할 것이 없어서" 통과한다 - 0건 수집은 통과가
    # 아니라 설정 오류다.
    assert len(_section_yaml_blocks()) == 1


def test_요구사항정의서_82의_예시가_로드된다(tmp_path: Path) -> None:
    # 문서를 그대로 파일로 써서 로더에 먹인다. 사용자가 하는 행동과 같다.
    path = tmp_path / "cuesift.yaml"
    path.write_text(_section_yaml_blocks()[0], encoding="utf-8")

    config = load_config(path)

    # 로드만 되고 아무 데도 안 가면 "허용은 되는데 무시되는 설정"이다.
    default_map = config.to_default_map()
    assert default_map["translate"]["to"] == "en,ja"
    assert default_map["check"]["spec"] == "ko"


def test_82의_가중치_키가_DEFAULT_WEIGHTS와_같은_집합이다(tmp_path: Path) -> None:
    # 설계 D9. 축약명(`spec`·`glossary`…)으로 되돌아가면 `struct.*` 5종을
    # 가리킬 수 없게 되는데, 로더는 `signals.weights`를 잎으로 두어
    # 하위 키를 검사하지 않으므로(LEAF_PATHS) 그 회귀는 조용히 통과한다.
    path = tmp_path / "cuesift.yaml"
    path.write_text(_section_yaml_blocks()[0], encoding="utf-8")

    weights = load_config(path).weights

    assert weights is not None
    assert set(weights) == set(DEFAULT_WEIGHTS)


def test_82에_잘못된_키를_더하면_거부된다(tmp_path: Path) -> None:
    # 위 게이트가 실패할 수 있는 게이트임을 같은 파일 안에서 보인다.
    # 문서를 손으로 망가뜨려 확인하는 것은 재현되지 않는다.
    path = tmp_path / "cuesift.yaml"
    # 개명 전 이름(설계 §7)을 되돌려 넣는다. YAML은 뒤에 온 매핑이 이기므로
    # `signals` 블록 전체가 이 세 줄로 바뀐다.
    path.write_text(
        _section_yaml_blocks()[0] + "\nsignals:\n  tier1:\n    consistency_n: 3\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="모르는 키"):
        load_config(path)
