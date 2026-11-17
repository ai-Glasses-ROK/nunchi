"use strict";
// Nunchi 웹 클라이언트: 글자/음성/사진 질문 → SSE로 답을 받아 화면에 쓰고 소리로 읽기.
// 외부 라이브러리 없이 동작합니다 (오프라인).

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem("nunchi." + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("nunchi." + k, v); } catch { /* 사생활 보호 모드 등 */ } },
};
const newSid = () => Math.random().toString(16).slice(2, 14);

const state = {
  sessionId: store.get("session", "") || newSid(),
  busy: false,
  rec: null,
  audioQueue: [],
  playing: false,
  player: new Audio(),
  unlocked: false,
};
store.set("session", state.sessionId);

// ---------------------------------------------------------------- 설정
for (const id of ["token", "speakMode", "style"]) {
  const el = $(id);
  el.value = store.get(id, el.value);
  el.addEventListener("change", () => { store.set(id, el.value); if (id === "token") refreshHealth(); });
}
$("settingsBtn").addEventListener("click", () => {
  const p = $("settings");
  p.hidden = !p.hidden;
  $("settingsBtn").setAttribute("aria-expanded", String(!p.hidden));
});
$("resetBtn").addEventListener("click", async () => {
  await fetch("/v1/session/reset", { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify({ session_id: state.sessionId }) }).catch(() => {});
  state.sessionId = newSid();
  store.set("session", state.sessionId);
  addMsg("hub", "대화를 새로 시작했어요.");
});
$("kbBtn").addEventListener("click", () => { const k = $("kb"); k.hidden = !k.hidden; if (!k.hidden) loadKB(); });

function headers(extra = {}) {
  const t = $("token").value.trim();
  return t ? { ...extra, Authorization: "Bearer " + t } : extra;
}

// ---------------------------------------------------------------- 상태 표시
function setStatus(level, text) {
  $("statusDot").className = "dot " + (level === "ok" ? "ok" : level === "bad" ? "bad" : "");
  $("statusText").textContent = text;
}

async function refreshHealth() {
  try {
    const r = await fetch("/v1/health", { headers: headers() });
    if (r.status === 401) { setStatus("bad", "토큰이 필요해요 → ⚙︎ 설정"); return; }
    const h = await r.json();
    const missing = h.missing_models && h.missing_models.length ? ` · 없는 모델: ${h.missing_models.join(", ")}` : "";
    setStatus(h.ok ? "ok" : "warn", `${h.models.llm} · 문서 ${h.index.documents}개 · STT ${h.stt} · TTS ${h.tts}${missing}`);
  } catch {
    setStatus("bad", "허브에 연결할 수 없어요");
  }
}

// ---------------------------------------------------------------- 메시지
function addMsg(role, text, opts = {}) {
  const div = document.createElement("div");
  div.className = "msg " + role + (opts.error ? " error" : "");
  if (opts.image) {
    const img = document.createElement("img");
    img.src = opts.image;
    img.alt = "보낸 사진";
    div.appendChild(img);
  }
  const p = document.createElement("p");
  p.textContent = text;
  div.appendChild(p);
  $("log").appendChild(div);
  div.scrollIntoView({ block: "end", behavior: "smooth" });
  return div;
}

function renderSources(bubble, sources) {
  if (!sources || !sources.length) return;
  const det = document.createElement("details");
  det.className = "sources";
  const sum = document.createElement("summary");
  sum.textContent = `참고 문서 ${sources.length}개`;
  det.appendChild(sum);
  const ol = document.createElement("ol");
  for (const s of sources) {
    const li = document.createElement("li");
    li.textContent = s.label || s.title;
    const snip = document.createElement("span");
    snip.className = "snippet";
    snip.textContent = (s.text || "").slice(0, 220) + ((s.text || "").length > 220 ? "…" : "");
    li.appendChild(snip);
    ol.appendChild(li);
  }
  det.appendChild(ol);
  bubble.appendChild(det);
}

function renderMeta(bubble, timings) {
  if (!timings) return;
  const names = { stt_ms: "인식", retrieval_ms: "검색", first_token_ms: "첫 글자", total_ms: "전체" };
  const parts = Object.entries(names).filter(([k]) => timings[k] !== undefined).map(([k, n]) => `${n} ${Math.round(timings[k])}ms`);
  if (!parts.length) return;
  const m = document.createElement("div");
  m.className = "meta";
  m.textContent = "⏱ " + parts.join(" · ");
  bubble.appendChild(m);
}

// ---------------------------------------------------------------- 소리
function unlockAudio() {
  // iOS 사파리는 '사용자 터치 안에서' 한 번 소리를 내야 이후 자동 재생을 허용합니다.
  if (state.unlocked) return;
  state.unlocked = true;
  try {
    state.player.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";
    state.player.play().catch(() => {});
  } catch { /* 무시 */ }
  if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(" ");
    u.volume = 0;
    speechSynthesis.speak(u);
  }
}

