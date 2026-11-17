// The chat's queue above the composer (chat/chatQueue.js, docs/dev/ai.md
// "Queue and Steer"): one row a message sent while the reply runs, in the
// order they go. A row is its grip (drag it, or the arrow keys, to
// reorder: shared/ui/useDragOrder.js), its first picture, its text on one
// line, then Steer (hand it to the reply under way, where the agent takes
// it), or Send while nothing runs and the queue is paused; Delete; and ⋯
// with Edit (the text, in its row) and Move up / down. A message the reply
// is taking in reads "Steering…" and offers nothing: the agent has it.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDragOrder } from "../shared/ui/useDragOrder.js";
import { ContextMenu, MenuItem, useMenuTrigger } from "../shared/ui/Menus";
import { AutoGrowTextarea } from "../shared/ui/Widgets";
import { ArrowDownIcon, ArrowUpIcon, CornerDownRightIcon, GripIcon, ListEndIcon, MoreIcon, PencilIcon, TrashIcon } from "../shared/ui/Icons";
import { composing } from "../shared/lib/hotkeys.js";
import { sendsOnEnter } from "./enterKey.js";
import { pictureAlt } from "./chatPictures.js";
import { pictureSrc } from "./pictureSrc.js";
import { t } from "../shared/i18n/i18n.js";

// The row's text in its place: the message on one line, or the field
// editing it (Enter keeps, Escape leaves it as it was, leaving the field
// keeps too).
function RowText({ item, editing, onDone }) {
  const [draft, setDraft] = useState(item.text);
  const field = useRef(null);
  useEffect(() => {
    if (!editing) return;
    setDraft(item.text);
    const el = field.current;
    el?.focus();
    el?.setSelectionRange(el.value.length, el.value.length);
  }, [editing]);
  if (!editing) return <span className="chatQueueText" title={item.text}>{item.text}</span>;
  const keep = () => onDone(draft.trim() ? draft : item.text);
  return (
    <AutoGrowTextarea ref={field} className="chatQueueEdit" rows={1} value={draft} aria-label={t("Edit the queued message")}
      onChange={(e) => setDraft(e.target.value)} onBlur={keep}
      onKeyDown={(e) => {
        if (composing(e)) return;
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onDone(item.text); }
        else if (sendsOnEnter(e)) { e.preventDefault(); keep(); }
      }} />
  );
}

function RowMenu({ first, last, onEdit, onUp, onDown }) {
  const menus = useMenuTrigger({ side: "above" });
  const pick = (run) => () => { menus.close(); run(); };
  return (
    <>
      <button type="button" className="ctlBtn chatQueueBtn" title={t("More")} aria-label={t("More")} {...menus.trigger("row")}>
        <MoreIcon size={14} />
      </button>
      {menus.menu ? (
        <ContextMenu {...menus.menu}>
          <MenuItem icon={PencilIcon} onClick={pick(onEdit)}>{t("Edit")}</MenuItem>
          <MenuItem icon={ArrowUpIcon} disabled={first} onClick={pick(onUp)}>{t("Move up")}</MenuItem>
          <MenuItem icon={ArrowDownIcon} disabled={last} onClick={pick(onDown)}>{t("Move down")}</MenuItem>
        </ContextMenu>
      ) : null}
    </>
  );
}

// `items` the queue's (chatQueue.js); `steers(item)` whether Steer can hand
// it to the reply under way now (false while nothing runs, or the reply
// takes none in), `steerNote(item)` why not when a reply runs ("" when it
// can, or nothing runs); `paused` whether the queue waits after a reply
// that was stopped or failed, when a row offers Send instead of Steer.
// onSteer(id), onSend(id), onRemove(id), onEdit(id, text), onMove(id, to)
// as chatQueue.js takes them.
export default function ChatQueue({ items, paused, steers, steerNote, onSteer, onSend, onRemove, onEdit, onMove, setStatus }) {
  const listRef = useRef(null);
  const [editing, setEditing] = useState("");
  const [said, setSaid] = useState("");
  const move = (id, to) => {
    onMove(id, to);
    const from = items.findIndex((m) => m.id === id);
    const at = to > from ? to - 1 : to;
    setSaid(t("Queued message {n} of {count}", { n: at + 1, count: items.length }));
  };
  const { sourceProps, keyProps, drag } = useDragOrder({ containerRef: listRef, axis: "y", start: "handle", onMove: move, onStatus: setStatus });
  if (!items.length) return null;
  const dragged = drag ? items.find((m) => m.id === drag.id) : null;
  return (
    <div className="chatQueue">
      {paused ? <div className="chatQueueNote">{t("Paused: the reply did not finish")}</div> : null}
      <div className="chatQueueList" ref={listRef} role="list" aria-label={t("Queued messages")}>
      {items.map((item, i) => {
        const steering = item.state === "steering";
        const picture = item.parts?.images?.[0];
        const note = steering ? "" : steerNote(item);
        const open = editing === item.id;
        return (
          <div key={item.id} role="listitem" data-order-id={item.id}
            className={`chatQueueRow${steering ? " steering" : ""}${drag?.id === item.id ? " lifted" : ""}`}>
            <button type="button" className="chatQueueGrip" disabled={steering}
              aria-label={t("Move the queued message")} title={t("Drag to reorder, or use the arrow keys")}
              {...(steering ? {} : { ...sourceProps(item.id), ...keyProps(item.id) })}>
              <ListEndIcon size={14} className="chatQueueGlyph" /><GripIcon size={14} className="chatQueueGripGlyph" />
            </button>
            {picture ? <img className="chatQueueThumb" src={pictureSrc(picture)} alt={pictureAlt(picture)} /> : null}
            <RowText item={item} editing={open}
              onDone={(text) => { setEditing(""); if (text !== item.text) onEdit(item.id, text); }} />
            {open ? null : steering ? (
              <span className="chatQueueState" role="status" title={t("The assistant takes it after its current step")}>
                <span className="transferSpin inline" aria-hidden="true" />{t("Steering…")}
              </span>
            ) : (
              <>
                {paused ? (
                  <button type="button" className="uiBtn ghost sm chatQueueAction" onClick={() => onSend(item.id)}
                    title={t("Send this message now; the rest of the queue follows it")}>
                    <ArrowUpIcon size={14} />{t("Send")}
                  </button>
                ) : steers(item) || note ? (
                  <button type="button" className="uiBtn ghost sm chatQueueAction" disabled={!steers(item)} onClick={() => onSteer(item.id)}
                    title={note || t("Send it into the reply under way: the assistant takes it after its current step")}>
                    <CornerDownRightIcon size={14} />{t("Steer")}
                  </button>
                ) : null}
                <button type="button" className="ctlBtn chatQueueBtn" onClick={() => onRemove(item.id)}
                  title={t("Delete")} aria-label={t("Delete the queued message")}>
                  <TrashIcon size={14} />
                </button>
                <RowMenu first={i === 0 || items[i - 1].state === "steering"} last={i === items.length - 1}
                  onEdit={() => setEditing(item.id)} onUp={() => move(item.id, i - 1)} onDown={() => move(item.id, i + 2)} />
              </>
            )}
          </div>
        );
      })}
      {drag?.bar ? <span className="dropBar" style={drag.bar} aria-hidden="true" /> : null}
      </div>
      <span className="srOnly" aria-live="polite">{said}</span>
      {dragged ? createPortal(
        <div className="dragGhost chatQueueGhost" style={{ left: drag.x, top: drag.y }} aria-hidden="true">{dragged.text}</div>,
        document.body,
      ) : null}
    </div>
  );
}
