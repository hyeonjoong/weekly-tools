"""봉투 하나를 점검해 :class:`Report` 를 만든다.

판정 규칙(바꿀 때는 회귀 테스트를 먼저 고칠 것):

* **치명** 은 네 가지뿐이다 — 고아 미디어 · 깨진 이미지 참조 ·
  외부 링크 이미지 · ``--expect`` 기준 누락 첨부물 ·
  그리고 ``--baseline`` 대비 앵커 회귀. 이 밖에는 치명으로 올리지 않는다.
* **중복 미디어는 언제나 경고** 다. Word 가 흔히 만들기 때문에 치명으로
  올리면 매 회차 울고, 매 회차 우는 리포트는 아무도 두 번 열지 않는다.
* ``--expect`` 없이 보충자료 실물을 못 찾은 것은 치명이 아니라
  **대조불가** 다. 보충자료를 저널 포털에 따로 올리는 것도 정상 절차다.
"""

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple

from . import findings as F
from .docxpkg import DocxInfo, DocxUnreadable, read_docx
from .envelope import LOCK_PREFIX, Envelope, EnvelopeFile, scan_envelope
from .findings import CRITICAL, Finding, INFO, WARNING
from .frontmatter import MATCH, MISMATCH, UNKNOWN, compare_frontmatter, FrontmatterResult
from .safeio import nfc, sanitize
from .spec import Expectations, Limits
from .textrefs import (TextRefs, extract_refs, looks_like_supplement_container,
                       looks_standard_part, normalize_filename, numbered_items_in_filename,
                       token_matches_file, token_text_pattern)

MB = 1024 * 1024


def mb(value: int) -> str:
    return f"{value / MB:.2f} MB"


@dataclass
class PromiseRow:
    token: str
    mentions: int
    matched: str
    verdict: str       # 있음 / 없음 / 대조불가


@dataclass
class AssetRow:
    name: str
    kind: str
    size: int
    digest8: str
    anchored: str
    dup_group: str
    mentions: int


@dataclass
class LedgerRow:
    label: str
    paragraphs: int
    comments: int
    revisions: int
    media: int
    duplicates: int
    orphans: int
    total_bytes: int


@dataclass
class Coverage:
    """커버리지 자백. 이게 없으면 리포트를 아예 내지 않는다."""

    parsed: List[str] = field(default_factory=list)
    listed_only: List[str] = field(default_factory=list)
    excluded_dirs: List[str] = field(default_factory=list)
    excluded_links: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    unchecked: List[str] = field(default_factory=list)


@dataclass
class Report:
    envelope: Envelope
    manuscript: DocxInfo
    refs: TextRefs
    findings: List[Finding] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    promises: List[PromiseRow] = field(default_factory=list)
    assets: List[AssetRow] = field(default_factory=list)
    ledger: List[LedgerRow] = field(default_factory=list)
    frontmatter: Optional[FrontmatterResult] = None
    baseline_label: str = ""

    @property
    def critical_count(self) -> int:
        return F.count_by(self.findings, CRITICAL)

    @property
    def warning_count(self) -> int:
        return F.count_by(self.findings, WARNING)

    @property
    def exit_code(self) -> int:
        return 1 if self.critical_count else 0


# ---------------------------------------------------------------- 개별 점검

