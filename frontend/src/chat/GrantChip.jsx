// The composer's "Allowed in this chat" chip, on both sides of the chat:
// what the person allowed for the rest of the conversation (Gamma AI's
// permissions, chat/approvals.js; the commands the agent on this computer
// runs without asking, chat/computer/useComputerChat.js), named in its
// title, a press taking it all back so each asks again. A shield alone,
// to keep the toolbar's room for the model and Send; under the pointer or
// the focus it turns to the × a press means.
import { ShieldIcon, XIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// `list` the grants in words ("Edit notes, Rename pages"), `title` what the
// chip says of them, `onRevoke` takes them back.
export default function GrantChip({ list, title, onRevoke, disabled = false }) {
  return (
    <button type="button" className="chatAttachToggle chatGrantChip chatGrantShield" onClick={onRevoke} disabled={disabled}
      title={title} aria-label={t("Allowed in this chat: {list}. Ask again", { list })}>
      <ShieldIcon size={14} /><XIcon size={14} />
    </button>
  );
}
