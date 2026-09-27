// What a failed chat request says, per failure kind. The server names the
// kind (ai_client.failure_kind: not_configured, allowance, auth, rate,
// overloaded, unreachable, bad_endpoint, too_long, other) on the HTTP error
// and on the stream's error line; a connection probe adds "no_model" (an
// entry with no model picked); "network" is the browser losing Gamma
// itself. The chat's error card, the login check's warning strip and the
// Test result on a Settings connection row all read their headline here,
// so no two of them word one failure differently.
//
// `fix` names the card's main action (ChatDock turns it into a button):
// connect / own_key open Settings → Connections, key / signin / connection
// open the failing connection's own form, new_chat starts over. `switchModel`
// says another model could get around the failure.
import { t } from "../shared/i18n/i18n.js";

export const FAILURE_KINDS = ["not_configured", "allowance", "auth", "rate", "overloaded",
  "unreachable", "bad_endpoint", "too_long", "no_model", "network", "other"];

export function failureCopy(kind, { provider = "", auth = "key" } = {}) {
  const name = provider || t("The AI service");
  switch (kind) {
    case "not_configured":
      return { headline: t("No AI service connected"), text: t("Connect one in Settings to start chatting."), fix: "connect" };
    case "allowance":
      return {
        headline: t("The shared AI allowance is used up"),
        text: t("This server's shared AI allowance for your account is used up for now. Add your own key, or try again later."),
        fix: "own_key", switchModel: true,
      };
    case "auth":
      return auth === "oauth"
        ? { headline: t("The {provider} sign-in has expired", { provider: name }),
            text: t("Sign in again, or pick another model for this chat."), fix: "signin", switchModel: true }
        : { headline: t("{provider} rejected the API key", { provider: name }),
            text: t("The key saved as “{provider}” no longer works. Update it, or pick another model for this chat.", { provider: name }),
            fix: "key", switchModel: true };
    case "rate":
      return {
        headline: t("Rate limit or quota reached"),
        text: t("{provider} is refusing more requests for now. Wait a moment and retry, or switch to another model.", { provider: name }),
        switchModel: true,
      };
    case "overloaded":
      return {
        headline: t("{provider} is overloaded — try again in a moment", { provider: name }),
        text: t("The problem is on the provider's side, not in your settings."),
        switchModel: true,
      };
    case "unreachable":
      return {
        headline: t("Couldn't reach {provider}", { provider: name }),
        text: t("The Gamma server got no answer from the service. Check its address, or try again."),
        fix: "connection", switchModel: true,
      };
    case "bad_endpoint":
      return {
        headline: t("The endpoint didn't answer like an AI API — check its base URL"),
        text: t("The address saved for “{provider}” answered, but not in a format Gamma understands.", { provider: name }),
        fix: "connection", switchModel: true,
      };
    case "too_long":
      return {
        headline: t("This conversation is too long for the model"),
        text: t("Start a new chat, lower the context size in the chat settings, or switch to a model with a larger context window."),
        fix: "new_chat", switchModel: true,
      };
    case "no_model":
      return {
        headline: t("No model picked for {provider}", { provider: name }),
        text: t("The connection offers no model yet. Edit it and pick at least one."),
        fix: "connection", switchModel: true,
      };
    case "network":
      return { headline: t("Lost the connection to Gamma"), text: t("The browser lost its connection to the Gamma server. Check your network, then retry.") };
    default:
      return { headline: t("The AI request failed"), text: t("{provider} returned an error; the details are below.", { provider: name }), switchModel: true };
  }
}

// The button label of each `fix`.
export function fixLabel(fix) {
  return {
    connect: t("Connect AI"),
    own_key: t("Add your own key"),
    key: t("Update key"),
    signin: t("Sign in again"),
    connection: t("Edit connection"),
    new_chat: t("New chat"),
  }[fix] || "";
}

// An Error carrying the server's classification, thrown by the chat's send
// path from an HTTP error body or a stream's {"error"} line.
export function chatFailure(detail, info = {}) {
  const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  err.kind = FAILURE_KINDS.includes(info.kind) ? info.kind : "other";
  err.provider = info.provider_name || "";
  err.providerId = info.provider_id || "";
  err.auth = info.provider_auth || "key";
  return err;
}

// The fields a failed reply saves (ChatDock's message), so a reload shows
// the same card. Messages saved before these fields keep their old bubble.
// The upstream status is not one of them: the detail already opens with it
// ("upstream 529: …").
export function failureFields(err) {
  if (err?.name === "TypeError") return { errorKind: "network", errorDetail: err.message };
  return {
    errorKind: err?.kind || "other",
    errorDetail: err?.message || "",
    ...(err?.provider ? { errorProvider: err.provider } : {}),
    ...(err?.providerId ? { errorProviderId: err.providerId } : {}),
    ...(err?.auth === "oauth" ? { errorAuth: "oauth" } : {}),
  };
}
