#!/bin/sh
# Export only public frontend settings; jq escapes values as JavaScript-compatible JSON.
set -eu
config=/usr/share/nginx/html/runtime-config.js
printf 'window.__RAV_PUBLIC_CONFIG__ = ' > "$config"
jq -n --arg apiUrl "${VITE_API_URL:-}" \
    --arg supabaseUrl "${VITE_SUPABASE_URL:-}" \
    --arg supabasePublishableKey "${VITE_SUPABASE_PUBLISHABLE_KEY:-${VITE_SUPABASE_ANON_KEY:-}}" \
    '{apiUrl: $apiUrl, supabaseUrl: $supabaseUrl, supabasePublishableKey: $supabasePublishableKey}' >> "$config"
printf ';\n' >> "$config"
