# calmbark — 반려견 안정 사운드 N-of-1 실험

반려견이 짖으면 마이크로 감지해, 사용자의 NBR 리비전 원고 티어 프레임워크대로
**합성한 저각성 안정 사운드를 자동 재생**하고, 모든 짖음 에피소드·개입 여부를 CSV로
남겨 **"이 소리가 실제로 우리 개의 짖음을 줄이는가"를 N-of-1 실험으로 검증할 수 있게**
하는 오프라인 브라우저 앱입니다. 카탈로그 최초의 데이터 **수집** 도구입니다 —
분석은 statwise / longistat 가 받습니다.

---

## 목적 / Why this exists

**한국어.** 재택 근무 중 옆방에서 개가 택배 기사 소리에 짖기 시작하면, 우리는
유튜브에서 "dog calming music"을 틀어줍니다. 효과가 있는지는 아무도 모릅니다 —
세어본 적이 없으니까요. 그리고 그 영상이 왜 진정 효과가 있다는 건지 근거는 아무
데도 없습니다. calmbark 는 이 두 구멍을 동시에 막습니다. 첫째, 재생되는 소리는
파일이 아니라 **논문 Table 1 의 설계 규칙(안정 레벨, 완만한 어택, 30–150 Hz 변조
회피, 규칙적 패턴, 42–80 BPM, 고주파 제한, 음의 스펙트럼 기울기)을 코드로 옮긴
합성**이고, 그 준수 여부가 `node --test` 회귀 테스트 7종으로 고정되어 있습니다 —
논문 주장을 기계가 검사합니다. 둘째, 효과를 주장하는 대신 **측정**합니다:
에피소드 단위 50% 무작위 개입 모드가 개입/비개입 에피소드의 지속시간·재발을 CSV로
남기고, 판단은 statwise 가 합니다.

**English.** calmbark is a fully offline browser app that detects dog barking via the
microphone and responds with a **synthesized low-arousal sound** whose design rules are
taken from Table 1 of the author's manuscript under revision at *Neuroscience &
Biobehavioral Reviews* — stable level, gradual onsets, avoidance of the 30–150 Hz
amplitude-modulation (roughness) band, predictable structure, slow 42–80 BPM
modulation, limited high-frequency energy, negative spectral slope. Compliance is not
a claim: each rule is pinned by an automated regression test against the rendered PCM.
And efficacy is not a claim either: a per-episode 50% randomized-intervention mode
produces a CSV so the effect can be *tested* (in `statwise`), not asserted. Every
byte stays on your machine — zero network requests, zero audio recording.

> **이 툴이 이기는 지점은 소리가 아니라 검증 가능성입니다.** "강아지 진정 음악"은
> 유튜브에도 많습니다. 유튜브가 절대 주지 않는 것은 (1) 왜 이 소리인지 논문 표
> 행 단위로 답할 수 있는 파라미터 소급성과 (2) 우리 개에게 실제로 효과가 있는지
> 검정할 수 있는 개입/비개입 로그입니다.

---

## 실행

```bash
# 더블클릭: 실행.command   (또는)
cd ~/Downloads/02_프로젝트/깃헙/calmbark-반려견안정사운드
bash 실행.command
```

`python3 -m http.server` 를 **127.0.0.1 전용**으로 띄우고 브라우저를 엽니다.
마이크(getUserMedia)는 보안 문맥에서만 동작하므로 localhost 서빙이 필수입니다
(`file://` 로 열면 마이크가 막힙니다). Python 은 이 서버 역할뿐이고, 앱은 순수
HTML/JS 입니다. 외부 의존성 **0**, CDN·웹폰트·원격 리소스 **0** (grep 테스트로 고정),
네트워크 요청 **0**, 녹음 저장 **0**.

화면의 3단계를 따르세요: **① 3초 보정 → ② 감도 확인 → ③ 모드 선택**.
자세한 절차와 실험 설계 권장안은 `사용법.md` 에 있습니다.

---

## 논문 ↔ 구현 대응표

원고 (1st revision) Table 1 의 티어별 설계 규칙이 어떻게 코드가 되었는지,
그리고 어떤 회귀 테스트가 그것을 고정하는지 (`tests/paper-compliance.test.mjs`,
프리셋 3종 × 7 테스트 + 음성 대조 7종):

