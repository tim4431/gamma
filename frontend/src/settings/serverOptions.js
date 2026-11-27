// The server options an admin sets in Settings (gamma/server_options.py):
// /api/admin/settings answers them as `options` (a secret as `<name>_set`)
// and takes {options: {name: value}}. The sections that show them (Users ›
// Guests, Server › Online services and Work folders, Tool usage › Online
// search for every account) save through this hook: only the options given,
// shown at once and put back when the save fails. `saveNumber` is a
// whole-number option's box committed.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { useSettingsRead } from "./settingsReads.js";
import { t } from "../shared/i18n/i18n.js";

export const SETTINGS_READ = "/admin/settings";

export function useServerOptions({ setStatus } = {}) {
  const read = useSettingsRead(SETTINGS_READ);
  const [error, setError] = React.useState("");
  const options = read.data?.options || null;
  async function save(patch, message) {
    setError("");
    const before = options;
    const shown = Object.fromEntries(Object.entries(patch).filter(([name]) => !name.endsWith("_key")));
    read.set((prev) => (prev ? { ...prev, options: { ...prev.options, ...shown } } : prev));
    try {
      read.set(await apiJson(`${API}${SETTINGS_READ}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ options: patch }),
      }));
      if (message) setStatus?.(message);
      return true;
    } catch (err) {
      read.set((prev) => (prev ? { ...prev, options: before } : prev));
      setError(t("Could not save: {message}", { message: err.message }));
      return false;
    }
  }
  // A whole-number option's box committed (`raw`, what it holds): saved,
  // with `message(n)`, when it is in the option's range (`options.ranges`)
  // and not what is saved; else nothing, and the box shows the stored value
  // again.
  async function saveNumber(name, raw, message) {
    const n = Number.parseInt(String(raw).trim(), 10);
    const [lo, hi] = options?.ranges?.[name] || [];
    if (!Number.isFinite(n) || !(n >= lo && n <= hi) || n === options[name]) return false;
    return save({ [name]: n }, message(n));
  }
  return { options, error: error || (!read.data ? read.error || "" : ""), save, saveNumber };
}
