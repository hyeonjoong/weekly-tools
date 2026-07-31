# HARDENING.md — eegband 적대적 하드닝 기록

이 문서는 `eegband` 도구에 대한 다중 라운드 적대적 검토(correctness / edge-cases /
usefulness / docs-honesty / test+PII)와 그에 따른 수정 내역을 라운드별로 남깁니다.

---

## Round 1 — 2026-07-16

**방법.** 독립 리뷰어 5명을 병렬로 투입(각각 correctness·edge-case·usefulness·
docs-honesty·test/PII 담당). numpy 2.4.3 / scipy 1.17.1 설치 환경에서 1차 원리부터
재계산·재현하여 검증. DSP 코어는 결함 없음으로 확인(`welch_psd` vs `scipy.signal.welch`
최대 상대오차 ≤ 3e-15, FFT vs O(n²) DFT ≤ 1e-9, 정현파 Parseval 일치). 발견된 결함은
모두 코어 밖(입력 처리·리포팅·문서·테스트)에 있었음.

### 수정한 결함

1. **[MAJOR] 시간 열 오탐 → 올바른 `--fs`를 조용히 덮어씀** (`dataio.py`).
   `_TIME_NAMES`에 `sample`/`samples`/`index`/`idx`/`ms`/`time_ms`가 포함되어, 정수
   표본 카운터(0,1,2,…)를 **초 단위 시간**으로 오인 → `infer_fs`가 fs=1 Hz 등을 산출하고
   `resolve_fs`가 사용자의 `--fs 128`을 덮어써 모든 주파수/파워가 128배 왜곡.
   → 카운터류 이름을 자동감지에서 제거, `time_ms`/`ms`는 밀리초로 명시 처리(초로 변환),
   자동감지는 명확한 초 단위 이름으로 한정.

2. **[HIGH] 미포착 크래시: 비단조/동일 타임스탬프** (`cli.py`).
   `resolve_fs` 호출이 `try/except` 밖에 있어 시간 열이 감소/정체하면 `ValueError`가
   그대로 트레이스백으로 노출. → `load_signal`+`resolve_fs`를 한 `try/except (ValueError,
   OSError)`로 묶어 `입력 오류: …` 메시지로 exit 2 처리.

3. **[MEDIUM-HIGH] 미포착 크래시: 디렉터리/권한 경로** (`cli.py`).
   `except (ValueError, FileNotFoundError)`가 `IsADirectoryError`/`PermissionError`를
   못 잡아 트레이스백. → `except (ValueError, OSError)`로 확장.

4. **[MAJOR, 커스텀 밴드] total 행 상대파워를 `100.0`으로 하드코딩** (`report.py`).
   `--bands`에 겹침/빈 구간이 있으면 밴드 상대파워 합이 100%가 아닌데도 total 행이 항상
   `100.0`을 출력(6 Hz 파워가 4–8 Hz 빈 구간에 있으면 밴드 0.0%/0.0%인데 total 100.0).
   → total 행에 실제 상대파워 합(`rel_sum`)을 출력하고, 비연속/겹침 밴드는 커버리지 경고 추가.

5. **[MISLEADING] 상수(영파워) 신호에서 peak를 0.5 Hz로 보고** (`analyze.py`).
   영파워일 때 `peak_frequency`가 첫 빈을 반환. → 총파워 0이면 peak=None(`n/a`)로 가드.

6. **[MISLEADING] 우세 대역 동률을 부동소수 잡음으로 결정** (`analyze.py`/`report.py`).
   → 상위 두 대역이 1% 이내면 `dominant_tie` 플래그 설정, 리포트에 `⚠ near-tie` /
   에폭 표에 `*` 표시, JSON에 `dominant_tie` 노출.

7. **[MINOR] SEF sub-bin 근사 오차** (`spectral.py`).
   누적-대-주파수 선형 보간은 세그먼트 내 누적이 2차식이라 sub-bin 오차 발생(삼각 PSD에서
   SEF50 오차 −0.028). → 세그먼트 내 2차식 근을 **정확히** 풀도록 교체(삼각 PSD에서
   √8, √15.2와 기계정밀도 일치).

