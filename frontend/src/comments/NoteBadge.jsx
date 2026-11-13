// The speech-bubble badge at a highlight's or a region's edge
// (docs/dev/comments.md "In the app"): drawn when the person typed a note on
// it or someone commented on it and no margin card shows the thread (a page
// without room for the column, a phone). Its click behaves like clicking
// the mark (`onClick`), and with threads also opens them (`onThread(badge)`,
// the popover's anchor); its hover tip shows the note's Markdown and counts
// the threads. The PDF's pages (pdf/PdfViewer.jsx) and the picture's view
// (documents/viewers/FileViews.jsx) draw it; it imports no viewer.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MessageSquareIcon } from "../shared/ui/Icons";
import { ChatMarkdown } from "../shared/ui/Widgets";
import { menuPress } from "../shared/ui/press.js";
import { lastPointer } from "../shared/lib/pointer.js";
import { t, tn } from "../shared/i18n/i18n.js";

// .pdfNoteTip's CSS max-width is min(NOTE_TIP_W, 100vw - 2·VIEWPORT_PAD) —
// keep in sync with app.css.
const NOTE_TIP_W = 520;
const VIEWPORT_PAD = 8;

// `hlId`: the block the badge stands for (`data-hl-id`, what the popover and
// the margin's outside-press rule find it by); `text`: its note; `threads`:
// its open comment threads, drawn in the accent; `style`: where it stands
// on its page's box; `onMenu(e)`: its menu, opened by a right-click or a
// held finger (shared/ui/press.js menuPress).
export default function NoteBadge({ hlId, text, hasNote = true, threads = 0, style, onClick, onMenu, onThread }) {
  const [tip, setTip] = useState(null);
  const btnRef = useRef(null);
  const tipRef = useRef(null);
  const timerRef = useRef(0);
  // A press that was not the mouse's (shared/lib/pointer.js). Touch has no
  // hover, so a tap opens the tip instead of jumping to the note — the
  // notes panel it would jump to isn't even on screen in the phone layout.
  const tapped = () => lastPointer() !== "mouse";
  const place = () => {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const below = r.top < window.innerHeight * 0.45;
    // Clamp so the tip's max width fits: .pdfNoteTip caps at NOTE_TIP_W px
    // (app.css) plus VIEWPORT_PAD of breathing room on each side.
    setTip({
      left: Math.max(VIEWPORT_PAD, Math.min(r.left - 12, window.innerWidth - NOTE_TIP_W - 2 * VIEWPORT_PAD)),
      ...(below ? { top: r.bottom + 6 } : { bottom: window.innerHeight - r.top + 6 }),
    });
  };
  const show = () => {
    if (tapped()) return; // a tap fires compatibility mouse events too
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(place, 120);
  };
  // Hide on a short delay so the pointer can cross the gap into the tip;
  // entering the tip cancels it — the tip scrolls, so it must survive hover.
  const hide = () => { clearTimeout(timerRef.current); timerRef.current = setTimeout(() => setTip(null), 200); };
  const hideNow = () => { clearTimeout(timerRef.current); setTip(null); };
  const hold = () => clearTimeout(timerRef.current);
  useEffect(() => () => clearTimeout(timerRef.current), []);
  const menu = menuPress(onMenu ? (e) => { hideNow(); onMenu(e); } : null);
  // Tapped-open tip: no pointer leaves a touch screen, so it closes on the
  // next tap outside it (or on the badge again).
  useEffect(() => {
    if (!tip || !tapped()) return;
    const away = (e) => {
      if (tipRef.current?.contains(e.target) || btnRef.current?.contains(e.target)) return;
      hideNow();
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [tip]);
  return (
    <>
      <span className="pdfNoteAnchor" style={style}>
        <button ref={btnRef} type="button" className={`pdfNoteBadge${threads ? " hasThread" : ""}`} data-hl-id={hlId}
          aria-label={threads ? tn("{n} comment", "{n} comments", threads) : t("Show highlight note")}
          {...menu}
          onMouseEnter={show} onMouseLeave={hide}
          onClick={(e) => {
            if (tapped() && !threads) { e.stopPropagation(); clearTimeout(timerRef.current); if (tip) setTip(null); else place(); return; }
            hideNow(); onClick?.(e);
            if (threads) onThread?.(btnRef.current);
          }}
        >
          <MessageSquareIcon size={10} />
        </button>
      </span>
      {tip ? createPortal(
        <div ref={tipRef} className="pdfNoteTip" style={tip} onMouseEnter={hold} onMouseLeave={hide}>
          {hasNote ? (text ? <ChatMarkdown text={text} /> : t("This highlight has a note")) : null}
          {threads ? <div className="pdfNoteTipThreads">{tn("{n} comment", "{n} comments", threads)}</div> : null}
        </div>,
        document.body
      ) : null}
    </>
  );
}
