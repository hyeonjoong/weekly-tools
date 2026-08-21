// tests/paper-compliance.test.mjs
//
// ★ 이 파일이 이 툴의 존재 이유다 ★
//
// NBR 1st revision 원고 "Acoustic Parameters for Autonomic Arousal Modulation"
// (Kim, Ha, Park, Thayer, Bosi, Eerola — in revision) Table 1 의 Tier 1/2 설계
// 규칙을, 엔진이 합성한 각 프리셋의 PCM 버퍼에 대한 7종 회귀 테스트로 고정한다.
//
// 모든 임계값은 하드코딩이며, 각 지표는 논문 파라미터의 PROXY(대용 측정)다 —
// 정식 심리음향 단위(asper, acum)가 아니라 "그 방향의 위반이 생기면 반드시
// 걸리는" 보수적 스칼라다. 엔진 코드를 바꿔서 이 테스트가 깨지면, 그 변경은
// 논문 설계 규칙을 벗어난 것이다.
//
// 파일 끝의 음성 대조(negative control) 테스트들은 일부러 규칙을 어긴 신호가
// 실제로 각 지표에 걸리는지 확인한다 — 테스트가 항상 통과하는 장식이 아니라
// 판별력이 있음을 증명하기 위해서다.

import { test } from "node:test";
import assert from "node:assert/strict";
import { PRESETS, renderPreset, mulberry32 } from "../engine.js";
import {
  spectralSlopeBeta,
  modulationSpectrum,
  roughnessProxy,
  dominantModulationHz,
  onsetRises,
  levelPercentileRangeDb,
  highFrequencyRatio,
  envelopePeriodicity,
} from "./helpers/dsp.mjs";

const SR = 44100;        // 실제 앱과 같은 샘플레이트
const SECONDS = 30;      // 프리셋 루프 길이 (엔진 기본과 동일)
const SEED = 20260821;   // 결정론 — 어떤 시드든 통과해야 하지만 테스트는 고정 시드

// ---- 프리셋별 버퍼/변조스펙트럼 캐시 (테스트 7개가 같은 버퍼를 공유) ----
const cache = new Map();
function rendered(id) {
  if (!cache.has(id)) {
    const x = renderPreset(id, SR, SECONDS, SEED);
    cache.set(id, { x, ms: null });
  }
  return cache.get(id);
}
function modspec(id) {
  const c = rendered(id);
  if (!c.ms) c.ms = modulationSpectrum(c.x, SR);
  return c.ms;
}

const presetIds = Object.keys(PRESETS);
assert.equal(presetIds.length, 3, "프리셋은 정확히 3개 (기획 문서 §범위 3)");

