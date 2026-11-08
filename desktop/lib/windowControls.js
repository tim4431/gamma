// Linux: the ends of the shell bar the window controls take. The title-bar
// overlay follows the desktop's own button layout, GTK's decoration layout
// "left:right" (the buttons of each side, comma-separated: GNOME's
// "appmenu:close", elementary's "close:maximize"), and a right-to-left UI
// mirrors it. The layout is GNOME's setting (KDE's GTK settings write it
// too), else GTK 3's settings.ini (the GTK Chromium draws with), else GTK's
// default, the right end. main.js reads it at the window's start and on
// each focus, so a layout changed meanwhile is seen on the way back.

const { execFile } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// The buttons the overlay draws (appmenu, menu, icon and spacer it does not).
const BUTTONS = new Set(['close', 'minimize', 'maximize']);
// Languages written right to left, by their primary subtag.
const RTL = new Set(['ar', 'ckb', 'dv', 'fa', 'he', 'iw', 'ps', 'sd', 'ug', 'ur', 'yi']);

// 'left', 'right', 'both' or 'none': the ends holding at least one button.
// A layout with no colon puts every button on the left, as GTK does.
function sides(layout, locale) {
  const [left = '', right = ''] = String(layout || '').trim().replace(/^'(.*)'$/, '$1').split(':');
  const has = (part) => part.split(',').some((b) => BUTTONS.has(b.trim()));
  let l = has(left);
  let r = has(right);
  if (RTL.has(String(locale || '').toLowerCase().split(/[-_]/)[0])) [l, r] = [r, l];
  return l && r ? 'both' : l ? 'left' : r ? 'right' : 'none';
}

function gsettingsLayout() {
  return new Promise((resolve) => {
    execFile('gsettings', ['get', 'org.gnome.desktop.wm.preferences', 'button-layout'], { timeout: 2000 },
      (err, out) => resolve(err ? null : String(out).trim() || null));
  });
}

function gtkIniLayout(env = process.env) {
  const config = env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  try {
    const m = /^\s*gtk-decoration-layout\s*=\s*(.*?)\s*$/m.exec(fs.readFileSync(path.join(config, 'gtk-3.0', 'settings.ini'), 'utf8'));
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

async function read(locale) {
  return sides((await gsettingsLayout()) || gtkIniLayout() || 'menu:close', locale);
}

module.exports = { sides, gtkIniLayout, read };
