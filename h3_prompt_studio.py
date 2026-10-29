#!/usr/bin/env python3
"""A standalone chat GUI for writing MiniMax H3 prompts.

A conversation, not a form: prompts are argued into shape over several turns ("shorter", "make her
angrier", "put the dialogue in French"), and the good version is often three regenerations back. So it
works the way a chat client does -- a running thread, several alternatives per answer that you page
through with the arrows, and saved history you can reopen.

Three things it does that Maestro's in-app enhancer does not:

* Keeps the LLM resident. ``services/llm_service.py`` unloads the model after 60 seconds idle, so every
  prompt written after a pause pays a full reload. This page pings the model often enough that it never
  unloads while the window is open.
* Keeps alternatives. Every answer can hold several takes, generated at different seeds and kept side by
  side, so comparing two phrasings does not mean losing the first one.
* Refuses an answer whose description contains no ``<d>`` dialogue tags. Speech written as narration is
  silent in an H3 render, so it retries until the tags are there rather than handing back a prompt that
  will generate a mute clip.

It owns no model of its own: generation goes to Maestro's ``/api/v1/llm/*`` endpoints, so nothing extra
is loaded into VRAM. Maestro must be running.

    python h3_prompt_studio.py [--port 42010] [--maestro http://127.0.0.1:42003]

The browser talks only to this server, which forwards to Maestro. Being same-origin, the page needs no
CORS grant from Maestro and does not have to know which port Maestro landed on.
"""

from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import re
import socketserver
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path

# Chats live under app/settings, which the repo already ignores, so saved conversations never turn into
# stray working-tree changes.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "app" / "settings" / "h3_prompt_studio"

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>H3 Prompt Studio</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --panel-2:#1e222b; --border:#2a2f3a;
    --text:#e6e8ec; --muted:#8b93a4; --accent:#ff6b35; --ok:#3ecf8e; --warn:#ffc94d; --bad:#ff5c5c;
  }
  * { box-sizing:border-box; }
  html, body { height:100%; }
  body { margin:0; background:var(--bg); color:var(--text); overflow:hidden;
         font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  button { font:inherit; cursor:pointer; border:0; border-radius:8px; }
  button:disabled { opacity:.45; cursor:default; }

  #app { display:grid; grid-template-columns:250px 1fr; height:100vh; }

  /* ---- sidebar ---- */
  aside { background:var(--panel); border-right:1px solid var(--border); display:flex; flex-direction:column; min-height:0; }
  .aside-head { display:flex; align-items:center; gap:8px; padding:12px 12px; border-bottom:1px solid var(--border); }
  .aside-head strong { font-size:13px; font-weight:600; flex:1; }
  #new-chat { background:var(--accent); color:#12141a; font-weight:650; font-size:12px; padding:6px 11px; }
  #chats { overflow-y:auto; flex:1; padding:8px; display:flex; flex-direction:column; gap:4px; }
  .chat-item { padding:9px 10px; border-radius:8px; cursor:pointer; border:1px solid transparent;
               display:flex; align-items:center; gap:8px; }
  .chat-item:hover { background:var(--panel-2); }
  .chat-item.active { background:var(--panel-2); border-color:var(--border); }
  .chat-item .t { flex:1; font-size:12.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .chat-item .x { background:transparent; color:var(--muted); font-size:15px; line-height:1; padding:2px 5px; opacity:0; }
  .chat-item:hover .x { opacity:1; }
  .chat-item .x:hover { color:var(--bad); }
  .empty { color:var(--muted); font-size:12px; padding:10px; }

  /* ---- main column ---- */
  #main { display:flex; flex-direction:column; min-width:0; min-height:0; }
  header { display:flex; align-items:center; gap:11px; padding:12px 18px;
           border-bottom:1px solid var(--border); background:var(--panel); }
  h1 { font-size:14px; margin:0; font-weight:600; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--muted); flex:0 0 auto; }
  .dot.on { background:var(--ok); box-shadow:0 0 8px var(--ok); }
  .dot.busy { background:var(--warn); }
  .dot.off { background:var(--bad); }
  #status { color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .spacer { flex:1; }
  .ghost { background:transparent; color:var(--text); border:1px solid var(--border); font-size:12px; padding:6px 12px; }
  .ghost:hover:not(:disabled) { background:var(--panel-2); }

  /* ---- thread ---- */
  #thread { flex:1; overflow-y:auto; padding:22px 18px 8px; }
  .wrap { max-width:860px; margin:0 auto; display:flex; flex-direction:column; gap:18px; }
  .msg { display:flex; flex-direction:column; gap:6px; }
  .who { font-size:10.5px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); }
  .bubble { border-radius:12px; padding:12px 15px; white-space:pre-wrap; overflow-wrap:anywhere; }
  .user .bubble { background:var(--panel-2); border:1px solid var(--border); }
  .bot  .bubble { background:var(--panel); border:1px solid var(--border);
                  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }
  .bot.pending .bubble { color:var(--muted); font-style:italic; font-family:inherit; }
  .bubble[contenteditable=true] { outline:2px solid var(--accent); outline-offset:1px; }
  .tools { display:flex; align-items:center; gap:5px; flex-wrap:wrap; }
  .tools button { background:transparent; color:var(--muted); font-size:11.5px; padding:4px 8px; }
  .tools button:hover:not(:disabled) { color:var(--text); background:var(--panel-2); }
  .swipe { display:flex; align-items:center; gap:2px; margin-right:4px; }
  .swipe .n { font-size:11.5px; color:var(--muted); min-width:38px; text-align:center; font-variant-numeric:tabular-nums; }
  .tag { font-size:10.5px; padding:2px 7px; border-radius:20px; border:1px solid; }
  .tag.ok  { color:var(--ok);  border-color:rgba(62,207,142,.4);  background:rgba(62,207,142,.08); }
  .tag.warn{ color:var(--warn);border-color:rgba(255,201,77,.4);  background:rgba(255,201,77,.08); }
  .tag.bad { color:var(--bad); border-color:rgba(255,92,92,.4);   background:rgba(255,92,92,.08); }
  .hint { color:var(--muted); font-size:13px; text-align:center; padding:48px 20px; line-height:1.7; }
  .hint code { background:var(--panel-2); padding:2px 6px; border-radius:5px; font-size:12px; }

  /* ---- composer ---- */
  footer { border-top:1px solid var(--border); background:var(--panel); padding:11px 18px 14px; }
  .cwrap { max-width:860px; margin:0 auto; display:flex; flex-direction:column; gap:9px; }
  .opts { display:flex; gap:9px; align-items:center; flex-wrap:wrap; }
  .opts label { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }
  select, input[type=number] { background:var(--panel-2); color:var(--text); border:1px solid var(--border);
    border-radius:7px; padding:5px 8px; font-size:12.5px; }
  .chk { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--muted);
         text-transform:none; letter-spacing:0; cursor:pointer; }
  .attached { display:flex; gap:8px; flex-wrap:wrap; }
  .chip { position:relative; border:1px solid var(--border); border-radius:9px; overflow:hidden;
          width:74px; height:74px; background:var(--panel-2); }
  .chip img { width:100%; height:100%; object-fit:cover; display:block; }
  .chip .x { position:absolute; top:2px; right:2px; background:rgba(0,0,0,.66); color:#fff;
             border-radius:50%; width:19px; height:19px; font-size:13px; line-height:19px; padding:0; }
  .chip.busy { display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:11px; }
  .chip.pinned { border-color:var(--accent); }
  .pinrow { display:flex; align-items:center; gap:9px; flex-wrap:wrap; }
  .pinrow .lbl { font-size:11px; color:var(--accent); letter-spacing:.05em; text-transform:uppercase; }
  .pinrow .why { font-size:11.5px; color:var(--muted); }
  .shot { max-width:270px; border-radius:10px; border:1px solid var(--border); display:block; margin-bottom:2px; }
  #attach { background:transparent; color:var(--muted); border:1px solid var(--border); padding:12px 13px; font-size:15px; }
  #attach:hover:not(:disabled) { color:var(--text); background:var(--panel-2); }
  .send-row { display:flex; gap:9px; align-items:flex-end; }
  #ask { flex:1; background:var(--panel-2); color:var(--text); border:1px solid var(--border);
         border-radius:10px; padding:11px 13px; font:inherit; resize:none; min-height:52px; max-height:210px; }
  #ask:focus { outline:none; border-color:var(--accent); }
  #send { background:var(--accent); color:#12141a; font-weight:650; padding:13px 20px; }
  .foot-note { font-size:11px; color:var(--muted); }

  @media (max-width:820px) {
    #app { grid-template-columns:1fr; }
    aside { display:none; }
  }
