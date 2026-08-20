"""반올림 인지 매칭.

원고에 `12.4` 라고 적혀 있고 출력에 `12.44` 가 있으면 그건 같은 값입니다.
원고에 `-3.47`, 출력에 `-3.4749` 도 같은 값입니다. 문제는 **반올림 방식이
소프트웨어마다 다르다**는 것입니다:

* `12.35` 를 1자리로 → 정확한 half-up 은 `12.4`, 실제 R/Python/SPSS 는
  이진 부동소수 표현(12.3499999…) 때문에 `12.3` 을 냅니다.
* `-3.475` 를 2자리로 → half-up 은 `-3.48`, 절반을 버리면 `-3.47` 입니다.

그래서 half-up · half-even · half-down 과 부동소수 반올림을 모두 계산해
**하나라도 맞으면 매칭**으로 봅니다. 오탐(있는 출처를 없다고 하는 것)이
미탐보다 훨씬 비싸기 때문입니다 — 없는 '치명'이 한 번 뜨면 사람은 리포트 전체를
믿지 않습니다.

의미는 보지 않습니다. `12.4` 가 ISI 평균인지 나이 평균인지 추정하지 않습니다.
값이 어디에 있는지만 **좌표로** 알려주고, 판단은 사람이 합니다.
"""

import bisect
from decimal import (Decimal, InvalidOperation, ROUND_HALF_DOWN,
                     ROUND_HALF_EVEN, ROUND_HALF_UP)
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .bundle import Cell

MATCH_EXACT = "정확"
MATCH_ROUND = "반올림"
MATCH_PERCENT = "백분율환산"
MATCH_INEQ = "부등호"
MATCH_ABS = "부호무시"


def norm_key(value: Decimal) -> str:
    """색인 키 — `12.40`/`12.4`, `-0`/`0` 을 같은 문자열로 만듭니다."""
    if value == 0:
        return "0"
    try:
        return format(value.normalize(), "f")
    except (InvalidOperation, ValueError):
        return format(value, "f")


def rounded_keys(value: Decimal, decimals: int) -> Set[str]:
    """출력값 하나를 소수 `decimals` 자리로 적었을 때 **나올 수 있는 표기들**."""
    keys: Set[str] = set()
    quant = Decimal(1).scaleb(-decimals)
    # 세 규칙 전부 허용합니다. 갈리는 건 정확히 절반(…5)인 경계값뿐이고,
    # '있는 출처를 없다'고 말하는 쪽이 그 반대보다 훨씬 비쌉니다.
    for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_HALF_DOWN):
        try:
            keys.add(norm_key(value.quantize(quant, rounding=mode)))
        except (InvalidOperation, ValueError):
            pass
    # 실제 통계 소프트웨어는 이진 부동소수 위에서 반올림합니다.
    try:
        as_float = float(value)
        if as_float == as_float and abs(as_float) != float("inf"):
            keys.add(norm_key(Decimal(repr(round(as_float, decimals)))))
    except (OverflowError, ValueError, InvalidOperation):
        pass
    return keys


