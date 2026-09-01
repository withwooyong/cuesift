"""`report.html` 렌더러 (FR-7.3 · 설계 §7.2).

**`string.Template`을 쓴다.** f-string은 CSS의 중괄호를 전부 두 배로 쓰게 하고,
하나만 빠뜨리면 예외가 아니라 조용히 깨진 CSS가 나간다 - pytest는 문자열 포함
여부만 보므로 못 잡고 브라우저에서 눈으로만 보인다. `ElementTree`는
`script`·`style`의 내용을 이스케이프해 JS를 망가뜨린다(설계 D5 · §3.5).

**`safe_substitute`를 쓰지 않는다.** 치환 누락이 조용해져 D5의 취지가 사라진다 -
`$table`이 그대로 출력에 남아도 아무도 모른다. `substitute`는 `KeyError`로 즉사한다.

**템플릿을 별도 `.html` 자산으로 빼지 않는다.** `specs/*.yaml`과 달리 사용자가
편집할 물건이 아니고, 자산으로 빼면 hatch의 `force-include`를 건드려 **휠에서만
누락되는** 실패를 새로 만든다.
"""

from __future__ import annotations

import contextlib
import html
import os
from pathlib import Path
from string import Template

from cuesift.report.highlight import split_spans
from cuesift.report.models import TriageOutcome
from cuesift.segment import Segment, SegmentRisk, Signal

_CSS = """
:root { color-scheme: light dark; }
body {
  font-family: system-ui, -apple-system, "Malgun Gothic", sans-serif;
  margin: 0; padding: 1.5rem; line-height: 1.6;
}
h1 { font-size: 1.25rem; margin: 0 0 1rem; }
.summary { border: 1px solid currentColor; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
.summary dl { display: flex; flex-wrap: wrap; gap: 1.5rem; margin: 0; }
.summary div { min-width: 8rem; }
.summary dt { font-size: 0.8rem; opacity: 0.7; }
.summary dd { margin: 0; font-size: 1.4rem; font-variant-numeric: tabular-nums; }
.meta { margin: 0.75rem 0 0; font-size: 0.85rem; opacity: 0.7; }
/* 배경과 글자색을 **함께** 지정한다. 이 문서는 `color-scheme: light dark`라
   한쪽만 주면 다크 모드에서 밝은 배경 위에 밝은 글자가 되어 배지가 사라진다 -
   나머지 표는 `currentColor`로만 그려 이 문제가 없다. */
.badge-stt { margin-left: 0.4em; padding: 0 0.35em; border-radius: 3px;
             background: #fef7e0; color: #7a5900; font-size: 0.8em; white-space: nowrap; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td {
  border-bottom: 1px solid currentColor; padding: 0.5rem;
  text-align: left; vertical-align: top;
}
th { font-size: 0.75rem; opacity: 0.7; }
td.tc, td.score, td.id { white-space: nowrap; font-variant-numeric: tabular-nums; }
tr.seg[data-hardfail="1"] td.score { font-weight: 700; }
mark { background: Highlight; color: HighlightText; padding: 0 0.1em; border-radius: 2px; }
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.filters .sigs { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.filters label { font-size: 0.85rem; cursor: pointer; }
.filters .count { margin: 0; font-size: 0.85rem; opacity: 0.7; }
noscript { display: block; width: 100%; font-size: 0.85rem; opacity: 0.8; }
tr.seg[hidden] { display: none; }
"""

# **`tr.seg[hidden]`이 없으면 필터가 화면에서만 안 먹는다.** 브라우저 기본
# 스타일시트의 `tr { display: table-row }`는 `[hidden]`의 `display: none`과 명시도가
# 같아 나중에 선언된 쪽이 이기는데, 그것이 기본 스타일이다 - JS는 `row.hidden`을
# 정상으로 세우고 행은 그대로 보인다. 예외도 콘솔 경고도 없다.

