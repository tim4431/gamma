// Share this page — the popover under the page header's link button, like
// the account menu: it hangs off its button (App wraps it in a
// `data-popover="share"` anchor, so the topbar's outside-click / Escape
// rules close it) and is built from the settings kit like the workspace
// Manage dialog: Link (Copy link, Stop sharing), Access, People, Citation.
// State is the server's share settings (docs/dev/api.md "Shares"): every
// change saves at once; the link itself only changes on Stop.
//
// Access is pictured, not described: three tiles say who may open the link
// (Anyone / Signed in / Invited only, the same glyphs the read-only view's
// badge uses) and one View / Edit toggle says what they may do; the one-line
// summary under them is the only prose. Invited people carry their own
// View / Edit toggle on top, whatever the tiles say.
//
// Gamma Cloud (PublishSection, docs/dev/mirror.md "Publishing"): on a server
// with cloud sign-in, a page can also be published to the share host, so its
// link works while this computer is off. Not published: one sentence and a
// Publish button, or the reason it cannot be (with the link action when the
// account has no Gamma Cloud identity). Published: the cloud link with Copy
// and Unpublish (confirmed inline), the publication's state line (the sync
// pill's reading, mirrorState) with a Sync-now button, and the cloud share's
// access as the same tiles + View / Edit toggle. App owns the data
// (GET/POST/DELETE /api/pages/{id}/publish) and polls it while open.
import React from "react";
import { MenuSelect } from "../shared/ui/Menus";
import { AccountPicker, Empty, IconChoices, Row, Section, Segmented } from "../settings/SettingsKit";
import { useAccounts } from "../settings/SettingsWorkspace";
import { mirrorState } from "../collaboration/MirrorPopover";
import {
  AlertCircleIcon, CheckIcon, CloudIcon, CloudOffIcon, CloudUploadIcon, CopyIcon, EyeIcon, GlobeIcon, LinkIcon,
  PenIcon, PlusIcon, RefreshIcon, ShieldIcon, Trash2Icon, UserIcon, UsersIcon,
} from "../shared/ui/Icons";

const SHARE_ROLE_OPTIONS = [["view", "Can view"], ["edit", "Can edit"]];
const ROLE_SEGMENTS = [
  ["view", "View", EyeIcon, "Can read the page"],
  ["edit", "Edit", PenIcon, "Can edit this page's notes and highlights — never other pages or the page's settings"],
];

const AUDIENCE_TILES = [
  { value: "anyone", label: "Anyone", hint: "with the link", Icon: GlobeIcon },
  { value: "users", label: "Signed in", hint: "any account here", Icon: UsersIcon },
  { value: "list", label: "Invited only", hint: "the people below", Icon: ShieldIcon },
];

// The cloud share's tiles: the same three, their hints in the share host's terms.
const CLOUD_AUDIENCE_TILES = [
  AUDIENCE_TILES[0],
  { ...AUDIENCE_TILES[1], hint: "any Gamma Cloud account" },
  { ...AUDIENCE_TILES[2], hint: "people invited there" },
];

// The refusal publish.py answers for an account without a Gamma Cloud identity.
export const PUBLISH_SIGN_IN = "Sign in with Gamma Cloud to publish.";

// The one sentence that says what the tiles + toggle add up to.
function accessSummary(settings, invited) {
  const who = settings.audience === "anyone" ? "Anyone with the link"
    : settings.audience === "users" ? "Anyone signed in"
      : null;
  if (!who) return invited ? "Only the people below can open it." : "Nobody can open it until you invite someone.";
  const verb = settings.role === "edit" ? "edit" : "read";
  return `${who} can ${verb} this page${invited ? "; invited people keep their own access" : ""}.`;
}

