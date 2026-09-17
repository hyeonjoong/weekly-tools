"""계산을 한 덩어리로 묶는다 — 판정이 아니라 셈과 대조.

`run_audit()` 는 `AuditResult` 를 돌려주고, 인쇄는 `report.py` 가 한다.
분리해 둔 이유는 테스트가 문자열을 거치지 않고 숫자를 직접 볼 수 있어야 하기
때문이다.
"""

import statistics

from doseaudit.dose import (REASON_NO_ACTIVITY, STATUS_ALL_ZERO, STATUS_NO_DATA,
                            check_vendor_summaries, module_summaries,
                            sensitivity_table, subject_exposure)
from doseaudit.errors import EXIT_CLEAN, EXIT_CRITICAL, EXIT_UNJUDGEABLE, RefuseError
from doseaudit.logs import load_bundle
from doseaudit.sanitize import vendor_display
from doseaudit.tracker import compare, load_tracker

LEVEL_CRITICAL = "치명"
LEVEL_WARN = "경고"
LEVEL_INFO = "정보"

#: 콘솔에 쏟아붓지 않는다 — 불일치는 한 줄 요약 + 최대 이만큼만 보이고 나머지는 CSV.
#: (매번 우는 체커는 두 번 열리지 않는다.)
CONSOLE_ROW_LIMIT = 5


class Finding(object):
    __slots__ = ("level", "title", "lines")

    def __init__(self, level, title, lines=()):
        self.level = level
        self.title = title
        self.lines = list(lines)


class Config(object):
    """선언받은 것 전부. 기본값으로 추론하는 항목은 하나도 없다."""

    __slots__ = ("logs", "tracker_path", "rule", "window", "enroll_source", "cap",
                 "target_per_week", "pool_types", "criterion", "out_dir")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))


class AuditResult(object):
    def __init__(self, config, bundle, tracker, exposures, modules, vendor,
                 comparison, sensitivity, findings, enrolls):
        self.config = config
        self.bundle = bundle
        self.tracker = tracker
        self.exposures = exposures
        self.modules = modules
        self.vendor = vendor
        self.comparison = comparison
        self.sensitivity = sensitivity
        self.findings = findings
        self.enrolls = enrolls

    def count(self, level):
        return sum(1 for f in self.findings if f.level == level)

    @property
    def exit_code(self):
        # 3 이 1 보다 **우선한다**: 못 읽은 파일이 있는데 "치명 2건"을 보고하면
        # 사람은 그 2건이 전부라고 읽는다.
        if self.bundle.unreadable:
            return EXIT_UNJUDGEABLE
        return EXIT_CRITICAL if self.count(LEVEL_CRITICAL) else EXIT_CLEAN

    def adherence_values(self):
        return [e.adherence_pct for e in self.exposures if e.adherence_pct is not None]


def resolve_enrolls(bundle, tracker, enroll_source):
    """피험자별 관찰 시작일. **어디서 왔는지가 리포트에 인쇄된다.**"""
    out = {}
    if enroll_source == "tracker" and tracker is not None:
        by_code = tracker.by_code()
        for subject in bundle.subjects:
            row = by_code.get(subject.code)
            out[subject.code] = row.enroll if row else None
        return out
    for subject in bundle.subjects:
        dates = [r.date for r in subject.rows() if r.usable]
        out[subject.code] = min(dates) if dates else None
    return out


def _fmt_int(n):
    return "{:,}".format(int(n))


def _signed(value):
    """`+3` / `-0.50` — 부호를 붙이되 **자르지 않는다**."""
    return "%+d" % value if float(value).is_integer() else "%+.2f" % value


