// detector.js — 짖음 감지 상태기계 (순수 로직 모듈)
//
// DOM / WebAudio 를 일절 참조하지 않는다. app.js 가 AnalyserNode 에서 뽑은
// "특징 프레임"(시각 ms + 250–4000 Hz 대역 에너지 dB + 재생 중 여부)을 숫자로
// 넣으면 이벤트 목록을 돌려줄 뿐이다. 그래서 node --test 로 완전 오프라인
// 검증이 된다 (tests/detector.test.mjs).
//
// ★ 정직성: 이것은 휴리스틱 에너지 검출기다. ML 짖음 분류가 아니다. ★
// TV 소리·문 닫는 소리를 짖음으로 오탐할 수 있고, 작은 낑낑거림을 놓칠 수
// 있다. 오탐도 로그에 남으므로 사후에 식별 가능하다(README 한계 절 참조).
//
// 상태기계:
//   CALIBRATING --(calibrationMs 경과)--> IDLE
//   IDLE --(대역 dB > 문턱)--> CANDIDATE
//   CANDIDATE --(minBurstMs 미만에 문턱 아래로)--> IDLE     (짧은 소리 무시)
//   CANDIDATE --(minBurstMs 이상 지속)--> BARKING
//   BARKING --(문턱 아래로 or maxBurstMs 초과)--> REFRACTORY (bark 이벤트 방출)
//   REFRACTORY --(refractoryMs 경과)--> IDLE                (재트리거 방지)
//
// 문턱 = 소음 바닥(롤링 중앙값) + sensitivityDb (+ 재생 중이면 playbackExtraDb)
//
// 재생 중 자기 트리거 완화 (기획 실패지점 3):
//   1) 재생 중에는 문턱을 playbackExtraDb 만큼 상향 — 스피커 소리가 짖음으로
//      오인될 여지를 줄인다 (재생음은 저역 중심이라 250–4000 Hz 대역 에너지
//      기여가 작지만, 방·스피커에 따라 새어 들어올 수 있다).
//   2) 재생 중에는 소음 바닥 갱신을 동결 — 재생음이 바닥을 끌어올려 이후
//      감도가 조용히 무뎌지는 것을 막는다.
//   3) 재생 중 감지된 짖음은 duringPlayback=true 로 별도 표시되어 로그에
//      정직하게 남는다 — 자기 트리거 의심 여부를 사후에 감사할 수 있다.

export const DETECTOR_DEFAULTS = {
  calibrationMs: 3000,   // 시작 후 이 시간은 소음 바닥 수집만 (감지 없음)
  sensitivityDb: 12,     // 바닥 대비 초과분 — UI 슬라이더로 조정
  minBurstMs: 60,        // 이보다 짧은 버스트는 무시 (클릭·문 소리 일부 걸러냄)
  maxBurstMs: 5000,      // 이보다 길면 강제 종료 (지속 소음이 짖음 1회로 무한 유지되는 것 방지)
  refractoryMs: 250,     // 버스트 종료 후 재트리거 금지 구간
  floorWindowFrames: 200, // 소음 바닥 롤링 중앙값 창 (25 ms 프레임 기준 ≈ 5 s)
  playbackExtraDb: 6,    // 재생 중 문턱 상향
  minFloorDb: -100,      // 라운드 1 C4: 보정 바닥이 이 이하(무음 클램프 근처)면
                         // 마이크 음소거/무입력으로 보고 보정을 거부한다 —
                         // −120 dB 바닥으로 보정되면 이후 모든 잡음이 "짖음"이
                         // 되고, 바닥 적응은 문턱 아래 프레임에서만 돌아서
                         // 영원히 회복 불가(유령 짖음 폭풍)임이 패널에서 재현됨
  frameGapMs: 5000,      // 라운드 1 C7: 프레임 간격이 이보다 크면(절전/탭 정지)
                         // 진행 중 버스트를 폐기 — 2시간 갭이 "7200초 짖음 1회"로
                         // 기록되는 것을 막는다
};

function median(arr) {
  const a = [...arr].sort((x, y) => x - y);
  const mid = a.length >> 1;
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
}

/**
 * 짖음 감지기 생성.
 * @param {object} config DETECTOR_DEFAULTS 의 부분 재정의
 * @returns {{ processFrame, getState, getNoiseFloor, getThreshold, setSensitivityDb }}
 *
 * processFrame({ tMs, bandDb, playbackActive }) → 이벤트 배열:
 *   { type: "calibrated", tMs, floorDb }
 *   { type: "calibration_failed", tMs, floorDb }  // C4: 마이크 무입력 의심 — 세션 시작 불가
 *   { type: "framegap", tMs, gapSec }             // C7: 프레임 공백 — 진행 버스트 폐기
 *   { type: "bark", tMs, endMs, peakDb, duringPlayback }
 */
