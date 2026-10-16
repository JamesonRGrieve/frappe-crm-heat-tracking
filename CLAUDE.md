# frappe-crm-heat-tracking — Agent Operating Guide

AGPL-3.0-or-later Frappe v16 app on top of Frappe CRM (`crm`). Scores CRM Leads: Fit, Engagement,
Heat (Hot/Warm/Cold) and the reasons. No fork of Frappe or CRM: document events, a scheduler job and
one guest endpoint.

## Model (design follows HubSpot / Salesforce Account Engagement / Zoho)

- **Two dimensions, never one number.** Fit = who the lead is (lead fields; no decay). Engagement = what
  the lead does (activities; exponential decay with `half_life_days`, default 30). Heat is a matrix:
  Hot = engagement >= `hot_engagement` AND fit >= `hot_fit`; Warm = engagement >= `warm_engagement` OR
  fit >= `warm_fit`; else Cold. `heat_score` = fit x `fit_weight` + engagement x the rest (for sorting).
- **Rules are data** (CRM Heat Settings tables). Fit rules: lead field(s) + condition (Is Set, Equals,
  Contains Any, At Least) + points; a `rule_group` gives only its best match. Computed facts:
  `$business_email`, `$email_bounced`, `$unsubscribed`. Activity rules: signal + points + per-day and
  total caps; Page View rules may carry path patterns (shell-style), which win over the plain Page View
  rule. Defaults (`defaults.py`) use only standard lead fields; business-specific rules are configuration.
- **Email opens score 1, capped at 3** (Apple Mail Privacy Protection fakes opens). Replies, calls,
  clicks and form submissions carry the weight.
- **Explainable.** `HeatResult.summary()` is stored in `heat_reasons` and shown in the CRM side panel.
- Scores are always **recomputed from source records**, never incremented, so a rule change or a
  deleted record corrects itself on the next pass.

## Code

- `scoring.py`, `defaults.py`, `links.py`: pure, no Frappe import, unit-tested in `tests/`.
- `heat.py`: gathers facts and activities, writes the lead's heat with `db_set(update_modified=False)`,
  doc-event handlers (enqueue a deduplicated `short` job per lead), daily `update_all_leads` (decay; also
  picks up opens and bounces, which Frappe writes without document events), Hot notification
  (CRM Notification type `Lead Heat`, added by a Property Setter).
- `email_tracking.py`: Communication `before_insert` rewrites links of a Sent email on a lead/deal (the
  queued body is the Communication's content), `after_insert` attaches the links to it; the guest
  `click` endpoint records a CRM Email Click and 302s to the stored URL (no open redirect).
- `install.py` (after_install + after_migrate, idempotent): lead custom fields, the side-panel section
  (only if missing), the notification type, settings defaults and default rules (only into empty tables).

## Activity sources

Form submission = lead created with a source listed in `website_lead_sources`. Page views = Web Page
Views whose `visitor_id` is linked to the lead by a CRM Heat Visitor (created from `heat_visitor_id`, a
32-hex ID, when the lead is inserted). Emails = Communications on the lead or its deals. Calls = CRM Call
Logs on the lead or its deals (Incoming, or Outgoing Completed >= 60 s).

## Testing

Pure logic: `uv run --no-project --python 3.14 --with pytest python -I -m pytest`. Frappe-backed:
`bench --site <site> run-tests --app crm_heat_tracking` (real DB). Lint:
`uvx ruff format --check . && uvx ruff check .`
