// Settings → Keyboard: every command with its shortcut, VSCode-style —
// click a chord and press the new one, Backspace unbinds, a reset button
// on each row that differs from its default, "Reset all" on the section.
// The rows come from the one command catalog (app/commands.js); the
// bindings are the account's `keybindings` preference (docs/dev/hotkeys.md).
// A chord two commands answer to is flagged on both rows. Under the
// commands, the keys the outliner owns outright (Enter, Tab, …), read-only.
import React from "react";
import { t } from "../shared/i18n/i18n.js";
import { KeyboardIcon } from "../shared/ui/Icons";
import { KeyBinding, KeyCaps, PaneHead, Row, Section } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { ALL_COMMANDS, GROUPS, fixedKeys } from "../app/commands.js";
import { FIXED_KEY_ICONS, commandIcon } from "../app/commandIcons.jsx";
import { chordLabel, conflicts, effectiveKeys } from "../shared/lib/hotkeys.js";

export function KeyboardSettings({ value }) {
  const { keybindings, setKeybindings, enterNewNote } = value;
  const [filter, setFilter] = React.useState("");
  const bindings = keybindings || {};
  const clashes = conflicts(ALL_COMMANDS, bindings);
  const modifiedIds = Object.keys(bindings).filter((id) => ALL_COMMANDS.some((c) => c.id === id));
  const bind = (id, chord) => setKeybindings({ ...bindings, [id]: chord });
  const reset = (id) => { const next = { ...bindings }; delete next[id]; setKeybindings(next); };

  const q = filter.trim().toLowerCase();
  const matches = (cmd) => !q
    || cmd.label.toLowerCase().includes(q)
    || cmd.group.toLowerCase().includes(q)
    || effectiveKeys(cmd, bindings).some((k) => chordLabel(k).toLowerCase().includes(q));
  const fixed = fixedKeys(enterNewNote).filter(([chords, what]) => !q
    || what.toLowerCase().includes(q) || chords.some((c) => chordLabel(c).toLowerCase().includes(q)));

  return (
    <div className="keyboardPane">
      <PaneHead icon={KeyboardIcon} title={t("Keyboard shortcuts")}>
        <input
          className="aiKeyInput keyFilter" type="search"
          placeholder={t("Filter shortcuts")} aria-label={t("Filter shortcuts")}
          value={filter} onChange={(e) => setFilter(e.target.value)}
        />
      </PaneHead>
      <Section title={t("Shortcuts")} scope="account" prefs={SECTION_PREFS.keyboard["Shortcuts"]}
        action={(
          <button type="button" className="uiBtn sm" disabled={!modifiedIds.length}
            title={t("Put every shortcut back to its default")} onClick={() => setKeybindings({})}>
            {t("Reset all")}
          </button>
        )}>
        {GROUPS.map((group) => {
          const cmds = ALL_COMMANDS.filter((c) => c.group === group && matches(c));
          if (!cmds.length) return null;
          return (
            <React.Fragment key={group}>
              <div className="keyGroupLabel">{group}</div>
              {cmds.map((cmd) => {
                const keys = effectiveKeys(cmd, bindings);
                const defaults = effectiveKeys(cmd, {});
                const modified = Object.prototype.hasOwnProperty.call(bindings, cmd.id);
                const others = keys.flatMap((k) => (clashes.get(k) || []).filter((c) => c.id !== cmd.id));
                let hint = "";
                if (others.length) hint = t("Also used by {names}", { names: others.map((c) => c.label).join(", ") });
                else if (modified) hint = defaults.length ? t("Default: {keys}", { keys: defaults.map((k) => chordLabel(k)).join(" · ") }) : t("No default");
                else if (keys.length > 1) hint = t("Also {keys}", { keys: keys.slice(1).map((k) => chordLabel(k)).join(" · ") });
                return (
                  <div key={cmd.id} data-conflict={others.length ? "true" : undefined}>
                    <Row icon={commandIcon(cmd)} label={cmd.label} hint={hint}>
                      <KeyBinding
                        chord={keys[0] || null} label={cmd.label} fixed={!!cmd.fixed} modified={modified}
                        conflict={others.length > 0}
                        onChange={(chord) => bind(cmd.id, chord)} onReset={() => reset(cmd.id)}
                      />
                    </Row>
                  </div>
                );
              })}
            </React.Fragment>
          );
        })}
        {!ALL_COMMANDS.some(matches) && !fixed.length ? <div className="popoverHint">{t("No matching shortcuts.")}</div> : null}
      </Section>
      {fixed.length ? (
        <Section title={t("Built in")}>
          {fixed.map(([chords, what, id]) => (
            <Row key={what} icon={FIXED_KEY_ICONS[id]} label={what}>
              <span className="keyBinding">
                {chords.map((chord) => <KeyCaps key={chord} chord={chord} />)}
              </span>
            </Row>
          ))}
        </Section>
      ) : null}
    </div>
  );
}
