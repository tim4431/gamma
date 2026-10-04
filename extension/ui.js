// Controls shared by the popup and the options page, on popup.css's recipes:
// the app's icons, ctxMenu rows and the folder picker (a MenuSelect).

// ---------- icons (mirrors frontend/src/shared/ui/Icons.jsx — 24×24 stroke glyphs) ----------

const ICON_PATHS = {
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  folderPlus: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M12 10v6"/><path d="M9 13h6"/>',
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

// ---------- the folder picker ----------

export const NEW_FOLDER = "__new__";

// The app's MenuSelect over `button` and its `menu` (siblings in a
// .selectWrap): "Library root", every folder of GET /api/library/folders
// ({id, path}) as its path, and "New folder…" (NEW_FOLDER) with `newFolder`.
// `value` is a folder id, "" for the root; a pick calls onPick(value).
export function folderPicker(button, menu, onPick, { newFolder = false } = {}) {
  let folders = [];
  const picker = {
    value: "",
    // An id that is no listed folder (deleted, or another server's) is the root.
    set(list, value) {
      folders = list;
      picker.value = value === NEW_FOLDER || folders.some((f) => f.id === value) ? value : "";
      render();
    },
  };
  const close = () => menu.classList.add("hidden");
  function render() {
    const folder = folders.find((f) => f.id === picker.value);
    const ic = document.createElement("span"); ic.className = "ctxMenuIcon";
    ic.appendChild(icon(picker.value === NEW_FOLDER ? "folderPlus" : "folder"));
    const label = document.createElement("span"); label.className = "uiSelectLabel";
    label.textContent = picker.value === NEW_FOLDER ? "New folder…" : folder ? folder.path.join(" / ") : "Library root";
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
    menu.replaceChildren(menuRow("Library root", "folder", picker.value === "", pick("")),
      ...folders.map((f) => menuRow(f.path.join(" / "), "folder", picker.value === f.id, pick(f.id))));
    if (newFolder) menu.appendChild(menuRow("New folder…", "folderPlus", picker.value === NEW_FOLDER, pick(NEW_FOLDER)));
    menu.classList.remove("hidden");
  });
  document.addEventListener("pointerdown", (e) => { if (!button.parentElement.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  render();
  return picker;
}
