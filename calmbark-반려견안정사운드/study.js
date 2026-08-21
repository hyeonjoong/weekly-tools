// study.js — 에피소드 집계 · 무작위 배정 · CSV 생성 (순수 로직 모듈)
//
// DOM / WebAudio / 전역 시계(현재 시각 API)를 참조하지 않는다 — 시각은 전부 인자로 받는다.
// 그래서 node --test 로 손계산 기대값과 대조할 수 있다 (tests/study.test.mjs).
//
// 산출 CSV 두 벌 (기획 §범위 6):
//   이벤트 로그   : 타임스탬프,유형,에피소드ID,상세
//   에피소드 요약 : 에피소드ID,시작,종료,짖음횟수,지속초,개입여부,모드,seed
// 에피소드 요약은 statwise(개입/비개입 비교)·longistat(일차 추이)에 그대로
// 들어가는 스키마다. 분석은 여기서 하지 않는다 — 기술통계까지만 (summarize).

// ---------------------------------------------------------------- PRNG
// engine.js 와 동일한 mulberry32 를 의도적으로 중복 정의한다 — 각 순수 모듈이
// 자기 완결적이도록 (모듈 간 import 없음). 알고리즘 드리프트는 테스트가 잡는다.
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------- 무작위 배정

export const MODES = ["관찰", "상시", "무작위"];

/**
 * 에피소드 단위 50% 무작위 배정기. 같은 seed → 같은 배정 수열 (재현 가능).
 * k번째 에피소드는 k번째 draw 를 쓴다 — 조회 순서와 무관하게 결정적.
 */
export function createAssigner(seed) {
  const rng = mulberry32(seed);
  const draws = [];
  return {
    assignmentFor(episodeIndex) {
      if (!Number.isInteger(episodeIndex) || episodeIndex < 0) {
        throw new Error("에피소드 인덱스는 0 이상 정수: " + episodeIndex);
      }
      while (draws.length <= episodeIndex) draws.push(rng() < 0.5);
      return draws[episodeIndex];
    },
  };
}

// ---------------------------------------------------------------- 시각 포맷

/** epoch ms → "YYYY-MM-DD HH:MM:SS" (주어진 오프셋 기준 — 현재 시각·로캘 비의존). */
export function formatTimestamp(msEpoch, tzOffsetMinutes) {
  const d = new Date(msEpoch + tzOffsetMinutes * 60000);
  const p = (v, w = 2) => String(v).padStart(w, "0");
  return (
    d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate()) +
    " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds())
  );
}

/** 파일명용 "YYYYMMDD_HHMM". */
export function formatFileStamp(msEpoch, tzOffsetMinutes) {
  const d = new Date(msEpoch + tzOffsetMinutes * 60000);
  const p = (v) => String(v).padStart(2, "0");
  return (
    d.getUTCFullYear() + p(d.getUTCMonth() + 1) + p(d.getUTCDate()) +
    "_" + p(d.getUTCHours()) + p(d.getUTCMinutes())
  );
}

// ---------------------------------------------------------------- CSV

/**
 * 셀 직렬화 + 수식 인젝션 가드.
 * 숫자는 비가드(음수 −3.5 가 "'-3.5" 로 망가지면 안 된다 — 기획 완료기준).
 * 문자열이 = + - @ 로 시작하면 ' 접두 (Excel/Sheets 수식 실행 차단).
 * 쉼표·따옴표·줄바꿈은 RFC 4180 방식으로 감싼다.
 */
