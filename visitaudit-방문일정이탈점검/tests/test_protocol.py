"""프로토콜 로딩·검증 — 깨진 프로토콜로는 판정을 시작하면 안 된다."""

import json

import pytest

from tests.conftest import PROTOCOL_JSON
from visitaudit.protocol import ProtocolError, load_protocol


def _write(tmp_path, obj) -> str:
    p = tmp_path / "p.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_loads_korean_keys(tmp_path):
    proto = load_protocol(_write(tmp_path, PROTOCOL_JSON))
    assert proto.study == "SERENE-TEST"
    assert proto.anchor == "Baseline"
    assert proto.target_n == 120
    assert proto.visit_names() == ["Screening", "Baseline", "V1", "V2", "EOT"]
    v1 = proto.get_visit("V1")
    assert (v1.offset, v1.win_lo, v1.win_hi, v1.required) == (28, -3, 3, True)
    assert len(proto.inclusion) == 2 and len(proto.exclusion) == 1
    assert proto.pp_rules.missing_required is True
    assert proto.pp_rules.max_days_out == 7
    # 명시 없으면 켜지는 규칙 (README 에 문서화됨)
    assert proto.pp_rules.eligibility_violation is True
    assert proto.pp_rules.dropout is True


def test_loads_english_aliases(tmp_path):
    obj = {
        "study": "S", "anchor": "BL", "target_n": 50,
        "visits": [
            {"name": "BL", "offset": 0, "window": [0, 0], "required": True},
            {"name": "W4", "offset": 28, "window": [-3, 3]},
        ],
        "pp_rules": {"missing_required": True, "max_days_out": 5},
    }
    proto = load_protocol(_write(tmp_path, obj))
    assert proto.anchor == "BL"
    assert proto.get_visit("W4").required is True  # 기본값 true
    assert proto.pp_rules.max_days_out == 5


def test_missing_file():
    with pytest.raises(ProtocolError, match="파일이 없습니다"):
        load_protocol("/없는/경로/프로토콜.json")


def test_broken_json(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ 이건 JSON 이 아님", encoding="utf-8")
    with pytest.raises(ProtocolError, match="깨져"):
        load_protocol(str(p))


@pytest.mark.parametrize("mutate,msg", [
    (lambda o: o.pop("기준방문"), "기준방문"),
    (lambda o: o.pop("방문"), "방문"),
    (lambda o: o.update({"방문": []}), "방문"),
    (lambda o: o.update({"기준방문": "없는방문"}), "방문 목록에 없습니다"),
    (lambda o: o["방문"].append({"이름": "V1", "오프셋": 99, "창": [0, 0]}), "중복"),
    (lambda o: o["방문"][2].update({"창": [3, -3]}), "창 시작"),
    (lambda o: o["방문"][2].update({"창": [0]}), "두 정수"),
    (lambda o: o["방문"][2].update({"오프셋": "스물여덟"}), "정수"),
    (lambda o: o.update({"목표N": 0}), "목표N"),
    (lambda o: o.update({"선정기준": [{"항목": "age", "연산": "~=", "값": 1}]}), "연산"),
    (lambda o: o.update({"선정기준": [{"연산": ">=", "값": 1}]}), "항목"),
    (lambda o: o.update({"PP제외규칙": {"창이탈일수초과": -1}}), "0 이상"),
])
def test_invalid_protocols(tmp_path, mutate, msg):
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    mutate(obj)
    with pytest.raises(ProtocolError, match=msg):
        load_protocol(_write(tmp_path, obj))


def test_toplevel_not_object(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ProtocolError, match="객체"):
        load_protocol(str(p))


def test_bom_tolerated(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(PROTOCOL_JSON, ensure_ascii=False).encode("utf-8"))
    assert load_protocol(str(p)).anchor == "Baseline"


def test_no_pp_rules_is_none(tmp_path):
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj.pop("PP제외규칙")
    assert load_protocol(_write(tmp_path, obj)).pp_rules is None
