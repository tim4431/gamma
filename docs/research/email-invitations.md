# Invitations by e-mail

Design and review date: 2026-10-25. How a Gamma server invites someone by
their e-mail address: who sends the mail, what the mailed link carries,
when the invitation brings an invite code, and what lets the person in.
The first build went through a review in five lenses, with every finding
checked by a second reader who tried to refute it. The fixed build then
went through a second review of the same kind, and that build through a
third. Each round changed the design, and the findings that did so are
kept here with the scenarios that reproduced them. The mechanics as
built are in [dev/cloud_accounts.md](../dev/cloud_accounts.md) ("E-mail
invitations from Gamma servers", "Invitations by e-mail") and
[dev/workspaces.md](../dev/workspaces.md) "Pending invitations".

**Status (2026-10-25).** Built, with the three reviews' fixes. An admin
invites an address to the server from Settings → Users; a shared
workspace's owner and a share's managers invite one to theirs. Only the
mailed link admits: nothing is granted by matching an address, and
registering through an invitation confirms nothing. The Gamma Cloud
username option left both member UIs. Its API stays: Approve on a
request to join is an invitation by subject. What is still open is the
last section.

## Where it started

A person with no account on a server could be invited by their Gamma
Cloud username ([dev/cloud_accounts.md](../dev/cloud_accounts.md) "A
share's invitations by cloud username"). That works only for someone who
has a Gamma Cloud account already and has told the inviter its name.
People know each other's e-mail addresses, not their usernames, and the
person invited often has no account anywhere yet.

## Who sends the mail

- **Each Gamma server mails.** Every server would need an SMTP setup of
  its own. A desktop app's server and a NAS seldom have one, mail from a
  home address lands in spam, and every server becomes a mail sender to
  watch for abuse.
- **The account server mails, on the inviter's own grant** (picked). It
  already sends mail from gammapdf.com with SPF and DKIM in place. It is
  where the person has to make an account anyway, so it can prefill the
  address and mint a code where one is needed. It knows whether the
  address has an account and can tell the person, without telling the
  inviting server, which gets the same answer either way. Its rate limits
  cover every server at once.

Three rules follow from the choice:

- The link has to reach the server from anywhere, so a server signing in
  through the desktop app's public client cannot invite by e-mail: the
  Gamma server hides the option, and the account server refuses that
  client's tokens. It refuses a loopback address too.
- The mail comes from the operator's domain, so its words are the account
  server's. What the inviter and their server choose (a display name, a
  page's title, the server's name) goes in with every link-like word
  turned into "[link]", since a link in an official mail borrows the
  domain's trust. The first review found the text was otherwise an open
  phishing channel at volume, and the second that the filter still had
  gaps (below).
- The Gamma server keeps its own row and the account server keeps its
  copy. Each side expires after 14 days.

## Why the link carries a secret of its own

In the first build the row's `id` was both the handle the lists showed
and the secret the mailed link carried back to the sign-in, which admits
whoever presents it. The lists went further than the people who manage
the invitations: a workspace's members (on a public workspace, every
account on the server), anyone reading a share's settings, read-scope
integration tokens. A scratch test against the branch reproduced both
ways in:

- a workspace viewer read a share's pending `edit` invitation from the
  share settings, linked their own cloud account with
  `start?link=1&invite=<id>`, and could then edit the page through the
  share;
- a stranger's new cloud account, signing in with a workspace
  invitation's id, was provisioned on a server that admits invited people
  only and became an editor of the workspace.

Either way the intended invitee's row was used up and their mail stopped
working. The fix separates the two. The `link_id`, 24 random bytes, goes
only to the account server and comes back only through the mailed link;
it is the only id admission and the claim match. The `id` stays the
handle for withdraw, resend and the role, and admits nothing. Since the
addresses are personal data too, the entries reach only the people who
manage them: a workspace's owners and the admins, a share's managers
signed in with a session. Tests check that nothing a list returns is
accepted by `start?invite=`.

## Only the mailed link admits

The first build also let the address itself do three things, each a
shortcut past the link:

