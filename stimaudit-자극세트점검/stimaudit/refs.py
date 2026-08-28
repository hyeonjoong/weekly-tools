"""논문 유래 참조값 — **판정하지 않고 나란히 놓기만 합니다.**

왜 이 파일이 따로 있는가
------------------------
NBR 리뷰(*Acoustic Parameters for Autonomic Arousal Modulation*)의 1st revision
에서 리뷰어 2 는 "간접 증거에서 보편적 설계 처방으로 건너뛴다"고 지적했고,
저자들은 **모든 수치를 reference value / exemplar 로 재라벨**했습니다. 개정본의
문장을 그대로 옮기면:

  "the numeric values attached to these principles are reference values reported
   in the cited studies rather than evidence-derived thresholds … what the
   evidence supports is the direction of each principle, not its cut-point."
  (manuscript_revised.md, Section 3 논의)

그런데 사내 기존 스크립트 `bell_acoustic_qc.py` 는 아직도
`TIER1_RULES = {"roughness_asper_max": 0.3, "attack_ms_min": 50.0}` 를
`tier1_compliant` 불리언으로 찍습니다 — **저자 자신이 철회한 프레이밍입니다.**

stimaudit 은 그것을 따라 하지 않습니다. 이 모듈의 자료구조에는 심각도 필드가
**존재하지 않습니다.** 구조적으로 등급을 붙일 수 없습니다.
치명/경고 판정은 오직 툴 자신의 방법론적 기준(조건 간 음량 불일치·클리핑·
죽은 파일·주장 불일치)에만 붙습니다.

출처 확인
---------
아래 수치와 인용은 2026-08-28 빌드 시점에
`논문_투고/02_BELL002_청각재활/Sound Parameter 정교화/2.NBR_1st_Revision/Source/
manuscript_revised.md` 를 직접 열어 해당 문장을 대조해 옮긴 것입니다.
개정 전 사내 스크립트의 숫자는 출처로 삼지 않았습니다.

**그리고 원문까지 한 번 더 갔습니다.** 리뷰 원고를 그대로 옮기는 것만으로는
부족합니다 — 원고 자체가 틀렸으면 그 오류가 툴에 그대로 복제되기 때문입니다.
2026-08-28 하드닝에서 Czempik et al. (2020) 원문(PMC7644698, Table 2)을 직접
확인한 결과 **LAeq20sec 의 상관은 r = −0.41 (p = 0.02) 이었습니다** — 원고에
적힌 −0.50 이 아닙니다. 또 57.9 dB 는 '평균보다 짧게 잔 환자'를 가르는 **ROC
절단점**이지 exemplar 값이 아닙니다. 이 모듈은 **원문 값**을 씁니다.
(원고 쪽 수정은 사람이 판단할 일이라 여기서 하지 않고, 빌드 보고서에 남깁니다.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

#: 리포트 어디서든 참조값 옆에 반드시 붙는 고지 문구.
DISCLAIMER = "reference value · 임계값 아님"
#: 참조값 표 아래에 붙는 긴 고지.
LONG_DISCLAIMER = (
    "※ 논문(1st revision)은 이 수치들을 임계값이 아니라 reference value 로 규정합니다.\n"
    "   \"준수/위반\"으로 읽지 마십시오. 파일별 티어 판정은 bell_acoustic_qc.py 소관이고,\n"
    "   stimaudit 은 측정값과 참조값을 나란히 놓기만 합니다."
)


@dataclass(frozen=True)
class ReferenceValue:
    """논문 유래 참조값 한 줄.

    **심각도 필드가 없습니다 — 의도적입니다.** 이 자료형은 등급을 표현할 수
    없으므로, 실수로라도 참조값에 치명/경고가 붙는 코드를 쓸 수 없습니다.
    """

    axis: str            # 축 이름 (한국어)
    value_text: str      # 참조값 표기 (예: "~50 ms 이상")
    citation: str        # 출처 문헌
    measured_by_tool: bool   # stimaudit 이 이 축을 실제로 재는가
    note: str = ""       # 논문 개정본의 성격 규정


#: 논문 Table 1 / 본문에서 확인한 참조값들.
REFERENCES: List[ReferenceValue] = [
    ReferenceValue(
        axis="온셋 상승시간 (attack)",
        value_text="~50 ms 이상 (수십 ms 이상)",
        citation="Foley et al. 2022 (JASA 151:3189) · Hailstone et al. 2009 (QJEP 62:2141) (방향성: Eerola et al. 2012)",
        measured_by_tool=True,
        note="개정본: \"exemplar values, not validated criteria\" — attack time 을 "
             "자율신경 측정과 함께 파라메트릭하게 조작한 연구가 아직 없기 때문. "
             "인용 문헌은 **방향**(느린 온셋일수록 각성 낮음)의 근거이며 50 ms 라는 "
             "수치 자체의 1차 출처는 확인되지 않았습니다 (Foley 2022 는 온셋을 "
             "5 ms 로 고정하고 감쇠를 조작했고, 두 논문 모두 자율신경 지표를 "
             "측정하지 않았습니다). 자율신경 지표로 온셋을 실제 조작한 1차 연구는 "
             "5 ms vs 200 ms 대비입니다 (Turpin, Schaefer & Boucsein 1999, "
             "Psychophysiology 36:453–463).",
    ),
    ReferenceValue(
        axis="템포 / 변조율",
        value_text="60–80 BPM (1.0–1.33 Hz)",
        citation="Bretherton et al. 2019 · Watanabe et al. 2017 · Krabs et al. 2015",
        measured_by_tool=True,
        note="개정본: \"exemplar values from those studies\" — 방향(느릴수록 각성 낮음)이 "
             "근거이고 절대 템포는 개인화 대상. 실제 제시 템포는 Bretherton 2019 가 "
             "60/90/120/150/180 BPM(180 BPM 에서도 부교감 우세 이동이 관찰되어 "
             "단조롭지 않습니다), Watanabe 2017 은 실험1 만 고정 80 BPM 이고 "
             "실험2·3 은 개인 기저심박 대비 상대 템포입니다 — 그 연구의 결론은 "
             "**기저심박보다 빠른 템포가 심박을 올린다**이므로 '느릴수록 낮다'의 "
             "근거로 쓰면 방향이 뒤집힙니다. Krabs 2015 는 90 vs 120 BPM 에서 "
             "HR·HRV 모두 차이를 찾지 못한 연구입니다(리듬 주기성이 정서가보다 "
             "직접적인 구동요인이라는 근거로 인용). 60–80 BPM 구간 자체의 1차 "
             "출처는 확인되지 않았습니다.",
    ),
    ReferenceValue(
        axis="서파(SO) 자극 반복률",
        value_text="~0.8 Hz",
        citation="Schade et al. 2020 (개방루프, 50 ms 버스트를 1.25초마다 = 0.8 Hz) · "
                 "Ngo et al. 2013 J Sleep Res 22:22–31 (리듬 자극 0.8 Hz)",
        measured_by_tool=True,
        note="0.8 Hz 는 이 두 편의 제시율입니다. 같은 해 Ngo et al. 2013 Neuron "
             "78:545–553 은 위상잠금이라 제시율이 피험자의 서파로 정해지므로 "
             "(펄스 쌍 간격 1.075초) 0.8 Hz 의 출처가 아닙니다.",
    ),
    ReferenceValue(
        axis="러프니스 (roughness)",
        value_text="~0.3 asper 근처 또는 그 아래",
        citation="단위(asper) 정의: Daniel & Weber 1997 · 계산 구현: Harrison & Pearce 2020 · "
                 "Eerola & Lahdelma 2021 (무차원 러프니스 지표) · "
                 "30–150 Hz AM 에 편도체가 선택적 반응(청각피질은 아님): Arnal et al. 2015",
        measured_by_tool=False,
        note="stimaudit 은 심리음향량을 계산하지 않습니다 — DEBUSSY / mosqito 소관. "
             "음악심리학 구현이 내는 러프니스는 무차원 지표라 asper 와 같은 눈금이 "
             "아니므로, 매니페스트에 계산 방법을 함께 적으십시오.",
    ),
    ReferenceValue(
        axis="샤프니스 (sharpness)",
        value_text="~1.5 acum (개정본이 인용 · 1차 출처 확인 안 됨)",
        citation="단위(acum) 정의: Zwicker & Fastl 2007 · 음악 자극에서의 사용례: "
                 "Eerola & Lahdelma 2022, Psychon Bull Rev 29:800–808",
        measured_by_tool=False,
        note="개정본: \"an exemplar value and remains provisional\". 1.5 acum 을 "
             "상한으로 보고한 1차 출처는 확인되지 않았습니다 (Zwicker & Fastl 의 "
             "불쾌도 모형 문턱은 1.75 acum) — 참조값으로만 읽으십시오.",
    ),
]

#: 레벨 축의 근거 — 왜 LAmax 를 평균과 나란히 반드시 인쇄하는가.
LEVEL_RATIONALE = (
    "Czempik et al. (2020, Sci Rep 10:19207) 은 ICU 에서 수면시간과의 상관이 "
    "LAmax r = −0.64 (p = 0.0001) 로 LAeq20sec r = −0.41 (p = 0.02) 보다 강하다고 "
    "보고했습니다 — 평균보다 순간 최대치가 더 관련된다는 뜻입니다. "
    "57.9 dB 는 '평균적 수면시간보다 짧게 잔 환자'를 가르는 **LAmax 의** ROC 절단점이고"
    "(AUC 0.81, 95% CI 0.64–0.93), 참조범위나 권고 상한이 아닙니다. "
    "단일 소음사건의 최대레벨이 등가레벨보다 수면방해를 잘 반영한다는 방향은 "
    "교통소음 수면연구의 통합 재분석에서도 보고됩니다 "
    "(Basner & McGuire 2018, Int J Environ Res Public Health 15:519 — "
    "실내 Lmax 10 dBA 증가당 각성 오즈비 항공 1.35 · 도로 1.36 · 철도 1.35)."
)

#: 절대 SPL 을 요구받았을 때 돌려주는 설명.
ABSOLUTE_SPL_REFUSAL = (
    "절대 음압(dB SPL / dB HL)은 재생 체인 보정 없이는 파일에서 알 수 없습니다.\n"
    "이 툴이 내는 LAeq·LAmax 는 모두 **dBFS 기준**(풀스케일 대비)입니다.\n"
    "WHO(2009, 2018)의 Lnight 40 dB / 55 dB 같은 참조값은 절대 SPL 이므로\n"
    "이 툴의 dBFS 값과 직접 비교할 수 없습니다 — 비교하려면 재생 장비의\n"
    "풀스케일 대응 SPL 을 측정해 보정 상수를 구하십시오."
)

#: LUFS 가 논문 유래가 아님을 밝히는 고지 — README 와 모든 리포트에 들어갑니다.
LUFS_PROVENANCE = (
    "LUFS / EBU R128 은 이 논문에 한 번도 나오지 않습니다. 논문은 라우드니스를\n"
    "파라미터로 채점하지 않았습니다. 여기서 LUFS 를 쓰는 이유는 오직 하나 —\n"
    "조건 간 체감 음량을 맞췄는지 보는 **실험 통제 관행의 표준 수단**이기 때문입니다.\n"
    "논문 단위(LAeq·LAmax·다이내믹 레인지)는 항상 LUFS 와 나란히 인쇄됩니다."
)


def unmeasured_axes() -> List[ReferenceValue]:
    return [r for r in REFERENCES if not r.measured_by_tool]
