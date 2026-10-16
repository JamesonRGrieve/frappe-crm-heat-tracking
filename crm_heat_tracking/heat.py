# SPDX-License-Identifier: AGPL-3.0-or-later
"""Frappe side of lead heat (pure scoring in ``scoring``): gathering a lead's facts and activities,
writing its scores, keeping them current from document events, and the nightly decay pass.

Activities come from:
- the lead itself (created from a website lead source = a form submission),
- Web Page Views from the visitor IDs linked to the lead (``CRM Heat Visitor``, set when the visitor
  submits a form),
- email Communications on the lead or its deals (opens, replies; a bounce is a fit fact),
- CRM Email Clicks on tracked links (mail-scanner clicks excluded),
- CRM Call Logs on the lead or its deals."""

import re

import frappe
from frappe import _
from frappe.utils import escape_html, now_datetime

from crm_heat_tracking import defaults
from crm_heat_tracking.scoring import Activity, Heat, HeatResult, Signal, is_business_email, score_lead

LEAD = "CRM Lead"
DEAL = "CRM Deal"
SETTINGS = "CRM Heat Settings"
VISITOR = "CRM Heat Visitor"
JOB_PREFIX = "crm_heat_tracking::"
# The router's tracker: crypto.randomUUID() without dashes.
VISITOR_ID = re.compile(r"^[0-9a-f]{32}$")
CONNECTED_CALL_SECONDS = 60
NOTIFICATION_TYPE = "Lead Heat"
COMMIT_EVERY = 100


def settings():
	return frappe.get_cached_doc(SETTINGS)


def lead_for(reference_doctype: str | None, reference_name: str | None) -> str | None:
	"""The lead a document on a lead or one of its deals belongs to."""
	if not reference_name:
		return None
	if reference_doctype == LEAD:
		return reference_name
	if reference_doctype == DEAL:
		return frappe.db.get_value(DEAL, reference_name, "lead")
	return None


def enqueue_lead(lead: str | None):
	if lead and settings().enabled:
		frappe.enqueue(
			"crm_heat_tracking.heat.update_lead",
			queue="short",
			job_id=f"{JOB_PREFIX}{lead}",
			deduplicate=True,
			enqueue_after_commit=True,
			lead=lead,
		)


def _references(lead: str) -> list[tuple[str, str]]:
	deals = frappe.get_all(DEAL, filters={"lead": lead}, pluck="name")
	return [(LEAD, lead), *((DEAL, deal) for deal in deals)]


def _emails(references: list[tuple[str, str]]) -> list[dict]:
	return [
		row
		for doctype, name in references
		for row in frappe.get_all(
			"Communication",
			filters={"reference_doctype": doctype, "reference_name": name, "communication_medium": "Email"},
			fields=[
				"sent_or_received",
				"delivery_status",
				"read_by_recipient_on",
				"communication_date",
				"creation",
			],
		)
	]


def facts(lead, emails: list[dict]) -> dict:
	values = lead.as_dict()
	values[defaults.BUSINESS_EMAIL] = is_business_email(lead.email)
	values[defaults.EMAIL_BOUNCED] = any(
		e.sent_or_received == "Sent" and e.delivery_status == "Bounced" for e in emails
	)
	values[defaults.UNSUBSCRIBED] = bool(lead.email) and bool(
		frappe.db.exists("Email Unsubscribe", {"email": lead.email})
	)
	return values


def activities(lead, emails: list[dict], references: list[tuple[str, str]]) -> list[Activity]:
	found: list[Activity] = []
	if lead.source and lead.source in settings().website_sources():
		found.append(Activity(Signal.FORM_SUBMISSION, lead.creation))
	visitors = frappe.get_all(VISITOR, filters={"lead": lead.name}, pluck="visitor_id")
	if visitors:
		found += [
			Activity(Signal.PAGE_VIEW, view.creation, view.path or "")
			for view in frappe.get_all(
				"Web Page View", filters={"visitor_id": ["in", visitors]}, fields=["path", "creation"]
			)
		]
	for e in emails:
		if e.sent_or_received == "Received":
			found.append(Activity(Signal.EMAIL_REPLY, e.communication_date or e.creation))
		elif e.read_by_recipient_on:
			found.append(Activity(Signal.EMAIL_OPEN, e.read_by_recipient_on))
	found += [
		Activity(Signal.EMAIL_CLICK, at)
		for at in frappe.get_all(
			"CRM Email Click", filters={"lead": lead.name, "likely_scanner": 0}, pluck="creation"
		)
	]
	for doctype, name in references:
		for call in frappe.get_all(
			"CRM Call Log",
			filters={"reference_doctype": doctype, "reference_docname": name},
			fields=["type", "status", "duration", "start_time", "creation"],
		):
			at = call.start_time or call.creation
			if call.type == "Incoming":
				found.append(Activity(Signal.INBOUND_CALL, at))
			elif call.status == "Completed" and (call.duration or 0) >= CONNECTED_CALL_SECONDS:
				found.append(Activity(Signal.CONNECTED_OUTBOUND_CALL, at))
	return found


