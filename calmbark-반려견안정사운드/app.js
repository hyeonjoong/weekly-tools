// app.js — 브라우저 접착층 (DOM + WebAudio + 마이크)
//
// 순수 로직은 전부 engine.js / detector.js / study.js 에 있다. 이 파일은
// 그 모듈들을 마이크·스피커·화면에 연결만 한다. 네트워크 요청은 없다.
// 마이크 스트림은 저장·전송되지 않는다 — 특징값(대역 에너지 dB)만 계산 후 버린다.

import { PRESETS, renderPreset, DEFAULT_LOOP_SECONDS } from "./engine.js";
import { createDetector } from "./detector.js";
import { createStudy, summarize, formatTimestamp, formatFileStamp } from "./study.js";

const $ = (id) => document.getElementById(id);
const tzOffsetMin = -new Date().getTimezoneOffset();

// ---------------------------------------------------------------- 상태

let audioCtx = null;
let micStream = null;
let analyser = null;
let freqData = null;
let detector = null;
let study = null;
let frameTimer = null; // setInterval 사용 — rAF 는 백그라운드 탭에서 완전 정지되지만
                       // 타이머는 스로틀만 되므로(≈1 Hz) 감지가 최소한 유지된다
let sessionActive = false;
let sessionStartMs = null;

// 재생 상태
let playback = { source: null, gain: null, active: false, endAt: 0, episodeId: "" };
let previewNodes = null;
const bufferCache = new Map(); // key: preset|sr|seed → AudioBuffer

const FADE_IN_SEC = 1.5;   // 기획: 개입 페이드인 ≥ 1 s (Tier 1 Onset Dynamics)
const FADE_OUT_SEC = 2.0;
const MAX_GAIN = 0.5;      // 상한 — UI 슬라이더도 이 위로 못 올라간다

// ---------------------------------------------------------------- 유틸

function nowMs() { return Date.now(); }

function uiLog(text) {
  const box = $("이벤트로그");
  const line = document.createElement("div");
  line.textContent = `[${formatTimestamp(nowMs(), tzOffsetMin).slice(11)}] ${text}`;
  box.prepend(line);
  while (box.childNodes.length > 60) box.removeChild(box.lastChild);
}

function currentSeed() {
  const v = parseInt($("시드").value, 10);
  return Number.isFinite(v) ? (v >>> 0) : 1;
}

function getAudioCtx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function getLoopBuffer(presetId, seed) {
  const ctx = getAudioCtx();
  const key = `${presetId}|${ctx.sampleRate}|${seed}`;
  if (!bufferCache.has(key)) {
    const pcm = renderPreset(presetId, ctx.sampleRate, DEFAULT_LOOP_SECONDS, seed);
    const buf = ctx.createBuffer(1, pcm.length, ctx.sampleRate);
    buf.copyToChannel(pcm, 0);
    bufferCache.set(key, buf);
  }
  return bufferCache.get(key);
}

// ---------------------------------------------------------------- 재생

function startPlayback(episodeId) {
  const ctx = getAudioCtx();
  stopPreview();
  if (playback.active) { // 이미 재생 중 — 연장만
    playback.endAt = nowMs() + Number($("재생길이").value) * 1000;
    return;
  }
  const source = ctx.createBufferSource();
  source.buffer = getLoopBuffer($("프리셋").value, currentSeed());
  source.loop = true;
  const gain = ctx.createGain();
  const target = Math.min(Number($("볼륨").value), MAX_GAIN);
  gain.gain.setValueAtTime(0.0001, ctx.currentTime);
  gain.gain.linearRampToValueAtTime(target, ctx.currentTime + FADE_IN_SEC);
  source.connect(gain).connect(ctx.destination);
  source.start();
  playback = {
    source, gain, active: true,
    endAt: nowMs() + Number($("재생길이").value) * 1000,
    episodeId,
  };
  study.logEvent(nowMs(), "재생시작", episodeId,
    `프리셋=${$("프리셋").value};게인=${target};페이드인=${FADE_IN_SEC}s`);
  uiLog(`개입 재생 시작 (${PRESETS[$("프리셋").value].label})`);
  updateStatus();
}