def _check_anchors(info: DocxInfo, refs: TextRefs, out: List[Finding]) -> None:
    orphans = info.orphans
    if orphans:
        detail = [f"{sanitize(m.name)}   {mb(m.size)}" for m in orphans]
        cited = refs.fig_citation_total
        detail.append(
            f"본문은 그림을 {cited}회 부르고 캡션은 {len(refs.fig_captions)}종이지만, "
            f"본문에 앵커된 이미지는 {len(info.body_anchored)}개입니다."
        )
        detail.append("머리글/바닥글/각주 이미지는 고아로 세지 않았습니다.")
        out.append(Finding(CRITICAL, "ORPHAN_MEDIA",
                           f"고아 이미지 {len(orphans)}개 — 패키지에 있으나 어디에도 앵커 없음", detail))

    broken = info.broken_image_rels
    if broken:
        out.append(Finding(CRITICAL, "BROKEN_REL",
                           f"깨진 이미지 참조 {len(broken)}건 — 본문이 부르는데 파일이 없음",
                           [f"{sanitize(r.source_part)} :: {sanitize(r.rel_id)} → {sanitize(r.target)}"
                            for r in broken]))

    external = info.external_image_rels
    if external:
        out.append(Finding(CRITICAL, "EXTERNAL_IMAGE",
                           f"외부 링크 이미지 {len(external)}건 — 봉투를 옮기면 그림이 사라집니다",
                           [f"{sanitize(r.source_part)} → {sanitize(r.target)}" for r in external]))

    if info.duplicate_rel_ids:
        out.append(Finding(WARNING, "DUP_REL_ID",
                           f"같은 관계 Id 가 두 번 선언된 곳이 {len(info.duplicate_rel_ids)}건 "
                           "있습니다 — Word 는 하나만 씁니다",
                           [sanitize(x) for x in info.duplicate_rel_ids[:10]]
                           + ["이 툴도 마지막 선언만 앵커로 셉니다."]))

    groups = info.duplicate_groups()
    if groups:
        detail = [" = ".join(sanitize(m.basename) for m in g) for g in groups]
        out.append(Finding(WARNING, "DUP_MEDIA",
                           f"중복 이미지 {len(groups)}쌍 — 같은 바이트, 다른 이름 "
                           f"({mb(info.duplicate_waste())} 낭비)", detail))


def _check_triad(info: DocxInfo, refs: TextRefs, out: List[Finding]) -> None:
    spread = " ".join(f"Fig{n}×{c}" for n, c in sorted(refs.fig_citations.items()))
    header = (f"Fig 인용 {refs.fig_citation_total}회"
              + (f" ({spread})" if spread else "")
              + f" · 캡션 {len(refs.fig_captions)}종 · 본문 앵커 이미지 {len(info.body_anchored)}개")
    detail = []
    if info.other_anchored:
        detail.append(f"머리글/바닥글/각주 등에 앵커된 이미지 {len(info.other_anchored)}개 (고아 아님)")
    detail.append("이미지 파일과 그림 번호를 짝지어 주지는 않습니다 — 패키지에 그 정보가 없습니다.")
    out.append(Finding(INFO, "TRIAD", header, detail))

    uncited = [n for n in refs.fig_captions if refs.fig_citations.get(n, 0) == 0]
    if uncited:
        out.append(Finding(WARNING, "FIG_UNCITED",
                           f"캡션은 있는데 본문이 한 번도 부르지 않는 그림 {len(uncited)}개",
                           [f"Fig. {n}" for n in uncited]))
    no_caption = [n for n in sorted(refs.fig_citations) if n not in refs.fig_captions]
    if no_caption:
        out.append(Finding(WARNING, "FIG_NO_CAPTION",
                           f"본문이 부르는데 캡션이 없는 그림 {len(no_caption)}개",
                           [f"Fig. {n} (본문 {refs.fig_citations[n]}회)" for n in no_caption]))


def _check_numbering(refs: TextRefs, out: List[Finding]) -> None:
    numbers = sorted(refs.fig_captions)
    if numbers:
        missing = [n for n in range(1, max(numbers) + 1) if n not in refs.fig_captions]
        if missing:
            out.append(Finding(WARNING, "FIG_GAP",
                               f"그림 번호가 건너뜁니다: {', '.join('Fig. %d' % n for n in missing)}",
                               ["캡션이 있는 번호: " + ", ".join(str(n) for n in numbers)]))
    if refs.fig_caption_dups:
        out.append(Finding(WARNING, "FIG_DUP_CAPTION",
                           f"같은 번호의 캡션이 두 번 이상 나옵니다: "
                           f"{', '.join('Fig. %d' % n for n in sorted(set(refs.fig_caption_dups)))}",
                           ["텍스트 상자 안에 캡션을 복사해 둔 경우도 여기에 걸립니다."]))
    order = [n for n in refs.fig_first_order if n in refs.fig_captions]
    if order and order != sorted(order):
        out.append(Finding(WARNING, "FIG_ORDER",
                           "본문 첫 등장 순서가 그림 번호 순이 아닙니다",
                           ["첫 등장 순: " + " → ".join(f"Fig. {n}" for n in order)]))
    for number, panels in sorted(refs.body_panels.items()):
        caption_panels = refs.caption_panels.get(number)
        if not caption_panels:
            continue
        missing_panels = [p for p in panels if p not in caption_panels]
        if missing_panels:
            out.append(Finding(WARNING, "PANEL_MISSING",
                               f"본문이 부르는 패널이 Fig. {number} 캡션에 없습니다: "
                               + ", ".join(f"({p})" for p in sorted(missing_panels)),
                               [f"캡션의 패널: {', '.join('(%s)' % p for p in caption_panels)}"]))


