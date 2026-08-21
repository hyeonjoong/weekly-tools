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
      if (startMs === null) startMs = tMs;

      if (state === "CALIBRATING") {
        floorSamples.push(bandDb);
        if (tMs - startMs >= cfg.calibrationMs) {
          floorRing = floorSamples.slice(-cfg.floorWindowFrames);
          floorDb = median(floorRing);
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
    getConfig() { return { ...cfg }; },
  };
}