- **An instant add.** A workspace's or a share's invitation to an
  address an account here was linked under added that account at once,
  mailed nothing, and answered with its username (`{member}`, `{user}`).
- **An address match.** A sign-in whose verified e-mail matched a waiting
  invitation was admitted by it and claimed it, link or not. The first
  round kept this on purpose: admitting whoever controls the inbox is
  what an invitation by address means, and the account server verifies
  the address.
- **A confirmation.** Registering from the invitation's page with the
  invited address made the account confirmed at once, with no verify
  mail, since the invitation's mail had reached that inbox.

The second review showed what each cost:

- **The instant add was an address oracle.** On the share host every
  signed-in user may invite by e-mail, while the directory there answers
  only an exact name. A scratch test there had a user who could not see
  another account share one of their own pages and invite
  `ceo@corp.example`. The answer was `{"user": "r2_victim"}`: the Gamma
  Cloud username behind the address, which also names that person's page
  host. An unknown address answered `{"pending": …}`. Each probe cost one
  of the 20 sends an hour, about 480 lookups a day per account. On a
  self-hosted server any editor could map addresses to local usernames
  the same way. Hiding the name in the answer would not have closed it:
  the address match would have turned the waiting row into a named share
  entry at the person's next sign-in, and the prober would have learned
  it then.
- **The confirmation made the link stand in for the inbox.** An
  invitation link is a bearer link that people forward, and the page
  prefilled the invited address for whoever opened it. In the scratch
  test the invitee forwarded the mail; the reader registered with the
  prefilled address and got a confirmed account (201, no verify mail).
  On a second server where someone had invited the same address, the
  address match then admitted that reader and gave them the membership.
  The shortcut stayed open after the invitation was accepted, for its
  whole 14 days, and the real owner found the address taken.

Decided: the person's own click on the link they were mailed is the one
way an invitation turns into access.

- A workspace's or a share's invitation never looks the address up. It
  always keeps the row, mails, and answers `{pending: entry}`, whoever has
  the address. Someone already here accepts like anyone else: the mail's
  *Continue as @name* signs them in through the link, and a membership
  or a share entry they have keeps its role. The admin-only refusal of a
  server invitation for an address already linked here stays, as admins
  see the accounts anyway.
- Admission and the claim match the link id only. The ID token's e-mail
  is no longer read for invitations.
- Registering from an invitation sends the verify mail like any other
  registration. Its link goes on to the invitation's page, which already
  asked an unconfirmed account to confirm first. The register page still
  prefills the address, editable, and says a link will confirm it.
- A claim drops the used link at the account server, so the link works
  once and a forwarded copy reaches the "not valid any more" page.

The cost is one extra click for each side: someone already on the server
opens the mail instead of being added, and a newcomer confirms their
address before continuing. The inviter sees "Invitation sent" for every
address. Weighed and not taken: keeping the instant add only where the
directory is visible (it still maps addresses to usernames for editors,
and makes two rules); keeping the confirmation but not prefilling the
address (the prefill was asked for), or only while the invitation is
unaccepted (forwarding still works before that).

## Why invite codes stay inside the operator's gate

The account server's registration mode defaults to `invite`. To let a
newcomer register, the first build minted a one-use free invite code for
every invitation to an address with no account. The review reproduced
what that opened. Any verified account can connect a server of its own
and get a `server` client ([dev/cloud_accounts.md](../dev/cloud_accounts.md)
"Connecting a server"), then invite throwaway addresses through it, 30 an
hour and 100 a day. Each invitation's page showed a code, and the code
was not bound to the invited address. `take_invite` lets a valid code
past both `invite` mode and the allowed mail domains, as the operator's
own yes. In the scratch test, with registration on `invite` and the
allowed domains set to `university.example`, a verified account's
invitation minted a code; a second browser registered
`zed@elsewhere.example` with it (201), where the same registration
without a code was a 403. The codes were also left out of the Admin list,
so the operator could not see them being minted. Every new account could
do the same again: `invite` mode became open registration, unseen.

What was built:

- A code is minted only where it is the operator's to give: through a
  client the operator made (no owner: the share host, a hosted server, a
  client made on the Admin page). A server someone connected themselves
  mints one only while the operator sets `invitation_signup` to `any`.