def _check_promises(env: Envelope, manuscript: EnvelopeFile, refs: TextRefs,
                    expect: Optional[Expectations], out: List[Finding],
                    others: Optional[List[DocxInfo]] = None) -> List[PromiseRow]:
    candidates = [f for f in env.visible_files if f.name != manuscript.name]
    containers = [f for f in candidates if looks_like_supplement_container(f.name)]
    parsed = {o.filename: o for o in (others or [])}
    rows: List[PromiseRow] = []
    for token, info in refs.supp.items():
        hits = [f.name for f in candidates if token_matches_file(token, f.name)]
        if hits:
            rows.append(PromiseRow(token, info.count, ", ".join(hits), "있음"))
            continue
        # 통합본(Supplementary Material 한 파일)에 들어 있는 경우를 본다.
        pattern = token_text_pattern(token)
        inside = [c.name for c in containers
                  if c.name in parsed and pattern.search(parsed[c.name].text)]
        if inside:
            rows.append(PromiseRow(token, info.count, ", ".join(inside), "있음(통합본)"))
            continue
        if expect is not None and any(nfc(e).lower() == nfc(token).lower()
                                      for e in expect.elsewhere):
            rows.append(PromiseRow(token, info.count, "(별도 경로 제출)", "별도제출"))
            continue
        if containers and expect is None:
            rows.append(PromiseRow(token, info.count,
                                   ", ".join(c.name for c in containers), "대조불가"))
            continue
        if expect is None:
            rows.append(PromiseRow(token, info.count, "", "대조불가"))
        else:
            required = any(nfc(r).lower() == nfc(token).lower() for r in expect.required)
            rows.append(PromiseRow(token, info.count, "", "없음" if required else "대조불가"))

    unmatched = [r for r in rows if r.verdict == "대조불가"]
    missing = [r for r in rows if r.verdict == "없음"]
    if missing:
        out.append(Finding(CRITICAL, "PROMISE_MISSING",
                           f"--expect 가 필수로 지정한 첨부물 {len(missing)}종이 봉투에 없습니다",
                           [f"{r.token} (본문 {r.mentions}회)" for r in missing]))
    if unmatched:
        if expect is None:
            reason = ("--expect 를 주지 않아 '없음'으로 단정하지 않았습니다. "
                      "저널 포털에 따로 올리는 절차라면 정상입니다.")
        else:
            reason = ("--expect 의 required 목록에 없는 항목이라 '없음'으로 단정하지 "
                      "않았습니다.")
        out.append(Finding(WARNING, "PROMISE_UNVERIFIED",
                           f"본문이 부르는 보충자료 {len(unmatched)}종을 봉투에서 찾지 못했습니다 (판정: 대조불가)",
                           [", ".join(r.token for r in unmatched), reason]))
    if expect is not None:
        promised = {nfc(r.token).lower() for r in rows}
        absent = []
        for item in expect.required:
            key = nfc(item).lower()
            if key in promised:
                continue
            wanted = normalize_filename(item)
            if any(token_matches_file(item, f.name) or wanted in normalize_filename(f.name)
                   for f in candidates):
                continue
            absent.append(item)
        if absent:
            out.append(Finding(CRITICAL, "EXPECT_MISSING",
                               f"--expect 필수 항목 {len(absent)}종에 해당하는 파일이 봉투에 없습니다",
                               [sanitize(a) for a in absent]))
    return rows


