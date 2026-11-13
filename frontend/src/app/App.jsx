import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { TabGroups } from "../workspace/TabGroups";
import PagePane, { keepPaneTree } from "../workspace/PagePane";
import { activate as activateGroupIn, activeGroup, groupById, groupOf, groupsOf, isSingle, joinAll, moveTab, normalizeLayout, reconcile as reconcileGroups, removeGroup, removeTab, reorderGroup, resizeSplit, singleLayout, splitGroup, tabOrder } from "../workspace/tabLayout.js";
import PdfViewer, { openPdfDocument, preloadPdfEngine } from "../pdf/PdfViewer";
import { highlightSpot, rangeSpot } from "../pdf/pdfSelectionSpot";
import { penSelecting } from "../pdf/penSelect.js";
import { addTerm, briefRequest, chunkRequest, endTranslations, forgetTranslation, lookupRequest, useTranslateJobs } from "./translateClient.js";
import { createRecall, langOfKey, modelOfKey, openKey } from "../pdf/translateRecall.js";
import { scopeOf, snapshot as translateSnapshot, span as translateSpan } from "../pdf/translateState.js";
import { exportFile, exportLayout, exportPages } from "../pdf/translateExport.js";
import { folderPapers, paperLayout } from "../pdf/folderTranslation.js";
import { useFolderTranslations } from "../pdf/useFolderTranslations.js";
import { docIdOf } from "../pdf/pdfSource";
import { discardApplied, discardOutcome, discardStep, discardUndone, noteSummary, pendingInOrder, reviewQueue, reviewStep } from "../pdf/highlightReview.js";
import { basePixels } from "../pdf/pageRaster";
import { DEFAULT_HIGHLIGHT, LINK_COLOR, highlightColor, normalizeColor } from "../shared/model/palette.js";
import { presetName, withStyle } from "../shared/model/markStyle.js";
import { PaletteContext } from "../shared/ui/paletteContext.js";
import { MarkMenuRows, MarkToolsContext } from "../pdf/MarkTools.jsx";
import { HighlighterMenu, HighlighterRows, HighlighterStrip } from "../pdf/HighlighterStrip.jsx";
import { useTextHighlighter } from "../pdf/useTextHighlighter.js";
import { clampZoom } from "../shared/model/zoom.js";
import { fmtDate, getLocale, resolveLocale, t, T, tn } from "../shared/i18n/i18n.js";
import { REOPEN_SETTINGS_KEY, resolveSettingsPane } from "../settings/settingsNavigation.js";
import { useTasks } from "../tasks/useTasks";
import { useAppJobs } from "../tasks/useAppJobs";
import { TaskBadge, TasksButton, TasksPanel } from "../tasks/TasksTray";
import { progressText, taskTitle } from "../tasks/taskKinds.js";
import { isActive } from "../tasks/taskModel.js";
import { parseGammaLink } from "../shared/model/gammaLinks.js";
import { pageHostUser, publicPath } from "../shared/lib/slug.js";
import { API, apiJson, assetUrl, getShareToken, setShareView, withShare, withWorkspace, blockLinkUrl, setCurrentWorkspace, getCurrentWorkspace, setLinkName, makeId, fmtBytes, getDocIdForUrl, metaSourceInfo, resolvePdfUrl, probePdfUrl, setExpectedUser, getExpectedUser, usePersistedState, usePersistedFlag, copyText, copyRich } from "../shared/lib/utils";
import { ITEM_TYPES, itemTypeFields, itemTypeOf } from "../shared/lib/itemTypes.js";
import {
  AutoGrowTextarea,
  BlockDropIndicator,
  ChatMarkdown,
  CopyBox,
  DockBar,
  DockWindow,
  GammaNavContext,
  OpenTabs,
  PopoverAnchor,
  useCopied,
  useTextScale,
} from "../shared/ui/Widgets";
import { BlockTree, _dragState } from "../editor/BlockTree";
import { MentionClock, MentionContext } from "../editor/MentionMenu";
import { REF_RE, refIdsOf, refLabelOf, refLabelsByBlock } from "../editor/refLabels.js";
import { BacklinksPanel } from "../editor/BacklinksPanel";
import { SubPagesPanel } from "../editor/SubPagesPanel";
import { PageCrumbs } from "../editor/PageCrumbs";
import { dropGapAtPoint, findObject } from "../editor/MdObject";
import { cutObject, moveObjectInTree } from "../editor/mdObjects";
import { scanMathSpans } from "../editor/mdScan";
import { sourceRangeOfSelection } from "../editor/clickToSource";
import { FileChipContext, forgetDocPages, rememberDocPage, setUploadReporter, uploadFilesAsLines } from "../transfers/FileChip";
import { ASK_PAST_FILES, attachFile, dropFilter, promoteChip, routeFile, uploadOneFile } from "../transfers/documentUploads.js";
import { uploadPdf } from "../shared/lib/uploadParts";
import { CardLabels, KindToggle, ListFindBox, ListSearchElsewhere, PageCard, SelectCheck, ViewToggle } from "../library/FileBrowser";
import { createChatSession } from "../chat/chatSession";
import { providerModels } from "../chat/modelPrefs";
import { addPictures, isPdfPicture, pictureUrl, regionBox, regionPicture, round4 } from "../chat/chatPictures.js";
import { MAX_CHAT_PASSAGES, MAX_INK_SELECTIONS, MAX_INK_SELECTION_BYTES } from "../chat/passageBudget.js";
// Agent tools whose applied action changes the open page's block tree
// (handleAgentEvent reloads it and lights the block up).
import { BLOCK_TOOLS } from "../chat/agentSteps.js";
import SearchPanel from "../search/SearchPanel";
import LibraryEmpty from "../library/LibraryEmpty";
import { ContextMenu, MenuButton, MenuDivider, MenuItem, MenuLabel, MenuScope, MenuSelect, SubMenuItem, menuGroups } from "../shared/ui/Menus";
import { backdropPress, menuPress, triggerPress } from "../shared/ui/press.js";
import { lastPointer, touchScreen } from "../shared/lib/pointer.js";
import { appShell, nativeHost, postToHost } from "../shared/lib/host.js";
import { dispositionName, saveBlob } from "../shared/lib/saveFile.js";
import { pickFiles } from "../shared/lib/pickFiles.js";
import { canPickFolder, filesUpload, folderPick } from "../transfers/folderZip.js";
import { doneWithOpenedFile, hostOpensFiles, listOpenedFiles, onFilesOpened, openedKind, placeOpenedFiles,
  readOpenedFile } from "../transfers/openedFiles.js";
import { importSource } from "../transfers/transferFormats.js";
import { bibliographyPreview } from "../transfers/exportApi";
import { collectEntryFiles } from "../shared/lib/dropEntries.js";
import { useWheelPan } from "../shared/ui/wheelPan";
import {
  ActivityIcon, AlertCircleIcon, ArrowDownIcon, ArrowLeftIcon, ArrowUpDownIcon, ArrowUpIcon, BugIcon, CheckIcon, CloudIcon, CopyIcon, DownloadIcon, ExportIcon,
  ExternalLinkIcon, EyeIcon, EyeOffIcon, FileGlyph, FileIcon, FileTextIcon, FitWidthIcon, FolderGlyph, HighlightIcon, SquareCheckIcon,
  FilePlusIcon, PaperclipIcon, FolderIcon, FolderOpenIcon, FolderPlusIcon, HardDriveIcon, HelpCircleIcon, HomeIcon, ImportIcon, IndentIcon, InfoIcon, LabelGlyph, LabelIcon, OutdentIcon,
  LanguagesIcon, LanguagesOffIcon, LinkIcon, LogOutIcon, MaximizeIcon, MenuIcon, MinimizeIcon, MoreIcon, MoveVerticalIcon, PenIcon, PinIcon, PlusIcon, QuoteIcon,
  RectSelectIcon, RefreshIcon, SettingsIcon, SparklesIcon, TextCursorIcon, Trash2Icon, TrashIcon, TypeIcon, UploadIcon,
  ScissorsIcon, ShareIcon, UserIcon, UsersIcon, XIcon, ZoomInIcon, ZoomOutIcon, NotebookIcon, SheetIcon, MicIcon, MessageSquareIcon, BellIcon,
  PanelTopIcon, LocateIcon, TextSearchIcon, CloudDownloadIcon, BracesIcon, SplitColumnsIcon, OnePaneIcon,
  StopIcon, GlobeIcon, ChevronRightIcon, FlagIcon,
} from "../shared/ui/Icons";
import ReportContentHost from "../support/ReportContentHost";
import { openReport as openContentReport } from "../support/reportContent.js";


import {
  setBlockText,
  addSiblingBlock,
  addChildBlock,
  addRootBlock,
  indentBlock,
  outdentBlock,
  EMPTY_VIEW,
  withEditing,
  closeEditing,
  isFolded,
  toggleFold,
  revealBlock,
  sameShape,
  isFoldedAway,
  updateBlockTree,
  removeBlockTree,
  flattenBlocks,
  blockPosition,
  blockQuote,
  isDescendant,
  findBlockContext,
  extractBlock,
  insertSibling,
  insertChild,
  addHighlightAsBlock,
  blocksToHighlights,
  acceptedPosition,
  closestPlacement,
  reviewUndone,
  placedToCheck,
  isInkGroup,
  isPlaceBlock,
  anchorOf,
  positionKeyOf,
  placeKeyOf,
  POSITION_KEY,
  isPlainNote,
  makeNewBlock,
  mergeIntoAbove,
  findBlock,
  moveSibling,
  removeBlockKeepChildren,
  visibleNeighbor,
  removeBlocks,
  indentBlocks,
  outdentBlocks,
  moveSiblings,
  insertBlocks,
  freshCopy,
} from "../shared/model/blockModel";
import { subtreeMarkdown } from "../editor/blockSelection.js";
import { useBlockSelection } from "../editor/useBlockSelection.js";
import { sourceCopied } from "../editor/sourceCopy.js";
import { chordLabel, composing, dispatch as dispatchHotkey, effectiveKeys, isTextField } from "../shared/lib/hotkeys.js";
import { APP_COMMANDS, liveAppCommands } from "./appCommands.js";
import { BLOCK_COMMANDS } from "../editor/blockCommands.js";
import { commandById, commandChord } from "./commands.js";
import { CommandMenuItem } from "./CommandMenuItem.jsx";
import { loadSession, saveSession, clearSession, setSessionScope } from "./sessionState";
import { ROLE_LABEL, actingRole, workspaceMeta } from "../settings/workspaceRoles.js";
import { LoginPage, SessionConflictPage, ShareBlockedPage, WorkspaceUnavailablePage, cloudOutcomeInUrl } from "../auth/LoginPage";
import { guestExpiryLabel } from "../auth/guestExpiry";
import { McpAuthorization } from "../auth/McpConsent";
import { TRANSLATE_LANGS, preloadPrefs, readPref, translateModelFor, useAppPrefs, useProfileSync } from "./prefs";
import { TOPBAR, TOPBAR_ITEMS } from "./topbarItems.js";
import { TOPBAR_ICONS } from "./topbarIcons.js";
import { BAR_PLACES } from "../shared/model/barLayout.js";
import { VIEWER_TOOLS } from "./viewerToolItems.js";
import { adoptAccountLook, takeBootRead } from "./boot.js";
import { applyTheme } from "./theme.js";
import { useNotices } from "./useNotices";
import { useReminders } from "./useReminders";
import { ReminderAlerts } from "./ReminderAlerts";
import { dotTone, noticeAction, noticeText } from "./notices";
import { useBlockHistory } from "../editor/blockHistory.js";
import { useMarks } from "../markup/MarkupLayers";
import { MarkupToolbar } from "../markup/MarkupToolbar";
import { PageToolsContext } from "../markup/PageTools";
import { makeStableActions, useStableActions } from "../shared/lib/stableActions.js";
import { keepIfSame, keepIfSameJson, sameMap, sameObject } from "../shared/lib/keepIfSame.js";
import { blocksById, commentAnchors, pageHighlights, recordingList, sheetNumbersOf, stampedList } from "./treeDerived.js";
import { isTextBox, normalizeTextBox } from "../markup/textBox.js";
import { useTextBoxes } from "../markup/useTextBoxes";
import { isPicture, normalizePicture, turnedBox } from "../markup/picture.js";
import { picturesDrag } from "../markup/pictureUpload.js";
import { usePictures } from "../markup/usePictures";
import { inkBounds } from "../ink/ink";
import { useBuildFence } from "../ink/buildFence.js";
import { useWorkspaceDeleting } from "./useWorkspaceDeleting.js";
import { useInkStrip } from "../ink/useInkStrip.js";
import { useInkLive } from "../ink/useInkLive.js";
import { useInkSave } from "../ink/useInkSave.js";
import { useInkSurfaces, visiblePart } from "../ink/useInkSurfaces.js";
import { useInkDocument } from "../ink/useInkDocument.js";
import { useInkStrokes } from "../ink/useInkStrokes.js";
import { useInkSelection } from "../ink/useInkSelection.js";
import { useInkRuler } from "../ink/useInkRuler.js";
import { useInkZoom } from "../ink/useInkZoom.js";
import { useStripKeys } from "../ink/useStripKeys.js";
import { usePencil } from "../ink/usePencil.js";
import { useShellBar } from "./useShellBar.js";
import { inkCommandContext } from "../ink/inkCommands.js";
import { NotebookViewer, PaperMenu, readPlace, showPlace } from "../notebook/NotebookViewer";
import { NoteSheetContext } from "../notebook/NoteSheet";
import {
  blockToSheet, firstSheetId, insertSheetAfter, isSheet, newSheet, normalizePaper, sheetAfterPlan, sheetOfBlock,
  sheetsOf, withAllSheetsPaper, withSheetPaper,
} from "../notebook/notebook";
import { generateKeyBetween } from "fractional-indexing";
import * as inkLive from "../ink/inkLive";
import { keepaliveFetch } from "../shared/lib/keepalive.js";
import * as playback from "../replay/playback.js";
import { recordingClock } from "../replay/clock.js";
import { audioSource } from "../recording/audioSource.js";
import * as recorderStore from "../recording/recorderStore.js";
import { RecordingContext } from "../recording/RecordingContext.js";
import { RecordingPill } from "../recording/RecordingPill";
import { PlayerBar } from "../recording/PlayerBar";
import { recordingAt, normalizeRecording } from "../recording/recording.js";
import { usePageCollab } from "../collaboration/usePageCollab";
import { retryableStatus } from "../collaboration/collabSession.js";
import { applyOps, applyPatch, displacedRow } from "../shared/model/blockOps";
import { PresenceBar } from "../collaboration/Presence";
import { ShareAccessPill } from "../sharing/ShareAccess";
import { moveExposure, moveExposureMessage } from "../sharing/shareReach.js";
import { BrandMark, Splash } from "../shared/ui/BrandMark";
import { cleanLinkName, loadLinkName, saveLinkName, LINK_NAME_MAX } from "../collaboration/linkName";
import { modelList, useProviderEditor } from "../settings/providerEditor.js";
import { useGuide } from "../guide/useGuide";
import { guideEvents } from "../guide/events";
import { askConnectorHere, IS_DESKTOP } from "../shared/lib/connector.js";
import { Empty, Section, StorageRing } from "../settings/SettingsKit";
import { LazyBoundary, lazySurface, useLatch, whenIdle } from "../shared/ui/lazy";
import { libraryAccess } from "../library/libraryAccess";
import { MirrorPopover } from "../collaboration/MirrorPopover";
import { MergeChip } from "../collaboration/MergeResolver";
import {
  ZOTERO_PDF_COMING, followSync, isZoteroPdfComing, linkGone, linkPath, pillLink, zoteroLibraryLabel, zoteroPdfCard, zoteroPdfSource,
  zoteroPdfState,
} from "../collaboration/syncLinks.js";
import { ZoteroPdfCard, ZoteroReadonlyBadge } from "../collaboration/ZoteroPage.jsx";
import { isVaultConflict } from "../collaboration/conflictKinds.js";
import { VaultBanner, VaultChip } from "../vault/VaultChip.jsx";
import { askShell, useVault } from "../vault/useVault.js";
import { addsToLibrary, carriesToServer, isOpenedPdf, libraryWorkspaces, mentionAgent, notesLocked as vaultNotesLocked, openedHere, vaultConflictsPath } from "../vault/vaultState.js";
import { hopTarget, lockedRow, touchesLocked } from "../vault/notesLock.js";
import { fetchZoteroPdf } from "../transfers/zoteroApi";
import {
  EMPTY_TREE,
  addToFolder,
  childFolders,
  filedIn,
  filingChips,
  findNamed,
  findPageForUrl,
  folderChain,
  folderPath,
  folderPositions,
  folderNeighbour,
  folderSubtree,
  inFolder,
  labelName,
  libraryTree,
  siblingFolders,
  formatFullDate,
  formatRelativeTime,
  formatShortDate,
  isFreshLibrary,
  welcomePageOf,
  pageBlocksOf,
  pageDescendants,
  topmostPages,
  pagesIntoPage,
  deleteConfirm,
  topPageOf,
  pageAncestors,
  friendlyApiError,
  pageKindLabel,
  defaultPageTitle,
  metadataToDraft,
  citationKeyOf,
  NO_LABEL,
  NO_LABEL_TITLE,
  PATH_SEP,
  folderAtPath,
  folderCompletion,
  folderInside,
  folderMatches,
  folderPathNames,
} from "../library/libraryUtils";
import { copyDocument, documentFileUrl, documentViewerUrl, fileHash, inPlace, isPdf, ownHash, pageDocument, parseUploadUrl, pdfProxyUrl, storedName, uploadUrl } from "../documents/pageDocument.js";
import { isLibraryPdf, workFolderOf } from "../documents/library.js";
import { mergeKnown } from "../explorer/knownPages.js";
import { fileRules, filingTargets, folderRules, refusalText, treeOf, workOf } from "../explorer/workFolders.js";
import { directoryMoves, filingOf, libraryCopy, listDirs, listRows, pagesMenu, workCounts } from "../explorer/workFolderActions.js";
import { useWorkFolderActions } from "../explorer/useWorkFolderActions.jsx";
import FolderList from "../explorer/FolderList.jsx";
import { useFolderRows, useWorkSort } from "../explorer/useFolderRows.js";
import { useSummaries } from "../explorer/useSummaries.js";
import FeedbackHost from "../feedback/FeedbackHost.jsx";
import { openFeedback } from "../feedback/useFeedback.js";
import { documentView, viewerOf } from "../documents/documentView.js";
import { VIEWERS } from "../documents/viewers.js";
import DocumentPane, { preloadViewer } from "../documents/DocumentPane.jsx";
import { replaceAccept, useViewerSurface } from "../documents/viewerSurface.js";
import { createLibraryMatcher } from "../library/librarySearch";
import { prefetchTrash } from "../library/trashList";
import { batchText, bulkOutcomes, runBatch, runBulk } from "../library/pageBatch.js";
import { useComments } from "../comments/useComments";
import { abandonedCommentHighlight, anchorWords, isOpen, shortCount, threadAnchor, threadLink, threadPlace } from "../comments/comments.js";
import { preloadCommentMargin } from "../comments/marginSlot";
import { ThreadPlaceContext } from "../comments/threadPlaceContext.js";
import { useInkThreads } from "../comments/useInkThreads.js";
import { useInbox } from "../inbox/useInbox";
import { InboxBadge, InboxButton } from "../inbox/InboxButton";
import { inboxJump, isUnread } from "../inbox/inboxText.js";
import { DialogOverlay, Popover } from "../shared/ui/Surfaces";
import { IconSwap, MorphText } from "../shared/ui/MotionWidgets";
import ListMotion from "../shared/ui/ListMotion";

// Surfaces fetched on first use, each mounted inside a LazyBoundary
// (docs/dev/frontend-refactor.md, "Lazy boundaries"). `preload()` fetches
// one ahead: on hover or focus of what opens it, at startup for the chat
// dock a desktop shows from the start, when idle for the Ctrl+P palette.
const ChatDock = lazySurface(() => import("../chat/ChatDock"));
const SettingsDialog = lazySurface(() => import("../settings/SettingsDialog"));
const GuideOverlay = lazySurface(() => import("../guide/GuideOverlay"));
const QuickOpen = lazySurface(() => import("../library/QuickOpen"));
const PagePicker = lazySurface(() => import("../library/PagePicker"));
const LinkDialog = lazySurface(() => import("../pdf/LinkDialog"));
const RecentlyDeleted = lazySurface(() => import("../library/RecentlyDeleted"));
const ReportProblem = lazySurface(() => import("../support/ReportProblem"));
const SharePopover = lazySurface(() => import("../sharing/SharePopover"), (m) => m.SharePopover);
const ImportDialog = lazySurface(() => import("../transfers/ImportExport"), (m) => m.ImportDialog);
const ExportDialog = lazySurface(() => import("../transfers/ImportExport"), (m) => m.ExportDialog);
const ImportReviewDialog = lazySurface(() => import("../transfers/ImportReviewDialog"));
// Comments (docs/dev/comments.md "In the app"): a block's thread popover,
// fetched when the pointer or focus reaches a comment control, and the
// Comments window, with the View menu.
const ThreadPopover = lazySurface(() => import("../comments/ThreadPopover"));
// A block's Properties popover (read-only: what it is and every key it
// stores), fetched when the pointer reaches the ⋮⋮ handle or a menu opens it.
const BlockProperties = lazySurface(() => import("../editor/BlockProperties"));
const CommentsPanel = lazySurface(() => import("../comments/CommentsPanel"));
// The Inbox's panel (docs/dev/comments.md "In the app"), fetched when the
// pointer or the focus reaches the bell.
const InboxPanel = lazySurface(() => import("../inbox/InboxPanel"));
// A synced vault's notes file edited whole (Edit the file, vault/VaultChip.jsx),
// and Add to library…, an opened PDF's (vault/useVault.js), while open.
const FileSourceDialog = lazySurface(() => import("../vault/FileSourceDialog"));
const FolderPickerDialog = lazySurface(() => import("../vault/FolderPickerDialog"));
// Choose files…, what a work folder tracks (explorer/TrackDialog.jsx), while open.
const TrackDialog = lazySurface(() => import("../explorer/TrackDialog"));
// What a header menu opens, fetched when the pointer or focus reaches its
// button so nothing waits on the network after the click: the account menu
// (Settings, Tours, Report a problem and, on a phone, the View rows) and the
// View menu (Import/Export, the AI Chat toggle).
const preloading = (...surfaces) => () => { for (const surface of surfaces) surface.preload(); };
const preloadAccountMenu = preloading(SettingsDialog, GuideOverlay, ReportProblem, ImportDialog, ChatDock);
const preloadViewMenu = preloading(ImportDialog, ChatDock, CommentsPanel);
// Why a native app's opened files went to another workspace, kept across
// the reload the switch is (takeOpenedFiles).
const OPENED_NOTE = "gamma-opened-note";
// The share link a share view's "Add to my library" hands the visitor's own
// library to import as it boots (addShareToLibrary).
const IMPORT_SHARE = "gamma-import-share";
// Recently deleted asks for its list with its module, so it lands whole.
const preloadTrash = () => { RecentlyDeleted.preload(); prefetchTrash(); };

// PDF load phases that own a row in the background-transfers popover; every
// other phase is viewer-local. Allowlist on purpose — the transfer handling's
// catch-all marks unrecognized phases as failed, so a new local phase must
// fail closed (ignored) rather than show a spurious error row.
const TRANSFER_PHASES = new Set(["start", "progress", "done", "cached", "error", "cancelled"]);
// A page that opens within this long shows no "Opening…" (openBlock): the
// page it leaves stays on screen until then, and the new one takes its
// place whole.
const OPENING_PILL_MS = 300;
// The open page's family (GET /pages/{id}/family) before it is read, or
// for a page outside any: no page above it and none inside.
const NO_FAMILY = { page: "", ancestors: [], subPages: [] };

// The compact layout ("phone" in the code): below 700px the desktop dock
// system is unusable, so the workspace switches to a single full-width panel
// with a bottom tab bar. The second clause keeps a rotated (landscape) phone
// in it — the width crosses 700px but a touch device that short is still a
// phone, and flipping to the desktop docks mid-rotation is jarring. The third
// gives a tablet held upright the same shell: two docks squeezed into 820px
// leave ~200px columns, while a full-width reader with full-screen Notes and
// Chat fits; turning it to landscape brings the docks back (docs/dev/ipad.md).
// ui-allow-start pointer-kind: the layout follows the screen (ui-design.md, Device questions)
const PHONE_MQ = "(max-width: 700px), (pointer: coarse) and (max-height: 500px), "
  + "(pointer: coarse) and (orientation: portrait) and (max-width: 1024px)";
// ui-allow-end
// A browser that declares itself mobile gets the phone layout regardless of
// the viewport numbers. "Request desktop site" flips this flag along with the
// UA, so it stays the escape hatch back to the desktop docks. Android tablets
// ("Android" without "Mobile") and iPads (desktop-class UA) are not phones:
// they get the compact layout only while upright, from PHONE_MQ.
const UA_MOBILE = navigator.userAgentData?.mobile
  ?? /iPhone|iPod|Android.+Mobile|Mobile.+Android/i.test(navigator.userAgent);
function useIsPhone() {
  const [mqPhone, setMqPhone] = useState(() => window.matchMedia(PHONE_MQ).matches);
  useEffect(() => {
    const mq = window.matchMedia(PHONE_MQ);
    const apply = () => setMqPhone(mq.matches);
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
  return UA_MOBILE || mqPhone;
}
// Safari on an iPhone or iPad (desktop-class UA plus touch points), not yet
// running as the installed home-screen app or inside a native app's shell
// (docs/dev/ipad.md).
const HOME_SCREEN_INSTALLABLE = (/iPad|iPhone/.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1))
  && !appShell();
// The installed app paints the status bar with theme-color, so it continues
// what sits under it: the topbar (chrome, --bg-page), or — on a phone's
// library with no tab open, where the compact layout drops the topbar — the
// library itself (content, --bg-surface). Read from the theme variables, not
// the elements: the topbar's background transitions, and a mid-transition
// read would lag a theme change. A native app's shell takes the same colour
// for the window behind its web view, the scheme for its system bars, and the
// theme's name (data-theme) for its bar and launcher: the desktop app's chrome
// pages, which paint themselves by that name.
function paintStatusBar(bareTop) {
  const bg = tokenHex(bareTop ? "--bg-surface" : "--bg-page");
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", bg);
  if (nativeHost()) {
    const root = document.documentElement;
    const scheme = root.getAttribute("data-scheme") === "dark" ? "dark" : "light";
    postToHost({ type: "theme", bg, scheme, name: root.getAttribute("data-theme") }).catch(() => {});
  }
}
// A colour token as #rrggbb. Most tokens are color-mix() expressions
// (tokens.css), which the theme-color meta can't take: the colour is
// resolved on a probe element and read back from one canvas pixel.
let tokenCanvas = null;
function tokenHex(name) {
  const probe = document.createElement("span");
  probe.style.color = `var(${name})`;
  document.body.append(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  tokenCanvas ||= document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  tokenCanvas.clearRect(0, 0, 1, 1);
  tokenCanvas.fillStyle = color;
  tokenCanvas.fillRect(0, 0, 1, 1);
  const [r, g, b] = tokenCanvas.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

// Drag payload prefix marking a folder drag (page cards drag their bare id),
// and the type a folder drag carries beside it, the one thing a drag-over
// can read (the custom order's drop beside a folder).
const FOLDER_DRAG = "gamma-folder:";
const FOLDER_DRAG_TYPE = "application/x-gamma-folder";
function startFolderDrag(e, id) {
  e.dataTransfer.setData("text/plain", FOLDER_DRAG + id);
  e.dataTransfer.setData(FOLDER_DRAG_TYPE, id);
  e.dataTransfer.effectAllowed = "move";
}
// A page drag (a library row or card, a Pages inside row) carries its bare
// id, what the folder and label drops read, and its own type beside it, so
// a page or the topbar's Home takes a page drag and nothing else (a file,
// a block's ⋮⋮, a text selection).
const PAGE_DRAG_TYPE = "application/x-gamma-page";
function startPageDrag(e, id) {
  e.dataTransfer.setData("text/plain", id);
  e.dataTransfer.setData(PAGE_DRAG_TYPE, id);
  e.dataTransfer.effectAllowed = "move";
}
const isPageDrag = (e) => Array.from(e.dataTransfer?.types || []).includes(PAGE_DRAG_TYPE);

// Home sort and kind filter are per-view choices: {"": "updated", "<folder
// id>": "title", "#<label id>": …} (root = ""). A view without a sort of its
// own inherits its nearest ancestor folder's; the kind filter falls back to
// "all".
const HOME_MAP_CODEC = {
  parse: (raw) => { try { const v = JSON.parse(raw); return v && typeof v === "object" ? v : undefined; } catch { return undefined; } },
  serialize: JSON.stringify,
};
const NO_VIEW_CHOICES = {};

// Home URL for the two browse filters, by id — a folder scope and a label
// filter can be active at once (a label view opened inside a folder).
function homeUrlFor(folder, label) {
  const q = [];
  const share = getShareToken(); // a folder share's library: the token names the workspace
  if (share) q.push(`share=${encodeURIComponent(share)}`);
  if (folder) q.push(`folder=${encodeURIComponent(folder)}`);
  if (label) q.push(label === NO_LABEL ? "unlabelled=1" : `label=${encodeURIComponent(label)}`);
  const url = q.length ? `/?${q.join("&")}` : "/";
  return share ? url : withWorkspace(url);
}

// Folder uploads file each file in the folders of its directory path, the
// picked/dropped root included: "papers/readout/x.pdf" → ["papers", "readout"].
function folderFromRelPath(relPath) {
  return (relPath || "").split("/").slice(0, -1).map((name) => name.trim()).filter(Boolean);
}

// Directory uploads occasionally expose a relative path as File.name (and
// some browsers also transmit it as the multipart filename). Folder placement
// is tracked separately; titles and original_filename must always be one leaf.
function uploadLeafName(file, fallback = "") {
  const raw = String(file?.name || file?.webkitRelativePath || "").replace(/\\/g, "/");
  return raw.slice(raw.lastIndexOf("/") + 1).trim() || fallback;
}

// --- Recently-viewed snapshots -------------------------------------------
// Composite the PDF viewer's visible area into a small JPEG data URL — the
// "last viewed place" cover for the recents cards. Reads the canvases pdf.js
// already painted, so it costs one drawImage per visible page. Returns null
// while the visible pages aren't painted yet (caller retries later).
// Size/quality keep a capture well under the server's MAX_SNAP_CHARS cap
// (gamma/routers/prefs.py) — a bigger thumbnail would be rejected with a 413.
const SNAP_WIDTH = 320;   // thumbnail backing width (px)
const SNAP_MAX_HEIGHT = 480;
const SNAP_QUALITY = 0.55;
// The recents queue length (cards in the "Recently viewed" strip); the local
// snapshot cache prunes to the same count. The server-side snapshot store
// keeps a few spares above this (PAGE_SNAPS_CAP = 30 in gamma/db.py).
const RECENTS_CAP = 24;
// How far past its 5 px a splitter takes a press, each side, for a mouse
// and a finger alike. The library's default for a finger (15 px) reached
// over half a window header, grip and close button included; 6 keeps the
// splitter grabbable and leaves the header its own.
const SASH_MARGINS = { coarse: 6, fine: 6 };
// The Settings panes of the AI group (old pane names resolve first): entering
// one loads the masked key list and the prompt drafts.
const AI_SETTINGS_PANES = ["ai", "assistant", "tools"];
const isAiPane = (pane) => AI_SETTINGS_PANES.includes(resolveSettingsPane(pane));
// A block's text for a chat chip (cursor block, attached block): its note,
// else its highlight quote.
const blockChipText = (b) => (b.content || "").trim() || blockQuote(b).trim() || t("(empty block)");
function captureViewerSnapshot(root) {
  const scroller = root?.querySelector(".pdfViewer");
  if (!scroller) return null;
  const vr = scroller.getBoundingClientRect();
  if (vr.width < 60 || vr.height < 60) return null;
  const k = SNAP_WIDTH / vr.width;
  const outH = Math.min(Math.round(vr.height * k), SNAP_MAX_HEIGHT);
  const capH = outH / k; // captured strip height in CSS px
  const out = document.createElement("canvas");
  out.width = SNAP_WIDTH; out.height = outH;
  const ctx = out.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, out.width, out.height);
  let covered = 0;
  for (const wrap of scroller.querySelectorAll(".pdfPageWrap")) {
    const r = wrap.getBoundingClientRect();
    if (r.bottom <= vr.top || r.top >= vr.top + capH) continue;
    // The canvas on screen, not the wrap's first: each page's canvases are
    // pairs that swap at every paint, the hidden one released.
    const canvas = basePixels(wrap);
    if (!canvas) continue;
    // Visible slice of this page in CSS px, mapped to the canvas backing
    // resolution on the source side and to the thumbnail on the dest side.
    const x0 = Math.max(vr.left, r.left), x1 = Math.min(vr.right, r.right);
    const y0 = Math.max(vr.top, r.top), y1 = Math.min(vr.top + capH, r.bottom);
    if (x1 <= x0 || y1 <= y0) continue;
    const kx = canvas.width / r.width, ky = canvas.height / r.height;
    try {
      ctx.drawImage(canvas,
        (x0 - r.left) * kx, (y0 - r.top) * ky, (x1 - x0) * kx, (y1 - y0) * ky,
        (x0 - vr.left) * k, (y0 - vr.top) * k, (x1 - x0) * k, (y1 - y0) * k);
    } catch { continue; }
    covered += (x1 - x0) * (y1 - y0);
  }
  // Mostly-blank captures (pages still rendering) are worse than keeping the
  // previous snapshot — require the strip to be at least 40% real page.
  if (covered < vr.width * capH * 0.4) return null;
  try { return out.toDataURL("image/jpeg", SNAP_QUALITY); } catch { return null; }
}

// Horizontal card strip. No arrow chrome: the wheel pans it sideways.
function CardCarousel({ label, children, className, guide }) {
  const trackRef = useWheelPan();
  return (
    <div className={"carouselRow" + (className ? " " + className : "")} data-guide={guide}>
      {label ? <div className="carouselLabel">{label}</div> : null}
      <div className="carouselTrack" ref={trackRef}>{children}</div>
    </div>
  );
}

// The share popover's invite box: the account directory as a picker, fetched
// only while the popover is open (the box mounts with it).
export default function App() {
  // Authorization must never mount library effects (saved-page restore,
  // autosave, navigation hotkeys). They can otherwise replace its URL.
  const params = new URLSearchParams(window.location.search);
  const requestId = params.get("gamma_oauth");
  const userCode = params.get("gamma_device");
  return requestId || userCode ? <McpAuthorization requestId={requestId} userCode={userCode} /> : <PageHostGate />;
}

// A page host (the share host's hostname per account, server-config's
// `page_host`; docs/dev/mirror.md "Publishing") serves published pages
// only: its path, /<slug>-<page id>, names the page, which opens in the share
// view as if its ?share= token were in the URL while the pretty address
// stays in the address bar. Any other path there is the share view's "not
// found". Everywhere else the app boots as before, with the server config it
// read here (a ?share= link knows it is a share view without asking).
function PageHostGate() {
  const [boot, setBoot] = useState(() => (new URLSearchParams(window.location.search).get("share") ? {} : null));
  useEffect(() => {
    if (boot) return undefined;
    let active = true;
    (async () => {
      let config = null;
      try { config = await (takeBootRead("config") || apiJson(`${API}/server-config`)); } catch {}
      if (!active) return;
      if (!pageHostUser(config?.page_host, window.location.hostname)) { setBoot({ serverConfig: config }); return; }
      let found = null;
      try {
        const q = new URLSearchParams({ host: window.location.host, path: window.location.pathname });
        const r = await fetch(`${API}/pages/resolve-public?${q}`);
        if (r.ok) found = await r.json();
      } catch {}
      if (!active) return;
      setShareView(found?.share || "");
      setBoot({ publicPage: found?.share ? found : { missing: true } });
    })();
    return () => { active = false; };
  }, [boot]);
  // The splash stays until the server's config is read.
  if (!boot) return <Splash />;
  return <LibraryApp publicPage={boot.publicPage || null} initialServerConfig={boot.serverConfig || null} />;
}

// `publicPage`: opened on a page host — {share, page_id} (resolved), or
// {missing: true}. `initialServerConfig`: GET /api/server-config, already read.
function LibraryApp({ publicPage = null, initialServerConfig = null }) {
  const params = new URLSearchParams(publicPage ? "" : window.location.search);
  const initialUrl = params.get("src") || params.get("url") || "";
  const initialShare = publicPage ? (publicPage.share || "") : (params.get("share") || "");
  const initialBlockId = params.get("block") || params.get("page") || "";
  // `page=` alone: a folder share opens that page of the folder even when
  // `block=` names a note or a highlight on it (a thread's link).
  // On a page host the pretty address names the shared page and `page=` a
  // page inside it (openSharedPage); nothing else of the query is read.
  const initialPageId = (publicPage ? new URLSearchParams(window.location.search) : params).get("page") || "";
  // A thread to open once its page's comments are in (Copy link, the inbox).
  const initialThreadId = params.get("thread") || "";
  const initialLabel = params.get("unlabelled") ? NO_LABEL : (params.get("label") || "");
  const initialFolder = params.get("folder") || "";
  // The desktop app's hand-over once it made a work folder (desktop/main.js
  // addDirectoryVault): its vault's tracking dialog opens (vault/useVault.js).
  const initialTrack = params.get("track") || "";
  // shareMode: this tab shows a page through a ?share= link (or a page
  // host's pretty address) — no account of its own, no library, no chat, no
  // prefs sync. readOnly: the block tree can't be edited; every share view
  // starts read-only and stays so unless the link resolves with edit rights
  // (Share → "They can: Edit notes").
  const shareMode = Boolean(initialShare) || Boolean(publicPage);
  const [readOnly, setReadOnly] = useState(shareMode);
  const [shareInfo, setShareInfo] = useState(null); // resolved share: {owner, role, canEdit, audience, viewer, deleting}
  // A folder share ({id, name}): the share view is then the home library
  // confined to that folder until a page opens; the topbar's home button
  // returns to it.
  const [sharedFolder, setSharedFolder] = useState(null);
  // "login" | "forbidden" | "missing" while the share can't open
  const [shareGate, setShareGate] = useState(publicPage?.missing ? "missing" : null);
  const [linkName, setLinkNameState] = useState(""); // the share view's display name when the viewer has no account
  const [renamingLink, setRenamingLink] = useState(false);

  // The workspace this tab works in and the ones the account may switch to
  // (from /api/session). Every API call carries the id (utils fetch
  // wrapper); a viewer role makes the tree read-only. wsReady gates the
  // data effects and the deep-link boot: nothing is fetched before the
  // workspace is known, or the first requests would land in the wrong one.
  const [workspace, setWorkspace] = useState(null);   // {id, name, role, personal, members}
  const [workspaces, setWorkspaces] = useState([]);
  const [wsReady, setWsReady] = useState(shareMode);
  // The library's folder and label trees (libraryUtils.libraryTree), read
  // with its pages (fetchHomeBlocks): every folder and label the home
  // views, menus and chips name, by id.
  const [libTree, setLibTree] = useState(EMPTY_TREE);
  const libTreeRef = useRef(libTree);
  libTreeRef.current = libTree;
  // What this viewer may do with the library itself (library/libraryAccess.js):
  // every affordance of the home listing asks this, never a role or a share.
  const lib = useMemo(
    () => libraryAccess({ shareMode, shareFolder: sharedFolder?.id || "", role: actingRole(workspace), tree: libTree }),
    [shareMode, sharedFolder, workspace, libTree],
  );
  // A folder's breadcrumb from the library's root (a folder share's folder)
  // down: each folder's id and name, and whether a separator precedes it.
  const folderCrumbs = (id) => folderChain(libTree, id).filter(lib.contains)
    .map((fid, i) => ({ id: fid, name: libTree.folders.get(fid).name, sep: i > 0 }));
  // The work folders in the folder tree (explorer/workFolders.js, the twin
  // of gamma/work_folders/filing.py): the folders whose files follow a
  // directory on disk, and what a person may do with them and their files.
  const workTree = useMemo(() => treeOf(libTree), [libTree]);
  const [workspaceUnavailable, setWorkspaceUnavailable] = useState(false);
  const wsId = workspace?.id || "";

  // Auth state: null=loading, false=logged out, {user, is_guest}=logged in
  const [authUser, setAuthUser] = useState(shareMode ? {user:"_public"} : null);
  // Chats and the recents covers are the workspace's, and only its editors
  // and owners write them: a viewer's conversation stays in the tab (ChatDock
  // `canSave`), its covers in this browser. So does everyone's while the
  // workspace is being deleted (actingRole).
  const canWriteWorkspace = !shareMode && actingRole(workspace) !== "viewer";
  const chatSession = useMemo(() => createChatSession((key, messages, updatedAt) => {
    if (getExpectedUser() !== authUser?.user || getCurrentWorkspace() !== wsId) {
      throw new Error("The account or workspace changed.");
    }
    if (!canWriteWorkspace) return Promise.resolve({ updated_at: updatedAt });
    // Conditional on the version this tab's copy is based on: a 409 brings
    // the stored conversation for the session to merge (chat/chatSession.js).
    return apiJson(`${API}/chats/${encodeURIComponent(key)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, updated_at: updatedAt }),
    });
  }), [authUser?.user, wsId, canWriteWorkspace]);
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginError, setLoginError] = useState(""); // a message, or {text, field} (LoginPage)
  // Username that now owns the browser session when it's no longer this tab's
  // user (someone logged into another account from a second tab) — freezes the
  // tab behind SessionConflictPage until reload.
  const [sessionConflict, setSessionConflict] = useState(null);

  // This tab's real signed-in user — null while loading, logged out, or in a
  // public share view.
  const sessionUser = authUser && authUser.user && authUser.user !== "_public" ? authUser.user : null;

  // Publish this tab's identity: the X-Gamma-User guard header on API calls
  // (utils.js fetch wrapper) plus a localStorage beacon other tabs listen to.
  useEffect(() => {
    setExpectedUser(sessionUser);
    if (sessionUser) {
      try { localStorage.setItem("gamma-active-user", sessionUser); } catch {}
    }
  }, [sessionUser]);

  // Detect the session being taken over by another account. Three signals:
  // the backend's 409 on a guarded API call, the localStorage beacon from the
  // tab that logged in, and a session re-check when this tab regains focus
  // (throttled — alt-tab flapping must not hammer the server).
  useEffect(() => {
    if (shareMode || !sessionUser) return;
    function conflict(who) {
      if (who && who !== sessionUser) setSessionConflict(who);
      else if (!who) setAuthUser(false); // logged out elsewhere → login page
    }
    function onMismatch(e) { conflict(e.detail?.user || ""); }
    function onStorage(e) {
      if (e.key === "gamma-active-user" && e.newValue !== null) conflict(e.newValue);
    }
    let lastCheck = 0;
    function onFocus() {
      const now = Date.now();
      if (now - lastCheck < 15000) return;
      lastCheck = now;
      apiJson(`${API}/session`).then((d) => conflict(d.user || "")).catch(() => {});
    }
    window.addEventListener("gamma-user-mismatch", onMismatch);
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("gamma-user-mismatch", onMismatch);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
    };
  }, [sessionUser, shareMode]);

  // Explicit ?ws= is authoritative: an inaccessible workspace shows a gate.
  // For a deep link without one, try the
  // workspace holding that page; else the last one used in this browser;
  // else the personal workspace.
  async function chooseWorkspace(user, list, dflt) {
    const ids = new Set(list.map((w) => w.id));
    const urlWs = new URLSearchParams(window.location.search).get("ws") || "";
    if (urlWs) return ids.has(urlWs) ? urlWs : null;
    if (!urlWs && initialBlockId) {
      try {
        const d = await apiJson(`${API}/workspaces/find-page/${encodeURIComponent(initialBlockId)}`);
        if (ids.has(d.workspace_id)) return d.workspace_id;
      } catch {}
    }
    let last = "", importing = false;
    try { last = localStorage.getItem(`gamma-last-ws:${user}`) || ""; } catch {}
    // a share view's "Add to my library" waiting to import (IMPORT_SHARE)
    // goes into a workspace this account may write to
    try { importing = Boolean(sessionStorage.getItem(IMPORT_SHARE)); } catch {}
    const writable = (id) => !importing || list.find((w) => w.id === id)?.role !== "viewer";
    if (last && ids.has(last) && writable(last)) return last;
    return ids.has(dflt) ? dflt : (list[0]?.id || null);
  }

  function applyWorkspace(user, id, list) {
    const w = list.find((x) => x.id === id);
    if (!w) throw new Error("Workspace is unavailable");
    setCurrentWorkspace(id);
    setSessionScope(user, id);
    setWorkspace(w);
    setWorkspaces(list);
    setReadOnly(actingRole(w) === "viewer");
    if (w.deleting) sayDeleting();
    try { localStorage.setItem(`gamma-last-ws:${user}`, id); } catch {}
    // Keep the id in the URL so a reload or a copied link lands here again.
    const url = new URL(window.location.href);
    if (url.searchParams.get("ws") !== id) {
      url.searchParams.set("ws", id);
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    }
    setWsReady(true);
  }

  // What the login page offers besides a password — a share link's sign-in
  // gate too (its Gamma Cloud button): read once, unauthenticated.
  const [serverConfig, setServerConfig] = useState(initialServerConfig);
  useEffect(() => {
    if (initialServerConfig) return;
    let active = true;
    apiJson(`${API}/server-config`).then((c) => { if (active) setServerConfig(c); }).catch(() => {});
    return () => { active = false; };
  }, []);

  // The load's first session is the one main.jsx asked for (app/boot.js).
  // A session that signs this tab in (that one, or a sign-in's) brings the
  // account's theme, on before the library draws, and its language, each
  // only if this app knows it (boot.js adoptAccountLook): a language other
  // than the running one reloads into it now, before the library shows
  // (main.jsx has already taken it for a browser new to the account).
  // Then the reads the first screen needs go out before the app renders the
  // session, so they lead what its parts ask for as they mount (the browser
  // keeps six requests in flight to the server): the library listing, an
  // account's preferences as one request (prefs.js preloadPrefs; the same
  // account's session read again keeps the ones it has) and the AI models.
  async function checkSession() {
    try {
      const data = await (takeBootRead("session") || apiJson(`${API}/session`));
      if (data.user) {
        if (!authUser?.user && data.profile) {
          const { theme, language } = adoptAccountLook(data.profile);
          if (language && resolveLocale(language) !== getLocale()) { window.location.reload(); return; }
          if (theme) setTheme(theme);
        }
        const list = data.workspaces || [];
        const chosen = await chooseWorkspace(data.user, list, data.default_workspace || "");
        if (!chosen) {
          setWorkspaceUnavailable(true);
          return;
        }
        setWorkspaceUnavailable(false);
        applyWorkspace(data.user, chosen, list);
        fetchHomeBlocks({ still: true });
        if (data.user !== authUser?.user) preloadPrefs(data.user, { guest: data.is_guest });
        refreshAiModels();
        setAuthUser({ user: data.user, is_guest: data.is_guest, is_admin: data.is_admin, build: data.build,
          guest_expires_at: data.guest_expires_at || "" });
      } else {
        setAuthUser(false);
      }
    } catch {
      setAuthUser(false);
    }
  }

  // Switching is a navigation: tabs, recents, the open page and the live
  // session all belong to the library being left, so the tab reloads on the
  // other workspace's URL, opening `page` of it when one is named.
  function switchWorkspace(id, page = "") {
    if (!id || id === wsId) return;
    leaveCurrentPage();
    const open = page ? `&block=${encodeURIComponent(page)}` : "";
    window.location.href = `${window.location.pathname}?ws=${encodeURIComponent(id)}${open}`;
  }

  // Effective storage quota + usage for the session user (GET /api/quota):
  // feeds the storage meters. Refreshed on login and after uploads; the
  // Settings displays fetch their own fresh copy.
  const [quotaInfo, setQuotaInfo] = useState(null); // {quota_mb, used_bytes}
  const refreshQuota = useCallback(() => {
    if (shareMode) return;
    apiJson(`${API}/quota`).then(setQuotaInfo).catch(() => {});
  }, [shareMode]);
  useEffect(() => {
    if (authUser?.user && !shareMode) refreshQuota();
    else setQuotaInfo(null);
  }, [authUser?.user, shareMode, refreshQuota]);

  // `then` runs after a successful sign-in; the default re-reads the session
  // rather than hand-building the auth state — it carries flags login
  // doesn't return (is_admin).
  async function doLogin(e, then = checkSession) {
    e?.preventDefault();
    setLoginError("");
    let res;
    try {
      res = await fetch(`${API}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUser, password: loginPass }),
        credentials: "include",
      });
    } catch { setLoginError(t("Can't reach the server.")); return; }
    if (!res.ok) {
      setLoginError(res.status === 401
        ? { text: t("That username and password don't match. Check both, or ask your server's admin to reset the password."), field: "password" }
        : res.status === 429 ? t("Too many attempts. Wait a few minutes, then try again.") : t("Login failed"));
      return;
    }
    try { await then(); } catch { setLoginError(t("Login failed")); }
  }

  async function doGuestLogin() {
    try {
      const res = await fetch(`${API}/login-guest`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        // A refusal says why (the guest cap, the rate limit): show that.
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch {}
        setLoginError(typeof detail === "string" && detail ? detail : t("Guest login failed"));
        return;
      }
      // Each guest login mints a fresh account with its own workspace: read
      // the session like any sign-in (workspace, expiry).
      await checkSession();
    } catch { setLoginError(t("Guest login failed")); }
  }

  // A share link that needs an account (signed-in users / specific people):
  // sign in right here, then resolve the link again with the new session.
  const doShareLogin = (e) => doLogin(e, async () => { setShareGate(null); await resolveShare(initialShare); });
  async function shareSwitchAccount() {
    try { await fetch(`${API}/logout`, { method: "POST", credentials: "include" }); } catch {}
    setLoginUser(""); setLoginPass(""); setLoginError("");
    setShareGate("login");
  }
  // A dead link's "Sign in to your library": sign in right here, then open
  // the library instead of the link.
  const doLibraryLogin = (e) => doLogin(e, async () => { window.location.assign("/"); });
  function librarySignIn() {
    setLoginError("");
    setShareGate("signin");
  }

  // Export a workspace as a backup zip (`wsId` — any of mine; the open one
  // by default), or every personal workspace at once (one export zip per
  // workspace inside): a background job (workspace-export) whose zipping
  // shows in the pill and Background tasks, and whose file downloads by
  // itself once it is ready — the browser's download manager fetches it,
  // however big (docs/dev/tasks.md).
  function exportWorkspace(wsId, withUploads) {
    return startWorkspaceExport({ ws: wsId || getCurrentWorkspace(), uploads: withUploads });
  }
  function exportAll(withUploads) {
    return startWorkspaceExport({ all: true, uploads: withUploads });
  }

  // Restore an exported zip into a workspace (`wsId` — any of mine; the open
  // one by default). mode "replace": pages + chats are replaced by the
  // backup (the server keeps the replaced state as a "Before restore"
  // snapshot), the backup's files come back. mode "merge": only pages/chats
  // missing there are added, existing data wins. Restoring into the OPEN
  // workspace reloads afterwards — every piece of in-memory state (home
  // feed, tabs, chats) is stale — while another workspace's restore leaves
  // this one alone.
  function importWorkspace(wsId, mode = "replace") {
    const target = wsId && wsId !== getCurrentWorkspace() ? wsId : null;
    const who = (target ? workspaces.find((w) => w.id === target)?.name : workspace?.name) || t("this workspace");
    pickFiles({ accept: ".zip,application/zip" }, ([f]) => {
      setConfirmBox(mode === "merge" ? {
        title: T("Merge backup"),
        message: (
          <>{t("Merge “{file}” ({size}) into the workspace “{workspace}”? Pages and chats from the backup that don't exist there yet will be {added}. Everything already in that workspace is {kept}.", {
            file: f.name, size: fmtBytes(f.size), workspace: who, added: <b>{t("added")}</b>, kept: <b>{t("kept unchanged")}</b> })}</>
        ),
        confirmLabel: t("Merge"),
        onConfirm: () => runBackupImport(f, mode, target),
      } : {
        title: T("Replace all data"),
        message: (
          <>{t("Restore “{file}” ({size}) into the workspace “{workspace}”? {replaced} Files the backup holds come back; files only the current notes use are cleaned up later. {final}", {
            file: f.name, size: fmtBytes(f.size), workspace: who,
            replaced: <b>{t("ALL of that workspace's notes and chats will be REPLACED by the backup.")}</b>,
            final: <b>{t("The current state is saved first as a “Before restore” snapshot in Settings → Backups.")}</b> })}</>
        ),
        confirmLabel: t("Replace"),
        danger: true,
        onConfirm: () => runBackupImport(f, mode, target),
      });
    });
  }

  // A guest account has no password, so logging out deletes it and its
  // workspace on the spot (docs/dev/guests.md): say so before it happens.
  function confirmGuestLogout() {
    setOpenPopover(null);
    setConfirmBox({
      title: T("Log out and delete this workspace?"),
      message: t("A guest can't sign back in: logging out deletes this workspace and everything in it now."),
      confirmLabel: t("Log out and delete"),
      danger: true,
      onConfirm: doLogout,
    });
  }

  async function doLogout() {
    // Flush pending edits while the session is still valid. Setting authUser
    // false after the cookie is removed performs a local-only workspace
    // teardown, without starting reads that race logout.
    leaveCurrentPage();
    // Translation jobs outlive the viewer: stop them before the session goes.
    endTranslations();
    await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
    // Logout kills the browser-wide session: tell other tabs of this account
    // so they drop to the login page instead of failing on their next save.
    try { localStorage.setItem("gamma-active-user", ""); } catch {}
    setAuthUser(false);
  }

  useEffect(() => {
    if (!shareMode) checkSession();
  }, [shareMode]);

  const [inputUrl, setInputUrl] = useState(initialUrl); // current page's source URL (shown in page properties)
  const [addUrl, setAddUrl] = useState(""); // "+" popover: URL to open
  const [pdfUrl, setPdfUrl] = useState("");
  // The place a link opens its page at: a PDF passage ({kind: "citation"})
  // or a text file's lines ({kind: "line"}, docs/dev/pdf_citations.md "Line
  // links"); a pasted one on a cold load, a share's too, opens there.
  const [pageTarget, setPageTarget] = useState(() => {
    const link = parseGammaLink(window.location.href, window.location.origin);
    return link?.kind === "citation" || link?.kind === "line" ? link : null;
  });
  const [focusedBlockId, setFocusedBlockId] = useState("");
  const [focusedBlock, setFocusedBlock] = useState(null);
  // The open page's document (documents/pageDocument.js): THE switch for
  // layout and page-level affordances. `docId`: the hash of the PDF it owns
  // ("" for a copy, which shows another page's file, and for a document of
  // any other format: its readers mean a PDF).
  const doc = useMemo(() => pageDocument(focusedBlock), [focusedBlock]);
  const docId = isPdf(doc) ? ownHash(doc) : "";
  // The open page's filing, ids: `properties.labels` / `properties.folders`.
  const [pageLabels, setPageLabels] = useState([]);
  const [pageFolders, setPageFolders] = useState([]);
  const [labelEditing, setLabelEditing] = useState(false);
  const [labelInput, setLabelInput] = useState("");
  const [labelSuggestionIdx, setLabelSuggestionIdx] = useState(-1);
  const [labelFilter, setLabelFilter] = useState(initialLabel);
  // File-browser home: folders are blocks of the folder tree (libTree), and
  // a page is filed by id — `properties.folders` lists the folders it sits
  // in, several at once (soft links); no folders → the library root.
  // folderFilter is the open folder's id ("" = the root), labelFilter the
  // open label's.
  const [folderFilter, setFolderFilter] = useState(initialFolder);
  // The drop target a drag is over: a folder's or label's id (`before:` /
  // `after:` one on a folder's edge, `crumb:` one of the folder path, the
  // back rows' "__up__" / "__label_up__"), `page:<id>` a page, "library"
  // the topbar's Home; null for none.
  const [folderDragOver, setFolderDragOver] = useState(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  // Home feed: per-folder sort criterion + how many rows are rendered (grows
  // on scroll). Changing the sort only pins it for the folder being viewed;
  // folders without an explicit choice inherit from their nearest ancestor.
  // Sort and kind are per-VIEW: the key is the folder's id, or "#<label id>"
  // in a label view. A view with no sort of its own inherits from its
  // nearest ancestor folder ("" = library root); labels are flat, so they
  // inherit the root's choice.
  const homeScope = labelFilter ? `#${labelFilter}` : folderFilter;
  function scopedPref(map, dflt) {
    if (labelFilter) return map[homeScope] || map[""] || dflt;
    const own = folderChain(libTree, folderFilter).reverse().find((id) => map[id]);
    return map[own] || map[""] || dflt;
  }
  const [homeSortMap, setHomeSortMap] = usePersistedState("gamma-home-sort-map", NO_VIEW_CHOICES, HOME_MAP_CODEC);
  const homeSort = useMemo(() => scopedPref(homeSortMap, "updated"), [homeSortMap, homeScope, libTree]);
  function changeHomeSort(v) { setHomeSortMap((m) => ({ ...m, [homeScope]: v })); }
  // Home layout: "list" (block-style rows) or "grid" (icon tiles).
  const [homeView, changeHomeView] = usePersistedState("gamma-home-view", "list");
  // Kind filter: "all" (folders + files), "folders", "files", or "labels" (the
  // labels used in this view, browsable like folders) — keyed per view like
  // the sort, but not inherited: a view with no entry of its own shows "all".
  const [homeKindsMap, setHomeKindsMap] = usePersistedState("gamma-home-kinds-map", NO_VIEW_CHOICES, HOME_MAP_CODEC);
  const homeKinds = homeKindsMap[homeScope] || "all";
  function changeHomeKinds(v) { setHomeKindsMap((m) => ({ ...m, [homeScope]: v })); }
  // Listing search box (left of the sort pill): live, per-view, not persisted —
  // matches float to the top of the current sort, the rest dim in place.
  const [homeQuery, setHomeQuery] = useState("");
  useEffect(() => { setHomeQuery(""); }, [homeScope]);
  const HOME_PAGE_CHUNK = 30;
  const [homeShowCount, setHomeShowCount] = useState(HOME_PAGE_CHUNK);
  useEffect(() => { setHomeShowCount(HOME_PAGE_CHUNK); }, [folderFilter, labelFilter, homeSort, homeKinds, homeQuery]);
  const loadMoreRef = useRef(null);

  // Load per-user browser prefs (tabs, recents) on login / account switch.
  // Keyed by account AND workspace ("user@ws"): open tabs and recents name
  // pages of one library.
  useEffect(() => {
    const u = authUser?.user && wsId ? `${authUser.user}@${wsId}` : "";
    tabHistoryRef.current = [];
    // Leaving the account or workspace (here too when the session expired
    // or another tab signed in): its translation jobs stop and its
    // documents' translations go with it.
    if (prefsUserRef.current && prefsUserRef.current !== u) endTranslations();
    if (!u || shareMode) {
      // Losing the session (logout button, expiry in another tab) must fully
      // close the workspace: a stale focusedBlockId would get merged into the
      // NEXT account's tab strip (applyServerTabs keeps the on-screen page) and
      // pushed to their server prefs, and the nav-back stack would reopen this
      // account's pages. Guarded on a previous user so the initial
      // session-loading render doesn't wipe the deep link / saved session.
      if (prefsUserRef.current && !shareMode) {
        // Local teardown only: there is no authenticated account to refresh.
        goHome(false);
        setNavStack([]);
      }
      prefsUserRef.current = "";
      setOpenTabs([]);
      setTabLayout(singleLayout([]));
      setRecentViews([]);
      pageSnapsRef.current = {};
      setPageSnaps({});
      snapsSyncedRef.current = false;
      recentsSyncRef.current = "";
      readPosRef.current = {};
      readPosLoadedRef.current = false;
      return;
    }
    prefsUserRef.current = u;
    tabsSyncRef.current = "";
    recentsSyncRef.current = "";
    snapsSyncedRef.current = false;
    // Local cache first for instant paint…
    let localTabs = [];
    try { localTabs = JSON.parse(localStorage.getItem(`gamma-tabs:${u}`) || "[]"); } catch {}
    if (!Array.isArray(localTabs)) localTabs = [];
    tabsReadRef.current.add(localTabs);
    setOpenTabs(localTabs);
    let localLayout = null;
    try { localLayout = JSON.parse(localStorage.getItem(`gamma-tab-layout:${u}`) || "null"); } catch {}
    setTabLayout(normalizeLayout(localLayout, localTabs.map((tab) => tab.id)));
    let localRecents = [];
    try { localRecents = JSON.parse(localStorage.getItem(`gamma-recent-views:${u}`) || "[]"); } catch {}
    if (!Array.isArray(localRecents)) localRecents = [];
    homeReadRef.current.add(localRecents);
    setRecentViews(localRecents);
    let localSnaps = {};
    try { localSnaps = JSON.parse(localStorage.getItem(`gamma-page-snaps:${u}`) || "{}"); } catch {}
    pageSnapsRef.current = localSnaps && typeof localSnaps === "object" ? localSnaps : {};
    setPageSnaps(pageSnapsRef.current);
    readPosLoadedRef.current = false;
    try { readPosRef.current = JSON.parse(localStorage.getItem(`gamma-read-pos:${u}`) || "{}"); } catch { readPosRef.current = {}; }
    // …then the server copy, which wins (tabs sync across browsers). An
    // account that has never synced seeds the server with this browser's tabs.
    readPref("open-tabs", authUser.user).then((d) => {
      if (prefsUserRef.current !== u) return;
      if (d.updated_at) applyServerTabs(u, d.value, d.updated_at);
      else if (Array.isArray(localTabs) && localTabs.length) pushTabsToServer(localTabs);
    }).catch(() => {});
    // Recents strip + its covers, together: the snap-prune effect compares
    // covers against the queue, so the server queue must be applied before
    // snapsSyncedRef lets the prune run — else a pre-sync local queue could
    // sweep covers another device just captured.
    Promise.allSettled([
      readPref("recent-views", authUser.user),
      apiJson(`${API}/page-snaps`),
    ]).then(([rv, sn]) => {
      if (prefsUserRef.current !== u) return;
      if (rv.status === "fulfilled") {
        if (rv.value.updated_at) applyServerRecents(u, rv.value.value, rv.value.updated_at);
        else if (localRecents.length) pushRecentsToServer(localRecents);
      }
      if (sn.status === "fulfilled") {
        mergePageSnaps(sn.value.snaps, { heal: true });
        snapsSyncedRef.current = true;
      }
    });
    readPref("read-pos", authUser.user).then((d) => {
      if (prefsUserRef.current !== u) return;
      readPosLoadedRef.current = true;
      if (mergeReadPos(u, d.value)) pushReadPosSoon(u);
    }).catch(() => { if (prefsUserRef.current === u) readPosLoadedRef.current = true; });
  }, [authUser?.user, wsId, shareMode]);

  // File a page: `filing` sets its root's `folders` and/or `labels` — the
  // whole list of ids, from the ones the trees have (an empty list unfiles)
  // — a `set` op through PUT /blocks/{id} (`filingProps`, the patch).
  const filingProps = (filing) => Object.fromEntries(Object.entries(filing).map(([key, ids]) => [key, ids.length ? ids : null]));
  async function filePage(pageId, filing) {
    await apiJson(`${API}/blocks/${pageId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ properties: filingProps(filing) }),
    });
  }
  // Many pages in one request (POST /blocks/bulk): `body` is `{trash: ids}`
  // or `{patches: [{id, properties}]}`; resolves runBulk's outcomes.
  async function sendBulk(body) {
    const answer = await apiJson(`${API}/blocks/bulk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return bulkOutcomes(answer.results);
  }
  // Pages patched in bulk: `patch(item)` is the properties patch of
  // `idOf(item)`, as PUT /blocks/{id} takes it.
  const patchPages = (items, idOf, patch, opts) => runBulk(items,
    (chunk) => sendBulk({ patches: chunk.map((item) => ({ id: idOf(item), properties: patch(item) })) }), opts);
  // One batch on the folder or the label tree (`tree`: "folders" /
  // "labels"; POST /pages/{tree}/ops): a new folder or label, a rename, a
  // move or a reorder, a pin — none of it touches a page. The listing is
  // read again after it: the home view follows the trees by re-reading
  // them on its own writes and when the window comes back (onWake).
  async function treeOps(tree, ops) {
    await apiJson(`${API}/pages/${tree}/ops`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client: "", ops }),
    });
    await fetchHomeBlocks();
  }
  // The writes that make folders and labels by name run one after another,
  // each on the trees the one before left (libTreeRef, which
  // fetchHomeBlocks sets as it reads them): a name committed twice — Enter,
  // then the field's blur — is made once.
  const nameWritesRef = useRef(Promise.resolve());
  function afterNameWrites(task) {
    const run = nameWritesRef.current.then(task);
    nameWritesRef.current = run.catch(() => {});
    return run;
  }
  // The folders at `paths` (each a list of names) below `under` ("" = the
  // top level), made where missing in one batch — a folder already there is
  // reused, a new one goes last among its siblings, as the server's
  // blocks_store.folder_inserts does. Returns a lookup: names → folder id.
  const ensureFolders = (paths, under = "") => afterNameWrites(async () => {
    const tree = libTreeRef.current;
    const kids = new Map();
    const childrenOf = (parent) => {
      if (!kids.has(parent)) kids.set(parent, siblingFolders(tree, parent));
      return kids.get(parent);
    };
    const ops = [], made = new Map();
    for (const names of paths) {
      let parent = under;
      for (const name of names) {
        const found = findNamed(childrenOf(parent), name);
        if (found) { parent = found.id; continue; }
        const id = makeId();
        ops.push({ op: "insert", id, parent: parent || "folders", content: name.trim() });
        childrenOf(parent).push({ id, name: name.trim() });
        parent = id;
      }
      made.set(names.join("\n"), parent);
    }
    if (ops.length) await treeOps("folders", ops);
    return (names) => made.get(names.join("\n")) || under;
  });
  // The label called `name`, made when there is none; its id.
  const makeLabel = (name) => afterNameWrites(async () => {
    const found = findNamed(libTreeRef.current.labels.values(), name);
    if (found) return found.id;
    const id = makeId();
    await treeOps("labels", [{ op: "insert", id, parent: "labels", content: name.trim() }]);
    return id;
  });

  // "New folder" in the open folder: a block of the folder tree, empty and
  // shared like any. A name a sibling has already makes nothing.
  async function commitNewFolder() {
    const name = newFolderName.trim();
    setNewFolderOpen(false);
    setNewFolderName("");
    if (!name) return;
    if (findNamed(siblingFolders(libTree, folderFilter), name)) {
      setStatus(t("There is already a folder “{name}” here.", { name }));
      return;
    }
    try {
      await ensureFolders([[name]], folderFilter);
    } catch (err) {
      setStatus(t("Could not make the folder: {message}", { message: err.message }));
    }
  }

  // --- Home file-manager: multi-select + copy/move/delete, folder rename ---
  const [selectedPages, setSelectedPages] = useState(() => new Set());
  const [selectedFolders, setSelectedFolders] = useState(() => new Set());
  const [selectedLabels, setSelectedLabels] = useState(() => new Set());
  // Select mode (the list bar's Select): a click or a tap toggles, as Ctrl
  // does — the way to pick several where there is no Ctrl.
  const [selecting, setSelecting] = useState(false);
  const lastPageClickRef = useRef(null); // anchor for shift-range selection
  const [homeMenu, setHomeMenu] = useState(null); // {kind: "page" | "folder" | "label", id, name?, x, y}
  // The page menu's "New label…" name while it is typed (null: not typing).
  const [homeMenuLabelDraft, setHomeMenuLabelDraft] = useState(null);
  useEffect(() => { setHomeMenuLabelDraft(null); }, [homeMenu]);
  const [folderRenaming, setFolderRenaming] = useState(null); // {id, draft}
  const [labelRenaming, setLabelRenaming] = useState(null); // {id, draft}

  // Ending the selection ends Select mode with it.
  function clearSelection() { setSelectedPages(new Set()); setSelectedFolders(new Set()); setSelectedLabels(new Set()); setSelecting(false); }

  // A click that toggles one item: Ctrl/Cmd, or any click in Select mode.
  const toggles = (e) => selecting || !!e && (e.ctrlKey || e.metaKey);
  // A finger's tap OPENS, as in a phone's file manager, and a mouse click
  // selects: so one tap opens on touch, where a double tap is not dependable.
  const isTap = (e) => !!e && !toggles(e) && !e.shiftKey && lastPointer() !== "mouse";

  // Modern file-manager semantics: plain click SELECTS, double-click opens
  // (a tap opens, see isTap). Ctrl/Cmd toggles a single item; Shift extends a
  // range from the last click.
  function handlePageClick(pageBlock, e) {
    const id = pageBlock._pageId;
    if (!id) return;
    if (isTap(e)) { if (homeEditingId !== id) openPage(id); return; }
    setSelectedFolders(new Set());
    setSelectedLabels(new Set());
    if (toggles(e)) {
      setSelectedPages((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
      });
      lastPageClickRef.current = id;
      return;
    }
    if (e && e.shiftKey && lastPageClickRef.current) {
      const order = homeVisiblePages.map((b) => b._pageId);
      const a = order.indexOf(lastPageClickRef.current);
      const b = order.indexOf(id);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        setSelectedPages((prev) => {
          const next = new Set(prev);
          for (let i = lo; i <= hi; i++) next.add(order[i]);
          return next;
        });
        return;
      }
    }
    // Plain click: select just this page (replacing any prior selection).
    lastPageClickRef.current = id;
    setSelectedPages(new Set([id]));
  }

  // Right-click on ANY page surface (grid card, list row, recents/pinned
  // strips): keep an existing multi-selection, otherwise select just
  // this page, then open the shared page menu at the cursor.
  function openPageMenu(id, name) {
    return (e) => {
      e.preventDefault();
      setSelectedPages((prev) => (prev.has(id) ? prev : new Set([id])));
      lastPageClickRef.current = id;
      // el: what it opened from, which the Properties popover gives the
      // focus back to
      setHomeMenu({ kind: "page", id, name, x: e.clientX, y: e.clientY, el: e.currentTarget });
    };
  }

  // A Pages inside row's menu (editor/SubPagesPanel.jsx): the same page
  // menu, for that one page, the library's selection let go (the panel
  // lists none of it). `panel` marks where it opened: its Open and the
  // rows that open the page first keep Back to the page the panel is on,
  // and it has no Rename, which is the listing's.
  function openSubPageMenu(id, name) {
    return (e) => {
      e.preventDefault();
      clearSelection();
      setHomeMenu({ kind: "page", id, name, x: e.clientX, y: e.clientY, el: e.currentTarget, panel: true });
    };
  }

  // Double-click / Enter / context-menu "Open": the old single-click behavior.
  // `block`: a block of the page to land on (openBlock).
  function openPage(id, block = "") {
    if (!id) return;
    clearSelection();
    openBlock(id, { restoreScroll: true, block });
  }

  // Container (folder or label) single-click selects, Ctrl/Cmd toggles within
  // its own kind; either way the other kinds' selections clear. Double-click
  // navigates in. Labels are the flat mirror of folders — same semantics,
  // same cards and rows, no nesting.
  function handleContainerClick(kind, id, e) {
    if (kind === "folder" && folderRenaming?.id === id) return;
    if (isTap(e)) { if (kind === "folder") openFolder(id); else openLabel(id); return; }
    const setOwn = kind === "folder" ? setSelectedFolders : setSelectedLabels;
    setSelectedPages(new Set());
    (kind === "folder" ? setSelectedLabels : setSelectedFolders)(new Set());
    if (toggles(e)) {
      setOwn((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
      });
    } else {
      setOwn(new Set([id]));
    }
  }
  const handleFolderClick = (id, e) => handleContainerClick("folder", id, e);
  const handleLabelClick = (id, e) => handleContainerClick("label", id, e);
  function openFolder(id) {
    id = lib.clamp(id);
    clearSelection();
    setFolderFilter(id);
    setLabelFilter("");
    window.history.replaceState(null, "", homeUrlFor(id, ""));
  }
  // Opening a label KEEPS the folder scope, so a label opened inside a folder
  // reads as "this folder, narrowed to that label".
  function openLabel(id, folder = folderFilter) {
    clearSelection();
    setLabelFilter(id);
    if (folder !== folderFilter) setFolderFilter(folder);
    window.history.replaceState(null, "", homeUrlFor(folder, id));
  }
  function closeLabel() {
    clearSelection();
    setLabelFilter("");
    window.history.replaceState(null, "", homeUrlFor(folderFilter, ""));
  }
  // Commit a grid-tile rename (list rows rename inline via the block editor).
  function commitPageRename(id, text) {
    setHomeEditingId(null);
    const tt = (text || "").trim();
    const cur = knownBlocks.find((b) => b.id === id)?.content || "";
    if (!tt || tt === cur) return;
    if (work.refused([id], (rules) => rules.rename, "named")) return;
    const back = (b) => (b.id === id && b.content === tt ? { ...b, content: cur } : b);
    setHomeBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, content: tt } : b)));
    patchKnown(id, { content: tt });
    apiJson(`${API}/blocks/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: tt }),
    }).catch((err) => {
      setHomeBlocks((prev) => prev.map(back));
      patchKnown(id, { content: cur });
      setStatus(t("Rename failed: {err}", { err: err }));
    });
  }

  // Deep-copy a page: new root block + a subtree clone with fresh block ids
  // (a highlight's id is its block's; same-page references remapped). The copy
  // shows the same file as a copy (documents/pageDocument.js copyDocument),
  // which it does not own: a PDF has one page, the one "by-doc" lookups and
  // the extension find. A copy of a work folder's note is a library page:
  // without the vault's stamps, filed in the library's folders it was in.
  async function duplicatePage(pageId) {
    const data = await apiJson(`${API}/blocks/${pageId}/subtree`);
    const src = data.block || {};
    // (nor what a Zotero link says of the page it keeps, which a copy is not: docs/dev/zotero_sync.md;
    // nor its Notion id, or an import of that page would find the copy)
    const { doc: _srcDoc, zotero_pdf: _zoteroPdf, zotero_readonly: _readonly, zotero_author: _author, notion_id: _notionId, ...own } = src.properties || {};
    const properties = libraryCopy(own, workTree);
    const copy = copyDocument(pageDocument(src));
    if (copy) properties.doc = copy;
    const created = await apiJson(`${API}/blocks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_id: "root", content: `${src.content || "Untitled"} (copy)`, properties }),
    });
    const ids = new Map();
    const clone = (list) => (list || []).map((b) => {
      const id = makeId();
      ids.set(b.id, id);
      const { zotero_readonly: _readonly, zotero_author: _author, ...properties } = b.properties || {};
      return { ...b, id, properties, children: clone(b.children) };
    });
    const remap = (list) => {
      for (const b of list || []) {
        const p = b.properties;
        if (ids.has(p.linked_highlight_id)) p.linked_highlight_id = ids.get(p.linked_highlight_id);
        remap(b.children);
      }
    };
    const cloned = clone(src.children);
    remap(cloned);
    await apiJson(`${API}/blocks/${created.id}/children`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blocks: cloned }),
    });
    return created.id;
  }
  // A batch's closing status (library/pageBatch.js batchText).
  function batchStatus(said, result, total) {
    const text = batchText(said, result, total);
    if (text) setStatus(text);
  }
  // One copy at a time: each reads its page whole and writes it again.
  async function duplicatePages(ids) {
    clearSelection();
    const result = await runBatch(ids, duplicatePage, { name: t("Duplicate {n} pages", { n: ids.length }), tasks, pool: 1 });
    await fetchHomeBlocks();
    rereadFamily(); // a copy of a page inside the open one is beside it there
    batchStatus(tn("Copied {n} page.", "Copied {n} pages.", result.done.length), result, ids.length);
  }

  // Pages to Recently deleted as a batch (the selection's Delete, a
  // folder's "Delete N pages too"): `tops` are the requests, `gone` every
  // page they take. Those leave the library at once and stay out of the
  // listings read meanwhile (fetchHomeBlocks); the read after the batch
  // brings back any that stayed. The tabs of the pages that went close,
  // with the pages inside them. Resolves the batch's result.
  const trashingRef = useRef(new Set());
  async function trashPages(tops, gone) {
    for (const id of gone) trashingRef.current.add(id);
    setHomeBlocks((prev) => prev.filter((b) => !gone.has(b.id)));
    dropKnown([...gone]);
    clearSelection();
    try {
      const result = await runBulk(tops, (chunk) => sendBulk({ trash: chunk }),
        { name: t("Delete {n} pages", { n: tops.length }), tasks });
      const went = new Set([...result.done, ...pageDescendants(pageBlocks, result.done)]);
      updateTabs((prev) => prev.filter((tab) => !went.has(tab.id)));
      return result;
    } finally {
      for (const id of gone) trashingRef.current.delete(id);
    }
  }
  const movedToTrash = (n) => tn("Moved {n} page to Recently deleted.", "Moved {n} pages to Recently deleted.", n);
  // Every page asked for went: no Stop came and none failed.
  const allWent = (result) => !result.stopped && !result.failed.length;

  // The pages inside a page go to Recently deleted with it (the server's
  // cascade): the confirmation counts them (deleteConfirm) and their tabs
  // close too.
  function deletePages(ids) {
    if (work.refused(ids, (rules) => rules.trash, "trash")) return;
    const { title, message, inside } = deleteConfirm(pageBlocks, ids);
    setConfirmBox({
      title,
      message,
      confirmLabel: t("Delete"),
      danger: true,
      onConfirm: async () => {
        // Only the topmost selected pages: the server's cascade takes the
        // pages inside them, and one trashed alone first would leave it.
        const tops = topmostPages(pageBlocks, ids);
        const result = await trashPages(tops, new Set([...ids, ...inside]));
        await fetchHomeBlocks();
        rereadFamily(); // one of them may be inside the open page
        // what a Stop or a failure left is counted as the batch counts: its tops
        batchStatus(movedToTrash(allWent(result) ? ids.length : result.done.length), result, tops.length);
      },
    });
  }
  // The page header's trash button: the open page, with the pages inside
  // it, whose tabs close too and are no place to go back to.
  function deleteOpenPage() {
    const id = focusedBlockId;
    if (!openRules.trash) { setStatus(refusalText("trash")); return; }
    const { title, message, inside } = deleteConfirm(pageBlocks, [id], pageTitle);
    setConfirmBox({
      title,
      message,
      confirmLabel: t("Delete"),
      danger: true,
      onConfirm: async () => {
        // Close the tab first, back to where you were, in place:
        // the server's `trashed` reaches the page socket with the
        // DELETE's answer, and on the page still open it would
        // put up the Recently deleted notice. The library drops
        // the pages at once and refetches once they are gone.
        const gone = [id, ...inside];
        setHomeBlocks((prev) => prev.filter((b) => !gone.includes(b.id)));
        dropKnown(gone);
        tabHistoryRef.current = tabHistoryRef.current.filter((entry) => !inside.includes(entry.blockId));
        await closeTab(id, { refresh: false });
        updateTabs((prev) => prev.filter((tab) => !inside.includes(tab.id)));
        try {
          await apiJson(`${API}/blocks/${id}`, { method: "DELETE" });
          setStatus(movedToTrash(1));
        } catch (err) {
          setStatus(t("Delete failed: {message}", { message: err.message }));
        }
        fetchHomeBlocks();
      },
    });
  }
  // The missing-page notice's Restore: the link named a page (or a block of
  // one) in Recently deleted — bring the page back and open what was linked.
  // The entry can be another page's (a sub-page went with the page above
  // it), so what opens is the page that holds the linked id once it is back.
  async function restoreMissingPage() {
    const { id, trashed } = missingPage;
    setMissingPage(null);
    try {
      await apiJson(`${API}/trash/${encodeURIComponent(trashed.id)}/restore`, { method: "POST" });
    } catch (err) {
      setStatus(t("Restore failed: {message}", { message: err.message }));
      return;
    }
    fetchHomeBlocks();
    const blk = await apiJson(`${API}/blocks/${encodeURIComponent(id)}`).catch(() => null);
    const page = blk ? blk.page_id || id : trashed.id;
    openBlock(page, { link: true, block: id !== page ? id : "" });
  }
  // The open page went to Recently deleted while open (pageGone): Restore
  // brings back its trash entry — the page above it when it went with that
  // one, so the whole tree returns rather than this page alone, outside it —
  // and refetches it; the edits that waited go out with that load
  // (collabSession's `unpark`).
  async function restoreOpenPage() {
    const id = focusedBlockId;
    let target = pageGone?.trashed?.id || "";
    if (!target) {
      try { await apiJson(`${API}/blocks/${encodeURIComponent(id)}`); } catch (err) {
        if (err?.status === 404) target = err.data?.trashed?.id || "";
      }
    }
    try {
      await apiJson(`${API}/trash/${encodeURIComponent(target || id)}/restore`, { method: "POST" });
    } catch (err) {
      setStatus(t("Restore failed: {message}", { message: err.message }));
      return;
    }
    fetchHomeBlocks();
    if (id === focusedBlockIdRef.current) { loadBlocksForBlock(id); rereadFamily(); }
  }

  // Where pages sit among pages: `place(id)` is the patch for each, a `set`
  // of `parent_page` on its root patched in bulk (patchPages; a page id puts
  // it inside that page, and the server drops its own folders; null takes
  // it out). `name` is the batch's row, `done(n)` what n moved pages say.
  // The listing and the open page's family are read again after.
  async function placePages(ids, place, name, done) {
    clearSelection();
    const result = await patchPages(ids, (id) => id, place, { name, tasks });
    await fetchHomeBlocks();
    rereadFamily();
    batchStatus(done(result.done.length), result, ids.length);
  }
  // "Move into page…": the move picker over every page but the moved ones
  // and the pages inside them, where they cannot go (moveExclude). Only the
  // topmost of `picked` move: a page inside one of them goes along inside
  // it rather than out of it. One naming an opened PDF moves nothing
  // (addingToLibrary).
  function pickPageParent(picked) {
    if (addingToLibrary(picked) || work.refused(picked, (rules) => rules.moveIntoPage)) return;
    pickMovePage({ pageIds: topmostPages(pageBlocks, picked) });
  }
  // A page whose link, or whose publication, reaches the pages moved in for
  // the first time asks first, saying who will open them (GET
  // /pages/{id}/reach, sharing/shareReach.js); one that changes nothing
  // about that moves at once. A reach that cannot be read does not stop it.
  async function movePagesInto(ids, parent) {
    const title = parent.content || t("Untitled");
    const move = () => placePages(ids, () => ({ parent_page: parent.id }), t("Move {n} pages into “{title}”", { n: ids.length, title }),
      (n) => tn("Moved {n} page into “{title}”.", "Moved {n} pages into “{title}”.", n, { title }));
    let reach = null;
    try { reach = await apiJson(`${API}/pages/${encodeURIComponent(parent.id)}/reach`); } catch {}
    // already reached: inside the share's or the publication's page, or filed in the shared folder
    const within = (id, entry) => (entry.page_id
      ? id === entry.page_id || pageAncestors(pagesById, id).some((a) => a.id === entry.page_id)
      : (topPageOf(pagesById, id)?._folders || []).includes(entry.folder));
    const message = moveExposureMessage(moveExposure(reach, ids, within), ids.length);
    if (!message) { move(); return; }
    setConfirmBox({
      title: T("Move into a shared page?"),
      message,
      confirmLabel: t("Move"),
      onConfirm: move,
    });
  }
  // "Move to library": out of the page they are in, filed in their top's
  // folders, where they were listed until now. Only the topmost of `picked`
  // that are inside a page move: a page inside another of them stays inside it.
  // One naming an opened PDF moves nothing (addingToLibrary). Whether any
  // did, or Add to library… opened instead (none: every one is in the
  // library already).
  function movePagesToLibrary(picked) {
    if (addingToLibrary(picked)) return true;
    const ids = topmostPages(pageBlocks, picked).filter((id) => pagesById.get(id)?._parentPage);
    if (!ids.length) return false;
    const place = (id) => {
      const folders = topPageOf(pagesById, id)?._folders || [];
      return { parent_page: null, folders: folders.length ? folders : null };
    };
    placePages(ids, place, t("Move {n} pages to the library", { n: ids.length }),
      (n) => tn("Moved {n} page to the library.", "Moved {n} pages to the library.", n));
    return true;
  }
  // How the open page stands with this computer, in the desktop app
  // (vault/useVault.js, the feature's owner): a synced vault keeps it, or it
  // is a PDF read where it lies. Its chip and banner, the notes lock, the
  // vault's conflicts and Add to library… read it; the page socket's `vault`
  // reads it again.
  const vault = useVault(focusedBlockId, !shareMode && !!authUser?.user, { track: initialTrack, setStatus: (msg) => setStatus(msg) });
  // A PDF opened from this computer (vault/vaultState.js isOpenedPdf) leaves
  // *Opened files* only through Add to library…, which stores its bytes
  // first (linked_files.add_to_library) (the open page says so itself too:
  // its standing with this computer, read whatever the listing's rows carry).
  const openedPdf = (id) => isOpenedPdf(id, pagesById) || (id === focusedBlockId && addsToLibrary(vault.state));
  // A move of pages that names an opened PDF is not made: the menus offer
  // the moves only when none is selected (Add to library… when all are), so
  // the one dialog that shows is Add to library…, for the opened ones.
  // Whether `ids` named one (the move is then not made).
  function addingToLibrary(ids) {
    const opened = ids.filter(openedPdf);
    if (opened.length) vault.setAdding(opened);
    return opened.length > 0;
  }
  // Whether Add to library… offers another server's library (the desktop
  // app's `carry-to-server`, one page at a time): not for the open page
  // whose file is known to be gone.
  const carriesAway = (ids) => IS_DESKTOP && ids.length === 1
    && (ids[0] !== focusedBlockId || !vault.state || carriesToServer(vault.state));
  // Whether the pages to add are known to be another account's opened files
  // (the open page's state says so): what keeps reading the file or takes it
  // elsewhere is the opener's alone, so their dialog offers neither.
  const openedByOther = (ids) => ids.length === 1 && ids[0] === focusedBlockId && !openedHere(vault.state);
  // Another workspace's folders for its dialog, by path, the ones last
  // worked in first (GET /library/folders, which `?ws=` points there).
  const readWorkspaceFolders = (id) => apiJson(`${API}/library/folders?ws=${encodeURIComponent(id)}`)
    .then((d) => (d.folders || []).map((f) => ({ id: f.id, name: f.path.join(PATH_SEP) })));
  // `target`: the workspace they go to. Another of this server takes each
  // with its notes, highlights and chat and the opened page here goes; once
  // all went, the tab opens the first there.
  async function addOpenedToLibrary(ids, folder, keepPushing, target = wsId) {
    vault.setAdding(null);
    const away = target !== wsId;
    const added = [];
    const result = await runBatch(ids, async (id) => {
      const page = await apiJson(`${API}/folder-links/opened/${encodeURIComponent(id)}/add`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder, keep_pushing: keepPushing, workspace: away ? target : "" }),
      });
      added.push(page?.id || id);
    }, { name: t("Add {n} PDFs to the library", { n: ids.length }), tasks });
    if (away && added.length && !result.failed.length) { switchWorkspace(target, added[0]); return; }
    await fetchHomeBlocks();
    rereadFamily();
    vault.refresh();
    batchStatus(tn("Added {n} PDF to the library.", "Added {n} PDFs to the library.", result.done.length), result, ids.length);
  }

  // Pin/unpin folders: `properties.pinned` (an ISO time; null unpins) on
  // the folder block, one batch on the folder tree — the workspace's pin,
  // like a page's. The Pinned strip reads it off the tree.
  async function setFoldersPinned(ids, pinned) {
    const stamp = pinned ? new Date().toISOString() : null;
    try {
      await treeOps("folders", ids.map((id) => ({ op: "set", id, props: { pinned: stamp } })));
    } catch (err) {
      setStatus(t("Pin failed: {err}", { err: err.message || err }));
    }
  }

  // Pin/unpin pages. Stored on the page (properties.pinned = ISO timestamp),
  // so it syncs across devices; null unpins, which drops the key.
  async function setPagesPinned(ids, pinned) {
    const stamp = pinned ? new Date().toISOString() : null;
    const result = await patchPages(ids, (id) => id, () => ({ pinned: stamp }),
      { name: pinned ? t("Pin {n} pages", { n: ids.length }) : t("Unpin {n} pages", { n: ids.length }), tasks });
    await fetchHomeBlocks();
    batchStatus("", result, ids.length);
  }

  // Refile pages: `refile` maps a page's filing — its `folders` and
  // `labels`, the ids the trees have (a dangling one goes with the write)
  // — and its id to the patch to write, or null to leave the page alone. The pages
  // with a patch are the batch: `name(n)` its row for n of them, `done(n)`
  // what n changed pages say. The selection and listing are refreshed.
  async function refilePages(ids, refile, name, done) {
    const patches = ids.flatMap((id) => {
      const b = knownBlocks.find((x) => x.id === id);
      const patch = b && refile({
        folders: filedIn(libTree.folders, b.properties?.folders),
        labels: filedIn(libTree.labels, b.properties?.labels),
      }, id);
      return patch ? [[id, patch]] : [];
    });
    clearSelection();
    const result = await patchPages(patches, ([id]) => id, ([, patch]) => filingProps(patch), { name: name(patches.length), tasks });
    await fetchHomeBlocks();
    batchStatus(done(result.done.length), result, patches.length);
  }

  // A page inside another is filed where its top is (its own folders count
  // for nothing), and a work folder's file not in the library by its
  // directory alone, so filing leaves them out (workFolderActions.filingOf):
  // the pages of `pageIds` it takes, and what the status adds about the
  // ones left out ("" for none). Null when it takes none, which the status
  // says.
  function filablePages(pageIds) {
    const filing = filingOf(pagesById, pageIds);
    if (filing.ids.length) return filing;
    setStatus(filing.why);
    return null;
  }

  // Add pages to a folder (soft link — their other folders are kept, except
  // one above the target: dragging a "readout" paper into "readout /
  // nondestructive" refines it, it shouldn't stay in both levels).
  // A page inside another is left out (filablePages). A work folder's
  // directories take no page: what they hold follows the disk.
  async function addPagesToFolder(pageIds, folder) {
    const path = folderPath(libTree, folder);
    if (workOf(workTree, folder)) {
      // into a work folder's directory: only its notes that follow move
      // there (workFolderActions.directoryMoves)
      const { into, why } = directoryMoves(pagesById, pageIds, folder, workTree);
      if (!into) { setStatus(why); return; }
      await refilePages(pageIds, ({ folders }, id) => (folders[0] === folder ? null : { folders: into.get(id) }),
        (n) => t("Move {n} pages to “{path}”", { n, path }),
        (n) => (n ? tn("Moved {n} page to “{path}”.", "Moved {n} pages to “{path}”.", n, { path }) : t("Already in “{path}”.", { path })));
      return;
    }
    const filable = filablePages(pageIds);
    if (!filable) return;
    await refilePages(filable.ids,
      ({ folders }) => (folders.includes(folder) ? null : { folders: addToFolder(libTree, folders, folder) }),
      (n) => t("Add {n} pages to “{path}”", { n, path }),
      (n) => (n ? tn("Added {n} page to “{path}”.", "Added {n} pages to “{path}”.", n, { path }) : t("Already in “{path}”.", { path })) + filable.left);
  }

  // Label mirror — labels are flat, so a soft add with no ancestor
  // refinement. Dropping a paper on a label tile lands here.
  async function addPagesToLabel(ids, label) {
    const name = labelName(libTree, label);
    await refilePages(ids,
      ({ labels }) => (labels.includes(label) ? null : { labels: [...labels, label] }),
      (n) => t("Label {n} pages “{name}”", { n, name }),
      (n) => (n ? tn("Labelled {n} page “{name}”.", "Labelled {n} pages “{name}”.", n, { name }) : t("Already labelled “{name}”.", { name })));
  }

  // Strip every label (the "No label" tile's drop target).
  async function clearPagesLabels(ids) {
    await refilePages(ids, ({ labels }) => (labels.length ? { labels: [] } : null),
      (n) => t("Clear the labels of {n} pages", { n }),
      (n) => (n ? tn("Cleared the labels on {n} page.", "Cleared the labels on {n} pages.", n) : t("No labels to clear.")));
  }

  // Remove one label from pages (the label view's back-row drop target).
  async function removePagesFromLabel(ids, label) {
    const name = labelName(libTree, label);
    await refilePages(ids,
      ({ labels }) => (labels.includes(label) ? { labels: labels.filter((l) => l !== label) } : null),
      (n) => t("Remove {n} pages from “{name}”", { n, name }),
      (n) => tn("Removed {n} page from “{name}”.", "Removed {n} pages from “{name}”.", n, { name }));
  }

  // Take pages out of one folder (the back row's drop target, the menu's
  // "Remove from"); "" takes them out of every folder but a work folder's
  // file's directory, which files it whatever the library does.
  async function removePagesFromFolder(pageIds, folder) {
    const name = folderPath(libTree, folder);
    if (folder && workOf(workTree, folder)) {
      setStatus(refusalText("filed"));
      return;
    }
    const filable = filablePages(pageIds);
    if (!filable) return;
    await refilePages(filable.ids, ({ folders }) => {
      const next = folder ? folders.filter((f) => f !== folder) : folders.filter((f) => workOf(workTree, f));
      return next.length === folders.length ? null : { folders: next };
    },
    (n) => (folder ? t("Remove {n} pages from “{name}”", { n, name }) : t("Remove {n} pages from every folder", { n })),
    (n) => (folder ? tn("Removed {n} page from “{name}”.", "Removed {n} pages from “{name}”.", n, { name }) : t("Removed from every folder.")) + filable.left);
  }

  // Rename a folder: one `set` of its name on the folder tree. Its pages,
  // subfolders, share and chat name it by id, so nothing else changes — no
  // page is touched, its modified time included.
  async function renameFolder(id, draft) {
    const name = draft.trim();
    setFolderRenaming(null);
    const folder = libTree.folders.get(id);
    if (!folder || !name || name === folder.name) return;
    if (findNamed(siblingFolders(libTree, folder.parent).filter((f) => f.id !== id), name)) {
      setStatus(t("There is already a folder “{name}” here.", { name }));
      return;
    }
    try {
      await treeOps("folders", [{ op: "set", id, content: name }]);
      setStatus(t("Folder renamed to “{newPath}”.", { newPath: [...folder.path.slice(0, -1), name].join(PATH_SEP) }));
    } catch (err) {
      setStatus(t("Rename failed: {err}", { err: err.message }));
    }
  }

  // Move folders (with what is below them) into `parent` ("" = the top
  // level), last there: one `move` each, in one batch on the folder tree. A
  // folder never moves into itself or below itself; its pages stay filed in
  // it, so they move with it. A work folder's directories stay where the
  // disk has them, and no folder goes into a work folder (foldersRefused).
  async function moveFolders(ids, parent) {
    const moving = ids.filter((id) => libTree.folders.has(id) && libTree.folders.get(id).parent !== parent
      && !(parent && inFolder(libTree, parent, id)));
    if (!moving.length || work.foldersRefused(moving, parent)) return;
    try {
      await treeOps("folders", moving.map((id) => ({ op: "move", id, parent: parent || "folders" })));
    } catch (err) {
      setStatus(t("Move failed: {message}", { message: err.message }));
      return;
    }
    clearSelection();
    const to = folderPath(libTree, parent) || t("All files");
    setStatus(moving.length === 1
      ? t("Moved “{name}” to “{to}”.", { name: libTree.folders.get(moving[0]).name, to })
      : t("Moved {n} folders to “{to}”.", { n: moving.length, to }));
  }

  // Put folders right before or after a sibling (dropped on the sibling's
  // edge in the custom order), in the order they are listed: one `move`
  // each with a fractional key between the new neighbours, in one batch on
  // the folder tree, which every member sees in the same order. A folder
  // never lands below itself.
  async function placeFolders(ids, target, after) {
    const parent = libTree.folders.get(target)?.parent ?? "";
    const listed = (id) => childFolders(libTree, libTree.folders.get(id).parent).indexOf(id);
    const moving = ids.filter((id) => libTree.folders.has(id) && id !== target && !(parent && inFolder(libTree, parent, id)))
      .sort((a, b) => listed(a) - listed(b));
    if (!moving.length || work.foldersRefused(moving.filter((id) => libTree.folders.get(id).parent !== parent), parent)) return;
    const order = childFolders(libTree, parent).filter((f) => !moving.includes(f));
    const before = after ? order[order.indexOf(target) + 1] || "" : target;
    const positions = folderPositions(libTree, parent, before, moving);
    try {
      await treeOps("folders", moving.map((id, i) => ({ op: "move", id, parent: parent || "folders", position: positions[i] })));
    } catch (err) {
      setStatus(t("Move failed: {message}", { message: err.message }));
    }
  }

  // Folders share the "text/plain" drag channel with page cards — prefixed so
  // drop targets can tell them apart. Returns null for a page drag.
  function droppedFolders(e) {
    const raw = e.dataTransfer.getData("text/plain");
    if (!raw.startsWith(FOLDER_DRAG)) return null;
    const id = raw.slice(FOLDER_DRAG.length);
    return selectedFolders.has(id) && selectedFolders.size > 1 ? [...selectedFolders] : [id];
  }

  // The edge of a folder of the listing a drag is over, in the custom
  // order: "before" / "after" near its leading or trailing edge (a row's top
  // or bottom, a tile's left or right), where a dropped folder takes its
  // place beside it; null in the middle, where it moves in. A drag knows
  // what it carries only through its types, so a folder drag is told by
  // FOLDER_DRAG_TYPE.
  function folderDropEdge(e) {
    if (homeSort !== "manual" || !e.dataTransfer.types.includes(FOLDER_DRAG_TYPE)
      || !e.currentTarget.closest(".fileList, .fileGrid")) return null;
    const box = e.currentTarget.getBoundingClientRect();
    const grid = homeView === "grid";
    const at = grid ? (e.clientX - box.left) / box.width : (e.clientY - box.top) / box.height;
    return at < 0.25 ? "before" : at > 0.75 ? "after" : null;
  }
  // Drag-over for a folder row or tile: which drop it would be (into it, or
  // beside it), shown on the target.
  function dragOverFolder(e, id) {
    e.preventDefault();
    const edge = folderDropEdge(e);
    setFolderDragOver(edge ? `${edge}:${id}` : id);
  }
  // The class a folder target wears while a drag is over it.
  const folderDropClass = (id) => (folderDragOver === id ? "dragOver"
    : folderDragOver === `before:${id}` ? "dropBefore" : folderDragOver === `after:${id}` ? "dropAfter" : "");

  // Shared drop dispatch for folder rows/tiles: a folder drag moves folders
  // in (or, on the edge of a folder in the custom order, beside it), a
  // page-card drag files the dragged (or whole selected) pages. The back row
  // overrides onPages to take them out of the open folder instead.
  function dropOnFolder(e, target, onPages = (ids) => addPagesToFolder(ids, target)) {
    if (!lib.organize) return;
    e.preventDefault();
    const edge = target ? folderDropEdge(e) : null;
    setFolderDragOver(null);
    const folders = droppedFolders(e);
    if (folders) {
      if (edge) { if (!folders.includes(target)) placeFolders(folders, target, edge === "after"); }
      else moveFolders(folders, target);
      return;
    }
    const ids = droppedPageIds(e);
    if (ids) onPages(ids);
  }

  // Drop dispatch for label rows/tiles: pages get the label, folder drags are
  // ignored (a folder can't be "labelled" — its papers each carry their own).
  function dropOnLabel(e, label, onPages = (ids) => (label === NO_LABEL ? clearPagesLabels(ids) : addPagesToLabel(ids, label))) {
    if (!lib.organize) return;
    e.preventDefault();
    setFolderDragOver(null);
    if (droppedFolders(e)) { setStatus(t("Folders can’t carry labels — drop pages instead.")); return; }
    const ids = droppedPageIds(e);
    if (ids) onPages(ids);
  }
  // The pages a card drag carries: the whole selection when the dragged card
  // is part of a multi-select, else just that card.
  function droppedPageIds(e) {
    const id = e.dataTransfer.getData("text/plain");
    if (!id) return null;
    return selectedPages.has(id) && selectedPages.size > 1 ? [...selectedPages] : [id];
  }
  // A drop target that takes page drags only (isPageDrag): `key` is what
  // folderDragOver reads while one is over it, `onPages` gets the dropped
  // ids. Nothing where the library cannot be changed.
  const pageDragTarget = (key, onPages) => (lib.organize ? {
    onDragOver: (e) => {
      if (!isPageDrag(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setFolderDragOver(key);
    },
    onDragLeave: () => setFolderDragOver(null),
    onDrop: (e) => {
      if (!isPageDrag(e)) return;
      e.preventDefault();
      setFolderDragOver(null);
      const ids = droppedPageIds(e);
      if (ids) onPages(ids);
    },
  } : {});
  // A page as a drop target (a library row or card, a Pages inside row, a
  // crumb over the open page's title): the pages dropped go inside it
  // (dropPagesInto). `title` names it where the listing does not.
  const pageDropProps = (id, title) => pageDragTarget(`page:${id}`, (ids) => dropPagesInto(ids, id, title));
  const pageDropClass = (id) => (folderDragOver === `page:${id}` ? "dragOver" : "");
  // The topbar's Home, the library: the pages inside other pages among the
  // dropped ones go to the library, as Move to library takes them.
  const libraryDropProps = pageDragTarget("library", (ids) => {
    if (!movePagesToLibrary(ids)) setStatus(t("Already in the library."));
  });

  // Pages dropped on a page go inside it, as Move into page… puts them: the
  // topmost of them, those not right inside it already (pagesIntoPage).
  // Never into one of them or a page inside one, which the status says; a
  // drag let go where it began does nothing. Never into a work folder's
  // file, nor a file into a page (fileRules).
  function dropPagesInto(ids, target, title) {
    if (work.refused([target, ...ids], (rules) => rules.moveIntoPage)) return;
    const name = pagesById.get(target)?.content || title || t("Untitled");
    const { refused, moving } = pagesIntoPage(pageBlocks, ids, target);
    if (refused === "inside") setStatus(t("A page can’t go inside a page that is inside it."));
    else if (moving.length) movePagesInto(moving, { id: target, content: name });
    else if (!refused) setStatus(t("Already inside “{title}”.", { title: name }));
  }

  // The library's view, read when a folder goes: "Delete N pages too" may
  // have run a while.
  const homeViewRef = useRef(null);
  homeViewRef.current = { folder: folderFilter, label: labelFilter };
  // Delete a folder with its subfolders (DELETE /folders/{id}): the server
  // takes their ids off every page filed under them, files their AI chats
  // into the library chat's history and stops their share links. "Delete N
  // pages too" moves those pages to Recently deleted first, and the folder
  // goes only once every one of them went.
  function deleteFolder(id) {
    const path = folderPath(libTree, id);
    const below = folderSubtree(libTree, id);
    const parent = libTree.folders.get(id)?.parent || "";
    const members = libraryPages.filter((b) => b._folders.some((f) => below.has(f)));
    // Those a person may send to Recently deleted (a work folder's file in
    // the library stays, filed by its directory: fileRules), and the pages
    // inside them, which their DELETE takes along (the server's cascade):
    // counted in what goes, and their tabs close too.
    const trashable = members.filter((b) => fileRules(b, workTree).trash);
    const inside = pageDescendants(pageBlocks, trashable.map((b) => b.id));
    const remove = async (statusMsg) => {
      try {
        await apiJson(`${API}/folders/${encodeURIComponent(id)}`, { method: "DELETE" });
      } catch (err) {
        setStatus(t("Delete failed: {message}", { message: err.message }));
        return;
      }
      const view = homeViewRef.current;
      if (below.has(view.folder)) {
        setFolderFilter(parent);
        if (homeModeRef.current) window.history.replaceState(null, "", homeUrlFor(parent, view.label));
      }
      await fetchHomeBlocks();
      setStatus(statusMsg);
    };
    if (!members.length) {
      setConfirmBox({
        title: T("Delete folder"),
        message: t("Delete the empty folder “{path}”?", { path: path }),
        confirmLabel: t("Delete folder"),
        onConfirm: () => { clearSelection(); remove(t("Folder “{path}” deleted.", { path })); },
      });
      return;
    }
    const n = members.length;
    const all = trashable.length + inside.length;
    const contains = tn("Delete “{path}”? It contains {n} page. Keep it in the library (only the folder goes away), or delete it too — pages linked into other folders as well. Deleted pages can be restored from Recently deleted for 30 days.",
      "Delete “{path}”? It contains {n} pages. Keep them in the library (only the folder goes away), or delete them too — pages linked into other folders as well. Deleted pages can be restored from Recently deleted for 30 days.", n, { path });
    setConfirmBox({
      title: T("Delete folder"),
      message: inside.length
        ? `${contains} ${tn("Deleting also takes the {n} page inside them.", "Deleting also takes the {n} pages inside them.", inside.length)}`
        : contains,
      confirmLabel: t("Keep pages"),
      onConfirm: () => {
        clearSelection();
        remove(tn("Folder “{path}” deleted — its {n} page stays in the library.", "Folder “{path}” deleted — its {n} pages stay in the library.", n, { path }));
      },
      ...(trashable.length ? { altLabel: tn("Delete {n} page too", "Delete {n} pages too", all), altDanger: true } : {}),
      onAlt: async () => {
        const ids = trashable.map((b) => b.id);
        const result = await trashPages(ids, new Set([...ids, ...inside]));
        if (allWent(result)) {
          await remove(tn("Folder “{path}” deleted — its {n} page moved to Recently deleted.", "Folder “{path}” deleted — its {n} pages moved to Recently deleted.", all, { path }));
          return;
        }
        await fetchHomeBlocks();
        setStatus(`${t("The folder “{path}” is kept.", { path })} ${batchText(movedToTrash(result.done.length), result, ids.length)}`);
      },
    });
  }

  // Rename a label: one `set` on the label tree — the pages carrying it
  // name it by id and are not touched.
  async function renameLabel(id, draft) {
    const name = draft.trim();
    setLabelRenaming(null);
    if (!name || name === libTree.labels.get(id)?.name) return;
    if (findNamed([...libTree.labels.values()].filter((l) => l.id !== id), name)) {
      setStatus(t("There is already a label “{name}”.", { name }));
      return;
    }
    try {
      await treeOps("labels", [{ op: "set", id, content: name }]);
      setStatus(t("Label renamed to “{newName}”.", { newName: name }));
    } catch (err) {
      setStatus(t("Rename failed: {err}", { err: err.message }));
    }
  }

  // Delete a label (DELETE /labels/{id}): the server takes it off every page.
  function deleteLabel(id) {
    const name = labelName(libTree, id);
    const members = knownBlocks.filter((b) => filedIn(libTree.labels, b.properties?.labels).includes(id));
    setConfirmBox({
      title: T("Delete label"),
      message: members.length
        ? t("Delete “{name}”? The label is removed from its {n} page{_s} — no pages are deleted.", { name, n: members.length, _s: members.length === 1 ? "" : "s" })
        : t("Delete the label “{name}”?", { name }),
      confirmLabel: t("Delete label"),
      onConfirm: async () => {
        try {
          await apiJson(`${API}/labels/${encodeURIComponent(id)}`, { method: "DELETE" });
        } catch (err) {
          setStatus(t("Delete failed: {message}", { message: err.message }));
          return;
        }
        if (labelFilter === id) {
          setLabelFilter("");
          window.history.replaceState(null, "", homeUrlFor(folderFilter, ""));
        }
        await fetchHomeBlocks();
        setStatus(t("Label “{name}” deleted.", { name: name }));
      },
    });
  }

  // Right-click on a folder or label chip anywhere opens the shared home
  // context menu (rename/delete) for it.
  const openTagMenu = (kind, id) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    setHomeMenu({ kind, id, x: e.clientX, y: e.clientY, el: e.currentTarget });
  };
  // Select mode's "⋯": the menu a right-click on the selection opens.
  const selectionCount = selectedPages.size + selectedFolders.size + selectedLabels.size;
  function openSelectionMenu(e) {
    const [page] = selectedPages, [folder] = selectedFolders, [label] = selectedLabels;
    if (page) openPageMenu(page)(e);
    else if (folder) openTagMenu("folder", folder)(e);
    else if (label) openTagMenu("label", label)(e);
  }
  const [pdfEffScale, setPdfEffScale] = useState(1); // actual render scale (incl. fit-width)
  // Browser fullscreen (whole app, like F11).
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const onFs = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);
  // Use native fullscreen on touch devices too; app fullscreen is a fallback
  // for browsers where the Fullscreen API is unavailable or rejects the request.
  const [pseudoFullscreen, setPseudoFullscreen] = useState(false);
  const fullscreenTapRef = useRef({ start: null, handledUntil: 0 });
  useEffect(() => {
    if (!pseudoFullscreen) return;
    document.documentElement.classList.add("appFocusFullscreen");
    const escape = (e) => { if (e.key === "Escape") setPseudoFullscreen(false); };
    window.addEventListener("keydown", escape);
    return () => {
      document.documentElement.classList.remove("appFocusFullscreen");
      window.removeEventListener("keydown", escape);
    };
  }, [pseudoFullscreen]);
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (pseudoFullscreen) {
      setPseudoFullscreen(false);
    } else if (!document.fullscreenEnabled) {
      setPseudoFullscreen(true);
    } else {
      document.documentElement.requestFullscreen().catch(() => setPseudoFullscreen(true));
    }
  }
  // Mobile browsers may omit the synthetic click after a scroll. Handle a
  // stationary touch release directly, then consume its compatibility click.
  const fullscreenPointerDown = (e) => {
    fullscreenTapRef.current = { handledUntil: 0, start: e.pointerType === "touch"
      ? { id: e.pointerId, x: e.clientX, y: e.clientY } : null };
  };
  const fullscreenPointerUp = (e) => {
    const tap = fullscreenTapRef.current, start = tap.start;
    tap.start = null;
    if (!start || start.id !== e.pointerId || Math.hypot(e.clientX - start.x, e.clientY - start.y) > 8) return;
    tap.handledUntil = Date.now() + 750;
    toggleFullscreen();
  };
  const fullscreenClick = (e) => {
    if (e.detail > 0 && Date.now() < fullscreenTapRef.current.handledUntil) {
      fullscreenTapRef.current.handledUntil = 0;
      return;
    }
    toggleFullscreen();
  };
  const restoredPdfUrlRef = useRef(null);
  const coarseRestorePendingRef = useRef(false); // last-read jump not yet applied
  // Something else is taking the viewport to its own target (a pinned search
  // hit, a highlight deep link): calling this makes the coarse last-read
  // jump stand down for the current document — its settle loop can't tell a
  // programmatic scroll from layout drift and would fight it back.
  const cancelCoarseRestoreRef = useRef(() => {});
  const [blocks, setBlocks] = useState([]);
  const [homeBlocks, setHomeBlocks] = useState([]);
  const [homeLoaded, setHomeLoaded] = useState(false); // the library listing has come back once
  const [homeFailed, setHomeFailed] = useState(false); // its last read failed
  // What the listing's loads set (the pages and trees a read brings back,
  // the view history read from the cache or the server): a change in the
  // listing only they made moves nothing (homeStill).
  const homeReadRef = useRef(new WeakSet());
  const [refCache, setRefCache] = useState({}); // { [blockId]: { content, page_title } }
  const [backlinks, setBacklinks] = useState([]);
  const [chatHidden, setChatHidden] = useState(false);
  // The Comments window (comments/CommentsPanel.jsx): closed until the View
  // menu, the command or the phone's More sheet shows it.
  const [commentsHidden, setCommentsHidden] = useState(true);
  const [homeEditingId, setHomeEditingId] = useState(null);
  // ---- Unified floating message pill ---------------------------------------
  // ONE pill, many sources. Each source posts into its own named channel
  // (status messages, the PDF load lifecycle, …) via postPill(channel, entry);
  // the pill renders a single winner, so messages can never overlap.
  //   entry: { msg, spinner?, error?, action?: {label, run}, final? }
  // Ongoing entries (spinner) hold their channel until the source posts again
  // or clears it (entry = null). Final entries linger 1s, then fade out. An
  // action is the pill's one button (Retry a PDF, Download a finished export).
  // When several channels are active: error > lingering final or a
  // `question` > ongoing, ties broken by recency. So a question (the pens
  // question below, on a channel of its own) stays over a spinner, and a
  // newer final message shows over it for its second, then goes.
  const [status, setStatusRaw] = useState(t("Ready."));
  // System log (Settings → Diagnostics): status messages, PDF load activity,
  // and uncaught errors from this session. In-memory only.
  const [sysLog, setSysLog] = useState([]); // [{t, msg, tone}], capped
  const logSys = useCallback((msg, tone = "") => {
    setSysLog((prev) => [...prev.slice(-499), { t: Date.now(), msg: String(msg), tone }]);
  }, []);
  useEffect(() => {
    const onErr = (e) => logSys(`error: ${e.message || "unknown"}${e.filename ? ` (${e.filename.split("/").pop()}:${e.lineno})` : ""}`, "error");
    const onRej = (e) => logSys(`unhandled rejection: ${e.reason?.message || e.reason || "unknown"}`, "error");
    const onApi = (e) => { if (e.detail?.message) logSys(e.detail.message, e.detail.tone); };
    window.addEventListener("error", onErr);
    window.addEventListener("unhandledrejection", onRej);
    window.addEventListener("gamma-api-log", onApi);
    return () => {
      window.removeEventListener("error", onErr);
      window.removeEventListener("unhandledrejection", onRej);
      window.removeEventListener("gamma-api-log", onApi);
    };
  }, [logSys]);
  // Debug log level (Settings → Diagnostics): when on, position-tracking and
  // sync events go to the system log (and the console), so a lost reading
  // position can be traced from any device — the log pane has a Copy button.
  const [debugLog, setDebugLog] = usePersistedFlag("gamma-debug-log", false);
  const debugLogRef = useRef(debugLog);
  useEffect(() => { debugLogRef.current = debugLog; }, [debugLog]);
  const dbg = useCallback((...args) => {
    if (!debugLogRef.current) return;
    const msg = "dbg: " + args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
    console.log(msg);
    logSys(msg);
  }, [logSys]);
  const [pillChannels, setPillChannels] = useState({}); // channel -> entry (+ seq, fading)
  const pillSeqRef = useRef(0);
  const pillTimersRef = useRef({}); // channel -> pending linger/fade timers
  // opts.after = [ms, patch]: once ms elapse — if this entry still owns the
  // channel — merge patch into it, or clear the channel when patch is null.
  // Callers use it for escalations and stuck-spinner caps without touching
  // the timer bookkeeping themselves.
  const postPill = useCallback((channel, entry, opts) => {
    const timers = pillTimersRef.current;
    (timers[channel] || []).forEach(clearTimeout);
    delete timers[channel];
    if (!entry) {
      setPillChannels((prev) => {
        if (!(channel in prev)) return prev;
        const next = { ...prev };
        delete next[channel];
        return next;
      });
      return;
    }
    const seq = ++pillSeqRef.current;
    setPillChannels((prev) => ({ ...prev, [channel]: { ...entry, seq, fading: false } }));
    // Only touch the entry we posted — a newer post owns the channel.
    const ifMine = (fn) => setPillChannels((prev) => (prev[channel]?.seq === seq ? fn(prev) : prev));
    if (entry.final) {
      timers[channel] = [
        setTimeout(() => ifMine((prev) => ({ ...prev, [channel]: { ...prev[channel], fading: true } })), 1000),
        setTimeout(() => ifMine((prev) => { const next = { ...prev }; delete next[channel]; return next; }), 1400),
      ];
    }
    if (opts?.after) {
      const [ms, patch] = opts.after;
      (timers[channel] ||= []).push(setTimeout(() => ifMine((prev) => {
        const next = { ...prev };
        if (patch === null) delete next[channel];
        else next[channel] = { ...next[channel], ...patch };
        return next;
      }), ms));
    }
  }, []);
  useEffect(() => () => Object.values(pillTimersRef.current).forEach((ts) => ts.forEach(clearTimeout)), []);
  const pillShown = useMemo(() => {
    const rank = (e) => (e.error ? 3 : e.final || e.question ? 2 : 1);
    return Object.values(pillChannels).sort((a, b) => rank(b) - rank(a) || b.seq - a.seq)[0] || null;
  }, [pillChannels]);
  // Status messages: logged (Settings → Diagnostics), mirrored into the
  // optional debug status bar, and posted to the pill. Messages ending in
  // "…"/"..." are in-progress; anything else is final. An `action`
  // ({label, run}) puts its button on the pill, which stays to be pressed.
  // `sticky`: the pill stays, as an error, until the next status replaces
  // it (a save that will not succeed on a retry).
  const setStatus = useCallback((msg, action, { sticky = false } = {}) => {
    const text = String(msg);
    setStatusRaw(text);
    logSys(text, sticky ? "error" : "");
    const ongoing = /(\.\.\.|…)\s*$/.test(text);
    if (sticky) postPill("status", { msg: text, action: action || undefined, error: true });
    else if (action) postPill("status", { msg: text, action }, { after: [8000, null] });
    else postPill("status", { msg: text, spinner: ongoing, final: !ongoing });
  }, [postPill, logSys]);
  // A workspace being deleted takes no more changes: a refused change, or
  // the session's listing, turns the tab read-only and says why
  // (app/useWorkspaceDeleting.js).
  const sayDeleting = useWorkspaceDeleting({ shareMode, setStatus, setWorkspace, setWorkspaces, setReadOnly, setShareInfo });
  const [loading, setLoading] = useState(false);
  // Window layout: ordered window ids per dock slot. Sizes are handled by
  // react-resizable-panels (persisted via autoSaveId), so this only stores
  // which window lives where and in what order.
  const [layout, setLayout] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("gamma-layout") || "null");
      if (saved && saved.left && saved.right && saved.bottom) return saved;
    } catch {}
    return { left: [], right: ["notes", "chat"], bottom: [] };
  });
  useEffect(() => {
    try { localStorage.setItem("gamma-layout", JSON.stringify(layout)); } catch {}
  }, [layout]);

  // Move a window to a slot at an index (drag-to-dock and drag-to-reorder).
  function moveWindow(id, side, index) {
    setLayout((prev) => {
      const next = {
        left: prev.left.filter((w) => w !== id),
        right: prev.right.filter((w) => w !== id),
        bottom: prev.bottom.filter((w) => w !== id),
      };
      const arr = [...next[side]];
      arr.splice(Math.max(0, Math.min(index, arr.length)), 0, id);
      next[side] = arr;
      return next;
    });
  }
  // Open tabs (Chrome-style): [{id, title}], stored PER USER. localStorage is
  // only a cache for instant paint — the server (/api/prefs/open-tabs) is the
  // source of truth, so tabs follow the account across browsers. Local changes
  // are debounce-pushed; regaining focus pulls the latest stored state.
  // Persistence happens inside the updater (not an effect) so a user switch
  // can't race an in-flight save into the wrong key.
  const [openTabs, setOpenTabs] = useState([]);
  // The tabs' arrangement into groups side by side (workspace/tabLayout.js,
  // docs/dev/tab_groups.md): this browser's, kept per account and workspace
  // like the dock layout, while the tabs themselves stay the synced list
  // above. One group is live and holds the page on screen in the work area;
  // every other shows its page to read (workspace/PagePane.jsx). Set and
  // kept in one step, as the tabs are, so an account switch cannot race a
  // write into the wrong key.
  const [tabLayout, setTabLayout] = useState(() => singleLayout([]));
  const tabLayoutRef = useRef(tabLayout);
  tabLayoutRef.current = tabLayout;
  function keepTabLayout(next) {
    tabLayoutRef.current = next;
    setTabLayout(next);
    const u = prefsUserRef.current;
    if (u) { try { localStorage.setItem(`gamma-tab-layout:${u}`, JSON.stringify(next)); } catch {} }
  }
  // Tabs read from this browser's cache or the server's copy: the strip
  // takes them without moving (OpenTabs `still`).
  const tabsReadRef = useRef(new WeakSet());
  // Local viewing order, separate from the link-jump Back stack and the
  // synced tab order. Home keeps its folder/label so closing a page can
  // return to the view it was opened from.
  const tabHistoryRef = useRef([]);
  useEffect(() => {
    if (shareMode || !prefsUserRef.current) return;
    const blockId = focusedBlockId || null;
    const history = tabHistoryRef.current.filter((entry) => entry.blockId !== blockId);
    tabHistoryRef.current = [...history, { blockId, folder: folderFilter, label: labelFilter }];
  }, [focusedBlockId, folderFilter, labelFilter, authUser?.user, wsId, shareMode]);
  const prefsUserRef = useRef(""); // whose tabs are currently loaded
  const tabsSyncRef = useRef("");  // updated_at of the last server state we applied/wrote
  const tabsPushTimerRef = useRef(null);
  // Debounced PUT of one synced pref (/api/prefs/<key>): quick successive
  // changes collapse into the last value; `onSaved` gets the server reply.
  // Shared by the open tabs, the recents queue and the pinned folders.
  function pushPrefSoon(timerRef, key, value, onSaved) {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      timerRef.current = null;
      if (!prefsUserRef.current) return;
      try {
        const d = await apiJson(`${API}/prefs/${key}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        });
        onSaved?.(d);
      } catch {}
    }, 600);
  }
  function pushTabsToServer(tabs) {
    pushPrefSoon(tabsPushTimerRef, "open-tabs", tabs, (d) => { tabsSyncRef.current = d.updated_at || ""; });
  }
  // Apply a server-side tabs state locally without echoing it back. A pending
  // local push is cancelled — the server copy applied here supersedes it (the
  // merge below re-pushes when this window still holds a tab the server lost).
  function applyServerTabs(user, value, updatedAt) {
    tabsSyncRef.current = updatedAt || "";
    if (tabsPushTimerRef.current) {
      clearTimeout(tabsPushTimerRef.current);
      tabsPushTimerRef.current = null;
    }
    setOpenTabs((prev) => {
      let tabs = orderTabs(Array.isArray(value) ? value : []);
      // The page open in THIS window keeps its tab even when the stored state
      // lacks it (just opened here and not pushed yet, or closed elsewhere
      // while it's still on screen here) — merged and pushed back.
      const fid = focusedBlockIdRef.current;
      if (fid && !tabs.some((t) => t.id === fid)) {
        tabs = [...tabs, prev.find((t) => t.id === fid) || { id: fid, title: T("Untitled") }];
        pushTabsToServer(tabs);
      }
      try { localStorage.setItem(`gamma-tabs:${user}`, JSON.stringify(tabs)); } catch {}
      tabsReadRef.current.add(tabs);
      return tabs;
    });
  }
  // Pinned tabs always sit left of unpinned ones; the partition is stable, so
  // pinning a tab lands it right after the existing pinned group and unpinning
  // drops it at the front of the unpinned group — no explicit move needed.
  const orderTabs = (tabs) => [...tabs.filter((t) => t.pinned), ...tabs.filter((t) => !t.pinned)];
  function updateTabs(updater) {
    setOpenTabs((prev) => {
      const next = orderTabs(typeof updater === "function" ? updater(prev) : updater);
      const u = prefsUserRef.current;
      if (u) {
        try { localStorage.setItem(`gamma-tabs:${u}`, JSON.stringify(next)); } catch {}
        pushTabsToServer(next);
      }
      return next;
    });
  }
  // pinned: true | undefined (undefined keys drop out of the synced JSON).
  function toggleTabPinned(id) {
    updateTabs((prev) => prev.map((t) => (t.id === id ? { ...t, pinned: t.pinned ? undefined : true } : t)));
  }
  // Every change of the arrangement: the layout is kept, the synced tabs
  // take its order, and the page on screen follows the live group's active
  // tab (the library when it has none).
  // `target`: what a press on a group's view of its page was on, which the
  // page opening takes up (openBlock): {edit: {id, clickPos}} a note,
  // {title: true} the title, {labels: true} its labels.
  function applyLayout(next, target) {
    const prev = tabLayoutRef.current;
    // a second press on a view whose page is still opening, on a note, the
    // title or the labels: the open takes this press's target instead
    if (next === prev) {
      const want = activeGroup(next)?.active || "";
      const aimed = target?.edit || target?.title || target?.labels || target?.then;
      if (aimed && want && want === tabPick && want !== focusedBlockId) selectTab(want, target);
      return;
    }
    keepTabLayout(next);
    const order = tabOrder(next);
    if (order.join("\n") !== tabOrder(prev).join("\n")) {
      updateTabs((tabs) => [...tabs].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id)));
    }
    const want = activeGroup(next)?.active || "";
    if (want === (tabPick || focusedBlockId || "")) return;
    if (want) selectTab(want, target);
    else if (focusedBlockId) goHome(true, true);
  }
  // A group's tabs as its strip shows them, pinned first.
  const groupTabs = (group) => orderTabs(group.tabs.map((id) => openTabs.find((tab) => tab.id === id)).filter(Boolean));
  const activateTabGroup = (groupId, tabId, target) => applyLayout(activateGroupIn(tabLayoutRef.current, groupId, tabId), target);
  const moveTabToGroup = (tabId, groupId, index) => applyLayout(moveTab(tabLayoutRef.current, tabId, groupId, index));
  const splitTabGroup = (groupId, side, tabId) => applyLayout(splitGroup(tabLayoutRef.current, groupId, side, tabId));
  // A tab into a group of its own beside the one it is in: the tab menu's,
  // the bar menu's and the palette's Split right. The only tab of a group
  // opens an empty group beside it instead, where the library shows to
  // pick that group's page.
  const splitTab = (tabId, side) => {
    const g = groupOf(tabLayoutRef.current, tabId);
    if (g) splitTabGroup(g.id, side, g.tabs.length === 1 ? null : tabId);
  };
  const joinTabGroups = () => applyLayout(joinAll(tabLayoutRef.current));
  // The pane a tab is not in, while there are two (the tab menu's Move to
  // the other pane).
  const otherPane = (tabId) => {
    const groups = groupsOf(tabLayoutRef.current);
    return groups.length === 2 ? groups.find((g) => !g.tabs.includes(tabId)) || null : null;
  };
  const closeTabGroup = (groupId) => applyLayout(removeGroup(tabLayoutRef.current, groupId));
  const resizeTabSplit = (splitId, sizes) => {
    const next = resizeSplit(tabLayoutRef.current, splitId, sizes);
    if (next !== tabLayoutRef.current) keepTabLayout(next);
  };
  // A strip's reorder: the dragged tab takes the target's place among the
  // tabs as shown; dragging never crosses the pinned/unpinned boundary.
  const reorderGroupTabs = (groupId, dragged, target) => {
    const group = groupById(tabLayoutRef.current, groupId);
    if (!group) return;
    const shown = groupTabs(group);
    const from = shown.findIndex((tab) => tab.id === dragged), to = shown.findIndex((tab) => tab.id === target);
    if (from < 0 || to < 0 || from === to || !!shown[from].pinned !== !!shown[to].pinned) return;
    const next = [...shown];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    applyLayout(reorderGroup(tabLayoutRef.current, groupId, next.map((tab) => tab.id)));
  };
  // Where a group's view of a page stands, kept where the page's opening
  // reads it (openBlock): its PDF's scroll (tabScrollRef), its notes' top
  // row (notesPlaceRef) and its zoom, the page's own (pageLayoutsRef), so
  // the page made live opens exactly as its view showed it.
  const paneScroll = useCallback((pageId, entry) => { tabScrollRef.current[pageId] = entry; }, []);
  const paneNotesPlace = useCallback((pageId, place) => { notesPlaceRef.current[pageId] = place; }, []);
  const paneZoom = useCallback((pageId, zoom) => {
    const user = prefsUserRef.current;
    const cur = pageLayoutsRef.current[pageId];
    if (!zoom || !user || cur?.pdfScale === zoom) return;
    pageLayoutsRef.current[pageId] = { ...(cur || {}), pdfScale: zoom };
    try { localStorage.setItem(`gamma-page-layouts:${user}`, JSON.stringify(pageLayoutsRef.current)); } catch {}
  }, []);
  // Last-read positions, synced like tabs so "jump to last read" follows the
  // account across browsers: blockId -> {page, at}. Per-paper entries merged
  // newest-wins, so two open windows only conflict when reading the SAME
  // paper (last writer wins).
  const readPosRef = useRef({});
  const readPosLoadedRef = useRef(false); // first server response arrived
  const readPosPushTimerRef = useRef(null);
  // Serialization of the copy the server is CONFIRMED to hold (successful
  // PUT, or a pull that showed both sides equal) — lets pushes no-op when
  // there is nothing new to say.
  const readPosSentRef = useRef("");
  // Merge the server copy in (per-entry newest-wins) and report whether the
  // merged map holds anything the server doesn't — i.e. a local entry the
  // server never received because a push was dropped while it was down or
  // restarting. Callers push the merged map back when so: pushing a superset
  // of what was just pulled can only add entries, never regress one, and it
  // doubles as the retry for those silently-dropped pushes.
  function mergeReadPos(user, server) {
    const merged = { ...readPosRef.current };
    const s = server && typeof server === "object" ? server : {};
    for (const [id, e] of Object.entries(s)) {
      if (!e || typeof e.page !== "number") continue;
      if (!merged[id] || (e.at || "") > (merged[id].at || "")) merged[id] = e;
    }
    readPosRef.current = merged;
    try { localStorage.setItem(`gamma-read-pos:${user}`, JSON.stringify(merged)); } catch {}
    const newer = Object.entries(merged).filter(([id, e]) => !s[id] || (e.at || "") > (s[id].at || ""));
    dbg("read-pos merged;", Object.keys(s).length, "server entries;",
      newer.length ? `local newer: ${newer.map(([id, e]) => `${id}=p${e.page}`).join(",")}` : "in sync");
    // In sync means the server is confirmed to hold exactly this copy.
    if (!newer.length) readPosSentRef.current = JSON.stringify({ value: merged });
    return newer.length > 0;
  }
  // Server writes are throttled hard: the first change arms one 15s timer
  // and every later change rides it, so steady reading costs at most four
  // PUTs a minute instead of one per page turn. This never risks the
  // position — localStorage gets the instant copy, and blur/pagehide flush
  // the armed timer, so the long window only delays what OTHER devices see
  // while this window still has focus.
  const READ_POS_PUSH_MS = 15000;
  function pushReadPosSoon(u) {
    if (readPosPushTimerRef.current) return; // armed — this change rides it
    dbg("read-pos push armed (15s)");
    readPosPushTimerRef.current = setTimeout(async () => {
      readPosPushTimerRef.current = null;
      if (prefsUserRef.current !== u) return;
      const body = JSON.stringify({ value: readPosRef.current });
      if (body === readPosSentRef.current) { dbg("read-pos push skipped (server current)"); return; }
      try {
        await apiJson(`${API}/prefs/read-pos`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body,
        });
        readPosSentRef.current = body;
        dbg("read-pos pushed OK");
      } catch (err) {
        dbg("read-pos push FAILED:", err?.message || String(err));
      }
    }, READ_POS_PUSH_MS);
  }
  // Text-only pages remember their topmost visible block ("" = at the top)
  // in the same synced map, with page 0 so the PDF restore path ignores it.
  function recordNotePos(pageId, blockId) {
    const u = prefsUserRef.current;
    if (!u || !pageId) return;
    const prev = readPosRef.current[pageId];
    if (prev && prev.page === 0 && (prev.block || "") === (blockId || "")) return;
    let at = new Date().toISOString();
    if (prev?.at && prev.at >= at) at = new Date(new Date(prev.at).getTime() + 1).toISOString();
    readPosRef.current = { ...readPosRef.current, [pageId]: { page: 0, block: blockId || "", at } };
    try { localStorage.setItem(`gamma-read-pos:${u}`, JSON.stringify(readPosRef.current)); } catch {}
    pushReadPosSoon(u);
  }
  function recordReadPos(blockId, page) {
    const u = prefsUserRef.current;
    if (!u || !blockId || !(page > 0)) return;
    const prev = readPosRef.current[blockId];
    if (prev?.page === page) return;
    // A new local action must supersede the entry it replaces even when that
    // entry came from a machine with a fast clock — bump 1ms past it.
    let at = new Date().toISOString();
    if (prev?.at && prev.at >= at) at = new Date(new Date(prev.at).getTime() + 1).toISOString();
    const next = { ...readPosRef.current, [blockId]: { page, at } };
    const ids = Object.keys(next);
    if (ids.length > 200) {
      ids.sort((a, b) => (next[a].at || "").localeCompare(next[b].at || ""));
      for (const id of ids.slice(0, ids.length - 200)) delete next[id];
    }
    readPosRef.current = next;
    try { localStorage.setItem(`gamma-read-pos:${u}`, JSON.stringify(next)); } catch {}
    dbg("read-pos record", blockId, "→ page", page);
    pushReadPosSoon(u);
  }
  // Multi-browser convergence: whenever this window regains focus, pull the
  // latest stored tabs. Skipped while a local push is pending (ours is newer).
  useEffect(() => {
    async function pullTabs() {
      const u = prefsUserRef.current;
      if (!u || document.hidden || tabsPushTimerRef.current) return;
      try {
        const d = await apiJson(`${API}/prefs/open-tabs`);
        if (prefsUserRef.current !== u || !d.updated_at) return;
        if (d.updated_at !== tabsSyncRef.current) applyServerTabs(u, d.value, d.updated_at);
      } catch {}
    }
    async function pullReadPos() {
      const u = prefsUserRef.current;
      if (!u || document.hidden || readPosPushTimerRef.current) return;
      try {
        const d = await apiJson(`${API}/prefs/read-pos`);
        if (prefsUserRef.current !== u) return;
        readPosLoadedRef.current = true;
        if (mergeReadPos(u, d.value)) pushReadPosSoon(u);
      } catch {}
    }
    async function pullRecents() {
      const u = prefsUserRef.current;
      if (!u || document.hidden || recentsPushTimerRef.current) return;
      try {
        const d = await apiJson(`${API}/prefs/recent-views`);
        if (prefsUserRef.current !== u || !d.updated_at) return;
        if (d.updated_at !== recentsSyncRef.current) applyServerRecents(u, d.value, d.updated_at);
      } catch {}
    }
    // Covers for whatever the recents pull just brought in: a delta fetch —
    // only snapshots newer than the newest one already held ride along.
    async function pullSnaps() {
      const u = prefsUserRef.current;
      if (!u || document.hidden || !snapsSyncedRef.current) return;
      const newest = Object.values(pageSnapsRef.current)
        .reduce((m, e) => ((e?.at || "") > m ? e.at : m), "");
      try {
        const d = await apiJson(`${API}/page-snaps?after=${encodeURIComponent(newest)}`);
        if (prefsUserRef.current !== u) return;
        if (d.snaps && Object.keys(d.snaps).length) mergePageSnaps(d.snaps);
      } catch {}
    }
    // Losing focus flushes the armed read-pos push right away, so the window
    // being switched TO (or a refresh moments later) pulls the fresh value —
    // the 15s throttle would otherwise delay a quick scroll-then-switch.
    // Deliberately not marked confirmed: keepalive can't report success, and
    // the pull-merge heal covers a flush the server never got. The armed
    // timer goes once the PUT is sent, not before: a close flushes on
    // pagehide and on going hidden, and Firefox can stop a pagehide listener
    // part way, so the other one sends it again.
    function flushReadPos() {
      if (!readPosPushTimerRef.current || !prefsUserRef.current) return;
      const body = JSON.stringify({ value: readPosRef.current });
      if (body === readPosSentRef.current) dbg("read-pos flush skipped (server current)");
      else {
        dbg("read-pos flushed (blur/pagehide)");
        // keepalive when the page's keepalive room has space for it at the
        // normal priority (shared/lib/keepalive.js): small, and recoverable
        try {
          keepaliveFetch(`${API}/prefs/read-pos`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body,
            credentials: "same-origin",
          }, { priority: "normal" }).catch(() => {});
        } catch {}
      }
      clearTimeout(readPosPushTimerRef.current);
      readPosPushTimerRef.current = null;
    }
    // Focus flaps (alt-tabbing through windows) must not hammer the server:
    // wake pulls run at most once per 15s. Cross-window handoff still
    // converges promptly — the leaving window's blur flush lands first, and
    // the arriving window usually last pulled more than 15s ago.
    let lastWakeAt = 0;
    const onWake = () => {
      if (Date.now() - lastWakeAt < 15000) return;
      lastWakeAt = Date.now();
      pullTabs();
      pullReadPos();
      pullRecents();
      pullSnaps();
    };
    const onVisibility = () => { if (document.hidden) flushReadPos(); else onWake(); };
    window.addEventListener("focus", onWake);
    window.addEventListener("blur", flushReadPos);
    window.addEventListener("pagehide", flushReadPos);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onWake);
      window.removeEventListener("blur", flushReadPos);
      window.removeEventListener("pagehide", flushReadPos);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
  // Recently-viewed pages — the library's top shortcut bar ([{id, at}], most
  // recent first, capped 24). Synced across devices like the tab strip:
  // whole-list last-write-wins on /api/prefs/recent-views (localStorage is
  // the instant-paint cache), so a × removal sticks everywhere instead of
  // being resurrected by a union merge. Also drives the "viewed" home sort,
  // which therefore ranks account-wide.
  const [recentViews, setRecentViews] = useState([]);
  const recentsSyncRef = useRef("");  // updated_at of the last server state we applied/wrote
  const recentsPushTimerRef = useRef(null);
  function pushRecentsToServer(list) {
    pushPrefSoon(recentsPushTimerRef, "recent-views", list, (d) => { recentsSyncRef.current = d.updated_at || ""; });
  }
  function applyServerRecents(user, value, updatedAt) {
    recentsSyncRef.current = updatedAt || "";
    if (recentsPushTimerRef.current) {
      clearTimeout(recentsPushTimerRef.current);
      recentsPushTimerRef.current = null;
    }
    let list = (Array.isArray(value) ? value : []).filter((r) => r && r.id).slice(0, RECENTS_CAP);
    // The page open in THIS window stays the freshest entry even when the
    // stored queue predates it (just opened here, push not flushed yet) —
    // merged and pushed back, tabs-style.
    const fid = focusedBlockIdRef.current;
    if (fid && list[0]?.id !== fid) {
      list = [{ id: fid, at: new Date().toISOString() }, ...list.filter((r) => r.id !== fid)].slice(0, RECENTS_CAP);
      pushRecentsToServer(list);
    }
    homeReadRef.current.add(list);
    setRecentViews(list);
    try { localStorage.setItem(`gamma-recent-views:${user}`, JSON.stringify(list)); } catch {}
  }
  function pushRecentView(id) {
    if (!id) return;
    setRecentViews((prev) => {
      const next = [{ id, at: new Date().toISOString() }, ...prev.filter((r) => r.id !== id)].slice(0, RECENTS_CAP);
      const u = prefsUserRef.current;
      if (u) { try { localStorage.setItem(`gamma-recent-views:${u}`, JSON.stringify(next)); } catch {} }
      pushRecentsToServer(next);
      return next;
    });
  }
  // The × on a recents card: drop the entry and its snapshot, on every device.
  function removeRecentView(id) {
    const u = prefsUserRef.current;
    setRecentViews((prev) => {
      const next = prev.filter((r) => r.id !== id);
      if (u) { try { localStorage.setItem(`gamma-recent-views:${u}`, JSON.stringify(next)); } catch {} }
      pushRecentsToServer(next);
      return next;
    });
    dropPageSnap(id);
  }
  // Page snapshots — a small JPEG of the viewer at the last-read spot,
  // captured from the already-rendered pdf.js canvases and shown as the
  // recents-card cover ({pageId: {img, at}}). Stored server-side with the
  // workspace (/api/page-snaps, per-page newest-`at` wins) so covers follow
  // the synced recents strip across devices; localStorage is the
  // instant-paint cache, capped so its footprint stays bounded. A viewer
  // pushes nothing (the server refuses its writes) and keeps its captures in
  // that cache. All writes go through setSnapsState so the ref, the state
  // and the cache never diverge.
  const [pageSnaps, setPageSnaps] = useState({});
  const pageSnapsRef = useRef({});
  const snapsSyncedRef = useRef(false); // first server copy merged (gates the prune)
  const snapPendingRef = useRef({});    // {id: {img, at}} awaiting push
  const snapPushTimerRef = useRef(null);
  const snapsWritableRef = useRef(canWriteWorkspace);
  snapsWritableRef.current = canWriteWorkspace;
  // The localStorage copy is written once the captures rest: serializing
  // every cover (hundreds of KB of JPEG) after each scroll-settle capture
  // was a synchronous stall in the middle of reading. The ref and the state
  // change at once; the cache lags by a moment and is what the next load
  // paints first — the server holds the covers themselves.
  const snapsWriteTimerRef = useRef(null);
  function setSnapsState(next) {
    pageSnapsRef.current = next;
    setPageSnaps(next);
    clearTimeout(snapsWriteTimerRef.current);
    snapsWriteTimerRef.current = setTimeout(() => {
      snapsWriteTimerRef.current = null;
      const u = prefsUserRef.current;
      if (u) { try { localStorage.setItem(`gamma-page-snaps:${u}`, JSON.stringify(pageSnapsRef.current)); } catch {} }
    }, 1500);
  }
  // Scroll-settle captures can fire every second or two while reading; batch
  // the uploads so steady reading costs one small PUT burst per 5s, always
  // sending the newest capture per page.
  function pushSnapSoon(id, img, at) {
    snapPendingRef.current[id] = { img, at };
    if (snapPushTimerRef.current) return; // armed — this capture rides it
    snapPushTimerRef.current = setTimeout(() => {
      snapPushTimerRef.current = null;
      const pending = snapPendingRef.current;
      snapPendingRef.current = {};
      if (!prefsUserRef.current || !snapsWritableRef.current) return;
      for (const [pid, snap] of Object.entries(pending)) {
        apiJson(`${API}/page-snaps/${encodeURIComponent(pid)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(snap),
        }).catch(() => {});
      }
    }, 5000);
  }
  function savePageSnap(id, img) {
    if (!id || !img) return;
    const at = new Date().toISOString();
    const next = { ...pageSnapsRef.current, [id]: { img, at } };
    const ids = Object.keys(next);
    if (ids.length > RECENTS_CAP) {
      ids.sort((a, b) => (next[a].at || "").localeCompare(next[b].at || ""));
      for (const drop of ids.slice(0, ids.length - RECENTS_CAP)) delete next[drop];
    }
    setSnapsState(next);
    pushSnapSoon(id, img, at);
  }
  function dropPageSnap(id) {
    if (pageSnapsRef.current[id]) {
      const next = { ...pageSnapsRef.current };
      delete next[id];
      setSnapsState(next);
    }
    delete snapPendingRef.current[id];
    if (!snapsWritableRef.current) return;
    apiJson(`${API}/page-snaps/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
  }
  // Merge a server copy in (per-page newest-`at` wins). With `heal` (the full
  // login pull only — delta pulls would re-push everything), local entries
  // the server lacks or holds older are uploaded: that retries dropped pushes
  // and doubles as the one-time migration of pre-sync localStorage snapshots.
  function mergePageSnaps(server, { heal = false } = {}) {
    const s = server && typeof server === "object" ? server : {};
    const merged = { ...pageSnapsRef.current };
    for (const [id, e] of Object.entries(s)) {
      if (!e?.img) continue;
      if (!merged[id] || (e.at || "") > (merged[id].at || "")) merged[id] = e;
    }
    if (heal) {
      for (const [id, e] of Object.entries(pageSnapsRef.current)) {
        if (e?.img && (!s[id] || (e.at || "") > (s[id].at || ""))) pushSnapSoon(id, e.img, e.at || "");
      }
    }
    setSnapsState(merged);
  }
  // A snapshot only exists to cover a recents card — when a page falls out of
  // the (capped) queue, its local copy goes too (the server prunes itself by
  // a count cap, so no DELETE here: another device's queue may still want
  // it). Gated until the login pull has applied the server queue + covers, so
  // a pre-sync local queue can't sweep covers synced from another device.
  useEffect(() => {
    if (!snapsSyncedRef.current) return;
    const keep = new Set(recentViews.map((r) => r.id));
    const stale = Object.keys(pageSnaps).filter((id) => !keep.has(id));
    if (!stale.length) return;
    const next = { ...pageSnapsRef.current };
    for (const id of stale) delete next[id];
    setSnapsState(next);
  }, [recentViews, pageSnaps]);
  const [tabMenu, setTabMenu] = useState(null); // {id, pinned, x, y} — tab right-click menu
  const [barMenu, setBarMenu] = useState(null); // {pane, x, y} — a bar's right-click Customize… menu
  // The tab a press picked while its page opens (selectTab): the strip shows
  // it selected from the press's own commit, while the page it leaves stays
  // on screen until the new one's tree lands (openBlock). "" when none is.
  const [tabPick, setTabPick] = useState("");
  // The arrangement follows the tabs and the page on screen: a tab opened
  // lands in the live group, one closed leaves its group, and the page on
  // screen (the one a press picked, while it opens) is its group's active
  // tab, with that group live.
  useEffect(() => {
    if (shareMode || !prefsUserRef.current) return;
    const next = reconcileGroups(tabLayoutRef.current, openTabs.map((tab) => tab.id), tabPick || focusedBlockId || null);
    if (next !== tabLayoutRef.current) keepTabLayout(next);
  }, [openTabs, focusedBlockId, tabPick, shareMode]); // eslint-disable-line react-hooks/exhaustive-deps
  // The group the page on screen stands in, where TabGroups lays the page
  // view over (`liveId`): the group of the page App holds, else (the
  // library) the live one. A press on another group's view makes that
  // group live at once, but its view stays, and the page on screen where
  // it is, until the pressed page has opened: the two change places in the
  // commit its tree lands in, so neither shows the other's page meanwhile.
  // The library while a picked tab opens stays where it was too.
  const shownGroupRef = useRef(null);
  // The last page with a viewer on screen, whose notes the docked Notes
  // window keeps while pages stand side by side (`notesPaper`, below).
  const notesPaperRef = useRef(null);
  const shownGroupId = (() => {
    if (focusedBlockId) return groupOf(tabLayout, focusedBlockId)?.id || tabLayout.active;
    if (tabPick && shownGroupRef.current && groupById(tabLayout, shownGroupRef.current)) return shownGroupRef.current;
    return tabLayout.active;
  })();
  shownGroupRef.current = shownGroupId;
  // A view of a page in a group that is not live, or in the docked Notes
  // window (PagePane): what it opens at and what App lends it.
  function paneProps(pageId) {
    const saved = pageLayoutsRef.current[pageId];
    return {
      pageId, title: openTabs.find((tab) => tab.id === pageId)?.title,
      overlay: (id, children) => collabRef.current.overlay(id, children),
      scroll: tabScrollRef.current[pageId], startPage: readPosRef.current[pageId]?.page || 0,
      zoom: saved?.pdfScale || pdfScale, pdfHidden: !!saved?.pdfHidden, notebookView: nbViewPages.has(pageId),
      notesPlace: notesPlaceRef.current[pageId], shared: paneShared,
      onScroll: paneScroll, onZoom: paneZoom, onNotesPlace: paneNotesPlace,
    };
  }
  // The latest openBlock call: an earlier open landing after it neither
  // posts nor takes down the "Opening…" of the one the reader is waiting on.
  const openSeqRef = useRef(0);
  // The pages the listing does not carry (docs/dev/work_folders.md): the
  // open work folder's directory, its files a page of rows at a time
  // (explorer/useFolderRows.js), and the summaries of the files open in a
  // tab, viewed lately or open now (explorer/useSummaries.js),
  // each read again with the listing. `knownBlocks` is every page row the
  // app has, the listing's first; `pageBlocks` below derives from it.
  const workFolder = folderFilter && !labelFilter ? workOf(workTree, folderFilter) : "";
  const workSort = useWorkSort();
  const folderRows = useFolderRows({ folderId: workFolder ? folderFilter : "", sort: workSort.sort, order: workSort.order, version: libTree });
  const listingIds = useMemo(() => new Set([...homeBlocks, ...folderRows.rows].map((b) => b.id)), [homeBlocks, folderRows.rows]);
  const summaries = useSummaries({
    ids: [...openTabs.map((tab) => tab.id), ...recentViews.map((r) => r.id), focusedBlockId],
    known: (id) => listingIds.has(id), version: libTree, enabled: !shareMode && !!authUser?.user,
  });
  const knownBlocks = useMemo(() => mergeKnown(homeBlocks, [...folderRows.rows, ...summaries.rows]),
    [homeBlocks, folderRows.rows, summaries.rows]);
  // A rename or a delete made here, on every row the app holds of the page.
  const patchKnown = (id, fields) => { folderRows.patch(id, fields); summaries.patch(id, fields); };
  const dropKnown = (ids) => { folderRows.drop(ids); summaries.drop(ids); };
  // The format of the document each known page carries ("" for none), for
  // a tab's kind icon (tabs store only id, title and pinned).
  const tabKinds = useMemo(() => new Map(knownBlocks.map((b) => [b.id, pageDocument(b)?.format || ""])), [knownBlocks]);
  const tabKindOf = useCallback((id) => tabKinds.get(id) || "", [tabKinds]);

  // Background tasks (tasks/useTasks.js, docs/dev/tasks.md): the server's
  // jobs — exports, backups, restores, imports, the search indexer — and
  // this tab's own work — a PDF loading, an upload, an AI lookup — in one
  // list; the tray in the topbar shows it. A local row is
  // {kind, name, state, info, fraction (0..1 when the work can measure
  // itself), cancel (a function when it can be stopped)}. A share view
  // lists no jobs: it follows the exports it starts under its token, the
  // share's (`follow`), whoever the visitor is.
  const tasks = useTasks({ enabled: shareMode || !!authUser?.user, follow: shareMode,
    onFinished: (job, started, jobs) => onJobFinished(job, started, jobs) });
  const { addLocal: addTask, updateLocal: updateTask } = tasks;
  // The search indexer of the open workspace runs (Settings shows it).
  const indexing = tasks.jobs.some((job) => job.kind === "indexing" && job.workspace === workspace?.id && isActive(job));
  // The ones there that ended, as a key: the search asks again when it moves.
  const indexed = tasks.jobs.filter((job) => job.kind === "indexing" && job.workspace === workspace?.id && !isActive(job))
    .map((job) => job.id).join(",");
  // So does a batch metadata lookup there (Settings → Maintenance).
  const metadataJob = tasks.jobs.find((job) => job.kind === "metadata" && job.workspace === workspace?.id && isActive(job)) || null;
  const transferByUrlRef = useRef({});
  // Byte-level download state reported by the PDF viewer (skips local uploads).
  // One row per URL: a re-download (LRU eviction, retry) reactivates the
  // existing entry instead of stacking duplicates.
  // One clock per document load: every phase is stamped with the ms since
  // the viewer started opening this url (the "open" phase, or the first
  // phase seen for a new url). The stamps go to the system log and to
  // performance.mark("pdf-<phase>", {detail: {url, ms}}) — readable from
  // devtools' Performance panel and from the e2e timing probe
  // (docs/dev/pdf_loading.md).
  const pdfLoadClockRef = useRef({ url: "", t0: 0 });
  // Called from the viewer's layout effects — before paint — when the
  // document's page boxes are in the DOM: on "layout" (a skeleton from the
  // manifest, no document yet) and on "rendered". Applying the pending
  // restore HERE means the document appears already scrolled to its
  // position: no flash of the top, no visible jump. `scale` is the one the
  // boxes are laid out at, which the viewer reports with the phase: a
  // viewer mounted again starts from its own, not the one the host last
  // heard of, and re-places the position itself when its fit-width settles.
  function applyPendingRestore(url, scale) {
    const p = pendingRestoreRef.current;
    if (!p || p.url !== url || restoreTokenRef.current !== p.token) return;
    const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
    const ratio = (scale || pdfEffScaleRef.current || p.entry.scale || 1) / (p.entry.scale || 1);
    const targetTop = p.entry.top * ratio;
    if (scroller && scroller.scrollHeight > targetTop) {
      // and across, when it was kept (a zoom wider than the viewer)
      scroller.scrollTo({ top: targetTop, ...(p.entry.left != null ? { left: p.entry.left * ratio } : null), behavior: "instant" });
      pendingRestoreRef.current = null;
      restoreTokenRef.current++; // the fallback loop is no longer needed
      if (restoringForRef.current === p.blockId) restoringForRef.current = null;
      dbg("exact restore: applied pre-paint, top", Math.round(targetTop));
    }
  }
  function handlePdfLoadState(url, st) {
    const clock = pdfLoadClockRef.current;
    if (st.phase === "open" || clock.url !== url) { clock.url = url; clock.t0 = performance.now(); }
    const ms = Math.round(performance.now() - clock.t0);
    // System log: lifecycle transitions only — byte/page progress would spam it.
    if (st.phase !== "progress" && st.phase !== "measuring") {
      try { performance.mark(`pdf-${st.phase}`, { detail: { url, ms } }); } catch {}
      const shortUrl = url.length > 100 ? url.slice(0, 100) + "…" : url;
      logSys(`pdf ${st.phase} +${ms} ms${st.bytes ? ` (${fmtBytes(st.bytes)})` : ""}${st.detail ? ` — ${st.detail}` : ""}: ${shortUrl}`, st.phase === "error" ? "error" : "");
    }
    // Feed the shared status pill — one channel for the whole load lifecycle,
    // so load progress and status messages can never stack.
    if (st.phase === "start") {
      // Escalates if the server keeps us waiting with no bytes.
      postPill("pdf-load", { msg: t("Requesting PDF…"), spinner: true },
        { after: [6000, { msg: t("Still waiting — the server may be fetching the PDF from its source…") }] });
    } else if (st.phase === "progress") {
      postPill("pdf-load", {
        msg: st.total
          ? t("Downloading… {loaded} of {total} ({total2}%)", { loaded: fmtBytes(st.loaded), total: fmtBytes(st.total), total2: Math.min(99, Math.floor((st.loaded / st.total) * 100)) })
          : `Downloading… ${fmtBytes(st.loaded)}`,
        spinner: true,
      });
    } else if (st.phase === "done" || st.phase === "cached") {
      postPill("pdf-load", { msg: t("Preparing document…"), spinner: true });
    } else if (st.phase === "parsing") {
      postPill("pdf-load", { msg: t("Preparing document — parsing…"), spinner: true });
    } else if (st.phase === "measuring") {
      postPill("pdf-load", { msg: t("Preparing document — measuring page {done} of {total}…", { done: st.done + 1, total: st.total }), spinner: true });
    } else if (st.phase === "error") {
      postPill("pdf-load", { msg: t("PDF load failed — {error}", { error: st.detail || t("unknown error") }), error: true,
        action: { label: t("Retry"), run: () => pdfRetryRef.current?.() } });
    } else if (st.phase === "cancelled" || st.phase === "painted") {
      postPill("pdf-load", null);
    }
    if (st.phase === "layout") {
      // Page boxes from the manifest are in the DOM, the document itself is
      // still loading: the reader lands on their page now — the exact tab
      // position here, the last-read page through the coarse restore below,
      // which accepts a laid-out skeleton as "pages in the DOM".
      postPill("pdf-load", { msg: t("Preparing document…"), spinner: true });
      pdfLaidOutUrlRef.current = url;
      applyPendingRestore(url, st.scale);
      return;
    }
    if (st.phase === "rendered") {
      // Pages are in the DOM but the first canvas paint is still in flight —
      // keep the pill up until the viewer reports "painted". Safety-capped so
      // a paint that errors out can't leave the spinner stuck forever.
      postPill("pdf-load", { msg: t("Rendering page…"), spinner: true }, { after: [20000, null] });
      pdfRenderedUrlRef.current = url; // this document's pages are now in the DOM
      setPdfDocNonce((n) => n + 1);    // lets a pinned search re-find its matches here
      applyPendingRestore(url, st.scale);
      return;
    }
    // Only phases that own a transfer row from here on — the catch-all branch
    // below marks anything unrecognized as a failed download.
    if (!TRANSFER_PHASES.has(st.phase)) return;
    if (url.startsWith("/api/uploads/")) return;
    if (st.phase === "cached") {
      const id = transferByUrlRef.current[url];
      if (id) updateTask(id, { state: "done", info: "cached" });
      return;
    }
    if (st.phase === "start") {
      // One row per URL: a re-download (LRU eviction, retry) runs its row
      // again, unless the row was cleared meanwhile.
      const prevId = transferByUrlRef.current[url];
      if (prevId && tasks.reviveLocal(prevId, { state: "running", info: t("downloading…"), cancel: st.cancel, fraction: undefined })) return;
      const name = (pageTitle || decodeURIComponent((url.split("source_url=")[1] || url).split("/").pop() || "PDF")).slice(0, 60);
      transferByUrlRef.current[url] = addTask({ name, kind: "download", info: t("downloading…"), cancel: st.cancel });
    } else if (st.phase === "progress") {
      const id = transferByUrlRef.current[url];
      if (id) updateTask(id, {
        state: "running",
        info: st.total ? `${fmtBytes(st.loaded)} / ${fmtBytes(st.total)}` : `${fmtBytes(st.loaded)}…`,
        fraction: st.total ? st.loaded / st.total : undefined,
      });
    } else {
      const id = transferByUrlRef.current[url];
      if (!id) return;
      if (st.phase === "cancelled") {
        delete transferByUrlRef.current[url];
        tasks.removeLocal(id); // aborted navigation — drop the entry
      } else {
        updateTask(id, st.phase === "done"
          ? { state: "done", info: fmtBytes(st.bytes) }
          : { state: "failed", info: st.detail || "failed" });
      }
    }
  }
  const [dockPreview, setDockPreview] = useState(null); // {left, top, width, height} of the drop target while dragging a window
  const [collapsedWins, setCollapsedWins] = useState({}); // window id -> collapsed to header bar
  // One popover open at a time; any click outside a [data-popover] container closes it.
  const [openPopover, setOpenPopover] = useState(null); // "menu" | "share" | "user" | "search"
  // The Ctrl+P palette (library/QuickOpen.jsx): null, or {prefix} — "" lists
  // pages, ">" the commands (Ctrl+Shift+P).
  const [quickOpen, setQuickOpen] = useState(null);
  // A link (the boot deep link, a page or block link clicked in the app)
  // named a page that isn't in this workspace: {id, trashed} (trashed: the
  // page's Recently deleted entry when that is where it went — the notice
  // then offers Restore), while the notice under the topbar says so.
  // Cleared by Dismiss, Search the library, Restore or the next open.
  const [missingPage, setMissingPage] = useState(null);
  // The Recently deleted dialog (library/RecentlyDeleted.jsx).
  const [trashOpen, setTrashOpen] = useState(false);
  // A query handed to the workspace search as it opens (the listing's
  // "Search inside notes, PDFs and files"); cleared whenever the search is closed,
  // so a later plain open keeps whatever was typed in it.
  const [searchSeed, setSearchSeed] = useState("");
  useEffect(() => { if (openPopover !== "search") setSearchSeed(""); }, [openPopover]);
  const openSearchWith = (query) => { setQuickOpen(null); setSearchSeed(query); setOpenPopover("search"); };
  // The app commands' context (app/appCommands.js) and the account's
  // keybindings, refreshed every render for the once-mounted key listener.
  const appCmdRef = useRef(null);
  const bindingsRef = useRef({});
  // The notes tree's row handlers of the last render, so the palette can
  // run a block command on the focused row without an open editor.
  const rowPropsRef = useRef(null);
  // The open editor's row just moved in the DOM (keepEditorThrough): the
  // move blurs its editor in some browsers, and that blur must not close
  // it (onStartEdit).
  const keepEditRef = useRef(null);
  useEffect(() => {
    if (!openPopover) return;
    function onDown(e) {
      // Dropdown menus (shared/ui/Menus.jsx) portal to <body>: a pick inside a
      // popover's own dropdown is not a click outside the popover.
      if (!(e.target.closest && e.target.closest("[data-popover], .ctxMenu"))) setOpenPopover(null);
    }
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [openPopover]);
  // The owner's share of the open page: null = not loaded, {token: null} =
  // not shared, else {token, audience, role, users}. The link is derived.
  const [shareSettings, setShareSettings] = useState(null);
  const [shareError, setShareError] = useState("");
  // What the popover is about: {kind: "page", id} (the open page) or
  // {kind: "folder", id} (a folder of the home library).
  const [shareTarget, setShareTarget] = useState(null);
  const shareUrl = shareSettings?.token
    ? `${window.location.origin}${window.location.pathname}?share=${shareSettings.token}`
    : "";
  const [shareCopied, flashShareCopied, resetShareCopied] = useCopied();
  // Gamma Cloud publishing of the open page (the share popover's Gamma Cloud
  // section, docs/dev/mirror.md "Publishing"): GET /api/pages/{id}/publish
  // tagged with the page it belongs to, the action running, its refusal.
  // Offered where the server has cloud sign-in and is not itself the share
  // host (server-config's `guest` is false only there), never to the guest.
  const publishOffered = !shareMode && !!serverConfig?.cloud?.enabled && serverConfig?.guest !== false
    && !!authUser?.user && !authUser?.is_guest;
  const [publishState, setPublishState] = useState(null);
  const [publishBusy, setPublishBusy] = useState("");
  const [publishError, setPublishError] = useState(""); // a refusal's detail, or {message, limit} for the plan's cap
  const [publishCopied, flashPublishCopied, resetPublishCopied] = useCopied();
  // The publication's state while the share popover is open: every 5 s while
  // a round runs or a local edit waits to be synced, else every 20 s.
  const publishActive = !!publishState?.mirror?.status?.running || !!publishState?.mirror?.pending_local;
  useEffect(() => {
    if (openPopover !== "share" || !publishOffered || !focusedBlockId) return undefined;
    const t = setInterval(() => loadPublishState({ quiet: true }), publishActive ? 5000 : 20000);
    return () => clearInterval(t);
  }, [openPopover, publishOffered, focusedBlockId, publishActive]); // eslint-disable-line react-hooks/exhaustive-deps
  // Workspace search lives in search/SearchPanel.jsx (SearchPanel); App only holds what
  // the PDF viewer needs from it: the match highlights and the search hook.
  const [findMarks, setFindMarks] = useState([]); // [{page, rect, active}] painted by PdfViewer
  const [pdfDocNonce, setPdfDocNonce] = useState(0); // bumped when a document finishes rendering
  const pdfSearchRef = useRef(null); // set by PdfViewer: async (RegExp) => [{page, snippet, rects, pageW, pageH}]
  const pdfCaptureRef = useRef(null); // set by PdfViewer: async (areaHighlight) => PNG data URL (re-crops the rect from the document)
  // Stable wrapper for the notes tree's area-snapshot cards (a fresh function
  // every render would re-fire each card's crop effect). Resolves null until
  // the viewer has a document. A region's position is the picture's.
  const capturePdfArea = useCallback(
    (b) => pdfCaptureRef.current
      ? pdfCaptureRef.current({ position: blockPosition(b, placeKeyOf(b)) })
      : Promise.resolve(null),
    [],
  );

  // A job started here from a menu (a workspace export, a restore) shows
  // how far it got in the pill until it ends (onJobFinished clears it).
  useEffect(() => {
    for (const job of tasks.jobs) {
      if (!isActive(job) || !tasks.startedHere(job.id)?.pill) continue;
      postPill(`job:${job.id}`, { msg: `${taskTitle(job)} — ${job.state === "queued" ? t("waiting for its turn…") : progressText(job)}`, spinner: true });
    }
  }, [tasks.jobs, tasks.startedHere, postPill]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    function onKey(e) {
      // The app commands (app/appCommands.js, docs/dev/hotkeys.md): search,
      // the palettes, back, undo/redo, rename, the panes… under the
      // account's keybindings; an open dialog keeps only the ones meant for
      // it. Escape is not a command: it always clears.
      if (appCmdRef.current && dispatchHotkey(liveAppCommands(), e, appCmdRef.current, bindingsRef.current)) return;
      if (e.key === "Escape") {
        setOpenPopover(null);
        setThreadPop(null);
        setQuickOpen(null);
        setHomeMenu(null);
        setPropsPop(null);
        setSelectedPages((prev) => (prev.size ? new Set() : prev));
        // Not the Esc a note's editor took to select its note (useBlockSelection.js).
        if (!e.defaultPrevented) blockSel.clear();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // The mouse's back button is Back too, and with no jump left to unwind it
  // goes to the library. Its default, the window's history, would leave
  // Gamma: the app moves by replaceState, so the entry before it is what
  // came before (the desktop app's launcher). Not while a dialog is open, as
  // Alt+← is not. A share view keeps the default: its history is its
  // navigation (goSharedPage). The pointerup, not the mouseup: a pointerdown
  // whose default a surface takes away (the palette does) sends no mouseup
  // at all, and Chromium went back. In the capture phase, before a layer
  // can stop it on its way up.
  useEffect(() => {
    function onUp(e) {
      const c = appCmdRef.current;
      if (e.button !== 3 || !c || c.shareMode) return;
      e.preventDefault();
      if (liveAppCommands().some((cmd) => cmd.id === "app.back")) c.mouseBack();
    }
    window.addEventListener("pointerup", onUp, true);
    return () => window.removeEventListener("pointerup", onUp, true);
  }, []);
  const [pdfHidden, setPdfHidden] = useState(false);
  const [pdfScale, setPdfScale] = useState("page-width");
  // Every localStorage-backed user preference (the Settings dialog's state)
  // lives in useAppPrefs (prefs.js) — one hook, one storage key per entry;
  // the account-scoped ones follow the account through its profile
  // (useProfileSync). A guest's account is thrown away, so theirs stay local.
  const appPrefs = useAppPrefs();
  // The account's presets replaced this browser's own edited list on load
  // (prefs.js `onReplaced`): one question a session offers to push the
  // local list instead (docs/dev/handwriting.md "Presets follow the
  // account"). It is logged and holds the pill's "pens" channel for 8 s,
  // so the statuses of a page opening under it never end it. The answer
  // takes it down and writes the list through the strip's setInkTools
  // (normalizeTools), declared below.
  const inkAdoptAskedRef = useRef(false);
  const profileSync = useProfileSync(appPrefs, authUser?.user && !authUser.is_guest && !shareMode ? authUser.user : "", {
    onReplaced: ({ name, local }) => {
      if (name !== "inkTools" || inkAdoptAskedRef.current) return;
      inkAdoptAskedRef.current = true;
      const ask = t("Use this browser's pens instead?");
      logSys(ask);
      postPill("pens", { msg: ask, question: true, action: { label: t("Use this browser's"), run: () => {
        postPill("pens", null);
        setInkTools(local);
      } } }, { after: [8000, null] });
    },
  });
  const {
    theme, setTheme, pdfDarkPage, setPdfDarkPage, uiScale, setUiScale, recentThumbs, setRecentThumbs,
    palette, setPalette,
    language, setLanguage,
    fileLabels, setFileLabels,
    oaFallback, setOaFallback, metaAutoFetch, setMetaAutoFetch, pdfSaveLocal, setPdfSaveLocal, openAddsToLibrary, setOpenAddsToLibrary,
    embAnnots, setEmbAnnots, smoothWheel, setSmoothWheel,
    inkPenOnly, setInkPenOnly, inkPenWritesPdf, setInkPenWritesPdf, inkPressure, setInkPressure,
    inkFingerUndo, setInkFingerUndo, inkDrawHold, setInkDrawHold, inkScribbleErase, setInkScribbleErase,
    inkEraserReturn, setInkEraserReturn, inkEraserFilter, setInkEraserFilter,
    inkHoverPreview, setInkHoverPreview, inkPencilTap, setInkPencilTap, inkPencilSqueeze, setInkPencilSqueeze, inkHaptics, setInkHaptics,
    replayMode, setReplayMode, replayRate, setReplayRate, replayFollow, setReplayFollow, replayLeadIn, setReplayLeadIn,
    recordingTranscribe, setRecordingTranscribe,
    inkTools, setInkTools: setInkToolsRaw, inkEraserMode, setInkEraserMode, inkEraserSize, setInkEraserSize,
    inkLassoMode, setInkLassoMode, inkLassoPictures, setInkLassoPictures, textBoxStyle, setTextBoxStyle,
    inkShapeKind, setInkShapeKind, inkShapeFill, setInkShapeFill, inkRulerUnits, setInkRulerUnits,
    selTranslate, setSelTranslate, selTranslateAuto, setSelTranslateAuto,
    translateLang, setTranslateLang, translateModel, setTranslateModel,
    translateEffort, setTranslateEffort, translateParallel, setTranslateParallel,
    translateSpeed, setTranslateSpeed, translateKeep, setTranslateKeep,
    searchDetailsHome, setSearchDetailsHome, searchDetailsPaper, setSearchDetailsPaper,
    enterNewBlock, setEnterNewBlock, backlinksVisible, setBacklinksVisible,
    keybindings, setKeybindings,
    statusBarVisible, setStatusBarVisible, suggestTours, setSuggestTours, tourProgress, setTourProgress,
    inboxSystemNotifications, setInboxSystemNotifications,
    topbar, setTopbar, viewerTools, setViewerTools, highlighterColor, highlightPresets, setHighlightPresets,
    chatEffort, setChatEffort, chatSpeed, setChatSpeed, aiLoginCheck, setAiLoginCheck, metaModel, setMetaModel,
    dictationModel, setDictationModel, dictationLang, setDictationLang,
    chatSystem, setChatSystem, agentSystem, setAgentSystem,
    metaPrompt, setMetaPrompt, citePrompt, setCitePrompt,
    chatContextChars, setChatContextChars, metaContextChars, setMetaContextChars,
    multiContextChars, setMultiContextChars,
    toolRounds, setToolRounds, toolRoundsUnlimited, setToolRoundsUnlimited,
    agentReadChars, setAgentReadChars, agentPerms, setAgentPerms,
    agentEnabled, setAgentEnabled,
    chatImgAutoClear, setChatImgAutoClear, chatPictures, setChatPictures,
    fetchInBackground, setFetchInBackground, delegateReads, setDelegateReads,
  } = appPrefs;
  const viewerWrapRef = useRef(null);
  const pdfRetryRef = useRef(null); // set by PdfViewer: re-runs a failed load (pill's Retry button)
  const appRef = useRef(null);

  // --- AI chat: model switcher, PDF attachment, selection focus, report ---
  const [aiInfo, setAiInfo] = useState(null); // {enabled, provider, models, default}
  const [chatModel, setChatModel] = useState(() => {
    try { return localStorage.getItem("gamma-chat-model") || ""; } catch { return ""; }
  });
  const [agentPromptDraft, setAgentPromptDraft] = useState("");
  const [promptDraft, setPromptDraft] = useState("");
  // AI providers (Settings → AI providers): a user-managed list of API keys,
  // OpenAI-platform style. Keys are stored server-side per user; the server
  // only ever returns a masked hint, so key fields here start empty and an
  // empty key on edit means "keep the stored one".
  const [aiKeysInfo, setAiKeysInfo] = useState(null); // masked GET /ai/settings: {providers: [], protocols: []}
  // Per-entry results of the list's Test button (a tiny live completion):
  // id -> {busy} | {ok, model, latency_ms} | {ok: false, error}
  const [aiKeyTests, setAiKeyTests] = useState({});
  // id -> {busy} | normalized subscription allowance. API-key providers
  // return an explicit unavailable reason because their billing APIs differ.
  const [aiKeyUsage, setAiKeyUsage] = useState({});
  // Login connection check (POST /ai/health, Settings → Provider and models):
  // null = nothing to report; a failed result renders the chat panel's
  // warning strip (the chat error card's headline for the failure's kind)
  // until fixed or dismissed.
  const [aiHealth, setAiHealth] = useState(null);
  // The connect dialog (settings/SettingsAi.jsx useProviderEditor, the path
  // Settings → Server's shared entries take too). A saved change refreshes
  // the model switchers and, while the chat shows the login check's warning
  // strip, re-runs the check (the edit may have fixed or removed its
  // credential); a connection the dialog made is tested at once.
  const aiEditor = useProviderEditor({
    info: aiKeysInfo, setInfo: setAiKeysInfo,
    base: `${API}/ai/providers`, signIn: `${API}/ai/oauth/chatgpt`,
    onSaved: () => { refreshAiModels(); if (aiHealth) checkAiHealth(); },
    onConnected: (entry, form) => finishNewConnection(entry, !!form.fromChat),
  });

  // The settings page (account popover → Settings…): two-column modal,
  // categories on the left, the selected pane on the right.
  const [settingsOpen, setSettingsOpen] = useState(() => { // null | pane id — see settingsNavigation.js
    // A language change reloads the page; the pane it was made on comes back.
    try {
      const pane = sessionStorage.getItem(REOPEN_SETTINGS_KEY);
      if (pane) { sessionStorage.removeItem(REOPEN_SETTINGS_KEY); return pane; }
    } catch {}
    return null;
  });
  // Settings on one pane, closing the popover that led there (the account
  // menu, a notice, the sync pill, the Share popover's cloud row, the chat).
  const openSettingsPane = (pane) => { setOpenPopover(null); setSettingsOpen(pane); };
  // The first settings sync with Gamma Cloud found two different copies:
  // Settings → Account & sync asks which to keep, opened once per page load.
  const askedCloudChoice = useRef(false);
  useEffect(() => {
    if (!profileSync.cloudChoice || askedCloudChoice.current) return;
    askedCloudChoice.current = true;
    setSettingsOpen((cur) => cur || "account");
  }, [profileSync.cloudChoice]);
  // A link to a Settings pane (`/?settings=backups`, Gamma Cloud's Export
  // for a read-only Pro server) opens it once the library is there, signed
  // in first if need be; the parameter then leaves the address.
  useEffect(() => {
    if (!wsReady || shareMode) return;
    const url = new URL(window.location.href);
    const pane = url.searchParams.get("settings");
    if (!pane) return;
    url.searchParams.delete("settings");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    setSettingsOpen(pane);
  }, [wsReady, shareMode]);
  // What wants a look (a newer release, errors in the log — app/notices.js):
  // the dot on the account button and on the Settings panes that resolve it;
  // "Settings…" lands on the strongest one.
  const notices = useNotices(!!authUser?.user && !authUser.is_guest && !shareMode);
  // "Report a problem" (account menu, Settings → Diagnostics): support/ReportProblem.jsx.
  const [reportOpen, setReportOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  // The import review dialog: {source, file, strip, folder} for a file just
  // chosen, or {jobId} for an import job opened again from Background tasks.
  const [importReview, setImportReview] = useState(null);
  // Export dialog: one "Export…" menu entry, the shape of the export chosen
  // here. Remembered across sessions — most people export the same way twice.
  const [exportOpen, setExportOpen] = useState(false);
  // null = export the focused page; a folder id = export that whole folder
  // (set when the dialog is opened from home with a folder open, or from a
  // folder card's context menu).
  const [exportFolder, setExportFolder] = useState(null);
  // The export job the dialog follows once Export was pressed (or that
  // Background tasks opened it on); null while the format is chosen.
  const [exportJobId, setExportJobId] = useState(null);
  // Starting, reopening and retrying jobs, and what to say when one ends
  // (tasks/useAppJobs.js); `tasks` above calls its onJobFinished.
  const { startWorkspaceExport, runBackupImport, runExport, exportStarting, closeExport, leaveExportJob, openTask, retryTask, onJobFinished } = useAppJobs({
    tasks, postPill, setStatus, setOpenPopover, setSettingsOpen,
    exportOpen, setExportOpen, exportJobId, setExportJobId, exportFolder, setExportFolder, importReview, setImportReview,
    pageId: focusedBlock?.id, shareMode, exportRawPdf, exportTranslation, exportFolderTranslations,
    workspaceId: workspace?.id, refreshQuota, fetchHomeBlocks, refreshSession: checkSession,
  });
  const [exportOpts, setExportOpts] = usePersistedState(
    "gamma-export-opts",
    { format: "pdf", highlights: true, notes: true, bundle: true, comments: false, notesAt: "beside" },
    {
      parse: (raw) => ({ format: "pdf", highlights: true, notes: true, bundle: true, comments: false, notesAt: "beside",
        ...JSON.parse(raw) }),
      serialize: JSON.stringify,
    },
  );
  // Which provider entry (API key) AI requests use. Only the key is chosen
  // here — the model itself is picked in the chat panel, scoped to this key.
  const [aiProvider, setAiProvider] = usePersistedState("gamma-ai-provider", "");
  // The pick follows the account (/api/prefs/ai-provider): the server copy
  // wins on login, so it survives restarts and other browsers/origins;
  // localStorage is just the instant-paint cache. Without this, each origin
  // (localhost / LAN / Tailscale) silently reverted to the first key.
  const aiProviderSyncRef = useRef(null); // last server-synced value; null = not loaded yet
  useEffect(() => {
    aiProviderSyncRef.current = null;
    const u = authUser?.user;
    if (!u || shareMode) return;
    const local = aiProvider;
    // The pick is the account's, in every workspace: an answer is stale only
    // once the account changed (not prefsUserRef, which is "user@ws").
    let active = true;
    readPref("ai-provider", u).then((d) => {
      if (!active) return;
      if (d.updated_at) {
        const server = typeof d.value === "string" ? d.value : "";
        aiProviderSyncRef.current = server;
        setAiProvider(server);
      } else if (local) {
        // Account has never synced: seed the server with this browser's pick.
        aiProviderSyncRef.current = local;
        apiJson(`${API}/prefs/ai-provider`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: local }),
        }).catch(() => {});
      } else {
        aiProviderSyncRef.current = "";
      }
    }).catch(() => {});
    return () => { active = false; };
  }, [authUser?.user, shareMode]);
  useEffect(() => {
    if (aiProviderSyncRef.current === null || aiProviderSyncRef.current === aiProvider || shareMode) return;
    aiProviderSyncRef.current = aiProvider;
    apiJson(`${API}/prefs/ai-provider`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: aiProvider }),
    }).catch(() => {});
  }, [aiProvider, shareMode]);
  // Last model picked per key, so switching keys and back restores the pick
  // (a workspace-wide memory — the chat model is never per-PDF).
  const chatModelMemRef = useRef(null);
  if (chatModelMemRef.current === null) {
    try { chatModelMemRef.current = JSON.parse(localStorage.getItem("gamma-chat-model-by-key") || "{}") || {}; }
    catch { chatModelMemRef.current = {}; }
  }
  // Keep the selected model inside the active key's model list: switching to
  // a key restores its remembered model, or falls back to its first one.
  useEffect(() => {
    const scoped = providerModels(aiInfo?.models, aiProvider);
    if (!scoped.length) return;
    if (!scoped.some((m) => m.id === chatModel)) {
      const remembered = chatModelMemRef.current[scoped[0].provider];
      setChatModel(scoped.some((m) => m.id === remembered) ? remembered : scoped[0].id);
    }
  }, [aiProvider, aiInfo, chatModel]);
  // Models the active key (Settings → AI & API keys) offers — the first
  // key's when none is selected or the selected one is gone. Model registry ids
  // are "<entryId>:<model>", so the id ROUTES the request to a key server-side;
  // every model this client sends must come from this list or an unselected
  // key would serve the call.
  const scopedAiModels = providerModels(aiInfo?.models, aiProvider);
  // The model AI calls (chat, citations, titles) actually send: chatModel
  // snapped into scope at render time — the effect above fixes the state, but
  // a request fired in the same render (or before /ai/models loads after a
  // key switch elsewhere) must not trust it. Empty list = registry not loaded
  // yet; nothing to validate against, so the stored pick passes through.
  const chatSendModel = scopedAiModels.length && !scopedAiModels.some((m) => m.id === chatModel)
    ? scopedAiModels[0].id
    : chatModel;

  // The masked key list the chat's setup card read (ChatDock onSetupInfo):
  // the one Connections shows, so a tile's connect dialog opens on it with
  // Settings while the pane reads its own.
  const chatKeysInfoRef = useRef(null);
  async function loadAiKeys() {
    aiEditor.close();
    setAiKeyTests({});
    setAiKeyUsage({});
    const fromChat = pendingAiFormRef.current?.service ? chatKeysInfoRef.current : null;
    // What the pane showed last stays while it is read again, so a visit
    // opens on the list rather than "Loading…".
    setAiKeysInfo((prev) => fromChat || prev);
    if (fromChat) {
      openAiForm(pendingAiFormRef.current, fromChat);
      pendingAiFormRef.current = null;
    }
    try {
      const info = await apiJson(`${API}/ai/settings`);
      setAiKeysInfo(info);
      // Opened by the chat on a service or an entry (openAiKeysEditor).
      openAiForm(pendingAiFormRef.current, info);
      pendingAiFormRef.current = null;
      // The server's shared entries may have changed (Settings → Server).
      refreshAiModels();
      // Usage is account status, not an edit action: fetch it as soon as the
      // pane opens. Only OAuth protocols have a portable percentage endpoint;
      // generic API-key providers would merely return "unavailable".
      const oauthProtocols = new Set((info.protocols || [])
        .filter((protocol) => protocol.auth === "oauth")
        .map((protocol) => protocol.id));
      (info.providers || [])
        .filter((provider) => oauthProtocols.has(provider.protocol))
        .forEach((provider) => queryAiProviderUsage(provider));
    } catch (err) {
      aiEditor.setAiKeysError(err.message);
    }
  }

  // Probe one entry with a tiny real completion — the honest answer to "will
  // my chats work", surfacing an expired ChatGPT sign-in as a readable error
  // here instead of a 502 mid-conversation. With `toolsModel` (one of its
  // picked models) it checks whether that model calls tools instead (a
  // model tag in Settings, the chat's No tools chip), and the lists that
  // show the verdict are read again. Resolves to the result.
  async function testAiProvider(p, toolsModel = "") {
    setAiKeyTests((t) => ({ ...t, [p.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/ai/providers/${p.id}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: probeModelFor(p.id), ...(toolsModel ? { tools_model: toolsModel } : {}) }),
      });
    } catch (err) {
      result = { ok: false, error: err.message };
    }
    setAiKeyTests((t) => ({ ...t, [p.id]: result }));
    // A passing test clears this provider's login-check warning strip.
    if (result.ok) setAiHealth((h) => (h?.provider_id === p.id ? null : h));
    if (result.tools) {
      refreshAiModels();
      // Not loadAiKeys, which would clear the other rows' results.
      if (aiKeysInfo) apiJson(`${API}/ai/settings`).then(setAiKeysInfo).catch(() => {});
    }
    return result;
  }
  async function queryAiProviderUsage(p) {
    setAiKeyUsage((u) => ({ ...u, [p.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/ai/providers/${p.id}/usage`, { method: "POST" });
    } catch (err) {
      result = { available: false, reason: err.message };
    }
    setAiKeyUsage((u) => ({ ...u, [p.id]: result }));
  }
  // Connection check of the active provider (Settings → Provider and models →
  // "Check connection at login"): a broken credential surfaces as the chat
  // panel's warning strip at login instead of as a failed chat later. The
  // default "ping" mode spends no tokens; network failures reaching our own
  // server stay silent — every other request would be failing too.
  const aiHealthArgsRef = useRef({});
  aiHealthArgsRef.current = { mode: aiLoginCheck, provider: scopedAiModels[0]?.provider || aiProvider };
  async function checkAiHealth() {
    const { mode, provider } = aiHealthArgsRef.current;
    if (mode === "off") { setAiHealth(null); return; }
    try {
      const r = await apiJson(`${API}/ai/health`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider_id: provider || "", mode, model: probeModelFor(provider) }),
      });
      setAiHealth(r.configured && !r.ok ? r : null);
    } catch {}
  }
  useEffect(() => {
    if (!authUser?.user || authUser.is_guest || shareMode) return;
    // Delayed so the server-synced active-provider pick can land first.
    const timer = setTimeout(checkAiHealth, 1500);
    return () => clearTimeout(timer);
  }, [authUser?.user, shareMode]);
  // Entering the AI panes from elsewhere refetches the masked key list;
  // moving between them keeps it (their edits refresh it themselves). It
  // runs before the frame is painted, so a form left open on an earlier
  // visit never shows again.
  const prevSettingsPaneRef = useRef(null);
  useLayoutEffect(() => {
    const cameFrom = prevSettingsPaneRef.current;
    prevSettingsPaneRef.current = settingsOpen;
    if (isAiPane(settingsOpen) && !isAiPane(cameFrom) && authUser?.user && !shareMode) loadAiKeys();
  }, [settingsOpen]);

  // Settings → Connections. `service` (a protocol id or "other", from the
  // chat's setup card) also opens the connect dialog set to that service;
  // `entry` (a chat error card's "Update key" / "Edit connection", the login
  // check's Fix…) opens that entry's form. A shared entry is the admin's to
  // edit (Settings → Server), so it gets the pane alone. The form opens at
  // once when the pane has the key list, else when loadAiKeys lands.
  const pendingAiFormRef = useRef(null); // {service} | {entry} | null
  function openAiKeysEditor({ service = "", entry = "" } = {}) {
    const target = service ? { service } : entry ? { entry } : null;
    if (isAiPane(settingsOpen) && aiKeysInfo) openAiForm(target, aiKeysInfo);
    else pendingAiFormRef.current = target;
    openSettingsPane("ai");
  }
  function openAiForm(target, info) {
    if (target?.service) aiEditor.startAdd(target.service, { list: info, fromChat: true });
    const own = target?.entry && info.providers?.find((p) => p.id === target.entry && !p.shared);
    if (own) aiEditor.startEdit(own);
  }

  // The model switchers everywhere feed off /ai/models — refresh after edits.
  // A read that fails leaves the last list and says so (`aiInfoFailed`): the
  // chat, waiting to learn its AI before it shows its tools, stops waiting.
  const [aiInfoFailed, setAiInfoFailed] = useState(false);
  const refreshAiModels = () => apiJson(`${API}/ai/models`)
    .then((info) => { setAiInfo(info); setAiInfoFailed(false); })
    .catch(() => setAiInfoFailed(true));

  // A connection the dialog just made: test it right away, and when the
  // dialog came from the chat's setup card go back to the chat, ready to ask.
  const [chatFocusSignal, setChatFocusSignal] = useState(0);
  function finishNewConnection(entry, fromChat) {
    testAiProvider(entry);
    if (!fromChat) return;
    const model = modelList(entry.models)[0];
    aiEditor.close();
    setSettingsOpen(null);
    setStatus(model ? t("Connected — {model} ready", { model }) : t("Connected"));
    setChatHidden(false);
    setChatFocusSignal((n) => n + 1);
  }

  function deleteAiProvider(p) {
    const label = p.label || p.protocol;
    setConfirmBox({
      title: T("Remove AI key"),
      message: t("Remove the \"{label}\" key? AI requests through it will stop working. This cannot be undone.", { label: label }),
      confirmLabel: t("Remove"),
      danger: true,
      onConfirm: async () => {
        // Close a form that edits this entry — saving it would 404 ("provider
        // not found") — and forget it as the active key.
        await aiEditor.run(() => apiJson(`${API}/ai/providers/${p.id}`, { method: "DELETE" }), aiEditor.aiKeysForm?.id === p.id);
        if (aiProvider === p.id) setAiProvider("");
      },
    });
  }

  // User management moved into Settings → Users (settings/SettingsDialog.jsx UsersSettings,
  // admins only) — App just opens that pane and lends it the shared pieces
  // (confirm dialog, status pill, session re-key after a self-rename).
  // PDF passages the next chat question focuses on, as {text, page, box}
  // (pdf/pdfSelectionSpot.js — where it sits, so the server can place it
  // and picture a formula). Ctrl (additive) appends — whether from text
  // selection or highlight clicks; plain replaces. A passage is kept whole:
  // how much of it the message quotes is the chat's budget to decide, and
  // its chip says so (chat/passageBudget.js). One past the most a message
  // takes is refused with a word, never dropped quietly.
  const [pdfSelections, setPdfSelections] = useState([]);
  // The viewer's mouseup listener, set up once, reads the set through this.
  const pdfSelectionsRef = useRef(pdfSelections);
  useEffect(() => { pdfSelectionsRef.current = pdfSelections; }, [pdfSelections]);
  function addPdfSelection(text, additive, spot) {
    const part = (text || "").trim();
    if (!part) return;
    const item = { text: part, page: spot?.page || 0, box: spot?.box || null };
    if (!additive) {
      setPdfSelections([item]);
      return;
    }
    const held = pdfSelectionsRef.current;
    if (!held.some((s) => s.text === part) && held.length >= MAX_CHAT_PASSAGES) {
      setStatus(t("A chat message takes at most {n} PDF passages — remove one to add another.", { n: MAX_CHAT_PASSAGES }));
      return;
    }
    setPdfSelections((prev) => (prev.some((s) => s.text === part) || prev.length >= MAX_CHAT_PASSAGES ? prev : [...prev, item]));
  }
  // Note chips for the next chat message — blocks attached with Ctrl+click /
  // the ⋮⋮ menu's "Add to chat" ({kind: "block", id, text}; the server serves
  // their live text with ids, so the agent can edit them) and rendered note
  // text selected with Ctrl held, as the exact source range it covers
  // ({kind: "note", id, from, to, text} — edit_block mode "selection"
  // rewrites just that). Cleared on send, like pdfSelections; a page switch
  // drops them (their ids belong to the page).
  const [chatNotes, setChatNotes] = useState([]);
  const chatNotesRef = useRef(chatNotes);
  useEffect(() => { chatNotesRef.current = chatNotes; }, [chatNotes]);
  // A block chip's text is only its preview: the server serves the block
  // itself, with its sub-blocks, within the message's budget.
  const blockChip = (block) => ({ kind: "block", id: block.id, text: blockChipText(block).slice(0, 4000) });
  const attachedBlocks = () => chatNotesRef.current.filter((n) => n.kind === "block");
  function addBlockToChat(block) {
    if (!block?.id || block.id === "root") return;
    const attached = attachedBlocks();
    if (!attached.some((n) => n.id === block.id) && attached.length >= MAX_CHAT_PASSAGES) {
      setStatus(t("A chat message takes at most {n} attached blocks — detach one to add another.", { n: MAX_CHAT_PASSAGES }));
      return;
    }
    setChatNotes((prev) => (prev.some((n) => n.kind === "block" && n.id === block.id)
      || prev.filter((n) => n.kind === "block").length >= MAX_CHAT_PASSAGES ? prev : [...prev, blockChip(block)]));
    setStatus(t("Block attached to your next chat message."));
  }
  // The request ChatDock sends at once (its askSignal): Transcribe's, below.
  const [chatAsk, setChatAsk] = useState(null);
  // Transcribe, its chat half: a handwriting block's "Transcribe with AI"
  // ({blocks: [block]}) and a lasso selection's Transcribe
  // (ink/useInkSelection.js transcribeInkSelection, whole groups saved).
  // Blocks go to the chat as chips (a group's picture rides with the
  // message, ai_context) with one request: the blocks the request is about
  // always go, the oldest attached block making room and the pill saying
  // so. The agent writes the text into a caption (view_ink, edit_block), or
  // answers with it where it may not edit. Else the selected strokes go as
  // ink chips, each taking the place of an earlier chip of its group,
  // within the server's caps (past them nothing is sent).
  function askToTranscribe({ blocks, chips }) {
    if (blocks) {
      let next = chatNotesRef.current, dropped = false;
      for (const block of blocks) {
        if (next.some((n) => n.kind === "block" && n.id === block.id)) continue;
        const attached = next.filter((n) => n.kind === "block");
        const oldest = attached.length >= MAX_CHAT_PASSAGES ? attached[0] : null;
        if (oldest) dropped = true;
        next = [...next.filter((n) => n !== oldest), blockChip(block)];
      }
      setChatNotes(next);
      if (dropped) setStatus(t("The oldest attached block was detached to make room."));
      showChat();
      setChatAsk({ id: Date.now(), text: t("Transcribe this handwriting into its caption.") });
      return;
    }
    const next = [...chatNotesRef.current.filter((n) => n.kind !== "ink" || !chips.some((c) => c.id === n.id)), ...chips];
    const sent = next.filter((n) => n.kind === "ink").map((n) => ({ page_id: n.page_id, block_id: n.id, space: n.space, strokes: n.strokes }));
    if (sent.length > MAX_INK_SELECTIONS || new TextEncoder().encode(JSON.stringify(sent)).length > MAX_INK_SELECTION_BYTES) {
      setStatus(t("Too much handwriting selected to transcribe; select less."));
      return;
    }
    setChatNotes(next);
    showChat();
    setChatAsk({ id: Date.now(), text: t("Transcribe the selected handwriting.") });
  }
  // A Ctrl-selection inside one block's rendered view → its source range;
  // one that can't be pinned down (it spans blocks, or an end isn't the
  // note's own text) attaches the block it started in instead.
  function addNoteSelection(range, rendered) {
    const rowId = rendered.closest("[data-block-id]")?.getAttribute("data-block-id");
    const block = rowId && findBlock(blocksRef.current, rowId);
    if (!block) return;
    const src = block.content || "";
    const inOne = rendered.contains(range.startContainer) && rendered.contains(range.endContainer);
    const at = inOne && sourceRangeOfSelection(rendered, src, range, scanMathSpans(src));
    const text = at ? src.slice(at.from, at.to) : "";
    if (!text.trim()) {
      addBlockToChat(block);
      return;
    }
    const note = { kind: "note", id: block.id, from: at.from, to: at.to, text };
    const same = (n) => n.kind === "note" && n.id === note.id && n.from === note.from && n.to === note.to;
    const notes = (list) => list.filter((n) => n.kind === "note").length;
    if (!chatNotesRef.current.some(same) && notes(chatNotesRef.current) >= MAX_CHAT_PASSAGES) {
      setStatus(t("A chat message takes at most {n} note selections — remove one to add another.", { n: MAX_CHAT_PASSAGES }));
      return;
    }
    setChatNotes((prev) => (prev.some(same) || notes(prev) >= MAX_CHAT_PASSAGES ? prev : [...prev, note]));
  }
  useEffect(() => { setChatNotes([]); }, [focusedBlockId]);
  // The open editor's selection, for the chat's Cursor chip, which becomes
  // a "Selection" chip riding with the message as an exact source range.
  // Kept after the editor closes (clicking into the chat closes it) until
  // the caret collapses somewhere, the page changes or the message is sent;
  // settled 120 ms after the last change so a drag doesn't re-render App on
  // every move.
  const [noteSel, setNoteSel] = useState(null); // {id, from, to}
  const noteSelTimerRef = useRef(null);
  function trackNoteSel(id, from, to) {
    clearTimeout(noteSelTimerRef.current);
    noteSelTimerRef.current = setTimeout(() => setNoteSel((prev) => (from === to
      ? null
      : prev?.id === id && prev.from === from && prev.to === to ? prev : { id, from, to })), 120);
  }
  useEffect(() => { clearTimeout(noteSelTimerRef.current); setNoteSel(null); }, [focusedBlockId]);
  // Pictures pending send in the chat (chat/chatPictures.js): the ones the
  // dock stored (pasted, picked) and regions of PDF pages the server draws
  // — a Ctrl+drag area selection, a rectangle highlight's click, "Attach
  // what I see". Lives here (not in ChatDock) so the viewer can attach even
  // while the chat window is closed.
  const [chatImages, setChatImages] = useState([]);
  // The pictures that came from the PDF (by URL), as opposed to pictures
  // pasted into the chat input. The auto-clear preference below only ever
  // drops these — a pasted picture must survive PDF clicks.
  const pdfImagesRef = useRef(new Set());
  function addChatPicture(picture) {
    if (!picture) return;
    if (isPdfPicture(picture)) {
      const seen = pdfImagesRef.current;
      seen.add(pictureUrl(picture));
      while (seen.size > 16) seen.delete(seen.values().next().value);
    }
    setChatImages((prev) => addPictures(prev, [picture], chatPictures));
  }
  // "Attach what I see": the visible part of each PDF page in view (at most
  // two), with the handwriting on it, as regions the server renders.
  function attachViewToChat() {
    const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
    if (!scroller || !focusedBlockId) return;
    const vr = scroller.getBoundingClientRect();
    const added = [];
    for (const wrap of scroller.querySelectorAll(".pdfPageWrap[data-page]")) {
      const r = wrap.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const x0 = Math.max(vr.left, r.left), x1 = Math.min(vr.right, r.right);
      const y0 = Math.max(vr.top, r.top), y1 = Math.min(vr.bottom, r.bottom);
      if (x1 - x0 < 40 || y1 - y0 < 40) continue;
      const box = [(x0 - r.left) / r.width, (y0 - r.top) / r.height, (x1 - r.left) / r.width, (y1 - r.top) / r.height].map(round4);
      const whole = box[0] <= 0.001 && box[1] <= 0.001 && box[2] >= 0.999 && box[3] >= 0.999;
      added.push(regionPicture("view", focusedBlockId, Number(wrap.dataset.page), whole ? null : box, { ink: true }));
      if (added.length >= 2) break;
    }
    added.forEach(addChatPicture);
  }
  // Ref-mirror of the snapshot auto-clear preference for the mouseup listener.
  const chatImgAutoClearRef = useRef(chatImgAutoClear);
  useEffect(() => { chatImgAutoClearRef.current = chatImgAutoClear; }, [chatImgAutoClear]);
  // Clicking a highlight — on the PDF or its card in the notes — feeds the
  // chat: text highlights set their quote as the selection, area rectangles
  // re-crop their region into an image attachment (the snapshot is never
  // stored; the viewer renders it fresh from the document each time).
  function addHighlightToChat(h, additive) {
    if (!h) return;
    if (h.position?.area) {
      // Its region, as the server renders it from the document (a picture
      // is its one page).
      addChatPicture(regionPicture("area", focusedBlockId, h.position.pageNumber || 1, regionBox(h.position)));
    } else {
      addPdfSelection(h.quote, additive, highlightSpot(h.position));
    }
  }
  // Styled in-app dialogs replacing window.confirm / link decisions.
  const [confirmBox, setConfirmBox] = useState(null); // {title, message, confirmLabel, danger, onConfirm, altLabel, altDanger, onAlt, onCancel}
  // Cancel, Escape and the backdrop all close it the same way, onCancel told.
  const cancelConfirm = () => { const fn = confirmBox?.onCancel; setConfirmBox(null); fn?.(); };
  // What the Move to page picker (library/PagePicker.jsx) moves: {blockId},
  // an object's {fragment}, or {pageIds} (Move into page).
  const [movePicker, setMovePicker] = useState(null);
  const [linkPrompt, setLinkPrompt] = useState(null); // external URL clicked inside the PDF
  const [linkDialog, setLinkDialog] = useState(null); // {position, quote, editBlockId?, url?} — making or changing a manual reference link (pdf/LinkDialog.jsx)
  // A highlight copied via "Copy as reference point" — link dialogs in other
  // papers offer it as a target, so links can point at an exact passage.
  const [refPoint, setRefPoint] = useState(null); // {pageId, pageTitle, blockId, quote}

  // --- Paper metadata (arXiv / DOI / AI) and citation export -----------------
  const [pageMeta, setPageMeta] = useState(null);   // properties.meta of the open page
  const [pageBibtex, setPageBibtex] = useState("");
  // What the share popover's Copy BibTeX gives: the entry the .bib export
  // prints (GET /api/bibliography, bibtex.page_entry), valid BibTeX however
  // the stored entry (pageBibtex) was written. Asked for when the popover
  // opens and again after an edit changes the stored entry.
  const [printedBibtex, setPrintedBibtex] = useState("");
  const sharedPageId = openPopover === "share" && shareTarget?.kind === "page" && pageMeta ? shareTarget.id : "";
  useEffect(() => {
    setPrintedBibtex("");
    if (!sharedPageId) return undefined;
    let live = true;
    bibliographyPreview({ pageId: sharedPageId })
      .then((data) => { if (live) setPrintedBibtex(data?.entries?.[0]?.text || ""); })
      .catch(() => {});  // the stored entry stays on show
    return () => { live = false; };
  }, [sharedPageId, pageBibtex]);
  // Block ids with a metadata fetch in flight. Tracked per page (not one
  // boolean) so the button doesn't spin for a fetch that belongs to a
  // different page. A page this tab handed to the metadata job (an upload)
  // spins too, until its record or its failure arrives over the page's
  // socket or the job ends.
  const [metaFetchingIds, setMetaFetchingIds] = useState(() => new Set());
  const metaQueuedRef = useRef(new Set());
  useEffect(() => { if (!metadataJob) metaQueuedRef.current.clear(); }, [metadataJob]);
  const metaBusy = metaFetchingIds.has(focusedBlockId)
    || (!!metadataJob && metaQueuedRef.current.has(focusedBlockId)
      && !focusedBlock?.properties?.meta && !focusedBlock?.properties?.meta_error);
  // Provenance wording shared with the share popover and the Settings →
  // Library status table (utils.js); `warn` drives the red "!".
  const metaSrc = metaSourceInfo(pageMeta);
  const [pptCite, setPptCite] = useState("");
  const [pptCiteBusy, setPptCiteBusy] = useState(false);
  // Pages whose slide citation this session already asked for (one try per
  // page — a failing AI call must not loop on every state change).
  const attemptedCiteRef = useRef(new Set());
  const [metaPopPos, setMetaPopPos] = useState({ top: 0, right: 0 }); // fixed-position anchor for the metadata popover
  const [copiedKey, flashCopied, resetCopied] = useCopied(); // "bibtex" | "ppt" | "source"
  // Editable copy of the metadata fields shown in the popover. Kept as flat
  // strings (authors comma-joined); rebuilt whenever the popover opens or a
  // fetch lands, so a refresh replaces any half-typed edits with the result.
  const [metaDraft, setMetaDraft] = useState(null);
  // The citation key pinned on the page (properties.cite_key), edited beside
  // the record but stored apart from it: a pin outranks the generated key and
  // survives a refetch, because the user's .tex files cite it.
  const pageCiteKey = focusedBlock?.properties?.cite_key || "";
  const [citeKeyDraft, setCiteKeyDraft] = useState("");
  useEffect(() => {
    if (openPopover !== "meta") return;
    setMetaDraft(metadataToDraft(pageMeta));
    setCiteKeyDraft(pageCiteKey);
  }, [openPopover, pageMeta, pageCiteKey]);
  // Unsaved edits in the popover — gates the Save button and Enter-to-save.
  const metaDirty = metaDraft && (JSON.stringify(metaDraft) !== JSON.stringify(metadataToDraft(pageMeta))
    || citeKeyDraft.trim() !== pageCiteKey);

  // PDF-text health shown in the metadata popover: a scanned/image-only PDF is
  // why metadata lookups fail and AI chat answers blind — surface it. One
  // fetch serves both the popover-open effect and the ↻ recheck button.
  const [pdfTextInfo, setPdfTextInfo] = useState(null); // null | {checking} | {error} | {found, ok, chars}
  async function checkPdfText(retryMeta = false) {
    setPdfTextInfo({ checking: true });
    try {
      const d = await apiJson(`${API}/pdf-text-status?doc_id=${encodeURIComponent(docId)}`);
      setPdfTextInfo(d);
      // Text became available (e.g. the source file was replaced) and there's
      // still no metadata — retry the lookup right away.
      if (retryMeta && d.ok && !pageMeta && focusedBlock) fetchMetadata(focusedBlock, true);
    } catch (err) {
      setPdfTextInfo({ error: friendlyApiError(err) });
      if (retryMeta) setStatus(t("Text check failed: {message}", { message: err.message }));
    }
  }
  useEffect(() => {
    if (openPopover !== "meta" || !docId || shareMode) { setPdfTextInfo(null); return; }
    checkPdfText();
  }, [openPopover, docId]);

  // Modal preview of what the AI actually gets to read.
  const [pdfTextPreview, setPdfTextPreview] = useState(null); // null | {loading} | {text}
  async function openPdfTextPreview() {
    if (!docId) return;
    setPdfTextPreview({ loading: true });
    try {
      const d = await apiJson(`${API}/pdf-text-status?doc_id=${encodeURIComponent(docId)}&preview=12000`);
      setPdfTextPreview({ text: d.text || t("(no text)") });
    } catch (err) {
      setPdfTextPreview({ text: t("Preview failed: {err}", { err: friendlyApiError(err) }) });
    }
  }

  // A DOI or arXiv id typed in place of the stored one is looked up by the
  // save (the registry's record replaces the fields), which takes seconds.
  const [metaSaving, setMetaSaving] = useState(false);
  async function saveMetaEdits() {
    const blockId = focusedBlockIdRef.current;
    if (!blockId || !metaDraft || metaSaving) return;
    setMetaSaving(true);
    try {
      const data = await apiJson(`${API}/metadata/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id: blockId, meta: metaDraft, cite_key: citeKeyDraft.trim() }),
      });
      if (focusedBlockIdRef.current !== blockId) return;
      setPageMeta(data.meta || null);
      setPageBibtex(data.bibtex || "");
      // Edited metadata invalidates the cached slide citation; the citation
      // effect regenerates it from the new record right away.
      setPptCite("");
      attemptedCiteRef.current.delete(blockId);
      setFocusedBlock((prev) => prev && prev.id === blockId
        ? { ...prev, properties: { ...prev.properties, meta: data.meta, bibtex: data.bibtex,
            cite_key: data.cite_key || undefined, ppt_cite: undefined, meta_error: undefined } }
        : prev);
      if (data.meta?.title && focusedBlock?.properties?.auto_title === focusedBlock?.content) {
        await renameTitle(data.meta.title);
      }
      fetchHomeBlocks({ still: true }); // keep the library's meta fresh for DOI-link matching
      const site = data.lookup?.registry === "arxiv" ? "arXiv" : "doi.org";
      setStatus(!data.lookup ? t("Metadata saved.")
        : data.lookup.found ? t("Metadata filled in from {site} ({id}).", { site, id: data.lookup.id })
          : t("{site} has no record of {id}; saved as typed.", { site, id: data.lookup.id }));
    } catch (err) {
      setStatus(t("Metadata save failed: {message}", { message: err.message }));
    } finally {
      setMetaSaving(false);
    }
  }
  // The Source row's Verify: the user vouches for the record as it stands,
  // which clears the red "!" everywhere. Unsaved edits are saved instead — a
  // hand edit already counts as vouched for.
  async function verifyMeta() {
    if (metaDirty) { saveMetaEdits(); return; }
    const blockId = focusedBlockIdRef.current;
    if (!blockId) return;
    try {
      const data = await apiJson(`${API}/metadata/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id: blockId }),
      });
      if (focusedBlockIdRef.current !== blockId) return;
      setPageMeta(data.meta);
      setFocusedBlock((prev) => prev && prev.id === blockId
        ? { ...prev, properties: { ...prev.properties, meta: data.meta } }
        : prev);
      fetchHomeBlocks({ still: true });
      setStatus(t("Metadata marked as verified."));
    } catch (err) {
      setStatus(t("Verify failed: {message}", { message: err.message }));
    }
  }
  // Draft copies of the editable prompts (empty = server default), rebuilt
  // from the saved values whenever the Prompts/Assistant pane opens.
  const [metaPromptDraft, setMetaPromptDraft] = useState("");
  const [citePromptDraft, setCitePromptDraft] = useState("");

  const focusedBlockIdRef = useRef("");
  useEffect(() => { focusedBlockIdRef.current = focusedBlockId || ""; }, [focusedBlockId]);
  const attemptedMetaRef = useRef(new Set()); // pages we already tried this session

  // Model actually sent with metadata lookups (per-paper fetch AND the
  // Settings batch retry): the dedicated pick while it's inside the active
  // key's scope, else the (already-scoped) chat model.
  const metaFetchModel = metaModel && scopedAiModels.some((m) => m.id === metaModel)
    ? metaModel
    : chatSendModel;

  // Model name the credential probes (Test button / login check "test" mode)
  // prefer: the effective metadata model when it belongs to the probed entry —
  // metadata fetches already trust it as the cheap utility model. The entry's
  // own "Test model" setting still wins server-side; registry ids are
  // "<entryId>:<model>", so a model from another entry is not sent.
  const probeModelFor = (providerId) => {
    const rid = metaModel || chatSendModel || "";
    return providerId && rid.startsWith(`${providerId}:`) ? rid.slice(providerId.length + 1) : "";
  };

  // --- PDF translation (the 文A button in the viewer's zoom column) ---
  // The viewer starts the jobs (translateCtl), which send translateChunk
  // below for each chunk and translateBrief for the paper's brief; the
  // selection popup uses the same chunk request. app/translateClient.js
  // follows the jobs of every document: their background-tasks rows, the
  // reading pill, how they ended. The settings live in Settings → Language
  // and Translation. What is sent: a machine-translation service
  // ("engine:<id>") or a model, per translateModelFor, else the chat model.
  const translateEngines = aiInfo?.translate_engines || [];
  const translatePick = translateModelFor(translateModel, translateEngines, scopedAiModels);
  const translateSendModel = translatePick || chatSendModel;
  const translateLangLabel = (TRANSLATE_LANGS.find(([code]) => code === translateLang) || ["", ""])[1];
  // The key the settings hold: the language and what translates into it.
  // What the viewer keeps the page's PDF under is paperKey, below.
  const translateKey = `${translateLang}|${translateSendModel}`;
  const pdfTranslateCtl = useRef(null); // imperative surface set by PdfViewer
  const [pdfTransState, setPdfTransState] = useState({ running: false, progress: 0, shown: true, pages: 0, current: false, translated: false, refsLeft: 0, url: "" });
  const [transMenu, setTransMenu] = useState(null); // {x, y} while the button's option menu is open
  // A translated paragraph's menu while it is open: what the viewer says of
  // the paragraph (PdfViewer onParagraphContext), indices, never nodes.
  const [paraMenu, setParaMenu] = useState(null);
  const [toolsMenu, setToolsMenu] = useState(null); // {x, y} while the strip's ⋯ menu is open
  const toolsMoreRef = useRef(null); // the ⋯ button: its own press toggles the menu
  // The ⋯ going (its tools back on the strip, the viewer closed) takes its
  // menu along, so the menu does not come back on its own with the ⋯.
  const toolsMoreAt = useCallback((el) => { toolsMoreRef.current = el; if (!el) setToolsMenu(null); }, []);
  // A menu from a button of the viewer's strip opens beside it, off the
  // strip's right edge.
  function stripMenuAt(el) {
    const r = el.getBoundingClientRect();
    return { x: r.right + 8, y: r.top - 4 };
  }
  function openTransMenu(el) { setTransMenu(stripMenuAt(el)); }
  // The papers this browser last left showing their translation, per account
  // and workspace (pdf/translateRecall.js): the viewer seeds those, and only
  // those, from their kept translations as they open. `recallRead` moves on
  // when a Forget lets one go, so paperKey reads the list again.
  const translateRecall = useMemo(() => (authUser?.user && wsId && !shareMode
    ? createRecall({ getItem: (k) => localStorage.getItem(k), setItem: (k, v) => localStorage.setItem(k, v) },
      `gamma-translated:${authUser.user}@${wsId}`)
    : null), [authUser?.user, wsId, shareMode]);
  const [recallRead, setRecallRead] = useState(0);
  // No chat model on offer and no service picked in Translate with (the free
  // service, or nothing, stands in for a chat model): a paper left translated
  // by a chat model then opens under that model's key (paperKey).
  const translateNoChatModel = !scopedAiModels.length && !translateEngines.some((e) => e.id === translateModel);
  useTranslateJobs({ screenUrl: pdfUrl, title: () => pageTitle, langLabel: translateLangLabel, postPill, addTask, updateTask, setStatus,
    retry: () => pdfTranslateCtl.current?.retryUntranslated(),
    rerun: () => pdfTranslateCtl.current?.rerun(),
    translateReferences: () => pdfTranslateCtl.current?.translateReferences(),
    followsChat: !translatePick, openSettings: () => openSettingsPane("translation"),
    // A page read through the link proxy has a copy for the export to read
    // only with Save external PDFs on; an upload always has its file.
    exportsTranslation: () => !!exportTranslated() && (!!docIdOf(pdfUrl) || pdfSaveLocal),
    openExport: () => exportAs("pdf"),
    statusFree: () => !pillChannels.status?.error && !pillChannels.status?.action });

  // POST /metadata/fetch for one page, tracked as a transfer-panel task:
  // the open page's lookup (an upload's are the metadata job's). Throws on
  // failure (with the task already marked).
  async function fetchMetadataRequest(block, force = false) {
    const ctl = new AbortController();
    const taskId = addTask({ name: `Metadata — ${(block.content || "paper").slice(0, 48)}`, kind: "ai", info: t("fetching…"), cancel: () => ctl.abort() });
    setMetaFetchingIds((prev) => new Set(prev).add(block.id));
    try {
      const data = await apiJson(`${API}/metadata/fetch`, {
        method: "POST", signal: ctl.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          block_id: block.id,
          prompt: metaPrompt || "",
          model: metaFetchModel || "",
          force: !!force,
          context_char_limit: metaContextChars,
          // the slide citation is generated in the same call
          cite_prompt: citePrompt || "",
          cite_model: chatSendModel || "",
        }),
      });
      updateTask(taskId, { state: "done", info: data.cached ? "cached" : data.source === "ai" ? t("AI-extracted") : data.source || "" });
      return data;
    } catch (err) {
      updateTask(taskId, { state: "failed", info: (err.message || "failed") });
      throw err;
    } finally {
      setMetaFetchingIds((prev) => { const next = new Set(prev); next.delete(block.id); return next; });
    }
  }

  // The page as the server left it after a metadata lookup: the record plus
  // the page's CURRENT title. The title is the server's word, not "renamed by
  // this call" — the extension's background lookup races the one the app
  // starts when the page opens, and whichever loses still has to show the
  // winner's rename. The automatic-title marker is gone once the title
  // moved off it.
  function mergeMetaResult(block, data) {
    const content = data.page_title || block.content;
    const props = { ...block.properties, meta: data.meta, bibtex: data.bibtex,
      ppt_cite: data.ppt_cite || undefined, meta_error: undefined };
    if (props.auto_title && props.auto_title !== content) props.auto_title = undefined;
    return { ...block, content, properties: props };
  }

  async function fetchMetadata(block, force) {
    if (!block?.id) return;
    if (force) setStatus(t("Refreshing paper metadata…"));
    try {
      const data = await fetchMetadataRequest(block, force);
      if (focusedBlockIdRef.current !== block.id) return;
      setPageMeta(data.meta || null);
      setPageBibtex(data.bibtex || "");
      // The slide citation arrives with the metadata; when it didn't (AI
      // off, or that one call failed) the citation effect gets one retry.
      setPptCite(data.ppt_cite || "");
      if (!data.ppt_cite) attemptedCiteRef.current.delete(block.id);
      setFocusedBlock((prev) => prev && prev.id === block.id ? mergeMetaResult(prev, data) : prev);
      // The title follows the server (a filename title replaced by the
      // paper's) — set before the library refetch below so a stale name can't
      // resurface in the link dialog / home list.
      if (data.page_title && focusedBlockIdRef.current === block.id) setPageTitle(data.page_title);
      if (!data.cached) {
        setStatus(t("Paper metadata found ({source}).", { source: data.source === "ai" ? t("AI-extracted") : data.source }));
        fetchHomeBlocks({ still: true }); // keep the library's meta fresh for DOI-link matching
      }
    } catch (err) {
      if (focusedBlockIdRef.current === block.id) setStatus(t("Metadata: {message}", { message: err.message }));
      // A lookup that found nothing leaves the server's negative-cache
      // marker on the page row (meta_error): copied from the row, so the
      // metadata popover's hint shows it and this copy stays the server's.
      // Another failure (the network, the server) stores no marker.
      if (err.status !== 404) return;
      apiJson(`${API}/blocks/${encodeURIComponent(block.id)}`).then((row) => {
        const marker = row?.properties?.meta_error;
        setFocusedBlock((prev) => {
          if (!prev || prev.id !== block.id) return prev;
          const props = { ...prev.properties };
          if (marker) props.meta_error = marker;
          else delete props.meta_error;
          return { ...prev, properties: props };
        });
      }, () => {});
    }
  }

  // The uploaded PDFs' lookups are the server's metadata job (one per
  // workspace: a later upload adds to it), so they go on when the tab
  // closes. The open page hears its record over its socket; the server may
  // replace the original-filename title only while the automatic title
  // marker still matches, so a lookup cannot undo a rename. A job that
  // cannot start leaves the papers to the lookup on open.
  function queueMetadataForUploads(uploaded) {
    if (shareMode || readOnly || !metaAutoFetch) return;
    const ids = uploaded.map(({ block }) => block).filter((block) => block?.id
      && !block.properties?.meta && !block.properties?.meta_error
      && !attemptedMetaRef.current.has(block.id)).map((block) => block.id);
    if (!ids.length) return;
    ids.forEach((id) => { attemptedMetaRef.current.add(id); metaQueuedRef.current.add(id); });
    tasks.start("metadata", {
      block_ids: ids, force: false,
      prompt: metaPrompt || "", model: metaFetchModel || "", context_char_limit: metaContextChars,
      cite_prompt: citePrompt || "", cite_model: chatSendModel || "",
    }).catch(() => ids.forEach((id) => { attemptedMetaRef.current.delete(id); metaQueuedRef.current.delete(id); }));
  }

  // Metadata is a page property: show it on any page that has it; a library
  // PDF (documents/library.js: not a work folder's file outside the library)
  // additionally fetches it in the background (arXiv → DOI → AI on the
  // server) and caches it in the page's properties.
  useEffect(() => {
    setPptCite("");
    resetCopied();
    const b = focusedBlock;
    if (shareMode || !b?.id || (!b.properties?.meta && !isLibraryPdf(b))) { setPageMeta(null); setPageBibtex(""); return; }
    if (b.properties.meta) {
      setPageMeta(b.properties.meta);
      setPageBibtex(b.properties.bibtex || "");
      setPptCite(b.properties.ppt_cite || "");
      return;
    }
    setPageMeta(null);
    setPageBibtex("");
    if (!metaAutoFetch) return; // manual via ↻ only
    if (b.properties.meta_error) return; // a past lookup failed — retry only via ↻
    if (attemptedMetaRef.current.has(b.id)) return;
    attemptedMetaRef.current.add(b.id);
    fetchMetadata(b, false);
  }, [focusedBlock?.id]);

  // Import annotations embedded in the PDF file itself (SumatraPDF, Acrobat…).
  // Idempotent server-side, so calling it on every upload is safe.
  // strip: rewrite the stored PDF without the annotations being imported.
  // Defaults to the Settings preference; the import dialog can override it for
  // one run (auto-import on open always follows the preference).
  async function importEmbeddedAnnots(blockId, targetDocId, silent, strip = embAnnots === "strip") {
    const taskId = addTask({ name: "Importing embedded PDF annotations", kind: "file", info: t("scanning…") });
    try {
      const res = await apiJson(`${API}/import/pdf-annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id: blockId, doc_id: targetDocId, strip }),
      });
      updateTask(taskId, {
        state: "done",
        info: res.imported > 0 ? `${res.imported} imported` : res.found > 0 ? t("already imported") : t("none found"),
      });
      if (res.imported > 0) {
        if (focusedBlockIdRef.current === blockId) await loadBlocksForBlock(blockId);
        setStatus(t("Imported {imported} annotation{_s} embedded in the PDF.", { imported: res.imported, _s: res.imported === 1 ? "" : "s" }));
      }
      if (res.stripped > 0 && focusedBlockIdRef.current === blockId) {
        // The stored file changed — cache-bust so the open viewer re-renders
        // the page without the now-stripped annotations baked in.
        setPdfUrl((u) => (u ? u + (u.includes("?") ? "&" : "?") + "annots=" + Date.now() : u));
      }
      if (res.imported === 0 && !silent) {
        setStatus(res.found > 0
          ? t("All embedded annotations were already imported.")
          : t("No annotations embedded in this PDF."));
      }
      return res;
    } catch (err) {
      updateTask(taskId, { state: "failed", info: (err.message || "failed") });
      if (!silent) setStatus(t("Annotation import failed: {message}", { message: err.message }));
    }
  }

  async function makePptCitation(force = false, blockArg = null) {
    const targetId = blockArg?.id || focusedBlockId;
    if (!targetId || pptCiteBusy) return;
    setPptCiteBusy(true);
    const ctl = new AbortController();
    const taskId = addTask({ name: `Slide citation — ${(blockArg?.content || pageTitle || "paper").slice(0, 48)}`, kind: "ai", info: t("generating…"), cancel: () => ctl.abort() });
    try {
      const data = await apiJson(`${API}/metadata/cite`, {
        method: "POST", signal: ctl.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id: targetId, prompt: citePrompt || "", model: chatSendModel || "", force }),
      });
      updateTask(taskId, { state: "done", info: "" });
      if (focusedBlockIdRef.current === targetId) setPptCite(data.citation || "");
    } catch (err) {
      updateTask(taskId, { state: "failed", info: (err.message || "failed") });
      if (!ctl.signal.aborted) setStatus(t("Citation failed: {message}", { message: err.message }));
    } finally {
      setPptCiteBusy(false);
    }
  }

  // The slide citation is generated together with the metadata (server
  // side, in the same fetch). Pages whose record predates that, whose
  // citation call failed, or whose metadata was just edited get it here, on
  // open — never first on opening the share popover. One attempt per page.
  useEffect(() => {
    if (shareMode || !aiInfo?.enabled || !focusedBlockId || pptCite || pptCiteBusy) return;
    if (!(pageMeta || pageBibtex) || attemptedCiteRef.current.has(focusedBlockId)) return;
    attemptedCiteRef.current.add(focusedBlockId);
    makePptCitation(false, { id: focusedBlockId, content: pageTitle });
  }, [aiInfo?.enabled, focusedBlockId, pageMeta, pageBibtex, pptCite]);

  // Copy + tick feedback for the page popovers, keyed by which row was hit.
  async function copyFlash(kind, text) {
    let ok;
    if (kind === "ppt") {
      // Rich copy: PowerPoint/Word get real italics & bold via text/html;
      // plain-text targets keep the markdown source so **bold** survives a
      // paste into notes or any markdown editor.
      const esc = (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const html = esc
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/_([^_]+)_/g, "<i>$1</i>")
        .replace(/\*([^*]+)\*/g, "<i>$1</i>");
      ok = await copyRich(html, text || "");
    } else {
      ok = await copyText(text || "");
    }
    if (ok) flashCopied(kind);
    else setStatus(t("Copy failed — copy manually."));
  }

  useEffect(() => {
    if (!chatModel) return;
    try {
      localStorage.setItem("gamma-chat-model", chatModel);
      // Also file it under its key (registry ids are "<entryId>:<model>").
      const key = (aiInfo?.models || []).find((m) => m.id === chatModel)?.provider || chatModel.split(":")[0];
      if (key) {
        chatModelMemRef.current = { ...chatModelMemRef.current, [key]: chatModel };
        localStorage.setItem("gamma-chat-model-by-key", JSON.stringify(chatModelMemRef.current));
      }
    } catch {}
  }, [chatModel]);

  // Capture text selected inside the PDF viewer so chat can focus on it.
  // Committed on mouseup (not selectionchange) so the modifier key is known:
  // Ctrl+select APPENDS another passage, plain select replaces the set, and
  // a plain click in the PDF clears it. Clicking into the chat keeps it.
  const penSelectionRef = useRef(null);
  useEffect(() => {
    function onMouseUp(e) {
      if (!viewerWrapRef.current?.contains(e.target)) {
        // Notes: Ctrl+select rendered note text attaches its source range as
        // a chip. (A plain drag opens the editor and selects there — the
        // Cursor chip's Selection; selections inside an open editor are the
        // editor's business.)
        if ((e.ctrlKey || e.metaKey) && e.target.closest?.(".blockList")
            && !e.target.closest(".cm-editor, textarea, input")) {
          setTimeout(() => {
            const sel = window.getSelection();
            if (!sel || sel.isCollapsed || !sel.rangeCount || !sel.toString().trim()) return;
            const node = sel.anchorNode;
            const rendered = (node?.nodeType === 3 ? node.parentElement : node)?.closest?.(".blockRendered");
            if (rendered) addNoteSelection(sel.getRangeAt(0), rendered);
          }, 10);
        }
        return;
      }
      const additive = e.ctrlKey || e.metaKey;
      setTimeout(() => {
        const sel = window.getSelection();
        const text = sel ? sel.toString().trim() : "";
        if (!text) {
          // Highlight clicks set the quote as the selection (in their own
          // click handler) — don't clear it from here.
          if (!additive && !e.target.closest?.("[data-hl-id]")) {
            setPdfSelections([]);
            // Optionally the same gesture drops PDF snapshots pending in the
            // chat (Settings → AI chat). Pasted images are never touched.
            if (chatImgAutoClearRef.current && pdfImagesRef.current.size) {
              setChatImages((prev) => prev.filter((p) => !pdfImagesRef.current.has(pictureUrl(p))));
            }
          }
          return;
        }
        const node = sel.anchorNode;
        const el = node?.nodeType === 3 ? node.parentElement : node;
        if (!(viewerWrapRef.current && el && viewerWrapRef.current.contains(el))) return;
        addPdfSelection(text, additive, rangeSpot(sel.getRangeAt(0)));
      }, 10);
    }
    // Touch has no mouseup after a long-press/handle selection, so iPad picks
    // the passage up from selectionchange instead. No modifier exists there,
    // so it always replaces; and an emptied selection is left alone rather
    // than cleared, since a tap into the chat is indistinguishable from a tap
    // that dismissed the selection — and that tap must keep it. A pen's drag
    // has no mouseup either, and is read once, at its end (onPenSelection).
    let selTimer = null;
    function readSelection() {
      const sel = window.getSelection();
      const text = sel ? sel.toString().trim() : "";
      if (!text) return;
      const node = sel.anchorNode;
      const el = node?.nodeType === 3 ? node.parentElement : node;
      if (!(viewerWrapRef.current && el && viewerWrapRef.current.contains(el))) return;
      addPdfSelection(text, false, rangeSpot(sel.getRangeAt(0)));
    }
    penSelectionRef.current = readSelection;
    function onSelectionChange() {
      if (lastPointer() === "mouse") return;
      clearTimeout(selTimer);
      if (!penSelecting()) selTimer = setTimeout(readSelection, 350);
    }
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("selectionchange", onSelectionChange);
    return () => {
      clearTimeout(selTimer);
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("selectionchange", onSelectionChange);
    };
  }, []);

  // Selection is page-scoped: drop it when switching documents.
  useEffect(() => { setPdfSelections([]); }, [focusedBlockId]);

  // The library listing: the pages, and the folder and label trees read in
  // the same snapshot (GET /blocks/root/children). A read that is a load
  // rather than the person's own change (`still`: the session's first,
  // coming back from a page, the window's wake, a refresh in the
  // background) marks what it sets, so the listing moves nothing for it,
  // and so does one that fails. A read that fails ends the wait as well:
  // the listing says so where its empty line would be. Each page carries
  // its effective parent as `_parentPage` ("" for a library page), the
  // field libraryUtils' page paths read (pageAncestors, pagePathLabel).
  function fetchHomeBlocks({ still = false } = {}) {
    return apiJson(`${API}/blocks/root/children`)
      .then((data) => {
        const children = (Array.isArray(data.children) ? data.children : [])
          .filter((b) => !trashingRef.current.has(b.id)) // a delete batch still on its way (trashPages)
          .map((b) => ({ ...b, _parentPage: b.parent || "" }));
        // with how many files each work folder's directory holds (folderMeta)
        libTreeRef.current = { ...libraryTree(data), workCounts: data.work_counts || {} };
        if (still) for (const read of [children, libTreeRef.current]) homeReadRef.current.add(read);
        setHomeBlocks(children);
        setLibTree(libTreeRef.current);
        setHomeLoaded(true);
        setHomeFailed(false);
        return children;
      })
      .catch(() => {
        const none = [];
        for (const read of [none, EMPTY_TREE]) homeReadRef.current.add(read);
        setHomeBlocks(none);
        setLibTree(EMPTY_TREE);
        setHomeFailed(true);
        return [];
      });
  }

  // The listing is read again when the window comes back (at most every
  // 15 s, like the prefs' wake pulls), so a folder or label another device
  // made, renamed, moved or deleted shows without a reload — the home view
  // does not join the trees' rooms (docs/dev/home_library.md). The open
  // page's family with it: a page another client makes inside it, or moves
  // into or out of it, writes nothing to its room, so its Pages inside
  // panel follows then, as the listing does.
  useEffect(() => {
    if (!authUser?.user || shareMode) return undefined;
    let lastAt = Date.now();
    const onWake = () => {
      if (document.hidden || Date.now() - lastAt < 15000) return;
      lastAt = Date.now();
      fetchHomeBlocks({ still: true });
      rereadFamily();
    };
    window.addEventListener("focus", onWake);
    document.addEventListener("visibilitychange", onWake);
    return () => {
      window.removeEventListener("focus", onWake);
      document.removeEventListener("visibilitychange", onWake);
    };
  }, [authUser?.user, shareMode]);

  useEffect(() => {
    function onExpired() { setAuthUser(false); }
    window.addEventListener("gamma-auth-expired", onExpired);
    return () => window.removeEventListener("gamma-auth-expired", onExpired);
  }, []);
  // A highlight (or a note linked to one) whose passage the PDF lands on
  // once its page's document and highlights are up (the effect after
  // `highlights`); a request on the open page bumps pdfJumpReq.
  const pendingJumpRef = useRef(null);
  const [pdfJumpReq, setPdfJumpReq] = useState(0);
  // Phase B2a: drop indicator state
  const [dropTarget, setDropTarget] = useState(null); // { targetId, above, rect }
  const dragLeaveTimer = useRef(null);

  useEffect(() => {
    window._gammaSetDropTarget = (dt) => { clearTimeout(dragLeaveTimer.current); setDropTarget(dt); };
    // The indicator is fixed to the viewport, so a drag end the rows miss (a
    // row re-rendered under the pointer, the dragend of a handle the move
    // detached) would leave a line hanging over the notes (#88). Every drag
    // starts and ends clean here; capture, so a new drag is reset before the
    // handle's onDragStart marks it, and a drop only hides the line —
    // onBlockDrop still reads _dragState.dropTarget.
    const reset = () => {
      _dragState.draggingId = null;
      _dragState.draggingIds = null;
      _dragState.fragment = null;
      _dragState.dropTarget = null;
      setDropTarget(null);
    };
    const hide = () => setDropTarget(null);
    window.addEventListener("dragstart", reset, true);
    window.addEventListener("dragend", reset, true);
    window.addEventListener("drop", hide, true);
    return () => {
      window._gammaSetDropTarget = null;
      window.removeEventListener("dragstart", reset, true);
      window.removeEventListener("dragend", reset, true);
      window.removeEventListener("drop", hide, true);
    };
  }, []);



  const [notesVisible, setNotesVisible] = useState(true);
  // A closed window forgets its fold: shown again, from wherever, it comes
  // back whole rather than as a bare header bar.
  useEffect(() => {
    setCollapsedWins((prev) => ((chatHidden && prev.chat) || (!notesVisible && prev.notes) || (commentsHidden && prev.comments)
      ? { ...prev, chat: prev.chat && !chatHidden, notes: prev.notes && notesVisible, comments: prev.comments && !commentsHidden } : prev));
  }, [chatHidden, notesVisible, commentsHidden]);
  // Phone layout: which overlay panel covers the center ('notes' | 'chat' |
  // null = the main view). Reset on navigation so a new page opens on its content.
  const isPhone = useIsPhone();
  const [phonePanel, setPhonePanel] = useState(null);
  // The Comments window's first showing: the right-hand column, last, when
  // no slot holds it yet (a page's own saved layout may not).
  useEffect(() => {
    if (commentsHidden || isPhone) return;
    if (!["left", "right", "bottom"].some((side) => layout[side].includes("comments"))) moveWindow("comments", "right", 99);
  }, [commentsHidden, isPhone, layout]); // eslint-disable-line react-hooks/exhaustive-deps
  // A phone overlay stays MOUNTED once it has been opened, hidden rather than
  // unmounted while another tab is up: unmounting threw away the notes scroll
  // position (and the chat's draft) on every flip to the PDF and back. Panels
  // the user never opened are still not mounted, so chat history isn't loaded
  // on a phone that only reads.
  const phoneSeen = useRef({});
  if (isPhone && phonePanel) phoneSeen.current[phonePanel] = true;
  // A touch screen, in any layout: kill the browser's own zoom. The viewport
  // meta covers Android and `touch-action: manipulation` (on html) the
  // double-tap, but iOS Safari honours neither — only refusing its gesture
  // events stops a pinch outside the viewers from scaling the whole app
  // until the toolbars sit off-screen. The viewers' own pinch-zoom is
  // unaffected: it runs off touchstart/touchmove.
  useEffect(() => {
    if (!touchScreen()) return undefined;
    const block = (e) => e.preventDefault();
    for (const ev of ["gesturestart", "gesturechange", "gestureend"]) {
      document.addEventListener(ev, block, { passive: false });
    }
    return () => {
      for (const ev of ["gesturestart", "gesturechange", "gestureend"]) {
        document.removeEventListener(ev, block);
      }
    };
  }, []);
  // What the startup bundle leaves out (docs/dev/frontend-refactor.md, "Lazy
  // boundaries"), fetched before it is needed. A desktop shows the chat dock
  // from the start: its module goes out with the session check. The PDF
  // engine too when the address opens a page or a share; on the library it
  // waits until the listing has been in for a moment (the first screen
  // painted) and the main thread is idle, with the Ctrl+P palette, which has
  // no button to hover.
  useEffect(() => {
    if (!isPhone) ChatDock.preload();
    if (initialUrl || initialShare || initialBlockId) preloadPdfEngine();
    if (initialThreadId) ThreadPopover.preload();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!homeLoaded) return undefined;
    return whenIdle(() => { preloadPdfEngine(); QuickOpen.preload(); PagePicker.preload(); }, 1500);
  }, [homeLoaded]);
  // What a drag on the PDF does — text selection (default) or rectangle
  // drawing. A mouse holds Ctrl; a finger has no Ctrl, so a touch screen gets
  // a sticky toggle button in the viewer's zoom column instead.
  const [areaSelectMode, setAreaSelectMode] = useState(false);
  // The build fence (ink/buildFence.js): a server updated under this
  // bundle stops the page drawing until it is reloaded (inkStale).
  const { inkStale, noteServerBuild } = useBuildFence({ sessionBuild: authUser?.build, setStatus });
  // Handwriting (docs/dev/handwriting.md): the tool strip (ink/useInkStrip.js:
  // its state, the presets on it, the armed tool and the commands that
  // change it), which the text boxes below read; the group outlined after
  // a jump.
  const {
    inkUi, inkVisible, inkPenPreset, inkPen, inkTool, textArmed, inkPenTool,
    setInkTools, pickInkTool, openInkStrip, toggleInkShape, previousTool, inkEraserLifted,
    revealInkStrip, openRulerRow, closeRulerRow, closeInkStripUi, toggleInkOptions, closeInkOptions,
    applyPencilUi, disarmInk, restorePen,
  } = useInkStrip({ focusedBlockId, readOnly, inkStale, inkTools, setInkToolsRaw,
    inkShapeKind, inkShapeFill, inkEraserReturn });
  // The text highlighters (pdf/useTextHighlighter.js, docs/dev/settings.md
  // "Text highlighters"): the account's list, the one armed this session (a
  // selection the mouse or the pen lets go of is then marked at once in its
  // look), their strip buttons' menu and the edits. Arming one puts the
  // pen's strip away; `highlighterColor` only seeds the first.
  const textHighlighter = useTextHighlighter({
    stored: highlightPresets, setStored: setHighlightPresets, seedColor: highlighterColor, palette, setPalette,
    canHighlight: !readOnly && !!pdfUrl && !pdfHidden, inkOpen: inkUi.open, onArm: () => { if (inkUi.open) closeInkStrip(); },
    openSettings: openSettingsPane, setStatus,
  });
  const [inkFlash, setInkFlash] = useState(null);
  // Sheets: the open page's (for handlers that run after an await); in the
  // notebook view the sheet under the middle of the view, the viewer's scroller.
  const nbSheetsRef = useRef([]);
  const nbScrollRef = useRef(null);
  const [nbCurrent, setNbCurrent] = useState("");
  const [paperMenu, setPaperMenu] = useState(false);
  const [flashingId, setFlashingId] = useState(null);
  const [highlightMenu, setHighlightMenu] = useState(null); // { id, x, y } or null
  const [focusedId, setFocusedId] = useState(null);
  // The viewer's own state beside the tree (blockModel's `view`): the block
  // whose editor is open and the folding they changed. Never in the tree:
  // opening an editor or revealing a block is not an edit, and the live
  // session and the undo history see only document changes.
  const [view, setView] = useState(EMPTY_VIEW);
  const viewRef = useRef(view);
  viewRef.current = view;
  const startEditing = (id) => setView((v) => withEditing(v, id));
  const reveal = (id) => setView((v) => revealBlock(blocksRef.current, v, id));
  const [pageTitle, setPageTitle] = useState("");
  const [titleEditing, setTitleEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");

  useEffect(() => {
    document.title = pageTitle
      ? `${pageTitle} — Gamma`
      : t("Gamma — Annotate PDFs, Share Your Thinking");
  }, [pageTitle]);

  const scrollToRef = useRef(() => {});
  const flashTimerRef = useRef(null);
  const [attachModeBlockId, setAttachModeBlockId] = useState(null);
  const attachModeBlockIdRef = useRef(null);
  // A highlight being moved (its menu's "Move to a selection…", a lost
  // one's row button): the next selection on the PDF becomes its place
  // instead of making a new highlight (docs/dev/highlight_relocation.md).
  const [moveHighlightId, setMoveHighlightId] = useState(null);
  const moveHighlightIdRef = useRef(null);
  const blocksRef = useRef(blocks);
  blocksRef.current = blocks;
  const blockRefs = useRef({});
  const pendingFocusRef = useRef(null);
  const pendingBlockScrollRef = useRef(null);
  // The home menu's Share… / Export… / Ask AI waiting for its page
  // ({id, action}): staged by the open that lands it (openBlock's `then`),
  // run by the effect after openPageThen.
  const pendingPageActionRef = useRef(null);
  // A request to scroll to (and focus) a block's row once it is rendered:
  // the id in the ref, and a counter the effect below listens to — a
  // request must be answered even when nothing else changes (the block was
  // never folded away, the tree is as it was).
  const [blockScrollReq, setBlockScrollReq] = useState(0);
  const scrollToBlock = (id) => { pendingBlockScrollRef.current = id; setBlockScrollReq((n) => n + 1); };
  // A jump to a block of the open page (a copied block link, a [[ref]], the
  // sync popover's): its row in the notes and, a highlight's, its passage on
  // the PDF, as openBlock's `block` lands one on another page.
  const landOnBlock = (id) => {
    scrollToBlock(id);
    reveal(id);
    pendingJumpRef.current = id;
    setPdfJumpReq((n) => n + 1);
  };
  // The AI agent's live footprint on the open page (handleAgentEvent):
  // aiMarks lights up the blocks it reads/edits (id → {kind, n}), aiLive is
  // the note edit it is still writing — streamed into the block (edit_block)
  // or a ghost row (create_block) — and aiScan is a whole-page read: the
  // rows ring top to bottom ({n, order: id → index}; BlockRow staggers its
  // ring by index, n alternates the animation name so back-to-back reads
  // restart it) while the list's left edge pulses. All display-only; the
  // tree itself reloads from the server when an edit is applied.
  const [aiMarks, setAiMarks] = useState(() => new Map());
  const [aiLive, setAiLive] = useState(null);
  const [aiScan, setAiScan] = useState(null);
  // The block row the cursor is on, for the chat's "Cursor" chip and
  // focus_block_id (null on the home page / when no row is focused); `sel`
  // is the text selected in it ({from, to, text} of its source).
  const focusedNote = useMemo(() => {
    if (!focusedBlockId || !focusedId) return null;
    const b = findBlock(blocks, focusedId);
    if (!b) return null;
    const text = noteSel?.id === b.id ? (b.content || "").slice(noteSel.from, noteSel.to) : "";
    return { id: b.id, text: blockChipText(b), ...(text.trim() ? { sel: { from: noteSel.from, to: noteSel.to, text } } : {}) };
  }, [blocks, focusedId, focusedBlockId, noteSel]);
  const aiMarkTimersRef = useRef(new Map());
  const aiMarkSeqRef = useRef(0);
  // Trees that did not come from an edit here: "load" (fetched from the
  // server — it becomes the collab base as is) or "remote" (another client's
  // ops applied — the base already has them, so the diff sends only our own
  // edits in the same render). Neither is an undo step, and a load empties
  // the undo stack. "fold" (foldBlocks) is an edit here that belongs to the
  // one before it, a text box's measured size: sent like any edit, part of
  // that edit's undo step rather than one of its own. Kept on the tree
  // VALUE, never a flag beside setBlocks: a flag outlived a load React
  // skipped (nothing changed) or was set by an effect that runs before the
  // autosave one, and the edit committed with it was taken for a load and
  // never sent — the server later refused the block as unknown.
  const treeOriginRef = useRef(new WeakMap());
  const loaded = (tree) => { treeOriginRef.current.set(tree, "load"); return tree; };
  const foldBlocks = (fn) => setBlocks((prev) => {
    const next = fn(prev);
    if (next !== prev) treeOriginRef.current.set(next, "fold");
    return next;
  });
  // A lasso step that took pictures, or Convert to text
  // (ink/useInkSelection.js), is one step of two histories: its strokes go
  // to the stroke history, its pictures or text box to the block history.
  // The tree its block half commits carries the step's link, kept on the
  // tree VALUE as the origins are, which the block history's entry keeps
  // (blockHistory.js linkOf).
  const stepLinksRef = useRef(new WeakMap());
  const stepBlocks = (link, fn) => setBlocks((prev) => {
    const next = fn(prev);
    if (next !== prev) stepLinksRef.current.set(next, link);
    return next;
  });
  // The block whose children the person folded or opened, kept on the view
  // VALUE that did it as the origins are on the tree: that fold moves
  // (BlockTree), and one any other view change makes (a jump revealing a
  // block, an editor opening) does not.
  const foldMovesRef = useRef(new WeakMap());
  const saveNowRef = useRef(false); // next autosave runs without the debounce (editor close)
  // THE undo history (Ctrl+Z anywhere on the page, editors included):
  // derived from the block tree's transitions, see blockHistory.js. Declared
  // before the autosave effect so its effect sees a tree's origin first.
  const caretRef = useRef(null);         // {id, from, to} the open editor's live selection
  const caretBeforeRef = useRef(null);   // {id, from, to} of the last editor change
  const pendingCaretRef = useRef(null);  // caret to place once a restore has committed
  // Text boxes (markup/useTextBoxes.js): the selected or edited box, its
  // edits. A box's editor and the notes' row editor are never open at once,
  // nor a selected box and the lasso's selection or a selected picture
  // (pictures, below); an ink tool armed makes the boxes inert.
  const textBoxes = useTextBoxes({
    blocks, blocksRef, setBlocks, foldBlocks, readOnly, pageId: focusedBlockId,
    rowEditingId: view.editingId, closeRowEditor: () => setView((v) => (v.editingId ? closeEditing(v, v.editingId) : v)),
    style: textBoxStyle, setStyle: setTextBoxStyle, keybindings,
    inert: !!inkUi.tool && inkUi.tool !== "text", onSelect: () => { clearInkSelection(); pictures.deselect(); },
  });
  const blockHistory = useBlockHistory(blocks, setBlocks, {
    originOf: (tree) => treeOriginRef.current.get(tree),
    linkOf: (tree) => stepLinksRef.current.get(tree),
    pageId: focusedBlockId,
    enabled: !readOnly && !!focusedBlockId,
    caretRef,
    caretBeforeRef,
    onCaret: (caret) => { pendingCaretRef.current = caret; },
    // a run of typing in a box's editor merges like one in a row's, and
    // an undo there keeps that editor, not the box's row, open
    editingId: view.editingId || textBoxes.editingId,
    onEditing: (id) => { if (!id || id !== textBoxes.editingId) setView((v) => withEditing(v, id)); },
  });
  // After a restore the kept-open editor has synced the new text (child
  // effects run first); now put the cursor where the change was.
  useEffect(() => {
    const caret = pendingCaretRef.current;
    if (!caret) return;
    pendingCaretRef.current = null;
    const ed = blockRefs.current[caret.id]?.current;
    if (!ed) return;
    ed.focus();
    ed.setSelectionRange(caret.from, caret.to);
  }, [blocks]);

  function registerRef(id, ref) {
    blockRefs.current[id] = ref;
  }

  useEffect(() => {
    // A request is a block id, or {id, caret: "start" | "end" | offset,
    // head?, reopen} from a keyboard command or a remote move of the row:
    // the caret (a selection up to `head`) lands where it says, and `reopen`
    // counts the tries at re-opening an editor a DOM move closed (the blur a
    // browser fires when a focused node moves).
    const req = pendingFocusRef.current;
    if (!req || readOnly) return;
    const id = typeof req === "string" ? req : req.id;
    const ref = blockRefs.current[id];
    if (ref?.current) {
      ref.current.focus();
      if (typeof req === "object" && req.caret != null) {
        const len = ref.current.value.length;
        const pos = req.caret === "end" ? len : req.caret === "start" ? 0 : Math.min(req.caret, len);
        ref.current.setSelectionRange(pos, req.head != null ? Math.min(req.head, len) : pos);
      }
      pendingFocusRef.current = null;
    } else if (typeof req === "object" && req.reopen > 0) {
      if (findBlock(blocks, id) && view.editingId !== id) {
        req.reopen -= 1;
        startEditing(id);
      }
    }
  }, [blocks, view.editingId, readOnly]);
  // A DOM move of the open editor's row (a move, an indent or outdent,
  // another client's op) blurs or remounts its editor: onStartEdit ignores
  // that one close (keepEditRef) and the effect above re-opens it, the
  // selection back where it was.
  function keepEditorThrough(id, caret, head) {
    keepEditRef.current = { id, until: performance.now() + 500 };
    pendingFocusRef.current = { id, caret, head, reopen: 2 };
  }

  // The open page's standing with this computer (`vault`, above): where its
  // vault keeps the conflicts the merge chips below read, and how many.
  const vaultState = vault.state;
  const vaultConflicts = vaultConflictsPath(vaultState);
  const vaultConflictCount = vaultState?.vault?.conflicts || 0;
  // The work folder's agent on this computer that the maker names in a
  // comment (`@codex`, vaultState.js mentionAgent): the comment fields'
  // `links.agent`, never MentionContext, which reaches the notes.
  const commentAgent = useMemo(() => (IS_DESKTOP ? mentionAgent(vaultState) : null), [vaultState]);

  // Merges an offline copy's sync decided on this page's blocks
  // (docs/dev/mirror.md), the differences a Zotero link kept
  // (docs/dev/zotero_sync.md), and the blocks whose file won over Gamma's
  // text in a synced vault (kind file_won, read from the vault's link): a
  // chip on each such row (MergeResolver), and for a link's row on the page
  // itself (its record) one on the page's header. Read on page open and
  // after any sync event; a decision is posted and the block's new text
  // arrives over the page socket like any edit.
  const [merges, setMerges] = useState(null);
  // The workspace's link (syncLinks.js pillLink): the session row's, or one
  // the pill detached ({ws, kind}, `pillHeld`), whose pill stays, detached
  // with Reattach, though the row read again lists an ordinary workspace.
  const [pillHeld, setPillHeld] = useState(null);
  const syncKind = pillLink(workspace, pillHeld);
  const mirrorWs = syncKind ? workspace.id : "";
  const loadMerges = useCallback(async () => {
    if ((!mirrorWs && !vaultConflicts) || !focusedBlockId || !authUser?.user) { setMerges(null); return; }
    const page = `/conflicts?page=${encodeURIComponent(focusedBlockId)}`;
    const read = (path) => apiJson(`${API}${path}`).then((d) => d.conflicts || [], () => []);
    const [linked, filed] = await Promise.all([mirrorWs ? read(linkPath(syncKind, mirrorWs, page)) : [],
      vaultConflicts && vaultConflictCount ? read(`${vaultConflicts}${page}`) : []]);
    const map = new Map();
    for (const c of [...linked, ...filed]) if (!map.has(c.block_id)) map.set(c.block_id, c);
    setMerges(map.size ? map : null);
  }, [syncKind, mirrorWs, vaultConflicts, vaultConflictCount, focusedBlockId, authUser?.user]);
  // No timer of its own: the sync pill polls the mirror and raises
  // "gamma:mirror-changed" when its open conflicts move.
  useEffect(() => { loadMerges(); }, [loadMerges]);
  useEffect(() => {
    window.addEventListener("gamma:mirror", loadMerges);
    window.addEventListener("gamma:mirror-changed", loadMerges);
    return () => {
      window.removeEventListener("gamma:mirror", loadMerges);
      window.removeEventListener("gamma:mirror-changed", loadMerges);
    };
  }, [loadMerges]);
  // One chip open at a time, and the page's conflicts in tree order: the
  // chip's ‹ › walk them and a decision opens the next one, so a page of
  // conflicts is worked through without hunting for the chips.
  const [mergeOpen, setMergeOpen] = useState(null);
  useEffect(() => { setMergeOpen(null); }, [focusedBlockId]);
  // (a Zotero link's row on the page itself, its record, comes first)
  const recordMerge = syncKind === "zotero" && focusedBlockId ? merges?.get(focusedBlockId) || null : null;
  const mergeOrder = useMemo(
    () => (merges ? [...(recordMerge ? [focusedBlockId] : []), ...flattenBlocks(blocks).map((b) => b.id).filter((id) => merges.has(id))] : []),
    [merges, blocks, recordMerge, focusedBlockId],
  );
  function showMerge(id) {
    setMergeOpen(id);
    if (id && id !== focusedBlockId) jumpToRef.current?.(focusedBlockId, id);
  }
  function stepMerge(fromId, delta) {
    if (!mergeOrder.length) { setMergeOpen(null); return; }
    const i = Math.max(0, mergeOrder.indexOf(fromId));
    showMerge(mergeOrder[(i + delta + mergeOrder.length) % mergeOrder.length]);
  }
  const mergeNav = (id) => ({ index: mergeOrder.indexOf(id) + 1, total: mergeOrder.length, onStep: (delta) => stepMerge(id, delta) });
  async function resolveMerge(conflict, choice) {
    const base = isVaultConflict(conflict) ? vaultConflicts : linkPath(syncKind, mirrorWs, "");
    await apiJson(`${API}${base}/conflicts/${conflict.id}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice }),
    });
    if (isVaultConflict(conflict)) vault.refresh();
    // on to the next conflict down the page (the one above when it was the last), else done
    const rest = mergeOrder.filter((id) => id !== conflict.block_id);
    const i = Math.max(0, mergeOrder.indexOf(conflict.block_id));
    showMerge(rest.length ? rest[Math.min(i, rest.length - 1)] : null);
    window.dispatchEvent(new CustomEvent("gamma:mirror"));
  }
  // Jump to a block on this page or another — the sync popover's lists and
  // Settings' merges (the "gamma:jump" event) land on the block itself.
  const jumpToRef = useRef(null);
  jumpToRef.current = (pageId, blockId) => {
    setOpenPopover(null);
    // a row of the folders or labels tree (a Zotero collection's) is about
    // its folder or label, which the library shows: the tree is no page
    if (pageId === "folders" || pageId === "labels") {
      if (!homeMode) goHome();
      if (pageId === "folders") openFolder(blockId || lib.root);
      else if (blockId) openLabel(blockId, "");
      return;
    }
    // a conflict on the page itself (a Zotero link's record) is on its header, not a row
    if (blockId && blockId === pageId) blockId = "";
    if (pageId && pageId !== focusedBlockId) { openPage(pageId, blockId); return; }
    if (blockId) landOnBlock(blockId);
  };
  useEffect(() => {
    const h = (e) => jumpToRef.current?.(e.detail?.page, e.detail?.block);
    // the link was forgotten: this is an ordinary workspace again (no pill, no merge chips, no PDF a link brings)
    const gone = () => setWorkspace(linkGone);
    window.addEventListener("gamma:jump", h);
    window.addEventListener("gamma:mirror-gone", gone);
    return () => { window.removeEventListener("gamma:jump", h); window.removeEventListener("gamma:mirror-gone", gone); };
  }, []);

  useEffect(() => {
    if (!pendingBlockScrollRef.current) return;
    const id = pendingBlockScrollRef.current;
    const row = document.querySelector(`[data-block-id="${id}"]`);
    if (row && !row.closest("[inert]")) {
      row.scrollIntoView({ block: "center", behavior: "smooth" });
      setFocusedId(id);
      pendingBlockScrollRef.current = null;
    } else if (isFoldedAway(blocks, id, view)) {
      // Inside a folded parent: unfold (the viewer's state — no ops, no
      // undo step) and try again once it renders.
      setView((v) => revealBlock(blocks, v, id));
    }
    // Otherwise the row isn't rendered yet (not in the tree yet, or the notes
    // window is closed, or folded — where its row waits inert and out of
    // sight — a highlight made on the PDF alone): keep the ref and try again
    // on the next tree change or when the notes come back.
  }, [blocks, view, blockScrollReq, notesVisible, collapsedWins.notes, phonePanel]);

  // A page's passive landing (openBlock): the notes scroll to where the
  // reader left off, else to the top, and that row flashes once — but no
  // row is focused, so the chat's Cursor chip waits for a real click or
  // caret. A tab's page back where it was left this session (`kept`) is
  // no landing to point out: it does not flash. Explicit jumps (deep
  // links, highlights, backlinks, search hits, citations) go through
  // pendingBlockScrollRef above and do focus.
  const landingRef = useRef(null); // {id, offset?, kept?} ("" = the top) until the page's rows render
  // The open page's notes list (notesWindow's), never a tab group's view of
  // another page that draws its notes with the same classes (PagePane).
  const liveSidebarRef = useRef(null);
  const liveNotesList = () => liveSidebarRef.current?.querySelector(":scope > .blockList") || null;
  // Whole notes selected as in Notion, by a drag across them, a box from
  // the blank beside them, Shift+click or Esc (editor/useBlockSelection.js);
  // what its keys and the handle's menu do is blockSelectionAction's.
  const blockSel = useBlockSelection({
    enabled: !readOnly && !!focusedBlockId,
    blocks,
    editing: view.editingId,
    list: liveNotesList,
    tree: () => blocksRef.current,
    view: () => viewRef.current,
    anchorOf: () => viewRef.current.editingId || focusedId,
    closeEditor: () => {
      const active = document.activeElement;
      if (active && liveNotesList()?.contains(active)) active.blur();
      pendingFocusRef.current = null;
      setView((v) => (v.editingId ? closeEditing(v, v.editingId) : v));
    },
    edit: (id) => {
      pendingFocusRef.current = { id, caret: "end" };
      startEditing(id);
      setFocusedId(id);
    },
    act: (action, ids) => blockSelectionAction(action, ids),
  });
  useEffect(() => {
    const land = landingRef.current;
    if (!land) return;
    landingRef.current = null;
    const list = liveNotesList();
    if (!list) return;
    const row = land.id ? list.querySelector(`.blockRowWrap[data-block-id="${land.id}"]`) : null;
    if (!row) { list.scrollTop = 0; return; }
    list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top + (land.offset || 0);
    if (!land.kept) triggerFlash(land.id);
  }, [blocks]);

  // Fetch backlinks for the focused block, while the "Linked from" section
  // is on (Settings → Reading & editing › Notes). Not in the share view:
  // backlinks span the library, so the server refuses share tokens (403) by
  // design.
  useEffect(() => {
    if (!focusedBlockId || shareMode || !backlinksVisible) { setBacklinks([]); return; }
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson(`${API}/blocks/${focusedBlockId}/backlinks`);
        if (!cancelled) setBacklinks(data.backlinks || []);
      } catch { if (!cancelled) setBacklinks([]); }
    })();
    return () => { cancelled = true; };
  }, [focusedBlockId, shareMode, backlinksVisible]);

  // The open page among pages (GET /pages/{id}/family): its ancestors, top
  // first, for the crumbs over its title, and the pages right inside it
  // for the Pages inside panel. Read on every open (familyRead, bumped by
  // the opens), again after this tab's own sub-page writes and when the
  // window comes back, with the listing (its wake, above); a share view
  // reads it with its token, the ancestors the share does not reach left
  // out. Until it lands the page reads as having none (`openFamily`). The
  // open of a listed page asks for it alongside the tree (prefetchFamily),
  // so the crumbs and the panel land with the tree; the read it bumps takes
  // that answer, and any other read (or one for another page) asks again.
  const [pageFamily, setPageFamily] = useState(NO_FAMILY);
  const [familyRead, setFamilyRead] = useState(0);
  const rereadFamily = () => setFamilyRead((n) => n + 1);
  const familyAheadRef = useRef(null); // {page, read}: an open's read, not yet taken
  const readFamily = (id) => apiJson(`${API}/pages/${encodeURIComponent(id)}/family`).then(
    (d) => ({ page: id, ancestors: d.ancestors || [], subPages: d.sub_pages || [] }), () => NO_FAMILY);
  const prefetchFamily = (id) => { familyAheadRef.current = { page: id, read: readFamily(id) }; };
  useEffect(() => {
    const ahead = familyAheadRef.current;
    familyAheadRef.current = null;
    if (!focusedBlockId) { setPageFamily(NO_FAMILY); return undefined; }
    let cancelled = false;
    (ahead?.page === focusedBlockId ? ahead.read : readFamily(focusedBlockId))
      .then((family) => { if (!cancelled) setPageFamily(family); });
    return () => { cancelled = true; };
  }, [focusedBlockId, familyRead]);
  const openFamily = pageFamily.page === focusedBlockId ? pageFamily : NO_FAMILY;
  // The open page is inside another: its folders are its top's, so its
  // header files it by labels only.
  const openSubPage = openFamily.ancestors.length > 0;
  // A crumb or a Pages inside row: that page, Back returning here (a share
  // view goes through its own navigation, openBlock's).
  const openFamilyPage = (id) => openBlock(id, { pushNav: true, restoreScroll: true });

  // The page's live session (collaboration/usePageCollab.js): the tree's transitions become ops
  // sent in debounced batches, other clients' batches arrive over the page
  // socket and apply below, presence rides the same socket. A load (a tree
  // marked by `loaded`) becomes the session's base instead of a change.
  const applyRemoteRef = useRef(null);
  const loadedSeqRef = useRef(null); // the op-log seq the last fetched tree reflects
  // An empty page opens with one client-minted placeholder block (openBlock)
  // that the server has never seen. It arrives with the load,
  // so the collab base must NOT count it as known — the first edit to it is
  // then diffed as an `insert`, not a `set` on a block the server rejects
  // (404 "no such block"). Cleared once that insert has been queued.
  const seedBlockIdRef = useRef(null);
  // The open page went to Recently deleted while open ({trashed}: its trash
  // entry when the server named it) — its edits wait, unsent — or null.
  const [pageGone, setPageGone] = useState(null);
  useEffect(() => { setPageGone(null); }, [focusedBlockId]);
  // The open page's comment threads (comments/useComments.js, docs/dev/
  // comments.md "In the app"). Who reads them reads the page; who writes
  // is signed in: a member (a viewer too), or an account a share admits —
  // not a link visitor without one, nor a guest account through a link.
  const commentMe = shareMode ? (shareInfo?.viewerIsGuest ? "" : shareInfo?.viewer || "") : authUser?.user || "";
  const canComment = !!commentMe;
  // Whose Inbox the page's comments may reach, so the Comments window
  // offers its Notify me: a commenter who is no guest account.
  const canFollow = canComment && (shareMode || !authUser?.is_guest);
  // The workspace's owner deletes anyone's comment; not through a share link.
  const commentOwner = !shareMode && workspace?.role === "owner";
  const comments = useComments({ pageId: focusedBlockId, enabled: !!focusedBlockId, me: commentMe, onStatus: (msg) => setStatus(msg),
    flush: () => collabRef.current?.flush() });
  const commentsRef = useRef(comments);
  commentsRef.current = comments;
  // Where the comment controls show: not on a page host's publication
  // (comments stay off the share host), nor on a page whose comments
  // answer 404 or 403.
  const commentsOn = !publicPage && comments.readable;
  const collab = usePageCollab({
    pageId: focusedBlockId,
    enabled: !!focusedBlockId,
    canWrite: !readOnly,
    // Someone wrote a comment on the page, or the socket came back: refetch its threads.
    onComments: () => commentsRef.current.refresh(),
    onRemoteOps: (ops, pageId, pos, before, own) => applyRemoteRef.current?.(ops, pageId, pos, before, own),
    onReload: (pageId) => {
      if (pageId === focusedBlockIdRef.current) loadBlocksForBlock(pageId);
    },
    onStatus: (msg) => setStatus(msg),
    onBuild: (build) => noteServerBuild(build),
    // Lasting, unlike the status line: edits waiting ("pending") stay up
    // until they are saved (and the pill with them); a dropped edit
    // ("rejected") stays a while.
    onSaveNotice: (msg, kind) => {
      if (msg) logSys(msg, kind === "rejected" ? "error" : "warning");
      if (kind === "rejected") postPill("saveRejected", { msg, error: true }, { after: [20000, null] });
      else postPill("save", msg ? { msg, error: true } : null);
    },
    onGone: (pageId, gone) => { if (pageId === focusedBlockIdRef.current) setPageGone(gone); },
    // a peer's handwriting stroke in progress: a wet stroke on its surface's layer
    onInkLive: (msg) => inkLive.receiveInkLive(msg),
    // a synced vault's round touched the page: how it stands with this computer, again
    onVault: vault.refresh,
  });
  const collabRef = useRef(collab);
  collabRef.current = collab;
  // Live co-drawing (ink/useInkLive.js): this tab's stroke in progress out
  // to the page's peers, and their wet strokes kept to the peers on it.
  const { handleInkLive, liveRemoved, liveSaved } = useInkLive({ focusedBlockId, readOnly, inkStale,
    peers: collab.peers, sendInkLive: (msg) => collabRef.current.sendInkLive(msg) });

  // The thread popover (comments/ThreadPopover.jsx): {blockId, threadId
  // (one to show and scroll to), anchor (the element it opened from, null
  // for the block's row control), compose (focus "New comment"), margin
  // (shown as the active card in the PDF's margin instead)}, or null.
  // A second press on the control it hangs from closes it.
  const [threadPop, setThreadPop] = useState(null);
  useEffect(() => { setThreadPop(null); }, [focusedBlockId]);
  // `pdf`: opened from the PDF (a highlight, its menu, the selection
  // popup) or a jump to a thread: a highlight whose page has room for the
  // margin opens its card there, its threadId the active card (an open
  // thread's; a resolved one has no card and opens in the popover).
  const pdfMarginRef = useRef(null); // set by PdfViewer: (hlId) => whether its card would show
  // A highlight's element on the PDF page, where its thread popover hangs.
  const highlightEl = (id) => document.querySelector(`[data-hl-id="${CSS.escape(id)}"]`);
  function openThread(blockId, anchor = null, { threadId = "", compose = false, toggle = true, pdf = false } = {}) {
    const thread = threadId ? commentsRef.current.threads.find((th) => th.id === threadId) : null;
    const margin = pdf && !(thread && !isOpen(thread)) && !!pdfMarginRef.current?.(blockId);
    if (margin) preloadCommentMargin(); else ThreadPopover.preload();
    setThreadPop((cur) => (toggle && cur?.blockId === blockId && !threadId ? null : { blockId, threadId, anchor, compose, margin }));
  }
  // Escape gives the focus back to the control it opened from.
  function closeThread(returnFocus) {
    const pop = threadPop;
    setThreadPop(null);
    if (!returnFocus || !pop || pop.margin) return;
    threadAnchor(pop)?.focus();
  }
  // The Properties popover (editor/BlockProperties.jsx): {blockId, anchor
  // (the element it hangs under: the row's handle, the highlight), at (a
  // menu's point {x, y} when there is no element), opener (what had the
  // focus, which Escape gives it back to), back (the ids stepped in from,
  // for its Back, at most 20), n (bumped by each open that is not a step:
  // the instance's key, so a step and Back change it in place)}, or null.
  // Another page closes it.
  const [propsPop, setPropsPop] = useState(null);
  useEffect(() => { setPropsPop(null); }, [focusedBlockId]);
  // `push`: a step into a block it names, which Back returns from. The
  // opener is what had the focus, or, when that was a row of the menu that
  // opened it (gone once the menu closes), the `opener` given (the library
  // card or "⋯" its menu opened from) or else the anchor. One that cannot
  // take the focus (a row's handle, a highlight on the PDF) leaves it.
  function openProps(blockId, anchor = null, { at = null, push = false, opener: given = null } = {}) {
    if (!blockId) return;
    BlockProperties.preload();
    const focused = document.activeElement;
    const opener = !focused || focused === document.body || focused.closest?.(".ctxMenu") ? given || anchor : focused;
    setPropsPop((cur) => ({ blockId, anchor, at,
      opener: push ? cur?.opener : opener,
      n: push && cur ? cur.n : (cur?.n ?? 0) + 1,
      back: push && cur ? [...cur.back, cur.blockId].slice(-20) : [] }));
  }
  function closeProps(returnFocus) {
    const opener = propsPop?.opener;
    setPropsPop(null);
    if (returnFocus && opener?.isConnected) opener.focus?.({ preventScroll: true });
  }
  // The selection popup's Comment: the highlight it just made ({id,
  // opened}), whose popover opens on it, from the passage once that is
  // drawn, with "New comment" focused. When the popover closes with
  // nothing on the highlight it is deleted again (the effect after
  // `highlights`).
  const freshCommentRef = useRef(null);
  function commentOnNewHighlight(id) {
    freshCommentRef.current = { id, opened: false };
    let frames = 0;
    const open = () => {
      if (freshCommentRef.current?.id !== id) return;
      const el = highlightEl(id);
      if (!el && ++frames < 30) { requestAnimationFrame(open); return; }
      openThread(id, el, { compose: true, toggle: false, pdf: true });
    };
    requestAnimationFrame(open);
  }
  // A thread of this page, from the Comments window or a deep link: its
  // block scrolled to (a highlight or an ink group on the PDF, a region on
  // the picture: its row and its place on the document), flashed, and the
  // popover open on the thread. A note's thread brings the notes over a
  // phone's PDF tab (showInNotes), so the note it is about shows under it.
  function showThread(thread) {
    if (thread.block_id !== focusedBlockId) {
      if (anchorOf(treeById.get(thread.block_id), positionKeyOf(doc))) landOnBlock(thread.block_id);
      else showInNotes(thread.block_id);
      triggerFlash(thread.block_id);
    }
    openThread(thread.block_id, null, { threadId: thread.id, toggle: false, pdf: true });
  }
  // A thread to open once its page's comments are in: the boot's ?thread=,
  // or one in this workspace named by its page and block (the inbox), as
  // {threadId, pageId} (pageId "": the first page that opens). The first
  // page whose comments load takes it: its own page opens the thread, any
  // other page drops it (the linked one never opened).
  const pendingThreadRef = useRef(initialThreadId ? { threadId: initialThreadId, pageId: initialPageId } : null);
  const threadGone = () => setStatus(t("That comment thread is no longer on this page."));
  async function openThreadAt(threadId, blockId, pageId) {
    if (pageId !== focusedBlockId) {
      pendingThreadRef.current = { threadId, pageId };
      openBlockLink(blockId || pageId, pageId);
      return;
    }
    pendingThreadRef.current = null;
    const here = commentsRef.current.threads.find((th) => th.id === threadId);
    if (here) { showThread(here); return; }
    // Not here yet: this refetch's own answer says.
    const got = await commentsRef.current.refresh();
    if (!got || got.pageId !== focusedBlockIdRef.current) return;
    const thread = got.threads.find((th) => th.id === threadId);
    if (thread) showThread(thread); else threadGone();
  }
  useEffect(() => {
    const want = pendingThreadRef.current;
    if (!want || !comments.loaded || !focusedBlockId) return;
    pendingThreadRef.current = null;
    if (want.pageId && want.pageId !== focusedBlockId) return;
    const thread = comments.threads.find((th) => th.id === want.threadId);
    if (thread) showThread(thread); else threadGone();
  }, [comments.loaded, comments.threads, focusedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps
  // The address of a thread, for its comment's Copy link: the share's in a
  // share view, else this workspace's.
  function copyThreadLink(comment) {
    const link = threadLink(window.location.origin,
      { pageId: focusedBlockId, blockId: threadPop?.blockId || "", threadId: comment.thread_id },
      shareMode ? { share: initialShare } : {});
    copyText(shareMode ? link : withWorkspace(link));
    setStatus(t("Link to the comment copied."));
  }
  // The labels a comment's [[links]] show: the page's own blocks, else the
  // reference cache.
  function commentLabels(body) {
    const out = {};
    for (const m of String(body || "").matchAll(REF_RE)) {
      const rb = treeById.get(m[1]) || refCache[m[1]];
      if (rb) out[m[1]] = refLabelOf(rb);
    }
    return out;
  }
  // A comment's [[link]] to a block outside this page: its label is fetched
  // once into the reference cache when the threads arrive, as a note's is
  // (BlockTree's onFetchRefs), so the chip names the page, not its id.
  useEffect(() => {
    const unknown = new Set();
    for (const th of comments.threads || []) {
      for (const c of th.comments || []) {
        for (const m of String(c.body || "").matchAll(REF_RE)) {
          if (!treeById.get(m[1]) && !refCache[m[1]]) unknown.add(m[1]);
        }
      }
    }
    if (unknown.size) onFetchRefs([...unknown]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [comments.threads]);
  // Set while the tree's transition effect hands a tree to the session: a
  // load there may apply held-back batches at once, inside React's commit.
  const committingRef = useRef(false);
  // Ops from another client (or a server-side writer): the page block's own
  // changes update the title/properties state, the rest apply to the tree
  // as a "remote" transition (no history entry; the base already has them,
  // so nothing of theirs is re-sent) and fold into every undo snapshot
  // (`before`: the session's base they were applied to; not for `own` ops,
  // ours put back on top of theirs — collabSession's `reassert`). The
  // transition commits at once (flushSync): its effect must diff against
  // the base before the next batch advances it — rendered later, a first
  // batch's tree would be diffed against a base already holding the
  // second, and the second batch's notes sent back as deletes (then
  // re-inserts).
  applyRemoteRef.current = (ops, pageId, pos, before, own = false) => {
    if (pageId !== focusedBlockId) return;
    for (const op of ops) {
      if (op.op !== "set" || op.id !== pageId) continue;
      if (op.content !== undefined) setPageTitle(op.content || t("Untitled"));
      setFocusedBlock((b) => {
        if (!b) return b;
        const props = op.props ? applyPatch(b.properties, op.props) : b.properties;
        if (op.props) {
          if ("folders" in op.props) setPageFolders(props.folders || []);
          if ("labels" in op.props) setPageLabels(props.labels || []);
          // moved into another page or out of one: the crumbs follow
          if ("parent_page" in op.props) rereadFamily();
          // A lookup the server ran (a metadata job, the extension's clip)
          if ("meta" in op.props) setPageMeta(props.meta || null);
          if ("bibtex" in op.props) setPageBibtex(props.bibtex || "");
          if ("ppt_cite" in op.props) setPptCite(props.ppt_cite || "");
          // The page's PDF replaced (attached, detached) in another tab or
          // by another person: the viewer follows, as openBlock shows it.
          // (by the address it is read from: a file the library takes in,
          // read in place before, is still the file the viewer shows)
          const was = pageDocument(b);
          const now = pageDocument({ properties: props });
          if ((was?.url || "") !== (now?.url || "")) {
            // a new version from elsewhere: a file pushed from disk, say
            if (was && now) guideEvents.emit("page.fileReplaced", { id: b.id });
            setInputUrl(now?.url || "");
            setPdfUrl(documentViewerUrl(now, { share: shareMode ? initialShare : "" }));
          }
        }
        return { ...b, ...(op.content !== undefined ? { content: op.content } : {}), properties: props };
      });
    }
    const treeOps = ops.filter((op) => !(op.op === "set" && op.id === pageId));
    if (!treeOps.length) return;
    // A move that shifts the row of the open editor (it, an ancestor, or a
    // reorder of their siblings) remounts or detaches that editor: its blur
    // is not the person leaving it, and the caret goes back where it was.
    const editing = view.editingId;
    if (editing && displacedRow(blocks, treeOps, editing, pageId)) {
      const c = caretRef.current?.id === editing ? caretRef.current : null;
      keepEditorThrough(editing, c ? c.from : "end", c?.to);
    }
    const apply = () => setBlocks((prev) => {
      try {
        const next = applyOps(prev, treeOps, pageId, pos);
        // ops landing on a fetched tree not committed yet keep it a load
        if (next !== prev) treeOriginRef.current.set(next, treeOriginRef.current.get(prev) === "load" ? "load" : "remote");
        return next;
      } catch (err) {
        // A batch we can't apply (should not happen): resync from the server
        // rather than take the page down.
        console.error("remote ops failed to apply", err);
        queueMicrotask(() => loadBlocksForBlock(pageId));
        return prev;
      }
    });
    if (committingRef.current) apply();
    else flushSync(apply);
    if (own) return;
    try { blockHistory.rebase(treeOps, { pageId, pos, before }); } catch (err) { console.error("undo history rebase failed", err); }
  };
  // Send queued edits NOW — before anything replaces the block tree.
  function flushPendingSave() { collabRef.current.flush(); }
  useEffect(() => {
    if (!focusedBlockId) return;
    const origin = treeOriginRef.current.get(blocks);
    treeOriginRef.current.delete(blocks); // one transition's worth: an undo back to this tree is an edit
    const seed = seedBlockIdRef.current;
    if (origin === "load") {
      const known = seed ? blocks.filter((b) => b.id !== seed) : blocks;
      committingRef.current = true;
      try { collab.commit(known, { isLoad: true, seq: loadedSeqRef.current }); } finally { committingRef.current = false; }
      loadedSeqRef.current = null;
      return;
    }
    if (readOnly) return;
    // Another client's ops don't make the untouched placeholder ours to save.
    const idle = origin === "remote" && seed && blocks.some((b) => b.id === seed && !b.content && !b.children?.length);
    const ops = collab.commit(idle ? blocks.filter((b) => b.id !== seed) : blocks, { now: saveNowRef.current });
    saveNowRef.current = false;
    if (seed && ops.some((op) => op.op === "insert" && op.id === seed)) seedBlockIdRef.current = null;
  }, [blocks, readOnly]);
  // Our place on the page for the others: the focused row (an open editor
  // reports its exact selection through onCaret).
  useEffect(() => {
    if (!focusedBlockId) return;
    if (caretRef.current && caretRef.current.id === focusedId) return;
    collab.sendCursor({ block: focusedId || "" });
  }, [focusedId, focusedBlockId]);


  // A highlight's id is its block's: deleting it removes the block (and
  // its descendants). persistBlocks fires via autosave.
  function deleteHighlight(id) {
    if (readOnly || !findBlock(blocks, id)) return;
    setBlocks(removeBlockTree(blocks, id));
  }

  // A highlight's colour and how it marks its passage: `color` (as
  // stored) and `style` (shared/model/markStyle.js), either or both; its
  // place, quote and notes stay.
  function changeHighlightMark(id, { color, style } = {}) {
    if (readOnly || !findBlock(blocks, id)) return;
    setBlocks(updateBlockTree(blocks, id, (b) => {
      const props = color === undefined ? b.properties : { ...b.properties, color: normalizeColor(color) || DEFAULT_HIGHLIGHT };
      return { ...b, properties: style === undefined ? props : withStyle(props, style) };
    }));
  }

  // Gamma's own links (chat citations, copied page/block links) open in
  // place wherever they are rendered — the chat, a note, an embed card —
  // through GammaNavContext; `target` the place it opens at (pageTarget).
  // In a share view openBlock goes through goSharedPage, which opens only
  // the pages the share reaches.
  async function openPageLink(id, target) {
    if (target && id === focusedBlockId) pushNav();
    setPageTarget(target ? { ...target } : null);
    if (id !== focusedBlockId) await openBlock(id, { pushNav: true, link: true });
    if (target) { setPdfHidden(false); setPhonePanel(null); }
  }

  // A [[ref]] chip or a copied block link: scroll to it on this page, else
  // open the page that holds it (`pageId` when the caller knows it — the
  // chat's list of the agent's note changes, a backlink).
  async function openBlockLink(id, pageId) {
    function findBlock(list) {
      for (const b of list || []) {
        if (b.id === id) return b;
        const found = findBlock(b.children || []);
        if (found) return found;
      }
      return null;
    }
    if (findBlock(blocks)) {
      landOnBlock(id);
    } else {
      // block-ref click = link jump to another page, which Back returns from
      const rootId = pageId || await pageOfBlock(id);
      if (rootId && rootId !== id) await openBlock(rootId, { pushNav: true, link: true, block: id });
      else await openBlock(id, { pushNav: true, link: true });
    }
  }

  // The page a block is on: the ref cache's, else the server's. A card's
  // link is pressable before its label lands, and the block opened as a
  // page would land on none of it. "" when neither knows (the block itself
  // is then opened).
  async function pageOfBlock(id) {
    const cached = refCacheRef.current[id]?.page_root_id;
    if (cached) return cached;
    try {
      const data = await apiJson(`${API}/block-search?ids=${encodeURIComponent(id)}`);
      return data.blocks?.[0]?.page_root_id || "";
    } catch {
      return "";
    }
  }

  // `probe`: the ids among them a [[ref]] or ![[embed]] names (not a Gamma
  // link's, which may name another server). One the search doesn't return
  // is gone, and its 404 says whether it is in Recently deleted (`trashed`,
  // its page's trash entry): the chip and the card show it so
  // (`refLabelOf`, editor/refLabels.js) rather than the bare id or a card that never
  // loads. A share view never asks: a share learns nothing beyond its pages.
  async function onFetchRefs(ids, probe = ids) {
    try {
      const res = await fetch(withShare(`${API}/block-search?ids=${ids.join(",")}`));
      const data = await res.json();
      const found = new Set((data.blocks || []).map((b) => b.id));
      const gone = res.ok && !shareMode ? probe.filter((id) => !found.has(id)).slice(0, 20) : [];
      const answers = await Promise.all(gone.map((id) => apiJson(`${API}/blocks/${encodeURIComponent(id)}`).then(
        () => null,
        (err) => (err.status === 404 ? [id, err.data?.trashed ? { trashed: err.data.trashed } : { missing: true }] : null))));
      const goneEntries = answers.filter(Boolean);
      if (data.blocks?.length || goneEntries.length) {
        setRefCache((prev) => {
          const next = { ...prev };
          data.blocks?.forEach((b) => { next[b.id] = b; });
          goneEntries.forEach(([id, entry]) => { next[id] = entry; });
          return next;
        });
      }
    } catch (_) {}
  }

  function onCacheRef(id, blockData) {
    // Merge-write: embed in-place edits push updated content for an already
    // cached ref, so this must overwrite (while keeping fields the caller
    // didn't send, e.g. page_title).
    setRefCache((prev) => ({ ...prev, [id]: { ...(prev[id] || {}), ...blockData } }));
  }

  // Embed cards and [[ref]] chips show a cached copy of a block on another
  // page, fetched once (the rows fetch what isn't cached). Opening a page
  // fetches the copies it shows again, so a card shows what its source says
  // now, not what it said on an earlier visit — the source may have been
  // edited on its own page since.
  const refCacheRef = useRef(refCache);
  refCacheRef.current = refCache;
  function refreshRefs(tree) {
    const ids = new Set();
    const walk = (list) => {
      for (const b of list || []) {
        for (const [id, ref] of refIdsOf(b)) if (ref) ids.add(id);
        walk(b.children);
      }
    };
    walk(tree);
    const cached = [...ids].filter((id) => refCacheRef.current[id]);
    for (let i = 0; i < cached.length; i += 50) onFetchRefs(cached.slice(i, i + 50));
  }

  // The emphasis of a jump: a highlight pulses (PdfViewer's flashHighlightId),
  // an ink group is outlined by its layer (inkFlash).
  function triggerFlash(highlightId) {
    if (isInkGroup(findBlock(blocksRef.current, highlightId))) {
      setInkFlash({ id: highlightId, nonce: Date.now() });
      return;
    }
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    setFlashingId(null);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setFlashingId(highlightId);
        flashTimerRef.current = setTimeout(() => setFlashingId(null), 1200);
      });
    });
  }


  // The deep link / saved-session boot, once the workspace is known (a share
  // view needs none and boots at once).
  const bootedRef = useRef(false);
  useEffect(() => {
    if (!wsReady || bootedRef.current) return;
    bootedRef.current = true;
    if (initialShare) resolveShare(initialShare);
    else if (shareMode) return; // a page host's address that names no shared page: "not found" is up
    else if (initialBlockId) {
      (async () => {
        const rootId = await pageOfBlock(initialBlockId);
        if (rootId && rootId !== initialBlockId) openBlock(rootId, { link: true, block: initialBlockId });
        else openBlock(initialBlockId, { link: true });
      })();
    }
    else if (initialUrl) openPdf(initialUrl);
    else if (initialLabel || initialFolder) {
      // Stay on home page with the label/folder filter — don't restore session
    }
    else {
      // A share view's "Add to my library" sent this tab here to import its link.
      let shared = "";
      try { shared = sessionStorage.getItem(IMPORT_SHARE) || ""; sessionStorage.removeItem(IMPORT_SHARE); } catch {}
      if (shared) { importSharedPage(shared); return; }
      // Bare `/` — try restore last session
      const session = loadSession();
      if (session.focusedBlockId) {
        openBlock(session.focusedBlockId).catch(() => {
          clearSession();
          setFocusedBlockId("");
        });
      }
    }
  }, [wsReady]);

  // Restore only after the account and workspace scope is known.
  useEffect(() => {
    if (!wsReady || shareMode) return;
    const session = loadSession();
    if (session.pdfScale != null) setPdfScale(session.pdfScale);
    if (session.pdfHidden != null) setPdfHidden(session.pdfHidden);
    if (session.notesVisible != null) setNotesVisible(session.notesVisible);
  }, [wsReady, shareMode]);

  // Interface size: app.css scales text and control boxes together.
  useEffect(() => {
    document.documentElement.style.setProperty("--ui-scale", String(uiScale));
  }, [uiScale]);

  // Theme: System tracks the OS preference live; Light/Dark pin it. Every
  // colour changes in one frame, with no transition (app/theme.js). The
  // theme-color meta follows: installed as a home-screen app, the status bar
  // is painted with it, so it matches what sits under it (docs/dev/ipad.md).
  const phoneBareTopRef = useRef(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      applyTheme(theme === "system" ? (mq.matches ? "dark" : "light") : theme);
      paintStatusBar(phoneBareTopRef.current);
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);

  // Interface language (shared/i18n/i18n.js): a change reloads the page, so
  // every module evaluates its text again under the new catalog (main.jsx).
  // It first lands in the account's profile — the reloaded app pulls the
  // profile and the server wins, so the server must already hold the new
  // value — and the Settings pane the change was made on reopens after it.
  useEffect(() => {
    if (resolveLocale(language) === getLocale()) return undefined;
    let live = true;
    profileSync.flush().then(() => {
      if (!live) return;
      // (the pref itself is already in localStorage, where main.jsx reads it)
      try { if (settingsOpen) sessionStorage.setItem(REOPEN_SETTINGS_KEY, settingsOpen); } catch {}
      window.location.reload();
    });
    return () => { live = false; };
  }, [language]);

  // Record a "recently viewed" entry whenever a page is opened. sessionUser
  // in the deps re-fires it once login resolves — a page opened by direct URL
  // shows before the session check finishes, and the first run skips.
  useEffect(() => {
    if (!focusedBlockId || shareMode || !prefsUserRef.current) return;
    pushRecentView(focusedBlockId);
  }, [focusedBlockId, shareMode, sessionUser]);

  // Keep the tab strip in sync with the open page.
  useEffect(() => {
    if (!focusedBlockId || shareMode || !prefsUserRef.current) return;
    const title = (pageTitle || t("Untitled")).slice(0, 60);
    const opened = !openTabs.some((tab) => tab.id === focusedBlockId);
    updateTabs((prev) => {
      const existing = prev.find((t) => t.id === focusedBlockId);
      if (existing && existing.title === title) return prev;
      if (existing) return prev.map((t) => (t.id === focusedBlockId ? { ...t, title } : t));
      return [...prev, { id: focusedBlockId, title }];
    });
    // A page got a tab of its own (the guide's tabs hint counts them).
    if (opened) guideEvents.emit("tab.opened");
  }, [focusedBlockId, pageTitle, shareMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Persist session state on relevant changes (skip initial mount)
  const firstRenderRef = useRef(true);
  useEffect(() => {
    if (!wsReady || shareMode) return;
    if (firstRenderRef.current) {
      firstRenderRef.current = false;
      return;
    }
    saveSession({
      focusedBlockId: focusedBlockId || undefined,
      pdfScale,
      pdfHidden,
      notesVisible,
    });
  }, [focusedBlockId, pdfScale, pdfHidden, notesVisible]);

  // Synced per-paper read position, fed by the scroll tracker on every scroll
  // (a ref so the tracker effect needn't re-register). Guarded: only while
  // the viewer is really showing this page's document and no scroll restore
  // is in flight (the tracker reports page 1 mid-load, which must not
  // overwrite the entry).
  const recordScrollPageRef = useRef(null);
  const trackPauseReasonRef = useRef(""); // last logged pause reason (debug log)
  useEffect(() => {
    recordScrollPageRef.current = (n) => {
      const reason = shareMode ? "readonly"
        : !focusedBlockId ? "no-focused-block"
        : !pdfUrl ? "no-pdf-url"
        : pdfRenderedUrlRef.current !== pdfUrl ? "doc-not-rendered"
        : restoringForRef.current === focusedBlockId ? "exact-restore-inflight"
        : coarseRestorePendingRef.current ? "coarse-restore-pending"
        : "";
      if (reason !== trackPauseReasonRef.current) {
        trackPauseReasonRef.current = reason;
        dbg(reason ? `tracker paused (${reason}) at page ${n}` : `tracker recording from page ${n}`);
      }
      if (reason) return;
      recordReadPos(focusedBlockId, n);
      // Refresh this page's recents-card snapshot once the scroll settles.
      if (snapTimerRef.current) clearTimeout(snapTimerRef.current);
      const id = focusedBlockId;
      snapTimerRef.current = setTimeout(() => {
        snapTimerRef.current = null;
        captureSnapRef.current?.(id);
      }, 1200);
    };
  });

  // Snapshot capture for the recents cards — same guards as the tracker, but
  // re-checked at fire time via the ref (the timers outlive this closure).
  // Returns true only when a real capture was stored.
  const snapTimerRef = useRef(null);
  const captureSnapRef = useRef(null);
  useEffect(() => {
    captureSnapRef.current = (id) => {
      if (!recentThumbs) return false;
      if (shareMode || !id || id !== focusedBlockId || !pdfUrl || pdfHidden) return false;
      if (pdfRenderedUrlRef.current !== pdfUrl) return false;
      if (restoringForRef.current === focusedBlockId || coarseRestorePendingRef.current) return false;
      const img = captureViewerSnapshot(viewerWrapRef.current);
      if (img) savePageSnap(id, img);
      return !!img;
    };
  });
  // A page opened and left without scrolling still gets a cover: the restore
  // scrolls with the tracker paused, so poll a few times after the document
  // (re)renders until a capture sticks. The scroll-settle path above keeps it
  // fresh afterwards.
  useEffect(() => {
    if (!recentThumbs || !pdfUrl || pdfHidden || shareMode || !focusedBlockId) return;
    const id = focusedBlockId;
    let tries = 0, timer = null;
    const attempt = () => {
      timer = null;
      if (!captureSnapRef.current?.(id) && tries++ < 10) timer = setTimeout(attempt, 1500);
    };
    timer = setTimeout(attempt, 1500);
    return () => { if (timer) clearTimeout(timer); };
  }, [pdfUrl, pdfHidden, focusedBlockId, shareMode, recentThumbs]);

  // A refetch of the open page. This tab's unsaved edits are laid over the
  // fetched tree (collab.overlay): never lost from the screen, never taken
  // as saved; the view (open editor, folding) lives beside the tree and a
  // refetch leaves it alone. An answer for a page no longer open, or
  // overtaken by a newer refetch, is dropped (the token); a failed refetch
  // keeps the tree on screen and tries again, ever longer apart.
  const refreshRef = useRef({ token: 0, timer: null, tries: 0 });
  async function loadBlocksForBlock(blockId) {
    const r = refreshRef.current;
    const token = ++r.token;
    clearTimeout(r.timer);
    const current = () => token === r.token && blockId === focusedBlockIdRef.current;
    try {
      const data = await apiJson(`${API}/blocks/${blockId}/subtree`);
      if (!current()) return;
      const children = collabRef.current.overlay(blockId, data.block?.children || []);
      loadedSeqRef.current = data.seq ?? null;
      setBlocks(loaded(children));
      r.tries = 0;
      postPill("refresh", null);
    } catch (err) {
      if (!current()) return;
      collabRef.current.reloadFailed(blockId);
      if (err?.status === 404 && err.data?.trashed) {
        // Moved to Recently deleted: the page stays on screen with its
        // unsaved edits, which wait for a restore (the notice says so).
        collabRef.current.gone(blockId, err.data.trashed);
        postPill("refresh", null);
        return;
      }
      const again = retryableStatus(err?.status || 0);
      postPill("refresh", {
        msg: again ? t("Couldn't refresh the page — retrying…") : t("Couldn't refresh the page: {message}", { message: err.message }),
        error: true,
      });
      if (again) {
        r.timer = setTimeout(() => { if (current()) loadBlocksForBlock(blockId); },
          Math.min(2000 * 2 ** r.tries++, 30000));
      }
    }
  }
  // Drop a pending refetch (and its retry) of the page being left.
  function cancelRefresh() {
    const r = refreshRef.current;
    r.token++;
    clearTimeout(r.timer);
    r.tries = 0;
    postPill("refresh", null);
  }

  // --- The AI agent's live footprint on the open page -----------------------
  // ChatDock forwards every agent stream event as it arrives (not just at the
  // end): a tool `action` (read/edit/create/move chips — the block it names
  // lights up, an applied edit reloads the tree so the change shows while
  // the agent carries on), a `progress` preview of an edit_block/create_block
  // call still being written (the block types it in), and `done`.
  function markAiBlock(id, kind, ttl) {
    const n = ++aiMarkSeqRef.current;
    setAiMarks((prev) => { const m = new Map(prev); m.set(id, { kind, n }); return m; });
    const timers = aiMarkTimersRef.current;
    clearTimeout(timers.get(id));
    timers.set(id, setTimeout(() => {
      timers.delete(id);
      setAiMarks((prev) => { if (!prev.has(id)) return prev; const m = new Map(prev); m.delete(id); return m; });
    }, ttl));
  }
  function handleAgentEvent(ev) {
    // Replies stream per conversation: another page's finishing reply must
    // not drop the preview this page's own reply is still writing.
    if (ev.type === "done") { if (ev.key === focusedBlockId) setAiLive(null); return; }
    if (!focusedBlockId) return;
    const inTree = (id) => !!id && (id === focusedBlockId || flattenBlocks(blocksRef.current).some((b) => b.id === id));
    if (ev.type === "progress") {
      // Only blocks of THIS page can be previewed: an edit targets a block in
      // the tree, a create a parent in it (the page id = a top-level block).
      if (!inTree(ev.tool === "edit_block" ? ev.block_id : ev.parent_id)) return;
      setAiLive({ tool: ev.tool, blockId: ev.block_id, parentId: ev.parent_id, afterId: ev.after_id, mode: ev.mode || "replace", find: ev.find, at: ev.at, content: ev.content || "" });
      return;
    }
    const a = ev.action;
    if (!a || a.error) return;
    if (a.page_id !== focusedBlockId && a.src_page_id !== focusedBlockId) return;
    if (a.kind === "ink") {
      if (a.block_id) markAiBlock(a.block_id, "read", 2500);
      return;
    }
    if (a.kind === "read") {
      if (a.block_id && a.block_id !== focusedBlockId) markAiBlock(a.block_id, "read", 2500);
      else {
        // Whole page read: sweep every row, top to bottom.
        const order = new Map(flattenBlocks(blocksRef.current).map((b, i) => [b.id, i]));
        setAiScan((prev) => ({ n: (prev?.n || 0) + 1, order }));
        clearTimeout(aiMarkTimersRef.current.get("__scan"));
        aiMarkTimersRef.current.set("__scan", setTimeout(() => setAiScan(null), 3200));
      }
      return;
    }
    if (!BLOCK_TOOLS.includes(a.tool)) return;
    // The edit landed: drop its preview, mark the block, and show the real
    // change. With the page live over its socket it has already arrived as
    // ops like any other client's; otherwise refetch (the open editor
    // survives the swap). Over the op-log poll (me.via "poll") it would land
    // only with the next read, after reveal() looked for the block.
    setAiLive((live) => (live && (live.tool === "create_block" || live.blockId === a.block_id) ? null : live));
    if (a.block_id) markAiBlock(a.block_id, a.kind === "create" ? "create" : a.kind === "move" ? "move" : "edit", 6000);
    const reveal = () => {
      if (!a.block_id) return;
      requestAnimationFrame(() => {
        document.querySelector(`[data-block-id="${a.block_id}"]`)
          ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    };
    const me = collabRef.current.me;
    if (me.connected && me.via === "socket") { reveal(); return; }
    loadBlocksForBlock(focusedBlockId).then(reveal);
  }
  const agentEventRef = useRef(null);
  agentEventRef.current = handleAgentEvent;
  useEffect(() => {
    // Page switch: the marks belong to the old page.
    setAiMarks(new Map());
    setAiLive(null);
    setAiScan(null);
    for (const t of aiMarkTimersRef.current.values()) clearTimeout(t);
    aiMarkTimersRef.current.clear();
  }, [focusedBlockId]);

  // The page carrying this document, created if the library has none
  // (lookup BY attachment — the dedup path for ingest, docs/dev/block_centric.md).
  async function getOrCreateBlockForDoc(src) {
    if (!src?.doc_id) throw new Error("docId required");
    return await apiJson(`${API}/blocks/by-doc/${src.doc_id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        default_title: defaultPageTitle({ url: src.source_url, name: src.original_filename }),
        source_url: src.source_url || null,
        original_filename: src.original_filename || null,
      })
    });
  }

  // ONE way to bring a PDF in — for a new page (openPdf / uploadFiles) or
  // an existing one (attachPdfToPage): upload the file, or resolve the URL
  // (arXiv / DOI / publisher page → direct link) and hash it into a doc_id,
  // checking that a link the library does not know yet really serves a PDF
  // before any page is bound to it (a dead link must not leave an empty page
  // behind; a document already in the library stays openable even once its
  // source has gone). Returns the attachment properties plus the viewer URL.
  async function resolvePdfSource({ file, url }, taskId) {
    if (file) {
      const filename = uploadLeafName(file, "upload.pdf");
      // One request, or in parts past PART_BYTES (shared/lib/uploadParts.js);
      // XHRs either way, so the task row gets byte progress and a stop button.
      const data = await uploadPdf(file, filename, {
        onProgress: (loaded, total) => updateTask(taskId, {
          info: `${fmtBytes(loaded)} / ${fmtBytes(total)}`, fraction: total ? loaded / total : undefined,
        }),
        onAbortable: (abort) => updateTask(taskId, { cancel: abort }),
      });
      return { doc_id: data.doc_id, source_url: data.source_url, original_filename: filename,
        viewerUrl: data.source_url, note: "" };
    }
    // Uploaded PDFs are already hosted locally — no external resolve or proxy.
    if (url.startsWith(`${API}/uploads/`)) {
      const own = parseUploadUrl(url);
      const doc_id = own?.ext === ".pdf" ? own.stem : await getDocIdForUrl(url);
      return { doc_id, source_url: url, original_filename: "", viewerUrl: uploadUrl(storedName(doc_id, ".pdf")), note: "" };
    }
    const resolved = await resolvePdfUrl(url, oaFallback);
    const source_url = resolved.source_url;
    const doc_id = await getDocIdForUrl(source_url);
    const known = await apiJson(`${API}/blocks/by-doc/${doc_id}`).catch(() => null);
    if (!known) {
      updateTask(taskId, { info: t("checking link…") });
      await probePdfUrl(source_url);
    }
    const viewerUrl = pdfProxyUrl(source_url, { save: pdfSaveLocal });
    transferByUrlRef.current[viewerUrl] = taskId; // the viewer's byte-level reporting takes over this row
    return { doc_id, source_url, original_filename: "", viewerUrl, note: resolved.note || "" };
  }

  async function renameTitle(newTitle) {
    if (readOnly || !focusedBlockId) return;
    const trimmed = (newTitle || "").trim();
    const finalTitle = trimmed || defaultPageTitle(doc);
    setPageTitle(finalTitle);
    setFocusedBlock((b) => b ? { ...b, content: finalTitle } : b);
    try {
      await apiJson(`${API}/blocks/${focusedBlockId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: finalTitle })
      });
      setHomeBlocks((prev) => prev.map((b) => b.id === focusedBlockId ? { ...b, content: finalTitle } : b));
      setStatus(t("Renamed to \"{finalTitle}\"", { finalTitle: finalTitle }));
    } catch (err) {
      setStatus(t("Rename failed: {message}", { message: err.message }));
    }
  }

  // Everything queued for the page is on the server when this resolves.
  async function persistBlocks() {
    if (readOnly || !focusedBlockId) return;
    await collab.flush();
  }

  // `folder`: the id of the folder the page is filed in ("" = none).
  async function uploadOnePdf(file, folder = "") {
    const transferId = addTask({ name: uploadLeafName(file, "upload.pdf"), kind: "upload", info: fmtBytes(file.size) });
    let src;
    try {
      src = await resolvePdfSource({ file }, transferId);
    } catch (err) {
      updateTask(transferId, { state: "failed", info: "failed" });
      throw err;
    }
    updateTask(transferId, { state: "done", info: fmtBytes(file.size) });
    const block = await getOrCreateBlockForDoc(src);
    if (folder) {
      // A PDF already in the library on a sub-page is filed where its top
      // page is, so the top goes into the folder (as a clip does).
      const top = topPageOf(pagesById, block.id);
      const own = !top || top.id === block.id;
      const folders = own ? filedIn(libTreeRef.current.folders, block.properties?.folders) : top._folders;
      if (!folders.includes(folder)) {
        const next = addToFolder(libTreeRef.current, folders, folder);
        try {
          await filePage(own ? block.id : top.id, { folders: next });
          if (own) block.properties = { ...block.properties, folders: next };
        } catch {}
      }
    }
    // If the file carries embedded annotations (SumatraPDF etc.), pull them in
    importEmbeddedAnnots(block.id, src.doc_id, true);
    return { src, block };
  }

  async function importOneMarkdown(file, folder = "") {
    const filename = uploadLeafName(file, "note.md");
    const transferId = addTask({ name: filename, kind: "file", info: fmtBytes(file.size) });
    const form = new FormData();
    form.append("file", file, filename);
    form.append("folder", folder);
    try {
      const data = await apiJson(`${API}/import/markdown`, { method: "POST", body: form });
      updateTask(transferId, { state: "done", info: `${data.imported || 0} notes` });
      return { data, kind: "markdown" };
    } catch (err) {
      updateTask(transferId, { state: "failed", info: "failed" });
      throw err;
    }
  }

  // A file of any other format as a library page of its own
  // (transfers/documentUploads.js uploadOneFile: the upload, then by-file,
  // which answers the page that already owns the file when one does).
  async function uploadOneDocument(file, folder = "") {
    const name = uploadLeafName(file);
    const transferId = addTask({ name, kind: "upload", info: fmtBytes(file.size) });
    try {
      const { page } = await uploadOneFile(file, {
        folderId: folder, name,
        onProgress: (loaded, total) => updateTask(transferId, {
          info: `${fmtBytes(loaded)} / ${fmtBytes(total)}`, fraction: total ? loaded / total : undefined,
        }),
        onAbortable: (abort) => updateTask(transferId, { cancel: abort }),
      });
      updateTask(transferId, { state: "done", info: fmtBytes(file.size) });
      return page;
    } catch (err) {
      updateTask(transferId, { state: "failed", info: "failed" });
      throw err;
    }
  }

  // A dropped or picked directory: hidden, temporary and unstorable files
  // are left out (transfers/fileRouting.js dropFilter), and
  // past ASK_PAST_FILES files a question comes first (the hosted page cap
  // counts every page made).
  function uploadFolder(found) {
    const { kept, skipped } = dropFilter(found);
    if (!kept.length) {
      setStatus(skipped.length ? t("Nothing to upload: the folder holds only hidden, temporary or unstorable files.") : t("The folder is empty."));
      return;
    }
    if (kept.length <= ASK_PAST_FILES) { uploadFiles(kept); return; }
    setConfirmBox({
      title: T("Upload a folder"),
      message: t("Upload {n} files to the library?", { n: kept.length }),
      confirmLabel: t("Upload"),
      onConfirm: () => uploadFiles(kept),
    });
  }

  async function uploadFiles(fileList, base = homeMode && folderFilter && !workFolder ? folderFilter : "") {
    if (shareMode) return;
    // A work folder open takes no upload: its files come from its directory.
    const besideWork = homeMode && !!workFolder && !base;
    // Accepts Files (picker/drop) or {file, folder} pairs (dropped-folder walk,
    // `folder` the directory names); the directory picker's Files carry the
    // path in webkitRelativePath instead. With a folder open in the library
    // view, uploads land inside it, their directories as folders below it;
    // `base` says otherwise (a file a native app was given goes to the top).
    // What each file becomes is transfers/fileRouting.js routeFile: a PDF
    // the PDF way, a Markdown file a note, any other a document page; a
    // file the server never stores (an executable) is left out.
    const items = Array.from(fileList || [])
      .map((it) => (it instanceof File ? { file: it, folder: folderFromRelPath(it.webkitRelativePath) } : it))
      .filter((it) => it?.file && routeFile(it.file))
      .map(({ file, folder }) => ({ file, filename: uploadLeafName(file), path: folder, route: routeFile(file) }));
    if (!items.length) {
      setStatus(t("Nothing to upload: files of these kinds cannot be stored."));
      return;
    }
    setLoading(true);
    const done = [];
    const failed = [];
    const stopped = []; // stopped from the tasks popover — not a failure
    try {
      // The directories' folders are made once, before any file lands.
      let folderOf;
      try {
        folderOf = await ensureFolders(items.map((it) => it.path).filter((path) => path.length), base);
      } catch (err) {
        setStatus(t("Upload failed: {failed}", { failed: err.message }));
        return;
      }
      for (const { file, filename, path, route } of items) {
        const folder = folderOf(path);
        setStatus(route === "markdown" ? t("Importing {filename}…", { filename }) : t("Uploading {filename}…", { filename }));
        try {
          done.push(route === "pdf" ? { ...(await uploadOnePdf(file, folder)), kind: "pdf" }
            : route === "markdown" ? await importOneMarkdown(file, folder)
              : { page: await uploadOneDocument(file, folder), kind: "file" });
        } catch (err) {
          if (err.aborted) stopped.push(filename);
          else failed.push(`${filename} (${err.message})`);
        }
      }
      const pdfDone = done.filter((item) => item.kind === "pdf");
      if (done.some((item) => item.kind !== "markdown")) refreshQuota();
      if (items.length === 1 && done.length && done[0].kind === "pdf") {
        // A single upload opens its page right away.
        const { src, block } = done[0];
        await openBlock(block.id, { viewerUrl: src.viewerUrl });
        setStatus(t("Uploaded {filename} ({doc_id})", { filename: items[0].filename, doc_id: src.doc_id }));
      } else if (items.length === 1 && done.length && done[0].kind === "file") {
        await fetchHomeBlocks();
        await openBlock(done[0].page.id, { pushNav: true });
        setStatus(t("Uploaded {filename}.", { filename: items[0].filename }));
      } else if (items.length === 1 && done.length) {
        await fetchHomeBlocks();
        await openBlock(done[0].data.block_id, { pushNav: true });
        setStatus(t("Imported {filename} as a note.", { filename: items[0].filename }));
      } else if (done.length) {
        fetchHomeBlocks();
        setStatus(failed.length
          ? t("Imported {n} of {n2} files — failed: {failed}", { n: done.length, n2: items.length, failed: failed.join(", ") })
          : t("Imported {n} files.", { n: done.length }));
      } else if (failed.length) {
        setStatus(t("Upload failed: {failed}", { failed: failed.join(", ") }));
      } else if (stopped.length) {
        setStatus(t("Upload stopped: {stopped}", { stopped: stopped.join(", ") }));
      }
      if (besideWork && done.length && !failed.length) setStatus(t("Uploaded to the library: a work folder's files come from its directory."));
      // Runs after the upload/import UI work and never delays its completion.
      queueMetadataForUploads(pdfDone);
    } finally {
      setLoading(false);
    }
  }

  async function importLogseq(files) {
    if (shareMode) return;
    const all = Array.from(files);
    const pdfFile = all.find((f) => f.name.endsWith('.pdf'));
    const ednFile = all.find((f) => f.name.endsWith('.edn'));
    const mdFile  = all.find((f) => f.name.endsWith('.md'));
    if (!pdfFile || !ednFile) {
      setStatus(t("Select at least a .pdf and .edn file."));
      return;
    }
    setLoading(true);
    setStatus(t("Importing {name}...", { name: pdfFile.name }));
    try {
      const form = new FormData();
      form.append("pdf", pdfFile);
      form.append("edn", ednFile);
      if (mdFile) form.append("md", mdFile);
      const resp = await fetch(`${API}/import/logseq`, { method: "POST", body: form, credentials: "include" });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      // The import endpoint already created the page; look it up and open it.
      const block = await getOrCreateBlockForDoc({ doc_id: data.doc_id, source_url: data.source_url, original_filename: pdfFile.name });
      await fetchHomeBlocks();
      await openBlock(block.id);
      setStatus(t("Imported {imported} highlights from {name}", { imported: data.imported, name: pdfFile.name }));
    } catch (err) {
      setStatus(t("Import failed: {message}", { message: err.message }));
    } finally {
      setLoading(false);
    }
  }

  // --- files a native app was given (transfers/openedFiles.js) --------------
  // The shell holds what the system gave it (a share, an Open in, AirDrop)
  // until this page takes it: once signed in to a workspace (the effect by
  // the import review's preload), and again whenever the shell says another
  // came. As the desktop does with a PDF it is handed (desktop/lib/
  // openFiles.js), files go to the workspace shown when the account can
  // write there, else to the personal one, and a workspace others see asks
  // first. Images alone go only to the workspace shown, onto its page in
  // view. Then each takes the path a picked file takes: a PDF or a Markdown
  // note becomes a page, an image a picture on the surface in view, a .note,
  // .ntb or .goodnotes opens the Notability & GoodNotes import's review, and
  // a .zip the Import dialog, which asks what export it is.
  const openedBusy = useRef(false);
  const openedAgain = useRef(false);
  async function takeOpenedFiles() {
    if (openedBusy.current) { openedAgain.current = true; return; }
    openedBusy.current = true;
    try {
      do {
        openedAgain.current = false;
        const held = await listOpenedFiles();
        if (held.length) await placeOpenedHeld(held);
      } while (openedAgain.current);
    } finally {
      openedBusy.current = false;
    }
  }

  // What the status says of opened images that found no surface in view.
  const noSurfaceFor = (list) => t("A picture goes on a PDF page or a notebook page: open one, then open {names} again.",
    { names: list.map((f) => f.name).join(", ") });

  async function placeOpenedHeld(held) {
    const named = (list) => list.length === 1 ? `“${list[0].name}”` : t("{n} files", { n: list.length });
    const what = named(held);
    const letGo = () => Promise.all(held.map((f) => doneWithOpenedFile(f.id)));
    const place = placeOpenedFiles(workspace, workspaces, held);
    if (!place) {
      await letGo();
      setStatus(t("Your account cannot add files here: {what} was not added.", { what }));
      return;
    }
    // Images alone go onto the surface in view only: with none, they are let
    // go before the question, whose answer could not place them.
    if (held.every((f) => openedKind(f) === "picture") && !pictureTarget()) {
      await letGo();
      setStatus(noSurfaceFor(held));
      return;
    }
    // The page reloads on the other workspace with the files still in the
    // shell, and the page there takes them; `note` says why, after the reload.
    // A picture among them finds no page in view there, and the status names
    // it then, so the note leaves it out.
    const goTo = (ws, note) => {
      try { if (note) sessionStorage.setItem(OPENED_NOTE, note); } catch {}
      switchWorkspace(ws.id);
    };
    if (place.switchTo) {
      const going = named(held.filter((f) => openedKind(f) !== "picture"));
      goTo(place.switchTo, t("{what} went into {workspace}: you can only read {readOnly}.",
        { what: going, workspace: place.switchTo.name, readOnly: workspace?.name || "" }));
      return;
    }
    if (place.audience) {
      const answer = await new Promise((resolve) => setConfirmBox({
        title: t("Add {what} to {workspace}?", { what, workspace: place.ws.name }),
        message: place.audience === "clone"
          ? t("{workspace} is a clone: what you add reaches the workspace it follows too.", { workspace: place.ws.name })
          : t("{workspace} is shared: everyone in it can see what you add.", { workspace: place.ws.name }),
        confirmLabel: t("Add to {workspace}", { workspace: place.ws.name }),
        onConfirm: () => resolve("here"),
        ...(place.mine ? { altLabel: t("Add to {workspace}", { workspace: place.mine.name }), onAlt: () => resolve("mine") } : {}),
        onCancel: () => resolve("cancel"),
      }));
      if (answer === "cancel") {
        await letGo();
        setStatus(t("Not added: {what}", { what }));
        return;
      }
      if (answer === "mine") {
        goTo(place.mine, "");
        return;
      }
    }
    await openOpenedHeld(held);
  }

  // Read every held file, let the shell's copy go, then open each the way a
  // picked one opens. The images go first, onto the surface in view as the
  // Picture button's do, since a page the upload opens would stop their
  // placing; with no surface in view they are named in the status. One
  // import dialog at a time: the note apps' review (their files together,
  // either app's) wins over a .zip's, and what none takes is named in the
  // status.
  async function openOpenedHeld(held) {
    const files = [];
    const failed = [];
    for (const entry of held) {
      try { files.push(await readOpenedFile(entry)); }
      catch (err) { failed.push(`${entry.name} (${err.message})`); }
      await doneWithOpenedFile(entry.id);
    }
    const pages = files.filter((f) => openedKind(f) === "page");
    const images = files.filter((f) => openedKind(f) === "picture");
    const notes = files.filter((f) => openedKind(f) === "noteapps");
    const archives = files.filter((f) => openedKind(f) === "archive");
    const unknown = files.filter((f) => !openedKind(f)).map((f) => f.name);
    const reviewed = notes.length ? { source: "noteapps", files: notes } : null;
    const later = reviewed ? archives : archives.slice(1);
    const unplaced = images.length && !(await pictures.placeInView(images)) ? images : [];
    if (pages.length) await uploadFiles(pages, "");
    if (reviewed) {
      const { source, files: picked } = reviewed;
      const file = picked.length === 1 ? picked[0] : filesUpload(picked, t(importSource(source).label));
      setImportReview({ source, file, folder: "" });
    } else if (archives.length) {
      setImportOpen({ file: archives[0] });
    }
    let note = "";
    try { note = sessionStorage.getItem(OPENED_NOTE) || ""; sessionStorage.removeItem(OPENED_NOTE); } catch {}
    const problems = [
      ...(unknown.length ? [t("Gamma does not open {names}.", { names: unknown.join(", ") })] : []),
      ...(unplaced.length ? [noSurfaceFor(unplaced)] : []),
      ...(later.length ? [t("Open {names} again once this import is done.", { names: later.map((f) => f.name).join(", ") })] : []),
      ...(failed.length ? [t("Could not read {names}.", { names: failed.join(", ") })] : []),
    ];
    const text = [note, ...problems].filter(Boolean).join(" ");
    if (note) {
      // Why the files are here, after the reload: it holds a channel of its
      // own for 8 s, as the pens question does, so the upload's statuses and
      // its metadata lookup's, ending a moment later, never take it down.
      logSys(text);
      postPill("opened", { msg: text, question: true }, { after: [8000, null] });
    } else if (problems.length) setStatus(text);
  }
  const takeOpenedRef = useRef(takeOpenedFiles);
  takeOpenedRef.current = takeOpenedFiles;

  // Open a PDF by URL: resolve it, find or create its page, open that page.
  async function openPdf(sourceUrl) {
    if (!sourceUrl || shareMode) return;
    // Prefer the saved copy, including its notes and locally cached PDF.
    // Re-resolving an arXiv link needlessly depends on the external source.
    const existingId = findPageForUrl(sourceUrl, homeBlocks.filter((b) => isPdf(pageDocument(b))));
    if (existingId) {
      await openBlock(existingId);
      return;
    }
    setLoading(true);
    setStatus(t("Opening PDF..."));
    // Visible from the moment Enter is pressed — resolve can take seconds.
    const taskId = addTask({ name: sourceUrl.slice(0, 60), kind: "download", info: t("resolving…") });
    try {
      const src = await resolvePdfSource({ url: sourceUrl }, taskId);
      const block = await getOrCreateBlockForDoc(src);
      updateTask(taskId, {
        name: (block.content || t("Untitled")).slice(0, 60),
        ...(src.viewerUrl.startsWith(`${API}/uploads/`) ? { state: "done", info: t("local file") } : { info: t("downloading…") }),
      });
      await openBlock(block.id, { viewerUrl: src.viewerUrl });
      setStatus(src.note || `Loaded ${src.doc_id}`);
      guideEvents.emit("paper.fetched");
    } catch (err) {
      updateTask(taskId, { state: "failed", info: (err.message || "failed") });
      setStatus(t("Open failed: {message}", { message: err.message }));
    } finally {
      setLoading(false);
    }
  }

  // "New page": a blank page in the open folder (if any), opened with its
  // title ready to type — or, given a title (Ctrl+P's "Create page"), opened
  // under it. What the page carries (a PDF…) is attached on the page itself
  // afterwards — creation never needs a file.
  async function createPage(folder = folderFilter, title = "") {
    if (shareMode) return;
    setOpenPopover(null);
    try {
      const created = await apiJson(`${API}/pages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, ...(folder ? { folders: [folder] } : {}) }),
      });
      if (title) {
        await fetchHomeBlocks();
        await openBlock(created.id, { pushNav: true });
        return;
      }
      await openNewPage(created.id);
    } catch (err) {
      setStatus(t("Create failed: {err}", { err: err.message || err }));
    }
  }
  // A page just made, opened Notion-style with its title ready to type;
  // Back returns to where it was made from.
  async function openNewPage(id) {
    if (!shareMode || sharedFolder) await fetchHomeBlocks(); // (a page share lists no library)
    await openBlock(id, { pushNav: true, focusTitle: true });
    setTitleDraft("");
    setTitleEditing(true);
  }

  // "New page inside": a blank page inside page `parentId`, opened like a
  // New page; the parent's Pages inside lists it when you come back. An
  // edit share's link makes one inside a page it opens (POST /pages takes
  // its token).
  async function createSubPage(parentId) {
    if (shareMode && readOnly) return;
    try {
      const created = await apiJson(`${API}/pages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "", parent_page: parentId }),
      });
      await openNewPage(created.id);
    } catch (err) {
      setStatus(t("Create failed: {err}", { err: err.message || err }));
    }
  }

  // "/page" in a block's editor (Notion's): a new page made under `id`, the
  // id the [[link]] typed into block `fromId` already names, inside the
  // open page, then opened. A page that could not be made takes its link
  // back out of the block.
  async function createLinkedPage(id, fromId) {
    try {
      const created = await apiJson(`${API}/pages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title: "", parent_page: focusedBlockId }),
      });
      onCacheRef(id, { content: created.content, page_title: created.content });
    } catch (err) {
      const link = `[[${id}]]`;
      setBlocks((prev) => {
        const b = findBlock(prev, fromId);
        return b?.content?.includes(link) ? setBlockText(prev, fromId, b.content.replace(link, "")) : prev;
      });
      setStatus(t("Create failed: {err}", { err: err.message || err }));
      return;
    }
    await openNewPage(id);
  }

  // "New notebook": a page with one sheet of paper, opened in the notebook
  // view with the title ready to type like a new page. The sheet's id
  // follows from the page's (firstSheetId), as the iPad makes it too.
  async function createNotebook(folder = folderFilter) {
    if (shareMode) return;
    setOpenPopover(null);
    const json = { "Content-Type": "application/json" };
    try {
      const created = await apiJson(`${API}/pages`, { method: "POST", headers: json,
        body: JSON.stringify({ title: "", ...(folder ? { folders: [folder] } : {}) }) });
      const sheet = newSheet(firstSheetId(created.id), null);
      await apiJson(`${API}/pages/${created.id}/ops`, { method: "POST", headers: json, body: JSON.stringify({
        client: "", ops: [{ op: "insert", id: sheet.id, parent: created.id, position: generateKeyBetween(null, null),
          content: "", props: sheet.properties }] }) });
      setNotebookView(created.id, true);
      guideEvents.emit("sheet.created", { id: sheet.id });
      await openNewPage(created.id);
    } catch (err) {
      setStatus(t("Create failed: {err}", { err: err.message || err }));
    }
  }

  // Attach a document to the open page (one that carries none), or replace
  // the one it carries: a PDF through the same ingest as opening a new PDF,
  // any other file as an upload named to the server
  // (transfers/documentUploads.js attachFile), then bound to THIS page via
  // POST (attach) or PUT (replace) /pages/{id}/attachment — no new page is
  // created.
  const [attachUrl, setAttachUrl] = useState("");
  // The metadata popover under its header button. Fixed positioning so it
  // floats above the window stack instead of being clipped by the notes
  // window / drawn under the chat below it.
  const metaBtnRef = useRef(null);
  function openMetaPopover() {
    const opening = openPopover !== "meta";
    if (opening) {
      const r = metaBtnRef.current?.getBoundingClientRect();
      if (r) setMetaPopPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    }
    setOpenPopover(opening ? "meta" : null);
  }
  // `replace`: a new version of the page's document (a paper compiled again
  // from its TeX, a figure drawn again). The page keeps its title, notes and
  // highlights; the server keeps the page's format, and between two PDFs
  // its relocate-highlights job moves each highlight to where its text is
  // in the new file (docs/dev/highlight_relocation.md); the old file goes
  // unless another page uses it.
  async function attachPdfToPage({ file, url, replace = false }) {
    const pageId = focusedBlockId;
    if (!pageId || shareMode || notebook || (!file && !url) || (replace ? !doc || readOnly : doc)) return;
    if (focusedFile) { setStatus(refusalText("document")); return; }
    const route = file ? routeFile(file) : "pdf";
    if (!route) { setStatus(t("Files of this kind cannot be stored.")); return; }
    if (route === "markdown") { setStatus(t("A Markdown file becomes a note: upload it to the library instead.")); return; }
    setOpenPopover(null);
    setAttachUrl("");
    setLoading(true);
    const taskId = addTask(file
      ? { name: uploadLeafName(file, "upload.pdf"), kind: "upload", info: fmtBytes(file.size) }
      : { name: url.slice(0, 60), kind: "download", info: t("resolving…") });
    try {
      let bound, src = null, viewerUrl = "", note = "";
      if (route === "pdf") {
        ({ viewerUrl, note, ...src } = await resolvePdfSource({ file, url }, taskId));
        bound = await apiJson(`${API}/pages/${encodeURIComponent(pageId)}/attachment`, {
          method: replace ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(src),
        });
      } else {
        bound = await attachFile(pageId, file, {
          replace, name: uploadLeafName(file),
          onProgress: (loaded, total) => updateTask(taskId, {
            info: `${fmtBytes(loaded)} / ${fmtBytes(total)}`, fraction: total ? loaded / total : undefined,
          }),
          onAbortable: (abort) => updateTask(taskId, { cancel: abort }),
        });
      }
      updateTask(taskId, { state: "done", info: replace ? t("replaced") : t("attached") });
      // A replacement on a page with highlights: the server finds each
      // one's text in the new file and moves it there (its job rides in the
      // answer; onJobFinished says what it found). A job that could not
      // start comes back as relocation_error, its reason.
      if (bound?.job) tasks.adopt(bound.job);
      if (replace) attemptedMetaRef.current.delete(pageId); // its lookup was the old file's
      if (file) {
        refreshQuota();
        if (src) importEmbeddedAnnots(pageId, src.doc_id, true);
      }
      fetchHomeBlocks();
      if (focusedBlockIdRef.current !== pageId) return; // navigated away meanwhile
      await openBlock(pageId, src ? { viewerUrl } : undefined);
      setPdfHidden(false);
      setStatus(note || (!replace ? (src ? t("PDF attached.") : t("File attached."))
        : bound?.relocation_error
          ? t("PDF replaced. Its highlights were not placed ({reason}): choose Find highlights in this PDF in the Document menu to place them.",
            { reason: t(bound.relocation_error) })
          : src ? t("PDF replaced.") : t("File replaced.")));
    } catch (err) {
      if (err.status === 409 && err.data?.page_id) {
        // The library already holds this file on another page — the page is
        // the unit, so open that one rather than duplicating the file.
        updateTask(taskId, { state: "done", info: t("already in library") });
        const title = pagesById.get(err.data.page_id)?.content || "";
        setStatus(title ? t("That file is already in the library as {title}", { title }) : t("That file is already in the library — opening it."));
        setLoading(false);
        openBlock(err.data.page_id, { pushNav: true });
        return;
      }
      if (isZoteroPdfComing(err)) {
        // The page waits for the PDF its Zotero item holds (this view had not
        // heard yet): the page is read again, so its card shows in the
        // paperclip's place.
        updateTask(taskId, { state: "done", info: t("in Zotero") });
        setStatus(t(ZOTERO_PDF_COMING));
        setLoading(false);
        if (focusedBlockIdRef.current === pageId) openBlock(pageId);
        return;
      }
      updateTask(taskId, { state: "failed", info: (err.message || "failed") });
      setStatus(replace
        ? t("Replace failed: {message}", { message: err.message })
        : t("Attach failed: {message}", { message: err.message }));
    } finally {
      setLoading(false);
    }
  }

  // Drop the page's document: the notes stay (highlights and regions keep
  // their positions as blocks); the file goes unless another page still
  // carries it.
  function detachPdfFromPage() {
    const pageId = focusedBlockId;
    if (!pageId || !doc || readOnly) return;
    setOpenPopover(null);
    setConfirmBox({
      title: T("Detach the file"),
      message: t("Remove \"{pageAttach}\" from this page? The notes and highlights stay as blocks; the file is deleted unless another page uses it. This can't be undone.", { pageAttach: doc.name || defaultPageTitle(doc) }),
      confirmLabel: t("Detach"),
      danger: true,
      onConfirm: async () => {
        try {
          await apiJson(`${API}/pages/${encodeURIComponent(pageId)}/attachment`, { method: "DELETE" });
          fetchHomeBlocks();
          if (focusedBlockIdRef.current === pageId) await openBlock(pageId);
          setStatus(t("File detached."));
        } catch (err) {
          setStatus(t("Detach failed: {message}", { message: err.message }));
        }
      },
    });
  }

  // "Add to library" on a file chip, filed in this page's first folder, then
  // opened (transfers/documentUploads.js promoteChip). A PDF: the file is
  // already stored under its hash, so this is the lookup-or-create BY
  // ATTACHMENT — a root page carrying it (the metadata fetch starts when the
  // page opens). A markdown file: a note page imported from the stored file
  // (a copy; the file stays). Any other: the library page owning that stored
  // file, made when none does. The chip on this page shows "open page" from
  // now on (rememberDocPage, by the stored name).
  async function promoteFile(hash, ext, name) {
    if (readOnly || shareMode || !hash) return;
    try {
      // the open page's first library folder: a work folder's directories are the disk's
      const folder = filedIn(libTree.folders, pageFolders).find((id) => !workOf(workTree, id)) || "";
      const page = await promoteChip({ hash, ext }, { name: name || "", folderId: folder });
      rememberDocPage(storedName(hash, `.${ext}`), { id: page.id, title: page.content });
      fetchHomeBlocks();
      await openBlock(page.id, { pushNav: true });
    } catch (err) {
      setStatus(t("Could not add to library: {message}", { message: err.message }));
    }
  }
  // The file chips reach App through a context: navigation and promotion
  // need openBlock and the page's folder. The value stays identity-stable
  // (the ref) so the memoized markdown never re-renders for it.
  const fileChipActionsRef = useRef({});
  fileChipActionsRef.current = { openBlock, promoteFile };
  const fileChipCtx = useMemo(() => ({
    readOnly,
    canOpen: !shareMode,
    openPage: (id) => fileChipActionsRef.current.openBlock(id, { pushNav: true }),
    promoteFile: (hash, ext, name) => fileChipActionsRef.current.promoteFile(hash, ext, name),
  }), [readOnly, shareMode]);

  // Every upload a drop or paste makes (fileChip.postFile) lands in the
  // background-tasks list, and in the status pill with a percentage once it
  // has run for a moment — a screenshot flashes by, a 200 MB dataset shows
  // its progress.
  const uploadPillRef = useRef({ shown: 0 });
  useEffect(() => {
    const pill = uploadPillRef.current;
    const showPill = (msg) => { pill.shown++; postPill("upload", { msg, spinner: true }); };
    const dropPill = () => { if (pill.shown) { pill.shown = 0; postPill("upload", null); } };
    setUploadReporter({
      start: (file, abort) => ({
        tid: addTask({ name: uploadLeafName(file, "file"), kind: "upload", info: fmtBytes(file.size), cancel: abort }),
        name: uploadLeafName(file, "file"), size: file.size, at: Date.now(), lastPct: -1,
      }),
      progress: (u, loaded, total) => {
        if (!u) return;
        const pct = total ? Math.min(99, Math.floor((loaded / total) * 100)) : 0;
        if (pct === u.lastPct) return;
        u.lastPct = pct;
        updateTask(u.tid, { info: `${fmtBytes(loaded)} / ${fmtBytes(total)} — ${pct}%`, fraction: total ? loaded / total : undefined });
        if (Date.now() - u.at > 400 || total > 2 * 1024 * 1024) showPill(`Uploading ${u.name}… ${pct}%`);
      },
      done: (u, ok, detail) => {
        if (!u) return;
        updateTask(u.tid, ok ? { state: "done", info: fmtBytes(u.size) } : { state: "failed", info: detail || "failed" });
        dropPill();
        if (!ok && !tasks.wasStopped(u.tid)) setStatus(t("Upload of {name} failed: {refused}", { name: u.name, refused: detail || "refused" }));
        else if (Date.now() - u.at > 400) setStatus(t("Uploaded {name}.", { name: u.name }));
      },
    });
    return () => setUploadReporter(null);
  }, []);

  // Files dropped on the open page outside any block row: one new block per
  // file at the end of the page (images inline, the rest as file chips). The
  // rows take drops on themselves; the page's DOCUMENT comes from the header.
  async function appendFileBlocks(files) {
    const pageId = focusedBlockId;
    const lines = await uploadFilesAsLines(files);
    if (focusedBlockIdRef.current !== pageId) return; // navigated away meanwhile
    if (!lines.length) { setStatus(t("Nothing added — the upload was refused.")); return; }
    setBlocks((prev) => {
      let out = prev;
      for (const line of lines) {
        const { blocks: next, newId } = addRootBlock(out);
        out = updateBlockTree(next, newId, (b) => ({ ...b, content: line }));
      }
      return out;
    });
    refreshQuota();
    setStatus(t("Added {n} file{_s}.", { n: lines.length, _s: lines.length === 1 ? "" : "s" }));
  }

  async function resolveShare(token) {
    setLoading(true);
    setStatus(t("Resolving share link..."));
    try {
      // Resolved by hand: the status code says whether signing in would help
      // (401 → login gate, 403 → not on the list, 404 → gone).
      const res = await fetch(`${API}/share/${encodeURIComponent(token)}`, { credentials: "include" });
      if (!res.ok) {
        if (res.status !== 401) {
          // Name the account that was refused so the message makes sense;
          // a dead link offers sign-in only to a visitor who isn't.
          try {
            const sess = await (await fetch(`${API}/session`, { credentials: "include" })).json();
            setShareInfo({ viewer: sess?.user || "" });
          } catch {}
        }
        setShareGate(res.status === 401 ? "login" : res.status === 403 ? "forbidden" : "missing");
        setStatus("");
        return;
      }
      const data = await res.json();
      // a Gamma Cloud sign-in started from the open link came back refused,
      // or asking to join: its sign-in page says so, with the way back
      setShareGate(cloudOutcomeInUrl() ? "login" : null);
      setShareInfo({ owner: data.username || "", role: data.role || "view", canEdit: Boolean(data.can_edit),
                     audience: data.audience || "anyone", viewer: data.viewer || "",
                     viewerIsGuest: Boolean(data.viewer_is_guest), workspace: data.workspace_id || "",
                     page: data.page_id || "", deleting: Boolean(data.deleting) });
      if (!data.viewer || data.viewer_is_guest) {
        // No account behind this visitor: a per-browser display name labels
        // their presence and edits (X-Gamma-Name / the socket's ?name=).
        const name = loadLinkName();
        setLinkName(name);
        setLinkNameState(name);
      }

      if (data.folder) {
        // A folder share: the home library confined to that folder (the
        // listing the token may read), or — with `page=` in the URL — one
        // of its pages; goSharedPage keeps the two in the history.
        setSharedFolder({ id: data.folder, name: data.folder_name || "" });
        setReadOnly(!data.can_edit);
        // (a folder= the shared tree lacks goes back to the shared folder
        // once the listing is in)
        setFolderFilter(initialFolder || data.folder);
        const pages = await fetchHomeBlocks();
        const want = [initialPageId, initialBlockId].find((id) => id && pages.some((b) => b.id === id));
        if (want) {
          await openSharedPage(token, want, data);
        } else if (data.deleting) {
          sayDeleting();
        } else {
          setStatus(t("Loaded shared folder."));
        }
        return;
      }
      // A page share reaches the pages inside its page too: `page=` names
      // one of them (goSharedPage), else the shared page opens.
      if (!(initialPageId && initialPageId !== data.page_id && await openSharedPage(token, initialPageId, data))) {
        await openSharedPage(token, data.page_id, data);
      }
    } catch (err) {
      setStatus(t("Share open failed: {message}", { message: err.message }));
    } finally {
      setLoading(false);
    }
  }

  // Load one shared page into the share view — a page share's page, or a
  // page of a folder share; `share` is the resolved link ({can_edit,
  // username}). The share names a page block directly (PDF pages and note
  // pages alike). Read access rides on the token, which apiJson
  // appends to every API call in a share view (utils.withShare) — never a
  // bare ?user=. A page the share does not reach leaves the one on screen,
  // and the address, as they are; the answer says whether the page opened.
  // `push`: the open is a step of the share's navigation (goSharedPage), a
  // history entry made once the page is known to open.
  async function openSharedPage(token, pageId, share, { push = false } = {}) {
    let block = null;
    try { block = await apiJson(`${API}/blocks/${encodeURIComponent(pageId)}`); } catch {}
    if (!block) {
      setStatus(t("Could not open that page."));
      return false;
    }
    if (focusedBlockId) leaveCurrentPage();
    // `share.page_id`: a page share's own page, whose address names no
    // `page=` (a folder share has none). On a page host the address bar
    // keeps the shared page's pretty address, its slug following the
    // title, and a page inside it rides along as `page=` (initialPageId).
    const own = pageId === share.page_id;
    const url = publicPage
      ? (own ? publicPath(block.content, pageId) : `${window.location.pathname}?page=${encodeURIComponent(pageId)}`)
      : `${window.location.pathname}?share=${encodeURIComponent(token)}${own ? "" : `&page=${encodeURIComponent(pageId)}`}`;
    if (push) window.history.pushState({ sharePage: pageId }, "", url);
    else if (publicPage) window.history.replaceState(window.history.state, "", url + window.location.hash);
    prefetchFamily(block.id);

    let childBlocks = [];
    try {
      const subtreeData = await apiJson(`${API}/blocks/${block.id}/subtree`);
      // with what this tab has not saved of it yet (back on a page just left)
      childBlocks = collabRef.current.overlay(block.id, subtreeData.block?.children || []);
      loadedSeqRef.current = subtreeData.seq ?? null;
    } catch {}

    const blockDoc = pageDocument(block);
    const proxiedUrl = isPdf(blockDoc) ? documentViewerUrl(blockDoc, { share: token }) : "";

    setFocusedBlockId(block.id);
    setFocusedBlock(block);
    rereadFamily();
    setView(EMPTY_VIEW);
    setPageTitle(block.content || defaultPageTitle(blockDoc));
    setBlocks(loaded(childBlocks));
    setInputUrl(blockDoc?.url || "");
    setPdfUrl(proxiedUrl);
    // Edit rights arrive with the share (none while its workspace is being
    // deleted, which the pill keeps saying); the autosave effect's suppress
    // flag (set above) swallows the first blocks change either way.
    setReadOnly(!share.can_edit);
    if (share.deleting) sayDeleting();
    else setStatus(share.can_edit ? t("Shared by {username} — your edits save to their page.", { username: share.username }) : t("Loaded shared page."));
    return true;
  }

  // A share view's navigation, each step a history entry beside the token;
  // popstate replays an entry without pushing. A folder share goes between
  // its library ("" — at `folder`, else where it was) and a page (`page=` /
  // `folder=`); a page share between its page ("") and the pages inside it
  // (`page=`, on a page host too, after the shared page's pretty address;
  // the id is kept in the entry's state). A page the share does not reach
  // makes no entry (openSharedPage). The ref keeps the once-registered
  // listener on the latest closure. A page's open answers whether it opened.
  function goSharedPage(pageId, { push = true, folder } = {}) {
    const sharedPage = sharedFolder ? "" : shareInfo?.page || "";
    const id = pageId || sharedPage;
    if (id) {
      setLoading(true);
      return openSharedPage(initialShare, id, { can_edit: !!shareInfo?.canEdit, deleting: !!shareInfo?.deleting,
                                                username: shareInfo?.owner || "", page_id: sharedPage }, { push })
        .catch((err) => {
          setStatus(t("Share open failed: {message}", { message: err.message }));
          return false;
        })
        .finally(() => setLoading(false));
    }
    if (!sharedFolder) return;
    const target = lib.clamp(folder ?? folderFilter);
    if (push) window.history.pushState(null, "", homeUrlFor(target, ""));
    goHome(true, true);   // the library, refreshed — the same path as the home button
    openFolder(target);
  }
  const goSharedPageRef = useRef(goSharedPage);
  goSharedPageRef.current = goSharedPage;
  useEffect(() => {
    if (!shareMode) return undefined;
    const onPop = () => {
      const params = new URLSearchParams(window.location.search);
      const page = params.get("page") || window.history.state?.sharePage || "";
      goSharedPageRef.current(page, { push: false, folder: params.get("folder") || "" });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [shareMode]);

  // `opts.block`: a block of the page to land on, scrolled to and focused
  // (a [[ref]], a search hit, a deep link); `opts.then`: what to do once the
  // page is on screen (openPageThen). Both belong to this open: staged as
  // its tree lands, never by an open a later one took over.
  async function openBlock(blockId, opts) {
    if (!blockId) return;
    // A link's place is its own page's: another page opened spends it.
    setPageTarget((target) => (target && target.pageId !== blockId ? null : target));
    if (shareMode) {
      if (opts?.block) pendingBlockScrollRef.current = pendingJumpRef.current = opts.block;
      // Back records the jump once the page has opened: one the share does
      // not reach leaves the page on screen, and no entry to come back by.
      const from = opts?.pushNav && blockId !== focusedBlockId ? navEntry() : null;
      if ((await goSharedPage(blockId)) && from) pushNav(from);
      return;
    }
    // Back records LINK jumps only — callers opt in via {pushNav: true}.
    // Plain navigation (library, search, tabs, home) never pushes.
    if (opts?.pushNav && blockId !== focusedBlockId) pushNav();
    leaveCurrentPage();
    forgetDocPages(); // the file chips' "which page carries this PDF" cache
    // A tab's pick stays for its own open (selectTab); another open drops it.
    setTabPick((pick) => (pick === blockId ? pick : ""));
    // The page's threads and its family are asked for alongside its tree,
    // and a PDF's comment margin with them, so none comes a round trip
    // after it. Only for a page the library lists: a link to one that is
    // gone asks for its tree alone.
    const listed = tabKinds.has(blockId);
    if (listed && blockId !== focusedBlockId) commentsRef.current.prefetch(blockId);
    if (listed) prefetchFamily(blockId);
    // The pane its document gets, and the margin the PDF's and a picture's
    // threads stand in, fetched as the page starts to open.
    const tabPane = viewerOf(tabKinds.get(blockId));
    if (VIEWERS[tabPane]?.margin) preloadCommentMargin();
    preloadViewer(tabKinds.get(blockId));
    // Until the tree lands the page on screen stays, and "Opening…" shows
    // only once the open has kept the reader waiting.
    const switching = !!focusedBlockId;
    const seq = ++openSeqRef.current;
    let slow = false;
    const opening = setTimeout(() => {
      if (openSeqRef.current !== seq) return;
      slow = true;
      postPill("opening", { msg: t("Opening..."), spinner: true });
    }, OPENING_PILL_MS);
    setLoading(true);
    try {
      const subtreeData = await apiJson(`${API}/blocks/${blockId}/subtree`);
      // A later open took over while this one waited (two tabs pressed in
      // quick succession): its page is the one wanted, whichever answers last.
      if (openSeqRef.current !== seq) return undefined;
      const block = subtreeData.block;
      if (!block) throw new Error("Block not found");
      // What this open was asked to land on and then do; a jump or an
      // action staged for an earlier page that never came is dropped. A
      // highlight to land on is its passage on the PDF too; an open without
      // a block keeps the jump its caller staged (a link region's).
      pendingBlockScrollRef.current = opts?.block || null;
      if (opts?.block) pendingJumpRef.current = opts.block;
      pendingPageActionRef.current = opts?.then ? { id: blockId, action: opts.then } : null;
      // A press on a view of the page (a tab group's), which made it live:
      // on a note, its editor opens with the caret where the press was.
      handedPressRef.current = opts?.edit?.clickPos ? { id: opts.edit.id, clickPos: opts.edit.clickPos } : null;
      const props = block.properties || {};
      // Back on a page left with edits still unsaved (their save may be
      // retrying): they are laid over the fetched tree, and the page's
      // session resumes (collabSession's commit).
      const childBlocks = collabRef.current.overlay(blockId, block.children || []);
      loadedSeqRef.current = subtreeData.seq ?? null;
      refreshRefs(childBlocks);

      setMissingPage(null);
      setFocusedBlockId(blockId);
      setFocusedBlock(block);
      rereadFamily();
      // The notes open folded as saved, or as a view of the page had them
      // folded (`opts.folds`, a tab group's press), with the note to edit
      // in sight.
      const openView = opts?.folds ? { ...EMPTY_VIEW, folds: opts.folds } : EMPTY_VIEW;
      setView(opts?.edit?.id ? revealBlock(childBlocks, openView, opts.edit.id) : openView);
      // The caret goes where the press mapped in the note's source; a press
      // that mapped nowhere leaves it where the editor puts it under the
      // pointer; none at all (a button's), at the note's end.
      if (opts?.edit?.id && !readOnly) {
        const at = opts.edit.clickPos;
        pendingFocusRef.current = { id: opts.edit.id, caret: at ? at.offset ?? undefined : "end", reopen: 1 };
      }
      setPageTitle(block.content || t("Untitled"));
      setPageLabels(props.labels || []);
      setPageFolders(props.folders || []);

      let openedPdfUrl = "";
      const blockDoc = pageDocument(block);
      // The pane the page's document gets (documents/viewers.js): only a
      // PDF has a viewer address; a page with a pane opens on its document
      // and seeds no note, since nothing waits for typing there.
      const pane = viewerOf(blockDoc?.format, blockDoc);
      if (isPdf(blockDoc)) {
        openedPdfUrl = opts?.viewerUrl || documentViewerUrl(blockDoc);
        setInputUrl(blockDoc.url);
        setPdfUrl(openedPdfUrl);
      } else {
        setInputUrl("");
        setPdfUrl("");
      }
      if (pane) {
        setBlocks(loaded(childBlocks));
        if (VIEWERS[pane]?.margin) preloadCommentMargin();
      } else if (childBlocks.length === 0 && !readOnly) {
        const seedId = makeId();
        seedBlockIdRef.current = seedId;
        // A page created from "New page" gets the title first (Notion-
        // style); the seed block waits for Enter there.
        if (!opts?.focusTitle) { pendingFocusRef.current = seedId; startEditing(seedId); }
        setBlocks(loaded([{ id: seedId, content: "", children: [], properties: {} }]));
      } else {
        setBlocks(loaded(childBlocks));
      }

      // The notes land where the reader left off or at the top — a passive
      // landing that focuses no row (see landingRef) — unless this open
      // lands on a block of its own (`opts.block`). A tab's page lands
      // exactly where it was left this session (notesPlaceRef); otherwise
      // a text-only page lands on its synced row.
      const has = (id) => !!id && flattenBlocks(childBlocks).some((b) => b.id === id);
      const kept = opts?.restoreScroll ? notesPlaceRef.current[blockId] : null;
      const notePos = !pane ? readPosRef.current[blockId] : null;
      landingRef.current = opts?.block ? null
        : kept && (!kept.id || has(kept.id)) ? { ...kept, kept: true }
          : { id: notePos?.page === 0 && has(notePos.block) ? notePos.block : "" };

      const newUrl = withWorkspace(`${window.location.pathname}?block=${encodeURIComponent(blockId)}`);
      window.history.replaceState({}, "", newUrl);
      // Pages remember their own window arrangement; pages without one
      // inherit whatever layout is currently on screen. With pages side by
      // side the windows are the groups' together and stay as they are
      // (docs/dev/tab_groups.md): only what belongs to the page itself, its
      // PDF shown or closed and its zoom, comes back with it.
      const savedUi = pageLayoutsRef.current[blockId];
      const sideBySide = !isPhone && !isSingle(tabLayoutRef.current);
      if (sideBySide) setPdfHidden(!!savedUi?.pdfHidden);
      else if (savedUi?.layout) {
        setLayout(savedUi.layout);
        setCollapsedWins(savedUi.collapsed || {});
        setPdfHidden(!!savedUi.pdfHidden);
        setChatHidden(!!savedUi.chatHidden);
        if (savedUi.notesVisible != null) setNotesVisible(savedUi.notesVisible);
        // Panel ratios apply once the restored window structure has
        // committed (a group's panel count must match its saved sizes).
        if (savedUi.sizes) {
          let tries = 0;
          const applySizes = () => {
            let pending = false;
            for (const [k, arr] of Object.entries(savedUi.sizes)) {
              const h = panelGroupRefs.current[k];
              if (!h || !Array.isArray(arr) || !arr.length) continue;
              try {
                const cur = h.getLayout();
                if (cur.length !== arr.length) { pending = true; continue; }
                if (cur.some((v, i) => Math.abs(v - arr[i]) > 0.5)) h.setLayout(arr);
              } catch { pending = true; }
            }
            if (pending && tries++ < 20) setTimeout(applySizes, 60);
          };
          requestAnimationFrame(applySizes);
        }
      }
      // Zoom travels with the page too. Applied before the PDF mounts, so a
      // numeric zoom is already in effect when the scroll restore runs — its
      // scale ratio is then exactly 1 and the position lands exactly. A
      // notebook without a zoom of its own fits its sheets, never taking on
      // the zoom the last PDF was read at.
      // A press on a view of the page brings the zoom the view showed.
      const zoom = opts?.zoom || savedUi?.pdfScale;
      if (zoom) setPdfScale(zoom);
      else if (!pane && sheetsOf(childBlocks).length) setPdfScale("page-width");
      if (opts?.restoreScroll) restorePdfScroll(tabScrollRef.current[blockId], blockId, openedPdfUrl);
      // A file with nothing written about it yet opens on its document
      // alone: the Notes window collapsed to its bar, its tail saying where
      // to start. A PDF keeps the notes open (its highlights land there), and
      // a page with its own arrangement, or side by side, keeps that.
      if (pane && pane !== "pdf" && !childBlocks.length && !savedUi && !sideBySide) setCollapsedWins((prev) => ({ ...prev, notes: true }));
      // "Ready." answers an "Opening…" the reader saw, for a page opened
      // where none was (the library, a link): a switch between pages is
      // answered by the page taking the other's place.
      if (slow && !switching) setStatus(t("Ready."));
      // Emit for every successful open, including reopening the same paper.
      guideEvents.emit("page.opened", { id: blockId, title: block.content || t("Untitled") });
      return openedPdfUrl;
    } catch (err) {
      if (openSeqRef.current !== seq) return undefined; // a later open took over
      if (err.status === 404 && opts?.link) {
        // Not a failure to report as one: the page is gone (or in Recently
        // deleted), or lives in another workspace. The notice says so; a dead
        // deep link leaves the address bar (a tab restore, which has no
        // `link`, just clears it).
        setMissingPage({ id: blockId, trashed: err.data?.trashed || null });
        const url = new URL(window.location.href);
        if (url.searchParams.get("block") === blockId || url.searchParams.get("page") === blockId) {
          url.searchParams.delete("block");
          url.searchParams.delete("page");
          window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
        }
        return undefined;
      }
      setStatus(t("Open failed: {message}", { message: err.message }));
      // If this was a session restore attempt that failed, clear it
      if (!window.location.search.includes("block=")) clearSession();
    } finally {
      clearTimeout(opening);
      // What the open on screen holds is the latest open's to let go of.
      if (openSeqRef.current === seq) {
        postPill("opening", null);
        setLoading(false);
        setTabPick((pick) => (pick === blockId ? "" : pick));
      }
    }
  }
  // A tab pressed: selected at once, its page opened behind it. The tab of
  // the page on screen, pressed while another's page opens, takes that
  // press back: the page stays as it is, not read again.
  function selectTab(id, target) {
    if (id === focusedBlockId) { dropTabPick(); return undefined; }
    setTabPick(id);
    const then = target?.then || (target?.title ? "title" : target?.labels ? "labels" : undefined);
    return openBlock(id, { restoreScroll: true, edit: target?.edit, folds: target?.folds, zoom: target?.zoom, then });
  }
  // The pending open of a picked tab let go: neither its landing nor its
  // finally runs, so the page on screen stays, its tab selected again.
  function dropTabPick() {
    if (!tabPick) return;
    ++openSeqRef.current;
    setTabPick("");
    postPill("opening", null);
    setLoading(false);
  }

  // --- Global back: one history for every navigation ------------------------
  // Entries capture the full "view": which page (or home + folder) and, for
  // PDFs, the exact scroll position (scale-aware). In-PDF link jumps, opening
  // other papers, search results, and going home all push here.
  const [navStack, setNavStack] = useState([]);
  const pdfEffScaleRef = useRef(1);
  useEffect(() => { pdfEffScaleRef.current = pdfEffScale; }, [pdfEffScale]);

  // Exact per-page reading positions so switching tabs returns to where you
  // were, not just to the same page. blockId -> {top, scale}, or for a page
  // with sheets {place} (notebook/sheetPlace.js), whichever view shows them.
  const tabScrollRef = useRef({});
  // The press a view of a page handed over as it made its group live
  // (PagePane's {edit: {id, clickPos}}): the editor of that note opening
  // on the page takes it (BlockTree's takeClickPos), so the caret lands
  // where the press was.
  const handedPressRef = useRef(null);
  const takeHandedPress = (id) => {
    const h = handedPressRef.current;
    if (!h || h.id !== id) return null;
    handedPressRef.current = null;
    return h.clickPos;
  };
  // Where each page's notes stood when it was left, for this session:
  // blockId -> {id, offset} (captureScrollPos), the row at their top ("" the
  // top itself) and the pixels scrolled into it.
  const notesPlaceRef = useRef({});
  // Per-page window layout (dock slots, collapsed bars, hidden windows, and
  // panel size ratios) — captured when leaving a page, restored on reopen.
  const pageLayoutsRef = useRef({});
  const panelGroupRefs = useRef({}); // group key -> react-resizable-panels imperative handle
  useEffect(() => {
    if (!authUser?.user || !wsId || shareMode) return;
    try { pageLayoutsRef.current = JSON.parse(localStorage.getItem(`gamma-page-layouts:${authUser.user}@${wsId}`) || "{}"); }
    catch { pageLayoutsRef.current = {}; }
  }, [authUser?.user, wsId, shareMode]);
  // The pages the notebook view is on for (docs/dev/notebooks.md): a page
  // with sheets shows them in the viewer's place while it is. A view of the
  // page, not part of it: remembered in this browser like its window
  // layout, and saved the moment it changes.
  const nbViewRef = useRef(new Set());
  const [nbViewPages, setNbViewPages] = useState(nbViewRef.current);
  useEffect(() => {
    let ids = [];
    if (authUser?.user && wsId && !shareMode) {
      try { ids = JSON.parse(localStorage.getItem(`gamma-notebook-view:${authUser.user}@${wsId}`) || "[]"); } catch {}
    }
    nbViewRef.current = new Set(Array.isArray(ids) ? ids : []);
    setNbViewPages(nbViewRef.current);
  }, [authUser?.user, wsId, shareMode]);
  // A switch shows the view (no compact-shell panel stays over it), and the
  // view it shows opens at the sheet the other one was at, or at `place`.
  function setNotebookView(pageId, on, place) {
    if (!pageId) return;
    setPhonePanel(null);
    if (nbViewRef.current.has(pageId) === on) return;
    const at = pageId === focusedBlockId && (place || readSheetPlace());
    if (at) tabScrollRef.current[pageId] = { place: at };
    const next = new Set(nbViewRef.current);
    if (on) next.add(pageId); else next.delete(pageId);
    while (next.size > 200) next.delete(next.values().next().value);
    nbViewRef.current = next;
    setNbViewPages(next);
    try { if (prefsUserRef.current) localStorage.setItem(`gamma-notebook-view:${prefsUserRef.current}`, JSON.stringify([...next])); } catch {}
  }
  // The reader's place on the open page's sheets, off whichever view shows
  // them: the notebook view, else the notes.
  function readSheetPlace() {
    const el = viewerWrapRef.current?.querySelector(".nbViewer") || liveNotesList();
    return el ? readPlace(el) : null;
  }
  const restoreTokenRef = useRef(0);   // bumped on navigation — kills in-flight restore loops
  const restoringForRef = useRef(null); // block whose restore hasn't landed yet
  const pdfRenderedUrlRef = useRef(""); // url of the document whose pages are in the DOM
  const pdfLaidOutUrlRef = useRef("");  // url whose manifest skeleton (exact page boxes, no document yet) is in the DOM
  const pendingRestoreRef = useRef(null); // {url, entry, blockId, token} applied pre-paint on "rendered"
  function captureScrollPos() {
    // The page's window layout travels with it — including panel size ratios.
    if (focusedBlockId && !shareMode && prefsUserRef.current) {
      const sizes = {};
      for (const [k, h] of Object.entries(panelGroupRefs.current)) {
        try { if (h) sizes[k] = h.getLayout(); } catch {}
      }
      // Side by side the windows are the groups' (openBlock leaves them be):
      // the page keeps its own arrangement and takes only what is its own.
      const own = !isPhone && !isSingle(tabLayoutRef.current)
        ? { ...(pageLayoutsRef.current[focusedBlockId] || {}), pdfHidden, pdfScale }
        : { layout, collapsed: collapsedWins, pdfHidden, chatHidden, notesVisible, sizes, pdfScale };
      pageLayoutsRef.current[focusedBlockId] = own;
      try {
        const keys = Object.keys(pageLayoutsRef.current);
        while (keys.length > 80) delete pageLayoutsRef.current[keys.shift()];
        localStorage.setItem(`gamma-page-layouts:${prefsUserRef.current}`, JSON.stringify(pageLayoutsRef.current));
      } catch {}
    }
    // The notes keep their place by block: the row at their top, and how
    // far into it they are scrolled, which its tab lands on again
    // (notesPlaceRef). A page without a viewer also keeps that row in the
    // synced reading positions; a PDF's entry there is its PDF page.
    const list = focusedBlockId ? liveNotesList() : null;
    if (list) {
      let place = { id: "", offset: 0 };
      if (list.scrollTop > 8) {
        const top = list.getBoundingClientRect().top;
        for (const row of list.querySelectorAll(".blockRowWrap[data-block-id]")) {
          const r = row.getBoundingClientRect();
          if (r.bottom > top + 4) { place = { id: row.dataset.blockId || "", offset: top - r.top }; break; }
        }
      }
      notesPlaceRef.current[focusedBlockId] = place;
      if (!shareMode && !viewerOf(doc?.format, doc)) recordNotePos(focusedBlockId, place.id);
    }
    // A page's sheets keep their place, on whichever view shows them.
    if (focusedBlockId && hasSheets) {
      const place = readSheetPlace();
      if (place) tabScrollRef.current[focusedBlockId] = { place };
    }
    // A pane of DocumentPane's own (the code view, the picture) reports
    // where it stands itself, through the surface's onPlace.
    if (ownPane) { viewerReport.current?.(); return; }
    // Only record a position when the viewer is actually showing THIS page's
    // document: mid-load the scroller still holds the previous document (or a
    // clamped 0), and a pending restore means the real position hasn't been
    // applied yet — in both cases keep the previously saved value.
    if (restoringForRef.current && restoringForRef.current === focusedBlockId) return;
    if (!pdfUrl || pdfRenderedUrlRef.current !== pdfUrl) return;
    const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
    if (focusedBlockId && scroller) {
      tabScrollRef.current[focusedBlockId] = { top: scroller.scrollTop, left: scroller.scrollLeft, scale: pdfEffScale };
    }
  }
  function cancelPdfRestore() {
    restoreTokenRef.current++;
    restoringForRef.current = null;
    pendingRestoreRef.current = null;
  }
  // Everything owed to the page being navigated away from: persist queued
  // edits, remember its scroll position + window layout, and kill any
  // in-flight scroll restore (or refetch of its tree) so it can't touch the
  // next document.
  function leaveCurrentPage() {
    flushPendingSave();
    // the pending handwriting too, now, while the page's threads are still
    // the ones its saver asks (a commented group erased empty keeps its
    // block, comments/useInkThreads.js): the next render shows the next
    // page's, and the saver's own flush on leaving would read those
    if (inkPending()) flushInk();
    // its tree as it stands, unsent edits and all, for the view of it a
    // tab group shows next (PagePane), which then draws at once
    if (focusedBlockId && focusedBlock) keepPaneTree(focusedBlockId, { ...focusedBlock, content: pageTitle }, blocksRef.current);
    seedBlockIdRef.current = null;
    captureScrollPos();
    cancelPdfRestore();
    cancelRefresh();
  }
  // Scroll the viewer back to an exact position. Two gates, both required:
  // the TARGET document must be the one rendered (the old document stays in
  // the DOM until the new one loads — scrolling it is what made restores land
  // wrong and visibly slide before the switch), and its layout height must be
  // stable for two ticks (pages get real heights asynchronously). The jump
  // itself is instant, after the new document is visible.
  function restorePdfScroll(entry, blockId, targetUrl) {
    if (entry?.top == null || !targetUrl) return;
    dbg("exact restore: pending for", blockId, "top", Math.round(entry.top));
    const token = ++restoreTokenRef.current;
    restoringForRef.current = blockId || null;
    // Preferred path: the "rendered" callback applies this pre-paint the
    // moment the target document mounts. The polling loop below is the
    // fallback (already-rendered documents, layout not tall enough yet).
    pendingRestoreRef.current = { url: targetUrl, entry, blockId, token };
    let tries = 0;
    let lastH = -1;
    const finish = () => { if (restoringForRef.current === blockId) restoringForRef.current = null; };
    const tryScroll = () => {
      if (restoreTokenRef.current !== token) { dbg("exact restore: superseded by navigation"); return; }
      if (pdfRenderedUrlRef.current === targetUrl) {
        const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
        const h = scroller ? scroller.scrollHeight : 0;
        const ratio = (pdfEffScaleRef.current || entry.scale || 1) / (entry.scale || 1);
        const targetTop = entry.top * ratio;
        if (scroller && h > targetTop && h === lastH) {
          scroller.scrollTo({ top: targetTop, ...(entry.left != null ? { left: entry.left * ratio } : null), behavior: "instant" });
          pendingRestoreRef.current = null;
          dbg("exact restore: applied, top", Math.round(targetTop));
          finish();
          return;
        }
        lastH = h;
        // Only the settle-after-render window is budgeted; waiting for the
        // document itself is uncounted (same reasoning as the coarse
        // restore: a big PDF on a slow load outlives any fixed budget, and
        // giving up unfreezes the tracker at the top of the document, which
        // then records page 1 over the real reading position).
        if (tries++ >= 80) {
          dbg("exact restore: gave up settling; height", h, "target", Math.round(targetTop));
          finish();
          return;
        }
      }
      setTimeout(tryScroll, 120);
    };
    tryScroll();
  }

  // Where the reader is, for Back to return to: the page, the folder, the
  // PDF's scroll and zoom, and a pane of DocumentPane's own its place (the
  // code view's {line}, reported through the surface's onPlace).
  function navEntry() {
    const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
    if (ownPane) viewerReport.current?.();
    return {
      blockId: focusedBlockId || null,
      folder: folderFilter,
      top: scroller ? scroller.scrollTop : null,
      scale: pdfEffScale,
      place: ownPane ? tabScrollRef.current[focusedBlockId] || null : null,
    };
  }
  // `entry`: one taken before an open that may not happen (openBlock's
  // share view), else where the reader is now.
  function pushNav(entry = navEntry()) {
    setNavStack((prev) => [...prev.slice(-29), entry]);
    guideEvents.emit("nav.pushed");
  }

  async function goBackNav() {
    const entry = navStack[navStack.length - 1];
    if (!entry) return;
    setNavStack((prev) => prev.slice(0, -1));
    guideEvents.emit("nav.back");
    // A code view's line: on its page the mounted view scrolls back to it
    // (the target's scrollOnly, no mark); another page's view opens there.
    if (entry.blockId && entry.blockId === focusedBlockId) {
      if (entry.place?.line) setPageTarget({ kind: "line", pageId: entry.blockId, line: entry.place.line, to: null, quote: "", scrollOnly: true });
      else restorePdfScroll(entry, entry.blockId, pdfUrl); // same document — just return to the reading position
    } else if (entry.blockId) {
      if (entry.place) tabScrollRef.current[entry.blockId] = entry.place;
      const openedUrl = await openBlock(entry.blockId);
      restorePdfScroll(entry, entry.blockId, openedUrl);
    } else {
      goHome();
      if (entry.folder) openFolder(entry.folder);
    }
  }
  const goBackNavRef = useRef(null);
  goBackNavRef.current = goBackNav;
  const navStackLen = navStack.length;
  // Phone: the topbar holds only tabs and Back (the Library tab is home),
  // so with neither it goes, and the status bar takes the library's colour.
  const phoneBareTop = isPhone && !shareMode && !openTabs.length && !navStackLen;
  useEffect(() => {
    phoneBareTopRef.current = phoneBareTop;
    paintStatusBar(phoneBareTop);
  }, [phoneBareTop]);

  function goHome(refreshHome = true, keepFilters = false) {
    leaveCurrentPage();
    clearSession();
    setFocusedBlockId(null);
    setFocusedBlock(null);
    setView(EMPTY_VIEW);
    setBlocks(loaded([]));
    setPdfUrl("");
    setInputUrl("");
    setPageTitle("");
    setPageLabels([]);
    setPageFolders([]);
    setBacklinks([]);
    setPdfHidden(false);
    if (!keepFilters) {
      setFolderFilter("");
      setLabelFilter("");
    }
    if (refreshHome) fetchHomeBlocks({ still: true });
    if (keepFilters && (labelFilter || folderFilter)) {
      window.history.replaceState(null, "", homeUrlFor(folderFilter, labelFilter));
    } else {
      window.history.replaceState({}, "", window.location.pathname);
    }
    guideEvents.emit("home.opened");
  }

  // Closing the open tab goes back to where you were: the latest page still
  // open or library view in the tab history (a promise while a page opens).
  // `refresh: false` leaves the library's refetch to the caller.
  function closeTab(id, { refresh = true } = {}) {
    const next = openTabs.filter((t) => t.id !== id);
    updateTabs(next);
    // Closed tabs must never become a later close's return destination.
    tabHistoryRef.current = tabHistoryRef.current.filter((entry) => entry.blockId !== id);
    // The tab a press picked, closed before its page lands: the press is
    // taken back, and what is on screen stays.
    if (tabPick && id === tabPick && id !== focusedBlockId) { dropTabPick(); return; }
    if (id !== focusedBlockId) return;
    // What shows next is found within the tab's group (docs/dev/tab_groups.md):
    // the latest page still open there or library view in the tab history,
    // else the group's neighbouring tab. A group emptied by the close goes,
    // and the neighbouring group's page comes forward.
    const group = groupOf(tabLayoutRef.current, id);
    const stillHere = (blockId) => !!group?.tabs.includes(blockId) && next.some((tab) => tab.id === blockId);
    const entry = [...tabHistoryRef.current].reverse().find((entry) => !entry.blockId || stillHere(entry.blockId));
    const arranged = removeTab(tabLayoutRef.current, id, { prefer: entry?.blockId || null });
    keepTabLayout(arranged);
    const live = activeGroup(arranged);
    if (group && live?.id !== group.id) {
      return live?.active ? selectTab(live.active) : goHome(refresh, true);
    }
    if (entry?.blockId) {
      return selectTab(entry.blockId);
    } else if (entry) {
      goHome(refresh);
      openFolder(entry.folder);
      if (entry.label) openLabel(entry.label, entry.folder);
    } else {
      goHome(refresh, true);
    }
  }

  // Where window `winId` would land dropped in `zone` ({side, index}): the
  // REAL landing geometry — the existing slot's rect (or the default size a
  // new slot would open with), halved to the drop position when other
  // windows already live there. The drag's preview, and the guide's scene
  // of that drag (services.dockZone).
  function dockPreviewRect(winId, zone) {
    const wa = document.querySelector(".workArea")?.getBoundingClientRect();
    if (!wa) return null;
    const slotEl = document.querySelector(`[data-panel-id="slot-${zone.side}"]`);
    let r;
    if (slotEl) {
      const b = slotEl.getBoundingClientRect();
      r = { left: b.left, top: b.top, width: b.width, height: b.height };
    } else if (zone.side === "bottom") {
      r = { left: wa.left, top: wa.top + wa.height * 0.68, width: wa.width, height: wa.height * 0.32 };
    } else if (zone.side === "left") {
      r = { left: wa.left, top: wa.top, width: wa.width * 0.26, height: wa.height };
    } else {
      r = { left: wa.left + wa.width * 0.72, top: wa.top, width: wa.width * 0.28, height: wa.height };
    }
    const others = layout[zone.side].filter((w) => w !== winId && winVisible[w]).length;
    if (others > 0) {
      if (zone.side === "bottom") {
        r = { ...r, width: r.width / 2, left: zone.index === 0 ? r.left : r.left + r.width / 2 };
      } else {
        r = { ...r, height: r.height / 2, top: zone.index === 0 ? r.top : r.top + r.height / 2 };
      }
    }
    return r;
  }

  // Drag any window by its grip; drop zones dock it left, right, or bottom.
  // Within a slot the drop half decides the order (top/left half = first),
  // which is how windows swap places. One implementation for every window.
  function startWindowDock(e, winId) {
    e.preventDefault();
    e.stopPropagation();
    const target = e.currentTarget;
    const startX = e.clientX;
    const startY = e.clientY;
    const pointerId = e.pointerId;
    let zone = null; // where it would land, once the press has moved far enough to be a drag
    try { target.setPointerCapture(pointerId); } catch (_) {}

    function zoneFor(ev) {
      if (ev.clientY > window.innerHeight * 0.65) {
        return { side: "bottom", index: ev.clientX < window.innerWidth / 2 ? 0 : 99 };
      }
      const side = ev.clientX < window.innerWidth / 2 ? "left" : "right";
      return { side, index: ev.clientY < window.innerHeight * 0.35 ? 0 : 99 };
    }
    // The preview (an App render) changes only when the zone does.
    function onMove(ev) {
      if (!zone && Math.hypot(ev.clientX - startX, ev.clientY - startY) < 8) return;
      const next = zoneFor(ev);
      if (zone && next.side === zone.side && next.index === zone.index) return;
      zone = next;
      setDockPreview(dockPreviewRect(winId, zone));
    }
    // A cancelled press (the system took the touch) moves nothing. A drop's
    // click is no press on the header, which would open a folded window.
    function onUp(ev) {
      if (zone && ev.type === "pointerup") {
        moveWindow(winId, zone.side, zone.index);
        guideEvents.emit("window.moved", { id: winId, side: zone.side });
        const swallow = (c) => c.stopPropagation();
        window.addEventListener("click", swallow, true);
        setTimeout(() => window.removeEventListener("click", swallow, true));
      }
      setDockPreview(null);
      try { target.releasePointerCapture(pointerId); } catch (_) {}
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
    }
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  }

  // The page header's label field files the open page: a label or a folder
  // picked from the suggestions, or a typed name — the label (else the
  // folder) of that name, or a new label made through the label tree's ops
  // ("New folder" among the suggestions makes a top-level folder instead).
  // Each pick writes at once, from the ids the trees have, with the same
  // refinement rule as addPagesToFolder.
  async function fileOpenPage(filing) {
    const pageId = focusedBlockId;
    const was = { folders: pageFolders, labels: pageLabels };
    if (filing.folders) setPageFolders(filing.folders);
    if (filing.labels) setPageLabels(filing.labels);
    try {
      await filePage(pageId, filing);
      await fetchHomeBlocks();
    } catch (err) {
      if (pageId === focusedBlockIdRef.current) {
        if (filing.folders) setPageFolders(was.folders);
        if (filing.labels) setPageLabels(was.labels);
      }
      setStatus(t("Could not file the page: {message}", { message: err.message }));
    }
  }
  const openPageFiling = () => ({ folders: filedIn(libTree.folders, pageFolders), labels: filedIn(libTree.labels, pageLabels) });
  // The open page's folders once filed in `id` too, as the work-folder
  // rule files it (fileRules.refile: a library page in no work folder's
  // directory, a work folder's file in library folders only while it is in
  // the library); the status says why not.
  function addOpenPageFolder(id) {
    const { folders } = openPageFiling();
    if (folders.includes(id)) return;
    const page = { properties: { ...(focusedBlock?.properties || {}), folders } };
    const { folders: filed, why } = fileRules(page, workTree).refile(id);
    if (filed) fileOpenPage({ folders: filed });
    else setStatus(why);
  }
  function addOpenPageLabel(id) {
    const { labels } = openPageFiling();
    if (!labels.includes(id)) fileOpenPage({ labels: [...labels, id] });
  }
  function removeOpenPageFolder(id) {
    if (focusedFile && !openRules.removableFolders.includes(id)) { setStatus(refusalText("filed")); return; }
    fileOpenPage({ folders: openPageFiling().folders.filter((f) => f !== id) });
  }
  function removeOpenPageLabel(id) {
    fileOpenPage({ labels: openPageFiling().labels.filter((l) => l !== id) });
  }
  // A suggestion picked ({kind: "label" | "folder", id}), or one to make
  // ({kind: "newLabel" | "newFolder", name}).
  async function pickLabelSuggestion(s) {
    setLabelInput("");
    setLabelSuggestionIdx(-1);
    if (!focusedBlockId || shareMode) return;
    try {
      if (s.kind === "folder") addOpenPageFolder(s.id);
      else if (s.kind === "label") addOpenPageLabel(s.id);
      else if (s.kind === "newFolder") addOpenPageFolder((await ensureFolders([s.path]))(s.path));
      else addOpenPageLabel(await makeLabel(s.name));
    } catch (err) {
      setStatus(t("Could not file the page: {message}", { message: err.message }));
    }
  }
  // Enter (or leaving the field) with a name typed: the label called that,
  // else the folder whose path or name it is, else a new label.
  function commitLabelInput() {
    const name = labelInput.trim();
    if (!name) return;
    const label = findNamed(libTree.labels.values(), name);
    // (a page inside another takes labels only: its folders are its top's)
    const folder = !openSubPage && (folderAtPath(libTree, name) || findNamed(libTree.folders.values(), name));
    pickLabelSuggestion(label ? { kind: "label", id: label.id }
      : folder ? { kind: "folder", id: folder.id } : { kind: "newLabel", name });
  }
  function closeLabelEditor() {
    commitLabelInput();
    setLabelInput("");
    setLabelEditing(false);
  }

  // Share popover (owner; sharing/SharePopover.jsx). Opening it only LOADS the
  // state — nothing is shared until its Share button creates the share;
  // settings changes save immediately and the token only changes on "Stop
  // sharing".
  function applyShareSettings(data) {
    setShareSettings(data);
    setShareError("");
  }
  // The endpoints for one target differ only in how they name it
  // (docs/dev/api.md "Shares"): /share/<page id> or /share/folder/<folder id>.
  const shareApi = (target, base) => (target.kind === "folder"
    ? `${API}/${base}/folder/${encodeURIComponent(target.id)}`
    : `${API}/${base}/${encodeURIComponent(target.id)}`);
  // The same target's settings stay while they are asked again, so the
  // popover opens on them; another target's go at once.
  async function loadShareSettings(target) {
    if (shareMode || (target.kind === "page" && !target.id)) return;
    if (shareTarget?.kind !== target.kind || shareTarget?.id !== target.id) setShareSettings(null);
    setShareTarget(target);
    try {
      applyShareSettings(await apiJson(shareApi(target, "share-settings")));
      resetShareCopied();
    } catch (err) {
      setStatus(t("Share failed: {message}", { message: err.message }));
    }
  }
  // `settings` ({audience, role}) is the new share's access, the popover's
  // NEW_SHARE.
  async function createShareLink(settings) {
    if (!shareTarget || shareMode) return false;
    try {
      applyShareSettings(await apiJson(shareApi(shareTarget, "share"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings || {}),
      }));
      resetShareCopied();
      guideEvents.emit("share.created");
      return true;
    } catch (err) {
      setShareError(err.message); // e.g. "unknown user(s): …" — shown in the popover
      return false;
    }
  }
  async function updateShareSettings(patch) {
    if (!shareTarget || !shareSettings?.token) return false;
    try {
      applyShareSettings(await apiJson(shareApi(shareTarget, "share-settings"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }));
      return true;
    } catch (err) {
      setShareError(err.message); // e.g. "unknown user(s): …" — shown in the dialog
      return false;
    }
  }
  // People: invitations are additive to general access (Notion-style) —
  // each invited account carries its own view/edit.
  function inviteShareUser(name, role) {
    if (!shareSettings?.token) return false;
    const current = shareSettings.users || [];
    if (current.some((u) => u.name === name)) return true;
    return updateShareSettings({ users: [...current, { name, role }] });
  }
  function setShareUserRole(name, role) {
    updateShareSettings({ users: (shareSettings?.users || []).map((u) => u.name === name ? { ...u, role } : u) });
  }
  function removeShareUser(name) {
    updateShareSettings({ users: (shareSettings?.users || []).filter((u) => u.name !== name) });
  }
  // People with no account here (docs/dev/api.md "Shares"): an invitation by
  // e-mail (to view; it waits until its mailed link is followed, even when
  // someone here has that address), a waiting one's role changed, its mail
  // sent again or withdrawn; a waiting invitation by Gamma Cloud username's
  // role changed or withdrawn; a request to join approved (an invitation by
  // its subject) or declined. Each answers the share's settings; a refusal
  // shows in the popover.
  async function shareCloudCall(path, method, body) {
    if (!shareTarget || !shareSettings?.token) return false;
    try {
      applyShareSettings(await apiJson(`${shareApi(shareTarget, "share-settings")}${path}`, body ? {
        method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      } : { method }));
      return true;
    } catch (err) {
      setShareError(err.message); // e.g. "That is not an e-mail address."
      // A 404: what the row names went meanwhile (an invitation accepted,
      // withdrawn elsewhere or expired), so the row would only fail again.
      // The people are read again straight into the settings, as
      // applyShareSettings would also clear the line that says why.
      if (err.status === 404) {
        try { setShareSettings(await apiJson(shareApi(shareTarget, "share-settings"))); } catch { /* the line stands */ }
      }
      return false;
    }
  }
  const emailInvite = (id) => `/email-invites/${encodeURIComponent(id)}`;
  const shareCloudInvites = {
    email: !!serverConfig?.cloud?.email_invites?.members,
    onInviteEmail: async (email) => {
      const ok = await shareCloudCall("/invites", "POST", { email, role: "view" });
      if (ok) setStatus(t("Invitation sent to {email}.", { email }));
      return ok;
    },
    onSetEmailRole: (id, role) => shareCloudCall("/invites", "POST", { invitation: id, role }),
    onResendEmail: async (id) => {
      const email = shareSettings?.pending?.find((p) => p.id === id)?.email;
      if (await shareCloudCall(`${emailInvite(id)}/resend`, "POST")) setStatus(t("Sent the invitation to {email} again.", { email }));
    },
    onWithdrawEmail: (id) => shareCloudCall(emailInvite(id), "DELETE"),
    onSetRole: (subject, role) => shareCloudCall("/invites", "POST", { subject, role }),
    onWithdraw: (subject) => shareCloudCall(`/invites/${encodeURIComponent(subject)}`, "DELETE"),
    onApprove: (subject) => shareCloudCall("/invites", "POST", { subject, role: "view" }),
    onDecline: (subject) => shareCloudCall(`/requests/${encodeURIComponent(subject)}`, "DELETE"),
  };
  async function stopSharing() {
    if (!shareTarget || !shareSettings?.token) return;
    try {
      await apiJson(shareApi(shareTarget, "share-settings"), { method: "DELETE" });
      applyShareSettings({ token: null, ...(shareTarget.kind === "folder" ? { folder: shareTarget.id } : { page_id: shareTarget.id }) });
      setStatus(t("Sharing stopped — the old link no longer opens."));
    } catch (err) {
      setStatus(t("Stop sharing failed: {message}", { message: err.message }));
    }
  }
  // The home page menu's Copy link / Share… / Export… / Ask AI. The last
  // three act on the open page, so the page is opened first and the action
  // runs once it is the one on screen (the effect below).
  function copyPageLink(id) {
    copyText(withWorkspace(`${window.location.origin}/?page=${encodeURIComponent(id)}`))
      .then((ok) => setStatus(ok ? t("Link copied.") : t("Couldn't copy the link.")));
  }
  // `pushNav`: Back returns to the page it was asked from (a Pages inside
  // row's menu).
  function openPageThen(id, action, pushNav = false) {
    setHomeMenu(null);
    clearSelection();
    openBlock(id, { restoreScroll: true, then: action, pushNav });
  }
  // The open page's Share popover (the topbar's link button, the page menu).
  function openPageShare(id) {
    loadShareSettings({ kind: "page", id });
    setShareError("");
    // The same page's publication stays on screen while it is read again
    // (its Gamma Cloud section opens on its content); another page's starts
    // from nothing.
    if (publishState?.page === focusedBlockId) {
      setPublishError("");
      resetPublishCopied();
      loadPublishState({ quiet: true });
    } else loadPublishState();
    setOpenPopover("share");
  }
  // The chat window shown and expanded (on a phone, its panel).
  function showChat() {
    setChatHidden(false);
    setCollapsedWins((prev) => ({ ...prev, chat: false }));
    if (isPhone) setPhonePanel("chat");
  }
  useEffect(() => {
    const pending = pendingPageActionRef.current;
    if (!pending || pending.id !== focusedBlockId) return;
    pendingPageActionRef.current = null;
    if (pending.action === "share") {
      openPageShare(pending.id);
    } else if (pending.action === "export") {
      setExportFolder(null);
      setExportOpen(true);
    } else if (pending.action === "ask") {
      showChat();
      setTimeout(() => document.querySelector(".chatInputArea")?.focus(), 200);
    } else if (pending.action === "title" && !readOnly) {
      setTitleDraft(pageTitle || t("Untitled"));
      setTitleEditing(true);
    } else if (pending.action === "labels" && !shareMode) {
      setLabelInput("");
      setLabelSuggestionIdx(-1);
      setLabelEditing(true);
    } else if (TOPBAR.item(pending.action)) {
      // a topbar item pressed in a pane whose page was a view (paneStandIns):
      // its popover, under the pane now live
      setOpenPopover(TOPBAR.item(pending.action).popover);
    }
  }, [focusedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps
  // Share a folder: the same popover under the topbar's link button, which
  // the folder view shows — so from the context menu the folder is opened first.
  function openFolderShare(id) {
    if (!homeMode) goHome();
    if (folderFilter !== id || labelFilter) openFolder(id);
    loadShareSettings({ kind: "folder", id });
    setShareError("");
    setOpenPopover("share");
  }
  // Publishing to Gamma Cloud (sharing/SharePopover.jsx PublishSection). POST
  // both publishes and changes an existing cloud share's audience / role; a
  // publish runs a sync round, so it can take seconds (a change of a page
  // already there runs none). Refusals come back as the
  // server's sentence and are shown in the section, never as an alert.
  const publishUrl = (pageId) => `${API}/pages/${encodeURIComponent(pageId)}/publish`;
  async function loadPublishState({ quiet = false } = {}) {
    const pageId = focusedBlockId;
    if (!publishOffered || !pageId) return;
    if (!quiet) { setPublishState(null); setPublishError(""); resetPublishCopied(); }
    try {
      const data = await apiJson(publishUrl(pageId));
      setPublishState({ ...data, page: pageId });
    } catch (err) {
      if (!quiet) setPublishState({ published: false, can_publish: false, reason: err.message, page: pageId });
    }
  }
  function markPublishing() {
    // the header's sync pill follows the publication without a reload
    setWorkspace((prev) => (prev && !prev.mirror_of && !prev.zotero_of && !prev.publishing ? { ...prev, publishing: true } : prev));
    window.dispatchEvent(new CustomEvent("gamma:mirror"));
  }
  async function publishPage(patch) {
    const pageId = focusedBlockId;
    if (!pageId || publishBusy) return;
    setPublishBusy(patch ? "update" : "publish");
    setPublishError("");
    if (patch) setPublishState((prev) => (prev?.share ? { ...prev, share: { ...prev.share, ...patch } } : prev));
    try {
      const out = await apiJson(publishUrl(pageId), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch || {}),
      });
      setPublishState((prev) => ({
        ...(prev || {}), page: pageId, published: true, can_publish: true, reason: undefined, error: undefined,
        url: out.url, public_url: out.public_url, share: out.share, mirror: out.mirror, status: out.mirror?.status,
      }));
      markPublishing();
    } catch (err) {
      // the plan's cap carries its count: the section offers the account page with it
      setPublishError(err.data?.limit ? { message: err.message, limit: err.data.limit, going: err.data.going || 1 } : err.message);
      if (patch) loadPublishState({ quiet: true }); // the tiles go back to what the share host holds
    } finally {
      setPublishBusy("");
    }
  }
  async function unpublishPage() {
    const pageId = focusedBlockId;
    if (!pageId || publishBusy) return;
    setPublishBusy("unpublish");
    setPublishError("");
    try {
      const out = await apiJson(publishUrl(pageId), { method: "DELETE" });
      setPublishState((prev) => ({
        ...(prev || {}), page: pageId, published: false, url: undefined, share: undefined, mirror: out.mirror,
      }));
      // the last page unpublished: the publication is invisible again (no pill)
      if (!(out.mirror?.published_pages || out.mirror?.page_filter || []).length) {
        setWorkspace((prev) => (prev?.publishing ? { ...prev, publishing: false } : prev));
      }
      window.dispatchEvent(new CustomEvent("gamma:mirror"));
      loadPublishState({ quiet: true });
    } catch (err) {
      setPublishError(err.message);
    } finally {
      setPublishBusy("");
    }
  }
  async function syncPublication() {
    const ws = publishState?.mirror?.ws;
    if (!ws || publishBusy) return;
    setPublishBusy("sync");
    setPublishError("");
    try {
      // the round asked for, then followed through the publication's status (its progress on the state line)
      await followSync(() => apiJson(`${API}${linkPath("mirror", ws, "/sync")}`, { method: "POST" }), async () => {
        const mirror = await apiJson(`${API}${linkPath("mirror", ws)}`);
        setPublishState((prev) => (prev?.mirror?.ws === ws ? { ...prev, mirror: { ...prev.mirror, status: mirror.status } } : prev));
        return mirror;
      });
    } catch (err) {
      setPublishError(err.message);
    }
    await loadPublishState({ quiet: true });
    setPublishBusy("");
    window.dispatchEvent(new CustomEvent("gamma:mirror"));
  }
  async function copyPublishLink() {
    const link = publishState?.public_url || publishState?.url;
    if (link && await copyText(link)) { flashPublishCopied(); return; }
    setStatus(t("Copy failed — select the link in the popover instead."));
  }

  // The share view's visitor renamed themself: keep it, and rejoin the room
  // so presence shows the new name (it travels in the socket handshake).
  function commitLinkName(raw) {
    const name = cleanLinkName(raw) || linkName;
    setRenamingLink(false);
    if (name === linkName) return;
    saveLinkName(name);
    setLinkName(name);
    setLinkNameState(name);
    collab.reconnect();
  }

  async function copyShareLink() {
    if (await copyText(shareUrl)) { flashShareCopied(); return; }
    setStatus(t("Copy failed — select the link in the popover instead."));
  }

  // Download the current page as Markdown. The server returns a bare .md, or a
  // .zip (page + assets/) when the page references uploaded files — we save
  // whichever comes back (shared/lib/saveFile.js), taking the filename from
  // Content-Disposition. Using fetch+blob (not a plain navigation) so a
  // 404/401 surfaces as a message instead of silently swapping the SPA for
  // an error page.
  async function downloadExport(path, fallbackName) {
    setStatus(t("Exporting page…"));
    // Shared views: withShare puts the scoped token on the export URL, so the
    // backend confines it to this page.
    try {
      const res = await fetch(withShare(`${API}${path}`), { credentials: "include" });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch { /* not JSON */ }
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const ctype = res.headers.get("Content-Type") || "";
      // If the SPA fallback served index.html, the export route isn't live yet.
      if (ctype.includes("text/html")) throw new Error("export route not found — restart the backend");
      const filename = dispositionName(res.headers.get("Content-Disposition"), fallbackName);
      if (!(await saveBlob(await res.blob(), filename))) {
        setStatus(t("The app did not take {filename}.", { filename }));
        return null;
      }
      setStatus(t("Exported {filename}", { filename: filename }));
      return filename;
    } catch (err) {
      setStatus(t("Export failed: {message}", { message: err.message }));
      return null;
    }
  }

  // Shared (read-only) view — exports and asset fetches carry the scoped share
  // token (?share=), which the backend validates and confines to this document.

  // Run what the import dialog was configured to do. Logseq needs files, so
  // its button opens the picker and the import starts once they're chosen.
  function runImport(o) {
    setImportOpen(false);
    if (o.source === "annots") {
      if (!docId || !focusedBlockId) { setStatus(t("Open a PDF first.")); return; }
      importEmbeddedAnnots(focusedBlockId, docId, false, o.strip);
      return;
    }
    if (["zotero", "markdown", "notion", "gamma", "noteapps"].includes(o.source)) {
      // into the folder open, but a work folder's, whose directories are the disk's: the library's top
      const review = (file) => setImportReview({ source: o.source, file, strip: o.strip,
        folder: homeMode && folderFilter && !workFolder ? folderFilter : "" });
      const { label, accept, multiple } = importSource(o.source);
      if (o.file) review(o.file); // dropped on the dialog: a .zip or a folder (folderZip.js)
      else if (o.pick === "folder") pickFiles({ directory: true }, (files) => review(folderPick(files)));
      else if (multiple) {
        // Notes picked together go up as one .zip named after the source.
        // Safari on an iPhone or iPad and the Android app (no folder
        // picker: canPickFolder) grey out a file whose extension they have
        // no type for, a .note, .ntb or .goodnotes among them, so there the
        // picker takes any file and the server says what it cannot read.
        pickFiles({ accept: canPickFolder() ? accept : "", multiple: true },
          (files) => review(files.length === 1 ? files[0] : filesUpload(files, t(label))));
      } else pickFiles({ accept }, ([file]) => review(file));
      return;
    }
    pickFiles({ accept: ".pdf,.edn,.md", multiple: true }, importLogseq);
  }

  // Text pasted into the "+" box, reduced to what was meant: a link copied
  // out of a chat arrives with the app's decorations around it ("[7:54 PM]
  // https://…", trailing punctuation), so when the text contains an http(s)
  // URL that URL is the input; an arXiv id or DOI has none and passes as is.
  function cleanAddInput(text) {
    const t = String(text || "").trim();
    const m = t.match(/https?:\/\/[^\s<>"'\]\)]+/);
    return m ? m[0] : t;
  }

  // A Gamma share link is "<origin>/?share=<token>": the SPA root with a
  // share query and nothing else in the path. That shape separates it from an
  // ordinary PDF/paper URL in the "+" box without a network round trip, so a
  // PDF hosted at some site's "/?share=" would be the one false positive — and
  // pasting it there does what the user meant either way (it opens as a PDF
  // once the share resolution fails). Returns the cleaned link or null.
  function parseGammaShareLink(text) {
    const link = cleanAddInput(text);
    let u;
    try { u = new URL(link); } catch { return null; }
    if (!/^https?:$/.test(u.protocol) || !u.searchParams.get("share")) return null;
    if (u.pathname !== "/" && !u.pathname.endsWith("/index.html")) return null;
    return link;
  }

  // Import what a share link shares — a page, or a folder with its pages and
  // subfolders — from another Gamma or this server into this library.
  // Browser-side on purpose: the browser can reach a Gamma on the LAN or at
  // localhost that the server's SSRF guard would rightly refuse. The owner's
  // Gamma answers share reads cross-origin, so with the token alone (no
  // cookies — only "anyone" links open from another origin; a same-origin
  // link also gets this session, so invite-only ones work here) the page or
  // folder is fetched as a Gamma export and merged through the same path as
  // Import → Gamma export. A folder share's link may name one of its pages
  // (page=, the share view's address while a page is open) or a subfolder
  // (folder=, while one is browsed): that is what comes over. The tab then
  // reloads into it: a page by the id the link named (block ids survive), a
  // folder by the id the merge reports (it may become a folder of the same
  // name this library already has).
  async function importSharedPage(shareUrl) {
    let origin = "", token = "", linkedPage = "", linkedFolder = "";
    try {
      const u = new URL(shareUrl, window.location.href);
      origin = u.origin;
      token = u.searchParams.get("share") || "";
      linkedPage = u.searchParams.get("page") || ""; // a page opened through a folder share
      linkedFolder = u.searchParams.get("folder") || ""; // a subfolder browsed in one
    } catch {}
    if (!token) { setStatus(t("That isn't a Gamma share link (no ?share= in it).")); return; }
    const local = origin === window.location.origin;
    const opts = { credentials: local ? "include" : "omit" };
    const host = local ? t("this Gamma") : new URL(origin).host;
    const tid = addTask({ name: t("Shared link from {host}", { host }).slice(0, 60), kind: "download", info: t("resolving…") });
    try {
      let r;
      try {
        r = await fetch(`${origin}${API}/share/${encodeURIComponent(token)}`, opts);
      } catch {
        throw new Error(t("couldn't reach {origin} — is it up, and a Gamma recent enough to share across servers?", { origin }));
      }
      if (r.status === 401 || r.status === 403) {
        throw new Error(local
          ? t("that link is shared with specific people only")
          : t("that link isn't open to anyone — only public share links can be imported from another Gamma"));
      }
      if (!r.ok) throw new Error(t("share link not found"));
      const info = await r.json();
      // the page the link names (a page inside the shared page, or a page of a
      // folder share) before the share's own page
      const pageId = linkedPage || info.page_id;
      const folder = pageId ? "" : linkedFolder || info.folder || "";
      updateTask(tid, {
        name: (pageId ? t("Shared page from {host}", { host }) : t("Shared folder from {host}", { host })).slice(0, 60),
        info: t("downloading…"),
      });
      // a page comes with the pages inside it, as the share opens them
      const what = pageId ? `pages/${encodeURIComponent(pageId)}` : `folders/${encodeURIComponent(folder)}`;
      r = await fetch(`${origin}${API}/${what}/export?mode=gamma${pageId ? "&sub_pages=1" : ""}&share=${encodeURIComponent(token)}`, opts);
      if (!r.ok) {
        // the server's words say which refusal it is (a share read's error is readable cross-origin too)
        let detail = "";
        try { detail = String((await r.json()).detail || ""); } catch {}
        // (through a share, a page or folder deleted meanwhile is refused as out of reach)
        throw new Error(detail === "no pages in that folder" ? t("that folder has no pages yet")
          : r.status === 403 ? t("that isn't in what the link shares — it may have been moved out or deleted")
          : r.status === 404 || r.status === 400 ? t("that Gamma is too old to export pages for another Gamma")
          : t("export failed ({status})", { status: r.status }));
      }
      const blob = await r.blob();
      updateTask(tid, { state: "done", info: fmtBytes(blob.size) });
      runBackupImport(new File([blob], pageId ? "shared-page.zip" : "shared-folder.zip", { type: "application/zip" }),
        "merge", null, pageId ? { openPage: pageId } : { openFolder: folder });
    } catch (err) {
      updateTask(tid, { state: "failed", info: String(err.message) });
      setStatus(t("Import failed: {message}", { message: err.message }));
    }
  }

  // The share view's "Add to my library": what it shows — the open page, or
  // the folder browsed (the shared folder or one below it) — is imported by
  // the visitor's own library, not by this tab, which has no workspace and
  // no background tasks: the link goes over in sessionStorage, where no
  // other site can put one (an address alone never imports anything), and
  // the library takes it as it boots (in a workspace it may write to:
  // chooseWorkspace). A page host serves no library, so it has no such button.
  function addShareToLibrary() {
    const root = window.location.pathname;
    const q = new URLSearchParams({ share: initialShare });
    if (focusedBlockId) q.set("page", focusedBlockId);
    else if (folderFilter) q.set("folder", folderFilter);
    try {
      sessionStorage.setItem(IMPORT_SHARE, `${window.location.origin}${root}?${q}`);
    } catch {
      setStatus(t("This browser keeps no session storage here: copy the link, then paste it into + in your library."));
      return;
    }
    window.location.href = root;
  }

  // Download the PDF exactly as stored — no highlight annotations. Reuses the
  // viewer's own URL (uploads route or /pdf proxy), so it works in share views.
  async function exportRawPdf() {
    if (!pdfUrl) { setStatus(t("No PDF open.")); return; }
    setOpenPopover(null);
    const path = pdfUrl.startsWith(API) ? pdfUrl.slice(API.length) : pdfUrl;
    const name = `${(pageTitle || docId || "paper").replace(/[\\/:*?"<>|]/g, "_").slice(0, 80)}.pdf`;
    await downloadExport(path, name);
  }

  // Ask the AI for the document's title and fill it into the page name.
  const [aiTitleBusy, setAiTitleBusy] = useState(false);
  async function aiFillTitle() {
    if (!docId || shareMode || aiTitleBusy) return;
    setAiTitleBusy(true);
    setStatus(t("Asking AI for the title…"));
    const ctl = new AbortController();
    const taskId = addTask({ name: `AI title — ${(pageTitle || "paper").slice(0, 48)}`, kind: "ai", info: t("asking…"), cancel: () => ctl.abort() });
    try {
      const data = await apiJson(`${API}/ai/chat`, {
        method: "POST", signal: ctl.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: "Extract the exact title of this document. Reply with ONLY the title text — no quotes, no authors, no extra words.",
          page_id: focusedBlockId,
          history: [],
          model: chatSendModel || "",
        }),
      });
      const title = (data.response || "").trim().replace(/^["'\s]+|["'\s]+$/g, "").split("\n")[0].slice(0, 200);
      updateTask(taskId, { state: title ? "done" : "failed", info: title ? "" : t("no title") });
      if (title) {
        await renameTitle(title);
        setStatus(t("Title filled in by AI."));
      } else {
        setStatus(t("AI returned no title."));
      }
    } catch (err) {
      updateTask(taskId, { state: "failed", info: (err.message || "failed") });
      setStatus(t("AI title failed: {message}", { message: err.message }));
    } finally {
      setAiTitleBusy(false);
    }
  }

  // A highlight from a selection ({quote, note, position, color}, as
  // addHighlightAsBlock takes it). Returns the new highlight's id. `edit`
  // false: its note is not opened for typing (a comment is written instead).
  // `place`: the key the position is stored under, the PDF's unless a
  // picture's region (addRegion).
  function addHighlight(highlight, { edit = true, place = POSITION_KEY.pdf } = {}) {
    if (readOnly) return "";
    const withId = { ...highlight, id: highlight.id || makeId() };
    const nextBlocks = addHighlightAsBlock(blocks, { ...withId, place });
    // Open the new block immediately so the user can type the note without
    // an extra click. addHighlightAsBlock appends at the top level.
    if (edit) {
      startEditing(withId.id);
      pendingFocusRef.current = withId.id;
    }
    scrollToBlock(withId.id);
    setBlocks(nextBlocks);
    // autosave effect will persist
    setStatus(t("Highlight saved."));
    guideEvents.emit("highlight.created", { id: withId.id, kind: withId.position?.area || place === POSITION_KEY.image ? "area" : "text" });
    return withId.id;
  }
  // A region drawn on the picture (the image viewer's tip): a highlight
  // block placed under image_position, its row's editor open for a note,
  // or its thread's field for a comment (commentOnNewHighlight, whose
  // abandoned-comment cleanup is the passage's).
  function addRegion(position, { color, comment = false } = {}) {
    const id = addHighlight({ quote: "", position, color: highlightColor(color) || DEFAULT_HIGHLIGHT }, { edit: !comment, place: POSITION_KEY.image });
    if (comment && id) commentOnNewHighlight(id);
  }

  // A page whose PDF Zotero holds and Gamma not yet (its `zotero_pdf`,
  // docs/dev/zotero_sync.md): its card shows in the attach popover, in the
  // prompt's place while something brings that PDF (`zoteroPdfShown`, the
  // server refuses another meanwhile), above the prompt when nothing can
  // (a linked file, WebDAV, no file access, a failed download, no link any
  // more), so the page takes a copy of its own, which stays in Gamma.
  // Opening the page asks a live link for that PDF first, once.
  const zoteroPdf = !doc && focusedBlock?.properties?.zotero_pdf || null;
  const zoteroSource = shareMode ? "" : zoteroPdfSource(workspace);
  const zoteroLinked = zoteroSource === "zotero";
  const zoteroCard = zoteroPdfCard(zoteroPdf, { source: zoteroSource });
  const zoteroPdfShown = zoteroCard?.takesOver || false;
  const fetchZoteroPdfNow = (pageId) => { fetchZoteroPdf(workspace.id, pageId).catch(() => {}); };
  const zoteroPdfAsked = useRef(new Set());
  const zoteroPdfWanted = zoteroLinked && zoteroPdfState(zoteroPdf)?.fetch ? focusedBlock?.id || "" : "";
  useEffect(() => {
    if (!zoteroPdfWanted || zoteroPdfAsked.current.has(zoteroPdfWanted)) return;
    zoteroPdfAsked.current.add(zoteroPdfWanted);
    fetchZoteroPdfNow(zoteroPdfWanted);
  }, [zoteroPdfWanted]); // eslint-disable-line react-hooks/exhaustive-deps -- once per page
  // The page's sheets of paper (notebook/notebook.js, docs/dev/notebooks.md).
  // The notes view draws them among the blocks (notebook/NoteSheet.jsx); on
  // a page without a PDF the notebook view — `notebook`, switched per page
  // (setNotebookView) — puts them in the viewer's place, with the same ink
  // tools, and lists them in the notes as rows.
  const nbSheets = useMemo(() => (focusedBlockId ? sheetsOf(blocks) : []), [focusedBlockId, blocks]);
  // Which viewer mounts and whether it shows (documents/documentView.js, the
  // rule a tab group's page shows by too): pdfUrl is only the PDF viewer's
  // input. A closed PDF stays mounted (div.viewerWrap pdfHidden); a closed
  // pane never hides the notebook view's sheets.
  const docView = documentView({ doc, url: pdfUrl, sheets: nbSheets.length,
    notebook: nbViewPages.has(focusedBlockId), hidden: pdfHidden, open: !!focusedBlockId });
  const hasSheets = docView.notebookAvailable;
  const notebook = docView.notebook;
  // A pane DocumentPane draws itself from the surface (the code view, the
  // picture, the player, the file pane, from the surface App builds:
  // documents/viewerSurface.js), not one App renders (`spec.host`); the
  // pane's word for the View menu and the phone tab ("PDF" before a PDF has
  // its address).
  const ownPane = !!docView.viewer && !docView.spec?.host;
  const paneWord = docView.spec ? t(docView.spec.word) : "PDF";

  // --- Handwriting ----------------------------------------------------------
  // Strokes live in inkStore drafts and reach the server as an .ink upload
  // plus a properties PATCH through the block API (a server-side writer, so
  // the change fans out over the page socket and lands in this tree like a
  // remote op); only the group's block itself is inserted through the tree.
  // The PATCH names the file the draft was edited from (base_properties):
  // when someone else changed the drawing meanwhile the server merges both
  // by stroke and answers with the merged file (gamma/ops.py merge_ink).
  // The save (ink/useInkSave.js over ink/inkSaver.js): the drafts go to the
  // server 700 ms after the last edit, at most 5 s after the first unsaved
  // one, and at once when the tab is hidden, the page goes away or another
  // page opens. Called after the page's collab session, so its pagehide
  // and visibilitychange listeners come after collab's. A group with a
  // comment thread keeps its block when erased empty (inkThreads, below).
  const { flushInk, scheduleInk, pending: inkPending } = useInkSave({ focusedBlockId, blocksRef, setStatus, liveSaved, keeps: (id) => inkThreads.keeps(id) });
  // The page's ink by surface (ink/useInkSurfaces.js): its groups, each
  // surface's, where a paste without a point goes, a surface's name in
  // messages.
  const { inkBlocks, inkBySurface, onPageInView, pasteTarget, inkPageNumber } = useInkSurfaces({
    blocks, nbSheets, nbSheetsRef, nbCurrent, notebook, pdfUrl, pdfHidden });
  // Pictures (markup/usePictures.js): the selected picture and its crop
  // mode, its edits, and the new ones the strip's Picture button, a paste
  // and a drop place on the surface in view. One selection in the app: a
  // picture selected lets go of a text box and of the lasso's selection, and
  // either of those lets go of the picture; a tool armed lets go of it. The
  // pictures a lasso takes are its selection's, not this one.
  // A new picture without a point goes to the surface in view, else to the
  // sheet among the notes that shows the most of itself.
  const pictureTarget = () => pasteTarget() || nbSheetsRef.current.reduce((best, s) => {
    const size = { width: s.paper.width, height: s.paper.height }, seen = visiblePart(s.id, size);
    const area = seen ? seen.width * seen.height : 0;
    return area > (best?.area || 0) ? { page: s.id, size, area } : best;
  }, null);
  const pictures = usePictures({ blocks, blocksRef, setBlocks, readOnly, pageId: focusedBlockId, tool: inkUi.tool,
    pasteTarget: pictureTarget, setStatus, onSelect: () => { textBoxes.actions.onBoxDeselect(); clearInkSelection(); } });
  // Undo / Redo (the strip's buttons, Ctrl+Z while it is open) step the
  // block history for text boxes and pictures (the Text tool armed, a box
  // or a picture selected), the stroke history otherwise.
  const boxUndo = textArmed || !!textBoxes.sel || !!pictures.sel;
  // The page's ink as a document (ink/useInkDocument.js): every edit
  // through applyInk, the stroke history and its steps, the eraser's
  // sweeps and a scribble's erasures, the journal's restored drafts. A step
  // of a lasso step that took pictures steps their half in the block
  // history too (undoLinked).
  const { inkHistoryState, inkOf, applyInk, inkUndo, undoableInkStatus, stripUndo, handleUndoGesture,
    handleInkEraseMany, handleInkEraseAlong } = useInkDocument({ focusedBlockId, readOnly, blocksRef, setBlocks,
    inkBySurface, inkPageNumber, inkEraserFilter, scheduleInk, liveRemoved,
    clearInkSelection: () => clearInkSelection(), boxUndo, undoBlocks, undoLinked: blockHistory.undoLinked, sayUndoable, setStatus,
    setInkFingerUndo });
  // The strokes (ink/useInkStrokes.js): a stroke, or a paste's strokes,
  // into the surface's current group, and the pill a snapped stroke
  // raises. A stroke while a held squeeze is open marks it used. A group
  // with a comment thread takes no more strokes (inkThreads, below).
  const { handleInkStroke, handleInkStrokes, handleHoldStatus } = useInkStrokes({ focusedBlockId, readOnly, inkStale,
    blocksRef, nbSheetsRef, applyInk, addSheetAfter,
    noteSqueezeUse: () => noteSqueezeUse(),
    undoableInkStatus, setStatus, closedGroup: (id) => inkThreads.closedGroup(id) });
  // The lasso selection and the handwriting clipboard
  // (ink/useInkSelection.js): the selection's edits and the selection
  // menu's actions, copy, cut and paste, Ctrl+D, select all, the ink half
  // of Transcribe, and Redraw as shapes and Convert to text, which ask the
  // AI with the chat's model, a task row each, and which a shared page's
  // view cannot use; the page tools' ink.aiWork washes the handwriting
  // they are working on. The text boxes' onSelect and the document above
  // reach clearInkSelection through an arrow, read only when they run. The
  // pictures the lasso takes, cut and pastes, and Convert to text's box,
  // are edited through stepBlocks.
  const { inkSelection, inkClip, inkAiWork, clearInkSelection, restoreInkSelection, handleInkSelect, handleInkMoveSelection,
    deleteInkSelection, selectionStill, handleInkAction, copyInkSelection, pasteInk, pasteAtCentre,
    duplicateInkSelection, selectAllInk } = useInkSelection({ focusedBlockId, readOnly, inkUi, lassoPictures: inkLassoPictures, blocksRef, inkOf,
    applyInk, flushInk, handleInkStrokes, pickInkTool, revealInkStrip, inkBySurface, pasteTarget,
    onBoxDeselect: () => { textBoxes.actions.onBoxDeselect(); pictures.deselect(); }, showInNotes,
    transcribe: shareMode ? null : askToTranscribe,
    ai: shareMode ? null : { model: chatSendModel, addTask, updateTask, pageTitle },
    stepBlocks, touchTextBox: textBoxes.touch, setStatus, undoableInkStatus,
    onComment: (id) => inkThreads.onComment(id), threaded: (id) => inkThreads.closedGroup(id) });
  // Comment threads on ink groups (comments/useInkThreads.js): the lasso
  // selecting a whole group with open threads opens its card, and a group
  // with a thread is closed to new strokes, the saver's deletion and a
  // merge; its predicates never change identity, so the arrows the ink
  // owners above hold read the latest threads.
  const inkThreads = useInkThreads({ threadsByBlock: comments.threadsByBlock, countByBlock: comments.countByBlock,
    loaded: comments.loaded, inkSelection, inkOf, openThread, triggerFlash });
  // The ruler (ink/useInkRuler.js): laid on the surface in view with its
  // row, Stamp line, and the layers' moves of it; it goes with the page,
  // and when the viewer stops showing its surface.
  const { inkRuler, toggleInkRuler, clearInkRuler, inkRulerOptions, stampRulerLine, onRuler } = useInkRuler({
    focusedBlockId, readOnly, inkStale, pdfHidden, notebook, inkUi, inkVisible, inkPen, setInkRulerUnits,
    pasteTarget, openRulerRow, closeRulerRow, handleInkStroke, undoableInkStatus });
  // The zoom window (ink/useInkZoom.js): opened on the PDF page in view,
  // its New line, the panel's and the layers' changes; another page, the
  // PDF hidden, the notebook view or a phone's layout close it.
  const { inkZoom, toggleInkZoom, inkZoomNewLine, changeInkZoom, handleInkZoom, clearInkZoom } = useInkZoom({
    focusedBlockId, readOnly, isPhone, pdfHidden, notebook, inkZoomHeight: appPrefs.inkZoomHeight, viewerWrapRef,
    pasteTarget, setStatus });
  // The strip closes (its ✕, Esc, the viewer's pen button): the tools
  // disarm, the ruler goes and the zoom window closes.
  function closeInkStrip() {
    closeInkStripUi();
    clearInkRuler();
    clearInkZoom();
  }
  // The strip's keys while it is open (ink/useStripKeys.js; the table is
  // ink/stripKeys.js): in the capture phase, so the page's block undo
  // never sees the stroke history's Ctrl+Z; Hold-E learns of an eraser
  // pass through noteInkEraseHold (handleInkEraseEnd).
  const { noteInkEraseHold } = useStripKeys({ inkUi, inkSelection, inkVisible, inkTools, setInkTools, pickInkTool,
    toggleInkShape, closeInkOptions, toggleInkOptions, closeInkStrip, inkUndo, boxUndo, hasBox: !!textBoxes.sel || !!pictures.sel,
    onPicture: readOnly ? null : pictures.pick,
    deleteInkSelection, selectionStill, clearInkSelection, restoreInkSelection, inkRuler, toggleInkRuler,
    inkZoom, toggleInkZoom, inkZoomNewLine, setStatus });
  // The pen's gestures (ink/usePencil.js): the Apple Pencil's double tap and
  // squeeze, or a stylus's button clicks, which a native app forwards, where
  // the page takes ink, and the host's ticks (hostHaptic, the page tools'
  // onHaptic); a stroke or an erase pass marks a held squeeze used through
  // noteSqueezeUse.
  const { hostHaptic, noteSqueezeUse } = usePencil({ readOnly,
    takesInk: (docView.shown && !!docView.spec?.ink) || nbSheets.length > 0,
    inkUi, inkVisible, previousTool, pickInkTool, applyPencilUi, inkSelection, selectionStill, restoreInkSelection,
    stripUndo, inkPencilTap, inkPencilSqueeze, inkHaptics });
  // A native app's own bar above the page (app/useShellBar.js): the View
  // menu's Server bar row, the way back once it is hidden; null elsewhere.
  const shellBar = useShellBar();
  // The eraser tool lifted (never the pen's eraser end or barrel, whose
  // lift is the switch): a held E (noteInkEraseHold) learns a pass was made; with
  // "Eraser returns to the last tool" on, back to the tool armed before it,
  // the options row closed — except while a held squeeze is open, whose
  // end goes back instead (noteSqueezeUse; the pass marks it used). A squeeze
  // that toggles the eraser (pencilActions.js) still toggles it: with the
  // return on, the lift gives back the tool armed before it and the next
  // squeeze arms the eraser again.
  function handleInkEraseEnd() {
    noteInkEraseHold();
    if (noteSqueezeUse()) return;
    inkEraserLifted();
  }

  // From the notes (a marker, an ink card, a picture's card): show an ink
  // group, a text box or a picture on its page and outline it. From the
  // page (Show note, a read-only click on ink, a box or a picture): show its
  // block in the notes.
  function showOnPage(id) {
    const b = findBlock(blocksRef.current, id);
    if (!b) return;
    setPhonePanel(null); // the compact shell: the page, not a panel over it
    const tb = isTextBox(b) ? normalizeTextBox(b.properties.text_box) : null;
    const pic = !tb && isPicture(b) ? normalizePicture(b.properties.picture) : null;
    if (!tb && !pic && isPicture(b)) return; // a picture whose file is missing is drawn nowhere
    const box = tb ? [tb.x, tb.y, tb.x + tb.w, tb.y + tb.h] : pic ? turnedBox(pic) : inkBounds(inkOf(id));
    const flash = () => (tb ? textBoxes.flashBox(id) : pic ? pictures.flashPicture(id) : setInkFlash({ id, nonce: Date.now() }));
    const sheetId = sheetOfBlock(blocksRef.current, id);
    if (sheetId && notebook) {
      nbScrollRef.current?.(sheetId, box);
      flash();
      return;
    }
    if (sheetId) {
      // A page among the notes: unfold what hides it, then the mark into view.
      reveal(sheetId);
      requestAnimationFrame(() => {
        const node = document.querySelector(`.noteSheet[data-sheet-id="${CSS.escape(sheetId)}"] .nbSheet`);
        const scroller = node?.closest(".blockList");
        if (!node || !scroller) return;
        const r = node.getBoundingClientRect(), k = r.width / normalizePaper(findBlock(blocksRef.current, sheetId)?.properties?.sheet).width;
        const y = r.top - scroller.getBoundingClientRect().top + (box ? box[1] * k : 0) - 120;
        scroller.scrollBy({ top: y, behavior: "smooth" });
      });
      flash();
      return;
    }
    // A box or a picture is on its pdf_page (it stores no position), an ink group where its position says.
    const position = tb || pic ? { pageNumber: b.properties.pdf_page } : b.properties.pdf_position;
    const wasHidden = pdfHidden;
    if (wasHidden) setPdfHidden(false);
    setTimeout(() => scrollToRef.current?.({ position, box: tb || (pic ? { y: box[1] } : undefined), offset: 120 }), wasHidden ? 300 : 0);
    flash();
  }
  function showInNotes(id) {
    scrollToBlock(id);
    reveal(id);
    // The compact shell brings the notes over the page: their panel, or a
    // page with sheets switches to its notes view.
    if (isPhone && !centerNotes) {
      if (hasSheets) setNotebookView(focusedBlockId, false);
      else { setNotesVisible(true); setPhonePanel("notes"); }
    }
  }

  // --- Sheets (docs/dev/notebooks.md) ---------------------------------------------
  // A sheet is a block carrying `sheet` (its paper): adding one is a tree
  // insert like any block's, so it syncs, merges and undoes like one. The
  // rules are the same in the notes view and the notebook view: a new page
  // goes right after a block, gets the paper of the page nearest before it
  // and starts folded (it shows its drawings itself). After a sheet its id
  // follows from that sheet's (sheetIdAfter), so two devices adding "the
  // page after this one" at once add one page; `once` adds none when that
  // page is there already (two strokes low on the last page add it once).
  function addSheetAfter(afterId, { once = false } = {}) {
    if (readOnly || !focusedBlockId) return "";
    const plan = sheetAfterPlan(blocksRef.current, afterId, makeId, { once });
    if (!plan) return "";
    if (!plan.add) return plan.id;
    setBlocks((prev) => insertSheetAfter(prev, afterId, plan.id));
    guideEvents.emit("sheet.created", { id: plan.id });
    return plan.id;
  }
  // The viewer's "Add page": after the last page.
  function addPageAtEnd() {
    const last = nbSheetsRef.current.at(-1);
    return last ? addSheetAfter(last.id) : "";
  }
  // "/note" in a block's editor (`rest`: its text without the command): the
  // block becomes the sheet when nothing else is in it, else a sheet goes in
  // after it.
  function insertSheetAt(blockId, rest) {
    if (readOnly) return;
    if ((rest || "").trim()) { addSheetAfter(blockId); return; }
    if (!findBlock(blocksRef.current, blockId)) return;
    setBlocks((prev) => blockToSheet(prev, blockId));
    guideEvents.emit("sheet.created", { id: blockId });
    saveNowRef.current = true;
    setView((v) => closeEditing(v, blockId));
  }
  function setSheetPaper(sheetId, paper) {
    if (readOnly) return;
    setBlocks((prev) => withSheetPaper(prev, sheetId, paper));
  }
  // Every sheet of the page gets `paper`.
  function applyPaperToAll(paper) {
    if (readOnly) return;
    setBlocks((prev) => withAllSheetsPaper(prev, paper));
  }
  // Leaving the page closes its paper menu.
  useEffect(() => { setPaperMenu(false); setNbCurrent(""); }, [focusedBlockId]);

  useEffect(() => { attachModeBlockIdRef.current = attachModeBlockId; }, [attachModeBlockId]);
  useEffect(() => { moveHighlightIdRef.current = moveHighlightId; }, [moveHighlightId]);

  // Attach mode (a note's ⊕ waits for a click on a highlight) and move mode
  // (a highlight waits for a selection) each end the other, so one banner
  // shows and the PDF's next click or selection means one thing. Both are
  // entered only through these; the setters are stable, so the callbacks
  // are too and BlockRow's memo holds.
  const startAttachMode = useCallback((blockId) => {
    setMoveHighlightId(null);
    setAttachModeBlockId(blockId);
  }, []);
  // A move started from a highlight's review card (its Move…) is
  // remembered, {id, prior} (prior: the card's step in effect, when it had
  // one): once the selection has moved the highlight, the card opens on it
  // again, as it was, its Move… pressed ("Moved"), pressing it again
  // undoing the move (pdf/HighlightReview.jsx). Any other start of a move
  // forgets it.
  const moveFromReviewRef = useRef(null);
  const startHighlightMove = useCallback((blockId) => {
    moveFromReviewRef.current = null;
    setAttachModeBlockId(null);
    setMoveHighlightId(blockId);
  }, []);
  const startReviewMove = useCallback((blockId, prior = null) => {
    startHighlightMove(blockId);
    moveFromReviewRef.current = { id: blockId, prior };
  }, [startHighlightMove]);

  // Escape ends whichever mode is on.
  useEffect(() => {
    if (!attachModeBlockId && !moveHighlightId) return;
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      setAttachModeBlockId(null);
      setMoveHighlightId(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [attachModeBlockId, moveHighlightId]);
  // Another page, or another file on this one, ends a move: the selection
  // it waits for would be on a PDF the highlight was not picked from.
  useEffect(() => { setMoveHighlightId(null); }, [focusedBlockId, docId, doc?.url]);

  // The highlight takes the selection's place, and its text as its quote:
  // a tree edit like any other (ops, undo). Whatever marked it lost or
  // placed by estimate goes with the old position. An area drag has no
  // text, so the highlight becomes an area with no quote, as one made the
  // usual way (null drops the key). A move started from the review card
  // opens it again on the highlight, "Moved", with what the move changed
  // for its undo (reviewStep), and the card's step before it when it had
  // one, which that undo goes back to.
  function moveHighlightTo(blockId, position, quote) {
    setMoveHighlightId(null);
    const from = moveFromReviewRef.current;
    const fromReview = from?.id === blockId;
    moveFromReviewRef.current = null;
    const block = blockId ? findBlock(blocks, blockId) : null;
    if (readOnly || !position || !block) return;
    setBlocks((prev) => updateBlockTree(prev, blockId, (b) => ({
      ...b,
      properties: applyPatch(b.properties, {
        pdf_position: position,
        quote: quote || (position.area ? null : b.properties?.quote),
      }),
    })));
    setStatus(t("Highlight moved."));
    triggerFlash(blockId);
    const step = fromReview ? reviewStep("moved", {
      position: block.properties?.pdf_position, quote: block.properties?.quote || "", prior: from.prior,
    }, position) : null;
    if (step) pdfReviewRef.current?.(blockId, { done: step, scroll: false });
  }

  // Use closest match on a lost highlight's review card: it takes the place
  // the run found for its passage below the account's threshold, and that
  // place's text (closestPlacement: lost, closest, doc_id, approx, match
  // and prev go), drawn solid. A tree edit like any other (ops, undo).
  function placeClosestMatch(blockId) {
    if (readOnly || !closestPlacement(findBlock(blocks, blockId)?.properties?.pdf_position)) return;
    setBlocks((prev) => updateBlockTree(prev, blockId, (b) => {
      const placed = closestPlacement(b.properties?.pdf_position);
      return placed ? { ...b, properties: applyPatch(b.properties, { pdf_position: placed.position, quote: placed.quote || null }) } : b;
    }));
    triggerFlash(blockId);
  }

  // Discard on the review card of a highlight to review, dashed or lost,
  // its passage not found as it was in the new PDF (highlightReview.js
  // discardStep): one with no note of its own, no child blocks and no
  // comment thread is deleted, as the highlight menu's Delete deletes it;
  // one that carries any stays as a note, its position its page alone, so
  // it leaves the PDF and the review queue. One tree edit either way (ops,
  // undo). The step goes back to the card, for its undo (Discarded pressed
  // again); `discardOf` says which of the two it would be, for its title.
  // `reviewNote(id)` is the note the block carries, which the card shows
  // (noteSummary: its text, the blocks under it, its comment threads).
  const discardThreads = (blockId) => comments.threadsByBlock.get(blockId)?.length || 0;
  const discardOf = (blockId) => discardOutcome(findBlock(blocks, blockId), discardThreads(blockId));
  const reviewNote = useCallback((blockId) => noteSummary(findBlock(blocks, blockId), comments.threadsByBlock.get(blockId)?.length || 0),
    [blocks, comments.threadsByBlock]);
  function discardReviewHighlight(blockId) {
    const step = readOnly ? null : discardStep(blocks, blockId, discardThreads(blockId));
    if (!step) return null;
    setBlocks((prev) => discardApplied(prev, blockId, step));
    return step;
  }

  // A step's button pressed again on the review card: the step it took
  // (Accept, Use closest match, a move started from it, Discard) taken
  // back, one more tree edit, so the app's own undo takes that back in
  // turn. Nothing when the highlight changed since (reviewUndone; a
  // discarded one back already), nor for an Accept that confirmed the
  // place a move or Use closest match on the card gave it (`confirms`: it
  // wrote nothing; the card is back on that step). `reviewPosition(id)`, the block's stored
  // pdf_position (null for no such block), tells the card whether its step
  // is still in effect (highlightReview.js stepInEffect): a deletion by
  // someone else, or a change, ends it.
  const reviewPosition = useCallback((blockId) => findBlock(blocks, blockId)?.properties?.pdf_position ?? null, [blocks]);
  function undoReviewStep(blockId, step) {
    if (readOnly || !step || step.confirms) return;
    if (step?.kind === "discarded") {
      setBlocks((prev) => discardUndone(prev, blockId, step));
      return;
    }
    const block = findBlock(blocks, blockId);
    if (!block || reviewUndone(block.properties, step) === block.properties) return;
    setBlocks((prev) => updateBlockTree(prev, blockId, (b) => {
      const props = reviewUndone(b.properties, step);
      return props === b.properties ? b : { ...b, properties: props };
    }));
  }

  // Accept on one pending after its PDF was replaced (matched roughly, or an
  // area placed by estimate: its review card, its menu), or the Document
  // menu's Accept all on every pending one of the page: the place stays, and
  // approx, match, prev and doc_id go (acceptedPosition), so it is drawn
  // solid and its rectangles are the current PDF's. One
  // tree edit like any other (ops, one undo step), however many it accepts.
  function acceptHighlightPlaces(ids) {
    const wanted = ids.filter((id) => findBlock(blocks, id));
    if (readOnly || !wanted.length) return;
    const accept = (b) => {
      const position = b.properties?.pdf_position;
      const accepted = acceptedPosition(position);
      return accepted === position ? b : { ...b, properties: { ...b.properties, pdf_position: accepted } };
    };
    setBlocks((prev) => wanted.reduce((tree, id) => updateBlockTree(tree, id, accept), prev));
    if (wanted.length > 1) setStatus(tn("{n} highlight accepted where it was placed.", "{n} highlights accepted where they were placed.", wanted.length));
  }
  const acceptHighlightPlace = (blockId) => acceptHighlightPlaces([blockId]);

  // The Document menu's "Find highlights in this PDF": the server's
  // relocate-highlights job for the page by hand (gamma/highlight_relocate.py),
  // for a PDF replaced before the job existed, or a run that did not finish.
  // While one of this account's runs places this page's highlights, the row
  // waits for it; a PDF shown from a URL alone has no stored file to read.
  const relocating = tasks.jobs.some((job) => job.kind === "relocate-highlights" && isActive(job)
    && job.params?.page_id === focusedBlockId);
  const pdfStored = !!fileHash(doc);
  // A PDF read where it lies on this computer (gamma/linked_files.py), or a
  // work folder's file, whose PDF is its file's wherever the server keeps
  // the bytes: no other file replaces it from the Document menu.
  const pdfLinked = inPlace(doc) || !!workFolderOf(focusedBlock);
  // The page as the PDF translated view sees it (translateState.scopeOf):
  // the page and the PDF it shows, together, since while the next PDF loads
  // the viewer still shows the last one, which this page must not name.
  // stored: the server reads its own copy for the paper's brief; a PDF read
  // through the proxy may have none, so the brief's request carries the
  // opening text. file: what the server keeps the paper's translations
  // under, the page's file (an upload, a duplicated page's, or for a PDF
  // opened from its link the copy the proxy saves under the link's hash).
  const pdfTransScope = { url: pdfUrl, pageId: focusedBlockId, stored: !!docIdOf(pdfUrl), file: fileHash(doc) };
  // The file "Forget this paper's translations" names: the page's, once the document
  // the viewer reports on (the one on screen) is the page's.
  const pdfTransFile = scopeOf(pdfTransState.url, pdfTransScope).file;
  // What the viewer keeps the PDF on screen under (its page entries' key) and
  // what its requests send: the settings' key, except for a paper this
  // browser left translated by a chat model while none is on offer now (a
  // connection removed): that paper opens under the key it was left under,
  // from what the workspace keeps (pdf/translateRecall.js openKey), and a job
  // on it asks that model, which needs its connection back. A selection
  // always asks what the settings name (translateSelection).
  const recalledKey = useMemo(() => (translateNoChatModel && pdfTransFile ? translateRecall?.keyOf(pdfTransFile) ?? null : null),
    [translateNoChatModel, pdfTransFile, translateRecall, recallRead]); // eslint-disable-line react-hooks/exhaustive-deps
  const paperKey = (translateNoChatModel && openKey(recalledKey, translateKey, { noChatModel: true })) || translateKey;
  const paperModel = modelOfKey(paperKey);
  const [translateChunk, translateBrief, translateLookup, translateSelection] = useMemo(() => {
    const settings = { lang: translateLang, model: paperModel, effort: translateEffort, speed: translateSpeed, keep: translateKeep };
    const own = paperModel === translateSendModel ? null : chunkRequest({ ...settings, model: translateSendModel });
    return [chunkRequest(settings), briefRequest(settings), lookupRequest(settings), own];
  }, [translateLang, paperModel, translateSendModel, translateEffort, translateSpeed, translateKeep]);
  // The translation the viewer shows over the page's PDF, which the page's
  // annotated PDF writes in place of the original text
  // (pdf/translateExport.js): only for the page's own stored file, the one
  // the export job reads (the hash of the page's own document names it;
  // exportFile). It is read under pdfUrl, the address the viewer opens for this page, so a last
  // page's translation still on screen while this one's PDF loads is never
  // read. Never in a share view, which cannot translate. The export reads it
  // as Export is pressed (tasks/useAppJobs.js runExport); the dialog says how
  // many pages it holds. A paper shown from its kept translations has only
  // the pages near where the reader went seeded: the rest are seeded as the
  // dialog opens on an annotated PDF (exportSeeding), through the lookup,
  // with no model call (translateCtl.seedAll), so the dialog counts the pages
  // the file will hold; until then it names no number and its Export waits.
  // The export seeds again, which reads only what is still unseeded.
  const translatedFile = () => (shareMode ? "" : exportFile(pdfUrl, doc));
  async function exportTranslation() {
    const file = translatedFile();
    if (file) await pdfTranslateCtl.current?.seedAll(pdfUrl);
    const layout = file && exportLayout(translateSnapshot(pdfUrl), paperKey,
      (pn, i, k) => translateSpan(pdfUrl, pn, i, k));
    return layout ? { doc_id: file, lang: translateLang, ...layout } : null;
  }
  function exportTranslated() {
    const pages = translatedFile() ? exportPages(translateSnapshot(pdfUrl), paperKey) : 0;
    return pages ? { pages, language: translateLangLabel || translateLang, counting: exportSeeding } : null;
  }
  const [exportSeeding, setExportSeeding] = useState(false);
  const exportsPdf = exportOpen && !exportFolder && exportOpts.format === "pdf";
  useEffect(() => {
    if (!exportsPdf || !translatedFile()) return;
    let live = true;
    setExportSeeding(true);
    Promise.resolve(pdfTranslateCtl.current?.seedAll(pdfUrl)).catch(() => {})
      .finally(() => { if (live) setExportSeeding(false); });
    return () => { live = false; setExportSeeding(false); };
  }, [exportsPdf, pdfUrl, paperKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // A folder's annotated PDF writes each paper as it would open
  // (pdf/folderTranslation.js): translated where this browser left it showing
  // its translation (translateRecall.openKey under the settings' key), read
  // with no viewer from the kept translations; the paper open in the viewer
  // as the viewer shows it (exportTranslation). Read as the dialog opens on
  // it (pdf/useFolderTranslations.js), so the dialog counts the papers and
  // its Export waits meanwhile. Never in a share view.
  const folderKeyOf = (doc) => openKey(translateRecall?.keyOf(doc), translateKey, { noChatModel: translateNoChatModel });
  const openHere = ({ page_id, doc_id }) => page_id === focusedBlockId && translatedFile() === doc_id;
  const folderTrans = useFolderTranslations({
    folder: exportOpen && exportOpts.format === "pdf" && !shareMode && translateRecall ? exportFolder : null,
    key: translateKey,
    papersOf: (folder) => folderPapers(pageBlocks, libTree, folder).filter((p) => openHere(p) || folderKeyOf(p.doc_id)),
    layoutOf: async (paper, signal) => {
      if (openHere(paper)) return exportTranslation();
      const key = folderKeyOf(paper.doc_id);
      return key && paperLayout({ url: uploadUrl(storedName(paper.doc_id, ".pdf")), pageId: paper.page_id, key, open: openPdfDocument,
        signal, lookup: lookupRequest({ lang: langOfKey(key), model: modelOfKey(key) }) });
    },
  });
  function exportFolderTranslations(folder) { return shareMode ? Promise.resolve([]) : folderTrans.translations(folder); }
  const folderTranslated = () => (folderTrans.translated && { ...folderTrans.translated, language: translateLangLabel || translateLang });
  // The Export dialog, preset to a format (transfers/transferFormats.js).
  function exportAs(format) {
    setExportFolder(null);
    setExportOpts((o) => ({ ...o, format }));
    setExportOpen(true);
  }
  async function relocateHighlights() {
    const pageId = focusedBlockId;
    if (!pageId || !pdfStored || readOnly) return;
    setOpenPopover(null);
    try {
      await tasks.start("relocate-highlights", { page_id: pageId });
    } catch (err) {
      setStatus(t("Could not start: {message}", { message: err.message }));
    }
  }

  // Both are tree edits like any other (ops, undo). A write of their own
  // plus a refetch would replace the tree under edits not yet saved.
  function linkHighlightToBlock(blockId, highlight) {
    // Store a pointer to the existing highlight (its block id) and its page,
    // NOT a copy of its place: that would draw a second highlight on the PDF
    // at the same spot. The jump logic resolves linked_highlight_id → scrolls
    // to the real highlight. A region has no page: the pointer alone.
    const page = highlight.position?.pageNumber;
    setBlocks((prev) => updateBlockTree(prev, blockId, (b) => ({
      ...b,
      properties: applyPatch(b.properties, { linked_highlight_id: highlight.id,
        ...(page ? { pdf_position: { pageNumber: page } } : {}) }),
    })));
    setAttachModeBlockId(null);
  }

  function unlinkHighlightFromBlock(blockId) {
    setBlocks((prev) => updateBlockTree(prev, blockId, (b) => ({
      ...b, properties: applyPatch(b.properties, { linked_highlight_id: null }),
    })));
  }

  // Entering AI settings initializes prompt drafts; navigation guards protect
  // any subsequent edits until Save or Cancel.
  useEffect(() => {
    if (!isAiPane(settingsOpen)) return;
    setPromptDraft(chatSystem || aiInfo?.default_prompt || "");
    setMetaPromptDraft(metaPrompt || aiInfo?.metadata_prompt || "");
    setCitePromptDraft(citePrompt || aiInfo?.cite_prompt || "");
    setAgentPromptDraft(agentSystem || aiInfo?.agent_prompt || "");
  }, [settingsOpen]);


  // --- reference links: PDF text regions manually linked to papers ----------

  // Match a URL against the library: DOI or arXiv id already known → that page.
  // Any document link (PDF annotation or manual reference link): if the target
  // paper is already in the library, open it; otherwise ask fetch-vs-browser.
  function handleDocLink(url) {
    const pid = findPageForUrl(url, homeBlocks);
    if (pid) {
      setStatus(t("Already in your library — opening."));
      openBlock(pid, { pushNav: true });
      return;
    }
    setLinkPrompt(url);
  }

  // A link region's target (docs/dev/pdf_citations.md): a URL, or a page
  // and optionally a highlight there (`link_block_id`, its block id). A key
  // the target does not set is dropped (null), all three when the link is
  // removed.
  function linkProps(props, target) {
    return applyPatch(props, { link_url: target.url || null, link_page_id: target.pageId || null,
      link_block_id: target.blockId || null });
  }

  // The link dialog's choice: a new link region over its selection, or the
  // link of the highlight it was opened on changed or removed.
  function createLinkHighlight(target) {
    const ld = linkDialog;
    if (!ld) return;
    setLinkDialog(null);
    if (ld.editBlockId) {
      // Re-pointing (or clearing) the link on an existing highlight block
      setBlocks(updateBlockTree(blocks, ld.editBlockId, (b) => ({
        ...b,
        properties: linkProps(b.properties, target),
      })));
      setStatus(target.url || target.pageId ? t("Link updated.") : t("Link removed."));
      return;
    }
    const id = makeId();
    let next = addHighlightAsBlock(blocks, { id, quote: ld.quote, position: ld.position, color: LINK_COLOR });
    next = updateBlockTree(next, id, (b) => ({ ...b, properties: linkProps(b.properties, target) }));
    setBlocks(next); // autosave persists
    setStatus(t("Reference linked."));
  }

  // Zoom in/out steps to the next multiple of 20%, anchored on the current
  // effective scale (so it also works from fit-width, e.g. 87% → 100%).
  function zoomStep(dir) {
    const cur = Math.round(pdfEffScale * 100);
    const next = dir > 0 ? (Math.floor(cur / 20) + 1) * 20 : (Math.ceil(cur / 20) - 1) * 20;
    zoomTo(next / 100);
  }
  // Every zoom change goes through here — buttons, Ctrl+scroll, fit-width —
  // so applying the scale and announcing it can't come apart.
  function zoomTo(next) {
    if (next === "page-width") {
      setPdfScale("page-width");
      postPill("pdf-zoom", { msg: t("Fit to width"), final: true });
      return;
    }
    const s = clampZoom(next);
    setPdfScale(String(Math.round(s * 10000) / 10000));
    postPill("pdf-zoom", { msg: t("Zoom {n}%", { n: Math.round(s * 100) }), final: true });
  }

  // A highlight's id is its block's.
  function jumpToHighlightId(id, additive) {
    setPhonePanel(null); // the compact shell: the PDF, not a panel over it
    if (pdfHidden) {
      pendingJumpRef.current = id;
      setPdfHidden(false);
      return;
    }
    // Try own highlight first
    const target = highlights.find((h) => h.id === id);
    if (target) {
      scrollToRef.current({ position: target.position });
      triggerFlash(id);
      // Same chat effect as clicking the highlight on the PDF itself.
      addHighlightToChat(target, additive);
      return;
    }
    const block = findBlock(blocks, id);
    // Block was linked to an existing highlight via attach mode
    const linkedId = block?.properties?.linked_highlight_id;
    if (linkedId) {
      const linkedTarget = highlights.find((h) => h.id === linkedId);
      if (linkedTarget) {
        scrollToRef.current({ position: linkedTarget.position });
        triggerFlash(linkedId);
        addHighlightToChat(linkedTarget, additive);
        return;
      }
    }
    // Fallback: page-level jump
    const page = block?.properties?.pdf_position?.pageNumber;
    if (page) scrollToRef.current({ position: { pageNumber: page } });
  }

  const treeBlocks = useMemo(() => flattenBlocks(blocks), [blocks]);
  const treeById = useMemo(() => blocksById(treeBlocks), [treeBlocks]);
  // Every row's [[ref]] and Gamma-link labels, resolved once per tree
  // change (editor/refLabels.js); a row's entry keeps its identity while
  // its labels read the same, so a memoized row (BlockTree.jsx) does not
  // re-render for a keystroke elsewhere.
  const refLabelsPrevRef = useRef(new Map());
  const refLabelsById = useMemo(() => {
    const next = refLabelsByBlock(blocks, treeById, refCache, refLabelsPrevRef.current);
    refLabelsPrevRef.current = next;
    return next;
  }, [blocks, treeById, refCache]);
  // The row callbacks are made inline in the render below (rowProps). The
  // rows get one stable wrapper per name that calls the latest one
  // (shared/lib/stableActions.js), so a memoized row re-renders only when
  // one of its data props changed; a name that is null or undefined (a
  // feature the row gates on) stays so.
  const stableRowProps = useMemo(makeStableActions, []);
  useEffect(() => { if (!notebook) setPaperMenu(false); }, [notebook]);
  // Turning scribble-to-erase on explains the gesture, once a session.
  const scribbleHintRef = useRef(true);
  const setScribbleErase = (on) => {
    setInkScribbleErase(on);
    if (!on || !scribbleHintRef.current) return;
    scribbleHintRef.current = false;
    setStatus(t("Scribble to erase is on: a fast, dense back-and-forth with the pen erases the strokes under it"));
  };
  // The notes view opens at the reader's place on the sheets (tabScrollRef;
  // the notebook view takes it as `place`), unless a jump to a block is on its
  // way there. Read before that jump's effect spends it, applied once the
  // sheets among the notes have measured their size.
  useLayoutEffect(() => {
    const place = !notebook && hasSheets && !pendingBlockScrollRef.current && tabScrollRef.current[focusedBlockId]?.place;
    if (!place) return undefined;
    const raf = requestAnimationFrame(() => {
      const list = liveNotesList();
      if (list) showPlace(list, place);
    });
    return () => cancelAnimationFrame(raf);
  }, [notebook, focusedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps
  // Kept by identity while the numbering is the same (nbSheets is derived
  // from the tree on every edit): a row prop of every block.
  const sheetNumbersPrev = useRef(new Map());
  const sheetNumbers = useMemo(() => keepIfSame(sheetNumbersPrev, sheetNumbersOf(nbSheets), sameMap),
    [nbSheets]);
  nbSheetsRef.current = nbSheets;
  // The page tools every surface's layers read (markup/PageTools.jsx), and
  // per surface its marks (markup/MarkupLayers.jsx): a PDF page's ink groups
  // by their position's page, a sheet's by its id, its text boxes and its
  // pictures, with the lasso selection, the selected box, the selected
  // picture and the flashes on the surface they are on.
  const inkFeaturesToldRef = useRef(null); // the page whose newer-ink pill was shown
  const pageActions = useStableActions({
    onStroke: handleInkStroke, onEraseAlong: handleInkEraseAlong, onEraseEnd: handleInkEraseEnd,
    onEraseMany: handleInkEraseMany, onSelect: handleInkSelect, onUndoGesture: handleUndoGesture, onHoldStatus: handleHoldStatus,
    onAction: handleInkAction, onMoveSelection: handleInkMoveSelection, onJump: showInNotes, ...textBoxes.actions,
    ...pictures.actions,
    // a held pen or mouse, or a right-click, asking for the clipboard at a page point
    onPaste: pasteInk,
    onInkLive: handleInkLive,
    // a held shape or a pen lasso's selection, ticking under a native host that plays haptics
    onHaptic: hostHaptic,
    // the zoom window's box dragged or resized, or a blank tap sending it there
    onZoom: handleInkZoom,
    // the ruler moved, turned or stretched on its surface (null: put away)
    onRuler,
    onFingerScroll: () => setStatus(t("Fingers scroll while a pen draws"),
      { label: t("Draw with finger"), run: () => setInkPenOnly(false) }),
    // A drawing with a pen style, a key or a channel this build does not
    // know (ink.js inkFeatures) is drawn plainly: said once per page visit.
    onInkFeatures: () => {
      if (inkFeaturesToldRef.current === focusedBlockIdRef.current) return;
      inkFeaturesToldRef.current = focusedBlockIdRef.current;
      setStatus(t("Some handwriting here comes from a newer Gamma and is drawn plainly; update to see it as drawn"));
    },
  });
  // The chords the selection menu's titles name ("Copy · Ctrl+C").
  const inkKeys = useMemo(() => {
    const label = (id) => { const chord = commandChord(id, keybindings); return chord ? chordLabel(chord) : ""; };
    return { copy: label("ink.copy"), cut: label("ink.cut"), duplicate: label("ink.duplicate") };
  }, [keybindings]);
  const pageTools = useMemo(() => ({
    readOnly,
    ink: { tool: inkTool, penTool: inkPenTool, penWritesPdf: inkPenWritesPdf, penOnly: inkPenOnly, pressure: inkPressure, eraserMode: inkEraserMode,
      eraserSize: inkEraserSize, eraserFilter: inkEraserFilter, drawHold: inkDrawHold,
      scribble: inkScribbleErase, fingerUndo: inkFingerUndo, lassoMode: inkLassoMode, lassoPictures: inkLassoPictures, clip: inkClip,
      keys: inkKeys, hoverPreview: inkHoverPreview, ruler: readOnly ? null : inkRuler, rulerUnits: inkRulerUnits,
      zoom: readOnly ? null : inkZoom, aiWork: inkAiWork, canComment: commentsOn && canComment },
    text: { armed: textArmed, style: textBoxStyle, zotero: zoteroLinked },
    actions: pageActions,
  }), [readOnly, inkTool, inkPenTool, inkPenWritesPdf, inkPenOnly, inkPressure, inkEraserMode, inkEraserSize, inkEraserFilter, inkClip, inkKeys,
    inkDrawHold, inkScribbleErase, inkFingerUndo, inkLassoMode, inkLassoPictures, inkHoverPreview, textArmed, textBoxStyle, zoteroLinked,
    pageActions, inkRuler, inkRulerUnits, inkZoom, inkAiWork, commentsOn, canComment]);
  // --- Recordings (docs/dev/recording.md) ---------------------------------------
  // The page's recording blocks and every block stamped with the moment it
  // was written, each list kept while its contents are the same, so the
  // player's marks and the rows' glyphs re-render only for a real change.
  const prevRecRef = useRef({ json: "", value: [] });
  const recordingBlocks = useMemo(() => keepIfSameJson(prevRecRef, recordingList(blocks)), [blocks]);
  const prevStampedRef = useRef({ json: "", value: [] });
  const stampedBlocks = useMemo(() => keepIfSameJson(prevStampedRef, stampedList(blocks)), [blocks]);
  // Only the id of the recording playing, so App never re-renders for a frame.
  const playingRecordingId = useSyncExternalStore(playback.subscribe, () => playback.now()?.scope.recording || null);
  const recordingNow = useSyncExternalStore(recorderStore.subscribe, recorderStore.coarseNow);
  // A new recording block, or a bookmark, goes at the page's top level like
  // an ink group; the tree's own save path inserts it.
  const insertRecordingBlock = useCallback(({ content, properties }) => {
    if (!focusedBlockIdRef.current) return null;
    const id = makeId();
    setBlocks((prev) => [...prev, { id, children: [], content, properties }]);
    return id;
  }, []);
  const transcribeRecording = useCallback((blockId) => {
    const b = findBlock(blocksRef.current, blockId);
    if (!b) return;
    tasks.start("transcribe", { block_id: blockId, model: dictationModel, language: dictationLang }, { download: "none" })
      .catch((err) => setStatus(String(err?.message || err)));
  }, [tasks, dictationModel, dictationLang]);
  useEffect(() => {
    recorderStore.configure({
      setStatus, insertBlock: insertRecordingBlock, leadIn: () => replayLeadIn * 1000,
      // a recording saved: the guide's recording tour, then the transcript Settings may ask for
      onDone: (blockId) => {
        guideEvents.emit("recording.stopped", { id: blockId });
        if (recordingTranscribe) transcribeRecording(blockId);
      },
    });
  }, [insertRecordingBlock, replayLeadIn, recordingTranscribe, transcribeRecording]);
  // A closed or crashed tab's unsaved audio, found at start: put up and
  // its block finished, silently; a block that is gone takes it with it.
  useEffect(() => {
    if (!authUser?.user || shareMode) return;
    let stale = false;
    recorderStore.leftBehind().then(async (entries) => {
      for (const entry of entries) {
        if (stale) return;
        const r = await recorderStore.recover(entry);
        if (r.ok) setStatus(t("An interrupted recording was recovered."));
      }
    }).catch(() => {});
    return () => { stale = true; };
  }, [authUser?.user, shareMode]);
  // Play a recording: its clock drives the page and the notes. A tap on
  // something written then plays from just before that moment.
  // The display choices as the player reads them when it starts (a ref, so
  // a change of mode or speed re-renders the player and nothing else).
  const replayPrefRef = useRef({});
  replayPrefRef.current = { leadIn: replayLeadIn * 1000, rate: replayRate, mode: replayMode };
  const playRecording = useCallback((blockId, { atWall = null } = {}) => {
    const b = findBlock(blocksRef.current, blockId);
    if (!b) return;
    if (recorderStore.coarseNow()) { setStatus(t("Stop the recording first.")); return; }
    const clock = recordingClock(normalizeRecording(b.properties.recording));
    if (!clock.duration) { setStatus(t("This recording has no audio.")); return; }
    const source = audioSource(clock, {
      onError: () => { playback.stop(); setStatus(t("The browser could not play this recording's audio.")); },
    });
    const { leadIn, rate, mode } = replayPrefRef.current;
    const at = atWall != null ? Math.max(0, (clock.toT(atWall) ?? 0) - leadIn) : 0;
    disarmInk(); // taps seek rather than draw
    clearInkSelection();
    playback.play(clock, { recording: blockId }, { source, at, rate, mode, onEnd: "hold", leadIn });
    guideEvents.emit("recording.played", { id: blockId });
  }, []);
  const toggleRecording = useCallback(() => {
    if (recorderStore.coarseNow()) { recorderStore.stop(); return; }
    if (!focusedBlockIdRef.current) return;
    playback.stop();
    recorderStore.start(focusedBlockIdRef.current);
  }, []);
  // Leaving a page stops what plays and ends a recording in progress.
  useEffect(() => () => { playback.stop(); recorderStore.stop(); }, [focusedBlockId]);
  const recordingCtx = useMemo(() => ({
    play: playRecording,
    transcribe: readOnly ? null : transcribeRecording,
    covers: (t0) => Number.isFinite(t0) && !!recordingAt(recordingBlocks, t0),
    playAt: (wall) => {
      const b = recordingAt(recordingBlocks, wall);
      if (!b) return;
      if (playback.now()?.scope.recording === b.id) playback.seekBefore(wall);
      else playRecording(b.id, { atWall: wall });
    },
  }), [playRecording, transcribeRecording, readOnly, recordingBlocks]);
  // Follow the writing: what the player names, shown. The player keeps the
  // one showTick; a group is shown through the latest render's showOnPage,
  // so the notebook view and the PDF are taken as they are now.
  const showOnPageRef = useRef(showOnPage);
  showOnPageRef.current = showOnPage;
  const showTick = useCallback((tick) => {
    if (tick.kind === "ink") { showOnPageRef.current(tick.group); return; }
    reveal(tick.id);
    requestAnimationFrame(() => {
      document.querySelector(`.blockRowWrap[data-block-id="${CSS.escape(tick.id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const replayPrefs = useMemo(() => ({ mode: replayMode, setMode: setReplayMode, rate: replayRate, setRate: setReplayRate,
    follow: replayFollow, setFollow: setReplayFollow }), [replayMode, setReplayMode, replayRate, setReplayRate, replayFollow, setReplayFollow]);
  const playerBar = playingRecordingId ? (
    <PlayerBar block={findBlock(blocksRef.current, playingRecordingId)} inkBlocks={inkBlocks} blocks={stampedBlocks}
      prefs={replayPrefs} onShow={replayFollow ? showTick : null} />
  ) : null;

  const marks = useMarks({ ink: inkBySurface, inkSelection, inkFlash, boxes: textBoxes.bySurface, boxSel: textBoxes.sel,
    boxFlash: textBoxes.flash, pictures: pictures.bySurface, pictureSel: pictures.sel, pictureFlash: pictures.flash });
  const homeMode = !focusedBlockId && lib.browse;
  // The one page selected on the home library (F2 renames it), or "".
  const homePick = homeMode && lib.organize && selectedPages.size === 1 && !selectedFolders.size && !selectedLabels.size
    ? [...selectedPages][0] : "";
  // What a step of the block history did, in the status pill. `inkStayed`:
  // a lasso step's pictures stepped without its strokes (stepBlockHistory).
  function reportUndo(applied, redo) {
    setStatus(applied?.blocked ? (redo ? t("Can't redo: someone else changed this since.") : t("Can't undo: someone else changed this since."))
      : applied?.inkStayed ? (redo ? t("Redone: {what} — the handwriting stays as it is: it changed since.", { what: applied.description })
        : t("Undone: {what} — the handwriting stays as it is: it changed since.", { what: applied.description }))
      : applied?.kept ? (redo ? t("Redone: {what} — kept what someone else changed since.", { what: applied.description })
        : t("Undone: {what} — kept what someone else changed since.", { what: applied.description }))
      : applied ? `${redo ? t("Redone") : t("Undone")}: ${applied.description}.`
      : (redo ? t("Nothing to redo in notes.") : t("Nothing to undo in notes.")));
  }
  // One step of the block history. The block half of a linked step
  // (`link`: a lasso step's pictures, or Convert to text's box) steps its
  // strokes' half in the stroke history too, when that is the stroke
  // history's newest entry (useInkDocument.js inkUndo). The pill then names
  // the step as the strip's does ("selection move"), else it says the
  // handwriting stays.
  function stepBlockHistory(redo, inEditor) {
    const applied = blockHistory.undo(redo, inEditor);
    if (!applied?.link) return applied;
    const strokes = inkUndo(redo, applied.link);
    return strokes ? { ...applied, description: t(strokes.label) } : { ...applied, inkStayed: true };
  }
  // Undo / Redo by a press rather than Ctrl+Z: the strip's for text boxes,
  // the editing bar's and the iPad keyboard's (rowProps.onUndo). A press
  // leaves an open editor focused, whose undo it then is, as Ctrl+Z in it
  // would be.
  function undoBlocks(redo) {
    reportUndo(stepBlockHistory(redo, !!document.activeElement?.closest?.(".cm-editor")), redo);
  }
  // A change the pill offers to take back, with Undo beside it, which
  // clears the pill and runs `run`. A stroke edit (a snapped shape, a
  // scribble erase, a stamped line) raises it through
  // ink/useInkDocument.js's undoableInkStatus, whose Undo steps the
  // stroke history as the strip's Undo button does.
  function sayUndoable(msg, run) {
    setStatus(msg, { label: t("Undo"), run: () => {
      postPill("status", null);
      run();
    } });
  }
  // The block history's: its Undo runs the app.undo command, so the
  // message names no key.
  function undoableStatus(msg) {
    sayUndoable(msg, () => commandById("app.undo").run(appCmdRef.current));
  }
  // What the keys and the handle's menu do to a selection of notes
  // (editor/useBlockSelection.js): `ids` are its roots in document order,
  // each taking the blocks under it along. A change of the tree is one
  // setBlocks, so one undo step and one batch for the room. What comes back
  // is selected next ([] lets the selection go); nothing keeps it.
  function blockSelectionAction(action, ids) {
    const tree = blocksRef.current;
    const n = ids.length;
    const roots = ids.map((id) => findBlock(tree, id)).filter(Boolean);
    // A locked page's notes stay as its file has them (vault/notesLock.js).
    const locked = (change, dir) => notesLocked && ids.some((id) => touchesLocked(tree, id, change, dir));
    if (action === "copy" || action === "cut") {
      const text = roots.map((b) => subtreeMarkdown(b, 0)).join("\n");
      sourceCopied(text, { outline: true });
      copyText(text);
      if (action === "copy") {
        setStatus(tn("Copied {n} block as markdown", "Copied {n} blocks as markdown", n));
        return undefined;
      }
    }
    if (readOnly) return undefined;
    switch (action) {
      case "cut":
      case "delete":
        if (locked("delete")) return undefined;
        setBlocks(removeBlocks(tree, ids));
        undoableStatus(tn("{n} block deleted.", "{n} blocks deleted.", n));
        return [];
      case "indent":
      case "outdent": {
        if (locked(action)) return undefined;
        const next = action === "indent" ? indentBlocks(tree, ids) : outdentBlocks(tree, ids);
        if (next === tree) return undefined;
        setBlocks(next);
        // under a parent that may be folded
        setView((v) => ids.reduce((w, id) => revealBlock(next, w, id), v));
        return undefined;
      }
      case "moveUp":
      case "moveDown": {
        const dir = action === "moveUp" ? -1 : 1;
        if (locked("delete") || (notesLocked && touchesLocked(tree, ids[dir < 0 ? 0 : n - 1], "move", dir))) return undefined;
        const next = moveSiblings(tree, ids, dir);
        if (next !== tree) setBlocks(next);
        return undefined;
      }
      case "duplicate": {
        // On a locked page a copy would be a note, as onDuplicate's.
        if (notesLocked) return undefined;
        const copies = roots.map(freshCopy);
        setBlocks(insertBlocks(tree, { type: "sibling", id: ids[n - 1], above: false }, copies));
        setStatus(tn("{n} block duplicated.", "{n} blocks duplicated.", n));
        return copies.map((b) => b.id);
      }
      case "chat":
        if (!shareMode) roots.forEach(addBlockToChat);
        return undefined;
      case "move":
        if (!locked("delete")) pickMovePage({ blockIds: ids });
        return undefined;
      default:
        return undefined;
    }
  }
  bindingsRef.current = keybindings;
  appCmdRef.current = {
    shareMode, homeMode, readOnly, hasPage: !!focusedBlockId, hasPdf: !!pdfUrl && !homeMode, hasDoc: !!doc && !homeMode, commentsOn,
    phone: isPhone, groups: groupsOf(tabLayout).length,
    splitTab: (side) => { if (focusedBlockId) splitTab(focusedBlockId, side); },
    joinGroups: joinTabGroups,
    search: (all) => {
      if (!all) {
        const homeFind = document.querySelector(".homeFindInput");
        if (homeFind) { homeFind.focus(); homeFind.select(); return true; }
      }
      setOpenPopover((p) => (p === "search" && !all ? null : "search"));
      return true;
    },
    palette: (prefix) => {
      setOpenPopover(null);
      setQuickOpen((v) => (v && v.prefix === prefix ? null : { prefix }));
    },
    back: () => goBackNavRef.current?.(),
    mouseBack: () => {
      if (navStackLen) goBackNavRef.current?.();
      else if (!homeMode) goHome();
    },
    undo: (redo) => {
      const active = document.activeElement;
      const inEditor = !!active?.closest?.(".cm-editor");
      if (!inEditor && isTextField(active)) return false;
      const applied = stepBlockHistory(redo, inEditor);
      if (applied || inEditor || !active || active === document.body) reportUndo(applied, redo);
      // Always swallowed in an editor: the browser's native contenteditable
      // undo would otherwise mutate CodeMirror's DOM behind its back.
      return inEditor || !!applied;
    },
    // On the home library F2 renames the one selected page in place.
    renameTitle: () => {
      if (homeMode) {
        if (homePick && !work.refused([homePick], (rules) => rules.rename, "named")) setHomeEditingId(homePick);
        return;
      }
      if (titleLocked) { setStatus(refusalText("named")); return; }
      setTitleDraft(pageTitle || t("Untitled"));
      setTitleEditing(true);
    },
    homePick,
    homePicks: homeMode && lib.organize ? selectedPages.size : 0,
    deletePages: () => deletePages([...selectedPages]),
    organize: lib.organize,
    recentlyDeleted: () => { preloadTrash(); setOpenPopover(null); setTrashOpen(true); },
    // On a phone Chat and Notes are the bottom bar's panels, which the
    // desktop windows' hidden flags don't drive.
    toggleChat: () => {
      if (isPhone) { setPhonePanel((p) => (p === "chat" ? null : "chat")); return; }
      if (chatHidden) guideEvents.emit("window.shown", { id: "chat" });
      setChatHidden((v) => !v);
    },
    // The Comments window; on a phone, its panel over the page.
    toggleComments: () => {
      if (isPhone) { setPhonePanel((p) => (p === "comments" ? null : "comments")); return; }
      if (commentsHidden) guideEvents.emit("window.shown", { id: "comments" });
      setCommentsHidden((v) => !v);
    },
    togglePdf: () => {
      if (pdfHidden) guideEvents.emit("window.shown", { id: "pdf" });
      setPdfHidden((v) => !v);
    },
    toggleNotes: () => {
      if (isPhone) { setNotesVisible(true); setPhonePanel((p) => (p === "notes" ? null : "notes")); return; }
      if (!notesVisible) guideEvents.emit("window.shown", { id: "notes" });
      setNotesVisible((v) => !v);
    },
    openSettings: (pane) => { setOpenPopover(null); setSettingsOpen((cur) => pane || cur || "account"); },
    exportAs,
    downloadPdf: () => exportRawPdf(),
    importDialog: () => { setOpenPopover(null); setImportOpen(true); },
    newPage: () => createPage(),
    // As the topbar's link button: load the open page's share settings, then
    // show the popover (a bare toggle showed a stale target or none).
    share: () => openPageShare(focusedBlockId),
    metadata: () => openMetaPopover(),
    // under the page title, else centred
    pageProperties: () => openProps(focusedBlockId, liveSidebarRef.current?.querySelector(".titleText") || null),
    attach: () => setOpenPopover((p) => (p === "attach" ? null : "attach")),
    reportProblem: () => { setOpenPopover(null); setReportOpen(true); },
    // The handwriting group (docs/dev/handwriting.md "Copy, cut and paste"):
    // the guards, and the actions the keys, the palette, the strip's More
    // menu and the selection menu share.
    ...inkCommandContext({ inkUi, inkTool, inkSelection, readOnly, inkClip },
      { copyInkSelection, pasteAtCentre, duplicateInkSelection, selectAllInk }),
    // The translated view, where the translate button applies (a PDF on
    // screen outside a share, as the viewer's tools are): the toggle shows or
    // hides what is translated, or translates the page being read when
    // nothing is, as the button's click. A closed PDF, or a phone's panel over
    // it, starts no job the reader cannot see.
    translation: !shareMode && !homeMode && docView.viewer === "pdf" && !docView.hidden && !(isPhone && phonePanel) ? {
      ...pdfTransState,
      toggle: () => {
        if (pdfTransState.pages > 0) pdfTranslateCtl.current?.setShown(!pdfTransState.shown);
        else pdfTranslateCtl.current?.translatePage();
      },
      page: () => pdfTranslateCtl.current?.translatePage(),
      document: () => pdfTranslateCtl.current?.translateDoc(),
    } : null,
    // The text highlighters, where the strip's apply: on or off, the next;
    // the pill names the one on.
    highlighter: !readOnly && docView.viewer === "pdf" && !docView.hidden ? {
      toggle: () => sayHighlighter(textHighlighter.toggle(null)),
      next: () => sayHighlighter(textHighlighter.next()),
    } : null,
  };
  function sayHighlighter(id) {
    const p = id ? textHighlighter.list.find((x) => x.id === id) : null;
    setStatus(p ? t("{name} on.", { name: presetName(p) }) : t("Text highlighter off."));
  }
  // What the command palette lists right now: the app commands that apply,
  // then the block commands that work on the focused row without an editor.
  function paletteCommands() {
    const ctx = appCmdRef.current;
    const entry = (cmd, run) => ({
      id: cmd.id, label: cmd.label, group: cmd.group, run,
      keyLabel: effectiveKeys(cmd, keybindings).map((k) => chordLabel(k)).join(" · "),
    });
    const out = APP_COMMANDS
      .filter((cmd) => cmd.palette !== false && (!cmd.when || cmd.when(ctx)))
      .map((cmd) => entry(cmd, () => cmd.run(ctx)));
    const row = rowPropsRef.current;
    const block = !homeMode && focusedId ? findBlock(blocks, focusedId) : null;
    // read-only, as the hotkeys: only the ones that change no note (a viewer comments)
    if (row && block) {
      const bctx = { block, tree: blocks, view, folded: isFolded(block, view), readOnly, editor: null, row };
      for (const cmd of BLOCK_COMMANDS) {
        if (cmd.palette === false || cmd.needsEditor || (cmd.edits && readOnly) || (cmd.when && !cmd.when(bctx))) continue;
        out.push(entry(cmd, () => cmd.run(bctx)));
      }
    }
    return out;
  }
  // The guide (docs/dev/onboarding.md): tours point at data-guide anchors
  // and advance on the events emitted below — some are offered by those
  // events (guide/triggers.js); never in the share view. They count the
  // library's own pages: a sub-page is filed with its parent.
  const libraryBlocks = useMemo(() => homeBlocks.filter((b) => !b._parentPage), [homeBlocks]);
  const unfiledLibrary = useMemo(() => libraryBlocks.length >= 10
    && libraryBlocks.every((b) => !b.properties?.folders?.length && !b.properties?.labels?.length), [libraryBlocks]);
  // A library big enough that folders and labels start to pay: the
  // "Organize your library" tour is offered once, past the folders hint.
  const growingLibrary = useMemo(() => libraryBlocks.length >= 20, [libraryBlocks]);
  // The open page carries enough of the user's own work to be worth
  // taking out of Gamma: the export hint.
  const annotatedPage = useMemo(() => blocksToHighlights(blocks).length >= 5, [blocks]);
  // Nothing in the library yet but the seeded Welcome page and Sketchbook, once the listing
  // has come back: the first tour is offered on it, and the library shows
  // "Start your library".
  const freshLibrary = useMemo(() => homeLoaded && isFreshLibrary(libraryBlocks), [homeLoaded, libraryBlocks]);
  // What the two "try this" hints need to know (guide/tours/hints.js), each
  // learnt once per load and never again: whether Gamma Connector is in
  // this browser (its content script answers; the desktop app's Connector
  // lives in the system browser, out of reach, so it stays unknown and
  // nothing is suggested), whether this account could link a Gamma Cloud
  // account but has not, and whether a setting that travels with the
  // account was changed here — the moment carrying settings elsewhere
  // starts to mean something.
  // Nothing is asked where nothing would be suggested ("Suggest tours" off),
  // nor in a native app's web view, where no extension can run to answer.
  const [connectorHere, setConnectorHere] = useState(undefined);
  useEffect(() => {
    if (shareMode || !authUser?.user || !suggestTours || IS_DESKTOP || nativeHost()) return;
    let live = true;
    askConnectorHere().then((here) => { if (live) setConnectorHere(here); });
    return () => { live = false; };
  }, [shareMode, authUser?.user, suggestTours]);
  const [cloudLink, setCloudLink] = useState(null); // {identity, enabled, connected}
  const cloudLogin = !!serverConfig?.cloud?.enabled;
  useEffect(() => {
    if (shareMode || !cloudLogin || !authUser?.user || authUser.is_guest || !suggestTours) { setCloudLink(null); return; }
    let live = true;
    apiJson(`${API}/auth/cloud/status`).then((d) => { if (live) setCloudLink(d); }).catch(() => {});
    return () => { live = false; };
  }, [shareMode, cloudLogin, authUser?.user, authUser?.is_guest, suggestTours]);
  // A setting of this account's own making, not the profile arriving: the
  // first push AFTER the sync has been quiet once (a profile the server
  // never held reads as every entry pending until its seeding push lands,
  // app/prefs.js). The guide's own record of the tours is no setting.
  const [prefsChanged, setPrefsChanged] = useState(false);
  const syncQuiet = useRef(false);
  useEffect(() => {
    const changing = [...profileSync.pending, ...profileSync.inflight].some((name) => name !== "tourProgress");
    if (!changing) {
      if (!profileSync.pending.size && !profileSync.inflight.size && profileSync.state === "loaded") syncQuiet.current = true;
    } else if (syncQuiet.current) setPrefsChanged(true);
  }, [profileSync.pending, profileSync.inflight, profileSync.state]);
  // Whether the first tour was done when this load's profile arrived, read
  // once: finishing it now does not count until the next visit.
  const firstRunDoneAtLoad = useRef(undefined);
  if (firstRunDoneAtLoad.current === undefined && profileSync.state === "loaded") {
    firstRunDoneAtLoad.current = tourProgress?.["first-run"]?.state === "done";
  }
  // The note the first tour's demo typed into, {page, id}: a replay on that
  // page in this session goes back to it while its text is still the start
  // of the demo's (guide/typeDemoNote.js).
  const guideNoteRef = useRef(null);
  const guide = useGuide({
    services: {
      // A finished tour's `restore`: "pen" re-arms the pen last drawn with
      // when the handwriting tour leaves the eraser, the lasso or Text armed.
      restore: (what) => {
        if (what !== "pen") return;
        restorePen();
      },
      // the first tour's finish card: "Connect an AI provider"; a setup
      // guide's step in Settings, and leaving Settings for one outside it
      openSettings: (pane) => appCmdRef.current.openSettings(pane),
      closeSettings: () => setSettingsOpen(null),
      // The welcome deck's way in: the seeded Welcome page, opened as a
      // click in the library opens it.
      openWelcome: () => {
        const page = welcomePageOf(homeBlocks);
        if (page) openPage(page.id);
      },
      show: (surface) => {
        if (surface === "chat" || surface === "windows") showChat();
        if (surface === "windows" && !homeMode && doc && !pdfHidden) {
          setNotesVisible(true);
          setCollapsedWins((prev) => ({ ...prev, notes: false }));
        }
      },
      // A scene's drag of a window to a dock side: where it would land.
      dockZone: (win, side) => dockPreviewRect(win, { side, index: 99 }),
      // The held key the area demo drags with (Settings → Keyboard).
      areaKey: () => commandChord("pdf.areaDrag", keybindings),
      findEquation: async () => {
        const hits = await pdfSearchRef.current?.(/Attention\s*\(/i);
        return hits?.[0] || null;
      },
      // The AI chat tour's figure: the first figure caption, whose figure
      // the demo frames (guide/previewArea.js).
      findFigure: async () => {
        const hits = await pdfSearchRef.current?.(/\b(?:Figure|Fig\.)\s*\d+\s*[:.]/);
        return hits?.[0] ? { ...hits[0], figure: true } : null;
      },
      // That demo's snapshot stays in the chat for the tour's next step;
      // when the tour ends, the PDF snapshots added since go again (a sent
      // one is gone already, a pasted image is never touched).
      snapshotDemo: () => {
        const before = new Set(chatImages.map(pictureUrl));
        return () => setChatImages((prev) => prev.filter((p) => before.has(pictureUrl(p)) || !pdfImagesRef.current.has(pictureUrl(p))));
      },
      // The block the note demo types into: the one it typed into before,
      // else the last highlight with no note, else a new last block.
      prepareNote: (text) => {
        const kept = guideNoteRef.current?.page === focusedBlockId ? findBlock(blocks, guideNoteRef.current.id) : null;
        const existing = kept && text.startsWith(kept.content || "") ? kept : null;
        const empty = flattenBlocks(blocks).reverse().find((b) => isPlaceBlock(b) && !(b.content || "").trim());
        const made = existing || empty ? null : makeNewBlock();
        const id = (existing || empty || made).id;
        guideNoteRef.current = { page: focusedBlockId, id };
        pendingFocusRef.current = id;
        setNotesVisible(true);
        startEditing(id);
        if (made) setBlocks((prev) => [...prev, made]);
        return id;
      },
    },
    enabled: !shareMode && wsReady && !!authUser?.user,
    // Nothing is suggested before the account's synced profile says whether to.
    suggest: suggestTours && profileSync.state !== "loading",
    scope: authUser?.user || "",
    // The tours this account has seen anywhere; a guest's stay in this browser.
    synced: { value: tourProgress, set: setTourProgress,
      ready: profileSync.state !== "loading" && profileSync.state !== "signed-out" },
    facts: {
      view: homeMode ? "home" : isPdf(doc) ? "pdf" : "page", hasPdf: isPdf(doc),
      // unknown (undefined) until /api/ai/models answers, so the AI chat
      // tour picks neither its setup step nor its chat steps too early
      aiConfigured: aiInfo ? !!aiInfo.enabled && !!aiInfo.models?.length : undefined,
      aiEditable: !authUser?.is_guest, // a guest can't store keys
      guest: !!authUser?.is_guest, // no Zotero link, assistant or comments of their own
      desktop: IS_DESKTOP, // the desktop app: files on this computer
      pdfChatVisible: isPdf(doc) && !pdfHidden && !collapsedWins.pdf && !isPhone,
      guideAvailable: !settingsOpen,
      // the phone (compact) layout: Home is the bottom bar's Library tab
      phone: !!isPhone,
      // a touch screen: the viewer offers its text/rectangle toggle
      touch: touchScreen(),
      sharedWorkspace: workspaces.some((w) => !w.personal),
      // what the topbar's Share button shares: the open page, or on the
      // library the open folder (desktop: a phone's More sheet shares it)
      shareTarget: focusedBlockId && !homeMode ? "page"
        : homeMode && !isPhone && lib.organize && libTree.folders.has(folderFilter) && !labelFilter ? "folder" : "",
      // the open page's or folder's share audience ("" unshared or not
      // loaded): the sharing tour words its access step for an
      // anyone-with-the-link share
      shareAudience: shareSettings?.token && ((shareTarget?.kind === "page" && shareTarget.id === focusedBlockId)
        || (shareTarget?.kind === "folder" && homeMode && shareTarget.id === folderFilter)) ? shareSettings.audience : "",
      onPage: !!focusedBlockId,
      editable: !readOnly,
      // which Enter starts a new note (Settings → Keyboard): the Shift+Enter
      // hint is for an account whose Enter breaks the line
      enterNewBlock: !!enterNewBlock,
      // the open paper's details are unverified (the ⓘ button's red "!"),
      // with the notes header that carries the button on screen
      unverifiedMeta: !!(focusedBlockId && !homeMode && pageMeta && metaSrc?.warn && !metaBusy)
        && (isPhone ? phonePanel === "notes" : !doc || pdfHidden || (notesVisible && !collapsedWins.notes)),
      // the first tour was finished before this page load (the Settings hint
      // comes on a later visit, not on top of the finish card)
      firstRunDone: firstRunDoneAtLoad.current === true,
      unfiledLibrary,
      emptyLibrary: freshLibrary,
      // the seeded Welcome page is in the library (the welcome deck's way in)
      welcomePage: !!welcomePageOf(homeBlocks),
      growingLibrary,
      annotatedPage,
      // the notebook view: the sheets fill the viewer, so the notes view's
      // own sheet and its tool row (the notebook tour's anchors) are not up
      notebookView: !!notebook,
      // the viewer's left edge (its tool column, outline and full screen)
      // is on screen: a PDF or the notebook view, not closed, and on a
      // phone not under the Notes or Chat panel
      viewerTools: docView.shown && !(isPhone && phonePanel),
      // this workspace has an offline copy, a publication or a Zotero link, so it has a sync pill
      clonedWorkspace: !!syncKind,
      installable: HOME_SCREEN_INSTALLABLE,
      // Gamma Connector in this browser (undefined while it is asked, and
      // in the desktop app), and a Gamma Cloud account this one could link.
      connectorHere,
      cloudLinkable: cloudLink ? !!cloudLink.enabled && cloudLink.connected !== false && !cloudLink.identity : undefined,
      prefsChanged,
      // a demo server: progress per visit, the first-run tour offered on arrival
      demo: !!serverConfig?.demo,
    },
    tidy: () => setOpenPopover(null),
  });
  useEffect(() => { if (openPopover) guideEvents.emit("popover.opened", { name: openPopover }); }, [openPopover]);
  useEffect(() => { if (quickOpen) guideEvents.emit("palette.opened"); }, [quickOpen]);
  useEffect(() => { if (settingsOpen) guideEvents.emit("settings.opened", { pane: settingsOpen }); }, [settingsOpen]);
  useEffect(() => { if (inkUi.options === true) guideEvents.emit("ink.options"); }, [inkUi.options]);
  // Someone ELSE: another account or a link visitor, not this account's own
  // second tab or the desktop app beside the browser.
  const othersHere = !!focusedBlockId && collab.peers.some((p) => !p.user || p.user !== authUser?.user);
  useEffect(() => { if (othersHere) guideEvents.emit("peer.joined"); }, [othersHere]);
  // Lazy surfaces that were always mounted while their modules came with the
  // app mount when first wanted and then stay (shared/ui/lazy.jsx).
  const quickOpenMounted = useLatch(!!quickOpen);
  const guideMounted = useLatch(guide.running || !!guide.offer || !!guide.finishCard);
  const settingsMounted = useLatch(!!settingsOpen);
  // A file picked in the Import dialog opens its review at once, already
  // uploading: fetch the review's module while the dialog is up.
  useEffect(() => { if (importOpen) ImportReviewDialog.preload(); }, [importOpen]);
  // A native app's opened files (takeOpenedFiles), once this page is signed
  // in to a workspace it can show: at once, then on each `filesOpened`. The
  // listener returns nothing, so the shell is not kept waiting.
  const openedReady = !shareMode && Boolean(authUser?.user) && wsReady && !workspaceUnavailable && hostOpensFiles();
  useEffect(() => {
    if (!openedReady) return undefined;
    takeOpenedRef.current();
    return onFilesOpened(() => { takeOpenedRef.current(); });
  }, [openedReady]);
  // The props a folder card shares between the pinned strip and the library
  // grid: glyph, title, count, selection/drag/drop behaviour and the context
  // menu. Each site adds its own className, tip, time and extras.
  function folderCardProps(f) {
    return {
      glyph: <FolderGlyph />,
      title: libTree.folders.get(f)?.name,
      kind: "Folder",
      count: folderMeta[f]?.count || 0,
      labelMode: fileLabels,
      checked: selecting && selectedFolders.has(f),
      onDragStart: (e) => startFolderDrag(e, f),
      onClick: (e) => handleFolderClick(f, e),
      onMenu: openTagMenu("folder", f),
      onDragOver: (e) => dragOverFolder(e, f),
      onDragLeave: () => setFolderDragOver(null),
      onDrop: (e) => dropOnFolder(e, f),
    };
  }
  const homeModeRef = useRef(homeMode);
  homeModeRef.current = homeMode;
  // Ctrl+scroll over the notes: session-only text size (see useTextScale).
  // Off on the home library — there the gesture stays the browser's zoom.
  const notesTextScale = useTextScale({ enabled: () => !homeModeRef.current });

  // The nth image / table / diagram of a block on this page, as a source
  // range — null once either is gone.
  function objectAt(sourceId, kind, idx) {
    const src = findBlock(blocks, sourceId);
    const obj = src ? findObject(src.content || "", kind, idx) : null;
    return obj ? { src, obj } : null;
  }
  // The Move to page picker, for a block (`blockId`) or an object
  // (`fragment`); "Move into page…" picks with it too (`pageIds`,
  // pickPageParent). It opens at once over the listing, which is read again
  // behind it, so a page made or deleted on another device since shows (not
  // in a share view, whose listing is not the library's).
  function pickMovePage(what) {
    setMovePicker(what);
    if (!shareMode) fetchHomeBlocks({ still: true });
  }
  // Where it cannot go: the page a block or an object leaves, or the moved
  // pages and the pages inside them. A sub-page is a page like any other
  // (its row names the pages it is in), and a block's or an object's move
  // offers the open page's own sub-pages first.
  const moveExclude = useMemo(() => {
    if (!movePicker) return null;
    const ids = movePicker.pageIds || [focusedBlockId];
    return new Set([...ids, ...(movePicker.pageIds ? pageDescendants(homeBlocks, ids) : [])]);
  }, [movePicker, homeBlocks, focusedBlockId]);
  const moveFirst = useMemo(() => (movePicker && !movePicker.pageIds
    ? homeBlocks.filter((p) => p._parentPage === focusedBlockId).map((p) => p.id) : []), [movePicker, homeBlocks, focusedBlockId]);
  // The link dialog's (pdf/LinkDialog.jsx): this page.
  const linkExclude = useMemo(() => new Set([focusedBlockId]), [focusedBlockId]);
  // An image / table / diagram to the end of another page: a new block there
  // holding its markdown, the object cut from its block here. The local cut
  // stays undoable (an undo leaves the copy on the other page).
  async function doMoveFragment(frag, page) {
    const title = (page.content || t("Untitled")).slice(0, 60);
    const found = objectAt(frag.sourceId, frag.kind, frag.idx);
    if (!found) return;
    const cut = cutObject(found.src.content || "", found.obj);
    try {
      await apiJson(`${API}/blocks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id: page.id, content: cut.md }),
      });
      setBlocks((prev) => setBlockText(prev, frag.sourceId, cut.content));
      setStatus(t("Moved to \"{title}\".", { title: title }));
    } catch (err) {
      setStatus(t("Move failed: {message}", { message: err.message }));
    }
  }
  // The picker's pick: what it was opened for goes to `page`.
  function moveToPicked(page) {
    const what = movePicker;
    setMovePicker(null);
    if (what?.fragment) doMoveFragment(what.fragment, page);
    else if (what?.pageIds) movePagesInto(what.pageIds, page);
    else if (what?.blockIds) doMoveBlocks(what.blockIds, page);
  }
  // Move block subtrees (one, or a selection's roots in document order) to
  // the end of another page: sync any queued edits first, then re-parent
  // each server-side (reorder carries parent_id), after the one before, so
  // they keep their order there. A block leaves this page through the
  // room's delete, which the server logs with the move: removed here as an
  // edit it would go out as a delete of a block that lives on the other
  // page by then, which the server refuses (403).
  async function doMoveBlocks(blockIds, page) {
    const title = (page.content || t("Untitled")).slice(0, 60);
    try {
      await persistBlocks(blocks);
      const kids = await apiJson(`${API}/blocks/${page.id}/children`);
      let last = (kids.children || []).slice(-1)[0]?.position || null;
      for (const blockId of blockIds) {
        const moved = await apiJson(`${API}/blocks/${blockId}/reorder`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ parent_id: page.id, before: last, after: null }),
        });
        last = moved.position;
      }
      // Older snapshots still contain the block; restoring one would put the
      // same id in two pages. A cross-page move is not undoable.
      blockHistory.clear();
      setStatus(t("Moved to \"{title}\".", { title: title }));
    } catch (err) {
      setStatus(t("Move failed: {message}", { message: err.message }));
    }
  }
  // Leaving home or changing folders drops the file-manager selection.
  useEffect(() => { clearSelection(); setHomeMenu(null); }, [folderFilter, labelFilter, homeMode]);
  // A folder or label the view names that the listing's trees lack (deleted
  // on another device, a stale link, a folder= a share does not reach): the
  // view goes back to the library's root, a share's to its folder.
  useEffect(() => {
    if (!homeLoaded) return;
    if (folderFilter && !libTree.folders.has(folderFilter)) openFolder(lib.root);
    else if (labelFilter && labelFilter !== NO_LABEL && !libTree.labels.has(labelFilter)) closeLabel();
  }, [homeLoaded, libTree]); // eslint-disable-line react-hooks/exhaustive-deps
  // Phone: navigating to another page (or home) closes any overlay panel.
  useEffect(() => { setPhonePanel(null); }, [focusedBlockId, homeMode]);
  // what each page is filed in (a sub-page in its top's folders), where it
  // is, how many pages it holds and whether it reads a file on this computer
  // (libraryUtils.pageBlocksOf)
  const pageBlocks = useMemo(() => pageBlocksOf(knownBlocks, libTree), [knownBlocks, libTree]);
  const pagesById = useMemo(() => new Map(pageBlocks.map((b) => [b.id, b])), [pageBlocks]);
  // The home view's own pages: a sub-page is reached through its parent
  // (the crumbs, Pages inside, a link), so the listing, the root feed and
  // the folder and label counts leave it out, and so they do a work
  // folder's file not in the library (`_listed`), which its directory lists
  // (explorer/FolderList.jsx), and so they do a row read beside the listing
  // (a tab's summary): the library draws what its last read carried, none
  // when that read failed. Tabs, recents and the pinned strip keep every
  // page; the pickers the listed ones and those of the tabs and recents
  // (pickerPages).
  const homeIds = useMemo(() => new Set(homeBlocks.map((b) => b.id)), [homeBlocks]);
  const libraryPages = useMemo(() => pageBlocks.filter((b) => b._listed && !b._parentPage && homeIds.has(b.id)), [pageBlocks, homeIds]);
  const pickerPages = useMemo(() => {
    const near = new Set([...openTabs.map((tab) => tab.id), ...recentViews.map((r) => r.id)]);
    return knownBlocks.filter((b) => near.has(b.id) || pagesById.get(b.id)?._listed !== false);
  }, [knownBlocks, pagesById, openTabs, recentViews]);
  // What may be done with work folders' files and folders, the menus' rows
  // for them and Add to library, Remove from library and Save a copy
  // (explorer/useWorkFolderActions.jsx). The open page as a work folder's
  // file: its work folder's id ("" for none), what a person may do with it
  // (fileRules), whether its title is the file's, which only the disk
  // renames, and whether it is in the library.
  const work = useWorkFolderActions({
    tree: workTree, pages: pagesById, focusedId: focusedBlockId, focusedBlock, vault, setStatus, confirm: setConfirmBox, openPage, tasks,
    reload: async (ids) => {
      await fetchHomeBlocks();
      if (ids.includes(focusedBlockIdRef.current)) loadBlocksForBlock(focusedBlockIdRef.current);
    },
  });
  const { work: focusedFile, rules: openRules, titleLocked, inLibrary: openInLibrary } = work.open;
  // Who a note can mention (editor/MentionMenu.jsx MentionContext,
  // docs/dev/mentions.md): the workspace's members, read once per
  // workspace, and the signed-in account. A share view mentions no one.
  const [people, setPeople] = useState([]);
  useEffect(() => {
    if (shareMode || !wsId || !authUser?.user) { setPeople([]); return undefined; }
    let live = true;
    apiJson(`${API}/people`).then((d) => { if (live) setPeople(d.people || []); }).catch(() => {});
    return () => { live = false; };
  }, [shareMode, wsId, authUser?.user]);
  const mentionPeople = useMemo(() => {
    const me = shareMode ? "" : authUser?.user || "";
    return { me, people, known: new Set([me, ...people.map((p) => p.username)].filter(Boolean)) };
  }, [shareMode, authUser?.user, people]);
  // The reminders written as "@… (remind @me)" (app/useReminders.js): the
  // cards and notifications of the ones due, and the chips' clock. One in
  // another workspace opens there (a workspace switch is a navigation).
  const refText = (id) => pageBlocks.find((p) => p.id === id)?.content ?? refCache[id]?.content;
  function openReminder(r) {
    if (r.workspace_id === wsId) { openBlockLink(r.block_id, r.page_id); return; }
    leaveCurrentPage();
    const q = new URLSearchParams({ ws: r.workspace_id, page: r.page_id, block: r.block_id });
    window.location.href = `${window.location.pathname}?${q}`;
  }
  const reminders = useReminders(!shareMode && !!authUser?.user, { labelOf: refText, onOpen: openReminder });
  // The account's Inbox (inbox/useInbox.js, docs/dev/comments.md "In the
  // app"): the bell's count, and the panel's items while it is open. A
  // click marks the item read, closes the panel and goes where it points:
  // a page of this workspace in place (the thread open, via openThreadAt),
  // another workspace or a share by navigation (with ?thread=), an
  // invitation by the workspace switch.
  const inbox = useInbox({
    enabled: !!authUser?.user && !authUser.is_guest && !shareMode,
    open: openPopover === "inbox",
    notify: inboxSystemNotifications,
    onOpen: (item) => openInboxItemRef.current?.(item),
    onStatus: (msg) => setStatus(msg),
  });
  async function openInboxItem(item) {
    setOpenPopover(null);
    const jump = inboxJump(item, wsId, window.location.pathname);
    const marked = isUnread(item) ? inbox.markRead(item, true) : null;
    if (jump.to === "workspace") {
      if (jump.workspaceId !== wsId) { await marked; switchWorkspace(jump.workspaceId); }
      return;
    }
    if (jump.to === "url") {
      await marked; // (sent with keepalive, so the navigation never cuts it off)
      leaveCurrentPage();
      window.location.href = jump.url;
      return;
    }
    if (isPhone) setPhonePanel(null);
    if (jump.to === "share") {
      // a request to join: decided in the Share popover of its page or folder
      if (jump.folderId) openFolderShare(jump.folderId);
      else if (jump.pageId === focusedBlockId) openPageShare(jump.pageId);
      else openPageThen(jump.pageId, "share");
      return;
    }
    if (!jump.pageId) {
      // a folder shared with a member: the folder in the library
      if (jump.blockId) { if (!homeMode) goHome(true, true); openFolder(jump.blockId); }
      return;
    }
    if (jump.threadId) await openThreadAt(jump.threadId, jump.blockId, jump.pageId);
    else await openBlockLink(jump.blockId || jump.pageId, jump.pageId);
  }
  const openInboxItemRef = useRef(openInboxItem);
  openInboxItemRef.current = openInboxItem;
  // Folders shown at the current level: the open folder's subfolders (the
  // top level at the root), in their order.
  const levelFolders = useMemo(() => childFolders(libTree, folderFilter), [libTree, folderFilter]);
  // The account-synced view history as a lookup ({pageId → ISO time},
  // RECENTS_CAP entries) — feeds the "Recently viewed" sort; unviewed pages
  // have no entry.
  const viewedAtById = useMemo(() => new Map(recentViews.map((r) => [r.id, r.at])), [recentViews]);
  // Per-folder rollup: page count + latest contained-page timestamps, so
  // folders can sort on the same criteria as files. Computed for EVERY
  // folder, not just the level on screen — the context menu's folder flyout
  // sorts the whole library by the same clock. One pass over the pages (each
  // page credits every folder above the ones it is filed in) — this
  // recomputes on every recents push, so a per-folder page scan would be
  // quadratic.
  // A folder in a work folder counts the files below it instead, from the
  // listing's `work_counts` (the files filed right in each directory).
  const folderMeta = useMemo(() => {
    const m = {};
    for (const id of libTree.folders.keys()) m[id] = { count: 0, updated: "", created: "", viewed: "" };
    const { inWork, counts } = workCounts(workTree);
    for (const [f, n] of counts) m[f].count += n;
    for (const b of libraryPages) {
      const v = viewedAtById.get(b._pageId) || "";
      const seen = new Set();
      for (const filed of b._folders) {
        for (const f of folderChain(libTree, filed)) {
          if (seen.has(f) || inWork.has(f)) continue;
          seen.add(f);
          const meta = m[f];
          meta.count++;
          if (b._updatedAt > meta.updated) meta.updated = b._updatedAt;
          if (b._createdAt > meta.created) meta.created = b._createdAt;
          if (v > meta.viewed) meta.viewed = v;
        }
      }
    }
    // A folder with no page in it or below it dates from its own making, so
    // a new one lists with the recent work instead of sinking to the bottom.
    for (const [id, folder] of libTree.folders) {
      if (!m[id].count) m[id].updated = m[id].created = folder.created;
    }
    return m;
  }, [libTree, workTree, libraryPages, viewedAtById]);
  // Folder order for the context menu's "Move to folder" flyout: every
  // folder, ranked by the same clock the library listing is sorted by, so
  // the folder you were last working in sits right under the cursor. Title
  // A–Z orders by path, the custom order keeps the tree's.
  // Only the library's folders: a work folder's directories file its own
  // files alone (filingTargets).
  const folderMenuIds = useMemo(() => {
    const ids = [...filingTargets(workTree)];
    const path = (id) => folderPath(libTree, id);
    if (homeSort === "manual") return ids;
    if (homeSort === "title") return ids.sort((a, b) => path(a).localeCompare(path(b)));
    const key = homeSort === "created" ? "created" : homeSort === "viewed" ? "viewed" : "updated";
    const stamp = (f) => (key === "viewed" ? (folderMeta[f]?.viewed || folderMeta[f]?.updated) : folderMeta[f]?.[key]) || "";
    return ids.sort((a, b) => stamp(b).localeCompare(stamp(a)) || path(a).localeCompare(path(b)));
  }, [libTree, workTree, folderMeta, homeSort]);
  // The rows of a "Move to" flyout, a page's or a folder's: each folder by
  // its path ("" the top level), checked where what is moved already is.
  const folderMenuRows = (ids, isIn, pick) => ids.map((f) => {
    const name = folderPath(libTree, f) || t("All files");
    return (
      <MenuItem key={f || "top"} icon={FolderIcon} title={isIn(f) ? t("Already in {f}", { f: name }) : name}
        trailing={isIn(f) ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
        onClick={() => { setHomeMenu(null); pick(f); }}>{name}</MenuItem>
    );
  });
  // The pages this view is about: inside a folder its members, at root every
  // library page (the library-wide recents feed). The listing below starts
  // from this set, and the "No label" rollup with it.
  const scopePages = useMemo(
    () => (folderFilter ? libraryPages.filter((b) => b._folders.includes(folderFilter)) : libraryPages),
    [libraryPages, folderFilter]
  );
  // A label is a tag, not a place, so a label view and its count take in
  // the sub-pages carrying it too: every page at root, and in a folder the
  // pages whose top is filed there. A sub-page carrying none is no part of
  // the "No label" rollup: it is filed with its parent.
  const labelScopePages = useMemo(
    () => (folderFilter ? pageBlocks.filter((b) => b._folders.includes(folderFilter)) : pageBlocks).filter((b) => b._listed && homeIds.has(b.id)),
    [pageBlocks, folderFilter, homeIds]
  );
  // Per-label rollup over the pages in scope — the flat mirror of folderMeta,
  // so label tiles sort and count exactly like folder tiles. At the root
  // every label is in it (a label is a block, carried or not); in a folder
  // the labels its pages carry.
  // Pages carrying no label roll up under NO_LABEL, so the labels view can
  // show what still needs filing (the entry exists only while there are any).
  const labelMeta = useMemo(() => {
    const m = {};
    if (!folderFilter) for (const id of libTree.labels.keys()) m[id] = { count: 0, updated: "", created: "", viewed: "" };
    for (const b of labelScopePages) {
      const v = viewedAtById.get(b._pageId) || "";
      for (const l of b._labels.length ? b._labels : b._parentPage ? [] : [NO_LABEL]) {
        const meta = (m[l] ||= { count: 0, updated: "", created: "", viewed: "" });
        meta.count++;
        if (b._updatedAt > meta.updated) meta.updated = b._updatedAt;
        if (b._createdAt > meta.created) meta.created = b._createdAt;
        if (v > meta.viewed) meta.viewed = v;
      }
    }
    return m;
  }, [libTree, labelScopePages, folderFilter, viewedAtById]);
  // Every label in the library by name, for the page menu's "Add label" flyout.
  const allLabels = useMemo(
    () => [...libTree.labels.values()].sort((a, b) => a.name.localeCompare(b.name)),
    [libTree]
  );
  // The labels of this view, in the tree's order (the listing sorts them).
  const scopeLabels = useMemo(() => [...libTree.labels.keys()].filter((id) => labelMeta[id]), [libTree, labelMeta]);
  // What the home list shows: containers and files as ONE sorted listing —
  // inside a folder → its subfolders + pages filed in it; at root →
  // top-level folders + EVERY page as a recents feed, loaded incrementally;
  // in "labels" mode → the labels in scope instead of folders and files;
  // inside a label → that label's pages only. Date sorts rank a container by
  // its most recent content; an empty folder by when it was made. The
  // custom order keeps the folders' own order (dragged by
  // hand, placeFolders) ahead of the pages in the library's. The search box
  // doesn't drop anything: matches are floated to the top of the sort and
  // the rest are flagged for dimming.
  const homeItems = useMemo(() => {
    const labelMode = !labelFilter && homeKinds === "labels";
    const items = labelFilter || homeKinds === "files" || labelMode ? [] : levelFolders.map((f) => ({
      kind: "folder", key: `folder:${f}`, folder: f,
      _title: libTree.folders.get(f).name,
      _updatedAt: folderMeta[f]?.updated || "", _createdAt: folderMeta[f]?.created || "",
      _viewedAt: folderMeta[f]?.viewed || "",
    }));
    if (labelMode) {
      // The "No label" catch-all rides along, pinned last after the sort.
      for (const l of labelMeta[NO_LABEL] ? [...scopeLabels, NO_LABEL] : scopeLabels) {
        items.push({
          kind: "label", key: `label:${l}`, label: l, _title: labelName(libTree, l),
          _updatedAt: labelMeta[l].updated, _createdAt: labelMeta[l].created,
          _viewedAt: labelMeta[l].viewed,
        });
      }
    }
    const pages = labelFilter === NO_LABEL ? scopePages.filter((b) => !b._labels.length)
      : labelFilter ? labelScopePages.filter((b) => b._labels.includes(labelFilter))
      : homeKinds === "folders" || labelMode ? []
      : scopePages;
    const pageItem = (b, extra) => ({
      kind: "page", key: b._pageId, block: b, _title: b.content,
      _updatedAt: b._updatedAt, _createdAt: b._createdAt,
      _viewedAt: viewedAtById.get(b._pageId) || "", ...extra,
    });
    for (const b of pages) items.push(pageItem(b));
    // "viewed" falls back to modified time so the never-viewed tail (the view
    // history keeps only the last 24 opens) still has a sensible order.
    const cmp = homeSort === "manual" ? null
      : homeSort === "title" ? (a, b) => a._title.localeCompare(b._title)
      : homeSort === "created" ? (a, b) => (b._createdAt || "").localeCompare(a._createdAt || "")
      : homeSort === "viewed" ? (a, b) => ((b._viewedAt || "").localeCompare(a._viewedAt || "") || (b._updatedAt || "").localeCompare(a._updatedAt || ""))
      : (a, b) => (b._updatedAt || "").localeCompare(a._updatedAt || "");
    if (cmp) items.sort(cmp);
    const none = items.findIndex((it) => it.kind === "label" && it.label === NO_LABEL);
    if (none >= 0) items.push(...items.splice(none, 1));
    // The same matcher as Ctrl+P (library/librarySearch.js): typo-tolerant,
    // and a page also matches on its chips, so "cs229" surfaces its papers.
    const match = createLibraryMatcher(homeQuery);
    if (!match) return items;
    // a page's chips by name, and a sub-page's path (a label view's)
    for (const it of items) {
      it._match = match(it._title, it.kind === "page"
        ? [...it.block._folderChips, ...it.block._labelChips].map((c) => c.name).concat(it.block._path || [])
        : []) > 0;
    }
    // A page inside another is no row of the listing, but its search finds
    // one: this view's sub-pages (at the root every one, in a folder those
    // whose top is filed there) that match by title, label or the pages
    // they are in, as Ctrl+P matches them, after the library's own matches.
    // A label view lists its sub-pages already, and "No label" none.
    const inside = labelFilter || labelMode || homeKinds === "folders" ? [] : labelScopePages
      .filter((b) => b._parentPage && match(b.content, b._labelChips.map((c) => c.name).concat(b._path || [])) > 0)
      .map((b) => pageItem(b, { _match: true }));
    if (cmp) inside.sort(cmp);
    return [...items.filter((it) => it._match), ...inside, ...items.filter((it) => !it._match)];
  }, [libTree, scopePages, labelScopePages, labelFilter, levelFolders, folderMeta, scopeLabels, labelMeta, viewedAtById, homeSort, homeKinds, homeQuery]);
  const homeVisibleItems = useMemo(() => homeItems.slice(0, homeShowCount), [homeItems, homeShowCount]);
  // The listing is a list that moves (ListMotion, docs/dev/ui-design.md
  // "Lists that change"): within one listing an item added, removed or
  // moved by its sort glides the others to their places. Another view,
  // kind filter, folder, label, sort or filter text is another listing, in
  // which nothing moves, and so is the next chunk Load more brings: its
  // rows arrive at once.
  const homeVisibleKeys = useMemo(() => homeVisibleItems.map((it) => it.key), [homeVisibleItems]);
  const homeListingScope = `${homeView}|${homeKinds}|${folderFilter}|${labelFilter}|${homeSort}|${homeQuery}|${homeShowCount}`;
  // What the listing is read from: the pages, the trees, and the view
  // history the "viewed" sort ranks by. A change is still when each of them
  // that changed was set by a load (homeReadRef: the listing read again on
  // coming home or on the window's wake, the server's view history landing
  // after it), so the person's own change moves even after a load.
  const homeSources = [homeBlocks, libTree, recentViews];
  const homeSourcesRef = useRef(homeSources);
  const homeChanged = homeSources.filter((source, i) => source !== homeSourcesRef.current[i]);
  const homeStill = homeChanged.length > 0 && homeChanged.every((source) => homeReadRef.current.has(source));
  useLayoutEffect(() => { homeSourcesRef.current = homeSources; });
  // A listing that takes another's place (another view, kind filter, folder
  // or label) comes into focus where it stands, as a pane does: its class is
  // played again on the listing in place, rather than remounting it, which
  // would build every card again. Not the listing a load or a page left
  // brings.
  const homeListingRef = useRef(null);
  const homeEmptyRef = useRef(null);
  const homeListingShownRef = useRef(null);
  useLayoutEffect(() => {
    const shown = homeMode && homeLoaded ? `${homeView}|${homeKinds}|${folderFilter}|${labelFilter}` : null;
    const before = homeListingShownRef.current;
    homeListingShownRef.current = shown;
    if (!shown || !before || shown === before) return;
    for (const el of [homeListingRef.current, homeEmptyRef.current]) {
      if (!el) continue;
      el.classList.remove("listingIn");
      void el.offsetWidth;
      el.classList.add("listingIn");
    }
  }, [homeMode, homeLoaded, homeView, homeKinds, folderFilter, labelFilter]);
  // The filter box matches titles and chips only; past it, the workspace
  // search reads notes and PDF text. With nothing matched a banner hands the
  // query over (Enter in the box does the same); with matches a quiet row
  // follows the last of them. homeMatchCount is null without a query.
  const homeQueryText = homeQuery.trim();
  const homeMatchCount = homeQueryText ? homeItems.filter((it) => it._match).length : null;
  // A menu row's / box's key hint: the chord this account gave the command.
  const commandKeyLabel = (id) => {
    const chord = commandChord(id, keybindings);
    return chord ? chordLabel(chord) : "";
  };
  // The quiet row goes right after the matches (they lead the listing).
  const withSearchMore = (nodes) => (homeMatchCount > 0 && homeMatchCount <= nodes.length
    ? [...nodes.slice(0, homeMatchCount), searchElsewhere(false), ...nodes.slice(homeMatchCount)] : nodes);
  const searchElsewhere = (none) => (
    <ListSearchElsewhere key="search-elsewhere" query={homeQueryText} none={none}
      keyLabel={commandKeyLabel("app.searchAll")} onSearch={() => openSearchWith(homeQueryText)} />
  );
  // "New folder" leads the listing wherever folders are listed — not inside a
  // label view or with the listing filtered to files or labels.
  // A work folder's directories come from the disk: none is made here.
  const newFolderAllowed = lib.organize && !labelFilter && !workFolder && homeKinds !== "files" && homeKinds !== "labels";
  // "New page" is the first item of the listing itself (like "New folder") —
  // Notion-style: creating a page needs no file. Not inside a label view
  // (pages are created plain, then labelled) nor when only folders show; in
  // a work folder only where its vault pulls, which writes the new note.
  const workRules = workFolder ? folderRules(workTree, folderFilter) : null;
  const newPageAllowed = lib.organize && !labelFilter && (workRules ? !!workRules.newPage
    : homeKinds !== "folders" && homeKinds !== "labels");
  // A library with nothing of the user's in it yet (only the seeded Welcome
  // page and Sketchbook, or nothing) shows "Start your library" at its root instead of the
  // empty line; a folder or label view keeps its own message.
  const libraryStart = freshLibrary && lib.organize && !folderFilter && !labelFilter ? (
    <LibraryEmpty
      onOpenLink={() => setOpenPopover("add")}
      onUpload={uploadFiles}
      onNewPage={() => createPage()}
      onImport={() => { setOpenPopover(null); setImportOpen(true); }}
      onTour={guide.startable().some((tour) => tour.id === "first-run") ? () => guide.start("first-run") : null}
    />
  ) : null;
  // What an empty listing says — the view it is empty for, not the library.
  const homeEmptyText = labelFilter === NO_LABEL
    ? t("Every page here carries a label.")
    : labelFilter
    ? t("Nothing is labelled “{label}” here — drop a page on a label to add it.", { label: labelName(libTree, labelFilter) })
    : homeKinds === "labels"
      ? (folderFilter ? t("No labels on the pages in this folder yet.") : t("No labels yet — add one from a page’s label field."))
      : folderFilter ? (lib.organize ? t("This folder is empty — start a page here or drag pages onto it from the library.") : t("This folder is empty."))
      : t("No pages yet — start with “New page”, or open a PDF from the + button above.");
  // The line over the listing: the start panel or what it is empty for, or,
  // when the read failed, that it could not be read. A failed read empties
  // the listing, which says nothing of the library: no "No pages yet".
  const homeEmptyLine = homeFailed ? (
    <div className="empty homeEmpty" ref={homeEmptyRef} role="alert">
      {t("Could not load the library.")}{" "}
      <button type="button" className="uiBtn sm" onClick={() => fetchHomeBlocks({ still: true })}>{t("Try again")}</button>
    </div>
  ) : libraryStart || (homeItems.length === 0 && !newFolderOpen ? (
    <div className="empty homeEmpty" ref={homeEmptyRef}>{homeEmptyText}</div>
  ) : null);
  // A work folder's directory (docs/dev/work_folders.md "The folder's list
  // view"), shown as a list whatever the library's view: its directories by
  // name with the files below each, then the files of the page of rows read
  // (explorer/FolderList.jsx), which the filter box narrows by name.
  const workDirs = workFolder ? listDirs(levelFolders, workTree, folderMeta) : [];
  const workRows = useMemo(() => listRows(folderRows.rows, pagesById, homeQuery), [folderRows.rows, pagesById, homeQuery]);
  // Timestamp shown on a library card and in the list's date column follows
  // the active sort: sorted by view time → viewed (falling back to modified,
  // same as the sort), by added → created; modified otherwise (incl. Title
  // A–Z). The column's header names it.
  const sortStamp = (item) => (
    homeSort === "viewed" ? (item._viewedAt || item._updatedAt)
      : homeSort === "created" ? item._createdAt
      : item._updatedAt
  );
  const cardTime = (item) => formatRelativeTime(sortStamp(item));
  const dateColumnTitle = homeSort === "viewed" ? t("Viewed") : homeSort === "created" ? t("Added") : t("Modified");
  // A list row's trailing columns, the same on every row: kind ("PDF",
  // "Page", or a folder's / label's page count), the date, the pin slot and
  // the "⋯" that opens `menu` (each kept empty where there is nothing to
  // pin or no menu, so the columns line up).
  const menuSlot = <span className="ctlBtn fileRowMenu fileRowMenuSlot" aria-hidden="true" />;
  const rowColumns = (item, kind, menu, pin = null) => {
    const stamp = sortStamp(item);
    return (
      <>
        <span className="fileRowKind">{kind}</span>
        <span className="fileRowDate" title={formatFullDate(stamp) || undefined}>{formatShortDate(stamp)}</span>
        {pin || <span className="fileRowPinSlot" aria-hidden="true" />}
        {menu ? <MenuButton className="fileRowMenu" open={menu} /> : menuSlot}
      </>
    );
  };
  // Page-shaped view of the visible slice, in the order it is drawn, which a
  // Shift+click ranges over: a work folder's rows in its list view.
  const homeVisiblePages = useMemo(
    () => (workFolder ? workRows : homeVisibleItems.filter((it) => it.kind === "page").map((it) => it.block)),
    [workFolder, workRows, homeVisibleItems]
  );
  // Pinned papers — shown as a favorites strip at the library root. Most
  // recently pinned first. Scrolls like the recents carousel: wheel pans it.
  const pinnedStripRef = useWheelPan();
  // Pinned folders join the same strip (a folder card, like the grid's),
  // merged with the pages by pin time: `pinned` on the folder block.
  const pinnedItems = useMemo(() => [
    ...[...libTree.folders.values()].filter((f) => f.pinned).map((f) => ({ kind: "folder", key: `f:${f.id}`, id: f.id, at: f.pinned })),
    ...pageBlocks.filter((b) => b._pinned).map((b) => ({ kind: "page", key: b._pageId, block: b, at: b._pinned })),
  ].sort((a, b) => b.at.localeCompare(a.at)), [libTree, pageBlocks]);
  // Recently-viewed pages that still exist, most recent first (top shortcut bar).
  const recentViewedPages = useMemo(() => {
    const byId = new Map(pageBlocks.map((b) => [b._pageId, b]));
    return recentViews
      .map((r) => { const b = byId.get(r.id); return b ? { ...b, _viewedAt: r.at } : null; })
      .filter(Boolean)
      .slice(0, 12);
  }, [recentViews, pageBlocks]);
  // Home keyboard: Esc clears the selection, Enter opens the single selected
  // item — the standard file-manager shortcuts.
  useEffect(() => {
    if (!homeMode) return;
    function onKey(e) {
      if (e.target.closest && e.target.closest("input, textarea, [contenteditable]")) return;
      if (e.key === "Escape" && (selecting || selectedPages.size || selectedFolders.size || selectedLabels.size)) {
        clearSelection();
      } else if (e.key === "Enter") {
        if (selectedLabels.size === 1) openLabel([...selectedLabels][0]);
        else if (selectedFolders.size === 1) openFolder([...selectedFolders][0]);
        else if (selectedPages.size === 1) openPage([...selectedPages][0]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [homeMode, selecting, selectedPages, selectedFolders, selectedLabels]);
  // Scrolling the "load more" sentinel into view grows the feed.
  useEffect(() => {
    const el = loadMoreRef.current;
    if (!el) return;
    const obs = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setHomeShowCount((c) => c + HOME_PAGE_CHUNK);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [homeVisibleItems.length, homeItems.length, homeMode]);
  // Identity-stable: every keystroke in a note replaces `blocks`, but the
  // derived highlights rarely change — returning the previous array when the
  // content is identical keeps the viewer's per-page memo effective (otherwise
  // each keystroke re-rendered every PdfPage's overlays).
  // The list is the page's document's: a PDF's highlights under
  // pdf_position, a picture's regions under image_position (positionKeyOf),
  // so every reader of it (the jumps, the margin, the chat) takes a region
  // with no branch.
  const prevHighlightsRef = useRef({ json: "", value: [] });
  const highlights = useMemo(() => keepIfSameJson(prevHighlightsRef, pageHighlights(blocks, treeBlocks, comments.countByBlock, positionKeyOf(doc), fileHash(doc))),
    [blocks, treeBlocks, comments.countByBlock, doc]);
  // What the comment cards stand beside on the document, the highlights or
  // regions and the ink groups (treeDerived.js commentAnchors); the viewer
  // keeps each page's slice while it reads the same.
  const anchors = useMemo(() => commentAnchors(blocks, comments.countByBlock, positionKeyOf(doc)), [blocks, comments.countByBlock, doc]);
  // What became of a thread's place once the PDF was rebuilt (comments.js
  // threadPlace), for every thread the comment surfaces draw
  // (comments/threadPlaceContext.js).
  const placeOf = useMemo(() => {
    const hash = fileHash(doc);
    return (thread) => threadPlace(thread, treeById.get(thread.block_id), hash);
  }, [treeById, doc]);
  // The page's highlights to review after its PDF was replaced, in document
  // order: the pending ones (drawn dashed until accepted or moved), the
  // Document menu's Accept all, and with the lost ones (hollow) the review
  // queue its Review counts and opens the first of, which also holds those
  // a card moved or placed on its closest match while they wait for its
  // Accept (`reviewWaiting`, PdfViewer's onReviewWaiting), as the card
  // counts them (docs/dev/highlight_relocation.md).
  const pendingHighlights = useMemo(() => pendingInOrder(highlights), [highlights]);
  const [reviewWaiting, setReviewWaiting] = useState([]);
  const reviewHighlights = useMemo(() => reviewQueue(highlights, reviewWaiting), [highlights, reviewWaiting]);
  // set by PdfViewer: scrolls to a highlight to review and opens its card
  // (with a step, `done`, the card says what it did: after a move from it)
  const pdfReviewRef = useRef(null);
  function reviewPending() {
    const first = reviewHighlights[0];
    setOpenPopover(null);
    if (!first) return;
    const wasHidden = pdfHidden;
    setPhonePanel(null); // the compact shell: the PDF, not a panel over it
    if (wasHidden) setPdfHidden(false);
    setTimeout(() => pdfReviewRef.current?.(first.id, { follow: true }), wasHidden ? 300 : 0);
  }
  // The selection popup's Comment highlight (commentOnNewHighlight): its
  // popover open marks it opened; once that popover is closed, or another
  // block's took its place, a highlight with no note and no thread is
  // deleted through the tree, as the highlight menu's Delete does. When
  // nothing else was edited since it was made, the deletion is folded into
  // its making (blockHistory takesBack): the undo history is as before the
  // Comment press, as a text box left empty leaves none.
  useEffect(() => {
    const fresh = freshCommentRef.current;
    if (!fresh) return;
    if (threadPop?.blockId === fresh.id) { fresh.opened = true; return; }
    if (!fresh.opened) return;
    freshCommentRef.current = null;
    if (readOnly || !findBlock(blocks, fresh.id)) return;
    if (abandonedCommentHighlight(highlights.find((h) => h.id === fresh.id), comments.threadsByBlock.get(fresh.id))) {
      const remove = (prev) => removeBlockTree(prev, fresh.id);
      (blockHistory.takesBack(remove(blocks)) ? foldBlocks : setBlocks)(remove);
    }
  }, [threadPop]); // eslint-disable-line react-hooks/exhaustive-deps
  // A link visitor without an account signs in through the share's own gate.
  const commentSignIn = shareMode && shareInfo && !shareInfo.viewer && !publicPage
    ? () => { setThreadPop(null); setLoginError(""); setShareGate("login"); } : undefined;
  // The PDF's comment margin (comments/marginSlot.jsx, docs/dev/comments.md
  // "The PDF margin"): what its cards read, the popover's props with the
  // active card's target; none on a phone. The handlers reach App's latest
  // through a ref, so the value changes only with what the cards show.
  const marginFns = useRef(null);
  marginFns.current = { commentLabels, openBlockLink, copyThreadLink, onCacheRef, commentSignIn, openThread };
  const commentMargin = useMemo(() => (commentsOn && !isPhone ? {
    threadsByBlock: comments.threadsByBlock,
    target: threadPop?.margin ? threadPop : null,
    me: commentMe,
    owner: commentOwner,
    canComment,
    people,
    labelsFor: (body) => marginFns.current.commentLabels(body),
    links: {
      pages: pageBlocks, rootId: focusedBlockId, lookupBlock: (id) => treeById.get(id), refCache,
      onCacheRef: (...args) => marginFns.current.onCacheRef(...args), agent: commentAgent,
    },
    onBlockRefClick: (...args) => marginFns.current.openBlockLink(...args),
    onAdd: comments.add,
    onEdit: comments.edit,
    onDelete: comments.remove,
    onResolve: comments.resolve,
    onCopyLink: (comment) => marginFns.current.copyThreadLink(comment),
    onSignIn: commentSignIn ? () => marginFns.current.commentSignIn?.() : undefined,
    onActivate: (blockId, threadId) => marginFns.current.openThread(blockId, null, { threadId, toggle: false, pdf: true }),
    onClose: () => setThreadPop(null),
  } : null), [commentsOn, isPhone, comments.threadsByBlock, threadPop, commentMe, shareMode, workspace?.role, canComment, people, // eslint-disable-line react-hooks/exhaustive-deps
    pageBlocks, focusedBlockId, treeById, refCache, commentAgent, !!commentSignIn, comments.add, comments.edit, comments.remove, comments.resolve]);
  // A press on a highlight or a region in the viewer: a note's ⊕ waiting
  // for its highlight picks it; else its row in the notes, and the chat is
  // fed as a click on the PDF feeds it (quote as the selection, an area or a
  // region as a picture).
  function highlightPressed(hlId, additive) {
    if (attachModeBlockIdRef.current) {
      const picked = highlights.find((h) => h.id === hlId);
      if (picked) linkHighlightToBlock(attachModeBlockIdRef.current, picked);
      return;
    }
    if (findBlock(blocks, hlId)) { scrollToBlock(hlId); reveal(hlId); }
    addHighlightToChat(highlights.find((h) => h.id === hlId), additive);
  }
  // The margin lost the room for the active card: its thread goes on in the
  // popover, from the highlight's or region's element.
  const marginLost = () => setThreadPop((cur) => (cur?.margin ? { ...cur, margin: false, anchor: highlightEl(cur.blockId) } : cur));
  // The surface of the panes DocumentPane draws itself (documents/
  // viewerSurface.js): the document and where its bytes are read, the
  // PDF's zoom, place, margin and ref contracts, the page's regions and
  // anchors (`highlights` and `anchors` are the document's), a line link's
  // mark on this page, and App's handlers; null handlers leave a control
  // out (no region drag when read-only, no Ask AI on a share, Show in
  // folder on the desktop alone).
  const { surface: viewerSurface, reportRef: viewerReport } = useViewerSurface({
    doc, fileUrl: documentFileUrl(doc, { asset: assetUrl }), pageId: focusedBlockId, readOnly, hidden: pdfHidden, phone: isPhone,
    scale: pdfScale, mark: pageTarget?.kind === "line" && pageTarget.pageId === focusedBlockId ? pageTarget : null,
    regions: highlights, anchors, flashId: flashingId, regionMode: areaSelectMode && !shareMode,
    commentMargin, marginRef: pdfMarginRef, scrollRef: scrollToRef, captureRef: pdfCaptureRef,
  }, {
    onZoomTo: zoomTo, onEffectiveScale: setPdfEffScale,
    placeOf: () => tabScrollRef.current[focusedBlockId] || null,
    onPlace: (p) => { if (focusedBlockId) tabScrollRef.current[focusedBlockId] = p; },
    // `done`: the mark a viewer that goes ended, never a newer link's
    onMarkDone: (done) => setPageTarget((target) => (done && target !== done ? target : null)),
    onRegion: readOnly ? null : addRegion,
    onRegionJump: highlightPressed,
    onRegionContext: setHighlightMenu,
    onRegionThread: commentsOn ? (hlId, anchor) => openThread(hlId, anchor, { toggle: false, pdf: true }) : null,
    onMarginLost: marginLost,
    onAskRegion: shareMode ? null : (position) => addChatPicture(regionPicture("area", focusedBlockId, 1, regionBox(position))),
    onReveal: IS_DESKTOP && focusedFile ? () => askShell("reveal-on-disk", { page: focusedBlockId }) : null,
  });
  // Kept by identity while the colours are the same (highlights are rebuilt
  // from the tree on every edit): a row prop of every block.
  const highlightColorsPrev = useRef({});
  const highlightColors = useMemo(() => keepIfSame(highlightColorsPrev, Object.fromEntries(highlights.map((h) => [h.id, h.color])), sameObject),
    [highlights]);
  useEffect(() => {
    if (pdfHidden) return;
    const id = pendingJumpRef.current;
    if (!id) return;
    let tries = 0;
    const attempt = () => {
      if (pendingJumpRef.current !== id) return; // superseded
      // Cross-paper jumps: the target page's blocks (and thus highlights)
      // load before its document swaps in — wait for the right document so
      // the jump isn't applied to the outgoing one.
      if (pdfUrl && pdfRenderedUrlRef.current !== pdfUrl && tries++ < 80) {
        setTimeout(attempt, 100);
        return;
      }
      let scrollTarget = anchors.find((x) => x.id === id);
      if (!scrollTarget) {
        // A highlight placed only by its page that was linked to one on the
        // PDF (attach mode) jumps to that one.
        const linked = findBlock(blocks, id)?.properties?.linked_highlight_id;
        if (linked) scrollTarget = anchors.find((x) => x.id === linked);
      }
      if (scrollTarget && scrollToRef.current) {
        setPhonePanel(null); // the compact shell: the PDF, not a panel over it
        cancelCoarseRestoreRef.current();
        scrollToRef.current({ position: scrollTarget.position });
        triggerFlash(scrollTarget.id);
      }
      pendingJumpRef.current = null;
    };
    setTimeout(attempt, 100);
  }, [pdfHidden, anchors, pdfJumpReq]);

  // Jump to the last-read page when a document opens, the page the
  // account-synced per-paper map (read-pos pref) holds for it. Waits until
  // this document's pages are rendered (however long that takes), then
  // scrolls and holds the target until the layout stops shifting under it.
  useEffect(() => {
    coarseRestorePendingRef.current = false;
    if (pdfHidden || !pdfUrl) return;
    if (restoredPdfUrlRef.current === pdfUrl) { dbg("restore: already done for this doc"); return; }
    const fid = focusedBlockIdRef.current;
    // Tab switches restore an exact per-page position (tabScrollRef) — the
    // coarse last-read page is only for (re)opening a paper cold.
    if (tabScrollRef.current[fid]) { dbg("restore: exact tab position exists for", fid); return; }
    dbg("restore: waiting for doc;", fid, "entry:", readPosRef.current[fid] || null);
    coarseRestorePendingRef.current = true;
    let cancelled = false;
    let tries = 0;
    const done = () => { coarseRestorePendingRef.current = false; };
    cancelCoarseRestoreRef.current = () => {
      if (cancelled) return;
      dbg("restore: stood down — another jump owns the viewport");
      cancelled = true;
      restoredPdfUrlRef.current = pdfUrl;
      done();
    };
    const tryRestore = () => {
      if (cancelled) return;
      // Wait — uncounted — until THIS document's pages are in the DOM. No
      // fixed budget: a big PDF on a slow load outlives any, and giving up
      // would unfreeze the scroll tracker at the top of the document, which
      // then records page 1 over the saved entry. Waiting is safe:
      // navigation cancels the loop (effect cleanup), an old document's
      // pages never match pdfUrl, and the tracker can't record anything
      // while coarseRestorePendingRef holds it paused.
      // A skeleton laid out from the manifest counts: its boxes are exact,
      // so the last-read page can be scrolled to before pdf.js has parsed
      // a byte, and nothing shifts under it when the document arrives.
      if (!scrollToRef.current || (pdfRenderedUrlRef.current !== pdfUrl && pdfLaidOutUrlRef.current !== pdfUrl)) {
        setTimeout(tryRestore, 100);
        return;
      }
      // The synced map may still be in flight (fresh browser, or a cache
      // holding this browser's OLDER position) — give the first server
      // response a bounded settle window (its catch sets the flag, so this
      // only rides out a slow response, not a dead server).
      if (!readPosLoadedRef.current && tries++ < 30) {
        setTimeout(tryRestore, 100);
        return;
      }
      const entry = fid ? readPosRef.current[fid] : null;
      const saved = entry?.page;
      if (!saved || saved <= 1) { dbg("restore: nothing to restore (saved page", saved, ")"); done(); return; }
      dbg("restore: doc rendered, scrolling to page", saved);
      restoredPdfUrlRef.current = pdfUrl;
      const pos = { pageNumber: saved };
      // A single scrollTo cannot be trusted on a cold load — it may be
      // computed before the fit-width scale applies, and late page-height
      // measurements shift the layout under the set scrollTop, either of
      // which leaves the viewport a page off. So verify against the live
      // DOM: the target page's top must sit at the viewport top (+80px, the
      // viewer's own jump offset), re-asserting until that holds for two
      // ticks. Real user input (wheel/touch/scrollbar grab) hands control
      // over instead of being fought, and the tracker stays paused until
      // done(), so no transient position is ever recorded.
      let stable = 0, settleTries = 0, userTookOver = false, inputEl = null;
      const INPUT_EVS = ["wheel", "touchstart", "mousedown"];
      const onUserInput = () => { userTookOver = true; };
      const unhookInput = () => {
        if (inputEl) for (const ev of INPUT_EVS) inputEl.removeEventListener(ev, onUserInput);
        inputEl = null;
      };
      const settle = () => {
        if (cancelled) { unhookInput(); return; }
        const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
        if (!scroller || settleTries++ > 60) { unhookInput(); done(); return; }
        if (userTookOver) { dbg("restore: user took over during settle"); unhookInput(); done(); return; }
        if (inputEl !== scroller) {
          unhookInput();
          inputEl = scroller;
          for (const ev of INPUT_EVS) inputEl.addEventListener(ev, onUserInput, { passive: true });
        }
        const pageEl = scroller.querySelector(`[data-page="${saved}"]`);
        const off = pageEl
          ? pageEl.getBoundingClientRect().top - scroller.getBoundingClientRect().top
          : null;
        // A page near the end of the document can't reach the viewport top —
        // the clamped bottom-of-document position is as good as it gets.
        const atEnd = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
        const aligned = off != null && (Math.abs(off - 80) <= 150 || (atEnd && off <= 80));
        if (aligned) {
          if (++stable >= 2) { dbg("restore: settled at page", saved); unhookInput(); done(); return; }
        } else {
          stable = 0;
          // "auto" = instant. The default smooth animation for short jumps
          // is interruptible by cold-load render work, which would strand
          // the viewport short of the target.
          scrollToRef.current({ position: pos, behavior: "auto" });
        }
        setTimeout(settle, 150);
      };
      settle();
    };
    tryRestore();
    return () => { cancelled = true; done(); cancelCoarseRestoreRef.current = () => {}; };
  }, [pdfUrl, pdfHidden]);

  // Track PDF scroll position — feeds the synced per-paper reading position.
  useEffect(() => {
    if (!pdfUrl || pdfHidden) return;
    let ticking = false;
    function onScroll(e) {
      // Fast bail without any DOM query: the notes/chat panes pass through
      // here too, but only the PDF scroller itself matters.
      const target = e.target;
      // the open page's viewer, never a tab group's view of another page
      if (!(target instanceof Element) || !target.classList.contains("pdfViewer") || target.closest("[data-read-view]")) return;
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        if (!target.isConnected) return;
        const pages = target.querySelectorAll('[data-page]');
        if (pages.length === 0) return;
        const cr = target.getBoundingClientRect();
        const midY = cr.top + cr.height / 2;
        // The pages are stacked top to bottom: a binary search over their
        // boxes finds the one under the middle of the view in a few layout
        // reads instead of one per page above it.
        let lo = 0, hi = pages.length - 1, found = null;
        while (lo <= hi) {
          const mid = (lo + hi) >> 1;
          const r = pages[mid].getBoundingClientRect();
          if (r.bottom < midY) lo = mid + 1;
          else if (r.top > midY) hi = mid - 1;
          else { found = pages[mid]; break; }
        }
        const n = found ? parseInt(found.dataset.page) : 0;
        if (!n) return;
        // Every scroll records, within one page too: that re-asserts this
        // window's position over one pulled from another window.
        recordScrollPageRef.current?.(n);
      });
    }
    // Capture-phase listener on the document: element scroll events don't
    // bubble, but they do pass document in the capture phase, so this hears
    // the viewer no matter how late its pages mount or how often layout
    // changes remount the scroller. Never attach to the scroller element
    // itself — a slow-loading document outlives any attach-retry budget,
    // and a remount silently drops a per-element listener, ending position
    // tracking for the rest of the session.
    document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => document.removeEventListener('scroll', onScroll, { capture: true });
  }, [pdfUrl, pdfHidden]);

  // Names the paper a citation pill points at, in the chat and in the notes:
  // the page title for its preview, and a short form (first author's
  // surname, else the title cut short) for text that cites several papers.
  function citeSource(id) {
    const page = knownBlocks.find((b) => b.id === id);
    const title = page?.content || (id === focusedBlockId ? pageTitle : "") || refCache[id]?.content || "";
    if (!title) return null;
    const first = page?.properties?.meta?.authors?.[0];
    const surname = first ? String(first).trim().split(/\s+/).pop() : "";
    return { title, short: surname || (title.length > 24 ? `${title.slice(0, 24).replace(/\s+\S*$/, "")}…` : title) };
  }

  // One navigation for every Gamma link card on screen (chat, notes, embeds).
  // Declared above every early return below — it is a hook, and the share
  // gate appears only after the first render.
  // Rebuilt when what the handlers close over changes; a click reads the
  // current value, so the cards themselves never re-render for navigation.
  const gammaNav = useMemo(
    () => ({ openPage: openPageLink, openBlock: openBlockLink, citeSource }),
    [focusedBlockId, blocks, refCache, shareMode, knownBlocks, pageTitle],
  );

  // What the views of pages in the tab groups (PagePane) take from App:
  // the [[ref]] labels and their fetches, the library's labels, file chips,
  // the notes' text size and keys, the PDF's looks, and what this account
  // may do. Stable while none of these change, so a view's rows render
  // again only for its own page.
  const paneFnsRef = useRef(null);
  paneFnsRef.current = { onFetchRefs, onCacheRef };
  const paneShared = useMemo(() => ({
    refCache, libTree, fileChipCtx, commentsOn, keybindings, enterNewBlock, pages: pageBlocks,
    textScaleStyle: notesTextScale.style, darkPage: pdfDarkPage, hideEmbeddedAnnots: embAnnots === "hide",
    canWrite: !readOnly, labels: !shareMode,
    onFetchRefs: (...a) => paneFnsRef.current.onFetchRefs(...a), onCacheRef: (...a) => paneFnsRef.current.onCacheRef(...a),
  }), [refCache, libTree, fileChipCtx, commentsOn, keybindings, enterNewBlock, pageBlocks, notesTextScale.style, pdfDarkPage, embAnnots, readOnly, shareMode]);

  // A share link that can't open yet: sign in (signed-in / specific-people
  // shares), or explain why not. "signin" is the blocked page's own sign-in,
  // which lands in the visitor's library rather than retrying the link.
  if (shareMode && shareGate) {
    return shareGate === "login" || shareGate === "signin" ? (
      <LoginPage
        username={loginUser}
        password={loginPass}
        error={loginError}
        onUsernameChange={setLoginUser}
        onPasswordChange={setLoginPass}
        onSubmit={shareGate === "login" ? doShareLogin : doLibraryLogin}
        cloudLogin={serverConfig?.cloud}
        next={shareGate === "login" ? undefined : "/"}
        forShare={shareGate === "login"}
        subtitle={shareGate === "login" ? t("Sign in to open this shared page") : t("Sign in to your library")}
        // the share view's own Sign in (the link had opened): back to reading
        onBack={shareGate === "login" && shareInfo?.owner ? () => setShareGate(null) : undefined}
      />
    ) : (
      <ShareBlockedPage
        reason={shareGate} viewer={shareInfo?.viewer || ""} onSwitchAccount={shareSwitchAccount}
        // a page host serves published pages only: nowhere else to go from there
        onSignIn={publicPage ? undefined : librarySignIn} offerHome={!publicPage}
      />
    );
  }

  if (workspaceUnavailable) return <WorkspaceUnavailablePage />;
  if (authUser === null) return <Splash />;

  if (authUser === false) {
    return (
      <LoginPage
        username={loginUser}
        password={loginPass}
        error={loginError}
        onUsernameChange={setLoginUser}
        onPasswordChange={setLoginPass}
        onSubmit={doLogin}
        // a read-only hosted server makes no guest accounts (gamma/hosted.py)
        onGuestLogin={serverConfig?.guest === false || serverConfig?.read_only ? undefined : doGuestLogin}
        cloudLogin={serverConfig?.cloud}
        readOnly={!!serverConfig?.read_only}
        demo={!!serverConfig?.demo && serverConfig?.guest !== false}
        guestSeeded={!!serverConfig?.guest_seeded}
        guestTtlHours={serverConfig?.guest_ttl_hours}
      />
    );
  }

  if (sessionConflict) {
    return (
      <SessionConflictPage
        tabUser={authUser.user}
        activeUser={sessionConflict}
        // Plain pathname: this tab's ?block= belongs to the old account.
        onReload={() => { window.location.href = window.location.pathname; }}
      />
    );
  }

  // The share popover (sharing/SharePopover.jsx), anchored under the topbar's
  // link button — the open page's, or the open folder's; the citation
  // section is App's (metadata + copy state).
  const sharePopover = (
    <LazyBoundary>
    <SharePopover
      target={shareTarget?.kind === "page" ? { ...shareTarget, title: pageTitle }
        : shareTarget && { ...shareTarget, name: folderPath(libTree, shareTarget.id) }}
      settings={shareSettings}
      error={shareError}
      me={authUser?.user || ""}
      meIsGuest={!!authUser?.is_guest}
      workspace={workspace}
      shareUrl={shareUrl}
      copied={!!shareCopied}
      onCopy={copyShareLink}
      onCreate={createShareLink}
      onUpdate={updateShareSettings}
      onInvite={inviteShareUser}
      onSetRole={setShareUserRole}
      onRemove={removeShareUser}
      cloudInvites={shareCloudInvites}
      onStop={stopSharing}
      onClose={() => { setOpenPopover(null); setShareError(""); }}
      publish={publishOffered && shareTarget?.kind !== "folder" ? {
        state: publishState?.page === focusedBlockId ? publishState : null,
        busy: publishBusy,
        error: publishError,
        copied: !!publishCopied,
        onCopy: copyPublishLink,
        canEdit: !readOnly,
        onPublish: publishPage,
        onUnpublish: unpublishPage,
        onSync: syncPublication,
        onLink: () => openSettingsPane("account"),
        accountUrl: serverConfig?.cloud?.issuer ? `${serverConfig.cloud.issuer}/` : "",
      } : null}
      citation={shareTarget?.kind !== "folder" && (pageMeta || pageBibtex) ? (
        <Section
          title={t("Citation")}
          action={
            <button
              type="button" className="uiBtn sm iconSq"
              title={t("Regenerate the citation")} aria-label={t("Regenerate the citation")}
              disabled={pptCiteBusy}
              onClick={() => makePptCitation(true)}
            >{pptCiteBusy ? "…" : <RefreshIcon size={16} />}</button>
          }
        >
          <div className="citeHead">
            <span className="citeLabel">{t("Slide citation")}</span>
            {/* Provenance right where the citation gets copied: a
                registry name, or a red "!" when nothing tied the
                record to this document. */}
            {metaSrc ? (
              <span className={`citeSourceTag${metaSrc.warn ? " warn" : ""}`} title={t(metaSrc.hint)}>
                {metaSrc.warn ? <span className="metaWarnDot inline" aria-hidden="true">!</span> : null}
                {t(metaSrc.label)}
              </span>
            ) : null}
          </div>
          {metaSrc?.warn ? <div className="settingsPaneHint citeWarnHint">{t(metaSrc.hint)}.</div> : null}
          {pptCite ? (
            <CopyBox
              copied={copiedKey === "ppt"} onCopy={() => copyFlash("ppt", pptCite)}
              title={t("Copy — pastes with real italics/bold into PowerPoint")} label={t("Copy slide citation")}
            >
              <div className="pptCitePreview"><ChatMarkdown text={pptCite} /></div>
            </CopyBox>
          ) : (
            <div className="settingsPaneHint">{pptCiteBusy ? t("Generating…") : t("Citation will generate when metadata is ready.")}</div>
          )}
          {pageBibtex ? (
            <>
              <div className="citeHead"><span className="citeLabel">{t("BibTeX")}</span></div>
              <CopyBox
                copied={copiedKey === "bibtex"} onCopy={() => copyFlash("bibtex", printedBibtex || pageBibtex)}
                title={t("Copy the BibTeX entry")} label={t("Copy BibTeX")}
              >
                <pre className="bibtexPre">{printedBibtex || pageBibtex}</pre>
              </CopyBox>
            </>
          ) : null}
        </Section>
      ) : null}
    />
    </LazyBoundary>
  );

  // Notion-style tail under the block tree: clicking the empty space below
  // the last block starts writing there — in the last block if it is still
  // empty, else in a fresh top-level one. On an empty page the zone carries
  // the invitation text; the per-row "+" handle covers inserting mid-page.
  function editTail() {
    if (readOnly) return;
    const last = blocks[blocks.length - 1];
    if (last && !last.content && !(last.children || []).length) {
      pendingFocusRef.current = last.id;
      startEditing(last.id);
      setFocusedId(last.id);
      return;
    }
    const { blocks: next, newId } = addRootBlock(blocks);
    pendingFocusRef.current = newId;
    startEditing(newId);
    setBlocks(next);
    setFocusedId(newId);
  }
  // Who links here (editor/BacklinksPanel.jsx), under the notes and above
  // the tail; an entry opens its page at the linking block, a link jump
  // like a [[ref]] chip's. Nothing to show while the section is off — the
  // list is only fetched when `backlinksVisible` is on.
  const backlinksPanel = !homeMode && focusedBlockId && backlinks.length ? (
    <BacklinksPanel backlinks={backlinks} pageId={focusedBlockId} pageTitle={pageTitle} pages={pageBlocks}
      refCache={refCache} onFetchRefs={onFetchRefs} onOpen={(bl) => openBlockLink(bl.id, bl.page_root_id)}
      collapsed={appPrefs.backlinksCollapsed} onCollapsedChange={appPrefs.setBacklinksCollapsed} />
  ) : null;
  // The pages inside the open page (editor/SubPagesPanel.jsx), above the
  // Linked from panel: each row opens its page and has the library's page
  // menu (not in a share view), and where the library may be changed a
  // last row makes another and a row drags as a library row does: onto
  // another row or a crumb it goes inside that page, onto the topbar's
  // Home to the library. A row takes a dropped page in too. A drag from
  // the panel carries its one page: the library's selection is let go.
  const subPagesPanel = !homeMode && focusedBlockId ? (
    <SubPagesPanel pages={openFamily.subPages} onOpen={openFamilyPage}
      onNew={lib.organize || (shareMode && !readOnly) ? () => createSubPage(focusedBlockId) : undefined}
      onMenu={shareMode ? undefined : (page) => openSubPageMenu(page.id, page.title)}
      rowProps={lib.organize ? (page) => ({
        draggable: true,
        onDragStart: (e) => { clearSelection(); startPageDrag(e, page.id); },
        ...pageDropProps(page.id, page.title),
        className: pageDropClass(page.id),
      }) : undefined}
      collapsed={appPrefs.subPagesCollapsed} onCollapsedChange={appPrefs.setSubPagesCollapsed} />
  ) : null;
  // A synced vault that pushes everything and does not pull: its notes are
  // written in the file, so here a note has no editor and no new one is
  // started; highlights keep theirs. A page whose document is no PDF has no
  // notes file: its notes are Gamma's own.
  const notesLocked = vaultNotesLocked(vaultState, doc);
  const notesTail = !homeMode && !readOnly && !notesLocked && focusedBlockId ? (
    <div className={"notesTail" + (blocks.length ? "" : " isEmpty")} data-motion-key="tail"
      onMouseDown={(e) => { e.preventDefault(); editTail(); }}>
      {blocks.length ? null : t("Click to start writing")}
    </div>
  ) : null;

  // The notes window - docked via notesDock, or filling the center when no PDF is shown.
  // The pages among the notes (notebook/NoteSheet.jsx): their marks and the
  // sheet actions (the tools reach them through PageToolsContext), and the
  // strip over the notes when no viewer that takes ink (the pane's `ink`)
  // is there to hold it.
  const viewerInk = docView.shown && !!docView.spec?.ink;
  // The pen's strip and the text highlighter both take a drag on the page:
  // opening one puts the other away (arming a highlighter closes the strip).
  const openInkTools = () => {
    if (inkUi.open) { closeInkStrip(); return; }
    textHighlighter.off();
    openInkStrip();
  };
  const noteSheetCtx = notebook ? null : {
    marks, inkOpen: inkUi.open,
    onPen: openInkTools, onPaper: setSheetPaper, onPaperAll: applyPaperToAll, onAddAfter: addSheetAfter,
    onNotebookView: hasSheets ? (sheetId) => setNotebookView(focusedBlockId, true, { id: sheetId, fy: 0 }) : undefined,
  };
  const markupToolbar = (
    <MarkupToolbar
      tools={inkTools} active={inkUi.tool} options={inkUi.options}
      eraserMode={inkEraserMode} eraserSize={inkEraserSize} eraserReturn={inkEraserReturn} eraserFilter={inkEraserFilter}
      lassoMode={inkLassoMode} textStyle={textBoxes.shownStyle}
      onPick={pickInkTool} onTextStyle={textBoxes.restyle} blockHistory={boxUndo}
      onPicture={readOnly ? undefined : pictures.pick}
      onUndo={() => stripUndo(false)} onRedo={() => stripUndo(true)}
      // the block history keeps no count to disable its buttons by: a step with nothing to undo says so
      canUndo={boxUndo || inkHistoryState.undo > 0} canRedo={boxUndo || inkHistoryState.redo > 0}
      // no argument: the armed tool's row; "ruler": the ruler's
      onToggleOptions={toggleInkOptions}
      ruler={inkRuler} rulerUnits={inkRulerUnits} onToggleRuler={toggleInkRuler} onRulerOptions={inkRulerOptions} onStampLine={stampRulerLine}
      shapeKind={inkShapeKind} shapeFill={inkShapeFill} onToggleShape={toggleInkShape} stylusPen={inkPenPreset}
      onShape={(patch) => {
        if ("kind" in patch) setInkShapeKind(patch.kind);
        if ("fill" in patch) setInkShapeFill(!!patch.fill);
      }}
      onChangeTools={setInkTools}
      // the More menu's command rows and the strip's pills; Handwriting settings… lands on the section
      cmdCtx={appCmdRef.current} bindings={keybindings} onStatus={setStatus}
      onOpenSettings={() => openSettingsPane("handwriting")}
      onEraser={(patch) => {
        if ("mode" in patch) setInkEraserMode(patch.mode);
        if ("size" in patch) setInkEraserSize(patch.size);
        if ("filter" in patch) setInkEraserFilter(patch.filter);
        if ("return" in patch) setInkEraserReturn(patch.return);
      }}
      onLasso={setInkLassoMode} lassoPictures={inkLassoPictures} onLassoPictures={setInkLassoPictures}
      // the zoom window: on a PDF page in view, never on a phone
      zoomOpen={!!inkZoom} zoomAvailable={docView.viewer === "pdf" && !docView.notebook && !pdfHidden} phone={isPhone} onToggleZoom={toggleInkZoom}
      onClose={closeInkStrip}
    />
  );
  const notesInkStrip = !viewerInk && !notebook && nbSheets.length > 0 && inkUi.open && !readOnly
    ? <div className="notesInkDock">{markupToolbar}</div> : null;
  // The player and the recording pill, when no viewer is there to hold
  // them: stuck to the bottom of the notes' list (docs/dev/recording.md).
  const notesReplayDock = !viewerInk && (playerBar || recordingNow)
    ? <div className="notesReplayDock">{playerBar}<RecordingPill /></div> : null;

  // When no viewer shows (home, a page with no document pane nor its
  // notebook view, the owner's pages and shared pages alike, or the pane
  // closed) the notes window takes the center instead of an empty viewer,
  // whether or not the Notes dock is on.
  const centerNotes = docView.center || homeMode;
  const notesWindow = notesVisible || centerNotes ? (
    <div className="sidebar" data-guide="dock.notes" ref={liveSidebarRef}>
          {!homeMode && <div className="pageTitleRow">
            <div className="pageTitleMain">
            {/* the pages the open page is inside, each opening that page */}
            <PageCrumbs ancestors={openFamily.ancestors} onOpen={openFamilyPage}
              targetProps={lib.organize ? (page) => ({ ...pageDropProps(page.id, page.title), className: pageDropClass(page.id) }) : undefined} />
            {titleEditing && !readOnly && !titleLocked && focusedBlockId ? (
              // A textarea so a long title wraps as it does when shown; a title
              // stays one line, so pasted line breaks become spaces.
              <AutoGrowTextarea
                className="titleEdit"
                autoFocus
                rows={1}
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value.replace(/\s*[\r\n]+\s*/g, " "))}
                onBlur={() => { renameTitle(titleDraft); setTitleEditing(false); }}
                onKeyDown={(e) => {
                  if (composing(e)) return;
                  if (e.key === "Enter") {
                    // The first block's editor mounts and takes focus within this same
                    // key event (discrete-event flush), so the key's default insert would
                    // land there as a newline.
                    e.preventDefault();
                    e.currentTarget.blur();
                    // On a fresh page, Enter continues into its empty first block.
                    const first = blocksRef.current?.[0];
                    if (blocksRef.current?.length === 1 && first && !first.content && viewRef.current.editingId !== first.id) {
                      pendingFocusRef.current = first.id;
                      startEditing(first.id);
                    }
                  }
                  else if (e.key === "Escape") { setTitleDraft(pageTitle); setTitleEditing(false); }
                }}
              />
            ) : (
              <h3
                className={!readOnly && !titleLocked && focusedBlockId ? "titleText editable" : "titleText"}
                title={titleLocked ? refusalText("named")
                  : !readOnly && focusedBlockId ? t("Click to rename") : undefined}
                onClick={() => {
                  if (readOnly || titleLocked || !focusedBlockId) return;
                  setTitleDraft(pageTitle || t("Untitled"));
                  setTitleEditing(true);
                }}
              >{focusedBlockId ? (pageTitle || t("Untitled")) : t("Notes")}</h3>
            )}
            {focusedBlockId && !shareMode ? (
              <div className={labelEditing ? "categoryFrontmatter editing" : "categoryFrontmatter"}>
                <span className="categoryIcon" title={t("Labels")}>
                  <LabelIcon size={14} />
                </span>
                {labelEditing ? (() => {
                    const filed = openPageFiling();
                    // a page inside another is filed by labels only
                    const chips = filingChips(libTree, openSubPage ? { labels: filed.labels } : filed);
                    const q = labelInput.trim();
                    const ql = q.toLowerCase();
                    // A path ending in "/" is a folder opened, not a name
                    // being typed: its subfolders are the whole list, with
                    // no label and nothing new to make among them.
                    const listing = q && !openSubPage ? folderInside(libTree, q) : null;
                    const labelSugs = q && !listing ? [...libTree.labels.values()]
                      .filter((l) => l.name.toLowerCase().includes(ql) && !filed.labels.includes(l.id))
                      .sort((a, b) => a.name.localeCompare(b.name)) : [];
                    // Only the folders the rule files the page in (fileRules:
                    // a library folder, never a work folder's directory; a
                    // file not in the library, none).
                    const targets = new Set(openRules.refileTargets());
                    const folderSugs = q && !openSubPage
                      ? folderMatches(libTree, q, filed.folders).filter((f) => targets.has(f.id)) : [];
                    // A name no label has can become one, or a folder at the
                    // path typed (its missing parents made with it), where
                    // the page may be filed in library folders.
                    const newFolder = !focusedFile || openInLibrary ? folderPathNames(q) : [];
                    const suggestions = [
                      ...folderSugs.map((f) => ({ kind: "folder", ...f })),
                      ...labelSugs.map((l) => ({ kind: "label", ...l })),
                    ].slice(0, 8);
                    if (q && !listing && !findNamed(libTree.labels.values(), q)) suggestions.push({ kind: "newLabel", name: q });
                    if (q && !listing && !openSubPage && newFolder.length && !folderAtPath(libTree, q)) {
                      suggestions.push({ kind: "newFolder", path: newFolder, name: newFolder.join(PATH_SEP) });
                    }
                    return (
                    <div className="categoryTagInputContainer">
                      <div className="categoryTagInputWrap">
                        {chips.folders.map((f) => (
                          <span key={`f:${f.id}`} className="categoryTag folderChip" title={t("Folder: {f}", { f: f.name })}>
                            <FolderIcon size={10} />
                            {f.name}
                            {!focusedFile || openRules.removableFolders.includes(f.id) ? (
                              <button className="uiClose uiCloseSm categoryTagRemove" tabIndex={-1} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); removeOpenPageFolder(f.id); }}><XIcon size={14} /></button>
                            ) : null}
                          </span>
                        ))}
                        {chips.labels.map((l) => (
                          <span key={`l:${l.id}`} className="categoryTag">
                            {l.name}
                            <button className="uiClose uiCloseSm categoryTagRemove" tabIndex={-1} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); removeOpenPageLabel(l.id); }}><XIcon size={14} /></button>
                          </span>
                        ))}
                        <input
                          className="categoryFrontmatterInput"
                          data-guide="page.labelInput"
                          value={labelInput}
                          onChange={(e) => { setLabelSuggestionIdx(-1); setLabelInput(e.target.value); }}
                          onKeyDown={(e) => {
                            if (composing(e)) return;
                            if (e.key === "ArrowDown") {
                              e.preventDefault();
                              if (suggestions.length > 0) {
                                setLabelSuggestionIdx(i => Math.min(i + 1, suggestions.length - 1));
                              }
                            } else if (e.key === "ArrowUp") {
                              e.preventDefault();
                              setLabelSuggestionIdx(i => Math.max(i - 1, -1));
                            } else if (e.key === "Enter" && labelSuggestionIdx >= 0 && labelSuggestionIdx < suggestions.length) {
                              e.preventDefault();
                              pickLabelSuggestion(suggestions[labelSuggestionIdx]);
                            } else if (e.key === "Tab" && !e.shiftKey && suggestions.length) {
                              // Tab completes, as a shell or VS Code does: the
                              // highlighted suggestion, else the first, written
                              // out in full and ending in the separator when it
                              // holds subfolders, so Tab after Tab walks down
                              // the tree. With nothing left to write it picks.
                              e.preventDefault();
                              const s = suggestions[labelSuggestionIdx] || suggestions[0];
                              const path = s.kind === "folder" ? folderCompletion(libTree, s.id) : "";
                              if (path && path !== labelInput) { setLabelSuggestionIdx(-1); setLabelInput(path); }
                              else pickLabelSuggestion(s);
                            } else if (e.key === "Enter" || e.key === "Escape") {
                              e.preventDefault();
                              closeLabelEditor();
                            } else if (e.key === "Backspace" && !labelInput && filed.labels.length) {
                              removeOpenPageLabel(filed.labels.at(-1));
                            }
                          }}
                          onBlur={closeLabelEditor}
                          autoFocus
                          placeholder={openSubPage ? t("Add a label…") : t("Add a label or a folder…")}
                        />
                      </div>
                      {suggestions.length > 0 ? (
                        <div className="categorySuggestions">
                          {suggestions.map((s, i) => (
                            <button key={`${s.kind}:${s.id || s.name}`} className={`categorySuggestionItem${i === labelSuggestionIdx ? " selected" : ""}`}
                              onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); pickLabelSuggestion(s); }}
                              onMouseEnter={() => setLabelSuggestionIdx(i)}
                            >{s.kind === "folder" ? <><FolderIcon size={14} />{s.name}</>
                              : s.kind === "newFolder" ? <><FolderPlusIcon size={14} />{t("New folder “{name}”", { name: s.name })}</>
                              : s.kind === "newLabel" ? <><PlusIcon size={14} />{t("New label “{name}”", { name: s.name })}</>
                              : s.name}</button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    );
                  })() : (() => {
                    const chips = filingChips(libTree, { folders: openSubPage ? [] : pageFolders, labels: pageLabels });
                    return (
                    <span
                      className={`categoryFrontmatterValue ${chips.folders.length || chips.labels.length ? "" : "empty"}`}
                      data-guide="page.labels"
                      onClick={() => { setLabelInput(""); setLabelSuggestionIdx(-1); setLabelEditing(true); }}
                      title={t("Click to edit")}
                    >
                      {chips.folders.length || chips.labels.length ? (
                        <>
                          {chips.folders.map((f) => (
                            <span
                              key={`f:${f.id}`}
                              className="categoryBadge folderChip"
                              title={t("Folder: {f} — right-click to rename or delete", { f: f.name })}
                              {...menuPress(openTagMenu("folder", f.id))}
                            ><FolderIcon size={10} />{f.name}</span>
                          ))}
                          {chips.labels.map((l) => (
                            <span
                              key={`l:${l.id}`}
                              className="categoryBadge"
                              title={t("Label: {label} — right-click to rename or delete", { label: l.name })}
                              {...menuPress(openTagMenu("label", l.id))}
                            >{l.name}</span>
                          ))}
                        </>
                      ) : t("Add labels...")}
                    </span>
                    );
                  })()}
              </div>
            ) : null}
            </div>
            {/* The right of the header: the page's buttons, with who else is
                here under them. */}
            <div className="pageHeadSide">
            {focusedBlockId ? (
              <div className="pageActionCol">
                {/* The page's own thread, for a share's visitors too. */}
                {commentsOn ? (
                <button
                  type="button"
                  className={`pageActionBtn pageCommentBtn${comments.countByBlock.get(focusedBlockId) ? " has" : ""}`}
                  data-comment-for={focusedBlockId}
                  title={comments.countByBlock.get(focusedBlockId)
                    ? tn("{n} open comment on this page", "{n} open comments on this page", comments.countByBlock.get(focusedBlockId))
                    : t("Comment on this page")}
                  aria-label={t("Comment on this page")}
                  onPointerEnter={ThreadPopover.preload}
                  onFocus={ThreadPopover.preload}
                  onClick={(e) => openThread(focusedBlockId, e.currentTarget)}
                >
                  <MessageSquareIcon size={16} />
                  {comments.countByBlock.get(focusedBlockId)
                    ? <span className="pageCommentCount">{shortCount(comments.countByBlock.get(focusedBlockId))}</span> : null}
                </button>
                ) : null}
                {!shareMode ? (<>
                {hasSheets && !notebook ? (
                  // the way to the notebook view that stays in sight, opening at the sheet in view
                  <button className="uiBtn sm ghost" onClick={() => setNotebookView(focusedBlockId, true)}
                    title={t("Notebook view: the pages beside the notes")}>
                    <NotebookIcon size={16} /> {t("Notebook")}
                  </button>
                ) : null}
                {/* a work folder's file is read where it is: no document is attached to one */}
                {doc || zoteroPdf || (!readOnly && !notebook && !focusedFile) ? (
                  <span data-popover="attach" className="popoverAnchor">
                    <button
                      className={`pageActionBtn ${doc ? "active" : ""}`} data-guide="page.document"
                      title={doc
                        ? t("Document: {name}", { name: doc.name || defaultPageTitle(doc) })
                        : zoteroPdfShown ? zoteroCard.title
                        : t("Attach a document to this page: a PDF by URL, arXiv id or DOI, or any file by upload — it gets its viewer, notes and comments.")}
                      aria-label={doc ? t("Document") : zoteroPdfShown ? t("PDF in Zotero") : t("Attach document")}
                      aria-haspopup={doc ? "menu" : undefined}
                      aria-expanded={openPopover === "attach"}
                      disabled={loading}
                      onClick={() => setOpenPopover((p) => (p === "attach" ? null : "attach"))}
                    >{zoteroPdfShown ? <CloudDownloadIcon size={16} /> : <PaperclipIcon size={16} />}</button>
                    {openPopover === "attach" && doc ? (
                      <Popover as={MenuScope} className="addPopover attachPopover" aria-label={t("Document")}>
                        <div className="popoverTitle">{t("Document")}</div>
                        <div className="popoverHint attachFileName" title={doc.url}>
                          <PaperclipIcon size={14} /> {doc.name || defaultPageTitle(doc)}
                        </div>
                        <MenuItem icon={pdfHidden ? EyeIcon : EyeOffIcon} onClick={() => { setPdfHidden((h) => !h); setOpenPopover(null); }}>
                          {isPdf(doc) ? (pdfHidden ? t("Show the PDF") : t("Hide the PDF")) : pdfHidden ? t("Show the file") : t("Hide the file")}
                        </MenuItem>
                        {pdfUrl ? (
                          <MenuItem icon={DownloadIcon} onClick={exportRawPdf} title={t("Download the PDF file exactly as stored — no highlights or notes")}>
                            {t("Download the PDF")}
                          </MenuItem>
                        ) : null}
                        {/* the highlights drawn dashed or hollow after a replacement, to
                            review: the first one's card; the dashed ones accepted as one edit */}
                        {reviewHighlights.length ? (
                          <MenuItem icon={TextSearchIcon} onClick={reviewPending}
                            title={t("Show the first dashed or hollow highlight with what it matched and where it was before")}>
                            {tn("Review {n} highlight", "Review {n} highlights", reviewHighlights.length)}
                          </MenuItem>
                        ) : null}
                        {pendingHighlights.length && !readOnly ? (
                          <MenuItem icon={CheckIcon}
                            onClick={() => { setOpenPopover(null); acceptHighlightPlaces(pendingHighlights.map((h) => h.id)); }}
                            title={t("Draw every dashed highlight solid where it is now")}>
                            {tn("Accept {n} rough match", "Accept all {n} rough matches", pendingHighlights.length)}
                          </MenuItem>
                        ) : null}
                        {!readOnly ? (
                          <>
                            {/* A document read where it lies on this computer (its doc at "path") has its
                                file: a new one is a new version of that file, which comes by itself, or
                                the file found again (Locate…); an upload or another file would leave the
                                page reading the old one. The page keeps its format: the picker offers the
                                format's own extensions (documents/viewerSurface.js replaceAccept). */}
                            {!pdfLinked ? (
                              <MenuItem icon={UploadIcon} disabled={loading}
                                onClick={() => pickFiles({ accept: replaceAccept(doc) }, ([f]) => attachPdfToPage({ file: f, replace: true }))}
                                title={isPdf(doc)
                                  ? t("Upload a new version of this PDF: the notes and highlights stay, and each highlight moves to where its text is in the new file")
                                  : t("Upload a new version of this file: the notes and comments stay")}>
                                {t("Replace the file…")}
                              </MenuItem>
                            ) : null}
                            {/* In the desktop app: its page preload passes the message to the app, which
                                asks for the file and pushes each new version of it here, a synced vault of
                                one file (desktop/docs/architecture.md). Making one takes an editor of the
                                workspace, as a folder's does (a share link's editor is none). */}
                            {IS_DESKTOP && lib.organize && !pdfLinked && isPdf(doc) ? (
                              <MenuItem icon={HardDriveIcon} data-guide="page.pushFile"
                                title={t("Each time a PDF on this computer changes, it becomes this page's PDF: the notes and highlights stay, and each highlight moves to where its text is")}
                                onClick={() => {
                                  setOpenPopover(null);
                                  askShell("follow-file-on-disk", { page: focusedBlockId });
                                }}>{t("Push from a file on this computer…")}</MenuItem>
                            ) : null}
                            {isPdf(doc) ? (
                              <MenuItem icon={LocateIcon} disabled={loading || relocating || !pdfStored} onClick={relocateHighlights}
                                title={!pdfStored ? t("This PDF is shown from its link and not stored on the server, so its highlights cannot be placed in it")
                                  : relocating ? t("This page's highlights are being placed — see Background tasks")
                                  : t("Find each highlight's text in this file and move the highlight there; one matched roughly is drawn dashed, one whose text is gone hollow")}>
                                {t("Find highlights in this PDF")}
                              </MenuItem>
                            ) : null}
                            {!pdfLinked ? <MenuItem icon={ScissorsIcon} onClick={detachPdfFromPage}>{t("Detach the file…")}</MenuItem> : null}
                          </>
                        ) : null}
                      </Popover>
                    ) : openPopover === "attach" && zoteroPdfShown ? (
                      <Popover className="addPopover attachPopover" aria-label={t("PDF in Zotero")}>
                        <ZoteroPdfCard pdf={zoteroPdf} source={zoteroSource} onFetch={() => fetchZoteroPdfNow(focusedBlockId)} />
                      </Popover>
                    ) : openPopover === "attach" ? (
                      <Popover className="addPopover attachPopover">
                        {/* a PDF in Zotero that cannot come: why, above the page's own copy */}
                        {zoteroPdf ? (
                          <ZoteroPdfCard pdf={zoteroPdf} source={zoteroSource} ownCopy={!readOnly && !notebook && !focusedFile}
                            onFetch={() => fetchZoteroPdfNow(focusedBlockId)} />
                        ) : null}
                        {!readOnly && !notebook && !focusedFile ? <>
                          <div className="popoverTitle">{t("Attach a document")}</div>
                          <input
                            autoFocus={!zoteroPdf}
                            className="searchInput"
                            value={attachUrl}
                            onChange={(e) => setAttachUrl(e.target.value)}
                            placeholder={t("PDF URL, arXiv id, or DOI — press Enter")}
                            onKeyDown={(e) => {
                              if (composing(e)) return;
                              if (e.key === "Enter" && attachUrl.trim() && !loading) attachPdfToPage({ url: attachUrl.trim() });
                              else if (e.key === "Escape") setOpenPopover(null);
                            }}
                          />
                          <MenuItem icon={UploadIcon} disabled={loading}
                            onClick={() => pickFiles({}, ([f]) => attachPdfToPage({ file: f }))}>{t("Upload a file…")}</MenuItem>
                        </> : null}
                      </Popover>
                    ) : null}
                  </span>
                ) : null}
                {/* a note page that is a Zotero note Gamma cannot write back */}
                {focusedBlock?.properties?.zotero_readonly ? <ZoteroReadonlyBadge props={focusedBlock.properties} onPage /> : null}
                {/* a Zotero link's difference on the record (both sides changed a field) */}
                {recordMerge ? (
                  <span className="zoteroRecordChip">
                    <MergeChip conflict={recordMerge} onResolve={resolveMerge} nav={mergeNav(focusedBlockId)}
                      open={mergeOpen === focusedBlockId} onOpenChange={(v) => setMergeOpen(v ? focusedBlockId : null)} />
                  </span>
                ) : null}
                {/* in the desktop app: the synced vault that keeps the page, or the file an opened PDF is read from */}
                <VaultChip state={vaultState} open={openPopover === "vault"} canAdd={lib.organize}
                  onOpenChange={(v) => { if (v) vault.refresh(); setOpenPopover(v ? "vault" : null); }}
                  onReveal={() => askShell("reveal-on-disk", { page: focusedBlockId })}
                  onEdit={() => vault.setEditing(focusedBlockId)} {...work.chip}
                  onFeedback={work.chip.file && canWriteWorkspace ? () => openFeedback({ kind: "file", id: focusedBlockId }) : undefined} />
                {/* a work folder's file has a record only once it is in the library */}
                {(isPdf(doc) || pageMeta) && (!focusedFile || openInLibrary) ? (
                  <span data-popover="meta" className="popoverAnchor">
                    <button
                      ref={metaBtnRef}
                      className="pageActionBtn"
                      data-guide="page.metadata"
                      title={metaBusy
                        ? t("Fetching paper metadata…")
                        : metaSrc?.warn
                          ? t(metaSrc.hint)
                          : t("Edit metadata (authors, venue, DOI, cite key…)")}
                      aria-label={t("Paper metadata")}
                      onClick={() => openMetaPopover()}
                    >
                      {/* Same busy affordance as the translate button: the
                          icon becomes a spinner while a fetch is running. */}
                      {metaBusy ? <span className="pillSpin" aria-hidden="true" /> : <InfoIcon size={16} />}
                    </button>
                    {/* Metadata nothing ties to this document (AI-extracted,
                        or an identifier resolved but unconfirmed) — flag it
                        so nobody cites it unverified. Only for things that
                        claim to be papers: course notes, slides etc.
                        (meta.kind) have no registry record to verify against. */}
                    {!metaBusy && metaSrc?.warn ? (
                      <span className="metaWarnDot" aria-hidden="true">!</span>
                    ) : null}
                    {openPopover === "meta" ? (
                      <Popover
                        className="sourcePopover metaPopover"
                        style={{
                          position: "fixed",
                          top: metaPopPos.top,
                          right: metaPopPos.right,
                          left: "auto",
                          zIndex: 1400,
                          maxHeight: `calc(100vh - ${metaPopPos.top + 12}px)`,
                          overflowY: "auto",
                        }}
                      >
                        <div className="popoverTitle citeSectionRow">
                          <span>{t("Paper metadata")}</span>
                          <button
                            className="searchToggle"
                            title={t("Refresh metadata (arXiv → DOI → AI)")}
                            disabled={metaBusy}
                            onClick={() => focusedBlock && fetchMetadata(focusedBlock, true)}
                          >{metaBusy ? "…" : "↻"}</button>
                        </div>
                        <div className="metaTable">
                          {/* The item type (Zotero's names) picks the BibTeX
                              entry type and the rows below it: a chapter's
                              venue is its book, a thesis's publisher its
                              university. */}
                          <div className="metaRow metaTypeRow">
                            <span className="metaKey">{t("Type")}</span>
                            <span className="metaVal metaValEdit">
                              <MenuSelect
                                value={metaDraft?.kind || itemTypeOf(pageMeta)}
                                options={ITEM_TYPES}
                                label={t("Item type")}
                                onChange={(kind) => setMetaDraft((d) => ({ ...(d || metadataToDraft(pageMeta)), kind }))}
                              />
                            </span>
                          </div>
                          {[
                            [T("Title"), "title"],
                            [T("Authors"), "authors"],
                            ...itemTypeFields(metaDraft?.kind || itemTypeOf(pageMeta), metaDraft),
                            ["DOI", "doi"],
                            ["arXiv", "arxiv_id"],
                          ].map(([label, key]) => (
                            <div className="metaRow" key={key}>
                              <span className="metaKey">{t(label)}</span>
                              <span className="metaVal metaValEdit">
                                <input
                                  className="metaInput"
                                  value={metaDraft?.[key] ?? ""}
                                  onChange={(e) => setMetaDraft((d) => ({ ...(d || metadataToDraft(null)), [key]: e.target.value }))}
                                  onKeyDown={(e) => {
                                    if (composing(e)) return;
                                    if (e.key !== "Enter") return;
                                    e.preventDefault();
                                    // Enter = Save (only when something actually changed)
                                    if (metaDirty) saveMetaEdits();
                                  }}
                                  placeholder="—"
                                />
                                {/* Identifier rows: open the registry page, or copy its URL. */}
                                {(key === "doi" || key === "arxiv_id") && metaDraft?.[key]?.trim() ? (() => {
                                  const id = metaDraft[key].trim();
                                  const url = key === "doi" ? `https://doi.org/${id}` : `https://arxiv.org/abs/${id}`;
                                  const site = key === "doi" ? "doi.org" : "arXiv";
                                  return (
                                    <>
                                      <a className="metaLink" href={url} target="_blank" rel="noreferrer" title={t("Open on {site}", { site: site })}>
                                        <ExternalLinkIcon size={14} />
                                      </a>
                                      <button
                                        className="chatMsgActionBtn metaRowBtn"
                                        title={t("Copy the {site} link", { site: site })}
                                        aria-label={t("Copy {site} link", { site: site })}
                                        onClick={() => copyFlash(key, url)}
                                      >
                                        <IconSwap swap={copiedKey === key} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
                                      </button>
                                    </>
                                  );
                                })() : null}
                                {key === "title" ? (
                                  <button
                                    className="searchToggle metaRowBtn"
                                    title={t("AI: read the PDF and fill in the paper's title")}
                                    aria-label={t("Fill in title with AI")}
                                    disabled={aiTitleBusy}
                                    onClick={aiFillTitle}
                                  >{aiTitleBusy ? "…" : <SparklesIcon size={14} />}</button>
                                ) : null}
                              </span>
                            </div>
                          ))}
                          {/* The citation key the bibliography export will
                              use. Empty = generated from the record (shown as
                              the placeholder); typed = pinned, and a refetch
                              leaves it alone. */}
                          <div className="metaRow">
                            <span className="metaKey">{t("Cite key")}</span>
                            <span className="metaVal metaValEdit">
                              <input
                                className="metaInput metaCiteKey"
                                value={citeKeyDraft}
                                onChange={(e) => setCiteKeyDraft(e.target.value)}
                                onKeyDown={(e) => {
                                  if (composing(e)) return;
                                  if (e.key !== "Enter") return;
                                  e.preventDefault();
                                  if (metaDirty) saveMetaEdits();
                                }}
                                placeholder={citationKeyOf(pageBibtex) || "—"}
                                title={t("The key your LaTeX document cites this paper by. Leave it empty to generate it from the authors and year; type one to pin it, and a metadata refresh will keep it.")}
                                aria-label={t("Citation key")}
                              />
                              {pageCiteKey ? (
                                <button
                                  className="chatMsgActionBtn metaRowBtn"
                                  title={t("Unpin: go back to the generated key")}
                                  aria-label={t("Unpin the citation key")}
                                  onClick={() => setCiteKeyDraft("")}
                                >
                                  <PinIcon size={14} />
                                </button>
                              ) : null}
                            </span>
                          </div>
                          {metaSrc ? (
                            <div className="metaRow">
                              <span className="metaKey">{t("Source")}</span>
                              <span className="metaVal metaValEdit">
                                <span className={metaSrc.warn ? "metaValWarn" : undefined} title={t(metaSrc.hint)}>
                                  {t(metaSrc.label)}
                                </span>
                                {metaSrc.warn && !readOnly ? (
                                  <button
                                    className="uiBtn sm metaVerifyBtn"
                                    title={t("I checked these fields against the paper — mark the record as correct")}
                                    onClick={verifyMeta}
                                  >
                                    <CheckIcon size={12} />{t("Verify")}
                                  </button>
                                ) : null}
                              </span>
                            </div>
                          ) : null}
                          {pageMeta?.at ? (
                            <div className="metaRow">
                              <span className="metaKey">{pageMeta.source === "manual" ? t("Edited") : t("Fetched")}</span>
                              <span className="metaVal">{fmtDate(pageMeta.at, { dateStyle: "medium", timeStyle: "short" })}</span>
                            </div>
                          ) : null}
                          <div className="metaRow">
                            <span className="metaKey">{t("Status")}</span>
                            <span className="metaVal metaStatus">
                              <span className={`metaCell ${!pdfTextInfo || pdfTextInfo.checking || pdfTextInfo.error ? "muted" : pdfTextInfo.ok ? "ok" : "bad"}`}
                                title={!pdfTextInfo || pdfTextInfo.checking ? t("Checking whether the PDF has a text layer")
                                  : pdfTextInfo.error ? t("Text check failed — {error}", { error: pdfTextInfo.error })
                                  : !pdfTextInfo.found ? t("The PDF file is not on the server")
                                  : pdfTextInfo.ok ? t("The PDF has a text layer — the AI and search can read it")
                                  : t("No text layer — scanned or image-only? The AI can't read it")}>
                                <i className="setDot" />{!pdfTextInfo || pdfTextInfo.checking ? "checking" : pdfTextInfo.error ? t("text ?") : !pdfTextInfo.found ? t("no file") : pdfTextInfo.ok ? "text" : t("no text")}
                              </span>
                              <span className={`metaCell ${!pdfTextInfo || pdfTextInfo.checking || pdfTextInfo.error || pdfTextInfo.indexed === undefined ? "muted" : pdfTextInfo.indexed ? "ok" : "muted"}`}
                                title={t("Whether library-wide search can find text in this paper. Papers index automatically in the background; Settings → Library maintenance → Rebuild forces a full re-index.")}>
                                <i className="setDot" />{!pdfTextInfo || pdfTextInfo.checking ? "…" : pdfTextInfo.error || pdfTextInfo.indexed === undefined ? t("index ?")
                                  : pdfTextInfo.indexed ? "indexed" : pdfTextInfo.index_stale ? t("stale index") : t("not indexed")}
                              </span>
                              {pdfTextInfo?.ok ? (
                                <button className="searchToggle metaRowBtn" style={{ marginLeft: "auto" }}
                                  title={t("Preview the extracted text (what the AI reads)")}
                                  onClick={openPdfTextPreview}><EyeIcon size={14} /></button>
                              ) : null}
                              {pdfTextInfo && !pdfTextInfo.checking && !pdfTextInfo.ok ? (
                                <button
                                  className="searchToggle metaRowBtn"
                                  style={{ marginLeft: "auto" }}
                                  title={t("Re-check text extraction (e.g. after replacing the source file) — retries the metadata lookup if text appears")}
                                  onClick={() => checkPdfText(true)}
                                >↻</button>
                              ) : null}
                            </span>
                          </div>
                          {doc && inputUrl ? (
                            <div className="metaRow">
                              <span className="metaKey">{t("Source file")}</span>
                              <span className="metaVal metaValEdit">
                                <span className="metaPath" title={inputUrl}>{inputUrl}</span>
                                <button
                                  className="chatMsgActionBtn metaRowBtn"
                                  title={t("Copy the source URL")}
                                  aria-label={t("Copy source URL")}
                                  onClick={() => copyFlash("source", inputUrl)}
                                >
                                  <IconSwap swap={copiedKey === "source"} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
                                </button>
                              </span>
                            </div>
                          ) : null}
                          {pdfTextPreview ? (
                            <DialogOverlay {...backdropPress(() => setPdfTextPreview(null))}>
                              <div className="reportModal" style={{ width: "min(640px, calc(100vw - 32px))" }} onClick={(e) => e.stopPropagation()}>
                                <div className="reportModalTitle">{t("Extracted PDF text")}</div>
                                <div className="reportPageList" style={{ maxHeight: "60vh", whiteSpace: "pre-wrap", fontSize: "calc(12px * var(--ui-font-scale, 1))", color: "var(--text-secondary)", padding: 10 }}>
                                  {pdfTextPreview.loading ? t("Extracting…") : pdfTextPreview.text}
                                </div>
                                {!pdfTextPreview.loading ? <div className="reportModalHint">{t("First 12,000 characters — the AI context is drawn from this.")}</div> : null}
                                <div className="reportModalBtns">
                                  <button className="uiBtn" onClick={() => setPdfTextPreview(null)}>{t("Close")}</button>
                                </div>
                              </div>
                            </DialogOverlay>
                          ) : null}
                        </div>
                        {!pageMeta ? (
                          <div className="popoverHint">{metaBusy
                            ? t("Fetching metadata…")
                            : focusedBlock?.properties?.meta_error
                              ? t("A previous lookup found nothing — it won't retry automatically. Fill the fields in by hand, or hit ↻ to retry.")
                              : t("No metadata found — fill the fields in by hand, or hit ↻ to retry.")}</div>
                        ) : null}
                        {metaDirty ? (
                          <div className="reportModalBtns">
                            <button className="uiBtn primary" disabled={metaSaving} onClick={saveMetaEdits}>
                              <MorphText>{metaSaving ? t("Saving…") : t("Save metadata")}</MorphText>
                            </button>
                          </div>
                        ) : null}
                      </Popover>
                    ) : null}
                  </span>
                ) : null}
                {openRules.trash ? (
                  <button
                    className="pageActionBtn pageDeleteBtn"
                    title={readOnly ? t("You can only view this workspace") : t("Delete this page")}
                    disabled={readOnly}
                    onClick={deleteOpenPage}
                  >
                    <TrashIcon size={16} />
                  </button>
                ) : null}
                </>) : null}
              </div>
            ) : null}
            {focusedBlockId && collab.peers.length ? (
              <PresenceBar
                peers={collab.peers}
                onJump={(id) => {
                  if (!id) return;
                  scrollToBlock(id);
                  reveal(id);
                }}
              />
            ) : null}
            </div>
          </div>}

          <div className={`blockList${aiScan ? " aiPageRead" : ""}`} ref={notesTextScale.ref} style={notesTextScale.style}>
            {notesTextScale.badge}
            {/* Recently-viewed shortcut strip. Labels are browsed like folders
                (the kind toggle's Labels mode) and shown as chips on each row,
                so this is the only carousel left. */}
            {homeMode && lib.history && recentViewedPages.length > 0 ? (
              <CardCarousel label={t("Recently viewed")} className="recentsCarousel" guide="home.recents">
                {recentViewedPages.map((b) => (
                  <PageCard key={b._pageId} title={b.content} glyph={<FileGlyph format={b._doc?.format} />} preview={b._preview}
                    snap={recentThumbs ? pageSnaps[b._pageId]?.img : null}
                    kind={pageKindLabel(b._doc)} time={formatRelativeTime(b._viewedAt)}
                    folders={b._folderChips} labels={b._labelChips} inside={b._path} pages={b._subPages} labelMode={fileLabels}
                    className={`${pageDropClass(b._pageId)} ${selectedPages.has(b._pageId) ? "selected" : ""}`}
                    {...pageDropProps(b._pageId)}
                    onClick={() => openPage(b._pageId)}
                    onMenu={openPageMenu(b._pageId, b.content)}>
                    <button
                      className="uiClose uiCloseSm pageCardClose"
                      title={t("Remove from Recently viewed")}
                      aria-label={t("Remove from Recently viewed")}
                      onClick={(e) => { e.stopPropagation(); removeRecentView(b._pageId); }}
                    ><XIcon size={14} /></button>
                  </PageCard>
                ))}
              </CardCarousel>
            ) : null}
            {homeMode && lib.pin && !labelFilter && !folderFilter && pinnedItems.length > 0 ? (
              <div className="pinnedSection">
                <div className="pinnedLabel"><PinIcon filled size={14} /> {t("Pinned")}</div>
                <div className="pinnedStrip" ref={pinnedStripRef} data-guide="home.pinned">
                  {pinnedItems.map((item) => item.kind === "folder" ? (() => { const f = item.id; return (
                    <PageCard
                      key={item.key}
                      {...folderCardProps(f)}
                      className={`${folderDropClass(f)} ${selectedFolders.has(f) ? "selected" : ""}`}
                      tip={t("{f}\nClick to select · double-click to open · drop a page or folder to move it in", { f: folderPath(libTree, f) })}
                      time={formatRelativeTime(folderMeta[f]?.updated)}
                      draggable
                      // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                      onDoubleClick={() => openFolder(f)}
                    >
                      <button
                        className="pinBtn tilePinBtn pinned"
                        title={t("Unpin")}
                        onClick={(e) => { e.stopPropagation(); setFoldersPinned([f], false); }}
                      ><PinIcon filled size={14} /></button>
                    </PageCard>
                  ); })() : (() => { const b = item.block; return (
                    <PageCard
                      key={b._pageId}
                      className={`${pageDropClass(b._pageId)} ${selectedPages.has(b._pageId) ? "selected" : ""}`}
                      glyph={<FileGlyph format={b._doc?.format} />}
                      preview={b._preview}
                      title={b.content}
                      tip={t("{content}\nClick to select · double-click to open", { content: b.content })}
                      kind={pageKindLabel(b._doc)}
                      time={formatRelativeTime(b._updatedAt)}
                      folders={b._folderChips} labels={b._labelChips} inside={b._path} pages={b._subPages} labelMode={fileLabels}
                      draggable
                      onDragStart={(e) => startPageDrag(e, b._pageId)}
                      {...pageDropProps(b._pageId)}
                      checked={selecting && selectedPages.has(b._pageId)}
                      onClick={(e) => handlePageClick(b, e)}
                      // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                      onDoubleClick={() => openPage(b._pageId)}
                      onMenu={openPageMenu(b._pageId, b.content)}
                    >
                      <button
                        className="pinBtn tilePinBtn pinned"
                        title={t("Unpin")}
                        onClick={(e) => { e.stopPropagation(); setPagesPinned([b._pageId], false); }}
                      ><PinIcon filled size={14} /></button>
                    </PageCard>
                  ); })())}
                </div>
              </div>
            ) : null}
            {homeMode && (folderFilter || labelFilter) ? (
              <div className="folderBrowser">
                    {folderFilter && !labelFilter && folderFilter !== lib.root ? (() => {
                    const parent = libTree.folders.get(folderFilter)?.parent || "";
                    return (
                    <div
                      className={`folderRow folderBackRow ${folderDragOver === "__up__" ? "dragOver" : ""}`}
                      onClick={() => openFolder(parent)}
                      onDragOver={(e) => { e.preventDefault(); setFolderDragOver("__up__"); }}
                      onDragLeave={() => setFolderDragOver(null)}
                      onDrop={(e) => dropOnFolder(e, parent, (ids) => removePagesFromFolder(ids, folderFilter))}
                      title={lib.organize ? t("Back — or drop a page or folder here to move it out of this folder") : t("Back")}
                    >
                      <ArrowLeftIcon size={14} />
                      <span className="folderName">{folderPath(libTree, parent) || t("All files")}</span>
                      {lib.organize ? <span className="folderHint">{t("drop here to move out of this folder")}</span> : null}
                    </div>
                    );
                    })() : null}
                    {/* The label view gets the same back row: it drops the
                        label and returns to the folder scope the label was
                        opened from, and a paper dropped on it loses the label. */}
                    {labelFilter ? (
                    <div
                      className={`folderRow folderBackRow ${folderDragOver === "__label_up__" ? "dragOver" : ""}`}
                      onClick={closeLabel}
                      // Inside "No label" there is no label to take off — the
                      // back row is plain navigation there.
                      {...(labelFilter === NO_LABEL || !lib.organize ? { title: T("Back") } : {
                        onDragOver: (e) => { e.preventDefault(); setFolderDragOver("__label_up__"); },
                        onDragLeave: () => setFolderDragOver(null),
                        onDrop: (e) => dropOnLabel(e, labelFilter, (ids) => removePagesFromLabel(ids, labelFilter)),
                        title: T("Back — or drop a page here to take this label off it"),
                      })}
                    >
                      <ArrowLeftIcon size={14} />
                      <span className="folderName">{folderPath(libTree, folderFilter) || t("All files")}</span>
                      {labelFilter === NO_LABEL || !lib.organize ? null : <span className="folderHint">{t("drop here to remove this label")}</span>}
                    </div>
                    ) : null}
                    <div className="folderCurrent">
                      {labelFilter ? <LabelIcon size={16} strokeDasharray={labelFilter === NO_LABEL ? "2 1.5" : undefined} /> : <FolderOpenIcon size={16} />}
                      {/* Breadcrumb: every folder of the path navigates to its level —
                          from the library's root on (a folder share starts at its
                          folder) — and files a page or moves a folder dropped on it */}
                      {folderCrumbs(folderFilter).map(({ id, name, sep }) => (
                        <span key={id}>
                          {sep ? <span className="crumbSep">/</span> : null}
                          <button className={`crumbBtn ${folderDragOver === `crumb:${id}` ? "dragOver" : ""}`} onClick={() => openFolder(id)}
                            {...(lib.organize ? {
                              onDragOver: (e) => { e.preventDefault(); setFolderDragOver(`crumb:${id}`); },
                              onDragLeave: () => setFolderDragOver(null),
                              onDrop: (e) => dropOnFolder(e, id),
                            } : {})}>{name}</button>
                        </span>
                      ))}
                      {labelFilter ? (
                        <span>
                          {folderFilter ? <span className="crumbSep">/</span> : null}
                          {labelFilter === NO_LABEL ? (
                            <span className="crumbBtn" title={t("Pages without any label")}>{NO_LABEL_TITLE}</span>
                          ) : (
                          <button
                            className="crumbBtn"
                            title={lib.organize ? t("Right-click to rename or delete this label") : undefined}
                            {...menuPress(openTagMenu("label", labelFilter))}
                          >{labelName(libTree, labelFilter)}</button>
                          )}
                        </span>
                      ) : null}
                    </div>
              </div>
            ) : null}
            {homeMode ? (
              <div className="homeListBar" data-guide="home.listing">
                <span className={`homeListLabel ${labelFilter || folderFilter ? "" : "homeListRoot"}`}>{labelFilter === NO_LABEL ? t("Unlabelled") : labelFilter ? t("Labelled") : folderFilter ? t("Contents") : t("Library")}</span>
                <span className="homeListSpacer" />
                <ListFindBox value={homeQuery} onChange={setHomeQuery} keyLabel={commandKeyLabel("app.search")} byName={!!workFolder}
                  onEnter={!workFolder && homeMatchCount === 0 ? () => openSearchWith(homeQueryText) : undefined} />
                {workFolder ? (
                  <MenuSelect
                    icon={ArrowUpDownIcon}
                    label={t("Sort this work folder's files")}
                    value={workSort.sort}
                    onChange={workSort.setSort}
                    options={[
                      ["name", t("Name"), TypeIcon],
                      ["format", t("Format"), FileIcon],
                      ["size", t("File size"), HardDriveIcon],
                      ["modified", t("Modified"), PenIcon],
                    ]}
                  />
                ) : (
                  <MenuSelect
                    icon={ArrowUpDownIcon}
                    label={labelFilter ? t("Sort this label") : folderFilter ? t("Sort this folder — subfolders inherit it") : t("Sort the library — folders inherit it")}
                    value={homeSort}
                    onChange={changeHomeSort}
                    options={[
                      ["updated", t("Recently modified"), PenIcon],
                      ["created", t("Recently added"), PlusIcon],
                      ["viewed", t("Recently viewed"), EyeIcon],
                      ["title", t("Title A–Z"), TypeIcon],
                      ["manual", t("Custom order"), MoveVerticalIcon],
                    ]}
                  />
                )}
                {/* A label holds papers only — nothing to filter by kind
                    there; a work folder lists its files, as a list. */}
                {labelFilter ? null : (
                  <KindToggle
                    value={homeKinds}
                    onChange={changeHomeKinds}
                    kinds={workFolder ? [] : undefined}
                    scopeLabel={folderFilter ? t("Shown in this folder") : t("Shown in the library")}
                  />
                )}
                {workFolder ? null : <ViewToggle view={homeView} onChange={changeHomeView} />}
                {/* the work folder's feedback: this directory's open comments, to read or send to an agent (feedback/) */}
                {workFolder && lib.organize ? (
                  <button type="button" className="uiBtn sm" title={t("The open comments on this folder's files, to read or send to an agent")}
                    onClick={() => openFeedback({ kind: "folder", id: folderFilter })}>{t("Feedback")}</button>
                ) : null}
                {selecting ? (
                  <>
                    <span className="homeSelectCount">{t("{n} selected", { n: selectionCount })}</span>
                    <MenuButton open={openSelectionMenu} disabled={!selectionCount} />
                    <button type="button" className="uiBtn sm" onClick={clearSelection}>{t("Done")}</button>
                  </>
                ) : (
                  <button type="button" className="uiBtn sm" title={t("Pick several pages or folders to act on together")}
                    data-guide="home.select" onClick={() => setSelecting(true)}>{t("Select")}</button>
                )}
                {lib.organize && !folderFilter && !labelFilter ? (
                  <button type="button" className="ctlBtn" title={t("Recently deleted")} data-guide="home.trash"
                    aria-label={t("Recently deleted")} onClick={() => setTrashOpen(true)}
                    onPointerEnter={preloadTrash} onFocus={preloadTrash}>
                    <Trash2Icon size={16} />
                  </button>
                ) : null}
              </div>
            ) : null}
            {homeMode && !workFolder && homeMatchCount === 0 ? searchElsewhere(true) : null}
            {/* Until the listing has come back (or its read failed) it is
                blank: no "empty" line before the rows, and nothing the rows
                would push down. */}
            {homeMode && !homeLoaded && !homeFailed ? null : homeMode && workFolder ? (
                <FolderList dirs={workDirs} rows={workRows} read={folderRows} folderId={folderFilter} sort={workSort.sort} query={homeQuery}
                  atTop={folderFilter === workFolder} choosing={!!work.linkOf(workFolder)?.choosing} onChoose={lib.organize ? work.chooser(workFolder) : null}
                  onNewPage={newPageAllowed ? () => createPage() : null} onRetry={() => fetchHomeBlocks({ still: true })}
                  listRef={homeListingRef} still={homeStill} onBlank={clearSelection}
                  row={{
                    selecting, selectedFolders, selectedPages, folderMenu: (id) => openTagMenu("folder", id), pageMenu: openPageMenu, menuPress,
                    onFolderClick: handleFolderClick, onFolderOpen: openFolder, onPageClick: handlePageClick, onPageOpen: openPage,
                    editingId: homeEditingId, onRename: commitPageRename, onRenameCancel: () => setHomeEditingId(null),
                    onPin: lib.pin ? setPagesPinned : null, draggable: lib.organize, onDragStart: startPageDrag,
                  }} />
            ) : homeMode && homeView === "grid" ? (
                <>
                  {homeEmptyLine}
                  <div className="fileGrid" ref={homeListingRef} onClick={(e) => { if (e.target.classList.contains("fileGrid")) clearSelection(); }}>
                    {newPageAllowed ? (
                      <PageCard
                        className="pageCardAdd"
                        glyph={<FilePlusIcon className="tileGlyph" size={null} strokeWidth={1.5} />}
                        title={t("New page")}
                        tip={t("Start a blank page here")}
                        labelMode={fileLabels}
                        onClick={() => createPage()}
                      />
                    ) : null}
                    {!newFolderAllowed ? null : newFolderOpen ? (
                      <PageCard
                        className="pageCardAdd"
                        glyph={<FolderGlyph />}
                        kind="Folder"
                        labelMode={fileLabels}
                        renameNode={
                          <input
                            autoFocus
                            className="tileRenameInput"
                            value={newFolderName}
                            placeholder={t("Folder name…")}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => setNewFolderName(e.target.value)}
                            onKeyDown={(e) => {
                              if (composing(e)) return;
                              if (e.key === "Enter") { e.preventDefault(); commitNewFolder(); }
                              else if (e.key === "Escape") { setNewFolderOpen(false); setNewFolderName(""); }
                            }}
                            onBlur={commitNewFolder}
                          />
                        }
                      />
                    ) : (
                      <PageCard
                        className="pageCardAdd"
                        glyph={<FolderPlusIcon className="tileGlyph" size={null} strokeWidth={1.5} />}
                        title={t("New folder")}
                        labelMode={fileLabels}
                        onClick={() => { setNewFolderName(""); setNewFolderOpen(true); }}
                      />
                    )}
                    <ListMotion keys={homeVisibleKeys} scope={homeListingScope} still={homeStill} enter="card" leave="card">
                    {withSearchMore(homeVisibleItems.map((item) => {
                      const dim = homeQuery && !item._match ? "homeDim" : "";
                      if (item.kind === "label") { const l = item.label; return (
                      <PageCard
                        key={item.key}
                        data-motion-key={item.key}
                        className={`${dim} ${folderDragOver === l ? "dragOver" : ""} ${selectedLabels.has(l) ? "selected" : ""}`}
                        glyph={<LabelGlyph dashed={l === NO_LABEL} />}
                        title={labelName(libTree, l)}
                        tip={l === NO_LABEL
                          ? t("Pages without any label · double-click to open · drop a page to clear its labels")
                          : t("Click to select · double-click to open · drop a page to label it")}
                        kind="Label"
                        count={labelMeta[l]?.count || 0}
                        time={cardTime(item)}
                        labelMode={fileLabels}
                        checked={selecting && selectedLabels.has(l)}
                        onClick={(e) => handleLabelClick(l, e)}
                        // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                        onDoubleClick={() => openLabel(l)}
                        onMenu={l === NO_LABEL ? undefined : openTagMenu("label", l)}
                        onDragOver={(e) => { e.preventDefault(); setFolderDragOver(l); }}
                        onDragLeave={() => setFolderDragOver(null)}
                        onDrop={(e) => dropOnLabel(e, l)}
                      />
                      ); }
                      if (item.kind === "folder") { const f = item.folder; return (
                      <PageCard
                        key={item.key}
                        data-motion-key={item.key}
                        {...folderCardProps(f)}
                        className={`${dim} ${folderDropClass(f)} ${selectedFolders.has(f) ? "selected" : ""}`}
                        tip={lib.organize ? t("Click to select · double-click to open · drop a page or folder to move it in") : t("Click to select · double-click to open")}
                        time={cardTime(item)}
                        renameNode={folderRenaming?.id === f ? (
                          <input
                            autoFocus
                            className="tileRenameInput"
                            defaultValue={libTree.folders.get(f).name}
                            onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => {
                              if (composing(e)) return;
                              if (e.key === "Enter") renameFolder(f, e.currentTarget.value);
                              else if (e.key === "Escape") setFolderRenaming(null);
                            }}
                            onBlur={(e) => renameFolder(f, e.currentTarget.value)}
                          />
                        ) : null}
                        draggable={lib.organize && folderRenaming?.id !== f}
                        // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                        onDoubleClick={() => { if (folderRenaming?.id !== f) openFolder(f); }}
                      />
                      ); }
                      const b = item.block;
                      const id = b._pageId;
                      const isPinned = !!b._pinned;
                      const isEditing = homeEditingId === id;
                      return (
                        <PageCard
                          key={id}
                          data-motion-key={item.key}
                          className={`${dim} ${pageDropClass(id)} ${selectedPages.has(id) ? "selected" : ""}`}
                          data-guide="home.card"
                          glyph={<FileGlyph format={b._doc?.format} />}
                          preview={b._preview}
                          title={b.content}
                          tip={t("{content}\nClick to select · double-click to open", { content: b.content })}
                          kind={pageKindLabel(b._doc)}
                          time={cardTime(item)}
                          folders={b._folderChips} labels={b._labelChips} inside={b._path} pages={b._subPages} labelMode={fileLabels}
                          renameNode={isEditing ? (
                            <input
                              autoFocus
                              className="tileRenameInput"
                              defaultValue={b.content}
                              onClick={(e) => e.stopPropagation()}
                              onKeyDown={(e) => {
                                if (composing(e)) return;
                                if (e.key === "Enter") commitPageRename(id, e.currentTarget.value);
                                else if (e.key === "Escape") setHomeEditingId(null);
                              }}
                              onBlur={(e) => commitPageRename(id, e.currentTarget.value)}
                            />
                          ) : null}
                          draggable={lib.organize && !isEditing}
                          checked={selecting && selectedPages.has(id)}
                          onDragStart={(e) => startPageDrag(e, id)}
                          {...pageDropProps(id)}
                          onClick={(e) => handlePageClick(b, e)}
                          // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                          onDoubleClick={() => { if (!isEditing) openPage(id); }}
                          onMenu={openPageMenu(id, b.content)}
                        >
                          {lib.pin ? (
                            <button
                              className={`pinBtn tilePinBtn ${isPinned ? "pinned" : ""}`}
                              title={isPinned ? t("Unpin") : t("Pin to top")}
                              onClick={(e) => { e.stopPropagation(); setPagesPinned([id], !isPinned); }}
                            ><PinIcon filled={isPinned} size={14} /></button>
                          ) : null}
                        </PageCard>
                      );
                    }))}
                    </ListMotion>
                  </div>
                  {homeItems.length > homeVisibleItems.length ? (
                    <button ref={loadMoreRef} className="loadMoreBtn" onClick={() => setHomeShowCount((c) => c + HOME_PAGE_CHUNK)}>
                      {t("Showing {shown} of {total} — load more", { shown: homeVisibleItems.length, total: homeItems.length })}
                    </button>
                  ) : null}
                </>
            ) : homeMode ? (
                <>
                  {homeEmptyLine}
                  <div className="fileList" ref={homeListingRef} onClick={(e) => { if (e.target.classList.contains("fileList")) clearSelection(); }}>
                    {newPageAllowed ? (
                      <button className="folderRow folderNewBtn" onClick={() => createPage()} title={t("Start a blank page here")}>
                        <FilePlusIcon size={16} />
                        <span className="folderName">{t("New page")}</span>
                      </button>
                    ) : null}
                    {!newFolderAllowed ? null : newFolderOpen ? (
                      <div className="folderRow folderNewRow">
                        <FolderPlusIcon size={16} />
                        <input
                          autoFocus
                          className="folderNewInput"
                          value={newFolderName}
                          onChange={(e) => setNewFolderName(e.target.value)}
                          placeholder={t("Folder name…")}
                          onKeyDown={(e) => {
                            if (composing(e)) return;
                            if (e.key === "Enter") { e.preventDefault(); commitNewFolder(); }
                            else if (e.key === "Escape") { setNewFolderOpen(false); setNewFolderName(""); }
                          }}
                          onBlur={commitNewFolder}
                        />
                      </div>
                    ) : (
                      <button className="folderRow folderNewBtn" onClick={() => { setNewFolderName(""); setNewFolderOpen(true); }}>
                        <FolderPlusIcon size={16} />
                        <span className="folderName">{t("New folder")}</span>
                      </button>
                    )}
                    {homeVisibleItems.length ? (
                      <div className="fileListHead" aria-hidden="true">
                        <span className="fileListHeadName">{t("Name")}</span>
                        <span className="fileRowKind">{t("Kind")}</span>
                        <span className="fileRowDate">{dateColumnTitle}</span>
                        <span className="fileRowPinSlot" />
                        {menuSlot}
                      </div>
                    ) : null}
                    <ListMotion keys={homeVisibleKeys} scope={homeListingScope} still={homeStill} enter="fade" leave="row">
                    {withSearchMore(homeVisibleItems.map((item) => {
                      const dim = homeQuery && !item._match ? "homeDim" : "";
                      if (item.kind === "label") { const l = item.label; const menu = l === NO_LABEL ? null : openTagMenu("label", l); return (
                      <div
                        key={item.key}
                        data-motion-key={item.key}
                        className={`folderRow labelRow rowMenuHost ${dim} ${folderDragOver === l ? "dragOver" : ""} ${selectedLabels.has(l) ? "selected" : ""}`}
                        onClick={(e) => handleLabelClick(l, e)}
                        // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                        onDoubleClick={() => openLabel(l)}
                        {...menuPress(menu)}
                        onDragOver={(e) => { e.preventDefault(); setFolderDragOver(l); }}
                        onDragLeave={() => setFolderDragOver(null)}
                        onDrop={(e) => dropOnLabel(e, l)}
                        title={l === NO_LABEL
                          ? t("Pages without any label · double-click to open · drop a page to clear its labels")
                          : lib.organize ? t("Click to select · double-click to open · right-click to rename or delete · drop a page to label it") : t("Click to select · double-click to open")}
                      >
                        {selecting && selectedLabels.has(l) ? <SelectCheck /> : <LabelIcon size={16} strokeDasharray={l === NO_LABEL ? "2 1.5" : undefined} />}
                        <span className="folderName">{labelName(libTree, l)}</span>
                        {rowColumns(item, tn("{n} page", "{n} pages", labelMeta[l]?.count || 0), menu)}
                      </div>
                      ); }
                      if (item.kind === "folder") { const f = item.folder; const menu = openTagMenu("folder", f); return (
                      <div
                        key={item.key}
                        data-motion-key={item.key}
                        className={`folderRow rowMenuHost ${dim} ${folderDropClass(f)} ${selectedFolders.has(f) ? "selected" : ""}`}
                        draggable={lib.organize && folderRenaming?.id !== f}
                        onDragStart={(e) => startFolderDrag(e, f)}
                        onClick={(e) => handleFolderClick(f, e)}
                        // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                        onDoubleClick={() => { if (folderRenaming?.id !== f) openFolder(f); }}
                        {...menuPress(menu)}
                        onDragOver={(e) => dragOverFolder(e, f)}
                        onDragLeave={() => setFolderDragOver(null)}
                        onDrop={(e) => dropOnFolder(e, f)}
                        title={lib.organize ? t("Click to select · double-click to open · right-click to rename or delete · drop a page or folder to move it in") : t("Click to select · double-click to open")}
                      >
                        {selecting && selectedFolders.has(f) ? <SelectCheck /> : <FolderIcon size={16} />}
                        {folderRenaming?.id === f ? (
                          <input
                            autoFocus
                            className="folderNewInput"
                            value={folderRenaming.draft}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => setFolderRenaming({ id: f, draft: e.target.value })}
                            onKeyDown={(e) => {
                              if (composing(e)) return;
                              if (e.key === "Enter") renameFolder(f, folderRenaming.draft);
                              else if (e.key === "Escape") setFolderRenaming(null);
                            }}
                            onBlur={() => renameFolder(f, folderRenaming.draft)}
                          />
                        ) : (
                          <span className="folderName">{libTree.folders.get(f).name}</span>
                        )}
                        {rowColumns(item, tn("{n} page", "{n} pages", folderMeta[f]?.count || 0), menu)}
                      </div>
                      ); }
                      const b = item.block;
                      const id = b._pageId;
                      const isPinned = !!b._pinned;
                      const isEditing = homeEditingId === id;
                      const menu = openPageMenu(id, b.content);
                      return (
                        <div
                          key={id}
                          data-motion-key={item.key}
                          className={`fileRow rowMenuHost ${dim} ${pageDropClass(id)} ${selectedPages.has(id) ? "selected" : ""}`}
                          data-guide="home.card"
                          draggable={lib.organize && !isEditing}
                          onDragStart={(e) => startPageDrag(e, id)}
                          {...pageDropProps(id)}
                          onClick={(e) => handlePageClick(b, e)}
                          // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                          onDoubleClick={() => { if (!isEditing) openPage(id); }}
                          {...menuPress(menu)}
                          title={t("{content}\nClick to select · double-click to open", { content: b.content })}
                        >
                          <span className="fileRowIcon">{selecting && selectedPages.has(id) ? <SelectCheck /> : <FileGlyph format={b._doc?.format} />}</span>
                          {isEditing ? (
                            <input
                              autoFocus
                              className="fileRowRename"
                              defaultValue={b.content}
                              onClick={(e) => e.stopPropagation()}
                              onKeyDown={(e) => {
                                if (composing(e)) return;
                                if (e.key === "Enter") commitPageRename(id, e.currentTarget.value);
                                else if (e.key === "Escape") setHomeEditingId(null);
                              }}
                              onBlur={(e) => commitPageRename(id, e.currentTarget.value)}
                            />
                          ) : (
                            <span className="fileRowName">{b.content || t("Untitled")}</span>
                          )}
                          <CardLabels className="fileRowLabels" folders={b._folderChips} labels={b._labelChips}
                            inside={b._path} pages={b._subPages} mode={fileLabels} onLabelMenu={(l) => openTagMenu("label", l)} />
                          {rowColumns(item, pageKindLabel(b._doc), menu, lib.pin ? (
                            <button
                              className={`pinBtn fileRowPin ${isPinned ? "pinned" : ""}`}
                              title={isPinned ? t("Unpin") : t("Pin to top")}
                              onClick={(e) => { e.stopPropagation(); setPagesPinned([id], !isPinned); }}
                            ><PinIcon filled={isPinned} size={14} /></button>
                          ) : null)}
                        </div>
                      );
                    }))}
                    </ListMotion>
                  </div>
                  {homeItems.length > homeVisibleItems.length ? (
                    <button ref={loadMoreRef} className="loadMoreBtn" onClick={() => setHomeShowCount((c) => c + HOME_PAGE_CHUNK)}>
                      {t("Showing {shown} of {total} — load more", { shown: homeVisibleItems.length, total: homeItems.length })}
                    </button>
                  ) : null}
                </>
            ) : (
            treeBlocks.length === 0 ? (
              <>{notesTail ? null : <div className="empty">{t("No blocks yet.")}</div>}{subPagesPanel}{backlinksPanel}{notesTail}</>
            ) : (
              (() => {
                // Indent and outdent re-parent the block, which remounts its
                // row: an open editor stays open with its selection, as one
                // does through a move (onMoveBlock).
                const keepOpenEditor = (id) => {
                  if (view.editingId !== id) return;
                  const editor = blockRefs.current[id]?.current;
                  keepEditorThrough(id, editor ? editor.selectionStart : "end", editor?.selectionEnd);
                };
                const rowProps = {
                  focusedId,
                  setFocusedId,
                  // The notes selected whole (useBlockSelection.js): BlockTree
                  // hands each row whether it is in it.
                  selection: blockSel.ids,
                  onSelectBlock: readOnly ? undefined : blockSel.selectBlock,
                  onSelectAll: readOnly ? undefined : blockSel.selectEvery,
                  onHandleSelect: blockSel.handleSelect,
                  dragSelection: blockSel.dragSelection,
                  onSelectionAction: blockSel.run,
                  view,
                  foldMoving: foldMovesRef.current.get(view),
                  flashingId, // a page's landing row flashes once (landingRef)
                  // The AI agent's live footprint (handleAgentEvent); rootId
                  // places a ghost row for a block being created at top level.
                  aiMarks,
                  aiLive,
                  aiScan,
                  rootId: focusedBlockId,
                  pages: pageBlocks, // the [[ link picker's page list
                  onJump: jumpToHighlightId,
                  onShowOnPage: showOnPage,
                  // Sheets: numbered as pages; drawn in place unless a notebook's viewer draws them.
                  sheetNumbers,
                  inlineSheets: !notebook,
                  onInsertSheet: readOnly ? undefined : insertSheetAt,
                  // "/page": a share's editor makes it inside the shared page too.
                  onNewPage: readOnly ? undefined : createLinkedPage,
                  // The @ menu's "Invite…" shares the page (Notion's invite to a
                  // page); its "Remind me" wakes the reminders.
                  onInvite: readOnly || shareMode || authUser?.is_guest ? undefined : () => openPageShare(focusedBlockId),
                  onReminderSet: reminders.noteSet,
                  onAddSheetAfter: readOnly ? undefined : addSheetAfter,
                  onEnterAttachMode: readOnly ? null : startAttachMode,
                  onUnlinkHighlight: readOnly ? null : unlinkHighlightFromBlock,
                  onMoveHighlight: readOnly || !isPdf(doc) ? null : startHighlightMove,
                  onOpenLinkTarget: (b) => {
                    const p = b.properties || {};
                    if (p.link_page_id) {
                      if (p.link_block_id) pendingJumpRef.current = p.link_block_id;
                      openBlock(p.link_page_id, { pushNav: true });
                    } else if (p.link_url) handleDocLink(p.link_url);
                  },
                  registerRef,
                  readOnly,
                  // The editing bar's Undo / Redo and the iPad keyboard's.
                  onUndo: readOnly ? undefined : undoBlocks,
                  // Area-highlight cards show their crop, re-rendered from the
                  // loaded document each session (never stored; the chat's
                  // area selection is drawn by the server instead); docNonce
                  // retries crops once the PDF is up,
                  // docKey keeps one paper's crops from serving another's.
                  captureArea: capturePdfArea,
                  docNonce: pdfDocNonce,
                  docKey: pdfUrl,
                  lookupBlock: (id) => treeById.get(id),
                  refLabelsById,
                  highlightColors,
                  refCache,
                  onFetchRefs,
                  onCacheRef,
                  onBlockRefClick: openBlockLink,
                  // Functional updates: these two fire from editor lifecycle
                  // (CodeMirror onChange, blur on unmount when a row moves in
                  // the tree), where the closure's tree can be a render stale
                  // and would overwrite a structural change. Closing an editor
                  // saves right away (the autosave skips its debounce once).
                  onChangeText: (id, text, selectionBefore) => {
                    if (readOnly) return;
                    caretBeforeRef.current = selectionBefore ? { id, ...selectionBefore } : null;
                    textBoxes.touch(id); // a text box refits to the text typed in its row
                    setBlocks((prev) => setBlockText(prev, id, text));
                  },
                  // The open editor's selection: the history's caret
                  // bookkeeping, and our caret for the others on the page.
                  onCaret: (id, from, to) => {
                    caretRef.current = { id, from, to };
                    trackNoteSel(id, from, to);
                    collab.sendCursor({ block: id, anchor: from, head: to });
                  },
                  takeClickPos: takeHandedPress,
                  onStartEdit: (id, editMode) => {
                    if (readOnly) return;
                    // A locked note's row has no editor to open (vault/notesLock.js).
                    if (editMode && notesLocked && lockedRow(findBlock(blocks, id))) return;
                    if (editMode) pendingFocusRef.current = id;
                    else if (keepEditRef.current?.id === id && performance.now() < keepEditRef.current.until) {
                      // The blur of a DOM move (keepEditorThrough); the
                      // focus effect puts the caret back.
                      keepEditRef.current = null;
                      return;
                    } else if (collab.tooLong(id)) {
                      // Its text is too long to save (the notice says so):
                      // the editor stays open on it until it is shortened.
                      return;
                    } else {
                      saveNowRef.current = true;
                      if (caretRef.current?.id === id) caretRef.current = null;
                      collab.sendCursor({ block: id });
                    }
                    setView((v) => (editMode ? withEditing(v, id) : closeEditing(v, id)));
                  },
                  peers: collab.peers,
                  merges,
                  onResolveMerge: resolveMerge,
                  mergeOpen,
                  onMergeOpen: setMergeOpen,
                  mergeNav,
                  enterNewBlock,
                  // `above` inserts before `id` instead (the "+" handle with
                  // Alt held). None on a page whose notes are locked
                  // (vault/notesLock.js): the block made would be a note,
                  // and its row would have no editor. The "+" goes with it.
                  onEnterSibling: notesLocked ? null : (id, { above = false } = {}) => {
                    if (readOnly) return;
                    const { blocks: next, newId } = addSiblingBlock(blocks, id, { above });
                    pendingFocusRef.current = newId;
                    startEditing(newId);
                    setBlocks(next);
                    setFocusedId(newId);
                  },
                  // The new-block key in a plain note (mdLines.newBlockPlan):
                  // the text kept and the block made are one transition, one
                  // undo step. The block goes first under open children, where
                  // the caret is, else right below; a caret before the text
                  // makes it above and stays. `keep` is the live editor's, which
                  // the closure's tree can lag, and reaches an editor that
                  // stays open through its value.
                  onNewBlock: (id, plan) => {
                    if (readOnly || notesLocked) return;
                    const selection = blockRefs.current[id]?.current;
                    if (selection) caretBeforeRef.current = { id, from: selection.selectionStart, to: selection.selectionEnd, keyed: !plan.stay };
                    if (plan.stay) {
                      setBlocks((prev) => setBlockText(prev, id, plan.keep));
                      return;
                    }
                    const host = findBlock(blocks, id);
                    const firstChild = !plan.above && !!host?.children?.length && !isFolded(host, view);
                    const newBlock = makeNewBlock({ content: plan.next });
                    setBlocks((prev) => addSiblingBlock(prev, id, { above: plan.above, keep: plan.keep, firstChild, newBlock }).blocks);
                    if (plan.above) return;
                    pendingFocusRef.current = { id: newBlock.id, caret: plan.caret };
                    startEditing(newBlock.id);
                    setFocusedId(newBlock.id);
                  },
                  // Backspace at the very start of a plain note
                  // (blockModel.mergeIntoAbove): the block it removes takes no
                  // comment thread with it, or nothing happens. A row that
                  // only loses the empty one above stays open, through the
                  // DOM move when that one was its parent.
                  onMergeAbove: (id) => {
                    if (readOnly || notesLocked) return;
                    const plan = mergeIntoAbove(blocks, id, view);
                    if (!plan || comments.threadsByBlock.get(plan.gone)?.length) return;
                    caretBeforeRef.current = { id, from: 0, to: 0, keyed: true };
                    if (plan.focusId === id) {
                      keepEditorThrough(id, 0);
                    } else {
                      pendingFocusRef.current = { id: plan.focusId, caret: plan.caret };
                      startEditing(plan.focusId);
                      setFocusedId(plan.focusId);
                    }
                    setBlocks((prev) => mergeIntoAbove(prev, id, view)?.blocks || prev);
                  },
                  onAddChild: (id) => {
                    if (readOnly || notesLocked) return;
                    const { blocks: next, newId } = addChildBlock(blocks, id);
                    pendingFocusRef.current = newId;
                    // Under a folded parent the new child is unfolded into view.
                    setView((v) => withEditing(revealBlock(next, v, newId), newId));
                    setBlocks(next);
                    setFocusedId(newId);
                  },
                  onIndent: (id) => {
                    if (readOnly || (notesLocked && touchesLocked(blocks, id, "indent"))) return;
                    const next = indentBlock(blocks, id);
                    if (next === blocks) return;
                    keepOpenEditor(id);
                    setBlocks(next);
                    setView((v) => revealBlock(next, v, id)); // its new parent may be folded
                    setFocusedId(id);
                  },
                  onOutdent: (id) => {
                    if (readOnly || (notesLocked && touchesLocked(blocks, id, "outdent"))) return;
                    const next = outdentBlock(blocks, id);
                    if (next === blocks) return;
                    keepOpenEditor(id);
                    setBlocks(next);
                    setFocusedId(id);
                  },
                  onToggle: (id) => {
                    const next = toggleFold(blocks, view, id);
                    foldMovesRef.current.set(next.view, id);
                    setBlocks(next.blocks);
                    setView(next.view);
                  },
                  // Delete the subtree (the handle menu, Backspace in an
                  // empty block) or, from the keyboard's "delete line", the
                  // block alone with its children lifted into its place;
                  // `focusAbove` moves the caret to the end of the block
                  // shown above.
                  onDelete: (id, { keepChildren = false, focusAbove = false } = {}) => {
                    if (readOnly || (notesLocked && touchesLocked(blocks, id, "delete"))) return;
                    const focus = focusAbove ? (notesLocked ? hopTarget : visibleNeighbor)(blocks, id, -1, view)?.id : null;
                    let next = keepChildren ? removeBlockKeepChildren(blocks, id) : removeBlockTree(blocks, id);
                    if (focus && findBlock(next, focus)) {
                      startEditing(focus);
                      pendingFocusRef.current = { id: focus, caret: "end" };
                      setFocusedId(focus);
                    }
                    setBlocks(next); // the transition's delete op
                    undoableStatus(t("Block deleted."));
                  },
                  // ↑ / ↓ at the editor's first / last line: the editor
                  // moves to the block shown above / below, caret at its
                  // end / start. False when there is none (the key then
                  // stays with the editor).
                  onHop: (id, dir) => {
                    if (readOnly) return false;
                    // past the locked notes, which have no editor to open
                    const target = (notesLocked ? hopTarget : visibleNeighbor)(blocks, id, dir, view);
                    if (!target) return false;
                    pendingFocusRef.current = { id: target.id, caret: dir < 0 ? "end" : "start" };
                    startEditing(target.id);
                    setFocusedId(target.id);
                    return true;
                  },
                  // Move block up / down: one step among the siblings, the editor
                  // staying open with its caret (the transition's move op).
                  onMoveBlock: (id, dir) => {
                    if (readOnly || (notesLocked && touchesLocked(blocks, id, "move", dir))) return false;
                    const next = moveSibling(blocks, id, dir);
                    if (next === blocks) return false;
                    const editor = blockRefs.current[id]?.current;
                    keepEditorThrough(id, editor ? editor.selectionStart : "end");
                    setBlocks(next);
                    setFocusedId(id);
                    return true;
                  },
                  getTree: () => blocks,
                  keybindings,
                  // Attach a block to the next chat message (chip with its id).
                  onAddToChat: shareMode ? null : addBlockToChat,
                  // A handwriting block's "Transcribe with AI".
                  onTranscribe: shareMode ? null : (block) => askToTranscribe({ blocks: [block] }),
                  // Comments: a row's control opens (or closes) the block's
                  // thread popover from where it sits; the handle menu and
                  // block.comment open it ready to write. Every row reads
                  // its own open count (BlockTree takes the map apart).
                  onComment: commentsOn
                    ? (id, anchor) => openThread(id, anchor || null, anchor ? {} : { compose: true, toggle: false }) : null,
                  onCommentPreload: ThreadPopover.preload,
                  // The handle menu's Properties (and block.properties): the
                  // popover hangs under the row's handle; a viewer has it too.
                  onProperties: (id, anchor) => openProps(id, anchor || null),
                  onPropertiesPreload: BlockProperties.preload,
                  commentCounts: comments.countByBlock,
                  // the block whose popover is open: its control stays shown, expanded
                  commentOpenFor: commentsOn && threadPop ? threadPop.blockId : "",
                  // `above` puts the copy before the original (Duplicate
                  // block above). None on a page whose notes are locked: a
                  // copy leaves the PDF anchoring behind, so it is a note.
                  onDuplicate: notesLocked ? null : (id, { above = false } = {}) => {
                    if (readOnly) return;
                    const src = findBlock(blocks, id);
                    if (!src) return;
                    // Fresh ids all the way down, the PDF anchoring left
                    // with the original (blockModel's freshCopy).
                    const copy = freshCopy(src);
                    setBlocks(insertSibling(blocks, id, copy, !above));
                    setFocusedId(copy.id);
                    setStatus(t("Block duplicated."));
                  },
                  onMoveToPage: (id) => { if (!readOnly && !(notesLocked && touchesLocked(blocks, id, "delete"))) pickMovePage({ blockIds: [id] }); },
                  // An image / table / diagram (the object frame's menu):
                  // to a new block above / below its own, or to another
                  // page through the same picker as a block move.
                  onMoveObject: ({ sourceId, kind, idx, target }) => {
                    if (readOnly || notesLocked) return;
                    if (target.type === "page") { pickMovePage({ fragment: { sourceId, kind, idx } }); return; }
                    const found = objectAt(sourceId, kind, idx);
                    const next = found ? moveObjectInTree(blocks, { sourceId, obj: found.obj, target }) : null;
                    if (next) setBlocks(next);
                  },
                  // None on a page whose notes are locked: the blocks would be
                  // notes, so a paste stays text in the highlight's editor.
                  onPasteBlocks: notesLocked ? null : (id, nodes, { replace = false } = {}) => {
                    if (readOnly || !nodes?.length) return;
                    // each a new block, stamped with the moment it was made
                    const toBlock = (n) => makeNewBlock({ content: n.content || "", children: (n.children || []).map(toBlock) });
                    const created = nodes.map(toBlock);
                    const count = (list) => list.reduce((n, b) => n + 1 + count(b.children), 0);
                    // A paste into an empty line (`replace`: nothing else in
                    // the block, no children) takes the line's place, as a
                    // text paste would, and the caret goes to the end of the
                    // last pasted block. A highlight's or ink's block stays.
                    const host = findBlock(blocks, id);
                    const dropHost = replace && !!host && isPlainNote(host);
                    let last = created[created.length - 1];
                    while (last.children.length) last = last.children[last.children.length - 1];
                    // Siblings after the pasting block (its own editor stays
                    // open, so its content is never rewritten under it).
                    // Functional: the paste handler dispatched the text removal
                    // in this same tick, so `blocks` here is one update behind.
                    setBlocks((prev) => {
                      let out = prev;
                      for (let i = created.length - 1; i >= 0; i--) {
                        out = insertSibling(out, id, created[i], true);
                      }
                      return dropHost ? removeBlockTree(out, id) : out;
                    });
                    if (dropHost) {
                      startEditing(last.id);
                      pendingFocusRef.current = { id: last.id, caret: "end" };
                      setFocusedId(last.id);
                    }
                    const n = count(created);
                    setStatus(tn("Pasted {n} block.", "Pasted {n} blocks.", n));
                  },
                  onStatus: (msg) => setStatus(msg),
                  onBlockDragOver: (e, block) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = "move";
                    clearTimeout(dragLeaveTimer.current);
                    // Only a block's ⋮⋮ drag or an object frame's drag shows
                    // where it lands: a link or text selection dragged over
                    // the notes has no dragend to take the line away again.
                    const frag = _dragState.fragment;
                    if (!_dragState.draggingId && !frag) return;
                    const wrap = e.currentTarget.closest(".sortableBlockWrap");
                    const r = wrap ? wrap.getBoundingClientRect() : e.currentTarget.getBoundingClientRect();
                    const px = e.clientX;
                    const py = e.clientY;
                    if (frag) {
                      // An object: over the row's middle it goes INTO the
                      // block, at the gap nearest the pointer; near the top
                      // or bottom edge it becomes a block of its own.
                      const edge = Math.min(14, Math.max(6, r.height * 0.3));
                      const rendered = e.currentTarget.querySelector(".blockRendered");
                      if (rendered && py - r.top > edge && r.bottom - py > edge) {
                        const gap = dropGapAtPoint(rendered, block.content || "", py);
                        if (gap) {
                          const rr = rendered.getBoundingClientRect();
                          const dt = { targetId: block.id, inside: true, offset: gap.offset, rect: { top: gap.y, left: rr.left, width: rr.width } };
                          _dragState.dropTarget = dt;
                          setDropTarget(dt);
                          return;
                        }
                      }
                    }
                    const above = frag ? py < r.top + r.height / 2 : (py - r.top) <= 16;
                    const td = parseInt((wrap || e.currentTarget).getAttribute("data-depth") || "0", 10);
                    const nested = (px - r.left) > 62;
                    const dt = { targetId: block.id, above, depth: nested ? td + 1 : td, rect: { top: r.top, left: r.left, width: r.width, bottom: r.bottom } };
                    _dragState.dropTarget = dt;
                    setDropTarget(dt);
                  },
                  // Hides the line, deferred: a dragleave is followed by
                  // the next row's dragover within a frame when the pointer
                  // just crossed rows (and in some engines its relatedTarget
                  // is null, which would read as "left the notes"). The
                  // computed target stays — a drop right after a leave still
                  // lands where the line was; a drop elsewhere goes through
                  // the window's drop/dragend reset instead.
                  onBlockDragLeave: () => {
                    clearTimeout(dragLeaveTimer.current);
                    dragLeaveTimer.current = setTimeout(() => setDropTarget(null), 80);
                  },
                  onBlockDrop: (e, block) => {
                    e.preventDefault();
                    const dt = _dragState.dropTarget;
                    setDropTarget(null);
                    _dragState.dropTarget = null;
                    _dragState.draggingId = null;
                    const frag = _dragState.fragment;
                    _dragState.fragment = null;
                    const many = _dragState.draggingIds;
                    _dragState.draggingIds = null;
                    // A locked page's notes stay where its file has them.
                    if (!dt || readOnly || notesLocked) return;
                    // Where the indicator's depth puts a dropped block: under
                    // the target, beside it, or beside one of its ancestors.
                    const placement = (tree) => {
                      const ctx = findBlockContext(tree, dt.targetId);
                      if (!ctx) return null;
                      if (dt.depth === ctx.depth + 1) return { type: "child", id: dt.targetId };
                      if (dt.depth === ctx.depth) return { type: "sibling", id: dt.targetId, above: dt.above };
                      if (dt.depth < ctx.depth && ctx.ancestors[dt.depth]) return { type: "sibling", id: ctx.ancestors[dt.depth], above: dt.above };
                      return null;
                    };
                    if (frag) {
                      const found = objectAt(frag.blockId, frag.kind, frag.idx);
                      const target = dt.inside ? { type: "inside", id: dt.targetId, offset: dt.offset } : placement(blocks);
                      const next = found && target ? moveObjectInTree(blocks, { sourceId: frag.blockId, obj: found.obj, target }) : null;
                      if (next) setBlocks(next);
                      return;
                    }
                    // The selection's notes, dragged by one of their handles:
                    // taken out together, put back one after another.
                    if (many) {
                      if (many.some((id) => isDescendant(blocks, id, dt.targetId))) return;
                      const moved = many.map((id) => findBlock(blocks, id)).filter(Boolean);
                      const remaining = removeBlocks(blocks, many);
                      const where = placement(remaining);
                      if (where) setBlocks(insertBlocks(remaining, where, moved));
                      return;
                    }
                    const sourceId = e.dataTransfer.getData("text/plain");
                    if (!sourceId || sourceId === dt.targetId) return;
                    if (isDescendant(blocks, sourceId, dt.targetId)) return;
                    const extracted = extractBlock(blocks, sourceId);
                    if (!extracted) return;
                    const { extracted: sourceBlock, remaining } = extracted;
                    const where = placement(remaining);
                    if (!where) return;
                    const next = where.type === "child"
                      ? insertChild(remaining, where.id, sourceBlock, false)
                      : insertSibling(remaining, where.id, sourceBlock, !where.above);
                    if (next) setBlocks(next);
                  },
                };
                rowPropsRef.current = rowProps;
                return (
                  <>
                    {notesInkStrip}
                    {/* A block added, removed or moved here glides the rows
                        around it to their new places (docs/dev/ui-design.md,
                        "Lists that change"): not a page opening or reloading,
                        another client's or the agent's change, a text box's
                        measured size, nor while writing on a sheet. */}
                    <ListMotion shape={blocks} same={sameShape} scope={focusedBlockId}
                      still={!!treeOriginRef.current.get(blocks)} off={readOnly || inkUi.open}
                      hold=".slashMenu, .refPopup">
                      <FileChipContext.Provider value={fileChipCtx}>
                        <NoteSheetContext.Provider value={noteSheetCtx}>
                          <BlockTree blocks={blocks} readOnly={readOnly} rowProps={stableRowProps(rowProps)}
                            zoteroLock={focusedBlock?.properties?.zotero_readonly ? focusedBlockId : ""} notesLocked={notesLocked} />
                        </NoteSheetContext.Provider>
                      </FileChipContext.Provider>
                      {subPagesPanel}
                      {backlinksPanel}
                      {notesTail}
                    </ListMotion>
                    {notesReplayDock}
                    <BlockDropIndicator target={dropTarget} />
                  </>
                );
              })()
            ))}
          </div>

        </div>
  ) : null;

  // Slot the windows into dock columns / the bottom row (the notes in the
  // center are no window: centerNotes).
  // The docked Notes window while pages stand side by side
  // (docs/dev/tab_groups.md): the notes of the page on screen when it has
  // a viewer; while the page on screen has its notes in its own group (a
  // page without a PDF, the library), those of the last page with a viewer
  // that was on screen and is still open in a group, drawn as a view of
  // that page (PagePane `dock`). The window keeps its place, and the paper
  // its notes beside the page being written; a press on them makes the
  // paper's group live.
  if (focusedBlockId && !centerNotes) notesPaperRef.current = focusedBlockId;
  const notesPaper = (() => {
    if (isPhone || isSingle(tabLayout) || !centerNotes || !notesVisible) return null;
    // a paper another group shows: the last one on screen, else (after a
    // reload) the first group's that has a PDF
    const shown = groupsOf(tabLayout).map((g) => g.active).filter((id) => id && id !== focusedBlockId);
    const kept = notesPaperRef.current;
    return shown.includes(kept) ? kept : shown.find((id) => tabKinds.get(id) === "pdf") || null;
  })();
  const winVisible = {
    notes: (Boolean(notesWindow) && !centerNotes) || !!notesPaper,
    chat: !chatHidden && (!shareMode || !!focusedBlockId),
    comments: !commentsHidden && !!focusedBlockId && !homeMode && commentsOn,
  };
  // A window's frame: its title, the grip's drag and fold, and its close.
  // The window and its folded bar share it.
  function windowFrame(id) {
    // Phone: windows are full-screen overlays — no dock dragging or collapsing,
    // and closing just returns to the main view.
    const common = {
      onGrip: isPhone ? undefined : (e) => startWindowDock(e, id),
      onFold: isPhone ? undefined : () => {
        const collapsed = !collapsedWins[id];
        // Folded, the window keeps the size it has now for what it holds
        // (--fold-w, --fold-h; app.css), so nothing in it lays out again
        // while it waits, unseen, in its panel at no size. The focus leaves
        // it, as a press elsewhere takes it: a finger's taps on the grip
        // leave it in a note's editor, which then closes.
        const panel = collapsed ? document.querySelector(`[data-panel-id="${id}"]`) : null;
        const box = panel?.getBoundingClientRect();
        if (box?.width && box.height) {
          panel.style.setProperty("--fold-w", `${box.width}px`);
          panel.style.setProperty("--fold-h", `${box.height}px`);
        }
        if (panel?.contains(document.activeElement)) document.activeElement.blur();
        setCollapsedWins((prev) => ({ ...prev, [id]: collapsed }));
        guideEvents.emit("window.collapsed", { id, collapsed });
      },
      collapsed: isPhone ? false : !!collapsedWins[id],
    };
    // A window closed with its × (desktop): the guide's window-closed hint
    // says where it waits (the View menu).
    const closeWindow = (hide) => () => {
      if (isPhone) { setPhonePanel(null); return; }
      hide();
      guideEvents.emit("window.closed", { id });
    };
    if (id === "notes") return { title: t("Notes"), common, onClose: closeWindow(() => setNotesVisible(false)) };
    if (id === "chat") return { title: t("Chat"), common, onClose: closeWindow(() => setChatHidden(true)) };
    return { title: t("Comments"), common, onClose: closeWindow(() => setCommentsHidden(true)) };
  }
  function renderWindow(id) {
    const { title, common, onClose } = windowFrame(id);
    if (id === "notes") {
      return (
        <DockWindow title={title} guide="notes.grip" {...common} onClose={onClose}>
          {notesPaper ? (
            <PagePane key={notesPaper} dock {...paneProps(notesPaper)}
              onActivate={(target) => { const g = groupOf(tabLayoutRef.current, notesPaper); if (g) activateTabGroup(g.id, notesPaper, target); }} />
          ) : notesWindow}
        </DockWindow>
      );
    }
    if (id === "chat") {
      return (
        // Until the chat's module is in, its window frame with an empty body.
        <LazyBoundary fallback={(
          <DockWindow title={title} guide="chat.grip" {...common} onClose={onClose}>
            <div className="chatPanel chatWindow" />
          </DockWindow>
        )}>
        <ChatDock
          {...common}
          session={chatSession}
          readOnly={shareMode}
          canSave={canWriteWorkspace}
          onClose={onClose}
          doc={doc} focusedBlockId={focusedBlockId} homeBlocks={knownBlocks} pickerPages={pickerPages} libraryTree={libTree} pageTitle={pageTitle}
          openTabs={openTabs} recentViews={recentViews}
          onOpenPage={openPageLink}
          pdfSelections={pdfSelections} setPdfSelections={setPdfSelections}
          chatNotes={chatNotes} setChatNotes={setChatNotes} focusedNote={focusedNote} onSelectionSent={() => setNoteSel(null)}
          chatImages={chatImages} setChatImages={setChatImages} chatPictureBudget={chatPictures}
          onAttachView={!homeMode && isPdf(doc) && pdfUrl && !pdfHidden ? attachViewToChat : undefined}
          chatModel={chatSendModel} setChatModel={setChatModel}
          chatEffort={chatEffort} setChatEffort={setChatEffort}
          chatSpeed={chatSpeed} setChatSpeed={setChatSpeed}
          dictationModel={dictationModel} dictationLang={dictationLang}
          chatSystem={chatSystem} aiInfo={aiInfo} aiInfoFailed={aiInfoFailed} aiProvider={aiProvider}
          chatContextChars={chatContextChars} setChatContextChars={setChatContextChars} multiContextChars={multiContextChars}
          openAiKeysEditor={openAiKeysEditor} preloadSettings={SettingsDialog.preload}
          onSetupInfo={(info) => { chatKeysInfoRef.current = info; }}
          focusSignal={chatFocusSignal}
          openSettings={openSettingsPane}
          isAdmin={!!authUser?.is_admin}
          checkModelTools={(m) => testAiProvider({ id: m.provider }, m.model)} refreshAiModels={refreshAiModels}
          aiHealth={aiHealth} dismissAiHealth={() => setAiHealth(null)}
          openPopover={openPopover} setOpenPopover={setOpenPopover}
          setStatus={setStatus} askConfirm={setConfirmBox}
          organizeFolder={!focusedBlockId && !shareMode ? folderFilter : null}
          toolRounds={toolRounds} toolRoundsUnlimited={toolRoundsUnlimited} addTask={addTask} removeTask={tasks.removeLocal}
          agentReadChars={agentReadChars} agentPerms={agentPerms} setAgentPerms={setAgentPerms} agentSystem={agentSystem}
          agentEnabled={agentEnabled} setAgentEnabled={setAgentEnabled}
          fetchInBackground={fetchInBackground} delegateReads={delegateReads}
          paperSave={{ allowOa: oaFallback, saveCopy: pdfSaveLocal, fetchMetadata: metaAutoFetch }}
          askSignal={chatAsk}
          onLibraryChange={fetchHomeBlocks}
          onAgentEvent={(ev) => agentEventRef.current?.(ev)}
          onNotesChange={(pageIds) => {
            // The AI edited note blocks server-side. With the page live over
            // its socket they already arrived as ops; otherwise (no socket, or
            // only the op-log poll, which reads them up to 2 s later) refetch
            // the open page.
            if (!focusedBlockId || !pageIds.includes(focusedBlockId)) return;
            const me = collabRef.current.me;
            if (me.connected && me.via === "socket") return;
            loadBlocksForBlock(focusedBlockId);
          }}
        />
        </LazyBoundary>
      );
    }
    if (id === "comments") {
      return (
        // Until its module is in, the window's frame with an empty body.
        <LazyBoundary fallback={(
          <DockWindow title={title} guide="comments.grip" {...common} onClose={onClose}>
            <div className="commentsPanel" />
          </DockWindow>
        )}>
          <CommentsPanel
            {...common}
            onClose={onClose}
            threads={comments.threads}
            pageId={focusedBlockId}
            notify={canFollow && commentsOn}
            onStatus={setStatus}
            // what a thread is on: a highlight's quote, else the note's text, else the handwriting's page
            anchorText={(id) => anchorWords(treeById.get(id))}
            // on a work folder's file, for a member who may edit: the feedback view (feedback/)
            onFeedback={focusedFile && canWriteWorkspace ? () => openFeedback({ kind: "file", id: focusedBlockId }) : undefined}
            labelOf={(id) => { const rb = treeById.get(id) || refCache[id]; return rb ? refLabelOf(rb) : null; }}
            onOpen={(thread) => {
              if (isPhone) { setNotesVisible(true); setPhonePanel("notes"); }
              showThread(thread);
            }}
          />
        </LazyBoundary>
      );
    }
    return null;
  }
  // Windows per slot, in stored order, visibility-filtered. On a phone the
  // dock slots are empty — windows render as full-screen overlays instead.
  const slotWins = (side) => (isPhone ? [] : layout[side].filter((w) => winVisible[w]));
  function renderSlotGroup(side, direction) {
    const wins = slotWins(side);
    // A folded window shows as a bar OUTSIDE the panel group: panel sizes
    // are percentage-based, so a panel could never be exactly one header
    // tall. Bars keep their side of the open windows so folding doesn't
    // reorder the column. The window itself stays in its panel, mounted and
    // out of sight (DockWindow; .dockPanelFolded in app.css), so a fold
    // keeps all it holds. While another window of the group is open, a
    // folded one's panel is pinned at no size (its min, max and default 0),
    // and the group's sizes are those of the open windows, shared evenly
    // until a layout is saved; with all of them folded the panels keep
    // their sizes, unseen.
    const firstOpen = wins.findIndex((w) => !collapsedWins[w]);
    const bar = (w) => {
      const { title, common, onClose } = windowFrame(w);
      return (
        <div key={w} className="collapsedBar">
          <DockBar title={title} guide={w === "notes" ? "notes.grip" : w === "chat" ? "chat.grip" : "comments.grip"}
            onGrip={common.onGrip} onFold={common.onFold} onClose={onClose} />
        </div>
      );
    };
    const before = wins.filter((w, i) => collapsedWins[w] && (firstOpen === -1 || i < firstOpen));
    const after = wins.filter((w, i) => collapsedWins[w] && firstOpen !== -1 && i > firstOpen);
    // The group keeps a saved layout per set of folded windows, as it kept
    // one per set of open windows when a fold took a window out of it. A
    // panel's `order` names the set too, since the group reads its saved
    // layout as its panels register: a fold registers them again.
    const folds = wins.filter((w) => collapsedWins[w]);
    const foldSet = wins.reduce((n, w, i) => (collapsedWins[w] ? n | (1 << i) : n), 0);
    return (
      <div className={`slotStack slotStack-${direction}`}>
        {before.map(bar)}
        <div className="slotStackGroup">
          <PanelGroup direction={direction} autoSaveId={`gamma-slot-${side}${folds.length ? `:${folds.join("+")}` : ""}`}
            ref={(h) => { panelGroupRefs.current[`slot-${side}`] = h; }}>
            {wins.map((w, i) => {
              const folded = !!collapsedWins[w];
              const pinned = folded && firstOpen !== -1;
              // One splitter between two open windows, right after the first:
              // the group moves it past the pinned panels between them.
              const sash = i > 0 && !collapsedWins[wins[i - 1]] && wins.slice(i).some((x) => !collapsedWins[x]);
              return (
                <React.Fragment key={w}>
                  {i > 0 ? (
                    <PanelResizeHandle className={sash ? `sash sash-${direction}` : "sashFolded"} disabled={!sash} hitAreaMargins={SASH_MARGINS} />
                  ) : null}
                  <Panel id={w} order={foldSet * wins.length + i + 1} minSize={pinned ? 0 : 15} maxSize={pinned ? 0 : 100}
                    defaultSize={pinned ? 0 : undefined} className={folded ? "dockPanelFolded" : undefined}>{renderWindow(w)}</Panel>
                </React.Fragment>
              );
            })}
          </PanelGroup>
        </div>
        {after.map(bar)}
      </div>
    );
  }

  // The "≡" View menu's rows (shared/ui/Menus.jsx), shared by the editing
  // and read-only topbars; on a phone the More sheet lists them instead of a
  // View button. A window toggle carries a check while shown. Read-only
  // share views omit AI chat and the import actions. A phone's bottom tabs
  // already switch Notes and Chat, so there only the PDF toggle is a window.
  // Undo, Redo and the two palettes are their commands (app/appCommands.js),
  // keys and all: the way to them without a keyboard.
  const commandRow = (id) => <CommandMenuItem key={id} id={id} ctx={appCmdRef.current} bindings={keybindings} />;
  const viewMenuItems = (menuReadOnly) => {
    const pdfRow = !homeMode && (!!doc || docView.notebookAvailable);
    // A window's row: a toggle, checked while the window shows.
    const shown = (on) => ({ role: "menuitemcheckbox", "aria-checked": on, trailing: on ? <CheckIcon size={14} className="ctxMenuCheck" /> : null });
    const exportable = (focusedBlock && !homeMode) || (homeMode && libTree.folders.has(folderFilter));
    // Bringing a closed window back retires the guide's window-closed hint.
    const toggleWindow = (id, isShown, toggle) => () => {
      if (!isShown) guideEvents.emit("window.shown", { id });
      toggle();
    };
    return menuGroups(
      [
        (!isPhone || pdfRow || shellBar) && <div key="windows" className="popoverSection">{t("Windows")}</div>,
        pdfRow && (doc ? (
          <MenuItem key="pdf" icon={FileIcon} {...shown(!pdfHidden)} onClick={toggleWindow("pdf", !pdfHidden, () => setPdfHidden((v) => !v))}>{paneWord}</MenuItem>
        ) : (
          <MenuItem key="pdf" icon={NotebookIcon} {...shown(notebook)} onClick={() => setNotebookView(focusedBlockId, !notebook)}>{t("Notebook view")}</MenuItem>
        )),
        !homeMode && !isPhone && (
          <MenuItem key="notes" icon={FileTextIcon} {...shown(notesVisible)} onClick={toggleWindow("notes", notesVisible, () => setNotesVisible((v) => !v))}>{t("Notes")}</MenuItem>
        ),
        (!menuReadOnly || focusedBlockId) && !isPhone && (
          <MenuItem key="chat" icon={SparklesIcon} {...shown(!chatHidden)} onClick={toggleWindow("chat", !chatHidden, () => setChatHidden((v) => !v))}>{t("AI Chat")}</MenuItem>
        ),
        !homeMode && focusedBlockId && commentsOn && !isPhone && (
          <MenuItem key="comments" icon={MessageSquareIcon} {...shown(!commentsHidden)}
            onPointerEnter={CommentsPanel.preload}
            onClick={toggleWindow("comments", !commentsHidden, () => setCommentsHidden((v) => !v))}>{t("Comments")}</MenuItem>
        ),
        shellBar && (
          <MenuItem key="shellBar" icon={PanelTopIcon} {...shown(shellBar.shown)}
            onClick={() => shellBar.set(!shellBar.shown)}>{t("Server bar")}</MenuItem>
        ),
      ],
      focusedBlockId && !readOnly ? ["app.undo", "app.redo"].map(commandRow) : [],
      ["app.quickOpen", "app.commandPalette"].map(commandRow),
      [
        !menuReadOnly && (
          <MenuItem
            key="import"
            icon={ImportIcon}
            data-guide="view.import"
            onClick={() => { setOpenPopover(null); setImportOpen(true); }}
            title={t("Bring in notes and highlights: a Zotero library, Notability notes and GoodNotes notebooks, Notion pages, Markdown, a Logseq export, a Gamma export, or the annotations saved inside this PDF")}
          >{t("Import…")}</MenuItem>
        ),
      ],
      [
        exportable && (
          <MenuItem
            key="export"
            icon={ExportIcon}
            data-guide="page.export"
            onClick={() => {
              setOpenPopover(null);
              setExportFolder(homeMode ? folderFilter : null);
              setExportOpen(true);
            }}
            title={homeMode
              ? t("Download the “{folder}” folder — every page in it as Markdown, a Logseq graph, a Zotero library, or a Gamma export", { folder: folderPath(libTree, folderFilter) })
              : t("Download this page — the PDF with highlights and notes, Markdown, a Logseq graph, a Zotero library, or a Gamma export")}
          >{t("Export…")}</MenuItem>
        ),
      ],
      [
        !homeMode && pdfUrl && (
          <MenuItem key="download" icon={DownloadIcon} onClick={exportRawPdf}
            title={t("Download the PDF file exactly as stored — no highlights or notes")}>{t("Download PDF")}</MenuItem>
        ),
      ],
      [
        // A shared or published page someone else made: Report
        // (support/reportContent.js), to the people who run this server.
        shareMode && focusedBlockId && !homeMode && !(shareInfo?.owner && shareInfo.viewer === shareInfo.owner) && (
          <MenuItem key="report" icon={FlagIcon} data-report="page"
            title={t("Tell the people who run this server about this page")}
            onClick={() => {
              setOpenPopover(null);
              openContentReport({ kind: "page", pageId: focusedBlockId, title: pageTitle,
                link: publicPage ? window.location.href : "" });
            }}>{t("Report this page…")}</MenuItem>
        ),
      ],
    );
  };
  const renderOverflowMenu = (menuReadOnly) => (
    <PopoverAnchor name="menu">
      <button
        className={`iconBtn ${openPopover === "menu" ? "activeIcon" : ""}`}
        onClick={() => setOpenPopover((p) => (p === "menu" ? null : "menu"))}
        onPointerEnter={preloadViewMenu}
        onFocus={preloadViewMenu}
        title={t("View — windows, import, export")}
        data-guide="header.view"
        aria-label={t("View")}
        aria-haspopup="menu"
        aria-expanded={openPopover === "menu"}
      >
        <MenuIcon size={16} />
      </button>
      {openPopover === "menu" ? (
        <Popover as={MenuScope} className="menuPopover" data-guide="view.menu" aria-label={t("View")}>{viewMenuItems(menuReadOnly)}</Popover>
      ) : null}
    </PopoverAnchor>
  );

  // Where each of the topbar's items goes (app/topbarItems.js, Settings →
  // Appearance › Top bar), in the order the user gave them: on the bar, or
  // a row of the account menu — the phone's More sheet, where Background
  // tasks, the Inbox and the View menu always go (the bottom bar keeps Add,
  // Search, a page's Share and the sync pill). An item off the bar keeps
  // its popover anchor where its button would be (`.offBar`), so its row,
  // its shortcut or App opens the popover in the same place. A tour or a
  // hint on screen shows them all. While pages stand side by side, an item
  // shown whose place is the panes stands at the end of each pane's strip
  // instead (paneLiveBar and paneStandIns, below).
  const topbarLayout = TOPBAR.read(topbar);
  const paneSplit = !isPhone && !isSingle(tabLayout);
  const barPlaces = TOPBAR.place(topbarLayout, {
    phone: isPhone,
    split: paneSplit,
    guide: guide.running || !!guide.offer,
    open: openPopover,
    active: { tasks: !!tasks.badge, inbox: inbox.unread > 0, sync: true },
  });
  const barAt = Object.fromEntries(barPlaces.map((x) => [x.id, x.at]));
  // An item has a button, on the bar or on the panes' strips; else it is a
  // row of the account menu.
  const hasButton = (id) => barAt[id] !== "menu";
  const offBar = (id) => (hasButton(id) ? "" : "offBar");
  const folderShareable = homeMode && lib.organize && libTree.folders.has(folderFilter) && !labelFilter;
  // the open page's Share, or the open folder's; a folder's is a row on a phone
  const sharesPage = !!focusedBlockId && !homeMode;
  const shareHasButton = hasButton("share") && (sharesPage || !isPhone);
  const syncs = !!authUser?.user && !!syncKind;
  const phoneMainActive = phonePanel === null || (phonePanel === "notes" && centerNotes);
  // An item's row in the account menu while it is off the bar (the View
  // menu's rows are the menu's last group, below).
  const barMenuRow = {
    add: () => !hasButton("add") && (
      <MenuItem key="add" icon={PlusIcon} onClick={() => setOpenPopover("add")}
        title={t("Add — a new page, a PDF by URL, arXiv id or DOI, or uploaded files")}>{t("Add…")}</MenuItem>
    ),
    tasks: () => !hasButton("tasks") && (
      <MenuItem key="tasks" icon={ActivityIcon} onClick={() => setOpenPopover("downloads")}
        title={t("Background tasks — exports, backups, imports, downloads and uploads")}
        trailing={<TaskBadge badge={tasks.badge} inline />}>
        {t("Background tasks")}
      </MenuItem>
    ),
    search: () => !hasButton("search") && (
      <CommandMenuItem key="search" id="app.search" ctx={appCmdRef.current} bindings={keybindings}
        onClick={() => setOpenPopover("search")} />
    ),
    share: () => !shareHasButton && (sharesPage ? commandRow("app.share") : folderShareable && (
      <MenuItem key="share" icon={LinkIcon} onClick={() => openFolderShare(folderFilter)}>{t("Share this folder")}</MenuItem>
    )),
    sync: () => !hasButton("sync") && syncs && (
      <MenuItem key="sync" icon={CloudIcon} onClick={() => setOpenPopover("mirror")}>{t("Sync status")}</MenuItem>
    ),
    inbox: () => !hasButton("inbox") && inbox.enabled && (
      <MenuItem key="inbox" icon={BellIcon} onPointerEnter={InboxPanel.preload} onClick={() => setOpenPopover("inbox")}
        trailing={<InboxBadge unread={inbox.unread} inline />}>
        {t("Inbox")}
      </MenuItem>
    ),
  };
  const barMenuRows = () => {
    const rows = [
      ...barPlaces.map(({ id }) => barMenuRow[id]?.()),
      isPhone && sharesPage && commentsOn && (
        // the Comments window, as a panel over the page (no bottom tab of its own)
        <MenuItem key="comments" icon={MessageSquareIcon} onPointerEnter={CommentsPanel.preload}
          onClick={() => { setOpenPopover(null); setPhonePanel("comments"); }}
          trailing={comments.countByBlock.size ? <span className="ctxMenuKey">{shortCount([...comments.countByBlock.values()].reduce((a, n) => a + n, 0))}</span> : null}>
          {t("Comments")}
        </MenuItem>
      ),
    ].filter(Boolean);
    return (
      <>
        {rows.length ? <><MenuDivider />{rows}</> : null}
        {hasButton("view") ? null : <><MenuDivider />{viewMenuItems(false)}</>}
      </>
    );
  };

  // The topbar's items by id (app/topbarItems.js): each its button, or
  // only its popover's anchor while it is off the bar. The bar renders
  // them in the user's order, in the DOM rather than by CSS order, so Tab
  // goes through them as they are seen.
  const topbarItem = {
    add: () => (
      <span data-popover="add" className={`popoverAnchor ${offBar("add")}`}>
        {hasButton("add") ? (
          <button
            className={`iconBtn addBtn ${openPopover === "add" ? "activeIcon" : ""}`}
            onClick={() => setOpenPopover((p) => (p === "add" ? null : "add"))}
            data-guide="header.add"
            title={t("Add — a new page, a PDF by URL, arXiv id or DOI, or uploaded files")}
            aria-label={t("Add")}
          >
            <PlusIcon size={16} />
            <span className="barLabel">{t("Add")}</span>
          </button>
        ) : null}
        {openPopover === "add" ? (
          <Popover className="addPopover" data-guide="add.popover">
            <input
              autoFocus
              className="searchInput"
              data-guide="add.urlInput"
              value={addUrl}
              onChange={(e) => setAddUrl(e.target.value)}
              placeholder={t("Paste a URL, DOI or arXiv id")}
              onKeyDown={(e) => {
                if (composing(e)) return;
                if (e.key === "Enter" && addUrl.trim() && !loading) {
                  setOpenPopover(null);
                  // A share link from another Gamma (or this one) brings the
                  // page or folder it shares over; anything else is a paper to fetch.
                  const shareLink = parseGammaShareLink(addUrl);
                  if (shareLink) importSharedPage(shareLink);
                  else openPdf(cleanAddInput(addUrl));
                  setAddUrl("");
                }
              }}
            />
            {parseGammaShareLink(addUrl) ? (
              <div className="popoverHint">
                {t("A Gamma share link — Enter copies what it shares, a page or a whole folder, into your library.")}
              </div>
            ) : null}
            <MenuItem icon={UploadIcon} disabled={loading} data-guide="add.upload"
              onClick={() => pickFiles({ multiple: true }, (files) => { setOpenPopover(null); uploadFiles([...files]); })}>{t("Upload files…")}</MenuItem>
            <MenuItem icon={FolderIcon} disabled={loading}
              onClick={() => pickFiles({ directory: true }, (files) => { setOpenPopover(null); uploadFolder([...files]); })}
              title={t("Upload every file in a folder — subfolders become folders; hidden and temporary files stay out")}>{t("Upload folder…")}</MenuItem>
            <MenuItem icon={FilePlusIcon} data-guide="add.newPage" onClick={() => createPage()}>{t("New page")}</MenuItem>
            <MenuItem icon={NotebookIcon} data-guide="add.newNotebook" onClick={() => createNotebook()}
              title={t("A page of blank paper to write on, with pages added as you go")}>{t("New notebook")}</MenuItem>
          </Popover>
        ) : null}
      </span>
    ),
    tasks: () => (
      <span data-popover="downloads" className={`popoverAnchor ${offBar("tasks")}`}>
        {/* The spinner, a red dot for a failure worth a look (a refused
            download leaves no broken page behind, so this is the sign it
            happened; AI lookups are quiet) or an accent dot for a file
            waiting to be downloaded (tasks/TasksTray.jsx). */}
        {hasButton("tasks") ? (
          <TasksButton tasks={tasks} open={openPopover === "downloads"}
            onToggle={() => setOpenPopover((p) => (p === "downloads" ? null : "downloads"))} />
        ) : null}
        {openPopover === "downloads" ? <TasksPanel tasks={tasks} onOpen={openTask} onRetry={retryTask} /> : null}
      </span>
    ),
    search: () => (
      <SearchPanel
        open={openPopover === "search"}
        onOpenChange={(v) => setOpenPopover(v ? "search" : null)}
        button={hasButton("search")}
        detailsDefault={focusedBlockId ? searchDetailsPaper : searchDetailsHome}
        focusedBlockId={focusedBlockId}
        homeBlocks={homeBlocks}
        tree={libTree}
        openBlock={openBlock}
        openPageLink={openPageLink}
        pdfSearchRef={pdfSearchRef}
        wakeTasks={tasks.wake}
        indexed={indexed}
        scrollToRef={scrollToRef}
        cancelCoarseRestoreRef={cancelCoarseRestoreRef}
        setPdfHidden={setPdfHidden}
        docNonce={pdfDocNonce}
        onFindMarks={setFindMarks}
        initialQuery={searchSeed}
      />
    ),
    share: () => (sharesPage ? (
      <span data-popover="share" className={`popoverAnchor ${shareHasButton ? "" : "offBar"}`}>
        {shareHasButton ? (
          <button
            className={`iconBtn ${openPopover === "share" ? "activeIcon" : ""}`}
            onClick={() => { if (openPopover === "share") setOpenPopover(null); else openPageShare(focusedBlockId); }}
            onPointerEnter={SharePopover.preload}
            onFocus={SharePopover.preload}
            disabled={loading}
            data-guide="header.share"
            title={t("Share")}
            aria-label={t("Share")}
          >
            <LinkIcon size={16} />
            <span className="barLabel">{t("Share")}</span>
          </button>
        ) : null}
        {openPopover === "share" && shareTarget?.kind === "page" ? sharePopover : null}
      </span>
    ) : folderShareable ? (
      // The same button for the open folder: one link for every page filed
      // in it. On a phone the More sheet offers it instead.
      <span data-popover="share" className={`popoverAnchor ${shareHasButton ? "" : "offBar"}`}>
        {shareHasButton ? (
          <button
            className={`iconBtn ${openPopover === "share" ? "activeIcon" : ""}`}
            onClick={() => { if (openPopover === "share") setOpenPopover(null); else openFolderShare(folderFilter); }}
            onPointerEnter={SharePopover.preload}
            onFocus={SharePopover.preload}
            data-guide="header.share"
            title={t("Share this folder")}
            aria-label={t("Share this folder")}
          >
            <LinkIcon size={16} />
          </button>
        ) : null}
        {openPopover === "share" && shareTarget?.kind === "folder" ? sharePopover : null}
      </span>
    ) : null),
    sync: () => (syncs ? (
      <MirrorPopover
        key={`${syncKind}:${workspace.id}`}
        wsId={workspace.id}
        kind={syncKind}
        mirrorOf={syncKind === "zotero" ? zoteroLibraryLabel({ name: workspace.zotero_of }) : workspace.mirror_of}
        publication={syncKind === "mirror" && !workspace.mirror_of && !!workspace.publishing}
        pageId={focusedBlockId}
        everyPage={topbarLayout.modes.sync === "always"}
        button={hasButton("sync")}
        open={openPopover === "mirror"}
        onToggle={() => setOpenPopover(openPopover === "mirror" ? null : "mirror")}
        jumpTo={(pageId, blockId) => jumpToRef.current?.(pageId, blockId)}
        onOpenSettings={() => openSettingsPane(workspace.mirror_of || workspace.mirror_detached || syncKind === "zotero" ? "workspaces" : "account")}
        onOpenAccount={() => openSettingsPane("account")}
        onLinkMoved={(path) => {
          // detached or reattached here: the session row says so (zotero_of,
          // mirror_of, …), and the page's PDF card follows; the pill that
          // detached the link stays, for its Reattach
          if (path === "/detach") setPillHeld({ ws: workspace.id, kind: syncKind });
          checkSession();
        }}
      />
    ) : null),
    inbox: () => (inbox.enabled ? (
      // The Inbox: off the bar (always on a phone), a row of the account
      // menu opens the panel.
      <span data-popover="inbox" className={`popoverAnchor ${offBar("inbox")}`}>
        {hasButton("inbox") ? (
          <InboxButton unread={inbox.unread} open={openPopover === "inbox"} onPreload={InboxPanel.preload}
            onToggle={() => setOpenPopover((p) => (p === "inbox" ? null : "inbox"))} />
        ) : null}
        {openPopover === "inbox" ? (
          <LazyBoundary>
            <InboxPanel inbox={inbox} workspaceId={wsId} onOpen={openInboxItem} />
          </LazyBoundary>
        ) : null}
      </span>
    ) : null),
    view: () => (hasButton("view") ? renderOverflowMenu(false) : null),
  };

  // Account & settings is no item: it stays last, the menu the items off
  // the bar go to and the way back to their setting.
  const accountButton = authUser?.user && (
    <span data-popover="user" className="popoverAnchor">
      <button
        className={`iconBtn ${openPopover === "user" ? "activeIcon" : ""}`}
        onClick={() => {
          const opening = openPopover !== "user";
          if (opening) refreshQuota(); // a fresh storage ring on open
          setOpenPopover(opening ? "user" : null);
        }}
        onPointerEnter={preloadAccountMenu}
        onFocus={preloadAccountMenu}
        data-guide="header.account"
        // The name stays put (every script finds the button by it); the
        // hover title counts what wants a look.
        title={isPhone ? (inbox.unread ? tn("More — {n} unread in the inbox; account, settings, background tasks, import and export",
          "More — {n} unread in the inbox; account, settings, background tasks, import and export", inbox.unread)
          : inbox.enabled ? t("More — inbox, account, settings, background tasks, import and export")
            : t("More — account, settings, background tasks, import and export"))
          : notices.list.length ? tn("Account & settings — {n} notice", "Account & settings — {n} notices", notices.list.length)
          : t("Account & settings")}
        aria-label={isPhone ? t("More") : t("Account & settings")}
        aria-haspopup="menu"
        aria-expanded={openPopover === "user"}
      >
        {isPhone ? <MenuIcon size={20} /> : <UserIcon size={20} />}
        {isPhone ? <span className="barLabel">{t("More")}</span> : null}
        {/* On a phone: the running spinner, then the Inbox's count, then the notice dot. */}
        {isPhone && tasks.badge === "running" ? <span className="transferSpin" aria-hidden="true" />
          : isPhone && inbox.unread ? <InboxBadge unread={inbox.unread} />
          : notices.tone ? <span className={`noticeDot ${dotTone(notices.tone)}`} data-tone={notices.tone} aria-hidden="true" /> : null}
      </button>
      {openPopover === "user" ? (
        <Popover as={MenuScope} className="userPopover" data-guide="account.menu" aria-label={isPhone ? t("More") : t("Account & settings")}>
          {/* The card is the way into your account: Settings → Account & sync. */}
          <button type="button" role="menuitem" className="userCard" data-guide="account.card"
            onClick={() => openSettingsPane("account")} title={t("Account & sync")}>
            <span className="userAvatar" aria-hidden="true">
              {authUser.is_guest
                ? <UserIcon size={20} />
                : <span className="userAvatarInitial">{authUser.user.charAt(0).toUpperCase()}</span>}
            </span>
            <span className="userCardMeta">
              <span className="userCardName">{authUser.is_guest ? t("Guest") : authUser.user}</span>
              <span className="userCardRole" title={authUser.is_guest && authUser.guest_expires_at
                ? t("Deleted at {time}", { time: fmtDate(authUser.guest_expires_at, { dateStyle: "medium", timeStyle: "short" }) }) : undefined}>
                {authUser.is_guest ? guestExpiryLabel(authUser.guest_expires_at) : workspace ? `${workspace.name} · ${workspaceMeta(workspace)}`
                  : t("Signed in")}
              </span>
            </span>
            {/* The storage ring, its numbers on hover. */}
            {quotaInfo ? (
              <span className="userCardQuota">
                <StorageRing usedBytes={quotaInfo.used_bytes} quotaMb={quotaInfo.quota_mb} />
              </span>
            ) : null}
          </button>
          {authUser.is_guest ? (
            <div className="popoverHint">{t("Your work stays until then, or until you log out; then the workspace is deleted with everything in it. Ask the admin for an account to keep your work.")}</div>
          ) : null}
          {/* What the red dot is about: one row per notice, its sentence
              and a link to the Settings pane that resolves it. The link
              is the menu's item and the sentence its description: a
              status region around them would stand between the item
              and its menu. */}
          {notices.list.length ? (
            <div className="accountNotices" data-testid="account-notices">
              {notices.list.map((notice) => (
                <div key={notice.id} className={`accountNotice ${notice.tone}`}>
                  {notice.tone === "info" ? <InfoIcon size={14} /> : <AlertCircleIcon size={14} />}
                  <span className="accountNoticeBody">
                    <span className="accountNoticeText" id={`account-notice-${notice.id}`}>{noticeText(notice)}</span>
                    <button type="button" role="menuitem" className="accountNoticeAction"
                      aria-describedby={`account-notice-${notice.id}`}
                      onClick={() => openSettingsPane(notice.pane)}>
                      {noticeAction(notice)} →
                    </button>
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          {barMenuRows()}
          <MenuDivider />
          {/* The workspace switcher: every library this account belongs
              to; switching reloads the tab on that workspace's URL. The
              open one is the checked radio of its group. */}
          <div data-guide="account.workspaces" role="group" aria-label={t("Workspaces")}>
          {workspaces.length ? <div className="popoverSection">{t("Workspaces")}</div> : null}
          {workspaces.map((w) => (
            <MenuItem
              key={w.id}
              role="menuitemradio"
              aria-checked={w.id === wsId}
              className={`wsItem ${w.id === wsId ? "active" : ""}`}
              icon={() => <span className="wsItemBadge" aria-hidden="true">{(w.name || "?").charAt(0).toUpperCase()}</span>}
              onClick={() => { setOpenPopover(null); switchWorkspace(w.id); }}
              title={w.personal ? t("Your personal workspace{default}", { default: w.default ? t(" (default)") : "" }) : t("{Shared} workspace · {members} member{_s} · you {role}", { Shared: w.access === "public" ? t("Public") : t("Shared"), members: w.members, _s: w.members === 1 ? "" : "s", role: ROLE_LABEL[w.role] || w.role })}
              trailing={<>
                <span className="wsItemMeta">{workspaceMeta(w)}</span>
                {w.id === wsId ? <CheckIcon size={14} className="wsItemCheck" /> : null}
              </>}
            ><span className="wsItemName">{w.name}</span></MenuItem>
          ))}
          </div>
          {!authUser.is_guest ? (
            <MenuItem
              icon={UsersIcon}
              onClick={() => openSettingsPane("workspaces")}
              title={t("All your workspaces: rename, members, export and import, create another")}
            >{t("Workspaces…")}</MenuItem>
          ) : null}
          <MenuDivider />
          <MenuItem icon={SettingsIcon} onClick={() => openSettingsPane(notices.firstPane || "account")}
            trailing={notices.tone ? <span className={`noticeDot inline ${dotTone(notices.tone)}`} aria-hidden="true" /> : null}>
            {t("Settings…")}
          </MenuItem>
          <MenuDivider />
          {/* The tours that can start here (guide.startable); a tour
              whose first step needs no open popover closes this menu.
              The setup guides follow under their own heading. */}
          <SubMenuItem id="tours" icon={HelpCircleIcon} label={t("Tours")} data-guide="account.tour">
            {menuGroups(
              guide.startable().filter((tour) => !tour.setup).map((tour) => (
                <MenuItem key={tour.id} data-tour={tour.id}
                  onClick={() => guide.start(tour.id)}>{t(tour.title)}</MenuItem>
              )),
              guide.startable().filter((tour) => tour.setup).map((tour, i) => [
                i === 0 ? <MenuLabel key="setup-label">{t("Setup guides")}</MenuLabel> : null,
                <MenuItem key={tour.id} data-tour={tour.id}
                  onClick={() => guide.start(tour.id)}>{t(tour.title)}</MenuItem>,
              ]).flat(),
            )}
          </SubMenuItem>
          <MenuItem icon={BugIcon} onClick={() => { setOpenPopover(null); setReportOpen(true); }}
            title={t("Describe what went wrong; Gamma adds its build, your browser and its recent log lines and opens a GitHub issue for you to review")}>
            {t("Report a problem…")}
          </MenuItem>
          <MenuDivider />
          <MenuItem icon={LogOutIcon} danger onClick={authUser.is_guest ? confirmGuestLogout : doLogout}>{t("Log out")}</MenuItem>
        </Popover>
      ) : null}
    </span>
  );

  // The topbar action buttons: the items in their order, then Account &
  // settings. On a phone these move to the bottom bar, in the same order:
  // the tab row is too narrow to hold both, and thumbs reach the bottom.
  // There each carries its word under the icon (.barLabel, hidden elsewhere).
  const topbarActions = (
    <>
      {barPlaces.filter((x) => x.at !== "pane").map(({ id }) => <React.Fragment key={id}>{topbarItem[id]()}</React.Fragment>)}
      {accountButton}
    </>
  );
  // The topbar items placed in the panes while pages stand side by side, at
  // the end of each pane's strip (TabGroups). The live pane's bar is the
  // items themselves, popovers and all, drawn once (`liveBar`) and moved
  // with the live pane. Another pane's page is a view, so its buttons stand
  // for them: a press makes that pane live and, once its page has opened,
  // opens the item there (selectTab's `then`), as a press on the view edits
  // there. A pane with nothing open has no page to share.
  const paneItems = barPlaces.filter((x) => x.at === "pane");
  const wakePaneItem = (group, id) => {
    if (group.active) activateTabGroup(group.id, undefined, { then: id });
    else { activateTabGroup(group.id); setOpenPopover(TOPBAR.item(id).popover); }
  };
  const paneLiveBar = paneItems.length ? (
    <div className="topbar paneBar">
      {paneItems.map(({ id }) => <React.Fragment key={id}>{topbarItem[id]()}</React.Fragment>)}
    </div>
  ) : null;
  const paneStandIns = (group) => (paneItems.length ? (
    <div className="topbar paneBar">
      {paneItems.filter(({ id }) => (id !== "share" || group.active) && (id !== "inbox" || inbox.enabled)).map(({ id, item }) => {
        const Icon = TOPBAR_ICONS[id];
        return (
          <button key={id} type="button" className="iconBtn" data-bar-item={id} title={t(item.label)} aria-label={t(item.label)}
            onClick={() => wakePaneItem(group, id)}>
            <Icon size={16} />
          </button>
        );
      })}
    </div>
  ) : null);

  // The viewer's side strip, the PDF's and the notebook's (app/viewerToolItems.js,
  // Settings → Appearance › Viewer tools): its tools in the user's order,
  // each on the strip or a row of the ⋯ menu at its end. A tool that does
  // not apply here is in neither: Paper and Notes view are a notebook's,
  // Translate a PDF's outside a share, the selection mode a touch screen's,
  // the text highlighter a PDF's, and a read-only page has nothing to write
  // on, highlight or record onto. A lit tool is in use and stays on the
  // strip whatever its mode (the pen while its tools are open, Record while
  // it records, the highlighter while it is on, Translate while it
  // translates or shows a translation, Paper while its panel is open, the
  // rectangle mode); a tour or a hint on screen shows them all, since their
  // steps point at them.
  const transLit = pdfTransState.running || (pdfTransState.pages > 0 && pdfTransState.shown);
  // Translate's click, its name for that click and its glyph: stop a running
  // job; on a translated page show the original or the translation (all
  // pages); anywhere else translate this page, which also shows the rest.
  // Its ⋯ row opens its menu instead.
  const translateClick = () => {
    if (pdfTransState.running) pdfTranslateCtl.current?.halt();
    else if (pdfTransState.current) pdfTranslateCtl.current?.setShown(!pdfTransState.shown);
    else pdfTranslateCtl.current?.translatePage();
  };
  const translateAction = pdfTransState.running ? t("Stop translating")
    : pdfTransState.current ? (pdfTransState.shown ? t("Show original") : t("Show translation"))
    : t("Translate");
  const TranslateGlyph = pdfTransState.pages > 0 && !pdfTransState.shown ? LanguagesOffIcon : LanguagesIcon;
  const translateProgress = Math.round(pdfTransState.progress * 100);
  // A PDF highlight's menu rows (its right-click or held finger, and the
  // Highlight flyout of a translated paragraph's menu, where its rects take no
  // pointer): its style (a passage's; a style keeps the menu open for a
  // colour), colour and presets, link, reference point, place, comment,
  // properties and Delete. `close` closes the menu they are in; `nested`,
  // they are a flyout's (MarkMenuRows).
  const highlightRows = (id, close, nested = false) => {
    const h = highlights.find((x) => x.id === id);
    return (
      <>
        <MarkMenuRows own={{ style: h?.style || "highlight", color: h?.color }} line={!!h && !h.position?.area && !h.linkTarget}
          nested={nested} onClose={close}
          onStyle={(style) => changeHighlightMark(id, { style })}
          onColor={(c) => changeHighlightMark(id, { color: highlightColor(c) })}
          onPreset={(p) => changeHighlightMark(id, { color: highlightColor(p.color), style: p.style })}
          onSave={readOnly ? null : () => textHighlighter.saveLook({ style: h?.style || "highlight", color: h?.color })} />
        <button
          role="menuitem"
          className="ctxMenuItem"
          onClick={() => {
            setLinkDialog({ position: null, quote: h?.quote || "", editBlockId: id, url: h?.linkTarget?.url || "" });
            close();
          }}
        >
          {h?.linkTarget ? t("Change link…") : t("Link to page…")}
        </button>
        {h?.linkTarget?.url ? (
          <button
            role="menuitem"
            className="ctxMenuItem"
            onClick={() => {
              setLinkPrompt(h.linkTarget.url);
              close();
            }}
          >
            {t("Open link in browser…")}
          </button>
        ) : null}
        <button
          role="menuitem"
          className="ctxMenuItem"
          onClick={() => {
            setRefPoint({
              pageId: focusedBlockId,
              pageTitle: pageTitle || t("Untitled"),
              blockId: id,
              quote: (h?.quote || "").slice(0, 200),
            });
            // Also a paste-able deep link: opening it jumps straight to
            // this highlight (in the browser, chat notes, anywhere).
            if (findBlock(blocks, id)) {
              copyText(blockLinkUrl(id));
            }
            close();
            setStatus(t("Reference point copied — paste the link, or pick it in another paper's link dialog."));
          }}
        >
          {t("Copy as reference point")}
        </button>
        {/* a highlight drawn dashed (matched roughly, or an area placed by
            estimate), pending: accepted where it is, it is drawn solid */}
        {!readOnly && placedToCheck(h?.position) ? (
          <button
            role="menuitem"
            className="ctxMenuItem"
            onClick={() => { acceptHighlightPlace(id); close(); }}
          >
            {t("Accept")}
          </button>
        ) : null}
        {!readOnly ? (
          <button
            role="menuitem"
            className="ctxMenuItem"
            onClick={() => { startHighlightMove(id); close(); }}
          >
            {t("Move to a selection…")}
          </button>
        ) : null}
        {commentsOn ? (
          <button
            role="menuitem"
            className="ctxMenuItem"
            onClick={() => {
              // the popover hangs from the highlight on the page
              close();
              openThread(id, highlightEl(id), { compose: true, toggle: false, pdf: true });
            }}
          >
            {t("Comment")}
          </button>
        ) : null}
        <button
          role="menuitem"
          className="ctxMenuItem"
          onPointerEnter={BlockProperties.preload}
          onClick={() => {
            // the popover hangs from the highlight on the page
            close();
            openProps(id, highlightEl(id));
          }}
        >
          {t("Properties")}
        </button>
        <button
          role="menuitem"
          className="ctxMenuItem"
          onClick={() => {
            deleteHighlight(id);
            close();
          }}
        >
          {t("Delete")}
        </button>
      </>
    );
  };
  // The translate button's menu, from its right-click or held finger and
  // from the ⋯ row of a hidden button (docs/dev/translation.md "The
  // button"): what to translate, the translation shown or hidden, the
  // language and the settings, then Forget, apart and asked first. The
  // catalog's rows (pdf.*) grey out with the reason they do not apply.
  const translateRows = (close) => {
    const waiting = t("Wait for the translation under way to finish");
    const cmdRow = (id, over) => (
      <CommandMenuItem key={id} id={id} ctx={appCmdRef.current} bindings={keybindings} onRun={close} {...over} />
    );
    const { running, shown, pages } = pdfTransState;
    const forget = () => {
      close();
      const url = pdfTransState.url, file = pdfTransFile, pageId = scopeOf(url, pdfTransScope).pageId;
      setConfirmBox({
        title: T("Forget this paper's translations?"),
        message: t("Its translations in every language and model, and its brief, are forgotten for everyone in this workspace. Showing it translated again asks the model or service anew."),
        confirmLabel: t("Forget"),
        danger: true,
        onConfirm: () => forgetTranslation(url, file, pageId, setStatus, translateRecall).then(() => setRecallRead((n) => n + 1)),
      });
    };
    return menuGroups(
      [
        running ? (
          <MenuItem key="stop" icon={StopIcon} onClick={() => { close(); pdfTranslateCtl.current?.halt(); }}>{t("Stop translating")}</MenuItem>
        ) : null,
        cmdRow("pdf.translatePage", { disabledTitle: running ? waiting : t("This page is translated") }),
        cmdRow("pdf.translateDocument", { disabledTitle: running ? waiting : t("Every page is translated") }),
        !running && pdfTransState.refsLeft > 0 ? (
          <MenuItem key="refs" icon={QuoteIcon} onClick={() => { close(); pdfTranslateCtl.current?.translateReferences(); }}>
            {t("Translate references too")}
          </MenuItem>
        ) : null,
      ],
      [
        pages > 0 ? cmdRow("pdf.toggleTranslation", {
          label: shown ? t("Show original") : t("Show translation"), icon: shown ? EyeOffIcon : EyeIcon,
        }) : null,
        <SubMenuItem key="lang" id="translateLang" icon={GlobeIcon} label={t("Translate into")}>
          {TRANSLATE_LANGS.map(([code, name]) => (
            <MenuItem key={code} role="menuitemradio" aria-checked={code === translateLang}
              trailing={code === translateLang ? <CheckIcon size={14} /> : null}
              onClick={() => { close(); setTranslateLang(code); }}>
              {name}
            </MenuItem>
          ))}
        </SubMenuItem>,
        <MenuItem key="settings" icon={SettingsIcon} onClick={() => { close(); openSettingsPane("translation"); }}>
          {t("Translation settings…")}
        </MenuItem>,
      ],
      // A stored PDF's translations are kept on the server under the page's
      // file, for the whole workspace: forgetting them there and here makes
      // the next click translate afresh. For a writer of the workspace.
      [
        !running && pdfTransState.translated && pdfTransFile && !readOnly ? (
          <MenuItem key="forget" icon={Trash2Icon} danger onClick={forget}>{t("Forget this paper's translations…")}</MenuItem>
        ) : null,
      ],
    );
  };
  // The strip's tools: those the pane brings (its descriptor's `tools`,
  // documents/viewers.js), each under App's own condition.
  const paneTool = (id) => !!docView.spec?.tools.includes(id);
  const stripApplies = {
    zoom: paneTool("zoom"),
    ink: paneTool("ink") && !readOnly,
    record: paneTool("record") && !readOnly,
    highlighter: paneTool("highlighter") && !readOnly && !!pdfUrl,
    translate: paneTool("translate") && !shareMode && !!pdfUrl,
    paper: paneTool("paper") && notebook && !readOnly,
    notesView: paneTool("notesView") && notebook,
    selectMode: paneTool("selectMode") && touchScreen() && !shareMode && (!!pdfUrl || !!docView.spec?.regions),
  };
  const stripPlaces = VIEWER_TOOLS.place(viewerTools, {
    guide: guide.running || !!guide.offer,
    pinned: { ink: inkUi.open, record: !!recordingNow, highlighter: textHighlighter.on, translate: transLit, paper: paperMenu, selectMode: areaSelectMode },
  }).filter((x) => stripApplies[x.id]);
  const stripMenuTools = stripPlaces.filter((x) => x.at === "menu");
  const toolsMore = docView.shown && stripMenuTools.length > 0;
  // Each tool's button on the strip, by id: zoom is its three buttons,
  // kept together (the tour points at them).
  const viewerTool = {
    zoom: () => (
      <div className="pdfCtlGroup" data-guide="viewer.zoom">
        <button onClick={() => zoomStep(-1)} title={t("Zoom out")} aria-label={t("Zoom out")}>
          <ZoomOutIcon size={16} />
        </button>
        <button onClick={() => zoomStep(1)} title={t("Zoom in")} aria-label={t("Zoom in")}>
          <ZoomInIcon size={16} />
        </button>
        <button className="pdfFitWidthBtn" onClick={() => zoomTo("page-width")} title={t("Fit to width")} aria-label={t("Fit to width")}>
          <FitWidthIcon size={16} />
        </button>
      </div>
    ),
    ink: () => (
      <button
        className={inkUi.open ? "modeActive" : ""}
        data-guide="pdf.inkButton"
        onClick={openInkTools}
        title={inkUi.open ? t("Close the markup tools (Esc)") : t("Handwriting: draw on the page with a pen, highlighter or eraser")}
        aria-label={t("Handwriting tools")}
      >
        <PenIcon size={16} />
      </button>
    ),
    record: () => (
      <button
        className={recordingNow ? "modeActive recordBtn" : ""}
        data-guide="viewer.record"
        onClick={toggleRecording}
        aria-pressed={!!recordingNow}
        title={recordingNow ? t("Stop recording") : t("Record audio: what you write meanwhile replays in step with it")}
        aria-label={recordingNow ? t("Stop recording") : t("Record audio")}
      >
        <MicIcon size={16} />
      </button>
    ),
    // A button per highlighter on the strip (pdf/HighlighterStrip.jsx); the
    // armed one alone while the item is hidden and pinned.
    highlighter: () => (
      <HighlighterStrip hl={textHighlighter} menuAt={stripMenuAt}
        only={VIEWER_TOOLS.read(viewerTools).modes.highlighter === "hidden" && !guide.running && !guide.offer} />
    ),
    // While a job runs, the bar under the glyph fills with its progress, in
    // the place the highlighter's colour takes, so the strip never moves; the
    // click stops the job, and the stop glyph says so (app.css .pdfTransBtn).
    translate: () => (
      <button
        data-guide="viewer.translate"
        className={"pdfTransBtn" + (transLit ? " modeActive" : "") + (pdfTransState.running ? " running" : "")}
        onClick={translateClick}
        {...menuPress((e) => { e.preventDefault(); openTransMenu(e.currentTarget); })}
        title={pdfTransState.running
          ? t("Translating… {progress}% — click to stop (right-click for options)", { progress: translateProgress })
          : pdfTransState.current
            ? (pdfTransState.shown
                ? (commandKeyLabel("pdf.peekOriginal")
                  ? t("Show the original (all pages; {key} peeks) — right-click for options", { key: commandKeyLabel("pdf.peekOriginal") })
                  : t("Show the original (all pages) — right-click for options"))
                : t("Show the translation — right-click for options"))
            : t("Translate this page into {translateLangLabel} — right-click: whole document & options", { translateLangLabel })}
        aria-label={translateAction}
      >
        <TranslateGlyph size={16} className="pdfTransGlyph" />
        {pdfTransState.running ? <>
          <StopIcon size={14} className="pdfTransStop" />
          <span className="pdfTransBar" aria-hidden="true" data-progress={translateProgress}>
            <span style={{ width: `${translateProgress}%` }} />
          </span>
        </> : null}
      </button>
    ),
    paper: () => (
      <button
        data-guide="viewer.paper"
        className={paperMenu ? "modeActive" : ""}
        onClick={() => setPaperMenu((v) => !v)}
        title={t("Paper: size, pattern and colour of this page")}
        aria-label={t("Paper of this page")}
        aria-expanded={paperMenu}
      >
        <SheetIcon size={16} />
      </button>
    ),
    notesView: () => (
      <button
        data-guide="viewer.notesView"
        onClick={() => setNotebookView(focusedBlockId, false)}
        title={t("Notes view: the pages among the notes")}
        aria-label={t("Notes view")}
      >
        <FileTextIcon size={16} />
      </button>
    ),
    selectMode: () => (
      <button
        data-guide="viewer.selectMode"
        className={areaSelectMode ? "modeActive" : ""}
        onClick={() => setAreaSelectMode((v) => !v)}
        // on a picture a finger pans unless it draws: the toggle says so
        title={docView.spec?.regions
          ? (areaSelectMode ? t("Region mode — a finger draws a region (tap to pan instead)") : t("Draw a region with a finger"))
          : areaSelectMode ? t("Rectangle mode — drag draws an area note (tap to switch to text selection)") : t("Text mode — drag selects text (tap to switch to rectangle drawing)")}
        aria-label={docView.spec?.regions ? t("Draw a region with a finger") : t("Toggle selection mode")}
        aria-pressed={docView.spec?.regions ? areaSelectMode : undefined}
      >
        <IconSwap swap={areaSelectMode} from={<TextCursorIcon size={16} />} to={<RectSelectIcon size={16} />} />
      </button>
    ),
  };
  // A tool's rows in the ⋯ menu: what its button does, under its word.
  const stripRow = (run) => () => { setToolsMenu(null); run(); };
  const viewerToolRows = {
    zoom: () => [
      <MenuItem key="zoomOut" icon={ZoomOutIcon} onClick={stripRow(() => zoomStep(-1))}>{t("Zoom out")}</MenuItem>,
      <MenuItem key="zoomIn" icon={ZoomInIcon} onClick={stripRow(() => zoomStep(1))}>{t("Zoom in")}</MenuItem>,
      <MenuItem key="fit" icon={FitWidthIcon} onClick={stripRow(() => zoomTo("page-width"))}>{t("Fit to width")}</MenuItem>,
    ],
    ink: () => <MenuItem key="ink" icon={PenIcon} onClick={stripRow(openInkTools)}>{t("Handwriting tools")}</MenuItem>,
    record: () => (
      <MenuItem key="record" icon={MicIcon} onClick={stripRow(toggleRecording)}>
        {recordingNow ? t("Stop recording") : t("Record audio")}
      </MenuItem>
    ),
    highlighter: () => <HighlighterRows key="highlighter" hl={textHighlighter} close={stripRow} />,
    // A hidden translate button's row opens its menu beside the row, as a
    // flyout would; that menu holds a flyout of its own (the language).
    translate: () => (
      <MenuItem key="translate" icon={TranslateGlyph} aria-haspopup="menu"
        trailing={<ChevronRightIcon size={14} className="ctxSubChev" />}
        onClick={(e) => { const at = stripMenuAt(e.currentTarget); setToolsMenu(null); setTransMenu(at); }}>
        {t("Translate")}
      </MenuItem>
    ),
    paper: () => <MenuItem key="paper" icon={SheetIcon} onClick={stripRow(() => setPaperMenu(true))}>{t("Paper of this page")}</MenuItem>,
    notesView: () => (
      <MenuItem key="notesView" icon={FileTextIcon} onClick={stripRow(() => setNotebookView(focusedBlockId, false))}>{t("Notes view")}</MenuItem>
    ),
    selectMode: () => (
      <MenuItem key="selectMode" icon={areaSelectMode ? RectSelectIcon : TextCursorIcon}
        onClick={stripRow(() => setAreaSelectMode((v) => !v))}>{t("Toggle selection mode")}</MenuItem>
    ),
  };
  // A right-click or a held finger on a bar's own background offers its
  // Customize… row (Settings → Appearance at the bar's section). `pane` is
  // the Settings target; `space(el)` says whether the press landed on the
  // bar itself. A control's own menu answers first, and what a portal of
  // the bar's shows (a popover's menu) is not the bar. The bar is no
  // [data-press]: that would keep the text of the popovers it holds from
  // being selected by a finger.
  // On the top bar (and a pane's strip) the press may be on an item's
  // button, whose place the menu then offers to change (barItemAt; a held
  // finger's press names the bar, so the item is the one at its point).
  const customizePress = (pane, space = () => true) => {
    const { "data-press": _, ...press } = menuPress((e) => {
      e.preventDefault();
      const at = pane === "topbar" ? document.elementFromPoint(e.clientX, e.clientY) || e.target : null;
      setBarMenu({ pane, x: e.clientX, y: e.clientY, item: at ? barItemAt(at) : null });
    });
    const mine = (e) => !e.defaultPrevented && e.currentTarget.contains(e.target) && space(e.target);
    return {
      onContextMenu: (e) => { if (mine(e)) press.onContextMenu(e); },
      onPointerDown: (e) => { if (mine(e)) press.onPointerDown(e); },
    };
  };
  // The topbar item a press is on: a pane's stand-in button, or the button
  // of an item's anchor (named by its popover), never its open popover.
  const barItemAt = (el) => {
    if (el.closest(".popover")) return null;
    const standIn = el.closest("[data-bar-item]");
    if (standIn) return standIn.dataset.barItem;
    const anchor = el.closest("[data-popover]");
    return (anchor && TOPBAR_ITEMS.find((x) => x.popover === anchor.dataset.popover)?.id) || null;
  };
  const topbarSpace = (el) => !!barItemAt(el) || !el.closest("button, a, input, [role='tab'], [data-popover]");
  // The strip's ⋯ opens its menu on the press, so the strip leaves that
  // press alone: a held finger there would open both menus. (The translate
  // button's own menuPress answers before the strip's.)
  const stripSpace = (el) => !el.closest("[aria-haspopup]");

  return (
    <GammaNavContext.Provider value={gammaNav}>
    <PageToolsContext.Provider value={pageTools}>
    <ThreadPlaceContext.Provider value={placeOf}>
    <RecordingContext.Provider value={recordingCtx}>
    <PaletteContext.Provider value={palette}>
    <MarkToolsContext.Provider value={textHighlighter.markTools}>
    <MentionContext.Provider value={mentionPeople}>
    <MentionClock.Provider value={reminders.now}>
    <div
      ref={appRef}
      className={`app ${pseudoFullscreen ? "pseudoFullscreen" : ""} ${isPhone ? "phoneUI" : ""}`}
      data-drop={homeMode && lib.organize ? "upload" : focusedBlockId && !readOnly ? "files" : undefined}
      onDragOver={shareMode ? undefined : (e) => {
        if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
        e.preventDefault();
        // The overlay only where a drop does something: the library imports
        // files, an editable page takes files as blocks; block rows take
        // files themselves, and a page or a sheet takes pictures
        // (markup/PictureLayer.jsx).
        const dropHere = ((homeMode && lib.organize) || (focusedBlockId && !readOnly)) && !e.target.closest(".blockRowWrap")
          && !e.target.closest("[data-read-view]") && !picturesDrag(e);
        appRef.current?.classList.toggle("dragOver", dropHere);
      }}
      onDragLeave={shareMode ? undefined : (e) => {
        if (e.currentTarget === e.target) appRef.current?.classList.remove("dragOver");
      }}
      onDrop={shareMode ? undefined : (e) => {
        appRef.current?.classList.remove("dragOver");
        if (!e.dataTransfer) return;
        // A tab group's view of another page is not the page files go to.
        if (e.target.closest?.("[data-read-view]")) { e.preventDefault(); return; }
        // Grab entries synchronously — the DataTransfer is neutered once the
        // handler returns. Folders only arrive via the entry API.
        const entries = Array.from(e.dataTransfer.items || [])
          .map((it) => it.webkitGetAsEntry?.())
          .filter(Boolean);
        if (entries.some((en) => en.isDirectory)) {
          e.preventDefault();
          collectEntryFiles(entries).then(uploadFolder);
          return;
        }
        const dropped = Array.from(e.dataTransfer.files || []);
        if (!dropped.length) return;
        if (!homeMode) {
          // Dropped on the open page (outside a row): every file becomes a
          // block at the end of the page, PDFs included — the page's
          // document is attached from the header, never by a drop.
          if (!focusedBlockId || readOnly) return;
          e.preventDefault();
          appendFileBlocks(dropped);
          return;
        }
        // The library takes every file the server stores as a new page: a
        // PDF, a Markdown note, a document of any other format.
        const files = dropped.filter((file) => routeFile(file));
        if (!files.length) return;
        e.preventDefault();
        uploadFiles(files);
      }}
    >
      {!shareMode ? (
        <>
          {/* On a phone the bottom bar's Library tab is home, and the bar
              shows only while it has tabs (or Back) to hold. */}
          <div className={`topbar ${phoneBareTop ? "phoneBare" : ""}`} {...customizePress("topbar", topbarSpace)}>
            {isPhone ? null : (
              <button
                className={`iconBtn homeBtn ${homeMode ? "activeIcon" : ""} ${folderDragOver === "library" ? "dragOver" : ""}`}
                onClick={goHome}
                data-guide="header.home"
                title={t("Home")}
                aria-label={t("Home")}
                {...libraryDropProps}
              >
                <HomeIcon size={16} />
              </button>
            )}
            {navStackLen > 0 ? (
              <button
                className="iconBtn navBackBtn"
                data-guide="header.back"
                onClick={goBackNav}
                {...menuPress((e) => { e.preventDefault(); setNavStack([]); })}
                title={t("Back to where you were{steps} — Alt+← · right-click to clear", { steps: navStackLen > 1 ? ` (${navStackLen} steps)` : "" })}
                aria-label={t("Back")}
              >
                <ArrowLeftIcon size={16} />
                <span className="navBackBadge"><span key={Math.min(navStackLen, 30)} className="countIn">{Math.min(navStackLen, 30)}</span></span>
              </button>
            ) : null}
            {/* With one group the strip is here, as always; split into
                groups, each group's frame carries its own (TabGroups). */}
            {isPhone || isSingle(tabLayout) ? (
              <OpenTabs
                groupId={activeGroup(tabLayout)?.id}
                tabs={isPhone ? openTabs : groupTabs(activeGroup(tabLayout))}
                activeId={tabPick || focusedBlockId}
                still={tabsReadRef.current.has(openTabs)}
                kindOf={tabKindOf}
                menuOpen={openPopover === "tabs"}
                onMenuOpenChange={(open) => setOpenPopover(open ? "tabs" : null)}
                onReorder={(dragged, target) => reorderGroupTabs(activeGroup(tabLayout)?.id, dragged, target)}
                onOpen={selectTab}
                onClose={closeTab}
                onTogglePin={toggleTabPinned}
                onContext={(tab, x, y) => { setTabMenu({ id: tab.id, pinned: !!tab.pinned, x, y }); guideEvents.emit("tab.menu"); }}
              />
            ) : <div className="tabStrip" aria-hidden="true" />}
            {isPhone ? null : topbarActions}
          </div>
          {statusBarVisible ? <div className="status">{status}</div> : null}
        </>
      ) : (
        <div className="topbar">
          {sharedFolder ? (
            <button
              className="iconBtn homeBtn" disabled={!focusedBlockId}
              title={t("Back to the shared folder")} aria-label={t("Back to the shared folder")}
              onClick={() => goSharedPage("")}
            >
              <HomeIcon size={16} />
            </button>
          ) : publicPage ? (
            // a page host has no front door of its own
            <BrandMark className="shareBrand" size={24} />
          ) : (
            <a className="shareBrand" href="/" title={t("Gamma")} aria-label={t("Gamma")}><BrandMark size={24} /></a>
          )}
          {sharedFolder ? (
            // A folder share: the folder path from the shared folder down,
            // each crumb returning to that folder's listing, then the page.
            <span className="readOnlyTitle shareCrumbs">
              {folderCrumbs(folderFilter).map(({ id, name, sep }) => (
                <span key={id}>
                  {sep ? <span className="crumbSep">/</span> : null}
                  {!focusedBlockId && id === folderFilter ? <span>{name}</span> : (
                    <button className="crumbBtn" title={t("Back to {folder}", { folder: folderPath(libTree, id) })}
                      onClick={() => goSharedPage("", { folder: id })}>{name}</button>
                  )}
                </span>
              ))}
              {focusedBlockId ? <><span className="crumbSep">/</span><span>{pageTitle}</span></> : null}
            </span>
          ) : (
            <span className="readOnlyTitle">{pageTitle}</span>
          )}
          {shareInfo ? <ShareAccessPill info={shareInfo} folder={!!sharedFolder && !focusedBlockId} /> : null}
          {shareInfo?.canEdit && linkName ? (renamingLink ? (
            <input
              className="linkNameInput"
              autoFocus
              defaultValue={linkName}
              maxLength={LINK_NAME_MAX}
              aria-label={t("Your name on this page")}
              onKeyDown={(e) => {
                if (composing(e)) return;
                if (e.key === "Enter") commitLinkName(e.currentTarget.value);
                else if (e.key === "Escape") setRenamingLink(false);
              }}
              onBlur={(e) => commitLinkName(e.currentTarget.value)}
            />
          ) : (
            <button
              type="button"
              className="uiTag linkNameTag"
              title={t("How others on this page see you — click to change")}
              onClick={() => setRenamingLink(true)}
            >{t("as {name}", { name: linkName })}</button>
          )) : null}
          {shareInfo?.owner && shareInfo.viewer === shareInfo.owner ? (
            // The owner landed on their own link: the page (or folder) is theirs already.
            <button
              className="uiBtn sm"
              title={focusedBlockId
                ? t("This is your page — open it in your library instead of the shared view")
                : t("This is your folder — open it in your library instead of the shared view")}
              onClick={() => {
                // Out of the share view (no token) into the share's workspace,
                // named by ws=: a page deep link finds its workspace on its
                // own (chooseWorkspace), a folder's has no such lookup.
                const ws = shareInfo.workspace ? `&ws=${encodeURIComponent(shareInfo.workspace)}` : "";
                window.location.href = focusedBlockId
                  ? `${window.location.pathname}?page=${encodeURIComponent(focusedBlockId)}${ws}`
                  : `${window.location.pathname}?folder=${encodeURIComponent(sharedFolder?.id || "")}${ws}`;
              }}
            >{t("Open in my library")}</button>
          ) : shareInfo?.viewer && !shareInfo.viewerIsGuest && !publicPage && (focusedBlockId || sharedFolder) ? (
            <button
              className="uiBtn sm"
              disabled={loading}
              title={focusedBlockId
                ? t("Copy this page — blocks, highlights, its PDF and files — into your own library")
                : t("Copy this folder — its pages, subfolders, PDFs and files — into your own library")}
              onClick={addShareToLibrary}
            >{t("Add to my library")}</button>
          ) : shareInfo && !shareInfo.viewer && !publicPage ? (
            // no account behind this visitor: the share's own sign-in gate
            <button className="uiBtn sm primary" onClick={() => { setLoginError(""); setShareGate("login"); }}>{t("Sign in")}</button>
          ) : null}
          {renderOverflowMenu(true)}
        </div>
      )}

      {missingPage ? (
        <div className="missingPageNotice" role="status">
          <AlertCircleIcon size={16} aria-hidden="true" />
          <span className="missingPageText">
            {missingPage.trashed ? (
              <><b>{t("“{title}” is in Recently deleted.", { title: missingPage.trashed.title })}</b>{" "}
                {missingPage.trashed.restorable === false ? t("Comes back when its file is tracked again.")
                  : lib.organize ? t("Restore it to open it again.") : t("Someone who can edit this workspace can restore it.")}</>
            ) : (
              <><b>{t("That page isn't here.")}</b> {t("It may have been deleted, or it lives in another workspace.")}</>
            )}
          </span>
          {missingPage.trashed && missingPage.trashed.restorable !== false && lib.organize ? (
            <button type="button" className="uiBtn sm primary" onClick={restoreMissingPage}>{t("Restore")}</button>
          ) : (
            <button type="button" className="uiBtn sm"
              onClick={() => { setMissingPage(null); setOpenPopover(null); setQuickOpen({ prefix: "" }); }}>
              {t("Search the library")}
            </button>
          )}
          <button type="button" className="uiBtn sm" onClick={() => setMissingPage(null)}>{t("Dismiss")}</button>
        </div>
      ) : null}
      {pageGone && focusedBlockId ? (
        <div className="missingPageNotice pageTrashedNotice" role="status">
          <AlertCircleIcon size={16} aria-hidden="true" />
          <span className="missingPageText">
            <b>{t("This page was moved to Recently deleted.")}</b>{" "}
            {pageGone.trashed?.restorable === false ? t("Comes back when its file is tracked again.") : <>
              {collab.hasPending() ? t("Your unsaved edits are kept here and saved if it is restored.") : null}{" "}
              {lib.organize ? null : t("Someone who can edit this workspace can restore it.")}
            </>}
          </span>
          {lib.organize && pageGone.trashed?.restorable !== false ? (
            <button type="button" className="uiBtn sm primary" onClick={restoreOpenPage}>{t("Restore")}</button>
          ) : null}
        </div>
      ) : null}
      {/* the open page's note its vault could not write, or its opened PDF's file gone */}
      {focusedBlockId && !pageGone && vault.banner ? (
        <VaultBanner banner={vault.banner}
          onLocate={IS_DESKTOP && lib.organize ? () => askShell("relocate-file", { page: focusedBlockId }) : null}
          onRemove={lib.organize && openRules.trash ? deleteOpenPage : null}
          onDismiss={vault.dismissBanner} />
      ) : null}
      {attachModeBlockId && (
        <div className="attachModeBanner">
          {t("Click a PDF highlight to link it")}
          <button onClick={() => setAttachModeBlockId(null)}>{t("Cancel")}</button>
        </div>
      )}
      {moveHighlightId && (
        <div className="attachModeBanner" data-move-highlight={moveHighlightId}>
          {t("Select the text on the PDF where this highlight belongs")}
          <button onClick={() => setMoveHighlightId(null)}>{t("Cancel")}</button>
        </div>
      )}
      {transMenu && (
        <ContextMenu x={transMenu.x} y={transMenu.y} onClose={() => setTransMenu(null)} aria-label={t("Translate")}>
          {translateRows(() => setTransMenu(null))}
        </ContextMenu>
      )}
      {/* A strip highlighter's menu: its look, name and place in the list. */}
      <HighlighterMenu hl={textHighlighter} />
      {/* The strip's ⋯: the tools off it, then the way to their setting. */}
      {toolsMenu && toolsMore ? (
        <ContextMenu x={toolsMenu.x} y={toolsMenu.y} onClose={() => setToolsMenu(null)} ignoreRef={toolsMoreRef}
          aria-label={t("More tools")}>
          {stripMenuTools.map(({ id }) => viewerToolRows[id]())}
          <MenuDivider />
          <MenuItem icon={SettingsIcon} onClick={() => { setToolsMenu(null); openSettingsPane("viewer-tools"); }}>
            {t("Customize…")}
          </MenuItem>
        </ContextMenu>
      ) : null}
      {barMenu ? (
        // The bar's menu: on an item that offers places, where it stands
        // while pages are side by side; on the top bar or a pane's strip,
        // the split itself; then Customize….
        <ContextMenu x={barMenu.x} y={barMenu.y} onClose={() => setBarMenu(null)}>
          {TOPBAR.item(barMenu.item)?.places ? (
            <>
              {BAR_PLACES.map((where) => {
                const on = topbarLayout.places[barMenu.item] === where;
                return (
                  <MenuItem key={where} role="menuitemradio" aria-checked={on} title={t(TOPBAR.placeTip(where))}
                    trailing={on ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
                    onClick={() => { setBarMenu(null); setTopbar(TOPBAR.setPlace(topbar, barMenu.item, where)); }}>
                    {where === "top" ? t("Show on the top bar") : t("Show in each pane")}
                  </MenuItem>
                );
              })}
              <MenuDivider />
            </>
          ) : null}
          {barMenu.pane === "topbar" && !isPhone && !shareMode && (paneSplit || focusedBlockId) ? (
            <>
              {paneSplit ? (
                <MenuItem icon={OnePaneIcon} title={t("Every tab back in one group")}
                  onClick={() => { setBarMenu(null); joinTabGroups(); }}>{t("Join tab groups")}</MenuItem>
              ) : (
                <MenuItem icon={SplitColumnsIcon} title={t("Show this page in a group of its own, to the right")}
                  onClick={() => { setBarMenu(null); splitTab(focusedBlockId, "right"); }}>{t("Split right")}</MenuItem>
              )}
              <MenuDivider />
            </>
          ) : null}
          <MenuItem icon={SettingsIcon} onClick={() => { setBarMenu(null); openSettingsPane(barMenu.pane); }}>
            {barMenu.pane === "topbar" ? t("Customize top bar…") : t("Customize viewer tools…")}
          </MenuItem>
        </ContextMenu>
      ) : null}

      <div className="workArea">
      <PanelGroup direction="horizontal" autoSaveId="gamma-work-h" ref={(h) => { panelGroupRefs.current["work-h"] = h; }}>
      {slotWins("left").length ? (
        <>
          <Panel id="slot-left" order={1} defaultSize={26} minSize={15} className="dockSlot">
            {renderSlotGroup("left", "vertical")}
          </Panel>
          <PanelResizeHandle className="sash sash-horizontal" hitAreaMargins={SASH_MARGINS} />
        </>
      ) : null}
      <Panel id="slot-center" order={2} minSize={30} className="dockSlot">
      <PanelGroup direction="vertical" autoSaveId="gamma-work-v" ref={(h) => { panelGroupRefs.current["work-v"] = h; }}>
      <Panel id="slot-main" order={1} minSize={20} className="dockSlot">
      <TabGroups layout={tabLayout} liveId={shownGroupId} single={isPhone} tabsOf={groupTabs} activeTabId={tabPick || focusedBlockId}
        liveBar={paneLiveBar}
        onActivate={activateTabGroup} onMoveTab={moveTabToGroup} onSplit={splitTabGroup} onResize={resizeTabSplit}
        sashMargins={SASH_MARGINS}
        strip={{
          still: tabsReadRef.current.has(openTabs), kindOf: tabKindOf,
          menuOpenFor: typeof openPopover === "string" && openPopover.startsWith("tabs:") ? openPopover.slice(5) : null,
          onMenuOpenChange: (groupId) => setOpenPopover(groupId ? `tabs:${groupId}` : null),
          onReorder: reorderGroupTabs, onClose: closeTab, onTogglePin: toggleTabPinned,
          onContext: (tab, x, y) => { setTabMenu({ id: tab.id, pinned: !!tab.pinned, x, y }); guideEvents.emit("tab.menu"); },
          actions: paneStandIns, headPress: () => customizePress("topbar", topbarSpace),
        }}
        renderPane={(group) => (group.active ? (
          <PagePane key={group.active} {...paneProps(group.active)}
            onActivate={(target) => activateTabGroup(group.id, undefined, target)} />
        ) : (
          <div className="pagePaneEmpty">
            <p>{t("Nothing is open here. Drag a tab in, or open a page from the library.")}</p>
            <button type="button" className="uiBtn sm" onClick={() => activateTabGroup(group.id)}>{t("Open a page here")}</button>
            <button type="button" className="uiBtn sm" onClick={() => closeTabGroup(group.id)}>{t("Close this group")}</button>
          </div>
        ))}
        live={(
      <div className={`main ${centerNotes ? "pdfHidden" : ""}`}>
        {pillShown ? (
          <div
            className={"statusPill" + (pillShown.fading ? " fading" : "") + (pillShown.error ? " error" : "") + (pillShown.action ? " interactive" : "")}
            role="status"
          >
            {pillShown.spinner ? <span className="pillSpin" aria-hidden="true" /> : null}
            {/* A new message morphs out of the one it replaces. An error
                wraps to be read in full, which a morph (one line while it
                moves) would cut, so it shows at once. */}
            <span className="pillText">{pillShown.error ? pillShown.msg : <MorphText>{pillShown.msg}</MorphText>}</span>
            {pillShown.action ? (
              <button type="button" className="pillRetryBtn" onClick={pillShown.action.run}>{pillShown.action.label}</button>
            ) : null}
          </div>
        ) : null}
        <div className={`viewerWrap ${centerNotes ? "pdfHidden" : ""}${inkZoom && pdfUrl ? " inkZoomOpen" : ""}`} ref={viewerWrapRef} data-guide="pdf.pane"
          style={inkZoom && pdfUrl ? { "--ink-zoom-h": `${appPrefs.inkZoomHeight}px` } : undefined}>
          {docView.shown ? (
            <button
              className="uiClose uiCloseLg pdfCloseBtn"
              onClick={() => {
                if (notebook) { setNotebookView(focusedBlockId, false); return; }
                setPdfHidden(true);
                guideEvents.emit("window.closed", { id: "pdf" });
              }}
              title={t(docView.spec.close)}
              aria-label={t(docView.spec.close)}
            ><XIcon size={16} /></button>
          ) : null}
          {docView.shown && (stripPlaces.some((x) => x.at === "bar") || toolsMore) ? (
            <div className="pdfCtlBox pdfZoomOverlay" data-guide="viewer.tools" {...customizePress("viewer-tools", stripSpace)}>
              {/* The tools on the strip in their order (viewerTool), then
                  the ⋯ while some that apply here are off it. */}
              {stripPlaces.map(({ id, at }) => (at === "bar" ? <React.Fragment key={id}>{viewerTool[id]()}</React.Fragment> : null))}
              {toolsMore ? (
                <button
                  ref={toolsMoreAt}
                  className={toolsMenu ? "modeActive" : ""}
                  {...triggerPress((e) => { const el = e.currentTarget; setToolsMenu((cur) => (cur ? null : stripMenuAt(el))); })}
                  title={t("More tools")}
                  aria-label={t("More tools")}
                  aria-haspopup="menu"
                  aria-expanded={!!toolsMenu}
                >
                  <MoreIcon size={16} />
                </button>
              ) : null}
            </div>
          ) : null}
          {notebook && paperMenu && !readOnly ? (() => {
            const sheet = nbSheets.find((x) => x.id === nbCurrent) || nbSheets.at(-1);
            if (!sheet) return null;
            return (
              <PaperMenu paper={sheet.paper} number={sheet.index + 1}
                onChange={(paper) => setSheetPaper(sheet.id, paper)}
                onApplyAll={() => applyPaperToAll(sheet.paper)}
                onClose={() => setPaperMenu(false)} />
            );
          })() : null}
          {viewerInk && inkUi.open && !readOnly ? markupToolbar : null}
          {viewerInk ? <RecordingPill /> : null}
          {viewerInk ? playerBar : null}
          {docView.shown ? (
            <div className="pdfCtlBox pdfFullscreenBox" data-guide="viewer.fullscreen">
              <button
                onPointerDown={fullscreenPointerDown}
                onPointerUp={fullscreenPointerUp}
                onPointerCancel={() => { fullscreenTapRef.current.start = null; }}
                onClick={fullscreenClick}
                title={isFullscreen || pseudoFullscreen ? t("Exit full screen") : t("Full screen")}
                aria-label={isFullscreen || pseudoFullscreen ? t("Exit full screen") : t("Full screen")}
              >
                {isFullscreen || pseudoFullscreen ? (
                  <MinimizeIcon size={16} />
                ) : (
                  <MaximizeIcon size={16} />
                )}
              </button>
            </div>
          ) : null}
          {/* The viewer the page's view names (documents/DocumentPane.jsx): the PDF
              and the notebook rendered here, any other pane from the surface, keyed
              by the page so it opens at the page's own place. */}
          <DocumentPane key={ownPane ? focusedBlockId : "host"} view={docView} surface={viewerSurface} render={{ pdf: () => (
            <PdfViewer url={pdfUrl} highlights={highlights} anchors={anchors}
              citation={pageTarget?.kind === "citation" && pageTarget.pageId === focusedBlockId ? pageTarget : null}
              hideEmbeddedAnnots={embAnnots === "hide"}
              smoothWheel={smoothWheel}
              onPageInView={onPageInView}
              zoomHeight={appPrefs.inkZoomHeight} onZoomHeight={appPrefs.setInkZoomHeight} onZoomChange={changeInkZoom}
              darkPage={pdfDarkPage}
              translateKey={paperKey}
              translateScope={pdfTransScope}
              translateParallel={translateParallel}
              onTranslate={shareMode ? undefined : translateChunk}
              onTranslateSelection={shareMode ? undefined : translateSelection}
              onTranslateBrief={shareMode ? undefined : translateBrief}
              onTranslateLookup={shareMode ? undefined : translateLookup}
              translateRecall={translateRecall}
              // only a translation the workspace keeps comes back on a reload
              translateKeeps={translateKeep && !readOnly}
              // Add to glossary in the selection's translation: the
              // workspace's terms, which only its writers change
              onAddTerm={shareMode || readOnly ? null : addTerm}
              selTranslate={selTranslate ? (selTranslateAuto ? "auto" : "button") : ""}
              translateLangLabel={translateLangLabel}
              translateCtlRef={pdfTranslateCtl}
              onTranslateState={setPdfTransState}
              onParagraphContext={setParaMenu}
              areaMode={areaSelectMode && !shareMode}
              areaKey={commandChord("pdf.areaDrag", keybindings)}
              peekKey={commandChord("pdf.peekOriginal", keybindings)}
              marks={marks}
              flashHighlightId={flashingId}
              pdfScaleValue={pdfScale} scrollRef={scrollToRef}
              searchRef={pdfSearchRef}
              captureRef={pdfCaptureRef}
              findMarks={findMarks}
              onEffectiveScale={setPdfEffScale}
              onZoomTo={zoomTo}
              onBeforeLinkJump={() => pushNav()}
              onLoadState={handlePdfLoadState}
              retryRef={pdfRetryRef}
              onExternalLink={handleDocLink}
              onLinkContext={setLinkPrompt}
              onLinkHighlight={(h) => {
                if (h.linkTarget?.pageId) {
                  if (h.linkTarget.blockId) pendingJumpRef.current = h.linkTarget.blockId;
                  openBlock(h.linkTarget.pageId, { pushNav: true });
                } else if (h.linkTarget?.url) handleDocLink(h.linkTarget.url);
              }}
              onJump={jumpToHighlightId}
              onHighlightJump={highlightPressed}
              onHighlightContext={setHighlightMenu}
              // a note badge's click on a highlight with threads: its popover, from the badge
              onHighlightThread={commentsOn ? (hlId, anchor) => openThread(hlId, anchor, { toggle: false, pdf: true }) : undefined}
              // its threads as cards beside the pages (comments/marginSlot.jsx)
              commentMargin={commentMargin}
              marginRef={pdfMarginRef}
              onMarginLost={marginLost}
              onPenSelection={() => penSelectionRef.current?.()}
              onAreaSelection={(sel) => addChatPicture(regionPicture("area", focusedBlockId, sel.pageNumber,
                regionBox({ boundingRect: sel.rect, width: sel.width, height: sel.height })))}
              // the selection popup's Comment: a highlight with its thread popover open on it
              selComment={commentsOn && canComment}
              highlighter={textHighlighter.on ? textHighlighter.tint : ""}
              moveMode={!!moveHighlightId}
              // a dashed or hollow highlight's review card (pdf/HighlightReview.jsx): its actions are an
              // editor's; a lost one's picture is of the file it was measured on, when not this one
              onAcceptHighlight={readOnly ? undefined : acceptHighlightPlace}
              onPlaceHighlight={readOnly ? undefined : placeClosestMatch}
              onMoveHighlight={readOnly ? undefined : startReviewMove}
              onDiscardHighlight={readOnly ? undefined : discardReviewHighlight}
              discardOf={readOnly ? undefined : discardOf}
              onUndoReview={readOnly ? undefined : undoReviewStep}
              reviewPosition={reviewPosition}
              reviewNote={reviewNote}
              // a card that went to a highlight: the notes scroll to its block and mark it, as a click on it does
              onReviewWaiting={setReviewWaiting}
              onReviewFollow={(hlId) => { if (findBlock(blocks, hlId)) { scrollToBlock(hlId); reveal(hlId); } }}
              fileId={fileHash(doc)}
              pageId={focusedBlockId || ""}
              reviewRef={pdfReviewRef}
              onSelectionFinished={readOnly ? undefined : (position, quote, hideTip, extras) => {
                if (moveHighlightIdRef.current) {
                  // a highlight waiting to be moved takes this selection's place
                  moveHighlightTo(moveHighlightIdRef.current, position, quote);
                  hideTip?.();
                  return;
                }
                if (extras?.link) {
                  setLinkDialog({ position, quote });
                  hideTip?.();
                  return;
                }
                // The armed highlighter's look, or the popup's pick: a swatch
                // (a palette colour, stored at its alpha) in the style picked
                // with it, or a highlighter's. Nothing here changes a
                // highlighter. A comment's passage is the wash, which marks
                // it commented.
                const armed = extras?.highlighter ? textHighlighter.armedPreset : null;
                const color = armed ? highlightColor(armed.color) : highlightColor(extras?.color) || DEFAULT_HIGHLIGHT;
                const style = extras?.comment ? "highlight" : armed ? armed.style : extras?.style || "highlight";
                const id = addHighlight({ quote, note: extras?.commentText || "", position, color, style }, { edit: !extras?.comment });
                hideTip?.();
                if (extras?.comment && id) commentOnNewHighlight(id);
                return id;
              }}
              // the bar over a highlight the highlighter just made: what its menu does to
              // that one highlight (a swatch its colour, its style kept; a highlighter its look)
              onHighlightAction={readOnly ? undefined : (id, action, value) => {
                if (action === "mark") changeHighlightMark(id, { color: highlightColor(value.color), style: value.style });
                else if (action === "style") changeHighlightMark(id, { style: value });
                else if (action === "save") textHighlighter.saveLook(value);
                else if (action === "remove") deleteHighlight(id);
                else if (action === "comment") {
                  openThread(id, highlightEl(id), { compose: true, toggle: false, pdf: true });
                } else if (action === "link") {
                  setLinkDialog({ position: null, quote: highlights.find((x) => x.id === id)?.quote || "", editBlockId: id });
                }
              }}
            />
          ), notebook: () => (
            <NotebookViewer key={focusedBlockId} place={tabScrollRef.current[focusedBlockId]?.place}
              sheets={nbSheets} marks={marks} scaleValue={pdfScale} onEffectiveScale={setPdfEffScale}
              onZoomTo={zoomTo} readOnly={readOnly} onAddSheet={readOnly ? undefined : addPageAtEnd}
              onCurrentSheet={setNbCurrent} scrollRef={nbScrollRef} />
          ) }} fallback={<div className="status">{t("No PDF open.")}</div>} />
        </div>


        {centerNotes ? notesWindow : null}
      </div>
        )} />
      </Panel>
      {slotWins("bottom").length ? (
        <>
          <PanelResizeHandle className="sash sash-vertical" hitAreaMargins={SASH_MARGINS} />
          <Panel id="slot-bottom" order={2} defaultSize={32} minSize={12} className="dockSlot">
            {renderSlotGroup("bottom", "horizontal")}
          </Panel>
        </>
      ) : null}
      </PanelGroup>
      </Panel>
      {slotWins("right").length ? (
        <>
          <PanelResizeHandle className="sash sash-horizontal" hitAreaMargins={SASH_MARGINS} />
          <Panel id="slot-right" order={3} defaultSize={28} minSize={15} className="dockSlot">
            {renderSlotGroup("right", "vertical")}
          </Panel>
        </>
      ) : null}
      </PanelGroup>
      {isPhone && winVisible.notes && (phonePanel === "notes" || phoneSeen.current.notes) ? (
        <div className={`phonePanel ${phonePanel === "notes" ? "" : "phonePanelHidden"}`}>{renderWindow("notes")}</div>
      ) : null}
      {isPhone && focusedBlockId && !homeMode && commentsOn && (phonePanel === "comments" || phoneSeen.current.comments) ? (
        <div className={`phonePanel ${phonePanel === "comments" ? "" : "phonePanelHidden"}`}>{renderWindow("comments")}</div>
      ) : null}
      {isPhone && (!shareMode || focusedBlockId) && (phonePanel === "chat" || phoneSeen.current.chat) ? (
        <div className={`phonePanel ${phonePanel === "chat" ? "" : "phonePanelHidden"}`}>{renderWindow("chat")}</div>
      ) : null}
      </div>
      {isPhone ? (
        // Phone: one bottom bar — view tabs on the left, the topbar's action
        // buttons on the right, each an icon over its word. The Library tab
        // is home (the topbar has no Home button here), so it stays on a
        // page's bar beside the page's own PDF / Notes / Chat.
        <div className="phoneBottomBar">
          <div className={`phoneTabBar ${shareMode ? "" : "hasActions"}`}>
            {!shareMode ? (
              <button
                className={`phoneTab ${homeMode && phoneMainActive ? "active" : ""}`}
                // Over the library, a covering panel just closes; otherwise
                // this is the Home button: back to the library's root.
                onClick={() => { if (!homeMode || phoneMainActive) goHome(); setPhonePanel(null); }}
                data-guide="header.home"
                title={t("Library")}
                aria-label={t("Library")}
              >
                <HomeIcon size={16} />
                <span>{t("Library")}</span>
              </button>
            ) : null}
            {hasSheets ? (
              // A page with sheets: the bar switches between its two views.
              [[true, NotebookIcon, t("Notebook")], [false, FileTextIcon, t("Notes")]].map(([on, Icon, label]) => (
                <button key={label} className={`phoneTab ${notebook === on && !phonePanel ? "active" : ""}`}
                  onClick={() => setNotebookView(focusedBlockId, on)} title={label} aria-label={label}>
                  <Icon size={16} />
                  <span>{label}</span>
                </button>
              ))
            ) : shareMode || !homeMode ? (
              <button
                className={`phoneTab ${phoneMainActive ? "active" : ""}`}
                onClick={() => setPhonePanel(null)}
                title={homeMode ? t("Library") : centerNotes ? t("Notes") : paneWord}
                aria-label={homeMode ? t("Library") : centerNotes ? t("Notes") : paneWord}
              >
                {homeMode ? <HomeIcon size={16} /> : centerNotes ? <FileTextIcon size={16} /> : notebook ? <NotebookIcon size={16} /> : <FileIcon size={16} />}
                <span>{homeMode ? t("Library") : centerNotes ? t("Notes") : paneWord}</span>
              </button>
            ) : null}
            {!centerNotes && !hasSheets ? (
              <button
                className={`phoneTab ${phonePanel === "notes" ? "active" : ""}`}
                onClick={() => { setNotesVisible(true); setPhonePanel((p) => (p === "notes" ? null : "notes")); }}
                title={t("Notes")}
                aria-label={t("Notes")}
              >
                <FileTextIcon size={16} />
                <span>{t("Notes")}</span>
              </button>
            ) : null}
            {(!shareMode || focusedBlockId) ? (
              <button
                className={`phoneTab ${phonePanel === "chat" ? "active" : ""}`}
                onClick={() => setPhonePanel((p) => (p === "chat" ? null : "chat"))}
                title={t("AI chat")}
                aria-label={t("AI chat")}
              >
                <SparklesIcon size={16} />
                <span>{t("Chat")}</span>
              </button>
            ) : null}
          </div>
          {/* keeps the .topbar class so every ".topbar X" style still applies */}
          {!shareMode ? <div className="topbar phoneActions">{topbarActions}</div> : null}
        </div>
      ) : null}
      {dockPreview ? (
        <div className="dockPreview" style={dockPreview} />
      ) : null}
      {importOpen ? (
        <LazyBoundary>
        <ImportDialog
          file={importOpen?.file || null}
          hasPdf={!!docId && !!focusedBlockId}
          strip={embAnnots === "strip"}
          busy={loading}
          onCancel={() => setImportOpen(false)}
          onImport={runImport}
        />
        </LazyBoundary>
      ) : null}
      {importReview ? <LazyBoundary><ImportReviewDialog {...importReview} folderPath={(id) => folderPath(libTree, id)} tasks={tasks}
        onClose={() => setImportReview(null)} /></LazyBoundary> : null}
      {trashOpen ? (
        <LazyBoundary>
        <RecentlyDeleted onClose={() => setTrashOpen(false)} confirm={setConfirmBox} setStatus={setStatus}
          tree={libTree} pages={pagesById} onRestored={() => { fetchHomeBlocks(); rereadFamily(); }} />
        </LazyBoundary>
      ) : null}
      {exportOpen ? (
        <LazyBoundary>
        <ExportDialog
          opts={exportOpts}
          setOpts={setExportOpts}
          hasPdf={(isPdf(doc) || hasSheets) && !exportFolder}
          pdfStored={!!fileHash(doc) || hasSheets}
          hasMeta={!!pageMeta || !!pageBibtex}
          pageId={focusedBlock?.id || ""}
          folder={exportFolder}
          folderName={folderPath(libTree, exportFolder)}
          // The pages inside the page, at any depth, from the listing; a
          // page share's view, which has none, knows only those right inside
          // it (family), and the dialog then names no number.
          subPages={exportFolder ? 0
            : Math.max(pageDescendants(pageBlocks, [focusedBlock?.id || ""]).length, openFamily.subPages.length)}
          subPagesDirect={!pageBlocks.some((page) => page.id === focusedBlock?.id)}
          translated={exportFolder ? folderTranslated() : exportTranslated()}
          // the translation shown being read (a folder's papers'), or the export started
          busy={exportSeeding || exportStarting || !!folderTranslated()?.counting}
          shareView={shareMode}
          onCancel={closeExport}
          onExport={runExport}
          onLeaveJob={leaveExportJob}
          job={exportJobId ? tasks.byId(exportJobId) : null}
          fetchJob={tasks.fetchJob}
          onDownload={() => { const job = tasks.byId(exportJobId); if (job) tasks.download(job); }}
          onStop={() => { const job = tasks.byId(exportJobId); if (job) tasks.cancel(job).catch((err) => setStatus(err.message)); }}
          onRetry={() => {
            const job = tasks.byId(exportJobId);
            if (job) retryTask(job, "auto").then((again) => { if (again) setExportJobId(again.id); });
          }}
        />
        </LazyBoundary>
      ) : null}
      {confirmBox ? (
        // data-popover keeps an open popover (e.g. search) alive while the dialog is up
        <DialogOverlay data-popover="confirm" {...backdropPress(cancelConfirm)}>
          <div
            className="reportModal confirmModal"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); cancelConfirm(); } }}
          >
            <div className="confirmHead">
              <span className={`confirmIcon ${confirmBox.danger ? "danger" : ""}`}>
                {confirmBox.danger ? <AlertCircleIcon size={16} /> : <InfoIcon size={16} />}
              </span>
              <span className="settingText">
                <span className="reportModalTitle">{t(confirmBox.title)}</span>
                <span className="confirmMessage">{t(confirmBox.message)}</span>
              </span>
            </div>
            <div className="reportModalBtns">
              <button className="uiBtn" onClick={cancelConfirm} autoFocus>{t("Cancel")}</button>
              {confirmBox.altLabel ? (
                <button
                  className={`uiBtn ${confirmBox.altDanger ? "danger" : ""}`}
                  onClick={() => { const fn = confirmBox.onAlt; setConfirmBox(null); fn?.(); }}
                >{confirmBox.altLabel}</button>
              ) : null}
              <button
                className={`uiBtn primary ${confirmBox.danger ? "danger" : ""}`}
                onClick={() => { const fn = confirmBox.onConfirm; setConfirmBox(null); fn?.(); }}
              >{confirmBox.confirmLabel ? t(confirmBox.confirmLabel) : t("OK")}</button>
            </div>
          </div>
        </DialogOverlay>
      ) : null}
      {labelRenaming ? (
        <DialogOverlay {...backdropPress(() => setLabelRenaming(null))}>
          <div className="reportModal confirmModal" onClick={(e) => e.stopPropagation()}>
            <div className="reportModalTitle">{t("Rename label")}</div>
            <div className="reportModalHint confirmMessage">{t("Renames “{name}” on every page that carries it.", { name: labelName(libTree, labelRenaming.id) })}</div>
            <input
              autoFocus
              className="aiKeyInput"
              aria-label={t("Label name")}
              value={labelRenaming.draft}
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) => setLabelRenaming((s) => ({ ...s, draft: e.target.value }))}
              onKeyDown={(e) => {
                if (composing(e)) return;
                if (e.key === "Enter") renameLabel(labelRenaming.id, labelRenaming.draft);
                else if (e.key === "Escape") setLabelRenaming(null);
              }}
            />
            <div className="reportModalBtns">
              <button className="uiBtn" onClick={() => setLabelRenaming(null)}>{t("Cancel")}</button>
              <button
                className="uiBtn primary"
                disabled={!labelRenaming.draft.trim()}
                onClick={() => renameLabel(labelRenaming.id, labelRenaming.draft)}
              >{t("Rename")}</button>
            </div>
          </div>
        </DialogOverlay>
      ) : null}
      {movePicker ? <LazyBoundary><PagePicker
        label={movePicker.pageIds ? t("Move into page") : movePicker.fragment ? t("Move to page")
          : movePicker.blockIds?.length > 1 ? t("Move {n} blocks to page", { n: movePicker.blockIds.length }) : t("Move block to page")}
        placeholder={movePicker.pageIds ? t("Move into page…") : movePicker.fragment ? t("Move to page…") : t("Move block to page…")}
        note={movePicker.pageIds
          ? tn("The page goes inside the one you pick and is listed there, under Pages inside, rather than in the library.",
            "The {n} pages go inside the one you pick and are listed there, under Pages inside, rather than in the library.", movePicker.pageIds.length)
          : movePicker.fragment
          ? t("It becomes a block of its own at the end of the chosen page.")
          : movePicker.blockIds?.length > 1
          ? t("The {n} blocks and their sub-blocks move to the end of the chosen page, in their order.", { n: movePicker.blockIds.length })
          : t("The block and its sub-blocks move to the end of the chosen page.")}
        hint={t("↑↓ choose · Enter move · Esc close")}
        // a page goes inside no work folder's file (fileRules)
        pages={movePicker.pageIds ? pickerPages.filter((b) => !workFolderOf(b)) : pickerPages}
        exclude={moveExclude}
        tree={libTree}
        recentViews={recentViews}
        openTabs={openTabs}
        first={moveFirst}
        firstTitle={t("Inside this page")}
        onPick={moveToPicked}
        onClose={() => setMovePicker(null)}
      /></LazyBoundary> : null}
      {vault.adding ? (
        <LazyBoundary>
          <FolderPickerDialog count={vault.adding.length}
            workspaces={libraryWorkspaces(workspaces, wsId)} workspace={wsId} opener={!openedByOther(vault.adding)}
            folders={[{ id: "", name: t("All files") }, ...folderMenuIds.map((id) => ({ id, name: folderPath(libTree, id) }))]}
            readFolders={readWorkspaceFolders}
            onPick={(folder, keep, target) => addOpenedToLibrary(vault.adding, folder, keep, target)}
            onCarry={carriesAway(vault.adding) ? () => {
              vault.setAdding(null);
              askShell("carry-to-server", { page: vault.adding[0] });
            } : null}
            onClose={() => vault.setAdding(null)} />
        </LazyBoundary>
      ) : null}
      {work.addDialog ? (
        <LazyBoundary>
          <FolderPickerDialog mode="work" {...work.addDialog}
            folders={[{ id: "", name: t("All files") }, ...folderMenuIds.map((id) => ({ id, name: folderPath(libTree, id) }))]} />
        </LazyBoundary>
      ) : null}
      {/* the feedback view, while an opener (the list bar, the Comments window, the vault chip) has it open */}
      <FeedbackHost me={authUser?.user || ""} canSend={canWriteWorkspace} links={vault.links} onOpenThread={openThreadAt} />
      {vault.tracking ? (
        <LazyBoundary>
          <TrackDialog link={vault.tracking.link} fresh={vault.tracking.fresh} setStatus={setStatus}
            onClose={vault.closeTracking}
            onSaved={() => {
              vault.closeTracking();
              setStatus(t("Saved. The work folder reads its files now."));
              fetchHomeBlocks();
              vault.refreshLinks();
              vault.watchRound(vault.tracking.link.id, () => { fetchHomeBlocks({ still: true }); vault.refreshLinks(); });
            }}
            onCancelled={() => {
              vault.closeTracking();
              vault.refreshLinks();
              if (!homeMode) goHome();
              openFolder(lib.root);
              fetchHomeBlocks();
            }} />
        </LazyBoundary>
      ) : null}
      {vault.editing ? (
        <LazyBoundary>
          <FileSourceDialog pageId={vault.editing} onClose={() => vault.setEditing("")}
            onSaved={() => setStatus(t("Saved to the file. The page takes it at the vault's next round."))} />
        </LazyBoundary>
      ) : null}
      {linkPrompt ? (
        <DialogOverlay {...backdropPress(() => setLinkPrompt(null))}>
          <div className="reportModal confirmModal linkPromptModal" onClick={(e) => e.stopPropagation()}>
            <div className="confirmHead">
              <span className="confirmIcon"><LinkIcon size={16} /></span>
              <span className="settingText">
                <span className="reportModalTitle">{t("External link")}</span>
                <span className="confirmMessage">{t("Open this link in a new browser tab, or pull the paper into your library.")}</span>
              </span>
            </div>
            <div className="linkPromptUrl">{linkPrompt}</div>
            <div className="reportModalBtns">
              <button className="uiBtn" onClick={() => setLinkPrompt(null)}>{t("Cancel")}</button>
              {(() => {
                // Right-click always lands here, even for links whose paper is
                // already in the library — offer that copy instead of a re-fetch.
                const pid = findPageForUrl(linkPrompt, homeBlocks);
                return pid ? (
                  <button
                    className="uiBtn"
                    onClick={() => { setLinkPrompt(null); openBlock(pid, { pushNav: true }); }}
                    title={t("This paper is already in your library")}
                  ><FileTextIcon size={14} />{t("Open in Gamma")}</button>
                ) : (
                  <button
                    className="uiBtn"
                    onClick={() => { const url = linkPrompt; setLinkPrompt(null); pushNav(); openPdf(url); }}
                    title={t("Resolve this link as a PDF and open it as a new paper in Gamma")}
                  ><DownloadIcon size={14} />{t("Fetch into Gamma")}</button>
                );
              })()}
              <button
                className="uiBtn primary"
                onClick={() => { window.open(linkPrompt, "_blank", "noopener"); setLinkPrompt(null); }}
              ><ExternalLinkIcon size={14} />{t("Open in browser")}</button>
            </div>
          </div>
        </DialogOverlay>
      ) : null}
      {linkDialog ? <LazyBoundary><LinkDialog
        quote={linkDialog.quote || ""}
        url={linkDialog.url || ""}
        linked={!!linkDialog.editBlockId && !!highlights.find((x) => x.id === linkDialog.editBlockId)?.linkTarget}
        pages={pickerPages}
        exclude={linkExclude}
        tree={libTree}
        recentViews={recentViews}
        openTabs={openTabs}
        refPoint={refPoint && refPoint.pageId !== focusedBlockId ? refPoint : null}
        onLink={createLinkHighlight}
        onClose={() => setLinkDialog(null)}
      /></LazyBoundary> : null}
      {/* Mounted once first wanted, then kept, as when they loaded with the
          app (useLatch): closed, they render nothing and run no requests. */}
      {quickOpenMounted ? <LazyBoundary><QuickOpen
        open={!!quickOpen}
        prefix={quickOpen?.prefix || ""}
        commands={paletteCommands}
        onClose={() => setQuickOpen(null)}
        pages={pickerPages}
        recentViews={recentViews}
        openTabs={openTabs}
        currentPageId={focusedBlockId}
        onOpen={openPage}
        tree={libTree}
        folderMeta={folderMeta}
        onOpenFolder={(id) => { if (!homeMode) goHome(); openFolder(id); }}
        onOpenLabel={(id) => { if (!homeMode) goHome(); openLabel(id, ""); }}
        onSearch={openSearchWith}
        onCreate={lib.organize ? (title) => createPage(homeMode ? folderFilter : "", title) : null}
      /></LazyBoundary> : null}
      {guideMounted ? <LazyBoundary><GuideOverlay guide={guide} keybindings={keybindings} /></LazyBoundary> : null}
      {settingsMounted ? <LazyBoundary><SettingsDialog
        activePane={settingsOpen}
        profileSync={profileSync}
        notices={notices}
        onPaneChange={setSettingsOpen}
        onClose={() => { aiEditor.close(); setSettingsOpen(null); }}
        papers={{
          theme,
          setTheme,
          language,
          setLanguage,
          uiScale,
          setUiScale,
          oaFallback,
          setOaFallback,
          metaAutoFetch,
          setMetaAutoFetch,
          pdfSaveLocal,
          setPdfSaveLocal,
          openAddsToLibrary,
          setOpenAddsToLibrary,
          embAnnots,
          setEmbAnnots,
          smoothWheel,
          setSmoothWheel,
          // the server's relocate-highlights run reads these from the profile
          relocateMinMatch: appPrefs.relocateMinMatch,
          setRelocateMinMatch: appPrefs.setRelocateMinMatch,
          relocateExactChars: appPrefs.relocateExactChars,
          setRelocateExactChars: appPrefs.setRelocateExactChars,
          inkPenOnly,
          setInkPenOnly,
          inkPenWritesPdf,
          setInkPenWritesPdf,
          inkPressure,
          setInkPressure,
          inkFingerUndo,
          setInkFingerUndo,
          inkHoverPreview,
          setInkHoverPreview,
          inkPencilTap,
          setInkPencilTap,
          inkPencilSqueeze,
          setInkPencilSqueeze,
          inkHaptics,
          setInkHaptics,
          inkDrawHold,
          setInkDrawHold,
          inkScribbleErase,
          setInkScribbleErase: setScribbleErase,
          inkEraserReturn,
          setInkEraserReturn,
          inkEraserFilter,
          setInkEraserFilter,
          setInkTools,
          confirm: setConfirmBox,
          replayLeadIn,
          setReplayLeadIn,
          recordingTranscribe,
          setRecordingTranscribe,
          selTranslate,
          setSelTranslate,
          selTranslateAuto,
          setSelTranslateAuto,
          translateLang,
          setTranslateLang,
          translateModel,
          setTranslateModel,
          translateEffort,
          setTranslateEffort,
          translateSpeed,
          setTranslateSpeed,
          translateParallel,
          setTranslateParallel,
          translateKeep,
          setTranslateKeep,
          readOnly, // a viewer of the workspace cannot clear its kept translations
          aiModels: scopedAiModels, // the translation picker's registry
          translateEngines, // …and the translation services set up for it
          refreshAiModels, // saving a service's key updates that list
          pdfDarkPage,
          setPdfDarkPage,
          palette,
          setPalette,
          highlightPresets,
          setHighlightPresets: textHighlighter.setPresets,
          highlighterColor,
          firstSwatch: textHighlighter.firstSwatch,
          suggestTours,
          setSuggestTours,
          recentThumbs,
          setRecentThumbs,
          fileLabels,
          setFileLabels,
          topbar,
          setTopbar,
          viewerTools,
          setViewerTools,
          backlinksVisible,
          setBacklinksVisible,
          inboxSystemNotifications,
          setInboxSystemNotifications,
          hasInbox: !!authUser?.user && !authUser.is_guest, // a guest has none
          isAdmin: !!authUser?.is_admin,
          setStatus,
          refreshQuota, // keep the storage meters in sync without a re-login
        }}
        keyboard={{ keybindings, setKeybindings, enterNewBlock, setEnterNewBlock }}
        library={{
          // the batch metadata lookup (a background job) uses the same
          // prompt/model/context prefs as the per-paper fetch in the
          // metadata popover
          metaPrompt,
          metaFetchModel,
          metaContextChars,
          citePrompt,
          citeModel: chatSendModel,
          indexing,
          metadataJob,
          tasks,
          wakeTasks: tasks.wake,
          setStatus,
          // status-table row click: jump to the paper (closing the dialog)
          openPaper: (id) => { setSettingsOpen(null); openPage(id); },
        }}
        ai={{
          ...aiEditor,
          chatModel: chatSendModel,
          setChatModel,
          chatEffort,
          setChatEffort,
          chatSpeed,
          setChatSpeed,
          aiProvider,
          setAiProvider,
          deleteAiProvider,
          aiKeyTests,
          testAiProvider,
          aiKeyUsage,
          queryAiProviderUsage,
          aiLoginCheck,
          setAiLoginCheck,
          metaModel,
          setMetaModel,
          aiModels: scopedAiModels,
          dictationModel,
          setDictationModel,
          dictationLang,
          setDictationLang,
        }}
        prompts={{
          aiInfo,
          chatSystem,
          metaPrompt,
          citePrompt,
          promptDraft,
          setPromptDraft,
          metaPromptDraft,
          setMetaPromptDraft,
          citePromptDraft,
          setCitePromptDraft,
          agentSystem,
          agentPromptDraft,
          setAgentPromptDraft,
          savePrompts: () => {
            const normalizePrompt = (draft, defaultValue) => {
              const value = (draft || "").trim();
              return value === (defaultValue || "").trim() ? "" : value;
            };
            setChatSystem(normalizePrompt(promptDraft, aiInfo?.default_prompt));
            setMetaPrompt(normalizePrompt(metaPromptDraft, aiInfo?.metadata_prompt));
            setCitePrompt(normalizePrompt(citePromptDraft, aiInfo?.cite_prompt));
            setAgentSystem(normalizePrompt(agentPromptDraft, aiInfo?.agent_prompt));
            setStatus(t("Prompts saved."));
          },
        }}
        context={{
          chatImgAutoClear,
          setChatImgAutoClear,
          chatPictures,
          setChatPictures,
          chatContextChars,
          setChatContextChars,
          metaContextChars,
          setMetaContextChars,
          multiContextChars,
          setMultiContextChars,
          toolRounds,
          setToolRounds,
          toolRoundsUnlimited,
          setToolRoundsUnlimited,
          agentReadChars,
          setAgentReadChars,
          agentPerms,
          setAgentPerms,
          agentEnabled,
          setAgentEnabled,
          fetchInBackground,
          setFetchInBackground,
          delegateReads,
          setDelegateReads,
          reset: () => {
            setChatContextChars(60000);
            setMetaContextChars(6000);
            setMultiContextChars(120000);
            setStatus(t("AI context limits reset."));
          },
        }}
        search={{ searchDetailsHome, setSearchDetailsHome, searchDetailsPaper, setSearchDetailsPaper, indexing, setStatus }}
        workspace={authUser?.user && !authUser.is_guest ? {
          workspace,
          me: authUser.user,
          isAdmin: !!authUser?.is_admin,
          switchWorkspace,
          refreshSession: checkSession,
          exportWorkspace,
          exportAll,
          importWorkspace,
          tasks,
          setStatus,
          confirm: setConfirmBox,
          closeSettings: () => setSettingsOpen(null),
        } : null}
        backups={authUser?.user && !authUser.is_guest ? {
          workspace,
          isAdmin: !!authUser?.is_admin, // Off-site copies
          tasks,
          setStatus,
          confirm: setConfirmBox,
        } : null}
        server={authUser?.is_admin ? {
          me: authUser.user,
          workspaces,
          switchWorkspace,
          refreshSession: checkSession,
          refreshQuota,
          tasks,
          setStatus,
          confirm: setConfirmBox,
          closeSettings: () => setSettingsOpen(null),
        } : null}
        users={authUser?.user ? {
          // Everyone gets this pane; only admins see the other accounts, the
          // account editor and each account's personal workspaces.
          isAdmin: !!authUser?.is_admin,
          me: authUser.user,
          isGuest: !!authUser?.is_guest,
          quotaInfo,
          workspaces,
          switchWorkspace,
          refreshSession: checkSession,
          tasks,
          setStatus,
          confirm: setConfirmBox,
          closeSettings: () => setSettingsOpen(null),
          onSelfRenamed: checkSession, // self-rename re-keys the whole app
          refreshQuota,
        } : null}
        diagnostics={{ statusBarVisible, setStatusBarVisible, sysLog, setStatus, debugLog, setDebugLog,
          openReport: () => { setSettingsOpen(null); setReportOpen(true); } }}
      /></LazyBoundary> : null}
      <ReportContentHost setStatus={setStatus} />
      {reportOpen ? (
        <LazyBoundary>
        <ReportProblem onClose={() => setReportOpen(false)} setStatus={setStatus}
          facts={{
            build: authUser?.build,
            view: { mode: shareMode ? "share" : homeMode ? "home" : "page", pdf: !!pdfUrl, readOnly, phone: isPhone, theme, uiScale },
            workspace: workspace ? {
              kind: workspace.mirror_of ? "clone" : workspace.zotero_of ? "zotero" : workspace.personal ? "personal" : workspace.access === "public" ? "public" : "shared",
              role: workspace.role,
            } : null,
            events: sysLog,
            isAdmin: !!authUser?.is_admin,
          }} />
        </LazyBoundary>
      ) : null}
      {tabMenu ? (() => {
        // Two pins: the tab pin (this device's tab strip, synced with the
        // tabs) and the library pin (the page's Pinned strip on the home
        // page — properties.pinned, same as the home context menu).
        const libPinned = !!knownBlocks.find((b) => b.id === tabMenu.id)?.properties?.pinned;
        // The tab's page among pages: a page made inside it, it moved into
        // another, or out of the one it is in. A work folder's file is never
        // one of them (fileRules): it has its library rows instead.
        const inside = !!pagesById.get(tabMenu.id)?._parentPage;
        const tabFile = work.fileOf(tabMenu.id);
        return (
        <ContextMenu x={tabMenu.x} y={tabMenu.y} onClose={() => setTabMenu(null)}>
          <MenuItem icon={PinIcon} onClick={() => { setTabMenu(null); toggleTabPinned(tabMenu.id); }}>
            {tabMenu.pinned ? t("Unpin tab") : t("Pin tab")}
          </MenuItem>
          <MenuItem icon={HomeIcon} title={t("Pinned pages sit in the Pinned strip at the top of the library, on every device")}
            onClick={() => { setTabMenu(null); setPagesPinned([tabMenu.id], !libPinned); }}>
            {libPinned ? t("Unpin from library") : t("Pin to library")}
          </MenuItem>
          <MenuItem icon={BracesIcon} onPointerEnter={BlockProperties.preload}
            onClick={() => { setTabMenu(null); openProps(tabMenu.id, null, { at: { x: tabMenu.x, y: tabMenu.y } }); }}>
            {t("Page properties")}
          </MenuItem>
          {!isPhone ? (
            // Tab groups (docs/dev/tab_groups.md): one group splits once, this
            // tab into a pane of its own on the right (the only tab of the
            // group: an empty pane beside it); split, the tab moves to the
            // other pane, or every tab goes back into one group.
            <>
              <MenuDivider />
              {isSingle(tabLayout) ? (
                <MenuItem icon={SplitColumnsIcon} title={t("Show this page in a group of its own, to the right")}
                  onClick={() => { setTabMenu(null); splitTab(tabMenu.id, "right"); }}>{t("Split right")}</MenuItem>
              ) : (
                <>
                  {otherPane(tabMenu.id) ? (
                    <MenuItem icon={SplitColumnsIcon} title={t("Show this page in the other pane")}
                      onClick={() => { setTabMenu(null); moveTabToGroup(tabMenu.id, otherPane(tabMenu.id).id); }}>{t("Move to the other pane")}</MenuItem>
                  ) : null}
                  <MenuItem icon={OnePaneIcon} title={t("Every tab back in one group")}
                    onClick={() => { setTabMenu(null); joinTabGroups(); }}>{t("Join tab groups")}</MenuItem>
                </>
              )}
            </>
          ) : null}
          {lib.organize ? (
            <>
              <MenuDivider />
              {tabFile ? work.libraryItems([tabFile], () => setTabMenu(null)) : (
                <MenuItem icon={FilePlusIcon} title={t("A blank page inside this one")}
                  onClick={() => { setTabMenu(null); createSubPage(tabMenu.id); }}>{t("New page inside")}</MenuItem>
              )}
              {tabFile ? null : openedPdf(tabMenu.id) ? (
                // a PDF opened from this computer: stored first, then filed
                <MenuItem icon={FolderPlusIcon} title={t("Store the PDF in the library and file the page in a folder: its highlights, notes and chat come with it")}
                  onClick={() => { setTabMenu(null); vault.setAdding([tabMenu.id]); }}>{t("Add to library…")}</MenuItem>
              ) : <>
                <MenuItem icon={IndentIcon} title={t("Put it inside another page, where it is listed under Pages inside")}
                  onClick={() => { setTabMenu(null); pickPageParent([tabMenu.id]); }}>{t("Move into page…")}</MenuItem>
                {inside ? (
                  <MenuItem icon={OutdentIcon} title={t("Take it out of the page it is in, back into the library and the folders it was filed in")}
                    onClick={() => { setTabMenu(null); movePagesToLibrary([tabMenu.id]); }}>{t("Move to library")}</MenuItem>
                ) : null}
              </>}
              <MenuDivider />
            </>
          ) : null}
          <MenuItem icon={XIcon} onClick={() => { setTabMenu(null); closeTab(tabMenu.id); }}>
            {t("Close tab")}
          </MenuItem>
        </ContextMenu>
        );
      })() : null}
      {homeMenu ? (
        <ContextMenu x={homeMenu.x} y={homeMenu.y} onClose={() => setHomeMenu(null)}>
            {homeMenu.kind === "page" ? (() => {
              // Acting on a selected card acts on the whole selection
              const ids = selectedPages.size > 1 && selectedPages.has(homeMenu.id) ? [...selectedPages] : [homeMenu.id];
              const many = ids.length > 1;
              const acted = ids.map((id) => pageBlocks.find((b) => b._pageId === id));
              const allPinned = acted.every((b) => b?._pinned);
              // A page inside another (the strips and the filter box list
              // them) is filed with its top: Move to folder files the library
              // pages among them and says it left the rest out
              // (filablePages); Move to library takes the topmost of them out.
              // A work folder's file (fileRules) is filed by its directory:
              // in the library it may be filed in library folders too, never
              // out of its directory; it has its own library rows, and with
              // other pages beside it neither those nor the moves.
              // Folders the acted-on pages already sit in (`ownFolders`) are
              // offered for removal alongside the current folder view (a
              // file's directory never: workFolderActions.pagesMenu).
              const { files, allFiles, anyFile, ruled, filable, leftOut, ownFolders, copied } = pagesMenu(acted, ids, pagesById, workTree);
              const outOf = topmostPages(pageBlocks, ids).filter((id) => pagesById.get(id)?._parentPage);
              const fromHere = folderFilter && !workFolder ? folderFilter : "";
              // PDFs opened from this computer (inside *Opened files*): all
              // of them, or any among the acted-on pages (addingToLibrary)
              const opened = ids.every(openedPdf);
              const anyOpened = ids.some(openedPdf);
              const allLabelled = (l) => acted.every((b) => b?._labels?.includes(l));
              const close = () => setHomeMenu(null);
              // A typed name: the label of that name, else a new one.
              const commitNewLabel = async () => {
                const name = (homeMenuLabelDraft || "").trim();
                close();
                if (!name) return;
                try {
                  addPagesToLabel(ids, await makeLabel(name));
                } catch (err) {
                  setStatus(t("Could not make the label: {message}", { message: err.message }));
                }
              };
              // Open · Rename | New page inside | Pin · Add label · Move to
              // folder · Add to library… · Remove from library · Save a copy
              // to library… · Move into page… · Move to library · Duplicate |
              // Copy link · Share… · Export… · Ask AI | Delete — the same rows
              // wherever the page was right-clicked (a Pages inside row's
              // without Rename: openSubPageMenu).
              return menuGroups(
                [
                  !many && (
                    <MenuItem key="open" icon={ExternalLinkIcon} keys={chordLabel("Enter")}
                      onClick={() => { close(); clearSelection(); openBlock(homeMenu.id, { restoreScroll: true, pushNav: !!homeMenu.panel }); }}>{t("Open")}</MenuItem>
                  ),
                  !many && lib.organize && !homeMenu.panel && ruled("rename") && (
                    <MenuItem key="rename" icon={PenIcon} keys={commandKeyLabel("app.renameTitle")}
                      onClick={() => { close(); clearSelection(); setHomeEditingId(homeMenu.id); }}>{t("Rename")}</MenuItem>
                  ),
                ],
                [
                  !many && lib.organize && !anyFile && (
                    <MenuItem key="newinside" icon={FilePlusIcon} title={t("A blank page inside this one")}
                      onClick={() => { close(); clearSelection(); createSubPage(homeMenu.id); }}>{t("New page inside")}</MenuItem>
                  ),
                ],
                [
                  lib.pin && (
                    <MenuItem key="pin" icon={PinIcon} onClick={() => { close(); setPagesPinned(ids, !allPinned); }}>
                      {allPinned ? t("Unpin") : many ? t("Pin {n} pages", { n: ids.length }) : t("Pin")}
                    </MenuItem>
                  ),
                  lib.organize && (
                    <SubMenuItem key="labels" id="labels" icon={LabelIcon} label={t("Add label")}
                      title={t("Labels are flat tags a page can carry several of; a checked one is removed")}>
                      {allLabels.map(({ id: l, name }) => (
                        <MenuItem
                          key={l}
                          role="menuitemcheckbox"
                          aria-checked={allLabelled(l)}
                          icon={LabelIcon}
                          title={allLabelled(l) ? t("Remove the label “{l}”", { l: name }) : name}
                          trailing={allLabelled(l) ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
                          onClick={() => { close(); if (allLabelled(l)) removePagesFromLabel(ids, l); else addPagesToLabel(ids, l); }}
                        >{name}</MenuItem>
                      ))}
                      {allLabels.length ? <MenuDivider /> : null}
                      {homeMenuLabelDraft === null ? (
                        <MenuItem icon={PlusIcon} onClick={() => setHomeMenuLabelDraft("")}>{t("New label…")}</MenuItem>
                      ) : (
                        <input
                          autoFocus
                          className="ctxMenuInput"
                          value={homeMenuLabelDraft}
                          placeholder={t("Label name")}
                          aria-label={t("New label")}
                          onChange={(e) => setHomeMenuLabelDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (composing(e)) return;
                            if (e.key === "Enter") { e.preventDefault(); commitNewLabel(); }
                            else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setHomeMenuLabelDraft(null); }
                          }}
                        />
                      )}
                    </SubMenuItem>
                  ),
                  lib.organize && filable.length > 0 && !anyOpened && (!anyFile || allFiles) && (
                    <SubMenuItem
                      key="folders"
                      id="folders"
                      icon={FolderIcon}
                      label={t("Move to folder")}
                      title={t("A page can sit in several folders — this adds it to the one you pick (same as dragging it onto the folder).")}
                    >
                      {folderMenuIds.length ? folderMenuRows(folderMenuIds, (f) => ownFolders.includes(f), (f) => addPagesToFolder(ids, f)) : (
                        <MenuItem disabled>{t("No folders yet")}</MenuItem>
                      )}
                      {fromHere || ownFolders.length ? <MenuLabel>{t("Remove from")}</MenuLabel> : null}
                      {fromHere ? (
                        <MenuItem icon={FolderOpenIcon} onClick={() => { close(); removePagesFromFolder(ids, fromHere); }}>{`“${folderPath(libTree, fromHere)}”`}</MenuItem>
                      ) : null}
                      {ownFolders.filter((f) => f !== fromHere).map((f) => (
                        <MenuItem key={`rm:${f}`} icon={FolderOpenIcon} title={folderPath(libTree, f)} onClick={() => { close(); removePagesFromFolder(ids, f); }}>{`“${folderPath(libTree, f)}”`}</MenuItem>
                      ))}
                      {fromHere || ownFolders.length ? (
                        <MenuItem icon={XIcon} onClick={() => { close(); removePagesFromFolder(ids, ""); }}>{t("All folders")}</MenuItem>
                      ) : null}
                      {leftOut ? (
                        <>
                          <MenuDivider />
                          <MenuItem disabled>{tn("{n} page inside another page is left out: it is filed with its top.",
                            "{n} pages inside other pages are left out: they are filed with their tops.", leftOut)}</MenuItem>
                        </>
                      ) : null}
                    </SubMenuItem>
                  ),
                  // PDFs opened from this computer are stored before they are
                  // filed: Add to library… in place of the moves, and with
                  // other pages beside them, neither
                  lib.organize && opened && (
                    <MenuItem key="add" icon={FolderPlusIcon} title={t("Store the PDF in the library and file the page in a folder: its highlights, notes and chat come with it")}
                      onClick={() => { close(); vault.setAdding(ids); }}>{t("Add to library…")}</MenuItem>
                  ),
                  ...(lib.organize && allFiles ? work.libraryItems(files, close) : []),
                  lib.organize && !anyOpened && !anyFile && (
                    <MenuItem key="into" icon={IndentIcon} title={t("Put it inside another page, where it is listed under Pages inside")}
                      onClick={() => { close(); pickPageParent(ids); }}>{t("Move into page…")}</MenuItem>
                  ),
                  lib.organize && outOf.length > 0 && !anyOpened && !anyFile && (
                    <MenuItem key="library" icon={OutdentIcon} title={t("Take it out of the page it is in, back into the library and the folders it was filed in")}
                      onClick={() => { close(); movePagesToLibrary(outOf); }}>{t("Move to library")}</MenuItem>
                  ),
                  lib.organize && copied.length > 0 && (
                    <MenuItem key="duplicate" icon={CopyIcon} onClick={() => { close(); duplicatePages(copied); }}>{copied.length > 1 ? t("Duplicate {n} pages", { n: copied.length }) : t("Duplicate")}</MenuItem>
                  ),
                ],
                [
                  !many && !shareMode && (
                    <MenuItem key="link" icon={LinkIcon} onClick={() => { close(); copyPageLink(homeMenu.id); }}>{t("Copy link")}</MenuItem>
                  ),
                  !many && lib.organize && (
                    <MenuItem key="share" icon={ShareIcon} title={t("Share this page — a link, and who may view or edit it")}
                      onClick={() => openPageThen(homeMenu.id, "share", homeMenu.panel)}>{t("Share…")}</MenuItem>
                  ),
                  !many && (
                    <MenuItem key="export" icon={ExportIcon} title={t("Download this page — the PDF with highlights and notes, Markdown, a Logseq graph, a Zotero library, or a Gamma export")}
                      onClick={() => openPageThen(homeMenu.id, "export", homeMenu.panel)}>{t("Export…")}</MenuItem>
                  ),
                  !many && !shareMode && (
                    <MenuItem key="ask" icon={SparklesIcon} onClick={() => openPageThen(homeMenu.id, "ask", homeMenu.panel)}>{t("Ask AI about this page")}</MenuItem>
                  ),
                  !many && (
                    <MenuItem key="props" icon={BracesIcon} onPointerEnter={BlockProperties.preload}
                      onClick={() => { close(); openProps(homeMenu.id, null, { at: { x: homeMenu.x, y: homeMenu.y }, opener: homeMenu.el }); }}>{t("Properties")}</MenuItem>
                  ),
                ],
                [
                  lib.organize && ruled("trash") && (
                    <MenuItem key="delete" icon={TrashIcon} danger keys={homeMenu.panel ? undefined : commandKeyLabel("app.deletePages")}
                      onClick={() => { close(); deletePages(ids); }}>{many ? t("Delete {n} pages", { n: ids.length }) : t("Delete")}</MenuItem>
                  ),
                ],
              );
            })() : homeMenu.kind === "label" ? menuGroups(
              [
                <MenuItem key="open" icon={LabelIcon} onClick={() => { const id = homeMenu.id; setHomeMenu(null); if (!homeMode) goHome(); openLabel(id, homeMode ? folderFilter : ""); }}>{t("Open")}</MenuItem>,
                lib.organize && <MenuItem key="rename" icon={PenIcon} onClick={() => { setHomeMenu(null); setLabelRenaming({ id: homeMenu.id, draft: labelName(libTree, homeMenu.id) }); }}>{t("Rename")}</MenuItem>,
                <MenuItem key="props" icon={BracesIcon} onPointerEnter={BlockProperties.preload}
                  onClick={() => { setHomeMenu(null); openProps(homeMenu.id, null, { at: { x: homeMenu.x, y: homeMenu.y }, opener: homeMenu.el }); }}>{t("Properties")}</MenuItem>,
              ],
              [lib.organize && <MenuItem key="delete" icon={TrashIcon} danger onClick={() => { setHomeMenu(null); deleteLabel(homeMenu.id); }}>{t("Delete")}</MenuItem>],
            ) : (() => {
              // Like pages: acting on a selected folder acts on the whole selection
              const folders = selectedFolders.size > 1 && selectedFolders.has(homeMenu.id) ? [...selectedFolders] : [homeMenu.id];
              const allPinned = folders.every((f) => libTree.folders.get(f)?.pinned);
              const id = homeMenu.id;
              // Inside a work folder the folders are its directories, which
              // follow the disk (folderRules): every row asks of each folder.
              const ruled = (rule) => folders.every((f) => lib.organizeIn(f)[rule]);
              const inWork = folders.some((f) => lib.workFolder(f));
              // Move up / down step one folder past its neighbour in the
              // custom order, where the listing shows that order (a work
              // folder's lists its directories by name).
              const [up, down] = [folderNeighbour(libTree, id, -1), folderNeighbour(libTree, id, 1)];
              const stepping = lib.organize && homeMode && homeSort === "manual" && folders.length === 1 && !inWork;
              const choose = folders.length === 1 && lib.organizeIn(id).chooseFiles ? work.chooser(id) : null;
              // Move to: the library's folders every one of them may go
              // into, in the flyout's order (folderRules moveTargets).
              const into = folders.map((f) => new Set(lib.organizeIn(f).moveTargets));
              return menuGroups(
                [
                  <MenuItem key="open" icon={FolderOpenIcon} keys={chordLabel("Enter")}
                    onClick={() => { setHomeMenu(null); if (!homeMode) goHome(); openFolder(id); }}>{t("Open")}</MenuItem>,
                  choose && (
                    <MenuItem key="track" icon={SquareCheckIcon} title={t("Choose which files of its directory the work folder tracks")}
                      onClick={() => { setHomeMenu(null); choose(); }}>{t("Choose files…")}</MenuItem>
                  ),
                ],
                [
                  ruled("newPage") && (
                    <MenuItem key="newpage" icon={FilePlusIcon} title={inWork ? t("A new note, written into this directory") : t("A blank page filed in this folder")}
                      onClick={() => { setHomeMenu(null); createPage(id); }}>{t("New page here")}</MenuItem>
                  ),
                  ruled("newSubfolder") && (
                    <MenuItem key="newfolder" icon={FolderPlusIcon}
                      onClick={() => {
                        setHomeMenu(null);
                        if (!homeMode) goHome();
                        openFolder(id);
                        setNewFolderName("");
                        setNewFolderOpen(true);
                      }}>{t("New subfolder")}</MenuItem>
                  ),
                ],
                [
                  ruled("move") && (
                    <SubMenuItem key="move" id="move" icon={FolderIcon} label={t("Move to")}>
                      {folderMenuRows(["", ...folderMenuIds.filter((f) => into.every((targets) => targets.has(f)))],
                        (f) => folders.every((m) => libTree.folders.get(m)?.parent === f), (f) => moveFolders(folders, f))}
                    </SubMenuItem>
                  ),
                  stepping && (
                    <MenuItem key="up" icon={ArrowUpIcon} disabled={!up}
                      onClick={() => { setHomeMenu(null); placeFolders([id], up, false); }}>{t("Move up")}</MenuItem>
                  ),
                  stepping && (
                    <MenuItem key="down" icon={ArrowDownIcon} disabled={!down}
                      onClick={() => { setHomeMenu(null); placeFolders([id], down, true); }}>{t("Move down")}</MenuItem>
                  ),
                ],
                [
                  lib.organizeIn(id).rename && (
                    <MenuItem key="rename" icon={PenIcon} onClick={() => { setHomeMenu(null); setFolderRenaming({ id, draft: libTree.folders.get(id)?.name || "" }); }}>{t("Rename")}</MenuItem>
                  ),
                  lib.pin && (
                    <MenuItem key="pin" icon={PinIcon} title={t("Pinned folders sit in the Pinned strip at the top of the library, on every device")}
                      onClick={() => { setHomeMenu(null); setFoldersPinned(folders, !allPinned); }}>
                      {allPinned ? t("Unpin") : folders.length > 1 ? t("Pin {n} folders", { n: folders.length }) : t("Pin")}
                    </MenuItem>
                  ),
                  lib.organize && (
                    <MenuItem key="share" icon={ShareIcon} title={t("A link that opens every page filed in this folder, now and later")}
                      onClick={() => { setHomeMenu(null); openFolderShare(id); }}>{t("Share…")}</MenuItem>
                  ),
                  <MenuItem
                    key="export"
                    icon={ExportIcon}
                    title={t("Download every page in this folder — Markdown, a Logseq graph, a Zotero library, or a Gamma export")}
                    onClick={() => { setHomeMenu(null); setExportFolder(id); setExportOpen(true); }}
                  >{t("Export…")}</MenuItem>,
                  // In the desktop app: its page preload passes the message to the app,
                  // which asks for a directory and makes it a synced vault of the folder
                  // (desktop/docs/architecture.md). Making one takes an editor.
                  IS_DESKTOP && lib.organize && folders.length === 1 && !inWork && (
                    <MenuItem key="disk" icon={HardDriveIcon}
                      title={t("Make this folder a synced vault: a directory on this computer where each page is a Markdown note beside its PDF, pulled and pushed by the desktop app")}
                      onClick={() => {
                        setHomeMenu(null);
                        askShell("keep-folder-on-disk", { folder: id });
                      }}>{t("Sync with this computer…")}</MenuItem>
                  ),
                  folders.length === 1 && (
                    <MenuItem key="props" icon={BracesIcon} onPointerEnter={BlockProperties.preload}
                      onClick={() => { setHomeMenu(null); openProps(id, null, { at: { x: homeMenu.x, y: homeMenu.y }, opener: homeMenu.el }); }}>{t("Properties")}</MenuItem>
                  ),
                ],
                [
                  lib.organizeIn(id).delete && (
                    <MenuItem key="delete" icon={TrashIcon} danger onClick={() => { setHomeMenu(null); deleteFolder(id); }}>{t("Delete")}</MenuItem>
                  ),
                ],
              );
            })()}
        </ContextMenu>
      ) : null}
      {highlightMenu ? (
        <ContextMenu x={highlightMenu.x} y={highlightMenu.y} onClose={() => setHighlightMenu(null)}>
          {highlightRows(highlightMenu.id, () => setHighlightMenu(null))}
        </ContextMenu>
      ) : null}
      {/* A translated paragraph's menu (PdfViewer onParagraphContext,
          docs/dev/translation.md "A paragraph's menu"): its text to copy, and
          for an editor what to do with its translation. A joined paragraph
          acts as one. A row that starts a job waits while one runs on the
          document; a service answers the same text the same way, so it has
          no Translate again. */}
      {paraMenu ? (() => {
        const close = () => setParaMenu(null);
        const ctl = pdfTranslateCtl.current;
        const waiting = paraMenu.running ? { disabled: true, title: t("Wait for the translation under way to finish") } : null;
        const ask = () => { close(); ctl?.translateUnit(paraMenu.url, paraMenu.pn, paraMenu.i); };
        return (
          <ContextMenu x={paraMenu.x} y={paraMenu.y} onClose={close}>
            {menuGroups(
              [
                paraMenu.translation && (
                  <MenuItem key="copy-translation" icon={CopyIcon} onClick={() => { close(); copyText(paraMenu.translation); }}>
                    {t("Copy translation")}
                  </MenuItem>
                ),
                <MenuItem key="copy-original" icon={FileTextIcon} onClick={() => { close(); copyText(paraMenu.source); }}>
                  {t("Copy original")}
                </MenuItem>,
              ],
              readOnly ? [] : [
                paraMenu.translated && !paperModel.startsWith("engine:") && (
                  <MenuItem key="again" icon={RefreshIcon} onClick={ask} {...waiting}>{t("Translate again")}</MenuItem>
                ),
                !paraMenu.translated && (
                  <MenuItem key="translate" icon={LanguagesIcon} onClick={ask} {...waiting}>{t("Translate this paragraph")}</MenuItem>
                ),
                paraMenu.translated && !paraMenu.original && !paraMenu.peek && (
                  <MenuItem key="original" icon={EyeOffIcon}
                    onClick={() => { close(); ctl?.setOriginal(paraMenu.url, paraMenu.unit, true); }}>
                    {t("Show original here")}
                  </MenuItem>
                ),
                paraMenu.original && (
                  <MenuItem key="translation" icon={EyeIcon}
                    onClick={() => { close(); ctl?.setOriginal(paraMenu.url, paraMenu.unit, false); }}>
                    {t("Show translation here")}
                  </MenuItem>
                ),
              ],
              // A highlight on the paragraph: its rects over the translation
              // take no pointer, so its own menu is here.
              [
                paraMenu.highlight && highlights.some((h) => h.id === paraMenu.highlight) && (
                  <SubMenuItem key="highlight" id="highlight" icon={HighlightIcon} label={t("Highlight")}>
                    {highlightRows(paraMenu.highlight, close, true)}
                  </SubMenuItem>
                ),
              ],
            )}
          </ContextMenu>
        );
      })() : null}
      {threadPop && focusedBlockId && commentsOn && !threadPop.margin ? (
        // One per app, keyed by its block: another block's is a fresh one.
        <LazyBoundary>
          <ThreadPopover
            key={threadPop.blockId}
            target={threadPop}
            threads={comments.threadsByBlock.get(threadPop.blockId)}
            kind={threadPop.blockId === focusedBlockId ? "page" : isPlaceBlock(treeById.get(threadPop.blockId)) ? "highlight" : "note"}
            me={commentMe}
            owner={commentOwner}
            canComment={canComment}
            people={people}
            labelsFor={commentLabels}
            // the fields' [[ and @ pickers, as a note row's (rowProps)
            links={{ pages: pageBlocks, rootId: focusedBlockId, lookupBlock: (id) => treeById.get(id), refCache, onCacheRef, agent: commentAgent }}
            onBlockRefClick={openBlockLink}
            onAdd={comments.add}
            onEdit={comments.edit}
            onDelete={comments.remove}
            onResolve={comments.resolve}
            onCopyLink={copyThreadLink}
            // a visitor without an account: the share's own sign-in gate
            onSignIn={commentSignIn}
            onClose={closeThread}
          />
        </LazyBoundary>
      ) : null}
      {propsPop ? (
        // One per app, keyed by its opening: a step and Back change it in place.
        <LazyBoundary>
          <BlockProperties
            key={propsPop.n}
            target={propsPop}
            // the block as this tab has it: the open page or a row of it (a
            // library card's page is the listing's summary, a few of its
            // keys, so it waits for the server's dict)
            live={(propsPop.blockId === focusedBlockId ? focusedBlock : null) || treeById.get(propsPop.blockId) || null}
            isOpenPage={!homeMode && propsPop.blockId === focusedBlockId}
            ctx={{ pages: pageBlocks, libTree, lookupBlock: (id) => treeById.get(id), refCache, onFetchRefs,
              // the open page ("" on the library)
              pageFileHash: fileHash(doc), shareMode, pageId: homeMode ? "" : focusedBlockId || "", pageTitle, tree: blocks }}
            nav={{
              openBlockLink, showOnPage, jumpToHighlightId,
              openFolder: (id) => { if (!homeMode) goHome(); openFolder(id); },
              openLabel: (id) => { if (!homeMode) goHome(); openLabel(id, homeMode ? folderFilter : ""); },
              openMetaPopover,
              // through the external-link prompt, as "Open link in browser…"
              openUrl: setLinkPrompt,
              stepInto: (id) => openProps(id, propsPop.anchor, { at: propsPop.at, push: true }),
              back: () => setPropsPop((p) => (p?.back.length ? { ...p, blockId: p.back.at(-1), back: p.back.slice(0, -1) } : p)),
            }}
            onClose={closeProps}
          />
        </LazyBoundary>
      ) : null}
      <ReminderAlerts due={reminders.due} now={reminders.now} labelOf={refText} phone={isPhone}
        onOpen={openReminder} onDismiss={reminders.dismiss} />
    </div>
    </MentionClock.Provider>
    </MentionContext.Provider>
    </MarkToolsContext.Provider>
    </PaletteContext.Provider>
    </RecordingContext.Provider>
    </ThreadPlaceContext.Provider>
    </PageToolsContext.Provider>
    </GammaNavContext.Provider>
  );
}
