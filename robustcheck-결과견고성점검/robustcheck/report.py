"""리포트와 산출 CSV.

**불변식 1: 커버리지 자백 없이는 리포트를 내보내지 않는다.**
`_require_confession()` 이 마지막 관문이고, 자백 블록의 필수 문장이 하나라도
빠지면 `ReportIntegrityError` 로 막힌다. 리포트가 조금 못생겨지는 것보다
"몇 개를 왜 못 돌렸는지 모르는 리포트"가 훨씬 나쁘다.

**불변식 2: 정렬은 뒤집힘 여부 순이지 유의성 순이 아니다.**
정렬은 `analyze.Analysis.ordered` 가 정하고, 이 모듈은 그 순서를 바꾸지 않는다.
"""

import csv
import io
import math
import os
from typing import Dict, List, Optional, Sequence

from . import __version__
from .analyze import Analysis, Judged
from .effects import effect_grade
from .loo import LOO_RULE_TEXT, LooEntry, LooRun
from .prep import PIPELINE_ORDER
from .safety import csv_safe
from .scenarios import effect_family
from .sentences import draft_english, draft_korean
from .verdict import CRITICAL, MULTIPLICITY_NOTE, WARNING, grade_formula_text

__all__ = [
    "ReportIntegrityError",
    "OUT_REPORT",
    "OUT_SCENARIOS",
    "OUT_SUBJECTS",
    "OUT_ISSUES",
    "render_report",
    "render_markdown",
    "render_scenarios_csv",
    "render_subjects_csv",
    "render_issues_csv",
    "NO_BEST_NOTE",
    "CONFESSION_HEADER",
]

OUT_REPORT = "견고성점검.md"
OUT_SCENARIOS = "시나리오표.csv"
OUT_SUBJECTS = "피험자영향.csv"
OUT_ISSUES = "문제목록.csv"

CONFESSION_HEADER = "[커버리지 자백]"

NO_BEST_NOTE = (
    "이 툴은 '가장 유의한 조합'을 추천하지 않는다. 정렬 기준은 뒤집힘 여부이지 "
    "유의성이 아니다. 여기 나온 조합 중 마음에 드는 것을 골라 논문에 쓰는 것은 "
    "p-해킹이다."
)

_SUBJECT_NOTE = (
    "여기 나열된 피험자는 '빼야 할 사람'이 아니다. 뺄 근거는 사전에 정한 "
    "규칙(프로토콜·이상치 정의)뿐이며, 결과를 보고 정하면 안 된다."
)

_MAX_LISTED = 12


class ReportIntegrityError(Exception):
    """커버리지 자백이 빠진 리포트를 내보내려 할 때."""


def _require_confession(text: str) -> str:
    missing = [marker for marker in (
        CONFESSION_HEADER, "총 ", "계산 ", "건너뜀 ", LOO_RULE_TEXT,
        MULTIPLICITY_NOTE, NO_BEST_NOTE,
    ) if marker not in text]
    if missing:
        raise ReportIntegrityError(
            "커버리지 자백 블록이 불완전해 리포트를 출력하지 않습니다. 누락: %s"
            % " / ".join(repr(m[:40]) for m in missing)
        )
    return text


# ------------------------------------------------------------------ 서식


def _fmt_alpha(alpha: float) -> str:
    """`%.3f` 로는 --alpha 0.0001 이 `.000` 으로 찍혀 0 처럼 보인다."""
    return ("%g" % alpha).lstrip("0") or "0"


def fmt_p(p: float) -> str:
    if p is None or math.isnan(p):
        return "NA"
    if p < 0.001:
        return "<.001"
    return ("%.3f" % p).lstrip("0")


def fmt_delta(value: float) -> str:
    if value is None or math.isnan(value):
        return "NA"
    if value != 0.0 and abs(value) < 0.0005:
        # `+.000` 은 "변화가 없다"로 읽힌다. 아주 작은 변화는 지수로 보인다.
        return "%+.1e" % value
    text = "%+.3f" % value
    return text.replace("+0.", "+.").replace("-0.", "-.")


def fmt_effect(value: float) -> str:
    if value is None or math.isnan(value):
        return "NA"
    return "%+.3f" % value


def fmt_stat(value: float) -> str:
    if value is None or math.isnan(value):
        return "NA"
    return "%.3f" % value


