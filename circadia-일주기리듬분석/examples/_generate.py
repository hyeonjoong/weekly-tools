"""예시 데이터 생성기 — 100% 합성, 결정론(고정 시드).

시나리오 2벌 × 벤더 열이름 3벌(애플건강/삼성헬스/핏빗식) = 6세트.
같은 시나리오는 세 벤더 파일의 '내용'(시각·값)이 완전히 동일하고
열 이름·타임스탬프 표기만 다르다 — 파서 등가성 테스트의 근거.

- 규칙적_1주: 2026-08-03(월)~08-09(일). 취침 23:30±10분·기상 07:30±10분,
  주말(금·토 밤) +20분. 심박 = 66 + 9·cos(2π(t−16)/24) + N(0,1.5) (5분 간격),
  걸음 = 출퇴근·점심·저녁 피크 패턴(10분 간격), 수면 중 0.
- 불규칙_1주: 2026-08-10(월)~08-16(일). 취침 23:40~03:20 들쭉날쭉, 짧은 밤,
  수요일 낮잠, 주말 +2.5h 이상 지연(사회적 시차 큼), 화 10–15시·목 13–16시
  착용 갭. 심박 = 70 + 5·cos(2π(t−18)/24) + N(0,3).

실제 인물 데이터가 아니며 어떤 실측 기록도 섞이지 않았습니다.
재생성: python3 examples/_generate.py  (이 폴더 기준 상위에서 실행해도 됨)
"""

import datetime as dt
import os
import random
from typing import List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
RNG = random.Random(20260827)


# ---------------------------------------------------------------------------
# 시나리오 — 수면 스케줄
# ---------------------------------------------------------------------------

def _t(day: dt.date, h: int, m: int, plus_days: int = 0) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=plus_days), dt.time(h, m))


def regular_sleep() -> List[Tuple[dt.datetime, dt.datetime]]:
    d0 = dt.date(2026, 8, 3)  # 월
    out = []
    for i in range(7):
        d = d0 + dt.timedelta(days=i)
        late = 20 if d.weekday() in (4, 5) else 0   # 금·토 밤 +20분
        bed_j, wake_j = RNG.randint(-10, 10), RNG.randint(-10, 10)
        bed = _t(d, 23, 30) + dt.timedelta(minutes=late + bed_j)
        wake = _t(d, 7, 30, plus_days=1) + dt.timedelta(minutes=late + wake_j)
        out.append((bed, wake))
    return out


def irregular_sleep() -> List[Tuple[dt.datetime, dt.datetime]]:
    d0 = dt.date(2026, 8, 10)  # 월
    sched = [   # (취침 h,m,다음날?, 기상 h,m) — 밤 배정일은 d0+i
        ((1, 10, 1), (8, 40, 1)),    # 월밤
        ((3, 0, 1), (6, 30, 1)),     # 화밤 — 짧은 밤
        ((1, 0, 1), (9, 10, 1)),     # 수밤
        ((23, 40, 0), (6, 50, 1)),   # 목밤 — 이른 밤
        ((2, 50, 1), (11, 20, 1)),   # 금밤(주말) — 지연
        ((3, 20, 1), (11, 40, 1)),   # 토밤(주말) — 지연
        ((0, 30, 1), (7, 40, 1)),    # 일밤
    ]
    out = []
    for i, ((bh, bm, bd), (wh, wm, wd)) in enumerate(sched):
        d = d0 + dt.timedelta(days=i)
        out.append((_t(d, bh, bm, bd), _t(d, wh, wm, wd)))
    # 수요일(8/12) 낮잠
    nap_d = dt.date(2026, 8, 12)
    out.append((_t(nap_d, 14, 0), _t(nap_d, 15, 10)))
    out.sort()
    return out


# ---------------------------------------------------------------------------
# 심박·걸음 시계열
# ---------------------------------------------------------------------------

def _in_any(t, intervals):
    return any(s <= t < e for s, e in intervals)


def gen_hr(d0: dt.date, days: int, mesor: float, amp: float, peak_h: float,
           noise: float, gaps: List[Tuple[dt.datetime, dt.datetime]]):
    import math
    out = []
    t = dt.datetime.combine(d0, dt.time())
    end = dt.datetime.combine(d0 + dt.timedelta(days=days), dt.time())
    while t < end:
        if not _in_any(t, gaps):
            h = t.hour + t.minute / 60.0
            v = mesor + amp * math.cos(2 * math.pi * (h - peak_h) / 24.0) \
                + RNG.gauss(0, noise)
            out.append((t, int(round(max(40, min(120, v))))))
        t += dt.timedelta(minutes=5)
    return out


def gen_steps(d0: dt.date, days: int, sleep, gaps,
              day_factors: List[float]):
    out = []
    t = dt.datetime.combine(d0, dt.time())
    end = dt.datetime.combine(d0 + dt.timedelta(days=days), dt.time())
    while t < end:
        if not _in_any(t, gaps):
            if _in_any(t, sleep):
                v = 0
            else:
                h = t.hour + t.minute / 60.0
                f = day_factors[(t.date() - d0).days % len(day_factors)]
                if 8.0 <= h < 9.0:
                    v = RNG.randint(500, 900)
                elif 12.0 <= h < 13.0:
                    v = RNG.randint(250, 500)
                elif 18.5 <= h < 19.5:
                    v = RNG.randint(400, 800)
                elif 7.0 <= h < 23.0:
                    v = RNG.randint(20, 180)
                else:
                    v = RNG.randint(0, 25)
                v = int(round(v * f))
            out.append((t, v))
        t += dt.timedelta(minutes=10)
    return out


