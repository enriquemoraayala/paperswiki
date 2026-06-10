#!/usr/bin/env python3
"""
viewer.py — local markdown viewer for rndresearch summaries.

Usage:
    python3 viewer.py [--port 8080] [--summaries summaries/]

Pages:
  /         Index of all papers — filter by status, live search.
  /paper/<slug>  Full rendered summary with status selector.
  /search   Run the arXiv agent with configurable parameters;
            edit research_topics.yaml directly in the browser.

Paper statuses saved to viewer_state.json (git-ignored).
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    import yaml as _yaml
    def _validate_yaml(text):
        try:
            _yaml.safe_load(text)
            return True, ""
        except Exception as e:
            return False, str(e)
except ImportError:
    def _validate_yaml(text):
        return True, ""

_MATH_BLOCK_RE   = re.compile(r'\$\$[\s\S]+?\$\$')
_MATH_INLINE_RE  = re.compile(r'\$(?!\$).+?(?<!\$)\$')
_MATH_ENV_RE     = re.compile(r'\\begin\{[^}]+\}[\s\S]*?\\end\{[^}]+\}', re.DOTALL)
_MATH_PAREN_RE   = re.compile(r'\\\([\s\S]*?\\\)')   # \( ... \)  inline
_MATH_BRACKET_RE = re.compile(r'\\\[[\s\S]*?\\\]')   # \[ ... \]  display

def _protect_math(text):
    """Replace LaTeX math blocks with placeholders so markdown doesn't mangle them."""
    placeholders = {}
    counter = [0]
    def _sub(m):
        key = f"\x00MATH{counter[0]}\x00"
        placeholders[key] = m.group(0)
        counter[0] += 1
        return key
    # Order matters: longest/greediest patterns first
    text = _MATH_ENV_RE.sub(_sub, text)
    text = _MATH_BLOCK_RE.sub(_sub, text)
    text = _MATH_BRACKET_RE.sub(_sub, text)
    text = _MATH_PAREN_RE.sub(_sub, text)
    text = _MATH_INLINE_RE.sub(_sub, text)
    return text, placeholders

def _restore_math(html, placeholders):
    for key, val in placeholders.items():
        html = html.replace(key, val)
    return html

try:
    import markdown as _md_lib
    def _render_md(text):
        text, ph = _protect_math(text)
        html = _md_lib.markdown(text, extensions=["tables", "fenced_code"])
        return _restore_math(html, ph)
except ImportError:
    print("[viewer] 'markdown' package not found — run: pip3 install markdown")
    def _render_md(text):
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre style='white-space:pre-wrap'>{esc}</pre>"

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHF]')
def _strip_ansi(s):
    return _ANSI_RE.sub('', s)

# ── constants ──────────────────────────────────────────────────────────────

STATES = {
    "unread":           {"label": "No leído",              "icon": "📬", "color": "#6e7781"},
    "interesting":      {"label": "Interesante",           "icon": "💡", "color": "#9a6700"},
    "favourite":        {"label": "Favorito",              "icon": "⭐", "color": "#cf222e"},
    "pending_analysis": {"label": "Pendiente de analizar", "icon": "🔎", "color": "#0550ae"},
    "analyzed":         {"label": "Analizado",             "icon": "🧪", "color": "#1a7f37"},
    "discarded":        {"label": "Descartado",            "icon": "🗑️", "color": "#8250df"},
}
DEFAULT_STATE = "unread"

# ── state persistence ──────────────────────────────────────────────────────

def _states_file(summaries_dir):
    return summaries_dir.parent / "viewer_state.json"

def load_states(summaries_dir):
    f = _states_file(summaries_dir)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(summaries_dir, states, slug, state):
    # "unread" is the default — omit it from the JSON so new papers
    # are automatically unread without any explicit entry.
    if state == DEFAULT_STATE:
        states.pop(slug, None)
    else:
        states[slug] = state
    _states_file(summaries_dir).write_text(
        json.dumps(states, indent=2, ensure_ascii=False), encoding="utf-8"
    )

# ── markdown parsing ───────────────────────────────────────────────────────

_FIELD_RE      = re.compile(r"\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|")
_LINK_RE       = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_ARXIV_HREF_RE = re.compile(r"\(https://arxiv\.org/abs/([^)]+)\)")

def _strip_links(text):
    return _LINK_RE.sub(r"\1", text)

def parse_summary(path):
    text  = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = path.stem
    for line in lines:
        if line.startswith("# "):
            title = line.lstrip("# ").strip()
            break

    meta = {}
    for line in lines:
        m = _FIELD_RE.match(line)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip()

    arxiv_raw = meta.get("arXiv ID", "")
    m2 = _ARXIV_HREF_RE.search(arxiv_raw)
    arxiv_id  = m2.group(1) if m2 else _strip_links(arxiv_raw)

    abstract = ""
    am = re.search(r"##\s*📄 Abstract\s*\n(.*?)(?=^##|\Z)", text, re.DOTALL | re.MULTILINE)
    if am:
        raw = am.group(1).strip()
        abstract = " ".join(
            line.lstrip("> ").strip() for line in raw.splitlines() if line.strip()
        )

    return {
        "slug":      path.stem,
        "title":     title,
        "date":      _strip_links(meta.get("Published", path.stem[:10])),
        "authors":   _strip_links(meta.get("Authors", "")),
        "arxiv_id":  arxiv_id,
        "abstract":  abstract,
        "full_html": _render_md(text),
    }

def load_summaries(summaries_dir):
    papers = []
    for p in sorted(summaries_dir.glob("*.md"), reverse=True):
        try:
            papers.append(parse_summary(p))
        except Exception as exc:
            print(f"[viewer] Warning: could not parse {p.name}: {exc}")
    return papers

# ── agent runner ───────────────────────────────────────────────────────────

_run = {"running": False, "output": [], "exit_code": None}
_run_lock = threading.Lock()

