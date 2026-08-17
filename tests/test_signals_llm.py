"""자가일관성 신호 (FR-4.1 · 설계 §6)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from tests.fakes.provider import AlwaysZeroProvider, EchoProvider

from cuesift.segment import Segment
from cuesift.signals.base import SignalContext, Tier1Context
from cuesift.signals.llm import SelfConsistency
from cuesift.spec import load_builtin


@pytest.fixture
def signal_ctx() -> SignalContext:
    # 기존 신호 테스트와 같은 방식이다 (tests/test_signals_derived.py).
    # conftest.py에는 SpecProfile fixture가 없다 - 각 파일이 load_builtin을
    # 직접 부른다.
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def _seg() -> Segment:
    return Segment(
        id="1",
        index=0,
        start_ms=0,
        end_ms=2000,
        source_text="그는 오지 않았다",
        target_text="He did not come",
    )


def _ctx(signal_ctx: SignalContext, texts: list[str]) -> Tier1Context:
    """시도마다 정해진 번역문을 내는 컨텍스트.

    **`EchoProvider`를 쓰는 이유는 재시도를 없애기 위해서다.** 이 가짜는
    요청받은 id를 그대로 채워 정상 JSON을 내므로 파싱이 실패하지 않고,
    호출 횟수가 정확히 `samples`와 같아진다. 응답 문자열을 손으로 조립하면
    `parse_translations`의 정수 id 계약(커밋 `817ed64`)을 다시 구현하는
    셈이고, 그 계약이 바뀌면 이 테스트가 조용히 재시도 경로를 타게 된다.

    시도마다 다른 프로바이더를 주는 것이 실제 배선과 같다(설계 §8) -
    캐시가 attempt로 갈리므로 각 시도가 자기 응답을 받는다.
    """
    # 기본 인자로 캡처한다. 루프 변수를 클로저로 잡으면 전부 마지막 값이
    # 되고, ruff의 B023이 그것을 잡는다.
    providers = [EchoProvider(transform=lambda _src, t=t: t) for t in texts]
    return Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=len(texts),
        temperature=1.0,
    )


def test_재번역이_모두_같으면_점수가_0이다(signal_ctx):
    """흔들리지 않았다 = 이 구간은 번역하기 쉽다."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert signal.score == 0.0


def test_재번역이_흩어지면_점수가_높다(signal_ctx):
    scattered = [
        "He did not come",
        "완전히 다른 문장이 나왔다",
        "Something else entirely here",
    ]
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, scattered))
    assert signal is not None
    assert signal.score > 0.5


def test_신호에_근거가_담긴다(signal_ctx):
    """FR-6.4 - review.json이 '왜 선별되었는지'를 이것으로 쓴다."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert len(signal.detail["samples"]) == 3
    assert len(signal.detail["pairwise"]) == 3  # 3개에서 나오는 쌍의 수


def test_hard_fail이_아니다(signal_ctx):
    """의미 판단은 결정론적이지 않다. hard fail은 오탐이 곧 지표 파괴다
    (FR-6.2 · 이 저장소의 '미탐이 오탐보다 낫다')."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert signal.hard_fail is False
    assert signal.tier == 1


def test_temperature가_프로바이더까지_간다(signal_ctx):
    """`_retranslate`가 `ctx.temperature`를 흘리지 않고 상수를 넘기면
    이 테스트 전까지는 7개 테스트가 전부 그대로 통과했다(변이 실측).
    0.0이 프로바이더에 도달하면 재번역 N개가 전부 동일해져 점수가
    항상 0.0이 된다 - 신호가 죽었는데 '판정했고 안전하다'로 보고된다
    (Q3 무음 열화). `Tier1Context.__post_init__`의 `temperature > 0`
    검사는 **요청값**만 보고 도달 여부는 못 보므로, 이 저장소의 유일한
    방어가 정확히 이 배선에서 끊긴다.

    **일부러 기본값이 아닌 0.7을 쓴다.** `temperature=1.0`으로 시험하면
    `_retranslate`가 실제로 `ctx.temperature`를 넘기는지와 코드가
    "그냥 상수 1.0을 쓰는지"가 구분되지 않는다 - 1.0은 이 파일의 다른
    모든 테스트가 쓰는 값이라 상수 하드코딩 변이가 전부 생존했다
    (2라운드 리뷰 지적)."""
    providers = [EchoProvider() for _ in range(3)]
    ctx = Tier1Context(
        signal=signal_ctx, provider_for=lambda a: providers[a], samples=3, temperature=0.7
    )
    SelfConsistency().collect_tier1(_seg(), ctx)
    assert [p.kwargs for p in providers] == [[(0.7, None)]] * 3


