// Who else is on the page (collaboration/usePageCollab.js `peers`): the avatar stack in the page
// header, and the small chips on the block a person is on. Colours are the
// room's per-peer index (CSS --peer-N); an open editor shows as a full
// avatar, a mere viewer as a faded one.
import React, { useLayoutEffect, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { renderedCaretRect } from "../editor/clickToSource";
import { scanMathSpans } from "../editor/BlockCmEditor";

function initial(peer) {
  const name = (peer?.name || "").trim();
  return name && name !== "Anonymous" ? name[0].toUpperCase() : "?";
}

function describe(peer) {
  const what = peer.anchor >= 0 ? "editing" : peer.block ? t("on a block") : "viewing";
  // No account behind the peer: a share-link visitor under a chosen name.
  return `${peer.name || "Anonymous"}${peer.user ? "" : " (via link)"} · ${what}`;
}

export function PeerAvatar({ peer, onClick, title }) {
  return (
    <span
      className={`peerAvatar peer-${peer.color % 8}${peer.anchor >= 0 ? "" : " viewing"}`}
      title={title || describe(peer)}
      onClick={onClick}
      role={onClick ? "button" : undefined}
    >{initial(peer)}</span>
  );
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
  const key = peers.map((p) => `${p.client}:${p.head}:${p.color}:${p.name}`).join("|");
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const place = () => {
      const spans = scanMathSpans(source);
      setSpots(peers.map((p) => {
        const r = renderedCaretRect(el, source, p.head, spans);
        return r && { ...r, client: p.client, color: p.color % 8, name: p.name || "Anonymous" };
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