def start_agent(params, agent_path, summaries_dir):
    """Launch the agent in a daemon thread. Returns False if already running."""
    with _run_lock:
        if _run["running"]:
            return False
        _run["running"]   = True
        _run["output"]    = []
        _run["exit_code"] = None

    cmd = [sys.executable, str(agent_path)]
    if params.get("days"):
        cmd += ["--days", str(int(params["days"]))]
    prov = params.get("provider", "auto")
    if prov and prov != "auto":
        cmd += ["--provider", prov]
    if params.get("model", "").strip():
        cmd += ["--model", params["model"].strip()]
    if params.get("topic_mode", "any") != "any":
        cmd += ["--topic-mode", params["topic_mode"]]
    if params.get("advanced_filter"):
        cmd += ["--advanced-filter"]
    if params.get("dry_run"):
        cmd += ["--dry-run"]

    def _thread():
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(agent_path.parent),
            )
            for raw in iter(proc.stdout.readline, ""):
                with _run_lock:
                    _run["output"].append(_strip_ansi(raw.rstrip()))
            proc.wait()
            code = proc.returncode
        except Exception as exc:
            with _run_lock:
                _run["output"].append(f"[ERROR] {exc}")
            code = -1

        with _run_lock:
            _run["exit_code"] = code
            _run["running"]   = False

        # Reload in-memory papers after agent finishes
        try:
            papers = load_summaries(summaries_dir)
            Handler.papers  = papers
            Handler.by_slug = {p["slug"]: p for p in papers}
        except Exception:
            pass

    threading.Thread(target=_thread, daemon=True).start()
    return True

# ── deep research runner ───────────────────────────────────────────────────

_deep_run = {"running": False, "output": [], "exit_code": None}
_deep_run_lock = threading.Lock()

def start_deep_agent(params, agent_path, summaries_dir, prompt_file):
    """Launch deep_research.py in a daemon thread. Returns False if already running."""
    with _deep_run_lock:
        if _deep_run["running"]:
            return False
        _deep_run["running"]   = True
        _deep_run["output"]    = []
        _deep_run["exit_code"] = None

    cmd = [sys.executable, str(agent_path)]
    prov = params.get("provider", "auto")
    if prov and prov != "auto":
        cmd += ["--provider", prov]
    if params.get("model", "").strip():
        cmd += ["--model", params["model"].strip()]
    if params.get("dry_run"):
        cmd += ["--dry-run"]
    cmd += ["--prompt", str(prompt_file)]
    cmd += ["--summaries", str(summaries_dir)]
    cmd += ["--state-file", str(summaries_dir.parent / "viewer_state.json")]

    def _thread():
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(agent_path.parent),
            )
            for raw in iter(proc.stdout.readline, ""):
                with _deep_run_lock:
                    _deep_run["output"].append(_strip_ansi(raw.rstrip()))
            proc.wait()
            code = proc.returncode
        except Exception as exc:
            with _deep_run_lock:
                _deep_run["output"].append(f"[ERROR] {exc}")
            code = -1

        with _deep_run_lock:
            _deep_run["exit_code"] = code
            _deep_run["running"]   = False

        # Reload states and papers after analysis finishes
        try:
            new_states = load_states(summaries_dir)
            Handler.states = new_states
            papers = load_summaries(summaries_dir)
            Handler.papers  = papers
            Handler.by_slug = {p["slug"]: p for p in papers}
        except Exception:
            pass

    threading.Thread(target=_thread, daemon=True).start()
    return True

