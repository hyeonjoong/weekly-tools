# stimaudit — 자극 세트 점검

실험에 쓸 **소리 파일 여러 개**를 한꺼번에 읽어, ① 각 파일이 사람에게 들려줄 수 있는 상태인가와 ② **이 세트가 유효한 대조인가**를 전수 대조하고, 설계서가 **주장하는 파라미터가 실제 신호에 있는지**까지 확인합니다. 표준 라이브러리만 씁니다.

---

## 목적 / Why this exists

**한국어.** 4조건 비교 실험에서 활성 조건이 대조군보다 소리가 크면, *"이 자극이 효과가 있다"* 는 결론은 *"소리가 더 컸다"* 는 결론과 **구분되지 않습니다.** 리뷰어가 반드시 묻는 질문이고, 데이터를 다 모은 뒤에는 고칠 방법이 없습니다. 그런데 지금 이걸 확인하는 방법은 대개 (a) 사운드 디자이너가 알아서 맞췄겠거니 하거나, (b) Audacity 로 파형을 눈으로 보는 것입니다. 파형 높이는 **피크**이지 음량이 아닙니다 — 핑크노이즈와 드론은 피크가 같아도 체감 음량이 6 dB 넘게 차이 납니다. stimaudit 은 세트 전체를 읽어 조건 간 라우드니스 차이·클리핑·DC·시작 클릭·좌우 불균형을 판정하고, 설계 JSON 이 주장한 반송주파수·맥놀이·변조율·길이가 실제 신호에 있는지 대조한 뒤, 논문 Methods 에 붙일 자극 기술표와 문단 초안까지 냅니다. **자극 설계를 만드는 사람과, 그 자극으로 나온 결과를 논문에 쓰는 사람**을 위한 도구입니다 — 자극 파일을 받은 날, 그리고 원고를 쓰기 시작하는 날에 여십시오.

**English.** If the active condition is louder than the control, *"the stimulus worked"* is not distinguishable from *"it was louder."* Reviewers ask this, and once data collection is finished it cannot be fixed. Yet the usual checks are (a) assuming the sound designer matched levels, or (b) eyeballing waveforms in Audacity — but waveform height is **peak**, not loudness: pink noise and a drone can differ by more than 6 dB in perceived loudness at identical peaks. stimaudit reads the whole set, flags between-condition loudness mismatch, clipping, DC offset, onset clicks and L/R imbalance, verifies that the carrier / binaural-beat / modulation-rate / duration your design document *claims* are actually present in the signal, and emits a stimulus-description table plus a draft Methods paragraph. It is for **the person who commissions the stimuli and the person who writes them up** — open it the day the files arrive, and the day you start the manuscript.

### 이 툴이 하지 않는 것 (경계)

| | 답하는 질문 | 소관 |
|---|---|---|
| DEBUSSY | 이 소리의 음향 지표 11개는 얼마인가? | 다른 툴 |
| `bell_acoustic_qc.py` | 이 소리 **하나**가 논문 Tier-1/2 규칙을 지키는가? | 다른 툴 |
| `calmbark` | 규칙대로 소리를 **만들기** | 다른 툴 |
| **stimaudit** | **이 소리들이 서로 비교 가능한 세트인가? 주장한 대로 만들어졌는가?** | **이 툴** |

- **파일이 1개면 아무 판정도 하지 않고 종료코드 2** 로 멈춥니다. 세트가 아니면 이 툴의 질문 자체가 성립하지 않습니다.
- **심리음향량(러프니스 asper · 샤프니스 acum · ISO 532 라우드니스)을 자체 계산하지 않습니다.** `--manifest` 로 DEBUSSY 값을 받아 쓰거나, 없으면 "그 축은 검사 안 함"으로 자백합니다. 프록시로 흉내 내는 순간 DEBUSSY 의 열등한 사본이 됩니다.
- **음량 보정본을 만들어 주지 않습니다.** 감사와 수정을 한 툴에 넣으면 사람이 결과를 안 보고 수정만 돌립니다. 얼마나 어긋났는지만 말하고, 고치는 건 사운드 담당자가 합니다.
- **통계 검정을 하지 않습니다.** 조건당 파일이 1~2개인 세트에 p값을 붙이면 거짓 정밀도입니다 → `statwise`.
- **절대 음압(dB SPL / dB HL)을 판정하지 않습니다.** 재생 체인 보정 없이는 파일에서 알 수 없습니다. `--spl-db` 를 주면 이유를 설명하고 종료코드 2 로 거절합니다.