</style>
</head>
<body>
<div id="app">
  <aside>
    <div class="aside-head">
      <strong>Chats</strong>
      <button id="new-chat">New</button>
    </div>
    <div id="chats"></div>
  </aside>

  <section id="main">
    <header>
      <span class="dot" id="dot"></span>
      <h1>H3 Prompt Studio</h1>
      <span id="status">connecting…</span>
      <span class="spacer"></span>
      <button class="ghost" id="reload">Reload model</button>
    </header>

    <div id="thread"></div>

    <footer>
      <div class="cwrap">
        <div class="opts">
          <label for="mode">Mode</label>
          <select id="mode">
            <option value="fl2va">FL2VA — text / first frame</option>
            <option value="ref2va">Ref2VA — references</option>
          </select>
          <label for="temp">Temp</label>
          <input type="number" id="temp" value="0.7" min="0" max="2" step="0.05" style="width:72px">
          <label for="tokens">Tokens</label>
          <input type="number" id="tokens" value="1200" min="200" max="8192" step="100" style="width:86px">
          <span class="spacer"></span>
          <label class="chk"><input type="checkbox" id="enforce" checked> Require &lt;d&gt; tags</label>
        </div>
        <div class="pinrow" id="pinned"></div>
        <div class="attached" id="attached"></div>
        <div class="send-row">
          <input type="file" id="file" accept="image/*" multiple hidden>
          <button id="attach" title="Attach a first frame">&#128206;</button>
          <textarea id="ask" rows="2" placeholder="Wonder Woman raises her sword on a windswept cliff and says something triumphant."></textarea>
          <button id="send">Send</button>
        </div>
        <div class="foot-note">Enter sends · Shift+Enter for a new line · → past the last take writes another one ·
          paste or attach a first frame to describe it</div>
      </div>
    </footer>
  </section>
</div>

<script>
const $ = id => document.getElementById(id);

// ---- the rules the model is held to -------------------------------------------------------------
// Dialogue first and last: it is the thing the enhancer most often drops, and the only failure that is
// silent -- a line written as narration simply never gets spoken in the render.
const DIALOGUE_RULE = `
DIALOGUE IS MANDATORY. Every word anyone speaks or sings must appear inside <d>[Language] ...</d>.
Text outside those tags is scene description and is NEVER spoken aloud, so a spoken line written as
narration is a line the video will not say.
- Put ONLY the spoken words inside the tags. Speaker, tone and action go outside them.
- Applies to quoted speech, "says"/"replies"/"shouts", voice-over, chants, whispers, lyrics, and
  off-screen/phone/radio voices.
- Reproduce supplied dialogue verbatim, in its original language, however many lines there are. Never
  summarize, paraphrase, shorten or omit it.
- If speech is requested WITHOUT the words given ("says something triumphant", "they argue"), write the
  line yourself: fitting the speaker and moment, and short enough to speak naturally within the shot
  (about two words per second). Never describe that speech occurs instead of writing it.
- Give each voice a stable ID (S1), (S2), used consistently across shots.

EXAMPLE. Request: Walter White says: "Jesse Yo!"
  CORRECT -> ...WALTER WHITE (S1) turns to face him and says <d>[English] Jesse Yo!</d>
  WRONG   -> ...WALTER WHITE (S1) turns to face him and delivers his line.
  WRONG   -> ...WALTER WHITE (S1) turns to face him and says "Jesse Yo!"
The words only exist if they are inside the tags. Describing that someone speaks produces silence.`;

