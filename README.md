# weekly-tools — 주간 자동 제작 툴 모음

매주 드문 드문 유용한 툴을 하나씩 만들어 이 저장소에 하위 폴더로 추가·커밋합니다.
같은 폴더가 내 컴퓨터(`~/Downloads/02_프로젝트/깃헙/`)에 그대로 쌓여 바로 실행할 수 있습니다.

- 폴더명은 `이름-역할` 규칙 — 한눈에 뭐하는 툴인지 보입니다.
- 각 폴더의 `실행.command`를 더블클릭하면 바로 동작을 볼 수 있고, `사용법.md`에 한글 안내가 있습니다.

## 설치된 툴

| 툴 (폴더) | 한 줄 설명 | 바로 실행 | 추가일 |
|----------|-----------|----------|--------|
| [revcheck-리비전응답점검](revcheck-리비전응답점검) | **제출본 · 개정본 · 응답서(point-by-point) 세 파일을 동시에 읽어**, 응답서에 적은 약속이 개정 원고에 실제로 반영됐는지 대조: **리뷰어 코멘트 번호 전수 점검**(2-4 다음이 2-6이면 치명 — 사람은 '없는 번호'를 못 본다), **응답서가 인용한 '개정 후 문구'가 개정본에 문자 그대로 있는가**(없으면 치명 + 가장 가까운 문장을 일치율과 나란히; 어긋난 숫자가 있으면 일치율 99%여도 치명, 축약 인용은 경고), **응답서에 연결되지 않은 채 있던 값이 바뀐 문단**(치명 — 숫자가 덧붙기만 한 것은 아님), 제출본 오첨부 사고, 검증 불가한 변경 주장, 위치 참조(`.docx` 는 줄 번호가 없어 **확인불가로 정직하게 강등**), 참고문헌·그림·표 증감. `리비전점검.md`/`문제목록.csv`/`변경목록.csv`/**`추가문헌.csv`(citecheck 입력 스키마 그대로)**. 변경내용 추적이 켜진 워드 파일은 **어느 상태로 읽었는지 첫 줄에 명시**하고, 코멘트 번호 체계를 못 잡으면 추측 대신 **종료코드 3(판정불가)** 으로 멈춤. 커버리지 자백 필수 출력. `numcheck`(숫자)·`draftcheck`(형식)·`citecheck`(DOI)가 원고 **1개**를 투고 직전에 보는 3종 세트라면, revcheck 는 원고 **3개**를 리비전 직전에 본다 (외부 의존성 0, 네트워크 0, 원본 읽기 전용, 204개 테스트, 적대적 서브에이전트 7명이 2라운드로 찾은 결함 40여 건 수정 — `HARDENING.md`) | `실행.command` 더블클릭 · 또는 `revcheck --old 제출본.docx --new 개정본.docx --response 응답서.docx --out-dir 결과` | 2026-08-14 (금) |
| [numcheck-원고수치검증](numcheck-원고수치검증) | 원고(.docx/.md/.tex/.txt) 한 개 → **본문에 적힌 숫자를 전부 다시 계산해 대조**: 비율(`23/48 (45.2%)` → 47.92%), 보고된 검정통계량에서 나오는 p(자체 구현 t/F/χ²/z/r CDF, scipy 대비 ≤1e-9), 하위군 N 합계, **GRIM/GRIMMER**(N = 23 에서 ISI 평균 14.37 은 존재할 수 없음 — SERENE 1차 지표가 ISI), 변화량(사후−사전), 신뢰구간 포함·CI↔p 모순, `p = .07` 인데 "유의하게"라고 쓴 문장. 줄번호가 붙은 한국어 지적 목록 + `문제목록.csv`/`재계산표.csv`. **반올림·버림·올림을 모두 허용하는 구간 비교**로 오탐을 막고, 단측검정·Greenhouse–Geisser 등 단서가 있으면 치명 → 경고로 자동 강등하고 강등 사유를 적음. **리포트 첫머리에 '후보 N개 중 재계산 M개 / 건너뜀 K개(사유별)'를 반드시 출력** — 재계산 가능 claim 이 5개 미만이거나 **원고를 잘라 읽었으면** 조용히 통과시키지 않고 종료코드 3. 산술적으로 불가능한 보고(`r = 1.5`, `t(0)`, 음수 χ²)도 치명으로 지적. `draftcheck`(형식)·`citecheck`(DOI)와 함께 원고 3종 세트 (외부 의존성 0, 네트워크 0, 원본 읽기 전용, 415개 테스트, 적대적 서브에이전트 11명이 3라운드로 찾은 결함 80건 수정 — `HARDENING.md`) | `실행.command` 더블클릭 · 또는 `numcheck 원고.docx --out-dir 검토` (GRIM 은 `--scale ISI=0:28:7`) | 2026-08-13 (목) |
| [joinaudit-데이터병합감사](joinaudit-데이터병합감사) | 출처가 다른 여러 CSV/TSV/XLSX(워치 HRV·호흡·EEG 요약·수면일기·ISI·UT 로그) → **피험자 × 시점 한 장의 분석용 표 + 병합 감사**: 피험자 ID 표기 정규화(`S01`/`S1`/`BELL-001-01`/전각/공백 — **퍼지 매칭 없음**, `S01`과 `S02`는 절대 안 붙음), **자정 넘김 야간 귀속**(23:40과 다음날 03:20은 같은 밤), **중복 키에서 카테시안 조인 원천 차단**(pandas `merge`가 조용히 행을 곱하는 자리), **파일 간 키 겹침 검사**(표는 나왔는데 아무것도 안 붙은 상태 적발), N-흐름(입력→최종, 드롭 사유별 행 목록)·커버리지 매트릭스·**논문 Methods 초안(한/영)**. 산출물 `merged.csv`는 `longistat`에 그대로 투입(시점별 표라 `statwise`/`table1` 앞에는 피험자당 1행 요약이 필요하며, 리포트가 그 유사반복 경고를 실행마다 계산). **확신이 없으면 추측해서 붙이는 대신 종료코드 3으로 멈춤** (외부 의존성 0, 네트워크 0, 원본 읽기 전용, 331개 테스트, 적대적 서브에이전트 4명이 찾은 결함 26건 수정 — `HARDENING.md`) | `실행.command` 더블클릭 · 또는 `joinaudit 워치.csv 일기.xlsx 설문.csv --align night --out-dir 결과` (먼저 `--inspect`) | 2026-08-07 (금) |
| [draftcheck-원고투고점검](draftcheck-원고투고점검) | 투고 직전 원고(.docx/.md/.tex/.txt) → **자기 정합성 전수 대조**: 본문 인용↔참고문헌(목록에 없는 번호·한 번도 인용 안 된 문헌·번호 순서), 그림/표 번호(미언급·유령 번호·건너뜀), 초록↔본문 표본수 불일치, 통계 보고(p=0.000·임계값만·효과크기/CI 누락), 약어 정의, 저널 분량 한도 — 줄번호 붙은 한국어 수정 목록 + `references.csv`로 citecheck 연결. **인식 못 하면 '이상 없음'이 아니라 '점검 불가'로 크게 경고** (외부 의존성 0, 네트워크 0, 원본 읽기 전용, 274개 테스트) | `실행.command` 더블클릭 · 또는 `draftcheck 원고.docx --limits 저널.json --out-dir 점검결과` | 2026-08-06 (목) |
| [table1-기저특성표](table1-기저특성표) | 임상 CSV → **출판용 "표 1(기저 특성표)"** 자동 생성: 변수별 연속/범주 자동 판별 → 알맞은 요약(평균±SD / 중앙값[IQR] · n(%))·검정 자동 선택 + **군간 표준화평균차(SMD)**·결측 정리, Markdown/CSV/TSV/JSON 출력 — BELL-001 SERENE 등 임상시험 Table 1용 (외부 의존성 0, 121개 테스트 통과) | `실행.command` 더블클릭 · 또는 `table1 data.csv --group arm` | 2026-07-16 (목) |
| [agreestat-측정일치도](agreestat-측정일치도) | 두 측정방법 CSV → **일치도 분석**: Bland–Altman(bias·95% LoA+CI·비례편향 검정)+ICC(2,1)/(3,1)+Lin CCC+반복측정 CV — 비접촉 호흡/워치-HRV vs PSG/밴드 검증용, 논문 문장까지 (외부 의존성 0, ICC를 Shrout&Fleiss 정확값과 대조) | `실행.command` 더블클릭 · 또는 `agreestat data.csv -a 방법A -b 방법B [-s 대상id]` | 2026-07-13 (월) |
| [eegband-뇌파대역분석](eegband-뇌파대역분석) | 단일채널 EEG CSV → **대역파워**(delta/theta/alpha/beta/gamma 절대·상대)+**서파활동(SWA)**+SEF95·peak·slowing ratio, 에폭 단위 요약 — 직접 구현한 Welch PSD (BELL-001 EEG 서파수면 지표용, 외부 의존성 0, scipy welch와 ≤1e-14 일치) | `실행.command` 더블클릭 · 또는 `python3 -m eegband.cli eeg.csv --fs 128 --epoch 30` | 2026-07-13 (월) |
| [hrvkit-심박변이도분석](hrvkit-심박변이도분석) | RR/IBI(또는 HR) CSV → **HRV 지표 한 번에**: 시간영역(SDNN·RMSSD·pNN50)+주파수영역(LF/HF, 직접 구현한 FFT·Welch PSD)+비선형(Poincaré SD1/SD2·SampEn), 이소성박동 자동 보정 — BELL-001 호흡→부교감→RSA/HRV 기전 정량화 (외부 의존성 0, scipy와 ≤1e-6 일치) | `실행.command` 더블클릭 · 또는 `python3 -m hrvkit.cli rr.csv` (또는 `--json`) | 2026-07-13 (월) |
| [statwise-그룹비교통계](statwise-그룹비교통계) | 두 그룹/여러 그룹 CSV → **정규성(Shapiro-Wilk)·등분산(Levene) 자동 점검** 후 알맞은 검정(Student t·Welch·Mann-Whitney·ANOVA·Kruskal-Wallis)을 골라 실행 — 효과크기(Hedges g·rank-biserial·η²)+95% CI+Holm 보정 사후검정+**논문용 APA 문장**까지 (외부 의존성 0, scipy와 p값 ≤1e-9 일치, 오프라인 예제 포함) | `실행.command` 더블클릭 · 또는 `statwise 데이터.csv --value 값열 --group 그룹열` (또는 `--wide`) | 2026-07-10 (금) |
| [pubgap-논문공백탐색](pubgap-논문공백탐색) | 키워드 → PubMed 최근 동향 요약(연도별 발행량·주요 저널/주제·부상/쇠퇴)과 **덜 연구된 각도(연구공백)** 제안 — MeSH 공동출현 lift + 초기하 p값으로 "개별로는 흔한데 함께는 드문" 조합을 논문 주제 후보로 (외부 의존성 0, 오프라인 데모 포함) | `실행.command` 더블클릭 · 또는 `python3 -m pubgap.cli "slow breathing AND sleep" --email 내메일` | 2026-07-09 (목) |
| [factorscan-설문요인분석](factorscan-설문요인분석) | 설문 척도 CSV → 요인분석 적합성(KMO·Bartlett)·요인 수(고유값/Kaiser/평행분석)·요인적재량(Varimax)·공통성·수정된 문항-총점 상관을 한 번에 진단 (numpy만; SPSS식 척도 타당도 표를 재현가능하게) | `실행.command` 더블클릭 · 또는 `python3 -m factorscan.cli 설문.csv --config 설정.json` | 2026-07-02 (목) |
| [logflow-사용자로그분석](logflow-사용자로그분석) | 사용자 이벤트 로그 CSV → 세션화·이벤트/사용자별 집계·DAU/WAU/MAU·리텐션(코호트 day-N)·퍼널 전환율을 한 번에 요약 (표준 라이브러리만, tz 보정·결측 처리 지원) | `실행.command` 더블클릭 · 또는 `python3 -m logflow.cli 로그.csv --funnel 단계1,단계2` | 2026-06-29 (월) |
| [surveyscan-설문응답분석](surveyscan-설문응답분석) | 설문 응답 CSV → 문항별 기술통계·결측 요약·역문항 자동 재코딩·하위척도 점수·Cronbach α(신뢰도)·문항-총점 상관·문항제거시 α (표준 라이브러리만) | `실행.command` 더블클릭 · 또는 `surveyscan 설문.csv -c 설정.json` | 2026-06-26 (금) |
| [paperforge-논문아이디어발굴기](paperforge-논문아이디어발굴기) | 보유 데이터(EEG·워치·호흡·설문·MoA) 매니페스트에서 멀티모달 논문 아이디어 매트릭스 생성 — 가설·변수·분석법·저널·표본 실현가능성 | `실행.command` 더블클릭 · 또는 `paperforge manifest.json` | 2026-06-25 (목) |
| [citecheck-인용DOI검증](citecheck-인용DOI검증) | 원고 인용/DOI를 Crossref로 검증 — 깨진 DOI·메타데이터 불일치·철회 탐지 | `실행.command` 더블클릭 · 또는 `citecheck refs.bib` | 2026-06-25 (목) |

> `citecheck`는 단독 저장소([github.com/hyeonjoong/citecheck](https://github.com/hyeonjoong/citecheck))로도 공개돼 있습니다. 이후 만드는 툴은 이 모노레포 하위 폴더로 쌓입니다.

## 새 툴 바로 쓰는 법

각 툴 폴더에서 한 번만 설치하면 명령어가 전역으로 등록됩니다:

```bash
cd ~/Downloads/02_프로젝트/깃헙/<폴더이름>
python3 -m pip install -e .
```

또는 그냥 폴더 안의 **`실행.command`를 더블클릭**하세요.

## 최신 상태로 당기기

자동 실행이 새 툴을 푸시하므로, 가끔 최신으로 맞추려면:

```bash
cd ~/Downloads/02_프로젝트/깃헙
git pull
```
