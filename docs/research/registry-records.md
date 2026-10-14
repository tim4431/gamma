# What the registries return for a citation

Probed 2026-10-13 by calling the metadata router's own registry functions
(`_fetch_doi`, `_fetch_arxiv`, `_crossref_search`) against doi.org,
DataCite, Crossref's REST API and the arXiv API, then running each record
through `bibtex.build_entry`. The question was whether the BibTeX Gamma
caches, copies and exports is right. It was not, in five ways that any
library hits; the mechanics as built are in
[dev/paper_metadata.md](../dev/paper_metadata.md#metadata-fetch--edit--cite)
and [dev/import_export.md](../dev/import_export.md#bibtex-bibliography).

## What came back

| Identifier | What the registry returned | What it did to the entry before this pass |
|---|---|---|
| `10.1051/0004-6361/201833910` (Planck 2018 VI) | CSL title `<i>Planck</i>\n   2018 results`, subtitle apart (`VI. Cosmological parameters`), first author `{"name": "Planck Collaboration"}`, container `Astronomy &amp; Astrophysics`; doi.org's BibTeX keyed `2020` with the same markup inside. | Title stored with `<i>`, so the title match failed and the record was flagged unverified; the subtitle was lost, so the entry named none of the twelve Planck 2018 papers; the first author became `""`, which crashed `default_key` (a hand edit answered 500) and printed `author = { and N. Aghanim …}`; `&amp;` went into the `.bib` as it is, a LaTeX error. |
| `10.1038/nsmb.2660` | container `Nature Structural &amp; Molecular Biology`, in the CSL JSON and in doi.org's BibTeX alike. | `&amp;` in the venue, in the popover and in both renderings. |
| `10.1021/acs.jpclett.9b00599` | title `… CsPbCl<sub>3</sub> Nanocrystals`. | `<sub>` in the popover and in the entry; normalized to `cspbcl sub 3 sub`, so the title match failed. |
| `10.1109/CVPR.2016.90` (ResNet) | type `proceedings-article`, container the conference name, publisher IEEE; doi.org renders `@inproceedings{…, booktitle=…}`. | The cached registrar entry was right, but a hand edit rebuilt it as `@article{…, journal={2016 IEEE Conference …}}`; so did the Crossref-search fallback and the AI path. |
| `10.48550/arXiv.1706.03762` (DataCite) | type `article`, no container; BibTeX `@misc{https://doi.org/10.48550/arxiv.1706.03762, …}` — the key is a URL. | A built entry was an `@article` without a journal. The URL key is legal BibTeX and stays; the pin covers it. |
| `10.1002/9780470316801` (Wiley book) | type `monograph`, ISBN list, container the series name; doi.org renders `@book` with the subtitle in the title. | Fine as `@book`; the subtitle was dropped from the stored title. |
| arXiv `1602.03837` (LIGO) | authors `The LIGO Scientific Collaboration`, `the Virgo Collaboration`; `journal_ref` `Phys. Rev. Lett. 116, 061102 (2016)`; `doi` of the PRL article. | `@article{collaboration2016, journal = {Phys. Rev. Lett. 116, 061102 (2016)}}` — the reference line typeset as a journal name, volume and pages missing. |
| arXiv `hep-th/9711200` (Maldacena) | `journal_ref` `Adv.Theor.Math.Phys.2:231-252,1998`, `doi` `10.1023/A:1026654312961`, whose registrar record is the IJTP reprint (1999, vol. 38). | Same shape; the published DOI was never followed. |
| arXiv `2301.00001` | no `doi`: arXiv's API does not report the DataCite DOIs it registers for every preprint, only an author-supplied journal DOI. | — (so following the `doi` field never lands on arXiv's own `10.48550/` DOI; the guard against it is belt and braces). |
| Every doi.org entry | pages as an en dash (`436–444`), `month=May` as a bare macro, `url=http://dx.doi.org/…`, DOI lowercased; the key is `Surname_Year`, with underscores inside a multi-word surname (`Das_Adhikari_2019`). | All legal BibTeX. The key style differs from the generated `surname2019`, which is why the pin and the case-insensitive uniqueness exist. |

Crossref's REST API (`_crossref_search`) returns the same markup and
entities, and `type`, which was not being selected.

## What was decided

- **Plain text in the record, LaTeX in the entry.** The stored title and
  venue read as the PDF prints them (`CsPbCl3`), because that is what the
  title match compares and what the popover shows; markup is turned into
  macros only where it is typeset (`bibtex.latex_text` for built values,
  `bibtex.clean_entry` for doi.org's rendering). An inline tag leaves
  nothing behind, a block tag a space — OpenAlex's cleaning used to insert a
  space for every tag, which split `CsPbCl3`; both now share
  `textnorm.plain_text`.
- **Subtitle joined with `: `.** Confirmation also accepts the title and
  the subtitle appearing separately in the head, since the PDF sets them on
  different lines.
- **The published record wins over the preprint's.** arXiv's `journal_ref`
  cannot be parsed into journal/volume/pages reliably; the DOI it names
  resolves to the registrar's record and BibTeX. The arXiv id stays on the
  record, so the entry keeps `eprint`/`archivePrefix` and the popover its
  arXiv row. Guarded by a 0.9 title similarity against mistyped DOIs.
- **One item type, under Zotero's names.** A first pass kept the
  registry's CSL type as a `work_type` field beside `kind`; it was replaced
  the same day by one vocabulary for `kind` itself, Zotero's item-type names
  (`gamma/item_types.py`), which every source maps onto and every output
  translates from. Zotero's names because they are CSL's types under another
  spelling and make a Zotero round trip the identity; only the nine types a
  paper library holds are kept. A Crossref type is a registration label,
  missing for every source but doi.org and Crossref, so it is an input, not
  the model. BibTeX's 14 types are an output with print-only distinctions
  (`@phdthesis`/`@mastersthesis`) and no preprint. The mechanics:
  [dev/paper_metadata.md](../dev/paper_metadata.md#metadata-fetch--edit--cite).
- **`@misc` for a record with neither venue nor arXiv id.** An `@article`
  without a journal is a BibTeX warning in every style.
- **Case-insensitive key uniqueness**, because classic BibTeX compares
  keys that way; biber does not, and a file both read is the point.
- **Not changed**: the registrar's own rendering stays the cached entry
  when doi.org served one (its key style included); `@article` with
  `journal = {arXiv preprint arXiv:…}` for bare preprints, since cached
  entries carry it; no case-protecting braces around titles; no `number`
  or `month` in built entries. The Zotero RDF export now writes each item
  type as Zotero does, with publisher and ISBN; those shapes are taken from
  Zotero's translator source and have not been imported into a live Zotero.
