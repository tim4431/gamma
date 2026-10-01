// Share this page — or this folder — the popover under the page header's
// link button (the folder view's, for a folder), like the account menu: it
// hangs off its button (App wraps it in a `data-popover="share"` anchor, so
// the topbar's outside-click / Escape rules close it) and is built from the
// settings kit like the workspace Manage dialog. Top to bottom: Link (the
// address and Copy link once a share exists; before, one line saying that
// choosing who can open it makes the link), Who has access (the invite box,
// you, the workspace's members in a shared workspace, the invited people),
// General access, Stop sharing (confirmed inline), then Gamma Cloud and the
// Citation. State is the server's share settings (docs/dev/api.md "Shares"):
// every change saves at once; the link itself only changes on Stop.
// `target` says what is shared — {kind: "page", title} or {kind: "folder",
// name} — and only the words differ: a folder share reaches every page filed
// in the folder, now and later, so its edit wording says so.
//
// Nothing is shared by opening the popover: the first audience tile picked,
// or the first person invited (as Invited only), creates the share with
// that access — never anyone-with-the-link unless that tile is the pick.
//
// Access is pictured, not described: three tiles say who may open the link
// (Anyone / Signed in / Invited only, the same glyphs the read-only view's
// badge uses) and one View / Edit toggle says what they may do (disabled
// under Invited only, where it grants nothing); the one-line summary under
// them is the only prose. Invited people carry their own View / Edit toggle
// on top, whatever the tiles say.
//
// Gamma Cloud (PublishSection, docs/dev/mirror.md "Publishing"): on a server
// with cloud sign-in, a page can also be published to the share host, so its
// link works while this computer is off. Not published: one sentence and a
// Publish button (the hint counting the plan's published pages when it caps
// them; the cap's refusal links to the account page), or the reason it
// cannot be (with the link action when the account has no Gamma Cloud
// identity). Published: the cloud link — the page's pretty address on its
// page host, the token link in the hover title — with Copy and Unpublish
// (confirmed inline), the publication's state line (the sync pill's reading,
// mirrorState) with a Sync-now button, and the cloud share's access as the
// same tiles + View / Edit toggle. App owns the data
// (GET/POST/DELETE /api/pages/{id}/publish) and polls it while open.
import React from "react";
import { AccountPicker, Empty, IconChoices, Row, Section, Segmented } from "../settings/SettingsKit";
import { useAccounts } from "../settings/SettingsWorkspace";
import { mirrorState } from "../collaboration/MirrorPopover";
import { T, t, tn } from "../shared/i18n/i18n.js";
import {
  AlertCircleIcon, CheckIcon, CloudIcon, CloudOffIcon, CloudUploadIcon, ExternalLinkIcon, EyeIcon, GlobeIcon,
  LinkIcon, PenIcon, RefreshIcon, ShieldIcon, Trash2Icon, UserIcon, UsersIcon,
  XIcon,
} from "../shared/ui/Icons";

const ROLE_SEGMENTS = {
  page: [
    ["view", t("View"), EyeIcon, t("Can read the page")],
    ["edit", t("Edit"), PenIcon, t("Can edit this page's notes and highlights — never other pages or the page's settings")],
  ],
  folder: [
    ["view", t("View"), EyeIcon, t("Can read every page in the folder")],
    ["edit", t("Edit"), PenIcon, t("Can edit the notes and highlights of every page in this folder, now and later — never other pages or any page's settings")],
  ],
};

const AUDIENCE_TILES = [
  { value: "anyone", label: T("Anyone"), hint: T("with the link"), Icon: GlobeIcon },
  { value: "users", label: T("Signed in"), hint: T("any account here"), Icon: UsersIcon },
  { value: "list", label: T("Invited only"), hint: T("the people above"), Icon: ShieldIcon },
];

// The cloud share's tiles: the same three, their hints in the share host's terms.
const CLOUD_AUDIENCE_TILES = [
  AUDIENCE_TILES[0],
  { ...AUDIENCE_TILES[1], hint: T("any Gamma Cloud account") },
  { ...AUDIENCE_TILES[2], hint: T("people invited there") },
];

// The refusal publish.py answers for an account without a Gamma Cloud identity,
// compared against the server's text, so it stays untranslated.
const PUBLISH_SIGN_IN = T("Sign in with Gamma Cloud to publish.");

