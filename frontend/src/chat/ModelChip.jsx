// The composer's model chip (docs/dev/ai.md "The model chip"): how the
// chat's agent answers, Gamma AI's and a computer agent's alike. It reads
// the model and its effort ("GPT-5 High"), led by the logo of the model's
// provider where one is drawn (ai/ProviderLogo.jsx), and by a bolt while it
// runs fast. A press opens one panel above it: the Fast button, the effort
// over the model's name, the way back to the model's own settings, and
// under them the effort's slider (shared/ui/StopSlider.jsx). The middle
// opens the models it switches between (Gamma AI's the one in use and the
// chat models, with All models…, the menu of every connection's
// (ai/ModelMenu.jsx), and Manage models…; a computer agent's its own); one
// picked comes back to the panel, where its effort can be set. A model with
// neither effort nor Fast has the list alone. Who answers is the header's
// agent picker (chat/AgentPicker.jsx).
import { useId, useState } from "react";
import { ContextMenu, MenuItem, MenuLabel, MenuSelect } from "../shared/ui/Menus";
import { MorphText } from "../shared/ui/MotionWidgets";
import StopSlider from "../shared/ui/StopSlider.jsx";
import { CheckIcon, ChevronRightIcon, SearchIcon, SettingsIcon, UndoIcon, ZapIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { ModelMenuList } from "../ai/ModelMenu.jsx";
import { ProviderLogo } from "../ai/ProviderLogo.jsx";
import { SpeedGlyph } from "./ChatTranscript.jsx";
import { effortLabel } from "./modelPrefs.js";

// The level the model answers at: the one picked, else its own where known.
const levelOf = (effort) => (effort ? effort.value || effort.own || "" : "");

function Knobs({ name, effort, fast, onSpeed, onReset, onModels }) {
  const level = levelOf(effort);
  return (
    <div className="modelPanelKnobs">
      <div className="modelPanelHead">
        {fast ? (
          <button type="button" className={`ctlBtn${fast.on ? " modeActive" : ""}`} aria-pressed={fast.on} aria-label={t("Fast")}
            title={t("Fast: quicker replies at a higher price")} onClick={() => onSpeed(fast.next)}>
            <ZapIcon size={16} fill={fast.on ? "currentColor" : "none"} />
          </button>
        ) : <span />}
        <button type="button" className="uiBtn ghost modelPanelPick" title={t("Select model")} onClick={onModels}>
          {effort ? <MorphText className={`modelPanelEffort${level ? "" : " unset"}`}>{level ? effortLabel(level) : t("Reasoning effort")}</MorphText> : null}
          <span className="modelPanelModel"><span className="modelMenuName">{name}</span><ChevronRightIcon size={14} /></span>
        </button>
        <button type="button" className="ctlBtn" disabled={!onReset} aria-label={t("Reset to default")} title={t("Reset to default")} onClick={onReset}>
          <UndoIcon size={16} />
        </button>
      </div>
      {effort ? (
        <StopSlider label={t("Reasoning effort")} value={level} unset={t("Default")} onChange={effort.onChange}
          stops={effort.levels.map((l) => [l, effortLabel(l)])} />
      ) : null}
    </div>
  );
}

function Models({ models, model, onPick, onAll, onManage }) {
  const id = useId();
  return <>
    <div className="modelPanelList" role="group" aria-labelledby={id}>
      <MenuLabel id={id}>{t("Select model")}</MenuLabel>
      {models.map((m) => (
        <MenuItem key={m.id} aria-pressed={m.id === model}
          trailing={m.id === model ? <CheckIcon size={14} className="ctxMenuCheck" /> : null} onClick={() => onPick(m.id)}>
          {m.service != null ? <ProviderLogo service={m.service} size={14} /> : null}
          <span className="modelMenuName">{m.name}</span>
          {m.where ? <span className="modelMenuWhere">{m.where}</span> : null}
        </MenuItem>
      ))}
    </div>
    {onAll || onManage ? (
      <div className="modelPanelFoot">
        {onAll ? <MenuItem icon={SearchIcon} title={t("Search every connection's models")} onClick={onAll}>{t("All models…")}</MenuItem> : null}
        {onManage ? <MenuItem icon={SettingsIcon} onClick={onManage}>{t("Manage models…")}</MenuItem> : null}
      </div>
    ) : null}
  </>;
}

// The open panel, `at` MenuSelect's placement and dismissal; the rest is
// ModelChip's.
function ModelPanel({ at, label, name, models, model, onModel, effort, fast, onSpeed, onReset, all, onManage }) {
  const [view, setView] = useState("knobs"); // "knobs", "models" or "all"
  if (view === "all") return <ModelMenuList {...at} {...all} label={t("All models")} />;
  const knobs = !!(effort || fast);
  const pick = (id) => {
    if (id !== model) onModel(id);
    if (knobs) setView("knobs");
    else at.onClose();
  };
  return (
    <ContextMenu {...at} className="modelPanel" role="dialog" aria-label={label}>
      {knobs && view === "knobs" ? <Knobs {...{ name, effort, fast, onSpeed, onReset }} onModels={() => setView("models")} /> : (
        <Models models={models} model={model} onPick={pick} onAll={all ? () => setView("all") : null}
          onManage={onManage ? () => { at.onClose(); onManage(); } : null} />
      )}
    </ContextMenu>
  );
}

// `label` the chip's name, `name` the model's, `logo` the service of its
// connection (Gamma AI's). The panel's: `models` [{id, name, where?,
// service?}], `model` the one in use, `onModel(id)`; `effort` {value,
// levels, own? (the model's own level where it is known), onChange}, null
// for a model without levels; `fast` modelPrefs.js fastChoice's, null where
// the model has no such speed, and `onSpeed(speed)`; `onReset` back to the
// model's own settings, null while nothing is picked; `all` the model
// menu's props (Gamma AI's), `onManage` Settings → AI → Models, null for
// none.
export default function ModelChip({ guide, logo = "", ...panel }) {
  const { label, name, effort, fast } = panel;
  const level = levelOf(effort);
  return (
    <span className="chatModelChip" data-guide={guide}>
      <MenuSelect up label={label} options={[]} popup="dialog" listbox={(at) => <ModelPanel at={at} {...panel} />}
        display={<>
          {fast?.on ? <SpeedGlyph speed="fast" /> : null}
          {logo && name ? <ProviderLogo service={logo} size={14} fallback={false} /> : null}
          <span className="chatModelText">{name || t("Model")}</span>
          {level ? <span className="chatModelEffort">{effortLabel(level)}</span> : null}
        </>} />
    </span>
  );
}