### 논문 수치를 임계값으로 쓰지 않습니다 — 타협 불가

이 툴이 참고하는 지식 기반은 저자 자신의 NBR 리뷰(*Acoustic Parameters for Autonomic Arousal Modulation*)입니다. 그 **1st revision 에서 리뷰어 2 가 "간접 증거에서 보편적 설계 처방으로 건너뛴다"고 지적했고, 저자들은 모든 수치를 reference value / exemplar 로 재라벨**했습니다. 개정본의 문장 그대로:

> "the numeric values attached to these principles are reference values reported in the cited studies rather than evidence-derived thresholds … what the evidence supports is the direction of each principle, not its cut-point."

그래서 stimaudit 은 `50 ms`·`0.3 asper`·`60–80 BPM`·`1.5 acum`·`0.8 Hz` 를 **측정값 + 참조값 + 출처 문헌**으로 나란히 인쇄할 뿐, 준수/위반을 찍지 않습니다. 참조값 자료형(`refs.ReferenceValue`)에는 **심각도 필드가 아예 없어** 구조적으로 등급을 붙일 수 없고, `tier1_compliant` 같은 불리언은 코드에도 CSV 스키마에도 존재하지 않습니다. 이 성질은 테스트로 강제됩니다(`tests/test_report.py`, `tests/test_boundaries.py`).

치명 판정은 오직 **툴 자신의 방법론적 기준** 넷에만 붙습니다: 조건 간 음량 불일치 · 클리핑 · 죽은 파일 · 주장 불일치.

### LUFS 는 논문 유래가 아닙니다

**LUFS / EBU R128 은 이 논문에 한 번도 나오지 않습니다.** 논문은 라우드니스를 파라미터로 채점하지 않았습니다. LUFS 를 쓰는 이유는 오직 하나 — 조건 간 체감 음량을 맞췄는지 보는 **실험 통제 관행의 표준 수단**이기 때문입니다. 그래서 논문 단위(**LAeq · LAmax · 다이내믹 레인지**, Table 2 항목 1)를 **항상 LUFS 와 나란히** 인쇄합니다. Czempik et al. (2020, Sci Rep 10:19207) 은 ICU 에서 수면시간과의 상관이 LAmax **r = −0.64** (p = 0.0001) 로 LAeq20sec **r = −0.41** (p = 0.02) 보다 강하다고 보고했기 때문에, LAmax 를 평균과 같은 표에 강제로 넣습니다. (이 논문의 57.9 dB 는 '평균보다 짧게 잔 환자'를 가르는 **ROC 절단점**이지 참조범위가 아닙니다. 두 수치 모두 2026-08-28 하드닝에서 **원문 Table 2 를 직접 확인**해 고친 것입니다 — 자세한 경위는 `HARDENING.md` 발견 U.)

---

## 설치

```bash
cd stimaudit-자극세트점검
python3 -m pip install -e .          # 안 되면: --user 또는 --break-system-packages
```

Python 3.9+ 면 됩니다. **의존성 0** — `pip` 이 아무것도 내려받지 않습니다.
설치가 귀찮으면 그냥 `python3 -m stimaudit ...` 로도 돌아갑니다.
맥에서는 `실행.command` 를 더블클릭하면 번들 예제로 전체 흐름이 한 번 돕니다.

`ffmpeg` 이 PATH 에 있으면 MP3·M4A·FLAC 등도 열립니다(임시 WAV 로 디코드). 없으면 그 파일을 **못 읽은 것으로 세고 종료코드 3** 입니다 — 분석은 한 줄도 ffmpeg 에 맡기지 않습니다.

---

## 사용법

```bash
# 1단계: 설계 JSON 없이 훑기 — 무엇이 얼마나 어긋났는지 먼저 봅니다 (파일 안 만듦)
stimaudit results/sound_samples/*.wav --inspect

# 그 출력에서 설계 JSON 뼈대를 받아 채웁니다
# (--emit-design 은 표준출력을 JSON 전용으로 씁니다 — 리포트는 화면(표준에러)으로)
stimaudit results/sound_samples/*.wav --inspect --emit-design > 설계.json

# 2단계: 세트 판정 + 자극 기술표
stimaudit results/sound_samples/*.wav --design 설계.json --out-dir 자극점검_202608

# 3단계: 버전 대조 (v1 → v2)
stimaudit 싱잉볼버전_20260803/*.wav --baseline "OPGG_원우님 사운드/" \
  --design 설계.json --out-dir 버전대조

# 교란 축까지 보려면 DEBUSSY 매니페스트를 붙입니다
stimaudit 표준화코퍼스/*.wav --design 설계.json \
  --manifest DEBUSSY_benchmark/09_A1A3_audio/A1A3_manifest.csv --out-dir 결과
```