def _check_reverse(env: Envelope, manuscript: EnvelopeFile, manuscript_text: str,
                   promises: List[PromiseRow], out: List[Finding],
                   refs: Optional[TextRefs] = None) -> None:
    """봉투에 있는데 본문이 부르지 않는 파일.

    `Fig3.tif` · `Table_2.pdf` 처럼 **번호가 붙은 제출물**은 본문이 그
    번호를 부르고 있으면 부른 것으로 본다. 그러지 않으면 "본문이 부르지
    않는 파일: Fig1.tif" 와 "Fig 인용 6회" 가 같은 리포트에 나란히 찍힌다.
    """
    claimed = set()
    for row in promises:
        for name in filter(None, (n.strip() for n in row.matched.split(","))):
            claimed.add(name)
    lowered = nfc(manuscript_text).lower()
    strays = []
    for item in env.visible_files:
        if item.name == manuscript.name or item.name in claimed:
            continue
        stem = nfc(item.name).rsplit(".", 1)[0].lower()
        if stem and stem in lowered:
            continue
        if looks_standard_part(item.name):
            continue
        if refs is not None and _filename_is_cited(item.name, refs):
            continue
        strays.append(item)
    if strays:
        out.append(Finding(WARNING, "STRAY_FILE",
                           f"본문이 부르지 않는 파일 {len(strays)}개",
                           [f"{sanitize(f.name)} ({mb(f.size)})" for f in strays]))
    locks = [f for f in env.junk_files if f.name.startswith(LOCK_PREFIX)]
    junk = [f for f in env.junk_files if not f.name.startswith(LOCK_PREFIX)]
    if locks:
        out.append(Finding(WARNING, "WORD_LOCK",
                           f"원고를 Word 로 열어 둔 채입니다 (잠금 파일 {len(locks)}개)",
                           [sanitize(f.name) for f in locks]
                           + ["저장하지 않은 변경이 있을 수 있습니다. "
                              "Word 를 닫고 다시 확인하세요. 원고 후보로는 세지 않았습니다."]))
    if junk:
        out.append(Finding(WARNING, "JUNK_FILE",
                           f"숨김/시스템 파일 {len(junk)}개 — 함께 올라가면 곤란합니다",
                           [sanitize(f.name) for f in junk]
                           + ["Finder 가 다시 만드는 파일입니다. 폴더째 압축할 때만 문제가 됩니다."]))
    if env.subdirs:
        out.append(Finding(WARNING, "SUBFOLDER",
                           f"봉투 안에 하위폴더 {len(env.subdirs)}개 — 폴더째 압축하면 함께 올라갑니다",
                           [sanitize(d) for d in env.subdirs]
                           + ["이 툴은 하위폴더를 봉투로 보지 않았습니다. "
                              "옛 판본·검토 메모가 들어 있으면 확인하세요."]))
    if env.links:
        out.append(Finding(WARNING, "SYMLINK",
                           f"심볼릭 링크 {len(env.links)}개 — 봉투로 보지 않았습니다",
                           [sanitize(x) for x in env.links]
                           + ["링크는 가리키는 곳이 봉투 밖일 수 있어 열지 않았습니다."]))


def _recoverable_waste(info: DocxInfo) -> int:
    """실제로 지울 수 있는 바이트.

    같은 바이트의 사본 묶음마다, **본문/머리글 어디든 앵커된 사본이 하나라도
    있으면 한 벌은 남겨야 한다.** 하나도 앵커돼 있지 않으면 묶음 전체를
    지울 수 있다. 중복분과 고아분을 그냥 더하면 "미디어 총량보다 낭비가 크다"는
    불가능한 숫자가 나온다.
    """
    anchored = info.anchored
    buckets: Dict[str, List] = {}
    for name in sorted(info.media):
        buckets.setdefault(info.media[name].sha256, []).append(info.media[name])
    total = 0
    for group in buckets.values():
        group_bytes = sum(m.size for m in group)
        if any(m.name in anchored for m in group):
            total += group_bytes - max(m.size for m in group)
        else:
            total += group_bytes
    return total


