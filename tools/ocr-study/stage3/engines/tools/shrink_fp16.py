"""fp16 PP-DocLayoutV3 that loads: convert, then point the graph's own Cast-to-float nodes at
float16 (the converter keeps them, so fp32 tensors meet fp16 ones), keeping the converter's
input/output casts. venv-quant\\Scripts\\python.exe -I tools\\shrink_fp16.py <in.onnx> <out.onnx>
(venv-quant\\ is under $OCR_STUDY_DIR\\stage3\\engines.)"""
import sys
import onnx
from onnx import TensorProto
from onnxconverter_common import float16

m = float16.convert_float_to_float16(onnx.load(sys.argv[1]), keep_io_types=True)
outs = {o.name for o in m.graph.output}
n = 0
for node in m.graph.node:
    if node.op_type == "Cast" and not node.name.startswith("graph_") and not set(node.output) & outs:
        for a in node.attribute:
            if a.name == "to" and a.i == TensorProto.FLOAT:
                a.i = TensorProto.FLOAT16
                n += 1
del m.graph.value_info[:]
onnx.save(m, sys.argv[2])
import onnxruntime as ort
ort.InferenceSession(sys.argv[2])
print(n, "casts retargeted; loads")