### 설계 JSON

```json
{
  "study": "RESONATE-pilot",
  "conditions": {
    "active":  ["S1_SO-CLAS.wav", "S2_spindle-target.wav"],
    "control": ["S3_pink.wav"],
    "pacing":  ["S6_breath-pacing.wav"]
  },
  "contrast": "modulation_peak_hz",
  "claims": {
    "S6_breath-pacing.wav": { "mod_hz": 0.1, "duration_s": 20.0 },
    "S3_pink.wav":          { "duration_s": 20.0 }
  },
  "pairs": {
    "싱잉볼_bi_(360+400Hz).wav": "bi_(360-400Hz).wav"
  }
}
```

`pairs` 는 `--baseline` 전용입니다 — **이름이 바뀐 파일**을 옛 이름에 짝지어 줍니다(이름이 같으면 안 적어도 되고, 적지 않으면 짝을 **추측하지 않고** 못 찾았다고 말합니다).

**필수가 아닙니다.** 없으면 파일 위생과 전 파일 쌍 음량 행렬만 냅니다(가치의 절반). 조건 판정과 주장 대조만 설계 JSON 을 요구합니다. 파일은 **basename** 으로 대조합니다(절대경로를 적으면 그 사람 컴퓨터 밖에서 못 씁니다). 지원 주장은 `carrier_hz` · `beat_hz` · `mod_hz` · `duration_s` 넷뿐이며, **파일 이름에서 추측하지 않습니다** — `bi_(360-400Hz).wav` 라는 이름을 보고 40 Hz 맥놀이를 기대하지 않습니다.

### 출력 예시 — 발췌 (번들 예제 `examples/어긋난세트/`)

아래는 **실제 실행 결과 129줄에서 발췌**한 것입니다. 생략한 곳은 전부 `…` 로 표시하고 무엇을 생략했는지 적었습니다 — 리포트가 실제보다 짧고 단정해 보이지 않게 하기 위해서입니다. (이 블록은 `tests/test_round1_fixes.py` 가 실제 출력과 대조하므로, 출력 형식이 바뀌면 테스트가 깨집니다.)

