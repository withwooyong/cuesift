"""벤치마크 전체 실행 (설계 스펙 §10 4~7단계).

`python -m bench.run`으로 실행한다 — `python bench/run.py`는 스크립트가 있는
디렉터리가 `sys.path[0]`이 되어 리포 루트가 빠지고, 그러면 `cuesift`·`bench`
패키지를 찾지 못해 `ModuleNotFoundError`가 난다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from scripts.fetch_ted2020 import load_manifest

from bench.classify_negation import CLEAN, classify
from bench.inject import Label, inject
from bench.measure import (
    _recall,
    ablation,
    check_invariants,
    label_counts,
    measure,
    random_baseline,
)
from bench.report import RunMeta, render_tier1_comparison, write_report
from bench.track_io import dump_audit, load_track
from cuesift.embed import (
    Embedder,
    EmbeddingError,
    EmbeddingNotFoundError,
    EmbeddingUnsupportedError,
    OpenAICompatibleEmbedder,
)
from cuesift.glossary import load_glossary
from cuesift.risk import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals import SignalContext, collect_all
from cuesift.signals.backtranslation import BackTranslation
from cuesift.spec import load_builtin
from cuesift.tier1 import triage_with_tier1
from cuesift.translate.openai_compat import OpenAICompatibleProvider
from cuesift.triage import select_by_budget

BUDGETS = (0.01, 0.02, 0.05, 0.10, 0.20, 0.30)
# FR-3.5는 이번 측정에서 빠진다(스펙 §5.3). 리포트에 미측정으로 표기한다.
UNMEASURED = ("struct.tag_lost",)
# 합성 트랙은 겹침이 0건이고 spec 주입기는 duration을 줄일 뿐이라 겹침을
# 만들지 않는다 — `spec.overlap`은 이 벤치 데이터로는 원리상 발화하지 않는다
# (계획 A 재리뷰의 미결 항목, report.py `### 판정 불가 신호` 절 참고).
UNMEASURABLE = ("spec.overlap",)

# Tier 1을 재는 예산 두 지점(FR-4.2 · 태스크7 브리프 Step 4).
#
# **BUDGETS(6개) 전부를 돌지 않는다.** 예산 지점마다 회색지대 후보가
# `floor(n × TIER1_MAX_RATIO)`건까지 새로 고려돼, 트랙 하나(예: n=5000)에서
# 예산 지점 6개를 다 돌면 후보가 최대 6 × 250 = 1,500건으로 늘어난다.
# 로컬 Ollama가 긴 입력에서 `ReadTimeout`을 낸 전례가 있어(실측,
# `cuesift.embed.openai_compat`) 후보가 늘수록 장시간 실행이 중간에 죽을
# 위험이 커진다. 10%는 이 프로젝트의 기본 운영점(README)이고 30%는
# "예산을 늘려도 소용없다"를 보여줄 비교점이다(report.py의
# `_NEGATION_COMPARISON_BUDGET`과 선택 이유가 같다).
TIER1_BUDGETS = (0.10, 0.30)

# Tier 1 후보 상한 비율. 값의 출처는 `cli.py:240`의
# `_TIER1_DEFAULT_MAX_RATIO = 0.05`다 — **임포트하지 않는다.** 밑줄로
# 시작하는 사적 상수라 벤치가 끌어다 쓸 대상이 아니고, 같은 값을 여기 새로
# 둔다(컨트롤러 판정 B).
#
# **올리면 무엇이 깨지는가.** 예산 지점마다 후보가 늘어(위 TIER1_BUDGETS
# 문단의 1,500건 산식이 더 커진다) 로컬 Ollama의 `ReadTimeout`(실측)을 만날
# 확률이 그만큼 늘고, 장시간 실행이 중간에 죽는다.
TIER1_MAX_RATIO = 0.05

# ④·⑤ 측정 방법의 한계(Task 7 리뷰어 재측정). 사실 자체는 주입기(`bench.inject`)·
# 용어집(`bench/glossary.ted.yaml`)의 소관이므로 report.py에는 하드코딩하지
# 않는다 — 여기서 한 번만 적어 report.py는 그대로 옮겨 싣기만 한다.
_NEGATION_SAMPLE_BIAS_CAVEAT = (
    "`negation`은 **부정 표현을 제거**해 만든다 — 삽입하지 않는다. 삽입은 언어를 알아야 "
    "하는데 주입기가 `target_lang`을 받지 않아, 일본어 문장에 영어 `not`이 끼워 들어가 "
    "**ja-ko 라벨 71건 전부가 의미 반전이 아니었다**(en-ko도 61/71이 같은 경로였다. "
    "2026-09-04 정정). 제거 전용이라 표본은 **원래 부정문이었던 세그먼트**로 기운다 — "
    "긍정문을 부정문으로 뒤집는 방향은 이 벤치가 재지 않는다. 자격은 en 524건 / ja 313건이고 "
    "quota는 72건이다."
)
_LABEL_REVISION_CAVEAT = (
    "**2026-07-29 리포트와 이 리포트는 정답지가 다르다.** 위 정정으로 negation 자격 "
    "세그먼트가 바뀌었고, `inject`는 유형 처리 순서를 **자격 희소도**로 정하므로 "
    "negation 외 6개 유형의 세그먼트 배정도 함께 달라졌다. 두 리포트의 수치를 직접 빼서 "
    "비교하면 안 된다."
)
_GLOSSARY_TRADEOFF_CAVEAT = (
    "용어집 30개는 en·ja 양쪽에서 대응률 79.8% 이상인 것만 채택했다. 그래도 **용어를 "
    "포함한 세그먼트의 약 20%는 깨끗한 트랙에서도 위반으로 잡힌다** — 대응어 목록이 실제 "
    "번역의 다양성을 다 담지 못하기 때문이다. `glossary.miss`는 hard fail이 아니라 예산을 "
    "우회하지 않는다."
)
CAVEATS = (_NEGATION_SAMPLE_BIAS_CAVEAT, _LABEL_REVISION_CAVEAT, _GLOSSARY_TRADEOFF_CAVEAT)


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


def _load_corpus_stats(track_path: Path) -> dict[str, object] | None:
    """`bench/build_track.py`가 트랙과 나란히 쓴 사이드카(§4.4)를 읽는다.

    **재계산하지 않는다** — 여기서 코퍼스를 다시 훑으면 트랙을 만든 실행과
    측정 실행이 서로 다른 숫자를 낼 수 있고, 그때 어느 쪽이 맞는지 알 수
    없다(팀장 지적, fix 라운드 1). 사이드카가 없으면(옛 트랙) `None`을
    돌리고 **경고를 출력한다** — 조용히 빠지면 §4.4 산출물이 리포트에서
    또 사라진다.
    """
    stats_path = track_path.with_name(f"{track_path.stem}.stats.json")
    if not stats_path.exists():
        print(
            f"경고: 코퍼스 통계 사이드카가 없다({stats_path}) — "
            "§4.4 '코퍼스 제외' 절을 리포트에서 생략한다. "
            "bench.build_track을 다시 실행하면 생긴다.",
            file=sys.stderr,
        )
        return None
    return json.loads(stats_path.read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 조립한다 (컨트롤러 판정 A).

    **`main()`에서 분리한다.** `main()`의 나머지는 트랙 파일
    (`data/bench/{pair}.clean.json`)을 실제로 읽는데 그 디렉터리는
    `.gitignore` 대상이라 CI에 존재하지 않는다 — 파서 기본값 하나를
    검사하려고 `main()`을 통째로 돌리면 그 테스트는 CI에서 영영 돌지
    않는다. 이 함수는 파서만 만들고 아무 파일도 읽지 않는다.
    """
    parser = argparse.ArgumentParser(description="벤치마크 전체 실행")
    parser.add_argument("--pair", required=True, choices=["en-ko", "ja-ko"])
    parser.add_argument("--track", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--out-dir", type=Path, default=Path("bench/results"))
    # 감사 산출물(스펙 §5.7)은 트랙과 같은 디렉터리에 둔다 — 둘은 같은
    # 가공물 계열이고 `data/`는 .gitignore라 리포를 오염시키지 않는다.
    # `--out-dir`(리포트)와 분리한 이유: 리포트는 커밋되지만 변조 트랙은
    # 코퍼스 파생물이라 CC BY-NC-ND 4.0에 걸려 커밋할 수 없다.
    parser.add_argument("--audit-dir", type=Path, default=None)

    # --- Tier 1 (FR-4.2 · 태스크7 브리프 Step 4) ---
    # 기본값이 전부 꺼짐·`None`인 것이 핵심이다 — `--tier1` 없이 부르는
    # 기존 경로는 이 인자들이 전혀 관여하지 않아 한 줄도 달라지지 않는다
    # (설계 D9 · `test_tier1_없이는_흐름이_같다`가 그 계약을 검사한다).
    # `%%`로 이스케이프한다 — argparse의 `HelpFormatter`가 help 문자열을
    # `%`-포맷팅하므로, 이스케이프하지 않은 `%`는 `--help` 호출이 아니라
    # **파서 조립 시점**(`add_argument`)에 `ValueError`를 던진다(실측:
    # `test_파서는_pair_없이_거부한다`가 이 자리에서 걸렸다).
    parser.add_argument("--tier1", action="store_true", help="Tier 1을 예산 10%%·30%%에서 측정한다")
    parser.add_argument("--base-url", default=None, help="역번역에 쓸 LLM 엔드포인트")
    parser.add_argument("--model", default=None, help="역번역에 쓸 LLM 모델")
    parser.add_argument("--embed-base-url", default=None, help="비우면 --base-url을 그대로 쓴다")
    parser.add_argument("--embed-model", default=None)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="역번역·임베딩 캐시 디렉터리 (재실행 시 재사용)",
    )
    return parser


