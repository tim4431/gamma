// Controls shared by the popup and the options page, on popup.css's recipes:
// the app's icons, ctxMenu rows and their placement, the workspace picker (a
// MenuSelect), and the chip fields: the labels, and the folder with its tree.

import {
  chooseWorkspace, currentWorkspace, folderByPath, folderTree, getSettings, makeFolder, namedKid, pathNames,
} from "./api.js";

// ---------- icons (mirrors frontend/src/shared/ui/Icons.jsx — 24×24 stroke glyphs) ----------

const ICON_PATHS = {
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  folderPlus: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M12 10v6"/><path d="M9 13h6"/>',
  folders: '<path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/>',
  tag: '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18z"/>',
  undo: '<path d="m9 4-5 5 5 5"/><path d="M4 9h10a6 6 0 0 1 0 12"/>',

  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  cookie: '<path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5"/><path d="M8.5 8.5v.01"/><path d="M16 15.5v.01"/><path d="M12 12v.01"/><path d="M11 17v.01"/><path d="M7 14v.01"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
};

// Gamma's icon sizes (14 inline with text, 16 in buttons) and its stroke:
// 1.6 px at any size (a check mark 2 px), like Icons.jsx's iconStroke.
export function icon(name, cls = "", size = 14, weight = 1.6) {
  const span = document.createElement("span");
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${(weight * 24) / size}" stroke-linecap="round" stroke-linejoin="round"${cls ? ` class="${cls}"` : ""}>${ICON_PATHS[name]}</svg>`;
  return span.firstChild;
}

// A ctxMenu row: icon, text, a check when selected. `tree` makes it a folder
// tree's row, `{depth, open, title, toggle, add}`: indented, led by a chevron
// (`open` true or false, null with nothing inside to show) and ending in the
// "+" that names a new subfolder. Those two are spans, as a button holds no
// button, so the row stays one element of its menu.
function menuRow(text, iconName, selected, onPick, tree = null) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "ctxMenuItem ctxMenuItemIconed";
  const ic = document.createElement("span"); ic.className = "ctxMenuIcon"; ic.appendChild(icon(iconName));
  const t = document.createElement("span"); t.className = "ctxMenuText"; t.textContent = text; t.title = tree ? tree.title : text;
  b.append(ic, t);
  if (selected) b.appendChild(icon("check", "ctxMenuCheck", 14, 2));
  if (tree) {
    b.classList.add("treeRow");
    b.style.setProperty("--depth", Math.min(tree.depth, 6));
    const twist = document.createElement("span"); twist.className = "treeTwist";
    if (tree.open !== null) {
      b.setAttribute("aria-expanded", tree.open);
      twist.title = tree.open ? "Collapse" : "Expand";
      twist.append(icon("chevronDown"));
      twist.addEventListener("click", (e) => { e.stopPropagation(); tree.toggle(); });
    }
    const add = document.createElement("span"); add.className = "treeAdd"; add.title = "New subfolder (+)";
    add.append(icon("plus"));
    add.addEventListener("click", (e) => { e.stopPropagation(); tree.add(); });
    b.prepend(twist);
    b.append(add);
  }
  // mousedown, not click: keeps focus where it is (the popup's folder and labels inputs rely on this).
  b.addEventListener("mousedown", (e) => e.preventDefault());
  b.addEventListener("click", onPick);
  return b;
}

// Shows a ctxMenu under its field, or over it when it would not fit below
// and there is more room above. The popup is as tall as its content, so a
// menu hanging past the footer would stretch the window instead of staying
// inside it. A menu already open keeps its side while its rows change.
function placeMenu(menu) {
  if (!menu.classList.contains("hidden")) return;
  menu.classList.remove("hidden", "up");
  menu.style.maxHeight = "";
  const most = parseFloat(getComputedStyle(menu).maxHeight);
  const field = menu.parentElement.getBoundingClientRect();
  const below = document.documentElement.clientHeight - field.bottom - 8;
  const above = field.top - 8;
  const up = menu.offsetHeight > below && above > below;
  menu.classList.toggle("up", up);
  menu.style.maxHeight = `${Math.max(0, Math.min(most, up ? above : below))}px`;
}

// ---------- the workspace picker ----------