function speakBrowser(text) {
  if (!("speechSynthesis" in window) || !text) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "ko-KR";
  const v = speechSynthesis.getVoices().find((x) => x.lang && x.lang.toLowerCase().startsWith("ko"));
  if (v) u.voice = v;
  u.rate = 1.05;
  speechSynthesis.speak(u);
}

function enqueueAudio(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  state.audioQueue.push(URL.createObjectURL(new Blob([bytes], { type: "audio/wav" })));
  playNext();
}

function playNext() {
  if (state.playing || !state.audioQueue.length) return;
  state.playing = true;
  const url = state.audioQueue.shift();
  const a = state.player;
  const next = () => { URL.revokeObjectURL(url); state.playing = false; playNext(); };
  a.onended = next;
  a.onerror = next;
  a.src = url;
  a.play().catch(next);
}

function stopSpeaking() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  state.audioQueue.length = 0;
  try { state.player.pause(); } catch { /* 무시 */ }
  state.playing = false;
}

// ---------------------------------------------------------------- SSE (fetch 스트림)
async function readError(r) {
  try { const j = await r.json(); return j.detail || JSON.stringify(j); } catch { return `HTTP ${r.status}`; }
}

async function streamRequest(url, init, onEvent) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(await readError(r));
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let ev = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) ev = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(ev, JSON.parse(data));
    }
  }
}

async function runTurn(url, init, userBubble) {
  if (state.busy) return;
  state.busy = true;
  stopSpeaking();
  const speakMode = $("speakMode").value;
  const bubble = addMsg("hub", "");
  const p = bubble.querySelector("p");
  bubble.classList.add("typing");
  let text = "";
  let sources = [];
  try {
    await streamRequest(url, init, (ev, d) => {
      if (ev === "transcript" && userBubble) {
        userBubble.querySelector("p").textContent = d.text ? `🎙️ ${d.text}` : "🎙️ (알아듣지 못함)";
      } else if (ev === "retrieval") {
        sources = d.sources;
      } else if (ev === "delta") {
        text += d.text;
        p.textContent = text;
        bubble.scrollIntoView({ block: "end" });
      } else if (ev === "sentence" && speakMode === "browser") {
        speakBrowser(d.text);
      } else if (ev === "audio" && speakMode === "hub") {
        enqueueAudio(d.wav_b64);
      } else if (ev === "error") {
        const e = document.createElement("div");
        e.className = "meta";
        e.textContent = `⚠️ ${d.stage}: ${d.message}`;
        bubble.appendChild(e);
      } else if (ev === "done") {
        if (!d.ok || !text) {
          p.textContent = d.answer || text;
          if (speakMode !== "off") speakBrowser(d.speech || d.answer);
        }
        renderSources(bubble, d.sources || sources);
        renderMeta(bubble, d.timings);
      }
    });
  } catch (e) {
    p.textContent = "요청 실패: " + e.message;
    bubble.classList.add("error");
  } finally {
    bubble.classList.remove("typing");
    state.busy = false;
  }
}

// ---------------------------------------------------------------- 글자 질문
function askText(q) {
  const userBubble = addMsg("user", q);
  const speakMode = $("speakMode").value;
  return runTurn(
    "/v1/ask",
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ question: q, session_id: state.sessionId, stream: true, speak: speakMode === "hub", style: $("style").value }),
    },
    userBubble,
  );
}

$("askForm").addEventListener("submit", (e) => {
  e.preventDefault();
  unlockAudio();
  const q = $("question").value.trim();
  if (!q) return;
  $("question").value = "";
  askText(q);
});
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) { unlockAudio(); askText(chip.dataset.q); }
});

// ---------------------------------------------------------------- 음성 녹음 (WAV 16kHz)
function encodeWav(samples, sr) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE");
  w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