def _where(analysis: Analysis, filename: str) -> str:
    """"`시나리오표.csv` 참조" 라고 쓰되, 파일을 안 쓴 실행이면 그렇게 말한다."""
    if analysis.writes_files:
        return "%s 참조" % filename
    return "--no-files 로 돌려 %s 를 남기지 않았다" % filename


def _axes_label(analysis: Analysis, judged: Judged) -> str:
    return judged.axes.label(include_missing=analysis.spec.design == "paired")


def _reason_kind(reason: str) -> str:
    """제외 사유를 사람이 읽는 큰 분류로 줄인다."""
    if reason.startswith("이상치"):
        return "이상치"
    if reason.startswith("결측") or "결측" in reason:
        return "결측"
    if reason.startswith("LOCF"):
        return "LOCF 불가"
    if "두 시점" in reason:
        return "결측"
    return reason


def _excluded_text(excluded: Sequence, limit: int = 6) -> str:
    """`제외 3명: 이상치 S018, S007 · 결측 S024` 형태."""
    grouped: Dict[str, List[str]] = {}
    for sid, reason in excluded:
        grouped.setdefault(_reason_kind(reason), []).append(sid)
    parts = []
    for kind in sorted(grouped, key=lambda k: (-len(grouped[k]), k)):
        ids = grouped[kind]
        shown = ", ".join(ids[:limit])
        more = "" if len(ids) <= limit else " 외 %d명" % (len(ids) - limit)
        parts.append("%s %s%s" % (kind, shown, more))
    return "제외 %d명: %s" % (len(excluded), " · ".join(parts))


# ------------------------------------------------------------- 본문 조립


def _header_lines(analysis: Analysis) -> List[str]:
    ds = analysis.dataset
    spec = analysis.spec
    lines = [
        "robustcheck %s — 결과 견고성 점검" % __version__,
        "입력: %s (%d행 / %d명 / 인코딩 %s)"
        % (os.path.basename(ds.path), ds.n_rows, len(ds.subjects), ds.encoding),
    ]
    if ds.dropped_no_id:
        lines.append("      ※ ID 가 빈 행 %d개는 제외했다." % ds.dropped_no_id)
    for column, count in sorted(ds.unreadable_cells.items()):
        lines.append("      ⚠ '%s' 열에서 숫자로 읽지 못한 칸 %d개를 결측 처리했다 "
                     "— 유럽식 소수점(3,14)이나 단위가 붙어 있지 않은지 확인하세요."
                     % (column, count))
    if ds.ragged_rows:
        lines.append("      ⚠ 헤더와 칸 수가 다른 행 %d개를 채우거나 잘라 읽었다 — "
                     "따옴표 없는 콤마가 값을 밀어냈을 수 있다." % ds.ragged_rows)
    if spec.timepoint:
        lines.append("      ※ --timepoint %s=%s 로 한 시점만 골라 읽었다."
                     % spec.timepoint)
    detail = spec.label
    if spec.design == "two-group" and ds.group_levels:
        base = analysis.baseline
        if base is not None and base.computed:
            detail = "two-group, %s(%s n=%d, %s n=%d), value=%s" % (
                spec.group, ds.group_levels[0], base.n_a,
                ds.group_levels[1], base.n_b, spec.value)
            if spec.covariate:
                detail += ", 기저보정=%s" % spec.covariate
    lines.append("주 분석: %s" % detail)
    lines.append("alpha = %s · 처리 순서: %s" % (_fmt_alpha(spec.alpha),
                                              PIPELINE_ORDER))
    return lines


def _baseline_lines(analysis: Analysis) -> List[str]:
    base = analysis.baseline
    family = effect_family(analysis.spec.design)
    if base is None or not base.computed:
        reason = base.skip_reason if base is not None else "기준선 시나리오 없음"
        return ["[기준선]  계산 불가 — %s" % reason,
                "          주 분석 자체가 돌지 않으면 흔들어 볼 것도 없다."]
    test = base.test
    df_text = "" if test.df is None else "(df = %.2f)" % test.df
    return [
        "[기준선]  %s = %s %s, p = %s, 효과크기 = %s (%s), n = %d"
        % (test.name, fmt_stat(test.statistic), df_text, fmt_p(test.p),
           fmt_effect(base.effect), effect_grade(base.effect, family), base.n),
        "          ※ 이 값은 기준점일 뿐이다. 아래는 전부 이 값 대비 *변화*다.",
        "          ※ 이 툴은 검정을 골라 주지 않는다 — 주 분석은 당신이 명시한 그대로다.",
    ]