const FL2VA = `You are a professional audiovisual prompt writer for MiniMax H3 (FL2VA).
${DIALOGUE_RULE}

Rewrite the user's request into ONE production-ready H3 prompt. Output only the prompt: no commentary,
no Markdown, no code fences. It must contain exactly these three fields, in order:

integrated_multimodal_description: one chronological audiovisual timeline. Open on [Shot 1] with no
timestamp; later cuts begin "[Shot N] At MM:SS.mmm, ...". Cut only when it conveys new subject, space,
state, viewpoint or time. Describe camera movement as type, amplitude and speed. Keep identity,
wardrobe, props, geography, lighting and sound continuous across shots. Use <scenetrans> at both sides
of a cut a line crosses, and <cutoff> only if the final frame truncates speech.

overall_soundscape: one compact paragraph of ambience, physical action sounds and non-verbal human
sounds. Never repeat dialogue here.

non_diegetic_music: audience-only score, described through instrumentation, tempo and dynamics, or
"N/A" when none is wanted.

Write everything in English except dialogue, lyrics and visible text, which keep their own language.`;

const REF2VA = `You are a professional audiovisual prompt writer for MiniMax H3 (Ref2VA).
${DIALOGUE_RULE}

Rewrite the user's request into ONE production-ready full-reference prompt. Output only the prompt: no
commentary, no Markdown, no code fences. Six sections, in this order:

subject_definitions: one line per referenced item. <Subject N> is reusable visible content; cite the
asset it comes from. <Picture N> gets its own line only as a concrete frame anchor or storyboard.
<Video N> covers whole-video roles. <Audio N> is copied or referenced sound. Numbering is independent
per label type.

summary: one paragraph opening with bracketed task types drawn from keyframe completion, reference
generation, video editing, video continuation, audio reuse, audio reference, combined with " + ".

retention_analysis: one line per label. Visible content uses fully_preserved / partially_preserved /
attribute_transfer / weak_reference; audio uses fully_copy / partially_copy / reference /
weak_reference. Never put a speaker ID in this section.

detailed_description: the main body, shot by shot. One or two sentences of global visual treatment
BEFORE [Shot 1]. Later cuts begin "[Shot N] At MM:SS.mmm, ...". A referenced subject who speaks is
written <Subject N> (Sx). Aim for 350-500 words unless the dialogue timeline needs otherwise.

overall_soundscape: ambience and physical sounds only.

non_diegetic_music: audience-only score, or "N/A".

Write everything in English except dialogue, lyrics and visible text.`;

const REPAIR_SYSTEM = `You repair MiniMax H3 prompts that are missing their dialogue tags.

You are given a prompt in which speech was written as narration, so none of it would be audible.
Return the SAME prompt, character for character, with ONE class of change:

* Every spoken or sung line must sit inside <d>[Language] ...</d>, containing ONLY the words said.
* Where the text merely reports that someone speaks -- "delivers the line", "says something
  triumphant", "they argue" -- replace that report with the actual words inside the tags. Invent them
  if they were never supplied: fitting the speaker, and short enough to say in the shot.
* Speech already given to you in the request must appear verbatim, in its original language.

Change nothing else: no new shots, no rewritten description, no commentary. Output only the prompt.`;

// A follow-up turn is a revision request, not a fresh brief. Without this the model tends to answer with
// only the changed sentence, which is useless as a prompt -- H3 needs the whole thing.
const REVISION_NOTE = `Apply the latest request to the prompt above and output the COMPLETE revised H3
prompt in the same format. Never reply with only the changed part, and never add commentary.`;

// Speech the user actually supplied, in quotes of any common flavour. Naming it back to the model as a
// hard requirement is far more reliable than hoping a general rule survives -- and losing supplied
// dialogue (rather than merely leaving it untagged) is the worst failure of the two.
function extractQuotes(text) {
  const out = [];
  // Explicit escapes for the curly forms: writing them literally is too easy to mangle in transit,
  // and a smart-quoted line is the common case when dialogue is pasted in from a script.
  const re = /[\u201C\u00AB]([^\u201D\u00BB\n]{1,200})[\u201D\u00BB]|"([^"\n]{1,200})"|[\u2018]([^\u2019\n]{2,200})[\u2019]|'([^'\n]{2,200})'/g;
  let m;
  while ((m = re.exec(text))) {
    const q = (m[1] || m[2] || m[3] || m[4] || '').trim();
    if (q && !out.includes(q)) out.push(q);
  }
  return out;
}

