#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

vast_directory="${HOME}/.config/vastai"
runpod_directory="${HOME}/.runpod"
rm -f "${vast_directory}/vast_api_key" "${runpod_directory}/config.toml"

required_variables=(
    SKYPILOT_INITIAL_BASIC_AUTH
    VAST_API_KEY
    RUNPOD_API_KEY
)
for variable_name in "${required_variables[@]}"; do
    if [[ -z "${!variable_name:-}" ]]; then
        printf 'Required SkyPilot server secret is missing: %s\n' "${variable_name}" >&2
        exit 1
    fi
done

install -d -m 700 "${vast_directory}" "${runpod_directory}"

vast_staged_file="$(mktemp "${vast_directory}/vast_api_key.XXXXXX")"
cleanup() {
    rm -f "${vast_staged_file}"
}
trap cleanup EXIT

printf '%s\n' "${VAST_API_KEY}" >"${vast_staged_file}"
chmod 600 "${vast_staged_file}"
mv -f "${vast_staged_file}" "${vast_directory}/vast_api_key"

python -c 'import os, runpod; runpod.set_credentials(os.environ["RUNPOD_API_KEY"], overwrite=True)'
chmod 600 "${runpod_directory}/config.toml"

/usr/local/bin/refresh-runpod-catalog.py

unset VAST_API_KEY RUNPOD_API_KEY
sky api start --deploy --enable-basic-auth --foreground &
server_pid="$!"
server_ready=false

stop_server() {
    if kill -0 "${server_pid}" 2>/dev/null; then
        kill "${server_pid}"
        wait "${server_pid}" || true
    fi
}
trap stop_server EXIT INT TERM

for _ in {1..60}; do
    if curl --fail --silent http://127.0.0.1:46580/api/health >/dev/null; then
        server_ready=true
        break
    fi
    sleep 1
done

if [[ "${server_ready}" != true ]]; then
    printf 'SkyPilot API server did not become healthy within 60 seconds.\n' >&2
    exit 1
fi

if ! kill -0 "${server_pid}" 2>/dev/null; then
    wait "${server_pid}"
fi

wait "${server_pid}"
