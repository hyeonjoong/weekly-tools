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
- **동향(trend)**: 연도별 발행 편수, 두 구간의 **연평균 편수** 비교, 로버스트 추세 기울기
  (**Theil–Sen**, 편/년), 그리고 **Mann–Kendall 단조추세 검정**(발행량이 시간에 따라
  *통계적으로 유의하게* 늘/줄고 있는지). **표본이면 성장률·추세검정·부상/쇠퇴를
  생략**하고, 연도 차트만 `(⚠️ 표본 — 추세 아님)` 라벨을 달아 남깁니다 — 표본의 연도
  분포를 분야의 추세로 읽으면 안 되기 때문입니다.
- **지형(landscape)**: 주요 저널, 주요 주제(MeSH descriptor)별 논문 수.
- **부상/쇠퇴**: 전체 기간을 초기/최근 두 구간으로 나눠 각 주제의 '비중' 변화로 순위를
  매기고, 표시된 행에 **Fisher 정확검정 p + BH-FDR q** 를 함께 표시(선택편향이 남는다는
  점도 함께 밝힘).
- **연구공백(gap) — 주제 축**: 빈출 상위 주제쌍의 **관측 동시등장 vs 기대(독립가정)
  비율(lift)** 을 계산해, lift가 낮은(=기대보다 훨씬 덜 엮인) 조합을 미개척 각도로 제안.
  각 조합에 초기하검정 p-value, **BH-FDR q-value**(여러 쌍 동시검정 보정), **부족 편수
  (기대−관측)**, 그리고 그 공백이 **메워지는 중인지·벌어지는 중인지·완전히 비어 있는지**
  를 보고합니다(JSON/CSV 에는 **nPMI·Jaccard·Ochiai** 연관 지표도).
  각 공백에는 **PubMed 검증 링크 2종**(MeSH 색인 기준 / 제목·초록 자유어 기준)이 붙어,
  그 공백이 진짜인지 **색인 artifact** 인지 클릭 한 번으로 가릴 수 있습니다.
  더해서 **대표 논문 ID**(A만/B만/함께 다룬 논문)와 **Swanson ABC 가교 주제 C**
  (A·B 각각과는 자주 엮이지만 A–B 자체는 드문 제3의 주제)를 제시합니다.
- **연구 각도 공백(angle gap) — MeSH 부주제어 축**: 색인자가 주제어에 붙인 부주제어
  (`Insomnia/therapy`, `Heart Rate/drug effects`, `Melatonin/adverse effects`)를 세로축으로
  삼아, **"이 주제는 논문이 많은데 *약물치료·이상반응·치료* 각도로는 거의 안 봤다"** 를
  주제쌍 공백과 **같은 통계**(초기하 하단꼬리 + BH-FDR + lift 정확구간)로 찾아냅니다.
  임상·제약 연구자에게 이 축은 곧바로 프로토콜 아이디어가 됩니다.
  분석 단위는 논문이 아니라 **색인 표목**((논문, 주제어) 한 칸 중 부주제어가 붙은 것)
  입니다 — 주변확률과 관측이 같은 모집단이어야 검정이 성립하기 때문입니다.
  NLM 색인 규칙상 불가능해 보이는 조합(해부 용어 × `/drug therapy` 등)은 어휘 가족
  휴리스틱으로 `⚠ 규칙상 불가?` 라고 **표시하고 순위를 뒤로 미룰 뿐, 검정에서 빼지는
  않습니다**(빼면 FDR 의 분모가 결과에 의존해 q 가 정직하지 않게 됩니다).
  `--angle-hide-implausible` 로 표에서 감출 수 있습니다.
- **근거 공백(evidence gap) — 연구 설계 축**: PubMed `PublicationType`(+ 보조로 연구설계
  MeSH)으로 각 논문의 근거 수준(메타분석/RCT/임상시험/관찰/증례/종설)을 판정해 **분야의
  근거 지형**을 그리고, 주제별 **개입연구(RCT·임상시험) 밀도**를 *그 주제를 달지 않은
  나머지 논문*과 Fisher 정확검정 + BH-FDR 로 비교합니다.
  **"논문은 많은데 개입연구는 유의하게 적은 주제" = 시험을 설계할 자리.**
  설계를 알 수 없는 논문은 분모에서 빼고 **커버리지를 함께 보고**해, '색인이 안 됨'과
  '시험이 없음'을 섞지 않습니다.
- **대상집단 공백(population gap) — '누구를 대상으로' 축**: 색인자가 다는 연령·성별
  체크 태그(`Aged`, `Child`, `Female`…)를 잡음이 아니라 **하나의 축**으로 씁니다.
  각 주제에 대해 "이 집단이 **나머지 논문보다** 유의하게 적게 색인됐는가"를 Fisher
  정확검정 + BH-FDR 로 판정해 **"이 주제의 논문은 이 분야 평균보다 고령자(65+) 비중이
  낮다"** 를 짚어 줍니다 — '한 편도 없다'가 아니라 **상대적으로 낮다**는 뜻이며,
  절대적으로 비어 있는 줄을 원하면 `관측 0` 인 행이나 `--population-sort share` 를
  보세요. 연령 축은 규제·설계 관점에서 곧바로 시험 계획이 됩니다(ICH E7 고령자,
  소아 개발계획). **성별 축은 사실상 포화** 라 참고용입니다 — MeSH `Female`/`Male` 은
  '그 성별 피험자가 한 명이라도 포함됐는가' 표시라 인체 논문 대부분에 둘 다 붙고,
  그래서 등록 성비나 성별 층화분석 여부는 이 축으로 알 수 없습니다(축의 모든 논문에
  붙은 집단은 정보가 없으므로 **검정에서 제외**하고 다중검정 예산에도 넣지 않습니다).
  분모는 **축마다 따로**(연령 태그가 있는 논문 / 성별 태그가 있는 논문) 잡고
  커버리지를 함께 보고해, '색인이 안 됨'과 '연구가 없음'을 섞지 않습니다.
  연령 구간은 NLM 정의를 그대로 써서 **서로 겹치므로**(40–70세 코호트 = 성인+중년+고령)
  비중의 합은 100% 가 아닙니다. 각 행에는 그 조합을 PubMed 에서 직접 세어 보는
  검증 링크가 붙습니다.
- **불확실성(정확 신뢰구간)**: **주제별 개입비율**(및 코퍼스 전체 개입비율)에는
  **Clopper–Pearson**(대상집단 축의 **집단 비중**에도 같은 구간), **lift**(주제쌍·각도·
  대상집단)에는 관측 편수의 **포아송 정확구간(Garwood)** 을 붙입니다. 표의 `95% CI`
  열이 무엇의 구간인지는 절마다 다릅니다: 대상집단 표에서는 **바로 왼쪽 `비중`** 의
  구간이고, 주제쌍·각도 표에서는 **lift** 의 구간입니다(다른 지표에는 구간이 없습니다).
  `0/8편 = 0%` 는 "시험이 없다"가 아니라 "상한이 37% 다"라는 뜻이고, `lift 0.00
  (95% CI 0.00–1.64)` 는 편수가 적어 단정할 수 없다는 뜻입니다. 점추정만 적으면
  독자가 작은 표본을 과신합니다.
