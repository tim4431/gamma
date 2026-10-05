# First-run onboarding: how others do it

Survey done September 2026 before designing Gamma's guide
([docs/dev/onboarding.md](../dev/onboarding.md)). Findings only.

## What was looked at

| Product | Shape | What to take |
|---|---|---|
| **VS Code** (Walkthroughs) | Declarative: an extension contributes `walkthroughs` in `package.json`, each step with markdown/media and `completionEvents` (`onCommand:`, `onSettingChanged:`, `onContext:`, `onLink:`, `onView:`). Steps tick themselves when the event fires; the Getting Started page lists walkthroughs with progress. | The closest match to "reconfigurable": steps as data, completion from named events, no step knows about UI code. Gamma's tours + event catalog copy this. Weakness: it points at nothing in the UI; you read and go find it. |
| **Notion** | Content-first: a Getting Started page with instructions inside the product's own blocks, templates, then a sidebar checklist with a progress bar. Tooltips are rare and one-off. | The welcome page as a real page you edit and can delete. The checklist that persists and counts things you did on your own. |
| **Linear** | Sample issues in a sample project, a "Getting started" checklist in the sidebar with items that complete from real actions (create an issue, invite a teammate), a few coach marks on first visit to a view, keyboard shortcuts taught by showing the key beside the action. | Task completion from real state, not from clicking Next. Sample data that the first tasks act on. |
| **Figma** | Interactive: the first file is a tutorial file; coach marks anchored to tools with "Try it" tasks; later features arrive as single dismissable tips on the tool. Inspect overlay labels layers. | Anchored spotlights that let you act on the real control. The anchor inspector idea. Feature tips bound to a state, not a sequence. |
| **Logseq** | Seeds a "tutorial" page into every new graph (markdown, with tasks, shortcuts in a table) and a "How to take dummy notes" page. No tour. | Gamma already does this for guests (since, for every new account: `seed.seed_welcome`). Content is cheap and users keep it; it cannot point at anything. |
| **Obsidian** | The Help vault (a sandbox vault of docs) and the empty-state "Create new vault / Open folder" screen. No tour, no checklist. Onboarding relies on the community. | An explicit sandbox to break things in. For Gamma the guest workspace is that sandbox. |
| **Zotero / Paperpile / Readwise Reader** | Zotero: none in-app (docs). Paperpile: a short checklist plus sample papers. Reader: sample documents plus a checklist ("highlight, add a note, ghostreader"), coach marks on the highlight toolbar. | Reader's onboarding is the nearest domain match: the first task is "select text, highlight". Sample documents ship with the account. |
| **Slack / Superhuman / Arc / Raycast** | Slack: coach marks with a pointer, once per surface. Superhuman: a human 30-minute onboarding, then in-app prompts to learn one shortcut per day. Arc: a scripted walkthrough with tasks and skip. Raycast: a "Walkthrough" command you can rerun. | Rerunnable from a menu. One thing at a time. Never block; always Skip. |
| **Tour libraries** (Driver.js, Shepherd.js, Intro.js, react-joyride) | Overlay with an SVG or box-shadow cutout around the target, a card positioned by a floating-position library, steps as an array of `{element, popover}`, async `beforeShow` waits, progress. Product-analytics tour builders (Appcues, Pendo, Chameleon) select elements from the live DOM. | The spotlight-via-mask technique and the step schema. The documented failure mode is universal: tours select by CSS class or generated id and rot on the next redesign. Every vendor recommends stable `data-*` attributes. That is why Gamma's registry exists and why class selectors are banned. |

## Patterns that recur

- **Sample content is what makes a tour possible.** Every product with a tour
  that teaches an action on content ships the content (a tutorial file, sample
  issues, sample documents). A tour over an empty workspace can only point at
  buttons.
- **Completion from real events beats Next.** VS Code, Linear and Reader all
  mark steps done from the action itself, and users who never open the tour
  still get credit in the checklist.
- **Three surfaces, not one.** Content (page/template), sequence (tour),
  state (checklist + tips). Products that only have one of them either force a
  tour on everyone (and get it skipped) or never point at anything.
- **Consent, then spotlight.** Nobody good starts dimming the screen on first
  paint. An invitation card, then the tour.
- **Rerunnable and versioned.** A menu entry to rerun; a version so a redesign
  can re-offer.
- **Selectors are the maintenance cost.** The one architectural decision that
  determines whether a tour survives a year of UI work is how steps find their
  targets.

## What Gamma took

- VS Code's data-driven steps with completion events.
- Figma's anchored spotlights with real interaction through the cutout, and
  its single tips on a tool, bound to a state (Gamma's hints).
- Reader's first task: select text, highlight, see the note.
- A seeded welcome page (already there for guests) upgraded to a markdown
  source plus a sample PDF rendered from it, so the sample content needs no
  binary and cannot drift from the copy.
- Stable `data-guide` anchors with a registry and tests, because that is the
  documented point of failure everywhere else.

## Not taken, and why

- A third-party tour library: the positioner and mask are small, the app
  already has its own popover system and design tokens, and the libraries'
  step schema is the part worth copying, not the code.
- Human or video onboarding: out of scope for self-hosted software; the
  website and the user guide cover the long form.
- Forced linear onboarding before the app is usable (the Arc shape): wrong for
  a tool people install on their own server and often open with a paper in
  hand.
- A persistent Getting-started checklist (Linear, Notion, Reader): designed,
  then turned down in the September 2026 UI review. Triggered tours and hints
  come after the user did the thing, and a hint's `doneOn` retires it once
  the feature was found unaided.
- An anchor inspector overlay (Figma's inspect): not built. The registry
  describes every id, and the tests name each step whose anchor went away.

## October 2026: a review of the guide as built

Gamma's own tours, driven in a browser and rendered in light and dark
themes before the "show, not tell" changes
([docs/dev/onboarding.md](../dev/onboarding.md), "Showing, not telling").
Findings only.

- **Steps blocked on the user.** Every step that asked for an action took
  Next away. Arrange windows needed three real gestures before it let go,
  and its spotlight was a 60 px title with nothing showing what a
  double-click or a drag looks like. The drop zones its copy mentioned are
  invisible until a drag starts.
- **Animations sat beside the thing, not on it.** The Add tour showed a
  wireframe of the Add popover 40 px below the real one. The drawings were
  grey-bar wireframes with loops between 4.4 and 6 s, looping for as long
  as the card was up, and some frames of a loop were nearly empty.
- **Layering.** A step inside a popover dimmed the popover except for the
  anchor, so the popover looked cut in half. The demo pointer rested on
  popover rows and on the text it typed. The note demo's card covered the
  Notes header.
- **Text.** About 1,900 words across the tours and hints. Cards narrated
  what the demo was already showing, under a status chip, a step count, a
  progress bar and two buttons.

What changed: light steps, scenes played on the real controls, popovers
undimmed as one surface, and one pointer and timing scale. Step
definitions went from 93 to 71 and the words on step cards from 1,627 to
828. Two drawings stayed, for the two ideas that have no place on screen.
