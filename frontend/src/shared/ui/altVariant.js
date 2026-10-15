// A control whose Alt+click does something else shows that variant while Alt
// is held over it (under the pointer, or under the keyboard's focus), so the
// outcome is visible before the click lands. Only an engaged control listens:
// an Alt held elsewhere (the PDF's translation peek, a block's Alt+click)
// leaves every other button alone. While engaged, a bare Alt is kept from
// focusing the browser's or the desktop shell's menu bar.
//
//   const [alt, altProps] = useAltVariant();
//   <button {...altProps} onClick={(e) => act(e.altKey)}>
//     {alt ? <TrashIcon /> : <PlusIcon />}
//   </button>
import { useEffect, useState } from "react";

export function useAltVariant() {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [alt, setAlt] = useState(false);
  const engaged = hovered || focused;
  useEffect(() => {
    if (!engaged) return undefined;
    const key = (e) => {
      if (e.key !== "Alt") return;
      e.preventDefault();
      setAlt(e.type === "keydown");
    };
    const off = () => setAlt(false);
    window.addEventListener("keydown", key);
    window.addEventListener("keyup", key);
    window.addEventListener("blur", off);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("keyup", key);
      window.removeEventListener("blur", off);
      setAlt(false);
    };
  }, [engaged]);
  // A mouse brings its modifier state along (Alt pressed before the pointer
  // arrived, or released while the window was in the background); a touch
  // has no Alt and no hover to keep.
  const fromPointer = (e) => { if (e.pointerType === "mouse") setAlt(e.altKey); };
  const props = {
    onPointerEnter: (e) => { if (e.pointerType === "mouse") { setHovered(true); setAlt(e.altKey); } },
    onPointerMove: fromPointer,
    onPointerLeave: () => setHovered(false),
    // Keyboard focus only: a clicked button keeps the focus, and an Alt held
    // later over the PDF must not restyle it.
    onFocus: (e) => setFocused(e.currentTarget.matches(":focus-visible")),
    onBlur: () => setFocused(false),
  };
  return [engaged && alt, props];
}
