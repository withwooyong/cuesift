#!/usr/bin/env python3
"""마크다운의 상대 링크가 실제로 존재하는 파일을 가리키는지 검사한다.

markdownlint는 문법·스타일 린터라 이것을 잡지 못한다. `[문서](없는파일.md)`는
문법적으로 완벽히 올바른 마크다운이기 때문이다. 실제로 문서를 `docs/`로 옮겼을 때
README의 링크 4개가 전부 끊겼는데도 markdownlint는 `0 issues`로 통과했다.

**외부 URL(http·https·mailto)은 검사하지 않는다.** 이 저장소에는 24개 호스트에
걸친 외부 URL이 있고, 그중에는 크롤러를 차단하는 도메인도 있다(Q5 조사에서 확인).
남의 서버 사정으로 CI가 간헐 실패하면 그 게이트는 곧 읽히지 않게 되고, 무시되는
게이트는 없는 게이트와 같다. 그래서 이 저장소가 통제하는 내부 링크만 대상으로 삼는다.

**앵커(`#절-제목`)도 검사하지 않는다.** 앵커 생성 규칙은 렌더러마다 다르고
한국어 제목에서는 특히 갈린다. 파일 존재 여부만 판정하고 앵커는 떼어낸다.

검사 대상은 `git ls-files`가 반환하는 **추적 중인** 마크다운 파일이다.
markdownlint가 `gitignore: true`로 보는 집합과 일치시켜, 로컬과 CI가 서로 다른
파일을 검사하는 일을 막는다.

사용법:
    python scripts/check_links.py

깨진 링크가 있으면 종료 코드 1, 없으면 0을 반환한다.
"""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

# `[텍스트](대상)` 및 `[텍스트](대상 "제목")` 형태의 인라인 링크.
# 이미지 `![대체텍스트](경로)`도 같은 형태라 함께 잡힌다.
#
# 링크 텍스트에 대괄호 한 겹까지 허용하는 이유는 README의 뱃지 때문이다.
# `[![License](배지이미지)](LICENSE)`처럼 링크 안에 이미지가 중첩되는데,
# 텍스트를 `[^\]]*`로 두면 바깥 링크의 대상(LICENSE)을 통째로 놓친다.
INLINE_LINK = re.compile(
    r"\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)

# `[라벨]: 대상` 형태의 참조 정의.
REFERENCE_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)

# 코드 펜스 안의 예시는 실제 링크가 아니므로 검사 전에 제거한다.
CODE_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)

EXTERNAL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def tracked_markdown_files(root: Path) -> list[Path]:
    """git이 추적 중인 마크다운 파일 목록을 반환한다."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "*.md"],
        capture_output=True,
        check=True,
    )
    names = result.stdout.decode("utf-8").split("\0")
    return [root / name for name in names if name]


def relative_targets(text: str) -> list[str]:
    """본문에서 검사 대상인 상대 링크만 골라낸다."""
    body = CODE_FENCE.sub("", text)
    targets = INLINE_LINK.findall(body) + REFERENCE_DEF.findall(body)

    keep = []
    for target in targets:
        # 순수 앵커(`#절`)는 같은 문서 안을 가리키므로 파일 검사 대상이 아니다.
        if not target or target.startswith("#"):
            continue
        # http:·https:·mailto: 등 스킴이 붙은 것은 외부 링크다.
        if EXTERNAL_SCHEME.match(target):
            continue
        keep.append(target)
    return keep


def resolve(source: Path, target: str) -> Path:
    """링크 대상을 소스 파일 기준의 실제 경로로 바꾼다."""
    path_part = target.split("#", 1)[0]
    return source.parent / urllib.parse.unquote(path_part)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    files = tracked_markdown_files(root)

    broken: list[tuple[Path, int, str]] = []
    checked = 0

    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for target in relative_targets(text):
            checked += 1
            if resolve(path, target).exists():
                continue
            lineno = next(
                (i for i, line in enumerate(lines, 1) if f"({target})" in line),
                0,
            )
            broken.append((path.relative_to(root), lineno, target))

    # 검사 대상이 0이면 "통과"가 아니라 설정 오류다. markdownlint의
    # `Linting: 0 files` 가드와 같은 이유로 명시적으로 실패시킨다.
    if not files:
        print("::error::추적 중인 마크다운 파일이 0개다. git 저장소 안에서 실행했는지 확인하라.")
        return 1
    if checked == 0:
        print("::error::검사한 상대 링크가 0개다. 링크 추출 정규식을 확인하라.")
        return 1

    print(f"검사 대상: 마크다운 {len(files)}개 파일 · 상대 링크 {checked}개")

    if broken:
        print(f"깨진 링크 {len(broken)}개:")
        for path, lineno, target in broken:
            location = f"{path.as_posix()}:{lineno}" if lineno else path.as_posix()
            print(f"  {location}  ->  {target}")
            print(f"::error file={path.as_posix()},line={lineno or 1}::깨진 링크: {target}")
        return 1

    print("깨진 링크 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
