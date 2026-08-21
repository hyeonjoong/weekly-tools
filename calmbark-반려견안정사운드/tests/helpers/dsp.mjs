// tests/helpers/dsp.mjs — 테스트 전용 분석 도구 (배포 코드 아님)
//
// 논문 준수 테스트가 쓰는 신호 분석 함수 모음.
// 전부 node 내장만 사용, 결정론적, 오프라인.
//
// 주의: 여기 있는 측정치는 전부 논문 파라미터의 PROXY(대용 측정)다.
// 정식 심리음향 측정(asper 러프니스, acum 샤프니스 등)은 Zwicker 모델이
// 필요하며(논문 Table 2는 Essentia/mosqito 파이프라인을 권장), 이 테스트는
// "그 방향의 위반이 있으면 반드시 걸리는" 보수적 스칼라 지표만 계산한다.

// ---------------------------------------------------------------- FFT

/** 반복형 radix-2 FFT (in-place). re/im: Float64Array, 길이는 2의 거듭제곱. */
export function fft(re, im) {
  const n = re.length;
  if ((n & (n - 1)) !== 0) throw new Error("FFT 길이는 2의 거듭제곱이어야 함: " + n);
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wRe = Math.cos(ang), wIm = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let curRe = 1, curIm = 0;
      for (let k = 0; k < len / 2; k++) {
        const aRe = re[i + k], aIm = im[i + k];
        const bRe = re[i + k + len / 2] * curRe - im[i + k + len / 2] * curIm;
        const bIm = re[i + k + len / 2] * curIm + im[i + k + len / 2] * curRe;
        re[i + k] = aRe + bRe; im[i + k] = aIm + bIm;
        re[i + k + len / 2] = aRe - bRe; im[i + k + len / 2] = aIm - bIm;
        const nRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nRe;
      }
    }
  }
}

// ---------------------------------------------------------------- Welch PSD

/**
 * Welch 평균 주기도 (Hann 창, 50% 겹침).
 * @returns {{freqs: Float64Array, psd: Float64Array}} psd[k] = 파워 (임의 단위)
 */
export function welchPsd(x, sampleRate, nfft = 16384) {
  const hop = nfft >> 1;
  const win = new Float64Array(nfft);
  for (let i = 0; i < nfft; i++) win[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (nfft - 1));
  const nBins = nfft / 2;
  const acc = new Float64Array(nBins);
  let segs = 0;
  const re = new Float64Array(nfft);
  const im = new Float64Array(nfft);
  for (let start = 0; start + nfft <= x.length; start += hop) {
    for (let i = 0; i < nfft; i++) { re[i] = x[start + i] * win[i]; im[i] = 0; }
    fft(re, im);
    for (let k = 0; k < nBins; k++) acc[k] += re[k] * re[k] + im[k] * im[k];
    segs++;
  }
  if (segs === 0) throw new Error("신호가 너무 짧음 (nfft=" + nfft + ", len=" + x.length + ")");
  const freqs = new Float64Array(nBins);
  const psd = new Float64Array(nBins);
  for (let k = 0; k < nBins; k++) {
    freqs[k] = (k * sampleRate) / nfft;
    psd[k] = acc[k] / segs;
  }
  return { freqs, psd };
}

/** [fLo, fHi) 대역 파워 합. */
export function bandPower(freqs, psd, fLo, fHi) {
  let s = 0;
  for (let k = 0; k < freqs.length; k++) if (freqs[k] >= fLo && freqs[k] < fHi) s += psd[k];
  return s;
}

/** [fLo, fHi) 대역 빈 평균 파워. */
export function bandMean(freqs, psd, fLo, fHi) {
  let s = 0, c = 0;
  for (let k = 0; k < freqs.length; k++) if (freqs[k] >= fLo && freqs[k] < fHi) { s += psd[k]; c++; }
  if (c === 0) throw new Error(`대역 ${fLo}-${fHi} Hz 에 빈이 없음`);
  return s / c;
}

// ---------------------------------------------------------------- 통계 유틸

