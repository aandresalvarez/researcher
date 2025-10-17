import { apiFetch } from '../core/api.mjs';
import { EV, emit } from '../core/events.mjs';
import { select } from '../core/store.mjs';
import { selectObsMetrics, selectObsSteps } from '../core/selectors.mjs';
import { obsLoadMetrics, obsLoadSteps } from '../core/actions.mjs';

export class UammObsPage extends HTMLElement {
  constructor(){ super(); this._mounted = false; this._autoId = null; }

  connectedCallback(){
    if (this._mounted) return; this._mounted = true;
    this.innerHTML = this._template();
    // Wire controls
    this._on('#auto-interval', 'change', () => this._setAuto());
    this._on('[data-action="metrics"]', 'click', () => obsLoadMetrics());
    this._on('[data-action="steps"]', 'click', () => this._reloadSteps());
    this._on('[data-action="copy-pack"]', 'click', () => this.copySelectedPack());
    this._on('[data-action="clear"]', 'click', () => this.clearFilters());
    this._on('[data-role="toggle-all"]', 'change', (e) => this.toggleAll(e.target));
    // Initial load
    // Reactive subscriptions
    this._metricsUnsub = select(selectObsMetrics, (m) => {
      const el = this.querySelector('#metrics');
      if (!el) return;
      if (m.loading) { el.textContent = 'Loading…'; return; }
      const data = m.data || {};
      const counts = `requests ${data.requests||0}, answers ${data.answers||0}, accept ${data.accept||0}, iterate ${data.iterate||0}, abstain ${data.abstain||0}`;
      const rates = data.rates ? `accept ${(data.rates.accept*100).toFixed(1)}%, iterate ${(data.rates.iterate*100).toFixed(1)}%, abstain ${(data.rates.abstain*100).toFixed(1)}%` : 'rates n/a';
      const lat = data.latency ? `p95 ${(data.latency.p95 ?? 'n/a')}s, avg ${(data.latency.average ?? 'n/a')}s (${data.latency.count} samples)` : 'latency n/a';
      const alerts = data.alerts ? Object.keys(data.alerts).length + ' categories' : 'none';
      el.innerHTML = `<div><strong>Counts</strong>: ${counts}</div><div><strong>Rates</strong>: ${rates}</div><div><strong>Latency</strong>: ${lat}</div><div><strong>Alerts</strong>: ${alerts}</div>`;
      this.renderAlerts(data.alerts||{});
      this.renderDomainList(data.by_domain||{});
    });
    this._stepsUnsub = select(selectObsSteps, (st) => {
      const tbody = this.querySelector('#steps-body');
      if (!tbody) return;
      if (st.loading) { tbody.innerHTML = ''; return; }
      const steps = st.items || [];
      tbody.innerHTML = steps.map(s => this._renderStepRow(s)).join('');
      const summary = this.querySelector('#steps-summary');
      if (summary) summary.textContent = `${steps.length} step(s)` + (st.domain?` • domain=${st.domain}`:'') + (st.action?` • action=${st.action}`:'');
      this.querySelectorAll('[data-action="view-step"]').forEach(btn => {
        btn.addEventListener('click', () => this.viewStep(btn.getAttribute('data-id')));
      });
    });
    obsLoadMetrics();
    this._reloadSteps();
    this._setAuto();
    // Listen for workspace/context changes
    this._wsHandler = () => { obsLoadMetrics(); this._reloadSteps(); };
    document.addEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
    document.addEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
    // Optional deep link to a step
    try{
      const p = new URLSearchParams(window.location.search);
      const open = p.get('open_step');
      if (open) setTimeout(()=>this.viewStep(open), 300);
    }catch(_){}
  }

  disconnectedCallback(){ 
    if (this._autoId) { clearInterval(this._autoId); this._autoId = null; } 
    this._mounted = false; 
    if (this._metricsUnsub) { try { this._metricsUnsub(); } catch(_){} this._metricsUnsub=null; }
    if (this._stepsUnsub) { try { this._stepsUnsub(); } catch(_){} this._stepsUnsub=null; }
    if (this._wsHandler) {
      document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
      document.removeEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
      this._wsHandler = null;
    }
  }

