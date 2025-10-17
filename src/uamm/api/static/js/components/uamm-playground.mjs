import { sse } from '../core/api.mjs';
import { getContext } from '../core/context.mjs';
import { log, isDebugEnabled } from '../core/debug.mjs';
import { EV, emit } from '../core/events.mjs';

export class UammPlayground extends HTMLElement {
  constructor(){
    super();
    this._es = null;
    this._t0 = null;
    this._ft = null;
    this._mounted = false;
  }

  connectedCallback(){
    if (this._mounted) return;
    this._mounted = true;
    this.innerHTML = this._template();
    // Wire handlers
    const form = this.querySelector('#ask-form');
    if (form) form.addEventListener('submit', (ev) => { ev.preventDefault(); this.start(); });
    const stopBtn = this.querySelector('#btn-stop');
    if (stopBtn) stopBtn.addEventListener('click', () => this.stop());
    const resetBtn = this.querySelector('#btn-reset');
    if (resetBtn) resetBtn.addEventListener('click', () => this.reset());
    const copyJson = this.querySelector('[data-action="copy-json"]');
    if (copyJson) copyJson.addEventListener('click', () => this.copyFinalJson());
    const copyCurl = this.querySelector('[data-action="copy-curl"]');
    if (copyCurl) copyCurl.addEventListener('click', () => this.copyCurl());
    // Init from URL
    this._initFromQuery();
  }

  disconnectedCallback(){ this.stop(); this._mounted = false; }

  _template(){
    const domainDefault = this.getAttribute('data-domain') || 'default';
    return `
    <div class="row">
      <div class="col-lg-6">
        <form id="ask-form" class="card mb-3">
          <div class="card-header">Agent Playground</div>
          <div class="card-body">
            <div class="mb-3">
              <label for="question" class="form-label">Question</label>
              <textarea id="question" name="question" class="form-control" rows="4" placeholder="Ask something…" required></textarea>
            </div>
            <div class="row g-3">
              <div class="col-md-6">
                <label for="domain" class="form-label">Domain</label>
                <input id="domain" name="domain" class="form-control" value="${escapeHtml(domainDefault)}" />
              </div>
              <div class="col-md-6">
                <label for="key" class="form-label">API Key (optional)</label>
                <input id="key" name="key" type="password" class="form-control" placeholder="wk_xxx (optional)" />
                <div class="form-text">Used for protected endpoints; not stored.</div>
              </div>
            </div>
            <div class="row g-3 mt-1">
              <div class="col-md-6">
                <label for="workspace" class="form-label">Workspace (optional)</label>
                <input id="workspace" name="workspace" class="form-control" placeholder="default" />
              </div>
              <div class="col-md-6">
                <label for="ref" class="form-label">Refinements</label>
                <input id="ref" name="ref" type="number" min="0" max="6" step="1" class="form-control" value="2" />
                <div class="form-text">max_refinements</div>
              </div>
            </div>
            <div class="row g-3 mt-1">
              <div class="col-md-6">
                <label for="mem" class="form-label">Memory Budget</label>
                <input id="mem" name="mem" type="number" min="0" max="32" step="1" class="form-control" value="8" />
              </div>
              <div class="col-md-6">
                <label for="delta" class="form-label">Borderline Δ</label>
                <input id="delta" name="delta" type="number" min="0" max="1" step="0.01" class="form-control" value="0.05" />
              </div>
            </div>
            <div class="form-check mt-2">
              <input class="form-check-input" type="checkbox" id="lite" name="lite" />
              <label class="form-check-label" for="lite">Stream Lite (hide events)</label>
            </div>
          </div>
          <div class="card-footer d-flex gap-2">
            <button id="btn-start" class="btn btn-primary" type="submit">Start Streaming</button>
            <button id="btn-stop" class="btn btn-outline-secondary" type="button" disabled>Stop</button>
            <button id="btn-reset" class="btn btn-outline-danger" type="button">Reset</button>
            <span class="ms-auto text-muted">
              <span id="status" class="badge text-bg-secondary">Idle</span>
              <span class="ms-2 small">FT: <span id="ft-lat">–</span> • Total: <span id="tot-lat">–</span></span>
            </span>
          </div>
        </form>
      </div>
      <div class="col-lg-6">
        <div class="card mb-3">
          <div class="card-header">Answer</div>
          <div class="card-body"><pre id="answer" class="mb-0"></pre></div>
        </div>
        <div class="card mb-3" id="events-card">
          <div class="card-header">Scores / Events</div>
          <div class="card-body">
            <div class="row g-3">
              <div class="col-md-6">
                <h6>Scores</h6>
                <div id="scores" class="small"></div>
              </div>
              <div class="col-md-6">
                <h6>Tools</h6>
                <ul id="tools-log" class="log small list-unstyled mb-0"></ul>
              </div>
            </div>
            <div class="row g-3 mt-2">
              <div class="col-md-6">
                <h6>PCN</h6>
                <div id="pcn" class="small"></div>
              </div>
              <div class="col-md-6">
                <h6>GoV</h6>
                <div id="gov" class="small"></div>
              </div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-header">Final JSON</div>
          <div class="card-body">
            <div class="d-flex gap-2 mb-2">
              <button class="btn btn-sm btn-outline-secondary" data-action="copy-json">Copy JSON</button>
              <button class="btn btn-sm btn-outline-primary" data-action="copy-curl">Copy cURL (POST)</button>
            </div>
            <code id="final-json"></code>
          </div>
        </div>
      </div>
    </div>`;
  }

