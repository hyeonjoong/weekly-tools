"""세트 대조 — **파일들 사이**만 봅니다.

이 툴의 심장입니다. 파일 하나하나에 점수를 매기는 일은 DEBUSSY 와
`bell_acoustic_qc.py` 가 합니다. stimaudit 이 답하는 질문은 하나입니다:

    "이 소리들이 서로 비교 가능한 세트인가?"

왜 중요한가: RESONATE 는 `S1_SO-CLAS` / `S2_spindle-target` / `S3_pink` /
`S6_breath-pacing` 4조건 비교이고 `S3_pink` 가 대조군입니다. S1 이 S3 보다
음량이 크면 **"SO-CLAS 가 효과 있다"는 결론은 "소리가 더 컸다"는 결론과
구분되지 않습니다.** 리뷰어가 반드시 묻고, 데이터를 다 모은 뒤에는 고칠
방법이 없습니다.

판정 규칙은 **좁게** 잡았습니다. 애매하면 치명이 아니라 경고, 경고가 애매하면
정보로 내립니다 — 매번 우는 체커는 첫 번째 실행 이후로 아무도 열지 않습니다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import findings as F
from .analyze import DC_WARN_DBFS, FileMetrics
from .claims import ClaimResult
from .design import Design

#: 좌우 RMS 차 경고 문턱 (dB).
LR_IMBALANCE_DB = 1.0
#: 트루피크 여유 경고 문턱 (dBTP).
TRUE_PEAK_CEILING_DBTP = -1.0
#: 세트 내 길이 불일치 경고 문턱 (최장/최단 비).
DURATION_RATIO_WARN = 1.05
#: 시작/끝 클릭 위험으로 보는 상승·하강 시간 (ms).
EDGE_CLICK_MS = 5.0
#: 조건 내 음량 산포 경고 문턱 (LU).
WITHIN_CONDITION_SPREAD_LU = 2.0


@dataclass
class LevelMatrix:
    """LUFS 차이 행렬. 조건이 있으면 조건 간, 없으면 파일 간."""

    labels: List[str]
    values: Dict[str, Optional[float]]          # 라벨 → 대표 LUFS
    diffs: Dict[Tuple[str, str], Optional[float]]
    is_condition_level: bool
    tol: float


@dataclass
class SetResult:
    findings: List[F.Finding] = field(default_factory=list)
    matrix: Optional[LevelMatrix] = None
    condition_of: Dict[str, str] = field(default_factory=dict)


def _mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals)


def _db_text(value: Optional[float]) -> str:
    """0.0 dBFS 는 참인 값입니다 — `value or nan` 같은 falsy 처리는 버그입니다.

    실제로 클리핑된 파일(표본 피크 정확히 0.00 dBFS)에서 "nan" 이 인쇄됐습니다.
    """
    return "—" if value is None else "{:.2f}".format(value)


def build_matrix(metrics: Dict[str, FileMetrics], design: Optional[Design],
                 tol: float) -> LevelMatrix:
    """LUFS 차이 행렬을 만듭니다.

    조건별 대표값은 **구성원 LUFS 의 산술 평균(LU 단위)** 입니다. 에너지 평균이
    아니라 LU 평균을 쓰는 이유: 조건 매칭에서 중요한 것은 체감 음량의 중심이지
    합산 에너지가 아니기 때문입니다. 조건에 파일이 1개면 그 값 그대로입니다.
    """
    if design and design.conditions:
        labels = list(design.conditions.keys())
        values: Dict[str, Optional[float]] = {}
        for cond in labels:
            got = [metrics[f].lufs_i for f in design.conditions[cond]
                   if f in metrics and metrics[f].lufs_i is not None]
            values[cond] = _mean(got) if got else None
        is_cond = True
    else:
        labels = sorted(metrics)
        values = {n: metrics[n].lufs_i for n in labels}
        is_cond = False
    diffs: Dict[Tuple[str, str], Optional[float]] = {}
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            va, vb = values[a], values[b]
            diffs[(a, b)] = abs(va - vb) if va is not None and vb is not None else None
    return LevelMatrix(labels=labels, values=values, diffs=diffs,
                       is_condition_level=is_cond, tol=tol)


def _evidence_pair(metrics: Dict[str, FileMetrics], design: Design,
                   a: str, b: str, va: Optional[float] = None,
                   vb: Optional[float] = None) -> str:
    """조건 대표값과, 차이를 가장 크게 벌리는 파일 쌍을 함께 증거로 붙입니다.

    조건 대표값은 구성원 LUFS 의 **산술 평균**이고 헤드라인의 Δ 는 그 평균의
    차이입니다. 그런데 전에는 증거 줄에 '가장 벌어진 파일 쌍'만 인쇄해서,
    읽는 사람이 그 두 값을 빼면 헤드라인과 다른 숫자가 나왔습니다
    (−14.6 − (−23.0) = 8.4 인데 헤드라인은 5.7). 리포트가 자기 산수를
    설명하지 못하면 사람이 툴을 의심합니다 — 둘 다 인쇄합니다.
    (적대적 검토 라운드 1, 문서 정직성 비평 P3)
    """
    lead = ""
    if va is not None and vb is not None:
        lead = "조건 평균 {} {:.1f} / {} {:.1f} LUFS".format(a, va, b, vb)
    best = None
    for fa in design.conditions.get(a, []):
        for fb in design.conditions.get(b, []):
            ma, mb = metrics.get(fa), metrics.get(fb)
            if not ma or not mb or ma.lufs_i is None or mb.lufs_i is None:
                continue
            d = abs(ma.lufs_i - mb.lufs_i)
            if best is None or d > best[0]:
                best = (d, fa, ma.lufs_i, fb, mb.lufs_i)
    if best is None:
        return lead
    pair = "가장 벌어진 쌍 {} {:.1f} ↔ {} {:.1f} LUFS".format(
        best[1], best[2], best[3], best[4])
    return (lead + " · " + pair) if lead else pair


def check_levels(metrics: Dict[str, FileMetrics], design: Optional[Design],
                 matrix: LevelMatrix, tol: float, crit: float) -> List[F.Finding]:
    """조건 간 음량 매칭 — 이 툴의 핵심 판정."""
    out: List[F.Finding] = []
    if not (design and design.conditions):
        return out          # 조건이 없으면 판정하지 않고 행렬만 냅니다
    if len(matrix.labels) < 2:
        return out
    # 라우드니스를 잴 수 없는 조건이 하나라도 있으면 **판정을 건너뛴 사실 자체를
    # 말합니다.** 전에는 `d is None` 을 "허용 안" 과 똑같이 조용히 넘겨서,
    # 400 ms 미만 자극(게이팅 블록이 하나도 안 나옴)이나 측정 실패가 섞이면
    # 38 dB 차이가 나는 세트가 "치명 0건"으로 통과했습니다.
    # (적대적 검토 라운드 1, 엣지케이스 파괴자 발견 1)
    for label in matrix.labels:
        if matrix.values.get(label) is None:
            members = design.conditions.get(label, [])
            why = [f for f in members
                   if f in metrics and metrics[f].lufs_i is None]
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_LEVEL_UNDECIDABLE, subject=label,
                detail="이 조건의 통합 라우드니스를 잴 수 없어 음량 대조에서 뺐습니다",
                measured=("측정 불가 파일: " + " · ".join(sorted(why))) if why
                         else "조건에 잴 수 있는 파일이 없습니다",
                reference="BS.1770 게이팅 블록(400 ms)이 하나도 나오지 않으면 "
                          "통합 라우드니스가 정의되지 않습니다",
                condition=label,
                consequence="이 조건이 낀 쌍은 \"치명 0건\"의 범위 밖입니다 — "
                            "LAeq 표를 직접 보십시오."))
    for (a, b), d in sorted(matrix.diffs.items()):
        if d is None or d <= tol:
            continue
        sev = F.CRITICAL if d > crit else F.WARNING
        # 얼마나 어긋났는지만 말하는 것으로는 한 걸음이 남습니다 — 사운드 담당자에게
        # 보낼 값은 "몇 dB 올려/내려 달라"입니다. 그 뺄셈은 이미 여기 있는 값으로
        # 끝나므로, **파일을 만들어 주지 않는다**는 경계는 지키면서 그 한 걸음을
        # 없앱니다. (보정본을 만들어 주면 사람이 결과를 안 보고 수정만 돌립니다.)
        va, vb = matrix.values.get(a), matrix.values.get(b)
        advice = ""
        if va is not None and vb is not None:
            quiet, loud = (a, b) if va < vb else (b, a)
            advice = ("{} 를 {:+.1f} dB 하면 {} 와 맞습니다 "
                      "(이 툴은 파일을 만들지 않습니다).".format(quiet, d, loud))
        out.append(F.Finding(
            severity=sev,
            kind=F.KIND_LEVEL_MISMATCH,
            subject="{} ↔ {}".format(a, b),
            detail="조건 간 통합 라우드니스 차이 {:.1f} LU".format(d),
            measured=_evidence_pair(metrics, design, a, b, va, vb),
            reference="허용 {:.1f} LU · 치명 {:.1f} LU 초과".format(tol, crit),
            action=advice,
            consequence=("이 대조는 \"효과\"와 \"소리가 더 컸다\"를 구분하지 못합니다."
                         if sev == F.CRITICAL else
                         "차이가 작지만 조건 매칭을 다시 확인하십시오."),
        ))
    # 조건 안에서의 산포도 봅니다 — 같은 조건 파일끼리 3 LU 벌어져 있으면
    # 조건 평균이 무의미해집니다.
    for cond, files in sorted(design.conditions.items()):
        vals = [(f, metrics[f].lufs_i) for f in files
                if f in metrics and metrics[f].lufs_i is not None]
        if len(vals) < 2:
            continue
        lo = min(vals, key=lambda kv: kv[1])
        hi = max(vals, key=lambda kv: kv[1])
        spread = hi[1] - lo[1]
        if spread > WITHIN_CONDITION_SPREAD_LU:
            out.append(F.Finding(
                severity=F.WARNING,
                kind=F.KIND_LEVEL_SPREAD,
                subject=cond,
                detail="같은 조건 안에서 음량이 {:.1f} LU 벌어져 있습니다".format(spread),
                measured="{} {:.1f} LUFS ↔ {} {:.1f} LUFS".format(lo[0], lo[1], hi[0], hi[1]),
                reference="조건 내 허용 {:.1f} LU".format(WITHIN_CONDITION_SPREAD_LU),
                condition=cond,
                consequence="조건 대표값(평균)이 구성원을 대표하지 못합니다.",
            ))
    return out


def check_hygiene(metrics: Dict[str, FileMetrics], design: Optional[Design]) -> List[F.Finding]:
    """파일 위생 — 클리핑·죽은 파일·DC·좌우·트루피크·클릭."""
    out: List[F.Finding] = []
    for name in sorted(metrics):
        m = metrics[name]
        cond = design.condition_of(name) if design else None
        cond = cond or ""
        if m.dead_reason:
            out.append(F.Finding(
                severity=F.CRITICAL, kind=F.KIND_DEAD, subject=name,
                detail=m.dead_reason, measured="길이 {:.1f}초".format(m.duration_s),
                reference="소리가 들어 있어야 합니다", condition=cond,
                consequence="이 파일은 자극으로 전달되지 않습니다."))
            continue
        if m.clip_run_count > 0:
            first = m.clip_runs[0] if m.clip_runs else None
            where = ("첫 구간 {:.2f}초 (ch{}) · {}샘플".format(
                first.start_s, first.channel + 1, first.length_samples) if first else "")
            out.append(F.Finding(
                severity=F.CRITICAL, kind=F.KIND_CLIPPING, subject=name,
                detail="클리핑 구간 {}곳 · 총 {}샘플".format(m.clip_run_count, m.clip_sample_count),
                measured=where,
                reference="연속 3샘플 이상 |x| ≥ −0.1 dBFS", condition=cond,
                consequence="파형이 잘려 왜곡이 들어갑니다 — 자극 자체가 설계와 다릅니다."))
        # 완전히 죽은 채널 — 좌우 차이 검사가 **원리적으로 성립하지 않는** 경우.
        # `dbfs(0)` 은 None 이라, 한쪽 채널이 전부 0 이면 아래 좌우 검사가 통째로
        # 건너뛰어져 "좌우 균형 검사함 · 경고 0건"이 나왔습니다. 진폭이 아주 작지만
        # 0 은 아닌 채널(83 dB 차)은 잡히는데 **가장 나쁜 경우만** 빠져나갔습니다.
        # (적대적 검토 라운드 1, 엣지케이스 파괴자 발견 3)
        if m.dead_reason is None and len(m.rms_dbfs) > 1:
            silent = [i + 1 for i, v in enumerate(m.rms_dbfs) if v is None]
            if silent and len(silent) < len(m.rms_dbfs):
                out.append(F.Finding(
                    severity=F.CRITICAL, kind=F.KIND_DEAD, subject=name,
                    detail="채널 {} 이(가) 전 구간 무음입니다 (표본이 전부 0)".format(
                        " · ".join("ch{}".format(i) for i in silent)),
                    measured="채널별 RMS: " + " / ".join(
                        _db_text(v) + " dBFS" for v in m.rms_dbfs),
                    reference="모든 채널에 신호가 있어야 좌우 비교가 성립합니다",
                    condition=cond,
                    consequence="한쪽 귀에 아무것도 들리지 않습니다 — "
                                "좌우 균형은 잴 수조차 없습니다."))
        if m.lr_rms_diff_db is not None and abs(m.lr_rms_diff_db) > LR_IMBALANCE_DB:
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_LR_IMBALANCE, subject=name,
                detail="좌우 RMS 차 {:.1f} dB".format(abs(m.lr_rms_diff_db)),
                measured="L {} / R {} dBFS RMS".format(
                    _db_text(m.rms_dbfs[0]), _db_text(m.rms_dbfs[1])),
                reference="허용 {:.1f} dB".format(LR_IMBALANCE_DB), condition=cond,
                consequence="한쪽 귀에 더 크게 들립니다 — 양이 자극 설계라면 문제입니다."))
        for ci, dc_db in enumerate(m.dc_dbfs):
            if dc_db is not None and dc_db > DC_WARN_DBFS:
                out.append(F.Finding(
                    severity=F.WARNING, kind=F.KIND_DC_OFFSET, subject=name,
                    detail="ch{} DC 오프셋 {:.1f} dBFS".format(ci + 1, dc_db),
                    measured="평균 {:+.5f}".format(m.dc_linear[ci]),
                    reference="{:.0f} dBFS 이하".format(DC_WARN_DBFS), condition=cond,
                    consequence="스피커에 불필요한 직류가 걸리고 헤드룸을 잡아먹습니다."))
        if m.true_peak_dbfs is not None and m.true_peak_dbfs > TRUE_PEAK_CEILING_DBTP:
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_TRUE_PEAK, subject=name,
                detail="트루피크 {:.2f} dBTP (근사)".format(m.true_peak_dbfs),
                measured="표본 피크 {} dBFS".format(_db_text(m.sample_peak_dbfs)),
                reference="{:.1f} dBTP 이하 권장".format(TRUE_PEAK_CEILING_DBTP), condition=cond,
                consequence="재생 체인의 리샘플링·코덱에서 클리핑이 생길 수 있습니다."))
        if (m.lead_silence_ms <= 0.0 and m.onset_rise_ms is not None
                and m.onset_rise_ms < EDGE_CLICK_MS):
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_EDGE_CLICK, subject=name,
                detail="선두 무음 0 ms · 상승시간 {:.1f} ms".format(m.onset_rise_ms),
                measured="1 % → 50 % 진폭까지 {:.1f} ms".format(m.onset_rise_ms),
                reference="{:.0f} ms 미만이면 클릭으로 들릴 수 있음".format(EDGE_CLICK_MS),
                condition=cond,
                consequence="재생 시작에 딸깍 소리가 붙어 각성 자극이 됩니다."))
        if (m.tail_silence_ms <= 0.0 and m.offset_fall_ms is not None
                and m.offset_fall_ms < EDGE_CLICK_MS):
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_EDGE_CLICK, subject=name,
                detail="끝 무음 0 ms · 하강시간 {:.1f} ms".format(m.offset_fall_ms),
                measured="1 % → 50 % 진폭까지 {:.1f} ms".format(m.offset_fall_ms),
                reference="{:.0f} ms 미만이면 클릭으로 들릴 수 있음".format(EDGE_CLICK_MS),
                condition=cond,
                consequence="재생 끝에 딸깍 소리가 붙습니다."))
    return out


def check_format(metrics: Dict[str, FileMetrics]) -> List[F.Finding]:
    """세트 전체의 포맷·길이 일관성."""
    out: List[F.Finding] = []
    if len(metrics) < 2:
        return out
    keys = {}
    for name in sorted(metrics):
        keys.setdefault(metrics[name].info.format_key, []).append(name)
    if len(keys) > 1:
        groups = " · ".join(
            "{} ({})".format(metrics[names[0]].info.format_label(), len(names))
            for names in keys.values())
        out.append(F.Finding(
            severity=F.WARNING, kind=F.KIND_FORMAT_MISMATCH, subject="세트 전체",
            detail="파일 포맷이 {}종으로 섞여 있습니다".format(len(keys)),
            measured=groups,
            reference="같은 세트는 같은 샘플레이트·채널·비트depth 권장",
            consequence="재생 체인이 일부만 리샘플링하면 조건 간 미세한 차이가 생깁니다."))
    durs = [(n, metrics[n].duration_s) for n in sorted(metrics) if metrics[n].duration_s > 0]
    if len(durs) >= 2:
        lo = min(durs, key=lambda kv: kv[1])
        hi = max(durs, key=lambda kv: kv[1])
        if lo[1] > 0 and hi[1] / lo[1] > DURATION_RATIO_WARN:
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_DURATION_MISMATCH, subject="세트 전체",
                detail="파일 길이가 {:.1f}배 차이납니다".format(hi[1] / lo[1]),
                measured="{} {:.1f}초 ↔ {} {:.1f}초".format(lo[0], lo[1], hi[0], hi[1]),
                reference="{:.2f}배 이내 권장".format(DURATION_RATIO_WARN),
                consequence="노출 시간이 조건 간 달라지면 그 자체가 교란 변수입니다."))
    return out


def check_claims(results: Sequence[ClaimResult], design: Optional[Design]) -> List[F.Finding]:
    """주장 대조 결과를 판정으로 옮깁니다."""
    out: List[F.Finding] = []
    for r in results:
        cond = design.condition_of(r.file) if design else None
        if r.is_mismatch:
            out.append(F.Finding(
                severity=F.CRITICAL, kind=F.KIND_CLAIM_MISMATCH, subject=r.file,
                detail="설계 {}={} · 실측 {}".format(r.key, r.claimed_text(), r.measured_text()),
                measured=r.note or r.measured_text(),
                reference="허용오차 ±{:.4g} {}".format(r.tolerance, r.unit),
                condition=cond or "",
                consequence="설계서와 실제 자극이 다릅니다 — 논문에 적을 값이 틀립니다."))
        elif r.is_undecidable:
            out.append(F.Finding(
                severity=F.WARNING, kind=F.KIND_CLAIM_UNDECIDABLE, subject=r.file,
                detail="{} 를 이 파일에서 잴 수 없습니다".format(r.key),
                measured=r.note, reference="설계 {}={}".format(r.key, r.claimed_text()),
                condition=cond or "",
                consequence="주장이 사실인지 이 툴로는 확인되지 않았습니다."))
    return out


def run(metrics: Dict[str, FileMetrics], design: Optional[Design],
        claim_results: Sequence[ClaimResult], lufs_tol: float,
        lufs_crit: float) -> SetResult:
    """세트 판정 전체."""
    matrix = build_matrix(metrics, design, lufs_tol)
    out: List[F.Finding] = []
    out.extend(check_levels(metrics, design, matrix, lufs_tol, lufs_crit))
    out.extend(check_hygiene(metrics, design))
    out.extend(check_format(metrics))
    out.extend(check_claims(claim_results, design))
    out.sort(key=F.sort_key)
    cond_of = {}
    if design:
        for name in metrics:
            c = design.condition_of(name)
            if c:
                cond_of[name] = c
    return SetResult(findings=out, matrix=matrix, condition_of=cond_of)
