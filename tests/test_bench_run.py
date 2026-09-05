"""`bench/run.py`의 파서 테스트 (컨트롤러 판정 A · 태스크7 브리프 Step 1).

**`test_bench_report.py`가 아니라 이 파일에 둔다.** `build_arg_parser`는
`bench/run.py`의 심볼이고 `test_bench_report.py`는 `render_markdown`·
`write_report`(둘 다 `bench/report.py`)를 검사하는 파일이다 — 브리프의
"Test: tests/test_bench_report.py"는 Task 7 착수 전에 잘린 것이라 이
모듈 경계를 몰랐다. 컨트롤러 노트가 "브리프의 코드 블록이 그대로 동작하지
않는다"고 이미 밝혔으므로, 테스트 배치도 실제 모듈 경계를 따른다.
"""

from __future__ import annotations

import pytest
from bench.run import build_arg_parser


def test_tier1_없이는_흐름이_같다():
    """`--tier1`이 꺼져 있으면 지금과 한 줄도 다르지 않다 (설계 D9).

    **켜져 있으면 CI가 LLM 백엔드를 요구하게 된다.** 벤치 테스트는
    data/가 .gitignore라 CI에서 이미 skip되는데, 기본값이 바뀌면
    로컬에서만 조용히 다른 것을 재게 된다.
    """
    parser_defaults = build_arg_parser().parse_args(["--pair", "en-ko"])
    assert parser_defaults.tier1 is False
    assert parser_defaults.embed_model is None


def test_tier1_인자_기본값이_전부_꺼짐이다():
    """새로 더한 인자 다섯 개(브리프 Step 4)가 전부 `None`·꺼짐이어야
    `--tier1` 없는 기존 호출이 이 인자들의 영향을 받지 않는다."""
    args = build_arg_parser().parse_args(["--pair", "ja-ko"])
    assert args.base_url is None
    assert args.model is None
    assert args.embed_base_url is None
    assert args.cache_dir is None


def test_파서는_pair_없이_거부한다():
    """`--pair`는 필수다 — 회귀 시 기존 계약(en-ko/ja-ko 둘만 허용)이 깨진다."""
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([])