| Tier | 파라미터 | 논문 설계 규칙 | calmbark 구현 | 회귀 테스트 (프록시) |
|---|---|---|---|---|
| 1 | Event Structure | 안정 평균 레벨, 순간 Lmax 최소화 | 지속 텍스처 + 느린 AGC 레벨 안정화, 얕은 진폭변조만 | ⑤ 125 ms RMS p95−p5 ≤ 15 dB, max−p50 ≤ 12 dB |
| 1 | Onset Dynamics | 완만한 어택 (수십 ms 이상) | 모든 레벨 상승이 올림코사인 반주기(≈300 ms), 개입 페이드인 1.5 s | ④ 온셋(≥8 dB 돌출) 10–90% 상승 ≥ 50 ms |
| 1 | Roughness | 러프니스 최소화 (Arnal et al. 2015: 편도체가 30–150 Hz AM 주율에 선택 반응) | 이산 트레몰로 없음; 드론 부분음 간격을 2·f0=168–208 Hz 로 설계해 대역 회피 | ② 변조 스펙트럼 30–150 Hz: 이산 피크 ≤ +10 dB & 대역 융기 ≤ 1.0 |
| 1 | Predictability | 규칙적 패턴, 급격한 구조 변화 회피 | 엄격 주기 변조, 루프 무봉합(크로스페이드+정수 사이클+위상 적분 정수) | ⑦ 변조 주기 자기상관 r ≥ 0.7 |
| 2 | Tempo / Rhythm | 문헌 예시 60–80 BPM, 개체 안정 HR 적응 | 느린 진폭변조 0.8 / 1.0 / 1.1 Hz (48–66 BPM) | ③ 지배 변조 주율 0.7–1.33 Hz |
| 2 | Sharpness | 고주파 에너지 제한 (1.5 acum 은 잠정값) | 저역통과 캐스케이드 — 초음파 성분 구조적 0 | ⑥ >4 kHz 에너지 ≤ 5% |
| 2 | Pitch | 저–중역 중심, 하강 음형 | 드론 프리셋: 84–104 Hz, 12 s 반코사인 하강 음형 | (전용 테스트 없음 — 드론 스펙트럼이 ①⑥ 에 포함) |
| 2 | Spectral Slope | 음의 기울기, 핑크~브라운 | 브라운 기울기(β≈−2)·핑크(β≈−1) 캐리어 | ① log-log PSD 회귀(100 Hz–8 kHz) β ≤ −0.5 |
| 2 | Complexity | 저복잡도 기악 텍스처 | 무선율 잡음/드론 텍스처 (구조적으로 저복잡도) | (전용 테스트 없음) |
| 3 | Semantic Content | 기악 기본, 가사는 통제 시에만 | 전부 합성 — 언어 성분 자동 부재 | (합성이므로 구조적 충족) |
| 3 | Harmonicity · Familiarity | 보편 처방 불가 | 처방하지 않음 (개체 선호 프로파일링은 범위 밖) | — |

**프록시 고지:** 위 테스트 지표는 정식 심리음향 단위(asper 러프니스, acum 샤프니스,
DIN 45692)가 아니라 그 방향의 위반을 잡는 보수적 스칼라입니다. 각 테스트 주석에
어떤 파라미터의 프록시인지 명시되어 있고, 음성 대조 테스트(일부러 규칙을 어긴
신호 7종)가 지표의 판별력을 증명합니다. 정식 측정이 필요하면 같은 저자의
DEBUSSY(11항목 추출기)로 렌더된 WAV 를 교차검증할 수 있습니다.

---

## 실험 모드와 산출물

| 모드 | 동작 | 용도 |
|---|---|---|
| 관찰 | 감지·기록만, 재생 없음 | 기저선 수집 (권장 시작점) |
| 상시 개입 | 모든 에피소드에 재생 | 적응/기기 확인용 — 효과 비교 불가 |
| **무작위 개입** | **에피소드 단위 50% 배정, seed 기록** | **N-of-1 비교 (권장)** |

CSV 두 벌을 내려받습니다 (UTF-8 BOM, 수식 인젝션 가드 적용):

- `calmbark_이벤트_YYYYMMDD_HHMM.csv` — `타임스탬프,유형,에피소드ID,상세`
  (세션시작·보정완료·에피소드시작·**배정**(seed 포함)·짖음·**짖음(재생중)**·재생시작·재생종료·에피소드종료·세션종료)
- `calmbark_에피소드_YYYYMMDD_HHMM.csv` — `에피소드ID,시작,종료,짖음횟수,지속초,개입여부,모드,seed`

에피소드 규칙: 마지막 짖음 종료 후 30초 안 재짖음은 같은 에피소드.

하류 연결:

```bash
statwise calmbark_에피소드_*.csv --value 지속초 --group 개입여부     # 개입/비개입 비교
longistat 여러날병합.csv --id 날짜 --time 세션 --value 지속초        # 일차 추이
```

> **주의 — 에피소드는 독립 관측이 아닐 수 있습니다.** 같은 날의 연속 에피소드는
> 상관될 수 있습니다(자극원 지속, 흥분 이월). 화면 요약은 기술통계까지만 보여주며,
> 표본이 작으면 차이는 우연입니다 — 검정은 statwise 로 하고, 여러 날 수집을 권합니다.

---

## 정직성 절 (Honesty)

- **효과는 미검증입니다.** 이 앱은 효과를 검증할 수 있게 하는 도구이지, 효과가
  검증된 도구가 아닙니다. "효과 없음"이 결과여도 도구로서는 성공입니다.