def _flip_line(analysis: Analysis, judged: Judged) -> List[str]:
    result = judged.result
    codes = " / ".join("%s %s (%s)" % (f.code, f.label, f.detail) for f in judged.flips)
    head = "  %s  %s → %s" % (judged.severity, _axes_label(analysis, judged), codes)
    same_scale = result.axes.log == analysis.baseline.axes.log
    detail = "        n = %d, p = %s (%s), 효과크기 = %s (%s)" % (
        result.n, fmt_p(result.p), fmt_delta(result.p - analysis.baseline.p),
        fmt_effect(result.effect),
        fmt_delta(result.effect - analysis.baseline.effect) if same_scale
        else "척도 다름 — 차이 계산 안 함")
    lines = [head, detail]
    if result.excluded:
        lines.append("        %s" % _excluded_text(result.excluded))
    if result.imputed:
        lines.append("        대체된 값 %d건" % result.imputed)
    return lines


def _baseline_is_significant(analysis: Analysis) -> bool:
    base = analysis.baseline
    return (base is not None and base.computed
            and not math.isnan(base.p) and base.p < analysis.spec.alpha)


def _split_for_listing(analysis: Analysis):
    """기준선이 **비유의**일 때는 ② 만 있는 시나리오를 한 줄로 접는다.

    귀무 결과에 대고 "이렇게 하면 p = .029, 저렇게 하면 p = .014" 를 유의성
    순서로 늘어놓으면, 정렬 기준이 아무리 결백해도 그 목록 자체가 곧
    "유의하게 만드는 방법 메뉴"다. 개수만 알리고 값은 CSV 로 보낸다.
    """
    if _baseline_is_significant(analysis):
        return analysis.flipped, []
    listed, collapsed = [], []
    for judged in analysis.flipped:
        # ② 가 섞여 있으면 접는다. ②+④ 를 펼쳐 두면 p 값이 그대로 인쇄돼
        # 접은 의미가 없어진다(실측: 접힌 실행 51건 중 33건에서 p 가 새어 나갔다).
        if any(f.code == "②" for f in judged.flips):
            collapsed.append(judged)
        else:
            listed.append(judged)
    return listed, collapsed


def _collapsed_lines(collapsed: List[Judged]) -> List[str]:
    return [
        "  ② 비유의 → 유의: %d건. **개수만 알린다.**" % len(collapsed),
        "     기준선이 비유의인데 유의해지는 조합의 p 값을 순서대로 늘어놓으면,",
        "     그 목록이 곧 '유의하게 만드는 방법'이 된다. 값은 산출 CSV 에만 남긴다.",
        "     (이 실행이 --no-files 라면 값은 어디에도 남지 않는다.)",
        "     이 결과가 뜻하는 바는 하나다 — **결론이 분석 선택에 민감하다.**",
    ]


def _scenario_lines(analysis: Analysis) -> List[str]:
    verdict = analysis.verdict
    lines = [
        "[시나리오 전수 대조]  %d개 중 %d개 계산 / %d개 건너뜀"
        % (analysis.total, analysis.computed, analysis.skipped),
        "  축 조합: %s" % analysis.grid_text,
    ]
    if analysis.undecidable_reason:
        lines.append("  판정불가 상태라 뒤집힘을 세지 않았다.")
        return lines
    lines.append(
        "  뒤집힘 ...... %d건 (치명 %d · 경고 %d)"
        % (len(analysis.flipped), verdict.critical_scenarios,
           verdict.warning_scenarios)
    )
    lines.append("  ※ 정렬은 **뒤집힘 여부** 순이다. 유의성 순으로 정렬하지 않는다.")
    lines.append("")
    if not analysis.flipped:
        lines.append("  뒤집힌 조합이 없다. 계산된 %d개 명세 모두에서 **유의성 판정**이 "
                     "기준선과 같았다." % analysis.computed)
        shifts = analysis.silent_effect_shifts
        if shifts:
            lines.append("  다만 기준선도 시나리오도 비유의인 채로 효과크기가 크게 "
                         "달라진 명세가 %d개 있다(뒤집힘으로 세지 않음): %s"
                         % (len(shifts),
                            " / ".join(_axes_label(analysis, j) for j in shifts[:3])))
        return lines

    listed, collapsed = _split_for_listing(analysis)
    for judged in listed[:_MAX_LISTED]:
        lines.extend(_flip_line(analysis, judged))
    if len(listed) > _MAX_LISTED:
        lines.append("  … 외 %d건 (%s)"
                     % (len(listed) - _MAX_LISTED, _where(analysis, OUT_SCENARIOS)))
    if collapsed:
        lines.append("")
        lines.extend(_collapsed_lines(collapsed))
    unchanged = analysis.computed - len(analysis.flipped) - 1
    if unchanged > 0:
        lines.append("")
        lines.append("  뒤집힘 없음: %d건 (%s)"
                     % (unchanged, _where(analysis, OUT_SCENARIOS)))
    return lines


