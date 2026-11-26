// The choices a citation is made with, as every picker of them shows them
// (docs/dev/citations.md): the account's added styles (citationApi.js's
// list, read through useAddedStyles), the props of a Style MenuSelect over
// them and the bundled ones (styleMenuProps: Settings → Citations, the
// share popover's Reference, the Export dialog's Formatted bibliography),
// and the Language row (Settings and the Export dialog), which a style
// that sets its own language turns off. Which style a citation is then
// made in is citeRules.js styleInUse.
import React from "react";
import { getLocale, t } from "../shared/i18n/i18n.js";
import { LanguagesIcon } from "../shared/ui/Icons";
import { MenuSelect } from "../shared/ui/Menus";
import { Row } from "../settings/SettingsKit";
import { isBundledStyle } from "./bundledStyles.js";
import { loadStyles, styleList, subscribeStyles } from "./citationApi.js";
import { styleKindLabel, styleSections } from "./citeRules.js";
import { citationLocales, styleLocale, styleMeta } from "./render.js";

// A bundled style as a trigger names it: the name and edition its title
// leads with, and the variant that tells two of a family apart (Chicago's
// notes from its author-date), where the full title would be cut off
// before saying it. Names, not interface text. The menus' rows keep the
// full titles.
const SHORT_NAMES = {
  apa: "APA 7th",
  "american-chemical-society": "ACS",
  "american-medical-association": "AMA 11th",
  "american-physics-society": "APS",
  "american-political-science-association": "APSA",
  "american-sociological-association": "ASA",
  "chicago-author-date": "Chicago 18th (author-date)",
  "chicago-notes-bibliography": "Chicago 18th (notes)",
  "chicago-shortened-notes-bibliography": "Chicago 18th (short notes)",
  "elsevier-harvard": "Elsevier Harvard",
  "harvard-cite-them-right": "Cite Them Right 12th",
  ieee: "IEEE",
  "mhra-notes": "MHRA 4th (notes)",
  "modern-language-association": "MLA 9th",
  nature: "Nature",
  "nlm-citation-sequence": "NLM/Vancouver",
};
// The longest title shown whole; a longer added style goes by its CSL
// title-short, when it has one.
const TITLE_SHOWN = 28;

// A style's short name (a trigger's, a pill's, the Quick Copy row's).
export function styleLabel(id) {
  if (SHORT_NAMES[id]) return SHORT_NAMES[id];
  const meta = styleMeta(id);
  return meta.title.length > TITLE_SHOWN && meta.short && meta.short.length < meta.title.length ? meta.short : meta.title;
}

// The account's added styles as citationApi holds them ({loaded, styles,
// limit, downloads, own, error, missing}), read while `active`: once for
// the tab, or with `fresh` again on each mount (Settings, which shows the
// list held meanwhile).
export function useAddedStyles({ active = true, fresh = false } = {}) {
  const list = React.useSyncExternalStore(subscribeStyles, styleList);
  React.useEffect(() => { if (active) loadStyles({ force: fresh }); }, [active, fresh]);
  return list;
}

const byTitle = (a, b) => (a.title || a.id).localeCompare(b.title || b.id, undefined, { sensitivity: "base" });

// A Style MenuSelect's props: every style a citation can be made in, the
// account's own (`added`, its rows) first under Your styles, each with its
// class after it, then the bundled ones in the sections by class every
// style menu has (citeRules.js styleSections); the first section is the
// menu's heading. Titles are names (`names`), not catalog text. The
// trigger shows the style's short name (styleLabel), its full title on
// hover.
export function styleMenuProps(added, value, onChange) {
  const own = (added || []).filter((row) => row?.id && !isBundledStyle(row.id)).sort(byTitle);
  const mine = own.map((row) => [row.id, row.title || row.id, null,
    row.kind ? <span key={row.id} className="ctxMenuHint" aria-hidden="true">{styleKindLabel(row.kind)}</span> : null]);
  const bundled = styleSections({ current: own.some((row) => row.id === value) ? "" : value });
  const [first, ...rest] = [...(mine.length ? [{ label: t("Your styles"), options: mine }] : []), ...bundled];
  const title = styleMeta(value).title;
  return { value, onChange, heading: first?.label, options: first?.options || [], names: true,
    display: <span title={title}>{styleLabel(value)}</span>,
    sections: rest.map((section) => ({ label: section.label, value, onChange, options: section.options })) };
}

// The languages a citation can be set in (every CSL locale, render.js
// citationLocales); null until they are in.
function useCitationLocales() {
  const [list, setList] = React.useState(null);
  React.useEffect(() => {
    let live = true;
    citationLocales().then((found) => { if (live) setList(found); }, () => {});
    return () => { live = false; };
  }, []);
  return list;
}

// The locale `style` sets for itself (its default-locale, or the one its
// dependent imposes), over the one chosen; "" when the chosen one applies.
function useStyleLocale(style) {
  const [found, setFound] = React.useState({ style: "", locale: "" });
  React.useEffect(() => {
    let live = true;
    styleLocale(style).then((locale) => { if (live) setFound({ style, locale: locale || "" }); },
      () => { if (live) setFound({ style, locale: "" }); });
    return () => { live = false; };
  }, [style]);
  return found.style === style ? found.locale : "";
}

// A locale's name in its own language, its tag until the list is in.
const localeName = (list, tag) => list?.find((locale) => locale.tag === tag)?.name || tag;

// The languages a reader most likely wants, ahead of the long list: the
// one chosen, English as the US and Britain write it, and the interface's
// language in each of its variants.
function suggestedLocales(locales, chosen) {
  const ui = getLocale();
  const wanted = [chosen, "en-US", "en-GB", ...locales.map(({ tag }) => tag).filter((tag) => tag === ui || tag.startsWith(`${ui}-`))];
  return [...new Set(wanted)].filter((tag) => locales.some((locale) => locale.tag === tag));
}

// The Language row: `value` the language chosen, `onChange` its setter,
// for citations in `style`; a style that sets its own shows that one and
// turns the menu off. Each language is named in itself, its English name
// after it when that differs; the likely ones (suggestedLocales) lead, the
// rest follow under All languages. Until the list is in, only the one shown.
export function CitationLanguageRow({ style, value, onChange, title }) {
  const locales = useCitationLocales();
  const own = useStyleLocale(style);
  const shown = own || value;
  const option = ({ tag, name, english }) => [tag, name, null,
    english && english !== name ? <span key={tag} className="ctxMenuHint" aria-hidden="true">{english}</span> : null, `${english} (${tag})`];
  const first = locales ? suggestedLocales(locales, shown) : [shown];
  const choices = locales ? {
    heading: t("Suggested"),
    options: first.map((tag) => option(locales.find((locale) => locale.tag === tag))),
    sections: [{ label: t("All languages"), value: shown, onChange,
      options: locales.filter(({ tag }) => !first.includes(tag)).map(option) }],
  } : { options: [[shown, shown]] };
  return (
    <Row icon={LanguagesIcon} label={t("Language")} title={title}
      hint={own ? t("Set by the style: {language}", { language: localeName(locales, own) }) : t("The language of the style's words and dates")}>
      <MenuSelect label={t("Citation language")} value={shown} {...choices} names display={localeName(locales, shown)}
        disabled={!!own} onChange={onChange} />
    </Row>
  );
}
