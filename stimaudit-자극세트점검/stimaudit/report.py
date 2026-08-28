"""리포트 — 콘솔 · Markdown · CSV · 문장 초안.

두 가지가 코드로 강제됩니다.

1. **커버리지 자백 없이는 리포트를 출력하지 않습니다.** `render_console` /
   `render_markdown` 은 `Coverage` 를 요구하고, 렌더 결과에 자백 블록이
   들어갔는지 마지막에 확인한 뒤에야 문자열을 돌려줍니다. "치명 0건"이
   정직한 문장이 되려면 무엇을 못 읽었고 무엇을 안 봤는지가 같은 화면에
   있어야 합니다.
2. **논문 유래 수치에는 심각도 표기가 붙지 않습니다.** 참조값은 `refs.py` 의
   `ReferenceValue`(심각도 필드가 없는 자료형)에서만 나오며, 항상 출처 문헌과
   "reference value · 임계값 아님" 고지를 같은 줄에 달고 인쇄됩니다.

산출물의 파일 경로는 **basename 만** 씁니다 — 절대경로가 들어가면 홈 디렉터리
사용자 이름이 논문 부록이나 협업자에게 딸려 나갑니다.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import findings as F
from . import levels, refs, safeio
from .analyze import FileMetrics
from .baseline import BaselineRow
from .claims import ClaimResult
from .design import Design
from .manifest import ConfoundRow
from .setcheck import LevelMatrix

#: 백틱 한 글자 — 마크다운 이스케이프에서 씁니다.
_BT = chr(96)

#: 커버리지 자백 블록의 제목 — 렌더 결과 검증에 씁니다.
COVERAGE_HEADER = "[커버리지 자백]"
#: 산출물 이름 (사용자 입력이 파일명에 들어가지 않습니다).
OUT_REPORT_MD = "자극점검.md"
OUT_ISSUES_CSV = "문제목록.csv"
OUT_TABLE_CSV = "자극기술표.csv"
OUT_TABLE_MD = "자극기술표.md"
OUT_MATRIX_CSV = "음량행렬.csv"
OUT_DRAFT_MD = "문장초안.md"
OUT_CONFOUND_CSV = "교란후보.csv"

TITLE = "stimaudit — 자극 세트 점검"


class ReportError(Exception):
    """리포트를 만들 수 없습니다(커버리지 자백 누락 등) — 내부 결함 신호."""


@dataclass
class ReportData:
    """리포트가 필요로 하는 전부. 커버리지는 필수입니다."""

    coverage: F.Coverage
    metrics: Dict[str, FileMetrics]
    order: List[str]
    findings: List[F.Finding]
    matrix: Optional[LevelMatrix] = None
    design: Optional[Design] = None
    claim_results: List[ClaimResult] = field(default_factory=list)
    confound_rows: List[ConfoundRow] = field(default_factory=list)
    confound_missing: List[str] = field(default_factory=list)
    baseline_rows: List[BaselineRow] = field(default_factory=list)
    baseline_unmatched: List[str] = field(default_factory=list)
    baseline_leftover: List[str] = field(default_factory=list)
    lufs_tol: float = 1.0
    lufs_crit: float = 2.0
    inspect_only: bool = False

    def condition_of(self, name: str) -> str:
        if not self.design:
            return ""
        return self.design.condition_of(name) or ""


def _fmt(value: Optional[float], digits: int = 2, unit: str = "") -> str:
    if value is None:
        return "—"
    return "{:.{d}f}{}".format(value, unit, d=digits)


def md_cell(text: object) -> str:
    """마크다운 표 셀 안전화.

    CSV 는 `safeio.sanitize_cell` 이 막지만 마크다운에는 대응물이 없었습니다.
    파일 이름에 세로줄이 있으면 표에 열이 하나 더 생기고, 백틱 세 개가 있으면
    리포트의 코드블록이 거기서 끝나 버립니다.
    """
    out = str(text)
    for ch in ("\r\n", "\n", "\r"):
        out = out.replace(ch, " ")
    out = out.replace("\\", "\\\\").replace("|", "\\|")
    return out.replace(_BT, "\u02cb")


def fence_safe(text: str) -> str:
    """코드펜스 안에 넣을 텍스트에서 펜스를 깨는 백틱 3연속을 무력화합니다."""
    return text.replace(_BT * 3, "\u02cb" * 3)


def width(text: str) -> int:
    """터미널 표시 폭. 한글·한자는 2칸을 차지합니다.

    `"{:<14s}".format()` 는 **문자 수**로 채우기 때문에 한국어 표에서 열이
    어긋납니다. 이 툴은 한국어 리포트가 본체이므로 표시 폭으로 맞춥니다.
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def flatten(text: str) -> str:
    """제어문자를 눌러 **리포트 한 줄이 여러 줄로 위조되는 것**을 막습니다.

    파일 이름은 사람이 짓는 값이고, 파일 이름에 개행을 넣는 것은 가능합니다.
    `a.wav\n[치명] 1건\n…` 같은 이름을 주면 콘솔 리포트와 `자극점검.md` 에
    가짜 `[치명]` 줄이 찍히는데, 정작 맨 아래 결론은 "치명 0건" 입니다 —
    리포트를 읽는 사람이 정반대의 결론을 얻습니다. CSV·마크다운 표·코드펜스는
    이미 막고 있었는데 **콘솔 렌더러만 빠져 있었습니다**.
    (적대적 검토 라운드 1, 안전성·테스트품질 감사 A2)
    """
    return "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in text)