// ---- talking to the studio's own server ----------------------------------------------------------
// Everything is same-origin; the server forwards to Maestro. The page therefore needs no CORS grant and
// never has to work out which port Maestro landed on.
async function api(path, body) {
  const res = await fetch(path, body === undefined
    ? {} : { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || (path + ' -> ' + res.status));
  return data;
}

function setState(kind, text) { $('dot').className = 'dot ' + kind; $('status').textContent = text; }

async function refreshStatus() {
  try {
    const s = await api('/api/state');
    if (!s.reachable) { setState('off', 'Maestro is not running — start it, and this page will connect on its own.'); return false; }
    vision = !!s.vision;
    setState(s.loaded ? 'on' : 'off',
             s.loaded ? ('model resident — ' + (s.model || 'local LLM') + (vision ? ' · vision' : ' · text only'))
                      : 'model not loaded');
    syncAttach();
    return !!s.loaded;
  } catch (e) { setState('off', String(e.message || e)); return false; }
}

async function ensureLoaded() {
  if (await refreshStatus()) return true;
  setState('busy', 'loading model…');
  try { await api('/api/load', {}); } catch (e) { setState('off', String(e.message || e)); return false; }
  return refreshStatus();
}

// The service unloads after 60s idle and only a real generation resets that timer, so keep one tiny
// request going. One token is enough to count as activity.
async function keepAlive() {
  if (busy) return;   // a real generation already counts as activity
  try { await api('/api/keepalive', {}); } catch (e) { /* a failed ping is not worth interrupting for */ }
  refreshStatus();
}

// ---- the dialogue gate ---------------------------------------------------------------------------
// The rule: an integrated_multimodal_description (or a Ref2VA detailed_description) must never come back
// without dialogue tags.
function missingDialogueTags(text) {
  return /integrated_multimodal_description|detailed_description/i.test(text) && !/<d>/.test(text);
}
function tagCount(text) { return (text.match(/<d>/g) || []).length; }

// ---- chat state ----------------------------------------------------------------------------------
// A message is { role:'user', text } or { role:'assistant', swipes:[{text,ms,attempts}], active }.
let chat = null;       // the open conversation
let index = [];        // sidebar listing
let busy = false;
let pending = [];      // images attached to the message being composed
let vision = false;    // whether the loaded model can actually look at them

const activeSwipe = m => (m.swipes && m.swipes[m.active]) || null;
const activeText  = m => { const s = activeSwipe(m); return s ? s.text : ''; };

function newChat() {
  chat = { id:'', title:'New chat', created:Date.now(), updated:Date.now(), messages:[], pinned:[],
           settings:readSettings() };
  pending = []; renderAttached(); renderPinned();
  render(); renderIndex(); $('ask').focus();
}

function readSettings() {
  return { mode:$('mode').value, temp:parseFloat($('temp').value) || 0.7,
           tokens:parseInt($('tokens').value, 10) || 1200, enforce:$('enforce').checked };
}
function applySettings(s) {
  if (!s) return;
  if (s.mode)   $('mode').value = s.mode;
  if (s.temp   !== undefined) $('temp').value = s.temp;
  if (s.tokens !== undefined) $('tokens').value = s.tokens;
  if (s.enforce !== undefined) $('enforce').checked = s.enforce;
}

// Saving is fire-and-forget: losing a save is recoverable, blocking the UI on disk is not.
let saveTimer = null;
function save(now) {
  if (!chat || !chat.messages.length) return;
  clearTimeout(saveTimer);
  const doSave = async () => {
    chat.settings = readSettings();
    chat.updated = Date.now();
    if (!chat.title || chat.title === 'New chat') {
      const first = chat.messages.find(m => m.role === 'user');
      if (first) chat.title = (first.text || '').replace(/\s+/g, ' ').trim().slice(0, 60)
                            || ((first.images || []).length ? 'Image prompt' : 'New chat');
    }
    try {
      const res = await api('/api/chats/save', chat);
      chat.id = res.id;
      await loadIndex();
    } catch (e) { console.warn('save failed', e); }
  };
  if (now) doSave(); else saveTimer = setTimeout(doSave, 700);
}

async function loadIndex() {
  try { index = (await api('/api/chats')).chats || []; } catch (e) { index = []; }
  renderIndex();
}

async function openChat(id) {
  try {
    chat = await api('/api/chats/get', { id });
    chat.pinned = chat.pinned || [];
    applySettings(chat.settings);
    pending = []; renderAttached(); renderPinned();
    render(); renderIndex();
  } catch (e) { console.warn(e); }
}

async function deleteChat(id, ev) {
  ev.stopPropagation();
  if (!confirm('Delete this chat?')) return;
  try { await api('/api/chats/delete', { id }); } catch (e) { /* already gone */ }
  if (chat && chat.id === id) newChat();
  await loadIndex();
}

function renderIndex() {
  const box = $('chats');
  box.textContent = '';
  if (!index.length) {
    const p = document.createElement('div');
    p.className = 'empty'; p.textContent = 'No saved chats yet.';
    box.appendChild(p); return;
  }
  for (const c of index) {
    const row = document.createElement('div');
    row.className = 'chat-item' + (chat && chat.id === c.id ? ' active' : '');
    row.onclick = () => openChat(c.id);
    const t = document.createElement('div');
    t.className = 't'; t.textContent = c.title; t.title = c.title;
    const x = document.createElement('button');
    x.className = 'x'; x.textContent = '×'; x.title = 'Delete';
    x.onclick = ev => deleteChat(c.id, ev);
    row.append(t, x);
    box.appendChild(row);
  }
}

// ---- attachments ---------------------------------------------------------------------------------
// The bytes go to this server, which keeps the file and hands Maestro a path -- Maestro reads images
// off disk, so shipping the data onward would not help it.
async function upload(file) {
  const b64 = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');
    r.onerror = () => reject(new Error('could not read ' + file.name));
    r.readAsDataURL(file);
  });
  return api('/api/upload', { name: file.name || 'pasted.png', data: b64 });
}

