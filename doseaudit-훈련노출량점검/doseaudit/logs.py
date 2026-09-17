"""피험자별 앱 훈련 로그 워크북 묶음을 펼친다.

이 로그의 생김새(실측):

    시트 1장 = 훈련 모듈 1개. 맨 위에 벤더가 넣은 `[요약]` 블록 3행이 있고,
    그 아래 어딘가에 `날짜 · 레벨 · 훈련유형 · 반복횟수 · 소요시간(초)` 헤더가 있고,
    그 아래가 데이터다.

**행 오프셋을 상수로 박지 않는다.** `날짜` 로 시작하는 행을 찾아 헤더로 삼되,
못 찾거나 5열 규격과 다르면 **추론하지 않고 거절한다**(exit 2). 형식이 바뀌었는데
틀린 판정을 내놓는 것보다, 판정하지 않는 편이 낫다.
"""

import os
import re

from doseaudit.errors import ReadError, RefuseError
from doseaudit.sanitize import mask_filename, nfc, safe_text
from doseaudit.values import (COUNT_UNITS, SECONDS_UNITS, ValueError_, parse_date,
                              strip_unit)
from doseaudit.xlsxread import read_workbook

#: 이 툴이 읽는 유일한 헤더 규격. 한 글자라도 다르면 판정하지 않는다.
SPEC_HEADER = ("날짜", "레벨", "훈련유형", "반복횟수", "소요시간(초)")

#: `[요약]` 블록에서 재계산 대조가 가능한 항목.
VENDOR_MEAN_SECONDS = "평균 소요시간"
VENDOR_MEAN_REPS = "평균 반복횟수"

_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,32}$")


class LogRow(object):
    """데이터 한 행. 해석 실패는 지우지 않고 사유와 함께 들고 다닌다."""

    __slots__ = ("date", "level", "ttype", "reps", "seconds", "excel_row", "fails", "fingerprint")

    def __init__(self, date, level, ttype, reps, seconds, excel_row, fails, fingerprint):
        self.date = date
        self.level = level
        self.ttype = ttype
        self.reps = reps
        self.seconds = seconds
        self.excel_row = excel_row
        self.fails = fails
        self.fingerprint = fingerprint

    @property
    def usable(self):
        """날짜를 읽은 행만 활동일 계산에 쓸 수 있다."""
        return self.date is not None


class ModuleData(object):
    """시트 1장 = 모듈 1개."""

    __slots__ = ("name", "rows", "vendor", "header_row")

    def __init__(self, name, rows, vendor, header_row):
        self.name = name
        self.rows = rows
        self.vendor = vendor
        #: 헤더가 있던 **엑셀 행 번호**(보이는 그대로). 오프셋 상수가 아니라 찾은 값이다.
        self.header_row = header_row

    @property
    def n_rows(self):
        return len(self.rows)

    @property
    def is_empty(self):
        """데이터 0행. `평균 0` 과 **전혀 다른 상태**다."""
        return not self.rows


class SubjectLog(object):
    """워크북 1개 = 피험자 1명."""

    __slots__ = ("code", "filename", "modules")

    def __init__(self, code, filename, modules):
        self.code = code
        self.filename = filename
        self.modules = modules

    def rows(self, module=None):
        if module is not None:
            mod = self.modules.get(module)
            return list(mod.rows) if mod else []
        out = []
        for mod in self.modules.values():
            out.extend(mod.rows)
        return out


class Bundle(object):
    """워크북 묶음 전체."""

    def __init__(self, subjects, unreadable, module_order, n_sheets, parse_fail_rows,
                 skipped_files, paths=()):
        self.subjects = subjects
        #: 실제로 읽은 워크북 경로. 산출물이 입력을 덮어쓰지 못하게 막는 데 쓴다.
        self.paths = list(paths)
        self.unreadable = unreadable          # [(표시용 파일명, 사유)]
        self.module_order = module_order      # 워크북에 적힌 시트 순서
        self.n_sheets = n_sheets
        self.parse_fail_rows = parse_fail_rows  # [(코드, 모듈, 엑셀행, 사유)]
        self.skipped_files = skipped_files    # [(표시용 파일명, 사유)] — xlsx 아님 등

    @property
    def n_workbooks(self):
        return len(self.subjects)

    @property
    def n_data_rows(self):
        return sum(len(s.rows()) for s in self.subjects)

    def by_code(self):
        return {s.code: s for s in self.subjects}

    def unreadable_codes(self):
        """못 읽은 워크북의 파일명에서 뽑아낸 피험자 코드들.

        "워크북이 없다"와 "워크북을 못 읽었다"는 다른 사실이고, 사람이 할 일도
        다르다(찾아보기 vs 벤더에게 다시 받기).
        """
        out = set()
        for display, _reason in self.unreadable:
            token = display.split("_")[0]
            if token and token != "?":
                out.add(token)
        return out

    def date_span(self):
        dates = [r.date for s in self.subjects for r in s.rows() if r.usable]
        return (min(dates), max(dates)) if dates else (None, None)