def _build_embedder(args: argparse.Namespace) -> Embedder:
    """임베더를 만든다.

    **저장소의 `cli._build_embedder(*, base_url, model, api_key)`와
    시그니처가 다르다** — 브리프는 CLI 것과 같은 모양(`args`를 받는다)을
    가정했지만(컨트롤러 노트 §1), CLI 것은 사적 함수라 임포트 대상이
    아니므로 벤치 전용으로 `Namespace`를 받는 얇은 래퍼를 새로 둔다.

    `--embed-base-url`이 비어 있으면 `--base-url`(번역 엔드포인트)로
    폴백한다 — 임베딩은 대개 번역과 같은 로컬 백엔드에서 나오므로, 폴백이
    없으면 사용자가 같은 주소를 두 번 쳐야 한다(`cli._resolve_embed`와
    같은 이유).
    """
    base_url = args.embed_base_url or args.base_url
    return OpenAICompatibleEmbedder(
        base_url=base_url, model=args.embed_model, api_key=_resolve_embed_key()
    )


def _resolve_embed_key() -> str | None:
    """임베딩용 API 키. 없으면 번역용 키로 폴백한다 (`cli._resolve_stt_key`와 같은 모양).

    **`or`가 아니라 `if key is None`으로 폴백한다** (리뷰 지적 4). `or`는
    빈 문자열(`CUESIFT_EMBED_API_KEY=""`)도 "설정 안 함"으로 뭉뚱그려
    번역용 키로 폴백시킨다 — `--embed-base-url`이 `--base-url`과 **다른
    호스트**일 때 번역용 키가 그 호스트의 `Authorization` 헤더에 실린다.
    실측(리뷰어): `CUESIFT_API_KEY=TRANSLATE-SECRET`·`CUESIFT_EMBED_API_KEY=""`를
    두면 `or` 버전은 `'TRANSLATE-SECRET'`을 냈고, `cli._resolve_embed_key`와
    같은 이 패턴은 `None`을 낸다(빈 키를 실으면 401이 나고 그것은 Fatal이라
    "키가 틀렸다"로 오독된다 — CLI 쪽 독스트링과 같은 근거).
    """
    key = os.environ.get("CUESIFT_EMBED_API_KEY")
    if key is None:
        # 설정하지 않았을 때만 폴백한다. 같은 조직의 키를 쓰는 경우가 흔하다.
        key = os.environ.get("CUESIFT_API_KEY")
    return key or None