// A tile pick as a settings patch: opening a link up to everyone never
// silently makes it editable.
const audiencePatch = (audience) => (audience === "anyone" ? { audience, role: "view" } : { audience });

// The one sentence that says what the tiles + toggle add up to. The cloud
// share (`cloud`) has no people list here: its invitations live on the
// share host.
function accessSummary(settings, kind, cloud = false) {
  const invited = (settings.users || []).length > 0;
  const who = settings.audience === "anyone" ? t("Anyone with the link") : settings.audience === "users" ? t("Anyone signed in") : null;
  if (!who && cloud) return invited ? t("Only the people invited there can open it.") : t("Nobody can open it until you invite someone.");
  if (!who) {
    return kind === "folder"
      ? t("Only the people above can open this folder's pages; the link does nothing for anyone else.")
      : t("Only the people above can open this page; the link does nothing for anyone else.");
  }
  const verb = settings.role === "edit" ? t("edit") : t("read");
  const access = invited ? t("; invited people keep their own access") : "";
  return kind === "folder"
    ? t("{who} can {verb} every page in this folder{access}.", { who, verb, access })
    : t("{who} can {verb} this page{access}.", { who, verb, access });
}

// That sentence under the tiles, amber when the link is editable without
// sign-in; `saving` stands in while a change is on its way.
function AccessSummary({ settings, kind, cloud = false, saving = false }) {
  const openEdit = settings.audience === "anyone" && settings.role === "edit";
  return (
    <div className={`settingsPaneHint shareSummary ${openEdit ? "shareWarn" : ""}`}>
      {openEdit ? <AlertCircleIcon size={14} /> : null}
      <span>
        {saving ? t("Saving…") : accessSummary(settings, kind, cloud)}
        {openEdit && !saving ? t(" No sign-in needed; edits are recorded under a name they choose.") : ""}
      </span>
    </div>
  );
}

function CopyLinkButton({ url, copied, onCopy, primary = false }) {
  return (
    <button type="button" className={`uiBtn sm ${copied ? "on" : primary ? "primary" : ""}`} onClick={onCopy} title={url}>
      {copied ? <CheckIcon size={14} /> : <LinkIcon size={14} />}
      {copied ? t("Copied") : t("Copy link")}
    </button>
  );
}

// Invite, on top of the people: an account from the directory, invited to
// view (the toggle on their row changes it), additive to general access.
// Before a link exists the first invitation creates the share, Invited only
// (App's inviteShareUser). Enter on a picked name invites; the box empties
// for the next one.
function ShareInvite({ exclude, onInvite }) {
  const accounts = useAccounts();
  const [username, setUsername] = React.useState("");
  const [round, setRound] = React.useState(0); // remounts the picker empty after each invitation
  const [busy, setBusy] = React.useState(false);
  async function submit() {
    if (!username || busy) return;
    setBusy(true);
    const ok = await onInvite(username, "view");
    setBusy(false);
    if (ok !== false) { setUsername(""); setRound((n) => n + 1); }
  }
  return (
    <div
      className="shareInvite"
      // the picker's own Enter picks the first match; the next Enter invites
      onKeyDown={(event) => { if (event.key === "Enter" && !event.defaultPrevented && username) { event.preventDefault(); submit(); } }}
    >
      <AccountPicker
        key={round} accounts={accounts} exclude={exclude} value={username} onChange={setUsername}
        placeholder={t("Invite people by name…")} autoFocus={round > 0} compact
      />
      <button type="button" className="uiBtn sm primary" disabled={!username || busy} onClick={submit}>{t("Invite")}</button>
    </div>
  );
}

// Stop sharing, the popover's last word on the link: a labelled danger
// button that asks first, saying who loses access.
function StopSharing({ invited, onStop }) {
  const [confirming, setConfirming] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  async function stop() {
    setBusy(true);
    await onStop();
    setBusy(false);
    setConfirming(false);
  }
  return (
    <div className="shareStop" data-guide="share.stop">
      {confirming ? (
        <div className="mirrorConfirm">
          <AlertCircleIcon size={14} />
          <span>
            {invited
              ? tn("Stop sharing? The link stops working and the {n} invited person loses access.",
                "Stop sharing? The link stops working and the {n} invited people lose access.", invited)
              : t("Stop sharing? The link stops working for everyone.")}
          </span>
          <span className="mirrorConfirmBtns">
            <button type="button" className="uiBtn sm primary danger" disabled={busy} onClick={stop}>{t("Stop sharing")}</button>
            <button type="button" className="uiBtn sm" disabled={busy} onClick={() => setConfirming(false)}>{t("Cancel")}</button>
          </span>
        </div>
      ) : (
        <button type="button" className="uiBtn sm danger" onClick={() => setConfirming(true)}
          title={t("The link stops working; sharing again later makes a new link.")}>
          <Trash2Icon size={14} />{t("Stop sharing")}
        </button>
      )}
    </div>
  );
}

