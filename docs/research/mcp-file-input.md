# Getting pasted content and files into Gamma through MCP

November 2026, MCP spec 2026-07-28, Gamma on Python SDK 1.30. The question:
how can an outside assistant put content into a note the way a person
pastes into a block ([dev/mcp.md](../dev/mcp.md#editing-notes))? Text,
Markdown, tables, math and links are text, so the limits there are the
tool's shape and the request size. Pictures and files are bytes, and MCP
has no standard way to carry them from client to server. Read from the
spec and its blog, the File Uploads Working Group's repository, the
clients' docs and issue trackers, and hosted MCP servers' docs as of
2026-11-19. Which clients can show a question is in
[mcp-elicitation.md](mcp-elicitation.md) and is not repeated here.

"Unconfirmed" marks a claim that rests only on a third-party source, or
that was not found stated at all.

## What the specification offers

- **The revision.** 2026-07-28 is current
  ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).
  It drops protocol sessions, `Mcp-Session-Id` and the `initialize`
  handshake (SEP-2567, SEP-2575), and replaces server requests such as
  `elicitation/create` with Multi Round-Trip Requests: the server answers
  `resultType: "input_required"`, and the client calls again with
  `inputResponses` and the echoed `requestState` (SEP-2322). Roots,
  Sampling and Logging are deprecated; for Roots the suggestion is to "pass
  directories or files via tool parameters". Python SDK
  [v2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
  speaks it and still serves 2025-era clients; v1.x gets security fixes
  only. Gamma's asking-first rides on a stateful SSE session, which the new
  revision does not have. One write-up says `ctx.elicit()` raises
  `NoBackChannelError` on a 2026-07-28 connection
  ([dev.to](https://dev.to/krlz/mcp-went-stateless-what-the-2026-07-28-spec-actually-changes-273k));
  unconfirmed.
- **A tool call cannot carry a file.** `tools/call` takes `name` and a JSON
  `arguments` object
  ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).
  Image, audio and resource content blocks appear only in results. The spec
  sets no message size limit; the transport or the server does.
- **Nor can elicitation**
  ([elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation)).
  Form mode is "limited to flat objects with primitive properties only".
  URL mode could hand the person to an upload page, but the URL "MUST NOT"
  be pre-authenticated, the server "MUST" verify who opens it, and the
  client must show the full URL and not pre-fetch it. `accept` means only
  that the person agreed to open the page.

### Proposals for file input

None is in the spec.

| Proposal | What it proposed | Status |
|---|---|---|
| [SEP-1306](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1306) | A `binary` elicitation mode: the client uploads multipart to `uploadEndpoints` with the MCP token | Closed 2026-03-12, superseded by SEP-2356. Reviewers asked whether an upload reaches the same instance of a scaled-out server |
| [SEP-2356](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2356) | `inputFiles` beside `inputSchema`, the value an RFC 2397 `data:` URI; cites `openai/fileParams` | Closed 2026-06-26 in favour of SEP-2631 |
| [SEP-2631](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2631) | `x-mcp-file` on URI-string properties: inline `data:`, or `files/authorizeUpload` transfer descriptors that keep the bytes out of JSON-RPC | Closed 2026-10-14 in favour of the experimental extension |
| [SEP-2532](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2532) | `resources/stream` for large server-to-client files | Draft, sent back to the WG on 2026-11-06 |

**The File Uploads Working Group**
([charter](https://modelcontextprotocol.io/community/working-groups/file-uploads),
2026-04-23, led from Anthropic with OpenAI a member) wants tools to declare
file inputs "so that hosts can present native file pickers", and notes that
servers today "resort to prose instructions asking for base64 strings or
local paths". Its extension `io.modelcontextprotocol/files` was announced
on 2026-11-10
([blog](https://blog.modelcontextprotocol.io/posts/exploring-file-uploads-and-downloads-in-mcp/),
[draft](https://github.com/modelcontextprotocol/experimental-ext-file-uploads/blob/main/specification/draft/file-uploads.md)),
for 2026-07-28 and later, tools only:

- the schema marks a file input `x-mcp-file` (`{accept, maxSize}`); the
  argument is a `FileInput` (`{id, name?, mimeType, size, digest?, data?}`);
- small files go inline as base64, capped by the server's `maxInlineBytes`
  per request (the example: 1 MiB);
- larger ones get `resultType: "upload_required"` with upload targets (a
  URL, PUT or POST, headers, `expiresAt`); the client sends the bytes and
  calls again. The server "must check storage itself": "a retry is not
  proof that the bytes arrived".

The [repository](https://github.com/modelcontextprotocol/experimental-ext-file-uploads)
says it "has not been proposed as a SEP" and has no reference
implementation. No client implements it. Codex's flag-gated SEP-2631 client
([openai/codex#28337](https://github.com/openai/codex/pull/28337)) closed
unmerged on 2026-06-30.

## What clients can do

| Client | Shell | Can a file's bytes reach a remote MCP server? |
|---|---|---|
| Claude Code | yes | Through `curl` from Bash to a URL the server gives ([FutureSearch](https://futuresearch.ai/blog/mcp-large-dataset-upload/), [r2-uploader-mcp](https://github.com/dtinth/r2-uploader-mcp)) |
| Claude Desktop, claude.ai connectors | the cloud code sandbox only | Not directly; no Anthropic doc of an attachment reaching a connector tool was found |
| ChatGPT apps and connectors | the data-analysis sandbox, without general network ([Simon Willison](https://simonwillison.net/2026/Jan/26/chatgpt-containers/), [community bug](https://community.openai.com/t/chatgpt-5-6-uses-the-offline-python-shell-sandbox-to-do-http-requests-which-always-fail/1386960)) | Through `_meta["openai/fileParams"]`, ChatGPT's own extension ([reference](https://developers.openai.com/plugins/reference)) |
| Codex CLI | yes | Through `curl`, but network is off by default in `workspace-write` ([docs](https://learn.chatgpt.com/docs/agent-approvals-security)); `sandbox_workspace_write.network_access = true` turns it on |
| Cursor | yes | Through `curl` only; its [MCP docs](https://cursor.com/docs/mcp) say nothing of file inputs |
| VS Code Copilot | yes | Through the terminal only ([MCP docs](https://code.visualstudio.com/docs/agent-customization/mcp-servers); read through a search summary, unconfirmed) |
| DeepSeek Harness | not checked | not checked |

- **Claude Code.** Its sandbox is opt-in. With it on, `allowedDomains`
  start empty, and on Linux and WSL2 a sandboxed command's localhost is its
  own, so a Gamma on `127.0.0.1` is out of reach unless the command is in
  `excludedCommands`. MCP tools run outside the sandbox
  ([sandboxing](https://code.claude.com/docs/en/sandboxing)).
- **Base64 through the model breaks files.**
  [anthropics/claude-code#95688](https://github.com/anthropics/claude-code/issues/95688)
  reports about 1 MB of base64 for 765 KB of files, a 145 KB screenshot
  corrupted as the model retyped it, and a 64 KB PDF refused as "not a
  valid base64 string". Redmine's
  [#305](https://github.com/jztan/redmine-mcp-server/issues/305): a
  3,232-byte PNG lost 32 characters and was saved broken.
- **claude.ai and Claude Desktop.** There is no attachment path
  ([Kestrel](https://github.com/kurtbruns/kestrel/issues/830),
  [mcp-upload](https://github.com/imaad786/mcp-upload); unconfirmed). The
  code sandbox can `curl` an attached file to a signed URL when its egress
  allows the server's domain
  ([Help Center](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude);
  files up to 30 MB), though users report the allowlist not always taking
  ([#30112](https://github.com/anthropics/claude-code/issues/30112),
  [#19087](https://github.com/anthropics/claude-code/issues/19087)).
  Connectors run from Anthropic's cloud, so the server must be on the
  public internet
  ([Help Center](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).
  Whether a file picker works in an MCP App's iframe there is unconfirmed
  ([postiz#2099](https://github.com/gitroomhq/postiz-app/pull/2099),
  "verified locally only").
- **ChatGPT.** It passes `{download_url, file_id, mime_type?, file_name?}`.
  `download_url` is said to be a temporary bearer URL
  ([sunpeak](https://sunpeak.ai/blogs/file-uploads-mcp-apps/); unconfirmed),
  and a path such as `/mnt/data/image.png` said to be converted to the object
  ([community](https://community.openai.com/t/how-to-pass-generated-images-from-conversation-into-chatgpt-app/1369293);
  unconfirmed). The mobile apps send a bare `"chat_upload://image_0"`
  ([#185](https://github.com/openai/openai-apps-sdk-examples/issues/185)),
  and about 10% of calls arrive without the key
  ([#237](https://github.com/openai/openai-apps-sdk-examples/issues/237)).
  Whether `fileParams` applies to an unpublished developer-mode connector
  is not confirmed.

## How other servers take files

| Server | Pattern | Details |
|---|---|---|
| Notion | ticket, server fetch, inline text | `notion-create-file-upload` returns a short-lived URL with headers (20 MiB); its reply carries `suggested_markdown` to paste as is. `notion-create-attachment` takes inline text (200 KiB), a public HTTPS URL (5 MiB free, 50 MiB paid, 1 minute, no redirects, no headers or cookies, no private addresses) or a finished upload ([reference](https://developers.notion.com/guides/mcp/tools-files-reference)) |
| Slack | ticket, two tools | `slack_get_file_upload_url` then `slack_complete_file_upload`; the length is needed up front ([docs](https://docs.slack.dev/ai/slack-mcp-server.md), [API](https://docs.slack.dev/reference/methods/files.getUploadURLExternal/)) |
| Linear | ticket | `prepare_file_upload` with name, type and exact size returns a signed URL whose signature covers the type and the size; base64 `create_attachment` deprecated ([Manufact](https://manufact.com/blog/mcp-file-uploads), [StackOne](https://www.stackone.com/connectors/linear/)). Not in [Linear's own docs](https://linear.app/docs/mcp); the tool names and the 60 s expiry are unconfirmed |
| GitHub | text only | `create_or_update_file` takes UTF-8; a PNG cannot be committed ([#3312](https://github.com/github/github-mcp-server/issues/3312)) |
| Google Drive | inline | `create_file` takes `text_content` or `base64_content`, no URL ([reference](https://developers.google.com/workspace/drive/api/reference/mcp/tools_list/create_file)); the corruption reports above come from it |
| Figma | `upload_assets` | up to 10 MB each, 60 a call ([tools](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)); how the bytes travel is not documented |
| Canva | server fetch, ticket | `upload-asset-from-url` and `create-upload-url` ([tools](https://www.canva.dev/docs/apps/mcp/tools/)); the public-HTTPS rule is from listings, unconfirmed |
| Cloudflare R2 (community) | ticket | [r2-uploader-mcp](https://github.com/dtinth/r2-uploader-mcp): a presigned PUT for 5 minutes, then `curl`; R2 says to "treat presigned URLs as bearer tokens" ([R2](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)) |
| redmine-mcp-server | ticket | [PR #306](https://github.com/jztan/redmine-mcp-server/pull/306): `create_upload_ticket` → `upload_url`, `ticket`, `expires_at`, `max_bytes`; one `curl -H "X-Upload-Ticket: $T" --data-binary @f`; 15 minutes, single use, stored as a SHA-256, one error for wrong and unknown, a raw body ("multipart was dropped because it buffers the whole body before the size cap applies"), 50 MiB |
| FutureSearch | ticket | `request_upload_url` → an HMAC-signed URL for 5 minutes, a `curl_command` and `max_size_bytes`; works from claude.ai's sandbox with the domain allowed ([blog](https://futuresearch.ai/blog/mcp-large-dataset-upload/)) |
| mcp-upload (library) | ticket and a browser page | single-use 15-minute tickets bound to their owner (100 concurrent redemptions, one won), and a plain form at the same URL for clients without a shell ([repo](https://github.com/imaad786/mcp-upload)) |
| Supabase | none | its storage tools only list and configure buckets ([docs](https://supabase.com/docs/guides/ai-tools/mcp); from a mirror listing, unconfirmed) |

The patterns, in order of reach:

1. **A ticket, then attach by id or URL** (Notion, Slack, Linear, Canva,
   Redmine, FutureSearch, R2). The model handles a short handle; a shell,
   a sandbox with egress or a browser sends the bytes.
2. **The server fetches a public URL** (Notion, Canva; ChatGPT's
   `fileParams` comes to the same for the server).
3. **Inline base64 with a cap** (Drive; Linear deprecated it). Every byte
   is retyped by the model, and the reports above break between 3 KB and
   145 KB.
4. **A browser hand-off page** (mcp-upload's form, an MCP App's drop zone):
   the only route for a client with no shell, no egress and no
   `fileParams`.
5. **The files extension**, once a client has it.

Text edits elsewhere avoid retyping too: Notion's
[markdown update](https://developers.notion.com/reference/update-page-markdown)
takes up to 100 `old_str`/`new_str` pairs, a whole replace, or an insert
`after:`; Redmine moved to find/replace with an expected-SHA-256 guard
([#314](https://github.com/jztan/redmine-mcp-server/issues/314), about 14k
output tokens to retype 50k characters).

## Security practice

- **Tickets are bearer tokens**
  ([AWS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)):
  short-lived (Linear 60 s, FutureSearch and R2 5 minutes, Redmine and
  mcp-upload 15), single-use, bound to an owner, stored as a hash, one error
  for unknown and wrong, the size enforced as the bytes stream (GCS
  [`x-goog-content-length-range`](https://docs.cloud.google.com/storage/docs/xml-api/reference-headers),
  Linear's signed size, Redmine's raw body), and what arrived checked in
  storage before it is attached.
- **The files extension's client rules** fit any target a server hands
  out: https only, no redirects, addresses checked against the IANA
  special-purpose registries, no `Authorization` or `Cookie` headers,
  credentials only to the same origin, targets redacted in logs.
- **A hand-off page** must not carry a pre-authenticated ticket in its URL
  and must check that the person opening it is the token's user (the
  elicitation spec's phishing section); a link in a tool result needs the
  same.
- **Fetching a URL the model gave**
  ([OWASP SSRF](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)):
  http and https only; block private, loopback, link-local, metadata,
  multicast, `fc00::/7` and `fe80::/10`; unmap IPv4-mapped and NAT64
  addresses; check every A/AAAA answer and connect to the checked address;
  "Disable redirects". The
  [MCP security practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
  add: don't hand-roll IP checks, pin DNS, use an egress proxy.
- **The file itself**
  ([OWASP uploads](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)):
  allowed extensions, the signature checked as well as `Content-Type`,
  server-chosen names, a size limit, and active content in a served file
  kept in mind. A file can carry instructions aimed at the model, and a
  matching digest proves only that the bytes match
  ([fluidlabs](https://fluidlabs.com/resources/mcp-file-upload-download-new-pattern-agents)).

## What Gamma chose

Built as [dev/mcp.md](../dev/mcp.md#editing-notes) "The paste" and
"Pictures and files" describe.

- **Text is a paste.** `create_block` splits a Markdown passage as the
  editor's Paste as → Blocks does, in one op batch and one question, so the
  model sends a passage once rather than a call per block. The request body
  went from 64 KiB to 1 MiB to fit 100,000 characters in any JSON escaping.
- **Files go by patterns 1 and 2, in one tool.** `upload_file(url)` is the
  server fetch, through the guard every Gamma fetch uses
  (`net_guard.guarded_urlopen`): public addresses, the checked address
  pinned, the size capped. It follows redirects and checks each again,
  where OWASP and Notion refuse them; changing that would change every
  fetch Gamma makes. `upload_file(name)` is the ticket, after Redmine's
  rules: 15 minutes, single use, a SHA-256 kept, one 404 for a ticket unknown, used, expired or
  revoked,
  a raw-body PUT capped as it streams, and bound to the connection and
  workspace, whose Edit level is checked again at the PUT. It goes in a
  header, not the URL, since access logs keep paths. The answer carries the
  Markdown line to paste, as Notion's `suggested_markdown` does. The bytes
  go through the upload in parts that people's large files use, so the
  quota and content checks are the same.
- **No base64.** The corruption reports start at 3 KB, and a generated SVG,
  diagram or table belongs in the note as text.
- **No hand-off page.** The person dropping the file into the note in Gamma
  is already the paste, and needs no new routes or sign-in checks.
- **Storing asks nobody.** The note write that shows the file asks with it
  named, so a figure costs one question, not two.
- **Not taken yet:** ChatGPT's `fileParams` (not confirmed for a
  developer-mode connector) and the files extension (no client). Either
  would land in `upload_file`'s `url` route.

## Still open

- No real Claude Code or Codex session has run the ticket's command against
  Gamma yet.
- claude.ai and Claude Desktop can paste only with asking off, and send a
  person's file only from the code sandbox with egress to a public Gamma.
- The 2026-07-28 revision and SDK v2 change how asking works; asking in
  Gamma's own window for clients that cannot be asked waits for that move.
- ChatGPT, DeepSeek Harness, Figma's byte transport and VS Code's page were
  not checked first-hand.
