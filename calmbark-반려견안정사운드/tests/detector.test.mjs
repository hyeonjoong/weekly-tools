// tests/detector.test.mjs — 짖음 감지 상태기계를 손계산 기대값과 대조
//
// 프레임 간격 25 ms (앱의 rAF 주기와 유사). 전부 합성 프레임 — 마이크 불필요.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createDetector, DETECTOR_DEFAULTS } from "../detector.js";

const STEP = 25;

/** tMs 구간 [from, to) 를 level(dB) 프레임으로 만들어 먹인다. */
function feed(det, fromMs, toMs, bandDb, playbackActive = false) {
  const out = [];
  for (let t = fromMs; t < toMs; t += STEP) {
    out.push(...det.processFrame({ tMs: t, bandDb: typeof bandDb === "function" ? bandDb(t) : bandDb, playbackActive }));
  }
  return out;
}

/** 3초 보정을 마친 감지기 (바닥 40 dB). */
function calibrated(config = {}) {
  const det = createDetector(config);
  const ev = feed(det, 0, 3025, 40);
  assert.equal(ev.length, 1, "보정 이벤트 1개");
  assert.equal(ev[0].type, "calibrated");
  assert.equal(ev[0].floorDb, 40);
  return det;
}

test("보정: 3초간 감지 없이 바닥 수집, calibrated 이벤트 1회", () => {
  const det = createDetector();
  // 보정 중에는 큰 소리도 이벤트를 내지 않는다 (바닥 표본으로만 들어감)
  const ev1 = feed(det, 0, 1000, 40);
  const ev2 = feed(det, 1000, 1100, 70); // 보정 중 소음 스파이크
  const ev3 = feed(det, 1100, 3025, 40);
  assert.equal(ev1.length + ev2.length, 0);
  assert.equal(ev3.length, 1);
  assert.equal(ev3[0].type, "calibrated");
  // 3초 창의 중앙값 — 스파이크 4프레임은 중앙값을 못 움직인다
  assert.equal(ev3[0].floorDb, 40);
  assert.equal(det.getState(), "IDLE");
});

test("트리거→최소지속→종료: 175 ms 버스트가 bark 1건", () => {
  const det = calibrated();
  feed(det, 3025, 4000, 40);
  // 60 dB > 문턱 52 (바닥 40 + 감도 12)
  const during = feed(det, 4000, 4175, 60);
  assert.equal(during.length, 0, "버스트 진행 중에는 이벤트 없음");
  const after = feed(det, 4175, 4200, 40);
  assert.equal(after.length, 1);
  const bark = after[0];
  assert.equal(bark.type, "bark");
  assert.equal(bark.tMs, 4000);        // 버스트 시작
  assert.equal(bark.endMs, 4175);      // 문턱 아래로 떨어진 프레임
  assert.equal(bark.peakDb, 60);
  assert.equal(bark.duringPlayback, false);
});

test("최소지속 미달(25 ms 스파이크)은 무시", () => {
  const det = calibrated();
  feed(det, 3025, 5000, 40);
  const ev = [
    ...feed(det, 5000, 5025, 70),  // 프레임 1개만 문턱 위
    ...feed(det, 5025, 6000, 40),
  ];
  assert.equal(ev.length, 0, "minBurstMs=60 미달 — bark 없음");
});

test("불응기: 종료 직후 재상승은 무시, 불응기 뒤에는 새 bark", () => {
  const det = calibrated();
  feed(det, 3025, 4000, 40);
  feed(det, 4000, 4175, 60);
  const e1 = feed(det, 4175, 4200, 40);        // bark #1, 불응기 4175+250=4425까지
  assert.equal(e1.length, 1);
  const e2 = feed(det, 4200, 4300, 60);        // 불응기 안 — 무시돼야 함
  const e3 = feed(det, 4300, 4425, 40);
  assert.equal(e2.length + e3.length, 0, "불응기 내 재상승이 bark 가 되면 안 됨");
  const e4 = feed(det, 4425, 4600, 60);        // 불응기 종료 후
  const e5 = feed(det, 4600, 4700, 40);
  assert.equal(e5.length, 1, "불응기 이후는 새 bark");
  assert.equal(e5[0].tMs, 4450, "불응기 해제 프레임(4425) 다음 프레임에서 시작");
});