8. **[UX/견고성] 비 UTF-8 CSV** (`dataio.py`).
   `utf-8-sig`만 열어 cp949(한국 Excel)·latin-1 파일이 실패(포착은 되나 메시지가 난해).
   → utf-8-sig → cp949 → latin-1 순으로 자동 디코드, 사용한 인코딩을 경고로 표시.

9. **[COSMETIC] `--bands`/`--sef` 오류의 exit code 불일치** (`cli.py`).
   `_parse_bands`가 `SystemExit`(exit 1, 접두어 없음) → `ValueError`로 바꿔 다른 입력
   오류와 동일하게 `입력 오류: …` exit 2로 통일.

10. **[DOCS-FALSE] README `python3 -m unittest -q`가 0 테스트 실행**.
    → `python3 -m unittest discover -s tests -q`로 수정, 테스트 수 50→79 갱신.

11. **[DOCS-IMPRECISE] "모든 표준 대역 경계가 정확히 빈에 떨어진다"**.
    사용자가 `--fs 128`을 직접 준 경우에만 성립(추정 fs≈127.9999에서는 ~1e-8 벗어남).
    또한 "빈 정렬 ≠ 0.5 Hz 분리 해상"(Hann 주엽 폭 ≈1 Hz). → 문서를 정확히 한정·보강.
    SEF95 예시 수치 1.95→1.89 Hz(정확 보간 반영), 에폭 표 SEF 수치도 갱신.

### 추가한 기능 (임상 유용성)

- **`--csv`**: 에폭별(없으면 전체) 대역파워 표를 CSV(stdout)로 출력 — R/SAS/Prism에서
  1차 종말점 통계를 바로 돌릴 수 있게. 파일 쓰기 없음(리다이렉트로 조합), `#` 주석행에
  버전·fs·입력파일 자기기술. 리뷰어가 꼽은 최우선 미비 기능.
- **에폭 분산**: 요약에 상대 delta와 절대 SWA의 `mean±SD (n=…)` 추가(종말점은 평균 SWA이므로
  산포 추정이 필요).
- **재현성 프로버넌스**: 리포트 Info 행에 `eegband vX.Y` + 입력파일, JSON에 `tool`/`version`/
  `source_file` 추가.

### 추가한 테스트

- `tests/test_cli.py`(신규): `main(argv)` 종료코드(성공 0 / 없는파일·디렉터리·잘못된
  `--sef`·`--bands`·비단조 시간 = 2), `--json`/`--csv` 상호배제, cp949 무크래시, JSON
  스키마·비유한→null, `--csv` 행수/파싱.
- `tests/test_properties.py`(신규): Parseval(다중 시드), 적분 가법성, SEF 단조성·정확성,
  peak 정확·영파워 None, `band_ratios` inf/nan 분기, 우세 동률, gappy 밴드 커버리지 경고,
  표본카운터 미탐·ms 변환·불규칙 fs 경고, `render_text` 무예외(상수/에폭/gappy), 인코딩 폴백.

**결과: `python3 -m pytest -q` → 79 passed(50→79). `unittest discover` 동일 통과.**
DSP 코어는 이미 견고했고, 이번 라운드는 입력 오탐(1건 심각)·미포착 크래시(2건)·오해 유발
출력(3건)·문서 오류(2건)를 제거하고 임상 워크플로 기능을 보강.

---

## Round 2 — 2026-07-16

**방법.** 새 리뷰어 3명 병렬 투입 — (1) Round-1 수정의 정확성/무회귀 검증 + 신규 코드
공격, (2) 신규 코드(인코딩 폴백·ms 변환·`--csv`·프로버넌스) 엣지케이스 + 회귀 배터리,
(3) 문서 재현성 + 임상 유용성 재점검. numpy/scipy로 수치 재검증.

