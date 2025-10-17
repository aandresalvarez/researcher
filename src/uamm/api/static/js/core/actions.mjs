import { patchState, getState } from './store.mjs';
import { setContext } from './context.mjs';
import { apiFetch } from './api.mjs';

export function setWorkspace(slug) {
  const s = getState();
  if (!slug || s.context.workspace === slug) return;
  patchState({ context: { ...s.context, workspace: slug } });
  // Keep legacy context (headers, events) in sync
  try { setContext({ workspace: slug }); } catch (_) {}
  // Invalidate dependent slices
  patchState({ stats: { ...s.stats, loading: true } });
  refreshStats();
}

export async function refreshStats() {
  const ws = getState().context.workspace || 'default';
  try {
    const res = await apiFetch(`/workspaces/${encodeURIComponent(ws)}/stats`);
    const data = await res.json();
    patchState({ stats: {
      docs: data.docs || (data.counts ? data.counts.docs || 0 : 0),
      steps: data.steps || (data.counts ? data.counts.steps || 0 : 0),
      tau: data.tau || null,
      paths: data.paths || {},
      loading: false,
    } });
  } catch (_) {
    patchState({ stats: { ...getState().stats, loading: false } });
  }
}

export async function loadIngested(limit = 20) {
  const ws = getState().context.workspace || 'default';
  patchState({ rag: { ingested: { ...getState().rag.ingested, loading: true } } });
  try {
    const res = await apiFetch(`/workspaces/${encodeURIComponent(ws)}/corpus?limit=${limit}`);
    const data = await res.json();
    patchState({ rag: { ingested: { items: data.docs || [], loading: false, lastUpdated: Date.now() } } });
  } catch (_) {
    patchState({ rag: { ingested: { ...getState().rag.ingested, loading: false } } });
  }
}

export async function loadFileStatus(limit = 50) {
  const ws = getState().context.workspace || 'default';
  patchState({ rag: { status: { ...getState().rag.status, loading: true } } });
  try {
    const res = await apiFetch(`/workspaces/${encodeURIComponent(ws)}/corpus/files?limit=${limit}`);
    const data = await res.json();
    patchState({ rag: { status: { ...getState().rag.status, items: data.files || [], loading: false } } });
  } catch (_) {
    patchState({ rag: { status: { ...getState().rag.status, loading: false } } });
  }
}

export async function loadFileDetail(path) {
  if (!path) return;
  const ws = getState().context.workspace || 'default';
  const curr = getState().rag.fileDetail.byPath[path] || { loading: false };
  patchState({ rag: { fileDetail: { byPath: { ...getState().rag.fileDetail.byPath, [path]: { ...curr, loading: true } } } } });
  try {
    const [rd, rh] = await Promise.all([
      apiFetch(`/workspaces/${encodeURIComponent(ws)}/corpus/file?` + new URLSearchParams({ path })),
      apiFetch(`/workspaces/${encodeURIComponent(ws)}/corpus/file/history?` + new URLSearchParams({ path })),
    ]);
    const [detail, hist] = await Promise.all([rd.json(), rh.json()]);
    patchState({ rag: { fileDetail: { byPath: { ...getState().rag.fileDetail.byPath, [path]: { loading: false, detail, events: hist.events || [] } } } } });
  } catch (_) {
    patchState({ rag: { fileDetail: { byPath: { ...getState().rag.fileDetail.byPath, [path]: { ...curr, loading: false } } } } });
  }
}

export async function loadRagEnv() {
  // Load RAG environment info
  try {
    patchState({ rag: { env: { ...getState().rag.env, loading: true } } });
  } catch (_) {}
  try {
    const res = await apiFetch('/rag/env');
    const data = await res.json();
    patchState({ rag: { env: { ...data, loading: false } } });
  } catch (_) {
    patchState({ rag: { env: { ...getState().rag.env, loading: false } } });
  }
}

