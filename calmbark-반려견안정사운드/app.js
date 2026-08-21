// app.js — 브라우저 접착층 (DOM + WebAudio + 마이크)
//
// 순수 로직은 전부 engine.js / detector.js / study.js 에 있다. 이 파일은
// 그 모듈들을 마이크·스피커·화면에 연결만 한다. 네트워크 요청은 없다.
// 마이크 스트림은 저장·전송되지 않는다 — 특징값(대역 에너지 dB)만 계산 후 버린다.

import { PRESETS, VETTED_SEEDS, renderPreset, DEFAULT_LOOP_SECONDS } from "./engine.js";
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
let calWatchdog = null;      // 라운드 1 C4: 보정이 영원히 안 끝나는 경우(전 프레임 무효) 감시
let csvDownloaded = true;    // 라운드 1 M12: 이전 세션 CSV 미다운로드 경고용

// 라운드 1 M5: 세션 타임라인은 단조 시계(performance.now)를 벽시계 기준점에
// 앵커해 쓴다 — 세션 중 OS 시계 변경(NTP 보정·수동 변경)이 음수 지속시간을
// 만들 수 없다. 세션 밖에서는 벽시계를 그대로 쓴다.
let anchorEpochMs = null;
let anchorPerfMs = null;
function nowMs() {
  if (anchorEpochMs !== null) return anchorEpochMs + (performance.now() - anchorPerfMs);
  return Date.now();
}

// 재생 상태 (라운드 1 C8: rampGain(페이드 전용) × volGain(슬라이더 전용) 분리 —
// 페이드인 중 볼륨 슬라이더 조작이 램프를 추월해 급출발하는 것을 구조적으로 차단)
let playback = { source: null, rampGain: null, volGain: null, active: false, endAt: 0, episodeId: "", fadingUntil: 0 };
let previewNodes = null;
const bufferCache = new Map(); // key: preset|sr|seed → AudioBuffer

const FADE_IN_SEC = 1.5;   // 기획: 개입 페이드인 ≥ 1 s (Tier 1 Onset Dynamics)
                           // — tests/purity.test.mjs 가 소스에서 ≥1 을 강제 (라운드 1 C8)
const FADE_OUT_SEC = 2.0;  // — 소스 검사로 ≥0.5 강제
const MAX_GAIN = 0.5;      // 상한 — UI 슬라이더도 이 위로 못 올라간다

// ---------------------------------------------------------------- 유틸

function uiLog(text) {
  const box = $("이벤트로그");
  const line = document.createElement("div");
  line.textContent = `[${formatTimestamp(nowMs(), tzOffsetMin).slice(11)}] ${text}`;
  box.prepend(line);
  while (box.childNodes.length > 60) box.removeChild(box.lastChild);
}

function showError(text) {
  const el = $("오류안내");
  el.textContent = text;
  el.hidden = false;
}

function currentSeed() {
  const v = parseInt($("시드").value, 10);
  return Number.isFinite(v) ? (v >>> 0) : 1;
}

