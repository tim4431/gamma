// A choice of one among a few ordered steps, as a slider that snaps from
// stop to stop (the chat's reasoning effort, chat/ModelChip.jsx): a thick
// rounded track, filled up to a round thumb that glides to the stop picked,
// a dot on each stop still ahead. With nothing picked the track is bare: no
// fill, no thumb, every stop a dot. What is drawn is paint; the control is a
// native range over it, so a press, a drag, a finger and the arrow keys all
// work, and it says the stop's name (aria-valuetext). Its native thumb is as
// wide as the one drawn, so a press lands on the dot under it
// (stopSlider.css).
import "./stopSlider.css";

// `stops` [[value, name]] in order, `value` the one picked (one not among
// them is none), `onChange(value)`; `label` the control's name, `unset`
// what it says with none picked.
export default function StopSlider({ label, value, onChange, stops, unset = "" }) {
  const at = stops.findIndex(([v]) => v === value);
  const last = stops.length - 1;
  const pick = (e) => onChange(stops[Number(e.currentTarget.value)][0]);
  return (
    <div className="stopSlider" style={{ "--at": at, "--last": Math.max(1, last) }}>
      <span className="stopSliderTrack" />
      {at < 0 ? null : <span className="stopSliderFill" />}
      {stops.map(([v], i) => (i > at ? <span key={v} className="stopSliderDot" style={{ "--i": i }} /> : null))}
      {/* With none picked the range stands on the first stop, where a press changes nothing: the click picks it. */}
      <input type="range" min={0} max={last} step={1} value={Math.max(0, at)} aria-label={label}
        aria-valuetext={at < 0 ? unset : stops[at][1]} onChange={pick} onClick={at < 0 ? pick : undefined} />
      {at < 0 ? null : <span className="stopSliderThumb" />}
    </div>
  );
}