test("최대지속: 끝없는 소음은 maxBurstMs 에서 강제 분절", () => {
  const det = calibrated();
  feed(det, 3025, 4000, 40);
  const ev = feed(det, 4000, 10000, 60); // 6초 연속 문턱 위
  assert.equal(ev.length, 1, "5초에서 1회 강제 종료");
  assert.equal(ev[0].tMs, 4000);
  assert.equal(ev[0].endMs - ev[0].tMs, DETECTOR_DEFAULTS.maxBurstMs);
});

test("소음 바닥 적응: 배경이 46 dB로 올라가면 55 dB는 더 이상 트리거 아님", () => {
  const det = calibrated();
  // 46 dB 는 문턱(52) 아래 → 바닥 표본으로 들어가 중앙값이 46으로 이동
  feed(det, 3025, 10000, 46); // 279 프레임 > floorWindowFrames 200
  assert.equal(det.getNoiseFloor(), 46);
  assert.equal(det.getThreshold(), 58);
  const ev = [
    ...feed(det, 10000, 10500, 55),  // 옛 문턱(52)보다 크지만 새 문턱(58) 아래
    ...feed(det, 10500, 11000, 46),
  ];
  assert.equal(ev.length, 0, "적응된 문턱 아래 소리는 무시");
});

test("오탐 0: 무음/일정 소음 50초 동안 bark 없음", () => {
  const det = calibrated();
  // 결정론적 ±1 dB 잔물결이 있는 배경 소음
  const ev = feed(det, 3025, 53025, (t) => 40 + Math.sin(t / 400));
  assert.equal(ev.filter((e) => e.type === "bark").length, 0);
});

test("재생 중: 문턱 +6 dB 상향 — 55 dB 는 무시, 60 dB 는 duringPlayback bark", () => {
  const det = calibrated();
  feed(det, 3025, 4000, 40);
  assert.equal(det.getThreshold(false), 52);
  assert.equal(det.getThreshold(true), 58);
  const e1 = [
    ...feed(det, 4000, 4500, 55, true),   // 52 < 55 < 58 — 재생 중이라 무시
    ...feed(det, 4500, 5000, 40, true),
  ];
  assert.equal(e1.length, 0, "재생 중 상향 문턱 아래는 무시");
  feed(det, 5000, 5200, 60, true);        // 58 초과
  const e2 = feed(det, 5200, 5300, 40, true);
  assert.equal(e2.length, 1);
  assert.equal(e2[0].duringPlayback, true, "재생 중 감지는 별도 표시 (기획 실패지점 3)");
});

test("재생 중 바닥 동결: 재생음 46 dB 가 바닥을 끌어올리지 못한다", () => {
  const det = calibrated();
  feed(det, 3025, 20000, 46, true); // 재생 중 — 바닥 갱신 없어야 함
  assert.equal(det.getNoiseFloor(), 40, "재생 중에는 바닥 동결");
  feed(det, 20000, 25000, 46, false); // 재생 끝 — 이제 적응
  assert.equal(det.getNoiseFloor(), 46);
});

test("감도 변경이 문턱에 즉시 반영", () => {
  const det = calibrated();
  det.setSensitivityDb(20);
  assert.equal(det.getThreshold(false), 60);
  const ev = [
    ...feed(det, 3025, 3500, 55),   // 예전 문턱(52) 위지만 새 문턱(60) 아래
    ...feed(det, 3500, 4000, 40),
  ];
  assert.equal(ev.length, 0);
});
