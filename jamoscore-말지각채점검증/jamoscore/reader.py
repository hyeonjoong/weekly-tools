"""시트 분류 · 블록 검출 · 패스 분절.

이 파일이 이 툴의 **가장 위험한 부분**이다. 열 위치를 고정해서 읽으면
`일음절` 블록처럼 열 구성이 다른 블록에서 가짜 불일치가 쏟아진다(기획 세션이
실제로 168셀 중 54셀을 가짜로 만들어 봤다). 그래서 여기서는

* 열을 **헤더 이름으로만** 찾고,
* 문항수·만점을 **추론하지 않고 ``--subtest`` 로 받고**,
* 못 찾으면 **추측하지 않고 멈춘다**.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .hangul import normalize
from .spec import SubtestSpec
from .xlsx import Workbook, column_name

#: 응답 열 헤더로 인정하는 이름.
RESPONSE_ALIASES = ("응답", "반응", "전사", "답", "response", "resp", "answer")
#: 점수 열 헤더로 인정하는 이름.
SCORE_ALIASES = ("점수", "채점", "정오", "score", "scoring", "correct", "정답여부")
#: 식별정보 시트로 보고 **열지 않는** 시트 이름 패턴.
PII_SHEET_PATTERNS = (
    "개인정보", "인적사항", "명단", "연락처", "주민", "대상자정보", "personal",
    "roster", "contact", "identifi",
)
#: 시트를 열어 본 뒤 '식별정보 시트'로 판단하는 열 이름 패턴.
PII_HEADER_PATTERNS = (
    "이름", "성명", "환자명", "연락처", "전화", "휴대폰", "생년월일", "생일",
    "주민등록", "주민번호", "주소", "보호자", "name", "phone", "birth", "dob",
    "address",
)
#: 헤더 행을 찾을 때 훑는 최대 행 수.
HEADER_SCAN_ROWS = 12
#: 문항 덩어리 아래에서 '시트가 적어 둔 요약 %'를 찾을 때 훑는 최대 행 수.
SUMMARY_SCAN_ROWS = 20


class LayoutError(Exception):
    """구조를 알아볼 수 없을 때. CLI 가 exit 2(판정 없이 거절)로 바꾼다."""


class Item:
    """문항 한 개."""

    __slots__ = ("subject", "test", "timepoint", "unit", "seq", "row", "col",
                 "stimulus", "response", "human_raw", "human", "rule",
                 "rule_reason", "sheet")

    def __init__(self, subject, test, timepoint, unit, seq, row, col,
                 stimulus, response, human_raw, sheet):
        self.subject = subject
        self.test = test
        self.timepoint = timepoint
        self.unit = unit
        self.seq = seq
        self.row = row
        self.col = col
        self.stimulus = stimulus
        self.response = response
        self.human_raw = human_raw
        self.human: Optional[float] = None
        self.rule = None
        self.rule_reason = ""
        self.sheet = sheet

    @property
    def where(self) -> str:
        return "%s!%s%d" % (self.sheet, column_name(self.col), self.row)

    def __repr__(self) -> str:
        return "Item(%s %s-%s #%d %r→%r %r)" % (
            self.subject, self.test, self.timepoint, self.seq,
            self.stimulus, self.response, self.human_raw)


class Pass:
    """블록 하나에서 한 번 매긴 채점(= 세로로 쌓인 한 덩어리)."""

    def __init__(self, subject, test, timepoint, spec: SubtestSpec, sheet: str,
                 col: int):
        self.subject = subject
        self.test = test
        self.timepoint = timepoint
        self.spec = spec
        self.sheet = sheet
        self.col = col
        self.items: List[Item] = []
        self.reported: Optional[str] = None
        self.reported_row: Optional[int] = None
        self.notes: List[tuple] = []
        #: 요약 %로 보이지만 확신할 수 없어 버린 셀 값(자백용).
        self.dropped_summary: List[str] = []
        #: 블록이 끊겨 이 패스 아래에 남은, 점수가 적힌 행 수(자백용).
        self.lost_rows = 0

    @property
    def unit(self) -> str:
        return self.spec.unit

    @property
    def key(self) -> Tuple[str, str, str, str]:
        return (self.subject, self.test, self.timepoint, self.unit)

    @property
    def denominator(self) -> int:
        """이 패스의 분모. **패스마다 독립으로 센다.**"""
        return len(self.items) * self.spec.max_points

    def scored_items(self) -> List[Item]:
        return [it for it in self.items if it.human is not None]

    @property
    def earned(self) -> float:
        return sum(it.human for it in self.scored_items())

    @property
    def percent(self) -> Optional[float]:
        if not self.items or self.denominator == 0:
            return None
        if len(self.scored_items()) != len(self.items):
            return None
        return round(100.0 * self.earned / self.denominator, 1)

    def __repr__(self) -> str:
        return "Pass(%s %s-%s %s %d문항)" % (
            self.subject, self.test, self.timepoint, self.unit, len(self.items))


class SheetRoles:
    """어느 시트를 무엇으로 봤는가 — `[커버리지 자백]` 의 재료."""

    def __init__(self):
        self.subjects: List[str] = []
        self.pii: List[str] = []            # 이름만 보고 **열지 않은** 시트
        self.pii_by_header: List[str] = []   # 열었으나 식별정보로 보여 버린 시트
        self.answer_key: Optional[str] = None
        self.summary: Optional[str] = None
        self.skipped: List[Tuple[str, str]] = []   # (시트, 사유)
        self.incomplete: List[Tuple[str, str]] = []  # 헤더는 스쳤으나 블록이 불완전
        self.unreadable: List[Tuple[str, str]] = []

    @property
    def total(self) -> int:
        n = (len(self.subjects) + len(self.pii) + len(self.pii_by_header)
             + len(self.skipped) + len(self.unreadable) + len(self.incomplete))
        return n + (1 if self.answer_key else 0) + (1 if self.summary else 0)

    @property
    def pii_all(self) -> List[str]:
        return list(self.pii) + list(self.pii_by_header)


def looks_like_pii_sheet(name: str, extra: Sequence[str] = ()) -> bool:
    low = normalize(name).lower().replace(" ", "")
    for pat in tuple(PII_SHEET_PATTERNS) + tuple(extra):
        if pat.lower().replace(" ", "") in low:
            return True
    return False


def looks_like_pii_header(rows: Dict[int, Dict[int, str]]) -> bool:
    """열 이름이 이름·연락처·생년월일이면 내용을 쓰지 않는다(이름이 평범해도)."""
    hits = set()
    for rnum in sorted(rows)[:3]:
        for text in rows[rnum].values():
            cell = normalize(text).lower().replace(" ", "")
            if len(cell) > 20:
                continue
            for pat in PII_HEADER_PATTERNS:
                if pat in cell:
                    hits.add(pat)
    return len(hits) >= 2


def _header_matches(text: str, test: str, timepoints: Sequence[str]) -> Optional[str]:
    """헤더 셀이 ``검사-시점`` 인가. 맞으면 시점 이름을 돌려준다."""
    cell = normalize(text).replace(" ", "")
    test_n = normalize(test).replace(" ", "")
    for tp in timepoints:
        tp_n = normalize(tp).replace(" ", "")
        for pattern in (
            "%s%s" % (test_n, tp_n),
            "%s-%s" % (test_n, tp_n),
            "%s_%s" % (test_n, tp_n),
            "%s%s" % (tp_n, test_n),
            "%s-%s" % (tp_n, test_n),
            "%s_%s" % (tp_n, test_n),
        ):
            if cell == pattern:
                return tp
    return None


def _alias_match(text: str, aliases: Sequence[str], exact_only: bool = False) -> bool:
    cell = normalize(text).replace(" ", "").lower()
    if not cell:
        return False
    for alias in aliases:
        low = alias.lower()
        if not low:
            continue          # 빈 별칭은 모든 헤더에 걸린다 — 무시한다
        if cell == low:
            return True
        if not exact_only and cell.startswith(low):
            return True
    return False


def _pick_column(header: Dict[int, str], col: int, aliases: Sequence[str],
                 exclude: Optional[int] = None) -> Optional[int]:
    """자극열 오른쪽 4칸에서 헤더 이름으로 열을 고른다. 정확 일치 우선."""
    window = [col + off for off in range(1, 5) if col + off != exclude]
    for exact_only in (True, False):
        for target in window:
            if _alias_match(header.get(target, ""), aliases, exact_only):
                return target
    return None


def find_header_row(rows: Dict[int, Dict[int, str]], tests: Sequence[str],
                    timepoints: Sequence[str]) -> Optional[int]:
    for rnum in sorted(rows):
        if rnum > HEADER_SCAN_ROWS:
            break
        for text in rows[rnum].values():
            for test in tests:
                if _header_matches(text, test, timepoints):
                    return rnum
    return None


def find_blocks(rows: Dict[int, Dict[int, str]], header_row: int,
                tests: Sequence[str], timepoints: Sequence[str],
                sheet: str, extra_response: Sequence[str] = (),
                extra_score: Sequence[str] = ()
                ) -> List[Tuple[int, str, str, int, int]]:
    """``(자극열, 검사, 시점, 응답열, 점수열)`` 목록.

    응답·점수 열은 자극열 **바로 오른쪽 4칸 이내**에서 헤더 이름으로 찾는다.
    못 찾으면 추측하지 않고 :class:`LayoutError`.
    """
    header = rows.get(header_row, {})
    response_aliases = tuple(RESPONSE_ALIASES) + tuple(extra_response)
    score_aliases = tuple(SCORE_ALIASES) + tuple(extra_score)
    blocks = []
    for col in sorted(header):
        for test in tests:
            tp = _header_matches(header[col], test, timepoints)
            if tp is None:
                continue
            # 정확히 일치하는 헤더를 **접두 일치보다 먼저** 쓴다.
            # (`반응유형` 이 `반응` 접두에 걸려 진짜 `응답` 열을 가로채던 사고.)
            resp_col = _pick_column(header, col, response_aliases)
            score_col = _pick_column(header, col, score_aliases,
                                     exclude=resp_col)
            if resp_col is None or score_col is None:
                missing = []
                if resp_col is None:
                    missing.append("응답")
                if score_col is None:
                    missing.append("점수")
                raise LayoutError(
                    "시트 '%s' %s%d 의 '%s' 블록 오른쪽에서 %s 열을 찾지 못했습니다.\n"
                    "  헤더 이름이 달라 보입니다. 인정하는 이름:\n"
                    "    응답 열 → %s\n    점수 열 → %s\n"
                    "  --inspect 로 실제 헤더를 확인한 뒤 엑셀의 헤더를 고치거나 "
                    "  --response-alias / --score-alias 로 이름을 추가하세요."
                    % (sheet, column_name(col), header_row, header[col],
                       "·".join(missing), ", ".join(response_aliases),
                       ", ".join(score_aliases))
                )
            if score_col < resp_col:
                raise LayoutError(
                    "시트 '%s' 의 '%s' 블록은 점수 열(%s)이 응답 열(%s)보다 왼쪽입니다 — "
                    "예상 밖 배치라 판정하지 않습니다."
                    % (sheet, header[col], column_name(score_col),
                       column_name(resp_col))
                )
            blocks.append((col, test, tp, resp_col, score_col))
            break
    return blocks


def _is_item_row(cells: Dict[int, str], col: int) -> bool:
    return bool(normalize(cells.get(col, "")))


def _is_reachable(value: float, denominator: int, tol: float = 0.051) -> bool:
    """``value`` 가 ``k/denominator`` 로 나올 수 있는 백분율인가(반올림 허용)."""
    if denominator <= 0:
        return False
    for k in range(denominator + 1):
        if abs(100.0 * k / denominator - value) <= tol:
            return True
    return False


_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
#: 1,234 / 1,234,567 처럼 **천 단위 구분**일 때만 쉼표를 지운다.
#: ("1,0" 같은 소수점 쉼표를 10 으로 바꿔 읽던 사고를 막는다.)
_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def parse_number(text: str) -> Optional[float]:
    """'1', '1.0', '77.8', '77.8%' → float. 아니면 None (조용히 0 으로 바꾸지 않는다)."""
    if text is None:
        return None
    s = normalize(text)
    if _THOUSANDS_RE.match(s):
        s = s.replace(",", "")
    if s.endswith("%"):
        s = s[:-1].strip()
    if not _NUMBER_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_passes(rows: Dict[int, Dict[int, str]], header_row: int,
                   block: Tuple[int, str, str, int, int], subject: str,
                   specs: List[SubtestSpec], sheet: str) -> List[Pass]:
    """블록 하나에서 선언된 패스 수만큼 세로로 잘라낸다.

    '연속한 자극 행 덩어리' 하나가 패스 하나다. 덩어리를 건너뛰어 이어 붙이지
    않는다 — 이어 붙이면 아래쪽 다른 표를 문항으로 오인한다.
    """
    col, test, tp, resp_col, score_col = block
    runs: List[List[int]] = []
    current: List[int] = []
    for rnum in sorted(rows):
        if rnum <= header_row:
            continue
        if _is_item_row(rows[rnum], col):
            current.append(rnum)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    # 선언한 패스보다 덩어리가 많으면 **조용히 버리지 않는다.** 중간에 빈 자극 칸
    # 하나가 끼면 그 아래 문항이 통째로 사라지면서도 커버리지는 100% 라고 말하던
    # 사고가 여기서 났다.
    # 아래에 한글이 더 있다고 다 문항은 아니다. 실제 채점 엑셀의 자극 열 아래에는
    # 음소 목록(가·거·고·구·그·기 …)이나 메모가 흔히 붙어 있고, 그것들도 같은
    # 열을 쓴다. **점수 칸이 숫자인 행만** 잃어버린 문항으로 센다 — 이 구분이
    # 없으면 정상 파일에서 치명이 수십 건 쏟아진다(실측 48건).
    leftover = []
    leftover_stimuli = 0
    for run in runs[len(specs):]:
        scored_rows = [r for r in run
                       if parse_number(rows[r].get(score_col, "")) is not None]
        if scored_rows:
            leftover.append(scored_rows)
            leftover_stimuli += len(run)
    leftover_rows = sum(len(r) for r in leftover)
    out: List[Pass] = []
    for idx, spec in enumerate(specs):
        p = Pass(subject, test, tp, spec, sheet, col)
        if idx >= len(runs):
            p.notes.append((
                "결측",
                "선언된 패스 %d(%s)에 해당하는 문항 덩어리가 시트에 없습니다 — "
                "이 피험자는 그 채점을 하지 않은 것으로 보입니다." % (idx + 1, spec.unit)))
            out.append(p)
            continue
        run = runs[idx]
        if spec.n_items is not None and len(run) != spec.n_items:
            p.notes.append((
                "치명",
                "문항수가 선언(%d)과 다릅니다 — 시트에서 %d행을 읽었습니다(%s%d~%s%d)."
                % (spec.n_items, len(run), column_name(col), run[0],
                   column_name(col), run[-1])))
        for seq, rnum in enumerate(run, start=1):
            cells = rows[rnum]
            p.items.append(Item(
                subject=subject, test=test, timepoint=tp, unit=spec.unit,
                seq=seq, row=rnum, col=col, sheet=sheet,
                stimulus=normalize(cells.get(col, "")),
                response=normalize(cells.get(resp_col, "")),
                human_raw=normalize(cells.get(score_col, "")),
            ))
        # 이 덩어리 아래의 '시트가 스스로 적어 둔 %'. 다음 문항 덩어리를 만나면
        # 멈추고, 문항 점수(0..만점)와 구별되는 값만 요약으로 받는다.
        for rnum in [r for r in sorted(rows) if r > run[-1]][:SUMMARY_SCAN_ROWS]:
            if _is_item_row(rows[rnum], col):
                break
            raw = normalize(rows[rnum].get(score_col, ""))
            value = parse_number(raw)
            if value is None:
                continue
            if not (0.0 <= value <= 100.0):
                continue
            if 0.0 < value < 1.0 and not _is_reachable(
                    value, len(run) * spec.max_points):
                # 엑셀의 '백분율 서식' 셀은 0.75 처럼 저장된다. 그대로 받으면
                # "0.75% 는 불가능한 값" 이라는 가짜 치명이 나온다.
                continue
            looks_percentage = ("%" in raw or "." in raw)
            if not looks_percentage:
                # 소수점도 % 기호도 없는 정수는 '정답수' 행일 수 있다. 그 분모로
                # 실제로 나올 수 있는 백분율일 때만 요약으로 받는다.
                denominator = len(run) * spec.max_points
                if not _is_reachable(value, denominator):
                    # 정수로 반올림해 적은 정상 요약(67 = 66.7)일 수도 있다.
                    # 그런 후보를 버렸다는 사실은 호출부가 자백에 적는다.
                    if _is_reachable(value, denominator, tol=0.51):
                        p.dropped_summary.append(raw)
                    continue
            p.reported = raw
            p.reported = raw
            p.reported_row = rnum
            break
        out.append(p)
    if leftover_rows and out:
        out[-1].lost_rows = leftover_rows
        first = leftover[0]
        out[-1].notes.append((
            "치명",
            "선언한 패스 %d개 아래에 자극 행이 %d개(그중 점수가 적힌 행 %d개) 더 "
            "있습니다(%s%d부터). 빈 자극 칸 하나가 블록을 끊었거나, 패스를 덜 "
            "선언했습니다 — 그대로 두면 이 행들이 계산에서 빠집니다."
            % (len(specs), leftover_stimuli, leftover_rows,
               column_name(col), first[0])))
    return out


def classify_sheets(wb: Workbook, tests: Sequence[str], timepoints: Sequence[str],
                    answer_key: Optional[str], summary: Optional[str],
                    subject_pattern: Optional[str],
                    pii_extra: Sequence[str] = (),
                    response_aliases: Sequence[str] = (),
                    score_aliases: Sequence[str] = ()) -> SheetRoles:
    """시트를 역할별로 나눈다. **식별정보 시트는 여기서 이름만 보고 제외** 된다."""
    roles = SheetRoles()
    pat = re.compile(subject_pattern) if subject_pattern else None
    for name in wb.sheet_names:
        if answer_key and name == answer_key:
            roles.answer_key = name
            continue
        if summary and name == summary:
            roles.summary = name
            continue
        if looks_like_pii_sheet(name, pii_extra):
            roles.pii.append(name)
            continue
        if pat is not None and not pat.search(name):
            roles.skipped.append((name, "--subject-pattern 에 맞지 않음"))
            continue
        try:
            rows = wb.read_sheet(name)
        except Exception as exc:                       # pragma: no cover - 방어
            roles.unreadable.append((name, str(exc)))
            continue
        if looks_like_pii_header(rows):
            rows = {}
            roles.pii_by_header.append(name)
            continue
        if not rows:
            roles.skipped.append((name, "빈 시트"))
            continue
        header_row = find_header_row(rows, tests, timepoints)
        if header_row is None:
            roles.skipped.append((name, "검사-시점 헤더 없음(통계·차트 시트로 판단)"))
            continue
        try:
            blocks = find_blocks(rows, header_row, tests, timepoints, name,
                                 response_aliases, score_aliases)
        except LayoutError as exc:
            # 헤더 이름은 스쳤지만 응답·점수 열이 없다. 통계표일 수도, 오타일 수도
            # 있으므로 조용히 버리지 않고 사유를 남긴다(리포트에 인쇄된다).
            roles.incomplete.append((name, str(exc).splitlines()[0]))
            continue
        if not blocks:
            roles.skipped.append((name, "완성된 검사 블록 없음"))
            continue
        roles.subjects.append(name)
    return roles
