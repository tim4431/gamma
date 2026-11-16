// The chat's History popover (ChatDock): the bucket's conversations with
// every agent, Gamma AI's and a computer agent's together
// (chat/computer/computerChat.js historyRows). A search field, then the
// conversation shown now and the others, each `.chatHistRow` opening its
// conversation, led by its agent's icon and named by it while the chat has
// several, with a row menu (⋯, a right-click or a held finger: menuPress)
// of the caller's items; the loading, empty and no-match hints; a section
// of the caller's under its caption, drawn in the same rows (the sessions
// the picked agent's CLI keeps of its directory, "Codex sessions in
// paper": computerChat.js sessionRows); and under the list what the caller
// adds (the bulk bar). A row that cannot open says why when pressed.
import { useState } from "react";
import { RefreshIcon } from "../shared/ui/Icons";
import { ContextMenu, MenuButton } from "../shared/ui/Menus";
import { Popover } from "../shared/ui/Surfaces";
import { menuPress } from "../shared/ui/press.js";
import { touchTyping } from "../shared/lib/pointer.js";
import { T, t } from "../shared/i18n/i18n.js";

// Compact age for a history row: "now", "5m", "3h", "2d", "4mo", "1y".
function relAge(iso) {
  const ms = Date.now() - Date.parse(iso || "");
  if (!Number.isFinite(ms) || ms < 60000) return t("now");
  const m = Math.floor(ms / 60000);
  if (m < 60) return t("{n}m", { n: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t("{n}h", { n: h });
  const d = Math.floor(h / 24);
  if (d < 30) return t("{n}d", { n: d });
  return d < 365 ? t("{n}mo", { n: Math.floor(d / 30) }) : t("{n}y", { n: Math.floor(d / 365) });
}

// The rows the search keeps: those whose title, preview or `by` holds the
// query.
export function matchRows(rows, query) {
  const q = String(query || "").trim().toLowerCase();
  return q ? rows.filter((s) => `${s.title || ""} ${s.preview || ""} ${s.by || ""}`.toLowerCase().includes(q)) : rows;
}

// What Delete asks of a history row (askConfirm's options), `onConfirm`
// deleting it.
export const deleteQuestion = (row, onConfirm) => ({
  title: T("Delete conversation"),
  message: t("Delete “{title}” from this chat's history? This can't be undone.", { title: row.title || t("Untitled") }),
  confirmLabel: t("Delete"), danger: true, onConfirm,
});

// One row: `off` why it cannot be opened now ("" while it can), `onMenu`
// its menu's opener (none: no menu), `detail` a muted line under its title
// (why a session is busy), `opening` while its open is on its way (a
// spinner in its age's place). Its agent (`row.agentName`, while the chat
// has several) leads its title, and names its icon where nothing after the
// title (`by`) does. Enter or Space opens it, as a press does.
function HistoryRow({ row, off, title, onOpen, onMenu, lead, Icon, detail = "", opening = false }) {
  const name = <span className="chatHistTitle">{row.title || t("Untitled")}</span>;
  const iconNames = row.agentName && !row.by ? { role: "img", "aria-label": row.agentName } : { "aria-hidden": "true" };
  return (
    <div className={`chatHistRow rowMenuHost${row.active ? " active" : ""}`}
      role="button" tabIndex={0} aria-disabled={off ? true : undefined} aria-busy={opening || undefined}
      title={off || [row.agentName, title].filter(Boolean).join(" · ")}
      onClick={() => onOpen(row)}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); onOpen(row); }
      }}
      {...(onMenu ? menuPress(onMenu) : {})}>
      {lead}
      {Icon ? <Icon size={14} className="chatHistIcon" {...iconNames} /> : null}
      {detail ? <span className="chatHistMain">{name}<span className="chatHistDetail">{detail}</span></span> : name}
      {row.by ? <span className="chatHistBy">{row.by}</span> : null}
      <span className="chatHistAge">
        {opening ? <span className="transferSpin inline" aria-hidden="true" /> : row.active ? t("now") : relAge(row.updated_at)}
      </span>
      {onMenu ? <MenuButton open={onMenu} /> : <span className="chatHistMenuSlot" aria-hidden="true" />}
    </div>
  );
}

