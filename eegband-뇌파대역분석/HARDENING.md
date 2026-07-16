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