_CSS = r"""
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: #f6f8fa; color: #1f2328; line-height: 1.65; font-size: 15px;
}
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── header ── */
.site-header {
  background: #1f2328; color: #fff; padding: 0 28px;
  display: flex; align-items: center; justify-content: space-between;
  height: 52px;
}
.site-header .brand { font-size: 1.05rem; font-weight: 700; letter-spacing: -.2px; white-space: nowrap; }
.site-header .brand span { opacity: .55; font-weight: 400; font-size: .82rem; margin-left: 7px; }
.site-nav { display: flex; gap: 4px; }
.nav-link {
  color: rgba(255,255,255,.7); padding: 6px 14px; border-radius: 6px;
  font-size: .88rem; font-weight: 500; transition: background .15s, color .15s;
}
.nav-link:hover { background: rgba(255,255,255,.1); color: #fff; text-decoration: none; }
.nav-link.active { background: rgba(255,255,255,.15); color: #fff; }

/* ── layout ── */
.container { max-width: 920px; margin: 28px auto; padding: 0 20px; }

/* ── toolbar (index) ── */
.toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
.toolbar input[type=search] {
  flex: 1 1 200px; padding: 8px 14px; font-size: .95rem;
  border: 1px solid #d0d7de; border-radius: 6px; outline: none; background: #fff;
}
.toolbar input[type=search]:focus { border-color: #0969da; box-shadow: 0 0 0 3px rgba(9,105,218,.15); }
.filter-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.ftab {
  padding: 5px 14px; border-radius: 20px; font-size: .82rem; font-weight: 500;
  cursor: pointer; border: 1.5px solid #d0d7de; background: #fff; color: #57606a;
  transition: all .15s; white-space: nowrap;
}
.ftab:hover { border-color: #0969da; color: #0969da; }
.ftab.active { background: #0969da; border-color: #0969da; color: #fff; }
.ftab .cnt { opacity: .75; margin-left: 4px; }
.summary-line { font-size: .82rem; color: #57606a; margin-bottom: 14px; }

/* ── cards ── */
.card {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
  padding: 18px 22px; margin-bottom: 14px;
  transition: box-shadow .15s, border-color .15s;
}
.card:hover { box-shadow: 0 3px 10px rgba(0,0,0,.07); border-color: #b0bec5; }
.card-top   { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.card-title { font-size: 1rem; font-weight: 600; margin-bottom: 5px; flex: 1; }
.card-meta  { font-size: .78rem; color: #57606a; margin-bottom: 9px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.arxiv-link {
  display: inline-flex; align-items: center; gap: 3px;
  background: #ddf4ff; color: #0550ae; padding: 1px 7px;
  border-radius: 12px; font-size: .75rem; font-weight: 500;
}
.arxiv-link:hover { background: #b6e3ff; text-decoration: none; }
.card-abstract { font-size: .88rem; color: #444d56; line-height: 1.5; }
.state-badge {
  display: inline-block; padding: 3px 10px; border-radius: 12px;
  font-size: .73rem; font-weight: 600; white-space: nowrap; flex-shrink: 0;
  border: 1.5px solid currentColor;
}
.state-selector { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 10px; }
.ssbtn {
  padding: 3px 10px; border-radius: 12px; font-size: .75rem; font-weight: 500;
  cursor: pointer; border: 1.5px solid #d0d7de; background: #fff; color: #57606a;
  transition: all .12s;
}
.ssbtn:hover { border-color: #0969da; color: #0969da; }

/* ── detail page ── */
.back-row { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
.back-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
  border: 1px solid #d0d7de; border-radius: 6px; font-size: .88rem;
  background: #fff; color: #24292f; text-decoration: none; transition: background .12s;
}
.back-btn:hover { background: #f3f4f6; text-decoration: none; }
.arxiv-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
  border-radius: 6px; font-size: .88rem; font-weight: 600;
  background: #ddf4ff; color: #0550ae; border: 1px solid #79c0ff;
  text-decoration: none; transition: background .12s;
}
.arxiv-btn:hover { background: #b6e3ff; text-decoration: none; }
.detail-state { margin-bottom: 20px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.detail-state label { font-size: .83rem; font-weight: 600; color: #57606a; }
.paper-body {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 32px 38px;
}
.paper-body h1 { font-size: 1.55rem; font-weight: 700; margin-bottom: 18px; line-height: 1.3; }
.paper-body h2 { font-size: 1.08rem; font-weight: 700; margin: 26px 0 8px;
  padding-bottom: 5px; border-bottom: 1px solid #eaeef2; }
.paper-body h3 { font-size: .95rem; font-weight: 600; margin: 16px 0 6px; }
.paper-body p  { margin-bottom: 12px; }
.paper-body ul, .paper-body ol { padding-left: 22px; margin-bottom: 12px; }
.paper-body li { margin-bottom: 4px; }
.paper-body table { border-collapse: collapse; margin-bottom: 16px; }
.paper-body th, .paper-body td { border: 1px solid #d0d7de; padding: 6px 14px; font-size: .88rem; text-align: left; }
.paper-body th { background: #f6f8fa; font-weight: 600; }
.paper-body blockquote { border-left: 4px solid #d0d7de; padding: 6px 16px; color: #57606a; margin: 10px 0; background: #f6f8fa; border-radius: 0 4px 4px 0; }
.paper-body pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 14px; overflow-x: auto; margin-bottom: 14px; }
.paper-body code { font-family: "SFMono-Regular", Consolas, monospace; font-size: .85em; background: #f6f8fa; padding: 1px 5px; border-radius: 3px; border: 1px solid #e8ecf0; }
.paper-body pre code { background: none; border: none; padding: 0; font-size: .88em; }
.paper-body a { color: #0969da; }

/* ── search page ── */
.search-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}
@media (max-width: 700px) { .search-layout { grid-template-columns: 1fr; } }

.panel {
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 22px 24px;
}
.panel h2 { font-size: 1rem; font-weight: 700; margin-bottom: 16px; color: #1f2328; }

.form-row { margin-bottom: 14px; }
.form-row label { display: block; font-size: .83rem; font-weight: 600; color: #57606a; margin-bottom: 5px; }
.form-row input[type=number],
.form-row input[type=text],
.form-row select {
  width: 100%; padding: 7px 10px; font-size: .9rem;
  border: 1px solid #d0d7de; border-radius: 6px; outline: none; background: #fff;
}
.form-row input:focus, .form-row select:focus { border-color: #0969da; box-shadow: 0 0 0 3px rgba(9,105,218,.12); }
.form-row .hint { font-size: .75rem; color: #8c959f; margin-top: 3px; }
.radio-group { display: flex; gap: 16px; margin-top: 4px; }
.radio-group label { display: flex; align-items: center; gap: 6px; font-weight: 400; color: #1f2328; font-size: .88rem; cursor: pointer; }
.check-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.check-row label { font-size: .88rem; color: #1f2328; cursor: pointer; }
.check-row input[type=checkbox] { width: 15px; height: 15px; cursor: pointer; }

.run-btn {
  width: 100%; padding: 10px; margin-top: 6px;
  background: #1f883d; color: #fff; border: none; border-radius: 6px;
  font-size: .95rem; font-weight: 600; cursor: pointer; transition: background .15s;
}
.run-btn:hover:not(:disabled) { background: #1a7f37; }
.run-btn:disabled { background: #8c959f; cursor: not-allowed; }

.yaml-editor {
  width: 100%; min-height: 340px; resize: vertical;
  font-family: "SFMono-Regular", Consolas, monospace; font-size: .82rem;
  padding: 10px 12px; border: 1px solid #d0d7de; border-radius: 6px;
  background: #f6f8fa; outline: none; line-height: 1.5;
}
.yaml-editor:focus { border-color: #0969da; background: #fff; box-shadow: 0 0 0 3px rgba(9,105,218,.12); }
.yaml-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.save-btn {
  padding: 7px 18px; background: #0969da; color: #fff; border: none;
  border-radius: 6px; font-size: .88rem; font-weight: 600; cursor: pointer; transition: background .15s;
}
.save-btn:hover:not(:disabled) { background: #0860ca; }
.save-btn:disabled { background: #8c959f; cursor: not-allowed; }
.status-msg { font-size: .83rem; }
.status-msg.success { color: #1a7f37; }
.status-msg.error   { color: #cf222e; }
.status-msg.info    { color: #57606a; }

/* console */
.console-panel { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; overflow: hidden; }
.console-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 18px; border-bottom: 1px solid #d0d7de; background: #f6f8fa; }
.console-header h2 { font-size: .95rem; font-weight: 700; }
.console-output {
  background: #0d1117; color: #e6edf3;
  font-family: "SFMono-Regular", Consolas, monospace; font-size: .82rem;
  padding: 16px 18px; min-height: 200px; max-height: 480px;
  overflow-y: auto; white-space: pre-wrap; word-break: break-all; line-height: 1.6;
}
.done-msg { padding: 10px 18px; font-size: .88rem; font-weight: 600; }
.done-msg.success { background: #dafbe1; color: #1a7f37; }
.done-msg.warning { background: #fff8c5; color: #9a6700; }
.done-msg.error   { background: #ffebe9; color: #cf222e; }
.running-indicator { display: inline-flex; align-items: center; gap: 6px; font-size: .8rem; color: #1a7f37; font-weight: 600; }
.dot-pulse { width: 8px; height: 8px; border-radius: 50%; background: #1a7f37; animation: pulse 1.2s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .3; } }

/* ── deep analysis section (rendered in paper detail) ── */
.deep-analysis-section { margin-top: 32px; }
.deep-analysis-section h2 { color: #1a7f37; border-bottom-color: #d1f0d9; }

/* ── pending papers list (deep research page) ── */
.pending-list { margin-bottom: 20px; }
.pending-item {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border: 1px solid #d0d7de; border-radius: 6px;
  padding: 10px 16px; margin-bottom: 8px; gap: 12px;
}
.pending-item .pi-title { font-size: .9rem; font-weight: 600; flex: 1; }
.pending-item .pi-meta  { font-size: .78rem; color: #57606a; }
.empty-state { text-align: center; padding: 32px; color: #57606a; font-size: .9rem; background: #fff; border: 1px dashed #d0d7de; border-radius: 8px; }
"""