// `rows` the listed ones ([{id, title, updated_at, active, by?,
// agentName?}], the search's matches: matchRows; `by` what is said after
// its title: whose conversation it is, "bob's chat with Codex", or its
// agent, "Codex on TIM-PC"), the active one first; `loading` while the
// earlier ones are read. `onOpen(row)` opens another one;
// `disabledReason(row)`, set while the row cannot be opened, greys it and
// is its title, and a press on it hands it to `onRefused(reason)` (the
// status pill). `menuItems(row, close)` the row menu's MenuItems (none:
// the row has no menu); `tip(row)` another row's title; `lead(row)` what
// comes before a row's title (a tick box), `icon(row)` its agent's icon
// (none: no icon), `editing(row)` what stands in for a row (a rename
// field), and `picking` while rows are ticked. `section` ({label, empty,
// rows, loading, reading, error, onRetry, onRefresh, opening, onOpen,
// disabledReason, icon}, null for none) is drawn after them under its
// caption, which ends in a button that reads its rows again (`onRefresh`;
// a spinner while `reading` them again in place), rows without menus (a
// menu-less row keeps the ⋯'s room, so every row's age stands in one
// column), each led by `icon` as the conversations are, a busy one
// (`row.busy`) saying why under its title, the one whose open is on its
// way (`opening`, its id) a spinner in its age's place; `empty` the line
// while it has no row, `error` why its rows could not be read, with Try
// again (`onRetry`). Escape in the search closes (`onClose`).
export default function ChatHistoryPop({
  query, onQuery, onClose, rows, loading, onOpen, disabledReason = () => "", onRefused, menuItems,
  tip = (row) => row.title, lead, icon, editing, picking = false, section = null, children,
}) {
  const [menu, setMenu] = useState(null); // {x, y, row}: a row's menu
  const openMenu = (row) => (e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, row }); };
  const close = () => setMenu(null);
  const tryOpen = (reason, row, go) => { if (reason) onRefused?.(reason); else go(row); };
  const open = (row) => { if (!row.active) tryOpen(disabledReason(row), row, onOpen); };
  const openListed = (row) => tryOpen(section.disabledReason(row), row, section.onOpen);
  const refresh = t("Read the sessions again");
  return (
    <Popover className="chatHistoryPop">
      <input
        autoFocus={!touchTyping()}
        className="searchInput"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder={t("Search conversations…")}
        onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } }}
      />
      <div className={`chatHistList${picking ? " picking" : ""}`}>
        {rows.map((s) => editing?.(s) || (
          <HistoryRow key={s.id} row={s} off={!s.active && disabledReason(s)} onOpen={open}
            title={s.active ? t("The conversation shown now") : tip(s)}
            onMenu={menuItems?.(s, close) ? openMenu(s) : null} lead={lead?.(s)} Icon={icon?.(s)} />
        ))}
        {menu ? <ContextMenu x={menu.x} y={menu.y} onClose={close}>{menuItems(menu.row, close)}</ContextMenu> : null}
        {loading ? <div className="popoverHint loadingHint">{t("Loading…")}</div>
          : !rows.some((s) => !s.active) && !String(query || "").trim()
            ? <div className="popoverHint">{t("No earlier conversations — New chat keeps the current one here.")}</div>
          : !rows.length ? <div className="popoverHint">{t("No conversation matches.")}</div>
          : null}
        {section ? <>
          <div className="popoverSection chatHistSection">
            <span>{section.label}</span>
            {section.onRefresh ? (
              <button type="button" className="ctlBtn chatHistRefresh" title={refresh} aria-label={refresh}
                disabled={section.loading} aria-busy={section.reading || undefined}
                onClick={section.reading ? undefined : section.onRefresh}>
                {section.reading ? <span className="transferSpin inline" aria-hidden="true" /> : <RefreshIcon size={14} />}
              </button>
            ) : null}
          </div>
          {section.rows.map((s) => (
            <HistoryRow key={`session:${s.id}`} row={s} off={section.disabledReason(s)} title={s.title}
              onOpen={openListed} Icon={section.icon} detail={s.busy} opening={section.opening === s.id} />
          ))}
          {section.loading ? <div className="popoverHint loadingHint">{t("Loading…")}</div>
            : section.error ? (
              <div className="popoverHint">
                {section.error}{" "}
                <button type="button" className="uiBtn sm ghost" onClick={section.onRetry}>{t("Try again")}</button>
              </div>
            )
            : !section.rows.length ? <div className="popoverHint">{String(query || "").trim()
              ? t("No session matches.") : section.empty}</div>
            : null}
        </> : null}
      </div>
      {children}
    </Popover>
  );
}
