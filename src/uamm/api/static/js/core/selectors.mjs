import { getState } from './store.mjs';

const EMPTY_FILE_DETAIL = Object.freeze({ chunks: [], events: [], loading: false });

export const selectContext = (s) => s.context;
export const selectRoute = (s) => s.route;
export const selectStats = (s) => s.stats;
export const selectWorkspaces = (s) => s.workspaces;
export const selectSettings = (s) => s.settings;

export const selectRagIngested = (s) => s.rag.ingested;
export const selectRagStatus = (s) => s.rag.status;
export const selectRagEnv = (s) => s.rag.env;
export const selectRagFileDetail = (path) => (s) => s.rag.fileDetail.byPath[path] || EMPTY_FILE_DETAIL;
export const selectObsMetrics = (s) => s.obs.metrics;
export const selectObsSteps = (s) => s.obs.steps;
export const selectWsDash = (s) => s.wsDash;
export const selectCp = (s) => s.cp;
export const selectEvalsSuites = (s) => s.evals.suites;
export const selectEvalsRuns = (s) => s.evals.runs;
export const selectEvalsReport = (s) => s.evals.report;
export const selectEvalsProposal = (s) => s.evals.proposal;
export const selectEvalsAdhoc = (s) => s.evals.adhoc;
export const selectWsAdminKeys = (s) => s.wsAdmin.keys;
export const selectWsAdminPacks = (s) => s.wsAdmin.packs;
export const selectWsAdminPreview = (s) => s.wsAdmin.preview;
export const selectWsAdminOverlay = (s) => s.wsAdmin.overlay;

export default {
  selectContext,
  selectRoute,
  selectStats,
  selectWorkspaces,
  selectSettings,
  selectRagIngested,
  selectRagStatus,
  selectRagEnv,
  selectRagFileDetail,
  selectObsMetrics,
  selectObsSteps,
  selectWsDash,
  selectCp,
  selectEvalsSuites,
  selectEvalsRuns,
  selectEvalsReport,
  selectEvalsProposal,
  selectEvalsAdhoc,
  selectWsAdminKeys,
  selectWsAdminPacks,
  selectWsAdminPreview,
  selectWsAdminOverlay,
};

