"""리포트 — 일치는 한 줄, 치명은 최대 5건, 나머지는 CSV.

일치 목록을 쏟아내면 사람이 읽기를 포기한다. 196쌍이 일치하면 ``일치 196쌍`` 한 줄이다.

``[커버리지 자백]`` 이 비면 리포트를 아예 출력하지 않는다(``ReportIntegrityError``).
자백 없는 리포트는 "전부 확인했다"로 읽히는데, 이 툴은 **짝지은 쌍에 대해서만** 말한다.
"""

import os
import re

from . import verdicts
from .engine import fmt_size, fmt_time
from .errors import ReportIntegrityError
from .textdiff import safe_path

#: 콘솔에 펼쳐 보여 줄 치명 항목 수. 나머지는 CSV로 보낸다.
CONSOLE_MAX_CRITICAL_ITEMS = 5

COVERAGE_HEADER = "[커버리지 자백]"


def sanitize_reason(text):
    """OS 가 준 오류 문자열도 그대로 믿지 않는다."""
    return safe_path(str(text))

NOT_CHECKED = (
    "원고 숫자 → tracecheck",
    "저널 한도/약속 → packlist",
    "응답서↔개정본 → revcheck",
    "그림 화질/픽셀 비교 → 검사 안 함",
)


def _rel(path):
    """산출물에 절대 경로(집 폴더 이름)를 싣지 않는다."""
    return safe_path(os.path.basename(os.path.normpath(path)))


def _diff_suffix(row):
    if row.diff is None:
        return ""
    if not row.diff.comparable:
        return "   (%s)" % row.diff.reason
    if row.diff.added == 0 and row.diff.removed == 0:
        # 행 단위로는 같은데 바이트가 다르다 — "+0행/-0행" 만 찍으면
        # "아무것도 안 바뀌었다"로 읽힌다.
        return "   (줄 내용은 같고 줄끝·마지막 개행만 다름)"
    return "   (+%d행 / -%d행)" % (row.diff.added, row.diff.removed)


def _render_row(row, indent="      "):
    lines = ["  " + safe_path(row.work_rel)]
    detail = "%s작업 %s (%s)  >  봉투 %s (%s)%s" % (
        indent, fmt_time(row.work_mtime), fmt_size(row.work_size),
        fmt_time(row.pkg_mtime), fmt_size(row.pkg_size), _diff_suffix(row),
    )
    lines.append(detail)
    # 봉투 안 경로를 반드시 보여 준다 — 한 작업본에 봉투 사본이 둘이면
    # 작업 경로만으로는 어느 파일을 다시 복사해야 하는지 알 수 없다.
    lines.append("%s봉투 안: %s" % (indent, safe_path(row.pkg_rel)))
    if row.diff is not None and row.diff.comparable:
        for preview in row.diff.preview:
            lines.append(indent + preview)
    return lines


