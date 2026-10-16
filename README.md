# frappe-crm-heat-tracking

Lead heat for [Frappe CRM](https://github.com/frappe/crm): every lead gets a **Fit** score, an
**Engagement** score and a **Hot / Warm / Cold** label, with the reasons behind them, kept current as the
lead visits the website, opens and clicks emails, replies and calls.

- **Fit** (0–100): who the lead is: business email, company, phone, website, revenue, team size, and any
  rule on the lead's own fields. It does not decay.
- **Engagement** (0–100): what the lead does: form submissions, page views and return visits, email
  clicks, replies, opens and calls. Points halve every 30 days, so a lead that goes quiet cools down.
- **Heat**: Hot when engagement and fit are both high; Warm when either is; Cold otherwise.
- **Why**: each lead's side panel lists the rules that scored it.
- **Notifications**: the lead owner gets a CRM notification when a lead turns Hot.

Rules, thresholds and the half-life are edited in **CRM Heat Settings**. Fit rules match any CRM Lead
field, including custom ones, so business-specific signals (services asked about, platforms) are
configuration, not code.

## Tracking

- **Website visits.** With [frappe-public-site-router](https://github.com/JamesonRGrieve/frappe-public-site-router)'s
  view tracking, each browser carries an anonymous visitor ID (none under Do Not Track or Global Privacy
  Control). A visitor's page views are linked to a lead only when they submit a form, which posts the ID
  in the lead's `heat_visitor_id` field.
- **Email clicks.** Links in emails sent from a lead or deal are rewritten to count clicks, then redirect
  to the original URL. Clicks by mail security scanners are recorded but not scored.
- **Email opens.** Frappe's own tracking pixel (Email Account → Track Email Status). Opens score little:
  Apple Mail Privacy Protection opens every email it receives.

## Install

```sh
bench get-app https://github.com/JamesonRGrieve/frappe-crm-heat-tracking
bench --site <site> install-app crm_heat_tracking
```

Requires Frappe CRM. In the CRM app, add the **Heat** column to the leads list, or pick **Heat** as a
kanban view's column field.

## License

AGPL-3.0-or-later
