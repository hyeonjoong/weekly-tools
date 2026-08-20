"""테스트 공용 도구 — 전부 오프라인, 전부 합성 데이터."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visitaudit.protocol import Criterion, PPRules, Protocol, VisitDef  # noqa: E402
from visitaudit.tables import Subject, _make_record  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")

# 예제와 같은 SERENE 풍 프로토콜 (합성)
PROTOCOL_JSON = {
    "연구명": "SERENE-TEST",
    "기준방문": "Baseline",
    "목표N": 120,
    "방문": [
        {"이름": "Screening", "오프셋": -14, "창": [-14, 13], "필수": True},
        {"이름": "Baseline", "오프셋": 0, "창": [0, 0], "필수": True},
        {"이름": "V1", "오프셋": 28, "창": [-3, 3], "필수": True},
        {"이름": "V2", "오프셋": 56, "창": [-5, 5], "필수": True},
        {"이름": "EOT", "오프셋": 84, "창": [-7, 7], "필수": True},
    ],
    "선정기준": [
        {"항목": "age", "연산": ">=", "값": 19},
        {"항목": "ISI_baseline", "연산": ">=", "값": 15},
    ],
    "제외기준": [{"항목": "OSA진단", "연산": "==", "값": "Y"}],
    "PP제외규칙": {"필수방문결측": True, "창이탈일수초과": 7},
}


def d(y: int, m: int, day: int) -> dt.date:
    return dt.date(y, m, day)


def rec(sid: str, visit: str, date: str, status: str = "", row: int = 2):
    """VisitRecord 하나 (tables 의 실제 생성 경로를 그대로 사용)."""
    return _make_record(sid, visit, date, status, row)


def subj(sid: str, arm: str = "A", enroll: str = "", dropout: str = "",
         dropout_reason: str = "", screenfail: str = "", row: int = 2,
         **extras) -> Subject:
    s = Subject(sid=sid, row_no=row, arm=arm, dropout_reason=dropout_reason,
                screenfail_reason=screenfail)
    if enroll:
        from visitaudit.dates import parse_date
        s.enroll_raw = enroll
        p = parse_date(enroll)
        s.enroll, s.enroll_error = p.date, p.error
    if dropout:
        from visitaudit.dates import parse_date
        s.dropout_raw = dropout
        p = parse_date(dropout)
        s.dropout, s.dropout_error = p.date, p.error
    s.extras = {k: str(v) for k, v in extras.items()}
    return s


def mini_protocol(pp: PPRules = None, incl=None, excl=None) -> Protocol:
    """Baseline + V1(오프셋 28, 창 ±3) 두 방문짜리 최소 프로토콜."""
    return Protocol(
        study="MINI", anchor="Baseline", target_n=None,
        visits=[VisitDef("Baseline", 0, 0, 0, True), VisitDef("V1", 28, -3, 3, True)],
        inclusion=incl or [], exclusion=excl or [], pp_rules=pp,
    )


def crit(item: str, op: str, value, kind: str = "선정") -> Criterion:
    return Criterion(item=item, op=op, value=value, kind=kind)


@pytest.fixture
def protocol_file(tmp_path):
    """표준 테스트 프로토콜 JSON 파일 경로."""
    path = tmp_path / "프로토콜.json"
    path.write_text(json.dumps(PROTOCOL_JSON, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_csv(tmp_path, name: str, text: str, encoding: str = "utf-8") -> str:
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return str(path)


def run_cli(args):
    """cli.main 을 프로세스 없이 호출 → 종료코드."""
    from visitaudit.cli import main
    return main(args)