// The app's MenuSelect over `button` and its `menu` (siblings in a
// .selectWrap). `set(items, value)` takes `[{value, label, icon}]` and the
// one to show; a value no item carries falls back to the first, which is
// the list's "everything" row (the default workspace).
// A pick calls onPick(value). `toItems` maps what `set` is given to items.
function menuSelect(button, menu, onPick, toItems) {
  let items = [];
  const picker = {
    value: "",
    set(list, value) {
      items = toItems(list);
      picker.value = items.some((i) => i.value === value) ? value : items.length ? items[0].value : "";
      render();
    },
  };
  const close = () => menu.classList.add("hidden");
  function render() {
    const item = items.find((i) => i.value === picker.value);
    const ic = document.createElement("span"); ic.className = "ctxMenuIcon";
    ic.appendChild(icon(item ? item.icon : "folder"));
    const label = document.createElement("span"); label.className = "uiSelectLabel";
    label.textContent = item ? item.label : "";
    button.title = label.textContent;
    button.replaceChildren(ic, label, icon("chevronDown", "uiSelectChev"));
  }
  const pick = (value) => () => {
    picker.value = value;
    render();
    close();
    onPick(value);
  };
  button.addEventListener("click", () => {
    if (!menu.classList.contains("hidden")) { close(); return; }
    menu.replaceChildren(...items.map((i) => menuRow(i.label, i.icon, i.value === picker.value, pick(i.value))));
    placeMenu(menu);
  });
  document.addEventListener("pointerdown", (e) => { if (!button.parentElement.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  render();
  return picker;
}

// The libraries GET /api/session lists, by name. `value` is a workspace id,
// "" for the account's default one — which the server lists first, and
// which saves name no workspace at all, as they always have. Picking another
// stores it and calls `onChosen`, to read that library. `field`, the folder
// field, empties first: what it holds belongs to the library being left.
export function workspacePicker(button, menu, field, onChosen) {
  const pick = async (value) => {
    const settings = await getSettings();
    if (value === currentWorkspace(settings)) return;
    field.clear();
    await chooseWorkspace(settings, value);
    await onChosen();
  };
  return menuSelect(button, menu, pick,
    (workspaces) => workspaces.map((w) => ({ value: w.default ? "" : w.id, label: w.name, icon: "folders" })));
}

// ---------- the chip fields ----------

// The name typed for something new, inside a menu (the app's .ctxMenuInput).
// Enter or leaving the input commits it, Escape or an empty input cancels,
// once. A blur with the input still the active element is the window losing
// the focus, which ends nothing.
function inlineName(placeholder, { commit, cancel }) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "field ctxMenuInput";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.setAttribute("aria-label", placeholder);
  let done = false;
  const end = (finish) => { if (!done) { done = true; finish(); } };
  const submit = () => {
    const name = input.value.trim();
    end(name ? () => commit(name) : cancel);
  };
  input.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); submit(); }
    else if (e.key === "Escape") { e.preventDefault(); end(cancel); }
  });
  input.addEventListener("blur", () => { if (document.activeElement !== input) submit(); });
  return input;
}