- **여러 출처 합치기**: `--from-file` 을 여러 번 주면 PubMed XML + Scopus CSV +
  EndNote RIS 를 **하나의 코퍼스로 병합**합니다. 중복은 **PMID → DOI(정규화) →
  제목+연도** 3단으로 제거하고, 살아남은 레코드의 **빈 필드만** 다른 출처에서 채웁니다
  (Scopus 레코드에 없는 MeSH 를 PubMed 레코드에서 가져오는 식). 무엇으로 몇 건을
  합쳤는지 리포트 첫머리에 밝힙니다(`--no-fuzzy-dedup` 으로 제목 대조 해제).
- **잡음 제거**: 연구 주제가 아닌 **체크 태그**(Humans/Male/Female/Adult/Aged/Animals…)와
  **방법론 표제어**(Treatment Outcome, Risk Factors, Surveys and Questionnaires,
  `… as Topic` …)를 **기본 제외**합니다(`--include-check-tags` 로 해제). 검색어 자체처럼
  자명한 주제는 `--exclude-term` 으로 뺄 수 있습니다.
- **정직성 장치**: 표본이 30편 미만이면 경고하고, 표본이 잘렸으면 추세를 생략하며,
  다중검정 예산(m)과 **달성한 최소 q** 를 밝히고, q ≤ 0.05 를 못 넘긴 후보를 추천할
  때는 그렇다고 말합니다. 주제가 MeSH 가 아니라 저자 키워드에서 왔다면 그것도 밝히고,
  전수 검증에서 색인 artifact 로 판정된 후보는 추천에서 뺍니다.
- **재현성**: 리포트 하단에 도구 버전·생성 시각·**입력 파일 sha256**·**분석 결과에 영향을
  주는 모든 옵션**을 남깁니다(표시 전용인 `--format`·`--csv-section` 은 제외).
  `--no-meta` 로 끄면 같은 입력·옵션에서 출력이 바이트 단위로 재현됩니다(동률은 이름
  오름차순으로 깨므로 해시 랜덤화와 무관). API 키·이메일은 절대 기록되지 않습니다.

> 공백 탐색의 근거: 연관규칙의 **lift**, 그리고 문헌기반발견(Literature-Based Discovery,
> Swanson ABC) 계열의 아이디어입니다. AB 공백(둘이 함께 드묾)에 더해, ABC **가교 주제** C
> 를 찾아 "A는 C를 통해 B와 연결된다"는 기전 서사를 제안합니다. 다만 문헌 구조상의 빈자리를
> 후보로 제시할 뿐 인과·타당성을 보장하지는 않으니(아래 한계), 제시된 대표 PMID 로 반드시
> 원문을 확인하세요. 여러 주제쌍을 한꺼번에 보므로 유의성은 raw p 대신 **q(FDR) ≤ 0.05** 로.

**입력/출력 형식**
- 입력(내용 기반 **자동 판별**, 확장자는 보조 힌트):
  - PubMed **efetch XML** (`PubmedArticle` · `PubmedBookArticle`) — 증분(pull) 파서로
    레코드마다 서브트리를 해제해 **전체 DOM 을 만들지 않습니다**. 다만 파일 자체는 한 번에
    읽고 디코드하므로 **최대 메모리는 입력 크기에 비례**합니다(레코드 밀도에 따라 파일의
    수 배). 그래서 입력을 64MB(.gz 는 해제 후 64MB)로 제한합니다 — 넘으면 rc 2 로 거부.
  - **MEDLINE/NBIB** — PubMed 웹의 *Save → PubMed format*.
  - **RIS** — EndNote/Zotero/Mendeley/Scopus 내보내기.
  - **CSV/TSV** — Scopus·Web of Science·Covidence·Rayyan·엑셀 편집본. 헤더 이름 차이
    (`Source title` / `Journal/Book` / `Publication Year` …), 구분자 자동 추정,
    헤더 앞 안내문 줄, 열 수가 들쭉날쭉한 행, `2019.0`·`Mar-2019` 같은 연도 표기를 흡수합니다.
  - 위 모두의 **.gz** 압축본. UTF-8/latin-1 관대 디코드.
  - 위 형식들을 **여러 개 동시에**(`--from-file` 반복) — 중복은 PMID→DOI→제목+연도로 제거.
  - MeSH **부주제어**(qualifier)는 네 형식 모두에서 읽습니다: XML `<QualifierName>`,
    NBIB/RIS/CSV 의 `Descriptor/*qualifier` 표기. DOI 도 함께 읽어 출처 간 병합에 씁니다.
  - MeSH 가 한 편도 없는 입력(RIS/CSV 내보내기)이면 **저자 키워드를 주제로 자동 승격**하고
    그 사실을 stderr 로 알립니다 — 조용히 빈 리포트가 나오지 않도록.
- 출력: **Markdown**(기본, ~210줄), **JSON**(`--format json`), **CSV**(`--format csv` —
  엑셀 한글 대비 UTF-8 BOM 포함). Markdown 은 읽는 흐름을 위해 일부 표(쇠퇴 주제,
  개입연구 과밀 주제)를 한 줄 요약으로만 싣습니다 — JSON/CSV 에는 더 많이 들어 있습니다
  (쇠퇴 주제는 Markdown 5개 / JSON·CSV 8개까지이며, 그 이상은 어디에도 싣지 않습니다).
  CSV 는 `--csv-section` 으로 표를 고릅니다:
  `gaps`(기본)·`yearly`·`journals`·`mesh`·`emerging`·`declining`·`evidence`·`topic-evidence`·
  `angles`(연구 각도 공백)·`population`(주제×대상집단 공백)·`population-profile`(집단 지형).

## Install

```bash
cd pubgap-논문공백탐색
python3 -m pip install -e .        # 순수 표준 라이브러리 — 외부 의존성 없음
# 또는 설치 없이 바로:  python3 -m pubgap.cli ...
```

의존성이 **전혀 없습니다**(urllib·xml 등 표준 라이브러리만 사용).

## Usage

