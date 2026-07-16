# pubgap — 논문 주제·연구공백 탐색기

키워드 하나로 **PubMed 최근 동향**을 요약하고, 아직 **덜 연구된 각도(연구공백)** 를
논문 주제 후보로 제안합니다. 연도별 발행량 추세, 주요 저널·주제(MeSH), 최근 부상/쇠퇴
주제, 그리고 "개별적으로는 흔한데 함께는 거의 안 다뤄진" 저조 조합을 찾아 줍니다.

## 목적 / Why this exists

**한국어** — 새 논문 주제를 찾을 때 가장 힘든 일은 "이미 다 나온 얘기 아닌가?"와
"그럼 아직 비어 있는 각도는 뭐지?"를 가려내는 것입니다. 이 도구는 관심 키워드로
PubMed를 훑어 (1) 이 분야가 커지는지/식는지, (2) 요즘 뜨는 세부 주제가 무엇인지,
(3) **두 개의 인기 주제가 개별적으로는 많이 연구됐지만 서로 결합해서는 거의 연구되지
않은 조합(=연구공백)** 을 자동으로 짚어 줍니다. 임상·제약 연구자가 다음 논문거리를
빠르게 스캔하고, 리뷰어에게 "왜 이 주제인가"를 근거(문헌 통계)로 설명할 때 씁니다.
회사(BELL) 맥락에서는 수면·호흡·HRV·EEG처럼 보유 모달리티를 결합한 미개척 조합을
발굴하는 데 특히 유용합니다.

**English** — The hard part of finding a new paper topic is separating "hasn't this all
been done?" from "so what angle is still open?". Give pubgap a keyword and it scans
PubMed to show (1) whether the field is growing or cooling, (2) which subtopics are
rising lately, and (3) **pairs of individually-popular topics that are rarely studied
*together* — under-researched combinations that make natural paper angles.** Built for a
clinical/pharma researcher scanning for the next manuscript and needing evidence (not a
hunch) for "why this topic." For BELL's work it is well-suited to surfacing unexplored
combinations across its own modalities (sleep, respiration, HRV, EEG).

**이 도구가 하는 것 / What it computes**
- **동향(trend)**: 연도별 발행 편수, 초기 대비 최근 성장, 연평균 성장률(CAGR), 그리고
  **Mann–Kendall 단조추세 검정**(발행량이 시간에 따라 *통계적으로 유의하게* 늘/줄고 있는지).
- **지형(landscape)**: 주요 저널, 주요 주제(MeSH descriptor)별 논문 수.
- **부상/쇠퇴**: 전체 기간을 초기/최근 두 구간으로 나눠, 각 주제의 '비중' 변화를 계산.
- **연구공백(gap)**: 빈출 상위 주제쌍의 **관측 동시등장 vs 기대(독립가정) 비율(lift)** 을
  계산해, lift가 낮은(=기대보다 훨씬 덜 엮인) 조합을 미개척 각도로 제안. 각 조합에는
  초기하검정 p-value 와, **여러 쌍을 동시에 검정한 것을 보정한 BH-FDR q-value** 를 함께 보고.
  또한 각 공백에 **대표 PMID**(A만/B만/함께 다룬 논문 — 바로 원문 확인용)와 **Swanson ABC
  가교 주제 C**(A·B 각각과는 자주 엮이지만 A–B 자체는 드문 제3의 주제)를 제시.
- **잡음 제거**: PubMed 가 임상 논문 대부분에 자동으로 붙이는 **체크 태그**(Humans/Male/
  Female/Adult/Aged/Animals…)는 연구 주제가 아니므로 **기본적으로 제외**(옵션으로 포함 가능).

> 공백 탐색의 근거: 연관규칙의 **lift**, 그리고 문헌기반발견(Literature-Based Discovery,
> Swanson ABC) 계열의 아이디어입니다. AB 공백(둘이 함께 드묾)에 더해, ABC **가교 주제** C
> 를 찾아 "A는 C를 통해 B와 연결된다"는 기전 서사를 제안합니다. 다만 문헌 구조상의 빈자리를
> 후보로 제시할 뿐 인과·타당성을 보장하지는 않으니(아래 한계), 제시된 대표 PMID 로 반드시
> 원문을 확인하세요. 여러 주제쌍을 한꺼번에 보므로 유의성은 raw p 대신 **q(FDR) ≤ 0.05** 로.

**입력/출력 형식**
- 입력: PubMed **efetch XML**, **MEDLINE/NBIB**(PubMed 웹의 *Save → PubMed format* 또는 인용
  관리자 내보내기), 그리고 이들의 **.gz** 압축본을 자동 판별합니다. UTF-8/latin-1 을 관대하게
  디코드하고, 같은 PMID 는 자동 중복 제거합니다.
- 출력: **Markdown**(기본), **JSON**(`--format json`), **CSV**(`--format csv`, 공백 후보 표 —
  엑셀 한글 대비 UTF-8 BOM 포함).

## Install

