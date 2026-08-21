"""주 분석 명세 — 이 툴은 검정을 골라 주지 않는다.

`--design` 과 그 설계가 요구하는 열이 없으면 **아무 판정도 하지 않고 exit 2**.
검정 선택은 `statwise` 의 존재 이유이고, 여기서 근사하는 순간 이 툴은
"논문에 쓴 분석과 무관한 답"을 조용히 내놓게 된다.
"""

from typing import Dict, List, Optional, Sequence, Tuple

from .dataio import InputError, Table, find_duplicate_ids, normalise_id, parse_number

__all__ = ["Spec", "Subject", "Dataset", "build_dataset", "DESIGNS", "MIN_VALID_N"]

DESIGNS = ("two-group", "paired", "corr")

# 유효 N 이 이보다 적으면 견고성을 논할 수 없다 → 종료코드 3(판정불가).
MIN_VALID_N = 6

# 시점 열로 흔히 쓰이는 이름 (long 포맷을 만났을 때 힌트를 주기 위한 것뿐,
# 자동으로 고르지는 **않는다**).
_TIMEPOINT_HINTS = ("timepoint", "time", "visit", "방문", "시점", "week", "주차")

# 사람이 "값이 없다"는 뜻으로 쓰는 표기. 이건 못 읽은 게 아니라 결측이다.
_BLANKS = {"na", "n/a", "nan", "none", "null", "nil", ".", "-", "--",
           "결측", "미측정", "없음", "#n/a", "#null!"}


class Spec:
    """사용자가 명시한 주 분석. 여기 없는 설계는 근사하지 않는다."""

    __slots__ = (
        "design", "id_col", "value", "group", "pre", "post", "x", "y",
        "covariate", "alpha", "timepoint",
    )

    def __init__(
        self,
        design: str,
        id_col: str = "subject_id",
        value: Optional[str] = None,
        group: Optional[str] = None,
        pre: Optional[str] = None,
        post: Optional[str] = None,
        x: Optional[str] = None,
        y: Optional[str] = None,
        covariate: Optional[str] = None,
        alpha: float = 0.05,
        timepoint: Optional[Tuple[str, str]] = None,
    ) -> None:
        self.design = design
        self.id_col = id_col
        self.value = value
        self.group = group
        self.pre = pre
        self.post = post
        self.x = x
        self.y = y
        self.covariate = covariate
        self.alpha = alpha
        self.timepoint = timepoint
        self.validate()

    def validate(self) -> None:
        if self.design not in DESIGNS:
            raise InputError(
                "--design 은 %s 중 하나여야 합니다 (받은 값: %r)."
                % (" / ".join(DESIGNS), self.design)
            )
        if not (0.0 < self.alpha < 1.0):
            raise InputError("--alpha 는 0 과 1 사이여야 합니다 (받은 값: %r)." % self.alpha)
        if self.design == "two-group":
            missing = [n for n, v in (("--group", self.group), ("--value", self.value))
                       if not v]
            if missing:
                raise InputError(
                    "two-group 설계는 %s 가 필요합니다. 이 툴은 검정을 골라 주지 "
                    "않습니다 — 주 분석을 명시해 주세요 (검정 선택은 statwise)."
                    % " · ".join(missing)
                )
        elif self.design == "paired":
            missing = [n for n, v in (("--pre", self.pre), ("--post", self.post))
                       if not v]
            if missing:
                raise InputError(
                    "paired 설계는 %s 가 필요합니다." % " · ".join(missing)
                )
            if self.covariate:
                raise InputError(
                    "paired 설계에는 --covariate-baseline 을 쓸 수 없습니다 "
                    "(--pre 가 이미 기저값입니다). 근사하지 않고 멈춥니다."
                )
        else:  # corr
            missing = [n for n, v in (("--x", self.x), ("--y", self.y)) if not v]
            if missing:
                raise InputError("corr 설계는 %s 가 필요합니다." % " · ".join(missing))
            if self.covariate:
                raise InputError(
                    "corr 설계에는 --covariate-baseline 을 쓸 수 없습니다. "
                    "편상관은 v1 범위 밖이고, 조용히 근사하지 않습니다."
                )
        self._reject_duplicate_columns()

    def _reject_duplicate_columns(self) -> None:
        """같은 열을 두 자리에 쓰면 결과가 자명해진다(`--x v --y v` → r = 1)."""
        pairs = {
            "two-group": (("--value", self.value), ("--group", self.group),
                          ("--covariate-baseline", self.covariate)),
            "paired": (("--pre", self.pre), ("--post", self.post)),
            "corr": (("--x", self.x), ("--y", self.y)),
        }[self.design]
        seen: Dict[str, str] = {}
        for flag, column in pairs:
            if not column:
                continue
            if column in seen:
                raise InputError(
                    "%s 와 %s 에 같은 열 '%s' 을(를) 지정했습니다. 서로 다른 열이어야 "
                    "합니다 — 같은 열을 비교하면 결과가 자명해집니다."
                    % (seen[column], flag, column)
                )
            seen[column] = flag
        if self.id_col and self.id_col in seen:
            raise InputError(
                "피험자 ID 열 '%s' 을(를) %s 에도 지정했습니다."
                % (self.id_col, seen[self.id_col])
            )

    @property
    def numeric_columns(self) -> List[str]:
        if self.design == "two-group":
            cols = [self.value]
            if self.covariate:
                cols.append(self.covariate)
        elif self.design == "paired":
            cols = [self.pre, self.post]
        else:
            cols = [self.x, self.y]
        return [c for c in cols if c]

    @property
    def label(self) -> str:
        if self.design == "two-group":
            base = "two-group, %s, value=%s" % (self.group, self.value)
            return base + (", 기저보정=%s" % self.covariate if self.covariate else "")
        if self.design == "paired":
            return "paired, pre=%s → post=%s" % (self.pre, self.post)
        return "corr, x=%s, y=%s" % (self.x, self.y)


