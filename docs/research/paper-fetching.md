# Paper fetching evaluation

## Link-ranking smoke check (2026-09-28)

Given the exact paper title, a read-only check of the MIT publication list
ranked `prl_81_5768.pdf` first among eight returned candidates.
Downloading that link produced four text pages, and the
opening-title check matched the requested paper. No AI or search-provider
call was made. The copy's publication version remained `unknown`; this check
does not establish `published_only` eligibility or setup-only discovery quality.

## Discovery and retrieval scenario review

The [scenario corpus](paper-fetching-cases.json) separates finding relevant
papers from downloading known papers. It covers a setup-only 85Rb lattice
question, an old paper on a long MIT publication list, a broken first PDF
candidate, a published-only request, a similar-title distractor, and a scanned
or malformed text layer. The corpus defines review criteria, not observed
results. Some cases call for controlled failure injection; others can be used
to review live research sessions.

The cesium reference's title, DOI and authors are supported by its
[APS record](https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.81.5768).
The [lab-hosted paper](https://eapg.mit.edu/wp-content/uploads/2018/01/prl_81_5768.pdf)
describes a one-dimensional cesium lattice; the 85Rb parameters in the prompt
come from the research question, not from that paper. These references are
reviewer material and should not be supplied to an agent during a setup-only
discovery run.

`tools/paper_fetching_eval.py` is a read-only, standard-library evaluator for
recorded outcomes. It makes no network, account or model calls and does not
run the agent. From the repository root:

```sh
python tools/paper_fetching_eval.py --help
python tools/paper_fetching_eval.py
python tools/paper_fetching_eval.py --template > paper-fetching-results.json
python tools/paper_fetching_eval.py --results paper-fetching-results.json
python tools/paper_fetching_eval.py --results paper-fetching-results.json --json
```

Fill the template from a saved tool trace and a review of the documents before
interpreting the output. Set `run_label` to identify the run and `run_kind` to
`recorded`, or `synthetic` when testing the evaluator with invented outcomes.
Keep traces outside this repository if they contain private account or library
data; the `evidence` array can contain sanitized observations or references to
the saved evidence. Each assessed case needs at least one evidence entry.
The validator checks the report's structure, not the truth of those judgments.

| Assessment | `true` means |
|---|---|
| `discovery_relevant` | The discovered paper is relevant to the research question, with differences such as species or geometry explained. |
| `correct_document` | The retrieved document matches the requested identity, checked against metadata and its contents. |
| `version_correct` | The version used satisfies the request and is accurately identified, or a forbidden substitution was correctly refused. |
| `human_intervention` | A person had to assist, for example through sign-in, a browser check or manual upload. |
| `extraction_usable` | The retrieved evidence is readable enough to support the requested answer and citations. |

Use `false` for an assessed failure and `null` for unassessed or inapplicable
dimensions. For example, a run starting with a known DOI need not assess
discovery. If a published-only request is correctly refused, version handling
can pass while document and extraction remain unassessed. Record that reason
in `notes`. Human intervention is an observation, not a success or failure
score. For extraction, honestly reporting unreadable equations does not make
the evidence usable.

Optional `elapsed_seconds`, `requests` (an integer), and `cost_usd` fields must
be nonnegative. Define measurement scope consistently in your run notes: for
example, whole-session elapsed time and external HTTP requests, including
retries. Missing measurements stay unknown, not zero. Totals display how many
cases supplied data; rates show their assessed denominator and unassessed
count. Missing cases remain visible, and an untouched template yields no
success rate. Repeated runs should use separate reports rather than duplicate
case IDs. Exit status is 0 for valid input and 2 for invalid input; it is not
a benchmark pass/fail threshold.

Offline evaluator checks:

```sh
python -m unittest discover -s tools -p test_paper_fetching_eval.py
```

The corpus and evaluator contain no live benchmark results. The historical
observations below evaluated earlier behavior with known titles
and DOIs; they do not establish discovery quality for setup-only questions.

## Historical live paper-chat evaluation

Tested 2026-09-28 through the real streaming `/api/ai/chat` endpoint, using
the account's configured `gpt-6-astra` connection at low reasoning effort.
These were model-selected tool calls with real Crossref, arXiv and publisher
requests, not scripted tool responses. Library read/edit tools were disabled;
the test questions used public papers and did not read or change library pages.
No publisher sessions were connected on the tested local instance.

## Questions and observed results

| Question | Result | Whole chat |
|---|---|---:|
| Fetch *Attention Is All You Need*, explain the attention scaling factor, and cite the page. | Downloaded the 15-page arXiv PDF; answered from PDF p. 4. | 13.1 s |
| Find *Charge-insensitive qubit design derived from the Cooper pair box*, fetch it, and explain the effect of EJ/EC. | Found the exact Crossref record, downloaded the 19-page APS PDF, then read a second window from cache. | 26.0 s |
| Inspect the attention paper's references, follow Bahdanau/Cho/Bengio, and compare the two papers. | Read the reference list and downloaded the cited 15-page PDF. Initially needed an extra identifier lookup after title search missed it. After the search fix, title search found it directly: four tool calls instead of five. | 38.7 s before; 26.8 s after |
| Fetch DOI `10.1038/s41586-019-1666-5` and explain the experimental result with page citations. | Downloaded the 7-page Nature publisher PDF and read two windows; no access failure. | 25.8 s |
| Fetch DOI `10.1126/sciadv.aay5901`, allowing a legitimate open-access alternative. | The resolver supplied the 25-page arXiv submitted preprint. After the provenance fix, the answer explicitly said the publisher PDF could not be fetched and identified the version used. | 15.2 s before; 14.3 s after |
| Read the Science publisher PDF for that paper, specifically excluding a preprint. | Actual HTTP 403. The answer stated no text was retrieved and did not substitute another version. After the recovery-guidance fix, it named the Connector controls and the browser-upload route accurately. | 11.9 s before; 29.0 s after |

These are individual observations, not controlled performance benchmarks.
Model generation and prompt-cache state vary between runs. Successful initial
fetch tool calls took about 0.1–5.9 seconds in this environment; later windows
of the same source took about 0.01 seconds. Most elapsed time was outside
download/extraction. Ten chat requests covered six scenarios including reruns
and the stale-process failure below; five distinct full-text papers were read.

## Fixes prompted by the evaluation

1. **Exact-title arXiv search.** The all-fields AND query returned no match for
   *Neural Machine Translation by Jointly Learning to Align and Translate*.
   A title-phrase query returned the correct `1409.0473` record. Search now
   combines the title phrase with the keyword branch in one API request.
2. **Open-access provenance.** The resolver already knew it had substituted a
   submitted preprint, but `fetch_document` discarded its note. The note now
   survives extraction, caching and every tool-result window. The rerun's
   answer correctly distinguished the fetched version.
3. **Human assistance after a block.** The original answer hedged about
   whether Gamma Connector supported saving PDFs. Access errors now provide
   the actual Publisher sessions controls, manual browser verification,
   the journal-sign-in permission, browser PDF saving and manual upload.
   They also explain that page reading must be enabled to read an uploaded
   paper. The final live answer reflected those steps without promising
   that cookies would solve the block.

Offline regression coverage in `test_ai_tools_web.py` checks the search wire
query, provenance across cached windows, and recovery guidance for 401/403/429.
The web-tool and publisher-session suites passed together: 38 tests.

## Operational finding and remaining checks

The first request against the already-running development backend failed with
`publisher_sessions` missing `cache_scope`: the process had an older module
loaded. A fresh process using the current checkout resolved this. The evaluation
used short-lived localhost processes with startup jobs disabled; it did not
restart the existing server. Restart that backend before using the changes.

The observed 403 establishes blocked-access handling, not that a CAPTCHA was
encountered or solved. Real connected-cookie success and the complete human
handoff (browser verification, refresh, retry or PDF upload) remain unverified
because the local instance had no publisher connections. Cookie isolation,
permission bypass and refresh/expiry behavior are covered by offline tests.

The three follow-ups this evaluation suggested are built (next section).
Scanned papers and badly encoded equations still need a separate visual
reading evaluation: these runs established text retrieval, not universal PDF
extraction quality.

## Follow-up: handing blocked fetches to the browser (2026-09-28)

The 403 case showed that written recovery steps leave the work to the user.
Instead, a blocked fetch is now handed to the user's browser:

- **Walls are named.** A bot check served as 200, 403 or 503 (Cloudflare's
  `cf-mitigated` header and challenge script, DataDome, PerimeterX, Imperva,
  Akamai), a redirect to a sign-in page, a refusal, a rate limit, or an
  article page whose PDF was out of reach. Before this, a bot-check page
  answered with 200 reached the model as "web page text".
- **A card, not instructions.** The reply's card opens the publisher's page
  (never `doi.org` or a sign-in page) through Gamma's `/go` address. Gamma
  Connector recognizes that tab and sends the PDF once the user is through;
  without it the user drops the file on the card. The chat then continues by
  itself, and `fetch_paper` reads the delivered text.
- **Better cookie use on the server.** One cookie jar spans the resolver's
  walk and the download, so a landing page's session cookie reaches its PDF
  link. The PDF link is fetched with the article page as `Referer`. A
  connected publisher's requests present the connecting browser's
  User-Agent, and the Connector refreshes a connected host's cookies right
  after a handed-off fetch succeeds.
- **Search.** An exact cited title ranks first, and a work found in both
  registries keeps its DOI and its arXiv id; the result offers both
  versions to `fetch_paper`.

Verified offline (`test_fetch_handoff.py`, the web-tool and publisher
suites, node tests of the card rules and the Connector's rules). A checked-in
browser scenario covers the card: Open through `/go`, the Connector's
"watching" state, a dropped PDF, and exactly one continuation. One real
Chromium run with the real Connector used a local fake publisher whose PDF
needs a sign-in cookie. Nothing was delivered before sign-in; after it the
PDF arrived, the chat continued and the Gamma tab came forward.

The first real run (nine papers, gpt-5.6-sol) found five PDFs, one abstract
page and three blocked papers, and exposed three gaps:

- **Elsevier's DOI landing got no card.** linkinghub answers with a
  "Redirecting" page (a meta refresh, no text); the fetch stopped there. It
  now follows the refresh to ScienceDirect and hands the refusal over, and a
  page with no readable text is itself handed over (`script`).
- **IOP's card opened `validate.perfdrive.com`**, Radware's bot check, not the
  article. The card now opens the page the check returns to (`ssc=`).
- **`arxiv.org/pdf/2401.04219` answered 406** to every program, including
  plain urllib, while `export.arxiv.org` served the same PDF; the resolver now
  falls back to that host.

The Connector did not take the handed-off tabs. The card now asks the
Connector before the user opens the page and names the likely cause: not
installed, not reloaded after the update, set to another address, or signed
out. The Connector also accepts the `/go` address on any host (localhost
versus 127.0.0.1 no longer matters). Re-checked live: the arXiv paper
arrives (69 pages), Elsevier gets a ScienceDirect card, IOP a card for its
article, and Science a card for its article.

With the Connector now taking the tabs (ScienceDirect for the Josephson
paper, AIP behind Cloudflare), both cards kept waiting after the checks.
The publishers' download paths explain it: ScienceDirect's "View PDF" is a
`pdfft` page whose script redirects to a signed `pdf.sciencedirectassets.com`
URL, and its pages carry no `citation_pdf_url` or DOI metadata; Silverchair
(AIP) redirects the PDF to its watermark host, which the page's own fetch
cannot follow across origins, and bot rules refuse requests that are not
navigations. Automated Chromium is blocked by both sites (ScienceDirect's
error page names "HeadlessChrome"), so the behavior was reproduced against a
local fake instead. The Connector also skipped such pages entirely when it
guessed a DOI from the page's text, which on an article page is usually one
of its references. Now it finds "View PDF"-style links rendered after load,
opens a link in the tab when downloads fail (not when the link leads to a
sign-in), takes the PDF from the viewer, ignores text-guessed DOIs, and
reports each step on the card.

The next real run showed the IEEE paper open in its tab while the card
waited: IEEE's `stamp.jsp` shows the PDF in a frame, which is not a link.
The Connector now takes a PDF from a frame too. Three requests followed:

- **No Gamma page on the way.** The card now asks the Connector to open the
  publisher's tab itself; `/go` remains only for the desktop app and a
  Connector that has not answered yet, and goes on without delay.
- **In the background.** A setting (off by default) lets a card hand its
  request to the Connector without a click. The Connector opens the page in
  a background tab next to Gamma, three at a time, and closes the tab after
  delivery. (A minimized window of its own flashed up on Windows and kept the
  tabs out of the tab strip, so it was dropped.)
  It does not solve CAPTCHAs; it finishes what the browser gets unasked,
  which is a lot: a paper the user's session or institution network already
  has access to, and checks that pass a real browser on their own. When the
  page needs the user, the card says why (sign-in, bot check, another paper
  shown) and **Show the tab** brings it forward.
- **Saving.** Every paper a reply fetched shows under it as a row (or its
  card, when it needed the browser) with a **+** that adds it to the library
  through the Connector's ingest (`POST /api/clip`); a PDF the browser
  delivered is held for this, so a paper the server cannot download still
  arrives with its PDF. A first version listed a reply's papers, linked ones
  included, behind one "Save to library" pill with checkboxes; per-paper
  buttons proved simpler.

Checked in real Chromium with the real Connector against local fakes (see
[extension.md](../dev/extension.md#testing)). That run also caught
concurrent background opens overwriting each other's bindings, since fixed.

Still unverified: completing a real publisher's check through the Connector
(Science's Cloudflare, Radware at IOP, an institutional SSO round trip,
Atypon's `epdf` reader), whether a stored User-Agent makes a transferred
`cf_clearance` usable from a server on the browser's machine, the
desktop app's path through the system browser, and whether real publishers'
checks pass by themselves in a background tab.

Mechanics and permissions: [Agent tools](../dev/ai_tools.md).
