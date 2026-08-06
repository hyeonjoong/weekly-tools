# rocdx — 진단정확도(ROC) 분석기

검사값 한 열과 기준 진단 한 열이 있는 **CSV를 넣으면**, ROC 곡선·AUC·절단점·민감도·
특이도·PPV/NPV·우도비를 **신뢰구간과 함께** 계산하고 **논문에 바로 붙일 문장**까지
출력합니다. 외부 라이브러리 없이 **표준 라이브러리만**으로 동작합니다.

```bash
python3 -m rocdx.cli examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis
```

```
  AUC = 0.944   95% CI [0.902, 0.969]  (logit 변환, DeLong SE = 0.0165)
  판별력 : 매우 우수 (outstanding) (Hosmer–Lemeshow 관례적 구간일 뿐 …)
  H0: AUC = 0.5 (동전 던지기와 같음) → p < 0.0001  [Mann-Whitney U, 동점 보정]

  ● Youden J 최대 (sensitivity + specificity - 1) [데이터에서 선택]
      절단점 (cut-off) : crp_mg_L >= 35.9
      2x2              : TP 56  FP 14  FN 6  TN 102
      민감도 Sens      : 90.3%  95% CI [80.5%, 95.5%]   (56/62)
      특이도 Spec      : 87.9%  95% CI [80.8%, 92.7%]   (102/116)
      LR+ / LR-        : 7.48 [4.55, 12.31]  /  0.11 [0.05, 0.24]
```

---

## 목적 / Why this exists

**한국어.** 새 바이오마커·검사·설문·기기 신호가 "질환을 얼마나 잘 가려내는가"를
보고하려면 AUC 하나로는 부족합니다. 심사자와 규제기관이 실제로 묻는 것은
**절단점이 무엇이고, 그 절단점에서 민감도·특이도가 얼마이며, 신뢰구간이 얼마나
넓은가**입니다. 그리고 여기에는 손으로 하면 거의 항상 틀리는 지점들이 있습니다.

- **절단점을 그 데이터에서 고르면 성능이 부풀려집니다.** Youden 최대점의 민감도
  95%는 다른 표본에서 그대로 재현되지 않습니다. rocdx는 이 사실을 보고서에
  **명시하고**, `--bootstrap` 으로 **부풀림(낙관, optimism)의 크기를 수치로**
  보여줍니다.
- **PPV/NPV는 유병률에 통째로 좌우됩니다.** 민감도 90%·특이도 90% 검사도
  유병률 1%에서는 PPV가 8%입니다. `--prevalence` 로 대상 인구집단 기준의
  PPV/NPV를 베이즈 정리로 다시 계산합니다.
- **두 검사의 AUC 비교는 같은 대상에서 측정했다면 짝지은 검정이 맞습니다.**
  `--compare` 는 DeLong 짝지은 검정을 씁니다(독립 표본 취급보다 훨씬 민감).
- **임상 CSV는 지저분합니다.** BOM·CP949·세미콜론 구분자·`N/A`·`미측정`·
  `<0.05`(검출한계)·`1,024.0`(천단위 쉼표)를 그대로 읽고, **무엇을 몇 건 버렸는지
  보고서 첫머리에 밝힙니다.**
- **낮을수록 나쁜 지표**(인지검사 점수, 폐활량 등)는 `--direction lower` 로
  다루며, 절단점은 **원래 단위 그대로** `인지검사점수 <= 22` 형태로 출력합니다.

**English.** `rocdx` turns a two-column CSV (an index-test value and a reference
diagnosis) into a full diagnostic-accuracy report: empirical ROC, AUC with a
DeLong interval, cut-off selection, and sensitivity / specificity / PPV / NPV /
likelihood ratios / diagnostic odds ratio with confidence intervals — plus a
ready-to-adapt results paragraph. It is deliberately blunt about the two things
that are most often overclaimed in this literature: a cut-off chosen on the same
data is optimistic (it quantifies the optimism by bootstrap), and PPV/NPV are
prevalence-dependent (it recomputes them at a prevalence you state).

**Who it's for.** 임상·제약·의료기기 연구자, 진단 성능을 보고해야 하는 대학원생,
검증(validation) 논문·STARD 표를 준비하는 사람. Clinical / pharma researchers who
must report and defend diagnostic accuracy numbers.

---