def build_findings(bundle, modules, vendor, comparison, exposures, config):
    """센 결과를 [치명]/[경고]/[정보] 로 나눈다.

    나누는 원칙 하나: **애매하면 `[치명]` 이 아니라 `[경고]`, 경고가 애매하면
    `대조불가`.** 이 툴은 판정을 만들어내지 않는다.
    """
    findings = []
    n_rows = bundle.n_data_rows
    if not bundle.subjects:
        # 전부 못 읽었다. 판정을 만들어내지 않는다 — 자백과 종료코드 3 만 남는다.
        return findings

    # ── 치명 1. 트래커 `완료일수` 가 다시 센 활동일수와 다르다 ────────────────
    if comparison and comparison["diffs"]:
        diffs = comparison["diffs"]
        lines = []
        for diff in diffs[:CONSOLE_ROW_LIMIT]:
            # `%d` 로 찍으면 3.5 가 3 이 되어 "트래커 3일 ↔ 로그 3일 (+0)" 이라는,
            # 고칠 데가 없는 치명이 인쇄된다. CSV·Methods 와 같은 포맷터를 쓴다.
            lines.append("%-12s 트래커 %s일  ↔  로그 %d일 (%s)"
                         % (diff.code, diff.fmt_tracker(), diff.recomputed, diff.fmt_delta()))
        if len(diffs) > CONSOLE_ROW_LIMIT:
            lines.append("... 나머지 %d명은 트래커불일치.csv 에 있습니다" % (len(diffs) - CONSOLE_ROW_LIMIT))
        if comparison["uniform_sign"]:
            direction = "트래커가 더 작습니다" if comparison["delta_min"] > 0 else "트래커가 더 큽니다"
            lines.append("%d명 전원에서 %s (차이 %s ~ %s)."
                         % (len(diffs), direction, _signed(comparison["delta_min"]),
                            _signed(comparison["delta_max"])))
            lines.append("한 방향으로만 어긋나면 오타가 아니라 **정의 차이**로 보입니다 —")
            lines.append("누군가 '완료'를 이 툴에 선언한 규칙과 다른 기준으로 셌습니다.")
        findings.append(Finding(
            LEVEL_CRITICAL,
            "트래커 `완료일수` 가 로그와 다릅니다 — %d명 중 %d명"
            % (len(diffs) + len(comparison["agree"]), len(diffs)),
            lines))

    # ── 치명 2. 데이터가 0행인 모듈 ───────────────────────────────────────────
    empty = [m for m in modules.values() if m.status == STATUS_NO_DATA]
    for module in empty:
        lines = ["%d개 파일 전부에서 데이터가 0행입니다." % module.n_files]
        printed = _vendor_printed_for(bundle, module.name)
        if printed:
            lines.append("벤더 [요약] 블록은 %s 로 인쇄됩니다." % " · ".join(printed))
        lines.append("\"0을 측정함\"이 아니라 \"데이터 없음\"입니다. 그대로 보고하지 마십시오.")
        findings.append(Finding(LEVEL_CRITICAL, "`%s` 모듈: 데이터 없음" % module.name, lines))

    # ── 치명 2-b. 데이터가 0행인데 평균이 인쇄된 시트 ─────────────────────────
    # 모듈 전체가 비면 위에서 이미 치명으로 잡힌다. 여기서 잡는 것은 **일부
    # 피험자에게만 비어 있는** 경우다 — 모듈 단위로는 보이지 않아, 이 툴이 막으려는
    # 바로 그 사고("없는 데이터의 평균")가 통째로 숨는 자리다.
    empty_names = {m.name for m in modules.values() if m.status == STATUS_NO_DATA}
    partial = [row for row in vendor.printed_on_empty if row[1] not in empty_names]
    if partial:
        nonzero = [row for row in partial if row[3]]
        lines = ["%s / %s / %s" % (code, name, " · ".join(printed))
                 for code, name, printed, _nz in partial[:CONSOLE_ROW_LIMIT]]
        if len(partial) > CONSOLE_ROW_LIMIT:
            lines.append("... 그 외 %d장" % (len(partial) - CONSOLE_ROW_LIMIT))
        lines.append("이 시트들은 데이터가 0행입니다 — 인쇄된 평균은 어떤 행에서도 나오지 않습니다.")
        lines.append("같은 모듈에 데이터가 있는 피험자가 있어, 모듈 단위로는 보이지 않습니다.")
        findings.append(Finding(
            LEVEL_CRITICAL if nonzero else LEVEL_WARN,
            "데이터가 0행인데 `[요약]` 이 평균을 인쇄한 시트 %d장%s"
            % (len(partial), " (0이 아닌 값 %d장)" % len(nonzero) if nonzero else ""),
            lines))

    # ── 치명 3. 벤더 요약의 산수가 실제로 어긋난 경우 ─────────────────────────
    if vendor.mismatches:
        lines = ["%s / %s / %s / 인쇄값 %s / %s"
                 % (m[0], m[1], m[2], vendor_display(m[3]), m[4])
                 for m in vendor.mismatches[:CONSOLE_ROW_LIMIT]]
        findings.append(Finding(
            LEVEL_CRITICAL,
            "벤더 [요약] 블록이 데이터에서 재계산되지 않습니다 — %d건" % len(vendor.mismatches),
            lines))

    # ── 경고. 벤더 요약을 재계산할 수 **없었던** 시트 ─────────────────────────
    # 재계산할 수 없는 것은 '틀렸다'가 아니다. [치명]으로 올리면 이 툴이
    # 절대 하지 않겠다고 한 말("벤더가 틀렸다")을 남은 행으로 낸 평균을 근거로
    # 하게 된다. 애매하면 [치명]이 아니라 [경고], 경고가 애매하면 `대조불가`다.
    if getattr(vendor, "uncomparable", None):
        lines = ["%s / %s / %s / 인쇄값 %s / %s"
                 % (m[0], m[1], m[2], vendor_display(m[3]), m[4])
                 for m in vendor.uncomparable[:CONSOLE_ROW_LIMIT]]
        if len(vendor.uncomparable) > CONSOLE_ROW_LIMIT:
            lines.append("... 외 %d건" % (len(vendor.uncomparable) - CONSOLE_ROW_LIMIT))
        lines.append("**벤더가 틀렸다는 뜻이 아닙니다** — 이 시트는 견줄 수가 없습니다.")
        findings.append(Finding(
            LEVEL_WARN,
            "벤더 [요약] 블록을 재계산하지 못한 시트 %d건" % len(vendor.uncomparable),
            lines))

    # ── 경고 1. 0초 행 ────────────────────────────────────────────────────────
    n_zero = sum(m.n_zero_sec for m in modules.values())
    if n_zero:
        lines = ["0초가 '0초 걸렸다'인지 '기록되지 않았다'인지 로그만으로는 구분할 수 없습니다.",
                 "그래서 이 툴의 모든 평균은 전체평균 · 0제외평균 · 0행비율 **세 값**으로만 나갑니다."]
        for module in modules.values():
            if module.status == STATUS_NO_DATA:
                continue
            total, nonzero, ratio = module.seconds.triple("초")
            lines.append("%-10s 전체평균 %s · 0제외평균 %s · 0행비율 %s%s"
                         % (module.name, total, nonzero, ratio,
                            "  ← 단일 평균으로 내보내지 않습니다" if module.status == STATUS_ALL_ZERO else ""))
        findings.append(Finding(
            LEVEL_WARN,
            "소요시간 0초 행 %s건 (%.1f%%)" % (_fmt_int(n_zero), 100.0 * n_zero / n_rows if n_rows else 0.0),
            lines))

    # ── 경고 2. 한쪽에만 있는 코드 ────────────────────────────────────────────
    if comparison and (comparison["only_tracker"] or comparison["only_logs"]):
        lines = []
        if comparison["only_tracker"]:
            lines.append("트래커에만 있음 %d명: %s"
                         % (len(comparison["only_tracker"]), ", ".join(comparison["only_tracker"][:8])))
        if comparison["only_logs"]:
            lines.append("로그에만 있음 %d명: %s"
                         % (len(comparison["only_logs"]), ", ".join(comparison["only_logs"][:8])))
        lines.append("어느 쪽이 맞는지 이 툴은 모릅니다 — 탈락인지 누락인지 사람이 확인해야 합니다.")
        findings.append(Finding(LEVEL_WARN, "트래커와 로그의 피험자 집합이 다릅니다", lines))

    # ── 경고 3. 트래커 내부 정합성 ────────────────────────────────────────────
    if comparison and comparison["progress_bad"]:
        lines = ["%s 인쇄 %g%% ↔ 완료일수÷현재까지일수 %g%%" % row
                 for row in comparison["progress_bad"][:CONSOLE_ROW_LIMIT]]
        findings.append(Finding(LEVEL_WARN, "트래커의 `진행률(%%)` 이 트래커 자신의 열에서 재현되지 않습니다 — %d명"
                                % len(comparison["progress_bad"]), lines))
    elif comparison and comparison["progress_checked"] and comparison["diffs"]:
        # **확인한 사람이 있을 때만** 정합하다고 말한다. 열이 없어 한 명도 확인하지
        # 못했는데 "전원 재현됩니다"라고 하면, 하지 않은 검증을 주장하게 된다.
        findings.append(Finding(
            LEVEL_INFO, "트래커의 `진행률(%)` 은 트래커 안에서 정합적입니다",
            ["`완료일수 ÷ 현재까지 일수` 로 %d명 전원이 재현됩니다."
             % len(comparison["progress_checked"]),
             "즉 손으로 들어간 값은 `완료일수` **하나**이고 나머지는 거기서 파생됐습니다 —",
             "고칠 곳은 한 열입니다."]))

    # ── 경고 3-b. 선언된 창 밖의 활동 ─────────────────────────────────────────
    outside = [e for e in exposures if e.n_outside_window]
    if outside:
        total = sum(e.n_outside_window for e in outside)
        lines = ["%-12s 창(%s ~ %s) 밖 %d일"
                 % (e.code, e.window_start, e.window_end, e.n_outside_window)
                 for e in outside[:CONSOLE_ROW_LIMIT]]
        if len(outside) > CONSOLE_ROW_LIMIT:
            lines.append("... 나머지는 피험자별노출량.csv 의 `창밖활동일수` 열에 있습니다")
        lines.append("분자(활동일)와 분모(관찰일수)는 **같은 창**을 봐야 하므로 이 날들은")
        lines.append("노출률 계산에서 빠졌습니다. 버린 것이 아니라 여기에 세어 두었습니다 —")
        lines.append("선언한 창이 의도한 것인지 확인하세요.")
        findings.append(Finding(
            LEVEL_WARN, "선언된 창 밖에서 기록된 활동일 %s일 (%d명)" % (_fmt_int(total), len(outside)),
            lines))

    # ── 경고 3-c. 창을 정하지 못한 피험자 ─────────────────────────────────────
    unknown = [e for e in exposures if not e.window_known]
    if unknown:
        lines = ["대상: %s%s" % (", ".join(e.code for e in unknown[:CONSOLE_ROW_LIMIT]),
                                " ..." if len(unknown) > CONSOLE_ROW_LIMIT else "")]
        # 이유를 뭉뚱그리면, 가입일이 멀쩡히 적힌 사람을 두고 "가입일을 확인하세요"라고
        # 안내하게 된다 — 가서 볼 것이 없는 곳으로 보내는 셈이다.
        by_reason = {}
        for e in unknown:
            by_reason.setdefault(getattr(e, "window_unknown_reason", None) or "사유 미상",
                                 []).append(e.code)
        for reason, codes in sorted(by_reason.items()):
            lines.append("· %s — %d명 (%s%s)"
                         % (reason, len(codes), ", ".join(codes[:CONSOLE_ROW_LIMIT]),
                            " ..." if len(codes) > CONSOLE_ROW_LIMIT else ""))
        lines.append("이 피험자들의 `활동일수` 는 **창 없이 워크북 전체에서** 센 값이라,")
        lines.append("창이 적용된 다른 피험자와 나란히 두면 안 됩니다 — 노출률도 내지 않았습니다.")
        if any(getattr(e, "window_unknown_reason", None) == REASON_NO_ACTIVITY for e in unknown):
            # 활동이 0건이면 다시 센 활동일수는 어느 창에서도 0 이라 모호함이 없다.
            # 그래서 이쪽은 **대조에서 빼지 않았다** — 빼면 가장 크게 어긋난 사람이 사라진다.
            lines.append("단, `%s` 인 피험자는 다시 센 활동일수가 어느 창에서도 0 이라"
                         % REASON_NO_ACTIVITY)
            lines.append("트래커 대조에는 **그대로 넣었습니다**(빼면 가장 크게 어긋난 사람이 사라집니다).")
        if any(getattr(e, "window_unknown_reason", None) != REASON_NO_ACTIVITY for e in unknown):
            lines.append("그 밖의 사유는 트래커 대조에서 뺐습니다"
                         "(`트래커불일치.csv` 의 사유 열에 적혀 있습니다).")
        findings.append(Finding(
            LEVEL_WARN, "선언된 창을 정할 수 없어 창 없이 센 피험자 %d명" % len(unknown), lines))

    # ── 경고 4. 해석하지 못한 값 ──────────────────────────────────────────────
    if bundle.parse_fail_rows:
        lines = ["%s / %s / %d행 / %s" % row for row in bundle.parse_fail_rows[:CONSOLE_ROW_LIMIT]]
        lines.append("조용히 버리지 않았습니다 — 이 행들은 평균과 활동일 계산에서 빠졌습니다.")
        findings.append(Finding(LEVEL_WARN, "값을 해석하지 못한 행 %d건" % len(bundle.parse_fail_rows), lines))

    # ── 정보. 상한 절단·선언된 기준의 **결과**를 콘솔에도 인쇄한다 ────────────
    capped = [e for e in exposures if e.capped]
    if capped:
        findings.append(Finding(
            LEVEL_INFO, "상한 절단으로 값이 바뀐 피험자 %d명" % len(capped),
            ["절단하지 않았다면 100%를 넘는 값이 그대로 보고됩니다.",
             "절단 여부는 `피험자별노출량.csv` 의 `상한절단` 열에 Y/N 으로 있습니다.",
             "대상: %s%s" % (", ".join(e.code for e in capped[:CONSOLE_ROW_LIMIT]),
                            " ..." if len(capped) > CONSOLE_ROW_LIMIT else "")]))

    if config.criterion is not None:
        judged = [e for e in exposures if e.adherence_pct is not None]
        met = [e for e in judged if e.adherence_pct >= config.criterion]
        if judged:
            findings.append(Finding(
                LEVEL_INFO,
                "선언된 기준(노출률 %g%% 이상)을 충족한 피험자 %d/%d명 (%.0f%%)"
                % (config.criterion, len(met), len(judged), 100.0 * len(met) / len(judged)),
                ["이 기준은 **연구팀이 준 값**입니다 — 이 툴이 정한 값이 아닙니다.",
                 "피험자별 충족/미달은 `피험자별노출량.csv` 의 `선언기준충족` 열에 있습니다.",
                 "노출률을 계산할 수 없어 판정하지 않은 피험자 %d명은 빠져 있습니다."
                 % (len(exposures) - len(judged))]))

    # ── 정보. 벤더 [요약] 재계산이 **맞았다**는 사실도 소리 내어 말한다 ───────
    if vendor.n_checked and not vendor.mismatches:
        findings.append(Finding(
            LEVEL_INFO,
            "벤더 [요약] 블록 %d/%d 시트가 데이터에서 그대로 재계산됩니다"
            % (vendor.n_agree, vendor.n_checked),
            ["즉 **산수는 맞습니다.** 이 툴은 벤더가 틀렸다고 말하지 않습니다.",
             "남는 문제는 계산이 아니라 **의미**입니다 — 0초 행과 데이터 0행 모듈을",
             "구분하지 않은 평균은, 정확하게 계산된 채로 잘못 읽힙니다.",
             "(데이터가 0행인 시트 %d장과 `[요약]` 블록이 없는 시트 %d장은 "
             "재계산할 대상이 없어 빠졌습니다.)"
             % (vendor.n_empty_with_block, vendor.n_no_block)]))

    # ── 정보. 중복 의심 ───────────────────────────────────────────────────────
    n_dup = sum(e.n_dup for e in exposures)
    if n_dup:
        lines = ["**제거하지 않았습니다.** 같은 날 같은 레벨을 여러 번 하는 것은 정상일 수 있습니다.",
                 "문항 하나가 한 행인 모듈에서는 지문이 겹치는 것이 **기대되는** 일이라,",
                 "이 숫자는 '오류 %s건'이 아니라 '지문이 겹친 행이 이만큼'이라는 뜻입니다."
                 % _fmt_int(n_dup),
                 "모듈별로 나눠 보면 어디가 유별난지 보입니다:"]
        for name, count, rows_in_module in _dup_by_module(modules, exposures):
            ratio = (100.0 * count / rows_in_module) if rows_in_module else 0.0
            lines.append("  %-12s %7s건 / %7s행 (%.1f%%)"
                         % (name, _fmt_int(count), _fmt_int(rows_in_module), ratio))
        findings.append(Finding(
            LEVEL_INFO,
            "중복의심 %s건 — (날짜·레벨·훈련유형·반복횟수·소요시간)이 완전히 같은 행" % _fmt_int(n_dup),
            lines))

    return findings


