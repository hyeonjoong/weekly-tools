"""조음 자질표(`--features`) — **받을 때만** 묶어 집계한다.

툴이 음운론적 분류를 스스로 주장하면, 근거 없는 해석이 사내 산출물로 굳는다.
그래서 자질은 코드에 없고, 사용자가 준 CSV 에만 있다.

CSV 형식(헤더 필수)::

    자모,조음위치,조음방법
    ㅂ,양순,파열
    ㄷ,치조,파열
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .hangul import normalize
from .safeio import read_text_any

JAMO_ALIASES = ("자모", "음소", "phoneme", "jamo", "symbol")


class FeatureError(ValueError):
    pass


def load(path: str) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """``({자모: {자질이름: 값}}, 자질이름목록)``."""
    text = read_text_any(path)
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(normalize(c) for c in r)]
    if len(rows) < 2:
        raise FeatureError("자질표에 데이터 행이 없습니다: %s" % path)
    header = [normalize(c) for c in rows[0]]
    key_idx = None
    for i, name in enumerate(header):
        if name.lower() in [a.lower() for a in JAMO_ALIASES]:
            key_idx = i
            break
    if key_idx is None:
        raise FeatureError(
            "자질표 첫 행에 '자모'(또는 음소/phoneme) 열이 없습니다. 실제 열: %s"
            % ", ".join(header))
    names = [h for i, h in enumerate(header) if i != key_idx and h]
    table: Dict[str, Dict[str, str]] = {}
    for row in rows[1:]:
        jamo = normalize(row[key_idx]) if key_idx < len(row) else ""
        if not jamo:
            continue
        table[jamo] = {
            header[i]: normalize(row[i])
            for i in range(len(header))
            if i != key_idx and i < len(row) and header[i]
        }
    if not table:
        raise FeatureError("자질표에서 자모를 하나도 읽지 못했습니다: %s" % path)
    return table, names


def group(counter: Counter, table: Dict[str, Dict[str, str]],
          feature: str) -> Tuple[Counter, int]:
    """``(목표,응답)`` 카운트를 자질 값 쌍으로 묶는다. 두 번째 값은 미분류 건수."""
    out: Counter = Counter()
    unclassified = 0
    for (target, got), n in counter.items():
        a = table.get(target, {}).get(feature)
        b = table.get(got, {}).get(feature)
        if not a or not b:
            unclassified += n
            continue
        out[(a, b)] += n
    return out, unclassified
