// How this install of Gamma is updated, by its channel (backend
// gamma/version.py `channel`, in `install_info`: GET /api/admin/server-info
// carries it, and so does the guidance of a server that cannot open its
// library): `ipad` (TestFlight), `android` (the release's APK), `desktop`
// (the app's own updater), `store` (the Microsoft Store), `mas` (the Mac App
// Store), `docker` (an image its admin pulls), `checkout` (run by hand).
// Settings → Server's Updates row and the standby page (auth/StandbyPage.jsx)
// both say it from here.
import React from "react";
import { t } from "../i18n/i18n.js";

// The Microsoft Store's updates page, which the desktop shell opens in the
// Store (desktop/main.js keeps the same address); the Mac App Store's
// listing of Gamma; TestFlight, which the iPad shell hands to the system.
export const STORE_UPDATES_URL = "ms-windows-store://downloadsandupdates";
const MAS_URL = "https://apps.apple.com/app/id6814705181";
const TESTFLIGHT_URL = "itms-beta://";

const code = (text) => React.createElement("code", null, text);

// The button to where the update is, `{href, label}`, or null where there
// is none: a server, which its admin updates.
export function updateLink(install) {
  switch (install?.channel) {
    case "ipad": return { href: TESTFLIGHT_URL, label: t("Open TestFlight") };
    case "android":
    case "desktop": return { href: install.releases_url, label: t("Download the latest Gamma") };
    case "store": return { href: STORE_UPDATES_URL, label: t("Open Store") };
    case "mas": return { href: MAS_URL, label: t("Open the App Store") };
    default: return null;
  }
}

// The sentence that says how it updates. `update`: the newer release or
// build the server's check found (`{kind, version}`), which Settings knows
// and the standby page does not: a Docker server then names that version,
// and a build of a branch other than main is built from it.
export function updateHowTo(install, update = null) {
  switch (install?.channel) {
    case "ipad": return t("In TestFlight, tap Update next to Gamma, then open Gamma again.");
    case "android": return t("Install the newest APK over this one, then open Gamma again.");
    case "desktop":
      return t("The desktop app updates itself: its top bar offers the update when it is ready, and Help → Check for Updates… asks now.");
    case "store":
      return t("Installed from the Microsoft Store: the Store has a release once Microsoft certifies it, one to three days after it is out, and Windows then installs it. To get it sooner, Open Store → Get updates.");
    case "mas": return t("Installed from the Mac App Store, which delivers its updates: update Gamma there, then open it again.");
    case "checkout": return t("Run from a checkout: update it to the newest commit and start the server again.");
    default: break;
  }
  const branchBuild = update ? update.kind === "build" : /-dev\./.test(install?.version || "");
  if (branchBuild && install.branch && install.branch !== "main") {
    return t("Newer commits are on {branch}: build that branch's image and restart the container.", { branch: code(install.branch) });
  }
  const latest = code(`${install?.image}:latest`);
  return update?.kind === "release"
    ? t("A server in Docker does not update itself: pull {latest} (or {version}) and restart the container.", { latest, version: code(`:${update.version}`) })
    : t("A server in Docker does not update itself: pull {latest} and restart the container.", { latest });
}