def score(lead) -> HeatResult:
	config = settings()
	references = _references(lead.name)
	emails = _emails(references)
	return score_lead(
		facts(lead, emails),
		activities(lead, emails, references),
		config.fit_rule_set(),
		config.activity_rule_set(),
		config.scoring_settings(),
		now_datetime(),
	)


def update_lead(lead: str):
	"""Recompute one lead's heat and store it without touching its modified time."""
	if not settings().enabled or not frappe.db.exists(LEAD, lead):
		return
	doc = frappe.get_doc(LEAD, lead)
	result = score(doc)
	previous = doc.get("heat")
	doc.db_set(
		{
			"heat": result.heat.value,
			"heat_score": result.score,
			"heat_fit": result.fit,
			"heat_engagement": result.engagement,
			"heat_reasons": result.summary(),
			"heat_updated_on": now_datetime(),
		},
		update_modified=False,
	)
	if result.heat is Heat.HOT and previous != Heat.HOT and doc.lead_owner and settings().notify_owner_on_hot:
		notify_owner(doc, result)


def notify_owner(lead, result: HeatResult):
	"""A notification in the CRM app's bell for the lead owner."""
	text = _("{0} is now Hot (score {1})").format(escape_html(lead.lead_name or lead.name), result.score)
	frappe.get_doc(
		{
			"doctype": "CRM Notification",
			"type": NOTIFICATION_TYPE,
			"from_user": "Administrator",
			"to_user": lead.lead_owner,
			"reference_doctype": LEAD,
			"reference_name": lead.name,
			"notification_type_doctype": LEAD,
			"notification_type_doc": lead.name,
			"notification_text": f'<div class="mb-2 leading-5 text-ink-gray-5">{text}</div>',
			"message": text,
		}
	).insert(ignore_permissions=True)


def update_all_leads():
	"""Daily: every open lead, so engagement decays even when nothing new happens."""
	if not settings().enabled:
		return
	for count, lead in enumerate(frappe.get_all(LEAD, filters={"converted": 0}, pluck="name"), start=1):
		update_lead(lead)
		if count % COMMIT_EVERY == 0:
			# A long job over every lead: keep each batch's writes instead of one transaction.
			frappe.db.commit()


def on_lead_insert(doc, method=None):
	visitor = (doc.get("heat_visitor_id") or "").strip().lower()
	if VISITOR_ID.match(visitor) and not frappe.db.exists(VISITOR, {"visitor_id": visitor, "lead": doc.name}):
		frappe.get_doc({"doctype": VISITOR, "visitor_id": visitor, "lead": doc.name}).insert(
			ignore_permissions=True
		)
	enqueue_lead(doc.name)


def on_lead_update(doc, method=None):
	enqueue_lead(doc.name)


def on_lead_trash(doc, method=None):
	"""Remove the lead's tracking records, which would otherwise block its deletion."""
	for doctype in ("CRM Email Click", "CRM Email Link", VISITOR):
		frappe.db.delete(doctype, {"lead": doc.name})


def on_communication_update(doc, method=None):
	enqueue_lead(lead_for(doc.reference_doctype, doc.reference_name))


def on_call_log_update(doc, method=None):
	enqueue_lead(lead_for(doc.reference_doctype, doc.reference_docname))


def on_page_view_insert(doc, method=None):
	if doc.get("visitor_id"):
		for lead in frappe.get_all(VISITOR, filters={"visitor_id": doc.visitor_id}, pluck="lead"):
			enqueue_lead(lead)