def coverage_block(analysis, min_coverage):
    """자백 블록. 비어 있을 수 없다."""
    lines = [COVERAGE_HEADER]
    total_seen = analysis.files_read + len(analysis.unreadable) + len(analysis.skipped_large)
    lines.append("  읽음: %d/%d 파일 (못 읽음 %d · 크기초과 건너뜀 %d)"
                 % (analysis.files_read, total_seen,
                    len(analysis.unreadable), len(analysis.skipped_large)))
    for rel, reason in analysis.unreadable[:5]:
        lines.append("    · 못 읽음: %s — %s" % (safe_path(rel), sanitize_reason(reason)))
    if len(analysis.unreadable) > 5:
        lines.append("    · … 외 %d개" % (len(analysis.unreadable) - 5))
    for rel, size in analysis.skipped_large[:5]:
        lines.append("    · 건너뜀(%s): %s" % (fmt_size(size), safe_path(rel)))
    if len(analysis.skipped_large) > 5:
        lines.append("    · … 외 %d개" % (len(analysis.skipped_large) - 5))

    lines.append("  셈의 범위: 같은 파일명으로 짝지어진 %d쌍만. "
                 "이름이 다른 사본은 짝짓지 않았습니다." % analysis.pair_count)
    renamed = analysis.renamed_identical
    if renamed:
        lines.append("    · 이름은 다르지만 내용이 똑같아 낡았을 수 없는 파일 %d개는 "
                     "판정에서 제외했습니다." % len(renamed))
    pkg_only = sum(len(p.pairing.pkg_only) for p in analysis.packages)
    ambiguous = sum(len(p.pairing.ambiguous) for p in analysis.packages)
    work_only = len(analysis.work_only)
    lines.append("  짝 없음: 봉투에만 %d개 · 작업폴더에만 %d개 "
                 "(봉투에 넣지 않은 파일 — 정상일 수 있습니다)" % (pkg_only, work_only))
    if ambiguous:
        lines.append("  이름이 겹쳐 **판정하지 않은** 봉투 파일: %d개 — "
                     "작업 폴더에 같은 이름이 여럿이고 내용이 서로 달라 "
                     "어느 것이 원본인지 알 수 없습니다 (짝없음.csv 참고)" % ambiguous)
    cov = analysis.coverage
    if cov is None:
        lines.append("  짝지음 비율: 계산 불가(봉투에 대상 파일이 없습니다)")
    else:
        lines.append("  짝지음 비율: %.0f%% (기준 %.0f%%)" % (cov * 100, min_coverage * 100))
    work_ext, pkg_ext = analysis.skipped_ext
    if work_ext or pkg_ext:
        combined = work_ext + pkg_ext
        top = " · ".join("%s %d" % (e.lstrip("."), n)
                         for e, n in combined.most_common(6))
        lines.append("  확장자가 대상 밖이라 보지 않은 파일: 봉투 %d개 · 작업 %d개  (%s)"
                     % (sum(pkg_ext.values()), sum(work_ext.values()), top))
        suggest = [e.lstrip(".") for e, _ in combined.most_common(3) if e.startswith(".")]
        if suggest:
            lines.append("    · 포함하려면: %s"
                         % " ".join("--ext %s" % e for e in suggest))
    excluded = analysis.excluded_dirs
    if excluded:
        shown = ", ".join(safe_path(e) for e in excluded[:4])
        more = "" if len(excluded) <= 4 else " 외 %d개" % (len(excluded) - 4)
        lines.append("  제외한 폴더(아카이브/구버전): %s%s" % (shown, more))
    designated = set(pkg.label for pkg in analysis.packages)
    nested = [d for d in analysis.work_scan.package_dirs if d not in designated]
    if nested:
        lines.append("  작업 폴더 안의 다른 봉투(작업본으로 세지 않음): %s"
                     % ", ".join(safe_path(n) for n in nested[:4]))
    if analysis.symlinks:
        lines.append("  따라가지 않은 심볼릭 링크: %d개 (%s)"
                     % (len(analysis.symlinks),
                        ", ".join(safe_path(x) for x in analysis.symlinks[:3])))
    if analysis.hidden:
        lines.append("  숨김 파일이라 보지 않음: %d개 (%s)"
                     % (len(analysis.hidden),
                        ", ".join(safe_path(x) for x in analysis.hidden[:3])))
    lines.append("  검사 안 함: " + " · ".join(NOT_CHECKED))
    lines.append("  이 툴은 봉투가 최신이라고 보증하지 않습니다 — "
                 "위에 적힌 짝에 대해서만 말합니다.")
    return lines


