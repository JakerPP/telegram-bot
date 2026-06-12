#!/usr/bin/env bash
set -euo pipefail

BASE_URL="https://api.housecallpro.com"
TOKEN_FILE="/opt/hcp/.hcp_token"
TELEGRAM_ENV="/opt/hcp/.telegram.env"

PHONE=""
ESTIMATE_ID=""
OPTION_ID=""
FORCE_REPLACE=0
DRY_RUN=0
ITEMS=()

if [[ -f "$TOKEN_FILE" ]]; then
  HCP_API_TOKEN="$(cat "$TOKEN_FILE" | tr -d '\n\r ')"
else
  echo "❌ Не найден HCP token file: $TOKEN_FILE"
  exit 1
fi

if [[ -f "$TELEGRAM_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$TELEGRAM_ENV"
fi

telegram_notify() {
  local text="$1"
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
    curl -s --get "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=${text}" >/dev/null 2>&1 || true
  fi
}

usage() {
  cat <<'EOF'
Usage:
  /opt/hcp/add_estimate_line_items.sh --phone "4165256968" --force-replace \
    --item "Name|materials|736.00|1|Description" \
    --item "Labor name|labor|245.00|1|Description"

Options:
  --phone PHONE
  --estimate-id ID
  --option-id ID
  --force-replace
  --dry-run
  --item "name|kind|price|qty|description"

Kinds:
  labor
  materials

Security:
  Keep tokens in /opt/hcp/.hcp_token and /opt/hcp/.telegram.env.
  Do not commit live tokens to GitHub.
EOF
}

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2; }
die() { echo "❌ ERROR: $*" >&2; telegram_notify "❌ HCP estimate line items error: $*"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1. Install it first."
}

normalize_phone() {
  local digits
  digits="$(printf '%s' "${1:-}" | tr -cd '0-9')"
  if [[ ${#digits} -eq 11 && "${digits:0:1}" == "1" ]]; then
    digits="${digits:1}"
  fi
  printf '%s' "$digits"
}

urlencode() {
  jq -rn --arg v "$1" '$v|@uri'
}

api_request() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local url="${BASE_URL}${path}"
  local attempt=0
  local max_attempts=5
  local tmp_body tmp_headers http_code retry_after sleep_for

  tmp_body="$(mktemp)"
  tmp_headers="$(mktemp)"

  while true; do
    : > "$tmp_body"
    : > "$tmp_headers"

    if [[ -n "$data" ]]; then
      http_code="$(
        curl -sS \
          -X "$method" "$url" \
          -D "$tmp_headers" \
          -o "$tmp_body" \
          -H "Authorization: Bearer ${HCP_API_TOKEN}" \
          -H "Accept: application/json" \
          -H "Content-Type: application/json" \
          --data "$data" \
          -w '%{http_code}'
      )" || http_code="000"
    else
      http_code="$(
        curl -sS \
          -X "$method" "$url" \
          -D "$tmp_headers" \
          -o "$tmp_body" \
          -H "Authorization: Bearer ${HCP_API_TOKEN}" \
          -H "Accept: application/json" \
          -H "Content-Type: application/json" \
          -w '%{http_code}'
      )" || http_code="000"
    fi

    if [[ "$http_code" =~ ^2 ]]; then
      cat "$tmp_body"
      rm -f "$tmp_body" "$tmp_headers"
      return 0
    fi

    if [[ "$http_code" == "429" || "$http_code" =~ ^5 ]] && (( attempt < max_attempts )); then
      retry_after="$(awk 'BEGIN{IGNORECASE=1} /^Retry-After:/ {gsub("\r","",$2); print $2; exit}' "$tmp_headers")"
      if [[ "$retry_after" =~ ^[0-9]+$ ]]; then
        sleep_for="$retry_after"
      else
        sleep_for=$(( 2 ** attempt ))
        (( sleep_for > 30 )) && sleep_for=30
      fi
      log "HTTP $http_code from HCP. Retrying in ${sleep_for}s..."
      sleep "$sleep_for"
      attempt=$((attempt + 1))
      continue
    fi

    echo "❌ HTTP $http_code failed for $method $path" >&2
    echo "----- RESPONSE -----" >&2
    cat "$tmp_body" >&2
    echo >&2
    rm -f "$tmp_body" "$tmp_headers"
    exit 1
  done
}

extract_array() {
  jq -c '
    if type == "array" then .
    elif .data? and (.data|type=="array") then .data
    elif .customers? and (.customers|type=="array") then .customers
    elif .estimates? and (.estimates|type=="array") then .estimates
    elif .items? and (.items|type=="array") then .items
    elif .results? and (.results|type=="array") then .results
    else []
    end
  '
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phone) PHONE="${2:-}"; shift 2 ;;
    --estimate-id) ESTIMATE_ID="${2:-}"; shift 2 ;;
    --option-id) OPTION_ID="${2:-}"; shift 2 ;;
    --force-replace) FORCE_REPLACE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --item) ITEMS+=("${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

need_cmd curl
need_cmd jq
need_cmd python3

[[ ${#ITEMS[@]} -gt 0 ]] || die "At least one --item is required."
[[ -n "$PHONE" || -n "$ESTIMATE_ID" ]] || die "Provide --phone or --estimate-id."

CUSTOMER_ID=""
CUSTOMER_NAME=""

if [[ -z "$ESTIMATE_ID" ]]; then
  PHONE_NORM="$(normalize_phone "$PHONE")"
  [[ -n "$PHONE_NORM" ]] || die "Invalid phone."

  log "Searching customer by phone: $PHONE_NORM"
  Q="$(urlencode "$PHONE_NORM")"
  CUSTOMERS_JSON="$(api_request GET "/customers?q=${Q}&page_size=100" | extract_array)"

  CUSTOMER_JSON="$(
    printf '%s' "$CUSTOMERS_JSON" | jq -c --arg p "$PHONE_NORM" '
      def normphone:
        tostring | gsub("[^0-9]"; "") |
        if length == 11 and startswith("1") then .[1:] else . end;

      [
        .[] |
        select(
          (
            .mobile_number //
            .mobile_phone //
            .phone_number //
            .phone //
            .mobile //
            .home_number //
            .work_number //
            ""
          | normphone) == $p
          or
          (
            [
              (.phones[]?.number? // empty),
              (.phone_numbers[]?.number? // empty)
            ] | map(normphone) | index($p)
          )
        )
      ][0] // empty
    '
  )"

  if [[ -z "$CUSTOMER_JSON" ]]; then
    echo "Customers returned:"
    printf '%s\n' "$CUSTOMERS_JSON" | jq .
    die "Customer not found by phone: $PHONE"
  fi

  CUSTOMER_ID="$(printf '%s' "$CUSTOMER_JSON" | jq -r '.id // .customer_id // empty')"
  CUSTOMER_NAME="$(printf '%s' "$CUSTOMER_JSON" | jq -r '.name // .full_name // ((.first_name // "") + " " + (.last_name // ""))')"

  [[ -n "$CUSTOMER_ID" ]] || die "Customer found but no customer id in response."

  log "Customer found: ${CUSTOMER_NAME} (${CUSTOMER_ID})"
  log "Searching estimates for customer..."

  ESTIMATES_JSON="$(api_request GET "/estimates?customer_id=${CUSTOMER_ID}&page_size=100" | extract_array)"

  ESTIMATE_JSON="$(
    printf '%s' "$ESTIMATES_JSON" | jq -c '
      map(
        select(
          ((.status // .state // "") | ascii_downcase) as $s |
          ($s | test("cancel|canceled|cancelled|declined|rejected|lost") | not)
        )
      )
      | sort_by(.updated_at // .created_at // .scheduled_start // "")
      | reverse
      | .[0] // empty
    '
  )"

  if [[ -z "$ESTIMATE_JSON" ]]; then
    echo "Estimates returned:"
    printf '%s\n' "$ESTIMATES_JSON" | jq .
    die "No active estimate found for customer: $CUSTOMER_NAME / $PHONE"
  fi

  ESTIMATE_ID="$(printf '%s' "$ESTIMATE_JSON" | jq -r '.id // .estimate_id // empty')"
  [[ -n "$ESTIMATE_ID" ]] || die "Estimate found but no estimate id in response."

  log "Using estimate: $ESTIMATE_ID"
fi

log "Reading estimate details..."
ESTIMATE_DETAILS="$(api_request GET "/estimates/${ESTIMATE_ID}")"

if [[ -z "$OPTION_ID" ]]; then
  OPTION_ID="$(
    printf '%s' "$ESTIMATE_DETAILS" | jq -r '
      .options[0].id //
      .data.options[0].id //
      .estimate.options[0].id //
      .estimate_option_id //
      .option_id //
      empty
    '
  )"
fi

[[ -n "$OPTION_ID" ]] || die "Could not determine estimate option id. Run again with --option-id."

log "Using option: $OPTION_ID"

ITEMS_JSON="$(
  python3 - "${ITEMS[@]}" <<'PY'
import json
import sys
from decimal import Decimal, ROUND_HALF_UP

items = []
for raw in sys.argv[1:]:
    parts = raw.split("|", 4)
    if len(parts) != 5:
        raise SystemExit(f"Bad --item format: {raw!r}. Expected name|kind|price|qty|description")

    name, kind, price, qty, description = [p.strip() for p in parts]
    kind = kind.lower()

    if kind == "material":
        kind = "materials"

    if kind not in {"labor", "materials"}:
        raise SystemExit(f"Bad item kind for {name!r}: {kind!r}. Use labor or materials.")

    price_decimal = Decimal(price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    qty_decimal = Decimal(qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    unit_price_cents = int((price_decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    items.append({
        "name": name,
        "description": description,
        "kind": kind,
        "unit_price": unit_price_cents,
        "unit_price_cents": unit_price_cents,
        "quantity": float(qty_decimal),
    })

print(json.dumps(items, separators=(",", ":")))
PY
)"

PAYLOAD="$(jq -n --argjson items "$ITEMS_JSON" '{line_items: $items}')"

echo
echo "Estimate ID: $ESTIMATE_ID"
echo "Option ID:   $OPTION_ID"
echo "Items:"
printf '%s\n' "$ITEMS_JSON" | jq -r '.[] | "- \(.kind): \(.name) | $" + ((.unit_price_cents/100)|tostring) + " x " + (.quantity|tostring)'
echo
echo "Payload:"
printf '%s\n' "$PAYLOAD" | jq .

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry-run mode. Nothing was changed."
  telegram_notify "🧪 HCP estimate line items dry-run OK
Estimate: ${ESTIMATE_ID}
Option: ${OPTION_ID}
Phone: ${PHONE}"
  exit 0
fi

log "Updating estimate option line items..."

RESPONSE="$(api_request PUT "/estimates/${ESTIMATE_ID}/options/${OPTION_ID}/line_items" "$PAYLOAD")"

echo
echo "HCP response:"
printf '%s\n' "$RESPONSE" | jq . 2>/dev/null || printf '%s\n' "$RESPONSE"

telegram_notify "✅ HCP estimate line items updated
Estimate: ${ESTIMATE_ID}
Option: ${OPTION_ID}
Phone: ${PHONE}"

log "Done."
