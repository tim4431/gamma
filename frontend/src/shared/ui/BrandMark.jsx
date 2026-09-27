// The Gamma mark: the favicon's artwork, generated from design/brand/marks/
// by tools/branding and served under /media/ like the tab icon, so it is
// usually cached before any page shows it. Decorative: the name next to it
// (or the link around it) carries the meaning.
export const BRAND_MARK_URL = "/media/icons/favicon.svg";

export function BrandMark({ size = 24, className = "" }) {
  return <img className={className} src={BRAND_MARK_URL} alt="" width={size} height={size} />;
}
