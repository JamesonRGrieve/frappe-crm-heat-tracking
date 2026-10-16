# SPDX-License-Identifier: AGPL-3.0-or-later
from . import __version__ as _version

app_name = "crm_heat_tracking"
app_title = "CRM Heat Tracking"
app_publisher = "Zephyrex Technologies Limited"
app_description = "Lead heat for Frappe CRM: fit and engagement scores, Hot/Warm/Cold, website visits and email click tracking"
app_email = "jameson@zephyrex.ca"
app_license = "AGPL-3.0-or-later"
app_version = _version

required_apps = ["frappe", "crm"]

# Lead fields, the CRM side-panel section, the notification type and default rules (idempotent).
after_install = "crm_heat_tracking.install.after_install"
after_migrate = ["crm_heat_tracking.install.after_migrate"]

doc_events = {
	"CRM Lead": {
		"after_insert": "crm_heat_tracking.heat.on_lead_insert",
		"on_update": "crm_heat_tracking.heat.on_lead_update",
		"on_trash": "crm_heat_tracking.heat.on_lead_trash",
	},
	"Communication": {
		# Before insert, so the queued email already carries the tracked links.
		"before_insert": "crm_heat_tracking.email_tracking.track_links",
		"after_insert": "crm_heat_tracking.email_tracking.attach_links",
		"on_update": "crm_heat_tracking.heat.on_communication_update",
	},
	"CRM Call Log": {"on_update": "crm_heat_tracking.heat.on_call_log_update"},
	"Web Page View": {"after_insert": "crm_heat_tracking.heat.on_page_view_insert"},
}

# Engagement decays with time, so every open lead is rescored daily even when nothing happens. Email
# opens and bounces are written by Frappe without document events, so they are picked up here too.
scheduler_events = {"daily_long": ["crm_heat_tracking.heat.update_all_leads"]}
