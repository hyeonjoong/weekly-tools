"""long CSV 입력 경로 (한 행 = 한 문항).

같은 개념이 XLSX 가 아니라 긴 CSV 로 오는 경우가 있다(예: 웹 검사도구가 뽑아
주는 시행 단위 로그). 블록·패스를 자를 필요가 없을 뿐 나머지 검사는 완전히
같은 코드를 탄다.

**문항 단위가 아니면(= 제시/응답 열이 없고 % 만 있으면) 판정하지 않고 거절한다.**
"""

from __future__ import annotations

import csv
import io
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

from .hangul import normalize
from .reader import (RESPONSE_ALIASES, SCORE_ALIASES, Item, LayoutError, Pass,
                     parse_number)
from .safeio import read_text_any
from .spec import SubtestSpec

STIMULUS_ALIASES = ("제시", "제시자극", "자극", "목표", "target", "stimulus",
                    "stim", "item_target", "word")
ID_ALIASES = ("id", "피험자", "대상", "대상자", "subject", "participant",
              "s_account", "sid", "환자id")
TIME_ALIASES = ("시점", "회기", "timepoint", "time", "session", "visit",
                "phase", "testset")
TEST_ALIASES = ("검사", "하위검사", "subtest", "test", "블록", "block", "condition")
#: 요약 % 만 든 파일에서 흔한 열 이름 — 이게 보이고 문항 열이 없으면 거절한다.
PERCENT_HINTS = ("%", "percent", "정답률", "점수율", "score_pct", "_전", "_후")


class LongCsvError(LayoutError):
    pass


def _match(name: str, aliases: Sequence[str]) -> bool:
    clean = normalize(name).lower().replace(" ", "").replace("_", "")
    if not clean:
        return False
    return any(clean == a.lower().replace("_", "")
               or clean.startswith(a.lower().replace("_", "")) for a in aliases)


def _explicit(header: Sequence[str], explicit: Optional[str]) -> Optional[int]:
    if not explicit:
        return None
    for i, name in enumerate(header):
        if normalize(name) == normalize(explicit):
            return i
    raise LongCsvError("CSV 에 '%s' 열이 없습니다. 실제 열: %s"
                       % (explicit, ", ".join(header)))


def _pick(header: Sequence[str], aliases: Sequence[str],
          used: Optional[set] = None) -> Optional[int]:
    """이름 별칭으로 열을 고른다. **이미 다른 역할로 정해진 열은 건드리지 않는다.**

    (`testset` 처럼 시점과 검사 양쪽 별칭에 걸리는 이름이 실제로 있다.)
    """
    used = used or set()
    for i, name in enumerate(header):
        if i in used:
            continue
        if _match(name, aliases):
            return i
    return None


def sniff_rows(path: str) -> Tuple[List[str], List[List[str]]]:
    text = read_text_any(path)
    if not text.strip():
        raise LongCsvError("CSV 가 비어 있습니다.")
    sample = text[:8192].replace("\r\n", "\n").replace("\r", "\n")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    # 엑셀 for Mac 의 "CSV (Macintosh)" 는 줄바꿈이 CR 하나뿐이라 csv 모듈이
    # '따옴표 없는 필드에 개행' 오류를 낸다. 미리 통일한다.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        rows = [r for r in reader]
    except csv.Error as exc:
        raise LongCsvError(
            "CSV 를 읽지 못했습니다: %s\n"
            "  칸 하나가 너무 길거나 따옴표가 맞지 않는 것 같습니다." % exc)
    if not rows:
        raise LongCsvError("CSV 에서 행을 읽지 못했습니다.")
    header = [normalize(c) for c in rows[0]]
    return header, rows[1:]


