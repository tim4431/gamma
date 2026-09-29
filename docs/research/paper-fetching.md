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

Still unverified: real publishers (Science's Cloudflare, an institutional
SSO round trip, Atypon's `epdf` reader), whether a stored User-Agent makes a
transferred `cf_clearance` usable from a server on the browser's machine,
and the desktop app's path through the system browser.

Mechanics and permissions: [Agent tools](../dev/ai_tools.md).
