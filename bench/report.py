"""⑤ report — 결과 리포트 (설계 스펙 §7).

**재현 정보를 헤더에 박는다.** `Recall@10% = 0.62`만 적힌 리포트는 몇 달 뒤
자기 자신조차 검증할 수 없다. 재현 불가능한 벤치마크 숫자는 없는 것보다
나쁘다 — 인용되기 때문이다.

**정직한 한계도 같은 원칙으로 박는다.** 좋은 숫자만 실린 리포트는 몇 달 뒤
독자가 그 숫자의 조건을 재구성할 수 없다는 점에서 재현 불가능한 리포트와
같다. `RunMeta`의 `hard_fail_false_positive_rate`·`unmeasurable`·`caveats`가
그 조건을 담는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from bench.measure import BudgetResult

# ⑥ negation 실측값을 읽는 기준 예산. bench/measure.py의 `ablation()` 기본값,
# Task 7 리포트의 "유형별 Recall (예산 10%)" 절과 맞춘다.
#
# **이 값이 `max(budget별 recall)`이면 안 된다.** 실측(en-ko, budget=30%)에서
# negation Recall이 29.6%까지 올라가는데, 이는 검출이 아니라 예산 자체가 30%라
# 코퍼스의 30%를 뽑으면 negation 라벨도 base rate로 그만큼 딸려 들어오기
# 때문이다(무작위 기준선도 같은 예산에서 ~30%를 낸다). 이걸 대신 쓰면
# "Tier 0로도 29.6%나 잡는다"로 정반대로 읽혀 스펙 §5.4의 논지가 뒤집힌다.
_NEGATION_REFERENCE_BUDGET = 0.10

# ⑥ "예산을 늘려도 소용없다"를 보여줄 비교 예산. 30%가 아니라 20%인 이유는
# 위와 같다 — 30%는 이미 base rate 혼입 구간이라 비교 자체가 무의미해진다.
# 20%는 여전히 review_ratio(20%)보다 뚜렷이 낮은 값을 내 신호가 실제로 얼마간의
# 판별력을 갖되 크게 부족하다는 것을 보여준다(Task 7 리뷰어 재측정:
# en-ko 9.86%, ja-ko 11.27% — 둘 다 20%에 한참 못 미친다).
_NEGATION_COMPARISON_BUDGET = 0.20


def _by_budget(results: Sequence[BudgetResult], budget: float) -> BudgetResult | None:
    """부동소수 오차를 허용해 예산으로 `BudgetResult`를 찾는다.

    `results`가 항상 `_NEGATION_REFERENCE_BUDGET`·`_NEGATION_COMPARISON_BUDGET`을
    포함한다고 가정하지 않는다 — 호출자가 다른 예산 스윕을 넘기면 조용히
    `None`을 반환해 ⑥ 문단을 생략한다(숫자 없이 문구만 내는 것보다 낫다).
    """
    return next((r for r in results if abs(r.budget - budget) < 1e-9), None)


@dataclass(frozen=True, slots=True)
class RunMeta:
    """이 숫자를 재현하는 데 필요한 전부.

    `hard_fail_false_positive_rate`는 필수다 — `check_invariants`가 항상
    반환하는 값이라 누락할 이유가 없고, 빠지면 "hard fail ≈ 0" 목표(요구사항
    정의서 §9.1)가 리포트만 봐서는 실측인지 주장인지 구분되지 않는다.

    `unmeasurable`·`caveats`는 이번 실행에 해당 사항이 없을 수 있어 기본값
    빈 튜플을 둔다 — `unmeasured`(FR-3.5, 일부러 뺀 신호)와 달리 이쪽은
    "데이터가 답할 수 없다"·"측정 조건에 편향이 있다"는 사유다.
    """

    pair: str
    seed: int
    manifest_sha256: str
    commit: str
    sample_size: int
    excluded: dict[str, int]
    injected: dict[str, int]
    unmeasured: tuple[str, ...]
    hard_fail_false_positive_rate: float
    unmeasurable: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


def render_markdown(
    meta: RunMeta,
    results: Sequence[BudgetResult],
    drops: Mapping[str, float],
    baseline: Mapping[float, tuple[float, float]],
) -> str:
    lines: list[str] = [
        f"# 벤치마크 결과 — {meta.pair}",
        "",
        f"> 측정일: {date.today().isoformat()}",
        "",
        "## 재현 정보",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 언어쌍 | `{meta.pair}` |",
        f"| 시드 | `{meta.seed}` |",
        f"| 코퍼스 SHA-256 | `{meta.manifest_sha256[:16]}…` |",
        f"| 코드 커밋 | `{meta.commit}` |",
        f"| 표본 수 | {meta.sample_size:,} |",
        "",
        "**가중치 미튜닝** — 스펙 §6.3에 따라 균등 가중으로 첫 숫자를 낸다. "
        "같은 데이터에서 맞춘 가중치는 새 데이터에서 재현되지 않는다.",
        "",
        "### 제외 건수",
        "",
        "| 사유 | 건수 |",
        "| --- | --- |",
    ]
    for reason, count in sorted(meta.excluded.items()):
        lines.append(f"| {reason} | {count:,} |")

    lines += ["", "### 유형별 실주입 건수", "", "| 유형 | 건수 |", "| --- | --- |"]
    for kind, count in sorted(meta.injected.items()):
        lines.append(f"| `{kind}` | {count:,} |")

    # ① hard fail 자연 오탐률 — 요구사항정의서 §9.1이 목표로 적은
    # "hard-fail 오탐 ≈ 0"의 실측값(스펙 §6.4 불변식 4가 반환한 값 그대로).
    # 설명이 없으면 독자가 "검출기가 오작동한다"로 오독한다 — 실은 한국어
    # 만·억 체계와 영어 billion 표기의 차이, 영어 자막이 숫자를 단어로 푸는
    # 관행 때문이다.
    lines += [
        "",
        "### hard fail 자연 오탐률",
        "",
        f"**hard fail 자연 오탐률: {meta.hard_fail_false_positive_rate:.2%}** — 주입하지 않은 "
        "세그먼트가 hard fail을 받은 비율이다. 검출기 버그가 아니라 한국어 만·억 체계와 영어 "
        "billion 표기의 차이, 그리고 영어 자막이 숫자를 단어로 푸는 표준 관행 때문이다"
        "(`500억 달러` → `50 billion`, `30억개` → `three billion`). 요구사항정의서 §9.1이 "
        '목표로 적은 "hard-fail 오탐 ≈ 0"의 실측값이며, 스펙 §6.4의 불변식 4는 이 값이 2%를 '
        "넘으면 결과를 내지 않고 실패한다.",
    ]

    if meta.unmeasured:
        lines += [
            "",
            "### 미측정 신호",
            "",
            "TED2020은 평문이라 마크업이 없다. 태그를 인위적으로 심으면 주입과 검출이 "
            "같은 가정을 공유해 측정이 자기 충족적이 된다(스펙 §5.3). "
            "**검출기는 구현돼 있으나 이번 측정에서 빠졌다 — '검출 실패'가 아니다.**",
            "",
        ]
        for name in meta.unmeasured:
            lines.append(f"- `{name}`")

    # ③ 판정 불가 신호 — FR-3.5(unmeasured, 일부러 뺐다)와 사유가 다르다.
    # 현재는 spec.overlap 하나뿐이다: 합성 트랙은 겹침이 0건이고, 주입기의
    # spec 유형은 duration을 줄이므로 겹침을 만들지 않는다. 그래서 이 신호는
    # 이 벤치 데이터로는 원리상 발화하지 않는다 — "쓸모없다"가 아니라
    # "측정하지 못했다"이다.
    if meta.unmeasurable:
        lines += [
            "",
            "### 판정 불가 신호",
            "",
            "아래 신호는 이 벤치의 데이터로 성능을 잴 수 없다. `spec.overlap`: 합성 트랙은 "
            "겹침이 0건이고 주입기의 규격 오류는 duration을 줄이므로 겹침을 만들지 않는다. "
            "따라서 기여도 `+0.0%`는 '쓸모없다'가 아니라 **'측정하지 못했다'**이다. 겹침이 "
            "있는 실제 자막 트랙이 있어야 판정할 수 있다. `unmeasured`(FR-3.5, 일부러 뺀 신호)와는 "
            "다른 범주다.",
            "",
        ]
        for name in meta.unmeasurable:
            lines.append(f"- `{name}`")

    lines += [
        "",
        "## Recall @ Budget",
        "",
        "**배수는 요청 예산이 아니라 실제 검수 비율로 나눈다.** hard fail이 예산을 "
        "우회하므로 둘은 다르고, 요청 예산으로 나누면 숫자가 부풀려진다(스펙 §6.2).",
        "",
        "| 요청 예산 | 실제 검수 | Recall | 무작위 기준 | 오라클 상한 | **배수** |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        mean, stdev = baseline.get(r.budget, (r.review_ratio, 0.0))
        lines.append(
            f"| {r.budget:.0%} | {r.review_ratio:.1%} | **{r.recall:.1%}** | "
            f"{mean:.1%} ±{stdev:.1%} | {r.oracle:.1%} | **{r.lift:.1f}x** |"
        )

    # ② 예산 하한 — hard fail이 가중합을 우회해 무조건 큐에 들어가므로
    # (FR-6.2), 요청 예산이 실제 검수 비율보다 낮은 구간은 여러 예산 행이
    # 같은 실제 비율·Recall로 뭉친다. 표만 보면 버그로 오독하기 쉬워 표
    # 바로 아래에 설명을 못박는다.
    lines += [
        "",
        "**요청 예산이 실제 검수 비율보다 낮으면 구분되지 않는다.** hard fail은 가중합을 "
        "우회해 무조건 큐에 들어가므로(FR-6.2), 위 표에서 예산이 낮은 구간은 같은 행 값을 "
        "낼 수 있다. 버그가 아니라 스펙 §6.2가 예측한 결과다. **배수는 요청 예산이 아니라 "
        "실제 검수 비율로 나눈 값이므로 이 구간에서도 정직하다.**",
    ]

    kinds = sorted({k for r in results for k in r.by_kind})
    if kinds:
        lines += [
            "",
            "## 유형별 Recall",
            "",
            "| 예산 | " + " | ".join(f"`{k}`" for k in kinds) + " |",
        ]
        lines.append("| --- |" + " --- |" * len(kinds))
        for r in results:
            cells = " | ".join(f"{r.by_kind.get(k, 0.0):.1%}" for k in kinds)
            lines.append(f"| {r.budget:.0%} | {cells} |")
        lines += [
            "",
            "`negation`에 검출 담당이 없는 것은 오류가 아니다. 부정어 하나가 뒤집힌 문장은 "
            "결정론적 코드로 원리상 구분되지 않는다. **이 유형의 Recall이 0에 수렴하는 것이 "
            "Tier 1·QE 투자를 정당화하는 근거 숫자**다(스펙 §5.4).",
        ]
        # ⑥ negation의 실측값 — 정성적 설명("0에 수렴")만으로는 검증할 수 없고,
        # 예산을 명시하지 않은 채 숫자만 적으면 위 표(예산별로 다른 값)와
        # 어긋나 오독한다(정정 — 팀장이 "1.41%"를 예산 무관 상수로 오인될
        # 문구로 지적). 기준 예산(10%)과 비교 예산(20%) 둘 다 `results`에서
        # 직접 뽑는다 — 상수로 박으면 재측정 때마다 조용히 거짓이 된다.
        ref = _by_budget(results, _NEGATION_REFERENCE_BUDGET)
        cmp = _by_budget(results, _NEGATION_COMPARISON_BUDGET)
        if "negation" in kinds and ref is not None:
            ref_recall = ref.by_kind.get("negation", 0.0)
            sentence = (
                f"결정론적 신호 9종을 전부 동원해도 의미 반전은 예산 "
                f"{_NEGATION_REFERENCE_BUDGET:.0%}에서 **{ref_recall:.2%}**만 잡는다. "
                "그중 대부분은 자연 오탐(`struct.number_missing`)이고 의미를 본 것이 아니다."
            )
            if cmp is not None:
                cmp_recall = cmp.by_kind.get("negation", 0.0)
                sentence += (
                    f" 예산을 {_NEGATION_COMPARISON_BUDGET:.0%}로 늘려도 **{cmp_recall:.2%}**에 "
                    "그친다 — 예산을 두 배로 써도 이 유형은 거의 잡히지 않는다."
                )
            sentence += " **이 숫자가 Tier 1·QE 투자를 정당화하는 근거다**(스펙 §5.4)."
            lines += ["", sentence]

    # ④·⑤ 측정 방법의 한계 — negation 표본 편향, 용어집 대응률의 대가 등
    # 호출자가 실측한 방법론적 주의사항. 리포트는 내용을 하드코딩하지 않고
    # 그대로 옮긴다 — 사실 자체는 주입기·용어집(Task 6·5)의 소관이다.
    if meta.caveats:
        lines += ["", "## 측정 방법의 한계", ""]
        for note in meta.caveats:
            lines.append(f"- {note}")

    lines += ["", "## 신호별 기여도 (ablation)", "", "| 신호 | Recall 하락폭 |", "| --- | --- |"]
    for name, drop in sorted(drops.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{name}` | {drop:+.1%} |")

    lines.append("")
    return "\n".join(lines)


def write_report(
    meta: RunMeta,
    results: Sequence[BudgetResult],
    drops: Mapping[str, float],
    baseline: Mapping[float, tuple[float, float]],
    out_dir: Path,
) -> tuple[Path, Path]:
    """`{pair}-{date}.md`와 같은 이름의 `.json`을 낸다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta.pair}-{date.today().isoformat()}"

    md_path = out_dir / f"{stem}.md"
    md_path.write_text(render_markdown(meta, results, drops, baseline), encoding="utf-8")

    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(
            {
                "meta": asdict(meta),
                "results": [asdict(r) for r in results],
                "ablation": dict(drops),
                "baseline": {str(k): list(v) for k, v in baseline.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
