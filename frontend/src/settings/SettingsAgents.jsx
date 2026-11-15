// Settings → AI → Connections' first section, Agents (docs/dev/settings.md
// "AI", docs/dev/local_agents.md "Who turns it on"): who the chat talks to.
// Gamma AI, with the connection it uses; then this computer, on the
// desktop app's own server (GET /api/local-agents, chat/useLocalAgents.js):
// each agent it found, its version, whether it is signed in, whether it is
// on, what it may run, the people its owner lets use it (co-authors:
// decision 13 of docs/research/agents-as-participants-plan.md), and the
// directories it offers them. In the desktop app the controls ask the shell
// (vault/useVault.js askShell: `set-agent`, `set-agent-runs`, `agent-dirs`,
// `agent-person`), which asks its own question and acts with its word; the
// page then reads the listing again until it changed. In any other window
// the same rows are read-only and say to change them in Gamma on that
// computer, but the owner may turn an agent off, or take a person off it,
// with the account's session. In the desktop app any agent on may be
// turned off, another account's too (the shell's word lets it), and the
// servers this computer answers follow (AnsweredServers), which no other
// window shows. On another
// server, the account's computers shown to it and their agents as each last
// announced them (GET /api/computers), read-only, and those of another
// account's computers that serve it, with who lends them.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { askShell } from "../vault/useVault.js";
import { sinceText } from "../vault/vaultState.js";
import { agentPeople, computerAgents, localAgent } from "../chat/agents.js";
import { readLocalAgents, useLocalAgents } from "../chat/useLocalAgents.js";
import { useSettingsRead } from "./settingsReads.js";
import { ADD_SERVER, linkRow } from "./answeredServers.js";
import { Empty, NameInput, Row, Section, Segmented, Toggle } from "./SettingsKit";
import { ActionMenu } from "../shared/ui/Menus";
import { BotIcon, FolderIcon, FolderPlusIcon, MonitorIcon, PlusIcon, ServerIcon, SparklesIcon, Trash2Icon, UserIcon,
  UserPlusIcon } from "../shared/ui/Icons";
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

  // After an ask of the shell: what it changes (the listing, unless `read`
  // and `current` name another read) read again until it changed.
  const watching = React.useRef(null);
  React.useEffect(() => () => clearInterval(watching.current), []);
  function ask(type, fields, { read = readLocalAgents, current = () => listingRef.current } = {}) {
    askShell(type, fields);
    clearInterval(watching.current);
    const before = JSON.stringify(current());
    const until = Date.now() + WATCH_MS;
    watching.current = setInterval(async () => {
      const now = await read().catch(() => current());
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
      {here && IS_DESKTOP ? <AnsweredServers ask={ask} /> : null}
      {far.map((computer) => <FarComputer key={computer.id || computer.name} computer={computer} />)}
    </>
  );
}

// The people an agent of the account's serves beside it (`agent.people`),
// each with its workspace and, for its owner, a way to take them off (the
// session's DELETE, in any window); in the desktop app a menu of whom else
// it may let use it (GET /api/local-agents/people, `places`), each asking
// the shell (`agent-person`), whose question carries the consent.
function AgentPeople({ agent, places, desktop, ask, change }) {
  const { named, offered } = agentPeople(agent.people, places);
  const remove = (p) => change(`/local-agents/${agent.name}/people?${new URLSearchParams({
    server: p.server, workspace_id: p.workspace_id, account: p.account })}`, { method: "DELETE" },
  (message) => t("Couldn't take {person} off {agent}: {message}", { person: p.username, agent: agent.label, message }));
  if (!named.length && !desktop) return null;
  return (
    <>
      {named.map((p) => (
        <Row key={p.key} icon={UserIcon} label={p.username} hint={t("May use {agent} in {workspace}", { agent: agent.label, workspace: p.where })}>
          <button type="button" className="uiBtn sm iconSq danger" title={t("Stop letting {person} use {agent}", { person: p.username, agent: agent.label })}
            aria-label={t("Stop letting {person} use {agent}", { person: p.username, agent: agent.label })} onClick={() => remove(p)}>
            <Trash2Icon size={16} />
          </button>
        </Row>
      ))}
      {desktop ? (
        <Row icon={UserPlusIcon} label={t("Who else may use {agent}", { agent: agent.label })}
          hint={t("They ask it with @ or Send to on your synced folders' files, in a conversation you can read; what it asks to run is allowed once at most.")}>
          <ActionMenu label={t("Add a person…")} disabled={!offered.length}
            items={offered.map((o) => ({ label: o.label, onClick: () => ask("agent-person", { agent: agent.name, place: o.place.workspace_id, person: o.person.id }) }))} />
        </Row>
      ) : null}
    </>
  );
}

// This computer's agents and the directories it offers them, `desktop`
// whether this window is the desktop app's (its controls ask the shell).
function ThisComputer({ listing, desktop, ask, change }) {
  const name = listing.computer || t("this computer");
  const agents = (listing.agents || []).map(localAgent);
  const owned = agents.some((a) => a.enabled && a.owner && !a.lentBy);
  const places = useSettingsRead(desktop && owned ? "/local-agents/people" : null).data?.places || [];
  const dirs = listing.dirs || [];
  const putDirs = (next) => change("/local-agents/dirs", {
    method: "PUT", body: JSON.stringify(next.map((d) => ({ id: d.id, path: d.path, name: d.name || "" }))),
  }, (message) => t("Couldn't change the directories: {message}", { message }));
  return (
    <Section title={listing.computer ? t("This computer: {computer}", { computer: listing.computer }) : t("This computer")}>
      {desktop ? null : <p className="setNotice">{t("Change these in Gamma on {computer}.", { computer: name })}</p>}
      {agents.map((agent) => {
        const mine = agent.enabled && agent.owner && !agent.lentBy;
        const others = agent.enabled && !mine;
        const offRow = mine && !desktop;
        return (
          <React.Fragment key={agent.name}>
            <Toggle icon={BotIcon} label={agent.label}
              hint={agent.lentBy ? t("{owner} lets you use it in this workspace", { owner: agent.lentBy })
                : others ? t("Turned on by another account on this computer") : agentHint(agent)}
              checked={agent.enabled} disabled={desktop ? !agent.enabled && agent.ready === false : !offRow}
              onChange={(on) => (desktop ? ask("set-agent", { agent: agent.name, enabled: on })
                : change(`/local-agents/${agent.name}`, { method: "PATCH", body: JSON.stringify({ enabled: false }) },
                  (message) => t("Couldn't turn {agent} off: {message}", { agent: agent.label, message })))} />
            {mine ? (
              <Row icon={null} label={t("What {agent} may run", { agent: agent.label })}
                hint={agent.runs === "ask" ? t("It asks you before it runs a command or changes a file outside its directory.")
                  : t("It works in its directory and runs nothing outside its sandbox.")}>
                <Segmented value={agent.runs} disabled={!desktop} options={RUNS}
                  onChange={(runs) => ask("set-agent-runs", { agent: agent.name, ask: runs === "ask" })} />
              </Row>
            ) : null}
            {mine ? <AgentPeople agent={agent} places={places} desktop={desktop} ask={ask} change={change} /> : null}
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

// The servers this computer answers (step 4.1 of
// docs/research/agents-as-participants-plan.md), the desktop app's alone:
// its links to other servers (GET /api/computer-links, one per server,
// workspace there and account), each with its address, the account there
// and how its channel stands, Remove, a new token for one that server
// refused, and whether that server may answer what the agents here ask
// (decision 7), off to begin with; then Add a server…. Every control asks
// the shell (`add-server`, `remove-server`, `link-asks`: answeredServers.js
// linkRow), which asks its own question and acts with its word; the section
// then reads the links again until they changed.
function AnsweredServers({ ask }) {
  const links = useSettingsRead("/computer-links");
  const linksRef = React.useRef(links.data);
  linksRef.current = links.data;
  const askLinks = ([type, fields]) => ask(type, fields, { read: links.reload, current: () => linksRef.current });
  const rows = links.data?.links || [];
  return (
    <Section title={t("Servers this computer answers")}>
      {links.error && !links.data ? <p className="setNotice">{t("Could not read the servers this computer answers: {message}", { message: links.error })}</p> : null}
      {rows.map((link) => {
        const row = linkRow(link);
        return (
          <React.Fragment key={link.id}>
            <Row icon={ServerIcon} label={row.label} hint={row.hint}>
              {row.reconnect ? (
                <button type="button" className="uiBtn sm" onClick={() => askLinks(row.reconnect)}>{t("Reconnect…")}</button>
              ) : null}
              <button type="button" className="uiBtn sm iconSq danger" title={t("Stop answering {server}", { server: row.server })}
                aria-label={t("Stop answering {server}", { server: row.server })} onClick={() => askLinks(row.remove)}>
                <Trash2Icon size={16} />
              </button>
            </Row>
            <Toggle icon={null} label={row.asks.label} hint={row.asks.hint} checked={row.asks.on}
              onChange={(on) => askLinks(row.asks.set(on))} />
          </React.Fragment>
        );
      })}
      <Row icon={PlusIcon} label={t("Another server")}
        hint={t("Its chats can then send work to the agents you turned on here, for your account there. No synced folder is needed.")}>
        <button type="button" className="uiBtn sm" onClick={() => askLinks(ADD_SERVER)}>{t("Add a server…")}</button>
      </Row>
    </Section>
  );
}

// Another computer of the account's, as it last announced itself to this
// server: its agents, read-only; or another account's whose agent serves
// this one (`lent_by`), that agent alone.
function FarComputer({ computer }) {
  const agents = computerAgents(computer);
  const online = computer.online === true;
  const when = online ? "" : sinceText(computer.since);
  const lent = computer.lent_by || "";
  return (
    <Section title={lent ? t("{computer}, {owner}'s", { computer: computer.name, owner: lent }) : computer.name}>
      <Row icon={MonitorIcon} label={online ? t("Connected") : when ? t("Offline since {when}", { when }) : t("Offline")}
        hint={lent ? t("{owner} lets you use its agent on the files of the synced folders it keeps.", { owner: lent })
          : t("Change this in Gamma on {computer}.", { computer: computer.name })} />
      {agents.length ? agents.map((agent) => (
        <Row key={agent.name} icon={BotIcon} label={agent.label} hint={agentHint(agent)}>
          <span className="uiTag">{lent ? t("Lent to you") : agent.enabled ? (agent.runs === "ask" ? t("On, asks before more") : t("On")) : t("Off")}</span>
        </Row>
      )) : <p className="setNotice">{t("No agent found on {computer}.", { computer: computer.name })}</p>}
    </Section>
  );
}
