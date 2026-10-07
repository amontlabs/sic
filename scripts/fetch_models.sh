#!/usr/bin/env bash
# Downloads the speech models used by the pipeline (sherpa-onnx segmentation, speaker embeddings, VAD).
# The Whisper model (mlx-community/whisper-large-v3-turbo) is fetched automatically on first use.
set -euo pipefail
cd "$(dirname "$0")/../data" && mkdir -p models && cd models
base=https://github.com/k2-fsa/sherpa-onnx/releases/download
if [ ! -f sherpa-onnx-pyannote-segmentation-3-0/model.onnx ]; then
  curl -sSL "$base/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" | tar xj
fi
[ -f nemo_en_titanet_small.onnx ] || curl -sSL -O "$base/speaker-recongition-models/nemo_en_titanet_small.onnx"
[ -f silero_vad.onnx ] || curl -sSL -O "$base/asr-models/silero_vad.onnx"
echo "models ready in $(pwd)"
