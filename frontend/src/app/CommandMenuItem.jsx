// A command (app/appCommands.js, editor/blockCommands.js) as a menu row:
// the catalog's label, its glyph (app/commandIcons.jsx) and the key this
// account gave it, greyed while the command does not apply, and run with
// the same ctx its key runs it with. A menu lists command ids, not handlers.
// `onRun` is told after the command ran (a menu that closes itself);
// `disabledTitle` is the row's title while it is greyed (why it does not
// apply); anything else is the row's (a title, onMouseDown). A command id
// the catalog does not know renders nothing.
import { MenuItem } from "../shared/ui/Menus";
import { chordLabel } from "../shared/lib/hotkeys.js";
import { commandById, commandChord } from "./commands.js";
import { commandIcon } from "./commandIcons.jsx";

export function CommandMenuItem({ id, ctx, bindings, onRun, disabledTitle, title, ...rest }) {
  const cmd = commandById(id);
  if (!cmd) return null;
  const chord = commandChord(id, bindings);
  const disabled = !!cmd.when && !cmd.when(ctx);
  return (
    <MenuItem icon={commandIcon(cmd)} keys={chord ? chordLabel(chord) : null} data-command={id}
      disabled={disabled} title={disabled && disabledTitle ? disabledTitle : title}
      onClick={() => { cmd.run(ctx); onRun?.(); }} {...rest}>
      {cmd.label}
    </MenuItem>
  );
}