class Subject:
    """피험자 1명 = 입력 1행."""

    __slots__ = ("sid", "group", "fields", "line")

    def __init__(self, sid: str, group: Optional[str],
                 fields: Dict[str, Optional[float]], line: int) -> None:
        self.sid = sid
        self.group = group
        self.fields = fields
        self.line = line

    def get(self, name: str) -> Optional[float]:
        return self.fields.get(name)

    def __repr__(self) -> str:  # pragma: no cover
        return "Subject(%s, group=%r)" % (self.sid, self.group)


class Dataset:
    """분석 대상 피험자 집합 + 읽기 과정에서 알게 된 사실."""

    __slots__ = ("subjects", "spec", "encoding", "path", "n_rows",
                 "group_levels", "dropped_no_id", "column_names", "ragged_rows",
                 "unreadable_cells")

    def __init__(
        self,
        subjects: List[Subject],
        spec: Spec,
        encoding: str,
        path: str,
        n_rows: int,
        group_levels: Tuple[str, ...],
        dropped_no_id: int,
        column_names: Sequence[str],
        ragged_rows: int = 0,
        unreadable_cells: Optional[Dict[str, int]] = None,
    ) -> None:
        self.subjects = subjects
        self.spec = spec
        self.encoding = encoding
        self.path = path
        self.n_rows = n_rows
        self.group_levels = group_levels
        self.dropped_no_id = dropped_no_id
        self.column_names = list(column_names)
        self.ragged_rows = ragged_rows
        # 열 이름 -> "비어 있지 않은데 숫자로 못 읽은 칸" 수. 유럽식 소수점이
        # 섞인 열이 조용히 반토막 나는 것을 막기 위해 반드시 리포트에 나간다.
        self.unreadable_cells = dict(unreadable_cells or {})

    def __len__(self) -> int:
        return len(self.subjects)


def _apply_timepoint(table: Table, spec: Spec) -> List[int]:
    """`--timepoint 열=값` 으로 행을 거른다. 지정이 없으면 전부."""
    if not spec.timepoint:
        return list(range(len(table.rows)))
    col, wanted = spec.timepoint
    values = table.column(col)
    keep = [i for i, v in enumerate(values) if v.strip() == wanted]
    if not keep:
        seen = sorted({v.strip() for v in values if v.strip()})[:12]
        raise InputError(
            "--timepoint %s=%s 에 해당하는 행이 없습니다. '%s' 열의 값 예시: %s"
            % (col, wanted, col, ", ".join(seen) or "(없음)")
        )
    return keep


def _timepoint_candidates(columns: Sequence[str]) -> List[str]:
    lowered = {c.lower(): c for c in columns}
    found = []
    for hint in _TIMEPOINT_HINTS:
        for low, original in lowered.items():
            if hint in low and original not in found:
                found.append(original)
    return found


