"""합성 예제 봉투를 만든다 (실제 원고는 절대 복사하지 않는다).

    python3 examples/_make_examples.py

만들어지는 것:

==========================  ============  ===================================
폴더                         기대 종료코드   무엇을 보여 주는가
==========================  ============  ===================================
01_정상봉투                        0        약속한 보충자료가 실제로 있는 봉투
02_고아있음                        1        Fig 그림이 앵커를 잃은 봉투
03_판정불가                        3        docx 가 손상돼 다 읽지 못한 봉투
04_거절_원고만                     2        원고 1개뿐 — 대조할 봉투가 아님
05_앵커회귀_지난회차/이번회차      1        지난 회차엔 붙어 있던 그림이 떨어짐
==========================  ============  ===================================

``02_고아있음`` 은 실제 사고의 **모양**만 그대로 옮겼다 —
미디어 6개 · 같은 바이트 3쌍 · 본문 앵커 4개 · 고아 2개.
실제 원고의 내용은 한 글자도 들어 있지 않다.
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docxbuild import build_corrupt_docx, build_docx, fake_png   # noqa: E402

TITLE = "Non-contact respiratory entrainment and slow-wave sleep: a synthetic example manuscript"

BODY = [
    "Abstract. This synthetic manuscript exists only to exercise packlist.",
    "Introduction. Slow breathing has been proposed to shift autonomic balance.",
    "We report three analyses (Fig. 1, Fig. 2) and two supplementary tables.",
    "Methods. Participants were randomised as summarised in Fig. 1a.",
    "Full randomisation counts are given in S1 Table.",
    "Results. Prefrontal high-beta power decreased (Fig. 2b).",
    "Connectivity increased during the active stage (Fig. 3).",
    "Per-band statistics are listed in S2 Table and S1 Fig.",
    "Autonomic indices are summarised in Fig. 4 and in S3 Table.",
    "Discussion. These synthetic numbers mean nothing outside this example.",
    "Sensitivity analyses appear in S2 Fig and S3 Fig.",
    "Limitations. The example contains no real participant data.",
]

CAPTIONS = [
    ("Fig. 1. Study design and participant flow. (a) allocation. (b) retention.", "image1.png"),
    ("Fig. 2. Prefrontal high-beta power. (a) Fp1. (b) Fp2.", "image2.png"),
    ("Fig. 3. Connectivity during the active stage. (a) gamma. (b) high-beta.", "image3.png"),
    ("Fig. 4. Autonomic panel. (a) TINN. (b) RSA.", "image4.png"),
]


def _reset(path: str) -> str:
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


def make_normal(root: str) -> None:
    folder = _reset(os.path.join(root, "01_정상봉투"))
    images = {f"image{i}.png": fake_png(f"normal-{i}", 3000 + i * 500) for i in range(1, 5)}
    build_docx(
        os.path.join(folder, "원고_synthetic_v3.docx"),
        paragraphs=[TITLE, "Hyeon-Joong Kim, Jiyeon Ha, Tuomas Eerola"] + BODY + CAPTIONS,
        images=images,
        anchored=[f"image{i}.png" for i in range(1, 5)],
        comments=4,
        title=TITLE,
    )
    build_docx(
        os.path.join(folder, "Cover_Letter.docx"),
        paragraphs=[
            "Dear Editor,",
            f"We are pleased to submit our manuscript entitled “{TITLE}” "
            "for consideration.",
            "Hyeon-Joong Kim, Jiyeon Ha, Tuomas Eerola",
            "Sincerely,",
        ],
    )
    for name in ("S1_Table.pdf", "S2_Table.pdf", "S3_Table.pdf",
                 "S1_Fig.pdf", "S2_Fig.pdf", "S3_Fig.pdf"):
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(b"%PDF-1.4\n% synthetic placeholder\n")


def make_orphan(root: str) -> None:
    """실제 사고와 같은 모양: 미디어 6 · 중복 3쌍 · 앵커 4 · 고아 2."""
    folder = _reset(os.path.join(root, "02_고아있음"))
    pairs = {}
    for index in (1, 2, 3):
        blob = fake_png(f"pair-{index}", 60000 + index * 1000)
        pairs[f"image{index}.png"] = blob
        pairs[f"image{index}0.png"] = blob
    build_docx(
        os.path.join(folder, "원고_synthetic_v10.docx"),
        paragraphs=[TITLE, "Hyeon-Joong Kim, Jiyeon Ha, Tuomas Eerola"] + BODY + CAPTIONS[:3]
        + [("Fig. 4. Autonomic panel. (a) TINN. (b) RSA.", None)],
        images=pairs,
        # image3.png / image30.png 은 일부러 앵커하지 않는다 → 고아 2개
        anchored=["image1.png", "image10.png", "image2.png", "image20.png"],
        comments=12,
        title=TITLE,
        creator="Un-named",
        last_modified_by="synthetic-editor",
    )
    with open(os.path.join(folder, "SubjectInfoExtra.pptx"), "wb") as handle:
        handle.write(b"PK\x03\x04synthetic-pptx-placeholder")
    with open(os.path.join(folder, ".DS_Store"), "wb") as handle:
        handle.write(b"\x00\x01synthetic")


def make_undecidable(root: str) -> None:
    folder = _reset(os.path.join(root, "03_판정불가"))
    build_corrupt_docx(os.path.join(folder, "원고_손상본.docx"))
    with open(os.path.join(folder, "S1_Table.pdf"), "wb") as handle:
        handle.write(b"%PDF-1.4\n% synthetic placeholder\n")


def make_refused(root: str) -> None:
    folder = _reset(os.path.join(root, "04_거절_원고만"))
    images = {"image1.png": fake_png("solo", 2500)}
    build_docx(
        os.path.join(folder, "원고_혼자.docx"),
        paragraphs=[TITLE] + BODY[:4] + [CAPTIONS[0]],
        images=images,
        anchored=["image1.png"],
        title=TITLE,
    )


def make_anchor_regression(root: str) -> None:
    """`--baseline` 이 잡는 사고를 그대로 재현한 한 쌍.

    두 봉투의 이미지 바이트는 **같고**, 이번 회차에서 Fig. 3·4 의 앵커만
    끊어져 있다. 실제 사고(수정4 → 수정5)와 같은 모양이다.
    """
    images = {f"image{i}.png": fake_png(f"reg-{i}", 20000 + i * 700) for i in range(1, 5)}
    previous = _reset(os.path.join(root, "05_앵커회귀_지난회차"))
    current = _reset(os.path.join(root, "05_앵커회귀_이번회차"))
    for folder, anchored, name in (
        (previous, [f"image{i}.png" for i in range(1, 5)], "원고_synthetic_v4.docx"),
        (current, ["image1.png", "image2.png"], "원고_synthetic_v5.docx"),
    ):
        build_docx(
            os.path.join(folder, name),
            paragraphs=[TITLE, "Hyeon-Joong Kim, Jiyeon Ha, Tuomas Eerola"] + BODY + CAPTIONS,
            images=images, anchored=anchored, comments=9, title=TITLE,
        )
        for supplement in ("S1_Table.pdf", "S2_Table.pdf", "S3_Table.pdf",
                           "S1_Fig.pdf", "S2_Fig.pdf", "S3_Fig.pdf"):
            with open(os.path.join(folder, supplement), "wb") as handle:
                handle.write(b"%PDF-1.4\n% synthetic placeholder\n")


def make_specs(root: str) -> None:
    with open(os.path.join(root, "제출목록_예시.json"), "w", encoding="utf-8") as handle:
        json.dump({"required": ["S1 Table", "S2 Table", "S3 Table",
                                "S1 Fig", "S2 Fig", "S3 Fig"],
                   "optional": ["Cover Letter"]}, handle, ensure_ascii=False, indent=2)
    with open(os.path.join(root, "저널규정_예시.json"), "w", encoding="utf-8") as handle:
        json.dump({"max_total_mb": 30, "max_file_mb": 20, "max_files": 20},
                  handle, ensure_ascii=False, indent=2)


def main() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    make_normal(root)
    make_orphan(root)
    make_undecidable(root)
    make_refused(root)
    make_anchor_regression(root)
    make_specs(root)
    print("합성 예제 봉투를 만들었습니다:", root)


if __name__ == "__main__":
    main()
