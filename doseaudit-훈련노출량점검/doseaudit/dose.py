"""노출량 계산 — 활동일, 관찰일, 모듈 요약, 벤더 `[요약]` 재계산, 정의 민감도.

이 모듈의 규칙 두 가지:

1. **평균은 절대 혼자 다니지 않는다.** 모든 평균은 `Mean3`(전체평균 · 0제외평균 ·
   0행비율) 로만 존재한다. 0초 행이 35.6% 인 자료에서 `평균 0.0초` 한 숫자는
   "0을 측정했다"로 읽히지만 실제로는 "기록되지 않았다"일 수 있기 때문이다.
2. **효과를 보지 않는다.** 여기에는 상관·회귀·군간검정이 없다. 노출량과 결과의
   관계는 `statwise`·`longistat`·`medpath` 의 일이다 (`test_강제장치.py` 가 AST 로 고정).
"""

import decimal
import math
import statistics
from collections import Counter, OrderedDict

from doseaudit.rules import ActiveDayRule

#: `평균 0회` 로 인쇄되지만 실제로는 데이터가 0행인 모듈.
STATUS_NO_DATA = "데이터 없음"
#: 모든 행의 소요시간이 0인 모듈 — 0과 미기록을 구분할 수 없다.
STATUS_ALL_ZERO = "0으로 기록됨 — 미측정과 구분 불가"
STATUS_OK = "데이터 있음"

#: 소요시간 0초를 '측정된 0' 으로 볼 수 없을 때 쓰는 표시.
UNCOMPARABLE = "대조불가"

#: 창을 정하지 못한 이유. **이유를 구분해야 사람이 어디를 고칠지 안다.**
REASON_NO_ENROLL = "가입일을 모름"
REASON_NO_ACTIVITY = "활동 기록이 하나도 없음"
REASON_WINDOW_EMPTY = "창의 끝이 시작보다 앞섬"

#: `훈련유형` 칸이 비어 있는 행을 담는 이름. 조용히 빼면 유형별 합이 안 맞는다.
TYPE_BLANK = "(유형없음)"


def _safe_mean(values):
    """평균. 낼 수 없으면(빈 목록·합계 넘침) None.

    `statistics.fmean` 은 중간 합이 넘치면 `OverflowError` 를 던진다. 벤더가
    보낸 칸에 1e308 이 적혀 있다고 해서 툴이 트레이스백으로 끝나면, 사용자는
    자기 자료의 어느 줄이 문제인지도 모른 채 종료코드 1(치명 발견)만 받는다.
    """
    if not values:
        return None
    try:
        mean = statistics.fmean(values)
    except (OverflowError, ValueError):
        return None
    return mean if math.isfinite(mean) else None


class Mean3(object):
    """평균을 **세 값으로만** 내보내는 단일 통로.

    `전체평균` 하나만 꺼내 쓰는 코드 경로가 이 파일 어디에도 없다는 것이
    이 툴의 핵심 안전장치다.
    """

    __slots__ = ("total_mean", "nonzero_mean", "zero_ratio", "n", "n_zero", "n_unparsed")

    def __init__(self, values, n_unparsed=0):
        known = [v for v in values if v is not None]
        self.n = len(known)
        # 해석하지 못한 값은 **여기서 센다.** 호출자가 따로 세어 넘겨 주기를
        # 기다리면(예전 동작) 아무도 넘겨 주지 않아 영원히 0이 되고, 그러면
        # `0행비율` 의 분모(해석된 행)와 CSV 의 `행수`(전체 행)가 어긋난 채
        # 둘 다 인쇄된다 — 산출물만 보고는 설명할 수 없는 차이가 된다.
        self.n_unparsed = n_unparsed + sum(1 for v in values if v is None)
        zeros = [v for v in known if v == 0]
        nonzero = [v for v in known if v != 0]
        self.n_zero = len(zeros)
        # 1e308 급 값이 두 개만 있어도 `fmean` 의 중간 합이 넘친다. 파일 안의
        # 숫자 하나가 툴을 트레이스백으로 끝내면 안 되므로, 평균을 낼 수 없는
        # 경우로 떨어뜨린다 — `대조불가` 로 인쇄되고 조용히 사라지지 않는다.
        self.total_mean = _safe_mean(known)
        self.nonzero_mean = _safe_mean(nonzero)
        self.zero_ratio = (len(zeros) / len(known)) if known else None

    def triple(self, unit=""):
        """`(전체평균, 0제외평균, 0행비율)` 을 사람이 읽는 문자열 3개로."""
        if self.n == 0 or self.total_mean is None:
            # 해석된 값이 없거나(전자) 합계가 넘쳐 평균을 낼 수 없는 경우(후자).
            # 둘 다 **숫자를 만들어내지 않는다.**
            return (UNCOMPARABLE, UNCOMPARABLE, UNCOMPARABLE)
        total = "%.2f%s" % (self.total_mean, unit)
        if self.nonzero_mean is None:
            nonzero = "%s(0행 100%%)" % UNCOMPARABLE
        else:
            nonzero = "%.2f%s" % (self.nonzero_mean, unit)
        return (total, nonzero, "%.1f%%" % (100 * self.zero_ratio))

    def csv_fields(self):
        """CSV 로 나가는 3칸. 단일 평균 칸은 존재하지 않는다."""
        if self.n == 0 or self.total_mean is None:
            return (UNCOMPARABLE, UNCOMPARABLE, UNCOMPARABLE)
        return (
            "%.4f" % self.total_mean,
            UNCOMPARABLE if self.nonzero_mean is None else "%.4f" % self.nonzero_mean,
            "%.4f" % self.zero_ratio,
        )


