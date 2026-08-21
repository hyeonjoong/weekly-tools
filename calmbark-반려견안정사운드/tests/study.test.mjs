// tests/study.test.mjs — 에피소드 집계 · 무작위 배정 · CSV 를 손계산과 대조

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createStudy,
  createAssigner,
  csvCell,
  buildCsv,
  formatTimestamp,
  formatFileStamp,
  summarize,
  EVENT_HEADER,
  EPISODE_HEADER,
  EPISODE_GAP_MS,
  OUTCOME_WINDOW_MS,
} from "../study.js";

const T0 = Date.UTC(2026, 7, 21, 3, 0, 0); // 2026-08-21 03:00:00Z = KST 12:00
const KST = 540;

function bark(study, tMs, endMs, peakDb = 60, duringPlayback = false) {
  return study.onBark({ tMs, endMs, peakDb, duringPlayback });
}

// ---------------------------------------------------------------- 에피소드 규칙

test("에피소드 30초 규칙: 10초 간격은 병합, 34.5초 간격은 새 에피소드", () => {
  const s = createStudy({ mode: "관찰", seed: 1 });
  bark(s, T0, T0 + 500);            // 짖음1: 0.0–0.5 s
  bark(s, T0 + 10000, T0 + 10500);  // 짖음2: 직전 종료(0.5s)에서 9.5초 뒤 → 병합
  bark(s, T0 + 45000, T0 + 45300);  // 직전 종료(10.5s)에서 34.5초 뒤 → 새 에피소드
  s.finalize();
  const eps = s.getEpisodes();
  assert.equal(eps.length, 2);
  assert.equal(eps[0].id, "E0001");
  assert.equal(eps[0].barkCount, 2);
  assert.equal(eps[0].startMs, T0);
  assert.equal(eps[0].endMs, T0 + 10500);      // 지속 10.5초
  assert.equal(eps[1].id, "E0002");
  assert.equal(eps[1].barkCount, 1);
  assert.equal(eps[1].endMs - eps[1].startMs, 300);
});

test("정확히 30초 간격도 새 에피소드 (경계값: gap ≥ 30000)", () => {
  const s = createStudy({ mode: "관찰", seed: 1 });
  bark(s, T0, T0 + 500);
  bark(s, T0 + 500 + EPISODE_GAP_MS, T0 + 500 + EPISODE_GAP_MS + 200);
  s.finalize();
  assert.equal(s.getEpisodes().length, 2);
});

test("tick: 30초 무짖음이면 에피소드가 닫힌다 (경계 직전은 유지)", () => {
  const s = createStudy({ mode: "관찰", seed: 1 });
  bark(s, T0, T0 + 500);
  assert.equal(s.tick(T0 + 500 + 29999), null, "29.999초 — 아직 열림");
  assert.ok(s.getOpenEpisode());
  const closed = s.tick(T0 + 500 + 30000);
  assert.ok(closed, "30초 — 닫힘");
  assert.equal(closed.id, "E0001");
  assert.equal(s.getOpenEpisode(), null);
  assert.equal(s.getEpisodes()[0].endMs, T0 + 500, "종료 시각 = 마지막 짖음 종료");
});

test("M1: tick 경계 경합 — 경계에 걸친 짖음은 방금 닫힌 에피소드를 재개한다", () => {
  const s = createStudy({ mode: "무작위", seed: 42 });
  const r1 = bark(s, T0, T0 + 500);
  const closed = s.tick(T0 + 500 + 30000); // 경계에서 닫힘
  assert.ok(closed);
  // 경계 이전에 시작된 버스트의 bark 가 뒤늦게 도착 (tMs − lastBarkEnd < 30초)
  const r2 = bark(s, T0 + 500 + 29900, T0 + 500 + 30100);
  assert.equal(r2.reopened, true, "재개돼야 함");
  assert.equal(r2.episodeStarted, false, "새 에피소드가 아님");
  assert.equal(r2.episode.id, "E0001");
  assert.equal(r2.intervene, r1.intervene, "원래 배정 유지 — 새 draw 없음");
  s.finalize();
  const eps = s.getEpisodes();
  assert.equal(eps.length, 1, "재병합 — 에피소드 1개");
  assert.equal(eps[0].barkCount, 2);
  const types = s.getEvents().map((e) => e.type);
  assert.ok(types.includes("에피소드재개"), "원장에 재개가 남아야 함");
  // 배정 수열이 어긋나지 않았는지: 다음 새 에피소드는 index 1 의 draw 를 받는다
  const r3 = bark(s, T0 + 500 + 30100 + 40000, T0 + 500 + 30100 + 40200);
  assert.equal(r3.episodeStarted, true);
  assert.equal(r3.intervene, createAssigner(42).assignmentFor(1), "index 1 draw — 유령 draw 없음");
});

