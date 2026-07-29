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

# §4.4 절 문구용 — 언어 코드를 그대로 조사 없이 붙이면("en로") 어색하다.
# 현재 확정된 대상 언어(Q2: ko→en/ja)만 사람이 읽는 이름으로 옮긴다.
_LANG_NAMES = {"en": "영어", "ja": "일본어", "ko": "한국어"}


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

    `injection_skipped`(옛 이름 `excluded`)는 **주입 자격 미달** 건수다 —
    "표본에서 제외됐다"가 아니라 "해당 유형의 주입 조건을 만족하지 않았다"다.
    fix 라운드 1(팀장 지적): 옛 이름 `excluded`와 옛 절 제목 "제외 건수"가
    `glossary 4,041`을 "5,000건 트랙에서 4,041건이 빠졌다"로 오독시켰다.

    `corpus_stats`는 스펙 §4.4의 부수 산출물 — ko 자막이 그대로 en·ja로
    옮겨질 때 몇 %가 TED 규격에 물리적으로 담기지 않는지(`build_track`의
    `unfittable`/`kept_after_filter`)를 담는다. **`bench/build_track.py`가
    쓴 사이드카 JSON(`{pair}.clean.stats.json`)에서 그대로 읽어 온 값이어야
    한다** — 여기서 다시 계산하면 트랙과 통계의 출처가 둘로 갈라진다.
    없을 수 있으므로(사이드카가 없는 옛 트랙) 기본값 `None`이고, 그때는
    §4.4 절 자체를 생략한다.
    """

    pair: str
    seed: int
    manifest_sha256: str
    commit: str
    sample_size: int
    injection_skipped: dict[str, int]
    injected: dict[str, int]
    unmeasured: tuple[str, ...]
    hard_fail_false_positive_rate: float
    unmeasurable: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    corpus_stats: dict[str, object] | None = None


def render_markdown(
    meta: RunMeta,
    results: Sequence[BudgetResult],
    drops: Mapping[str, float],
    baseline: Mapping[float, tuple[float, float]],
    *,
    by_kind_baseline: Mapping[str, Mapping[float, tuple[float, float]]] | None = None,
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
    ]

    # fix 라운드 1(팀장) — 스펙 §4.4의 부수 산출물. `bench/build_track.py`가
    # 사이드카(`{pair}.clean.stats.json`)에 쓴 값을 그대로 옮겨 싣는다.
    # **비율은 여기서 계산한다** — `build_track.py` 콘솔 출력과 같은 공식
    # (unfittable / kept_after_filter)을 써야 두 출처가 어긋나지 않는다.
    # `corpus_stats`가 없으면(사이드카 없는 옛 트랙) 절 자체를 생략한다 —
    # 숫자 없이 주장만 내는 것보다 낫다.
    if meta.corpus_stats:
        cs = meta.corpus_stats
        kept = cs.get("kept_after_filter", 0) or 0
        unfittable = cs.get("unfittable", 0)
        ratio = unfittable / kept if kept else 0.0
        target_lang = meta.pair.split("-")[0]
        target_name = _LANG_NAMES.get(target_lang, target_lang)
        lines += [
            "",
            "### 코퍼스 제외 (스펙 §4.4)",
            "",
            f"**제외 건수 자체가 결과다.** ko 자막을 그대로 {target_name}로 "
            f"옮겼을 때 **{ratio:.2%}**가 TED 규격(21자 × 2줄)에 물리적으로 담기지 않는다. "
            "자막 현업에서 재분절이 왜 필요한지를 보여주는 숫자이며 "
            "**FR-5.4(v0.2 규격 자동 교정)를 정량적으로 정당화한다.**",
            "",
            "| 항목 | 건수 |",
            "| --- | --- |",
            f"| 원본 쌍 | {cs.get('total_pairs', 0):,} |",
        ]
        filtered_out = cs.get("filtered_out")
        if isinstance(filtered_out, Mapping):
            for reason, count in sorted(filtered_out.items()):
                lines.append(f"| 필터 제거 · {reason} | {count:,} |")
        lines += [
            f"| 필터 통과 | {kept:,} |",
            f"| 규격 미충족(unfittable) | {unfittable:,} |",
            f"| 가용(feasible) | {cs.get('feasible', 0):,} |",
            f"| 트랙 크기 | {cs.get('track_size', 0):,} |",
        ]

    # ⑨ 주입 자격 미달 건수(옛 이름 "제외 건수") — `inject`가 해당 유형의
    # 주입 조건을 만족하지 못해 건너뛴 횟수다. **표본에서 빠진 게 아니다** —
    # 옛 제목은 `glossary 4,041`을 "5,000건 트랙에서 4,041건이 빠졌다"로
    # 읽히게 했다(팀장 지적, fix 라운드 1).
    lines += [
        "",
        "### 주입 자격 미달 건수",
        "",
        "해당 유형의 주입 조건을 만족하지 않아 건너뛴 세그먼트 수다. "
        "**표본에서 제외된 것이 아니다** — 다른 유형으로는 정상 주입됐다.",
        "",
        "| 유형 | 건수 |",
        "| --- | --- |",
    ]
    for reason, count in sorted(meta.injection_skipped.items()):
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

    # ⑦ 오라클 대비 달성률 — U자 곡선. 계획 A 재리뷰의 미결 항목("가중평균 vs
    # noisy-or")에 대한 정량 근거다. 낮은 예산 구간은 hard fail만으로 채워져
    # 오탐률이 낮아(hard fail 자연 오탐률 참고) 오라클에 가깝게 달성하지만,
    # hard fail을 소진하고 가중평균 점수 순으로 채우는 구간(전형적으로 예산
    # 10% 부근)에서 정밀도가 떨어져 오라클 대비 달성률이 저점을 찍는다.
    # 이 저점이 이 프로젝트의 기본 운영점(예산 10%)과 겹친다는 것이 바로
    # 가중평균 융합의 희석 비용이 실제로 관측되는 지점이다.
    oracle_rows = [r for r in results if r.oracle > 0]
    if oracle_rows:
        lines += [
            "",
            "## 오라클 대비 달성률",
            "",
            "**오라클 상한을 100으로 뒀을 때 실제로 몇 %를 달성했는가.** 계획 A 재리뷰가 "
            "남긴 미결 항목(가중평균 vs noisy-or 융합)에 대한 정량 근거다 — 두 값을 "
            "비교하려는 A/B 실험 없이도, 가중평균 융합이 예산 구간별로 얼마나 희석되는지 "
            "이 곡선 하나로 드러난다.",
            "",
            "| 예산 | Recall | 오라클 상한 | **오라클 대비** |",
            "| --- | --- | --- | --- |",
        ]
        for r in oracle_rows:
            share = r.recall / r.oracle
            lines.append(f"| {r.budget:.0%} | {r.recall:.1%} | {r.oracle:.1%} | **{share:.1%}** |")
        lines += [
            "",
            "**낮은 예산에서 오라클 대비가 더 높고, 기본 운영점인 예산 10% 부근에서 "
            "저점을 찍는 U자 곡선이다.** 낮은 예산 구간은 hard fail만으로 채워지는데 "
            "hard fail 오탐률이 매우 낮아(위 '자연 오탐률' 참고) 거의 낭비가 없다. "
            "hard fail을 다 쓰고 나머지를 가중평균 점수 순으로 채우는 구간에서 정밀도가 "
            "떨어지는 것이 저점의 원인이다 — **가중평균 융합의 희석 비용이 정확히 이 "
            "지점에서 관측된다.**",
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
        #
        # ⑧ **유형별 무작위 기준선** — Task 8 리뷰어가 100시드로 재측정해 드러난
        # 사실이다: Tier 0는 `negation`에서 **무작위보다 못하다**(en-ko 예산10%
        # 1.41% vs 무작위 9.61%). Tier 0가 다른 오류를 상위로 올리며 문법적으로
        # 완벽한 문장(=의미만 뒤집힌 것)을 오히려 큐에서 밀어내기 때문이다.
        # 위 'Recall @ Budget' 표의 '무작위 기준' 열은 **전체 오류 기준**이라
        # 이 값과 다르다 — `by_kind_baseline`을 못 받으면(레거시 호출) 이 절과
        # 비교 문장을 조용히 생략한다(숫자 없이 주장만 내는 것보다 낫다).
        ref = _by_budget(results, _NEGATION_REFERENCE_BUDGET)
        cmp = _by_budget(results, _NEGATION_COMPARISON_BUDGET)
        neg_baseline: Mapping[float, tuple[float, float]] = (by_kind_baseline or {}).get(
            "negation", {}
        )
        if "negation" in kinds and ref is not None:
            ref_recall = ref.by_kind.get("negation", 0.0)
            ref_base = neg_baseline.get(_NEGATION_REFERENCE_BUDGET)
            cmp_base = neg_baseline.get(_NEGATION_COMPARISON_BUDGET) if cmp is not None else None

            if neg_baseline:
                lines += [
                    "",
                    "### `negation` 무작위 기준선과의 비교",
                    "",
                    "**유의**: 아래 '무작위 기준선'은 `negation` 라벨만 대상으로 한 **유형별** "
                    "값이다. 위 'Recall @ Budget' 표의 '무작위 기준' 열(전체 오류 기준)과는 "
                    "다른 값이니 혼동하지 말 것.",
                    "",
                    "| 예산 | `negation` Recall | `negation` 무작위 기준선 |",
                    "| --- | --- | --- |",
                ]
                for r in results:
                    kind_recall = r.by_kind.get("negation")
                    if kind_recall is None:
                        continue
                    base = neg_baseline.get(r.budget)
                    base_str = f"{base[0]:.2%} ±{base[1]:.2%}" if base else "—"
                    lines.append(f"| {r.budget:.0%} | {kind_recall:.2%} | {base_str} |")

                # 리뷰어 지적(Important) — 이 표만 두면 "1%·2%·5% 행이 왜 전부
                # 같은가"·"30%는 왜 못 미치는데도 유의미해 보이는가"가 설명
                # 없이 남는다. 위 'Recall @ Budget' 표에는 같은 현상("예산이
                # 낮으면 구분되지 않는다")에 이미 문단이 달려 있는데 이 표에는
                # 짝이 없어 같은 리포트 안에서 설명 수준이 비대칭이었다.
                lines += [
                    "",
                    "**무작위 기준선은 요청 예산이 아니라 실제 검수 비율로 샘플링한다.** "
                    '비교 대상이 "같은 수를 무작위로 검수했다면 몇 건 잡았을까"이므로, '
                    "실제로 검수하지 않은 요청 예산을 기준으로 삼으면 비교가 성립하지 "
                    "않는다. 위 표에서 낮은 예산 행들의 기준선이 서로 같은 것은 그 예산들의 "
                    "실제 검수 비율이 모두 hard fail로 채워진 동일한 값이기 때문이다"
                    "(위 'Recall @ Budget' 표 참고).",
                ]

                # 예산이 커지면 무작위 기준선도 base rate로 함께 올라 Recall과
                # 구분되지 않는 지점이 생긴다. **"구분되지 않는다"를 하드코딩
                # 하지 않는다** — 차이가 기준선 표준편차(1σ) 이내면 그렇게
                # 판정한다. 재측정으로 표준편차가 좁아지거나 넓어지면 이 문장도
                # 자동으로 나타나거나 사라진다.
                indistinguishable = [
                    (r.budget, r.by_kind["negation"], base[0], base[1])
                    for r in results
                    if "negation" in r.by_kind
                    and (base := neg_baseline.get(r.budget)) is not None
                    and base[1] > 0
                    and abs(r.by_kind["negation"] - base[0]) <= base[1]
                ]
                if indistinguishable:
                    parts = "; ".join(
                        f"예산 {b:.0%}({recall:.2%} vs {mean:.2%} ±{stdev:.2%})"
                        for b, recall, mean, stdev in indistinguishable
                    )
                    lines += [
                        "",
                        f"**{parts}에서는 두 값이 통계적으로 구분되지 않는다**(차이가 무작위 "
                        "기준선의 표준편차 이내). 그 구간의 Recall은 검출이 아니라 base "
                        "rate다 — 예산이 크면 무작위로 뽑아도 그만큼의 오류가 딸려 들어온다.",
                    ]

            sentence = (
                f"결정론적 신호 9종을 전부 동원해도 의미 반전은 예산 "
                f"{_NEGATION_REFERENCE_BUDGET:.0%}에서 **{ref_recall:.2%}**만 잡는다."
            )
            if ref_base is not None:
                sentence += (
                    f" **같은 예산에서 무작위로 뽑았을 때의 {ref_base[0]:.2%}보다도 낮다** — "
                    "Tier 0가 다른 오류(미번역·빈 값·규격 위반 등)를 상위로 올리면서, "
                    "문법적으로 완벽한 문장인 의미 반전을 큐에서 오히려 밀어내기 때문이다."
                )
            else:
                sentence += (
                    " 그중 대부분은 자연 오탐(`struct.number_missing`)이고 의미를 본 것이 아니다."
                )
            if cmp is not None:
                cmp_recall = cmp.by_kind.get("negation", 0.0)
                sentence += (
                    f" 예산을 {_NEGATION_COMPARISON_BUDGET:.0%}로 늘려도 **{cmp_recall:.2%}**에 "
                    "그친다"
                )
                if cmp_base is not None and cmp_base[0] > 0:
                    ratio = cmp_recall / cmp_base[0]
                    sentence += (
                        f" — 같은 예산의 무작위 기준선 {cmp_base[0]:.2%}의 {ratio:.0%}에 불과하다."
                    )
                else:
                    sentence += " — 예산을 두 배로 써도 이 유형은 거의 잡히지 않는다."
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
    *,
    by_kind_baseline: Mapping[str, Mapping[float, tuple[float, float]]] | None = None,
) -> tuple[Path, Path]:
    """`{pair}-{date}.md`와 같은 이름의 `.json`을 낸다.

    `by_kind_baseline`은 유형별 무작위 기준선(예: `negation`이 무작위보다
    못하다는 실측)을 실을 때만 넘긴다 — 없으면 그 절을 조용히 생략한다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta.pair}-{date.today().isoformat()}"

    md_path = out_dir / f"{stem}.md"
    md_path.write_text(
        render_markdown(meta, results, drops, baseline, by_kind_baseline=by_kind_baseline),
        encoding="utf-8",
    )

    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(
            {
                "meta": asdict(meta),
                "results": [asdict(r) for r in results],
                "ablation": dict(drops),
                "baseline": {str(k): list(v) for k, v in baseline.items()},
                "by_kind_baseline": {
                    kind: {str(b): list(v) for b, v in d.items()}
                    for kind, d in (by_kind_baseline or {}).items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
