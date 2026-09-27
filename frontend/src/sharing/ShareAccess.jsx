// The share view's access pill (the read-only topbar, app/App.jsx): what this
// visitor may do — view or edit — and whose page it is, with the sentence
// behind it as the hover title. On phones only the icon and the role show.
import React from "react";
import { PeerAvatar } from "../collaboration/Presence";
import { EyeIcon, PencilIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// A stable colour per owner name, from the presence palette (--peer-0…7).
function ownerColor(name) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return hash % 8;
}

// `info`: the resolved share ({owner, canEdit}); `folder`: a folder share's
// listing is showing rather than one of its pages.
export function ShareAccessPill({ info, folder = false }) {
  const owner = info.owner || "";
  const who = owner || t("the owner");
  const explain = info.canEdit
    ? (folder
      ? t("Your changes save to {owner}'s pages for everyone.", { owner: who })
      : t("Your changes save to {owner}'s page for everyone.", { owner: who }))
    : (folder
      ? t("You can read the pages in this folder. Only {owner} can change them.", { owner: who })
      : t("You can read this page. Only {owner} can change it.", { owner: who }));
  const Icon = info.canEdit ? PencilIcon : EyeIcon;
  return (
    <span className={`shareAccess${info.canEdit ? " edit" : ""}`} title={explain}>
      <Icon size={14} aria-hidden="true" />
      <b>{info.canEdit ? t("Can edit") : t("View only")}</b>
      {owner ? (
        <span className="shareAccessOwner">
          {t(" · shared by {owner}", {
            owner: <><PeerAvatar peer={{ name: owner, color: ownerColor(owner), anchor: 0 }} title={owner} />{owner}</>,
          })}
        </span>
      ) : null}
      <span className="srOnly">{explain}</span>
    </span>
  );
}