```bash
# 1) 오프라인 데모 — 번들 예시(수면/호흡/HRV/EEG/수면제 28편)로 즉시 확인
pubgap --from-file examples/sleep_pubmed.xml

# 2) 실제 PubMed 조회 (네트워크) — 최근 10년, 최대 300편
pubgap "slow breathing AND sleep" --years 10 --email you@lab.org

# 3) 결과를 파일로 + 나중에 재분석하려 원본 XML 저장
pubgap "hearing loss AND cognitive decline" --out gap.md --save-xml raw.xml

# 4) JSON / CSV 로 (파이프라인/스프레드시트용)
pubgap --from-file examples/sleep_pubmed.xml --format json
pubgap --from-file examples/sleep_pubmed.xml --format csv --out gaps.csv

# 5) 내려받은 NBIB/RIS/CSV 또는 .gz 파일 그대로 분석 (형식 자동 판별)
pubgap --from-file my_export.nbib
pubgap --from-file endnote_export.ris
pubgap --from-file scopus.csv
pubgap --from-file pubmed_result.xml.gz

# 5-1) 여러 출처를 하나의 코퍼스로 합치기 (중복은 PMID→DOI→제목+연도로 자동 제거,
#      빈 항목은 다른 출처 레코드로 보강 — 예: Scopus CSV 에 없는 MeSH 를 PubMed XML 에서)
pubgap --from-file pubmed.xml --from-file scopus.csv --from-file wos.ris

# 6) 통계적으로 유의한 공백만 / 연도 범위 / 대표(별표) 주제만
pubgap --from-file examples/sleep_pubmed.xml --gap-max-q 0.05
pubgap --from-file examples/sleep_pubmed.xml --min-year 2019 --max-year 2024
pubgap "slow breathing AND sleep" --min-year 2018 --include-keywords --email you@lab.org
#    ※ --major-topics-only 는 별표(MajorTopicYN="Y")가 붙은 실제 PubMed 색인에서만
#      의미가 있습니다. 번들 예시에는 별표가 없어 주제가 비고, 그 사실을 경고로 알립니다.

# 7) 검색어 자체를 주제에서 제외 (기본 정렬은 이미 '부족 편수' 순)
pubgap --from-file examples/sleep_pubmed.xml --exclude-term Sleep

# 7-1) 전체 편수만 1초 만에 확인하고, 그 수만큼 전수 분석
pubgap "slow breathing AND sleep" --count-only --email you@lab.org
pubgap "slow breathing AND sleep" --max-records 1500 --email you@lab.org

# 8) 근거 공백(설계 축) 표만 스프레드시트로
pubgap --from-file examples/sleep_pubmed.xml --format csv --csv-section topic-evidence

# 9) 연구 각도(MeSH 부주제어) 공백 — "이 주제를 약물치료 관점에서 본 논문이 없다"
pubgap --from-file examples/sleep_pubmed.xml --format csv --csv-section angles
pubgap --from-file examples/sleep_pubmed.xml --angle-top-k 20 --angle-max-lift 0.7

# 10) 대상집단(연령·성별) 공백 — "이 주제는 고령자에서 거의 안 봤다"
pubgap --from-file examples/sleep_pubmed.xml --format csv --csv-section population
pubgap --from-file examples/sleep_pubmed.xml --population-sort q --population-min-articles 8
```

주요 옵션:
- 입력: `--from-file`(XML/NBIB/RIS/CSV/.gz 자동판별; **여러 번 지정하면 합쳐서 분석**),
  `--no-fuzzy-dedup`(제목+연도 대조 끄기 → PMID·DOI 가 같을 때만 중복 처리),
  `--years N`, `--max-records M`,
  `--min-year/--max-year`(연도 범위, 미상 제외), `--save-xml`, `--email/--api-key`.
- 주제 처리: `--major-topics-only`(MeSH 대표주제만), `--include-keywords`(저자 키워드 보강 —
  MeSH 미부여 최신 논문 대비), `--include-check-tags`(체크 태그도 포함, 기본은 제외),
  `--exclude-term TERM`(반복 가능)·`--exclude-terms-file PATH`(한 줄에 하나, `#` 주석).
- 공백 기준/정렬: `--gap-min-expected`(기본 2.0), `--gap-max-lift`(기본 0.5),
  `--gap-max-q`(최대 BH-FDR q — 기본 없음), `--gap-top-k`(기본 12, 최대 200),
  `--no-bridges`(가교 계산 끔),
  `--gap-sort {deficit,lift,q,expected,npmi}` — **기본 `deficit`**.
- 근거 공백: `--top-evidence K`(기본 12; 3편 미만 주제는 통계가 무의미해 제외),
  `--no-evidence`(끄기).
- 각도 공백(MeSH 부주제어 축): `--angle-top-k K`(기본 12; **표목 3개 미만 주제는 제외**),
  `--angle-top-qualifiers M`(기본 10, 최대 100), `--angle-min-expected`(기본 1.0),
  `--angle-max-lift`(기본 0.5), `--angle-hide-implausible`(색인 규칙상 불가능해 보이는 칸을
  표에서 감춤), `--no-angles`(끄기).
- 대상집단 공백(연령·성별 축): `--population-top-k K`(기본 12, 최대 200),
  `--population-min-articles N`(기본 5 — 이보다 얇은 주제는 검정하지 않음),
  `--population-sort {deficit,share,q,lift}`(기본 deficit), `--no-population`(끄기).
- 네트워크: `--sample {stratified,recent}`(기본 stratified — 연도별 균등 표집),
  `--count-only`(편수만 1초 만에 확인), `--verify-gaps`/`--no-verify-gaps`
  (공백쌍을 PubMed **전수**로 재계산해 색인 artifact 를 걸러냄; 조회 경로 기본 켜짐).
- 표시/출력: `--top-mesh`(기본 15)/`--top-journals`(기본 8), `--format {md,json,csv}`(`--json` 은 구버전 별칭),
  `--csv-section`, `--out`, `--no-meta`(실행 정보 블록 끄기 → 바이트 단위 재현), `--version`.

### 출력 예시 (번들 **합성** 예시: 수면·호흡·HRV·EEG·수면제 28편)

> ⚠️ `examples/sleep_pubmed.xml` 는 **실제 논문이 아니라 합성 데이터**입니다(PMID·제목 가짜).
> 오프라인에서 도구 동작을 보여주기 위한 것이며, 아래 숫자는 실제 문헌 통계가 아닙니다.
> 또한 아래는 **발췌**입니다 — 실제 출력에는 `## 연도별 발행량`, `## 주요 저널`, `## ↗︎ 최근 부상 주제`,
> 각 표의 설명 각주, `### 실행 정보` 가 더 있습니다(전체 약 210줄).

