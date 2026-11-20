"""Seeded bi-level 'old scan' degradation of a clean grey equation image."""
import numpy as np
from PIL import Image, ImageFilter

PAD = 30
MARGIN = 10


def degrade(clean, seed):
    """clean: uint8 grey array (255 = paper). -> (uint8 0/255 array, params)"""
    rng = np.random.default_rng(seed)
    p = {"seed": int(seed)}
    im = Image.fromarray(np.pad(clean, PAD, constant_values=255), mode="L")

    # 1. ink spread (dilation) or thinning (erosion), 0..1 px (blend with a 3x3 filter)
    op = str(rng.choice(["none", "dilate", "erode"], p=[0.4, 0.35, 0.25]))
    # a 3x3 filter moves each edge by 1 px (2 px of stroke width); hairlines in
    # Times at 300 dpi are ~2 px, so thinning is kept to under half of that
    amt = {"none": 0.0, "dilate": float(rng.uniform(0.25, 0.8)),
           "erode": float(rng.uniform(0.1, 0.35))}[op]
    p["morph"] = op; p["morph_amount_px"] = round(amt, 3)
    if op != "none":
        f = im.filter(ImageFilter.MinFilter(3) if op == "dilate" else ImageFilter.MaxFilter(3))
        im = Image.blend(im, f, amt)

    # 2. small skew
    ang = float(rng.uniform(-0.6, 0.6))
    p["rotation_deg"] = round(ang, 3)
    im = im.rotate(ang, resample=Image.BICUBIC, fillcolor=255)

    # 3. optical blur
    sig = float(rng.uniform(0.6, 1.0))
    p["blur_sigma_px"] = round(sig, 3)
    im = im.filter(ImageFilter.GaussianBlur(sig))
    g = np.asarray(im, dtype=np.float32) / 255.0
    h, w = g.shape
    ink_ref = g < 0.5  # where the ink is, for cropping and speckle placement

    # 4. threshold with pixel noise and low-frequency paper/exposure variation
    nsig = float(rng.uniform(0.03, 0.09))
    lf_amp = float(rng.uniform(0.0, 0.06))
    lf = Image.fromarray(((rng.standard_normal((h, w)) * 0.5 + 0.5).clip(0, 1) * 255).astype(np.uint8))
    lf = np.asarray(lf.filter(ImageFilter.GaussianBlur(12)), dtype=np.float32) / 255.0
    lf = (lf - lf.mean()) / (lf.std() + 1e-6)
    thr = float(rng.uniform(0.5, 0.66))
    p.update(noise_sigma=round(nsig, 3), lowfreq_amp=round(lf_amp, 3), threshold=round(thr, 3))
    ink = (g + rng.standard_normal((h, w)) * nsig + lf * lf_amp) < thr

    # 5. JBIG2/CCITT-like resolution loss for some: thin strokes break
    if rng.random() < 0.4:
        fct = float(rng.uniform(0.5, 0.7))
        keep = float(rng.uniform(0.45, 0.6))
        p["lossy"] = {"scale": round(fct, 3), "cell_ink_fraction": round(keep, 3)}
        b = Image.fromarray((ink * 255).astype(np.uint8))
        sw, sh = max(1, round(w * fct)), max(1, round(h * fct))
        small = np.asarray(b.resize((sw, sh), Image.BOX), dtype=np.float32) / 255.0 > keep
        ink = np.asarray(Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.NEAREST)) > 127
    else:
        p["lossy"] = None

    # 6. crop to ink + margin
    ys, xs = np.nonzero(ink_ref)
    y0 = max(ys.min() - MARGIN, 0); y1 = min(ys.max() + MARGIN + 1, h)
    x0 = max(xs.min() - MARGIN, 0); x1 = min(xs.max() + MARGIN + 1, w)
    ink = ink[y0:y1, x0:x1].copy()
    ink_ref = ink_ref[y0:y1, x0:x1]
    h, w = ink.shape

    # 7. speckles (dust) and dropouts (white pinholes in ink)
    nsp = int(rng.integers(2, 13))
    ndr = int(rng.integers(0, 7))
    p.update(speckles=nsp, dropouts=ndr)
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(nsp):
        cy, cx, r = rng.uniform(0, h), rng.uniform(0, w), rng.uniform(0.6, 1.6)
        ink |= (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    iy, ix = np.nonzero(ink_ref)
    for _ in range(ndr):
        k = rng.integers(len(iy))
        r = rng.uniform(0.6, 1.2)
        ink &= ~((yy - iy[k]) ** 2 + (xx - ix[k]) ** 2 <= r * r)

    return np.where(ink, 0, 255).astype(np.uint8), p


def save_bilevel(a, path):
    Image.fromarray(a, mode="L").convert("1", dither=Image.NONE).save(path, optimize=True)