```
$ stimaudit examples/어긋난세트/*.wav --design examples/어긋난세트/설계.json --out-dir 결과

stimaudit — 자극 세트 점검
입력 4개 파일 / 읽음 4 / 못 읽음 0 · 조건 3개 (active 2 · control 1 · binaural 1)

[치명] 6건
  음량 불일치         active ↔ binaural         조건 간 통합 라우드니스 차이 3.5 LU
                                                실측 조건 평균 active -17.3 / binaural -13.8 LUFS · 가장 벌어진 쌍 B_active_loud.wav -20.0 ↔ B_binaural_wrong.wav -13.8 LUFS
                                                기준 허용 1.0 LU · 치명 2.0 LU 초과
                                                조치 active 를 +3.5 dB 하면 binaural 와 맞습니다 (이 툴은 파일을 만들지 않습니다).
                                                → 이 대조는 "효과"와 "소리가 더 컸다"를 구분하지 못합니다.
  음량 불일치         active ↔ control          조건 간 통합 라우드니스 차이 5.7 LU
                                                실측 조건 평균 active -17.3 / control -23.0 LUFS · 가장 벌어진 쌍 B_clipped_dc.wav -14.6 ↔ B_control_pink.wav -23.0 LUFS
                                                기준 허용 1.0 LU · 치명 2.0 LU 초과
                                                조치 control 를 +5.7 dB 하면 active 와 맞습니다 (이 툴은 파일을 만들지 않습니다).
                                                → 이 대조는 "효과"와 "소리가 더 컸다"를 구분하지 못합니다.
  음량 불일치         control ↔ binaural        조건 간 통합 라우드니스 차이 9.2 LU
  … (음량 불일치 1건 · 주장 불일치 2건 생략)
  클리핑              B_clipped_dc.wav          클리핑 구간 1곳 · 총 400샘플
                                                실측 첫 구간 0.50초 (ch1) · 400샘플
                                                기준 연속 3샘플 이상 |x| ≥ −0.1 dBFS
                                                → 파형이 잘려 왜곡이 들어갑니다 — 자극 자체가 설계와 다릅니다.


[경고] 6건
  DC 오프셋           B_clipped_dc.wav          ch1 DC 오프셋 -26.0 dBFS
                                                실측 평균 +0.05013
                                                기준 -60 dBFS 이하
                                                → 스피커에 불필요한 직류가 걸리고 헤드룸을 잡아먹습니다.
  시작/끝 클릭 위험   B_active_loud.wav         선두 무음 0 ms · 상승시간 0.6 ms
                                                실측 1 % → 50 % 진폭까지 0.6 ms
                                                기준 5 ms 미만이면 클릭으로 들릴 수 있음
                                                → 재생 시작에 딸깍 소리가 붙어 각성 자극이 됩니다.
  … (조건 내 음량 산포 · 좌우 불균형 · 트루피크 · 포맷 불일치 4건 생략)

[정보] 논문 참조값 대조 — 판정하지 않습니다. 값만 나란히 놓습니다.
  · 온셋 상승시간 (attack) — 참조 ~50 ms 이상 (수십 ms 이상) · 출처 Foley et al. 2022 (JASA 151:3189) · Hailstone et al. 2009 (QJEP 62:2141) (방향성: Eerola et al. 2012) · reference value · 임계값 아님
      B_active_loud.wav                         0.6 ms
      B_binaural_wrong.wav                     86.9 ms
      B_clipped_dc.wav                        500.0 ms
      B_control_pink.wav                      131.3 ms
  · 템포 / 변조율 — 참조 60–80 BPM (1.0–1.33 Hz) · 출처 Bretherton et al. 2019 · Watanabe et al. 2017 · Krabs et al. 2015 · reference value · 임계값 아님
  · 서파(SO) 자극 반복률 — 참조 ~0.8 Hz · 출처 Schade et al. 2020 (개방루프, 50 ms 버스트를 1.25초마다 = 0.8 Hz) · Ngo et al. 2013 J Sleep Res 22:22–31 (리듬 자극 0.8 Hz) · reference value · 임계값 아님
    (포락선에서 잰 지배적 변조율 하나입니다. 상대강도가 낮으면 주기적 변조가 아니라 잡음의 요동입니다.)
      B_active_loud.wav                       0.736 Hz    (44.2 BPM)  깊이 15.4%   상대강도 0.46  ※ 3주기 미만 — 페이드 모양일 수 있음
      B_binaural_wrong.wav                    0.738 Hz    (44.3 BPM)  깊이 22.8%   상대강도 0.54  ※ 3주기 미만 — 페이드 모양일 수 있음
      B_clipped_dc.wav                        1.250 Hz    (75.0 BPM)  깊이 27.8%   상대강도 0.01  ※ 상대강도 낮음 — 잡음의 요동일 수 있음
      B_control_pink.wav                      1.150 Hz    (69.0 BPM)  깊이 29.4%   상대강도 0.05
  ※ 논문(1st revision)은 이 수치들을 임계값이 아니라 reference value 로 규정합니다.
     "준수/위반"으로 읽지 마십시오. 파일별 티어 판정은 bell_acoustic_qc.py 소관이고,
     stimaudit 은 측정값과 참조값을 나란히 놓기만 합니다.

[정보] 레벨 — 논문 단위 (Table 2 항목 1: LAeq + dynamic range, dBFS 기준)
  파일                                        LAeq       LAmax  다이내믹레인지
  B_active_loud.wav                       -30.0 dB    -29.8 dB          0.9 dB
  B_binaural_wrong.wav                    -23.2 dB    -22.8 dB          7.5 dB
  B_clipped_dc.wav                        -22.4 dB    -15.2 dB          7.5 dB
  B_control_pink.wav                      -27.2 dB    -26.6 dB          7.9 dB
  ※ Czempik et al. (2020, Sci Rep 10:19207) 은 ICU 에서 수면시간과의 상관이 LAmax r = −0.64 (p = 0.0001) 로 LAeq20sec r = −0.41 (p = 0.02) 보다 강하다고 보고했습니다 — 평균보다 순간 최대치가 더 관련된다는 뜻입니다. 57.9 dB 는 '평균적 수면시간보다 짧게 잔 환자'를 가르는 **LAmax 의** ROC 절단점이고(AUC 0.81, 95% CI 0.64–0.93), 참조범위나 권고 상한이 아닙니다. 단일 소음사건의 최대레벨이 등가레벨보다 수면방해를 잘 반영한다는 방향은 교통소음 수면연구의 통합 재분석에서도 보고됩니다 (Basner & McGuire 2018, Int J Environ Res Public Health 15:519 — 실내 Lmax 10 dBA 증가당 각성 오즈비 항공 1.35 · 도로 1.36 · 철도 1.35).
  … (고지 3줄 생략)

[정보] 조건 간 LUFS 차이 행렬  (LUFS 는 논문 유래 아님 — 조건 매칭용 관행 지표)
                 active    control   binaural
  active              —       5.7*       3.5*
  control                        —       9.2*
  binaural                                  —
  (* = --lufs-tol 1.0 LU 초과 · 치명은 2.0 LU 초과)

… (조건 매칭용 LUFS·LRA·트루피크 표와 주장 대조표 생략)

[커버리지 자백]
  읽음: 4파일 / 5채널 / 총 12.0초   ·   못 읽음: 0
  검사한 축: 레벨(LAeq·LAmax·DR·LUFS·LRA·트루피크) · 클리핑 · DC 오프셋 · 앞뒤 무음 · 상승/하강 시간 · 포맷·길이 일관성 · 좌우 균형 · 주장 대조 5건
  검사 안 함:
    · 러프니스 (roughness) — stimaudit 은 심리음향량을 계산하지 않습니다 — DEBUSSY / mosqito 소관. 음악심리학 구현이 내는 러프니스는 무차원 지표라 asper 와 같은 눈금이 아니므로, 매니페스트에 계산 방법을 함께 적으십시오. · 참조 ~0.3 asper 근처 또는 그 아래 · 출처 단위(asper) 정의: Daniel & Weber 1997 · 계산 구현: Harrison & Pearce 2020 · Eerola & Lahdelma 2021 (무차원 러프니스 지표) · 30–150 Hz AM 에 편도체가 선택적 반응(청각피질은 아님): Arnal et al. 2015 · reference value · 임계값 아님
    · 샤프니스 (sharpness) — 개정본: "an exemplar value and remains provisional". 1.5 acum 을 상한으로 보고한 1차 출처는 확인되지 않았습니다 (Zwicker & Fastl 의 불쾌도 모형 문턱은 1.75 acum) — 참조값으로만 읽으십시오. · 참조 ~1.5 acum (개정본이 인용 · 1차 출처 확인 안 됨) · 출처 단위(acum) 정의: Zwicker & Fastl 2007 · 음악 자극에서의 사용례: Eerola & Lahdelma 2022, Psychon Bull Rev 29:800–808 · reference value · 임계값 아님
    · 교란 후보(매니페스트 지표) — --manifest 없음 — DEBUSSY 로 뽑아 붙이십시오
  설계: 조건 3개 · 주장 5건
  교란 후보: 계산 안 함 — --manifest 없음
  분석 소요: 2.2초


치명 6건. 이 상태로 실험에 태우면 안 됩니다.
exit 1
```