**핵심 결과: 신규 버그·회귀 없음.** 모든 Round-1 수정이 정확함으로 재확인.
- SEF 2차 정확해: 무작위 조각선형 PSD 3500개 × 7 분위 = 최대오차 1.30e-09, 실제 Welch
  PSD 20개 = 4.77e-10, 단조성 398,000회 검사 0위반. `m<0`(하강 PSD)에서도 올바른 근 선택,
  음수 PSD 불가(방어코드는 도달 불가)까지 증명.
- `rel_sum`=Σ상대(기본대역 정확히 1.0), gap/overlap 경고 정확·기본대역 미발생 확인.
- `dominant_tie`는 리더 대비 상대허용(1%), 1밴드/영파워에서 무크래시.
- 상수신호 peak=None이 overall·에폭·리포트·CSV·JSON 전 경로에서 안전.
- 문서 수치 전부 문자단위 재현(delta 1072.564 / total 1086.536 / peak 1.50 / SEF95 1.89 /
  epoch 98.6·98.8% / mean±SD / alpha 88.0%·10 Hz), 테스트 79 확인.

### 이번 라운드에 반영한 개선(모두 LOW/폴리시 — 감사추적·CSV 인체공학)

1. **CSV 프로버넌스 자기재현성** (`report.py`). 기존 `#` 주석이 4-필드 CSV 행이라 순진한
   `csv.DictReader`가 가짜 헤더로 오독 → **단일 필드**로 변경하고 전체 분석 파라미터
   (nperseg/noverlap/nfft, sef 분위, 밴드 정의, 보간표본수, 인코딩, 입력파일)를 담아
   내보낸 에폭 CSV만으로 재현 가능하게 함.
2. **JSON `provenance` 블록**(`report.py`): `sef_percent`·`n_interpolated_samples`·
   `input_encoding` 추가.
3. **산포 라벨 명확화**(`report.py`): `± SD (표본 SD, n-1)` 명시 + 종말점 평균을 위한
   **SEM** 추가.
4. **비수치 값 열 오류 힌트**(`dataio.py`): "no numeric values"에 구분자/인코딩(UTF-16 등)
   힌트 추가 — UTF-16 등에서 혼란스러운 메시지 완화.
5. **`--json`/`--csv` 조기 배타 체크**(`cli.py`): `analyze()` 이전으로 이동(불필요 계산 방지).
6. 회귀 테스트 추가(`test_cli.py`): JSON provenance 블록, CSV 주석 단일필드, 비수치 힌트.

**결과: `python3 -m pytest -q` → 82 passed(79→82). `unittest discover` 동일 통과.**
버그는 발견되지 않았고, 감사추적/이식성 관점의 폴리시만 반영.

---

## Round 3 — 2026-07-16 (확인 라운드)

**방법.** 새 리뷰어 2명 — (1) 두 라운드가 놓쳤을 수 있는 결함을 가정 없이 독립 재검토,
(2) 문서·통합 최종 검증. numpy/scipy로 수치 재도출.

**핵심 결과: 물질적(정확성) 버그 0건.** 두 라운드 연속 클린.
- Welch vs scipy: nperseg∈{2,3,4,5,512,n}, noverlap∈{0,100,199,128} 모두 ≤2.8e-16 일치.
  마지막 부분 세그먼트 드롭·`n_seg`·Nyquist 비배가 정확. 2-톤 대역파워(delta 8.0, beta 4.5)
  정확. SEF 정확성: 무작위 300 스펙트럼+하강+단일빈 스파이크 vs 이분법 오라클 최대오차 8e-13.
- 수치 극단(진폭 1e12/1e-12, DC 1e9 오프셋 detrend 제거, 임펄스, 전부 음수) 모두 유한·정확.
- **결정성**: 별도 프로세스 2회 실행에서 JSON·CSV 바이트 동일(집합순서 비의존).
- 상호작용(에폭+커스텀밴드, 에폭<세그먼트 클램프, nperseg>epoch_len, sef+단일밴드,
  time+fs불일치+에폭) 모두 정상. `--json --csv` 조기 거부. n=1 에폭 SD/SEM 0-가드.