class NumberIndex:
    """번들 수치 셀 색인. 원고에 나온 소수 자릿수에 대해서만 색인합니다."""

    def __init__(self, cells: Iterable[Cell], decimals_needed: Iterable[int]):
        self.cells: List[Cell] = list(cells)
        self._decimals = sorted({d for d in decimals_needed if 0 <= d <= 12})
        self._map: Dict[Tuple[int, str], List[int]] = {}
        self._absmap: Dict[Tuple[int, str], List[int]] = {}   # 음수 셀만
        self._sorted: List[Tuple[Decimal, int]] = []
        self._by_coord: Dict[Tuple[str, str, Optional[int], str], List[int]] = {}
        self._values: List[Decimal] = []
        self._build()

    def _build(self) -> None:
        for i, cell in enumerate(self.cells):
            negative = cell.value < 0
            for decimals in self._decimals:
                for key in rounded_keys(cell.value, decimals):
                    self._map.setdefault((decimals, key), []).append(i)
            if negative:
                # 양수 셀의 절댓값 키는 `_map` 과 글자 그대로 같습니다. 그것까지
                # 다시 저장하면 색인 시간과 메모리가 정확히 두 배가 됩니다.
                for decimals in self._decimals:
                    for key in rounded_keys(-cell.value, decimals):
                        self._absmap.setdefault((decimals, key), []).append(i)
            self._by_coord.setdefault(cell.coord, []).append(i)
        self._sorted = sorted(((c.value, i) for i, c in enumerate(self.cells)),
                              key=lambda pair: pair[0])
        self._values = [pair[0] for pair in self._sorted]

    def lookup(self, value: Decimal, decimals: int) -> List[Cell]:
        idxs = self._map.get((decimals, norm_key(value)))
        if not idxs:
            return []
        return [self.cells[i] for i in idxs]

    def lookup_abs(self, value: Decimal, decimals: int) -> List[Cell]:
        """부호를 무시한 조회.

        원고는 "14.6분 **감소**"라고 쓰고 출력은 `-14.63` 으로 저장합니다. 이건
        같은 결과인데 값만 보면 안 맞습니다 — 실제 원고로 돌려 보니 거짓 치명의
        가장 큰 원인이었습니다. 정식 매칭으로 올리지는 않고(방향이 뒤집혔을
        가능성이 진짜로 있으므로) 경고로 내려서 사람이 보게 합니다.
        """
        magnitude = -value if value < 0 else value
        key = (decimals, norm_key(magnitude))
        idxs = list(self._map.get(key, ())) + list(self._absmap.get(key, ()))
        return [self.cells[i] for i in sorted(set(idxs))]

    def lookup_range(self, low: Optional[Decimal], high: Optional[Decimal],
                     *, include_low: bool = True,
                     include_high: bool = False) -> List[Cell]:
        """`<0.001` 같은 부등호 표기를 위한 구간 조회."""
        if not self._values:
            return []
        start = 0 if low is None else bisect.bisect_left(self._values, low)
        end = len(self._values) if high is None else bisect.bisect_right(
            self._values, high)
        out = []
        for value, i in self._sorted[start:end]:
            if low is not None and not include_low and value == low:
                continue
            if high is not None and not include_high and value == high:
                continue
            out.append(self.cells[i])
        return out

    def nearest(self, value: Decimal, limit: int = 1) -> List[Cell]:
        """가장 가까운 값 — '없음'이라고만 하지 않고 근거를 같이 보여 주려고 씁니다."""
        if not self._values:
            return []
        pos = bisect.bisect_left(self._values, value)
        candidates = []
        for i in range(max(0, pos - 6), min(len(self._sorted), pos + 7)):
            val, idx = self._sorted[i]
            if self.cells[idx].from_label:
                continue        # `phq9_change` 의 9 를 '가장 가까운 값'이라 하면 안 됩니다
            candidates.append((abs(val - value), idx))
        candidates.sort(key=lambda pair: (pair[0], pair[1]))
        return [self.cells[idx] for _, idx in candidates[:limit]]

    def at_coord(self, coord) -> List[Cell]:
        return [self.cells[i] for i in self._by_coord.get(coord, [])]


def needed_decimals(decimals: Iterable[int]) -> Set[int]:
    """원고 자릿수 + 백분율 환산(±2자리)까지 미리 잡아 둡니다."""
    out: Set[int] = set()
    for d in decimals:
        if d < 0 or d > 12:
            continue
        out.add(d)
        out.add(min(12, d + 2))
        out.add(max(0, d - 2))
    return out


def match_method(value: Decimal, cells: List[Cell], decimals: int) -> str:
    for cell in cells:
        if cell.value == value:
            return MATCH_EXACT
    return "%s(%d자리)" % (MATCH_ROUND, decimals)


def distinct_values(cells: List[Cell]) -> List[Decimal]:
    seen: List[Decimal] = []
    for cell in cells:
        if cell.value not in seen:
            seen.append(cell.value)
    return sorted(seen)


def inequality_bounds(op: str, value: Decimal) -> Tuple[Optional[Decimal],
                                                        Optional[Decimal],
                                                        bool, bool]:
    """`<0.001` → (하한, 상한, 하한포함, 상한포함).

    p 값 표기가 대부분이라 하한은 0 으로 둡니다(음수 p 는 없습니다).
    """
    if op in ("<", "<="):
        # p 값처럼 0~1 사이의 양수일 때만 하한을 0 으로 잡습니다. 모든 `<` 에
        # 0 을 깔면 "차이는 <2.0" 같은 표기에서 음수 출력값을 놓칩니다.
        low = Decimal(0) if 0 < value <= 1 else None
        return low, value, True, op == "<="
    if op in (">", ">="):
        return value, None, op == ">=", True
    return None, None, True, True