def clip(text: str, limit: int) -> str:
    """표시 폭 기준으로 자릅니다(한글 절반이 잘려 깨지지 않게)."""
    text = flatten(text)
    if width(text) <= limit:
        return text
    out = []
    used = 0
    for c in text:
        w = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        if used + w > limit - 1:
            break
        out.append(c)
        used += w
    return "".join(out) + "…"


def lj(text: str, n: int) -> str:
    """표시 폭 기준 왼쪽 정렬."""
    text = clip(text, n)
    return text + " " * max(0, n - width(text))


def rj(text: str, n: int) -> str:
    """표시 폭 기준 오른쪽 정렬."""
    text = clip(text, n)
    return " " * max(0, n - width(text)) + text


def _header_line(d: ReportData) -> str:
    cov = d.coverage
    n_input_failed = sum(1 for _n, why in cov.unreadable if not why.startswith("(기준 폴더)"))
    n_base_failed = cov.n_unreadable - n_input_failed
    head = "입력 {}개 파일 / 읽음 {} / 못 읽음 {}".format(
        cov.n_input, cov.n_read, n_input_failed)
    if n_base_failed:
        head += " (기준 폴더에서 못 읽음 {})".format(n_base_failed)
    bits = [head]
    if d.design and d.design.conditions:
        parts = " · ".join("{} {}".format(c, len(fs)) for c, fs in d.design.conditions.items())
        bits.append("조건 {}개 ({})".format(len(d.design.conditions), parts))
    else:
        bits.append("설계 JSON 없음 — 조건 판정 안 함")
    return " · ".join(bits)


# ------------------------------------------------------------------ 콘솔

def render_console(d: ReportData) -> str:
    """터미널용 한국어 리포트."""
    if d.coverage is None:
        raise ReportError("커버리지 자백 없이는 리포트를 만들지 않습니다.")
    L: List[str] = [TITLE, _header_line(d), ""]

    crit = [f for f in d.findings if f.severity == F.CRITICAL]
    warn = [f for f in d.findings if f.severity == F.WARNING]

    if d.inspect_only:
        L.append("--inspect 모드 — 판정하지 않고 측정값만 보여줍니다.")
        L.append("")
    else:
        L.extend(_finding_block("[치명]", crit))
        L.extend(_finding_block("[경고]", warn))

    L.extend(_reference_block(d))
    L.extend(_level_block(d))
    L.extend(_matrix_block(d))
    L.extend(_claims_block(d))
    L.extend(_confound_block(d))
    L.extend(_baseline_block(d))
    L.extend(_coverage_block(d.coverage))

    L.append("")
    if d.inspect_only:
        # 못 읽은 파일이 있으면 --inspect 에서도 그 사실을 **맨 아래 한 줄**로
        # 말합니다. 사용법.md 가 "맨 아래 한 줄부터 보세요" 라고 안내하는데,
        # 전에는 이 분기가 먼저 걸려 그 줄이 --inspect 경로에 아예 없었습니다.
        if not d.coverage.complete:
            L.append("판정불가 — 못 읽은 파일이 {}개 있습니다. 다 못 들었으면 "
                     "어떤 결론도 세트 전체에 대한 것이 아닙니다."
                     .format(d.coverage.n_unreadable))
        L.append("판정하지 않았습니다. 조건 판정과 주장 대조는 --design 설계.json 을 붙이십시오.")
        L.append("설계 JSON 뼈대는 --emit-design 으로 받을 수 있습니다.")
    elif not d.coverage.complete:
        L.append("판정불가 — 못 읽은 파일이 {}개 있습니다. 다 못 들었으면 \"치명 0건\"은 거짓말입니다."
                 .format(d.coverage.n_unreadable))
    else:
        # 음량 대조에서 빠진 조건이 있으면 **맨 아래 한 줄에** 그 사실을 붙입니다.
        # 자격 조건이 판정 목록 안에만 있으면 "치명 0건"만 읽고 지나갑니다.
        n_dropped = sum(1 for f in d.findings
                        if f.kind == F.KIND_LEVEL_UNDECIDABLE)
        scope = " (음량 대조에서 빠진 조건 {}개 — 아래 판정이 그 조건을 덮지 않습니다)".format(
            n_dropped) if n_dropped else ""
        if crit:
            L.append("치명 {}건. 이 상태로 실험에 태우면 안 됩니다.{}".format(len(crit), scope))
        elif warn:
            L.append("치명 0건 · 경고 {}건. 경고를 읽고 판단하십시오.{}".format(len(warn), scope))
        else:
            L.append("치명 0건 · 경고 0건. 세트로 써도 됩니다 (검사한 축에 한해서).{}".format(scope))

    text = "\n".join(L)
    if COVERAGE_HEADER not in text:
        raise ReportError("커버리지 자백 블록이 리포트에 없습니다 — 출력을 거부합니다.")
    return text


def _finding_block(mark: str, items: Sequence[F.Finding]) -> List[str]:
    if not items:
        return []
    L = ["{} {}건".format(mark, len(items))]
    # 유형 칸은 가장 긴 이름("트루피크 여유 부족" = 표시폭 18)보다 넓어야 합니다 —
    # 딱 맞추면 구분 공백이 사라져 `트루피크 여유 부족B_clipped_dc.wav` 가 됩니다.
    pad = " " * 48
    for f in items:
        L.append("  " + lj(f.kind, 20) + lj(f.subject, 26) + f.detail)
        if f.measured:
            L.append(pad + "실측 " + f.measured)
        if f.reference:
            L.append(pad + "기준 " + f.reference)
        if f.action:
            L.append(pad + "조치 " + f.action)
        if f.consequence:
            L.append(pad + "→ " + f.consequence)
    L.append("")
    return L


