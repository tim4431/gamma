// A highlighter over a picture (docs/dev/handwriting.md, the planes): a
// picture (markup/PictureLayer.jsx) is never inverted with a dark page, so
// a highlighter plane that screens there, or is drawn as it is on dark
// paper, would all but vanish over one. On a surface with pictures
// InkLayer draws each highlighter plane through a mask of the surface less
// its pictures, and its strokes again through the pictures alone, always
// multiplied. The mask is the pictures as the page draws them: this is
// where each one goes. Pure, so it runs under node --test.

// Picture `pic` (normalized, markup/picture.js) as SVG in its surface's
// points, as PictureView draws it: crop, then flips, then the turn.
// `frame`, the nested viewport {x, y, width, height} that clips the image
// to the frame; `image`, the whole image's box in that viewport, so sized
// and placed that the crop fills the frame; `flip`, its mirror about the
// frame's axes; `turn`, the frame's turn about its centre, clockwise as
// seen (CSS rotate's sense). `flip` and `turn` are transforms, or null
// when there is none.
export function pictureShape(pic) {
  const c = pic.crop, width = pic.w / c.w, height = pic.h / c.h;
  const fx = pic.flip_h ? -1 : 1, fy = pic.flip_v ? -1 : 1;
  return {
    frame: { x: pic.x, y: pic.y, width: pic.w, height: pic.h },
    image: { x: -c.x * width + 0, y: -c.y * height + 0, width, height }, // -0 as 0
    flip: fx < 0 || fy < 0 ? `matrix(${fx} 0 0 ${fy} ${fx < 0 ? pic.w : 0} ${fy < 0 ? pic.h : 0})` : null,
    turn: pic.angle ? `rotate(${pic.angle} ${pic.x + pic.w / 2} ${pic.y + pic.h / 2})` : null,
  };
}