_JS = """
(function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll('tr.seg'));
  var hardOnly = document.getElementById('f-hardfail');
  var sigBoxes = Array.prototype.slice.call(document.querySelectorAll('.f-sig'));
  var counter = document.getElementById('count');
  if (!rows.length || !hardOnly || !counter) { return; }

  function apply() {
    var allowed = {};
    sigBoxes.forEach(function (box) { if (box.checked) { allowed[box.value] = true; } });
    var shown = 0;
    rows.forEach(function (row) {
      var names = (row.getAttribute('data-signals') || '').split(' ').filter(Boolean);
      // 신호가 하나도 없는 행은 신호 필터로 거르지 않는다 - 거를 근거가 없다.
      var bySignal = !names.length || names.some(function (n) { return allowed[n]; });
      var byHard = !hardOnly.checked || row.getAttribute('data-hardfail') === '1';
      var visible = bySignal && byHard;
      row.hidden = !visible;
      if (visible) { shown += 1; }
    });
    counter.textContent = String(shown);
  }

  hardOnly.addEventListener('change', apply);
  sigBoxes.forEach(function (box) { box.addEventListener('change', apply); });
  apply();
})();
"""

_SHELL = Template(
    """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>$css</style>
</head>
<body>
<h1>$title</h1>
$summary
$filters
$table
<script>$js</script>
</body>
</html>
"""
)

_SUMMARY = Template(
    """<section class="summary">
<dl>
<div><dt>총 세그먼트</dt><dd>$total</dd></div>
<div><dt>검수 대상</dt><dd>$selected</dd></div>
<div><dt>실제 검수 비율</dt><dd>$ratio</dd></div>
<div><dt>hard fail</dt><dd>$hard_fail</dd></div>
<div><dt>번역 실패</dt><dd>$excluded</dd></div>
</dl>
<p class="meta">$source_lang -&gt; $target_lang · 규격 $profile · 정책 $policy$origin</p>
</section>"""
)

_FILTERS = Template(
    """<section class="filters">
<label><input type="checkbox" id="f-hardfail"> hard fail만</label>
<div class="sigs">$checkboxes</div>
<p class="count">표시 중 <span id="count">$total</span> / $total</p>
<noscript>브라우저에서 스크립트를 쓸 수 없어 필터가 동작하지 않습니다. 전량을 표시합니다.</noscript>
</section>"""
)

_CHECKBOX = Template(
    '<label><input type="checkbox" class="f-sig" value="$name" checked> $name</label>'
)

_TABLE = Template(
    """<table>
<thead><tr><th>ID</th><th>시각</th><th>위험도</th><th>원문</th><th>번역</th><th>사유</th></tr></thead>
<tbody id="rows">$rows</tbody>
</table>"""
)

_ROW = Template(
    """<tr class="seg" data-hardfail="$hardfail" data-signals="$signals" data-stt="$stt">
<td class="id">$id$badge</td>
<td class="tc">$timecode</td>
<td class="score">$score</td>
<td class="src">$source</td>
<td class="tgt">$target</td>
<td class="why">$reasons</td>
</tr>"""
)


def esc(value: object) -> str:
    """HTML 이스케이프. 속성에도 들어가므로 따옴표까지 변환한다.

    **`quote=True`가 기본이지만 명시한다** - 이 함수가 속성값에도 쓰이므로
    누군가 `quote=False`로 바꾸면 원문의 따옴표가 속성을 탈출한다.
    """
    return html.escape(str(value), quote=True)


# STT 원문 배지 (FR-1.4). `_ROW`의 `id` 칸에 그대로 얹힌다.
#
# **두 문구에 `<`·`>`·`"`·`&`가 들어가면 이 상수가 마크업을 깨뜨린다.** 여기에
# `esc`를 감아 두는 것은 방어가 아니라 **검사되지 않는 죽은 코드다** - 오늘의
# 값에서 `esc`는 한 바이트도 바꾸지 않아 지워도 어떤 테스트도 빨개지지 않는다
# (실측: 변이 M13 생존). 그래서 리터럴로 두고, 두 문구가 이스케이프가 필요 없는
# 값이라는 것을 `test_배지가_붙는_칸도_원문을_이스케이프한다`가 대신 잰다.
# 이 자리를 사용자 문자열로 바꾸는 날 그 테스트가 먼저 빨개진다.
_STT_BADGE_TEXT = "원문 검수 필요"
_STT_BADGE_TITLE = "STT로 생성한 원문이다"
_STT_BADGE = f'<span class="badge-stt" title="{_STT_BADGE_TITLE}">{_STT_BADGE_TEXT}</span>'


