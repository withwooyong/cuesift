"""`TriageOutcome`의 파생 수치 (FR-7.2 · 설계 §7.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cuesift.report import TriageOutcome
from cuesift.report.models import (
    COST_BASIS,
    COST_BASIS_VOCABULARY,
    _validate_basis_vocabulary,
    layer_tokens_reported,
    resolve_cost_scope,
)
from cuesift.segment import Segment, SegmentRisk, Signal
from cuesift.translate.provider import TokenUsage


def _risk(
    seg_id: str,
    *,
    selected: bool = False,
    hard_fail: bool = False,
    reasons: list[str] | None = None,
    score: float = 0.5,
    signals: list[Signal] | None = None,
) -> SegmentRisk:
    return SegmentRisk(
        segment_id=seg_id,
        signals=[] if signals is None else signals,
        risk_score=score,
        hard_fail=hard_fail,
        selected=selected,
        reasons=[] if reasons is None else reasons,
    )


def _segment(seg_id: str, *, index: int = 0) -> Segment:
    return Segment(
        id=seg_id,
        index=index,
        start_ms=0,
        end_ms=1000,
        source_text="원문",
        target_text="target",
    )


def _outcome(
    *,
    risks: tuple[SegmentRisk, ...],
    excluded_failures: int = 0,
    segments: tuple[Segment, ...] | None = None,
    cost_includes: tuple[str, ...] | None = None,
    cost_unreported: tuple[str, ...] | None = None,
    usage: TokenUsage | None = None,
) -> TriageOutcome:
    """기본값은 `segments`를 `risks`에서 파생해 불변식을 만족시킨다.

    `segments`를 명시하면 그 파생을 우회한다 - 길이 불일치 방어를 실제로
    발동시키려면 헬퍼가 대신 맞춰 주지 않아야 한다.

    **`cost_includes`·`cost_unreported`도 `None`일 때는 아예 넘기지 않는다.**
    헬퍼가 언제나 명시하면 `TriageOutcome`의 기본값이 한 번도 실행되지 않는다.
    """
    extra: dict[str, object] = {}
    if cost_includes is not None:
        extra["cost_includes"] = cost_includes
    if cost_unreported is not None:
        extra["cost_unreported"] = cost_unreported
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label="예산 10%",
        policy_kind="budget",
        policy_value=0.1,
        risks=risks,
        segments=(
            tuple(_segment(r.segment_id, index=i) for i, r in enumerate(risks))
            if segments is None
            else segments
        ),
        excluded_failures=excluded_failures,
        usage=usage,
        **extra,
    )


def _usage_outcome(
    usage: TokenUsage,
    *,
    cost_includes: tuple[str, ...] | None = None,
    cost_unreported: tuple[str, ...] | None = None,
) -> TriageOutcome:
    """`usage`가 **있는** 결과. 계측 판정 테스트는 전부 이것을 쓴다.

    `usage=None`을 참 케이스로 쓰면 판정이 첫 절에서 단락돼 나머지 절을
    한 번도 밟지 않는다.
    """
    return _outcome(
        risks=(_risk("00000"),),
        usage=usage,
        cost_includes=cost_includes,
        cost_unreported=cost_unreported,
    )


def test_total은_triaged와_excluded의_합이다() -> None:
    """설계 §6.2 — 이 산수가 파일 안에서 검산된다.

    셋을 하나로 합치면 `review_ratio`의 분모가 무엇인지 소비자가 알 수 없고,
    배수의 분모가 조용히 틀린다.

    **픽스처를 비대칭으로 짠다.** risks 1개·선별 0개로는 `len(risks)`도,
    여집합 `sum(not selected)`도, `len(segments)`도 전부 1을 내 `triaged`를
    무엇으로 바꿔 놓아도 통과한다. 선별 1개를 섞어 여집합이 다른 값을 내게 한다.
    """
    outcome = _outcome(
        risks=(_risk("00000", selected=True), _risk("00001")),
        excluded_failures=3,
    )

    assert outcome.triaged_segments == 2  # 여집합(선별 안 된 것)은 1이다
    assert outcome.excluded_failures == 3
    assert outcome.total_segments == 5


def test_selected는_참인_것만_원래_순서로_낸다() -> None:
    """이 튜플의 순서가 곧 `review.json`의 `segments[]` 순서라 NFR-3 대상이다.

    **선별을 2개로 둔다.** 1개뿐이면 `tuple(reversed(...))`로 뒤집어도 결과가
    같아 순서가 검증되지 않는다 - 같은 파일이 `signal_hits`에는 정렬 테스트를
    따로 두고 있어 비대칭이었다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", selected=True),
            _risk("00001"),
            _risk("00002", selected=True),
        )
    )

    assert outcome.selected_for_review == 2
    assert [r.segment_id for r in outcome.selected] == ["00000", "00002"]