def _loo_entry_line(analysis: Analysis, entry: LooEntry) -> str:
    base = analysis.loo_baseline.reference
    hide = (not _baseline_is_significant(analysis)
            and any(f.code == "②" for f in entry.flips))
    if hide:
        # 기준선이 비유의인데 "이 사람을 빼면 p = .045" 를 찍으면, 그게 곧
        # 누구를 빼야 유의해지는지 알려 주는 것이다. 값은 CSV 에만 남긴다.
        return ("    %s 제외 → 유의해짐 (값은 %s — 여기 적지 않는다)"
                % (entry.sid, _where(analysis, OUT_SUBJECTS)))
    mark = ""
    if entry.solo_flip:
        mark = "   ⚠ 단독으로 결론을 뒤집음"
    elif entry.flips:
        mark = "   (경고: %s)" % entry.flips[0].label
    return "    %s 제외 → p %s → %s (%s)%s" % (
        entry.sid, fmt_p(base.p), fmt_p(entry.p), fmt_delta(entry.delta_p), mark)


def _loo_lines(analysis: Analysis) -> List[str]:
    if analysis.loo_baseline is None:
        lines = ["[Leave-one-out]  돌리지 않았다."]
        lines.extend("  ※ %s" % n for n in analysis.loo_notes)
        return lines
    run = analysis.loo_baseline
    computed = sum(1 for e in run.entries if e.computed)
    lines = [
        "[Leave-one-out]  %d명 전수 (기준선, 계산 %d) + 뒤집힘 시나리오 %d개 재실행"
        % (len(run.entries), computed, len(analysis.loo_extra)),
        "  가장 영향력 큰 피험자 (|Δp| 순, 최대 5명)",
    ]
    top = run.top(5)
    if not top:
        lines.append("    (계산 가능한 leave-one-out 결과가 없다)")
    for entry in top:
        lines.append(_loo_entry_line(analysis, entry))
    solo = run.solo_flippers
    if solo:
        lines.append("  ⚠ %d명 중 %d명이 단독으로 결론을 뒤집는다: %s%s"
                     % (len(run.entries), len(solo),
                        ", ".join(e.sid for e in solo[:8]),
                        "" if len(solo) <= 8 else " 외 %d명" % (len(solo) - 8)))
    else:
        lines.append("  단독으로 결론을 뒤집는 피험자는 없다.")
    warned = run.warned
    if warned:
        lines.append("  경고 수준(② 또는 ④)으로 결론을 흔드는 피험자: %d명 — %s%s"
                     % (len(warned), ", ".join(e.sid for e in warned[:8]),
                        "" if len(warned) <= 8 else " 외 %d명" % (len(warned) - 8)))
    skipped = [e for e in run.entries if not e.computed]
    if skipped:
        lines.append("  계산 불가 %d명 (사유: %s)"
                     % (len(skipped),
                        ", ".join(sorted({e.skip_reason for e in skipped}))))
    if analysis.loo_extra:
        with_flippers = [e for e in analysis.loo_extra if e.solo_flippers]
        lines.append("  뒤집힘 시나리오 %d개에서도 leave-one-out 을 돌렸다 — "
                     "**참고용**이고, 등급은 위의 기준선 결과로만 매긴다."
                     % len(analysis.loo_extra))
        if not with_flippers:
            lines.append("    그 시나리오들 안에서 단독으로 결론을 뒤집는 피험자는 "
                         "없었다.")
        show_p = _baseline_is_significant(analysis)
        for extra in with_flippers:
            solos = extra.solo_flippers
            names = ", ".join(e.sid for e in solos[:4])
            if len(solos) > 4:
                names += " 외 %d명" % (len(solos) - 4)
            lines.append("    · %s%s → 그 시나리오 안에서 결론을 뒤집는 피험자 "
                         "%d명 — %s"
                         % (extra.axes.label(
                             include_missing=analysis.spec.design == "paired"),
                            (" (p = %s)" % fmt_p(extra.reference.p)) if show_p else "",
                            len(solos), names))
    lines.append("  ※ %s" % _SUBJECT_NOTE)
    for note in analysis.loo_notes:
        lines.append("  ※ %s" % note)
    return lines


