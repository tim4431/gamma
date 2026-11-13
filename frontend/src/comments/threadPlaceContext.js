// What became of a thread's place once its PDF was rebuilt, for both
// comment surfaces (Thread.jsx): App's placeOf(thread) for the open page
// (comments.js threadPlace over its tree and the file the page shows), ""
// for nothing to say. Without a provider no thread says anything. A module
// of its own so that App's provider imports no surface: Thread.jsx and the
// composer stay in the lazy chunks (docs/dev/frontend-refactor.md "Lazy
// boundaries").
import { createContext } from "react";

export const ThreadPlaceContext = createContext(null);
