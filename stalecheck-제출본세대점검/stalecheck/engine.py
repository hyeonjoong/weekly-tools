"""점검 한 판을 조립한다 — 읽고, 짝짓고, 판정하고, 세어서 자백한다."""

import os
import time

from . import pairing as pairing_mod
from . import scanning, siblings, textdiff, verdicts, writer
from .errors import RefusedError
from .scanning import DEFAULT_EXTS, DEFAULT_MAX_BYTES, nfc


def fmt_time(epoch):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def fmt_size(nbytes):
    return "{:,} B".format(nbytes)


class Row(object):
    """판정 한 줄. 산출물에는 **상대경로만** 실린다(집 폴더 이름이 새지 않게)."""

    __slots__ = ("verdict", "work_rel", "pkg_rel", "work_mtime", "pkg_mtime",
                 "work_size", "pkg_size", "diff", "normalized", "how", "reason",
                 "raw_differs")

    def __init__(self, verdict, pair, diff, package_label, raw_differs=False):
        self.verdict = verdict
        self.work_rel = pair.work.rel
        self.pkg_rel = os.path.join(package_label, pair.pkg.rel)
        self.work_mtime = pair.work.mtime
        self.pkg_mtime = pair.pkg.mtime
        self.work_size = pair.work.size
        self.pkg_size = pair.pkg.size
        self.diff = diff
        self.normalized = pair.normalized
        self.how = pair.how
        self.reason = verdict.reason
        #: 원시 바이트는 달랐는데 정규화 후 같아졌는가 — ``diff -r`` 대비 유일한 우위
        self.raw_differs = raw_differs


class PackageResult(object):
    """봉투 하나에 대한 결과."""

    def __init__(self, label, root, scan, pairs):
        self.label = label
        self.root = root
        self.scan = scan
        self.pairing = pairs
        self.rows = []

    @property
    def critical_rows(self):
        return [r for r in self.rows
                if r.verdict.label in verdicts.CRITICAL_VERDICTS]

    @property
    def warn_rows(self):
        return [r for r in self.rows
                if r.verdict.label == verdicts.WARN_PACKAGE_NEWER]

    @property
    def undecidable_rows(self):
        return [r for r in self.rows if r.verdict.label == verdicts.UNDECIDABLE]

    @property
    def same_rows(self):
        return [r for r in self.rows if r.verdict.label == verdicts.SAME]

    @property
    def normalized_only_rows(self):
        """원시 바이트는 다른데 정규화 후 같아진 쌍 — ``diff -r`` 대비 유일한 우위."""
        return [r for r in self.same_rows if r.raw_differs]


