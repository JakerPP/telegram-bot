# HCP Estimate Line Items

Helper script for adding line items to Housecall Pro estimates from a VPS.

## Security

Do not commit live tokens.

Secrets must stay on the VPS:

```bash
/opt/hcp/.hcp_token
/opt/hcp/.telegram.env
```

## Install

```bash
apt update
apt install -y curl jq
mkdir -p /opt/hcp
chmod 700 /opt/hcp
```

Create `/opt/hcp/.hcp_token` with the HCP API token only.

Create `/opt/hcp/.telegram.env` like this:

```bash
TELEGRAM_BOT_TOKEN="PASTE_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID="PASTE_TELEGRAM_CHAT_ID_HERE"
```

Copy the script:

```bash
cp hcp/add_estimate_line_items.sh /opt/hcp/add_estimate_line_items.sh
chmod +x /opt/hcp/add_estimate_line_items.sh
```

## Smoke test

```bash
TOKEN="$(cat /opt/hcp/.hcp_token | tr -d '\n\r ')"
curl -i -s "https://api.housecallpro.com/customers?q=PHONE&page_size=10" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json"
```

## Dry run

```bash
/opt/hcp/add_estimate_line_items.sh --phone "PHONE" --force-replace --dry-run \
  --item "Part name|materials|100.00|1|Description" \
  --item "Labor name|labor|200.00|1|Description"
```

## Real run

Remove `--dry-run`.

## Important Bash quoting warning

Do not put `$59` inside double quotes unless escaped as `\$59`. Bash can turn `$59` into `9`.

Safer wording:

```bash
Shipping fee 59 USD converted to CAD
```

## Line item kind rule

Use `materials`, not `material`.
