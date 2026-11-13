// How a link by path in a note reaches the file it names (PathLink.jsx).
// App provides it only while the open page is a work folder's:
//   { resolve(href) → Promise<page row | null>, open(pageId), say(text) }
// resolve asks GET /folders/{W}/at with the note's directory as the base
// (docs/dev/work_folders.md "Paths"). Without a provider a relative link is
// the plain link it always was. A module of its own so that App's provider
// imports no editor code.
import { createContext } from "react";

export const PathLinkContext = createContext(null);