def _filename_is_cited(filename: str, refs: TextRefs) -> bool:
    for kind, number in numbered_items_in_filename(filename):
        if kind == "fig" and (number in refs.fig_citations or number in refs.fig_captions):
            return True
        if kind == "table" and (number in refs.table_citations or number in refs.table_captions):
            return True
    return False


def _check_hygiene(env: Envelope, info: DocxInfo, limits: Optional[Limits],
                   out: List[Finding]) -> None:
    waste = _recoverable_waste(info)
    detail = [f"원고 {mb(info.file_size)}(압축된 파일 크기) · "
              f"그 안의 미디어 {mb(info.media_bytes)}(압축 해제 기준)"]
    if waste:
        detail.append(f"중복 {mb(info.duplicate_waste())} · 고아 {mb(info.orphan_waste())} "
                      f"→ 실제로 지울 수 있는 양 {mb(waste)} (압축 해제 기준, "
                      "겹치는 파일은 한 번만 셉니다)")
    out.append(Finding(INFO, "SIZE", f"봉투 총 {mb(env.total_bytes)}", detail))

    stamps = [s for s in (info.creator, info.last_modified_by) if s]
    if stamps:
        out.append(Finding(INFO, "DOC_PROPS",
                           f"원고 속성에 작성자/최종수정자 기록이 남아 있습니다 ({len(stamps)}개 항목)",
                           [f"작성자: {sanitize(info.creator) or '(없음)'}",
                            f"최종수정자: {sanitize(info.last_modified_by) or '(없음)'}",
                            "익명 심사 저널이면 지우세요. 이 툴은 세기만 하고 지우지 않습니다."]))

    if limits is None or limits.empty:
        return
    if limits.max_total_mb is not None and env.total_bytes > limits.max_total_mb * MB:
        out.append(Finding(WARNING, "LIMIT_TOTAL",
                           f"봉투 총 용량이 --limits 상한을 넘습니다: "
                           f"{mb(env.total_bytes)} > {limits.max_total_mb} MB",
                           ["상한은 --limits 로 받은 값입니다. 최종 판단은 사람이 합니다."]))
    if limits.max_file_mb is not None:
        over = [f for f in env.visible_files if f.size > limits.max_file_mb * MB]
        if over:
            out.append(Finding(WARNING, "LIMIT_FILE",
                               f"파일당 상한({limits.max_file_mb} MB)을 넘는 파일 {len(over)}개",
                               [f"{sanitize(f.name)} ({mb(f.size)})" for f in over]))
    if limits.max_files is not None and len(env.visible_files) > limits.max_files:
        out.append(Finding(WARNING, "LIMIT_COUNT",
                           f"파일 개수가 상한을 넘습니다: {len(env.visible_files)} > {limits.max_files}",
                           []))


def _check_frontmatter(result: FrontmatterResult, out: List[Finding]) -> None:
    """표제 3종. **대조하지 못한 것도 소리 내어 말한다** —
    침묵을 '일치'로 읽는 것이 이 툴이 막으려는 바로 그 오해다."""
    tally = {MATCH: 0, MISMATCH: 0, UNKNOWN: 0}
    for checks in result.checks.values():
        for check in checks:
            tally[check.verdict] = tally.get(check.verdict, 0) + 1
    if result.checks:
        out.append(Finding(INFO, "FRONTMATTER_TALLY",
                           f"표제 3종(제목·저자순서·원고번호) · 문서 {len(result.checks)}개 "
                           f"→ 일치 {tally[MATCH]} · 불일치 {tally[MISMATCH]} · "
                           f"대조불가 {tally[UNKNOWN]}",
                           ["대조불가는 '같다'는 뜻이 아니라 '이 문서에서 찾지 못했다'는 뜻입니다."]))
    bad = result.mismatches()
    if not bad:
        return
    detail = []
    for filename, checks in result.checks.items():
        for check in checks:
            if check.verdict == MISMATCH:
                detail.append(f"{sanitize(filename)} · {check.field}: "
                              f"원고 '{sanitize(check.reference, 120)}' ↔ 이 문서 "
                              f"'{sanitize(check.found, 120) or '(못 찾음)'}'"
                              + (f" — {check.note}" if check.note else ""))
    out.append(Finding(WARNING, "FRONTMATTER",
                       f"봉투 안 문서들의 표제가 원고와 다릅니다 ({len(bad)}건)", detail))


