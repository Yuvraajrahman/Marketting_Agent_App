#!/usr/bin/env bash
# Clone Deep-Live-Cam (optional) and document face model placement.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/third_party" "$ROOT/models/faces"

if [[ ! -d "$ROOT/third_party/Deep-Live-Cam" ]]; then
  echo "Cloning Deep-Live-Cam into third_party/ (optional engine reference)…"
  git clone --depth 1 https://github.com/hacksider/Deep-Live-Cam.git "$ROOT/third_party/Deep-Live-Cam" || true
fi

echo ""
echo "Face setup notes:"
echo "  1. Place a clear front-facing photo in models/faces/<name>.jpg"
echo "  2. Place inswapper_128.onnx in models/ (Deep-Live-Cam / FaceFusion download it on first run)"
echo "  3. pip install insightface onnxruntime opencv-python-headless pyvirtualcam"
echo "  4. Enable OBS Virtual Camera for meeting apps"
echo ""
echo "Done."