for (const id of presetIds) {
  // ------------------------------------------------------------------
  // ① 스펙트럼 기울기 β < 0
  // PROXY 근거: Table 1 Tier 2 "Spectral Slope" — Negative slope;
  // pink-to-brown range. Table 2 item 7은 log-log 회귀 주파수 범위 보고를
  // 요구하므로 여기 명시: 100 Hz – 8 kHz, Welch/Hann 16384.
  // 논문 요구는 β<0 이지만 측정 잡음 여유를 두고 β ≤ −0.5 로 고정한다
  // (핑크 ≈ −1, 브라운 ≈ −2 이므로 정상 엔진에는 넉넉한 기준).
  // ------------------------------------------------------------------
  test(`[${id}] ① 스펙트럼 기울기 β<0 (핑크~브라운)`, () => {
    const beta = spectralSlopeBeta(rendered(id).x, SR, 100, 8000);
    assert.ok(beta <= -0.5, `β=${beta.toFixed(3)} — −0.5 이하가 아님`);
  });

  // ------------------------------------------------------------------
  // ② 30–150 Hz 변조(러프니스) 대역에 유의 에너지 없음
  // PROXY 근거: Table 1 Tier 1 "Roughness" — 편도체가 30–150 Hz 진폭변조
  // 주율에 선택적으로 반응한다는 Arnal et al. (2015)의 대역. asper 단위
  // 러프니스(Zwicker/Daniel–Weber) 대신 진폭 엔벨로프 변조 스펙트럼에서
  //  (a) 대역 안에 20–190 Hz 중앙값보다 10 dB 이상 솟은 이산 피크(트레몰로) 없음
  //  (b) 대역 평균 PSD가 인접한 더 느린 대역(5–25 Hz) 평균을 넘지 않음
  //      (대역 제한 러프니스 잡음 주입도 잡는다)
  // 을 요구한다. 참고: 광대역 가우스 잡음은 엔벨로프 요동(σ/μ≈0.52)이
  // 본질적으로 있어 대역 파워가 0이 되지는 않는다 — 판정 기준은 "추가로
  // 주입된 유의 변조 성분이 없는가"다.
  // ------------------------------------------------------------------
  test(`[${id}] ② 러프니스 대역(30–150 Hz) 유의 변조 성분 없음 — Arnal 2015`, () => {
    const r = roughnessProxy(modspec(id));
    assert.ok(
      r.peakOverMedianDb <= 10,
      `30–150 Hz 안 이산 피크 +${r.peakOverMedianDb.toFixed(1)} dB > 10 dB (트레몰로 의심)`
    );
    assert.ok(
      r.bandElevation <= 1.0,
      `30–150 Hz 평균 PSD가 5–25 Hz 평균의 ${r.bandElevation.toFixed(2)}배 — 대역 융기(러프니스 에너지 주입)`
    );
  });

  // ------------------------------------------------------------------
  // ③ 지배적 느린 변조 0.7–1.33 Hz
  // PROXY 근거: Table 1 Tier 2 "Tempo/Rhythm" — 문헌 예시값 60–80 BPM,
  // 기획은 42–80 BPM(0.7–1.33 Hz) 허용·기본 1 Hz. 변조 스펙트럼(0.25–16 Hz)
  // 최대 피크가 이 창 안이어야 한다.
  // ------------------------------------------------------------------
  test(`[${id}] ③ 지배적 변조 주율 0.7–1.33 Hz (42–80 BPM)`, () => {
    const hz = dominantModulationHz(modspec(id));
    assert.ok(hz >= 0.7 && hz <= 1.33, `지배 변조 ${hz.toFixed(3)} Hz — 0.7–1.33 밖`);
  });

  // ------------------------------------------------------------------
  // ④ 모든 온셋 10–90% 상승 ≥ 50 ms
  // PROXY 근거: Table 1 Tier 1 "Onset Dynamics" — gradual attack, 문헌
  // 기준값은 수십 ms 이상. 엔벨로프(30 ms 평활, 웜업 0.5 s 제외)에서
  // ≥8 dB 골→마루 상승("온셋 이벤트" — 텍스처 자체 요동 ≈4σ 초과)을 전부
  // 찾아 10–90% 상승 시간이 50 ms 미만인 것이 하나라도 있으면 실패.
  // 상승이 아예 없으면(무이벤트 정상 텍스처) 통과. 임계 근거는 dsp.mjs 참조.
  // ------------------------------------------------------------------
  test(`[${id}] ④ 온셋 10–90% 상승 ≥ 50 ms`, () => {
    const rises = onsetRises(rendered(id).x, SR);
    const bad = rises.filter((r) => r.riseMs < 50);
    assert.equal(
      bad.length, 0,
      `50 ms 미만 온셋 ${bad.length}개: ` +
        bad.slice(0, 3).map((b) => `${b.riseMs.toFixed(1)}ms@${b.tSec.toFixed(1)}s`).join(", ")
    );
  });

  // ------------------------------------------------------------------
  // ⑤ 레벨 백분위 다이내믹 레인지 ≤ 15 dB
  // PROXY 근거: Table 1 Tier 1 "Event Structure" — stable mean level,
  // minimise transient Lmax. 125 ms 프레임 RMS 분포의 p95−p5 ≤ 15 dB,
  // 그리고 순간 최대가 중앙값보다 12 dB 이상 솟지 않을 것(Lmax 프록시).
  // ------------------------------------------------------------------
  test(`[${id}] ⑤ 다이내믹 레인지 p95−p5 ≤ 15 dB`, () => {
    const d = levelPercentileRangeDb(rendered(id).x, SR);
    assert.ok(d.rangeDb <= 15, `p95−p5 = ${d.rangeDb.toFixed(2)} dB > 15`);
    assert.ok(d.maxOverMedianDb <= 12, `max−p50 = ${d.maxOverMedianDb.toFixed(2)} dB > 12 (순간 Lmax)`);
  });

  // ------------------------------------------------------------------
  // ⑥ >4 kHz 에너지 비율 상한 (샤프니스 프록시)
  // PROXY 근거: Table 1 Tier 2 "Sharpness" — limit high-frequency energy
  // (문헌의 1.5 acum은 잠정값). acum 계산(DIN 45692) 대신 4 kHz 초과
  // 파워 비율 ≤ 5% 를 요구한다. 부수 효과: 초음파 성분도 구조적으로 차단.
  // ------------------------------------------------------------------
  test(`[${id}] ⑥ 4 kHz 초과 에너지 ≤ 5% (샤프니스 프록시)`, () => {
    const ratio = highFrequencyRatio(rendered(id).x, SR, 4000);
    assert.ok(ratio <= 0.05, `>4 kHz 비율 ${(ratio * 100).toFixed(2)}% > 5%`);
  });

  // ------------------------------------------------------------------
  // ⑦ 루프 주기 자기상관 높음 (예측가능성 프록시)
  // PROXY 근거: Table 1 Tier 1 "Predictability" — regular patterns; avoid
  // abrupt structural change. 엔벨로프의 자기상관을 프리셋의 변조 주기
  // (1/amHz) lag 에서 계산, r ≥ 0.7 요구. 구조가 주기마다 반복되면 1에
  // 접근하고, 무작위 엔벨로프면 0 근처로 떨어진다.
  // ------------------------------------------------------------------
  test(`[${id}] ⑦ 변조 주기 자기상관 r ≥ 0.7 (예측가능성 프록시)`, () => {
    const period = 1 / PRESETS[id].amHz;
    const r = envelopePeriodicity(rendered(id).x, SR, period);
    assert.ok(r >= 0.7, `r(${period.toFixed(3)}s) = ${r.toFixed(3)} < 0.7`);
  });
}