- render_csv 단일필드 프로버넌스: 경로에 콤마/밴드명에 개행이 있어도 `csv.writer`가 인용→
  정확히 1필드. `analyze()`를 CLI 없이 호출 시 encoding 기본 utf-8-sig, JSON은 null(문자열
  'None' 미출력).

### 반영(비물질 관찰 1건 — 문서 정직성)

- **선형 추세 누설 명시**(README): 디트렌드가 세그먼트별 평균 제거(scipy 기본)라 선형 드리프트가
  delta로 새어들 수 있음을 한계로 명시하고 사전 고역통과 필터를 권고.

비물질 관찰(수정 불요): CSV `#` 프리앰블은 소비 시 1행 스킵 필요(코드에 의도 명시),
per-epoch 계산은 정확하나 리포트의 nperseg는 overall 기준(외형만), `--bands`가 과학표기
경계(`1e-3`)를 거부(실제 EEG 경계엔 불필요).

**최종: 82 passed. 3라운드(발견→수정→2회 클린 확인) 완료. DSP 코어는 시종 정확했고,
입력 처리·리포팅·문서·테스트·임상 워크플로를 하드닝하여 종료.**

---

## Round 4 — 2026-07-16 (심층 기능 확장 + 적대적 하드닝)

이전 3라운드가 기존 기능을 하드닝했다면, 이번 라운드는 **임상 유용성을 실질적으로 확장**하고
그 신규 코드를 다시 5인 병렬 적대 검토로 굳혔습니다. 모든 신규 DSP는 scipy/numpy로 1차
원리부터 재검증(≤4e-14)했습니다.

### 추가한 기능 (실질 임상 확장)

1. **Welch 디트렌드 모드 `--detrend {constant,linear,none}`.** `linear`(세그먼트별 최소제곱
   직선 제거)는 느린 드리프트가 delta/SWA로 새는 것을 막음 — 3라운드에서 한계로만 명시했던
   것을 실제 기능으로 구현. scipy `detrend='linear'`와 ≤2.3e-15 일치.
2. **강건 평균 `--average {mean,median}`.** 세그먼트 주기도 중앙값 + 편향보정(`median_bias`,
   scipy `_median_bias`와 ≤5.6e-16 일치) — 일시적 아티팩트(움직임·근전위)에 강건. n_seg=1/2
   포함 scipy `average='median'`과 ≤3.9e-14 일치.
3. **스펙트럼 엔트로피.** 대역 내 PSD 빈을 확률분포로 본 정규 섀넌 엔트로피(총 빈 수로 정규화).
4. **대역별 피크 + IAF(개인 알파 주파수), 뚜렷함(prominence) 게이팅.** 대역 내부 국소 최대이며
   양쪽 경계보다 크고 대역 중앙값의 3배 이상일 때만 "피크"로 보고 — 1/f 잡음 argmax를 억제
   (실측: 실 스핀들/알파 20–2500×, 잡음 <2×, 3× 문턱이 명확 분리).
5. **신호 품질/아티팩트 지표 `[0]`.** 진폭 min/max/ptp/RMS + 클리핑(반복되는 레일 고정)·평탄
   구간(≥3 연속 동일값)·보간 비율. 순수 기술 지표.
6. **에폭 아티팩트 제거 `--max-amp T`.** 최대 |진폭| > T µV 에폭을 `✗REJ` 표시하고 SWA 요약
   (mean/SD/CI/density)에서 제외 — 품질 지표를 실제 종말점 계산에 반영(리뷰어 최우선 미비점).
7. **에폭 요약 통계 확장.** 기존 mean±SD·SEM에 **t-기반 95% CI(df>30은 Cornish–Fisher 전개로
   불연속 제거, scipy t.ppf와 ≤3e-8)·중앙값·IQR·범위** 추가. 자기상관 경고 명시.