test("M5: 시계 역행으로 지속시간이 음수가 되면 한국어 오류로 거부한다", () => {
  const s = createStudy({ mode: "관찰", seed: 1 });
  s.onBark({ tMs: T0 + 1000, endMs: T0 + 900, peakDb: 60 }); // endMs < tMs (역행)
  assert.throws(() => s.finalize(), /음수/);
});

// ---------------------------------------------------------------- 배정

test("모드별 개입: 관찰=항상 0, 상시=항상 1", () => {
  for (const [mode, expected] of [["관찰", false], ["상시", true]]) {
    const s = createStudy({ mode, seed: 7 });
    for (let k = 0; k < 5; k++) {
      const r = bark(s, T0 + k * 60000, T0 + k * 60000 + 300);
      assert.equal(r.episodeStarted, true);
      assert.equal(r.intervene, expected, mode);
    }
  }
});

test("무작위 배정: 같은 seed → 같은 수열, 다른 seed → 다른 수열", () => {
  const seq = (seed) => {
    const s = createStudy({ mode: "무작위", seed });
    const flags = [];
    for (let k = 0; k < 20; k++) flags.push(bark(s, T0 + k * 60000, T0 + k * 60000 + 300).intervene);
    return flags;
  };
  const a = seq(42), b = seq(42), c = seq(43);
  assert.deepEqual(a, b, "같은 seed 재현");
  assert.notDeepEqual(a, c, "다른 seed 는 달라야 함 (seed 42/43 에서 확인됨)");
  assert.ok(a.includes(true) && a.includes(false), "20회면 양쪽 다 나와야 정상");
});

test("createAssigner: 인덱스 조회가 멱등이고 순서 무관", () => {
  const a1 = createAssigner(123);
  const later = a1.assignmentFor(5);
  const early = a1.assignmentFor(0);
  const a2 = createAssigner(123);
  assert.equal(a2.assignmentFor(0), early);
  assert.equal(a2.assignmentFor(5), later);
  assert.equal(a1.assignmentFor(5), later, "재조회 멱등");
  assert.throws(() => a1.assignmentFor(-1));
});

test("배정이 이벤트 로그에 남는다 (seed 포함)", () => {
  const s = createStudy({ mode: "무작위", seed: 99 });
  const r = bark(s, T0, T0 + 300);
  const assign = s.getEvents().filter((e) => e.type === "배정");
  assert.equal(assign.length, 1);
  assert.equal(assign[0].episodeId, "E0001");
  assert.match(assign[0].detail, /개입=[01];모드=무작위;seed=99/);
  assert.equal(assign[0].detail.includes(`개입=${r.intervene ? 1 : 0}`), true);
});

test("재생 중 짖음은 별도 유형으로 로그된다", () => {
  const s = createStudy({ mode: "상시", seed: 1 });
  bark(s, T0, T0 + 300, 61, false);
  bark(s, T0 + 5000, T0 + 5300, 63, true);
  const types = s.getEvents().map((e) => e.type);
  assert.ok(types.includes("짖음"));
  assert.ok(types.includes("짖음(재생중)"), "자기 트리거 의심 여부를 사후 감사 가능해야 (기획 실패지점 3)");
});

// ---------------------------------------------------------------- C1: 오염·고정창 지표

