"""트랙 직렬화 (설계 스펙 §5.7).

가공물은 `data/bench/`에만 둔다 — CC BY-NC-ND 4.0이라 리포에 커밋하지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from cuesift.segment import Segment

_FIELDS = ("id", "index", "start_ms", "end_ms", "source_text", "target_text")


def dump_track(segments: Sequence[Segment], path: Path) -> None:
    """트랙을 JSON으로 쓴다. `ensure_ascii=False` — ko·ja가 읽을 수 있어야 디버깅이 된다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{f: getattr(seg, f) for f in _FIELDS} for seg in segments]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_track(path: Path) -> list[Segment]:
    """JSON에서 트랙을 읽는다.

    `Segment.__post_init__`이 타임코드를 검증하므로 손상된 파일은
    여기서 `ValueError`로 드러난다 — 조용히 음수 duration을 만들지 않는다.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Segment(**{f: item[f] for f in _FIELDS}) for item in raw]
