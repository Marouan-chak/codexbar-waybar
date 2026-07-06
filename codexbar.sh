#!/usr/bin/env bash
# Waybar custom module for CodexBar (Linux CLI).
# Fetches usage from configured providers sequentially (with a small stagger
# to avoid per-provider rate limits) and emits Waybar JSON
# ({"text","tooltip","class","percentage"}) keyed on the most-constrained window.
# Last successful per-provider snapshot is cached, so a transient 429 is masked
# until the next refresh recovers it.
#
# Linux-only providers — cookies/web providers are macOS-only. The list is
# read from ~/.codexbar/config.json (use the popover's Settings view to
# manage it); falls back to codex+claude+gemini if the file is missing.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CODEXBAR="${CODEXBAR_BIN:-${HOME}/.local/bin/codexbar}"
CONFIG_PATH="${HOME}/.codexbar/config.json"
STATE_PATH="${XDG_CONFIG_HOME:-${HOME}/.config}/codexbar-waybar/state.json"
CACHE_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/codexbar-waybar"
mkdir -p "$CACHE_DIR"

# Per-instance bar provider selection (written by the popup's Settings view).
# `null` (or unset) means "show the highest used% across providers".
BAR_PROVIDER=""
# How to render reset times in the tooltip / popover:
#   provider — use the provider's `resetDescription` as-is (default; backward
#              compatible — Claude OAuth gives "May 17 at 6:20AM" with no TZ,
#              Codex gives "7:10 AM" in UTC with no TZ marker, etc.)
#   local    — format `resetsAt` (ISO 8601 UTC) in the system local timezone
#              with a today / this-year / future-year tier and a TZ suffix
#   utc      — same tiering, formatted in UTC with a literal "UTC" suffix
RESET_TIME_FORMAT="provider"
if [[ -f "$STATE_PATH" ]] && command -v jq >/dev/null 2>&1; then
    BAR_PROVIDER="$(jq -r '.barProvider // empty' "$STATE_PATH" 2>/dev/null)"
    rf="$(jq -r '.resetTimeFormat // empty' "$STATE_PATH" 2>/dev/null)"
    [[ -n "$rf" ]] && RESET_TIME_FORMAT="$rf"
fi
[[ -n "${CODEXBAR_BAR_PROVIDER:-}" ]] && BAR_PROVIDER="$CODEXBAR_BAR_PROVIDER"
[[ -n "${CODEXBAR_RESET_TIME_FORMAT:-}" ]] && RESET_TIME_FORMAT="$CODEXBAR_RESET_TIME_FORMAT"
case "$RESET_TIME_FORMAT" in
    provider|local|utc) ;;
    *) RESET_TIME_FORMAT="provider" ;;
esac

# Read enabled providers from the codexbar CLI's config, fall back to a
# sensible default if it's missing or unreadable. Override with the
# CODEXBAR_PROVIDERS env var (space-separated list) when you want to bypass
# config.json for a specific waybar instance.
if [[ -n "${CODEXBAR_PROVIDERS:-}" ]]; then
    # shellcheck disable=SC2206
    PROVIDERS=( ${CODEXBAR_PROVIDERS} )
