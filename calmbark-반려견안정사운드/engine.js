// engine.js — 안정 사운드 합성 엔진 (순수 로직 모듈)
//
// NBR 1st revision Table 1 의 Tier 1/2 설계 규칙을 실행 가능한 합성으로 옮긴다.
// 이 모듈은 DOM / WebAudio / 네트워크를 일절 참조하지 않는다 — 숫자를 받아
// Float32Array PCM 버퍼를 돌려줄 뿐이다. 같은 인자 = 같은 버퍼 (mulberry32 시드).
// 브라우저(app.js)와 node --test(tests/) 양쪽에서 그대로 import 된다.
//
// 설계 규칙 ↔ 구현 대응 (tests/paper-compliance.test.mjs 가 회귀로 고정):
//   Tier 1 Event Structure  → 지속 텍스처, 순간 피크 없음, 얕은 진폭변조만
//   Tier 1 Onset Dynamics   → 모든 레벨 상승이 완만한 코사인 반주기 (수백 ms)
//   Tier 1 Roughness        → 30–150 Hz 진폭변조 성분 회피 (Arnal et al. 2015 —
//                             편도체가 30–150 Hz AM 주율에 선택 반응하는 대역).
//                             잡음 프리셋: 이산 AM 없음. 드론 프리셋: 부분음
//                             간격을 150 Hz 초과(2·f0 = 168–208 Hz)로 설계
//   Tier 1 Predictability   → 엄격히 주기적인 변조, 루프 경계 무봉합(크로스페이드
//                             + 정수 사이클 + 위상 적분이 정수가 되는 음형).
//                             캐리어의 100 Hz 미만 에너지를 제한해 비주기적
//                             느린 레벨 방황(엔벨로프 랜덤워크)도 구조적으로 억제
//   Tier 2 Tempo/Rhythm     → 느린 진폭변조 0.7–1.33 Hz (42–80 BPM)
//   Tier 2 Sharpness        → 저역통과 캐스케이드 — 4 kHz 초과 에너지 구조적 차단
//                             (부수 효과로 초음파 성분 0)
//   Tier 2 Pitch            → 드론: 저역 중심(84–104 Hz) + 느린 하강 음형
//   Tier 2 Spectral Slope   → β≈−1(핑크)~−2(브라운) — 회귀 대역 100 Hz–8 kHz 기준
//   Tier 3 Semantic Content → 전부 합성 — 가사·언어 성분 자동 부재

// ---------------------------------------------------------------- PRNG

/** mulberry32 — 결정론적 32비트 PRNG. 같은 시드 = 같은 수열. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------- 프리셋 정의

/**
 * amHz: 느린 진폭변조 주율(Hz). 0.7–1.33 안이어야 하며(Tier 2 Tempo),
 *       루프 길이 × amHz 가 정수가 되도록 골라 루프 경계가 이어진다.
 * amDepth: 변조 깊이 0..1. 얕게 유지해야 다이내믹레인지 ≤15 dB (Tier 1) —
 *       동시에 잡음 캐리어의 확률적 요동보다는 깊어야 주기 구조가 지배한다
 *       (Tier 1 Predictability).
 */
export const PRESETS = {
  brown_waves: {
    label: "브라운 파도",
    description: "브라운 기울기(β≈−2) 잡음에 1 Hz(60 BPM) 파도형 진폭변조",
    amHz: 1.0,
    amDepth: 0.55,
  },
  pink_low: {
    label: "핑크 저역",
    description: "저역통과 핑크 잡음(β≈−1)에 1.1 Hz(66 BPM) 진폭변조",
    amHz: 1.1,
    amDepth: 0.5,
  },
  drone_descend: {
    label: "저주파 드론 (느린 하강)",
    description: "84–104 Hz 사인 드론 + 느린 하강 음형 + 저역 잡음 바닥, 1 Hz(60 BPM) 변조",
    amHz: 1.0, // 라운드 1 M9: 0.8 Hz(48 BPM)는 문헌 예시 대역(60–80 BPM) 밖이라 60 BPM 으로 상향
    amDepth: 0.4,
  },
};

