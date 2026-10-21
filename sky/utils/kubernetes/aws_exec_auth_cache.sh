#!/usr/bin/env bash
# `aws` front installed first on the API server's PATH by exec_auth_cache.py.
# `aws ... eks get-token ...` is served from a per-host cache shared by all
# processes; every other invocation runs the real CLI unchanged.
#
# The installer prepends REAL_AWS and CACHE_DIR assignments to this file.
# Requires bash >= 5.0 (EPOCHSECONDS), flock, sha256sum and GNU date.

# An entry younger than this is served without taking the lock.
FRESH_SECONDS=300
# An entry is never served within this many seconds of its expiry.
MIN_REMAINING_SECONDS=60
# After a failed refresh, a stale entry is served without retrying for this long.
RETRY_SECONDS=30
LOCK_WAIT_SECONDS=30

prev=''
is_get_token=0
for arg in "$@"; do
    if [[ "$prev" == eks && "$arg" == get-token ]]; then
        is_get_token=1
        break
    fi
    prev="$arg"
done
if (( ! is_get_token )); then
    exec "$REAL_AWS" "$@"
fi

umask 077
if [[ ! -d "$CACHE_DIR" ]]; then
    mkdir -p "$CACHE_DIR" || exec "$REAL_AWS" "$@"
fi

# argv and the AWS_* environment select the identity, so both key the entry.
key=$({
    printf '%s\0' "$@"
    for name in ${!AWS_*}; do
        printf '%s=%s\0' "$name" "${!name}"
    done
} | sha256sum) || exec "$REAL_AWS" "$@"
entry="$CACHE_DIR/${key%% *}"

# Entry format: "<fetched epoch> <expiry epoch>\n<aws stdout>\n".
load_entry() {
    fetched=''
    expiry=''
    body=''
    [[ -f "$entry" ]] || return 1
    { IFS=' ' read -r fetched expiry; IFS= read -r -d '' body; } \
        2>/dev/null < "$entry"
    if [[ "$fetched" =~ ^[0-9]+$ && "$expiry" =~ ^[0-9]+$ && -n "$body" ]]; then
        return 0
    fi
    body=''
    return 1
}

is_usable() {
    (( EPOCHSECONDS < expiry - MIN_REMAINING_SECONDS ))
}

is_fresh() {
    (( EPOCHSECONDS < fetched + FRESH_SECONDS )) && is_usable
}

retry_due() {
    local retry_at=''
    { read -r retry_at; } 2>/dev/null < "$entry.retry"
    [[ ! "$retry_at" =~ ^[0-9]+$ ]] || (( EPOCHSECONDS >= retry_at ))
}

serve() {
    printf '%s' "$body"
    exit 0
}

# Runs the real command; on success sets body and stores it when the output
# carries an expirationTimestamp. Failures are never stored.
refresh() {
    local started=$EPOCHSECONDS out expires tmp
    local re='"expirationTimestamp":[[:space:]]*"([^"]+)"'
    out=$("$REAL_AWS" "$@" {lock_fd}>&-) || return
    if [[ "$out" =~ $re ]] &&
        expires=$(date -u -d "${BASH_REMATCH[1]}" +%s 2>/dev/null); then
        tmp="$entry.$$.tmp"
        { printf '%s %s\n%s\n' "$started" "$expires" "$out" > "$tmp" &&
            mv -f "$tmp" "$entry"; } || rm -f "$tmp"
    fi
    body="$out"$'\n'
}

if load_entry && is_fresh; then
    serve
fi

exec {lock_fd}>>"$entry.lock" || exec "$REAL_AWS" "$@"

if [[ -n "$body" ]] && is_usable; then
    # Stale but usable: one process refreshes, the others keep serving it.
    retry_due || serve
    flock -n "$lock_fd" || serve
    if load_entry && is_fresh; then
        serve
    fi
else
    # Nothing usable: wait for an in-flight refresh, then fetch if still needed.
    flock -w "$LOCK_WAIT_SECONDS" "$lock_fd"
    locked=$?
    if load_entry && is_usable; then
        serve
    fi
    if (( locked != 0 )); then
        echo "Timed out waiting for another aws eks get-token to finish." >&2
        exit 1
    fi
fi

refresh "$@" && serve
rc=$?
if [[ -n "$body" ]] && is_usable; then
    printf '%s\n' "$(( EPOCHSECONDS + RETRY_SECONDS ))" > "$entry.retry"
    echo "aws eks get-token failed (exit $rc); using the cached token," \
        "which expires in $(( expiry - EPOCHSECONDS ))s." >&2
    serve
fi
exit "$rc"
