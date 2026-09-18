#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""100% 합성 예제 폴더를 만든다 — 실데이터·원고는 저장소에 들어오지 않는다.

**왜 파일로 커밋하지 않고 생성하는가:** 이 툴의 판정은 수정시각(mtime)에 기대는데
git 은 mtime 을 저장하지 않는다. 클론하면 모든 파일이 같은 시각이 되어 예제가
전부 ``[판정불가]`` 로 떨어진다. 그래서 예제는 mtime 까지 못 박아 **생성**한다.

이 파일은 ``stalecheck`` 패키지 바깥이다. 툴 자체는 아무것도 쓰지 않는다.

    python3 examples/예제_만들기.py /tmp/stalecheck_예제
"""

import os
import shutil
import struct
import sys
import time
import zipfile
import zlib

# 봉투를 싼 시각 / 그 다음 날 작업한 시각 (고정 — 테스트가 여기에 기댄다)
T_PACKAGE = time.mktime((2026, 7, 30, 11, 10, 0, 0, 0, -1))
T_WORK = time.mktime((2026, 7, 31, 10, 39, 0, 0, 0, -1))

STALE_ROOT = "예제_논문폴더"
CLEAN_ROOT = "예제_깨끗한폴더"
PACKAGE_DIR = "submission"


# --------------------------------------------------------------------------
# 최소 PDF — 생성시각 메타데이터만 다른 두 벌을 만들기 위한 것
# --------------------------------------------------------------------------
def make_pdf(text, created, doc_id):
    """xref 오프셋까지 맞춘 1쪽짜리 PDF 바이트."""
    stream = ("BT /F1 18 Tf 72 700 Td (%s) Tj ET" % text).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Producer (stalecheck example) /CreationDate (D:%s) /ModDate (D:%s) >>"
        % (created.encode("ascii"), created.encode("ascii")),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R /ID [<%s> <%s>] >>\n"
            % (len(objects) + 1, doc_id.encode("ascii"), doc_id.encode("ascii")))
    out += b"startxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)


# --------------------------------------------------------------------------
# 최소 DOCX — docProps 만 다른 두 벌
# --------------------------------------------------------------------------
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def make_docx(paragraph, modified):
    """``docProps/core.xml`` 의 저장시각만 다른 DOCX 를 만들 수 있게 한다."""
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>" % paragraph
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dcterms="http://purl.org/dc/terms/">'
        '<dcterms:modified xsi:type="dcterms:W3CDTF" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">%s</dcterms:modified>'
        "</cp:coreProperties>" % modified
    )
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in (("[Content_Types].xml", _CONTENT_TYPES),
                           ("_rels/.rels", _RELS),
                           ("word/document.xml", doc),
                           ("docProps/core.xml", core)):
            info = zipfile.ZipInfo(name, date_time=(2026, 7, 30, 0, 0, 0))
            zf.writestr(info, data)
    return buf.getvalue()


def make_png(width, height, rgb):
    """작고 진짜인 PNG(색만 다르게)."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


ANALYSIS_PACKAGE = '''\
# 03_분석.py — 봉투에 들어간 판(7월 30일)
import csv

def 평균(값들):
    return sum(값들) / len(값들)

with open("data/측정.csv", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))

print("N =", len(rows))
'''

ANALYSIS_WORK = '''\
# 03_분석.py — 작업 폴더의 최신판(7월 31일)
import csv

def 평균(값들):
    return sum(값들) / len(값들)

with open("data/측정.csv", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))

print("N =", len(rows))

# --- 원고가 보고하는 두 값을 표에서 베끼지 않고 여기서 계산한다 ---
전후차 = [float(r["post"]) - float(r["pre"]) for r in rows]
print("평균 변화량 =", round(평균(전후차), 2))
print("개선 인원 =", sum(1 for d in 전후차 if d > 0))
'''

TABLE_SCRIPT = '''\
# 05_표.py — 봉투와 작업 폴더가 같은 판
print("Table 1")
'''

MANUSCRIPT_MD = """\
# 예제 원고

이 문서는 합성 예제입니다. 실제 연구 자료가 아닙니다.

## 결과
평균 변화량은 -3.4점이었습니다.
"""


def _write(path, data, mtime):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(data)
    os.utime(path, (mtime, mtime))