- Never while allowed mail domains are set (an address at one registers
  without a code, any other is one the operator kept out), never in
  `open` mode (no code is needed) or `closed` (no account is made), and
  never for an address that has an account.
- The codes are marked on their own row (`invites.invitation`) and stay
  out of the operator's list by that mark. The first build's subquery
  over the invitations let a spent code back into the list once its
  invitation was purged.
- The mail and the page say how the person gets in: sign in, create an
  account, or, when a new account needs a code the invitation does not
  carry, that it does.

Weighed and not taken: binding a code to the invited address, which
breaks "create one with this address, or another one"; listing the codes
in Admin under their inviter; a cap on the codes each account mints.

## The admission rule

What admits is the sign-in that came back through the mailed link:

| sign-in policy | an admin's invitation to the server | a workspace's or a share's invitation |
|---|---|---|
| `refuse` | admits | does not admit; the member UIs hide E-mail and the API refuses |
| `claim` | admits | does not admit, as under `refuse` |
| `provision` | admits (every verified account is let in anyway) | admits |
| `invited` | admits | admits |

Under `refuse` and `claim` the admin decides who gets an account, so a
member must not bring a stranger in, and an admin's invitation is that
decision. Under `invited`, an invitation from someone here is what the
policy asks for. An invitation never links an existing account that has
no cloud identity: the refusal for that case comes first, since linking
it would hand the account to whoever holds the link. A hosted plan's
account cap and the read-only check still apply after admission.

## The limits on the account server

The first review gave the send a per-client limit (600 an hour) and a
daily circuit breaker across all servers (3000), both counted right after
the token. The second found two gaps:

- **Refused calls spent the shared budget.** A desktop-client token, which
  any verified account can get, or a server sending bad fields, was
  refused with no mail but still counted in the breaker. In the scratch
  test 30 such calls left `invitations:all` at 30. Thirty free accounts
  at 100 calls a day fill it in a few hours, and every server, the share
  host and hosted ones included, then gets 429 for a day, with no mail
  volume or audit rows to explain it. Now the client limit and the
  breaker count only a request that will send, after every check. The
  breaker is also split: only servers people connected themselves share
  it (`invitations:connected`), and the servers the operator runs are
  bounded by their own per-client limit. Self-connected accounts can
  still fill their shared bucket with real sends, but those cost mail,
  leave audit rows, and no longer reach the operator's servers.
- **Nothing limited mail to one inbox.** One account could mail the same
  address 30 times an hour and 100 a day from the official domain, with
  fresh ids or by re-inviting. Now each inviter may mail one inbox 5 times
  an hour and 20 a day. There is no limit across inviters, as one would
  let anyone keep a person from being invited at all. The limit depends
  only on earlier sends, so it says nothing about accounts.

## Smaller findings that shaped it

- A re-send keeps the code only for the same inbox, and moves the code's
  expiry with the invitation. Carried to another address, the code would
  tell whoever sent both whether the first address has an account.
- A re-send whose mail fails puts the invitation it replaced back as it
  was, so the link already in the person's inbox keeps working.
- An invitation belongs to the sign-in client that sent it. Another
  client registered for the same address gets a 409 and can neither
  replace nor cancel it.
- `safe_next` refuses whitespace and control characters:
  `/<tab>/evil.example` reached another host once the browser dropped the
  tab.
- The per-IP bucket counts only failed tokens, since the share host's
  people all come from one address. The limiter keeps each bucket's own
  window, as a full table had dropped the daily cap every hour.
- The send waits 60 seconds, since the account server mails before it
  answers. In the first build a call that got no answer withdrew its
  link, as the mail may have gone out. The second review found that this
  also hit a resend or a re-invite that timed out: the link the person
  already held stopped working, while the row stayed listed as waiting.
  Now the link is withdrawn only when no row here carries it: a first
  send that stored nothing, or a row withdrawn while the call hung.
