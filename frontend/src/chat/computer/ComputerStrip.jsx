// The strip of a conversation with a computer's agent (docs/dev/
// local_agents.md "The chat's computer side"), in the chat's health strip's
// slot under its header: the agent and where it works, or what stops it
// or waits for the person (computerChat.js stripLine), a plain line and not
// a live region (the turn's pill in the transcript is the one read out,
// and says how a turn goes; where it works outside a synced folder is the
// composer's folder chip, PlaceChip.jsx); and its ⋯ menu, the desktop
// shell's to do: Continue in a terminal and, on a page of a work folder
// kept here, Show in Explorer (vaultState revealLabel). The page only asks: the shell reads the
// session and the folder from its own server, never a command from the
// page.
import { ContextMenu, MenuItem, useMenuTrigger } from "../../shared/ui/Menus";
import { HardDriveIcon, MoreIcon, ShieldIcon, TerminalIcon } from "../../shared/ui/Icons";
import { t } from "../../shared/i18n/i18n.js";
import { askShell } from "../../vault/useVault.js";
import { revealLabel } from "../../vault/vaultState.js";
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
          {reveal ? <MenuItem icon={HardDriveIcon} onClick={() => ask("reveal-on-disk")}>{revealLabel()}</MenuItem> : null}
        </ContextMenu>
      ) : null}
    </>
  );
}

// `side` is useComputerSide's for the bucket `bucket`, `page` whether the
// bucket is a page.
export default function ComputerStrip({ side, bucket, page = false }) {
  const { info, label } = side;
  const conversation = side.chat.conversation;
  const strip = stripLine(info, liveTurn(conversation));
  if (!strip.text) return null;
  return (
    <div className={`chatHealthStrip computerStrip ${strip.kind}`}>
      {strip.kind === "checking" ? <span className="transferSpin inline" aria-hidden="true" />
        : strip.kind === "asking" ? <ShieldIcon size={14} aria-hidden="true" /> : null}
      <span className="chatHealthText">{strip.text}</span>
      {offersMenu(info) ? (
        <StripMenu bucket={bucket} label={label} session={canContinue(info, conversation)} reveal={revealsFile(info, page)} />
      ) : null}
    </div>
  );
}
