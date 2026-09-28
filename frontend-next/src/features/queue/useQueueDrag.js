/**
 * Pointer-based drag engine for the queue, ported from frontend/app.js:5416-5650.
 *
 * One engine serves every drag source via a `data-drag-source` attribute on
 * the row:
 *   queue      — reorder within the queue (carries data-drag-idx)
 *   rec        — insert a recommendation at the drop index (carries data-drag-key)
 *   search-lib — same, for search result rows owned by the search feature.
 *                They opt in simply by rendering those attributes; nothing here
 *                imports from that feature.
 *
 * Pointer Events rather than HTML5 drag-and-drop, because HTML5 DnD does not
 * fire on touchscreens at all and this has to work on a phone.
 *
 * The ghost card and drop indicator are positioned by writing to style
 * directly instead of through React state. That is deliberate: they move with
 * every pointermove (~60/sec) and routing that through a render would re-render
 * the whole sidebar mid-drag. Only the `dragging` boolean is state.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { trackKey } from '../../lib/vibe';

const DRAG_THRESHOLD = 6; // px of mouse travel before a drag begins

export function useQueueDrag({ queue, recs, onReorder, onInsert, styles }) {
  const listRef = useRef(null);
  const emptyRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  // Latest values, read from inside the pointer handlers without re-binding
  // the document listener on every queue change.
  const dataRef = useRef({ queue, recs, onReorder, onInsert });
  dataRef.current = { queue, recs, onReorder, onInsert };

  const drag = useRef({
    active: false, started: false, sourceEl: null, sourceKind: null,
    sourceIdx: -1, sourceKey: null, startX: 0, startY: 0,
    ghost: null, ghostWidth: 0, indicator: null, dropTargetIdx: -1, ranCleanup: false,
  });

  /** Resolve a drag source back to the full track object. */
  const trackFromSource = useCallback((kind, idx, key) => {
    const { queue: q, recs: r } = dataRef.current;
    if (kind === 'queue') return q[idx] || null;
    if (kind === 'rec') return r.find((x) => trackKey(x) === key) || null;
    // search-lib rows are foreign; we only know their key, which is enough to
    // let the search feature resolve them on drop via the same attribute.
    return null;
  }, []);

  useEffect(() => {
    const d = drag.current;

    const updateDropTarget = (x, y) => {
      const list = listRef.current;
      const setIndicator = (left, top, width, visible) => {
        if (!d.indicator) return;
        d.indicator.style.left = `${left}px`;
        d.indicator.style.top = `${top}px`;
        d.indicator.style.width = `${width}px`;
        d.indicator.style.opacity = visible ? '1' : '0';
      };

      // Empty queue: the empty-state block is itself the drop zone for index 0.
      if (!list) {
        const empty = emptyRef.current;
        const box = empty ? empty.getBoundingClientRect() : null;
        if (box && x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) {
          d.dropTargetIdx = 0;
          setIndicator(box.left, box.top + box.height / 2, box.width, true);
        } else {
          d.dropTargetIdx = -1;
          setIndicator(0, 0, 0, false);
        }
        return;
      }

      const box = list.getBoundingClientRect();
      // 20px of slop above/below so a drop just past the last row still lands.
      if (x < box.left || x > box.right || y < box.top - 20 || y > box.bottom + 20) {
        d.dropTargetIdx = -1;
        setIndicator(0, 0, 0, false);
        return;
      }

      const rows = list.querySelectorAll('[data-drag-source]');
      let targetIdx = rows.length;
      let indicatorY = null;
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i].getBoundingClientRect();
        if (y < r.top + r.height / 2) {
          targetIdx = i;
          indicatorY = r.top;
          break;
        }
      }
      if (indicatorY == null) {
        indicatorY = rows.length
          ? rows[rows.length - 1].getBoundingClientRect().bottom
          : box.top;
      }
      d.dropTargetIdx = targetIdx;
      setIndicator(box.left, indicatorY, box.width, true);
    };

    const beginDrag = (ev) => {
      if (d.started) return;
      d.started = true;
      setDragging(true);

      const rect = d.sourceEl.getBoundingClientRect();
      const t = trackFromSource(d.sourceKind, d.sourceIdx, d.sourceKey);

      // A fresh card rather than a clone of the source row: sources have
      // different layouts (queue rows are grid, search rows are flex) and
      // cloning leaked those mismatched layouts into the ghost.
      const ghost = document.createElement('div');
      ghost.className = styles.ghost;
      const art = document.createElement(t?.artwork_url ? 'img' : 'div');
      art.className = styles.ghostArt;
      if (t?.artwork_url) {
        art.src = t.artwork_url;
        art.alt = '';
      }
      const body = document.createElement('div');
      const title = document.createElement('div');
      title.className = styles.ghostTitle;
      title.textContent = t?.title || 'Track';
      const sub = document.createElement('div');
      sub.className = styles.ghostSub;
      sub.textContent = t?.artist || '';
      body.append(title, sub);
      ghost.append(art, body);

      const width = Math.min(Math.max(rect.width, 220), 320);
      ghost.style.width = `${width}px`;
      ghost.style.left = `${ev.clientX - width / 2}px`;
      ghost.style.top = `${ev.clientY - 24}px`;
      document.body.appendChild(ghost);
      d.ghost = ghost;
      d.ghostWidth = width;

      d.sourceEl.classList.add(styles.isDragging);
      // Clear any text selection the mousedown started before the threshold.
      try { window.getSelection()?.removeAllRanges(); } catch { /* ignore */ }

      const ind = document.createElement('div');
      ind.className = styles.dropIndicator;
      document.body.appendChild(ind);
      d.indicator = ind;

      updateDropTarget(ev.clientX, ev.clientY);
    };

    const onPointerMove = (ev) => {
      if (!d.active) return;
      if (!d.started) {
        const dx = ev.clientX - d.startX;
        const dy = ev.clientY - d.startY;
        if (dx * dx + dy * dy < DRAG_THRESHOLD * DRAG_THRESHOLD) return;
        beginDrag(ev);
      }
      ev.preventDefault();
      if (d.ghost) {
        d.ghost.style.left = `${ev.clientX - d.ghostWidth / 2}px`;
        d.ghost.style.top = `${ev.clientY - 24}px`;
      }
      updateDropTarget(ev.clientX, ev.clientY);
    };

    const cleanup = () => {
      if (d.ranCleanup) return;
      d.ranCleanup = true;
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      document.removeEventListener('pointercancel', cleanup);
      d.sourceEl?.classList.remove(styles.isDragging);
      d.ghost?.remove();
      d.indicator?.remove();
      Object.assign(d, {
        active: false, started: false, sourceEl: null, sourceKind: null,
        sourceIdx: -1, sourceKey: null, ghost: null, indicator: null, dropTargetIdx: -1,
      });
      setDragging(false);
    };

    function onPointerUp() {
      if (!d.active) return;
      const { started, dropTargetIdx: target, sourceKind: kind, sourceIdx, sourceKey } = d;
      cleanup();
      if (!started || target < 0) return;
      const { onReorder: reorder, onInsert: insert, recs: r } = dataRef.current;
      if (kind === 'queue') {
        reorder(sourceIdx, target);
      } else if (kind === 'rec') {
        const t = r.find((x) => trackKey(x) === sourceKey);
        if (t) insert(t, target);
      } else if (kind === 'search-lib') {
        // The search feature owns resolution for its own rows; it listens for
        // this event and calls back with the track.
        document.dispatchEvent(
          new CustomEvent('vibescape:queue-drop', { detail: { key: sourceKey, index: target } })
        );
      }
    }

    const onPointerDown = (ev) => {
      if (d.active) return;
      if (ev.button !== undefined && ev.button !== 0) return; // left button only
      const source = ev.target.closest?.('[data-drag-source]');
      if (!source) return;
      const kind = source.getAttribute('data-drag-source');
      const isTouch = ev.pointerType === 'touch';
      const handle = ev.target.closest?.('[data-drag-handle]');

      // On touch, queue/rec rows must be dragged by the grip — the whole row is
      // a tap target (jump / play) and there is no movement threshold that can
      // tell a tap from the start of a drag. Mouse can drag from anywhere on
      // the row because the 6px threshold protects the click.
      if ((kind === 'queue' || kind === 'rec') && isTouch && !handle) return;
      if (kind === 'search-lib' && isTouch) return; // no visible queue there on mobile

      Object.assign(d, {
        active: true, started: false, ranCleanup: false,
        startX: ev.clientX, startY: ev.clientY,
        sourceEl: source, sourceKind: kind,
        sourceIdx: kind === 'queue' ? parseInt(source.getAttribute('data-drag-idx'), 10) : -1,
        sourceKey: source.getAttribute('data-drag-key') || null,
      });

      // Grip drags on touch start immediately, both because it feels right and
      // because it stops the browser claiming the gesture as a scroll.
      if (handle && isTouch) {
        ev.preventDefault();
        beginDrag(ev);
      }
      // Mouse: suppress the text-selection gesture. preventDefault on
      // pointerdown does not cancel the later click, so tap-to-jump survives.
      if (!isTouch) ev.preventDefault();

      document.addEventListener('pointermove', onPointerMove, { passive: false });
      document.addEventListener('pointerup', onPointerUp);
      document.addEventListener('pointercancel', cleanup);
    };

    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      cleanup();
    };
  }, [trackFromSource, styles]);

  return { listRef, emptyRef, dragging };
}
