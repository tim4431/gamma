import assert from 'node:assert/strict';
import test from 'node:test';
import { geometry } from './ink-entry.mjs';
const ink = globalThis.GammaInk;
const sample = brush => ink.encodeStroke({ id:'stable', brush, t0:1000, ch:'xyptaz', samples:[
  {x:10,y:20,p:.2,t:0,a:45,z:90}, {x:40,y:20,p:.8,t:16,a:50,z:100}, {x:70,y:40,p:.5,t:40,a:90,z:180}] });
test('CoreGraphics commands match the browser outline, including midpoint rounding', () => {
  for (const brush of [undefined,'monoline']) {
    const stroke = sample(brush), shape = geometry(stroke), f = n => n.toFixed(2);
    let path = `M${shape.points[0].map(f).join(',')} Q`;
    shape.points.forEach((p,i) => { path += `${p.map(f).join(',')} ${shape.midpoints[i].map(f).join(',')} `; });
    assert.equal(path + 'Z', ink.strokePath(stroke).d);
  }
});
test('native codec uses Gamma samples and stable transform identity', () => {
  let document = ink.newNotebookInk('sheet1',612,792);
  document = ink.appendStroke(document,sample());
  const moved = ink.translateStrokes(document,['stable'],12,8);
  assert.equal(moved.strokes[0].id,'stable');
  assert.deepEqual(moved.strokes[0].pts.slice(2),document.strokes[0].pts.slice(2));
  assert.equal(moved.strokes[0].t0,1000);
  assert.equal(document.version,2);
});
test('native partial erasure retains source identity and original time origin', () => {
  const document = ink.appendStroke(ink.newInk(1,612,792),sample());
  const erased = ink.eraseAt(document,10,20,1).ink;
  assert.equal(erased.version,2);
  assert.equal(erased.strokes[0].source_id,'stable');
  assert.equal(erased.strokes[0].t0,1000);
  assert.equal(ink.decodeStroke(erased.strokes[0])[0].t,16);
});
test('native replay calls the shared projection and never changes canonical ink', () => {
  const original = ink.appendStroke(ink.newInk(1,612,792), sample());
  const document = ink.eraseAt(original,10,20,1).ink;
  const bytes = JSON.stringify(document);
  ink.setReplay({segmentId:'audio1',segmentIds:['audio1','audio2'],ms:10,
    events:[{kind:'stroke',segment_id:'audio1',block_id:'group',stroke_id:'stable',start_ms:0,end_ms:40}]});
  assert.equal(ink.projectReplay(document,'group').strokes.length,0);
  ink.setReplay({segmentId:'audio1',segmentIds:['audio1','audio2'],ms:20,
    events:[{kind:'stroke',segment_id:'audio1',block_id:'group',stroke_id:'stable',start_ms:0,end_ms:40}]});
  assert.equal(ink.decodeStroke(ink.projectReplay(document,'group').strokes[0]).length,1);
  assert.equal(JSON.stringify(document),bytes);
  ink.setReplay(null);
  assert.equal(ink.projectReplay(document,'group'),document);
});
