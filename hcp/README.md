# HCP Line Items Workflow

Operational scripts and rules for Housecall Pro line items from the VPS.

## Security

Do not commit live tokens.

Secrets must stay on the VPS:

```bash
/opt/hcp/.hcp_token
/opt/hcp/.telegram.env
```

## Install on VPS

```bash
apt update
apt install -y curl jq python3
mkdir -p /opt/hcp
chmod 700 /opt/hcp
```

Create `/opt/hcp/.hcp_token` with the HCP API token only.

Create `/opt/hcp/.telegram.env` like this:

```bash
TELEGRAM_BOT_TOKEN="PASTE_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID="PASTE_TELEGRAM_CHAT_ID_HERE"
```

## Script separation

### Job invoice line items

Use when Jake asks to add **job line items** or invoice/job charges.

Script:

```bash
/opt/hcp/add_line_items.sh
```

Endpoint:

```text
POST /jobs/{JOB_ID}/line_items
```

Tags:

```text
Line items added
Please check Line items — only if the job already had subtotal/line items before the script
```

### Estimate / quote line items

Use when Jake asks to make/update an **estimate for the client**.

Script:

```bash
/opt/hcp/add_estimate_line_items.sh
```

Verified endpoint:

```text
PUT /estimates/{ESTIMATE_ID}/options/{OPTION_ID}/line_items/bulk_update
```

Tags on latest active job:

```text
Estimate is created
Please check Estimate — only if the estimate option already had non-tax line items before the script
Need to provide a quote — if estimate is not found and needs to be created
```

## Price rules

Jiffy jobs:

```text
First visit:  labor $150.00
Second visit: labor $100.00
```

All other jobs:

```text
Default labor: $245.00
```

Explicit labor price in `--item` overrides default labor.

## Item format

Use six fields:

```text
name|kind|price|qty|description|part_number
```

Kinds:

```text
labor
materials
```

Use `materials`, not `material`.

Part number must be separate from description:

```bash
--item "ASKO main control board|materials|643.03|1|Main control board replacement for ASKO dishwasher D5634XXLHS/TH|496852"
```

Do not write the part number inside description.

## Job line items example

```bash
/opt/hcp/add_line_items.sh --phone "CLIENT_PHONE" --sleep 4 \
  --item "Part name|materials|123.45|1|Description without part number|PART-NUMBER" \
  --default-labor "Labour for replacement, testing and verification"
```

Jiffy first visit:

```bash
/opt/hcp/add_line_items.sh --phone "CLIENT_PHONE" --jiffy-first
```

Jiffy second visit:

```bash
/opt/hcp/add_line_items.sh --phone "CLIENT_PHONE" --jiffy-second
```

## Estimate line items example

```bash
/opt/hcp/add_estimate_line_items.sh --phone "CLIENT_PHONE" --sleep 4 \
  --item "Part name|materials|123.45|1|Description without part number|PART-NUMBER" \
  --default-labor "Labour for replacement, testing and verification"
```

## Bash quoting warning

Do not put `$59` inside double quotes unless escaped as `\$59`. Bash can turn `$59` into `9`.

Safer wording:

```text
Shipping fee 59 USD converted to CAD
```

## Verified Boris test

Estimate endpoint verified on Boris estimate:

```text
Estimate ID: csr_a21b5403d3394d30a1339f6b14605b26
Option ID:   est_72616401af1e477ba4eaeb022e13fc07
Endpoint:    PUT /estimates/{estimate_id}/options/{option_id}/line_items/bulk_update
Result:      subtotal $1,110.80, tax/fee $177.73, total $1,288.53
```
