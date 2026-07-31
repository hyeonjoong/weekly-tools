"""설문 분석 설정(config) 로드 및 검증.

config(JSON)는 하위척도 구성·역문항·척도 범위·결측 처리 기준을 담는다.
config가 없으면 데이터의 숫자형 컬럼 전체를 하나의 척도로 보는 기본 설정을 만든다.

예시 config:
{
  "scale_min": 0,
  "scale_max": 4,
  "subscales": {
    "불면증상": ["ISI1", "ISI2", "ISI3"],
    "주간기능": ["DAY1", "DAY2"]
  },
  "reverse_items": ["DAY2"],
  "min_valid_ratio": 0.5,
  "score_method": "sum",
  "severity_bands": {
    "불면증상": [[0, 7, "없음"], [8, 14, "역치하"], [15, 21, "중등도"], [22, 28, "중증"]]
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


class ConfigError(ValueError):
    """config 내용이 잘못되었을 때 발생."""


SCORE_METHODS = ("mean", "sum")

# config에서 인식하는 최상위 키. 여기 없는 키는 오타로 보고 오류를 낸다.
# (예: "reverse_item" 오타 → 역문항이 조용히 적용되지 않아 α가 틀리게 나온다.)
# '_' 로 시작하는 키는 주석용으로 허용한다(예: "_메모": "2026-07 ISI 설정").
KNOWN_KEYS = frozenset(
    {"subscales", "reverse_items", "scale_min", "scale_max",
     "min_valid_ratio", "score_method", "severity_bands"}
)


@dataclass
class SurveyConfig:
    subscales: Dict[str, List[str]]
    reverse_items: List[str] = field(default_factory=list)
    # 하위척도별 임상 심각도 구간: {하위척도명: [(하한, 상한, 라벨), ...]} (양끝 포함).
    # 예: ISI 총점 0-7 없음 / 8-14 역치하 / 15-21 중등도 / 22-28 중증.
    severity_bands: Dict[str, List[Tuple[float, float, str]]] = field(default_factory=dict)
    scale_min: Optional[float] = None
    scale_max: Optional[float] = None
    # 응답자별 하위척도 점수를 계산할 때, 최소 이 비율 이상 응답해야 점수를 부여.
    min_valid_ratio: float = 0.5
    # 하위척도 점수 산출 방식: "mean"(가용문항 평균) 또는 "sum"(비례배분 합).
    # ISI·PHQ-9·GAD-7 등 임상 척도는 보통 총합(sum)으로 보고한다.
    score_method: str = "mean"

    def all_items(self) -> List[str]:
        """모든 하위척도에 속한 문항 이름(중복 제거, 등장 순서 유지)."""
        seen: List[str] = []
        for items in self.subscales.values():
            for it in items:
                if it not in seen:
                    seen.append(it)
        return seen

    def reverse_set(self) -> set:
        return set(self.reverse_items)


def load_config(path: str) -> SurveyConfig:
    """JSON 파일에서 config를 읽어 검증한다."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return _from_dict(raw)