  _initFromQuery(){
    try{
      const p = new URLSearchParams(window.location.search);
      const q = p.get('q') || '';
      const dom = p.get('domain') || '';
      const qel = this.querySelector('#question');
      const del = this.querySelector('#domain');
      if (qel && q) qel.value = q;
      if (del && dom) del.value = dom;
      if (q && p.get('autostart') === '1') { this.start(); }
    }catch(_){}
  }

  start(){
    // Prevent duplicate stream
    if (this._es) this.stop();
    const q = this._val('#question');
    const domain = this._val('#domain') || 'default';
    const wsInput = this._val('#workspace');
    const ws = wsInput || (getContext().workspace || 'default');
    const ref = this._val('#ref');
    const mem = this._val('#mem');
    const delta = this._val('#delta');
    if (!q) return;
    this._resetOutputs();
    const params = { question: q, domain, workspace: ws };
    if (ref) params.max_refinements = ref;
    if (mem) params.memory_budget = mem;
    if (delta) params.borderline_delta = delta;
    const lite = !!this.querySelector('#lite')?.checked;
    if (lite) params.stream_lite = true;
    // Hide events panel when lite is enabled
    const evCard = this.querySelector('#events-card');
    if (evCard) evCard.style.display = lite ? 'none' : '';
    const url = `/ui/agent/stream?` + new URLSearchParams(params);
    this._t0 = performance.now();
    this._ft = null;
    this._setStatus('Connecting…', 'secondary');
    this._toggleButtons(true);
    const { source, close } = sse(url, {
      on: {
        ready: () => this._setStatus('Streaming…', 'primary'),
        token: (data) => this._onToken(data),
        tool: (data) => this._appendTool(data),
        score: (data) => this._renderScores(data),
        pcn: (data) => this._setText('#pcn', this._safeString(data)),
        gov: (data) => this._setText('#gov', this._safeString(data)),
        final: (data) => this._onFinal(data),
        error: () => { this._setStatus('Error', 'danger'); this._toast('Streaming error'); },
      },
      onError: () => { this._setStatus('Error', 'danger'); this._toast('Streaming error'); },
    });
    this._es = { source, close };
  }

  stop(){
    if (this._es) { try { this._es.close(); } catch(_){} this._es = null; }
    this._toggleButtons(false);
  }

  reset(){
    const f = this.querySelector('#ask-form');
    if (f) f.reset();
    this._resetOutputs();
    this._setStatus('Idle', 'secondary');
  }

