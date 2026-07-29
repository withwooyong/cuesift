"""타임코드 합성 (설계 스펙 §4.2).

FR-2.4가 "번역이 타임코드를 보존한다"고 규정하므로 시간은 원문(ko)에 붙고
en·ja가 물려받는다. 그런데 같은 시간에 en은 42자, ja는 13자 제한을 받으므로
**ko 기준으로만 정하면 번역 쪽에 규격 위반이 무작위로 섞여 들어온다.**
그러면 이후 검출되는 위반이 주입분인지 합성 실패인지 구분할 수 없다.

그래서 duration을 **세 언어가 모두 만족하는 값**으로 잡는다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from bench.corpus import SentencePair
from cuesift.spec import SpecProfile, text_width

# CPS 한도에 정확히 붙이면 부동소수 반올림 한 번으로 위반이 된다.
# 1.10이면 여유가 충분하면서도 duration이 비현실적으로 길어지지 않는다.
# 이 값이 너무 작으면 깨끗함 불변식(Task 4)이 실패로 잡아낸다.
SAFETY = 1.10

# 세그먼트 사이 고정 간격. 0이면 `end == start` 경계가 되는데,
# check_overlaps는 그것을 겹침으로 보지 않지만 duration 반올림이
# 한 번만 어긋나도 겹침이 생긴다 (FR-5.1).
GAP_MS = 120


@dataclass(frozen=True, slots=True)
class TimedText:
    """줄바꿈까지 확정된 텍스트와 그것을 담을 수 있는 duration."""

    source_text: str
    target_text: str
    duration_ms: int


def wrap_text(text: str, profile: SpecProfile) -> str | None:
    """`profile`의 줄 수·줄 길이 안에 담기도록 줄바꿈을 넣는다.

    담을 수 없으면 `None`. **억지로 담지 않는다** — 한도를 넘긴 채 넣으면
    그 세그먼트가 트랙 내내 규격 위반으로 잡히는 영구 오탐이 된다.

    공백이 있으면 어절 단위로, 없으면 문자 단위로 나눈다. TED2020의
    일본어는 실제로 공백으로 어절을 구분하지만(예: "どうもありがとう
    クリス"), URL처럼 공백이 전혀 없는 텍스트도 있을 수 있다. 그때
    공백 분할만 쓰면 텍스트가 통째로 한 줄이 되어 전량이 줄길이 위반이 된다.
    """
    limit = profile.max_chars_per_line
    mode = profile.char_counting

    if text_width(text, mode) <= limit:
        return text

    lines: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    tokens = text.split(" ")
    has_spaces = len(tokens) > 1

    units = [t + " " for t in tokens[:-1]] + [tokens[-1]] if has_spaces else list(text)

    for unit in units:
        candidate = current + unit
        if text_width(candidate.rstrip(), mode) <= limit:
            current = candidate
            continue
        flush()
        # 단위 하나가 한 줄을 넘으면(긴 URL, 공백 없는 긴 어절) 담을 수 없다.
        if text_width(unit.rstrip(), mode) > limit:
            return None
        current = unit
    flush()

    if len(lines) > profile.max_lines:
        return None
    return "\n".join(ln.rstrip() for ln in lines)


def required_duration_ms(texts: Mapping[str, str], profiles: Mapping[str, SpecProfile]) -> int:
    """세 언어의 CPS 한도를 **모두** 만족하는 최소 duration.

    가장 빡빡한 언어가 값을 정한다. 하나라도 넘으면 그 언어의 트랙이
    규격 위반으로 오염되고, 깨끗한 트랙 전제가 무너진다.
    """
    needed = 0.0
    for lang, text in texts.items():
        profile = profiles[lang]
        # 줄바꿈은 표시 폭이 아니다. CPS 계산에서 빼지 않으면 2줄 세그먼트가
        # 실제보다 길게 계산돼 duration이 불필요하게 늘어난다.
        width = text_width(text.replace("\n", ""), profile.char_counting)
        needed = max(needed, width / profile.max_cps * 1000.0)

    duration = math.ceil(needed * SAFETY)
    floor = max(p.min_duration_ms for p in profiles.values())
    return max(duration, floor)


def plan_segment(
    pair: SentencePair,
    target_lang: str,
    profiles: Mapping[str, SpecProfile],
) -> TimedText | None:
    """문장 쌍 하나를 타임코드가 붙을 수 있는 형태로 만든다.

    담을 수 없으면 `None`을 돌려 **표본에서 제외**한다. 제외 건수 자체가
    결과다(§4.4) — "ko 자막을 그대로 en·ja로 옮겼을 때 몇 %가 물리적으로
    규격을 만족시킬 수 없는가"는 FR-5.4(규격 자동 교정)를 정량적으로
    정당화하는 숫자다.
    """
    active = {"ko": profiles["ko"], target_lang: profiles[target_lang]}

    wrapped_source = wrap_text(pair.source, active["ko"])
    wrapped_target = wrap_text(pair.target, active[target_lang])
    if wrapped_source is None or wrapped_target is None:
        return None

    texts = {"ko": wrapped_source, target_lang: wrapped_target}
    duration = required_duration_ms(texts, active)

    ceiling = min(p.max_duration_ms for p in active.values())
    if duration > ceiling:
        return None

    return TimedText(
        source_text=wrapped_source,
        target_text=wrapped_target,
        duration_ms=duration,
    )