def _confession_lines(analysis: Analysis) -> List[str]:
    lines = [
        CONFESSION_HEADER,
        "  총 %d / 계산 %d / 건너뜀 %d"
        % (analysis.total, analysis.computed, analysis.skipped),
    ]
    if analysis.coverage:
        width = max(len(k) for k in analysis.coverage)
        for reason in sorted(analysis.coverage,
                             key=lambda r: (-analysis.coverage[r], r)):
            pad = "." * max(3, 34 - width)
            lines.append("    %s %s %d" % (reason.ljust(width), pad,
                                           analysis.coverage[reason]))
    else:
        lines.append("    (건너뛴 시나리오 없음)")
    notes: List[str] = []
    for judged in analysis.judged:
        for note in judged.result.notes:
            if note not in notes:
                notes.append(note)
    for note in notes[:6]:
        lines.append("  ※ %s" % note)
    if len(notes) > 6:
        lines.append("  ※ … 외 %d건 (%s 의 비고 열 — %s)"
                     % (len(notes) - 6, OUT_SCENARIOS,
                        "기록됨" if analysis.writes_files else "이번엔 안 남김"))
    identical = _identical_to_baseline(analysis)
    if identical:
        lines.append("  ※ 기준선과 **완전히 같은 결과**가 나온 시나리오 %d개: %s. "
                     "규칙이 아무도 배제하지 않아 사실상 같은 분석이다."
                     % (len(identical), " / ".join(identical[:4])))
    lines.append("  ※ %s" % LOO_RULE_TEXT)
    lines.append("  ※ %s" % MULTIPLICITY_NOTE)
    lines.append("  ※ %s" % NO_BEST_NOTE)
    return lines


def _identical_to_baseline(analysis: Analysis) -> List[str]:
    """기준선과 유지 피험자·통계량이 완전히 같은(= 규칙이 무력했던) 시나리오.

    "12개 명세를 흔들어 봤다"고 말하면서 그중 4개가 기준선의 복사본이면 그건
    부풀린 숫자다. 몇 개가 실질적으로 같은 분석이었는지 밝힌다.
    """
    base = analysis.baseline
    if base is None or not base.computed:
        return []
    same: List[str] = []
    for judged in analysis.judged:
        r = judged.result
        if r.axes.is_baseline or not r.computed:
            continue
        if (r.ids == base.ids and r.axes.log == base.axes.log
                and r.axes.test == base.axes.test):
            same.append(_axes_label(analysis, judged))
    return same


def _formula_lines(analysis: Analysis) -> List[str]:
    lines = ["[등급 산출식]"]
    lines.extend("  %s" % t for t in grade_formula_text())
    lines.append("  효과크기 등급 경계: d 계열 0.2 / 0.5 / 0.8, r 계열 0.1 / 0.3 / 0.5")
    lines.append("  ③④ 를 세는 조건(오탐 억제를 위해 셋 다 만족해야 한다):")
    lines.append("    · 최소 변화폭 d 0.10 · r 0.05 이상 (경계를 스치는 변화 제외)")
    lines.append("    · 기준선이나 해당 시나리오 중 **하나는 유의** — 양쪽 다 "
                 "비유의면 결론('차이를 확인하지 못했다')이 유지된 것이다")
    lines.append("    · ④ 는 **같은 척도끼리만** — 로그변환 여부가 다르면 "
                 "효과크기 등급을 비교하지 않는다")
    lines.append("    · ③ 은 양쪽 효과크기가 모두 '小' 이상일 때만 (d 0.2 · r 0.1)")
    return lines


def render_report(analysis: Analysis) -> str:
    """콘솔·파일 공통 리포트 본문. 자백이 없으면 여기서 막힌다."""
    blocks: List[List[str]] = [
        _header_lines(analysis),
        _baseline_lines(analysis),
        _scenario_lines(analysis),
        _loo_lines(analysis),
        _confession_lines(analysis),
        _formula_lines(analysis),
        ["판정: %s" % analysis.verdict.summary(),
         "종료코드 %d" % analysis.exit_code],
    ]
    text = "\n\n".join("\n".join(block) for block in blocks)
    return _require_confession(text)


