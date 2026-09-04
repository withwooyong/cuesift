"""오류 주입기 테스트 (설계 스펙 §5).

**라운드트립이 핵심이다** — 라벨이 틀리면 모든 숫자가 틀린다(스펙 §8).
"""

from __future__ import annotations

import random

import pytest
from bench.inject import INJECTORS, inject

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment import Segment
from cuesift.spec import check_text, load_builtin

PROFILE = load_builtin("ted-en")
GLOSSARY = Glossary(entries=(GlossaryEntry(source="기후", targets=("climate",)),))


def _track(n: int = 70) -> list[Segment]:
    """**3건에 1건꼴로 부정 표현을 넣는다.**

    negation 주입기가 제거 전용이 된 뒤로 부정 표현이 없는 트랙은 자격이
    0건이라, 픽스처에 하나도 없으면 `inject`가 "실주입 0건" 가드로 죽는다.
    비율을 100%가 아니라 1/3로 둔 이유는 자격률이 `scarcity` 정렬 순서를
    바꾸기 때문이다 — 전량이 자격이면 negation이 가장 흔한 유형이 되어
    처리 순서가 실트랙과 반대로 뒤집힌다.
    """
    segs = []
    for i in range(n):
        start = i * 4000
        target = f"Climate issue number {i} is here"
        if i % 3 == 0:
            target = f"Climate issue number {i} is not here"
        segs.append(
            Segment(
                id=f"s{i:03d}",
                index=i,
                start_ms=start,
                end_ms=start + 3500,
                source_text=f"기후 변화 문제 {i} 번입니다",
                target_text=target,
            )
        )
    return segs


def test_registry_has_all_seven_types():
    """유형이 빠지면 그 유형의 Recall이 정의되지 않는데, 리포트는 조용히 넘어간다."""
    assert set(INJECTORS) == {
        "untranslated",
        "empty",
        "degeneration",
        "number",
        "glossary",
        "spec",
        "negation",
    }


def test_injection_does_not_mutate_the_input_track():
    """원본이 오염되면 '깨끗한 트랙 대비' 비교가 불가능해진다."""
    original = _track()
    before = [(s.target_text, s.end_ms) for s in original]
    inject(original, GLOSSARY, PROFILE, rate=0.10, seed=1)
    assert [(s.target_text, s.end_ms) for s in original] == before


def test_labels_are_exclusive_one_error_per_segment():
    """유형별 Recall이 정의되려면 라벨이 배타적이어야 한다(스펙 §5.5)."""
    _, labels, _ = inject(_track(), GLOSSARY, PROFILE, rate=0.10, seed=1)
    ids = [lb.segment_id for lb in labels]
    assert len(ids) == len(set(ids))


def test_injection_rate_is_respected():
    segs = _track(100)
    _, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=1)
    assert 5 <= len(labels) <= 15


def test_same_seed_gives_the_same_errors():
    """NFR-3 재현성 — 시드가 같은데 결과가 다르면 리포트를 재현할 수 없다."""
    a = inject(_track(), GLOSSARY, PROFILE, rate=0.10, seed=7)[1]
    b = inject(_track(), GLOSSARY, PROFILE, rate=0.10, seed=7)[1]
    assert [(x.segment_id, x.kind) for x in a] == [(y.segment_id, y.kind) for y in b]


@pytest.mark.parametrize("kind", sorted(INJECTORS))
def test_every_injector_actually_changes_the_segment(kind):
    """**라운드트립** — 라벨이 붙었는데 텍스트가 그대로면 그 라벨은 거짓이다.

    거짓 라벨은 분모를 부풀려 Recall을 낮추고, 원인이 검출기로 오인된다.
    """
    seg = Segment(
        id="s0",
        index=0,
        start_ms=0,
        end_ms=4000,
        source_text="기후 변화 문제 3 번입니다",
        # `not`이 들어 있어야 negation도 자격을 갖는다 — 제거 전용이라
        # 부정 표현이 없으면 이 주입기만 `None`을 낸다.
        target_text="Climate issue number 3 is not here",
    )

    result = INJECTORS[kind](seg, GLOSSARY, PROFILE, random.Random(0))
    assert result is not None, f"{kind}: 자격을 갖춘 세그먼트인데 주입되지 않았다"
    mutated, detail = result
    changed = (mutated.target_text != seg.target_text) or (mutated.end_ms != seg.end_ms)
    assert changed, f"{kind}: 라벨만 붙고 실제 변화가 없다"
    assert isinstance(detail, dict)


def test_zero_actual_injection_is_a_failure():
    """**링크 체커에서 얻은 교훈의 직접 적용.**

    "0 broken"이 통과로 읽혔던 것처럼, "용어 위반 Recall 100%"가 실은
    "용어 위반을 1건도 주입 못 했음"일 수 있다. 용어집이 비면 정확히 그렇게 된다.
    """
    empty_glossary = Glossary(entries=())
    with pytest.raises(ValueError, match="glossary"):
        inject(_track(), empty_glossary, PROFILE, rate=0.10, seed=1)


