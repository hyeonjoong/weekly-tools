# paperforge — 보유 데이터 → 멀티모달 논문 아이디어 매트릭스

보유한 데이터셋을 한 줄씩 적은 **매니페스트(JSON)** 를 입력하면, 모달리티를 교차·결합해
바로 쓸 수 있는 **논문 아이디어 매트릭스**(가설 · 변수 · 권장 분석법 · 적합 저널 · 표본
실현가능성)를 만들어 주는 커맨드라인 도구입니다.

## 목적 / Why this exists

**한국어** — 우리 팀은 같은 피험자에게서 EEG(뇌파), 스마트워치(HR/HRV), 호흡밴드, 설문,
MoA 테스트 데이터를 모읍니다. 정작 "이 데이터들을 **어떻게 엮어 논문 한 편**으로 만들지"를
정리하는 일은 매번 백지에서 시작합니다. paperforge는 보유 모달리티를 입력받아, 단일
모달리티뿐 아니라 **EEG×호흡, 워치×설문 같은 멀티모달 조합**을 우선해 검증 가능한 연구
아이디어를 뽑아 주고, 각 아이디어의 **가설·종속/독립변수·권장 통계분석·적합 저널 유형**과
**현재 표본으로 가능한지(검정력 근사)** 까지 한 장의 표로 정리합니다. 연구 기획 회의 전,
또는 새 데이터셋이 들어왔을 때 "무엇을 쓸 수 있나"를 5초 만에 훑는 용도입니다.

**English** — A lab that collects EEG, smartwatch HR/HRV, respiration-band,
questionnaire and MoA data on the same subjects repeatedly faces the same blank
page: *which paper can we actually write by combining these?* paperforge takes a
manifest of the datasets you already hold and emits a ranked idea matrix —
prioritising cross-modal combinations — where each idea carries a hypothesis,
predictor/outcome variables, a recommended analysis, a suitable journal type, and
a quick **feasibility check** (closed-form sample-size approximation) against your
current N. It is for the clinical/physiology researcher scoping the next study or
sizing up a freshly collected dataset. It proposes and prioritises; it does not
run the analysis for you.

## Install

```bash
cd ~/Downloads/02_프로젝트/깃헙/paperforge-논문아이디어발굴기
python3 -m pip install -e .
```

설치 없이도 실행할 수 있습니다: `python3 -m paperforge.cli <매니페스트.json>`
또는 폴더의 **`실행.command` 더블클릭**.

## Usage

```bash
# 전체 아이디어를 점수순으로 출력
paperforge examples/sleep_moa_manifest.json

# 상위 5개만, 마크다운 파일과 CSV로 저장
paperforge examples/sleep_moa_manifest.json --top 5 --out ideas.md --csv ideas.csv

# 검정력 기준 바꾸기 (지원: alpha 0.05/0.01/0.10, power 0.80/0.90/0.95)
paperforge examples/sleep_moa_manifest.json --power 0.90
```

### 매니페스트 형식 (입력)

```json
{
  "study": "수면 MoA 파일럿",
  "datasets": [
    {"name": "MoA EEG", "modality": "eeg", "n": 40,
     "variables": ["delta_power", "theta_power", "alpha_power"]},
    {"name": "호흡밴드", "modality": "respiration", "n": 40,
     "variables": ["resp_rate", "rsa"]}
  ]
}
```

- `modality`는 한글/영문 별칭을 모두 인식합니다: `eeg`/`뇌파`, `watch`/`워치`/`스마트워치`,
  `respiration`/`호흡`/`호흡밴드`, `questionnaire`/`설문`, `behavior`/`유저테스트`, `moa`.
- `n`(표본수)을 넣으면 실현가능성(검정력)까지 자동 판정합니다. 없으면 "표본수 미상"으로 표시.
- 여러 모달리티는 **같은 피험자에서 연결(linked)** 돼 있어야 멀티모달 분석이 가능합니다(리포트에 주의 문구 표시).

### 출력 예시 (발췌)

```
| # | 아이디어 | 모달리티 | 권장 N | 보유 N | 실현가능성 | 적합 저널 |
|---|----------|----------|-------|-------|-----------|-----------|
| 1 | EEG·HRV·호흡 통합 각성지수 개발·검증 | EEG × 워치 × 호흡밴드 × 설문 | 57 | 90 | 충분 가능 | IEEE JBHI / Sensors |
| 2 | EEG–호흡 위상결합과 수면 깊이 | EEG × 호흡밴드 × 설문 | 85 | 92 | 충분 가능 | Psychophysiology / J. Sleep Research |
| 7 | 야간 HRV 동태의 기술·군집 분석 | 워치 × 설문 | 비적용 | 90 | 탐색적 | Sensors / Sleep Health |
| 8 | MoA 반응자 프로파일링 | MoA × EEG × 워치 × 호흡 × 설문 | 126 | 90 | 표본 부족 우려 | Frontiers in Neuroscience |
```

표는 **실현가능성 우선**(충분 가능 → 탐색적 → 표본 부족)으로, 같은 등급 안에서는
멀티모달·변수 풍부 순으로 정렬됩니다. 각 아이디어는 아래 상세 블록에 가설·변수·권장
분석·신규성 메모·표본 경고까지 풀어 줍니다.

## Notes / limitations

- **아이디어 템플릿은 사람이 큐레이션한 지식베이스**(수면/각성 생리)입니다 — 망라가 아니라
  출발점입니다. 분야가 다르면 `paperforge/knowledge.py`에 템플릿을 추가하세요.
- **권장 N은 계획용 근사치**입니다: 상관은 Fisher-z, 평균차/회귀는 정규근사로 계산하고
  항상 올림(round up)합니다. 정밀 검정력은 G*Power 등으로 확정하세요. 정규근사는 두 군
  비교에서 군당 약 1명 적게 나올 수 있어, 효과크기 가정은 보수적(소~중)으로 잡았습니다.
- 효과크기 가정은 각 템플릿에 보수적으로 내장돼 있습니다(예: 상관 r=0.30). 실제 예상
  효과가 더 작다면 권장 N은 더 커집니다.
- 설계에 맞춰 다른 공식을 씁니다: 상관/회귀/독립 2군은 각 닫힌형, **피험자내(반복측정)**
  설계는 paired 공식(더 적은 N), **탐색적(군집 등)** 설계는 고정 표본기준을 두지 않고
  "비적용/탐색적"으로 표시합니다.
- 외부 네트워크를 쓰지 않습니다(완전 오프라인). 표준 라이브러리만 사용.

## License

MIT © 2026 hyeonjoong
