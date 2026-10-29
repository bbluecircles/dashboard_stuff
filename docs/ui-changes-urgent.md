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

## 2. Label every RVU figure as exactly what it computes

Until the RVU calculation is reworked, no figure may be presented as "RVU" or "wRVU" on its own. Each label must say that it is a sum, over what, and at what grain. Use these strings verbatim.

| JSON field | Label |
| --- | --- |
| `wrvu_total` | RVU sum (one procedure per visit) |
| `wrvu_average` | RVU per priced visit |
| `wrvu_procedure_count` | Priced visits |
| `wrvu_prior_year_total` | Prior-year RVU sum |
| `wrvu_prior_year_average` | Prior-year RVU per priced visit |
| `wrvu_yoy_change_pct` | RVU sum change vs prior year |
| `wrvu_state_specialty_median` | Specialty median of provider RVU sums |
| `wrvu_state_specialty_p25` | Specialty 25th percentile of provider RVU sums |
| `wrvu_state_specialty_p75` | Specialty 75th percentile of provider RVU sums |
| `wrvu_state_specialty_average` | Specialty mean of provider RVU sums (do not show) |
| `wrvu_specialty_percentile` | RVU sum percentile within specialty |
| `activity_specialty_percentile` | Activity vs specialty peers (visits and RVU sum) |
| practice `wrvu_at_site` | RVU sum at site |
| practice `wrvu_share_pct` | Share of RVU sum at site |
| group `wrvu_total` | Sum of members' RVU sums |
| group `wrvu_average` | RVU per priced visit, members combined |

Add one caption under the RVU numbers on Overview, provider and group: **"RVU figures price one procedure per visit from the fee schedule and are under review."** The developer tooltip already explains the formula.

## Not in this change

Top 5 instead of top 3, the sites rename rules, payer classes 6 and 7, and the referral filtering are being reworked on a separate branch and need data rebuilds. Do not change those on the demo build.