def _reference_block(d: ReportData) -> List[str]:
    """논문 참조값 대조 — **판정 없음**. 값·참조값·출처를 나란히 놓습니다."""
    # **모든 파일을 싣습니다.** 못 잰 파일을 조용히 빼면, 커버리지 자백은
    # "상승/하강 시간을 검사했다"고 말하는데 그 파일만 표에서 사라집니다
    # (2초 넘는 페이드인이 그랬습니다 — 수면 음향 자극에서는 흔한 길이입니다).
    if not d.order:
        return []
    rows_rise = [(n, d.metrics[n].onset_rise_ms) for n in d.order if n in d.metrics]
    rows_mod = [(n, d.metrics[n].env_mod_hz, d.metrics[n].env_mod_ratio,
                 d.metrics[n].env_mod_depth) for n in d.order if n in d.metrics]
    L = ["[정보] 논문 참조값 대조 — 판정하지 않습니다. 값만 나란히 놓습니다."]
    by_axis = {r.axis: r for r in refs.REFERENCES}
    if rows_rise:
        r = by_axis["온셋 상승시간 (attack)"]
        L.append("  · {} — 참조 {} · 출처 {} · {}".format(
            r.axis, r.value_text, r.citation, refs.DISCLAIMER))
        for n, v in rows_rise:
            if v is None:
                edge = d.metrics[n].edge_window_ms / 1000.0
                L.append("      " + lj(n, 36) + rj("—", 12)
                         + "  (앞 {:.0f}초 안에서 피크의 50 % 에 도달하지 않음)".format(edge))
            else:
                L.append("      " + lj(n, 36) + rj("{:.1f} ms".format(v), 12))
    if rows_mod:
        r = by_axis["템포 / 변조율"]
        L.append("  · {} — 참조 {} · 출처 {} · {}".format(
            r.axis, r.value_text, r.citation, refs.DISCLAIMER))
        s = by_axis["서파(SO) 자극 반복률"]
        L.append("  · {} — 참조 {} · 출처 {} · {}".format(
            s.axis, s.value_text, s.citation, refs.DISCLAIMER))
        L.append("    (포락선에서 잰 지배적 변조율 하나입니다. 상대강도가 낮으면 "
                 "주기적 변조가 아니라 잡음의 요동입니다.)")
        for n, hz, ratio, depth in rows_mod:
            if hz is None:
                if depth is not None and depth < 0.01:
                    why = "변조 깊이 {:.3f} % — 사실상 평평함".format(depth * 100.0)
                else:
                    why = "분석 대역(≤ 20 Hz) 안에 뚜렷한 변조 없음"
                L.append("      " + lj(n, 36) + rj("—", 12) + "  ({})".format(why))
                continue
            dur = d.metrics[n].duration_s
            weak = ""
            if hz * dur < 3.0:
                weak = "  ※ 3주기 미만 — 페이드 모양일 수 있음"
            elif (ratio or 0.0) < 0.05:
                weak = "  ※ 상대강도 낮음 — 잡음의 요동일 수 있음"
            L.append("      " + lj(n, 36) + rj("{:.3f} Hz".format(hz), 12)
                     + rj("({:.1f} BPM)".format(hz * 60.0), 14)
                     + rj("깊이 {:.1f}%".format((depth or 0.0) * 100.0), 12)
                     + rj("상대강도 {:.2f}".format(ratio or 0.0), 16) + weak)
    L.append("  " + refs.LONG_DISCLAIMER.replace("\n", "\n  "))
    L.append("")
    return L


def _level_block(d: ReportData) -> List[str]:
    if not d.order:
        return []
    L = ["[정보] 레벨 — 논문 단위 (Table 2 항목 1: LAeq + dynamic range, dBFS 기준)"]
    L.append("  " + lj("파일", 36) + rj("LAeq", 12) + rj("LAmax", 12) + rj("다이내믹레인지", 16))
    for n in d.order:
        m = d.metrics[n]
        L.append("  " + lj(n, 36) + rj(_fmt(m.laeq_dbfs, 1, " dB"), 12)
                 + rj(_fmt(m.lamax_dbfs, 1, " dB"), 12)
                 + rj(_fmt(m.dynamic_range_db, 1, " dB"), 16))
    L.append("  ※ " + refs.LEVEL_RATIONALE)
    L.append("  ※ 절대 음압(dB SPL)이 아니라 풀스케일 대비(dBFS)입니다.")
    L.append("  ※ 다이내믹 레인지는 LAmax 아래 {:.0f} dB 미만인 창을 제외한 뒤 구한 "
             "p95−p5 이므로 구조적으로 {:.0f} dB 를 넘지 않습니다.".format(
                 levels.DR_FLOOR_BELOW_MAX, levels.DR_FLOOR_BELOW_MAX))
    L.append("")
    L.append("[정보] 레벨 — 조건 매칭용 (LUFS · LRA · 트루피크)")
    L.append("  " + lj("파일", 36) + rj("LUFS", 13) + rj("LRA", 10)
             + rj("트루피크", 14) + rj("표본피크", 14))
    for n in d.order:
        m = d.metrics[n]
        L.append("  " + lj(n, 36) + rj(_fmt(m.lufs_i, 1, " LUFS"), 13)
                 + rj(_fmt(m.lra, 1, " LU"), 10)
                 + rj(_fmt(m.true_peak_dbfs, 2, " dBTP"), 14)
                 + rj(_fmt(m.sample_peak_dbfs, 2, " dBFS"), 14))
    L.append("  ※ " + refs.LUFS_PROVENANCE.replace("\n", "\n  ※ "))
    L.append("  ※ 트루피크는 4배 오버샘플 근사입니다(창 씌운 sinc). 표본 피크는 정확한 값입니다.")
    if any(d.metrics[n].lra is None for n in d.order):
        L.append("  ※ LRA 가 대시(—)인 파일은 길이가 4초 미만이라 3초 블록이 2개 "
                 "나오지 않은 것입니다 — 계산하지 않았습니다.")
    L.append("")
    return L


