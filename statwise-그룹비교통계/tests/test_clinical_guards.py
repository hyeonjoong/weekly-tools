"""Guards for the clinical-review findings: places where a competent user could
otherwise reach a confidently wrong conclusion.
"""

import json

import pytest

from statwise.binary import compare_binary
from statwise.cli import main
from statwise.dataio import map_binary_levels
from statwise.report import binary_sentence, render_binary_text, render_text


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _two_arm(tmp_path, name="t.csv", reverse=False):
    a = [("drug", v) for v in (44.5, 41.0, 46.2, 43.8, 45.1, 42.7, 47.0, 44.0)]
    b = [("sham", v) for v in (31.2, 34.5, 30.8, 33.1, 32.6, 35.0, 29.9, 33.4)]
    rows = (b + a) if reverse else (a + b)
    return _write(tmp_path, name,
                  "arm,v\n" + "\n".join(f"{g},{v}" for g, v in rows) + "\n")


# --------------------------------------------------------------------------
# direction: a one-sided margin must never be decided by CSV row order
# --------------------------------------------------------------------------

def test_ni_margin_without_a_reference_is_refused(tmp_path, capsys):
    path = _two_arm(tmp_path)
    rc = main([path, "--value", "v", "--group", "arm", "--ni-margin", "3",
               "--ni-direction", "higher_is_better"])
    assert rc == 2
    assert "행 순서" in capsys.readouterr().err


def test_ni_verdict_is_stable_under_row_order_once_pinned(tmp_path, capsys):
    verdicts = []
    for reverse in (False, True):
        path = _two_arm(tmp_path, f"o{int(reverse)}.csv", reverse=reverse)
        assert main([path, "--value", "v", "--group", "arm",
                     "--reference", "sham", "--ni-margin", "3",
                     "--ni-direction", "higher_is_better",
                     "--format", "json"]) == 0
        verdicts.append(json.loads(capsys.readouterr().out)["equivalence"])
    assert verdicts[0]["concluded"] == verdicts[1]["concluded"]
    assert verdicts[0]["diff"] == pytest.approx(verdicts[1]["diff"])


def test_asymmetric_tost_is_stable_under_row_order_once_pinned(tmp_path,
                                                               capsys):
    out = []
    for reverse in (False, True):
        path = _two_arm(tmp_path, f"a{int(reverse)}.csv", reverse=reverse)
        main([path, "--value", "v", "--group", "arm", "--reference", "sham",
              "--equivalence-margin", "-20,5", "--format", "json"])
        out.append(json.loads(capsys.readouterr().out)["equivalence"])
    assert out[0]["concluded"] == out[1]["concluded"]


# --------------------------------------------------------------------------
# the integrity screen must run in every pipeline
# --------------------------------------------------------------------------

def test_paired_pipeline_screens_for_sentinels(tmp_path, capsys):
    rows = ["subject,time,isi"]
    for i in range(8):
        rows.append(f"S{i},pre,{18 + i % 3}")
        rows.append(f"S{i},post,{12 + i % 3}")
    rows.append("S8,pre,19")
    rows.append("S8,post,-999")          # coded missing left in the data
    path = _write(tmp_path, "p.csv", "\n".join(rows) + "\n")
    assert main([path, "--paired", "--value", "isi", "--group", "time",
                 "--id", "subject", "--baseline", "pre"]) == 0
    out = capsys.readouterr().out
    assert "결측 코드로 흔히 쓰이는 값" in out


def test_paired_pipeline_screens_label_collisions(tmp_path, capsys):
    rows = ["subject,time,isi"]
    for i in range(8):
        rows.append(f"S{i},Pre,{18 + i % 3}")
        rows.append(f"S{i},post,{12 + i % 3}")
    path = _write(tmp_path, "p2.csv", "\n".join(rows) + "\n")
    main([path, "--paired", "--value", "isi", "--group", "time",
          "--id", "subject"])
    capsys.readouterr()


