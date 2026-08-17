"""`python -m cuesift` 진입점.

콘솔 스크립트 `cuesift`(`pyproject.toml`의 `[project.scripts]`, `cuesift.cli:run`)와
**같은** `run()`을 부른다. 이 파일이 없으면 `python -m cuesift`가
`No module named cuesift.__main__`으로 죽는다(실측: WP7b Task 4 리뷰
라운드 1) - Task 7의 live 테스트가 이 경로에 기댄다. 별도 로직을 두지
않는 것은 두 진입점이 갈라지면 한쪽만 `_TolerantOutput` 등의 방어를
받는 사고가 나기 때문이다.
"""

from __future__ import annotations

from cuesift.cli import run

if __name__ == "__main__":
    run()
