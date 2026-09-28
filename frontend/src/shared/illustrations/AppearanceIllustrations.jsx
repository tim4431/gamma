import React from "react";

// Small, abstract reading scenes: the same shapes make palettes easy to compare.
// Each scene is painted from its theme's own tokens: it carries the theme's
// data-theme / data-scheme, and tokens.css resolves the colours inside any
// element that does (illustrations.css picks the roles: chrome, content,
// text, accent). System is Light with Dark over its right half.
function ThemeScene({ theme, scheme }) {
  return (
    <g className="themeScene" data-theme={theme} data-scheme={scheme}>
      <rect className="themeChrome" width="180" height="72" />
      <rect className="themeContent" x="19" y="14" width="142" height="64" rx="5" />
      <path className="themeInk" d="M48 14v64" opacity=".1" />
      <g className="themeInk" strokeWidth="3" strokeLinecap="round" opacity=".25">
        <path d="M28 25h10M28 34h7M28 43h10M61 49h38M61 56h49M61 63h32" />
      </g>
      <path className="themeInk" d="M61 29h35" strokeWidth="4" strokeLinecap="round" opacity=".8" />
      <path className="themeAccentLine" d="M61 38h23" strokeWidth="4" strokeLinecap="round" opacity=".55" />
      <circle className="themeAccent" cx="134" cy="37" r="12" opacity=".18" />
      <path className="themeAccent" d="M119 56l13-19 16 19z" opacity=".48" />
    </g>
  );
}

export function ThemePreview({ theme, scheme }) {
  const clipId = React.useId();
  if (theme !== "system") {
    return <svg className="setPicturePreview" viewBox="0 0 180 72" aria-hidden="true"><ThemeScene theme={theme} scheme={scheme} /></svg>;
  }
  return (
    <svg className="setPicturePreview" viewBox="0 0 180 72" aria-hidden="true">
      <defs><clipPath id={clipId}><path d="M90 0h90v72H90z" /></clipPath></defs>
      <ThemeScene theme="light" scheme="light" />
      <g clipPath={`url(#${clipId})`}><ThemeScene theme="dark" scheme="dark" /></g>
    </svg>
  );
}

export function PdfPreview({ dark }) {
  return (
    <svg className={`appearancePdfPreview${dark ? " isDark" : ""}`} viewBox="0 0 64 78" aria-hidden="true">
      <rect className="appearancePdfPaper" x="1" y="1" width="62" height="76" rx="4" />
      <g fill="currentColor">
        <rect x="11" y="13" width="29" height="3" rx="1.5" />
        <rect x="11" y="21" width="41" height="2" rx="1" opacity=".35" />
        <rect x="11" y="27" width="35" height="2" rx="1" opacity=".35" />
        <circle cx="25" cy="45" r="9" opacity=".12" />
        <path d="M22 54l11-17 12 17z" opacity=".4" />
        <rect x="11" y="62" width="41" height="2" rx="1" opacity=".35" />
        <rect x="11" y="68" width="27" height="2" rx="1" opacity=".35" />
      </g>
    </svg>
  );
}

