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
- **동향(trend)**: 연도별 발행 편수 + 초기 대비 최근 성장.
- **지형(landscape)**: 주요 저널, 주요 주제(MeSH descriptor)별 논문 수.
- **부상/쇠퇴**: 전체 기간을 초기/최근 두 구간으로 나눠, 각 주제의 '비중' 변화를 계산.
- **연구공백(gap)**: 빈출 상위 주제쌍의 **관측 동시등장 vs 기대(독립가정) 비율(lift)** 을
  계산해, lift가 낮은(=기대보다 훨씬 덜 엮인) 조합을 미개척 각도로 제안.

> 공백 탐색의 근거: 연관규칙의 **lift**, 그리고 문헌기반발견(Literature-Based Discovery,
> Swanson ABC) 계열의 아이디어입니다. "개별적으로 흔한 A·B가 함께는 드물다"는 문헌
> 구조상의 빈자리를 후보로 제시할 뿐, 인과·타당성을 보장하지는 않습니다(아래 한계 참고).

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

# 4) JSON 으로 (파이프라인/추가 분석용)
pubgap --from-file examples/sleep_pubmed.xml --json
```

주요 옵션: `--years N`(최근 N년), `--max-records M`(최대 편수), `--from-file`(오프라인 XML),
`--save-xml`(원본 저장), `--email/--api-key`(NCBI 예절/rate limit), `--gap-min-expected`(공백
기준: 최소 기대 동시등장), `--gap-max-lift`(공백 기준: 최대 lift), `--top-mesh/--top-journals`,
`--json`, `--out`.

### 출력 예시 (번들 **합성** 예시: 수면·호흡·HRV·EEG 18편)

> ⚠️ `examples/sleep_pubmed.xml` 는 **실제 논문이 아니라 합성 데이터**입니다(PMID·제목 가짜).
> 오프라인에서 도구 동작을 보여주기 위한 것이며, 아래 숫자는 실제 문헌 통계가 아닙니다.

```
# 연구 동향·공백 리포트 — `examples/sleep_pubmed.xml`

- 분석 논문: **18편** (MeSH 주제어 보유 18편) · 발행연도 2015–2024
- 발행량: **2020년 이후**가 전체의 50% (그 이전 대비 1.00배)

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
| 주제 A | 주제 B | 함께(관측) | 기대 | lift | p |
|---|---|---:|---:|---:|---:|
| Heart Rate | Electroencephalography | 0 | 2.7 | 0.00 | 0.011 |
| Respiration | Electroencephalography | 0 | 2.7 | 0.00 | 0.011 |
| Sleep | Heart Rate | 1 | 4.0 | 0.25 | 0.008 |

> 제안: **Heart Rate × Electroencephalography** 를 결합한 분석/논문을 검토하세요.
> 관련 논문 각각 8·6편이 있으나 둘을 함께 다룬 논문은 0편뿐입니다(기대 2.7편, p=0.011).
```

이 **합성** 예시는 도구가 어떻게 저조 조합을 짚는지 보여주려고 EEG가 호흡/심박과 함께
나오지 않게 일부러 구성했습니다(그래서 EEG×HRV, EEG×호흡이 상위 공백으로 나옵니다). 실제
PubMed에서는 이 조합들이 함께 색인되는 경우가 많으므로, 이 데모의 공백은 **예시일 뿐** 실제
문헌 공백이 아닙니다. 다만 "보유 모달리티(EEG·호흡·HRV)를 결합한 조합을 훑어 준다"는 **활용
방식**은 BELL-001 맥락에서 그대로 유효합니다 — 실제 키워드로 돌려 확인하세요.

## 어떻게 계산하나 (요약)

| 항목 | 계산 |
|---|---|
| 부상/쇠퇴 | 기간을 `(최소연도+최대연도+1)//2` 기준 초기/최근으로 나눠, 주제별 `최근비중−초기비중` |
| 기대 동시등장 | `count(A)·count(B) / N` (독립 가정) |
| lift | `관측 동시등장 / 기대` — 낮을수록 미개척 |
| p-value | 초기하분포 하단꼬리 `P(X ≤ 관측)` — 우연히 이만큼 덜 엮일 확률(작을수록 유의) |
| 공백 필터 | `기대 ≥ --gap-min-expected` 이고 `lift ≤ --gap-max-lift` 인 조합만 |

## 한계 / Limitations
- **MeSH 의존**: 공동출현 분석은 PubMed가 색인한 MeSH 주제어에 기반합니다. 아주 최신
  논문은 MeSH가 아직 안 붙어 있을 수 있어(그 논문은 주제 통계에서 빠짐), `--years`를
  넉넉히 두는 편이 안전합니다.
- **탐색적 신호**: "공백"은 문헌 부재의 통계적 신호일 뿐, 그 조합이 반드시 새롭고 타당함을
  뜻하지 않습니다(용어가 다르게 색인됐거나, 임상적으로 무의미한 조합일 수 있음).
  착수 전 상위 조합의 대표 논문을 반드시 직접 확인하세요.
- **표본 상한**: 기본 최대 300편만 가져옵니다(`--max-records`로 조정). 매우 큰 분야는
  esearch 정렬(최신순) 상 최근 편향이 생길 수 있습니다.
- NCBI E-utilities 예절을 위해 `--email`(가능하면 `--api-key`) 지정을 권장합니다.

## License
MIT © 2026 hyeonjoong
