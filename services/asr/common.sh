# Общее для fetch.sh и serve.sh: версии и каталоги. Подключается через source, сам не запускается.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "$here/versions.env"

inference_dir="${COMPASS_INFERENCE_DIR:-$HOME/.cache/clin-compass}"
asr_dir="$inference_dir/models/asr"
# Каталог распакованной модели; fetch.sh кладёт сюда же bpe.vocab и silero_vad.onnx.
model_dir="$asr_dir/$MODEL_NAME"
