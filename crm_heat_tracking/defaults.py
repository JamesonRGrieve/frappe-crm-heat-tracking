# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules a new install starts with (pure; no Frappe import). They only use standard CRM Lead fields
and the computed facts below; business-specific rules (services, platforms, high-intent pages) are added
in CRM Heat Settings.

Weights follow common B2B scoring practice: replies, calls, clicks and form submissions carry intent;
email opens are nearly worthless since Apple Mail Privacy Protection opens every email it receives, so
they score 1 point and at most 3 in total."""

from crm_heat_tracking.scoring import ActivityRule, Condition, FitRule, Signal

# Computed facts, available to fit rules next to the lead's own fields.
BUSINESS_EMAIL = "$business_email"
EMAIL_BOUNCED = "$email_bounced"
UNSUBSCRIBED = "$unsubscribed"
COMPUTED_FACTS = (BUSINESS_EMAIL, EMAIL_BOUNCED, UNSUBSCRIBED)

REVENUE_GROUP = "Revenue"
TEAM_GROUP = "Team size"

FIT_RULES = (
	FitRule("Business email address", (BUSINESS_EMAIL,), Condition.IS_SET, 15),
	FitRule("Company named", ("organization",), Condition.IS_SET, 10),
	FitRule("Phone number given", ("mobile_no", "phone"), Condition.IS_SET, 10),
	FitRule("Has a website", ("website",), Condition.IS_SET, 10),
	FitRule("Revenue $1M+ a year", ("annual_revenue",), Condition.AT_LEAST, 20, ("1000000",), REVENUE_GROUP),
	FitRule("Revenue $250K+ a year", ("annual_revenue",), Condition.AT_LEAST, 10, ("250000",), REVENUE_GROUP),
	FitRule(
		"11+ employees",
		("no_of_employees",),
		Condition.EQUALS,
		10,
		("11-50", "51-200", "201-500", "501-1000", "1000+"),
		TEAM_GROUP,
	),
	FitRule("Email bounced", (EMAIL_BOUNCED,), Condition.IS_SET, -30),
	FitRule("Unsubscribed from email", (UNSUBSCRIBED,), Condition.IS_SET, -20),
)

ACTIVITY_RULES = (
	ActivityRule("Submitted a form", Signal.FORM_SUBMISSION, 30),
	ActivityRule("Replied to an email", Signal.EMAIL_REPLY, 25),
	ActivityRule("Called in", Signal.INBOUND_CALL, 20),
	ActivityRule("Took a call", Signal.CONNECTED_OUTBOUND_CALL, 10),
	ActivityRule("Clicked a link in an email", Signal.EMAIL_CLICK, 10, daily_cap=20),
	ActivityRule("Came back to the website", Signal.RETURN_VISIT, 5, daily_cap=5),
	ActivityRule("Viewed a page", Signal.PAGE_VIEW, 1, daily_cap=5),
	ActivityRule("Opened an email", Signal.EMAIL_OPEN, 1, total_cap=3),
)
