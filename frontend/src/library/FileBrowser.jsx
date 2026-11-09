// Presentational pieces for the modern file-manager home library: the shared
// page/folder card, the folder+label chip strip, the List/Grid view switch and
// the folders/files kind filter. The large iPadOS-style tile glyphs
// (FolderGlyph, FileGlyph) live in shared/ui/Icons.jsx with the rest of the shared
// icons. All interaction (selection, drag, rename, context menus) stays wired
// in App.jsx alongside the shared handlers.
import React from "react";
import { CheckIcon, FileIcon, FolderFilesIcon, FolderIcon, GridIcon, ListIcon, LabelIcon, PagesIcon, SearchIcon, XIcon } from "../shared/ui/Icons";
import { MenuButton } from "../shared/ui/Menus";
import { FADE_IN, useSlidingPill } from "../shared/ui/MotionWidgets";
import { menuPress } from "../shared/ui/press.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { composing } from "../shared/lib/hotkeys.js";

// Folder + label chips for a page, filtered by the Settings → Appearance →
// Library "File labels" preference ("off" | "labels" | "folders" | "both"). `folders` and
// `labels` are {id, name} — a folder's name its path — as App's page rows
// carry them (`_folderChips` / `_labelChips`). Purely informational — chips
// don't navigate; `onLabelMenu(id)` optionally wires the label rename/delete
// context menu where the caller offers one. With the chips enabled the span
// always renders, empty or not: on a card it is the reserved chip line that
// keeps every footer the same height. Two chips show whatever the
// preference, as they say where a page is and what it holds rather than
// how it is filed: `pages`, how many pages are right inside it, and
// `inside`, a sub-page's path, the pages it is in ("Top › Mid"), in place
// of folder chips — a page inside another is filed with its top, and the
// path is where it is.
function CardLabels({ folders, labels, inside = "", pages = 0, mode = "both", onLabelMenu, className = "cardLabels" }) {
  if (mode === "off" && !inside && !pages) return null;
  const showFolders = (mode === "both" || mode === "folders") && folders?.length > 0 && !inside;
  const showLabels = (mode === "both" || mode === "labels") && labels?.length > 0;
  const holds = pages ? tn("{n} page inside", "{n} pages inside", pages) : "";
  return (
    <span className={className} data-guide={showFolders || showLabels ? "home.cardLabels" : undefined}>
      {pages ? (
        <span className="pagesTagBadge" title={holds} aria-label={holds}>
          <PagesIcon size={10} />
          {pages}
        </span>
      ) : null}
      {inside ? (
        <span className="insideTagBadge" title={t("Inside {path}", { path: inside })}>
          <FileIcon size={10} />
          {inside}
        </span>
      ) : null}
      {showFolders ? folders.map((f) => (
        <span key={`f:${f.id}`} className="folderTagBadge" title={t("In folder {f}", { f: f.name })}>
          <FolderIcon size={10} />
          {f.name}
        </span>
      )) : null}
      {showLabels ? labels.map((l) => (
        <span
          key={`l:${l.id}`}
          className="labelTagBadge"
          title={onLabelMenu ? t("Label: {l} — right-click to rename or delete", { l: l.name }) : t("Label: {l}", { l: l.name })}
          {...menuPress(onLabelMenu?.(l.id))}
        >
          <LabelIcon size={10} />
          {l.name}
        </span>
      )) : null}
    </span>
  );
}

// The one home-library card: a cover (page snapshot when available, else a
// text preview of the page's first blocks, else the file/folder glyph) over a
// bottom-stuck footer of title + labels + kind/time.
// Used by every card surface — both carousels, the pinned strip and the grid
// listing — and it renders the SAME geometry everywhere; the container only
// decides the width (fixed in a carousel, fluid grid track in .fileGrid).
// Click and drag handlers arrive via rootProps, overlay buttons (pin,
// remove ×) and rename inputs as nodes.
// `onMenu` opens the card's menu: a right-click, a held finger, or its "⋯"
// in the cover's other corner. `checked` marks it picked in Select mode,
// with a check where the "⋯" was.
function PageCard({
  glyph, snap, preview, title, tip, renameNode, kind, count, time,
  folders, labels, inside, pages, labelMode, onLabelMenu, onMenu, checked = false,
  className = "", children, ...rootProps
}) {
  return (
    <div className={`pageCard ${onMenu ? "rowMenuHost" : ""} ${className}`} title={tip || title} {...menuPress(onMenu)} {...rootProps}>
      <div className="pageCardCover">
        {snap ? <img src={snap} alt="" draggable={false} {...FADE_IN} />
          : preview ? <div className="pageCardPreview" aria-hidden="true">{preview}</div>
          : glyph}
      </div>
      <div className="pageCardBody">
        {renameNode || <div className="pageCardTitle">{title || t("Untitled")}</div>}
        <CardLabels folders={folders} labels={labels} inside={inside} pages={pages} mode={labelMode} onLabelMenu={onLabelMenu} />
        {kind || count != null || time ? (
          <div className="pageCardMeta">
            {kind ? <span className="pageCardKind">{kind}</span> : null}
            {count != null ? <span className="pageCardCount">{count}</span> : null}
            <span className="pageCardTime">{time}</span>
          </div>
        ) : null}
      </div>
      {checked ? <SelectCheck /> : onMenu ? <MenuButton className="pageCardMenu" open={onMenu} /> : null}
      {children ? <div className="pageCardCorner">{children}</div> : null}
    </div>
  );
}