// ======================================================================
// 음성 대조 (negative controls) — 규칙을 일부러 어긴 신호가 걸리는지 확인.
// 이게 없으면 위 7종이 "무엇이든 통과시키는" 장식일 수 있다.
// ======================================================================

function whiteNoise(n, seed) {
  const rng = mulberry32(seed);
  const x = new Float32Array(n);
  for (let i = 0; i < n; i++) x[i] = rng() * 2 - 1;
  return x;
}

test("[대조] 백색소음은 ① 기울기 테스트에 걸린다 (β ≈ 0)", () => {
  const x = whiteNoise(SR * 10, 1);
  const beta = spectralSlopeBeta(x, SR, 100, 8000);
  assert.ok(beta > -0.5, `백색소음 β=${beta.toFixed(3)} 가 −0.5 이하로 나옴 — 지표 고장`);
});

test("[대조] 70 Hz 트레몰로는 ② 러프니스 테스트에 걸린다", () => {
  // 러프니스 대역 한복판(70 Hz)에 깊은 진폭변조를 건 저역 소음
  const n = SR * 10;
  const rng = mulberry32(2);
  const x = new Float32Array(n);
  let lp = 0;
  const a = 1 - Math.exp((-2 * Math.PI * 800) / SR);
  for (let i = 0; i < n; i++) {
    lp += a * ((rng() * 2 - 1) - lp);
    const am = 1 + 0.8 * Math.sin((2 * Math.PI * 70 * i) / SR);
    x[i] = lp * am * 0.5;
  }
  const r = roughnessProxy(modulationSpectrum(x, SR));
  assert.ok(
    r.peakOverMedianDb > 10 || r.bandElevation > 1.0,
    `트레몰로가 안 걸림 (peak=+${r.peakOverMedianDb.toFixed(1)}dB, elev=${r.bandElevation.toFixed(2)}) — 지표 고장`
  );
});

test("[대조] 대역 제한 러프니스 잡음도 ② 테스트에 걸린다", () => {
  // 이산 피크 없이 30–150 Hz 대역 전체에 퍼진 무작위 변조 (잡음 AM)
  const n = SR * 10;
  const rng = mulberry32(7);
  const x = new Float32Array(n);
  let lp = 0, m1 = 0, m2 = 0;
  const a = 1 - Math.exp((-2 * Math.PI * 800) / SR);
  const aHi = 1 - Math.exp((-2 * Math.PI * 150) / SR);
  const aLo = 1 - Math.exp((-2 * Math.PI * 30) / SR);
  for (let i = 0; i < n; i++) {
    lp += a * ((rng() * 2 - 1) - lp);
    const w = rng() * 2 - 1;
    m1 += aHi * (w - m1);           // <150 Hz
    m2 += aLo * (m1 - m2);          // <30 Hz
    const band = m1 - m2;           // 대략 30–150 Hz 대역 변조원
    x[i] = lp * (1 + 6 * band) * 0.5;
  }
  const r = roughnessProxy(modulationSpectrum(x, SR));
  assert.ok(r.bandElevation > 1.0,
    `대역 잡음 변조가 안 걸림 (elev=${r.bandElevation.toFixed(2)}) — 지표 고장`);
});

