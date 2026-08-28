"""명령줄 진입점.

경계를 지키는 강제 장치가 여기 모여 있습니다.

* **파일이 1개면 아무 판정도 하지 않고 종료코드 2** 로 멈추고
  `bell_acoustic_qc.py` / DEBUSSY 를 가리킵니다. 세트가 아니면 이 툴의 질문
  ("이 소리들이 서로 비교 가능한가")이 성립하지 않습니다.
* **절대 SPL 요구는 거절합니다** (종료코드 2). 재생 체인 보정 없이 파일에서
  dB SPL 을 알 수 없고, 아는 척하는 것이 이 도메인에서 가장 위험합니다.
* **못 읽은 파일이 하나라도 있으면 종료코드 3** 이고, 이는 치명(1)보다
  우선합니다. 다 못 들었으면 "치명 0건"은 거짓말입니다.

종료코드
--------
0 치명 0건 · 1 치명 발견 · 2 입력/옵션 오류 · 3 판정불가(못 읽은 파일 있음)
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from . import baseline as baseline_mod
from . import claims as claims_mod
from . import decode as decode_mod
from . import design as design_mod
from . import findings as F
from . import manifest as manifest_mod
from . import refs, report, safeio, setcheck
from .analyze import FileMetrics, analyze_file
from .wavread import WavError, WavInfo, probe

EXIT_OK = 0
EXIT_CRITICAL = 1
EXIT_USAGE = 2
EXIT_UNDECIDABLE = 3

#: 위치인자로 폴더를 받았을 때 안에서 집어 올리는 확장자.
AUDIO_EXT = (".wav",) + decode_mod.DECODABLE_EXT

SINGLE_FILE_MESSAGE = """파일이 1개뿐입니다 — stimaudit 은 **세트**를 봅니다.

이 툴이 답하는 질문은 "이 소리들이 서로 비교 가능한 세트인가"입니다.
파일이 하나면 그 질문 자체가 성립하지 않습니다. 파일 하나를 보려면:

  · 음향 지표 11종 추출        → DEBUSSY
  · 파일별 Tier-1/2 준수 판정  → bell_acoustic_qc.py