8. **CSV 인체공학.** `--no-comment`(base-R/SAS용 순수 사각형), 대역비 3종·엔트로피·대역별
   피크·(제거 시)`peak_amp_uv`/`rejected` 열 추가, 프로버넌스에 detrend/average/max_amp 포함.

### 적대 검토에서 고치거나 개선한 것 (5인 병렬, 신규 코드 집중)

- **[MEDIUM] 클리핑 오탐**: 연속 신호의 유일한 전역 min/max 2개가 클리핑으로 계수되어 n<100에서
  가짜 "ADC 포화" 경고. → 레일 값이 **2회 이상 반복**될 때만 클리핑으로 계수하도록 수정.
- **[MISLEADING] IAF가 실재하지 않는 알파에서도 확정 보고**: 1/f 위 잡음 argmax를 IAF로 출력.
  → prominence 게이팅으로 뚜렷한 피크만 표시, 아니면 `n/a`.
- **[MISLEADING] "95% CI"가 유사복제(pseudoreplication)**: 기록 내 에폭은 자기상관이라 CI가
  불확실성을 과소평가. → 리포트/JSON/문서에 "기록 내 분포이며 피험자간 추론 CI 아님" 명시.
- **[LOW] `_t_crit` df>30 불연속**: z=1.96 고정으로 n=32에서 CI가 급격히 좁아짐. → Cornish–Fisher
  t-분위 전개로 대체(연속·정확).
- **[LOW] 엔트로피 정규화 분모**: 양수 빈 수로 나눠 반쯤 빈 대역이 1.0. → **총 대역 빈 수**로 정규화.
- **[LOW] 에폭 표에서 무전력 에폭 peak/SEF가 `0.00`**: `n/a`로 수정(overall과 일관).
- **[LOW] Nyquist 초과 대역에서 total 범위가 역순(`30–4`)** 표시: `_range_safe`로 방지.
- **[LOW] linear 디트렌드 nperseg≤2**: 자유도 소진으로 PSD≈0 → 이를 설명하는 경고 추가.
- **테스트**: 약한 테스트 3건 강화(median_bias 단조감소·near-tie 플래그·평탄 플래그 실제 검증),
  회귀 테스트 다수 추가(클리핑 오탐 방지, prominence, 아티팩트 제거, overlap/too-short 경고,
  단일 에폭 CI 붕괴, df>30 t-분위, 빈 대역 peak=None, 엔트로피 총빈 정규화). 82→**145 passed**.
- **PII**: 도구는 완전 오프라인·무파일쓰기·무주입(재확인). 입력 **파일명**만 프로버넌스에 기록되므로
  파일명에 PHI를 넣지 말라는 주의를 문서에 추가.

**결과: `python3 -m pytest -q` → 145 passed(82→145). `unittest discover` 동일. DSP 신규 코드는
scipy 기준 기계정밀도로 일치했고, 발견된 지적은 전부 출력/라벨링·문서 정직성 및 임상 워크플로
확장이었음. 도구는 이제 드리프트 제거·강건 평균·아티팩트 제거·엔트로피/IAF·품질 지표까지 갖춘
실사용 수면/약물 EEG 분석기로 확장됨.**

### 검증 라운드 (신규 코드 대상 2차 3인 병렬 적대 검토)

Round-4 수정/기능을 새 3인 패널(correctness·edge-case·docs/integration)로 재공격:
- **correctness/regression: 클린.** 아티팩트 제거 요약이 채택 에폭만 사용함을 손계산으로 확인,
  `_t_crit` df>30 CF 전개 vs scipy ≤2.6e-8·불연속 없음, 엔트로피 총빈 정규화(반빈 대역=0.5),
  prominence 3× 게이트, 클리핑 반복-레일, median/detrend 전 모드 scipy 재일치(≤5.3e-15).