# ── JavaScript ─────────────────────────────────────────────────────────────

_INDEX_JS = r"""
const ALL_PAPERS = JSON.parse(document.getElementById('papers-data').textContent);
let currentFilter = 'all';
let currentSearch = '';

function renderCards() {
  const list = document.getElementById('card-list');
  list.innerHTML = '';
  let shown = 0;
  ALL_PAPERS.forEach(p => {
    const state = currentStates[p.slug] || 'unread';
    if (currentFilter !== 'all' && state !== currentFilter) return;
    const q = currentSearch.toLowerCase();
    if (q && !p.title.toLowerCase().includes(q) && !p.abstract.toLowerCase().includes(q) && !p.authors.toLowerCase().includes(q)) return;
    shown++;
    list.insertAdjacentHTML('beforeend', cardHTML(p, state));
  });
  document.getElementById('shown-count').textContent = shown;
}

function cardHTML(p, state) {
  const s = STATES[state];
  const badgeStyle = `color:${s.color};border-color:${s.color};`;
  const arxivHref  = p.arxiv_id ? `https://arxiv.org/abs/${p.arxiv_id}` : '';
  const arxivLink  = arxivHref
    ? `<a class="arxiv-link" href="${arxivHref}" target="_blank" onclick="event.stopPropagation()">arXiv ↗ ${p.arxiv_id}</a>`
    : '';
  const ssBtns = Object.entries(STATES).map(([k, v]) =>
    `<button class="ssbtn${k===state?' active':''}" style="${k===state?`color:${v.color};border-color:${v.color};`:''}"` +
    ` onclick="setState('${p.slug}','${k}')" data-state="${k}">${v.icon} ${v.label}</button>`
  ).join('');
  const authShort = p.authors.length > 90 ? p.authors.slice(0,90)+'…' : p.authors;
  const absPrev   = p.abstract.length > 380 ? p.abstract.slice(0,380)+'…' : p.abstract;
  return `
  <div class="card" data-slug="${p.slug}">
    <div class="card-top">
      <div class="card-title"><a href="/paper/${p.slug}">${escHtml(p.title)}</a></div>
      <span class="state-badge" style="${badgeStyle}">${s.icon} ${s.label}</span>
    </div>
    <div class="card-meta">
      <span>📅 ${p.date}</span>
      <span>👤 ${escHtml(authShort)}</span>
      ${arxivLink}
    </div>
    <div class="card-abstract">${escHtml(absPrev)}</div>
    <div class="state-selector">${ssBtns}</div>
  </div>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setState(slug, state) {
  fetch(`/api/state/${slug}`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({state})
  }).then(r => r.json()).then(d => {
    if (d.ok) { currentStates[slug] = state; updateTabCounts(); renderCards(); }
  });
}

function updateTabCounts() {
  const counts = {all: ALL_PAPERS.length};
  Object.keys(STATES).forEach(k => counts[k] = 0);
  ALL_PAPERS.forEach(p => { const s = currentStates[p.slug] || 'unread'; if (counts[s] !== undefined) counts[s]++; });
  document.querySelectorAll('.ftab').forEach(tab => {
    const f = tab.dataset.filter;
    tab.querySelector('.cnt').textContent = `(${counts[f] ?? 0})`;
  });
}

document.getElementById('search').addEventListener('input', e => { currentSearch = e.target.value; renderCards(); });
document.querySelectorAll('.ftab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.ftab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    currentFilter = tab.dataset.filter;
    renderCards();
  });
});

updateTabCounts();
renderCards();
"""

_DETAIL_JS = r"""
function setState(slug, state) {
  fetch(`/api/state/${slug}`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({state})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      document.querySelectorAll('.ssbtn').forEach(b => {
        const active = b.dataset.state === state;
        b.classList.toggle('active', active);
        const s = STATES[b.dataset.state];
        b.style.color = active ? s.color : '';
        b.style.borderColor = active ? s.color : '';
      });
      const badge = document.getElementById('state-badge');
      if (badge) {
        const s = STATES[state];
        badge.textContent = s.icon + ' ' + s.label;
        badge.style.color = s.color;
        badge.style.borderColor = s.color;
      }
    }
  });
}
"""

