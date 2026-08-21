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
  assert.equal(eps[0].id, "E001");
  assert.equal(eps[0].barkCount, 2);
  assert.equal(eps[0].startMs, T0);
  assert.equal(eps[0].endMs, T0 + 10500);      // 지속 10.5초
  assert.equal(eps[1].id, "E002");
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
  assert.equal(closed.id, "E001");
  assert.equal(s.getOpenEpisode(), null);
  assert.equal(s.getEpisodes()[0].endMs, T0 + 500, "종료 시각 = 마지막 짖음 종료");
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
  assert.equal(assign[0].episodeId, "E001");
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

// ---------------------------------------------------------------- CSV

test("에피소드 CSV: 헤더·행 수·내용 손계산 대조", () => {
  const s = createStudy({ mode: "상시", seed: 5 });
  bark(s, T0, T0 + 500);
  bark(s, T0 + 10000, T0 + 10500);
  bark(s, T0 + 60000, T0 + 60250);
  s.finalize();
  const csv = s.episodesCsv(KST);
  const lines = csv.trimEnd().split("\r\n");
  assert.equal(lines.length, 3, "헤더 + 에피소드 2");
  assert.equal(lines[0], EPISODE_HEADER.join(","));
  assert.equal(lines[1], "E001,2026-08-21 12:00:00,2026-08-21 12:00:10,2,10.5,1,상시,5");
  assert.equal(lines[2], "E002,2026-08-21 12:01:00,2026-08-21 12:01:00,1,0.3,1,상시,5");
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
    { startMs: 0, endMs: 10000, barkCount: 4, intervene: true },
    { startMs: 0, endMs: 20000, barkCount: 6, intervene: true },
    { startMs: 0, endMs: 40000, barkCount: 10, intervene: false },
  ];
  const s = summarize(eps);
  assert.equal(s.intervened.n, 2);
  assert.equal(s.intervened.meanDurationSec, 15);
  assert.equal(s.intervened.meanBarks, 5);
  assert.equal(s.control.n, 1);
  assert.equal(s.control.meanDurationSec, 40);
  const empty = summarize([]);
  assert.equal(empty.intervened.n, 0);
  assert.equal(empty.intervened.meanDurationSec, null);
});