```
# 연구 동향·공백 리포트 — `examples/sleep_pubmed.xml`

- 분석 논문: **28편** (MeSH 주제어 보유 28편) · 발행연도 2015–2024
- 발행량: **2020년 이후** 연 3.6편 (그 이전 연 2.0편 대비 1.80배) · 추세 기울기 +0.3편/년(Theil–Sen)
- 추세 검정(Mann–Kendall): **유의한 증가 추세 ↗︎** (τ=+0.60, p=0.034, n=10년)
- ⚠️ **표본 주의**: 분석 논문이 28편으로 적습니다(권장 ≥30편). 아래 공백 통계(기대·lift·p·q)는 한두 편의 색인 차이로 크게 흔들립니다 — `--max-records` 를 늘리거나 검색어를 넓혀 보세요.

## 요약 (결론부터)

- **주제 조합 1순위**: Sleep × Heart Rate — 함께 2편(기대 5.5, lift 0.37, q=0.076)
- **연구 각도 1순위**: Heart Rate × drug effects — 함께 0개 표목(기대 1.1, q=0.496)
- **대상집단 1순위**(상대 과소대표): Sleep × 고령 (65+) — 17편 중 5편(기대 12.4, q=0.198) ⚠️ 탐색적(q>0.05)
- 통계적 견고성: 주제쌍 11개를 검정해 **달성한 최소 q=0.076** — **q≤0.05 인 후보가 없어, 아래는 모두 탐색적 후보입니다.**
- ⚠️ 표본이 작아(30편 미만) 아래 통계는 한두 편의 색인 차이로 흔들립니다.

## 주요 주제 (MeSH 주제어, 논문 수)

- Sleep — 17
- Respiration — 11
- Heart Rate — 9
- Electroencephalography — 7
- Autonomic Nervous System — 6
- Hypnotics and Sedatives — 5
...
- Memory — 1  ·(공백 탐색 제외)

## 🧪 근거 지형 (연구 설계 구성)

- 연구 설계가 확인된 논문 **28편** (전체 28편의 100%) 기준입니다. 설계를 알 수 없는 0편은 분모에서 제외했습니다 — '색인이 안 됨'과 '시험이 없음'을 섞지 않기 위해서입니다.

| 근거 수준 | 편수 | 비중 |
|---|---:|---:|
| 무작위배정 임상시험(RCT) | 10 | 36% |
| 기타 임상시험(비무작위·초기상) | 2 | 7% |
| 관찰연구 | 13 | 46% |
| 증례보고 | 1 | 4% |
| 종설/지침(비1차연구) | 2 | 7% |

- **개입연구(RCT·임상시험) 12편 = 설계 확인된 논문의 43%** (95% CI 24–63%)

### 개입연구가 상대적으로 적은 주제 (= 시험을 설계할 자리 후보)

| 주제 | 논문 | 개입연구 | 개입비율 | 95% CI | 그 외 논문 | p | q(FDR) |
|---|---:|---:|---:|:--:|---:|---:|---:|
| Monitoring, Physiologic | 3 | 0 | 0% | 0–71% | 48% | 0.238 | 0.524 |
| Electroencephalography | 7 | 1 | 14% | 0–58% | 52% | 0.184 | 0.507 |
| Hypnotics and Sedatives | 5 | 1 | 20% | 1–72% | 48% | 0.355 | 0.539 |
| Sleep | 17 | 6 | 35% | 14–62% | 55% | 0.441 | 0.539 |
...

## 🎯 연구 각도 공백 (MeSH 부주제어 축)

- 부주제어가 색인된 논문 **28편** (전체 28편의 100%) · 서로 다른 각도 10종 기준입니다.
- 이 분야가 주로 보는 각도: physiology(17), methods(13), drug effects(9), adverse effects(4), pharmacology(4), drug therapy(2)

| 주제 | 연구 각도 | 주제 표목 | 함께(관측) | 기대 | 부족 | lift | 95% CI | q(FDR) | 색인 가능성 | 이 주제의 주요 각도 |
|---|---|---:|---:|---:|---:|---:|:--:|---:|:--:|---|
| Heart Rate | drug effects | 8 | 0 | 1.1 | +1.1 | 0.00 | 0.00–3.23 | 0.496 |  | physiology 8 |
| Electroencephalography | physiology | 7 | 0 | 3.3 | +3.3 | 0.00 | 0.00–1.13 | 0.187 | ⚠ 규칙상 불가? | methods 7 |
...(이하 생략)

## 👥 대상집단 공백 (연령·성별 축)

- 대상집단이 색인된 논문 **28편** (전체 28편의 100%) 기준입니다 — 연령 태그 28편 · 성별 태그 28편. 태그가 없는 논문은 **분모에서 제외**했습니다('색인 안 됨'과 '연구 없음'을 섞지 않기 위해서입니다).

| 대상집단 | 논문 | 분모 | 비중 | 95% CI |
|---|---:|---:|---:|:--:|
| 소아·청소년 (0–18) | 3 | 28 | 11% | 2–28% |
| 청년 (19–24) | 9 | 28 | 32% | 16–52% |
| 성인 (19–44) | 28 | 28 | 100% | 88–100% |
| 중년 (45–64) | 25 | 28 | 89% | 72–98% |
| 고령 (65+) | 13 | 28 | 46% | 28–66% |
| 초고령 (80+) | 4 | 28 | 14% | 4–33% |
| 여성 | 28 | 28 | 100% | 88–100% |
| 남성 | 28 | 28 | 100% | 88–100% |
| 임신 | 0 | 28 | 0% | 0–12% |

### 상대적 과소대표 — '나머지 논문보다 이 집단 비중이 낮은 주제'

| 주제 | 대상집단 | 논문 | 관측 | 기대 | 부족 | 비중 | 95% CI | 그 외 | q(FDR) | 확인 |
|---|---|---:|---:|---:|---:|---:|:--:|---:|---:|:--:|
| Sleep | 고령 (65+) | 17 | 5 | 12.4 | 7.4 | 29% | 10–56% | 73% | 0.198 | [PubMed](…) |
| Respiration | 청년 (19–24) | 11 | 0 | 5.8 | 5.8 | 0% | 0–28% | 53% | 0.046 | [PubMed](…) |
| Electroencephalography | 고령 (65+) | 7 | 0 | 4.3 | 4.3 | 0% | 0–41% | 62% | 0.060 | [PubMed](…) |
| …(중략) | | | | | | | | | | |
| Hypnotics and Sedatives | 고령 (65+) | 5 | 0 | 2.8 | 2.8 | 0% | 0–52% | 57% | 0.198 | [PubMed](…) |
...(이하 생략 — 전체는 `--csv-section population`)

## 🔍 덜 연구된 주제 조합 (저조 조합 = 연구공백 후보)

각각 개별적으로는 자주 다뤄지지만 **함께는 기대보다 훨씬 드물게** 연구된 주제쌍입니다. lift(관측/기대)가 낮을수록 미개척 조합입니다.

| 주제 A | 주제 B | 함께(관측) | 기대 | 부족 | lift | 95% CI | p | q(FDR) | 추이 |
|---|---|---:|---:|---:|---:|:--:|---:|---:|:--:|
| Sleep | Heart Rate | 2 | 5.5 | +3.5 | 0.37 | 0.04–1.32 | 0.007 | 0.076 | – 0/10→2/18 |
| Respiration | Electroencephalography | 0 | 2.8 | +2.8 | 0.00 | 0.00–1.34 | 0.016 | 0.090 | ⬜ 완전공백 0/10→0/18 |
| Heart Rate | Electroencephalography | 0 | 2.2 | +2.2 | 0.00 | 0.00–1.64 | 0.043 | 0.156 | ⬜ 완전공백 0/10→0/18 |

_정렬: **부족 편수 내림차순(기대−관측)** (`--gap-sort`). `부족`=기대−관측(**독립 가정 대비** 부족분, '있었어야 할 논문 수'가 아니다) · `lift`=관측/기대 · `95% CI`=lift 의 포아송 정확구간(상한이 1 을 넘으면 '덜 엮였다'고 단정할 수 없다는 뜻) · `p`=초기하 하단꼬리 · `q`=BH-FDR 보정 · `추이`: ⬜완전공백(양쪽 구간 모두 0편) / ↗메워짐 / ↘벌어짐 / –판단불가. 자세한 읽는 법은 사용법.md 참고._
_검정한 주제쌍 m=11개 · 달성한 최소 q=0.076 — **q≤0.05 인 후보가 없습니다.** 검정 수가 많을수록 q 는 나빠지므로, `--gap-top-k` 를 낮춰 검정 수를 줄이거나 `--max-records` 를 올려 표본을 키우세요. (`--gap-min-expected` 를 낮추면 검정 수가 오히려 늘어 q 는 더 나빠집니다.)_

> 제안: **Sleep × Heart Rate** 를 결합한 분석/논문을 검토하세요. 관련 논문 각각 17·9편이 있으나 둘을 함께 다룬 논문은 2편뿐입니다(기대 5.5편, lift 0.37, 95% CI 0.04–1.32, p=0.007, q=0.076).

> ⚠️ 이 후보의 q=0.076 는 다중검정 보정 기준(0.05)을 넘습니다 — **탐색적 후보**로만 쓰고, 아래 검증 링크로 실제 문헌을 확인하세요.

> 검증: [MeSH 색인 기준으로 이 조합 검색](https://pubmed.ncbi.nlm.nih.gov/?term=%22Sleep%22%5BMeSH+Terms%5D+AND+%22Heart+Rate%22%5BMeSH+Terms%5D) · [제목/초록(자유어) 기준](https://pubmed.ncbi.nlm.nih.gov/?term=%22Sleep%22%5BTitle%2FAbstract%5D+AND+%22Heart+Rate%22%5BTitle%2FAbstract%5D) — 자유어 검색에서는 논문이 많이 나온다면, 이 '공백'은 연구 공백이 아니라 **색인 방식의 차이(artifact)** 일 가능성이 큽니다.

> 가교(Swanson ABC): Sleep 와 Heart Rate 를 잇는 제3 주제 → **Monitoring, Physiologic**(A&C 2·C&B 2), **Autonomic Nervous System**(A&C 2·C&B 3), **Respiration**(A&C 5·C&B 4). 두 주제가 각각 C 와는 자주 엮이므로, C 를 매개로 한 연결 가설을 세울 수 있습니다.

> 함께 다룬 2편의 설계 구성: 관찰연구 2. 개입연구가 0편이면 **시험을 설계할 자리**, 전부 0편이면 색인/개념 문제일 수 있습니다.

> 대표 논문(확인용 ID = PMID 또는 DOI):
> - Sleep — `30000002` (2016, J Sleep Res) EEG slow-wave activity in patients with insomnia
> - Sleep — `30000003` (2016, Chest) Respiratory rate monitoring during sleep
> - Sleep — `30000005` (2017, Sleep) Cortical arousal and EEG markers of fragmented sleep
> - Heart Rate — `30000001` (2015, Sleep Med) Slow breathing and heart rate variability in healthy adults
> - Heart Rate — `30000004` (2017, Psychophysiology) Parasympathetic activation via paced breathing
> - Heart Rate — `30000006` (2018, Front Neurosci) Heart rate variability biofeedback for stress
> - 함께 — `30000014` (2022, Sleep Med) Wearable HRV monitoring across the night
> - 함께 — `30000021` (2021, Front Physiol) Beta-blockers and heart rate variability during the night

---
_주의: 이 리포트는 MeSH 주제어 공동출현 기반 휴리스틱입니다. '공백'은 문헌 부재의 신호일 뿐 인과/타당성을 보장하지 않으며, 실제 착수 전 위 검증 링크와 대표 논문을 직접 확인하세요._
```