_SEARCH_JS = r"""
async function loadTopics() {
  try {
    const r = await fetch('/api/topics');
    const d = await r.json();
    document.getElementById('yaml-editor').value = d.yaml;
    showYamlMsg('', '');
  } catch(e) {
    showYamlMsg('Error cargando el fichero de topics', 'error');
  }
}

async function saveTopics() {
  const yaml = document.getElementById('yaml-editor').value;
  const btn  = document.getElementById('save-yaml-btn');
  btn.disabled = true; btn.textContent = 'Guardando…';
  try {
    const r = await fetch('/api/topics', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({yaml})
    });
    const d = await r.json();
    showYamlMsg(d.ok ? '✅ Topics guardados correctamente' : '❌ Error: ' + d.error, d.ok ? 'success' : 'error');
  } catch(e) {
    showYamlMsg('❌ Error de red', 'error');
  }
  btn.disabled = false; btn.textContent = '💾 Guardar topics';
}

function showYamlMsg(text, cls) {
  const el = document.getElementById('yaml-status');
  el.textContent = text; el.className = 'status-msg ' + cls;
}

async function runAgent() {
  const data = {
    days:            parseInt(document.getElementById('days').value) || 7,
    provider:        document.getElementById('provider').value,
    model:           document.getElementById('model').value.trim(),
    topic_mode:      document.querySelector('input[name=topic_mode]:checked').value,
    advanced_filter: document.getElementById('advanced_filter').checked,
    dry_run:         document.getElementById('dry_run').checked,
  };
  const runBtn = document.getElementById('run-btn');
  runBtn.disabled = true; runBtn.textContent = '⏳ Iniciando…';
  try {
    const r = await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await r.json();
    if (d.ok) {
      startStreaming();
    } else {
      alert('No se puede iniciar: ' + d.error);
      runBtn.disabled = false; runBtn.textContent = '▶ Ejecutar agente';
    }
  } catch(e) {
    alert('Error de red al iniciar el agente');
    runBtn.disabled = false; runBtn.textContent = '▶ Ejecutar agente';
  }
}

let _es = null;
function startStreaming() {
  if (_es) { _es.close(); }
  const output  = document.getElementById('console-output');
  const panel   = document.getElementById('console-panel');
  const doneMsg = document.getElementById('done-msg');
  const runBtn  = document.getElementById('run-btn');
  const indic   = document.getElementById('run-indicator');

  output.textContent = '';
  panel.style.display = '';
  doneMsg.style.display = 'none';
  doneMsg.className = 'done-msg';
  runBtn.disabled = true; runBtn.textContent = '⏳ Ejecutando…';
  indic.style.display = '';
  panel.scrollIntoView({behavior: 'smooth', block: 'start'});

  _es = new EventSource('/api/run/stream');
  _es.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.line !== undefined) {
      output.textContent += d.line + '\n';
      output.scrollTop = output.scrollHeight;
    }
    if (d.done) {
      _es.close(); _es = null;
      indic.style.display = 'none';
      runBtn.disabled = false; runBtn.textContent = '▶ Ejecutar agente';
      const ok = d.exit_code === 0;
      doneMsg.textContent = ok
        ? '✅ Agente completado. Los nuevos papers aparecen en el índice.'
        : '⚠️ El agente terminó con código ' + d.exit_code + ' (puede haber errores arriba).';
      doneMsg.className = 'done-msg ' + (ok ? 'success' : 'warning');
      doneMsg.style.display = '';
    }
  };
  _es.onerror = function() {
    _es.close(); _es = null;
    indic.style.display = 'none';
    runBtn.disabled = false; runBtn.textContent = '▶ Ejecutar agente';
  };
}

async function checkRunStatus() {
  try {
    const r = await fetch('/api/run/status');
    const d = await r.json();
    if (d.running) startStreaming();
  } catch(e) {}
}

loadTopics();
checkRunStatus();
"""

_DEEP_JS = r"""
async function loadDeepPrompt() {
  try {
    const r = await fetch('/api/deep-prompt');
    const d = await r.json();
    document.getElementById('prompt-editor').value = d.yaml;
    showPromptMsg('', '');
  } catch(e) {
    showPromptMsg('Error cargando el fichero de prompt', 'error');
  }
}

async function saveDeepPrompt() {
  const yaml = document.getElementById('prompt-editor').value;
  const btn  = document.getElementById('save-prompt-btn');
  btn.disabled = true; btn.textContent = 'Guardando…';
  try {
    const r = await fetch('/api/deep-prompt', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({yaml})
    });
    const d = await r.json();
    showPromptMsg(d.ok ? '✅ Prompt guardado' : '❌ Error: ' + d.error, d.ok ? 'success' : 'error');
  } catch(e) {
    showPromptMsg('❌ Error de red', 'error');
  }
  btn.disabled = false; btn.textContent = '💾 Guardar prompt';
}

function showPromptMsg(text, cls) {
  const el = document.getElementById('prompt-status');
  el.textContent = text; el.className = 'status-msg ' + cls;
}

async function runDeep() {
  const data = {
    provider: document.getElementById('deep-provider').value,
    model:    document.getElementById('deep-model').value.trim(),
    dry_run:  document.getElementById('deep-dry-run').checked,
  };
  const runBtn = document.getElementById('deep-run-btn');
  runBtn.disabled = true; runBtn.textContent = '⏳ Iniciando…';
  try {
    const r = await fetch('/api/deep-run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await r.json();
    if (d.ok) {
      startDeepStreaming();
    } else {
      alert('No se puede iniciar: ' + d.error);
      runBtn.disabled = false; runBtn.textContent = '🔬 Lanzar análisis';
    }
  } catch(e) {
    alert('Error de red al iniciar el agente');
    runBtn.disabled = false; runBtn.textContent = '🔬 Lanzar análisis';
  }
}

let _des = null;
function startDeepStreaming() {
  if (_des) { _des.close(); }
  const output  = document.getElementById('deep-console-output');
  const panel   = document.getElementById('deep-console-panel');
  const doneMsg = document.getElementById('deep-done-msg');
  const runBtn  = document.getElementById('deep-run-btn');
  const indic   = document.getElementById('deep-run-indicator');

  output.textContent = '';
  panel.style.display = '';
  doneMsg.style.display = 'none';
  doneMsg.className = 'done-msg';
  runBtn.disabled = true; runBtn.textContent = '⏳ Analizando…';
  indic.style.display = '';
  panel.scrollIntoView({behavior: 'smooth', block: 'start'});

  _des = new EventSource('/api/deep-run/stream');
  _des.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.line !== undefined) {
      output.textContent += d.line + '\n';
      output.scrollTop = output.scrollHeight;
    }
    if (d.done) {
      _des.close(); _des = null;
      indic.style.display = 'none';
      runBtn.disabled = false; runBtn.textContent = '🔬 Lanzar análisis';
      const ok = d.exit_code === 0;
      doneMsg.textContent = ok
        ? '✅ Análisis completado. Abre los papers analizados para ver los resultados.'
        : '⚠️ El agente terminó con código ' + d.exit_code;
      doneMsg.className = 'done-msg ' + (ok ? 'success' : 'warning');
      doneMsg.style.display = '';
    }
  };
  _des.onerror = function() {
    _des.close(); _des = null;
    indic.style.display = 'none';
    runBtn.disabled = false; runBtn.textContent = '🔬 Lanzar análisis';
  };
}

async function checkDeepStatus() {
  try {
    const r = await fetch('/api/deep-run/status');
    const d = await r.json();
    if (d.running) startDeepStreaming();
  } catch(e) {}
}

loadDeepPrompt();
checkDeepStatus();
"""

