#!/usr/bin/env bash
# Install system + Python dependencies for Lesarin.
#
# System packages are required for the OCR fallback (scanned PDFs):
#   tesseract-ocr      — the OCR engine
#   tesseract-ocr-dan  — Danish language data (closest available to Faroese)
#   poppler-utils      — PDF rasterisation used by pdf2image
set -euo pipefail

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq || apt-get update -qq || true
  (sudo apt-get install -y -qq tesseract-ocr tesseract-ocr-dan poppler-utils \
    || apt-get install -y -qq tesseract-ocr tesseract-ocr-dan poppler-utils) || \
    echo "WARN: could not install system OCR packages; digital-PDF path still works."
else
  echo "WARN: apt-get not found. Install tesseract-ocr, tesseract-ocr-dan and poppler-utils manually for OCR."
fi

python3 -m pip install -q -r "$(dirname "$0")/../requirements.txt"

echo "Setup complete."
