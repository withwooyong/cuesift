"""`cuesift check` 배선 테스트 (FR-8.2·FR-7.5).

설계 §10.3이 지목한 "diff로 판정할 수 없는 것" 셋을 여기서 닫는다 —
큐 번호가 원본 위치인가 · stdout/stderr 분리 · 종료 코드 2와 66의 구분.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuesift.cli import (
    _format_detail,
    _format_report,
    _format_timecode,
    _harden_output_streams,
    _resolve_profile,
    app,
)
from cuesift.spec import SpecViolation, TrackViolation

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
# 리포 루트 기준으로 고정한다. 상대 경로 "specs/ted-ko.yaml"로 두면 pytest를
# 리포 루트가 아닌 곳에서 돌릴 때만 실패해, 통과가 실행 위치에 의존하게 된다.
SPECS = Path(__file__).parents[1] / "specs"


def test_resolve_profile_reads_a_builtin_name():
    profile, label = _resolve_profile("ko")
    assert profile.name == "ko"
    assert label == "ko"


def test_resolve_profile_reads_a_yaml_path():
    """FR-5.3 — 사용자 프로파일이 CLI에서 도달 가능해야 한다."""
    profile, _ = _resolve_profile(str(SPECS / "ted-ko.yaml"))
    assert profile.name == "ted-ko"


def test_resolve_profile_labels_a_user_file_with_its_source():
    """사용자 프로파일은 규격 이름만으로 내장과 구별되지 않는다 (설계 §7.2).

    `name: ko`인 사용자 파일과 내장 `ko`는 헤더가 **바이트 단위로 같아진다.**
    그러면 "엉뚱한 프로파일로 통과한 것을 알 수 없다"를 막으려고 헤더를 둔
    의미가 사라진다 — 하필 FR-5.3 경로에서만 죽는다.

    출처를 label에 함께 실어 구별한다. 확장자 판정(D10)이 여기 한 곳에만
    남도록 label을 `_resolve_profile`이 만든다 — Task 6이 확장자를 다시 보면
    D10 로직이 두 곳으로 복제된다.
    """
    profile, label = _resolve_profile(str(SPECS / "ted-ko.yaml"))
    assert profile.name == "ted-ko"
    assert label.startswith("ted-ko (")
    assert "ted-ko.yaml" in label
    assert label != _resolve_profile("ted-ko")[1], "내장과 구별되지 않는다"


def test_resolve_profile_routes_an_uppercase_extension_as_a_path(tmp_path):
    """`.YAML`도 경로로 라우팅한다.

    **Windows는 파일명 대소문자를 구분하지 않으므로 `my-spec.YAML`은 완전히
    정상인 파일명**이고, 이 프로젝트의 개발 플랫폼이 Windows다. 소문자만 보면
    실존하는 파일이 "내장 이름이 없다"는 D10이 막으려던 틀린 진단을 받는다.

    라우팅만 소문자화하고 `load_profile`에는 **원본 문자열**을 넘긴다 —
    CI의 Linux는 대소문자를 구분하므로 경로를 소문자화하면 파일을 못 찾는다.
    """
    target = tmp_path / "USER.YAML"
    target.write_text((SPECS / "ted-ko.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profile, label = _resolve_profile(str(target))
    assert profile.name == "ted-ko"
    assert "USER.YAML" in label, "원본 대소문자가 보존되지 않았다"


def test_resolve_profile_rejects_an_unknown_builtin_name():
    """오타 난 내장 이름은 사용 가능한 목록을 함께 보여준다."""
    import typer

    with pytest.raises(typer.BadParameter) as caught:
        _resolve_profile("th")
    assert "ted-ko" in str(caught.value)


def test_resolve_profile_reports_a_missing_yaml_as_a_path_problem():
    """확장자로 가르므로 오타 난 경로가 '내장 이름이 없다'는 틀린 진단을 받지 않는다.

    존재 여부로 갈랐다면 './없는.yaml'이 내장 이름으로 해석되어
    "'./없는.yaml' 프로파일이 없다. 사용 가능: en, ja, ..."라는
    엉뚱한 메시지가 나갔을 것이다.
    """
    import typer

    with pytest.raises(typer.BadParameter) as caught:
        _resolve_profile("./없는파일.yaml")
    message = str(caught.value)
    assert "사용 가능" not in message, "내장 이름으로 잘못 해석됐다"


def test_resolve_profile_reads_a_yml_extension(tmp_path):
    """`.yml`도 경로로 받는다 (설계 §8 D10의 논리적 귀결).

    `.yml`을 빼면 `--spec spec.yml`이 내장 이름으로 해석되어 D10이 막으려던
    틀린 진단을 그대로 받는다. `.yaml`과 같은 줄을 지나므로 statement 커버리지
    100%가 이 공백을 가린다 — 값으로 지나가는 테스트가 따로 있어야 한다.

    실패가 아니라 **로드 성공**까지 확인한다. 실패 경로만 보면 확장자 분기가
    아니라 예외 처리를 재게 된다.
    """
    target = tmp_path / "custom.yml"
    target.write_text((SPECS / "ted-ko.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert _resolve_profile(str(target))[0].name == "ted-ko"


def test_resolve_profile_wraps_a_broken_builtin_profile(monkeypatch):
    """내장 프로파일이 깨졌을 때 `ValueError`가 새어나가지 않아야 한다.

    `load_builtin`은 내부에서 `load_profile`을 부르므로(profile.py) 동봉된
    YAML이 손상되면 `FileNotFoundError`가 아니라 `ValueError`가 난다.
    이것이 새어나가면 미처리 traceback으로 **종료 코드 1**이 되는데,
    이 저장소에서 1은 "규격 위반 발견"이라 **패키징 사고가 자막 결함으로
    오보된다**. 사용자는 자막을 고치려 들고 진짜 원인은 숨는다.
    """
    import typer

    from cuesift import cli

    def _broken(name: str):
        raise ValueError("specs/ko.yaml: 필수 필드가 없다")

    monkeypatch.setattr(cli, "load_builtin", _broken)
    with pytest.raises(typer.BadParameter) as caught:
        _resolve_profile("ko")
    assert "필수 필드가 없다" in str(caught.value)


def test_resolve_profile_wraps_a_yaml_syntax_error(tmp_path):
    """YAML 문법 오류도 `BadParameter`로 모아야 한다.

    문법 오류는 `yaml.YAMLError`(`ParserError` 등)라서 **`OSError`도 `ValueError`도
    아니다.** 둘만 잡으면 사용자가 준 `--spec ./my.yaml` 하나로 미처리 traceback과
    **종료 코드 1**이 나가고, 이 저장소에서 1은 "규격 위반 발견"이다.
    FR-5.3이 존재하는 이유인 사용자 경로에 그대로 뚫려 있던 구멍이다.
    """
    import typer

    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [unclosed\n  bad: : :\n", encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        _resolve_profile(str(broken))


def test_format_timecode_is_fixed_regardless_of_input_format():
    """SRT는 쉼표, VTT는 마침표를 쓰지만 출력 표기는 하나로 고정한다 (설계 §7.3)."""
    assert _format_timecode(83400) == "00:01:23.400"
    assert _format_timecode(0) == "00:00:00.000"
    assert _format_timecode(3_661_007) == "01:01:01.007"


def test_format_report_uses_the_original_cue_number_not_the_filtered_index():
    """설계 §10.3 — diff로는 판정할 수 없는 항목이다.

    주석이 없는 파일에서는 event_index와 segment.index가 같아 틀려도 통과한다.
    여기서는 일부러 갈라진 event_index를 넣어 원본 큐 번호를 쓰는지 본다.
    """
    violations = [
        TrackViolation("00001", 5000, SpecViolation("line_length", 22.0, 16.0, line_index=1)),
    ]
    # 원본 이벤트 2번(0-based) → 큐 번호 3. 필터 후 인덱스로 세면 2가 된다.
    event_index = {"00000": 1, "00001": 2}

    lines = _format_report(
        source_name="check_violations.ass",
        fmt="ass",
        profile_label="ko",
        cue_total=4,
        violations=violations,
        event_index=event_index,
    )

    assert any("#3" in line for line in lines), lines
    assert not any("#2 " in line for line in lines), lines


def test_format_report_renders_each_violation_kind():
    violations = [
        TrackViolation("00000", 83400, SpecViolation("line_length", 21.0, 16.0, line_index=1)),
        TrackViolation("00000", 83400, SpecViolation("cps", 18.2, 12.0)),
        TrackViolation("00001", 242100, SpecViolation("overlap", 1200.0, 0.0)),
        TrackViolation("00002", 371000, SpecViolation("empty_cue", 0.0, 0.0)),
        TrackViolation("00003", 400000, SpecViolation("duration_short", 500.0, 833.0)),
    ]
    event_index = {"00000": 0, "00001": 1, "00002": 2, "00003": 3}

    body = "\n".join(
        _format_report(
            source_name="x.srt",
            fmt="srt",
            profile_label="ko",
            cue_total=120,
            violations=violations,
            event_index=event_index,
        )
    )

    assert "x.srt (srt · 검사 큐 120개 · 프로파일 ko)" in body
    assert "line_length" in body and "21.0 > 16.0" in body
    # line_index는 0-based다 — 사람이 읽는 좌표는 +1.
    assert "(2번째 줄)" in body
    assert "overlap" in body and "1200ms" in body
    assert "empty_cue" in body and "텍스트 없음" in body
    assert "duration_short" in body and "500ms < 833ms" in body
    # 위반 5건이지만 위반 큐는 4개다 — 한 큐에 두 건이 있다.
    assert "위반 5건 · 위반 큐 4/120개 (3.3%)" in body


def test_format_report_states_what_it_checked_when_clean():
    """'통과했나'가 아니라 '무엇을 대상으로 통과했나'를 본다."""
    lines = _format_report(
        source_name="clean.ko.srt",
        fmt="srt",
        profile_label="ko",
        cue_total=120,
        violations=[],
        event_index={},
    )
    # em dash가 아니라 ASCII 하이픈이다. 전역 제약 "출력 문자열에 em dash 금지" 참조.
    assert lines == ["clean.ko.srt (srt · 검사 큐 120개 · 프로파일 ko) - 위반 없음"]


def test_format_report_keeps_columns_aligned_across_cue_number_widths():
    """큐 번호 자릿수가 섞여도 뒤따르는 열이 밀리지 않아야 한다.

    설계 §7.2의 예시가 `#17`·`#64`·`#98`로 **전부 2자리**라 이 결함이 보이지
    않았다. 실제 자막은 수백~수천 큐이므로 1자리와 3자리가 한 리포트에 섞인다.
    폭을 주지 않으면 타임코드·kind·수치 열이 전부 오른쪽으로 밀린다.

    **부분 문자열이 아니라 열 위치를 본다.** `in body` 단언은 밀린 줄도 그대로
    통과시키므로 정렬 결함을 영원히 못 잡는다.
    """
    event_index = {"a": 0, "b": 41, "c": 118}
    violations = [
        TrackViolation(seg_id, 83400, SpecViolation("cps", 18.2, 12.0)) for seg_id in event_index
    ]

    lines = _format_report(
        source_name="x.srt",
        fmt="srt",
        profile_label="ko",
        cue_total=120,
        violations=violations,
        event_index=event_index,
    )

    rows = [line for line in lines if line.lstrip().startswith("#")]
    assert len(rows) == 3, lines
    # 타임코드는 폭이 고정(`HH:MM:SS.mmm`)이라 시작 위치가 같으면 뒤도 전부 같다.
    assert len({row.index("00:01:23.400") for row in rows}) == 1, rows


def test_format_detail_renders_duration_long_with_the_opposite_sign():
    """`duration_long`을 값으로 지나가는 테스트 — 커버리지가 가리는 분기다.

    `sign = "<" if kind == "duration_short" else ">"`는 **조건식 한 줄**이라
    `duration_short`만 지나가도 statement 커버리지가 100%로 찬다. 부호가
    뒤집혀도 어느 게이트도 울리지 않으므로 값으로 지나가는 테스트가 필요하다.

    두 부호를 한 테스트에서 대조하는 것이 요점이다 — 한쪽만 보면 둘이
    같은 부호를 내도 통과한다.
    """
    assert _format_detail(SpecViolation("duration_long", 9000.0, 7000.0)) == "9000ms > 7000ms"
    assert _format_detail(SpecViolation("duration_short", 500.0, 833.0)) == "500ms < 833ms"


def test_format_report_denominator_stays_the_filtered_cue_count():
    """`검사 큐 N개`가 아래의 `#N`보다 작은 것은 정상이다 — 분모를 바꾸면 안 된다.

    주석·드로잉이 섞인 파일에서는 검사 대상(2개)보다 원본 큐 번호(#4)가 크다.
    이것이 자기모순처럼 보여 분모를 **원본 이벤트 수로 되돌리는** 수정이 들어오면
    검사 대상이 아닌 이벤트까지 세어 위반 비율이 과소평가되고, 그것은
    Recall@Budget 지표를 직접 건드린다. 낱말을 `검사 큐`로 좁혀 모호함만 없앴다.
    """
    event_index = {"a": 0, "b": 3}
    violations = [
        TrackViolation("a", 1000, SpecViolation("line_length", 5.5, 3.0, line_index=0)),
        TrackViolation("b", 7000, SpecViolation("line_length", 6.0, 3.0, line_index=0)),
    ]

    body = "\n".join(
        _format_report(
            source_name="tags.ass",
            fmt="ass",
            profile_label="ko",
            cue_total=2,
            violations=violations,
            event_index=event_index,
        )
    )

    assert "tags.ass (ass · 검사 큐 2개 · 프로파일 ko)" in body
    assert "#4" in body, "원본 큐 번호는 검사 큐 수보다 클 수 있다"
    # 분모는 검사한 큐 수(2)다. 원본 이벤트 수(4)로 되돌리면 50.0%가 된다.
    assert "위반 큐 2/2개 (100.0%)" in body


def test_check_reports_all_violation_kinds_with_original_cue_numbers():
    """설계 §10.3의 세 항목 중 둘을 한 번에 닫는다 — 큐 번호와 출력 형식.

    이 픽스처는 머리말 Comment 때문에 네 큐 전부 event_index != index다.
    큐 번호를 segment.index + 1로 계산하면 네 줄이 전부 1씩 작게 나온다.
    """
    result = runner.invoke(app, ["check", str(FIXTURES / "check_violations.ass"), "--spec", "ko"])

    assert result.exit_code == 1
    out = result.stdout
    # 낱말은 `검사 큐`다. 계획서의 `큐 4개`는 Task 5 리뷰가 낱말을 좁히기 전의 표기라
    # 그대로 두면 이 단언이 헤더 전체를 못 보고 통과한다(실측으로 정정).
    assert "check_violations.ass (ass · 검사 큐 4개 · 프로파일 ko)" in out
    assert "#3" in out and "line_length" in out and "22.0 > 16.0" in out
    assert "(2번째 줄)" in out
    assert "#3" in out and "cps" in out and "25.5 > 12.0" in out
    assert "#4" in out and "overlap" in out and "500ms" in out
    assert "#5" in out and "empty_cue" in out and "텍스트 없음" in out
    assert "위반 4건 · 위반 큐 3/4개 (75.0%)" in out
    # 필터 후 인덱스로 셌다면 #1·#2·#3·#4가 나온다. #1은 정상 큐라 나오면 안 된다.
    assert "#1 " not in out


def test_check_passes_a_clean_track():
    result = runner.invoke(app, ["check", str(FIXTURES / "minimal.srt"), "--spec", "ko"])
    assert result.exit_code == 0
    assert "minimal.srt (srt · 검사 큐 2개 · 프로파일 ko) - 위반 없음" in result.stdout


def test_check_flags_line_count_not_line_length_on_multiline():
    """실측: multiline.vtt는 세 줄이지만 각 줄은 16자를 넘지 않는다."""
    result = runner.invoke(app, ["check", str(FIXTURES / "multiline.vtt"), "--spec", "ko"])
    assert result.exit_code == 1
    assert "line_count" in result.stdout
    assert "line_length" not in result.stdout


def test_check_flags_overlap_with_the_measured_gap():
    """실측: overlap.vtt의 겹침은 1000ms다."""
    result = runner.invoke(app, ["check", str(FIXTURES / "overlap.vtt"), "--spec", "ko"])
    assert result.exit_code == 1
    assert "overlap" in result.stdout
    assert "1000ms" in result.stdout


def test_check_now_catches_the_empty_cue_that_nothing_caught_before():
    """설계 §12.3 — 세 경로 전부 놓치던 사각지대를 check_empty_cues가 닫는다."""
    result = runner.invoke(app, ["check", str(FIXTURES / "empty_cue.srt"), "--spec", "ko"])
    assert result.exit_code == 1
    assert "empty_cue" in result.stdout
    assert "#2" in result.stdout


@pytest.mark.parametrize(
    "fixture",
    ["not_subtitle.txt", "cp949.srt", "reversed.srt", "all_comments.ass"],
)
def test_bad_file_content_exits_66_not_2(fixture: str):
    """설계 §10.3 — 둘 다 '0이 아님'이라 != 0으로 단언하면 뒤바뀌어도 통과한다.

    66은 파일 내용이 틀렸다는 뜻이고 2는 호출이 틀렸다는 뜻이다.
    CI가 둘을 구분하지 못하면 '경로 오타'와 '자막이 깨졌다'에 같은 대응을 한다.
    """
    result = runner.invoke(app, ["check", str(FIXTURES / fixture), "--spec", "ko"])
    assert result.exit_code == 66


def test_missing_file_exits_2_not_66():
    result = runner.invoke(app, ["check", str(FIXTURES / "없는파일.srt"), "--spec", "ko"])
    assert result.exit_code == 2


def test_directory_input_exits_2():
    """HANDOFF가 WP6으로 미뤄 둔 항목 — typer가 거른다 (설계 D11).

    인제스트에서 처리하면 디렉터리가 not_found로 보고되어
    '존재하는데 없다'는 틀린 진단이 남는다.
    """
    result = runner.invoke(app, ["check", str(FIXTURES), "--spec", "ko"])
    assert result.exit_code == 2


def test_unknown_profile_exits_2():
    result = runner.invoke(app, ["check", str(FIXTURES / "minimal.srt"), "--spec", "th"])
    assert result.exit_code == 2


def test_fail_on_none_prints_violations_but_exits_0():
    """게이트를 끄고 보고만 받는 경로다 (설계 §11 R2)."""
    result = runner.invoke(
        app,
        ["check", str(FIXTURES / "overlap.vtt"), "--spec", "ko", "--fail-on", "none"],
    )
    assert result.exit_code == 0
    assert "overlap" in result.stdout


def test_fail_on_hard_and_any_agree_in_v01():
    """설계 §5.1 — v0.1에는 등급이 하나다. 둘이 갈라지면 등급이 생긴 것이다."""
    codes = []
    for value in ("hard", "any"):
        result = runner.invoke(
            app,
            ["check", str(FIXTURES / "overlap.vtt"), "--spec", "ko", "--fail-on", value],
        )
        codes.append(result.exit_code)
    assert codes == [1, 1]


def test_a_clean_file_with_a_non_cp949_name_still_exits_zero(tmp_path):
    """출력에 실리는 것은 우리 리터럴만이 아니다 — 사용자 파일 경로가 그대로 들어간다.

    Windows 기본 로케일(cp949)에서 인코딩할 수 없는 문자가 파일명에 있으면
    리다이렉트 시 `UnicodeEncodeError`로 프로세스가 죽고 종료 코드 1이 나간다.
    이 저장소에서 1은 "규격 위반 발견"이므로 **위반 0건인 파일이 CI에서 실패로 읽힌다.**

    실측된 사례: `Amélie.srt`(U+00E9) · `S01E01 – ko.srt`(U+2013).
    이모지·간체 한자·NBSP도 같다. 자막 파일명에 흔한 문자들이다.
    """
    target = tmp_path / "Amélie – ko.srt"
    target.write_bytes((FIXTURES / "minimal.srt").read_bytes())

    result = runner.invoke(app, ["check", str(target), "--spec", "ko"])

    assert result.exit_code == 0, result.stderr


def test_an_unreadable_file_exits_66_not_1(monkeypatch):
    """읽을 수 없는 파일은 "파일이 틀림"(66)이지 "규격 위반"(1)이 아니다.

    `loader.py`가 `OSError`를 `IngestError`로 정규화하지 않으면 `PermissionError`가
    `except IngestError`를 통과해 미처리 traceback이 되고 exit 1이 된다.
    Windows에서는 편집기·트랜스코더·OneDrive가 자막을 잡고 있는 것이 흔하다.

    **권한을 실제로 조작하지 않는 이유:** 그쪽은 `test_ingest.py`가 플랫폼별 수단으로
    맡는다. 여기서 같은 설정을 반복하면 잠금 수단이 안 먹는 환경에서 이 테스트도 함께
    조용히 건너뛰어져, **CLI 계약(66)이 검증되지 않은 채로 통과**한다.
    읽기 지점에서 `PermissionError`를 던지게 하는 것이 플랫폼과 무관하게 같은 계약을
    검증한다.
    """
    import pysubs2

    def raise_permission_error(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pysubs2, "load", raise_permission_error)

    result = runner.invoke(app, ["check", str(FIXTURES / "minimal.srt"), "--spec", "ko"])

    assert result.exit_code == 66, f"exit {result.exit_code} — 1이면 규격 위반으로 오보된다"


def test_violations_go_to_stdout_and_diagnostics_go_to_stderr():
    """설계 §10.3 — mix_stderr면 섞여서 구분되지 않는다.

    위반 목록은 이 명령의 정상 산출물이지 오류 메시지가 아니다.
    `cuesift check ... > violations.txt`로 갈무리할 수 있어야 한다.
    """
    ok = runner.invoke(app, ["check", str(FIXTURES / "overlap.vtt"), "--spec", "ko"])
    assert "overlap" in ok.stdout
    assert "overlap" not in ok.stderr

    bad = runner.invoke(app, ["check", str(FIXTURES / "cp949.srt"), "--spec", "ko"])
    assert bad.exit_code == 66
    assert "utf-8" in bad.stderr
    assert bad.stdout.strip() == ""


def test_harden_output_streams_covers_stderr_too():
    """stdout만 걸면 66이 1로 바뀐다 — `IngestError` 메시지는 stderr로 나간다.

    `CliRunner`의 스트림은 utf-8이라 CLI 테스트로는 이 결함이 드러나지 않는다.
    cp949 스트림을 직접 만들어 확인한다.

    **설정값만 보지 않고 실제로 써 본다.** `errors` 속성만 단언하면 핸들러가
    쓰기 경로에 안 걸려도 통과한다.
    """
    streams = {name: io.TextIOWrapper(io.BytesIO(), encoding="cp949") for name in ("out", "err")}
    original = (sys.stdout, sys.stderr)
    sys.stdout, sys.stderr = streams["out"], streams["err"]
    try:
        _harden_output_streams()
        for stream in streams.values():
            # cp949가 인코딩하지 못하는 문자들이다(실측): U+00E9 · U+2013 · U+2014.
            stream.write("Amélie – ko.srt 위반 없다 — 끝")
            stream.flush()
    finally:
        sys.stdout, sys.stderr = original

    assert [stream.errors for stream in streams.values()] == ["backslashreplace"] * 2


def test_harden_output_streams_survives_a_stream_without_reconfigure():
    """`io.StringIO`로 stdout을 갈아 끼운 호출자가 있어도 죽지 않아야 한다.

    `AttributeError`로 죽으면 그것이야말로 이 함수가 막으려던 종류의 사고다 —
    미처리 traceback은 종료 코드 1이고, 1은 이 저장소에서 "규격 위반 발견"이다.
    `io.StringIO`에는 `reconfigure`가 없다(실측).
    """
    original = (sys.stdout, sys.stderr)
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        _harden_output_streams()
    finally:
        sys.stdout, sys.stderr = original
