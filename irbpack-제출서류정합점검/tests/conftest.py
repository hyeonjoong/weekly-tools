"""공용 픽스처 — 합성 패킷을 .md 로 빠르게 만들고 run_packet 을 돌립니다."""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from irbpack import cli, safeio  # noqa: E402
from irbpack.model import CRITICAL, Issue, MATCH, Result, UNCOMPARABLE, WARNING  # noqa: E402

TITLE = "성인 불면증 환자 대상 소리 기반 수면 앱의 유효성 연구"
PI = "박가상"
ORG = "가상대학교병원"
OFFICE = "031-000-0000"


def md_protocol(visits="2", dur="약 90분", comp="교통비 실비 지급", n="총 90명(시험군 45명, 대조군 45명)",
                age="만 19~45세", keep="3년", ver="1.2", date="2026-08-25", title=TITLE, pi=PI, contact=OFFICE, extra="") -> str:
    return "\n".join([
        "# 연구계획서", "", "| 연구제목 | {} |".format(title), "|---|---|", "| Version No | {} |".format(ver),
        "| 작성일 | {} |".format(date), "| 연구책임자 이름 | {} |".format(pi), "| 연구기관 | {} |".format(ORG), "",
        "## 1. 연구 배경", "불면증은 흔하다.", "## 2. 연구 방법",
        "본 연구는 총 {}회 방문으로 진행한다. 각 방문의 소요시간은 {}이다.".format(visits, dur),
        "## 3. 연구 대상자", "연구 대상자는 {} 이며 연령은 {} 이다.".format(n, age),
        "### 선정기준", "1) 불면증 진단을 받은 자", "2) 서면 동의한 자", "### 제외기준", "1) 수면제 복용 중인 자",
        "## 4. 평가 항목", "평가 항목은 다음과 같다: 불면증심각도척도(ISI), 수면일지, 삶의질 설문지.",
        "## 5. 보상", "대상자에게는 {}한다.".format(comp),
        "## 6. 개인정보", "개인정보는 연구 종료 후 {}간 보관 후 폐기한다.".format(keep),
        "문의: 연구책임자 {} 전화 {}".format(pi, contact), extra,
    ])


def md_icf(kind="성인용", visits="총 2회 방문", dur="약 90분", comp="교통비 실비", n="총 90명(시험군 45명, 대조군 45명)",
           age="만 19~45세", keep="3년", ver="1.2", date="2026-08-25", title=TITLE, pi=PI, contact=OFFICE, extra="") -> str:
    return "\n".join([
        "# 연구대상자 설명문 및 동의서 ({})".format(kind), "", "| 연구제목 | {} |".format(title), "|---|---|",
        "| 버전 | {} |".format(ver), "| 날짜 | {} |".format(date), "",
        "귀하는 본 연구에 참여하도록 요청받았습니다. 참여는 자발적입니다.", "연구책임자: {} ({})".format(pi, ORG),
        "## 연구 대상", "본 연구에는 {} 이 참여합니다. 연령은 {} 입니다.".format(n, age),
        "## 연구 절차", "귀하는 {}을 하게 되며, 1회 방문 시 {}이 소요됩니다.".format(visits, dur),
        "방문 시 불면증심각도척도(ISI) 와 수면일지, 삶의질 설문지를 작성합니다.",
        "## 보상", "연구 참여에 대한 보상으로 {}가 지급됩니다.".format(comp),
        "## 개인정보", "귀하의 개인정보는 연구 종료 후 {}간 보관된 뒤 폐기됩니다.".format(keep),
        "## 연락처", "문의: {} 연구책임자, 전화 {}".format(pi, contact), "위 내용을 이해하였으며 자발적으로 동의합니다.", extra,
    ])


def md_crf(visits=2, forms=("불면증심각도척도 (ISI)", "수면일지", "삶의질 설문지"), ver="1.2", date="2026-08-25", title=TITLE) -> str:
    lines = ["# 증례기록서 (CRF)", "", "| 연구제목 | {} |".format(title), "|---|---|", "| Version | {} |".format(ver),
             "| Date | {} |".format(date), "| 대상자 번호 | ____ |", "", "## 방문 일정", "| 회기 | 일자 |", "|---|---|"]
    lines += ["| Visit {} | ____ |".format(i) for i in range(1, visits + 1)]
    lines += ["", "## 평가 폼"] + list(forms)
    return "\n".join(lines)


def md_ad(contact=OFFICE, n="시험군 45명, 대조군 45명 (총 90명)", age="만 19~45세", ver="1.2", title=TITLE, extra="", comp="교통비 실비") -> str:
    return "\n".join([
        "# 연구대상자 모집 공고", "{} 에서 연구 참여자를 모집합니다.".format(ORG), "",
        "| 연구제목 | {} |".format(title), "|---|---|", "| 버전 | {} |".format(ver), "",
        "모집 대상: {} 성인. 모집 인원: {}.".format(age, n), "총 2회 방문하며 1회 방문 시 약 90분이 소요됩니다.",
        "참여자에게는 {} 가 지급됩니다.".format(comp), "문의: {} 연구책임자 / 연락처: {}".format(PI, contact), extra,
    ])


def write_packet(tmp_path, files: Dict[str, str], sub="packet") -> str:
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    for name, text in files.items():
        if isinstance(text, bytes):
            (d / name).write_bytes(text)
        else:
            (d / name).write_text(text, encoding="utf-8")
    return str(d)


def full_packet(tmp_path, **over) -> str:
    files = {
        "연구계획서_v1.2.md": over.get("protocol", md_protocol()),
        "ICF_성인용_v1.2.md": over.get("icf", md_icf()),
        "CRF_v1.2.md": over.get("crf", md_crf()),
        "모집공고_v1.2.md": over.get("ad", md_ad()),
    }
    files.update(over.get("extra_files", {}))
    for k in over.get("drop", ()):
        files.pop(k, None)
    return write_packet(tmp_path, files, sub=over.get("sub", "packet"))


def run(path: str, roles: Optional[List[Tuple[str, str]]] = None, baseline: Optional[str] = None) -> Tuple[Result, List[str]]:
    files, err = cli._collect([path])
    assert not err, err
    bfiles = None
    if baseline:
        bfiles, berr = cli._collect([baseline])
        assert not berr
    safeio.clear_protected()
    safeio.protect_inputs(files + (bfiles or []))
    return cli.run_packet(files, roles or [], bfiles)


def by_sev(res: Result, sev: str) -> List[Issue]:
    return [i for i in res.issues if i.severity == sev]


def items_of(issues: Sequence[Issue]) -> List[str]:
    return [i.item for i in issues]


@pytest.fixture
def clean_safeio():
    safeio.clear_protected()
    yield
    safeio.clear_protected()