def day_buckets(subject):
    """피험자의 행을 `{날짜: {모듈: [LogRow, ...]}}` 로 묶는다."""
    out = {}
    for module_name, module in subject.modules.items():
        for row in module.rows:
            if row.usable:
                out.setdefault(row.date, {}).setdefault(module_name, []).append(row)
    return out


def duplicate_suspects(subject):
    """`(날짜·레벨·훈련유형·반복횟수·소요시간)` 이 완전히 같은 행의 반복 건수.

    **제거하지 않는다.** 같은 날 같은 레벨을 두 번 하는 것은 정상일 수 있다 —
    이 툴은 "같은 지문이 N번 반복됐다"는 사실만 센다.
    """
    total = 0
    per_module = {}
    for module_name, module in subject.modules.items():
        counts = Counter(row.fingerprint for row in module.rows)
        dups = sum(c - 1 for c in counts.values() if c > 1)
        per_module[module_name] = dups
        total += dups
    return total, per_module


def longest_gap_days(active_days, start=None, end=None):
    """선언된 창 안에서 **훈련이 없었던 가장 긴 연속 일수**.

    창의 **양 끝도 공백이다.** 창이 시작됐는데 한참 뒤에야 첫 훈련이 나온 구간과,
    마지막 훈련 뒤 창이 끝날 때까지의 침묵은 둘 다 '훈련이 없던 날'이다. 이걸
    빼고 활동일 사이만 재면, 3월에 그만둔 사람이 6월까지 열려 있는 창에서
    `최장공백 6일` 로 인쇄된다 — 실제로는 94일이다. 중도 이탈 패턴을 보여
    주라고 있는 열이 정확히 그 패턴을 가린다.

    창을 정하지 못했으면(`start is None`) 잴 기준이 없으므로 활동일 사이만 재고,
    활동일이 2개 미만이면 None.
    """
    days = sorted(active_days)
    if start is None or end is None:
        if len(days) < 2:
            return None
        return max((b - a).days - 1 for a, b in zip(days, days[1:]))
    if not days:
        # 창 안에 활동이 하나도 없었다 — 창 전체가 공백이다(양 끝 포함).
        return (end - start).days + 1
    gaps = [(days[0] - start).days, (end - days[-1]).days]
    gaps.extend((b - a).days - 1 for a, b in zip(days, days[1:]))
    return max(gaps)


class SubjectExposure(object):
    """피험자 1명의 노출량. 판정이 아니라 셈이다."""

    __slots__ = ("code", "active_days", "n_active", "first", "last", "observed_days",
                 "days_per_week", "longest_gap", "n_rows", "n_zero_sec", "n_dup",
                 "module_rows", "adherence_pct", "capped", "n_unparsed_dates",
                 "window_start", "window_end", "n_outside_window", "dup_by_module",
                 "window_known", "window_unknown_reason")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))