elif [[ -f "$CONFIG_PATH" ]] && command -v jq >/dev/null 2>&1; then
    readarray -t PROVIDERS < <(jq -r '[.providers[]? | select(.enabled == true) | .id] | .[]' "$CONFIG_PATH" 2>/dev/null)
    [[ ${#PROVIDERS[@]} -eq 0 ]] && PROVIDERS=(codex claude gemini)
else
    PROVIDERS=(codex claude gemini)
fi

# Setup Antigravity SSL if it is one of the enabled providers.
setup_antigravity_ssl() {
    if ! command -v openssl >/dev/null 2>&1 || ! command -v lsof >/dev/null 2>&1 || ! command -v pgrep >/dev/null 2>&1; then
        return 1
    fi

    local pid
    pid=$(pgrep -f "Antigravity.*/language_server" | head -n 1)
    if [[ -z "$pid" ]]; then
        return 1
    fi

    local ports
    ports=$(lsof -a -p "$pid" -iTCP -sTCP:LISTEN -F n 2>/dev/null | grep -oE '[0-9]+$')
    if [[ -z "$ports" ]]; then
        return 1
    fi

    local cert=""
    # shellcheck disable=SC2086
    for port in $ports; do
        local out
        out=$(openssl s_client -showcerts -connect 127.0.0.1:"$port" < /dev/null 2>/dev/null)
        if [[ "$out" == *"-----BEGIN CERTIFICATE-----"* ]]; then
            cert=$(echo "$out" | openssl x509 -outform PEM 2>/dev/null)
            if [[ -n "$cert" ]]; then
                break
            fi
        fi
    done

    if [[ -z "$cert" ]]; then
        return 1
    fi

    local scratch_dir="${CACHE_DIR}/scratch"
    mkdir -p "$scratch_dir"
    local cert_file="${scratch_dir}/localhost.crt"
    echo "$cert" > "$cert_file"

    local sys_ca=""
    for path in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/ca-bundle.pem /var/lib/ca-certificates/ca-bundle.pem; do
        if [[ -f "$path" ]]; then
            sys_ca="$path"
            break
        fi
    done

    local bundle_file="${scratch_dir}/custom-ca-bundle.crt"
    if [[ -n "$sys_ca" ]]; then
        cat "$sys_ca" "$cert_file" > "$bundle_file"
    else
        cat "$cert_file" > "$bundle_file"
    fi

    ANTIGRAVITY_CUSTOM_CA_BUNDLE="$bundle_file"
    ANTIGRAVITY_LD_PRELOAD="${SCRIPT_DIR}/cert_redirect.so"
    return 0
}

if [[ " ${PROVIDERS[*]} " == *" antigravity "* ]]; then
    setup_antigravity_ssl
fi

# Per-provider --source override. Codex/Claude need explicit oauth on Linux
# (their `auto` falls back to web first, which is macOS-only).
declare -A SOURCE_OVERRIDES=(
    [codex]=oauth
    [claude]=oauth
)

# If the primary source returns a provider-level error (e.g. Claude OAuth
# hitting HTTP 429), fall back to this source. Mostly useful for Claude
# where the local CLI logs have the same windowing data.
declare -A FALLBACK_SOURCES=(
    [claude]=cli
)

# Antigravity has no Linux login flow in the CLI: it expects Google OAuth
# credentials at ~/.codexbar/antigravity/oauth_creds.json (written by the macOS
# app), or passed inline via $ANTIGRAVITY_OAUTH_CREDENTIALS_JSON. On Linux the
# Antigravity CLI (`agy`) logs in through the shared Gemini flow and drops the
# same Google creds at ~/.gemini/oauth_creds.json. Bridge that file into the
# env var so an `agy` login satisfies codexbar without a second login.
# Override the source path with $CODEXBAR_ANTIGRAVITY_CREDS.
ANTIGRAVITY_CREDS="${CODEXBAR_ANTIGRAVITY_CREDS:-${HOME}/.gemini/oauth_creds.json}"

fetch_one() {
    local p="$1" src="$2"
    local args=(usage --provider "$p" --format json --no-color)
    [[ -n "$src" ]] && args+=(--source "$src")
    local runner=()
    if [[ "${PROVIDER_TIMEOUT_SECS:-20}" != "0" ]] && command -v timeout >/dev/null 2>&1; then
        runner=(timeout "${PROVIDER_TIMEOUT_SECS:-20}")
    fi
    if [[ "$p" == "antigravity" ]]; then
        if [[ -z "${ANTIGRAVITY_OAUTH_CREDENTIALS_JSON:-}" && -f "$ANTIGRAVITY_CREDS" ]]; then
            "${runner[@]}" env \
                CUSTOM_CA_BUNDLE="${ANTIGRAVITY_CUSTOM_CA_BUNDLE:-}" \
                LD_PRELOAD="${ANTIGRAVITY_LD_PRELOAD:-}" \
                ANTIGRAVITY_OAUTH_CREDENTIALS_JSON="$(cat "$ANTIGRAVITY_CREDS")" \
                "$CODEXBAR" "${args[@]}" 2>/dev/null
        else
            "${runner[@]}" env \
                CUSTOM_CA_BUNDLE="${ANTIGRAVITY_CUSTOM_CA_BUNDLE:-}" \
                LD_PRELOAD="${ANTIGRAVITY_LD_PRELOAD:-}" \
                "$CODEXBAR" "${args[@]}" 2>/dev/null
        fi
        return
    fi
    "${runner[@]}" "$CODEXBAR" "${args[@]}" 2>/dev/null
}

fetch_provider() {
    local p="$1"
    local primary="${SOURCE_OVERRIDES[$p]:-}"
    local fallback="${FALLBACK_SOURCES[$p]:-}"

    local body fallback_body
    body="$(fetch_one "$p" "$primary")"

    # Only retry when the response is a valid array with a provider-level
    # error — network failures and rate limits land here. Auth misconfig
    # surfaces the same way; the fallback may still error, which is fine
    # because the cache layer will mask it.
    if [[ -n "$fallback" ]] \
        && echo "$body" | jq -e 'type == "array" and (.[0].error // null) != null' >/dev/null 2>&1; then
        fallback_body="$(fetch_one "$p" "$fallback")"
        if echo "$fallback_body" | jq -e 'type == "array"' >/dev/null 2>&1; then
            body="$fallback_body"
        fi
    fi

    echo "$body"
}

# Sequential fetch with a small stagger between providers (avoids 429s).
STAGGER_SECS="${CODEXBAR_STAGGER:-0.5}"
PROVIDER_TIMEOUT_SECS="${CODEXBAR_PROVIDER_TIMEOUT:-20}"
LAST_GOOD="$CACHE_DIR/last.json"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

i=0
for p in "${PROVIDERS[@]}"; do
    (( i > 0 )) && sleep "$STAGGER_SECS"
    fetch_provider "$p" > "$tmpdir/$p.json"
    i=$((i + 1))
done

append_merged_entry() {
    local entry="$1"
    [[ -z "$entry" ]] && return
    if (( first )); then
        merged+="$entry"
        first=0
    else
        merged+=",$entry"
    fi
}

merged="["
first=1
for p in "${PROVIDERS[@]}"; do
    body="$(cat "$tmpdir/$p.json")"
    # CLI returns a JSON array; unwrap and append elements.
    if [[ -z "$body" ]]; then
        continue
    fi
    if ! echo "$body" | jq -e 'type == "array"' >/dev/null 2>&1; then
        append_merged_entry "$(jq -cn \
            --arg provider "$p" \
            --arg message "invalid JSON from provider" \
            '{provider: $provider, error: {message: $message}}')"
        continue
    fi
    inner="$(echo "$body" | jq -c '.[]')"
    while IFS= read -r entry; do
        append_merged_entry "$entry"
    done <<< "$inner"
done
merged+="]"

last_good_json=""
if [[ -f "$LAST_GOOD" ]]; then
    last_good_json="$(jq -c 'select(type == "array")' "$LAST_GOOD" 2>/dev/null || true)"
fi

# Persist fresh successful provider snapshots without dropping older successful
# entries for providers that failed this refresh.
if [[ "$merged" != "[]" ]] && echo "$merged" | jq -e 'any(.error | not)' >/dev/null 2>&1; then
    if [[ -n "$last_good_json" ]]; then
        merged_for_cache="$(jq -c --argjson prev "$last_good_json" '
            def cache_key: (.provider // "") + "\u0000" + (.account // "");
            ([$prev[]? | select((.error | not) and (.stale != true))
              | {key: cache_key, value: .}] | from_entries) as $ok_prev
            | ([.[]? | select(.error | not)
            | {key: cache_key, value: (del(.stale))}] | from_entries) as $ok_fresh
            | ($ok_prev + $ok_fresh) | [.[]]
        ' <<< "$merged")"
        [[ -n "$merged_for_cache" ]] && echo "$merged_for_cache" > "$LAST_GOOD"
    else
        echo "$merged" | jq -c 'map(select(.error | not) | del(.stale))' > "$LAST_GOOD"
    fi
    last_good_json="$(jq -c 'select(type == "array")' "$LAST_GOOD" 2>/dev/null || true)"
fi

# Fill in errored or missing providers with the last successful snapshot for
# display (marks them stale). Missing happens when a provider returns empty
# output instead of a JSON array.
if [[ -n "$last_good_json" ]]; then
    providers_json="$(printf '%s\n' "${PROVIDERS[@]}" | jq -R . | jq -s .)"
    merged="$(jq -c \
        --argjson prev "$last_good_json" \
        --argjson requested "$providers_json" '
        def cache_key: (.provider // "") + "\u0000" + (.account // "");
        ([$prev[]? | select(.error | not) | {key: cache_key, value: .}] | from_entries) as $ok_prev
        | ([$prev[]? | select(.error | not)] | group_by(.provider) | map({key: .[0].provider, value: .}) | from_entries) as $prev_by_provider
        | (map(.provider) | unique) as $seen
        | map(if .error and $ok_prev[cache_key]
              then $ok_prev[cache_key] + {stale: true}
              else . end) as $direct_current
        | ($direct_current
           | map(select(.error and ($ok_prev[cache_key] | not)) as $err
                 | ($prev_by_provider[$err.provider] // [])
                 | map(. + {stale: true}))
           | add // []) as $provider_replacements
        | ($direct_current | map(select((.error and ($prev_by_provider[.provider] // [] | length > 0)) | not))) as $current
        | ($requested
           | map(. as $pid | select(($seen | index($pid)) | not))
           | map(($prev_by_provider[.] // []) | map(. + {stale: true}))
           | add // []) as $missing
        | $current + $provider_replacements + $missing
    ' <<< "$merged")"
fi

if [[ "$merged" == "[]" ]]; then
    printf '{"text":"","tooltip":"CodexBar: no provider data","class":"stale","percentage":0}\n'
    exit 0
fi

echo "$merged" | jq -c \
    --arg now "$(date -u +%FT%TZ)" \
    --arg bar_provider "$BAR_PROVIDER" \
    --arg reset_format "$RESET_TIME_FORMAT" '
    # Collect all usage windows across providers
    def provider_name(p):
        if p == null then "Unknown"
        else {
          abacus:"Abacus AI", alibaba:"Alibaba", alibabatokenplan:"Alibaba Token Plan",
          amp:"Amp", antigravity:"Antigravity", augment:"Augment",
          azureopenai:"Azure OpenAI", bedrock:"AWS Bedrock", chutes:"Chutes",
          claude:"Claude", clawrouter:"ClawRouter", codebuff:"Codebuff",
          codex:"Codex", commandcode:"Command Code", copilot:"Copilot",
          crof:"Crof", crossmodel:"CrossModel", cursor:"Cursor",
          deepgram:"Deepgram", deepseek:"DeepSeek", devin:"Devin",
          doubao:"Doubao", elevenlabs:"ElevenLabs", factory:"Droid",
          gemini:"Gemini", grok:"Grok", groq:"Groq",
          jetbrains:"JetBrains AI", kilo:"Kilo", kimi:"Kimi",
          kimik2:"Kimi K2", kiro:"Kiro", litellm:"LiteLLM",
          llmproxy:"LLM Proxy", manus:"Manus", mimo:"Xiaomi MiMo",
          minimax:"MiniMax", mistral:"Mistral", moonshot:"Moonshot / Kimi API",
          ollama:"Ollama", openai:"OpenAI", opencode:"OpenCode",
          opencodego:"OpenCode Go", openrouter:"OpenRouter", perplexity:"Perplexity",
          poe:"Poe", qoder:"Qoder", sakana:"Sakana AI",
          stepfun:"StepFun", synthetic:"Synthetic", t3chat:"T3 Chat",
          venice:"Venice", vertexai:"Vertex AI", warp:"Warp",
          windsurf:"Windsurf", zai:"z.ai", zed:"Zed"
        }[p] // (p | gsub("-"; " ") | ascii_upcase)
        end;

    def entry_name(entry):
        entry as $entry
        | provider_name($entry.provider)
          + (if (($entry.account // "") != "") then " (\($entry.account))" else "" end);

    # Insert spaces the providers omit. Claude OAuth gives "May 17 at 6:20AM"
    # (no space before AM/PM); Claude CLI gives "Resets6:20am(Europe/Paris)"
    # (no space anywhere). Normalise both to a single, consistent style.
    def normalize_reset:
        sub("^Resets(?=\\S)"; "Resets ")
        | sub("^resets(?=\\S)"; "resets ")
        | gsub("(?<=\\S)\\("; " (")
        | gsub(",(?=\\S)"; ", ")
        | gsub("(?<=\\d)(?=[AaPp][Mm]\\b)"; " ");

    # Format a UNIX timestamp in the system local timezone, tiered by how far
    # away the reset is. Today drops the date; this year drops the year.
    def fmt_local_ts:
        . as $ts
        | ($ts | strflocaltime("%Y-%m-%d")) as $rd
        | (now | strflocaltime("%Y-%m-%d")) as $td
        | ($ts | strflocaltime("%Y")) as $ry
        | (now | strflocaltime("%Y")) as $cy
        | if $rd == $td then ($ts | strflocaltime("%-I:%M %p %Z"))
          elif $ry == $cy then ($ts | strflocaltime("%b %-d at %-I:%M %p %Z"))
          else ($ts | strflocaltime("%b %-d %Y at %-I:%M %p %Z")) end;

    # Same tiering, formatted in UTC. We hardcode the "UTC" suffix because
    # `strftime("%Z")` after `gmtime` is empty on some libc builds.
    def fmt_utc_ts:
        . as $ts
        | ($ts | gmtime | strftime("%Y-%m-%d")) as $rd
        | (now | gmtime | strftime("%Y-%m-%d")) as $td
        | ($ts | gmtime | strftime("%Y")) as $ry
        | (now | gmtime | strftime("%Y")) as $cy
        | if $rd == $td then ($ts | gmtime | strftime("%-I:%M %p UTC"))
          elif $ry == $cy then ($ts | gmtime | strftime("%b %-d at %-I:%M %p UTC"))
          else ($ts | gmtime | strftime("%b %-d %Y at %-I:%M %p UTC")) end;

    # Build the trailing " — resets …" fragment for a usage window. Mode is
    # one of "provider" (preserve the provider string), "local", or "utc".
    # Relative phrases like "Resets in 2 hours" are kept verbatim even in
    # absolute modes — "in 2 hours" is more useful than a wall-clock time.
    def reset_phrase(w):
        if w == null then ""
        else (w.resetDescription // "" | normalize_reset) as $clean
             | (if $clean == "" then ""
                elif ($clean | test("^[Rr]esets")) then " — \($clean)"
                else " — resets \($clean)" end) as $from_desc
             | if $reset_format == "provider" then $from_desc
               elif ($clean | test("^[Rr]esets in ")) then $from_desc
               elif w.resetsAt != null then
                   (try (w.resetsAt | fromdateiso8601) catch null) as $ts
                   | if $ts == null then $from_desc
                     elif $reset_format == "utc" then " — resets \($ts | fmt_utc_ts)"
                     else " — resets \($ts | fmt_local_ts)" end
               else $from_desc
               end
        end;

    def fmt_window(w; name):
        if w == null or w.usedPercent == null then empty
        else "\(name): \(w.usedPercent)%" + reset_phrase(w)
        end;

    def fmt_named_window(item; provider):
        if item == null or item.window == null then empty
        else (item.title // item.id // "Extra quota") as $title
             | if (item | has("usageKnown")) and item.usageKnown == false then
                   "\(provider) \($title): reset tracked" + reset_phrase(item.window)
               else fmt_window(item.window; "\(provider) \($title)") end
        end;

    def money(v; currency):
        if currency == null or currency == "USD" then "$\((v * 100 | round / 100))"
        else "\(currency) \((v * 100 | round / 100))" end;

    def status_line(entry):
        entry as $entry
        | if $entry.status == null then empty
        else ($entry.status.indicator // "unknown" | gsub("_"; " ") | ascii_upcase) as $indicator
             | if ($entry.status.description // "") == "" then
                   "\(entry_name($entry)) status: \($indicator)"
               else
                   "\(entry_name($entry)) status: \($indicator) — \($entry.status.description)"
               end
        end;

    def pace_lines(entry):
        entry as $entry
        |
        [
          ($entry.pace.primary.summary? | select(. != null) | "\(entry_name($entry)) session pace: \(.)"),
          ($entry.pace.secondary.summary? | select(. != null) | "\(entry_name($entry)) weekly pace: \(.)")
        ];

    def credits_lines(entry):
        entry as $entry
        |
        [
          ($entry.credits.remaining? | select(type == "number")
           | "\(entry_name($entry)) credits: \(money(.; "USD")) remaining"),
          ($entry.credits.codexCreditLimit? | select(.limit? > 0)
           | "\(entry_name($entry)) \(.title // "Monthly credit limit"): \(money(.used; "USD")) / \(money(.limit; "USD"))"),
          ($entry.openaiDashboard.creditsRemaining? | select(type == "number")
           | "\(entry_name($entry)) dashboard credits: \(money(.; "USD")) remaining"),
          ($entry.openaiDashboard.codexCreditLimit? | select(.limit? > 0)
           | "\(entry_name($entry)) \(.title // "Monthly credit limit"): \(money(.used; "USD")) / \(money(.limit; "USD"))")
        ];

    def reset_credit_line(entry):
        entry as $entry
        | $entry.usage.codexResetCredits as $rc
        | if $rc == null then empty
          else ($rc.availableCount // ([$rc.credits[]? | select(.status == "available")] | length)) as $count
          | ([$rc.credits[]? | select(.status == "available" and .expires_at != null)] | sort_by(.expires_at) | .[0].expires_at) as $next
          | if $next != null then
                (try ($next | fromdateiso8601) catch null) as $ts
                | if $ts == null then "\(entry_name($entry)) reset credits: \($count) available"
                  else "\(entry_name($entry)) reset credits: \($count) available; next expires \($ts | fmt_local_ts)" end
            elif $count > 0 then "\(entry_name($entry)) reset credits: \($count) available; no expiry"
            else "\(entry_name($entry)) reset credits: none available" end
          end;

    def cost_lines(entry):
        entry as $entry
        |
        [
          ($entry.usage.providerCost? | select(.used? != null)
           | "\(entry_name($entry)) \(.period // "budget"): \(money(.used; .currencyCode // "USD"))"
             + (if .limit? > 0 then " / \(money(.limit; .currencyCode // "USD"))" else "" end)),
          ($entry.usage.openRouterUsage.balance? | select(type == "number")
           | "\(entry_name($entry)) balance: \(money(.; "USD"))"),
          ($entry.usage.sakanaPayAsYouGo.creditBalance? | select(type == "number")
           | "\(entry_name($entry)) pay-as-you-go balance: \(money(.; "USD"))"),
          ($entry.antigravityPlanInfo? | (.planDisplayName // .displayName // .planShortName // .planName) as $plan
           | select($plan != null) | "\(entry_name($entry)) plan: \($plan)")
        ];

    def provider_lines(entry):
        entry as $entry
        | if $entry.error then
            "\(provider_name($entry.provider)): error — \($entry.error.message)"
        else
            (entry_name($entry)) as $name
            | [
                status_line($entry),
                fmt_window($entry.usage.primary;   "\($name) primary"),
                fmt_window($entry.usage.secondary; "\($name) secondary"),
                fmt_window($entry.usage.tertiary;  "\($name) tertiary"),
                ($entry.usage.extraRateWindows[]? | fmt_named_window(.; $name)),
                reset_credit_line($entry)
            ]
            + pace_lines($entry)
            + credits_lines($entry)
            + cost_lines($entry)
            | map(select(. != null and . != "")) | join("\n")
        end;

    def max_pct(entry):
        if entry.error then 0
        else
            [entry.usage.primary.usedPercent,
             entry.usage.secondary.usedPercent,
             entry.usage.tertiary.usedPercent]
            | map(select(. != null)) | (max // 0)
        end;

    def pct_or_null(w):
        if w == null or w.usedPercent == null then null
        else (w.usedPercent | floor) end;

    # When the user has pinned a provider for the bar text, surface session
    # and weekly inline ("3% • 12%"). Otherwise emit the global max%.
    def bar_text(entry):
        if entry == null or entry.error then "🤖 ⚠"
        else
            [pct_or_null(entry.usage.primary),
             pct_or_null(entry.usage.secondary)]
            | map(select(. != null))
            | if length == 0 then "🤖 —"
              elif length == 1 then "🤖 \(.[0])%"
              else "🤖 \(.[0])% • \(.[1])%" end
        end;

    . as $all
    | ($all | map(max_pct(.)) | max // 0) as $pct
    | ($all | map(provider_lines(.)) | map(select(. != ""))) as $lines
    | ($all | all(.error)) as $all_errored
    | ($all | any(.stale == true)) as $any_stale
    | (if $bar_provider == "" then null
       else ($all | map(select(.provider == $bar_provider)) | .[0]) end) as $pinned
    | {
        text: (if $pinned != null then bar_text($pinned)
               elif $all_errored then "🤖 ⚠"
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
