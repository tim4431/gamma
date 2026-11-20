"""PP-DocLayoutV3 made smaller: fp16 weights (inputs/outputs kept fp32) and dynamic int8.

  venv-quant\\Scripts\\python.exe -I tools\\shrink_layout.py <in.onnx> <out_dir>

(venv-quant\\ is under $OCR_STUDY_DIR\\stage3\\engines.)"""
import pathlib
import sys

import onnx
from onnxconverter_common import float16
from onnxruntime.quantization import QuantType, quantize_dynamic

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
m = onnx.load(str(src))
m16 = float16.convert_float_to_float16(m, keep_io_types=True)
onnx.save(m16, str(out / "fp16.onnx"))
quantize_dynamic(str(src), str(out / "int8.onnx"), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul", "Gemm"])
for f in ("fp16.onnx", "int8.onnx"):
    print(f, round((out / f).stat().st_size / 2**20, 1), "MB")
