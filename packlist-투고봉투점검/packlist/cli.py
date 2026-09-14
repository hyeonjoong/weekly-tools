"""명령줄 진입점.

종료코드 규칙(바꾸지 말 것):
0 치명 없음 · 1 치명 있음 · 2 판정 없이 거절 · 3 판정 불가.
**3 이 1 보다 우선한다.** 다 읽지 못했으면 "치명 2건"은 거짓말이다.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE, __version__
from .analysis import analyse, mb
from .docxpkg import DocxInfo, DocxUnreadable, read_docx
from .envelope import Envelope, EnvelopeFile, NeedsManuscript, choose_manuscript, detect_irb_packet, scan_envelope
from .report import ARTIFACTS, console_lines
from .safeio import (UsageError, precheck_artifacts, prepare_out_dir, sanitize,
                     write_text)
from .spec import load_expectations, load_limits
from .textrefs import extract_refs

PROG = "packlist"


def _emit(lines: List[str], stream=None) -> None:
    """리포트 한 덩이를 출력한다.

    * `| head` 로 파이프가 끊겨도 종료코드를 뒤집지 않는다.
    * fd 1 이 닫힌 채(``>&-``, launchd) 실행되면 ``sys.stdout`` 이 ``None``
      이다 — 그때 터지면 종료코드가 1 이 되어 '치명 발견'과 구별되지 않는다.
    * 콘솔 인코딩이 한글을 못 담으면 **조용히 삼키지 않고** 대체 문자로 찍는다.
    """
    if stream is None:
        stream = sys.stdout
    if stream is None:
        return
    try:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        for line in lines:
            try:
                stream.write(line + "\n")
            except UnicodeEncodeError:
                stream.write(line.encode(encoding, "backslashreplace").decode(encoding) + "\n")
        stream.flush()
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, stream.fileno())
        except (OSError, ValueError, AttributeError):
            pass
    except (ValueError, OSError, AttributeError):
        # 닫힌 스트림. 판정 자체는 끝났으므로 종료코드를 바꾸지 않는다.
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="투고 봉투 점검 — 원고가 약속한 첨부물이 봉투 안에 실제로 있고 "
                    "docx 안에 앵커돼 있는지 대조합니다. 입력은 읽기만 합니다.",
        epilog="원고 한 개의 텍스트 점검은 draftcheck, IRB 패킷의 내용 정합은 irbpack 이 합니다.",
    )
    parser.add_argument("envelope", help="점검할 봉투 폴더")
    parser.add_argument("--out-dir", help="리포트를 쓸 폴더 (없으면 콘솔만)")
    parser.add_argument("--baseline", help="지난 회차 봉투 폴더 (앵커 회귀 검출)")
    parser.add_argument("--manuscript", help="봉투에 docx 가 여럿일 때 원고 파일명")
    parser.add_argument("--baseline-manuscript", help="기준 봉투의 원고 파일명")
    parser.add_argument("--expect", help="필수 제출물 목록 JSON")
    parser.add_argument("--limits", help="용량·개수 상한 JSON")
    parser.add_argument("--inspect", action="store_true",
                        help="봉투가 어떻게 읽혔는지만 인쇄하고 끝낸다 "
                             "(판정하지 않으며 --out-dir 를 무시한다)")
    parser.add_argument("--skip-frontmatter", action="store_true",
                        help="표제 3종(제목·저자순서·원고번호) 대조를 건너뛴다")
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    return parser


def _load_envelope(path: str) -> Envelope:
    env = scan_envelope(path)
    hits = detect_irb_packet(env)
    if len(hits) >= 2:
        raise UsageError(
            "IRB/임상 패킷으로 보입니다 — 투고 봉투가 아닙니다.\n"
            "  근거: " + ", ".join(sanitize(h) for h in hits[:4]) + "\n"
            "  패킷 안 문서들의 내용 정합은 irbpack 이 봅니다."
        )
    return env


def _read_all(env: Envelope,
              manuscript: EnvelopeFile) -> Tuple[DocxInfo, List[DocxInfo], List[str]]:
    """원고와 부속 docx 를 읽는다.

    **원고를 못 읽으면** 판정 불가(3)다. 부속 문서 하나를 못 읽는 것은
    원고 점검을 막지 않는다 — 그 사실을 자백에 싣고 계속한다.
    """
    main = read_docx(env.root / manuscript.name, manuscript.name)
    others, unreadable = [], []
    for item in env.docx_files:
        if item.name == manuscript.name:
            continue
        try:
            others.append(read_docx(env.root / item.name, item.name))
        except DocxUnreadable as exc:
            unreadable.append(str(exc))
    return main, others, unreadable


def _inspect(env: Envelope) -> List[str]:
    lines = [f"packlist {__version__} — 봉투 읽기 결과 (판정하지 않습니다)",
             f"봉투: {env.label}/",
             f"  파일 {len(env.visible_files)}개 · 하위폴더 {len(env.subdirs)}개(제외) · "
             f"숨김/시스템 {len(env.junk_files)}개",
             ""]
    for item in env.visible_files:
        lines.append(f"  - {sanitize(item.name)}  [{item.kind}]  {mb(item.size)}")
    for item in env.junk_files:
        lines.append(f"  - {sanitize(item.name)}  [숨김/시스템]  {mb(item.size)}")
    if env.subdirs:
        lines.append("  제외된 하위폴더: " + ", ".join(sanitize(d) for d in env.subdirs))
    lines.append("")
    for item in env.docx_files:
        try:
            info = read_docx(env.root / item.name, item.name)
        except DocxUnreadable as exc:
            # 판정하지 않는 명령이므로 한 파일을 못 읽는다고 멈추지 않는다.
            lines.append(f"  · {sanitize(item.name)}")
            lines.append(f"      못 읽음 — {sanitize(str(exc))}")
            continue
        refs = extract_refs(info.paragraphs)
        spread = " ".join(f"Fig{n}×{c}" for n, c in sorted(refs.fig_citations.items()))
        lines.append(f"  · {sanitize(item.name)}")
        lines.append(f"      문단 {info.raw_paragraph_count} · 코멘트 {info.comment_count} · "
                     f"변경내용 {info.insertions + info.deletions}")
        lines.append(f"      미디어 {len(info.media)}개 · 본문 앵커 {len(info.body_anchored)} · "
                     f"머리글/각주 앵커 {len(info.other_anchored)} · 고아 {len(info.orphans)} · "
                     f"중복 {len(info.duplicate_groups())}쌍")
        lines.append(f"      Fig 인용 {refs.fig_citation_total}회"
                     + (f" ({spread})" if spread else "")
                     + f" · 캡션 {len(refs.fig_captions)}종")
        if refs.supp:
            lines.append(f"      보충자료 토큰 {len(refs.supp)}종: "
                         + ", ".join(sorted(refs.supp))[:200])
    lines.append("")
    lines.append("문단 수는 word/document.xml 의 <w:p> 개수입니다 — "
                 "빈 문단·표 안 문단·텍스트 상자 문단을 모두 포함합니다.")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        env = _load_envelope(args.envelope)
        expect = load_expectations(args.expect) if args.expect else None
        limits = load_limits(args.limits) if args.limits else None
        # --inspect 는 판정하지 않으므로 산출물도 없다. 빈 폴더만 만들어 두면
        # 사람이 "리포트가 어디 갔지" 하고 헤맨다.
        out_dir = None
        if args.out_dir and not args.inspect:
            # 폴더를 **만들기 전에** 위치부터 본다. 먼저 만들어 버리면
            # 거절하면서도 봉투 안에 빈 폴더를 남기게 된다.
            envelope_root = env.root.resolve()
            wanted = Path(args.out_dir).absolute()
            if wanted == envelope_root or envelope_root in wanted.parents:
                raise UsageError(
                    "--out-dir 가 점검할 봉투 폴더 안입니다.\n"
                    "  리포트가 봉투 안으로 들어가면 폴더째 압축할 때 그대로 올라갑니다. "
                    "봉투 바깥의 폴더를 지정하세요."
                )
            out_dir = prepare_out_dir(args.out_dir)
            precheck_artifacts(out_dir, [name for name, _ in ARTIFACTS])
    except UsageError as exc:
        _emit([f"{PROG}: {exc}"], sys.stderr)
        return EXIT_REFUSED

    if args.inspect:
        if args.out_dir:
            _emit([f"{PROG}: --inspect 는 판정하지 않으므로 리포트를 쓰지 않습니다 "
                   "(--out-dir 는 무시했습니다)."], sys.stderr)
        _emit(_inspect(env))
        return EXIT_OK

    if env.unreadable:
        _emit([f"{PROG}: 판정 불가 — 봉투 안에서 읽지 못한 파일이 있습니다: "
               + ", ".join(sanitize(n) for n in env.unreadable),
               "  다 읽지 못한 봉투에 대해 판정하지 않습니다."], sys.stderr)
        return EXIT_UNDECIDABLE

    try:
        manuscript = choose_manuscript(env, args.manuscript)
    except NeedsManuscript as exc:
        _emit([f"{PROG}: 봉투에 .docx 가 {len(exc.candidates)}개입니다 — 어느 것이 원고인지 "
               "추측하지 않습니다.",
               "  --manuscript 로 지정하세요:",
               *[f"    --manuscript {sanitize(c)}" for c in exc.candidates[:20]]], sys.stderr)
        return EXIT_REFUSED
    except UsageError as exc:
        _emit([f"{PROG}: {exc}"], sys.stderr)
        return EXIT_REFUSED

    if len(env.visible_files) == 1:
        _emit([f"{PROG}: 봉투에 원고 1개뿐입니다 — 대조할 첨부물이 없습니다.",
               f"  {sanitize(manuscript.name)}",
               "  이 툴은 '원고가 약속한 파일이 봉투 안에 있는가'를 봅니다.",
               "  원고 하나의 텍스트 점검(번호·인용·형식)은 draftcheck 가 합니다."], sys.stderr)
        return EXIT_REFUSED

    try:
        info, others, unreadable_side = _read_all(env, manuscript)
    except DocxUnreadable as exc:
        _emit([f"{PROG}: 판정 불가 — {exc}",
               "  다 읽지 못한 봉투에 대해 '치명 N건'이라고 말하지 않습니다."], sys.stderr)
        return EXIT_UNDECIDABLE

    baseline = None
    if args.baseline:
        try:
            base_env = _load_envelope(args.baseline)
            base_file = choose_manuscript(base_env, args.baseline_manuscript)
        except NeedsManuscript as exc:
            _emit([f"{PROG}: 기준 봉투에 .docx 가 {len(exc.candidates)}개입니다 — "
                   "--baseline-manuscript 로 지정하세요."], sys.stderr)
            return EXIT_REFUSED
        except UsageError as exc:
            _emit([f"{PROG}: --baseline: {exc}"], sys.stderr)
            return EXIT_REFUSED
        try:
            base_info = read_docx(base_env.root / base_file.name, base_file.name)
        except DocxUnreadable as exc:
            _emit([f"{PROG}: 판정 불가 — 기준 봉투: {exc}"], sys.stderr)
            return EXIT_UNDECIDABLE
        baseline = (base_file.name, base_info, base_env.total_bytes)

    report = analyse(env, manuscript, info, others, baseline=baseline,
                     expect=expect, limits=limits,
                     compare_titles=not args.skip_frontmatter)
    report.coverage.unreadable.extend(unreadable_side)

    written = []
    if out_dir is not None:
        # 판정을 인쇄하기 **전에** 쓴다. 인쇄한 뒤 쓰기에 실패하면
        # "치명 1건"을 찍어 놓고 종료코드만 2(거절)로 바뀌는 모순이 생긴다.
        try:
            # 네 개를 **먼저 전부 렌더**하고 나서 쓴다. 중간에 렌더가 실패하면
            # 새 .md 와 낡은 .csv 가 섞인 반쪽 리포트가 남는다.
            rendered = [(name, render(report)) for name, render in ARTIFACTS]
            precheck_artifacts(out_dir, [name for name, _ in rendered])
            written = [write_text(out_dir, name, text) for name, text in rendered]
        except UsageError as exc:
            _emit([f"{PROG}: {exc}"], sys.stderr)
            return EXIT_REFUSED

    _emit(console_lines(report))
    if written:
        _emit(["", "리포트: " + ", ".join(sanitize(Path(p).name) for p in written)])

    return EXIT_CRITICAL if report.critical_count else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
