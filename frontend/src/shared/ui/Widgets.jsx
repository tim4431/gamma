// Shared presentational widgets: dockable windows, chat markdown and its
// links, and the auto-growing textarea. The tab strip is workspace/OpenTabs.jsx.
import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { textOf } from "../lib/textOf";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { CheckIcon, CopyIcon, ExternalLinkIcon, FileTextIcon, MoreIcon, XIcon } from "./Icons";
import { ContextMenu, MenuItem, MenuLabel, SubMenuItem, useMenuTrigger } from "./Menus";
import { LinkHover, describeLink } from "./LinkCard.jsx";
import { doublePress } from "./press.js";
import { assetUrl, copyText } from "../lib/utils";
import { citesSeveralSources, isSourceLink, parseGammaLink } from "../model/gammaLinks.js";
import { remarkPaperLinks } from "../lib/remarkPaperLinks.js";
import { diagramFence, normalizeChatMarkdown, remarkDiagrams, remarkLoneDisplayMath } from "../lib/diagramMarkdown.js";
import { DiagramFigure, diagramCodeProps } from "./DiagramFigure";
import { highlightCode } from "../../editor/codeHighlight.js";
import { t } from "../../shared/i18n/i18n.js";
import { IconSwap, MorphText } from "./MotionWidgets";

// Shared chrome for every dockable window: one grip (drag to move or
// reorder, double press to fold), the close button right beside it, then
// the window's own controls. Notes and chat both use this so their behavior
// can't drift apart. Without `onGrip` (the compact shell has no drag and no
// fold) the title is a plain label.
//
// Folded (`collapsed`), a window is its header alone: its slot shows the
// header as a bar (`DockBar`), and one press on the bar anywhere but a
// button opens it. The window itself keeps its body mounted, out of sight
// and inert, where it stood: a fold changes no state inside it (the chat's
// conversation and its place, the notes' scroll, a half-typed message).
//
// When the click that opened a folded window was the first of a double
// click, the second lands on the opened grip: that one does not fold it.
// A finger's double tap folds on its second release, and that tap's click
// comes after, onto the bar where the grip was: it does not open it again.
let unfoldedAt = -Infinity;
let foldedAt = -Infinity;
const UNFOLD_ECHO_MS = 800;
const FOLD_ECHO_MS = 350;

// The window's actions (`actions`), its icon buttons after its own controls
// (`headerContent`): each {id, icon, label, title, onClick, disabled,
// danger, guide, props (more of the button's props)}, with `glyph` for
// something drawn in the header in place of the icon (the chat's context
// ring). A toggle has `on`. One with `popover` ({name, content}) opens the
// popover App's openPopover names, and is `open` while it is; `items`
// ([{icon, label, title, onClick, disabled}]) make it a menu and `choice`
// ({value, options: [[value, label]], onChange}) a choice of one, either
// opened from the button. The header keeps them inside the window: the
// grip's title gives way first, and the actions that still do not fit fold
// from the end (one with a `keep` rank after those with a lower one) into
// a "⋯" whose menu holds them, a toggle as a checkbox row, a menu or a
// choice as a flyout. A popover drops from the header's right edge whether
// its button shows or is folded.