  _template(){
    return `
<div class="row">
  <div class="col-lg-5">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center gap-2 flex-wrap">
        <span>Metrics</span>
        <div class="ms-auto d-flex align-items-center gap-2">
          <div class="input-group input-group-sm" style="width: 220px;">
            <span class="input-group-text">Auto</span>
            <select id="auto-interval" class="form-select">
              <option value="0">Off</option>
              <option value="5000">5s</option>
              <option value="15000">15s</option>
              <option value="60000">60s</option>
            </select>
          </div>
          <button class="btn btn-sm btn-outline-secondary" data-action="metrics">Refresh</button>
        </div>
      </div>
      <div class="card-body">
        <div id="metrics" class="small text-muted">Loading…</div>
        <div id="alerts" class="mt-2"></div>
      </div>
    </div>
  </div>
  <div class="col-lg-7">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center gap-2 flex-wrap">
        <span>Recent Steps</span>
        <div class="ms-auto d-flex gap-2 align-items-center flex-wrap">
          <input type="text" id="filter-domain" class="form-control form-control-sm" list="obs-domain-list" placeholder="domain (optional)" style="width: 160px;" />
          <datalist id="obs-domain-list"></datalist>
          <select id="filter-action" class="form-select form-select-sm" style="width: 140px;">
            <option value="">action (any)</option>
            <option>accept</option>
            <option>iterate</option>
            <option>abstain</option>
          </select>
          <input type="number" id="limit" class="form-control form-control-sm" value="50" style="width: 80px;" />
          <button class="btn btn-sm btn-outline-primary" data-action="steps">Refresh</button>
          <button class="btn btn-sm btn-outline-success" data-action="copy-pack">Copy Selected</button>
          <button class="btn btn-sm btn-outline-secondary" data-action="clear">Clear</button>
        </div>
      </div>
      <div class="card-body">
        <div class="table-responsive">
          <table class="table table-sm table-hover align-middle">
            <thead>
              <tr class="text-nowrap">
                <th><input class="form-check-input" type="checkbox" data-role="toggle-all"></th>
                <th>Time</th><th>Domain</th><th>Action</th><th>S1</th><th>S2</th><th>S</th><th>CP</th><th></th>
              </tr>
            </thead>
            <tbody id="steps-body"></tbody>
          </table>
        </div>
        <div class="small text-muted" id="steps-summary"></div>
      </div>
    </div>
  </div>
</div>

<!-- Offcanvas for step details (scoped to component) -->
<div class="offcanvas offcanvas-end" tabindex="-1" id="obs-step-detail" aria-labelledby="obsStepDetailLabel">
  <div class="offcanvas-header">
    <h5 id="obsStepDetailLabel">Step Details</h5>
    <button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>
  </div>
  <div class="offcanvas-body">
    <div id="step-summary" class="mb-2 small text-muted"></div>
    <pre id="step-json" class="small"></pre>
  </div>
  <div class="offcanvas-footer border-top p-2">
    <button class="btn btn-sm btn-outline-secondary" data-action="copy-json">Copy JSON</button>
  </div>
</div>`;
  }

  async loadMetrics(){ await obsLoadMetrics(); }

  async loadSteps(){ await this._reloadSteps(); }
  _reloadSteps(){
    const limit = parseInt(this._val('#limit') || '50');
    const dom = (this._val('#filter-domain') || '').trim();
    const act = (this._val('#filter-action') || '').trim();
    return obsLoadSteps({ domain: dom, action: act, limit });
  }

  async viewStep(id){
    try{
      const res = await apiFetch('/steps/' + encodeURIComponent(id));
      const data = await res.json();
      if (!res.ok) throw new Error('failed');
      const sum = `action ${data.action}, domain ${data.domain}, S ${(data.final_score ?? '')}`;
      this._setText('#step-summary', sum);
      this._setText('#step-json', JSON.stringify(data, null, 2));
      if (window.bootstrap) {
        const off = new window.bootstrap.Offcanvas(this.querySelector('#obs-step-detail'));
        off.show();
      }
    } catch (e) {
      this._toast('Failed to load step ' + id);
    }
  }

  copyStepJson(){
    try{ const txt = this._text('#step-json'); navigator.clipboard.writeText(txt); }catch(_){}
  }

