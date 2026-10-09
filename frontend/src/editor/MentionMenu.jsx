// The "@" menu of the block editor and the chips mentions render as
// (docs/dev/mentions.md). The syntax, the rows and the dates are the pure
// editor/mentions.js; editor/BlockTree.jsx owns the trigger, the keys and
// the insertion. App provides the two contexts: who can be mentioned
// (MentionContext, changes rarely — the rows read it) and the minute clock
// (MentionClock, read only by the chips, so a tick re-renders nothing else).
import React, { createContext, useContext, useEffect } from "react";
import { useCaretAnchored } from "./LatexEditor";
import { MarkedText } from "../search/SearchPanel";
import { PeerAvatar, nameColor } from "../collaboration/Presence";
import { AlarmClockIcon, ClockIcon, FileGlyph, UserPlusIcon } from "../shared/ui/Icons";
import { fmtDate, t } from "../shared/i18n/i18n.js";
import {
  MENTION_SECTIONS, NO_MENTIONS, dateLabel, dayDelta, localMoment, mentionChip, mentionChipClass, scanMentions,
} from "./mentions.js";

export const MentionContext = createContext(NO_MENTIONS);
export const MentionClock = createContext(null);

// A mention in a rendered note (BlockMarkdown's `mention:` links): `raw`
// is the token's text.
export function MentionChip({ raw }) {
  const people = useContext(MentionContext);
  const clock = useContext(MentionClock);
  const token = scanMentions(raw)[0];
  const chip = token && mentionChip(token, people, clock || new Date());
  if (!chip) return raw;
  return (
    <span className={mentionChipClass(chip)} title={chip.title} data-mention={raw}>{chip.text}</span>
  );
}

function RowIcon({ row }) {
  if (row.kind === "date") return <ClockIcon size={16} />;
  if (row.kind === "remind") return <AlarmClockIcon size={16} />;
  if (row.kind === "person") return <PeerAvatar peer={{ name: row.user, color: nameColor(row.user), anchor: 0 }} title={row.user} />;
  if (row.kind === "invite") return <UserPlusIcon size={16} />;
  return <FileGlyph isPdf={row.isPdf} size={16} />;
}

function RowText({ row, query, now }) {
  if (row.kind === "date") {
    // A date further than a day away says its weekday too ("next fri").
    const far = Math.abs(dayDelta(row.date, now)) > 1;
    return <>{dateLabel(row, now)}{far ? <span className="mentionAside">{fmtDate(localMoment(row.date), { weekday: "long" })}</span> : null}</>;
  }
  if (row.kind === "remind") return <>{t("Remind me")}<span className="mentionAside">— {dateLabel(row, now)}</span></>;
  if (row.kind === "person") {
    return <><MarkedText text={row.user} query={query} />{row.you ? <span className="mentionAside">{t("(You)")}</span> : null}</>;
  }
  if (row.kind === "invite") return t("Invite…");
  return <MarkedText text={row.title || t("Untitled")} query={query} />;
}

// Caret-anchored, the [[ picker's surface and rows: the section titles,
// one row per choice (a page's folder path under its title), a key-hint
// footer.
export function MentionMenuPopup({ rows, selected, anchor, query, onPick }) {
  const [ref, style] = useCaretAnchored(anchor, false, [rows]);
  const now = new Date();
  useEffect(() => {
    ref.current?.querySelector(".refPopupItem.selected")?.scrollIntoView({ block: "nearest" });
  }, [selected, ref]);
  return (
    <div ref={ref} className="refPopup mentionMenu" style={style} role="listbox" aria-label={t("Mention")}>
      {rows.map((row, i) => (
        <React.Fragment key={row.key}>
          {row.section !== rows[i - 1]?.section ? <div className="refPopupHead">{t(MENTION_SECTIONS[row.section])}</div> : null}
          <button
            type="button"
            role="option"
            aria-selected={i === selected}
            className={`refPopupItem${i === selected ? " selected" : ""}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(row)}
          >
            <span className="refPopupIcon">
              <RowIcon row={row} />
            </span>
            <span className="refPopupBody">
              <span className="refPopupText"><RowText row={row} query={query} now={now} /></span>
              {row.path ? <span className="refPopupMeta">{row.path}</span> : null}
            </span>
          </button>
        </React.Fragment>
      ))}
      <div className="refPopupFooter">{t("↑↓ choose · Enter insert · Esc keep text")}</div>
    </div>
  );
}