// How many of the header's `n` actions fold (useHeaderFold): all of them
// fit, or as many as fit beside the "⋯". The room is the header's less
// what else stands in it at its narrowest (the grip's min-width), so it is
// the same however many are folded, and a fold never undoes itself.
function useHeaderFold(n) {
  const headerRef = useRef(null);
  const rowRef = useRef(null);
  const [folded, setFolded] = useState(0);
  const fit = useCallback(() => {
    const header = headerRef.current, row = rowRef.current;
    const button = row?.firstElementChild;
    if (!n || !header || !button) return;
    const grip = header.querySelector(".dockGrip");
    const spacer = header.querySelector(".dockHeaderSpacer");
    const end = header.getBoundingClientRect().right - parseFloat(getComputedStyle(header).paddingRight);
    const box = row.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
    const room = box.width + spacer.getBoundingClientRect().width - Math.max(0, box.right - end)
      + Math.max(0, grip.getBoundingClientRect().width - parseFloat(getComputedStyle(grip).minWidth));
    const fits = Math.floor((room + gap + 0.5) / (button.getBoundingClientRect().width + gap));
    setFolded(fits >= n ? 0 : n - Math.max(0, fits - 1));
  }, [n]);
  // Again after each render (what else the header holds may have changed)
  // and whenever the header, its title or its free room changes size.
  useLayoutEffect(fit);
  useLayoutEffect(() => {
    const header = headerRef.current;
    if (!n || !header || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(fit);
    for (const el of [header, header.querySelector(".dockGrip"), header.querySelector(".dockHeaderSpacer")]) observer.observe(el);
    return () => observer.disconnect();
  }, [n, fit]);
  return [n ? folded : 0, headerRef, rowRef];
}

// An action's name: a choice's says what is chosen ("All comments — Notify me").
function actionName(a) {
  if (!a.choice) return a.label;
  const current = a.choice.options.find(([value]) => value === a.choice.value);
  return current ? `${current[1]} — ${a.label}` : a.label;
}

// A choice's rows: one of a set, the chosen one checked.
function choiceRows(choice, done) {
  return choice.options.map(([value, label]) => (
    <MenuItem key={value} role="menuitemradio" aria-checked={value === choice.value}
      trailing={value === choice.value ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
      onClick={() => { done(); choice.onChange(value); }}>{label}</MenuItem>
  ));
}

function itemRows(items, done) {
  return items.map((it) => (
    <MenuItem key={it.label} icon={it.icon} title={it.title} disabled={it.disabled}
      onClick={() => { done(); it.onClick(); }}>{it.label}</MenuItem>
  ));
}

function DockHeader({ title, guide, onGrip, onFold, onClose, headerContent, actions = [], collapsed }) {
  const shown = collapsed ? [] : actions;
  const [folded, headerRef, rowRef] = useHeaderFold(shown.length);
  const { kind, trigger, menu, close } = useMenuTrigger();
  // The fold takes from the end, a higher `keep` later.
  const order = [...shown].reverse().sort((a, b) => (a.keep || 0) - (b.keep || 0));
  const away = new Set(order.slice(0, folded));
  // A popover hangs under the header (.dockHeaderPop): a window low in its
  // column leaves it only the room from there to the window's foot, which
  // --dock-pop-room hands its max-height (app.css), measured before it
  // paints and again as the window resizes, so its last rows stay in reach.
  const popOpen = shown.some((a) => a.popover && a.open);
  useLayoutEffect(() => {
    const el = headerRef.current;
    if (!popOpen || !el) return undefined;
    const room = () => el.style.setProperty("--dock-pop-room", `${Math.max(160, window.innerHeight - el.getBoundingClientRect().bottom - 16)}px`);
    room();
    window.addEventListener("resize", room);
    return () => window.removeEventListener("resize", room);
  }, [popOpen, headerRef]);
  const unfold = collapsed && onFold ? (e) => {
    if (e.target.closest("button") || e.timeStamp - foldedAt < FOLD_ECHO_MS) return;
    unfoldedAt = e.timeStamp;
    onFold();
  } : undefined;
  const fold = onFold && ((e) => {
    if (e.timeStamp - unfoldedAt <= UNFOLD_ECHO_MS) return;
    foldedAt = e.timeStamp;
    onFold();
  });
  const button = (a) => {
    const name = actionName(a);
    return (
      <button key={a.id} type="button" className={`ctlBtn${a.on || a.open || kind === a.id ? " modeActive" : ""}${a.danger ? " danger" : ""}`}
        data-guide={a.guide} data-popover={a.popover?.name} disabled={a.disabled} title={a.title || name} aria-label={name}
        aria-pressed={a.on} aria-expanded={a.open} {...a.props}
        {...(a.items || a.choice ? trigger(a.id) : { onClick: a.onClick })}>
        {a.glyph || <a.icon size={16} />}
      </button>
    );
  };
  const foldRow = (a) => {
    if (a.items || a.choice) {
      return (
        <SubMenuItem key={a.id} id={a.id} icon={a.icon} label={a.label} title={a.title} disabled={a.disabled}>
          {a.items ? itemRows(a.items, close) : choiceRows(a.choice, close)}
        </SubMenuItem>
      );
    }
    const toggle = a.on === undefined ? {} : { role: "menuitemcheckbox", "aria-checked": a.on };
    return (
      <MenuItem key={a.id} icon={a.icon} title={a.title} danger={a.danger} disabled={a.disabled} {...toggle}
        trailing={a.on ? <CheckIcon size={14} className="ctxMenuCheck" /> : undefined}
        onClick={(e) => { close(); a.onClick(e); }}>{a.label}</MenuItem>
    );
  };
  const open = shown.find((a) => a.id === kind);
  return (
    <div className="dockWindowHeader" ref={headerRef} onClick={unfold}>
      <span
        className="dockGrip"
        data-guide={guide}
        onPointerDown={onGrip}
        {...doublePress(fold)}
        title={onGrip ? (collapsed ? t("Click to expand · drag to move this window") : t("Drag to move this window · double-click to collapse")) : undefined}
      ><span className="dockGripTitle">{onGrip ? "⠿ " : null}{title}</span></span>
      {onClose ? (
        <button className="uiClose" onClick={onClose} title={t("Close window (reopen from the View menu)")} aria-label={t("Close {title}", { title: title })}><XIcon size={14} /></button>
      ) : null}
      <span className="dockHeaderSpacer" />
      {collapsed ? null : headerContent}
      {shown.length ? (
        <div className="ctlBtnRow dockHeaderActions" ref={rowRef}>
          {shown.filter((a) => !away.has(a)).map(button)}
          {away.size ? (
            <button type="button" className={`ctlBtn${kind === "fold" || order.some((a) => away.has(a) && a.open) ? " modeActive" : ""}`}
              title={t("More")} aria-label={t("More")} {...trigger("fold")}>
              <MoreIcon size={16} />
            </button>
          ) : null}
        </div>
      ) : null}
      {menu && kind === "fold" ? (
        <ContextMenu {...menu}>{shown.filter((a) => away.has(a)).map(foldRow)}</ContextMenu>
      ) : menu && open ? (
        <ContextMenu {...menu}>
          {open.items ? itemRows(open.items, close) : <>
            <MenuLabel>{open.label}</MenuLabel>
            {choiceRows(open.choice, close)}
          </>}
        </ContextMenu>
      ) : null}
      {shown.filter((a) => a.popover).map((a) => (
        <span key={a.id} className="dockHeaderPop" data-popover={a.popover.name}>{a.popover.content}</span>
      ))}
    </div>
  );
}

// Whether the dock window around is folded (`useDockFolded`). What it holds
// stays mounted but can take neither the focus nor a press, so what would
// (a note's editor, and its editing bar outside the window) waits for the
// window to open, as it did when a fold unmounted it.
const DockFolded = createContext(false);
const useDockFolded = () => useContext(DockFolded);

function DockWindow({ title, guide, onGrip, onFold, onClose, headerContent, actions, collapsed, children }) {
  return (
    <div className={`dockWindow${collapsed ? " dockWindowFolded" : ""}`} inert={!!collapsed}>
      {collapsed ? null : (
        <DockHeader title={title} guide={guide} onGrip={onGrip} onFold={onFold} onClose={onClose} headerContent={headerContent} actions={actions} />
      )}
      <div className="dockWindowBody"><DockFolded.Provider value={!!collapsed}>{children}</DockFolded.Provider></div>
    </div>
  );
}

// A folded window's bar: its header, in its slot beside the open windows.
function DockBar({ title, guide, onGrip, onFold, onClose }) {
  return (
    <div className="dockWindow collapsed">
      <DockHeader title={title} guide={guide} onGrip={onGrip} onFold={onFold} onClose={onClose} collapsed />
    </div>
  );
}

// Markdown + KaTeX rendering for AI chat messages. Unlike block rendering this
// deliberately omits rehypeRaw: model output is untrusted, so raw HTML stays inert.
// Models often emit \( \) / \[ \] LaTeX delimiters, which remark-math doesn't
// recognize — normalize them to $ / $$ so math always renders.
// Memoized: the chat input re-renders all of ChatDock on every keystroke,
// and without the memo each keypress re-ran ReactMarkdown + KaTeX over every
// AI message in the conversation — visible typing lag on long chats.
// Selecting rendered chat/note text and hitting Ctrl+C would copy the plain
// rendered characters — **bold**, *italics*, `code`, links, and list markers
// all vanish, and KaTeX's double DOM (hidden MathML + visual layer) duplicates
// every symbol. Instead, serialize the copied selection back to markdown
// source for text/plain (paste into a note or any editor keeps the
// formatting), and keep the formatted fragment as text/html for rich targets
// like Word. Formulas become their LaTeX source (KaTeX keeps it in an
// <annotation encoding="application/x-tex">).
function fragmentToMarkdown(node, ctx = {}) {
  if (node.nodeType === Node.TEXT_NODE) {
    return ctx.pre ? node.textContent : node.textContent.replace(/\s+/g, " ");
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node;
  if (el.hasAttribute("data-markdown-copy-ignore")) return "";
  if (el.hasAttribute("data-diagram-source")) return `\n\n${diagramFence(el.dataset.diagramKind, el.dataset.diagramSource)}\n\n`;
  // An @ mention chip copies as its text ("@2026-10-09"), not its label ("@Today").
  if (el.hasAttribute("data-mention")) return el.getAttribute("data-mention");
  if (el.classList.contains("katex-display")) {
    const tex = el.querySelector('annotation[encoding="application/x-tex"]')?.textContent;
    return tex != null ? `\n\n$$\n${tex.trim()}\n$$\n\n` : el.textContent;
  }
  if (el.classList.contains("katex")) {
    const tex = el.querySelector('annotation[encoding="application/x-tex"]')?.textContent;
    return tex != null ? `$${tex.trim()}$` : el.textContent;
  }
  const kids = (c = ctx) => Array.from(el.childNodes).map((n) => fragmentToMarkdown(n, c)).join("");
  // Inline markers hug the text: whitespace at the edges of <strong>bold </strong>
  // must stay outside the ** or the markdown doesn't parse back.
  const wrap = (marker, close = marker) => {
    const raw = kids();
    const inner = raw.trim();
    return inner ? `${raw.match(/^\s*/)[0]}${marker}${inner}${close}${raw.match(/\s*$/)[0]}` : raw;
  };
  switch (el.tagName) {
    case "STRONG": case "B": return wrap("**");
    case "EM": case "I": return wrap("*");
    case "DEL": case "S": return wrap("~~");
    // A note's underline is the tag itself (editor/mdMarks.js).
    case "U": return wrap("<u>", "</u>");
    case "CODE": return ctx.pre ? el.textContent : `\`${el.textContent}\``;
    case "PRE": return `\n\n\`\`\`\n${el.textContent.replace(/\n$/, "")}\n\`\`\`\n\n`;
    case "A": {
      const href = el.getAttribute("href") || "";
      if (href.startsWith("blockref:")) return `[[${href.slice(9)}]]`; // note ref chips round-trip
      const inner = kids().trim();
      return href ? `[${inner || href}](${href})` : inner;
    }
    case "IMG": return `![${el.getAttribute("alt") || ""}](${el.getAttribute("src") || ""})`;
    case "BR": return "\n";
    case "HR": return "\n\n---\n\n";
    // A heading or a list item the selection only touches (a triple click
    // ends it at the start of the next block) copies as nothing, not as a
    // bare "## " or "- ".
    case "H1": case "H2": case "H3": case "H4": case "H5": case "H6": {
      const inner = kids().trim();
      return inner ? `\n\n${"#".repeat(Number(el.tagName[1]))} ${inner}\n\n` : "";
    }
    case "UL": case "OL": {
      const indent = "  ".repeat(ctx.listDepth || 0);
      const items = Array.from(el.children).filter((c) => c.tagName === "LI").map((li, i) => {
        const marker = el.tagName === "OL" ? `${(Number(el.getAttribute("start")) || 1) + i}. ` : "- ";
        const inner = Array.from(li.childNodes)
          .map((n) => fragmentToMarkdown(n, { ...ctx, listDepth: (ctx.listDepth || 0) + 1 }))
          .join("").replace(/^\n+|\n+$/g, "");
        return inner.trim() ? `${indent}${marker}${inner.replace(/\n+/g, "\n").replace(/\n/g, `\n${indent}  `)}` : "";
      }).filter(Boolean);
      return items.length ? `\n\n${items.join("\n")}\n\n` : "";
    }
    case "BLOCKQUOTE":
      return `\n\n${kids().trim().split("\n").map((l) => `> ${l}`).join("\n")}\n\n`;
    case "TABLE": {
      const rows = Array.from(el.querySelectorAll("tr")).map((tr) =>
        `| ${Array.from(tr.children).map((td) => kidsOf(td).trim().replace(/\|/g, "\\|") || " ").join(" | ")} |`);
      function kidsOf(td) { return Array.from(td.childNodes).map((n) => fragmentToMarkdown(n, ctx)).join(""); }
      if (!rows.length) return "";
      const cols = el.querySelector("tr")?.children.length || 1;
      rows.splice(1, 0, `| ${Array(cols).fill("---").join(" | ")} |`);
      return `\n\n${rows.join("\n")}\n\n`;
    }
    case "P": case "DIV": case "LI": return `\n\n${kids().trim()}\n\n`;
    default: return kids();
  }
}

// A selection of rendered Markdown (`from`: a Selection, the document's by
// default, or one Range) as a copy puts it on the clipboard: `md` its
// Markdown source (text/plain), `html` the fragment without what is marked
// data-markdown-copy-ignore (text/html). Both "" for an empty selection.
export function selectionMarkdown(from = window.getSelection()) {
  const ranges = !from ? [] : typeof from.cloneContents === "function" ? [from]
    : from.isCollapsed ? [] : Array.from({ length: from.rangeCount }, (_, i) => from.getRangeAt(i));
  if (!ranges.length || ranges.every((r) => r.collapsed)) return { md: "", html: "" };
  const holder = document.createElement("div");
  for (const range of ranges) holder.appendChild(range.cloneContents());
  const md = Array.from(holder.childNodes).map((n) => fragmentToMarkdown(n))
    .join("").replace(/\n{3,}/g, "\n\n").trim();
  holder.querySelectorAll("[data-markdown-copy-ignore]").forEach((el) => el.remove());
  return { md, html: holder.innerHTML };
}

function handleMarkdownCopy(e) {
  // A copy something inside already made stays: a synced block's editor puts
  // its source on the clipboard (and remembers it, editor/sourceCopy.js).
  if (e.defaultPrevented || !e.clipboardData) return;
  const { md, html } = selectionMarkdown();
  if (!md) return;
  e.preventDefault();
  e.clipboardData.setData("text/plain", md);
  e.clipboardData.setData("text/html", html);
}

// Copy the contents of a code/prompt block without its surrounding reply or
// outer Markdown markers. Read the live DOM so streaming updates are included.
function ChatCopyBlock({ as: Tag, children }) {
  const contentRef = useRef(null);
  const [copied, flashCopied] = useCopied();
  const [failed, setFailed] = useState(false);
  const isCode = Tag === "pre";
  async function copyContent() {
    const el = contentRef.current;
    if (!el) return;
    const text = isCode
      ? (el.querySelector("code")?.textContent || "").replace(/\n$/, "")
      : Array.from(el.childNodes).map((n) => fragmentToMarkdown(n)).join("").replace(/\n{3,}/g, "\n\n").trim();
    const ok = await copyText(text);
    setFailed(!ok);
    if (ok) flashCopied();
  }
  return (
    <div className={`chatCopyBlock ${isCode ? "chatCopyCode" : "chatCopyQuote"}`}>
      <div className="chatCopyTools" data-markdown-copy-ignore="">
        <button type="button" className="chatCopyButton" onClick={copyContent}
          aria-label={isCode ? t("Copy code") : t("Copy quoted text")}
          title={failed ? t("Copy failed — select the text and press Ctrl+C") : t("Copy only this block's content")}>
          <IconSwap swap={!!copied} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
          <span aria-live="polite"><MorphText>{failed ? t("Try again") : copied ? t("Copied") : t("Copy")}</MorphText></span>
        </button>
      </div>
      <Tag ref={contentRef}>{children}</Tag>
    </div>
  );
}
// A fenced block in a reply, coloured by the same highlight.js wrapper and
// theme as the notes' code cards (editor/codeHighlight.js).
function HighlightedCode({ children }) {
  const codeProps = React.Children.toArray(children).find((c) => c?.props)?.props || {};
  const lang = /language-([\w+#-]+)/.exec(codeProps.className || "")?.[1] || "";
  const raw = textOf(codeProps.children).replace(/\n$/, "");
  const html = useMemo(() => highlightCode(raw, lang), [raw, lang]);
  return <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />;
}
function ChatPre({ children, copy = false }) {
  const diagram = diagramCodeProps(children);
  if (diagram) return <DiagramFigure {...diagram} />;
  const code = <HighlightedCode>{children}</HighlightedCode>;
  return copy ? <ChatCopyBlock as="pre">{code}</ChatCopyBlock> : <pre>{code}</pre>;
}
const ChatCopyPre = ({ children }) => <ChatPre copy>{children}</ChatPre>;
const CHAT_COPY_COMPONENTS = {
  pre: ChatCopyPre,
  blockquote: ({ children }) => <ChatCopyBlock as="blockquote">{children}</ChatCopyBlock>,
};

// Navigation for Gamma's own links, provided once by App: the chat, the
// rendered notes and anything else that renders a library link opens a page
// in place instead of reloading the app. { openPage(id, target),
// openBlock(id), openThread(threadId, blockId, pageId) (a comment thread
// open on its page: a computer agent's answer card's threads, the chat's
// thread note), citeSource(pageId) → {title, short} | null }; `target` is
// where to land ({kind: "citation", pageId, page, quote} or {kind: "line",
// pageId, line, to, quote}, null for the page's top); citeSource names the
// paper or file a citation pill points at.
const GammaNavContext = createContext(null);

// A page or block link into a Gamma library rendered as a card, in the chat
// and in a note (a citation or a line link is a CitationPill). `link` comes
// from parseGammaLink, `label` is a resolved page title when the caller has
// one (the note renderer resolves it through the [[ref]] cache); `children`
// is the author's own link text.
//
// The card only claims the link once the id resolves locally: `link.foreign`
// (a link written against another host, e.g. copied before the server moved,
// or pointing at somebody else's Gamma) is handed to the card by a caller
// that could resolve it, and falls back to a plain external link otherwise.
//
// Its link card (shared/ui/LinkCard.jsx) says where it goes; `edit` is the
// card's, from a note that may be edited.
function GammaLinkCard({ link, label, edit, children }) {
  const nav = useContext(GammaNavContext);
  // A bare link (autolinked, or link text that is the URL itself) is not a
  // label — the resolved title reads better.
  const raw = textOf(children).trim();
  const text = /^(https?:\/\/|\/?\?)/i.test(raw) ? "" : raw;
  return (
    <LinkHover edit={edit}
      describe={(_, ctx) => describeLink({ kind: "url", href: link.href, label: label || text }, { ctx })}>
    <a href={link.href || "#"} className={`gammaLinkCard gammaLink-${link.kind}`}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || !nav) return;
        e.preventDefault();
        e.stopPropagation();
        if (link.kind === "block") nav.openBlock?.(link.blockId);
        else nav.openPage?.(link.pageId, null);
      }}>
      <FileTextIcon size={14} aria-hidden="true" />
      {/* The author's own link text, markup and all; a bare link falls back
          to the resolved title. */}
      <span className="gammaLinkLabel">{text ? children : label || "page"}</span>
      {/* The page beside the author's own label ("see this · Paper"). */}
      {text && label && label !== text ? <span className="gammaLinkSrc">{label}</span> : null}
    </a>
    </LinkHover>
  );
}

// Whether the chat reply being rendered cites more than one source (a note
// passes its own `multi`).
const CiteMultiContext = createContext(false);
// An author's label that names only the place ("p. 2", "ll. 3–5", "line 7"),
// so the source goes in front of it.
const PLACE_LABEL = /^(pp?\.|ll?\.|lines?)\s*\d/i;

// A line link's lines in words, "line 7" or "lines 3–5": a pill's label when
// its author gave none, and a pasted link's when its file has no title.
function linePlace(link) {
  return link.to > link.line
    ? t("lines {from}–{to}", { from: link.line, to: link.to })
    : t("line {n}", { n: link.line });
}

// A citation, in a chat reply or a note: a small tinted pill on the text's
// baseline, so a "p. 2" mid-sentence doesn't push the lines apart. It cites a
// PDF passage (`link.kind` "citation") or a text file's lines ("line").
// Hovering or focusing it previews its link card (shared/ui/LinkCard.jsx:
// the source, the PDF page or the lines, the quote), a right-click or a
// held finger pins it; a click (or Enter) opens it in the PDF or the file,
// and so does the card's own link. `multi`: the text cites several
// sources, so a pill that names only the place also names its source
// ("Vaswani · p. 2", "main.tex · line 7"). `guide` marks the chat's
// citations for the tour; `edit` is the card's, from a note that may be
// edited.
function CitationPill({ link, multi, guide, edit, children }) {
  const nav = useContext(GammaNavContext);
  const raw = textOf(children).trim();
  const own = /^(https?:\/\/|\/?\?)/i.test(raw) ? "" : raw;
  const source = nav?.citeSource?.(link.pageId) || null;
  const isLine = link.kind === "line";
  const placeLabel = isLine ? linePlace(link) : `p. ${link.page}`;
  const named = multi && source?.short && (!own || PLACE_LABEL.test(own));
  const open = (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || !nav) return;
    e.preventDefault();
    e.stopPropagation();
    nav.openPage?.(link.pageId, isLine
      ? { kind: "line", pageId: link.pageId, line: link.line, to: link.to, quote: link.quote }
      : { kind: "citation", pageId: link.pageId, page: link.page, quote: link.quote });
  };
  return (
    <LinkHover edit={edit}
      describe={(_, ctx) => describeLink({ kind: "url", href: link.href, label: own }, { ctx })}>
      <a href={link.href || "#"} className={`citePill gammaLink-${link.kind}`} data-guide={guide}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={open}>
        {named ? <span className="citePillSrc" data-markdown-copy-ignore="">{source.short} · </span> : null}
        <span className="gammaLinkLabel">{own ? children : placeLabel}</span>
      </a>
    </LinkHover>
  );
}

// Keep the renderer type stable: replacing it on each streamed delta unmounts
// links and loses clicks when an update lands between mouse-down and mouse-up.
// Context supplies the latest navigation callback without replacing the link.
function ChatMarkdownLink({ href, children, title }) {
  const nav = useContext(GammaNavContext);
  const multi = useContext(CiteMultiContext);
  const link = nav ? parseGammaLink(href, window.location.origin) : null;
  // The chat writes its own citations, so a link it produced is this
  // library's by construction; a foreign host in chat text is an ordinary
  // external link. The citation tour is the PDF's: a file's line pill is not
  // its anchor.
  if (link && !link.foreign && isSourceLink(link)) {
    return <CitationPill link={{ ...link, href }} multi={multi} guide={link.kind === "citation" ? "chat.citation" : undefined}>{children}</CitationPill>;
  }
  if (link && !link.foreign) return <GammaLinkCard link={{ ...link, href }}>{children}</GammaLinkCard>;
  return <ChatWebLink href={href} title={title}>{children}</ChatWebLink>;
}

// A web link in a chat reply: its link card says where it goes.
function ChatWebLink({ href, title, children }) {
  return (
    <LinkHover siteHref={href}
      describe={(site, ctx) => describeLink({ kind: "url", href, label: title || textOf(children) }, { ctx, site })}>
      <a href={href} className="gammaLinkCard" target="_blank" rel="noreferrer">
        <ExternalLinkIcon size={14} aria-hidden="true" /><span className="gammaLinkLabel">{children}</span>
      </a>
    </LinkHover>
  );
}
const CHAT_MARKDOWN_COMPONENTS = { a: ChatMarkdownLink, pre: ChatPre };
const CHAT_MARKDOWN_COPY_COMPONENTS = { ...CHAT_MARKDOWN_COMPONENTS, ...CHAT_COPY_COMPONENTS };

// Gamma's own links (the library agent links the pages it found as
// /?page=<id>, citations add the PDF page and quote) open in place through
// GammaNavContext; Ctrl/Cmd-click still opens a new tab.
const ChatMarkdown = React.memo(function ChatMarkdown({ text, copyBlocks = false }) {
  const normalized = useMemo(() => normalizeChatMarkdown(text), [text]);
  const multiSource = useMemo(() => citesSeveralSources(normalized), [normalized]);
  return (
    <div onCopy={handleMarkdownCopy}>
      <CiteMultiContext.Provider value={multiSource}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath, remarkLoneDisplayMath, remarkPaperLinks, remarkDiagrams]}
          rehypePlugins={[rehypeKatex]}
          urlTransform={(url) => assetUrl(defaultUrlTransform(url))}
          components={copyBlocks ? CHAT_MARKDOWN_COPY_COMPONENTS : CHAT_MARKDOWN_COMPONENTS}
        >
          {normalized}
        </ReactMarkdown>
      </CiteMultiContext.Provider>
    </div>
  );
});

