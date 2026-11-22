# Text recognition models

The models Tesseract reads scanned PDF pages with
([backend/gamma/ocr_engine.py](../gamma/ocr_engine.py)): the fast LSTM
models for English, French and German, loaded together (`eng+fra+deu`), as
[the OCR study](../../docs/research/ocr.md) measured them. A server that
reads no text itself (the mobile apps, macOS before 15, no engine
installed) sends the same files to the window that asks, which reads the
pages with tesseract.js (`GET /api/ocr/models/<tag>/<lang>.traineddata`,
`ocr_engine.models_tag`). They live here, beside the gamma package and not
in it, so they stay out of the Docker image's code layer (the Dockerfile
copies this folder in a layer of its own). The mobile apps carry them
beside gamma/, where `config.tessdata_dir` looks by default: Android's
Chaquopy source set includes `tessdata/**` (android/app/build.gradle.kts),
and the iPad's ipad/scripts/build-python.sh copies the folder to
app/tessdata. `GAMMA_TESSDATA_DIR` points a server at another folder.

From [tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast),
commit `87416418657359cb625c412a48b6e1d6d41c29bd` (2024-08-01), unchanged:

| File | Bytes | SHA-256 |
|---|---|---|
| eng.traineddata | 4113088 | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |
| fra.traineddata | 1130365 | `ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca` |
| deu.traineddata | 1525436 | `19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d` |

[LICENSE](LICENSE) is that repository's: the Apache License 2.0.
[NOTICE](NOTICE) carries the notices of Tesseract, Leptonica and the image
libraries the tesserocr wheels bundle.

backend/tests/test_ocr_engine.py checks each file against its size and hash
here. To move to newer models, take the three files from one commit and
put its hash and theirs in this table. Text already recognized stays as it
is; only a run from then on reads with the new models.