export const DEFAULT_LOOP_SECONDS = 30;

/**
 * 검증 시드 목록 (라운드 1 C2). 논문 준수 스위프 테스트가 이 16개 시드 × 3
 * 프리셋 전부에서 ②(러프니스 대역 융기 ≤ 0.90)·④(온셋 상승 ≥ 60 ms — 판정
 * 임계 50 ms 에 여유)를 강제한다. 앱의 자동 세션 시드는 이 목록에서만 뽑고,
 * 직접 입력한 시드는 허용하되 이벤트 로그에 `시드(미검증)` 으로 남긴다.
 */
export const VETTED_SEEDS = [1, 2, 3, 5, 6, 8, 11, 13, 14, 15, 16, 21, 23, 24, 51738, 20260821];

export const DEFAULT_SEED = 0xca1a; // 렌더 기본값(테스트 편의) — 앱 세션 시드는 VETTED_SEEDS 에서

// ---------------------------------------------------------------- 내부 빌딩블록

/** 1차 저역통과 (one-pole). 컷오프 위에서 파워 기울기 β −2. */
function onePoleLp(x, sampleRate, cutoffHz) {
  const a = 1 - Math.exp((-2 * Math.PI * cutoffHz) / sampleRate);
  let y = 0;
  const out = new Float32Array(x.length);
  for (let i = 0; i < x.length; i++) {
    y += a * (x[i] - y);
    out[i] = y;
  }
  return out;
}

/** 1차 고역통과 — 컷오프 아래(초저역 럼블)를 걷어낸다. */
function onePoleHp(x, sampleRate, cutoffHz) {
  const a = 1 - Math.exp((-2 * Math.PI * cutoffHz) / sampleRate);
  let lp = 0;
  const out = new Float32Array(x.length);
  for (let i = 0; i < x.length; i++) {
    lp += a * (x[i] - lp);
    out[i] = x[i] - lp;
  }
  return out;
}

/**
 * 루프 무봉합 크로스페이드: raw(길이 N+F)를 받아 앞 F 샘플에 꼬리(raw[N..N+F))를
 * 등파워로 섞은 길이 N 버퍼를 돌려준다. 결과의 [N-1]→[0] 이행은 raw 의
 * 연속 두 샘플 이행과 동일 — 루프 클릭 없음 (Tier 1 Predictability:
 * abrupt structural change 회피).
 */
function loopCrossfade(raw, n, fadeLen) {
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = raw[i];
  for (let i = 0; i < fadeLen; i++) {
    const gIn = Math.sin((Math.PI / 2) * (i / fadeLen));
    const gOut = Math.cos((Math.PI / 2) * (i / fadeLen));
    out[i] = raw[i] * gIn + raw[n + i] * gOut;
  }
  return out;
}

/**
 * 느린 진폭변조 엔벨로프. (1−depth)..1 범위의 올림 코사인 —
 * 골→마루 10–90% 상승이 반주기의 ~59% (amHz 1 Hz면 ≈295 ms ≥ 50 ms,
 * Tier 1 Onset Dynamics). 루프에 정수 사이클이 들어가야 한다.
 */
function amEnvelope(i, sampleRate, amHz, depth) {
  const t = i / sampleRate;
  return 1 - depth + depth * 0.5 * (1 - Math.cos(2 * Math.PI * amHz * t));
}

/**
 * 브라운 기울기 잡음: 백색 → one-pole LP(cornerHz).
 * 코너 위에서 β≈−2 (브라운). 코너를 수백 Hz(브라운 캐리어 300 Hz)에 두는 이유: 누설 적분기
 * (코너 ~8 Hz)식 브라운은 에너지가 DC 근처에 몰려 엔벨로프가 비주기적으로
 * 느리게 방황한다(랜덤워크 레벨). 그 방황은 Predictability(⑦)와 Onset(④)
 * 프록시를 실제로 악화시키는 물리적 실체라, 스펙트럼 기울기를 유지하면서
 * 대역폭을 넓혀(≈수백 Hz) 요동을 빠르게 만들고 평활로 지워지게 한다.
 */
