#!/usr/bin/env bash
# The ONNX detectors and recognizers (and the Paddle model of PP-FormulaNet_plus-S, for
# its conversion) into $OCR_STUDY_DIR/stage3/engines/dl (default OCR_STUDY_DIR: tools/ocr-study/.work).
set -u
T="$(cd "$(dirname "$0")" && pwd)"
cd "$T"
WORK=${OCR_STUDY_DIR:-../../../.work}
mkdir -p "$WORK/stage3/engines/dl"
DL=$(cd "$WORK/stage3/engines/dl" && pwd)
bash "$T/fetch.sh" "$DL"/rapid-layout-pp_doc_layoutv3 "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv3.onnx" 250dbad1dfb9e4983fab75e1bf5085cd56ec3f41d5c7d0f8623ec74856e7aa67
bash "$T/fetch.sh" "$DL"/rapid-layout-doclayout_docstructbench "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/doclayout/doclayout_yolo_docstructbench_imgsz1024.onnx" 3b452baef10ecabd615491bc82cc4d49475fbc2cd7a8e535044f2c6bb28fb9fe
bash "$T/fetch.sh" "$DL"/pp-doclayout_plus-l-onnx "https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L_onnx/resolve/feb74619326f634e0e883218598096a3733ad9f7/README.md"
bash "$T/fetch.sh" "$DL"/pp-doclayout_plus-l-onnx "https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L_onnx/resolve/feb74619326f634e0e883218598096a3733ad9f7/inference.yml"
bash "$T/fetch.sh" "$DL"/pp-doclayout_plus-l-onnx "https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L_onnx/resolve/feb74619326f634e0e883218598096a3733ad9f7/inference.onnx" 77afb2caa74dd13240d087d2eced91d7fcd2caebd16006a0a66162fc8707ff0e
bash "$T/fetch.sh" "$DL"/mineru4-onnx-formulanet_plus_m "https://huggingface.co/opendatalab/MinerU-4_models_onnx/resolve/358310b4f64b95f9fefc372ad899356e4111f376/README.md"
bash "$T/fetch.sh" "$DL"/mineru4-onnx-formulanet_plus_m "https://huggingface.co/opendatalab/MinerU-4_models_onnx/resolve/358310b4f64b95f9fefc372ad899356e4111f376/manifest.json"
bash "$T/fetch.sh" "$DL"/mineru4-onnx-formulanet_plus_m "https://huggingface.co/opendatalab/MinerU-4_models_onnx/resolve/358310b4f64b95f9fefc372ad899356e4111f376/MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M_inference.yml"
bash "$T/fetch.sh" "$DL"/mineru4-onnx-formulanet_plus_m "https://huggingface.co/opendatalab/MinerU-4_models_onnx/resolve/358310b4f64b95f9fefc372ad899356e4111f376/MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M.onnx" 20a32595c2b30282dcd069e6295e9fad96115697beb66acb9b681b814006d193
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/README.md"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/config.json"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/generation_config.json"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/preprocessor_config.json"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/special_tokens_map.json"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/tokenizer.json"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/tokenizer_config.json"
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/encoder_model.onnx" 080a3f660f08bc9ebcacdd96e34be6b6400f8c7e62d7cd0dd8251badc37f610b
bash "$T/fetch.sh" "$DL"/pix2text-mfr-1.5 "https://huggingface.co/breezedeus/pix2text-mfr-1.5/resolve/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/decoder_model.onnx" 917deb98e91a0453c5f234f58a0f32f9fb037de8527c7eb4ed394daf9e692f2a
bash "$T/fetch.sh" "$DL"/rapid-latex-ocr-v0.0.0 "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/image_resizer.onnx"
bash "$T/fetch.sh" "$DL"/rapid-latex-ocr-v0.0.0 "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/encoder.onnx"
bash "$T/fetch.sh" "$DL"/rapid-latex-ocr-v0.0.0 "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/decoder.onnx"
bash "$T/fetch.sh" "$DL"/rapid-latex-ocr-v0.0.0 "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/tokenizer.json"
bash "$T/fetch.sh" "$DL"/pp-formulanet_plus-s-paddle "https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S/resolve/3d46f557e3a1752f4bf81202395af3b5ecfadfd2/README.md"
bash "$T/fetch.sh" "$DL"/pp-formulanet_plus-s-paddle "https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S/resolve/3d46f557e3a1752f4bf81202395af3b5ecfadfd2/config.json"
bash "$T/fetch.sh" "$DL"/pp-formulanet_plus-s-paddle "https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S/resolve/3d46f557e3a1752f4bf81202395af3b5ecfadfd2/inference.yml"
bash "$T/fetch.sh" "$DL"/pp-formulanet_plus-s-paddle "https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S/resolve/3d46f557e3a1752f4bf81202395af3b5ecfadfd2/inference.json"
bash "$T/fetch.sh" "$DL"/pp-formulanet_plus-s-paddle "https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S/resolve/3d46f557e3a1752f4bf81202395af3b5ecfadfd2/inference.pdiparams" e464f94412feaa98f8791eacc84684f887b3569e30e80c52b8112e9cf7d4069b
