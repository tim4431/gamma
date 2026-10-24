import { t } from "../i18n/i18n.js";

// The Gamma mark: the favicon's artwork, generated from design/brand/marks/
// by tools/branding and served under /media/ like the tab icon, so it is
// usually cached before any page shows it. Decorative: the name next to it
// (or the link around it) carries the meaning.
export const BRAND_MARK_URL = "/media/icons/favicon.svg";

export function BrandMark({ size = 24, className = "" }) {
  return <img className={className} src={BRAND_MARK_URL} alt="" width={size} height={size} />;
}

// The spinner index.html's splash set turning, read while that splash still
// stands (this module loads before the app's first render replaces it).
const SPIN_START = (() => {
  try { return document.querySelector("#splash .spin")?.getAnimations()[0]?.startTime ?? null; } catch { return null; }
})();
// Each splash after it turns on from where it was, so no hand-over restarts it.
const keepTurning = (el) => {
  const spin = el?.getAnimations?.()[0];
  if (spin && SPIN_START != null) spin.startTime = SPIN_START;
};

// What shows until the app's first real screen: the splash index.html paints
// before the bundle (its #splash styles are there, inline), the same mark,
// name and spinner, so the hand-over from it, from the server's config to
// the session and from the session to the library (or the login, or the
// assistant's consent page) never jumps. The mark is drawn in place, as
// index.html draws it (design/brand/marks/favicon.svg at 56 px), so it is
// on the first frame; the favicon as a picture would blink out while it
// loads.
export function Splash() {
  return (
    <div id="splash" role="status" aria-label={t("Loading Gamma…")}>
      <svg className="mark" width="56" height="56" viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <rect width="32" height="32" rx="7" fill="#1e1e1c" />
        <path d="M 6 16 C 9 10.5 13 10.5 16 16 C 19 21.5 23 21.5 26 16" stroke="#e8a020" strokeWidth="1.2" fill="none" opacity="0.6" strokeLinecap="round" />
        <path d="M 6 16 C 9 21.5 13 21.5 16 16 C 19 10.5 23 10.5 26 16" stroke="#e8a020" strokeWidth="1.2" fill="none" opacity="0.6" strokeLinecap="round" />
        <path d="M 6 4  Q 2 16 6 28" stroke="#e8a020" strokeWidth="1.8" fill="none" strokeLinecap="round" opacity="0.9" />
        <path d="M 26 4 Q 30 16 26 28" stroke="#e8a020" strokeWidth="1.8" fill="none" strokeLinecap="round" opacity="0.9" />
        <rect x="9" y="8" width="13" height="3" rx="0.8" fill="#eeebe4" />
        <rect x="9" y="8" width="3" height="15" rx="0.8" fill="#eeebe4" />
      </svg>
      <div className="name">{t("Gamma")}</div>
      <div className="spin" ref={keepTurning} />
    </div>
  );
}