export async function loadWorkspaces() {
  patchState({ workspaces: { ...getState().workspaces, loading: true } });
  try {
    const res = await apiFetch('/workspaces');
    const data = await res.json();
    if (!res.ok) throw new Error(data && data.error || 'failed');
    const wsArr = (data.workspaces || []).sort((a,b)=>a.slug.localeCompare(b.slug));
    patchState({ workspaces: { list: wsArr, loading: false, error: null } });
  } catch (e) {
    patchState({ workspaces: { ...getState().workspaces, loading: false, error: e.message } });
  }
}

// Settings
export async function loadSettings() {
  patchState({ settings: { ...getState().settings, loading: true } });
  try {
    const res = await apiFetch('/settings');
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ settings: { data: data.settings || {}, loading: false, error: null } });
  } catch (e) {
    patchState({ settings: { ...getState().settings, loading: false, error: e.message } });
  }
}

export async function applySettings(changes) {
  try {
    const res = await apiFetch('/settings', { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ changes }) });
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    // Merge returned settings if provided; else merge the changes
    const next = data.settings || { ...getState().settings.data, ...changes };
    patchState({ settings: { data: next, loading: false, error: null } });
  } catch (e) {
    patchState({ settings: { ...getState().settings, loading: false, error: e.message } });
  }
}

// Observability (metrics + steps)
export async function obsLoadMetrics() {
  patchState({ obs: { metrics: { ...getState().obs.metrics, loading: true } } });
  try {
    const res = await apiFetch('/metrics');
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ obs: { metrics: { data, loading: false } } });
  } catch (_) {
    patchState({ obs: { metrics: { ...getState().obs.metrics, loading: false } } });
  }
}

export async function obsLoadSteps({ domain, action, limit } = {}) {
  const curr = getState().obs.steps;
  const nextFilt = {
    domain: domain !== undefined ? domain : curr.domain,
    action: action !== undefined ? action : curr.action,
    limit: limit !== undefined ? limit : curr.limit,
  };
  patchState({ obs: { steps: { ...curr, ...nextFilt, loading: true } } });
  try {
    const params = new URLSearchParams({ limit: String(nextFilt.limit || 50) });
    if (nextFilt.domain) params.set('domain', nextFilt.domain);
    if (nextFilt.action) params.set('action', nextFilt.action);
    const res = await apiFetch('/steps/recent?' + params.toString());
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ obs: { steps: { ...nextFilt, items: data.steps || [], loading: false } } });
  } catch (_) {
    patchState({ obs: { steps: { ...getState().obs.steps, loading: false } } });
  }
}

// Workspaces dashboard + trend
export async function loadWorkspaceDash() {
  const ws = getState().context.workspace || 'default';
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/stats');
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ wsDash: { ...getState().wsDash, last_step_ts: data.last_step_ts || null, doc_latest: data.doc_latest || null } });
  } catch (_) {}
}

export async function loadWorkspaceTrend(days) {
  const d = days || getState().wsDash.trend.days || 7;
  patchState({ wsDash: { ...getState().wsDash, trend: { ...getState().wsDash.trend, days: d, loading: true } } });
  const ws = getState().context.workspace || 'default';
  try {
    const tr = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/trend?days=' + d);
    const td = await tr.json();
    if (tr.ok) {
      patchState({ wsDash: { ...getState().wsDash, trend: { docs: td.docs || [], steps: td.steps || [], days: d, loading: false } } });
    } else {
      patchState({ wsDash: { ...getState().wsDash, trend: { ...getState().wsDash.trend, loading: false } } });
    }
  } catch (_) {
    patchState({ wsDash: { ...getState().wsDash, trend: { ...getState().wsDash.trend, loading: false } } });
  }
}
export async function uploadFiles({ files, overrideName, wsHeader, apiKey }) {
  const ws = getState().context.workspace || 'default';
  const form = new FormData();
  if (files && files.length === 1 && overrideName) form.append('filename', overrideName);
  if (wsHeader) form.append('workspace', wsHeader);
  if (apiKey) form.append('api_key', apiKey);
  for (const f of files || []) form.append('files', f);
  const res = await apiFetch('/rag/upload-files', { method: 'POST', body: form });
  try { await res.json(); } catch (_) {}
  // Invalidate
  await Promise.all([refreshStats(), loadIngested(), loadFileStatus()]);
}

