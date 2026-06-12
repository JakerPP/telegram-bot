#!/usr/bin/env bash
set -euo pipefail

BASE_URL="https://api.housecallpro.com"
TOKEN_FILE="/opt/hcp/.hcp_token"
TELEGRAM_ENV="/opt/hcp/.telegram.env"

PHONE=""
CUSTOMER_ID=""
JOB_ID=""
DRY_RUN=0
SLEEP_BETWEEN_REQUESTS=4
JOB_HAD_LINE_ITEMS_BEFORE=0
ITEMS=()

TAG_LINE_ITEMS_ADDED="tag_e233b08e115f4cf5a63864ac0657fe8a"      # Line items added
TAG_PLEASE_CHECK_LINE_ITEMS="tag_026da0eb8aac443586b6a03d735b0a70" # Please check Line items

if [[ -f "$TOKEN_FILE" ]]; then
  HCP_API_TOKEN="$(cat "$TOKEN_FILE" | tr -d '\n\r ')"
else
  echo "Missing HCP token file: $TOKEN_FILE" >&2
  exit 1
fi

if [[ -f "$TELEGRAM_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$TELEGRAM_ENV"
fi

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2; }
die() { echo "ERROR: $*" >&2; telegram_notify "❌ HCP job line items error: $*"; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

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
  /opt/hcp/add_line_items.sh --phone "4165256968" \
    --item "Part name|materials|123.45|1|Description without part number|PART-NUMBER" \
    --default-labor "Labour for replacement, testing and verification"

Options:
  --phone PHONE
  --customer-id CUSTOMER_ID
  --job-id JOB_ID
  --dry-run
  --sleep SECONDS
  --jiffy-first
  --jiffy-second
  --default-labor "Description"
  --item "name|kind|price|qty|description|part_number"

Rules:
  - Adds job invoice line items only: POST /jobs/{JOB_ID}/line_items.
  - Use materials, not material.
  - Part number goes in separate part_number field, not in description.
  - Jiffy first visit = labor $150.
  - Jiffy second visit = labor $100.
  - Non-Jiffy default labor = labor $245.
  - Job tags:
      Line items added
      Please check Line items only if job already had subtotal/line items before this script.
EOF
}

normalize_phone() {
  local digits
  digits="$(printf '%s' "${1:-}" | tr -cd '0-9')"
  if [[ ${#digits} -eq 11 && "${digits:0:1}" == "1" ]]; then digits="${digits:1}"; fi
  printf '%s' "$digits"
}

urlencode() { jq -rn --arg v "$1" '$v|@uri'; }

api_request() {
  local method="$1" path="$2" data_file="${3:-}" url="${BASE_URL}${path}"
  local attempt=1 max_attempts=8 tmp_body tmp_headers http_code reset sleep_for now
  tmp_body="$(mktemp)"; tmp_headers="$(mktemp)"

  while true; do
    : > "$tmp_body"; : > "$tmp_headers"
    if [[ -n "$data_file" ]]; then
      http_code="$(curl -sS -X "$method" "$url" -D "$tmp_headers" -o "$tmp_body" \
        -H "Authorization: Bearer ${HCP_API_TOKEN}" -H "Accept: application/json" -H "Content-Type: application/json" \
        --data-binary @"$data_file" -w '%{http_code}')" || http_code="000"
    else
      http_code="$(curl -sS -X "$method" "$url" -D "$tmp_headers" -o "$tmp_body" \
        -H "Authorization: Bearer ${HCP_API_TOKEN}" -H "Accept: application/json" -H "Content-Type: application/json" \
        -w '%{http_code}')" || http_code="000"
    fi

    if [[ "$http_code" == "200" || "$http_code" == "201" || "$http_code" == "204" ]]; then
      cat "$tmp_body"; rm -f "$tmp_body" "$tmp_headers"; return 0
    fi

    if [[ "$http_code" == "429" ]]; then
      reset="$(awk 'BEGIN{IGNORECASE=1} /^ratelimit-reset:/ {gsub("\r","",$2); print $2; exit}' "$tmp_headers")"
      if [[ "$reset" =~ ^[0-9]+$ ]]; then now="$(date +%s)"; sleep_for=$((reset - now + 1)); else sleep_for=$((attempt * 4)); fi
      (( sleep_for < 4 )) && sleep_for=4; (( sleep_for > 35 )) && sleep_for=35
      (( attempt >= max_attempts )) && { cat "$tmp_body" >&2; rm -f "$tmp_body" "$tmp_headers"; return 1; }
      log "HTTP 429 rate limit. Sleeping ${sleep_for}s, retry ${attempt}/${max_attempts}..."
      sleep "$sleep_for"; attempt=$((attempt + 1)); continue
    fi

    if [[ "$http_code" =~ ^5 ]] && (( attempt < max_attempts )); then
      sleep_for=$((attempt * 4)); (( sleep_for > 35 )) && sleep_for=35
      log "HTTP $http_code. Sleeping ${sleep_for}s, retry ${attempt}/${max_attempts}..."
      sleep "$sleep_for"; attempt=$((attempt + 1)); continue
    fi

    echo "HTTP $http_code failed for $method $path" >&2; cat "$tmp_body" >&2
    rm -f "$tmp_body" "$tmp_headers"; return 1
  done
}

find_customer_by_phone() {
  local p q json
  p="$(normalize_phone "$PHONE")"; [[ -n "$p" ]] || die "Invalid phone"
  q="$(urlencode "$p")"
  log "Searching customer by phone: $p"
  json="$(api_request GET "/customers?q=${q}&page_size=100")"
  CUSTOMER_ID="$(printf '%s' "$json" | jq -r --arg p "$p" '
    def normphone: tostring|gsub("[^0-9]";"")|if length==11 and startswith("1") then .[1:] else . end;
    [(.customers//.data//[])[]|select(((.mobile_number//.mobile_phone//.phone_number//.phone//.mobile//.home_number//.work_number//"")|normphone)==$p)][0].id//empty')"
  [[ -n "$CUSTOMER_ID" ]] || die "Customer not found by phone: $PHONE"
  log "Customer found: $CUSTOMER_ID"
}

find_latest_active_job() {
  local json
  [[ -n "$CUSTOMER_ID" ]] || return 0
  log "Searching latest active job..."
  json="$(api_request GET "/jobs?customer_id=${CUSTOMER_ID}&page_size=100")"
  JOB_ID="$(printf '%s' "$json" | jq -r '
    [(.jobs//.data//[])[]|select((.canceled_at==null) and (.deleted_at==null))|select(((.work_status//.status//"")|ascii_downcase)|test("completed|canceled|cancelled")|not)]
    |sort_by(.updated_at//.created_at//"")|reverse|.[0].id//empty')"
  [[ -n "$JOB_ID" ]] || die "No active job found for customer: $CUSTOMER_ID"
  log "Using latest active job: $JOB_ID"
}

check_existing_job_line_items() {
  local json subtotal
  log "Checking existing job line items..."
  json="$(api_request GET "/jobs/${JOB_ID}")"
  subtotal="$(printf '%s' "$json" | jq -r '.subtotal // 0')"
  if [[ "$subtotal" =~ ^[0-9]+$ ]] && (( subtotal > 0 )); then
    JOB_HAD_LINE_ITEMS_BEFORE=1
    log "Job already had subtotal/line items before adding new items. subtotal=${subtotal} cents"
  else
    JOB_HAD_LINE_ITEMS_BEFORE=0
    log "No existing job subtotal/line items detected."
  fi
}

build_one_item_payload() {
  python3 - "$1" <<'PY'
import json, sys
from decimal import Decimal, ROUND_HALF_UP
raw = sys.argv[1]
parts = [p.strip() for p in raw.split("|")]
if len(parts) == 5:
    name, kind, price, qty, description = parts; part_number = ""
elif len(parts) == 6:
    name, kind, price, qty, description, part_number = parts
else:
    raise SystemExit("Bad --item format. Expected: name|kind|price|qty|description|part_number")
kind = kind.lower()
if kind == "material": kind = "materials"
if kind not in {"labor", "materials"}: raise SystemExit("Bad kind. Use labor or materials.")
price_cents = int((Decimal(price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100).quantize(Decimal("1")))
qty_decimal = Decimal(qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
payload = {
  "name": name, "description": description, "service_item_type": "pricebook_material",
  "unit_price": price_cents, "unit_cost": 0, "quantity": float(qty_decimal),
  "kind": kind, "taxable": True
}
if part_number: payload["part_number"] = part_number
print(json.dumps(payload, ensure_ascii=False))
PY
}

add_job_tag() {
  local tag_id="$1" tmp
  if [[ "$DRY_RUN" -eq 1 ]]; then echo "Prepared job tag: $tag_id"; return 0; fi
  tmp="$(mktemp)"; jq -n --arg tag_id "$tag_id" '{tag_id:$tag_id}' > "$tmp"
  log "Adding job tag_id: $tag_id"
  api_request POST "/jobs/${JOB_ID}/tags" "$tmp" | jq . 2>/dev/null || true
  rm -f "$tmp"
  sleep "$SLEEP_BETWEEN_REQUESTS"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phone) PHONE="${2:-}"; shift 2 ;;
    --customer-id) CUSTOMER_ID="${2:-}"; shift 2 ;;
    --job-id) JOB_ID="${2:-}"; shift 2 ;;
    --sleep) SLEEP_BETWEEN_REQUESTS="${2:-4}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --jiffy-first) ITEMS+=("Service Call – Jiffy First Visit|labor|150.00|1|First Jiffy diagnostic/service visit|"); shift ;;
    --jiffy-second) ITEMS+=("Second Jiffy Visit|labor|100.00|1|Second Jiffy visit for installation, testing and verification|"); shift ;;
    --default-labor) ITEMS+=("Labour|labor|245.00|1|${2:-Labour for repair, installation, testing and verification}|"); shift 2 ;;
    --item) ITEMS+=("${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

need_cmd curl; need_cmd jq; need_cmd python3
[[ ${#ITEMS[@]} -gt 0 ]] || die "At least one --item, --jiffy-first, --jiffy-second, or --default-labor is required."
if [[ -z "$CUSTOMER_ID" && -n "$PHONE" ]]; then find_customer_by_phone; fi
if [[ -z "$JOB_ID" && -n "$CUSTOMER_ID" ]]; then find_latest_active_job; fi
[[ -n "$JOB_ID" ]] || die "Provide --job-id or --phone/--customer-id so latest active job can be found."

check_existing_job_line_items

SUCCESS_COUNT=0
for item in "${ITEMS[@]}"; do
  tmp="$(mktemp)"
  build_one_item_payload "$item" | jq . > "$tmp"
  echo; echo "Prepared job line item:"; cat "$tmp"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Dry-run: not adding item."
  else
    log "Adding job line item..."
    api_request POST "/jobs/${JOB_ID}/line_items" "$tmp" | jq . || true
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    sleep "$SLEEP_BETWEEN_REQUESTS"
  fi
  rm -f "$tmp"
done

add_job_tag "$TAG_LINE_ITEMS_ADDED"
if [[ "$JOB_HAD_LINE_ITEMS_BEFORE" -eq 1 ]]; then add_job_tag "$TAG_PLEASE_CHECK_LINE_ITEMS"; fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  telegram_notify "🧪 HCP job line items dry-run OK
Job: ${JOB_ID}
Phone: ${PHONE}"
else
  EXTRA=""
  if [[ "$JOB_HAD_LINE_ITEMS_BEFORE" -eq 1 ]]; then EXTRA="
⚠️ Job already had line items before this script. Please check line items for duplicates or overwritten items."; fi
  telegram_notify "✅ HCP job line items added
Job: ${JOB_ID}
Phone: ${PHONE}
Items added: ${SUCCESS_COUNT}${EXTRA}"
fi

log "Done."
