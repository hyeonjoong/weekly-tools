// tests/paper-compliance.test.mjs
//
// ★ 이 파일이 이 툴의 존재 이유다 ★
//
// NBR 1st revision 원고 "Acoustic Parameters for Autonomic Arousal Modulation"
// (Kim, Ha, Park, Thayer, Bosi, Eerola — in revision) Table 1 의 Tier 1/2 설계
// 규칙을, 엔진이 합성한 각 프리셋의 PCM 버퍼에 대한 회귀 테스트로 고정한다.
//
// 구성 (라운드 1 C2/C3 반영):
//   · 전체 7종 지표 — 시드 3개(고정 시드 + 패널 재현 시드 8·15) × 프리셋 3종
//   · ②(러프니스 대역)·④(온셋 상승) — 검증 시드 16종 전부 스위프,
//     여유 임계(융기 ≤0.90 / 상승 ≥60 ms)로 강제. 앱은 이 목록에서만
//     자동 시드를 뽑으므로 "검증 시드에서 준수"가 곧 배포 보장이다.
//   · ⑧ 크레스트/클릭 가드 — 엔벨로프 평활 지표가 놓치는 1샘플급 과도 검출
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
import { PRESETS, VETTED_SEEDS, renderPreset, mulberry32 } from "../engine.js";
import {
  spectralSlopeBeta,
  modulationSpectrum,
  roughnessProxy,
  dominantModulationHz,
  onsetRises,
  levelPercentileRangeDb,
  highFrequencyRatio,
  envelopePeriodicity,
  crestFactorDb,
  diffCrestDb,
} from "./helpers/dsp.mjs";

const SR = 44100;        // 실제 앱과 같은 샘플레이트
const SECONDS = 30;      // 프리셋 루프 길이 (엔진 기본과 동일)
// 전체 7종 지표를 도는 시드: 기존 고정 시드 + 라운드 1 패널이 결함을 재현했던 시드 8·15
const FULL_SEEDS = [20260821, 8, 15];

// ---- (프리셋, 시드)별 버퍼/변조스펙트럼 캐시 ----
const cache = new Map();
function rendered(id, seed) {
  const key = id + "|" + seed;
  if (!cache.has(key)) cache.set(key, { x: renderPreset(id, SR, SECONDS, seed), ms: null });
  return cache.get(key);
}
function modspec(id, seed) {
  const c = rendered(id, seed);
  if (!c.ms) c.ms = modulationSpectrum(c.x, SR);
  return c.ms;
}

const presetIds = Object.keys(PRESETS);
assert.equal(presetIds.length, 3, "프리셋은 정확히 3개 (기획 문서 §범위 3)");
assert.equal(VETTED_SEEDS.length, 16, "검증 시드는 정확히 16개 (라운드 1 C2)");

