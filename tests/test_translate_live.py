"""실제 엔드포인트 왕복 (opt-in, FR-2.1~2.6).

**기본 제외다.** `pyproject.toml`의 `addopts`에 `-m "not live"`가 있어
`pytest`를 그냥 돌리면 `deselected`로 빠진다. 돌리려면 명령줄에서 `-m live`로
그 기본값을 덮는다.

상용 API:

    CUESIFT_LIVE_BASE_URL=https://api.openai.com/v1 \\
    CUESIFT_LIVE_MODEL=gpt-4o-mini \\
    CUESIFT_LIVE_API_KEY=sk-... \\
    .venv/Scripts/python.exe -m pytest tests/test_translate_live.py -m live -v -s

로컬 Ollama (키 없음):

    CUESIFT_LIVE_BASE_URL=http://localhost:11434/v1 \\
    CUESIFT_LIVE_MODEL=qwen2.5:3b \\
    .venv/Scripts/python.exe -m pytest tests/test_translate_live.py -m live -v -s

**Ollama 쪽이 이 파일의 진짜 목적이다** (요구사항정의서 §12 Q3: 로컬 LLM은
OpenAI 호환 엔드포인트로 일원화하되 능력은 균일하지 않다).

**두 명령의 `-s`는 장식이 아니다.** 이 파일이 존재하는 이유가 "약한 모델에서
`calls > 1`을 눈으로 읽는 것"인데, `-s`가 없으면 pytest가 stdout을 삼켜
**통과한 실행에서는 그 줄이 아예 안 보인다.**

## 이 테스트가 검증하지 **못하는** 것

**상용 프론티어 모델로 통과해도 개별 폴백 경로는 검증되지 않는다.** 폴백은
"모델이 배치 지시(번호·개수)를 어김"이 방아쇠인데, 프론티어 모델은 그것을
거의 어기지 않는다. 그래서 실사용에서 폴백이 한 번도 발동하지 않은 채
"live 통과"가 찍히고, 정작 약한 모델을 만나는 날 처음 실행된다.

폴백의 실물 검증에는 **소형 로컬 모델이 필요하다** (설계 §9.3).
가짜 프로바이더로 상정해 만든 변이가 진짜 변이를 전부 덮는다는 보장이 없고,
이 저장소는 "변이 배터리는 자기가 상정한 변이만 잡는다"에 이미 여러 번 물렸다.

**첫 실제 실행이 그 예언을 곧바로 확인했다** (2026-08-16, Ollama `qwen2.5:3b`).
이 독스트링은 상정 변이로 "번호 누락·코드펜스·설명 덧붙임"을 적고 있었으나
실물이 낸 첫 변이는 **셋 다 아니었다** - `{"id": "0"}`처럼 번호를 문자열로 냈다.
JSON도 구조도 개수도 순서도 맞았고 어긋난 것은 타입 하나였다. 원인은 모델의
능력이 아니라 프롬프트가 파서의 정수 요구를 전달하지 않은 것이었고, 프롬프트와
파서 양쪽을 고쳐 닫았다(CHANGELOG의 Fixed 첫 항목).

**그래서 이 파일이 관찰하는 것은 "약한 모델의 실수"가 아니라 "우리 명세의
빈틈"이다.** 위의 상정 목록을 다시 단정형으로 쓰지 않는 이유가 이것이다 -
다음 빈틈도 목록 밖에 있을 가능성이 높다.

## 호출 횟수를 단정하지 않는 이유

`usage.calls == 1`(배치 한 번에 성공)로 못 박으면 **약한 모델에서 이 파일이
빨개진다** - 폴백이 발동해 호출이 4회가 되기 때문이다. 그런데 그 발동이야말로
여기서 보고 싶은 현상이라, 단정하면 목적과 정반대로 동작한다. 그래서
하한을 풀고 실제 값은 `-v -s`의 출력으로 읽는다.

- `calls == 1`: 배치 경로가 그대로 성공했다 (프론티어 모델의 통상)
- `calls > 1`: **폴백이 실물에서 발동했다** (약한 모델. 이것이 관찰 목표다)

**대신 상한을 건다.** `calls >= 1`은 `failures == ()`가 이미 함의하므로
그것만으로는 공허하다. 상한 `(1 + 세그먼트 수) x (max_retries + 1)`은
"배치 1회 + 세그먼트별 폴백, 각각 최대 재시도"라는 구조가 낼 수 있는 최대치다.
넘으면 재시도 루프가 폭주했다는 뜻이고, 그것은 폴백 관찰과 무관하게 결함이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cuesift.segment.models import Segment
from cuesift.signals.base import SignalContext
from cuesift.spec import load_builtin
from cuesift.tier1 import triage_with_tier1
from cuesift.translate import (
    DEFAULT_MAX_RETRIES,
    OpenAICompatibleProvider,
    translate_segments,
)

pytestmark = pytest.mark.live


def _provider() -> OpenAICompatibleProvider:
    """환경변수가 없으면 **skip이지 fail이 아니다.**

    `-m live`를 준 것은 "돌릴 의사가 있다"이지 "엔드포인트가 있다"가 아니다.
    없을 때 실패로 처리하면 붉은 게이트가 상시화되고, 무시되는 게이트는
    없는 게이트와 같다.

    `CUESIFT_LIVE_API_KEY`는 **선택이라 설정하지 않아도 된다.** 로컬 Ollama는
    키를 요구하지 않는다. 변수가 없으면 `os.environ.get`이 `None`을 주고,
    어댑터는 `if self._api_key:`로 검사하므로 `None`과 빈 문자열 **둘 다**
    `Authorization` 헤더를 붙이지 않는다. `is not None`이었다면 빈 문자열에서
    `Bearer `가 나가 서버가 401을 냈을 것이고, 401은 Fatal이라 "키가 없다"가
    "키가 틀렸다"로 둔갑한다.
    """
    base_url = os.environ.get("CUESIFT_LIVE_BASE_URL")
    model = os.environ.get("CUESIFT_LIVE_MODEL")
    if not base_url or not model:
        pytest.skip("CUESIFT_LIVE_BASE_URL / CUESIFT_LIVE_MODEL이 없다")
    return OpenAICompatibleProvider(
        base_url=base_url,
        model=model,
        api_key=os.environ.get("CUESIFT_LIVE_API_KEY"),
    )


def test_실제_엔드포인트로_한_배치를_왕복한다() -> None:
    """ko->en 세 줄. 계약은 "전부 번역됐고 타임코드가 그대로다"이다."""
    segments = [
        Segment(id="s0", index=0, start_ms=0, end_ms=1000, source_text="안녕하세요."),
        Segment(id="s1", index=1, start_ms=1000, end_ms=2000, source_text="반갑습니다."),
        Segment(id="s2", index=2, start_ms=2000, end_ms=3000, source_text="잘 부탁드립니다."),
    ]

    provider = _provider()
    try:
        result = translate_segments(segments, provider=provider, source_lang="ko", target_lang="en")
    finally:
        # 직접 만든 클라이언트라 우리가 닫는다. 안 닫으면 세션 끝까지 소켓이 남는다.
        provider.close()

    assert result.failures == (), f"실패: {result.failures}"
    assert result.target_lang == "en"
    assert all(s.target_text for s in result.segments)

    # 타임코드와 순서가 그대로여야 한다 (FR-2.4). 모델이 번호를 섞으면
    # 여기가 아니라 개수 검증이 먼저 울지만, 개수가 맞은 채 내용만 밀린
    # 경우는 구조 검증이 잡지 못한다 - 그것은 Tier 0 길이비 신호의 몫이다.
    assert [(s.id, s.start_ms, s.end_ms) for s in result.segments] == [
        ("s0", 0, 1000),
        ("s1", 1000, 2000),
        ("s2", 2000, 3000),
    ]

    # NFR-2 비용 투명성. 사용량을 세지 않으면 리포트가 0원을 보고한다.
    assert result.usage.prompt_tokens > 0
    # 상한만 건다. 하한(`>= 1`)은 위의 `failures == ()`가 이미 함의해 공허하다.
    # 이 값은 "배치 1회 + 세그먼트별 폴백"에 각각 재시도가 다 붙은 최대치이고,
    # 넘으면 폴백이 아니라 재시도 루프가 폭주한 것이다.
    최대_호출 = (1 + len(segments)) * (DEFAULT_MAX_RETRIES + 1)
    assert result.usage.calls <= 최대_호출, f"호출 {result.usage.calls}회 > 상한 {최대_호출}회"
    # 폴백 발동 여부를 눈으로 읽는 자리다. 위 독스트링 "호출 횟수를 단정하지
    # 않는 이유"를 참고할 것.
    print(f"\n[live] calls={result.usage.calls} usage={result.usage}")


@pytest.mark.live
def test_cli가_실제_프로세스로_동작한다(tmp_path: Path) -> None:
    """`typer.Exit`이 실제 프로세스 종료 코드가 되는지는 CliRunner가
    완전히 증명하지 못한다. 진짜로 돌려 본다.

    **`-m live`로만 돈다.** CI에는 엔드포인트가 없다.

    `CUESIFT_LIVE_*` 접두사는 이 파일이 이미 쓰는 예약어이고
    `test_translate_api.py`의 게이트가 그 문자열로 live 마커 누락을
    판정한다. CLI가 실제로 읽는 것은 접두사 없는 `CUESIFT_BASE_URL`·
    `CUESIFT_MODEL`이므로 서브프로세스 환경에 변환해 넘긴다.
    """
    base_url = os.environ.get("CUESIFT_LIVE_BASE_URL")
    model = os.environ.get("CUESIFT_LIVE_MODEL")
    if not base_url or not model:
        pytest.skip("CUESIFT_LIVE_BASE_URL / CUESIFT_LIVE_MODEL이 없다")

    fixture = Path(__file__).parent / "fixtures" / "ingest" / "minimal.srt"
    env = {
        **os.environ,
        "CUESIFT_BASE_URL": base_url,
        "CUESIFT_MODEL": model,
        # 자식 프로세스의 stdout 인코딩을 명시한다(판정 P14). 한국어 로케일
        # Windows에서 자식의 실제 콘솔 코드페이지는 cp949인데 아래
        # `subprocess.run(..., encoding="utf-8")`은 그것을 utf-8로
        # 디코드한다고 가정한다 - 없으면 `UnicodeDecodeError: 'utf-8'
        # codec can't decode byte 0xb0`로 죽는다(실측). 부모 프로세스의
        # 환경변수만으로는 자식에게 전달되지 않으므로 여기서 명시해야 한다.
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "cuesift",
            "translate",
            str(fixture),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
    )

    assert proc.returncode in (0, 1), proc.stderr
    assert (tmp_path / "minimal.en.srt").exists()

    # 2회차는 캐시 히트라 실제 호출이 0이어야 한다 - 재개의 실물 증거다.
    again = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "cuesift",
            "translate",
            str(fixture),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
    )

    assert "실제 호출 0개" in again.stdout, again.stdout
    # [리뷰 라운드 1 Important 4] "캐시 히트 = 재개 성공"이 아니다 - `CachingProvider`는
    # 형식을 어긴 응답도 저장한다(README "형식을 어긴 응답도 캐시됩니다" 참고, 의도된
    # 설계다). 그래서 위 단언 하나만으로는 "재개가 쓰레기를 재사용하는 상태"와
    # "재개가 동작하는 상태"가 구분되지 않는다 - 리뷰어가 모든 응답을 형식 위반으로
    # 내는 가짜 엔드포인트로 재현했다: 1회차 실패 2/2·exit 1, 2회차 캐시 히트 2개·
    # 실제 호출 0개·**여전히 실패 2/2**·exit 1인데 위 단언까지는 전부 통과했다.
    # 2회차의 종료 코드와 실패 수까지 단언해야 "캐시를 재사용했다"가 아니라
    # "번역이 성공한 채로 재개됐다"를 증명한다.
    assert again.returncode == 0, again.stderr
    assert "실패 0개" in again.stdout, again.stdout


@pytest.mark.live
def test_자가일관성이_실제_엔드포인트에서_신호를_낸다(tmp_path: Path) -> None:
    """설계 §11 A4 - 가짜가 아니라 실제 모델에서 신호가 나오는지 본다.

    **점수의 크기를 단정하지 않는다.** temperature=1.0이라 재번역이 흔들리는
    정도는 모델마다 다르고, 특정 수치를 기대하면 모델을 바꿀 때마다 빨개진다.
    이 파일이 호출 횟수를 단정하지 않는 것과 같은 이유다.

    **`warn`은 기본값이 없다**(`tier1.py`의 Ruling P12) - Tier 1이 돌았을 때와
    안 돌았을 때 반환값의 형태(길이·`selected`)가 완전히 같아, `warn` 하나가
    유일한 관측 통로다. 그래서 `messages.append`를 넘기고 받은 내용을 그대로
    출력한다 - 비어 있으면 그 자체가 "Tier 1이 안 돌았다"는 신호다.

    **세그먼트에 원본 트랙에서와 같이 0이 아닌 `index`를 그대로 쓴다** -
    Task 7 착수 시점에는 `qwen2.5:3b`가 단일 세그먼트 호출에서 `index != 0`을
    무시하고 항상 `id: 0`을 답해(실측 6/6) 이 테스트가 실패했다. 원인과
    수정(로컬 `index=0`으로 재번호)은 `signals/llm.py`의 `_retranslate`
    독스트링(Ruling P13)이 단일 출처다. 여기서 실전과 다른 index를 골라
    우회하면 이 회귀를 다시 놓칠 수 있어 실전 값 그대로 둔다.
    """
    segments = [
        Segment(
            id="s0",
            index=0,
            start_ms=0,
            end_ms=2000,
            source_text="그는 끝내 오지 않았다.",
            target_text="He never came.",
        ),
        Segment(
            id="s1",
            index=1,
            start_ms=2000,
            end_ms=4000,
            source_text="비가 그치기를 기다렸다.",
            target_text="We waited for the rain to stop.",
        ),
    ]
    ctx = SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )

    provider = _provider()
    # Tier 0 신호가 없어 둘 다 위험도 0.0으로 동점이다. `budget_ratio=0.5`
    # (세그먼트 2건 → quota 1)에서는 동점을 세그먼트 id 순으로 깬다 - `s0`이
    # 선별되고 남은 `s1`(index=1)이 회색지대라 거기에 Tier 1이 붙는다.
    messages: list[str] = []
    try:
        risks = triage_with_tier1(
            segments,
            ctx,
            budget_ratio=0.5,
            provider=provider,
            max_ratio=1.0,
            warn=messages.append,
            samples=3,
            temperature=1.0,
            cache_dir=tmp_path,
            identity=provider.cache_identity,
        )
    finally:
        # 직접 만든 클라이언트라 우리가 닫는다. 안 닫으면 세션 끝까지
        # 소켓이 남는다(기존 live 테스트와 같은 이유).
        provider.close()

    names = {s.name for r in risks for s in r.signals}
    scores = [s.score for r in risks for s in r.signals if s.name == "llm.self_consistency"]
    # 이 파일의 관례대로 `-s`로 읽는다. 통과한 실행에서도 값이 보여야 한다.
    # score는 **출력만 하고 단정하지 않는다**(설계 §11 A4, 판정 P15) -
    # temperature=1.0은 변이를 보장하지 않으므로 재번역 3개가 우연히 같으면
    # score가 정확히 0.0이 되고, 그것도 "판정했고 일관됨"이라는 유효한
    # 결과다. `assert score > 0.0`을 달면 그 정상 케이스에서 간헐적으로
    # 빨개진다 - 이 파일이 호출 횟수·score 크기를 단정하지 않는 것과 같은
    # 이유다.
    print(f"\n신호: {sorted(names)}")
    print(f"score: {scores}")
    print(f"진단(warn): {messages}")
    assert "llm.self_consistency" in names
