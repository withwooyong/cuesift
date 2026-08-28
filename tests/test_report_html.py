"""`report.html` 렌더러 (FR-7.3 · 설계 §7.2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cuesift.report import build_html, write_html
from cuesift.report.models import TriageOutcome
from cuesift.segment import Segment, SegmentRisk, Signal, Span


def _outcome(
    risks: list[SegmentRisk] | None = None,
    segments: list[Segment] | None = None,
    **kwargs: object,
) -> TriageOutcome:
    """테스트용 TriageOutcome. 기본은 선별 0건이다."""
    defaults: dict[str, object] = {
        "source_lang": "ko",
        "target_lang": "en",
        "profile_name": "netflix-en",
        "policy_label": "예산 10%",
        "policy_kind": "budget",
        "policy_value": 0.1,
        "risks": tuple(risks or ()),
        "segments": tuple(segments or ()),
        "excluded_failures": 0,
        "usage": None,
    }
    defaults.update(kwargs)
    return TriageOutcome(**defaults)  # type: ignore[arg-type]


def _pair(n: int, *, hard_fail: int = 0, selected: int = 0) -> tuple[list, list]:
    """세그먼트 `n`개와 짝이 맞는 위험 `n`개.

    `TriageOutcome.__post_init__`이 두 목록의 **길이와 id 집합**을 모두 보므로
    한쪽만 만들면 생성 시점에 `ValueError`가 난다.
    """
    segments = [
        Segment(
            id=f"s{i}",
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text="가",
            target_text="a",
        )
        for i in range(n)
    ]
    risks = [
        SegmentRisk(
            segment_id=f"s{i}",
            signals=[],
            risk_score=0.5,
            hard_fail=i < hard_fail,
            selected=i < selected,
        )
        for i in range(n)
    ]
    return segments, risks


def _rich_outcome() -> TriageOutcome:
    """요약 다섯 칸의 값이 **전부 다른** 리포트.

    같은 값이 둘이면 두 칸을 맞바꾼 변이가 통과한다 - 어느 칸이 어느 프로퍼티를
    읽는지 재지 못하는 것이다. 실제 값은
    총 70(=41+29) · 검수 13 · 비율 31.7%(13/41) · hard fail 7 · 번역 실패 29다.
    """
    segments, risks = _pair(41, hard_fail=7, selected=13)
    return _outcome(risks=risks, segments=segments, excluded_failures=29)


def test_요약이_총_세그먼트_수를_담는다() -> None:
    """**흔하지 않은 수를 쓴다.** `assert "1" in html`은 아무것도 재지 않는다 -
    "1"은 타임코드에도 CSS의 `1.5rem`에도 있어 어떤 문서든 통과한다.
    """
    segments, risks = _pair(37)
    html = build_html(_outcome(risks=risks, segments=segments))

    assert ">37<" in html


def test_총_세그먼트_수가_번역_실패분을_포함한다() -> None:
    """`total = triaged + excluded`다 (`models.py`의 `total_segments`).

    `len(risks)`를 그대로 읽는 변이는 `excluded_failures=0`인 입력에서 구별되지
    않는다 - 위 테스트만으로는 생존한다.
    """
    assert ">70<" in build_html(_rich_outcome())


def test_요약이_검수_대상_수를_담는다() -> None:
    """`selected_for_review`를 `triaged_segments`로 바꾸는 변이의 게이트."""
    assert ">13<" in build_html(_rich_outcome())


def test_요약이_실제_검수_비율을_담는다() -> None:
    """**요청 예산(10%)이 아니라 실제 비율(31.7%)이다** (CLAUDE.md · 설계 §6.2).

    hard fail이 예산을 우회하므로 둘은 다르다. 이 칸을 상수나 `policy_value`로
    바꾸는 변이는 다른 어떤 단언에도 걸리지 않는다 - README 배수의 분모가
    화면에서 조용히 틀린다.
    """
    html = build_html(_rich_outcome())

    assert ">31.7%<" in html
    assert ">10.0%<" not in html


def test_요약이_hard_fail_건수를_담는다() -> None:
    """예산을 우회한 건수 (FR-6.2). 검수자가 "왜 예산보다 많은가"를 읽는 칸이다."""
    assert ">7<" in build_html(_rich_outcome())


def test_요약이_번역_실패_건수를_담는다() -> None:
    """트리아지에서 **빠진** 것이라 위 넷 어디에도 안 나온다. 이 칸이 비면
    검수자는 원본에 없던 세그먼트가 사라진 것을 모른다.
    """
    assert ">29<" in build_html(_rich_outcome())


def test_요약이_언어쌍과_규격과_정책을_한_줄로_담는다() -> None:
    """재현성 필드 - 파일만 보고 무엇을 어느 규격으로 걸렀는지 알아야 한다.

    **`"ko" in html`로는 재지 못한다.** 셸의 `<html lang="ko">`가 이미 참으로
    만들어 `source_lang`을 통째로 지워도 통과한다.

    **`"ko -&gt; en"`으로도 부족하다.** `<title>`이 같은 문자열을 갖고 있어
    meta 줄을 통째로 지워도 통과한다 - 실측으로 대상 언어를 뺀 변이가
    **생존했다.** 줄 전체로 단언해야 게이트가 된다.
    """
    html = build_html(_outcome())

    assert "ko -&gt; en · 규격 netflix-en · 정책 예산 10%" in html


def test_문서_제목이_언어쌍을_담는다() -> None:
    """브라우저 탭과 북마크에 뜨는 이름이다. 여러 언어쌍의 리포트를 나란히
    열면 이것만이 구별 수단이라 `report.html`이라는 파일명으로는 못 가른다.
    """
    assert "<title>검수 리포트 · ko -&gt; en</title>" in build_html(_outcome())


def test_요약이_정책_라벨을_사용자가_친_그대로_담는다() -> None:
    """`policy_label`이지 `policy_value`가 아니다 (`models.py`의 독스트링).

    kind/value에서 재생성하면 `"예산 10%"`가 `"budget 0.1"`로 바뀐다.
    """
    assert "예산 10%" in build_html(_outcome())


def test_선별이_0건이어도_문서가_나온다() -> None:
    """빈 리포트와 실행 실패는 다르다 (설계 D10).

    파일이 없으면 소비자가 "실행이 안 됐다"와 "걸린 것이 없다"를 구분하지 못한다.
    """
    html = build_html(_outcome())

    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html


def test_문서에_charset이_있다() -> None:
    """한국어가 깨지지 않으려면 필수다. 파일로 열리는 문서라 서버 헤더가 없다."""
    assert 'charset="utf-8"' in build_html(_outcome())


@pytest.mark.parametrize("field", ["source_lang", "target_lang", "profile_name", "policy_label"])
def test_요약에_실리는_필드가_모두_이스케이프된다(field: str) -> None:
    """`esc`를 거치지 않는 변이의 게이트.

    **네 자리를 각각 재야 한다.** 한 자리만 걸면 다른 자리의 `esc`를 지운 변이가
    생존한다 - 실측으로 `policy_label`이 그랬다. 이 값들은 전부 CLI 인자와
    `specs/*.yaml`에서 오므로 렌더러가 마지막 방어선이다.

    **`"<script>" in html`로는 재지 못한다** - 셸이 진짜 `<script>` 태그를
    갖고 있어 언제나 참이다. 주입한 문자열 전체로 단언한다.
    """
    html = build_html(_outcome(**{field: "<script>alert(1)</script>"}))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_따옴표가_이스케이프되어_속성을_탈출하지_못한다() -> None:
    """`esc`의 `quote=True` 게이트.

    지금 요약은 텍스트 자리에만 들어가지만 Task 7의 필터가 값을 속성에 싣는다.
    `quote=False`로 바꾸는 변이는 **그때까지 조용하다** - 여기서 고정한다.
    """
    html = build_html(_outcome(profile_name='netflix" onload="x'))

    assert 'onload="x' not in html
    assert "&quot;" in html


def test_값에_든_달러_기호가_치환으로_해석되지_않는다() -> None:
    """`string.Template`은 **템플릿의** `$`만 본다 - 치환된 값은 재스캔하지 않는다.

    조립 순서를 바꿔 결과를 한 번 더 `substitute`에 넣으면 이 단언이 깨진다.
    Task 6에서 세그먼트 본문이 들어가면 `$100` 같은 자막이 실제로 도달한다.
    """
    html = build_html(_outcome(policy_label="예산 $total 원"))

    assert "예산 $total 원" in html


def test_출력에_미치환_placeholder가_남지_않는다() -> None:
    """`substitute`를 `safe_substitute`로 바꾸는 변이의 게이트 (설계 D5).

    `substitute`는 키가 빠지면 `KeyError`로 즉사하지만 `safe_substitute`는
    `$table`을 그대로 출력에 남긴다 - 아무도 모른다.

    **정상 경로만으로는 두 메서드가 구별되지 않는다**(실측: 그 변이는 생존한다).
    이 단언이 잡는 것은 메서드 자체가 아니라 **그 위험이 현실화된 상태**다 -
    Task 6·7이 placeholder를 늘리면서 키를 빠뜨리면 여기서 걸린다.
    """
    assert re.search(r"\$\w+", build_html(_rich_outcome())) is None


def test_write_html이_파일을_쓴다(tmp_path: Path) -> None:
    path = tmp_path / "out.report.html"
    write_html(_outcome(), path)

    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_write_html은_상위_디렉터리를_만든다(tmp_path: Path) -> None:
    """`--review-out`이 없는 디렉터리를 가리킬 수 있다.

    `write_review`가 `path.parent.mkdir(parents=True, exist_ok=True)`를 한다
    (실측). 두 산출물이 같은 `--review-out`으로 나가므로 한쪽만 만들면 조합에
    따라 실패한다.
    """
    path = tmp_path / "없던디렉터리" / "out.report.html"
    write_html(_outcome(), path)

    assert path.exists()


def test_write_html이_한국어를_utf8로_쓴다(tmp_path: Path) -> None:
    """인코딩을 생략하면 윈도우에서 `cp949`가 기본이라 문서의 `charset="utf-8"`과
    어긋난다 - 파일은 정상 생성되고 종료 코드도 0이라 브라우저에서만 깨져 보인다.
    """
    path = tmp_path / "out.report.html"
    write_html(_outcome(policy_label="예산 10% · 한국어"), path)

    assert "한국어" in path.read_text(encoding="utf-8")


def _risk_with_span(segment_id: str, name: str, start: int, end: int, side: str) -> SegmentRisk:
    """구간 하나를 가진 hard fail 위험. 하이라이트 테스트의 공용 픽스처다."""
    return SegmentRisk(
        segment_id=segment_id,
        signals=[
            Signal(
                name=name,
                tier=0,
                score=1.0,
                hard_fail=True,
                spans=(Span(start=start, end=end, side=side),),
            )
        ],
        risk_score=1.0,
        hard_fail=True,
        selected=True,
        reasons=[name],
    )


def _marked(html_out: str) -> str:
    """첫 `<mark>`가 감싼 텍스트. 속성이 붙어 있어도 본문만 꺼낸다."""
    return html_out.split("<mark", 1)[1].split(">", 1)[1].split("</mark>", 1)[0]


def test_태그가_있는_원문에서_하이라이트가_어긋나지_않는다() -> None:
    """**이 계획의 최우선 게이트다** (설계 D7 · §10.1).

    `html.escape`는 길이를 보존하지 않는다 - `<`(1자)가 `&lt;`(4자)가 된다.
    이스케이프를 분할보다 먼저 하면 오프셋이 전부 밀리는데 **예외는 나지
    않는다.** 엉뚱한 구간이 조용히 칠해진다.

    자막에 태그가 들어오는 것은 가정이 아니라 사실이다 - `struct.tag_lost`가
    태그를 세고 있다.
    """
    source = "He said <i>2024</i> loudly"
    # **이 단언이 픽스처의 게이트다.** 계획서 초안은 `[12, 16)`을 적었는데
    # `"He said "`가 8자라 실제 자리는 `[11, 15)`였다 - 이 줄이 없었다면 아래
    # 실패가 "오프셋을 잘못 적었다"가 아니라 "D7을 어겼다"로 읽혔다.
    assert source[11:15] == "2024"

    seg = Segment(
        id="s1",
        index=0,
        start_ms=0,
        end_ms=1000,
        source_text=source,
        target_text="크게 말했다",
    )
    risk = _risk_with_span("s1", "struct.number_missing", 11, 15, "source")
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "<mark" in html_out
    # 칠해진 것이 2024여야 한다. 이스케이프를 먼저 걸었다면 "&gt;20" 근처가 칠해진다.
    assert _marked(html_out) == "2024"


def test_원문의_태그가_이스케이프되어_실행되지_않는다() -> None:
    """자막 원문이 그대로 마크업이 되면 안 된다."""
    seg = Segment(
        id="s1",
        index=0,
        start_ms=0,
        end_ms=1000,
        source_text="<script>alert(1)</script>",
        target_text="a",
    )
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_번역문의_태그도_이스케이프된다() -> None:
    """원문만 이스케이프하는 변이가 **원문 테스트만으로는 생존한다.**

    `_row_html`은 두 칸을 각각 부르므로 한쪽만 걸어도 다른 쪽은 그대로 나간다.
    번역문은 LLM 출력이라 원문보다 예측이 어렵다.
    """
    seg = Segment(
        id="s1",
        index=0,
        start_ms=0,
        end_ms=1000,
        source_text="가",
        target_text="<img src=x onerror=alert(1)>",
    )
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "<img src=x" not in html_out
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_out


def test_구간이_없는_신호는_하이라이트를_만들지_않는다() -> None:
    """신호 10종 중 7종은 구간 개념이 성립하지 않는다 (스펙 §3.2).

    빈 것과 아직 안 만든 것을 구분할 필요가 없다 - 배지로만 보여준다.
    """
    seg = Segment(
        id="s1", index=0, start_ms=0, end_ms=1000, source_text="가나다", target_text="abc"
    )
    risk = SegmentRisk(
        segment_id="s1",
        signals=[Signal(name="length.ratio", tier=0, score=0.8)],
        risk_score=0.8,
        hard_fail=False,
        selected=True,
    )
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "<mark" not in html_out
    assert "length.ratio" in html_out


def test_side가_source면_원문_칸만_칠한다() -> None:
    """`Span.side`가 어느 칸을 칠할지 가르는 유일한 판별자다."""
    seg = Segment(
        id="s1", index=0, start_ms=0, end_ms=1000, source_text="2024년", target_text="the year"
    )
    risk = _risk_with_span("s1", "struct.number_missing", 0, 4, "source")
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert html_out.count("<mark") == 1
    # **칸까지 봐야 한다.** 개수만 세면 두 칸을 맞바꾼 변이가 통과한다 -
    # 양쪽 텍스트가 각각 유효 오프셋을 가지면 개수가 그대로 1이다.
    assert '<td class="src"><mark' in html_out


def test_side가_target이면_번역문_칸을_칠한다() -> None:
    seg = Segment(
        id="s1",
        index=0,
        start_ms=0,
        end_ms=1000,
        source_text="중요",
        target_text="<b>important</b>",
    )
    risk = _risk_with_span("s1", "struct.tag_lost", 0, 3, "target")
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert html_out.count("<mark") == 1
    assert '<td class="tgt"><mark' in html_out
    # 칠해진 것이 `<b>`여야 한다 - 이스케이프 순서가 번역문 칸에서도 같다.
    assert _marked(html_out) == "&lt;b&gt;"


def test_선별되지_않은_세그먼트는_행이_없다() -> None:
    """선별된 것만 담는다 (설계 D4 · review.json의 D3와 같은 집합)."""
    segs = [
        Segment(id=f"s{i}", index=i, start_ms=0, end_ms=1000, source_text="가", target_text="a")
        for i in range(3)
    ]
    risks = [
        SegmentRisk(
            segment_id=f"s{i}", signals=[], risk_score=0.5, hard_fail=False, selected=i == 0
        )
        for i in range(3)
    ]
    html_out = build_html(_outcome(risks=risks, segments=segs))

    assert html_out.count('<tr class="seg"') == 1


def test_행이_짝이_맞는_세그먼트와_조인된다() -> None:
    """**위치가 아니라 id로 조인한다.**

    선별된 것이 첫 번째면 위치 조인과 id 조인의 결과가 같아 구별되지 않는다 -
    앞의 테스트가 정확히 그 배치다. 선별을 **마지막**에 두면 갈린다.
    """
    texts = ["원숭이", "너구리", "다람쥐"]
    segs = [
        Segment(id=f"s{i}", index=i, start_ms=0, end_ms=1000, source_text=t, target_text="a")
        for i, t in enumerate(texts)
    ]
    risks = [
        SegmentRisk(
            segment_id=f"s{i}", signals=[], risk_score=0.5, hard_fail=False, selected=i == 2
        )
        for i in range(3)
    ]
    html_out = build_html(_outcome(risks=risks, segments=segs))

    assert "다람쥐" in html_out
    assert "원숭이" not in html_out
    assert '<td class="id">s2</td>' in html_out


def test_행이_타임코드를_읽을_수_있게_담는다() -> None:
    """검수자가 자막 편집기에서 그 자리를 찾아야 한다."""
    seg = Segment(
        id="s1", index=0, start_ms=192000, end_ms=195000, source_text="가", target_text="a"
    )
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "00:03:12" in html_out


def test_타임코드가_한_시간을_넘겨도_맞는다() -> None:
    """**192초짜리 하나로는 시(時) 항이 재어지지 않는다.**

    `//3600`을 지운 변이도, `% 3600`을 지운 변이도 3분 12초에서는 같은 값을
    낸다. 강연 자막은 한 시간을 넘고, 그때 타임코드가 틀리면 검수자가 그
    자리를 못 찾는다 - 리포트의 존재 이유가 사라진다.
    """
    seg = Segment(
        id="s1", index=0, start_ms=3_723_000, end_ms=3_724_000, source_text="가", target_text="a"
    )
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert '<td class="tc">01:02:03</td>' in html_out


def test_행이_위험도를_소수점_둘째_자리로_담는다() -> None:
    """점수가 없으면 검수자가 큐의 순서를 신뢰할 근거를 잃는다.

    0.5·1.0 같은 값은 요약의 다른 수치와 겹쳐 우연히 통과한다 - 겹치지 않는
    값을 쓴다(Task 5 ②가 배운 것과 같다).
    """
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="가", target_text="a")
    risk = SegmentRisk(
        segment_id="s1", signals=[], risk_score=0.4237, hard_fail=False, selected=True
    )
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert '<td class="score">0.42</td>' in html_out


def test_행이_선별_사유를_담는다() -> None:
    """FR-6.4. **사유 없이는 검수자가 무엇을 볼지 모른다.**

    `data-signals`와 다른 집합이다 - 사유는 0점 신호를 담지 않는다
    (`fuse.py`: "0점 신호를 사유에 넣으면 리포트가 거짓말한다").
    """
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="가", target_text="a")
    risk = SegmentRisk(
        segment_id="s1",
        signals=[],
        risk_score=0.5,
        hard_fail=False,
        selected=True,
        reasons=["용어집 위반", "숫자 누락"],
    )
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert '<td class="why">용어집 위반 · 숫자 누락</td>' in html_out


def test_행의_신호_이름은_정렬해_담는다() -> None:
    """NFR-3(재현성). 같은 입력이 다른 HTML을 내면 diff가 무의미해진다.

    **이름이 넷이어야 한다.** 파이썬이 문자열 해시를 프로세스마다
    무작위화하므로 `sorted` 제거 변이가 집합 순회 순서만으로 통과할 수 있다 -
    Task 4의 실측에서 둘이면 20회 중 2회 생존, 넷이면 0회였다.
    """
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="가", target_text="a")
    names = ["struct.tag_lost", "glossary.miss", "length.ratio", "banned.term"]
    risk = SegmentRisk(
        segment_id="s1",
        signals=[Signal(name=n, tier=0, score=0.5) for n in names],
        risk_score=0.5,
        hard_fail=False,
        selected=True,
    )
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert 'data-signals="banned.term glossary.miss length.ratio struct.tag_lost"' in html_out


@pytest.mark.parametrize(("hard_fail", "expected"), [(True, "1"), (False, "0")])
def test_행이_hard_fail_여부를_속성으로_담는다(hard_fail: bool, expected: str) -> None:
    """**JS가 읽는 계약이다** (설계 D3).

    파이썬이 보장하는 것은 이 속성이 outcome과 일치한다는 것까지고, 필터
    동작 자체는 Task 9에서 live로 확인한다. 여기서 재지 않으면 속성을
    상수로 만든 변이가 **양쪽 모두** 생존한다.

    **`data-hardfail="1"`만으로 단언하면 안 된다.** CSS의
    `tr.seg[data-hardfail="1"] td.score`가 그 문자열을 문서에 **항상** 넣어
    두므로 행을 통째로 지워도 참이 된다 - 실측으로 변이가 생존했다. 태그
    문맥까지 묶어야 행을 재는 것이 된다(Task 5 ②의 `padding: 1.5rem`과 같다).
    """
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="가", target_text="a")
    risk = SegmentRisk(
        segment_id="s1", signals=[], risk_score=0.5, hard_fail=hard_fail, selected=True
    )
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert f'<tr class="seg" data-hardfail="{expected}"' in html_out


def test_번역문이_없는_세그먼트도_행이_나온다() -> None:
    """`target_text`는 `None`이 될 수 있다 (`segment/models.py`).

    `or ""`를 빼면 `esc(None)`이 문자열 **"None"** 을 내는데, 그것은 예외가
    아니라 화면에 그럴듯하게 찍히는 거짓 번역문이다.
    """
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="가", target_text=None)
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert '<td class="tgt"></td>' in html_out
    assert "None" not in html_out


def test_세그먼트_id가_이스케이프된다() -> None:
    """id는 자막 파일에서 온다 - 우리가 만든 값이 아니다."""
    seg = Segment(id='s"1<x>', index=0, start_ms=0, end_ms=1000, source_text="가", target_text="a")
    risk = SegmentRisk(
        segment_id='s"1<x>', signals=[], risk_score=1.0, hard_fail=True, selected=True
    )
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "<x>" not in html_out
    assert "s&quot;1&lt;x&gt;" in html_out


def test_자막_본문의_달러_기호가_치환으로_해석되지_않는다() -> None:
    """`$table`이 값이 아니라 **템플릿**에 있을 때만 치환된다.

    행 문자열을 다시 `substitute`에 넣으면 그 순간 이 성질이 깨진다 -
    자막의 `$100`이 `KeyError`나 엉뚱한 치환이 된다.
    """
    seg = Segment(
        id="s1",
        index=0,
        start_ms=0,
        end_ms=1000,
        source_text="$table $summary $100",
        target_text="a",
    )
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    html_out = build_html(_outcome(risks=[risk], segments=[seg]))

    assert "$table $summary $100" in html_out


def test_write_html이_실패해도_기존_리포트를_보존한다(tmp_path: Path) -> None:
    """**Task 5가 Task 6으로 넘긴 미결이다.**

    `write_text`는 먼저 truncate하며 열고 **그 다음에** 인코딩한다 - 그 순서가
    방어선의 바깥이다. 서로게이트가 섞인 자막이 오면 재실행에서 지난 실행의
    정상 리포트가 **0바이트로 파괴된다**(`write_review`의 실측 근거와 같다).

    Task 5에서 맞추지 않은 이유는 도달 경로가 없었기 때문이다 - 요약에 실리는
    넷은 언어 코드·규격 이름·정책 라벨이라 자막 본문이 아니었다. **세그먼트
    본문이 들어오는 지금 그 경로가 생겼다.**
    """
    path = tmp_path / "report.html"
    seg_ok = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="정상", target_text="a")
    risk_ok = SegmentRisk(
        segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True
    )
    write_html(_outcome(risks=[risk_ok], segments=[seg_ok]), path)
    before = path.read_bytes()
    assert before

    seg_bad = Segment(
        id="s1", index=0, start_ms=0, end_ms=1000, source_text="깨진 \ud800 자막", target_text="a"
    )
    with pytest.raises(UnicodeEncodeError):
        write_html(_outcome(risks=[risk_ok], segments=[seg_bad]), path)

    assert path.read_bytes() == before


def test_write_html이_실패해도_임시_파일을_남기지_않는다(tmp_path: Path) -> None:
    """남은 `.tmp`는 다음 실행에서 사람이 산출물로 오인한다."""
    path = tmp_path / "report.html"
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="\ud800", target_text="a")
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)

    with pytest.raises(UnicodeEncodeError):
        write_html(_outcome(risks=[risk], segments=[seg]), path)

    assert list(tmp_path.iterdir()) == []


def test_write_html이_줄바꿈을_LF로_쓴다(tmp_path: Path) -> None:
    """NFR-3(재현성). 텍스트 모드의 기본값은 `\\n`을 `os.linesep`으로 번역하므로
    Windows는 CRLF를, Linux CI는 LF를 낸다 - **같은 입력이 다른 바이트를 낸다.**
    `write_review`가 같은 이유로 `newline="\\n"`을 건다.
    """
    path = tmp_path / "report.html"
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="가", target_text="a")
    risk = SegmentRisk(segment_id="s1", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    write_html(_outcome(risks=[risk], segments=[seg]), path)

    assert b"\r\n" not in path.read_bytes()