def subject_exposure(subject, rule, window, enroll, target_per_week, cap):
    """피험자 1명의 노출량을 **선언된 정의로** 센다.

    분자(활동일)와 분모(관찰일수)는 **같은 창**을 본다. 창 밖의 활동일은 버리지 않고
    `n_outside_window` 로 따로 세어 리포트에 인쇄한다 — 조용히 빠지면 사람이
    "왜 내가 아는 날짜가 없지"를 영영 모른다.
    """
    buckets = day_buckets(subject)
    all_rows = subject.rows()
    usable_dates = [r.date for r in all_rows if r.usable]
    last_overall = max(usable_dates) if usable_dates else None

    start, end = window.bounds(enroll, last_overall)
    # 창을 정하지 못한 **이유**를 구분해 둔다. 한 문장으로 뭉뚱그리면
    # "가입일을 확인하세요"라고 안내받은 사람이 멀쩡한 가입일 칸을 들여다보게 된다.
    if start is not None:
        unknown_reason = None
    elif enroll is None:
        unknown_reason = REASON_NO_ENROLL
    elif last_overall is None:
        unknown_reason = REASON_NO_ACTIVITY
    else:
        unknown_reason = REASON_WINDOW_EMPTY
    active_all = {d for d, bucket in buckets.items() if rule.is_active(bucket)}
    if start is None:
        active = active_all
        n_outside = 0
    else:
        active = {d for d in active_all if start <= d <= end}
        n_outside = len(active_all) - len(active)

    first = min(active) if active else None
    last = max(active) if active else None

    observed = window.observed_days(enroll, last_overall)
    per_week = (len(active) / (observed / 7.0)) if observed else None

    adherence = None
    capped = False
    if observed and target_per_week:
        # `--target-per-week 5e-324` 처럼 아주 작은 값이면 분모가 0.0 으로
        # 언더플로해 ZeroDivisionError 가 난다. 나눌 수 없으면 노출률을 내지 않는다.
        denominator = target_per_week * observed / 7.0
        adherence = (100.0 * len(active) / denominator) if denominator else None
    if adherence is not None:
        if cap is not None and adherence > cap:
            adherence = float(cap)
            capped = True

    n_dup, dup_per_module = duplicate_suspects(subject)
    return SubjectExposure(
        code=subject.code,
        active_days=active,
        n_active=len(active),
        first=first,
        last=last,
        observed_days=observed,
        days_per_week=per_week,
        longest_gap=longest_gap_days(active, start, end),
        n_rows=len(all_rows),
        n_zero_sec=sum(1 for r in all_rows if r.seconds == 0),
        n_dup=n_dup,
        dup_by_module=dup_per_module,
        module_rows={name: mod.n_rows for name, mod in subject.modules.items()},
        adherence_pct=adherence,
        capped=capped,
        n_unparsed_dates=sum(1 for r in all_rows if not r.usable),
        window_start=start,
        window_end=end,
        n_outside_window=n_outside,
        #: 창을 정하지 못해 **창 없이** 센 피험자. 이 숫자를 창이 적용된 다른
        #: 피험자와 나란히 두거나 트래커와 대조하면, 정의가 다른 값을 비교하게 된다.
        window_known=start is not None,
        window_unknown_reason=unknown_reason,
    )


class ModuleSummary(object):
    """모듈 1개의 요약. `평균 0` 과 `데이터 없음` 을 절대 섞지 않는다."""

    __slots__ = ("name", "n_files", "n_files_with_data", "n_rows", "n_zero_sec",
                 "seconds", "reps", "status", "by_type")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))