// CP (Conformal Prediction) threshold
export async function cpLoadThreshold(domain, adminKey) {
  const dom = (domain || 'default').trim() || 'default';
  patchState({ cp: { ...getState().cp, domain: dom, loading: true, error: null } });
  try {
    const init = {};
    const url = '/cp/threshold?' + new URLSearchParams({ domain: dom });
    const res = await (adminKey ? apiFetch(url, { headers: { Authorization: 'Bearer ' + adminKey } }) : apiFetch(url));
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.error) || 'failed');
    patchState({ cp: { domain: data.domain || dom, result: data, loading: false, error: null } });
  } catch (e) {
    patchState({ cp: { ...getState().cp, loading: false, error: e.message } });
  }
}

// Evals
export async function evalsLoadSuites() {
  patchState({ evals: { ...getState().evals, suites: { ...getState().evals.suites, loading: true, error: null } } });
  try {
    const res = await apiFetch('/evals/suites');
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ evals: { ...getState().evals, suites: { items: data.suites || [], loading: false, error: null } } });
  } catch (e) {
    patchState({ evals: { ...getState().evals, suites: { ...getState().evals.suites, loading: false, error: e.message } } });
  }
}

export async function evalsLoadRuns() {
  patchState({ evals: { ...getState().evals, runs: { ...getState().evals.runs, loading: true, error: null } } });
  try {
    const res = await apiFetch('/evals/runs');
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ evals: { ...getState().evals, runs: { items: data.runs || [], loading: false, error: null } } });
  } catch (e) {
    patchState({ evals: { ...getState().evals, runs: { ...getState().evals.runs, loading: false, error: e.message } } });
  }
}

export async function evalsViewRun(runId) {
  if (!runId) return;
  patchState({ evals: { ...getState().evals, report: { ...getState().evals.report, loading: true, error: null } } });
  try {
    const res = await apiFetch('/evals/report/' + encodeURIComponent(runId));
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ evals: { ...getState().evals, report: { data, loading: false, error: null } } });
  } catch (e) {
    patchState({ evals: { ...getState().evals, report: { ...getState().evals.report, loading: false, error: e.message } } });
  }
}

export async function evalsRunSuites({ suiteIds, updateCp, adminKey, llmEnabled }) {
  const body = { suite_ids: suiteIds || [], update_cp: !!updateCp };
  const headers = { 'content-type': 'application/json' };
  try {
    if (llmEnabled) body.llm_enabled = true;
    const res = await apiFetch('/evals/run', { method:'POST', headers, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    // After run, refresh runs list
    try { await evalsLoadRuns(); } catch(_){}
    return data;
  } catch (e) {
    throw e;
  }
}

export async function tunerPropose({ suiteIds, targets }) {
  patchState({ evals: { ...getState().evals, proposal: { ...getState().evals.proposal, loading: true, error: null } } });
  const body = { suite_ids: suiteIds || [], targets: targets || {} };
  try {
    const res = await apiFetch('/tuner/propose', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    const proposal_id = data.proposal_id || null;
    const patch = (data.proposal||{}).config_patch || null;
    const canary = data.canary || [];
    patchState({ evals: { ...getState().evals, proposal: { id: proposal_id, patch, canary, loading: false, error: null } } });
  } catch (e) {
    patchState({ evals: { ...getState().evals, proposal: { ...getState().evals.proposal, loading: false, error: e.message } } });
  }
}

export async function tunerApply() {
  const id = getState().evals.proposal.id;
  if (!id) return;
  try {
    const res = await apiFetch('/tuner/apply', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ proposal_id: id, approved: true }) });
    await res.json();
  } catch (_) {}
}

export async function evalsRunAdhoc(body) {
  patchState({ evals: { ...getState().evals, adhoc: { ...getState().evals.adhoc, loading: true, error: null } } });
  try {
    const res = await apiFetch('/evals/run', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ evals: { ...getState().evals, adhoc: { data, loading: false, error: null } } });
    try { await evalsLoadRuns(); } catch(_){}
  } catch (e) {
    patchState({ evals: { ...getState().evals, adhoc: { ...getState().evals.adhoc, loading: false, error: e.message } } });
  }
}

