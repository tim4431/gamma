// The one model menu (docs/dev/ai.md "The model menu"): a search box over
// every connection's models, the chat models first (the account's
// favorites: the list the chat's model chip offers), then a group per
// connection under its logo, named with how it is paid for. A row shows the model, what it
// can't do ("no tools", for the handwriting task "can't convert to text"),
// whether it sees pictures, its context window and, on a connection paid
// per token, its price; its star adds it to the chat models. A task's menu starts
// with "Use chat model", the translation task's ends with the translation
// services, and a pick that is no longer offered stands at the top, marked
// unavailable. What it lists is modelMenu.js menuGroups.
//
// The focus stays in the search box: the arrow keys move through the
// rows, Enter picks, Escape closes (ContextMenu), and an input method's
// Enter is its own. The list is a listbox inside a ContextMenu, its options
// drawn as menu rows. ModelMenuList is the open menu, for a caller that
// opens it from a control of its own (the chat's model chip);
// TaskModelMenu is a task's, a MenuSelect that opens it (Settings → AI →
// Models, Language & translation's Translate with). ChatModels is Settings
// → AI → Models' list of the chat models. ModelFacts is a row's facts, the
// connect dialog's model checklist's too.
import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ContextMenu, MenuItem, MenuLabel, MenuSelect } from "../shared/ui/Menus";
import { CheckIcon, EyeIcon, SearchIcon, StarIcon, XIcon } from "../shared/ui/Icons";
import { composing } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { billingLabel, followed, modelName } from "./aiModels.js";
import { menuGroups, modelFacts, pickableRows } from "./modelMenu.js";
import { ProviderLogo } from "./ProviderLogo.jsx";
import "./modelMenu.css";

// A model's facts (modelMenu.js modelFacts): tags for what it can't do, an
// eye for pictures, then its context window and price, and after them
// `children` (a row's own marks).
export function ModelFacts({ model, task = "", children }) {
  return (
    <span className="modelMenuFacts">
      {modelFacts(model, { task }).map((fact) => {
        if (fact.kind === "vision") {
          return <span key="vision" className="modelMenuFact" data-fact="vision" title={fact.title} role="img" aria-label={fact.title}><EyeIcon size={14} /></span>;
        }
        const tag = fact.kind === "tools" || fact.kind === "ink";
        return <span key={fact.kind} className={tag ? "uiTag warn modelMenuTag" : "modelMenuFact"} data-fact={fact.kind} title={fact.title}>{fact.text}</span>;
      })}
      {children}
    </span>
  );
}

const NONE_MISSING = {};

