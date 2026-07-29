"""트랙 빌더의 §4.4 부수 산출물 테스트.

fix 라운드 1(팀장): `bench/run.py`가 코퍼스를 다시 훑어 §4.4 통계를 재계산하면
`bench/build_track.py`가 만든 트랙과 출처가 둘로 갈라진다. `build_track`이
트랙을 쓸 때 **사이드카 JSON**(`{pair}.clean.stats.json`)을 함께 쓰고, 콘솔
출력과 같은 값을 공유해야 한다 — 이 테스트가 그 일치를 검증한다.
"""

from __future__ import annotations

import json

from bench.build_track import _corpus_stats, main
from bench.corpus import FilterStats


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_corpus_stats_shape_matches_console_formula():
    """`_corpus_stats`는 `main()`의 콘솔 출력과 같은 값을 공유하는 순수 함수다.

    `build_track.py` 콘솔 공식(`100 * unfittable / max(stats.kept, 1)`)의
    분자·분모가 그대로 페이로드에 실려야, 리포트 쪽에서 같은 비율을
    재계산할 수 있다.
    """
    stats = FilterStats(total=100, kept=90, dropped={"empty": 5, "duplicate": 3, "ratio": 2})
    payload = _corpus_stats(stats, unfittable=40, feasible=50, track_size=5)

    assert payload == {
        "total_pairs": 100,
        "filtered_out": {"empty": 5, "duplicate": 3, "ratio": 2},
        "kept_after_filter": 90,
        "unfittable": 40,
        "feasible": 50,
        "track_size": 5,
    }


def test_build_track_writes_sidecar_matching_console_output(tmp_path, capsys):
    """`main()`을 실제로 돌려 사이드카 파일이 콘솔 출력과 일치하는지 확인한다.

    5쌍 중 1쌍(L5)은 원문이 규격(21자×2줄)에 물리적으로 담기지 않도록
    길게 지어 **unfittable**로 만든다. 나머지 4쌍은 짧아 전부 가용하다.
    `--size`를 가용 건수(4)보다 크게 줘 `sample()`이 전부를 고르게 한다
    (`bench/corpus.py`의 `n >= len(pairs)`면 그대로 반환하는 분기).
    """
    data_dir = tmp_path / "data"
    pair_dir = data_dir / "en-ko"
    pair_dir.mkdir(parents=True)

    # 규격(21자 × 2줄, latin_half)에 담기지 않는 긴 한국어 문장 — 공백으로
    # 나눠도 2줄(42 단위)을 넘겨 wrap_text가 None을 반환한다.
    unfittable_ko = " ".join(["가나다라마바사아자차카타파하"] * 10)

    _write(
        pair_dir / "TED2020.en-ko.ko",
        [
            "안녕하세요 만나서 반갑습니다",
            "오늘 날씨가 좋습니다",
            "감사합니다 여러분",
            "다시 만나요",
            unfittable_ko,
        ],
    )
    _write(
        pair_dir / "TED2020.en-ko.en",
        [
            "Hello, nice to meet you.",
            "The weather is nice today.",
            "Thank you everyone.",
            "See you again.",
            "This pair will not fit regardless.",
        ],
    )

    out_dir = tmp_path / "out"
    code = main(
        [
            "--pair",
            "en-ko",
            "--data-dir",
            str(data_dir),
            "--out-dir",
            str(out_dir),
            "--size",
            "10",
            "--seed",
            "1",
        ]
    )
    assert code == 0

    captured = capsys.readouterr()
    assert "규격 미충족 제외 1 (20.00%)" in captured.out

    stats_path = out_dir / "en-ko.clean.stats.json"
    assert stats_path.exists()
    payload = json.loads(stats_path.read_text(encoding="utf-8"))

    assert payload["total_pairs"] == 5
    assert payload["filtered_out"] == {"empty": 0, "duplicate": 0, "ratio": 0}
    assert payload["kept_after_filter"] == 5
    assert payload["unfittable"] == 1
    assert payload["feasible"] == 4
    assert payload["track_size"] == 4

    # 사이드카의 unfittable/kept_after_filter가 콘솔의 20.00%와 같은 값이어야
    # 두 출처가 갈라지지 않는다(팀장 지적의 핵심).
    ratio = 100 * payload["unfittable"] / payload["kept_after_filter"]
    assert f"{ratio:.2f}%" in captured.out
