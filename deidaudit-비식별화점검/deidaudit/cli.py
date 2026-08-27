"""deidaudit CLI — 공유 전 비식별화 점검.

종료코드
    0  치명 0건 — 내보내도 됨
    1  치명 발견
    2  입력/옵션 오류 (경계를 지키는 강제 장치 포함)
    3  판정불가 (파싱 실패·인코딩 불명·검사율 80% 미만·자체검증 실패)

**3 이 1 보다 우선합니다.** 다 못 봤으면 "치명 0건"은 거짓말이기 때문입니다.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .audit import AuditResult, run_audit, safe_sheet_label, sheet_name_has_person
from .coverage import CoverageError
from .findings import CRITICAL
from .kanon import TARGET_K
from .pseudo import (
    EMPTY_SUBJECT_KEY,
    PseudoPlan,
    VerificationError,
    build_plan,
    key_rows,
    normalize_subject,
    transform_table,
    verify_plan,
    verify_transform,
)
from .report import (
    PROBLEM_HEADER,
    RISK_HEADER,
    console_report,
    markdown_report,
    now_string,
    problem_rows,
    risk_rows,
    summary_report,
    verdict_lines,
)
from .safety import (
    PathSafetyError,
    ensure_key_outside,
    ensure_not_input,
    ensure_output_target,
    find_inside,
    is_within,
    lexical,
    needs_escaping,
    real,
    safe_output_name,
    unique_path,
    write_csv,
    write_private_csv,
    write_text,
)
from .tabular import DEFAULT_MAX_BYTES, norm_key

EXIT_OK = 0
EXIT_CRITICAL = 1
EXIT_USAGE = 2
EXIT_UNDETERMINED = 3

# 이 툴이 절대 하지 않는 일 — 파일 병합. joinaudit 의 영역입니다.
_BANNED_FLAGS = {
    "--join", "--merge", "--concat", "--combine", "--on", "--how",
    "--left-join", "--inner-join", "--outer-join", "--append",
}

_DEFAULT_LINK_CANDIDATES = [
    "subject_id", "subjectid", "피험자ID", "피험자번호", "대상자ID", "대상자번호",
    "환자번호", "등록번호", "record_id", "id", "참여자ID", "subject",
]

_DEFAULT_EXPORT_DIRNAME = "내보내기"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deidaudit",
        description=(
            "피험자 단위 CSV/TSV/XLSX 를 밖으로 내보내기 직전에 전수 스캔해서, "
            "직접식별자·자유텍스트에 숨은 인명·엑셀 숨김 시트·문서 메타데이터·"
            "재식별 위험(k)을 파일·시트·행·열까지 찍어 보고합니다."
        ),
        epilog=(
            "예) deidaudit 수면일기.csv UT로그.xlsx --quasi birth,sex,visit_date\n"
            "    deidaudit *.csv --quasi birth,sex --pseudonymize --shift-dates \\\n"
            "              --link-id subject_id --out-dir 내보내기_2026-08-27 \\\n"
            "              --key-out ~/보안/키.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="*", help="검사할 CSV/TSV/XLSX 파일들 (합치지 않습니다)")
    parser.add_argument("--quasi", default="", help="준식별자 열 이름(쉼표 구분). 주면 재식별 위험 k 를 계산합니다")
    parser.add_argument("--link-id", default="", help="피험자 ID 열 이름(쉼표 구분, 파일마다 다르면 여러 개). 파일 간 같은 사람에게 같은 가명을 주기 위해서만 씁니다")
    parser.add_argument("--audit-only", action="store_true", help="감사만 하고 데이터 사본을 만들지 않습니다(기본 동작)")
    parser.add_argument("--pseudonymize", action="store_true", help="피험자 ID 를 결정론적 가명으로 치환한 사본을 만듭니다")
    parser.add_argument("--shift-dates", action="store_true", help="피험자별 고정 오프셋(±180일)으로 날짜를 이동합니다")
    parser.add_argument(
        "--shift-weeks", action="store_true",
        help="날짜 이동량을 7의 배수(±25주)로 맞춰 **요일을 보존**합니다. 수면 연구에서 주중/주말은 공변량입니다 "
             "(대신 오프셋 후보가 360개 → 50개로 줄어 보호 강도는 조금 낮아집니다)",
    )
    parser.add_argument("--drop-columns", default="", help="사본에서 뺄 열 이름(쉼표 구분). 자동 삭제는 하지 않습니다")
    parser.add_argument("--out-dir", default="", help="리포트와 사본을 쓸 폴더")
    parser.add_argument(
        "--report-dir", default="",
        help="점검 리포트를 쓸 폴더. 사본을 만들 때는 기본값이 `<out-dir>_점검리포트` 입니다 — "
             "리포트가 내보낼 폴더와 함께 나가면, 행 번호로 사본과 맞춰 가명이 다시 사람이 됩니다",
    )
    parser.add_argument("--key-out", default="", help="원본ID↔가명ID·날짜 오프셋 매핑표 경로. **--out-dir 밖이어야 합니다**")
    parser.add_argument("--salt", default="", help="가명·오프셋 재현용 솔트(미지정 시 난수 생성 후 키 파일에 기록)")
    parser.add_argument("--prefix", default="P", help="가명 ID 접두사 (기본 P)")
    parser.add_argument("--target-k", type=int, default=TARGET_K, help=f"안전 기준 k (기본 {TARGET_K})")
    parser.add_argument("--max-detail", type=int, default=5000, help="문제목록.csv 에 쓸 최대 행 수 (기본 5000)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="이보다 큰 파일은 읽지 않고 자백합니다")
    parser.add_argument("--quiet", action="store_true", help="콘솔 리포트를 줄입니다(결론만)")
    parser.add_argument("--version", action="version", version=f"deidaudit {__version__}")
    return parser


def _split_list(text: str) -> List[str]:
    return [piece.strip() for piece in text.split(",") if piece.strip()]


def _guard_merge_flags(argv: Sequence[str]) -> Optional[str]:
    """병합 관련 옵션이 오면 경계를 지키기 위해 거부합니다."""
    for arg in argv:
        head = arg.split("=", 1)[0]
        if head in _BANNED_FLAGS:
            return (
                f"'{head}' 는 이 툴에 없습니다. deidaudit 은 파일을 **절대 합치지 않습니다**.\n"
                "  여러 파일을 하나로 합쳐야 한다면 먼저 `joinaudit` 을 쓰세요.\n"
                "  (`--link-id` 는 파일 간 같은 피험자에게 같은 가명을 주기 위한 것일 뿐,\n"
                "   행을 결합하지 않습니다.)"
            )
    return None


def _resolve_inputs(files: Sequence[str]) -> Tuple[List[Path], List[str]]:
    paths: List[Path] = []
    errors: List[str] = []
    seen = set()
    for name in files:
        path = Path(os.path.expanduser(name))
        if not path.exists():
            errors.append(f"입력 파일이 없습니다: {name}")
            continue
        if path.is_dir():
            errors.append(f"폴더는 받지 않습니다(파일을 지정하세요): {name}")
            continue
        try:
            mode = os.stat(str(path)).st_mode
        except OSError as exc:
            errors.append(f"파일 정보를 읽을 수 없습니다 ({exc.__class__.__name__}): {name}")
            continue
        if not stat.S_ISREG(mode):
            # 이름있는 파이프(FIFO)를 읽으려 하면 영원히 멈춥니다.
            errors.append(f"일반 파일이 아닙니다(파이프·소켓·장치는 받지 않습니다): {name}")
            continue
        key = real(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths, errors


def _unknown_columns(result: AuditResult, names: Sequence[str]) -> List[str]:
    """어느 표에도 없는 열 이름을 돌려줍니다(오타로 인한 조용한 유출 방지)."""
    known = {norm_key(col) for info in result.tables for col in info.table.columns}
    known |= {norm_key(col) for info in result.tables for col in info.table.original_columns}
    return [name for name in names if norm_key(name) not in known]


def _column_hint(result: AuditResult, limit: int = 40) -> str:
    """사용 가능한 열 이름 목록. 열 이름 자체가 식별자면 가려서 보여 줍니다."""
    seen: List[str] = []
    for info in result.tables:
        for index in range(len(info.table.columns)):
            col = info.safe_column(index)
            if col not in seen:
                seen.append(col)
    text = ", ".join(seen[:limit])
    return text + (f" … (외 {len(seen) - limit}개)" if len(seen) > limit else "")


def _make_dir(path: Path) -> Optional[str]:
    """폴더를 만들고, 실패하면 사람이 읽을 수 있는 사유를 돌려줍니다."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except NotADirectoryError:
        return f"경로의 중간이 폴더가 아닙니다: {path}"
    except FileExistsError:
        return f"같은 이름의 파일이 이미 있습니다(폴더를 만들 수 없습니다): {path}"
    except PermissionError:
        return f"폴더를 만들 권한이 없습니다: {path}"
    except OSError as exc:
        return f"폴더를 만들 수 없습니다 ({exc.__class__.__name__}): {path}"
    if not path.is_dir():
        return f"폴더가 아닙니다: {path}"
    return None