def render_console(analysis, min_coverage=0.0):
    """콘솔 리포트 전문. 자백이 비면 출력하지 않고 죽는다."""
    lines = []
    lines.append("[세대 점검] %s" % _rel(analysis.work_root))
    lines.append("  작업 폴더 : %s   (대상 파일 %d개)"
                 % (_rel(analysis.work_root), analysis.work_scan.read_count))
    for pkg in analysis.packages:
        lines.append("  봉투      : %s   (대상 파일 %d개)"
                     % (safe_path(pkg.label), pkg.scan.read_count))
    norm_only = len(analysis.normalized_only_rows)
    lines.append("  짝지음 %d쌍 · 일치 %d쌍 · 그중 메타데이터만 달랐던 것 %d쌍"
                 % (analysis.pair_count, len(analysis.same_rows), norm_only))
    lines.append("")

    criticals = analysis.critical_rows
    lines.append("%s — %d쌍" % (verdicts.CRITICAL_STALE_PACKAGE, len(criticals)))
    if criticals:
        # 가장 결정적인 한 줄을 목록 **위**에 둔다 — 아래로 밀면 읽히지 않는다.
        oldest = min(r.pkg_mtime for r in criticals)
        lines.append("  봉투 파일 중 %d개가 %s 이후 갱신되지 않았습니다."
                     % (len(criticals), fmt_time(oldest)))
    for row in criticals[:CONSOLE_MAX_CRITICAL_ITEMS]:
        lines.extend(_render_row(row))
    if len(criticals) > CONSOLE_MAX_CRITICAL_ITEMS:
        lines.append("  … 전체 %d건은 세대불일치.csv" % len(criticals))
    lines.append("")

    warns = analysis.warn_rows
    lines.append("%s — %d쌍" % (verdicts.WARN_PACKAGE_NEWER, len(warns)))
    for row in warns[:CONSOLE_MAX_CRITICAL_ITEMS]:
        lines.append("  %s" % safe_path(row.work_rel))
        lines.append("      봉투 %s  >  작업 %s — 봉투에서 손으로 고쳤을 수 있고, "
                     "그 수정은 작업 폴더에 없습니다."
                     % (fmt_time(row.pkg_mtime), fmt_time(row.work_mtime)))
    if len(warns) > CONSOLE_MAX_CRITICAL_ITEMS:
        lines.append("  … 전체 %d건은 세대불일치.csv" % len(warns))

    undecidable = analysis.undecidable_rows
    lines.append("%s — %d쌍" % (verdicts.UNDECIDABLE, len(undecidable)))
    for row in undecidable[:CONSOLE_MAX_CRITICAL_ITEMS]:
        lines.append("  %s — %s" % (safe_path(row.work_rel), row.reason))
    if len(undecidable) > CONSOLE_MAX_CRITICAL_ITEMS:
        lines.append("  … 전체 %d건은 세대불일치.csv" % len(undecidable))

    lines.append("")
    lines.append("%s — %d건" % ("[경고] 빌드 누락", len(analysis.build_lag)))
    for finding in analysis.build_lag[:CONSOLE_MAX_CRITICAL_ITEMS]:
        lines.append("  %s (%s)  >  %s (%s)"
                     % (safe_path(finding.newer), fmt_time(finding.newer_mtime),
                        safe_path(finding.older), fmt_time(finding.older_mtime)))
    if len(analysis.build_lag) > CONSOLE_MAX_CRITICAL_ITEMS:
        lines.append("  … 외 %d건" % (len(analysis.build_lag) - CONSOLE_MAX_CRITICAL_ITEMS))

    lines.append("%s — %d건" % ("[경고] 그림 형제 세대 불일치", len(analysis.figure_lag)))
    for finding in analysis.figure_lag[:CONSOLE_MAX_CRITICAL_ITEMS]:
        lines.append("  %s (%s)  >  %s (%s)"
                     % (safe_path(finding.newer), fmt_time(finding.newer_mtime),
                        safe_path(finding.older), fmt_time(finding.older_mtime)))
    if len(analysis.figure_lag) > CONSOLE_MAX_CRITICAL_ITEMS:
        lines.append("  … 외 %d건" % (len(analysis.figure_lag) - CONSOLE_MAX_CRITICAL_ITEMS))

    lines.append("")
    confession = coverage_block(analysis, min_coverage)
    if not confession or confession[0] != COVERAGE_HEADER or len(confession) < 3:
        raise ReportIntegrityError(
            "커버리지 자백이 비어 리포트를 출력하지 않습니다. "
            "자백 없는 리포트는 '전부 확인했다'로 읽힙니다."
        )
    lines.extend(confession)
    text = "\n".join(lines)
    if COVERAGE_HEADER not in text:
        raise ReportIntegrityError("커버리지 자백이 리포트에 없습니다.")
    return text


