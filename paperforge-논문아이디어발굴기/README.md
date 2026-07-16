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

# 상위 5개만, 마크다운 파일과 CSV/JSON으로 저장
paperforge examples/sleep_moa_manifest.json --top 5 --out ideas.md --csv ideas.csv --json ideas.json

# 검정력 기준 바꾸기 (지원: alpha 0.05/0.01/0.10, power 0.80/0.90/0.95)
paperforge examples/sleep_moa_manifest.json --power 0.90

# 스프레드시트에서 내보낸 CSV/TSV 인벤토리도 그대로 입력 가능
paperforge examples/sleep_moa_manifest.csv

# 프로스펙티브 설계: 중도탈락 20% 가정 → 권장 "모집" N을 상향 보정
paperforge examples/sleep_moa_manifest.json --dropout 0.2

# 효과가 가정보다 30% 작다면? 권장 N이 어떻게 커지는지 (보수적 계획)
paperforge examples/sleep_moa_manifest.json --effect-scale 0.7
```

**옵션 요약**

| 옵션 | 설명 |
|------|------|
| `--top N` | 상위 N개만 출력 |
| `--out PATH` | Markdown 리포트 저장 (미지정 시 stdout) |
| `--csv PATH` | CSV 매트릭스 저장 |
| `--json PATH` | 구조화 JSON 저장 (프로그램 연동용, 전 필드 포함) |
| `--alpha` / `--power` | 검정력 기준 (지원: alpha 0.05/0.01/0.10, power 0.80/0.90/0.95) |
| `--dropout p` | 예상 중도탈락 비율(0≤p<1). 권장 모집 N=⌈권장 N/(1−p)⌉ 로 표시 |
| `--effect-scale S` | 가정 효과크기 배율(>0, 기본 1.0). `<1`이면 더 작은 효과를 가정해 권장 N↑ |

### 매니페스트 형식 (입력) — JSON 또는 CSV/TSV

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

**CSV/TSV 입력** — 엑셀/스프레드시트로 관리하는 데이터 인벤토리를 그대로 넣을 수 있습니다.
확장자(`.csv`/`.tsv`/`.json`)로 형식을 판별하고, 그 외 확장자는 내용으로 자동 감지합니다.

```csv
study,name,modality,n,variables,notes
내 코호트,MoA EEG,뇌파,92,delta_power;theta_power,안정상태
,스마트워치,워치,90,rmssd;sdnn,
```

- 헤더는 한/영 별칭을 인식합니다: `modality`/`모달리티`/`종류`, `n`/`표본수`, `variables`/`변수`,
  `sampling_hz`/`샘플링`, `name`/`이름`, `notes`/`비고`, `study`/`연구명`. **`modality` 열만 필수.**
- 한 셀에 여러 변수는 **`;` 또는 `|`** 로 구분합니다(쉼표는 CSV 구분자라 사용 불가).
- `study` 열의 첫 값이 연구명이 됩니다(없으면 파일명). 빈 `n`/`variables` 셀은 "미제공"으로 처리.
- 인코딩은 UTF-8을 권장하지만, 엑셀에서 자주 나오는 **CP949/EUC-KR** 파일도 자동 감지해
  읽습니다(경고 표시). `#`로 시작하는 줄과 빈 줄은 건너뜁니다.

### 민감도 분석 · 중도탈락 보정 (신규)

- **탐지가능 최소효과(MDES)**: 각 아이디어에 대해 "지금 가진 N으로 alpha/power 기준
  **검출 가능한 가장 작은 효과크기**"를 함께 보고합니다(예: `r≥0.29`, `d≥0.59`, `f²≥0.13` — 모두 N≈90 기준).
  (`d≥0.59`는 각 군을 반씩 나눈 총 N=90 기준.) 이는 검정력 공식의 **정확한 역산**이라
  권장 N과 상호일치합니다(권장 N에서 MDES를 역산하면 가정한 효과크기가 그대로 나옴).
  "표본 부족"을 실행 가능한 숫자로 바꿔 줍니다.
- **`--dropout p`**: 프로스펙티브 설계용. 분석에 필요한 N은 그대로 두고, **모집 목표 N**을
  `⌈권장 N/(1−p)⌉` 로 상향해 함께 표시합니다(실현가능성 판정 자체는 바꾸지 않음).
- **표본수 민감도 스트립**: 모든 실현가능성 판정은 템플릿에 내장된 **하나의** 효과크기 가정에
  달려 있습니다. 그래서 각 아이디어의 상세 블록에 효과가 가정보다 작을/클 때의 권장 N을
  함께 보여 줍니다 — 예: `보수적 N=194(효과 0.2) / 계획 N=85(효과 0.3) / 낙관적 N=37(효과 0.45)`.
  회의 전에 "가정이 낙관적이면 표본이 얼마나 더 필요한가"를 한눈에 봅니다.
