// study.js — 에피소드 집계 · 무작위 배정 · CSV 생성 (순수 로직 모듈)
//
// DOM / WebAudio / 전역 시계(현재 시각 API)를 참조하지 않는다 — 시각은 전부 인자로 받는다.
// 그래서 node --test 로 손계산 기대값과 대조할 수 있다 (tests/study.test.mjs).
//
// 산출 CSV 두 벌 (기획 §범위 6 + 라운드 1 C1):
//   이벤트 로그   : 타임스탬프,유형,에피소드ID,상세
//   에피소드 요약 : 에피소드ID,시작,종료,짖음횟수,지속초,개입여부,모드,seed,
//                   재생겹침초,후속180초짖음수
//
// 라운드 1 C1 — 무작위 모드 대조군 오염 문제:
//   재생(기본 180초)이 에피소드 병합 간격(30초)보다 길어서, 개입 에피소드의
//   재생이 아직 나오는 중에 시작된 "비개입" 에피소드는 실제로는 소리에 노출된다.
//   에피소드 규칙을 군마다 다르게 바꾸면 병합 자체가 편향되므로 규칙은 대칭으로
//   두고, 대신 (a) 오염을 열로 드러내고(재생겹침초) (b) 두 군에 동일하게 적용되는
//   고정 창 결과지표(후속180초짖음수 — 에피소드 시작 후 180초 안 짖음 수)를
//   1차 비교 지표로 제공한다. 재생겹침초>0 인 비개입 에피소드는 per-protocol
//   비교에서 제외하라(README·사용법). 분석은 여기서 하지 않는다 — statwise 몫.

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
 * null/undefined 는 빈 칸 (라운드 1 M6).
 * 문자열이 탭/CR/=/+/−/@ 로 시작하면 ' 접두 (라운드 1 M7: 탭·CR 로 시작해
 * 가드를 우회하는 변종까지 차단). 쉼표·따옴표·줄바꿈은 RFC 4180 인용.
 */