test("C1: 재생겹침초 — 개입 재생 중 시작된 비개입 에피소드는 오염으로 드러난다", () => {
  // 패널 재현 시나리오: 짖음 t=0/10/100s, E0001 개입(재생 0–190s), E0002 비개입.
  // 배정이 [개입, 비개입] 이 되는 seed 를 결정론적으로 찾는다.
  let seed = -1;
  for (let s = 1; s < 200; s++) {
    const a = createAssigner(s);
    if (a.assignmentFor(0) === true && a.assignmentFor(1) === false) { seed = s; break; }
  }
  assert.ok(seed > 0, "탐색 실패");
  const s = createStudy({ mode: "무작위", seed });
  const r1 = bark(s, T0, T0 + 500);
  assert.equal(r1.intervene, true);
  s.playbackStarted(T0);                       // 가청 구간 0 → 190s
  bark(s, T0 + 10000, T0 + 10500);
  const r3 = bark(s, T0 + 100000, T0 + 100300); // 89.5s 공백 → 새 에피소드 (재생 중!)
  assert.equal(r3.episodeStarted, true);
  assert.equal(r3.intervene, false, "비개입 배정인데 소리에 노출 — 오염 사례");
  s.playbackEnded(T0 + 190000);
  s.finalize();
  const m = s.getEpisodeMetrics();
  assert.equal(m.length, 2);
  assert.equal(m[0].overlapSec, 10.5, "E0001: 자기 재생과의 겹침 = 에피소드 전체");
  assert.equal(m[1].overlapSec, 0.3, "E0002: 비개입인데 겹침 > 0 → 오염 표시");
  // 고정 180초 창 짖음수 — 두 군에 동일한 창이라 1차 지표로 편향 없음
  assert.equal(m[0].followWindowBarks, 3, "E0001 창 [0,180s): 짖음 0/10/100s → 3");
  assert.equal(m[1].followWindowBarks, 1, "E0002 창 [100,280s): 짖음 100s → 1");
  const sum = summarize(m);
  assert.equal(sum.contaminatedControls, 1);
  assert.equal(sum.intervened.meanFollowWindowBarks, 3);
  assert.equal(sum.control.meanFollowWindowBarks, 1);
});

test("C1: 재생이 없으면 겹침 0, 열린 재생 구간은 에피소드 끝까지로 간주", () => {
  const s = createStudy({ mode: "상시", seed: 1 });
  bark(s, T0, T0 + 500);
  s.playbackStarted(T0 + 200); // playbackEnded 호출 없음 (세션 강제 종료 등)
  s.finalize();
  const m = s.getEpisodeMetrics();
  assert.equal(m[0].overlapSec, 0.3, "열린 구간 [0.2s, ∞) ∩ [0, 0.5s] = 0.3s");
  assert.equal(OUTCOME_WINDOW_MS, 180000, "고정 창은 180초 (재생길이 설정과 무관)");
});

// ---------------------------------------------------------------- CSV

test("에피소드 CSV: 헤더·행 수·내용 손계산 대조 (오염·고정창 열 포함)", () => {
  const s = createStudy({ mode: "상시", seed: 5 });
  bark(s, T0, T0 + 500);
  bark(s, T0 + 10000, T0 + 10500);
  bark(s, T0 + 60000, T0 + 60250);
  s.finalize();
  const csv = s.episodesCsv(KST);
  const lines = csv.trimEnd().split("\r\n");
  assert.equal(lines.length, 3, "헤더 + 에피소드 2");
  assert.equal(lines[0], EPISODE_HEADER.join(","));
  // E0001 후속창 [0,180s): 짖음 0/10/60s → 3. E0002 창 [60,240s): 짖음 60s → 1.
  assert.equal(lines[1], "E0001,2026-08-21 12:00:00,2026-08-21 12:00:10,2,10.5,1,상시,5,0,3");
  assert.equal(lines[2], "E0002,2026-08-21 12:01:00,2026-08-21 12:01:00,1,0.3,1,상시,5,0,1");
  assert.ok(csv.endsWith("\r\n"));
});

