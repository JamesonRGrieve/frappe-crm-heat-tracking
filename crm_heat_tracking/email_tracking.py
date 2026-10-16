# SPDX-License-Identifier: AGPL-3.0-or-later
"""Email click tracking, Frappe side (pure rewriting and scanner detection in ``links``).

Before an email Communication on a lead or deal is saved (and so before it is queued: the sent body is
the Communication's content), its links are rewritten to ``click`` with a random token per link. A click
is recorded, then redirected to the original URL, which only ever comes from the stored link, so the
endpoint cannot be used as an open redirect. Opens are Frappe's own: the Email Account's Track Email
Status pixel sets the Communication's ``read_by_recipient_on``."""

import re
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import get_datetime, get_url, now_datetime
from werkzeug.utils import redirect

from crm_heat_tracking import heat
from crm_heat_tracking.links import is_scanner, rewrite_links

LINK = "CRM Email Link"
CLICK = "CRM Email Click"
CLICK_PATH = "/api/method/crm_heat_tracking.email_tracking.click"
TOKEN_BYTES = 16
TOKEN = re.compile(r"^[0-9a-f]{32}$")
# Generous for a person, tight for a script hammering tokens.
CLICKS_PER_MINUTE = 30
SECONDS_PER_MINUTE = 60
USER_AGENT_MAX = 500
# Temporary: the tracked URL must keep coming back to this endpoint, never be cached as the target.
REDIRECT_STATUS = 302


def track_links(doc, method=None):
	"""Communication before_insert: rewrite the links of an email sent from a lead or deal."""
	if doc.communication_medium != "Email" or doc.sent_or_received != "Sent":
		return
	config = heat.settings()
	if not (config.enabled and config.track_email_clicks):
		return
	lead = heat.lead_for(doc.reference_doctype, doc.reference_name)
	if not lead:
		return
	created: list[str] = []

	def tracked_url(url: str) -> str:
		token = secrets.token_hex(TOKEN_BYTES)
		link = frappe.get_doc({"doctype": LINK, "token": token, "url": url, "lead": lead})
		link.insert(ignore_permissions=True)
		created.append(link.name)
		return get_url(f"{CLICK_PATH}?t={token}")

	doc.content = rewrite_links(doc.content, tracked_url)
	doc.flags.crm_heat_links = created


def attach_links(doc, method=None):
	"""Communication after_insert: the links now know their email (its name is set only on insert)."""
	for name in doc.flags.crm_heat_links or ():
		frappe.db.set_value(LINK, name, "communication", doc.name, update_modified=False)


@frappe.whitelist(allow_guest=True, methods=["GET", "HEAD"])
@rate_limit(limit=CLICKS_PER_MINUTE, seconds=SECONDS_PER_MINUTE)
def click(t: str | None = None):
	link = (
		frappe.db.get_value(
			LINK, {"token": t}, ["name", "url", "lead", "communication", "creation"], as_dict=True
		)
		if t and TOKEN.match(t)
		else None
	)
	if not link:
		frappe.throw(_("This link is not valid."), frappe.DoesNotExistError)
	sent = (
		link.communication and frappe.db.get_value("Communication", link.communication, "creation")
	) or link.creation
	user_agent = (frappe.request.headers.get("User-Agent") or "")[:USER_AGENT_MAX]
	scanner = is_scanner(
		user_agent, frappe.request.method, (now_datetime() - get_datetime(sent)).total_seconds()
	)
	frappe.get_doc(
		{
			"doctype": CLICK,
			"link": link.name,
			"lead": link.lead,
			"url": link.url,
			"likely_scanner": int(scanner),
			"user_agent": user_agent,
		}
	).insert(ignore_permissions=True)
	if not scanner:
		heat.enqueue_lead(link.lead)
	# A GET request is not committed by Frappe; the click must be kept.
	frappe.db.commit()
	return redirect(link.url, code=REDIRECT_STATUS)
