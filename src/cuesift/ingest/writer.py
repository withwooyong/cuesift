"""번역된 세그먼트를 자막 파일로 쓴다 (FR-7.1 · 설계 §5.2).

**`ingest`가 pysubs2를 아는 유일한 곳이라는 §7.2의 경계를 지킨다.**
`report`는 순수 모듈이라 이것을 담을 수 없고, `output/`을 새로 만들면
pysubs2를 아는 곳이 둘로 늘어난다.

읽기와 쓰기가 같은 디렉터리에 있는 실질 이득도 있다 - 라운드트립이 깨지면
한 곳에서 드러난다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from pathlib import Path

from cuesift.ingest.loader import IngestResult
from cuesift.segment.models import Segment

# 텍스트 맨 앞의 오버라이드 블록들. `{\an8}{\i1}` 같은 연속도 한 번에 잡는다.
#
# **이 보정이 없으면 `\an8`(화면 위쪽)이 사라져 자막이 아래로 내려온다.**
# pysubs2의 `plaintext` setter가 태그를 전부 지우기 때문이다 [실측 2026-08-17].
# 중간·후행 태그는 되살릴 수 없다 - 원문 "기울임" 3글자에 걸린 강조가
# 번역문 "italic"의 어디에 걸리는지 결정할 근거가 없다 (설계 §5.2.1).
_LEADING_OVERRIDES = re.compile(r"^(?:\{[^}]*\})*")


def write_subtitle(
    result: IngestResult,
    segments: Sequence[Segment],
    out_path: Path,
) -> None:
    """`segments`의 `target_text`를 원본 자막 구조에 얹어 `out_path`에 쓴다.

    **`target_text`가 `None`인 세그먼트는 원문을 그대로 둔다** (FR-2.6 부분
    실패). 빈 문자열로 두면 화면에서 자막이 사라지는데, 그것은 "번역이
    안 됐다"보다 발견하기 어렵다 (설계 §5.3).

    **`result.subs`를 `deepcopy`한다.** 직접 고치면 `--to en,ja`에서 두 번째
    언어가 첫 번째 번역 위에 덮인다 - 같은 `IngestResult`를 두 번 쓰기
    때문이고, 예외도 경고도 없이 조용히 틀린다.

    `event_index`로 짝짓는 이유는 인제스트가 **표시되지 않는 이벤트를
    걸러냈기** 때문이다(`_keep_displayed`). 위치로 짝지으면 주석 이벤트가
    하나만 있어도 그 뒤가 전부 밀린다.
    """
    subs = copy.deepcopy(result.subs)

    for segment in segments:
        if segment.target_text is None:
            continue
        raw_index = result.event_index[segment.id]
        event = subs.events[raw_index]
        prefix = _LEADING_OVERRIDES.match(event.text).group(0)
        # setter를 먼저 부르는 순서가 중요하다. 이것이 `\n`을 SSA의 `\N`으로
        # 바꿔 주고, 그 다음에 접두를 붙여야 접두가 변환 대상이 되지 않는다.
        event.plaintext = segment.target_text
        event.text = prefix + event.text

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # `format_`을 넘기지 않으면 pysubs2가 확장자로 판별하는데, 확장자가 없는
    # 경로에서 예외가 난다. 원본 포맷을 명시하는 것이 FR-7.1의
    # "입력과 동일 포맷 기본"과도 맞는다.
    subs.save(str(out_path), format_=result.format)
