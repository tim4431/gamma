#!/usr/bin/env bash
# TexTeller 3 into $OCR_STUDY_DIR/stage3/engines/dl (default OCR_STUDY_DIR: tools/ocr-study/.work).
set -u
T="$(cd "$(dirname "$0")" && pwd)"
cd "$T"
WORK=${OCR_STUDY_DIR:-../../../.work}
mkdir -p "$WORK/stage3/engines/dl"
DL=$(cd "$WORK/stage3/engines/dl" && pwd)
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/README.md"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/config.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/generation_config.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/tokenizer.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/tokenizer_config.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/vocab.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/merges.txt"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/special_tokens_map.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/added_tokens.json"
bash "$T/fetch.sh" "$DL"/texteller-3 "https://huggingface.co/OleehyO/TexTeller/resolve/7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea/model.safetensors" 6d535b29f94de294f6d82cb777285379480672cbe396bbd645840eda56b2c407
