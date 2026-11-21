---
name: explain
description: Explains the selected passage, an equation or a concept from the paper at the level asked for, from first intuition to the details.
argument-hint: "[what to explain and for whom, e.g. 'eq. 3 for an undergraduate']"
---

Explain something from the paper to the person, at the level they need.

What they asked for, if anything: $ARGUMENTS

If that is empty, take the request from the conversation.

## Work out what to explain

- If the message quotes a passage, selects text or names an equation, figure or section, explain that.
- Otherwise, if it names a concept, explain that concept as this paper uses it.
- If it asks for nothing specific but a passage is quoted, explain the quoted passage.
- If you cannot tell what they mean, ask one short question instead of guessing.
- Read the surrounding text in the paper (the definitions, the section it sits in, the notation) before explaining, so your explanation matches the paper's own meaning, not a textbook's that differs from it.

## Work out the level

- If they say who it is for ("like I'm new to the field", "for an expert", "for my students"), aim there.
- Otherwise assume a smart reader from a neighbouring field: they know general science and maths, but not this subfield's jargon.

## The explanation

1. **The short answer** — one or two sentences saying what it is or what it means.
2. **The intuition** — why it is there and what it does, with an analogy or a small concrete example where it helps. Say where the analogy breaks down.
3. **The details** — the precise version: for an equation, what each symbol is, what each term contributes, and what happens in the limiting or simple cases; for a method, the steps in order; for a result, what was measured and what it shows.
4. **How it fits the paper** — why the authors need it and what depends on it later.
5. **Check yourself** (only when it helps) — one question or a tiny worked example the person can use to test that they understood.

## Rules

- Define every term and symbol the first time you use it. Do not answer jargon with more jargon.
- Keep the paper's notation; if you introduce your own, say so.
- Separate what the paper says from your own explanation or background knowledge, and never put words in the authors' mouths.
- If the passage is ambiguous or seems wrong, say so plainly and explain the most likely reading.
- Be as short as the question allows: a simple question gets a short answer, and the later sections can be skipped.
- Write in the language the person wrote their message in. Use Markdown, and LaTeX between $…$ for maths.
