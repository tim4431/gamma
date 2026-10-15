// What the notes need from App about recordings (docs/dev/recording.md):
// how a recording block's card starts its playback and its transcription.
// App provides it around the notes; a row reads it with useRecordings.
//   {play(blockId), transcribe(blockId) | null, covers(t0), playAt(wall)}
import { createContext, useContext } from "react";

export const RecordingContext = createContext(null);
export const useRecordings = () => useContext(RecordingContext);
