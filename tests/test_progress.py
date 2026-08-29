"""진행 표시와 비대화형 감지 (FR-8.5)."""

from __future__ import annotations

import os


def test_진행_표시가_모든_테스트에서_기본으로_꺼져_있다() -> None:
    # **이 단언이 픽스처의 게이트다.** 픽스처가 사라지면 진행 줄이 기존
    # stderr 단언에 섞여 수십 건이 한꺼번에 죽는데, 그때 원인은 진행
    # 표시가 아니라 각 테스트의 문제처럼 보인다 (설계 §9 R2).
    assert os.environ["CUESIFT_PROGRESS"] == "0"
