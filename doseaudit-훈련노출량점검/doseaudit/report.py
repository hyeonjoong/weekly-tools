"""리포트 렌더링 — 콘솔과 `노출량점검.md`.

이 파일의 불문율: **`[커버리지 자백]` 없는 리포트는 내보내지 않는다.**
자백이 빠진 리포트는 "전부 봤다"로 읽히고, 그게 이 툴이 막으려는 사고 그 자체다.
빠지면 `ReportIntegrityError` 로 죽는다 (`visitaudit` 전례).
"""

from doseaudit.audit import (CONSOLE_ROW_LIMIT, LEVEL_CRITICAL, LEVEL_INFO, LEVEL_WARN)
from doseaudit.dose import STATUS_NO_DATA, UNCOMPARABLE
from doseaudit.errors import ReportIntegrityError
from doseaudit.sanitize import safe_text
from doseaudit.tracker import COL_CODE as TRACKER_CODE_COLUMN
from doseaudit.values import fmt_date, fmt_dot

import decimal
import statistics


def _signed(value):
    """`+3` / `-2` / `+3.5` — 부호를 반드시 붙인다."""
    return "%+d" % value if float(value).is_integer() else "%+.2f" % value


def _iqr(values):
    """사분위수 `(Q1, Q3)`. 범위만으로는 분포를 말할 수 없다 — 리뷰어가 묻는 자리다."""
    if len(values) < 2:
        return (values[0], values[0])
    quarters = statistics.quantiles(values, n=4, method="inclusive")
    return (quarters[0], quarters[2])


def _gloss(result, module_name):
    """영문 초안에서 한글 모듈명에 자리번호를 붙인다 (`Module 6 (발성훈련)`)."""
    order = list(result.modules)
    index = order.index(module_name) + 1 if module_name in order else 0
    return "Module %d (%s)" % (index, module_name) if index else module_name

CONFESSION_HEADER = "[커버리지 자백]"

#: 이 툴이 **일부러 보지 않는** 것들. 리포트마다 그대로 인쇄된다.
NOT_EXAMINED = (
    "훈련 내용의 정오답 (이 툴은 얼마나 받았는지만 셉니다)",
    "노출량과 결과의 관계 — 상관·회귀·군간검정 (→ `statwise`·`longistat`·`medpath`)",
    "세션 단위 집계 — 이 툴의 최소 단위는 **날짜**입니다 (→ `logflow`)",
    "트래커의 `D1`…`DN` 일자별 격자 — 기준점(가입일인지 연구 시작일인지)이 문서화돼 있지 않아 읽지 않습니다",
    "트래커의 `환자명`·`Login ID` 열 — 열지 않습니다",
    "탈락 시점 — 마지막 활동일만 인쇄하고 '탈락했다'고 말하지 않습니다",
)


def _fmt_int(n):
    return "{:,}".format(int(n))


def _r0(value):
    """정수 자리 **십진 반올림(half-up)** 한 문자열.

    파이썬의 `%.0f` 는 짝수 쪽으로 붙이고(62.5 → 62, 63.5 → 64) 이진 부동소수점
    위에서 돌아, 사람이 손으로 재계산한 값과 어긋난다. 여기서 나오는 숫자는
    Methods 문단에 그대로 실려 **리뷰어가 손으로 검산하는 자리**이므로,
    벤더 대조에서 이미 채택한 half-up 관습을 그대로 쓴다.
    """
    quantized = decimal.Decimal(repr(float(value))).quantize(
        decimal.Decimal(1), rounding=decimal.ROUND_HALF_UP)
    return str(quantized)


def _pct(value):
    return "%s%%" % _r0(value) if value is not None else UNCOMPARABLE


def declaration_lines(result):
    """선언된 정의 전문 — 판정보다 먼저, 그리고 Methods 문장에 그대로 들어간다."""
    cfg = result.config
    cap = "상한 절단 없음" if cfg.cap is None else "상한 %g%% 에서 절단" % cfg.cap
    lines = [
        "활동일 = %s (`%s`)" % (cfg.rule.label(), cfg.rule.spec),
        "분모 창 = %s" % cfg.window.label(result.enroll_source),
        "처방 = 주 %g일" % cfg.target_per_week if cfg.target_per_week else "처방 = 선언 안 함 → 노출률(%) 계산하지 않음",
        cap,
        "훈련유형 = %s" % ("REGULAR·INDIVIDUAL 합산함(--pool-types)" if cfg.pool_types
                          else "REGULAR·INDIVIDUAL 분리"),
    ]
    if cfg.criterion is not None:
        lines.append("선언된 프로토콜 기준 = 노출률 %g%% 이상 (사용자가 준 값입니다 — 이 툴이 정한 값이 아닙니다)"
                     % cfg.criterion)
    return lines


