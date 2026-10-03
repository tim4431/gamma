// Surfaces fetched on first use instead of with the startup bundle
// (docs/dev/frontend-refactor.md, "Lazy boundaries"): `lazySurface` makes the
// component, `LazyBoundary` holds the place while it loads, `useLatch` keeps
// one mounted once it was wanted, `whenIdle` defers a preload.
import React from "react";

// A component whose module is fetched the first time it renders, or earlier
// through `Component.preload()` (on hover or focus of what opens it, at
// startup when it is on the first screen). Once the module is in, it renders
// straight away, without a Suspense round. A failed fetch (a deploy replaced
// the hashed files under an open tab) is not cached: the next mount retries.
export function lazySurface(load, pick = (module) => module.default) {
  let Loaded = null;
  let pending = null;
  let Lazy = null;
  const fetchModule = () => {
    pending ||= load().then((module) => {
      Loaded = pick(module);
      return { default: Loaded };
    }, (error) => {
      pending = null;
      Lazy = null;
      throw Object.assign(error instanceof Error ? error : new Error(String(error)), { lazySurface: true });
    });
    return pending;
  };
  function Surface(props) {
    if (Loaded) return <Loaded {...props} />;
    Lazy ||= React.lazy(fetchModule);
    return <Lazy {...props} />;
  }
  Surface.preload = () => { fetchModule().catch(() => {}); };
  return Surface;
}

// Suspense for a lazy surface, showing `fallback` while it loads: null for a
// dialog or popover that opens on a click, the window's own empty shell for
// one on screen at load, so the layout never jumps. A surface whose module
// could not be fetched keeps showing the fallback instead of taking the app
// down; any other error goes on up as it did before the split.
export class LazyBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error) {
    if (error?.lazySurface) console.warn("Could not load part of the app; reload the page to get it.", error);
  }
  render() {
    const { error } = this.state;
    const { fallback = null, children } = this.props;
    if (error && !error.lazySurface) throw error;
    return error ? fallback : <React.Suspense fallback={fallback}>{children}</React.Suspense>;
  }
}

// True from the first render where `on` is: a surface that used to be
// mounted from startup (and did nothing while closed) mounts when first
// wanted and then stays mounted, keeping its state between openings.
export function useLatch(on) {
  const latched = React.useRef(false);
  if (on) latched.current = true;
  return latched.current;
}

// Run `fn` when the main thread is next idle after `delay` ms (Safari has no
// requestIdleCallback: a short timeout there). The main thread is idle while
// the first screen still waits on its requests, so a preload that should not
// compete with them passes a delay. Returns a cancel function.
export function whenIdle(fn, delay = 0) {
  let idle = null;
  const timer = setTimeout(() => {
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(() => fn(), { timeout: 2000 });
      idle = () => window.cancelIdleCallback(id);
    } else {
      const id = setTimeout(fn, 200);
      idle = () => clearTimeout(id);
    }
  }, delay);
  return () => { clearTimeout(timer); idle?.(); };
}