// One row of who has access: you, the workspace's members, an invited account.
function PersonRow({ name, tag, sub, icon: Icon, active, children }) {
  return (
    <div className="aiProvRow">
      <span className={`aiProvAvatar ${active ? "active" : ""}`}><Icon size={16} /></span>
      <span className="aiProvMeta">
        <span className="aiProvName">
          {name}
          {tag ? <span className="uiTag">{tag}</span> : null}
        </span>
        <span className="aiProvDesc">{sub}</span>
      </span>
      <span className="aiProvActions">{children}</span>
    </div>
  );
}

// Gamma Cloud: `state` is GET /api/pages/{id}/publish (null while loading),
// `busy` the action running ("publish" | "update" | "unpublish" | "sync" |
// ""), `error` the last refusal's detail (or {message, limit} when the plan's
// cap refused it), `copied` / `onCopy` the cloud link's copy, `canEdit` false
// for a workspace viewer (nothing to press), `accountUrl` the Gamma Cloud
// account page (the Settings Account row's "Open account").
function PublishSection({ state, busy, error, copied, onCopy, canEdit, onPublish, onUnpublish, onSync, onLink, accountUrl }) {
  const [confirming, setConfirming] = React.useState(false);
  const published = !!state?.published;
  React.useEffect(() => { if (!published) setConfirming(false); }, [published]);
  const share = state?.share;
  const mirror = state?.mirror;
  const st = mirror ? mirrorState(mirror, { busy: busy === "sync" }) : null;
  const running = busy === "sync" || !!mirror?.status?.running;
  const spinning = (what) => (busy === what ? <span className="mirrorSpin"><RefreshIcon size={14} /></span> : null);
  const capped = !!error?.limit;
  // a published page whose publication cannot run (detached, the identity gone) says why
  const problem = (capped ? error.message : error) || state?.error || (published && !state?.can_publish ? state.reason : "") || "";
  const errorLine = problem ? (
    <>
      <div className="settingsPaneHint aiKeysError" role="alert">{problem}</div>
      {capped && accountUrl ? (
        <div className="publishAccount">
          <a className="uiBtn sm" href={accountUrl} target="_blank" rel="noopener"
            title={t("Your Gamma Cloud account: plan, devices, sign-in methods")}>
            <ExternalLinkIcon size={14} />{t("Open account")}
          </a>
        </div>
      ) : null}
    </>
  ) : null;
  // "3 of 5 pages published" where the plan caps them (the refusal's count is the freshest)
  const limit = (capped ? error.limit : null) || state?.limit;
  const counted = limit && limit.max != null ? t("{used} of {max} pages published", { used: limit.used, max: limit.max }) : "";
  const link = state?.public_url || state?.url || "";

  if (!state) {
    return (
      <Section title={t("Gamma Cloud")}>
        <Row icon={CloudIcon} label={t("Publish")} hint={t("Loading…")} />
      </Section>
    );
  }
  if (!published) {
    return (
      <Section title={t("Gamma Cloud")}>
        <Row icon={CloudIcon} label={t("Publish")} className="publishRow"
          hint={!state.can_publish ? state.reason : counted || t("Keep this page reachable while this computer is off.")}
          title={t("Keep this page reachable while this computer is off: publishing copies it to the Gamma Cloud share host and shares it there; edits keep syncing both ways.")}>
          {state.can_publish && canEdit ? (
            <button type="button" className="uiBtn sm primary" disabled={!!busy} onClick={() => onPublish()}>
              {spinning("publish") || <CloudUploadIcon size={14} />}{t("Publish")}
            </button>
          ) : !state.can_publish && state.reason === PUBLISH_SIGN_IN ? (
            <button type="button" className="uiBtn sm" onClick={onLink}>
              <CloudIcon size={14} />{t("Link Gamma Cloud account")}
            </button>
          ) : null}
        </Row>
        {errorLine}
      </Section>
    );
  }
  return (
    <Section
      title={t("Gamma Cloud")}
      action={share && share.audience !== "list" && canEdit ? (
        <Segmented
          value={share.role} options={ROLE_SEGMENTS.page} disabled={!!busy}
          onChange={(role) => { if (role !== share.role) onPublish({ role }); }}
        />
      ) : null}
    >
      <Row icon={CloudIcon} label={t("Cloud link")} hint={link || t("no link yet: the share on the share host was not made")}
        title={link && link !== state.url ? t("{link}\nAlso works: {url}", { link: link, url: state.url }) : link}>
        <span className="shareLinkBtns">
          {link ? (
            <CopyLinkButton url={link} copied={copied} onCopy={onCopy} />
          ) : canEdit && state.can_publish ? (
            // publishing failed after the page reached the share host: the same call finishes the job
            <button type="button" className="uiBtn sm primary" disabled={!!busy} onClick={() => onPublish()}
              title={t("The page is on the share host but its share was not made; publishing again makes the link.")}>
              {spinning("publish") || <CloudUploadIcon size={14} />}{t("Publish again")}
            </button>
          ) : null}
          {canEdit ? (
            <button type="button" className={`uiBtn sm iconSq danger ${confirming ? "on" : ""}`} disabled={!!busy}
              onClick={() => setConfirming((v) => !v)} aria-label={t("Unpublish")}
              title={t("Unpublish: the cloud link stops working and the copy on Gamma Cloud is deleted; this page stays here.")}>
              {spinning("unpublish") || <CloudOffIcon size={16} />}
            </button>
          ) : null}
        </span>
      </Row>
      {confirming ? (
        <div className="mirrorConfirm">
          <AlertCircleIcon size={14} />
          <span>{t("Unpublish? The cloud link stops working and the copy there is deleted; this page stays here.")}</span>
          <span className="mirrorConfirmBtns">
            <button type="button" className="uiBtn sm primary danger" disabled={!!busy}
              onClick={async () => { await onUnpublish(); setConfirming(false); }}>{t("Unpublish")}</button>
            <button type="button" className="uiBtn sm" disabled={!!busy} onClick={() => setConfirming(false)}>{t("Cancel")}</button>
          </span>
        </div>
      ) : null}
      {st ? (
        <div className={`mirrorState ${st.tone} publishState`} data-state={st.state} title={st.title}>
          <st.Icon size={14} />
          <div className="mirrorStateBody">
            <div className="mirrorStateLine">
              <span>{st.text}</span>
              <button type="button" className={`iconBtn sm ${running ? "mirrorSpin" : ""}`} disabled={running || !!busy || !!mirror?.detached}
                onClick={onSync} aria-label={t("Sync now")}
                title={running ? t("A round is running") : mirror?.detached ? t("Detached — reattach in the sync settings") : t("Sync now")}>
                <RefreshIcon size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {share ? (
        <>
          <IconChoices
            label={t("Who can open the cloud link")} value={share.audience} options={CLOUD_AUDIENCE_TILES}
            onChange={(audience) => {
              if (!canEdit || busy || audience === share.audience) return;
              onPublish(audiencePatch(audience));
            }}
          />
          <AccessSummary settings={share} kind="page" cloud saving={busy === "update"} />
        </>
      ) : null}
      {errorLine}
    </Section>
  );
}

// Props: target ({kind: "page", title} or {kind: "folder", name}; a page
// when omitted), settings (null while loading; {token: null} when
// unshared), error (the last failed save, e.g. an unknown username), me /
// meIsGuest (your account; a guest can't search the account directory, so
// gets no invite box), workspace (the open workspace, {name, personal,
// members, access}: a shared one's members already open every page),
// shareUrl, copied / onCopy, and one callback per action — onCreate takes
// the new share's settings ({audience, role}). `citation` is the page's
// citation section (App owns it), shown when the page has metadata.
// `publish` is the Gamma Cloud section's props (PublishSection), or null
// where the server offers no publishing — App passes neither for a folder.
export function SharePopover({
  target, settings, error, me, meIsGuest, workspace, shareUrl, copied, onCopy,
  onCreate, onUpdate, onInvite, onSetRole, onRemove, onStop, onClose, citation, publish,
}) {
  const users = settings?.users || [];
  const shared = !!settings?.token;
  const kind = target?.kind === "folder" ? "folder" : "page";
  const title = kind === "folder"
    ? t("Share folder “{name}”", { name: target?.name || "" })
    : target?.title ? t("Share “{title}”", { title: target.title }) : t("Share this page");
  // a shared workspace's members open every page with their workspace role;
  // a public one's, anyone signed in here too
  const members = workspace && !workspace.personal ? workspace.members || 0 : 0;

  // a tile pick: the first one creates the share with that audience
  function pickAudience(audience) {
    if (!shared) onCreate(audiencePatch(audience));
    else if (audience !== settings.audience) onUpdate(audiencePatch(audience));
  }

  return (
    <div className="popover sharePopover" role="dialog" aria-label={title}>
      <div className="sharePopoverHead">
        <span className="popoverTitle" title={title}>{title}</span>
        <button type="button" className="uiClose" onClick={onClose} aria-label={t("Close")} title={t("Close")}><XIcon size={14} /></button>
      </div>
      <div className="settingsForm">
        {settings === null ? <Empty icon={LinkIcon}>{t("Loading…")}</Empty> : null}
        {settings ? (
          <>
            <Section title={t("Link")} guide={shared ? "share.link" : undefined}>
              {shared ? (
                <Row icon={LinkIcon} label={t("Share link")} hint={shareUrl} title={shareUrl}>
                  <CopyLinkButton url={shareUrl} copied={copied} onCopy={onCopy} primary />
                </Row>
              ) : (
                <Row icon={LinkIcon} label={t("Share link")} className="shareNoLink"
                  hint={kind === "folder"
                    ? t("Choosing who can open this folder creates its link.")
                    : t("Choosing who can open this page creates its link.")}
                  title={kind === "folder"
                    ? t("A link lets people open every page filed in this folder, including pages you file here later — read-only or editable, for anyone or only for accounts you name.")
                    : t("A link lets people open this page — read-only or editable, for anyone or only for accounts you name.")} />
              )}
            </Section>
            <Section title={t("Who has access")} guide="share.people">
              {!meIsGuest ? <ShareInvite exclude={[me, ...users.map((u) => u.name)]} onInvite={onInvite} /> : null}
              <PersonRow name={me} tag={t("you")} sub={t("Owner")} icon={meIsGuest ? UserIcon : ShieldIcon} active />
              {members ? (
                <PersonRow
                  name={t("{name} members", { name: workspace.name })} tag={t("workspace")} icon={UsersIcon}
                  sub={workspace.access === "public"
                    ? tn("{n} member · anyone signed in here can read it too", "{n} members · anyone signed in here can read it too", members)
                    : tn("{n} person · opens it with their workspace role", "{n} people · open it with their workspace role", members)}
                />
              ) : null}
              {users.map((u) => (
                <PersonRow key={u.name} name={u.name} sub={t("Invited")} icon={UserIcon}>
                  <Segmented
                    value={u.role} options={ROLE_SEGMENTS[kind]}
                    onChange={(role) => { if (role !== u.role) onSetRole(u.name, role); }}
                  />
                  <button
                    type="button" className="uiBtn sm iconSq"
                    title={t("Remove {name}", { name: u.name })} aria-label={t("Remove {name}", { name: u.name })}
                    onClick={() => onRemove(u.name)}
                  >
                    <Trash2Icon size={16} />
                  </button>
                </PersonRow>
              ))}
              {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
            </Section>
            <Section
              title={t("General access")}
              guide="share.access"
              action={
                <Segmented
                  value={shared ? settings.role : null} options={ROLE_SEGMENTS[kind]}
                  disabled={!shared || settings.audience === "list"}
                  onChange={(role) => { if (shared && role !== settings.role) onUpdate({ role }); }}
                />
              }
            >
              <IconChoices
                label={t("Who can open the link")} value={shared ? settings.audience : null} options={AUDIENCE_TILES}
                onChange={pickAudience}
              />
              {shared ? <AccessSummary settings={settings} kind={kind} /> : null}
            </Section>
            {shared ? <StopSharing invited={users.length} onStop={onStop} /> : null}
          </>
        ) : null}
        {settings && publish ? <PublishSection {...publish} /> : null}
        {settings ? citation : null}
      </div>
    </div>
  );
}
