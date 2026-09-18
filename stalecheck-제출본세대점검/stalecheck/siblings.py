"""형제 파일 점검 — 같은 폴더의 같은 이름, 다른 확장자.

원고는 ``.md`` 로 쓰고 ``.docx`` 로 굽는다. 그림은 ``.png`` 와 ``.pdf`` 가 늘 쌍으로
나온다. 한쪽만 다시 만드는 일이 실제로 생기고, 그러면 **봉투는 멀쩡한데 소스와
산출물이 어긋난 채** 제출된다. 이건 봉투↔작업 비교로는 안 잡힌다.
"""

import os
from collections import defaultdict

WARN_BUILD_STALE = "[경고] 빌드 누락"
WARN_FIGURE_SIBLING = "[경고] 그림 형제 세대 불일치"

#: ``.md`` 가 ``.docx`` 보다 이만큼 더 최신이면 "굽는 걸 잊었다"로 본다.
BUILD_LAG_SEC = 60.0

#: 같은 그림의 두 포맷이 이보다 벌어지면 다른 세대로 본다.
#: 한 번의 빌드 스크립트로 두 포맷을 같이 저장하면 보통 1초 안쪽이다.
FIGURE_LAG_SEC = 3600.0

SOURCE_TO_BUILD = (("md", "docx"), ("tex", "pdf"))
FIGURE_FORMATS = ("png", "pdf")


class SiblingFinding(object):
    __slots__ = ("kind", "newer", "older", "newer_mtime", "older_mtime", "lag")

    def __init__(self, kind, newer, older, newer_mtime, older_mtime):
        self.kind = kind
        self.newer = newer
        self.older = older
        self.newer_mtime = newer_mtime
        self.older_mtime = older_mtime
        self.lag = newer_mtime - older_mtime


def _group_by_stem(records):
    groups = defaultdict(dict)
    for rec in records:
        directory = os.path.dirname(rec.rel)
        stem, ext = os.path.splitext(os.path.basename(rec.rel))
        groups[(directory, stem.casefold())][ext.lower().lstrip(".")] = rec
    return groups


def find_build_lag(records, lag=BUILD_LAG_SEC):
    """소스(``.md``/``.tex``)가 산출물(``.docx``/``.pdf``)보다 최신인 경우."""
    findings = []
    for _, by_ext in sorted(_group_by_stem(records).items()):
        for src_ext, out_ext in SOURCE_TO_BUILD:
            src = by_ext.get(src_ext)
            out = by_ext.get(out_ext)
            if src is None or out is None:
                continue
            if src.mtime - out.mtime > lag:
                findings.append(SiblingFinding(WARN_BUILD_STALE, src.rel, out.rel,
                                               src.mtime, out.mtime))
    return findings


def find_figure_sibling_lag(records, lag=FIGURE_LAG_SEC):
    """같은 그림의 ``.png`` / ``.pdf`` 가 서로 다른 세대인 경우."""
    findings = []
    a_ext, b_ext = FIGURE_FORMATS
    for _, by_ext in sorted(_group_by_stem(records).items()):
        a = by_ext.get(a_ext)
        b = by_ext.get(b_ext)
        if a is None or b is None:
            continue
        if abs(a.mtime - b.mtime) <= lag:
            continue
        newer, older = (a, b) if a.mtime > b.mtime else (b, a)
        findings.append(SiblingFinding(WARN_FIGURE_SIBLING, newer.rel, older.rel,
                                       newer.mtime, older.mtime))
    return findings