- **`--effect-scale S`**: 위 스트립을 전역으로 고정. `0.7`이면 모든 효과크기를 30% 작게 가정해
  실현가능성까지 다시 판정합니다(더 보수적인 계획).

### 통계 모델링에 관한 정직성 노트

- **증분타당도(ΔR²) 검정**: "복합지수가 단일 모달리티보다 낫다", "공변량 보정 후 HRV가 예측한다"
  같은 가설은 전체 R²≠0 검정이 아니라 **추가 예측변수의 증분설명력** 검정입니다. 해당 아이디어는
  비중심 F의 분자 자유도를 *추가된 예측변수 수*로 두고 정확히 사이징합니다(전체 모델 df가 아님).
- **불균형 2군**: 반응자/비반응자처럼 군 크기가 다르면 표본수가 늘어납니다(30:70이면 약 1.2배).
  해당 템플릿에는 균형(50:50) 가정과 실제 비율 보정 계수가 주석으로 명시돼 있습니다.
- **접근 가능한 열**: 상세 블록의 "접근 가능한 열"은 그 조합의 **전체 변수 목록**이며, 가설의
  예측/결과 변수와 자동 매칭한 것이 아닙니다(라벨에 명시).

### 출력 예시 (발췌)

```
| # | 아이디어 | 모달리티 | 권장 N | 보유 N | 탐지가능 효과 | 실현가능성 | 적합 저널 |
|---|----------|----------|-------|-------|-------------|-----------|-----------|
| 1 | EEG·HRV·호흡 통합 각성지수 개발·검증 | EEG × 워치 × 호흡밴드 × 설문 | 68 | 90 | f²≥0.11 | 충분 가능 | IEEE JBHI / Sensors |
| 2 | EEG–호흡 위상결합과 수면 깊이 | EEG × 호흡밴드 × 설문 | 85 | 92 | r≥0.29 | 충분 가능 | Psychophysiology / J. Sleep Research |
| 7 | 야간 HRV 동태의 기술·군집 분석 | 워치 × 설문 | 비적용 | 90 | — | 탐색적 | Sensors / Sleep Health |
| 8 | MoA 반응자 프로파일링 | MoA × EEG × 워치 × 호흡 × 설문 | 126 | 90 | d≥0.59 | 표본 부족 우려 | Frontiers in Neuroscience |
```

`탐지가능 효과` 열은 보유 N으로 검출 가능한 **최소 효과크기(MDES)** 입니다 — "표본 부족"이라도
이 값보다 큰 효과라면 검출 가능하다는 뜻입니다.

표는 **실현가능성 우선**(충분 가능 → 탐색적 → 표본 부족)으로, 같은 등급 안에서는
멀티모달·변수 풍부 순으로 정렬됩니다. 각 아이디어는 아래 상세 블록에 가설·변수·권장
분석·신규성 메모·표본 경고까지 풀어 줍니다.

## Notes / limitations

- **아이디어 템플릿은 사람이 큐레이션한 지식베이스**(수면/각성 생리)입니다 — 망라가 아니라
  출발점입니다. 분야가 다르면 `paperforge/knowledge.py`에 템플릿을 추가하세요.
- **권장 N 계산 방식**: 상관은 Fisher-z, 독립 2군/대응표본은 정규근사(항상 올림),
  **다중회귀는 비중심 F 분포로 정확히** 계산합니다(SciPy 없이 stdlib로 구현, G*Power의
  "R² deviation from zero"와 일치 — 예: f²=0.15에서 예측변수 k=1·3·5 → N=55·77·92).
  상관/평균차의 정규근사는 두 군 비교에서 군당 약 1명 적게 나올 수 있으니, 최종 검정력은
  G*Power 등으로 확정하세요.
- 효과크기 가정은 각 템플릿에 보수적으로 내장돼 있습니다(예: 상관 r=0.30). 실제 예상
  효과가 더 작다면 권장 N은 더 커집니다.
- **탐지가능 최소효과(MDES)** 는 각 검정력 공식의 정확한 역산입니다: 상관은 `tanh`, 2군/대응은
  정규근사, 회귀는 비중심 F 곡선을 이분법으로 역산합니다. 권장 N과 상호일치하도록 설계돼
  있어(권장 N에서 MDES 역산 → 가정 효과크기 복원) 계획용 근사의 한계는 동일하게 적용됩니다.
- 설계에 맞춰 다른 공식을 씁니다: 상관/회귀/독립 2군은 각 닫힌형, **피험자내(반복측정)**
  설계는 paired 공식(더 적은 N), **탐색적(군집 등)** 설계는 고정 표본기준을 두지 않고
  "비적용/탐색적"으로 표시합니다.
- 외부 네트워크를 쓰지 않습니다(완전 오프라인). 표준 라이브러리만 사용.

## License

MIT © 2026 hyeonjoong
