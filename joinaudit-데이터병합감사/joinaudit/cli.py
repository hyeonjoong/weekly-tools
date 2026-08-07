"""joinaudit CLI — 여러 파일을 한 장의 분석용 표로 합치고, 그 과정을 감사한다.

이 파일이 지키는 규칙은 하나다: **확신이 없으면 병합하지 않는다.**
자동 탐지 결과는 언제나 화면 첫 블록에 나오고, 후보가 둘 이상이거나 날짜 형식을
확정할 수 없으면 추측하는 대신 종료코드 3으로 멈추고 사람에게 열 이름을 묻는다.
틀린 표를 자신 있게 내놓는 것이 이 툴이 저지를 수 있는 가장 큰 실패다.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Sequence

from . import __version__
from .dataio import Frame, LoadError, load_table, normalize_numeric_columns
from .detect import EXPLICIT, detect_date, detect_key, detect_visit
from .hygiene import (check_columns, check_key_overlap, check_prefix_conflict,
                      check_ranges, check_timezones, check_units, check_yield)
from .issues import CRITICAL, INFO, WARNING, Issue, IssueLog
from .keys import AliasTable, KeyNormalizer, load_alias_table
from .merge import (FilePlan, Ledger, make_prefix, merge_files,
                    resolve_duplicates, snap_to_base, assign_keys_and_times)
from .report import (OutputError, prepare_out_dir, screen_summary,
                     verify_downstream_schema, write_audit, write_coverage,
                     write_issues, write_merged)
from .spec import Spec, SpecError, load_spec
from .timeline import VisitNormalizer, parse_cutoff

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_WARN = 2
EXIT_UNMERGEABLE = 3

_ALIGNS = ("night", "visit", "date")
_DUP_POLICIES = ("error", "first", "last", "mean")
_HOWS = ("outer", "inner", "left")
_DATE_ORDERS = ("ymd", "dmy", "mdy")


# --------------------------------------------------------------------------
# 인자
# --------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """사용법 오류를 종료코드 **1** 로 끝낸다.

    argparse 기본값은 2인데, 이 툴에서 2는 "경고는 있지만 병합은 됐다"는 뜻이다.
    래퍼 스크립트가 `$? -eq 2` 로 성공을 판정하면, 아무것도 실행되지 않은 오타를
    "경고와 함께 병합 성공"으로 읽게 된다.
    """

    def error(self, message: str) -> "None":       # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_FAIL, f"{self.prog}: 오류: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="joinaudit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "출처가 다른 여러 CSV/TSV/XLSX 를 피험자 × 시점으로 병합하고, "
            "누가 왜 빠졌는지를 증거로 남깁니다."),
        epilog=(
            "예시\n"
            "  joinaudit watch_hrv.csv diary.xlsx isi.csv --align night --out-dir 결과\n"
            "  joinaudit *.csv --key subject_id --dup-policy first --how inner\n"
            "  joinaudit watch_hrv.csv diary.xlsx --inspect   # 무엇을 키로 잡는지만 확인\n"
            "\n종료코드: 0 문제 없음 · 1 실패 · 2 경고 있음(병합은 됨) · 3 병합 불가\n"))
    p.add_argument("files", nargs="*", metavar="파일",
                   help="병합할 표 파일 2개 이상 (CSV/TSV/XLSX)")
    p.add_argument("--key", action="append", default=[], metavar="열|파일=열",
                   help="피험자 ID 열. 생략하면 자동 탐지(결과를 화면에 표시)")
    p.add_argument("--date", action="append", default=[], metavar="열|파일=열",
                   help="날짜/타임스탬프 열")
    p.add_argument("--visit", action="append", default=[], metavar="열|파일=열",
                   help="방문/시점 라벨 열")
    p.add_argument("--visit-label", action="append", default=[],
                   metavar="파일=라벨",
                   help=("시점 열이 없고 **파일 하나가 곧 한 시점**인 자료에 라벨을 "
                         "붙입니다 (예: `--visit-label 설문_4주.csv=W4`). "
                         "--align visit 과 함께 씁니다"))
    p.add_argument("--sheet", action="append", default=[], metavar="시트|파일=시트",
                   help="XLSX 시트 이름 또는 1-기반 번호 (기본: 첫 시트)")
    p.add_argument("--header-row", action="append", default=[],
                   metavar="N|파일=N",
                   help="헤더 행 번호(1-기반). 생략하면 자동 탐지")
    p.add_argument("--date-format", action="append", default=[],
                   metavar="ymd|dmy|mdy|파일=형식",
                   help="날짜 자릿수 해석. 열만 보고 확정할 수 없을 때 지정")
    p.add_argument("--align", choices=_ALIGNS, default="date",
                   help=("시점 정렬 방식 (기본: date). **수면 자료처럼 타임스탬프가 "
                         "자정을 넘기면 반드시 night 를 쓰세요** — date 로 두면 한 "
                         "밤이 두 날짜로 갈라져 정상 자료가 중복 키로 잡힙니다"))
    p.add_argument("--night-cutoff", default="12:00", metavar="HH:MM",
                   help="--align night 의 하루 경계 (기본: 12:00)")
    p.add_argument("--tolerance-days", type=int, default=0, metavar="N",
                   help="기준 파일 시점과의 날짜 허용오차 (기본: 0)")
    p.add_argument("--dup-policy", choices=_DUP_POLICIES, default="error",
                   help="중복 키 처리 (기본: error — 제외하고 보고)")
    p.add_argument("--how", choices=_HOWS, default="outer",
                   help="병합 방식 (기본: outer)")
    p.add_argument("--base", metavar="파일",
                   help="기준 파일 (--how left, --tolerance-days 에서 사용)")
    p.add_argument("--alias", metavar="alias.csv",
                   help="사람이 명시하는 ID 대응표 (열: 파일,원본ID,표준ID)")
    p.add_argument("--spec", metavar="spec.json",
                   help="연구별 규칙 (id_prefixes / visit_aliases / ranges)")
    p.add_argument("--out-dir", default="결과", metavar="폴더",
                   help="결과 폴더 (기본: 현재 폴더 아래 `결과/`)")
    p.add_argument("--prefix", metavar="a,b,c",
                   help="파일별 열 접두어 (기본: 파일 이름)")
    p.add_argument("--long", action="store_true",
                   help="merged.csv 를 long 형식으로 (subject_id,timepoint,variable,value)")
    p.add_argument("--inspect", action="store_true",
                   help="병합하지 않고 파일별 탐지 결과만 출력")
    p.add_argument("--no-key-normalize", action="store_true",
                   help="ID 의 선행 0 제거를 하지 않음 (S01 과 S1 을 다른 사람으로)")
    p.add_argument("--no-auto-prefix", action="store_true",
                   help="공통 접두어 자동 제거를 하지 않음")
    p.add_argument("--unify-id-heads", action="store_true",
                   help=("파일 안에서 상수인 ID 머리말을 떼어 파일 간 표기를 맞춤 "
                         "(S07 / BELL-001-07 / 07 → 7). 서로 다른 코호트를 "
                         "합칠 위험이 있으므로 기본은 꺼져 있음"))
    p.add_argument("--quiet", action="store_true", help="화면 요약을 줄임")
    p.add_argument("--version", action="version",
                   version=f"joinaudit {__version__}")
    return p


def _per_file(values: Sequence[str], labels: Sequence[str],
              option: str) -> Dict[str, str]:
    """`--opt 값` 또는 `--opt 파일=값` 을 {파일라벨: 값} 으로.

    `파일` 은 전체 경로가 아니라 화면에 보이는 파일 이름으로 적을 수 있게 한다.
    어느 파일도 가리키지 않는 이름은 오타이므로 조용히 넘기지 않고 알린다.
    """
    out: Dict[str, str] = {}
    known = {label: label for label in labels}
    for raw in values:
        if "=" in raw:
            target, _, value = raw.partition("=")
            target, value = target.strip(), value.strip()
            matches = [l for l in labels
                       if l == target or os.path.basename(l) == target
                       or os.path.splitext(l)[0] == target]
            if not matches:
                raise ValueError(
                    f"{option} 의 '{target}' 에 해당하는 입력 파일이 없습니다. "
                    f"입력 파일: {', '.join(labels)}")
            for label in matches:
                out[label] = value
        else:
            for label in known:
                out.setdefault(label, raw.strip())
    return out


# --------------------------------------------------------------------------
# 탐지
# --------------------------------------------------------------------------

def _make_prefixes(frames: Sequence[Frame], explicit: Optional[str]) -> List[str]:
    """파일별 열 접두어. 서로 겹치지 않게 만든다."""
    if explicit is not None:
        parts = [p.strip() for p in explicit.split(",")]
        if len(parts) != len(frames):
            raise ValueError(
                f"--prefix 는 입력 파일 수({len(frames)})와 같은 개수여야 합니다"
                f"(지금 {len(parts)}개).")
        if any(not p for p in parts):
            raise ValueError("--prefix 에 빈 값이 있습니다.")
        base = [make_prefix(p) for p in parts]
    else:
        base = [make_prefix(f.label) for f in frames]

    out: List[str] = []
    seen: Dict[str, int] = {}
    for name in base:
        if name in seen:
            seen[name] += 1
            name = f"{name}{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
    return out


def _plan_file(index: int, frame: Frame, prefix: str, align: str,
               opts: Dict[str, Dict[str, str]], issues: IssueLog,
               visits: VisitNormalizer) -> FilePlan:
    """파일 하나의 키/시점 열을 확정한다. 확정 못 하면 blocking 문제를 남긴다."""
    label = frame.label
    key_det = detect_key(frame, opts["key"].get(label))
    plan = FilePlan(index=index, frame=frame, prefix=prefix, key_det=key_det)

    if not key_det.ok:
        issues.add(Issue(
            file=label, kind="키탐지실패", severity=CRITICAL, blocking=True,
            message=key_det.reason,
            advice=f"`--key {label}=열이름` 처럼 파일별로 지정할 수도 있습니다."))
        return plan

    if align == "visit":
        fixed = opts["visit_label"].get(label)
        if fixed and not opts["visit"].get(label):
            # 파일 하나가 곧 한 시점인 자료(`설문_기저.csv` / `설문_4주.csv`).
            plan.time_kind, plan.fixed_label = "fixed", fixed
            canon, known = visits(fixed)
            issues.add(Issue(
                file=label, kind="시점라벨지정", severity=INFO,
                message=(f"이 파일의 모든 행에 시점 '{canon}' 을 부여했습니다"
                         f"(--visit-label {fixed})"),
                advice=("" if known else
                        "이 라벨은 사전 정의표에 없어 표기 그대로 씁니다. "
                        "다른 파일과 붙어야 한다면 `--spec` 의 visit_aliases 에 "
                        "적어 주세요.")))
            return plan
        explicit_visit = opts["visit"].get(label)
        det = detect_visit(frame, explicit_visit, visits)
        if det.ok:
            plan.time_kind, plan.time_col = "visit", det.column
        elif det.ambiguous:
            issues.add(Issue(file=label, kind="시점열모호", severity=CRITICAL,
                             blocking=True, message=det.reason,
                             advice=f"`--visit {label}=열이름` 으로 지정하세요."))
        elif explicit_visit:
            # 사람이 이름을 적었는데 그 열이 없다 — 오타다. `--key`/`--date` 와
            # 똑같이 멈춰야 한다(예전에는 조용히 피험자 단위로 떨어졌다).
            issues.add(Issue(file=label, kind="시점열없음", severity=CRITICAL,
                             blocking=True, message=det.reason))
        else:
            _note_subject_level(plan, frame, key_det.column, issues,
                                "방문/시점 열")
            if not opts["visit_label"].get(label):
                issues.add(Issue(
                    file=label, kind="시점라벨없음", severity=WARNING,
                    message=("--align visit 인데 이 파일에는 시점 열이 없습니다"),
                    advice=(f"파일 하나가 곧 한 시점이라면 "
                            f"`--visit-label {label}=기저` 처럼 라벨을 직접 "
                            "붙이세요. 그러지 않으면 timepoint 가 빈 칸으로 "
                            "남아 하류 툴에 쓸 수 없습니다.")))
        return plan

    # align in ('date', 'night')
    explicit_date = opts["date"].get(label)
    det = detect_date(frame, explicit_date)
    if not det.ok:
        if det.ambiguous:
            issues.add(Issue(file=label, kind="날짜열모호", severity=CRITICAL,
                             blocking=True, message=det.reason,
                             advice=f"`--date {label}=열이름` 으로 지정하세요."))
        elif explicit_date:
            issues.add(Issue(file=label, kind="날짜열없음", severity=CRITICAL,
                             blocking=True, message=det.reason))
        else:
            _note_subject_level(plan, frame, key_det.column, issues, "날짜 열")
        return plan

    plan.time_kind, plan.time_col, plan.date_plan = "date", det.column, det.plan
    forced = opts["date_format"].get(label)
    if plan.date_plan is not None:
        if forced:
            if forced not in _DATE_ORDERS:
                issues.add(Issue(
                    file=label, kind="날짜형식오류", severity=CRITICAL,
                    blocking=True,
                    message=f"--date-format 값이 잘못되었습니다: {forced!r}",
                    advice="ymd / dmy / mdy 중 하나여야 합니다."))
            else:
                plan.date_plan.order = forced
                plan.date_plan.ambiguous = False
                plan.date_plan.note = f"--date-format {forced} 으로 지정됨"
        elif plan.date_plan.ambiguous:
            issues.add(Issue(
                file=label, kind="날짜형식모호", severity=CRITICAL, blocking=True,
                key=det.column or "",
                message=(f"'{det.column}' 열의 날짜 형식을 확정할 수 없습니다 — "
                         + plan.date_plan.note),
                advice=(f"`--date-format {label}=dmy` 처럼 지정하세요. "
                        "추측해서 붙이면 하루~한 달씩 어긋납니다.")))
        elif not plan.date_plan.candidates:
            issues.add(Issue(
                file=label, kind="날짜형식혼재", severity=CRITICAL, blocking=True,
                key=det.column or "",
                message=(f"'{det.column}' 열을 한 가지 날짜 형식으로 설명할 수 "
                         "없습니다(형식이 섞여 있습니다)"),
                advice="원본에서 날짜 표기를 한 가지로 통일한 뒤 다시 실행하세요."))
    return plan


def _note_subject_level(plan: FilePlan, frame: Frame, key_column: Optional[str],
                        issues: IssueLog, what: str) -> None:
    """시점 열이 없는 파일 — 피험자 단위로 모든 시점에 붙는다는 사실을 알린다."""
    plan.time_kind = "none"
    subjects = len({v.strip() for v in frame.column(key_column or frame.header[0])
                    if v.strip()})
    per_subject = frame.nrows / subjects if subjects else 0
    severity = INFO if per_subject <= 1.0 else WARNING
    extra = ("" if severity == INFO else
             " 피험자당 여러 행이 있으므로 중복 키로 걸립니다 — "
             "이 파일의 시점 열을 `--visit`/`--date` 로 알려 주거나 "
             "`--align` 을 바꾸세요.")
    issues.add(Issue(
        file=frame.label, kind="피험자단위파일", severity=severity,
        message=(f"{what}을 찾지 못해 피험자 단위로 취급합니다"
                 f"({frame.nrows}행 / 피험자 {subjects}명)." + extra),
        advice="이 파일의 값은 해당 피험자의 모든 시점 행에 같은 값으로 붙습니다."))


# --------------------------------------------------------------------------
# --inspect
# --------------------------------------------------------------------------

def _inspect_text(plans: Sequence[FilePlan], issues: IssueLog,
                  rerun: str = "") -> str:
    out: List[str] = ["[탐지 결과] 병합은 하지 않습니다.", ""]
    for plan in plans:
        frame = plan.frame
        out.append(f"● {frame.label}")
        out.append(f"    행 {frame.nrows} · 열 {len(frame.header)} · "
                   f"인코딩 {frame.encoding} · 구분자 {frame.delimiter!r}"
                   + (f" · 시트 {frame.sheet}" if frame.sheet else ""))
        out.append(f"    헤더 행: 원본 {frame.header_row_index + 1}행")
        key = plan.key_det
        out.append(f"    피험자 키: {key.column or '(찾지 못함)'} — {key.reason}")
        if key.ok:
            values = [v.strip() for v in frame.column(key.column) if v.strip()]
            uniq = sorted(set(values))
            out.append(f"      고유 ID {len(uniq)}개, 예: "
                       + ", ".join(uniq[:6]) + (" ..." if len(uniq) > 6 else ""))
        if plan.time_kind == "date":
            plan_note = plan.date_plan.note if plan.date_plan else ""
            out.append(f"    날짜 열: {plan.time_col} — {plan_note}")
            if plan.date_plan:
                dp = plan.date_plan
                out.append(f"      파싱 성공 {dp.parsed}행 / 실패 {dp.failed}행 / "
                           f"시각 포함 {dp.has_time}행")
        elif plan.time_kind == "fixed":
            out.append(f"    시점: --visit-label 로 파일 전체에 "
                       f"'{plan.fixed_label}' 부여")
        elif plan.time_kind == "visit":
            out.append(f"    시점 열: {plan.time_col}")
            values = sorted({v.strip() for v in frame.column(plan.time_col)
                             if v.strip()})
            out.append("      라벨: " + ", ".join(values[:10])
                       + (" ..." if len(values) > 10 else ""))
        else:
            out.append("    시점 열: 없음 → 피험자 단위로 붙습니다")
        out.append(f"    열 목록: " + ", ".join(frame.header[:12])
                   + (" ..." if len(frame.header) > 12 else ""))
        out.append("")
    blocking = issues.blocking
    if blocking:
        out.append("[!] 이대로는 병합할 수 없습니다:")
        for item in blocking:
            out.append(f"    · {item.file}: {item.message}")
            if item.advice:
                out.append(f"      → {item.advice}")
    else:
        out.append("탐지 결과가 맞다면 --inspect 만 빼고 그대로 다시 실행하세요:")
        out.append("  " + rerun)
    return "\n".join(out)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    issues = IssueLog()

    if len(args.files) < 2 and not args.inspect:
        parser.print_usage(sys.stderr)
        print("joinaudit: 병합할 파일을 2개 이상 지정하세요. "
              "(파일 하나의 구조만 보려면 --inspect)", file=sys.stderr)
        return EXIT_FAIL
    if not args.files:
        parser.print_usage(sys.stderr)
        print("joinaudit: 입력 파일이 없습니다.", file=sys.stderr)
        return EXIT_FAIL
    if args.tolerance_days < 0:
        print("joinaudit: --tolerance-days 는 0 이상이어야 합니다.", file=sys.stderr)
        return EXIT_FAIL

    try:
        cutoff = parse_cutoff(args.night_cutoff)
    except ValueError as exc:
        print(f"joinaudit: --night-cutoff: {exc}", file=sys.stderr)
        return EXIT_FAIL
    cutoff_text = cutoff.strftime("%H:%M")

    # -- 스펙 / 별칭표 --------------------------------------------------
    spec = Spec()
    if args.spec:
        try:
            spec = load_spec(args.spec)
        except SpecError as exc:
            print(f"joinaudit: {exc}", file=sys.stderr)
            return EXIT_FAIL
    alias = AliasTable()
    if args.alias:
        try:
            alias = load_alias_table(args.alias)
        except LoadError as exc:
            print(f"joinaudit: 별칭표를 읽을 수 없습니다 — {exc}", file=sys.stderr)
            return EXIT_FAIL

    # -- 파일 적재 -------------------------------------------------------
    labels = [os.path.basename(p) for p in args.files]
    if len(set(labels)) != len(labels):
        # 이름이 같은 파일이 서로 다른 폴더에서 왔다 — 상대 경로로 구분한다.
        labels = list(args.files)
    try:
        sheets = _per_file(args.sheet, labels, "--sheet")
        header_rows = _per_file(args.header_row, labels, "--header-row")
        opts = {
            "key": _per_file(args.key, labels, "--key"),
            "date": _per_file(args.date, labels, "--date"),
            "visit": _per_file(args.visit, labels, "--visit"),
            "visit_label": _per_file(args.visit_label, labels, "--visit-label"),
            "date_format": _per_file(args.date_format, labels, "--date-format"),
        }
    except ValueError as exc:
        print(f"joinaudit: {exc}", file=sys.stderr)
        return EXIT_FAIL

    for label, sheet in sheets.items():
        if sheet and not label.lower().endswith((".xlsx", ".xlsm")):
            print(f"joinaudit: --sheet 는 엑셀 파일에만 쓸 수 있습니다 "
                  f"('{label}' 에 '{sheet}' 이 지정되었습니다).", file=sys.stderr)
            return EXIT_FAIL

    frames: List[Frame] = []
    for path, label in zip(args.files, labels):
        header_row: Optional[int] = None
        raw = header_rows.get(label)
        if raw:
            try:
                header_row = int(raw)
            except ValueError:
                print(f"joinaudit: --header-row 값이 숫자가 아닙니다: {raw!r}",
                      file=sys.stderr)
                return EXIT_FAIL
        try:
            frames.append(load_table(path, label=label,
                                     sheet=sheets.get(label),
                                     header_row=header_row))
        except LoadError as exc:
            print(f"joinaudit: {exc}", file=sys.stderr)
            return EXIT_FAIL

    try:
        prefixes = _make_prefixes(frames, args.prefix)
    except ValueError as exc:
        print(f"joinaudit: {exc}", file=sys.stderr)
        return EXIT_FAIL

    base_index = 0
    if args.base:
        matches = [i for i, l in enumerate(labels)
                   if l == args.base or os.path.basename(l) == os.path.basename(args.base)]
        if not matches:
            print(f"joinaudit: --base '{args.base}' 이(가) 입력 파일에 없습니다. "
                  f"입력: {', '.join(labels)}", file=sys.stderr)
            return EXIT_FAIL
        if len(matches) > 1:
            print(f"joinaudit: --base '{args.base}' 이(가) 여러 입력 파일에 "
                  f"해당합니다({', '.join(labels[i] for i in matches)}). "
                  "전체 경로로 하나만 지정하세요.", file=sys.stderr)
            return EXIT_FAIL
        base_index = matches[0]

    # -- 탐지 -------------------------------------------------------------
    visits = VisitNormalizer(spec.visit_aliases or None)
    plans = [_plan_file(i, frame, prefixes[i], args.align, opts, issues, visits)
             for i, frame in enumerate(frames)]
    for warning in spec.warnings:
        issues.add(Issue(file=os.path.basename(args.spec or ""), kind="스펙경고",
                         severity=WARNING, message=warning,
                         advice="스펙 파일의 항목 이름을 확인하세요."))

    check_timezones(plans, issues)

    if args.inspect:
        rerun = " ".join(p for p in _argv_text(argv).split()
                         if p != "--inspect")
        print(_inspect_text(plans, issues, rerun))
        return EXIT_UNMERGEABLE if issues.blocking else EXIT_OK

    if issues.blocking:
        print("[!] 병합할 수 없습니다 — 추측해서 붙이지 않고 멈춥니다.\n")
        for item in issues.blocking:
            print(f"  · {item.file}: {item.message}")
            if item.advice:
                print(f"    → {item.advice}")
        print("\n먼저 `joinaudit <파일들> --inspect` 로 각 파일의 구조를 확인하세요.")
        try:
            out_dir = prepare_out_dir(args.out_dir, args.files)
            write_issues(issues, os.path.join(out_dir, "문제목록.csv"))
            print(f"\n문제 목록: {os.path.join(out_dir, '문제목록.csv')}")
        except OutputError:
            pass
        return EXIT_UNMERGEABLE

    # -- 출력 폴더(작업 전에 미리 확인해서 헛수고를 막는다) -----------------
    try:
        out_dir = prepare_out_dir(args.out_dir, args.files)
    except OutputError as exc:
        print(f"joinaudit: {exc}", file=sys.stderr)
        return EXIT_FAIL

    # -- 값 열 확정 & 숫자 표기 정규화 --------------------------------------
    for plan in plans:
        frame = plan.frame
        key_col = plan.key_det.column
        plan.value_columns = [c for c in frame.header if c != key_col]
        exclude = [c for c in (key_col, plan.time_col) if c]
        normalize_numeric_columns(frame, exclude=exclude)

    # -- 병합 ---------------------------------------------------------------
    normalizer = KeyNormalizer(prefixes=spec.id_prefixes, alias=alias,
                               zero_pad=not args.no_key_normalize,
                               auto_prefix=not args.no_auto_prefix,
                               unify_heads=args.unify_id_heads)
    # 접두어 제거는 파일 단위 판단이라, 파일마다 다른 접두어가 떨어지면
    # `BELL-001-01` 과 `BELL-002-01` 이 똑같이 `01` 이 되어 남남이 붙는다.
    # 병합을 시작하기 전에 확인하고, 충돌하면 자동 제거를 끈다.
    probed = {plan.label: normalizer.probe_prefix(
        plan.frame.column(plan.key_det.column or plan.frame.header[0]))
        for plan in plans}
    if not check_prefix_conflict(probed, issues):
        normalizer.auto_prefix = False
    ledger = Ledger([f.nrows for f in frames])
    for plan in plans:
        assign_keys_and_times(plan, normalizer, ledger, issues, args.align,
                              cutoff, visits)
    for plan in plans:
        resolve_duplicates(plan, ledger, issues, args.dup_policy, args.align)
    snap_to_base(plans, base_index, args.tolerance_days, ledger, issues)
    result = merge_files(plans, ledger, issues, normalizer, args.how,
                         args.align, base_index)

    # 모르는 방문 라벨은 여기서 한 번에 보고한다(행마다 울지 않는다).
    for plan in plans:
        if plan.unknown_visits:
            labels_text = ", ".join(f"{k}({v}행)" for k, v in
                                    sorted(plan.unknown_visits.items())[:10])
            issues.add(Issue(
                file=plan.label, kind="미등록시점라벨", severity=WARNING,
                key=plan.time_col or "",
                message=f"사전 정의표에 없는 시점 라벨: {labels_text}",
                advice=("추측하지 않고 원본 표기를 그대로 시점으로 썼습니다. "
                        "다른 파일과 붙어야 한다면 `--spec` 의 visit_aliases 에 "
                        "적어 주세요.")))

    # -- 위생 점검 -----------------------------------------------------------
    counts = result.ledger.counts()
    check_yield(plans, issues, len(result.rows),
                counts.get("피험자미매칭", 0) + counts.get("시점미매칭", 0),
                result.ledger.total)
    check_key_overlap(plans, issues, unify_heads=args.unify_id_heads)
    check_columns(plans, issues)
    check_ranges(plans, spec.ranges, issues)
    check_units(plans, issues)

    # -- 산출물 --------------------------------------------------------------
    merged_path = os.path.join(out_dir, "merged.csv")
    try:
        write_merged(result, merged_path, long_format=args.long)
        schema_problems = verify_downstream_schema(merged_path)
        if schema_problems:
            for problem in schema_problems:
                issues.add(Issue(
                    file="merged.csv", kind="스키마검증실패", severity=CRITICAL,
                    message=problem,
                    advice="하류 툴(statwise/table1/longistat)에 그대로 넣지 마세요."))
        write_coverage(result, os.path.join(out_dir, "키매칭표.csv"))
        write_audit(result, issues, os.path.join(out_dir, "병합감사.md"),
                    argv_text=_argv_text(argv), cutoff_text=cutoff_text,
                    dup_policy=args.dup_policy, tolerance=args.tolerance_days,
                    spec_lines=spec.describe(), schema_problems=schema_problems)
        # 문제목록은 위 검증 결과까지 담아야 하므로 마지막에 쓴다.
        write_issues(issues, os.path.join(out_dir, "문제목록.csv"))
    except OutputError as exc:
        print(f"joinaudit: {exc}", file=sys.stderr)
        return EXIT_FAIL

    exit_code = issues.exit_code()
    if result.ledger_error:
        exit_code = max(exit_code, EXIT_WARN)

    if not args.quiet:
        print(screen_summary(result, issues, cutoff_text, args.dup_policy,
                             args.tolerance_days, out_dir, exit_code,
                             schema_problems))
    else:
        print(f"joinaudit: 피험자 {len(result.subjects)}명 / {len(result.rows)}행 → "
              f"{merged_path} (종료코드 {exit_code})")
    return exit_code


def _argv_text(argv: Optional[Sequence[str]]) -> str:
    parts = list(argv) if argv is not None else sys.argv[1:]
    return "joinaudit " + " ".join(
        (f'"{p}"' if " " in p else p) for p in parts)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
