// Settings → AI → Connections' first section, Agents (docs/dev/settings.md
// "AI", docs/dev/local_agents.md "Who turns it on"): who the chat talks to.
// Gamma AI, with the connection it uses; then this computer, on the
// desktop app's own server (GET /api/local-agents, chat/useLocalAgents.js):
// each agent it found, its version, whether it is signed in, whether it is
// on, what it may run, and the directories it offers them. In the desktop
// app the controls ask the shell (vault/useVault.js askShell: `set-agent`,
// `set-agent-runs`, `agent-dirs`), which asks its own question and acts
// with its word; the page then reads the listing again until it changed.
// In any other window the same rows are read-only and say to change them
// in Gamma on that computer, but the owner may turn an agent off with the
// account's session. In the desktop app any agent on may be turned off,
// another account's too (the shell's word lets it). On another server, the account's
// computers shown to it and their agents as each last announced them (GET
// /api/computers), read-only.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { askShell } from "../vault/useVault.js";
import { sinceText } from "../vault/vaultState.js";
import { computerAgents, localAgent } from "../chat/agents.js";
import { readLocalAgents, useLocalAgents } from "../chat/useLocalAgents.js";
import { useSettingsRead } from "./settingsReads.js";
import { Empty, NameInput, Row, Section, Segmented, Toggle } from "./SettingsKit";
import { BotIcon, FolderIcon, FolderPlusIcon, MonitorIcon, SparklesIcon, Trash2Icon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
// How long the page reads the listing again after asking the shell, and how
// often: the shell's question waits for the person.
const WATCH_MS = 60_000;
const WATCH_EVERY_MS = 1000;

// An agent's line under its name: its version and how it stands.
function agentHint(agent) {
  const state = agent.ready === true ? t("Signed in") : agent.ready == null ? t("Checking…") : agent.problem;
  return [agent.version ? t("Version {version}", { version: agent.version }) : "", state].filter(Boolean).join(" · ");
}

// What an agent may run, as the segment names it: within its sandbox
// alone, or asking first for anything more (a command, a file outside).
const RUNS = [["never", t("Only in its sandbox")], ["ask", t("Asks before more")]];

// `connection` the connection Gamma AI uses ("" for none), `signedIn`
// whether a signed-in account reads the pane (a guest has no agents).
export function SettingsAgents({ connection, signedIn, setStatus }) {
  const { listing, error } = useLocalAgents(signedIn, { withError: true });
  const computers = useSettingsRead(signedIn ? "/computers" : null);
  React.useEffect(() => { if (signedIn) readLocalAgents({ fresh: true }); }, [signedIn]);
  const here = listing?.here === true;
  const listingRef = React.useRef(listing);
  listingRef.current = listing;

  // After an ask of the shell: the listing read again until it changed.
  const watching = React.useRef(null);
  React.useEffect(() => () => clearInterval(watching.current), []);
  function ask(type, fields) {
    askShell(type, fields);
    clearInterval(watching.current);
    const before = JSON.stringify(listingRef.current);
    const until = Date.now() + WATCH_MS;
    watching.current = setInterval(async () => {
      const now = await readLocalAgents();
      if (Date.now() > until || JSON.stringify(now) !== before) clearInterval(watching.current);
    }, WATCH_EVERY_MS);
  }

  // A change the page makes itself, then the listing read again; `failed`
  // words a refusal.
  async function change(path, init, failed) {
    try {
      await apiJson(`${API}${path}`, { headers: JSON_HEADERS, ...init });
    } catch (err) {
      setStatus?.(failed(err.message || String(err)));
    }
    readLocalAgents();
  }

  const far = (computers.data?.computers || []).filter((c) => c && typeof c.name === "string" && c.name);
  return (
    <>
      <Section title={t("Agents")}>
        <Row icon={SparklesIcon} label={t("Gamma AI")}
          hint={connection ? t("Uses {connection}", { connection }) : t("Connect an AI service under Connections to use it.")}>
          <span className={`uiTag${connection ? "" : " warn"}`}>{connection ? t("Ready") : t("Not connected")}</span>
        </Row>
        {signedIn && !listing && !error ? <Empty icon={BotIcon} loading>{t("Loading…")}</Empty> : null}
        {error && !listing ? <p className="setNotice">{t("Could not read the agents on this computer: {message}", { message: error })}</p> : null}
      </Section>
      {here ? <ThisComputer listing={listing} desktop={IS_DESKTOP} ask={ask} change={change} /> : null}
      {far.map((computer) => <FarComputer key={computer.id || computer.name} computer={computer} />)}
    </>
  );
}

// This computer's agents and the directories it offers them, `desktop`
// whether this window is the desktop app's (its controls ask the shell).
function ThisComputer({ listing, desktop, ask, change }) {
  const name = listing.computer || t("this computer");
  const agents = (listing.agents || []).map(localAgent);
  const dirs = listing.dirs || [];
  const putDirs = (next) => change("/local-agents/dirs", {
    method: "PUT", body: JSON.stringify(next.map((d) => ({ id: d.id, path: d.path, name: d.name || "" }))),
  }, (message) => t("Couldn't change the directories: {message}", { message }));
  return (
    <Section title={listing.computer ? t("This computer: {computer}", { computer: listing.computer }) : t("This computer")}>
      {desktop ? null : <p className="setNotice">{t("Change these in Gamma on {computer}.", { computer: name })}</p>}
      {agents.map((agent) => {
        const others = agent.enabled && !agent.owner;
        const offRow = agent.enabled && agent.owner && !desktop;
        return (
          <React.Fragment key={agent.name}>
            <Toggle icon={BotIcon} label={agent.label}
              hint={others ? t("Turned on by another account on this computer") : agentHint(agent)}
              checked={agent.enabled} disabled={desktop ? !agent.enabled && agent.ready === false : !offRow}
              onChange={(on) => (desktop ? ask("set-agent", { agent: agent.name, enabled: on })
                : change(`/local-agents/${agent.name}`, { method: "PATCH", body: JSON.stringify({ enabled: false }) },
                  (message) => t("Couldn't turn {agent} off: {message}", { agent: agent.label, message })))} />
            {agent.enabled && agent.owner ? (
              <Row icon={null} label={t("What {agent} may run", { agent: agent.label })}
                hint={agent.runs === "ask" ? t("It asks you before it runs a command or changes a file outside its directory.")
                  : t("It works in its directory and runs nothing outside its sandbox.")}>
                <Segmented value={agent.runs} disabled={!desktop} options={RUNS}
                  onChange={(runs) => ask("set-agent-runs", { agent: agent.name, ask: runs === "ask" })} />
              </Row>
            ) : null}
          </React.Fragment>
        );
      })}
      {dirs.map((dir) => (
        <Row key={dir.id} icon={FolderIcon} label={dir.name} hint={dir.path}>
          {desktop ? <>
            <NameInput className="aiKeyInput" value={dir.name} aria-label={t("Name of the directory")}
              onCommit={(next) => putDirs(dirs.map((d) => (d.id === dir.id ? { ...d, name: next } : d)))} />
            <button type="button" className="uiBtn sm" onClick={() => ask("agent-dirs", { dir: dir.id })}>{t("Move…")}</button>
            <button type="button" className="uiBtn sm iconSq danger" title={t("Stop offering this directory")}
              aria-label={t("Stop offering this directory")} onClick={() => putDirs(dirs.filter((d) => d.id !== dir.id))}>
              <Trash2Icon size={16} />
            </button>
          </> : null}
        </Row>
      ))}
      {desktop ? (
        <Row icon={FolderPlusIcon} label={t("Directories offered")}
          hint={t("Where an agent can work from a chat whose page is in no synced folder.")}>
          <button type="button" className="uiBtn sm" onClick={() => ask("agent-dirs", { dir: "new" })}>{t("Offer a directory…")}</button>
        </Row>
      ) : null}
    </Section>
  );
}

// Another computer of the account's, as it last announced itself to this
// server: its agents, read-only.
function FarComputer({ computer }) {
  const agents = computerAgents(computer);
  const online = computer.online === true;
  const when = online ? "" : sinceText(computer.since);
  return (
    <Section title={computer.name}>
      <Row icon={MonitorIcon} label={online ? t("Connected") : when ? t("Offline since {when}", { when }) : t("Offline")}
        hint={t("Change this in Gamma on {computer}.", { computer: computer.name })} />
      {agents.length ? agents.map((agent) => (
        <Row key={agent.name} icon={BotIcon} label={agent.label} hint={agentHint(agent)}>
          <span className="uiTag">{agent.enabled ? (agent.runs === "ask" ? t("On, asks before more") : t("On")) : t("Off")}</span>
        </Row>
      )) : <p className="setNotice">{t("No agent found on {computer}.", { computer: computer.name })}</p>}
    </Section>
  );
}
