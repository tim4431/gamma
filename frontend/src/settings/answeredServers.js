// The rows of Settings → AI → Agents → Servers this computer answers
// (SettingsAgents.jsx AnsweredServers; step 4.1 of
// docs/research/agents-as-participants-plan.md), pure: what each link to
// another server (GET /api/computer-links) says and which shell ask each of
// its controls sends (desktop/preload.js PAGE_ASKS: `add-server`,
// `remove-server`, `link-asks`).
import { t } from "../shared/i18n/i18n.js";
import { sinceText } from "../vault/vaultState.js";

// Add a server…: the shell asks which.
export const ADD_SERVER = ["add-server", { link: "new" }];

// A link's server as its row names it: the address's host name.
export function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url || "";
  }
}

// How a link's channel stands (`status`, docs/dev/computers.md "Where the
// credential is kept"), since when it is so where it is not connected.
export function linkState(status) {
  const when = status?.at ? sinceText(status.at) : "";
  switch (status?.state) {
    case "connected": return t("Connected");
    case "refused": return when ? t("Refused this computer's token since {when}", { when }) : t("Refused this computer's token");
    case "older": return t("Older than this app: it cannot show this computer");
    case "unreachable": return when ? t("Not reachable since {when}", { when }) : t("Not reachable");
    default: return t("Connecting…");
  }
}

// A link's row: `{server, label, hint, reconnect, remove, asks: {label,
// hint, title, on, set}}`, `label` its host and the workspace there, `hint` its
// address, the account there and how its channel stands; `reconnect` the
// shell ask that hands a server that refused this computer's token a new one
// (null otherwise), `remove` the one that stops answering it, `asks.set(on)`
// the one that turns its switch on or off.
export function linkRow(link) {
  const server = hostOf(link.remote_url);
  const who = link.username ? t("as {account}", { account: link.username }) : "";
  return {
    server,
    label: link.workspace_name ? t("{server}: {workspace}", { server, workspace: link.workspace_name }) : server,
    hint: [link.remote_url, who, linkState(link.status)].filter(Boolean).join(" · "),
    reconnect: link.status?.state === "refused" ? ["add-server", { link: link.id }] : null,
    remove: ["remove-server", { link: link.id }],
    asks: {
      label: t("{server} may answer what the agents ask", { server }),
      hint: t("On: the agents' asks may also be allowed from {server}", { server }),
      title: t("Off, what an agent here asks to run is answered on this computer only. On, it can also be allowed in any window signed in to {server} as you, and once by a person you let use the agent, for their own turn.", { server }),
      on: Boolean(link.asks),
      set: (on) => ["link-asks", { link: link.id, asks: on }],
    },
  };
}
