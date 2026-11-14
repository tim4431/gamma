// The strip of a conversation with a computer's agent (docs/dev/
// local_agents.md "The chat's computer side"), in the chat's health strip's
// slot under its header: the agent, where it works and what it is at
// (computerChat.js stripLine), a plain line and not a live region (the
// turn's pill in the transcript is the one read out); after it where the
// agent works outside a synced folder (workPlace: while the conversation
// has no turn a menu of the directories this computer offers and No
// folder, then the one it works in); and its ⋯ menu, the desktop shell's to
// do: Continue in a terminal and, on a page of a work folder kept here,
// Show in folder. The page only asks: the shell reads the session and the
// folder from its own server, never a command from the page.
import { ContextMenu, MenuItem, MenuSelect, useMenuTrigger } from "../../shared/ui/Menus";
import { FolderIcon, FolderOpenIcon, MoreIcon, TerminalIcon } from "../../shared/ui/Icons";
import { t } from "../../shared/i18n/i18n.js";
import { askShell } from "../../vault/useVault.js";
import { canContinue, liveTurn, offersMenu, revealsFile, stripLine } from "./computerChat.js";

function StripMenu({ bucket, label, session, reveal }) {
  const menus = useMenuTrigger();
  const ask = (type) => { menus.close(); askShell(type, { page: bucket }); };
  const title = t("More");
  if (!session && !reveal) return null;
  return (
    <>
      <button type="button" className="ctlBtn computerStripMenu" title={title} aria-label={title} {...menus.trigger("strip")}>
        <MoreIcon size={16} />
      </button>
      {menus.menu ? (
        <ContextMenu {...menus.menu}>
          {session ? (
            <MenuItem icon={TerminalIcon} onClick={() => ask("continue-in-terminal")}
              title={t("Opens a terminal in the directory it works in with this conversation's {agent} session, under your own {agent} settings", { agent: label })}>
              {t("Continue in a terminal")}
            </MenuItem>
          ) : null}
          {reveal ? <MenuItem icon={FolderOpenIcon} onClick={() => ask("reveal-on-disk")}>{t("Show in folder")}</MenuItem> : null}
        </ContextMenu>
      ) : null}
    </>
  );
}

function Place({ place, onPick }) {
  if (!place) return null;
  if (!place.pick) {
    return <span className="computerPlace" title={t("Where the agent works in this conversation")}><FolderIcon size={14} />{place.text}</span>;
  }
  if (place.options.length < 2) return <span className="computerPlace"><FolderIcon size={14} />{place.options[0][1]}</span>;
  return (
    <span className="computerPlace">
      <MenuSelect label={t("Where the agent works")} value={place.value} onChange={onPick} options={place.options}
        heading={t("Works in")} display={<><FolderIcon size={14} />{place.options.find(([v]) => v === place.value)?.[1]}</>} />
    </span>
  );
}

// `side` is useComputerSide's for the bucket `bucket`, `page` whether the
// bucket is a page.
export default function ComputerStrip({ side, bucket, page = false }) {
  const { info, label, place, pickPlace } = side;
  const conversation = side.chat.conversation;
  const strip = stripLine(info, liveTurn(conversation));
  if (!strip.text) return null;
  return (
    <div className={`chatHealthStrip computerStrip ${strip.kind}`}>
      {strip.kind === "working" || strip.kind === "waiting" ? <span className="transferSpin inline" aria-hidden="true" /> : null}
      <span className="chatHealthText">{strip.text}</span>
      <Place place={place} onPick={pickPlace} />
      {offersMenu(info) ? (
        <StripMenu bucket={bucket} label={label} session={canContinue(info, conversation)} reveal={revealsFile(info, page)} />
      ) : null}
    </div>
  );
}
