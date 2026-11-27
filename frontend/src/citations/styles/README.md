# Citation styles

The citation styles Gamma ships, in the Citation Style Language: Zotero's
bundled set (APA 7th edition, the three Chicago styles, IEEE, Nature, MLA,
the two Harvard styles, AMA, ACS, APSA, ASA, MHRA notes and NLM/Vancouver)
and the American Physical Society's. The browser renders citations and
bibliographies with them through citeproc-js
([cslCore.js](../cslCore.js)); each file is a lazy chunk of its own
([cslAssets.js](../cslAssets.js)), fetched the first time something is
rendered in that style. [bundledStyles.js](../bundledStyles.js) and
tests/shared/citationstyles.json list them with the title, short title and
class each file's `<info>` gives; styles a user adds live on the server
(routers/citations.py), not here.

These styles come from the [Citation Style Language](https://citationstyles.org/)
project: [citation-style-language/styles](https://github.com/citation-style-language/styles),
commit `e27b9b7e204b1a40998f774ff05c8a6ed6a5f7d8`, unchanged. They are
released under the Creative Commons Attribution-ShareAlike 3.0 Unported
license ([LICENSE](LICENSE)); the project asks software that uses them to
mention the CSL project and link to [citationstyles.org](https://citationstyles.org/),
which Settings → Citations does.

| File | Bytes | SHA-256 |
|---|---|---|
| american-chemical-society.csl | 10783 | `6dac0308d4c0e2b462ef4c7607b970fda9be9a730784c93568e6e3cc8639bce6` |
| american-medical-association.csl | 11575 | `d70f843d93c2b1e8c59c0e26e2396bea4ed99fb4a1aac69b6dddc249a10fadf7` |
| american-physics-society.csl | 8034 | `cbe194ca976f7ca152b7e889dff8c261273754140ac6dafca47ce64eab6b54ba` |
| american-political-science-association.csl | 8159 | `ab2794dbdfcb4930044d03ee250d8fbb9a27c441b741b7b265c2b174008045a2` |
| american-sociological-association.csl | 12848 | `da8f401c2af2c405a1b58836089a7fe287311207028dd569070ed3f035521f1f` |
| apa.csl | 85817 | `3917def5fc951b104d64216c3373096042368b9704ba269ca83c14ebaba889fc` |
| chicago-author-date.csl | 167761 | `002fade78d7e4fe9d42936a16b43a8066b097013f6255df40b1bfba6631eff9b` |
| chicago-notes-bibliography.csl | 242663 | `4b6be4bceaf8f3c31b49331c9f9e38c977f666d6ab80e19a2ac8482c7511abeb` |
| chicago-shortened-notes-bibliography.csl | 176402 | `bb6e16b492930fab11581c5407a98fd5a203010944ccb3854e61734f3d27bd4c` |
| elsevier-harvard.csl | 8102 | `71e5cbd5906f4c1a03f0772a3d8b014ac06fe3f87a848397b94c66139003568c` |
| harvard-cite-them-right.csl | 10569 | `0ca3d2c7c881cd98cdd81098b5349f5921edf430498aa9a5a41918621cf7cc45` |
| ieee.csl | 17587 | `b4c7619fc16c45a31e4cc3271eab94ffe83192d3b4c7fc729470a3b459448de3` |
| mhra-notes.csl | 138019 | `9fa37e7faabf28795309b74b42c459b9586fd8f58529ed2db9f40722db3a7637` |
| modern-language-association.csl | 44381 | `e1432698ef28938d044b06bd45ddcc57d6b974cec25f71007075646a11f99484` |
| nature.csl | 6444 | `15b1272a3c360168e0b51ab9257534b08ced67190ed57be7a1c32cea8fc87be8` |
| nlm-citation-sequence.csl | 18126 | `492f7935df42054b33d515f7275122f419780df990ea35925dcbbff0fecf1137` |

frontend/tests/citations.test.mjs checks each file against its size and
hash here (with LF line endings, as the repository has them). To move to
newer styles, take the files from one commit, put its hash and theirs in
this table, and update the titles in bundledStyles.js and
tests/shared/citationstyles.json if a file's `<info>` changed.