def _from_dict(raw: dict) -> SurveyConfig:
    if not isinstance(raw, dict):
        raise ConfigError("config 최상위는 객체(JSON object)여야 합니다.")

    # 오타 키를 조용히 무시하지 않는다 — 'reverse_item' 같은 오타는 역문항 재코딩을
    # 통째로 건너뛰게 만들어 α·점수를 틀리게 하고, 사용자는 알아챌 방법이 없다.
    unknown_keys = [
        k for k in raw
        if not (isinstance(k, str) and (k in KNOWN_KEYS or k.startswith("_")))
    ]
    if unknown_keys:
        hints = []
        for k in sorted(map(str, unknown_keys)):
            near = [kk for kk in KNOWN_KEYS if kk.startswith(str(k)[:4])]
            hints.append(f"{k}" + (f" (혹시 '{near[0]}'?)" if near else ""))
        raise ConfigError(
            "config에 알 수 없는 키가 있습니다: " + ", ".join(hints)
            + ". 사용 가능한 키: " + ", ".join(sorted(KNOWN_KEYS))
            + " (메모는 '_' 로 시작하는 키를 쓰세요)"
        )

    subscales = raw.get("subscales")
    if not isinstance(subscales, dict) or not subscales:
        raise ConfigError("'subscales'는 비어있지 않은 객체여야 합니다.")
    parsed: Dict[str, List[str]] = {}
    for name, items in subscales.items():
        if not isinstance(items, list) or not items:
            raise ConfigError(f"하위척도 '{name}'의 문항 목록이 비어있거나 리스트가 아닙니다.")
        if not all(isinstance(i, str) for i in items):
            raise ConfigError(f"하위척도 '{name}'의 문항 이름은 모두 문자열이어야 합니다.")
        # 같은 하위척도 안의 문항 중복은 손으로 JSON을 쓸 때 흔한 복붙 실수인데,
        # 그대로 두면 k와 문항간 상관이 부풀려져 α가 실제보다 높게 나온다.
        # (서로 다른 하위척도가 같은 문항을 공유하는 것은 정상이므로 허용한다.)
        dupes = sorted({i for i in items if items.count(i) > 1})
        if dupes:
            raise ConfigError(
                f"하위척도 '{name}'에 같은 문항이 중복되어 있습니다: "
                + ", ".join(dupes)
                + " (중복은 α를 부풀립니다 — 한 번만 적으세요)"
            )
        parsed[str(name)] = list(items)

    reverse_items = raw.get("reverse_items", [])
    if not isinstance(reverse_items, list) or not all(isinstance(i, str) for i in reverse_items):
        raise ConfigError("'reverse_items'는 문자열 리스트여야 합니다.")

    scale_min = raw.get("scale_min")
    scale_max = raw.get("scale_max")
    for nm, val in (("scale_min", scale_min), ("scale_max", scale_max)):
        if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):
            raise ConfigError(f"'{nm}'는 숫자여야 합니다.")
    if reverse_items and (scale_min is None or scale_max is None):
        raise ConfigError("역문항(reverse_items)이 있으면 scale_min과 scale_max를 모두 지정해야 합니다.")
    if scale_min is not None and scale_max is not None and scale_min >= scale_max:
        raise ConfigError("scale_min은 scale_max보다 작아야 합니다.")

    min_valid_ratio = raw.get("min_valid_ratio", 0.5)
    if isinstance(min_valid_ratio, bool) or not isinstance(min_valid_ratio, (int, float)) \
            or not (0 <= min_valid_ratio <= 1):
        raise ConfigError("'min_valid_ratio'는 0과 1 사이의 숫자여야 합니다.")

    score_method = raw.get("score_method", "mean")
    if score_method not in SCORE_METHODS:
        raise ConfigError(
            "'score_method'는 'mean' 또는 'sum' 이어야 합니다: " + repr(score_method)
        )

    # 역문항이 어떤 하위척도에도 없으면 사용자의 오타일 가능성이 높다.
    known = set()
    for items in parsed.values():
        known.update(items)
    unknown_rev = [r for r in reverse_items if r not in known]
    if unknown_rev:
        raise ConfigError(
            "reverse_items에 어떤 하위척도에도 없는 문항이 있습니다: " + ", ".join(unknown_rev)
        )

    bands = _parse_bands(raw.get("severity_bands"), parsed)

    return SurveyConfig(
        subscales=parsed,
        reverse_items=list(reverse_items),
        severity_bands=bands,
        scale_min=float(scale_min) if scale_min is not None else None,
        scale_max=float(scale_max) if scale_max is not None else None,
        min_valid_ratio=float(min_valid_ratio),
        score_method=score_method,
    )


