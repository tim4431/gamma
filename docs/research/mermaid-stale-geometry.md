# Mermaid diagrams drawn as dots: stale SVG geometry in Chrome

The question: in one Chrome installation every Mermaid diagram rendered
with its nodes as tiny coloured marks inside huge empty subgraph boxes,
the whole drawing scaled down to fit. Other browsers, and other copies of
the same Chrome build, drew the same note correctly. What was it? Measured
in October 2026; the workaround now in force is in
[dev/diagrams.md](../dev/diagrams.md#mermaid).

## What the broken browser reported

Chrome 155.0.8059.41 on Windows 11 (4K screen at 200%, the Gamma site at
90% page zoom, so DPR 1.8). A two-node `flowchart TD; A --> B` came out
742 × 1309 (ELK) or 2092 × 2038 (dagre) instead of 168 × 146 / 168 × 156,
on the app's page and equally on a bare same-origin JSON page with none of
the app's code running. A real note with three subgraphs came out
22092 × 26038 user units, drawn 628 px wide.

Mermaid measures a label inside a `foreignObject` first sized
`min(10 × width, 16384)` px (1200 px for a plain node), reads the inner
`<div>`'s `getBoundingClientRect()`, shrinks the box to it and shifts the
label group with a `transform`; the node shape then goes in and the layout
takes the node group's `getBBox()`. In the broken browser:

- the `<div>` measured correctly (`120 × 21` for "A");
- the node group's `getBBox()` was `1276 × 1224`, exactly the healthy
  `152 × 48` shape united with a 1200 × 1200 box at the origin;
- the `foreignObject`'s own `getBBox()` stayed `1200 × 1200` with its
  attributes already `120 × 24`, and its group's box stayed at `0,0`
  although its `transform` was `translate(-60, -12)`.

So `getBBox()` kept answering with the geometry these elements had at their
first layout. Every node grew by its measuring box; dagre and ELK then
spaced ranks and subgraphs accordingly.

## What did not change it

The same Chrome binary in a fresh profile drew correctly in every
combination tried: Playwright's Chromium, installed Chrome 155.0.8059.40 and
.41, headless and a visible window, real page zoom 90%–180%, forced device
scale factors 1.25–2, interface size 1.1, the gamma-light theme, a live
PDF viewer (pdf.js) on the same page, the user's field-trial seed and
randomization sources copied into the profile (27 variations active), and
the desktop app's Electron 38 (Chromium 140). In the broken installation,
Incognito, a browser restart (which also applied a pending update) and
four render-host styles (off screen or on, hidden or transparent) all
still failed; no extension was allowed in Incognito, the measuring
functions were native, and no Chrome flags were set. The deployed bundle
was byte-identical to a local build apart from chunk hashes.

What refreshed the geometry, tried in the broken browser:

| Attempt | Two-node result |
|---|---|
| Forced reflow (`body.offsetHeight`) before `getBBox()` | unchanged |
| Setting `width`/`height`/`transform` to another value and back | unchanged |
| Rewriting each element's `style` attribute | unchanged |
| `display: none` on the measured group, reflow, restore | final size right, layout still inflated |
| `display: none` on every element of the subtree | `foreignObject` sizes fresh, a group's `transform` still stale (nodes 196 wide, not 152) |
| The box computed from the DOM: attributes, transform lists, the browser's box only for leaves | `168 × 146`, `168 × 156`, and the real note `1068.135 × 646.594`, matching a healthy browser to the decimal |

The last row is what was built. On a healthy Chrome it matched the native
`getBBox()` on all 111 calls Mermaid made across eleven diagram types
(flowcharts with odd shapes, subgraphs and math, sequence, class, state,
ER, gantt, pie, mindmap).

## Still open

Why this installation: the trigger was never reproduced outside it. A
field trial assigned to the installation stayed the likeliest explanation,
but the copied seed did not activate it. No Chromium bug has been filed;
a reduced test case is the probe in `shared/lib/svgGeometry.js`. When the
probe no longer fires in that browser, the computed boxes can go.
