#!/bin/sh
set -eu

# Encode bytes, never interpolate operator input into JavaScript syntax.
encode() {
    printf '%s' "$1" | od -An -v -tx1 | awk '{ for (i = 1; i <= NF; i++) printf "%%%s", $i }'
}

output=${1:-/tmp/langboard-runtime-config.js}
temporary="${output}.tmp"
umask 022
{
    # Leave a rejected sentinel if decoding ever fails; never fall back silently.
    printf 'globalThis.__LANGBOARD_RUNTIME_CONFIG__ = {invalid: true};\n'
    printf 'globalThis.__LANGBOARD_RUNTIME_CONFIG__ = {\n'
    for key in API_URL PUBLIC_UI_URL SOCKET_URL; do
        case "$key" in
            API_URL) value=${LANGBOARD_UI_API_URL:-} ;;
            PUBLIC_UI_URL) value=${LANGBOARD_UI_PUBLIC_UI_URL:-} ;;
            SOCKET_URL) value=${LANGBOARD_UI_SOCKET_URL:-} ;;
        esac
        if [ -n "$value" ]; then
            printf '"%s": decodeURIComponent("%s"),\n' "$key" "$(encode "$value")"
        fi
    done
    printf '};\n'
} > "$temporary"
mv -f "$temporary" "$output"

# Embed the same public configuration before module execution. Keep the
# standalone script for existing clients, without a blocking navigation request.
index_source=${2:-/usr/share/nginx/html/index.html}
index_output=${3:-/tmp/langboard-index.html}
if [ -f "$index_source" ]; then
    awk '
        FNR == NR { config = config $0 "\n"; next }
        match($0, /<script src="\/runtime-config.js"><\/script>/) {
            print substr($0, 1, RSTART - 1) "<script>\n" config "</script>" substr($0, RSTART + RLENGTH)
            replaced = 1
            next
        }
        { print }
        END { if (!replaced) exit 1 }
    ' "$output" "$index_source" > "${index_output}.tmp"
    mv -f "${index_output}.tmp" "$index_output"
fi
