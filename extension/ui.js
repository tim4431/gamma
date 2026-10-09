// Controls shared by the popup and the options page, on popup.css's recipes:
// the app's icons, ctxMenu rows and the folder picker (a MenuSelect).

// ---------- icons (mirrors frontend/src/shared/ui/Icons.jsx — 24×24 stroke glyphs) ----------

const ICON_PATHS = {
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  folderPlus: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M12 10v6"/><path d="M9 13h6"/>',
  folders: '<path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/>',
  tag: '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',

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

// A ctxMenu row: icon, text, a check when selected.
export function menuRow(text, iconName, selected, onPick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "ctxMenuItem ctxMenuItemIconed";
  const ic = document.createElement("span"); ic.className = "ctxMenuIcon"; ic.appendChild(icon(iconName));
  const t = document.createElement("span"); t.className = "ctxMenuText"; t.textContent = text; t.title = text;
  b.append(ic, t);
  if (selected) b.appendChild(icon("check", "ctxMenuCheck", 14, 2));
  // mousedown, not click: keeps focus where it is (the popup's labels input relies on this).
  b.addEventListener("mousedown", (e) => e.preventDefault());
  b.addEventListener("click", onPick);
  return b;
}

// ---------- the pickers ----------

export const NEW_FOLDER = "__new__";

// The app's MenuSelect over `button` and its `menu` (siblings in a
// .selectWrap). `set(items, value)` takes `[{value, label, icon}]` and the
// one to show; a value no item carries falls back to the first, which is
// each list's "everything" row (the library root, the default workspace).
// A pick calls onPick(value). `toItems` maps what `set` is given to items.
export function menuSelect(button, menu, onPick, toItems = (list) => list) {
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
    menu.classList.remove("hidden");
  });
  document.addEventListener("pointerdown", (e) => { if (!button.parentElement.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  render();
  return picker;
}

// GET /api/library/folders' folders ({id, path}) by their path, under
// "Library root" and over "New folder…" (NEW_FOLDER) with `newFolder`.
// `value` is a folder id, "" for the root.
export function folderPicker(button, menu, onPick, { newFolder = false } = {}) {
  return menuSelect(button, menu, onPick, (folders) => [
    { value: "", label: "Library root", icon: "folder" },
    ...folders.map((f) => ({ value: f.id, label: f.path.join(" / "), icon: "folder" })),
    ...(newFolder ? [{ value: NEW_FOLDER, label: "New folder…", icon: "folderPlus" }] : []),
  ]);
}

// The libraries GET /api/session lists, by name. `value` is a workspace id,
// "" for the account's default one — which the server lists first, and
// which saves name no workspace at all, as they always have.
export function workspacePicker(button, menu, onPick) {
  return menuSelect(button, menu, onPick,
    (workspaces) => workspaces.map((w) => ({ value: w.default ? "" : w.id, label: w.name, icon: "folders" })));
}