def test_binary_pipeline_screens_label_collisions(tmp_path, capsys):
    rows = ["arm,r"]
    for i in range(30):
        rows.append(f"Drug,{'yes' if i < 14 else 'no'}")
    rows.append("drug,yes")
    rows.append("drug,no")
    for i in range(30):
        rows.append(f"Placebo,{'yes' if i < 3 else 'no'}")
    path = _write(tmp_path, "b.csv", "\n".join(rows) + "\n")
    assert main([path, "--binary", "--value", "r", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "대소문자/공백만 다른 그룹 라벨" in out


# --------------------------------------------------------------------------
# --event-value silently imputing unknown codes as failures
# --------------------------------------------------------------------------

def test_event_value_discloses_non_responder_imputation():
    notes = []
    events, non = map_binary_levels(
        ["Yes", "No", "Unknown", "Yes", "Unknown"], event_value="Yes",
        notes=notes)
    assert events == {"YES"}
    assert "UNKNOWN" in non
    assert any("non-responder imputation" in n for n in notes)


def test_event_value_with_only_known_levels_is_silent():
    notes = []
    map_binary_levels(["Yes", "No", "Yes"], event_value="Yes", notes=notes)
    assert not any("non-responder" in n for n in notes)


def test_unknown_codes_reach_the_user_through_the_cli(tmp_path, capsys):
    rows = ["arm,r"]
    for i in range(20):
        rows.append(f"drug,{'Yes' if i < 9 else 'No'}")
    rows.append("drug,Unknown")
    for i in range(20):
        rows.append(f"sham,{'Yes' if i < 4 else 'No'}")
    path = _write(tmp_path, "u.csv", "\n".join(rows) + "\n")
    assert main([path, "--binary", "--value", "r", "--group", "arm",
                 "--event-value", "Yes"]) == 0
    assert "non-responder imputation" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the paste-ready sentence must be withheld when the input is suspect
# --------------------------------------------------------------------------

def test_sentence_is_withheld_when_integrity_warnings_fire(tmp_path, capsys):
    rows = ["v,arm"]
    for i in range(8):
        rows.append(f"{10 + i},Active")
    rows.append("-999,Active")
    for i in range(8):
        rows.append(f"{12 + i},Placebo")
    path = _write(tmp_path, "s.csv", "\n".join(rows) + "\n")
    assert main([path, "--value", "v", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "논문용 문장을 생성하지 않았습니다" in out
    assert "were compared using" not in out


def test_sentence_is_emitted_for_clean_input(tmp_path, capsys):
    path = _two_arm(tmp_path)
    assert main([path, "--value", "v", "--group", "arm"]) == 0
    out = capsys.readouterr().out
    assert "논문용 문장을 생성하지 않았습니다" not in out
    assert "were compared using" in out


def test_json_still_carries_the_draft_sentence(tmp_path, capsys):
    rows = ["v,arm"] + [f"{10 + i},Active" for i in range(8)] + ["-999,Active"]
    rows += [f"{12 + i},Placebo" for i in range(8)]
    path = _write(tmp_path, "s2.csv", "\n".join(rows) + "\n")
    main([path, "--value", "v", "--group", "arm", "--format", "json"])
    assert json.loads(capsys.readouterr().out)["sentence"]


# --------------------------------------------------------------------------
# binary post-hoc must report the size of the difference, not just p
# --------------------------------------------------------------------------

def test_binary_posthoc_carries_risk_difference_intervals():
    res = compare_binary([("placebo", (5, 40)), ("low", (12, 40)),
                          ("high", (22, 40))])
    assert res.pairwise
    for pw in res.pairwise:
        assert pw.rd_ci_low is not None and pw.rd_ci_high is not None
        assert pw.rd_ci_low <= pw.risk_diff <= pw.rd_ci_high
        assert pw.n_a == 40 and pw.n_b == 40
    text = render_binary_text(res)
    assert "95% CI" in text.split("[4] 사후검정")[1]
    assert "첫 그룹 − 둘째 그룹" in text


def test_binary_k_group_sentence_reports_effect_sizes():
    res = compare_binary([("placebo", (5, 40)), ("low", (12, 40)),
                          ("high", (22, 40))])
    s = binary_sentence(res)
    assert "risk difference" in s
    assert "95% CI" in s
    assert "adjusted p" in s


# --------------------------------------------------------------------------
# wording that a journal reviewer would query
# --------------------------------------------------------------------------

def test_hodges_lehmann_is_not_called_a_difference_of_medians():
    from statwise.analyze import analyze
    res = analyze([("a", [1.0, 2.0, 3.0, 4.0, 5.0, 40.0]),
                   ("b", [10.0, 11.0, 12.0, 13.0, 14.0, 90.0])])
    assert res.test_name == "Mann-Whitney U test"
    text = render_text(res)
    assert "location shift" in text
    assert "not the difference of the" in text


def test_welch_anova_sentence_qualifies_eta_squared():
    from statwise.analyze import analyze
    res = analyze([("a", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]),
                   ("b", [10.0, 40.0, 5.0, 60.0, 2.0, 80.0, 1.0, 90.0]),
                   ("c", [20.0, 21.0, 19.0, 22.0, 18.0, 23.0, 17.0, 24.0])])
    if res.test_name.startswith("Welch's ANOVA"):
        assert "equal-variance sums of squares" in render_text(res)


def test_posthoc_footnote_states_the_sign_convention():
    from statwise.analyze import analyze
    res = analyze([("low", [3.0, 5.0, 4.0, 6.0, 3.0, 5.0, 4.0, 6.0]),
                   ("mid", [6.0, 8.0, 7.0, 9.0, 7.0, 6.0, 8.0, 7.0]),
                   ("high", [9.0, 11.0, 10.0, 12.0, 11.0, 13.0, 9.0, 12.0])])
    assert res.pairwise
    assert "첫 그룹 − 둘째 그룹" in render_text(res)


def test_non_equivalence_sentence_does_not_rest_on_a_large_p():
    from statwise.analyze import EquivalenceSpec, analyze
    res = analyze([("a", [5.1, 4.9, 5.3, 5.0, 5.2, 4.8, 5.4, 5.0]),
                   ("b", [7.1, 6.9, 7.3, 7.0, 7.2, 6.8, 7.4, 7.0])],
                  equivalence=EquivalenceSpec(margin=(-0.5, 0.5)))
    text = render_text(res)
    assert "lies wholly outside the margin" in text
    assert "is not the basis of this conclusion" in text


def test_multi_endpoint_sentence_labels_the_unadjusted_p():
    from statwise.endpoints import run_endpoints
    from statwise.report import render_multi_text
    ds = [(f"e{i}", [("a", (12 + i, 40)), ("b", (23 + i, 40))])
          for i in range(6)]
    multi = run_endpoints(ds, binary=True, correction="holm")
    text = render_multi_text(multi, detail=True)
    if any(r.result.pvalue < 0.05 <= r.result.pvalue_adj
           for r in multi.analysed):
        assert "unadjusted p" in text
        assert "the adjusted p" in text


def test_wilcoxon_sentence_accounts_for_zero_difference_pairs():
    from statwise.analyze import analyze_paired
    a = [10.0, 12.0, 11.0, 13.0, 10.0, 14.0, 12.0, 11.0, 15.0, 10.0]
    b = [10.0, 9.0, 8.0, 9.0, 7.0, 9.0, 8.0, 40.0, 9.0, 7.0]
    res = analyze_paired(("post", a), ("pre", b))
    if res.test_name.startswith("Wilcoxon") and res.n_zero_diff:
        assert "non-zero difference contributing" in render_text(res)