function brownSlopeNoise(n, sampleRate, rng, cornerHz) {
  const white = new Float32Array(n);
  for (let i = 0; i < n; i++) white[i] = rng() * 2 - 1;
  return onePoleLp(white, sampleRate, cornerHz);
}

/** 핑크 잡음 (Paul Kellet 근사, β≈−1). */
function pinkNoise(n, rng) {
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const w = rng() * 2 - 1;
    b0 = 0.99886 * b0 + w * 0.0555179;
    b1 = 0.99332 * b1 + w * 0.0750759;
    b2 = 0.969 * b2 + w * 0.153852;
    b3 = 0.8665 * b3 + w * 0.3104856;
    b4 = 0.55 * b4 + w * 0.5329522;
    b5 = -0.7616 * b5 - w * 0.016898;
    out[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362) * 0.11;
    b6 = w * 0.115926;
  }
  return out;
}

/**
 * 레벨 안정화(느린 AGC): |x|의 one-pole 추적기(trackHz)로 캐리어를 나눠
 * trackHz 미만의 확률적 레벨 방황을 제거한다. 가우스 잡음의 엔벨로프 요동
 * (σ/μ≈0.52)은 제거 불가능한 물리량이지만, 그 "느린 성분"이 비주기적 레벨
 * 이벤트처럼 들리는 것은 설계로 막을 수 있다 (Tier 1 Event Structure /
 * Predictability). 이 뒤에 걸리는 의도적 AM(0.7–1.33 Hz)은 이 함수 이후에
 * 적용되므로 억제되지 않는다.
 */
function stabilizeLevel(x, sampleRate, trackHz) {
  const n = x.length;
  let mean = 0;
  for (let i = 0; i < n; i++) mean += Math.abs(x[i]);
  mean /= n;
  if (mean === 0) return x;
  const a = 1 - Math.exp((-2 * Math.PI * trackHz) / sampleRate);
  let tr = mean; // 평균으로 초기화 — 웜업 과도 없음
  const floor = 0.05 * mean;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    tr += a * (Math.abs(x[i]) - tr);
    out[i] = (x[i] * mean) / Math.max(tr, floor);
  }
  return out;
}

/** 영위상 저역통과: one-pole 를 순방향+역방향으로 적용 (오프라인 렌더라 가능).
 *  순 응답 = 1/(1+(f/fc)²), 위상 오차 0 — 대역 분리에 위상 지연 문제가 없다. */
function zeroPhaseLp(arr, sampleRate, fc, initValue) {
  const a = 1 - Math.exp((-2 * Math.PI * fc) / sampleRate);
  const n = arr.length;
  const out = new Float64Array(n);
  let y = initValue;
  for (let i = 0; i < n; i++) { y += a * (arr[i] - y); out[i] = y; }
  y = initValue;
  for (let i = n - 1; i >= 0; i--) { y += a * (out[i] - y); out[i] = y; }
  return out;
}

/**
 * 엔벨로프 정칙화 (라운드 1 C2): 정류 신호의 영위상 엔벨로프(≤300 Hz)를
 * 다시 영위상 20 Hz 로 평활한 "목표 엔벨로프"로 치환한다 —
 * g = envSlow / envFast 를 곱하면 출력 엔벨로프 ≈ envSlow 가 되어
 * 20 Hz 초과 주율의 확률적 엔벨로프 요동(러프니스 대역 30–150 Hz 포함)이
 * 주율에 따라 1/(1+(f/20)²) 로 억제된다: 150 Hz 요동은 ~2%만 남고,
 * ② 분모 대역(5–25 Hz)은 대부분 보존된다. 위상 오차가 없어 대역 경계에서
 * 역효과(라운드 1 스위프에서 관찰된 융기 악화)가 없다.
 * 의도적 AM(0.7–1.33 Hz)은 이 함수 이후에 곱해지므로 영향받지 않는다.
 * 목적: 시드와 무관하게 ② 대역 융기 ≤0.90, ④ 온셋 상승 ≥60 ms 보장
 * (라운드 1 C2 — seed 8/15 에서 ② 융기 >1.0 재현 결함의 구조적 수정).
 */