def test_점수는_쌍별_유사도의_평균이다(signal_ctx):
    """최대/최소가 아니라 평균이다 - 흩어짐의 정의(설계 §6.1).

    `_ctx`의 표본이 서로 고르게 흩어져 있어(쌍별 유사도가 대략
    0.14~0.21로 몰림) 평균·최솟값·최댓값이 사실상 같은 값을 낸다 -
    `score > 0.5`만으로는 집계 함수를 구속하지 못한다(변이 실측:
    평균을 최솟값·최댓값으로, 나눗셈을 통째로 빼도 7개가 그대로
    통과했다). 이 테스트는 유사도가 뚜렷이 갈리는 표본을 써서
    세 집계 방식이 서로 다른 값을 내도록 만든다."""
    texts = ["He did not come", "He did not come!", "완전히 다른 문장이 나왔다"]
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, texts))
    assert signal is not None
    pw = signal.detail["pairwise"]
    assert signal.score == pytest.approx(1.0 - sum(pw) / len(pw))
    assert signal.score != pytest.approx(1.0 - min(pw))
    assert signal.score != pytest.approx(1.0 - max(pw))


def test_성공분이_2개_미만이면_None이다(signal_ctx):
    """**score=0.0이 아니다.** 0.0은 '판정했고 안전하다'이고 None은
    '판정 대상이 아니다'다 - 0점 신호를 내면 review.json이 무의미한
    항목으로 채워진다(signals/base.py의 계약).

    **경계는 정확히 성공분 1개다.** 전량 실패(0개)만 시험하면 `< 2`가
    `< 1`로 느슨해지는 변이를 못 잡는다 - 성공분 1개면
    `combinations([x], 2)`가 빈 리스트라 `sum([])/len([])`에서
    `ZeroDivisionError`가 나고, `collect_tier1`이 예외를 안 잡으므로
    앞선 세그먼트 신호까지 통째로 날아간다. garbage 2개·정상 1개로
    성공분을 정확히 1개 만든다.
    """
    # _ctx를 쓰지 않는 이유는 그쪽이 transform으로 **정상** 응답을 내기
    # 때문이다 - "망가진 문자열"을 transform에 넣어도 그냥 번역문이 된다.
    providers = [EchoProvider(garbage=True), EchoProvider(garbage=True), EchoProvider()]
    ctx = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=3,
        temperature=1.0,
    )
    assert SelfConsistency().collect_tier1(_seg(), ctx) is None


def test_성공분이_0개면_None이다(signal_ctx):
    """**표본 교체가 아니라 추가다.** 위 테스트가 성공분 1개를 시험하도록
    바뀌면서 성공분 0개(프로바이더 전량 장애) 경로가 리포에서 사라질
    뻔했다(2라운드 리뷰 지적) - `< 2`를 `== 1`로 좁히는 변이는 성공분
    1개는 여전히 `None`으로 잡지만, 성공분 0개는 가드를 통과시켜
    `combinations([], 2)` → `sum([])/len([])` → `ZeroDivisionError`를
    낸다. `collect_tier1`이 예외를 안 잡으므로 앞선 세그먼트 신호까지
    통째로 날아가고, 전량 실패는 401·네트워크 단절에서 1개 실패보다
    오히려 흔한 시나리오다."""
    providers = [EchoProvider(garbage=True) for _ in range(3)]
    ctx = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=3,
        temperature=1.0,
    )
    assert SelfConsistency().collect_tier1(_seg(), ctx) is None


def test_번역문이_없으면_None이다(signal_ctx):
    """번역 실패분은 검수 대상이 아니라 재실행 대상이다
    (TranslationResult 독스트링)."""
    seg = Segment(
        id="1",
        index=0,
        start_ms=0,
        end_ms=2000,
        source_text="그는 오지 않았다",
        target_text=None,
    )
    same = "He did not come"
    assert SelfConsistency().collect_tier1(seg, _ctx(signal_ctx, [same, same, same])) is None


