#!/usr/bin/env bash
# Claude Code statusLine command (bash — works around the Windows PowerShell spawn bug in CC >= 2.1.68)
# Displays:  model | <used>/<total> [gradient bar] | 5h <pct>% <reset-countdown> | 7d <pct>%
#   model = model.display_name
#   ctx   = context_window.total_input_tokens / context_window.context_window_size
#           bar fills by context_window.used_percentage, colored green->yellow->red
#   5h    = rate_limits.five_hour.used_percentage  + countdown to .resets_at
#   7d    = rate_limits.seven_day.used_percentage
# context_window/rate_limits populate after the first API response (rate_limits = Pro/Max only).
# No jq dependency: targeted grep/sed on the status JSON.

raw="$(cat)"
flat="$(printf '%s' "$raw" | tr -d '\r\n')"

# String value by unique key.
jstr() {
    printf '%s' "$flat" \
        | grep -oP "\"$1\"\\s*:\\s*\"\\K(\\\\.|[^\"\\\\])*" \
        | head -1 | sed -e 's/\\\\/\\/g' -e 's/\\\//\//g'
}
# Number value by unique key.
jnum() {
    printf '%s' "$flat" | grep -oP "\"$1\"\\s*:\\s*\\K-?[0-9]+(\\.[0-9]+)?" | head -1
}
# Number value of an inner key scoped inside a named object. $1=object, $2=inner key.
jnested() {
    printf '%s' "$flat" \
        | grep -oP "\"$1\"\\s*:\\s*\\{[^}]*?\"$2\"\\s*:\\s*\\K-?[0-9]+(\\.[0-9]+)?" \
        | head -1
}

# Format a token count: 1234567 -> 1.2M, 780000 -> 780k, 512 -> 512
fmt_tok() {
    awk -v n="$1" 'BEGIN{
        if (n>=1000000){ v=n/1000000; if (v==int(v)) printf "%dM", v; else printf "%.1fM", v }
        else if (n>=1000){ printf "%dk", int(n/1000+0.5) }
        else { printf "%d", n }
    }'
}

# Countdown from an epoch-seconds reset time: -> "1h47m" or "12m"
fmt_eta() {
    local now rem h m
    now=$(date +%s); rem=$(( $1 - now )); (( rem < 0 )) && rem=0
    h=$(( rem/3600 )); m=$(( (rem%3600)/60 ))
    if (( h > 0 )); then printf '%dh%02dm' "$h" "$m"; else printf '%dm' "$m"; fi
}

ESC=$'\033'; RESET="${ESC}[0m"
GREEN="${ESC}[0;32m"; YELLOW="${ESC}[0;33m"; RED="${ESC}[0;31m"
CYAN="${ESC}[0;36m"; MAGENTA="${ESC}[0;35m"; DIM="${ESC}[0;90m"

# Gradient bar: width cells, left=green .. mid=yellow .. right=red; fill by percent used.
make_bar() {
    local pct="$1" width=10 i filled out=""
    filled=$(awk -v p="$pct" -v w="$width" 'BEGIN{f=int(p/100*w+0.5); if(f>w)f=w; if(f<0)f=0; print f}')
    for ((i=0; i<width; i++)); do
        local zone
        if   (( i < 4 )); then zone="$GREEN"
        elif (( i < 7 )); then zone="$YELLOW"
        else                   zone="$RED"; fi
        if (( i < filled )); then out="${out}${zone}█"; else out="${out}${DIM}░"; fi
    done
    printf '%s%s' "$out" "$RESET"
}

# --- Gather fields ---
model="$(jstr display_name)"
# effort.level lives inside the "effort" object; scope the match so a future top-level "level" can't shadow it.
effort="$(printf '%s' "$flat" | grep -oP '"effort"\s*:\s*\{[^}]*?"level"\s*:\s*"\K[^"]+' | head -1)"
remaining="$(jnum remaining_percentage)"
in_tok="$(jnum total_input_tokens)"
cw_size="$(jnum context_window_size)"
u5h="$(jnested five_hour used_percentage)"
r5h="$(jnested five_hour resets_at)"
u7d="$(jnested seven_day used_percentage)"

# --- Build segments ---
parts=()
if [ -n "$model" ]; then
    seg="${GREEN}${model}${RESET}"
    if [ -n "$effort" ]; then
        # Prettify to the project's ladder wording, and color-code by reasoning depth.
        case "$effort" in
            low)    eff_txt="low";    eff_col="$DIM"    ;;
            medium) eff_txt="medium"; eff_col="$DIM"    ;;
            high)   eff_txt="high";   eff_col="$CYAN"   ;;
            xhigh)  eff_txt="xHigh";  eff_col="$YELLOW" ;;
            max)    eff_txt="Max";    eff_col="$RED"    ;;
            *)      eff_txt="$effort"; eff_col="$CYAN"  ;;
        esac
        seg="${seg} ${DIM}·${RESET} ${eff_col}${eff_txt}${RESET}"
    fi
    parts+=("$seg")
fi

if [ -n "$remaining" ] && [ -n "$cw_size" ]; then
    used_pct=$(awk -v r="$remaining" 'BEGIN{printf "%.0f", 100-r}')
    ctx_txt="${MAGENTA}$(fmt_tok "${in_tok:-0}")/$(fmt_tok "$cw_size")${RESET}"
    parts+=("${ctx_txt} $(make_bar "$used_pct")")
fi

if [ -n "$u5h" ]; then
    seg="${CYAN}5h $(awk -v v="$u5h" 'BEGIN{printf "%.0f", v}')%${RESET}"
    [ -n "$r5h" ] && seg="${seg} ${DIM}$(fmt_eta "$r5h")${RESET}"
    parts+=("$seg")
fi

[ -n "$u7d" ] && parts+=("${YELLOW}7d $(awk -v v="$u7d" 'BEGIN{printf "%.0f", v}')%${RESET}")

# Join with " | "
out=""
for p in "${parts[@]}"; do
    if [ -z "$out" ]; then out="$p"; else out="$out | $p"; fi
done
printf '%s\n' "$out"