  copyFinalJson(){
    try{ const txt = this._text('#final-json'); navigator.clipboard.writeText(txt); }catch(_){}
  }

  copyCurl(){
    try{
      const origin = window.location.origin;
      const url = origin + '/agent/answer';
      const q = this._val('#question');
      const domain = this._val('#domain') || 'default';
      const key = this._val('#key');
      const ws = this._val('#workspace');
      const ref = this._val('#ref');
      const mem = this._val('#mem');
      const delta = this._val('#delta');
      const body = {
        question: q,
        domain,
        use_memory: true,
        memory_budget: parseInt(mem || '8'),
        max_refinements: parseInt(ref || '2'),
        borderline_delta: parseFloat(delta || '0.05'),
        stream: false,
      };
      let cmd = `curl -s -X POST ${url} \\\n+  -H 'content-type: application/json' \\\n+  -d '${JSON.stringify(body)}'`;
      if (key){ cmd += ` \\\n+  -H 'Authorization: Bearer ${key}'`; }
      if (ws){ cmd += ` \\\n+  -H 'X-Workspace: ${ws}'`; }
      navigator.clipboard.writeText(cmd);
    }catch(_){}
  }

  _onToken(data){
    const t = (data && data.text) ? data.text : '';
    const el = this.querySelector('#answer');
    if (el) el.textContent += (t + ' ');
    if (!this._ft){ this._ft = performance.now(); this._setText('#ft-lat', Math.round(this._ft - this._t0) + ' ms'); }
  }

  _onFinal(d){
    try { this._setText('#final-json', JSON.stringify(d || {}, null, 2)); } catch(_) {}
    const total = performance.now() - this._t0;
    this._setText('#tot-lat', Math.round(total) + ' ms');
    this._setStatus('done', 'success');
    this.stop();
  }

  _appendTool(d){
    const el = this.querySelector('#tools-log');
    if (!el) return;
    const li = document.createElement('li');
    const name = d && d.name ? d.name : 'tool';
    const status = d && d.status ? d.status : '';
    const ts = new Date().toLocaleTimeString();
    li.textContent = `[${ts}] ${name} — ${status}`;
    el.appendChild(li);
  }

  _renderScores(d){
    const el = this.querySelector('#scores');
    if (!el) return;
    if (!d || typeof d !== 'object') { el.textContent = ''; return; }
    const s1 = d.s1 ?? d.snne ?? '';
    const s2 = d.s2 ?? '';
    const fs = d.final_score ?? d.S ?? '';
    const tau = d.tau ?? d.cp_tau ?? '';
    el.textContent = `SNNE: ${s1}  Verifier: ${s2}  Final: ${fs}  tau: ${tau}`;
  }

  _setStatus(text, kind){
    const el = this.querySelector('#status');
    if (el){ el.textContent = text; el.className = 'badge text-bg-' + (kind || 'secondary'); }
  }

  _toggleButtons(streaming){
    const s=this.querySelector('#btn-start');
    const p=this.querySelector('#btn-stop');
    if (s) s.disabled = streaming;
    if (p) p.disabled = !streaming;
  }

  _resetOutputs(){
    ['#answer','#scores','#pcn','#gov','#final-json'].forEach(sel => {
      const el = this.querySelector(sel);
      if (!el) return;
      if (sel==='#tools-log') el.innerHTML=''; else el.textContent='';
    });
    const tl = this.querySelector('#tools-log'); if (tl) tl.innerHTML='';
    this._setText('#ft-lat', '–');
    this._setText('#tot-lat', '–');
  }

  _text(sel){ const el = this.querySelector(sel); return (el && el.textContent) || ''; }
  _val(sel){ const el = this.querySelector(sel); return (el && (el.value||'').trim()) || ''; }
  _setText(sel, text){ const el = this.querySelector(sel); if (el) el.textContent = String(text); }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }
  _safeString(v){ try { return JSON.stringify(v); } catch (_) { return String(v); } }
}

function escapeHtml(s){
  return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
}

customElements.define('uamm-playground', UammPlayground);