export function percentile(arr, p) {
  const a = Array.from(arr).sort((u, v) => u - v);
  if (a.length === 0) throw new Error("빈 배열의 백분위수");
  const idx = (p / 100) * (a.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return a[lo] + (a[hi] - a[lo]) * (idx - lo);
}

export function median(arr) { return percentile(arr, 50); }

function linreg(xs, ys) {
  const n = xs.length;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; sxx += xs[i] * xs[i]; sxy += xs[i] * ys[i]; }
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx);
  return { slope, intercept: (sy - slope * sx) / n };
}

// ---------------------------------------------------------------- 엔벨로프

/**
 * 진폭 엔벨로프: |x| → 1차 저역통과 2단 캐스케이드(lpHz) → 데시메이션.
 * 반환 샘플레이트 = sampleRate / decim (기본 44100/44 ≈ 1002.3 Hz).
 * 30–150 Hz 변조를 살려야 하므로 lpHz는 그보다 훨씬 위(300 Hz)로 둔다.
 */
export function amplitudeEnvelope(x, sampleRate, lpHz = 300, decim = 44) {
  const a = 1 - Math.exp((-2 * Math.PI * lpHz) / sampleRate);
  let y1 = 0, y2 = 0;
  const outLen = Math.floor(x.length / decim);
  const out = new Float64Array(outLen);
  let oi = 0;
  for (let i = 0; i < x.length; i++) {
    const r = Math.abs(x[i]);
    y1 += a * (r - y1);
    y2 += a * (y1 - y2);
    if (i % decim === 0 && oi < outLen) out[oi++] = y2;
  }
  return { env: out, rate: sampleRate / decim };
}

/** 이동평균 평활 (창 ms). */
export function movingAverage(env, rate, ms) {
  const w = Math.max(1, Math.round((ms / 1000) * rate));
  const out = new Float64Array(env.length);
  let acc = 0;
  for (let i = 0; i < env.length; i++) {
    acc += env[i];
    if (i >= w) acc -= env[i - w];
    out[i] = acc / Math.min(i + 1, w);
  }
  return out;
}

// ---------------------------------------------------------------- ① 스펙트럼 기울기

/**
 * log-log PSD 회귀 기울기 β (P ∝ f^β 의 β).
 * 논문 Table 2 item 7: 회귀 주파수 범위를 함께 보고 — 여기서는 100 Hz–8 kHz.
 */
export function spectralSlopeBeta(x, sampleRate, fLo = 100, fHi = 8000) {
  const { freqs, psd } = welchPsd(x, sampleRate, 16384);
  const xs = [], ys = [];
  for (let k = 0; k < freqs.length; k++) {
    if (freqs[k] >= fLo && freqs[k] <= fHi && psd[k] > 0) {
      xs.push(Math.log10(freqs[k]));
      ys.push(Math.log10(psd[k]));
    }
  }
  return linreg(xs, ys).slope;
}

// ---------------------------------------------------------------- 변조 스펙트럼 (②③⑦ 공용)

export function modulationSpectrum(x, sampleRate) {
  const { env, rate } = amplitudeEnvelope(x, sampleRate, 300, 44);
  let mean = 0;
  for (const v of env) mean += v;
  mean /= env.length;
  const centered = new Float64Array(env.length);
  for (let i = 0; i < env.length; i++) centered[i] = env[i] - mean;
  const { freqs, psd } = welchPsd(centered, rate, 8192);
  return { freqs, psd, env, envRate: rate, envMean: mean };
}

/**
 * ② 러프니스 프록시: 30–150 Hz 변조 대역 지표 (Arnal et al. 2015 대역).
 *  - peakOverMedianDb: 30–150 Hz 안 최대 빈이 20–190 Hz 중앙값보다 몇 dB
 *    위인가 — 이산 트레몰로 성분(뾰족한 피크)을 잡는다.
 *  - bandElevation: 평균 PSD(30–150) / 평균 PSD(5–25) — 인접한 더 느린
 *    대역보다 러프니스 대역이 솟아 있으면(>1) 대역 제한 변조 에너지가
 *    주입된 것. 정상적인 저역 잡음 텍스처의 엔벨로프 스펙트럼은 변조
 *    주율에 따라 단조 감소하므로 1을 넘지 않는다.
 *  - ratio: 대역파워(30–150)/대역파워(0.4–150) — 참고 보고용. 광대역
 *    가우스 잡음은 엔벨로프 요동이 본질적으로 있어(σ/μ ≈ 0.52) 이 값이
 *    0이 되지 않는다. 판정에는 위 두 지표를 쓴다.
 */