function downsample(data, from, to) {
  if (from === to) return data;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(data.length / ratio));
  for (let i = 0; i < out.length; i++) {
    // 구간 평균 = 간단한 저역 통과 + 솎아내기
    const start = Math.floor(i * ratio);
    const end = Math.min(data.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += data[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

async function startRec() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(proc);
  proc.connect(ctx.destination);
  state.rec = { stream, ctx, src, proc, chunks, started: performance.now() };
  $("micBtn").classList.add("recording");
  $("micBtn").setAttribute("aria-pressed", "true");
}

async function stopRec() {
  const r = state.rec;
  if (!r) return;
  state.rec = null;
  $("micBtn").classList.remove("recording");
  $("micBtn").setAttribute("aria-pressed", "false");
  r.proc.disconnect();
  r.src.disconnect();
  r.stream.getTracks().forEach((t) => t.stop());
  const sr = r.ctx.sampleRate;
  await r.ctx.close();
  const total = r.chunks.reduce((n, c) => n + c.length, 0);
  if (total < sr * 0.3) { addMsg("hub", "너무 짧아요. 버튼을 누른 채로 말해 보세요."); return; }
  const all = new Float32Array(total);
  let off = 0;
  for (const c of r.chunks) { all.set(c, off); off += c.length; }
  const wav = encodeWav(downsample(all, sr, 16000), 16000);
  const userBubble = addMsg("user", "🎙️ 인식 중…");
  const fd = new FormData();
  fd.append("file", wav, "voice.wav");
  fd.append("session_id", state.sessionId);
  fd.append("stream", "true");
  fd.append("speak", String($("speakMode").value === "hub"));
  fd.append("style", $("style").value);
  await runTurn("/v1/voice", { method: "POST", headers: headers(), body: fd }, userBubble);
}

// 누르고 있는 동안 녹음(길게 누르기) 또는 짧게 탭하면 토글
let pressAt = 0;
let toggleMode = false;
$("micBtn").addEventListener("pointerdown", async (e) => {
  e.preventDefault();
  unlockAudio();
  stopSpeaking();
  if (state.rec) { toggleMode = false; await stopRec(); return; }
  pressAt = performance.now();
  try { await startRec(); } catch (err) { addMsg("hub", "마이크를 쓸 수 없어요: " + err.message, { error: true }); }
});
$("micBtn").addEventListener("pointerup", async () => {
  if (!state.rec) return;
  if (performance.now() - pressAt < 350) { toggleMode = true; return; } // 짧게 탭 → 다시 탭할 때까지 녹음
  if (!toggleMode) await stopRec();
});

// ---------------------------------------------------------------- 사진 질문
function resizeImage(file, maxSide = 1280) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error("이미지 변환 실패"))), "image/jpeg", 0.85);
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error("이미지를 열 수 없어요"));
    img.src = URL.createObjectURL(file);
  });
}

$("photo").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";
  if (!file) return;
  unlockAudio();
  const q = $("question").value.trim() || "지금 보이는 게 뭐야? 짧게 설명해 줘.";
  $("question").value = "";
  let blob;
  try { blob = await resizeImage(file); } catch (err) { addMsg("hub", err.message, { error: true }); return; }
  const userBubble = addMsg("user", q, { image: URL.createObjectURL(blob) });
  const fd = new FormData();
  fd.append("image", blob, "photo.jpg");
  fd.append("question", q);
  fd.append("session_id", state.sessionId);
  fd.append("stream", "true");
  fd.append("speak", String($("speakMode").value === "hub"));
  fd.append("style", $("style").value);
  await runTurn("/v1/vision", { method: "POST", headers: headers(), body: fd }, userBubble);
});

// ---------------------------------------------------------------- 지식베이스
async function loadKB() {
  try {
    const r = await fetch("/v1/kb", { headers: headers() });
    if (!r.ok) throw new Error(await readError(r));
    const kb = await r.json();
    $("kbStats").textContent = `문서 ${kb.stats.documents}개 · 조각 ${kb.stats.chunks}개 · 임베딩 ${kb.stats.embedded_chunks}개 (${kb.stats.embed_model || "없음"})`;
    const ul = $("kbList");
    ul.textContent = "";
    for (const d of kb.documents) {
      const li = document.createElement("li");
      li.textContent = `${d.title} — 조각 ${d.n_chunks}개`;
      ul.appendChild(li);
    }
  } catch (e) {
    $("kbStats").textContent = "불러오기 실패: " + e.message;
  }
}

$("kbFiles").addEventListener("change", async (e) => {
  const files = [...(e.target.files || [])];
  e.target.value = "";
  if (!files.length) return;
  $("kbStats").textContent = "올리는 중…";
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  try {
    const r = await fetch("/v1/kb/upload", { method: "POST", headers: headers(), body: fd });
    if (!r.ok) throw new Error(await readError(r));
    const res = await r.json();
    const failed = res.results.filter((x) => !x.ok);
    if (failed.length) addMsg("hub", "일부 실패: " + failed.map((x) => `${x.name}(${x.error})`).join(", "), { error: true });
  } catch (err) {
    addMsg("hub", "업로드 실패: " + err.message, { error: true });
  }
  loadKB();
  refreshHealth();
});

// ---------------------------------------------------------------- 시작
if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
  $("micBtn").disabled = true;
  $("micBtn").title = "HTTPS에서만 마이크 사용 가능";
  $("secureNote").textContent =
    "브라우저 마이크는 HTTPS(또는 허브 컴퓨터의 localhost)에서만 동작해요. 폰 브라우저에서 말하려면 허브를 " +
    "`nunchi serve --host 0.0.0.0 --https` 로 실행하세요. 글자·사진 질문은 지금도 됩니다.";
}
if ("speechSynthesis" in window) speechSynthesis.getVoices(); // 목소리 목록 미리 불러오기
refreshHealth();
setInterval(refreshHealth, 30000);

// 데모용: 주소에 ?q=질문 을 붙이면 열자마자 물어봅니다 (&speak=off 로 소리 끄기)
(() => {
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  if (params.get("speak")) $("speakMode").value = params.get("speak");
  if (q) setTimeout(() => askText(q), 300);
})();
