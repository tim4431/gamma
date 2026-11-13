#!/bin/sh
# Connect DeepSeek Harness on this computer to a Gamma server: add the Gamma
# plugin to the dsh profiles here and save the connection, which the plugin
# reads whenever dsh starts. Gamma's Settings > AI > Integrations > DeepSeek
# Harness gives the command that runs this with the server's address; it
# opens Gamma's sign-in in the browser, where the user allows the connection,
# and collects its token, which is never shown.
#   sh install-gamma-dsh.sh <server>/mcp [profile...]
#   sh install-gamma-dsh.sh --remove [profile...]
# A command from a Gamma without this sign-in passes a one-time setup code
# after the address, which is claimed instead.
# --remove undoes it: the plugin leaves every profile that has it (or those
# named), and the saved connection is deleted.
# GAMMA_DSH_BUNDLE=<file> installs that dsh-gamma tarball instead of the
# release's (development).
set -eu

# Release packaging pins the version, the tarball and its digest into this file.
gamma_version='__GAMMA_DSH_VERSION__'
gamma_download='__GAMMA_DSH_URL__'
gamma_digest='__GAMMA_DSH_SHA256__'

dsh_home="${DSH_HOME:-$HOME/.dsh}"
# The desktop app's own CLI manages its profile; a dsh on PATH or npx the others.
desktop_cli=""
for gamma_cli in "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" \
        "$HOME/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" \
        "/opt/DeepSeek Harness/resources/runtime/cli/bin/dsh"; do
    if [ -x "$gamma_cli" ]; then desktop_cli=$gamma_cli; break; fi
done
path_cli=$(command -v dsh 2>/dev/null || true)
npx_cli=$(command -v npx 2>/dev/null || true)
gamma_quit='If DeepSeek Harness is open, quit it completely, then run this command again.'

gamma_dsh() {  # profile, then the dsh arguments
    if [ -z "$desktop_cli$path_cli$npx_cli" ]; then
        printf 'Install DeepSeek Harness first: https://github.com/deepseek-ai/deepseek-harness\n' >&2; exit 1
    fi
    gamma_profile=$1; shift
    if [ "$gamma_profile" = desktop ] && [ -n "$desktop_cli" ]; then "$desktop_cli" "$@"
    elif [ -n "$path_cli" ]; then "$path_cli" "$@"
    elif [ -n "$npx_cli" ]; then "$npx_cli" --yes @deepseek-ai/dsh "$@"
    else "$desktop_cli" "$@"
    fi
}

gamma_installed() {  # the dsh-gamma version a profile has, else nothing
    sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$dsh_home/profiles/$1/node_modules/dsh-gamma/package.json" 2>/dev/null | head -n 1
}

if [ "${1:-}" = --remove ]; then
    shift
    gamma_profiles="$*"
    if [ -z "$gamma_profiles" ] && [ -d "$dsh_home/profiles" ]; then
        gamma_profiles=$(ls "$dsh_home/profiles")
    fi
    for gamma_profile in $gamma_profiles; do
        # Only a profile that depends on the plugin: `remove` refuses the others.
        grep -q '"dsh-gamma"' "$dsh_home/profiles/$gamma_profile/package.json" 2>/dev/null || continue
        printf 'Removing Gamma from the DeepSeek Harness %s profile...\n' "$gamma_profile"
        gamma_dsh "$gamma_profile" plugin --profile "$gamma_profile" remove dsh-gamma || {
            printf 'Could not remove Gamma from the %s profile (see above). %s\n' "$gamma_profile" "$gamma_quit" >&2
            exit 1
        }
    done
    rm -f "$dsh_home/gamma.json" "$dsh_home/dsh-gamma.tgz"
    printf 'Gamma is removed from DeepSeek Harness. In Gamma, disconnect it under Settings > AI > Integrations to end its access.\n'
    exit 0
fi