export function roughnessProxy(ms) {
  const { freqs, psd } = ms;
  const inBand = [], wide = [];
  for (let k = 0; k < freqs.length; k++) {
    if (freqs[k] >= 30 && freqs[k] < 150) inBand.push(psd[k]);
    if (freqs[k] >= 20 && freqs[k] < 190) wide.push(psd[k]);
  }
  const peak = Math.max(...inBand);
  const med = median(wide);
  return {
    peakOverMedianDb: 10 * Math.log10(peak / Math.max(med, 1e-30)),
    bandElevation: bandMean(freqs, psd, 30, 150) / bandMean(freqs, psd, 5, 25),
    ratio: bandPower(freqs, psd, 30, 150) / bandPower(freqs, psd, 0.4, 150),
  };
}

/** ③ 지배적 느린 변조 주율 (0.25–16 Hz 탐색). */
export function dominantModulationHz(ms) {
  const { freqs, psd } = ms;
  let best = -1, bestP = -Infinity;
  for (let k = 0; k < freqs.length; k++) {
    if (freqs[k] >= 0.25 && freqs[k] <= 16 && psd[k] > bestP) { bestP = psd[k]; best = freqs[k]; }
  }
  return best;
}

// ---------------------------------------------------------------- ④ 온셋 상승 시간

/**
 * 엔벨로프에서 골→마루 상승(≥ riseDbMin dB)을 전부 찾아
 * 10–90% 상승 시간을 잰다. 반환: 상승 목록 [{riseMs, riseDb, tSec}].
 * 상승이 하나도 없으면(완전 정상 텍스처) 빈 배열 — 위반 아님.
 *
 * 평활 30 ms 근거: 이벤트 수준 어택은 수십~수백 ms 스케일이다. 30 ms
 * 이동평균은 잡음 캐리어의 미시 요동(수 ms)을 지우되, 50 ms 판정 경계는
 * 보존한다 — 5 ms 어택도 평활 후 ≈24 ms(0.8×창)로 측정돼 50 ms 미만으로
 * 걸리고(음성 대조로 고정), 60 ms 어택은 ≈60 ms로 측정돼 통과한다.
 *
 * riseDbMin 8 근거: 가우스 잡음 텍스처는 평활 후에도 σ ≈ 1 dB 정도의
 * 제거 불가능한 레벨 요동을 가진다(엔벨로프 통계 σ/μ≈0.52 의 잔여분).
 * 관측된 3σ급 요동이 골→마루 6–7.5 dB 까지 나오므로, "온셋 이벤트"는
 * ≥8 dB(≈4σ) 돌출로 정의한다 — 타악/알림/노크류 어택은 통상 ≥10 dB 라
 * 확실히 걸리고, 8 dB 미만의 빠른 요동은 텍스처 자체 변동에 마스킹되는
 * 것으로 간주한다(알려진 한계로 HARDENING.md 에 기록).
 *
 * skipStartSec 0.5 근거: 엔벨로프 추종기 상태가 0에서 출발하므로 첫
 * ~0.3 s 는 신호와 무관한 가짜 상승(웜업)이 잡힌다. 실제 재생은 앱이
 * ≥1 s 페이드인을 걸므로 버퍼 첫머리는 온셋 판정 대상이 아니다.
 */