def subject_code_from_filename(filename):
    """`{AccessCode}_{LoginID}_{n}.xlsx` 에서 **Access Code(첫 토큰)** 만 뽑는다.

    `LoginID` 는 개인 식별이 가능하므로 **읽되 절대 출력하지 않는다** — 여기서
    아예 버린다. 코드 길이는 8자와 10자 두 종류가 실재하므로 **길이로 검증하지
    않는다**(`3D609A5O` vs `OBPOZZXNJX`).
    """
    stem = nfc(os.path.basename(filename))
    for ext in (".xlsx", ".xlsm"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    token = stem.split("_")[0].strip()
    if not _CODE_RE.match(token):
        raise ReadError("파일명에서 피험자 코드(첫 토큰)를 뽑지 못했습니다")
    return token


def _find_header(rows):
    """`날짜` 로 시작하는 행을 찾아 헤더로 삼는다. 못 찾으면 None."""
    for idx, (_excel_row, cells) in enumerate(rows):
        if cells and nfc(cells[0]).strip() == SPEC_HEADER[0]:
            return idx
    return None


def _vendor_summary(rows, upto):
    """헤더 위쪽의 `[요약]` 블록을 `{항목: 원문값}` 으로 읽는다.

    **키와 값 모두 `safe_text` 를 거친다.** 이 블록은 벤더가 채워 보내는 칸이고,
    그 원문은 콘솔과 공유용 `노출량점검.md` 에 그대로 인쇄된다. 거르지 않으면
    칸에 적힌 담당자 실명이 리포트로 새고, 줄바꿈 한 번으로 `[치명]` 줄을
    위조할 수 있다 — 파일명·시트이름에 대해 이미 막아 둔 것과 같은 구멍이다.
    """
    out = {}
    for _excel_row, cells in rows[:upto]:
        if len(cells) >= 2 and nfc(cells[0]).strip().startswith("평균"):
            key = safe_text(nfc(cells[0]).strip(), max_len=60)
            out[key] = safe_text(nfc(cells[1]).strip(), max_len=60)
    return out


def _parse_sheet(code, sheet_name, rows, display_name):
    header_idx = _find_header(rows)
    if header_idx is None:
        raise RefuseError(
            "'%s' 시트 '%s' 에서 `날짜` 로 시작하는 헤더 행을 찾지 못했습니다.\n"
            "       이 툴이 읽는 규격은 `%s` 5열입니다. 형식이 바뀌었을 수 있습니다 —\n"
            "       추측해서 세는 대신 판정하지 않습니다."
            % (safe_text(display_name), safe_text(sheet_name), " · ".join(SPEC_HEADER))
        )
    header = [nfc(c).strip() for c in rows[header_idx][1]]
    if tuple(header[:5]) != SPEC_HEADER or len(header) < 5:
        raise RefuseError(
            "'%s' 시트 '%s' 의 헤더가 5열 규격과 다릅니다.\n"
            "         기대: %s\n"
            "         실제: %s\n"
            "       추측해서 세지 않습니다 — 벤더에게 규격을 확인하세요."
            % (safe_text(display_name), safe_text(sheet_name),
               " · ".join(SPEC_HEADER), " · ".join(safe_text(h, 40) for h in header[:8]) or "(빈 행)")
        )

    vendor = _vendor_summary(rows, header_idx)
    header_excel_row = rows[header_idx][0]
    out_rows = []
    fails = []
    for excel_row, cells in rows[header_idx + 1:]:
        padded = list(cells) + [""] * (5 - len(cells))
        if not any(nfc(c).strip() for c in padded[:5]):
            # 규격 5열이 전부 비었는데 그 **뒤쪽 열**에는 내용이 있는 행.
            # 조용히 버리면 "데이터 1행"이라고 말하면서 2행이 사라진다.
            if any(nfc(c).strip() for c in cells[5:]):
                fails.append((code, sheet_name, excel_row,
                              "규격 5열이 모두 비었고 그 뒤 열에만 값이 있음 — 세지 않았습니다"))
            continue
        row_fails = []
        try:
            date = parse_date(padded[0])
        except ValueError_ as exc:
            date = None
            row_fails.append("날짜:%s" % exc)
        try:
            reps = strip_unit(padded[3], COUNT_UNITS)
        except ValueError_ as exc:
            reps = None
            row_fails.append("반복횟수:%s" % exc)
        try:
            seconds = strip_unit(padded[4], SECONDS_UNITS)
        except ValueError_ as exc:
            seconds = None
            row_fails.append("소요시간:%s" % exc)
        ttype = nfc(padded[2]).strip()
        level = nfc(padded[1]).strip()
        fingerprint = (nfc(padded[0]).strip(), level, ttype,
                       nfc(padded[3]).strip(), nfc(padded[4]).strip())
        out_rows.append(LogRow(date, level, ttype, reps, seconds, excel_row,
                               tuple(row_fails), fingerprint))
        for reason in row_fails:
            fails.append((code, sheet_name, excel_row, reason))
    return ModuleData(sheet_name, out_rows, vendor, header_excel_row), fails


def _candidate_files(logs_path):
    """`--logs` 가 가리키는 곳에서 읽을 워크북 목록과 건너뛴 것들을 모은다."""
    if os.path.isfile(logs_path):
        # 파일 하나를 직접 준 경우에도 **폴더와 같은 기준으로** 본다. 여기서
        # 그냥 통과시키면 단일 이벤트 로그 CSV 가 "피험자 코드를 못 뽑았다"는
        # 엉뚱한 사유로 종료코드 3(판정 불가) 이 되는데, 문서가 약속한 값은
        # 2(판정 없이 거절 → `logflow` 로 안내)다.
        lower = logs_path.lower()
        if lower.endswith((".csv", ".tsv")):
            raise RefuseError(
                "`--logs` 가 CSV 파일 하나를 가리킵니다.\n"
                "       단일 이벤트 로그 CSV 는 이 툴의 입력이 아닙니다 → `logflow` 를 쓰세요.\n"
                "       이 툴은 피험자별 워크북(.xlsx) **묶음**을 읽습니다."
            )
        if lower.endswith(".xls"):
            raise RefuseError(
                "구형 `.xls` 는 읽지 않습니다 — 엑셀에서 `.xlsx` 로 저장한 뒤 다시 주세요.")
        if not lower.endswith((".xlsx", ".xlsm")):
            raise RefuseError(
                "`--logs` 가 워크북(.xlsx)이 아닌 파일을 가리킵니다: %s"
                % safe_text(os.path.basename(logs_path)))
        return [logs_path], []
    if not os.path.isdir(logs_path):
        if os.path.exists(logs_path):
            raise RefuseError(
                "`--logs` 가 일반 파일도 폴더도 아닙니다: %s"
                % safe_text(os.path.basename(logs_path) or logs_path))
        raise RefuseError("`--logs` 경로가 없습니다: %s" % safe_text(logs_path))

    files, skipped = [], []
    try:
        entries = sorted(os.listdir(logs_path))
    except OSError as exc:
        raise RefuseError("`--logs` 폴더를 읽지 못했습니다: %s" % type(exc).__name__) from exc
    for name in entries:
        if name.startswith("~$") or name.startswith("."):
            continue
        full = os.path.join(logs_path, name)
        # 건너뛴 파일의 이름도 **외부 입력**이다. 마스킹하지 않으면 Login ID 와
        # 사람 이름이 리포트로 새고, 개행을 심어 `[치명]` 줄을 위조할 수 있다.
        shown = mask_filename(name, mask_head=True)
        if os.path.isdir(full):
            skipped.append((shown, "하위 폴더 — 한 겹만 읽습니다"))
            continue
        lower = name.lower()
        if lower.endswith((".xlsx", ".xlsm")):
            files.append(full)
        elif lower.endswith((".csv", ".tsv")):
            skipped.append((shown, "CSV — 이 툴은 워크북 묶음을 읽습니다"))
        elif lower.endswith(".xls"):
            skipped.append((shown, "구형 .xls — 엑셀에서 .xlsx 로 저장하세요"))
        else:
            skipped.append((shown, "워크북이 아님"))
    return files, skipped


def load_bundle(logs_path):
    """`--logs` 를 읽어 `Bundle` 로 돌려준다.

    - 못 읽은 워크북은 죽지 않고 `unreadable` 에 쌓인다 → 종료코드 3.
    - 헤더 규격이 다르면 그 자리에서 `RefuseError` → 종료코드 2.
    """
    files, skipped = _candidate_files(logs_path)
    if not files:
        if any("CSV" in reason for _n, reason in skipped):
            raise RefuseError(
                "워크북(.xlsx)이 하나도 없고 CSV 만 있습니다.\n"
                "       단일 이벤트 로그 CSV 는 이 툴의 입력이 아닙니다 → `logflow` 를 쓰세요."
            )
        raise RefuseError("`--logs` 에서 읽을 워크북(.xlsx)을 하나도 찾지 못했습니다")

    subjects, unreadable, parse_fails = [], [], []
    module_order, n_sheets = [], 0
    seen_codes = {}
    for path in files:
        # 파일명 가운데 토큰(Login ID)은 화면에도 남기지 않는다.
        display = mask_filename(os.path.basename(path))
        try:
            code = subject_code_from_filename(path)
        except ReadError as exc:
            # 첫 토큰이 Access Code 가 아니면 그것이 사람 이름일 수 있다
            # (`홍길동_hong1234_1.xlsx`). 코드가 아니라고 판단한 값을 그대로
            # 인쇄하지 않는다 — 자리만 남긴다.
            unreadable.append((mask_filename(os.path.basename(path), mask_head=True),
                               str(exc)))
            continue
        try:
            sheets = read_workbook(path)
        except ReadError as exc:
            unreadable.append((display, str(exc)))
            continue
        if code in seen_codes:
            raise RefuseError(
                "피험자 코드 '%s' 를 가진 워크북이 둘입니다 (%s, %s).\n"
                "       어느 쪽이 맞는지 이 툴은 알 수 없습니다 — 합치지 않고 거절합니다."
                % (safe_text(code), safe_text(seen_codes[code]), display)
            )
        modules = {}
        seen_sheet_names = set()
        for sheet_name, rows in sheets:
            n_sheets += 1
            # **자르기 전에** 구분한다. 잘린 뒤에 비교하면 서로 다른 긴 이름이
            # 같아져서, 멀쩡한 워크북을 "이름이 같은 시트가 둘"이라고 거절한다.
            full_name = safe_text(nfc(sheet_name).strip(), max_len=400)
            name = full_name if len(full_name) <= 60 else full_name[:59] + "\u2026"
            if full_name in seen_sheet_names:
                # macOS 는 같은 한글 이름을 NFC/NFD 두 가지로 줄 수 있다. 덮어쓰면
                # 먼저 읽은 시트의 행이 통째로 사라지면서 "시트 2장"이라고 말한다.
                raise RefuseError(
                    "'%s' 에 이름이 같은 시트가 둘입니다: '%s'.\n"
                    "       (유니코드 정규화 후 같아지는 경우를 포함합니다.)\n"
                    "       어느 쪽이 맞는지 추측하지 않습니다 — 시트 이름을 구분해 주세요."
                    % (display, name))
            seen_sheet_names.add(full_name)
            module, fails = _parse_sheet(code, name, rows, display)
            if name in modules:
                # 잘린 표시 이름이 겹치면 번호를 붙인다. **번호는 반드시 올라가야
                # 한다** — 후보를 매번 같은 값으로 다시 만들면 영원히 돌고, 멈추지
                # 않는 툴은 틀린 답보다 나쁘다(빈 답조차 주지 않는다).
                base = name[:57]
                suffix = 2
                candidate = "%s~%d" % (base, suffix)
                while candidate in modules:
                    suffix += 1
                    candidate = "%s~%d" % (base, suffix)
                name = candidate
            modules[name] = module
            parse_fails.extend(fails)
            if name not in module_order:
                module_order.append(name)
        seen_codes[code] = display
        subjects.append(SubjectLog(code, display, modules))

    if not subjects and not unreadable:
        raise RefuseError("워크북을 하나도 읽지 못했습니다 — 입력 경로를 확인하세요")
    # 전부 못 읽었더라도 **거절(2)이 아니라 판정 불가(3)** 이다. 그리고 사유 목록을
    # 버리지 않는다 — 무엇을 못 봤는지가 이 리포트의 본문이다.
    return Bundle(subjects, unreadable, module_order, n_sheets, parse_fails, skipped,
                  paths=files)
