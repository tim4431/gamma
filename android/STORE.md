# Publishing on Google Play

The Play listing of the Android app, package `com.gammapdf.gamma`: the
copy, the graphics, the answers to the Play Console's declarations, and
the steps from a new personal developer account to production, through
the closed test such an account must run first. How a release reaches
Play (the APK on GitHub, the bundle to the internal track), the Play App
Signing choice and the service account's setup are in
[docs/dev/android.md "Publishing on Google Play"](../docs/dev/android.md#publishing-on-google-play)
and [docs/dev/github_actions.md "Google Play, once"](../docs/dev/github_actions.md#google-play-once);
what Play's policies ask of this app, with sources, in
[docs/research/android.md "Publishing"](../docs/research/android.md#publishing).
The rules and Console paths here were checked against Google's pages on
2026-11-03, each with its source; the Console renames things, so what it
shows wins over the labels here.

Nothing here is submitted yet: the app is not in the Console. Keep the
copy in step with the [README](../README.md), the website and the
[Microsoft Store listing](../desktop/assets/store/listing.md), and keep the
declarations in step with the code: reviewers compare them.

## Listing copy

**App name** (30 characters at most): Gamma PDF

The launcher shows the app's own label, "Gamma" (`res/values/strings.xml`);
"Gamma PDF" is the Microsoft Store's name and the feature graphic's.

**Short description** (80 characters at most; this one is 79):

Read and annotate papers, keep nested notes, write with a stylus, work offline.

**Full description** (4,000 characters at most; this one is 3,635). Each
claim is something the app does; the ones a real device has not shown yet
are listed under [The closed test](#the-closed-test), and the text is
checked against them before it is submitted.

```text
Gamma PDF is a reading and note-taking app for research papers. Open a PDF, highlight it, and keep your notes as an outliner where every highlight is a block you can nest, link, search and share.

The app carries a Gamma server of its own, so your library lives on the device and works with no connection, and no account is needed to start. It also opens any Gamma server by its address, one you run on a NAS or a VPS or a Gamma Cloud library, and switches between them from the bar at the top.

READ AND ANNOTATE
• Open a paper by pasting a link (arXiv, DOI, or a publisher page), from your files, or by sharing a PDF to Gamma from another app. Gamma finds the PDF, and looks for a legal open-access copy when the DOI is paywalled.
• Select text or drag a box around a figure, pick a color, and add a comment. Each highlight becomes a note block, and tapping the block takes the PDF back to it.
• Highlights already saved in the file by other PDF readers come in as blocks too.
• Type text boxes onto the page. Strokes and boxes are notes as well, and export into an annotated PDF.
• Citations in the PDF are clickable: jump to the reference, and fetch a cited arXiv or DOI paper into your library.
• The title, authors and venue are looked up for you, and BibTeX is one tap away.

WRITE WITH A STYLUS
• Write on PDF pages and on sheets of paper in your notes with an Android stylus, such as an S Pen, a USI pen or a Wacom pen, with the pressure and tilt it reports.
• The line under the pen is drawn by the app itself, ahead of the page, and the stroke is saved as ink you can lasso, move, resize, recolor and erase.
• Draw and hold to turn a stroke into a clean line, arrow, rectangle, circle or other shape, and write along the ruler for a straight line.
• While the pen writes, a palm resting on the screen does nothing.
• A click of the pen's button switches to the eraser and its second button back to the last tool, or what you choose in Settings. Holding the button while you write erases.

RECORD
• Record a lecture or a meeting while you read and write. Play it back and your notes and handwriting appear as they were written; a tap on any of them plays that moment.

TAKE NOTES
• Highlights and free notes are the same kind of block, nested, reordered and linked the same way, with one undo history for the page.
• Markdown, LaTeX math, code blocks, callouts, tables and Mermaid diagrams render as you type.
• Link pages with [[page]] mentions, and embed blocks that edit their source in place.

ORGANIZE AND SEARCH
• Labels for what you search by, and folders that nest; one paper can sit in several folders without a copy.
• Search finds titles, notes, highlights and the full text of every PDF at once.

ASK AN AI ABOUT YOUR PAPERS (optional)
• Chat about the open paper, or several, to explain a passage, summarize or compare. The assistant can also search, read and file papers, asking before each change.
• AI features are off until you add your own provider in Settings (an Anthropic or OpenAI API key, or a ChatGPT sign-in). Nothing is sent to any AI service otherwise.

SHARE AND MOVE YOUR DATA
• On a Gamma server, share a page by link, and edit it together, live.
• Keep an offline copy of a workspace from another Gamma server on the device; it syncs while the app is open.
• Import Zotero libraries, Notability and GoodNotes notes, and Logseq, Markdown or Notion exports. Export a zip of all your data at any time; saved files go to your Downloads folder.

Gamma has no ads and no analytics. Your library stays on the device, or on the server you choose.

Gamma is open source: https://github.com/tim4431/Gamma
```

## Store settings

| Field | Value |
|---|---|
| App or game | App |
| Free or paid | Free (no in-app products) |
| Category | Productivity |
| Contact e-mail | `support@gammapdf.com`, forwarded to the developer by Cloudflare Email Routing. A personal account shows it on the listing, with the developer's legal name and country; `noreply@gammapdf.com` is the account server's sender and takes no mail |
| Website | <https://gammapdf.com> |
| Privacy policy | <https://gammapdf.com/privacy>, the site's copy of [PRIVACY.md](../PRIVACY.md) (`sites/` renders it at `/privacy/`); the app links it too, among its help links |

The privacy policy names the iPad and Android apps: the same server
runs on the device with its data in the app's private storage, left out
of the cloud backup and deleted with the app; the microphone is used only
for recordings and dictation; the apps make no update checks. Reviewers
read it beside the Data safety answers below. Play asks for the policy's
link inside the app as well as in the Console, and for a privacy contact
and the retention and deletion of data in it
([User Data](https://support.google.com/googleplay/android-developer/answer/10144311)).

## Graphics

| Asset | File | Notes |
|---|---|---|
| App icon, 512 × 512 | `store/icon-512.png` | A 32-bit PNG with an alpha channel, 1,024 KB at most, as Play's spec asks; the launcher icon's visible square as a full-bleed plate |
| Feature graphic, 1024 × 500 | `store/feature-graphic.png` | A 24-bit PNG with no alpha, as Play's spec asks; the Chrome marquee's layout with the logo's name and "Read papers. Keep what you learn." |
| Phone screenshots | `store-screenshots/01-read.png` … `06-search.png` | Six, 1080 × 1920 (9:16), 24-bit PNG with no alpha: enough to publish and, at four or more of 1080 px in 9:16, for Play's recommendations. Order and alt text below |
| 7-inch and 10-inch tablet screenshots | not made yet | Four of each for the tablet listing, between 1080 and 7680 px, 16:9 or 9:16 |

Both images are brand outputs, generated from `design/brand/marks/favicon.svg`
by `node tools/branding/build.mjs` ([design/brand/README.md](../design/brand/README.md))
to the sizes and formats of Play's
[graphic assets](https://support.google.com/googleplay/android-developer/answer/9866151).
Do not edit them by hand. The brand check treats every PNG under
`android/store/` as one of its outputs and fails on any other, so the
screenshots go to a folder of their own, such as
`android/store-screenshots/`.

The screenshots show the real app, taken on the emulator or a device
(`adb exec-out screencap -p > shot.png` gives the whole window, the bar
and the launcher included), in portrait on a phone and in landscape on a
tablet. Play's Metadata policy asks for screenshots "that reflect your
app's actual experience"
([Metadata](https://support.google.com/googleplay/android-developer/answer/9898842)),
so a browser shot with the Android stand-in host of the browser tests
(`frontend/tests/e2e/shells.mjs`, `androidShell`), which lays the page out
as in the app but has neither the bar nor the launcher, is not one.

The phone set is `tools/readme-media/shoot-play.mjs`
([recipe](../tools/readme-media/WORKFLOW.md#stills)): This device's own
library in the Android app on a 1080 × 1920 emulator, the status bar set by
System UI's demo mode. Its papers are arXiv preprints whose arXiv record
gives CC BY 4.0, since Play's Intellectual Property policy asks for rights
to what a listing shows
([IP](https://support.google.com/googleplay/android-developer/answer/9888072));
the curated demo of `tools/readme-media` is not used here, as its papers
carry arXiv's default license and its atoms PDF is the journal's own. CC BY
asks for credit: the papers are Google Quantum AI and Collaborators,
"Quantum error correction below the surface code threshold",
[arXiv:2408.13687](https://arxiv.org/abs/2408.13687) (the one the shots
read); Senoo et al., "High-fidelity entanglement and coherent multi-qubit
mapping in an atom array", [arXiv:2506.13632](https://arxiv.org/abs/2506.13632);
Liu et al., "KAN: Kolmogorov-Arnold Networks",
[arXiv:2404.19756](https://arxiv.org/abs/2404.19756). The chat's exchange
was written from the paper's own text and saved for the shot, not
generated; its connection, "My AI service" with the model "Assistant", is
a placeholder that only lists its model.

| Order | File | Alt text (140 characters at most) |
|---|---|---|
| 1 | `01-read.png` | A research paper open on a phone, its figure in view and a passage below it highlighted |
| 2 | `02-notes.png` | The paper's notes: each highlight is a block with a comment, and a nested note holds a formula |
| 3 | `03-pen.png` | Writing on the PDF with a pen: a phrase circled, and a straight line drawn under another below it |
| 4 | `04-chat.png` | The paper's AI chat explains a quantity from the paper, its formula rendered |
| 5 | `05-library.png` | The library: folders, and the papers filed in them with their labels |
| 6 | `06-search.png` | One search finds a word in titles and in the full text of every PDF in the library |

Tablet screenshots are still to make. A tablet's WebView has no "Mobile"
in its user agent, so the web app gives it the compact layout only while
upright and the desktop's docks in landscape (`frontend/src/app/App.jsx`,
`PHONE_MQ`); the landscape set needs a tablet emulator and shots of its
own.

## Data safety

What the code does with data, which the answers follow:

- **No analytics, crash reporting, advertising or tracking.** The app
  depends on no Google Play services and no analytics or ads library
  (`android/gradle/libs.versions.toml`), and declares no advertising ID.
  Nothing is sent to the developer by the app itself.
- **This device.** The library, the account and the password stay in the
  app's private storage; the WebView talks to the server on `127.0.0.1`,
  which never leaves the device. Backups and a move to a new device leave
  the library and the password out. Update checks are off
  (`GAMMA_UPDATE_CHECK=off`).
- **A server the person adds** receives what the web app sends any
  server: the library (PDFs and other files, notes, highlights,
  handwriting, pictures, recordings, comments, chats) and the account's
  sign-in. That server is the person's or their organisation's, unless it
  is a Gamma Cloud library, which the developer runs
  ([PRIVACY.md "Gamma Cloud"](../PRIVACY.md#gamma-cloud)). The app allows
  plain `http` to any server the person adds.
- **Gamma Cloud**, only when the person signs in with it: the e-mail
  address, the username, the display name and the password go to
  account.gammapdf.com, in the system browser; a Gamma server signed in
  with it (This device's included) checks in with the account server, as
  [PRIVACY.md](../PRIVACY.md) describes.
- **Third parties, for features the person uses**, as from any Gamma
  server: paper identifiers and titles to the scholarly services, the text
  and pictures the person asks about (and audio for dictation and
  transcription) to the AI provider they configured with their own key,
  translation to the translator chosen in Settings, and links to the sites
  they lead to. The person starts each, and chose the provider.
- **The microphone** records only while the page records or dictates,
  after the system's question, for the server shown.
- **Report a problem** opens GitHub's issue form, prefilled; the person
  submits it there or not.
- **Report** on a comment, a shared or published page or an AI reply sends
  what is reported (its text, its author or model, the page's title), the
  reason, the person's words and their username on that server to the
  server; on a server with Gamma Cloud sign-in, This device's included,
  the server sends it on to the account server, with the person's Gamma
  Cloud account when they linked one. The person starts each.

The answers, read as the research note reads Play's definitions
("collection" is data leaving the device, even to a server the person
chose; transfers the person starts are not sharing):

| Question | Answer |
|---|---|
| Does the app collect or share any of the required user data types? | Yes |
| Is all of the user data collected by the app encrypted in transit? | No: the person may add a server by a plain `http` address. Gamma Cloud and any `https` server are encrypted. Answering yes would need the app to refuse `http` for the servers added |
| Do you provide a way for users to request that their data be deleted? | Yes. The deletion URL is <https://gammapdf.com/delete-account/>, a public page that names the app and the developer and gives the steps for a Gamma Cloud account (its Settings page on account.gammapdf.com, *Delete account*); the app links to it from Settings → Account & sync. This device's library is deleted by deleting its pages in the app, clearing the app's storage or uninstalling it; a library on the person's own server, on that server |

| Data type | Collected | Shared | Optional | Purpose |
|---|---|---|---|---|
| Personal info: Name, Email address, User IDs | Yes (Gamma Cloud, or a server's own account) | No | Yes | App functionality, Account management |
| Files and docs | Yes (to the server the person uses) | No | Yes | App functionality |
| Photos | Yes (pictures placed on pages) | No | Yes | App functionality |
| Audio: Voice or sound recordings | Yes (recordings, dictation) | No | Yes | App functionality |
| App activity: Other user-generated content | Yes (notes, highlights, handwriting, comments, chats, reports) | No | Yes | App functionality |
| Location, Financial info, Health and fitness, Messages (SMS, e-mail), Contacts, Calendar, Web browsing, Installed apps, App info and performance, Device or other IDs | No | No | | |

Comments go to other people on the same server; they are counted above as
user-generated content, and if the Console reads them as messages,
*Messages → Other in-app messages* is ticked the same way. None of it is
processed ephemerally. Gamma Cloud's plans are bought on its
website through Stripe, never from the app, which links to no page that
leads to checkout ([below](#before-the-first-review)) and stores no
payment data.

## Content rating

The IARC questionnaire, in the category for utility and productivity apps:

- No violence, sexual content, profanity, drugs, alcohol, tobacco,
  gambling or crude humour in the app itself; what a library holds is the
  person's own.
- **Users interact**: on a server several people use, they share pages,
  comment and edit together, and a page can be shared by a public link.
  The developer runs no public space of its own in the app; the demo
  server's guest workspaces are private and deleted after a few hours.
- **No location sharing, no purchases in the app.**
- The AI chat produces text with the person's own provider and key. Each
  reply can be reported from inside the app; whether Play's AI-generated
  content policy applies to it is open
  ([Before the first review](#before-the-first-review)).

## Sign-in details

The Console takes the reviewers' instructions under App content →
*Sign-in details* (formerly *App access*) → **+ Add new instructions**, up
to five sets
([prepare your app for review](https://support.google.com/googleplay/android-developer/answer/9859455)).
The app works without an account, but its first screen is a list of
servers, and its Gamma Cloud sign-in is a part of the app restricted by a
login, so Play asks for a way into both:

1. Open the app. The first launch shows a list of servers; tap **Open**
   on **This device**.
2. The first start after an install unpacks the app's server, which can
   take a minute or more; the bar at the top says so meanwhile.
3. The library opens signed in as `admin`, with no password to enter.
   Every feature is there: add a PDF from the library's upload button or
   by sharing one to Gamma, highlight, take notes, write with a stylus.
4. Optional, to see a server elsewhere: tap the mark at the top left for
   the list of servers, then *Add remote server…*, enter
   `https://demo.gammapdf.com`, open it and tap **Try the demo** for a
   temporary guest workspace.
5. **Gamma Cloud**: in Settings → Account & sync, start the Gamma Cloud
   sign-in; the system browser opens account.gammapdf.com, where the test
   account's e-mail address and password, given here, sign in. The
   account is made for the reviewers and kept: Google asks for
   credentials that are reusable, valid from any location, in English
   and not held up by two-step verification
   ([sign-in requirements](https://support.google.com/googleplay/android-developer/answer/15748846)),
   and reviewers also use it to check the path to deleting an account.
6. AI features need the reviewer's own provider key (Settings → AI);
   nothing else needs one.

The research note's register also suggests saying here that the developer
publishes the web application the app shows, and listing what the app
adds natively: the server on the device, the stylus layer, opening and
saving files, the Gamma Cloud sign-in.

## Audience, ads and other declarations

| Declaration | Answer |
|---|---|
| Target audience | 18 and over. A tool for research papers, not designed for children; choosing younger ages brings in the Families policy |
| Appeals to children | No |
| Ads | No, the app contains no ads |
| Advertising ID | Not used |
| Government app | No |
| Financial features | None |
| Health | No |
| News app | No |
| Permissions | `INTERNET`, `RECORD_AUDIO` (recordings and dictation, asked when first used) and `MODIFY_AUDIO_SETTINGS`; no storage permission (saves go through MediaStore, picks through the system's pickers), no location, no foreground service |

## Before the first review

What Play's policies ask of the app beyond the forms, from the policy
register in the research note and Google's pages. Settled in the app:

- **Payments.** Inside the apps (any native host) the web app leaves out
  Settings → Account & sync's *Open account* and the *Manage plan*
  button of a Gamma Cloud hosted server's Settings → Server → Plan, so
  the plan is read-only there. The pages they open lead to checkout:
  account.gammapdf.com's *Plan & billing* and its plans page go to
  Stripe, and Play's Payments policy forbids even "directly linking to a
  webpage that could lead to an alternate payment method"
  ([Payments policy](https://support.google.com/googleplay/android-developer/answer/9858738),
  [understanding it](https://support.google.com/googleplay/android-developer/answer/10281818)).
  Plans are bought on account.gammapdf.com in a browser the app does not
  open for that. The browser the app's Gamma Cloud sign-in opens shows
  the account server's sign-in and sign-up pages without their links to
  gammapdf.com (its pricing) and the account home (*Plan & billing*): the
  app's authorize URL carries `gamma_app=1`, and a new account comes back
  to the sign-in rather than to the Overview
  ([cloud_accounts.md "From a native app"](../docs/dev/cloud_accounts.md#the-oidc-provider)).
  Still linking *Pricing*: the header of the gammapdf.com pages the app
  opens (the privacy policy, the deletion steps, the docs), and of the
  terms and the privacy policy the account server's sign-in and sign-up
  pages link to under their form.
- **Account deletion.** Play counts a Gamma Cloud account as one the app
  lets the person create, because the sign-in's browser reaches the
  account server's sign-up: account creation includes the case where
  "the app directs the user to an app account creation flow outside of
  the app". The app therefore needs a readily discoverable way to start
  deleting the account from inside it, and the Console a public page that
  names the app and the developer as on the listing and gives the steps
  ([account deletion](https://support.google.com/googleplay/android-developer/answer/13327111)).
  The page is <https://gammapdf.com/delete-account/>; the app links to it
  from Settings → Account & sync, and its URL is the Data safety form's
  answer above. This device's admin account is made and used offline,
  which Play does not count. Deleting the account deletes its library on
  the shared server and the pages it published within the hour, as the
  page says, rather than only closing them
  ([cloud_accounts.md "A deleted account on the share host"](../docs/dev/cloud_accounts.md)).
- **The privacy policy** is linked in the Console and inside the app,
  among its help links; Play asks for both, and for a privacy contact and
  the retention and deletion of data in the policy
  ([User Data](https://support.google.com/googleplay/android-developer/answer/10144311)).
  [PRIVACY.md](../PRIVACY.md) names the developer, Gamma PDF, as the
  Console and [DELETE_ACCOUNT.md](../DELETE_ACCOUNT.md) do, gives
  `support@gammapdf.com` as its contact, and the 30 days after which a
  deleted Gamma Cloud account's records go.
- **A reviewer account** for Gamma Cloud, under
  [Sign-in details](#sign-in-details).
- **Play App Signing** takes the release key as the app signing key,
  supplied with Google's PEPK tool at the first internal release, so the
  GitHub APK and Play's builds have one signer, as the release notes
  assume
  ([docs/dev/android.md "Publishing on Google Play"](../docs/dev/android.md#publishing-on-google-play)).

- **AI-generated content.** Play asks apps that generate content with AI
  to let users report or flag offensive output "without needing to exit
  the app" ([AI-generated content](https://support.google.com/googleplay/android-developer/answer/13985936)).
  Every AI reply in the chat has *Report this reply*: a dialog in the app
  takes a reason and the person's words and sends the reply's text and
  model to the server, which keeps it for its admins and, with Gamma
  Cloud sign-in on (This device's server has it), sends it on to the
  developer's account server, which mails the developer
  ([docs/dev/cloud.md "Reports"](../docs/dev/cloud.md#reports)).
- **User-generated content.** Shared pages, comments, live editing and
  public links show one person's content to others. Where Play's policy
  applies, it asks for terms accepted before people post, a definition
  of objectionable content, and reporting and blocking inside the app
  ([user-generated content](https://support.google.com/googleplay/android-developer/answer/9876937)).
  The Gamma Cloud sign-in and sign-up pages say that continuing accepts
  the [terms](../TERMS.md#what-you-may-not-share), which define what may
  not be shared and how it is reported. A comment's menu and the share
  view's View menu, which a published page opens in, have *Report*,
  which reaches the server's admins and, on a server with Gamma Cloud
  sign-in, the developer; a report names the author, so it reports the
  person as well.

Open, not decided:

- **Whether those policies apply.** For the AI policy, its examples in
  scope are chatbots whose chat is "a central feature of the app", and
  "productivity apps that use AI to improve an existing feature" are out
  of scope for now
  ([its FAQ](https://support.google.com/googleplay/android-developer/answer/14094294)),
  while Gamma's chat is optional, off until the person adds their own
  provider, and free-form. Whether review treats Gamma's sharing as
  user-generated content is unconfirmed.
- **Blocking.** There is no way for one person to block another. Sharing
  is by link or with named people on the same server, so nobody can reach
  someone who did not share with them; if review asks for blocking, it is
  still to be built.
- **A report on a server without Gamma Cloud** reaches only that server's
  admins, not the developer: a server the person added themselves.

## The closed test

A personal developer account created after 13 November 2023 reaches
production only after a closed test with at least 12 testers opted in
continuously for the 14 days before it applies
([production access](https://support.google.com/googleplay/android-developer/answer/14151465)).
The steps, once, from the account to production:

1. **The account.** The personal developer account, the one-time US$25
   fee paid, the developer's identity verified, and besides that:
   - the contact e-mail, the contact phone and the developer e-mail, each
     verified with the code Google sends
     ([required information](https://support.google.com/googleplay/android-developer/answer/10841920));
   - **device verification**, which a personal account needs before it
     can publish: on the Console's Home, *Verify that you have access to
     an Android mobile device* → **View details**, then the Play Console
     mobile app, signed in as the owner on a physical, non-rooted Android
     10 or later device, → **Verify**
     ([device verification](https://support.google.com/googleplay/android-developer/answer/14316361));
   - the **trader declaration**, asked when the account is created, under
     Manage account → Account details → About you. A trader's phone
     number and address are shown on the listing; Google makes no legal
     determination, and Gamma Cloud's paid plans bear on the answer
     ([trader status](https://support.google.com/googleplay/android-developer/answer/17594752)).
2. **The signing choice.** The release key as the app signing key,
   supplied at the first internal release (step 4). It can be changed
   until a release rolls out on open testing or production and never
   after; a change after an internal or closed release makes those
   testers reinstall
   ([docs/dev/android.md "Publishing on Google Play"](../docs/dev/android.md#publishing-on-google-play)).
3. **The app in the Console**, with the package name `com.gammapdf.gamma`,
   the listing above, the graphics, the privacy policy and every
   declaration (Sign-in details, Ads, Content rating, Target audience,
   Data safety with the deletion page, Government, Financial features,
   Health, News) filled in.
4. **The first bundle by hand.** Internal testing → **Create new
   release**; in its *App integrity* section, **Change app signing key**
   and provide a copy of the release key with Google's PEPK tool; then
   upload a release run's `store-android` artifact (`gh run download
   <run-id> -n store-android`) and roll it out. The commands are in
   [docs/dev/github_actions.md "Google Play, once"](../docs/dev/github_actions.md#google-play-once).
   Then the service account and its secret, so later releases arrive on
   their own, with `PLAY_RELEASE_STATUS=draft` while the app is a draft:
   each such upload waits on the internal track as a draft until it is
   rolled out by hand in the Console.
5. **Developer verification.** Check that the Console's *Android
   developer verification* page lists the release key's SHA-256 for
   `com.gammapdf.gamma` under *Package names*, and **Add key** if not.
6. **The device checks first.** Before the testers come, run the device
   checks the listing depends on on a real stylus device
   ([docs/dev/android.md "Device checks"](../docs/dev/android.md#device-checks)):
   the pen's pressure, tilt, palm rejection and buttons, the recording,
   the Gamma Cloud sign-in, opening and saving files, and This device's
   first start on 64-bit ARM. Change the description for any claim that
   does not hold.
7. **The testers.** A tester list, a Google Group or e-mail addresses of
   Google accounts, of more than 12 (15 to 20), since one who opts out
   breaks the count and one who opts out and back in starts their 14
   days again. They stay off the internal track's list: someone opted
   into internal testing counts for the closed test only after leaving it
   ([testing tracks](https://support.google.com/googleplay/android-developer/answer/9845334)).
   Lab colleagues and the GitHub community are the recruits; give them a
   short test plan (This device, a PDF, the pen, a remote server such as
   the demo), and ask them to install and use the app, not only opt in.
8. **The closed track.** Promote the internal release to a closed
   testing track. Under its *Testers* tab, add the list or the Google
   Group and a feedback address; under *Countries / regions*, check that
   it covers every tester, who is matched by their Play account's country
   ([countries](https://support.google.com/googleplay/android-developer/answer/7550024)).
   The opt-in link appears once the closed release has passed review;
   send it then. Testers install from Google Play. Since Play's build and
   the GitHub APK have one signer, Android takes Play's build over a
   GitHub APK of the same or a lower versionCode, and This device's
   library stays
   ([app updates](https://developer.android.com/google/play/app-updates)).
9. **Fourteen days.** Keep at least 12 testers opted in for 14 days in a
   row (the Console's Dashboard counts them). Google looks at real use:
   ship the fixes testers report through the usual releases (CI to
   internal, then promoted to the closed track), note what changed, and
   collect the feedback under Monitor and improve → Ratings and reviews →
   *Testing feedback*: the application asks for a summary of it.
10. **Production access.** **Apply for production** from the Dashboard:
    the questionnaire asks how testers were recruited, whether they used
    every feature, a summary of their feedback and how it was collected,
    who the app is for and what it is worth to them, how many installs
    the first year should bring, and what changed after the test. The
    answer usually takes seven days or less.
11. **Production.** Test and release → Production → *Countries / regions*
    → **Add countries / regions**; then **Create new release** with a
    release's bundle (*Add from library*) and its notes (500 characters
    at most per language), **Start rollout to production**, and **Send
    for review** on *Publishing overview*. The first release goes to every
    country chosen at once: a staged rollout is only for updates, and
    managed publishing cannot hold a first publish
    ([staged rollouts](https://support.google.com/googleplay/android-developer/answer/6346149),
    [managed publishing](https://support.google.com/googleplay/android-developer/answer/9859654)).
    Review can take up to seven days, or longer
    ([review](https://support.google.com/googleplay/android-developer/answer/9859751)).
    Once the app is public, set the `PLAY_URL` variable to its listing,
    <https://play.google.com/store/apps/details?id=com.gammapdf.gamma>,
    so the release notes carry the Google Play button; leave it unset
    before then, since only the closed test's testers can use its opt-in
    link. CI keeps feeding the internal track; each release is promoted
    by hand, and later ones can be staged.