// `models` the registry's, `favorites` the starred ids, `missing` the
// names of the ids no longer offered (/api/ai/models'), `value` the pick
// shown checked, `onPick(id)`, `onToggleFavorite(id)` (none: no stars),
// `follow` the "Use chat model (X)" row's words (a task's menu), `engines`
// the translation services, `task` the task ("ink" marks the models that
// see no pictures), `label` the list's name; the rest is the ContextMenu's
// placement and dismissal.
export function ModelMenuList({ x, y, anchorRight = false, anchorBottom = false, flipY, onClose, ignoreRef,
  models, favorites = [], missing = NONE_MISSING, value = "", onPick, onToggleFavorite = null, follow = "", engines = [], task = "", label }) {
  const id = useId();
  const listRef = useRef(null);
  const [query, setQuery] = useState("");
  const groups = useMemo(() => menuGroups(models, { favorites, query, value, follow: !!follow, engines, missing }),
    [models, favorites, query, value, follow, engines, missing]);
  const rows = useMemo(() => pickableRows(groups), [groups]);
  const [active, setActive] = useState(() => Math.max(0, rows.findIndex((r) => r.id === value)));
  useEffect(() => { if (query) setActive(0); }, [query]);
  const activeKey = rows[active]?.key || "";
  // An option's element id, by its place among the groups' rows.
  const places = useMemo(() => new Map(groups.flatMap((g) => g.rows.map((r) => `${g.key}|${r.id}`)).map((key, i) => [key, i])), [groups]);
  const optionId = (key) => `${id}-o${places.get(key)}`;
  useEffect(() => {
    if (activeKey) document.getElementById(optionId(activeKey))?.scrollIntoView({ block: "nearest" });
  }, [activeKey]);
  // The list keeps the height it opened with while the search narrows it,
  // so a menu that opens upward does not move under the pointer.
  const [minHeight, setMinHeight] = useState(0);
  useLayoutEffect(() => { if (listRef.current) setMinHeight(listRef.current.offsetHeight); }, []);
  const pick = (row) => {
    if (!row || row.unavailable) return;
    onClose();
    if (row.id !== value) onPick(row.id);
  };
  const onKeyDown = (e) => {
    if (composing(e)) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (rows.length) setActive((i) => (i + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(rows[active]);
    }
  };
  const heading = (group) => {
    if (group.key === "favorites") return t("Chat models");
    if (group.key === "engines") return t("Translation services");
    if (!group.key.startsWith("connection:")) return null;
    const billing = billingLabel(group.billing);
    return <>
      <ProviderLogo service={group.service} size={14} />
      <span className="modelMenuGroupName">{group.label}</span>
      {billing ? <span className="uiTag modelMenuBilling">{billing}</span> : null}
    </>;
  };
  const row = (group, r) => {
    const key = `${group.key}|${r.id}`;
    const selected = r.id === value && !r.unavailable;
    const name = r.id === "" ? follow : r.model ? r.model.model : r.label;
    // Where a row stands outside its connection's group, it names the connection.
    const where = r.model && group.key === "favorites" ? r.model.provider_name
      : r.unavailable && !r.engine ? missing?.[r.id]?.connection || t("a removed connection") : "";
    const starrable = onToggleFavorite && !r.engine && r.id !== "" && (r.model || r.favorite);
    return (
      <div key={key} className="modelMenuRow">
        <MenuItem id={optionId(key)} role="option" aria-selected={key === activeKey} aria-disabled={r.unavailable || undefined}
          tabIndex={-1} disabled={r.unavailable} className={`modelMenuOption${key === activeKey ? " active" : ""}`}
          title={r.unavailable ? t("{model} is no longer offered", { model: name }) : undefined}
          onMouseDown={(e) => e.preventDefault()} onClick={() => pick(r)}
          trailing={r.unavailable ? <span className="modelMenuFacts"><span className="uiTag warn modelMenuTag">{t("unavailable")}</span></span>
            : <ModelFacts model={r.model} task={task}>{selected ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}</ModelFacts>}>
          <span className="modelMenuName">{name}</span>
          {where ? <span className="modelMenuWhere">{where}</span> : null}
        </MenuItem>
        {starrable ? (
          <button type="button" className={`ctlBtn modelMenuStar${r.favorite ? " on" : ""}`} tabIndex={-1} aria-pressed={r.favorite}
            aria-label={r.favorite ? t("Remove {model} from the chat models", { model: name }) : t("Add {model} to the chat models", { model: name })}
            title={r.favorite ? t("Remove from the chat models") : t("Add to the chat models")}
            onMouseDown={(e) => e.preventDefault()} onClick={() => onToggleFavorite(r.id)}>
            <StarIcon size={14} filled={r.favorite} />
          </button>
        ) : null}
      </div>
    );
  };
  return (
    <ContextMenu x={x} y={y} anchorRight={anchorRight} anchorBottom={anchorBottom} flipY={flipY} onClose={onClose}
      ignoreRef={ignoreRef} className="modelMenu" role="none">
      <label className="modelMenuSearch">
        <SearchIcon size={14} />
        <input className="aiKeyInput" type="text" role="combobox" aria-expanded="true" aria-controls={id}
          aria-autocomplete="list" aria-activedescendant={activeKey ? optionId(activeKey) : undefined}
          aria-label={t("Search models")} placeholder={t("Search models…")} spellCheck={false} autoComplete="off" autoFocus
          value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown} />
      </label>
      <div ref={listRef} id={id} role="listbox" aria-label={label || t("Models")} className="modelMenuList"
        style={minHeight ? { minHeight } : undefined}>
        {groups.map((group) => {
          const head = heading(group);
          return (
            <div key={group.key} role="group" aria-labelledby={head ? `${id}-h-${group.key}` : undefined}
              aria-label={head ? undefined : group.key === "missing" ? t("Picked") : undefined}>
              {head ? <MenuLabel id={`${id}-h-${group.key}`}>{head}</MenuLabel> : null}
              {group.rows.map((r) => row(group, r))}
            </div>
          );
        })}
        {!groups.length ? <div className="modelMenuEmpty">{query ? t("No model matches “{query}”.", { query }) : t("No models yet")}</div> : null}
      </div>
    </ContextMenu>
  );
}