test("이벤트 CSV: 헤더와 유형 순서", () => {
  const s = createStudy({ mode: "관찰", seed: 1 });
  s.logEvent(T0 - 4000, "세션시작", "", "감도=12dB");
  s.logEvent(T0 - 1000, "보정완료", "", "바닥=40dB");
  bark(s, T0, T0 + 500);
  s.finalize();
  const lines = s.eventsCsv(KST).trimEnd().split("\r\n");
  assert.equal(lines[0], EVENT_HEADER.join(","));
  const types = lines.slice(1).map((l) => l.split(",")[1]);
  assert.deepEqual(types, ["세션시작", "보정완료", "에피소드시작", "배정", "짖음", "에피소드종료"]);
});

test("수식 인젝션 가드: 문자열 =,+,-,@ 는 ' 접두, 숫자는 비가드", () => {
  assert.equal(csvCell("=SUM(A1)"), "'=SUM(A1)");
  assert.equal(csvCell("+82-10"), "'+82-10");
  assert.equal(csvCell("-cmd"), "'-cmd");
  assert.equal(csvCell("@import"), "'@import");
  assert.equal(csvCell(-3.5), "-3.5", "음수 숫자는 가드하면 안 됨 (기획 완료기준)");
  assert.equal(csvCell(0), "0");
  assert.equal(csvCell("안전한 텍스트"), "안전한 텍스트");
  assert.equal(csvCell("a,b"), '"a,b"', "쉼표는 RFC4180 인용");
  assert.equal(csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(csvCell("=a,b"), "\"'=a,b\"", "가드 후 인용");
  assert.throws(() => csvCell(NaN));
});

test("M7: 탭/CR 로 시작하는 셀도 가드된다 (우회 변종)", () => {
  assert.ok(csvCell("\t=1+1").includes("'\t=1+1"), "탭 접두 우회 차단");
  assert.ok(csvCell("\r@cmd").includes("'\r@cmd"), "CR 접두 우회 차단");
});

test("M6: null/undefined 는 빈 칸", () => {
  assert.equal(csvCell(null), "");
  assert.equal(csvCell(undefined), "");
  assert.equal(buildCsv(["a", "b"], [[null, undefined]]), "a,b\r\n,\r\n");
});

test("buildCsv: 행 안의 위험 셀도 가드된다", () => {
  const csv = buildCsv(["a", "b"], [["=1+1", 2]]);
  assert.equal(csv, "a,b\r\n'=1+1,2\r\n");
});

test("formatTimestamp / formatFileStamp: KST 오프셋 손계산", () => {
  assert.equal(formatTimestamp(T0, KST), "2026-08-21 12:00:00");
  assert.equal(formatTimestamp(T0, 0), "2026-08-21 03:00:00");
  assert.equal(formatFileStamp(T0, KST), "20260821_1200");
});

// ---------------------------------------------------------------- 기술통계

test("summarize: 개입/비개입 평균 손계산 (검정은 없음 — statwise 몫)", () => {
  const eps = [
    { startMs: 0, endMs: 10000, barkCount: 4, intervene: true, overlapSec: 10, followWindowBarks: 4 },
    { startMs: 0, endMs: 20000, barkCount: 6, intervene: true, overlapSec: 20, followWindowBarks: 6 },
    { startMs: 0, endMs: 40000, barkCount: 10, intervene: false, overlapSec: 0, followWindowBarks: 10 },
  ];
  const s = summarize(eps);
  assert.equal(s.intervened.n, 2);
  assert.equal(s.intervened.meanDurationSec, 15);
  assert.equal(s.intervened.meanBarks, 5);
  assert.equal(s.intervened.meanFollowWindowBarks, 5);
  assert.equal(s.control.n, 1);
  assert.equal(s.control.meanDurationSec, 40);
  assert.equal(s.contaminatedControls, 0);
  const empty = summarize([]);
  assert.equal(empty.intervened.n, 0);
  assert.equal(empty.intervened.meanDurationSec, null);
  assert.equal(empty.contaminatedControls, 0);
});
