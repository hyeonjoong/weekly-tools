"""`--inspect` — 판정하기 **전에**, 어떻게 읽었는지부터 보여 준다.

`--active-day` 를 필수로 두면 진입 장벽이 된다. 그 장벽을 낮추는 것이 이 화면의
일이다: 고를 수 있는 규칙을 **각각의 결과값과 함께** 늘어놓고, 사용자는 보고 고른다.
**고르는 행위 자체가 이 툴의 산출물이다** — 고른 규칙이 Methods 문장에 그대로 들어간다.
"""

import shlex
import statistics

from doseaudit.dose import day_buckets
from doseaudit.logs import SPEC_HEADER
from doseaudit.rules import CONTRAST_RULES, CONTRAST_WINDOWS, ActiveDayRule
from doseaudit.sanitize import safe_text
from doseaudit.values import fmt_dot


def _fmt_int(n):
    return "{:,}".format(int(n))


def render_inspect(bundle, tracker=None, max_files=3, logs_arg="로그폴더/",
                   tracker_arg=None):
    """읽은 결과를 사람이 1초 안에 '잘못 읽었다'를 알 수 있게 인쇄한다."""
    lines = []
    first, last = bundle.date_span()
    lines.append("[점검] 판정하지 않습니다 — 어떻게 읽었는지만 보여 줍니다.")
    lines.append("")
    lines.append("읽은 워크북 %d개 · 시트 %d장 · 데이터 %s행 (%s ~ %s)"
                 % (bundle.n_workbooks, bundle.n_sheets, _fmt_int(bundle.n_data_rows),
                    fmt_dot(first) or "?", fmt_dot(last) or "?"))
    lines.append("기대한 헤더 5열: %s" % " · ".join(SPEC_HEADER))
    lines.append("  → %d장 중 한 장이라도 이 규격과 다르면 판정하지 않고 종료코드 2 로 멈춥니다."
                 % bundle.n_sheets)
    lines.append("")

    lines.append("파일별 (앞 %d개):" % min(max_files, bundle.n_workbooks))
    for subject in bundle.subjects[:max_files]:
        lines.append("  · %s" % subject.filename)
        lines.append("      피험자 코드(파일명 첫 토큰): %s   ← 조인은 이것만 씁니다" % subject.code)
        lines.append("      (파일명의 Login ID 는 읽되 출력하지 않습니다)")
        for name, module in subject.modules.items():
            vendor = ("[요약] 블록 있음(%d개 항목)" % len(module.vendor)) if module.vendor else "[요약] 없음"
            mark = "  ← 데이터 0행" if module.is_empty else ""
            lines.append("      %-10s 데이터 %5s행 · 헤더 = 엑셀 %d행 · %s%s"
                         % (name, _fmt_int(module.n_rows), module.header_row, vendor, mark))
    if bundle.n_workbooks > max_files:
        lines.append("  ... 나머지 %d개도 같은 5열 규격으로 읽혔습니다 (달랐다면 여기까지 오지 못합니다)."
                     % (bundle.n_workbooks - max_files))
        lines.append("  전체 피험자 코드 %d개: %s"
                     % (bundle.n_workbooks,
                        ", ".join(s.code for s in bundle.subjects)))
    empty_everywhere = [name for name in bundle.module_order
                        if all((s.modules.get(name) is None or s.modules[name].is_empty)
                               for s in bundle.subjects)]
    if empty_everywhere:
        lines.append("")
        lines.append("  ※ 모든 워크북에서 데이터가 0행인 모듈: %s"
                     % ", ".join("`%s`" % n for n in empty_everywhere))
        lines.append("    벤더 [요약] 은 이 모듈을 `평균 0` 으로 인쇄합니다 — '0을 측정함'이 아니라 '데이터 없음'입니다.")
    lines.append("")

    if bundle.unreadable:
        lines.append("못 읽은 파일 %d개:" % len(bundle.unreadable))
        for name, reason in bundle.unreadable:
            lines.append("  · %s — %s" % (name, safe_text(reason, 80)))
        lines.append("")
    if bundle.skipped_files:
        lines.append("건너뛴 것 %d개: %s"
                     % (len(bundle.skipped_files),
                        ", ".join("%s(%s)" % (n, r) for n, r in bundle.skipped_files[:5])))
        lines.append("")

    if tracker is not None:
        lines.append("트래커: %d행 · Access Code 로만 조인합니다" % len(tracker.rows))
        if tracker.dropped_columns:
            lines.append("  열지 않은 열(개인식별): %s"
                         % ", ".join(safe_text(c, 30) for c in tracker.dropped_columns))
        if tracker.grid_columns:
            lines.append("  읽지 않은 일자별 격자 열: D1…D%d (%d열) — 기준점이 문서화돼 있지 않습니다"
                         % (tracker.grid_columns, tracker.grid_columns))
        lines.append("")

    lines.append("선언할 수 있는 활동일 규칙과, 각각으로 셌을 때의 결과:")
    lines.append("  ※ 아래는 **창을 적용하기 전**, 워크북 전체에서 센 값입니다.")
    lines.append("    창을 선언하면 그 바깥의 활동일이 빠지므로 실제 판정값은 이보다 작을 수 있습니다.")
    lines.append("  %-16s %-12s %-12s %s" % ("규칙", "활동일수 중앙값(창 적용 전)", "합계", "뜻"))
    for spec in CONTRAST_RULES:
        rule = ActiveDayRule.parse(spec)
        counts = []
        for subject in bundle.subjects:
            buckets = day_buckets(subject)
            counts.append(sum(1 for b in buckets.values() if rule.is_active(b)))
        median = statistics.median(counts) if counts else 0
        lines.append("  %-16s %-12s %-12s %s"
                     % (spec, "%.1f일" % median, "%s일" % _fmt_int(sum(counts)), rule.label()))
    lines.append("")
    lines.append("분모의 창은 **이보다 더 크게** 움직입니다 — 반드시 같이 선언하세요:")
    for spec in CONTRAST_WINDOWS:
        lines.append("  %-16s %s" % (spec, _WINDOW_HINT[spec]))
    lines.append("  %-16s %s" % ("enroll-to-cut", "가입일 → 데이터컷 (`--cut YYYY-MM-DD` 필요)"))
    lines.append("")
    if tracker is None:
        # 안내대로 `--inspect` 부터 실행한 사람은 여기서 트래커를 주지 않았고,
        # 그러면 아래 복사용 줄에도 `--tracker` 가 없어서 **이 툴의 머리 기사**
        # (트래커 `완료일수` 대조)가 통째로 꺼진 채 실행된다. 그 사실을 말해 준다.
        lines.append("트래커를 주지 않았습니다 — `완료일수` 대조가 꺼져 있습니다.")
        lines.append("  `--tracker 참여현황.xlsx` 를 붙이면 손으로 관리한 표와 대조합니다")
        lines.append("  (이 툴이 실데이터에서 30명 중 23명의 불일치를 찾아낸 검사입니다).")
        lines.append("")
    if _pasteable(logs_arg) and _pasteable(tracker_arg):
        lines.append("골랐으면 아래 줄을 그대로 복사해 쓰세요 (규칙만 바꾸면 됩니다):")
        lines.append("")
        for spec in CONTRAST_RULES:
            lines.append("  %s" % _command_line(logs_arg, tracker_arg, spec))
    else:
        lines.append("경로에 제어문자가 들어 있어 복사용 실행 줄을 만들지 않았습니다 —")
        lines.append("잘못 붙여넣으면 엉뚱한 명령이 됩니다. 경로 이름을 먼저 확인하세요.")
        lines.append("선언할 인자: --active-day <규칙> --window <창> --target-per-week N --out-dir 결과/")
    lines.append("")
    lines.append("고른 규칙은 리포트와 Methods 초안 문장에 그대로 박힙니다 — **고르는 것이 산출물입니다.**")
    return lines


