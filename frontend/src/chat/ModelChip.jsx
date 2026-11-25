// The composer's model chip (docs/dev/ai.md "The model chip"): how the
// chat's agent answers, Gamma AI's and a computer agent's alike. It reads
// the model and its effort ("GPT-5 · high"), led by the logo of the model's
// provider where one is drawn (ai/ProviderLogo.jsx), the speed chosen its
// glyph after them. A press opens one panel above it, in three bands: the
// models (Gamma AI's are the model menu's search box over every
// connection's models, the chat models first and each model once, a star
// making one a chat model: ai/ModelMenu.jsx ModelPicker; a computer
// agent's its own list),
// then each knob the model takes as a segmented control (the reasoning
// effort, the speed: `knobs`), then Manage models… for Gamma AI. A model
// picked closes the panel; a knob changed leaves it open, so effort and
// speed are set in one visit. Who answers is the header's agent picker
// (chat/AgentPicker.jsx).
import { useId } from "react";
import { ContextMenu, MenuItem, MenuSelect } from "../shared/ui/Menus";
import { CheckIcon, SettingsIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { ModelPicker } from "../ai/ModelMenu.jsx";
import { ProviderLogo } from "../ai/ProviderLogo.jsx";
import { Segmented } from "../settings/SettingsKit";
import { SPEED_LABELS } from "./modelPrefs.js";
import { SPEED_ICONS } from "./ChatTranscript.jsx";

// The reasoning effort's knob: Default (`fallback` its words, "Default" or a
// computer agent's own "Default (high)"), then the levels the model takes.
export const effortKnob = (value, onChange, levels, fallback = t("Default")) => ({
  id: "effort", label: t("Reasoning effort"), value, onChange, options: [["", fallback], ...levels.map((ef) => [ef, ef])],
});

// The speed's: Default, then the tiers the model's provider sells, each with
// the glyph the chip shows for it.
export const speedKnob = (value, onChange, tiers, fallback = t("Default")) => ({
  id: "speed", label: t("Speed"), value, onChange,
  options: [["", fallback], ...tiers.map((tier) => [tier, SPEED_LABELS[tier] || tier, SPEED_ICONS[tier]])],
});

// `label` the chip's name (knobsLabel, or a computer agent's), `text` its
// words, `logo` the service of the model's connection (Gamma AI's), `glyph`
// what follows the words (the speed's); `picker` ModelPicker's props for
// Gamma AI ({models, favorites, missing, value, onPick, onToggleFavorite}),
// else `models` [[id, label]] a computer agent's, `model` the one chosen,
// `onModel(id)`; `knobs` [{id, label, value, onChange, options}] (Segmented's
// options), `onManage` Settings → AI → Models (null for none).
export default function ModelChip({ guide, label, text = "", logo = "", glyph = null, picker = null, models = [], model = "",
  onModel, knobs = [], onManage = null }) {
  const id = useId();
  const panel = (at) => (
    <ContextMenu {...at} className="modelPanel" role="dialog" aria-label={label}>
      {picker ? <ModelPicker {...picker} label={t("Models")} onClose={at.onClose} once /> : (
        <div className="modelPanelList" role="group" aria-label={t("Model")}>
          {models.map(([value, name]) => (
            <MenuItem key={value} aria-pressed={value === model}
              trailing={value === model ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
              onClick={() => { at.onClose(); if (value !== model) onModel(value); }}>{name}</MenuItem>
          ))}
        </div>
      )}
      {knobs.length ? (
        <div className="modelPanelKnobs">
          {knobs.map((knob) => (
            <div key={knob.id} className="modelPanelKnob" role="group" aria-labelledby={`${id}-${knob.id}`}>
              <span className="popoverSection" id={`${id}-${knob.id}`}>{knob.label}</span>
              <Segmented value={knob.value} onChange={knob.onChange} options={knob.options} />
            </div>
          ))}
        </div>
      ) : null}
      {onManage ? (
        <div className="modelPanelFoot">
          <MenuItem icon={SettingsIcon} onClick={() => { at.onClose(); onManage(); }}>{t("Manage models…")}</MenuItem>
        </div>
      ) : null}
    </ContextMenu>
  );
  return (
    <span className="chatModelChip" data-guide={guide}>
      <MenuSelect up label={label} options={[]} popup="dialog" listbox={panel} display={<>
        {logo && text ? <ProviderLogo service={logo} size={14} fallback={false} /> : null}
        <span className="chatModelText">{text || t("Model")}</span>
        {glyph}
      </>} />
    </span>
  );
}
