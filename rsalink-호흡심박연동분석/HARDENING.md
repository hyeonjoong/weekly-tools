# HARDENING.md — rsalink

## 라운드 0 (빌드 시점, 2026-09-10)

상습 결함 5종을 빌드 단계에서 선제 차단하고 각각 테스트로 고정했다.

| # | 결함 | 차단 | 테스트 |
|---|---|---|---|
| 1 | 산출물 경로가 심볼릭링크/하드링크(nlink>1)면 링크 너머 파일을 덮어씀 | `report._guard_target`: islink → exit 2, `st_nlink > 1` → exit 2, 타깃 바이트 불변 | `tests/test_cli_guards.py::test_symlink_target_refused`, `::test_hardlink_target_refused` |
| 2 | `.gitignore` 가 실측 CSV/EDF/ZIP 을 놓침 | 루트 `*.csv *.edf *.zip` 전역 차단, 예시 폴더만 허용 | `::test_gitignore_blocks_data_files` (임시 폴더에 `git init` 복사본으로 `check-ignore`) |
| 3 | `--out-dir` 이 기존 파일이거나 권한 없음 | `report.prepare_out_dir`: 한국어 오류 + exit 2, 계산 **전에** 검사 | `::test_out_dir_is_file_exit2`, `::test_out_dir_permission_denied_exit2` |
| 4 | md/csv 에 절대경로 기록 | 리포트·CSV 는 `os.path.basename` 만 | `::test_artifacts_basename_only` |
| 5 | 인과·진단 문구로 미끄러짐 | 판정어는 고정 목록("동반됨/비동반/판정 보류(사유)", "동일/상이/판정 보류") | `::test_forbidden_phrases` — 금지어 스템을 report.py · segments.py · cli.py · README · 사용법 · 실행.command 와 리포트 실행 출력에서 검사 |

추가로 고정한 것: exit 3 게이트, 호흡당 박동 <3 → 판정 불가, 아티팩트 자백, 5분 미만 "짧음", 타임스탬프 역행·tz 혼재 거부, 자동 정렬 없음, CSV 수식 가드, 인용 6편 고정.

## 라운드 1 — 적대적 패널 (2026-09-10)

