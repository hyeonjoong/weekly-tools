# -*- coding: utf-8 -*-
"""방향 판정 — 증거가 없으면 판정하지 않는다."""

import pytest

from stalecheck import verdicts


class FakeRec(object):
    def __init__(self, mtime, rel="f.md"):
        self.mtime = mtime
        self.rel = rel


class FakePair(object):
    def __init__(self, pkg_mtime, work_mtime, pkg_hash="a", work_hash="b"):
        self.pkg = FakeRec(pkg_mtime)
        self.work = FakeRec(work_mtime)
        self.pkg_hash = pkg_hash
        self.work_hash = work_hash

    @property
    def same(self):
        return self.pkg_hash == self.work_hash


def test_identical_hash_is_same():
    assert verdicts.judge(FakePair(0, 100, "h", "h")).label == verdicts.SAME


def test_identical_hash_ignores_mtime_difference():
    """내용이 같으면 mtime 이 아무리 벌어져도 낡은 게 아니다."""
    v = verdicts.judge(FakePair(0, 10 ** 9, "h", "h"))
    assert v.label == verdicts.SAME


def test_work_newer_is_critical():
    v = verdicts.judge(FakePair(1000, 2000))
    assert v.label == verdicts.CRITICAL_STALE_PACKAGE
    assert v.reason


def test_package_newer_is_warning():
    v = verdicts.judge(FakePair(2000, 1000))
    assert v.label == verdicts.WARN_PACKAGE_NEWER
    assert "손으로" in v.reason


def test_equal_mtime_is_undecidable():
    v = verdicts.judge(FakePair(1000, 1000))
    assert v.label == verdicts.UNDECIDABLE


@pytest.mark.parametrize("delta", [0.0, 0.5, 1.0, 1.9, -0.5, -1.9])
def test_within_tolerance_is_undecidable(delta):
    v = verdicts.judge(FakePair(1000, 1000 + delta))
    assert v.label == verdicts.UNDECIDABLE


@pytest.mark.parametrize("delta", [2.01, 3, 60, 86400])
def test_beyond_tolerance_work_newer_is_critical(delta):
    v = verdicts.judge(FakePair(1000, 1000 + delta))
    assert v.label == verdicts.CRITICAL_STALE_PACKAGE


@pytest.mark.parametrize("delta", [2.01, 3, 60, 86400])
def test_beyond_tolerance_package_newer_is_warning(delta):
    v = verdicts.judge(FakePair(1000, 1000 - delta))
    assert v.label == verdicts.WARN_PACKAGE_NEWER


def test_tolerance_is_configurable():
    v = verdicts.judge(FakePair(1000, 1100), tolerance=200)
    assert v.label == verdicts.UNDECIDABLE


def test_zero_tolerance_still_treats_exact_tie_as_undecidable():
    v = verdicts.judge(FakePair(1000, 1000), tolerance=0)
    assert v.label == verdicts.UNDECIDABLE


def test_unreadable_package_is_undecidable():
    v = verdicts.judge(FakePair(1000, 2000, pkg_hash=None))
    assert v.label == verdicts.UNDECIDABLE
    assert "읽지" in v.reason


def test_unreadable_work_is_undecidable():
    v = verdicts.judge(FakePair(1000, 2000, work_hash=None))
    assert v.label == verdicts.UNDECIDABLE


def test_unreadable_wins_over_mtime_evidence():
    """읽지 못한 파일에 대해 mtime 만으로 '구세대'라고 말하지 않는다."""
    v = verdicts.judge(FakePair(0, 10 ** 9, pkg_hash=None, work_hash=None))
    assert v.label == verdicts.UNDECIDABLE


def test_only_stale_package_counts_as_critical():
    assert verdicts.CRITICAL_VERDICTS == frozenset({verdicts.CRITICAL_STALE_PACKAGE})
    assert verdicts.WARN_PACKAGE_NEWER not in verdicts.CRITICAL_VERDICTS
    assert verdicts.UNDECIDABLE not in verdicts.CRITICAL_VERDICTS


def test_tolerance_default_is_two_seconds():
    assert verdicts.MTIME_TOLERANCE_SEC == 2.0


def test_labels_are_distinct_and_bracketed():
    labels = [verdicts.CRITICAL_STALE_PACKAGE, verdicts.WARN_PACKAGE_NEWER,
              verdicts.UNDECIDABLE]
    assert len(set(labels)) == 3
    for label in labels:
        assert label.startswith("[") and "]" in label
    assert not verdicts.SAME.startswith("[")


def test_verdict_equality_by_label():
    assert verdicts.Verdict("x", "a") == verdicts.Verdict("x", "b")
    assert verdicts.Verdict("x") != verdicts.Verdict("y")


def test_verdict_not_equal_to_other_types():
    assert verdicts.Verdict("x") != "x"


def test_judge_covers_exactly_the_four_labels():
    """네 갈래가 전부 **도달 가능**하고, 다섯 번째가 없다는 것까지 확인한다."""
    seen = set()
    for pkg, work, ph, wh in [(0, 100, "a", "b"), (100, 0, "a", "b"),
                              (10, 10, "a", "b"), (0, 100, "a", "a"),
                              (0, 100, None, "b")]:
        seen.add(verdicts.judge(FakePair(pkg, work, ph, wh)).label)
    assert seen == {verdicts.SAME, verdicts.CRITICAL_STALE_PACKAGE,
                    verdicts.WARN_PACKAGE_NEWER, verdicts.UNDECIDABLE}


def test_no_guessing_branch_in_source():
    """'애매하면 최신 쪽' 같은 경로가 없음을 소스에서 확인한다."""
    import inspect
    source = inspect.getsource(verdicts.judge)
    assert source.count("return Verdict") == 5
    assert "abs(" not in source, "절댓값으로 방향을 뭉개면 추측이 된다"


def test_negative_mtimes_handled():
    v = verdicts.judge(FakePair(-100, -50))
    assert v.label == verdicts.CRITICAL_STALE_PACKAGE


def test_float_precision_near_tolerance():
    assert verdicts.judge(FakePair(0, 2.0)).label == verdicts.UNDECIDABLE
    assert verdicts.judge(FakePair(0, 2.0001)).label == verdicts.CRITICAL_STALE_PACKAGE
