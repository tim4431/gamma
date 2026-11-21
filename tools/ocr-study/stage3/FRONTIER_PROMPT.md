# The frontier pass's prompt

The prompt each of the six agents got (Claude Opus 5.5, reasoning effort
medium), word for word but for the paths and ids. `frontier.py prompt <k>`
prints it filled in for group k: `{SCAN}` is the synthetic scans' folder,
`{OUT}` the readings' folder, `{ROOT}` the Stage 3 working folder, `{K}`
the group and `{IDS}` its crop ids.

---
You are standing in for a hosted vision model that an app sends a scanned equation image to, asking for LaTeX. Do exactly one reading of each image, as such a model would.

For each id below, Read the image {SCAN}/<id>.png (and nothing else) and transcribe the printed display equation into LaTeX:
- amsmath LaTeX for the body only, no \[ \] or $$ delimiters, no equation number;
- one pass per image: do not render, compile or check your LaTeX, and do not revisit an image;
- if an image holds several rows, separate them with \\.

ids: {IDS}

Strict rules, since this is a measurement:
- Read ONLY those PNG files. Do not list, open or search any other file or folder (no gt, clean, json, tex or other engines' outputs anywhere under {ROOT}).
- When done, Write one file {OUT}/_batch{K}.json: a JSON object mapping each id to its LaTeX string (properly JSON-escaped backslashes). Every id must be present; an unreadable one maps to "".
Return only the number of ids written.
