// The account's colour palette for every control that offers swatches: the
// selection popup's highlight colours, a highlight's recolour menu, the tool
// strip's pen and highlighter colours, the handwriting selection menu and a
// text box's colours. App provides the `palette` preference
// (shared/model/palette.js normalizePalette's {wash, ink}); without a
// provider — tests, a one-off render — the defaults apply.
import { createContext, useContext } from "react";
import { DEFAULT_PALETTE } from "../model/palette.js";

export const PaletteContext = createContext(DEFAULT_PALETTE);
export const usePalette = () => useContext(PaletteContext) || DEFAULT_PALETTE;
