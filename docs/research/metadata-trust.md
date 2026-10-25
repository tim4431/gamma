# When a looked-up record is this document

Surveyed 2026-10-25, after a hand-written note was given a textbook
chapter's record. The question was how the metadata lookup decides that a
registry record describes the PDF in hand, and why the popover showed no
warning. The mechanics as built are in
[dev/paper_metadata.md](../dev/paper_metadata.md#metadata-fetch--edit--cite).

## The case

The note was titled "Finite-Depth Quasi-Phase Matching and Converter
Efficiency", by one author, with no DOI or arXiv id near its top. The lookup
asked Crossref with the first page's largest type. No registered work has
that title, so Crossref answered with related ones, among them the SPIE
*Field Guide to Nonlinear Optics* chapter "Quasi-phase-matching"
(`10.1117/3.1002081.ch35`, no authors listed). The picker took a hit
whenever its normalized title, at least 15 characters, was a substring of
the text head. "quasi phase matching" is 20 characters and sits inside the
note's own title, so the chapter was stored as confirmed: source "doi.org",
type book chapter, no warning, and the page renamed after it.

## What the survey found

- **The AI never saw a Crossref hit.** AI extraction ran only when no step
  had produced a record, and the Crossref step had already set one.
- **`meta.source` hid the evidence.** "doi" covered eight paths: a DOI from
  the paper's address or the publisher's tags, a DOI printed in the text
  and title-checked, an unconfirmed DOI fallback, a Crossref search hit, an
  arXiv record upgraded to its published DOI, a DOI the AI produced, a
  Crossref hit found with the AI's title, and a DOI the user typed. Nothing
  recorded which.
- **The title test was a substring test.** It was not word-bounded ("ion
  trapping in cavities" matched inside "fusion trapping in cavitiesx"), a
  subtitle had no minimum length (a subtitle "a" matched anywhere), and a
  title passed from anywhere in the 20,000-character head: a phrase of a
  longer title, a section heading, a reference list. For a document shorter
  than the head the reference list is always inside it.
- **The same test confirmed more than Crossref hits.** An arXiv id or a DOI
  printed in the text was confirmed when its record's title passed it, so a
  short paper whose reference list prints a cited preprint's title and id
  got that preprint's record. An ISBN was confirmed by a sentence citing
  the book. A DOI or arXiv id the AI produced was accepted whenever it
  resolved, whatever work it named.
- **A Crossref search title has no subtitle.** The search returns the main
  title only ("Planck 2018 results"), which every volume of a series shares;
  doi.org's record joins the subtitle.
- **An unconfirmed record renamed the page.** The automatic-title rename ran
  for every stored record, and once it ran the marker was gone, so a later
  correct lookup could not rename the page.

## What was decided

- Every record stores `meta.match`, what tied it to the document, and the
  unverified flag follows from it. The popover's Source row says it for a
  confirmed record ("doi.org · confirmed by AI"), and the warning's hint
  says why for an unconfirmed one.
- A record found through the text is confirmed only by the title page:
  its title is the document's own (the largest type, or whole lines that
  are no numbered heading, 15 characters at least), and one of its authors
  is printed by it, not merely in the reference list. A phrase in the text
  only makes it a record the text names. This needs no AI, so publisher
  PDFs found by title still resolve offline. A review of the first cut
  showed how often a plain "an author is named" test misses the paper's
  own record: two-letter surnames, accents spelled plain on one side,
  affiliation marks glued to names, and records that list no authors. A
  second review then showed the rule confirming wrong records wherever a
  line could pass for a title: an unnumbered heading or a CV entry with a
  cited author under it, a reference that wraps its title onto a line of
  its own, a series' main title, numbered parts. The rule now looks at the
  first page's largest type when there is one and at the first page's
  lines only when there is not, names are folded for the forms PDFs print
  (OT1 accent marks, undecomposed letters, affiliation letters glued on),
  a record whose title is the document's own but whose authors are not
  found is its own unconfirmed `title_only` (first in line for the AI),
  and a record whose doi.org fetch fails is never confirmed by the title
  page. A last adversarial pass found the rule still confirming records
  that list no authors by the largest type alone (a supplement's
  component above its article, a textbook chapter on a note's topic, an
  Elsevier masthead's front-matter page), a printed ISBN by a reference or
  a syllabus naming the book, "Comment on X" as X, and "sense" as Sen. None
  of those is confirmed now: no author, no confirmation; an ISBN needs its
  book's title page; a leading phrase makes another work; a name is a
  capitalized word. Letting title-block lines confirm beside the largest
  type, to catch titles it misses, reopened headings, CV entries and
  syllabus textbooks in one more pass, so they only rank a record now:
  where there is a largest type, only it confirms without the AI. The
  rounds converged on that trade: every widening of the deterministic
  rule confirmed some other work, and what it leaves unconfirmed is safe —
  flagged, or checked by the AI first.
- The records the text names go to one AI call that picks the one that is
  this document, if any. Asking per record would cost a call each; asking
  once shows the AI the alternatives side by side. A refused or failed call
  keeps the first record flagged rather than failing the lookup, since a
  used-up allowance says nothing about the record.
- The web page's own record still beats the named records without an AI
  call: the publisher's tags are trusted like the DOI they carry.
- An unconfirmed record no longer renames the page.

Declined: confirming every Crossref hit with the AI, including title-page
matches. It would make the common publisher case depend on a configured
model and cost a call per lookup, for no gain over the title-and-author
test.

## Still open

- Records stored before `meta.match` keep the flag they were stored with.
  The note's chapter stays unflagged until its page is refetched.
- Identity matching still reads `meta.doi` without the flag: the clip's
  dedup (`clip.find_page`) and the library's `findPageForUrl` could match a
  wrongly recorded page to the real work.
- What the AI context, the translation brief and the exports say about a
  paper comes from its record, flagged or not.
- `_find_doi_candidates` stops at eight DOIs with their variants, so a
  paper's own DOI printed only in the end trailer is never tried when the
  head cites eight or more.
- A replaced PDF keeps the page's `web_meta` and `web_url`, whose
  identifiers stay trusted outright for the new file.
- A registry record that holds only the main title of a PDF printing
  "Main: subtitle" ties as a phrase, not as the title page: a series' main
  title cannot be told from it.
- Without the AI these mainstream layouts stay flagged (`title_only`, or
  `id_only` for an own DOI): a title the largest type misses (sharing its
  size with "Abstract" or a heading, a two-word title, an Elsevier
  masthead set larger than the title), a cover page before the title
  block (an Accepted Manuscript), and authors whose two-letter surnames
  carry glued affiliation letters ("Lia,b"). With the AI on, one check
  settles each.
- Records found without AI by the title of a book whose title page has a
  largest type: the ISBN alone confirms nothing, so they wait for the AI
  too.
- Without a largest type, a short note whose first page carries a
  reference that wraps its title onto a line of its own, with an author on
  the line above, still reads as that work's title page.