## 무엇을 계산하나 / What it computes

| 항목 | 내용 |
|---|---|
| ROC 곡선 | 경험적(비모수) 곡선의 **모든 절단점**, 동점(tie)은 하나의 점으로 정직하게 처리 |
| AUC | 곡선아래면적 + **DeLong 표준오차**, logit 변환 신뢰구간(기본) 또는 Wald. 신뢰수준은 `--alpha` 로 조정되며 출력 라벨도 함께 바뀝니다 |
| AUC 검정 | H0: AUC=0.5 에 대한 **Mann–Whitney U** (동점 보정, 연속성 보정) |
| 두 검사 비교 | **DeLong 짝지은 검정**(같은 대상) — 차이·신뢰구간·z·p (분산을 추정할 수 없으면 p를 내놓지 않고 그렇다고 말합니다). 비교가 2건 이상이면 **Holm 다중비교 보정 p**도 함께 |
| 비열등성 | `--ni-margin` — 사전에 정한 AUC 한계에 대한 **단측 비열등성 검정**(차이 신뢰구간 하한 기준). 비열등/우월/입증실패를 구분해 서술 |
| 부분 AUC | `--pauc-min-spec` — 실제로 쓰는 구간(예: 특이도 0.9~1.0)만 적분한 **pAUC** + **McClish 표준화값**, 부트스트랩 백분위 구간. 표준화값은 위로 1이 상한이지만 아래로는 경계가 없어 우연보다 나쁜 구간에서는 음수가 나올 수 있고, 그때는 논문 문장 초안이 성능 주장을 거부합니다 |
| 군집 자료 | `--cluster-col` / `--cluster` — 한 환자가 여러 행(병변·반복측정)을 내는 자료에서 **군집 단위 부트스트랩** AUC 구간. 지정만 해도 중복 ID를 찾아 경고 |
| 절단점 선택 | Youden J 최대, 좌상단(0,1) 최근접, **특이도 하한**·**민감도 하한** 조건, 사용자 지정 절단점 |
| 절단점별 지표 | 민감도·특이도·PPV·NPV·정확도·균형정확도 (**Wilson 구간**), LR+·LR− (Simel 로그척도 구간, 셀이 0이면 0.5 보정), 진단오즈비 (Haldane 보정) |
| 유병률 보정 | `--prevalence` 로 대상 인구집단 기준 PPV/NPV 재계산 |
| 낙관 추정 | `--bootstrap` — **절단점 선택 자체를 부트스트랩**하여, 재선택한 절단점을 원자료에 적용한 분포로 구간을 만들고 낙관(optimism)의 크기를 추정 (제거하지는 않음) |
| 출력 | 텍스트 보고서 · ASCII ROC 곡선 · 마크다운 표(`--markdown`) · **SVG 그림**(`--plot-svg`) · **JSON**(`--json`, `-` 이면 표준출력) · 절단점 전체 CSV(`--points-csv`) · 한국어/영어 논문 문장 |

모든 통계량은 표준 라이브러리로 직접 구현했습니다(정규 분위수, Wilson 구간,
mid-rank 기반 DeLong 분산). 검증 방식은 `tests/` 를 보세요 — DeLong 분산은 O(n²)
정의식과, AUC는 완전탐색·사다리꼴 면적과, Wilson 구간은 공표된 값과 대조합니다.

---

## 하지 않는 것 / Honest limits

이 도구가 **하지 않는** 것을 분명히 적어 둡니다. 아래 항목이 필요한 연구라면
rocdx의 숫자를 그대로 쓰면 안 됩니다.

- **절단점을 그 자료에서 고르면 성능은 부풀려집니다.** `--bootstrap` 의 낙관 보정은
  그 크기를 **추정**할 뿐 제거하지 않습니다(내부검증). 진짜 검증은 독립 표본입니다.
- **경험적(비모수) ROC만** 계산합니다. 이항정규(binormal) 모형 적합과 곡선
  평활화는 제공하지 않습니다. 부분 AUC는 사다리꼴(경험적) 적분이며, 구간 경계가
  관측된 절단점 사이에 떨어지면 **선형 보간**합니다 — 비질환군이 적으면 구간이
  몇 개의 계단으로만 결정되므로 경고가 뜹니다. pAUC에는 **해석적 신뢰구간이
  없어** `--bootstrap` 백분위 구간만 제공하고, DeLong 방식의 pAUC 차이 검정은
  하지 않습니다.