```bash
cd pubgap-논문공백탐색
python3 -m pip install -e .        # 순수 표준 라이브러리 — 외부 의존성 없음
# 또는 설치 없이 바로:  python3 -m pubgap.cli ...
```

의존성이 **전혀 없습니다**(urllib·xml 등 표준 라이브러리만 사용).

## Usage

```bash
# 1) 오프라인 데모 — 번들 예시(수면/호흡/HRV/EEG 18편)로 즉시 확인
pubgap --from-file examples/sleep_pubmed.xml

# 2) 실제 PubMed 조회 (네트워크) — 최근 10년, 최대 300편
pubgap "slow breathing AND sleep" --years 10 --email you@lab.org

# 3) 결과를 파일로 + 나중에 재분석하려 원본 XML 저장
pubgap "hearing loss AND cognitive decline" --out gap.md --save-xml raw.xml

# 4) JSON / CSV 로 (파이프라인/스프레드시트용)
pubgap --from-file examples/sleep_pubmed.xml --format json
pubgap --from-file examples/sleep_pubmed.xml --format csv --out gaps.csv

# 5) 내려받은 NBIB(MEDLINE) 또는 .gz 파일 그대로 분석 (형식 자동 판별)
pubgap --from-file my_export.nbib
pubgap --from-file pubmed_result.xml.gz

# 6) 대표(별표) 주제만 / 통계적으로 유의한 공백만 / 연도 범위
pubgap --from-file examples/sleep_pubmed.xml --major-topics-only --gap-max-q 0.05
pubgap "slow breathing AND sleep" --min-year 2018 --include-keywords --email you@lab.org
```

주요 옵션:
- 입력: `--from-file`(XML/NBIB/.gz 자동판별), `--years N`, `--max-records M`,
  `--min-year/--max-year`(연도 범위, 미상 제외), `--save-xml`, `--email/--api-key`.
- 주제 처리: `--major-topics-only`(MeSH 대표주제만), `--include-keywords`(저자 키워드 보강 —
  MeSH 미부여 최신 논문 대비), `--include-check-tags`(체크 태그도 포함, 기본은 제외).
- 공백 기준: `--gap-min-expected`(최소 기대 동시등장), `--gap-max-lift`(최대 lift),
  `--gap-max-q`(최대 BH-FDR q — 유의한 공백만), `--gap-top-k`, `--no-bridges`(가교 계산 끔).
- 표시/출력: `--top-mesh/--top-journals`, `--format {md,json,csv}`(`--json` 은 구버전 별칭), `--out`.

### 출력 예시 (번들 **합성** 예시: 수면·호흡·HRV·EEG 18편)

> ⚠️ `examples/sleep_pubmed.xml` 는 **실제 논문이 아니라 합성 데이터**입니다(PMID·제목 가짜).
> 오프라인에서 도구 동작을 보여주기 위한 것이며, 아래 숫자는 실제 문헌 통계가 아닙니다.

```
# 연구 동향·공백 리포트 — `examples/sleep_pubmed.xml`

- 분석 논문: **18편** (MeSH 주제어 보유 18편) · 발행연도 2015–2024
- 발행량: **2020년 이후**가 전체의 **50%** (그 이전 대비 1.00배) · 연평균 +0%
- 추세 검정(Mann–Kendall): **뚜렷한 추세 없음** (τ=+0.00, p=1.000, n=10년)

## 주요 주제 (MeSH, 논문 수)
- Sleep — 9
- Heart Rate — 8
- Respiration — 8
- Electroencephalography — 6
- Autonomic Nervous System — 5
...

## ↗︎ 최근 부상 주제 (비중 상승)
| 주제 | 초기 | 최근 | 비중변화 |
|---|---:|---:|---:|
| Acoustic Stimulation | 0 | 2 | +22%p |
| Sleep | 4 | 5 | +11%p |
...

## 🔍 덜 연구된 각도 (저조 조합 = 연구공백 후보)
| 주제 A | 주제 B | 함께(관측) | 기대 | lift | p | q(FDR) |
|---|---|---:|---:|---:|---:|---:|
| Heart Rate | Electroencephalography | 0 | 2.7 | 0.00 | 0.011 | 0.034 |
| Respiration | Electroencephalography | 0 | 2.7 | 0.00 | 0.011 | 0.034 |
| Sleep | Heart Rate | 1 | 4.0 | 0.25 | 0.008 | 0.034 |
| Sleep | Autonomic Nervous System | 1 | 2.5 | 0.40 | 0.147 | 0.331 |

> 제안: **Heart Rate × Electroencephalography** 를 결합한 분석/논문을 검토하세요.
> 관련 논문 각각 8·6편이 있으나 둘을 함께 다룬 논문은 0편뿐입니다(기대 2.7편, p=0.011, q=0.034).

> 가교(Swanson ABC): Heart Rate 와 Electroencephalography 를 잇는 제3 주제
> → **Sleep**(A&C 1·C&B 5), **Autonomic Nervous System**(A&C 3·C&B 1) …

> 대표 PMID(확인용) — Heart Rate: 30000001, 30000004, 30000006 ·
> Electroencephalography: 30000002, 30000005, 30000009
```