def test_ineligible_segments_are_counted_not_silently_skipped():
    """자격 미달 건수를 세지 않으면 주입 부족이 드러나지 않는다.

    **브리프 원본 픽스처(70건 전부 숫자 없음)는 실제로 돌려 보면 `ValueError`가
    난다** — number 자격 세그먼트가 0건이면 quota(1)를 절대 못 채우므로
    "실주입 0건이면 실패" 가드가 그대로 발동한다(정정 1과 무관하게 브리프
    자체의 결함). 숫자를 완전히 빼면 "미달 건수가 세어지는지"가 아니라
    "0건이면 예외가 나는지"를 재는 다른 테스트가 된다. 그래서 숫자 있는
    세그먼트를 2건만 남겨(70건 중 인덱스 0·35) quota(1)는 채워지되 그 전에
    많은 실패가 쌓이게 한다 — 실측(seed=1): `skipped["number"] == 19`.
    """
    no_numbers = [
        Segment(
            id=f"s{i}",
            index=i,
            start_ms=i * 4000,
            end_ms=i * 4000 + 3500,
            source_text="기후 변화 문제입니다",
            # negation은 3건에 1건꼴로 자격을 준다 — 이 테스트가 재는 것은
            # number의 skipped 누적이지 negation의 굶주림이 아니다.
            target_text=(
                f"Climate issue {i} is here"
                if i % 35 == 0
                else ("Climate issue is not here" if i % 3 == 0 else "Climate issue is here")
            ),
        )
        for i in range(70)
    ]
    _, _, skipped = inject(no_numbers, GLOSSARY, PROFILE, rate=0.10, seed=1)
    assert skipped.get("number", 0) > 0


def test_scarce_types_do_not_starve_the_later_ones():
    """**자격이 희소한 유형을 나중에 처리하면 뒤 유형이 굶는다.**

    알파벳순으로 돌면 glossary(자격률 2% 수준)가 인덱스를 대량 소비하고
    number·spec·untranslated가 0건이 된다 — 실트랙 en-ko(5,000건)에서
    10시드 전부 재현됐다. ja-ko는 number 자격률이 두 배라 통과하므로,
    **언어쌍 하나로만 테스트하면 이 결함이 보이지 않는다.**

    **seed=1이 아니라 seed=6을 쓴다.** 브리프가 제시한 픽스처를 500건
    규모·seed=1로 원본(정정 전) 알고리즘에 돌려 보니 통과해 버렸다 —
    글로서리 51건 중 71%(325/500)를 써 버리긴 하지만 500건 규모라
    number(5%)가 남은 175건에서 우연히 quota를 채웠다. 실측(원본 알고리즘,
    이 픽스처): seed 1~10 중 실패는 seed 6 하나뿐(`missing=['spec',
    'untranslated']`), 나머지는 우연히 통과. 표본이 작을수록 굶주림이
    운에 좌우된다는 뜻이므로, **결정론적으로 재현되는 seed=6을 고정한다**
    (정정 적용 후에는 seed=6도 통과 — 아래에서 확인).
    """
    # 용어집 키를 가진 세그먼트를 전체의 2%만 둔다(실트랙 en-ko의 1.78%를 모사).
    segs = []
    for i in range(500):
        has_term = i % 50 == 0  # 2%
        has_digit = i % 20 == 0  # 5%
        src = f"기후 변화 문제 {i}입니다" if has_term else f"어떤 문제 {i}입니다"
        tgt = "We discuss climate here" if has_term else "We discuss the topic here"
        if has_digit:
            tgt += f" in {2000 + i}"
        # negation 자격을 10%로 둔다. 이 테스트가 재는 굶주림의 주인공은
        # glossary(2%)와 number(5%)이므로, negation을 그보다 흔하게 만들어
        # `scarcity` 정렬에서 뒤로 보내야 원래 재려던 순서가 보존된다.
        if i % 10 == 0:
            tgt = tgt.replace("We discuss", "We do not discuss", 1)
        start = i * 6000
        segs.append(
            Segment(
                id=f"s{i:04d}",
                index=i,
                start_ms=start,
                end_ms=start + 5000,
                source_text=src,
                target_text=tgt,
            )
        )

    _, labels, skipped = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=6)

    by_kind = {}
    for lb in labels:
        by_kind[lb.kind] = by_kind.get(lb.kind, 0) + 1
    missing = [k for k in INJECTORS if by_kind.get(k, 0) == 0]
    assert not missing, f"실주입 0건인 유형: {missing} (자격 미달 {skipped})"


