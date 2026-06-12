#!/usr/bin/env bash
set -euo pipefail

# Final working endpoint verified on 2026-06-12:
#   PUT /estimates/{ESTIMATE_ID}/options/{OPTION_ID}/line_items/bulk_update
#
# This wrapper intentionally refuses to run until the full production script is copied
# from /opt/hcp/add_estimate_line_items.sh on the VPS.
#
# Operational rules:
# - Use this script only when creating/updating estimate line items for a client quote.
# - Do not use job line item endpoints here.
# - Part number belongs in a separate part_number field, not in description.
# - Jiffy first visit labor: 150.00
# - Jiffy second visit labor: 100.00
# - Default non-Jiffy labor: 245.00
# - Tags:
#   - Estimate is created
#   - Please check Estimate only if estimate already had non-tax line items.
# - If estimate is missing, tag latest active job as Need to provide a quote.

echo "Use the production version on VPS: /opt/hcp/add_estimate_line_items.sh" >&2
echo "Verified endpoint: PUT /estimates/{ESTIMATE_ID}/options/{OPTION_ID}/line_items/bulk_update" >&2
exit 1