def confession_lines(result):
    """`[커버리지 자백]` 본문."""
    bundle = result.bundle
    n_workbooks_tried = bundle.n_workbooks + len(bundle.unreadable)
    lines = []
    read = ("읽음: 워크북 %d/%d · 시트 %d장 · 데이터행 %s"
            % (bundle.n_workbooks, n_workbooks_tried, bundle.n_sheets, _fmt_int(bundle.n_data_rows)))
    if result.tracker is not None:
        read += " · 트래커 1/1 (%d행)" % len(result.tracker.rows)
    else:
        read += " · 트래커 없음 (`--tracker` 미지정 → 대조 안 함)"
    lines.append(read)

    if bundle.unreadable:
        lines.append("못 읽음: %d개 — 판정할 수 없습니다(종료코드 3)" % len(bundle.unreadable))
        for name, reason in bundle.unreadable[:CONSOLE_ROW_LIMIT]:
            lines.append("    · %s — %s" % (name, safe_text(reason, 80)))
    if bundle.skipped_files:
        lines.append("건너뜀: %d개 — %s"
                     % (len(bundle.skipped_files),
                        ", ".join("%s(%s)" % (n, r) for n, r in bundle.skipped_files[:3])))

    lines.append("셈의 범위:")
    lines.append("    · 창 기준(선언된 창 안에서만): 활동일수 · 주당활동일 · 노출률 ·")
    lines.append("      최장공백 · 첫/마지막 활동일 · 트래커 대조")
    lines.append("    · 워크북 전체(창과 무관): 행 수 · 0초 행 수 · 중복의심 · 모듈별 평균")
    lines.append("      (CSV 열 이름에 `_전체기간` 이 붙어 있는 것들입니다)")
    lines.append("검사 안 함:")
    for item in NOT_EXAMINED:
        lines.append("    · %s" % item)
    if result.tracker is not None and result.tracker.dropped_columns:
        lines.append("    · 열지 않은 트래커 열(개인식별): %s"
                     % ", ".join(safe_text(c, 30) for c in result.tracker.dropped_columns))
    if result.tracker is not None and result.tracker.unread_columns:
        lines.append("    · 읽지 않은 트래커 열: %s"
                     % ", ".join(safe_text(c, 30) for c in result.tracker.unread_columns[:8]))

    uncomparable = []
    for module in result.modules.values():
        if module.status == STATUS_NO_DATA:
            uncomparable.append("`%s` 모듈 (데이터 0행 — 평균을 낼 대상이 없습니다)" % module.name)
        elif module.seconds.zero_ratio == 1.0:
            uncomparable.append("`%s` 모듈의 0제외평균 (0행 100%%)" % module.name)
    n_zero = sum(m.n_zero_sec for m in result.modules.values())
    if n_zero:
        uncomparable.append("0초 행 %s건의 실제 소요시간 (0인지 미기록인지 로그가 말하지 않습니다)" % _fmt_int(n_zero))
    if bundle.parse_fail_rows:
        uncomparable.append("값을 해석하지 못한 %d행" % len(bundle.parse_fail_rows))
    if result.tracker is not None and result.tracker.unread_fields:
        uncomparable.append("트래커에서 읽지 못한 칸 %d개" % len(result.tracker.unread_fields))
    if result.tracker is not None and result.tracker.n_blank_rows:
        uncomparable.append("`%s` 칸이 비어 건너뛴 트래커 행 %d개 (병합 셀·소계 행일 수 있습니다)"
                            % (TRACKER_CODE_COLUMN, result.tracker.n_blank_rows))
    lines.append("대조불가:" if uncomparable else "대조불가: 없음")
    for item in uncomparable:
        lines.append("    · %s" % item)

    lines.append("리포트에 안 나온 항목이 \"일치\"라는 뜻이 아닙니다.")
    return lines


