// lib/windowControls: which ends of the shell bar a Linux desktop's
// title-bar layout gives the window controls. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { sides, gtkIniLayout } = require('../lib/windowControls');

test('the layout names the ends that hold a window button', () => {
  assert.equal(sides("'appmenu:close'"), 'right'); // GNOME, as gsettings prints it
  assert.equal(sides('appmenu:minimize,maximize,close'), 'right');
  assert.equal(sides('close,minimize,maximize:'), 'left');
  assert.equal(sides('close:maximize'), 'both'); // elementary OS
  assert.equal(sides('icon:'), 'none'); // no button the overlay draws
  assert.equal(sides('close,minimize'), 'left'); // no colon: all on the left
  assert.equal(sides(' close : '), 'left');
});

test('a right-to-left UI mirrors the layout', () => {
  assert.equal(sides('appmenu:close', 'ar'), 'left');
  assert.equal(sides('close:', 'he-IL'), 'right');
  assert.equal(sides('close:maximize', 'fa'), 'both');
  assert.equal(sides('appmenu:close', 'en-US'), 'right');
});

test("GTK's settings.ini gives the layout when GNOME's setting is missing", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-wco-'));
  try {
    assert.equal(gtkIniLayout({ XDG_CONFIG_HOME: dir }), null);
    fs.mkdirSync(path.join(dir, 'gtk-3.0'));
    fs.writeFileSync(path.join(dir, 'gtk-3.0', 'settings.ini'), '[Settings]\ngtk-theme-name=Breeze\ngtk-decoration-layout=close,minimize:menu \n');
    assert.equal(gtkIniLayout({ XDG_CONFIG_HOME: dir }), 'close,minimize:menu');
    assert.equal(sides(gtkIniLayout({ XDG_CONFIG_HOME: dir })), 'left');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
