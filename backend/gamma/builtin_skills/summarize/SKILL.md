---
name: summarize
description: A structured summary of the paper or pages in context — the question, the method, the main results with their evidence, and the limits.
argument-hint: "[focus or length, e.g. 'for a journal club' or 'one paragraph']"
---

Summarize the paper (or the pages, notes or selection) you have in front of you.

What the person asked for, if anything: $ARGUMENTS

If that is empty, take the request from the conversation. If it asks for a focus, a length or an audience, let it shape everything below; it wins over these defaults.

## First, find the material

- Work from the text you can actually read: the document in context, the passages quoted in the message, or the files and pages you can open.
- If you can open the paper but have not read it yet, read it before writing. Skim the abstract, introduction and conclusion first, then the methods and results sections, figures and tables.
- If you have no paper at all, say so in one line and ask which one to summarize. Do not summarize from memory of a title.
- If you see only part of it (a few pages, an excerpt), say which part, and summarize only that.

## Then write the summary

Use these sections, each short, in this order:

1. **In one sentence** — what the paper does and what it finds, in plain words.
2. **The question** — the problem, why it matters, and the gap in earlier work the authors claim to fill.
3. **The approach** — the method, data, model or experimental setup, with the details a reader needs to judge the results (sample sizes, datasets, baselines, key assumptions).
4. **The results** — the main findings, each with the evidence behind it: the number, table, figure or theorem, and where it is (e.g. "Table 2", "Fig. 3b", "§4.1").
5. **Limits and open questions** — what the authors say the work does not cover, and anything you notice yourself, marked as yours.
6. **Why it matters** — who should care and what it changes, in two or three sentences.

## Rules

- Keep the authors' claims and your own reading apart: "the authors report…" versus "this suggests…".
- Never invent numbers, citations or results. If something is unclear in the text, say it is unclear.
- Keep the paper's terms and notation, and define a term the first time it appears if a newcomer would not know it.
- Prefer concrete statements ("cuts error by 12% on ImageNet") over vague ones ("improves performance").
- Default length: about 300–500 words. Write in the language the person wrote their message in.
- Use Markdown headings or bold labels and bullet points, so the summary can be pasted into notes as it is.