def render_markdown(analysis, min_coverage=0.0, command=""):
    """`세대점검.md` 본문 — 콘솔 리포트를 코드 블록으로 감싼다."""
    body = render_console(analysis, min_coverage=min_coverage)
    header = [
        "# 제출본 세대 점검 — %s" % _rel(analysis.work_root),
        "",
        "생성: %s" % fmt_time(analysis.generated_at),
        "",
        "> `packlist` 는 봉투 안이 약속대로 채워졌는지를 보고, `stalecheck` 는 그 봉투가",
        "> 며칠 전 세계를 담고 있는지를 본다. 봉투는 완벽하게 채워진 채로 한 세대 낡을 수 있다.",
        "",
    ]
    if command:
        header.extend(["실행: `%s`" % command, ""])
    # 본문 안의 백틱 연속열보다 긴 펜스를 써서, 어떤 파일 이름도 펜스를 닫지 못하게 한다.
    longest = max([len(m) for m in re.findall(r"`+", body)] or [0])
    fence = "`" * max(3, longest + 1)
    return "\n".join(header) + fence + "\n" + body + "\n" + fence + "\n"


MISMATCH_HEADER = ("판정", "작업경로", "봉투경로", "작업mtime", "봉투mtime",
                   "작업크기", "봉투크기", "행추가", "행삭제", "정규화적용",
                   "짝짓기근거", "사유")

MATCH_HEADER = ("작업경로", "봉투경로", "작업mtime", "봉투mtime",
                "정규화적용", "짝짓기근거")

UNPAIRED_HEADER = ("구분", "경로", "mtime", "크기", "메모")

SIBLING_HEADER = ("판정", "최신파일", "낡은파일", "최신mtime", "낡은mtime", "차이_초")


def mismatch_rows(analysis):
    """`세대불일치.csv` 행 — 일치는 빼고 판정이 붙은 것만."""
    rows = []
    for row in analysis.rows:
        if row.verdict.label == verdicts.SAME:
            continue
        added = removed = ""
        if row.diff is not None and row.diff.comparable:
            added, removed = row.diff.added, row.diff.removed
        elif row.diff is not None:
            added = removed = row.diff.reason
        rows.append((row.verdict.label, row.work_rel, row.pkg_rel,
                     fmt_time(row.work_mtime), fmt_time(row.pkg_mtime),
                     row.work_size, row.pkg_size, added, removed,
                     row.normalized, row.how, row.reason))
    return rows


def match_rows(analysis):
    """`일치목록.csv` 행 — 오탐 억제 회귀에 쓰는 조용한 증거."""
    rows = []
    for row in analysis.same_rows:
        rows.append((row.work_rel, row.pkg_rel, fmt_time(row.work_mtime),
                     fmt_time(row.pkg_mtime), row.normalized, row.how))
    for label, pair in analysis.renamed_identical:
        rows.append((pair.work.rel, os.path.join(label, pair.pkg.rel),
                     fmt_time(pair.work.mtime), fmt_time(pair.pkg.mtime),
                     pair.normalized, pair.how))
    return rows


def unpaired_rows(analysis):
    """`짝없음.csv` 행 — 봉투에만/작업폴더에만 있는 파일."""
    rows = []
    for pkg in analysis.packages:
        for rec in pkg.pairing.pkg_only:
            rows.append(("봉투에만 있음", os.path.join(pkg.label, rec.rel),
                         fmt_time(rec.mtime), rec.size, ""))
        for rec, candidates in pkg.pairing.ambiguous:
            rows.append(("이름중복으로 판정 안 함", os.path.join(pkg.label, rec.rel),
                         fmt_time(rec.mtime), rec.size,
                         "작업 폴더에 같은 이름 %d개" % len(candidates)))
    for rec in analysis.work_only:
        rows.append(("작업폴더에만 있음", rec.rel, fmt_time(rec.mtime), rec.size, ""))
    return rows


def sibling_rows(analysis):
    """`형제점검.csv` 행 — 빌드 누락과 그림 형제 불일치."""
    rows = []
    for finding in list(analysis.build_lag) + list(analysis.figure_lag):
        rows.append((finding.kind, finding.newer, finding.older,
                     fmt_time(finding.newer_mtime), fmt_time(finding.older_mtime),
                     int(finding.lag)))
    return rows