for (const id of presetIds) {
  for (const seed of FULL_SEEDS) {
    const tag = `[${id}·seed${seed}]`;
    // ------------------------------------------------------------------
    // ① 스펙트럼 기울기 β < 0
    // PROXY 근거: Table 1 Tier 2 "Spectral Slope" — Negative slope;
    // pink-to-brown range. Table 2 item 7은 log-log 회귀 주파수 범위 보고를
    // 요구하므로 여기 명시: 100 Hz – 8 kHz, Welch/Hann 16384.
    // 논문 요구는 β<0 이지만 측정 잡음 여유를 두고 β ≤ −0.5 로 고정한다.
    // 실측(44.1 kHz)은 −2.9~−5.5 로 핑크~브라운 서술 범위(−1~−2)보다 가파르다 —
    // 저역통과 캐스케이드가 회귀 상단(>1.2 kHz)을 추가로 깎기 때문이며, 상한
    // (덜 가파른 쪽) 위반만이 각성 방향 위험이라 검정은 단측이다 (README 대응표).
    // ------------------------------------------------------------------
    test(`${tag} ① 스펙트럼 기울기 β<0 (핑크~브라운)`, () => {
      const beta = spectralSlopeBeta(rendered(id, seed).x, SR, 100, 8000);
      assert.ok(beta <= -0.5, `β=${beta.toFixed(3)} — −0.5 이하가 아님`);
    });

    // ------------------------------------------------------------------
    // ② 30–150 Hz 변조(러프니스) 대역에 유의 에너지 없음
    // PROXY 근거: Table 1 Tier 1 "Roughness" — 편도체가 30–150 Hz 진폭변조
    // 주율에 선택적으로 반응한다는 Arnal et al. (2015)의 대역. asper 단위
    // 러프니스(Zwicker/Daniel–Weber) 대신 진폭 엔벨로프 변조 스펙트럼에서
    //  (a) 대역 안에 20–190 Hz 중앙값보다 10 dB 이상 솟은 이산 피크(트레몰로) 없음
    //  (b) 대역 평균 PSD가 인접한 더 느린 대역(5–25 Hz) 평균을 넘지 않음
    // 을 요구한다. 검증 시드 전수 스위프는 아래 별도 테스트가 더 엄한 여유
    // 임계(≤0.90)로 수행한다.
    // ------------------------------------------------------------------
    test(`${tag} ② 러프니스 대역(30–150 Hz) 유의 변조 성분 없음 — Arnal 2015`, () => {
      const r = roughnessProxy(modspec(id, seed));
      assert.ok(r.peakOverMedianDb <= 10,
        `30–150 Hz 안 이산 피크 +${r.peakOverMedianDb.toFixed(1)} dB > 10 dB (트레몰로 의심)`);
      assert.ok(r.bandElevation <= 1.0,
        `30–150 Hz 평균 PSD가 5–25 Hz 평균의 ${r.bandElevation.toFixed(2)}배 — 대역 융기`);
    });

    // ------------------------------------------------------------------
    // ③ 지배적 느린 변조 0.7–1.33 Hz
    // PROXY 근거: Table 1 Tier 2 "Tempo/Rhythm" — 문헌 예시값 60–80 BPM
    // (프리셋 실제값 1.0/1.1/1.0 Hz = 60/66/60 BPM), 구현 허용 대역은
    // 42–80 BPM(0.7–1.33 Hz). 변조 스펙트럼(0.25–16 Hz) 최대 피크 기준.
    // ------------------------------------------------------------------
    test(`${tag} ③ 지배적 변조 주율 0.7–1.33 Hz (42–80 BPM)`, () => {
      const hz = dominantModulationHz(modspec(id, seed));
      assert.ok(hz >= 0.7 && hz <= 1.33, `지배 변조 ${hz.toFixed(3)} Hz — 0.7–1.33 밖`);
    });

    // ------------------------------------------------------------------
    // ④ 모든 온셋 10–90% 상승 ≥ 50 ms
    // PROXY 근거: Table 1 Tier 1 "Onset Dynamics" — gradual attack, 문헌
    // 기준값은 수십 ms 이상. 엔벨로프(30 ms 평활, 웜업 0.5 s 제외)에서
    // ≥8 dB 골→마루 상승("온셋 이벤트" — 텍스처 자체 요동 ≈4σ 초과)을 전부
    // 찾아 10–90% 상승 시간이 50 ms 미만인 것이 하나라도 있으면 실패.
    // 검증 시드 전수 스위프는 아래 별도 테스트가 ≥60 ms 여유로 수행한다.
    // ------------------------------------------------------------------
    test(`${tag} ④ 온셋 10–90% 상승 ≥ 50 ms`, () => {
      const bad = onsetRises(rendered(id, seed).x, SR).filter((r) => r.riseMs < 50);
      assert.equal(bad.length, 0,
        `50 ms 미만 온셋 ${bad.length}개: ` +
          bad.slice(0, 3).map((b) => `${b.riseMs.toFixed(1)}ms@${b.tSec.toFixed(1)}s`).join(", "));
    });

    // ------------------------------------------------------------------
    // ⑤ 레벨 백분위 다이내믹 레인지 ≤ 15 dB
    // PROXY 근거: Table 1 Tier 1 "Event Structure" — stable mean level,
    // minimise transient Lmax. 125 ms 프레임 RMS 분포의 p95−p5 ≤ 15 dB,
    // 그리고 순간 최대가 중앙값보다 12 dB 이상 솟지 않을 것(Lmax 프록시).
    // ------------------------------------------------------------------
    test(`${tag} ⑤ 다이내믹 레인지 p95−p5 ≤ 15 dB`, () => {
      const d = levelPercentileRangeDb(rendered(id, seed).x, SR);
      assert.ok(d.rangeDb <= 15, `p95−p5 = ${d.rangeDb.toFixed(2)} dB > 15`);
      assert.ok(d.maxOverMedianDb <= 12, `max−p50 = ${d.maxOverMedianDb.toFixed(2)} dB > 12 (순간 Lmax)`);
    });

    // ------------------------------------------------------------------
    // ⑥ >4 kHz 에너지 비율 상한 (샤프니스 프록시)
    // PROXY 근거: Table 1 Tier 2 "Sharpness" — limit high-frequency energy
    // (문헌의 1.5 acum은 잠정값). acum 계산(DIN 45692) 대신 4 kHz 초과
    // 파워 비율 ≤ 5% 를 요구한다. 부수 효과: 초음파 성분도 구조적으로 차단.
    // ------------------------------------------------------------------
    test(`${tag} ⑥ 4 kHz 초과 에너지 ≤ 5% (샤프니스 프록시)`, () => {
      const ratio = highFrequencyRatio(rendered(id, seed).x, SR, 4000);
      assert.ok(ratio <= 0.05, `>4 kHz 비율 ${(ratio * 100).toFixed(2)}% > 5%`);
    });

    // ------------------------------------------------------------------
    // ⑦ 루프 주기 자기상관 높음 (예측가능성 프록시)
    // PROXY 근거: Table 1 Tier 1 "Predictability" — regular patterns; avoid
    // abrupt structural change. 엔벨로프의 자기상관을 프리셋의 변조 주기
    // (1/amHz) lag 에서 계산, r ≥ 0.7 요구.
    // ------------------------------------------------------------------
    test(`${tag} ⑦ 변조 주기 자기상관 r ≥ 0.7 (예측가능성 프록시)`, () => {
      const period = 1 / PRESETS[id].amHz;
      const r = envelopePeriodicity(rendered(id, seed).x, SR, period);
      assert.ok(r >= 0.7, `r(${period.toFixed(3)}s) = ${r.toFixed(3)} < 0.7`);
    });

    // ------------------------------------------------------------------
    // ⑧ 크레스트/클릭 가드 (라운드 1 C3)
    // PROXY 근거: Table 1 Tier 1 "Event Structure"(minimise transient Lmax)
    // / "Onset Dynamics" 의 미시 스케일 — 엔벨로프 평활 기반 ②③④⑦ 은
    // 1샘플급 클릭(놀람 과도)을 희석해 통과시킬 수 있음이 패널에서 증명됐다.
    //  (a) 1 ms 창 피크 vs 국소 100 ms RMS ≤ 14 dB
    //      (클린 엔진 26시드 스위프 최악 10.5 dB + 3.5 dB 여유)
    //  (b) 인접 샘플 차분 크레스트 ≤ 22 dB (스위프 최악 18.3 dB + 여유) —
    //      AM 마루에 심어 국소 RMS 를 탄 클릭까지 잡는 2차 방어선
    // ------------------------------------------------------------------
    test(`${tag} ⑧ 크레스트/클릭 가드 (놀람 과도 없음)`, () => {
      const x = rendered(id, seed).x;
      const crest = crestFactorDb(x, SR);
      const diff = diffCrestDb(x);
      assert.ok(crest <= 14, `1ms/100ms 크레스트 ${crest.toFixed(1)} dB > 14`);
      assert.ok(diff <= 22, `차분 크레스트 ${diff.toFixed(1)} dB > 22`);
    });
  }
}

