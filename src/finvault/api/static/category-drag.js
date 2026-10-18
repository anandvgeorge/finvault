"use strict";

// Pointer events keep mouse, pen and touch dragging on the same path.
// Only the move handle captures gestures, leaving tile labels and page scrolling alone.
class CategoryDrag {
    constructor(document, { container, scroller, canDrag, onMove, announce }) {
        this.document = document;
        this.view = document.defaultView;
        this.container = container;
        this.scroller = scroller;
        this.canDrag = canDrag;
        this.onMove = onMove;
        this.announce = announce;
        this.active = null;
        this.frame = null;
        this.suppressClick = false;

        container.addEventListener("pointerdown", event => this.start(event));
        document.addEventListener("pointermove", event => this.move(event), { passive: false });
        document.addEventListener("pointerup", event => this.finish(event));
        document.addEventListener("pointercancel", event => {
            if (this.active?.pointerId === event.pointerId) this.cancel(false);
        });
        document.addEventListener("lostpointercapture", event => {
            if (this.active?.pointerId === event.pointerId) this.cancel();
        });
        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && this.active) { event.preventDefault(); this.cancel(); }
        });
        document.addEventListener("pointerdown", () => { if (!this.active) this.suppressClick = false; }, true);
        document.addEventListener("click", event => {
            if (this.suppressClick && event.detail !== 0) { event.preventDefault(); event.stopImmediatePropagation(); }
        }, true);
        this.view.addEventListener("blur", () => this.cancel(false));
        this.view.addEventListener("resize", () => this.cancel());
        document.addEventListener("visibilitychange", () => { if (document.hidden) this.cancel(false); });
    }

    start(event) {
        if (this.active || event.isPrimary === false || event.button !== 0 || !this.canDrag()) return;
        const handle = event.target.closest?.(".category-move");
        const row = handle?.closest("[data-category-row]");
        if (!row || !this.container.contains(row) || handle.matches(":disabled")) return;
        const section = row.closest(".category-section");
        this.active = {
            pointerId: event.pointerId, handle, row, id: row.dataset.categoryRow,
            source: section.dataset.groupId || null,
            label: row.querySelector(".category-name").textContent,
            startX: event.clientX, startY: event.clientY,
            x: event.clientX, y: event.clientY, dragging: false, target: null,
        };
        // Native text dragging must not compete with the pointer gesture.
        try { handle.setPointerCapture(event.pointerId); } catch {}
    }

    move(event) {
        const state = this.active;
        if (!state || event.pointerId !== state.pointerId) return;
        if (!this.canDrag() || !state.row.isConnected) { this.cancel(); return; }
        state.x = event.clientX; state.y = event.clientY;
        if (!state.dragging) {
            if (Math.hypot(state.x - state.startX, state.y - state.startY) < 8) return;
            state.dragging = true;
            state.row.classList.add("is-dragging");
            this.document.body.classList.add("category-dragging");
            state.preview = this.document.createElement("div");
            state.preview.className = "category-drag-preview";
            state.preview.textContent = state.label;
            state.preview.setAttribute("aria-hidden", "true");
            this.document.body.appendChild(state.preview);
            this.announce(`Moving ${state.label}. Drop into a section, or press Escape to cancel.`);
            this.frame = this.view.requestAnimationFrame(() => this.tick());
        }
        event.preventDefault();
        this.updateTarget();
    }

    targetAt(x, y) {
        const section = this.document.elementFromPoint(x, y)?.closest(".category-section");
        return section && this.container.contains(section) ? section : null;
    }

    updateTarget() {
        const state = this.active;
        const target = this.targetAt(state.x, state.y);
        if (target !== state.target) {
            state.target?.classList.remove("is-drop-target");
            state.target = target;
            target?.classList.add("is-drop-target");
        }
        const width = state.preview.offsetWidth, height = state.preview.offsetHeight;
        const left = Math.max(8, Math.min(state.x + 12, this.view.innerWidth - width - 8));
        const top = Math.max(8, Math.min(state.y + 12, this.view.innerHeight - height - 8));
        state.preview.style.transform = `translate(${left}px, ${top}px)`;
    }

    edgeSpeed(y, top, bottom) {
        if (bottom <= top || y < top - 24 || y > bottom + 24) return 0;
        const edge = Math.min(40, (bottom - top) / 3);
        if (y < top + edge) return -Math.min(12, Math.ceil((top + edge - y) / 3));
        if (y > bottom - edge) return Math.min(12, Math.ceil((y - bottom + edge) / 3));
        return 0;
    }

    scroll() {
        const { x, y } = this.active;
        const header = this.document.querySelector(".page-header")?.getBoundingClientRect();
        const footer = this.document.querySelector(".save-bar")?.getBoundingClientRect();
        const top = Math.max(0, header?.bottom ?? 0);
        const bottom = Math.min(this.view.innerHeight, footer?.top ?? this.view.innerHeight);
        const rect = this.scroller.getBoundingClientRect();
        if (x < rect.left - 24 || x > rect.right + 24) return;
        if (this.scroller.scrollHeight > this.scroller.clientHeight + 1) {
            const previous = this.scroller.scrollTop;
            this.scroller.scrollTop += this.edgeSpeed(y, Math.max(top, rect.top), Math.min(bottom, rect.bottom));
            if (this.scroller.scrollTop !== previous) return;
        }
        this.view.scrollBy(0, this.edgeSpeed(y, top, bottom));
    }

    tick() {
        this.frame = null;
        if (!this.active?.dragging) return;
        if (!this.canDrag() || !this.active.row.isConnected) { this.cancel(); return; }
        this.scroll();
        this.updateTarget();
        this.frame = this.view.requestAnimationFrame(() => this.tick());
    }

    finish(event) {
        const state = this.active;
        if (!state) {
            if (this.suppressClick) this.view.setTimeout(() => { this.suppressClick = false; }, 0);
            return;
        }
        if (event.pointerId !== state.pointerId) return;
        if (!state.dragging) { this.cleanup(); return; }
        event.preventDefault();
        this.suppressClick = true;
        const target = this.canDrag() ? this.targetAt(event.clientX, event.clientY) : null;
        const groupId = target ? target.dataset.groupId || null : undefined;
        this.cleanup();
        // Suppress the click generated by releasing a dragged move button.
        this.view.setTimeout(() => { this.suppressClick = false; }, 0);
        if (target && groupId !== state.source) this.onMove(state.id, groupId);
        else this.announce(`${state.label} kept in its current section.`);
    }

    cancel(suppressClick = true) {
        if (this.active) {
            if (this.active.dragging) this.announce(`Move cancelled. ${this.active.label} kept in its current section.`);
            this.suppressClick = suppressClick;
            this.cleanup();
        } else if (!suppressClick) this.suppressClick = false;
    }

    cleanup() {
        const state = this.active;
        this.active = null;
        if (this.frame !== null) { this.view.cancelAnimationFrame(this.frame); this.frame = null; }
        state.target?.classList.remove("is-drop-target");
        state.row.classList.remove("is-dragging");
        state.preview?.remove();
        this.document.body.classList.remove("category-dragging");
        try { state.handle.releasePointerCapture(state.pointerId); } catch {}
    }
}