def test_signal_hits는_선별되지_않은_것도_건수로_센다() -> None:
    """집계는 `risks` **전체**를 본다.

    선별분으로 좁히면 "예산 밖으로 밀린 위험"이 사라져 사용자가 다음 예산을
    정할 근거를 잃는다. 화면 요약이 이미 같은 규칙을 따른다.

    **같은 사유를 2회 담는다.** 모든 사유가 1회씩이면 집계를 버리고 존재
    여부만 내는 구현(`{k: 1 for k in sorted(counts)}`)도, 반환형을 `set`으로
    바꾼 구현도 통과한다 - 프로퍼티 이름이 "적발 **건수**"이고 화면이
    `"{name} {count}개"`로 찍는데도 그렇다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", selected=True, reasons=["spec.violation"]),
            _risk("00001", selected=False, reasons=["struct.empty", "spec.violation"]),
        )
    )

    assert outcome.signal_hits == {"spec.violation": 2, "struct.empty": 1}


def test_signal_hits는_이름순으로_정렬된다() -> None:
    """NFR-3 재현성 — `Counter`의 순서는 삽입 순이라 세그먼트 순서가 바뀌면 흔들린다."""
    outcome = _outcome(
        risks=(
            _risk("00000", reasons=["struct.empty"]),
            _risk("00001", reasons=["glossary.miss"]),
        )
    )

    assert list(outcome.signal_hits) == ["glossary.miss", "struct.empty"]


def test_hard_fail_count는_전체에서_센다() -> None:
    """**3개 중 2개로 비대칭이다.** 2개 중 1개면 여집합(`if not r.hard_fail`)도
    똑같이 1을 내 뒤집힌 구현이 살아남는다. hard fail은 검수 예산을 우회하므로
    (FR-6.2) 이 수치가 뒤집히면 실제 검수 비율이 부풀어 Recall@Budget이 무너진다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", hard_fail=True),
            _risk("00001", hard_fail=True),
            _risk("00002"),
        )
    )

    assert outcome.hard_fail_count == 2  # 여집합은 1이다


def test_review_ratio는_triaged를_분모로_쓴다() -> None:
    """실패분을 분모에 넣으면 README 배수가 무너진다 (설계 §6.2)."""
    outcome = _outcome(
        risks=(_risk("00000", selected=True), _risk("00001"), _risk("00002"), _risk("00003")),
        excluded_failures=6,
    )

    # 분모가 triaged(4)면 0.25, total(10)이면 0.1이다.
    assert outcome.review_ratio == pytest.approx(0.25)


def test_risks가_비면_review_ratio는_0이다() -> None:
    """전량 번역 실패 경로. `ZeroDivisionError`가 아니라 0.0이어야 한다."""
    outcome = _outcome(risks=(), excluded_failures=10)

    assert outcome.review_ratio == 0.0
    assert outcome.total_segments == 10
    assert outcome.selected_for_review == 0


def test_segments와_risks의_길이가_다르면_거부한다() -> None:
    """`segments`는 `risks`와 같은 집합이라는 것이 이 모델의 계약이다.

    깨진 채로 통과시키면 리포트 생성기의 `by_id[risk.segment_id]`가 KeyError를
    내는데, 그 시점은 파일을 쓰는 도중이라 어느 조합이 어긋났는지 스택에 남지
    않는다. 생성 시점으로 실패를 앞당긴다.
    """
    with pytest.raises(ValueError, match="길이가 다르다"):
        _outcome(
            risks=(_risk("00000"), _risk("00001")),
            segments=(_segment("00000"),),
        )


def test_segments와_risks의_id가_다르면_거부한다() -> None:
    """**길이가 같아도** id가 어긋나면 거부한다.

    길이만 보는 검증은 이 입력을 통과시킨다. 그러면 리포트 생성기가
    `{s.id: s for s in segments}`를 `risk.segment_id`로 찾는 순간 KeyError가
    나는데, 그 시점은 파일을 쓰는 도중이라 어느 조합이 어긋났는지 스택에 없다.
    """
    with pytest.raises(ValueError, match="집합이 다르다"):
        _outcome(
            risks=(_risk("00000"), _risk("00001")),
            segments=(_segment("00000"), _segment("99999", index=1)),
        )


