#!/usr/bin/env bash
# Guarda y recupera el historial del bot entre corridas de GitHub Actions.
#
# El repo es público, así que el historial (operaciones simuladas, tus decisiones, avisos
# ya mandados, lecturas de Claude) se guarda CIFRADO con AES-256 usando el secreto
# STATE_KEY, en la rama "estado" (un solo commit que se reemplaza cada vez).
# Cualquiera puede ver que existe el archivo, pero sin STATE_KEY no se puede leer.
#
#   bash scripts/state.sh restore   # baja y descifra en data/
#   bash scripts/state.sh save      # cifra data/ y lo sube a la rama "estado"
#   bash scripts/state.sh selftest  # prueba cifrar/descifrar sin red
set -euo pipefail

BRANCH=estado
FILE=estado.tar.gz.enc
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

need_key() {
  if [ -z "${STATE_KEY:-}" ]; then
    echo "Falta el secreto STATE_KEY (ver README, fase 6)." >&2
    exit 1
  fi
}

pack() {   # data/ → $1 (cifrado), sin cachés grandes del backtest
  mkdir -p data/cache
  tar -czf "$TMP/estado.tgz" --exclude=data/cache/backtest --exclude=data/backtest data
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:STATE_KEY \
    -in "$TMP/estado.tgz" -out "$1"
}

unpack() { # $1 (cifrado) → data/
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:STATE_KEY -in "$1" | tar -xz
}

case "${1:-}" in
  restore)
    need_key
    if git fetch --quiet --depth=1 origin "$BRANCH" 2>/dev/null; then
      git show "FETCH_HEAD:$FILE" > "$TMP/$FILE"
      unpack "$TMP/$FILE"
      echo "Historial recuperado."
    else
      echo "Sin historial previo (primera corrida)."
    fi
    ;;
  save)
    need_key
    pack "$TMP/$FILE"
    REPO_DIR="$TMP/repo"
    mkdir "$REPO_DIR"
    cp "$TMP/$FILE" "$REPO_DIR/"
    cd "$REPO_DIR"
    git init --quiet
    git checkout --quiet -b "$BRANCH"
    git add "$FILE"
    git -c user.name="pead-bot" -c user.email="pead-bot@users.noreply.github.com" \
      commit --quiet -m "Historial cifrado $(date -u +%Y-%m-%dT%H:%MZ)"
    git push --quiet --force \
      "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$BRANCH"
    echo "Historial guardado (cifrado)."
    ;;
  selftest)
    need_key
    pack "$TMP/$FILE"
    if head -c 64 "$TMP/$FILE" | LC_ALL=C grep -q "sqlite\|alert:"; then
      echo "ERROR: el archivo no parece cifrado" >&2; exit 1
    fi
    mkdir "$TMP/out" && (cd "$TMP/out" && unpack "$TMP/$FILE")
    diff -rq data "$TMP/out/data" --exclude=backtest && echo "selftest ok: cifra y descifra igual"
    ;;
  *)
    echo "uso: $0 restore|save|selftest" >&2; exit 2;;
esac
