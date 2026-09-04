"""③ inject — 오류 주입과 정답 라벨 (설계 스펙 §5).

**세그먼트당 최대 1개 오류.** 유형별 Recall이 정의되려면 라벨이 배타적이어야 한다.

**어떤 유형이든 실주입이 0건이면 실패시킨다.** 링크 체커에서 얻은 교훈의
직접 적용이다 — "0 broken"이 통과로 읽혔던 것처럼 "용어 위반 Recall 100%"가
실은 "1건도 주입 못 했음"일 수 있다.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from cuesift.glossary import Glossary
from cuesift.segment import Segment
from cuesift.spec import SpecProfile, check_text

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 부정 제거 규칙. **제거만 한다 — 삽입하지 않는다.**
#
# 삽입 경로가 언어를 몰라서 일본어 문장에 영어 `not`을 끼워 넣었고, 그 결과
# **ja-ko 라벨 71건 전부가 의미 반전이 아니었다**(실측). 일본어는 공백 토큰
# 경계가 없어 삽입 위치조차 무의미했다(`（笑） not この子達は利口です`).
# en-ko도 61/71이 같은 경로를 타 `He not knew we had to get involved`처럼
# 문법이 깨진 문장이 정답지에 들어갔다.
#
# **제거 전용이면 언어 인자가 필요 없다** — 부정 표현 자체가 언어를 식별하기
# 때문이다. 영어 문장에 `ません`이 없고 일본어 문장에 `don't`가 없으므로,
# 매칭되는 규칙이 곧 언어 판정이다. 규칙이 하나도 안 걸리면 자격 미달이다.
#
# 순서가 의미를 바꾼다. `ませんでした`가 `ません`보다 **먼저** 와야 하고
# (뒤집히면 `ますでした`라는 없는 형태가 나온다), 영어 축약형이 ` not `보다
# 먼저 와야 한다.
#
# `\b`는 ASCII 규칙에만 쓴다 — CJK 텍스트에 적용하면 단어 경계가 전부 깨진다.
_NEGATION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 영어 — 축약형이 먼저다.
    (re.compile(r"\bcan(?:'|’)t\b", re.I), "can"),
    (re.compile(r"\bwon(?:'|’)t\b", re.I), "will"),
    (re.compile(r"\bdon(?:'|’)t\b", re.I), "do"),
    (re.compile(r"\bdoesn(?:'|’)t\b", re.I), "does"),
    (re.compile(r"\bdidn(?:'|’)t\b", re.I), "did"),
    (re.compile(r"\bisn(?:'|’)t\b", re.I), "is"),
    (re.compile(r"\baren(?:'|’)t\b", re.I), "are"),
    (re.compile(r"\bwasn(?:'|’)t\b", re.I), "was"),
    (re.compile(r"\bweren(?:'|’)t\b", re.I), "were"),
    (re.compile(r"\bcouldn(?:'|’)t\b", re.I), "could"),
    (re.compile(r"\bwouldn(?:'|’)t\b", re.I), "would"),
    (re.compile(r"\bshouldn(?:'|’)t\b", re.I), "should"),
    (re.compile(r"\bhaven(?:'|’)t\b", re.I), "have"),
    (re.compile(r"\bhasn(?:'|’)t\b", re.I), "has"),
    (re.compile(r"\bhadn(?:'|’)t\b", re.I), "had"),
    (re.compile(r"\bcannot\b", re.I), "can"),
    (re.compile(r"\bnever\b", re.I), "always"),
    # ` not `을 통째로 지운다. `\bnot\b`를 빈 문자열로 바꾸면 이중 공백이
    # 남아 CPS 계산이 원문과 어긋난다.
    (re.compile(r" not ", re.I), " "),
    # 일본어 — 정중체 부정만 다룬다.
    (re.compile("ませんでした"), "ました"),
    (re.compile("ません"), "ます"),
)

# `ません`을 기계적으로 `ます`로 바꾸면 **일본어에 없는 형태**가 나오는 자리.
#
# `かもしれません → かもしれます`는 의미 반전이 아니라 입력 파손이라, 그대로
# 라벨을 붙이면 역번역이 "부정이 사라졌다"가 아니라 "문장이 깨졌다"를 잡는다.
# Q4 spike에서 60건을 눈으로 훑어 12건가량이 이 부류였다.
#
# **문자열 완전 일치로 두면 같은 말의 다른 표기가 그대로 빠져나간다.**
# 한자(`かも知れません`)·히라가나(`にすぎません`)·구어(`じゃありませんか`)가
# 전부 다른 문자열이라, 제외 판단이 이미 내려진 뒤에도 자격 313건 중 4건이
# 정답지에 들어갔다(이월 19 실측, 2026-09-04). 표기 변이는 정규식으로 묶는다.
_JA_MASEN_EXCLUSIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"かも(?:知|し)れません"),
    re.compile(r"すみません"),
    re.compile(r"いけません"),
    re.compile(r"(?:過|す)ぎません"),
    re.compile(r"(?:で(?:は)?|じゃ)ありませんか"),
    re.compile(r"てなりません"),
)

# 부정 의문문은 제안·의뢰·확인이라 **긍정형과 뜻이 같다.**
# 「紙管で教会を再建しませんか？」→「再建しますか？」는 의미 반전이 아니므로
# 라벨이 거짓이 되고, Recall의 분모가 오염된다. 실측 6건(313건 중 1.9%).
_JA_NEGATIVE_QUESTION = re.compile(r"ません(?:か|かね|でしょうか)[?？!！」』）)\s]*$")

Injector = Callable[[Segment, Glossary, SpecProfile, random.Random], "tuple[Segment, dict] | None"]


@dataclass(frozen=True, slots=True)
class Label:
    """정답 한 건. `detail`은 라운드트립 검증에 쓴다."""

    segment_id: str
    kind: str
    detail: dict = field(default_factory=dict)


def _untranslated(seg, glossary, profile, rng):
    """FR-3.1 — 번역문을 ko 원문으로 되돌린다."""
    return replace(seg, target_text=seg.source_text), {"replaced_with": "source"}


def _empty(seg, glossary, profile, rng):
    """FR-3.2 — 빈 값. 공백만 남기는 경우도 섞는다."""
    value = rng.choice(["", "   "])
    return replace(seg, target_text=value), {"value": value}


def _degeneration(seg, glossary, profile, rng):
    """FR-3.3 — 마지막 어절을 3~8회 반복한다."""
    tokens = (seg.target_text or "").split()
    if not tokens:
        return None
    times = rng.randint(3, 8)
    return replace(seg, target_text=" ".join(tokens + [tokens[-1]] * times)), {"repeats": times}


def _number(seg, glossary, profile, rng):
    """FR-3.4 — 숫자 1개를 변경하거나 삭제한다. **숫자가 없으면 자격 미달.**"""
    text = seg.target_text or ""
    matches = list(_NUMBER.finditer(text))
    if not matches:
        return None
    m = rng.choice(matches)
    original = m.group()
    if rng.random() < 0.5:
        digits = original.replace(",", "")
        try:
            changed = str(int(digits) + rng.randint(1, 9))
        except ValueError:
            changed = ""
    else:
        changed = ""
    mutated = text[: m.start()] + changed + text[m.end() :]
    return replace(seg, target_text=mutated), {"from": original, "to": changed}


def _glossary(seg, glossary, profile, rng):
    """FR-3.7 — 대응어를 비등재 표현으로 치환한다.

    **원문에 용어집 키가 있고 번역문에 대응어가 있는 세그먼트만 자격이 있다.**
    둘 중 하나만 있으면 치환할 대상이 없거나 이미 위반 상태다.
    """
    text = seg.target_text or ""
    for entry in glossary.entries:
        if entry.source not in seg.source_text:
            continue
        for target in entry.targets:
            idx = text.lower().find(target.lower())
            if idx < 0:
                continue
            bogus = "thingamajig"
            mutated = text[:idx] + bogus + text[idx + len(target) :]
            return replace(seg, target_text=mutated), {"term": entry.source, "from": target}
    return None


def _spec(seg, glossary, profile, rng):
    """FR-3.8 — duration을 줄여 CPS를 넘긴다.

    텍스트 확장이 아니라 duration 축소를 쓰는 이유는, 확장하면 길이비 신호가
    함께 발화해 **라벨의 배타성이 흐려지기** 때문이다.
    """
    shrunk = max(profile.min_duration_ms // 2, int(seg.duration_ms * 0.25))
    if shrunk >= seg.duration_ms:
        return None
    return replace(seg, end_ms=seg.start_ms + shrunk), {
        "from_ms": seg.duration_ms,
        "to_ms": shrunk,
    }


def _restore_leading_case(matched: str, replacement: str) -> str:
    """치환값의 첫 글자 대소문자를 원본에 맞춘다.

    맞추지 않으면 `Don't → do`가 되어 문두 대문자가 사라진다. 그것은 의미
    반전이 아니라 **표기 파손**이고, 두 결함이 한 세그먼트에 섞이면 라벨이
    무엇을 뜻하는지 알 수 없게 된다(스펙 §5.5의 배타성).
    """
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _negation(seg, glossary, profile, rng):
    """의미 반전. **검출 담당이 없다** — 이 유형의 Recall 0이 Tier 1 투자 근거다.

    **부정 표현을 제거만 한다.** 삽입하지 않는 이유는 `_NEGATION_RULES`의
    주석에 있다 — 삽입 경로가 언어를 몰라 ja-ko 라벨 71건 전부를 무효로 만들었다.

    **부정 표현이 없으면 자격 미달이다.** 삽입 경로를 남겨 두면 어떤 문장이든
    라벨이 붙어, 정답지가 "의미 반전"이 아니라 "무언가 변형됨"을 뜻하게 된다.

    규격 검사를 남겨 둔 이유는 방어다. 제거는 텍스트를 짧게 만들어 CPS·줄
    길이 위반을 **원리적으로** 만들지 못하지만, 원본이 이미 위반인 트랙이
    들어오면 라벨 배타성이 깨진다(스펙 §5.5).
    """
    text = seg.target_text or ""

    # 개행을 지우고 판정한다. **자막은 어절 중간에 줄바꿈이 들어가**
    # (`現れるかもし\nれません`) 개행을 그대로 둔 매칭은 한 글자에 뚫린다.
    # 판정은 boolean뿐이고 치환은 원본 텍스트에 하므로 위치는 어긋나지 않는다.
    flat = text.replace("\n", "")

    # ja 제외어가 하나라도 있으면 문장 전체를 쓰지 않는다. `ません`이 여러 번
    # 나오는 문장에서 "쓸 수 있는 자리만 골라 치환"하면 어느 자리가 라벨의
    # 근거인지 감사 산출물에서 되짚을 수 없다.
    if any(x.search(flat) for x in _JA_MASEN_EXCLUSIONS):
        return None
    if _JA_NEGATIVE_QUESTION.search(flat):
        return None

    for pattern, replacement in _NEGATION_RULES:
        match = pattern.search(text)
        if match is None:
            continue
        candidate = (
            text[: match.start()]
            + _restore_leading_case(match.group(0), replacement)
            + text[match.end() :]
        )
        mutated = replace(seg, target_text=candidate)
        if check_text(candidate, mutated.duration_ms, profile):
            return None
        return mutated, {"removed": match.group(0)}

    return None


INJECTORS: dict[str, Injector] = {
    "untranslated": _untranslated,
    "empty": _empty,
    "degeneration": _degeneration,
    "number": _number,
    "glossary": _glossary,
    "spec": _spec,
    "negation": _negation,
}


def inject(
    segments: Sequence[Segment],
    glossary: Glossary,
    profile: SpecProfile,
    *,
    rate: float = 0.10,
    seed: int = 20260729,
) -> tuple[list[Segment], list[Label], dict[str, int]]:
    """오류를 주입하고 정답 라벨을 만든다.

    **입력을 변형하지 않는다** — 원본이 오염되면 "깨끗한 트랙 대비" 비교가
    불가능해진다.
    """
    if not 0.0 < rate <= 1.0:
        raise ValueError(f"rate는 0보다 크고 1 이하여야 한다 (받은 값: {rate})")

    rng = random.Random(seed)
    kinds = sorted(INJECTORS)
    target_total = round(len(segments) * rate)

    order = list(range(len(segments)))
    rng.shuffle(order)

    mutated = list(segments)
    labels: list[Label] = []
    skipped: dict[str, int] = {k: 0 for k in kinds}
    achieved: dict[str, int] = {k: 0 for k in kinds}

    quota = {k: target_total // len(kinds) for k in kinds}
    for i in range(target_total - sum(quota.values())):
        quota[kinds[i % len(kinds)]] += 1

    # 유형별 자격 세그먼트 수를 먼저 세어 처리 순서를 정한다.
    # **알파벳순으로 처리하면 자격이 희소한 유형이 인덱스를 대량 소비해
    # 뒤 유형이 굶는다** — 실트랙 en-ko에서 glossary 자격률이 1.78%라
    # 그 차례에 5,000개 중 약 3,800개를 소비하고 spec·untranslated가
    # 0건이 되어 아래 "실주입 0건" 가드가 10시드 전부 발동했다.
    # 시드를 고정한 별도 Random으로 세므로 이 사전 스캔은 본 주입에 쓰는
    # rng의 상태를 건드리지 않는다(재현성 유지).
    scarcity = {
        k: sum(
            1 for s in segments if INJECTORS[k](s, glossary, profile, random.Random(0)) is not None
        )
        for k in kinds
    }

    assigned: set[int] = set()
    for kind in sorted(kinds, key=lambda k: scarcity[k]):
        for idx in order:
            if achieved[kind] >= quota[kind]:
                break
            if idx in assigned:
                continue
            result = INJECTORS[kind](mutated[idx], glossary, profile, rng)
            if result is None:
                skipped[kind] += 1
                continue
            mutated[idx], detail = result
            assigned.add(idx)
            labels.append(Label(segment_id=mutated[idx].id, kind=kind, detail=detail))
            achieved[kind] += 1

    empty_kinds = [k for k in kinds if achieved[k] == 0]
    if empty_kinds:
        raise ValueError(
            f"실주입 0건인 유형이 있다: {', '.join(empty_kinds)}. "
            f"그대로 두면 '해당 유형 Recall 100%'가 실은 '1건도 주입 못 했음'이 된다. "
            f"자격 미달 건수: { {k: skipped[k] for k in empty_kinds} }"
        )

    # 최종 리뷰 Minor(Task 6 이월) — 0건 가드는 71/72처럼 1건 미달을 못 잡는다.
    # 리포트의 "유형별 실주입 건수"에 72가 아니라 71이 찍혀도 독자는 그것이
    # 정상 quota인지 1건 미달인지 구분할 수단이 없다. 라벨 총계가 목표(표본 ×
    # rate)에 못 미치면 실제 오류율이 낮아져 오라클 상한·배수의 분모 관계가
    # 조용히 바뀐다 — 스펙 §5.5가 "0 broken이 통과로 읽혔던 것처럼"이라며
    # 세운 원칙과 같다. `empty_kinds`와 메시지를 분리하는 이유는 0건은 원인
    # 파악(자격 미달 전멸)이 다르기 때문이다.
    shortfall = [k for k in kinds if achieved[k] < quota[k]]
    if shortfall:
        raise ValueError(
            f"quota를 채우지 못한 유형이 있다: "
            f"{ {k: f'{achieved[k]}/{quota[k]}' for k in shortfall} }. "
            f"그대로 두면 라벨 총계가 목표에 미달해 오류율이 낮아지고 "
            f"오라클 상한·배수의 분모 관계가 조용히 바뀐다. "
            f"자격 미달 건수: { {k: skipped[k] for k in shortfall} }"
        )

    return mutated, labels, skipped