def _negation_classes(
    mutated: Sequence[Segment], labels: Sequence[Label], lang: str
) -> dict[str, str]:
    """negation 라벨이 붙은 세그먼트에만 잡음 분류를 매긴다 (Task 6 `classify`).

    **negation 외 라벨에는 부르지 않는다** — `classify`의 규칙은 부정 표현
    제거를 전제하므로 다른 유형(빈 값·미번역 등)에 걸면 판정이 무의미하다.
    """
    by_id = {seg.id: seg for seg in mutated}
    return {
        lb.segment_id: classify(
            by_id[lb.segment_id].source_text, by_id[lb.segment_id].target_text or "", lang
        )
        for lb in labels
        if lb.kind == "negation"
    }


def _negation_recall_scores(
    selected_ids: set[str],
    labels: Sequence[Label],
    negation_classes: Mapping[str, str],
) -> dict[str, float | int]:
    """예산 하나에서의 negation 전체 Recall과 clean 부분집합 Recall을 낸다.

    **`clean_total`을 항상 함께 낸다** — `render_tier1_comparison`이 해상도
    (1/`clean_total`)를 보여주려면 분모가 있어야 한다(브리프 Step 2).
    """
    negation_ids = {lb.segment_id for lb in labels if lb.kind == "negation"}
    clean_ids = {sid for sid, cls in negation_classes.items() if cls == CLEAN}
    return {
        "negation_recall": _recall(selected_ids, negation_ids),
        "clean_recall": _recall(selected_ids, clean_ids),
        "clean_total": len(clean_ids),
    }