function envelopeRegularize(x, sampleRate) {
  const n = x.length;
  let mean = 0;
  for (let i = 0; i < n; i++) mean += Math.abs(x[i]);
  mean /= n;
  if (mean === 0) return x;
  const rect = new Float64Array(n);
  for (let i = 0; i < n; i++) rect[i] = Math.abs(x[i]);
  const envFast = zeroPhaseLp(rect, sampleRate, 300, mean);
  const envSlow = zeroPhaseLp(envFast, sampleRate, 20, mean);
  const floor = 0.25 * mean; // 골 과증폭(크레스트 상승) 방지
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const g = Math.max(envSlow[i], floor) / Math.max(envFast[i], floor);
    out[i] = x[i] * Math.min(g, 3);
  }
  return out;
}

function normalizePeak(x, peak) {
  let m = 0;
  for (let i = 0; i < x.length; i++) {
    const a = Math.abs(x[i]);
    if (a > m) m = a;
  }
  if (m === 0) return x;
  const g = peak / m;
  for (let i = 0; i < x.length; i++) x[i] *= g;
  return x;
}

// ---------------------------------------------------------------- 드론 음형

const DRONE = {
  contourPeriodSec: 15, // 하강 12 s + 복귀 3 s
  fMean: 94,            // 구간 평균 94 Hz → 위상 적분이 정수 (94×15 = 1410 사이클)
  fSwing: 10,           // 104 → 84 Hz
  // 부분음: f 와 3f 만 사용. 간격 2·f0 = 168–208 Hz — 러프니스 대역(30–150 Hz)
  // 초과로 설계해 부분음 맥놀이(엔벨로프 변조)가 Arnal 대역에 들어가지 않는다.
  // (f 와 2f 를 쓰면 간격 f0 = 84–104 Hz 가 대역 한복판에 떨어진다.)
  partials: [
    { mult: 1, gain: 1.0 },
    { mult: 3, gain: 0.35 },
  ],
  bedGainDb: -26, // 저역 잡음 바닥 (드론 RMS 대비) — 라운드 1 C2: −20 → −26,
                  // 바닥×부분음 교차항이 30–150 Hz 변조 융기의 주범이라 6 dB 더 낮춤
};

/**
 * 하강 음형 주파수 궤적 f(t) [Hz]. 주기 15 s:
 *   0–12 s: 104 → 84 로 반코사인 하강 (Tier 2 Pitch: descending contours)
 *   12–15 s: 84 → 104 로 반코사인 복귀 (다음 하강 준비 — 온셋 없음, 주파수만 이동)
 * 각 구간 평균이 fMean 이라 루프 위상 적분이 정수 → 루프 무봉합.
 */
function droneFreqAt(tInPeriod) {
  const { fMean, fSwing } = DRONE;
  if (tInPeriod < 12) return fMean + fSwing * Math.cos((Math.PI * tInPeriod) / 12);
  return fMean - fSwing * Math.cos((Math.PI * (tInPeriod - 12)) / 3);
}

// ---------------------------------------------------------------- 렌더러

/**
 * 프리셋 하나를 루프 가능한 PCM 버퍼로 렌더한다.
 * @param {string} presetId  PRESETS 키
 * @param {number} sampleRate 예: 44100, 48000
 * @param {number} seconds   루프 길이(초). seconds × amHz 는 정수여야 한다.
 * @param {number} seed      결정론 시드
 * @returns {Float32Array}   피크 0.85 정규화, 루프 경계 무봉합
 */
