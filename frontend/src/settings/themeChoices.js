// The themes Settings → Appearance offers, in its order: id, name, hint.
// A module of its own, free of React, so the design pages' theme sheet
// (tools/designSheets.mjs) names each theme as the app does.
import { T } from "../shared/i18n/locales.js";

export const THEME_CHOICES = [
  ["system", T("System"), T("Match your device")],
  ["light", T("Light"), T("Bright & crisp")],
  ["dark", T("Dark"), T("A quieter backdrop")],
  ["gamma-light", T("Gamma Light"), T("Warm gray & amber")],
  ["gamma-dark", T("Gamma Dark"), T("Charcoal & soft gold")],
  ["sepia", T("Sepia"), T("Warm paper, deep ink")],
  ["solarized", T("Solarized Light"), T("Warm paper, softer ink")],
  ["gray", T("Gray"), T("Soft & neutral")],
];