// A task's model menu: `role` of the account's models (`ai`, useAiModels),
// a MenuSelect opening ModelMenuList under it. The trigger names the model
// the task resolves to, or says Choose… while its pick is no longer
// offered (its Settings row is then red: aiModels.js taskRow); the menu
// checks the pick, with the row for no pick first, "Use chat model (X)"
// (translation's names the free service, "Default: X", when that is what
// it falls to) and, for translation, the services last.
export function TaskModelMenu({ role, ai, label }) {
  const info = ai.aiInfo;
  const r = ai.roles[role] || { pick: "", model: "", state: "follows" };
  const engines = role === "translate" ? info?.translate_engines || [] : [];
  const target = role === "chat" ? "" : followed(info, role);
  const follow = role === "chat" ? ""
    : engines.some((e) => e.id === target) ? t("Default: {name}", { name: modelName(info, target) })
      : t("Use chat model ({model})", { model: target ? modelName(info, target) : t("none") });
  return (
    <MenuSelect label={label} options={[]} disabled={!ai.canWrite}
      display={r.state === "missing" ? t("Choose…") : modelName(info, r.model) || t("None")}
      listbox={(at) => (
        <ModelMenuList {...at} label={label} models={ai.models} favorites={ai.favorites} missing={info?.missing}
          value={role === "chat" && !r.pick ? r.model : r.pick} onPick={(id) => ai.setRole(role, id)}
          onToggleFavorite={ai.canWrite ? ai.toggleFavorite : null} follow={follow} engines={engines} task={role} />
      )} />
  );
}

// The chat models (Settings → AI → Models): the models the chat's model
// chip offers to switch to, besides the one in use — the account's
// favorites, in the order they were added. Each is a chip with its
// connection's logo and a remove button; one no longer offered stays,
// marked unavailable, so it can be removed. "Add model…" opens the model
// menu: a pick adds that model and closes it, a star adds or removes one
// and leaves it open for the next. `ai` is useAiModels'.
export function ChatModels({ ai }) {
  const info = ai.aiInfo;
  const favorites = ai.favorites;
  const add = (id) => { if (!favorites.includes(id)) ai.toggleFavorite(id); };
  return (
    <div className="aiModelChips" role="list" aria-label={t("Chat models")}>
      {favorites.map((id) => {
        const model = ai.models.find((m) => m.id === id);
        const name = modelName(info, id);
        const where = model ? model.provider_name : info?.missing?.[id]?.connection || t("a removed connection");
        return (
          <span key={id} role="listitem" className="categoryTag chatModelTag"
            title={model ? where : t("{model} is no longer offered", { model: name })}>
            {model ? <ProviderLogo service={model.service} size={14} fallback={false} /> : null}
            {name}
            {model ? null : <span className="uiTag warn modelMenuTag">{t("unavailable")}</span>}
            {ai.canWrite ? (
              <button type="button" className="uiClose uiCloseSm" title={t("Remove from the chat models")}
                aria-label={t("Remove {model} from the chat models", { model: name })} onClick={() => ai.toggleFavorite(id)}>
                <XIcon size={14} />
              </button>
            ) : null}
          </span>
        );
      })}
      {ai.canWrite ? (
        <MenuSelect label={t("Add a chat model")} options={[]} display={t("Add model…")} disabled={!ai.models.length}
          listbox={(at) => (
            <ModelMenuList {...at} label={t("Add a chat model")} models={ai.models} favorites={favorites}
              missing={info?.missing} onPick={add} onToggleFavorite={ai.toggleFavorite} />
          )} />
      ) : null}
    </div>
  );
}
