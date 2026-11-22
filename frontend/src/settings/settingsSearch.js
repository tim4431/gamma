// Settings search (docs/dev/settings.md): the catalog the dialog's search box
// reads, apart from settingsNavigation.js so App, which only resolves pane
// names, does not load it before the dialog opens.
import { t } from "../shared/i18n/i18n.js";
import { TOPBAR_ITEMS } from "../app/topbarItems.js";
import { VIEWER_TOOL_ITEMS } from "../app/viewerToolItems.js";
import { TRANSLATE_PARALLEL_MAX } from "../app/prefDefs.js";

// Search names the actual setting, including settings on the second-level
// AI pages. Each entry: the pane, the setting's label (the `data-setting` the
// jump focuses — a Row's label, a Section's or PaneHead's title), the
// section it sits in and its one-line hint (the row's own words, same t()
// key), English synonyms, and — for a setting one level down, inside a
// workspace's Manage page or a row's menu — the `target` that is on the pane
// itself. tests/settingsSearch.test.mjs checks every target against its
// pane's source. An admin-only section of a pane everyone has (Backups ›
// Bucket) is wrapped in `admin()`, a setting shown only under a
// native host with a capability (Handwriting › the pen section, in
// the iPad and Android apps) in `hostOnly(flags, entry)`, a flag or the list
// of flags its row is shown under: the host's own, or those hostCaps
// derives from them. One shown in every app takes `platform`, which every
// host declares (Account & sync › Delete Gamma Cloud account). One only the
// desktop app reads (Reading › PDFs › Add opened PDFs to the library) is
// wrapped in `desktopOnly`.
const e = (pane, label, section, hint, keywords, target) => ({ pane, label, section, hint, keywords, target: target || label });
const admin = (entry) => ({ ...entry, admin: true });
const hostOnly = (flags, entry) => ({ ...entry, host: [].concat(flags) });
const desktopOnly = (entry) => ({ ...entry, desktop: true });
// What people call a proxy setting, shared by every row of the Network proxy pane.
const PROXY_WORDS = "proxy http https socks socks5 vpn clash v2ray network firewall no_proxy bypass internet china";
const entries = [
  e("server", t("Public server URL"), t("Assistant connections"), t("The address assistants use to reach Gamma; HTTPS unless on this computer or this network"), "public address HTTPS HTTP LAN NAS remote proxy OAuth MCP assistant sign-in"),
  e("appearance", t("Theme"), null, t("System, light, dark and five more"), "dark light gamma amber gold sepia solarized gray system colors"),
  e("translation", t("Interface language"), t("Language"), t("Interface text only."), "interface text locale english chinese 中文 语言"),
  e("appearance", t("Dark PDF pages"), t("PDF pages"), t("Light text on a dark page; figures invert too."), "flip invert page colors"),
  e("appearance", t("Color groups"), t("Colors"), t("Click a swatch to change its colour or opacity. Existing highlights and strokes keep their colours."), "palette swatches colours alpha opacity transparency add group delete"),
  e("appearance", t("Text highlights"), t("Colors"), t("The colours the selection bar, a highlight's menu and a highlighter's menu offer."), "palette highlight colours popup swatches group"),
  e("toolbars", t("Text highlighters"), t("Text highlighters"), t("Each is a style and a colour, named if you like. The shown ones, up to four, are buttons on the viewer's strip and in the selection bar; hidden ones are a menu away. Keys 1–9 press them in this order."), "highlighter preset style underline wavy squiggly strikethrough strikeout colour definition question disputed strip key duplicate copy"),
  e("appearance", t("Ink highlights"), t("Colors"), t("A highlighter pen in the handwriting tool strip: the colours its options row offers."), "palette highlighter presets colours tool strip handwriting"),
  e("appearance", t("Ink pens"), t("Colors"), t("A pen in the handwriting tool strip: the colours its options row offers."), "palette pen presets ink colours tool strip handwriting"),
  e("appearance", t("Text boxes"), t("Colors"), t("Typed text boxes on a page; boxes are opaque."), "palette text box colours"),
  e("appearance", t("Interface size"), t("Interface"), t("Text, buttons, icons and switches."), "zoom text buttons controls scale touch"),
  e("appearance", t("Status bar"), t("Interface"), t("Show the latest activity below your tabs."), "notifications messages"),
  e("appearance", t("Inbox notifications"), t("Interface"), t("New inbox items pop up on this device, once each."), "system notifications desktop alerts bell mentions comments replies push"),
  e("appearance", t("Recents thumbnails"), t("Library"), t("Preview the page you last read."), "snapshots library home display cards"),
  e("appearance", t("File labels"), t("Library"), t("Show the folders a file belongs to."), "folders chips library display cards"),
  e("toolbars", t("Toolbars"), null, t("What the top bar and the viewer strip offer, in which order, and the text highlighters"),
    "toolbar toolbars customize customise buttons strip bar layout arrange order show hide"),
  // Top bar and Viewer tools: a row per item of app/topbarItems.js and
  // app/viewerToolItems.js, its label, hint and synonyms
  ...TOPBAR_ITEMS.map((item) => e("toolbars", t(item.label), t("Top bar"), t(item.hint),
    `topbar top bar header toolbar button show hide order reorder arrange drag move${item.places ? " pane split side by side" : ""} ${item.keywords}`)),
  ...VIEWER_TOOL_ITEMS.map((item) => e("toolbars", t(item.label), t("Viewer tools"), t(item.hint),
    `viewer tools strip side toolbar PDF notebook button show hide order reorder arrange drag move ${item.keywords}`)),
  e("diagnostics", t("Suggest tours"), t("Tours"), t("A short tour the first time a feature comes up."), "guide onboarding hints help"),
  e("reading", t("Imported annotations"), t("PDFs"), t("Annotations saved inside a PDF become highlights"), "hide strip keep remove originals highlights PDF"),
  e("reading", t("Page direction"), t("PDFs"), t("Pages one under another, or side by side"), "page direction horizontal vertical scroll scrolling across down side by side layout spread book flip PDF"),
  e("reading", t("Smooth wheel scrolling"), t("PDFs"), t("Glide one mouse-wheel notch at a time instead of jumping"), "smooth scroll scrolling mouse wheel animation glide PDF"),
  e("reading", t("Open-access fallback"), t("PDFs"), t("Fetch a free copy when a publisher blocks the PDF"), "download PDF publisher free library"),
  e("reading", t("Auto-fetch metadata"), t("PDFs"), t("Title, authors and BibTeX on first open"), "title authors BibTeX DOI library"),
  e("reading", t("Save external PDFs"), t("PDFs"), t("Keep a server copy of PDFs opened from a URL"), "download storage offline URL library"),
  desktopOnly(e("reading", t("Add opened PDFs to the library"), t("PDFs"), t("In the desktop app, a PDF opened from this computer goes into the library"),
    "desktop open with file computer disk opened files read in place store library")),
  e("reading", t("Apply a rough match from"), t("Replacing a PDF"), t("How alike a changed passage must read for its highlight to move"),
    "replace new version highlights relocate move rough fuzzy match threshold percent similarity dashed"),
  e("reading", t("Passages shorter than this must match exactly"), t("Replacing a PDF"), t("A shorter passage that changed is not moved"),
    "replace new version highlights relocate exact short passage characters minimum length"),
  e("reading", t("Linked from"), t("Notes"), t("List the pages whose notes link to this one"), "backlinks linked mentions references links notes"),
  // the section's action, on its rule
  e("handwriting", t("Reset presets"), t("Pens & eraser"), t("The strip's pens and highlighters back to the default seven"), "handwriting pen highlighter tool strip presets default restore ink",
    t("Pens & eraser")),
  e("handwriting", t("Eraser returns to the last tool"), t("Pens & eraser"), t("After erasing, return to the tool you had"), "handwriting eraser auto deselect back previous pen ink"),
  e("handwriting", t("Erases"), t("Pens & eraser"), t("Everything, pens only or highlighters only"), "handwriting eraser filter pens highlighters only ink"),
  e("handwriting", t("Draw and hold"), t("Pens & eraser"), t("Hold the pen still to straighten a line or clean up a shape"), "handwriting straight line shape circle rectangle snap recognise hold pen ink"),
  e("handwriting", t("Scribble to erase"), t("Pens & eraser"), t("A dense back-and-forth with the pen erases the ink under it"), "handwriting scratch out scribble erase gesture pen ink"),
  e("handwriting", t("Draws with"), t("This device"), t("Pen only, or pen and finger"), "handwriting pen finger touch stylus ink"),
  e("handwriting", t("Stylus draws on PDFs right away"), t("This device"), t("Without opening the tools first"), "handwriting pen ink stylus select text"),
  e("handwriting", t("Pressure-sensitive strokes"), t("This device"), t("Pen strokes thicken with pressure"), "handwriting pen ink width"),
  e("handwriting", t("Undo with fingers"), t("This device"), t("Two fingers undo, three redo"), "handwriting undo redo two three finger tap double tap gesture touch ink"),
  e("handwriting", t("Hover preview"), t("This device"), t("The footprint over the page shows the pen's style"), "handwriting hover cursor footprint ring brush pencil dashed dotted pen ink"),
  // the pen section (SettingsDialog.jsx PencilSettings), shown only under
  // `pencil`, in Apple's words in the iPad app and a stylus's in any other,
  // each gesture's row where the host forwards that gesture (hostCaps)
  hostOnly(["pencil", "apple", "pencilTap"], e("handwriting", t("Double tap"), t("Apple Pencil"), t("What a double tap on the Pencil does"), "Apple Pencil iPad double tap gesture eraser last previous tool options undo stylus")),
  hostOnly(["pencil", "apple", "pencilSqueeze"], e("handwriting", t("Squeeze"), t("Apple Pencil"), t("What squeezing Apple Pencil Pro does"), "Apple Pencil Pro iPad squeeze gesture hold eraser last previous tool options undo stylus")),
  hostOnly(["pencil", "apple", "haptics"], e("handwriting", t("Haptics"), t("Apple Pencil"), t("A tick in Apple Pencil Pro when a shape snaps, the lasso selects or the tool changes"), "Apple Pencil Pro iPad haptic feedback vibration tick snap")),
  hostOnly(["pencil", "stylus", "pencilTap"], e("handwriting", t("Pen button"), t("Stylus"), t("What a click of the pen's button does"), "stylus pen button side barrel click gesture eraser last previous tool options undo")),
  hostOnly(["pencil", "stylus", "pencilSqueeze"], e("handwriting", t("Second pen button"), t("Stylus"), t("What a click of the pen's second button does"), "stylus pen second button side barrel click gesture eraser last previous tool options undo")),
  hostOnly(["pencil", "stylus", "haptics"], e("handwriting", t("Haptics"), t("Stylus"), t("A tick when a shape snaps, the lasso selects or the tool changes"), "stylus pen haptic feedback vibration tick snap")),
  e("handwriting", t("Handwriting"), null, t("The pen and the eraser, and how this device draws"),
    "handwriting ink pen pencil stylus eraser draw drawing tablet touch"),
  e("reading", t("Lead-in"), t("Recordings"), t("How far before a written moment a tap plays from"), "audio recording replay seconds before playback tap seek"),
  e("reading", t("Transcribe recordings"), t("Recordings"), t("Write the spoken words under a recording when it ends"), "audio transcript speech to text whisper automatic"),
  e("reading", t("On the home page"), t("Search opens as"), t("Full panel: grouped result lists"), "search panel find bar expand results"),
  e("reading", t("On a page"), t("Search opens as"), t("Find bar: match counter and next / previous"), "search panel find bar expand results"),
  e("translation", t("Translation button"), t("Viewer & selection"), t("Off, it moves to the ⋯ menu"), "translation shortcut viewer strip show hide"),
  e("translation", t("Translate into"), t("Viewer & selection"), t("Also in the translate button's menu"), "translation language target"),
  e("translation", t("Translate a selection"), t("Viewer & selection"), t("A button next to the highlight colors"), "translation popup highlight selected text button"),
  e("translation", t("Translate on select"), t("Viewer & selection"), t("Without clicking the button first"), "translation popup selected text automatic"),
  e("translation", t("Translate with"), t("Service"), t("A chat model reads the paper first and keeps its terms; a service is quick and needs no AI"), "translation model engine service AI Google Youdao"),
  e("translation", t("Translation services"), t("Service"), t("Microsoft for free; Google or Youdao with a key"), "translation engine API key Google Cloud Youdao Microsoft"),
  e("translation", t("Keep translations"), t("Service"), t("A paper translated again needs no AI call"), "translation keep store save cache remember restart workspace"),
  e("translation", t("Kept in this workspace"), t("Service"), t("Translated paragraphs and the papers' briefs, with Clear"), "translation kept stored cache clear delete forget size"),
  e("translation", t("Glossary"), null, t("The workspace's terms and how to translate them"), "translation glossary terms terminology dictionary vocabulary rendering consistent words"),
  e("translation", t("Translation effort"), t("Speed"), t("Default asks for the model's lowest level, the fastest"), "translation reasoning thinking speed"),
  e("translation", t("Translation speed"), t("Speed"), t("Only models whose provider offers that tier"), "translation fast mode speed service tier priority flex latency"),
  e("translation", t("Parallel requests"), t("Speed"), t("How many chunks translate at once (1–{max})", { max: TRANSLATE_PARALLEL_MAX }), "translation concurrency speed parallel"),
  e("keyboard", t("Keyboard shortcuts"), null, t("Every command's keys, rebindable"), "keys hotkeys bindings rebind command palette VSCode"),
  e("keyboard", t("Enter makes"), t("Built in"), t("A new note or a new line; Shift+Enter makes the other"), "enter key new note line break notes shift return"),
  e("agents", t("Agents"), null, t("Gamma AI, and Codex and Claude Code on your computers"), "agent codex claude computer local CLI sandbox run commands co-author people"),
  desktopOnly(e("agents", t("Directories"), null, t("Where an agent can work from a chat whose page is in no synced folder."),
    "agent codex claude directory folder offer path working directory cwd")),
  desktopOnly(e("agents", t("Servers this computer answers"), null,
    t("Its chats can then send work to the agents you turned on here, for your account there. No synced folder is needed."),
    "server NAS link remote another add reconnect answer asks allow may answer what the agents ask")),
  e("ai", t("Models for each task"), null, t("Each task can use any connection's model"), "AI model task role per task default favorites"),
  e("ai", t("Chat"), t("Models for each task"), t("The model a new chat starts with"), "default chat model titles structured notes"),
  e("ai", t("Metadata & citations"), t("Models for each task"), t("Used when identifiers do not settle which paper the PDF is"), "metadata model AI extraction identifiers check slide citation"),
  e("ai", t("Translation"), t("Models for each task"), t("A model or a translation service"), "translation model translate with engine service"),
  e("ai", t("Handwriting"), t("Models for each task"), t("Redraw as shapes and Convert to text"), "handwriting ink model convert text shapes redraw"),
  e("ai", t("Dictation"), t("Models for each task"), t("For the chat mic button; needs an OpenAI key"), "dictation model speech voice mic transcription"),
  e("ai", t("Dictation language"), t("Models for each task"), t("Naming the language improves accuracy"), "speech voice mic"),
  e("ai", t("Connections"), null, t("AI providers, keys and ChatGPT sign-in"), "provider credentials API key ChatGPT login service billing subscription"),
  e("ai", t("Check at login"), t("Connection check"), t("Checks the connections your tasks use when Gamma opens"), "connection test credential ping"),
  e("usage", t("Usage"), null, t("Tokens, calls and estimated API spend, per model and task"),
    "token usage statistics consumption cost spend estimate price money dollars bill input output cached chart history reset subscription quota plan remaining"),
  e("usage", t("Shared allowance"), t("Usage"), t("What the server's shared keys still allow you today"), "shared AI tokens daily limit quota used up"),
  e("usage", t("By model"), t("Usage"), t("Calls, tokens and estimated cost of each model, sorted by any column"), "token usage per model connection cost sort filter task"),
  e("usage", t("By task"), t("Usage"), t("Tokens and cost of chat, translation, metadata and the other tasks"), "token usage per kind task chat translation metadata citations handwriting"),
  e("assistant", t("Default reasoning effort"), t("Chat"), t("Each model gets the nearest level it takes"), "chat thinking effort reasoning level"),
  e("assistant", t("Default speed"), t("Chat"), t("Only models whose provider offers that tier"), "chat fast mode speed service tier priority flex latency"),
  e("assistant", t("Clear snapshots on click"), t("Chat"), t("A plain click in the PDF also drops pending snapshots"), "chat images selections"),
  e("assistant", t("Prompts"), null, t("Chat, metadata, citation and agent prompts"), "custom system prompt citation metadata agent"),
  e("assistant", t("Context size"), null, t("How much paper text a chat reads"), "context budget standard larger characters limits pages"),
  e("assistant", t("Single paper"), t("Context size"), t("Read from the open paper for one chat message"), "advanced context budget"),
  e("assistant", t("Metadata extraction"), t("Context size"), t("Read while detecting identifiers and extracting fields"), "advanced context budget"),
  e("assistant", t("Multi-paper total"), t("Context size"), t("Shared evenly by every selected paper"), "advanced context budget"),
  e("assistant", t("Your skills"), null, t("Write or import a SKILL.md of your own"),
    "skill skills slash command SKILL.md template instructions recipe workflow add new import edit delete custom"),
  e("assistant", t("Built-in skills"), null, t("Summarize, review and explain, each on or off"), "skill skills builtin default summarize review explain turn off disable copy slash command"),
  e("tools", t("Tool usage"), null, t("What chats may do, where they search and how far they go"), "agent tools permissions access control online search limits"),
  e("tools", t("Assistant tools"), t("Tools"), t("Let chats read, search and edit your library"), "allow master switch permissions agent access"),
  e("tools", t("Use skills on its own"), t("Tools"), t("Load one of your skills when a request fits it"), "skills slash commands instructions load_skill", t("Tools")),
  e("tools", t("Search online"), t("Tools"), t("Search the web and Crossref, arXiv and OpenAlex, and follow papers' citations"), "web internet research papers references citations", t("Tools")),
  e("tools", t("Fetch documents"), t("Tools"), t("Read a web page, DOI or URL without saving it to your library"), "download web internet page site PDF", t("Tools")),
  e("tools", t("Use journal sign-ins"), t("Tools"), t("Use connected publisher cookies when fetching documents"), "cookies publisher journals session access", t("Tools")),
  e("tools", t("Folder chat"), t("Tools"), t("Home and folder views"), "permissions tools read search edit rename move access"),
  e("tools", t("PDF chat"), t("Tools"), t("Pages with a PDF"), "permissions tools read search edit access"),
  e("tools", t("Notes chat"), t("Tools"), t("Note pages"), "permissions tools read search edit access"),
  e("tools", t("Fetch blocked papers in the background"), t("Tools"), t("Gamma Connector tries in an unfocused tab and sends the PDF back"), "connector extension paywall sign-in captcha download"),
  e("tools", t("Read long papers with a helper"), t("Tools"), t("A second pass reads the document and hands back a cited answer"), "delegate subagent read_paper long document tokens"),
  e("tools", t("Search the web with"), t("Online search"), t("Current facts, documentation, news, and pages the registries miss"), "web internet search engine Brave Tavily SearXNG ChatGPT OpenAI Anthropic Google"),
  e("tools", t("Online search services"), t("Online search"), t("Brave Search, Tavily, SearXNG and an OpenAlex key"), "API key Brave Tavily SearXNG OpenAlex budget"),
  e("tools", t("Tool rounds"), t("Tool limits"), t("AI ↔ tool round-trips per message"), "advanced tool requests limits"),
  e("tools", t("Unlimited rounds"), t("Tool limits"), t("Run until the reply is done or you stop it"), "advanced tool rounds limit unlimited no limit changes continue"),
  e("tools", t("Read window"), t("Tool limits"), t("Document text per read tool call"), "advanced context characters"),
  e("integrations", t("Integrations"), null, t("Access for Codex, Claude Code, DeepSeek Harness, or any MCP assistant: reading, and editing notes where you allow it."), "external assistants Codex Claude Code DeepSeek Harness dsh plugins MCP tokens read-only edit view role permissions revoke connections workspaces access setup command"),
  e("account", t("Account & sync"), null, t("Your account, Gamma Cloud link and settings sync"), "profile account user"),
  e("account", t("Gamma Cloud"), null, t("Publish pages for free and carry your settings to your other servers"), "cloud link unlink sign-in identity plan publish sync set up"),
  e("account", t("Zotero"), null, t("Link a Zotero library to a workspace and keep the two in step"), "zotero library libraries link sync connect disconnect reconnect api key oauth sign-in references citations"),
  e("account", t("Settings sync"), null, t("Carry these settings to your other Gamma servers"), "sync cloud fetch push profile preferences"),
  e("account", t("Publishing"), null, t("Pages you publish to Gamma Cloud"), "sync publish published pages Gamma Cloud share stop conflicts"),
  hostOnly("platform", e("account", t("Delete Gamma Cloud account"), null, t("How to delete it, and what that removes, on gammapdf.com"),
    "remove erase close cancel account cloud data privacy gammapdf.com")),
  e("workspaces", t("Workspaces"), null, t("Personal and shared workspaces, export and import"), "library libraries default new personal shared"),
  e("workspaces", t("Clones and Zotero links"), null, t("Offline copies of workspaces on other Gamma servers, and workspaces kept in step with a Zotero library"), "sync clone mirror offline copy remote workspace origin server pull push detach reattach conflicts zotero library link"),
  e("workspaces", t("Your storage"), t("Storage"), t("all personal workspaces"), "storage quota space used disk"),
  e("workspaces", t("Members"), t("Manage"), t("Invite people, by account or e-mail, and set roles, in a shared workspace's Manage page"), "invite invitation members roles sharing owner editor viewer people email e-mail mail", t("Shared")),
  e("workspaces", t("Export workspace"), t("Data"), t("The Data menu on a workspace's row; Export all for every one"), "export download zip data import merge", t("Personal")),
  e("workspaces", t("Duplicate"), t("Manage"), t("a new personal workspace with a copy of every page"), "duplicate copy workspace", t("Personal")),
  e("workspaces", t("Delete workspace"), t("Manage"), t("In a workspace's Manage page, under Danger zone"), "delete remove leave destroy", t("Personal")),
  e("backups", t("Backups"), null, t("Server-kept snapshots and the tasks that take them."), "snapshot backup"),
  e("backups", t("Periodic backup tasks"), null, t("Hourly, daily, weekly or monthly snapshots"), "automatic scheduled tasks hourly daily weekly monthly cron retention schedule"),
  e("backups", t("Restore a backup"), t("Saved snapshots"), t("A snapshot's Restore menu: replace or merge"), "restore replace merge snapshot roll back download", t("Saved snapshots")),
  admin(e("backups", t("Bucket"), null, t("AWS S3, Cloudflare R2, MinIO or another S3-compatible store, for backup tasks to copy their snapshots to"), "administration offsite off-site backup bucket S3 R2 Cloudflare AWS MinIO cloud remote storage replicate disaster recovery")),
  admin(e("backups", t("Cloud copies"), null, t("What the bucket and Google Drive hold, and the workspaces only the cloud has"), "administration restore cloud bucket drive copies lost disk disaster recovery fetch bring back download browse")),
  admin(e("backups", t("Google Drive"), null, t("A folder in your Google Drive, for backup tasks to copy their snapshots to"), "administration offsite off-site backup google drive gdrive cloud remote storage sign in connect account oauth disaster recovery")),
  e("maintenance", t("Library maintenance"), null, t("Storage, search index and metadata health"), "metadata health text index rebuild papers"),
  e("maintenance", t("Uploaded files"), t("Storage"), t("PDFs and images on the server"), "storage quota uploads space used disk"),
  e("users", t("Users"), null, t("Accounts, storage limits, sign-in, guests and reports"), "administration accounts limits personal workspaces"),
  e("users", t("Change password"), t("Edit"), t("Edit on an account's row sets a new password"), "administration password credentials reset account", t("Users")),
  e("users", t("Invite by e-mail"), null, t("Mail someone a link to join this server with a Gamma Cloud account"), "administration invite invitation email e-mail mail new account join Gamma Cloud register sign up", t("Users")),
  e("users", t("Account server"), t("Sign-in"), t("The Gamma Cloud address people sign in through; empty turns it off"),
    "administration Gamma Cloud sign-in login single sign-on SSO account server issuer client policy unknown cloud accounts provision claim invited join requests"),
  e("users", t("Reports"), null, t("What people reported here, open and resolved"), "administration moderation abuse flag report comment page AI reply resolve"),
  e("server", t("Online services"), null, t("Paper lookups and the AI model catalog this server asks"), "administration offline metadata registries crossref arxiv openalex doi lookup models.dev catalog privacy internet"),
  e("server", t("Work folders"), null, t("The largest file a work folder keeps"), "administration vault synced folder size limit large files keep"),
  e("users", t("Most guests at once"), t("Guests"), t("Past it, a guest login waits for a guest to expire"), "administration guest limit cap maximum accounts demo"),
  admin(e("tools", t("Online search for every account"), null, t("The SearXNG instance and OpenAlex key accounts without their own use"), "administration searxng openalex key server-wide shared search")),
  e("server", t("Zotero sign-in"), null, t("This server's own Zotero app, so people connect Zotero by signing in at zotero.org"), "administration zotero oauth app client key secret callback sign-in connect register"),
  e("server", t("Server"), null, t("Dashboard, updates, the shared AI provider and workspaces, backups and the log"), "administration dashboard version update log"),
  e("users", t("Default quota"), t("Storage defaults"), t("Personal uploads per account; 0 = unlimited"), "administration storage quota limit space upload size"),
  e("server", t("Shared workspaces"), null, t("Workspaces an admin makes for a team"), "administration new shared workspace members"),
  e("server", t("Shared AI provider"), null, t("AI keys every account on this server can use"), "administration API key everyone lab members guests connection models access who selected accounts ChatGPT subscription"),
  e("server", t("Allowance per account"), t("Shared AI provider"), t("Tokens a day on the shared keys; 0 = unlimited"), "administration shared AI tokens daily limit quota budget"),
  e("server", t("Allowance per guest"), t("Shared AI provider"), t("Tokens a day for each guest; 0 = unlimited"), "administration shared AI tokens daily limit quota budget guests"),
  e("server", t("Recognize text in scanned PDFs"), t("Text recognition"), t("The Document menu offers Recognize text for a scan"),
    "administration OCR scan scanned tesseract text recognition picture pages turn off disable"),
  e("server", t("Recognize scans automatically"), t("Text recognition"), t("A PDF uploaded with no text is read from its page pictures"),
    "administration OCR scan scanned automatic tesseract text recognition picture pages upload"),
  e("server", t("Files recognized at once"), t("Text recognition"), t("Each keeps a core busy and about 50 MB of memory while it runs"),
    "administration OCR workers parallel concurrent CPU cores memory text recognition"),
  e("users", t("Guest sign-in"), t("Guests"), t("The login page offers a throwaway account"), "administration guests visitors anonymous turn off disable try it login button"),
  e("users", t("Guest workspaces last"), t("Guests"), t("Then the guest's account and workspace are deleted"), "administration guests temporary expiry hours delete throwaway"),
  e("users", t("Demo mode"), t("Guests"), t("The login page leads with Try the demo"), "administration guests try the demo public login page first-run tour"),
  e("server", t("Check databases"), t("Databases"), t("a quick check of every account and workspace database"), "administration integrity corruption damaged sqlite quick_check health"),
  e("server", t("Off-site copies"), t("Server backups"), t("These stay on this disk; a bucket keeps copies elsewhere"),
    "administration offsite off-site backup bucket S3 R2 remote copy lost disk disaster recovery"),
  // Network proxy: the rows shown only under Manual (and the name lookups,
  // under a proxy) jump to the Proxy row, which always is, and offers Manual.
  e("proxy", t("Network proxy"), null, t("How this server reaches the internet: AI providers, paper lookups and downloads, translation, Zotero, Gamma Cloud and other Gamma servers. Not a library proxy (EZproxy)."),
    `administration ${PROXY_WORDS} outbound connections`),
  e("proxy", t("Proxy"), t("Connection"), t("Use system settings follows the server's environment or this computer's network settings; No proxy connects directly; Manual uses a proxy you set"),
    `administration ${PROXY_WORDS} system environment HTTPS_PROXY ALL_PROXY manual direct`),
  e("proxy", t("Proxy server"), t("Connection"), t("http:// or socks5://, with its port"), `administration ${PROXY_WORDS} address host port`, t("Proxy")),
  e("proxy", t("Sign-in"), t("Connection"), t("Only if the proxy asks for one"), `administration ${PROXY_WORDS} username password credentials authentication`, t("Proxy")),
  e("proxy", t("Don't use the proxy for"), t("Connection"), t("Hosts, domains (*.example.com), IP addresses and ranges (10.0.0.0/8), <local>. This computer always connects directly."),
    `administration ${PROXY_WORDS} exceptions direct local lan intranet`, t("Proxy")),
  e("proxy", t("Let the proxy look up names"), t("Connection"), t("For fake-IP VPNs, or when only the proxy resolves names"),
    `administration ${PROXY_WORDS} dns resolve names fake-ip tun socks5h`, t("Proxy")),
  e("proxy", t("Test the connection"), t("Test"), t("Tries the settings above before you save them"), `administration ${PROXY_WORDS} check try reach`),
  e("diagnostics", t("Debug logging"), t("Tracing"), t("Trace reading-position, restore and sync events"), "diagnostics tracing browser system log"),
  e("diagnostics", t("Privacy policy"), t("Help"), t("What Gamma stores, what it sends and to whom, on gammapdf.com"),
    "privacy data personal information collected shared deletion legal gammapdf.com"),
];
export const SETTINGS_SEARCH = entries;

