"""이상박동 탐지/보정 테스트."""

import pytest

from hrvkit.artifacts import clean_rr, detect_artifacts


def test_physiologic_range_flag():
    rr = [800, 810, 250, 805, 2500, 790]  # 250(너무 짧음), 2500(너무 김)
    flags = detect_artifacts(rr, min_rr=300, max_rr=2000)
    assert flags[2] is True
    assert flags[4] is True
    assert flags[0] is False and flags[5] is False


def test_relative_jump_flag():
    # 국소 중앙값(~800) 대비 40% 급변 → 이상
    rr = [800, 805, 795, 1150, 800, 810, 790]
    flags = detect_artifacts(rr, rel_thresh=0.2, window=5)
    assert flags[3] is True
    assert sum(flags) == 1


def test_clean_interpolate_keeps_length_and_fixes():
    rr = [800, 810, 1200, 805, 795]  # 인덱스2가 조기수축류 이상
    cleaned, flags = clean_rr(rr, method="interpolate")
    assert len(cleaned) == len(rr)
    assert flags[2] is True
    # 보정값은 양옆 정상값(810, 805) 사이로 들어와야 함
    assert 805 - 1e-9 <= cleaned[2] <= 810 + 1e-9


def test_clean_remove_shortens():
    rr = [800, 810, 2500, 805, 795]
    cleaned, flags = clean_rr(rr, method="remove")
    assert len(cleaned) == 4
    assert 2500 not in cleaned


def test_clean_none_preserves():
    rr = [800, 2500, 805]
    cleaned, flags = clean_rr(rr, method="none")
    assert cleaned == [800.0, 2500.0, 805.0]
    assert flags[1] is True


def test_all_identical_no_artifacts():
    flags = detect_artifacts([800.0] * 20)
    assert not any(flags)


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        clean_rr([800, 810, 805], method="bogus")


def test_edge_artifact_at_boundary_interpolates():
    # 마지막 값이 이상이면 왼쪽 정상값으로 채움(길이 유지)
    rr = [800, 810, 805, 2500]
    cleaned, flags = clean_rr(rr, method="interpolate")
    assert flags[3] is True
    assert cleaned[3] == pytest.approx(805.0)