def _summary_html(outcome: TriageOutcome) -> str:
    """요약 통계 (FR-7.4).

    **수치를 여기서 세지 않는다.** `TriageOutcome`의 프로퍼티를 읽는다 -
    `_format_triage_summary`가 같은 판단을 이미 내려 두었고, 여기서 다시 세면
    화면 요약과 `review.json`이 갈라질 자리가 생긴다.

    **`ratio`는 `policy_value`가 아니라 `review_ratio`다.** hard fail이 검수
    예산을 우회하므로(FR-6.2) "예산 10% 요청"과 "실제 10% 검수"는 다르고,
    요청값을 그리면 README 배수의 분모가 화면에서 조용히 틀린다.
    """
    # 행이 0개인 실행에서도 출처가 드러나야 한다 - `review.json`의
    # `summary.source_from_stt`와 같은 이유이고, 같은 원천에서 읽는다.
    #
    # **`outcome.segments`에서 유도하면 안 된다.** 그 집합에는 번역 실패분이
    # 빠져 있어 전량 실패 실행에서 비고, 빈 이터러블 위의 `any`는 `False`를 내
    # **HTML 어디에도 "STT"가 남지 않는다**(실측: 4건 전량 실패에서 origin 사라짐).
    # `outcome.selected`로 좁히는 것도 같은 이유로 금지다(설계 D3).
    origin = " · 원문 STT" if outcome.source_from_stt else ""
    return _SUMMARY.substitute(
        origin=origin,
        total=outcome.total_segments,
        selected=outcome.selected_for_review,
        ratio=f"{outcome.review_ratio:.1%}",
        hard_fail=outcome.hard_fail_count,
        excluded=outcome.excluded_failures,
        source_lang=esc(outcome.source_lang),
        target_lang=esc(outcome.target_lang),
        profile=esc(outcome.profile_name),
        policy=esc(outcome.policy_label),
    )


def _filters_html(outcome: TriageOutcome) -> str:
    """필터 UI (FR-7.3 · 설계 D2·D3).

    **신호 목록을 하드코딩하지 않는다.** 하드코딩하면 신호가 추가될 때
    필터에서만 빠지고 그 사실이 화면에 드러나지 않는다 - 검수자는 그 신호로
    걸린 행을 영원히 못 좁힌다(NFR-5).

    **`selected`에서 뽑는다.** 행이 `outcome.selected`로만 만들어지므로
    (`build_html`) `risks` 전체에서 뽑으면 **어떤 행도 갖지 않은 신호**가
    필터에 뜬다 - 끄든 켜든 화면이 그대로라 필터가 고장난 것처럼 보인다.

    **정렬은 재현성이다**(NFR-3). 집합을 정렬 없이 돌면 같은 입력이 실행마다
    다른 HTML을 내 diff가 무의미해진다 - `_row_html`의 `data-signals`가 같은
    이유로 이미 정렬돼 있고, 두 어휘가 같아야 JS의 비교가 성립한다.

    **`checked`를 빼면 안 된다.** JS는 로드 직후 `apply()`를 한 번 돌리므로,
    꺼진 채로 시작하면 신호를 가진 행이 **전부** 숨은 빈 표가 검수자를 맞는다.

    파이썬이 보장하는 것은 여기까지다 - 체크박스가 실제로 행을 거르는지는
    자동 게이트가 없고 live로 확인한다(설계 D3 · §10.6).
    """
    names = sorted({sig.name for risk in outcome.selected for sig in risk.signals})
    return _FILTERS.substitute(
        checkboxes="".join(_CHECKBOX.substitute(name=esc(name)) for name in names),
        total=outcome.selected_for_review,
    )


