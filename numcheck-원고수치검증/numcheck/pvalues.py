"""p 재계산 — `t(45) = 2.31, p = .003` 에서 p 가 정말 .003 인가.

**statcheck 가 겪은 문제를 처음부터 피하도록 설계했다.** 보고된 통계량은 이미
반올림된 값이다. `t = 2.31` 은 실제로는 2.30 ~ 2.32 어딘가이고, 거기서 나오는
p 도 하나의 값이 아니라 구간이다. numcheck 는 그 **p 구간과 보고된 p 의
반올림 구간이 겹치지 않을 때만** 지적한다.

그래도 정당하게 다를 수 있는 경우가 있다. 단측검정, 다중비교 보정, 보정된
자유도(Greenhouse–Geisser), Welch 근사, 연속성 보정. 이런 단서가 같은 문장에
있으면 **치명을 경고로 자동 강등하고 강등했다고 적는다.** 단서가 없어도
단측으로 계산했을 때 값이 맞아떨어지면 역시 경고로 낮춘다.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .dists import p_from_statistic
from .docio import Line
from .mathx import NumericError
from .model import SKIP_REASONS, Claim, Finding
from .options import Options
from .rounding import Reported, effective_k, fmt, parse_number
from .textutil import (
    CORRECTION_HINTS,
    ONE_TAILED_HINTS,
    has_keyword,
    normalize,
    sentence_at,
    snippet,
)

__all__ = ["check_pvalues", "find_pvalues", "PValue", "Statistic"]

_NUM = r"\d*\.?\d+(?:\s*[×x*]\s*10\s*\^?\s*[-+]?\d+|[eE][-+]?\d+)?"
_DF = r"\d{1,6}(?:\.\d{1,3})?"

# ── p 값 ─────────────────────────────────────────────────────────────────────

_P_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[pP]|[Pp]-?\s?values?)\s*(?P<op>[<>=]=?|≤|≥)\s*(?P<val>" + _NUM + r")"
)


@dataclass
class PValue:
    span: Tuple[int, int]
    op: str            # '=' | '<' | '>' | '<=' | '>='
    value: Reported
    raw: str

    def interval(self, k: float = 1.0) -> Tuple[float, float]:
        """이 보고가 허용하는 참 p 의 구간(항상 [0, 1] 안으로 자른다)."""
        if self.op in ("<", "<="):
            lo, hi = 0.0, self.value.value
        elif self.op in (">", ">="):
            lo, hi = self.value.value, 1.0
        elif self.value.value == 0.0:
            # `p = 0` 은 ulp 가 1 이라 그냥 두면 구간이 [0, 1] 이 되어 **무엇과도
            # 맞는다.** p 가 정확히 0 일 수는 없으므로 `p < .001` 과 같게 다룬다
            # (`p = .000` 과 판정이 갈리지 않도록).
            lo, hi = 0.0, min(self.value.ulp, 0.001)
        else:
            lo, hi = self.value.interval(k)
        return max(0.0, lo), min(1.0, hi)

    @property
    def nominal(self) -> float:
        return self.value.value


def find_pvalues(text: str) -> List[PValue]:
    """줄에서 보고된 p 값을 모두 찾는다."""
    out: List[PValue] = []
    for m in _P_RE.finditer(text):
        parsed = parse_number(m.group("val"))
        if parsed is None or not (0.0 <= parsed.value <= 1.0):
            continue
        op = m.group("op")
        op = {"≤": "<=", "≥": ">=", "==": "="}.get(op, op)
        out.append(PValue(m.span(), op, parsed, m.group(0)))
    return out


# ── 검정통계량 ───────────────────────────────────────────────────────────────


@dataclass
class Statistic:
    span: Tuple[int, int]
    kind: str            # t | F | chi2 | r | z
    value: Reported
    df: Tuple[float, ...]
    df_exact: bool
    raw: str
    label: str
    # 산술적으로 존재할 수 없는 보고(|r| > 1, df ≤ 0, 음수 χ²/F)의 사유.
    # 예전에는 이런 통계량을 조용히 버렸고, 그러면 짝이 없어진 p 가
    # '검정통계량 없음' 으로 기록됐다 — 같은 줄에 통계량이 인쇄돼 있는데도.
    # 산술 오류를 잡는 툴이 산술적으로 불가능한 값을 못 본 척한 셈이다.
    invalid: str = ""


_STAT_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    ("t", re.compile(
        rf"(?<![A-Za-z0-9_])t\s*[\(\[_{{]\s*(?P<df>{_DF})\s*[\)\]}}]?\s*=\s*"
        rf"(?P<val>[-+]?{_NUM})")),
    ("F", re.compile(
        rf"(?<![A-Za-z0-9_])F\s*[\(\[]\s*(?P<df1>{_DF})\s*,\s*(?P<df2>{_DF})\s*[\)\]]\s*=\s*"
        rf"(?P<val>[-+]?{_NUM})")),
    ("chi2", re.compile(
        # 한국어 원고를 읽는 것이 이 툴이 statcheck 과 갈리는 지점인데, 정작
        # 한국어 표기 '카이제곱' 이 빠져 있었다.
        r"(?<![A-Za-z0-9_])(?:χ\s*2|χ²|chi\s*-?\s*squared?|Chi\s*-?\s*Squared?|X\s*2|x²"
        r"|카이\s*-?\s*제곱|카이스퀘어|[Cc]hi\s*2)\s*"
        rf"[\(\[]\s*(?P<df>{_DF})\s*(?:,\s*[nN]\s*=\s*\d{{1,7}}\s*)?[\)\]]\s*=\s*"
        rf"(?P<val>[-+]?{_NUM})")),
    ("r", re.compile(
        rf"(?<![A-Za-z0-9_])r\s*[\(\[_{{]\s*(?P<df>{_DF})\s*[\)\]}}]?\s*=\s*"
        rf"(?P<val>[-+]?{_NUM})")),
    ("z", re.compile(rf"(?<![A-Za-z0-9_])[zZ]\s*=\s*(?P<val>[-+]?{_NUM})")),
)

_KIND_LABEL = {"t": "t 검정", "F": "F 검정", "chi2": "χ² 검정", "r": "상관 r", "z": "z 검정"}


def _df_exact(text: str) -> bool:
    return "." not in text


def find_statistics(text: str) -> List[Statistic]:
    """줄에서 자유도가 붙은 검정통계량 보고를 모두 찾는다."""
    found: List[Statistic] = []
    taken: List[Tuple[int, int]] = []
    for kind, pattern in _STAT_PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < b and a < m.end() for a, b in taken):
                continue
            value = parse_number(m.group("val"))
            if value is None:
                continue
            if kind == "F":
                d1, d2 = m.group("df1"), m.group("df2")
                dfs = (float(d1), float(d2))
                exact = _df_exact(d1) and _df_exact(d2)
            elif kind == "z":
                dfs, exact = (), True
            else:
                d = m.group("df")
                dfs, exact = (float(d),), _df_exact(d)
            invalid = ""
            if any(d <= 0 for d in dfs):
                invalid = f"자유도가 {'0' if min(dfs) == 0 else '음수'} 입니다"
            elif kind == "r" and abs(value.value) > 1.0:
                invalid = f"상관계수는 -1 과 1 사이여야 하는데 {value.raw} 입니다"
            elif kind in ("chi2", "F") and value.value < 0:
                invalid = f"{_KIND_LABEL[kind]} 통계량은 음수일 수 없는데 {value.raw} 입니다"
            taken.append(m.span())
            found.append(Statistic(m.span(), kind, value, dfs, exact, m.group(0),
                                   _KIND_LABEL[kind], invalid))
    found.sort(key=lambda s: s.span[0])
    return found


# ── p 구간 계산 ──────────────────────────────────────────────────────────────


def _abs_range(lo: float, hi: float) -> Tuple[float, float]:
    """[lo, hi] 위에서 |x| 의 최소·최대."""
    if lo <= 0.0 <= hi:
        return 0.0, max(abs(lo), abs(hi))
    return min(abs(lo), abs(hi)), max(abs(lo), abs(hi))


def p_range(stat: Statistic, k: float = 1.0, tail: str = "two") -> Optional[Tuple[float, float]]:
    """보고된 통계량의 반올림 구간에서 나올 수 있는 p 의 [최소, 최대]."""
    # `r = 1` 처럼 정수로 적힌 값에 ±1 ulp 를 그대로 주면 구간이 [0, 2] 가 되고,
    # |r| 을 0.999999 로 잘라도 p 구간이 [0, 1] 이라 **무엇과도 맞는다**. 그러면
    # 108 자릿수 차이가 나는 값을 "일치" 라고 인쇄하게 된다.
    k_stat = effective_k(k, stat.value) if stat.kind == "r" else k
    s_lo, s_hi = stat.value.interval(k_stat)
    if stat.kind == "r":
        s_lo, s_hi = max(-1.0, s_lo), min(1.0, s_hi)
    if stat.kind in ("t", "z", "r"):
        a_lo, a_hi = _abs_range(s_lo, s_hi)
    else:
        a_lo, a_hi = max(0.0, s_lo), max(0.0, s_hi)
    if stat.kind == "r":
        a_lo = min(a_lo, 0.999999)
        a_hi = min(a_hi, 0.999999)
    # 자유도도 반올림돼 있을 수 있다(Welch, Greenhouse–Geisser).
    if stat.df_exact:
        df_sets = [stat.df]
    else:
        lows, highs = [], []
        for d in stat.df:
            r = parse_number(f"{d}")
            pad = k * (r.ulp if r else 0.1)
            lows.append(max(1e-6, d - pad))
            highs.append(d + pad)
        # F 는 df1 에 대해 증가, df2 에 대해 감소하는 구간이 있으므로 극값이
        # **대각 코너**(low, high) / (high, low) 에 놓인다. (low,low)/(high,high)
        # 두 점만 보면 p 구간을 과소산정해 정직한 보고에 경고를 낸다.
        df_sets = [tuple(c) for c in itertools.product(*zip(lows, highs))]
        df_sets.append(stat.df)
    values: List[float] = []
    for dfs in df_sets:
        for magnitude in (a_lo, a_hi):
            try:
                values.append(p_from_statistic(stat.kind, magnitude, dfs, tail))
            except (NumericError, ValueError, OverflowError, ZeroDivisionError):
                return None
    if not values or any(not math.isfinite(v) for v in values):
        return None
    return min(values), max(values)


def nominal_p(stat: Statistic, tail: str = "two") -> Optional[float]:
    try:
        value = abs(stat.value.value) if stat.kind in ("t", "z", "r") else stat.value.value
        if stat.kind == "r":
            value = min(value, 0.999999)
        return p_from_statistic(stat.kind, value, stat.df, tail)
    except (NumericError, ValueError, OverflowError, ZeroDivisionError):
        return None


def _describe(stat: Statistic) -> str:
    if stat.kind == "F":
        return f"F({stat.df[0]:g}, {stat.df[1]:g}) = {stat.value.raw}"
    if stat.kind == "z":
        return f"z = {stat.value.raw}"
    return f"{stat.kind if stat.kind != 'chi2' else 'χ²'}({stat.df[0]:g}) = {stat.value.raw}"


def _overlap(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    return a[0] <= b[1] + 1e-15 and b[0] <= a[1] + 1e-15


# ── 본 검사 ──────────────────────────────────────────────────────────────────


def check_pvalues(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    out: List[Tuple[Claim, Optional[Finding]]] = []
    for ln in lines:
        text = normalize(ln.text)
        if not text.strip():
            continue
        stats = find_statistics(text)
        ps = find_pvalues(text)
        if ln.section == "References":
            for st in stats:
                out.append((Claim(ln.no, ln.section, "statistic", "p 재계산",
                                  _q(opts, text, st.span),
                                  skip_reason=SKIP_REASONS["reference"], verdict="건너뜀"), None))
            continue
        used_p: List[int] = []
        for st in stats:
            quote = _q(opts, text, st.span)
            claim = Claim(ln.no, ln.section, "statistic", "p 재계산", quote,
                          reported=_describe(st))
            if st.invalid:
                claim.checked = True
                claim.verdict = "불일치"
                claim.recomputed = "-"
                claim.note = st.invalid
                # 짝이 되는 p 는 '검정통계량 없음' 이 아니라 이 지적에 딸린 것이므로
                # 여기서 소비해 둔다.
                paired = _pair_pvalue(st, ps, text, used_p)
                if paired is not None:
                    used_p.append(paired[0])
                out.append((claim, Finding(
                    "치명", ln.no, ln.section, "검정통계량", quote,
                    st.raw.strip(), "-",
                    f"{st.raw.strip()} — {st.invalid}. 이 값에서는 p 를 계산할 수 없습니다."
                    " 오탈자이거나 다른 값이 잘못 옮겨진 자리입니다.",
                    message_en=(f"{st.raw.strip()} is arithmetically impossible "
                                f"({st.invalid}); no p value can be derived from it."),
                )))
                continue
            match = _pair_pvalue(st, ps, text, used_p)
            if match is None:
                claim.skip_reason = SKIP_REASONS["no_pvalue"]
                claim.verdict = "건너뜀"
                claim.note = "같은 문장에 p 값이 없어 대조 불가"
                out.append((claim, None))
                continue
            idx, pv = match
            used_p.append(idx)
            out.append(_judge(ln, text, st, pv, claim, opts))
        # 통계량 없이 홀로 있는 p 값 — 재계산 불가(후보로만 기록)
        for i, pv in enumerate(ps):
            if i in used_p:
                continue
            out.append((Claim(ln.no, ln.section, "pvalue", "p 재계산",
                              _q(opts, text, pv.span), reported=pv.raw,
                              skip_reason=SKIP_REASONS["no_statistic"], verdict="건너뜀",
                              note="검정통계량과 자유도가 함께 적혀 있지 않음"), None))
    return out


def _pair_pvalue(st: Statistic, ps: List[PValue], text: str,
                 used: List[int]) -> Optional[Tuple[int, PValue]]:
    """통계량과 같은 문장 안의 p 값을 짝짓는다. 뒤에 오는 것을 우선한다."""
    s_start, s_end, _ = sentence_at(text, st.span[0])
    after = [(i, p) for i, p in enumerate(ps)
             if i not in used and s_start <= p.span[0] < s_end and p.span[0] >= st.span[1]]
    if after:
        return min(after, key=lambda item: item[1].span[0])
    before = [(i, p) for i, p in enumerate(ps)
              if i not in used and s_start <= p.span[0] < s_end]
    if before:
        return max(before, key=lambda item: item[1].span[0])
    return None


def _judge(ln: Line, text: str, st: Statistic, pv: PValue, claim: Claim,
           opts: Options) -> Tuple[Claim, Optional[Finding]]:
    rng = p_range(st, opts.k, "two")
    nominal = nominal_p(st, "two")
    if rng is None or nominal is None:
        claim.skip_reason = SKIP_REASONS["ambiguous"]
        claim.verdict = "건너뜀"
        claim.note = "통계량 또는 자유도가 계산 범위를 벗어남"
        return claim, None
    if rng[1] - rng[0] > 0.98:
        # 반올림 구간이 너무 넓어 어떤 p 와도 맞는다. 이때 "일치" 를 찍으면
        # 검사하지 않은 것을 검사했다고 말하는 셈이다.
        claim.skip_reason = SKIP_REASONS["no_power"]
        claim.verdict = "건너뜀"
        claim.note = "보고된 통계량의 자릿수가 낮아 p 구간이 [0, 1] 에 가까움"
        return claim, None
    claim.checked = True
    # F·χ² 의 p 는 상측 단측 꼬리다. 이걸 "양측"이라고 적으면 손으로 검산하는
    # 사용자가 툴을 불신한다.
    tail_ko, tail_en = _tail_label(st.kind)
    claim.reported = f"{_describe(st)}, {pv.raw}"
    claim.recomputed = f"p = {fmt(nominal, 6)} ({tail_ko})"
    claimed = pv.interval(opts.k)
    if _overlap(rng, claimed):
        claim.verdict = "일치"
        return claim, None

    quote = claim.quote
    sentence = sentence_at(text, st.span[0])[2]
    one_hint = has_keyword(sentence, ONE_TAILED_HINTS)
    corr_hint = has_keyword(sentence, CORRECTION_HINTS)
    one_rng = p_range(st, opts.k, "one")
    one_fits = one_rng is not None and _overlap(one_rng, claimed)

    claim.verdict = "불일치"
    level = "치명"
    downgraded = ""
    extra = ""
    extra_en = ""
    if one_hint or one_fits:
        level = "경고"
        one_nom = nominal_p(st, "one")
        downgraded = f"단측검정 가능성({one_hint or '단측으로 계산하면 값이 맞음'})"
        extra = (f" 단측으로 계산하면 p = {fmt(one_nom or 0.0, 6)} 로"
                 f"{' 보고값과 맞습니다' if one_fits else ' 됩니다'}.")
        extra_en = (f" One-tailed it would be p = {fmt(one_nom or 0.0, 6)}"
                    f"{', which matches the reported value' if one_fits else ''}.")
    elif corr_hint:
        level = "경고"
        downgraded = f"보정 단서 '{corr_hint}' 가 같은 문장에 있음"
        extra = (f" 같은 문장에 '{corr_hint}' 가 있어 보정된 자유도·p 를 쓴 것일 수 있습니다."
                 " 확인 요망.")
        extra_en = (f" '{corr_hint}' appears in the same sentence, so corrected df/p may "
                    "have been used — please check.")
    else:
        claimed_sig = _decision(pv, opts.alpha)
        flips = claimed_sig is not None and (nominal < opts.alpha) != claimed_sig
        ratio = _ratio(nominal, pv.nominal)
        if not flips and ratio < 2.0:
            level = "경고"
            downgraded = "차이가 근소하고 유의성 판정이 바뀌지 않음"
            extra = " 유의성 판정은 바뀌지 않습니다."
            extra_en = " The significance decision does not change."
        elif flips:
            extra = f" 유의성 판정(α = {opts.alpha:g})이 뒤집힙니다."
            extra_en = f" The significance decision at α = {opts.alpha:g} flips."
        else:
            extra = (f" 보고값과 {_ratio_text(ratio)} 차이."
                     " 유의성 판정은 바뀌지 않으나 값이 틀립니다.")
            extra_en = (f" Off by {_ratio_text_en(ratio)}; the significance decision is "
                        "unchanged but the value is wrong.")

    finding = Finding(
        level, ln.no, ln.section, "p 재계산", quote,
        f"{_describe(st)}, {pv.raw}", f"p = {fmt(nominal, 6)} ({tail_ko})",
        f"{_describe(st)} 에서 나오는 {tail_ko} p 는 {fmt(nominal, 6)}"
        f" (반올림 고려 시 {fmt(rng[0], 6)}–{fmt(rng[1], 6)}) 인데,"
        f" 원고에는 {pv.raw} 로 적혀 있습니다.{extra}",
        downgraded=downgraded,
        message_en=(
            f"{_describe(st)} gives a {tail_en} p of {fmt(nominal, 6)} "
            f"(rounding-tolerant range {fmt(rng[0], 6)}–{fmt(rng[1], 6)}), "
            f"but the manuscript reports {pv.raw}.{extra_en}"
        ),
    )
    if downgraded:
        claim.note = downgraded
    return claim, finding


def _tail_label(kind: str) -> Tuple[str, str]:
    """이 통계량의 p 가 어느 꼬리인가. F·χ² 는 관례상 항상 상측 단측이다."""
    if kind in ("t", "z", "r"):
        return "양측", "two-tailed"
    return "상측", "upper-tail"


def _decision(pv: PValue, alpha: float) -> Optional[bool]:
    """보고된 p 가 '유의하다'고 말하고 있는가. 말하지 않으면 None.

    `p > .001` 은 상한이 아니라 하한이므로 유의성에 대해 아무 주장도 하지
    않는다(.001 < α 인 경우). 이걸 "유의하지 않다"로 읽으면 진짜 p 가
    1e-10 일 때 "유의성 판정이 뒤집힙니다"라는 **틀린 사유**를 붙이게 된다.
    """
    if pv.op in ("<", "<="):
        return pv.nominal <= alpha
    if pv.op in (">", ">="):
        return False if pv.nominal >= alpha else None
    return pv.nominal < alpha


def _ratio_text(ratio: float) -> str:
    """배수 표기. 1e33 같은 숫자를 그대로 찍으면 리포트가 우스워진다."""
    if not math.isfinite(ratio):
        return "비교 불가할 만큼(보고값이 0)"
    if ratio >= 1000:
        return "1000배 이상"
    return f"{ratio:.1f}배"


def _ratio_text_en(ratio: float) -> str:
    if not math.isfinite(ratio):
        return "an unmeasurable factor (the reported p is zero)"
    if ratio >= 1000:
        return "a factor of 1000 or more"
    return f"a factor of {ratio:.1f}"


def _ratio(a: float, b: float) -> float:
    lo = min(abs(a), abs(b))
    hi = max(abs(a), abs(b))
    if lo <= 0:
        return float("inf") if hi > 0 else 1.0
    return hi / lo


def _q(opts: Options, text: str, span: Tuple[int, int]) -> str:
    if not opts.quote:
        return ""
    return snippet(text, span[0], span[1])
