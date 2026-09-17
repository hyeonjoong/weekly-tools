"""리포트 — `[커버리지 자백]` 없는 리포트는 내보내지 않는다."""

import pytest

from doseaudit.audit import (LEVEL_CRITICAL, LEVEL_INFO, LEVEL_WARN, Config, run_audit)
from doseaudit.errors import ReportIntegrityError
from doseaudit.report import (CONFESSION_HEADER, NOT_EXAMINED, confession_lines,
                              declaration_lines, methods_draft_en, methods_draft_kr,
                              render_console, render_markdown, sensitivity_lines,
                              _assert_confession)
from doseaudit.rules import ActiveDayRule, Window


@pytest.fixture
def flawed_result(flawed_logs, flawed_tracker):
    return run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))


@pytest.fixture
def clean_result(clean_logs, clean_tracker):
    return run_audit(Config(
        logs=clean_logs, tracker_path=clean_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))


def test_자백이_빠지면_출력하지_않고_죽는다():
    with pytest.raises(ReportIntegrityError):
        _assert_confession("치명 0건 · 경고 0건 → 종료코드 0")


def test_콘솔_리포트에_자백이_있다(flawed_result):
    assert CONFESSION_HEADER in "\n".join(render_console(flawed_result))


def test_마크다운에도_자백이_있다(flawed_result):
    text = render_markdown(flawed_result, render_console(flawed_result))
    assert CONFESSION_HEADER in text


def test_깨끗한_자료에서도_자백은_나온다(clean_result):
    """조용한 리포트일수록 '무엇을 안 봤는지'가 중요하다."""
    assert CONFESSION_HEADER in "\n".join(render_console(clean_result))


@pytest.mark.parametrize("idx", range(len(NOT_EXAMINED)))
def test_안_보는_것들이_전부_인쇄된다(clean_result, idx):
    text = "\n".join(confession_lines(clean_result))
    assert NOT_EXAMINED[idx] in text


def test_자백에_읽은_수와_시도한_수가_같이_나온다(clean_result):
    assert "워크북 4/4" in "\n".join(confession_lines(clean_result))


def test_선언된_정의가_판정보다_먼저_인쇄된다(flawed_result):
    lines = render_console(flawed_result)
    declaration = next(i for i, l in enumerate(lines) if "선언된 정의" in l)
    first_finding = next(i for i, l in enumerate(lines) if l.startswith("[치명]"))
    assert declaration < first_finding


def test_선언_블록에_규칙과_창과_상한이_모두_있다(flawed_result):
    text = "\n".join(declaration_lines(flawed_result))
    assert "any-row" in text
    assert "2026-04-27" in text
    assert "상한 절단 없음" in text
    assert "REGULAR" in text


def test_콘솔_불일치는_다섯_줄까지만(flawed_result):
    """23명을 콘솔에 쏟아내면 두 번 열지 않는다."""
    finding = next(f for f in flawed_result.findings if f.level == LEVEL_CRITICAL)
    code_lines = [l for l in finding.lines if "트래커" in l and "↔" in l]
    assert len(code_lines) <= 5


def test_같은_방향_불일치는_한_문장으로_묶는다(flawed_result):
    finding = next(f for f in flawed_result.findings
                   if f.level == LEVEL_CRITICAL and "완료일수" in f.title)
    assert any("정의 차이" in l for l in finding.lines)


def test_깨끗한_자료는_치명도_경고도_없다(clean_result):
    """오탐 억제 — 여기서 경고가 뜨면 그건 툴의 잘못이다."""
    assert clean_result.count(LEVEL_CRITICAL) == 0
    assert clean_result.count(LEVEL_WARN) == 0
    assert clean_result.exit_code == 0


def test_결함_자료는_치명과_경고를_모두_낸다(flawed_result):
    assert flawed_result.count(LEVEL_CRITICAL) >= 2
    assert flawed_result.count(LEVEL_WARN) >= 3
    assert flawed_result.exit_code == 1


def test_빈_모듈은_치명이고_평균_0_이라고_말하지_않는다(flawed_result):
    finding = next(f for f in flawed_result.findings if "발성훈련" in f.title)
    assert finding.level == LEVEL_CRITICAL
    assert "데이터 없음" in finding.title
    body = "\n".join(finding.lines)
    assert "데이터 없음" in body and "0을 측정함" in body


def test_중복의심은_경고가_아니라_정보다(flawed_result):
    """문항 하나가 한 행인 모듈에서는 지문이 겹치는 게 정상이다 — 울면 안 된다."""
    finding = next(f for f in flawed_result.findings if "중복의심" in f.title)
    assert finding.level == LEVEL_INFO


def test_민감도_표는_최소_네_규칙을_낸다(flawed_result):
    lines = sensitivity_lines(flawed_result)
    rule_lines = [l for l in lines if "%" in l and "→" not in l]
    assert len(rule_lines) >= 4


def test_민감도_표는_어느_값도_권장하지_않는다(flawed_result):
    text = "\n".join(sensitivity_lines(flawed_result))
    for word in ("권장", "추천", "이 값을 쓰세요", "바람직", "적절한 값", "올바른"):
        assert word not in text
    assert "어느 값이 옳은지 말하지 않습니다" in text


@pytest.mark.parametrize("draft", [methods_draft_kr, methods_draft_en])
def test_Methods_초안에_선언된_정의가_박힌다(flawed_result, draft):
    text = draft(flawed_result)
    assert "2026-04-27" in text
    assert "3" in text
    assert len(text) > 200


def test_한국어_초안과_영어_초안이_다르다(flawed_result):
    assert methods_draft_kr(flawed_result) != methods_draft_en(flawed_result)


def test_영어_초안에_퍼센트가_깨져_있지_않다(flawed_result):
    assert "%%" not in methods_draft_en(flawed_result)


def test_초안이_빈_모듈을_측정되지_않았다로_쓴다(flawed_result):
    assert "측정되지 않았다" in methods_draft_kr(flawed_result)
    assert "absence of measurement" in methods_draft_en(flawed_result)


def test_상한을_주면_절단된_인원수를_인쇄한다(flawed_logs, flawed_tracker):
    result = run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("fixed-weeks=1", None),
        enroll_source=None, cap=100.0, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))
    assert "절단" in methods_draft_kr(result)
    assert "명이다" in methods_draft_kr(result)


def test_마크다운에_모듈표와_민감도표가_있다(flawed_result):
    text = render_markdown(flawed_result, render_console(flawed_result))
    assert "## 모듈별 요약" in text
    assert "## 정의 민감도" in text
    assert "## Methods 초안 (KR)" in text
    assert "## Methods draft (EN)" in text


def test_마크다운에_절대경로가_새지_않는다(flawed_result):
    """산출물은 공유되는 물건이다 — 홈 디렉터리 경로가 섞이면 계정 이름이 나간다."""
    import os
    text = render_markdown(flawed_result, render_console(flawed_result))
    assert os.path.expanduser("~") not in text
    assert "/Users/" not in text


def test_종료코드_줄이_맨_아래에_있다(flawed_result):
    assert render_console(flawed_result)[-1].startswith("치명 ")