function stopPlayback(reason) {
  if (!playback.active) return;
  const ctx = getAudioCtx();
  const { source, gain, episodeId } = playback;
  gain.gain.cancelScheduledValues(ctx.currentTime);
  gain.gain.setValueAtTime(gain.gain.value, ctx.currentTime);
  gain.gain.linearRampToValueAtTime(0.0001, ctx.currentTime + FADE_OUT_SEC);
  source.stop(ctx.currentTime + FADE_OUT_SEC + 0.05);
  // 페이드아웃 동안에도 스피커에서 소리가 나므로 playbackActive 는 종료 후 해제
  const doneAt = nowMs() + FADE_OUT_SEC * 1000 + 100;
  const ep = episodeId;
  playback = { source: null, gain: null, active: false, endAt: 0, episodeId: "", fadingUntil: doneAt };
  setTimeout(() => { playback.fadingUntil = 0; updateStatus(); }, FADE_OUT_SEC * 1000 + 150);
  if (study) {
    study.logEvent(nowMs(), "재생종료", ep, `사유=${reason};페이드아웃=${FADE_OUT_SEC}s`);
  }
  uiLog(`재생 종료 (${reason})`);
  updateStatus();
}

function playbackAudible() {
  return playback.active || (playback.fadingUntil && nowMs() < playback.fadingUntil);
}

// 미리듣기 (세션 밖에서도 동작 — 버튼 클릭이 곧 사용자 제스처)
function togglePreview() {
  if (previewNodes) { stopPreview(); return; }
  const ctx = getAudioCtx();
  const source = ctx.createBufferSource();
  source.buffer = getLoopBuffer($("프리셋").value, currentSeed());
  source.loop = true;
  const gain = ctx.createGain();
  const target = Math.min(Number($("볼륨").value), MAX_GAIN);
  gain.gain.setValueAtTime(0.0001, ctx.currentTime);
  gain.gain.linearRampToValueAtTime(target, ctx.currentTime + FADE_IN_SEC);
  source.connect(gain).connect(ctx.destination);
  source.start();
  previewNodes = { source, gain };
  $("미리듣기").textContent = "미리듣기 정지";
}

function stopPreview() {
  if (!previewNodes) return;
  const ctx = getAudioCtx();
  previewNodes.gain.gain.cancelScheduledValues(ctx.currentTime);
  previewNodes.gain.gain.setValueAtTime(previewNodes.gain.gain.value, ctx.currentTime);
  previewNodes.gain.gain.linearRampToValueAtTime(0.0001, ctx.currentTime + 0.3);
  previewNodes.source.stop(ctx.currentTime + 0.4);
  previewNodes = null;
  $("미리듣기").textContent = "미리듣기";
}

// ---------------------------------------------------------------- 특징 추출

function bandDbFromAnalyser() {
  analyser.getFloatFrequencyData(freqData);
  const ctx = audioCtx;
  const binHz = ctx.sampleRate / analyser.fftSize;
  const lo = Math.max(1, Math.round(250 / binHz));
  const hi = Math.min(freqData.length - 1, Math.round(4000 / binHz));
  let power = 0;
  for (let k = lo; k <= hi; k++) {
    power += Math.pow(10, freqData[k] / 10); // dB → 선형 파워
  }
  return 10 * Math.log10(Math.max(power, 1e-12));
}

// ---------------------------------------------------------------- 메인 루프

