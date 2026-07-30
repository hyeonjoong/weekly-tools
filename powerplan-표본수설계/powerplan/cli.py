"""powerplan CLI — 설계별 하위 명령으로 표본수/검정력을 계산한다.

    powerplan ttest2 --d 0.5 --power 0.8            # 표본수 구하기
    powerplan ttest2 --d 0.5 --n 40                 # 확보 가능한 n의 검정력
    powerplan pilot data.csv --value isi --group arm --power 0.8

각 하위 명령은 공통 옵션(--alpha/--sides/--power/--n/--n-total/--dropout/--cluster-*/
--comparisons/--interim/--spending/--timing/--sensitivity/--format)을 공유한다.
단 정밀도 기준 설계(icc/loa/kappa)는 --alpha와 출력 옵션만 받고, --interim은
anova/equiv에 적용되지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import errno
import math
import os
import stat
import sys

from . import __version__
from .designs import (
    CorrelationTest,
    CrossoverT,
    EquivalenceProportions,
    EquivalenceT,
    LogRankSurvival,
    McNemarPaired,
    NonInferiorityProportions,
    NonInferiorityT,
    OneSampleProportion,
    OneSampleT,
    OneWayAnova,
    PairedT,
    RepeatedMeasuresT,
    TwoProportions,
    TwoSampleT,
)
from .effects import cohen_d, cohen_f_from_means
from .pilot import (effect_from_paired, effect_from_two_group, read_paired,
                    read_two_group, strip_unsafe)
from .precision import diagnostic_plan, icc_plan, kappa_plan, loa_plan
from .report import render_json, render_markdown, render_text
from .sequential import SPENDING_KINDS
from .solve import Adjustments, make_plan, smallest_unit
from .validate import PowerPlanError, alpha_value, as_float, as_int, probability

_EPILOG = """
예시:
  powerplan ttest2 --d 0.5 --power 0.8 --dropout 0.15
  powerplan ttest2 --mean1 8 --mean2 5 --sd 6 --power 0.9 --sensitivity
  powerplan paired --dz 0.45 --power 0.8            # 전후 비교(대응표본)
  powerplan repeated --d 0.4 --post 3 --rho 0.6 --power 0.8   # 반복측정(MMRM)
  powerplan prop2 --p1 0.30 --p2 0.50 --power 0.8
  powerplan anova --k 3 --f 0.25 --power 0.8
  powerplan corr --r 0.35 --power 0.8
  powerplan survival --hr 0.7 --median1 12 --accrual 18 --followup 12 --power 0.8
  powerplan mcnemar --p01 0.05 --p10 0.15 --power 0.8   # 같은 사람의 두 판정
  powerplan prop1 --p1 0.60 --p0 0.45 --power 0.8   # 단일군 vs 성능목표치(기기 확증시험)
  powerplan crossover --diff 3 --sd-within 6 --power 0.8   # 2x2 교차설계
  powerplan diag --sens 0.9 --spec 0.85 --prevalence 0.2 --half-width 0.05
  powerplan noninf --margin 3 --sd 8 --power 0.8    # 비열등성 (연속형)
  powerplan noninf --margin 0.1 --p1 0.7 --p2 0.7 --power 0.8   # 비열등성 (비율)
  powerplan equiv --margin 5 --sd 8 --power 0.8     # 동등성(TOST)
  powerplan icc --icc 0.8 --width 0.15 --raters 2   # 신뢰도 연구(정밀도)
  powerplan loa --sd-diff 2.0 --half-width 0.5      # Bland-Altman LoA
  powerplan kappa --kappa 0.7 --width 0.2           # 범주형 판정 일치도
  powerplan pilot pilot.csv --value isi --group arm --power 0.8
  powerplan ttest2 --d 0.5 --n 30                   # 검정력만 확인
  powerplan ttest2 --d 0.5 --power 0.8 --interim 2  # 중간분석 2회(군차별설계)

설계 고르기:
  두 군 평균 비교 → ttest2 | 전후(같은 사람) → paired | 기준값 대비 → onesample
  같은 지표를 여러 번 측정 → repeated | 반응률(예/아니오) → prop2 | 3군 이상 → anova
  두 변수 관계 → corr | 사건까지의 시간(재발·사망) → survival
  같은 사람의 두 이분형 판정 → mcnemar
  "더 나쁘지 않다" → noninf | "같다" → equiv  (둘 다 연속형·이분형 모두 지원)
  단일군 vs 성능목표치 → prop1 | 같은 사람이 두 처치를 다 받음 → crossover
  측정 일치도/신뢰도 연구 → icc, loa (연속형) · kappa (범주형) — 정밀도 기준
  민감도·특이도 추정 → diag (유병률까지 반영해 전체 등록 인원을 계산)