def module_summaries(bundle, pool_types=False):
    """모듈 × (행수 · 0행비율 · 전체평균 · 0제외평균 · 훈련유형 분리)."""
    out = OrderedDict()
    for name in bundle.module_order:
        rows, n_files, n_with_data = [], 0, 0
        for subject in bundle.subjects:
            module = subject.modules.get(name)
            if module is None:
                continue
            n_files += 1
            if module.rows:
                n_with_data += 1
            rows.extend(module.rows)

        seconds = Mean3([r.seconds for r in rows])
        reps = Mean3([r.reps for r in rows])
        if not rows:
            status = STATUS_NO_DATA
        elif seconds.zero_ratio == 1.0:
            status = STATUS_ALL_ZERO
        else:
            status = STATUS_OK

        by_type = OrderedDict()
        if not pool_types:
            # 빈 `훈련유형` 칸도 **한 무리로 센다.** 빼 버리면 유형별 행수의 합이
            # 모듈 행수보다 작아지는데, 그 차이를 설명하는 줄이 산출물 어디에도
            # 없어 "행이 어디로 갔나"를 사람이 영영 알 수 없다.
            for ttype in sorted({(r.ttype or TYPE_BLANK) for r in rows}):
                sub = [r for r in rows if (r.ttype or TYPE_BLANK) == ttype]
                by_type[ttype] = {
                    "n_rows": len(sub),
                    "n_zero_sec": sum(1 for r in sub if r.seconds == 0),
                    "seconds": Mean3([r.seconds for r in sub]),
                    "reps": Mean3([r.reps for r in sub]),
                }
        out[name] = ModuleSummary(
            name=name, n_files=n_files, n_files_with_data=n_with_data,
            n_rows=len(rows), n_zero_sec=sum(1 for r in rows if r.seconds == 0),
            seconds=seconds, reps=reps, status=status, by_type=by_type,
        )
    return out


class VendorCheck(object):
    """벤더 `[요약]` 블록 재계산 결과.

    **이 툴은 '벤더가 틀렸다'고 말하지 않는다.** 산수는 맞는지만 확인하고,
    맞다면 남는 문제는 계산이 아니라 **의미**라는 사실을 말한다.
    """

    __slots__ = ("n_checked", "n_agree", "mismatches", "n_empty_with_block",
                 "n_no_block", "printed_on_empty", "uncomparable")

    def __init__(self, n_checked, n_agree, mismatches, n_empty_with_block,
                 n_no_block, printed_on_empty, uncomparable=()):
        self.n_checked = n_checked
        self.n_agree = n_agree
        self.mismatches = mismatches
        #: 데이터가 0행인데 `[요약]` 블록은 있는 시트 수.
        self.n_empty_with_block = n_empty_with_block
        #: `[요약]` 블록 자체가 없던 시트 수. (두 가지를 한 숫자로 합치면
        #: "데이터가 0행이라 빠졌다"고 말하면서 행이 있는 시트를 포함하게 된다.)
        self.n_no_block = n_no_block
        #: **데이터가 0행인데 평균이 인쇄된** 시트 — `(코드, 모듈, [인쇄값], 0이아님)`.
        #: 한 모듈이 *일부* 피험자에게만 비어 있으면 모듈 단위 치명이 뜨지 않아,
        #: 이 경우가 통째로 보이지 않게 된다. 그 자리를 여기서 붙잡는다.
        self.printed_on_empty = printed_on_empty
        #: **재계산할 수 없었던** 항목 — `(코드, 모듈, 항목, 인쇄값, 사유)`.
        #: 해석 못 한 행이 섞였거나, 인쇄값이 숫자가 아니거나, 해석된 값이 하나도
        #: 없는 경우. `mismatches`(= 산수가 어긋남)와 **절대 섞지 않는다**:
        #: 견줄 수 없는 것을 틀렸다고 부르는 순간 이 툴은 벤더를 무고하게 된다.
        self.uncomparable = list(uncomparable)


def printed_decimals(raw):
    """인쇄된 값의 소수 자릿수. `'4.34초'` → 2, `'2회'` → 0.

    벤더는 열마다 다른 자릿수로 인쇄한다(실측: 소요시간 2자리, 반복횟수 0자리).
    자릿수를 고정해 두면 한쪽 열에서 반드시 틀린 판정이 나온다.

    `strip_unit` 과 **같은 전각 정규화**를 먼저 한다. `'１．５초'` 는 값으로는
    1.5 로 읽히는데 자릿수만 0으로 세면, 맞는 값을 놓고 "벤더가 틀렸다"고
    말하게 된다 — 이 툴이 절대 하지 않겠다고 한 말이다.
    """
    from doseaudit.values import normalize_number_text

    digits = "".join(ch for ch in normalize_number_text(raw) if ch.isdigit() or ch == ".")
    return len(digits.split(".", 1)[1]) if "." in digits else 0