def _collect_raw(
    risks: Sequence[SegmentRisk],
    mutated: Sequence[Segment],
    labels: Sequence[Label],
    negation_classes: Mapping[str, str],
    budget: float,
) -> list[dict[str, object]]:
    """Tier 1 후보가 낸 원자료를 세그먼트 단위로 남긴다 (브리프 Step 5 · 이월 20).

    **집계값만 남기면 라벨이 바뀔 때마다 전체를 다시 돌려야 한다** — 이월
    20번이 열린 이유가 그것이다(스파이크가 집계만 남겨 라벨 4건 교체에
    213회 재실행이 필요했다). `llm.backtranslation` 신호가 실린
    `SegmentRisk`만 골라 세그먼트별 레코드를 남기면, 라벨 교체는 이 파일을
    다시 훑는 것으로 끝난다.

    **`selected`가 없으면 원자료만으로 Recall@Budget을 되계산할 수 없다**
    (리뷰 지적 6) — 후보였는지는 남아도 그 예산에서 실제로 큐에 담겼는지가
    없으면, 라벨을 고친 뒤 이 파일만 다시 훑어서는 새 Recall을 낼 수 없고
    결국 재실행이 필요해져 이월 20이 다시 열린다.
    """
    by_id = {seg.id: seg for seg in mutated}
    label_by_id = {lb.segment_id: lb.kind for lb in labels}
    records: list[dict[str, object]] = []
    for risk in risks:
        # 회색지대 밖이라 Tier 1 후보가 아니었던 세그먼트, 혹은 역번역/임베딩이
        # 실패해(§5.1) 신호를 못 낸 세그먼트는 여기서 걸러진다.
        bt = next((s for s in risk.signals if s.name == BackTranslation.name), None)
        if bt is None:
            continue
        seg = by_id[risk.segment_id]
        records.append(
            {
                "segment_id": risk.segment_id,
                "source_text": seg.source_text,
                "target_text": seg.target_text,
                "back_translation": bt.detail.get("back_translation"),
                "cosine": bt.detail.get("cosine"),
                "score": bt.score,
                "label_kind": label_by_id.get(risk.segment_id),
                "negation_class": negation_classes.get(risk.segment_id),
                "budget_ratio": budget,
                "selected": risk.selected,
            }
        )
    return records


