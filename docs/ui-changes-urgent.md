# UI changes required before the next demo (2026-10-29)

Two changes, both labeling and visibility only. No new data, no new routes. The API field-help catalog (`GET /v1/field-help`) already carries the new labels and help text, so a UI that binds labels from the catalog picks them up on refresh; a UI with hardcoded labels must change them to the strings below.

## 1. Remove every reference to "hospital systems"

Reliable hospital-system groupings are not available across the board, so the UI must not present anything as a hospital system or health system.

- **Hide the Hospital affiliations block** on the provider Overview tab and on the group Overview tab. Do not render `hospital_affiliations[]` anywhere. The API still returns the array; ignore it.
- **Do not use the words** "hospital system", "health system" or "parent system" in any label, caption, tooltip, filter name or empty state.
- **Relabel the parent fields** as plain organizations:
  - provider `primary_organization_parent_name`: **Parent organization**
  - provider `primary_organization_parent_id`: **Parent organization id**
  - group `parent_name` / `parent_id`: **Parent organization** / **Parent organization id**
  - group dump filter `parent`: **Parent organization contains**
- The Sites tab is unchanged: it lists addresses, not systems.

## 2. RVU labels (superseded on the rework branch)

On `claude/pd-rework` the RVU calculation prices every billed procedure line with the physician work RVU, so the interim "sum" labels and the "under review" caption no longer apply there: bind every `wrvu_*` label and tooltip from the field-help catalog, which now reads "Work RVU total", "Average work RVU per procedure", "Priced procedures" and so on. The demo branch keeps the interim labels until it is merged.