- **edge-case: 크래시/오답 0.** `--max-amp` 음수/0/전량제거/전체단일에폭/거대값/median+linear+커스텀
  밴드/비UTF8, prominence 경계, 엔트로피 1빈/영빈, df>30 CI, `--no-comment` 파싱, 53만 표본 2.3s.
- **docs: 전 수치 재현·과대표현 없음.**
- 2차에서 나온 저심각 관찰 2건도 반영: (1) `--max-amp`를 `--epoch` 없이 준 경우 CSV에 빈 rej 열이
  붙던 것 → 에폭이 있을 때만 열 추가. (2) 라이브러리 직접호출로 NaN/inf를 넘기면 조용히 NaN 스펙트럼
  → `analyze()`가 비유한 입력을 명확한 오류로 거부(CLI는 `load_signal`이 보간하므로 무영향). 145 passed.

---

## 2026-07-31 — 기능 확장(전원잡음 진단·제거 / 기저 대비 변화) + 4인 병렬 적대 검토 1라운드

v0.2.0 → **v0.3.0**. 테스트 **359 → 506 passed**(+1 skip). scipy/numpy 없이도 486 passed.

### 새로 추가한 기능 (임상/약동학 실사용 관점)

1. **전원(50/60 Hz) 잡음 진단·제거** — 신규 `eegband/linenoise.py`.
   - 각 고조파의 ±`--line-bw` 창 최고 PSD를 **주변 배경 중앙값**과 비교(기본 3×)해 검출.
     `excess(µV²)`는 배경을 뺀 초과 파워로, 진폭 A 정현파에서 **정확히 A²/2** (손 검산 가능).
   - `--notch` 는 해당 빈을 **log-선형 스펙트럼 보간**으로 대체한 뒤 대역파워·SEF·엔트로피·
     1/f 적합을 계산. PSD 위에서 처리하므로 시간영역 노치의 링잉·위상왜곡·경계효과가 없음.
   - **에일리어싱**: `fs/2 < f₀` 면 `|f₀−k·fs|` 로 접혀 들어옴(fs=100에서 60 Hz → 40 Hz).
   - `--line-freq auto|off|HZ`, `--line-bw`, `--notch`.
2. **기저(baseline) 대비 변화 검정** — `--baseline SEC`.
   - 지표별 Δ·Δ%·95% CI·**Hedges' g**·**Welch t**·**BH-FDR q**. 각 기록이 자기 자신의 대조군.
   - 연속 에폭은 독립이 아니므로 **AR(1) 유효표본수**로 각 군의 분산·SE·CI·자유도를 보정.
   - 신규 통계: `student_t_sf`(정규화 불완전베타), `t_quantile`, `welch_ttest`, `bh_fdr`
     — scipy와 ≤1e-12 일치(분수 자유도 포함).
3. 부수: `examples/dose_session.csv`(합성 투약 세션) + `실행.command` 예제 5, `--csv-summary`
   전원잡음/기저대비 열, `--psd-csv` 가 노치 결과를 반영, JSON `line_noise`/`baseline_contrast`.

### 적대 검토(4인 병렬: correctness / edge-case / docs / test+PII)에서 고친 것

**통계·정확성**
- **[HIGH] BH-FDR 가족에 중복 검정.** 기본 대역에서 SWA=delta 이므로 `swa_*` 와 `delta_*` 는
  비트단위 동일한 검정. BH는 동점을 **최상위 순위**로 처리하므로 사본이 최유의 검정을
  rank 1→2로 밀어올려 q를 **절반**으로 만들었음(반보수적). → 동일 대조는 가족에서 **한 번만**
  세고 q를 공유. 리포트/JSON에 실제 `m`(`BH-FDR family m`) 표기.
- **[HIGH] 큰 진폭에서 `OverflowError` 트레이스백.** `(v-mean)**2` 는 예외를 던지지만
  `x*x` 는 `inf` 를 반환. → 제곱합 전부 곱셈으로 교체 + `_safe_fsum`(오버플로 시 NaN),
  `lag1_autocorr`/`welch_ttest` 가 비유한 분산에서 None 반환. 1e150 진폭 입력이 exit 0.