- The link filter missed the server's own name, which a self-connected
  server chooses freely, and its list of top-level domains missed most
  real ones (`gamma-cloud.services`, `help.network`). Now every dotted
  name whose last part is two or more letters becomes "[link]", except a
  name ending in a file extension common in page titles, so
  `attention.pdf`, `notes.md` and `Node.js` stay. Accepted trade-offs: a
  name ending in an extension that is also a domain (`evil.zip`) passes,
  and a dotted abbreviation in a title (`Proc.IEEE`) becomes "[link]" in
  the mail, not on the page.
- Withdrawing had asked the account server on the withdrawer's own grant,
  so an admin or an editor with no Gamma Cloud link left the link and its
  code alive, and stopping a share, deleting a workspace or making it
  personal never asked at all. Under invite-only sign-up such a code
  could still register an account. Now every way a row goes but its
  expiry drops its link (withdraw, the claim, the cascades), on the first
  grant the server holds among whoever acts, the inviter and the admin
  who last signed in, since the account server checks only the token's
  client.
- The third review found the cascades (stopping a share, deleting a
  folder or a workspace, making one personal, deleting an account) still
  dropped links at the server's stored address. On a server with no
  confirmed public URL that is a loopback origin the account server
  refuses, or nothing, while the mail had gone out at the inviter's https
  origin, under which the account server keeps the link. Now each row
  records the address its mail went out with, and its link is dropped
  there. A mail sent again from another address drops the copy at the
  earlier one, since the account server replaces a link only under the
  same address.
- `manage.py delete-user` exited before the background thread had asked
  the account server to drop the deleted workspaces' links. It now waits
  for those calls, as it already did for the grant's release.
- A Gamma server with no name of its own sends its hostname as its name,
  without the port. On another port the mail took that name for a link
  and named the server "[link] (lab.example.org:8443)". The hostname alone
  now counts as no name, so the mail names the host once, with its port.
- An invitation by e-mail never lowers a share entry or a membership that
  someone already has.

Refuted: `/invite/continue` as an open redirect. It needs a signed-in,
confirmed visitor and a live link, and it adds nothing, since a
self-connected client's `/authorize` already redirects anyone to its
registered address.

## Still open

- **The connect flow does not prove control of an origin.** The answer
  to *Connect* hands the code to the approving browser, and whoever
  approves can open the page with a challenge of their own and redeem the
  code. So any verified account can get a `server` client for any https
  origin. Through it, it can send official mail naming that server,
  whose links lead to the real server's sign-in with an id it does not
  know (no admission), and under the default `invitation_signup` no
  code. The flaw predates this work, but invitations now lean on the
  client's origin. Handing the code over only as a redirect to the
  origin, or checking a file the origin serves, would close it.
- **A link can outlive its row.** Dropping a link is best effort: when
  the account server cannot be reached, or no account here holds a Gamma
  Cloud grant to ask with, the row goes here all the same and the link
  lives on there until its 14 days end. It still opens a valid-looking
  invitation page and ends in a sign-in the invitation no longer admits.
  The hourly sweep drops no links, as both sides expire on the same 14
  days. An account deleted here leaves the invitations it sent to targets
  that remain, with an inviter that no longer exists, until they expire
  or are withdrawn.
- **The account server's rate limits live in memory.** One uvicorn worker
  holds them, so a restart resets them, the daily circuit breaker
  included, and a second worker would split them. Cloudflare's rules do
  not help here, since the caller is a server.
- **A refused send can still spend its client's budget.** The client
  limit and the breaker run just before the row is written, so a send
  refused there with a 409 (another client of the same server owns the
  invitation) still counts. Only the server's own clients can cause it.
- **Self-connected servers bring no code by default.** Under `operator`,
  an invitation from a server someone connected themselves carries no
  code while sign-up needs one. The newcomer is told so and needs a code
  from elsewhere. Setting `any` brings back the bypass above for anyone
  who can connect a server, which the open connect flaw makes anyone. A
  trust mark per client, or `any` once connecting proves the origin,
  would be the next step.
- **A failed verify mail loses the way back.** When the verify mail fails
  while registering from an invitation, the page goes to the Overview,
  so it does not claim a link went out, and the Overview's resend does
  not carry `next`. The person gets back to the invitation through the
  link in the invitation's mail.