def _matrix_block(d: ReportData) -> List[str]:
    mx = d.matrix
    if mx is None or len(mx.labels) < 2:
        return []
    what = "조건 간" if mx.is_condition_level else "파일 간"
    L = ["[정보] {} LUFS 차이 행렬  (LUFS 는 논문 유래 아님 — 조건 매칭용 관행 지표)".format(what)]
    labels = mx.labels
    wide = min(max([width(x) for x in labels] + [10]), 30)
    cell = max(11, min(16, max(width(x) for x in labels) + 2))
    L.append("  " + lj("", wide) + "".join(rj(x, cell) for x in labels))
    for i, a in enumerate(labels):
        cells = []
        for j, b in enumerate(labels):
            if i == j:
                cells.append(rj("—", cell))
            elif j < i:
                cells.append(rj("", cell))
            else:
                v = mx.diffs.get((a, b))
                if v is None:
                    cells.append(rj("?", cell))
                else:
                    star = "*" if v > mx.tol else ""
                    cells.append(rj("{:.1f}{}".format(v, star), cell))
        L.append("  " + lj(a, wide) + "".join(cells))
    L.append("  (* = --lufs-tol {:.1f} LU 초과 · 치명은 {:.1f} LU 초과)".format(mx.tol, d.lufs_crit))
    if not mx.is_condition_level:
        L.append("  설계 JSON 이 없어 조건 판정은 하지 않았습니다 — 정보로만 보십시오.")
    L.append("")
    return L


def _claims_block(d: ReportData) -> List[str]:
    if not d.claim_results:
        return []
    L = ["[정보] 주장 대조 — 설계 JSON 의 claims 만 검사합니다 (파일명에서 추측하지 않습니다)"]
    L.append("  " + lj("파일", 32) + lj("항목", 13) + rj("설계", 14)
             + rj("실측", 16) + "  " + "판정")
    for r in d.claim_results:
        L.append("  " + lj(r.file, 32) + lj(r.key, 13) + rj(r.claimed_text(), 14)
                 + rj(r.measured_text(), 16) + "  " + r.verdict)
        if r.note:
            L.append("      └ {}".format(r.note))
    L.append("")
    return L


def _confound_block(d: ReportData) -> List[str]:
    if not d.confound_rows:
        # 지표를 하나도 못 붙였을 때가 **가장 말해야 하는 순간**입니다. 전에는
        # 여기서 그냥 돌아서서, 세미콜론 구분 CSV(유럽·한국 엑셀의 기본 내보내기)나
        # 파일명이 안 맞는 매니페스트를 줘도 화면에 아무 말이 없었습니다.
        # (적대적 검토 라운드 1, 엣지케이스 파괴자 발견 6)
        if d.confound_missing:
            return ["[정보] 교란 후보 — 계산하지 못했습니다",
                    "  ※ 매니페스트에서 짝을 못 찾은 파일 {}개: {}".format(
                        len(d.confound_missing), ", ".join(flatten(x) for x in d.confound_missing[:5])),
                    "  ※ 매니페스트의 `file` 열이 입력 파일 이름과 같은지, 구분자가 "
                    "쉼표인지 확인하십시오 (세미콜론 CSV 는 읽지 못합니다).",
                    ""]
        return []
    labels = list(d.design.conditions.keys()) if d.design else []
    L = ["[정보] 교란 후보 — DEBUSSY 매니페스트의 지표를 조건 간 비교합니다 (통계 검정 없음)"]
    L.append("  " + lj("지표", 28) + rj("최대차이", 14) + rj("상대차이", 12) + "  조건별 평균")
    for row in d.confound_rows[:14]:
        per = " / ".join("{} {:.4g}".format(c, row.per_condition[c])
                         for c in labels if row.per_condition.get(c) is not None)
        tag = "  ← 의도한 대조축" if row.is_contrast else ""
        L.append("  " + lj(row.column, 28) + rj(_fmt(row.max_diff, 4), 14)
                 + rj(_fmt(row.relative_diff, 3), 12) + "  " + per + tag)
    if len(d.confound_rows) > 14:
        L.append("  … 화면에는 14개만 보여 줍니다. 전체 {}개는 {} 에 있습니다.".format(
            len(d.confound_rows), OUT_CONFOUND_CSV))
    if d.confound_missing:
        L.append("  ※ 매니페스트에 없는 파일 {}개: {}".format(
            len(d.confound_missing), ", ".join(flatten(x) for x in d.confound_missing[:5])))
    L.append("  ※ 순위는 **상대차이**(최대차이 / 조건평균 절댓값 평균) 기준입니다 — "
             "원시 차이로 줄 세우면 단위가 큰 지표만 위로 올라옵니다.")
    L.append("  ※ 조건당 파일이 1~2개인 세트에 p값을 붙이면 거짓 정밀도입니다 — 검정은 statwise 로.")
    L.append("")
    return L