> ※ `examples/sleep_pubmed.xml` 는 체크 태그(Humans/Male/…)를 넣지 않은 합성 데이터라
> 필터 효과가 안 보입니다. 실제 PubMed 조회에서는 이 태그들이 자동 제거됩니다.

이 **합성** 예시는 도구가 어떻게 저조 조합을 짚는지 보여주려고 EEG가 호흡/심박과 함께
나오지 않게 일부러 구성했습니다(그래서 EEG×HRV, EEG×호흡이 상위 공백으로 나옵니다). 실제
PubMed에서는 이 조합들이 함께 색인되는 경우가 많으므로, 이 데모의 공백은 **예시일 뿐** 실제
문헌 공백이 아닙니다. 다만 "보유 모달리티(EEG·호흡·HRV)를 결합한 조합을 훑어 준다"는 **활용
방식**은 BELL-001 맥락에서 그대로 유효합니다 — 실제 키워드로 돌려 확인하세요.

## 어떻게 계산하나 (요약)

| 항목 | 계산 |
|---|---|
| 부상/쇠퇴 | 기간을 `(최소연도+최대연도+1)//2` 기준 초기/최근으로 나눠, 주제별 `최근비중−초기비중` |
| CAGR | 조밀 시계열 양끝 해로 `(끝해/첫해)^(1/연수) − 1` (첫/끝해 0 이면 미표시) |
| 추세 검정 | **Mann–Kendall**: 조밀 연도 시계열(빠진 해=0)에 대해 `S`, 동률보정 `τ(tau-b)`, 정규근사 `z`(연속성 보정), 양측 `p` |
| 기대 동시등장 | `count(A)·count(B) / N` (독립 가정) |
| lift | `관측 동시등장 / 기대` — 낮을수록 미개척 |
| p-value | 초기하분포 하단꼬리 `P(X ≤ 관측)` — 우연히 이만큼 덜 엮일 확률(작을수록 유의) |
| q-value | 검정한 **모든** 후보쌍(기대≥min_expected)에 **Benjamini–Hochberg FDR** 적용 — 다중검정 보정 |
| 공백 필터 | `기대 ≥ --gap-min-expected` 이고 `lift ≤ --gap-max-lift`(옵션 `q ≤ --gap-max-q`) 인 조합만 |
| 가교(ABC) | 각 공백 A–B 에 대해, A·B 각각과 함께 등장한 제3 주제 C 를 `min(A&C수, C&B수)` 로 순위 |
| 체크 태그 | Humans/Male/Female/Adult/Aged/Animals… 등 색인용 태그를 주제 분석에서 기본 제외 |

> 통계 구현은 순수 표준 라이브러리입니다. 기본 테스트는 scipy 없이 **독립 손계산/브루트포스**로
> 검증하며(`tests/test_stats.py`), scipy 가 설치돼 있으면 `tests/test_scipy_crosscheck.py` 가
> `benjamini_hochberg`·`mann_kendall`·`hypergeom_lower_tail` 을 각각 `scipy.stats` 의
> `false_discovery_control`·`kendalltau`·`hypergeom` 과 대조합니다(없으면 자동 skip).

## 한계 / Limitations
- **MeSH 의존**: 공동출현 분석은 PubMed가 색인한 MeSH 주제어에 기반합니다. 아주 최신
  논문은 MeSH가 아직 안 붙어 있을 수 있어(그 논문은 주제 통계에서 빠짐), `--years`를
  넉넉히 두거나 `--include-keywords`로 저자 키워드를 보완하세요.
- **동의어/상하위어 미통합**: `Sleep` 과 `Sleep, REM` 처럼 뜻이 겹치는 descriptor 를 서로
  다른 주제로 셉니다(MeSH 트리를 쓰지 않는 무의존 설계의 대가). 이는 공백을 실제보다
  부풀릴 수 있으니, 제시된 **대표 PMID** 로 원문을 확인해 걸러내세요.
- **탐색적 신호**: "공백"은 문헌 부재의 통계적 신호일 뿐, 그 조합이 반드시 새롭고 타당함을
  뜻하지 않습니다(용어가 다르게 색인됐거나, 임상적으로 무의미한 조합일 수 있음).
  착수 전 상위 조합의 대표 논문을 반드시 직접 확인하세요. 가교(ABC) 역시 가설 출발점일 뿐입니다.
- **부상/쇠퇴는 비중 변화**: 초기/최근 '비중' 차이일 뿐 유의성 검정이 아니며, 특히 편수가
  적은 주제(예: 0→2편)는 잡음일 수 있으니 표의 초기/최근 실제 편수를 함께 보세요.
- **표본 상한**: 기본 최대 300편만 가져옵니다(`--max-records`로 조정). 매우 큰 분야는
  esearch 정렬(최신순) 상 최근 편향이 생길 수 있습니다.
- NCBI E-utilities 예절을 위해 `--email`(가능하면 `--api-key`) 지정을 권장합니다.

## License
MIT © 2026 hyeonjoong
