"""CSV 읽기 · 열 이름 표준화 · 지표 자동 판별.

현실의 메타분석 추출표는 열 이름이 제각각(RevMan, metafor, R meta 패키지, 손으로 만든 엑셀)이라
흔한 이름들을 표준 이름으로 매핑한다. 매핑되지 않으면 ``--map 원본열=표준열`` 로 지정할 수 있다.
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .effects import MEASURES, REQUIRED_COLUMNS

__all__ = ["read_table", "detect_measure", "CANONICAL_ALIASES", "TableError", "canonical_columns"]

_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1")

#: 표준 열 이름 → 허용하는 원본 열 이름들 (비교는 소문자·영숫자만 남긴 형태로 한다)
CANONICAL_ALIASES: Dict[str, Tuple[str, ...]] = {
    "study": ("study", "studies", "studyid", "studylabel", "label", "name", "author",
              "authoryear", "trial", "id", "연구", "연구명", "저자", "논문"),
    "subgroup": ("subgroup", "group", "moderator", "mod", "category", "cat", "stratum",
                 "하위군", "그룹", "분류"),
    "effect": ("effect", "effectsize", "es", "yi", "y", "te", "estimate", "smd", "md",
                "g", "hedgesg", "cohend", "logor", "lnor", "logrr", "lnrr", "효과크기"),
    "se": ("se", "sei", "sete", "stderr", "standarderror", "seeffect", "표준오차"),
    "ci_low": ("cilow", "cilower", "cilb", "lower", "lowerci", "lcl", "ll", "low", "ci95low",
               "lowerlimit", "하한"),
    "ci_high": ("cihigh", "ciupper", "ciub", "upper", "upperci", "ucl", "ul", "high", "ci95high",
                "upperlimit", "상한"),
    "n": ("n", "ntotal", "total", "totaln", "표본수"),
    "n1": ("n1", "n1i", "ne", "nexp", "nexperimental", "ntreat", "ntreatment", "nt", "nintervention",
           "group1n", "n1group", "실험군n", "처치군n", "실험군수", "처치군수", "실험군표본수"),
    "mean1": ("mean1", "m1", "m1i", "meane", "meanexp", "meantreat", "mean1group", "실험군평균"),
    "sd1": ("sd1", "s1", "sd1i", "sde", "sdexp", "sdtreat", "sd1group", "실험군sd"),
    "n2": ("n2", "n2i", "nc", "ncontrol", "nctrl", "ncomparator", "nplacebo", "group2n",
           "n2group", "대조군n", "대조군수", "비교군수", "대조군표본수"),
    "mean2": ("mean2", "m2", "m2i", "meanc", "meancontrol", "meanctrl", "mean2group", "대조군평균"),
    "sd2": ("sd2", "s2", "sd2i", "sdc", "sdcontrol", "sdctrl", "sd2group", "대조군sd"),
    "events1": ("events1", "event1", "e1", "eventse", "evente", "eventexp", "eventtreat",
                "r1", "x1", "ai", "사건1", "실험군사건"),
    "events2": ("events2", "event2", "e2", "eventsc", "eventc", "eventcontrol", "eventctrl",
                "r2", "x2", "ci2", "사건2", "대조군사건"),
}

# 2x2 표를 a/b/c/d(칸 빈도)로 적는 관행: a=1군 사건, b=1군 비사건, c=2군 사건, d=2군 비사건
_CELL_FORM = ("a", "b", "c", "d")


class TableError(ValueError):
    """CSV를 읽거나 해석할 수 없는 경우."""


def _compact(name: str) -> str:
    """열 이름 비교용 정규화: 소문자 + 영숫자/한글만 남김."""
    return re.sub(r"[^0-9a-z가-힣]+", "", (name or "").strip().lower())


def _build_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for canon, aliases in CANONICAL_ALIASES.items():
        lookup[_compact(canon)] = canon
        for alias in aliases:
            key = _compact(alias)
            # 먼저 등록된 쪽을 우선(위 표의 순서가 곧 우선순위)
            lookup.setdefault(key, canon)
    return lookup


_LOOKUP = _build_lookup()


def canonical_columns() -> List[str]:
    return sorted(CANONICAL_ALIASES)


#: 읽어들일 최대 크기 (CSV 추출표는 아무리 커도 수 MB — 그 이상은 잘못 지정한 파일이다)
MAX_BYTES = 256 * 1024 * 1024


def _decode(path: str) -> "Tuple[str, List[str]]":
    if not os.path.exists(path):
        raise TableError("파일을 찾을 수 없습니다: %s" % path)
    if os.path.isdir(path):
        raise TableError("폴더가 아니라 CSV 파일 경로를 지정하세요: %s" % path)
    if not os.path.isfile(path):
        # /dev/zero, 이름있는 파이프 등을 읽으면 영원히 멈춘다.
        raise TableError("일반 파일이 아닙니다 (장치·파이프 등): %s" % path)
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise TableError(
            "파일이 너무 큽니다 (%.1f MB > %d MB). CSV 추출표가 맞는지 확인하세요: %s"
            % (size / 1048576.0, MAX_BYTES // 1048576, path)
        )
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise TableError("파일이 너무 큽니다 (%d MB 초과): %s" % (MAX_BYTES // 1048576, path))
    if not raw.strip():
        raise TableError("파일이 비어 있습니다: %s" % path)

    warnings: List[str] = []
    head = raw[:1000]
    looks_utf16 = raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (
        head.count(b"\x00") > 0.2 * len(head)   # BOM 없는 UTF-16: 널 바이트가 절반 가까이
    )
    if looks_utf16:
        # 엑셀의 "유니코드 텍스트(*.txt)" 저장 형식
        for enc in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc), warnings
            except UnicodeDecodeError:
                continue
        raise TableError(
            "UTF-16으로 보이는데 해독하지 못했습니다. 엑셀에서 'CSV UTF-8'로 다시 저장해 주세요: %s" % path
        )
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "latin-1":
            warnings.append(
                "파일 인코딩을 알아내지 못해 latin-1로 읽었습니다 — 한글이 깨져 보일 수 있습니다. "
                "엑셀에서 'CSV UTF-8'로 다시 저장하는 것을 권합니다."
            )
        elif enc in ("cp949", "euc-kr"):
            warnings.append("파일을 %s(한글 윈도우) 인코딩으로 읽었습니다." % enc)
        return text, warnings
    # latin-1은 실패하지 않으므로 여기 도달하지 않지만 방어적으로 남긴다.
    raise TableError("파일 인코딩을 인식할 수 없습니다: %s" % path)  # pragma: no cover


def _sniff_delimiter(sample: str) -> str:
    first = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {d: first.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def read_table(
    path: str,
    mapping: Optional[Dict[str, str]] = None,
    label_column: Optional[str] = None,
    subgroup_column: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[str], List[str]]:
    """CSV를 읽어 ``(표준화된 레코드, 원본 헤더, 경고)`` 를 반환한다.

    각 레코드에는 원본 파일에서의 행 번호가 ``__row__`` 로 들어간다(헤더=1행).
    """
    text, warnings = _decode(path)
    delim = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise TableError("CSV를 해석할 수 없습니다: %s" % exc)
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise TableError("데이터 행이 없습니다: %s" % path)

    header = [(c or "").strip().lstrip("﻿") for c in rows[0]]
    explicit = {_compact(k): v for k, v in (mapping or {}).items()}
    for canon in explicit.values():
        if canon not in CANONICAL_ALIASES:
            raise TableError(
                "--map 의 표준 열 이름이 잘못되었습니다: %r (가능: %s)"
                % (canon, ", ".join(canonical_columns()))
            )

    # 원본 열 인덱스 → 표준 이름
    index_to_canon: Dict[int, str] = {}
    used: Dict[str, int] = {}
    for i, name in enumerate(header):
        key = _compact(name)
        canon = explicit.get(key) or _LOOKUP.get(key)
        if label_column and name == label_column:
            canon = "study"
        if subgroup_column and name == subgroup_column:
            canon = "subgroup"
        if not canon:
            continue
        if canon in used:
            warnings.append(
                "열 '%s'와 '%s'가 모두 '%s'로 해석되어 앞의 열만 사용합니다."
                % (header[used[canon]], name, canon)
            )
            continue
        used[canon] = i
        index_to_canon[i] = canon

    unused_map = [k for k in explicit if k not in {_compact(h) for h in header}]
    if unused_map:
        warnings.append(
            "--map 에 적은 열이 파일에 없습니다: %s (파일의 열: %s)"
            % (", ".join(unused_map), ", ".join(header))
        )

    if label_column and label_column not in header:
        raise TableError("--label 로 지정한 열 '%s'이 파일에 없습니다. (있는 열: %s)"
                         % (label_column, ", ".join(header)))
    if subgroup_column and subgroup_column not in header:
        raise TableError("--subgroup 으로 지정한 열 '%s'이 파일에 없습니다. (있는 열: %s)"
                         % (subgroup_column, ", ".join(header)))

    # a/b/c/d 2x2 형식 지원 (사건수 열이 따로 없을 때만)
    cell_idx = {c: i for i, name in enumerate(header) for c in _CELL_FORM if _compact(name) == c}
    cell_form = len(cell_idx) == 4 and "events1" not in used

    records: List[Dict[str, str]] = []
    ncols = len(header)
    for r, row in enumerate(rows[1:], start=2):
        if len(row) > ncols:
            warnings.append("행 %d: 열 수가 헤더보다 많아 뒤쪽을 잘라냈습니다." % r)
            row = row[:ncols]
        rec: Dict[str, str] = {"__row__": str(r)}
        for i, canon in index_to_canon.items():
            rec[canon] = (row[i] if i < len(row) else "").strip()
        if cell_form:
            vals = {c: (row[cell_idx[c]] if cell_idx[c] < len(row) else "").strip() for c in _CELL_FORM}
            rec["events1"] = vals["a"]
            rec["events2"] = vals["c"]
            rec["n1"] = _sum_text(vals["a"], vals["b"])
            rec["n2"] = _sum_text(vals["c"], vals["d"])
        records.append(rec)

    if not records:
        raise TableError("헤더만 있고 데이터 행이 없습니다: %s" % path)
    return records, header, warnings


def _sum_text(x: str, y: str) -> str:
    try:
        return repr(float(x) + float(y))
    except (TypeError, ValueError):
        return ""


def detect_measure(records: Sequence[Dict[str, str]], header: Sequence[str]) -> str:
    """사용 가능한 열로부터 지표를 추론한다.

    이분형(사건수) → 'or', 연속형(평균/SD) → 'smd', 이미 계산된 효과 → 'generic'.
    """
    present = set()
    for rec in records:
        for key, value in rec.items():
            if key != "__row__" and (value or "").strip():
                present.add(key)

    def has(cols):
        return all(c in present for c in cols)

    if has(REQUIRED_COLUMNS["or"]):
        return "or"
    if has(REQUIRED_COLUMNS["smd"]):
        return "smd"
    if "effect" in present and ("se" in present or {"ci_low", "ci_high"} <= present):
        return "generic"
    raise TableError(
        "지표를 자동으로 판별하지 못했습니다.\n"
        "  인식된 표준 열: %s\n"
        "  파일의 열: %s\n"
        "  필요한 조합 중 하나:\n"
        "    · 이미 계산된 효과크기: effect + se (또는 ci_low, ci_high)\n"
        "    · 연속형 2군: n1, mean1, sd1, n2, mean2, sd2\n"
        "    · 이분형 2군: events1, n1, events2, n2\n"
        "  열 이름이 다르면 --map 원본열=표준열 로 알려주세요 (예: --map 실험군수=n1)."
        % (", ".join(sorted(present - {"__row__"})) or "(없음)", ", ".join(header))
    )


def validate_measure(records: Sequence[Dict[str, str]], measure: str) -> None:
    """지정한 지표에 필요한 열이 실제로 있는지 확인."""
    if measure not in MEASURES:
        raise TableError("알 수 없는 지표: %r (가능: %s)" % (measure, ", ".join(MEASURES)))
    present = {k for rec in records for k, v in rec.items() if k != "__row__" and (v or "").strip()}
    if measure == "generic":
        if "effect" not in present:
            raise TableError("--measure generic 에는 effect 열이 필요합니다.")
        if "se" not in present and not {"ci_low", "ci_high"} <= present:
            raise TableError("--measure generic 에는 se 열 또는 ci_low/ci_high 열이 필요합니다.")
        return
    missing = [c for c in REQUIRED_COLUMNS[measure] if c not in present]
    if missing:
        raise TableError(
            "--measure %s 에 필요한 열이 없습니다: %s (필요: %s)"
            % (measure, ", ".join(missing), ", ".join(REQUIRED_COLUMNS[measure]))
        )
