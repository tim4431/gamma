# Deleting your Gamma Cloud account

_Last updated: 2026-11-08._

This page is for people who use **Gamma PDF**, the app published on
Google Play by **Gamma PDF**, or another Gamma app (the desktop app for
Windows, macOS and Linux, and the iPad app), and who made a Gamma Cloud
account. It gives the steps to delete the account, says what deleting it
removes and when, and what it leaves alone. The Android and iPad apps link
to it from Settings → Account & sync → *Delete Gamma Cloud account*.

## What a Gamma Cloud account is

A Gamma Cloud account is the optional account at
[account.gammapdf.com](https://account.gammapdf.com). It signs you in to
Gamma servers with one account, carries your settings from one server to
another, publishes pages, and holds a paid plan if you buy one. You may
have made it on that website, or through *Sign in with Gamma Cloud* in an
app, which opens the same website in your browser.

You do not need one. The apps and Gamma servers work without it: the
library an app keeps on your device (*This device* in the Android app,
*This iPad* on the iPad, the desktop app's own library) and a Gamma server
you run yourself have accounts of their own, which are not Gamma Cloud
accounts and which this page does not delete.

## How to delete it

1. In a browser, open
   [account.gammapdf.com/settings](https://account.gammapdf.com/settings)
   and sign in with your e-mail address or username and your password, or
   with Google or GitHub if that is how you signed up.
2. On the **Settings** page, in the **Danger zone** section, find
   **Delete account** and press **Delete my account…**.
3. If the account has a password, enter it to confirm that it is you. An
   account made with Google or GitHub that never set a password skips this.
   Press **Delete my account**.
4. A dialog asks "Delete this account? This cannot be undone." On a paid
   plan it adds that your subscription ends now and is not refunded.
   Confirm it.

The account is deleted at once, and the browser goes to the sign-in page.
If the account pays for a plan and the subscription cannot be cancelled at
that moment (our payment provider, Stripe, does not answer), nothing is
deleted and the page says so; try again a few minutes later.

## What is deleted, and when

**At once:**

- Nobody can sign in to the account any more. Every browser signed in to it
  is signed out, and every Gamma server signed in with it, the one inside
  an app included, ends those sign-ins when it next checks the account,
  within the hour.
- The password, the Google and GitHub sign-in links, the settings the apps
  sync between servers, and the list of servers you signed in to are
  deleted. Links not used yet (to confirm an address, reset a password or
  connect a server) stop working.
- A paid plan's subscription is cancelled at Stripe, at once rather than at
  the end of the period, and what was paid is not refunded
  ([terms](TERMS.md#no-refunds)). A Pro plan's server turns read-only, as
  when a plan ends.

**Within about an hour**, our shared server, app.gammapdf.com, deletes your
account there and its library: a Lite or Plus plan's library, the pages a
free account published, and a Pro account's library left there from an
earlier plan. That is:

- your workspaces there, with their pages, notes, files and backups, and
  the shared workspaces you alone owned, which their other members lose;
- the pages you published from those workspaces, which stop opening at
  their links and at your `<username>-pages.gammapdf.com` address. The
  pages stay in the app you published them from;
- your account there, its sign-ins and its connections (an offline copy,
  the browser extension, an assistant).

What you wrote in a workspace someone else owns stays there, as part of
that workspace, and so does a link you made to one of its pages: the link
belongs to the page, and that workspace's owners can turn it off.

**After 30 days** the rest of the account is removed: the username, the
e-mail address, the display name and plan, the record of the subscription,
the invitations it sent by e-mail, and a Pro plan's server with its files
and backups. Until then the username and the address stay reserved, so
nobody else can take them.

**Within those 30 days** the account can be restored if you ask us (see
[below](#if-you-cannot-sign-in)). It comes back with its username and
e-mail address, but not with what was deleted at once or on the shared
server: you sign in again by resetting the password, or with Google or
GitHub on the same address. A cancelled subscription stays cancelled, and
the library on the shared server does not come back.

**What is kept.** The account server's audit log keeps its entries for the
account (sign-ins and password reset requests with the IP address they came
from, plan changes, the deletion itself), and the list of payment events
Stripe sent about it keeps each event's id, type and date. Before the
shared server deletes a workspace it writes one copy of it, which only we
can reach, in case the deletion was made in error; that copy is removed
after 90 days. Backups of the shared server made before the deletion
still hold what they held: the automatic ones until they are replaced in
their rotation, and a copy we made by hand or keep off-site until we
delete it. We use backups only to recover the server after a fault. A
report you made of a comment, a page or an AI reply stays until it is
resolved and for 180 days after, without your account once it is removed.
Stripe keeps its own records of payments under its privacy policy. The
[privacy policy](PRIVACY.md#gamma-cloud) lists everything the account
server holds.

## What deleting the account leaves alone

- **The library on your device.** *This device* in the Android app,
  *This iPad* and the desktop app's library are stored on your device, not
  by Gamma Cloud. Delete pages in the app, or delete the whole library
  by uninstalling the app (on Android, clearing the app's storage in the
  system settings does the same; for the desktop app, deleting its data
  directory).
- **A Gamma server you or your organisation run.** Its library and the
  account you have on it stay. Delete them on that server: its
  administrator removes an account in Settings → Users.
- **What you exported or downloaded.** Those copies are yours. Export
  the library on the shared server before you delete the account if you
  want to keep it: afterwards it cannot be exported.

## If you cannot sign in

Reset the password first: **Forgot password?** on the
[sign-in page](https://account.gammapdf.com/login) mails a link to the
account's address.

If that does not work, write to
[support@gammapdf.com](mailto:support@gammapdf.com), the contact the
[privacy policy](PRIVACY.md#contact) gives, saying that you want your Gamma
Cloud account deleted and giving its username; from the account's own
address if you can. Never send a password. We write to the account's
e-mail address to confirm that the request is yours, then delete the
account as described above. Ask the same way to restore a deleted account.