async function addFiles(files) {
  for (const file of files) {
    if (!file || !/^image\//.test(file.type)) continue;
    const slot = { name: file.name || 'pasted.png', busy: true };
    pending.push(slot);
    renderAttached();
    try {
      Object.assign(slot, await upload(file), { busy: false });
    } catch (e) {
      pending.splice(pending.indexOf(slot), 1);
      setState('off', String(e.message || e));
    }
    renderAttached();
  }
}

function renderAttached() {
  const box = $('attached');
  box.textContent = '';
  pending.forEach((img, i) => {
    const chip = document.createElement('div');
    chip.className = 'chip' + (img.busy ? ' busy' : '');
    if (img.busy) { chip.textContent = '…'; }
    else {
      const el = document.createElement('img');
      el.src = img.url; el.alt = img.name;
      const x = document.createElement('button');
      x.className = 'x'; x.textContent = '×'; x.title = 'Remove';
      x.onclick = () => { pending.splice(i, 1); renderAttached(); };
      chip.append(el, x);
    }
    box.appendChild(chip);
  });
}

// A pinned frame belongs to the conversation, not to one message: it is sent with every take, every
// swipe and every follow-up until it is removed, so the same shot never has to be attached twice.
function renderPinned() {
  const box = $('pinned');
  if (!box) return;
  box.textContent = '';
  const pins = (chat && chat.pinned) || [];
  if (!pins.length) return;

  const lbl = document.createElement('span');
  lbl.className = 'lbl'; lbl.textContent = '\u{1F4CC} Pinned frame';
  box.appendChild(lbl);

  pins.forEach((img, i) => {
    const chip = document.createElement('div');
    chip.className = 'chip pinned';
    const el = document.createElement('img');
    el.src = img.url; el.alt = img.name || 'pinned frame';
    const x = document.createElement('button');
    x.className = 'x'; x.textContent = '×'; x.title = 'Unpin — later takes stop seeing this frame';
    x.onclick = () => { chat.pinned.splice(i, 1); renderPinned(); save(); };
    chip.append(el, x);
    box.appendChild(chip);
  });

  const why = document.createElement('span');
  why.className = 'why'; why.textContent = 'sent with every take and follow-up';
  box.appendChild(why);
}

// The button stays clickable without vision so the reason is discoverable, but says what will happen.
function syncAttach() {
  const b = $('attach');
  if (!b) return;
  b.title = vision ? 'Attach a first frame'
                   : 'The loaded model has no vision support — an attached image would be ignored';
  b.style.opacity = vision ? '' : '.5';
}

// ---- rendering the thread ------------------------------------------------------------------------
function button(label, title, fn, disabled) {
  const b = document.createElement('button');
  b.textContent = label; b.title = title; b.onclick = fn; b.disabled = !!disabled;
  return b;
}

function render(keepScroll) {
  const thread = $('thread');
  const before = thread.scrollTop, atBottom = thread.scrollHeight - thread.clientHeight - before < 80;
  thread.textContent = '';

  if (!chat || !chat.messages.length) {
    const h = document.createElement('div');
    h.className = 'hint';
    h.innerHTML = 'Describe the shot you want and it comes back as a full H3 prompt.<br>'
                + 'Ask for changes in the next message — <code>make it shorter</code>, '
                + '<code>put the dialogue in French</code> — and the whole prompt is rewritten.<br>'
                + 'Use <code>↻</code> or the <code>→</code> arrow for another take of the same answer.';
    thread.appendChild(h);
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'wrap';

  chat.messages.forEach((m, i) => {
    const el = document.createElement('div');
    el.className = 'msg ' + (m.role === 'user' ? 'user' : 'bot') + (m.pending ? ' pending' : '');

    const who = document.createElement('div');
    who.className = 'who';
    who.textContent = m.role === 'user' ? 'You' : (chat.settings && chat.settings.mode === 'ref2va' ? 'Ref2VA' : 'FL2VA');

    const bub = document.createElement('div');
    bub.className = 'bubble';
    for (const img of (m.images || [])) {
      const shot = document.createElement('img');
      shot.className = 'shot'; shot.src = img.url; shot.alt = img.name || 'attachment';
      bub.appendChild(shot);
    }
    const body = document.createElement('div');
    body.textContent = m.pending ? m.pending : (m.role === 'user' ? m.text : activeText(m));
    bub.appendChild(body);

    el.append(who, bub);

    if (!m.pending) el.appendChild(toolbar(m, i, bub));
    wrap.appendChild(el);
  });

  thread.appendChild(wrap);
  if (keepScroll) thread.scrollTop = before;
  else if (atBottom) thread.scrollTop = thread.scrollHeight;
}

function toolbar(m, i, bub) {
  const bar = document.createElement('div');
  bar.className = 'tools';

  if (m.role === 'assistant') {
    const n = (m.swipes || []).length;
    const sw = document.createElement('div');
    sw.className = 'swipe';
    sw.appendChild(button('‹', 'Previous take', () => { m.active--; render(true); save(); }, busy || m.active <= 0));
    const lbl = document.createElement('span');
    lbl.className = 'n'; lbl.textContent = (m.active + 1) + ' / ' + n;
    sw.appendChild(lbl);
    // Right past the last take writes a new one, the way a chat client's swipe does.
    sw.appendChild(button('›', m.active >= n - 1 ? 'Write another take' : 'Next take',
      () => { if (m.active < n - 1) { m.active++; render(true); save(); } else regenerate(i); }, busy));
    bar.appendChild(sw);

    const s = activeSwipe(m);
    if (s) {
      const tag = document.createElement('span');
      if (missingDialogueTags(s.text)) { tag.className = 'tag bad'; tag.textContent = 'no <d> tags — speech would be silent'; }
      else if (tagCount(s.text))       { tag.className = 'tag ok';  tag.textContent = tagCount(s.text) + ' dialogue line(s)'; }
      else                             { tag.className = 'tag warn'; tag.textContent = 'no dialogue'; }
      bar.appendChild(tag);
      if (s.ms) {
        const t = document.createElement('span');
        t.className = 'tag'; t.style.color = 'var(--muted)'; t.style.borderColor = 'var(--border)';
        t.textContent = (s.ms / 1000).toFixed(1) + 's' + (s.attempts > 1 ? ' · ' + s.attempts + ' passes' : '');
        bar.appendChild(t);
      }
    }

    bar.appendChild(button('↻', 'Write another take', () => regenerate(i), busy));
    bar.appendChild(button('Copy', 'Copy this take', ev => {
      navigator.clipboard.writeText(activeText(m));
      const b = ev.target, t = b.textContent; b.textContent = 'Copied'; setTimeout(() => b.textContent = t, 1100);
    }));
  } else {
    bar.appendChild(button('Resend', 'Send this message again', () => resend(i), busy));
  }

  bar.appendChild(button('Edit', 'Edit in place', () => edit(m, bub), busy));
  bar.appendChild(button('Delete', 'Remove this message', () => {
    chat.messages.splice(i, m.role === 'user' && chat.messages[i + 1] && chat.messages[i + 1].role === 'assistant' ? 2 : 1);
    render(); save();
  }, busy));
  return bar;
}

function edit(m, bub) {
  // The text sits in its own node so attachments are not swept into the edit.
  bub = bub.lastChild || bub;
  bub.contentEditable = 'true';
  bub.focus();
  const done = () => {
    bub.contentEditable = 'false';
    const text = bub.innerText.trim();
    if (m.role === 'user') m.text = text;
    else { const s = activeSwipe(m); if (s) s.text = text; }
    render(true); save();
  };
  bub.onblur = done;
  bub.onkeydown = e => { if (e.key === 'Escape') { e.preventDefault(); bub.blur(); } };
}

// ---- building the request ------------------------------------------------------------------------
// generate() takes a flat prompt, so earlier turns are folded into it as a transcript. Each assistant
// turn contributes the take that is currently selected -- swiping changes what the next turn builds on.
function buildPrompt(upto) {
  const history = [];
  for (let i = 0; i < upto; i++) {
    const m = chat.messages[i];
    if (m.role === 'user') history.push('USER: ' + m.text);
    else if (activeText(m)) history.push('ASSISTANT (H3 prompt):\n' + activeText(m));
  }
  let request = chat.messages[upto].text;
  if ((chat.pinned || []).length) {
    request = 'The attached image is the first frame of the shot. Describe what is actually in it — '
            + 'subject, wardrobe, setting, framing and light — and open [Shot 1] on it.\n\n' + request;
  }
  if (!history.length) return request;
  return 'Conversation so far:\n\n' + history.join('\n\n') + '\n\n---\nNEW REQUEST: ' + request
       + '\n\n' + REVISION_NOTE;
}

// One take. Retries within itself when the answer comes back with untagged speech.
async function produce(userIdx, seed) {
  const cfg = readSettings();
  const system = cfg.mode === 'ref2va' ? REF2VA : FL2VA;
  const base = buildPrompt(userIdx);
  const started = performance.now();

  // Every quote anywhere in the thread: a follow-up like "make it shorter" carries no dialogue itself,
  // and the lines from the original request must still survive the rewrite.
  // Every generate call is stateless, so the frame has to travel with each one -- including the
  // regeneration of a turn several messages later, which is still describing that same frame.
  // The pinned set is the whole answer to "what can the model see" -- deliberately not also scraping
  // images off individual messages, because then unpinning would not actually stop them being sent and
  // the strip in the composer would no longer tell the truth. A message keeps its own images only as a
  // record of what was attached when it was written.
  const shots = (chat.pinned || []).map(img => img.path).filter(Boolean);
  const quotes = extractQuotes(chat.messages.filter(m => m.role === 'user').map(m => m.text).join('\n'));
  const must = quotes.length
    ? '\n\nThese exact words are spoken and MUST appear verbatim, each inside its own '
      + '<d>[Language] ...</d> tag: ' + quotes.map(q => '"' + q + '"').join(', ')
    : '';

  let text = '', attempts = 0;
  for (let i = 0; i < (cfg.enforce ? 4 : 1); i++) {
    attempts++;
    if (attempts > 1) setState('busy', 'dialogue was not tagged — repairing (attempt ' + attempts + ')');
    const first = attempts === 1;
    const res = await api('/api/generate', {
      prompt: first ? base + must
                    : 'Prompt to repair:\n\n' + text + must,
      system_prompt: first ? system : REPAIR_SYSTEM,
      max_new_tokens: cfg.tokens,
      // Only the writing pass needs to see the frame; the repair pass edits text it is already given,
      // and re-encoding the image for it costs time for nothing. Newest few only, to bound the context.
      image_paths: first ? shots.slice(-4) : [],
      // The repair pass is a near-mechanical edit, so it runs cold; sampling at the writing temperature
      // is what lets it wander off and reword the scene instead of just tagging the speech.
      temperature: first ? cfg.temp : 0.2,
      top_p: first ? 0.9 : 0.8,
      seed: first ? seed : seed + attempts,
    });
    const out = (res.text || '').trim();
    // A repair pass that came back worse than what it was given is not an improvement.
    if (!first && missingDialogueTags(out) && !missingDialogueTags(text)) break;
    text = out;
    if (!cfg.enforce || !missingDialogueTags(text)) break;
  }
  return { text, attempts, ms: Math.round(performance.now() - started), seed };
}

// Distinct seeds, so a second take is actually a different one rather than the same greedy answer.
const freshSeed = () => Math.floor(Math.random() * 2147483647);

async function run(userIdx, target, note) {
  if (busy) return;
  busy = true; $('send').disabled = true;
  if (!await ensureLoaded()) {
    busy = false; $('send').disabled = false;
    if (target) { target.pending = null; if (!target.swipes.length) chat.messages.pop(); }
    render();
    setState('off', 'The model is not loaded, so nothing can be written yet.');
    return;
  }
  setState('busy', note);
  try {
    const take = await produce(userIdx, freshSeed());
    target.swipes.push(take);
    target.active = target.swipes.length - 1;
  } catch (e) {
    setState('off', String(e.message || e));
    if (!target.swipes.length) chat.messages.splice(chat.messages.indexOf(target), 1);
  } finally {
    target.pending = null;
    busy = false; $('send').disabled = false;
    render(); save(true); refreshStatus();
  }
}

async function send() {
  const text = $('ask').value.trim();
  const images = pending.filter(i => !i.busy && i.path);
  if ((!text && !images.length) || busy) return;
  if (pending.some(i => i.busy)) return;   // an attachment is still uploading
  if (!chat) newChat();
  $('ask').value = ''; sizeAsk();
  pending = []; renderAttached();
  // Newly attached frames become the conversation's pinned set, replacing any earlier one: attaching a
  // different frame means the shot changed.
  if (images.length) { chat.pinned = images; renderPinned(); }
  chat.messages.push({ role:'user', text, images });
  const bot = { role:'assistant', swipes:[], active:0, pending:'writing…' };
  chat.messages.push(bot);
  render();
  await run(chat.messages.length - 2, bot, 'writing…');
}

// Another take of an existing answer, from the same context.
async function regenerate(botIdx) {
  const bot = chat.messages[botIdx];
  const userIdx = botIdx - 1;
  if (!bot || bot.role !== 'assistant' || userIdx < 0) return;
  bot.pending = 'writing another take…';
  render();
  await run(userIdx, bot, 'writing another take…');
}

// Re-run a user message: drops whatever followed it and answers again.
async function resend(userIdx) {
  chat.messages.length = userIdx + 1;
  const bot = { role:'assistant', swipes:[], active:0, pending:'writing…' };
  chat.messages.push(bot);
  render();
  await run(userIdx, bot, 'writing…');
}

// ---- wiring --------------------------------------------------------------------------------------
function sizeAsk() {
  const a = $('ask');
  a.style.height = 'auto';
  a.style.height = Math.min(a.scrollHeight, 210) + 'px';
}

$('send').onclick = send;
$('attach').onclick = () => $('file').click();
$('file').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
// Pasting a screenshot straight in is how a first frame usually arrives.
window.addEventListener('paste', e => {
  const files = [...(e.clipboardData && e.clipboardData.files || [])];
  if (files.length) { e.preventDefault(); addFiles(files); }
});
// Dropping onto the window, likewise.
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => {
  const files = [...(e.dataTransfer && e.dataTransfer.files || [])];
  if (files.length) { e.preventDefault(); addFiles(files); }
});
$('new-chat').onclick = newChat;
$('reload').onclick = async () => { setState('busy', 'loading model…'); await api('/api/load', {}).catch(()=>{}); refreshStatus(); };
$('ask').addEventListener('input', sizeAsk);
$('ask').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
for (const id of ['mode','temp','tokens','enforce']) $(id).addEventListener('change', () => save());

newChat();
loadIndex();
ensureLoaded();
// Comfortably inside the service's 60s idle window, so the model never unloads while this is open.
setInterval(keepAlive, 45000);
// A lighter poll so the page notices Maestro appearing (or moving) without waiting for the next ping.
setInterval(() => { if (!busy && $('dot').className.indexOf('on') === -1) refreshStatus(); }, 5000);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------------------------------
# Storage: one JSON file per chat.
# --------------------------------------------------------------------------------------------------
class Store:
    ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    MAX_IMAGE_BYTES = 16 * 1024 * 1024

    def __init__(self, root: Path):
        self.root = root
        self.uploads = root / "uploads"
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save_image(self, name: str, raw: bytes) -> dict:
        """Store an attachment and describe it both ways it will be needed.

        `url` is how this page shows it; `path` is the absolute filename handed to Maestro, which reads
        the file directly off disk rather than receiving the bytes.
        """
        if len(raw) > self.MAX_IMAGE_BYTES:
            raise ValueError(f"image is larger than {self.MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        ext = Path(str(name or "")).suffix.lower()
        if ext not in self.ALLOWED_IMAGE_EXT:
            raise ValueError(f"unsupported image type '{ext or name}'")
        stem = uuid.uuid4().hex[:16]
        path = self.uploads / f"{stem}{ext}"
        path.write_bytes(raw)
        return {"id": path.name, "url": f"/uploads/{path.name}", "path": str(path.resolve())}

    def image_path(self, name: str) -> Path | None:
        """Resolve an upload by name, refusing anything that points outside the uploads directory."""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(name or ""))
        if not safe or safe.startswith("."):
            return None
        path = (self.uploads / safe).resolve()
        if path.parent != self.uploads.resolve() or not path.is_file():
            return None
        return path

    @staticmethod
    def _safe(chat_id: str) -> str:
        # Ids come from the page, so they are only ever used after being reduced to this alphabet --
        # a path separator in there would otherwise write outside the store.
        return re.sub(r"[^A-Za-z0-9_-]", "", str(chat_id or ""))[:64]

    def _path(self, chat_id: str) -> Path | None:
        safe = self._safe(chat_id)
        return self.root / f"{safe}.json" if safe else None

    def list(self) -> list[dict]:
        out = []
        for f in self.root.glob("*.json"):
            try:
                data = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            out.append({
                "id": data.get("id") or f.stem,
                "title": data.get("title") or "Untitled",
                "updated": data.get("updated") or 0,
                "messages": len(data.get("messages") or []),
            })
        out.sort(key=lambda c: c["updated"], reverse=True)
        return out

    def get(self, chat_id: str) -> dict | None:
        path = self._path(chat_id)
        if not path or not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, chat: dict) -> str:
        chat_id = self._safe(chat.get("id")) or uuid.uuid4().hex[:12]
        chat["id"] = chat_id
        chat.setdefault("created", int(time.time() * 1000))
        chat["updated"] = int(time.time() * 1000)
        path = self.root / f"{chat_id}.json"
        with self._lock:
            # Written aside and moved into place so an interrupted save cannot truncate the chat that
            # was already there.
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(path)
        return chat_id

    def delete(self, chat_id: str) -> None:
        path = self._path(chat_id)
        if path and path.exists():
            path.unlink()


