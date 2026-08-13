"""GRIM/GRIMMER 를 원고 문장에 붙이는 계층.

**척도를 추측하지 않는다**는 규칙이 여기서 구현된다. 문장에 등록된 척도 이름이
명시적으로 있고(ISI, PSQI, …), 그 문장 안에 평균과 N 이 둘 다 있을 때만 검사한다.
"평균 연령 42.7세 (N = 23)" 에는 절대 발동하지 않는다 — 연령은 등록된 척도가
아니기 때문이다.

이름은 알지만 구조(문항 수·범위)를 모르는 척도(단어인지도 등)를 만나면
"지정하면 검사할 수 있습니다"를 **정보** 등급으로 남긴다. 조용히 넘어가면
사용자는 검사됐다고 착각한다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .docio import Line
from .grim import grim_check, grim_has_power, grimmer_check
from .model import SKIP_REASONS, Claim, Finding
from .options import Options
from .rounding import Reported, fmt, parse_number
from .textutil import normalize, sentences, snippet

__all__ = ["check_grim"]

_NUM = r"[-+]?\d{1,6}(?:\.\d{1,4})?"
_INT = r"\d{1,6}"

# 평균을 알아볼 수 있는 표기들 (SD 가 따라오는 형태를 우선한다)
_MEAN_SD = re.compile(rf"(?P<mean>{_NUM})\s*(?:±|\+/-|\+-)\s*(?P<sd>{_NUM})")
# `(SD 3.4)` 뿐 아니라 실제로 훨씬 흔한 `(SD 3.4, N = 23)` 도 받는다.
# 예전에는 SD 뒤에 곧바로 `)` 를 요구해서, 이 형태가 **후보로도 잡히지 않았다** —
# 커버리지 자백에 "건너뜀"으로도 안 나오는, 가장 나쁜 종류의 누락이었다.
_MEAN_SD_PAREN = re.compile(
    rf"(?P<mean>{_NUM})\s*[\(\[]\s*(?:SD|표준편차)\s*[=:]?\s*(?P<sd>{_NUM})"
    rf"\s*(?:[,;][^)\]]{{0,40}})?[\)\]]",
    re.IGNORECASE,
)
# `평균 14.37` / `mean 14.37` / `M = 14.37`.
# `M` 에 단어 경계가 없으면 `SEM 1.37` 의 M 이 걸려 헛된 GRIM 위반이 난다.
_MEAN_MARKED = re.compile(
    rf"(?:평균|\bmean\b|\bM\b)\s*(?:값)?\s*(?:은|는|이|가)?\s*[=:]?\s*(?P<mean>{_NUM})",
    re.IGNORECASE,
)
# 영어 원고에서 압도적으로 흔한 어순: `the ISI mean was 14.37`,
# `the mean ISI at week 8 was 14.37`, `the ISI averaged 14.37`.
# 숫자가 `mean` 바로 뒤에 붙기를 요구하면 이것들이 **후보로도 안 잡힌다** —
# 커버리지 자백에 '건너뜀'으로도 안 나오는, 가장 나쁜 종류의 누락이다.
# 사이에 `at week 8` 처럼 숫자가 낄 수 있으므로 숫자를 막지 않는다. 대신
# `was/were` 를 앵커로 삼아 그 **직후** 수만 평균으로 읽는다.
_MEAN_MARKED_EN = re.compile(
    rf"\b(?:mean|average)\b[^\n]{{0,40}}?\bw(?:as|ere)\s*[=:]?\s*(?P<mean>{_NUM})",
    re.IGNORECASE,
)
_MEAN_AVERAGED = re.compile(
    rf"\baveraged\s*[=:]?\s*(?P<mean>{_NUM})", re.IGNORECASE
)

# GRIM 을 **꺼야 하는** 평균들. 보정평균·추정 주변평균·대체(imputation) 후 평균·
# 기하/절사/가중 평균은 개인값이 정수가 아니거나 정수 합의 산술평균이 아니므로
# GRIM 의 전제가 무너진다. SERENE 의 1차 지표(ISI)는 MMRM 보정평균으로 보고될
# 가능성이 높다 — 여기서 오탐이 나면 이 툴은 두 번 다시 열리지 않는다.
_DERIVED_MEAN = re.compile(
    r"(조정\s*평균|보정(된|한)?\s*평균|추정\s*주변\s*평균|최소제곱\s*평균|LS\s*-?\s*mean"
    r"|least[\s-]*squares?\s+mean|estimated\s+marginal\s+mean|\bEMM\b|\bMMRM\b"
    r"|공분산분석|\bANCOVA\b|다중\s*대입|대치|대체값|\bimputat|\bimputed\b|\bLOCF\b|\bBOCF\b"
    r"|기하\s*평균|geometric\s+mean|절사\s*평균|trimmed\s+mean"
    r"|가중\s*평균|weighted\s+mean|예측\s*평균|predicted\s+mean|adjusted\s+mean)",
    re.IGNORECASE,
)
_N_PATTERNS = (
    re.compile(rf"\b[nN]\s*=\s*(?P<v>{_INT})"),
    re.compile(rf"(?P<v>{_INT})\s*명"),
)


def _find_n(text: str) -> Optional[int]:
    """문장에서 표본수를 하나만 확실하게 찾을 수 있으면 그 값."""
    values = set()
    for pattern in _N_PATTERNS:
        for m in pattern.finditer(text):
            try:
                v = int(m.group("v"))
            except ValueError:  # pragma: no cover
                continue
            if 1 < v <= 1_000_000:
                values.add(v)
    if len(values) == 1:
        return values.pop()
    return None


def _find_mean(text: str, lo: float, hi: float) -> Optional[Tuple[Reported, Optional[Reported],
                                                                 Tuple[int, int]]]:
    """(평균, SD 또는 None, 위치). 척도 범위 안에 드는 것만 인정한다."""
    for pattern, has_sd in ((_MEAN_SD, True), (_MEAN_SD_PAREN, True), (_MEAN_MARKED, False),
                            (_MEAN_MARKED_EN, False), (_MEAN_AVERAGED, False)):
        for m in pattern.finditer(text):
            mean = parse_number(m.group("mean"))
            if mean is None or not (lo - 1e-9 <= mean.value <= hi + 1e-9):
                continue
            sd = parse_number(m.group("sd")) if has_sd else None
            if sd is not None and sd.value < 0:
                sd = None
            return mean, sd, m.span()
    return None


# 표 한 행에서 쓰는 형태: `14.37 (N = 23)` · `14.37 ± 3.21 (n = 23)`
_CELL_MEAN = re.compile(
    rf"^\s*(?P<mean>{_NUM})\s*(?:(?:±|\+/-)\s*(?P<sd>{_NUM})\s*)?"
    rf"[\(\[]\s*[nN]\s*=\s*(?P<n>{_INT})\s*[\)\]]"
)


def _table_row_grim(ln: Line, text: str, opts: Options):
    """표 한 행의 GRIM.

    행을 셀 단위로 자르면 첫 칸의 척도 이름("ISI 평균")과 다음 칸의 값
    ("14.37 (N = 23)")이 갈라져 표 안의 GRIM 을 전부 놓친다. 셀까지 읽어 놓고
    검사를 못 하면 커버리지 자백이 거짓말이 된다.

    다만 표는 머리행의 N 이 어느 열에 걸리는지 알 수 없으므로, **셀 안에 N 이
    함께 적힌 경우에만** 검사한다(`14.37 (N = 23)`). 추측하지 않는다.
    """
    match = opts.registry.find(text)
    if match is None:
        return []
    scale = match[0]
    results = []
    for cell in text.split("|"):
        m = _CELL_MEAN.match(cell.strip())
        if not m:
            continue
        mean = parse_number(m.group("mean"))
        sd = parse_number(m.group("sd")) if m.group("sd") else None
        if mean is None or not (scale.lo - 1e-9 <= mean.value <= scale.hi + 1e-9):
            continue
        try:
            n = int(m.group("n"))
        except ValueError:  # pragma: no cover
            continue
        if not (1 < n <= 1_000_000):
            continue
        quote = snippet(text, 0, min(len(text), 90)) if opts.quote else ""
        results.extend(_evaluate_grim(ln, scale, mean, sd, n, quote, opts,
                                      derived_cue=_derived_cue(text)))
    return results


def _item_mean_possible(mean: Reported, n: int, scale, k: float) -> bool:
    """이 값이 '문항 평균' 으로 읽으면 도달 가능한가.

    문항평균의 입도는 1/(문항수 × N) 이다. 총점 범위의 위쪽에 있는 값은
    문항평균일 수 없으므로 이 판정을 시도조차 하지 않는다.
    """
    if not scale.integer_sum or scale.items <= 1:
        return False
    item_hi = scale.lo + (scale.hi - scale.lo) / scale.items
    if mean.value > item_hi + 1e-9:
        return False
    return grim_check(mean, n * scale.items, 1.0,
                      scale.lo, item_hi, k).consistent


def _derived_cue(text: str) -> Optional[str]:
    """보정·대체된 평균임을 알리는 단서가 있으면 그 단서."""
    m = _DERIVED_MEAN.search(text)
    return m.group(0).strip() if m else None


def _evaluate_grim(ln: Line, scale, mean: Reported, sd: Optional[Reported], n: int,
                   quote: str, opts: Options, derived_cue: Optional[str] = None):
    """(척도, 평균, SD, N) 한 조합에 GRIM + GRIMMER 를 적용한다.

    ``derived_cue`` 가 있으면 그 평균은 원자료의 산술평균이 아니므로(보정평균·
    대체 후 평균 등) GRIM 을 돌리지 않고 건너뛴 사실만 남긴다.
    """
    out = []
    claim = Claim(ln.no, ln.section, "grim", f"GRIM ({scale.name})", quote,
                  reported=f"평균 {mean.raw}" + (f" ± {sd.raw}" if sd else "")
                           + f", N = {n}")
    if derived_cue:
        claim.skip_reason = SKIP_REASONS["derived_mean"]
        claim.verdict = "건너뜀"
        claim.note = f"'{derived_cue}' 가 있어 원자료의 산술평균이 아님"
        return [(claim, None)]
    if not grim_has_power(mean, n, scale.unit, opts.k):
        claim.skip_reason = SKIP_REASONS["no_power"]
        claim.verdict = "건너뜀"
        claim.note = f"N = {n} 에서는 소수 {mean.decimals}자리로 가려낼 수 없음"
        return [(claim, None)]
    # 총점이 아니라 **문항 평균**으로 보고했을 수 있다. `PSS 평균 2.37 (N = 23)`
    # 은 총점(0–40)으로 읽으면 GRIM 위반이지만 문항평균(0–4)으로 읽으면
    # 545/230 = 2.3696 으로 가능하다. 0 에서 시작하는 척도는 문항평균 범위를
    # 통째로 포함하므로 범위 가드가 걸러 주지 않는다. 두 해석 중 **하나라도**
    # 가능하면 지적하지 않는다.
    if _item_mean_possible(mean, n, scale, opts.k):
        claim.skip_reason = SKIP_REASONS["ambiguous"]
        claim.verdict = "건너뜀"
        claim.note = "총점 평균인지 문항 평균인지 알 수 없음(문항 평균으로는 가능)"
        return [(claim, None)]
    result = grim_check(mean, n, scale.unit, scale.lo, scale.hi, opts.k)
    claim.checked = True
    if not result.consistent:
        claim.verdict = "불일치"
        claim.recomputed = f"가장 가까운 가능한 평균 {fmt(result.nearest or 0.0, 4)}"
        # 인쇄하는 등식이 실제 계산과 달라서는 안 된다. percent-of-count 척도는
        # 개인 점수의 증분이 1 이 아니므로(50문항이면 2%p) `평균 × N` 이 아니라
        # `평균 × N ÷ 증분` 이 정수여야 한다.
        product = mean.value * n / scale.unit
        if abs(scale.unit - 1.0) < 1e-12:
            form_ko = f"(정수)/{n}"
            form_en = f"(integer)/{n}"
            equation = f"{mean.raw} × {n} = {fmt(product, 4)}"
            equation_en = equation
            kind_en = "an integer sum score"
        else:
            form_ko = f"(정수)×{scale.unit:g}/{n}"
            form_en = f"(integer)×{scale.unit:g}/{n}"
            equation = f"{mean.raw} × {n} ÷ {scale.unit:g} = {fmt(product, 4)}"
            equation_en = equation
            kind_en = (f"a percent-of-{scale.items} score "
                       f"(individual scores step by {scale.unit:g})")
        return [(claim, Finding(
            "치명", ln.no, ln.section, f"GRIM ({scale.name})", quote,
            f"평균 {mean.raw} (N = {n})", f"{fmt(result.nearest or 0.0, 4)}",
            f"GRIM 위반. {scale.name} 은 {_unit_text(scale)} 이므로 N = {n} 명의 평균은"
            f" {form_ko} 꼴이어야 합니다. {equation} 로"
            f" 정수가 아니며, 반올림·버림·올림 어느 관례로도 이 평균은 나올 수 없습니다."
            f" 가장 가까운 가능한 값은 {fmt(result.nearest or 0.0, 4)} 입니다.",
            message_en=(
                f"GRIM violation. {scale.name} is {kind_en}, so a mean over "
                f"N = {n} must be {form_en}. {equation_en} "
                f"is not an integer; the nearest attainable mean is "
                f"{fmt(result.nearest or 0.0, 4)}."
            ),
        ))]
    claim.verdict = "일치"
    claim.recomputed = f"가능 (합계 {fmt(result.numerator or 0.0, 2)})"
    out.append((claim, None))
    # GRIM 을 통과했으면 SD 까지 본다(정수 합 척도에서만).
    if sd is not None and scale.integer_sum and opts.strict_grimmer:
        ok, reason = grimmer_check(mean, sd, n, scale.lo, scale.hi, opts.k)
        sd_claim = Claim(ln.no, ln.section, "grim", f"GRIMMER ({scale.name})", quote,
                         checked=True,
                         reported=f"평균 {mean.raw} ± {sd.raw}, N = {n}",
                         verdict="일치" if ok else "불일치")
        if ok:
            out.append((sd_claim, None))
        else:
            out.append((sd_claim, Finding(
                "경고", ln.no, ln.section, f"GRIMMER ({scale.name})", quote,
                f"평균 {mean.raw} ± {sd.raw} (N = {n})", "-",
                f"GRIMMER 위반 가능. 정수 점수 {n} 개로는 이 (평균, SD) 조합을"
                f" 만들 수 없습니다 — {reason}."
                " SD 가 표본(n−1)/모집단(n) 어느 정의든 결과는 같습니다.",
                downgraded="SD 정의·중도절단 등 예외가 있을 수 있어 경고",
                message_en=(
                    f"Possible GRIMMER violation: no set of {n} integer scores can "
                    f"produce this (mean, SD) pair — {reason}."
                ),
            )))
    return out


def check_grim(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    out: List[Tuple[Claim, Optional[Finding]]] = []
    seen_hints = set()
    for ln in lines:
        if ln.section == "References":
            continue
        text = normalize(ln.text)
        if not text.strip():
            continue
        if ln.kind == "table":
            out.extend(_table_row_grim(ln, text, opts))
            continue
        units = sentences(text)
        single_sentence = len(units) == 1
        for start, _end, sentence in units:
            match = opts.registry.find(sentence)
            if match is None:
                _maybe_hint(ln, text, sentence, opts, seen_hints, out)
                continue
            scale, _s, _e = match
            found = _find_mean(sentence, scale.lo, scale.hi)
            if found is None:
                continue
            mean, sd, span = found
            span = (start + span[0], start + span[1])
            quote = snippet(text, span[0], span[1]) if opts.quote else ""
            # 줄 전체에서 N 을 끌어오는 것은 **그 줄에 문장이 하나뿐일 때만** 허용한다.
            # 그러지 않으면 "ISI 평균은 14.25 였다. 이 연구에는 총 46명이 참여하였다."
            # 에서 전체 N 46 을 하위군 평균에 붙여 헛된 GRIM 위반을 만든다.
            n = _find_n(sentence)
            if n is None and single_sentence:
                n = _find_n(text)
            # 보정·대체 단서는 **문단(줄) 전체**에서 찾는다. 같은 문단에서 MMRM 이나
            # 다중대입을 말하면서 평균을 보고했다면 그 평균이 원자료의 산술평균이라고
            # 단정할 수 없다. 이 툴의 다른 애매한 자리와 같은 선택 — 지적하는 대신
            # 침묵하고, 침묵했다는 사실을 커버리지 자백에 남긴다.
            derived = _derived_cue(sentence) or _derived_cue(text)
            if n is None:
                out.append((Claim(
                    ln.no, ln.section, "grim", f"GRIM ({scale.name})", quote,
                    reported=f"평균 {mean.raw}" + (f" ± {sd.raw}" if sd else ""),
                    skip_reason=SKIP_REASONS["no_n"], verdict="건너뜀",
                    note="같은 문장에서 표본수를 특정할 수 없음"), None))
                continue
            out.extend(_evaluate_grim(ln, scale, mean, sd, n, quote, opts,
                                      derived_cue=derived))
    return out


def _unit_text(scale) -> str:
    if scale.integer_sum:
        return f"{scale.items}문항 정수 합({scale.lo:g}–{scale.hi:g})"
    return f"{scale.items}개 항목 대비 백분율(증분 {scale.unit:g})"


_HAS_MEAN_HINT = re.compile(r"(±|평균|\bmean\b|\bSD\b)", re.IGNORECASE)


def _maybe_hint(ln: Line, text: str, sentence: str, opts: Options, seen, out) -> None:
    """구조를 모르는 '알려진 척도 이름'을 안내한다(정보 등급, 이름당 1회)."""
    hit = opts.registry.find_unconfigured(sentence)
    if hit is None:
        return
    name, howto = hit
    if not _HAS_MEAN_HINT.search(sentence):
        return
    if name in seen:
        return
    seen.add(name)
    quote = snippet(text, 0, min(len(text), 60)) if opts.quote else ""
    claim = Claim(ln.no, ln.section, "grim", f"GRIM ({name})", quote,
                  skip_reason=SKIP_REASONS["unknown_scale"], verdict="건너뜀",
                  note="척도 구조를 알 수 없어 GRIM 미실행")
    out.append((claim, Finding(
        "정보", ln.no, ln.section, f"GRIM ({name})", quote, "-", "-",
        f"척도 '{name}' 은 정수 척도 여부·문항 수를 알 수 없어 GRIM 을 건너뛰었습니다."
        f" {howto}",
        verdict="건너뜀",
        message_en=(
            f"Scale '{name}' was skipped: its granularity (integer? how many items?) is "
            f"unknown, so GRIM cannot be applied. Declare it with --scale to enable it."
        ),
    )))