const AutoGrowTextarea = React.forwardRef(function AutoGrowTextarea(props, forwardedRef) {
  const innerRef = useRef(null);

  useEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    // Empty inputs use their rows height. scrollHeight also counts wrapped
    // placeholder text, which can leave the chat tall after clearing context.
    if (!el.value) { el.style.height = ""; return; }
    el.style.height = "0px";
    // Heights are border-box, and scrollHeight leaves out the border.
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  }, [props.value]);

  return (
    <textarea
      {...props}
      ref={(el) => {
        innerRef.current = el;
        if (typeof forwardedRef === "function") forwardedRef(el);
        else if (forwardedRef) forwardedRef.current = el;
      }}
    />
  );
});

// Copy-confirmation flash: `copied` holds whatever key was passed to `flash`
// (true, a message index, "bibtex", …) and reverts to null after `ms`.
// One definition for chat messages, the citation buttons, and the share
// dialog, so the confirm timing can't drift apart.
function useCopied(ms = 1500) {
  const [copied, setCopied] = useState(null);
  const flash = useCallback((key = true) => {
    setCopied(key);
    setTimeout(() => setCopied((cur) => (cur === key ? null : cur)), ms);
  }, [ms]);
  const reset = useCallback(() => setCopied(null), []);
  return [copied, flash, reset];
}