# ── page builders ──────────────────────────────────────────────────────────

def _page(title, body, active_nav, extra_js="", states_const="{}"):
    states_js = (
        f"const STATES = {json.dumps(STATES, ensure_ascii=False)};\n"
        f"const currentStates = {states_const};"
    )
    nav_items = [
        ("/",              "📋 Papers",        "index"),
        ("/search",        "🔍 Arxiv Search",  "search"),
        ("/deep-research", "🧪 Deep Research", "deep"),
    ]
    nav_html = "".join(
        f'<a href="{href}" class="nav-link{"active" if key == active_nav else ""}">{label}</a>'
        for href, label, key in nav_items
    )
    return (
        "<!DOCTYPE html>\n<html lang='es'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        f"<title>{title}</title>\n"
        f"<style>{_CSS}</style>\n"
        "<script>MathJax = {"
        "  tex: {"
        "    inlineMath: [['$','$'],['\\\\(','\\\\)']],"
        "    displayMath: [['$$','$$'],['\\\\[','\\\\]']],"
        "    packages: {'[+]': ['ams']},"
        "    tags: 'ams'"
        "  },"
        "  options: { skipHtmlTags: ['script','noscript','style','textarea','pre','code'] }"
        "};</script>\n"
        "<script src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js'"
        " id='MathJax-script' async></script>\n"
        "</head>\n<body>\n"
        "<header class='site-header'>\n"
        "  <div class='brand'>🔬 rndresearch <span>paper viewer</span></div>\n"
        f"  <nav class='site-nav'>{nav_html}</nav>\n"
        "</header>\n"
        f"<div class='container'>\n{body}\n</div>\n"
        f"<script>{states_js}</script>\n"
        + (f"<script>{extra_js}</script>\n" if extra_js else "")
        + "</body>\n</html>"
    ).encode("utf-8")


def _index_page(papers, states):
    papers_json = json.dumps(
        [{k: p[k] for k in ("slug","title","date","authors","arxiv_id","abstract")} for p in papers],
        ensure_ascii=False
    )
    filter_tabs = (
        '<div class="filter-tabs">'
        + "".join(
            f'<button class="ftab{"active" if f=="all" else ""}" data-filter="{f}">'
            f'{"📋" if f=="all" else STATES[f]["icon"]} '
            f'{"Todos" if f=="all" else STATES[f]["label"]}'
            f'<span class="cnt"></span></button>'
            for f in ["all"] + list(STATES.keys())
        )
        + '</div>'
    )
    body = (
        f'<div class="toolbar">'
        f'  <input id="search" type="search" placeholder="🔍 Buscar papers…" autofocus>'
        f'  {filter_tabs}'
        f'</div>'
        f'<p class="summary-line"><span id="shown-count">0</span> de {len(papers)} papers</p>'
        f'<div id="card-list"></div>'
        f'<script id="papers-data" type="application/json">{papers_json}</script>'
    )
    return _page("rndresearch – papers", body, "index", _INDEX_JS, json.dumps(states, ensure_ascii=False))


def _detail_page(paper, states):
    state     = states.get(paper["slug"], DEFAULT_STATE)
    st        = STATES[state]
    arxiv_id  = paper["arxiv_id"]
    arxiv_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""

    back_row = (
        f'<div class="back-row">'
        f'  <a class="back-btn" href="/">← Volver al índice</a>'
        + (f'  <a class="arxiv-btn" href="{arxiv_url}" target="_blank">📄 Ver en arXiv ↗ {arxiv_id}</a>' if arxiv_url else "")
        + f'  <span class="state-badge" id="state-badge" style="color:{st["color"]};border-color:{st["color"]}">'
          f'{st["icon"]} {st["label"]}</span>'
        f'</div>'
    )
    ss_btns = "".join(
        f'<button class="ssbtn{"active" if k==state else ""}" '
        f'style="{"color:"+v["color"]+";border-color:"+v["color"] if k==state else ""}" '
        f'data-state="{k}" onclick="setState(\'{paper["slug"]}\',\'{k}\')">'
        f'{v["icon"]} {v["label"]}</button>'
        for k, v in STATES.items()
    )
    body = (
        back_row
        + f'<div class="detail-state"><label>Estado:</label>{ss_btns}</div>'
        + f'<div class="paper-body">{paper["full_html"]}</div>'
    )
    return _page(paper["title"], body, "index", _DETAIL_JS, json.dumps(states, ensure_ascii=False))