- **[MED] `t_quantile` 브래킷 상한 2²⁰.** df<1 이나 극소 p에서 **상한값 자체를 조용히 반환**
  (참값 1.68e12 → 1048576). → 브래킷을 실제로 감싸도록 확장, 실패 시 inf.
- **[LOW] `bh_fdr` 이 정수 p를 가족에서 탈락.** `isinstance(p,float)` → 실수 전체 허용.

**전원잡음(대부분 내가 도입한 신규 결함)**
- **[HIGH] 에일리어싱 오탐이 진짜 리듬을 삭제.** fs=80에서 50 Hz의 3고조파가 정확히 10 Hz
  (알파 한가운데)에 떨어져 "알파의 96.7%가 전기잡음"으로 판정, `--notch` 가 알파를 지움.
  → `auto` 는 **접힌 고조파를 절대 자동 판정하지 않고** `ⓘ 에일리어싱 의심` 으로 보고만 함.
  제거하려면 `--line-freq` 명시 필요(그 자리의 진짜 활동도 지워진다고 재경고).
- **[HIGH] 그 수정이 과도해 fs≤102 Hz에서 완전 침묵.** 후보가 모두 Nyquist 위면 리포트를
  아예 만들지 않아, 위 경고가 **발화 불가**. fs=100/60 Hz 기록에서 gamma의 79%가 전원인데
  아무 말도 없었음. → 접힌 위치로라도 리포트를 구성(판정은 여전히 안 함).
- **[HIGH] 에폭 JSON이 하지도 않은 제거를 주장.** 에폭이 기록의 f0을 **숫자**로 받아
  `source="user"` 가 되면서 에일리어싱 가드가 꺼짐 → 에폭마다 225 µV² 의 허위 "mains excess".
  → `source` 를 함께 상속.
- **[HIGH] 절대 하한 없는 비율 검정.** 순수 정현파의 **반올림 오차**(총파워의 7e-13)가
  "전원잡음 검출·제거됨"으로 기록되고 `--csv-summary` 의 QC 열까지 오염. → `MIN_EXCESS_SHARE`
  (총파워의 1e-6) 하한 추가.
- **[MED] 대역 경계에서 오염량 오귀속.** ±bw 창에 균등 안분해 "g2의 86%가 전기잡음"이라
  보고했으나 실제 제거량은 0. → 피크 **중심 주파수를 포함하는 대역에 전액** 귀속.
- **[MED] 에폭마다 검출을 재판정.** 10초 에폭은 Welch 세그먼트가 적어 깨끗한 에폭의 ~20%가
  우연히 3× 를 넘김 → 무작위 부분집합만 노치되어 에폭 간 비교 불가. → 제거 대상은 **기록
  수준에서 한 번** 결정하고 모든 에폭이 상속.
- **[MED] `--notch` 무효과인데 침묵 + 틀린 이유 표시.** `fs/2<=51` 만 보고 "상수 신호"라
  단정했음. → `windows_fit()` 로 실제 원인(창이 Nyquist 안에 안 들어감 / 창이 빈 간격보다
  좁음 / 임계 미달)을 판별하고, 무효과 시 **경고**.
- **[LOW] 천문학적 비율이 24자리로 출력** → `_ratio()` 로 축약.

**보고 정직성**
- **[MED] Δ% 를 비(比)척도가 아닌 지표에도 출력.** log10 SWA가 바로 윗줄 SWA와 다른 %를
  보이고, 0.005→−0.022 인 1/f 지수가 **−534%** 로 표시. → log10·지수·엔트로피는 `n/a`.
- **[MED] CSV 프로버넌스가 "전체 파라미터"라면서 `notch/line_freq/line_bw/baseline/epoch`
  누락** — 노치한 내보내기와 안 한 것이 **바이트 동일**. → 전부 기록.