# --------------------------------------------------------------------------------------------------
# Maestro: found once, then remembered.
# --------------------------------------------------------------------------------------------------
class Maestro:
    """Forwards to Maestro's LLM endpoints.

    Maestro's port is assigned by Pinokio at launch, so the configured address can be stale -- typically
    when the studio is started first, or Maestro is restarted onto another port. Rather than fail, probe
    the range Pinokio hands out and adopt whichever instance answers.
    """

    PROBE_RANGE = range(42000, 42013)

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self._lock = threading.Lock()

    def _call(self, base: str, path: str, payload=None, timeout=600):
        url = base + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST" if data is not None else "GET",
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")

    def _rebind(self) -> bool:
        """Look for a live Maestro and remember where it was."""
        with self._lock:
            for port in self.PROBE_RANGE:
                candidate = f"http://127.0.0.1:{port}"
                if candidate == self.base:
                    continue
                try:
                    self._call(candidate, "/api/v1/llm/status", timeout=1.2)
                except Exception:
                    continue
                self.base = candidate
                return True
        return False

    def call(self, path: str, payload=None, timeout=600):
        try:
            return self._call(self.base, path, payload, timeout)
        except Exception:
            if not self._rebind():
                raise
            return self._call(self.base, path, payload, timeout)

    def status(self) -> dict:
        try:
            s = self.call("/api/v1/llm/status", timeout=4)
        except Exception:
            return {"reachable": False, "loaded": False, "maestro": self.base}
        loaded = s.get("loaded")
        if loaded is None:
            loaded = s.get("running") or s.get("is_loaded")
        return {"reachable": True, "loaded": bool(loaded),
                "model": s.get("model_id") or s.get("model") or "",
                "vision": bool(s.get("vision")), "maestro": self.base}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    maestro: Maestro
    store: Store

    # -- plumbing ---------------------------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Ports get recycled between apps, so a browser that once loaded a different app on this same
        # host:port will happily replay its cached shell here -- which looks like this page serving
        # someone else's broken UI. Nothing here is worth caching anyway.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802 - http.server's interface
        self._send(200, b"", "text/html; charset=utf-8")

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def log_message(self, *_args):
        pass

    # -- routes -----------------------------------------------------------------------------------
    def do_GET(self):  # noqa: N802 - http.server's interface
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(self.maestro.status())
        elif self.path == "/api/chats":
            self._json({"chats": self.store.list()})
        elif self.path.startswith("/uploads/"):
            path = self.store.image_path(self.path[len("/uploads/"):])
            if not path:
                self._json({"error": "not found"}, 404)
                return
            ctype, _ = mimetypes.guess_type(str(path))
            self._send(200, path.read_bytes(), ctype or "application/octet-stream")
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802 - http.server's interface
        body = self._body()
        try:
            if self.path == "/api/generate":
                # Generation is slow and the request must not be cut short under it, so no short timeout.
                self._json(self.maestro.call("/api/v1/llm/generate", body))
            elif self.path == "/api/load":
                self._json(self.maestro.call("/api/v1/llm/load", {}, timeout=600))
            elif self.path == "/api/keepalive":
                self._json(self.maestro.call("/api/v1/llm/generate", {
                    "prompt": "ok", "system_prompt": "Reply with one word.", "max_new_tokens": 1,
                }, timeout=120))
            elif self.path == "/api/upload":
                # Sent as base64 in JSON rather than multipart: the stdlib has no multipart parser worth
                # using here, and an attachment is a one-off small payload.
                import base64
                try:
                    raw = base64.b64decode(body.get("data", ""), validate=True)
                except Exception:
                    self._json({"error": "attachment was not valid base64"}, 400)
                    return
                try:
                    self._json(self.store.save_image(body.get("name", ""), raw))
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
            elif self.path == "/api/chats/save":
                self._json({"id": self.store.save(body)})
            elif self.path == "/api/chats/get":
                chat = self.store.get(body.get("id", ""))
                self._json(chat if chat else {"error": "no such chat"}, 200 if chat else 404)
            elif self.path == "/api/chats/delete":
                self.store.delete(body.get("id", ""))
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            self._json({"error": f"Maestro returned {e.code}: {detail}"}, 502)
        except Exception as e:
            self._json({"error": f"Maestro is unreachable ({e})"}, 502)