def test_시도마다_다른_프로바이더를_받는다(signal_ctx):
    """설계 §8 - attempt별로 캐시가 갈려야 분산이 관측된다."""
    seen: list[int] = []
    providers = [EchoProvider() for _ in range(3)]

    def provider_for(attempt: int):
        seen.append(attempt)
        return providers[attempt]

    ctx = Tier1Context(signal=signal_ctx, provider_for=provider_for, samples=3, temperature=1.0)
    SelfConsistency().collect_tier1(_seg(), ctx)
    assert seen == [0, 1, 2]


def test_전역_index_대신_로컬_0으로_보낸다(signal_ctx):
    """Ruling P13 - 원본 전역 index를 그대로 보내면 약한 모델이 프롬프트
    예시(`{"id": 0, "text": "<번역문>"}`)의 0을 그대로 베낀다. 항목이
    여럿이면 서로 구분해야 하니 실제 id를 읽지만, **항목이 하나뿐이면
    구분할 필요가 없어 예시를 복사한다.** 실측(`qwen2.5:3b`,
    temperature=1.0, 서로 다른 문장·인덱스 2조 각 3회 = 6/6 재현):
    `index != 0`인 세그먼트는 항상 `{"id": 0, ...}`를 답해
    `parse_translations`가 "id가 누락됐다"로 거부했고, 같은 세그먼트를
    `index=0`으로만 바꾸면 3/3 성공했다.

    `loader.py`가 `id = f"{index:05d}"`로 매기고 `select_tier1_candidates`가
    작은 id를 먼저 큐에 담으므로 **실전 Tier 1 후보는 거의 항상
    `index != 0`이다** - 재번호하지 않으면 이 신호가 실물 모델에서
    조용히 항상 `None`이 될 뻔했다(Q3 무음 열화).

    `EchoProvider`는 요청받은 id를 그대로 채우는 가짜라 이 결함 자체를
    재현하지는 못한다(약한 모델의 "예시를 베끼는" 실패를 흉내 내지
    않는다) - 대신 **프롬프트에 실제로 실린 번호**를 검사해 "seg를
    로컬 index=0으로 재번호했는가"라는 배선을 직접 단정한다."""
    seg = Segment(
        id="1",
        index=7,
        start_ms=0,
        end_ms=2000,
        source_text="그는 오지 않았다",
        target_text="He did not come",
    )
    providers = [EchoProvider() for _ in range(3)]
    ctx = Tier1Context(
        signal=signal_ctx, provider_for=lambda a: providers[a], samples=3, temperature=1.0
    )
    SelfConsistency().collect_tier1(seg, ctx)
    for p in providers:
        prompt = p.calls[0][-1].content
        assert "[0] " in prompt
        assert "[7] " not in prompt


def test_증상_게이트_index가_0이_아니어도_AlwaysZeroProvider에서_신호가_난다(signal_ctx):
    """Ruling P13의 **증상** 게이트다 (최종 브랜치 리뷰 A4/A10).

    위 `test_전역_index_대신_로컬_0으로_보낸다`는 **메커니즘**(프롬프트에
    실제로 `[0]`이 실리는가)을 보고, 이 테스트는 **결과**(신호가 실제로
    나오는가)를 본다 - 재번호 배선을 되돌리면 실측으로는 **둘 다** 죽는다
    (`EchoProvider`도 프롬프트에 실린 번호를 그대로 채우므로 `[7]`이
    실리면 응답도 id 7이 되어 메커니즘 게이트의 프롬프트 단언이 깨진다,
    최종 브랜치 리뷰가 재현). 이 테스트가 따로 있을 값어치는 "지금 둘 다
    죽는가"가 아니라 **무엇을 보고 죽는가**에 있다 - 메커니즘 게이트는
    프롬프트 문자열을 보므로 프롬프트 포맷이 바뀌면(예: 마커 표기가
    `[N]`에서 다른 형식으로 바뀌면) 그 단언은 무의미해지지만, 이 게이트는
    `collect_tier1`의 반환값만 보므로 프롬프트 포맷과 무관하게 살아남는다.

    `AlwaysZeroProvider`는 실물 `qwen2.5:3b`의 실측된 실패 양식(요청받은
    id를 무시하고 항상 `id: 0`으로 답한다)을 그대로 흉내 낸다. 재번호가
    살아 있으면 로컬 `index=0`으로 보낸 요청의 기대 id도 0이라 응답의
    `id: 0`과 맞아떨어져 **성공한다.** 재번호가 없으면 원본 index(7)를
    기대하는데 응답은 항상 0이라 **파싱이 거부돼 재번역이 전부 실패하고
    `collect_tier1`이 `None`을 반환한다** - 이것이 신호가 조용히 사라지던
    실제 결함이다.

    **되돌려서 확인했다**(이 저장소의 규율 - 게이트는 실패시켜 본 뒤에야
    게이트다): `signals/llm.py`의 `local_seg = replace(seg, index=0)`을
    `local_seg = seg`로 되돌리면 이 테스트가 `assert signal is not None`에서
    **AssertionError로 죽는다.** 확인 후 원상 복구했다.
    """
    seg = Segment(
        id="1",
        index=7,
        start_ms=0,
        end_ms=2000,
        source_text="그는 오지 않았다",
        target_text="He did not come",
    )
    providers = [AlwaysZeroProvider() for _ in range(3)]
    ctx = Tier1Context(
        signal=signal_ctx, provider_for=lambda a: providers[a], samples=3, temperature=1.0
    )

    signal = SelfConsistency().collect_tier1(seg, ctx)

    assert signal is not None, "재번호가 빠지면 재번역이 전부 실패해 신호가 조용히 None이 된다"
    assert signal.name == "llm.self_consistency"


