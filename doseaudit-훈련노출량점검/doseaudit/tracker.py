"""손으로 관리한 참여현황 트래커를 읽는다 — **이름은 열지 않고**.

트래커의 첫 열은 실명(`환자명`)이고 둘째 열은 `Login ID` 다. 둘 다 개인 식별이
가능하므로 **이 모듈이 읽는 순간 버린다.** 조인은 `Access Code` 로만 한다.
(`deidaudit`·`jamoscore` 전례 — 출력에 실명이 0건인지 테스트가 고정한다.)

`D1`…`D145` 일자별 격자도 **읽지 않는다.** 145열의 기준점(가입일인지 연구
시작일인지)이 어디에도 적혀 있지 않아, 읽으면 추론이 되고 추론하면 조용히
거짓말하게 된다. 커버리지 자백에 `검사 안 함` 으로 적는다.
"""

import csv
import os
import re

from doseaudit.dose import REASON_NO_ACTIVITY
from doseaudit.errors import ReadError, RefuseError
from doseaudit.sanitize import nfc, safe_text
from doseaudit.values import fmt_number
from doseaudit.values import (DAY_UNITS, PERCENT_UNITS, ValueError_, parse_date,
                              strip_unit)
from doseaudit.xlsxread import read_workbook

COL_CODE = "Access Code"
#: 워크북 파일명과 **같은 규격**으로 본다. 손으로 관리한 표에 이름이나 Login ID 가
#: 들어가 있으면, 그 값이 리포트와 CSV 로 그대로 흘러나간다.
CODE_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")
COL_ENROLL = "가입일"
COL_DONE = "완료일수"
COL_ELAPSED = "현재까지 일수"
COL_PROGRESS = "진행률(%)"

#: 이 툴이 실제로 값을 꺼내 쓰는 열. 나머지는 "읽지 않은 열"로 자백에 나간다.
USED_COLUMNS = frozenset((COL_CODE, COL_ENROLL, COL_DONE, COL_ELAPSED, COL_PROGRESS))

#: 열자마자 버리는 열. 이름이 조금 달라도 걸리도록 느슨하게 맞춘다.
#: 앵커를 걸지 않는다. `성명` 만 막으면 `환자 성명`·`성명(한글)`·`수급자 성함`이
#: 전부 빠져나가고, 그래놓고 리포트는 "이름 열은 열지 않습니다"라고 말하게 된다.
FORBIDDEN_PATTERNS = (
    re.compile(r"성\s*명"), re.compile(r"성\s*함"), re.compile(r"이\s*름"),
    re.compile(r"환\s*자\s*명"), re.compile(r"피\s*험\s*자\s*명"),
    re.compile(r"참\s*여\s*자\s*명"), re.compile(r"대\s*상\s*자\s*명"),
    re.compile(r"(?i)\bname\b"), re.compile(r"(?i)login\s*_?\s*id"),
    re.compile(r"(?i)user\s*_?\s*id"),
    re.compile(r"(?i)(환자|피험자|참여자|대상자|보호자)\s*_?\s*id"),
    re.compile(r"연\s*락\s*처"), re.compile(r"전\s*화"), re.compile(r"휴\s*대"),
    re.compile(r"생\s*년\s*월\s*일"), re.compile(r"주\s*민"), re.compile(r"(?i)e-?mail"),
    re.compile(r"이\s*메\s*일"), re.compile(r"주\s*소"), re.compile(r"보\s*호\s*자"),
)

#: 일자별 격자 열 (`D1` … `D145`) — 읽지 않는다.
_GRID_RE = re.compile(r"^D\d+$")


class TrackerRow(object):
    __slots__ = ("code", "enroll", "done_days", "elapsed_days", "progress", "excel_row")

    def __init__(self, code, enroll, done_days, elapsed_days, progress, excel_row):
        self.code = code
        self.enroll = enroll
        self.done_days = done_days
        self.elapsed_days = elapsed_days
        self.progress = progress
        self.excel_row = excel_row


class Tracker(object):
    def __init__(self, rows, dropped_columns, grid_columns, unread_fields, n_raw_rows,
                 n_blank_rows=0, unread_columns=()):
        self.rows = rows
        self.dropped_columns = dropped_columns    # 실명·ID 등 열지 않은 열
        self.grid_columns = grid_columns          # D1…DN 격자 열 수
        self.unread_fields = unread_fields        # [(코드, 항목, 사유)]
        self.n_raw_rows = n_raw_rows
        #: Access Code 칸이 비어 건너뛴 행 — 조용히 사라지면 분모가 줄어든다.
        self.n_blank_rows = n_blank_rows
        #: 이 툴이 아예 읽지 않은 열 전부(개인식별 열만이 아니라).
        self.unread_columns = list(unread_columns)

    def by_code(self):
        return {r.code: r for r in self.rows}