def _dump_raw(
    records: Sequence[Mapping[str, object]],
    out_dir: Path,
    pair: str,
    *,
    model: str | None,
    embed_model: str | None,
    commit: str,
) -> Path:
    """`{pair}.backtranslation.json`에 원자료를 남긴다 (브리프 Step 5).

    **`bench/results/`가 아니라 audit-dir인 이유는 `dump_audit`와 같다** —
    원문·번역문이 실려 CC BY-NC-ND 4.0에 걸려 커밋할 수 없다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{pair}.backtranslation.json"
    payload = {
        "pair": pair,
        "commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "translate_model": model,
        "embed_model": embed_model,
        "record_count": len(records),
        "records": list(records),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _make_stdout_lossy() -> None:
    """콘솔 인코딩이 좁아도 출력이 죽지 않게 한다 (2026-09-05 실측).

    **이 저장소는 한국어 출력이 규약인데 Windows 콘솔의 기본 인코딩은
    cp949다.** 리포트 문자열의 엠대시(U+2014) 하나가 `print`를
    `UnicodeEncodeError`로 죽였고, 그것이 벤치 전체를 exit 1로 끝냈다 -
    한 시간 48분의 LLM 호출 뒤였다.

    `errors="replace"`가 정답인 이유는 **콘솔 출력이 부수 효과이기 때문**이다.
    사람이 읽으라고 찍는 한 줄이 측정을 죽이면 안 되고, 물음표로 바뀐 글자
    하나는 파일에 남는 리포트에 영향이 없다.

    `reconfigure`는 파이썬 3.7+의 `TextIOWrapper` 메서드다. 리다이렉트된
    stdout에도 걸린다 - 파이프로 넘길 때가 오히려 인코딩이 좁아지는 자리다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # `encoding`은 건드리지 않는다 - 환경이 UTF-8이면 그대로 두는 것이
            # 맞고, 좁은 인코딩일 때만 대체 문자로 흘려보내면 된다.
            reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _make_stdout_lossy()
    args = build_arg_parser().parse_args(argv)

    target_lang = args.pair.split("-")[0]
    track_path = args.track or Path(f"data/bench/{args.pair}.clean.json")
    profile = load_builtin(f"ted-{target_lang}")
    glossary = load_glossary(Path("bench/glossary.ted.yaml"), target_lang)
    ctx = SignalContext(
        profile=profile, glossary=glossary, source_lang="ko", target_lang=target_lang
    )

    segments = load_track(track_path)
    mutated, labels, skipped = inject(segments, glossary, profile, rate=args.rate, seed=args.seed)

    # 스펙 §5.7 — **측정 전에** 감사 산출물을 남긴다. 측정이 불변식에서
    # 실패해도(`check_invariants`) 무엇을 주입했는지는 디스크에 남아야
    # 원인을 볼 수 있다. 뒤로 미루면 실패한 실행은 아무것도 남기지 않는다.
    commit = _commit()
    injected_path, labels_path = dump_audit(
        mutated,
        labels,
        args.audit_dir or track_path.parent,
        args.pair,
        seed=args.seed,
        commit=commit,
    )
    print(f"감사 산출물 -> {injected_path}\n              {labels_path}")

    results = measure(mutated, labels, ctx, list(BUDGETS))

    signals = collect_all(mutated, ctx)
    risks = [fuse(seg.id, signals[seg.id]) for seg in mutated]
    fp_rate = check_invariants(results, labels, mutated, risks)

    drops = ablation(mutated, labels, ctx, budget=0.10)

    error_ids = {lb.segment_id for lb in labels}
    baseline = {r.budget: random_baseline(mutated, error_ids, r.review_ratio) for r in results}

    # 유형별 무작위 기준선(negation) — Task 8 리뷰어가 발견: Tier 0는 의미
    # 반전에서 무작위보다 못하다. `error_ids`를 negation 라벨로만 좁혀 같은
    # `random_baseline`을 재사용한다(전용 함수를 새로 만들 필요가 없다).
    negation_ids = {lb.segment_id for lb in labels if lb.kind == "negation"}
    by_kind_baseline = {
        "negation": {
            r.budget: random_baseline(mutated, negation_ids, r.review_ratio) for r in results
        }
    }

    manifest = load_manifest(Path("bench/manifest.json"))
    corpus_stats = _load_corpus_stats(track_path)
    meta = RunMeta(
        pair=args.pair,
        seed=args.seed,
        manifest_sha256=manifest.get(args.pair, {}).get("sha256", "unknown"),
        commit=commit,
        sample_size=len(segments),
        injection_skipped=dict(skipped),
        injected=label_counts(labels),
        unmeasured=UNMEASURED,
        hard_fail_false_positive_rate=fp_rate,
        unmeasurable=UNMEASURABLE,
        caveats=CAVEATS,
        corpus_stats=corpus_stats,
    )
    md_path, json_path = write_report(
        meta, results, drops, baseline, args.out_dir, by_kind_baseline=by_kind_baseline
    )
    print(f"리포트 -> {md_path}\n         {json_path}")
    for r in results:
        print(
            f"  예산 {r.budget:.0%}  실제 {r.review_ratio:.1%}  "
            f"Recall {r.recall:.1%}  배수 {r.lift:.1f}x"
        )

    if args.tier1:
        # `--tier1` 없이는 위까지 한 줄도 다르지 않다(설계 D9) — 이 아래가
        # 전부다. 필수 인자는 여기서 검사한다: `OpenAICompatibleProvider`에
        # `base_url=None`을 그대로 넘기면 `_require_http_url`이 트레이스백을
        # 낸다 — 사람이 읽을 메시지로 먼저 막는다.
        missing = [
            flag
            for flag, value in (
                ("--base-url", args.base_url),
                ("--model", args.model),
                ("--embed-model", args.embed_model),
            )
            if not value
        ]
        if missing:
            print(f"오류: --tier1을 쓰려면 {', '.join(missing)}이 필요하다", file=sys.stderr)
            return 2

        embedder = _build_embedder(args)
        try:
            # 판정 D — 프로브 실패는 역번역을 한 건도 부르기 전에 끝낸다
            # (설계 D7). 뒤로 미루면 비싼 역번역을 수백 회 한 뒤에야
            # 임베딩이 안 된다는 것을 알게 된다. 벤치의 `main()`은 `int`를
            # 반환하는 규약이라(CLI의 종료 코드 2·69·70은 옮기지 않는다)
            # 예외를 여기서 사람이 읽을 메시지로 바꿔 0이 아닌 값을 낸다.
            dimensions = embedder.probe()
        except EmbeddingUnsupportedError as exc:
            # 판정 E — 501(모델이 못 한다)과 404(경로가 없다)를 합치지
            # 않는다. 이쪽은 --embed-model을 바꿔야 한다.
            print(f"오류: 임베딩 프로브 실패(501) — --embed-model을 바꿔라: {exc}", file=sys.stderr)
            embedder.close()
            return 1
        except EmbeddingNotFoundError as exc:
            # 저쪽은 --embed-base-url을 바꿔야 한다 — 취할 행동이 정반대다.
            print(
                f"오류: 임베딩 프로브 실패(404) — --embed-base-url을 바꿔라: {exc}",
                file=sys.stderr,
            )
            embedder.close()
            return 1
        except EmbeddingError as exc:
            print(f"오류: 임베딩 프로브 실패 — {type(exc).__name__}: {exc}", file=sys.stderr)
            embedder.close()
            return 1
        print(f"임베딩 준비됨 ({args.embed_model}, {dimensions}차원)")

        # 판정 C — 벤치가 직접 만든다. CLI의 `_build_provider`는 사적
        # 함수라 임포트 대상이 아니다. `identity`는 프로바이더의
        # `cache_identity`에서 얻고, 캐시를 실제로 붙일지는
        # `triage_with_tier1`의 `cache_dir`가 정한다 —
        # `tier1._provider_factory`가 `cache_dir is None`이면 그대로
        # `inner`를 돌려주므로, 여기서 `CachingProvider`로 다시 감싸면
        # 이중 래핑이 된다.
        provider = OpenAICompatibleProvider(
            base_url=args.base_url, model=args.model, api_key=os.environ.get("CUESIFT_API_KEY")
        )

        negation_classes = _negation_classes(mutated, labels, target_lang)
        raw_records: list[dict[str, object]] = []
        tier1_comparisons: list[str] = []
        try:
            # 스펙 §5.7과 같은 원칙(위 316행 "측정 전에 감사 산출물을
            # 남긴다")을 예산 루프에도 적용한다(리뷰 지적 3) — 로컬 Ollama가
            # 긴 입력에서 `ReadTimeout`을 낸 전례(`TIER1_MAX_RATIO` 주석)가
            # 있어 30% 예산 도중 죽으면, `finally` 없이는 이미 끝난 10% 예산의
            # 역번역 결과까지 통째로 사라진다 — 이월 20이 열린 것이 정확히
            # 이 실패 경로였다.
            for budget in TIER1_BUDGETS:
                tier1_risks = triage_with_tier1(
                    mutated,
                    ctx,
                    budget_ratio=budget,
                    provider=provider,
                    max_ratio=TIER1_MAX_RATIO,
                    warn=print,
                    embedder=embedder,
                    cache_dir=args.cache_dir,
                    identity=provider.cache_identity,
                )
                tier1_selected = {r.segment_id for r in tier1_risks if r.selected}
                tier1_scores = _negation_recall_scores(tier1_selected, labels, negation_classes)
                raw_records.extend(
                    _collect_raw(tier1_risks, mutated, labels, negation_classes, budget)
                )

                # Tier 0만으로의 같은 예산 — 위 `risks`(Tier 0 융합 결과, 예산
                # 미적용)에 같은 예산을 적용해 비교 기준을 낸다. 새로 수집하지
                # 않는다 — 이미 계산돼 있는 것을 재사용하지 않으면 Tier 0와
                # Tier 1이 서로 다른 신호 스냅샷을 비교하게 된다.
                tier0_selected = {
                    r.segment_id for r in select_by_budget(risks, budget) if r.selected
                }
                tier0_scores = _negation_recall_scores(tier0_selected, labels, negation_classes)

                comparison = render_tier1_comparison(
                    tier0=tier0_scores, tier1=tier1_scores, budget=budget
                )
                # **모으는 것이 먼저고 찍는 것이 나중이다.** 순서가 반대면
                # `print`의 실패가 데이터 수집을 막는다 - 2026-09-05 실행에서
                # 실제로 그랬다. cp949 콘솔이 이 문자열의 엠대시(U+2014)를
                # 인코딩하지 못해 `print`가 죽었고, 그 예산 지점의 비교표가
                # 목록에 들어가지 못해 아래 `if tier1_comparisons:`가 거짓이
                # 되면서 리포트 재작성까지 통째로 건너뛰었다. 한 시간 48분의
                # LLM 호출이 원자료로만 남았다(`finally` 덕분에 그것은 살았다).
                tier1_comparisons.append(comparison)
                print(comparison)
        finally:
            # 예산 루프가 도중에 죽어도(위 주석) 지금까지 모은 것은 남긴다.
            # `raw_records`가 비어도(첫 예산에서 죽음) 빈 목록으로라도 쓴다 —
            # "시도했으나 0건"과 "아예 안 돌았다"를 파일 존재로 구분한다.
            raw_path = _dump_raw(
                raw_records,
                args.audit_dir or track_path.parent,
                args.pair,
                model=args.model,
                embed_model=args.embed_model,
                commit=commit,
            )
            print(f"Tier 1 원자료 -> {raw_path}")

            if tier1_comparisons:
                # 리뷰 지적 1 — `render_tier1_comparison`의 출력은 집계
                # 수치만 담아 CC BY-NC-ND 제약을 받지 않는다. 콘솔 `print`만
                # 남기면 스크롤백이 닫히는 순간 사라지므로, 커밋 가능한
                # `bench/results/`의 리포트 파일에도 같은 내용을 싣는다.
                # 같은 경로(`{pair}-{date}.md`/`.json`)를 다시 써 Tier 0
                # 리포트를 Tier 1 비교로 확장한다.
                tier1_md_path, tier1_json_path = write_report(
                    meta,
                    results,
                    drops,
                    baseline,
                    args.out_dir,
                    by_kind_baseline=by_kind_baseline,
                    tier1_comparisons=tier1_comparisons,
                )
                print(f"리포트(Tier 1 포함) -> {tier1_md_path}\n              {tier1_json_path}")

            embedder.close()
            provider.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
