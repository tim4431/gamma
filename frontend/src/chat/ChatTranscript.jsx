// The chat's transcript (docs/dev/ai.md "The chat window"): the messages of
// the conversation shown, whichever agent it is with. Gamma AI's are drawn
// as the chat saves them; a computer agent's come in the same shape
// (chat/computer/computerChat.js shownMessages), so one set of rows draws
// both: the person's message with what it carried, and the reply with its
// one steps pill, the cards it waits on, its text as it streams, what it
// changed, how it ended, and the foot under it. What a row offers comes
// from the handlers the dock passes: editing a sent message, reporting a
// reply, reverting a change, saving its papers are Gamma AI's, and a
// conversation with a computer's agent passes none of them.
import { useMemo, useState } from "react";
import { API, withWorkspace } from "../shared/lib/utils";
import { chipPicture, pictureAlt } from "./chatPictures.js";
import { pictureSrc } from "./pictureSrc.js";
import { AutoGrowTextarea, ChatMarkdown } from "../shared/ui/Widgets";
import { Chevron, Collapse, IconSwap } from "../shared/ui/MotionWidgets";
import ListMotion from "../shared/ui/ListMotion";
import FetchHandoffCards, { LiveHandoffCards } from "./FetchHandoffCards";
import ReplyPapers from "./ReplyPapers";
import StepsPill from "./StepsPill";
import ApprovalCard, { AskCard } from "./ApprovalCard";
import ChatErrorCard from "./ChatErrorCard";
import AgentChanges, { ACTION_ICONS, FileChanges, ThreadsOpened } from "./AgentChanges";
import { changeSteps, noteReview } from "./noteReview.js";
import { declinedSummary } from "./approvals.js";
import { cachedPercent, fmtTokens, liveUsage, usageDetail } from "./tokenUsage";
import { areaPicturesNote, canContinueAfterLimit, continueAfterLimitPrompt, coverageNote, limitNote, shortenedNote, trimmedNote,
  toolsOffNote, truncatedNote } from "./coverage.js";
