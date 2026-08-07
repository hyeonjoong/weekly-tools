"""`--spec spec.json` — 연구마다 다른 규칙을 **사람이 명시**하는 자리.

이 툴은 규칙을 넓혀 추측하지 않는다. 그러니 연구별로만 참인 것들
(우리 연구의 ID 접두어, 우리 프로토콜의 방문 라벨, 우리 변수의 정상 범위)은
코드가 아니라 이 파일에 들어간다.

```json
{
  "id_prefixes": ["BELL-001-"],
  "visit_aliases": {"baseline": ["BL", "기저", "V1"], "week4": ["W4", "4주"]},
  "ranges": {"isi_total": [0, 28], "tst_min": [120, 720]}
}
```

세 키 모두 선택이다. 없으면 그 검사는 **아예 돌지 않는다** — 임의의 정상범위를
지어내는 것보다 검사하지 않는 편이 낫다. 모르는 키가 있으면 조용히 무시하지 않고
경고한다(오타 난 설정이 조용히 무시되는 것은 설정이 없는 것보다 나쁘다).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

__all__ = ["Spec", "SpecError", "load_spec"]

_KNOWN_KEYS = ("id_prefixes", "visit_aliases", "ranges")
_MAX_SPEC_BYTES = 4 * 1024 * 1024


class SpecError(ValueError):
    """스펙 파일을 쓸 수 없을 때 — 메시지는 사람이 고칠 수 있는 한국어."""


@dataclass
class Spec:
    """검증을 마친 스펙. 비어 있는 것이 기본값이며 그때는 아무 검사도 늘지 않는다."""

    id_prefixes: List[str] = field(default_factory=list)
    visit_aliases: Dict[str, List[str]] = field(default_factory=dict)
    ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    path: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.id_prefixes or self.visit_aliases or self.ranges)

    def describe(self) -> List[str]:
        """리포트에 그대로 나갈 한 줄짜리 설명들."""
        out: List[str] = []
        if self.id_prefixes:
            out.append("ID 접두어: " + ", ".join(repr(p) for p in self.id_prefixes))
        if self.visit_aliases:
            pairs = ", ".join(
                f"{canon}({'/'.join(labels)})"
                for canon, labels in sorted(self.visit_aliases.items()))
            out.append("방문 라벨 별칭: " + pairs)
        if self.ranges:
            pairs = ", ".join(f"{name} [{lo:g}, {hi:g}]"
                              for name, (lo, hi) in sorted(self.ranges.items()))
            out.append("허용 범위: " + pairs)
        return out


def _as_str_list(value: object, where: str) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if not isinstance(item, str):
                raise SpecError(f"{where} 의 항목은 문자열이어야 합니다: {item!r}")
            if item.strip():
                out.append(item.strip())
        return out
    raise SpecError(f"{where} 는 문자열 또는 문자열 목록이어야 합니다.")


def load_spec(path: str) -> Spec:
    """스펙 JSON을 읽어 검증한다. 형식 오류는 `SpecError`."""
    if not os.path.exists(path):
        raise SpecError(f"스펙 파일을 찾을 수 없습니다: {path}")
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise SpecError(f"스펙 파일을 열 수 없습니다: {exc}")
    if size > _MAX_SPEC_BYTES:
        raise SpecError(f"스펙 파일이 너무 큽니다(> {_MAX_SPEC_BYTES // 1024}KB).")
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except UnicodeDecodeError:
        raise SpecError(f"스펙 파일 '{path}' 을 UTF-8로 읽을 수 없습니다.")
    except json.JSONDecodeError as exc:
        raise SpecError(
            f"스펙 파일 '{path}' 이 올바른 JSON이 아닙니다 "
            f"({exc.lineno}행 {exc.colno}열: {exc.msg}).")
    except OSError as exc:
        raise SpecError(f"스펙 파일을 열 수 없습니다: {exc}")

    if not isinstance(data, dict):
        raise SpecError("스펙 파일의 최상위는 객체(`{ ... }`)여야 합니다.")

    spec = Spec(path=os.path.abspath(path))
    for key in data:
        if key not in _KNOWN_KEYS:
            spec.warnings.append(
                f"스펙에 모르는 항목 '{key}' 이 있어 무시했습니다"
                f"(쓸 수 있는 항목: {', '.join(_KNOWN_KEYS)}).")

    if "id_prefixes" in data:
        spec.id_prefixes = _as_str_list(data["id_prefixes"], "id_prefixes")

    if "visit_aliases" in data:
        raw = data["visit_aliases"]
        if not isinstance(raw, dict):
            raise SpecError("visit_aliases 는 `{정규라벨: [별칭...]}` 객체여야 합니다.")
        for canon, labels in raw.items():
            if not str(canon).strip():
                raise SpecError("visit_aliases 의 정규 라벨이 비어 있습니다.")
            spec.visit_aliases[str(canon).strip()] = _as_str_list(
                labels, f"visit_aliases['{canon}']")

    if "ranges" in data:
        raw = data["ranges"]
        if not isinstance(raw, dict):
            raise SpecError("ranges 는 `{변수이름: [최소, 최대]}` 객체여야 합니다.")
        for name, bounds in raw.items():
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise SpecError(
                    f"ranges['{name}'] 는 [최소, 최대] 두 개의 숫자여야 합니다.")
            try:
                low, high = float(bounds[0]), float(bounds[1])
            except (TypeError, ValueError):
                raise SpecError(f"ranges['{name}'] 의 값이 숫자가 아닙니다: {bounds!r}")
            if not (math.isfinite(low) and math.isfinite(high)):
                raise SpecError(
                    f"ranges['{name}'] 에 무한대/NaN 이 있습니다: {bounds!r}. "
                    "그런 범위는 아무것도 걸러 내지 못합니다.")
            if not (low <= high):
                raise SpecError(
                    f"ranges['{name}'] 의 최소({low:g})가 최대({high:g})보다 큽니다.")
            spec.ranges[str(name).strip()] = (low, high)

    return spec


def merge_visit_aliases(spec: Spec) -> Dict[str, Sequence[str]]:
    """`VisitNormalizer` 에 넘길 형태로."""
    return {canon: labels for canon, labels in spec.visit_aliases.items()}
