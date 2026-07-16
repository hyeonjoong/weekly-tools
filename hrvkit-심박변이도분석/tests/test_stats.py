"""짝지은 코호트 통계 — Wilcoxon 부호순위, paired_summary, --paired 모드."""

import math
import os
import random

import pytest

from hrvkit import analyze_rr, cli
from hrvkit.stats import normal_cdf, paired_summary, wilcoxon_signed_rank
from hrvkit.report import paired_group, render_paired_group

try:
    import scipy.stats as _sps
    HAVE_SCIPY = True
except Exception:  # pragma: no cover
    HAVE_SCIPY = False


def test_normal_cdf_known_values():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert normal_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_wilcoxon_all_positive_min_pvalue():
    # 모두 같은 방향(양수) → 두 방향 최소 p. n=8 → p≈0.0143
    w = wilcoxon_signed_rank([1, 2, 3, 4, 5, 6, 7, 8])
    assert w["n_pairs"] == 8
    assert w["p_value"] == pytest.approx(0.0143, abs=0.002)


def test_wilcoxon_zeros_excluded():
    w = wilcoxon_signed_rank([0, 0, 1, 2, 3])
    assert w["n_pairs"] == 3


def test_wilcoxon_all_zero_p_one():
    w = wilcoxon_signed_rank([0, 0, 0])
    assert w["p_value"] == 1.0
    assert w["z"] == 0.0


def test_wilcoxon_symmetric_sign():
    # 부호를 뒤집어도 양측 p는 동일
    d = [3.0, -1.0, 2.0, 5.0, -2.0, 4.0, 1.5, -0.5, 6.0, 2.5]
    a = wilcoxon_signed_rank(d)["p_value"]
    b = wilcoxon_signed_rank([-x for x in d])["p_value"]
    assert a == pytest.approx(b, rel=1e-12)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_wilcoxon_matches_scipy_normal_approx():
    rng = random.Random(3)
    for _ in range(6):
        d = [round(rng.gauss(1.5, 4), 2) for _ in range(25)]
        mine = wilcoxon_signed_rank(d)
        ref = _sps.wilcoxon(d, correction=True, method="approx",
                            zero_method="wilcox")
        assert mine["p_value"] == pytest.approx(float(ref.pvalue), abs=1e-6)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_wilcoxon_matches_scipy_with_ties():
    d = [0, 0, 1, 1, 1, -1, -2, 3, 3, -3, 5, 2, 2, -2]
    mine = wilcoxon_signed_rank(d)
    ref = _sps.wilcoxon(d, correction=True, method="approx",
                        zero_method="wilcox")
    assert mine["p_value"] == pytest.approx(float(ref.pvalue), abs=1e-6)


def test_paired_summary_basic():
    base = [10, 12, 11, 13, 9]
    interv = [14, 15, 13, 18, 12]
    s = paired_summary(base, interv)
    assert s["n"] == 5
    assert s["mean_diff"] == pytest.approx(3.4)
    assert s["n_increased"] == 5
    assert s["cohens_dz"] == pytest.approx(s["mean_diff"] / s["sd_diff"])


def test_paired_summary_ignores_nonfinite():
    s = paired_summary([1, float("nan"), 3], [2, 5, 4])
    assert s["n"] == 2   # NaN 짝 제외


def test_paired_summary_empty():
    assert paired_summary([], [])["n"] == 0


def _mk_pair(seed):
    rng = random.Random(seed)
    base = [800 + rng.gauss(0, 15) for _ in range(200)]
    interv = [880 + rng.gauss(0, 35) for _ in range(200)]
    return (analyze_rr(base, source=f"b{seed}.csv"),
            analyze_rr(interv, source=f"i{seed}.csv"))


def test_paired_group_and_render():
    pairs = [_mk_pair(s) for s in range(6)]
    g = paired_group(pairs)
    assert g["_meta"]["n_subjects"] == 6
    assert g["rmssd"]["n"] == 6
    assert g["rmssd"]["mean_diff"] > 0     # 개입에서 RMSSD 증가
    out = render_paired_group(pairs)
    assert "짝지은 코호트" in out
    assert "Wilcoxon" in out
    assert "RMSSD" in out


def test_cli_paired_mode(capsys, tmp_path):
    rng = random.Random(11)
    rows = ["baseline,intervention,subject"]
    for s in range(6):
        base = [800 + rng.gauss(0, 15) for _ in range(200)]
        interv = [880 + rng.gauss(0, 35) for _ in range(200)]
        bp = tmp_path / f"b{s}.csv"
        ip = tmp_path / f"i{s}.csv"
        bp.write_text("rr_ms\n" + "\n".join(f"{x:.1f}" for x in base) + "\n",
                      encoding="utf-8")
        ip.write_text("rr_ms\n" + "\n".join(f"{x:.1f}" for x in interv) + "\n",
                      encoding="utf-8")
        rows.append(f"b{s}.csv,i{s}.csv,S{s}")
    man = tmp_path / "manifest.csv"
    man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rc = cli.main(["--paired", str(man)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "코호트" in out and "RMSSD" in out


def test_cli_paired_json(capsys, tmp_path):
    rng = random.Random(5)
    rows = ["baseline,intervention"]
    for s in range(5):
        for tag, mean in (("b", 800), ("i", 880)):
            p = tmp_path / f"{tag}{s}.csv"
            vals = [mean + rng.gauss(0, 20) for _ in range(200)]
            p.write_text("rr_ms\n" + "\n".join(f"{x:.1f}" for x in vals) + "\n",
                         encoding="utf-8")
        rows.append(f"b{s}.csv,i{s}.csv")
    man = tmp_path / "m.csv"
    man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    import json
    rc = cli.main(["--paired", str(man), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["mode"] == "paired"
    assert data["_meta"]["n_subjects"] == 5


def test_cli_no_input_errors(capsys):
    rc = cli.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "CSV" in err or "paired" in err


def test_cli_paired_bad_manifest(capsys, tmp_path):
    man = tmp_path / "bad.csv"
    man.write_text("baseline,intervention\n", encoding="utf-8")  # 데이터 없음
    rc = cli.main(["--paired", str(man)])
    assert rc == 2
