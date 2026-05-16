#!/usr/bin/env bash
# Waybar custom module for CodexBar (Linux CLI).
# Fetches usage from configured providers sequentially (with a small stagger
# to avoid per-provider rate limits) and emits Waybar JSON
# ({"text","tooltip","class","percentage"}) keyed on the most-constrained window.
# Last successful per-provider snapshot is cached, so a transient 429 is masked
# until the next refresh recovers it.
#
# Linux-only providers used here (cookies/web providers are macOS-only).
# Edit PROVIDERS / SOURCE_OVERRIDES below to add more.

set -u

CODEXBAR="${CODEXBAR_BIN:-${HOME}/.local/bin/codexbar}"
CACHE_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/codexbar-waybar"
mkdir -p "$CACHE_DIR"

# Provider list and per-provider source override.
# Codex/Claude need explicit --source oauth on Linux (auto tries web first).
PROVIDERS=(codex claude gemini)
declare -A SOURCE_OVERRIDES=(
    [codex]=oauth
    [claude]=oauth
)

fetch_provider() {
    local p="$1"
    local src="${SOURCE_OVERRIDES[$p]:-}"
    local args=(usage --provider "$p" --format json --no-color)
    [[ -n "$src" ]] && args+=(--source "$src")
    "$CODEXBAR" "${args[@]}" 2>/dev/null
}

# Sequential fetch with a small stagger between providers (avoids 429s).
STAGGER_SECS="${CODEXBAR_STAGGER:-0.5}"
LAST_GOOD="$CACHE_DIR/last.json"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

i=0
for p in "${PROVIDERS[@]}"; do
    (( i > 0 )) && sleep "$STAGGER_SECS"
    fetch_provider "$p" > "$tmpdir/$p.json"
    i=$((i + 1))
done

merged="["
first=1
for p in "${PROVIDERS[@]}"; do
    body="$(cat "$tmpdir/$p.json")"
    # CLI returns a JSON array; unwrap and append elements.
    if [[ -z "$body" ]] || ! echo "$body" | jq -e 'type == "array"' >/dev/null 2>&1; then
        continue
    fi
    inner="$(echo "$body" | jq -c '.[]')"
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        if (( first )); then
            merged+="$entry"
            first=0
        else
            merged+=",$entry"
        fi
    done <<< "$inner"
done
merged+="]"

# Fill in errored providers with the last successful snapshot (marks them stale).
if [[ -f "$LAST_GOOD" ]] && [[ "$merged" != "[]" ]]; then
    merged="$(jq -c --argjson prev "$(cat "$LAST_GOOD")" '
        ([$prev[]? | select(.error | not) | {key: .provider, value: .}] | from_entries) as $ok_prev
        | map(if .error and $ok_prev[.provider]
              then $ok_prev[.provider] + {stale: true}
              else . end)
    ' <<< "$merged")"
fi

# Persist snapshot if at least one provider succeeded.
if [[ "$merged" != "[]" ]] && echo "$merged" | jq -e 'any(.error | not)' >/dev/null 2>&1; then
    echo "$merged" > "$LAST_GOOD"
fi

if [[ "$merged" == "[]" ]]; then
    printf '{"text":"","tooltip":"CodexBar: no provider data","class":"stale","percentage":0}\n'
    exit 0
fi

echo "$merged" | jq -c --arg now "$(date -u +%FT%TZ)" '
    # Collect all usage windows across providers
    def provider_name(p):
        {codex:"Codex", claude:"Claude", gemini:"Gemini",
         copilot:"Copilot", openai:"OpenAI", cursor:"Cursor",
         vertexai:"Vertex AI", openrouter:"OpenRouter"}[p] // (p | ascii_upcase);

    def reset_phrase(d):
        if d == null then ""
        elif (d | test("^[Rr]esets")) then " — \(d)"
        else " — resets \(d)" end;

    def fmt_window(w; name):
        if w == null or w.usedPercent == null then empty
        else "\(name): \(w.usedPercent)%" + reset_phrase(w.resetDescription)
        end;

    def provider_lines(entry):
        if entry.error then
            "\(provider_name(entry.provider)): error — \(entry.error.message)"
        else
            [
                fmt_window(entry.usage.primary;   "\(provider_name(entry.provider)) primary"),
                fmt_window(entry.usage.secondary; "\(provider_name(entry.provider)) secondary"),
                fmt_window(entry.usage.tertiary;  "\(provider_name(entry.provider)) tertiary")
            ] | map(select(. != null)) | join("\n")
        end;

    def max_pct(entry):
        if entry.error then 0
        else
            [entry.usage.primary.usedPercent,
             entry.usage.secondary.usedPercent,
             entry.usage.tertiary.usedPercent]
            | map(select(. != null)) | (max // 0)
        end;

    . as $all
    | ($all | map(max_pct(.)) | max // 0) as $pct
    | ($all | map(provider_lines(.)) | map(select(. != ""))) as $lines
    | ($all | all(.error)) as $all_errored
    | ($all | any(.stale == true)) as $any_stale
    | {
        text: (if $all_errored then "🤖 ⚠"
               else "🤖 \($pct)%" end),
        tooltip: ($lines | join("\n")),
        class: (if $all_errored then "stale"
                elif $pct >= 90 then "critical"
                elif $pct >= 70 then "warning"
                elif $any_stale then "stale"
                else "ok" end),
        percentage: $pct
    }
'