def _check_baseline(current: DocxInfo, base: DocxInfo, base_label: str,
                    out: List[Finding]) -> None:
    """지난 회차엔 앵커돼 있었는데 이번에 고아가 된 그림 — 이 툴의 존재 이유."""
    # 기준 회차에서 앵커돼 있던 '사본 목록'을 예산으로 쓴다.
    available = [base.media[n] for n in sorted(base.anchored) if n in base.media]
    # 이번 회차에도 여전히 앵커된 사본 수만큼 먼저 뺀다 — Word 가 사본을 하나
    # 더 만들어 그게 고아가 된 경우를 '앵커가 끊겼다'고 말하면 거짓이다.
    still: Dict[str, int] = {}
    for name in current.anchored:
        media = current.media.get(name)
        if media is not None:
            still[media.sha256] = still.get(media.sha256, 0) + 1
    kept = []
    for media in available:
        if still.get(media.sha256, 0) > 0:
            still[media.sha256] -= 1
            continue
        kept.append(media)
    available = kept

    regressed, by_name = [], []
    for orphan in current.orphans:
        hit = next((m for m in available if m.sha256 == orphan.sha256), None)
        if hit is not None:
            available.remove(hit)
            regressed.append(orphan)
            continue
        # 공저자 Word 가 저장하면서 이미지를 다시 인코딩하면 해시가 달라진다.
        # 그때는 파일 이름으로 잇되, **같은 예산에서** 소비한다.
        hit = next((m for m in available if m.basename == orphan.basename), None)
        if hit is not None:
            available.remove(hit)
            by_name.append(orphan)

    if regressed or by_name:
        detail = [f"{sanitize(m.basename)}   {mb(m.size)}  (바이트 동일)" for m in regressed]
        detail += [f"{sanitize(m.basename)}   {mb(m.size)}  (파일 이름 기준 — 바이트는 달라졌습니다)"
                   for m in by_name]
        detail.append(f"기준 판본: {sanitize(base_label)}")
        out.append(Finding(CRITICAL, "ANCHOR_REGRESSION",
                           f"지난 회차엔 앵커돼 있던 그림 {len(regressed) + len(by_name)}개가 "
                           "이번 회차에서 고아가 됐습니다", detail))
    current_hashes = {m.sha256 for m in current.media.values()}
    current_names = {m.basename for m in current.media.values()}
    lost = [base.media[name] for name in sorted(base.media)
            if base.media[name].sha256 not in current_hashes
            and base.media[name].basename not in current_names]
    if lost:
        out.append(Finding(WARNING, "MEDIA_LOST",
                           f"지난 회차에 있던 이미지 {len(lost)}개가 이번 회차에 없습니다",
                           [f"{sanitize(m.basename)} ({mb(m.size)})" for m in lost]))
    delta = current.media_bytes - base.media_bytes
    if abs(delta) >= MB:
        direction = "늘었습니다" if delta > 0 else "줄었습니다"
        out.append(Finding(INFO, "MEDIA_DELTA",
                           f"미디어 용량이 지난 회차 대비 {mb(abs(delta))} {direction}",
                           [f"{mb(base.media_bytes)} → {mb(current.media_bytes)}"]))


# ---------------------------------------------------------------- 조립

