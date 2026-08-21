// tests/engine-basic.test.mjs — 엔진의 결정론·루프 연속성·기본 위생

import { test } from "node:test";
import assert from "node:assert/strict";
import { PRESETS, renderPreset, mulberry32, DEFAULT_LOOP_SECONDS, DEFAULT_SEED } from "../engine.js";

const SR = 44100;

test("mulberry32: 같은 시드 = 같은 수열, [0,1) 범위", () => {
  const a = mulberry32(7), b = mulberry32(7), c = mulberry32(8);
  const seqA = Array.from({ length: 100 }, a);
  const seqB = Array.from({ length: 100 }, b);
  const seqC = Array.from({ length: 100 }, c);
  assert.deepEqual(seqA, seqB);
  assert.notDeepEqual(seqA, seqC);
  for (const v of seqA) assert.ok(v >= 0 && v < 1);
});

for (const id of Object.keys(PRESETS)) {
  test(`[${id}] 결정론: 같은 (sr, 길이, seed) → 동일 버퍼`, () => {
    const x1 = renderPreset(id, SR, 30, 4242);
    const x2 = renderPreset(id, SR, 30, 4242);
    assert.ok(Buffer.from(x1.buffer).equals(Buffer.from(x2.buffer)));
  });

  test(`[${id}] 기본 위생: 길이·유한값·피크 정규화`, () => {
    const x = renderPreset(id, SR, 30, DEFAULT_SEED);
    assert.equal(x.length, SR * 30);
    let peak = 0;
    for (let i = 0; i < x.length; i++) {
      assert.ok(Number.isFinite(x[i]), `x[${i}] 비유한`);
      peak = Math.max(peak, Math.abs(x[i]));
    }
    assert.ok(peak <= 0.8501 && peak > 0.84, `피크 ${peak} — 0.85 정규화 기대`);
  });

  test(`[${id}] 루프 경계 무봉합: [끝]→[처음] 이행이 인접 샘플 크기`, () => {
    const x = renderPreset(id, SR, 30, DEFAULT_SEED);
    // 버퍼 내부의 최대 인접 샘플 차이보다 루프 경계 차이가 크면 안 된다
    let maxStep = 0;
    for (let i = 1; i < x.length; i++) maxStep = Math.max(maxStep, Math.abs(x[i] - x[i - 1]));
    const loopStep = Math.abs(x[0] - x[x.length - 1]);
    assert.ok(loopStep <= maxStep, `루프 이행 ${loopStep} > 내부 최대 이행 ${maxStep} (클릭 위험)`);
  });

  test(`[${id}] 48 kHz 렌더도 동작 (AudioContext 기본 샘플레이트 대응)`, () => {
    const x = renderPreset(id, 48000, 30, DEFAULT_SEED);
    assert.equal(x.length, 48000 * 30);
    assert.ok(Number.isFinite(x[0]) && Number.isFinite(x[x.length - 1]));
  });
}

test("잡음 프리셋은 시드에 따라 달라진다 (시드가 장식이 아님)", () => {
  for (const id of ["brown_waves", "pink_low"]) {
    const x1 = renderPreset(id, SR, 30, 1);
    const x2 = renderPreset(id, SR, 30, 2);
    assert.ok(!Buffer.from(x1.buffer).equals(Buffer.from(x2.buffer)), id);
  }
});

test("정수 사이클이 아닌 루프 길이는 거부한다", () => {
  // pink_low amHz=1.1 — 10초면 11사이클(정수)이라 통과, 7초면 7.7사이클이라 거부
  assert.throws(() => renderPreset("pink_low", SR, 7, 1), /정수 사이클/);
  assert.throws(() => renderPreset("drone_descend", SR, 10, 1), /배수/);
  assert.throws(() => renderPreset("없는프리셋", SR, 30, 1), /알 수 없는 프리셋/);
});

test("기본 루프 길이는 세 프리셋 모두에서 유효하다", () => {
  for (const id of Object.keys(PRESETS)) {
    const cycles = DEFAULT_LOOP_SECONDS * PRESETS[id].amHz;
    assert.ok(Math.abs(cycles - Math.round(cycles)) < 1e-9, `${id}: ${cycles}`);
    assert.ok(PRESETS[id].amHz >= 0.7 && PRESETS[id].amHz <= 1.33, `${id} amHz 범위`);
  }
});
