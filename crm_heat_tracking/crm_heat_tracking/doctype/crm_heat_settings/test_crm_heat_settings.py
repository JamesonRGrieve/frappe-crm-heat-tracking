# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests on a real site (``bench run-tests --app crm_heat_tracking``): install, scoring a
lead from its fields and website visits, email link tracking and clicks, rule validation, and cleanup
when a lead is deleted."""

import secrets
from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from crm_heat_tracking import email_tracking, heat, install

SITE_SOURCE = "Website"


def visitor_id() -> str:
	return secrets.token_hex(16)


class TestCRMHeat(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		install.ensure()
		if not frappe.db.exists("CRM Lead Source", SITE_SOURCE):
			frappe.get_doc({"doctype": "CRM Lead Source", "source_name": SITE_SOURCE}).insert()

	def make_lead(self, **values):
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Heat Test",
				"email": f"{visitor_id()}@acme.example",
				**values,
			}
		)
		lead.insert(ignore_permissions=True)
		return lead

	def test_install_is_idempotent(self):
		rules = len(frappe.get_single(heat.SETTINGS).fit_rules)
		install.ensure()
		self.assertEqual(len(frappe.get_single(heat.SETTINGS).fit_rules), rules)
		self.assertIn(heat.NOTIFICATION_TYPE, frappe.get_meta("CRM Notification").get_options("type"))

	def test_website_lead_with_visits_is_scored(self):
		visitor = visitor_id()
		for path in ("about", "contact"):
			frappe.get_doc({"doctype": "Web Page View", "path": path, "visitor_id": visitor}).insert(
				ignore_permissions=True
			)
		lead = self.make_lead(
			source=SITE_SOURCE, organization="Acme Ltd", mobile_no="780-555-0100", heat_visitor_id=visitor
		)
		self.assertTrue(frappe.db.exists(heat.VISITOR, {"visitor_id": visitor, "lead": lead.name}))
		heat.update_lead(lead.name)
		lead.reload()
		# Business email 15 + company 10 + phone 10; form 30 + two page views.
		self.assertEqual((lead.heat_fit, lead.heat_engagement), (35, 32))
		self.assertEqual(lead.heat, "Warm")
		self.assertIn("Submitted a form", lead.heat_reasons)

	def test_ignores_a_malformed_visitor_id(self):
		lead = self.make_lead(heat_visitor_id="not-a-visitor")
		self.assertFalse(frappe.db.exists(heat.VISITOR, {"lead": lead.name}))

	def test_sent_email_links_are_tracked_and_clicks_count(self):
		lead = self.make_lead()
		comm = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": "Your quote",
				"content": '<p><a href="https://omg.example/quote">Quote</a> <a href="mailto:x@y.example">mail</a></p>',
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name,
			}
		).insert(ignore_permissions=True)
		link = frappe.get_last_doc(email_tracking.LINK, filters={"lead": lead.name})
		self.assertEqual(link.url, "https://omg.example/quote")
		self.assertEqual(link.communication, comm.name)
		self.assertIn(f"{email_tracking.CLICK_PATH}?t={link.token}", comm.content)
		self.assertIn("mailto:x@y.example", comm.content)
		frappe.get_doc(
			{"doctype": email_tracking.CLICK, "link": link.name, "lead": lead.name, "url": link.url}
		).insert(ignore_permissions=True)
		heat.update_lead(lead.name)
		self.assertIn(
			"Clicked a link in an email", frappe.db.get_value("CRM Lead", lead.name, "heat_reasons")
		)

	def test_received_email_links_are_left_alone(self):
		lead = self.make_lead()
		content = '<a href="https://omg.example/x">x</a>'
		comm = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"subject": "Re: quote",
				"content": content,
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(comm.content, content)

	def test_scores_decay_in_the_daily_pass(self):
		lead = self.make_lead(source=SITE_SOURCE)
		frappe.db.set_value("CRM Lead", lead.name, "creation", now_datetime() - timedelta(days=60))
		heat.update_all_leads()
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "heat_engagement"), 8)

	def test_rejects_unknown_fields_and_misplaced_patterns(self):
		settings = frappe.get_single(heat.SETTINGS)
		settings.append(
			"fit_rules", {"label": "x", "fields": "no_such_field", "condition": "Is Set", "points": 1}
		)
		self.assertRaises(frappe.ValidationError, settings.save)
		settings.reload()
		settings.append(
			"activity_rules", {"label": "x", "signal": "Email Click", "points": 1, "path_patterns": "contact"}
		)
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_deleting_a_lead_removes_its_tracking(self):
		visitor = visitor_id()
		lead = self.make_lead(heat_visitor_id=visitor)
		frappe.delete_doc("CRM Lead", lead.name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists(heat.VISITOR, {"visitor_id": visitor}))
