"""라운드 3 — 견고성·정직성 회귀 테스트.

전부 실제로 크래시했거나, 멈췄거나, **조용히 통과 도장을 찍었던** 입력이다.
이 파일에서 가장 중요한 것은 "잘라 놓고 이상 없음이라고 말하지 않는다" 쪽이다:
그게 이 툴의 존재 이유를 직접 무너뜨리는 실패였다.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from conftest import analyze_text
from numcheck.docio import (
    MAX_LINE_CHARS,
    ManuscriptError,
    manuscript_from_text,
    read_manuscript,
)
from numcheck.engine import analyze_manuscript
from numcheck.pvalues import Statistic, find_statistics, p_range
from numcheck.report import write_csvs
from numcheck.rounding import parse_number
from numcheck.scales import ScaleError, load_scale_config

CLEAN_BODY = "\n".join(
    f"In group {i}, {i}/48 ({i / 48 * 100:.1f}%) responded, t(46) = 3.05, p = 0.004."
    for i in range(1, 9)
)


# ── 잘라 냈으면 '이상 없음' 이라고 말하지 않는다 ─────────────────────────────
# 이전 동작: 20,000자를 넘는 줄의 뒷부분을 버리고도 종료코드 0 + "문제를 찾지
# 못했습니다" 를 냈다. 잘린 뒤에 있던 치명 오류는 흔적도 남지 않았다.


def test_truncated_line_cannot_report_all_clear():
    long_row = "| " + " | ".join(f"cell {i}" for i in range(3000)) + " |"
    report = analyze_text(f"## Results\n{CLEAN_BODY}\n{long_row}\n")
    assert report.truncated is True
    assert report.exit_code() == 3


def test_untruncated_manuscript_still_reports_clean():
    report = analyze_text(f"## Results\n{CLEAN_BODY}\n")
    assert report.truncated is False
    assert report.exit_code() == 0


def test_truncation_does_not_mask_real_findings():
    """잘렸더라도 찾은 치명은 치명으로 나와야 한다(3 이 1 을 덮으면 안 된다)."""
    long_row = "| " + " | ".join(f"cell {i}" for i in range(3000)) + " |"
    report = analyze_text(
        f"## Results\n{CLEAN_BODY}\n전체 48명 중 23명 (12.3%) 이 반응하였다.\n{long_row}\n")
    assert report.truncated is True
    assert report.exit_code() == 1


def test_line_limit_also_sets_truncated():
    ms = manuscript_from_text("\n".join(["ok"] * 400_050) + "\n", "md")
    assert ms.truncated is True


def test_long_line_is_actually_cut_at_the_limit():
    ms = manuscript_from_text("x" * (MAX_LINE_CHARS + 500) + "\n", "md")
    assert max(len(ln.text) for ln in ms.lines) <= MAX_LINE_CHARS


# ── 산술적으로 불가능한 통계량은 버리지 않는다 ───────────────────────────────


@pytest.mark.parametrize("text,cue", [
    ("r(38) = 1.5, p = 0.01.", "상관계수"),
    ("r(38) = -2.7, p = 0.44.", "상관계수"),
    ("t(0) = 2.3, p = 0.03.", "자유도"),
    ("F(0, 0) = 1, p = 0.5.", "자유도"),
    ("chi2(3) = -5, p = 0.02.", "음수"),
    ("F(2, 88) = -4.12, p = 0.02.", "음수"),
])
def test_impossible_statistics_are_critical_findings(text, cue):
    report = analyze_text("## Results\n" + text + "\n")
    got = [f for f in report.findings if f.item == "검정통계량"]
    assert len(got) == 1 and got[0].level == "치명", text
    assert cue in got[0].message, text


def test_impossible_statistic_does_not_leave_a_lying_skip_reason():
    """이전 동작: 통계량을 버려서 짝 없는 p 가 '검정통계량 없음' 으로 기록됐다 —
    같은 줄에 통계량이 인쇄돼 있는데도. CSV 가 원고에 대해 거짓을 말했다."""
    report = analyze_text("## Results\nThe correlation was r(38) = 1.5, p = 0.01.\n")
    assert not any(c.skip_reason == "검정통계량 없음" for c in report.claims)


def test_valid_statistics_are_not_swept_up():
    for text in ("r(38) = 0.41, p = .01.", "r(38) = -0.41, p = .01.",
                 "F(2, 88) = 4.12, p = .02.", "chi2(3) = 5.0, p = .17."):
        report = analyze_text("## Results\n" + text + "\n")
        assert [f for f in report.findings if f.item == "검정통계량"] == [], text


# ── 판별력 없는 p 구간을 '일치' 라고 말하지 않는다 ───────────────────────────
# 이전 동작: `r(38) = 1, p = 0.99` 를 "일치" 로 기록했다. 재계산값 6.7e-110 을
# 같은 행에 인쇄해 놓고서.


def test_integer_r_does_not_match_everything():
    report = analyze_text("## Results\nAlso r(38) = 1, p = 0.99.\n")
    rows = [c for c in report.claims if c.item == "p 재계산"]
    assert rows and all(c.verdict != "일치" for c in rows)


def test_integer_z_is_judged_against_its_real_rounding_interval():
    """`z = 0` 은 반올림하면 |z| ∈ [0, 1] 이므로 p = 0.99 가 실제로 가능하다.

    여기서 지적하면 오탐이다 — 자릿수가 낮은 보고에서 관대해지는 것은 설계이지
    버그가 아니다. 진짜 문제였던 `r = 1` 은 위 테스트가 지킨다.
    """
    report = analyze_text("## Results\nThe test gave z = 0, p = 0.99.\n")
    assert [f for f in report.findings if f.item == "p 재계산"] == []


# ── 성능: 한 줄에 비율 토큰이 많아도 선형에 가깝게 끝난다 ────────────────────
# 이전 동작: 17KB 한 줄에서 53초. `_context_denominators` 가 후보마다
# blocked 목록을 선형으로 훑어 M³ 이 됐다.


def test_dense_proportion_line_is_not_cubic():
    segment = ",".join(f"{i % 90 + 1}명({i % 90 + 1}.0%)" for i in range(1600))
    text = ("총 100명 중 " + segment)[:MAX_LINE_CHARS]
    started = time.monotonic()
    analyze_text("## Results\n" + text + "\n")
    assert time.monotonic() - started < 15.0


# ── 오버플로: `1e400` 이 트레이스백 + 종료코드 1 을 냈다 ─────────────────────


@pytest.mark.parametrize("token", ["1e400", "1e-400", "1E999", "2.5e400"])
def test_absurd_exponents_are_rejected_not_crashed(token):
    assert parse_number(token) is None


def test_absurd_exponent_in_a_manuscript_does_not_crash():
    report = analyze_text(f"## Results\nThe effect was z = 1e400.\n{CLEAN_BODY}\n")
    assert report.exit_code() in (0, 1, 2)


# ── --scale-config 의 잘못된 값이 트레이스백으로 터지지 않는다 ───────────────


@pytest.mark.parametrize("spec", [
    {"X": {"min": 0, "max": 100, "items": 7, "unit": "x"}},
    {"X": {"min": 0, "max": 100, "items": 7, "unit": None}},
    {"X": {"min": 0, "max": 100, "items": 0, "percent_of_count": True}},
    {"X": {"min": 0, "max": 100, "items": 7, "aliases": 5}},
    {"X": {"min": 0, "max": "inf", "items": 7}},
    {"X": {"min": 0, "max": 100, "items": 7, "aliases": [1, 2, 3]}},
])
def test_malformed_scale_config_raises_a_clean_error(tmp_path, spec):
    path = tmp_path / "scales.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ScaleError):
        load_scale_config(path)


def test_a_good_scale_config_still_loads(tmp_path):
    path = tmp_path / "scales.json"
    path.write_text(json.dumps(
        {"ISI": {"min": 0, "max": 28, "items": 7, "aliases": ["불면증 심각도"]}}),
        encoding="utf-8")
    scales = load_scale_config(path)
    assert len(scales) == 1 and scales[0].items == 7


# ── 파이프·장치 파일에서 멈추지 않는다 ──────────────────────────────────────
# 이전 동작: st_size 가 0 이라 크기 상한을 통과한 뒤 read() 에서 영원히 멈췄다.
# `numcheck <(pandoc ...)` 로 평범하게 도달한다.


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo 가 없는 플랫폼")
def test_fifo_is_refused_instead_of_hanging(tmp_path):
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)
    with pytest.raises(ManuscriptError) as exc:
        read_manuscript(fifo)
    assert "일반 파일이 아닙니다" in str(exc.value)


def test_character_device_is_refused():
    if not os.path.exists("/dev/zero"):
        pytest.skip("/dev/zero 가 없는 플랫폼")
    with pytest.raises(ManuscriptError):
        read_manuscript("/dev/zero")


# ── 평문 chi2 표기 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["chi2", "Chi2", "chi 2", "χ²", "χ2", "X2",
                                  "chi-square", "카이제곱"])
def test_plain_ascii_chi_square_spellings(name):
    stats = find_statistics(f"{name}(3) = 8.14, p = .04")
    assert len(stats) == 1 and stats[0].kind == "chi2", name


# ── 거절한 실행은 빈 출력 폴더를 남기지 않는다 ──────────────────────────────


def test_refused_write_does_not_leave_an_empty_directory(tmp_path):
    from numcheck.report import OutputRefused

    target = tmp_path / "검토"
    target.mkdir()
    (target / "문제목록.csv").write_text("남의 파일입니다", encoding="utf-8")
    report = analyze_manuscript(manuscript_from_text(f"## Results\n{CLEAN_BODY}\n"))
    with pytest.raises(OutputRefused):
        write_csvs(report, target)
    assert (target / "문제목록.csv").read_text(encoding="utf-8") == "남의 파일입니다"


def test_fresh_directory_is_created_and_written(tmp_path):
    target = tmp_path / "새폴더" / "검토"
    report = analyze_manuscript(manuscript_from_text(f"## Results\n{CLEAN_BODY}\n"))
    written = write_csvs(report, target)
    assert len(written) == 3 and all(p.exists() for p in written)


# ── F 의 p 구간이 대각 코너를 포함하는지 (수치) ─────────────────────────────


def test_f_p_range_diagonal_corner_bounds():
    stat = Statistic((0, 0), "F", parse_number("1.41"), (2.1, 5.3), False, "", "F 검정")
    lo, hi = p_range(stat, 1.0, "two")
    assert lo <= 0.31949 + 1e-5
    assert hi >= 0.33044 - 1e-5


# ── D1: zip 중앙 디렉터리에 적힌 크기를 믿지 않는다 ──────────────────────────
# 이전 동작: 148바이트라고 선언한 1MB .docx 가 실제로 1GB 로 풀려도 상한 검사를
# 그대로 통과했다(RSS 2GB). `zf.read()` 는 **선언된** 크기를 믿기 때문이다.


def _bomb_docx(path, payload_mb: int = 64) -> None:
    """선언 크기는 작지만 실제로는 거대한 .docx 를 만든다."""
    import struct
    import zipfile
    import zlib

    prefix = (b'<?xml version="1.0"?><w:document '
              b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              b"<w:body><w:p><w:r><w:t>23/48 (47.9%)</w:t></w:r></w:p></w:body>"
              b"</w:document>")
    real = prefix + b" " * (payload_mb * 1024 * 1024)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", real)
    raw = bytearray(path.read_bytes())
    # 중앙 디렉터리와 로컬 헤더의 uncompressed-size 를 prefix 길이로 위조하고,
    # CRC 도 prefix 기준으로 바꿔 검증을 통과시킨다.
    fake_len, crc = len(prefix), zlib.crc32(prefix) & 0xFFFFFFFF
    central = raw.rfind(b"PK\x01\x02")
    local = raw.find(b"PK\x03\x04")
    raw[central + 16:central + 20] = struct.pack("<I", crc)
    raw[central + 24:central + 28] = struct.pack("<I", fake_len)
    raw[local + 14:local + 18] = struct.pack("<I", crc)
    raw[local + 22:local + 26] = struct.pack("<I", fake_len)
    path.write_bytes(bytes(raw))


def test_declared_zip_size_cannot_smuggle_a_huge_part(tmp_path):
    bomb = tmp_path / "bomb.docx"
    _bomb_docx(bomb)
    try:
        ms = read_manuscript(bomb)
    except ManuscriptError:
        return  # 거부한 것도 정답
    # 거부하지 않았다면 최소한 **실제로 읽은 양**이 상한 안이어야 한다.
    assert sum(len(ln.text) for ln in ms.lines) < 20 * 1024 * 1024


# ── D3: 자리 구분 숫자 열에서 역추적이 폭발하지 않는다 ──────────────────────
# 이전 동작: `'1' + ',000'*4999` (2만 자) 한 줄에 16초, 400KB 파일에 6분 33초.


def test_comma_grouped_digit_run_does_not_backtrack(tmp_path):
    payload = "1" + ",000" * 4_990
    started = time.monotonic()
    analyze_text("## Results\n" + payload + "\n")
    assert time.monotonic() - started < 5.0


def test_normal_thousands_separators_still_parse():
    report = analyze_text("## Results\n전체 1,200명 중 600명 (50.0%) 이 반응하였다.\n")
    assert any(c.item == "비율 재계산" and c.verdict == "일치" for c in report.claims)


# ── 원고 자체를 덮어쓰지 않는다 (이름이 아니라 파일 동일성으로) ─────────────
# 이전 동작: macOS 가 한글 파일명을 NFD 로 저장하므로 `요약.txt` 이름 비교가
# 갈라져 가드를 통과했고, `--force` 로 **원고가 파괴됐다**.


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_manuscript_named_like_an_output_file_is_never_overwritten(tmp_path, form):
    import hashlib
    import unicodedata

    from numcheck.cli import main

    name = unicodedata.normalize(form, "요약.txt")
    path = tmp_path / name
    path.write_text(f"## Results\n{CLEAN_BODY}\n", encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    code = main([str(path), "--out-dir", str(tmp_path), "--force", "--quiet"])
    assert code == 3, form
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before, form


def test_check_targets_refuses_the_manuscript_by_identity(tmp_path):
    from numcheck.report import OutputRefused, check_targets

    manuscript = tmp_path / "요약.txt"
    manuscript.write_text("numcheck — 원고 수치 재계산 검증\n이전 산출물처럼 보임",
                          encoding="utf-8")
    # --force 로도, '우리 산출물처럼 보여도' 허용하지 않는다.
    with pytest.raises(OutputRefused):
        check_targets(tmp_path, force=True, manuscript=manuscript)


# ── 변이 테스트에서 살아남은 구멍들 (M5 · M17 · M19) ────────────────────────


def test_grim_power_threshold_is_pinned():
    """M5: 배수를 2.0 → 1.0 으로 바꿔도 아무 테스트가 안 깨졌다. 그 변이는
    정직하게 건너뛴 claim 을 '재계산했다' 로 바꿔 — 커버리지 자백이 거짓말이 된다."""
    from numcheck.grim import grim_has_power

    assert grim_has_power(parse_number("14.37"), 7, 1.0, 1.0) is True
    assert grim_has_power(parse_number("14.3"), 7, 1.0, 1.0) is False


def test_grim_power_is_reflected_in_the_coverage_confession():
    from numcheck.scales import ScaleRegistry, parse_scale_arg

    reg = ScaleRegistry()
    reg.add(parse_scale_arg("ISI=0:28:7"))
    report = analyze_text("## Results\nISI 평균은 14.3 (N = 7) 이었다.\n", registry=reg)
    grim = [c for c in report.claims if "GRIM" in c.item]
    assert grim and all(not c.checked for c in grim)
    assert all(c.skip_reason == "판별력 없음" for c in grim)


def test_hedged_significance_wording_is_still_reported():
    """M17: HEDGE_MAX_MULTIPLE 을 3.0 → 30.0 으로 늘려도 아무도 안 깨졌다."""
    report = analyze_text("## Results\nThe effect was marginally significant (p = 0.51).\n")
    got = [f for f in report.findings if f.item == "유의성 문구"]
    assert len(got) == 1 and got[0].level == "치명"


def test_hedged_wording_just_outside_the_window_is_silent():
    report = analyze_text("## Results\nThe effect was marginally significant (p = 0.12).\n")
    assert [f for f in report.findings if f.item == "유의성 문구"] == []


def test_effective_k_requires_all_values_to_be_integers():
    """M19: all() → any() 로 바꿔도 안 깨졌다. 그 변이는 오탐을 만든다 —
    `3.0 (95% CI 4 to 9)` 이 치명이 된다."""
    from numcheck.rounding import effective_k

    ints = [parse_number("3"), parse_number("4"), parse_number("9")]
    mixed = [parse_number("3.0"), parse_number("4"), parse_number("9")]
    assert effective_k(1.0, *ints) == pytest.approx(0.5)
    assert effective_k(1.0, *mixed) == pytest.approx(1.0)


def test_mixed_integer_and_decimal_ci_is_not_flagged():
    report = analyze_text("## Results\n군간 차이는 3.0 (95% CI 4 to 9) 이었다.\n")
    assert [f for f in report.findings if f.item == "신뢰구간 정합"] == []
