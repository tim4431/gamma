#!/usr/bin/env bash
# llama.cpp b12359 (Windows CPU and CUDA 12.4 builds) and the five VLMs' GGUF files into
# $OCR_STUDY_DIR/stage3/engines/dl (default OCR_STUDY_DIR: tools/ocr-study/.work). The
# llama.cpp zips are then unpacked into dl/llamacpp-cpu-b12359/bin and dl/llamacpp-cuda12-b12359/bin.
set -u
T="$(cd "$(dirname "$0")" && pwd)"
cd "$T"
WORK=${OCR_STUDY_DIR:-../../../.work}
mkdir -p "$WORK/stage3/engines/dl"
DL=$(cd "$WORK/stage3/engines/dl" && pwd)
bash "$T/fetch.sh" "$DL"/llamacpp-cpu-b12359 "https://github.com/ggml-org/llama.cpp/releases/download/b12359/llama-b12359-bin-win-cpu-x64.zip" 837c1655e87df3b66989f4be33a792a1399c785bf259038dc75c900096683e69
bash "$T/fetch.sh" "$DL"/llamacpp-cuda12-b12359 "https://github.com/ggml-org/llama.cpp/releases/download/b12359/llama-b12359-bin-win-cuda-12.4-x64.zip" e9c0abf442e8339c06728366c1ad2c711332f9939c0c00603fb75d85fd44c914
bash "$T/fetch.sh" "$DL"/llamacpp-cuda12-b12359 "https://github.com/ggml-org/llama.cpp/releases/download/b12359/cudart-llama-bin-win-cuda-12.4-x64.zip" 8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6
bash "$T/fetch.sh" "$DL"/paddleocr-vl-1.6-gguf "https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF/resolve/511b09642bb324401f15f97cc23bc67e8f0a291d/README.md"
bash "$T/fetch.sh" "$DL"/paddleocr-vl-1.6-gguf "https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF/resolve/511b09642bb324401f15f97cc23bc67e8f0a291d/chat_template.jinja"
bash "$T/fetch.sh" "$DL"/paddleocr-vl-1.6-gguf "https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF/resolve/511b09642bb324401f15f97cc23bc67e8f0a291d/PaddleOCR-VL-1.6-GGUF.gguf" f3ae46ec885050acf4b3d31944431e1fd90d50664fb09126af4a3c050ba14ee8
bash "$T/fetch.sh" "$DL"/paddleocr-vl-1.6-gguf "https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF/resolve/511b09642bb324401f15f97cc23bc67e8f0a291d/PaddleOCR-VL-1.6-GGUF-mmproj.gguf" 204d757d7610d9b3faab10d506d69e5b244e32bf765e2bab2d0167e65e0a058a
bash "$T/fetch.sh" "$DL"/glm-ocr-gguf "https://huggingface.co/ggml-org/GLM-OCR-GGUF/resolve/65a42de1148dbed2297e922b5dbc7d9b70c36578/README.md"
bash "$T/fetch.sh" "$DL"/glm-ocr-gguf "https://huggingface.co/ggml-org/GLM-OCR-GGUF/resolve/65a42de1148dbed2297e922b5dbc7d9b70c36578/GLM-OCR-Q8_0.gguf" 45bc244a6446aff850521dc41f18bc8d7105ad5f0c2c8c28af04e7cc4f4d50b1
bash "$T/fetch.sh" "$DL"/glm-ocr-gguf "https://huggingface.co/ggml-org/GLM-OCR-GGUF/resolve/65a42de1148dbed2297e922b5dbc7d9b70c36578/mmproj-GLM-OCR-Q8_0.gguf" 9c4b58e33e316ed142eb5dcb41abec3844d3e6e5dc361ffb782c3fa9d175141f
bash "$T/fetch.sh" "$DL"/granite-docling-258m-gguf "https://huggingface.co/ibm-granite/granite-docling-258M-GGUF/resolve/e1e344cb19bae46a1d5edb4f04a27ebe0c268f72/granite-docling-258M-BF16.gguf" fba182dea1c3adccbf2c5c22971fa7d4479587d83b20607405df1710a3683723
bash "$T/fetch.sh" "$DL"/granite-docling-258m-gguf "https://huggingface.co/ibm-granite/granite-docling-258M-GGUF/resolve/e1e344cb19bae46a1d5edb4f04a27ebe0c268f72/mmproj-model-f16.gguf" 4b7b2c4ed3162bd9cb323668c188e2324838b8f8ca4f3f9767f16c4c600d43ad
bash "$T/fetch.sh" "$DL"/lightonocr-2-1b-gguf "https://huggingface.co/ggml-org/LightOnOCR-2-1B-GGUF/resolve/7d4daf70c2856a155b1413def00f4ae59843a9cf/LightOnOCR-2-1B-Q8_0.gguf" f4cfc0ea9765a4cac22a235191c48aefa94df223419f5ab658b6201f36206d58
bash "$T/fetch.sh" "$DL"/lightonocr-2-1b-gguf "https://huggingface.co/ggml-org/LightOnOCR-2-1B-GGUF/resolve/7d4daf70c2856a155b1413def00f4ae59843a9cf/mmproj-LightOnOCR-2-1B-Q8_0.gguf" e9b45eb85b0f0afb3cc138236eec7adb75879420130e5e2049ee9af3f24e337f
bash "$T/fetch.sh" "$DL"/dots-ocr-gguf "https://huggingface.co/ggml-org/dots.ocr-GGUF/resolve/2c093a32ca360a396bc6d87d60408636130b9d9b/README.md"
bash "$T/fetch.sh" "$DL"/dots-ocr-gguf "https://huggingface.co/ggml-org/dots.ocr-GGUF/resolve/2c093a32ca360a396bc6d87d60408636130b9d9b/dots.ocr-Q8_0.gguf" 6f2db1a75215893760b7c7185d52f11715d2e0cb7bff9cefd3f6d409fa1de7e9
bash "$T/fetch.sh" "$DL"/dots-ocr-gguf "https://huggingface.co/ggml-org/dots.ocr-GGUF/resolve/2c093a32ca360a396bc6d87d60408636130b9d9b/mmproj-dots.ocr-Q8_0.gguf" 20d3d35bea3a64ead0b4b213f8db0de136e174bb5b75b8e85786c51e8e822423
