// A command (app/appCommands.js, editor/blockCommands.js) as a menu row:
// the catalog's label, its glyph (app/commandIcons.jsx) and the key this
// account gave it, greyed while the command does not apply, and run with
// the same ctx its key runs it with. A menu lists command ids, not handlers.
import { MenuItem } from "../shared/ui/Menus";
import { chordLabel } from "../shared/lib/hotkeys.js";
import { commandById, commandChord } from "./commands.js";
import { commandIcon } from "./commandIcons.jsx";

export function CommandMenuItem({ id, ctx, bindings }) {
  const cmd = commandById(id);
  const chord = commandChord(id, bindings);
  return (
    <MenuItem icon={commandIcon(cmd)} keys={chord ? chordLabel(chord) : null} data-command={id}
      disabled={!!cmd.when && !cmd.when(ctx)} onClick={() => cmd.run(ctx)}>
      {cmd.label}
    </MenuItem>
  );
}
