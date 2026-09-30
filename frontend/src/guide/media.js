// The illustration registry: the pictures a guide card may show above its
// copy, by id — the same contract as the anchor registry, so a tour names an
// illustration and never a file path (docs/dev/onboarding.md, "Illustrations").
//
// A step's `media` is an id here; the drawing is `guide/media/<id>.svg`,
// inlined into the card by GuideOverlay so it reads the theme's tokens
// (`var(--accent)`, `currentColor`) — eight themes, so a picture that
// carried its own colours would be wrong in six of them. Each drawing keeps
// its own `<style>` with its animation; guide.css stops every one of them
// under `prefers-reduced-motion`.
//
// ratio: the drawing's width over its height. The card reserves the box from
// this before the drawing is laid out, so a step with a picture never moves
// its own card after placement (`place.js` measures the card once).
// Keep it between 1.4 and 2.2: the card is 320 px wide, so a taller
// picture pushes the copy off a short viewport.

export const MEDIA = {
  "add-paper": { ratio: 1.6, description: "The Add popover's four ways in: a link fetched, files uploaded, a blank page, a notebook" },
  "page-notebook": { ratio: 1.6, description: "One page, two views: notes with a sheet among them, and the same sheets in the viewer" },
  "labels-folders": { ratio: 1.55, description: "One paper carries labels and sits in several folders at once" },
  "notebook-pages": { ratio: 1.6, description: "Writing low on the last sheet adds the next one under it" },
  "notebook-paper": { ratio: 1.75, description: "The paper menu: pattern, size and background changing on one sheet" },
  "tasks-keep-going": { ratio: 1.8, description: "A job carries on on the server while the window that started it closes" },
};

export const mediaRatio = (id) => MEDIA[id]?.ratio || 1.6;