- **종간 외삽입니다.** 근거 프레임워크는 사람 대상 문헌의 종합입니다. 개의 청각
  범위·정서 반응은 다르며, 개 대상 파라미터 검증은 존재하지 않습니다. 이 앱이
  만드는 데이터가 바로 그 공백을 개인 수준에서 메우는 시도입니다.
- **감지는 휴리스틱입니다.** 250–4000 Hz 대역 에너지 문턱 — ML 분류가 아닙니다.
  문 닫는 소리·TV·박수가 짖음으로 기록될 수 있고(오탐), 낮은 낑낑거림은 놓칠 수
  있습니다(미탐). 오탐 의심 행(짖음 1회·지속 0초대)은 CSV에서 사후 식별하세요.
- **재생음 자기 감지 가능성.** 재생 중 감지는 문턱 +6 dB·소음 바닥 동결로 완화했고
  `짖음(재생중)` 유형으로 따로 기록되지만, 스피커·방 배치에 따라 자기 트리거가
  완전히 없다고 보장할 수 없습니다. 재생 중 행은 보수적으로 해석하세요.
- **수의학적 조언이 아닙니다.** 심한 분리불안·자해 수준의 반응이 의심되면
  수의사·행동 전문가와 상담하세요. 혐오 자극(초음파 목걸이류)과 정반대 접근이지만,
  그렇다고 치료가 되는 것은 아닙니다.
- **볼륨은 디지털 게인 %일 뿐입니다.** 절대 SPL 보정이 없습니다 — 실제 음압은
  스피커·거리·방에 좌우됩니다. 개의 청각은 사람보다 민감하므로 낮게 시작해
  사람 귀 기준 "배경음 수준"을 넘기지 마세요.

## 한계 (솔직하게)

- **통계를 하지 않습니다.** 화면 비교표는 기술통계뿐입니다. 검정은 statwise,
  추이는 longistat 의 몫입니다.
- **온셋 테스트는 8 dB 미만 돌출을 보지 않습니다.** 가우스 텍스처의 제거 불가능한
  자체 요동(≈4σ ≤ 7.5 dB) 아래는 "이벤트"로 정의하지 않았습니다(HARDENING.md).
- **논문 준수 테스트는 44.1 kHz·고정 시드 기준입니다.** 48 kHz 는 스모크 테스트만
  합니다. 임의 시드 10종 스윕으로 여유를 확인했지만 모든 시드를 보장하진 않습니다.
- **백그라운드 탭에서는 감지가 거칠어집니다.** 타이머가 초당 1회 수준으로 스로틀되어
  1초 미만 버스트를 놓칠 수 있습니다. 세션 중에는 탭을 전면에 두세요.
- **다일 스케줄링·모바일 패키징·고양이(타 축종) 모드는 없습니다.** 세션 단위
  도구입니다. 타 축종은 감지 대역·프리셋 검증이 전혀 없으므로 지원하지 않습니다.
- **감지 프레임 계층(app.js)은 자동 테스트 밖입니다.** 순수 로직(엔진·감지기·집계)은
  73개 테스트로 고정했지만 WebAudio 접착층은 수동 스모크만 거쳤습니다.
- **echoCancellation 을 껐습니다.** 켜면 OS가 스피커 소리와 함께 개 소리 특성도
  왜곡할 수 있어서입니다. 대신 자기 트리거 완화는 문턱·동결·별도 로그로 처리합니다.

---

## 테스트

```bash
node --test        # 73개, 완전 오프라인·결정론 (외부 패키지 0)
```

가장 중요한 것은 `tests/paper-compliance.test.mjs` — 프리셋 3종 × 논문 준수 7종
(기울기·러프니스·템포·온셋·다이내믹레인지·샤프니스·예측가능성) + **음성 대조 7종**
(백색소음·70 Hz 트레몰로·대역 잡음 변조·클릭 버스트·고역 잡음·무작위 엔벨로프·3 Hz
변조가 각각 제대로 *걸리는지*)입니다. 이 대조가 없으면 준수 테스트는 무엇이든
통과시키는 장식일 수 있습니다. `tests/purity.test.mjs` 는 외부 URL 참조 0 과 순수
모듈의 DOM/WebAudio 무접촉을 소스 grep 으로 고정합니다.

적대적 검증 기록은 `HARDENING.md` 에 있습니다.

## Citation

이 앱의 사운드 설계 규칙과 티어 프레임워크의 출처:

Kim H-J, Ha J, Park J, Thayer JF, Bosi M, Eerola T.
*Acoustic Parameters for Autonomic Arousal Modulation: A Narrative Review and
Parameter-Level Evidence Framework.* (1st revision, in revision at
*Neuroscience & Biobehavioral Reviews*.)

러프니스 회피 대역(30–150 Hz)의 1차 근거:
Arnal LH, Flinker A, Kleinschmidt A, Giraud A-L, Poeppel D. Human screams occupy
a privileged niche in the communication soundscape. *Current Biology*. 2015.

## 라이선스

MIT © 2026 hyeonjoong
