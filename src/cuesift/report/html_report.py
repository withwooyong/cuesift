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

import html
from pathlib import Path
from string import Template

from cuesift.report.models import TriageOutcome

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
"""

# Task 7의 필터가 채운다. 빈 문자열이어도 `<script></script>`는 그대로 나가는데,
# 그것이 정상이다 - 태그를 조건부로 없애면 Task 7이 셸까지 고쳐야 한다.
_JS = ""

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
<p class="meta">$source_lang -&gt; $target_lang · 규격 $profile · 정책 $policy</p>
</section>"""
)


def esc(value: object) -> str:
    """HTML 이스케이프. 속성에도 들어가므로 따옴표까지 변환한다.

    **`quote=True`가 기본이지만 명시한다** - 이 함수가 속성값에도 쓰이므로
    누군가 `quote=False`로 바꾸면 원문의 따옴표가 속성을 탈출한다.
    """
    return html.escape(str(value), quote=True)


def _summary_html(outcome: TriageOutcome) -> str:
    """요약 통계 (FR-7.4).

    **수치를 여기서 세지 않는다.** `TriageOutcome`의 프로퍼티를 읽는다 -
    `_format_triage_summary`가 같은 판단을 이미 내려 두었고, 여기서 다시 세면
    화면 요약과 `review.json`이 갈라질 자리가 생긴다.

    **`ratio`는 `policy_value`가 아니라 `review_ratio`다.** hard fail이 검수
    예산을 우회하므로(FR-6.2) "예산 10% 요청"과 "실제 10% 검수"는 다르고,
    요청값을 그리면 README 배수의 분모가 화면에서 조용히 틀린다.
    """
    return _SUMMARY.substitute(
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


def build_html(outcome: TriageOutcome) -> str:
    """트리아지 결과를 단일 파일 HTML로 만든다 (FR-7.3).

    **`review.json`과 같은 `TriageOutcome`에서 나온다.** 두 산출물의 수치가
    갈라질 자리가 구조적으로 없다 - 화면 요약도 같은 객체를 읽는다.

    **치환된 값을 다시 `substitute`에 넣지 않는다.** `Template`은 템플릿의 `$`만
    보고 값은 재스캔하지 않으므로, 이 순서에서는 자막 본문의 `$100`이 안전하다.
    조립을 한 겹 더 감싸면 그 자막이 `KeyError`나 엉뚱한 치환이 된다.
    """
    return _SHELL.substitute(
        title=f"검수 리포트 · {esc(outcome.source_lang)} -&gt; {esc(outcome.target_lang)}",
        css=_CSS,
        js=_JS,
        summary=_summary_html(outcome),
        filters="",
        table="",
    )


def write_html(outcome: TriageOutcome, path: Path) -> None:
    """`report.html`을 쓴다 (FR-7.3).

    상위 디렉터리 생성은 `write_review`와 **같은 계약**이다 - 두 산출물이 같은
    `--review-out`으로 나가므로 한쪽만 만들면 조합에 따라 실패한다.

    **`encoding="utf-8"`을 생략하면 안 된다.** 윈도우의 기본은 `cp949`라
    문서가 선언한 `charset="utf-8"`과 어긋나는데, 파일은 정상 생성되고 종료
    코드도 0이라 브라우저에서만 깨져 보인다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(outcome), encoding="utf-8")