def test_순서만_다른_것은_통과한다() -> None:
    """`risks`는 위험도 내림차순, `segments`는 트랙 원본 순서라 **순서가 다른 것이 정상이다.**

    검증을 순서까지 고정하면(`tuple(...)` 비교) 이 정상 입력이 `ValueError`로
    거부돼 트리아지 경로가 통째로 죽는다. 그 회귀를 막는 것이 이 테스트다 -
    "더 엄격하게" 조이는 변경은 게이트가 없으면 아무 소리도 내지 않는다.
    """
    outcome = _outcome(
        risks=(_risk("00001"), _risk("00000")),
        segments=(_segment("00000"), _segment("00001", index=1)),
    )

    assert outcome.triaged_segments == 2
    assert [r.segment_id for r in outcome.risks] == ["00001", "00000"]


def test_excluded_failures가_음수면_거부한다() -> None:
    """음수는 `total_segments`를 `triaged_segments`보다 작게 만들어 화면이
    "2개 중 5개 검수"라는 불가능한 요약을 낸다. 프로그램은 정상 종료하고 종료
    코드도 0이라 어떤 게이트에도 걸리지 않는다.
    """
    with pytest.raises(ValueError, match="음수다"):
        _outcome(risks=(_risk("00000"),), excluded_failures=-5)


def test_cost_includes는_기본값이_있다() -> None:
    """기존 생성부 전부가 이 인자 없이 계속 동작해야 한다."""
    outcome = _outcome(risks=(_risk("00000"),))

    assert outcome.cost_includes == ("translation",)


def test_등록되지_않은_계층은_생성_시점에_거부된다() -> None:
    """**범위를 넓히면 규약도 함께 선언하게 만든다** (Task 3 리뷰 이월).

    막지 않으면 `cost_includes=("translation", "tier2")` 같은 값이 그대로 파일에
    실리고, 그 계층이 캐시 적중을 세는지 아닌지는 아무 데도 적히지 않은 채
    소비자가 추측하게 된다. 여기서 던지면 계층을 늘린 사람이 `COST_BASIS`에
    한 줄을 더할 수밖에 없다.
    """
    with pytest.raises(ValueError, match="tier2"):
        _outcome(risks=(_risk("00000"),), cost_includes=("translation", "tier2"))


def test_token_counts_reported가_계측_불능을_구별한다() -> None:
    """§12 Q3 - 백엔드 능력이 균일하지 않으므로 **탐지**가 필요하다.

    `calls > 0`인데 토큰 합이 0인 것은 "공짜로 돌았다"가 아니라 "백엔드가 usage를
    안 냈다"이다. 이 구별이 없으면 `cost`가 0을 사실로 보고한다(NFR-2).

    **세 갈래를 한 테스트가 다 밟는다.** 이전 판은 참 케이스가 `usage=None`이라
    첫 절에서 단락됐고, `calls == 0` 절을 지워도 이 파일은 전원 통과했다 -
    죽는 것이 `test_report_json.py` 하나뿐이라 **게이트가 다른 계층 파일에
    의존**하는 상태였다.
    """
    assert _usage_outcome(TokenUsage(12, 34, calls=2)).token_counts_reported is True
    assert _usage_outcome(TokenUsage(0, 0, calls=0)).token_counts_reported is True
    assert _usage_outcome(TokenUsage(0, 0, calls=3)).token_counts_reported is False


def test_usage가_없으면_계측을_믿을_수_없다고_본다() -> None:
    """**"모른다"를 "믿을 수 있다"로 보고하지 않는다.**

    수치가 아예 없으면 신뢰성을 뒷받침할 것이 없다. 소음 우려가 없는 것은
    실측으로 확인했다 - 생산 경로의 `TriageOutcome` 생성부는 `cli.py` 한
    곳이고 언제나 `usage=translated.usage`를 넘기며 `TranslationResult.usage`는
    `TokenUsage`라 `None`이 될 수 없다. 즉 `None`은 테스트에서만 온다.
    """
    assert _outcome(risks=(_risk("00000"),)).usage is None
    assert _outcome(risks=(_risk("00000"),)).token_counts_reported is False
    assert layer_tokens_reported(None) is False


