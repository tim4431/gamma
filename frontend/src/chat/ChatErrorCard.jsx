// The card a failed reply ends in, on both sides of the chat: a Gamma AI
// reply (ChatDock, worded per failure kind by chat/chatErrors.js) and a
// turn of the agent on this computer (chat/computer/ComputerChat.jsx). A
// headline saying what went wrong, one plain sentence (`text`), the buttons
// that fix it or try again (`actions`), and the raw words of whoever failed
// folded under `detailLabel` ("Details"). `compact`: a reply that broke off
// partway keeps its text and gets the card without its sentence.
import { AlertCircleIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export default function ChatErrorCard({ headline, text = "", actions = [], detail = "", detailLabel = "", compact = false }) {
  return (
    <div className={`chatErrorCard${compact ? " compact" : ""}`}>
      <div className="chatErrorHead"><AlertCircleIcon size={16} /><span>{headline}</span></div>
      {!compact && text ? <div className="chatErrorText">{text}</div> : null}
      {actions.length ? <div className="chatErrorActions">{actions}</div> : null}
      {detail ? (
        <details className="chatErrorDetails">
          <summary>{detailLabel || t("Details")}</summary>
          <pre>{detail}</pre>
        </details>
      ) : null}
    </div>
  );
}
