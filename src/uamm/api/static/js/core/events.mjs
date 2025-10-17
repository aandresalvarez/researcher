// UAMM UI Events and helpers (vanilla, no globals required)

export const EV = Object.freeze({
  TOAST: 'uamm:toast',
  CONTEXT_CHANGE: 'uamm:context-change',
  WORKSPACE_CHANGE: 'uamm:workspace-change',
  RAG_UPLOAD_DONE: 'uamm:rag-upload-done',
  STREAM_READY: 'uamm:stream-ready',
  STREAM_TOKEN: 'uamm:stream-token',
  STREAM_FINAL: 'uamm:stream-final',
  STREAM_ERROR: 'uamm:stream-error',
});

export function emit(target, type, detail = undefined, opts = {}) {
  const { bubbles = true, composed = true, cancelable = false } = opts;
  target.dispatchEvent(
    new CustomEvent(type, { detail, bubbles, composed, cancelable })
  );
}

export function on(target, type, handler, options) {
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}