#: `--inspect` 에서 창을 고를 때 붙이는 한 줄 설명.
_WINDOW_HINT = {
    "enroll-to-last": "가입일 → 그 사람의 마지막 활동일 (중도 이탈자의 분모가 짧아집니다)",
    "fixed-weeks=8": "가입일부터 고정 8주 (모두에게 같은 길이의 창)",
    "fixed-weeks=12": "가입일부터 고정 12주 (주수 × 7 이 정수 일수여야 합니다)",
}


def _pasteable(path):
    """제어문자가 든 경로는 복사용 줄로 만들지 않는다 (없으면 통과)."""
    if not path:
        return True
    return not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in str(path))


def _command_line(logs_arg, tracker_arg, rule_spec):
    """복사해서 바로 쓸 수 있는 실행 줄 한 개.

    `--active-day` 를 필수로 둔 대가를 여기서 갚는다 — 네 개의 인자를 다시
    타이핑하게 두면, 선언은 결정이 아니라 귀찮은 상용구가 되고 만다.
    """
    parts = ["doseaudit --logs %s" % _quote(logs_arg)]
    if tracker_arg:
        parts.append("--tracker %s" % _quote(tracker_arg))
    else:
        # 자리를 비워 두지 않고 **빈칸으로 남겨** 둔다. 줄에서 아예 빠지면
        # 복사해 쓴 사람은 트래커 대조가 꺼진 줄도 모른 채 실행하게 된다.
        parts.append("--tracker 참여현황.xlsx")
    parts.append("--active-day %s" % rule_spec)
    parts.append("--window enroll-to-cut --cut YYYY-MM-DD")
    parts.append("--target-per-week 3 --out-dir 결과/")
    return " ".join(parts)


def _quote(path):
    """셸에 그대로 붙여도 안전하게 인용한다.

    이 줄은 문서가 **"그대로 복사해 쓰세요"** 라고 말하는 줄이다. 따옴표를
    직접 붙이면 경로 안의 작은따옴표 하나로 명령이 갈라진다(벤더 ZIP 에서
    나온 폴더 이름이 입력이 될 수 있다). `shlex.quote` 에 맡긴다.

    **자르지도 않는다** — 잘린 경로를 붙여넣으면 조용히 다른 곳을 가리킨다.
    """
    return shlex.quote(str(path))