def build_example(dest):
    """``dest`` 아래에 예제 두 벌을 만든다. 기존 내용은 지우고 다시 만든다."""
    dest = os.path.abspath(dest)
    for name in (STALE_ROOT, CLEAN_ROOT):
        target = os.path.join(dest, name)
        if os.path.isdir(target):
            shutil.rmtree(target)

    # ---------------- 낡은 봉투가 있는 예제 (exit 1) ----------------
    root = os.path.join(dest, STALE_ROOT)
    pkg = os.path.join(root, PACKAGE_DIR)

    _write(os.path.join(root, "analysis/03_분석.py"), ANALYSIS_WORK, T_WORK)
    _write(os.path.join(pkg, "analysis/03_분석.py"), ANALYSIS_PACKAGE, T_PACKAGE)

    _write(os.path.join(root, "analysis/05_표.py"), TABLE_SCRIPT, T_WORK)
    _write(os.path.join(pkg, "analysis/05_표.py"), TABLE_SCRIPT, T_PACKAGE)

    _write(os.path.join(root, "figures/그림1_결과.png"),
           make_png(24, 16, (30, 90, 160)), T_WORK)
    _write(os.path.join(pkg, "05_Figures/그림1_결과.png"),
           make_png(24, 16, (30, 90, 155)), T_PACKAGE)

    # 같은 그림을 두 번 렌더했을 뿐 — 내용은 같고 생성시각만 다르다.
    _write(os.path.join(root, "figures/그림2_흐름도.pdf"),
           make_pdf("Figure 2", "20260731103900", "A1" * 16), T_WORK)
    _write(os.path.join(pkg, "05_Figures/그림2_흐름도.pdf"),
           make_pdf("Figure 2", "20260730111000", "B2" * 16), T_PACKAGE)

    # 같은 원고를 다시 저장했을 뿐 — docProps 만 다르다.
    _write(os.path.join(root, "manuscript/본문.docx"),
           make_docx("예제 원고 본문", "2026-07-31T01:39:00Z"), T_PACKAGE)
    _write(os.path.join(pkg, "본문.docx"),
           make_docx("예제 원고 본문", "2026-07-30T02:10:00Z"), T_PACKAGE)

    # 소스가 산출물보다 최신 → [경고] 빌드 누락
    _write(os.path.join(root, "manuscript/본문.md"), MANUSCRIPT_MD, T_WORK)

    # 같은 그림의 두 포맷이 하루 벌어져 있다 → [경고] 그림 형제 세대 불일치
    _write(os.path.join(root, "figures/그림1_결과.pdf"),
           make_pdf("Figure 1", "20260730111000", "D4" * 16), T_PACKAGE)

    # 이름만 바꿔 넣은 사본 — 내용이 같으므로 낡았을 수 없다(판정 대상 아님)
    붙임 = make_png(12, 12, (10, 140, 70))
    _write(os.path.join(root, "figures/그림3_부록.png"), 붙임, T_WORK)
    _write(os.path.join(pkg, "05_Figures/Figure3.png"), 붙임, T_PACKAGE)

    # 봉투에만 있는 파일 (정상일 수 있다 — 짝없음.csv 로만 자백)
    _write(os.path.join(pkg, "00_커버레터.md"), "# Cover letter\n", T_PACKAGE)

    # 아카이브 — 작업본으로 세면 안 된다
    _write(os.path.join(root, "_이전/그림1_결과.png"),
           make_png(24, 16, (200, 40, 40)), T_PACKAGE)

    # ---------------- 봉투가 최신인 예제 (exit 0) ----------------
    clean = os.path.join(dest, CLEAN_ROOT)
    cpkg = os.path.join(clean, PACKAGE_DIR)
    _write(os.path.join(clean, "analysis/03_분석.py"), ANALYSIS_WORK, T_WORK)
    _write(os.path.join(cpkg, "analysis/03_분석.py"), ANALYSIS_WORK, T_WORK)
    _write(os.path.join(clean, "figures/그림1_결과.png"),
           make_png(24, 16, (30, 90, 160)), T_WORK)
    _write(os.path.join(cpkg, "05_Figures/그림1_결과.png"),
           make_png(24, 16, (30, 90, 160)), T_WORK)
    _write(os.path.join(clean, "figures/그림2_흐름도.pdf"),
           make_pdf("Figure 2", "20260731103900", "A1" * 16), T_WORK)
    _write(os.path.join(cpkg, "05_Figures/그림2_흐름도.pdf"),
           make_pdf("Figure 2", "20260731104500", "C3" * 16), T_WORK)
    return {"stale": root, "stale_package": pkg,
            "clean": clean, "clean_package": cpkg}


def main(argv):
    dest = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_생성")
    paths = build_example(dest)
    print("예제를 만들었습니다:")
    for key in ("stale", "clean"):
        print("  %s" % paths[key])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