- **한 행 = 한 관측**이며, 기본 신뢰구간은 행끼리 **독립**이라고 가정합니다.
  같은 환자가 여러 행을 내는 자료(병변별·반복측정)라면 `--cluster-col` 로 단위를
  알려 주세요: 중복이 있으면 경고하고, `--cluster --bootstrap N` 으로 **군집 단위
  부트스트랩** 구간을 함께 계산합니다. 다만 군집 보정은 **AUC와 pAUC 구간에만**
  적용되며, 절단점별 민감도·특이도의 Wilson 구간과 DeLong 비교 검정은 여전히
  독립 가정입니다.
- 독립 가정이 **어느 쪽으로 틀리는지는 설계에 따라 다릅니다.** 한 단위의 행들이
  같은 결과를 공유하는 반복측정 자료에서는 DeLong 구간이 **좁아지고**(모의실험에서
  95% 구간의 실제 포함률 83%), 한 단위가 질환·비질환을 하나씩 내는 짝지은 설계
  (양쪽 눈, 짝지은 대조)에서는 오히려 **넓어집니다**(포함률 100%). 도구는 자료를 보고
  어느 상황인지 판정해 경고 문구를 바꾸며, 군집 부트스트랩은 두 경우 모두에서
  95%에 가깝습니다(94~96%).
- **완전 사례 분석(complete case)** 입니다. 결측은 대치(imputation)하지 않고
  행을 제외하며, 제외 건수와 사유를 보고서에 표시합니다. 결측이 무작위가 아니면
  편향이 남습니다.
- **검증 편향(verification/partial verification bias), 불완전 기준검사
  (imperfect gold standard), 스펙트럼 편향**을 보정하지 않습니다. 기준 진단은
  참으로 간주합니다.
- **공변량 보정, 로지스틱 모형, 다변량 예측모형 개발**은 범위 밖입니다
  (rocdx는 이미 존재하는 점수 한 열을 평가합니다).
- AUC·비교·비열등성의 p값과 신뢰구간은 **정규근사**에 기반합니다(부분 AUC와 군집
  보정 구간은 부트스트랩 백분위 구간이라 정규근사를 쓰지 않습니다). 한 군의 사례가 10명 미만이면
  경고가 뜨고, 그때 숫자는 참고용으로만 보세요.
- **의료기기가 아니며 개별 환자의 진료 판단에 사용해서는 안 됩니다.** 연구용
  분석 도구입니다.
- **다중비교 보정은 `--compare` 를 2개 이상 지정한 AUC 비교에만** 적용됩니다
  (Holm). 여러 절단점 규칙을 함께 보거나, 여러 하위군을 따로 돌려 보거나, 부분
  AUC와 전체 AUC를 함께 보고할 때의 보정은 하지 않으므로 사용자가 계획해야 합니다.
- **비열등성 한계(`--ni-margin`)는 사용자가 사전에 임상적으로 정해야 합니다.**
  도구는 한계의 타당성을 검증할 수 없고, 자료를 본 뒤 고른 한계는 아무것도
  증명하지 못합니다. 검정은 정규근사 단측 검정이며(단측 α = `--alpha`/2),
  비열등성이 성립해도 **우월성을 뜻하지 않습니다.**
- 검출한계 표기(`<0.05`, `>100`)는 **한계값 자체로 대체**합니다. LOD/2·LOD/√2 대치나
  중도절단(censored) 모형은 쓰지 않으므로, 한계 미만/초과 값이 많으면 한계값에 동점이
  몰려 AUC와 절단점이 왜곡됩니다. 상한(`>`, `≥`)도 같은 방식으로 처리됩니다.
- 퍼센트 표기(`12%`)가 있는 열은 **0~1 비율로 변환**되어 절단점도 0~1 척도로
  출력됩니다(변환 건수는 보고서에 표시). 한 열에 `12%`와 `91`이 섞여 있으면 단위가
  100배 어긋나므로 경고를 띄우고, 그 결과는 신뢰할 수 없습니다.
- `--positive-label` 만 지정하면 **그 값이 아닌 모든 값**(판정보류·불확정 포함)이
  비질환군이 됩니다. 건수를 보고서에 표시하지만, 제외하려면 `--negative-label` 도
  함께 지정해야 합니다.