// The app's categoryTag chip input over a ctxMenu of suggestions, for the
// folder and the labels alike: typing filters the menu, the arrow keys move
// through it, Enter commits the highlighted row or else what was typed,
// Backspace on an empty input removes the last chip. A chip, and a row, is
// `{text, value, icon, row?, chipIcon?, title?}`: `row` is the menu's
// wording when it differs from the chip's, `chipIcon` a glyph on the chip.
// `suggest(fragment, chips, naming)` lists the rows, `fromText(fragment)` makes
// the chip typed text stands for. `single` keeps one chip, which a commit
// replaces; `separator` commits as it is typed (the labels' ","). A fragment
// left uncommitted still counts: `values()` reads the box as a save does.
// `onChange(chip)` follows each chip picked or removed, where that takes
// effect at once (the options page, which has no Save).
//
// A row can also carry `selected` (its check), `last` (it stays in sight at
// the bottom), `tree` (menuRow's; Right and Left fold it and walk the tree, and
// "+" is its "+"), or `start`, which opens a name input instead of being a
// choice: `naming` then holds that `start`, and one of the rows `suggest` lists
// is the input, `{draft(name) → chip, placeholder, depth, last?}`. The focus
// moves into it and back. Committing, Escape, or using another row of the menu
// or a chip's × ends it; those last two make nothing.
//
// `complete(row)` makes Tab a completion key over what is being typed, as a
// shell or VS Code completes a path: it returns the text the row puts in the
// input — nothing committed, the menu filtered again — or "" for a row that
// completes nothing. Tab takes the highlighted row, else the first one. With
// an empty field, or with nothing left to add, Tab leaves for the next field
// as it always has.
export function chipField({ box, input, menu, suggest, fromText, single = false, separator = "",
                           onChange = null, complete = null }) {
  const placeholder = input.placeholder;
  const same = (a, b) => a.text.toLowerCase() === b.text.toLowerCase();
  let chips = [];
  let rows = [];
  let sel = -1;       // the highlighted row
  let naming = null;  // the open name input's `start`, null with none open
  const field = {
    set(list) { naming = null; close(); chips = list; input.value = ""; render(); },
    values() {
      const typed = input.value.trim() ? fromText(input.value.trim()) : null;
      if (!typed || !typed.text) return chips;
      if (single) return [typed];
      return chips.some((c) => same(c, typed)) ? chips : [...chips, typed];
    },
    chosen: () => chips[0],  // what is committed, whatever is typed over it
    refresh: () => update(),
    name(start) { naming = start; update(); },
  };
  const close = () => { sel = -1; menu.classList.add("hidden"); };
  function add(chip) {
    if (single) chips = [chip];
    else if (!chips.some((c) => same(c, chip))) chips = [...chips, chip];
  }
  // The labels' menu stays open for the next one; the folder is chosen. A
  // chip with a write `owed` reports the change itself, once it is made.
  function commit(chip) {
    add(chip);
    input.value = "";
    sel = -1;
    render();
    if (single) close(); else update();
    if (onChange && !chip.owed) onChange(chip);
  }
  function drop(chip) {
    chips = chips.filter((other) => other !== chip);
    render();
    update();
    if (onChange) onChange(chips[0] || null);
  }
  const pick = (r) => { if (r.start === undefined) commit(r); else field.name(r.start); };
  function render() {
    for (const el of box.querySelectorAll(".categoryTag")) el.remove();
    for (const c of chips) {
      const el = document.createElement("span");
      el.className = "categoryTag";
      el.title = c.title || c.text;
      if (c.chipIcon) el.append(icon(c.chipIcon, "", 12));
      const text = document.createElement("span"); text.className = "categoryTagText"; text.textContent = c.text;
      const x = document.createElement("button");
      x.type = "button"; x.className = "uiClose uiCloseSm"; x.tabIndex = -1; x.append(icon("x")); x.title = `Remove "${c.text}"`;
      x.addEventListener("mousedown", (e) => e.preventDefault());
      x.addEventListener("click", leavingName(() => drop(c)));
      el.append(text, x);
      box.insertBefore(el, input);
    }
    // One folder chip leaves nothing to prompt for; none reads "Library root".
    input.placeholder = single && chips.length ? "" : placeholder;
  }
  function highlight(i) {
    sel = i;
    [...menu.children].forEach((el, j) => el.classList.toggle("selected", j === i));
    if (i >= 0) menu.children[i].scrollIntoView({ block: "nearest" });
  }
  // Using another row of the menu, or a chip's ×, drops an open name input.
  const leavingName = (act) => () => {
    if (naming !== null) { naming = null; input.focus(); }
    act();
  };
  // A name input ended, with what makes its chip (`draft`) or null. One
  // dropped already makes nothing. Ended from inside it, the focus goes back
  // to the field, unless it stays for the next label; ended by leaving it, the
  // focus has gone on.
  function endName(name, draft) {
    if (naming === null) return;
    const inside = document.activeElement === name;
    const chip = draft && draft();
    if (single || !inside || !chip) naming = null;
    if (inside && naming === null) input.focus();
    if (chip) commit(chip);
    else if (!inside) close();
  }
  function nameRow(r) {
    const name = inlineName(r.placeholder, {
      commit: (text) => endName(name, () => r.draft(text)),
      cancel: () => endName(name, null),
    });
    const el = document.createElement("div");
    el.className = "menuName" + (r.last ? " menuLast" : "");
    el.style.setProperty("--depth", Math.min(r.depth, 6));
    el.append(name);
    return el;
  }
  function rowElement(r, i) {
    if (r.draft) return nameRow(r);
    const tree = r.tree && { ...r.tree, toggle: leavingName(r.tree.toggle), add: leavingName(r.tree.add) };
    const row = menuRow(r.row || r.text, r.icon, !!r.selected, leavingName(() => pick(r)), tree);
    row.tabIndex = -1;  // Tab goes on to the next field; the keys below reach every row
    row.classList.toggle("menuLast", !!r.last);
    row.classList.toggle("selected", i === sel);
    row.addEventListener("mouseenter", () => highlight(i));
    return row;
  }
  function update() {
    rows = document.activeElement === input || naming !== null ? suggest(input.value.trim(), chips, naming) : [];
    if (!rows.length) { close(); return; }
    sel = Math.min(sel, rows.length - 1);
    const opening = menu.classList.contains("hidden");
    menu.replaceChildren(...rows.map(rowElement));
    placeMenu(menu);
    const name = menu.querySelector("input");
    if (name) name.focus();
    else if (opening) menu.querySelector(".ctxMenuCheck")?.scrollIntoView({ block: "nearest" });
  }
  // The tree's keys on its highlighted row: Right opens a folder, then goes
  // into it; Left closes it, then goes up to its parent. Whether it took the key.
  function treeKey(e, { open, parent, toggle, add }) {
    if (e.key === "ArrowRight") {
      if (open === false) toggle(); else if (open) highlight(sel + 1);
    } else if (e.key === "ArrowLeft") {
      const above = rows.findIndex((r) => r.tree && r.value.folder === parent);
      if (open) toggle(); else if (above >= 0) highlight(above);
    } else if (e.key === "+") add();
    else return false;
    e.preventDefault();
    return true;
  }
  // A press anywhere in the box opens the menu, a chip's × being its own.
  box.addEventListener("pointerdown", (e) => {
    if (e.target === input || e.target.closest(".uiClose")) return;
    e.preventDefault();
    if (document.activeElement === input) update(); else input.focus();
  });
  // A press on the menu (its scrollbar too) leaves the focus in the input, or
  // in a name input, where it sets the caret. Its rows out of the Tab order,
  // Chrome would stop on the scrolling menu instead, which the blur then hides.
  menu.tabIndex = -1;
  menu.addEventListener("mousedown", (e) => { if (e.target.tagName !== "INPUT") e.preventDefault(); });
  input.addEventListener("input", () => {
    if (separator && input.value.includes(separator)) {
      const parts = input.value.split(separator);
      for (const p of parts.slice(0, -1)) if (p.trim()) add(fromText(p.trim()));
      input.value = parts.at(-1).trimStart();
      render();
    }
    sel = -1;
    update();
  });
  input.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    const open = !menu.classList.contains("hidden");
    const tree = open && sel >= 0 && rows[sel].tree;
    if (tree && treeKey(e, tree)) return;
    if (e.key === "Tab" && !e.shiftKey && complete && open && input.value.trim()) {
      const row = rows[sel] || rows[0];
      const text = row ? complete(row) : "";
      if (text && text !== input.value) {
        e.preventDefault();
        input.value = text;
        sel = -1;
        update();
      }
    } else if (e.key === "ArrowDown" && rows.length) {
      e.preventDefault();
      if (open) highlight(Math.min(sel + 1, rows.length - 1)); else update();
    } else if (e.key === "ArrowUp" && open) {
      e.preventDefault(); highlight(Math.max(sel - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const typed = input.value.trim() && fromText(input.value.trim());
      if (open && sel >= 0) pick(rows[sel]);
      else if (typed && typed.text) commit(typed);
    } else if (e.key === "Backspace" && !input.value && chips.length) {
      drop(chips.at(-1));
    } else if (e.key === "Escape" && open) {
      e.preventDefault();  // or Chrome closes the popup with the menu
      close();
    }
  });
  // Focus opens the menu, and so does a click once focused (after a pick, or Escape).
  input.addEventListener("focus", update);
  input.addEventListener("click", update);
  input.addEventListener("blur", () => {
    if (naming !== null) return;
    close();
    if (onChange) input.value = "";  // with no Save to read it, it says nothing
  });
  render();
  return field;
}