패널 4명이 낸 중대 14 + 경미 38 을 항목별로 고쳤다. 아래는 finding → fix. 라운드 1 종료 시점 pytest **126 passed**(라운드 0: 62) — 단, `test_hrvkit_example_rr_parses_readonly` 는 형제 폴더 `../hrvkit*/examples/session_20min.csv` 가 있을 때만 돌고 없으면 skip 이라 그 환경에서는 **125 passed + 1 skipped** 다(라운드 2 #7 에서 정정).

### A. 계산 정확성

| # | finding | fix | 테스트 |
|---|---|---|---|
| A1 | RSA peak-valley 가 생리적 호흡→RR 지연을 허용하지 않아 τ 0.5–1.0 s 에서 12/분 −26%, 15/분 −48%~음수 | RR 값을 **간격 중점 시각**(`RRSeries.t_mid` = 박동 시각 − RR/2)에 배정해 RR/2 ≈ 0.45 s 의 기계적 지연 제거. `--rsa-lag SEC`(**기본 1.0**)로 흡기/호기 창을 뒤로 이동. 호흡별.csv 에 `RR최소_위상`·`RR최대_위상` 열. 음수 RSA 안내문에서 `--resp-offset` 권고 삭제 → "위상 지연 의심: --rsa-lag 확인, 위상 열 참조" | `test_rsa.py::test_rsa_pv_recovers_with_physiological_lag`(12·15/분 × RR 500·900 × τ 0/0.5/1.0), `::test_rsa_lag_zero_loses_delayed_extremes`, `::test_phase_columns_locate_extremes`, `::test_hand_values_*` |
| A2 | 호기 말 정지 파형에서 신호 MAD 가 붕괴해 잡음 골을 호흡으로 검출 | 임계 = max(k × **잡음** MAD·1.4826, 0.15·(p95−p5)), k 기본 1.0 → **3.0**. 잡음 MAD 는 평활 잔차(원 파형 − 이동평균)의 MAD. 호흡수 CV > 40% 플래그, CV > 60% 또는 \|중앙값−평균\|/중앙값 > 25% → exit 3 (구간 안에서 계산) | `test_breath.py::test_pause_waveform_not_oversplit`(σ 0.10, SNR 8.6 dB → 6.22/분·유효 100%; k 1.0 이면 13.8/분), `::test_rate_stability_flag_and_gate`, `test_cli_guards.py::test_gate_rate_unstable_exit3` |
| A3 | ±20% 판정이 추정량 분산을 보정하지 않음 | 판정 보류 사유 추가: HF 고정 < 30 ms² 바닥(**클램프**, 아래 결정 참조), Welch L < 6, 구간 RSA 전부 판정불가, 스펙트럼 없음, 구간 데이터 부족. 판정 줄과 ⑥ 표에 L 표기. 기준 줄에 "±20%·30 ms²·L≥6 은 문헌 근거 없는 이 툴의 관례" 명시 | `test_segments.py::test_hold_low_welch_segments`, `::test_hold_hf_floor_clamp`, `::test_hold_all_rsa_undetermined`, `::test_verdict_line_shows_L_and_no_nan` |
| A4 | RR `NA`/빈칸/문자/nan/inf 셀이 조용히 박동을 삭제 | 파싱 실패 셀 = 제외 건수에 포함(`n_unparsed` 별도 자백) + 중앙값 RR 로 시간 전진 → 20% 게이트 적용. 숫자 열 자동 탐색은 비어 있지 않은 셀의 80% 이상이 숫자면 채택 | `test_parse.py::test_rr_na_cells_counted_as_excluded`, `::test_rr_headerless_with_na_still_finds_column` |
| A5 | 구간이 데이터 끝을 넘으면 끝값 홀드로 스펙트럼 희석·판정 반전, 거대 구간이면 행 폭발 | `clip_segments`: 두 신호 공통 범위로 클리핑, 표에 "(요청 M:SS–M:SS, 데이터 범위로 N% 잘림)", 조건비교.csv 에 `t0/t1_requested_s`·`clipped_frac`. 잘린 뒤 < 2분이면 스펙트럼·판정 생략("구간 데이터 부족" 보류). 리샘플 격자 상한 = RR 마지막 박동. 1 s 미만 차이는 잘림으로 치지 않음 | `test_cli_guards.py::test_stimulus_beyond_data_is_clipped_and_numbers_match`(5:00–60:00 = 5:00–15:00 수치 동일), `::test_huge_stimulus_finishes_fast`(1000000:00 → < 1 s), `::test_segment_clipped_below_2min_holds` |
| A6 | 헤더 없는 `--segments` 첫 행 소실 | 3열이고 첫 두 셀이 시각이면 데이터로 취급, 아니면 exit 2 "헤더 start,end,label 필요". 라벨 중복 → exit 2 | `test_segments.py::test_segments_csv_headerless_and_duplicates` |

**A1 τ-지연 회복률 실측표** (RSA_pv 중앙값 / 2A; 사인 호흡, A 40 ms, 300 s; `RR_i = m − A·sin(θ(t_i − τ))`, t_i = 박동 시각). "구" = 라운드 0(끝 박동 배정·lag 0), 나머지는 중점 배정:

| 호흡수 | RR | τ | 구 | lag 0 | lag 0.5 | lag 0.75 | **lag 1.0(기본)** |
|---|---|---|---|---|---|---|---|
| 6/분 | 900 | 0 / 0.5 / 1.0 / 1.5 | .95 / .82 / .60 / .33 | .99 / .94 / .81 / .58 | .99 / .99 / .94 / .80 | .99 / .99 / .98 / .88 | **.99 / .99 / .99 / .94** |
| 12/분 | 900 | 0 / 0.5 / 1.0 / 1.5 | .80 / .33 / −.24 / −.22 | .95 / .77 / .30 / −.29 | .95 / .95 / .76 / .29 | .95 / .95 / .90 / .55 | **.95 / .95 / .95 / .76** |
| 15/분 | 900 | 0 / 0.5 / 1.0 / 1.5 | .69 / .07 / −.49 / .08 | .93 / .65 / .00 / −.47 | .93 / .93 / .64 / −.02 | .93 / .93 / .84 / .36 | **.93 / .93 / .93 / .64** |
| 20/분 | 900 | 0 / 0.5 / 1.0 / 1.5 | .54 / −.32 / −.32 / .56 | .86 / .41 / −.35 / −.29 | .86 / .83 / .47 / −.32 | .86 / .83 / .78 / −.04 | **.60 / .83 / .85 / .39** |
| 12/분 | 500 | 0 / 0.5 / 1.0 / 1.5 | .92 / .57 / .01 / .02 | .99 / .80 / .31 / −.19 | .99 / .99 / .80 / .32 | .99 / .99 / .93 / .56 | **.99 / .99 / .99 / .79** |
| 15/분 | 500 | 0 / 0.5 / 1.0 / 1.5 | .91 / .37 / −.27 / .37 | .98 / .69 / .00 / .03 | .98 / .98 / .68 / −.02 | .98 / .98 / .92 / .39 | **.98 / .98 / .97 / .66** |

읽는 법: 중점 배정 뒤 창의 오른쪽 여유는 정확히 lag 이고 유효 지연은 τ − RR/2 다. 극값이 창 오른쪽 끝을 넘으면 박동 이산화 손실이 한쪽으로만 쌓여 급락한다(12/분·RR 900·lag 0.5·τ 1.0 → .76). τ = 0 열은 lag 와 무관한 양측 이산화 바닥(15/분·RR 900 → .93). 오프셋 오차 민감도(τ 0.7, RR 900, lag 1.0): 오차 −1.0 s 에서 12/분 .60·15/분 .42, −1.5 s 에서 15/분 부호 반전(−.28), +1.0 s 까지는 ≥ .86.

### B. 안전·가드

| # | finding | fix | 테스트 |
|---|---|---|---|
| B1 | 쓰기 프로브가 고정 이름이라 링크를 따라감; 산출물을 하나 쓰고 나서 다음 가드; out-dir 을 입력 검증 전에 생성; `~` 미확장 | 프로브 = `tempfile.mkstemp(dir=out_dir)`(난수명·O_EXCL). 산출물 3종은 **하나라도 쓰기 전에** 전부 가드(islink·nlink>1·입력 realpath 와 동일 → exit 2), 쓰기는 임시파일 + `os.replace`. out-dir 은 `expanduser`+`realpath`, 링크면 "(링크 → 실제경로)" 출력, 생성은 입력 파싱 **뒤** | `::test_probe_name_symlink_and_hardlink_untouched`, `::test_input_equals_artifact_refused`, `::test_out_dir_not_created_when_input_invalid`, `::test_out_dir_tilde_expanded`, `::test_out_dir_symlink_dir_uses_realpath` |
| B2 | CSV 수식 가드 테스트 0건, md 표에 `\|` 미이스케이프 | 라벨 `=HYPERLINK`·`+cmd\|pipe`·`--subject "=1+1"` 이 두 CSV 에서 `'` 접두인지, `_cell` 단위(탭·CR·@·-)와 CLI 수준 모두 검사. md 표는 `_md()` 로 `\|`·개행 이스케이프 | `::test_csv_formula_guard_labels_and_subject` |
| B3 | `.gitignore` allow-list 가 `examples/**/*.csv` 로 넓음, `*.tsv` 누락 | `!examples/paced_6bpm/*.csv`·`!examples/sham/*.csv` 만 허용, `*.tsv` 추가 | `::test_gitignore_blocks_data_files`(`examples/실제.csv`, `examples/real_subject/RR.csv`, `RR.tsv`, `examples/sham/x.tsv` 차단 확인) |

### C. 문서·주장 규율

| # | finding | fix | 테스트 |
|---|---|---|---|
| C1 | 금지어 목록이 좁음(완곡한 인과 문장 통과) | KR 스템 20 + EN 스템 18 로 확장(bare `기인`·`suggest` 는 충돌로 제외). 검사 대상: 소스 3 + 문서 3 + **리포트 실행 출력 4종**(paced·sham·이벤트 입력·판정 보류). 완곡 문장 4종 뮤턴트("…를 반영한다", "부교감 활성 증가를 시사", "driven by … suggesting vagal", "…로 인해 … 설명할 수")가 모두 죽는 것을 확인 | `::test_forbidden_phrases`, `::test_report_output_no_forbidden[paced/sham/events/hold]`, `::test_forbidden_scanner_kills_euphemism_mutants` |
| C2 | 이벤트 입력인데 Methods 가 파형 검출을 서술 | `br.source == "events"` 분기: "흡기 시작은 제공된 이벤트 목록, 흡기 끝은 주기의 40% 가정, 결맞음 미계산" | `::test_events_input_ignores_waveform_options_with_warning` |
| C3 | "1×MAD" 하드코딩 | k_mad·smooth_s·기준선 창(2·max_breath_s)·lag·아티팩트 기준·제외 %·Welch 세그먼트·L 을 실제 값으로 보간 | 리포트 출력 검사 |
| C4 | `--resp-offset` 부호 도움말 반대 | "호흡 기록이 RR 기록보다 늦게 시작했으면 양수(= 호흡 시작 절대시각 − RR 시작 절대시각)" — cli·사용법 | — |
| C5 | `--inspect` 오프셋 힌트가 RR 타임스탬프 관례에 의존 | "간격 시작 기준 d / 박동 시각 기준 d+RR₁" 둘 다 표시(리포트 ① 에도 행 추가), 사용법에 관례 설명. README 한계의 "줄 수 있다" → 실측치 | `::test_abs_time_diff_row_both_conventions` |

### D. 경미 (전부 수정)

| # | fix |
|---|---|
| D1 | 구간별 nperseg·Δf·L 을 ④ 각주와 조건비교.csv(`welch_nperseg`, `welch_df_hz`)에 |
| D2 | 판정 문구·코드 일치: 비율 **< 0.80 또는 > 1.25** 만 변화(정확히 0.80·1.25 는 변화 없음), "불변" → "임계 미만", 호흡 없는 구간은 "호흡 없음"(nan 노출 금지) |
| D3 | 구간 호흡수 CV > 15% → "호흡중심 대역의 평균 중심 무의미 가능" 플래그(+csv `flag_rate_cv_gt15`) |
| D4 | ④ 표에 호흡당 박동 열 |
| D5 | 호흡수·HF 가 같은 방향이면 "Hirsch & Bishop 1981 롤오프 방향과 불일치" 한 줄 |
| D6 | 결맞음에 1/L 과 null 95% = 1 − 0.05^(1/(L−1)) 병기(+csv `coherence_null95`) |
| D7 | exit 3 메시지에 옵션 힌트; 사용법에 3–4/분 페이싱 → `--max-breath-s 25` |
| D8 | 제안 오프셋 r > 0 → "--invert 확인"; ±5 s·호흡 주기 배수 모호성 문구; 절대 시각 둘 다 있으면 ① 에 절대 시각 차 행 |
| D9 | 트레이스백 → 한국어 exit 2: 타임스탬프 전부 동일, parse_mmss 오형식(음수 파트 포함), 옵션 nan/inf/범위(`_validate_options`) |
| D10 | 호흡별.csv 구간 라벨에 `--resp-offset` 적용(segments.py 와 일치) |
| D11 | 한쪽만 tz-aware → 절대 시각 비교 생략 + 명시 자백; 호흡당 샘플 < 4 플래그; fs < 0.2 Hz → "타임스탬프 단위 의심" exit 2; 파형 nan/inf/비숫자 셀 제외 + 자백 |
| D12 | 디트렌드 전 \|z\| > 10·MAD·1.4826 클리핑 + 샘플 수 자백 |
| D13 | 기준선 폴백 규칙 문서화; 열 이름은 실제 라벨; 중복 라벨 exit 2; 이벤트 입력 + `--invert/--smooth-s/--k-mad` → "무시됨" 경고(stderr + ① 행) |
| D14 | Grossman 1990 ".91 vs .84" → "복합 디트렌드법보다 높음"; Grossman & Taylor 를 "RSA 는 호흡수·깊이 모두에 의존" 문장에; README "압반사 기원의 LF 피크" → "압반사 공명 기전으로 통상 해석되는 0.1 Hz 부근 피크(Lehrer & Gevirtz 2014)"; "부교감 지표 감소" → "HF 감소" |
| D15 | Methods EN에 Welch 세그먼트 길이·평균 제거·아티팩트 기준·제외 %·결맞음 단일 bin·1/L·RR 배정 규칙; Results 보류 문구 "HF 고정 파워 변화가 ±20% 안이라 동반 여부는 판정하지 않았고"; "동반됨이었고" → "동반되었고"; EN "conclusion was different" → 방향 명시 |
| D16 | README: 3상태 판정 문서화, "두 단어 구조" 삭제, 출력 블록은 절 단위 그대로 + "…" 생략 표시, rsalink/hrvkit/agreestat 3행 경계표, `--events` 플래그 없음 명시, RR 타임스탬프 "없음(RR 누적)" |
| D17 | test_segments 동어반복 제거, `or True` 제거, 판정 문자열 검사 → 정규식, `POWER_RATIO_LO/HI` 단일 상수 + 경계값(0.79/0.80/0.81·1.24/1.25/1.26) 테스트, "짧음(<5분)" 테스트, sham CLI 테스트, 제안 오프셋 "표시만" 테스트, hrvkit `examples/session_20min.csv` 읽기 전용 파싱 테스트, 실행.command 테스트는 tmp 복사본에서 실행(저장소 안에 결과/ 생성 안 함) |

### 패널 결정과 다르게 한 것 (근거 한 줄씩)

1. **`--rsa-lag` 기본 0.5 → 1.0.** 중점 배정 뒤 창의 오른쪽 여유가 정확히 lag 인데 유효 지연 τ − RR/2 가 τ 1.0 s·RR 900 에서 0.55 s 라 0.5 로는 패널의 수용 기준(τ ∈ {0.5, 1.0}, 12·15/분 ≥ 0.85·2A)을 채울 수 없다(실측 .64–.80); 1.0 이면 6–15/분·RR 500–900 에서 ≥ .93 이고 τ = 0 은 변하지 않는다. 대가는 20/분·τ 0·RR 900 에서 .86 → .60 (패널이 비생리적이라 본 영역; 사용법에 `--rsa-lag 0.5` 안내).
2. **`k·MAD` 의 MAD 를 신호가 아니라 잡음(평활 잔차) MAD·1.4826 으로 정의.** 신호 MAD 로는 k 3.0 이 깨끗한 사인(MAD 0.707, 돌출 2.0)조차 전부 기각한다(2.12 > 2.0). 잡음 MAD + 진폭 바닥이면 정지형 파형 조건(6/분·σ 0.10 → 6.0–6.3, 유효 ≥ 90%)과 기존 사인 조건을 동시에 만족한다.
3. **HF 30 ms² 바닥을 "보류 조건"이 아니라 "클램프"로 적용.** 글자 그대로면 기준 276 → 자극 11 ms² 인 paced 예시(RSA 파워가 고정 HF 밖으로 이동한, 이 툴이 보여주려는 바로 그 경우)가 보류가 된다. 30 미만을 30 으로 놓고 본 비율이 ±20% 안일 때만 보류, 밖이면 판정 + 주석(지시서의 C1 이 paced·sham 과 "판정 보류"를 별개 사례로 두는 것과 일치).
4. **호흡수 CV/skew 안정성은 구간 안에서 계산.** 전체 기록으로 계산하면 12→6/분 프로토콜 자체가 CV 34%·skew 최대 50% 가 되어 정상 예시가 exit 3 이 된다(paced 예시는 호흡 수 61:59 로 중앙값이 두 무리 사이에 떨어져 우연히 통과). 임계는 패널 값 그대로.
5. **구간 잘림 1 s 허용.** 요청 5:00–15:00 이 데이터 끝 14:59.9 에 0.1 s 걸리는 것을 "잘림"으로 표기하지 않는다(격자 상한이 처리).

### 미해결 / 알려진 한계

1. **τ = 1.0 s·15/분·RR 900 의 회복률 .93 은 사인 호흡 기준.** 정지형 파형에서는 흡기 시작이 정지 구간 안의 잡음 최저점에 놓여 onset 지터가 크므로 RSA 창 자체가 흔들린다 — 호흡수는 복원되지만 RSA_pv 회복률은 측정하지 않았다.
2. **호흡수 skew 게이트(25%)는 이봉 구간에 민감.** 사용자가 프로토콜 전환을 걸치는 구간(예: 2:00–15:00)을 주면 exit 3 이 된다. 구간을 프로토콜에 맞춰 나누라는 뜻이지만, 임계 자체의 문헌 근거는 없다.
3. **한쪽만 tz-aware 인 경우 거부하지 않고 자백만 한다**(절대 시각 비교 생략). 두 파일이 같은 장비 시계라면 상대 시간 분석은 여전히 유효하기 때문.
4. **이벤트 입력의 흡기 끝 40% 가정**은 그대로다(파형이 있으면 파형을 쓰는 편이 낫다).
5. **호흡 깊이 미측정**(라운드 0 항목 6) 그대로.

### exit 행렬 재확인 (2026-09-10)

0: paced · sham · `--inspect` · 데이터 밖 구간(클리핑) · 잘린 뒤 < 2분(보류, 리포트는 출력).
2: 파일 없음 · `--segments`+`--baseline` 충돌 · `--pace 0` · `--smooth-s nan` · `--max-breath-s inf` · `--k-mad 0` · `--rsa-lag -1` · `--resp-band-halfwidth 0` · `--baseline x:yy-5:00` · 구간 CSV 헤더 불량 · 라벨 중복 · 시간 오형식 · 타임스탬프 전부 동일 · fs < 0.2 Hz · 입력 = 산출물 경로 · `--out-dir` 이 파일 · 산출물 심볼릭링크.
3: RR 제외 > 20% · 사용 구간 < 2분 · 유효 호흡 < 60% · 구간 호흡수 CV > 60%(신규).
`echo | bash 실행.command` → exit 0.

## 라운드 2 — 검증자 (2026-09-10)

검증자가 낸 중대 2 + 경미 7 을 항목별로 고쳤다. 아래는 finding → fix. 전체 pytest **164 passed**(hrvkit 예시가 있는 환경; 없으면 163 passed + 1 skipped). `echo | bash 실행.command` → exit 0. 예시 두 벌의 숫자는 라운드 1 과 동일(README 붙임 블록은 ⑥ 의 "잡음 마진" 줄과 판정 기준 문장만 갱신).

### 중대

| # | finding | fix | 테스트 |
|---|---|---|---|
| 1 | "RSA 전부 판정불가" 보류가 판정 n == 0 일 때만 걸려, HR 40(RR 1500 ms)·자극 호흡 20/분(박동 1.98/호흡, 판정 3 vs 판정불가 198)이 **동반됨**으로 나옴 | 보류 조건을 "우세"로: 구간 호흡당 박동 평균 < 3 **또는** 판정불가 > 판정 이면 `"{구간} RSA 판정불가 우세(호흡당 박동 b, 판정 n/판정불가 m)"` 보류(`segments.hold_reasons`, `RSA_MIN_BEATS = 3`). 판정 기준 줄·README·사용법 문구 갱신 | `test_segments.py::test_hold_when_undetermined_dominates_hr40_20bpm`(합성 HR 40·12→20/분 → 판정 보류), `::test_hold_undetermined_majority_even_with_enough_beats`(19/21 보류, 21/19 동반됨) |
| 2 | ±20% 고정 규칙이 추정량 분산을 무시 — 백색잡음 RR 에서 오판 | 로그비 잡음 마진 `noise_margin_ln(L_base, L_stim, B)`: M = max(ln 1.25, 1.96·√(2/L_eff)), L_eff = min(L)·B_eff, B_eff = 0.5·(HF 0.15–0.40 안 Welch 빈 수; 256 → 16, 128 → 8, 64 → 4; `spectral.band_bin_count`, `BandResult.hf_n_bins`). 유도는 `noise_margin_ln` docstring(빈당 상대분산 1/L, B 빈 합·Hann 50% 겹침 상관 → 독립 성분 ≈ 절반, var(ln 로그비) ≈ 2/L_eff). "변화" = \|ln 비\| > M, 아니면 `판정 보류(HF 변화 \|ln비\| ≤ 잡음 마진 ±M%)`. 30 ms² 클램프는 그대로(클램프 뒤 \|ln 비\| 로 판정). ⑥ 에 "잡음 마진 ±M% (비율 lo 배 미만 또는 hi 배 초과면 변화; HF 고정 \|ln 비\|; L_eff = …) — L·대역 빈 수 기반, 이 툴의 관례" 줄, 판정 기준 줄에 M·%·비율 경계·공식·L_eff, Results KR/EN 보류 문구에 마진 %. 호흡중심 대역 방향도 같은 M | `test_segments.py::test_noise_margin_hand_literal`(L=8·B=16 → 0.346482, 비율 0.7072–1.4141, ±29.3%; L=30 → 바닥 ln 1.25 = ±20%), `::test_hf_band_bin_count_literal`, `::test_margin_widens_verdict_and_is_printed`(L=10: 비율 1.30 보류·1.40 동반됨·L=40 이면 1.30 ↑), `::test_false_change_rate_pure_noise_20_seeds`(아래 실측), paced 동반됨·sham 비동반 유지(`test_paced_scenario_verdict_accompanied`, `test_sham_scenario_verdict_not_accompanied`, CLI 두 예시) |

**#2 오판률 실측** (호흡 12/분 고정, RSA A = 3 ms, RR 백색잡음 σ 15–30 ms(seed 마다 등간격), 20 seed, seed 1000–1019; "변화" = 보류 사유 없이 HF 고정이 ↑/↓):

| 구간 길이 | Welch L | 옛 ±20% 규칙 | 마진 M | 마진 뒤 | 로그비 RMS 실측 / 이론 √(1/(L₁·8)+1/(L₂·8)) |
|---|---|---|---|---|---|
| 5분 / 5분 | 8 / 8 | **5/20 = 25%** | 0.346 (±29%, 비율 0.71–1.41) | **1/20 = 5%** | 0.176 / 0.177 |
| 4분 / 4분 | 6 / 6 | 4/20 = 20% | 0.400 (±33%, 0.67–1.49) | 2/20 = 10% | 0.210 / 0.204 |
| 4분 / 8분 | 6 / 14 | 4/20 = 20% | 0.400 | 0/20 = 0% | 0.171 / 0.173 |
| 5분 / 10분 | 8 / 17 | 1/20 = 5% | 0.346 | 0/20 = 0% | — |

실측 RMS 가 이론과 1–3% 안에서 맞는다 — B_eff = 0.5·B 근사가 이 합성에서 성립한다는 뜻(실제 RR 의 잡음이 백색이 아니면 달라질 수 있다). 테스트는 5분/5분(검증자 수치 25–35% 를 재현하는 설정)으로 고정: 마진 뒤 ≤ 2/20 이고 옛 규칙이면 ≥ 4/20 이어야 통과(뮤턴트 방어).

### 경미

| # | finding | fix | 테스트 |
|---|---|---|---|
| 3 | 전역 0.15·(p95−p5) 바닥이 진폭 비 ≳7:1 의 작은 호흡 조건을 지움 | 바닥을 **60 s 창별**(10 s 간격 블록, ~4 Hz 로 솎아 p95−p5)로 계산해 골마다 그 자리의 바닥을 쓴다(`breath.rolling_range_floor`). 리포트 ② 에 창별 중앙값·범위, Methods KR/EN·cli 도움말·README·사용법 갱신. 실측: 진폭 1.0(0–5분) → 0.12(5–15분) 는 전역 바닥 0.196 에 골(돌출 ≈0.23)이 거의 다 기각돼 유효 16/120 이었고(0.14 는 이 파형에선 통과), 창별이면 두 조건 모두 검출 | `test_breath.py::test_small_breath_condition_survives_after_large[0.14/0.12]`(두 조건 유효 ≥ 90%·호흡수 12 ± 0.3), `::test_rolling_floor_equals_global_when_short` |
| 4 | 정지형 파형 A2 가 경계선(20 seed 중 2 가 6.5/분 초과) | 원인은 잡음 골이 아니라 **흡기 시작 지터**였다: 골 = 평탄한 정지 구간 안의 잡음 최저점이라 주기가 4–14 s 로 교대하고 rate 평균이 Jensen 편향으로 올라간다(seed 15: 호흡 28개인데 6.85/분). 임계는 k·MAD(0.27)가 바닥(0.12)보다 커서 바닥 비율 조정은 효과가 없다. 대신 **흡기 시작 보정**: 봉우리에서 골 쪽으로 내려오며 누적 최소를 추적, 신호가 누적 최소보다 tol = max(3·σ_smooth, 흡기 진폭의 10%) 이상 되오르면 정지 → 누적 최소 자리(`breath._refine_onset`). 매끈한 파형은 되오름이 골을 지나야 생기므로 골 그대로(사인 4종 shift 0.00 s). 20 seed: 6.18–6.85 → 6.04–6.32/분, 주기 CV 19–35% → 13–26%, 유효 ≥ 96%. k 2.0 은 σ 0.3 사인을 깨고(shift 4 s), 4.0 은 6.53 까지 남아 3.0 채택 | `test_breath.py::test_pause_waveform_sharper_multi_seed[0–19]`(5.8–6.6, CV ≤ 30%, 유효 ≥ 90%), `::test_refine_onset_literal`, `::test_onset_refinement_leaves_sine_onsets_at_trough`, 기존 `::test_onset_is_trough_peak_between`·`::test_pause_waveform_not_oversplit` 유지 |
| 5 | 구간별 CV/skew 게이트와 음수 RSA 안내 문구가 테스트로 고정되지 않음 | 이벤트 입력 30/분 2분 + 5/분 13분: 전체 기록이면 CV 74%·skew 240% 로 exit 3, 구간이면 exit 0(뮤턴트 "whole-record CV" 는 여기서 죽는다; 같은 데이터를 구간 없이 주면 exit 3). 음수 RSA 안내는 리포트 ③·사용법 모두 `--rsa-lag` 를 가리키고 `--resp-offset` 을 담지 않음 | `test_cli_guards.py::test_rate_gate_is_per_segment_not_whole_record`, `::test_negative_rsa_hint_mentions_rsa_lag_not_resp_offset` |
| 6 | `rsa.py` docstring 의 오프셋 부호("호흡 시계가 앞서면 양수")가 CLI 와 반대 | "호흡 기록이 RR 기록보다 늦게 시작했으면 양수(= 호흡 시작 절대시각 − RR 시작 절대시각)"로 통일 | `test_rsa.py::test_resp_offset_docstring_sign_matches_cli` |
| 7 | HARDENING 의 "126 passed" 가 환경 의존 | 라운드 1 문장에 skip 조건 명시(hrvkit 예시 없으면 125 + 1 skipped); 이 라운드 수치도 두 경우 병기 | — |
| 8 | macOS 에서 `--out-dir /tmp/…` 가 "(링크 → 실제경로)" 로 표기됨(/private 접두) | `report.path_differs_beyond_private`: realpath 와 abspath 를 앞의 `/private` 를 벗기고 비교해 다를 때만 표기. 폴더 존재 여부와 무관하게 판정(라운드 1 은 폴더가 이미 있을 때만 찍었다) | `test_cli_guards.py::test_link_note_ignores_macos_private_prefix`(문자열 5종 + /tmp CLI 두 번), 기존 `::test_out_dir_symlink_dir_uses_realpath` 유지 |
| 9 | 겹치는 `--baseline/--stimulus`(또는 `--segments` 행)이 조용히 통과; `--stimulus -1:00-5:00` 이 빈 문자열 `''` 오류 | `segments.check_no_overlap` → exit 2 "구간 겹침: …"(끝 = 시작은 허용). `parse_range`: 부호로 시작하면 "구간이 부호로 시작합니다 … 음수 시각은 쓸 수 없습니다", 한쪽이 비면 "(시작 또는 끝이 비어 있음)". argparse 자체 오류도 `_Parser.error` 로 한국어 한 줄 + exit 2(구 Python 은 `-1:00-5:00` 를 옵션으로 오인해 영문 usage 를 냈다; 3.14 는 값으로 넘긴다) | `test_segments.py::test_overlap_check_unit`, `test_cli_guards.py::test_overlapping_segments_exit2`, `::test_negative_or_empty_range_clean_korean_message` |

### 검증자 지시와 다르게 한 것 (근거 한 줄씩)

1. **#4 는 바닥 비율 조정이 아니라 흡기 시작 보정.** 실패 seed 의 초과분은 잡음 골이 아니라 평탄 정지 구간 안의 흡기 시작 지터(주기 4–14 s 교대) + rate 평균의 Jensen 편향이고, 임계는 k·MAD(0.27)가 바닥(0.12–0.17)보다 커서 0.20·창별 바닥으로도 아무것도 바뀌지 않는다.
2. **#3 은 "구간별"이 아니라 60 s 롤링 창.** 호흡 검출기는 구간을 모르고(구간은 RR 시계·오프셋 뒤에 정해진다), 롤링 창이면 구간 없이 돌려도 같은 결과다. 테스트는 지시의 0.14 에 더해 이 파형에서 실제로 실패하던 0.12 도 넣었다(0.14 는 전역 바닥 0.196 < 돌출 0.27 이라 이미 통과).
3. **#2 의 마진 % 표기는 100·(1 − e^{−M}).** 바닥 ln 1.25 가 라운드 1 문서의 "±20%(비율 0.80–1.25)" 로 그대로 읽히도록; e^{M} − 1 로 적으면 같은 바닥이 "±25%" 가 되어 문서·테스트와 어긋난다. 비율 경계(lo–hi 배)를 항상 같이 찍는다.
4. **#2 호흡중심 대역 방향도 같은 M.** 2행("동일/상이")은 두 대역의 방향을 비교하는 것이라 잣대가 다르면 비교가 아니다. 호흡중심 대역만의 빈 수(±0.04 Hz ≈ 5빈)로 마진을 따로 두면 5분 구간에서 ±60% 가 넘어 2행이 거의 항상 보류가 된다.
5. **#9 argparse 오류까지 한국어로.** 지시는 `-1:00-5:00` 한 경우였지만, 그 문자열은 Python 버전에 따라 argparse 가 먼저 잡기도 하므로(3.9–3.11) 두 경로 모두 막았다. `--help/--version` 은 그대로.
6. **#7 실제 수치는 126 → 164 passed(이 환경, hrvkit 예시 있음).** 지시서의 "125 + 1 skipped" 는 hrvkit 예시가 없는 환경의 수치라 두 경우를 병기했다.

### 미해결 / 알려진 한계 (라운드 2 갱신)

1. **정지형 파형의 흡기 시작 지터**는 줄었지만 남아 있다: σ 0.10·10 Hz 에서 보정 뒤에도 상승 발치보다 평균 1.5 s(최대 ≈5 s) 앞. 호흡수는 맞지만 호흡별 RSA 창이 그만큼 흔들린다 — RSA_pv 회복률은 여전히 사인 기준.
2. **잡음 마진의 B_eff = 0.5·B 는 근사**이며 백색잡음 합성에서만 검증했다(로그비 RMS 이론 대비 1–3%). 실제 RR 의 잡음이 유색이거나 HF 안에 RSA 봉우리가 뾰족하면 빈 파워가 고르지 않아 분산이 다를 수 있다. 5분 구간의 마진은 ±29% 라 그보다 작은 실제 변화는 보류가 된다 — 이것은 의도(잡음과 구분되지 않는 변화를 판정하지 않는다).
3. 라운드 1 의 미해결 2–5 는 그대로.

### exit 행렬 재확인 (라운드 2)

0: paced · sham · `--inspect` · 데이터 밖 구간(클리핑) · 잘린 뒤 < 2분(보류) · HR 40·호흡 20/분(판정불가 우세 보류) · `--out-dir /tmp/…`(링크 표기 없음).
2: 라운드 1 목록 + 구간 겹침(`--baseline 0:00-6:00 --stimulus 5:00-15:00`, `--segments` 겹침 행) · `--stimulus -1:00-5:00` / `--stimulus=-1:00-5:00` / `--baseline 5:00-`(빈 문자열 노출 없음) · 알 수 없는 옵션 · 인자 누락.
3: 라운드 1 목록 + 구간 없이 준 30/분→5/분 이벤트 기록(전체 한 구간 skew 240%; 구간을 주면 0).
`echo | bash 실행.command` → exit 0.