def test_계층별_신고가_합계에_가려진_무음을_잡는다() -> None:
    """**A: 1토큰이 999회 무음 호출을 가린다.**

    번역은 상용 API(토큰을 낸다)이고 Tier 1은 로컬 Ollama(안 낸다)인 구성이
    §12 Q3가 경고한 바로 그것이고 가장 흔하다. 합계만 보면 토큰이 0이 아니라
    통과하는데, 실제로는 Tier 1 호출 전량의 비용을 모른다.

    **부분 열화를 직접 조립해서 잰다** - 합이 0인 케이스만 덮으면 이 회귀를
    영영 놓친다.
    """
    합계는_0이_아니다 = TokenUsage(prompt_tokens=1, completion_tokens=0, calls=1000)

    가려짐 = _usage_outcome(합계는_0이_아니다, cost_includes=("translation", "tier1"))
    assert 가려짐.token_counts_reported is True, "이 줄이 바로 결함의 모양이다"

    신고됨 = _usage_outcome(
        합계는_0이_아니다,
        cost_includes=("translation", "tier1"),
        cost_unreported=("tier1",),
    )
    assert 신고됨.token_counts_reported is False


def test_resolve_cost_scope가_합계까지_함께_낸다() -> None:
    """**합계를 여기서 내지 않으면 호출자가 손으로 합치고, 그 순간 함정이 열린다.**

    합친 값을 키 하나로 넘기면 `{"translation": tr + t1}` → `includes=("translation",)`
    이 되어 Tier 1이 범위에서 조용히 사라지고 판정도 통과한다(실측). 함수는
    호출자가 무엇을 합쳤는지 알 수 없어 **검증으로는 막을 수 없다** - 손으로
    합칠 이유를 없애는 것이 유일한 방어다.

    입력 순서를 보존해야 한다 - 그 순서가 곧 `includes`의 순서이고 파일에
    나간다(NFR-3 재현성).
    """
    scope = resolve_cost_scope(
        {
            "translation": TokenUsage(10, 20, calls=2),
            "tier1": TokenUsage(0, 0, calls=9),
        }
    )

    assert scope.includes == ("translation", "tier1")
    assert scope.unreported == ("tier1",)
    # 합계는 두 계층을 다 담는다 - 호출자가 따로 더할 것이 남으면 안 된다.
    assert scope.usage == TokenUsage(10, 20, calls=11)

    # 그대로 생성자에 넣을 수 있어야 한다 - 넣을 수 없으면 헬퍼가 아니다.
    outcome = _outcome(
        risks=(_risk("00000"),),
        usage=scope.usage,
        cost_includes=scope.includes,
        cost_unreported=scope.unreported,
    )
    assert outcome.token_counts_reported is False


def test_돌지_않은_계층은_범위에서_빠진다() -> None:
    """**`None`은 "안 돌았다"이지 "돌았는데 계측이 죽었다"가 아니다.**

    Tier 1을 끈 실행에서 `"tier1"`이 `includes`에 남으면 파일이 "이 숫자는 Tier 1도
    덮는다"고 말하고, `unreported`에까지 실리면 사용자가 **없는 비용을 의심한다.**
    호출자가 dict를 조건부로 조립하지 않아도 되게 하려는 것이 이 특례다.
    """
    scope = resolve_cost_scope({"translation": TokenUsage(10, 20, calls=2), "tier1": None})

    assert scope.includes == ("translation",)
    assert scope.unreported == ()
    assert scope.usage == TokenUsage(10, 20, calls=2)


def test_돈_계층이_하나도_없으면_거부한다() -> None:
    """`TriageOutcome`도 빈 범위를 거부하지만 그 메시지는 **생성부**를 가리켜
    원인이 여기(잘못 조립한 매핑)라는 것을 감춘다.
    """
    with pytest.raises(ValueError, match="실제로 돈 계층이 없다"):
        resolve_cost_scope({"translation": None, "tier1": None})


def test_CostScope는_이름으로_받는다() -> None:
    """**`includes`와 `unreported`는 둘 다 `tuple[str, ...]`이다.**

    위치로 받으면 둘이 뒤바뀌어도 타입으로는 드러나지 않고, 그때 "범위"와
    "무음 계층"이 통째로 맞바뀐 채 파일에 실린다. 필드 이름이 그 실수를
    성립하지 않게 한다.
    """
    scope = resolve_cost_scope({"translation": TokenUsage(1, 1, calls=1)})

    assert (scope.usage, scope.includes, scope.unreported) == (
        TokenUsage(1, 1, calls=1),
        ("translation",),
        (),
    )


def test_cost_unreported는_범위_밖_계층을_거부한다() -> None:
    """실리지 않는 판정을 받아 두면 배선부는 "신고했다"고 믿는데 리포트는 침묵한다."""
    with pytest.raises(ValueError, match="tier1"):
        _outcome(risks=(_risk("00000"),), cost_unreported=("tier1",))


