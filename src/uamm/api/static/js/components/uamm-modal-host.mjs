import { select, getState } from '../core/store.mjs';
import { selectRagFileDetail } from '../core/selectors.mjs';
import { loadFileDetail } from '../core/actions.mjs';

export class UammModalHost extends HTMLElement {
  constructor(){ super(); this._mounted = false; this._currentPath = null; }

  connectedCallback(){
    if (this._mounted) return;
    this._mounted = true;
    this.innerHTML = this._template();
    // Setup modal accessibility: manage inert and focus
    try{
      const modal = document.getElementById('fileDetailModal');
      if (modal && window.bootstrap) {
        modal.addEventListener('shown.bs.modal', () => {
          modal.removeAttribute('inert');
          modal.setAttribute('aria-hidden', 'false');
        });
        modal.addEventListener('hide.bs.modal', () => {
          try{ if (document.activeElement) document.activeElement.blur(); }catch(_){ }
          modal.setAttribute('inert', '');
          modal.setAttribute('aria-hidden', 'true');
          this._currentPath = null;
        });
      }
    }catch(_){ }
  }

  disconnectedCallback(){
    this._mounted = false;
  }

  openFile(path){
    if (!path) return;
    // Ensure component is mounted and modal exists
    if (!this._mounted) {
      console.warn('Modal host not mounted yet, retrying...');
      setTimeout(() => this.openFile(path), 100);
      return;
    }
    const modal = document.getElementById('fileDetailModal');
    if (!modal) {
      console.warn('File detail modal not found in DOM');
      return;
    }
    this._currentPath = path;
    loadFileDetail(path);
    const detail = selectRagFileDetail(path)(getState());
    this._renderDetail(detail);
    if (window.bootstrap) {
      window.bootstrap.Modal.getOrCreateInstance(modal).show();
    }
    // Subscribe to updates
    if (this._detailUnsub) { try { this._detailUnsub(); } catch(_){} }
    this._detailUnsub = select(selectRagFileDetail(path), (data) => {
      if (this._currentPath === path) this._renderDetail(data);
    });
  }

  _template(){
    return `
  <!-- File detail modal -->
  <div class="modal fade" id="fileDetailModal" tabindex="-1" aria-labelledby="fileDetailLabel" inert>
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="fileDetailLabel">File Detail</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body" id="filedetail-body">Loading…</div>
      </div>
    </div>
  </div>
    `;
  }

  _renderDetail(data){
    const body = document.getElementById('filedetail-body');
    if (!body) return;
    if (data.loading) { body.innerHTML = '<div class="small text-muted">Loading…</div>'; return; }
    const detail = data.detail || {};
    const file = detail.file || {};
    const chunks = detail.chunks || [];
    const events = data.events || [];
    const path = file.path || this._currentPath || '';
    const name = file.name || path.split('/').pop() || 'file';
    const status = file.status || 'unknown';
    const reason = file.reason || '';
    const chunkCount = file.chunks || chunks.length || 0;
    let rows = '';
    chunks.slice(0, 20).forEach((c, idx) => {
      const snip = (c.snippet || '').substring(0, 480);
      rows += `<div class="list-group-item"><small class="text-muted">#${idx+1}</small><div class="small mt-1">${this._escape(snip)}</div></div>`;
    });
    let histRows = '';
    events.slice(0, 20).forEach(e => {
      const ts = e.ts ? new Date(e.ts * 1000).toLocaleString() : 'n/a';
      histRows += `<div class="list-group-item"><div class="d-flex justify-content-between"><span class="badge text-bg-${this._statusBadge(e.status)}">${e.status}</span><small class="text-muted">${ts}</small></div>${e.reason?'<div class="small text-muted">'+this._escape(e.reason)+'</div>':''}</div>`;
    });
    body.innerHTML = `
      <div class="mb-2">
        <div class="fw-semibold">file</div>
        <div class="small">${this._escape(name)}</div>
        <div class="small text-muted">${this._escape(path)}</div>
      </div>
      <div class="mb-2"><span class="badge text-bg-${this._statusBadge(status)}">${status}</span>${reason?' <span class="small text-muted">'+this._escape(reason)+'</span>':''}</div>
      <div class="mb-2 d-flex align-items-center justify-content-between"><div class="fw-semibold">Chunks</div><div class="small text-muted">showing ${Math.min(chunks.length, 20)} of ${chunkCount}</div></div>
      <div class="list-group list-group-flush mb-3">${rows || '<div class="list-group-item small text-muted">No chunks</div>'}</div>
      <div class="mb-2 d-flex align-items-center justify-content-between"><div class="fw-semibold">History</div><div class="small text-muted">${events.length} events</div></div>
      <div class="list-group list-group-flush">${histRows || '<div class="list-group-item small text-muted">No history</div>'}</div>
    `;
  }

  _statusBadge(s){
    if (!s) return 'secondary';
    const l = s.toLowerCase();
    if (l === 'ready') return 'success';
    if (l === 'skipped') return 'warning';
    if (l.includes('error') || l.includes('fail')) return 'danger';
    return 'secondary';
  }

  _escape(s){
    return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
  }
}

customElements.define('uamm-modal-host', UammModalHost);
