"""번역된 자막 쓰기 검증 (FR-7.1 · 설계 §5.2).

**라운드트립이 이 파일의 주제다.** 읽고 → 갈아끼우고 → 쓰고 → 다시 읽어
대조한다. 한 방향만 보면 두 방향이 어긋나도 드러나지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuesift.ingest import load_subtitle, write_subtitle

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


def _translated(result: object, prefix: str = "EN:") -> list:
    """모든 세그먼트에 번역문을 채운 사본을 만든다."""
    for segment in result.segments:
        segment.target_text = f"{prefix}{segment.source_text}"
    return result.segments


def test_번역문이_실제로_쓰인다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")
    out = tmp_path / "out.srt"

    write_subtitle(result, _translated(result), out)

    reread = load_subtitle(out)
    assert [s.source_text for s in reread.segments] == [
        f"EN:{s.source_text}" for s in load_subtitle(_FIXTURES / "minimal.srt").segments
    ]


def test_타임코드가_보존된다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")
    before = [(s.start_ms, s.end_ms) for s in result.segments]
    out = tmp_path / "out.srt"

    write_subtitle(result, _translated(result), out)

    assert [(s.start_ms, s.end_ms) for s in load_subtitle(out).segments] == before


def test_여러_줄이_보존된다(tmp_path: Path) -> None:
    # VTT/SRT writer는 텍스트에 섞인 원시 \n도 그대로 처리한다 - `plaintext`
    # setter가 \n을 SSA의 \N으로 바꾸든 안 바꾸든 이 픽스처에서는 결과가
    # 같다(판별력 0) [실측 2026-08-17]. 그래서 이 테스트는 "여러 줄 왕복이
    # 되는가"만 재고, \N 변환이 실제로 필요한지는 아래 ASS 테스트가 잰다.
    result = load_subtitle(_FIXTURES / "multiline.vtt")
    for segment in result.segments:
        segment.target_text = "첫\n둘\n셋"
    out = tmp_path / "out.vtt"

    write_subtitle(result, result.segments, out)

    assert load_subtitle(out).segments[0].source_text == "첫\n둘\n셋"


def test_ssa_다중행이_N으로_보존된다(tmp_path: Path) -> None:
    # ASS/SSA는 한 Dialogue가 파일에서 한 줄이다. `plaintext` setter를 쓰지
    # 않고 원시 \n을 그대로 event.text에 넣으면(예: event.text = target_text로
    # 바뀌는 회귀) 그 줄에서 파일이 물리적으로 잘리고, `\n` 뒤의 텍스트는
    # Dialogue: 로 시작하지 않는 고아 줄이 되어 재읽기에서 통째로 사라진다
    # [실측 2026-08-17: "첫\n둘" -> 재읽기 "첫"]. `plaintext` setter가 \n을
    # SSA의 \N으로 바꿔야만 살아남는다.
    result = load_subtitle(_FIXTURES / "basic.ssa")
    for segment in result.segments:
        segment.target_text = "첫\n둘"
    out = tmp_path / "out.ssa"

    write_subtitle(result, result.segments, out)

    assert load_subtitle(out).segments[0].source_text == "첫\n둘"


def test_선행_태그_블록이_되붙는다(tmp_path: Path) -> None:
    # {\an8}은 화면 위쪽 자막이라는 뜻이다. 잃으면 자막이 아래로 내려온다.
    # pysubs2의 plaintext setter는 태그를 전부 지우므로 보정이 필요하다 [실측].
    import pysubs2

    result = load_subtitle(_FIXTURES / "tags.ass")
    out = tmp_path / "out.ass"

    write_subtitle(result, _translated(result), out)

    written = pysubs2.load(out, encoding="utf-8")
    dialogues = [e for e in written.events if e.type == "Dialogue"]
    assert dialogues[0].text.startswith("{\\an8}")


def test_주석_이벤트는_건드리지_않는다(tmp_path: Path) -> None:
    # `_keep_displayed`가 걸러낸 이벤트다. 위치로 짝지으면 전부 밀린다.
    import pysubs2

    result = load_subtitle(_FIXTURES / "tags.ass")
    out = tmp_path / "out.ass"

    write_subtitle(result, _translated(result), out)

    written = pysubs2.load(out, encoding="utf-8")
    comments = [e for e in written.events if e.type == "Comment"]
    assert comments and all("EN:" not in e.text for e in comments)


def test_실패_세그먼트는_원문을_남긴다(tmp_path: Path) -> None:
    # 빈 문자열로 두면 화면에서 사라져 발견이 더 어렵다 (설계 §5.3).
    result = load_subtitle(_FIXTURES / "minimal.srt")
    original = result.segments[0].source_text
    for segment in result.segments[1:]:
        segment.target_text = f"EN:{segment.source_text}"
    out = tmp_path / "out.srt"

    write_subtitle(result, result.segments, out)

    assert load_subtitle(out).segments[0].source_text == original


def test_원본_결과를_변형하지_않는다(tmp_path: Path) -> None:
    # deepcopy가 없으면 --to en,ja에서 두 번째 언어가 첫 번째 위에 덮인다.
    result = load_subtitle(_FIXTURES / "minimal.srt")
    before = [e.text for e in result.subs.events]

    write_subtitle(result, _translated(result), tmp_path / "en.srt")

    assert [e.text for e in result.subs.events] == before


def test_두_언어를_연달아_써도_섞이지_않는다(tmp_path: Path) -> None:
    # **부분 실패 시나리오여야 deepcopy 누락을 잡는다.** 두 언어 모두 전량
    # 성공하면 매 write_subtitle 호출이 전 이벤트를 완전히 덮어쓰고 나서
    # 즉시 저장하므로, subs를 공유해도(=deepcopy 없어도) 저장 시점에는 이전
    # 언어의 흔적이 안 남는다(실측). 진짜 위험은 2차 번역에서 **일부** 세그먼트가
    # 실패(target_text=None)해서 그 자리를 아무도 덮어쓰지 않는 경우다 -
    # deepcopy가 없으면 그 자리에 1차 번역의 텍스트가 잔류한다 [실측 2026-08-17].
    result = load_subtitle(_FIXTURES / "minimal.srt")
    originals = [s.source_text for s in result.segments]

    write_subtitle(result, _translated(result, "EN:"), tmp_path / "a.srt")
    assert all(s.source_text.startswith("EN:") for s in load_subtitle(tmp_path / "a.srt").segments)

    # 2차 번역(JA)에서 0번 세그먼트만 실패했다고 가정한다.
    result.segments[0].target_text = None
    result.segments[1].target_text = f"JA:{originals[1]}"
    write_subtitle(result, result.segments, tmp_path / "b.srt")

    b_texts = [s.source_text for s in load_subtitle(tmp_path / "b.srt").segments]
    assert b_texts[0] == originals[0]  # EN: 잔여가 남으면 안 된다 - 원문이어야 한다
    assert b_texts[1] == f"JA:{originals[1]}"


def test_없는_디렉터리를_만든다(tmp_path: Path) -> None:
    result = load_subtitle(_FIXTURES / "minimal.srt")
    out = tmp_path / "없는" / "깊은" / "out.srt"

    write_subtitle(result, _translated(result), out)

    assert out.exists()


def test_확장자_없는_경로에도_원본_포맷으로_쓴다(tmp_path: Path) -> None:
    # `save(format_=...)`를 생략하면 pysubs2가 경로의 확장자로 포맷을
    # 판별하는데, 확장자가 없으면 `UnknownFileExtensionError`를 낸다 [실측
    # 2026-08-17]. `result.format`을 명시하는 것이 FR-7.1의 "입력과 동일
    # 포맷 기본"의 실제 계약이다.
    result = load_subtitle(_FIXTURES / "minimal.srt")
    out = tmp_path / "out_확장자_없음"

    write_subtitle(result, _translated(result), out)

    assert out.exists()


@pytest.mark.parametrize("fixture", ["minimal.srt", "multiline.vtt", "basic.ssa", "crlf_bom.srt"])
def test_픽스처_라운드트립(fixture: str, tmp_path: Path) -> None:
    # 큐 개수가 유지되는지가 최소 계약이다. 하나라도 사라지면 타임코드가 밀린다.
    result = load_subtitle(_FIXTURES / fixture)
    out = tmp_path / f"out{Path(fixture).suffix}"

    write_subtitle(result, _translated(result), out)

    assert len(load_subtitle(out).segments) == len(result.segments)