// Invite, inline under the people list (a popover can't host a modal): an
// account from the directory plus its own role, additive to general access.
function ShareInviteForm({ exclude, error, onSubmit, onCancel }) {
  const accounts = useAccounts();
  const [username, setUsername] = React.useState("");
  const [role, setRole] = React.useState("view");
  return (
    <div className="shareInvite">
      <AccountPicker accounts={accounts} exclude={exclude} value={username} onChange={setUsername} autoFocus compact />
      <div className="shareInviteRow">
        <MenuSelect value={role} label="Access" options={SHARE_ROLE_OPTIONS} onChange={setRole} />
        <span className="shareInviteBtns">
          <button type="button" className="uiBtn sm" onClick={onCancel}>Cancel</button>
          <button type="button" className="uiBtn sm primary" disabled={!username} onClick={() => onSubmit(username, role)}>Invite</button>
        </span>
      </div>
      {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
    </div>
  );
}

// One person on the page: the owner (you) or an invited account.
function PersonRow({ name, self, sub, icon: Icon, active, children }) {
  return (
    <div className="aiProvRow">
      <span className={`aiProvAvatar ${active ? "active" : ""}`}><Icon size={15} /></span>
      <span className="aiProvMeta">
        <span className="aiProvName">
          {name}
          {self ? <span className="uiTag">you</span> : null}
        </span>
        <span className="aiProvDesc">{sub}</span>
      </span>
      <span className="aiProvActions">{children}</span>
    </div>
  );
}

// A copyable text box: the rendered text (or a scrolling <pre>) with the
// copy button pinned top-right — the same for the slide citation and BibTeX.
export function CopyBox({ children, copied, onCopy, title, label }) {
  return (
    <div className="copyBox">
      <div className="copyBoxBody">{children}</div>
      <button type="button" className={`uiBtn sm iconSq copyBoxBtn ${copied ? "on" : ""}`} onClick={onCopy} title={title} aria-label={label}>
        {copied ? <CheckIcon size={13} /> : <CopyIcon size={13} />}
      </button>
    </div>
  );
}

// Gamma Cloud: `state` is GET /api/pages/{id}/publish (null while loading),
// `busy` the action running ("publish" | "update" | "unpublish" | "sync" |
// ""), `error` the last refusal's detail, `copied` / `onCopy` the cloud
// link's copy, `canEdit` false for a workspace viewer (nothing to press).
function PublishSection({ state, busy, error, copied, onCopy, canEdit, onPublish, onUnpublish, onSync, onLink }) {
  const [confirming, setConfirming] = React.useState(false);
  const published = !!state?.published;
  React.useEffect(() => { if (!published) setConfirming(false); }, [published]);
  const share = state?.share;
  const mirror = state?.mirror;
  const st = mirror ? mirrorState(mirror, { busy: busy === "sync" }) : null;
  const running = busy === "sync" || !!mirror?.status?.running;
  const spinning = (what) => (busy === what ? <span className="mirrorSpin"><RefreshIcon size={13} /></span> : null);
  const openEdit = share && share.audience === "anyone" && share.role === "edit";
  const problem = error || state?.error || "";
  const errorLine = problem ? <div className="settingsPaneHint aiKeysError" role="alert">{problem}</div> : null;

  if (!state) {
    return (
      <Section title="Gamma Cloud">
        <Row icon={CloudIcon} label="Publish" hint="Loading…" />
      </Section>
    );
  }
  if (!published) {
    return (
      <Section title="Gamma Cloud">
        <Row icon={CloudIcon} label="Publish"
          hint={state.can_publish ? "Keep this page reachable while this computer is off." : state.reason}
          title="Publishing copies this page to the Gamma Cloud share host and shares it there; edits keep syncing both ways.">
          {state.can_publish && canEdit ? (
            <button type="button" className="uiBtn sm primary" disabled={!!busy} onClick={() => onPublish()}>
              {spinning("publish") || <CloudUploadIcon size={13} />}Publish
            </button>
          ) : !state.can_publish && state.reason === PUBLISH_SIGN_IN ? (
            <button type="button" className="uiBtn sm" onClick={onLink}>
              <CloudIcon size={13} />Link Gamma Cloud account
            </button>
          ) : null}
        </Row>
        {errorLine}
      </Section>
    );
  }
  return (
    <Section
      title="Gamma Cloud"
      action={share && share.audience !== "list" && canEdit ? (
        <Segmented
          value={share.role} options={ROLE_SEGMENTS} disabled={!!busy}
          onChange={(role) => { if (role !== share.role) onPublish({ role }); }}
        />
      ) : null}
    >
      <Row icon={CloudIcon} label="Cloud link" hint={state.url || "no link on the share host"} title={state.url}>
        <span className="shareLinkBtns">
          {state.url ? (
            <button type="button" className={`uiBtn sm ${copied ? "on" : ""}`} onClick={onCopy} title={state.url}>
              {copied ? <CheckIcon size={13} /> : <LinkIcon size={13} />}
              {copied ? "Copied" : "Copy link"}
            </button>
          ) : null}
          {canEdit ? (
            <button type="button" className={`uiBtn sm iconSq danger ${confirming ? "on" : ""}`} disabled={!!busy}
              onClick={() => setConfirming((v) => !v)} aria-label="Unpublish"
              title="Unpublish: the cloud link stops working and the copy on Gamma Cloud is deleted; this page stays here.">
              {spinning("unpublish") || <CloudOffIcon size={13} />}
            </button>
          ) : null}
        </span>
      </Row>
      {confirming ? (
        <div className="mirrorConfirm">
          <AlertCircleIcon size={14} />
          <span>Unpublish? The cloud link stops working and the copy there is deleted; this page stays here.</span>
          <span className="mirrorConfirmBtns">
            <button type="button" className="uiBtn sm danger dangerBtn" disabled={!!busy}
              onClick={async () => { await onUnpublish(); setConfirming(false); }}>Unpublish</button>
            <button type="button" className="uiBtn sm" disabled={!!busy} onClick={() => setConfirming(false)}>Cancel</button>
          </span>
        </div>
      ) : null}
      {st ? (
        <div className={`mirrorState ${st.tone} publishState`} data-state={st.state} title={st.title}>
          <st.Icon size={14} />
          <div className="mirrorStateBody">
            <div className="mirrorStateLine">
              <span>{st.text}</span>
              <button type="button" className={`iconBtn sm ${running ? "mirrorSpin" : ""}`} disabled={running || !!busy}
                onClick={onSync} aria-label="Sync now" title={running ? "A round is running" : "Sync now"}>
                <RefreshIcon size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {share ? (
        <>
          <IconChoices
            label="Who can open the cloud link" value={share.audience} options={CLOUD_AUDIENCE_TILES}
            onChange={(audience) => {
              if (!canEdit || busy || audience === share.audience) return;
              onPublish(audience === "anyone" ? { audience, role: "view" } : { audience });
            }}
          />
          <div className={`settingsPaneHint shareSummary ${openEdit ? "shareWarn" : ""}`}>
            {openEdit ? <AlertCircleIcon size={13} /> : null}
            <span>
              {busy === "update" ? "Saving…" : accessSummary(share, (share.users || []).length > 0)}
              {openEdit && busy !== "update" ? " No sign-in needed; edits are recorded under a name they choose." : ""}
            </span>
          </div>
        </>
      ) : null}
      {errorLine}
    </Section>
  );
}

// Props: settings (null while loading; {token: null} when unshared), error
// (the last failed save, e.g. an unknown username), me / meIsGuest (the
// owner's account), shareUrl, copied / onCopy, and one callback per action.
// `citation` is the page's citation section (App owns it), shown when the
// page has metadata. `publish` is the Gamma Cloud section's props
// (PublishSection), or null where the server offers no publishing.
export function SharePopover({
  settings, error, me, meIsGuest, shareUrl, copied, onCopy,
  onCreate, onUpdate, onInvite, onSetRole, onRemove, onStop, onClose, citation, publish,
}) {
  const [inviting, setInviting] = React.useState(false);
  const users = settings?.users || [];
  const shared = !!settings?.token;
  const openEdit = shared && settings.audience === "anyone" && settings.role === "edit";

  async function invite(name, role) {
    const ok = await onInvite(name, role);
    if (ok !== false) setInviting(false);
  }

  return (
    <div className="popover sharePopover" role="dialog" aria-label="Share this page">
      <div className="sharePopoverHead">
        <span className="popoverTitle">Share this page</span>
        <button type="button" className="uiClose" onClick={onClose} aria-label="Close" title="Close">×</button>
      </div>
      <div className="settingsForm">
        {settings === null ? <Empty icon={LinkIcon}>Loading…</Empty> : null}
        {settings && !shared ? (
          <Section title="Link">
            <Row icon={LinkIcon} label="Share link" hint="not shared yet"
              title="A link lets people open this page — read-only or editable, for anyone or only for accounts you name.">
              <button type="button" className="uiBtn sm primary" onClick={onCreate}>
                <LinkIcon size={13} />Create link
              </button>
            </Row>
          </Section>
        ) : null}
        {shared ? (
          <>
            <Section title="Link">
              <Row icon={LinkIcon} label="Share link" hint={shareUrl} title={shareUrl}>
                <span className="shareLinkBtns">
                  <button type="button" className={`uiBtn sm ${copied ? "on" : ""}`} onClick={onCopy} title={shareUrl}>
                    {copied ? <CheckIcon size={13} /> : <LinkIcon size={13} />}
                    {copied ? "Copied" : "Copy link"}
                  </button>
                  <button type="button" className="uiBtn sm iconSq danger" onClick={onStop}
                    aria-label="Stop sharing"
                    title="Stop sharing — the link stops working; sharing again later makes a new link with default settings.">
                    <Trash2Icon size={13} />
                  </button>
                </span>
              </Row>
            </Section>
            <Section
              title="Access"
              action={settings.audience !== "list" ? (
                <Segmented
                  value={settings.role} options={ROLE_SEGMENTS}
                  onChange={(role) => { if (role !== settings.role) onUpdate({ role }); }}
                />
              ) : null}
            >
              <IconChoices
                label="Who can open the link" value={settings.audience} options={AUDIENCE_TILES}
                onChange={(audience) => {
                  if (audience === settings.audience) return;
                  // Opening a link up to everyone never silently makes it editable.
                  onUpdate(audience === "anyone" ? { audience, role: "view" } : { audience });
                }}
              />
              <div className={`settingsPaneHint shareSummary ${openEdit ? "shareWarn" : ""}`}>
                {openEdit ? <AlertCircleIcon size={13} /> : null}
                <span>
                  {accessSummary(settings, users.length > 0)}
                  {openEdit ? " No sign-in needed; edits are recorded under a name they choose." : ""}
                </span>
              </div>
            </Section>
            <Section
              title="People"
              action={
                <button type="button" className={`uiBtn sm ${inviting ? "on" : ""}`} onClick={() => setInviting((v) => !v)}>
                  <PlusIcon size={13} /> Invite
                </button>
              }
            >
              <PersonRow name={me} self sub="Owner" icon={meIsGuest ? UserIcon : ShieldIcon} active />
              {users.map((u) => (
                <PersonRow key={u.name} name={u.name} sub="Invited" icon={UserIcon}>
                  <Segmented
                    value={u.role} options={ROLE_SEGMENTS}
                    onChange={(role) => { if (role !== u.role) onSetRole(u.name, role); }}
                  />
                  <button
                    type="button" className="uiBtn sm iconSq"
                    title={`Remove ${u.name}`} aria-label={`Remove ${u.name}`}
                    onClick={() => onRemove(u.name)}
                  >
                    <Trash2Icon size={13} />
                  </button>
                </PersonRow>
              ))}
              {inviting ? (
                <ShareInviteForm
                  exclude={[me, ...users.map((u) => u.name)]}
                  error={error}
                  onSubmit={invite}
                  onCancel={() => setInviting(false)}
                />
              ) : error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
            </Section>
          </>
        ) : null}
        {settings && publish ? <PublishSection {...publish} /> : null}
        {settings ? citation : null}
      </div>
    </div>
  );
}