def _vendor_agrees(recomputed, printed, decimals):
    """벤더가 인쇄한 값이 **인쇄된 자릿수로 반올림한 재계산 평균과 같은가**.

    `abs(차이) < 1.0` 로 보면 평균 2.9 와 인쇄값 2 가 '일치'가 된다 — 반복횟수처럼
    평균이 1~3인 열에서는 45% 오차를 눈감아 주는 셈이다. 자릿수를 맞춰 비교한다.

    반올림은 `round()` 가 아니라 **십진 반올림(half-up)** 으로 한다. 파이썬의
    `round()` 는 짝수로 붙이고(2.5→2) 이진 부동소수점 위에서 돌아서(4.045 는
    실제로 4.04499…), 스프레드시트가 인쇄한 값과 어긋난다. 그 어긋남은 곧바로
    **"벤더가 틀렸다"는 거짓 치명**이 되는데, 이 툴이 절대 하지 않겠다고 한 말이다.
    """
    # 인쇄된 자릿수가 decimal 기본 컨텍스트(28자리)를 넘으면 `quantize` 가
    # InvalidOperation 으로 죽는다. 벤더 칸에 소수점 40자리가 적혀 있다고 해서
    # 툴이 트레이스백으로 끝나면 안 된다 — 비교할 수 있는 자릿수로 줄인다.
    decimals = max(0, min(int(decimals), 15))
    quantum = decimal.Decimal(1).scaleb(-decimals)
    rounded = decimal.Decimal(repr(recomputed)).quantize(quantum,
                                                        rounding=decimal.ROUND_HALF_UP)
    return abs(rounded - decimal.Decimal(repr(printed))) < quantum / 2


def check_vendor_summaries(bundle):
    """모든 시트의 `[요약]` 블록을 데이터에서 다시 계산해 대조한다."""
    from doseaudit.logs import VENDOR_MEAN_REPS, VENDOR_MEAN_SECONDS
    from doseaudit.sanitize import vendor_display
    from doseaudit.values import COUNT_UNITS, SECONDS_UNITS, ValueError_, strip_unit

    n_checked = n_agree = n_empty_with_block = n_no_block = 0
    mismatches, printed_on_empty, uncomparable = [], [], []
    for subject in bundle.subjects:
        for name, module in subject.modules.items():
            if not module.vendor:
                n_no_block += 1
                continue
            if not module.rows:
                # 재계산할 행이 없다. 그런데 **평균은 인쇄돼 있다** — 그 값이
                # 0이든 아니든, 데이터에서 다시 나오지 않는 숫자다.
                n_empty_with_block += 1
                printed = ["%s %s" % (k, vendor_display(v))
                           for k, v in sorted(module.vendor.items())]
                nonzero = False
                for raw in module.vendor.values():
                    try:
                        if strip_unit(raw, SECONDS_UNITS + COUNT_UNITS) != 0:
                            nonzero = True
                    except ValueError_:
                        nonzero = True
                printed_on_empty.append((subject.code, name, printed, nonzero))
                continue
            sec = Mean3([r.seconds for r in module.rows])
            rep = Mean3([r.reps for r in module.rows])
            ok = True
            compared_any = False
            for key, mean3, units in ((VENDOR_MEAN_SECONDS, sec, SECONDS_UNITS),
                                      (VENDOR_MEAN_REPS, rep, COUNT_UNITS)):
                raw = module.vendor.get(key)
                if raw is None:
                    # 이 블록에는 이 항목이 없다. **확인한 것으로 세지 않는다** —
                    # 없는 항목으로 '재계산 일치'를 주장하면, 이 툴이 자랑하는
                    # `150/150` 이 실제로 검산한 수보다 커진다.
                    continue
                if mean3.n_unparsed:
                    # 해석 못 한 행이 섞여 있으면 **이 시트는 재계산할 수 없다.**
                    # 남은 행으로만 낸 평균을 '재계산값'이라 부르며 벤더와 견주면,
                    # 분모가 다른 두 숫자를 나란히 놓고 "벤더가 틀렸다"고 말하게 된다 —
                    # 이 툴이 절대 하지 않겠다고 한 단 하나의 말이다.
                    ok = False
                    uncomparable.append((
                        subject.code, name, key, raw,
                        "%s — 해석 못 한 행 %d개가 섞여 있어 이 시트는 재계산할 수 없습니다"
                        % (UNCOMPARABLE, mean3.n_unparsed)))
                    continue
                try:
                    printed = strip_unit(raw, units)
                except ValueError_:
                    # 인쇄값이 숫자가 아니다. 산수가 틀린 것이 아니라 **견줄 수 없는** 것이다.
                    ok = False
                    uncomparable.append((subject.code, name, key, raw,
                                         "%s — 인쇄값을 숫자로 해석하지 못했습니다" % UNCOMPARABLE))
                    continue
                if mean3.total_mean is None:
                    # 재계산할 값이 없다. 인쇄값이 0 이라고 해서 '일치'로 세면,
                    # 그것이 바로 이 툴이 막으려는 사고(미측정을 0으로 읽기)다.
                    ok = False
                    uncomparable.append((subject.code, name, key, raw,
                                         "%s — 해석된 값이 하나도 없습니다" % UNCOMPARABLE))
                    continue
                compared_any = True
                if not _vendor_agrees(mean3.total_mean, printed, printed_decimals(raw)):
                    ok = False
                    mismatches.append((subject.code, name, key, raw,
                                       "재계산 %.4f" % mean3.total_mean))
            if compared_any:
                n_checked += 1
                if ok:
                    n_agree += 1
    return VendorCheck(n_checked, n_agree, mismatches, n_empty_with_block,
                       n_no_block, printed_on_empty, uncomparable)