export function csvCell(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) throw new Error("CSV에 비유한 숫자: " + v);
    return String(v);
  }
  let s = String(v);
  if (/^[\t\r=+\-@]/.test(s)) s = "'" + s;
  if (/[",\r\n\t]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

/** 행 배열 → CSV 문자열 (CRLF, 마지막 줄 개행 포함). BOM은 호출측(파일 저장) 몫. */
export function buildCsv(headerRow, rows) {
  const lines = [headerRow, ...rows].map((r) => r.map(csvCell).join(","));
  return lines.join("\r\n") + "\r\n";
}

export const EVENT_HEADER = ["타임스탬프", "유형", "에피소드ID", "상세"];
export const EPISODE_HEADER = [
  "에피소드ID", "시작", "종료", "짖음횟수", "지속초", "개입여부", "모드", "seed",
  "재생겹침초", "후속180초짖음수",
];

// ---------------------------------------------------------------- 스터디 세션

export const EPISODE_GAP_MS = 30000;      // 30초 안 재짖음 = 같은 에피소드 (기획 §범위 2)
export const OUTCOME_WINDOW_MS = 180000;  // 라운드 1 C1: 고정 결과지표 창 —
                                          // 재생길이 슬라이더와 무관하게 180초 고정
                                          // (두 군에 동일해야 편향이 없다)

/**
 * 한 세션의 전체 기록: 이벤트 로그 + 에피소드 집계 + 배정 + 재생 구간.
 *
 * 사용 (app.js / 테스트 공통):
 *   const s = createStudy({ mode: "무작위", seed: 42 });
 *   s.logEvent(tMs, "세션시작", "", "감도=12dB");
 *   const r = s.onBark({ tMs, endMs, peakDb, duringPlayback });
 *   s.playbackStarted(tMs); s.playbackEnded(tMs);  // 가청 구간 (페이드아웃 끝까지)
 *   s.tick(nowMs); s.finalize(); s.eventsCsv(tz); s.episodesCsv(tz);
 */
export function createStudy({ mode, seed, gapMs = EPISODE_GAP_MS }) {
  if (!MODES.includes(mode)) throw new Error("알 수 없는 모드: " + mode);
  const assigner = createAssigner(seed);
  const events = [];            // {tMs, type, episodeId, detail}
  const episodes = [];          // {id, startMs, endMs, barkCount, intervene}
  const allBarks = [];          // {tMs} — 후속창 짖음수 계산용 (에피소드 무관 전체)
  const playbackIntervals = []; // {startMs, endMs|null} — 가청 재생 구간 (C1)
  let open = null;
  let lastBarkEndMs = null;

  function episodeId(index) {
    return "E" + String(index + 1).padStart(4, "0"); // 라운드 1 M6: 1000개 초과 정렬 대비
  }

  function logEvent(tMs, type, epId = "", detail = "") {
    events.push({ tMs, type, episodeId: epId, detail });
  }

  function closeOpen(endMs) {
    if (endMs < open.startMs) {
      // 라운드 1 M5: 시계 역행 방지선 — 앱이 단조 시계를 쓰므로 도달 불가해야 한다
      throw new Error(`에피소드 지속시간이 음수 (${open.id}: ${open.startMs} → ${endMs})`);
    }
    open.endMs = endMs;
    episodes.push(open);
    logEvent(endMs, "에피소드종료", open.id,
      `짖음=${open.barkCount};지속초=${round1((open.endMs - open.startMs) / 1000)}`);
    open = null;
  }

  function round1(v) { return Math.round(v * 10) / 10; }

  /** [aLo,aHi] 와 재생 구간들의 겹침 초 (열린 구간은 aHi 까지로 간주). */
  function overlapSec(aLo, aHi) {
    let total = 0;
    for (const itv of playbackIntervals) {
      const bLo = itv.startMs;
      const bHi = itv.endMs === null ? aHi : itv.endMs;
      total += Math.max(0, Math.min(aHi, bHi) - Math.max(aLo, bLo));
    }
    return round1(total / 1000);
  }

  function metricsFor(e) {
    const winEnd = e.startMs + OUTCOME_WINDOW_MS;
    return {
      ...e,
      overlapSec: overlapSec(e.startMs, e.endMs),
      followWindowBarks: allBarks.filter((b) => b.tMs >= e.startMs && b.tMs < winEnd).length,
    };
  }

  return {
    logEvent,

    /** 감지기 bark 이벤트 반영. */
    onBark({ tMs, endMs, peakDb, duringPlayback = false }) {
      // 30초 규칙: 직전 짖음 "종료"에서 gapMs 이상 지나면 새 에피소드
      if (open && tMs - lastBarkEndMs >= gapMs) closeOpen(lastBarkEndMs);

      // 라운드 1 M1: tick 이 경계에서 방금 닫았는데 그 경계에 걸친 짖음이
      // 도착한 경우(진행 중이던 버스트) — 직전 에피소드를 다시 열어 병합한다.
      // 새 배정은 뽑지 않는다 (재개된 에피소드는 원래 배정을 유지).
      let reopened = false;
      if (!open && episodes.length > 0 && lastBarkEndMs !== null && tMs - lastBarkEndMs < gapMs) {
        open = episodes.pop();
        reopened = true;
        logEvent(tMs, "에피소드재개", open.id, "경계 짖음 재병합 — 배정 유지");
      }

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
      allBarks.push({ tMs });
      logEvent(tMs, duringPlayback ? "짖음(재생중)" : "짖음", open.id,
        `peak dB=${round1(peakDb)}`);
      return { episodeStarted, reopened, intervene: open.intervene, episode: { ...open } };
    },

    /** 재생 가청 구간 기록 (라운드 1 C1 — 페이드인 시작 ~ 페이드아웃 끝). */
    playbackStarted(tMs) {
      const last = playbackIntervals[playbackIntervals.length - 1];
      if (last && last.endMs === null) return; // 이미 열려 있음 (연장은 구간 유지)
      playbackIntervals.push({ startMs: tMs, endMs: null });
    },
    playbackEnded(tMs) {
      const last = playbackIntervals[playbackIntervals.length - 1];
      if (last && last.endMs === null) last.endMs = tMs;
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
    /** 파생 지표 포함 (재생겹침초·후속180초짖음수) — 요약 화면·CSV 용. */
    getEpisodeMetrics() { return episodes.map(metricsFor); },
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
        episodes.map(metricsFor).map((e) => [
          e.id,
          formatTimestamp(e.startMs, tzOffsetMinutes),
          formatTimestamp(e.endMs, tzOffsetMinutes),
          e.barkCount,
          round1((e.endMs - e.startMs) / 1000),
          e.intervene ? 1 : 0,
          mode,
          seed,
          e.overlapSec,
          e.followWindowBarks,
        ])
      );
    },
  };
}

// ---------------------------------------------------------------- 기술통계

/**
 * 개입/비개입 기술통계 비교 — 여기까지만. 검정은 statwise 의 몫이다.
 * 입력은 getEpisodeMetrics() 결과를 권장 (후속창·오염 지표 포함).
 * 라운드 1 C1: 1차 비교 지표는 두 군에 동일한 고정 창의 후속180초짖음수이며,
 * 재생겹침초>0 인 비개입 에피소드(오염)는 contaminatedControls 로 센다.
 */
export function summarize(episodes) {
  const g = (flag) => episodes.filter((e) => e.intervene === flag);
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  const r1 = (v) => Math.round(v * 10) / 10;
  const stat = (list) => {
    if (list.length === 0) {
      return { n: 0, meanDurationSec: null, meanBarks: null, meanFollowWindowBarks: null };
    }
    const withFollow = list.filter((e) => typeof e.followWindowBarks === "number");
    return {
      n: list.length,
      meanDurationSec: r1(mean(list.map((e) => (e.endMs - e.startMs) / 1000))),
      meanBarks: r1(mean(list.map((e) => e.barkCount))),
      meanFollowWindowBarks: withFollow.length ? r1(mean(withFollow.map((e) => e.followWindowBarks))) : null,
    };
  };
  const contaminatedControls = g(false).filter(
    (e) => typeof e.overlapSec === "number" && e.overlapSec > 0
  ).length;
  return { intervened: stat(g(true)), control: stat(g(false)), contaminatedControls };
}
