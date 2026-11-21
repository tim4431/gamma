"""Check the paddle2onnx conversion of PP-FormulaNet_plus-S against Paddle Inference.

Step 1 (main venv):  venv\\Scripts\\python.exe -I tools\\check_formulanet_s.py dump crops\\smoke
    pre-processes each crop exactly as recognize.py does, runs the converted ONNX, saves
    input tensor + token ids to work/check_s/<crop>.npz
Step 2 (venv-p2o):  venv-p2o\\Scripts\\python.exe -I tools\\check_formulanet_s.py paddle
    runs the official Paddle inference model (paddlepaddle 3.1.1, CPU) on the same tensors and
    compares token ids.
(venv\\, venv-p2o\\, crops\\smoke, work\\ and dl\\ are under $OCR_STUDY_DIR\\stage3\\engines.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

ENG = common.ENGINES
OUT = ENG / "work" / "check_s"


def dump(crops_dir):
    import numpy as np

    import recognize

    common.patch_onnxruntime()
    m = recognize.build("pp-formulanet-plus-s")
    OUT.mkdir(parents=True, exist_ok=True)
    for c in sorted(Path(crops_dir).glob("*.png")):
        x = m.preprocess(recognize.load_rgb(c))
        ids = m.sess.run(None, {m.inp: x})[0]
        np.savez(OUT / f"{c.stem}.npz", x=x, ids=ids)
        print(c.stem, x.shape, ids.shape)


def paddle_check():
    import numpy as np
    import paddle.inference as pi

    d = ENG / "dl" / "pp-formulanet_plus-s-paddle"
    cfg = pi.Config(str(d / "inference.json"), str(d / "inference.pdiparams"))
    cfg.disable_gpu()
    cfg.disable_mkldnn()  # the oneDNN scale kernel throws inside the decode loop on this build
    cfg.set_cpu_math_library_num_threads(8)
    pred = pi.create_predictor(cfg)
    iname = pred.get_input_names()[0]
    for f in sorted(OUT.glob("*.npz")):
        z = np.load(f)
        h = pred.get_input_handle(iname)
        h.copy_from_cpu(z["x"])
        pred.run()
        out = pred.get_output_handle(pred.get_output_names()[0]).copy_to_cpu()
        a, b = z["ids"][0].tolist(), out[0].tolist()
        a = a[: a.index(2) + 1] if 2 in a else a
        b = b[: b.index(2) + 1] if 2 in b else b
        print(f.stem, "identical" if a == b else f"DIFFER onnx={len(a)} paddle={len(b)} first diff at {next((i for i,(p,q) in enumerate(zip(a,b)) if p!=q), min(len(a),len(b)))}")


if __name__ == "__main__":
    if sys.argv[1] == "dump":
        dump(sys.argv[2])
    else:
        paddle_check()
