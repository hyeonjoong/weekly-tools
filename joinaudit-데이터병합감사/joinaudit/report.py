"""산출물 — 이 툴이 파는 것은 병합 결과가 아니라 **증거**다.

`merged.csv` 는 pandas 세 줄로도 만들 수 있다. pandas 가 절대 주지 않는 것은
"입력 662행 → 최종 248행, 차이 414행의 내역" 이라는 문장과 그 근거가 되는 행
단위 목록이고, 그게 여기서 나온다.

쓰는 파일
--------
* `merged.csv`   — 분석용 표. 하류 툴(statwise/table1/longistat)에 그대로 투입.
* `병합감사.md`  — N-흐름, 파일별 요약, 드롭 사유, 커버리지, 적용 규칙, Methods 초안.
* `문제목록.csv` — 파일·행번호·키·심각도·유형·설명·권고.
* `키매칭표.csv` — 피험자 × 파일 커버리지(1/0).

안전
----
* **입력 파일을 덮어쓰지 않는다.** 출력 경로가 입력 파일과 같으면 거부한다.
* **CSV 수식 인젝션 방어** — `= + - @`, 탭/CR 로 시작하는 **비숫자** 셀 앞에
  `'` 를 붙인다. 숫자는 건드리지 않는다(`-3.2` 를 `'-3.2` 로 만들면 하류 통계
  툴이 전부 결측으로 읽는다).
* **오류 메시지에 개인식별정보를 넣지 않는다** — 사람 이름·연락처가 들어올 수
  있는 원본 셀 값은 리포트의 '설명' 칸에만, 그것도 잘라서 넣는다.
"""

from __future__ import annotations

import csv
import datetime as _dt
import errno
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .dataio import is_missing
from .detect import BY_CONTENT, EXPLICIT
from .issues import CRITICAL, CSV_HEADER, INFO, WARNING, IssueLog
from .merge import (DISPOSITIONS, DROP_DATE_PARSE, DROP_DUPLICATE, DROP_NO_KEY,
                    DROP_SUBJECT_UNMATCHED, DROP_TIME_CONFLICT,
                    DROP_TIME_UNMATCHED, DUP_MERGED, USED, FilePlan,
                    MergeResult)

__all__ = [
    "OUTPUT_NAMES", "OutputError", "prepare_out_dir", "escape_cell",
    "write_merged", "write_issues", "write_coverage", "write_audit",
    "screen_summary", "methods_draft", "verify_downstream_schema",
    "nflow_rows",
]

OUTPUT_NAMES = ("merged.csv", "병합감사.md", "문제목록.csv", "키매칭표.csv")


def md_cell(value: str) -> str:
    """마크다운 표 칸에 넣을 수 있게. `|` 하나가 표 전체를 어긋나게 만든다.

    이 리포트는 IRB·Methods 근거로 쓰이므로, 조용히 어긋난 커버리지 표는 이 툴이
    막으려는 바로 그 실패다.
    """
    text = " ".join(str(value).split())
    return text.replace("\\", "\\\\").replace("|", "\\|")

# CSV 수식 인젝션: 엑셀/Numbers/LibreOffice 가 수식으로 해석하는 시작 문자.
_RISKY_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class OutputError(RuntimeError):
    """산출물을 쓸 수 없을 때 — 메시지는 사람이 조치할 수 있는 한국어."""


# --------------------------------------------------------------------------
# 안전한 쓰기
# --------------------------------------------------------------------------

def escape_cell(value: str) -> str:
    """CSV 수식 인젝션 방어. 숫자로 읽히는 셀은 그대로 둔다."""
    text = "" if value is None else str(value)
    if not text:
        return text
    if _NUMBER_RE.match(text.strip()):
        return text
    if text[0] in _RISKY_PREFIXES:
        return "'" + text
    return text


def prepare_out_dir(out_dir: str, input_paths: Sequence[str]) -> str:
    """출력 폴더를 만들고, 입력 파일을 덮어쓸 위험이 없는지 검사한다.

    반환값은 실제 경로(realpath). 상위 폴더로 빠져나가는 `..` 자체는 막지 않는다
    (사용자가 자기 컴퓨터에서 원하는 곳에 쓸 수 있어야 한다). 대신 **쓰려는 네
    파일 중 하나라도 입력 파일과 같은 실제 경로**면 거부한다.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"결과 폴더를 만들 수 없습니다: {out_dir} ({exc})")
    if not os.path.isdir(out_dir):
        raise OutputError(f"'{out_dir}' 은(는) 폴더가 아닙니다.")

    real_dir = os.path.realpath(out_dir)
    inputs = {os.path.realpath(p) for p in input_paths}
    input_ids = {i for i in (_file_identity(p) for p in input_paths) if i}
    for name in OUTPUT_NAMES:
        target = os.path.join(real_dir, name)
        # 경로 문자열이 다르다고 다른 파일인 것이 아니다. macOS(APFS)는 대소문자를
        # 구분하지 않고(`MERGED.CSV`), 한글 파일 이름은 NFC/NFD 두 표기가 같은
        # 파일을 가리키며, 하드링크는 이름이 아예 다르다. **장치+아이노드**로
        # 봐야 세 경우가 한 번에 잡힌다.
        if os.path.realpath(target) in inputs:
            raise OutputError(_overwrite_message(name))
        ident = _file_identity(target)
        if ident and ident in input_ids:
            raise OutputError(_overwrite_message(name))
    if not os.access(real_dir, os.W_OK):
        raise OutputError(f"결과 폴더에 쓸 권한이 없습니다: {out_dir}")
    return real_dir


def _file_identity(path: str):
    """(장치, 아이노드). 파일이 없으면 None."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _overwrite_message(name: str) -> str:
    return (f"결과 파일 '{name}' 이 입력 파일과 **같은 파일**입니다"
            "(이름이 달라도 대소문자·한글 정규화·하드링크로 같을 수 있습니다). "
            "이 툴은 원본을 절대 덮어쓰지 않습니다 — `--out-dir` 을 다른 "
            "폴더로 지정하세요.")