- 부트스트랩은 **질환군/비질환군을 각각 층화 재표본**합니다(환자-대조 설계에 맞음).
  연속 등록 코호트에서는 표본 유병률이 고정되므로 불확실성이 약간 과소평가됩니다.
- **부트스트랩은 큰 자료에서 오래 걸립니다.** 재표본마다 곡선을 다시 만들기 때문에
  대략 `행 수 × 반복 수`에 비례합니다(20만 행 × 200회 ≈ 3분, × 2000회 ≈ 30분).
  절단점이 여러 개여도 재표본은 한 번만 뽑아 공유하므로 규칙을 추가해도 느려지지
  않습니다. 큰 자료에서는 `--bootstrap 200` 정도로 먼저 확인하세요.
- **군집 부트스트랩은 군집 수가 적으면 신뢰할 수 없습니다.** 20개 미만이면 경고하고,
  모든 재표본이 같은 AUC를 주는 경우(예: 군집 2개)에는 폭 0인 구간을 만들지 않고
  구간 자체를 표시하지 않습니다.
- 절단점 선택 규칙(Youden 등)은 **오분류 비용이 같다고 가정**합니다. 위양성과
  위음성의 임상적 대가가 다르면 `--min-spec` / `--min-sens` / `--cutoff` 로 직접
  요구조건을 주는 편이 낫습니다.

---

## 설치 / Install

설치 없이 바로 실행할 수 있습니다(Python 3.9+ 만 있으면 됩니다).

```bash
cd ~/Downloads/02_프로젝트/깃헙/rocdx-진단정확도분석
python3 -m rocdx.cli --help
```

콘솔 명령(`rocdx`)으로 쓰고 싶으면:

```bash
python3 -m pip install -e .
rocdx --help
```

macOS에서는 **`실행.command` 를 더블클릭**하면 번들 예제로 전체 기능이 시연됩니다.

---

## 사용법 / Usage

```bash
# 0) 열 이름을 모를 때
python3 -m rocdx.cli 내파일.csv --list-columns

# 1) 기본 — 검사값 열과 기준 진단 열만 지정
python3 -m rocdx.cli 내파일.csv --score crp_mg_L --truth sepsis

# 2) 결과 열이 '양성/음성'이 아닌 값일 때
python3 -m rocdx.cli 내파일.csv --score 점수 --truth 판정 --positive-label 재발

# 3) 낮을수록 질환인 지표 (인지검사 점수, 폐활량 …)
python3 -m rocdx.cli 내파일.csv --score mmse --truth dementia --direction lower

# 4) 임상 요구조건이 있을 때 — 특이도 95% 이상에서 가장 민감한 절단점
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --min-spec 0.95

# 5) 이미 정해진 절단점의 성능 (여러 개 가능)
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --cutoff 10 --cutoff 20

# 6) 선별검사 상황 — 대상 인구집단 유병률 2% 기준 PPV/NPV
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --prevalence 0.02

# 7) 절단점 선택의 낙관(optimism)까지 부트스트랩으로 추정
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --bootstrap 2000

# 8) 같은 환자에서 잰 두 검사 비교 (DeLong 짝지은 검정)
python3 -m rocdx.cli 내파일.csv --score pct --truth sepsis --compare crp

# 9) 논문/발표용 산출물
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --markdown
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --points-csv points.csv
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --plot-svg roc.svg

# 10) 실제로 쓰는 구간만 평가 — 특이도 0.90~1.00 부분 AUC (+ 부트스트랩 구간)
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis \
    --pauc-min-spec 0.90 --bootstrap 2000

# 11) 비열등성 — 새 검사가 기존 검사보다 AUC 0.05 이상 나쁘지 않은가
python3 -m rocdx.cli 내파일.csv --score new --truth sepsis \
    --compare crp --ni-margin 0.05

# 12) 한 환자가 여러 행(병변·반복측정)을 내는 자료 — 군집 보정 구간
python3 -m rocdx.cli 내파일.csv --score score --truth 조직검사 \
    --cluster-col 환자ID --cluster --bootstrap 2000

# 13) 다른 프로그램에 넘기기 — JSON (파이프로 쓰려면 --json -)
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --json out.json
python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --json - | jq .auc.estimate
```