def sensitivity_table(bundle, declared_rule, declared_window, enrolls, target_per_week, cap,
                      contrast_specs=None, contrast_windows=None):
    """정의를 바꾸면 중앙값 노출률이 몇 %p 움직이는지.

    **두 축을 모두 흔든다.** 활동일 규칙(분자)만 흔들면 이 툴의 한 줄 요약
    ("분모가 무엇이었는지를 드러낸다")이 빈다 — 실측에서 더 크게 움직인 쪽은
    분모의 창이었다(51% → 67% → 90% → 100%).

    **어느 값이 옳은지 말하지 않는다.** 이 표의 전부는 "정의를 문서에 적지 않으면
    이 중 어느 값이든 될 수 있다"는 한 문장이다.
    """
    from doseaudit.rules import CONTRAST_RULES, CONTRAST_WINDOWS, Window

    rule_specs = list(contrast_specs if contrast_specs is not None else CONTRAST_RULES)
    window_specs = list(contrast_windows if contrast_windows is not None else CONTRAST_WINDOWS)

    entries, seen = [], set()

    def add(label, rule, window, this_cap, axis, declared=False):
        if label in seen:
            return
        seen.add(label)
        entries.append((label, rule, window, this_cap, axis, declared))

    add(declared_rule.spec, declared_rule, declared_window, cap, "활동일 규칙", declared=True)
    for spec in rule_specs:
        add(spec, ActiveDayRule.parse(spec), declared_window, cap, "활동일 규칙")
    for spec in window_specs:
        # 선언된 창과 같은 창을 대조 행으로 또 넣으면, 같은 설정이 라벨 없이
        # 한 번 더 찍혀 "창을 바꿔도 같다"로 읽힌다.
        if spec == declared_window.spec:
            continue
        add(spec, declared_rule, Window.parse(spec, None), cap, "분모의 창")
    if cap is None:
        add("%s + --cap 100" % declared_rule.spec, declared_rule, declared_window, 100, "상한 절단")
    else:
        # 상한을 **준** 경우에도 절단이 무엇을 바꿨는지 보여 준다. 안 그러면
        # 모든 행이 상한에 눌려 표 전체가 같은 값이 되고, 이 표의 용도가 사라진다.
        add("%s (상한 절단 없이)" % declared_rule.spec, declared_rule, declared_window,
            None, "상한 절단")

    rows = []
    for label, rule, window, this_cap, axis, declared in entries:
        values = []
        for subject in bundle.subjects:
            exposure = subject_exposure(subject, rule, window, enrolls.get(subject.code),
                                        target_per_week, this_cap)
            if exposure.adherence_pct is not None:
                values.append(exposure.adherence_pct)
        row = {"label": label, "axis": axis, "declared": declared, "n": len(values)}
        if values:
            row.update({"median": statistics.median(values), "min": min(values),
                        "max": max(values), "below50": sum(1 for v in values if v < 50)})
        else:
            row.update({"median": None, "min": None, "max": None, "below50": 0})
        rows.append(row)
    return rows
