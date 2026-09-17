"""산출물 4종을 쓴다.

파일 이름에 **절대 경로를 넣지 않는다.** 산출물은 공유되는 물건이라, 홈 디렉터리
경로가 섞여 들어가면 사용자 계정 이름이 같이 나간다 — 파일명은 basename 만 쓴다.
"""

import os

from doseaudit.csvout import write_csv
from doseaudit.dose import UNCOMPARABLE
from doseaudit.paths import open_artifact, preflight
from doseaudit.report import render_markdown
from doseaudit.values import fmt_date

REPORT_MD = "노출량점검.md"
EXPOSURE_CSV = "피험자별노출량.csv"
MODULE_CSV = "모듈별요약.csv"
TRACKER_CSV = "트래커불일치.csv"


def _num(value, digits=2):
    return "" if value is None else ("%.*f" % (digits, value))


def exposure_rows(result):
    """`피험자별노출량.csv` — `joinaudit`/`statwise` 가 바로 먹을 수 있는 스키마.

    키 열 이름이 `subject_id` 인 것은 우연이 아니다. `joinaudit` 이 이 이름을
    피험자 키로 자동 인식한다.
    """
    modules = list(result.modules.keys())
    header = ["subject_id", "활동일수", "관찰일수", "주당활동일", "노출률", "상한절단",
              "최장공백일", "첫활동일", "마지막활동일", "창시작일", "창종료일",
              "창밖활동일수", "창적용",
              # 아래 네 개는 **창과 무관하게 워크북 전체**에서 센 값이다. 같은 행에
              # 창 기준 값과 나란히 있으므로, 이름에 범위를 박아 둔다.
              "총행수_전체기간", "소요시간0초행수_전체기간",
              "중복의심행수_전체기간", "날짜해석실패행수_전체기간"]
    if result.config.criterion is not None:
        header.append("선언기준충족")
    header += ["행수_%s_전체기간" % name for name in modules]

    rows = []
    for exposure in sorted(result.exposures, key=lambda e: e.code):
        row = [
            exposure.code,
            exposure.n_active,
            # 관찰일수는 반올림하지 않는다 — CSV 에서 노출률을 되짚어 계산할 때
            # `10` 과 `10.5` 는 다른 답을 준다.
            _num(exposure.observed_days, 1),
            _num(exposure.days_per_week, 3),
            _num(exposure.adherence_pct, 2),
            "Y" if exposure.capped else "N",
            "" if exposure.longest_gap is None else exposure.longest_gap,
            fmt_date(exposure.first),
            fmt_date(exposure.last),
            fmt_date(exposure.window_start),
            fmt_date(exposure.window_end),
            exposure.n_outside_window,
            "Y" if exposure.window_known else "N",
            exposure.n_rows,
            exposure.n_zero_sec,
            exposure.n_dup,
            exposure.n_unparsed_dates,
        ]
        if result.config.criterion is not None:
            if exposure.adherence_pct is None:
                row.append(UNCOMPARABLE)
            else:
                row.append("충족" if exposure.adherence_pct >= result.config.criterion else "미달")
        row += [exposure.module_rows.get(name, "") for name in modules]
        rows.append(row)
    return header, rows


def module_rows(result):
    """`모듈별요약.csv` — 평균은 항상 3칸(전체·0제외·0행비율)으로만 나간다."""
    # `소요시간_해석실패행수` 가 있어야 `0행비율` 을 CSV 안에서 재현할 수 있다.
    # 비율의 분모는 **해석된 행**이고 `행수` 는 **전체 행**이라, 이 칸이 없으면
    # `0초행수 ÷ 행수` 가 인쇄된 비율과 어긋나고 그 차이를 설명할 길이 없다.
    header = ["모듈", "훈련유형", "파일수", "데이터있는파일수", "행수", "소요시간0초행수",
              "소요시간_해석실패행수",
              "소요시간_전체평균", "소요시간_0제외평균", "소요시간_0행비율",
              "반복횟수_전체평균", "반복횟수_0제외평균", "반복횟수_0행비율", "상태"]
    rows = []
    for module in result.modules.values():
        sec = module.seconds.csv_fields()
        rep = module.reps.csv_fields()
        rows.append([module.name, "(전체)", module.n_files, module.n_files_with_data,
                     module.n_rows, module.n_zero_sec, module.seconds.n_unparsed,
                     sec[0], sec[1], sec[2], rep[0], rep[1], rep[2], module.status])
        for ttype, stats in module.by_type.items():
            tsec = stats["seconds"].csv_fields()
            trep = stats["reps"].csv_fields()
            rows.append([module.name, ttype, "", "", stats["n_rows"],
                         stats["n_zero_sec"], stats["seconds"].n_unparsed,
                         tsec[0], tsec[1], tsec[2], trep[0], trep[1], trep[2], ""])
    return header, rows


def tracker_rows(result):
    """`트래커불일치.csv` — **불일치만**. 일치한 사람은 여기 나오지 않는다."""
    header = ["subject_id", "트래커_완료일수", "재계산_활동일수", "차이", "사유"]
    rows = []
    comparison = result.comparison
    if not comparison:
        return header, rows
    for diff in comparison["diffs"]:
        rows.append([diff.code, diff.fmt_tracker(), diff.recomputed, diff.fmt_delta(),
                     "트래커 `완료일수` 와 선언된 규칙(%s)으로 다시 센 활동일수가 다름" % result.config.rule.spec])
    for code, reason in comparison.get("uncomparable", ()):
        rows.append([code, "", "", "", reason])
    for code in comparison.get("unreadable_in_tracker", ()):
        rows.append([code, "", "", "", "로그 워크북을 읽지 못함 — 없는 것이 아니라 못 읽은 것"])
    for code in comparison["only_tracker"]:
        rows.append([code, "", "", "", "트래커에만 있음 — 대응하는 로그 워크북이 없음"])
    for code in comparison["only_logs"]:
        rows.append([code, "", "", "", "로그에만 있음 — 트래커에 행이 없음"])
    return header, rows


def write_all(result, out_dir, console_lines):
    """산출물 4종을 쓰고 만들어진 파일 이름(basename)을 돌려준다.

    **하나라도 쓸 수 없으면 아무것도 쓰지 않는다** — 반쯤 채워진 결과 폴더는
    완전한 결과로 오해되기 때문이다.
    """
    planned = [EXPOSURE_CSV, MODULE_CSV, REPORT_MD]
    if result.comparison is not None:
        planned.insert(2, TRACKER_CSV)
    # 이 실행이 **읽은** 파일들 — 산출물이 이 중 하나를 덮어쓰려 하면 거절한다.
    inputs = [result.config.tracker_path]
    inputs.extend(getattr(result.bundle, "paths", ()) or ())
    preflight(out_dir, planned, inputs)

    written = []
    header, rows = exposure_rows(result)
    written.append(write_csv(out_dir, EXPOSURE_CSV, header, rows))
    header, rows = module_rows(result)
    written.append(write_csv(out_dir, MODULE_CSV, header, rows))
    if result.comparison is not None:
        header, rows = tracker_rows(result)
        written.append(write_csv(out_dir, TRACKER_CSV, header, rows))
    # 리포트 본문을 **열기 전에** 만든다 — 자백 블록이 빠졌다면
    # (`ReportIntegrityError`) 빈 파일을 남기지 않고 죽어야 한다.
    markdown = render_markdown(result, console_lines) + "\n"
    with open_artifact(out_dir, REPORT_MD) as fh:
        fh.write(markdown)
    written.append(REPORT_MD)
    return [os.path.basename(name) for name in written]