import { failureCopy } from "./chatErrors";
import { chipNote, helperStatus, runningLabel, splitActions, stepsSummary } from "./agentSteps";
import { sendsOnEnter } from "./enterKey.js";
import { composing } from "../shared/lib/hotkeys.js";
import { openReport } from "../support/reportContent.js";
import { AlertCircleIcon, ArrowDownIcon, ArrowUpIcon, BookIcon, CheckIcon, ClockIcon, CopyIcon, FileIcon, FlagIcon, FolderIcon,
  InfoIcon, PencilIcon, QuoteIcon, XIcon, ZapIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

// What the model was given for a reply, per document — streamed by
// /api/ai/chat as its first line and saved on the message. Shown only when
// it matters: the paper or text file was truncated, or the PDF file was
// requested but text went instead (the provider refused it, or it was too
// large or missing). A notes-only page asks for no file. A full native
// attachment or a document that fit whole stays silent. The pill folds in
// what the reply's tools read on their own ("pages 1–2 of 7 · read 3–5 with
// tools", a text file's in lines) and which pages or lines nobody saw; a
// selection says where the server placed it
// ("p. 7 · Methods › Noise model") and whether a picture of it went along
// (its text layer looked like a formula or table). Four more pills, per
// reply: the oldest messages left out of a conversation the model can't
// hold any more, the selected passages and attached blocks that went short,
// a reply cut off at the output limit, and a reply a tool limit or the
// window stopped (chat/coverage.js). A reply whose model can't call tools
// says it went without them (`toolsOff`, `model` its model's name).
function ContextCoverage({ items, actions, tools, trimmed, shortened, truncated, limit, toolsOff, model }) {
  const [open, setOpen] = useState(-1); // the pill whose explanation is unfolded
  const [shown, setShown] = useState(-1); // the explanation under them, kept while it folds away
  const notes = (items || []).flatMap((c) => {
    const out = [];
    const note = coverageNote(c, { actions, tools });
    if (note) out.push(note);
    const areas = areaPicturesNote(c);
    if (areas) out.push(areas);
    const cropped = (c.selection?.passages || []).filter((p) => p.crop);
    if (cropped.length) {
      out.push({
        short: T("Picture of the selection sent"),
        long: t("The selected text looked like a formula or table (or wasn't in the extracted text), so the model also got a picture of that region of the page."),
        refused: false,
        // Drawn again by the server from the saved page + crop box — the
        // same picture the model got. Replies saved before the box was kept
        // have none to show.
        pictures: c.doc_id ? cropped.filter((p) => p.box?.length === 4).map((p) =>
          withWorkspace(`${API}/ai/selection-crop/${c.doc_id}?page=${p.page}&box=${p.box.join(",")}`)) : [],
      });
    }
    return out;
  });
  if (toolsOff) notes.push(toolsOffNote(toolsOff, model));
  for (const extra of [trimmedNote(trimmed), shortenedNote(shortened), truncatedNote(truncated), limitNote(limit)]) if (extra) notes.push(extra);
  if (!notes.length) return null;
  // Pills like the agent's steps: a click unfolds the explanation under them.
  const unfold = (i) => {
    setOpen(open === i ? -1 : i);
    if (open !== i) setShown(i);
  };
  return (
    <div className="chatCoverage">
      {notes.map((n, i) => (
        <button key={i} type="button" className="chatPill" aria-expanded={open === i}
          title={open === i ? undefined : n.long} onClick={() => unfold(i)}>
          {n.refused ? <AlertCircleIcon size={14} /> : <InfoIcon size={14} />}
          <span className="chatPillText">{n.short}</span>
          <Chevron open={open === i} dir="down" size={14} />
        </button>
      ))}
      <Collapse open={!!notes[open]} className="chatCoverageMore">
        <p className="chatCoverageNote">{notes[shown]?.long}</p>
        {notes[shown]?.pictures?.map((src) => (
          <img key={src} className="chatCoverageCrop" src={src} alt={t("Picture of the selection sent")} draggable={false} />
        ))}
      </Collapse>
    </div>
  );
}

// A failed request as the error card (ChatErrorCard), worded per its kind
// (message.errorKind, chat/chatErrors.js), the provider's own words folded
// under "Details"; a computer agent's turn says why in its computer's own
// sentence (`errorSaid`). A reply that broke off partway keeps its text and
// gets the compact card (no sentence) under it.
function ReplyErrorCard({ message, compact, actions }) {
  const copy = failureCopy(message.errorKind, { provider: message.errorProvider, auth: message.errorAuth, said: message.errorSaid });
  const fromProvider = !["network", "not_configured"].includes(message.errorKind);
  return (
    <ChatErrorCard headline={copy.headline} text={copy.text} actions={actions} compact={compact}
      detail={message.errorDetail || ""} detailLabel={fromProvider ? t("Details from the provider") : t("Details")} />
  );
}

// The agent's work in a reply (chat/agentSteps.js): one pill summing up its
// steps (StepsPill), which expands to every call's chip (arguments and
// output), and under it the changes — renamed or filed pages, edited or
// added notes — each old → new with a link to what changed. While the reply
// streams (`live`) the same pill says what is under way from the start:
// "Thinking" (or "Responding" once text arrives, the token count ticking
// beside it), the step running now, or what that step waits for, each new
// label morphing in over the last. `waiting`: what the running call waits
// for — the user's answer on an approval card, or a blocked paper from
// their browser — so nothing spins. `helpers`: the helpers the running call
// handed documents to, a row each under the pill, laid out as a chip: the
// document and what its helper is doing now. A reply that ends without a
// step folds its pill away; meanwhile it keeps its last words.
// `still`: a reply loaded or read again, not this tab's own: its label
// changes at once and is not read out. `pill`: what a computer agent's turn
// says of itself ({label, waiting, queued}: computerChat.js turnLabel), in
// place of Gamma AI's words.
function AgentSteps({ actions, live, still, running, waiting, helpers = [], usage, text, pill, open, onToggle, titleOf, children }) {
  const { failed, declined } = splitActions(actions);
  const done = !live && actions.length > 0;
  const label = pill ? pill.label
    : waiting === "paper" ? t("Waiting for the paper from your browser")
      : waiting ? t("Waiting for your approval")
        : running ? runningLabel(running, titleOf)
          : done ? stepsSummary(actions)
            : text ? t("Responding") : t("Thinking");
  return (
    <StepsPill label={label} live={live} waiting={pill ? pill.waiting : !!waiting} queued={!!pill?.queued} still={still} steps={actions.length}
      counts={{ failed, declined }} title={t("Show every step with its arguments and output")} open={open} onToggle={onToggle}
      meter={<UsageLine usage={usage} className="chatMsgUsage live" />}
      under={live && helpers.length ? (
        <div className="chatHelpers" role="status">
          {helpers.map((h) => (
            <div key={h.id} className={`chatToolActionHead plain${h.state === "failed" ? " err" : ""}`} title={h.label}>
              {h.state === "done" ? <CheckIcon size={14} /> : h.state === "failed" ? <XIcon size={14} />
                : <span className="transferSpin inline" aria-hidden="true" />}
              <span>{h.label}</span>
              <span className="chatToolActionNote">{helperStatus(h)}</span>
            </div>
          ))}
        </div>
      ) : null}>
      {children}
    </StepsPill>
  );
}

// The token line under a reply, Claude Code style: prompt in, reply out,
// and the share of the prompt the provider served from its cache. Every
// count comes from the provider's own report ({"usage"} lines of the chat
// stream, summed over an agent reply's rounds); a reply without one shows
// nothing. While the reply streams the same line ticks up next to the
// "Responding" pill: exact counts for the rounds already reported, a "~"
// estimate from the characters received for the one still arriving.
export function UsageLine({ usage, className = "chatMsgUsage" }) {
  if (!usage || !(usage.input || usage.output)) return null;
  const cached = cachedPercent(usage);
  const live = !!usage.estimate;
  return (
    <span className={className} title={live ? t("Counting while the reply streams — the provider's own count replaces the estimate when it finishes") : usageDetail(usage)}>
      {usage.input ? <span className="chatMsgUsagePart"><ArrowUpIcon size={9} />{fmtTokens(usage.input)}</span> : null}
      <span className="chatMsgUsagePart"><ArrowDownIcon size={9} />{live ? "~" : ""}{fmtTokens(usage.output)}</span>
      {cached && !live ? <span className="chatMsgUsagePart">{cached}{t("% cached")}</span> : null}
    </span>
  );
}

// The speed tier a call asked for rides on the model chip as a glyph rather
// than a word — a bolt for the provider's fast route, a clock for its slow,
// cheap one — so the chip stays short. Nothing for its usual routing, and
// the tooltip (`knobsLabel`) names the tier the glyph stands for.
const SPEED_ICONS = { fast: ZapIcon, flex: ClockIcon };
export function SpeedGlyph({ speed, size = 13 }) {
  const Icon = SPEED_ICONS[speed];
  return Icon ? <Icon size={size} className="chatSpeedGlyph" aria-hidden="true" /> : null;
}
export const knobsLabel = (speed) => (speed
  ? t("Model, reasoning effort and speed ({speed})", { speed })
  : t("Model, reasoning effort and speed"));

const toolCallText = (a) => {
  const args = Object.entries(a.args || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
  const head = `${a.tool || a.kind}(${args})`;
  return [head, a.result].filter(Boolean).join("\n\n");
};

// What a tool chip unfolds to: the calls a helper made for it, the picture
// it looked at, and the raw call with its output. A component of its own,
// so none of it is built while the chip is shut.
function ToolCallDetail({ action: a }) {
  const picture = chipPicture(a);
  return (
    <>
      {a.children?.length ? (
        // What a helper did for this call: its own calls, without their output.
        <div className="chatToolChildren">
          {a.children.map((child, k) => {
            const ChildIcon = ACTION_ICONS[child.kind] || FolderIcon;
            return (
              <div key={k} className={`chatToolActionHead plain${child.error ? " err" : ""}`} title={child.summary}>
                <ChildIcon size={14} />
                <span>{child.summary}</span>
                {chipNote(child) ? <span className="chatToolActionNote">{chipNote(child)}</span> : null}
              </div>
            );
          })}
        </div>
      ) : null}
      {picture ? (
        // What the assistant looked at or clipped, drawn again by the server.
        <img className="chatToolPicture" src={pictureSrc(picture)} alt={a.summary} loading="lazy" />
      ) : null}
      <pre className="chatToolDetail">{toolCallText(a)}</pre>
    </>
  );
}

// A sent message edited in place (Gamma AI's): Send goes again from it,
// replacing it and everything after it.
function EditBubble({ i, editing, onEdit, onResend, replying }) {
  const resend = () => { const text = editing.text; onEdit(null); onResend(i, text); };
  return (
    <div className="chatBubbleRow user" data-msg-idx={i} data-motion-key={i}>
      <div className="chatMsgCol">
        <div className="chatBubble user chatEditBubble">
          <AutoGrowTextarea
            autoFocus
            className="chatEditTextarea"
            value={editing.text}
            onChange={(e) => onEdit({ idx: i, text: e.target.value })}
            onKeyDown={(e) => {
              if (composing(e)) return;
              if (sendsOnEnter(e)) {
                e.preventDefault();
                resend();
              } else if (e.key === "Escape") { e.preventDefault(); onEdit(null); }
            }}
          />
          <div className="chatEditBtns">
            <button type="button" className="uiBtn sm" onClick={() => onEdit(null)}>{t("Cancel")}</button>
            <button type="button" className="uiBtn sm chatEditSend"
              disabled={!editing.text.trim() || replying}
              onClick={resend}
              title={t("Re-send — replaces this message and everything after it")}>{t("Send")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// One message's row. A reply that had ended when its row was first drawn
// is still, as one loaded is (`still`): its pill folds and relabels at once
// and is not read out.
function MessageRow({ m, i, last, responding, still, findHit, props }) {
  const {
    replying, titleOf, onOpenPage, openActions, toggleAction, onEdit, copiedIdx, onCopy, canReport, approvals, onAnswer,
    errorActions, changes, handoffs, papers,
  } = props;
  const [settled] = useState(!responding);
  const isUser = m.role === "user";
  return (
    <div className={`chatBubbleRow ${isUser ? "user" : "ai"}${findHit ? " findHit" : ""}`} data-msg-idx={i} data-motion-key={i}>
      <div className="chatMsgCol">
        <div className={`chatBubble ${isUser ? "user" : "ai"}${m.error && !m.errorKind ? " error" : ""}`}>
          {m.notice ? <div className="chatNotice">{m.notice}</div> : null}
          {m.images?.length ? (
            <div className="chatMsgImages">
              {m.images.map((p, j) => <img key={j} src={pictureSrc(p)} className="chatMsgImage" alt={pictureAlt(p)} title={pictureAlt(p)} loading="lazy" />)}
            </div>
          ) : null}
          {m.pdfs?.length || m.passages?.length ? (
            <div className="chatMsgPdfs">
              {(m.pdfs || []).map((n, j) => (
                <span key={j} className="chatPdfChip" title={n}>
                  <FileIcon size={14} />
                  {n.slice(0, 40)}{n.length > 40 ? "…" : ""}
                </span>
              ))}
              {(m.passages || []).map((s, j) => (
                <span key={`q${j}`} className="chatPdfChip" title={s.text}>
                  <QuoteIcon size={14} />
                  {s.page ? t("p. {page}", { page: s.page }) : t("PDF passage")}
                </span>
              ))}
            </div>
          ) : null}
          {!isUser && (m.context?.length || m.trimmed || m.shortened || m.truncated || m.limit || m.toolsOff) ? (
            <ContextCoverage items={m.context} actions={m.actions} tools={!!m.tools}
              trimmed={m.trimmed} shortened={m.shortened} truncated={m.truncated} limit={m.limit}
              toolsOff={m.toolsOff} model={m.model} />
          ) : null}
          {/* The reply's one status pill, from the start of the turn; a
              reply that ends without a step folds it away. */}
          {!isUser ? (
            <AgentSteps actions={m.actions || []} live={responding} still={still || settled} running={responding ? m.step : null}
              helpers={m.helpers} usage={responding ? liveUsage(m.usage, m.live) : null} text={m.text} pill={m.pill}
              waiting={!responding ? ""
                : m.approval && !approvals.answered.has(m.approval.id) ? "approval"
                  : m.handoffs?.length ? "paper" : ""}
              open={openActions.has(`${i}:steps`)} onToggle={() => toggleAction(`${i}:steps`)}
              titleOf={titleOf}>
              <div className="chatToolActions">
                {(m.actions || []).map((a, j) => {
                  const Icon = ACTION_ICONS[a.kind] || FolderIcon;
                  // Chats saved before tool output was recorded, and a
                  // computer agent's steps, have no raw call: their chips
                  // stay plain text.
                  const hasDetail = !!(a.tool || a.result);
                  const key = `${i}:${j}`;
                  const open = openActions.has(key);
                  return (
                    <div key={j} className={`chatToolAction${a.error ? " err" : ""}`}>
                      {hasDetail ? (
                        <button type="button" className="chatToolActionHead"
                          onClick={() => toggleAction(key)}
                          title={open ? t("Hide tool output") : t("Show tool output")}>
                          <Icon size={14} />
                          <span>{a.declined ? declinedSummary(a, titleOf) : a.summary}</span>
                          {chipNote(a) ? <span className="chatToolActionNote">{chipNote(a)}</span> : null}
                          <Chevron open={open} dir="down" size={10} />
                        </button>
                      ) : (
                        <div className="chatToolActionHead plain" title={a.summary}>
                          <Icon size={14} />
                          <span>{a.summary}</span>
                        </div>
                      )}
                      {hasDetail ? <Collapse open={open} className="chatToolMore"><ToolCallDetail action={a} /></Collapse> : null}
                    </div>
                  );
                })}
              </div>
            </AgentSteps>
          ) : null}
          {!isUser && m.actions?.length ? (
            <AgentChanges actions={m.actions} onOpenPage={onOpenPage} busy={replying}
              onReverted={changes.onReverted && m.id ? (indexes, answer, reverted) => changes.onReverted(m.id, indexes, answer, reverted) : undefined}
              onRevertMany={changes.onReverted && m.id && changes.many ? () => changes.onRevertMany(m.id) : undefined}
              onShowChange={m.id ? (k) => noteReview.show(changeSteps(changes.messages, m.id, k, changes.key)) : undefined} />
          ) : null}
          {isUser && m.contextPages?.length ? <div className="chatMsgPdfs">
            {m.contextPages.map((p) => <button type="button" key={p.id} className="crumbBtn" title={p.title} onClick={() => onOpenPage?.(p.id)}><BookIcon size={14} /><span className="linkChipText">{p.title}</span></button>)}
          </div> : null}
          {isUser
            ? <div className="chatUserText">{m.text}</div>
            : m.text && !(m.error && m.errorKind) ? <ChatMarkdown text={m.text} copyBlocks /> : null}
          {responding && m.approval ? (
            <ApprovalCard key={m.approval.id} approval={m.approval} kindLabel={approvals.kindLabel}
              titleOf={titleOf} onDecide={approvals.onDecide} answered={approvals.answered.get(m.approval.id) || ""} />
          ) : null}
          {responding ? (m.asks || []).map((ask) => (
            <AskCard key={ask.id} ask={ask} label={m.label} onAnswer={(decision, note) => onAnswer(m, ask, decision, note)} />
          )) : null}
          {responding && m.handoffs?.length ? (
            <LiveHandoffCards handoffs={m.handoffs} readOnly={handoffs.readOnly}
              autoOpen={handoffs.autoOpen} onNeverWait={handoffs.onNeverWait} />
          ) : null}
          {!isUser && !responding && m.actions?.some((a) => a.handoff) ? (
            <FetchHandoffCards actions={m.actions} isLast={i === last}
              busy={replying} draft={handoffs.draft} readOnly={handoffs.readOnly}
              autoOpen={handoffs.autoOpen} onContinue={handoffs.onContinue} />
          ) : null}
          {!isUser && !responding && canContinueAfterLimit({ limit: m.limit, truncated: m.truncated,
            isLast: i === last, idle: !replying && !handoffs.draft, readOnly: handoffs.readOnly }) ? (
            <div className="chatLimitActions">
              <button type="button" className="uiBtn sm primary"
                title={t("Carry on where the reply stopped, in a new message with a fresh budget")}
                onClick={() => handoffs.onContinue(continueAfterLimitPrompt())}>{t("Continue")}</button>
            </div>
          ) : null}
          {!isUser && m.files ? <FileChanges files={m.files} /> : null}
          {!isUser && m.threads?.length ? <ThreadsOpened threads={m.threads} /> : null}
          {!isUser && !responding && papers && !m.error ? (
            <ReplyPapers actions={m.actions} text={m.text} {...papers} />
          ) : null}
          {!isUser && m.errorKind && !responding ? (
            <ReplyErrorCard message={m} compact={!m.error} actions={errorActions(m, i)} />
          ) : null}
        </div>
        {!responding ? <div className="chatMsgFoot">
          {!isUser && m.model ? (
            <span className="chatMsgModel" title={knobsLabel(m.speed)}>
              {[m.model, m.effort].filter(Boolean).join(" · ")}
              <SpeedGlyph speed={m.speed} size={12} />
            </span>
          ) : null}
          {!isUser ? <UsageLine usage={m.usage} /> : null}
          <div className="chatMsgActions">
          <button type="button" className="chatMsgActionBtn" title={t("Copy message")}
            onClick={() => onCopy(i, m.text)}>
            <IconSwap swap={copiedIdx === i} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
          </button>
          {canReport && !isUser && m.text && !(m.error && m.errorKind) ? (
            // Report: offensive or harmful output, to the people who run the server and Gamma's developer
            <button type="button" className="chatMsgActionBtn chatMsgReport" title={t("Report this reply")}
              aria-label={t("Report this reply")}
              onClick={() => openReport({ kind: "ai", text: m.text, model: m.model || "" })}>
              <FlagIcon size={14} />
            </button>
          ) : null}
          {onEdit && isUser && !replying ? (
            <button type="button" className="chatMsgActionBtn" title={t("Edit and re-send (removes later messages)")}
              onClick={() => onEdit({ idx: i, text: m.text })}>
              <PencilIcon size={14} />
            </button>
          ) : null}
          </div>
        </div> : null}
      </div>
    </div>
  );
}

// `messages` the rows (Gamma AI's with the reply's placeholder while one is
// on its way, or a computer agent's conversation, shownMessages), `scope`
// what they are of (a switch is another list: nothing of the one before
// moves into it), `replying` while the last one is under way, `still` when
// they were loaded, not this tab's own, `findHit` the row Find is on (-1:
// none). The rest are what the rows offer:
//   titleOf(pageId)          a page a step names; onOpenPage(id) opens one
//   openActions, toggleAction  the chips unfolded ("<row>:<chip>", "<row>:steps")
//   editing, onEdit, onResend  a sent message edited and sent again (none:
//                            no Edit)
//   copiedIdx, onCopy        Copy, and the row that was just copied
//   canReport                whether a reply can be reported
//   approvals                Gamma AI's approval cards: {answered, kindLabel,
//                            onDecide}
//   onAnswer(m, ask, decision, note)  a computer agent's asks answered
//   errorActions(m, i)       an error card's buttons
//   changes                  Gamma AI's changes under a reply: {onReverted
//                            (null where nothing is reverted: a viewer),
//                            onRevertMany, many (whether the conversation has
//                            several to revert), messages, key}
//   handoffs                 the fetch handoffs' and the limit's cards:
//                            {readOnly, autoOpen, onNeverWait, onContinue,
//                            draft}
//   papers                   Save to library's props; null where nothing is
//                            saved
export default function ChatTranscript({ messages, scope, replying, still, findHit = -1, editing = null, onResend, ...props }) {
  // The messages' keys for ListMotion: their places, as React keys them (a
  // reply takes its placeholder's place, so it does not come in twice).
  const motionKeys = useMemo(() => messages.map((_, i) => String(i)), [messages.length]);
  const last = messages.length - 1;
  const all = { ...props, replying };
  return (
    // A message sent or answered rises into place; a conversation loaded or
    // switched to shows still (its messages are the ones just loaded). Rows
    // are keyed by their conversation as well as their place, so a switch
    // mounts its own: nothing of the one before opens, folds or morphs
    // into it.
    <ListMotion scope={scope} keys={motionKeys} enter="rise" moves={false} still={still}>
      {messages.map((m, i) => (editing?.idx === i
        ? <EditBubble key={`${scope}:${i}`} i={i} editing={editing} onEdit={props.onEdit} onResend={onResend} replying={replying} />
        : <MessageRow key={`${scope}:${i}`} m={m} i={i} last={last} still={still} findHit={findHit === i} props={all}
            responding={replying && m.role !== "user" && !!m.partial && i === last} />))}
    </ListMotion>
  );
}