def _dup_by_module(modules, exposures):
    """모듈별 중복의심 건수와 그 모듈의 행수. 전체 한 숫자는 신호가 되지 못한다."""
    per_module = {}
    for exposure in exposures:
        for name, count in exposure.dup_by_module.items():
            per_module[name] = per_module.get(name, 0) + count
    out = []
    for name, module in modules.items():
        out.append((name, per_module.get(name, 0), module.n_rows))
    return out


def _vendor_printed_for(bundle, module_name):
    """빈 모듈의 `[요약]` 블록이 실제로 무엇을 인쇄하는지 모아 온다."""
    seen = []
    for subject in bundle.subjects:
        module = subject.modules.get(module_name)
        if module and module.vendor:
            for key, value in sorted(module.vendor.items()):
                label = "%s %s" % (key, vendor_display(value))
                if label not in seen:
                    seen.append(label)
    return seen[:4]


def run_audit(config):
    """워크북 묶음을 읽고, 선언된 정의로 세고, 트래커와 대조한다."""
    bundle = load_bundle(config.logs)
    try:
        tracker = load_tracker(config.tracker_path) if config.tracker_path else None
    except RefuseError as exc:
        # 트래커를 거절하는 것은 맞다. 다만 **못 읽은 워크북이 있다는 사실**까지
        # 같이 사라지면, 사용자는 로그가 전부 멀쩡한 줄 안다.
        if bundle.unreadable:
            raise RefuseError(
                "%s\n\n       (덧붙여: 워크북 %d개를 읽지 못했습니다 — %s)\n"
                "       트래커를 고친 뒤 다시 돌리면 그 목록이 전부 인쇄됩니다."
                % (exc, len(bundle.unreadable),
                   ", ".join(name for name, _r in bundle.unreadable[:3])))
        raise

    enroll_source = config.enroll_source or ("tracker" if tracker is not None else "first-activity")
    enrolls = resolve_enrolls(bundle, tracker, enroll_source)

    exposures = [subject_exposure(s, config.rule, config.window, enrolls.get(s.code),
                                  config.target_per_week, config.cap)
                 for s in bundle.subjects]
    modules = module_summaries(bundle, pool_types=config.pool_types)
    vendor = check_vendor_summaries(bundle)
    comparison = (compare(tracker, exposures, unreadable_codes=bundle.unreadable_codes())
                  if tracker is not None else None)
    sensitivity = (sensitivity_table(bundle, config.rule, config.window, enrolls,
                                     config.target_per_week, config.cap)
                   if config.target_per_week else [])
    findings = build_findings(bundle, modules, vendor, comparison, exposures, config)

    result = AuditResult(config, bundle, tracker, exposures, modules, vendor,
                         comparison, sensitivity, findings, enrolls)
    result.enroll_source = enroll_source
    return result


def median_or_none(values):
    return statistics.median(values) if values else None
