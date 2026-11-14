// The chat's History popover, on both sides of the chat: Gamma AI's
// conversations of the page or folder (ChatDock) and the page's
// conversations with the agent on this computer (chat/computer/
// ComputerChat.jsx). A search field, then the conversation shown now and
// the earlier ones, each `.chatHistRow` opening its conversation, with a
// row menu (⋯, a right-click or a held finger: menuPress) of the caller's
// items; the loading, empty and no-match hints; and under the list what
// the caller adds (ChatDock's bulk bar).
import { useState } from "react";
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

// The rows the search keeps: those whose title or preview holds the query.
export function matchRows(rows, query) {
  const q = String(query || "").trim().toLowerCase();
  return q ? rows.filter((s) => `${s.title || ""} ${s.preview || ""}`.toLowerCase().includes(q)) : rows;
}

// What Delete asks of a history row (askConfirm's options), `onConfirm`
// deleting it.
export const deleteQuestion = (row, onConfirm) => ({
  title: T("Delete conversation"),
  message: t("Delete “{title}” from this chat's history? This can't be undone.", { title: row.title || t("Untitled") }),
  confirmLabel: t("Delete"), danger: true, onConfirm,
});

// `rows` the listed ones ([{id, title, updated_at, active}], the search's
// matches: matchRows), the active one first; `loading` while the earlier
// ones are read. `onOpen(row)` opens an earlier one; `disabledReason`, set
// while none can be opened, greys them and is their title. `menuItems(row,
// close)` the row menu's MenuItems (none: the row has no menu); `tip(row)`
// an earlier row's title; `lead(row)` what comes before a row's title (a
// tick box), `editing(row)` what stands in for a row (a rename field), and
// `picking` while rows are ticked. Escape in the search closes (`onClose`).
export default function ChatHistoryPop({
  query, onQuery, onClose, rows, loading, onOpen, disabledReason = "", menuItems, tip = (row) => row.title,
  lead, editing, picking = false, children,
}) {
  const [menu, setMenu] = useState(null); // {x, y, row}: a row's menu
  const openMenu = (row) => (e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, row }); };
  const close = () => setMenu(null);
  const open = (row) => { if (!row.active && !disabledReason) onOpen(row); };
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
        {rows.map((s) => {
          const stand = editing?.(s);
          if (stand) return stand;
          const off = !s.active && !!disabledReason;
          const hasMenu = !!menuItems?.(s, close);
          return (
            <div key={s.id} className={`chatHistRow rowMenuHost${s.active ? " active" : ""}`}
              role="button" tabIndex={0} aria-disabled={off || undefined}
              title={off ? disabledReason : s.active ? t("The conversation shown now") : tip(s)}
              onClick={() => open(s)}
              onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) open(s); }}
              {...(hasMenu ? menuPress(openMenu(s)) : {})}>
              {lead?.(s)}
              <span className="chatHistTitle">{s.title || t("Untitled")}</span>
              <span className="chatHistAge">{s.active ? t("now") : relAge(s.updated_at)}</span>
              {hasMenu ? <MenuButton open={openMenu(s)} /> : null}
            </div>
          );
        })}
        {menu ? <ContextMenu x={menu.x} y={menu.y} onClose={close}>{menuItems(menu.row, close)}</ContextMenu> : null}
        {loading ? <div className="popoverHint loadingHint">{t("Loading…")}</div>
          : !rows.some((s) => !s.active) && !String(query || "").trim()
            ? <div className="popoverHint">{t("No earlier conversations — New chat keeps the current one here.")}</div>
          : !rows.length ? <div className="popoverHint">{t("No conversation matches.")}</div>
          : null}
      </div>
      {children}
    </Popover>
  );
}
