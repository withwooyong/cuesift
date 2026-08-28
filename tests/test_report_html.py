"""`report.html` 렌더러 (FR-7.3 · 설계 §7.2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cuesift.report import build_html, write_html
from cuesift.report.models import TriageOutcome
from cuesift.segment import Segment, SegmentRisk

# `Signal`·`Span`은 Task 6에서 쓴다. 지금 import하면 ruff의 F401(미사용)에
# 걸려 `ruff check .`가 실패한다 - 그때 함께 더한다.


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