// Select mode's mark on a picked row or card: in a row it takes the glyph's
// place, on a card the cover's corner.
function SelectCheck() {
  return <span className="selectCheck" aria-hidden="true"><CheckIcon size={12} weight={3} /></span>;
}

// List / Grid segmented control; the chosen one's plate slides
// (useSlidingPill), as in the kind toggle below.
function ViewToggle({ view, onChange }) {
  const ref = React.useRef(null);
  useSlidingPill(ref, view);
  return (
    <div className="homeViewToggle" role="group" aria-label={t("View mode")} ref={ref}>
      <span className="slidePill" aria-hidden="true" />
      <button
        className={`homeViewBtn ${view === "list" ? "active" : ""}`}
        onClick={() => onChange("list")}
        title={t("List view")}
        aria-pressed={view === "list"}
      >
        <ListIcon size={16} />
      </button>
      <button
        className={`homeViewBtn ${view === "grid" ? "active" : ""}`}
        onClick={() => onChange("grid")}
        title={t("Grid view")}
        aria-pressed={view === "grid"}
      >
        <GridIcon size={16} />
      </button>
    </div>
  );
}

// What the listing shows: folders + files, folders only, files only, or the
// labels in scope (labels browse exactly like folders — see LabelRow/openLabel
// in App.jsx).
function KindToggle({ value, onChange, scopeLabel }) {
  const kinds = [
    ["all", t("Folders & files"), FolderFilesIcon],
    ["folders", t("Folders only"), FolderIcon],
    ["files", t("Files only"), FileIcon],
    ["labels", t("Labels"), LabelIcon],
  ];
  const ref = React.useRef(null);
  useSlidingPill(ref, value);
  return (
    <div className="homeViewToggle" role="group" aria-label={scopeLabel || t("Shown items")} title={scopeLabel || undefined} ref={ref}>
      <span className="slidePill" aria-hidden="true" />
      {kinds.map(([val, label, Icon]) => (
        <button
          key={val}
          className={`homeViewBtn ${value === val ? "active" : ""}`}
          data-guide={val === "labels" ? "home.labelsView" : undefined}
          onClick={() => onChange(val)}
          title={label}
          aria-pressed={value === val}
        >
          <Icon size={16} />
        </button>
      ))}
    </div>
  );
}

// Listing filter box: matching items float to the top of the current sort,
// the rest stay put but dimmed. Live as you type — the caller debounces
// nothing, the listing is already memoized. It matches titles and folder /
// label chips only, and says so; `onEnter` (Enter with nothing matched)
// hands the query to the full search. `keyLabel` is its shortcut, shown in
// the hover title.
function ListFindBox({ value, onChange, onEnter, keyLabel }) {
  const label = t("Filter by title or label");
  return (
    <div className={`homeFindBox ${value ? "active" : ""}`} data-guide="home.filter">
      <SearchIcon size={14} />
      <input
        className="homeFindInput"
        value={value}
        placeholder={label}
        title={keyLabel ? `${label} (${keyLabel})` : label}
        aria-label={t("Filter this listing by title or label")}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (composing(e)) return;
          if (e.key === "Escape" && value) { e.stopPropagation(); onChange(""); }
          else if (e.key === "Enter" && value.trim() && onEnter) { e.preventDefault(); onEnter(); }
        }}
      />
      {value ? (
        <button className="uiClose uiCloseSm homeFindClear" title={t("Clear search")} aria-label={t("Clear search")} onClick={() => onChange("")}><XIcon size={14} /></button>
      ) : null}
    </div>
  );
}

// Where the filter box points past itself, to the workspace search over
// notes and PDF text: a banner when nothing in the listing matched (the
// query is filled into the search), else a quiet row under the matches.
// Neither is a .fileRow / .folderRow, which callers count.
function ListSearchElsewhere({ query, none, keyLabel, onSearch }) {
  if (none) {
    return (
      <div className="homeSearchBanner" role="status">
        <span className="homeSearchBannerText">
          {t("No title or label matches “{q}”.", { q: <strong key="q">{query}</strong> })}
        </span>
        <button type="button" className="uiBtn primary sm homeSearchBannerBtn" onClick={onSearch}>
          <SearchIcon size={14} />
          {t("Search inside notes and PDFs")}
          {keyLabel ? <kbd className="homeSearchKey">{keyLabel}</kbd> : null}
        </button>
      </div>
    );
  }
  return (
    <button type="button" className="homeSearchMore" onClick={onSearch}
      title={keyLabel ? t("Search inside notes and PDFs") + ` (${keyLabel})` : undefined}>
      <SearchIcon size={14} />
      <span>{t("Also search inside notes and PDFs for “{q}”", { q: query })}</span>
    </button>
  );
}

export { ViewToggle, KindToggle, ListFindBox, ListSearchElsewhere, CardLabels, PageCard, SelectCheck };