def _timecode(ms: int) -> str:
    """밀리초를 `HH:MM:SS`로. 검수자가 자막 편집기에서 그 자리를 찾는 데 쓴다.

    **세 항이 전부 필요하다.** 강연 자막은 한 시간을 넘으므로 `//3600`을 지우면
    두 번째 시간대의 자막이 전부 첫 시간대의 자리를 가리킨다 - 검수자가 그
    자리를 못 찾으면 리포트의 존재 이유가 사라진다. `% 3600`이 없으면 분이
    60을 넘어 `01:62:03` 같은 값이 나간다.
    """
    total = ms // 1000
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _highlighted(text: str, signals: list[Signal], side: str) -> str:
    """한쪽 텍스트를 구간별로 칠한다 (FR-7.3 · 설계 D7).

    **이스케이프는 분할 뒤에 조각 단위로 건다.** `html.escape`는 길이를
    보존하지 않으므로(`<` 1자 -> `&lt;` 4자) 먼저 걸면 오프셋이 전부
    어긋난다 - 그리고 **예외가 나지 않는다.** 어긋난 오프셋도 유효한
    슬라이스라 엉뚱한 구간이 조용히 칠해진다.

    자막에 태그가 들어오는 것은 가정이 아니다 - `struct.tag_lost`가 태그를
    세고 있는 것이 그 증거다.

    **`span.side` 걸러내기를 빼면 반대쪽 칸의 오프셋으로 이 칸을 칠한다.**
    길이가 우연히 맞으면 유효한 슬라이스라 역시 예외가 나지 않는다.
    """
    pairs = [(sig.name, span) for sig in signals for span in sig.spans if span.side == side]
    return "".join(
        esc(frag.text)
        if not frag.signals
        else f'<mark data-sig="{esc(" ".join(frag.signals))}">{esc(frag.text)}</mark>'
        for frag in split_spans(text, pairs)
    )


def _row_html(risk: SegmentRisk, segment: Segment) -> str:
    """세그먼트 하나. `SegmentRisk`와 `Segment`를 조인한다 - `_segment_doc`의 형제다.

    **`data-hardfail`·`data-signals` 둘은 JS가 읽는 계약이다.** 파이썬이 보장하는
    것은 이 속성이 outcome과 일치한다는 것까지고, 필터 동작 자체는 live로
    확인한다(설계 D3). **`data-stt`는 셋째지만 오늘 JS가 읽지 않는다** - 필터를
    늘릴 때 쓰라고 내는 것이고, "둘"이라고 적힌 채 셋이 되면 다음 사람이
    `_JS`에서 이 속성의 사용처를 찾다가 없는 것을 버그로 본다.

    `signals`를 정렬하는 것은 재현성(NFR-3)이다 - 정렬을 빼면 파이썬의 문자열
    해시 무작위화가 같은 입력에 다른 HTML을 내 diff가 무의미해진다.

    **`target_text`의 `or ""`를 빼면 안 된다.** `None`은 `esc(None)`을 거쳐
    문자열 `"None"`이 되는데, 그것은 예외가 아니라 화면에 그럴듯하게 찍히는
    거짓 번역문이다.
    """
    names = sorted({sig.name for sig in risk.signals})
    return _ROW.substitute(
        hardfail="1" if risk.hard_fail else "0",
        signals=esc(" ".join(names)),
        # **`data-stt`는 위 둘과 달리 오늘 `_JS`가 읽지 않는다.** 필터를 늘릴 때
        # 쓰라고 내는 자리다 - STT 입력에서는 전 행이 같은 값이라 필터로서
        # 정보가 0이고(설계 D8이 점수에 넣지 않는 것과 같은 이유), 그래서
        # `_JS`를 훑어 속성 사용처를 세는 테스트도 이것을 세지 않는다.
        stt="1" if segment.source_from_stt else "0",
        # **배지는 `id` 칸에 얹힌다.** 별도 칸으로 빼면 `_TABLE`의 `<thead>`도
        # 같이 늘어나야 하는데, 자막 경로에서는 언제나 비어 있는 칸이 된다.
        badge=_STT_BADGE if segment.source_from_stt else "",
        id=esc(segment.id),
        timecode=_timecode(segment.start_ms),
        score=f"{risk.risk_score:.2f}",
        source=_highlighted(segment.source_text, risk.signals, "source"),
        target=_highlighted(segment.target_text or "", risk.signals, "target"),
        reasons=esc(" · ".join(risk.reasons)) or "&nbsp;",
    )