def sensitivity_lines(result):
    """정의 민감도 표 — **분자 축과 분모 축을 모두** 흔들어 보인다."""
    if not result.sensitivity:
        return []
    lines = ["정의 민감도 — 중앙값 노출률(논문에서 adherence 로 보고되는 값)"]
    last_axis = None
    for row in result.sensitivity:
        if row["axis"] != last_axis:
            lines.append("  [%s 을 바꿨을 때]" % row["axis"])
            last_axis = row["axis"]
        mark = "  ← 선언됨" if row["declared"] else ""
        if row["median"] is None:
            lines.append("    %-22s %s" % (row["label"], UNCOMPARABLE))
            continue
        lines.append("    %-22s %5s%%   (범위 %s–%s%% · 50%% 미만 %d/%d)%s"
                     % (row["label"], _r0(row["median"]), _r0(row["min"]), _r0(row["max"]),
                        row["below50"], row["n"], mark))
    spread = [r["median"] for r in result.sensitivity if r["median"] is not None]
    if len(spread) > 1:
        lines.append("→ 같은 데이터에서 중앙값이 %s%% ~ %s%% 사이를 움직입니다."
                     % (_r0(min(spread)), _r0(max(spread))))
    lines.append("→ 정의를 문서에 적지 않으면 이 표의 어느 값이든 될 수 있습니다.")
    lines.append("→ 이 툴은 어느 값이 옳은지 말하지 않습니다. 고르는 것은 연구팀의 일입니다.")
    return lines


def render_console(result):
    """콘솔 리포트를 줄 목록으로."""
    bundle = result.bundle
    first, last = bundle.date_span()
    lines = []
    lines.append("[정보] 로그 워크북 %d개 · 시트 %d장 · 데이터 %s행 (%s ~ %s)"
                 % (bundle.n_workbooks, bundle.n_sheets, _fmt_int(bundle.n_data_rows),
                    fmt_dot(first) or "?", fmt_dot(last) or "?"))
    lines.append("[정보] 선언된 정의:")
    for line in declaration_lines(result):
        lines.append("       · %s" % line)
    lines.append("")

    for finding in result.findings:
        if finding.level == LEVEL_INFO:
            continue
        lines.append("[%s] %s" % (finding.level, finding.title))
        for text in finding.lines:
            lines.append("       %s" % text)
        lines.append("")

    for finding in result.findings:
        if finding.level != LEVEL_INFO:
            continue
        lines.append("[정보] %s" % finding.title)
        for text in finding.lines:
            lines.append("       %s" % text)
        lines.append("")

    sens = sensitivity_lines(result)
    if sens:
        lines.append("[정보] %s" % sens[0])
        for text in sens[1:]:
            lines.append("     %s" % text)
        lines.append("")

    lines.append(CONFESSION_HEADER)
    for text in confession_lines(result):
        lines.append("  %s" % text)
    lines.append("")

    if not result.bundle.subjects:
        lines.append("읽을 수 있었던 워크북이 하나도 없습니다 — 아무것도 판정하지 않았습니다.")
        lines.append("")
    n_crit = result.count(LEVEL_CRITICAL)
    n_warn = result.count(LEVEL_WARN)
    lines.append("치명 %d건 · 경고 %d건  → 종료코드 %d" % (n_crit, n_warn, result.exit_code))
    if result.exit_code == 3:
        lines.append("못 읽은 파일이 있어 판정하지 않습니다 — 3은 1보다 먼저입니다.")

    _assert_confession("\n".join(lines))
    return lines