function frameLoop() {
  if (!sessionActive) return;
  const t = nowMs();
  const bandDb = bandDbFromAnalyser();
  const playbackActive = playbackAudible();

  const events = detector.processFrame({ tMs: t, bandDb, playbackActive });
  for (const ev of events) {
    if (ev.type === "calibrated") {
      study.logEvent(t, "보정완료", "", `바닥=${ev.floorDb.toFixed(1)}dB`);
      uiLog(`보정 완료 — 소음 바닥 ${ev.floorDb.toFixed(1)} dB. 감시를 시작합니다.`);
    } else if (ev.type === "bark") {
      const r = study.onBark({
        tMs: ev.tMs, endMs: ev.endMs, peakDb: ev.peakDb, duringPlayback: ev.duringPlayback,
      });
      uiLog(
        (ev.duringPlayback ? "짖음 감지(재생 중) " : "짖음 감지 ") +
        `— ${r.episode.id}, 피크 ${ev.peakDb.toFixed(1)} dB`
      );
      if (r.episode && r.intervene) {
        startPlayback(r.episode.id); // 새 에피소드면 시작, 재생 중이면 연장
      }
      renderEpisodes();
    }
  }

  const closed = study.tick(t);
  if (closed) {
    uiLog(`에피소드 ${closed.id} 종료 (짖음 ${closed.barkCount}회)`);
    renderEpisodes();
  }

  if (playback.active && t >= playback.endAt) stopPlayback("재생시간종료");

  // 레벨 미터 + 상태
  const floor = detector.getNoiseFloor();
  const th = detector.getThreshold(playbackActive);
  $("레벨값").textContent = bandDb.toFixed(1) + " dB";
  $("바닥값").textContent = floor === null ? "보정 중…" : floor.toFixed(1) + " dB";
  $("문턱값").textContent = th === null ? "—" : th.toFixed(1) + " dB" + (playbackActive ? " (재생 중 +6)" : "");
  const pct = Math.max(0, Math.min(100, (bandDb + 100) * 1.25)); // −100..−20 dB → 0..100
  $("레벨바").style.width = pct + "%";
  $("레벨바").style.background = th !== null && bandDb > th ? "#c0392b" : "#2e7d32";
  updateStatus();
}

function updateStatus() {
  const el = $("상태값");
  if (!sessionActive) { el.textContent = "대기 (세션 시작 전)"; return; }
  if (detector.getState() === "CALIBRATING") { el.textContent = "① 보정 중 — 3초간 조용히 해주세요"; return; }
  el.textContent = playbackAudible() ? "개입 재생 중 (감지 계속, 문턱 +6 dB)" : "감시 중";
}

// ---------------------------------------------------------------- 에피소드 표 + 요약

function renderEpisodes() {
  const eps = study.getEpisodes();
  const open = study.getOpenEpisode();
  const tbody = $("에피소드표");
  tbody.innerHTML = "";
  const all = open ? [...eps, { ...open, openFlag: true }] : eps;
  for (const e of all) {
    const tr = document.createElement("tr");
    const dur = ((e.endMs - e.startMs) / 1000).toFixed(1);
    tr.innerHTML =
      `<td>${e.id}${e.openFlag ? " (진행중)" : ""}</td>` +
      `<td>${formatTimestamp(e.startMs, tzOffsetMin).slice(11)}</td>` +
      `<td>${e.barkCount}</td><td>${dur}</td>` +
      `<td>${e.intervene ? "개입" : "비개입"}</td>`;
    tbody.appendChild(tr);
  }
  // 요약
  const s = summarize(eps);
  const hours = sessionStartMs ? Math.max((nowMs() - sessionStartMs) / 3600000, 1 / 3600) : null;
  $("요약내용").innerHTML =
    `<p>에피소드 ${eps.length}건` +
    (hours ? ` · 시간당 ${(eps.length / hours).toFixed(1)}건` : "") + `</p>` +
    `<table class="mini"><tr><th></th><th>n</th><th>평균 지속(초)</th><th>평균 짖음(회)</th></tr>` +
    `<tr><td>개입</td><td>${s.intervened.n}</td><td>${s.intervened.meanDurationSec ?? "—"}</td><td>${s.intervened.meanBarks ?? "—"}</td></tr>` +
    `<tr><td>비개입</td><td>${s.control.n}</td><td>${s.control.meanDurationSec ?? "—"}</td><td>${s.control.meanBarks ?? "—"}</td></tr></table>` +
    `<p class="주의">기술통계까지만 보여줍니다. 표본이 작으면 차이는 우연일 수 있습니다 — ` +
    `판단은 <b>statwise</b>(그룹 비교)·<b>longistat</b>(일차 추이)로 검정하세요.</p>`;
}

// ---------------------------------------------------------------- 세션 시작/종료

