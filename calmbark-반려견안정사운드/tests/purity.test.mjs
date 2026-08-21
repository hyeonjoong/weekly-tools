// tests/purity.test.mjs — 모듈 순수성 + 외부 참조 0 을 소스 수준에서 고정
//
// 기획 완료기준: "네트워크 요청 0 — HTML/JS에 http(s):// 외부 참조(CDN·폰트·
// 이미지·오디오) 전무를 grep 테스트로 고정" — 그 grep 이 바로 이 파일이다.
// 또 순수 모듈(engine/detector/study)이 DOM/WebAudio 를 건드리지 않는 것도
// 소스 검사로 고정한다 (import 그래프가 아니라 소스 문자열 검사 — 우회로가
// 생기면 HARDENING 라운드에서 지적할 것).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

const file = (rel) => new URL("../" + rel, import.meta.url);
const src = (rel) => readFileSync(file(rel), "utf8");

const PURE_MODULES = ["engine.js", "detector.js", "study.js"];
const SHIPPED = ["index.html", "app.js", "engine.js", "detector.js", "study.js"];

test("순수 모듈: DOM/WebAudio/네트워크/전역시계 식별자 전무", () => {
  const banned = /\b(document|window|navigator|AudioContext|webkitAudioContext|fetch|XMLHttpRequest|WebSocket|localStorage|sessionStorage|indexedDB|Worker|importScripts|Date\.now|performance\.now)\b/;
  for (const m of PURE_MODULES) {
    const s = src(m);
    const hit = s.match(banned);
    assert.equal(hit, null, `${m} 에 금지 식별자: ${hit && hit[0]}`);
  }
});

test("순수 모듈: import 문 없음 (자기 완결)", () => {
  for (const m of PURE_MODULES) {
    assert.ok(!/^\s*import\s/m.test(src(m)), `${m} 에 import 문`);
  }
});