export function csvCell(v) {
  if (typeof v === "number") {
    if (!Number.isFinite(v)) throw new Error("CSV에 비유한 숫자: " + v);
    return String(v);
  }
  let s = String(v);
  if (/^[=+\-@]/.test(s)) s = "'" + s;
  if (/[",\r\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

/** 행 배열 → CSV 문자열 (CRLF, 마지막 줄 개행 포함). BOM은 호출측(파일 저장) 몫. */
export function buildCsv(headerRow, rows) {
  const lines = [headerRow, ...rows].map((r) => r.map(csvCell).join(","));
  return lines.join("\r\n") + "\r\n";
}

export const EVENT_HEADER = ["타임스탬프", "유형", "에피소드ID", "상세"];
export const EPISODE_HEADER = ["에피소드ID", "시작", "종료", "짖음횟수", "지속초", "개입여부", "모드", "seed"];

// ---------------------------------------------------------------- 스터디 세션

export const EPISODE_GAP_MS = 30000; // 30초 안 재짖음 = 같은 에피소드 (기획 §범위 2)

/**
 * 한 세션의 전체 기록: 이벤트 로그 + 에피소드 집계 + 배정.
 *
 * @param {object} p
 * @param {"관찰"|"상시"|"무작위"} p.mode
 * @param {number} p.seed        무작위 모드 배정 시드 (다른 모드에서도 기록용)
 * @param {number} [p.gapMs]     에피소드 병합 간격 (기본 30000)
 *
 * 사용 (app.js / 테스트 공통):
 *   const s = createStudy({ mode: "무작위", seed: 42 });
 *   s.logEvent(tMs, "세션시작", "", "감도=12dB");
 *   const r = s.onBark({ tMs, endMs, peakDb, duringPlayback });
 *   // r.episodeStarted === true 면 r.intervene 에 따라 앱이 재생 시작
 *   s.tick(nowMs); // 주기 호출 — 30초 무짖음이면 에피소드 종료
 *   s.finalize(nowMs); s.eventsCsv(tz); s.episodesCsv(tz);
 */
export function createStudy({ mode, seed, gapMs = EPISODE_GAP_MS }) {
  if (!MODES.includes(mode)) throw new Error("알 수 없는 모드: " + mode);
  const assigner = createAssigner(seed);
  const events = [];   // {tMs, type, episodeId, detail}
  const episodes = []; // {id, startMs, endMs, barkCount, intervene}
  let open = null;     // 진행 중 에피소드
  let lastBarkEndMs = null;

  function episodeId(index) {
    return "E" + String(index + 1).padStart(3, "0");
  }

  function logEvent(tMs, type, epId = "", detail = "") {
    events.push({ tMs, type, episodeId: epId, detail });
  }

  function closeOpen(endMs) {
    open.endMs = endMs;
    episodes.push(open);
    logEvent(endMs, "에피소드종료", open.id,
      `짖음=${open.barkCount};지속초=${round1((open.endMs - open.startMs) / 1000)}`);
    open = null;
  }

  function round1(v) { return Math.round(v * 10) / 10; }

  return {
    logEvent,

    /** 감지기 bark 이벤트 반영. */
    onBark({ tMs, endMs, peakDb, duringPlayback = false }) {
      // 30초 규칙: 직전 짖음 "종료"에서 gapMs 이상 지나면 새 에피소드
      if (open && tMs - lastBarkEndMs >= gapMs) closeOpen(lastBarkEndMs);

      let episodeStarted = false;
      if (!open) {
        const index = episodes.length;
        const intervene =
          mode === "상시" ? true : mode === "무작위" ? assigner.assignmentFor(index) : false;
        open = { id: episodeId(index), startMs: tMs, endMs, barkCount: 0, intervene };
        episodeStarted = true;
        logEvent(tMs, "에피소드시작", open.id, "");
        logEvent(tMs, "배정", open.id, `개입=${intervene ? 1 : 0};모드=${mode};seed=${seed}`);
      }
      open.barkCount += 1;
      open.endMs = endMs;
      lastBarkEndMs = endMs;
      logEvent(tMs, duringPlayback ? "짖음(재생중)" : "짖음", open.id,
        `peak dB=${round1(peakDb)}`);
      return { episodeStarted, intervene: open.intervene, episode: { ...open } };
    },

    /** 주기 호출 — gapMs 무짖음이면 에피소드 종료. 종료됐으면 에피소드 반환. */
    tick(nowMs) {
      if (open && nowMs - lastBarkEndMs >= gapMs) {
        const closed = { ...open };
        closeOpen(lastBarkEndMs);
        return closed;
      }
      return null;
    },

    /** 세션 종료 — 열린 에피소드를 마지막 짖음 종료 시각으로 닫는다. */
    finalize() {
      if (open) closeOpen(lastBarkEndMs);
    },

    getEpisodes() { return episodes.map((e) => ({ ...e })); },
    getOpenEpisode() { return open ? { ...open } : null; },
    getEvents() { return events.map((e) => ({ ...e })); },

    eventsCsv(tzOffsetMinutes) {
      return buildCsv(
        EVENT_HEADER,
        events.map((e) => [formatTimestamp(e.tMs, tzOffsetMinutes), e.type, e.episodeId, e.detail])
      );
    },

    episodesCsv(tzOffsetMinutes) {
      return buildCsv(
        EPISODE_HEADER,
        episodes.map((e) => [
          e.id,
          formatTimestamp(e.startMs, tzOffsetMinutes),
          formatTimestamp(e.endMs, tzOffsetMinutes),
          e.barkCount,
          round1((e.endMs - e.startMs) / 1000),
          e.intervene ? 1 : 0,
          mode,
          seed,
        ])
      );
    },
  };
}

// ---------------------------------------------------------------- 기술통계

/**
 * 개입/비개입 기술통계 비교 — 여기까지만. 검정은 statwise 의 몫이다.
 * (UI 는 이 결과 옆에 "표본이 작으면 우연 — statwise 로 검정하세요"를 띄운다.)
 */
export function summarize(episodes) {
  const g = (flag) => episodes.filter((e) => e.intervene === flag);
  const stat = (list) => {
    if (list.length === 0) return { n: 0, meanDurationSec: null, meanBarks: null };
    const durs = list.map((e) => (e.endMs - e.startMs) / 1000);
    const barks = list.map((e) => e.barkCount);
    const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
    return {
      n: list.length,
      meanDurationSec: Math.round(mean(durs) * 10) / 10,
      meanBarks: Math.round(mean(barks) * 10) / 10,
    };
  };
  return { intervened: stat(g(true)), control: stat(g(false)) };
}
