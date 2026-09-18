# -*- coding: utf-8 -*-
"""짝짓기 — 추측하지 않는다."""

import os
import unicodedata

import pytest

from conftest import T0, T1, write
from stalecheck import pairing, scanning


def scan(path, **kw):
    kw.setdefault("exts", (".md", ".py", ".png", ".pdf", ".docx", ".csv"))
    return scanning.scan_tree(str(path), **kw)


def pair(work_dir, pkg_dir):
    w = scan(work_dir)
    p = scan(pkg_dir, package_mode=True)
    return pairing.pair_files(p.files, w.files, pairing.HashCache())


def test_same_name_same_content_is_a_match(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "p" / "a.md", "x", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1 and res.pairs[0].same


def test_same_name_different_content_is_a_pair_not_a_match(tmp_path):
    write(tmp_path / "w" / "a.md", "new", T1)
    write(tmp_path / "p" / "a.md", "old", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1 and not res.pairs[0].same


def test_name_matching_ignores_directory(tmp_path):
    write(tmp_path / "w" / "figures" / "f.png", b"a", T1)
    write(tmp_path / "p" / "05_Figures" / "f.png", b"b", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1


def test_name_matching_is_case_insensitive(tmp_path):
    write(tmp_path / "w" / "Figure1.PNG", b"a", T1)
    write(tmp_path / "p" / "figure1.png", b"b", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1


def test_nfd_and_nfc_names_pair(tmp_path):
    nfd = unicodedata.normalize("NFD", "그림.png")
    write(tmp_path / "w" / nfd, b"a", T1)
    write(tmp_path / "p" / "그림.png", b"b", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1, "NFD 파일명이 짝지어지지 않으면 한글 폴더가 통째로 조용해진다"


def test_renamed_identical_is_not_a_judged_pair(tmp_path):
    write(tmp_path / "w" / "fig1_원본.png", b"same", T1)
    write(tmp_path / "p" / "Figure1.png", b"same", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs == []
    assert len(res.renamed_identical) == 1
    assert res.renamed_identical[0].how == pairing.MATCH_HASH


def test_renamed_different_content_is_not_paired(tmp_path):
    write(tmp_path / "w" / "fig1_원본.png", b"new", T1)
    write(tmp_path / "p" / "Figure1.png", b"old", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs == [] and res.renamed_identical == []
    assert len(res.pkg_only) == 1, "이름이 다른 사본은 추측해서 짝짓지 않는다"


def test_renamed_ambiguous_hash_is_not_paired(tmp_path):
    """같은 내용의 작업본이 둘이면 어느 쪽인지 모른다 — 짝짓지 않는다."""
    write(tmp_path / "w" / "a.png", b"same", T1)
    write(tmp_path / "w" / "b.png", b"same", T1)
    write(tmp_path / "p" / "Figure1.png", b"same", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.renamed_identical == [] and len(res.pkg_only) == 1


def test_duplicate_names_without_hash_match_are_ambiguous(tmp_path):
    write(tmp_path / "w" / "A" / "f.png", b"x", T1)
    write(tmp_path / "w" / "B" / "f.png", b"y", T1)
    write(tmp_path / "p" / "f.png", b"z", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs == []
    assert len(res.ambiguous) == 1
    assert len(res.ambiguous[0][1]) == 2


def test_duplicate_names_one_matching_one_diverged_is_ambiguous(tmp_path):
    """봉투와 맞는 사본을 골라 버리면 판정이 언제나 '일치'로 고정된다.

    갈라진 최신본(`B/f.png`)이 있는데도 조용해지는 것이 정확히 이 툴이 막아야 할 일이다.
    """
    write(tmp_path / "w" / "A" / "f.png", b"same", T0)
    write(tmp_path / "w" / "B" / "f.png", b"diverged-and-newer", T1)
    write(tmp_path / "p" / "f.png", b"same", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs == []
    assert len(res.ambiguous) == 1
    assert len(res.ambiguous[0][1]) == 2


def test_duplicate_names_all_identical_resolve_quietly(tmp_path):
    """후보 전부가 같은 내용이면 어느 것을 골라도 결과가 같으므로 짝지어도 된다."""
    write(tmp_path / "w" / "A" / "f.png", b"same", T1)
    write(tmp_path / "w" / "B" / "f.png", b"same", T1)
    write(tmp_path / "p" / "f.png", b"same", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1 and res.pairs[0].same


def test_duplicate_names_all_identical_but_stale_is_judged(tmp_path):
    write(tmp_path / "w" / "A" / "f.png", b"new", T1)
    write(tmp_path / "w" / "B" / "f.png", b"new", T1)
    write(tmp_path / "p" / "f.png", b"old", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 1 and not res.pairs[0].same


def test_zero_byte_files_are_not_cross_paired_by_hash(tmp_path):
    """0바이트 파일끼리 엮이면 짝지음 비율만 부풀고 양쪽 결손이 가려진다."""
    write(tmp_path / "w" / "data" / "빈파일.csv", b"", T1)
    write(tmp_path / "p" / "__init__.py", b"", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.renamed_identical == []
    assert len(res.pkg_only) == 1 and len(res.work_only) == 1
    assert res.coverage == pytest.approx(0.0)


def test_renamed_identical_requires_same_extension(tmp_path):
    write(tmp_path / "w" / "표.csv", b"boilerplate", T1)
    write(tmp_path / "p" / "표.md", b"boilerplate", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.renamed_identical == [] and len(res.pkg_only) == 1


def test_package_only_file_listed(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "p" / "커버레터.md", "y", T0)
    write(tmp_path / "p" / "a.md", "x", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert [r.rel for r in res.pkg_only] == ["커버레터.md"]


def test_work_only_file_listed(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "w" / "메모.md", "y", T1)
    write(tmp_path / "p" / "a.md", "x", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert [r.rel for r in res.work_only] == ["메모.md"]


def test_coverage_is_paired_over_package_files(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "p" / "a.md", "x", T0)
    write(tmp_path / "p" / "b.md", "y", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.coverage == pytest.approx(0.5)


def test_coverage_none_when_package_empty(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    os.makedirs(str(tmp_path / "p"))
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.coverage is None


def test_coverage_counts_renamed_identical(tmp_path):
    write(tmp_path / "w" / "원본.png", b"same", T1)
    write(tmp_path / "p" / "Figure1.png", b"same", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.coverage == pytest.approx(1.0)


def test_pdf_metadata_only_difference_counts_as_match(tmp_path):
    from conftest import 예제
    write(tmp_path / "w" / "f.pdf", 예제.make_pdf("F", "20260731000000", "AA" * 16), T1)
    write(tmp_path / "p" / "f.pdf", 예제.make_pdf("F", "20260730000000", "BB" * 16), T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs[0].same
    assert res.pairs[0].normalized == "pdf-meta"


def test_normalized_property_reports_none_when_untouched(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "p" / "a.md", "x", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs[0].normalized == "none"


def test_hash_cache_reads_each_file_once(tmp_path, monkeypatch):
    calls = []
    from stalecheck import normalize
    real = normalize.content_hash

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(pairing, "content_hash", counting)
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "p" / "a.md", "x", T0)
    pair(tmp_path / "w", tmp_path / "p")
    assert len(calls) == len(set(calls))


def test_unreadable_file_recorded_in_cache(tmp_path):
    p = write(tmp_path / "p" / "a.md", "x", T0)
    write(tmp_path / "w" / "a.md", "x", T1)
    w = scan(tmp_path / "w")
    pk = scan(tmp_path / "p", package_mode=True)
    os.remove(p)
    cache = pairing.HashCache()
    res = pairing.pair_files(pk.files, w.files, cache)
    assert cache.unreadable
    assert res.pairs[0].pkg_hash is None


def test_empty_package_yields_no_pairs(tmp_path):
    write(tmp_path / "w" / "a.md", "x", T1)
    os.makedirs(str(tmp_path / "p"))
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs == [] and len(res.work_only) == 1


def test_empty_work_yields_package_only(tmp_path):
    os.makedirs(str(tmp_path / "w"))
    write(tmp_path / "p" / "a.md", "x", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pkg_only) == 1


def test_both_empty(tmp_path):
    os.makedirs(str(tmp_path / "w"))
    os.makedirs(str(tmp_path / "p"))
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs == [] and res.coverage is None


def test_one_work_file_matched_once_only(tmp_path):
    """봉투에 같은 파일이 두 벌 있으면 둘 다 판정하되 작업본은 한 번만 소비된다."""
    write(tmp_path / "w" / "a.md", "x", T1)
    write(tmp_path / "p" / "A" / "a.md", "x", T0)
    write(tmp_path / "p" / "B" / "a.md", "y", T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert len(res.pairs) == 2
    assert res.work_only == []


def test_pair_same_property_uses_normalized_hash(tmp_path):
    from conftest import 예제
    write(tmp_path / "w" / "a.docx", 예제.make_docx("본문", "2026-07-31T00:00:00Z"), T1)
    write(tmp_path / "p" / "a.docx", 예제.make_docx("본문", "2026-07-30T00:00:00Z"), T0)
    res = pair(tmp_path / "w", tmp_path / "p")
    assert res.pairs[0].same and res.pairs[0].normalized == "zip-docprops"


def test_match_constants_are_distinct():
    assert len({pairing.MATCH_NAME, pairing.MATCH_HASH, pairing.AMBIGUOUS}) == 3
