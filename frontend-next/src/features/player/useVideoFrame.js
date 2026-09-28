import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Drag + 8-way resize for the video frame. Ported from
 * frontend/app.js:1730-1928 (`setupVideoFrameDragResize`).
 *
 * Behaviour: the first drag/resize gesture "detaches" the frame — it flips to
 * position:fixed, seeded with its current on-screen rect so the grab feels
 * continuous. Size and position persist in localStorage, so the panel
 * remembers where it was parked. The dock button snaps it back into the
 * layout.
 *
 * Every pointermove writes `style.left/top/width/height` DIRECTLY on the node.
 * These fire ~60x/second; routing them through React state would re-render the
 * player tree on every frame. Only `detached` — which changes once per gesture
 * — is state.
 */

const STORAGE_KEY = 'vs.videoFrame.rect';
const MIN_W = 240;
const MIN_H = 160;

function loadRect() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const r = JSON.parse(raw);
    if (!r || typeof r.left !== 'number' || typeof r.top !== 'number') return null;
    return r;
  } catch {
    return null;
  }
}

function saveRect(rect) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(rect)); } catch { /* storage off */ }
}

function clearRect() {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* storage off */ }
}

/** Keep the panel on-screen and above the minimum size. */
function clamp(rect) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.max(MIN_W, Math.min(rect.width, vw * 0.96));
  const height = Math.max(MIN_H, Math.min(rect.height, vh * 0.92));
  return {
    left: Math.max(0, Math.min(rect.left, vw - width)),
    top: Math.max(0, Math.min(rect.top, vh - height)),
    width,
    height,
  };
}

export default function useVideoFrame(frameRef) {
  const [detached, setDetached] = useState(() => !!loadRect());
  const dragRef = useRef(null);
  const resizeRef = useRef(null);

  const apply = useCallback((rect) => {
    const node = frameRef.current;
    if (!node) return null;
    const c = clamp(rect);
    node.style.left = `${c.left}px`;
    node.style.top = `${c.top}px`;
    node.style.width = `${c.width}px`;
    node.style.height = `${c.height}px`;
    return c;
  }, [frameRef]);

  /** Seed the fixed-position rect from wherever the frame currently sits. */
  const ensureDetached = useCallback(() => {
    const node = frameRef.current;
    if (!node) return null;
    const r = node.getBoundingClientRect();
    const rect = { left: r.left, top: r.top, width: r.width, height: r.height };
    if (!detached) setDetached(true);
    return apply(rect) || rect;
  }, [frameRef, detached, apply]);

  // Restore a parked rect on mount.
  useEffect(() => {
    const saved = loadRect();
    if (saved && frameRef.current) apply(saved);
  }, [frameRef, apply]);

  // Keep it on-screen when the viewport shrinks.
  useEffect(() => {
    if (!detached) return;
    const onResize = () => {
      const node = frameRef.current;
      if (!node) return;
      const r = node.getBoundingClientRect();
      const next = apply({ left: r.left, top: r.top, width: r.width, height: r.height });
      if (next) saveRect(next);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [detached, frameRef, apply]);

  /* ------------------------------------------------------------------ drag */

  const onDragPointerDown = useCallback((ev) => {
    if (ev.button !== 0) return;
    // Don't swallow clicks on the dock button or any future control, and
    // never start a drag from a resize handle — those sit on top of the frame
    // and must win, otherwise grabbing an edge moves the panel instead of
    // resizing it.
    if (ev.target.closest('button, a, input, [data-resize]')) return;

    const start = ensureDetached();
    if (!start) return;

    dragRef.current = {
      pointerId: ev.pointerId,
      surface: ev.currentTarget,
      offsetX: ev.clientX - start.left,
      offsetY: ev.clientY - start.top,
      width: start.width,
      height: start.height,
    };
    frameRef.current?.classList.add('is-dragging');
    try { ev.currentTarget.setPointerCapture(ev.pointerId); } catch { /* no capture */ }
    ev.preventDefault();
  }, [ensureDetached, frameRef]);

  const onDragPointerMove = useCallback((ev) => {
    const d = dragRef.current;
    if (!d || ev.pointerId !== d.pointerId) return;
    const rect = apply({
      left: ev.clientX - d.offsetX,
      top: ev.clientY - d.offsetY,
      width: d.width,
      height: d.height,
    });
    if (rect) saveRect(rect);
  }, [apply]);

  const endDrag = useCallback((ev) => {
    const d = dragRef.current;
    if (!d || (ev && ev.pointerId !== d.pointerId)) return;
    frameRef.current?.classList.remove('is-dragging');
    try { d.surface.releasePointerCapture(d.pointerId); } catch { /* already released */ }
    dragRef.current = null;
  }, [frameRef]);

  /* ---------------------------------------------------------------- resize */

  const onResizePointerDown = useCallback((ev) => {
    if (ev.button !== 0) return;
    const dir = ev.currentTarget.dataset.resize || 'se';
    const start = ensureDetached();
    if (!start) return;

    resizeRef.current = {
      pointerId: ev.pointerId,
      surface: ev.currentTarget,
      dir,
      startX: ev.clientX,
      startY: ev.clientY,
      left: start.left,
      top: start.top,
      right: start.left + start.width,
      bottom: start.top + start.height,
    };
    frameRef.current?.classList.add('is-resizing');
    try { ev.currentTarget.setPointerCapture(ev.pointerId); } catch { /* no capture */ }
    ev.preventDefault();
    ev.stopPropagation();
  }, [ensureDetached, frameRef]);

  const onResizePointerMove = useCallback((ev) => {
    const r = resizeRef.current;
    if (!r || ev.pointerId !== r.pointerId) return;

    const dx = ev.clientX - r.startX;
    const dy = ev.clientY - r.startY;
    let { left, top, right, bottom } = r;

    // Anchor edges stay put; grabbed edges follow the pointer.
    if (r.dir.includes('e')) right = Math.max(r.left + MIN_W, r.right + dx);
    if (r.dir.includes('w')) left = Math.min(r.right - MIN_W, r.left + dx);
    if (r.dir.includes('s')) bottom = Math.max(r.top + MIN_H, r.bottom + dy);
    if (r.dir.includes('n')) top = Math.min(r.bottom - MIN_H, r.top + dy);

    const rect = apply({ left, top, width: right - left, height: bottom - top });
    if (rect) saveRect(rect);
  }, [apply]);

  const endResize = useCallback((ev) => {
    const r = resizeRef.current;
    if (!r || (ev && ev.pointerId !== r.pointerId)) return;
    frameRef.current?.classList.remove('is-resizing');
    try { r.surface.releasePointerCapture(r.pointerId); } catch { /* already released */ }
    resizeRef.current = null;
  }, [frameRef]);

  /* ------------------------------------------------------------------ dock */

  const dock = useCallback(() => {
    const node = frameRef.current;
    if (node) {
      node.style.left = '';
      node.style.top = '';
      node.style.width = '';
      node.style.height = '';
    }
    clearRect();
    setDetached(false);
  }, [frameRef]);

  return {
    detached,
    dock,
    dragProps: {
      onPointerDown: onDragPointerDown,
      onPointerMove: onDragPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
    },
    resizeProps: {
      onPointerDown: onResizePointerDown,
      onPointerMove: onResizePointerMove,
      onPointerUp: endResize,
      onPointerCancel: endResize,
    },
  };
}

export const RESIZE_DIRS = ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se'];
