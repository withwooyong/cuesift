"""트랙 직렬화 (설계 스펙 §5.7).

가공물은 `data/bench/`에만 둔다 — CC BY-NC-ND 4.0이라 리포에 커밋하지 않는다.

**주입 감사 산출물도 여기서 낸다.** 변조 트랙과 정답 라벨이 디스크에 없으면
제3자는 코드를 직접 돌려야만 라벨을 검증할 수 있고, 그것은 감사가 아니라
재현이다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from bench.inject import Label
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


def dump_audit(
    mutated: Sequence[Segment],
    labels: Sequence[Label],
    out_dir: Path,
    pair: str,
    *,
    seed: int,
    commit: str,
) -> tuple[Path, Path]:
    """변조 트랙과 정답 라벨을 **함께** 쓴다 (스펙 §5.7).

    두 파일을 각각 쓰는 함수 둘로 두지 않는다 — 짝이 아닌 산출물은 감사에
    쓸 수 없는데, 함수가 둘이면 한쪽만 호출하는 실수가 구조적으로 가능하다.

    변조 트랙은 `dump_track`과 **같은 형식**이다. 감사자가 새 파서를 짜야
    한다면 감사 수단이 아니다 — `load_track`으로 그대로 읽힌다.

    라벨 파일은 트랙의 SHA-256을 함께 적는다. 라벨만 있고 어느 트랙의
    것인지 모르면 "이 정답이 저 트랙에 대응한다"를 확인할 수 없다
    (`bench/manifest.json`이 코퍼스에 대해 하는 것과 같은 관용구).
    """
    injected_path = out_dir / f"{pair}.injected.json"
    labels_path = out_dir / f"{pair}.labels.json"

    dump_track(mutated, injected_path)

    payload = {
        "pair": pair,
        "seed": seed,
        "commit": commit,
        "injected_track": injected_path.name,
        "injected_track_sha256": hashlib.sha256(injected_path.read_bytes()).hexdigest(),
        "segment_count": len(mutated),
        "labels": [
            {"segment_id": lb.segment_id, "kind": lb.kind, "detail": lb.detail} for lb in labels
        ],
    }
    labels_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return injected_path, labels_path


def load_labels(path: Path) -> list[Label]:
    """정답 라벨을 읽고 **짝인 트랙의 해시를 대조한다**.

    해시를 적기만 하고 아무도 대조하지 않으면 없는 게이트와 같다. 트랙이
    라벨과 어긋나면 여기서 `ValueError`로 드러난다 — 어긋난 채로 측정하면
    Recall이 조용히 틀린다.

    트랙 파일이 없으면 대조를 건너뛴다. 라벨만 따로 건네받는 경우가 있고
    (`detail` 검토), 그때 파일 부재로 실패시키면 쓸 수 없는 API가 된다.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))

    track_path = path.with_name(payload["injected_track"])
    expected = payload["injected_track_sha256"]
    if track_path.exists():
        actual = hashlib.sha256(track_path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(
                f"변조 트랙의 SHA-256이 라벨 기록과 다르다 ({track_path.name}): "
                f"기록 {expected[:16]}… / 실제 {actual[:16]}…"
            )

    return [
        Label(segment_id=item["segment_id"], kind=item["kind"], detail=item["detail"])
        for item in payload["labels"]
    ]