// A copyable text box: the rendered text (or a scrolling <pre>) with the copy
// button pinned top-right — the share popover's slide citation and BibTeX,
// and the export dialog's whole bibliography.
function CopyBox({ children, copied, onCopy, title, label }) {
  return (
    <div className="copyBox">
      <div className="copyBoxBody">{children}</div>
      <button type="button" className={`uiBtn sm iconSq copyBoxBtn ${copied ? "on" : ""}`} onClick={onCopy} title={title} aria-label={label}>
        <IconSwap swap={!!copied} from={<CopyIcon size={16} />} to={<CheckIcon size={16} />} />
      </button>
    </div>
  );
}

function PopoverAnchor({ name, children, className = "" }) {
  return (
    <span data-popover={name} className={`popoverAnchor ${className}`.trim()}>
      {children}
    </span>
  );
}

function BlockDropIndicator({ target }) {
  if (!target) return null;
  // An object (image / table / diagram) dropped INSIDE a block: the line
  // sits in the gap between two of its rendered constructs.
  if (target.inside) {
    return (
      <div className="dropIndicator dropIndicatorInside"
        style={{ top: target.rect.top, left: target.rect.left, width: target.rect.width }} />
    );
  }
  const indentStep = 14;
  const baseOffset = 40; // 22px short of a row's text, where App's onBlockDragOver starts to nest
  const left = target.rect.left + baseOffset + target.depth * indentStep;
  return (
    <div
      className="dropIndicator"
      style={{
        top: target.above ? target.rect.top : target.rect.bottom,
        left,
        width: Math.max(40, target.rect.width - (baseOffset + target.depth * indentStep)),
      }}
    />
  );
}