def _baseline_block(d: ReportData) -> List[str]:
    if not d.baseline_rows and not d.baseline_unmatched:
        return []
    L = ["[정보] 버전 대조 — 이전 폴더와의 차이"]
    for r in d.baseline_rows:
        L.append("  " + lj(r.name, 36) + r.summary())
        L.append("      " + lj("(기준 " + r.baseline_name + ")", 34)
                 + "{} → {}".format(_fmt(r.lufs_before, 1, " LUFS"),
                                    _fmt(r.lufs_now, 1, " LUFS")))
    if d.baseline_unmatched:
        L.append("  ※ 기준 폴더에서 짝을 못 찾은 파일 {}개: {}".format(
            len(d.baseline_unmatched), ", ".join(flatten(x) for x in d.baseline_unmatched[:5])))
        L.append("     이름이 바뀌었으면 설계 JSON 의 `pairs` 에 적으십시오 (추측하지 않습니다).")
    if d.baseline_leftover:
        L.append("  ※ 기준 폴더에만 있고 이번에 안 온 파일 {}개: {}".format(
            len(d.baseline_leftover), ", ".join(flatten(x) for x in d.baseline_leftover[:5])))
    L.append("")
    return L


def _coverage_block(cov: F.Coverage) -> List[str]:
    """**필수 블록.** 무엇을 읽었고 무엇을 안 봤는지."""
    L = [COVERAGE_HEADER]
    L.append("  읽음: {}파일 / {}채널 / 총 {:.1f}초   ·   못 읽음: {}".format(
        cov.n_read, cov.n_channels_total, cov.total_seconds, cov.n_unreadable))
    for name, why in cov.unreadable:
        L.append("    × {} — {}".format(flatten(name), flatten(why)))
    for name, note in cov.read_notes:
        # 잘린 data 청크나 ffmpeg 디코드 경유는 "깨끗하게 읽었다"가 아닙니다.
        L.append("    ! {} — {}".format(flatten(name), flatten(note)))
    L.append("  검사한 축: " + (" · ".join(cov.axes_checked) if cov.axes_checked else "없음"))
    L.append("  검사 안 함:")
    if cov.axes_skipped:
        for axis, why in cov.axes_skipped:
            L.append("    · {} — {}".format(axis, why))
    else:
        L.append("    · 없음")
    if cov.design_note:
        L.append("  설계: " + cov.design_note)
    if cov.confound_note:
        L.append("  교란 후보: " + cov.confound_note)
    L.append("  분석 소요: {:.1f}초".format(cov.elapsed_seconds))
    L.append("")
    return L


# ---------------------------------------------------------------- Markdown

def render_markdown(d: ReportData) -> str:
    if d.coverage is None:
        raise ReportError("커버리지 자백 없이는 리포트를 만들지 않습니다.")
    L = ["# stimaudit — 자극 세트 점검", "", _header_line(d), ""]
    L.append("> 이 툴은 **파일들 사이**만 봅니다. 소리 하나하나의 음향 지표는 DEBUSSY,")
    L.append("> 파일별 티어 준수 판정은 `bell_acoustic_qc.py` 소관입니다.")
    L.append("")
    L.append("```")
    L.append(fence_safe(render_console(d)))
    L.append("```")
    L.append("")
    L.append("## 판정 규칙")
    L.append("")
    L.append("| 심각도 | 무엇에 붙는가 |")
    L.append("|---|---|")
    L.append("| 치명 | 조건 간 음량 차이 > {:.1f} LU · 주장 불일치 · 클리핑 · 죽은 파일 |".format(d.lufs_crit))
    L.append("| 경고 | 조건 간 음량 차이 > {:.1f} LU · 조건 내 산포 · 좌우 불균형 · DC · 트루피크 · 포맷/길이 불일치 · 시작·끝 클릭 위험 |".format(d.lufs_tol))
    L.append("| 정보 | 논문 참조값 대조 · 레벨 표 · 행렬 · 교란 후보 · 버전 대조 |")
    L.append("")
    L.append("논문 유래 수치에는 **어떤 심각도도 붙지 않습니다.** 개정본(1st revision)이")
    L.append("모든 수치를 reference value 로 재라벨했기 때문입니다 — 측정값·참조값·출처를")
    L.append("나란히 인쇄할 뿐 준수/위반을 찍지 않습니다.")
    L.append("")
    text = "\n".join(L)
    if COVERAGE_HEADER not in text:
        raise ReportError("커버리지 자백 블록이 리포트에 없습니다 — 출력을 거부합니다.")
    return text


# ------------------------------------------------------------ 문장 초안

