"""선언받는 정의들 — 활동일 규칙, 분모의 창, 상한 절단.

이 툴의 존재 이유가 여기 있다. "며칠 훈련했는가"는 **데이터가 답하는 질문이
아니라 정의가 답하는 질문**이다. 그래서 전부 인자로 선언받고, 선언된 문장을
리포트와 Methods 초안에 그대로 박는다. 추론하는 기본값은 하나도 없다.
"""

import datetime
import math
import re

from doseaudit.errors import RefuseError

_RULE_RE = re.compile(r"^(min-rows|min-modules|min-seconds)=(\d+(?:\.\d+)?)$")
_WEEKS_RE = re.compile(r"^fixed-weeks=(\d+(?:\.\d+)?)$")

# `datetime.date` 의 상한(9999-12-31)을 넘는 창은 만들 수 없다. 넉넉히 100년.
MAX_WEEKS = 5200.0

#: 정의 민감도 표에서 선언된 규칙과 나란히 세우는 **미리 정해진** 대조 규칙들.
#: 어느 것도 권장하지 않는다 — 정의를 안 적으면 얼마나 움직이는지만 보여 준다.
CONTRAST_RULES = ("any-row", "min-rows=5", "min-modules=2", "min-seconds=60")

#: 분모의 창을 바꿔 가며 세우는 대조 창들.
#: **이쪽이 더 큰 지렛대다** — 실측에서 활동일 규칙은 중앙값을 51%→16% 로 움직였지만,
#: 창은 51%→67%→90%→100% 로 움직였다. 이 툴의 한 줄 요약이 "분모가 무엇이었는지를
#: 드러낸다"인데 분모를 흔들어 보이지 않으면 그 문장이 빈다.
CONTRAST_WINDOWS = ("enroll-to-last", "fixed-weeks=8", "fixed-weeks=12")


class ActiveDayRule(object):
    """'이 날은 훈련한 날인가'의 정의."""

    __slots__ = ("kind", "n", "spec")

    def __init__(self, kind, n, spec):
        self.kind = kind
        self.n = n
        self.spec = spec

    @classmethod
    def parse(cls, text):
        if text is None:
            raise RefuseError(
                "`--active-day` 가 선언되지 않았습니다.\n"
                "       '며칠 훈련했는가'는 데이터가 아니라 **정의**가 답하는 질문입니다.\n"
                "       고를 수 있는 규칙: any-row · min-rows=N · min-modules=K · min-seconds=S\n"
                "       무엇을 고를지 모르겠으면 먼저 `--inspect` 로 각 규칙의 결과를 보세요."
            )
        text = text.strip()
        if text == "any-row":
            return cls("any-row", 0.0, text)
        m = _RULE_RE.match(text)
        if not m:
            raise RefuseError(
                "`--active-day %s` 를 해석하지 못했습니다.\n"
                "       any-row · min-rows=N · min-modules=K · min-seconds=S 중 하나여야 합니다."
                % text[:60]
            )
        value = float(m.group(2))
        if not math.isfinite(value):
            # `min-rows=999…9`(400자리)는 float 로 inf 가 된다. 받아 주면 **모든 날이
            # 비활동**이 되어, 트래커 대조가 전원 불일치라는 [치명]을 만들어 낸다 —
            # 이 툴이 스스로는 절대 내리지 않겠다고 한 판정을 표현 불가능한 임계값이
            # 대신 내리는 셈이다. 그래서 판정하지 않고 거절한다.
            raise RefuseError(
                "`--active-day %s` 의 값이 너무 커서 숫자로 표현되지 않습니다(inf).\n"
                "       표현할 수 없는 임계값으로는 판정하지 않습니다." % text[:60])
        if value <= 0:
            raise RefuseError("`--active-day %s` 의 값은 0보다 커야 합니다" % text[:60])
        return cls(m.group(1), value, text)

    def label(self):
        return {
            "any-row": "행이 하나라도 있는 날",
            "min-rows": "행이 %g개 이상인 날" % self.n,
            "min-modules": "서로 다른 모듈이 %g개 이상인 날" % self.n,
            "min-seconds": "소요시간 합이 %g초 이상인 날" % self.n,
        }[self.kind]

    def label_en(self):
        return {
            "any-row": "a day with at least one logged row",
            "min-rows": "a day with at least %g logged rows" % self.n,
            "min-modules": "a day with rows in at least %g distinct modules" % self.n,
            "min-seconds": "a day whose logged duration sums to at least %g seconds" % self.n,
        }[self.kind]

    def is_active(self, bucket):
        """`bucket` = 그 날짜의 `{모듈이름: [LogRow, ...]}`."""
        if self.kind == "any-row":
            return any(bucket.values())
        if self.kind == "min-rows":
            return sum(len(v) for v in bucket.values()) >= self.n
        if self.kind == "min-modules":
            return sum(1 for v in bucket.values() if v) >= self.n
        # min-seconds — 해석 못 한 소요시간은 0 으로 세지 않고 **빼고** 더한다.
        total = 0.0
        for rows in bucket.values():
            for row in rows:
                if row.seconds is not None:
                    total += row.seconds
        return total >= self.n