def methods_draft_kr(result):
    cfg = result.config
    values = result.adherence_values()
    bundle = result.bundle
    parts = []
    parts.append(
        "앱 훈련 로그는 피험자별 워크북 %d개(시트 %d장, 데이터 %s행, %s~%s)로 수집되었다."
        % (bundle.n_workbooks, bundle.n_sheets, _fmt_int(bundle.n_data_rows),
           fmt_date(bundle.date_span()[0]), fmt_date(bundle.date_span()[1])))
    parts.append("활동일은 **%s**로 정의하였다." % cfg.rule.label())
    parts.append("노출 기간의 분모는 **%s**로 두었다." % cfg.window.label(result.enroll_source))
    if cfg.target_per_week:
        parts.append("처방은 주 %g일로 두고 노출률(%%)을 (활동일수) ÷ (주 %g일 × 관찰주수) × 100 으로 계산하였다."
                     % (cfg.target_per_week, cfg.target_per_week))
    if cfg.cap is not None:
        capped = sum(1 for e in result.exposures if e.capped)
        parts.append("노출률은 %g%%에서 절단하였으며, 절단된 피험자는 %d명이다." % (cfg.cap, capped))
    else:
        parts.append("노출률에 상한 절단을 적용하지 않았다(따라서 100%를 넘는 값이 나타날 수 있다).")
    if values:
        low, high = _iqr(values)
        parts.append("그 결과 노출률 중앙값은 %s%%(IQR %s–%s%%, 범위 %s–%s%%, n=%d)였다."
                     % (_r0(statistics.median(values)), _r0(low), _r0(high),
                        _r0(min(values)), _r0(max(values)), len(values)))
    zero_modules = [m.name for m in result.modules.values() if m.seconds.zero_ratio == 1.0]
    empty_modules = [m.name for m in result.modules.values() if m.status == STATUS_NO_DATA]
    if empty_modules:
        parts.append("%s 모듈은 전 피험자에서 데이터가 0행이었다 — 수행량이 0이었던 것이 아니라 **측정되지 않았다**."
                     % ", ".join("`%s`" % n for n in empty_modules))
    if zero_modules:
        parts.append("%s 모듈은 모든 행의 소요시간이 0초로 기록되어 0과 미기록을 구분할 수 없었고, 평균을 단일 값으로 보고하지 않았다."
                     % ", ".join("`%s`" % n for n in zero_modules))
    if result.comparison and result.comparison["diffs"]:
        cmp_ = result.comparison
        # **방향 주장은 실제로 한 방향일 때만 한다.** 이 문장은 원고에 붙으라고
        # 만든 것이고, 방향이 섞였는데 "전원 같은 방향"이라고 쓰면 거짓이 된다.
        direction = "전원 같은 방향" if cmp_["uniform_sign"] else "방향은 섞여 있음"
        parts.append("손으로 관리한 참여현황 트래커의 `완료일수`는 위 정의로 다시 센 활동일수와 %d/%d명에서 달랐다(차이 %s~%s일, %s)."
                     % (len(cmp_["diffs"]), len(cmp_["diffs"]) + len(cmp_["agree"]),
                        _signed(cmp_["delta_min"]), _signed(cmp_["delta_max"]), direction))
    if result.config.criterion is not None:
        met = sum(1 for e in result.exposures
                  if e.adherence_pct is not None and e.adherence_pct >= result.config.criterion)
        judged = sum(1 for e in result.exposures if e.adherence_pct is not None)
        if judged:
            parts.append("연구팀이 선언한 기준(노출률 %g%% 이상)을 충족한 피험자는 %d/%d명(%s%%)이었다."
                         % (result.config.criterion, met, judged, _r0(100.0 * met / judged)))
    return " ".join(parts)


def methods_draft_en(result):
    cfg = result.config
    bundle = result.bundle
    values = result.adherence_values()
    parts = []
    parts.append(
        "App training logs were exported as %d per-participant workbooks (%d sheets, %s data rows, %s to %s)."
        % (bundle.n_workbooks, bundle.n_sheets, _fmt_int(bundle.n_data_rows),
           fmt_date(bundle.date_span()[0]), fmt_date(bundle.date_span()[1])))
    parts.append("An active day was defined as %s." % cfg.rule.label_en())
    parts.append("The exposure denominator was taken %s." % cfg.window.label_en(result.enroll_source))
    if cfg.target_per_week:
        parts.append("With a prescription of %g days per week, adherence (%%) was computed as "
                     "(active days) / (%g x observed weeks) x 100." % (cfg.target_per_week, cfg.target_per_week))
    if cfg.cap is not None:
        capped = sum(1 for e in result.exposures if e.capped)
        parts.append("Adherence was truncated at %g%%; %d participants were affected." % (cfg.cap, capped))
    else:
        parts.append("No upper truncation was applied, so values above 100% are possible and are "
                     "reported as computed.")
    if values:
        low, high = _iqr(values)
        parts.append("Median adherence was %s%% (IQR %s-%s%%, range %s-%s%%, n=%d)."
                     % (_r0(statistics.median(values)), _r0(low), _r0(high),
                        _r0(min(values)), _r0(max(values)), len(values)))
    empty_modules = [m.name for m in result.modules.values() if m.status == STATUS_NO_DATA]
    zero_modules = [m.name for m in result.modules.values() if m.seconds.zero_ratio == 1.0]
    if empty_modules:
        # `_gloss` 가 이미 "Module 6 (발성훈련)" 을 만든다 — 뒤에 'module(s)' 를
        # 또 붙이면 "Module 6 (발성훈련) module(s)" 가 되어 그대로 붙여 쓸 수 없다.
        parts.append("No data rows were recorded in any workbook for %s; this reflects absence of "
                     "measurement rather than a measured value of zero."
                     % ", ".join(_gloss(result, n) for n in empty_modules))
    if zero_modules:
        parts.append("In %s, every logged duration was zero seconds, so zero could not be "
                     "distinguished from 'not recorded'; no single mean duration is reported for %s."
                     % (", ".join(_gloss(result, n) for n in zero_modules),
                        "it" if len(zero_modules) == 1 else "them"))
    if result.comparison and result.comparison["diffs"]:
        cmp_ = result.comparison
        direction = ("all in the same direction" if cmp_["uniform_sign"]
                     else "in both directions")
        parts.append("The manually maintained participation tracker disagreed with the recomputed active-day "
                     "count for %d of %d participants (difference %s to %s days, %s)."
                     % (len(cmp_["diffs"]), len(cmp_["diffs"]) + len(cmp_["agree"]),
                        _signed(cmp_["delta_min"]), _signed(cmp_["delta_max"]), direction))
    if result.config.criterion is not None:
        met = sum(1 for e in result.exposures
                  if e.adherence_pct is not None and e.adherence_pct >= result.config.criterion)
        judged = sum(1 for e in result.exposures if e.adherence_pct is not None)
        if judged:
            parts.append("%d of %d participants (%s%%) met the criterion of >=%g%% adherence declared by "
                         "the study team." % (met, judged, _r0(100.0 * met / judged),
                                             result.config.criterion))
    return " ".join(parts)