def test_samples가_호출_횟수를_정한다(signal_ctx):
    """`range(ctx.samples)`가 상수 `range(3)`으로 굳어도, 이 파일의 다른
    테스트는 전부 `samples=3`이라 못 잡는다(2라운드 리뷰 지적 - 변이
    실측: 상수로 바꿔도 전부 통과). `samples=5`로 호출해 실제 호출
    횟수·`attempt` 순서가 요청값을 그대로 따르는지 확인한다. Tier 1은
    이 저장소의 유일한 유료 계층이라 이 배선이 새면 사용자가 설정한
    예산이 조용히 다른 값으로 실행된다."""
    seen: list[int] = []
    providers = [EchoProvider() for _ in range(5)]

    def provider_for(attempt: int):
        seen.append(attempt)
        return providers[attempt]

    ctx = Tier1Context(signal=signal_ctx, provider_for=provider_for, samples=5, temperature=1.0)
    SelfConsistency().collect_tier1(_seg(), ctx)
    assert seen == [0, 1, 2, 3, 4]


def test_detail이_실제_요청값을_담는다(signal_ctx):
    """`detail["temperature"]`·`detail["requested_samples"]`가 상수로
    새거나 `len(samples)`로 새는 변이를, 이 파일의 다른 테스트는 전부
    `temperature=1.0`·전량 성공(`len(samples) == ctx.samples`)이라 못
    잡는다(2라운드 리뷰 지적). 기본값이 아닌 `temperature=0.6`을 쓰고,
    `samples=4` 중 1개를 실패시켜 **성공분(3)이 요청값(4)보다 적은**
    상태를 만든다 - 그래야 `requested_samples`가 `ctx.samples`가 아니라
    `len(samples)`로 새는 변이까지 구속한다."""
    same = "He did not come"
    providers = [EchoProvider(transform=lambda _s, t=same: t) for _ in range(3)]
    providers.append(EchoProvider(garbage=True))  # 4번째 시도만 실패시킨다
    ctx = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=4,
        temperature=0.6,
    )
    signal = SelfConsistency().collect_tier1(_seg(), ctx)
    assert signal is not None
    assert signal.detail["temperature"] == 0.6
    assert signal.detail["requested_samples"] == 4
    assert len(signal.detail["samples"]) == 3


def test_패키지_임포트만으로_tier1이_등록된다():
    """`signals/__init__.py`의 import 줄이 유일한 배선이다.

    이 파일 상단에서 `cuesift.signals.llm`을 직접 임포트하고 있어,
    그 배선 줄에서 `llm`을 빼는 변이를 넣어도 **이 파일의 다른
    테스트들은** 등록이 되살아나 잡지 못한다 - pytest가 실행 전에
    테스트 모듈을 전부 수집(import)하기 때문이다(이 테스트를 추가하기
    전 변이 실측: 지정 7파일이 123 passed로 초록). 실제 소비자
    (`bench/run.py`, 앞으로의 CLI)는 `cuesift.signals`만 임포트하므로,
    이 테스트는 별도 서브프로세스에서 그 소비자와 같은 임포트 경로만
    밟는다 - 추가한 뒤 같은 변이를 넣으면 정확히 이 테스트만 죽는다
    (1 failed, 125 passed).
    """
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cuesift.signals import registry;"
            "print(sorted(n for n, c in registry().items() if c.tier == 1))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "llm.self_consistency" in out.stdout