def _open_new(path: str, encoding: str):
    """심볼릭 링크를 따라가지 않고, 소유자만 읽을 수 있게 연다.

    `open(path, "w")` 는 링크를 따라가 **엉뚱한 파일을 잘라 버린다.** 임상
    자료를 다루므로 권한도 0600 으로 좁힌다(기본 umask 는 0644 를 만든다).
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.ELOOP, errno.EMLINK):
            raise OutputError(
                f"'{os.path.basename(path)}' 이 심볼릭 링크입니다. 링크를 따라가면 "
                "엉뚱한 파일을 덮어쓰게 되므로 쓰지 않았습니다 — 링크를 지우거나 "
                "`--out-dir` 을 다른 폴더로 지정하세요.")
        raise OutputError(
            f"'{os.path.basename(path)}' 을(를) 쓸 수 없습니다: {exc}")
    return os.fdopen(fd, "w", encoding=encoding, newline="")


def _write_csv(path: str, header: Sequence[str],
               rows: Sequence[Sequence[str]]) -> None:
    """UTF-8 BOM 으로 쓴다 — 한국어 엑셀이 BOM 없는 UTF-8 CSV 를 깨뜨린다."""
    try:
        with _open_new(path, "utf-8-sig") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow([escape_cell(c) for c in header])
            for row in rows:
                writer.writerow([escape_cell(c) for c in row])
    except OSError as exc:
        raise OutputError(f"'{os.path.basename(path)}' 을(를) 쓸 수 없습니다: {exc}")


def _write_text(path: str, text: str) -> None:
    try:
        with _open_new(path, "utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        raise OutputError(f"'{os.path.basename(path)}' 을(를) 쓸 수 없습니다: {exc}")


# --------------------------------------------------------------------------
# merged.csv
# --------------------------------------------------------------------------

def write_merged(result: MergeResult, path: str, long_format: bool = False
                 ) -> None:
    """분석용 표를 쓴다.

    기본은 wide(1행 = 피험자×시점). `--long` 이면
    `subject_id,timepoint,variable,value` 네 열의 EAV(entity-attribute-value)
    형식 — 결측은 행 자체를 만들지 않는다.

    주의: 이 EAV 형식은 **`longistat` 에 그대로 들어가지 않는다.** `longistat`
    은 값 열의 *이름*을 `--value` 로 받으므로 wide 출력(기본값)을 써야 한다.
    `--long` 은 사람이 훑어보거나 직접 피벗할 때를 위한 것이다.
    """
    if not long_format:
        # README 가 약속한 대로 결측은 **빈 칸**으로 낸다. `NA` / `.` 를 그대로
        # 흘려보내면 pandas 가 그 열을 통째로 문자형으로 읽고, 이 파일 자신의
        # 스키마 자체 검증도 실패한다(평범한 SPSS/R 내보내기가 전부 걸린다).
        rows = [[("" if is_missing(cell) else cell) for cell in row]
                for row in result.rows]
        _write_csv(path, result.header, rows)
        return

    header = ["subject_id", "timepoint", "variable", "value"]
    rows: List[List[str]] = []
    for row in result.rows:
        subject, timepoint = row[0], row[1]
        for col, value in zip(result.header[2:], row[2:]):
            if is_missing(value):
                continue
            rows.append([subject, timepoint, col, value.strip()])
    _write_csv(path, header, rows)


def verify_downstream_schema(path: str) -> List[str]:
    """만든 `merged.csv` 가 하류 툴이 요구하는 모양인지 **자기 검증**한다.

    다른 툴의 코드를 import 하지 않는다. `statwise`/`table1`/`longistat` 이
    공통으로 요구하는 조건만 파일을 다시 읽어 확인한다.

    1. 헤더는 정확히 한 줄이고 비어 있지 않다.
    2. 열 이름이 중복되지 않는다(중복되면 pandas 가 조용히 하나만 남긴다).
    3. 모든 행의 열 수가 헤더와 같다(1행 = 1관측).
    4. 결측은 `NA`/`.` 같은 토큰이 아니라 **빈 칸**이다.
    5. 열 이름에 줄바꿈이 없다.

    문제 문자열 목록을 돌려준다. 빈 목록 = 조건 만족.
    """
    problems: List[str] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
    except OSError as exc:
        return [f"merged.csv 를 다시 읽을 수 없습니다: {exc}"]

    if not rows:
        return ["merged.csv 가 비어 있습니다."]
    header = rows[0]
    if not header or not any(h.strip() for h in header):
        problems.append("헤더 행이 비어 있습니다.")
    dupes = sorted({h for h in header if header.count(h) > 1})
    if dupes:
        problems.append("열 이름이 중복됩니다: " + ", ".join(dupes))
    if any("\n" in h or "\r" in h for h in header):
        problems.append("열 이름에 줄바꿈이 들어 있습니다.")

    width = len(header)
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            problems.append(
                f"{i}행의 열 수({len(row)})가 헤더({width})와 다릅니다.")
            break
    bad_missing = set()
    for row in rows[1:]:
        for cell in row:
            token = cell.strip().upper()
            if token in ("NA", "N/A", "NAN", "NULL", "NONE", "."):
                bad_missing.add(cell.strip())
    if bad_missing:
        problems.append(
            "결측이 빈 칸이 아니라 토큰으로 들어 있습니다: " +
            ", ".join(sorted(bad_missing)))
    return problems


# --------------------------------------------------------------------------
# 문제목록.csv / 키매칭표.csv
# --------------------------------------------------------------------------

def write_issues(issues: IssueLog, path: str) -> None:
    _write_csv(path, CSV_HEADER, [i.as_row() for i in issues.items])


def display_map(result: MergeResult) -> Dict[str, str]:
    """정규 키 -> `merged.csv` 에 실제로 쓰인 표시 ID.

    `final_keys[i]` 와 `rows[i]` 는 같은 순서로 만들어지므로 둘을 짝지으면
    된다. 커버리지 표와 병합 표가 **같은 표기**를 써야 사람이 두 파일을
    눈으로 대조할 수 있다.
    """
    mapping: Dict[str, str] = {}
    for (key, _tp), row in zip(result.final_keys, result.rows):
        mapping.setdefault(key, row[0] if row else key)
    return mapping


def write_coverage(result: MergeResult, path: str) -> None:
    """피험자 × 파일 커버리지(1/0). 어느 피험자가 어느 파일에 없는지 한눈에."""
    labels = [p.label for p in result.plans]
    header = ["subject_id"] + labels + ["보유파일수", "빠진파일"]
    display = display_map(result)
    rows: List[List[str]] = []
    for subject in sorted(result.coverage):
        marks = result.coverage[subject]
        cells = [str(1 if marks.get(label) else 0) for label in labels]
        missing = [label for label in labels if not marks.get(label)]
        rows.append([display.get(subject, subject)] + cells
                    + [str(len(labels) - len(missing)), ", ".join(missing)])
    _write_csv(path, header, rows)


# --------------------------------------------------------------------------
# N-흐름
# --------------------------------------------------------------------------

# 전부 **제외** 사유다. `--dup-policy mean` 으로 평균에 반영된 행은 실제로
# 기여했으므로 여기 없고, `기여` 쪽에 들어간다.
_FLOW_LABELS = (
    (DROP_NO_KEY, "피험자 ID 없음"),
    (DROP_DATE_PARSE, "날짜/시점 해석 실패"),
    (DROP_DUPLICATE, "중복 키"),
    (DROP_TIME_CONFLICT, "시점 충돌"),
    (DROP_SUBJECT_UNMATCHED, "피험자 미매칭"),
    (DROP_TIME_UNMATCHED, "시점 미매칭"),
)


def nflow_rows(result: MergeResult) -> Tuple[List[Tuple[str, int]], int, int]:
    """(제외 사유별 (설명, 건수) 목록, 입력 행 합계, 기여한 행 수).

    `입력 = 기여 + Σ제외` 가 항상 성립한다. `중복 키(평균에 반영)` 행은 값이
    실제로 평균에 들어갔으므로 **기여**로 센다 — 제외로 세면 산술이 깨지고
    Methods 문장이 거짓이 된다.
    """
    counts = result.ledger.counts()
    total = result.ledger.total
    used = counts.get(USED, 0) + counts.get(DUP_MERGED, 0)
    lines = [(label, counts.get(disp, 0)) for disp, label in _FLOW_LABELS]
    return lines, total, used


# --------------------------------------------------------------------------
# 화면 요약
# --------------------------------------------------------------------------

def _fmt_detect(plan: FilePlan) -> str:
    key_col = plan.key_det.column or "?"
    bits = [f"키: {key_col}"]
    if plan.key_det.confidence == EXPLICIT:
        bits[-1] += " (지정)"
    elif plan.key_det.confidence == BY_CONTENT:
        bits[-1] += " (내용 추정)"
    else:
        bits[-1] += " (자동 탐지)"
    if plan.time_kind == "date":
        bits.append(f"날짜열: {plan.time_col} (자동 탐지)"
                    if plan.key_det.confidence != EXPLICIT
                    else f"날짜열: {plan.time_col}")
    elif plan.time_kind == "visit":
        bits.append(f"시점열: {plan.time_col}")
    elif plan.time_kind == "fixed":
        bits.append(f"시점: 파일 전체에 '{plan.fixed_label}' 지정")
    else:
        bits.append("시점열 없음 → 피험자 단위")
    return "  ".join(bits)


def screen_summary(result: MergeResult, issues: IssueLog,
                   cutoff_text: str, dup_policy: str, tolerance: int,
                   out_dir: str, exit_code: int,
                   schema_problems: Sequence[str] = ()) -> str:
    """화면에 그대로 찍는 요약. 첫 블록은 **언제나 자동 탐지 결과**다."""
    out: List[str] = []
    add = out.append

    add("[입력]")
    for plan in result.plans:
        subjects = len(plan.subjects())
        add(f"  {plan.label:<22} {plan.frame.nrows:>6}행  피험자 {subjects}명  "
            + _fmt_detect(plan))

    # 키 정규화
    parts: List[str] = []
    total_zero = sum(p.key_stats.zero_pad for p in result.plans if p.key_stats)
    total_ws = sum(p.key_stats.whitespace for p in result.plans if p.key_stats)
    total_alias = sum(p.key_stats.alias for p in result.plans if p.key_stats)
    total_prefix = sum(p.key_stats.prefix for p in result.plans if p.key_stats)
    total_fw = sum(p.key_stats.fullwidth for p in result.plans if p.key_stats)
    if total_zero:
        parts.append(f"제로패딩 정규화 {total_zero}건")
    if total_ws:
        parts.append(f"공백 제거 {total_ws}건")
    if total_fw:
        parts.append(f"전각→반각 {total_fw}건")
    if total_prefix:
        prefixes = sorted({p.key_stats.prefix_value for p in result.plans
                           if p.key_stats and p.key_stats.prefix_value})
        tail = f"({', '.join(prefixes)})" if prefixes else ""
        parts.append(f"접두어 제거 {total_prefix}건{tail}")
    if total_alias:
        parts.append(f"별칭표 적용 {total_alias}건")
    heads = sorted({p.key_stats.head_value for p in result.plans
                    if p.key_stats and p.key_stats.head_value})
    if heads:
        parts.append("ID 머리말 통일(" + ", ".join(repr(h) for h in heads) + ")")
    add("")
    merged_n = len(result.merged_spellings)
    if merged_n:
        sample = ", ".join(
            "/".join(v) for _k, v in sorted(result.merged_spellings.items())[:3])
        tail = (f" → 서로 다른 표기가 한 사람으로 합쳐진 경우 **{merged_n}건**"
                f" ({sample}{' 외' if merged_n > 3 else ''})")
    else:
        tail = " → 서로 다른 표기가 한 사람으로 합쳐진 경우는 없었습니다"
    add("[키 정규화] " + (", ".join(parts) if parts else "바꿀 것이 없었습니다")
        + tail)
    if parts and not merged_n:
        add("           (위 건수는 내부 표준화 계산이며 병합 결과를 바꾸지 "
            "않았습니다)")

    if result.align == "night":
        moved = sum(1 for plan in result.plans if plan.time_kind == "date")
        add(f"[야간 귀속] {cutoff_text} 기준. 그 이전 시각의 타임스탬프는 "
            f"전날 밤에 귀속했습니다(날짜 파일 {moved}개).")
    if tolerance:
        add(f"[시점 허용오차] 기준 파일 '{result.plans[result.base_index].label}' "
            f"의 시점에 ±{tolerance}일까지 맞춥니다.")

    # 심각/경고 요약
    counts = issues.counts()
    if counts.get(CRITICAL) or counts.get(WARNING):
        add("")
        by_kind: Dict[str, int] = {}
        for item in issues.items:
            if item.severity == INFO:
                continue
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        add("[!] " + ", ".join(f"{k} {v}건" for k, v in sorted(
            by_kind.items(), key=lambda kv: -kv[1])))
        for item in issues.items[:3]:
            if item.severity == INFO:
                continue
            text = " ".join(item.message.split())
            if len(text) > 110:
                text = text[:110] + " …"
            add(f"      {item.severity}: {text}")
        if counts.get(CRITICAL, 0) + counts.get(WARNING, 0) > 3:
            add("      ... 전체 목록은 문제목록.csv")

    # N-흐름
    lines, total, used = nflow_rows(result)
    merged_rows = result.ledger.counts().get(DUP_MERGED, 0)
    add("")
    add("[N 흐름]")
    add(f"  {'입력 행 합계':.<38} {total:>6}")
    for label, count in lines:
        if count:
            add(f"  {label:.<38} {('-' + str(count)):>6}")
    add("  " + "-" * 45)
    subjects = len(result.subjects)
    add(f"  {'병합에 기여한 입력 행':.<38} {used:>6}"
        + (f"  (중복 키 {merged_rows}행 평균 반영 포함)" if merged_rows else ""))
    add(f"  {'최종 표':.<38} 피험자 {subjects}명 / {len(result.rows)}행")

    if result.ledger_error:
        add("")
        add(f"[내부 오류] {result.ledger_error}")

    info_kinds = sorted({i.kind for i in issues.items if i.severity == INFO})
    if info_kinds:
        add("")
        add("[정보] " + ", ".join(info_kinds) + " — 종료코드에 영향 없음")

    if schema_problems:
        add("")
        add("[!] merged.csv 스키마 자체 검증 실패:")
        for problem in schema_problems:
            add(f"      {problem}")

    add("")
    add(f"결과 파일: {out_dir}")
    for name in OUTPUT_NAMES:
        add(f"  · {name}")
    add("")
    reason = {0: "문제 없음", 1: "병합 실패", 2: "경고 있음(병합은 됨)",
              3: "병합 불가"}.get(exit_code, "")
    add(f"종료코드 {exit_code} ({reason}).")
    if exit_code == 2:
        add("문제목록.csv 를 먼저 확인하세요.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Methods 초안
# --------------------------------------------------------------------------

def methods_draft(result: MergeResult, cutoff_text: str, dup_policy: str,
                  tolerance: int) -> Tuple[str, str]:
    """N-흐름을 논문 Methods 한 문단으로. (한국어, 영어)."""
    lines, total, used = nflow_rows(result)
    files = ", ".join(p.label for p in result.plans)
    subjects = len(result.subjects)
    dropped = {label: n for label, n in lines if n}

    align_ko = {
        "night": f"수면 자료의 자정 넘김을 처리하기 위해 {cutoff_text} 를 하루의 "
                 f"경계로 삼아, 그 이전 시각의 기록은 앞선 날짜의 밤에 귀속하였다",
        "visit": "방문 라벨을 사전 정의한 정규 라벨로 맞추어 시점을 정렬하였다",
        "date": "기록된 날짜를 그대로 시점으로 사용하였다",
    }[result.align]
    align_en = {
        "night": f"To handle post-midnight sleep records, {cutoff_text} was used "
                 f"as the day boundary; timestamps before this cut-off were "
                 f"attributed to the preceding night",
        "visit": "Visit labels were normalised to pre-defined canonical labels",
        "date": "Recorded calendar dates were used as time points",
    }[result.align]

    dup_ko = {
        "error": "중복 키(같은 피험자·같은 시점의 복수 행)는 어느 쪽이 맞는지 "
                 "정할 수 없으므로 병합에서 제외하였다",
        "first": "중복 키는 첫 번째 기록만 사용하였다",
        "last": "중복 키는 마지막 기록만 사용하였다",
        "mean": "중복 키의 수치형 변수는 평균으로 통합하였다",
    }[dup_policy]
    dup_en = {
        "error": "Duplicate keys (multiple rows for the same subject and time "
                 "point) were excluded, as the correct record could not be "
                 "determined",
        "first": "For duplicate keys, only the first record was retained",
        "last": "For duplicate keys, only the last record was retained",
        "mean": "For duplicate keys, numeric variables were averaged",
    }[dup_policy]

    how_ko = {"inner": "모든 파일에 공통으로 존재하는", "outer": "어느 한 파일에라도 존재하는",
              "left": f"기준 파일('{result.plans[result.base_index].label}')에 존재하는"}[result.how]
    how_en = {"inner": "present in all sources", "outer": "present in any source",
              "left": f"present in the reference source "
                      f"('{result.plans[result.base_index].label}')"}[result.how]

    drop_ko = ("; ".join(f"{label} {n}행" for label, n in dropped.items())
               if dropped else "제외된 행 없음")
    drop_en = ("; ".join(f"{_EN_REASON.get(label, label)}, n = {n}"
                         for label, n in dropped.items())
               if dropped else "no rows were excluded")

    tol_ko = (f" 서로 다른 파일의 측정일은 기준 파일의 시점에 ±{tolerance}일까지 "
              f"맞추었고, 같은 거리의 후보가 둘 이상이면 병합하지 않았다." if tolerance else "")
    tol_en = (f" Dates from different sources were matched to the reference "
              f"time points within ±{tolerance} day(s); ties were left unmerged."
              if tolerance else "")

    ko = (
        f"{len(result.plans)}개 자료원({files})을 피험자 식별자와 시점을 기준으로 "
        f"병합하였다. 피험자 식별자는 공백·대소문자·자릿수 표기 차이만 결정론적 "
        f"규칙으로 정규화하였으며, 유사도 기반 추정 매칭은 사용하지 않았다. "
        f"{align_ko}.{tol_ko} {dup_ko}. 병합은 {how_ko} 피험자·시점을 대상으로 "
        f"하였다. 입력 {total}행 중 {used}행이 병합에 사용되었고(제외: {drop_ko}), "
        f"최종 분석 자료는 피험자 {subjects}명, {len(result.rows)}행이었다."
    )
    en = (
        f"Data from {len(result.plans)} sources ({files}) were merged by subject "
        f"identifier and time point. Subject identifiers were normalised using "
        f"deterministic rules only (whitespace, letter case, and zero-padding); "
        f"no similarity-based or fuzzy matching was applied. {align_en}.{tol_en} "
        f"{dup_en}. The merge retained subject-time points {how_en}. Of "
        f"{total} input rows, {used} contributed to the merged dataset "
        f"({drop_en}), yielding a final analytic dataset of {subjects} subjects "
        f"and {len(result.rows)} rows."
    )
    return ko, en


_EN_REASON = {
    "피험자 ID 없음": "missing subject identifier",
    "날짜/시점 해석 실패": "unparseable date or time point",
    "중복 키": "duplicate key",
    "시점 충돌": "ambiguous time-point match",
    "피험자 미매칭": "subject not present in the merged key set",
    "시점 미매칭": "time point not present in the merged key set",
}


# --------------------------------------------------------------------------
# 병합감사.md
# --------------------------------------------------------------------------

def write_audit(result: MergeResult, issues: IssueLog, path: str,
                argv_text: str, cutoff_text: str, dup_policy: str,
                tolerance: int, spec_lines: Sequence[str] = (),
                schema_problems: Sequence[str] = (),
                today: Optional[str] = None) -> None:
    """사람이 읽는 감사 리포트. 논문 Methods 와 IRB 자료에 그대로 들어간다."""
    md: List[str] = []
    add = md.append
    stamp = today or _dt.date.today().isoformat()

    add("# 병합 감사 리포트")
    add("")
    add(f"- 생성일: {stamp}")
    add(f"- 실행 명령: `{argv_text}`")
    add(f"- 병합 방식: `--how {result.how}` · 시점 정렬: `--align {result.align}`"
        f" · 중복 정책: `--dup-policy {dup_policy}`"
        + (f" · 허용오차: {tolerance}일" if tolerance else ""))
    add(f"- 기준 파일: `{result.plans[result.base_index].label}`")
    add("")
    add("> 이 리포트는 **무엇이 어떻게 합쳐졌고 무엇이 왜 빠졌는지**의 기록입니다. "
        "숫자를 논문에 쓰기 전에 `문제목록.csv` 를 먼저 확인하세요.")
    add("")

    # 1. 입력
    add("## 1. 입력 파일")
    add("")
    add("| 파일 | 행 | 고유 피험자 | 키 열 | 시점 | 인코딩 | 열 수 |")
    add("|---|---:|---:|---|---|---|---:|")
    for plan in result.plans:
        frame = plan.frame
        timepoint = {
            "date": f"날짜: `{plan.time_col}`",
            "visit": f"방문: `{plan.time_col}`",
            "fixed": f"파일 전체 = `{plan.fixed_label}` (--visit-label)",
            "none": "없음(피험자 단위)",
        }.get(plan.time_kind, plan.time_kind)
        add(f"| `{md_cell(plan.label)}` | {frame.nrows} | {len(plan.subjects())} | "
            f"`{md_cell(str(plan.key_det.column))}` ({plan.key_det.confidence}) | "
            f"{md_cell(timepoint)} | {frame.encoding} | {len(frame.header)} |")
    add("")
    for plan in result.plans:
        notes = [n for n in plan.frame.notes]
        if notes:
            add(f"- `{plan.label}` 읽기 중 처리: "
                + "; ".join(f"{n.detail}"
                            + (f" ({n.count}건)" if n.count else "")
                            for n in notes[:10]))
    add("")

    # 2. 적용한 규칙
    add("## 2. 적용한 규칙 (그대로 Methods 에 옮길 수 있는 문장)")
    add("")
    add("- 피험자 ID: NFKC 정규화 → 공백 제거 → (별칭표) → 접두어 제거 → "
        "대문자화 → 선행 0 제거. **편집거리 기반 추측 매칭은 하지 않습니다** "
        "(`S01` 과 `S02` 는 어떤 경우에도 붙지 않습니다).")
    for plan in result.plans:
        stats = plan.key_stats
        if not stats:
            continue
        bits = []
        if stats.whitespace:
            bits.append(f"공백 {stats.whitespace}건")
        if stats.fullwidth:
            bits.append(f"전각 {stats.fullwidth}건")
        if stats.zero_pad:
            bits.append(f"제로패딩 {stats.zero_pad}건")
        if stats.prefix:
            bits.append(f"접두어 '{stats.prefix_value}' 제거 {stats.prefix}건"
                        if stats.prefix_value else f"접두어 제거 {stats.prefix}건")
        if stats.alias:
            bits.append(f"별칭표 {stats.alias}건")
        if stats.head_value:
            bits.append(f"머리말 '{stats.head_value}' 통일 {stats.head}건")
        if stats.missing:
            bits.append(f"ID 없음 {stats.missing}행")
        add(f"  - `{plan.label}`: " + (", ".join(bits) if bits else "바꾼 것 없음"))
    if result.align == "night":
        add(f"- 야간 귀속: **{cutoff_text} 를 하루의 경계**로 삼아, 그 이전 시각의 "
            "기록은 앞선 날짜의 밤에 귀속했습니다. 예: 23:40 과 다음날 03:20 은 "
            "같은 밤, 같은 날 13:00 은 다음 밤입니다.")
        for plan in result.plans:
            if plan.time_kind == "date" and plan.no_time_rows:
                add(f"  - `{plan.label}`: 시각 없이 날짜만 있는 {plan.no_time_rows}행은 "
                    "날짜를 그대로 썼습니다(시각을 모르는데 하루를 옮기지 않습니다).")
    elif result.align == "visit":
        for plan in result.plans:
            if plan.time_kind == "fixed":
                add(f"- `{plan.label}`: 시점 열이 없어 `--visit-label` 로 파일 전체에 "
                    f"'{plan.fixed_label}' 을 부여했습니다(파일 하나 = 한 시점).")
        add("- 시점 정렬: 방문 라벨을 사전 정의표로 정규화했습니다. "
            "**모르는 라벨은 추측하지 않고** 원본 표기를 그대로 쓰되 보고합니다.")
        for plan in result.plans:
            if plan.unknown_visits:
                labels = ", ".join(f"{k}({v}행)" for k, v in
                                   sorted(plan.unknown_visits.items())[:10])
                add(f"  - `{plan.label}` 의 미등록 라벨: {labels}")
    else:
        add("- 시점 정렬: 기록된 날짜를 그대로 시점으로 썼습니다.")
    add(f"- 중복 키 정책: `--dup-policy {dup_policy}` — "
        + {"error": "중복은 병합에서 제외했습니다(어느 행이 맞는지 툴이 정하지 않습니다).",
           "first": "첫 행만 남겼습니다.", "last": "마지막 행만 남겼습니다.",
           "mean": "수치형은 평균, 값이 다른 비수치형은 비웠습니다."}[dup_policy])
    add("- **카테시안 조인은 어떤 경우에도 하지 않습니다.** 병합 전에 파일마다 "
        "(피험자, 시점)당 정확히 한 행으로 축약하므로, 출력 행 수가 입력보다 "
        "늘어나는 일은 구조적으로 불가능합니다.")
    for line in spec_lines:
        add(f"- 스펙(`--spec`): {line}")
    add("")

    # 3. N-흐름
    lines, total, used = nflow_rows(result)
    add("## 3. N 흐름 (입력 → 최종)")
    add("")
    add("```")
    add(f"  {'입력 행 합계':.<38} {total:>6}")
    for label, count in lines:
        if count:
            add(f"  {label:.<38} {('-' + str(count)):>6}")
    add("  " + "-" * 45)
    add(f"  {'병합에 기여한 입력 행':.<38} {used:>6}")
    add(f"  {'최종 표':.<38} 피험자 {len(result.subjects)}명 / {len(result.rows)}행")
    add("```")
    add("")
    add(f"산술 확인: 입력 {total} = 기여 {used} + 제외 "
        f"{total - used}. **어떤 행도 사유 없이 사라지지 않습니다.**")
    if result.ledger_error:
        add("")
        add(f"> **내부 오류**: {result.ledger_error} 이 결과를 쓰지 마세요.")
    add("")

    # 4. 파일별 처분
    add("## 4. 파일별 행 처분")
    add("")
    header_cells = ["파일", "입력"] + [md_cell(d) for d in DISPOSITIONS]
    add("| " + " | ".join(header_cells) + " |")
    add("|" + "---|" * len(header_cells))
    for plan in result.plans:
        counts = result.ledger.counts(plan.index)
        cells = [f"`{md_cell(plan.label)}`", str(plan.frame.nrows)]
        cells += [str(counts.get(d, 0)) for d in DISPOSITIONS]
        add("| " + " | ".join(cells) + " |")
    add("")

    # 5. 파일 간 교집합·차집합
    add("## 5. 파일 간 피험자 교집합 / 차집합")
    add("")
    display = display_map(result)

    def shown_ids(keys) -> str:
        names = sorted(display.get(k, k) for k in keys)
        return ", ".join(names[:15]) + (" 외" if len(names) > 15 else "")

    sets = {p.label: p.subjects() for p in result.plans}
    all_subjects = set().union(*sets.values()) if sets else set()
    common = set.intersection(*sets.values()) if sets else set()
    add(f"- 전체 등장 피험자: **{len(all_subjects)}명**")
    add(f"- 모든 파일에 있는 피험자: **{len(common)}명**")
    for label, subjects in sets.items():
        only = (subjects - set().union(*(s for l, s in sets.items() if l != label))
                if len(sets) > 1 else set())
        if only:
            add(f"- `{label}` 에만 있는 피험자 {len(only)}명: {shown_ids(only)}")
        missing = all_subjects - subjects
        if missing:
            add(f"- `{label}` 에 **없는** 피험자 {len(missing)}명: {shown_ids(missing)}")
    add("")

    # 6. 커버리지 매트릭스
    add("## 6. 피험자 × 파일 커버리지")
    add("")
    add("전체 표는 `키매칭표.csv` 에 있습니다. 여기에는 **빠진 파일이 있는 피험자만** "
        "보여 줍니다(모두 갖춘 피험자는 볼 필요가 없습니다).")
    add("")
    labels = [p.label for p in result.plans]
    incomplete = [(s, marks) for s, marks in sorted(result.coverage.items())
                  if not all(marks.get(l) for l in labels)]
    if not incomplete:
        add("모든 피험자가 모든 파일에 존재합니다.")
    else:
        add("| 피험자 | " + " | ".join(f"`{md_cell(l)}`" for l in labels) + " |")
        add("|" + "---|" * (len(labels) + 1))
        for subject, marks in incomplete[:40]:
            add(f"| {md_cell(display.get(subject, subject))} | "
                + " | ".join("O" if marks.get(l) else "**·**" for l in labels)
                + " |")
        if len(incomplete) > 40:
            add("")
            add(f"... 외 {len(incomplete) - 40}명 (전체는 `키매칭표.csv`)")
    add("")

    # 7. 문제 목록 요약
    add("## 7. 발견한 문제")
    add("")
    counts = issues.counts()
    add(f"- 심각 {counts.get(CRITICAL, 0)}건 / 경고 {counts.get(WARNING, 0)}건 / "
        f"정보 {counts.get(INFO, 0)}건 — 전체 목록은 `문제목록.csv`")
    add("")
    by_kind = issues.by_kind()
    if by_kind:
        add("| 유형 | 건수 |")
        add("|---|---:|")
        for kind, n in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0])):
            add(f"| {md_cell(kind)} | {n} |")
        add("")
        add("### 상위 항목")
        add("")
        for item in issues.items[:20]:
            where = f" ({item.file}"
            where += f" {item.line}행" if item.line else ""
            where += f", 키 {item.key}" if item.key else ""
            where += ")"
            add(f"- **{item.severity} · {item.kind}**{where} — {item.message}")
            if item.advice:
                add(f"  - 권고: {item.advice}")
    else:
        add("문제 없음.")
    add("")

    # 8. 스키마 자체 검증
    add("## 8. 하류 툴 투입 가능 여부 (merged.csv 자체 검증)")
    add("")
    if schema_problems:
        for problem in schema_problems:
            add(f"- **실패**: {problem}")
    else:
        add("`merged.csv` 는 헤더 1줄, 열 이름 중복 없음, 모든 행의 열 수 동일, "
            "결측은 빈 칸입니다.")
        add("")
        subject_level = all(p.subject_level for p in result.plans) or \
            len(result.rows) <= len(result.subjects)
        if subject_level:
            add("이 표는 **1행 = 1피험자** 이므로 세 툴 모두에 그대로 넣을 수 있습니다.")
            add("")
            add("```bash")
            add("table1    merged.csv --group <그룹열>")
            add("statwise  merged.csv --value <값열> --group <그룹열>")
            add("```")
        else:
            add(f"다만 이 표는 **1행 = 피험자 × 시점** 입니다"
                f"(피험자 {len(result.subjects)}명, {len(result.rows)}행). "
                "그러므로:")
            add("")
            add("```bash")
            add("# 반복측정을 반복측정으로 다루는 툴 — 이 표에 바로 쓸 수 있습니다")
            add("longistat merged.csv --id subject_id --time timepoint --value <값열>")
            add("```")
            add("")
            add("> **주의 — 유사반복(pseudoreplication).** `statwise` 와 `table1` 은 "
                "**1행 = 1피험자**를 전제합니다. 이 시점별 표를 그대로 넣으면 "
                f"같은 사람의 {len(result.rows) // max(len(result.subjects), 1)}개 "
                "행이 서로 독립인 관측으로 취급되어 N이 부풀고 p값이 실제보다 "
                "작아집니다. 먼저 피험자당 한 행으로 요약(기저값·평균·변화량 등)한 "
                "뒤에 넣으세요. 이 툴은 그 요약을 대신 해 주지 않습니다 — 어떤 "
                "요약이 맞는지는 연구 질문에 달려 있기 때문입니다.")
    add("")

    # 9. Methods 초안
    ko, en = methods_draft(result, cutoff_text, dup_policy, tolerance)
    add("## 9. Methods 초안")
    add("")
    add("### 한국어")
    add("")
    add(ko)
    add("")
    add("### English")
    add("")
    add(en)
    add("")
    add("> 위 문장의 숫자는 이 실행에서 실제로 센 값입니다. 그대로 쓰되, "
        "제외 사유가 연구 프로토콜과 맞는지는 반드시 사람이 확인하세요.")
    add("")

    _write_text(path, "\n".join(md) + "\n")