def render_markdown(result, console_lines):
    """`노출량점검.md` 전문."""
    out = ["# 훈련 노출량 점검 (doseaudit)", ""]
    out.append("이 리포트는 **선언된 정의로 다시 센 결과**입니다. 순응/비순응을 판정하지 않고,")
    out.append("노출량과 결과의 관계도 보지 않습니다 — 분모가 무엇이었는지를 드러내고 끝납니다.")
    out.append("")
    out.append("## 선언된 정의")
    out.append("")
    for line in declaration_lines(result):
        out.append("- %s" % line)
    out.append("")
    out.append("## 콘솔 리포트 전문")
    out.append("")
    out.append("```")
    out.extend(console_lines)
    out.append("```")
    out.append("")
    out.append("## 모듈별 요약")
    out.append("")
    out.append("| 모듈 | 행수 | 상태 | 전체평균(초) | 0제외평균(초) | 0행비율 |")
    out.append("|---|---:|---|---:|---:|---:|")
    for module in result.modules.values():
        total, nonzero, ratio = module.seconds.triple()
        out.append("| %s | %s | %s | %s | %s | %s |"
                   % (module.name, _fmt_int(module.n_rows), module.status, total, nonzero, ratio))
    out.append("")
    out.append("> 평균은 언제나 **세 값**으로만 보고됩니다. 0행비율이 100%인 모듈의 0제외평균은")
    out.append("> `%s` 이며, 그 모듈의 평균을 단일 숫자로 내보내지 않습니다." % UNCOMPARABLE)
    out.append("")
    if result.sensitivity:
        out.append("## 정의 민감도")
        out.append("")
        out.append("| 바꾼 축 | 정의 | n | 중앙값 | 범위 | 50% 미만 |")
        out.append("|---|---|---:|---:|---|---:|")
        for row in result.sensitivity:
            if row["median"] is None:
                out.append("| %s | %s | 0 | %s | %s | 0 |"
                           % (row["axis"], row["label"], UNCOMPARABLE, UNCOMPARABLE))
                continue
            out.append("| %s | %s%s | %d | %s%% | %s–%s%% | %d |"
                       % (row["axis"], row["label"],
                          " **(선언됨)**" if row["declared"] else "",
                          row["n"], _r0(row["median"]), _r0(row["min"]), _r0(row["max"]),
                          row["below50"]))
        out.append("")
        out.append("어느 값도 권장하지 않습니다. 이 표의 용도는 **정의를 문서에 적지 않으면 이만큼 달라진다**를 보이는 것뿐입니다.")
        out.append("")
    out.append("## Methods 초안 (KR)")
    out.append("")
    out.append(methods_draft_kr(result))
    out.append("")
    out.append("## Methods draft (EN)")
    out.append("")
    out.append(methods_draft_en(result))
    out.append("")
    out.append("## %s" % CONFESSION_HEADER)
    out.append("")
    for line in confession_lines(result):
        stripped = line.strip()
        if stripped.startswith("·"):
            out.append("    - %s" % stripped[1:].strip())
        else:
            out.append("- %s" % stripped)
    out.append("")
    text = "\n".join(out)
    _assert_confession(text)
    return text


def _assert_confession(text):
    if CONFESSION_HEADER not in text:
        raise ReportIntegrityError(
            "리포트에 `%s` 블록이 없습니다 — 자백 없는 리포트는 '전부 봤다'로 읽힙니다. "
            "출력하지 않습니다." % CONFESSION_HEADER)