// Whether a folder's path (its names) holds a typed one: each typed name,
// in order, within one of as many consecutive names of the path, so
// "phys/c" finds "Physics / Clocks" and "2026" any folder named with it.
function pathHolds(names, fragment) {
  const want = pathNames(fragment.toLowerCase());
  const have = names.map((name) => name.toLowerCase());
  for (let i = 0; i + want.length <= have.length; i++) {
    if (want.every((w, j) => have[i + j].includes(w))) return true;
  }
  return false;
}

// A path as the field writes it, the chips' separator ("Lab / Clocks"), and
// the same with the separator that opens the folder: what Tab leaves in the
// field for a folder with subfolders, so the next keys search inside it.
const pathText = (path, inside = false) => path.join(" / ") + (inside ? " / " : "");

// A chip's value is the filing POST /api/clip takes: a folder id, or a new
// folder's path (made by the save).
const folderChip = (f) => ({ text: pathText(f.path), value: { folder: f.id, folder_path: "" },
                             icon: "folder", chipIcon: "folder" });

function newFolderChip(path) {
  const names = pathNames(path);
  const text = pathText(names);
  return { text, value: { folder: "", folder_path: names.join("/") }, icon: "folderPlus", chipIcon: "folderPlus",
           row: `New folder “${text}”`, title: `${text} — a new folder, made when you save` };
}