자세한 한국어 안내는 [`사용법.md`](사용법.md) 를 보세요.

### 입력 형식

한 행이 한 대상이고, 최소 두 개의 열이 있으면 됩니다.

```csv
patient_id,crp_mg_L,procalcitonin_ng_mL,sepsis
P0001,82.4,3.11,Yes
P0002,9.6,0.08,No
P0003,<0.05,N/A,No
```

- 결과(진단) 열: `1/0`, `Yes/No`, `양성/음성`, `case/control`, `질환/정상`,
  `있음/없음` 등을 자동 인식합니다. 그 밖의 값은 `--positive-label` 로 알려 주세요
  (그 값이 아닌 **나머지는 모두 비질환군**이 되므로, 판정보류를 빼려면
  `--negative-label` 도 함께 지정하세요).
- 검사값 열: 숫자, 천단위 쉼표(`1,024.0`), 퍼센트(`12%`), 검출한계(`<0.05`)를
  읽습니다. 검출한계는 **한계값 자체로 대체**하고 보고서에 건수를 표시합니다.
- 인코딩(UTF-8/BOM/CP949/EUC-KR)과 구분자(`,` `;` `tab` `|`)는 자동 판별하며,
  `--encoding` / `--sep` 로 강제할 수 있습니다. 보고서 첫머리에 무엇으로 읽었는지
  표시합니다.
- 쉼표는 열 단위로 해석합니다: 세 자리씩 묶여 있으면 천단위 구분자(`1,024.0`),
  그렇지 않으면 유럽식 소수점(`1,06` → 1.06). 후자로 판단하면 보고서에 명시합니다.
- `--list-columns` 는 기본적으로 **열 이름만** 보여 줍니다(환자정보가 화면·로그에
  남지 않도록). 값 미리보기가 필요하면 `--show-samples` 를 붙이세요(8자까지).

### 번들 예제

| 파일 | 내용 |
|---|---|
| `examples/sepsis_biomarker.csv` | 합성 데이터 180명. CRP·프로칼시토닌·WBC 세 지표, `Yes/No` 결과, 결측·`N/A`·`<0.05`·천단위 쉼표가 섞여 있음 |
| `examples/cognitive_screen_kr.csv` | 합성 데이터 150명. **CP949 인코딩 + 세미콜론 구분자 + 한글 열 이름**, 인지검사 점수(낮을수록 질환) |
| `examples/lesion_multi_reader.csv` | 합성 데이터 92병변 / 42명. 한 환자가 최대 4개 병변을 내는 **군집 자료** — `--cluster-col 환자ID` 시연용 |

세 파일 모두 **난수로 만든 합성 데이터**이며 실제 환자 정보가 아닙니다.

---

## 개발 / Tests

```bash
python3 -m pytest
```

`tests/` (291개)는 통계 핵심을 실제로 검증합니다: DeLong 분산 대 O(n²) 정의식,
AUC 대 완전탐색·사다리꼴 면적, 신뢰구간의 몬테카를로 포함률, Wilson 구간의 공표값,
Simel 0.5 보정, 유병률 가정 PPV/NPV, 부트스트랩 백분위수 순서통계량, 지저분한 CSV
파싱(인코딩·구분자·쉼표 해석·퍼센트 혼용), 절단점 선택의 낙관 편향(잡음 지표에서
J>0이 사라지는지), `--alpha`/`--direction`/`--compare` 등 CLI 옵션이 실제로 결과를
바꾸는지, 보고서가 모든 경고를 실제로 출력하는지, 그리고 오류 메시지가 원자료를
그대로 되뱉지 않는지.

새로 추가된 기능도 같은 기준으로 검증합니다: 부분 AUC는 손으로 계산한 곡선과
대조하고 `pAUC(0,t) + pAUC(t,1) = AUC` 항등식을 확인하며, Holm 보정은 손계산 예제와,
군집 부트스트랩은 **행을 그대로 복제해도 구간이 좁아지지 않는지**(독립 가정 구간은
좁아집니다)로, JSON은 NaN/Infinity를 절대 내보내지 않는지와 방향이 뒤집힌 절단점이
원래 단위로 나오는지로, SVG는 XML 파싱과 열 이름 이스케이프로 검증합니다.

적대적 리뷰와 수정 이력은 [`HARDENING.md`](HARDENING.md) 에 있습니다.

## License

MIT
