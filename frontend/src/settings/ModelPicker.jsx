import React, { useEffect, useId, useRef, useState } from "react";
import { ContextMenu } from "../shared/ui/Menus";
import { t } from "../shared/i18n/i18n.js";

// Native datalist popups can grow beyond the screen, especially over RDP.
// Keep focus in the input while the portalled, bounded list handles scrolling.
export function ModelPicker({ models, value, onChange, onAdd, loading }) {
  const id = useId();
  const input = useRef(null);
  const list = useRef(null);
  const [menu, setMenu] = useState(null);
  const [active, setActive] = useState(-1);
  const matches = models.filter((model) => model.toLowerCase().includes(value.trim().toLowerCase()));
  const open = () => {
    const rect = input.current.getBoundingClientRect();
    setMenu({ x: rect.left, y: rect.bottom + 4 });
  };
  const add = (model) => {
    if (!model?.trim()) return;
    onAdd(model.trim());
    onChange("");
    setActive(-1);
    setMenu(null);
  };
  useEffect(() => { setActive(-1); }, [value, models.join("\n")]);
  useEffect(() => {
    list.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [active]);
  useEffect(() => {
    if (!menu) return;
    const close = (event) => {
      if (!list.current?.contains(event.target)) setMenu(null);
    };
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menu]);
  return <>
    <input ref={input} className="aiKeyInput" type="text" spellCheck={false}
      role="combobox" aria-label={t("Add a model")} aria-autocomplete="list"
      aria-expanded={!!menu} aria-controls={menu ? id : undefined}
      aria-activedescendant={menu && active >= 0 ? `${id}-${active}` : undefined}
      autoComplete="off"
      placeholder={loading ? t("Loading models…") : t("Add a model — type or pick{available}", { available: models.length ? ` (${models.length} available)` : "" })}
      value={value} onFocus={open} onClick={open}
      onBlur={() => setMenu(null)}
      onChange={(event) => { onChange(event.target.value); setActive(-1); open(); }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape" && menu) {
          event.preventDefault(); event.stopPropagation(); setMenu(null);
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (!menu) open();
          setActive((current) => matches.length
            ? (current + (event.key === "ArrowDown" ? 1 : current < 0 ? 0 : -1) + matches.length) % matches.length : -1);
        } else if (event.key === "Enter") {
          event.preventDefault();
          add(menu && active >= 0 ? matches[active] : value);
        }
      }} />
    {menu ? <ContextMenu {...menu} onClose={() => setMenu(null)} ignoreRef={input} className="aiModelMenu">
      <div ref={list} id={id} role="listbox" aria-label={t("Available models")} className="aiModelOptions">
        {matches.map((model, index) => <button type="button" role="option" tabIndex={-1}
          id={`${id}-${index}`} aria-selected={index === active} key={model}
          className="ctxMenuItem" onMouseDown={(event) => event.preventDefault()}
          onClick={() => add(model)}>{model}</button>)}
        {!matches.length ? <div className="ctxMenuLabel">{loading ? t("Loading models…") : value.trim() ? t("Press Enter to add this model") : t("No models available")}</div> : null}
      </div>
    </ContextMenu> : null}
  </>;
}
