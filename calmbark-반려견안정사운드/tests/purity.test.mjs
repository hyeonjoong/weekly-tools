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

test("app.js: 마이크 오디오 데이터 저장·전송 금지 패턴 없음", () => {
  const s = src("app.js");
  // 녹음 저장(MediaRecorder)·업로드(fetch/XHR/sendBeacon)·소켓 전무
  const banned = /\b(MediaRecorder|sendBeacon|XMLHttpRequest|WebSocket|EventSource|fetch)\b/;
  const hit = s.match(banned);
  assert.equal(hit, null, `app.js 에 저장/전송 API: ${hit && hit[0]}`);
});
