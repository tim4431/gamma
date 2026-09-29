// Shared options rows and popup switcher, using the Connector's menu controls.
export function renderServerList(container, settings, { select, remove, busy = false }) {
  container.replaceChildren();
  for (const origin of settings.servers) {
    const current = origin === settings.server;
    const row = document.createElement("div");
    row.className = "serverRow" + (current ? " current" : "");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ctxMenuItem ctxMenuItemIconed serverPick";
    button.dataset.origin = origin;
    button.setAttribute("aria-pressed", String(current));
    button.title = current ? `${origin} (current server)` : `Switch to ${origin}`;
    button.disabled = busy;
    const check = document.createElement("span");
    check.className = "ctxMenuIcon serverCheck";
    check.setAttribute("aria-hidden", "true");
    check.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4 10-10"/></svg>';
    const label = document.createElement("span");
    label.className = "ctxMenuText";
    label.textContent = origin;
    button.append(check, label);
    button.onclick = () => select(origin);
    const close = document.createElement("button");
    close.type = "button";
    close.className = "uiClose serverRemove";
    close.setAttribute("aria-label", `Remove ${origin}`);
    close.title = `Remove ${origin}`;
    close.disabled = busy;
    close.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.74" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m18 6-12 12M6 6l12 12"/></svg>';
    close.onclick = () => remove(origin);
    row.append(button, close);
    container.append(row);
  }
  container.onkeydown = (event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const buttons = [...container.querySelectorAll(".serverPick:not(:disabled)")];
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
  };
}
