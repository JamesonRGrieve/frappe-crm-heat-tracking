# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install and migrate: CRM Lead's heat fields, a Lead Heat section in the CRM app's lead side panel,
the Lead Heat notification type, and the default rules. Every step is idempotent and leaves changes
made in the ERP alone: rules are seeded only into empty tables, and the panel section only when missing."""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from crm_heat_tracking import defaults
from crm_heat_tracking.heat import LEAD, NOTIFICATION_TYPE, SETTINGS
from crm_heat_tracking.scoring import Heat

SIDE_PANEL = "CRM Lead-Side Panel"
PANEL_SECTION = "crm_heat_section"
SHOWN_FIELDS = ("heat", "heat_score", "heat_fit", "heat_engagement", "heat_reasons", "heat_updated_on")

CUSTOM_FIELDS = {
	LEAD: [
		{
			"fieldname": "crm_heat_section",
			"fieldtype": "Section Break",
			"label": "Lead Heat",
			"insert_after": "lost_notes",
			"collapsible": 1,
		},
		{
			"fieldname": "heat",
			"fieldtype": "Select",
			"label": "Heat",
			"options": "\n".join(["", *(h.value for h in Heat)]),
			"insert_after": "crm_heat_section",
			"read_only": 1,
			"in_standard_filter": 1,
			"in_list_view": 1,
		},
		{
			"fieldname": "heat_score",
			"fieldtype": "Int",
			"label": "Heat Score",
			"insert_after": "heat",
			"read_only": 1,
		},
		{
			"fieldname": "heat_fit",
			"fieldtype": "Int",
			"label": "Fit",
			"insert_after": "heat_score",
			"read_only": 1,
		},
		{
			"fieldname": "heat_engagement",
			"fieldtype": "Int",
			"label": "Engagement",
			"insert_after": "heat_fit",
			"read_only": 1,
		},
		{"fieldname": "crm_heat_column", "fieldtype": "Column Break", "insert_after": "heat_engagement"},
		{
			"fieldname": "heat_reasons",
			"fieldtype": "Small Text",
			"label": "Why",
			"insert_after": "crm_heat_column",
			"read_only": 1,
		},
		{
			"fieldname": "heat_updated_on",
			"fieldtype": "Datetime",
			"label": "Heat Updated On",
			"insert_after": "heat_reasons",
			"read_only": 1,
		},
		{
			"fieldname": "heat_visitor_id",
			"fieldtype": "Data",
			"label": "Website Visitor ID",
			"insert_after": "heat_updated_on",
			"hidden": 1,
			"description": "Set by the website form: the visitor's anonymous ID, linking their page views to this lead.",
		},
	]
}


def after_install():
	ensure()


def after_migrate():
	ensure()


def ensure():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	ensure_panel_section()
	ensure_notification_type()
	ensure_settings()


def ensure_panel_section():
	if not frappe.db.exists("CRM Fields Layout", SIDE_PANEL):
		return
	layout = frappe.get_doc("CRM Fields Layout", SIDE_PANEL)
	sections = json.loads(layout.layout or "[]")
	if any(section.get("name") == PANEL_SECTION for section in sections):
		return
	heat_section = {
		"label": "Lead Heat",
		"name": PANEL_SECTION,
		"opened": True,
		"columns": [{"name": "crm_heat_column", "fields": list(SHOWN_FIELDS)}],
	}
	layout.layout = json.dumps([heat_section, *sections])
	layout.save(ignore_permissions=True)


def ensure_notification_type():
	options = (frappe.get_meta("CRM Notification").get_options("type") or "").split("\n")
	if NOTIFICATION_TYPE not in options:
		make_property_setter(
			"CRM Notification",
			"type",
			"options",
			"\n".join([*options, NOTIFICATION_TYPE]),
			"Text",
			validate_fields_for_doctype=False,
		)


def ensure_settings():
	"""Fill any setting that was never saved with its default, and seed the rules into empty tables."""
	settings = frappe.get_single(SETTINGS)
	unset = [
		df for df in settings.meta.fields if df.default is not None and settings.get(df.fieldname) is None
	]
	for df in unset:
		settings.set(df.fieldname, df.default)
	seed = not (settings.fit_rules or settings.activity_rules)
	if seed:
		add_default_rules(settings)
	if unset or seed:
		settings.save(ignore_permissions=True)


def add_default_rules(settings):
	for rule in defaults.FIT_RULES:
		settings.append(
			"fit_rules",
			{
				"label": rule.label,
				"fields": ", ".join(rule.fields),
				"condition": rule.condition.value,
				"match_values": "\n".join(rule.values),
				"points": rule.points,
				"rule_group": rule.group,
			},
		)
	for rule in defaults.ACTIVITY_RULES:
		settings.append(
			"activity_rules",
			{
				"label": rule.label,
				"signal": rule.signal.value,
				"points": rule.points,
				"path_patterns": "\n".join(rule.path_patterns),
				"daily_cap": rule.daily_cap,
				"total_cap": rule.total_cap,
			},
		)