def test_negation_never_introduces_a_spec_violation():
    """**부정어 삽입이 CPS를 넘기면 의미 반전이 아니라 길이 증가를 재게 된다.**

    스펙 §5.4는 이 유형의 Recall 0이 Tier 1 투자 근거라고 못 박았다.
    오염되면 그 근거가 사라진다.
    """
    # CPS 여유가 거의 없는 세그먼트 — 'not' 삽입은 토큰이 2개 이상이어야
    # 발동하므로(코드 경로: `len(tokens) < 2`면 애초에 시도조차 안 한다),
    # 단일 토큰 텍스트로는 이 회귀를 잡지 못한다. 두 토큰(폭 15)에
    # duration을 min_duration_ms 경계(833ms)로 둬 cps0=18.0(여유 3)으로
    # 하면, "not" 삽입으로 늘어나는 폭 +4(신규 토큰 3자 + 공백 1개)가
    # cps1=22.8로 21을 넘긴다.
    tight = Segment(
        id="t0",
        index=0,
        start_ms=0,
        end_ms=833,
        source_text="가나다라마바사",
        target_text="aaaaaaa bbbbbbb",
    )
    baseline = check_text(tight.target_text, tight.duration_ms, PROFILE)
    assert baseline == [], "픽스처 전제: 원본은 깨끗하다"
    assert INJECTORS["negation"](tight, GLOSSARY, PROFILE, random.Random(0)) is None


# --- 이월 17: negation 주입이 언어를 모른다 -------------------------------------
#
# 주입기는 `target_lang`을 받지 않는다. **제거 전용 설계가 그 인자를 불필요하게
# 만든다** — 부정 표현 자체가 언어를 식별하기 때문이다. 영어 문장에 `ません`이
# 없고 일본어 문장에 `don't`가 없으므로, 매칭되는 규칙이 곧 언어 판정이다.

PROFILE_JA = load_builtin("ted-ja")


def _neg_seg(target: str, *, source: str = "가나다라마바사") -> Segment:
    """부정 규칙만 보는 최소 세그먼트.

    **`end_ms`는 `max_duration_ms`(7000) 아래여야 한다.** 넘기면 원본이 이미
    `duration_long` 위반이라 주입기가 규격 검사에서 전부 `None`을 반환하고,
    그러면 이 테스트들이 "규칙이 맞아서"가 아니라 "아무것도 시도되지 않아서"
    통과한다 — 실제로 8000ms로 썼다가 두 건이 가짜 통과했다.
    """
    return Segment(
        id="n0", index=0, start_ms=0, end_ms=5000, source_text=source, target_text=target
    )


def _negate(text: str, profile=PROFILE):
    return INJECTORS["negation"](_neg_seg(text), GLOSSARY, profile, random.Random(0))


def test_negation_never_inserts_a_foreign_token_into_japanese():
    """**ja-ko 라벨 71건 전부가 의미 반전이 아니었다**(실측, 이월 17).

    일본어 문장에 영어 `not`을 끼워 넣는 것은 의미 반전이 아니라 이물질
    삽입이다. 게다가 일본어는 공백 토큰 경계가 없어 삽입 위치조차 무의미하다
    (실측: `（笑） not この子達は利口です` — 웃음 표기 뒤에 붙었다).

    **문장에 공백이 있어야 이 회귀가 잡힌다.** 삽입 경로는 `len(tokens) < 2`면
    시도조차 하지 않으므로, 공백 없는 일본어 문장으로는 버그가 아니라
    "토큰이 모자람"을 재게 된다 — 실제로 그렇게 써서 한 번 가짜 통과했다.
    """
    out = _negate("でも 私思うんです", profile=PROFILE_JA)
    assert out is None or "not" not in out[0].target_text


def test_negation_skips_segments_without_any_negation_expression():
    """**부정 표현이 없으면 자격 미달이다.**

    삽입 경로가 살아 있으면 어떤 문장이든 라벨이 붙어, 정답지가
    '의미 반전'이 아니라 '무언가 변형됨'을 뜻하게 된다.
    """
    assert _negate("Climate issue number 3 is here") is None


def test_negation_removes_english_negation_and_keeps_it_grammatical():
    out = _negate("This was not done in isolation.")
    assert out is not None
    assert out[0].target_text == "This was done in isolation."


def test_negation_preserves_case_of_english_contractions():
    """`Don't`를 `do`로 바꾸면 문두 대문자가 사라져 **입력 파손**이 섞인다."""
    out = _negate("Don't look away from it.")
    assert out is not None
    assert out[0].target_text == "Do look away from it."


def test_negation_flips_japanese_masen_to_masu():
    out = _negate("我々が進化のゴールでもありません", profile=PROFILE_JA)
    assert out is not None
    assert out[0].target_text == "我々が進化のゴールでもあります"


def test_negation_skips_japanese_masen_that_is_not_a_negation():
    """`かもしれません → かもしれます`는 **일본어에 없는 형태**다.

    기계적 치환은 의미 반전이 아니라 입력 파손을 만든다. Q4 spike에서
    60건을 눈으로 훑어 12건가량이 이 부류였다(인수인계 "2차 ja 제외").
    """
    assert _negate("それは正しいかもしれません", profile=PROFILE_JA) is None


def test_negation_never_makes_the_text_longer():
    """제거 전용이면 규격 위반이 **원리적으로** 불가능하다.

    삽입 방식이 ja에서 1,524건을 건너뛰어 짧은 문장만 남긴 편향이 이 성질로
    사라진다.
    """
    for text, profile in (
        ("This was not done in isolation.", PROFILE),
        ("Don't look away from it.", PROFILE),
        ("我々が進化のゴールでもありません", PROFILE_JA),
    ):
        out = _negate(text, profile=profile)
        assert out is not None, text
        assert len(out[0].target_text) <= len(text), text
