#!/usr/bin/env bash
# SmartStock rootless development stack.
#
# PostgreSQL 16 + pgvector and Ollama, with no Docker and no sudo. Everything
# lives under ~/.local/share/smartstock, so nothing touches system packages.
#
#   scripts/devstack.sh start | stop | status | psql | logs | bootstrap
#
# `bootstrap` provisions from nothing (micromamba, Postgres, extensions, Ollama)
# and is safe to re-run. `start` assumes bootstrap already happened.
set -euo pipefail

ROOT="${SMARTSTOCK_STACK_ROOT:-$HOME/.local/share/smartstock}"
PGENV="$ROOT/pgenv"
PGDATA="$ROOT/pgdata"
PGBIN="$PGENV/bin"
PGPORT="${SMARTSTOCK_PGPORT:-5432}"
PGUSER_NAME="smartstock"
PGDB="smartstock"
OLLAMA_BIN="$ROOT/ollama/bin/ollama"
OLLAMA_ADDR="${SMARTSTOCK_OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_MODEL="${SMARTSTOCK_OLLAMA_MODEL:-granite3.1-moe:3b}"
MICROMAMBA="$HOME/.local/bin/micromamba"

export PGPASSWORD=smartstock
export OLLAMA_MODELS="${OLLAMA_MODELS:-$ROOT/ollama-models}"

DATABASE_URL="postgresql+psycopg://smartstock:smartstock@127.0.0.1:${PGPORT}/${PGDB}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m warn\033[0m %s\n' "$*" >&2; }

pg_running() { "$PGBIN/pg_isready" -q -h 127.0.0.1 -p "$PGPORT" 2>/dev/null; }
ollama_running() { curl -sf --max-time 2 "http://${OLLAMA_ADDR}/api/tags" >/dev/null 2>&1; }

bootstrap() {
  mkdir -p "$ROOT" "$HOME/.local/bin"

  if [ ! -x "$MICROMAMBA" ]; then
    log "installing micromamba"
    curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
      | tar -xj -C /tmp bin/micromamba
    install -m 0755 /tmp/bin/micromamba "$MICROMAMBA"
  fi

  if [ ! -x "$PGBIN/postgres" ]; then
    log "installing postgresql 16 + pgvector (rootless)"
    MAMBA_ROOT_PREFIX="$ROOT/mamba" "$MICROMAMBA" create -y -p "$PGENV" \
      -c conda-forge postgresql=16 pgvector
  fi

  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    log "initialising cluster at $PGDATA"
    rm -rf "$PGDATA"; mkdir -p "$PGDATA"
    printf 'smartstock' > "$ROOT/.pgpw"
    "$PGBIN/initdb" -D "$PGDATA" --username="$PGUSER_NAME" --pwfile="$ROOT/.pgpw" \
      --auth-local=trust --auth-host=scram-sha-256 -E UTF8 >/dev/null
    rm -f "$ROOT/.pgpw"
  fi

  if [ ! -x "$OLLAMA_BIN" ]; then
    log "installing ollama (rootless tarball)"
    local tag
    tag=$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest \
          | sed -n 's/.*"tag_name": "\([^"]*\)".*/\1/p' | head -1)
    curl -Ls -o "$ROOT/ollama.tar.zst" \
      "https://github.com/ollama/ollama/releases/download/${tag}/ollama-linux-amd64.tar.zst"
    mkdir -p "$ROOT/ollama"
    tar --zstd -xf "$ROOT/ollama.tar.zst" -C "$ROOT/ollama"
    rm -f "$ROOT/ollama.tar.zst"
  fi

  start
  provision_db
  if ! "$OLLAMA_BIN" list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
    log "pulling $OLLAMA_MODEL (this is a multi-GB download)"
    OLLAMA_HOST="$OLLAMA_ADDR" "$OLLAMA_BIN" pull "$OLLAMA_MODEL"
  fi
  log "bootstrap complete"
}

provision_db() {
  "$PGBIN/psql" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${PGDB}'" | grep -q 1 \
    || "$PGBIN/createdb" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" "$PGDB"
  # Tests run against their own database so they cannot pollute demo data.
  "$PGBIN/psql" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${PGDB}_test'" | grep -q 1 \
    || "$PGBIN/createdb" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" "${PGDB}_test"
  for db in "$PGDB" "${PGDB}_test"; do
    "$PGBIN/psql" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" -d "$db" -q -c \
      "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
  done
}

start() {
  if pg_running; then
    log "postgres already listening on $PGPORT"
  else
    log "starting postgres"
    # setsid detaches into its own session so that a terminating parent shell
    # (or an agent sandbox reaping a process group) cannot take the server down.
    setsid nohup "$PGBIN/postgres" -D "$PGDATA" \
      -p "$PGPORT" -k /tmp -c listen_addresses=127.0.0.1 \
      >>"$PGDATA/server.log" 2>&1 < /dev/null &
    disown 2>/dev/null || true
    for _ in $(seq 1 60); do pg_running && break; sleep 1; done
  fi

  if ollama_running; then
    log "ollama already listening on $OLLAMA_ADDR"
  elif [ -x "$OLLAMA_BIN" ]; then
    log "starting ollama"
    OLLAMA_HOST="$OLLAMA_ADDR" OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-2h}" \
      setsid nohup "$OLLAMA_BIN" serve >"$ROOT/ollama.log" 2>&1 < /dev/null &
    disown 2>/dev/null || true
    for _ in $(seq 1 30); do ollama_running && break; sleep 1; done
  else
    warn "ollama not installed; run: scripts/devstack.sh bootstrap"
  fi
  status
}

stop() {
  if pg_running; then
    log "stopping postgres"
    "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null || true
  fi
  pkill -f "$OLLAMA_BIN serve" 2>/dev/null && log "stopped ollama" || true
}

status() {
  printf '\n  postgres  %s  %s\n' \
    "$(pg_running && echo 'up  ' || echo 'down')" "$DATABASE_URL"
  if pg_running; then
    printf '  schema    %s\n' \
      "$("$PGBIN/psql" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" -d "$PGDB" -tAc \
         'select version_num from alembic_version' 2>/dev/null || echo 'not migrated')"
  fi
  printf '  ollama    %s  http://%s\n' \
    "$(ollama_running && echo 'up  ' || echo 'down')" "$OLLAMA_ADDR"
  if ollama_running; then
    printf '  models    %s\n' \
      "$(curl -s "http://${OLLAMA_ADDR}/api/tags" \
         | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | paste -sd', ' - || true)"
  fi
  printf '\n'
}

case "${1:-start}" in
  bootstrap) bootstrap ;;
  start)     start ;;
  stop)      stop ;;
  restart)   stop; start ;;
  status)    status ;;
  psql)      shift; exec "$PGBIN/psql" -w -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER_NAME" -d "$PGDB" "$@" ;;
  logs)      tail -n "${2:-40}" "$PGDATA/server.log" ;;
  *) echo "usage: $0 {bootstrap|start|stop|restart|status|psql|logs}" >&2; exit 2 ;;
esac
