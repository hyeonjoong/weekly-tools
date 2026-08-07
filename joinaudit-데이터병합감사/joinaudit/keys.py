"""피험자 ID 정규화 — **결정론적 규칙만**. 편집거리 추측은 하지 않는다.

임상 데이터에서 ID를 편집거리로 붙이는 것은 조용히 틀리는 가장 나쁜 방법이다
(`S01`과 `S02`는 한 글자 차이지만 다른 사람이다). 그래서 이 모듈이 하는 일은
전부 "설명할 수 있는 치환"뿐이고, 규칙으로 못 붙이는 것은 사람이 `--alias`
표에 적어야 한다.

적용 순서(각 단계는 리포트에 건수와 함께 남는다)

1. **NFKC 정규화** — 전각 `Ｓ０１` → `S01`.
2. **공백 제거** — 앞뒤 공백, 내부 공백, NBSP. 엑셀에서 붙여넣기하면 흔하다.
3. **별칭표** — 사람이 명시한 대응(`--alias`). 규칙보다 우선한다.
4. **접두어 제거** — `--spec` 의 `id_prefixes`, 그리고 한 파일의 모든 ID가
   공유하면서 구분자로 끝나는 접두어(`BELL-001-`)의 자동 제거.
5. **대문자화**.
6. **제로패딩 정규화** — 끝자리 숫자열의 선행 0 제거(`S01`→`S1`, `S001`→`S1`).
   `S01`과 `S02`는 서로 다른 키로 남는다 — 이 성질을 테스트가 지킨다.

병합 키는 6단계 결과(정규 키)이지만, 사람이 보는 산출물에는 **표시 ID**가
나간다. 표시 ID는 그 키로 모인 원본 표기 중 자릿수가 가장 많은 것을 결정론적
으로 고른 값이라, `S1`과 `S01`이 섞인 자료에서는 `S01`이 나온다.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .dataio import LoadError, is_missing, load_table

__all__ = ["KeyNormalizer", "AliasTable", "load_alias_table", "canonical_key",
           "common_head", "strip_common_head"]

_WS_RE = re.compile(r"\s+", re.UNICODE)
_TRAILING_NUM_RE = re.compile(r"^(?P<head>.*?)(?P<num>\d+)$", re.DOTALL)
_SEPARATORS = "-_.:/ "


def _strip_ws(text: str) -> str:
    """모든 공백류(NBSP 포함)를 제거한다."""
    return _WS_RE.sub("", text.replace(" ", " ").replace("​", ""))


def canonical_key(raw: str, prefixes: Sequence[str] = (),
                  zero_pad: bool = True) -> str:
    """원본 ID -> 정규 키. 빈 문자열이면 키 없음."""
    value = unicodedata.normalize("NFKC", raw or "")
    value = _strip_ws(value)
    if not value:
        return ""
    upper = value.upper()
    for prefix in prefixes:
        p = _strip_ws(unicodedata.normalize("NFKC", prefix)).upper()
        if p and upper.startswith(p) and len(upper) > len(p):
            upper = upper[len(p):].lstrip(_SEPARATORS)
            break
    if not upper:
        return ""
    if zero_pad:
        m = _TRAILING_NUM_RE.match(upper)
        if m:
            num = m.group("num")
            # 선행 0만 걷어낸다. 전부 0이면 '0' 하나를 남긴다.
            upper = m.group("head") + (num.lstrip("0") or "0")
    return upper


def common_head(keys: Sequence[str]) -> Optional[str]:
    """한 파일의 모든 키가 `<같은 머리말><숫자>` 꼴이면 그 머리말을 돌려준다.

    파일마다 같은 사람을 `S07` / `BELL-001-07` / `07` 로 적는 일은 흔한데,
    이 셋을 잇는 결정론적 규칙은 "머리말이 **파일 안에서 상수**면 그 머리말은
    사람을 구분하는 정보가 아니다" 하나뿐이다. 그래서 상수일 때만 인정한다.

    * `S01..S16` -> `'S'`
    * `01..16`   -> `''`  (이미 숫자만 남은 파일)
    * `C01,P01`  -> `None` (머리말이 둘 — 사람을 구분하는 정보일 수 있다)
    * `S01,S1A`  -> `None` (숫자로 끝나지 않는 키가 있다)

    이 함수만으로는 아무것도 바뀌지 않는다. 적용 여부는 `--unify-id-heads` 로
    사람이 정한다 — `S01..S16` 과 `C01..C16` 이 서로 다른 코호트일 수도 있고,
    그 판단은 툴이 할 수 없다.
    """
    heads = set()
    for key in keys:
        if not key:
            continue
        m = _TRAILING_NUM_RE.match(key)
        if not m:
            return None
        heads.add(m.group("head"))
        if len(heads) > 1:
            return None
    if len(heads) != 1:
        return None
    return heads.pop()


def strip_common_head(keys: Sequence[str]) -> Sequence[str]:
    """`common_head` 가 있으면 떼어 낸 키 목록, 없으면 원본 그대로."""
    head = common_head(keys)
    if not head:
        return list(keys)
    return [k[len(head):] if k else k for k in keys]


# --------------------------------------------------------------------------
# 별칭표
# --------------------------------------------------------------------------

@dataclass
class AliasTable:
    """(파일, 원본ID) -> 표준ID. 파일이 비었거나 '*' 이면 모든 파일에 적용."""

    entries: Dict[Tuple[str, str], str] = field(default_factory=dict)
    path: Optional[str] = None

    def lookup(self, file_label: str, raw: str) -> Optional[str]:
        probe = _strip_ws(unicodedata.normalize("NFKC", raw)).upper()
        if not probe:
            return None
        for scope in (file_label.upper(), os.path.basename(file_label).upper(), "*"):
            hit = self.entries.get((scope, probe))
            if hit is not None:
                return hit
        return None

    def __len__(self) -> int:
        return len(self.entries)


_ALIAS_FILE_COLS = ("파일", "file", "filename", "source")
_ALIAS_RAW_COLS = ("원본id", "원본", "raw_id", "raw", "original", "from")
_ALIAS_STD_COLS = ("표준id", "표준", "standard_id", "standard", "canonical", "to")


def _pick(header: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    lowered = {h.strip().lower().replace(" ", ""): h for h in header}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    return None


def load_alias_table(path: str) -> AliasTable:
    """별칭 CSV를 읽는다. 열: `파일,원본ID,표준ID` (영문 이름도 허용)."""
    frame = load_table(path, label=os.path.basename(path))
    raw_col = _pick(frame.header, _ALIAS_RAW_COLS)
    std_col = _pick(frame.header, _ALIAS_STD_COLS)
    file_col = _pick(frame.header, _ALIAS_FILE_COLS)
    if raw_col is None or std_col is None:
        raise LoadError(
            f"별칭표 '{path}' 에 필요한 열이 없습니다. "
            f"`파일,원본ID,표준ID` 형식이어야 합니다(현재 열: {', '.join(frame.header)}).")

    entries: Dict[Tuple[str, str], str] = {}
    for row in frame.rows:
        raw = frame.cell(row, raw_col).strip()
        std = frame.cell(row, std_col).strip()
        if not raw or not std:
            continue
        scope = (frame.cell(row, file_col).strip() if file_col else "") or "*"
        probe = _strip_ws(unicodedata.normalize("NFKC", raw)).upper()
        entries[(scope.upper(), probe)] = std
    return AliasTable(entries=entries, path=os.path.abspath(path))


# --------------------------------------------------------------------------
# 정규화기
# --------------------------------------------------------------------------

@dataclass
class NormalizationStats:
    """한 파일에 대해 실제로 무엇이 일어났는지."""

    whitespace: int = 0
    fullwidth: int = 0
    case: int = 0
    zero_pad: int = 0
    alias: int = 0
    prefix: int = 0
    prefix_value: str = ""
    head: int = 0
    head_value: str = ""
    missing: int = 0
    # 같은 파일 안에서 서로 다른 원본 표기가 한 키로 합쳐진 경우.
    collisions: Dict[str, List[str]] = field(default_factory=dict)


class KeyNormalizer:
    """파일 하나의 ID 열을 정규 키로 바꾸고, 무슨 일이 있었는지 기록한다."""

    def __init__(self, prefixes: Sequence[str] = (),
                 alias: Optional[AliasTable] = None,
                 zero_pad: bool = True,
                 auto_prefix: bool = True,
                 unify_heads: bool = False) -> None:
        self.prefixes = [p for p in prefixes if p]
        self.alias = alias or AliasTable()
        self.zero_pad = zero_pad
        self.auto_prefix = auto_prefix
        self.unify_heads = unify_heads
        # 정규 키 -> 원본 표기 집합(표시 ID 선정에 쓴다).
        self.display_candidates: Dict[str, set] = {}

    # -- 자동 접두어 -------------------------------------------------------
    @staticmethod
    def detect_common_prefix(values: Sequence[str]) -> str:
        """한 파일의 모든 ID가 공유하면서 **구분자로 끝나는** 접두어.

        구분자에서 끊는 것이 핵심이다. `S01..S26` 의 공통 접두어 `S` 를 떼면
        숫자만 남아 다른 파일의 `1..26` 과 우연히 붙어 버린다. `BELL-001-`
        처럼 구분자로 끝나는 것만 접두어로 인정한다.
        """
        vals = [v for v in values if v]
        if len(vals) < 2:
            return ""
        lcp = os.path.commonprefix(vals)
        cut = max((lcp.rfind(sep) for sep in "-_"), default=-1)
        if cut < 0:
            return ""
        prefix = lcp[:cut + 1]
        if len(prefix) < 2:
            return ""
        stripped = [v[len(prefix):].lstrip(_SEPARATORS) for v in vals]
        if any(not s for s in stripped):
            return ""
        if len(set(stripped)) != len(set(vals)):
            return ""            # 접두어를 떼면 서로 다른 ID가 겹친다 -> 포기
        return prefix

    def probe_prefix(self, raw_values: Sequence[str]) -> str:
        """실제로 정규화하지 않고, 이 파일에서 떼어질 접두어만 미리 본다.

        접두어 제거는 파일 단위 판단이라, 두 파일에서 **서로 다른** 접두어가
        떨어지면 `BELL-001-01` 과 `BELL-002-01` 이 똑같이 `01` 이 되어 남남이 한
        사람으로 붙는다. 호출부가 그 상황을 미리 감지할 수 있게 열어 둔다.
        """
        if self.prefixes:
            return self.prefixes[0]
        if not self.auto_prefix:
            return ""
        cleaned = [_strip_ws(unicodedata.normalize("NFKC", v))
                   for v in raw_values if v and not is_missing(v)]
        return self.detect_common_prefix([v for v in cleaned if v])

    # -- 본 작업 -----------------------------------------------------------
    def normalize_column(self, file_label: str, raw_values: Sequence[str]
                         ) -> Tuple[List[str], NormalizationStats]:
        """원본 ID 목록 -> 정규 키 목록(빈 문자열 = 키 없음) + 통계.

        단계마다 실제로 값을 바꾼 건수를 센다. 리포트의 "무엇을 했는가" 문장이
        추정이 아니라 계측이어야 하기 때문이다.
        """
        stats = NormalizationStats()

        # 1~2단계: NFKC + 공백 제거
        cleaned: List[str] = []
        for raw in raw_values:
            if is_missing(raw):
                cleaned.append("")
                continue
            nfkc = unicodedata.normalize("NFKC", raw)
            if nfkc != raw:
                stats.fullwidth += 1
            no_ws = _strip_ws(nfkc)
            if no_ws != nfkc:
                stats.whitespace += 1
            cleaned.append(no_ws)

        # 4단계 준비: 접두어 목록 확정. 사람이 지정한 것이 있으면 자동 탐지는
        # 하지 않는다(두 규칙이 겹치면 무엇이 적용됐는지 설명할 수 없다).
        prefixes = list(self.prefixes)
        if self.auto_prefix and not prefixes:
            auto = self.detect_common_prefix([v for v in cleaned if v])
            if auto:
                prefixes = [auto]
                stats.prefix_value = auto

        keys: List[str] = []
        raw_by_key: Dict[str, set] = {}
        for raw, value in zip(raw_values, cleaned):
            if not value:
                keys.append("")
                stats.missing += 1
                continue

            # 3단계: 별칭표(규칙보다 우선)
            aliased = self.alias.lookup(file_label, value)
            if aliased is not None:
                stats.alias += 1
                value = _strip_ws(unicodedata.normalize("NFKC", aliased))

            # 4단계: 접두어
            upper = value.upper()
            for prefix in prefixes:
                p = _strip_ws(unicodedata.normalize("NFKC", prefix)).upper()
                if p and upper.startswith(p) and len(upper) > len(p):
                    upper = upper[len(p):].lstrip(_SEPARATORS)
                    stats.prefix += 1
                    break

            # 5단계: 대문자화
            if value != value.upper():
                stats.case += 1

            # 6단계: 제로패딩
            if self.zero_pad:
                m = _TRAILING_NUM_RE.match(upper)
                if m:
                    num = m.group("num")
                    trimmed = num.lstrip("0") or "0"
                    if trimmed != num:
                        stats.zero_pad += 1
                    upper = m.group("head") + trimmed

            if not upper:
                keys.append("")
                stats.missing += 1
                continue

            keys.append(upper)

        # 7단계(선택): 파일 안에서 상수인 머리말 제거. 파일마다 `S07` /
        # `BELL-001-07` / `07` 로 적힌 같은 사람을 잇는 유일한 결정론적 규칙이다.
        if self.unify_heads:
            head = common_head([k for k in keys if k])
            if head:
                stats.head_value = head
                keys = [k[len(head):] if k else k for k in keys]
                stats.head = sum(1 for k in keys if k)

        for raw, key in zip(raw_values, keys):
            if not key:
                continue
            raw_by_key.setdefault(key, set()).add(raw.strip())
            self.display_candidates.setdefault(key, set()).add(raw.strip())

        for key, raws in raw_by_key.items():
            if len(raws) > 1:
                stats.collisions[key] = sorted(raws)
        return keys, stats

    # -- 표시 ID -----------------------------------------------------------
    def display_id(self, key: str) -> str:
        """정규 키로 모인 원본 표기 중 하나를 결정론적으로 고른다.

        끝자리 숫자 자릿수가 가장 많은 표기(= 제로패딩이 가장 온전한 표기)를
        우선하고, 동률이면 **짧은 쪽**, 그다음 사전순. `S1`/`S01` 이 섞여 있으면
        `S01` 이, `S01`/`BELL-001-01` 이 섞여 있으면 `S01` 이 나온다.
        """
        raws = self.display_candidates.get(key)
        if not raws:
            return key

        def rank(value: str) -> Tuple[int, int, str]:
            m = _TRAILING_NUM_RE.match(value)
            digits = len(m.group("num")) if m else 0
            return (-digits, len(value), value)

        return sorted(raws, key=rank)[0]
