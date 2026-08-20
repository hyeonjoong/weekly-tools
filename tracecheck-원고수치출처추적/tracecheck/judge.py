"""판정 — 원고 숫자 하나하나에 등급과 근거를 답니다.

등급은 셋뿐입니다.

* **치명** — 현재 번들 어디에도 없는 숫자. `--previous` 를 줬고 옛 번들에만
  있으면 '재분석 후 갱신 누락'으로 더 강하게 말합니다.
* **경고** — 매칭은 됐지만 사람이 한 번 봐야 하는 것: 백분율 환산으로만 맞음,
  어느 출력값을 반올림한 건지 확정 불가, 우연 매칭 의심.
* **정보** — 출처가 확인됨. 파일·행·열을 적습니다.

근거 없이 '치명'만 찍는 출력은 사람이 검증할 수 없고, 검증할 수 없는 경고는
무시됩니다. 그래서 매칭마다 `매칭수`와 `출처위치`를 항상 남깁니다.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from .bundle import Cell
from .match import (MATCH_ABS, MATCH_INEQ, MATCH_PERCENT, NumberIndex,
                    distinct_values, inequality_bounds, match_method)
from .numbers import Number, plain

GRADE_CRITICAL = "치명"
GRADE_WARN = "경고"
GRADE_INFO = "정보"
GRADE_SKIP = "건너뜀"

VERDICT_MATCHED = "출처확인"
VERDICT_MISSING = "출처없음"
VERDICT_STALE = "구버전잔존"
VERDICT_AMBIGUOUS = "출처모호"
VERDICT_PERCENT = "단위확인요망"
VERDICT_CHANCE = "우연매칭의심"
VERDICT_SIGN = "부호확인요망"
VERDICT_LABEL = "라벨문자열매칭"


@dataclass
class Judgement:
    number: Number
    grade: str
    verdict: str
    method: str = ""
    matches: List[Cell] = field(default_factory=list)
    prev_matches: List[Cell] = field(default_factory=list)
    current_at_coord: Optional[Cell] = None
    note: str = ""
    advice: str = ""

    @property
    def match_count(self) -> int:
        return len(self.matches) or len(self.prev_matches)

    @property
    def source_files(self) -> str:
        cells = self.matches or self.prev_matches
        seen = []
        for cell in cells:
            if cell.file not in seen:
                seen.append(cell.file)
        return " · ".join(seen[:3]) + (" 외" if len(seen) > 3 else "")

    @property
    def source_locs(self) -> str:
        cells = self.matches or self.prev_matches
        return " · ".join("%s %s" % (c.file, c.loc) for c in cells[:3]) + \
            (" 외 %d곳" % (len(cells) - 3) if len(cells) > 3 else "")

    @property
    def source_raws(self) -> str:
        cells = self.matches or self.prev_matches
        seen = []
        for cell in cells:
            key = str(cell.value)
            if key not in seen:
                seen.append(key)
        return " · ".join(seen[:4]) + (" 외" if len(seen) > 4 else "")


def judge_number(number: Number, current: NumberIndex,
                 previous: Optional[NumberIndex], *,
                 chance_matches: int = 12) -> Judgement:
    """숫자 하나를 판정합니다."""
    matches, method = _find(number, current)
    if matches:
        return _grade_matched(number, matches, method, current,
                              chance_matches=chance_matches)
    # 순서가 중요합니다. 이전 번들의 **값 일치**(강한 증거)를 현재 번들의
    # 부호 무시 매칭(약한 증거)보다 먼저 봅니다. 반대로 하면, 현재 번들 어딘가에
    # 우연히 같은 크기의 음수가 있다는 이유만으로 진짜 '구버전 잔존'이
    # 부호 경고로 숨어 버립니다(2라운드에서 실제로 재현됐습니다).
    if previous is not None:
        prev_matches, prev_method = _find(number, previous)
        if prev_matches:
            return _stale(number, prev_matches, prev_method, current)
    sign_matches = _find_abs(number, current)
    if sign_matches:
        return _sign_only(number, sign_matches)
    if previous is not None:
        prev_abs = _find_abs(number, previous)
        if prev_abs:
            return _stale(number, prev_abs, MATCH_ABS, current)
    return _missing(number, current, previous)


def _find(number: Number, index: NumberIndex):
    """직접 매칭 → (셀 목록, 매칭방식). 못 찾으면 백분율 환산까지 시도합니다."""
    if number.op:
        low, high, inc_low, inc_high = inequality_bounds(number.op, number.value)
        cells = index.lookup_range(low, high, include_low=inc_low,
                                   include_high=inc_high)
        if cells:
            return cells, "%s(%s%s)" % (MATCH_INEQ, number.op, number.value)
        # SPSS·jamovi 는 p 값을 `<.001` 이라고 **문자 그대로** 내보냅니다.
        # 그 셀은 경계값 0.001 로 읽히므로 구간 조회에서 빠집니다.
        cells = index.lookup(number.value, number.decimals)
        if cells:
            return cells, "%s(%s%s, 같은 표기)" % (MATCH_INEQ, number.op, number.value)
        return [], ""
    cells = index.lookup(number.value, number.decimals)
    if cells:
        return cells, match_method(number.value, cells, number.decimals)
    return _find_percent(number, index)


def _find_percent(number: Number, index: NumberIndex):
    """백분율 ↔ 비율 환산 (원고 62.5% ↔ 출력 0.625, 원고 0.63 ↔ 출력 62.5).

    양쪽 다 **좁게** 겁니다. 환산은 증거가 약한 매칭이라, 넓게 걸면 아무 숫자나
    아무 셀에 붙어 진짜 '출처 없음'을 경고로 숨깁니다. 2라운드에서 실제로
    `65`(병상 수)가 `0.65`(표준오차)에 붙어 엉뚱한 갱신 권고까지 나왔습니다.

    * 정방향(원고 % → 출력 비율): 원고에 소수가 있거나 `%` 가 붙은 경우만.
      맨 정수(`65`)는 환산하지 않습니다.
    * 역방향(원고 비율 → 출력 %): 0~1 사이의 소수 2자리 이상만, 그리고
      **소수점이 있는 출력값**만 받습니다(맨 정수 n·순번과 붙는 것을 막습니다).
    """
    if number.is_percent or number.decimals >= 1:
        as_fraction = number.value / Decimal(100)
        cells = index.lookup(as_fraction, min(12, number.decimals + 2))
        if cells:
            return cells, MATCH_PERCENT
    if number.decimals < 2 or not 0 < abs(number.value) < 1:
        return [], ""
    half = Decimal(1).scaleb(-number.decimals) / 2
    low = (number.value - half) * 100
    high = (number.value + half) * 100
    # 반대 방향에서는 **소수점이 있는 출력값만** 받습니다. 맨 정수(n, 순번, id)는
    # 번들에 널려 있어서, 원고의 `0.07` 이 아무 `7` 에나 붙어 치명을 경고로 숨깁니다.
    cells = [c for c in index.lookup_range(low, high, include_low=True,
                                           include_high=False)
             if c.decimals > 0 and not c.from_label]
    if cells:
        return cells, MATCH_PERCENT
    return [], ""


def _find_abs(number: Number, index: NumberIndex) -> List[Cell]:
    """부호만 다른 값 찾기 — 부등호·0 에는 적용하지 않습니다."""
    if number.op or number.value == 0:
        return []
    return [c for c in index.lookup_abs(number.value, number.decimals)
            if not c.from_label]


def _grade_matched(number: Number, matches: List[Cell], method: str,
                   index: NumberIndex, *, chance_matches: int) -> Judgement:
    values = distinct_values(matches)
    if method == MATCH_PERCENT:
        return Judgement(
            number=number, grade=GRADE_WARN, verdict=VERDICT_PERCENT,
            method=MATCH_PERCENT, matches=matches,
            note="백분율↔비율 환산으로만 매칭됨 (출력값 %s). 원고 표기 단위(%% 인지 비율인지)를 확인하세요."
                 % _values_text(values),
            advice="원고의 단위 표기와 출력의 단위가 같은지 확인")
    if matches and all(cell.from_label for cell in matches):
        return Judgement(
            number=number, grade=GRADE_WARN, verdict=VERDICT_LABEL,
            method=method, matches=matches,
            note="같은 값이 번들의 **라벨 문자열 안에서만** 나옵니다 (%s). "
                 "`phq9_change` 의 9 처럼 이름의 일부일 가능성이 큽니다."
                 % matches[0].raw[:40],
            advice="이 숫자가 실제 분석 결과값인지 확인 — 아니면 출처가 없는 것입니다")
    exact = any(cell.value == number.value for cell in matches)
    # 부등호(`p<0.001`)는 애초에 여러 값을 가리키므로 '자릿수 상충'이 아닙니다.
    if not exact and not number.op and len(values) > 1:
        return Judgement(
            number=number, grade=GRADE_WARN, verdict=VERDICT_AMBIGUOUS,
            method=method, matches=matches,
            note="소수 %d자리로는 어느 출력값을 반올림한 건지 확정할 수 없습니다 (후보 %s)."
                 % (number.decimals, _values_text(values)),
            advice="원고에 자릿수를 한 자리 더 적거나, 어느 출력에서 왔는지 확정")
    if (len(matches) >= chance_matches and number.decimals <= 1
            and abs(number.value) < 10):
        return Judgement(
            number=number, grade=GRADE_WARN, verdict=VERDICT_CHANCE,
            method=method, matches=matches,
            note="번들 %d곳에서 같은 값이 나옵니다 — 자릿수가 낮아 우연히 맞았을 수 있습니다."
                 % len(matches),
            advice="이 값이 실제로 어느 출력에서 온 것인지 눈으로 확인")
    return Judgement(
        number=number, grade=GRADE_INFO, verdict=VERDICT_MATCHED,
        method=method, matches=matches,
        note="출처 확인 (%s)" % _values_text(values))


def _stale(number: Number, prev_matches: List[Cell], method: str,
           current: NumberIndex) -> Judgement:
    """옛 번들에만 있는 값 — 이 툴의 존재 이유입니다."""
    at_coord = None
    for cell in prev_matches:
        same = current.at_coord(cell.coord)
        if same:
            at_coord = same[0]
            break
    note = ("현재 번들에 없고, 이전 번들 %s 에만 있습니다(%s). 재분석 후 갱신 누락으로 보입니다."
            % (prev_matches[0].file + " " + prev_matches[0].loc,
               _values_text(distinct_values(prev_matches))))
    if at_coord is not None:
        current_value = at_coord.value
        if method == MATCH_PERCENT:
            # 원고가 %로 적었으면 대응하는 현재 값도 %로 환산해 보여 줘야 합니다.
            current_value = current_value * Decimal(100)
        note += " 현재 번들의 같은 자리 값은 %s 입니다." % plain(current_value)
        if method == MATCH_ABS:
            # 원고는 크기로("4.77점 감소"), 출력은 부호로 적은 경우입니다.
            # 부호까지 그대로 옮겨 적으라고 하면 안 됩니다.
            magnitude = -current_value if current_value < 0 else current_value
            advice = ("원고의 %s 를 현재 값 %s(출력 표기 %s, 부호 규약이 다름) 로 "
                      "갱신할지 확인" % (number.text, plain(magnitude),
                                     plain(current_value)))
        else:
            advice = "원고의 %s 를 현재 값 %s 로 갱신할지 확인" % (number.text,
                                                        plain(current_value))
    else:
        advice = "재분석 결과에서 이 값에 해당하는 항목을 다시 확인"
    return Judgement(
        number=number, grade=GRADE_CRITICAL, verdict=VERDICT_STALE,
        method=method, matches=[], prev_matches=prev_matches,
        current_at_coord=at_coord, note=note, advice=advice)


def _sign_only(number: Number, matches: List[Cell]) -> Judgement:
    """부호만 다른 값이 있을 때.

    원고는 "14.6분 감소", 출력은 `-14.63` — 사람에게는 같은 결과지만 값으로는
    다릅니다. 이걸 치명으로 올리면 실제 원고에서 거짓 치명이 쏟아지고, 정보로
    내리면 방향이 진짜로 뒤집힌 사고를 놓칩니다. 그래서 경고입니다.
    """
    values = distinct_values(matches)
    return Judgement(
        number=number, grade=GRADE_WARN, verdict=VERDICT_SIGN,
        method=MATCH_ABS, matches=matches,
        note="부호만 다른 값이 출력에 있습니다 (%s). 원고가 '감소/증가'로 방향을 "
             "말로 적었다면 정상이지만, 방향이 뒤집힌 것은 아닌지 확인하세요."
             % _values_text(values),
        advice="원고 문장의 방향(감소/증가)과 출력값의 부호가 일치하는지 확인")


def _missing(number: Number, current: NumberIndex,
             previous: Optional[NumberIndex]) -> Judgement:
    note = "현재 번들의 어느 파일에서도 이 값을 찾지 못했습니다."
    nearest = current.nearest(number.value, limit=1)
    if nearest:
        cell = nearest[0]
        note += (" 가장 가까운 값은 %s (%s %s) 입니다."
                 % (plain(cell.value), cell.file, cell.loc))
    if previous is None:
        note += " (`--previous` 미지정 — 구버전 잔존 여부는 검사하지 않았습니다.)"
    return Judgement(
        number=number, grade=GRADE_CRITICAL, verdict=VERDICT_MISSING,
        method="", matches=[], note=note,
        advice="이 숫자가 어느 분석에서 나온 값인지 확인 — 번들에 빠진 출력이 있을 수 있습니다")


def _values_text(values: List[Decimal]) -> str:
    shown = " · ".join(plain(v) for v in values[:4])
    return shown + (" 외" if len(values) > 4 else "")


def judge_all(numbers: List[Number], current: NumberIndex,
              previous: Optional[NumberIndex], *,
              chance_matches: int = 12) -> List[Judgement]:
    return [judge_number(n, current, previous, chance_matches=chance_matches)
            for n in numbers]
