"""벤치마크 전체 실행 (설계 스펙 §10 4~7단계).

`python -m bench.run`으로 실행한다 — `python bench/run.py`는 스크립트가 있는
디렉터리가 `sys.path[0]`이 되어 리포 루트가 빠지고, 그러면 `cuesift`·`bench`
패키지를 찾지 못해 `ModuleNotFoundError`가 난다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.fetch_ted2020 import load_manifest

from bench.inject import inject
from bench.measure import ablation, check_invariants, label_counts, measure, random_baseline
from bench.report import RunMeta, write_report
from bench.track_io import load_track
from cuesift.glossary import load_glossary
from cuesift.risk import fuse
from cuesift.signals import SignalContext, collect_all
from cuesift.spec import load_builtin

BUDGETS = (0.01, 0.02, 0.05, 0.10, 0.20, 0.30)
# FR-3.5는 이번 측정에서 빠진다(스펙 §5.3). 리포트에 미측정으로 표기한다.
UNMEASURED = ("struct.tag_lost",)
# 합성 트랙은 겹침이 0건이고 spec 주입기는 duration을 줄일 뿐이라 겹침을
# 만들지 않는다 — `spec.overlap`은 이 벤치 데이터로는 원리상 발화하지 않는다
# (계획 A 재리뷰의 미결 항목, report.py `### 판정 불가 신호` 절 참고).
UNMEASURABLE = ("spec.overlap",)

# ④·⑤ 측정 방법의 한계(Task 7 리뷰어 재측정). 사실 자체는 주입기(`bench.inject`)·
# 용어집(`bench/glossary.ted.yaml`)의 소관이므로 report.py에는 하드코딩하지
# 않는다 — 여기서 한 번만 적어 report.py는 그대로 옮겨 싣기만 한다.
_NEGATION_SAMPLE_BIAS_CAVEAT = (
    "`negation`은 주입 결과가 규격을 위반하지 않는 세그먼트에만 넣는다. 부정어 삽입이 "
    "CPS를 넘기면 `spec.violation`이 발화해 **의미 반전이 아니라 길이 증가를 잡은 것**이 "
    "Recall로 집계되기 때문이다(정정 전 실측: en 79.7% / ja 93.7%가 `spec.violation`으로 "
    "발화). 그 결과 이 유형의 표본은 CPS 여유가 큰 짧은 세그먼트로 기운다."
)
_GLOSSARY_TRADEOFF_CAVEAT = (
    "용어집 30개는 en·ja 양쪽에서 대응률 79.8% 이상인 것만 채택했다. 그래도 **용어를 "
    "포함한 세그먼트의 약 20%는 깨끗한 트랙에서도 위반으로 잡힌다** — 대응어 목록이 실제 "
    "번역의 다양성을 다 담지 못하기 때문이다. `glossary.miss`는 hard fail이 아니라 예산을 "
    "우회하지 않는다."
)
CAVEATS = (_NEGATION_SAMPLE_BIAS_CAVEAT, _GLOSSARY_TRADEOFF_CAVEAT)


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="벤치마크 전체 실행")
    parser.add_argument("--pair", required=True, choices=["en-ko", "ja-ko"])
    parser.add_argument("--track", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--out-dir", type=Path, default=Path("bench/results"))
    args = parser.parse_args(argv)

    target_lang = args.pair.split("-")[0]
    track_path = args.track or Path(f"data/bench/{args.pair}.clean.json")
    profile = load_builtin(f"ted-{target_lang}")
    glossary = load_glossary(Path("bench/glossary.ted.yaml"), target_lang)
    ctx = SignalContext(
        profile=profile, glossary=glossary, source_lang="ko", target_lang=target_lang
    )

    segments = load_track(track_path)
    mutated, labels, skipped = inject(segments, glossary, profile, rate=args.rate, seed=args.seed)

    results = measure(mutated, labels, ctx, list(BUDGETS))

    signals = collect_all(mutated, ctx)
    risks = [fuse(seg.id, signals[seg.id]) for seg in mutated]
    fp_rate = check_invariants(results, labels, mutated, risks)

    drops = ablation(mutated, labels, ctx, budget=0.10)

    error_ids = {lb.segment_id for lb in labels}
    baseline = {r.budget: random_baseline(mutated, error_ids, r.review_ratio) for r in results}

    manifest = load_manifest(Path("bench/manifest.json"))
    meta = RunMeta(
        pair=args.pair,
        seed=args.seed,
        manifest_sha256=manifest.get(args.pair, {}).get("sha256", "unknown"),
        commit=_commit(),
        sample_size=len(segments),
        excluded=dict(skipped),
        injected=label_counts(labels),
        unmeasured=UNMEASURED,
        hard_fail_false_positive_rate=fp_rate,
        unmeasurable=UNMEASURABLE,
        caveats=CAVEATS,
    )
    md_path, json_path = write_report(meta, results, drops, baseline, args.out_dir)
    print(f"리포트 -> {md_path}\n         {json_path}")
    for r in results:
        print(
            f"  예산 {r.budget:.0%}  실제 {r.review_ratio:.1%}  "
            f"Recall {r.recall:.1%}  배수 {r.lift:.1f}x"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
