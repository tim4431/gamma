// The `src` an <img> in the chat shows a picture from: a chat picture
// (chat/chatPictures.js), a chip's picture URL or a plain URL, with the
// workspace and share parameters a browser-issued request needs (the fetch
// wrapper adds them to API calls; an <img> bypasses it). A data URL as it is.
import { withShare, withWorkspace } from "../shared/lib/utils";
import { pictureUrl } from "./chatPictures.js";

export function pictureSrc(picture) {
  const url = pictureUrl(picture);
  return !url || url.startsWith("data:") ? url : withShare(withWorkspace(url));
}