def render_markdown(analysis: Analysis) -> str:
    body = render_report(analysis)
    lines = ["# 결과 견고성 점검 — robustcheck %s" % __version__, "", "```", body,
             "```", ""]
    lines.append("---")
    lines.append("")
    lines.extend(draft_korean(analysis))
    lines.append("")
    lines.extend(draft_english(analysis))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 이 리포트를 읽는 법")
    lines.append("")
    lines.append("- **기준선은 결과가 아니다.** 한 줄만 인쇄되는 이유가 그것이다. "
                 "이 툴은 새 결론을 만들지 않는다.")
    lines.append("- **뒤집힌 조합을 골라 쓰면 안 된다.** " + NO_BEST_NOTE)
    lines.append("- **피험자를 빼는 근거는 사전 규칙뿐이다.** " + _SUBJECT_NOTE)
    lines.append("- 전수 표는 `%s`, 피험자별 영향은 `%s`, 치명·경고만 추린 것은 "
                 "`%s` 에 있다." % (OUT_SCENARIOS, OUT_SUBJECTS, OUT_ISSUES))
    return _require_confession("\n".join(lines))


# ------------------------------------------------------------------ CSV


def _csv(header: Sequence[str], rows: Sequence[Sequence[object]],
         id_columns: Sequence[str] = ()) -> str:
    """CSV 문자열. `id_columns` 로 지정한 열은 숫자 예외를 적용하지 않는다.

    `007` 이나 `+1e5` 같은 피험자 ID 를 숫자로 두면 Excel 이 `7`, `100000` 으로
    바꿔 리포트와 원본 표의 피험자가 어긋난다 — 수식 실행보다 조용하고,
    임상 자료에서는 더 나쁜 사고다.
    """
    id_index = {i for i, name in enumerate(header) if name in id_columns}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(cell, numeric_ok=i not in id_index)
                         for i, cell in enumerate(row)])
    return buffer.getvalue()


SCENARIO_HEADER = [
    "이상치규칙", "결측처리", "검정", "로그변환", "기준선여부", "계산됨",
    "n", "n_군1", "n_군2", "검정명", "검정방식", "통계량", "df", "p", "Δp",
    "비교효과크기", "효과크기등급", "Δ효과크기", "검정고유효과크기", "효과크기이름",
    "판정", "뒤집힘코드", "뒤집힘설명", "제외인원", "제외ID", "대체건수",
    "건너뜀사유", "비고",
]


def render_scenarios_csv(analysis: Analysis) -> str:
    base = analysis.baseline
    family = effect_family(analysis.spec.design)
    rows: List[List[object]] = []
    for judged in analysis.ordered:
        r = judged.result
        axes = r.axes
        computed = r.computed
        have_base = base is not None and base.computed
        verdict_text = ("기준선" if axes.is_baseline else
                        ("건너뜀" if not computed else
                         (judged.severity or "변화없음")))
        rows.append([
            axes.outlier, axes.missing, axes.test, axes.log,
            "Y" if axes.is_baseline else "", "Y" if computed else "N",
            r.n if computed else "", r.n_a if computed else "",
            r.n_b if computed else "",
            r.test.name if computed else "",
            r.test.method if computed else "",
            fmt_stat(r.test.statistic) if computed else "",
            ("%.3f" % r.test.df) if computed and r.test.df is not None else "",
            fmt_p(r.p) if computed else "",
            fmt_delta(r.p - base.p) if computed and have_base else "",
            fmt_effect(r.effect) if computed else "",
            effect_grade(r.effect, family) if computed else "",
            (fmt_delta(r.effect - base.effect)
             if computed and have_base and axes.log == base.axes.log
             else ("척도다름" if computed and have_base else "")),
            fmt_effect(r.native_effect) if computed else "",
            r.native_effect_name if computed else "",
            verdict_text,
            " ".join(f.code for f in judged.flips),
            " / ".join("%s %s" % (f.label, f.detail) for f in judged.flips),
            len(r.excluded) if computed else "",
            ";".join(sid for sid, _ in r.excluded) if computed else "",
            r.imputed if computed else "",
            (r.skip_reason + (" — " + r.skip_detail if r.skip_detail else ""))
            if not computed else "",
            " / ".join(r.notes),
        ])
    return _csv(SCENARIO_HEADER, rows, id_columns=("제외ID",))


