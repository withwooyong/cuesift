"""벤치 용어집 코퍼스 검증기 (설계 스펙 §5.6).

`bench/glossary.ted.yaml`의 각 항목에 대해 TED2020 코퍼스에서 **등장 건수**와
**대응률**(원문에 용어가 나온 문장 중 번역문에 대응어가 하나라도 있는 비율)을
실측한다. 용어집을 고칠 때마다 다시 돌려 기준을 계속 만족하는지 확인하는
회귀 게이트다 — 돌리지 않으면 대응률이 낮은 용어가 몰래 섞여
"용어집이 틀려서 생긴 오탐"과 "검출기 성능"을 구분할 수 없게 된다.

기준 미달 항목이 있으면 표시하고 종료 코드 1을 낸다. 조용히 통과하면
용어집이 썩어도 아무도 모른다.

`cuesift`를 임포트하므로 획득 스크립트(`fetch_ted2020.py`)와 달리 표준
라이브러리 제약(스펙 §3.4)의 대상이 아니다 — 그 제약은 제품 없이 도는
스크립트에 붙은 것이다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bench.corpus import SentencePair, load_pairs

from cuesift.glossary import Glossary, load_glossary

GLOSSARY = Path("bench/glossary.ted.yaml")

# cuesift.glossary._BOUNDARY와 반드시 같은 규칙이어야 한다 — 검증기가 제품과
# 다른 기준으로 재면 "검증 통과"와 "제품이 실제로 잡는 것"이 어긋난다.
#
# `\b` 대신 라틴 문자·숫자 룩어라운드를 쓰는 이유는 CJK 때문이다. 파이썬
# 정규식에서 CJK 문자는 전부 `\w`라 `\b気候変動\b`는 「これは気候変動です」에서
# 매칭되지 않는다 — 조사가 붙어도 `\w\w` 경계라 단어 경계가 서지 않는다.
# 이 프로젝트에서 실제로 `\b` 수정안이 CJK를 전부 깨뜨려 폐기된 바 있다.
_BOUNDARY = r"(?<![a-zA-Z0-9]){}(?![a-zA-Z0-9])"


def _contains_term(text: str, term: str) -> bool:
    """`text`에 `term`이 cuesift.glossary와 같은 경계 규칙으로 등장하는지."""
    return re.search(_BOUNDARY.format(re.escape(term)), text, re.IGNORECASE) is not None


def measure(pairs: list[SentencePair], glossary: Glossary) -> list[tuple[str, int, float]]:
    """항목별 `(용어, 원문 등장 건수, 대응률%)`.

    대응률의 분모는 **원문에 용어가 등장한 건수**다. 코퍼스 전체가 아니라
    "검사 대상이 된 문장 중 몇 %가 통과했는가"라야 낮은 등장 빈도의 용어가
    우연히 100% 대응률로 보이는 착시를 안 만든다.
    """
    results = []
    for entry in glossary.entries:
        hits = 0
        matched = 0
        for pair in pairs:
            if _contains_term(pair.source, entry.source):
                hits += 1
                if any(_contains_term(pair.target, t) for t in entry.targets):
                    matched += 1
        rate = 100.0 * matched / hits if hits else 0.0
        results.append((entry.source, hits, rate))
    return results


def _corpus_paths(data_dir: Path, pair: str, target_lang: str) -> tuple[Path, Path]:
    src = data_dir / pair
    return src / f"TED2020.{pair}.ko", src / f"TED2020.{pair}.{target_lang}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="벤치 용어집 코퍼스 검증")
    parser.add_argument(
        "--pair",
        choices=["en-ko", "ja-ko"],
        action="append",
        help="검증할 언어쌍. 반복 지정 가능. 기본값은 en-ko·ja-ko 둘 다.",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/ted2020"))
    parser.add_argument("--min-hits", type=int, default=20)
    parser.add_argument("--min-rate", type=float, default=79.8)
    args = parser.parse_args(argv)

    pairs_to_check = args.pair or ["en-ko", "ja-ko"]

    all_ok = True
    for pair in pairs_to_check:
        target_lang = pair.split("-")[0]
        ko_path, other_path = _corpus_paths(args.data_dir, pair, target_lang)
        sentence_pairs = load_pairs(ko_path, other_path)
        glossary = load_glossary(GLOSSARY, target_lang)

        print(f"\n# {pair} (코퍼스 {len(sentence_pairs):,}쌍)")
        print(f"{'ko 용어':<12} {'등장':>8} {'대응률':>8}  판정")
        for term, hits, rate in measure(sentence_pairs, glossary):
            ok = hits >= args.min_hits and rate >= args.min_rate
            all_ok = all_ok and ok
            verdict = "PASS" if ok else "FAIL"
            print(f"{term:<12} {hits:>8,} {rate:>7.1f}%  {verdict}")

    if not all_ok:
        print(f"\n기준 미달 항목이 있다 (등장 >= {args.min_hits}, 대응률 >= {args.min_rate}%).")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