// Ctrl+scroll text size for a scrolling text panel (the notes list, the chat
// transcript): a session-only scale — nothing is stored — applied as the
// `--text-scale` custom property on the panel, which app.css multiplies into
// the panel's base font sizes. Returns a callback ref for the panel (a native
// non-passive wheel listener: React's onWheel can't preventDefault, and the
// browser would zoom the whole page), the inline style to spread onto it,
// and a badge to render as its first child — a transient "120%" pill that
// shows for a moment after each change. `enabled` (a function, read live) can
// hand the gesture back to the browser, e.g. on the home library.
const TEXT_SCALE_MIN = 0.6, TEXT_SCALE_MAX = 2.5;
function useTextScale({ enabled } = {}) {
  const [scale, setScale] = useState(1);
  const [badge, setBadge] = useState(false);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const accRef = useRef(0);
  const badgeTimerRef = useRef(null);
  const cleanupRef = useRef(null);
  const ref = useCallback((el) => {
    if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null; }
    if (!el) return;
    function onWheel(e) {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (enabledRef.current && !enabledRef.current()) return;
      e.preventDefault();
      // Mouse wheels send ±100 per notch (or 3 lines on Firefox), trackpad
      // pinches a stream of small deltas: accumulate to one step per ~40px.
      accRef.current += e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
      if (Math.abs(accRef.current) < 40) return;
      const dir = accRef.current < 0 ? 1 : -1;
      accRef.current = 0;
      setScale((s) => {
        const next = Math.round(Math.min(TEXT_SCALE_MAX, Math.max(TEXT_SCALE_MIN, s * (dir > 0 ? 1.1 : 1 / 1.1))) * 100) / 100;
        return Math.abs(next - 1) < 0.03 ? 1 : next; // snap back onto 100%
      });
      setBadge(true);
      clearTimeout(badgeTimerRef.current);
      badgeTimerRef.current = setTimeout(() => setBadge(false), 1200);
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    cleanupRef.current = () => { el.removeEventListener("wheel", onWheel); clearTimeout(badgeTimerRef.current); };
  }, []);
  const badgeNode = badge ? (
    <div className="textScaleBadge" aria-live="polite"><span>{Math.round(scale * 100)}%</span></div>
  ) : null;
  return { ref, style: scale === 1 ? undefined : { "--text-scale": scale }, badge: badgeNode };
}

export {
  AutoGrowTextarea,
  CitationPill,
  CopyBox,
  GammaLinkCard,
  GammaNavContext,
  BlockDropIndicator,
  ChatMarkdown,
  DockBar,
  DockWindow,
  handleMarkdownCopy,
  linePlace,
  PopoverAnchor,
  useCopied,
  useDockFolded,
  useTextScale,
};