SUBJECT_HEADER = [
    "시나리오", "subject_id", "분석포함", "계산됨", "제외시p", "Δp",
    "제외시효과크기", "Δ효과크기", "단독뒤집기", "판정", "뒤집힘코드", "설명",
    "건너뜀사유",
]


def _subject_rows(analysis: Analysis, run: LooRun, label: str) -> List[List[object]]:
    rows: List[List[object]] = []
    ordered = sorted(run.entries,
                     key=lambda e: (0 if e.solo_flip else (1 if e.flips else 2),
                                    -abs(e.delta_p) if e.computed
                                    and not math.isnan(e.delta_p) else 0.0,
                                    e.sid))
    for entry in ordered:
        rows.append([
            label, entry.sid, "Y" if entry.in_analysis else "N",
            "Y" if entry.computed else "N",
            fmt_p(entry.p) if entry.computed else "",
            fmt_delta(entry.delta_p) if entry.computed else "",
            fmt_effect(entry.effect) if entry.computed else "",
            fmt_delta(entry.delta_effect) if entry.computed else "",
            "Y" if entry.solo_flip else "",
            entry.severity or ("건너뜀" if not entry.computed else "변화없음"),
            " ".join(f.code for f in entry.flips),
            " / ".join("%s %s" % (f.label, f.detail) for f in entry.flips),
            entry.skip_reason,
        ])
    return rows


def render_subjects_csv(analysis: Analysis) -> str:
    rows: List[List[object]] = []
    if analysis.loo_baseline is not None:
        rows.extend(_subject_rows(analysis, analysis.loo_baseline, "기준선"))
        for extra in analysis.loo_extra:
            label = extra.axes.label(
                include_missing=analysis.spec.design == "paired")
            rows.extend(_subject_rows(analysis, extra, label))
    return _csv(SUBJECT_HEADER, rows, id_columns=("subject_id",))


ISSUE_HEADER = ["구분", "대상", "등급", "판정", "설명", "권고"]


def render_issues_csv(analysis: Analysis) -> str:
    rows: List[List[object]] = []
    if analysis.undecidable_reason:
        rows.append(["판정불가", "-", CRITICAL, "판정불가",
                     analysis.undecidable_reason,
                     "표본이나 시나리오가 부족합니다. 견고성을 논하기 전에 "
                     "자료를 먼저 확인하세요."])
        return _csv(ISSUE_HEADER, rows, id_columns=("대상",))
    for judged in analysis.flipped:
        r = judged.result
        rows.append([
            "시나리오", _axes_label(analysis, judged), judged.severity,
            " / ".join(f.label for f in judged.flips),
            "p %s → %s, 효과크기 %s → %s%s" % (
                fmt_p(analysis.baseline.p), fmt_p(r.p),
                fmt_effect(analysis.baseline.effect), fmt_effect(r.effect),
                (", 제외 %d명" % len(r.excluded)) if r.excluded else ""),
            "이 조합을 골라 쓰라는 뜻이 아닙니다. 사전에 정한 분석이 "
            "무엇이었는지 확인하고, 민감도 분석으로 함께 보고하세요.",
        ])
    if analysis.loo_baseline is not None:
        for entry in analysis.loo_baseline.solo_flippers:
            rows.append([
                "피험자", entry.sid, CRITICAL, "단독 뒤집기",
                "제외 시 p %s → %s (%s)" % (
                    fmt_p(analysis.loo_baseline.reference.p), fmt_p(entry.p),
                    fmt_delta(entry.delta_p)),
                "이 피험자를 빼라는 뜻이 아닙니다. 결론이 1명에 의존한다는 "
                "사실을 논문에 그대로 보고하세요.",
            ])
        for entry in analysis.loo_baseline.warned:
            rows.append([
                "피험자", entry.sid, WARNING,
                " / ".join(f.label for f in entry.flips),
                "제외 시 p %s → %s (%s)" % (
                    fmt_p(analysis.loo_baseline.reference.p), fmt_p(entry.p),
                    fmt_delta(entry.delta_p)),
                "민감도 분석 문단에 함께 보고하세요.",
            ])
    return _csv(ISSUE_HEADER, rows, id_columns=("대상",))
