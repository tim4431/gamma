// One page render at a time, the nearest page first. Every page that comes
// within the look-ahead used to start its own pdf.js render, so during a
// scroll two or three ran interleaved and one frame carried chunks of each.
// pdf.js's own viewer renders one page at a time, in order of distance from
// the view, and so does this queue (docs/research/pdf-wheel-spin.md).
//
//   enter({ distance, start })  queues a render. `distance()` is how far the
//       page is from the view in CSS px, 0 when it touches it, read again at
//       every decision; `start()` begins the render and returns pdf.js's
//       task ({promise, cancel}). Answers {promise, cancel}: the promise
//       settles as the task's does, and a cancel before the start rejects it
//       with RENDER_CANCELLED.
//   poke()  the view moved or paused: start what can start now.
//
// Two rules beyond the order. A render of a page away from the view is
// preempted when one that touches the view arrives (pdf.js can only
// cancel, so it starts over once the nearer one is done). And while `fast()`
// says the scroller is moving faster than pages can be looked at, renders
// away from the view wait; the pages a fast scroll passes would be drawn
// for nothing, and each chunk drawn is a frame the scroll loses.
// No DOM: pageRaster.js supplies the distances and the speed.

export const RENDER_CANCELLED = new Error("render cancelled before it started");

export class RenderQueue {
  constructor({ fast = () => false } = {}) {
    this.fast = fast;
    this.waiting = [];
    this.running = null;
  }

  enter({ distance, start }) {
    const entry = { distance, start, task: null, cancelled: false, restart: false, resolve: null, reject: null };
    const promise = new Promise((resolve, reject) => { entry.resolve = resolve; entry.reject = reject; });
    this.waiting.push(entry);
    this.poke();
    return {
      promise,
      cancel: () => {
        if (entry.cancelled) return;
        entry.cancelled = true;
        if (entry.task) { if (!entry.restart) entry.task.cancel(); } // preempted: already cancelled, it will not come back
        else { this.remove(entry); entry.reject(RENDER_CANCELLED); }
      },
    };
  }

  remove(entry) {
    const i = this.waiting.indexOf(entry);
    if (i >= 0) this.waiting.splice(i, 1);
  }

  poke() {
    if (!this.waiting.length) return;
    const near = this.waiting.filter((e) => e.distance() === 0);
    if (this.running) {
      // A page in view arrived while one away from it draws: that one starts over later.
      if (near.length && !this.running.restart && this.running.distance() > 0) {
        this.running.restart = true;
        this.running.task.cancel();
      }
      return;
    }
    let next = near[0];
    if (!next && !this.fast()) {
      for (const e of this.waiting) if (!next || e.distance() < next.distance()) next = e;
    }
    if (next) this.run(next);
  }

  run(entry) {
    this.remove(entry);
    this.running = entry;
    let task;
    try {
      task = entry.start();
    } catch (e) {
      this.running = null;
      entry.reject(e);
      this.poke();
      return;
    }
    entry.task = task;
    const done = (settle) => {
      this.running = null;
      settle();
      this.poke();
    };
    task.promise.then(
      (v) => done(() => entry.resolve(v)),
      (e) => done(() => {
        if (entry.restart && !entry.cancelled) {
          entry.restart = false; entry.task = null;
          this.waiting.push(entry);
        } else entry.reject(e);
      }),
    );
  }
}
