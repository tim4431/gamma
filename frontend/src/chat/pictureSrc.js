// The `src` an <img> in the chat shows a picture from: a chat picture
// (chat/chatPictures.js), a chip's picture URL or a plain URL, with the
// workspace and share parameters a browser-issued request needs (`assetUrl`,
// the same step the chat markdown's images take). A data URL as it is.
import { assetUrl } from "../shared/lib/utils";
import { pictureUrl } from "./chatPictures.js";

export function pictureSrc(picture) {
  return assetUrl(pictureUrl(picture));
}