> 같은 코퍼스를 지저분한 CSV 내보내기로 담은 `examples/sleep_export.csv` 도 함께 있습니다.
> `pubgap --from-file examples/sleep_export.csv` 는 위와 **동일한 리포트**를 냅니다
> (헤더 이름 차이·엑셀이 망친 연도·빈 행·안내문 줄을 모두 흡수한다는 회귀 테스트이기도 합니다).

> ※ `examples/sleep_pubmed.xml` 에는 체크 태그(Humans/Male/Female/Adult/Aged…)가
> **합성으로 들어 있습니다**. 기본 설정에서는 주제 분석에서 제외되어 위 '주요 주제'에
> 안 보이고, **대상집단 축에서만** 신호로 쓰입니다. `--include-check-tags` 를 붙이면
> 이 태그들이 주제 목록 상위를 채우는 것을 확인할 수 있습니다.

이 **합성** 예시는 도구가 어떻게 저조 조합을 짚는지 보여주려고 EEG가 호흡/심박과 함께
나오지 않게 일부러 구성했습니다(그래서 EEG×HRV, EEG×호흡이 상위 공백으로 나옵니다). 실제
PubMed에서는 이 조합들이 함께 색인되는 경우가 많으므로, 이 데모의 공백은 **예시일 뿐** 실제
문헌 공백이 아닙니다. 다만 "보유 모달리티(EEG·호흡·HRV)를 결합한 조합을 훑어 준다"는 **활용
방식**은 BELL-001 맥락에서 그대로 유효합니다 — 실제 키워드로 돌려 확인하세요.

## 어떻게 계산하나 (요약)

