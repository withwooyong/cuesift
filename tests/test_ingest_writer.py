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


def _leftovers(directory: Path) -> list[str]:
    """`.tmp` 잔여물 목록. 원자적 쓰기가 `finally`에서 지우는 것을 잰다."""
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


# **고립 서로게이트를 소스 리터럴로 쓰지 않는다.** `"\\ud800"` escape를 그대로
# 적으면 모듈에 서로게이트 문자열 **상수**가 생기고, pytest의 단언 재작성이
# 그 모듈을 다시 compile할 때 `UnicodeEncodeError`로 **수집 자체가 죽는다**
# (실측: `Interrupted: 1 error during collection`). 런타임에 만들면 상수가
# 아니므로 그 경로를 타지 않는다 - 재려는 것은 "값이 파일 쓰기에서 죽는가"이지
# "소스에 적을 수 있는가"가 아니다.
_LONE_SURROGATE = chr(0xD800)


def test_인코딩_실패해도_기존_자막이_보존되고_잘린_파일이_남지_않는다(tmp_path: Path) -> None:
    """**잘린 자막이 디스크에 남는 것이 예외가 새는 것보다 나쁘다.**

    `subs.save`는 대상을 먼저 truncate하며 열고 **그 다음에** 인코딩하므로,
    LLM이 낸 문자열에 고립 서로게이트가 하나만 있어도 그 지점까지 쓰인
    파일이 남는다 - 실측(수정 전): 10큐 중 5번째만 오염시키면
    `ten_cues.en.srt`가 **274바이트 · 큐 5개**로 남고 **5번째는 타임코드만**
    있었다. 소비자는 그것을 "번역이 다 됐다"로 읽는다.

    **오염을 5번째에 둔다.** 첫 큐에 두면 파일이 거의 비어 나와 "빈 파일"과
    구별되지 않고, 마지막에 두면 잘린 정도가 작아 큐 개수 단언이 무뎌진다.
    가운데여야 "일부는 쓰였다"는 형태가 재진다.

    **고립 서로게이트에 도달 경로가 있다** - `openai_compat.py`의
    `response.json()`이 그 문자를 통과시키고 isinstance 검사도 지나간다
    (§12 Q3가 백엔드 능력의 불균일을 전제한다).

    | 무엇을 재나 | 어떤 변이가 죽나 |
    | --- | --- |
    | 기존 내용이 **바이트 단위로** 그대로다 | `os.replace`를 `subs.save(out_path)`로 되돌리기 |
    | `.tmp`가 남지 않는다 | `finally`의 `unlink` 제거 |
    """
    out = tmp_path / "ten_cues.en.srt"
    good_result = load_subtitle(_FIXTURES / "ten_cues.srt")
    write_subtitle(good_result, _translated(good_result), out)
    good = out.read_bytes()
    assert len(load_subtitle(out).segments) == 10, "픽스처가 10큐가 아니다"

    poisoned = load_subtitle(_FIXTURES / "ten_cues.srt")
    for i, segment in enumerate(poisoned.segments):
        segment.target_text = f"EN:{_LONE_SURROGATE}오염" if i == 4 else f"EN:{segment.source_text}"

    with pytest.raises(UnicodeEncodeError):
        write_subtitle(poisoned, poisoned.segments, out)

    assert out.read_bytes() == good, "인코딩 실패가 기존 자막을 잘린 파일로 바꿨다"
    assert _leftovers(tmp_path) == [], "임시 파일이 남았다"


def test_성공한_쓰기도_임시_파일을_남기지_않는다(tmp_path: Path) -> None:
    """정상 경로의 `finally` 도달을 따로 잰다.

    위 테스트는 **실패 경로**의 정리만 재므로 `finally`를 `except`로 바꾸는
    변이를 놓친다. `os.replace`가 원본을 옮기므로 정상 경로의 `unlink`는
    언제나 no-op이고, 그 사실이 `missing_ok=True`가 필요한 이유다.
    """
    result = load_subtitle(_FIXTURES / "minimal.srt")
    out = tmp_path / "out.srt"

    write_subtitle(result, _translated(result), out)

    assert out.exists()
    assert _leftovers(tmp_path) == [], "임시 파일이 남았다"
