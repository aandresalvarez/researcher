import { apiFetch, apiFetchWith } from '../core/api.mjs';
import { EV } from '../core/events.mjs';
import { select } from '../core/store.mjs';
import { selectCp } from '../core/selectors.mjs';
import { cpLoadThreshold } from '../core/actions.mjs';

export class UammCpPage extends HTMLElement {
  constructor(){ super(); this._mounted = false; }
  connectedCallback(){ 
    if (this._mounted) return; 
    this._mounted = true; 
    this.innerHTML = this._template(); 
    this._on('[data-action="refresh"]', 'click', () => this._loadFromInputs()); 
    // Reactive subscription
    this._cpUnsub = select(selectCp, (cp) => {
      const el = this.querySelector('#cp-result');
      if (!el) return;
      if (cp.loading) { el.textContent = 'Loading…'; return; }
      if (cp.error) { el.innerHTML = '<span class="text-danger">Error</span>'; return; }
      const data = cp.result || {};
      const tau = (data.tau === null || data.tau === undefined) ? 'n/a' : data.tau;
      const stats = data.stats || {};
      el.innerHTML = `
        <div><strong>Domain</strong>: ${this._escape(data.domain||cp.domain||'default')}</div>
        <div><strong>τ (tau)</strong>: ${this._escape(String(tau))} ${data.cached ? '<span class=\"badge text-bg-info\">cached</span>' : ''}</div>
        <div class="mt-2"><strong>Stats</strong>:</div>
        <pre class="small">${this._escape(JSON.stringify(stats, null, 2))}</pre>
      `;
    });
    this._loadFromInputs(); 
    // Listen for workspace/context changes
    this._wsHandler = () => this._loadFromInputs();
    document.addEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
    document.addEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
  }
  disconnectedCallback(){ 
    this._mounted = false; 
    if (this._cpUnsub) { try { this._cpUnsub(); } catch(_){} this._cpUnsub = null; }
    if (this._wsHandler) {
      document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
      document.removeEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
      this._wsHandler = null;
    }
  }

  _template(){
    return `
<div class="row">
  <div class="col-lg-6">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center">
        <span>CP Threshold</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="refresh">Refresh</button>
      </div>
      <div class="card-body">
        <div class="row g-2 align-items-end">
          <div class="col-md-5"><input id="cp-domain" class="form-control" placeholder="domain" value="default" /></div>
          <div class="col-md-7"><input id="cp-key" type="password" class="form-control" placeholder="Admin API Key (optional)" /></div>
        </div>
        <div class="mt-3" id="cp-result" class="small text-muted">Loading…</div>
      </div>
    </div>
  </div>
</div>`;
  }

  _loadFromInputs(){
    const dom = (this._val('#cp-domain') || 'default').trim() || 'default';
    const key = (this._val('#cp-key') || '').trim();
    try { cpLoadThreshold(dom, key || undefined); } catch(_){}
  }

  _on(sel, type, fn){ const el = this.querySelector(sel); if (el) el.addEventListener(type, fn); }
  _val(sel){ const el = this.querySelector(sel); return (el && el.value) || ''; }
  _escape(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
}

customElements.define('uamm-cp-page', UammCpPage);