def _table_from_path(path):
    """트래커를 `[(행번호, [셀, ...]), ...]` 로. xlsx 도 csv 도 받는다."""
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        sheets = read_workbook(path)
        for _name, rows in sheets:
            if any(any(nfc(c).strip() == COL_CODE for c in cells) for _r, cells in rows):
                return rows
        return sheets[0][1]
    if lower.endswith((".csv", ".tsv")):
        delim = "\t" if lower.endswith(".tsv") else ","
        for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                with open(path, "r", encoding=encoding, newline="") as fh:
                    return [(i + 1, list(row)) for i, row in enumerate(csv.reader(fh, delimiter=delim))]
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                raise ReadError("트래커를 열지 못했습니다: %s" % type(exc).__name__) from exc
        raise ReadError("트래커 인코딩을 utf-8/utf-8-sig/cp949 중 어느 것으로도 읽지 못했습니다")
    raise RefuseError("`--tracker` 는 .xlsx 또는 .csv 여야 합니다: %s" % safe_text(os.path.basename(path)))


def load_tracker(path):
    """참여현황 트래커를 읽는다. 실명·Login ID·`D1…DN` 격자는 읽지 않는다."""
    if not os.path.isfile(path):
        raise RefuseError("`--tracker` 파일이 없습니다: %s" % safe_text(path))
    table = _table_from_path(path)

    header_idx = None
    for idx, (_excel_row, cells) in enumerate(table):
        if any(nfc(c).strip() == COL_CODE for c in cells):
            header_idx = idx
            break
    if header_idx is None:
        raise RefuseError(
            "트래커에서 `%s` 열을 찾지 못했습니다.\n"
            "       이 툴은 실명이 아니라 `%s` 로만 조인합니다 — 열 이름을 확인하세요."
            % (COL_CODE, COL_CODE)
        )

    header = [nfc(c).strip() for c in table[header_idx][1]]
    index = {}
    dropped, grid = [], 0
    for pos, name in enumerate(header):
        if any(p.search(name) for p in FORBIDDEN_PATTERNS):
            dropped.append(name)
            continue
        if _GRID_RE.match(name):
            grid += 1
            continue
        if name and name not in index:
            index[name] = pos

    missing = [c for c in (COL_CODE, COL_DONE) if c not in index]
    if missing:
        raise RefuseError(
            "트래커에 필요한 열이 없습니다: %s\n"
            "       최소한 `%s` 와 `%s` 가 있어야 대조할 수 있습니다."
            % (", ".join(missing), COL_CODE, COL_DONE)
        )

    def cell(cells, name):
        pos = index.get(name)
        if pos is None or pos >= len(cells):
            return ""
        return nfc(cells[pos]).strip()

    rows, unread = [], []
    seen = {}
    n_raw = n_blank = 0
    # 읽지 않은 열 = 전체 − (이 툴이 **실제로 쓰는** 열) − (개인식별로 열지 않은 열)
    #              − (일자별 격자).
    # `index` 와 견주면 안 된다 — `index` 에는 버리지 않은 열이 **전부** 들어 있어서
    # 이 목록이 영원히 비고, 그래놓고 자백은 "읽지 않은 열 전부를 인쇄합니다"라고 말한다.
    # `비고`·`담당자` 같은 열은 실제로 읽지 않으므로 여기 나와야 한다.
    unread_columns = [name for name in header
                      if name and name not in USED_COLUMNS and name not in dropped
                      and not _GRID_RE.match(name)]
    for excel_row, cells in table[header_idx + 1:]:
        code = cell(cells, COL_CODE)
        if not code:
            # 병합 셀·소계 행 때문에 흔히 생긴다. 조용히 건너뛰면 분모가 줄어든다.
            if any(nfc(c).strip() for c in cells):
                n_blank += 1
            continue
        n_raw += 1
        if not CODE_SHAPE.match(code):
            # 코드 자리에 사람 이름이 들어와 있을 수 있다 — 값을 인쇄하지 않는다.
            unread.append((("%d행" % excel_row), COL_CODE,
                           "Access Code 규격이 아님 — 값을 읽지 않았습니다"))
            continue
        if code in seen:
            raise RefuseError(
                "트래커에 `%s` 가 두 번(%d행·%d행) 있습니다 — 어느 행이 맞는지 추측하지 않습니다."
                % (safe_text(code), seen[code], excel_row)
            )
        seen[code] = excel_row

        def number(name, units):
            raw = cell(cells, name)
            if not raw:
                unread.append((code, name, "빈 칸"))
                return None
            try:
                return strip_unit(raw, units)
            except ValueError_ as exc:
                unread.append((code, name, str(exc)))
                return None

        enroll_raw = cell(cells, COL_ENROLL)
        enroll = None
        if enroll_raw:
            try:
                enroll = parse_date(enroll_raw)
            except ValueError_ as exc:
                unread.append((code, COL_ENROLL, str(exc)))
        elif COL_ENROLL in index:
            unread.append((code, COL_ENROLL, "빈 칸"))

        rows.append(TrackerRow(code, enroll, number(COL_DONE, DAY_UNITS),
                               number(COL_ELAPSED, DAY_UNITS) if COL_ELAPSED in index else None,
                               number(COL_PROGRESS, PERCENT_UNITS) if COL_PROGRESS in index else None,
                               excel_row))
    if not rows:
        raise RefuseError("트래커에서 `%s` 를 가진 행을 하나도 찾지 못했습니다" % COL_CODE)
    return Tracker(rows, dropped, grid, unread, n_raw, n_blank, unread_columns)


