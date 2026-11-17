// Where a computer's agent works (computerChat.workPlace), at the start of
// the composer's bar, as the folder a Codex thread works in: a folder chip
// whose menu lists the directories offered, the ones used last first, and
// Its own directory, the new conversation's own until its first turn; then
// Add folder… where the desktop app can offer one (`onAdd`: the shell's
// folder dialog). Once the conversation has a turn it is the conversation's,
// shown and not to change.
import { MenuSelect } from "../../shared/ui/Menus";
import { FolderIcon, FolderPlusIcon } from "../../shared/ui/Icons";
import { t } from "../../shared/i18n/i18n.js";

// `place` workPlace's, `agent` the agent's name, `onPick(id)` the choice of
// a conversation that begins, `onAdd` null where no folder can be added here.
export default function PlaceChip({ place, agent, onPick, onAdd = null }) {
  const words = { agent, folder: place.text };
  const label = place.pick ? t("{agent} works in {folder}", words)
    : t("{agent} works in {folder} in this conversation: start a new chat to work elsewhere", words);
  const add = onAdd && place.pick ? [{
    icon: FolderPlusIcon, label: t("Add folder…"), onClick: onAdd,
    title: t("Choose a folder on this computer for {agent} to work in", words),
  }] : [];
  return (
    <span className="chatPlaceChip">
      <MenuSelect up start label={label} heading={t("Works in")} value={place.value} onChange={onPick}
        options={place.options.map(([value, name]) => [value, name, FolderIcon])}
        locked={place.pick ? "" : t("Start a new chat to work elsewhere")} actions={add} />
    </span>
  );
}
