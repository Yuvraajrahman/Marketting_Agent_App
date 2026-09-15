#!/usr/bin/env bash
# Optional Applio/RVC third_party clone + voice model layout.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/third_party" "$ROOT/models/voice"

if [[ ! -d "$ROOT/third_party/applio" ]]; then
  echo "Cloning Applio into third_party/ (optional RVC engine)…"
  git clone --depth 1 https://github.com/IAHispano/Applio.git "$ROOT/third_party/applio" || true
fi

echo ""
echo "Voice setup notes:"
echo "  1. Follow Applio Apple Silicon docs inside third_party/applio"
echo "  2. Copy hubert_base.pt to models/voice/hubert_base.pt"
echo "  3. Place RVC .pth (+ optional .index) in models/voice/<name>.pth"
echo "  4. brew install blackhole-2ch — meeting app mic = BlackHole 2ch"
echo "  5. pip install torch sounddevice soundfile"
echo ""
echo "Done."