def build_draft(d: ReportData) -> str:
    """Methods 에 붙일 자극 기술 문단 (KR/EN).

    **측정하지 않은 축은 문장에서 뺍니다.** 러프니스·샤프니스를 재지 않았으면
    문장에 그 단어가 나오지 않습니다. 뺐다는 사실은 리포트의 커버리지 자백에
    남습니다 — 문장 자체에 "재지 않았다"고 쓰면 Methods 에 그대로 붙일 수 없으니,
    문장은 잰 것만 말하고 안 잰 것은 리포트가 말합니다.
    """
    vals = [d.metrics[n].lufs_i for n in d.order if d.metrics[n].lufs_i is not None]
    n_files = len(d.order)
    criticals = [f for f in d.findings if f.severity == F.CRITICAL]
    # 세트를 다 읽지 못했으면 이 문단은 **세트 전체의 기술이 아닙니다.**
    # 전에는 배너가 치명 유무로만 걸려 있어서, 못 읽은 파일이 있는 깨끗한 세트가
    # 아무 표시 없이 "모든 자극은 …" 이라는 **거짓 문장**을 받았습니다 — 그것도
    # 사용자가 원고에 그대로 붙이라고 안내받는 바로 그 파일에서.
    # (적대적 검토 라운드 1, 문서 정직성 비평 P0)
    complete = d.coverage is None or d.coverage.complete
    lines: List[str] = ["# 자극 기술 문장 초안", ""]
    if not complete:
        lines += [
            "> **경고 — 읽지 못한 파일이 {}개 있습니다.**".format(d.coverage.n_unreadable),
            "> 아래 문단은 **읽은 파일만** 기술한 것이라 세트 전체의 기술이 아닙니다.",
            "> 못 읽은 파일이 무엇이고 왜 못 읽었는지는 `{}` 의 커버리지 자백에 있습니다.".format(OUT_REPORT_MD),
            "",
        ]
    if criticals:
        # 치명이 남은 세트의 기술 문단을 아무 표시 없이 내주면, 그대로 복사해
        # 붙이는 순간 리포트가 잡은 결함이 원고에서 사라집니다.
        lines += [
            "> **경고 — 이 문단을 아직 붙이지 마십시오.**",
            "> 이 세트에는 치명 {}건이 남아 있습니다({}).".format(
                len(criticals),
                " · ".join(sorted({f.kind for f in criticals}))),
            "> 아래 숫자는 **지금 상태의 자극**을 정확히 기술한 것이지, 쓸 만한 자극이라는 뜻이 아닙니다.",
            "> 자극을 고친 뒤 다시 돌려서 받은 문단을 쓰십시오.",
            "",
        ]
    lines += [
                        "아래 문단은 **측정된 축만** 언급합니다. 재지 않은 축은 의도적으로",
                        "빠져 있고, 무엇이 빠졌는지는 `{}` 의 커버리지 자백에 있습니다.".format(OUT_REPORT_MD),
                        "(문장에 \"재지 않았다\"고 쓰면 Methods 에 그대로 붙일 수 없으므로,",
                        " 문장은 잰 것만 말하고 안 잰 것은 리포트가 말합니다.)",
                        "", "## 한국어", ""]
    kr: List[str] = []
    en: List[str] = []
    if vals:
        lo, hi = min(vals), max(vals)
        spread = hi - lo
        # `_matrix_max(d) or spread` 는 안 됩니다 — 완벽히 맞은 세트의 0.0 이
        # falsy 라서 파일 간 산포가 "조건 간 최대 차이"로 둔갑합니다.
        # **조건이 실제로 둘 이상일 때만** "조건 간" 이라고 씁니다. 설계 JSON 이
        # 없거나 --emit-design 뼈대(전 파일이 한 조건)를 그대로 쓴 경우에는
        # 파일 간 산포가 나오는데, 그것을 "between-condition difference" 라고
        # 쓰면 **하지 않은 실험 통제를 했다고 주장하는 문장**이 됩니다.
        # (적대적 검토 라운드 1, 문서 정직성 비평 P1)
        by_condition = (d.matrix is not None and d.matrix.is_condition_level
                        and len(d.matrix.labels) >= 2)
        mmax = _matrix_max(d) if by_condition else None
        if mmax is None:
            mmax = spread
            gap_kr = "파일 간 최대 차이 {:.1f} LU".format(mmax)
            gap_en = "maximum between-file difference {:.1f} LU".format(mmax)
        else:
            gap_kr = "조건 간 최대 차이 {:.1f} LU".format(mmax)
            gap_en = "maximum between-condition difference {:.1f} LU".format(mmax)
        kr.append("자극 {}개의 통합 라우드니스(ITU-R BS.1770-4)는 {:.1f} ~ {:.1f} LUFS 였다"
                  "({}).".format(n_files, lo, hi, gap_kr))
        en.append("Integrated loudness (ITU-R BS.1770-4) of the {} stimuli ranged from "
                  "{:.1f} to {:.1f} LUFS ({})."
                  .format(n_files, lo, hi, gap_en))
    laeqs = [d.metrics[n].laeq_dbfs for n in d.order if d.metrics[n].laeq_dbfs is not None]
    lamaxs = [d.metrics[n].lamax_dbfs for n in d.order if d.metrics[n].lamax_dbfs is not None]
    if laeqs and lamaxs:
        kr.append("A-가중 등가레벨(LAeq)은 {:.1f} ~ {:.1f} dBFS, 최대레벨(LAmax)은 "
                  "{:.1f} ~ {:.1f} dBFS 였다(재생 체인 보정 전 값이므로 절대 음압이 아니다)."
                  .format(min(laeqs), max(laeqs), min(lamaxs), max(lamaxs)))
        en.append("A-weighted equivalent level (LAeq) ranged from {:.1f} to {:.1f} dBFS and "
                  "maximum level (LAmax) from {:.1f} to {:.1f} dBFS; these are full-scale "
                  "referenced and not calibrated sound pressure levels."
                  .format(min(laeqs), max(laeqs), min(lamaxs), max(lamaxs)))
    durs = [d.metrics[n].duration_s for n in d.order]
    if durs:
        if max(durs) - min(durs) < 0.05:
            if complete:
                kr.append("모든 자극의 길이는 {:.1f}초였다.".format(durs[0]))
                en.append("All stimuli were {:.1f} s long.".format(durs[0]))
            else:
                kr.append("읽은 자극 {}개의 길이는 {:.1f}초였다.".format(n_files, durs[0]))
                en.append("The {} stimuli that could be read were {:.1f} s long."
                          .format(n_files, durs[0]))
        else:
            kr.append("자극 길이는 {:.1f} ~ {:.1f}초였다.".format(min(durs), max(durs)))
            en.append("Stimulus duration ranged from {:.1f} to {:.1f} s.".format(min(durs), max(durs)))
    fmts = {d.metrics[n].info.format_key for n in d.order}
    if len(fmts) == 1 and d.order:
        info = d.metrics[d.order[0]].info
        if complete:
            kr.append("모든 파일은 {} Hz · {}채널 · {}비트로 저장되었다.".format(
                info.sample_rate, info.n_channels, info.bits))
            en.append("All files were stored at {} Hz, {} channel(s), {}-bit.".format(
                info.sample_rate, info.n_channels, info.bits))
        else:
            kr.append("읽은 파일 {}개는 모두 {} Hz · {}채널 · {}비트로 저장되어 있었다.".format(
                n_files, info.sample_rate, info.n_channels, info.bits))
            en.append("The {} files that could be read were all stored at "
                      "{} Hz, {} channel(s), {}-bit.".format(
                          n_files, info.sample_rate, info.n_channels, info.bits))
    matched = [r for r in d.claim_results if r.verdict == "일치"]
    failed = [r for r in d.claim_results if r.verdict != "일치"]
    if matched and not failed:
        # 하나라도 어긋났으면 "확인되었다"는 문장을 아예 쓰지 않습니다.
        # 맞은 것만 골라 적으면 절반의 진실이 되고, Methods 에서 그건 거짓입니다.
        more_kr = " 외 {}건".format(len(matched) - 4) if len(matched) > 4 else ""
        more_en = " and {} more".format(len(matched) - 4) if len(matched) > 4 else ""
        kr.append("설계상 주장한 값({}{})은 신호에서 확인되었다.".format(
            " · ".join("{} {}".format(r.file, r.key) for r in matched[:4]), more_kr))
        en.append("Design claims ({}{}) were verified against the signals.".format(
            ", ".join("{} {}".format(r.file, r.key) for r in matched[:4]), more_en))
    elif failed:
        kr.append("※ 주장 대조 {}건 중 {}건이 어긋나거나 판정되지 않았습니다 — "
                  "확인되지 않은 값을 Methods 에 쓰지 마십시오. "
                  "자세한 내용은 `{}` 의 주장 대조 표를 보십시오.".format(
                      len(d.claim_results), len(failed), OUT_TABLE_MD))
        en.append("NOTE: {} of {} design claims did not verify — do not state "
                  "unverified values in Methods. See the claim table in `{}`.".format(
                      len(failed), len(d.claim_results), OUT_TABLE_MD))
    lines.extend("- " + s for s in kr)
    lines.extend(["", "## English", ""])
    lines.extend("- " + s for s in en)
    lines.extend(["", "## 붙이기 전에 확인할 것", "",
                  "- 절대 음압(dB SPL)을 적어야 한다면 재생 장비를 실측해 보정 상수를 구하십시오.",
                  "  이 툴의 값은 전부 dBFS 기준입니다.",
                  "- 러프니스·샤프니스 등 심리음향량이 필요하면 DEBUSSY 로 뽑아 별도로 적으십시오.",
                  ""])
    return "\n".join(lines)