// ======================================================================
// 검증 시드 전수 스위프 (라운드 1 C2) — ②·④ 를 여유 임계로 강제.
// 앱의 자동 세션 시드는 VETTED_SEEDS 에서만 뽑으므로, 이 스위프가 곧
// "배포되는 소리"의 준수 보장이다. 직접 입력 시드는 로그에 `시드(미검증)`.
// ======================================================================

for (const id of presetIds) {
  test(`[스위프·${id}] 검증 시드 16종: ② 융기 ≤ 0.90 · ④ 상승 ≥ 60 ms`, () => {
    for (const seed of VETTED_SEEDS) {
      const x = rendered(id, seed).x;
      const r = roughnessProxy(modspec(id, seed));
      assert.ok(r.bandElevation <= 0.90,
        `seed ${seed}: 융기 ${r.bandElevation.toFixed(3)} > 0.90`);
      assert.ok(r.peakOverMedianDb <= 10,
        `seed ${seed}: 이산 피크 +${r.peakOverMedianDb.toFixed(1)} dB > 10`);
      const slow = onsetRises(x, SR).filter((q) => q.riseMs < 60);
      assert.equal(slow.length, 0,
        `seed ${seed}: 60 ms 미만 상승 ${slow.length}개 (최소 ${slow[0]?.riseMs.toFixed(1)} ms)`);
    }
  });
}

// ======================================================================
// 음성 대조 (negative controls) — 규칙을 일부러 어긴 신호가 걸리는지 확인.
// 이게 없으면 위 지표들이 "무엇이든 통과시키는" 장식일 수 있다.
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
  assert.ok(r.peakOverMedianDb > 10 || r.bandElevation > 1.0,
    `트레몰로가 안 걸림 (peak=+${r.peakOverMedianDb.toFixed(1)}dB, elev=${r.bandElevation.toFixed(2)}) — 지표 고장`);
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
    m1 += aHi * (w - m1);
    m2 += aLo * (m1 - m2);
    x[i] = lp * (1 + 6 * (m1 - m2)) * 0.5;
  }
  const r = roughnessProxy(modulationSpectrum(x, SR));
  assert.ok(r.bandElevation > 1.0,
    `대역 잡음 변조가 안 걸림 (elev=${r.bandElevation.toFixed(2)}) — 지표 고장`);
});