def build_html(outcome: TriageOutcome) -> str:
    """트리아지 결과를 단일 파일 HTML로 만든다 (FR-7.3).

    **`review.json`과 같은 `TriageOutcome`에서 나온다.** 두 산출물의 수치가
    갈라질 자리가 구조적으로 없다 - 화면 요약도 같은 객체를 읽는다.

    **치환된 값을 다시 `substitute`에 넣지 않는다.** `Template`은 템플릿의 `$`만
    보고 값은 재스캔하지 않으므로, 이 순서에서는 자막 본문의 `$100`이 안전하다.
    조립을 한 겹 더 감싸면 그 자막이 `KeyError`나 엉뚱한 치환이 된다.

    **행은 `outcome.selected`로 만든다** - `review.json`의 `segments[]`와 같은
    집합이다(설계 D3·D4). `risks` 전체를 돌면 예산 밖으로 밀린 것까지 화면에
    올라와 "검수 대상" 칸의 수치와 행 개수가 갈린다.

    **조인은 위치가 아니라 id로 한다.** `selected`는 걸러낸 부분집합이라
    `segments`와 순서가 어긋나고, 위치로 맞추면 **다른 세그먼트의 본문이 다른
    세그먼트의 위험도와 함께** 조용히 나간다.
    """
    by_id = {seg.id: seg for seg in outcome.segments}
    rows = "".join(_row_html(risk, by_id[risk.segment_id]) for risk in outcome.selected)
    return _SHELL.substitute(
        title=f"검수 리포트 · {esc(outcome.source_lang)} -&gt; {esc(outcome.target_lang)}",
        css=_CSS,
        js=_JS,
        summary=_summary_html(outcome),
        filters=_filters_html(outcome),
        table=_TABLE.substitute(rows=rows),
    )


def write_html(outcome: TriageOutcome, path: Path) -> None:
    """`report.html`을 쓴다 (FR-7.3).

    상위 디렉터리 생성은 `write_review`와 **같은 계약**이다 - 두 산출물이 같은
    `--review-out`으로 나가므로 한쪽만 만들면 조합에 따라 실패한다.

    **`encoding="utf-8"`을 생략하면 안 된다.** 윈도우의 기본은 `cp949`라
    문서가 선언한 `charset="utf-8"`과 어긋나는데, 파일은 정상 생성되고 종료
    코드도 0이라 브라우저에서만 깨져 보인다.

    **원자성도 `write_review`와 같은 계약이다.** 두 산출물이 같은
    `--review-out`으로 나가는데 한쪽만 원자적이면, 같은 실패에서 한 파일은
    보존되고 다른 파일은 파괴된다 - 사용자가 그 차이를 알 방법이 없다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # **직렬화를 끝내고 나서 연다.** `build_html`이 던지면 파일에 손을 대기
    # 전이어야 한다.
    text = build_html(outcome)
    # **임시 파일에 쓰고 `os.replace`로 갈아 끼운다** - `write_text`는 먼저
    # truncate하며 열고 **그 다음에** 인코딩하므로, 서로게이트(`\ud800`)가 섞인
    # 자막이 오면 재실행에서 지난 실행의 정상 리포트가 **0바이트로 파괴된다**
    # (`json_report.py`의 실측 근거가 그것이다). 자막 본문이 이 문자열에
    # 들어오는 것은 Task 6부터다 - Task 5까지는 언어 코드·규격 이름·정책
    # 라벨뿐이라 도달 경로가 없었고, 게이트를 세울 수 없는 코드를 미리 넣지
    # 않았다.
    #
    # **같은 디렉터리에 둬야 한다.** `os.replace`는 같은 파일시스템 안에서만
    # 원자적이고 `tempfile.gettempdir()`은 다른 볼륨일 수 있다.
    # **PID를 이름에 넣는 것은 동시 실행 때문이다** - 고정 이름이면 두
    # 프로세스가 같은 임시 파일을 밟는다.
    #
    # **`newline="\n"`이 없으면 줄바꿈이 플랫폼마다 갈린다.** 텍스트 모드의
    # 기본값은 `\n`을 `os.linesep`으로 번역하므로 Windows에서는 CRLF가, Linux
    # CI에서는 LF가 나간다 - 같은 입력이 다른 바이트를 낸다(NFR-3).
    #
    # **`finally`의 `unlink`를 `contextlib.suppress(OSError)`로 감싼다.** 감싸지
    # 않으면 정리 실패가 진행 중이던 예외를 **대체**해, 호출자가
    # `UnicodeEncodeError` 대신 `PermissionError`를 보고 다른 종료 코드로
    # 분류한다.
    tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