class Window(object):
    """분모의 창 — 노출 기간을 어디서 어디까지로 볼 것인가."""

    __slots__ = ("kind", "cut", "weeks", "spec")

    def __init__(self, kind, cut, weeks, spec):
        self.kind = kind
        self.cut = cut
        self.weeks = weeks
        self.spec = spec

    @classmethod
    def parse(cls, text, cut_text):
        if text is None:
            raise RefuseError(
                "`--window` 가 선언되지 않았습니다.\n"
                "       분모를 어디서 어디까지로 잡느냐에 따라 순응률 중앙값이 통째로 바뀝니다.\n"
                "       고를 수 있는 창: enroll-to-cut --cut YYYY-MM-DD · enroll-to-last · fixed-weeks=N"
            )
        text = text.strip()
        if text == "enroll-to-cut":
            if not cut_text:
                raise RefuseError("`--window enroll-to-cut` 에는 `--cut YYYY-MM-DD` 가 필요합니다")
            try:
                cut = datetime.date.fromisoformat(cut_text.strip())
            except ValueError:
                raise RefuseError("`--cut %s` 는 YYYY-MM-DD 형식이 아닙니다" % cut_text[:40])
            return cls("enroll-to-cut", cut, None, "enroll-to-cut(%s)" % cut.isoformat())
        if text == "enroll-to-last":
            return cls("enroll-to-last", None, None, "enroll-to-last")
        m = _WEEKS_RE.match(text)
        if m:
            weeks = float(m.group(1))
            if not math.isfinite(weeks):
                raise RefuseError(
                    "`--window fixed-weeks=%s` 의 값이 숫자로 표현되지 않습니다(inf)" % m.group(1)[:40])
            if weeks <= 0:
                raise RefuseError("`--window fixed-weeks=N` 의 N 은 0보다 커야 합니다")
            if weeks > MAX_WEEKS:
                # `date` 가 표현할 수 있는 범위를 넘으면 `timedelta` 덧셈이
                # OverflowError 로 죽는다. 트레이스백 대신 거절한다.
                raise RefuseError(
                    "`--window fixed-weeks=%s` 는 너무 깁니다(최대 %g주).\n"
                    "       임상시험의 분모로 쓸 수 있는 길이가 아닙니다."
                    % (m.group(1)[:40], MAX_WEEKS))
            if not float(weeks * 7).is_integer():
                # 날짜가 최소 단위라 반나절짜리 창은 만들 수 없다. 조용히 잘라서
                # 선언한 것보다 짧은 분모를 쓰느니, 받지 않는다.
                raise RefuseError(
                    "`--window fixed-weeks=%s` 는 하루 단위로 떨어지지 않습니다(%g일).\n"
                    "       이 툴의 최소 단위는 날짜입니다 — 정수 일수가 되는 주수를 주세요."
                    % (m.group(1), weeks * 7))
            return cls("fixed-weeks", None, weeks, text)
        raise RefuseError(
            "`--window %s` 를 해석하지 못했습니다.\n"
            "       enroll-to-cut · enroll-to-last · fixed-weeks=N 중 하나여야 합니다." % text[:60]
        )

    def bounds(self, enroll, last_activity):
        """창의 `(시작일, 종료일)`. 정할 수 없으면 `(None, None)`.

        **분자와 분모는 같은 구간을 봐야 한다.** 활동일(분자)은 워크북 전체에서 세고
        관찰일수(분모)만 이 창으로 잡으면, 가입 전·데이터컷 이후의 활동이 분자에만
        들어가 노출률이 100%를 훌쩍 넘는 값으로 부풀어 오른다. 그래서 창을 여기서
        한 번만 정의하고 양쪽이 같이 쓴다.
        """
        if enroll is None:
            # 고정 주수라도 **시작일을 모르면 창이 없다.** 주수만으로 분모를 만들면
            # 시작점 없는 비율이 되고, 그건 셈이 아니라 추론이다.
            return (None, None)
        if self.kind == "fixed-weeks":
            try:
                end = enroll + datetime.timedelta(days=self.weeks * 7.0 - 1)
            except (OverflowError, OSError, ValueError):
                # 손으로 관리한 트래커는 '진행 중'을 9999-12-31 로 적는 일이 있다.
                # 거기에 주수를 더하면 date 의 표현 범위를 넘는다. 트레이스백 대신
                # **창이 없다**로 떨어뜨리면, 이 피험자는 이미 있는 '창을 정하지
                # 못한 피험자' 경고 경로로 정직하게 보고된다.
                return (None, None)
        elif self.kind == "enroll-to-cut":
            end = self.cut
        else:
            end = last_activity
        if end is None or end < enroll:
            return (None, None)
        return (enroll, end)

    def observed_days(self, enroll, last_activity):
        """관찰일수(일). 셀 수 없으면 None.

        **양 끝을 모두 포함해서 센다** — 트래커의 `현재까지 일수` 와 같은 관습이다
        (가입일 2026-01-29, 기준일 2026-06-21 → 144일). 관습을 맞춰 두어야
        이 툴의 숫자와 손으로 만든 표의 숫자를 바로 비교할 수 있다.
        """
        start, end = self.bounds(enroll, last_activity)
        if start is None:
            return None
        return float((end - start).days + 1)

    def label(self, enroll_source):
        src = {"tracker": "가입일(트래커)", "first-activity": "첫 활동일(로그)"}[enroll_source]
        if self.kind == "enroll-to-cut":
            return "%s → %s (데이터컷)" % (src, self.cut.isoformat())
        if self.kind == "enroll-to-last":
            return "%s → 마지막 활동일" % src
        return "%s 부터 고정 %g주" % (src, self.weeks)

    def label_en(self, enroll_source):
        src = {"tracker": "the enrolment date recorded in the participation tracker",
               "first-activity": "each participant's first logged activity date"}[enroll_source]
        if self.kind == "enroll-to-cut":
            return "from %s to the data cut of %s" % (src, self.cut.isoformat())
        if self.kind == "enroll-to-last":
            return "from %s to that participant's last logged activity" % src
        return "a fixed %g-week window starting at %s" % (self.weeks, src)
