// Resolve the existing frontend toolchain without a second node_modules tree.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
export const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
export const { chromium } = require('playwright');

// The Gamma server a recorder talks to; run-case.mjs sets it per case.
export const BASE = process.env.BASE_URL || 'http://127.0.0.1:9001';
// Curated demo pages the shots are written against.
export const CURATED = { atoms: 'fy0-h_BqOHcH', qec: 'BHuT16WnxdQb' };

// The `session` cookie value the suite runner writes next to a recording.
export function readSession(dir = process.cwd()) {
  return fs.readFileSync(path.join(dir, 'session.txt'), 'utf8').trim();
}

// Retina capture. Playwright's recordVideo, and Chrome's screencast under
// `deviceScaleFactor` emulation, deliver frames at the window's CSS size, and
// CSS zoom misplaces the app's popups. A headless shell whose screen itself is
// 2× with a 1440×900 window gives the page its normal viewport at
// devicePixelRatio 2, and its screencast frames are 2880×1800.
export const VIEW = { width: 1440, height: 900, scale: 2 };
export function launchRetina(options = {}) {
  return chromium.launch({ headless: true, ...options, args: [
    `--force-device-scale-factor=${VIEW.scale}`, `--window-size=${VIEW.width},${VIEW.height}`, ...(options.args || [])] });
}
// Context options for launchRetina: the window is the viewport; light English UI.
export const RETINA = { viewport: null, colorScheme: 'light', locale: 'en-US' };

// Save the page's screencast as JPEG frames plus frames.json (seconds since the
// capture started). `clock()` reads the same timeline for a recorder's marks.
export async function startCapture(page, dir, { quality = 90 } = {}) {
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  const size = { width: VIEW.width * VIEW.scale, height: VIEW.height * VIEW.scale };
  const frames = [], t0 = Date.now();
  await page.screencast.start({ size, quality, onFrame: ({ data, timestamp }) => {
    const file = `${String(frames.length).padStart(6, '0')}.jpg`;
    fs.writeFileSync(path.join(dir, file), data);
    frames.push({ t: (timestamp - t0) / 1000, file });
  } });
  return {
    clock: () => (Date.now() - t0) / 1000,
    async stop() {
      await page.screencast.stop();
      if (frames.length < 2) throw new Error('The screencast delivered no frames');
      fs.writeFileSync(path.join(dir, 'frames.json'), JSON.stringify({ ...size, scale: VIEW.scale, frames }));
      return dir;
    },
  };
}

// Screencasts have no pointer: draw an arrow that follows the mouse, with a
// ring on each press. `zoom` is the CSS zoom the page applies to <html> (the
// arrow lives inside it, so its CSS px are zoom× the pointer's).
export function addCursor(context, { zoom = 1 } = {}) {
  return context.addInitScript((zoom) => {
    window.addEventListener('DOMContentLoaded', () => {
      const c = document.createElement('div');
      c.id = '__fakecur';
      c.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 2.5v16.2l4.3-3.9 2.9 6.4 3-1.3-2.8-6.3h5.9z" '
        + 'fill="#16181d" stroke="#fff" stroke-width="1.7" stroke-linejoin="round"/></svg>';
      c.style.cssText = 'position:fixed;z-index:2147483647;left:0;top:0;width:24px;height:24px;margin:-2.5px 0 0 -4px;'
        + 'pointer-events:none;opacity:0;filter:drop-shadow(0 1.5px 2px rgba(0,0,0,.35))';
      const arrow = c.firstChild;
      arrow.style.cssText = 'transform-origin:4px 2.5px;transition:transform .12s ease-out';
      document.body.appendChild(c);
      const z = Number(zoom) || 1;
      let x = 0, y = 0;
      document.addEventListener('mousemove', e => {
        x = e.clientX / z; y = e.clientY / z;
        c.style.opacity = '1';
        c.style.transform = `translate(${x}px,${y}px)`;
      }, true);
      document.addEventListener('mousedown', () => {
        arrow.style.transform = 'scale(.86)';
        const ring = document.createElement('div');
        ring.style.cssText = `position:fixed;z-index:2147483646;left:${x}px;top:${y}px;width:30px;height:30px;margin:-15px 0 0 -15px;`
          + 'border-radius:50%;border:2px solid rgba(37,99,235,.55);background:rgba(37,99,235,.10);pointer-events:none';
        document.body.appendChild(ring);
        ring.animate([{ transform: 'scale(.35)', opacity: 1 }, { transform: 'scale(1.25)', opacity: 0 }],
          { duration: 520, easing: 'cubic-bezier(.2,.7,.3,1)' }).onfinish = () => ring.remove();
      }, true);
      // An editor may swallow the mouseup, so the press also relaxes on its own.
      const rest = () => { arrow.style.transform = ''; };
      document.addEventListener('mouseup', rest, true);
      document.addEventListener('mousedown', () => setTimeout(rest, 320), true);
    });
  }, zoom);
}

// Eased pointer travel that remembers where the pointer is (`at()`), for the
// recorders that click or drag from the last glided-to spot.
export function pointer(page, x = 0, y = 0) {
  const glide = async (tx, ty, steps = 28) => {
    await page.mouse.move(tx, ty, { steps });
    x = tx; y = ty;
    await page.waitForTimeout(120);
  };
  const glideTo = async (target, fx = 0.5, fy = 0.5, steps = 28) => {
    const locator = typeof target === 'string' ? page.locator(target).first() : target;
    const b = await locator.boundingBox();
    if (!b) throw new Error('no box for ' + (typeof target === 'string' ? target : 'locator'));
    await glide(b.x + b.width * fx, b.y + b.height * fy, steps);
    return b;
  };
  return { glide, glideTo, at: () => ({ x, y }) };
}

// Optional recording workspace; applied before Gamma reads the initial URL.
export async function configureContext(context) {
  const workspace = process.env.MEDIA_WORKSPACE;
  if (workspace) await context.addInitScript(({ workspace, base }) => {
    if (location.origin !== new URL(base).origin) return;
    const url = new URL(location.href);
    url.searchParams.set('ws', workspace);
    history.replaceState(null, '', url);
  }, { workspace, base: process.env.BASE_URL });
  context.on('page', page => {
    page.setDefaultTimeout(20000);
    const move = page.mouse.move.bind(page.mouse);
    let px = 0, py = 0;
    page.mouse.move = async (x, y, options = {}) => {
      if (options.steps > 1) {
        const frames = 20, duration = 320, start = performance.now();
        const ox = px, oy = py;
        for (let i = 1; i <= frames; i++) {
          const u = i / frames, e = u*u*(3-2*u);
          await move(ox + (x-ox)*e, oy + (y-oy)*e);
          const wait = start + duration*i/frames - performance.now();
          if (wait > 0) await page.waitForTimeout(wait);
        }
      } else await move(x, y);
      px = x; py = y;
    };
  });
}