def load(path: str, specs: List[SubtestSpec], timepoints: Sequence[str],
         id_col: Optional[str] = None, time_col: Optional[str] = None,
         test_col: Optional[str] = None, stim_col: Optional[str] = None,
         resp_col: Optional[str] = None, score_col: Optional[str] = None):
    """long CSV 를 :class:`~jamoscore.reader.Pass` 목록으로."""
    header, body = sniff_rows(path)
    # 사용자가 직접 지정한 열을 **먼저** 확정한 뒤, 남은 열에서만 자동 검출한다.
    fixed = {
        "stim": _explicit(header, stim_col), "resp": _explicit(header, resp_col),
        "score": _explicit(header, score_col), "id": _explicit(header, id_col),
        "time": _explicit(header, time_col), "test": _explicit(header, test_col),
    }
    used = {i for i in fixed.values() if i is not None}
    i_stim = fixed["stim"] if fixed["stim"] is not None else _pick(
        header, STIMULUS_ALIASES, used)
    if i_stim is not None:
        used.add(i_stim)
    i_resp = fixed["resp"] if fixed["resp"] is not None else _pick(
        header, RESPONSE_ALIASES, used)
    if i_resp is not None:
        used.add(i_resp)
    i_score = fixed["score"] if fixed["score"] is not None else _pick(
        header, SCORE_ALIASES, used)
    if i_score is not None:
        used.add(i_score)
    if i_stim is None or i_resp is None or i_score is None:
        missing = [n for n, v in (("제시/자극", i_stim), ("응답", i_resp),
                                  ("점수", i_score)) if v is None]
        looks_summary = any(
            any(h in normalize(c).lower() for h in PERCENT_HINTS) for c in header)
        raise LongCsvError(
            "문항 단위 열(%s)을 찾지 못했습니다.\n"
            "  실제 열 이름: %s\n%s"
            % (", ".join(missing), ", ".join(header),
               "  이 파일은 문항이 아니라 요약 %를 담고 있는 것으로 보입니다 — "
               "jamoscore 는 할 말이 없습니다.\n"
               "  군/시점 비교는 statwise · longistat 를 쓰세요."
               if looks_summary else
               "  --long-stim / --long-response / --long-score 로 열 이름을 "
               "직접 알려 주세요."))
    i_id = fixed["id"] if fixed["id"] is not None else _pick(
        header, ID_ALIASES, used)
    if i_id is not None:
        used.add(i_id)
    i_time = fixed["time"] if fixed["time"] is not None else _pick(
        header, TIME_ALIASES, used)
    if i_time is not None:
        used.add(i_time)
    i_test = fixed["test"] if fixed["test"] is not None else _pick(
        header, TEST_ALIASES, used)
    if i_id is None:
        raise LongCsvError(
            "피험자 ID 열을 찾지 못했습니다. 실제 열: %s\n"
            "  --long-id 로 알려 주세요." % ", ".join(header))

    spec_by_name = {}
    for spec in specs:
        spec_by_name.setdefault(spec.name, spec)
    default_spec = specs[0] if specs else None

    passes: "OrderedDict[tuple, Pass]" = OrderedDict()
    unknown_tests = set()
    for lineno, row in enumerate(body, start=2):
        if not any(normalize(c) for c in row):
            continue
        def cell(idx):
            return normalize(row[idx]) if idx is not None and idx < len(row) else ""
        subject = cell(i_id)
        if not subject:
            continue
        test = cell(i_test) if i_test is not None else (
            default_spec.name if default_spec else "검사")
        tp = cell(i_time) if i_time is not None else timepoints[0]
        spec = spec_by_name.get(test)
        if spec is None:
            unknown_tests.add(test)
            continue
        key = (subject, test, tp, spec.unit)
        if key not in passes:
            passes[key] = Pass(subject, test, tp, spec,
                               "%s" % _basename(path), 1)
        p = passes[key]
        p.items.append(Item(
            subject=subject, test=test, timepoint=tp, unit=spec.unit,
            seq=len(p.items) + 1, row=lineno, col=1,
            sheet=_basename(path),
            stimulus=cell(i_stim), response=cell(i_resp),
            human_raw=cell(i_score)))
    if not passes:
        raise LongCsvError(
            "선언한 --subtest 이름(%s)과 맞는 행이 하나도 없습니다.\n"
            "  CSV 의 검사 이름: %s"
            % (", ".join(sorted(spec_by_name)) or "(없음)",
               ", ".join(sorted(unknown_tests)[:10]) or "(검사 열 없음)"))
    return list(passes.values()), sorted(unknown_tests)


def _basename(path: str) -> str:
    import os
    return os.path.basename(path)