export function createDetector(config = {}) {
  const cfg = { ...DETECTOR_DEFAULTS, ...config };
  let state = "CALIBRATING";
  let startMs = null;         // 첫 프레임 시각
  let floorSamples = [];      // 보정 구간 수집
  let floorRing = [];         // 롤링 중앙값 링버퍼
  let floorDb = null;
  let burstStartMs = 0;
  let burstPeakDb = -Infinity;
  let burstDuringPlayback = false;
  let refractoryUntilMs = 0;
  let lastFrameMs = null;     // C7: 프레임 공백 감지
  let invalidFrames = 0;      // M4: NaN/±Inf 입력 프레임 수

  function threshold(playbackActive) {
    return floorDb + cfg.sensitivityDb + (playbackActive ? cfg.playbackExtraDb : 0);
  }

  function pushFloor(bandDb) {
    floorRing.push(bandDb);
    if (floorRing.length > cfg.floorWindowFrames) floorRing.shift();
    floorDb = median(floorRing);
  }

  return {
    processFrame({ tMs, bandDb, playbackActive = false }) {
      const events = [];
      // 라운드 1 M4: 비유한 입력은 프레임 자체를 무시 (집계만 하고 상태 불변)
      if (!Number.isFinite(tMs) || !Number.isFinite(bandDb)) {
        invalidFrames += 1;
        return events;
      }
      if (startMs === null) startMs = tMs;

      // 라운드 1 C7: 프레임 공백(절전·탭 정지) — 진행 중 버스트를 "짖음"으로
      // 내보내지 않고 폐기한다. 보정 중이었다면 보정을 처음부터 다시 시작.
      if (lastFrameMs !== null && tMs - lastFrameMs > cfg.frameGapMs) {
        events.push({ type: "framegap", tMs, gapSec: Math.round((tMs - lastFrameMs) / 1000) });
        if (state === "CALIBRATING") {
          startMs = tMs;
          floorSamples = [];
        } else if (state === "CANDIDATE" || state === "BARKING") {
          state = "IDLE"; // 갭을 걸친 버스트는 신뢰 불가 — bark 미방출 폐기
        }
      }
      lastFrameMs = tMs;

      if (state === "CALIBRATING") {
        floorSamples.push(bandDb);
        if (tMs - startMs >= cfg.calibrationMs) {
          const floor = median(floorSamples.slice(-cfg.floorWindowFrames));
          if (floor <= cfg.minFloorDb) {
            // 라운드 1 C4: 무음 클램프 수준의 바닥 = 마이크 음소거/무입력 의심.
            // 이대로 보정하면 문턱이 −108 dB 가 되어 모든 프레임이 짖음이 되고,
            // 바닥 적응은 "문턱 아래" 프레임에서만 돌므로 영원히 회복 불가.
            floorSamples = [];
            startMs = tMs; // 재시도 대비 리셋 (앱은 세션을 중단하고 안내한다)
            events.push({ type: "calibration_failed", tMs, floorDb: floor });
            return events;
          }
          floorRing = floorSamples.slice(-cfg.floorWindowFrames);
          floorDb = floor;
          floorSamples = [];
          state = "IDLE";
          events.push({ type: "calibrated", tMs, floorDb });
        }
        return events;
      }

      const th = threshold(playbackActive);
      const above = bandDb > th;

      // 소음 바닥 적응: 문턱 아래 & 재생 중 아님 & 버스트 진행 중 아님일 때만.
      // (재생 중 동결 — 자기 트리거 완화 2번. 버스트 중 동결 — 짖음이 바닥을
      //  끌어올리는 것 방지.)
      if (!above && !playbackActive && (state === "IDLE" || state === "REFRACTORY")) {
        pushFloor(bandDb);
      }

      switch (state) {
        case "IDLE":
          if (above && tMs >= refractoryUntilMs) {
            state = "CANDIDATE";
            burstStartMs = tMs;
            burstPeakDb = bandDb;
            burstDuringPlayback = playbackActive;
          }
          break;
        case "CANDIDATE":
          if (bandDb > burstPeakDb) burstPeakDb = bandDb;
          if (playbackActive) burstDuringPlayback = true;
          if (!above) {
            state = "IDLE"; // minBurstMs 미달 — 무시
          } else if (tMs - burstStartMs >= cfg.minBurstMs) {
            state = "BARKING";
          }
          break;
        case "BARKING":
          if (bandDb > burstPeakDb) burstPeakDb = bandDb;
          if (playbackActive) burstDuringPlayback = true;
          if (!above || tMs - burstStartMs >= cfg.maxBurstMs) {
            events.push({
              type: "bark",
              tMs: burstStartMs,
              endMs: tMs,
              peakDb: burstPeakDb,
              duringPlayback: burstDuringPlayback,
            });
            refractoryUntilMs = tMs + cfg.refractoryMs;
            state = "REFRACTORY";
          }
          break;
        case "REFRACTORY":
          if (tMs >= refractoryUntilMs) {
            state = "IDLE";
            // 불응기 종료 시점에 여전히 문턱 위면 다음 프레임에서 새 버스트로 잡힌다
          }
          break;
      }
      return events;
    },
    getState() { return state; },
    getNoiseFloor() { return floorDb; },
    getThreshold(playbackActive = false) {
      return floorDb === null ? null : threshold(playbackActive);
    },
    setSensitivityDb(db) { cfg.sensitivityDb = db; },
    getInvalidFrameCount() { return invalidFrames; }, // 라운드 1 M4
    getConfig() { return { ...cfg }; },
  };
}