test("출하 파일 전부에 http(s):// 외부 참조 0 (CDN·폰트·이미지·오디오·모든 URL)", () => {
  for (const f of SHIPPED) {
    assert.ok(existsSync(file(f)), `${f} 가 존재해야 함`);
    const s = src(f);
    const hit = s.match(/https?:\/\/[^\s"'<>)]*/i);
    assert.equal(hit, null, `${f} 에 URL 참조: ${hit && hit[0]}`);
    assert.ok(!/\/\/cdn\.|url\(\s*\/\//i.test(s), `${f} 에 프로토콜 생략 외부 참조`);
  }
});

test("index.html: 스크립트는 로컬 모듈만, 외부 리소스 태그 없음", () => {
  const s = src("index.html");
  for (const m of s.matchAll(/<script[^>]*\bsrc\s*=\s*["']([^"']+)["']/gi)) {
    assert.match(m[1], /^\.?\/?[\w.-]+\.js$/, `외부 스크립트 의심: ${m[1]}`);
  }
  assert.ok(!/<link[^>]*\bhref\s*=\s*["'](?!#)[^"']*\/\//i.test(s), "외부 링크 태그");
  assert.ok(!/<(img|audio|video|iframe|source)\b[^>]*\bsrc\s*=\s*["'][^"']*\/\//i.test(s), "외부 미디어 태그");
  assert.ok(!/@import|fonts\.googleapis|fontawesome/i.test(s), "외부 폰트/CSS 의심 패턴");
});

test("app.js: 마이크 오디오 데이터 저장·전송·영속화 금지 패턴 없음", () => {
  const s = src("app.js");
  // 녹음 저장(MediaRecorder)·업로드(fetch/XHR/sendBeacon)·소켓 전무 +
  // 라운드 1 M8: 로컬 영속화(localStorage 등)도 금지 — 세션 데이터는 CSV 로만 나간다
  const banned = /\b(MediaRecorder|sendBeacon|XMLHttpRequest|WebSocket|EventSource|fetch|localStorage|sessionStorage|indexedDB)\b/;
  const hit = s.match(banned);
  assert.equal(hit, null, `app.js 에 저장/전송/영속화 API: ${hit && hit[0]}`);
});

// ================================================================ 라운드 1
// 소스 계약(source-contract) 검사 — app.js 는 브라우저 없이는 실행 테스트가
// 불가능하므로, 뮤테이션이 생존했던 안전 상수·가드를 소스 수준에서 고정한다.
// (한계: grep 이므로 의미론적 우회는 못 잡는다 — HARDENING 라운드 0 §C3 참조)

test("C8: 페이드 상수 고정 — FADE_IN ≥ 1 s, FADE_OUT ≥ 0.5 s (뮤턴트 킬)", () => {
  const s = src("app.js");
  const fadeIn = s.match(/const FADE_IN_SEC = ([0-9.]+)/);
  const fadeOut = s.match(/const FADE_OUT_SEC = ([0-9.]+)/);
  assert.ok(fadeIn, "FADE_IN_SEC 선언이 있어야 함");
  assert.ok(fadeOut, "FADE_OUT_SEC 선언이 있어야 함");
  assert.ok(Number(fadeIn[1]) >= 1, `페이드인 ${fadeIn[1]}s < 1 s — Tier 1 Onset Dynamics 위반`);
  assert.ok(Number(fadeOut[1]) >= 0.5, `페이드아웃 ${fadeOut[1]}s < 0.5 s`);
});

test("C8: 볼륨 슬라이더는 volGain 만 만진다 (램프 추월 금지)", () => {
  const s = src("app.js");
  // 페이드(rampGain)와 슬라이더(volGain)가 분리된 직렬 체인이어야 한다
  assert.match(s, /rampGain\)\.connect\(volGain\)/, "rampGain → volGain 직렬 체인");
  const volumeHandler = s.match(/\$\("볼륨"\)\.addEventListener\("input",[\s\S]*?\}\);/);
  assert.ok(volumeHandler, "볼륨 핸들러가 있어야 함");
  const code = volumeHandler[0].replace(/\/\/[^\n]*/g, ""); // 주석 제외 (코드만 검사)
  assert.ok(!/rampGain/.test(code), "볼륨 핸들러가 rampGain 을 만지면 안 됨");
  assert.ok(/volGain/.test(code), "볼륨 핸들러는 volGain 을 조작");
});

test("C1: 재생 연장이 새 에피소드 id 로 재생연장 이벤트를 남긴다 (원장 무결성 — 라운드 2 N1)", () => {
  const s = src("app.js");
  assert.match(s, /logEvent\([^)]*"재생연장",\s*episodeId/,
    "재생 연장 경로는 새 에피소드 id 를 담은 재생연장 이벤트를 기록해야 함");
});

test("C5: 세션 중 미리듣기 차단 + playbackAudible 에 미리듣기 포함", () => {
  const s = src("app.js");
  assert.match(s, /\$\("미리듣기"\)\.disabled = true/, "세션 시작 시 미리듣기 비활성");
  assert.match(s, /\$\("미리듣기"\)\.disabled = false/, "세션 종료 시 재활성");
  const audible = s.match(/function playbackAudible\(\) \{[\s\S]*?\n\}/);
  assert.ok(audible, "playbackAudible 함수");
  assert.ok(/previewNodes/.test(audible[0]), "미리듣기 소리도 가청 재생으로 취급 (방어선)");
  const preview = s.match(/function togglePreview\(\) \{[\s\S]*?\n\}/);
  assert.ok(/if \(sessionActive\) return/.test(preview[0]), "togglePreview 의 세션 가드");
});

test("C6: 마이크 트랙 종료 핸들러 존재", () => {
  const s = src("app.js");
  assert.match(s, /track\.onended/, "track.onended 핸들러");
  assert.match(s, /오류\(마이크종료\)/, "마이크 종료가 원장에 남아야 함");
});

test("M2: getUserMedia await 이전에 재진입 가드가 선다", () => {
  const s = src("app.js");
  const fn = s.match(/async function startSession\(\) \{[\s\S]*?getUserMedia/);
  assert.ok(fn, "startSession 함수");
  assert.ok(/sessionActive = true[\s\S]*getUserMedia/.test(fn[0]),
    "sessionActive=true 가 getUserMedia await 앞에 있어야 함 (더블클릭 스트림 누수)");
});

test("M5: 세션 타임라인은 단조 시계 앵커를 쓴다", () => {
  const s = src("app.js");
  assert.match(s, /performance\.now\(\)/, "performance.now 사용");
  const fn = s.match(/function nowMs\(\) \{[\s\S]*?\n\}/);
  assert.ok(/anchorEpochMs/.test(fn[0]) && /performance\.now/.test(fn[0]),
    "nowMs 가 앵커+단조 시계 조합이어야 함");
});

test("C2: 자동 시드는 VETTED_SEEDS 에서 뽑고, 미검증 시드는 로그된다", () => {
  const s = src("app.js");
  assert.match(s, /VETTED_SEEDS\[Math\.floor\(Math\.random\(\) \* VETTED_SEEDS\.length\)\]/,
    "자동 시드 = 검증 목록에서 선택");
  assert.match(s, /시드\(미검증\)/, "목록 밖 시드는 `시드(미검증)` 으로 기록");
});