def _search_page(topics_file):    # params panel
    params_html = """
<div class="panel">
  <h2>⚙️ Parámetros de búsqueda</h2>
  <div class="form-row">
    <label for="days">Días hacia atrás</label>
    <input type="number" id="days" value="7" min="1" max="365">
    <div class="hint">Busca papers publicados en los últimos N días</div>
  </div>
  <div class="form-row">
    <label for="provider">Proveedor LLM</label>
    <select id="provider">
      <option value="auto">Auto-detectar (por variables de entorno)</option>
      <option value="anthropic">Anthropic Claude</option>
      <option value="openai">OpenAI GPT</option>
      <option value="azure">Azure OpenAI</option>
    </select>
  </div>
  <div class="form-row">
    <label for="model">Modelo (opcional)</label>
    <input type="text" id="model" placeholder="Ej: claude-opus-4-5, gpt-4o, my-deployment">
    <div class="hint">Deja vacío para usar el modelo por defecto del proveedor</div>
  </div>
  <div class="form-row">
    <label>Modo de topics</label>
    <div class="radio-group">
      <label><input type="radio" name="topic_mode" value="any" checked> <strong>any</strong> — al menos un topic</label>
      <label><input type="radio" name="topic_mode" value="all"> <strong>all</strong> — todos los topics</label>
    </div>
    <div class="hint">any: red amplia &nbsp;·&nbsp; all: solo papers que aparecen en todos los topics</div>
  </div>
  <div class="check-row">
    <input type="checkbox" id="advanced_filter">
    <label for="advanced_filter">🔬 Filtro avanzado (el LLM evalúa cada abstract antes de resumir)</label>
  </div>
  <div class="check-row">
    <input type="checkbox" id="dry_run">
    <label for="dry_run">⚠️ Dry run (sin llamadas LLM ni escritura de ficheros)</label>
  </div>
  <button class="run-btn" id="run-btn" onclick="runAgent()">▶ Ejecutar agente</button>
</div>"""

    yaml_html = f"""
<div class="panel">
  <h2>📋 Topics — <code>research_topics.yaml</code></h2>
  <textarea class="yaml-editor" id="yaml-editor" spellcheck="false"></textarea>
  <div class="yaml-actions">
    <button class="save-btn" id="save-yaml-btn" onclick="saveTopics()">💾 Guardar topics</button>
    <span class="status-msg" id="yaml-status"></span>
  </div>
</div>"""

    console_html = """
<div class="console-panel" id="console-panel" style="display:none">
  <div class="console-header">
    <h2>🖥️ Salida del agente</h2>
    <span class="running-indicator" id="run-indicator" style="display:none">
      <span class="dot-pulse"></span> Ejecutando…
    </span>
  </div>
  <pre class="console-output" id="console-output"></pre>
  <div class="done-msg" id="done-msg" style="display:none"></div>
</div>"""

    body = (
        f'<div class="search-layout">{params_html}{yaml_html}</div>'
        + console_html
    )
    return _page("rndresearch – Arxiv Search", body, "search", _SEARCH_JS)


def _deep_research_page(papers, states):
    """Deep Research page: pending papers list, prompt editor, run form, console."""
    # Papers pending analysis
    pending = [p for p in papers if states.get(p["slug"], DEFAULT_STATE) == "pending_analysis"]

    if pending:
        items = "".join(
            f'<div class="pending-item">'
            f'  <span class="pi-title"><a href="/paper/{p["slug"]}">{p["title"]}</a></span>'
            f'  <span class="pi-meta">📅 {p["date"]}'
            + (f' &nbsp;·&nbsp; <a class="arxiv-link" href="https://arxiv.org/abs/{p["arxiv_id"]}" target="_blank">arXiv ↗</a>' if p["arxiv_id"] else "")
            + f'</span></div>'
            for p in pending
        )
        pending_html = (
            f'<div style="margin-bottom:20px">'
            f'<h2 style="font-size:1rem;font-weight:700;margin-bottom:12px">'
            f'🔎 Papers pendientes de análisis ({len(pending)})</h2>'
            f'<div class="pending-list">{items}</div>'
            f'</div>'
        )
    else:
        pending_html = (
            '<div class="empty-state" style="margin-bottom:20px">'
            '🔎 No hay papers marcados como <strong>Pendiente de analizar</strong>.<br>'
            'Marca papers desde la página principal o en la vista de detalle.'
            '</div>'
        )

    params_html = """
<div class="panel">
  <h2>⚙️ Parámetros del análisis</h2>
  <div class="form-row">
    <label for="deep-provider">Proveedor LLM</label>
    <select id="deep-provider">
      <option value="auto">Auto-detectar (por variables de entorno)</option>
      <option value="anthropic">Anthropic Claude</option>
      <option value="openai">OpenAI GPT</option>
      <option value="azure">Azure OpenAI</option>
    </select>
  </div>
  <div class="form-row">
    <label for="deep-model">Modelo (opcional)</label>
    <input type="text" id="deep-model" placeholder="Ej: claude-opus-4-5, gpt-4o">
    <div class="hint">Deja vacío para usar el modelo por defecto. Los análisis profundos consumen más tokens — considera modelos potentes.</div>
  </div>
  <div class="check-row">
    <input type="checkbox" id="deep-dry-run">
    <label for="deep-dry-run">⚠️ Dry run (sin llamadas LLM ni escritura de ficheros)</label>
  </div>
  <button class="run-btn" id="deep-run-btn" onclick="runDeep()">🔬 Lanzar análisis</button>
</div>"""

    prompt_html = """
<div class="panel">
  <h2>📝 Prompt — <code>deep_research_prompt.yaml</code></h2>
  <textarea class="yaml-editor" id="prompt-editor" spellcheck="false" style="min-height:380px"></textarea>
  <div class="yaml-actions">
    <button class="save-btn" id="save-prompt-btn" onclick="saveDeepPrompt()">💾 Guardar prompt</button>
    <span class="status-msg" id="prompt-status"></span>
  </div>
</div>"""

    console_html = """
<div class="console-panel" id="deep-console-panel" style="display:none">
  <div class="console-header">
    <h2>🖥️ Salida del agente de análisis</h2>
    <span class="running-indicator" id="deep-run-indicator" style="display:none">
      <span class="dot-pulse"></span> Analizando…
    </span>
  </div>
  <pre class="console-output" id="deep-console-output"></pre>
  <div class="done-msg" id="deep-done-msg" style="display:none"></div>
</div>"""

    body = (
        pending_html
        + f'<div class="search-layout">{params_html}{prompt_html}</div>'
        + console_html
    )
    return _page("rndresearch – Deep Research", body, "deep", _DEEP_JS)