  async copySelectedPack(){
    const ids = Array.from(this.querySelectorAll('.step-select:checked')).map(el => el.value);
    if (!ids.length) { this._toast('No steps selected'); return; }
    try {
      const cases = [];
      for (const id of ids) {
        const res = await apiFetch('/steps/' + encodeURIComponent(id));
        const st = await res.json();
        if (!res.ok) continue;
        const item = {
          id: st.id, domain: st.domain, action: st.action,
          s1: st.s1, s2: st.s2, S: st.final_score,
          cp_accept: st.cp_accept, issues: st.issues, tools_used: st.tools_used,
          question: st.question, answer: st.answer, trace: st.trace,
        };
        cases.push(item);
      }
      const pack = { generated_at: new Date().toISOString(), count: cases.length, cases };
      await navigator.clipboard.writeText(JSON.stringify(pack, null, 2));
      this._toast('Copied ' + cases.length + ' case(s)');
    } catch (e) {
      this._toast('Failed to copy selection');
    }
  }

  clearFilters(){
    const fd = this.querySelector('#filter-domain');
    const fa = this.querySelector('#filter-action');
    if (fd) fd.value = '';
    if (fa) fa.value = '';
    this.loadSteps();
  }

  renderAlerts(alerts){
    const el = this.querySelector('#alerts'); if (!el) return;
    if (!alerts || typeof alerts !== 'object' || !Object.keys(alerts).length) {
      el.innerHTML = '<span class="badge text-bg-success">No alerts</span>';
      return;
    }
    const order = ['latency','abstain','cp','approvals'];
    const color = { latency: 'warning', abstain: 'warning', cp: 'info', approvals: 'info' };
    const html = order.filter(k => alerts[k]).map(k => {
      const scope = alerts[k];
      const count = (scope && typeof scope === 'object') ? Object.keys(scope).length : 1;
      return `<span class="badge me-1 text-bg-${color[k]||'secondary'}">${k} ${count}</span>`;
    }).join('');
    el.innerHTML = html || '<span class="badge text-bg-success">No alerts</span>';
  }

  renderDomainList(byDomain){
    const dl = this.querySelector('#obs-domain-list');
    if (!dl) return;
    const keys = Object.keys(byDomain || {});
    dl.innerHTML = keys.map(k => `<option value="${this._escape(k)}">`).join('');
  }

  toggleAll(cb){ this.querySelectorAll('.step-select').forEach(el => { el.checked = cb.checked; }); }

  _setAuto(){
    const sel = this.querySelector('#auto-interval');
    if (!sel) return;
    const ms = parseInt(sel.value || '0');
    if (this._autoId) { clearInterval(this._autoId); this._autoId = null; }
    if (ms > 0) { this._autoId = setInterval(() => { obsLoadMetrics(); this._reloadSteps(); }, ms); }
  }

  _renderStepRow(s){
    const when = s.created_at || s.time || '';
    const cp = s.cp_accept===true ? '<span class="badge text-bg-success">accept</span>' : (s.cp_accept===false ? '<span class="badge text-bg-danger">reject</span>' : '');
    const s1 = (s.s1 ?? '').toString().slice(0,5);
    const s2 = (s.s2 ?? '').toString().slice(0,5);
    const S = (s.final_score ?? '').toString().slice(0,5);
    return `<tr>
      <td><input class="form-check-input step-select" type="checkbox" value="${this._escape(s.id)}"></td>
      <td class="text-nowrap">${this._escape(when)}</td>
      <td>${this._escape(s.domain||'')}</td>
      <td><span class="badge text-bg-${this._badge(s.action)}">${this._escape(s.action||'')}</span></td>
      <td>${this._escape(s1)}</td>
      <td>${this._escape(s2)}</td>
      <td>${this._escape(S)}</td>
      <td>${cp}</td>
      <td class="text-end"><button class="btn btn-sm btn-outline-secondary" data-action="view-step" data-id="${this._escape(s.id)}">View</button></td>
    </tr>`;
  }

  _badge(a){ switch((a||'').toLowerCase()){ case 'accept': return 'success'; case 'iterate': return 'warning'; case 'abstain': return 'secondary'; default: return 'light'; } }
  _escape(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
  _text(sel){ const el = this.querySelector(sel); return (el && el.textContent) || ''; }
  _val(sel){ const el = this.querySelector(sel); return (el && el.value) || ''; }
  _setText(sel, v){ const el = this.querySelector(sel); if (el) el.textContent = v; }
  _on(sel, type, fn){ const el = this.querySelector(sel); if (el) el.addEventListener(type, fn); }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }
}

customElements.define('uamm-obs-page', UammObsPage);