"""


def _add_common(parser: argparse.ArgumentParser, sides: bool = True,
                ratio: bool = False, cluster: bool = True,
                interim: bool = True) -> None:
    """공통 옵션. 설계에 **의미가 없는** 옵션은 아예 등록하지 않는다.

    받아 놓고 나중에 거절하면 `--help`와 실제 동작이 어긋나 문서까지 틀어진다.
    """
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="유의수준 (기본 0.05)")
    if sides:
        parser.add_argument("--sides", type=int, default=2, choices=(1, 2),
                            help="양측(2, 기본) / 단측(1)")
    if ratio:
        parser.add_argument("--ratio", type=float, default=1.0,
                            help="2군/1군 배분비 (기본 1.0 = 1:1)")
    parser.add_argument("--power", type=float, default=None,
                        help="목표 검정력 (예: 0.8). 주면 표본수를 계산")
    parser.add_argument("--n", type=int, default=None,
                        help="확보 가능한 표본수(1군 기준). 주면 검정력을 계산")
    parser.add_argument("--n-total", type=int, default=None,
                        help="확보 가능한 **총** 표본수 (--n 대신, 배분비에 맞춰 나눔)")
    parser.add_argument("--dropout", type=float, default=0.0,
                        help="예상 탈락률 (예: 0.15 = 15%%)")
    if interim:
        parser.add_argument("--interim", type=int, default=None,
                            help="중간분석 횟수 (최종분석 제외). 군차별설계로 표본수를 보정")
        parser.add_argument("--spending", default="obf", choices=SPENDING_KINDS,
                            help="α 소비함수 (기본 obf = O'Brien-Fleming형)")
        parser.add_argument("--timing", default=None,
                            help="중간분석 정보비율 (예: 0.5 또는 0.4,0.7). 기본은 균등 배치")
    if cluster:
        parser.add_argument("--cluster-size", type=int, default=None,
                            help="군집 무작위배정: 군집당 인원 m")
        parser.add_argument("--cluster-icc", type=float, default=None,
                            help="군집 무작위배정: 군집내 상관 ICC")
    parser.add_argument("--comparisons", type=int, default=1,
                        help="다중비교 개수 (α 보정)")
    parser.add_argument("--alpha-method", default="bonferroni",
                        choices=("bonferroni", "sidak", "none"),
                        help="α 보정 방법 (기본 bonferroni)")
    parser.add_argument("--sensitivity", action="store_true",
                        help="가정이 틀렸을 때의 표본수/검정력 표를 함께 출력")
    parser.add_argument("--format", default="text", choices=("text", "md", "json"),
                        help="출력 형식 (기본 text)")
    parser.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    parser.add_argument("--force", action="store_true",
                       help="-o 로 지정한 파일이 이미 있어도 덮어쓰기")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powerplan",
        description="임상연구 표본수·검정력 계산기 (외부 의존성 0) · "
                    "평균·ANOVA·반복측정·교차설계는 비중심 t/F 정확계산, "
                    "단일군 비율(prop1)은 정확 이항검정 · "
                    "두 군 비율·상관·생존(Schoenfeld)·McNemar·ICC·LoA·kappa·"
                    "진단정확도는 근사 · 중간분석(군차별설계)은 Z 정규근사",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"powerplan {__version__}")
    sub = parser.add_subparsers(dest="design", metavar="설계")

    p = sub.add_parser("ttest2", help="두 독립군 평균 비교 (독립표본 t)")
    p.add_argument("--d", type=float, default=None, help="Cohen's d")
    p.add_argument("--mean1", type=float, default=None, help="1군 평균 (--d 대신)")
    p.add_argument("--mean2", type=float, default=None, help="2군 평균")
    p.add_argument("--sd", type=float, default=None, help="공통 표준편차")
    p.add_argument("--analysis", default="raw", choices=("raw", "ancova", "change"),
                   help="분석 방법: raw(추적값만) · ancova(기저값 보정) · change(변화량)")
    p.add_argument("--baseline-r", type=float, default=0.0,
                   help="기저값-추적값 상관 r (ancova/change에 필요)")
    _add_common(p, ratio=True)

    p = sub.add_parser("paired", help="대응표본(전후) 평균 비교")
    p.add_argument("--dz", type=float, default=None, help="Cohen's dz (변화량 평균/변화량 SD)")
    p.add_argument("--diff", type=float, default=None, help="예상 변화량 (--dz 대신)")
    p.add_argument("--sd-diff", type=float, default=None, help="변화량의 표준편차")
    _add_common(p, cluster=False)

    p = sub.add_parser("onesample", help="단일표본 평균 비교 (기준값 대비)")
    p.add_argument("--d", type=float, default=None, help="Cohen's d")
    p.add_argument("--mean", type=float, default=None, help="예상 평균")
    p.add_argument("--ref", type=float, default=None, help="기준값")
    p.add_argument("--sd", type=float, default=None, help="표준편차")
    _add_common(p, cluster=False)

    p = sub.add_parser("prop2", help="두 군 비율(반응률) 비교")
    p.add_argument("--p1", type=float, required=True, help="1군(대조) 비율")
    p.add_argument("--p2", type=float, required=True, help="2군(중재) 비율")
    p.add_argument("--continuity", action="store_true", help="연속성 보정 적용(보수적)")
    _add_common(p, ratio=True)

    p = sub.add_parser("anova", help="여러 군(k개) 평균 비교 (일원배치 ANOVA)")
    p.add_argument("--k", type=int, required=True, help="군 수 (≥2)")
    p.add_argument("--f", type=float, default=None, help="Cohen's f")
    p.add_argument("--means", default=None, help="군 평균들 (예: 8,6,5) — --f 대신")
    p.add_argument("--sd", type=float, default=None, help="군내 표준편차 (--means와 함께)")
    _add_common(p, sides=False, interim=False)

    p = sub.add_parser("corr", help="상관계수 검정 (r ≠ 0)")
    p.add_argument("--r", type=float, required=True, help="예상 상관계수")
    p.add_argument("--bias-correct", action="store_true",
                   help="Fisher z 편향보정 (G*Power 정확법과 근사 일치)")
    _add_common(p)

    p = sub.add_parser("noninf", help="비열등성 검정 (두 군 평균 또는 비율, 단측)")
    p.add_argument("--margin", type=float, required=True,
                   help="비열등성 마진 (평균이면 원래 단위, 비율이면 위험차)")
    p.add_argument("--sd", type=float, default=None, help="표준편차 (연속형 결과)")
    p.add_argument("--diff", type=float, default=0.0,
                   help="가정하는 실제 차이 (중재−대조, 기본 0 · 연속형)")
    p.add_argument("--p1", type=float, default=None, help="대조군 비율 (이분형 결과)")
    p.add_argument("--p2", type=float, default=None, help="중재군 비율 (이분형 결과)")
    p.add_argument("--lower-is-better", action="store_true",
                   help="결과지표가 낮을수록 좋은 경우 (ISI, 통증점수, 사망률 등)")
    _add_common(p, sides=False, ratio=True)
    # 비열등성은 관례상 단측 0.025 — _add_common의 '기본 0.05' 도움말을 덮어쓴다
    p.set_defaults(alpha=0.025)
    for action in p._actions:
        if action.dest == "alpha":
            action.help = "단측 유의수준 (비열등성 관례에 따라 **기본 0.025**)"

    p = sub.add_parser("equiv", help="동등성 검정 (TOST, 두 군 평균 또는 비율)")
    p.add_argument("--margin", type=float, required=True,
                   help="동등성 마진 ± (평균이면 원래 단위, 비율이면 위험차)")
    p.add_argument("--sd", type=float, default=None, help="표준편차 (연속형 결과)")
    p.add_argument("--diff", type=float, default=0.0,
                   help="가정하는 실제 차이 (기본 0 · 연속형)")
    p.add_argument("--p1", type=float, default=None, help="대조군 비율 (이분형 결과)")
    p.add_argument("--p2", type=float, default=None, help="중재군 비율 (이분형 결과)")
    _add_common(p, sides=False, ratio=True, interim=False)
    for action in p._actions:
        if action.dest == "alpha":
            action.help = "각 단측검정의 유의수준 (기본 0.05 — 전체 1종오류도 0.05)"

    p = sub.add_parser("survival", help="생존분석 두 군 비교 (로그순위 · Cox 비례위험)")
    p.add_argument("--hr", type=float, required=True, help="위험비 HR (중재/대조)")
    p.add_argument("--median1", type=float, default=None,
                   help="대조군 생존기간 중앙값 (지수 생존모형)")
    p.add_argument("--accrual", type=float, default=0.0,
                   help="등록기간 (중앙값과 같은 시간 단위, 기본 0 = 동시 등록)")
    p.add_argument("--followup", type=float, default=0.0,
                   help="마지막 등록자의 추가 추적기간")
    p.add_argument("--event-rate", type=float, default=None,
                   help="**대조군**에서 연구 종료까지 사건을 겪을 비율 (--median1 대신)")
    p.add_argument("--method", default="schoenfeld", choices=("schoenfeld", "freedman"),
                   help="표본수 공식: schoenfeld(기본) · freedman(더 보수적)")
    p.add_argument("--time-unit", default="개월", help="시간 단위 표기 (기본 '개월')")
    _add_common(p, ratio=True)

    p = sub.add_parser("repeated", help="반복측정 두 군 비교 (MMRM/반복측정 ANCOVA)")
    p.add_argument("--d", type=float, default=None, help="Cohen's d (1회 측정의 SD 기준)")
    p.add_argument("--mean1", type=float, default=None, help="1군 평균 (--d 대신)")
    p.add_argument("--mean2", type=float, default=None, help="2군 평균")
    p.add_argument("--sd", type=float, default=None, help="1회 측정의 표준편차")
    p.add_argument("--post", type=int, default=1, help="사후 측정 횟수 p (기본 1)")
    p.add_argument("--baseline-n", type=int, default=1, help="사전 측정 횟수 b (기본 1)")
    p.add_argument("--rho", type=float, required=True, help="측정 간 상관 ρ (복합대칭 가정)")
    p.add_argument("--analysis", default="ancova", choices=("post", "change", "ancova"),
                   help="분석 방법: ancova(기본) · change(변화량) · post(보정 없음)")
    p.add_argument("--estimand", default="last", choices=("last", "average"),
                   help="1차 평가변수: last(마지막 방문, 기본) · average(사후 방문 평균)")
    _add_common(p, ratio=True)

    p = sub.add_parser("prop1", help="단일군 반응률 vs 성능목표치 (정확 이항검정)")
    p.add_argument("--p1", type=float, required=True, help="예상 반응률")
    p.add_argument("--p0", type=float, required=True, help="성능목표치(performance goal)")
    _add_common(p, cluster=False, interim=False)
    p.set_defaults(alpha=0.025, sides=1)
    for action in p._actions:
        if action.dest == "alpha":
            action.help = "단측 유의수준 (성능목표치 설계 관례에 따라 **기본 0.025**)"
        elif action.dest == "sides":
            action.help = "단측(1, 기본) / 양측(2)"

    p = sub.add_parser("crossover", help="2×2 교차설계 (AB/BA — 같은 사람이 두 처치를 다 받음)")
    p.add_argument("--diff", type=float, required=True, help="처치 간 차이 (원래 단위)")
    p.add_argument("--sd-within", type=float, required=True,
                   help="**개인 내** 표준편차 σ_w (= 차이의 SD / √2)")
    _add_common(p, cluster=False, interim=False)

    p = sub.add_parser("mcnemar", help="대응 비율 비교 (McNemar — 같은 사람의 두 판정)")
    p.add_argument("--p01", type=float, required=True,
                   help="전체 대상자 중 '방법1만 양성'인 비율")
    p.add_argument("--p10", type=float, required=True,
                   help="전체 대상자 중 '방법2만 양성'인 비율")
    _add_common(p, cluster=False)

    p = sub.add_parser("kappa", help="범주형 일치도 κ 연구 (신뢰구간 폭 기준)")
    p.add_argument("--kappa", type=float, required=True, help="예상 κ (0~1)")
    p.add_argument("--width", type=float, required=True, help="목표 CI 전체 폭 (예: 0.2 = ±0.1)")
    p.add_argument("--prevalence", type=float, default=0.5,
                   help="관심 범주의 유병률 π (기본 0.5)")
    p.add_argument("--alpha", type=float, default=0.05, help="1 − 신뢰수준 (기본 0.05)")
    p.add_argument("--n", type=int, default=None,
                   help="확보 가능한 대상자 수 — 주면 그때의 예상 정밀도를 계산")
    p.add_argument("--format", default="text", choices=("text", "md", "json"))
    p.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    p.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    p = sub.add_parser("icc", help="ICC 신뢰도 연구 (신뢰구간 폭 기준)")
    p.add_argument("--icc", type=float, required=True, help="예상 ICC")
    p.add_argument("--width", type=float, required=True,
                   help="목표 CI 전체 폭 (신뢰수준은 --alpha로 정합니다)")
    p.add_argument("--raters", type=int, default=2, help="대상자당 측정 횟수 k (기본 2)")
    p.add_argument("--alpha", type=float, default=0.05, help="1 − 신뢰수준 (기본 0.05)")
    p.add_argument("--n", type=int, default=None,
                   help="확보 가능한 대상자 수 — 주면 그때의 예상 정밀도를 계산")
    p.add_argument("--format", default="text", choices=("text", "md", "json"))
    p.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    p.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    p = sub.add_parser("loa", help="Bland–Altman 일치한계 정밀도 기준 표본수")
    p.add_argument("--sd-diff", type=float, required=True, help="예상되는 차이의 표준편차")
    p.add_argument("--half-width", type=float, required=True, help="목표 LoA CI 반폭")
    p.add_argument("--alpha", type=float, default=0.05, help="1 − 신뢰수준 (기본 0.05)")
    p.add_argument("--n", type=int, default=None,
                   help="확보 가능한 대상자 수 — 주면 그때의 예상 정밀도를 계산")
    p.add_argument("--format", default="text", choices=("text", "md", "json"))
    p.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    p.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    p = sub.add_parser("diag", help="진단정확도 연구 (민감도·특이도 정밀도 기준)")
    p.add_argument("--sens", type=float, required=True, help="예상 민감도")
    p.add_argument("--spec", type=float, required=True, help="예상 특이도")
    p.add_argument("--prevalence", type=float, required=True,
                   help="대상 집단의 유병률 (연속 등록 코호트 기준)")
    p.add_argument("--half-width", type=float, required=True,
                   help="목표 신뢰구간 반폭 (예: 0.05 = ±5%%p)")
    p.add_argument("--method", default="wilson", choices=("wilson", "wald"),
                   help="신뢰구간: wilson(기본, 실제 보고에 가까움) · wald(고전 Buderer)")
    p.add_argument("--alpha", type=float, default=0.05, help="1 − 신뢰수준 (기본 0.05)")
    p.add_argument("--n", type=int, default=None,
                   help="확보 가능한 대상자 수 — 주면 그때의 예상 정밀도를 계산")
    p.add_argument("--format", default="text", choices=("text", "md", "json"))
    p.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    p.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    p = sub.add_parser("pilot", help="사전연구 CSV → 효과크기 → 본연구 표본수")
    p.add_argument("csv", help="사전연구 데이터 CSV")
    p.add_argument("--value", default=None, help="결과변수 열 (두 군 비교)")
    p.add_argument("--group", default=None, help="군 열 (두 군 비교)")
    p.add_argument("--groups", default=None, help="비교할 두 군 이름 (예: sham,device)")
    p.add_argument("--pre", default=None, help="사전 측정 열 (전후 비교)")
    p.add_argument("--post", default=None, help="사후 측정 열 (전후 비교)")
    p.add_argument("--baseline", default=None,
                   help="기저값 열 (두 군 비교) — 기저값 상관 r을 추정해 ANCOVA 표본수도 제시")
    p.add_argument("--filter", action="append", default=None, metavar="열=값",
                   help="특정 행만 사용 (예: --filter 군=중재). 여러 번 지정 가능")
    p.add_argument("--plan-on", default="conservative",
                   choices=("conservative", "observed"),
                   help="표본수를 무엇으로 계획할지: conservative(신뢰구간 하한, 기본) "
                        "또는 observed(관측 효과크기 — 과소추정 위험)")
    p.add_argument("--conf", type=float, default=0.95, help="효과크기 신뢰수준 (기본 0.95)")
    p.add_argument("--skip-invalid", action="store_true",
                   help="숫자로 읽을 수 없는 값을 결측으로 처리")
    p.add_argument("--show-values", action="store_true",
                   help="오류 메시지에 문제가 된 **원본 셀 값**을 보여 줍니다 "
                        "(자유기술 값이면 재식별 위험 — 기본은 위치만 표시)")
    p.add_argument("--redact", action="store_true",
                   help="군 라벨·관측 범위를 결과에서 가립니다 (출력물을 공유할 때)")
    _add_common(p)
    return parser


#: 재현 정보에서 값을 가려야 하는 옵션 (--redact를 켰을 때)
_REDACT_OPTIONS = frozenset({"--filter", "--groups", "--value", "--group", "--baseline",
                             "--pre", "--post"})


def unit_from_total(design, total: int) -> int:
    """총 N → 설계의 unit(1군 n·군당 n·순서당 n …).

    예전에는 설계 이름을 하드코딩한 목록으로 나눴다. 그러다 보니 새로 추가한
    `crossover`가 목록에 빠져 총 66명을 "순서당 66명 = 총 132명"으로 읽고 검정력을
    17%p 부풀렸다. 이제는 **설계에게 직접 물어본다** — allocation(u)["total"]이
    u에 대해 비감소이므로, 총 N을 넘지 않는 가장 큰 u를 이분법으로 찾는다.
    """
    lo = design.min_unit
    if design.allocation(lo)["total"] > total:
        raise PowerPlanError(
            f"--n-total {total:,}은 이 설계에 너무 작습니다 "
            f"(최소 배분이 {design.allocation(lo)['total']:,}명입니다)"
        )
    hi = max(lo, total)
    while design.allocation(hi)["total"] <= total and hi < total:
        hi *= 2
    hi = min(hi, total)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if design.allocation(mid)["total"] <= total:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _apply_n_total(args, design) -> str | None:
    """--n-total(총 N) → --n(단위당 n). 환산 내용을 설명 문구로 돌려준다."""
    total = getattr(args, "n_total", None)
    if total is None:
        return None
    if getattr(args, "n", None) is not None:
        raise PowerPlanError("--n 과 --n-total 은 함께 쓸 수 없습니다 (하나만 지정하세요)")
    total = as_int("--n-total", total, minimum=1)
    if total > MAX_GIVEN_N:
        raise PowerPlanError(
            f"--n-total: {MAX_GIVEN_N:,}보다 큰 표본수는 지원하지 않습니다 (받은 값: {total:,})"
        )
    unit = unit_from_total(design, total)
    args.n = unit
    alloc = design.allocation(unit)
    note = (f"--n-total {total:,} → {design.unit_kr} {unit:,} "
            f"({_alloc_summary(alloc)})")
    if alloc["total"] < total:
        note += (f" · 배분이 딱 떨어지지 않아 {total - alloc['total']:,}명은 계산에 "
                 "쓰이지 않았습니다")
    return note


def _alloc_summary(alloc: dict) -> str:
    parts = [f"{k}={v:,}" for k, v in alloc.items() if k != "total"]
    return ", ".join(parts) + f" → 총 {alloc['total']:,}명"


def _parse_timing(raw) -> tuple[float, ...] | None:
    """'0.4,0.7' → (0.4, 0.7). 값 검증은 sequential.check_timing이 맡는다."""
    if raw is None:
        return None
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts:
        raise PowerPlanError("--timing: 정보비율을 하나 이상 적으세요 (예: --timing 0.5)")
    out = []
    for part in parts:
        try:
            out.append(float(part))
        except ValueError:
            raise PowerPlanError(
                f"--timing: 숫자여야 합니다 (받은 값: {part!r}). 예: --timing 0.5,1.0"
            ) from None
    return tuple(out)


def _shell_quote(token: str) -> str:
    """명령줄을 다시 붙여 쓸 수 있게 최소한으로 인용한다."""
    if token and all(ch.isalnum() or ch in "-_.,=/:+@" for ch in token):
        return token
    return "'" + token.replace("'", "'\\''") + "'"


#: 재현 정보에서 값을 가려야 하는 옵션 (--redact를 켰을 때)
_REDACT_OPTIONS = frozenset({"--filter", "--groups", "--value", "--group", "--baseline",
                             "--pre", "--post"})


def _shorten_path_token(token: str) -> tuple[str, bool]:
    """경로처럼 보이는 토큰을 파일 이름만 남긴다. `--opt=경로` 형태도 처리한다."""
    if token.startswith("--") and "=" in token:
        name, _, value = token.partition("=")
        shortened, changed = _shorten_path_token(value)
        return (f"{name}={shortened}", changed)
    if token.startswith("-") and len(token) > 2 and not token.startswith("--"):
        # -o/경로 처럼 붙여 쓴 짧은 옵션
        shortened, changed = _shorten_path_token(token[2:])
        return (token[:2] + shortened, changed)
    if not token.startswith("-") and (os.sep in token
                                      or (os.altsep and os.altsep in token)):
        base = os.path.basename(token.rstrip(os.sep)) or token
        return base, True
    return token, False


def _provenance(argv) -> dict:
    """이 결과를 만든 명령·버전·시각 — 저장물만 봐도 재현할 수 있게.

    프로토콜·SAP 부록에 붙이는 표에는 "어떤 도구의 어떤 버전에, 무엇을 넣어
    얻은 숫자인가"가 반드시 들어가야 한다. 표만 있고 명령이 없으면 심사에서
    재현할 수 없다.

    다만 **경로는 파일 이름만 남긴다**. 임상 자료의 폴더 경로에는 연구명·사이트·
    대상자 식별자가 들어가는 일이 흔한데, 이 줄은 그대로 문서에 붙여 넣게 되어
    있기 때문이다 (재현에 필요한 것은 옵션이지 폴더 위치가 아니다).
    """
    shortened = False
    redact = "--redact" in argv
    tokens: list[str] = []
    hide_next = False
    for token in argv:
        if hide_next:
            tokens.append("(가림)")
            hide_next = False
            continue
        token, changed = _shorten_path_token(token)
        shortened = shortened or changed
        if redact:
            name = token.split("=", 1)[0]
            if name in _REDACT_OPTIONS:
                if "=" in token:
                    tokens.append(f"{name}=(가림)")
                    continue
                hide_next = True
                tokens.append(token)
                continue
        # 값에 터미널 이스케이프·양방향 오버라이드·수식 선행문자가 들어올 수 있다.
        # 이 줄은 사용자가 문서에 그대로 붙여 넣는 자리이므로 반드시 걸러야 한다.
        tokens.append(strip_unsafe(token, 200))
    command = "powerplan " + " ".join(_shell_quote(a) for a in tokens)
    return {
        "tool": "powerplan",
        "version": __version__,
        "command": command,
        "generated": _datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "paths_shortened": shortened,
        "redacted": redact,
    }


def _adjustments(args) -> Adjustments:
    return Adjustments(
        dropout=getattr(args, "dropout", 0.0),
        cluster_size=getattr(args, "cluster_size", None),
        cluster_icc=getattr(args, "cluster_icc", None),
        comparisons=getattr(args, "comparisons", 1),
        alpha_method=getattr(args, "alpha_method", "bonferroni"),
        interim=getattr(args, "interim", None),
        spending=getattr(args, "spending", "obf"),
        timing=_parse_timing(getattr(args, "timing", None)),
    )


def _require_one_target(args) -> None:
    if args.power is None and args.n is None:
        raise PowerPlanError(
            "목표 검정력(--power 0.8) 또는 확보 가능한 표본수(--n 30) 중 하나를 지정하세요"
        )

#: --n 상한. 이보다 큰 표본수는 계획이 아니라 오타이며, 내부 로그 계산의
#: 상쇄오차 영역(자유도 1e12 이상)에 들어가 결과를 신뢰할 수 없다.
MAX_GIVEN_N = 10_000_000


def _validate_common(args) -> None:
    """설계별 검증 **이전에** 사용자 입력을 먼저 거른다.

    예전에는 α 검증이 설계 생성 시점에만 있어서, 다중비교 보정이 먼저 실행되며
    `--alpha 2 --alpha-method sidak`이 복소수를 만들어 트레이스백으로 죽었다.
    """
    if getattr(args, "alpha", None) is not None:
        # 다중비교 보정 **전의** α도 (하한, 0.5) 안이어야 한다. 보정 후만 검사하면
        # --alpha 0.99 --comparisons 100 같은 입력이 조용히 통과하고, 하한이 없으면
        # 1 − α/2가 정확히 1.0으로 반올림돼 분위수가 무한대가 된다.
        # icc/loa/kappa의 신뢰수준에도 같은 기준을 적용한다.
        alpha_value("--alpha", args.alpha)
    if getattr(args, "power", None) is not None:
        probability("--power", args.power)
    if getattr(args, "conf", None) is not None:
        probability("--conf", args.conf)
    if getattr(args, "n", None) is not None:
        as_int("--n", args.n, minimum=1)
        if args.n > MAX_GIVEN_N:
            raise PowerPlanError(
                f"--n: {MAX_GIVEN_N:,}보다 큰 표본수는 지원하지 않습니다 (받은 값: {args.n:,})"
            )
    if getattr(args, "n_total", None) is not None:
        as_int("--n-total", args.n_total, minimum=1)
    for name in ("d", "dz", "sd", "sd_diff", "mean", "mean1", "mean2", "ref",
                 "margin", "diff", "r", "p1", "p2", "f", "icc", "width", "half_width",
                 "baseline_r", "ratio", "hr", "median1", "accrual", "followup",
                 "event_rate", "rho", "p01", "p10", "kappa", "prevalence",
                 "p0", "sd_within", "sens", "spec"):
        value = getattr(args, name, None)
        if value is not None:
            as_float(f"--{name.replace('_', '-')}", value)


def _parse_filters(raw) -> list[tuple[str, str]]:
    """['군=중재', ...] → [('군', '중재'), ...]."""
    out = []
    for item in raw or ():
        if "=" not in item:
            raise PowerPlanError(
                f"--filter 는 '열=값' 형태여야 합니다 (받은 값: {item!r}). 예: --filter 군=중재"
            )
        column, value = item.split("=", 1)
        if not column.strip():
            raise PowerPlanError(f"--filter 의 열 이름이 비어 있습니다 (받은 값: {item!r})")
        out.append((column.strip(), value.strip()))
    return out


def _write_output(path: str, text: str, force: bool) -> None:
    """결과 저장 — 심볼릭/하드 링크를 따라가지 않고, 조용히 덮어쓰지 않는다.

    파일 권한은 **열고 나서도** 0600으로 맞춘다 (환자 유래 통계가 담길 수 있으므로).
    O_CREAT의 mode 인자는 새로 만들 때만 적용되므로, `--force`로 기존 파일을
    덮어쓰면 그 파일의 권한(예: 0666)이 그대로 남는 문제가 있었다.
    """
    # O_TRUNC를 처음부터 걸면 하드링크를 확인하기 전에 이미 내용이 날아간다.
    # 먼저 열고, 검사한 뒤, 직접 자른다.
    # 파이프(FIFO)에 열면 읽는 쪽이 붙을 때까지 영원히 멈춘다 — 먼저 걸러낸다.
    try:
        info = os.lstat(path)
    except OSError:
        info = None
    if info is not None:
        if stat.S_ISLNK(info.st_mode):
            raise PowerPlanError(
                f"심볼릭 링크입니다: {path} — 링크는 따라가지 않습니다. "
                "실제 파일 경로를 지정하세요"
            )
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISCHR(info.st_mode):
            raise PowerPlanError(
                f"일반 파일이 아닙니다(파이프/소켓/디렉터리 등): {path}. "
                "저장할 파일 경로를 지정하세요"
            )
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    if not force:
        flags |= os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise PowerPlanError(
            f"이미 있는 파일입니다: {path} — 덮어쓰려면 --force 를 붙이세요"
        ) from None
    except OSError as exc:
        detail = exc.strerror or ""
        if getattr(exc, "errno", None) == errno.ELOOP:
            detail = "심볼릭 링크는 따라가지 않습니다 — 실제 파일 경로를 쓰세요"
        raise PowerPlanError(
            f"결과를 저장할 수 없습니다: {path} ({detail})"
        ) from None
    try:
        info = os.fstat(fd)
        if getattr(info, "st_nlink", 1) > 1:
            # 하드 링크는 O_NOFOLLOW로 막을 수 없다 — 다른 이름으로도 가리켜지는
            # 파일을 덮어쓰면 엉뚱한 파일이 지워진다
            os.close(fd)
            raise PowerPlanError(
                f"하드 링크가 걸린 파일입니다: {path} — 다른 이름을 쓰세요"
            )
        if stat.S_ISREG(info.st_mode):
            os.fchmod(fd, 0o600)
            if force:
                os.ftruncate(fd, 0)
    except PowerPlanError:
        raise
    except OSError:  # pragma: no cover - /dev/null 등
        pass
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError as exc:
        raise PowerPlanError(
            f"결과를 저장할 수 없습니다: {path} ({exc.strerror})"
        ) from None


def _make_design(args, alpha: float):
    """하위 명령 인자 → 설계 객체."""
    key = args.design
    if key == "ttest2":
        if args.d is None:
            if None in (args.mean1, args.mean2, args.sd):
                raise PowerPlanError(
                    "--d 를 주거나, --mean1/--mean2/--sd 세 개를 모두 주세요"
                )
            d = cohen_d(args.mean1, args.mean2, args.sd)
        else:
            d = args.d
        return TwoSampleT(d, alpha, args.sides, args.ratio,
                          getattr(args, "baseline_r", 0.0),
                          getattr(args, "analysis", "raw"))
    if key == "paired":
        if args.dz is None:
            if None in (args.diff, args.sd_diff):
                raise PowerPlanError(
                    "--dz 를 주거나, --diff(예상 변화량)와 --sd-diff(변화량 SD)를 함께 주세요"
                )
            if args.sd_diff <= 0:
                raise PowerPlanError("--sd-diff: 0보다 커야 합니다")
            dz = args.diff / args.sd_diff
        else:
            dz = args.dz
        return PairedT(dz, alpha, args.sides)
    if key == "onesample":
        if args.d is None:
            if None in (args.mean, args.ref, args.sd):
                raise PowerPlanError("--d 를 주거나, --mean/--ref/--sd 를 모두 주세요")
            d = cohen_d(args.mean, args.ref, args.sd)
        else:
            d = args.d
        return OneSampleT(d, alpha, args.sides)
    if key == "prop2":
        return TwoProportions(args.p1, args.p2, alpha, args.sides, args.ratio,
                              args.continuity)
    if key == "anova":
        if args.f is None:
            if args.means is None or args.sd is None:
                raise PowerPlanError("--f 를 주거나, --means(예: 8,6,5)와 --sd 를 함께 주세요")
            means = [m for m in (x.strip() for x in args.means.split(",")) if m]
            if len(means) != args.k:
                raise PowerPlanError(
                    f"--means의 값 개수({len(means)})가 --k({args.k})와 다릅니다"
                )
            f = cohen_f_from_means(means, args.sd)
        else:
            f = args.f
        return OneWayAnova(f, args.k, alpha)
    if key == "corr":
        return CorrelationTest(args.r, alpha, args.sides, args.bias_correct)
    if key in ("noninf", "equiv"):
        binary = args.p1 is not None or args.p2 is not None
        if binary:
            if None in (args.p1, args.p2):
                raise PowerPlanError(
                    "이분형 결과에는 --p1(대조군 비율)과 --p2(중재군 비율)를 모두 주세요"
                )
            if args.sd is not None:
                raise PowerPlanError(
                    "--sd(연속형)와 --p1/--p2(이분형)는 함께 쓸 수 없습니다 — "
                    "결과변수가 연속형이면 --sd만, 이분형이면 --p1/--p2만 주세요"
                )
            if args.diff:
                raise PowerPlanError(
                    "이분형에서는 가정 차이가 --p2 − --p1로 정해집니다 (--diff를 빼세요)"
                )
            if key == "noninf":
                return NonInferiorityProportions(args.p1, args.p2, args.margin, alpha,
                                                 args.ratio, args.lower_is_better)
            return EquivalenceProportions(args.p1, args.p2, args.margin, alpha, args.ratio)
        if args.sd is None:
            raise PowerPlanError(
                "연속형 결과면 --sd(표준편차)를, 이분형 결과면 --p1/--p2(두 군 비율)를 "
                "지정하세요"
            )
        if key == "noninf":
            return NonInferiorityT(args.margin, args.sd, args.diff, alpha, args.ratio,
                                   args.lower_is_better)
        return EquivalenceT(args.margin, args.sd, args.diff, alpha, args.ratio)
    if key == "survival":
        return LogRankSurvival(args.hr, alpha, args.sides, args.ratio, args.median1,
                               args.accrual, args.followup, args.event_rate,
                               args.time_unit, args.method)
    if key == "repeated":
        if args.d is None:
            if None in (args.mean1, args.mean2, args.sd):
                raise PowerPlanError(
                    "--d 를 주거나, --mean1/--mean2/--sd(1회 측정의 SD) 세 개를 모두 주세요"
                )
            d = cohen_d(args.mean1, args.mean2, args.sd)
        else:
            d = args.d
        return RepeatedMeasuresT(d, args.post, args.baseline_n, args.rho, args.analysis,
                                 alpha, args.sides, args.ratio, args.estimand)
    if key == "mcnemar":
        return McNemarPaired(args.p01, args.p10, alpha, args.sides)
    if key == "prop1":
        return OneSampleProportion(args.p1, args.p0, alpha, args.sides)
    if key == "crossover":
        return CrossoverT(args.diff, args.sd_within, alpha, args.sides)
    raise PowerPlanError(f"알 수 없는 설계: {key}")  # pragma: no cover


def _units_for(design, target_power: float) -> str:
    """이 설계에서 목표 검정력에 필요한 표본수를 짧은 문구로."""
    try:
        alloc = design.allocation(smallest_unit(design, target_power))
    except PowerPlanError:
        return "계산 불가"
    if "n1" in alloc:
        return f"군당 {alloc['n1']:,}명(총 {alloc['total']:,}명)"
    return f"{alloc['n']:,}명"


def _pilot_plan(args) -> tuple[dict, list[str]]:
    """사전연구 CSV → 관측 효과크기 요약 + 표본수 계획."""
    paired_mode = args.pre is not None or args.post is not None
    group_mode = args.value is not None or args.group is not None
    if paired_mode and group_mode:
        raise PowerPlanError(
            "전후 비교(--pre/--post)와 두 군 비교(--value/--group)는 함께 쓸 수 없습니다"
        )
    if not paired_mode and not group_mode:
        raise PowerPlanError(
            "두 군 비교는 --value/--group, 전후 비교는 --pre/--post 를 지정하세요"
        )
    adj = _adjustments(args)
    alpha, alpha_info = adj.adjusted_alpha(args.alpha)
    target_power = args.power if args.power is not None else 0.80
    filters = _parse_filters(args.filter)

    if paired_mode:
        if None in (args.pre, args.post):
            raise PowerPlanError("전후 비교에는 --pre 와 --post 를 모두 주세요")
        if args.baseline:
            raise PowerPlanError("--baseline 은 두 군 비교(--value/--group)에서만 씁니다")
        data = read_paired(args.csv, args.pre, args.post, args.skip_invalid, filters,
                           args.show_values, args.redact)
        observed = effect_from_paired(data, args.conf)
        effect_value, conservative = observed["dz"], observed["conservative_d"]
        design_at = lambda value: PairedT(value, alpha, args.sides)  # noqa: E731
    else:
        if None in (args.value, args.group):
            raise PowerPlanError("두 군 비교에는 --value 와 --group 을 모두 주세요")
        groups = None
        if args.groups:
            parts = [g.strip() for g in args.groups.split(",") if g.strip()]
            if len(parts) != 2:
                raise PowerPlanError("--groups 는 '군A,군B' 형태로 두 개만 지정하세요")
            if parts[0] == parts[1]:
                raise PowerPlanError(f"--groups 에 같은 군을 두 번 적었습니다: {parts[0]}")
            groups = tuple(parts)
        data = read_two_group(args.csv, args.value, args.group, groups, args.skip_invalid,
                              filters, args.baseline, args.show_values, args.redact)
        observed = effect_from_two_group(data, args.conf)
        effect_value, conservative = observed["d"], observed["conservative_d"]
        design_at = lambda value: TwoSampleT(value, alpha, args.sides, 1.0)  # noqa: E731

    # 무엇으로 계획할지: 기본은 신뢰구간 하한(보수적). 하한이 0을 포함하면
    # 계획 자체가 불가능하므로 관측값으로 되돌리고 크게 경고한다.
    plan_on = args.plan_on
    ci_crosses_zero = conservative <= 0.0
    if plan_on == "conservative" and ci_crosses_zero:
        plan_on = "observed"
    planning_effect = conservative if plan_on == "conservative" else effect_value

    plan = make_plan(design_at(planning_effect), target_power=target_power,
                     unit=args.n, adjustments=adj, sensitivity=args.sensitivity,
                     alpha_adjustment=alpha_info)
    plan["pilot"] = {"data": data, "observed": observed, "planned_on": plan_on,
                     "planning_effect": planning_effect}

    extra: list[str] = []
    ci = observed["ci"]
    extra.append("")
    extra.append("■ 사전연구에서 관측된 효과크기")
    if data.get("filters"):
        extra.append("  선택 조건: " + ", ".join(
            f"{f['column']}={f['value']}" for f in data["filters"])
            + f" (제외된 행 {data.get('filtered_out', 0)}개)")
    if observed["kind"] == "two_group":
        g1, g2 = data["group1"], data["group2"]
        for g in (g1, g2):
            has_range = g["n"] and g["min"] is not None and g["max"] is not None
            extra.append(f"  {g['label']:<20} n={g['n']:,}  평균={g['mean']:.4g}  "
                         f"SD={g['sd']:.4g}"
                         + (f"  범위 {g['min']:.4g}~{g['max']:.4g}" if has_range else "")
                         + (f"  결측={g['missing']}" if g["missing"] else ""))
        extra.append(f"  평균차 {observed['mean_diff']:.4g} · 합동 SD {observed['sd_pooled']:.4g}")
        extra.append(f"  Cohen's d = {observed['d']:.4f}"
                     + (f"  (Hedges g = {observed['hedges_g']:.4f})"
                        if observed["hedges_g"] is not None else ""))
        drop = g1["missing"] + g2["missing"]
        if drop:
            total_rows = g1["n"] + g2["n"] + drop
            extra.append(f"  결측/탈락 {drop}명 / 전체 {total_rows}명 = "
                         f"{drop / total_rows:.1%} → 본연구 --dropout 참고값")
        if data.get("baseline_r") is not None:
            extra.append(f"  기저값('{data['baseline_column']}')-추적값 군내 상관 r = "
                         f"{data['baseline_r']:.4f}"
                         f"  → ANCOVA 계획: --analysis ancova --baseline-r "
                         f"{data['baseline_r']:.3f}")
    else:
        d = data["diff"]
        extra.append(f"  쌍 n={d['n']:,}  변화량 평균={d['mean']:.4g}  변화량 SD={d['sd']:.4g}"
                     + (f"  (불완전 쌍 {data['incomplete_pairs']}개 제외)"
                        if data["incomplete_pairs"] else ""))
        extra.append(f"  Cohen's dz = {observed['dz']:.4f}"
                     + (f"  (Hedges g = {observed['hedges_g']:.4f})"
                        if observed["hedges_g"] is not None else ""))
        if data.get("pre_post_r") is not None:
            extra.append(f"  사전-사후 상관 r = {data['pre_post_r']:.4f}"
                         f"  → 두 군 비교를 계획한다면 --analysis ancova --baseline-r "
                         f"{data['pre_post_r']:.3f}")
    extra.append(f"  {ci['conf']:.0%} 신뢰구간(비중심 t 정확법): "
                 f"[{ci['low']:.4f}, {ci['high']:.4f}]")

    extra.append("")
    if plan_on == "conservative":
        extra.append(f"■ 계획 기준: **신뢰구간 하한** {conservative:.4f} (보수적, 기본값)")
        extra.append(f"  참고) 관측값 {effect_value:.4f}을 그대로 쓰면 표본수가 "
                     f"{_units_for(design_at(effect_value), target_power)}으로 줄지만, "
                     "사전연구 효과크기는 위로 편향되기 쉬워 위험합니다 (--plan-on observed).")
    elif ci_crosses_zero:
        extra.append("■ 계획 기준: 관측 효과크기 (⚠ 신뢰구간이 0을 포함)")
        extra.append("  사전연구만으로는 효과크기를 확정할 수 없습니다. 아래 표본수는 "
                     "참고용일 뿐이며, 임상적으로 의미있는 최소 차이(MCID)를 직접 정해 "
                     "ttest2/paired 로 다시 계산하세요.")
        plan["suppress_protocol_sentence"] = (
            "사전연구 효과크기의 신뢰구간이 0을 포함하므로 이 표본수를 근거로 한 "
            "프로토콜 문장은 만들지 않았습니다. MCID를 정해 ttest2/paired로 다시 "
            "계산한 뒤 그 결과의 문장을 쓰세요.")
    else:
        extra.append(f"■ 계획 기준: 관측 효과크기 {effect_value:.4f} (--plan-on observed)")
        extra.append(f"  보수적 기준(신뢰구간 하한 {conservative:.4f})으로는 "
                     f"{_units_for(design_at(conservative), target_power)}이 필요합니다.")

    plan["notes"].insert(0,
        "사전연구의 관측 효과크기는 위로 편향되기 쉽습니다(승자의 저주). 기본값은 "
        "신뢰구간 하한으로 계획하는 것이며, 임상적으로 의미있는 최소 차이(MCID)가 있다면 "
        "그쪽이 더 좋습니다.")
    plan["notes"].append(
        "계획용 하한의 신뢰수준은 --conf 로 조절합니다. 문헌에서는 60~80% 하한을 쓰는 "
        "제안이 많습니다(Browne 1995; Kieser & Wassmer 1996) — 기본 0.95는 가장 보수적입니다.")
    plan["notes"].append(
        f"읽은 파일: {data['path']} (인코딩 {data['encoding']}, 구분자 "
        f"{data['delimiter']!r})")
    if data.get("redacted"):
        plan["notes"].append(
            "--redact: 군 라벨을 '군1/군2'로 바꾸고 관측 범위(최소·최대)를 지웠습니다.")
    if data.get("invalid_ignored"):
        plan["notes"].append(
            f"--skip-invalid: 숫자로 읽을 수 없는 값 {data['invalid_ignored']}개를 "
            "결측으로 처리했습니다 — 원자료를 꼭 확인하세요")
    if data.get("other_groups"):
        others = ", ".join(
            f"{g['label']}(n={g['n']})" if "n" in g else f"{g['label']}(행 {g['rows']})"
            for g in data["other_groups"][:5])
        plan["notes"].append(f"비교에 쓰지 않은 군: {others}")
    return plan, extra


def main(argv: list[str] | None = None) -> int:
    # 한글을 표현할 수 없는 인코딩(PYTHONIOENCODING=ascii 등)에서도 죽지 않게
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):  # pragma: no cover - 리다이렉트된 스트림
            pass
    parser = _build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if not args.design:
        parser.print_help()
        return 0
    try:
        _validate_common(args)
        extra: list[str] = []
        if args.design == "icc":
            plan = icc_plan(args.icc, args.width, args.raters, args.alpha, args.n)
        elif args.design == "loa":
            plan = loa_plan(args.sd_diff, args.half_width, args.alpha, args.n)
        elif args.design == "kappa":
            plan = kappa_plan(args.kappa, args.width, args.prevalence, args.alpha, args.n)
        elif args.design == "diag":
            plan = diagnostic_plan(args.sens, args.spec, args.prevalence,
                                   args.half_width, args.alpha, args.n, args.method)
        elif args.design == "pilot":
            # 사전연구는 두 군 1:1 비교이므로 총 N을 반으로 나눈다
            if getattr(args, "n_total", None) is not None:
                if args.n is not None:
                    raise PowerPlanError("--n 과 --n-total 은 함께 쓸 수 없습니다")
                total = as_int("--n-total", args.n_total, minimum=1)
                if total > MAX_GIVEN_N:
                    raise PowerPlanError(
                        f"--n-total: {MAX_GIVEN_N:,}보다 큰 표본수는 지원하지 않습니다")
                args.n = max(1, total // 2)
                total_note = (f"--n-total {total:,} → 1군 {args.n:,} "
                              f"(두 군 1:1 → 총 {2 * args.n:,}명)")
            else:
                total_note = None
            plan, extra = _pilot_plan(args)
            if total_note:
                plan["notes"].append(total_note)
        else:
            adj = _adjustments(args)
            alpha, alpha_info = adj.adjusted_alpha(args.alpha)
            design = _make_design(args, alpha)
            limit = getattr(design, "max_unit", None)
            if limit and args.n is not None and args.n > limit:
                raise PowerPlanError(
                    f"--n: {design.key} 설계는 {limit:,}까지만 지원합니다 "
                    f"(받은 값: {args.n:,}). 정확 이항검정은 꼬리를 직접 더하므로 "
                    "이보다 크면 계산이 오래 걸리고, 그 범위에서는 정규근사(prop2 등)로도 "
                    "충분히 정확합니다"
                )
            total_note = _apply_n_total(args, design)
            _require_one_target(args)
            plan = make_plan(design, target_power=args.power, unit=args.n,
                             adjustments=adj, sensitivity=args.sensitivity,
                             alpha_adjustment=alpha_info)
            if total_note:
                plan["notes"].append(total_note)
        plan["provenance"] = _provenance(raw_argv)
        if args.format == "json":
            text = render_json(plan)
        elif args.format == "md":
            text = render_markdown(plan)
        else:
            text = render_text(plan)
            if extra:
                # 사전연구 관측치를 먼저 보여주고, 그 뒤에 계획을 붙인다
                text = "\n".join(extra).lstrip("\n") + "\n\n" + text
        if args.output is not None:
            if not str(args.output).strip():
                raise PowerPlanError(
                    "-o(--output): 저장할 파일 이름이 비어 있습니다. 셸 변수가 비었는지 "
                    "확인하세요 (예: -o \"$OUT\")"
                )
            _write_output(args.output, text, getattr(args, "force", False))
            print(f"저장했습니다: {args.output}")
        else:
            print(text)
        return 0
    except PowerPlanError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # 분포 계산 계층이 범위를 벗어난 입력을 거절한 경우 (자유도·비중심모수 상한 등)
        print(f"오류(수치 범위): {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover - head/파이프 사용 시
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        print("중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
