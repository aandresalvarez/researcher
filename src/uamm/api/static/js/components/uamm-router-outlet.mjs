import { select } from '../core/store.mjs';
import { selectRoute } from '../core/selectors.mjs';

export class UammRouterOutlet extends HTMLElement {
  constructor(){ super(); this._mounted = false; }

  connectedCallback(){
    if (this._mounted) return;
    this._mounted = true;
    
    // Subscribe to route changes
    this._routeUnsub = select(selectRoute, (route) => {
      this._renderRoute(route);
    });
  }

  disconnectedCallback(){
    this._mounted = false;
    if (this._routeUnsub) { try { this._routeUnsub(); } catch(_){} this._routeUnsub = null; }
  }

  _renderRoute(route) {
    if (!route || !route.name) return;
    const pages = ['uamm-home-page', 'uamm-rag-page', 'uamm-obs-page', 'uamm-cp-page', 'uamm-evals-page', 'uamm-workspaces-page'];
    // Hide any SPA pages managed within this outlet only
    pages.forEach(tag => {
      this.querySelectorAll(tag).forEach(el => { el.style.display = 'none'; });
    });

    // Show or create the active page
    const activeTag = this._getPageTag(route.name);
    if (activeTag) {
      let el = this.querySelector(activeTag);
      if (!el) {
        // Create dynamically inside this outlet
        try { el = document.createElement(activeTag); this.appendChild(el); } catch (_) {}
      }
      if (el) el.style.display = 'block';
    }
  }

  _getPageTag(routeName) {
    const mapping = {
      'home': 'uamm-home-page',
      'rag': 'uamm-rag-page', 
      'obs': 'uamm-obs-page',
      'cp': 'uamm-cp-page',
      'evals': 'uamm-evals-page',
      'workspaces': 'uamm-workspaces-page'
    };
    // For routes not managed by SPA (e.g., 'docs'), return null
    return mapping[routeName] || null;
  }
}

customElements.define('uamm-router-outlet', UammRouterOutlet);