// A native host's flags as the pen section reads them, its rows and their
// search entries alike: `apple` in the iPad app, `stylus` under any other
// host, and a gesture's own flag where the host forwards that gesture
// (pencilGestures; a host without the list, the iPad app, forwards both).
// Null for no host.
export function hostCaps(host) {
  if (!host) return null;
  const gestures = Array.isArray(host.pencilGestures) ? host.pencilGestures : ["tap", "squeeze"];
  const apple = host.platform === "ipad";
  return { ...host, apple, stylus: !apple, pencilTap: gestures.includes("tap"), pencilSqueeze: gestures.includes("squeeze") };
}

// Every word of the query must appear in the entry's label, synonyms, hint
// or section; entries whose label holds every word come first, then the
// rest, each group in table order. An `admin` entry is offered only while
// the Server pane is, which the dialog shows to admins alone; a `hostOnly`
// one only under a native host (`host`: hostCaps of shared/lib/host.js
// nativeHost()) that has every one of its flags; a `desktopOnly` one only in
// the desktop app (`desktop`: IS_DESKTOP).
export function searchSettings(query, allowedPanes, { host = null, desktop = false } = {}) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const has = (text) => words.every((word) => text.toLocaleLowerCase().includes(word));
  const isAdmin = allowedPanes.includes("server");
  const found = entries.filter((entry) => allowedPanes.includes(entry.pane) && (!entry.admin || isAdmin)
    && (!entry.host || entry.host.every((flag) => !!host?.[flag])) && (!entry.desktop || desktop)
    && has(`${entry.label} ${entry.keywords} ${entry.hint || ""} ${entry.section || ""}`));
  return [...found.filter((entry) => has(entry.label)), ...found.filter((entry) => !has(entry.label))];
}
