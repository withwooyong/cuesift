"""② build — 깨끗한 자막 트랙 합성 (설계 스펙 §4).

결과는 **규격 위반 0건인 트랙**이다. 이후 검출되는 규격 위반은 100%
주입분이므로 오탐이 원리적으로 0이 된다. 이 전제가 깨지면 Recall 숫자의
분모와 분자가 둘 다 오염된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from bench.corpus import FilterStats, filter_pairs, load_pairs, sample
from bench.timing import GAP_MS, SentencePair, plan_segment
from bench.track_io import dump_track
from cuesift.segment import Segment
from cuesift.spec import SpecProfile, check_overlaps, check_text, load_builtin

# 스펙 §4.1 — 언어쌍당 5000. Tier 0는 LLM 호출이 없어 실행이 수 초라
# 표본을 키우는 비용이 사실상 0이다. 주입률 10%면 오류 500건이고
# Recall 추정 표준오차가 약 2%p다.
SAMPLE_SIZE = 5000
DEFAULT_SEED = 20260729


def build(
    pairs: Sequence[SentencePair],
    target_lang: str,
    profiles: Mapping[str, SpecProfile],
) -> tuple[list[Segment], dict[str, int]]:
    """문장 쌍을 타임코드가 붙은 트랙으로 만든다. `(트랙, 제외사유별 건수)`."""
    excluded = {"unfittable": 0}
    segments: list[Segment] = []
    cursor = 0

    for pair in pairs:
        planned = plan_segment(pair, target_lang, profiles)
        if planned is None:
            excluded["unfittable"] += 1
            continue
        start = cursor
        end = start + planned.duration_ms
        segments.append(
            Segment(
                id=f"{target_lang}-{len(segments):05d}",
                index=len(segments),
                start_ms=start,
                end_ms=end,
                source_text=planned.source_text,
                target_text=planned.target_text,
            )
        )
        cursor = end + GAP_MS

    return segments, excluded


def _corpus_stats(
    stats: FilterStats, unfittable: int, feasible: int, track_size: int
) -> dict[str, object]:
    """스펙 §4.4 부수 산출물 페이로드 — 사이드카 JSON과 콘솔 출력이 공유한다.

    **여기서만 계산한다.** `bench/run.py`가 코퍼스를 다시 훑어 재계산하면
    트랙과 통계의 출처가 둘로 갈라지고, 언젠가 두 숫자가 어긋난다(팀장
    지적, fix 라운드 1). `main()`의 콘솔 출력(`100 * unfittable /
    max(stats.kept, 1)`)과 같은 분자·분모를 그대로 옮겨 실어야 리포트
    쪽에서 같은 비율을 재현할 수 있다.
    """
    return {
        "total_pairs": stats.total,
        "filtered_out": dict(stats.dropped),
        "kept_after_filter": stats.kept,
        "unfittable": unfittable,
        "feasible": feasible,
        "track_size": track_size,
    }


def assert_clean(
    segments: Sequence[Segment],
    profiles: Mapping[str, SpecProfile],
    target_lang: str,
) -> None:
    """트랙이 규격 위반 0건인지 확인한다. **위반이 있으면 진행하지 않는다.**

    `검사하지 않고 통과하는 게이트는 없는 게이트보다 나쁘다` — 여기서
    통과시키면 그 위반이 리포트에서 "검출 성공"으로 집계된다.
    """
    problems: list[str] = []

    for seg in segments:
        for lang, text in (("ko", seg.source_text), (target_lang, seg.target_text or "")):
            for violation in check_text(text, seg.duration_ms, profiles[lang]):
                problems.append(f"{seg.id}[{lang}] {violation.kind} {violation.measured}")

    for seg_id, violation in check_overlaps(segments).items():
        problems.append(f"{seg_id} 겹침 {violation.measured}ms")

    if problems:
        shown = "\n  ".join(problems[:10])
        raise AssertionError(
            f"합성 트랙에 규격 위반 {len(problems)}건이 있다. 깨끗한 트랙 전제가 깨졌다:\n  {shown}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="깨끗한 벤치마크 트랙 합성")
    parser.add_argument("--pair", required=True, choices=["en-ko", "ja-ko"])
    parser.add_argument("--data-dir", type=Path, default=Path("data/ted2020"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/bench"))
    parser.add_argument("--size", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    target_lang = args.pair.split("-")[0]
    profiles = {
        "ko": load_builtin("ted-ko"),
        target_lang: load_builtin(f"ted-{target_lang}"),
    }

    src = args.data_dir / args.pair
    pairs = load_pairs(src / f"TED2020.{args.pair}.ko", src / f"TED2020.{args.pair}.{target_lang}")
    kept, stats = filter_pairs(pairs)

    # **가용 풀을 먼저 만든다.** plan_segment 통과율이 약 50%라, 표본을 먼저 뽑으면
    # 최종 트랙이 요청한 크기의 절반이 되어 스펙 §4.1의 "언어쌍당 5000"이 깨진다.
    # 그러면 오류 건수가 절반이 되어 Recall 추정 표준오차가 스펙의 약속(약 2%p)을 넘는다.
    feasible = [p for p in kept if plan_segment(p, target_lang, profiles) is not None]
    unfittable = len(kept) - len(feasible)

    chosen = sample(feasible, args.size, args.seed)
    segments, excluded = build(chosen, target_lang, profiles)

    # 풀이 이미 가능한 것만 담고 있으므로 여기서 또 빠지면 두 판정이 어긋난 것이다.
    if excluded["unfittable"]:
        raise AssertionError(
            f"가용 풀에서 뽑았는데 build가 {excluded['unfittable']}건을 제외했다 — "
            f"plan_segment 판정이 두 경로에서 다르다"
        )
    assert_clean(segments, profiles, target_lang)

    out = args.out_dir / f"{args.pair}.clean.json"
    dump_track(segments, out)

    # 스펙 §4.4 부수 산출물 — 트랙과 나란히 사이드카를 쓴다. `bench/run.py`가
    # 이 파일을 읽어 리포트에 싣는다. 여기서 쓰지 않으면 이 실행에서만
    # 존재하는 숫자(콘솔 출력)가 사라져 재현할 수 없다.
    stats_payload = _corpus_stats(stats, unfittable, len(feasible), len(segments))
    stats_path = args.out_dir / f"{args.pair}.clean.stats.json"
    stats_path.write_text(
        json.dumps(stats_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"원본 {stats.total:,}쌍 -> 필터 후 {stats.kept:,}쌍")
    for reason, count in sorted(stats.dropped.items()):
        print(f"  제거 {reason}: {count:,}")
    print(
        f"규격 미충족 제외 {unfittable:,} ({100 * unfittable / max(stats.kept, 1):.2f}%) "
        f"-> 가용 {len(feasible):,}"
    )
    print(f"표본 {len(chosen):,} -> 트랙 {len(segments):,}")
    print(f"트랙 -> {out}")
    print(f"통계 -> {stats_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