`--out-dir` 를 주면 6개 파일(+ `--manifest` 시 1개)이 나옵니다:

| 파일 | 내용 |
|---|---|
| `자극점검.md` | 한국어 리포트 + 커버리지 자백 |
| `문제목록.csv` | 파일·조건·유형·심각도·실측값·기준값·연구상 의미 |
| `자극기술표.csv` / `.md` | Methods 용 자극 기술표 (길이·fs·LUFS·LRA·트루피크·LAeq·LAmax·DR·주장/실측) |
| `음량행렬.csv` | 조건 간(또는 파일 간) LUFS 차이 행렬 |
| `문장초안.md` | KR/EN 자극 기술 문단 — **측정 안 한 축은 문장에서 뺍니다**. 치명이 남아 있으면 맨 위에 "아직 붙이지 마십시오" 경고가 붙습니다 |
| `교란후보.csv` | `--manifest` 를 붙였을 때만 — 조건 간 지표 차이 전체 (화면에는 상위 14개만 인쇄) |

### 종료코드

| 코드 | 의미 |
|---|---|
| 0 | 치명 0건 — 세트로 써도 됨 (검사한 축에 한해서) |
| 1 | 치명 발견 (조건 간 음량 > 2.0 LU · 주장 불일치 · 클리핑 · 죽은 파일) |
| 2 | 입력/옵션 오류 (파일 1개, `--out-dir` 없음, 설계 JSON 이 없는 파일을 가리킴, 절대 SPL 요구, 같은 이름 중복) |
| 3 | 판정불가 — 읽지 못한 파일이 **하나라도** 있음 |

