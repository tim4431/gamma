# GoodNotes and OneNote notebooks, and what a Gamma import would read of them

Survey 2026-10, for a GoodNotes import and an optional OneNote import, to
sit beside the Notability import
([dev/import_export.md](../dev/import_export.md#notability-notes)). Neither
vendor documents its format. What is known of `.goodnotes` is third-party
reverse engineering of files the app itself wrote between 2024-11 and
2026-09-30 (schema 24, 25 and 35, iPad and Mac), checked against the app's
own PDF exports and, by three writers, against imports into the Mac app.
What is known of OneNote rests on Microsoft's open specifications for the
container and the object model, on the ISF specification for the number
encoding its undocumented ink layer reuses, and on five independent
readers. What GoodNotes stores per stroke is already in
[ink-storage.md](ink-storage.md#goodnotes-goodnotes-schema-24--25--35):
this note links to it rather than repeating it, and says where the newer
evidence corrects or extends it ("What this corrects in ink-storage.md",
below). Dates are written as 2026-10-25.

Tags:

- **[V]**: read from the bytes of files the app wrote, and confirmed by at
  least two independent sources, or by one with explicit byte evidence, or
  by a converter that reproduces the app's own render or export.
- **[V1]**: one source.
- **[I]**: inferred, consistent with the samples but not proven.
- **[C]**: the sources conflict; the resolution follows.

## Scope

- **In: GoodNotes 6 and 7 `.goodnotes`** as exported by the iOS, iPadOS and
  Mac apps (the only platforms that write it), schema 24, 25 and 35, and
  the `.zip` forms around it: a folder export, which keeps the folder tree,
  and a manual backup `.goodnotes.zip`. GoodNotes 5 wrote the same
  container [V1]; GoodNotes 4 files are a different, undocumented format
  that GoodNotes 5's own converter stopped reading around 2020-01, and are
  out.
- **In, optional: OneNote** as the desktop app's `.one` section and
  `.onepkg` notebook exports (the MS-ONESTORE §2.3 revision store, in a
  Microsoft Cabinet), and as the FSSHTTPB "alternative packaging"
  (MS-ONESTORE §2.8) that every OneDrive or SharePoint download and OneNote
  for the web's "Export notebook" ZIP carry; `.onetoc2` for the section
  order. The Microsoft Graph API is weighed as the alternative and declined
  for a first version. `.onex` (rights-protected sections) and
  password-protected sections are out: no public reader decodes them.
- **The fallback for both**: the apps' own PDF exports, which Gamma's
  existing PDF and annotation imports already take
  ([dev/import_export.md](../dev/import_export.md#importing-annotations-embedded-in-a-pdf)).
  GoodNotes on Android, Windows and the web exports PDF only, so those
  users arrive this way whatever else is built.
- **Out**: writing either format. Nothing GoodNotes added in 2026 (Layers,
  Whiteboards, Text Documents, Quick Diagramming connectors, the Selective
  Eraser) has a published sample; its encoding is unknown.

## Sources

Facts may be taken from every source below; code only from the MIT, Apache,
BSD and CC0 ones. The GPL ones (goodparse, onenote-md-exporter,
ConvertOneNote2MarkDown), the MPL ones (onenote.rs, Joplin's vendored
converter), the AGPL repository and fixtures (Joplin, the `joplin/*.one`
files in onenote.rs), the LGPL one (libmson) and the unlicensed ones
(inkref, Federwerk, nnc) are facts only, the stance gnnote itself takes
(it runs goodparse as a separate-process oracle, "mere aggregation", and
copies nothing from the others). Microsoft's specifications are under the
Open Specifications Promise, which allows copies "to develop
implementations". Two projects flag a legal caveat: parser-for-goodnotes'
LEGAL-NOTICE says Goodnotes' Terms and Conditions "contain restrictions
concerning format analysis", and inkterop takes the position that
interoperability reverse engineering of an unencrypted container falls
under 17 U.S.C. §1201(f), "not legal advice". The clause itself was not
retrieved; see "Still open".

| Source | Licence | What it covers |
|---|---|---|
| gnnote, `jakubfabrici/notability-goodnotes`: [goodnotes-container.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/goodnotes-container.md), [goodnotes-stroke.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/goodnotes-stroke.md), [goodnotes-v35-strokes.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/goodnotes-v35-strokes.md), [goodnotes-elements.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/goodnotes-elements.md), [goodnotes-v35-elements.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/goodnotes-v35-elements.md), [goodnotes-v35-binding.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/goodnotes-v35-binding.md), [pdf.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/pdf.md), [ecosystem.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/ecosystem.md), [onenote.md](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/onenote.md), its [GoodNotes reader](https://github.com/jakubfabrici/notability-goodnotes/blob/main/gnnote/goodnotes/reader.py) and [OneNote reader](https://github.com/jakubfabrici/notability-goodnotes/blob/main/gnnote/onenote/reader.py) | MIT (clean-room; commit 0d765ea, 2026-10-03) | The densest byte-level documentation of `.goodnotes`: 13 app-written files 2024-11 to 2026-09-30 (5,677 elements) checked against six GoodNotes PDF exports (25 pages); every claim tagged with evidence; corrections to the other parsers. Its OneNote reader (2026-10-03) reads both packagings and OneDrive ZIPs on 56 sample sections, with one2html as its oracle |
| inkterop, `cable729/inkterop`: [goodnotes.md](https://github.com/cable729/inkterop/blob/main/docs/formats/goodnotes.md), [calibration-results.md](https://github.com/cable729/inkterop/blob/main/docs/calibration-results.md), [erase-audit.md](https://github.com/cable729/inkterop/blob/main/docs/erase-audit.md), [validated-writes.md](https://github.com/cable729/inkterop/blob/main/docs/validated-writes.md), [HANDOFF-note-apps.md](https://github.com/cable729/inkterop/blob/main/docs/HANDOFF-note-apps.md), [research.md](https://github.com/cable729/inkterop/blob/main/docs/research.md), [onenote.md](https://github.com/cable729/inkterop/blob/main/docs/formats/onenote.md), [isf.md](https://github.com/cable729/inkterop/blob/main/docs/formats/isf.md), the [fixture README](https://github.com/cable729/inkterop/blob/main/core/tests/fixtures/goodnotes/README.md) | MIT code, CC BY 4.0 docs, CC0 self-made fixtures (2026-07-11) | A GoodNotes 7.1.2 iPad calibration page (one row per tool, 91 records, checked against the app's export), the Mac fixture `gn-mac-mixed-pens.goodnotes`, import bisection in GoodNotes Mac (what a file needs to open), the erase audit; a classic-revision-store OneNote reader and an ISF reader verified against 11 Microsoft files |
| goodnotes-codec, `Taylor-Nilsen/goodnotes-codec`: [FORMAT.md](https://github.com/Taylor-Nilsen/goodnotes-codec/blob/2df312d030a326e0d92dd223c8c1d35b4b125ed9/docs/FORMAT.md), [schema.py](https://github.com/Taylor-Nilsen/goodnotes-codec/blob/2df312d030a326e0d92dd223c8c1d35b4b125ed9/goodnotes/schema.py), [paper.py](https://github.com/Taylor-Nilsen/goodnotes-codec/blob/main/goodnotes/paper.py), [model.py](https://github.com/Taylor-Nilsen/goodnotes-codec/blob/2df312d030a326e0d92dd223c8c1d35b4b125ed9/goodnotes/model.py), [pdf.py](https://github.com/Taylor-Nilsen/goodnotes-codec/blob/2df312d030a326e0d92dd223c8c1d35b4b125ed9/goodnotes/pdf.py) | MIT (created 2026-10-07) | GoodNotes' own field names, transcribed from the web app's Swift core shipped as WebAssembly; a lossless round trip claimed over 206 private documents (4,720 pages, 307,070 strokes); imports checked in GoodNotes Mac 7.0.60; the Essentials paper catalogue and geometry; events `#57`, `#63`, `#65`; fields `#11`, `#16`; kinds 20, 21, 22. Terse, no evidence pointers, several readings contradicted by gnnote |
| parser-for-goodnotes, `Kaih1825/parser-for-goodnotes`: wiki [02](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/wiki/02-archive-and-wire-format.md), [03](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/wiki/03-compression-and-tpl-binary.md), [04](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/wiki/04-stroke-geometry-and-rendering.md), [05](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/wiki/05-shapes-text-and-elements.md), [09](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/wiki/09-current-re-findings.md), [recording.py](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/src/goodnotes_re/recording.py), [LEGAL-NOTICE](https://github.com/Kaih1825/parser-for-goodnotes/blob/main/LEGAL-NOTICE.md) | MIT (self-described "vibe coding"; 2026-08-26) | Ribbon rendering at 99.79 % pixel agreement, eraser-cut outlines, `#55` reorder, sticky-note parent/child placement, the only audio-sync reader (`gn-recordings`, `gn-export-audio`); wrong on the `bv4-` header, `#9.#2` and the "MIME type" in the attachment index |
| goodparse, `franzthiemann/goodparse`: [README](https://github.com/franzthiemann/goodparse), [samples](https://github.com/franzthiemann/goodparse/tree/main/samples) | GPL-3.0 (its `pyproject.toml` says MIT: unresolved) | The public corpus `Test4`–`Test9.goodnotes` with GoodNotes' own PDF exports beside them (the ground truth gnnote used), and the 2024 `test`–`test3`; its README's A4/72-dpi and stride-12 readings are superseded |
| inkref, `juliank1m/inkref`: [FINDINGS.md](https://github.com/juliank1m/inkref/blob/main/FINDINGS.md), [PDF_FINDINGS.md](https://github.com/juliank1m/inkref/blob/main/PDF_FINDINGS.md) | none (all rights reserved) | Goodnotes 7.0.34 Mac: the live RocksDB store, the tombstone census over a real library (11,003 deleted records), the 1/132-in and 1/144-in calibrations against the app, synthetic strokes imported and rendered, the measured Editable PDF export |
| Goodnotes' help centre: [Import files](https://support.goodnotes.com/hc/en-us/articles/7353717816463), [Export documents or pages](https://support.goodnotes.com/hc/en-us/articles/7353742824975), [Editable and Flattened PDF](https://support.goodnotes.com/hc/en-us/articles/8537070839183), [PDF outlines and hyperlinks](https://support.goodnotes.com/hc/en-us/articles/7353742854543), [Change a page template](https://support.goodnotes.com/hc/en-us/articles/7353743758991), [Custom templates](https://support.goodnotes.com/hc/en-us/articles/7353728093199), [Auto Backup](https://support.goodnotes.com/hc/en-us/articles/7352786555279), [Manual backup](https://support.goodnotes.com/hc/en-us/articles/7353694866831), [Organize folders and documents](https://support.goodnotes.com/hc/en-us/articles/15303674514191-Organize-folders-and-documents-in-Goodnotes), [I can't search my notes](https://support.goodnotes.com/hc/en-us/articles/7353695209743), [Export errors](https://support.goodnotes.com/hc/en-us/articles/4819546709007), [Pen tool](https://support.goodnotes.com/hc/en-us/articles/7353756785679-Using-the-Pen-tool), [Pencil tool](https://support.goodnotes.com/hc/en-us/articles/10730874907023-Create-lively-drawings-with-the-Pencil-tool), [Highlighter](https://support.goodnotes.com/hc/en-us/articles/7353718215823-Highlighting-with-the-Highlighter-tool), [Why the highlighter is not behind the text](https://support.goodnotes.com/hc/en-us/articles/360002892176-Why-does-the-Highlighter-not-appear-behind-the-text-it-highlights), [Tape Tool](https://support.goodnotes.com/hc/en-us/articles/9489290046607-Use-the-Tape-Tool-to-hide-and-reveal-content), [Shapes](https://support.goodnotes.com/hc/en-us/articles/13682939148943-Draw-perfect-lines-curves-and-shapes), [Shape Tool](https://support.goodnotes.com/hc/en-us/articles/6553746937359-Draw-shapes-with-the-Shape-Tool), [Apple Pencil Pro](https://support.goodnotes.com/hc/en-us/articles/9757771783823-Utilize-the-new-features-of-Apple-Pencil-Pro), [Layers](https://support.goodnotes.com/hc/en-us/articles/16536297803535-Organize-content-with-layers), [Updates](https://support.goodnotes.com/hc/en-us/articles/14028970030351), [What's new July to September 2026](https://www.goodnotes.com/blog/whats-new-in-goodnotes-july-september-2026), the [App Store listing](https://apps.apple.com/us/app/goodnotes-ai-notes-docs-pdf/id1444383602), the [feedback board](https://feedback.goodnotes.com/forums/191274-customer-suggestions-for-goodnotes/suggestions/44389644-migrate-from-notability-in-native-format) | © Goodnotes | Menu paths, formats, limits, tool behaviour as the vendor states it; never format facts |
| [Apple's framed LZ4](https://developer.apple.com/documentation/compression/compression_lz4) | © Apple | The `bv41`/`bv4-`/`bv4$` frame |
| Precedents: [Graphite](https://github.com/Graphite-Note/Graphite) and its [issue #30](https://github.com/Graphite-Note/Graphite/issues/30), [sanenotes #805](https://github.com/swiftsaneai/sanenotes/issues/805), [MPU Talk 42590](https://talk.macpowerusers.com/t/how-to-transfer-my-existing-notes-from-goodnotes-to-notability/42590), [MPU Talk 10581](https://talk.macpowerusers.com/t/import-notability-into-goodnotes/10581), [Microsoft Q&A 5258013](https://learn.microsoft.com/en-us/answers/questions/5258013/migrate-goodnotes-to-onenote), [Notability: Importing Files](https://support.gingerlabs.com/hc/en-us/articles/206061357-Importing-Files), [Samsung Notes PDF import](https://www.samsung.com/us/support/answer/ANS10002404/), [MyScript Notes help](https://help.myscript.com/notes/organize-notes/create-notes/) | Apache-2.0 (Graphite); the rest © their authors | Who imports what, and what users complain about |
| [MS-ONESTORE v13.3](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/ae670cd2-4b38-4b24-82d1-87cfb2cc3725) ([PDF](https://officeprotocoldocs-f5hpbjgea6b8gneq.b02.azurefd.net/files/MS-ONESTORE/%5bMS-ONESTORE%5d.pdf)), [MS-ONE v20221115](https://officeprotocoldocs-f5hpbjgea6b8gneq.b02.azurefd.net/files/MS-ONE/%5bMS-ONE%5d.pdf) and its Learn pages ([jcidPageNode](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/e381b7c7-b434-43a2-ba23-0d08bafd281a), [jcidImageNode](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/b7bb4d1a-2a57-4819-9eb4-5a2ce8cf210f), [jcidEmbeddedFileNode](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/a665b5ad-ff40-4c0c-9e42-4b707254dc3f), [LastModifiedTime](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/3db4c369-593a-4c27-8fac-825511e82454)), [MS-ISF](https://download.microsoft.com/download/0/B/E/0BE8BDD7-E5E8-422A-ABFD-4342ED7AD886/InkSerializedFormat%28ISF%29Specification.pdf) | Open Specifications Promise | The revision store, the FSSHTTPB packaging, property sets, JCIDs and property ids, Time32; ISF's signed multibyte integers and packet GUIDs. Ink, printouts and math are absent from MS-ONE |
| [m-siemens.de, "Decoding OneNote's file format secrets"](https://m-siemens.de/blog/2026/05/decoding-onenote-s-file-format-secrets/) (2026-05-16); onenote.rs ([README](https://raw.githubusercontent.com/msiemens/onenote.rs/master/README.md), [CHANGELOG](https://raw.githubusercontent.com/msiemens/onenote.rs/master/CHANGELOG.md), [wiki: Ink Object Format](https://github.com/msiemens/onenote.rs/wiki/Ink-Object-Format), [ink.rs](https://raw.githubusercontent.com/msiemens/onenote.rs/master/crates/parser/src/onenote/ink.rs), [docs.rs](https://docs.rs/onenote_parser/latest/onenote_parser/contents/struct.InkStroke.html)); [one2html 2.0.0](https://github.com/msiemens/one2html/releases/tag/v2.0.0) | MPL-2.0 (onenote.rs), MIT (one2html) | How the ink layer was decoded (the ISF varint and the length prefix), the ink object hierarchy, math, printouts as PNG, handwriting recognition output; the reference parser Joplin builds on |
| Joplin: [onenote-converter](https://raw.githubusercontent.com/laurent22/joplin/dev/packages/onenote-converter/README.md), [the importer](https://raw.githubusercontent.com/laurent22/joplin/dev/packages/lib/services/interop/InteropService_Importer_OneNote.ts), [import help](https://joplinapp.org/help/apps/import_export/), releases [3.2](https://joplinapp.org/news/20250114-release-3-2), [3.6](https://joplinapp.org/news/20260505-release-3-6), [3.7](https://joplinapp.org/news/20260831-release-3-7), [PR 14305](https://api.github.com/repos/laurent22/joplin/pulls/14305), forum threads [37720](https://discourse.joplinapp.org/t/onenote-importer/37720), [48580](https://discourse.joplinapp.org/t/penned-note-imports-generate-excessive-svg-resources-and-cripple-syncing/48580), [43826](https://discourse.joplinapp.org/t/onenote-zip-import-incomplete/43826), [47924](https://discourse.joplinapp.org/t/importing-from-onenote-on-windows/47924) | AGPL-3.0-or-later (repo), MPL-2.0-no-copyleft-exception (the vendored parser) | The file-based precedent and its bug history |
| Obsidian Importer: [onenote.ts](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/src/formats/onenote.ts), [inkml.ts](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/src/formats/onenote/inkml.ts), [ink-svg.ts](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/src/formats/onenote/ink-svg.ts), [auth.ts](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/src/formats/onenote/auth.ts), the file importer's [ink.ts](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/src/formats/onenote-file/semantic/ink.ts), [onex.ts](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/src/formats/onenote-file/onex.ts), [fixtures SOURCE.md](https://raw.githubusercontent.com/obsidianmd/obsidian-importer/master/tests/onenote-file/fixtures/SOURCE.md), [release 2.4.0](https://github.com/obsidianmd/obsidian-importer/releases/tag/2.4.0), [help](https://obsidian.md/help/import/onenote) | MIT (its ink codec ported from OfficeIMO, MIT) | The Graph precedent (`includeInkML`, paging, throttling) and the offline file importer with a CAB/LZX decoder |
| [OfficeIMO.OneNote](https://raw.githubusercontent.com/EvotecIT/OfficeIMO/master/OfficeIMO.OneNote/README.md), [pyOneNote](https://github.com/DissectMalware/pyOneNote), [Apache Tika's OneNoteParser](https://raw.githubusercontent.com/apache/tika/main/tika-parsers/tika-parsers-standard/tika-parsers-standard-modules/tika-parser-microsoft-module/src/main/java/org/apache/tika/parser/microsoft/onenote/OneNoteParser.java), [py-onenote-parser](https://raw.githubusercontent.com/Kev744/py-onenote-parser/main/README.md), [OneNote2Obsidian](https://raw.githubusercontent.com/lalogeorgi/OneNote2Obsidian/main/README.md), [onenote-md-exporter](https://raw.githubusercontent.com/alxnbl/onenote-md-exporter/main/README.md), [libmson](https://api.github.com/repos/blu-base/libmson), [one-extract](https://api.github.com/repos/volexity/one-extract), [file(1) on `.onepkg`](https://mailman.astron.com/pipermail/file/2022-September/000825.html), [CABTrap PoC](https://github.com/Edubr2020/CABTrap_OneNote2007) | MIT, Apache-2.0, Apache-2.0, MIT, MIT, GPL-3.0, LGPL-2.1, BSD-3-Clause | The other readers, what each decodes, and the `.onepkg` container |
| Microsoft support and Graph: [Export as PDF (desktop)](https://support.microsoft.com/en-us/onenote/onenote-help-and-learning/export-notes-from-onenote-as-a-pdf), [Export and import notebooks (web)](https://support.microsoft.com/en-us/office/export-and-import-onenote-notebooks-a4b60da5-8f33-464e-b1ba-b95ce540f309), [Print in OneNote for Windows 10](https://support.microsoft.com/en-us/onenote/print-notes-in-onenote-for-windows-10), [Mac Save as PDF](https://support.microsoft.com/en-au/onenote/onenote-for-mac-help-and-learning/export-notes-as-a-pdf-file-in-onenote-for-mac), [iPad print](https://support.microsoft.com/en-us/onenote/print-notes-from-onenote-on-your-iphone-or-ipad), [MC1104312](https://mc.merill.net/message/MC1104312), [MC1139479](https://mc.merill.net/message/MC1139479), [MC1011142](https://mc.merill.net/message/MC1011142), [Get OneNote content](https://learn.microsoft.com/en-us/graph/onenote-get-content), [Input and output HTML](https://learn.microsoft.com/en-us/graph/onenote-input-output-html), [Permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference), [Throttling limits](https://learn.microsoft.com/en-us/graph/throttling-limits), [Redirect URIs](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url), [OneNote Ink beta APIs (2017-07-07)](https://devblogs.microsoft.com/microsoft365dev/onenote-ink-beta-apis/), [OneNote API throttling (2016)](https://devblogs.microsoft.com/microsoft365dev/onenote-api-throttling-and-how-to-avoid-it), [Webhooks deprecation](https://devblogs.microsoft.com/microsoft365dev/onenote-webhooks-api-deprecation), [get-pages?search deprecation](https://devblogs.microsoft.com/microsoft365dev/onenote-get-pagessearch-api-deprecation), Q&A [666739](https://learn.microsoft.com/answers/questions/666739/retrieving-inknode-from-onenote-using-graph.html), [5431088](https://learn.microsoft.com/en-us/answers/questions/5431088/onenote-and-exporting-saving-notebooks), [293292](https://learn.microsoft.com/en-nz/answers/questions/293292/how-to-decode-*-one-files-in-onenote-online-format), [10738143](https://learn.microsoft.com/en-us/answers/a/10738143), [5341085](https://learn.microsoft.com/en-us/answers/questions/5341085/is-there-a-way-to-migrate-onenote-notebooks-contai) | © Microsoft | Export paths per app, retirements with dates, the Graph endpoints, permissions, registration and limits |

## GoodNotes, the file

### The container

- A `.goodnotes` is a plain deflate ZIP, no encryption, members in the
  order GoodNotes writes them: `document.info.pb` (optional, always 0
  bytes), `index.search.pb`, `index.notes.pb`, `search/<UUID>`,
  `notes/<UUID>`, `index.events.pb`, `thumbnail.jpg`,
  `index.attachments.pb`, `attachments/<UUID>`, `schema.pb`; attachment
  members keep the source file's mtime [V: gnnote on 13 files, inkterop,
  inkref, codec]. Member order is not enforced on import [V1: inkref].
- Every `.pb`, `notes/` and `search/` member is a stream of
  `<varint length><protobuf message>` records, never one message; a 0-byte
  member is a valid empty stream; the group wire types 3, 4, 6 and 7 never
  occur, so a reader may refuse them [V: gnnote, p4g; goodparse's "current
  GoodNotes uses group wire types" is wrong].
- `schema.pb` is one varint field, `08 18` = 24, `08 19` = 25, `08 23` =
  35: the largest schema value any record in the file carries. 25 appears
  only on pencil strokes (`#7.#3 = 5`), 35 only on `#21` text elements;
  every event still says 24 in the iOS 26 build of 2026-09-30 [V: gnnote].
  p4g's "contains 35" is true only of such files, and inkref's and
  inkterop's "schema 24 versus 25 tracks the writing platform" is explained
  by the Mac files holding pencil strokes [C, resolved: content, not
  platform].
- `index.notes.pb` is one 82-byte record per page, `{#1 notes-layer UUID N,
  #2 "notes/" + N}`; deleted pages stay listed with a 0-byte member; the
  list is neither display nor, since Test9, creation order: "treat the
  list purely as the set of pages" [V: gnnote, codec]. inkterop's "document
  order" is superseded.
- The page UUID `P` the events use and the member's `N` differ by one:
  `N = P + 1` as a 128-bit integer **with carry** (3 of 36 pairs carry into
  the next digit) [V: gnnote, inkterop "entity = content − 1", codec].
  goodparse and p4g match the first 32 hex characters and survive the
  carry by accident; inkterop found that random entity UUIDs leave the
  page blank on import.
- `index.attachments.pb` is `{#1 attachment id, #2 member name}` per
  attachment: no MIME type, size or role. The type is sniffed (`%PDF-`,
  `\x89PNG`, `\xff\xd8`, `....ftyp`), the size is the `#6` event's `#5`,
  the role follows from what references the id. A PDF inserted as a page
  (Test9) has a content-derived v5 id whose bytes sit under a different v4
  member name, so `#6.#1` and `#6.#2` must both be read [V: gnnote]. p4g's
  "MIME type stored for recordings" is contradicted by the record shape
  [C].
- `thumbnail.jpg` is a baseline JPEG of the first page in display order,
  499 px high [V: gnnote; inkref calls it the cover, which is that page].
  The search index (`index.search.pb` and `search/<X>`) is optional and
  asynchronous: three of the six 2026 files have none [V: gnnote].
- The live Mac app keeps no `.goodnotes`: RocksDB (`notes_main.db`),
  SQLite, loose attachments and an event-sourced op log, with a Yjs CRDT
  library linked; the records have the export's shape; the export
  `documentType` enum holds only `GOOD_NOTES_5`, "no GN5 → GN6 format
  break" [V1: inkref, Goodnotes 7.0.34 Mac].

### The event journal is the document model

- Every record is `{#1 entity UUID, #E body}`, `E` the event kind; every
  body ends with `#10 fixed64` = float64 milliseconds since the Unix epoch,
  `#11` an event UUID, a device id, a sequence number and a schema, at
  per-kind field numbers (13/14/15 for most; 14/15/16 for `#6`; 15/16 for
  `#2`). Sequence numbers rise per device with gaps; timestamps never fall
  [V: gnnote]. codec names the sequence a "hybrid logical clock" and the
  attribute events last-writer-wins [V1 naming].
- Nearly every mutable value is a register `{#1 version, #2 random uint32
  nonce}`, version 1 or 2 on creation and +1 per edit, the nonce distinct
  on all 5,677 elements [V: gnnote, codec's "version stamp"].
- Kinds seen in app-written files: `#30` document created (title `#2.#1`,
  recognition language `#9` "en_US"/"de_DE"/"auto", `"P"` in `#6`, a
  constant UUID in `#3`/`#7` of unknown meaning), `#31` renamed, `#6`
  attachment added (`#5` exact byte size, `#6` document id, `#12` =
  `{1, 1}` on paper PDFs and empty on PNG, JPEG, M4A, a sticker PDF and
  the photo-PDF behind one page), `#2` paper template created, `#54` page
  created (`#3.#1` template, `#4.#1` order key, `#17` a constant 48-byte
  colour block), `#3` page re-bound to another template (new in 2026-09;
  the last wins, proven against the exports by counting ruled rows),
  `#56` page deleted, `#10` current-page pointer, `#102` notes layer
  written, `#103`–`#106` search and recognition bookkeeping (`#106` an
  inventory of a page's element UUIDs with their clocks), `#15` unknown,
  `#160`/`#163` recording started and stopped [V: gnnote]. Documented by
  codec alone, from Mac exports: `#57` bookmark `3 {1 1}`, `#63` page
  rotation `3 {1 quarter turns}`, `#65` outline entry `4 order key, 5
  title`, `#161`/`#210`/`#213` transcript timings [V1]. By p4g alone:
  `#55` page reordered `{#2 P, #3 {#1 new key}}` [V1]. codec reads
  `#6.#12` as `pageRange {1 first, 2 count}` and says `#6` is required or
  GoodNotes ignores the attachment; gnnote's empty `#12` on the photo-PDF
  behind a page does not fit that reading [C, open].
- The event semantics the parsers disagree on (`#10`, `#102`, `#104`,
  `#105`: current page, layer written, "page-link", "paper applied") are
  all bookkeeping a reader can ignore [C, harmless]; inkterop found by
  bisection that a file missing `#2`/`#54`/`#105` opens with zero pages,
  which matters only to a writer [V1].
- Nothing structural changed between the 2024-11 files and the iOS 26.6.1
  build of 2026-09-30: `#30, #6, #10, #54, #56, #102, #103–#105` have
  byte-identical shapes; the new kinds are event `#3`, the inserted-PDF
  `#2` variant and element kinds `#9` and `#21` [V: gnnote].

### Pages and their order

- Display order is the `#54.#4.#1` order keys (overridden by a later
  `#55`) compared bytewise as ASCII; the alphabet seen is `!0-9A-Z_a-z~`;
  pages created together get `<4 random><digit>`, an appended page a key
  whose first character steps `4 → A → F → K → O`, an inserted page an
  in-between key. The sort reproduces the page order of all six GoodNotes
  PDF exports, 25 pages [V: gnnote, codec; p4g takes the latest key per
  page by timestamp].
- A deleted page is a `#56` event; its `index.notes.pb` entry and member
  stay (0 bytes in the 2026 files) [V: gnnote].
- The minimal reading algorithm, checked against all 13 files and the six
  exports (gnnote v35-binding §9): attachments `{id → member}` from
  `index.attachments.pb`; walk the events in file order collecting `#6`
  id-to-storage aliases, `#2` templates, `#54`/`#3` page-to-template,
  `#54`/`#55` order keys, `#56` deletions; for every `index.notes.pb` entry
  compute `P = N − 1`, skip deleted pages, resolve the template and the
  PDF's MediaBox, scale 6/11, sort by key [I, as a procedure; each step V].

### Paper templates

- **Every page is bound to a one-page PDF attachment**, built-in paper
  included: `index.notes.pb` N → P = N − 1 → `#54` (or the latest `#3`)
  `#3.#1` = template T → `#2` (entity T): `#4` attachment id, `#5` 1-based
  PDF page, `#8` canvas size, `#9` name, `#7` line spacing (optional),
  `#18` layout block (optional) → `index.attachments.pb` → a PDF whose
  MediaBox is the page's size. Verified on all 33 live pages of 13 files;
  several pages may share one template [V: gnnote, inkref, inkterop,
  codec].
- Built-in papers are one-page PDF-1.7 files with `/Producer (svg2pdf)`, a
  CalRGB `/srgb` space, a content stream that fills the page with the
  paper colour and, for ruled, grid and dotted papers, Form XObjects of
  one filled 0.5-pt rectangle per rule (colour 0.8157 0.8235 0.8275) or
  about 4,300 small circles; 892–953 B blank, 1,904 B wide-ruled, 3,224 or
  3,492 B grid, 41.8 KB dotted. GoodNotes' own export of a paper-only page
  is exactly the template's fills [V: gnnote; inkref].
- Names, `#2.#9`, are `<catalogue UUID>_<size>_<n>_<m> - <Colour>`. The
  leading UUID is the ruling design, the size token `standard` or `a4`
  (Letter and Tabloid tokens are unobserved and presumed by analogy [I]),
  the colour the suffix; the two digits are unknown (`_standard_1_1 -
  Blue` and `_standard_1_1 - Yellow` share them, `_a4_1_7 - Green`
  exists), so codec's "`_standard_1_<colour>`, 1 yellow 2 white 3 black"
  is contradicted [C: gnnote's bytes win]. The catalogue, from codec's
  Mac exports, with gnnote's sightings in brackets: blank
  `B03314A5-9B9F-556B-B373-4678E13D0DAC` [seen], dotted
  `CC365888-75C9-5DBF-B1D4-A7E8E0AD3B7F` [seen], squared
  `9FE8F365-4BEE-5057-8573-1A56C77CAC19` [seen, Yellow and White],
  narrow_ruled `238EE861-EE9A-51BD-B010-832CD3C50D2C` [codec only, V1],
  wide_ruled `AA273C17-F440-5125-B34E-F9C3C62F89E2` [seen]; colours
  white, yellow, black at codec, plus Blue, Green and Baby Blue at gnnote
  [V for the four seen twice, V1 for narrow ruled].
- Geometry, in PDF points from the page's top-left: dotted r 0.5 dots
  every 16; squared 0.5-pt lines every 16; narrow ruled 0.5-pt rules every
  17.5 from 50; wide ruled every 25 from 49.5; rules stop 30.95 pt
  (narrow) and 13.45 pt (wide) above the bottom of Standard paper; line
  colour (0.816, 0.824, 0.827) on white (1, 1, 1) and yellow (0.9725,
  0.9686, 0.9137) paper, (0.345, 0.349, 0.357) on black (0.2) paper
  [V1: codec, measured from the Standard PDFs]. gnnote's `#18` numbers
  agree: `#18 = {1 writing area {origin, size}, 2 spacing minus line
  width, 3 line width, 5 1 light / 2 dark}` in canvas units, `#18.#2`
  28.4167 + `#18.#3` 0.91667 = 29.333 canvas = 16 pt for grid and dots,
  44.92 + 0.9167 = 45.83 = 25 pt wide-ruled, writing area origin (44,
  58.67), size (canvas − 88, canvas − 117.33) [V].
- `#7` (29.333 canvas = 16 pt) and an `#18` block also sit on blank White
  paper, whose PDF is a plain fill; only the presence of `#18.#3`
  coincides with drawn rules, 13 of 13 catalogue templates. A reader
  classifies from `#18.#3` or from the PDF, never from `#7` [V1: gnnote].
- The size menu: "Goodnotes Standard, A7 (iPhone), A6, A5, A4, A3, Letter,
  Tabloid", portrait or landscape; Standard is 455.04 × 588.45 pt,
  MediaBox `[0 0 455.04 588.45]`, canvas 834.24 × 1078.825 [V: the vendor's
  template page, codec's `SIZES`, gnnote, inkref]. A third-party guide's
  "1536 × 1960 px" for Standard fits no single ppi [C: low-quality source,
  ignored].
- Custom templates are any PDF or image added as a cover or paper (only
  the first page of a multi-page PDF; the size category does not resize
  the file); a page's template is changed from the page's More menu [V:
  vendor]. Whether an image template is stored raw or wrapped in a
  page-sized PDF is unknown (codec's writer wraps). Two further `#2`
  variants exist for user PDFs: used *as a template* (`#6 = 1`, a bare
  UUID as `#9`) and *inserted as a page* (no `#6`, no `#9`, value-less
  registers) [V1: gnnote, Test9].
- `417A3734-…` ("Blue", 950 B: a fill plus one 22.75-pt vertical band) and
  `BF443295-…` ("Baby Blue") are not rulings; the band matches GoodNotes'
  simple notebook cover, and the page is page 1 of the notebook. Covers
  are ordinary pages bound to a cover template [I: gnnote; no source
  verifies it].

### Imported PDFs

- A user PDF is stored byte for byte, producer string and mtime included
  (Test9: a 779,994-B Excel PDF of 595.2 × 841.68 pt, a 321,483-B Figma PDF
  of 1280 × 904.9995, a 143,137-B Quartz strip of 454.9091 × 143.2785);
  `#6.#5` equals the member size in 25 of 25 cases [V: gnnote].
- Each page has its own template and so its own size: one document mixes
  A4, landscape 1280 × 905 and a 455 × 143 strip; "page size is per page
  and not per document" [V: gnnote, inkref]. goodparse reads one size per
  document and mis-sizes such files.
- `#2.#5` is the 1-based page of the attachment (p4g reads it, codec
  writes it); every imported PDF in the public corpus is one page, so a
  value above 1 is unobserved in app output, and whether a multi-page
  import is one shared attachment or split into one-page attachments is
  unknown; an importer resolves `#5` and tolerates both [V1 for the
  field; I for the rest].
- Rotation is an event, `#63 … 3 {1 quarter turns}` [V1: codec]; no
  `/Rotate` was observed in any template PDF; there is no page crop: every
  reader that checked exports saw the full MediaBox [I].
- GoodNotes indexes the PDF's text into `search/<attachment id>` (3.9–10.8
  KB on Test9's PDFs; a 5-byte stub for unindexed ones); it is derived
  data [V: gnnote].
- Word and PowerPoint imports are converted to PDF (iPadOS and iOS only)
  [V: vendor]. Whether a PDF's own annotations and outline survive inside
  the attachment is implied by the export article (Editable keeps "the
  original PDF's" outline and links) but unchecked on bytes [I].

### Units

- All element geometry is in canvas units, origin top-left, y down, 1 unit
  = 6/11 PDF point (`#2.#8` = MediaBox × 11/6 computed in double and stored
  as float32: 595.28 → 1091.3466796875, 841.89 → 1543.4649658203125); the
  ratio is 6/11 for all seven page sizes in the corpus, so "derive the
  scale from the width ratio" [V: gnnote; inkref three independent ways,
  1/132 in]. Stroke widths use a second unit, 1/144 in: a flat stroke's
  `W` draws at `W/2` pt (below). goodparse's "PDF points, A4" and
  inkterop's "PDF points @ 72 dpi" are off by 11/6 [C, resolved by the
  exports]; inkref's "US Legal" for 792 × 1224 pt is Tabloid [C, a label].
- Gamma's frames are also points from the top-left, y down: the GoodNotes
  canvas maps with one scale and no flip.

### Versions and platforms

- The current app is "Goodnotes: AI Notes, Docs, PDF" 7.1.32 (listing read
  2026-10-25), iOS/iPadOS 18.6+, macOS 15.6+; 7.0.0 shipped 2025-09-23 and
  dropped the number from the name; 7.1.2 was on an iPad by 2026-07-10,
  7.0.34 on a Mac in 2026-08, 7.0.60 is codec's import target [V: App
  Store, inkterop, inkref, codec]. The "GoodNotes 6" labels in gnnote's
  and inkterop's docs are loose; Test6–8 came from an iOS 16.7.16 device
  that cannot run 7.1.x, so only Test9 and the Mac exports are surely from
  2026 builds [I].
- The file carries a title (`#30.#2.#1`, `#31` renames), a creation time
  (`#30.#10`), a recognition language and device ids, nothing else about
  the library: folders, favourites and tags are not in it (Auto Backup
  says it does not propagate favourites); the outline is (`#65`) [V:
  gnnote, codec, vendor]. The modified time is the last event's timestamp
  [I].

### Acquisition paths and limits

- `.goodnotes` is written by iOS, iPadOS and the Mac app only. iOS: the
  library item's menu → Export, or the Share icon → Export All, or the
  thumbnail view → Select → Export (pages), or a folder → Export (one
  `.zip` keeping the folder tree); Mac: Documents view → item menu →
  Export → Goodnotes, with options to include the page background,
  annotations and sticky notes; Auto Backup (iOS and iPadOS only) writes
  "Goodnotes Document" files to Google Drive, Dropbox, OneDrive or WebDAV
  about every 12 h; Manual Backup writes a `.goodnotes.zip` restorable
  directly on iOS only [V: vendor]. Android, Windows and the web import
  `.goodnotes` and export PDF only; the request to change that is open
  since 2023-04-29 [V: vendor, feedback board]. A share sheet or AirDrop
  delivers the file; the Mac's library folder does not [V1: inkref].
- Sizes seen: 15–140 KB for notebooks of a few pages of ink, shapes and
  text; 1.1 MB for
  one page of 5,000 records (941 KB of `notes/`, 903 KB of `search/`
  uncompressed); 2–5 MB with photos or PDFs; a page member of about 356
  KB per 1,000 synthetic strokes [V: directory listings, inkref].
- Import caps stated for Android, Windows and the web: 200 MB per
  `.goodnotes` and 1 GB per PDF for subscribers; free accounts 3 notebooks
  and 5 MB per file [V: vendor]; no article restricts export [I]. An
  export can fail with "Attachment not found" after an interrupted iCloud
  sync, and the vendor's recovery (export PDF, re-import) loses
  editability [V: vendor].
- What a `.goodnotes.zip` backup holds beyond one `.goodnotes` per
  document (a manifest, the top-level layout) is unverified [I].

## GoodNotes, the strokes

The per-field account of a stroke is in
[ink-storage.md](ink-storage.md#goodnotes-goodnotes-schema-24--25--35);
what follows is what a reader needs beyond it, and where the 2026-10
evidence differs.

### The record

- An element is a (metadata, content) pair, in every schema: metadata
  `{#1 UUID, #2 clock, [#3 1 = deleted], [#4 attachment UUID], [#6 UUID],
  #8 device id, #9 per-device counter, #14 5381, #16 schema, [#20 parent]}`
  and a content record with exactly one top-level field, its kind: `#7`
  ink, `#1` image, `#8` RTF text, `#9` shape fill, `#21` text or shape box,
  `#20` sticky note, `#22` line, `#11` math; 5,677 of 5,677 and 254 of 254
  paired [V: gnnote; inkterop's "schema-24 files are flat" was its own
  unpaired stream, refuted]. GoodNotes' names (codec's schema): `3
  deleted`, `8 replicaId`, `9 logical_timestamp`, `14 mergeableHash` (5381
  = djb2 of ""), `20 parentId` [V1 naming].
- The ink message `#7`: `1 id` (the UUID again), `2 data` (the geometry
  blob), `3 type`, `4 color {1 r, 2 g, 3 b, 4 a}` as fixed32 floats with
  zero components omitted, `5 blendMode` (1 = MULTIPLY, the highlighter),
  `6 translation` (the lasso offset added to every point), `7 position`
  (draw-order index, member index in a group), `9 shape`, `10 elementId`
  (group), `11 lineStyle`, `14 deleted`, `15 version` (= metadata `#2`),
  `16 erasers`, `17 tag`, `18 ignoredInAIChecks`, `19 logical_group`, `20
  tapeStyle`, `21 schemaVersion`, `22 mathSuggestionId` [V for the fields
  seen, V1 for the names]. `#11`, `#16`, `#17`, `#19` and `#22` never occur
  in gnnote's 13 files.
- The blob is Apple framed LZ4 around a Troy Hanson `tpl` image: `bv41 u32
  raw u32 payload <LZ4 block>` chunks of at most 32 KiB raw (a block may be
  larger than its input), `bv4- u32 raw <bytes>` with an **8-byte** header
  when LZ4 would not shrink it, `bv4$` at the end; p4g reads two words
  after `bv4-` and silently drops such strokes [V: gnnote, 254 of 254
  re-encoded byte for byte]. The `tpl` image is self-describing: `v` u16,
  `u` u32 holding float32 bits, `A(x)` count-prefixed, `S(…)` struct;
  `f` is 8 bytes per gnnote, p4g and inkref, 4 per inkterop and codec, and
  only ever occurs with count 0 [C, moot].
- Five format strings cover every stroke of the corpus (5,931): `#3`
  absent and `vuA(v)A(S(uu))A(S(uuuu))vA(f)` (or without the trailing
  `vA(f)`, the older sub-version that survives copy and paste) is the
  constant-width "flat" pen, highlighter and shape stroke; `#3 = 1` and
  `vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)` the variable-width "ribbon";
  `#3 = 4` the same with a leading width word (one stroke, 2026-06); `#3 =
  5`, schema 25, the pencil [V: gnnote, codec's `STROKE_TYPE`: 0
  CONSTANT_WIDTH, 1 VARIABLE_WIDTH, 4 DYNAMIC_WIDTH, 5 PENCIL; 2 and 3 are
  legacy, unobserved].

### The geometry

- **Flat**: `[0] v` sub-version, `[1] u` = float32 `W`, `[2] A(v)` command
  kinds (0 moveTo starts a sub-path and takes one `A(S(uu))` point, 1
  quadCurveTo takes one `(cx, cy, x, y)`), `[5] v = 1`, `[6] A(f)` empty.
  The quads are true quadratic Béziers: the control deviates from the
  chord midpoint by median 0.09, p99 1.25, max 11.4 units over 43,206
  segments, and the app's export draws each as the cubic `c1 = P0 + 2/3(C
  − P0)`, `c2 = P1 + 2/3(C − P1)` to 0.0002 pt [V: gnnote]. p4g and
  goodparse treat the control as a point ("~0.1-unit error for app-written
  strokes, wrong in principle"). An empty TPL is 62 bytes: a shape or a
  tombstone [V].
- **Ribbon**: `[1] A(v)` centreline commands, `[2]` move floats, `[3]`
  quad floats, `[4]`–`[10]` a prebuilt outline (per-segment command counts,
  codes 0 move, 1 quad, 2 cubic, 3 arc `(cx cy r a0 a1)`, 6 elliptical arc
  `(cx cy rx ry a0 a1 rot)`, the points, cubics, arcs and clockwise flags).
  The per-point third float `r` is the **half-width in canvas units**:
  every cap arc has radius `r`, and the export's rect equals `(min(x − r),
  min(y − r), max(x + r), max(y + r)) × 6/11` to 0.3 pt on 55 of 59 live
  ribbons; `r` varies smoothly (0.35–1.0 in Test9 handwriting), "the
  pressure and velocity response is already baked in" [V: gnnote, codec's
  "per-point half widths"]. GoodNotes names the centreline commands `0
  moveTo (x y w)`, `1 quadCurveTo (cx cy cw x y w)`, `4
  moveToWithRotation`, `5 quadCurveToWithRotation` [V1 naming]: the first
  triple of a panel is a control point by name, an on-curve point by
  gnnote's geometric test; GoodNotes places it within about 0.1 unit of the
  chord, so either reading reproduces the export [C, harmless]. The app
  fills the stored outline non-zero; p4g's filled panels plus a hairline
  reach 99.79 % pixel agreement [V]. Strokes of 1,439 and 1,699 points
  span several LZ4 blocks; p4g drops their first point, goodparse returns
  None [V].
- **Flags 4 and 5**, the "WithRotation" layout: each panel is 9 floats
  `(x1, y1, w1, x2, y2, w2, alt1, alt2, k)` and the anchor 4 `(x, y, w,
  angle)`; on the tape bands the extra columns are the constants (0, 0,
  0.1), the caps are elliptical arcs with `rx = r/10`, so the band ends
  flat [V: gnnote]; on inkterop's iPad 7.1.2 fountain row they vary with
  the Pencil's angle (1.0–1.7 rad), "parsing it as triplets yields phantom
  points" [V1]. The fountain pen reacts to Apple Pencil Pro rotation and
  has a "Tip Flatness" setting [V: vendor]. Reading: flags 4/5 appear
  whenever GoodNotes has a nib angle to record, tape always and the
  fountain pen with tilt input, while `#20 {#1 ""}` alone marks the tape
  [I].
- **Type 4** (one stroke, ex2): gnnote and p4g read its panels as `(x1,
  y1, x2, y2, r1, r2)`, codec writes `(cx, cy, cw, x, y, w)` and the Mac
  accepts it [C, unresolved].
- **Pencil** (`vuA(v)A(S(uuuuu))A(S(u×11))…`): `[1] u` = `W`, one 5-tuple
  `(x, y, a, b, c)`, then N 11-tuples `(u32 seed, x1, y1, a, b, c, x2, y2,
  a, b, c)`, one sub-path per stroke, the trailing five arrays empty. On
  Mac files `a = π/6`, `b = π/3`, `c = 0` are constants; on the iPad file
  `a` 0.281–0.640 varies slowly, `b` 0.877–1.030 is nearly constant per
  stroke, `c` 0.073–2.376 varies point to point. gnnote reads `c` as the
  UITouch force (0 to about 4), `a` the altitude and `b` the azimuth;
  codec names the point `x y azimuth altitude force` and writes "30
  degrees azimuth, 60 degrees altitude" for a mouse; the force column is
  agreed, the order of the two angles is not [C, open]. The seed word is
  a random texture seed [V]. goodparse's stride-12 "x, y, pressure" with
  "zero-pressure sentinels" is this layout misread [V: gnnote].

### Widths: the rendered-width rule

- A flat stroke's `W` and a shape's `#9.#15` draw at **exactly `W/2`
  pt** in GoodNotes' own exports: 1.299 → 0.6496, 1.559 → 0.7795, 12 → 6,
  24 → 12, 36 → 18, 38.398 → 19.199, 17 distinct values over 87 strokes and
  30 shapes; inkref calibrated the same against the Mac app with ten
  strokes from 0.5 to 24 units, "mean ratio 2.0000, spread 0.0000" [V:
  gnnote, inkref]. The export pads a stroke's bounding box by `W × 72/132`,
  which is why gnnote's first draft said `W × 6/11` (8.3 % too thick);
  goodparse's `W × 0.25` is half the exported width; codec's PDF exporter
  strokes at `W × 6/11` (9 % wide) and is unchecked against the app;
  inkterop reports `W` as points [C: the exports win]. Whether `W/2` is
  also the on-screen width inside the app is unmeasured.
- A ribbon's full width is `2r × 6/11` pt; tape's `r` is 9.0 (9.82 pt);
  a pencil's `W` is only a nominal size: the app rasterises pencils
  (one image per stroke in the export), and the measured widths are
  0.54–0.99 `W` at force 0, median 0.45 `W` with force, and two strokes
  with identical records rendered 2.4 × apart, so a setting outside the
  record is involved [V measurements: gnnote; no rule]. Defaults seen on
  the 7.1.2 iPad: ball 1.559 (0.78 pt), highlighter 24 (12 pt), tape r 9
  [V1: inkterop].

### Colour and the highlighter

- `#4` is RGBA float32 in 0..1 with 0.0 components omitted; opaque black
  is `{#4 1.0}` alone [V].
- The highlighter is a flat stroke with `#5 = 1` and alpha exactly 0.5 (62
  of 62 and 12 of 12; every other stroke 1.0), round caps and joins,
  exported as an ordinary stroked path with `CA 0.5`, one per stroke; its
  "Draw in Straight Line" option stores `#9.#1` lines with an empty TPL,
  still `#5 = 1` [V: gnnote; inkterop's calibration]. GoodNotes names
  `#5` `blendMode` with 1 = MULTIPLY [V1 naming]; whether the screen's
  multiply differs visibly from the export's 50 % alpha is unknown.
- **Draw order is record order and the highlighter draws on top** of
  earlier ink: the help centre says "the highlighting will be on top of
  that content, just like regular pen strokes" and that a transparent
  colour "blends with the text rather than goes behind it"; a 2025 update
  added a manual "send to back" that reorders records [V: vendor, gnnote].
  codec adds that images and widgets always draw under ink [V1].

### Erasing

- **A whole-stroke erase leaves a tombstone pair**: metadata `#3 = 1` with
  content `#14 = 1`, the TPL the 62-byte empty header, a UUID occurring
  nowhere else, `#9` empty but on 4; 1,636 of 5,677 elements in gnnote's
  corpus, and 11,003 of 11,003 records of a real library's `deleted`
  bucket against 0 of 58,064 live ones [V: gnnote, inkref]. "Empty
  geometry ⇔ tombstone or auto-shape, 100 of 100: there is no empty live
  stroke" [V: gnnote].
- inkterop's erase audit of its iPad 7.1.2 page reads the same bytes the
  other way: "erasing removes the ink record from the page file", and the
  empty `#14 = 1` re-records "are NOT erase tombstones" (a trial
  tombstone rule dropped 2 visible strokes), paired with live strokes by
  the reused `#7` draw-order index [V1]. The readings reconcile: a reader
  that skips metadata `#3 = 1`, content `#14 = 1` and any stroke with no
  points and no `#9` renders exactly what the app renders under either
  [C, resolved for a reader].
- **Partial erases** are stored three ways in the sources, and which the
  current build writes is undocumented: several sub-paths in one record
  (flags `[0, 1×9, 0, 1×9]`, two start points, on one legacy-format Test5
  record; "no current-format stroke in the corpus has more than one
  sub-path") [V1: gnnote; p4g's corpus "verified the mechanism at 99.79
  %"]; the stroke trimmed in place with the same number of live records
  and none tombstoned (the standard eraser, Mac 7.0.34) [V1: inkref];
  `#16 erasers`, repeated `PEraserStroke` with template
  `vS(vvvvvvvv)S(uuuuuu)` "which GoodNotes subtracts from this ink" [V1:
  codec, absent from every public file]; and a content kind `10 penEraser
  {1 id, 2 commands, …, 5 constantRadius}` exists in the schema [V1
  naming]. The eraser's cut geometry is "exact closed vector contour
  panels" on ribbons [V1: p4g]. The 2026 Selective Eraser and the
  "erase highlighter only" mode have no sample [C, open].
- The clocks carry no geometry: `#15` echoes metadata `#2`, versions are 2
  on untouched strokes and 3–17 after edits; `#106` is a per-page
  inventory of element UUIDs and clocks, "no stroke geometry is ever
  stored in the events log" [V: gnnote, inkterop].

### Shapes and fills

- A recognised shape is a stroke whose TPL is empty and whose `#9` holds
  parametric geometry in canvas units, `#6` applied: `#1` repeated points
  (2 = a line, 3–5 = a polyline, closed when first = last, two identical
  points = a dot of diameter `W/2` pt), `#2 {P0, C, P1}` one quadratic
  (the export's cubic matches `c1 = P0 + 2/3(C − P0)` to 0.01 pt), `#3
  {centre, (w, h)}` an axis-aligned rectangle (0.00 pt), `#4 {centre,
  (a, b), θ}` an ellipse rotated by `θ` radians in y-down coordinates (to
  0.0001 on 12 ellipses), `#15` f32 width drawn at `W/2` pt, `#5 {#2 1}`
  on 37 of 38, unknown; 41 shapes verified against exports [V: gnnote;
  codec names `STROKE_SHAPE = {1 polyline, 2 quadCurve, 3 rectangle, 4
  ellipse, 5 lineStyle, 15 thickness}`]. p4g reads `#9.#2` as a polyline
  (0.4 pt off, cosmetic); goodparse drops `#9` shapes [C, resolved]. A
  rotated rectangle has never been observed.
- **Fill** is a second element of kind `#9` (`fillShape = {1 id, 2 rect, 3
  position, 4 shapes, 5 originalStrokeIds, 6 translation, 7 color, 8
  elementId}`): `#4` repeats the outline's geometry byte for byte, `#5`
  names the outline stroke (13 of 13 resolve on the same page; 5 precede
  their parent), `#7` is the stroke's RGB at **alpha 0.1**; the export
  draws it at `fill_opacity = 0.1` under or over the outline (both
  orders occur); absent for open shapes and for closed shapes drawn with
  fill off; a tombstoned fill has NaN rects; `#9.#2` is a bounding box,
  not a clip [V: gnnote, Test6–8]. The vendor: with Fill on "every closed
  shape you draw will be filled with a transparent version of the
  selected color" [V].
- Dashed ink is `#11 lineStyle {2 {1 lineDashLengths (packed float32,
  multiples of the width), 2 lineCap 0 BUTT 1 ROUND 2 SQUARE}}`; the Mac
  app has no dashed pen and draws such ink solid; the iPad pen presets
  offer Solid, Dashed and Dotted [V1 naming: codec; vendor]. p4g also
  reads an optional dash array from `#9.#5.#1`, unverified; the TPL
  `A(f)` is empty in every observed stroke [C, open].
- The newer Shape tool writes kind `#21` boxes and kind `#22` lines
  (below, "the other content"); arrows exist only there.

### Groups, tape, time and audio

- A lasso move adds `#7.#6 {dx, dy}` to every stored point and to `#9`
  geometry (1,205 strokes confirmed against renders; Test9's 44 grouped
  strokes with `(−488.608, 627.542)` land to 0.05 pt); offsets accumulate;
  grouped strokes share `#10` and a `#7.#7` clock with a member index;
  "grouping only, no geometric effect" [V: gnnote, codec].
- `#20 {#1 ""}` on a ribbon with flags 4/5 and constant `r` is the **Tape
  tool** (hide and reveal, solid or patterned, its own palette): opaque,
  colour (0.925, 0.325, 0.294) in the samples, flat elliptical caps [V:
  gnnote; vendor]. inkterop calls the same signature "marker"; the help
  centre has no marker tool [C, resolved by the vendor's tool list].
- A stroke holds **no timestamp**: metadata `#8` is the device id (an
  early reading as nanoseconds was wrong), `#9` a per-device counter not
  increasing in file order; the only times are the events' `#10` [V:
  gnnote]. Replay timing exists only through audio: event `#160
  audioNoteCreated {1 audioId, 2 attachment, 3 doc, 4 durationFlicks, 5
  references {stroke UUID → {1 timestampFlicks, 2 pageId}}, 6 position}`
  with `FLICKS_PER_SECOND = 705_600_000` [V1 naming: codec]; p4g models
  the same numbers as a 31.25 kHz Mach clock scaled by 625/441 to land
  strokes within 50 ms of the voice, an empirical fit [V1]. The unit is
  disputed; the tick is one per stroke, never per sample [C, open].
- The 2026 Layers feature has no field in any schema seen (candidates by
  name only: metadata `10 logical_group`, stroke `19 logical_group`) [I];
  stabilisation, smoothing and preset settings (pressure sensitivity, tip
  sharpness, dash density) are not in the record [V as absences].

### What this corrects in ink-storage.md

- "Erased strokes are removed from the page file, not tombstoned in
  place": a whole-stroke erase leaves a tombstone pair in the file
  (gnnote's 1,636, inkref's 11,003); inkterop's calibration page is the
  one source for removal, and its "re-records" are those tombstones. The
  statement that every live record renders stands.
- "Schema 25 pairs them strictly as a journal": every schema does.
- "A partially erased stroke stays one record with sub-paths" is one
  legacy-format record; inkref saw in-place trimming and codec names
  `#16 erasers`; the current build's form is open.
- "The 31.25 kHz event clock [inferred]": GoodNotes' own name for the
  unit is flicks (705,600,000 per second); p4g's clock is a fit to it.
- "Renderers that draw highlighters first then pens match the app's
  screenshots [inferred]": the vendor says the highlighter draws on top in
  record order, and "send to back" reorders records. For Gamma the
  choice is in the decisions below.
- "Fountain and brush are not distinguishable per stroke" stands [V:
  inkterop's calibration, every field identical]; codec's table assigning
  `4 DYNAMIC_WIDTH` to the brush is unverified on any current build [C].
- New since the note: the control point of a flat stroke's quad is a true
  control, not an on-curve point (p4g and goodparse read it as one), and
  the `bv4-` chunk has an 8-byte header (p4g reads 12 and drops the
  stroke).

### Field by field onto `gamma-ink` v2

The target is the stroke file of
[dev/handwriting.md](../dev/handwriting.md#the-stroke-file-gamma-ink-v2)
(`tool`, `brush`, `pen`, `size`, `color`, `opacity`, `band`, `dash`,
`shape` with baked `pts`, `fill`, `from`, the channels `x y p t a z w o`;
`w` a multiplier of `size`, `o` written by the pencil alone from its law,
`t0` never invented, band 0 under the ink, dash geometry from `size`).
Samples are `(stored + #6) × 6/11` pt, y down, no flip [V]; `size × max(w)
≤ 100` and `w` in 0..4 are the file's caps.

| GoodNotes | `tool` | `brush` | `pen` | `size` (pt) | `color`, `opacity` | `band` | `dash` | `shape`, `fill` | channels | lost |
|---|---|---|---|---|---|---|---|---|---|---|
| Ball pen: flat, `#5` absent | `pen` | `monoline` | false | `W/2` | `#4` RGB, `#4.A` (1) | 1 | `#11` when present | — | `xy`; the quads flattened (the control honoured) to samples at most 1 pt apart as the Notability import flattens cubics, then decimated at 0.1 pt; a sub-path (each `0` command) its own stroke | nothing [V geometry; I for the split] |
| Highlighter: flat, `#5 = 1` | `highlighter` | — | false | `W/2` (24 → 12) | RGB, `#4.A` (0.5) | 0 (the default, omitted) | — | a straight one: `shape line` | `xy` | GoodNotes' on-top MULTIPLY becomes Gamma's under-the-ink multiply (decision below) [V fields] |
| Fountain or brush: ribbon, `#3 = 1` (and 4) | `pen` | none (the plain Pen) | true | `2 × max(r) × 6/11` | RGB, A | 1 | `#11` | — | `xyw`, `w_i = r_i / max(r)` (so `w ≤ 1`, never clamped), samples the centreline's triples in order, flags 4/5 read as 9-float panels | the prebuilt outline and its flat nib caps; fountain versus brush; a later brush restyle re-bakes from `p = 0.5` since no pressure exists [V for `r`; I for the choice of `max`] |
| Pencil: `#3 = 5` | `pen` | `pencil` | true when the force column varies, else false | `W/2` nominal | RGB, A | 1 | — | — | `xypaz` with `p = c ÷ 25/6` (`notability_map.MAX_FORCE`, UIKit's maximum), `a`/`z` from the angle columns only where they vary (the Mac constants π/6, π/3, 0 are "no tilt", as the Notability import treats 1.0 and π/2), then `w` and `o` baked by Gamma's own pencil law from them, exactly what `restyleBrush` would write; a force-0 pencil is `pen: false`, even, no `o` | GoodNotes' width and darkness law (unknown), its grain; the angle order is disputed, so `a` and `z` may be swapped until a calibration file settles it [I throughout] |
| Tape: `#20 {#1 ""}` | `pen` | `monoline` | false | `2r × 6/11` (9.82) | RGB, 1 | 1 | — | — | `xy` | the hide-and-reveal behaviour and the flat caps; kept opaque so what it hid stays hidden [I] |
| Auto-shape `#9` | as its stroke (`#5` decides) | `monoline` | false (a shape is even) | `#9.#15 / 2` | RGB, A | by tool | `#11` | `#1` of 2 → `line`; `#3` → `rect`; `#4` with θ = 0 → `ellipse`; `#1` closed with 4 → `triangle`; any other polyline, a `#2` quadratic and a turned ellipse → a plain even stroke along the curve, as the Notability import does; `fill {color: RGB, opacity: 0.1}` from the kind-9 companion keyed by its `#5` | `pts` baked by `ink.shape_samples` | a rotated rectangle (unobserved), the `#9.#5` flag [V geometry; I mapping] |
| Dashed `#11` | | | | | | | `dashed` when the on length exceeds a tenth of the width, else `dotted` (the lengths are multiples of the width; Gamma derives its own) | | | the exact lengths [V1 naming; I rule] |
| Tombstone, deleted page, empty live stroke | skipped | | | | | | | | | nothing: the app does not render them [V] |

- **z-order** is the record index after the tombstones are dropped,
  within one ink group per page; Gamma draws every band 0 before band 1,
  which only moves the highlighters (decision below) [V for the order].
  Groups (`#10`) are not kept: Gamma has none.
- **Stroke ids** are `n0`, `n1`, … per file through `ink.group_files`, as
  the Notability import packs them, so the same notebook stores the same
  files in any workspace; the GoodNotes UUID is not kept [decision].
- **`t`, `t0`, `from`** are never written: no per-sample time exists, the
  audio tick is per stroke and its unit disputed, and a sub-path split is
  not a Gamma cut.
- **What cannot be recovered**, as absences in the file [V]: fountain
  versus brush; the pencil's force-to-width and darkness laws and the
  setting that renders identical records 2.4 × apart; the preset's pressure
  sensitivity, tip sharpness and dash density; erased ink; lasso history;
  Layers.

## GoodNotes, the other content

- **Images** (kind `#1`; metadata `#4` = attachment): `#1 {#1 E, #2 rect
  {origin, size} the placement in canvas units with the raster's aspect,
  #3 rect {centre, size} the visible rect (equal to `#2` in all 8), #4
  attachment, #5 clock, [#6 kind: absent PNG, 1 JPEG, 3 sticker PDF;
  codec's enum 0 PNG, 1 JPEG, 2 JPEG 2000, 3 PDF, 4 HEIC, 5 GIF], [#7
  group], #15 clock, #18 24}`; verified against renders (image rect ×
  72/132 correlates 0.96 with the PNG); the bytes are the attachment as
  is, pixel density not stored [V: gnnote]. An EXIF-rotated JPEG
  (Orientation 6, 3840 × 2160) is stored unchanged with `#2` holding the
  displayed 360 × 640, and the export draws it through a 90° matrix [V1:
  gnnote, Test9]. Crop and rotation: p4g reads `#3.#3` radians and crops
  to the intersection when `#3.#2 ≠ #2.#2`; codec says `#2` is the
  rotated image's bounds, `#3 {center, size, 3 angle}`, and "cropping
  writes a new, cropped attachment"; gnnote never saw a non-zero angle or
  a crop in 8 images [C, open]. A die-cut sticker is an image whose
  attachment is a one-page vector PDF (4,113 B, MediaBox 254 × 214) at a
  `#2` size equal to the MediaBox in canvas units, its lettering separate
  `#21` elements grouped with it [V1: gnnote, Test9].
  - Gamma, as built: a picture block through `notability_map.picture_block`
    ([dev/pictures.md](../dev/pictures.md)), its frame `#2 × 6/11` and its
    turn (`#3.#3`, radians) as `angle` in degrees, the bytes stored as they
    came (`note_app_import.AppImport.store_picture`), never decoded: the
    EXIF orientation is applied where the picture is drawn, as for any
    picture. A crop (`#3` other than `#2`) is not applied: the picture
    comes in whole, counted. A sticker PDF is rasterised to a PNG through
    PyMuPDF (`goodnotes_import._sticker`), once for all the pictures that
    show it, its longer side twice that of the largest of their frames and
    at most 1,024 px.
- **Legacy text, kind `#8`** (every schema ≤ 25 file, the iOS 16 builds of
  2026-09 included; 21 instances): `{#1 E, #2 outer rect, #3 text frame =
  outer inset by 10 units, #4 {1.0, _, _, 1.0}, #5 clock, #6 RTF bytes, #7
  RGB fill (alpha omitted = transparent), #9 RGB border, #10 f32 padding
  10, #15 clock, #18 {#2 5.0}, #19 {#4 0.2}, #20 ".tb-0", #27 24}`. The
  RTF is Cocoa `NSAttributedString` output (`{\rtf1\ansi\ansicpg1252
  \cocoartf2709 … \fonttbl … HelveticaNeue; HelveticaNeue-Bold;
  HelveticaNeue-Italic …}`): `\fsN` in half-points of canvas units (`\fs48`
  → 24 units → 13.09 pt), `\cfK` into `\colortbl`, `\sl-559` a fixed line
  height, `\b`, `\i`, `\ul`, `\strike` runs; the frame origin matches the
  export's glyph origin to 0.06 pt on 19 of 19 [V: gnnote]. p4g falls back
  to CP950 for its Chinese samples [V1]; link storage in `#8` is unknown.
- **Schema-35 text, kind `#21`** (Test9, the iOS 26 build; the typed box
  as well as sticker lettering; no `#8` in that file): `{#1 E, #2 35, #3
  clock, [#7 group clock, #8 group UUID], #9 kind (0 shape, 1 textbox),
  #20 transform {#1 origin (top-left), #2 rotation rad, #3 uniform
  scale}, #21 {#2 fixed (w, h, +inf)} | {#3 auto-size constraints}, #22
  geometry, #30 fill RGB(A), #31 border, #32 text {#1 {#2 Apple-LZ4
  protobuf blob, #3 8-byte content hash, #4 1}, #2 content size, #5
  default style {#30 family "Helvetica Neue", #40 size 24.0, #60 −60, #70
  −20}, #10 insets (10, 10, 10, 10), [#11 line height]}, #33 shadow}`. The
  blob is a run list `{#1 UTF-8 text, #2 style {#3 RGBA, [#30 family], #40
  size, #60 weight (−30 bold Futura), #70}, #3 paragraph {#2 line spacing,
  [#4 alignment 2 centre]}}`, −404 = inherit; positions, sizes (24 → 13.09
  pt, 55 → 30.0 pt), colours and the rotation about the origin match
  `Test9.pdf` to 0.01 pt [V: gnnote]. p4g adds `f1` strike, `f2`
  underline, `f50` italic, `f3.f3` list type (bullet, numbered) and
  alignment 1/2/3; codec adds char attribute `4` = link URL and the
  outline style `{1 width, 2 {1 solid | 2 {1 dash, 2 gap}}, 3 {1 color}}`
  [V1 each]. The content hash's algorithm is unknown (codec: not FNV,
  djb2, xxh64, MurmurHash3, MD5 or SHA prefixes; the app accepts any).
  - Gamma: a fixed-width text box per `notability_map.text_box`'s rules,
    frame `(#20.#1 + inset) × 6/11`, `size` the majority run's size × 6/11,
    `color` the majority colour, `bg` from `#30` (`#7` on `#8`), the runs
    to Markdown through `box_markdown` (bold, italic, strike; underline
    and alignment not kept, as in the Notability import), a link run as a
    Markdown link, a page link (`https://app.goodnotes.com/documents/
    ?anchor=…`) as plain text; a rotated or scaled box upright on its
    centre with the scale applied to its size [I]. `#8` needs a small
    reader of Cocoa RTF (the font and colour tables, `\fsN`, `\cfN`, the
    four run marks, `\par`, `\'hh` in the declared code page and `\uN`):
    the stdlib has none, and the box's text is the only thing lost
    without it [I].
- **Shape-tool boxes** are kind `#21` with `#9 = 0` and `#22` geometry:
  `{1 {1 corner radius}}` a rounded rectangle (the radius clamped to half
  the short side, so a pill), `{2}` an ellipse, `{3 {1 vertices in the unit
  square}}` a polygon, with `#30` fill, `#31` outline (width, dash,
  colour) and optional text [V1: codec, p4g's `_parse_type35_shape`;
  gnnote verified the `#20` transform]. **Lines and connectors** are kind
  `#22`: `20 {1 start, 2 mid handle on the curve, 3 end}` straight or
  curved, `21 {1 start, 3 end, 5 knee, 4 startDirection}` elbow, `30`/`31`
  arrowheads (0 none, 1 line/open V, 2 triangle/filled; p4g adds 3+ dot),
  `32` style [V1: codec and p4g agree on the record, differ on the enum].
  Neither `#20` nor `#22` occurs in any public file.
  - Gamma: a `#21` rectangle or ellipse without rotation → a `rect` or
    `ellipse` shape stroke with its fill at the stored alpha (Gamma's
    `fill.opacity` written when it is not 0.25), the corner radius lost; a
    polygon → `polygon` when its params fit `shapes.js`, else the outline
    as a stroke; a straight `#22` → `line` or `arrow` (one head; a second
    head drawn into the samples as the Notability import draws a curved
    line's); a curved or elbow line → a plain stroke; the box's text → a
    text box over it [I].
- **Sticky notes**, kind `#20`: like `#21` with `30` the card colour, `31`
  the text (an LZ4 run blob), `32` author id, `33` author name (codec);
  p4g: `#20 position`, `#21 size (default 256 × 256)`, `#40 1 = expanded`,
  and child strokes, shapes and text carry the note's UUID (metadata `#20
  parentId`) and are positioned relative to the note's origin; a
  collapsed note renders as an icon [V1 each; no public sample]. Exports
  can include or exclude them [V: vendor].
  - Gamma: a filled rectangle stroke in band 0 at the card colour, as the
    Notability import's stickies, with the note's text as a text box over
    it and its children offset by the note's origin; a collapsed note as
    a note-yellow auto text box of its text at the icon's place, as a
    `/Text` sticky imports from a PDF [I].
- **Math**, kind `#11`: `9` the LaTeX, `11` the original strokes [V1:
  codec; inkref lists "math group"]. Gamma: a display-math text box of the
  LaTeX in its colour, as the Notability import's formula; whether the app
  still shows the strokes is unknown, so whether to import them too waits
  for a sample (open).
- **Audio**: a recording is an M4A (`ftyp`, AAC) attachment (73,229 B for
  one session; `#6.#12` empty) bound by `#160` and ended or deleted by
  `#163`; a deleted recording keeps its `#6` though its member is gone; a
  recording is document-level, and `#5` names the page of each timed
  stroke [V: gnnote, p4g]. p4g extracts the track and renders synchronised
  video [V1]. The community salvages audio by unzipping the file [V: MPU
  Talk].
  - Gamma: a recording block per `#160`
    ([dev/recording.md](../dev/recording.md)) with one segment, its
    length from the file's movie header (`recording.mp4_duration_ms`),
    `started` from the event's `#10` timestamp [I], placed after
    everything else as the Notability import places its recordings; no
    stroke is put on it, since Gamma's replay places a stroke by
    per-sample times, which the file lacks.
- **Links and outlines**: a link is a `#21` run attribute; page links are
  `https://app.goodnotes.com/documents/?anchor=<base64 "page-<id>">#<base64
  doc id>`; GoodNotes-made links stay clickable even in a flattened PDF
  [V1: codec; vendor]; outline entries are `#65 {4 order key, 5 title}`,
  bookmarks `#57` [V1: codec]. Gamma has no page anchor an import could
  target and no outline on a sheet notebook; an outline entry can become
  the sheet's title, whose content is a sheet block's title
  ([dev/notebooks.md](../dev/notebooks.md)); bookmarks are dropped with a
  warning [I].
- **The search index**: `search/<N>` holds the recognised text of a
  handwritten page with per-word rectangles and stroke references (up to
  903 KB), or a typed page's words with element indices and box-local
  rectangles; built in the background by "Index PDF and Handwritten
  Notes" (iOS), present in three of the six 2026 files [V: gnnote]. Gamma
  has no place for recognised handwriting (Notability's `HandwritingIndex`
  is not read either); open.

## The GoodNotes PDF exports as the fallback

- **Editable** (iOS and Mac): keeps the original PDF's outline and
  hyperlinks; "images, text boxes, handwriting, and highlighting can be
  selected, moved, or resized in another PDF viewer"; handwriting not
  searchable. **Flattened**: one layer, smaller, loses outlines and the
  original's hyperlinks (GoodNotes-made links stay), and with "Enable
  Handwriting Recognition" the handwriting becomes searchable, selectable
  and copyable as recognised characters, an invisible text layer no
  source has measured [V: vendor, article updated 2026-09-16; I for the
  layer]. Android, Windows and the web export PDF only, which inkref holds
  to be raster on the strength of a feature request [V1, weak].
- Measured on the app's own files (Goodnotes 7.0.34 Mac → "macOS 26.6
  Quartz PDFContext", and the iOS exports of Test4–9): ink is one `/Ink`
  annotation per stroke, not page content; `/InkList` is the densely
  flattened centreline (4 authored points → 31, 9 → 81), in page points
  (`y = 100` units → 54.55 pt); colours bit-exact; the extracted bbox sits
  0.55–1.62 pt inside the authored one; `/BS /W` is exactly `W/2` for
  flat strokes (0.5 → 0.25 … 24 → 12.0); the `/AP /N` form holds the
  cubic path (`m l c v y re h`, `S`-painted = ink, `f`-painted = not);
  a highlighter has `/CA 0.5` [V: inkref, gnnote]. A shape's `/InkList`
  holds only the Bézier anchors (an ellipse becomes 4 points) while the
  appearance draws the curve; fountain pens and tape are one filled blob
  per segment with `/BS /W` about 45 % of the drawn width; the pencil is a
  raster `/Stamp`; images, shape fills and stickers are `/Stamp`
  annotations (a photo keeps its JPEG bytes as `/DCTDecode` with the EXIF
  turn as a matrix); typed text is real text (HelveticaNeue 13.09 pt,
  Futura-Bold for sticker letters) or `/FreeText` whose `/DA` always says
  13 pt whatever the size; the page size is the paper's MediaBox and the
  paper is drawn as page content in both modes; `Test9.pdf` mixes
  annotated pages (2, 4) with flattened ones (3, 5–7) in one export [V:
  gnnote]. An imported PDF's OCR layer "may be stripped" by export [V:
  vendor].
- **What Gamma's annotation import makes of an Editable export today**
  (`ink.from_pdf_ink`, `_text_box_from_annotation`;
  [dev/import_export.md](../dev/import_export.md#importing-annotations-embedded-in-a-pdf)):
  each `/Ink` becomes one `pen: false` polyline stroke per `/InkList`
  path, tool `pen`, `size` the `/BS /W` (at least 0.2), `opacity` the
  `/CA`, `dash` from `/BS /S /D`, band 1. So a ball pen comes in at its
  true width; a highlighter as a translucent pen drawn over the ink, not
  Gamma's highlighter under it; a fountain stroke as its centreline at
  about 45 % of its width with no `w`; a shape as its anchor polygon (an
  ellipse a quadrilateral); a pencil, every image, sticker and fill not
  at all (`/Stamp` is not imported); a `/FreeText` box at 13 pt whatever
  it was; the paper and the original PDF as the page's own content (a
  built-in paper's ruling becomes part of the PDF, which a sheet cannot
  carry); no audio, no stickies, no page structure beyond the PDF's. gnnote
  reads the same annotations back to editable strokes with the appearance
  path preferred and blob widths estimated, and leaves page content alone:
  "ink drawn into the page content stays part of the background" [V: its
  pdf.md]. That is the ceiling of any PDF route: a Flattened export is
  page content and yields nothing editable.
- Improvements to the existing import that the measurements justify
  without a `.goodnotes` reader: read the `/AP /N` path when it agrees with
  `/InkList` (exact shapes), take the highlighter from `/CA ≤ 0.6` (inkref's
  `w > 2.5` fallback misclassified a 3-pt pen), estimate a filled blob's
  width from its stadium geometry, and take a `/FreeText`'s size from the
  appearance rather than `/DA` [I, from gnnote's reader and inkref].

## OneNote

### Containers, packagings and who reads what

- Two byte-level packagings carry one object model. The **classic revision
  store** (MS-ONESTORE §2.3; `guidFileType` `{7B5C52E4-D88C-4DA7-AEB1-
  5378D02996D3}` for `.one`, `{43FF2FA1-EFD9-4C76-9EE2-10EA5722765F}` for
  `.onetoc2`; `guidFileFormat` `{109ADD3F-911B-49F5-A5D0-1791EDC8AED8}` at
  byte 48) is what the Windows desktop app writes and exports; a 1024-byte
  header, a transaction log that says how many FileNodes per list are
  committed ("uncommitted trailing nodes are real in the wild"), FileNode
  lists with magic `0xA4567AB1F5F7F4C4`, object spaces, revision manifests,
  object groups and file-data objects [V: the spec; inkterop and gnnote
  implement it]. The **FSSHTTPB alternative packaging** (§2.8;
  `guidFileFormat` `{638DE92F-A6D4-4bc1-9A36-B3FC2511A5B7}`, cell schema
  `{1F937CB4-B26F-445F-B9F8-17E20160E461}` for `.one`) is "the file format
  for .one and .onetoc2 files stored on a server": an envelope holding the
  result of a Query Changes sub-request as a Data Element Package, which
  every OneDrive or SharePoint download and OneNote for the web's export
  ZIP carry; every object is two declaration/data pairs (partition 4 the
  JCID, partition 1 the same `ObjectSpaceObjectPropSet` as the desktop
  format, partition 2 a BLOB for file data) [V: the spec; gnnote, Tika,
  onenote.rs 2.0, OfficeIMO]. OneDrive files prefix every BLOB with a
  compact-integer length the spec does not mention [V1: gnnote]. Above the
  container the property sets are byte-identical, so one reader serves
  both after a second front end [V].
- `.onetoc2` lists sections in `0x24001CF6` entries (name `0x1C001D6B`,
  order `0x14001CB9`, identity `0x1C001D94`, not in the MS-ONE tables) and
  carries no ink; section groups are sub-folders with their own [V1:
  gnnote; inkterop]. `.onepkg` is a Microsoft Cabinet ("MSCF", LZX) of
  desktop `.one` and `.onetoc2` files, which only the Windows desktop app
  writes; Python's stdlib has neither CAB nor LZX; the Obsidian importer
  ships a TypeScript LZX decoder validated against `makecab` at window
  sizes 15–21 with E8 post-processing, OfficeIMO a managed one (single
  cabinets, uncompressed, MSZIP or LZX) [V: file(1), gnnote, Obsidian,
  OfficeIMO]. A 2007-era PoC extracted members to `..` paths, so member
  names are lookup keys only [V].
- Encryption is detectable, not decryptable: a desktop object space whose
  second FileNode is `0x07C` (`ObjectDataEncryptionKeyV2FNDX`) or a
  revision manifest with `odcsDefault` ≠ "no encryption"; in FSSHTTPB a
  root object with role 3 [V: the spec, gnnote, OfficeIMO]. The cloud
  cipher (AES-256/SHA-512 claimed) was never answered by Microsoft [V1,
  open]. `.onex` is an OLE compound file with `EncryptedPackage` and DRM
  streams, rights-protected, which Obsidian classifies and refuses [V1].
- Who reads what, with licences: gnnote (MIT) both packagings and the
  OneDrive ZIP, refusing `.onepkg`; inkterop (MIT) the classic store only;
  the Obsidian file importer (MIT) both plus CAB; OfficeIMO (MIT, C#) both,
  `.onepkg` and `.onetoc2`, and writes them, its desktop output opened
  and re-saved by OneNote; onenote.rs (MPL-2.0) both since 2.0.0
  (2026-08-06; the desktop support was built by Joplin and merged
  upstream), one2html (MIT) on top with `.onepkg` behind a feature; Tika
  (Apache-2.0) both for text and attachments, no ink; pyOneNote
  (Apache-2.0) the desktop header only, for embedded files;
  py-onenote-parser (MIT, three weeks old) claims both, ink "tentative"
  [V: each repository]. No maintained pure-Python reader exists beyond
  gnnote's and inkterop's.

### The object model

- Hierarchy: section `0x00060007` → page series `0x00060008`
  (`ChildGraphSpaceElementNodes 0x2C001D63`, one object space per page) →
  page manifest `0x00060037` → page `0x0006000B`, whose
  `ElementChildNodes 0x24001C20` hold outlines `0x0006000C`, pictures
  `0x00060011`, embedded files `0x00060035` and ink containers
  `0x00060014`, and whose `StructureElementChildNodes 0x24001D5F` hold the
  title `0x0006002C`; outline → outline elements `0x0006000D` (indentation
  by nesting) → rich text `0x0006000E`, pictures, tables `0x00060022` [V:
  gnnote, pyOneNote's JCID table, the spec].
- **Units**: layout values are IEEE-754 floats in half-inch units, 36 pt:
  `PageWidth 0x14001C01`, `PageHeight 0x14001C02` ("in half-inch
  increments"), margins, `OffsetFromParentHoriz/Vert 0x14001C14/15`
  (page-absolute, set only on objects directly under the page),
  `LayoutMaxWidth/Height`, `PictureWidth/Height 0x140034CD/CE`; a title
  draws at (36 pt + offset, 18 pt + offset) [V: the spec, gnnote, OfficeIMO,
  Obsidian's `NATIVE_UNITS_PER_HALF_INCH = 1270`]. A page is an unbounded
  canvas that grows with its content, default 754 × 783 pt [V1: gnnote];
  the layout engine (how text flows inside an outline, where inline ink
  sits) is not in the spec and was reverse-engineered from OneNote for the
  web's minified code [V: m-siemens].
- **Rich text**: `RichEditTextUnicode 0x1C001C22` (UTF-16 with a trailing
  NUL), run ends `TextRunIndex 0x1C001E12`, run styles `TextRunFormatting
  0x24001E13` → `ParagraphStyle 0x2000342C` objects with `Bold 0x08001C04`,
  `Italic`, `Underline`, `Strikethrough`, `Font 0x1C001C0A`, `FontSize
  0x10001C0B` (half-points), `FontColor 0x14001C0C` (COLORREF),
  `ParagraphAlignment`, `NumberListFormat`, `ListNodes`, `WzHyperlinkUrl
  0x1C001E20`, `Hidden` [V: the spec, gnnote's schema]. Math lives inside
  text runs as a flattened operator tree with the noncharacters U+FDD0,
  U+FDEE, U+FDEF and a parallel `MathInlineObjects` array [V: m-siemens;
  onenote.rs 1.1.0]. Tables carry `TableColumnWidths` and a locked-columns
  bit array [V]. Note tags are `NoteTagStates` [V1].
- **Pictures**: `jcidImageNode` with `PictureContainer 0x20001C3F` → a
  `jcidPictureContainer14 0x00080039` file-data object, "by default,
  images do not contain any data"; `WebPictureContainer14 0x200034C8` a
  web-ready copy; `PictureWidth/Height`; `ImageFilename 0x1C001DD7`;
  `DisplayedPageNumber 0x14003480` "for multi-page files such as XPS or
  PDF"; `IsBackground`; `ImageAltText`; `LanguageID` for OCR [V: the
  spec]. Readers size a picture from `PictureWidth/Height`, else the PNG
  or JPEG pixel size at 96 dpi (0.75 pt per px), else 144 pt, scaled down
  to `LayoutMaxWidth/Height`, and prefer the normal payload with the web
  copy as fallback; only PNG and JPEG are decoded, EMF, WMF, TIFF, BMP and
  GIF dropped [V: gnnote, OfficeIMO].
- **Embedded files**: `jcidEmbeddedFileNode 0x00060035` with
  `EmbeddedFileContainer` → `jcidEmbeddedFileContainer 0x00080036`, a
  file-data object, and `EmbeddedFileName`; a file-data reference begins
  `<file>` (an external `.onebin` named by UUID), `<ifndf>{GUID}` (a
  `FileDataStoreObject` inside the file) or `<invfdo>` [V: the spec;
  pyOneNote resolves `<ifndf>`]. The embedded-file property ids are
  inferred from gnnote's reading of a misaligned table extract [I].
- **Printouts** are not a spec concept: a PDF printout is one image node
  per printed page, PNG when the embedded bytes are PNG, with a non-spec
  property `0x200035C1` pointing at the source PDF; no open reader carries
  the PDF over ("not carried over *yet*"), though it is very likely a
  file-data object, and Graph exposes it as `<object
  data-attachment="x.pdf" type="application/pdf">` [V1: gnnote; one2html
  2.0; Joplin 3.6 "PDF printouts are imported correctly"; I for the PDF's
  presence].
- **Times**: `LastModifiedTime` is a Time32, seconds since 1980-01-01 UTC;
  `CreationTimeStamp` a FILETIME (1601); `Author`, `AuthorMostRecent`,
  `AuthorOriginal` exist [V: the spec, Tika]. `AudioRecordingGuids` sits on
  the page; no reader reads a recording [V1].
- **Revisions and deleted content**: inkterop replays every revision in
  list order and keeps the first declaration per root role; gnnote follows
  the current revision plus its dependencies ("later declarations win");
  conflict pages hang off the page manifest, version history lives in
  other contexts, `IsDeletedGraphSpaceContent 0x1C001DE9` marks deleted
  pages, and the recycle bin is the `OneNote_RecycleBin` group or
  `onenote_deletedpages.one` [V: inkterop, gnnote, OfficeIMO]. Both
  strategies "yield the newest state on every sample".

### Ink

- Ink is absent from MS-ONE and lives in four undocumented JCIDs through
  the ordinary property-set machinery; its numbers reuse the Tablet PC
  ISF encoding ("a format Microsoft had published, just not in the OneNote
  specs") [V: m-siemens, the onenote.rs wiki, gnnote, inkterop; the ISF
  spec].
- InkContainer `0x00060014` {`OffsetFromParentHoriz/Vert` half-inch,
  `InkScalingX 0x14001C46`, `InkScalingY 0x14001C47` (f32, default 1.0),
  `InkData 0x20003415`, `LastModifiedTime 0x14001D7A`, or
  `ContentChildNodes 0x24001C1F` → nested containers}; InkDataNode
  `0x0002003B` {`InkStrokes 0x24003416` (ordered), `InkBoundingBox
  0x1C003418` (four LE u32)}; InkStrokeNode `0x00020047` {`InkPath
  0x1C00340B`, `InkStrokeProperties 0x20003409`, `InkBias 0x0C00341C`,
  `LanguageID`}; StrokePropertiesNode `0x00120048` {`InkDimensions
  0x1C00340A`, `InkWidth 0x1400340D`, `InkHeight 0x1400340C` (f32,
  HIMETRIC), `InkColor 0x1400340F`, `InkTransparency 0x0C003414` (u8),
  `InkRasterOperation 0x0C003413` (u8, the INKMETRIC enum), `InkPenTip
  0x0C003412` (0 round, 1 rectangle), `InkIgnorePressure 0x08003411`,
  `InkAntialiased`, `InkFitToCurve`} [V: the wiki, gnnote, inkterop]. The
  wiki gives the last two as `0x8000340E`/`0x80003410`, gnnote's errata
  `0x0800340E`/`0x08003410`: the same 26-bit ids, different type bits
  [C, minor].
- `InkPath` is a stream of ISF multibyte signed integers (7-bit groups,
  low bit the sign): a count first (`count = first >> 1`), then `count`
  values, dimension-major (all X, all Y, then the rest), each channel's
  first value absolute and the rest first-order deltas; "no delta-delta,
  no Huffman, unlike ISF packet data"; two implementations consume the
  buffer exactly and a third (Obsidian's `decodeSignedVector`) agrees;
  onenote.rs decoded the values as absolute until 2.1.0's
  `absolute_path()` [V: inkterop, gnnote, Obsidian; the wiki; the
  CHANGELOG].
- `InkDimensions` is packed 32-byte entries, a mixed-endian GUID, i32
  lower and upper limits, and 8 bytes that are a u32 unit and an f32
  resolution: X and Y unit 2 (cm) at 1000 per cm, HIMETRIC, 0.01 mm [V:
  inkterop, gnnote, the wiki]. X is `{598A6A8F-52C0-4BA0-93AF-AF357411A561}`,
  Y `{B53F9F75-04E0-4498-A7EE-C30DBB5A9011}`, pressure the ISF
  `GUID_NORMAL_PRESSURE {7307502D-F9F4-4E18-B3F2-2CE1B1A3610C}` with limits
  [0, 32767] in files (ISF's own default metric is 0..1023) [V: the ISF
  spec, gnnote, inkterop]. Obsidian's `2d500773-f4f9-4e18-…` is the ISF
  GUID with its first three fields byte-swapped by its own reader [I]; the
  wiki's `{1d6cb91e-2fd9-4e7f-9dca-bb8d29e91e74}` is another GUID
  altogether, and onenote.rs matches only X and Y [C: the ISF value,
  observed in files with limits 0..32767, wins].
  Other packet GUIDs are skipped but still count in the sample arithmetic
  [V1: the wiki]. Tilt (ISF indexes 9 and 10) could appear and no sample
  shows it [I].
- **The transform to points**, validated independently by two readers on
  `scaled_ink.one` (one container with `InkScalingY` 7.18 and
  `OffsetFromParentVert` −19.03): `page_pt = 36 × Σ ancestor offsets +
  value × InkScaling × 72/2540`; a nested container uses its own offsets
  and scaling [V: inkterop, gnnote]. onenote.rs's `InkPoint` applies the
  scaling but not the half-inch offsets, and its wiki notes two parser
  bugs (width and height swapped; `InkScalingY` read from the X property)
  [V1: the wiki].
- **Colour** is a COLORREF `0x00BBGGRR`, absent = black [V: gnnote,
  inkterop, Obsidian]; the wiki says `0xAARRGGBB`, docs.rs "not specified"
  [C: the implementations win]. **Transparency**: 0 or absent = opaque,
  alpha `1 − t/255`, matching Microsoft's InkML brush ("0 is opaque, 0 to
  256") [I: gnnote, inkterop, "no sample exercises it"]; the wiki says the
  opposite [C, open until a sample]. **Highlighter**: `InkRasterOperation
  = 9` (mask pen), observed as a 56 × 400 HIMETRIC chisel [V: gnnote,
  inkterop; the 2017 blog's `maskPen`]. **Width**: gnnote takes
  `max(InkWidth, InkHeight)`, inkterop `InkWidth` else `InkHeight`, default
  53 HIMETRIC (0.53 mm, ISF's v2 default) [C, open; both in points
  ≈ 1.5 pt]. **Pressure to width**: "OneNote's curve is undocumented";
  gnnote applies WPF's `1.5p + 0.25` and notes mouse and finger strokes
  carry a constant 0.75 [I].
- **Inline ink words** are anchored to text: U+FFFC runs whose
  `TextRunDataObject 0x24003458` entry is an InkDataNode and whose
  `TextRunData 0x40003499` carries `EmbeddedInkStartX/Y 0x1400349E/9F`,
  `EmbeddedInkWidth/Height`, `EmbeddedInkOffsetHoriz/Vert`; run types
  `0x00020026` (ink space) and `0x00020027` (ink line break) consume no
  data entries; without the offsets, fall back to the InkDataNode's
  bounding box [V: the wiki, gnnote]. Their exact position depends on
  OneNote's text layout, which no open reader reproduces: page-level ink
  is exact, inline ink approximate [V: inkterop, gnnote]. Handwriting
  recognition output is stored beside the ink (onenote.rs 2.0.0,
  Obsidian, one2html's `<title>`) [V].
- **Unknown** [V as gaps]: stroke properties `0x3419/0x341A/0x341B/0x341D/
  0x345B/0x3420` and `0x349E`–`0x34A5`; the pressure-to-width law; tilt;
  how erased strokes are kept (history is in the revision store and only
  the current revision is walked; "no erased sample yet"); whether a
  partly erased stroke is split into several nodes; any iPad-made sample.

### Acquisition paths per app

- **OneNote for Windows desktop** (Microsoft 365, 2024, 2021, 2016): File
  → Export → Export Current (page, section, notebook) → "OneNote 2010-2016
  Section (*.one/*.onex)" or "OneNote Package (*.onepkg)" (the labels as
  Joplin documents them), PDF, XPS, DOCX, MHT; `.doc` retired 2025-07-28
  (MC1104312) [V: Microsoft, Joplin's help]. The only app that writes
  `.one` and `.onepkg` [V: Obsidian's help]. Backups in
  `%LOCALAPPDATA%\Microsoft\OneNote\16.0\Backup` work as input [V1:
  Obsidian's help].
- **OneNote for Windows 10**: retired 2025-10-14 (MC1139479, read-only
  since); it never opened local files and exports only by printing to
  "Microsoft Print to PDF" [V: Microsoft]. This contradicts onenote.rs's
  "OneNote for Windows 10/11 (via `.one` export)" line [C: Microsoft's
  pages win; what a Windows 11 or Mac "backup" is remains unknown].
- **OneNote for Mac**: File → Save as PDF, one page; no `.one` or
  `.onepkg`; onenote.rs says it reads "OneNote for Mac backup files",
  whose location and packaging no Microsoft page states [V; V1; open].
- **OneNote for iPad and iPhone**: Share → Send Copy of Page → Send with
  Another App → Print → Create PDF; "we have removed the ability to share
  single pages"; opening an exported notebook from OneDrive "isn't
  supported on iOS" [V: Microsoft]. gnnote: "no file export of its own",
  a flattened PDF [V1].
- **OneNote for the web** (personal Microsoft accounts only): right-click
  the notebook → Export notebook → a ZIP with the notebook's folder, a
  "OneNote Table of Contents" and FSSHTTPB `.one` files; "doesn't work for
  OneDrive (work or school) or SharePoint notebooks"; a ZIP holding only
  `errors.txt` means it failed; the import side is "deprecated"; Joplin's
  variant downloads the notebook folder from OneDrive's My Files
  (2–4 GB limit) [V: Microsoft, Joplin]. A 2025-11 user found no
  right-click export in the web app and used the OneDrive download
  instead [V1: Joplin forum].
- **Work or school accounts**: whole-notebook export only through the
  desktop app (which some policies forbid); a Mac or iPad user with such
  an account has no file route at all but opening the notebook once in
  OneNote for Windows [V: Microsoft Q&A 5431088, Clarkson].
- Password-protected sections are skipped by every tool; rights-protected
  files cannot be read [V: Obsidian, OneNote2Obsidian, onenote-md-exporter].

### The Graph API

- `https://graph.microsoft.com/v1.0/me/onenote`: `notebooks`,
  `sectionGroups`, `sections/{id}/pages` (`pagelevel=true` for the
  indentation and order), `pages/{id}/content[?includeIDs]` (HTML,
  `Accept: text/html`), `resources/{id}/$value`; 20 pages per request by
  default, `top` at most 100, `skip` for paging, `expand=…(levels=max)` for
  the hierarchy; Microsoft's own warning that backup or restore of
  sections or notebooks "will reach OneNote API limitations" [V: the
  docs, updated 2025-06-30]. Obsidian works around "Setting $top disables
  @odata.nextLink" and "Graph ignored $skip and repeated a full batch"
  [V1].
- Output HTML: `<body data-absolute-enabled="true">` with absolutely
  positioned `div`s (`left:48px; top:120px; width:624px`), inline spans,
  tables without spans, `<img src="…/$value" data-fullres-src="…" width
  height>`, split images with `data-options="printout"`, `<object
  data-attachment="x.pdf" type="application/pdf">`, OCR text in `alt`
  [V: the docs, updated 2024-11-07; Obsidian].
- **Ink** only through `?includeInkML=true`, announced in the 2017-07-07
  beta blog: a multipart `text/html` + `application/inkml+xml` response,
  channels X and Y integers in himetric (1/1000 cm, origin the page's
  top-left, y down), F pressure in "dev", brush `width, height, color,
  transparency (0..256, 0 opaque), tip, rasterOp (maskPen | copyPen |
  noOperation), ignorePressure, antiAliased, fitToCurve`; "some ink
  created by the OneNote Win32 client is not returned" (workaround "treat
  as drawing"); confirmed on the beta endpoint by a 2021 Q&A answer (with
  the placeholder `<!-- InkNode is not supported -->` in the HTML) and used
  in production against **v1.0** by Obsidian (2023–2026), which converts
  only `trace` and `brush` to one SVG per page, ignoring pressure and
  context and aligning nothing to the HTML [V: the blog, Q&A 666739,
  Obsidian's code]. The current v1.0 documentation does not mention it
  [V]. Graph cannot export PDF or images [V: Q&A].
- **Permissions**: `Notes.Read` and `Notes.ReadWrite` delegated only, no
  admin consent, personal accounts yes; `Notes.Read.All` delegated (admin
  consent no) and application (yes), not for personal accounts;
  `Notes.ReadWrite.CreatedByApp` deprecated; **app-only tokens retired
  2025-03-31** (MC1011142: 401 since) [V]. Obsidian requests `notes.read`
  for consumer tenants and `notes.read.all` for organisations, detected
  from the token's `tid` [V1]; only notebooks the account owns import;
  shared ones do not [V: Obsidian's help].
- **Registration**: an Entra app with `https` redirect URIs (loopback
  `http://127.0.0.1` allowed with a port that is ignored; IPv6 loopback
  not), at most 100 URIs for an app that admits personal accounts, no
  wildcards or query strings; "Mobile and desktop applications" with a
  public-client flow for Electron, iOS and Flutter, and the device-code
  flow needs none [V: the docs, 2025-05-14]. Obsidian uses one public
  client id, the `common` authority, `response_type=code`, a custom scheme
  `obsidian://importer-auth/`, no secret, the refresh token in secret
  storage [V1].
- **Throttling**: 120 requests per minute and 400 per hour per app per
  user, 5 concurrent, applying to every OneNote resource, and **no
  `Retry-After` on 429** [V: the limits page]; Microsoft's 2016 guidance
  (per app and per user, `$expand`, never retry 4xx) [V]. Obsidian waits
  60 s by default, spaces attachment downloads by up to 3 s, aborts after
  90 minutes without a success, and its 1.6.1 and 2.2.0 releases were
  about back-off; a user found the import "frequently paused" [V1 each].
  A 1,000-page notebook with images is a multi-hour, resumable job [I].
- **Deprecations**: consumer webhooks 2023-06-16 ("no replacement"),
  `get-pages?search` for consumers 2024-05-05, app-only auth 2025-03-31;
  no notice against the delegated API as of 2026-10-25 [V]. `includeInkML`
  has been undocumented for nine years: its deprecation risk is moderate
  [I].

### Precedents

- **Joplin** (file-based): OneNote archives since 3.2 (2025-01-14), File →
  Import → "ZIP - OneNote Notebook"; `.one`/`.onepkg` since 3.5.5; 3.6
  (2026-05-05) made `.onepkg` work on Linux and macOS and "substantially
  improved" handwriting (positioning, scaling, nested containers) and
  printouts; 3.7 (2026-08-31) another round on note order, printouts and
  handwriting. The converter is onenote.rs and one2html compiled to WASM;
  ink becomes `<svg>` extracted to image files, one per drawing, which
  made "thousands of files" per handwritten notebook until PR 14305
  (2026-02-09) batched consecutive strokes; XPS printouts rasterise
  through PowerShell on Windows only, and the importer file has no PDF
  printout code of its own (the converter handles them since 3.6) [V:
  releases, code, forum]. Complaints: missing sections and pages (three reports),
  sections over about 100 MB missing, no import log, tags as `?` images,
  an extra pointer note per section, the documented web export not where
  the help says [V: forum].
- **Obsidian Importer** (Graph, then files): the account path with
  `includeInkML`, `$skip` paging and back-off; 2.4.0 (2026-08-11 by the
  GitHub API's release date; one researcher read the undated release page
  as 2025 [C]) added an offline `.one`/`.onepkg` importer "without
  requiring a Microsoft account or internet connection" with "ink
  drawings" among its content, because of rate limits, shared and locked
  notebooks and org consent [V: releases, help; I for the motive]. The
  account path's ink SVG is appended at the end of the note, not placed
  [V: code]; where the file importer puts its `<title> - Ink.svg` was not
  read.
- **OneNote2Obsidian** (MIT, alpha): preserves positioned text, images,
  ink, arrows and tables in a canvas sidecar; **onenote-md-exporter**
  (GPL-3.0, Windows COM): "handwriting is lost", drawings flattened;
  **gnnote**: the one converter that keeps OneNote ink editable and
  positioned, printouts as pictures [V]. Evernote, Notion, Apple Notes,
  Samsung Notes, Nebo, Xournal++, FuseBase, UpNote, Logseq and Anytype
  have no OneNote importer and route through PDF, DOCX, HTML or ENEX [V,
  as absences]. Commercial: Aspose.Note rendered ink only from 25.3 [V].
- Every shipping product treats OneNote handwriting as a picture; an
  importer that keeps it as positioned, editable strokes would exceed both
  Joplin and Obsidian [I].

### The PDF export

- Desktop: File → Export → page, section or notebook → PDF, "a static
  snapshot"; web and Mac one page at a time; Windows 10 by printing; iPad
  by Create PDF [V: Microsoft]. Nothing documents how ink or text are
  encoded. Indirect evidence says ink is vector (an Inkscape bug on a
  "glitter pencil" export; an Adobe thread on "mostly vectors of my
  writing"), as filled outlines rather than stroked centrelines (inkref,
  from a rnote maintainer's description of OneNote's SVG), that typed text
  stays text, that no handwriting text layer exists (Evernote could not
  OCR it), and that OneNote 2016's export loses highlighter transparency
  [I throughout; V1 for each report]. For Gamma a OneNote PDF is an
  ordinary PDF whose ink is page content: nothing editable comes back
  [I]. No skeletonisation of filled outlines exists in any tool [V: inkref].

### Onto Gamma

- A **page** becomes a sheet of paper ([dev/notebooks.md](../dev/notebooks.md))
  of the page's width (`PageWidth × 36`, else the content's) and the
  height its content needs, white and blank, titled with the page's
  `CachedTitleString` (a sheet block's content is its title); a canvas
  taller than Gamma's 2,000-pt cap on a paper's side is cut into stacked
  sheets, each item on the sheet holding its vertical centre, the rule
  `notability_map.frames` already applies to a Notability canvas [I].
- **Ink**: each InkStrokeNode one stroke, samples by the transform above,
  `pen: true` with `p = pressure / 32767` where the channel exists and
  varies, `w` baked by Gamma's own Pen law (`1 + (p − 0.5)`) from it, so
  a restyle changes nothing; `pen: false` when `InkIgnorePressure` is set
  or the pressure is constant (a mouse's 0.75); `size` the larger of
  `InkWidth` and `InkHeight` × 72/2540 pt (gnnote's choice; the chisel's
  short side is lost either way); `color` from the COLORREF; `opacity`
  `1 − InkTransparency/255`; raster op 9 → `highlighter` in band 0; the
  rectangle tip, anti-aliasing and fit-to-curve flags dropped; inline ink
  words placed by `EmbeddedInkStartX/Y` where present, else by the
  InkDataNode's box, with a warning that they are approximate [I; the
  WPF curve is the alternative law, and neither is OneNote's].
- **Text**: an outline a fixed-width text box at its page offsets and
  `LayoutMaxWidth`, a line per paragraph nested by outline element,
  bold, italic and strikethrough as Markdown, the majority size (`FontSize
  / 2` pt) and colour as the box's, lists as Markdown bullets and
  numbers, a hyperlink run as a link, a table as a Markdown table; the
  flow inside the box is Gamma's typesetting, not OneNote's, so line
  breaks move [I]. Math runs as their plain characters with a warning;
  note tags dropped with a warning [I].
- **Pictures**: a PNG or JPEG node a picture box at its offsets and
  `PictureWidth/Height` (else pixels at 0.75 pt), the normal payload with
  the web copy as fallback; BMP, GIF and TIFF converted by Pillow, EMF and
  WMF dropped with a warning [I].
- **A printout page**: when the source PDF can be recovered from a
  file-data object named by `0x200035C1` or an embedded file of the same
  name, the page becomes a page carrying that PDF (the original route),
  each printout image's rect giving the frame that maps the ink over it
  into the PDF page's points; else the printout images are pictures under
  the ink on the sheet [I; the recovery is unverified in any reader].
- **Embedded files** stored as uploads and linked from a text box;
  audio and video recordings dropped with a warning (no reader reads
  them); conflict pages, version history, the recycle bin and
  password-protected sections skipped and said so, as gnnote does [I].

## Precedents and user expectations

- **No shipping app imports `.goodnotes` natively**: Notability's import
  article (updated 2026-09-03) lists PDF, PowerPoint, spreadsheets and its
  own files; Samsung Notes, Noteful, Flexcil, Nebo/MyScript Notes,
  Xournal++, Saber, Kilonotes and Notewise take PDF (and sometimes images
  or Office files) or their own format [V, as absences; the vendor
  pages]. GoodNotes itself imports PDF, JPG/PNG, Word and PowerPoint
  (iPadOS/iOS), `.goodnotes`, `.goodnotes.zip` and CSV/TSV flashcards, and
  "for other apps you will need to export all the notes in that app as
  PDFs"; a native Notability import has been requested since 2021-11-02
  [V: vendor; feedback board]. The native-import precedents are
  converters (gnnote, inkterop, goodparse, p4g, inkref) and two apps in
  development: Graphite (Apache-2.0, TestFlight), whose issue #30
  (2026-10-20) converts ball, fountain, brush, pencil "with force and
  tilt" and highlighter to PDF ink, shapes "as ink along their outline",
  tape and older variable-width ink "only counted in the import report",
  audio "written as .m4a files byte for byte" with the stroke timing
  "left out for now and reported", text boxes "not converted yet"; and
  sanenotes #805 (2026-09-13), a tier-A native import gated on "the
  maintainer legality decision" with a tier-B PDF fallback "always
  available" and a report headed "Best we could bring over" [V].
- **OneNote**: Joplin and Obsidian (above); everyone else routes through
  PDF, DOCX or HTML, which drops or flattens ink [V].
- **What users complain about**: that handwriting is not editable after a
  move ("so that the imported notes are going to be editable"); audio
  recordings, the most-cited loss in 2025 ("I haven't found any solution
  to transfer my audio notes"; the unzip workaround); folder hierarchy;
  "blurry" results; on the OneNote side missing sections and pages,
  broken layout where handwriting and attachments mix, overly complex
  output, rate-limited imports that pause, confusing export steps, and
  silent loss (locked sections, tags) discovered afterwards [V: MPU Talk,
  Q&A, the feedback board, the Joplin and Obsidian threads]. Nobody
  complains about pen texture, pressure nuance, page size, dashes, lasso
  history or erased ink, because no shipping importer preserves ink at all
  [I]. Users judge an import on completeness and a one-step path [I].
- **The convention**: every converter and importer author documents loss
  per document, as a human-readable warning per lossy step (gnnote, also
  in an `X-GnNote-Warnings` header), an import report (Graphite, "only
  counted in the import report"), or a report that labels what was
  flattened (sanenotes); Apple's own phrasing, "features the Notes app
  doesn't support are dropped", is the industry's [V]. Gamma's Notability
  import has it: one warning per kind of content left out
  (`notability.LEFT_OUT`, `left_out_reasons`), the report's `pages`,
  `skipped` and `warnings`, and `entries` listing what the upload held and
  was not imported.

## How it would fit Gamma

The GoodNotes import is a second source of the reviewed import
([dev/import_export.md](../dev/import_export.md#the-import-dialog)), built
on the Notability import's bones (`gamma/notability_import.py`,
`notability_map.py`, `notability.py`, `import_zip.py`): a preview that
reads every notebook but no ink and writes nothing, a commit as the
`import` job, one page per notebook, files stored first and the folder and
page written in one short transaction under the write lock with the
re-import key checked again there, a 507 stopping the import and any other
refusal skipping the notebook, a report of what was dropped.

**Reused as it is**

- The dialog's source card, picker and review flow (`resolveImport`,
  `ImportReviewDialog`), the `review` and `jobs/import` endpoints,
  staging, `_review_source`'s table with a `goodnotes` entry beside
  `notability`; the iPad share sheet opening a `.goodnotes` straight into
  its review as a `.note` does.
- The guarded zip walk: `import_zip.open_zip` with the 16 MiB central
  directory cap, `walk_zip`, the ratio rule, nested zips to two levels for
  a folder export and a `.goodnotes.zip` backup, each `.goodnotes` opened
  only while it is read in a stack of its own (`open_nested`), members
  read into memory only after their size is checked and the disk's free
  space is.
- `notability_import.compose_pdf` (a PDF page copied from its reader by
  page number, a paper page painted through `paper_ops`), the storing of a
  picture's file as it came, never decoded, only its size read from its
  header (as built, `note_app_import.AppImport.store_picture`), the
  `_Import` commit shape, the `_Skip` and `_Stop` rules, `_ordered`, the
  report's fields.
- `notability_map`'s pure helpers whose inputs are generic: `Frame` (a
  GoodNotes page is `Frame(top 0, bottom H, ox 0, oy 0, k 6/11, width,
  height, pdf, pdf_page)`), `Media` and `Run` (an image, a text box, a
  sticky and a formula fit its fields), `Shape` (a parametric shape fits
  its kinds and `fill`), `text_box`, `picture_block`, `sticky_stroke`,
  `shape_stroke` with `normalise_rect` and `normalise_ellipse`,
  `box_markdown`, `_checked` (every stroke through `ink.Stroke`),
  `MAX_FORCE`, `STEP_PT`, `DECIMATE_PT` and the cubic flattening
  (`_pieces`, `_cubic`, `_edge`) for the flat pen's quadratics raised to
  cubics; `ink.shape_samples` and `ink.group_files`; `normalize_paper` and
  `normalize_text_box`; the Pillow and PyMuPDF the server has.
- Not reused: `Note`, `Curve` and `InkHash`. They are one continuous
  canvas of W units with Bézier chains in parallel arrays and a
  Notability inset; a GoodNotes notebook is per-page sizes and templates
  with strokes already in points per page and three geometry layouts. A
  reader of its own, `gamma/goodnotes.py`, fills a per-page model
  (`Notebook{title, created, digest, pages: [Page{frame, source: paper |
  (attachment, pdf_page, rotation), elements in z-order}], attachments,
  recordings, warnings, left_out}`) whose strokes are sample lists in
  points with their look, and a thin `goodnotes_map.py` turns a page into
  the same `PagePlan` the Notability planner emits.

**The reader**, stdlib only: a schema-free protobuf wire reader (varint,
fixed32, fixed64, length-delimited; the group types refused; nesting
capped), the Apple-LZ4 frame with each block's declared raw size bounded
(32 KiB blocks, a stroke of at most a few MB: the largest seen is 133,538
B) and the 64 KiB history kept, the `tpl` image decoded by its format
string with every `A(x)` count checked against the bytes left (a count can
claim billions), the five known strings and the two legacy ones named, an
unknown string a warning and a dropped stroke. Caps as the Notability
import's: elements per page, points per stroke (1,699 seen), pages, text,
runs, pictures; a NaN, an infinity or a coordinate past `COORD_LIMIT`
leaves its element out.

**What a notebook becomes** (the Notability planner's four kinds):

| The notebook | Becomes |
|---|---|
| Every page bound to a catalogue paper (its template name's UUID in the catalogue and its PDF a svg2pdf fill with or without rules), and something on it | A notebook: a sheet per page, each with its own paper from the template's pattern (blank, dots, grid, ruled), pitch ((`#18.#2` + `#18.#3`) × 6/11: 16, 17.5 or 25 pt, within Gamma's 12–96), colour (the PDF's first `scn` triple) and line colour (the catalogue's light or dark grey), size from the MediaBox, landscape kept |
| Every page a catalogue paper and nothing on any | A plain page: its outline entries as notes |
| Its pages are exactly one user PDF's pages 1..n in order, no rotation | A page carrying that PDF's bytes as they are |
| Anything else: a cover, a custom template, a PDF inserted among paper pages, several PDFs, pages reordered, duplicated, deleted or rotated | A page carrying one PDF composed of the pages in order (`compose_pdf`): a PDF page copied from its attachment by `#2.#5` with `/Rotate` from `#63`, a paper page painted from its template (a catalogue paper as `paper_ops`, any other template as its PDF page), so the notebook keeps its order and mixed sizes |

- The cover question decides how many notebooks stay sheet notebooks: a
  template whose PDF is a plain fill with at most one rectangle (the
  inferred cover) could be read as a blank sheet of its fill colour, which
  keeps a cover-plus-Essentials notebook a notebook; since covers are an
  inference, the first version treats an unrecognised template as a PDF
  page and the calibration notebook settles it [decision, I].
- Ink per page goes into one group in `canvas` space (a sheet) or
  `pdf-page` space (a composed or original PDF), packed by `group_files`;
  tombstones and deleted pages skipped; sub-paths split; `#6` added; the
  fill companion joined to its outline by `#5`; z-order the record order,
  stickies first as the Notability import orders them.
- Text boxes, pictures and stickies' text as text boxes under the sheet or
  on the PDF page; recordings after everything; the search index,
  thumbnails, `document.info.pb` and the bookkeeping events never read.
- **The preview** lists each `.goodnotes` of the upload with its title,
  page count, what it would become (notebook, plain page, the PDF as it
  is, a composed PDF), its PDFs, pictures and recordings, and which
  notebooks the library already holds; the tree's folders are the upload's
  directories above each file, less one shared top directory, as the
  Notability preview builds them.
- **The re-import key**: `properties.goodnotes_import` = the sha256 of
  `index.events.pb` followed by every `notes/` member in `index.notes.pb`
  order (the attachments are content-addressed already and the search
  index, thumbnail and `document.info.pb` are derived), checked in the
  preview and again under the lock, so an unchanged notebook imported
  twice makes one page; the document UUID (`#30`'s entity) beside it lets
  the preview say "imported before, edited since" [decision].
- **The report**: the Notability report's fields and one warning per kind
  of content left out: tape as opaque ink, shape boxes' corner radii,
  rotated rectangles, dashed lengths, the pencil's look, pictures of a
  kind not decoded (HEIC, JPEG 2000), bookmarks and page links, math
  strokes, Layers, the unknown `#55`/`#63` when they do occur, a template
  whose PDF did not open (paper, with a warning, as the Notability import
  does), an attachment the index names that the zip lacks.

**OneNote, the equivalent**: a `onenote` source whose reader handles the
three containers (`.onepkg` as a Cabinet with an LZX decoder of our own,
the OneDrive or web ZIP, a bare `.one` or `.onetoc2`) and both packagings
behind one property-set walk, with the encryption markers refusing a
section early; every section of a notebook one Gamma page of sheets (one
per OneNote page, in `.onetoc2` then page-series order; subpages in that
order), filed by section group under the destination; the mapping of the
"Onto Gamma" section; the same preview, commit, digest key (the section's
current revision's root object ids, or the file's bytes) and report. The
scope gnnote reached in 3,400 lines, with the sample set it lists, is the
size of the work. **The Graph route** would cost Gamma, a self-hosted web
app, an Entra registration per deployment hostname (the 100-URI cap without
wildcards rules out one vendor app for every self-hosted origin), or a
relay through the Gamma Cloud account server with one fixed redirect, or
the desktop and iPad apps alone using a loopback or custom-scheme public
client with a vendor client id as Obsidian does; then delegated consent
(admin consent in many organisations), 120 requests a minute with no
`Retry-After`, multi-hour jobs for a notebook of pictures, no shared
notebooks, ink without pressure through an option undocumented since 2017,
and a maintenance surface Microsoft has retired three pieces of since 2023.
The file route reaches the same notebooks (a personal account through the
web ZIP on any OS, a work account through the Windows app) with richer ink.
Graph is not worth it for a first version; it would only add work or
school notebooks for Mac- or iPad-only users without a Windows machine,
whom org consent hampers on Graph too.

## Samples and tests

**Fixtures that may be vendored**

- GoodNotes: inkterop's `core/tests/fixtures/goodnotes/gn-mac-mixed-pens.goodnotes`
  (CC0; 82,381 B; GoodNotes 6 Mac App Store 2026-07-09; schema 25; one
  page with fountain, brush, ball, pencil, highlighter and tape strokes and
  one shape-tool ellipse; no images, text or user PDF). goodparse's
  `Test4`–`Test9.goodnotes` with the app's own `Test4.pdf`–`Test9.pdf` are
  the richest public corpus (Test9 the only schema-35 file with user PDFs,
  a sticker and a JPEG) but sit in a GPL-3.0 repository with no data
  licence and hold a photo and a German handball form: usable only as a
  local differential oracle, never vendored, unless the author grants a
  licence. p4g's `assets/ex1–ex3`, `record.goodnotes` (the one public
  recording) are in an MIT repository whose LEGAL-NOTICE asks not to
  publish third-party content: the same. codec's 206 documents are
  private.
- OneNote: Apache Tika's `testOneNote2016.one` (desktop) and the two
  `testOneNoteFromOffice365*.one` (FSSHTTPB), Apache-2.0; Microsoft's
  Interop-TestSuites samples, MIT; the Obsidian importer's fixtures (MIT;
  its SOURCE.md records provenance and refused the AGPL files). onenote.rs's
  `handwriting_recognition.one` is stated as MPL-2.0 by one note and its
  README says its third-party fixtures are MIT or AGPL-3.0-or-later: check
  the file's own licence before vendoring [C]; its `joplin/scaled_ink.one`,
  `desktop_missing_ink.one`, `onenote_desktop.one` are AGPL-3.0-or-later,
  facts only (the `InkScalingY` 7.18 validation lives there). No sample
  made on an iPad exists for either app.

**Calibration documents to make in GoodNotes 7 on an iPad**, one notebook
per feature as inkterop's protocol does, each exported as `.goodnotes`, as
Editable PDF and as Flattened PDF (once more with handwriting recognition
on), the notebook's title and the device's build noted, and kept under
`backend/tests/samples/goodnotes/` beside the exports so the reader is held
to the app's own output as `test_notability_samples.py` holds the Notability
reader to its sample:

1. **Pens**: one row per tool at two thicknesses and colours: fountain
   with and without Apple Pencil Pro rotation, brush, ball, pencil at
   varying force and tilt, the highlighter freehand and with "Draw in
   Straight Line", tape, a Dashed and a Dotted pen preset; settles the
   type-4 question, the pencil's angle order, `#11`'s encoding on the
   iPad, and whether `W/2` holds on screen.
2. **Shapes**: every Shape-tool kind with fill on and off, a rotated
   ellipse, a rounded rectangle, a polygon, straight, curved and elbow
   lines with open and filled heads at one and both ends, a dashed
   outline, a highlighter shape, a text in a shape box.
3. **Erasing**, as before-and-after pairs: a whole stroke, the standard
   eraser through a ball, a fountain and a pencil stroke, the Selective
   Eraser, "erase highlighter only"; settles tombstone versus removal
   versus `#16` on a current build.
4. **Lasso**: moved ink (`#6`), a moved group, resized ink, copy and paste
   (the sub-version 1 form), a highlighter sent to back, a rotated
   selection.
5. **Text**: `#21` boxes with every style, a list, a hyperlink, a page
   link, a rotated and a scaled box; an `#8` RTF box needs an older build
   and is covered by Test5–8.
6. **Images**: a PNG, a JPEG with EXIF orientation 6, a HEIC, a GIF, a
   cropped, a rotated and a flipped picture, a sticker, an Elements item.
7. **Stickies**: expanded and collapsed, with ink and text on them.
8. **Audio**: a recording with ink on two pages during it, the wall-clock
   start noted; settles the tick unit against the file's duration.
9. **Papers**: every Essentials ruling in white, yellow and black, Letter,
   Tabloid, A3 to A7, landscape, a custom image template, a custom PDF
   template, a notebook with its default cover and one without.
10. **PDFs**: a three-page PDF with annotations and an outline imported
    whole, pages reordered, one deleted, one duplicated, one rotated, a
    page inserted between, a second PDF inserted; settles whether a
    multi-page import is one attachment, and `#55` and `#63` on the iPad.
11. **Math conversion**, a page bookmark, an outline entry; a Whiteboard
    and a Text Document exported (to see whether they export at all); a
    notebook with Layers.

**In OneNote**, from the Windows desktop app (exported as `.one`, as
`.onepkg` and as PDF) and from the same notebook on a personal OneDrive
(the web ZIP), with a page per feature: the pen, pencil and highlighter at
several widths, colours and transparencies, with a pressure stylus and a
mouse; a partial and a whole erase; lasso-moved and resized ink; ink
converted to shape and to text; inline ink words in a text outline; typed
text with styles, lists, a table, tags and an equation; PNG, JPEG and EMF
pictures; a PDF printout with ink over it and the PDF attached; an audio
recording; a page taller than 2,000 pt; nested section groups; a
password-protected section (to see it refused); and, if an iPad is at
hand, a page inked there and synced.

**Tests**: `test_goodnotes_samples.py` imports every file of the sample
folder and fails on anything left out for want of reading it, holds each
page to the Editable export (page order and sizes equal the export's
pages and MediaBoxes; a flat stroke's bbox within 0.3 pt of its `/Ink`'s
and its width exactly `/BS /W`; a ribbon's extent within 0.3 pt of its
annotation's rect; colours bit-exact; a text frame's origin within 0.1 pt
of the export's glyph origin; a picture's rect within 0.1 pt of its
`/Stamp`), and to the Flattened export as a raster oracle for pencils and
ribbons (a pixel IoU floor as inkterop's 96.6 % registered-mode floor);
`test_goodnotes.py` covers what the samples cannot on synthetic files: a
`bv4-` chunk, a multi-block stroke, an `A(x)` count past the bytes, a
truncated block, a UUID that carries, tombstones, a deleted page, a `#3`
re-bind, a `#55` reorder, an attachment named but missing, a zip bomb; and
the shared fixtures pin the flattening and the shape samples as the
Notability tests do. `GAMMA_GOODNOTES_SAMPLES` names a folder of more
notebooks to import the same way. The OneNote tests are the same shape on
the Tika, Interop-TestSuites and self-made files, with one2html run as a
subprocess oracle for the page, stroke, highlighter and picture counts, as
gnnote does.

## Recommendation

Build the GoodNotes import first and from the file: a reader of its own
behind the reviewed import, reusing the Notability import's walk, planner
helpers, composer, picture pipeline, commit and report, with the four
notebook kinds above; the calibration notebooks before the first line of
the stroke decoder, since the open questions (type 4, the pencil's angles,
dashes, partial erases, multi-page PDFs, covers) are each settled by one
file and its export. In the same change, teach the existing PDF-annotation
import what a GoodNotes Editable export is (the appearance path, the
highlighter by `/CA`, blob widths), since Android, Windows and web users
have nothing else. OneNote second, optionally, by the file route with both
packagings and the Cabinet, at gnnote's scope (pages, ink, pictures, text,
printouts as pictures; tables, math, tags and recordings dropped with a
warning): it is worth it only because the PDF fallback yields nothing
editable for OneNote and the Windows 10 app's retirement is sending users
out with handwriting that no shipping importer keeps as strokes; it is a
larger piece of work than GoodNotes for a smaller share of Gamma's users,
so it waits for demand. Decline the Graph route.

## As built (2026-10)

The GoodNotes import is built by the file route
([dev/import_export.md](../dev/import_export.md#goodnotes-notebooks)): a
reader of its own (`gamma/goodnotes_codec.py`, `gamma/goodnotes.py`,
`gamma/rtf.py`), a planner (`gamma/goodnotes_map.py`) that places shapes
and boxes through the Notability planner's helpers, and an import
(`gamma/goodnotes_import.py`) on a base both imports now share
(`gamma/note_app_import.py`). It was built against the public notebooks
(goodparse's six with GoodNotes' Editable exports beside them, its three
older ones, parser-for-goodnotes' four, inkterop's CC0 fixture), not the
calibration notebooks this note asks for first; what those would settle
stays open below. On that corpus every notebook imports whole, and against
the six exports the page sizes are exact, every highlighter, ball pen and
straight shape has its annotation's width and lies within 2 pt of it, and
81 of 85 variable-width strokes lie within 0.5 pt of their record's rect
(all inside it). Where the build departs from the plan above:

- **The pencil** gets `p` from its force and `w` and `o` from the brush
  table's Pencil law at that pressure held upright, as the Sketchbook
  bakes the Pen's law. Its tilt is not written: with the two angle columns
  in dispute, a wrong altitude would widen a stroke up to three times.
- **Covers** are blank sheets of their colour, their band left out: in
  three of the six exported notebooks page 1 is a live cover, so reading
  covers as PDF pages would make most notebooks composed PDFs. A design not
  in the catalogue (Cornell, music, planners, a custom template) is its
  own PDF page, so it looks as GoodNotes drew it.
- **A polyline shape's points** (`#9.#1`) are one message holding the
  points as repeated `#1` messages, not repeated points in `#9` itself.
- **The re-import key** is a digest of every member's path, size and
  CRC-32 from the zip's directory, computed without reading a byte, not a
  hash of the journal and the pages.
- **Left out**, named by kind: Shape tool objects (`#21` with `#9` 0),
  connectors (`#22`), sticky notes (`#20`) and math (`#11`), none of which
  a public file holds. A cropped picture comes in whole, and a turned page
  upright, each counted.
- **Pictures** are picture blocks ([dev/pictures.md](../dev/pictures.md)),
  not the picture boxes first planned (`picture_box`, decoded through
  `picture_bytes` for a crop or a turn), both of which are gone: the file
  is stored as it came, never decoded, its frame and its turn kept as
  numbers (`notability_map.picture_block` through the shared
  `AppImport.store_picture`). A sticker's PDF is rendered to one PNG for
  all the pictures that show it (`goodnotes_import._sticker`).
- **The iPad app** declares `.goodnotes` under an identifier of its own
  (`net.gammapdf.goodnotes`), GoodNotes' own being unpublished; whether
  GoodNotes' declaration hides Gamma from the share sheet is a device
  check ([dev/ipad.md](../dev/ipad.md)).
- **Not built**: the PDF-annotation import's handling of an Editable
  export (the appearance path, the highlighter by `/CA`, blob widths): it
  changes how every foreign PDF's ink imports, so it is a change of its
  own. OneNote waits for demand, as recommended.

## What the sources conflict on, and the reading recommended

- **Whole-stroke erase**: tombstone pairs (gnnote, inkref's library) versus
  records removed (inkterop's one page). Read both the same way: skip
  `#3 = 1`, `#14 = 1` and pointless strokes.
- **Partial erase**: sub-paths in one record (one legacy record), in-place
  trimming (Mac), `#16 erasers` (codec). Handle the first two (a sub-path
  per stroke; a trimmed stroke is just a stroke) and drop `#16` with a
  warning until a sample shows its template's meaning.
- **The flat width**: `W/2` (the exports, inkref's calibration) over
  `W × 6/11` (codec's exporter), `W × 0.25` (goodparse) and `W` as points
  (inkterop).
- **The ribbon's first triple**: a control point by GoodNotes' name, an
  on-curve point by gnnote's test; take every triple as a sample.
- **Type 4's panel layout**: `(x1, y1, x2, y2, r1, r2)` (gnnote, p4g on one
  stroke) versus `(cx, cy, cw, x, y, w)` (codec's writer). Read by the
  outline's arc radii when a sample appears; until then type 4 strokes
  are read as gnnote reads them and reported.
- **The pencil's angle columns**: `(altitude, azimuth)` (gnnote) versus
  `(azimuth, altitude)` (codec). Write `a` from the column whose range
  sits within 0..π/2 and varies slowly, `z` from the other, and settle it
  on the calibration file.
- **Fountain versus brush**: not stored per stroke (inkterop, every field
  identical); codec's `4 = brush` is a table entry. Both become the plain
  Pen.
- **The schema number**: content (gnnote) over platform (inkref,
  inkterop).
- **The template name's digits and `#6.#12`**: codec's readings are
  contradicted by gnnote's bytes; the catalogue UUID and the PDF decide the
  paper, and `#12` is ignored.
- **Image crop and rotation**: p4g's intersection and `#3.#3`, codec's new
  attachment; gnnote saw neither. Read `#3` as p4g does when it differs
  from `#2`, and report.
- **The audio tick**: flicks (codec's names) over p4g's fitted Mach clock;
  neither gives a per-sample time, so nothing is placed on a recording.
- **The highlighter's place**: GoodNotes draws it on top in record order
  with a multiply-like blend; Gamma's highlighter is band 0, under the ink,
  multiplied. The highlighter goes to band 0: under black ink the two look
  alike (multiply over black is black), while a 50 % source-over band in
  band 1 would grey the ink beneath it, and a "sent to back" stroke is
  then no different.
- **The pencil**: GoodNotes' law is unknown; Gamma's own pencil bake from
  the raw force and tilt is the one law that makes a later restyle a
  no-op, and a force-0 pencil is even so it stays visible.
- **OneNote's pressure GUID, colour layout and transparency polarity**:
  the two Python readers' ISF-consistent values over the onenote.rs wiki's.
- **OneNote's stroke width**: the larger of width and height (gnnote) over
  width-else-height (inkterop); both lose the chisel.
- **`.onepkg` on Linux and macOS**: Joplin's 3.6 release note over its
  help page's "Windows only"; the Windows dependency is XPS rasterisation.
- **OneNote for Windows 10/11 and Mac "exports"**: Microsoft's pages (no
  file export) over onenote.rs's README.
- **Covers**: an inference; read them as PDF pages until the calibration
  notebook shows their template.

## Still open

- GoodNotes: the form of a partial erase on a current build and the
  `PEraserStroke` template; type 4's layout and which pen writes it; the
  pencil's angle order, force normalisation and the setting that renders
  identical records 2.4 × apart; `#11` dashes on the iPad; whether a
  multi-page PDF is one attachment; `#55` and `#63` in iPad files;
  `/Rotate` and a non-zero MediaBox origin in a template PDF; the cover
  templates and the full Essentials catalogue (colours, Cornell, music,
  planners; the `_n_m` digits); how an image template is stored; image
  crop and rotation; HEIC, JPEG 2000 and GIF attachments; the sticky
  note's children in a real file; the math group's strokes; the content
  hash's algorithm; `#30.#3`/`#7`, `#54.#17`, `#15`, `#9.#5`, metadata
  `#6`; Layers, Whiteboards and Text Documents; the `.goodnotes.zip`
  layout; whether free accounts can export `.goodnotes`; Goodnotes' Terms
  clause on format analysis; whether the app re-imports its Editable PDF
  as native objects; the Flattened export's recognition layer; a sample
  from the Android, Windows or web builds.
- OneNote: a partly erased stroke, tilt, non-zero transparency, an
  iPad-made notebook; the pressure-to-width law; the printout's source
  PDF and `0x200035C1`; the Mac backup files; the cloud cipher; whether
  `includeInkML` is supported on v1.0; the run-index byte layout, read
  from gnnote's reader rather than the spec; `.onex`.
- Gamma: stickies with text, recognised handwriting as search text,
  outline entries as sheet titles, strokes placed on a recording once the
  tick's unit is known, a cover read as a sheet, a vector sticker kept as
  vector art rather than a PNG, a GoodNotes crop applied (once where `#3`
  cuts the image is confirmed), the RTF reader's scope, and the
  Notability import's helpers generalised (the `Media` and `Shape` inputs)
  rather than copied.
