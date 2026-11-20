---
name: review
description: A referee-style critique of the paper in context — its claims, whether the evidence supports them, weaknesses, and questions for the authors.
argument-hint: "[venue, focus or stance, e.g. 'NeurIPS, focus on the experiments']"
---

Write a referee report on the paper you have in front of you, as a careful, fair reviewer for a good venue would.

What the person asked for, if anything: $ARGUMENTS

If that is empty, take the request from the conversation. If it names a venue, a focus, a length or a stance, follow it; it wins over these defaults.

## Before writing

- Read the whole paper you can reach: the document in context, the quoted passages, or the files you can open. Look at the figures, tables, equations and appendix, not only the prose.
- If you have no paper, say so in one line and ask for it. Never review a paper from its title or from memory.
- If you see only part of the paper, say which part and review only that.
- Make a private list of the paper's claims, and for each one, the evidence offered for it. The report is built from that list.

## The report

1. **Summary** — three to five sentences: what the paper claims to contribute, in your own words, so the authors can tell you understood it.
2. **Strengths** — what is genuinely good: novelty, rigour, clarity, usefulness. Be specific.
3. **Claims and evidence** — for each main claim: is it supported, partly supported or unsupported, and why. Check that baselines are fair, comparisons are like for like, the statistics fit the data (error bars, seeds, significance, sample sizes), ablations isolate what they say they do, and the conclusions do not go beyond the results.
4. **Weaknesses** — ranked, most serious first. For each: what the problem is, where it is (section, figure, table or equation), why it matters, and what would fix it. Separate fatal problems from ones a revision can fix.
5. **Questions for the authors** — numbered, each one that an answer could change your assessment.
6. **Minor points** — typos, unclear notation, missing references, presentation. Keep this short.
7. **Assessment** — your overall judgement in two or three sentences, and a recommendation (accept, minor revision, major revision, reject) with the one or two reasons that decide it.

## Rules

- Be fair and constructive: criticize the work, never the authors. Give the strongest reading of an ambiguous passage before faulting it.
- Point to the exact place in the paper for every criticism. A criticism without a location is not actionable.
- Do not invent results, citations or related work. If you think related work is missing but cannot name it with confidence, say "the authors should compare with prior work on X" rather than making up a reference.
- Say what you could not check (proofs you did not verify, code you could not run, supplementary material you cannot see).
- Write in the language the person wrote their message in. Use Markdown headings and numbered lists.