// The folder field of the popup and the options page: one chip over the
// library's folders, none being the library root. With nothing typed its menu
// is their tree, the folders above the chosen one open: a chevron folds a
// folder, a click picks it. A row's "+" and the last row, "New folder…", name a
// new folder in place. It is made at once, by its id, so that a parent holding
// "/" or sharing its name with a sibling is the right one; a name a sibling
// has picks that sibling. Typed text instead lists every folder whose path
// holds it, and in the popup a path no folder has as a new folder, made by the
// save. A chip's `owed` is the write not yet known to have landed, which `owed()`
// hands to a save to send again, so the save cannot go to another folder
// instead; `onError(err)` shows a failure. With `onChange` (chipField) a choice
// takes effect at once, so a new folder reports once it exists, and typed text
// only filters.
export function folderChipField({ box, input, menu, onChange = null, onError }) {
  let folders = [];        // GET /api/library/folders' list
  let tree = folderTree([]);
  let listed = false;      // whether that list could be read
  let revealed = "";       // the chosen folder whose parents were last opened
  const open = new Set();  // the folders showing what is inside them

  // The chip of the folder at a path, else of a new folder of that path.
  function atPath(path) {
    const found = tree.byId.get(folderByPath(folders, path));
    return found ? folderChip(found) : newFolderChip(path);
  }
  const fromText = (path) => {
    const chip = atPath(path);
    return onChange && !chip.value.folder ? { text: "" } : chip;
  };

  // The chip of the folder called `name` under `parent` (a node, null = the
  // top level): the one there, else a new one, on its way.
  function named(parent, name) {
    const found = namedKid(parent ? parent.kids : tree.roots, name);
    if (found) return folderChip(found);
    const id = crypto.randomUUID();
    const path = [...(parent ? parent.path : []), name];
    const list = folders;
    const owed = { id, parent: parent ? parent.id : "", name };
    const chip = { ...folderChip({ id, path }), chipIcon: "folderPlus", title: `${path.join(" / ")} — a new folder`, owed };
    chip.landed = () => {
      chip.owed = null;
      list.unshift({ id, path });
      if (list === folders) tree = folderTree(folders);
      if (onChange && field.chosen() === chip) onChange(chip);
    };
    makeFolder(owed).then(() => { if (chip.owed) chip.landed(); },
                          (err) => { if (chip.owed && field.chosen() === chip) onError(err); });
    return chip;
  }

  function treeRows(chosen, naming) {
    if (chosen !== revealed) {
      revealed = chosen;
      for (let f = tree.byId.get(chosen)?.parent; f; f = f.parent) open.add(f.id);
    }
    const nameInput = (parent, depth, last) => ({ draft: (name) => named(parent, name), placeholder: "Folder name", depth, last });
    const rows = [];
    const walk = (nodes, depth) => {
      for (const f of nodes) {
        rows.push({ ...folderChip(f), row: f.name, selected: f.id === chosen, tree: {
          depth, open: f.kids.length ? open.has(f.id) : null, parent: f.parent ? f.parent.id : "", title: f.path.join(" / "),
          toggle: () => { if (!open.delete(f.id)) open.add(f.id); field.refresh(); },
          add: () => { open.add(f.id); field.name(f.id); },
        } });
        if (naming === f.id) rows.push(nameInput(f, depth + 1));
        if (open.has(f.id)) walk(f.kids, depth + 1);
      }
    };
    walk(tree.roots, 0);
    rows.push(naming === "" ? nameInput(null, 0, true) : { row: "New folder…", icon: "folderPlus", start: "", last: true });
    return rows;
  }

  // The folder a typed path ending in the separator names, when it has
  // subfolders: "Lab / Clocks /" asks to see what is inside, which is where
  // Tab's completion leaves the field. One with nothing inside is no such
  // ask — its own row, from the match below, is what the field still needs.
  function insideOf(fragment) {
    if (!/\/\s*$/.test(fragment)) return null;
    const node = tree.byId.get(folderByPath(folders, fragment));
    return node && node.kids.length ? node : null;
  }

  // Every folder whose path holds the typed names, those whose own name the
  // last typed one begins first: that is the folder being named, rather than
  // one merely holding the text somewhere above it — and so what Tab takes.
  function ranked(fragment, chosen) {
    const last = pathNames(fragment.toLowerCase()).at(-1) || "";
    const found = folders.filter((f) => f.id !== chosen && pathHolds(f.path, fragment));
    const heads = found.filter((f) => f.path.at(-1).toLowerCase().startsWith(last));
    return [...heads, ...found.filter((f) => !heads.includes(f))];
  }

  function suggest(fragment, chips, naming) {
    const chosen = chips.length ? chips[0].value.folder : "";
    if (!fragment) return listed ? treeRows(chosen, naming) : [];
    const inside = insideOf(fragment);
    const rows = (inside ? inside.kids : ranked(fragment, chosen)).map(folderChip);
    if (!onChange && pathNames(fragment).length && !folderByPath(folders, fragment)) rows.push(newFolderChip(fragment));
    return rows;
  }

  // Tab's completion: the row's folder written out in full, ending in the
  // separator when it holds subfolders, so Tab after Tab walks down the tree.
  // The ways to a new folder complete nothing — they are a choice, not a path.
  function complete(row) {
    const node = row.value && row.value.folder && tree.byId.get(row.value.folder);
    return node ? pathText(node.path, node.kids.length > 0) : "";
  }

  const field = chipField({ box, input, menu, suggest, fromText, single: true, onChange, complete });
  return {
    ...field,
    // The library's folders (null when they could not be read, like the bare
    // paths a server from before folder ids lists) and the default folder
    // (defaultFolder). A path stored before folders had ids picks the folder
    // of that path, else comes up as a new folder, which the next save makes.
    // An id the list lacks leaves the field empty, the library root; with no
    // list it says nothing of that id, which stays the default.
    load(list, { folder, folder_path }) {
      listed = !!list && list.every((f) => f.path);
      folders = listed ? list : [];
      tree = folderTree(folders);
      open.clear();
      revealed = "";
      const found = tree.byId.get(folder);
      const chip = folder_path ? atPath(folder_path) : found ? folderChip(found)
        : folder && !listed ? folderChip({ id: folder, path: ["Default folder"] }) : null;
      field.set(chip ? [chip] : []);
    },
    clear() { this.load(null, { folder: "", folder_path: "" }); },
    // What a save must make first for the folder it files into: makeFolder's
    // first argument, or null. `landed()` says a save did, which an answer to
    // the field's own write, still out or lost, can then not undo.
    owed() {
      const [chip] = field.values();
      return (chip && chip.owed) || null;
    },
    landed() {
      const [chip] = field.values();
      if (chip && chip.owed) chip.landed();
    },
  };
}