export function onsetRises(x, sampleRate, { riseDbMin = 8, smoothMs = 30, skipStartSec = 0.5 } = {}) {
  const { env, rate } = amplitudeEnvelope(x, sampleRate, 300, 44);
  const smFull = movingAverage(env, rate, smoothMs);
  const sm = smFull.subarray(Math.min(Math.round(skipStartSec * rate), smFull.length - 2));
  const eps = 1e-9;
  const hystDb = 2;
  const rises = [];
  let mode = "seekMin";
  let minV = sm[0], minI = 0, maxV = sm[0], maxI = 0;
  const db = (v) => 20 * Math.log10(Math.max(v, eps));
  for (let i = 1; i < sm.length; i++) {
    const v = sm[i];
    if (mode === "seekMin") {
      if (v < minV) { minV = v; minI = i; }
      if (db(v) > db(minV) + hystDb) { mode = "seekMax"; maxV = v; maxI = i; }
    } else {
      if (v > maxV) { maxV = v; maxI = i; }
      if (db(v) < db(maxV) - hystDb) {
        const riseDb = db(maxV) - db(minV);
        if (riseDb >= riseDbMin) {
          const a10 = minV + 0.1 * (maxV - minV);
          const a90 = minV + 0.9 * (maxV - minV);
          let t10 = -1, t90 = -1;
          for (let j = minI; j <= maxI; j++) {
            if (t10 < 0 && sm[j] >= a10) t10 = j;
            if (t90 < 0 && sm[j] >= a90) { t90 = j; break; }
          }
          if (t10 >= 0 && t90 >= 0) {
            rises.push({ riseMs: ((t90 - t10) / rate) * 1000, riseDb, tSec: minI / rate });
          }
        }
        mode = "seekMin"; minV = v; minI = i;
      }
    }
  }
  return rises;
}

// ---------------------------------------------------------------- ⑤ 다이내믹 레인지

/**
 * 프레임 RMS 레벨(dB)의 백분위 범위 p95 − p5.
 * 창 125 ms, 겹침 50%. (LAeq 이벤트 변동의 프록시 — 논문 Table 2 item 1)
 */
export function levelPercentileRangeDb(x, sampleRate, winMs = 125) {
  const w = Math.round((winMs / 1000) * sampleRate);
  const hop = w >> 1;
  const levels = [];
  for (let start = 0; start + w <= x.length; start += hop) {
    let s = 0;
    for (let i = 0; i < w; i++) s += x[start + i] * x[start + i];
    levels.push(10 * Math.log10(Math.max(s / w, 1e-20)));
  }
  return {
    rangeDb: percentile(levels, 95) - percentile(levels, 5),
    maxOverMedianDb: Math.max(...levels) - median(levels),
  };
}

// ---------------------------------------------------------------- ⑥ 고주파 비율

/** >4 kHz 파워 / (≥20 Hz 전체 파워). 샤프니스(acum) 방향의 프록시. */
export function highFrequencyRatio(x, sampleRate, splitHz = 4000) {
  const { freqs, psd } = welchPsd(x, sampleRate, 16384);
  const hi = bandPower(freqs, psd, splitHz, sampleRate / 2);
  const total = bandPower(freqs, psd, 20, sampleRate / 2);
  return hi / total;
}

// ---------------------------------------------------------------- ⑦ 주기 자기상관

/**
 * 엔벨로프(50 ms 평활, 평균 제거)의 정규화 자기상관을 lag = periodSec 에서 계산.
 * 값 1에 가까울수록 루프 주기 구조가 규칙적 (predictability 프록시).
 */
export function envelopePeriodicity(x, sampleRate, periodSec) {
  const { env, rate } = amplitudeEnvelope(x, sampleRate, 300, 44);
  const sm = movingAverage(env, rate, 50);
  let mean = 0;
  for (const v of sm) mean += v;
  mean /= sm.length;
  const lag = Math.round(periodSec * rate);
  let num = 0, den = 0;
  for (let i = 0; i < sm.length; i++) den += (sm[i] - mean) * (sm[i] - mean);
  for (let i = 0; i + lag < sm.length; i++) num += (sm[i] - mean) * (sm[i + lag] - mean);
  const overlap = sm.length - lag;
  return (num / overlap) / (den / sm.length);
}