test("[대조] 급격한 온셋(클릭形 버스트)은 ④⑤ 테스트에 걸린다", () => {
  const n = SR * 10;
  const rng = mulberry32(3);
  const x = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const tInCycle = (i % SR) / SR;
    let g = 0;
    if (tInCycle < 0.005) g = tInCycle / 0.005;
    else if (tInCycle < 0.25) g = 1 - (tInCycle - 0.005) / 0.245;
    x[i] = (rng() * 2 - 1) * g * 0.8;
  }
  const fast = onsetRises(x, SR).filter((r) => r.riseMs < 50);
  assert.ok(fast.length > 0, "5 ms 어택이 온셋 지표에 안 걸림 — 지표 고장");
  const d = levelPercentileRangeDb(x, SR);
  assert.ok(d.rangeDb > 15, `버스트 신호 DR=${d.rangeDb.toFixed(1)} dB 가 15 이하 — 지표 고장`);
});

test("[대조] 고역 잡음은 ⑥ 샤프니스 프록시에 걸린다", () => {
  const n = SR * 10;
  const rng = mulberry32(4);
  const x = new Float32Array(n);
  let lp = 0;
  const a = 1 - Math.exp((-2 * Math.PI * 6000) / SR);
  for (let i = 0; i < n; i++) {
    const w = rng() * 2 - 1;
    lp += a * (w - lp);
    x[i] = (w - lp) * 0.7;
  }
  const ratio = highFrequencyRatio(x, SR, 4000);
  assert.ok(ratio > 0.05, `고역 잡음 >4kHz 비율 ${(ratio * 100).toFixed(1)}% 가 5% 이하 — 지표 고장`);
});

test("[대조] 무작위 엔벨로프는 ⑦ 예측가능성 프록시에 걸린다", () => {
  const n = SR * 30;
  const rng = mulberry32(5);
  const x = new Float32Array(n);
  let lp = 0, gTarget = 0.5, g = 0.5;
  const a = 1 - Math.exp((-2 * Math.PI * 500) / SR);
  for (let i = 0; i < n; i++) {
    if (i % (SR >> 2) === 0) gTarget = 0.15 + 0.85 * rng();
    g += (gTarget - g) * 0.00005;
    lp += a * ((rng() * 2 - 1) - lp);
    x[i] = lp * g;
  }
  const r = envelopePeriodicity(x, SR, 1.0);
  assert.ok(r < 0.7, `무작위 엔벨로프 r=${r.toFixed(3)} 가 0.7 이상 — 지표 고장`);
});

test("[대조] 지배 변조가 3 Hz면 ③ 템포 테스트에 걸린다", () => {
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

test("[대조] 1샘플 클릭 주입은 ⑧ 크레스트 가드에 걸린다 (라운드 1 C3)", () => {
  // 패널이 증명한 맹점의 재현: 깨끗한 프리셋 버퍼에 매초 풀스케일 1샘플 클릭.
  // ②③④⑦ 은 엔벨로프 평활로 이를 희석해 통과시켰다 — ⑧ 이 잡아야 한다.
  // 측정 사실: 진폭 크레스트(1ms/100ms)만으로는 1샘플 클릭을 못 가른다
  // (클린 엔진 자체 가우스 피크 ~8–10.5 dB vs 클릭 ~10–12 dB — 분리 없음).
  // 실제로 클릭을 잡는 것은 차분 크레스트(클린 ≤18.3 dB vs 클릭 ~38 dB)다.
  // ⑧ 은 둘 중 하나만 넘어도 실패하므로, 대조는 "가드 전체가 걸리는가"를 확인한다.
  const x = Float32Array.from(rendered("brown_waves", 20260821).x);
  for (let s = 0; s < 30; s++) x[s * SR + (SR >> 1)] = 1.0;
  const crest = crestFactorDb(x, SR);
  const diff = diffCrestDb(x);
  assert.ok(crest > 14 || diff > 22,
    `클릭 주입이 ⑧ 에 안 걸림 (crest ${crest.toFixed(1)} dB, diff ${diff.toFixed(1)} dB) — 지표 고장`);
  assert.ok(diff > 22, `차분 크레스트 ${diff.toFixed(1)} dB 가 22 이하 — 클릭 검출력 상실`);
});
