# Live paper-chat evaluation

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
- **In the background.** A setting lets a card hand its request to the
  Connector without a click. The Connector opens the page in a tab beside
  the Gamma one, three at a time, and closes the tab after delivery. (As
  built it was off by default and used a minimized window of its own; both
  changed on 2026-09-30 — the window was easy to lose track of, and the
  setting earns its keep — see [extension.md](../dev/extension.md).)
  It does not solve CAPTCHAs; it finishes what the browser gets unasked,
  which is a lot: a paper the user's session or institution network already
  has access to, and checks that pass a real browser on their own. When the
  page needs the user, the card says why (sign-in, bot check, another paper
  shown) and **Show the tab** brings it next to Gamma.
- **Saving.** Each reply lists the papers it read or linked, with checkboxes,
  and saves the chosen ones through the Connector's ingest (`POST
  /api/clip`); a PDF the browser delivered is held for this, so a paper the
  server cannot download still arrives with its PDF.

Checked in real Chromium with the real Connector against local fakes (see
[extension.md](../dev/extension.md#testing)). That run also caught
concurrent background opens overwriting each other's bindings, since fixed.

Still unverified: completing a real publisher's check through the Connector
(Science's Cloudflare, Radware at IOP, an institutional SSO round trip,
Atypon's `epdf` reader), whether a stored User-Agent makes a transferred
`cf_clearance` usable from a server on the browser's machine, the
desktop app's path through the system browser, and whether real publishers'
checks pass by themselves in a minimized window.

## Discovery from a research question (2026-09-29)

The earlier rounds started from a known title or DOI. This one started from a
setup: an 85Rb 1D standing-wave lattice at 785 nm, a waist of about 100 µm and
15 µK, asking what limits the density. Codex, given that question, found
*Degenerate Raman Sideband Cooling of Trapped Cesium Atoms at Very High Atomic
Densities* (Vuletić, Chin, Kerman and Chu, PRL 81, 5768) as a lab-hosted PDF
and compared four other papers on loss mechanisms. It got there with the
model provider's hosted web search: several rephrased queries in the field's
vocabulary, abstracts read before answering, and the lab's publication list as
the copy. Gamma then had two registries and no general web search.

What the registries and pages did, measured the same day:

- **OpenAlex relevance.** Its `search` put the PRL first for "degenerate raman
  sideband cooling high density" in 0.4 s. Broad concept queries drifted to
  highly cited reviews: "light-assisted collisions loss optical lattice high
  density" returned a gold-nanoparticle review among its top five. Its
  `title_and_abstract.search` filter timed out (504, "query took too long").
- **OpenAlex without a key.** Keyless search was paused ("Anonymous search is
  paused while the search cluster recovers from heavy load", 503), and after
  about a dozen calls the keyless daily budget was gone (429). Lookups by id
  kept working. Since February 2026 a free key gives ten times the keyless
  budget; searches cost $1 per 1 000 and lookups nothing.
- **Crossref's citation sort.** `sort=is-referenced-by-count` with a
  bibliographic query returned a diabetic-retinopathy paper and an essay on
  ChatGPT for "raman sideband cooling": the sort replaces relevance instead of
  ordering the relevant hits.
- **arXiv's AND.** Every word of a descriptive query is required, so a setup
  description finds nothing.
- **A lab's publication list.** The MIT group's "all publications" page holds
  238 distinct PDF links; the PRL is at position 227. The old link list kept
  the first eight URLs, without their text, so a fetch of the page could not
  have led to the paper.
- **Unpaywall.** The resolver took the best location only; when it failed the
  other copies were never tried.

What was built, with its reasoning:

- **OpenAlex as a third registry**, asked in parallel with Crossref and arXiv,
  merged per work, adding abstracts, citation counts and open-access PDFs. Its
  citation graph backs `related_papers`, which follows a paper to the works it
  cites or that cite it. The account can set an OpenAlex key in Settings, so
  keyless pauses and budgets stop mattering. An outage is named in the result
  rather than silently missing.
- **Ordering applied locally.** `sort` (citations, recent) and `from_year`
  reorder or filter the registries' relevant candidates, because the
  registries' own citation sorts drop relevance.
- **A relaxed arXiv retry** with the five longest words when a long query
  finds nothing.
- **General web search** (`search_web`) through the chat's own AI connection
  (the provider's hosted search, what Codex uses), Brave Search or SearXNG.
  The provider route needs no setup for OpenAI, ChatGPT sign-in and Anthropic
  connections, so it is Automatic's fallback. Results are leads: when the
  provider reports the pages its search returned, only those pass. The
  registries stay the first step because they establish identity.
- **Links with their text, ranked against the paper.** On the MIT list the PRL
  moved from position 227 to first once `fetch_paper` was given its title.
- **Several open-access copies**, up to four, tried in version order before the
  browser handoff. With web search on, the model may make one search for
  another legitimate copy before ending its reply.
- **Identity and version in every fetch.** The result names the PDF's version
  and says whether the expected title appears; `version: "published"` refuses
  unpublished substitutes.
- **A discovery recipe in the prompt**: short concept queries, abstracts
  before fetching, citations followed, and analogies told apart from direct
  evidence.

Offline tests cover every piece (`test_paper_discovery.py`,
`test_search_services.py`, the hosted-search wire tests in `test_ai_wire.py`).
A live run of the pieces against the real services, from an isolated data
directory, found:

- "Raman sideband cooling high density" returned the PRL in the top five, with
  its abstract and 214 citations, next to the 3D Raman sideband cooling PRL
  and a 39K paper. Crossref's book chapters titled "Raman Sideband Cooling"
  came first, so OpenAlex now leads the interleave.
- Keyless OpenAlex ran out of budget between two queries, and the result named
  it with the remedy. The other registries still answered.
- The PRL's most cited citing works are reviews (single trapped ions, optical
  dipole traps), so `related_papers` gained `sort: "recent"`.
- Fetching the MIT list through `fetch_paper`'s path returned 238 links in
  0.9 s, and the title ranked the PRL first.
- Two DOIs with open-access copies resolved to the publisher's PDF and were
  labelled `publisher`; Unpaywall listed the published copy before the arXiv
  preprint for both.

Not verified live: the hosted search call on a real OpenAI, ChatGPT or
Anthropic connection (the request and stream shapes follow the providers'
documentation and Codex CLI's tool spec), Brave and SearXNG with real
accounts, and a full chat answering the 85Rb question end to end.

Mechanics and permissions: [Agent tools](../dev/ai_tools.md).

## What a real reply cost, and the shape that followed (2026-09-30)

A Raman-sideband-cooling question was answered with four non-arXiv PDFs
fetched and verified. The reply was 26 000 input tokens for 800 output —
and reading the run explained where they went, which turned out to be four
separate design problems rather than one.

- **A verification read cost a full window.** Each `fetch_paper` put 20 000
  characters of paper into the conversation, where every later round
  re-sent it, although the task only needed the title and the version. Four
  fetches were roughly 20 000 of the 26 000 tokens.
- **The fetches were serial.** The loop ran a round's calls one after
  another, so four publisher round trips were four waits.
- **The model put intent in the query.** The searches included "journal pdf
  publisher" — words that confuse a bibliographic match. The tool had
  year and sort filters but no way to say "a published article" or "must
  have a free PDF", so the model spelled it into the text.
- **A wall ended the turn.** A blocked paper told the model to stop and
  wait for the browser. With several papers in flight, the others waited
  for a synthetic "Please continue." message that re-planned from scratch
  and left a turn in the user's name they had not written.

What was built, with its reasoning:

- **`fetch_paper(mode="probe")`**, 1 500 characters of front matter with the
  version and the identity check. Verifying four candidates now costs about
  what one full read did, and the prompt says to probe before reading.
- **A round's reads run side by side** (`ai_agent.AgentLoop._groups`, four
  at a time), while changes stay serial so the user still watches them
  happen in order and the change budget stays exact. Their shared counters
  moved into one locked `Tally`, which also let a second search list a work
  it had already shown as one line instead of a whole record.
- **`kind` and `open_access` on `search_papers`**, routed to the registries
  that can answer them (an `article` search skips arXiv; a `preprint`
  search skips Crossref's bibliographic index). A filter belongs in an
  argument, not in the query text.
- **The reply waits on the card** (`ai_agent.PaperWait`). A blocked fetch
  now pauses the turn exactly as an Ask permission does, the calls of a
  round wait together, and the delivered PDF is read by running the same
  call again — so the model gets the paper where it asked for it. The
  automatic continuation message is gone; Skip can say what to do instead,
  and "don't wait in this chat" keeps the old card-under-the-reply flow for
  anyone who does not want to be held up.
- **`read_paper`**, the first delegation: one document and one question to
  a helper whose own conversation carries the windows, answering in a cited
  paragraph. This is where a subagent earns its keep — not in verifying
  four PDFs (the probe does that for less), but in "read these four papers
  and answer X", where the alternative is carrying four papers for the rest
  of the reply. Its token counts ride back on the action so the reply's
  footer still says what the whole answer cost.
- **Research as a background job** (`gamma/paper_research.py`), the same
  loop headless, filing a report page. The user starts it; a model that
  could put itself to work for ten minutes unasked is a different product,
  and the reasoning that made changes ask before they run
  ([ai-permissions.md](ai-permissions.md)) applies here too.

The two loop pauses are now one mechanism with two policies, which is why
the extraction came first: `AgentLoop` takes a `gate` (before a call) and a
`settle` (after a round), the chat passes an approval card and a browser
wait, and a job passes neither because nothing can ask a user who is not
there.

Not measured: the same question re-run end to end against the real
services, so the token figure above has no after to compare with. The
offline tests cover each piece (`test_ai_agent_loop.py` for the batching,
`test_fetch_handoff.py` for the four wait outcomes, `test_ai_tools_web.py`
for the probe and the helper, `test_paper_discovery.py` for the filters and
the dedup, `test_paper_research.py` for the job).