test("[대조] 급격한 온셋(클릭形 버스트)은 ④⑤ 테스트에 걸린다", () => {
  // 1초마다 5 ms 어택으로 켜지는 버스트 — 알림음/노크 같은 구조
  const n = SR * 10;
  const rng = mulberry32(3);
  const x = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const tInCycle = (i % SR) / SR;
    let g = 0;
    if (tInCycle < 0.005) g = tInCycle / 0.005;        // 5 ms 어택
    else if (tInCycle < 0.25) g = 1 - (tInCycle - 0.005) / 0.245;
    x[i] = (rng() * 2 - 1) * g * 0.8;
  }
  const rises = onsetRises(x, SR);
  const fast = rises.filter((r) => r.riseMs < 50);
  assert.ok(fast.length > 0, "5 ms 어택이 온셋 지표에 안 걸림 — 지표 고장");
  const d = levelPercentileRangeDb(x, SR);
  assert.ok(d.rangeDb > 15, `버스트 신호 DR=${d.rangeDb.toFixed(1)} dB 가 15 이하 — 지표 고장`);
});

test("[대조] 고역 잡음은 ⑥ 샤프니스 프록시에 걸린다", () => {
  // 6 kHz 고역통과 방향의 잡음 (백색에서 저역 제거)
  const n = SR * 10;
  const rng = mulberry32(4);
  const x = new Float32Array(n);
  let lp = 0;
  const a = 1 - Math.exp((-2 * Math.PI * 6000) / SR);
  for (let i = 0; i < n; i++) {
    const w = rng() * 2 - 1;
    lp += a * (w - lp);
    x[i] = (w - lp) * 0.7; // 고역만 남김
  }
  const ratio = highFrequencyRatio(x, SR, 4000);
  assert.ok(ratio > 0.05, `고역 잡음 >4kHz 비율 ${(ratio * 100).toFixed(1)}% 가 5% 이하 — 지표 고장`);
});

test("[대조] 무작위 엔벨로프는 ⑦ 예측가능성 프록시에 걸린다", () => {
  // 주기 구조 없는 느린 무작위 게인 (랜덤워크) — 예측 불가능 텍스처
  const n = SR * 30;
  const rng = mulberry32(5);
  const x = new Float32Array(n);
  let lp = 0, gTarget = 0.5, g = 0.5;
  const a = 1 - Math.exp((-2 * Math.PI * 500) / SR);
  for (let i = 0; i < n; i++) {
    if (i % (SR >> 2) === 0) gTarget = 0.15 + 0.85 * rng(); // 250 ms마다 새 목표
    g += (gTarget - g) * 0.00005;
    lp += a * ((rng() * 2 - 1) - lp);
    x[i] = lp * g;
  }
  const r = envelopePeriodicity(x, SR, 1.0);
  assert.ok(r < 0.7, `무작위 엔벨로프 r=${r.toFixed(3)} 가 0.7 이상 — 지표 고장`);
});

test("[대조] 지배 변조가 3 Hz면 ③ 템포 테스트에 걸린다", () => {
  // 180 BPM 상당 — 빠른 변조
  const n = SR * 20;
  const rng = mulberry32(6);
  const x = new Float32Array(n);
  let lp = 0;
  const a = 1 - Math.exp((-2 * Math.PI * 800) / SR);
  for (let i = 0; i < n; i++) {
    lp += a * ((rng() * 2 - 1) - lp);
    const am = 0.65 + 0.35 * Math.sin((2 * Math.PI * 3 * i) / SR);
    x[i] = lp * am * 0.6;
  }
  const hz = dominantModulationHz(modulationSpectrum(x, SR));
  assert.ok(hz < 0.7 || hz > 1.33, `3 Hz 변조의 지배 주율이 ${hz.toFixed(2)} Hz로 나옴 — 지표 고장`);
});