def test_빈_cost_includes를_거부한다() -> None:
    """**C1: `includes`의 존재 이유가 정확히 뒤집힌 채로 파일이 나간다.**

    `{"includes": [], "prompt_tokens": 1234}`는 종료 코드 0으로 통과하면서
    NFR-2 비용 투명성을 부정한다. "호출자가 그러지 않는다"에 기대지 않는 것은
    `triage_with_tier1`이 중복 id를 검사하는 이유와 같다 - 공개 데이터클래스다.
    """
    with pytest.raises(ValueError, match="비었다"):
        _outcome(risks=(_risk("00000"),), cost_includes=())


def test_중복_계층을_거부한다() -> None:
    """중복이면 `cost_basis`의 dict 컴프리헨션이 조용히 dedupe해
    `includes` 2개 / `basis` 1개가 나간다 - 파일만 보는 소비자는 판정할 수 없다.
    """
    with pytest.raises(ValueError, match="중복"):
        _outcome(risks=(_risk("00000"),), cost_includes=("translation", "translation"))


def test_문자열을_그대로_넘기면_원인을_말해_준다() -> None:
    """**D2: 우연히 거부되는 것과 원인을 말하는 것은 다르다.**

    `"translation"`은 문자 단위로 쪼개져 `['t','r','a',...]`가 된다. 가드가
    없으면 미등록 계층 메시지가 그 글자 목록을 뱉어 사람이 원인을 못 찾는다.
    """
    with pytest.raises(ValueError, match="문자열이 왔다"):
        _outcome(risks=(_risk("00000"),), cost_includes="translation")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="문자열이 왔다"):
        _outcome(risks=(_risk("00000"),), cost_unreported="translation")  # type: ignore[arg-type]


def test_규약_어휘는_닫혀_있다() -> None:
    """**C2: 키만 파생시키면 절반만 닫힌다.**

    값에는 검사가 없어 오타(`cached-incuded`)나 §8.4에 없는 제3의 어휘
    (`estimated`)를 등록해도 그대로 JSON에 실린다. 기존 두 값은 리포트
    테스트가 지키지만 **새 계층은 무방비**였다.
    """
    assert set(COST_BASIS.values()) <= COST_BASIS_VOCABULARY

    with pytest.raises(ValueError, match="cached-incuded"):
        _validate_basis_vocabulary({"tier2": "cached-incuded"})
    with pytest.raises(ValueError, match="estimated"):
        _validate_basis_vocabulary({"tier2": "estimated"})


def test_규약_어휘의_단일_출처는_요구사항정의서_8_4다() -> None:
    """코드만 고치고 문서를 안 고치면 여기서 걸린다.

    소비자는 `review.json`의 낱말을 §8.4에서 찾는다. 문서에 없는 낱말이 파일에
    실리면 그 낱말은 **아무 데도 정의돼 있지 않다** - `basis`를 더한 이유가
    "규약을 밝힌다"였는데 밝힌 곳이 없어지는 것이다.
    """
    본문 = (Path(__file__).resolve().parents[1] / "docs" / "요구사항정의서.md").read_text(
        encoding="utf-8"
    )
    # **§8.4 안에서만 찾는다.** 문서 어디에 있든 통과하면 어휘 선언이 §8.4에서
    # 다른 절로 옮겨가도 게이트가 침묵한다 - 소비자는 `review.json`의 계약을
    # §8.4에서 찾으므로 그 절에 없는 선언은 없는 것과 같다.
    시작 = 본문.index("### 8.4 `review.json` 구조")
    절 = 본문[시작 : 본문.index("\n## ", 시작)]

    선언 = [line for line in 절.splitlines() if "cost.basis`의 어휘는" in line]
    assert len(선언) == 1, f"§8.4의 어휘 선언 줄을 1개 찾아야 한다 (찾은 것: {len(선언)})"

    # **콜론으로 자르지 않는다.** 문장에 콜론이 하나 더 들어오면 파싱이 어긋난다.
    # 어휘는 하이픈으로 이어진 소문자라 그 형태로 직접 집는다 - 같은 줄의
    # `cost.basis`(점)와 `COST_BASIS_VOCABULARY`(대문자)는 걸리지 않는다.
    # 하이픈 없는 어휘가 새로 생기면 이 단언이 불일치로 시끄럽게 실패한다.
    문서_어휘 = set(re.findall(r"`([a-z]+(?:-[a-z]+)+)`", 선언[0]))
    assert 문서_어휘 == set(COST_BASIS_VOCABULARY)