class Analysis(object):
    def __init__(self, work_root, work_scan):
        self.work_root = work_root
        self.work_scan = work_scan
        self.packages = []
        self.build_lag = []
        self.figure_lag = []
        self.unreadable = []        # (rel, reason)
        self.generated_at = time.time()

    # --- 집계 -------------------------------------------------------------
    @property
    def rows(self):
        out = []
        for pkg in self.packages:
            out.extend(pkg.rows)
        return out

    @property
    def critical_rows(self):
        return [r for r in self.rows if r.verdict.label in verdicts.CRITICAL_VERDICTS]

    @property
    def warn_rows(self):
        return [r for r in self.rows if r.verdict.label == verdicts.WARN_PACKAGE_NEWER]

    @property
    def undecidable_rows(self):
        return [r for r in self.rows if r.verdict.label == verdicts.UNDECIDABLE]

    @property
    def same_rows(self):
        return [r for r in self.rows if r.verdict.label == verdicts.SAME]

    @property
    def normalized_only_rows(self):
        return [r for r in self.same_rows if r.raw_differs]

    @property
    def renamed_identical(self):
        out = []
        for pkg in self.packages:
            for pair in pkg.pairing.renamed_identical:
                out.append((pkg.label, pair))
        return out

    @property
    def pair_count(self):
        return len(self.rows)

    @property
    def coverage(self):
        paired = 0
        total = 0
        for pkg in self.packages:
            p = pkg.pairing
            paired += len(p.pairs) + len(p.renamed_identical)
            total += (len(p.pairs) + len(p.renamed_identical)
                      + len(p.pkg_only) + len(p.ambiguous))
        if total == 0:
            return None
        return paired / float(total)

    @property
    def work_only(self):
        """어느 봉투에도 짝지어지지 않은 작업 파일.

        봉투가 여럿일 때 한 봉투의 결과만 보면, 다른 봉투에 들어간 파일이
        '작업폴더에만 있음'으로 잘못 실린다. 그래서 전 봉투를 합쳐서 센다.
        """
        used = set()
        for pkg in self.packages:
            for pair in pkg.pairing.pairs:
                used.add(pair.work.path)
            for pair in pkg.pairing.renamed_identical:
                used.add(pair.work.path)
        return [rec for rec in self.work_scan.files if rec.path not in used]

    @property
    def skipped_ext(self):
        """확장자가 대상 밖이라 보지 않은 파일 수 — 작업/봉투를 나눠서 돌려준다."""
        from collections import Counter
        pkg_counter = Counter()
        for pkg in self.packages:
            pkg_counter.update(pkg.scan.skipped_ext)
        return Counter(self.work_scan.skipped_ext), pkg_counter

    @property
    def symlinks(self):
        """따라가지 않은 심볼릭 링크 — **봉투 쪽도 포함**한다.

        봉투 그림이 전부 심볼릭 링크면 한 쌍도 비교되지 않는데,
        작업 폴더 쪽만 세면 그 사실이 리포트에 나타나지 않는다.
        """
        out = list(self.work_scan.symlinks)
        for pkg in self.packages:
            out.extend(os.path.join(pkg.label, rel) for rel in pkg.scan.symlinks)
        return out

    @property
    def hidden(self):
        """`.` 으로 시작해 스캔하지 않은 파일 — 양쪽 합산."""
        out = list(self.work_scan.hidden)
        for pkg in self.packages:
            out.extend(os.path.join(pkg.label, rel) for rel in pkg.scan.hidden)
        return out

    @property
    def ambiguous_count(self):
        """이름이 겹쳐 **판정하지 않은** 봉투 파일 수."""
        return sum(len(pkg.pairing.ambiguous) for pkg in self.packages)

    @property
    def excluded_dirs(self):
        """제외한 폴더 — 봉투 쪽은 `봉투라벨/경로` 로 구분해서 돌려준다."""
        out = list(self.work_scan.excluded_dirs)
        for pkg in self.packages:
            out.extend(os.path.join(pkg.label, rel)
                       for rel in pkg.scan.excluded_dirs)
        return out

    @property
    def skipped_large(self):
        out = list(self.work_scan.skipped_large)
        for pkg in self.packages:
            out.extend((os.path.join(pkg.label, rel), size)
                       for rel, size in pkg.scan.skipped_large)
        return out

    @property
    def package_had_files(self):
        """봉투 안에 (확장자·크기 필터 이전의) 파일이 하나라도 있었는가."""
        for pkg in self.packages:
            if (pkg.scan.read_count or pkg.scan.skipped_ext
                    or pkg.scan.skipped_large or pkg.scan.unreadable):
                return True
        return False

    @property
    def files_read(self):
        n = self.work_scan.read_count
        for pkg in self.packages:
            n += pkg.scan.read_count
        return n


def _raw_differs(pair):
    """정규화 없이는 달랐을 쌍인가. 못 읽으면 False(과장하지 않는다)."""
    from .normalize import UnreadableFile, raw_hash
    try:
        return raw_hash(pair.pkg.path) != raw_hash(pair.work.path)
    except UnreadableFile:
        return False


