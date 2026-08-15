#!/bin/bash
# Shared, SAFE reader for the deployment .env.
#
# WHY THIS EXISTS (OPS-003, 2026-08-15)
# ------------------------------------
# Four scripts needed values out of .env and each one did `. "$TARSIERPOS_DIR/.env"`
# -- i.e. asked BASH to EXECUTE it. But .env is not a shell script: on pos-01 it
# holds
#     DJANGO_SECRET_KEY=2jem(th$hr32kk9qv70m=*odf+145-&lgl1u@w*3(tt59q1szl
# and the unquoted `(` makes bash die with
#     .env: line 1: syntax error near unexpected token `('
# BEFORE it sets anything. Every variable then falls back to a wrong default, and
# the script either misreports or aborts.
#
# The same single character silently broke THREE production behaviours on the live
# register, undetected for a month:
#   * tarsierpos-cert-renew  -> exited 1 every run; the TLS cert was heading for
#     expiry on 2026-09-03, which would have taken https://localhost -- and with it
#     the kiosk -- down.
#   * daily-health           -> wrong service name + "cert not found", two false
#     alarms daily that trained everyone to ignore the one TRUE warning.
#   * backup_db (indirectly) -> its failure was the warning nobody read.
#
# systemd's EnvironmentFile= parser does NO shell expansion, which is exactly why
# gunicorn never cared and the breakage stayed invisible.
#
# This reader parses .env as DATA: literal KEY=VALUE, no evaluation, no subshell,
# and only the keys a caller explicitly asks for. A hostile or merely awkward value
# cannot execute anything.
#
# Usage:
#   . "$(dirname "$0")/../lib/load-env.sh"
#   tarsierpos_load_env "$TARSIERPOS_DIR/.env" TAILSCALE_HOSTNAME TARSIERPOS_SERVICE
#
# Returns 0 even when the file is absent -- callers decide whether a missing value
# is fatal.

tarsierpos_load_env() {
    local envf="$1"; shift || true
    [ -f "$envf" ] || return 0
    local want=" $* "
    local line key val
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        key=${line%%=*}
        [ "$key" = "$line" ] && continue          # no '=' on this line
        # keys are shell-identifier-shaped; anything else is not ours to touch
        case "$key" in
            *[!A-Za-z0-9_]*|'') continue ;;
        esac
        case "$want" in
            *" $key "*) ;;
            *) continue ;;
        esac
        val=${line#*=}
        # strip ONE layer of matching surrounding quotes, the way systemd does
        case "$val" in
            \"*\") val=${val#\"}; val=${val%\"} ;;
            \'*\') val=${val#\'}; val=${val%\'} ;;
        esac
        printf -v "$key" '%s' "$val"
        export "${key?}"
    done < "$envf"
    return 0
}