export function renderPreset(presetId, sampleRate, seconds = DEFAULT_LOOP_SECONDS, seed = DEFAULT_SEED) {
  const preset = PRESETS[presetId];
  if (!preset) throw new Error("알 수 없는 프리셋: " + presetId);
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    throw new Error("샘플레이트가 유효하지 않음: " + sampleRate); // 라운드 1 M6
  }
  if (!Number.isFinite(seconds) || seconds <= 0) {
    throw new Error("루프 길이(초)가 유효하지 않음: " + seconds); // 라운드 1 M6
  }
  const cycles = seconds * preset.amHz;
  if (Math.abs(cycles - Math.round(cycles)) > 1e-9) {
    throw new Error(`루프 ${seconds}s × ${preset.amHz}Hz = ${cycles} — 정수 사이클이 아님 (루프 경계가 끊긴다)`);
  }
  if (presetId === "drone_descend" && Math.abs((seconds / DRONE.contourPeriodSec) % 1) > 1e-9) {
    throw new Error(`드론 루프 길이는 음형 주기 ${DRONE.contourPeriodSec}s 의 배수여야 함`);
  }
  const n = Math.round(sampleRate * seconds);
  const fadeLen = Math.round(sampleRate * 0.25);
  const rng = mulberry32(seed);

  let carrier;
  if (presetId === "brown_waves") {
    // β≈−2 (코너 300 Hz 위) → 저역통과 2단(1.2 kHz)로 고주파 추가 차단 (Tier 2 Sharpness)
    let raw = brownSlopeNoise(n + fadeLen, sampleRate, rng, 300);
    raw = onePoleLp(raw, sampleRate, 1200);
    raw = onePoleLp(raw, sampleRate, 1200);
    carrier = stabilizeLevel(loopCrossfade(raw, n, fadeLen), sampleRate, 3);
    carrier = envelopeRegularize(carrier, sampleRate);
  } else if (presetId === "pink_low") {
    // β≈−1 → 초저역 럼블 제거(HP 80 — 레벨 방황 억제) → 저역통과 2단(1.8 kHz)
    let raw = pinkNoise(n + fadeLen, rng);
    raw = onePoleHp(raw, sampleRate, 80);
    raw = onePoleLp(raw, sampleRate, 1800);
    raw = onePoleLp(raw, sampleRate, 1800);
    carrier = stabilizeLevel(loopCrossfade(raw, n, fadeLen), sampleRate, 3);
    carrier = envelopeRegularize(carrier, sampleRate);
  } else {
    // 드론: 사인 부분음(위상 적분 → 루프 연속) + 저역 잡음 바닥(크로스페이드)
    carrier = new Float32Array(n);
    for (const p of DRONE.partials) {
      let phase = 0;
      for (let i = 0; i < n; i++) {
        const tInPeriod = (i / sampleRate) % DRONE.contourPeriodSec;
        phase += (2 * Math.PI * p.mult * droneFreqAt(tInPeriod)) / sampleRate;
        carrier[i] += p.gain * Math.sin(phase);
      }
    }
    let bedRaw = brownSlopeNoise(n + fadeLen, sampleRate, rng, 120);
    bedRaw = onePoleLp(bedRaw, sampleRate, 400);
    bedRaw = onePoleLp(bedRaw, sampleRate, 400);
    const bed = loopCrossfade(bedRaw, n, fadeLen);
    // 잡음 바닥을 드론 RMS 대비 bedGainDb 로 스케일
    let rmsC = 0, rmsB = 0;
    for (let i = 0; i < n; i++) { rmsC += carrier[i] * carrier[i]; rmsB += bed[i] * bed[i]; }
    const bedScale = Math.sqrt(rmsC / Math.max(rmsB, 1e-20)) * Math.pow(10, DRONE.bedGainDb / 20);
    for (let i = 0; i < n; i++) carrier[i] += bed[i] * bedScale;
  }

  // 느린 진폭변조 (Tier 2 Tempo) — 정수 사이클이라 루프 경계에서 연속
  for (let i = 0; i < n; i++) {
    carrier[i] *= amEnvelope(i, sampleRate, preset.amHz, preset.amDepth);
  }
  return normalizePeak(carrier, 0.85);
}
