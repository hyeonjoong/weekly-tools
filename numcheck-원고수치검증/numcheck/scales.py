"""GRIM 을 돌릴 수 있는 "정수 척도" 사전.

GRIM(Granularity-Related Inconsistency of Means)은 **개인 점수가 이산적일 때만**
성립한다. ISI 는 7문항 0–4점 정수 합(0–28)이므로 N = 23 명의 평균은 반드시
정수/23 꼴이어야 한다. 나이·BMI·수면시간에 같은 규칙을 적용하면 헛소리가 된다.

그래서 numcheck 는 **척도를 추측하지 않는다.** 이름이 명시적으로 매칭될 때만
GRIM 을 켜고, 모르면 건너뛰고 "건너뛰었다"고 적는다. 사용자는
``--scale ISI=0:28:7`` 또는 ``--scale-config scales.json`` 으로 언제든 추가할 수 있다.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = [
    "Scale",
    "ScaleRegistry",
    "BUILTIN_SCALES",
    "NEEDS_CONFIG",
    "parse_scale_arg",
    "load_scale_config",
]


class ScaleError(ValueError):
    """척도 지정이 잘못되었을 때."""


@dataclass(frozen=True)
class Scale:
    """한 척도의 산술 구조.

    ``unit``  한 사람의 점수가 가질 수 있는 최소 증분.
              정수 합 척도면 1, "고정 목록 대비 정답률(%)" 이면 100/items.
    ``integer_sum``  개인 점수가 정수인가 (GRIMMER 는 이때만 적용).
    """

    name: str
    lo: float
    hi: float
    items: int
    unit: float = 1.0
    integer_sum: bool = True
    aliases: Tuple[str, ...] = ()
    note: str = ""

    def achievable(self, mean: float, n: int) -> bool:
        """N 명의 평균이 이 값일 수 있는가 (범위만; 정밀 판정은 grim.py)."""
        return self.lo - 1e-9 <= mean <= self.hi + 1e-9


# ── 내장 척도 ────────────────────────────────────────────────────────────────
# 원칙: **총점 정의가 모호하지 않은 것만** 넣는다. 모호하면 사용자가 지정한다.
BUILTIN_SCALES: Tuple[Scale, ...] = (
    Scale("ISI", 0, 28, 7, aliases=("insomnia severity index", "불면증 심각도 지수",
                                    "불면증심각도지수", "불면증 심각도 척도")),
    Scale("PSQI", 0, 21, 7, aliases=("pittsburgh sleep quality index", "피츠버그 수면의 질 지수",
                                     "피츠버그수면질지수")),
    Scale("ESS", 0, 24, 8, aliases=("epworth sleepiness scale", "엡워스 주간졸림증 척도",
                                    "엡워스졸음척도")),
    Scale("PHQ-9", 0, 27, 9, aliases=("phq9", "patient health questionnaire-9",
                                      "patient health questionnaire 9")),
    Scale("GAD-7", 0, 21, 7, aliases=("gad7", "generalized anxiety disorder-7",
                                      "generalized anxiety disorder 7")),
    Scale("HADS-A", 0, 21, 7, aliases=("hads anxiety", "hads-anxiety", "hads 불안")),
    Scale("HADS-D", 0, 21, 7, aliases=("hads depression", "hads-depression", "hads 우울")),
    Scale("HADS", 0, 42, 14, aliases=("hospital anxiety and depression scale",),
          note="HADS 총점(불안+우울)으로 봅니다. 하위척도면 HADS-A/HADS-D 로 적으세요."),
    Scale("PSS", 0, 40, 10, aliases=("perceived stress scale", "지각된 스트레스 척도")),
    Scale("BDI-II", 0, 63, 21, aliases=("bdi2", "beck depression inventory-ii",
                                        "beck depression inventory ii")),
    Scale("FSS", 9, 63, 9, aliases=("fatigue severity scale", "피로 중증도 척도")),
    Scale("MMSE", 0, 30, 30, aliases=("mini-mental state examination", "간이정신상태검사")),
    Scale("MoCA", 0, 30, 30, aliases=("montreal cognitive assessment", "몬트리올 인지평가")),
)

# 이름은 알지만 **구조를 알 수 없어** 자동으로 켤 수 없는 것들.
# 발견하면 "지정하면 검사할 수 있습니다" 라고 정보 등급으로 안내한다.
NEEDS_CONFIG: Dict[str, str] = {
    "단어인지도": "목록 길이(문항 수)를 알아야 합니다. 예: --scale 단어인지도=0:100:50 --percent-of-count 단어인지도",
    "word recognition score": "list length required",
    "wrs": "list length required",
    "문장인지도": "목록 길이(문항 수)가 필요합니다.",
    "수면효율": "연속형 비율입니다. 정수 척도가 아니면 GRIM 이 성립하지 않습니다.",
    "sleep efficiency": "continuous ratio — GRIM does not apply unless it is a discrete count.",
    "정답률": "문항 수를 알아야 합니다.",
    "vas": "0–100 정수로 기록했다면 --scale VAS=0:100:1 로 지정하세요.",
}


def _alias_pattern(alias: str) -> re.Pattern:
    """별칭 하나를 '단어 경계가 지켜지는' 정규식으로.

    ASCII 이름은 앞뒤에 영숫자가 오면 안 된다(`ISI` 가 `PISI` 에 걸리면 안 됨).
    한글 이름은 앞뒤에 한글이 오면 안 된다.
    """
    # 공백/하이픈/밑줄이 **연속**되면 `[\s\-_]*` 가 그만큼 나란히 붙어 지수적
    # 백트래킹이 된다(공백 12개짜리 별칭에 36초). 먼저 하나로 접는다.
    alias = re.sub(r"[\s\-_]+", " ", alias.strip())
    escaped = re.escape(alias).replace(r"\ ", r"[\s\-_]*")
    left = r"(?<![A-Za-z0-9])" if re.match(r"[A-Za-z0-9]", alias) else r"(?<![가-힣])"
    right = r"(?![A-Za-z0-9])" if re.search(r"[A-Za-z0-9]$", alias) else r"(?![가-힣])"
    return re.compile(left + escaped + right, re.IGNORECASE)


@dataclass
class ScaleRegistry:
    """내장 + 사용자 지정 척도. 이름 매칭의 유일한 창구."""

    scales: List[Scale] = field(default_factory=lambda: list(BUILTIN_SCALES))
    _patterns: List[Tuple[re.Pattern, Scale]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        pairs: List[Tuple[re.Pattern, Scale]] = []
        for scale in self.scales:
            for alias in (scale.name,) + tuple(scale.aliases):
                pairs.append((_alias_pattern(alias), scale))
        # 긴 별칭을 먼저 본다 — "HADS-A" 가 "HADS" 보다 우선해야 한다.
        pairs.sort(key=lambda pair: -len(pair[0].pattern))
        self._patterns = pairs

    def add(self, scale: Scale) -> None:
        """같은 이름이 있으면 **덮어쓴다**(사용자 지정이 내장보다 우선).

        한 개씩 넣어도 전체 재생성이 일어나지 않도록, 여기서는 기존 패턴을
        재사용하고 새 별칭만 컴파일해 끼워 넣는다.
        """
        lowered = scale.name.lower()
        self.scales = [s for s in self.scales if s.name.lower() != lowered]
        self.scales.append(scale)
        self._patterns = [
            pair for pair in self._patterns if pair[1].name.lower() != lowered
        ]
        for alias in (scale.name,) + tuple(scale.aliases):
            self._patterns.append((_alias_pattern(alias), scale))
        self._patterns.sort(key=lambda pair: -len(pair[0].pattern))

    def add_many(self, scales) -> None:
        """여러 척도를 한 번에 추가하고 패턴을 **한 번만** 다시 만든다.

        척도마다 _rebuild() 를 부르면 O(n²) 가 되어, 1,000개짜리 설정 파일에
        58초가 걸렸다(사실상 멈춘 것으로 보인다).
        """
        incoming = list(scales)
        if not incoming:
            return
        names = {s.name.lower() for s in incoming}
        self.scales = [s for s in self.scales if s.name.lower() not in names]
        self.scales.extend(incoming)
        self._rebuild()

    def find(self, text: str) -> Optional[Tuple[Scale, int, int]]:
        """텍스트에서 척도 이름을 찾으면 (척도, 시작, 끝). 없으면 ``None``.

        여러 개가 걸리면 **가장 앞에 나온 것**을 쓴다(그 줄의 주어일 가능성이 높다).
        """
        best: Optional[Tuple[Scale, int, int]] = None
        for pattern, scale in self._patterns:
            m = pattern.search(text)
            if m and (best is None or m.start() < best[1]):
                best = (scale, m.start(), m.end())
        return best

    def find_unconfigured(self, text: str) -> Optional[Tuple[str, str]]:
        """구조를 모르는 '알려진 이름'을 찾으면 (이름, 안내문)."""
        low = text.lower()
        for name, hint in NEEDS_CONFIG.items():
            if name.lower() in low:
                # 사용자가 이미 지정했다면 안내하지 않는다
                if self.find(name):
                    return None
                return name, hint
        return None


# ── 사용자 지정 파싱 ─────────────────────────────────────────────────────────

_SCALE_ARG = re.compile(r"^\s*(?P<name>[^=]{1,60}?)\s*=\s*(?P<lo>-?\d+(?:\.\d+)?)\s*:\s*"
                        r"(?P<hi>-?\d+(?:\.\d+)?)\s*:\s*(?P<items>\d+)\s*$")


def _validate(name: str, lo: float, hi: float, items: int, unit: float) -> None:
    if not name.strip():
        raise ScaleError("척도 이름이 비어 있습니다.")
    if not (lo < hi):
        raise ScaleError(f"'{name}': 최솟값({lo})이 최댓값({hi})보다 작아야 합니다.")
    if items <= 0 or items > 10_000:
        raise ScaleError(f"'{name}': 문항 수는 1~10000 사이여야 합니다 (받은 값: {items}).")
    if not (unit > 0):
        raise ScaleError(f"'{name}': 점수 증분(unit)은 0보다 커야 합니다.")
    if unit > (hi - lo):
        raise ScaleError(f"'{name}': 점수 증분({unit})이 척도 범위보다 큽니다.")


def parse_scale_arg(arg: str, percent_of_count: bool = False) -> Scale:
    """``ISI=0:28:7`` 형식 한 개를 :class:`Scale` 로."""
    m = _SCALE_ARG.match(arg)
    if not m:
        raise ScaleError(
            f"--scale 형식이 잘못되었습니다: {arg!r}\n"
            "  올바른 형식: --scale ISI=0:28:7   (이름=최소:최대:문항수)"
        )
    name = m.group("name").strip()
    lo = float(m.group("lo"))
    hi = float(m.group("hi"))
    items = int(m.group("items"))
    unit = (hi - lo) / items if percent_of_count else 1.0
    _validate(name, lo, hi, items, unit)
    return Scale(name, lo, hi, items, unit, integer_sum=not percent_of_count)


def load_scale_config(path) -> List[Scale]:
    """JSON 척도 설정 파일을 읽는다.

    ``{"ISI": {"min":0,"max":28,"items":7},
        "단어인지도": {"min":0,"max":100,"items":50,"percent_of_count":true,
                       "aliases":["word recognition score"]}}``
    """
    p = Path(path)
    if not p.exists():
        raise ScaleError(f"척도 설정 파일이 없습니다: {p}")
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScaleError(f"척도 설정 파일을 읽을 수 없습니다: {p.name} ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScaleError(f"척도 설정 JSON 을 해석할 수 없습니다: {p.name} ({exc})") from exc
    if not isinstance(data, dict):
        raise ScaleError("척도 설정은 {\"이름\": {...}} 형태의 객체여야 합니다.")
    out: List[Scale] = []
    for name, spec in data.items():
        if not isinstance(spec, dict):
            raise ScaleError(f"'{name}' 의 설정이 객체가 아닙니다.")
        # unit·aliases 도 사용자 입력이다. try 밖에 두면 `"unit": null`,
        # `"items": 0`, `"aliases": 5` 가 전부 날 트레이스백으로 터진다.
        try:
            lo = float(spec["min"])
            hi = float(spec["max"])
            items = int(spec["items"])
            pct = bool(spec.get("percent_of_count", False))
            if pct and items == 0:
                raise ValueError("percent_of_count 인데 items 가 0")
            unit = (hi - lo) / items if pct else float(spec.get("unit", 1.0))
            aliases = spec.get("aliases", ())
            if isinstance(aliases, str):
                aliases = (aliases,)
            aliases = tuple(str(a) for a in aliases)[:50]
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ScaleError(
                f"'{name}' 설정에 min/max/items 가 올바르게 있어야 합니다 ({exc})."
            ) from exc
        if not all(math.isfinite(v) for v in (lo, hi, unit)):
            raise ScaleError(f"'{name}': min/max/unit 은 유한한 수여야 합니다.")
        # 별칭이 숫자면 "1" 이 정규식이 되어 숫자 하나만 있어도 척도가 매칭된다.
        for alias in aliases:
            if not re.search(r"[A-Za-z가-힣]", alias):
                raise ScaleError(
                    f"'{name}': 별칭 {alias!r} 에 글자가 없습니다 — 숫자만으로는 척도를"
                    " 식별할 수 없습니다."
                )
        _validate(str(name), lo, hi, items, unit)
        out.append(Scale(str(name), lo, hi, items, unit, integer_sum=not pct, aliases=aliases))
    return out