def _assets(env: Envelope, manuscript: EnvelopeFile, info: DocxInfo,
            refs: TextRefs, promises: List[PromiseRow]) -> List[AssetRow]:
    rows: List[AssetRow] = []
    text = nfc(info.text).lower()
    claimed: Dict[str, List[str]] = {}
    for row in promises:
        for name in filter(None, (n.strip() for n in row.matched.split(","))):
            claimed.setdefault(name, []).append(row.token)
    for item in env.files:
        stem = nfc(item.name).rsplit(".", 1)[0].lower()
        mentions = text.count(stem) if stem else 0
        kind = "원고" if item.name == manuscript.name else item.kind
        anchored = "본문참조" if item.name in claimed else "-"
        rows.append(AssetRow(item.name, kind, item.size, (item.sha256 or "")[:8],
                             anchored, "", mentions))
    group_names: Dict[str, str] = {}
    for index, group in enumerate(info.duplicate_groups(), start=1):
        for media in group:
            group_names[media.name] = f"D{index}"
    for name in sorted(info.media):
        media = info.media[name]
        if name in info.body_anchored:
            anchored = "본문앵커"
        elif name in info.other_anchored:
            anchored = "머리글/각주앵커"
        else:
            anchored = "고아"
        rows.append(AssetRow(f"[원고 내부] {media.name}", "미디어", media.size,
                             media.sha256[:8], anchored, group_names.get(name, ""), 0))
    return rows


def _ledger_row(label: str, env_bytes: int, info: DocxInfo) -> LedgerRow:
    return LedgerRow(label, info.raw_paragraph_count, info.comment_count,
                     info.insertions + info.deletions, len(info.media),
                     len(info.duplicate_groups()), len(info.orphans), env_bytes)


def analyse(env: Envelope, manuscript: EnvelopeFile, info: DocxInfo,
            others: List[DocxInfo],
            baseline: Optional[Tuple[str, DocxInfo, int]] = None,
            expect: Optional[Expectations] = None,
            limits: Optional[Limits] = None,
            compare_titles: bool = True) -> Report:
    """모든 점검을 돌려 리포트를 만든다."""
    refs = extract_refs(info.paragraphs)
    out: List[Finding] = []
    _check_anchors(info, refs, out)
    if baseline is not None:
        _check_baseline(info, baseline[1], baseline[0], out)
    promises = _check_promises(env, manuscript, refs, expect, out, others)
    _check_reverse(env, manuscript, info.text, promises, out, refs)
    _check_triad(info, refs, out)
    _check_numbering(refs, out)
    _check_hygiene(env, info, limits, out)

    frontmatter = None
    if compare_titles and others:
        frontmatter = compare_frontmatter(info, others)
        _check_frontmatter(frontmatter, out)

    report = Report(envelope=env, manuscript=info, refs=refs,
                    findings=F.sort_findings(out), promises=promises,
                    frontmatter=frontmatter)
    report.assets = _assets(env, manuscript, info, refs, promises)
    report.ledger = [_ledger_row(info.filename, env.total_bytes, info)]
    if baseline is not None:
        report.baseline_label = baseline[0]
        report.ledger.insert(0, _ledger_row(baseline[0], baseline[2], baseline[1]))

    report.coverage = _coverage(env, info, others, expect, limits, baseline)
    return report


def _coverage(env: Envelope, info: DocxInfo, others: List[DocxInfo],
              expect: Optional[Expectations], limits: Optional[Limits],
              baseline) -> Coverage:
    cov = Coverage()
    cov.parsed = [info.filename] + [o.filename for o in others]
    cov.listed_only = [f.name for f in env.visible_files
                       if not f.parsed]
    cov.excluded_dirs = list(env.subdirs)
    cov.excluded_links = list(env.links)
    cov.unreadable = list(env.unreadable) + list(info.parts_unparsed)
    if expect is None:
        cov.unchecked.append("필수 제출물 (--expect 없음 — 보충자료 실물은 '대조불가'로 남겼습니다)")
    if limits is None or limits.empty:
        cov.unchecked.append("용량·개수 상한 (--limits 없음)")
    if baseline is None:
        cov.unchecked.append("지난 회차 대비 앵커 회귀 (--baseline 없음)")
    cov.unchecked.append("이미지 안의 글자 (OCR 없음 — 그림 속 p값 표기는 보지 못합니다)")
    cov.unchecked.append("PDF·PPTX·HWP 내부 (v1 은 .docx 만 엽니다)")
    cov.unchecked.append("원고 텍스트의 품질·숫자·인용 (draftcheck · numcheck · citecheck 담당)")
    if not others:
        cov.unchecked.append("표제 3종 동기화 (봉투에 다른 docx 가 없습니다)")
    return cov