| 항목 | 계산 |
|---|---|
| 부상/쇠퇴 | 기간을 `(최소연도+최대연도+1)//2` 기준 초기/최근으로 나눠(`>= split` 이면 최근), 주제별 `최근비중−초기비중` 으로 **순위**를 매기고, 표시된 행에만 Fisher 정확검정 `p` + BH-FDR `q` |
| 성장 배수 | 두 구간의 **연평균 편수** 비(`최근/년 ÷ 초기/년`). 구간 햇수가 다를 수 있어 총량비 대신 이 값을 표시 |
| 추세 기울기 | **Theil–Sen**(모든 점쌍 기울기의 중앙값, 편/년). CAGR 처럼 양끝 한 해에 휘둘리지 않음. CAGR 은 JSON/`growth.cagr` 에만 |
| 추세 검정 | **Mann–Kendall**: 조밀 연도 시계열(빠진 해=0)에 대해 `S`, 동률보정 `τ(tau-b)`, 정규근사 `z`(연속성 보정), 양측 `p` |
| N (분모) | **주제어를 하나라도 가진 논문 수**. 색인 안 된 논문을 넣으면 기대값이 낮아져 진짜 공백이 가려진다 |
| 기대 동시등장 | `count(A)·count(B) / N` (독립 가정) |
| lift | `관측 동시등장 / 기대` — 낮을수록 미개척 |
| 부족(deficit) | `기대 − 관측` — '있었어야 하는데 없는 논문 편수'(연구자 단위의 효과크기) |
| Jaccard | `관측 / (count(A) + count(B) − 관측)` — 합집합 대비 겹침 |
| Ochiai(cosine) | `관측 / √(count(A)·count(B))` — 문헌계량 공어분석의 표준 유사도 |
| nPMI | `log₂(p(A,B)/(p(A)p(B))) / −log₂ p(A,B)` ∈ [−1, 1]. 관측 0 이면 정의상 −1(완전 배타), 독립이면 0 |
| p-value | 초기하분포 하단꼬리 `P(X ≤ 관측)` — 우연히 이만큼 덜 엮일 확률(작을수록 유의) |
| q-value | 검정한 **모든** 후보쌍(기대≥min_expected)에 **Benjamini–Hochberg FDR** 적용 — 다중검정 보정 |
| lift 95% CI | 관측 편수의 **포아송 정확구간(Garwood)** 을 기대값으로 나눈 값 = 역학의 SIR/SMR 구간과 같은 방식. 상한이 1 을 넘으면 "덜 엮였다"고 단정할 수 없다는 뜻 |
| 공백 필터 | `기대 ≥ --gap-min-expected` 이고 `lift ≤ --gap-max-lift`(옵션 `q ≤ --gap-max-q`) 인 조합만 |
| 공백 추이 | 동시등장을 초기/최근으로 나눠 **구간 논문 수로 정규화한 비율**을 비교. 양쪽 0편이면 `⬜ 완전공백`, 비율이 오르면 `↗ 메워짐`, 내리면 `↘ 벌어짐`, 같으면 `→ 유지`, 동시등장 3편 미만·연도 미상 혼입·한쪽 구간 0편이면 `–`(판단불가). 판정 가능한 행이 하나도 없으면 열 자체를 생략한다 |
| 가교(ABC) | 각 공백 A–B 에 대해, 제3 주제 C 를 **`lift(A,C)×lift(C,B)`** 로 순위(빈도 순이 아님 — 그러면 검색어 자체가 늘 1위). 코퍼스의 80% 초과에 붙는 C, 지지도 2편 미만은 제외 |
| 검증 링크 | 각 공백에 대해 `"A"[MeSH Terms] AND "B"[MeSH Terms]` 와 `[Title/Abstract]` 두 가지 PubMed URL. 두 결과 수가 크게 다르면 색인 artifact |
| 근거 tier | `PublicationType` → 메타분석·체계적고찰 > RCT > 기타 임상시험 > 관찰 > 증례 > 종설. **가장 높은 tier 하나**로 대표. PublicationType 에 설계 신호가 없으면 코호트/후향/환자대조 등 **연구설계 MeSH** 를 관찰연구 신호로 보조 사용 |
| 설계 커버리지 | 위 tier 로 **판정 가능한** 논문 비율. `Journal Article` 은 설계 정보가 아니므로 커버리지에 포함되지 않음 |
| 개입연구 판정 | RCT·임상시험 **태그의 존재 여부**(대표 tier 가 아님) — `Meta-Analysis + RCT` 인 논문도 개입연구로 센다 |
| 근거 공백 p/q | 주제 × 개입여부의 2×2 **Fisher 정확검정**(양측, *그 주제를 달지 않은 나머지* 대비) + BH-FDR |
| 개입비율 95% CI | **Clopper–Pearson 정확구간**. `0/8편 = 0%` 를 구간 없이 적으면 "시험이 없다"로 읽히지만 실제 상한은 37% 다 |
| 연구 각도(부주제어) | MeSH descriptor 에 붙은 qualifier 를 소문자로 정규화해 **주제 × 각도** 격자를 만든다. `/` 뒤가 **NLM 공식 부주제어 76종**일 때만 부주제어로 인정한다(저자 키워드의 `AI/machine learning` 을 주제×각도로 오해하지 않도록) |
| 각도 분석 단위 | **색인 표목** = (논문, 주제어) 한 칸 중 부주제어가 붙은 것. `N`=전체 표목 수, `n(주제)`=그 주제의 표목 수, `n(각도)`=그 각도가 붙은 표목 수. 주변확률과 관측이 같은 단위여야 검정이 성립한다(논문 단위 주변확률 + 표목 단위 관측을 섞으면 논문당 주제어 d개일 때 lift 가 1/d 로 눌려 **모든 칸이 공백으로** 보인다) |
| 각도 공백 | 기대 `n(주제)·n(각도)/N`, 관측 = 그 주제에 그 각도가 붙은 표목 수. 주제쌍 공백과 **같은** 초기하 하단꼬리 + BH-FDR + lift 정확구간. 표목 3개 미만 주제는 제외 |
| 각도 구조 표시 | NLM 은 주제 범주별로 붙일 수 있는 부주제어를 제한한다(해부 용어에 `/drug therapy` 불가). MeSH 규칙 파일 없이, "그 각도를 쓰는 **다른** 주제들이 함께 쓰는 각도 어휘"를 후보 주제가 하나도 공유하지 않으면 `⚠ 규칙상 불가?` 로 **표시하고 순위를 내린다**(검정에서 빼지는 않는다 — 빼면 m 이 결과에 의존해 q 가 왜곡된다). 판정은 후보 주제 자신의 기여를 뺀 leave-one-out 이라 그 칸의 관측값과 무관하다 |
| 대상집단 그룹 | NLM 연령 체크 태그를 6개 구간(소아·청소년/청년/성인/중년/고령/초고령)과 성별·임신으로 묶는다. **구간은 서로 겹친다**(NLM 정의 그대로) — 비중의 합은 1이 아니다 |
| 대상집단 분모 | **축(연령/성별)마다 따로**: 그 축의 태그가 하나라도 붙은 논문만. 성별만 색인된 논문을 연령 분모에 넣으면 모든 연령대가 실제보다 비어 보인다. 태그가 없는 논문은 제외하고 커버리지를 함께 보고한다 |
| 대상집단 p/q | 주제 × 집단의 2×2 **Fisher 정확검정**(양측, *그 주제를 달지 않은 나머지* 대비) + **검정한 전부**(과대대표 포함)에 BH-FDR. 그 축의 논문 **전부**에 붙었거나 **하나도** 안 붙은 집단은 p 가 항상 1 이라 검정에서 빼고 m 에도 넣지 않는다(혼성 임상연구의 Male/Female). 기대 = 그 주제 편수 × 나머지의 집단 비중, 부족 = 기대−관측. 비중에 Clopper–Pearson, lift 에 포아송 정확구간 |
| 상하위어 의심 | 한쪽 주제어의 단어 집합이 다른 쪽에 **완전히 포함**되면(`Sleep` ⊂ `Sleep, REM`) `⚠상하위어?` 로 표시한다. 정의상 함께 색인되지 않아 lift 가 낮게 나오지만 연구 공백이 아니다 |
| 대표 논문 | 각 공백의 A만/B만/함께 다룬 논문을 **제목·연도·저널과 함께** 최대 3편씩. 함께 다룬 논문의 **연구 설계 구성**(관찰 n편 / RCT n편)도 함께 — '관찰만 있고 RCT 0편'과 '아무것도 0편'은 결론이 다르다 |
| 중복 제거 | **PMID → DOI(정규화) → 제목+연도** 3단. 제목 키는 소문자·영숫자/한글/한자만 남긴 문자열(20자 이상; 공백·구두점·그 외 문자는 **삭제**)이고, 연도가 같거나 한쪽이 미상일 때만 같은 논문으로 본다. 살아남은 레코드의 **빈 필드만** 중복본에서 채운다(값 덮어쓰기 없음) |
| 체크 태그 | Humans/Male/Female/Adult/Aged/Animals… 등 색인용 태그 **및** Treatment Outcome·Risk Factors·Surveys and Questionnaires·`… as Topic` 같은 **방법론 표제어**를 주제 분석에서 기본 제외(`--include-check-tags` 로 해제) |
| 표본 절단 | PubMed 가 보고한 전체 편수 > **실제로 받아온 편수**이면 `truncated`(연도 필터로 줄인 것은 절단이 아니다) — 연도 분포가 절단의 흔적이므로 **추세·부상/쇠퇴 출력을 생략**한다 |