def _matrix_max(d: ReportData) -> Optional[float]:
    if d.matrix is None:
        return None
    vals = [v for v in d.matrix.diffs.values() if v is not None]
    return max(vals) if vals else None


# ------------------------------------------------------------------ 파일

#: 이 툴이 만들 수 있는 산출물 이름 전부 — 쓰기 **전에** 한꺼번에 검사합니다.
ALL_OUTPUTS = (OUT_REPORT_MD, OUT_ISSUES_CSV, OUT_TABLE_CSV, OUT_TABLE_MD,
               OUT_MATRIX_CSV, OUT_DRAFT_MD, OUT_CONFOUND_CSV)


def write_outputs(out_dir: str, d: ReportData) -> List[str]:
    """산출물 전부를 `out_dir` 에 씁니다. 반환은 쓴 파일 경로들.

    **먼저 전부 검사하고, 그다음에 씁니다.** 파일마다 쓰기 직전에 검사하면
    거절이 세 번째 산출물에서 걸렸을 때 앞의 두 개가 이미 입력 폴더에
    떨어져 있습니다 (라운드 2 검증 지적).
    """
    for name in ALL_OUTPUTS:
        safeio.refuse_if_input(os.path.join(out_dir if isinstance(out_dir, str)
                                            else os.fspath(out_dir), name))
    written: List[str] = []
    written.append(safeio.write_text(out_dir, OUT_REPORT_MD, render_markdown(d) + "\n"))
    written.append(safeio.write_csv(
        out_dir, OUT_ISSUES_CSV,
        ["파일", "조건", "유형", "심각도", "실측값", "기준값", "설명",
         "조치", "연구상_의미"],
        [[f.subject, f.condition, f.kind, f.severity, f.measured, f.reference,
          f.detail, f.action, f.consequence] for f in d.findings]))
    written.append(safeio.write_csv(out_dir, OUT_TABLE_CSV, _table_header(d), _table_rows(d)))
    written.append(safeio.write_text(out_dir, OUT_TABLE_MD, _table_markdown(d)))
    written.append(safeio.write_csv(out_dir, OUT_MATRIX_CSV, *_matrix_csv(d)))
    written.append(safeio.write_text(out_dir, OUT_DRAFT_MD, build_draft(d)))
    if d.confound_rows:
        labels = list(d.design.conditions.keys()) if d.design else []
        written.append(safeio.write_csv(
            out_dir, OUT_CONFOUND_CSV,
            ["지표", "의도한_대조축", "최대차이", "상대차이", "최대차이_조건쌍"] + labels,
            [[r.column, "예" if r.is_contrast else "", _num(r.max_diff, 6),
              _num(r.relative_diff, 4), " ↔ ".join(x for x in r.max_pair if x)]
             + [_num(r.per_condition.get(c), 6) for c in labels]
             for r in d.confound_rows]))
    return written


