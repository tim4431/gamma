// Pointer samples stay in page units; predictions are a disposable preview.
// A pen's sample carries its tilt as integer degrees, `a` (altitude) and
// `z` (azimuth), when the pen reports one (penTilt); the lift keeps the
// last contact's tilt, as it keeps its pressure.
export function inkSample(d, event, ending = false) {
  const previous = d.samples.at(-1);
  const tilt = !d.pen ? null : ending ? (previous?.a != null ? { a: previous.a, z: previous.z } : null) : penTilt(event);
  return {
    ...d.toPt(event),
    // Pointer-up pressure is always zero. Keep the last contact pressure
    // instead of making a bulb or a sudden thin spot at the end of a letter.
    p: d.pen && d.pressure
      ? (ending ? previous?.p ?? 0.5 : event.pressure > 0 ? event.pressure : 0.5)
      : 0.5,
    t: Math.max(previous?.t ?? 0, event.timeStamp - d.startTime),
    ...tilt,
  };
}

// A pen's tilt as integer degrees, {a: altitude 0..89, z: azimuth 0..359},
// from `altitudeAngle` / `azimuthAngle` (radians), else from `tiltX` /
// `tiltY` (degrees, converted as the Pointer Events spec does); null when
// it reports none — no tilt at all, or held upright (an altitude that
// rounds to 90°, where the azimuth means nothing) — so an untilted stroke
// is written without the a / z letters whichever browser drew it.
export function penTilt(event) {
  let altitude, azimuth;
  if (Number.isFinite(event?.altitudeAngle) && Number.isFinite(event?.azimuthAngle)) {
    altitude = event.altitudeAngle;
    azimuth = event.azimuthAngle;
  } else if (Number.isFinite(event?.tiltX) && Number.isFinite(event?.tiltY) && (event.tiltX || event.tiltY)) {
    ({ altitude, azimuth } = fromTilt(event.tiltX, event.tiltY));
  } else return null;
  const a = Math.round(altitude * 180 / Math.PI);
  if (!(a < 90)) return null;
  return { a: Math.max(0, a), z: ((Math.round(azimuth * 180 / Math.PI) % 360) + 360) % 360 };
}
// A pen's barrel roll as integer degrees 0..359, clockwise as seen
// (PointerEvent.twist, which a pen without the sensor reports as 0; the
// iPad host's roll arrives the same way, hostInk.js hostEvent), or null for
// a mouse, a finger, or an event without one. Read at a calligraphy
// stroke's first sample: it turns the stroke's nib (ink.js strokeNib).
export function penTwist(event) {
  if (event?.pointerType && event.pointerType !== "pen") return null;
  return Number.isFinite(event?.twist) ? ((Math.round(event.twist) % 360) + 360) % 360 : null;
}
// tiltX / tiltY (degrees, −90..90) → altitude and azimuth (radians).
function fromTilt(tiltX, tiltY) {
  const tx = tiltX * Math.PI / 180, ty = tiltY * Math.PI / 180;
  const edge = Math.abs(tiltX) === 90 || Math.abs(tiltY) === 90;
  let azimuth = 0;
  if (tiltX === 0) azimuth = tiltY > 0 ? Math.PI / 2 : tiltY < 0 ? 3 * Math.PI / 2 : 0;
  else if (tiltY === 0) azimuth = tiltX < 0 ? Math.PI : 0;
  else if (!edge) {
    azimuth = Math.atan2(Math.tan(ty), Math.tan(tx));
    if (azimuth < 0) azimuth += 2 * Math.PI;
  }
  const altitude = edge ? 0 : tiltX === 0 ? Math.PI / 2 - Math.abs(ty) : tiltY === 0 ? Math.PI / 2 - Math.abs(tx)
    : Math.atan(1 / Math.sqrt(Math.tan(tx) ** 2 + Math.tan(ty) ** 2));
  return { altitude, azimuth };
}

export function appendInkSample(d, event, ending = false) {
  const next = inkSample(d, event, ending);
  const previous = d.samples.at(-1);
  // Repeated endpoints should not turn a tap into a tiny artificial line.
  if (previous && next.x === previous.x && next.y === previous.y && next.p === previous.p) return;
  d.samples.push(next);
}

export function predictedInkSamples(d, event) {
  if (!d.pen || d.use.tool !== "pen") return [];
  const last = d.samples.at(-1);
  if (!last) return [];
  const out = [];
  for (const prediction of event.getPredictedEvents?.() || []) {
    const ahead = prediction.timeStamp - event.timeStamp;
    if (ahead <= 0) continue;
    if (ahead > 16) break;
    const point = inkSample(d, prediction);
    // Limit overshoot in screen pixels at every zoom level.
    if (Math.hypot(point.x - last.x, point.y - last.y) * d.k > 12) break;
    out.push({ ...point, p: last.p });
  }
  return out;
}