async function startSession() {
  if (sessionActive) return;
  $("마이크안내").hidden = true;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false, // 스피커 소리를 "지워주는" 처리가 감지 특성을 흔들지 않게 끔
        noiseSuppression: false,
        autoGainControl: false,
      },
    });
  } catch (err) {
    $("마이크안내").hidden = false;
    uiLog("마이크 권한이 거부되어 세션을 시작할 수 없습니다.");
    return;
  }
  micStream = stream;
  const ctx = getAudioCtx();
  const src = ctx.createMediaStreamSource(stream);
  analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.3;
  freqData = new Float32Array(analyser.frequencyBinCount);
  src.connect(analyser); // destination 에는 연결하지 않는다 — 마이크 소리 재생 안 함

  detector = createDetector({ sensitivityDb: Number($("감도").value) });
  study = createStudy({ mode: $("모드").value, seed: currentSeed() });
  sessionActive = true;
  sessionStartMs = nowMs();
  study.logEvent(sessionStartMs, "세션시작", "",
    `모드=${$("모드").value};seed=${currentSeed()};감도=${$("감도").value}dB;` +
    `프리셋=${$("프리셋").value};재생길이=${$("재생길이").value}s;게인=${$("볼륨").value}`);
  uiLog("세션 시작 — 3초간 조용히 해주세요 (소음 바닥 보정).");
  $("세션시작").disabled = true;
  $("세션종료").disabled = false;
  ["모드", "시드", "프리셋"].forEach((id) => ($(id).disabled = true));
  updateStatus();
  renderEpisodes();
  frameTimer = setInterval(frameLoop, 40);
}

function endSession() {
  if (!sessionActive) return;
  stopPlayback("세션종료");
  study.finalize();
  study.logEvent(nowMs(), "세션종료", "", "");
  sessionActive = false;
  clearInterval(frameTimer);
  if (micStream) micStream.getTracks().forEach((tr) => tr.stop());
  micStream = null;
  $("세션시작").disabled = false;
  $("세션종료").disabled = true;
  ["모드", "시드", "프리셋"].forEach((id) => ($(id).disabled = false));
  uiLog("세션 종료 — CSV를 내려받아 statwise/longistat 에 넣으세요.");
  updateStatus();
  renderEpisodes();
}

// ---------------------------------------------------------------- CSV 다운로드

function downloadCsv(kind) {
  if (!study) return;
  const stamp = formatFileStamp(sessionStartMs ?? nowMs(), tzOffsetMin);
  const text = kind === "이벤트" ? study.eventsCsv(tzOffsetMin) : study.episodesCsv(tzOffsetMin);
  const blob = new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }); // BOM — 엑셀 한글 호환
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `calmbark_${kind}_${stamp}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---------------------------------------------------------------- 초기화

function init() {
  // 프리셋 목록
  const sel = $("프리셋");
  for (const [id, p] of Object.entries(PRESETS)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = `${p.label} — ${p.description}`;
    sel.appendChild(opt);
  }
  // 무작위 기본 시드 (재현 가능하도록 화면에 노출·수정 가능)
  $("시드").value = String(Math.floor(Math.random() * 1e9));

  $("세션시작").addEventListener("click", startSession);
  $("세션종료").addEventListener("click", endSession);
  $("미리듣기").addEventListener("click", togglePreview);
  $("이벤트CSV").addEventListener("click", () => downloadCsv("이벤트"));
  $("에피소드CSV").addEventListener("click", () => downloadCsv("에피소드"));
  $("감도").addEventListener("input", () => {
    $("감도값").textContent = $("감도").value + " dB";
    if (detector) detector.setSensitivityDb(Number($("감도").value));
  });
  $("볼륨").addEventListener("input", () => {
    $("볼륨값").textContent = Math.round(Number($("볼륨").value) * 100) + "%";
    const ctx = audioCtx;
    const target = Math.min(Number($("볼륨").value), MAX_GAIN);
    if (ctx && playback.active) playback.gain.gain.setTargetAtTime(target, ctx.currentTime, 0.2);
    if (ctx && previewNodes) previewNodes.gain.gain.setTargetAtTime(target, ctx.currentTime, 0.2);
  });
  $("재생길이").addEventListener("input", () => {
    $("재생길이값").textContent = $("재생길이").value + "초";
  });
  $("모드").addEventListener("change", () => {
    $("모드설명").textContent = {
      "관찰": "감지·기록만 합니다. 소리는 재생되지 않습니다 (기저선 수집).",
      "상시": "모든 에피소드에 개입합니다 (효과 비교 불가 — 적응 확인용).",
      "무작위": "에피소드 단위 50% 무작위 개입 — seed 로 재현 가능. N-of-1 비교용.",
    }[$("모드").value];
  });
  updateStatus();
}

document.addEventListener("DOMContentLoaded", init);
