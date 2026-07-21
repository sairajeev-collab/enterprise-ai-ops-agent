"""The "Ops Command Center" landing page (public).

A single self-contained page served at ``/``: dark, glassmorphic, TailwindCSS via
CDN, vanilla JS. It authenticates, submits a request (channel + message only — no
password on the submit step), visualizes the LangGraph pipeline node-by-node, and
can stream live progress over Server-Sent Events (consumed via fetch + a stream
reader, since EventSource cannot send auth headers or a POST body).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])

_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Enterprise AI Operations Agent — Ops Command Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background:
      radial-gradient(1200px 600px at 10% -10%, rgba(109,94,246,.25), transparent 60%),
      radial-gradient(1000px 500px at 100% 0%, rgba(34,197,94,.15), transparent 55%),
      #0b0e14; }
    .glass { background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.10);
             backdrop-filter: blur(12px); }
    .node.active { animation: pulse 1s ease-in-out infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.55} }
    pre { white-space: pre-wrap; }
  </style>
</head>
<body class="min-h-screen text-slate-100 font-sans">
  <div class="max-w-6xl mx-auto px-5 py-6">
    <header class="flex items-center gap-3 mb-6">
      <div class="h-9 w-9 rounded-xl bg-gradient-to-br from-indigo-500 to-fuchsia-500
                  grid place-items-center font-bold">◆</div>
      <div>
        <h1 class="text-lg font-semibold leading-tight">Ops Command Center</h1>
        <p class="text-xs text-slate-400">Enterprise AI Operations Agent · LangGraph pipeline</p>
      </div>
      <span id="auth-pill" class="ml-auto text-xs px-3 py-1 rounded-full
            bg-rose-500/15 text-rose-300 border border-rose-500/20">signed out</span>
    </header>

    <div class="grid lg:grid-cols-2 gap-5">
      <!-- Step 1: Auth -->
      <section class="glass rounded-2xl p-5">
        <h2 class="text-xs uppercase tracking-wider text-slate-400 mb-3">1 · Authenticate</h2>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-slate-400">Account</label>
            <input id="account" value="ops-service"
              class="w-full mt-1 rounded-lg bg-black/30 border border-white/10 px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="text-xs text-slate-400">Password</label>
            <input id="password" type="password" value="local-dev-password"
              class="w-full mt-1 rounded-lg bg-black/30 border border-white/10 px-3 py-2 text-sm" />
          </div>
        </div>
        <button id="login"
          class="mt-4 w-full rounded-lg bg-indigo-500 hover:bg-indigo-400 transition
                 py-2.5 text-sm font-semibold">Get token</button>
        <p id="auth-msg" class="text-xs mt-2 text-slate-400"></p>
      </section>

      <!-- Step 2: Submit (NO password here) -->
      <section class="glass rounded-2xl p-5">
        <h2 class="text-xs uppercase tracking-wider text-slate-400 mb-3">2 · Submit request</h2>
        <label class="text-xs text-slate-400">Channel</label>
        <select id="channel"
          class="w-full mt-1 mb-3 rounded-lg bg-black/30 border border-white/10 px-3 py-2 text-sm">
          <option>email</option><option>support_ticket</option><option>slack</option>
          <option>pdf</option><option>invoice</option><option>meeting_notes</option>
        </select>
        <label class="text-xs text-slate-400">Message</label>
        <textarea id="body" rows="4"
          class="w-full mt-1 rounded-lg bg-black/30 border border-white/10 px-3 py-2 text-sm"
        >Production is down, we're seeing an outage across all regions right now.</textarea>
        <div class="flex flex-wrap gap-4 mt-3 text-sm">
          <label class="flex items-center gap-2">
            <input id="inline" type="checkbox" checked class="accent-indigo-500" /> Run Inline (Sync)</label>
          <label class="flex items-center gap-2">
            <input id="stream" type="checkbox" checked class="accent-emerald-500" /> Stream Progress (SSE)</label>
        </div>
        <div class="flex flex-wrap gap-2 mt-3" id="examples"></div>
        <button id="run" disabled
          class="mt-4 w-full rounded-lg bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40
                 transition py-2.5 text-sm font-semibold">Run pipeline</button>
        <p id="run-msg" class="text-xs mt-2 text-rose-300"></p>
      </section>
    </div>

    <!-- Step 3: Pipeline -->
    <section class="glass rounded-2xl p-5 mt-5">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-xs uppercase tracking-wider text-slate-400">3 · Live pipeline</h2>
        <span id="status-badge" class="text-xs px-3 py-1 rounded-full bg-white/5 border border-white/10"></span>
        <button id="retry-btn" class="hidden text-xs ml-2 px-3 py-1 rounded-full bg-rose-500/20 text-rose-200 border border-rose-500/30 hover:bg-rose-500/30">&#8635; Retry</button>
      </div>
      <div id="pipeline" class="flex flex-wrap gap-2"></div>
    </section>

    <!-- Results -->
    <section id="results" class="grid md:grid-cols-2 gap-5 mt-5"></section>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    let token = localStorage.getItem("ops_token") || null;
    let lastId = null;
    const NODES = ["classify","extract","retrieve","create_ticket","draft_reply","notify","persist","generate_report"];
    const EXAMPLES = [
      ["Outage", "Production is down, we're seeing an outage across all regions right now."],
      ["Billing", "I need a refund for my invoice urgently. from Jane Smith jane@acme.com"],
      ["Account", "I can't login and it's blocking my work today. lee@umbrella.com"],
      ["Low signal", "Just wanted to say thanks for the great onboarding yesterday."],
    ];

    function setAuthed(on){
      $("run").disabled = !on;
      $("auth-pill").textContent = on ? "authenticated" : "signed out";
      $("auth-pill").className = "ml-auto text-xs px-3 py-1 rounded-full border " +
        (on ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/20"
            : "bg-rose-500/15 text-rose-300 border-rose-500/20");
    }
    setAuthed(!!token);

    function renderPipeline(states){
      $("pipeline").innerHTML = NODES.map(n => {
        const s = states[n] || "idle";
        const cls = s === "done" ? "bg-emerald-500/20 text-emerald-200 border-emerald-500/30"
          : s === "active" ? "node active bg-amber-500/20 text-amber-200 border-amber-500/30"
          : "bg-white/5 text-slate-400 border-white/10";
        const mark = s === "done" ? "✓ " : s === "active" ? "● " : "";
        return `<span class="text-xs px-3 py-1.5 rounded-lg border ${cls}">${mark}${n}</span>`;
      }).join("");
    }
    const pstate = {};
    function resetPipeline(){ NODES.forEach(n => pstate[n]="idle"); renderPipeline(pstate); $("results").innerHTML=""; }

    function badge(status){
      const map = { completed:"bg-emerald-500/20 text-emerald-200",
        needs_review:"bg-amber-500/20 text-amber-200", failed:"bg-rose-500/20 text-rose-200",
        running:"bg-indigo-500/20 text-indigo-200", queued:"bg-slate-500/20 text-slate-200" };
      $("status-badge").className = "text-xs px-3 py-1 rounded-full border border-white/10 " + (map[status]||"bg-white/5");
      $("status-badge").textContent = (status||"").replace("_"," ");
      // A failed run gets a retry button — the "get the lost refund out" affordance.
      $("retry-btn").classList.toggle("hidden", !(status === "failed" && lastId));
    }

    async function retry(){
      if (!lastId) return;
      const r = await fetch("/v1/requests/" + lastId + "/retry", {
        method: "POST", headers: { "Authorization": "Bearer " + token } });
      if (r.ok) { badge("queued"); $("run-msg").textContent = "Requeued " + lastId; }
      else { $("run-msg").textContent = "Retry failed (" + r.status + ")"; }
    }

    function card(title, inner){
      return `<div class="glass rounded-2xl p-5"><h3 class="text-sm font-semibold mb-2">${title}</h3>${inner}</div>`;
    }
    function esc(s){ return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

    function renderResults(f){
      const out = [];
      const conf = f.confidence != null ? Math.round(f.confidence*100)+"%" : "—";
      out.push(card("Classification",
        `<div class="text-sm text-slate-300">type <b>${f.request_type||"—"}</b> ·
         priority <b>${f.priority||"—"}</b> · confidence <b>${conf}</b></div>` +
        (f.review_reason ? `<p class="text-xs text-amber-300 mt-2">${esc(f.review_reason)}</p>`:"")));
      if (f.ticket) out.push(card("Jira ticket",
        `<div class="text-sm">Ref <b>${esc(f.ticket.key)}</b></div>
         <a class="text-xs text-indigo-300 break-all" href="${esc(f.ticket.url)}">${esc(f.ticket.url)}</a>`));
      if (f.reply) out.push(card("Customer reply",
        `<div class="text-xs text-slate-400 mb-1">${f.reply.sent?"sent":"drafted"}</div>
         <pre class="text-xs bg-black/30 rounded-lg p-3">${esc(f.reply.body||"")}</pre>`));
      out.push(card("Slack notification",
        `<div class="text-sm">${f.notification_sent ? "✓ notified" : "not sent"}</div>`));
      if (f.report) out.push(card("Manager report",
        `<pre class="text-xs bg-black/30 rounded-lg p-3">${esc(f.report)}</pre>`));
      $("results").innerHTML = out.join("");
    }

    async function login(){
      $("auth-msg").textContent = ""; $("run-msg").textContent = "";
      try {
        const r = await fetch("/v1/auth/token", { method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({ account_id:$("account").value, password:$("password").value }) });
        if (!r.ok) throw new Error("Invalid credentials ("+r.status+")");
        token = (await r.json()).access_token; localStorage.setItem("ops_token", token);
        $("auth-msg").textContent = "Authenticated."; setAuthed(true);
      } catch(e){ $("auth-msg").textContent = e.message; setAuthed(false); }
    }

    function payload(){ return { channel:$("channel").value, body:$("body").value }; }

    async function run(){
      $("run-msg").textContent=""; resetPipeline(); $("run").disabled=true;
      const inline = $("inline").checked, stream = $("stream").checked;
      try {
        if (inline && stream) await runStream();
        else if (inline) await runInline();
        else await runAsync();
      } catch(e){ $("run-msg").textContent = e.message; }
      finally { $("run").disabled = !token; }
    }

    async function runStream(){
      badge("running");
      const res = await fetch("/v1/requests?inline=true&stream=true", { method:"POST",
        headers:{"Content-Type":"application/json","Authorization":"Bearer "+token}, body: JSON.stringify(payload()) });
      if (!res.ok || !res.body) throw new Error("Stream failed ("+res.status+")");
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      while (true){
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, {stream:true});
        let idx;
        while ((idx = buf.indexOf("\\n\\n")) >= 0){
          const raw = buf.slice(0, idx); buf = buf.slice(idx+2);
          handleEvent(raw);
        }
      }
    }
    function handleEvent(raw){
      let ev="message", data="";
      raw.split("\\n").forEach(line=>{
        if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      });
      let d = {}; try { d = JSON.parse(data); } catch(e){ return; }
      if (ev === "stream_start"){ lastId = d.request_id; }
      else if (ev === "node_start"){ if (pstate[d.node] !== "done") pstate[d.node] = "active"; renderPipeline(pstate); }
      else if (ev === "node_delta"){ pstate[d.node] = "done"; renderPipeline(pstate); }
      else if (ev === "complete"){ badge(d.final.status); renderResults(d.final); }
      else if (ev === "error"){ badge("failed"); $("run-msg").textContent = d.detail || "pipeline error"; }
    }

    async function runInline(){
      badge("running"); NODES.forEach(n=>pstate[n]="active"); renderPipeline(pstate);
      const res = await fetch("/v1/requests?inline=true", { method:"POST",
        headers:{"Content-Type":"application/json","Authorization":"Bearer "+token}, body: JSON.stringify(payload()) });
      if (!res.ok) throw new Error("Request failed ("+res.status+")");
      const { id } = await res.json();
      lastId = id;
      await loadStatus(id, true);
    }

    async function runAsync(){
      const res = await fetch("/v1/requests", { method:"POST",
        headers:{"Content-Type":"application/json","Authorization":"Bearer "+token}, body: JSON.stringify(payload()) });
      if (!res.ok) throw new Error("Request failed ("+res.status+")");
      const { id, status } = await res.json(); lastId = id; badge(status);
      for (let i=0;i<10;i++){ await new Promise(r=>setTimeout(r,1500)); if (await loadStatus(id, false)) break; }
    }

    async function loadStatus(id, lightAll){
      const res = await fetch("/v1/requests/"+id, { headers:{"Authorization":"Bearer "+token} });
      if (!res.ok) return false;
      const s = await res.json(); badge(s.status);
      const done = ["completed","needs_review","failed"].includes(s.status);
      if (lightAll || done){ NODES.forEach(n=>pstate[n]="done"); if (s.status==="needs_review"){ NODES.forEach(n=>{ if(n!=="classify") pstate[n]="idle"; }); } renderPipeline(pstate); }
      // Build a "final"-shaped object from artifacts.
      const A = {}; (s.artifacts||[]).forEach(a=>A[a.kind]=a);
      renderResults({
        status:s.status, request_type:s.request_type, priority:s.priority, confidence:s.confidence,
        ticket: A.ticket ? A.ticket.payload : null,
        reply: A.reply ? A.reply.payload : null,
        notification_sent: !!A.notification,
        report: A.report ? A.report.payload.report : null,
        review_reason: A.review ? A.review.payload.reason : null,
      });
      return done;
    }

    EXAMPLES.forEach(([label,text])=>{
      const b=document.createElement("button");
      b.className="text-xs px-2.5 py-1 rounded-md bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10";
      b.textContent=label; b.onclick=()=>{ $("body").value=text; };
      $("examples").appendChild(b);
    });
    $("login").onclick = login; $("run").onclick = run; $("retry-btn").onclick = retry;
    resetPipeline();
  </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    return HTMLResponse(_HTML)
