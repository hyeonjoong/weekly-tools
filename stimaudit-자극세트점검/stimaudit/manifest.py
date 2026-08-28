"""DEBUSSY 매니페스트 읽기 — 지표를 **다시 뽑지 않고 받아 씁니다.**

경계선입니다. 러프니스(asper)·샤프니스(acum)·ISO 532 라우드니스 같은
심리음향량은 이 툴이 **절대 자체 계산하지 않습니다.** 프록시로 흉내 내는 순간
stimaudit 은 DEBUSSY 의 열등한 사본이 됩니다. 필요하면 `--manifest` 로
DEBUSSY 가 뽑아 놓은 CSV 를 받아 쓰고, 없으면 "해당 축은 검사 안 함"으로
커버리지에 자백합니다.

받는 형식: `A1A3_manifest.csv` 스키마 — 첫 열이 파일 이름이고 나머지가 지표.
(`file, duration_s, sample_rate, laeq_dbfs_a, …, roughness_asper, tempo_bpm,
modulation_peak_hz, spectral_centroid_hz, sharpness_acum, spectral_slope_beta,
hnr_db, …`)

**통계 검정은 하지 않습니다.** 조건당 파일이 1~2개인 세트에서 p값은 거짓
정밀도입니다. 차이 값과 방향만 적습니다 — 검정이 필요하면 `statwise` 로 가십시오.
"""
from __future__ import annotations

import csv
import os
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: 파일 이름 열로 인정하는 헤더 이름(소문자 비교).
_FILE_COLUMNS = ("file", "filename", "output_file", "path", "name", "wav")


def _short(path: str) -> str:
    """오류 메시지에 절대경로(홈 디렉터리·사용자 이름)를 흘리지 않습니다.

    리포트 산출물은 이미 basename 만 쓰는데 오류 메시지만 전체 경로를
    인쇄하고 있었습니다 — 화면 캡처를 공유하는 순간 그대로 새어 나갑니다.
    """
    import os
    return os.path.basename(path) or path


def _finite(text: str):
    """유한한 실수면 그 값, 아니면 None.

    `float("nan")` / `float("inf")` 는 파이썬에서 통과하지만, 교란 후보표에
    들어가면 `최대차이 nan` 같은 무의미한 줄이 됩니다 — 숫자가 아닌 것으로 봅니다.
    """
    try:
        num = float(text)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


class ManifestError(Exception):
    """매니페스트를 읽지 못했습니다 — CLI 가 종료코드 2 로 바꿉니다."""


@dataclass
class Manifest:
    path: str
    metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)   # 파일 → 지표
    columns: List[str] = field(default_factory=list)
    skipped_columns: List[str] = field(default_factory=list)

    def covered(self, names: Sequence[str]) -> List[str]:
        return [n for n in names if n in self.metrics]


