"""번역된 세그먼트를 자막 파일로 쓴다 (FR-7.1 · 설계 §5.2).

**`ingest`가 pysubs2를 아는 유일한 곳이라는 §7.2의 경계를 지킨다.**
`report`는 순수 모듈이라 이것을 담을 수 없고, `output/`을 새로 만들면
pysubs2를 아는 곳이 둘로 늘어난다.

읽기와 쓰기가 같은 디렉터리에 있는 실질 이득도 있다 - 라운드트립이 깨지면
한 곳에서 드러난다.
"""

from __future__ import annotations

import contextlib
import copy
import os
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

    **예외를 잡지 않는다.** 호출자(`cli.py`)가 종료 코드로 바꾼다 - 디스크
    사정(`OSError`)과 내용 결함은 성격이 달라 코드가 갈린다.
    """
    subs = copy.deepcopy(result.subs)

    for segment in segments:
        if segment.target_text is None:
            continue
        raw_index = result.event_index[segment.id]
        event = subs.events[raw_index]
        # `target_text`에 `{...}`가 들어 있으면(LLM 출력은 우리 통제 밖이라
        # 올 수 있다) 조용히 사라진다 [실측 2026-08-17] - srt/vtt는 저장 시점에
        # 이미 지워지고, ass/ssa는 파일엔 남지만 재생 시 오버라이드 블록으로
        # 해석돼 화면에서 사라진다. ko→en/ja에서는 드물어 이번 범위에서
        # 고치지 않지만, "조용히 틀리는" 부류라 기록만 남긴다.
        prefix = _LEADING_OVERRIDES.match(event.text).group(0)
        # setter를 먼저 부르는 순서가 중요하다. 이것이 `\n`을 SSA의 `\N`으로
        # 바꿔 주고, 그 다음에 접두를 붙여야 접두가 변환 대상이 되지 않는다.
        event.plaintext = segment.target_text
        event.text = prefix + event.text

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # **임시 파일에 쓰고 `os.replace`로 갈아 끼운다 - 실패해도 잘린 자막이
    # 남지 않는다.** `subs.save`는 대상을 먼저 truncate하며 열고 **그 다음에**
    # 인코딩하므로, LLM이 낸 문자열에 고립 서로게이트가 하나만 있어도 그
    # 지점까지 쓰인 파일이 디스크에 남는다(실측: 10큐 중 5번째만 오염시키면
    # `ten_cues.en.srt`가 274바이트·큐 5개로 남고 **5번째는 타임코드만**
    # 있다). 그 파일은 "번역이 다 됐다"로 읽히는데 실제로는 절반이 없다 -
    # `report/json_report.py`가 막는 것과 **같은 병**이고, 산출물이 사용자가
    # 실제로 내보내는 자막이라 결과는 더 무겁다.
    #
    # 고립 서로게이트에 도달 경로가 있다는 것은 실측이다 -
    # `translate/openai_compat.py`의 `response.json()`이 `"\ud800"`을 그대로
    # 통과시키고 `isinstance(content, str)`도 지나간다. 요구사항정의서 §12 Q3가
    # **"로컬 LLM 백엔드의 능력이 균일하지 않다"**를 전제로 두고 있다.
    #
    # **`format_`을 명시하는 것이 이제 이 임시 파일의 전제이기도 하다.**
    # 넘기지 않으면 pysubs2가 확장자로 판별해 `.tmp`에서 죽는다(원래도
    # 확장자 없는 경로에서 죽었다). 원본 포맷을 명시하는 것이 FR-7.1의
    # "입력과 동일 포맷 기본"과도 맞는다. **이 인자를 지우면 정상 경로가
    # 통째로 깨진다** - 아래 `os.replace`가 아니라 `save`에서 깨진다.
    #
    # PID를 이름에 넣는 것은 동시 실행 때문이고, `finally`의 `unlink`를
    # `contextlib.suppress(OSError)`로 감싸는 것은 정리 실패가 진행 중이던
    # 예외를 **대체**하지 못하게 하기 위해서다 - 둘 다 `store/cache.py`의
    # `store()`가 먼저 밟은 자리이고 그쪽에 상세한 기록이 있다.
    tmp = out_path.parent / f"{out_path.name}.{os.getpid()}.tmp"
    try:
        subs.save(str(tmp), format_=result.format)
        os.replace(tmp, out_path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