gamma_server=${1:?Copy the setup command from Gamma: Settings > AI > Integrations > DeepSeek Harness.}
shift
case "$gamma_server" in
    *'@'*|*'?'*|*'#'*|*' '*|*'"'*|*'\'*) printf 'Copy the setup command from Gamma again.\n' >&2; exit 1 ;;
    http://*/mcp|https://*/mcp) ;;
    *) printf 'Copy the setup command from Gamma again.\n' >&2; exit 1 ;;
esac
# An older Gamma's setup code: 20 to 128 of these characters, no profile's name.
gamma_code=""
case "${1:-}" in
    *[!A-Za-z0-9_-]*|'') ;;
    *) if [ "${#1}" -ge 20 ] && [ "${#1}" -le 128 ]; then gamma_code=$1; shift; fi ;;
esac
command -v curl >/dev/null 2>&1 || { printf 'Install curl first.\n' >&2; exit 1; }

gamma_profiles="$*"
if [ -z "$gamma_profiles" ]; then
    [ -n "$desktop_cli" ] && gamma_profiles=desktop
    [ -d "$dsh_home/profiles/web" ] && gamma_profiles="${gamma_profiles:+$gamma_profiles }web"
    [ -n "$gamma_profiles" ] || gamma_profiles=web
fi
for gamma_profile in $gamma_profiles; do
    if [ "$gamma_profile" = desktop ] && [ ! -f "$dsh_home/profiles/desktop/package.json" ]; then
        printf 'Open DeepSeek Harness once, quit it, and run this command again.\n' >&2; exit 1
    fi
done

# Add the plugin where it is missing or older; a profile that has this
# version keeps it, so connecting again needs no restart of the app first.
gamma_pending=""
for gamma_profile in $gamma_profiles; do
    if [ -n "${GAMMA_DSH_BUNDLE:-}" ] || [ "$(gamma_installed "$gamma_profile")" != "$gamma_version" ]; then
        gamma_pending="${gamma_pending:+$gamma_pending }$gamma_profile"
    fi
done
if [ -n "$gamma_pending" ] && [ -z "$desktop_cli$path_cli$npx_cli" ]; then
    printf 'Install DeepSeek Harness first: https://github.com/deepseek-ai/deepseek-harness\n' >&2; exit 1
fi
if [ -n "$gamma_pending" ]; then
    mkdir -p "$dsh_home"
    # One file every profile names: pnpm cannot add a remote tarball it resolved before.
    gamma_target="$dsh_home/dsh-gamma.tgz"
    if [ -n "${GAMMA_DSH_BUNDLE:-}" ]; then
        cp "$GAMMA_DSH_BUNDLE" "$gamma_target.part"
    else
        printf 'Downloading the Gamma plugin...\n'
        curl --fail --silent --show-error --location "$gamma_download" -o "$gamma_target.part"
        if command -v sha256sum >/dev/null 2>&1; then
            gamma_actual=$(sha256sum "$gamma_target.part" | cut -d ' ' -f 1)
        else
            gamma_actual=$(shasum -a 256 "$gamma_target.part" | cut -d ' ' -f 1)
        fi
        if [ "$gamma_actual" != "$gamma_digest" ]; then
            rm -f "$gamma_target.part"
            printf 'The Gamma plugin download is incomplete or changed. Run the command again.\n' >&2; exit 1
        fi
    fi
    mv -f "$gamma_target.part" "$gamma_target"
    for gamma_profile in $gamma_pending; do
        printf 'Adding Gamma to the DeepSeek Harness %s profile...\n' "$gamma_profile"
        gamma_dsh "$gamma_profile" plugin --profile "$gamma_profile" add "$gamma_target" || {
            printf 'Could not add Gamma to the %s profile (see above). %s\n' "$gamma_profile" "$gamma_quit" >&2
            exit 1
        }
    done
fi

