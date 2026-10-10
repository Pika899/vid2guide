"use strict";

// Session token: passed by the launcher in the URL fragment, never sent as part of the URL.
const TOKEN = (() => {
  const m = location.hash.match(/t=([\w-]+)/);
  try {
    if (m) { sessionStorage.setItem("v2g-token", m[1]); history.replaceState(null, "", "/"); return m[1]; }
    return sessionStorage.getItem("v2g-token") || "";
  } catch { return m ? m[1] : ""; }
})();

const $ = (sel, root = document) => root.querySelector(sel);
const pref = {
  get(k, d) { try { return localStorage.getItem("v2g-" + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("v2g-" + k, v); } catch { /* private mode */ } },
};

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { "x-token": TOKEN, ...(body ? { "content-type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { $("#session-error").hidden = false; throw new Error("Sessione non valida"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Errore ${res.status}`);
  return data;
}

const state = { videos: [], jobs: [], settings: null, messages: {} };

// --- Formatting ----------------------------------------------------------

function fmtDuration(s) {
  if (s == null) return "durata n/d";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min ${s % 60} s`;
}
function fmtSize(b) {
  return b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB";
}
function fmtEta(s) {
  if (s == null) return "";
  if (s < 90) return "meno di 2 minuti";
  const m = Math.round(s / 60);
  return m < 60 ? `circa ${m} minuti` : `circa ${Math.floor(m / 60)} h ${m % 60} min`;
}
function fmtUsd(x) {
  if (x == null) return "n/d";
  return x < 0.01 ? "meno di 0,01 $" : x.toFixed(2).replace(".", ",") + " $";
}

// --- Settings ------------------------------------------------------------

async function loadSettings() {
  state.settings = await api("/api/settings");
  const provSel = $("#provider");
  provSel.innerHTML = "";
  for (const p of state.settings.providers) {
    provSel.append(new Option(p.label + (p.key ? "" : " — manca la chiave"), p.id));
  }
  provSel.value = pref.get("provider", state.settings.providers.find(p => p.key)?.id || "anthropic");
  fillModels();
  $("#no-key").hidden = state.settings.providers.some(p => p.key);
  renderKeys();
}

function fillModels() {
  const p = state.settings.providers.find(x => x.id === $("#provider").value);
  const sel = $("#model");
  sel.innerHTML = "";
  for (const m of p?.models || []) sel.append(new Option(m.label, m.id));
  const saved = pref.get("model-" + p?.id, "");
  if ([...sel.options].some(o => o.value === saved)) sel.value = saved;
}

const KEY_LINKS = {
  anthropic: "https://console.anthropic.com/settings/keys",
  openai: "https://platform.openai.com/api-keys",
};

function renderKeys() {
  const box = $("#key-list");
  box.innerHTML = "";
  for (const p of state.settings.providers) {
    const el = document.createElement("div");
    el.className = "key";
    el.innerHTML = `
      <div class="key-head"><strong></strong><span class="small muted status"></span></div>
      <label class="field"><span>Nuova chiave</span><input type="password" autocomplete="off" spellcheck="false"></label>
      <div class="row">
        <button type="button" class="btn small save">Salva e verifica</button>
        <button type="button" class="btn ghost small del">Elimina</button>
        <a class="small" target="_blank" rel="noopener">Dove trovo la chiave?</a>
      </div>
      <p class="key-msg small" aria-live="polite"></p>`;
    $("strong", el).textContent = p.label;
    $(".status", el).textContent = p.key ? `Salvata (${p.key})` : "Non impostata";
    $("a", el).href = KEY_LINKS[p.id];
    $(".del", el).disabled = !p.key;
    const msg = $(".key-msg", el);
    $(".save", el).onclick = async () => {
      const key = $("input", el).value.trim();
      if (!key) { msg.className = "key-msg small err"; msg.textContent = "Incolla prima la chiave."; return; }
      msg.className = "key-msg small"; msg.textContent = "Verifico la chiave…";
      try {
        const r = await api(`/api/keys/${p.id}`, { method: "POST", body: { key } });
        msg.className = "key-msg small ok";
        msg.textContent = r.persistent ? "Chiave valida e salvata."
          : "Chiave valida, ma il portachiavi non è disponibile: resterà attiva solo finché vid2guide è aperto.";
        await loadSettings();
      } catch (e) { msg.className = "key-msg small err"; msg.textContent = e.message; }
    };
    $(".del", el).onclick = async () => {
      await api(`/api/keys/${p.id}`, { method: "DELETE" });
      await loadSettings();
    };
    box.append(el);
  }
}

// --- Upload --------------------------------------------------------------

function upload(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/videos/upload?name=" + encodeURIComponent(file.name));
    xhr.setRequestHeader("x-token", TOKEN);
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) $("#upload-status").textContent =
        `Copio «${file.name}»: ${Math.round(100 * e.loaded / e.total)}%`;
    };
    xhr.onload = () => {
      if (xhr.status < 300) return resolve();
      let detail = `Errore ${xhr.status}`;
      try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* keep default */ }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("Copia interrotta."));
    xhr.send(file);
  });
}

async function uploadAll(files) {
  for (const f of files) {
    try {
      await upload(f);
      $("#upload-status").textContent = `«${f.name}» aggiunto.`;
    } catch (e) {
      $("#upload-status").textContent = `«${f.name}»: ${e.message}`;
    }
  }
  refreshVideos();
}

// --- Video list ----------------------------------------------------------

function jobFor(name) {
  return state.jobs.find(j => j.video === name && ["queued", "running"].includes(j.status))
    || state.jobs.find(j => j.video === name);
}

function renderVideos() {
  const list = $("#videos");
  $("#empty").hidden = state.videos.length > 0;
  list.innerHTML = "";
  for (const v of state.videos) {
    const li = $("#video-tpl").content.firstElementChild.cloneNode(true);
    $(".video-name", li).textContent = v.name;
    $(".video-meta", li).textContent = `${fmtDuration(v.duration)} · ${fmtSize(v.size)}`;
    const chips = $(".chips", li);
    const chip = (text, cls = "") => { const c = document.createElement("span"); c.className = "chip " + cls; c.textContent = text; chips.append(c); };
    chip(v.transcribed ? "Trascritto" : "Da trascrivere", v.transcribed ? "ok" : "");
    if (v.guide) chip("Guida pronta", "ok");

    const job = jobFor(v.name);
    const active = job && ["queued", "running"].includes(job.status);
    if (active) chip(job.status === "queued" ? "In coda" : (job.kind === "guide" ? "Creo la guida" : "Trascrivo"), "run");

    const btn = act => $(`[data-act=${act}]`, li);
    btn("transcribe").textContent = v.transcribed ? "Ritrascrivi" : "Trascrivi";
    btn("transcribe").classList.toggle("ghost", v.transcribed);
    btn("transcribe").disabled = active;
    btn("transcribe").title = v.transcribed
      ? "Rifà la trascrizione solo se hai cambiato qualità o nomi propri; altrimenti tiene quella che c'è."
      : "";
    btn("guide").textContent = v.guide ? "Rifai la guida" : "Crea guida";
    btn("guide").disabled = active || !v.transcribed;
    btn("guide").title = v.transcribed ? "" : "Prima serve la trascrizione";
    btn("pdf").hidden = !v.guide;
    btn("transcript").hidden = !v.transcribed;

    btn("transcribe").onclick = () => startTranscription(v);
    btn("guide").onclick = () => startGuide(v);
    btn("pdf").onclick = () => api("/api/open", { method: "POST", body: { target: "pdf", video: v.name } }).catch(showErr(v));
    btn("transcript").onclick = () => api("/api/open", { method: "POST", body: { target: "transcript", video: v.name } }).catch(showErr(v));

    const msg = $(".video-msg", li);
    if (active) {
      const box = $(".job", li);
      box.hidden = false;
      $(".job-phase", box).textContent = job.status === "queued" ? "In attesa che finisca il lavoro in corso" : job.phase;
      $(".job-eta", box).textContent = job.eta_seconds != null ? "Mancano " + fmtEta(job.eta_seconds) : "";
      const pct = Math.round(100 * (job.fraction || 0));
      $(".bar > div", box).style.width = pct + "%";
      $(".bar", box).setAttribute("aria-valuenow", pct);
      $("[data-act=cancel]", box).onclick = () => api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
    } else if (job && job.status === "failed") {
      msg.className = "video-msg small err"; msg.textContent = job.message;
    } else if (job && job.status === "cancelled") {
      msg.className = "video-msg small"; msg.textContent = job.message || "Annullato.";
    } else if (job && job.status === "done" && job.kind === "guide" && v.guide_meta) {
      const m = v.guide_meta;
      msg.className = "video-msg small ok";
      msg.textContent = `Guida pronta: ${m.index_entries} voci nell'indice, ${m.screen_prompts} prompt a schermo. ` +
        `Costo: ${fmtUsd(m.cost_usd)} (${m.input_tokens.toLocaleString("it")} token in entrata, ${m.output_tokens.toLocaleString("it")} in uscita` +
        (m.screenshots && m.screenshots.calls ? `; di cui screenshot: ${fmtUsd(m.screenshots.cost_usd)}` : "") + ")." +
        (m.cost_may_be_higher ? " Calcolato con il prezzo standard: per richieste molto lunghe il servizio può applicare un prezzo più alto." : "");
    } else if (state.messages[v.name]) {
      msg.className = "video-msg small err"; msg.textContent = state.messages[v.name];
    }
    list.append(li);
  }
}

const showErr = v => e => { state.messages[v.name] = e.message; renderVideos(); };

async function startTranscription(v) {
  delete state.messages[v.name];
  try {
    await api("/api/transcribe", { method: "POST", body: {
      video: v.name, quality: $("#quality").value, terms: $("#terms").value || null } });
    await refreshJobs();
  } catch (e) { showErr(v)(e); }
}

async function startGuide(v) {
  delete state.messages[v.name];
  const provider = $("#provider").value, model = $("#model").value;
  const p = state.settings.providers.find(x => x.id === provider);
  if (!p?.key) { $("#settings").showModal(); return; }
  const body = $("#confirm-body");
  body.innerHTML = "<p class='muted'>Calcolo il costo…</p>";
  const dlg = $("#confirm");
  $("#confirm-ok").disabled = true;
  dlg.showModal();
  try {
    const est = await api("/api/guide/estimate", { method: "POST", body: { video: v.name, provider, model } });
    const modelLabel = p.models.find(m => m.id === model)?.label || model;
    body.innerHTML = `
      <p class="small"></p>
      <div class="cost small">
        <span class="in"></span>
        <span class="out muted"></span>
      </div>
      <p class="small muted">Il costo effettivo, calcolato dai token usati, appare alla fine.
        È addebitato sul tuo account del servizio scelto.</p>`;
    $("p", body).textContent = `Video: ${v.name} — modello: ${modelLabel}.`;
    $(".in", body).textContent = `Lettura della trascrizione: ${est.exact ? "" : "circa "}` +
      `${est.input_tokens.toLocaleString("it")} token, ${fmtUsd(est.input_cost)}.`;
    $(".out", body).textContent = "In più: il testo della guida scritto dal modello" +
      (est.output_price_per_mtok != null ? ` (${fmtUsd(est.output_price_per_mtok / 100)} ogni 10.000 token)` : "") +
      " e la lettura degli screenshot dei prompt, uno per ogni prompt mostrato a schermo." +
      (est.may_cost_more ? " Prezzi standard: per richieste molto lunghe il servizio può applicare un prezzo più alto." : "");
    $("#confirm-ok").disabled = false;
  } catch (e) {
    body.innerHTML = "<p class='small' style='color:var(--err)'></p>";
    $("p", body).textContent = e.message;
  }
  dlg.onclose = async () => {
    if (dlg.returnValue !== "ok") return;
    pref.set("provider", provider); pref.set("model-" + provider, model);
    try {
      await api("/api/guide", { method: "POST", body: { video: v.name, provider, model } });
      await refreshJobs();
    } catch (e) { showErr(v)(e); }
  };
}

// --- Links ---------------------------------------------------------------

const dismissed = new Set();

function renderDownloads() {
  const list = $("#downloads");
  list.innerHTML = "";
  for (const j of state.jobs.filter(j => j.kind === "download" && !dismissed.has(j.id))) {
    const li = document.createElement("li");
    li.className = "download small";
    const url = document.createElement("span");
    url.className = "url muted";
    url.textContent = j.video;
    li.append(url);
    const line = document.createElement("div");
    line.className = "row between";
    const text = document.createElement("span");
    line.append(text);
    li.append(line);
    if (["queued", "running"].includes(j.status)) {
      text.textContent = j.status === "queued" ? "In attesa che finisca il lavoro in corso" : j.phase;
      const eta = document.createElement("span");
      eta.className = "muted";
      eta.textContent = j.eta_seconds != null ? "Mancano " + fmtEta(j.eta_seconds) : "";
      line.append(eta);
      const bar = document.createElement("div");
      bar.className = "bar";
      bar.innerHTML = "<div></div>";
      bar.firstChild.style.width = Math.round(100 * (j.fraction || 0)) + "%";
      li.append(bar);
      const stop = document.createElement("button");
      stop.type = "button"; stop.className = "link small"; stop.textContent = "Interrompi";
      stop.onclick = () => api(`/api/jobs/${j.id}/cancel`, { method: "POST" });
      li.append(stop);
    } else {
      text.className = j.status === "done" ? "ok" : (j.status === "failed" ? "err" : "");
      text.textContent = j.status === "done" ? `Scaricato: ${j.result.video}. Lo trovi qui sotto, tra i tuoi video.`
        : (j.message || "Interrotto.");
      const close = document.createElement("button");
      close.type = "button"; close.className = "link small"; close.textContent = "Nascondi";
      close.onclick = () => { dismissed.add(j.id); renderDownloads(); };
      line.append(close);
    }
    list.append(li);
  }
}

async function submitLink(e) {
  e.preventDefault();
  const url = $("#link").value.trim();
  if (!url) return;
  const browser = $("#needs-login").checked ? $("#browser").value : null;
  try {
    await api("/api/download", { method: "POST", body: { url, browser } });
    $("#link").value = "";
    await refreshJobs();
  } catch (err) {
    $("#upload-status").textContent = err.message;
  }
}

// --- Polling -------------------------------------------------------------

let lastJobsKey = "";
async function refreshJobs() {
  try {
    state.jobs = (await api("/api/jobs")).jobs;
    const key = state.jobs.map(j => j.id + j.status).join();
    if (key !== lastJobsKey) { lastJobsKey = key; await refreshVideos(false); }
    renderVideos();
    renderDownloads();
  } catch { /* shown by the session banner, or transient */ }
}
async function refreshVideos(render = true) {
  try { state.videos = (await api("/api/videos")).videos; } catch { return; }
  if (render) renderVideos();
}

// --- Wiring --------------------------------------------------------------

function wire() {
  $("#open-settings").onclick = () => $("#settings").showModal();
  document.addEventListener("click", e => {
    const a = e.target.closest("[data-action]")?.dataset.action;
    if (a === "settings") $("#settings").showModal();
    if (a === "open-videos") api("/api/open", { method: "POST", body: { target: "videos" } });
    if (a === "open-guides") api("/api/open", { method: "POST", body: { target: "guides" } });
  });
  $("#provider").onchange = () => { pref.set("provider", $("#provider").value); fillModels(); };
  $("#model").onchange = () => pref.set("model-" + $("#provider").value, $("#model").value);
  $("#quality").value = pref.get("quality", "accurate");
  $("#quality").onchange = () => pref.set("quality", $("#quality").value);
  $("#file-input").onchange = e => uploadAll([...e.target.files]);
  $("#link-form").onsubmit = submitLink;
  $("#needs-login").onchange = () => { $("#browser-field").hidden = !$("#needs-login").checked; };
  $("#browser").value = pref.get("browser", /Mac/.test(navigator.platform) ? "chrome" : "edge");
  $("#browser").onchange = () => pref.set("browser", $("#browser").value);
  const drop = $("#drop");
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); uploadAll([...e.dataTransfer.files]); };
  // Dropping a file elsewhere on the page must not make the browser open it.
  window.addEventListener("dragover", e => e.preventDefault());
  window.addEventListener("drop", e => e.preventDefault());
}

(async function start() {
  if (!TOKEN) { $("#session-error").hidden = false; return; }
  wire();
  try { await loadSettings(); } catch { return; }
  await refreshVideos();
  await refreshJobs();
  setInterval(refreshJobs, 1500);
  setInterval(() => refreshVideos(), 10000);
})();
