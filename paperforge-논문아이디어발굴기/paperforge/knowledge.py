"""Curated knowledge base of paper-idea templates.

Each template encodes a publishable analysis angle for sleep/arousal
physiology research that combines the modalities this lab actually collects
(EEG, smartwatch HR/HRV, respiration band, questionnaires, behaviour, MoA).

Templates are intentionally hand-curated, not generated, so that the
hypotheses, recommended analyses and effect-size assumptions are defensible.
``required`` lists the canonical modality keys that MUST all be present for the
idea to be offered; ``effect`` feeds the sample-size feasibility check.

The effect sizes are deliberately *conservative planning priors* drawn from the
typical magnitudes reported in psychophysiology (small-to-medium), so the
feasibility flag errs toward "you may be underpowered" rather than the reverse.
"""
from __future__ import annotations

# Each idea: id, title, required modalities, optional modalities, hypothesis,
# predictors, outcomes, analysis, design, effect (for power), journal, novelty.
IDEA_TEMPLATES = [
    # ---- Multimodal (preferred) ----------------------------------------
    {
        "id": "eeg_resp_coupling",
        "title": "EEG–호흡 위상결합과 수면 깊이 (respiration-locked cortical slowing)",
        "required": ["eeg", "respiration"],
        "optional": ["questionnaire"],
        "hypothesis": "느린 호흡 위상에 EEG 서파(delta/theta) 파워가 증가하며, 이 호흡-피질 결합 강도가 주관적 수면의 질과 양(+)의 관계를 보인다.",
        "predictors": ["호흡 위상(흡기/호기)", "호흡률", "RSA"],
        "outcomes": ["delta/theta 파워", "위상-진폭 결합(PAC) 지수"],
        "analysis": "위상-진폭 결합(modulation index) + 피험자내 혼합효과모형(linear mixed model); 결합지수와 설문 점수는 부분상관.",
        "design": "within-subject (반복측정)",
        "effect": {"type": "correlation", "r": 0.30},
        "journal": "Psychophysiology / Journal of Sleep Research (생리신호 방법론)",
        "novelty": "워치·설문만 쓰는 수면 연구가 많아 EEG×호흡 결합을 직접 정량화한 사례가 적음 — 멀티모달 기여 명확.",
    },
    {
        "id": "hrv_questionnaire_arousal",
        "title": "야간 HRV로 본 주관적 수면·스트레스의 생리 지표화",
        "required": ["watch", "questionnaire"],
        "optional": ["respiration"],
        "hypothesis": "수면 중 HRV(RMSSD, LF/HF)가 다음 날 주관적 회복감·스트레스 설문 점수를 예측한다.",
        "predictors": ["RMSSD", "SDNN", "LF/HF", "야간 평균 HR"],
        "outcomes": ["주관적 수면의 질(PSQI 등)", "스트레스/회복 설문"],
        "analysis": "다중회귀(공변량: 연령·성별) + 표준화 계수; 필요시 위계적 회귀로 증분설명력 보고.",
        "design": "correlational / predictive",
        "effect": {"type": "regression", "f2": 0.15, "k": 3},
        "journal": "Sleep Health / Frontiers in Psychology (디지털 헬스 측정)",
        "novelty": "소비자 워치 HRV를 주관적 지표의 객관 대리지표로 검증 — 실용·재현성 측면에서 게재가치.",
    },
    {
        "id": "multimodal_arousal_index",
        "title": "EEG·HRV·호흡 통합 각성지수(composite arousal index) 개발·검증",
        "required": ["eeg", "watch", "respiration"],
        "optional": ["questionnaire", "behavior"],
        "hypothesis": "EEG(베타/알파비), HRV, 호흡 변동성을 결합한 복합 각성지수가 단일 모달리티보다 주관적 각성/수면잠복기를 더 잘 설명한다.",
        "predictors": ["EEG 베타/알파 비", "HRV 지표", "호흡 변동성"],
        "outcomes": ["주관적 각성/이완", "수면 잠복기"],
        "analysis": "주성분/요인분석으로 복합지수 도출 → 증분타당도(위계적 회귀); 교차검증(LOOCV).",
        "design": "index construction + validation",
        "effect": {"type": "regression", "f2": 0.15, "k": 3},
        "journal": "IEEE J. Biomedical and Health Informatics / Sensors (멀티모달 지표)",
        "novelty": "3모달 통합 지표는 드물고 임상·웨어러블 양쪽에 어필 — 방법론 논문으로 강함.",
    },
    {
        "id": "watch_vs_eeg_validation",
        "title": "소비자 워치 수면지표 vs EEG 기준선 일치도(검증 연구)",
        "required": ["watch", "eeg"],
        "optional": [],
        "hypothesis": "워치 추정 수면지표(수면시간·각성)는 EEG 기준선과 체계적 편향을 가지며 Bland–Altman 한계 내에서 일치한다.",
        "predictors": ["워치 수면단계/각성 추정"],
        "outcomes": ["EEG 기반 수면지표(기준선)"],
        "analysis": "Bland–Altman 일치도 + 급내상관(ICC) + 민감도/특이도(에포크 단위).",
        "design": "method comparison",
        "effect": {"type": "correlation", "r": 0.30},
        "journal": "Sleep / Journal of Clinical Sleep Medicine (기기 검증)",
        "novelty": "기기 검증 연구는 인용 수요 꾸준 — 회사 보유 EEG가 기준선이 되는 강점.",
    },
    {
        "id": "moa_responder_profiling",
        "title": "MoA 반응자 프로파일링: 생리신호로 본 반응자/비반응자",
        "required": ["moa", "eeg"],
        "optional": ["watch", "respiration", "questionnaire"],
        "hypothesis": "MoA 테스트의 반응자와 비반응자는 기저 EEG/자율신경 프로파일에서 차이를 보인다.",
        "predictors": ["기저 EEG 파워", "기저 HRV/호흡"],
        "outcomes": ["MoA 반응 여부(이분)"],
        "analysis": "두 군 비교(t/Mann–Whitney + 효과크기 Hedges g) → 로지스틱 회귀로 분류; ROC.",
        "design": "two-group comparison",
        "effect": {"type": "two_group", "d": 0.5},
        "journal": "Frontiers in Neuroscience / Scientific Reports (기전·바이오마커)",
        "novelty": "제품 MoA를 생리 바이오마커로 설명 — 회사 데이터만의 차별점.",
    },
    {
        "id": "behavior_physio_link",
        "title": "유저테스트 행동지표와 생리각성의 연결",
        "required": ["behavior", "watch"],
        "optional": ["eeg", "questionnaire"],
        "hypothesis": "유저테스트 중 행동지표(반응시간·이탈·과제수행)가 동시 측정된 생리각성(HR/HRV)과 관련된다.",
        "predictors": ["HR/HRV 변화", "EEG 각성지표"],
        "outcomes": ["과제 수행/행동 로그 지표"],
        "analysis": "상관/혼합효과모형(시행 수준) + 다중비교 보정(FDR).",
        "design": "within-subject correlational",
        "effect": {"type": "correlation", "r": 0.30},
        "journal": "International Journal of Human–Computer Studies / Applied Ergonomics",
        "novelty": "행동×생리 동시측정은 UX·생리심리 경계 영역으로 신선.",
    },
    # ---- Single-modality fallbacks (still useful) ----------------------
    {
        "id": "eeg_spectral_profile",
        "title": "수면/이완 상태의 EEG 스펙트럼 프로파일 기술연구",
        "required": ["eeg"],
        "optional": ["questionnaire"],
        "hypothesis": "상태(이완 전/후 등)에 따라 EEG 대역별 파워가 체계적으로 변한다.",
        "predictors": ["상태/조건"],
        "outcomes": ["delta/theta/alpha/beta 파워", "전두 알파 비대칭"],
        "analysis": "반복측정 ANOVA / 혼합효과모형 + 효과크기(η²); 다중비교 보정.",
        "design": "within-subject",
        "effect": {"type": "paired", "d": 0.5},
        "journal": "Frontiers in Human Neuroscience (기술적 EEG)",
        "novelty": "단일모달이라 신규성은 낮지만 후속 멀티모달 연구의 토대.",
    },
    {
        "id": "hrv_descriptive",
        "title": "야간 HRV 동태의 기술·군집 분석",
        "required": ["watch"],
        "optional": ["questionnaire"],
        "hypothesis": "야간 HRV 시간경과 패턴으로 구별되는 하위군집이 존재한다.",
        "predictors": ["야간 HRV 시계열 특징"],
        "outcomes": ["군집 소속", "주관적 수면(있다면)"],
        "analysis": "특징추출 → k-means/계층군집 + 실루엣; 군집 간 설문 비교.",
        "design": "exploratory clustering",
        "effect": {"type": "exploratory"},
        "journal": "Sensors / Sleep Health (탐색적)",
        "novelty": "탐색적이나 표본 적어도 가능 — 파일럿/예비연구로 적합.",
    },
    {
        "id": "questionnaire_psychometrics",
        "title": "설문 척도의 심리측정 타당화(신뢰도·요인구조)",
        "required": ["questionnaire"],
        "optional": [],
        "hypothesis": "사용 설문이 기대된 하위요인 구조와 내적일관성을 보인다.",
        "predictors": ["문항 응답"],
        "outcomes": ["하위척도 점수", "신뢰도/요인구조"],
        "analysis": "Cronbach α + 탐색적/확인적 요인분석 + 문항-총점 상관.",
        "design": "psychometric",
        "effect": {"type": "correlation", "r": 0.30},
        "journal": "BMC Psychology / PLOS ONE (측정도구)",
        "novelty": "도구 타당화는 인용 토대가 되며 생리데이터와 묶을 발판.",
    },
]


def template_by_id(idea_id: str):
    for t in IDEA_TEMPLATES:
        if t["id"] == idea_id:
            return t
    return None
