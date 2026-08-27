"""재식별 위험 — 준식별자 동치류 크기(k) 계산.

가명화는 이름·전화번호를 지우는 일이고, 재식별은 **남은 것들의 조합**으로
일어납니다. `생년 + 성별 + 방문일` 만으로 24명 중 24명이 각각 유일해지면
그 파일은 이름이 없어도 이름이 있는 것과 같습니다.

**반복측정 주의**: long 형식(피험자 × 시점)에서 행 단위로 k 를 세면
같은 사람의 12개 행이 "동치류 크기 12"로 보여 안전한 것처럼 보입니다.
그래서 피험자 ID 열을 알 수 있으면 **행이 아니라 서로 다른 사람 수**로
k 를 셉니다. 어느 쪽으로 셌는지는 리포트에 그대로 적습니다.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

from .tabular import Table

TARGET_K = 5
MAX_FULL_SEARCH_COLUMNS = 8


@dataclass
class Scenario:
    """준식별자 일부를 뺐을 때의 결과."""

    removed: Tuple[str, ...]
    min_k: int
    n_units_k1: int
    n_units_lt_target: int

    @property
    def label(self) -> str:
        return ", ".join(self.removed) if self.removed else "(제거 없음)"


@dataclass
class KResult:
    """표 하나에 대한 재식별 위험 계산 결과."""

    file: str
    sheet: str
    quasi_used: List[str]
    quasi_missing: List[str]
    unit: str  # "사람" 또는 "행"
    id_column: Optional[str]
    n_units: int
    n_classes: int
    min_k: int
    n_units_k1: int
    n_units_lt_target: int
    size_distribution: Counter = field(default_factory=Counter)
    scenarios: List[Scenario] = field(default_factory=list)
    best_removal: Optional[Scenario] = None
    notes: List[str] = field(default_factory=list)
    searched_all_subsets: bool = True


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    return text if text else "(빈값)"


def _units_by_key(
    table: Table, quasi_idx: Sequence[int], id_idx: Optional[int]
) -> Tuple[Dict[Tuple[str, ...], set], str]:
    """준식별자 조합 → 단위(사람 또는 행) 집합."""
    buckets: Dict[Tuple[str, ...], set] = defaultdict(set)
    if id_idx is not None:
        unit = "사람"
        for r, row in enumerate(table.rows):
            key = tuple(_norm(row[i]) if i < len(row) else "(빈값)" for i in quasi_idx)
            subject = _norm(row[id_idx]) if id_idx < len(row) else "(빈값)"
            buckets[key].add(subject)
    else:
        unit = "행"
        for r, row in enumerate(table.rows):
            key = tuple(_norm(row[i]) if i < len(row) else "(빈값)" for i in quasi_idx)
            buckets[key].add(r)
    return buckets, unit


def _summarize(buckets: Dict[Tuple[str, ...], set], target: int) -> Tuple[int, int, int, Counter]:
    """동치류 크기 분포와 **서로 다른 단위 수** 기준의 위험 집계.

    반복측정 자료에서는 한 사람이 여러 동치류에 걸칩니다. 그래서 위험한
    사람 수를 셀 때 (동치류 × 사람) 쌍이 아니라 **서로 다른 사람**을 셉니다
    — 그러지 않으면 8명짜리 파일에서 "위험한 사람 24명"이 나옵니다.
    """
    sizes = Counter(len(v) for v in buckets.values())
    if not sizes:
        return 0, 0, 0, sizes
    min_k = min(sizes)
    at_risk_unique: set = set()
    at_risk_below: set = set()
    for members in buckets.values():
        if len(members) == 1:
            at_risk_unique |= members
        if len(members) < target:
            at_risk_below |= members
    return min_k, len(at_risk_unique), len(at_risk_below), sizes


def _count_units(buckets: Dict[Tuple[str, ...], set]) -> int:
    """서로 다른 단위(사람 또는 행)의 수."""
    seen: set = set()
    for members in buckets.values():
        seen |= members
    return len(seen)


def compute_k(
    table: Table,
    quasi: Sequence[str],
    id_column: Optional[str] = None,
    target: int = TARGET_K,
) -> Optional[KResult]:
    """표 하나에 대해 동치류 크기 분포와 열 제거 시나리오를 계산합니다.

    Args:
        table: 대상 표.
        quasi: 준식별자 열 이름 목록(이 표에 없는 이름은 자백에 남습니다).
        id_column: 피험자 ID 열 이름(있으면 사람 단위로 셉니다).
        target: 안전 기준 k(기본 5).

    Returns:
        KResult, 또는 이 표에 준식별자가 하나도 없으면 None.
    """
    present: List[Tuple[str, int]] = []
    missing: List[str] = []
    for name in quasi:
        idx = table.column_index(name)
        if idx is None:
            missing.append(name)
        else:
            present.append((table.columns[idx], idx))
    if not present:
        return None

    id_idx = table.column_index(id_column) if id_column else None
    quasi_idx = [i for _, i in present]
    quasi_names = [n for n, _ in present]

    buckets, unit = _units_by_key(table, quasi_idx, id_idx)
    min_k, n_k1, n_lt, sizes = _summarize(buckets, target)
    n_units = _count_units(buckets)

    result = KResult(
        file=table.file,
        sheet=table.sheet,
        quasi_used=quasi_names,
        quasi_missing=missing,
        unit=unit,
        id_column=table.columns[id_idx] if id_idx is not None else None,
        n_units=n_units,
        n_classes=len(buckets),
        min_k=min_k,
        n_units_k1=n_k1,
        n_units_lt_target=n_lt,
        size_distribution=sizes,
    )
    if id_idx is None:
        result.notes.append(
            "피험자 ID 열을 특정하지 못해 **행 단위**로 셌습니다. 같은 사람이 여러 행이면 k 가 실제보다 커 보입니다 "
            "(`--link-id` 로 ID 열을 지정하면 사람 단위로 셉니다)."
        )

    # 열 제거 시나리오
    m = len(present)
    result.searched_all_subsets = m <= MAX_FULL_SEARCH_COLUMNS
    max_remove = m if result.searched_all_subsets else 2
    best: Optional[Scenario] = None
    singles: List[Scenario] = []
    for size in range(1, max_remove + 1):
        found_at_this_size: List[Scenario] = []
        for removed in combinations(range(m), size):
            keep = [quasi_idx[i] for i in range(m) if i not in removed]
            removed_names = tuple(quasi_names[i] for i in removed)
            if keep:
                sub_buckets, _ = _units_by_key(table, keep, id_idx)
            else:
                # 준식별자를 전부 빼면 전원이 한 동치류입니다.
                sub_buckets = {(): set().union(*buckets.values()) if buckets else set()}
            s_min, s_k1, s_lt, _ = _summarize(sub_buckets, target)
            scenario = Scenario(removed=removed_names, min_k=s_min, n_units_k1=s_k1, n_units_lt_target=s_lt)
            if size == 1:
                singles.append(scenario)
            found_at_this_size.append(scenario)
        if best is None:
            reaching = [s for s in found_at_this_size if s.min_k >= target]
            if reaching:
                best = max(reaching, key=lambda s: s.min_k)
    result.scenarios = sorted(singles, key=lambda s: (-s.min_k, s.label))
    result.best_removal = best
    if best is None and not result.searched_all_subsets:
        result.notes.append(
            f"준식별자가 {m}개라 전수 조합 탐색을 하지 않고 1~2개 제거까지만 봤습니다."
        )
    return result