def _table_header(d: ReportData) -> List[str]:
    return ["파일", "조건", "길이_s", "샘플레이트_Hz", "채널", "비트depth", "인코딩",
            "LUFS", "LRA_LU", "트루피크_dBTP", "표본피크_dBFS",
            "LAeq_dBFS", "LAmax_dBFS", "다이내믹레인지_dB",
            "선두무음_ms", "말미무음_ms", "상승시간_ms", "하강시간_ms",
            "좌우RMS차_dB", "클리핑구간수", "주장", "주장_실측", "주장_판정"]


def _claims_for(d: ReportData, name: str) -> Tuple[str, str, str]:
    rs = [r for r in d.claim_results if r.file == name]
    if not rs:
        return "", "", ""
    return ("; ".join("{}={}".format(r.key, r.claimed_text()) for r in rs),
            "; ".join("{}={}".format(r.key, r.measured_text()) for r in rs),
            "; ".join("{}:{}".format(r.key, r.verdict) for r in rs))


def _table_rows(d: ReportData) -> List[List[object]]:
    rows: List[List[object]] = []
    for n in d.order:
        m = d.metrics[n]
        c1, c2, c3 = _claims_for(d, n)
        rows.append([
            n, d.condition_of(n), round(m.duration_s, 3), m.info.sample_rate,
            m.info.n_channels, m.info.bits, m.info.encoding,
            _num(m.lufs_i, 2), _num(m.lra, 2), _num(m.true_peak_dbfs, 2),
            _num(m.sample_peak_dbfs, 2), _num(m.laeq_dbfs, 2), _num(m.lamax_dbfs, 2),
            _num(m.dynamic_range_db, 2), round(m.lead_silence_ms, 1),
            round(m.tail_silence_ms, 1), _num(m.onset_rise_ms, 1), _num(m.offset_fall_ms, 1),
            _num(m.lr_rms_diff_db, 2), m.clip_run_count, c1, c2, c3])
    return rows


def _num(v: Optional[float], digits: int) -> object:
    return "" if v is None else round(v, digits)


def _table_markdown(d: ReportData) -> str:
    head = ["파일", "조건", "길이(s)", "fs(Hz)", "ch", "bit", "LUFS", "LRA(LU)",
            "트루피크(dBTP)", "LAeq(dBFS)", "LAmax(dBFS)", "DR(dB)"]
    L = ["# 자극 기술표 (Methods 용)", "",
         "레벨은 전부 **풀스케일 대비(dBFS)** 입니다 — 절대 음압이 아닙니다.",
         "LUFS 는 논문 유래가 아니라 조건 매칭용 관행 지표입니다.", "",
         "| " + " | ".join(head) + " |",
         "|" + "|".join(["---"] * len(head)) + "|"]
    for n in d.order:
        m = d.metrics[n]
        L.append("| " + " | ".join([
            md_cell(n), md_cell(d.condition_of(n) or "—"), "{:.1f}".format(m.duration_s),
            str(m.info.sample_rate), str(m.info.n_channels), str(m.info.bits),
            _fmt(m.lufs_i, 1), _fmt(m.lra, 1), _fmt(m.true_peak_dbfs, 2),
            _fmt(m.laeq_dbfs, 1), _fmt(m.lamax_dbfs, 1), _fmt(m.dynamic_range_db, 1)]) + " |")
    L.append("")
    if d.claim_results:
        L.extend(["## 주장 대조", "", "| 파일 | 항목 | 설계 | 실측 | 판정 |", "|---|---|---|---|---|"])
        for r in d.claim_results:
            L.append("| {} | {} | {} | {} | {} |".format(
                md_cell(r.file), md_cell(r.key), r.claimed_text(),
                r.measured_text(), r.verdict))
        L.append("")
    L.extend(["## 검사하지 않은 축", ""])
    for r in refs.unmeasured_axes():
        L.append("- **{}** — 참조 {} · 출처 {} · {}".format(
            r.axis, r.value_text, r.citation, refs.DISCLAIMER))
    L.append("")
    return "\n".join(L)


def _matrix_csv(d: ReportData) -> Tuple[List[str], List[List[object]]]:
    mx = d.matrix
    if mx is None:
        return ["안내"], [["행렬을 만들 수 없습니다 (비교 대상이 2개 미만)"]]
    header = ["", ] + list(mx.labels)
    rows: List[List[object]] = []
    for a in mx.labels:
        row: List[object] = [a]
        for b in mx.labels:
            if a == b:
                row.append("")
                continue
            key = (a, b) if (a, b) in mx.diffs else (b, a)
            v = mx.diffs.get(key)
            row.append("" if v is None else round(v, 2))
        rows.append(row)
    rows.append([])
    rows.append(["대표 LUFS"])
    for a in mx.labels:
        rows.append([a, _num(mx.values.get(a), 2)])
    return header, rows