> 통계 구현은 순수 표준 라이브러리입니다. 기본 테스트는 scipy 없이 **독립 손계산/브루트포스**로
> 검증하며(`tests/test_stats.py`, `tests/test_exact_ci.py`), scipy 가 설치돼 있으면
> `tests/test_scipy_crosscheck.py` 가 `benjamini_hochberg`·`mann_kendall`·`hypergeom_lower_tail`·
> `fisher_exact_two_sided`·`clopper_pearson`·`poisson_count_ci`·`reg_inc_beta` 를 각각
> `scipy.stats` 의 `false_discovery_control`·`kendalltau`·`hypergeom`·`fisher_exact`·`beta.ppf`·
> `chi2.ppf`·`special.betainc` 와 대조합니다(없으면 자동 skip).

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
- **부상/쇠퇴는 비중 변화**: 순위는 초기/최근 '비중' 차이로 매기고, 표시된 행에만 Fisher
  정확검정 p 를 덧붙입니다(순위 자체가 검정 결과는 아닙니다). 특히 편수가 적은 주제
  (예: 0→2편)는 비중 변화가 커도 p 는 유의하지 않으니 실제 편수를 함께 보세요.
- **표본 상한과 표집 편향**: 기본 최대 300편만 가져옵니다(`--max-records`). 기본 표집은
  **연도 층화**(`--sample stratified`)라 표본이 한 해로 붕괴하지 않지만, 여전히 전수는
  아닙니다. 받아온 편수가 전체 검색 결과보다 적으면 리포트가
  `PubMed 검색 결과 N편 중 M편 분석`을 밝히고 **추세·성장률·부상/쇠퇴 출력을 생략**합니다
  (공백 분석은 유지). `--count-only` 로 전체 편수를 먼저 확인한 뒤 `--max-records` 를
  그 수 이상으로 두면 전수 분석이 됩니다. 30편 미만이면 **표본 주의** 경고를 띄웁니다.
  `--min-year/--max-year` 로 기간을 좁힌 것은 절단이 아니므로 추세가 그대로 나옵니다.
- **다중검정 예산**: 검정 수 m 은 상위 K 주제쌍 중 `기대 ≥ --gap-min-expected` 를
  통과한 수입니다(상한은 `K(K−1)/2`). m 이 클수록 q 는 나빠지므로, 좁은 분야에서는
  `q ≤ 0.05` 인 후보가 하나도 없을 수 있습니다. 리포트는 **m 과 실제로 달성한 최소 q**
  를 표시하고, 0.05 를 못 넘기면 그렇다고 말합니다.
  (BH 는 `q_(i) = min_{j≥i}(m·p_(j)/j)` 이므로 q 가 `p×m` 보다 **작을 수도** 있습니다 —
  '필요한 p' 를 역산하지 않는 이유입니다. q 를 낮추려면 `--gap-top-k` 를 낮춰 검정 수를
  줄이거나 `--max-records` 를 올리세요. `--gap-min-expected` 를 낮추면 m 이 늘어
  q 는 **나빠집니다**.)
- **근거 tier 는 색인에 의존합니다**: `PublicationType` 은 NLM 색인자가 답니다. 최신
  논문은 아직 안 붙어 있을 수 있고(그래서 커버리지를 함께 보고합니다), 등록만 되고
  결과 미발표인 시험은 애초에 PubMed 에 없습니다 — "RCT 가 없다"를 "시험이 없었다"로
  읽지 말고 ClinicalTrials.gov 등을 함께 확인하세요.
- **방법론 MeSH 를 기본 제외합니다**: `Treatment Outcome`·`Risk Factors`·
  `Surveys and Questionnaires`·`Cohort Studies`·`… as Topic` 등은 연구 주제가 아니라
  색인·방법론 표제어라 주제 통계에서 뺍니다. 이 표제어 자체를 연구하는 경우
  (예: 설문 도구 개발 연구)에는 `--include-check-tags` 로 되살리세요. 전체 목록은
  `analyze.CHECK_TAGS` / `analyze.METHOD_TAGS` 에 있습니다.
- **RIS/CSV 입력은 MeSH 가 없을 수 있습니다**: 그 경우 저자 키워드를 주제로 승격하고
  리포트 첫머리에 그 사실을 밝힙니다. 키워드는 MeSH 만큼 표준화돼 있지 않아 같은
  개념이 여러 표기로 흩어질 수 있습니다(동의어 분산 → 공백이 부풀려질 수 있음).
  가능하면 PubMed XML/NBIB 를 쓰세요. RIS/CSV 의 KW 열이 MeSH 색인 표기(`*Sleep`,
  `Heart Rate/*physiology`)를 쓰면 **코퍼스 전체를 한 번에** MeSH 로 해석합니다
  (레코드마다 다르게 해석하면 통계가 깨지므로).