// Workspace admin actions
export async function wsLoadKeys(ws, adminKey) {
  patchState({ wsAdmin: { ...getState().wsAdmin, keys: { ...getState().wsAdmin.keys, ws, loading: true, error: null } } });
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/keys', adminKey ? { headers: { Authorization: 'Bearer ' + adminKey } } : {});
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ wsAdmin: { ...getState().wsAdmin, keys: { ws, items: data.keys || [], loading: false, error: null } } });
  } catch (e) {
    patchState({ wsAdmin: { ...getState().wsAdmin, keys: { ...getState().wsAdmin.keys, loading: false, error: e.message } } });
  }
}

export async function wsIssueKey(ws, role, label, adminKey) {
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/keys', { method:'POST', headers:{'content-type':'application/json', ...(adminKey?{Authorization:'Bearer '+adminKey}:{}) }, body: JSON.stringify({ role, label }) });
    await res.json();
  } catch (_) {}
  await wsLoadKeys(ws, adminKey);
}

export async function wsDeactivateKey(ws, id, adminKey) {
  try {
    await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/keys/' + encodeURIComponent(id) + '/deactivate', { method:'POST', headers: adminKey?{ Authorization: 'Bearer ' + adminKey }:{} });
  } catch (_) {}
  await wsLoadKeys(ws, adminKey);
}

export async function wsDeleteWorkspace(slug) {
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(slug) + '/delete', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ purge: false }) });
    await res.json();
  } catch (_) {}
  await loadWorkspaces();
}

export async function wsLoadPolicyPacks() {
  patchState({ wsAdmin: { ...getState().wsAdmin, packs: { ...getState().wsAdmin.packs, loading: true, error: null } } });
  try {
    const res = await apiFetch('/policies');
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ wsAdmin: { ...getState().wsAdmin, packs: { items: data.policies || [], loading: false, error: null } } });
  } catch (e) {
    patchState({ wsAdmin: { ...getState().wsAdmin, packs: { ...getState().wsAdmin.packs, loading: false, error: e.message } } });
  }
}

export async function wsApplyOverlay(ws, overlay) {
  patchState({ wsAdmin: { ...getState().wsAdmin, overlay: { applying: true, error: null, ok: false } } });
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/policies/overlay', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ overlay }) });
    await res.json();
    patchState({ wsAdmin: { ...getState().wsAdmin, overlay: { applying: false, error: null, ok: true } } });
  } catch (e) {
    patchState({ wsAdmin: { ...getState().wsAdmin, overlay: { applying: false, error: e.message, ok: false } } });
  }
}

export async function wsApplyPack(ws, name) {
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/policies/apply', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ name }) });
    await res.json();
  } catch (_) {}
}

export async function wsPreviewPack(ws, name) {
  patchState({ wsAdmin: { ...getState().wsAdmin, preview: { ...getState().wsAdmin.preview, loading: true, error: null } } });
  try {
    const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/policies/preview/' + encodeURIComponent(name));
    const data = await res.json();
    if (!res.ok) throw new Error('failed');
    patchState({ wsAdmin: { ...getState().wsAdmin, preview: { data, loading: false, error: null } } });
  } catch (e) {
    patchState({ wsAdmin: { ...getState().wsAdmin, preview: { ...getState().wsAdmin.preview, loading: false, error: e.message } } });
  }
}
export default {
  setWorkspace,
  refreshStats,
  loadIngested,
  loadFileStatus,
  loadFileDetail,
  loadRagEnv,
  loadWorkspaces,
  loadSettings,
  applySettings,
  obsLoadMetrics,
  obsLoadSteps,
  loadWorkspaceDash,
  loadWorkspaceTrend,
  cpLoadThreshold,
  evalsLoadSuites,
  evalsLoadRuns,
  evalsViewRun,
  evalsRunSuites,
  tunerPropose,
  tunerApply,
  evalsRunAdhoc,
  wsLoadKeys,
  wsIssueKey,
  wsDeactivateKey,
  wsDeleteWorkspace,
  wsLoadPolicyPacks,
  wsApplyOverlay,
  wsApplyPack,
  wsPreviewPack,
  uploadFiles,
};
