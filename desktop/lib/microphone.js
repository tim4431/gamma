// The microphone, for recording into a note and dictating to the assistant
// (docs/architecture.md "The microphone"). Electron gives a page every
// permission it asks for unless a handler decides, and this handler keeps
// that, except for the microphone on macOS. There the system asks the user
// once whether Gamma may use it, with the app's NSMicrophoneUsageDescription
// (electron-builder.cjs), and a build signed with the hardened runtime or the
// App Sandbox needs the audio-input entitlement besides. Electron does not
// ask macOS on the page's behalf, so the handler asks before the page gets
// the microphone, and refuses the page when the user said no: the page then
// reports that it could not record, and `onDenied` says where macOS keeps
// the answer. No Electron in here: `prefs` is Electron's systemPreferences.
// Unit tested by test/microphone.test.js (`npm test`).

function create({ platform = process.platform, prefs, onDenied = () => {} }) {
  async function allowed() {
    const status = prefs.getMediaAccessStatus('microphone');
    if (status === 'not-determined') return prefs.askForMediaAccess('microphone');
    if (status === 'denied' || status === 'restricted') {
      onDenied(status);
      return false;
    }
    return true;
  }

  // session.setPermissionRequestHandler's handler.
  return function handler(_webContents, permission, callback, details) {
    const audio = permission === 'media' && ((details && details.mediaTypes) || []).includes('audio');
    if (platform !== 'darwin' || !audio) return callback(true);
    allowed().then((ok) => callback(Boolean(ok)), () => callback(false));
  };
}

module.exports = { create };