- **PMID 가 없는 레코드**: RIS/CSV 는 PMID 대신 DOI(`doi:…`)나 `?` 로 식별됩니다.
  `?` 이고 제목도 짧으면(정규화 후 20자 미만) 각각 고유한 것으로 취급합니다.
- **여러 파일 합치기의 한계**: `--from-file` 을 여러 번 주면 PMID → DOI → 제목+연도 순으로
  중복을 제거하고, 리포트 첫머리에 **무엇으로 몇 건을 합쳤는지** 밝힙니다. 제목 대조는
  휴리스틱이라(구두점·대소문자만 무시) 오탈자가 있는 레코드는 못 잡고, 반대로 제목과
  연도가 모두 같은 **서로 다른** 논문(같은 학회의 유사 초록 등)은 합쳐질 수 있습니다 —
  그게 걱정되면 `--no-fuzzy-dedup` 으로 PMID·DOI 만 쓰세요. 살아남은 레코드의 **빈
  필드만** 다른 출처에서 채우므로(값 덮어쓰기 없음) 편수가 이중계수되지는 않습니다.
- **연구 각도(부주제어) 축의 한계**: NLM 은 주제 범주별로 붙일 수 있는 부주제어를
  제한합니다(해부 용어에 `/drug therapy` 는 애초에 불가능). 도구는 어휘 가족 휴리스틱으로
  그런 칸에 `⚠ 규칙상 불가?` 를 붙이고 순위를 내리지만 **완벽하지 않습니다** — 표시가
  없는 행에도 규칙상 불가능한 조합이 남을 수 있고, 반대로 정당한 공백에 표시가 붙을 수도
  있습니다(특히 그 각도를 쓰는 주제가 코퍼스에 하나뿐일 때). 각 행의 PubMed 확인 링크를
  착수 전 반드시 클릭하세요. 또한 RIS/CSV 내보내기에는 부주제어가 대개 없습니다 —
  그 경우 절 전체를 "각도 분석을 낼 수 없습니다" 안내로 대체합니다(커버리지 수치는
  JSON 의 `qualifier_coverage` 에만 남습니다).
- **대상집단 축의 한계**: 이 축이 세는 것은 **색인자가 단 체크 태그**이지 실제 연구
  대상이 아닙니다. (1) 고령자를 포함한 연구라도 연령이 보고되지 않으면 태그가 없고,
  오래된 레코드일수록 성깁니다 — `Aged` 가 없다고 65세 이상을 연구하지 않은 것은
  아닙니다. 반대로 `Aged` 가 있다고 노인 대상 연구인 것도 아닙니다(한 명만 포함돼도
  붙습니다). (2) 표의 윗줄은 대개 **절대적 부재가 아니라 상대적 과소대표**입니다.
  (3) 성별 축은 포화되어 사실상 정보가 없습니다(위 참조). (4) RIS/CSV 내보내기에는
  체크 태그가 대개 없어 절 전체가 "낼 수 없습니다" 안내로 바뀝니다. (5) `확인` 링크는
  검색어·기간과 무관한 PubMed 전체 검색이므로 표의 편수와 직접 비교할 수 없습니다
  (다만 MeSH 하위어 자동 확장은 꺼 두어 표와 **같은 정의**로 셉니다).
- **`p` 와 `95% CI` 는 서로 다른 모형입니다**: `p` 는 초기하(양쪽 주변합을 고정),
  `95% CI` 는 관측 편수의 포아송 구간으로 **기대값을 오차 없는 상수로 봅니다**. 그래서
  `p<0.05` 인데 CI 상한이 1 을 넘는 행이 정상적으로 나옵니다(특히 주변합이 코퍼스의
  큰 비중을 차지할 때 CI 는 보수적으로 넓어집니다) — 그럴 땐 **보수적인 CI 쪽**을
  따르세요.
- **`--min-year/--max-year` 를 쓰면 층화 표집이 꺼집니다**: 그 창 안에서 최신순으로
  받습니다. 창 안 결과가 `--max-records` 보다 많으면 최신 쪽으로 치우친 표본이 되므로,
  `--count-only` 로 창 안 편수를 확인해 `--max-records` 를 그 이상으로 두세요.
- **`--from-file` 에서 무시되는 옵션**: `--years`·`--save-xml` 은 네트워크 조회 전용이며,
  파일 분석에서 지정하면 무시된다고 알려 줍니다(기간 제한은 `--min-year/--max-year`).
- NCBI E-utilities 예절을 위해 `--email`(가능하면 `--api-key`) 지정을 권장합니다.
  API 키는 리포트·실행정보에 **절대 기록되지 않고**, 오류 메시지에서도 가려집니다.

- **전수 검증의 의미**: `--verify-gaps` 는 같은 검색 제한 안에서 두 주제의 실제 동시색인
  편수를 다시 조회합니다. PubMed 는 MeSH 상하위어를 **자동 확장(explode)** 하므로,
  부모×자식 쌍은 전수에서 lift 가 1 이상으로 나와 자동으로 걸러집니다. 반대로 이 때문에
  전수 편수는 리포트 표본의 `함께(관측)` 값과 **정의가 달라** 직접 비교할 수 없습니다
  (표본은 문자 그대로의 동시부여, 전수는 계층 확장 포함).
- **`--top-evidence K` 는 행 수가 아닙니다**: 상위 K 주제 중 **3편 이상**인 것만 검정합니다
  (그 미만은 2×2 표가 무의미). 부상/쇠퇴 표도 각각 최대 8행으로 고정돼 있습니다.
- **`--years N` 은 달력 연도가 아니라 `N×365+1`일 전부터**입니다(PubMed `reldate`).
  경계 연도는 일부만 포함됩니다. `--sample stratified` 는 달력 연도로 층화합니다.
- **자원 상한**(넘으면 rc 2 로 명확히 거부): 입력 파일 64MB(.gz 는 해제 후 64MB),
  HTTP 응답 256MB, CSV 필드 16MB, `--gap-top-k` 최대 200, `--years` 최대 100.
  전수 검증은 상위 30쌍까지만 조회합니다(쌍마다 PubMed 요청이 하나씩 필요).
  발행연도는 1500~2200 범위만 인정합니다 — 범위 밖 값은 '연도 미상'으로 처리해,
  손상된 레코드 하나가 추세 계산을 마비시키지 못하게 합니다.

### 종료 코드 (스크립트 연동용)

| rc | 의미 |
|---:|---|
| 0 | 정상 — 리포트 생성 |
| 1 | 입력은 정상이나 **분석할 논문이 0편** (검색어/기간 조정 필요) |
| 2 | 사용자 입력 문제 — 잘못된 옵션, 파일 없음/권한/디렉터리, 형식 판별 실패, 출력 경로 오류 |
| 3 | 조회·분석 중 예기치 못한 오류(네트워크/PubMed 오류 포함) |

## License
MIT © 2026 hyeonjoong