def _validate_roots(work_root, package_roots):
    """입력 두 쪽의 관계를 **실체 경로로** 확인한다.

    어휘 비교(`abspath`)만 하면 작업 폴더 안에 심어 둔 심볼릭 링크가 트리 **밖**을
    가리켜도 통과한다. 그러면 툴은 엉뚱한 폴더를 봉투라고 부르며 비교한다.
    """
    work_root = os.path.abspath(os.path.expanduser(work_root))
    if not os.path.isdir(work_root):
        raise RefusedError("--work 폴더가 없습니다: %s" % work_root)
    # 양쪽을 같은 기준(실체 경로)으로 맞춘다. 한쪽만 풀면 봉투 라벨이
    # `../../private/tmp/...` 처럼 나와 리포트가 읽을 수 없게 된다.
    work_root = os.path.realpath(work_root)
    work_real = writer.canonical(work_root)
    resolved = []
    for raw in package_roots:
        pkg = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(pkg):
            raise RefusedError("--package 폴더가 없습니다: %s" % pkg)
        pkg_real = writer.canonical(pkg)
        if pkg_real == work_real:
            raise RefusedError(
                "--work 와 --package 가 같은 폴더입니다. 봉투는 작업 폴더 안쪽이어야 합니다."
            )
        # 받아들이는 쪽이므로 대소문자를 **접지 않는다** — 접으면 대소문자 구분
        # 파일시스템에서 `Work/` 가 `work/` 안이라고 판정돼 트리 밖을 봉투로 받는다.
        if not writer.is_inside(pkg, work_root, fold=False):
            raise RefusedError(
                "--package 가 --work 안에 있지 않습니다.\n"
                "  --work    : %s\n  --package : %s\n"
                "  (심볼릭 링크를 풀어 비교했습니다. 봉투가 링크로 트리 밖을 가리키면 "
                "무엇과 무엇을 비교하는지 알 수 없어 거절합니다.)"
                % (work_root, pkg)
            )
        # 실체 경로로 바꿔 둔다 — 링크 경로를 그대로 쓰면 작업 스캔이
        # 링크가 가리키는 **진짜 폴더**를 빼지 못해 봉투 파일이 작업본으로도 세어진다.
        resolved.append(os.path.realpath(pkg))
    for i, a in enumerate(resolved):
        for b in resolved[i + 1:]:
            if (writer.is_inside(a, b, fold=False)
                    or writer.is_inside(b, a, fold=False)):
                raise RefusedError(
                    "--package 두 개가 서로 포함관계입니다: %s / %s" % (a, b)
                )
    return work_root, resolved


def analyze(work_root, package_roots, exts=DEFAULT_EXTS,
            max_bytes=DEFAULT_MAX_BYTES,
            exclude_pattern=scanning.DEFAULT_EXCLUDE_PATTERN,
            tolerance=verdicts.MTIME_TOLERANCE_SEC,
            check_siblings=True):
    """점검 한 판. 입력을 읽기만 한다."""
    work_root, package_roots = _validate_roots(work_root, package_roots)

    work_scan = scanning.scan_tree(
        work_root, exts=exts, max_bytes=max_bytes,
        exclude_pattern=exclude_pattern, package_mode=False,
        skip_subtrees=package_roots,
    )
    analysis = Analysis(work_root, work_scan)
    cache = pairing_mod.HashCache()

    for pkg_root in package_roots:
        label = nfc(os.path.relpath(pkg_root, work_root))
        pkg_scan = scanning.scan_tree(
            pkg_root, exts=exts, max_bytes=max_bytes,
            exclude_pattern=exclude_pattern, package_mode=True,
        )
        cache.label = label
        pairs = pairing_mod.pair_files(pkg_scan.files, work_scan.files, cache)
        cache.label = ""
        result = PackageResult(label, pkg_root, pkg_scan, pairs)
        for pair in pairs.pairs:
            verdict = verdicts.judge(pair, tolerance=tolerance)
            raw_differs = False
            if verdict.label == verdicts.SAME:
                diff = None
                if pair.normalized != "none":
                    # 정규화가 개입한 일치만 원시 바이트를 되짚는다(비용 최소).
                    raw_differs = _raw_differs(pair)
            else:
                diff = textdiff.line_diff(pair.pkg.path, pair.work.path)
            result.rows.append(Row(verdict, pair, diff, label, raw_differs))
        result.rows.sort(key=_row_sort_key)
        analysis.packages.append(result)

    if check_siblings:
        analysis.build_lag = siblings.find_build_lag(work_scan.files)
        analysis.figure_lag = siblings.find_figure_sibling_lag(work_scan.files)

    unreadable = list(work_scan.unreadable)
    for pkg in analysis.packages:
        unreadable.extend((os.path.join(pkg.label, rel), reason)
                          for rel, reason in pkg.scan.unreadable)
    unreadable.extend(cache.unreadable)
    seen = set()
    deduped = []
    for rel, reason in unreadable:
        if rel in seen:
            continue
        seen.add(rel)
        deduped.append((rel, reason))
    analysis.unreadable = deduped
    return analysis


_VERDICT_ORDER = {
    verdicts.CRITICAL_STALE_PACKAGE: 0,
    verdicts.WARN_PACKAGE_NEWER: 1,
    verdicts.UNDECIDABLE: 2,
    verdicts.SAME: 3,
}


def _row_sort_key(row):
    return (_VERDICT_ORDER.get(row.verdict.label, 9), row.work_rel)