def load(path: str) -> Manifest:
    """매니페스트 CSV 를 읽습니다."""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
    except FileNotFoundError:
        raise ManifestError("매니페스트를 찾을 수 없습니다: {}".format(_short(path))) from None
    except UnicodeDecodeError as exc:
        raise ManifestError(
            "매니페스트가 UTF-8 이 아닙니다: {}\n사유: {}".format(_short(path), exc)) from exc
    except csv.Error as exc:
        # 한 셀이 131 072자를 넘으면 csv 모듈이 여기서 터집니다 — 트레이스백 대신
        # 한국어 오류로 바꿔 종료코드 2 로 보냅니다.
        raise ManifestError(
            "매니페스트 CSV 를 해석할 수 없습니다: {}\n사유: {}".format(_short(path), exc)) from exc
    except OSError as exc:
        raise ManifestError("매니페스트를 열 수 없습니다: {}\n사유: {}".format(
            path, exc.strerror or exc)) from exc
    if not rows:
        raise ManifestError("매니페스트가 비어 있습니다: {}".format(_short(path)))
    header = [h.strip() for h in rows[0]]
    if not header:
        raise ManifestError("매니페스트에 헤더가 없습니다.")
    fcol = 0
    for i, h in enumerate(header):
        if h.lower() in _FILE_COLUMNS:
            fcol = i
            break
    m = Manifest(path=path)
    numeric: Dict[str, int] = {}
    nonnumeric: List[str] = []
    for i, h in enumerate(header):
        if i == fcol or not h:
            continue
        vals = [r[i] for r in rows[1:] if i < len(r) and r[i].strip() != ""]
        ok = 0
        for v in vals:
            if _finite(v) is not None:
                ok += 1
        if vals and ok == len(vals):
            numeric[h] = i
        else:
            nonnumeric.append(h)
    m.columns = sorted(numeric)
    m.skipped_columns = nonnumeric
    for r in rows[1:]:
        if fcol >= len(r):
            continue
        name = unicodedata.normalize("NFC", os.path.basename(r[fcol].strip()))
        if not name:
            continue
        entry: Dict[str, float] = {}
        for h, i in numeric.items():
            if i < len(r) and r[i].strip() != "":
                num = _finite(r[i])
                if num is not None:
                    entry[h] = num
        m.metrics[name] = entry
    if not m.metrics:
        raise ManifestError("매니페스트에서 파일 행을 하나도 읽지 못했습니다: {}".format(_short(path)))
    return m


@dataclass
class ConfoundRow:
    """교란 후보 한 줄 — 조건별 평균과 최대 차이."""

    column: str
    per_condition: Dict[str, Optional[float]]
    max_diff: Optional[float]
    max_pair: Tuple[str, str]
    is_contrast: bool
    #: 척도 무관 크기 = 최대차이 / 조건 평균들의 절댓값 평균.
    #: 원시 차이로 정렬하면 단위가 큰 지표(spectral_centroid_hz, 수백~수천)가
    #: 항상 앞에 오고 러프니스(0.05 → 0.5, 10배 차이)가 표에서 밀려납니다.
    relative_diff: Optional[float] = None


def confound_table(man: Manifest, conditions: Dict[str, List[str]],
                   contrast: Optional[str]) -> Tuple[List[ConfoundRow], List[str]]:
    """조건 간 지표 차이표. 반환 = (행 목록, 매니페스트에 없는 파일 목록).

    `contrast` 로 지정한 축(의도한 차이)은 표에 남기되 `is_contrast` 로 구분해
    "이건 의도한 차이"라고 표시합니다. 나머지가 벌어져 있으면 교란 후보입니다.
    """
    missing: List[str] = []
    for files in conditions.values():
        for f in files:
            if f not in man.metrics:
                missing.append(f)
    rows: List[ConfoundRow] = []
    labels = list(conditions.keys())
    for col in man.columns:
        per: Dict[str, Optional[float]] = {}
        for cond in labels:
            vals = [man.metrics[f][col] for f in conditions[cond]
                    if f in man.metrics and col in man.metrics[f]]
            per[cond] = sum(vals) / len(vals) if vals else None
        best: Optional[float] = None
        pair = ("", "")
        for i, a in enumerate(labels):
            for b in labels[i + 1:]:
                if per[a] is None or per[b] is None:
                    continue
                d = abs(per[a] - per[b])
                if best is None or d > best:
                    best, pair = d, (a, b)
        scale = [abs(v) for v in per.values() if v is not None]
        mean_abs = sum(scale) / len(scale) if scale else 0.0
        rel = (best / mean_abs) if (best is not None and mean_abs > 0) else None
        rows.append(ConfoundRow(column=col, per_condition=per, max_diff=best,
                                max_pair=pair, is_contrast=(col == contrast),
                                relative_diff=rel))
    # 의도한 대조축을 맨 위로, 그다음은 **척도 무관** 차이가 큰 순.
    rows.sort(key=lambda r: (not r.is_contrast,
                             -(r.relative_diff if r.relative_diff is not None else 0.0)))
    return rows, sorted(set(missing))