class TrackerDiff(object):
    """트래커 값과 다시 센 값의 차이.

    `완료일수` 를 `int()` 로 잘라 표시하면 3.5 가 3 이 되어 `트래커 3일 ↔ 로그 3일
    (+0)` 이라는, 고칠 데가 없는 치명이 인쇄된다. **온 값 그대로** 들고 다닌다.
    """

    __slots__ = ("code", "tracker_done", "recomputed", "delta")

    def __init__(self, code, tracker_done, recomputed):
        self.code = code
        self.tracker_done = tracker_done
        self.recomputed = recomputed
        self.delta = recomputed - tracker_done

    def fmt_tracker(self):
        return fmt_number(self.tracker_done)

    def fmt_delta(self):
        return ("%+d" % self.delta if float(self.delta).is_integer()
                else "%+.2f" % self.delta)


def compare(tracker, exposures, unreadable_codes=()):
    """트래커 `완료일수` 와 **선언된 규칙으로 다시 센 활동일수**를 대조한다.

    돌려주는 것: 불일치 목록(부호 포함) · 일치 코드 · 한쪽에만 있는 코드 ·
    트래커 내부의 `진행률(%)` 정합성.
    """
    t_by = tracker.by_code()
    e_by = {e.code: e for e in exposures}

    diffs, agree, uncomparable = [], [], []
    for code in sorted(set(t_by) & set(e_by)):
        done = t_by[code].done_days
        if done is None:
            uncomparable.append((code, "트래커 `완료일수` 를 읽지 못함"))
            continue
        exposure = e_by[code]
        if not getattr(exposure, "window_known", True):
            reason = getattr(exposure, "window_unknown_reason", None)
            if reason == REASON_NO_ACTIVITY:
                # 활동이 **하나도 없어서** 창의 끝을 못 잡은 경우다. 이때 다시 센
                # 활동일수는 어느 창을 고르든 0 이라 대조에 모호함이 없다 —
                # 그런데 여기서 빼 버리면, 트래커가 `완료 5일` 이라고 적어 둔
                # **가장 크게 어긋난 사람**이 조용히 사라지고 `치명 0건 · 종료코드 0`
                # 이 나온다. 창을 못 정한 것과 셀 수 없는 것은 다르다.
                if abs(0 - done) < 1e-9:
                    agree.append(code)
                else:
                    diffs.append(TrackerDiff(code, done, 0))
                continue
            # 그 밖의 경우(가입일 미상 등)는 창 없이 센 숫자를 창 기준의 트래커 값과
            # 견주게 되므로 **다른 정의를 비교**하는 셈이다 — 대조하지 않는다.
            uncomparable.append((
                code, "선언된 창을 정할 수 없어 창 없이 셈(%s) — 대조하지 않음"
                      % (reason or "사유 미상")))
            continue
        recomputed = e_by[code].n_active
        if abs(recomputed - done) < 1e-9:
            agree.append(code)
        else:
            diffs.append(TrackerDiff(code, done, recomputed))

    unreadable = set(unreadable_codes)
    # 워크북이 **없는** 것과 **못 읽은** 것은 다른 사실이다. 사람에게 주는 다음
    # 행동이 다르므로(찾아보기 vs 다시 받기), 기계가 읽는 CSV 에서도 구분한다.
    only_tracker = sorted(set(t_by) - set(e_by) - unreadable)
    unreadable_in_tracker = sorted((set(t_by) - set(e_by)) & unreadable)
    only_logs = sorted(set(e_by) - set(t_by))

    progress_bad, progress_checked, progress_skipped = [], [], []
    for code in sorted(t_by):
        row = t_by[code]
        if row.done_days is None or not row.elapsed_days or row.progress is None:
            # 열이 없거나 값이 비어 있으면 **확인하지 않은 것**이다.
            # 빈 `progress_bad` 를 "전원 재현됨"으로 읽으면 없는 검증을 주장하게 된다.
            progress_skipped.append(code)
            continue
        expected = round(100.0 * row.done_days / row.elapsed_days)
        progress_checked.append(code)
        if abs(expected - row.progress) > 1:
            progress_bad.append((code, row.progress, expected))

    deltas = [d.delta for d in diffs]
    uniform_sign = bool(deltas) and (all(x > 0 for x in deltas) or all(x < 0 for x in deltas))
    return {
        "diffs": diffs,
        "agree": agree,
        "uncomparable": uncomparable,
        "only_tracker": only_tracker,
        "unreadable_in_tracker": unreadable_in_tracker,
        "only_logs": only_logs,
        "progress_bad": progress_bad,
        "progress_checked": progress_checked,
        "progress_skipped": progress_skipped,
        "uniform_sign": uniform_sign,
        "delta_min": min(deltas) if deltas else None,
        "delta_max": max(deltas) if deltas else None,
    }