gamma_base=${gamma_server%/mcp}
gamma_post() {  # path, JSON body: Gamma's reply
    curl --silent --show-error --location -X POST -H 'Content-Type: application/json' \
        --data "$2" "$gamma_base$1" || { printf 'Could not reach Gamma at %s.\n' "$gamma_base" >&2; exit 1; }
}
gamma_field() {  # JSON reply, name: that field's value
    printf '%s' "$1" | sed -n "s/.*\"$2\": *\"\\{0,1\\}\\([^\",}]*\\).*/\\1/p" | head -n 1
}
gamma_token_of() {  # JSON reply: its token, else nothing
    printf '%s' "$1" | sed -n 's/.*"token": *"\(gamma_[A-Za-z0-9_-]*\)".*/\1/p'
}
gamma_fail() {  # JSON reply, what to say without Gamma's own words
    gamma_detail=$(printf '%s' "$1" | sed -n 's/.*"detail": *"\([^"]*\)".*/\1/p')
    printf '%s\n' "${gamma_detail:-$2}" >&2; exit 1
}

printf 'Connecting to Gamma...\n'
if [ -n "$gamma_code" ]; then
    gamma_reply=$(gamma_post /api/integrations/claim "{\"code\":\"$gamma_code\"}") || exit 1
    gamma_token=$(gamma_token_of "$gamma_reply")
    [ -n "$gamma_token" ] || gamma_fail "$gamma_reply" 'Gamma did not accept the setup code. Copy a new command from Gamma.'
else
    # The browser sign-in: the page shows the code printed here, and the
    # user ticks the workspaces DeepSeek Harness may read and allows it.
    gamma_reply=$(gamma_post /api/integrations/device '{"name":"DeepSeek Harness"}') || exit 1
    gamma_device=$(gamma_field "$gamma_reply" device_code)
    gamma_user=$(gamma_field "$gamma_reply" user_code)
    case "$gamma_device" in
        *[!A-Za-z0-9_-]*|'') gamma_fail "$gamma_reply" 'Gamma did not start a sign-in. Copy the command from Gamma again.' ;;
    esac
    case "$gamma_user" in
        [A-Z][A-Z][A-Z][A-Z]-[A-Z][A-Z][A-Z][A-Z]) ;;
        *) gamma_fail "$gamma_reply" 'Gamma did not start a sign-in. Copy the command from Gamma again.' ;;
    esac
    # The address is built here, never taken from the reply, since it is opened.
    gamma_signin="$gamma_base/?gamma_device=$gamma_user"
    printf 'Allow DeepSeek Harness in Gamma, in your browser. Check that the page shows %s.\n' "$gamma_user"
    printf 'If no browser opens, go to %s\n' "$gamma_signin"
    if [ "$(uname 2>/dev/null)" = Darwin ]; then open "$gamma_signin" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$gamma_signin" >/dev/null 2>&1 || true
    fi
    gamma_interval=$(gamma_field "$gamma_reply" interval)
    case "$gamma_interval" in [1-9]|10) ;; *) gamma_interval=2 ;; esac
    gamma_left=$(gamma_field "$gamma_reply" expires_in)
    case "$gamma_left" in ''|*[!0-9]*) gamma_left=600 ;; esac
    gamma_token=""
    while [ -z "$gamma_token" ]; do
        if [ "$gamma_left" -le 0 ]; then printf 'The sign-in expired. Run the command again.\n' >&2; exit 1; fi
        sleep "$gamma_interval"
        gamma_left=$((gamma_left - gamma_interval))
        gamma_reply=$(gamma_post /api/integrations/device/token "{\"device_code\":\"$gamma_device\"}") || exit 1
        gamma_token=$(gamma_token_of "$gamma_reply")
        if [ -z "$gamma_token" ] && [ "$(gamma_field "$gamma_reply" status)" != pending ]; then
            gamma_fail "$gamma_reply" 'Gamma did not connect DeepSeek Harness. Run the command again.'
        fi
    done
fi
mkdir -p "$dsh_home"
(umask 077 && printf '{"url": "%s", "token": "%s"}\n' "$gamma_server" "$gamma_token" > "$dsh_home/gamma.json")
chmod 600 "$dsh_home/gamma.json"
printf "Gamma is connected. Start DeepSeek Harness, or restart it if it is open: Gamma's tools are mcp__gamma__...\n"