**3 이 1보다 우선합니다.** 다 못 들었으면 "치명 0건"은 거짓말입니다.

---

## 지표와 그 출처

| 지표 | 규격 / 근거 | 검증 |
|---|---|---|
| 통합 라우드니스 (LUFS) | ITU-R BS.1770-4 K-weighting, 400 ms 블록 / 75 % 겹침, 절대 −70 LUFS · 상대 −10 LU 게이트 · 오프셋 −0.691 | K-weighting 계수가 BS.1770-4 의 48 kHz 표와 **기계 정밀도(8.9e-16)까지** 일치. 합성 신호 6종과 **실물 자산 12개**에서 `ffmpeg -af ebur128` 대비 **≤ 0.05 LU** 일치 (HARDENING.md 표) |
| LRA | EBU Tech 3342 v3/v4 — 3 s 블록 / **100 ms 홉**(v3 이 요구하는 2.9 s 이상 겹침), 상대 −20 LU, 10~95 백분위 | 실물 4개에서 ffmpeg 대비 **≤ 0.04 LU**. 파일이 4초 미만이면 블록이 2개 안 나와 계산하지 않고 `—` 로 둡니다 |
| LAeq · LAmax · 다이내믹 레인지 | IEC 61672-1 A-weighting (아날로그 원형을 2차 섹션 3개로 쌍선형 변환, 1 kHz 재정규화). 논문 Table 2 항목 1 | 규격표와 4 kHz 까지 **0.13 dB 이내**. 10 kHz 는 48 kHz 에서 −1.2 dB, 44.1 kHz 에서 −1.5 dB (쌍선형 왜곡, class 1 허용범위 +2.0/−3.0 dB 안) |
| 트루피크 | 4배 오버샘플 **근사** (창 씌운 sinc, DC 이득 1 로 정규화). 표본 피크는 정확한 값 | 클리핑된 실물 자산에서 `ffmpeg ebur128=peak=true` 의 +1.5 dBTP 와 0.07 dB 이내 일치 |
| 클리핑 | 연속 3샘플 이상 \|x\| ≥ −0.1 dBFS 인 구간 | 주입 테스트 (구간 수·위치·길이·채널) |
| 상승/하강 시간 | 파일 피크의 1 % → 50 % 진폭까지 걸린 시간. 머리/꼬리 2초 창 | 페이드 길이를 알고 있는 합성 신호로 고정 |
| 반송주파수 · 맥놀이 | 세그먼트 평균 파워스펙트럼(16384-pt Hann)의 최대 피크 + 포물선 보간. 두드러짐 6 dB 미만이면 "반송음 없음". **채널 피크가 15 % 안에 모이면 그 평균**이 반송주파수(양이 맥놀이 관례), 더 벌어지면 판정불가 — 주장값에 가까운 채널을 고르지 않습니다 | 실물 `bi_` 파일에서 L 349.2 / R 389.2 Hz → 반송 369.2 Hz · 맥놀이 40.0 Hz |
| 변조율 | 제곱 신호에 **40 Hz 2차 버터워스 저역통과**를 건 뒤 10 ms 포락선을 뽑아 FFT. 반송음의 2배 성분이 데시메이션에서 접히는 것을 막습니다. **변조 깊이 0.5 % 미만이면 값을 내지 않고**, 1.5주기 미만 대역은 제외하며, 주장 대조는 3주기 이상 · 20 Hz 이하일 때만 | 실물 `S6_breath-pacing.wav` 0.1002 Hz(설계 0.1 Hz) · `S2_spindle-target.wav` 0.8006 Hz. 순수 톤(50 Hz–15 kHz)은 깊이 ≤ 3e-4 로 "변조 없음", 깊이 2 % AM 은 검출 |

---

## 한계 · 주의 (Notes & limitations)