class Server(socketserver.ThreadingTCPServer):
    # Threaded because a generation occupies its connection for minutes, and the status poll and
    # keepalive still have to get through while it runs.
    daemon_threads = True
    allow_reuse_address = True


def free_port(preferred: int) -> int:
    """First free port at or above `preferred`.

    Deliberately outside the 42000-42012 band Pinokio hands out and recycles between apps: landing on a
    port another app recently held means inheriting its cached pages in the browser, which presents as
    this page serving that app's UI.
    """
    import socket
    for candidate in range(preferred, preferred + 60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise SystemExit(f"no free port in {preferred}-{preferred + 60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat GUI for writing MiniMax H3 prompts.")
    parser.add_argument("--port", type=int, default=0,
                        help="port to serve the page on (default: first free port from 42777)")
    parser.add_argument("--maestro", default="auto",
                        help="the running Maestro instance, or 'auto' to find it")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="where saved chats are kept")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args()

    # "auto" (or an empty/None-ish value) means no address was supplied, so start from the usual one and
    # let the prober find the real instance.
    configured = (args.maestro or "").strip()
    if configured.lower() in ("", "auto", "null", "none", "undefined"):
        configured = "http://127.0.0.1:42003"
    Handler.maestro = Maestro(configured)
    Handler.store = Store(Path(args.data_dir))
    port = args.port or free_port(42777)
    url = f"http://127.0.0.1:{port}/"

    with Server(("127.0.0.1", port), Handler) as httpd:
        # Printed on its own marked line so a launcher can capture this URL specifically. The command
        # line that started this process also contains an http:// address (--maestro), and a shell echoes
        # that before any output here, so a bare URL pattern would match the wrong one.
        # flush=True because stdout is block-buffered when it is a pipe rather than a terminal, which is
        # exactly how a launcher runs this. Without it the URL sits in the buffer and the launcher waits
        # forever for a line that has already been written.
        print(f"PROMPT_STUDIO_URL {url}", flush=True)
        print(f"H3 Prompt Studio  ->  {url}", flush=True)
        print(f"talking to Maestro at {Handler.maestro.base}", flush=True)
        print(f"chats saved in {Handler.store.root}", flush=True)
        print("the model stays resident while this page is open; Ctrl+C to stop", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