def _parse_bands(
    raw_bands: object, subscales: Dict[str, List[str]]
) -> Dict[str, List[Tuple[float, float, str]]]:
    """severity_bands 파싱·검증.

    형식: {"하위척도명": [[하한, 상한, "라벨"], ...]} — 하한·상한 모두 **포함**.
    검증 항목(모두 조용한 오분류로 이어지므로 오류로 막는다):
      - 하위척도 이름이 subscales 에 실제로 있어야 한다(오타 → 구간이 통째로 무시됨)
      - 각 구간은 [숫자, 숫자, 문자열] 3원소이고 하한 ≤ 상한
      - 구간끼리 겹치면 안 된다(겹치면 같은 점수가 두 심각도로 분류됨)
    """
    if raw_bands is None:
        return {}
    if not isinstance(raw_bands, dict) or not raw_bands:
        raise ConfigError("'severity_bands'는 비어있지 않은 객체여야 합니다.")
    out: Dict[str, List[Tuple[float, float, str]]] = {}
    for name, spec in raw_bands.items():
        if name not in subscales:
            raise ConfigError(
                f"severity_bands 의 '{name}' 은 subscales 에 없는 하위척도입니다"
                f" (사용 가능: {', '.join(subscales)})"
            )
        if not isinstance(spec, list) or not spec:
            raise ConfigError(f"severity_bands['{name}'] 는 비어있지 않은 리스트여야 합니다.")
        parsed: List[Tuple[float, float, str]] = []
        for band in spec:
            if not isinstance(band, (list, tuple)) or len(band) != 3:
                raise ConfigError(
                    f"severity_bands['{name}'] 의 각 구간은 [하한, 상한, \"라벨\"] "
                    f"3원소여야 합니다: {band!r}"
                )
            lo, hi, label = band
            for v in (lo, hi):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ConfigError(
                        f"severity_bands['{name}'] 의 구간 경계는 숫자여야 합니다: {band!r}"
                    )
            if not isinstance(label, str) or not label.strip():
                raise ConfigError(
                    f"severity_bands['{name}'] 의 라벨은 비어있지 않은 문자열이어야 합니다: {band!r}"
                )
            if float(lo) > float(hi):
                raise ConfigError(
                    f"severity_bands['{name}'] 구간의 하한이 상한보다 큽니다: {band!r}"
                )
            parsed.append((float(lo), float(hi), label.strip()))
        parsed.sort(key=lambda b: (b[0], b[1]))
        for prev, cur in zip(parsed, parsed[1:]):
            # 판정(band_index)이 ±BAND_TOL 을 허용하므로 겹침 검증도 같은 폭으로 본다.
            # (엄격하게만 보면 틈이 2·tol 보다 좁은 구간이 통과해 두 구간에 동시에 걸린다.)
            if cur[0] <= prev[1] + 2 * BAND_TOL:
                raise ConfigError(
                    f"severity_bands['{name}'] 의 구간이 겹칩니다: "
                    f"[{prev[0]:g}, {prev[1]:g}] 와 [{cur[0]:g}, {cur[1]:g}] "
                    "(겹치면 같은 점수가 두 심각도로 분류됩니다)"
                )
        out[str(name)] = parsed
    return out


BAND_TOL = 1e-9  # 경계 판정 허용오차(부동소수 잡음 흡수). 겹침 검증도 같은 값을 쓴다.


def band_index(
    score, bands: Sequence[Tuple[float, float, str]]
):
    """점수(또는 점수 리스트)를 구간 **인덱스** 로. 어디에도 안 들어가면 None.

    라벨이 아니라 인덱스를 돌려주는 이유: 서로 다른 두 구간이 같은 라벨을 쓸 수 있어
    (예: 0~2 '낮음', 6~8 '낮음') 라벨로 집계하면 분포표가 중복 집계된다.
    """
    if isinstance(score, (list, tuple)):
        return [band_index(s, bands) for s in score]
    if score is None:
        return None
    for i, (lo, hi, _label) in enumerate(bands):
        if (lo - BAND_TOL) <= score <= (hi + BAND_TOL):
            return i
    return None


def band_label(
    score: Optional[float], bands: Sequence[Tuple[float, float, str]]
) -> Optional[str]:
    """점수를 심각도 라벨로. 어떤 구간에도 속하지 않으면 None(미분류).

    경계는 양끝 포함이고 구간은 겹치지 않도록 검증되어 있으므로 결과는 유일하다.
    부동소수 오차(예: 평균 7.999999999999999)로 경계가 어긋나는 것을 막기 위해
    아주 작은 허용오차(BAND_TOL)를 두며, 겹침 검증도 같은 허용오차를 쓴다
    (검증은 엄격, 판정은 느슨하면 틈이 2·tol 보다 좁은 구간에서 오분류가 난다).
    """
    if score is None:
        return None
    i = band_index(score, bands)
    return None if i is None else bands[i][2]


def auto_config(numeric_columns: Sequence[str]) -> SurveyConfig:
    """config 없이 실행할 때: 숫자형 컬럼 전체를 '전체'라는 하나의 척도로 본다."""
    if not numeric_columns:
        raise ConfigError("숫자형 컬럼이 없어 자동 설정을 만들 수 없습니다.")
    return SurveyConfig(subscales={"전체": list(numeric_columns)})