# ---------------------------------------------------------------------------
# 벤더 파일 렌더러 — 내용 동일, 표기만 다름
# ---------------------------------------------------------------------------

def _apple_ts(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") + " +0900"


def _samsung_ts(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") + ".000"


def _fitbit_ts(t: dt.datetime) -> str:
    return t.strftime("%m/%d/%Y %I:%M:%S %p").lstrip("0")


APPLE_STAGES = ("HKCategoryValueSleepAnalysisAsleepCore",
                "HKCategoryValueSleepAnalysisAsleepDeep",
                "HKCategoryValueSleepAnalysisAsleepREM")


def write_set(root: str, name: str, hr, steps, sleep) -> None:
    for vendor in ("애플건강", "삼성헬스", "핏빗"):
        d = os.path.join(root, f"{name}_{vendor}")
        os.makedirs(d, exist_ok=True)
        hr_p = os.path.join(d, "심박.csv")
        st_p = os.path.join(d, "걸음.csv")
        sl_p = os.path.join(d, "수면.csv")
        if vendor == "애플건강":
            with open(hr_p, "w", encoding="utf-8") as fh:
                fh.write("startDate,endDate,value,unit\n")
                for t, v in hr:
                    fh.write(f"{_apple_ts(t)},{_apple_ts(t + dt.timedelta(minutes=5))},{v},count/min\n")
            with open(st_p, "w", encoding="utf-8") as fh:
                fh.write("startDate,endDate,value,unit\n")
                for t, v in steps:
                    fh.write(f"{_apple_ts(t)},{_apple_ts(t + dt.timedelta(minutes=10))},{v},count\n")
            with open(sl_p, "w", encoding="utf-8") as fh:
                fh.write("startDate,endDate,value\n")
                stage_rng = random.Random(7)   # 표기용 — 분석엔 영향 없음
                for s, e in sleep:
                    # InBed 행(파서가 제외) + Asleep 단계 분할(파서가 병합)
                    fh.write(f"{_apple_ts(s - dt.timedelta(minutes=8))},"
                             f"{_apple_ts(e + dt.timedelta(minutes=4))},"
                             "HKCategoryValueSleepAnalysisInBed\n")
                    cur = s
                    while cur < e:
                        seg = min(e, cur + dt.timedelta(minutes=stage_rng.choice((30, 45, 60))))
                        fh.write(f"{_apple_ts(cur)},{_apple_ts(seg)},"
                                 f"{stage_rng.choice(APPLE_STAGES)}\n")
                        cur = seg
        elif vendor == "삼성헬스":
            with open(hr_p, "w", encoding="utf-8") as fh:
                fh.write("start_time,end_time,heart_rate\n")
                for t, v in hr:
                    fh.write(f"{_samsung_ts(t)},{_samsung_ts(t + dt.timedelta(minutes=5))},{v}\n")
            with open(st_p, "w", encoding="utf-8") as fh:
                fh.write("start_time,end_time,step_count\n")
                for t, v in steps:
                    fh.write(f"{_samsung_ts(t)},{_samsung_ts(t + dt.timedelta(minutes=10))},{v}\n")
            with open(sl_p, "w", encoding="utf-8") as fh:
                fh.write("start_time,end_time\n")
                for s, e in sleep:
                    fh.write(f"{_samsung_ts(s)},{_samsung_ts(e)}\n")
        else:  # 핏빗식
            with open(hr_p, "w", encoding="utf-8") as fh:
                fh.write("Time,Heart Rate\n")
                for t, v in hr:
                    fh.write(f"{_fitbit_ts(t)},{v}\n")
            with open(st_p, "w", encoding="utf-8") as fh:
                fh.write("Time,Steps\n")
                for t, v in steps:
                    fh.write(f"{_fitbit_ts(t)},{v}\n")
            with open(sl_p, "w", encoding="utf-8") as fh:
                fh.write("Start Time,End Time\n")
                for s, e in sleep:
                    fh.write(f"{_fitbit_ts(s)},{_fitbit_ts(e)}\n")


def main() -> None:
    # 규칙적 1주
    reg_sleep = regular_sleep()
    reg_hr = gen_hr(dt.date(2026, 8, 3), 7, mesor=66, amp=9, peak_h=16,
                    noise=1.5, gaps=[])
    reg_steps = gen_steps(dt.date(2026, 8, 3), 7, reg_sleep, [],
                          day_factors=[1.0] * 7)
    write_set(HERE, "규칙적_1주", reg_hr, reg_steps, reg_sleep)

    # 불규칙 1주 — 착용 갭 포함
    irr_sleep = irregular_sleep()
    gaps = [(dt.datetime(2026, 8, 11, 10, 0), dt.datetime(2026, 8, 11, 15, 0)),
            (dt.datetime(2026, 8, 13, 13, 0), dt.datetime(2026, 8, 13, 16, 0))]
    irr_hr = gen_hr(dt.date(2026, 8, 10), 7, mesor=70, amp=5, peak_h=18,
                    noise=3.0, gaps=gaps)
    irr_steps = gen_steps(dt.date(2026, 8, 10), 7, irr_sleep, gaps,
                          day_factors=[0.5, 1.2, 0.7, 1.0, 1.3, 0.4, 0.9])
    write_set(HERE, "불규칙_1주", irr_hr, irr_steps, irr_sleep)
    print("생성 완료:", HERE)


if __name__ == "__main__":
    main()
