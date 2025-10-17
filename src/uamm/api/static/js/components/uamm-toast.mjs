import { EV } from '../core/events.mjs';

export class UammToast extends HTMLElement {
  constructor(){ super(); this._handler = null; }
  connectedCallback(){
    if (this._handler) return;
    this._handler = (ev) => {
      try {
        const detail = ev.detail || {};
        const msg = typeof detail === 'string' ? detail : (detail.message || '');
        const el = document.getElementById('toast');
        const body = document.getElementById('toast-body');
        if (!el || !body || !window.bootstrap) return;
        body.textContent = msg;
        window.bootstrap.Toast.getOrCreateInstance(el).show();
      } catch(_){}
    };
    document.addEventListener(EV.TOAST, this._handler);
  }
  disconnectedCallback(){ if (this._handler) { document.removeEventListener(EV.TOAST, this._handler); this._handler = null; } }
}

customElements.define('uamm-toast', UammToast);