def build_dataset(table: Table, spec: Spec) -> Dataset:
    """표 + 명세 → 피험자 목록. 여기서 걸리는 모든 문제는 exit 2 다."""
    if spec.id_col not in table.columns:
        raise InputError(
            "피험자 ID 열 '%s' 이(가) 없습니다. `--id 열이름` 으로 지정해 주세요. "
            "이 파일의 열: %s" % (spec.id_col, ", ".join(table.columns))
        )
    if spec.timepoint and spec.timepoint[0] not in table.columns:
        raise InputError(
            "--timepoint 의 열 '%s' 이(가) 없습니다. 이 파일의 열: %s"
            % (spec.timepoint[0], ", ".join(table.columns))
        )
    needed = spec.numeric_columns + ([spec.group] if spec.group else [])
    for col in needed:
        table.index_of(col)  # 없으면 InputError

    keep = _apply_timepoint(table, spec)
    id_idx = table.index_of(spec.id_col)
    group_idx = table.index_of(spec.group) if spec.group else None
    numeric_idx = {c: table.index_of(c) for c in spec.numeric_columns}

    subjects: List[Subject] = []
    ids: List[str] = []
    unreadable: Dict[str, int] = {}
    dropped_no_id = 0
    for i in keep:
        row = table.rows[i]
        sid = normalise_id(row[id_idx] if id_idx < len(row) else "")
        if not sid:
            dropped_no_id += 1
            continue
        ids.append(sid)
        group = None
        if group_idx is not None:
            group = (row[group_idx] if group_idx < len(row) else "").strip()
        fields = {}
        for c, idx in numeric_idx.items():
            raw = row[idx] if idx < len(row) else ""
            value = parse_number(raw)
            if value is None and raw.strip() and raw.strip().lower() not in _BLANKS:
                unreadable[c] = unreadable.get(c, 0) + 1
            fields[c] = value
        subjects.append(Subject(sid, group, fields, i + 2))

    duplicates = find_duplicate_ids(ids)
    if duplicates:
        shown = ", ".join("%s(%d행)" % (k, v)
                          for k, v in sorted(duplicates.items())[:6])
        extra = "" if len(duplicates) <= 6 else " 외 %d명" % (len(duplicates) - 6)
        hint = _timepoint_candidates(table.columns)
        hint_text = ""
        if hint:
            sample = sorted({v.strip() for v in table.column(hint[0]) if v.strip()})[:5]
            hint_text = (
                "\n  이 파일에는 시점처럼 보이는 열이 있습니다: %s"
                "\n  한 시점만 쓰려면 `--timepoint %s=%s` 처럼 지정하세요 (값 예시: %s)."
                % (", ".join(hint), hint[0], sample[0] if sample else "값",
                   ", ".join(sample) or "(없음)")
            )
        raise InputError(
            "피험자 ID 가 중복입니다: %s%s.\n"
            "  robustcheck 는 **1행 = 1피험자(와이드)** 만 받습니다. "
            "시점별 long 포맷에서 첫 행을 몰래 고르지 않습니다.%s"
            % (shown, extra, hint_text)
        )

    # 열 전체가 비어 있으면, 아래 군 개수 검사가 "군이 0개"라는 엉뚱한 말을
    # 하기 전에 진짜 원인을 먼저 말한다.
    for col in spec.numeric_columns:
        if subjects and all(s.get(col) is None for s in subjects):
            raise InputError(
                "'%s' 열에 숫자 값이 하나도 없습니다(전부 결측이거나 숫자가 "
                "아님). 열 이름과 값 형식을 확인해 주세요." % col)

    group_levels: Tuple[str, ...] = ()
    if spec.design == "two-group":
        levels = sorted({s.group for s in subjects
                         if s.group and s.get(spec.value) is not None})
        if len(levels) != 2:
            raise InputError(
                "two-group 설계는 군이 정확히 2개여야 합니다. '%s' 열에서 %d개를 "
                "찾았습니다: %s. 3군 이상은 이 툴의 범위가 아닙니다(statwise)."
                % (spec.group, len(levels), ", ".join(levels) or "(없음)")
            )
        group_levels = (levels[0], levels[1])

    return Dataset(
        subjects=subjects,
        spec=spec,
        encoding=table.encoding,
        path=table.path,
        n_rows=len(keep),
        group_levels=group_levels,
        dropped_no_id=dropped_no_id,
        column_names=table.columns,
        ragged_rows=table.ragged,
        unreadable_cells=unreadable,
    )
