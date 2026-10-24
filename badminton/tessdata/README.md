# Tesseract model

`eng.traineddata` is Tesseract's English model, used to read booking screenshots (`badminton/services/ocr.py`). It ships with the code because the server can't install anything: the `tesserocr` wheel brings Tesseract itself, but no models.

| | |
|---|---|
| Source | [tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast), commit `87416418657359cb625c412a48b6e1d6d41c29bd` |
| SHA-256 | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |
| License | Apache 2.0, see [`LICENSE`](LICENSE) |

The *fast* model is small (4 MB) and reads the screenshots' clean text accurately.