def _fail(message: str, code: int = EXIT_USAGE) -> int:
    print(f"\n[오류] {message}\n", file=sys.stderr)
    return code


def run(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 본체. 종료코드를 돌려줍니다."""
    argv = list(sys.argv[1:] if argv is None else argv)

    banned = _guard_merge_flags(argv)
    if banned:
        return _fail(banned)

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.files:
        parser.print_help()
        return _fail("검사할 파일을 하나 이상 지정하세요.")

    paths, errors = _resolve_inputs(args.files)
    if errors:
        return _fail("\n  ".join(errors))
    if not paths:
        return _fail("검사할 파일이 없습니다.")

    quasi = _split_list(args.quasi)
    drop_columns = _split_list(args.drop_columns)
    link_ids = _split_list(args.link_id) or list(_DEFAULT_LINK_CANDIDATES)
    link_explicit = bool(_split_list(args.link_id))

    if args.shift_weeks and not args.shift_dates:
        return _fail("--shift-weeks 는 --shift-dates 와 함께 씁니다.")
    export_requested = bool(args.pseudonymize or args.shift_dates or drop_columns)
    if args.audit_only and export_requested:
        return _fail("--audit-only 와 --pseudonymize/--shift-dates/--drop-columns 는 함께 쓸 수 없습니다.")

    out_dir = Path(os.path.expanduser(args.out_dir)) if args.out_dir else None
    key_out = Path(os.path.expanduser(args.key_out)) if args.key_out else None
    report_dir = Path(os.path.expanduser(args.report_dir)) if args.report_dir else None

    if export_requested:
        if out_dir is None:
            return _fail("사본을 만들려면 --out-dir 이 필요합니다.")
        if (args.pseudonymize or args.shift_dates) and key_out is None:
            return _fail(
                "--pseudonymize / --shift-dates 를 쓰려면 --key-out 이 필요합니다.\n"
                "  가명과 날짜 오프셋을 되돌릴 수 없으면 원자료와 대조할 방법이 사라집니다.\n"
                "  키 파일은 반드시 --out-dir **밖**(예: ~/보안/키.csv)에 두세요."
            )
    if key_out is not None and out_dir is not None:
        try:
            ensure_key_outside(key_out, out_dir)
        except PathSafetyError as exc:
            return _fail(str(exc))
    if key_out is not None and out_dir is None:
        return _fail("--key-out 은 --out-dir 과 함께 씁니다.")
    if report_dir is not None and out_dir is None:
        return _fail("--report-dir 은 --out-dir 과 함께 씁니다.")

    if out_dir is not None:
        explicit_report_dir = report_dir is not None
        if report_dir is None:
            # 사본을 만들 때는 리포트를 내보낼 폴더 **밖**에 씁니다.
            # 리포트의 행 번호는 사본의 행과 1:1 로 맞아, 둘이 함께 나가면
            # "가명 P001 = 성이 김, 1988년생, 번호 끝 89" 가 복원됩니다.
            base = lexical(out_dir)
            report_dir = (
                base.parent / f"{base.name}_점검리포트" if export_requested else out_dir
            )
        # **기본값도 반드시 검사합니다.** `--out-dir .` 이나 `--out-dir ..` 은 기본 규칙만으로
        # 리포트를 내보낼 폴더 안에 만들고, 심볼릭 링크를 걸면 명시 지정과 결과가 갈립니다.
        if export_requested and is_within(report_dir, out_dir):
            hint = (
                "  다른 폴더를 지정하세요 (`--report-dir`)."
                if explicit_report_dir
                else "  `--out-dir` 을 상위 폴더가 있는 새 폴더 이름으로 주거나 `--report-dir` 을 직접 지정하세요."
            )
            return _fail(
                "점검 리포트를 쓸 폴더가 --out-dir 안에 있습니다: "
                f"{report_dir}\n"
                "  리포트에는 어느 행에 무엇이 있었는지가 적혀 있어, 사본과 함께 나가면\n"
                "  행 번호를 맞추는 것만으로 가명이 다시 사람이 됩니다.\n" + hint
            )

    if args.target_k < 2:
        return _fail("--target-k 는 2 이상이어야 합니다.")
    if args.max_detail < 1:
        return _fail("--max-detail 은 1 이상이어야 합니다.")

    # ---- 감사 ----
    result = run_audit(paths, quasi, link_ids, args.max_bytes, args.target_k)
    try:
        report_text = console_report(result, args.target_k)
    except CoverageError as exc:
        # 자백 블록을 못 만들어도 **왜 못 읽었는지는 반드시 보여 줍니다.**
        # 사유가 안 보이면 사용자는 고칠 방법이 없습니다.
        if result.coverage.skipped:
            print("읽지 못한 입력:")
            for target, reason in result.coverage.skipped:
                print(f"  · {target} — {reason}")
        return _fail(
            f"커버리지 자백 블록을 만들 수 없어 리포트를 출력하지 않습니다: {exc}\n"
            "  (다 보지 못한 채 '치명 0건'이라고 말하지 않기 위한 안전장치입니다.)",
            EXIT_UNDETERMINED,
        )

    unknown = _unknown_columns(result, quasi)
    if unknown:
        return _fail(
            "--quasi 에 준 열 이름이 어느 파일에도 없습니다: " + ", ".join(unknown) + "\n"
            "  이름이 틀린 채로 계산하면 실제보다 안전해 보이는 k 가 나옵니다.\n"
            "  사용 가능한 열: " + _column_hint(result)
        )
    unknown = _unknown_columns(result, drop_columns)
    if unknown:
        return _fail(
            "--drop-columns 에 준 열 이름이 어느 파일에도 없습니다: " + ", ".join(unknown) + "\n"
            "  오타 하나로 지웠다고 생각한 열이 그대로 나갑니다.\n"
            "  사용 가능한 열: " + _column_hint(result)
        )
    if link_explicit and all(info.link_index is None for info in result.tables):
        return _fail(
            "--link-id 에 준 열 이름이 어느 파일에도 없습니다: " + args.link_id + "\n"
            "  사용 가능한 열: " + _column_hint(result)
        )

    undetermined = result.coverage.undetermined
    if not args.quiet:
        print(report_text)
    verdict = verdict_lines(result, undetermined)

    # ---- 내보내기 ----
    export_sections: List[str] = []
    exit_code = EXIT_UNDETERMINED if undetermined else (EXIT_CRITICAL if result.findings.critical_count else EXIT_OK)

    if export_requested and undetermined:
        for line in verdict:
            print(line)
        return _fail(
            "검사하지 못한 부분이 있어 사본을 만들지 않았습니다.\n"
            "  다 읽지 못한 입력으로 '안전한 사본'을 만들면 그 사본의 안전을 보장할 수 없습니다.",
            EXIT_UNDETERMINED,
        )

    plan: Optional[PseudoPlan] = None
    transforms = []
    verification: List[str] = []
    export_paths: List[Path] = []
    recheck: Optional[AuditResult] = None

    if export_requested:
        try:
            plan, transforms, verification, export_paths = _do_export(
                result, args, paths, out_dir, drop_columns, link_ids, link_explicit
            )
        except PathSafetyError as exc:
            # "취소합니다"라고 말했으면 실제로 지워야 합니다 — 재감사도 요약도 없는
            # 반쪽짜리 사본이 폴더에 남으면, 그게 그대로 나갑니다.
            _cleanup_export(out_dir)
            return _fail(str(exc))
        except OSError as exc:
            _cleanup_export(out_dir)
            return _fail(f"사본을 쓰지 못했습니다 ({exc.__class__.__name__}): {exc.strerror or exc}")
        except VerificationError as exc:
            _cleanup_export(out_dir)
            return _fail(
                f"날짜 이동 자체검증에 실패해 내보내기를 취소했습니다: {exc}\n"
                "  (검증을 통과하지 못한 사본은 만들지 않습니다.)",
                EXIT_UNDETERMINED,
            )

        # 내보낸 사본을 **다시 감사**합니다 — 실제로 안전해졌는지 확인하는 유일한 방법입니다.
        recheck = run_audit(
            export_paths, quasi, link_ids, args.max_bytes, args.target_k,
            display_prefix=f"{_DEFAULT_EXPORT_DIRNAME}/",
        )
        try:
            recheck_text = console_report(recheck, args.target_k)
        except CoverageError as exc:
            _cleanup_export(out_dir)
            return _fail(f"내보낸 사본을 다시 읽지 못했습니다: {exc}", EXIT_UNDETERMINED)
        export_sections.append("=" * 66)
        export_sections.append("[내보낸 사본 재감사] — 방금 만든 사본을 처음부터 다시 검사했습니다")
        export_sections.append(
            "  ※ 날짜를 이동한 열은 여전히 '정확 날짜 열' 경고로 잡힙니다. 값은 이동됐지만\n"
            "     형태는 날짜이기 때문입니다 — 이동 사실은 비식별화_요약.md 에 있습니다."
        )
        export_sections.append("")
        export_sections.append(recheck_text)
        if not args.quiet:
            print("\n".join(export_sections))
        for warning in plan.warnings:
            print(f"  ※ {warning}")
        verdict = verdict_lines(recheck, recheck.coverage.undetermined, subject="내보낸 사본")
        exit_code = (
            EXIT_UNDETERMINED
            if recheck.coverage.undetermined
            else (EXIT_CRITICAL if recheck.findings.critical_count else EXIT_OK)
        )

    # ---- 리포트 파일 ----
    if out_dir is not None:
        try:
            _write_reports(
                result, args, report_dir, out_dir, paths, undetermined, export_sections,
                transforms, plan, verification, key_out, drop_columns, recheck,
            )
        except PathSafetyError as exc:
            return _fail(str(exc))
        except CoverageError as exc:
            return _fail(f"자백 블록을 만들 수 없어 리포트를 쓰지 않았습니다: {exc}", EXIT_UNDETERMINED)
        except OSError as exc:
            return _fail(f"리포트를 쓰지 못했습니다 ({exc.__class__.__name__}): {exc.strerror or exc}")
        if export_requested and report_dir != out_dir:
            print(f"\n보낼 폴더: `{out_dir / _DEFAULT_EXPORT_DIRNAME}`  ← 이 폴더만 보내세요.")
            print(f"점검 리포트: `{report_dir}`  ← 내 컴퓨터에만 두세요(행 번호가 사본과 맞습니다).")
        else:
            print(f"\n리포트를 `{report_dir}` 에 썼습니다.")
        if key_out is not None:
            print(f"키 파일: `{key_out}`  ← 이 파일은 절대 함께 보내지 마세요.")

    print()
    for line in verdict:
        print(line)
    print()
    return exit_code


def _cleanup_export(out_dir: Optional[Path]) -> None:
    """검증 실패 시 만들다 만 사본을 지웁니다."""
    if out_dir is None:
        return
    export_dir = out_dir / _DEFAULT_EXPORT_DIRNAME
    if not export_dir.exists():
        return
    for path in sorted(export_dir.rglob("*"), reverse=True):
        try:
            path.unlink() if path.is_file() else path.rmdir()
        except OSError:
            pass
    try:
        export_dir.rmdir()
    except OSError:
        pass


def _do_export(
    result: AuditResult,
    args,
    inputs: Sequence[Path],
    out_dir: Path,
    drop_columns: Sequence[str],
    link_ids: Sequence[str],
    link_explicit: bool,
):
    """가명화·날짜이동·열제외 사본을 만듭니다(자체검증 포함)."""
    # 가명화를 요청했는데 ID 열을 못 찾은 표가 있으면 **아무것도 쓰지 않고** 멈춥니다.
    # 그대로 진행하면 원본 ID(예: `KHJ-1988`)가 사본에 그대로 남은 채
    # "치명 0건 · 내보내도 되는 상태" 가 나옵니다.
    if args.pseudonymize:
        missing = [
            info for info in result.tables
            if not info.table.hidden_sheet and info.link_index is None and info.table.n_rows
        ]
        if missing:
            lines = []
            for info in missing:
                cols = ", ".join(info.safe_column(i) for i in range(min(15, len(info.table.columns))))
                lines.append(f"    · {info.label} — 있는 열: {cols}")
            raise PathSafetyError(
                "--pseudonymize 를 요청했지만 다음 표에서 피험자 ID 열을 찾지 못했습니다.\n"
                + "\n".join(lines)
                + "\n  이대로 진행하면 그 표의 ID 가 **원본 그대로** 사본에 남습니다.\n"
                "  `--link-id <열이름>` 으로 ID 열을 알려 주거나, 그 파일을 입력에서 빼세요."
            )


    export_dir = out_dir / _DEFAULT_EXPORT_DIRNAME
    if export_dir.exists() and any(export_dir.iterdir()):
        raise PathSafetyError(
            f"내보내기 폴더에 이미 파일이 있습니다: {export_dir}\n"
            "  옛 사본이 새 사본과 섞여 함께 나가는 사고를 막기 위해 빈 폴더를 요구합니다."
        )
    problem = _make_dir(export_dir)
    if problem:
        raise PathSafetyError(problem)

    # 1) 전체 피험자 집합에서 가명·오프셋 계획을 만듭니다(파일 간 일관).
    subjects: List[str] = []
    for info in result.tables:
        if info.link_index is None:
            continue
        for row in info.table.rows:
            if info.link_index < len(row):
                subjects.append(normalize_subject(row[info.link_index]))
    fallback_by_table = {}
    for info in result.tables:
        if info.table.hidden_sheet:
            continue  # 어차피 사본으로 내보내지 않습니다.
        if info.link_index is None:
            key = f"(ID열없음:{info.table.label})"
            fallback_by_table[info.table.label] = key
            subjects.append(key)

    plan = build_plan(
        subjects or [EMPTY_SUBJECT_KEY],
        salt=args.salt or None,
        prefix=args.prefix,
        shift_dates=args.shift_dates,
        week_aligned=args.shift_weeks,
    )
    verification = list(verify_plan(plan))
    if fallback_by_table:
        plan.warnings.append(
            "피험자 ID 열을 찾지 못한 표가 있어 그 표 전체에 하나의 오프셋을 적용했습니다: "
            + ", ".join(sorted(fallback_by_table))
        )

    # 2) 표별 변환 + 자체검증
    transforms = []
    export_paths: List[Path] = []
    skipped_hidden: List[str] = []
    renamed: List[Tuple[str, str]] = []
    hidden_in_export: List[str] = []
    masked_sheets: List[str] = []
    sheet_ordinal = 0
    for info in result.tables:
        table = info.table
        if table.hidden_sheet:
            # 숨김 시트는 사본에 내보내지 않습니다 — 눈에 안 보이는 것이 함께 나가는 것이 사고의 본체입니다.
            skipped_hidden.append(info.label)
            continue
        transform = transform_table(
            table=table,
            profiles=info.profiles,
            plan=plan,
            link_index=info.link_index,
            drop_columns=drop_columns,
            shift_dates=args.shift_dates,
            fallback_subject=fallback_by_table.get(table.label, EMPTY_SUBJECT_KEY),
            replace_id=args.pseudonymize,
        )
        verification.extend(
            verify_transform(
                table=table,
                result=transform,
                plan=plan,
                link_index=info.link_index,
                shift_dates=args.shift_dates,
                fallback_subject=fallback_by_table.get(table.label, EMPTY_SUBJECT_KEY),
                profiles=info.profiles,
                replace_id=args.pseudonymize,
                drop_columns=drop_columns,
            )
        )
        transforms.append(transform)
        if table.hidden_columns or table.hidden_rows:
            kept_hidden = [
                table.columns[i] for i in sorted(table.hidden_columns)
                if i < len(table.columns) and table.columns[i] in transform.columns
            ]
            if kept_hidden or table.hidden_rows:
                hidden_in_export.append(
                    f"{info.label}: 숨김 열 {', '.join(kept_hidden) or '없음'} · 숨김 행 {len(table.hidden_rows)}개"
                )

        stem = Path(safe_output_name(table.file)).stem
        if table.sheet:
            safe_sheet = safe_sheet_label(table.sheet)
            if safe_sheet != table.sheet or sheet_name_has_person(table.sheet):
                # 시트 이름 자체가 식별자입니다 — 파일 이름으로 내보내면 보낼 폴더
                # 안까지 따라갑니다. 서수로 바꾸고 그 사실을 알립니다.
                sheet_ordinal += 1
                safe_sheet = f"시트{sheet_ordinal}"
                masked_sheets.append(f"{table.file}!{safe_sheet}")
            name = f"{stem}__{safe_output_name(safe_sheet)}.csv"
        else:
            name = f"{stem}.csv"
        # 같은 이름이 이미 있으면 `_2` 를 붙입니다. 덮어쓰면 코호트의 절반이
        # 조용히 사라지고, 요약은 "2개 표를 내보냈다"고 말합니다.
        target = unique_path(export_dir, name)
        if target.name != name:
            renamed.append((table.label, target.name))
        target = ensure_output_target(target, out_dir)
        ensure_not_input(target, inputs)
        write_csv(target, transform.columns, transform.rows)
        export_paths.append(target)

    if masked_sheets:
        plan.warnings.append(
            "시트 이름 자체에 식별자가 들어 있어 사본 파일명을 서수로 바꿨습니다: "
            + ", ".join(masked_sheets)
            + " — 원본 엑셀의 시트 이름도 고치세요."
        )
    if hidden_in_export:
        # 숨김 시트는 내보내지 않지만, 숨김 **열/행**은 데이터이므로 그대로 나갑니다.
        # 비대칭이라 반드시 말해 줘야 합니다.
        plan.warnings.append(
            "숨김 시트와 달리 숨김 열·행은 **일반 열/행으로 사본에 포함**됩니다: "
            + "; ".join(hidden_in_export)
        )
    if renamed:
        plan.warnings.append(
            "이름이 겹쳐 사본 파일명을 바꿨습니다: "
            + ", ".join(f"{label} → {new}" for label, new in renamed)
        )
    if len(set(export_paths)) != len(export_paths) or len(export_paths) != len(transforms):
        raise PathSafetyError("사본 파일 수가 표 수와 맞지 않습니다 — 내보내기를 취소합니다.")

    if args.shift_dates:
        unshiftable = []
        for info in result.tables:
            if info.table.hidden_sheet:
                continue
            exported = next((t for t in transforms if t.table is info.table), None)
            if exported is None:
                continue
            for profile in info.profiles:
                name = info.table.columns[profile.index]
                if name not in exported.columns:
                    continue
                if profile.is_partial_date_column:
                    unshiftable.append(
                        f"{info.label} · {info.safe_column(profile.index)} 열 "
                        f"(값의 {profile.date_ratio:.0%}만 날짜로 읽힘)"
                    )
                elif profile.ambiguous_date_ratio >= 0.5:
                    unshiftable.append(
                        f"{info.label} · {info.safe_column(profile.index)} 열 (일/월 순서를 알 수 없는 표기)"
                    )
        if unshiftable:
            raise VerificationError(
                "--shift-dates 를 요청했지만 다음 열은 이동하지 못했습니다:\n    · "
                + "\n    · ".join(unshiftable)
                + "\n  이대로 내보내면 **원본 날짜가 그대로 나가면서** 나머지만 이동한 사본이 됩니다.\n"
                "  (연-월-일 표기로 통일하거나 `미상`·`N/A` 같은 값을 비워 두고 다시 돌리세요.)"
            )
        if not any(t.shifted_columns for t in transforms):
            plan.warnings.append(
                "--shift-dates 를 요청했지만 이동할 날짜 열을 하나도 찾지 못했습니다 — "
                "사본의 날짜는 원본과 같습니다."
            )
    if skipped_hidden:
        plan.warnings.append(
            "숨김 시트는 사본에 내보내지 않았습니다: " + ", ".join(skipped_hidden)
        )
    if not transforms:
        raise PathSafetyError(
            "내보낼 표가 하나도 없습니다(전부 숨김 시트였거나 읽지 못했습니다)."
        )

    # 3) 키 파일 — 반드시 --out-dir 밖
    if args.key_out:
        key_path = Path(os.path.expanduser(args.key_out))
        ensure_key_outside(key_path, out_dir)
        ensure_not_input(key_path, inputs)
        problem = _make_dir(key_path.parent)
        if problem:
            raise PathSafetyError(problem)
        rows = key_rows(plan)
        # 원본 ID 는 이스케이프하지 않습니다 — 키 파일은 원자료로 되돌아가는
        # 유일한 길이라, `=A1` 같은 ID 앞에 따옴표가 붙으면 조인이 깨집니다.
        write_private_csv(key_path, ["원본ID", "가명ID", "날짜오프셋_일"], [[a, b, c] for a, b, c in rows])
        risky = [a for a, _b, _c in rows if needs_escaping(a)]
        if risky:
            plan.warnings.append(
                f"원본 ID {len(risky)}개가 `=`/`+`/`@`/`-` 로 시작합니다. 키 파일에는 조인이 깨지지 않도록 "
                "그대로 적었으니, 엑셀로 열 때 수식으로 실행되지 않게 '텍스트'로 가져오세요."
            )
        # 문자열 비교로는 하드링크를 잡을 수 없습니다 — 실제로 쓴 뒤 inode 로 확인합니다.
        collision = find_inside(key_path, out_dir)
        if collision is not None:
            try:
                key_path.unlink()
            except OSError:
                pass
            raise PathSafetyError(
                f"키 파일이 --out-dir 안의 파일과 같은 실제 파일입니다(하드링크): {collision}\n"
                "  키 파일을 지웠습니다. 내보내기 폴더와 무관한 경로를 지정하세요."
            )
        # 솔트만 있으면 모든 가명과 모든 날짜 오프셋을 재생성할 수 있습니다 —
        # 키 파일과 똑같이 다뤄야 합니다(위치 검사 + 심볼릭 링크 무시 + inode 재확인).
        salt_note = key_path.with_name(key_path.stem + "_솔트.txt")
        ensure_key_outside(salt_note, out_dir)
        ensure_not_input(salt_note, inputs)
        write_text(
            salt_note,
            "이 솔트를 --salt 로 주면 같은 가명·같은 날짜 오프셋이 다시 나옵니다.\n"
            "솔트와 매핑표는 원자료와 같은 보안 등급으로 다루세요.\n\n"
            f"salt = {plan.salt}\n",
            private=True,
        )
        collision = find_inside(salt_note, out_dir)
        if collision is not None:
            try:
                salt_note.unlink()
            except OSError:
                pass
            raise PathSafetyError(
                f"솔트 파일이 --out-dir 안의 파일과 같은 실제 파일입니다: {collision}\n"
                "  솔트만으로 모든 가명과 날짜 오프셋을 되살릴 수 있습니다. 파일을 지웠습니다."
            )

    return plan, transforms, verification, export_paths


def _write_reports(
    result: AuditResult,
    args,
    report_dir: Path,
    out_dir: Path,
    inputs: Sequence[Path],
    undetermined: bool,
    export_sections: Sequence[str],
    transforms,
    plan,
    verification,
    key_out: Optional[Path],
    drop_columns: Sequence[str],
    recheck: Optional[AuditResult] = None,
) -> None:
    """리포트 파일들을 씁니다.

    자백 블록을 만들 수 없으면 아무것도 쓰지 않고, **모든 대상 경로를 먼저
    검증한 뒤에** 쓰기 시작합니다(중간에 실패해 반쪽짜리 리포트가 남지 않도록).
    모든 리포트는 0600 으로 만듭니다 — 리포트도 원본과 같은 취급입니다.
    """
    result.coverage.validate()  # 여기서 실패하면 파일을 하나도 만들지 않습니다.
    problem = _make_dir(report_dir)
    if problem:
        raise PathSafetyError(problem)
    generated = now_string()
    # 경로에는 계정명과 폴더 이름이 들어 있습니다 — 파일명만 남깁니다.
    command = "deidaudit " + " ".join([Path(f).name for f in args.files] + _echo_flags(args))

    md = markdown_report(result, args.target_k, undetermined, command, generated)
    if export_sections:
        md += "\n## 내보낸 사본 재감사\n\n```\n" + "\n".join(export_sections) + "\n```\n"
    md += "\n## 결론\n\n" + "\n".join(f"- {line.strip()}" for line in verdict_lines(result, undetermined)) + "\n"

    problems = problem_rows(result, args.max_detail)
    text_targets = [("점검결과.md", md)]
    csv_targets = [
        ("문제목록.csv", PROBLEM_HEADER, problems),
        ("재식별위험.csv", RISK_HEADER, risk_rows(result, args.target_k)),
    ]
    if len(result.findings.items) > len(problems):
        text_targets.append(
            (
                "문제목록_잘림안내.txt",
                f"지적 {len(result.findings.items):,}건 중 {len(problems):,}건만 문제목록.csv 에 썼습니다.\n"
                f"--max-detail 을 늘리면 더 씁니다.\n",
            )
        )
    if recheck is not None:
        csv_targets.append(("내보낸사본_문제목록.csv", PROBLEM_HEADER, problem_rows(recheck, args.max_detail)))
    if transforms:
        text_targets.append(
            (
                "비식별화_요약.md",
                summary_report(
                    result=result,
                    transforms=transforms,
                    plan=plan,
                    verification=verification,
                    key_out=key_out,
                    out_dir=out_dir,
                    dropped=drop_columns,
                    generated=generated,
                ),
            )
        )

    # 1) 모든 대상 경로를 먼저 검증합니다(하나라도 걸리면 아무것도 쓰지 않습니다).
    resolved = {}
    for name, _text in text_targets:
        path = ensure_output_target(report_dir / name, report_dir)
        ensure_not_input(path, inputs)
        resolved[name] = path
    for name, _header, _rows in csv_targets:
        path = ensure_output_target(report_dir / name, report_dir)
        ensure_not_input(path, inputs)
        resolved[name] = path

    # 2) 그다음에 씁니다.
    for name, text in text_targets:
        write_text(resolved[name], text, private=True)
    for name, header, rows in csv_targets:
        write_csv(resolved[name], header, rows, private=True)


def _echo_flags(args) -> List[str]:
    """리포트에 다시 적을 실행 옵션(경로는 그대로, 비밀값은 제외)."""
    flags: List[str] = []
    if args.quasi:
        flags += ["--quasi", args.quasi]
    if args.link_id:
        flags += ["--link-id", args.link_id]
    if args.pseudonymize:
        flags.append("--pseudonymize")
    if args.shift_dates:
        flags.append("--shift-dates")
    if args.drop_columns:
        flags += ["--drop-columns", args.drop_columns]
    if args.out_dir:
        flags += ["--out-dir", args.out_dir]
    if args.key_out:
        flags += ["--key-out", "<키파일경로>"]
    if args.salt:
        flags += ["--salt", "<솔트>"]
    return flags


def main() -> None:  # pragma: no cover - 진입점
    sys.exit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
