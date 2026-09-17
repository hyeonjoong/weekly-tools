"""`--inspect` — 판정 이전에 '어떻게 읽었는지'를 보여 주는 화면.

이 화면의 일은 `--active-day` 를 필수로 둔 대가를 갚는 것이다: 고를 수 있는
규칙을 **각각의 결과값과 함께** 늘어놓고, 복사해서 바로 쓸 수 있는 실행 줄까지 준다.
"""

import pytest

from doseaudit.inspection import render_inspect
from doseaudit.logs import load_bundle
from doseaudit.rules import CONTRAST_RULES, CONTRAST_WINDOWS
from doseaudit.tracker import load_tracker


@pytest.fixture
def flawed_bundle(flawed_logs):
    return load_bundle(flawed_logs)


@pytest.fixture
def text(flawed_bundle, flawed_logs, flawed_tracker):
    return "\n".join(render_inspect(flawed_bundle, load_tracker(flawed_tracker),
                                    logs_arg=flawed_logs, tracker_arg=flawed_tracker))


def test_판정하지_않는다고_먼저_말한다(text):
    assert text.splitlines()[0].startswith("[점검] 판정하지 않습니다")
    assert "[치명]" not in text
    assert "[경고]" not in text


def test_읽은_수를_인쇄한다(text):
    assert "워크북 6개" in text and "시트 36장" in text and "데이터 91행" in text


def test_기대한_헤더_5열을_인쇄한다(text):
    assert "날짜 · 레벨 · 훈련유형 · 반복횟수 · 소요시간(초)" in text


def test_실제로_읽은_시트_수로_말한다(text):
    """실데이터의 180 이 상수로 박혀 있던 자리."""
    assert "36장 중" in text
    assert "180장" not in text


def test_피험자_코드와_헤더_위치를_보여_준다(text):
    assert "피험자 코드(파일명 첫 토큰)" in text
    assert "헤더 = 엑셀 5행" in text
    assert "[요약] 블록 있음" in text


def test_LoginID_를_인쇄하지_않는다(text):
    for login in ("echo", "foxtrot", "golf", "hotel", "india", "juliet"):
        assert "_%s_" % login not in text


def test_데이터_0행_모듈을_따로_짚어_준다(text):
    assert "모든 워크북에서 데이터가 0행인 모듈: `발성훈련`" in text
    assert "'0을 측정함'이 아니라 '데이터 없음'입니다" in text


def test_트래커의_열지_않은_열과_격자를_말한다(text):
    assert "열지 않은 열(개인식별): 환자명, Login ID" in text
    assert "읽지 않은 일자별 격자 열: D1…D20" in text


@pytest.mark.parametrize("spec", CONTRAST_RULES)
def test_고를_수_있는_규칙과_결과값을_나란히_보여_준다(text, spec):
    line = next(l for l in text.splitlines() if l.strip().startswith(spec))
    assert "일" in line          # 활동일수 중앙값과 합계


@pytest.mark.parametrize("spec", CONTRAST_WINDOWS)
def test_분모의_창도_고르게_한다(text, spec):
    """활동일 규칙만 보여 주면, 더 큰 지렛대를 숨기는 셈이 된다."""
    assert spec in text
    assert "분모의 창은 **이보다 더 크게** 움직입니다" in text


@pytest.mark.parametrize("spec", CONTRAST_RULES)
def test_복사해서_바로_쓸_수_있는_실행_줄을_준다(text, spec):
    """네 개의 인자를 다시 타이핑하게 두면 선언이 상용구가 된다."""
    line = next(l for l in text.splitlines()
                if l.strip().startswith("doseaudit --logs") and "--active-day %s " % spec in l)
    for fragment in ("--window", "--target-per-week", "--out-dir"):
        assert fragment in line


def test_트래커가_없으면_대조가_꺼졌다고_말한다(flawed_bundle, flawed_logs):
    """사용법은 ①`--inspect` → ②복사해서 실행 을 안내한다.

    그 경로에서 `--tracker` 가 조용히 빠지면, 사용자는 **이 툴의 머리 기사**
    (트래커 `완료일수` 대조 — 실데이터에서 30명 중 23명)가 꺼진 줄도 모른 채
    실행하게 된다. 빠뜨렸다는 사실을 말해 주고, 복사용 줄에 자리를 남긴다.
    """
    text = "\n".join(render_inspect(flawed_bundle, None, logs_arg=flawed_logs))
    assert "대조가 꺼져 있습니다" in text
    command = next(l for l in text.splitlines() if l.strip().startswith("doseaudit --logs"))
    assert "--tracker" in command


def test_트래커를_주면_실행_줄이_그_경로를_쓴다(flawed_bundle, flawed_logs, flawed_tracker):
    from doseaudit.tracker import load_tracker
    text = "\n".join(render_inspect(flawed_bundle, load_tracker(flawed_tracker),
                                    logs_arg=flawed_logs, tracker_arg=flawed_tracker))
    assert "대조가 꺼져 있습니다" not in text
    command = next(l for l in text.splitlines() if l.strip().startswith("doseaudit --logs"))
    assert "--tracker" in command


def test_공백이_든_경로는_따옴표로_감싼다(flawed_bundle):
    text = "\n".join(render_inspect(flawed_bundle, None, logs_arg="M&Q 결과/로그/"))
    assert "'M&Q 결과/로그/'" in text


def test_못_읽은_파일을_그대로_보여_준다(tmp_path, make_workbook, basic_rows):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "ZZ99ZZ99_x_2.xlsx").write_bytes(b"broken")
    bundle = load_bundle(str(tmp_path / "logs"))
    text = "\n".join(render_inspect(bundle))
    assert "못 읽은 파일 1개" in text
    assert "ZZ99ZZ99_…_2.xlsx" in text


def test_건너뛴_파일_이름도_마스킹된다(tmp_path, make_workbook, basic_rows):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "김철수_secretlogin_3.csv").write_text("x", encoding="utf-8")
    text = "\n".join(render_inspect(load_bundle(str(tmp_path / "logs"))))
    assert "김철수" not in text and "secretlogin" not in text
    assert "건너뛴 것" in text


def test_마지막_문장이_고르는_것이_산출물이라고_말한다(text):
    assert text.rstrip().endswith("고르는 것이 산출물입니다.**")
