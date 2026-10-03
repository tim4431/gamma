// The illustration registry: the pictures a guide card may show above its
// copy, by id — the same contract as the anchor registry, so a tour names an
// illustration and never a file path (docs/dev/onboarding.md, "Illustrations").
//
// A drawing is for a concept that has no place on screen; a gesture on the
// real UI is the guide's to show, not a picture's. A step's `media` is an id
// here; the drawing is `guide/media/<id>.svg`, inlined into the card by
// GuideOverlay so it reads the theme's tokens — eight themes, so a picture
// that carried its own colours would be wrong in most of them. A drawing is
// shapes and nothing more: its look and its motion are the shared classes of
// guide/media.css (no <style>, no colour, no words of its own), so every
// drawing plays the same rhythm and rests on its finished picture, which is
// also all that reduced motion shows.
//
// ratio: the drawing's width over its height (its viewBox). The card reserves
// the box from this before the drawing is laid out, so a step with a picture
// never moves its own card after placement (`place.js` measures the card
// once). Keep it between 1.4 and 2.2: the card is 320 px wide, so a taller
// picture pushes the copy off a short viewport.

export const MEDIA = {
  "labels-folders": { ratio: 1.55, description: "One paper carries labels and sits in several folders at once, never copied" },
  "page-notebook": { ratio: 1.6, description: "One page, two views: a sheet among the notes, and the same sheets filling the viewer" },
};

export const mediaRatio = (id) => MEDIA[id]?.ratio || 1.6;
