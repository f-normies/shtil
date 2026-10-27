#!/usr/bin/env bash
# Скачивает GigaAM-v3 e2e_rnnt и Silero VAD, сверяет SHA-256 из versions.env и строит bpe.vocab.
# Файлы лежат вне репозитория: $COMPASS_INFERENCE_DIR/models/asr (по умолчанию ~/.cache/clin-compass).
# Если файлы уже скачаны и хеши совпадают, повторный запуск ничего не качает.
set -euo pipefail

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

sha_matches() {
  [[ "$(shasum -a 256 "$1" | cut -d ' ' -f 1)" == "$2" ]]
}

# fetch <url> <файл> <sha256>: качает, только если файла нет или хеш другой.
fetch() {
  local url=$1 dest=$2 sha=$3
  if [[ -f "$dest" ]] && sha_matches "$dest" "$sha"; then
    echo "Уже скачан: $dest"
    return
  fi
  curl -fL --retry 3 -o "$dest.part" "$url"
  if ! sha_matches "$dest.part" "$sha"; then
    rm -f "$dest.part"
    echo "SHA-256 файла $url не совпал с versions.env. Файл удалён." >&2
    exit 1
  fi
  mv "$dest.part" "$dest"
}

mkdir -p "$asr_dir"
archive="$asr_dir/$MODEL_NAME.tar.bz2"
fetch "$ASR_MODELS_URL/$MODEL_NAME.tar.bz2" "$archive" "$MODEL_SHA256"

# Распаковка при каждом запуске: файлы модели всегда совпадают с проверенным архивом.
tar -xjf "$archive" -C "$asr_dir"

# Подсказкам в beam search нужен bpe.vocab, а в архиве его нет. Рецепт sherpa-onnx
# (.github/scripts/test-online-transducer.sh): токены из tokens.txt с равными оценками —
# кодировщик берёт самое длинное совпадение.
awk '{print $1 "\t-1.0"}' "$model_dir/tokens.txt" >"$model_dir/bpe.vocab"

fetch "$ASR_MODELS_URL/$VAD_FILE" "$model_dir/$VAD_FILE" "$VAD_SHA256"

echo "Модель готова: $model_dir"