비교할 파일을 2개 이상 주십시오."""


def normalize_name(path: str) -> str:
    """macOS 는 파일명을 NFD 로 저장하는데 설계 JSON 은 보통 NFC 입니다.

    정규화하지 않으면 `싱잉볼_bi.wav` 가 눈으로는 같아 보이는데 매칭에 실패해
    "설계 JSON 이 없는 파일을 가리킵니다"라는 엉뚱한 오류가 납니다.
    """
    return unicodedata.normalize("NFC", os.path.basename(path))


def _expand_inputs(items: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
    """폴더는 안의 오디오 파일로 펼칩니다.

    반환 = (파일 목록, 없는 항목, 존재하지만 일반 파일이 아닌 항목).
    """
    out: List[str] = []
    missing: List[str] = []
    unusable: List[str] = []
    for item in items:
        p = os.path.expanduser(item)
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if os.path.splitext(name)[1].lower() in AUDIO_EXT:
                    full = os.path.join(p, name)
                    if os.path.isfile(full):
                        out.append(full)
        elif os.path.isfile(p):
            out.append(p)
        elif os.path.exists(p):
            # 존재하지만 일반 파일이 아님(/dev/null, 명명 파이프, 소켓 …).
            # "찾을 수 없습니다"는 명백히 틀린 안내입니다.
            unusable.append(item)
        else:
            missing.append(item)
    return out, missing, unusable


def _load_set(paths: Sequence[str], decoder: decode_mod.TempDecoder,
              label: str, quiet: bool) -> Tuple[Dict[str, FileMetrics], List[Tuple[str, str]], float]:
    """파일들을 읽어 분석합니다. 반환 = (이름→지표, [(이름, 실패사유)], 총 초)."""
    metrics: Dict[str, FileMetrics] = {}
    failed: List[Tuple[str, str]] = []
    total = 0.0
    for i, path in enumerate(paths, 1):
        name = normalize_name(path)
        if not quiet:
            # 진행 표시도 **파일 이름을 그대로 찍는 출력 경로**입니다. 리포트
            # 본문만 막아 두면 `2>&1 | tee log` 로 남긴 로그에 파일 이름으로 만든
            # 가짜 `[치명]` 줄이 들어갑니다 (라운드 2 검증, 항목 8).
            sys.stderr.write("\r  {} 분석 중 ({}/{}) {:<40s}".format(
                label, i, len(paths), report.flatten(name)[:40]))
            sys.stderr.flush()
        target = path
        try:
            if decode_mod.needs_decode(path):
                target = decoder.decode(path)
            info = probe(target)
            if target != path:
                info.source_note = "ffmpeg 로 디코드해 읽음 (원본 {})".format(
                    os.path.splitext(path)[1])
            # **분석이 끝난 뒤에** 표시용 경로로 바꿉니다. 전에는 여기서 먼저
            # `info.path = path` 로 되돌려 놓는 바람에 `analyze_file` 이 임시 WAV 가
            # 아니라 **원본 압축 파일을 다시 열어** 모든 MP3/M4A/FLAC 가
            # "RIFF 가 아님"으로 실패했습니다 — 디코드 통로 전체가 죽어 있었습니다.
            m = analyze_file(info)
            if target != path:
                info.path = path
            metrics[name] = m
            total += info.duration_s
        except (WavError, decode_mod.DecodeError) as exc:
            failed.append((name, str(exc)))
        except MemoryError:
            failed.append((name, "메모리 부족 — 파일이 너무 큽니다"))
        except OSError as exc:
            failed.append((name, "읽기 실패: {}".format(exc.strerror or exc)))
    if not quiet and paths:
        sys.stderr.write("\r{:<70s}\r".format(""))
        sys.stderr.flush()
    return metrics, failed, total


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stimaudit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="자극 세트 점검 — 실험에 쓸 소리 파일 여러 개를 전수 대조합니다.",
        epilog="""예시
  stimaudit sounds/*.wav --inspect
  stimaudit sounds/*.wav --design 설계.json --out-dir 자극점검_202608
  stimaudit v2/*.wav --baseline v1/ --design 설계.json --out-dir 버전대조
  stimaudit sounds/*.wav --inspect --emit-design > 설계.json

무엇을 하지 않는가
  · 소리 하나하나의 음향 지표 추출  → DEBUSSY 소관
  · 파일별 Tier-1/2 준수 판정        → bell_acoustic_qc.py 소관
  · 소리 합성                        → calmbark 소관
  · 음량 보정본 출력                 → 감사와 수정을 한 툴에 넣지 않습니다
  · 통계 검정                        → statwise 소관
  · 절대 음압(dB SPL) 판정           → 재생 체인 보정 없이는 불가능합니다
""")
    p.add_argument("files", nargs="*", metavar="파일",
                   help="WAV 파일 2개 이상 (폴더를 주면 안의 오디오를 집어 올립니다)")
    p.add_argument("--design", metavar="설계.json",
                   help="조건 매핑 + 주장(claims). 없으면 위생 점검과 파일 간 행렬만 냅니다")
    p.add_argument("--out-dir", metavar="폴더",
                   help="리포트·CSV·문장초안을 쓸 폴더 (--inspect 가 아니면 필수)")
    p.add_argument("--manifest", metavar="지표.csv",
                   help="DEBUSSY 스타일 지표 CSV — 교란 후보 비교에 씁니다 (지표를 다시 뽑지 않습니다)")
    p.add_argument("--baseline", metavar="이전폴더",
                   help="이전 버전 폴더와 짝지어 무엇이 달라졌는지 냅니다")
    p.add_argument("--inspect", action="store_true",
                   help="판정하지 않고 측정값만 봅니다 (파일을 만들지 않습니다)")
    p.add_argument("--emit-design", action="store_true",
                   help="설계 JSON 뼈대를 표준출력으로 냅니다")
    p.add_argument("--lufs-tol", type=float, default=1.0, metavar="LU",
                   help="조건 간 음량 차이 경고 문턱 (기본 1.0 LU)")
    p.add_argument("--lufs-crit", type=float, default=2.0, metavar="LU",
                   help="조건 간 음량 차이 치명 문턱 (기본 2.0 LU)")
    p.add_argument("--spl-db", type=float, metavar="dB",
                   help="[거절됩니다] 절대 음압 기준. 왜 거절하는지는 실행하면 설명합니다")
    p.add_argument("--quiet", action="store_true", help="진행 표시를 끕니다")
    p.add_argument("--version", action="version", version="stimaudit {}".format(__version__))
    return p


def _fail(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return EXIT_USAGE


def _make_output_safe(stream) -> None:
    """한글을 못 쓰는 콘솔에서 **크래시하지 않도록** 인코딩 오류 정책을 바꿉니다.

    한국 윈도우의 기본 콘솔 인코딩은 cp949 인데, 리포트에는 `—`(em dash)와
    `↔` 가 들어갑니다. 그대로 두면 `UnicodeEncodeError` 트레이스백이 뜨고
    **종료코드 1** 로 끝납니다 — 즉 인코딩 사고가 "치명 발견"으로 보고됩니다.
    (적대적 검토 라운드 1, 엣지케이스 파괴자 발견 4)
    """
    try:
        stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _make_output_safe(sys.stdout)
    _make_output_safe(sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    t0 = time.monotonic()

    if args.spl_db is not None:
        return _fail("--spl-db 는 받지 않습니다.\n\n" + refs.ABSOLUTE_SPL_REFUSAL)
    # `nan <= 0` 은 False, `nan < nan` 도 False 라서 부등식만으로는 NaN 이 그대로
    # 통과합니다. 그러면 33 LU 차이가 "차이가 작지만…" 경고로 인쇄되고 종료코드 0
    # 이 나옵니다 — 툴의 핵심 판정이 조용히 꺼집니다(라운드 1 검토에서 발견).
    if not math.isfinite(args.lufs_tol) or not math.isfinite(args.lufs_crit):
        return _fail("--lufs-tol / --lufs-crit 은 유한한 숫자여야 합니다 "
                     "(nan · inf 는 판정을 조용히 무력화합니다).")
    if args.lufs_tol <= 0 or args.lufs_crit <= 0:
        return _fail("--lufs-tol 과 --lufs-crit 은 0보다 커야 합니다.")
    if args.lufs_crit < args.lufs_tol:
        return _fail("--lufs-crit ({:.2f}) 이 --lufs-tol ({:.2f}) 보다 작습니다 — "
                     "치명 문턱은 경고 문턱보다 커야 합니다.".format(args.lufs_crit, args.lufs_tol))
    if not args.files:
        parser.print_help(sys.stderr)
        return _fail("\n분석할 파일을 주십시오 (2개 이상).")

    paths, missing, unusable = _expand_inputs(args.files)
    if missing:
        return _fail("다음 경로를 찾을 수 없습니다:\n  " + "\n  ".join(missing[:10]))
    if unusable:
        return _fail("다음 경로는 일반 파일이 아니라 읽을 수 없습니다 "
                     "(장치·파이프·소켓):\n  " + "\n  ".join(unusable[:10]))
    if not paths:
        return _fail("입력에서 오디오 파일을 찾지 못했습니다. 확장자: " + ", ".join(AUDIO_EXT))

    seen: Dict[str, str] = {}
    dupes: List[str] = []
    unique: List[str] = []
    for p in paths:
        n = normalize_name(p)
        if n in seen:
            if os.path.realpath(p) != os.path.realpath(seen[n]):
                dupes.append("{}  ←  {} / {}".format(n, seen[n], p))
            continue
        seen[n] = p
        unique.append(p)
    if dupes:
        return _fail("서로 다른 폴더에 **같은 이름**의 파일이 있습니다:\n  " + "\n  ".join(dupes[:8]) +
                     "\n설계 JSON 과 리포트가 파일 이름으로 대조하므로 구분할 수 없습니다. "
                     "이름을 바꾸거나 한 번에 하나씩 돌리십시오.")
    paths = unique

    if len(paths) < 2:
        return _fail(SINGLE_FILE_MESSAGE)
    if not args.inspect and not args.emit_design and not args.out_dir:
        return _fail("--out-dir 가 필요합니다 (리포트·CSV·문장초안을 쓸 폴더).\n"
                     "판정 없이 값만 보려면 --inspect 를 쓰십시오.")

    out_dir = None
    writes_files = bool(args.out_dir) and not args.inspect and not args.emit_design
    if args.out_dir and not writes_files:
        sys.stderr.write(
            "안내: --inspect / --emit-design 는 파일을 만들지 않으므로 --out-dir 를 "
            "무시합니다 (빈 폴더도 만들지 않습니다).\n")
    if writes_files:
        # 이번 실행에서 **읽는** 파일은 전부 덮어쓰기 금지로 등록합니다.
        # (입력 WAV · 설계 JSON · 매니페스트 CSV · 기준 폴더의 WAV)
        safeio.clear_protected()
        safeio.protect_inputs(list(paths) + [args.design, args.manifest])
        if args.baseline:
            try:
                safeio.protect_inputs(
                    os.path.join(args.baseline, n) for n in os.listdir(args.baseline))
            except OSError:
                pass
        try:
            out_dir = safeio.prepare_out_dir(args.out_dir)
        except safeio.OutputError as exc:
            return _fail(str(exc))

    input_names = [normalize_name(p) for p in paths]

    # `--emit-design` 는 **표준출력을 JSON 전용으로** 씁니다. `--inspect` 와 같이 주면
    # 사람이 읽는 리포트는 표준에러로 보냅니다 — 그러지 않으면 문서가 안내하는
    # `--inspect --emit-design > 설계.json` 이 리포트까지 JSON 파일에 쏟아 넣어
    # 파일을 못 쓰게 만듭니다.
    # 표준출력이 닫힌 채로 실행되면(`stimaudit … >&-`) 파이썬은 `sys.stdout`
    # 을 **None** 으로 둡니다. 그대로 쓰면 AttributeError 트레이스백 + 종료코드
    # 1(치명 발견)이 됩니다 — 출력 사고를 판정 결과로 보고하는 셈입니다.
    report_stream = sys.stdout if sys.stdout is not None else sys.stderr
    if report_stream is None:
        return EXIT_UNDECIDABLE
    if args.emit_design:
        if sys.stdout is None:
            return _fail("--emit-design 은 표준출력으로 JSON 을 씁니다 — "
                         "표준출력이 닫혀 있습니다.")
        sys.stdout.write(design_mod.emit_skeleton(input_names))
        sys.stdout.flush()
        if not args.inspect:
            return EXIT_OK
        report_stream = sys.stderr

    design = None
    if args.design:
        try:
            design = design_mod.load(args.design)
            design_mod.check_against_inputs(design, input_names)
        except design_mod.DesignError as exc:
            return _fail(str(exc))

    man = None
    if args.manifest:
        try:
            man = manifest_mod.load(args.manifest)
        except manifest_mod.ManifestError as exc:
            return _fail(str(exc))

    base_paths: List[str] = []
    if args.baseline:
        base_paths, base_missing, base_unusable = _expand_inputs([args.baseline])
        if base_missing or base_unusable:
            return _fail("--baseline 경로를 찾을 수 없습니다: {}".format(args.baseline))
        if not base_paths:
            return _fail("--baseline 폴더에 오디오 파일이 없습니다: {}".format(args.baseline))

    decoder = decode_mod.TempDecoder()
    try:
        metrics, failed, total_s = _load_set(paths, decoder, "입력", args.quiet)
        base_metrics: Dict[str, FileMetrics] = {}
        base_failed: List[Tuple[str, str]] = []
        if base_paths:
            base_metrics, base_failed, _ = _load_set(base_paths, decoder, "기준", args.quiet)
    finally:
        decoder.cleanup()

    order = [n for n in input_names if n in metrics]
    claim_results: List[claims_mod.ClaimResult] = []
    if design and design.claims:
        claim_results = claims_mod.check_all(metrics, design.claims)

    result = setcheck.run(metrics, design, claim_results, args.lufs_tol, args.lufs_crit)

    unassigned = design_mod.unassigned_inputs(design, order) if design else []
    if unassigned:
        # 조용히 빼면 설계 JSON 의 오타 하나로 자극 하나가 대조에서 사라집니다.
        result.findings.append(F.Finding(
            severity=F.WARNING, kind=F.KIND_UNASSIGNED, subject="세트 전체",
            detail="입력 {}개가 어느 조건에도 속하지 않습니다".format(len(unassigned)),
            measured=" · ".join(unassigned[:6]),
            reference="설계 JSON 의 conditions 에 넣거나 입력에서 빼십시오",
            consequence="이 파일들은 위생 점검만 받고 조건 간 음량 판정에서는 빠집니다."))
        result.findings.sort(key=F.sort_key)

    confound_rows: List[manifest_mod.ConfoundRow] = []
    confound_missing: List[str] = []
    confound_note = "계산 안 함 — --manifest 없음"
    if man is not None:
        if design and design.conditions:
            confound_rows, confound_missing = manifest_mod.confound_table(
                man, design.conditions, design.contrast)
            confound_note = "매니페스트 지표 {}개를 조건 간 비교 (통계 검정 없음)".format(len(man.columns))
        else:
            confound_note = "계산 안 함 — 조건을 알아야 비교할 수 있습니다 (--design 필요)"

    base_rows: List[baseline_mod.BaselineRow] = []
    base_unmatched: List[str] = []
    base_leftover: List[str] = []
    if base_metrics:
        base_rows, base_unmatched, base_leftover = baseline_mod.compare(
            metrics, base_metrics, design.pairs if design else None)

    coverage = _build_coverage(
        input_names, metrics, failed, total_s, design, man, confound_note,
        base_failed, time.monotonic() - t0, unassigned)

    data = report.ReportData(
        coverage=coverage, metrics=metrics, order=order,
        findings=result.findings if not args.inspect else [],
        matrix=result.matrix, design=design, claim_results=claim_results,
        confound_rows=confound_rows, confound_missing=confound_missing,
        baseline_rows=base_rows, baseline_unmatched=base_unmatched,
        baseline_leftover=base_leftover, lufs_tol=args.lufs_tol,
        lufs_crit=args.lufs_crit, inspect_only=args.inspect)

    try:
        report_stream.write(report.render_console(data) + "\n")
    except report.ReportError as exc:
        sys.stderr.write("리포트를 만들지 못했습니다: {}\n".format(exc))
        return EXIT_UNDECIDABLE
    except (OSError, UnicodeError) as exc:
        # 파이프가 끊겼거나(`| head`) 콘솔이 한글을 못 씁니다. 어느 쪽이든
        # **판정 결과가 아니라 출력 사고**이므로 종료코드 1(치명 발견)로
        # 끝내면 안 됩니다. 3(판정불가)이 정직합니다.
        try:
            sys.stderr.write("리포트를 출력하지 못했습니다: {}\n".format(exc))
        except Exception:
            pass
        return EXIT_UNDECIDABLE

    if out_dir:
        try:
            written = report.write_outputs(out_dir, data)
        except safeio.OutputError as exc:
            return _fail(str(exc))
        finally:
            out_dir.close()
        report_stream.write("\n산출물 {}개 → {}\n".format(len(written), args.out_dir))
        for w in written:
            report_stream.write("  {}\n".format(os.path.basename(w)))

    if failed or base_failed:
        # 기준 폴더를 다 못 읽었으면 "무엇이 달라졌는지"도 다 못 본 것입니다.
        return EXIT_UNDECIDABLE          # 3 은 1보다 우선합니다
    if args.inspect:
        return EXIT_OK
    if F.count(result.findings, F.CRITICAL):
        return EXIT_CRITICAL
    return EXIT_OK


def _build_coverage(input_names, metrics, failed, total_s, design, man,
                    confound_note, base_failed, elapsed, unassigned) -> F.Coverage:
    """커버리지 자백 — 무엇을 읽었고 무엇을 안 봤는지."""
    axes = ["레벨(LAeq·LAmax·DR·LUFS·LRA·트루피크)", "클리핑", "DC 오프셋",
            "앞뒤 무음", "상승/하강 시간", "포맷·길이 일관성"]
    if any(metrics[n].info.n_channels == 2 for n in metrics):
        axes.append("좌우 균형")
    skipped: List[Tuple[str, str]] = []
    for r in refs.unmeasured_axes():
        why = "{} · 참조 {} · 출처 {} · {}".format(
            r.note, r.value_text, r.citation, refs.DISCLAIMER)
        skipped.append((r.axis, why))
    if design and design.claims:
        axes.append("주장 대조 {}건".format(sum(len(v) for v in design.claims.values())))
    else:
        skipped.append(("주장 대조", "설계 JSON 의 claims 가 없어 검사하지 않았습니다"))
    if man is None:
        skipped.append(("교란 후보(매니페스트 지표)", "--manifest 없음 — DEBUSSY 로 뽑아 붙이십시오"))
    if any(metrics[n].info.n_channels > 2 for n in metrics):
        skipped.append(("3채널 이상 파일의 채널 가중",
                        "BS.1770 서라운드 가중(1.41)을 붙일 채널 배치 정보가 없어 전부 1.0 으로 뒀습니다"))
    design_note = ("조건 {}개 · 주장 {}건".format(
        len(design.conditions), sum(len(v) for v in design.claims.values()))
        if design else "설계 JSON 없음 — 조건 판정과 주장 대조를 하지 않았습니다")
    unreadable = list(failed) + [(n, "(기준 폴더) " + w) for n, w in base_failed]
    notes = [(n, metrics[n].info.source_note) for n in sorted(metrics)
             if metrics[n].info.source_note]
    if unassigned:
        skipped.append(("조건 미지정 파일 {}개".format(len(unassigned)),
                        "조건 간 음량 판정에서 제외됨: " + ", ".join(unassigned[:6])))
    return F.Coverage(
        n_input=len(input_names), n_read=len(metrics), unreadable=unreadable,
        read_notes=notes,
        total_seconds=total_s,
        n_channels_total=sum(metrics[n].info.n_channels for n in metrics),
        axes_checked=axes, axes_skipped=skipped, confound_note=confound_note,
        design_note=design_note, elapsed_seconds=elapsed)
