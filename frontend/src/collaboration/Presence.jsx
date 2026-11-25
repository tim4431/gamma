// Who else is on the page (collaboration/usePageCollab.js `peers`): the avatar stack in the page
// header, and the small chips on the block a person is on. A person's colour
// comes from their name (personColor.js), so the same person looks the same
// here as beside their comment; an open editor shows as a full avatar, a
// mere viewer as a faded one. `PersonAvatar` draws someone who is not on the
// page, by username alone, `AgentAvatar` an agent, by its kind, and
// `ActorAvatar` whoever wrote or did something, either one.
import React, { useLayoutEffect, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { agentKindIcon } from "../chat/AgentPicker";
import { renderedCaretRect } from "../editor/clickToSource";
import { scanMathSpans } from "../editor/mdScan";
import { personColor } from "./personColor.js";

function initial(peer) {
  const name = (peer?.name || "").trim();
  return name && name !== "Anonymous" ? name[0].toUpperCase() : "?";
}

function describe(peer) {
  const what = peer.anchor >= 0 ? "editing" : peer.block ? t("on a block") : "viewing";
  // No account behind the peer: a share-link visitor under a chosen name.
  return `${peer.name || "Anonymous"}${peer.user ? "" : " (via link)"} · ${what}`;
}

function PeerAvatar({ peer, onClick, title }) {
  return (
    <span
      className={`peerAvatar peer-${personColor(peer.name)}${peer.anchor >= 0 ? "" : " viewing"}`}
      title={title || describe(peer)}
      onClick={onClick}
      role={onClick ? "button" : undefined}
    >{initial(peer)}</span>
  );
}

// The avatar of someone who is not on the page, by username: a share's
// owner, a person to mention.
export function PersonAvatar({ name, title }) {
  return <PeerAvatar peer={{ name, anchor: 0 }} title={title} />;
}

// The avatar of an agent that wrote or made something, by its kind
// (chat/agents.js agentKind): its icon in place of an initial, on a neutral
// disc, so an agent never reads as a person of the page. `size` is the
// icon's: 14 in the 20 px disc, 10 in a 16 px one (a highlight's row).
export function AgentAvatar({ kind, title, size = 14 }) {
  const Icon = agentKindIcon(kind);
  return <span className="peerAvatar agentAvatar" data-agent={kind} title={title}><Icon size={size} /></span>;
}

// The avatar of whoever wrote or did something: an agent (`kind`, its kind:
// chat/agents.js agentKind's, "" for a person) by its kind's icon, else the
// person `name`. A comment's author (an agent's, the one that wrote it for
// them), an Inbox item's actor. `title`: who it is, whole ("Written by Codex
// on TIM-PC, for tim").
export function ActorAvatar({ name, kind = "", title }) {
  return kind ? <AgentAvatar kind={kind} title={title} /> : <PersonAvatar name={name || "?"} title={title} />;
}

// Chips on a block row: up to three avatars, then a "+n".
export function PeerChips({ peers }) {
  if (!peers?.length) return null;
  const shown = peers.slice(0, 3);
  return (
    <span className="peerChips" aria-hidden="true" data-guide="notes.peers">
      {shown.map((p) => <PeerAvatar key={p.client} peer={p} />)}
      {peers.length > shown.length ? <span className="peerMore">+{peers.length - shown.length}</span> : null}
    </span>
  );
}

// The carets of people editing a block whose editor we don't have open,
// drawn over its rendered view (`containerRef`, positioned). Placed by text
// (editor/clickToSource.js), since the rendered layout isn't the source's; a
// caret that can't be placed is left out. Re-placed when the text, a caret
// or the view's size changes (images loading, the window resizing).
export function RenderedCarets({ peers, source, containerRef }) {
  const [spots, setSpots] = useState([]);
  const key = peers.map((p) => `${p.client}:${p.head}:${p.name}`).join("|");
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const place = () => {
      const spans = scanMathSpans(source);
      setSpots(peers.map((p) => {
        const r = renderedCaretRect(el, source, p.head, spans);
        return r && { ...r, client: p.client, color: personColor(p.name), name: p.name || "Anonymous" };
      }).filter(Boolean));
    };
    place();
    const ro = new ResizeObserver(place);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, source]);
  return spots.map((s) => (
    <span key={s.client} className={`peerCaret peer-${s.color}`} data-name={s.name}
      data-markdown-copy-ignore="" aria-hidden="true"
      style={{ left: s.left, top: s.top, height: s.height }} />
  ));
}

// The header stack; clicking an avatar jumps to that person's block.
export function PresenceBar({ peers, onJump }) {
  if (!peers?.length) return null;
  return (
    <span className="presenceBar" aria-label={t("People on this page")} data-guide="page.presence">
      {peers.map((p) => (
        <PeerAvatar
          key={p.client}
          peer={p}
          title={`${describe(p)}${p.block ? " — click to jump" : ""}`}
          onClick={p.block && onJump ? () => onJump(p.block) : undefined}
        />
      ))}
    </span>
  );
}
