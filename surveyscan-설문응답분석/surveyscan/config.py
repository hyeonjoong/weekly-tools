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
  "min_valid_ratio": 0.5
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


class ConfigError(ValueError):
    """config 내용이 잘못되었을 때 발생."""


@dataclass
class SurveyConfig:
    subscales: Dict[str, List[str]]
    reverse_items: List[str] = field(default_factory=list)
    scale_min: Optional[float] = None
    scale_max: Optional[float] = None
    # 응답자별 하위척도 점수를 계산할 때, 최소 이 비율 이상 응답해야 점수를 부여.
    min_valid_ratio: float = 0.5

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
    subscales = raw.get("subscales")
    if not isinstance(subscales, dict) or not subscales:
        raise ConfigError("'subscales'는 비어있지 않은 객체여야 합니다.")
    parsed: Dict[str, List[str]] = {}
    for name, items in subscales.items():
        if not isinstance(items, list) or not items:
            raise ConfigError(f"하위척도 '{name}'의 문항 목록이 비어있거나 리스트가 아닙니다.")
        if not all(isinstance(i, str) for i in items):
            raise ConfigError(f"하위척도 '{name}'의 문항 이름은 모두 문자열이어야 합니다.")
        parsed[str(name)] = list(items)

    reverse_items = raw.get("reverse_items", [])
    if not isinstance(reverse_items, list) or not all(isinstance(i, str) for i in reverse_items):
        raise ConfigError("'reverse_items'는 문자열 리스트여야 합니다.")

    scale_min = raw.get("scale_min")
    scale_max = raw.get("scale_max")
    if reverse_items and (scale_min is None or scale_max is None):
        raise ConfigError("역문항(reverse_items)이 있으면 scale_min과 scale_max를 모두 지정해야 합니다.")
    if scale_min is not None and scale_max is not None and scale_min >= scale_max:
        raise ConfigError("scale_min은 scale_max보다 작아야 합니다.")

    min_valid_ratio = raw.get("min_valid_ratio", 0.5)
    if not isinstance(min_valid_ratio, (int, float)) or not (0 <= min_valid_ratio <= 1):
        raise ConfigError("'min_valid_ratio'는 0과 1 사이의 숫자여야 합니다.")

    # 역문항이 어떤 하위척도에도 없으면 사용자의 오타일 가능성이 높다.
    known = set()
    for items in parsed.values():
        known.update(items)
    unknown_rev = [r for r in reverse_items if r not in known]
    if unknown_rev:
        raise ConfigError(
            "reverse_items에 어떤 하위척도에도 없는 문항이 있습니다: " + ", ".join(unknown_rev)
        )

    return SurveyConfig(
        subscales=parsed,
        reverse_items=list(reverse_items),
        scale_min=scale_min,
        scale_max=scale_max,
        min_valid_ratio=float(min_valid_ratio),
    )


def auto_config(numeric_columns: Sequence[str]) -> SurveyConfig:
    """config 없이 실행할 때: 숫자형 컬럼 전체를 '전체'라는 하나의 척도로 본다."""
    if not numeric_columns:
        raise ConfigError("숫자형 컬럼이 없어 자동 설정을 만들 수 없습니다.")
    return SurveyConfig(subscales={"전체": list(numeric_columns)})