# ── HTTP handler ───────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    papers: list             = []
    by_slug: dict            = {}
    states: dict             = {}
    summaries_dir: Path      = Path("summaries")
    topics_file: Path        = Path("research_topics.yaml")
    agent_path: Path         = Path("agent.py")
    deep_agent_path: Path    = Path("deep_research.py")
    deep_prompt_file: Path   = Path("deep_research_prompt.yaml")

    def log_message(self, fmt, *args):
        pass

    def _send(self, data, status=200, ctype="text/html; charset=utf-8"):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), status, "application/json")

    def do_GET(self):
        path = unquote(urlparse(self.path).path).rstrip("/") or "/"

        if path == "/":
            self._send(_index_page(self.papers, self.states))

        elif path.startswith("/paper/"):
            slug  = path[len("/paper/"):]
            paper = self.by_slug.get(slug)
            if paper:
                self._send(_detail_page(paper, self.states))
            else:
                self._send(b"<h1>404 - Paper not found</h1>", 404)

        elif path == "/search":
            self._send(_search_page(self.topics_file))

        elif path == "/deep-research":
            self._send(_deep_research_page(self.papers, self.states))

        elif path == "/api/topics":
            try:
                yaml_text = self.topics_file.read_text(encoding="utf-8")
                self._json({"ok": True, "yaml": yaml_text})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)

        elif path == "/api/run/status":
            with _run_lock:
                self._json({"running": _run["running"], "exit_code": _run["exit_code"]})

        elif path == "/api/run/stream":
            self._stream_sse(_run, _run_lock)

        elif path == "/api/deep-prompt":
            try:
                yaml_text = self.deep_prompt_file.read_text(encoding="utf-8")
                self._json({"ok": True, "yaml": yaml_text})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)

        elif path == "/api/deep-run/status":
            with _deep_run_lock:
                self._json({"running": _deep_run["running"], "exit_code": _deep_run["exit_code"]})

        elif path == "/api/deep-run/stream":
            self._stream_sse(_deep_run, _deep_run_lock)

        else:
            self._send(b"<h1>404 - Not found</h1>", 404)

    def do_POST(self):
        path   = unquote(urlparse(self.path).path)
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        if path.startswith("/api/state/"):
            slug = path[len("/api/state/"):]
            try:
                state = json.loads(body).get("state", "")
                if state not in STATES:
                    raise ValueError(f"unknown state: {state}")
                save_state(self.summaries_dir, self.states, slug, state)
                self._json({"ok": True})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)

        elif path == "/api/topics":
            try:
                yaml_text = json.loads(body).get("yaml", "")
                ok, err = _validate_yaml(yaml_text)
                if not ok:
                    self._json({"ok": False, "error": err}, 400)
                    return
                self.topics_file.write_text(yaml_text, encoding="utf-8")
                self._json({"ok": True})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)

        elif path == "/api/run":
            try:
                params = json.loads(body)
                ok = start_agent(params, self.agent_path, self.summaries_dir)
                if ok:
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "El agente ya está en ejecución"}, 409)
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)

        elif path == "/api/deep-prompt":
            try:
                yaml_text = json.loads(body).get("yaml", "")
                ok, err = _validate_yaml(yaml_text)
                if not ok:
                    self._json({"ok": False, "error": err}, 400)
                    return
                self.deep_prompt_file.write_text(yaml_text, encoding="utf-8")
                self._json({"ok": True})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)

        elif path == "/api/deep-run":
            try:
                params = json.loads(body)
                ok = start_deep_agent(
                    params, self.deep_agent_path, self.summaries_dir, self.deep_prompt_file
                )
                if ok:
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "El agente de análisis ya está en ejecución"}, 409)
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)

        else:
            self._send(b"Not found", 404)

    def _stream_sse(self, run_dict, run_lock):
        """Server-Sent Events endpoint — streams agent output lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        sent = 0
        try:
            while True:
                with run_lock:
                    batch   = run_dict["output"][sent:]
                    running = run_dict["running"]
                    code    = run_dict["exit_code"]
                    total   = len(run_dict["output"])

                for line in batch:
                    msg = json.dumps({"line": line}, ensure_ascii=False)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                    sent += 1

                if batch:
                    self.wfile.flush()

                if not running and sent >= total:
                    msg = json.dumps({"done": True, "exit_code": code})
                    self.wfile.write(f"data: {msg}\n\n".encode())
                    self.wfile.flush()
                    break

                time.sleep(0.15)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ── threading server (needed for SSE + concurrent requests) ───────────────

class _ThreadingServer(HTTPServer):
    def process_request(self, request, client_address):
        threading.Thread(
            target=self._process_request_thread,
            args=(request, client_address),
            daemon=True,
        ).start()

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


# ── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Local viewer for rndresearch summaries.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--summaries", type=Path, default=Path(__file__).parent / "summaries")
    args = parser.parse_args()

    if not args.summaries.is_dir():
        print(f"[viewer] Error: directory not found: {args.summaries}")
        raise SystemExit(1)

    base          = args.summaries.parent
    topics_f      = base / "research_topics.yaml"
    agent_f       = base / "agent.py"
    deep_agent_f  = base / "deep_research.py"
    deep_prompt_f = base / "deep_research_prompt.yaml"

    print(f"[viewer] Loading summaries from {args.summaries} …")
    papers = load_summaries(args.summaries)
    states = load_states(args.summaries)
    print(f"[viewer] {len(papers)} papers loaded.")

    Handler.papers           = papers
    Handler.by_slug          = {p["slug"]: p for p in papers}
    Handler.states           = states
    Handler.summaries_dir    = args.summaries
    Handler.topics_file      = topics_f
    Handler.agent_path       = agent_f
    Handler.deep_agent_path  = deep_agent_f
    Handler.deep_prompt_file = deep_prompt_f

    url = f"http://localhost:{args.port}"
    print(f"[viewer] Serving at {url}  (Ctrl+C to stop)")
    for f, label in [(topics_f, "research_topics.yaml"), (agent_f, "agent.py"),
                     (deep_agent_f, "deep_research.py"), (deep_prompt_f, "deep_research_prompt.yaml")]:
        if not f.exists():
            print(f"[viewer] Warning: file not found: {f}")
    print()

    threading.Timer(0.5, webbrowser.open, args=[url]).start()
    try:
        _ThreadingServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[viewer] Stopped.")

if __name__ == "__main__":
    main()