function sliderVolume() {
  return Math.min(Number($("볼륨").value), MAX_GAIN);
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

/** source → rampGain(페이드) → volGain(슬라이더) → destination 체인 생성. */
function makeChain(presetId, seed) {
  const ctx = getAudioCtx();
  const source = ctx.createBufferSource();
  source.buffer = getLoopBuffer(presetId, seed);
  source.loop = true;
  const rampGain = ctx.createGain();
  rampGain.gain.setValueAtTime(0.0001, ctx.currentTime);
  rampGain.gain.linearRampToValueAtTime(1, ctx.currentTime + FADE_IN_SEC);
  const volGain = ctx.createGain();
  volGain.gain.setValueAtTime(sliderVolume(), ctx.currentTime);
  source.connect(rampGain).connect(volGain).connect(ctx.destination);
  return { source, rampGain, volGain };
}

// ---------------------------------------------------------------- 재생

function startPlayback(episodeId) {
  stopPreview();
  const t = nowMs();
  if (playback.active) { // 이미 재생 중 — 연장
    playback.endAt = t + Number($("재생길이").value) * 1000;
    if (playback.episodeId !== episodeId) {
      // 라운드 1 C1(c): 새 에피소드가 기존 재생을 연장하면 원장에 남긴다 —
      // 이전에는 연장이 무기록이라 재생종료가 옛 에피소드에 귀속됐다 (P1-8/P2-1)
      study.logEvent(t, "재생연장", episodeId, `기존=${playback.episodeId}`);
      playback.episodeId = episodeId;
    }
    return;
  }
  const { source, rampGain, volGain } = makeChain($("프리셋").value, currentSeed());
  source.start();
  playback = {
    source, rampGain, volGain, active: true,
    endAt: t + Number($("재생길이").value) * 1000,
    episodeId, fadingUntil: 0,
  };
  study.playbackStarted(t); // C1: 가청 구간 기록 (페이드인 시작부터)
  study.logEvent(t, "재생시작", episodeId,
    `프리셋=${$("프리셋").value};게인=${sliderVolume()};페이드인=${FADE_IN_SEC}s`);
  uiLog(`개입 재생 시작 (${PRESETS[$("프리셋").value].label})`);
  updateStatus();
}

function stopPlayback(reason) {
  if (!playback.active) return;
  const ctx = getAudioCtx();
  const { source, rampGain, episodeId } = playback;
  rampGain.gain.cancelScheduledValues(ctx.currentTime);
  rampGain.gain.setValueAtTime(rampGain.gain.value, ctx.currentTime);
  rampGain.gain.linearRampToValueAtTime(0.0001, ctx.currentTime + FADE_OUT_SEC);
  source.stop(ctx.currentTime + FADE_OUT_SEC + 0.05);
  // 페이드아웃 동안에도 스피커에서 소리가 나므로 playbackActive 는 종료 후 해제
  const t = nowMs();
  const audibleEnd = t + FADE_OUT_SEC * 1000 + 100;
  playback = { source: null, rampGain: null, volGain: null, active: false, endAt: 0, episodeId: "", fadingUntil: audibleEnd };
  setTimeout(() => { playback.fadingUntil = 0; updateStatus(); }, FADE_OUT_SEC * 1000 + 150);
  if (study) {
    study.playbackEnded(audibleEnd); // C1: 가청 구간 종료 = 페이드아웃 끝
    study.logEvent(t, "재생종료", episodeId, `사유=${reason};페이드아웃=${FADE_OUT_SEC}s`);
  }
  uiLog(`재생 종료 (${reason})`);
  updateStatus();
}

function playbackAudible() {
  // 라운드 1 C5: 미리듣기도 스피커 소리다 — 세션 중이라면(방어선일 뿐,
  // 미리듣기 버튼 자체가 세션 중 비활성) 감지기에 재생 중으로 알린다
  return playback.active ||
    (playback.fadingUntil !== 0 && nowMs() < playback.fadingUntil) ||
    previewNodes !== null;
}

// 미리듣기 — 세션 밖 전용 (라운드 1 C5: 세션 중에는 버튼이 비활성화된다.
// 보정·감지 중 미리듣기는 소음 바닥을 오염시키고 가짜 에피소드를 만든다)
function togglePreview() {
  if (previewNodes) { stopPreview(); return; }
  if (sessionActive) return; // 방어선 — 버튼 비활성이 우회돼도 동작 금지
  const { source, rampGain, volGain } = makeChain($("프리셋").value, currentSeed());
  source.start();
  previewNodes = { source, rampGain, volGain };
  $("미리듣기").textContent = "미리듣기 정지";
}

function stopPreview() {
  if (!previewNodes) return;
  const ctx = getAudioCtx();
  previewNodes.rampGain.gain.cancelScheduledValues(ctx.currentTime);
  previewNodes.rampGain.gain.setValueAtTime(previewNodes.rampGain.gain.value, ctx.currentTime);
  previewNodes.rampGain.gain.linearRampToValueAtTime(0.0001, ctx.currentTime + 0.3);
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
      clearTimeout(calWatchdog);
      study.logEvent(t, "보정완료", "", `바닥=${ev.floorDb.toFixed(1)}dB`);
      uiLog(`보정 완료 — 소음 바닥 ${ev.floorDb.toFixed(1)} dB. 감시를 시작합니다.`);
    } else if (ev.type === "calibration_failed") {
      // 라운드 1 C4: 음소거/무입력 마이크 — 이대로 진행하면 유령 짖음 폭풍
      clearTimeout(calWatchdog);
      study.logEvent(t, "오류(보정실패)", "", `바닥=${ev.floorDb === null ? "무효" : ev.floorDb.toFixed(1) + "dB"}`);
      showError("마이크가 음소거이거나 입력이 없습니다 — 마이크(입력 장치·음소거 스위치·입력 레벨)를 확인한 뒤 '세션 시작'으로 다시 보정하세요.");
      uiLog("보정 실패 — 마이크 입력이 없어 세션을 중단합니다.");
      endSession();
      return;
    } else if (ev.type === "framegap") {
      // 라운드 1 C7: 절전·탭 정지 공백 — 진행 버스트는 감지기가 이미 폐기했다
      study.logEvent(t, `갭(${ev.gapSec}초)`, "", "프레임 공백 — 진행 중 버스트 폐기");
      uiLog(`프레임 공백 ${ev.gapSec}초 감지 (절전/백그라운드?) — 구간 기록을 신뢰하지 마세요.`);
    } else if (ev.type === "bark") {
      const r = study.onBark({
        tMs: ev.tMs, endMs: ev.endMs, peakDb: ev.peakDb, duringPlayback: ev.duringPlayback,
      });
      uiLog(
        (ev.duringPlayback ? "짖음 감지(재생 중) " : "짖음 감지 ") +
        `— ${r.episode.id}, 피크 ${ev.peakDb.toFixed(1)} dB`
      );
      if (r.episode && r.intervene) {
        startPlayback(r.episode.id); // 새 에피소드면 시작, 재생 중이면 연장(원장 기록)
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
  if (detector && detector.getState() === "CALIBRATING") { el.textContent = "① 보정 중 — 3초간 조용히 해주세요"; return; }
  el.textContent = playbackAudible() ? "개입 재생 중 (감지 계속, 문턱 +6 dB)" : "감시 중";
}

// ---------------------------------------------------------------- 에피소드 표 + 요약

function renderEpisodes() {
  const eps = study.getEpisodeMetrics();
  const open = study.getOpenEpisode();
  const tbody = $("에피소드표");
  tbody.innerHTML = "";
  const all = open ? [...eps, { ...open, openFlag: true }] : eps;
  for (const e of all) {
    const tr = document.createElement("tr");
    const dur = ((e.endMs - e.startMs) / 1000).toFixed(1);
    const contaminated = !e.intervene && typeof e.overlapSec === "number" && e.overlapSec > 0;
    tr.innerHTML =
      `<td>${e.id}${e.openFlag ? " (진행중)" : ""}</td>` +
      `<td>${formatTimestamp(e.startMs, tzOffsetMin).slice(11)}</td>` +
      `<td>${e.barkCount}</td><td>${dur}</td>` +
      `<td>${e.intervene ? "개입" : "비개입"}${contaminated ? " ⚠오염" : ""}</td>`;
    tbody.appendChild(tr);
  }
  // 요약 — 1차 지표는 고정 180초 창 짖음수 (라운드 1 C1: 두 군에 동일한 창)
  const s = summarize(eps);
  const hours = sessionStartMs ? Math.max((nowMs() - sessionStartMs) / 3600000, 1 / 3600) : null;
  const stamp = formatFileStamp(sessionStartMs ?? nowMs(), tzOffsetMin);
  $("요약내용").innerHTML =
    `<p>에피소드 ${eps.length}건` +
    (hours ? ` · 시간당 ${(eps.length / hours).toFixed(1)}건` : "") + `</p>` +
    `<table class="mini"><tr><th></th><th>n</th><th>후속180초 짖음(회)*</th><th>평균 지속(초)</th><th>평균 짖음(회)</th></tr>` +
    `<tr><td>개입</td><td>${s.intervened.n}</td><td>${s.intervened.meanFollowWindowBarks ?? "—"}</td><td>${s.intervened.meanDurationSec ?? "—"}</td><td>${s.intervened.meanBarks ?? "—"}</td></tr>` +
    `<tr><td>비개입</td><td>${s.control.n}</td><td>${s.control.meanFollowWindowBarks ?? "—"}</td><td>${s.control.meanDurationSec ?? "—"}</td><td>${s.control.meanBarks ?? "—"}</td></tr></table>` +
    `<p class="주의">* 1차 비교 지표 — 에피소드 시작 후 180초 고정 창의 짖음 수(두 군 동일 창이라 재생 길이 편향 없음).</p>` +
    (s.contaminatedControls > 0
      ? `<p class="주의">⚠ 비개입 에피소드 ${s.contaminatedControls}건이 재생과 겹쳤습니다(오염 — 재생겹침초&gt;0). per-protocol 비교에서는 제외하세요.</p>`
      : "") +
    `<p class="주의">기술통계까지만 보여줍니다. 표본이 작으면 차이는 우연일 수 있습니다 — 검정 명령:</p>` +
    `<code>statwise calmbark_에피소드_${stamp}.csv --value 후속180초짖음수 --group 개입여부</code>`;
}

// ---------------------------------------------------------------- 세션 시작/종료

async function startSession() {
  if (sessionActive) return;
  // 라운드 1 M12: 이전 세션의 CSV 를 안 내려받았으면 확인
  if (study && study.getEpisodes().length > 0 && !csvDownloaded) {
    const ok = window.confirm(
      "이전 세션의 CSV를 아직 내려받지 않았습니다. 새 세션을 시작하면 이전 기록이 사라집니다. 계속할까요?"
    );
    if (!ok) return;
  }
  // 라운드 1 M2: 재진입 가드를 await 앞에 — 더블클릭이 마이크 스트림을 누수시키지 않게
  sessionActive = true;
  $("세션시작").disabled = true;
  $("마이크안내").hidden = true;
  $("오류안내").hidden = true;
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
    sessionActive = false;
    $("세션시작").disabled = false;
    $("마이크안내").hidden = false;
    uiLog("마이크 권한이 거부되어 세션을 시작할 수 없습니다.");
    updateStatus();
    return;
  }
  micStream = stream;
  // 라운드 1 C6: 장치 뽑힘/권한 회수로 트랙이 죽으면 조용히 "감시 중" 으로
  // 남지 않고 세션을 중단·기록·안내한다
  for (const track of stream.getTracks()) {
    track.onended = () => {
      if (!sessionActive) return; // 정상 종료(우리가 stop 한 것)는 무시
      study.logEvent(nowMs(), "오류(마이크종료)", "", "장치 분리/권한 회수 추정");
      showError("마이크 입력이 끊겼습니다(장치 분리 또는 권한 회수). 세션을 종료했습니다 — 마이크 확인 후 다시 시작하세요.");
      uiLog("마이크 트랙 종료 — 세션을 중단합니다.");
      endSession();
    };
  }
  const ctx = getAudioCtx();
  const src = ctx.createMediaStreamSource(stream);
  analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.3;
  freqData = new Float32Array(analyser.frequencyBinCount);
  src.connect(analyser); // destination 에는 연결하지 않는다 — 마이크 소리 재생 안 함

  stopPreview();
  $("미리듣기").disabled = true; // 라운드 1 C5: 세션 중 미리듣기 금지

  // 라운드 1 M5: 단조 타임라인 앵커
  anchorEpochMs = Date.now();
  anchorPerfMs = performance.now();

  const seed = currentSeed();
  detector = createDetector({ sensitivityDb: Number($("감도").value) });
  study = createStudy({ mode: $("모드").value, seed });
  csvDownloaded = false;
  sessionStartMs = nowMs();
  study.logEvent(sessionStartMs, "세션시작", "",
    `모드=${$("모드").value};seed=${seed};감도=${$("감도").value}dB;` +
    `프리셋=${$("프리셋").value};재생길이=${$("재생길이").value}s;게인=${$("볼륨").value}`);
  // 라운드 1 C2(b): 검증 시드 목록 밖 시드는 정직하게 표시
  if (!VETTED_SEEDS.includes(seed)) {
    study.logEvent(sessionStartMs, "시드(미검증)", "",
      `seed=${seed} — 검증 목록 밖: 논문 준수 스위프가 이 시드의 합성음을 보장하지 않음`);
    uiLog(`주의: 직접 입력한 시드 ${seed} 는 검증 목록 밖입니다 (로그에 기록됨).`);
  }
  uiLog("세션 시작 — 3초간 조용히 해주세요 (소음 바닥 보정).");
  // 라운드 1 C4 감시견: 8초가 지나도 보정이 안 끝나면(전 프레임 무효 등) 중단
  calWatchdog = setTimeout(() => {
    if (sessionActive && detector.getState() === "CALIBRATING") {
      study.logEvent(nowMs(), "오류(보정실패)", "", "보정 시간 초과 — 유효 프레임 없음 추정");
      showError("마이크가 음소거이거나 입력이 없습니다 — 확인 후 '세션 시작'으로 다시 보정하세요.");
      uiLog("보정 시간 초과 — 세션을 중단합니다.");
      endSession();
    }
  }, 8000);
  $("세션종료").disabled = false;
  ["모드", "시드", "프리셋"].forEach((id) => ($(id).disabled = true));
  updateStatus();
  renderEpisodes();
  frameTimer = setInterval(frameLoop, 40);
}

function endSession() {
  if (!sessionActive) return;
  sessionActive = false; // C6: 트랙 stop() 이 부르는 onended 재진입 차단
  clearTimeout(calWatchdog);
  stopPlayback("세션종료");
  study.finalize();
  study.logEvent(nowMs(), "세션종료", "", "");
  clearInterval(frameTimer);
  if (micStream) micStream.getTracks().forEach((tr) => tr.stop());
  micStream = null;
  $("세션시작").disabled = false;
  $("세션종료").disabled = true;
  $("미리듣기").disabled = false;
  ["모드", "시드", "프리셋"].forEach((id) => ($(id).disabled = false));
  uiLog("세션 종료 — CSV를 내려받아 statwise 로 검정하세요.");
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
  csvDownloaded = true; // M12
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
  // 라운드 1 C2(b): 자동 시드는 검증 목록에서만 뽑는다 (직접 수정은 허용 —
  // 그 경우 세션 로그에 `시드(미검증)` 이 남는다)
  $("시드").value = String(VETTED_SEEDS[Math.floor(Math.random() * VETTED_SEEDS.length)]);

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
    // 라운드 1 C8/P2-11: 슬라이더는 volGain 만 만진다 — rampGain(페이드)을
    // 추월할 수 없다 (두 게인 노드가 곱으로 직렬)
    const ctx = audioCtx;
    if (ctx && playback.active) playback.volGain.gain.setTargetAtTime(sliderVolume(), ctx.currentTime, 0.2);
    if (ctx && previewNodes) previewNodes.volGain.gain.setTargetAtTime(sliderVolume(), ctx.currentTime, 0.2);
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