- **레벨은 전부 dBFS 기준입니다 — 절대 음압이 아닙니다.** WHO(2009, 2018)의 Lnight 40 / 55 dB 같은 참조값과 **직접 비교할 수 없습니다.** 비교하려면 재생 장비의 풀스케일 대응 SPL 을 실측해 보정 상수를 구하십시오.
- **LAmax 는 100 ms 직사각 창의 최댓값**이며, IEC 61672 의 Fast(τ = 125 ms) 지수시간가중이 아닙니다. 지수가중보다 조금 크게 나옵니다.
- **트루피크는 근사**입니다. BS.1770-4 Annex 2 의 계수표 대신 창 씌운 sinc 를 쓰고, 표본 극값 근방만 보간합니다. 표본 피크가 하한이므로 **과소평가는 하되 과대평가는 하지 않습니다.**
- **A-weighting 은 10 kHz 부근에서 규격값보다 약 1.2 dB 낮습니다** (쌍선형 변환의 주파수 왜곡). IEC 61672 class 1 허용범위(+2.0/−3.0 dB) 안이지만, 12.5 kHz 이상이 중요한 자극이면 전용 계측기를 쓰십시오.
- **3채널 이상 파일**은 채널 배치를 알 수 없어 BS.1770 서라운드 가중(1.41)을 붙이지 않고 전부 1.0 으로 둡니다. 리포트가 그 사실을 자백합니다.
- **LRA 는 파일이 4초 미만이면 계산하지 않습니다**(3초 블록이 2개 나오지 않음). 표에는 `—` 로 표시되고 리포트가 그 이유를 인쇄합니다. 번들 예제는 3초라서 항상 `—` 입니다.
- **변조율은 20 Hz 까지만 봅니다.** 10 ms 포락선의 나이퀴스트가 50 Hz 이고 앞단 저역통과가 40 Hz 이기 때문입니다. 30–150 Hz AM(Arnal 2015 의 편도체 대역)이 필요하면 DEBUSSY 의 `modulation_peak_hz` 를 `--manifest` 로 받아 쓰십시오 — 20 Hz 를 넘는 `mod_hz` 주장은 값을 내지 않고 판정불가로 둡니다.
- **채널 수 64개까지만 읽습니다.** 손상된 헤더가 6만 채널을 주장하면 스펙트럼 누적기만으로 수 GB 를 먹기 때문입니다.
- **성능**: 순수 파이썬이라 24bit/48 kHz 스테레오 189초(54 MB)가 **약 13~14초** 걸립니다(Apple Silicon · Python 3.14, 유휴 상태 실측 — 스테레오 1분당 약 4.5초. 머신이 바쁘면 20초를 넘길 수 있습니다). 실측 예: 54~58 MB 24bit 스테레오 **3개(총 438초 오디오, 170 MB)를 69초**에 처리하고 피크 RSS 는 **84 MB** 였습니다. 20~40분짜리 실제 수면 자극이라면 4조건 세트에 **6~10분**을 잡으십시오. 메모리는 **파일 크기에 비례하지 않습니다** — 대부분이 고정 버퍼(64 k 프레임 블록 · 16384-pt 스펙트럼 누적기 · 양끝 2초 샘플)이고, 파일 길이에 비례하는 부분은 10 ms 프레임 요약뿐입니다.
- **원본은 읽기 전용**입니다. 출력은 `--out-dir` 에만 쓰고, 산출물 자리에 심볼릭/하드 링크가 있으면 **거절**합니다(링크를 따라가 원본을 덮어쓰는 사고를 막습니다). CSV 는 수식 인젝션(`= + - @`)을 방어합니다.
- **네트워크에 접속하지 않습니다.** 임상 자료를 다루므로 어떤 값도 밖으로 나가지 않습니다. `examples/` 의 오디오는 전부 `_make_examples.py` 가 계산으로 지어낸 합성음이며, 실제 회사 자산은 커밋되어 있지 않습니다.
- **`--baseline` 짝짓기는 파일 이름이 같은 것끼리**입니다. 이름이 바뀌었으면 설계 JSON 의 `pairs` 에 적으십시오 — 이름 유사도로 추측하지 않습니다.

---

## 테스트

```bash
python3 -m pytest -q      # 546 tests
```

전부 오프라인이고, 기대값은 손으로 계산할 수 있는 것(사인파 RMS, 클리핑 샘플 수, 규격 계수표)만 씁니다. 적대적 검토 기록은 `HARDENING.md` 에 있습니다.

MIT License · Copyright (c) 2026 hyeonjoong