- **[MED] AR(1) 보정 효과를 과장.** "보정 없이는 p가 수십~수백 배 과소평가" 는 이 도구의
  예제에서 실측 **1.0배**. ρ̂≤0 이거나 창이 짧으면 보정이 사실상 0(하한 n_eff=2). → 문구를
  사실대로 고치고, **`n_eff`·`df` 열을 표에 노출**해 보정 여부를 직접 확인 가능하게 함.
- **[MED] Hedges' g 가드레일 없음.** 에폭 변동이 작으면 g=200 이 나오는데 문헌의 피험자간
  g처럼 읽힘. → 리포트/문서에 "기록 내 에폭 변동으로 표준화, 피험자간 g와 비교 불가" 명시.
- **[LOW] `swa` 라는 이름의 `--bands` 가 핵심 지표 키와 충돌**해 CSV 열 60개 중복. → 충돌
  시 핵심 지표를 유지하고 대역 파생 키를 생략.
- **문서**: README §1/§3/§6/§9/§10 출력 블록을 **실제 실행 결과로 재생성**(전부 낡아 있었음),
  실행 불가 명령(`--bands '...'`) 수정, "600초 기저"↔`--baseline 120` 모순 수정, 기본 gamma는
  30–45 Hz라 50/60 Hz를 **포함하지 않는다**는 사실 명시(그 경우 `--notch` 는 대역파워를 바꾸지
  않음), `실행.command` 가 `sed` 로 "위약 대조 아님" 캐비엇을 잘라먹던 것 수정.

**테스트 품질(변이 테스트)**
- 리뷰어가 **13개 변이 생존**을 보고: CSV/JSON 값 계층이 *열 이름 존재*만 검사해
  `p↔q` 교환, `mean_baseline↔mean_post` 교환, **`q_bh_fdr := p`**(무보정 p를 FDR q로 보고),
  `line_detected` 상시 0, AR(1) 보정 끄기, **p 단측화(모든 p 절반)**, 합동분산 단순평균,
  배경 미차감, 노치 경계 `<=`→`<`, `DEFAULT_BW`, `shoulder`, 상대파워 스케일 100→1 이
  모두 통과. → 신규 `tests/test_output_values.py`(값 대조·AR(1) 통합·표준라이브러리만으로
  **양측 p** 고정·불균등 n 합동분산·노치 내부 상수)로 **13/13 전부 사멸** 확인.
- 무의미했던 기존 테스트 3건 수정: 중앙값-vs-평균 테스트의 이상치가 shoulder 창 **밖**에
  있어 아무것도 검증하지 않던 것, `if lnr is not None:` 로 감싸 **0개 단언**을 실행하던 2건.
- `unittest.main()` 가드가 파일 **중간**에 있어 `python3 tests/test_cli.py` 로는 30/44만
  돌던 3개 모듈 수정.
- 결정성: `PYTHONHASHSEED` 3종·`TZ`/`LC_ALL` 변경·3회 반복 모두 동일. 예제 재생성은 md5 동일.

**PII/보안**: 신규 코드에 파일 쓰기·네트워크·`eval`/`exec`/`pickle` 없음(재확인). EDF 환자
식별 필드는 여전히 읽지 않음. `dose_session.csv` 는 시드 고정 합성 데이터(숫자 열 2개).

**성능**: 51.2만 표본 `--epoch 30 --notch --baseline` 2.3 s (노치 오버헤드 선형).

**결과: `python3 -m pytest -q` → 506 passed, 1 skipped. `EEGBAND_REQUIRE_ORACLES=1` 동일.
numpy/scipy 없는 환경 486 passed. `unittest discover` 동일. 검토에서 나온 지적 중 신규
기능의 **오답·크래시·허위 보고**는 전부 수정했고, 남은 것은 사전 존재 사항(입력 파일명이
프로버넌스에 기록됨 — 문서에 이미 경고)뿐임.**
