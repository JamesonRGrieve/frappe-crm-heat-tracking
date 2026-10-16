# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the pure scoring engine (no bench): fit rules and groups, engagement decay and caps,
return visits, page-pattern rules, heat bands and the reasons text."""

from datetime import datetime, timedelta

import pytest

from crm_heat_tracking import defaults
from crm_heat_tracking.scoring import (
	Activity,
	ActivityRule,
	Condition,
	FitRule,
	Heat,
	Settings,
	Signal,
	classify,
	decayed,
	is_business_email,
	rule_for,
	score_engagement,
	score_fit,
	score_lead,
	with_return_visits,
)

NOW = datetime(2026, 10, 16, 12, 0)
SETTINGS = Settings()


def days_ago(days: float) -> datetime:
	return NOW - timedelta(days=days)


class TestBusinessEmail:
	@pytest.mark.parametrize("email", ["chris@obsessedmediagroup.ca", "Owner@Shop.Example.com"])
	def test_company_domains(self, email):
		assert is_business_email(email)

	@pytest.mark.parametrize(
		"email", ["someone@gmail.com", "x@Hotmail.CA", "", None, "no-at-sign", "a@localhost"]
	)
	def test_personal_or_invalid(self, email):
		assert not is_business_email(email)


class TestFitRules:
	def test_is_set_ignores_blank_values(self):
		rule = FitRule("Phone", ("mobile_no", "phone"), Condition.IS_SET, 10)
		assert not rule.matches({"mobile_no": "  ", "phone": None})
		assert rule.matches({"mobile_no": "", "phone": "780-555-0100"})

	def test_equals_is_case_insensitive(self):
		rule = FitRule("Has site", ("omg_has_website",), Condition.EQUALS, 5, ("Yes",))
		assert rule.matches({"omg_has_website": "yes"})
		assert not rule.matches({"omg_has_website": "No"})

	def test_contains_any(self):
		rule = FitRule("ERP", ("omg_interest",), Condition.CONTAINS_ANY, 20, ("ERP", "Program"))
		assert rule.matches({"omg_interest": "Brochure Site Builds, OMG Program"})
		assert not rule.matches({"omg_interest": "Brochure Site Builds"})

	def test_at_least_compares_numbers(self):
		rule = FitRule("Revenue", ("annual_revenue",), Condition.AT_LEAST, 20, ("1000000",))
		assert rule.matches({"annual_revenue": 2_500_000.0})
		assert rule.matches({"annual_revenue": "$1,000,000"})
		assert not rule.matches({"annual_revenue": 999_999})
		assert not rule.matches({"annual_revenue": "unknown"})
		assert not rule.matches({"annual_revenue": 0})

	def test_group_awards_only_its_best_match(self):
		facts = {"annual_revenue": 3_000_000}
		fit, reasons = score_fit(facts, defaults.FIT_RULES)
		assert fit == 20
		assert [r.label for r in reasons] == ["Revenue $1M+ a year"]

	def test_negative_rules_and_floor_at_zero(self):
		fit, reasons = score_fit({defaults.EMAIL_BOUNCED: True}, defaults.FIT_RULES)
		assert fit == 0
		assert reasons[0].points == -30

	def test_capped_at_100(self):
		rules = [FitRule(f"r{i}", ("x",), Condition.IS_SET, 30) for i in range(5)]
		assert score_fit({"x": 1}, rules)[0] == 100


class TestEngagement:
	def test_points_halve_every_half_life(self):
		assert decayed(40, days_ago(30), NOW, 30) == pytest.approx(20)
		assert decayed(40, days_ago(60), NOW, 30) == pytest.approx(10)
		assert decayed(40, NOW + timedelta(hours=1), NOW, 30) == 40

	def test_form_submission_today(self):
		score, reasons = score_engagement(
			[Activity(Signal.FORM_SUBMISSION, NOW)], defaults.ACTIVITY_RULES, SETTINGS, NOW
		)
		assert score == 30
		assert reasons[0].label == "Submitted a form"

	def test_daily_cap_limits_page_views(self):
		views = [Activity(Signal.PAGE_VIEW, NOW - timedelta(minutes=i), "about") for i in range(12)]
		score, reasons = score_engagement(views, defaults.ACTIVITY_RULES, SETTINGS, NOW)
		assert score == 5
		assert reasons[0].count == 5

	def test_total_cap_keeps_the_newest_opens(self):
		opens = [Activity(Signal.EMAIL_OPEN, days_ago(d)) for d in (0, 1, 2, 40, 50)]
		_, reasons = score_engagement(opens, defaults.ACTIVITY_RULES, SETTINGS, NOW)
		assert reasons[0].count == 3
		expected = sum(decayed(1, days_ago(d), NOW, 30) for d in (0, 1, 2))
		assert reasons[0].points == pytest.approx(expected)

	def test_unmatched_signal_is_ignored(self):
		rules = [ActivityRule("Form", Signal.FORM_SUBMISSION, 30)]
		assert score_engagement([Activity(Signal.EMAIL_CLICK, NOW)], rules, SETTINGS, NOW) == (0, ())

	def test_capped_at_100(self):
		replies = [Activity(Signal.EMAIL_REPLY, NOW - timedelta(hours=h)) for h in range(10)]
		assert score_engagement(replies, defaults.ACTIVITY_RULES, SETTINGS, NOW)[0] == 100


class TestReturnVisits:
	def test_one_per_later_day(self):
		views = [
			Activity(Signal.PAGE_VIEW, datetime(2026, 10, 1, 9), "/"),
			Activity(Signal.PAGE_VIEW, datetime(2026, 10, 1, 10), "about"),
			Activity(Signal.PAGE_VIEW, datetime(2026, 10, 3, 15), "contact"),
			Activity(Signal.PAGE_VIEW, datetime(2026, 10, 3, 14), "seo"),
		]
		returns = [a for a in with_return_visits(views) if a.signal == Signal.RETURN_VISIT]
		assert returns == [Activity(Signal.RETURN_VISIT, datetime(2026, 10, 3, 14))]

	def test_single_day_has_none(self):
		views = [Activity(Signal.PAGE_VIEW, NOW, "/")]
		assert with_return_visits(views) == views


class TestPagePatterns:
	HIGH_INTENT = ActivityRule(
		"High-intent page", Signal.PAGE_VIEW, 5, ("contact", "*-migration", "*-program"), 15
	)
	RULES = (HIGH_INTENT, *defaults.ACTIVITY_RULES)

	@pytest.mark.parametrize("path", ["contact", "/contact/", "shopify-migration", "omg-program"])
	def test_patterned_rule_wins(self, path):
		assert rule_for(Activity(Signal.PAGE_VIEW, NOW, path), self.RULES) is self.HIGH_INTENT

	@pytest.mark.parametrize("path", ["/", "about", "migrations-overview"])
	def test_other_pages_use_the_plain_rule(self, path):
		assert rule_for(Activity(Signal.PAGE_VIEW, NOW, path), self.RULES).label == "Viewed a page"

	def test_home_page_pattern(self):
		rule = ActivityRule("Home", Signal.PAGE_VIEW, 2, ("/",))
		assert rule.matches_path("/")
		assert not rule.matches_path("about")


class TestHeat:
	@pytest.mark.parametrize(
		("fit", "engagement", "heat"),
		[
			(40, 50, Heat.HOT),
			(100, 100, Heat.HOT),
			(39, 90, Heat.WARM),
			(60, 0, Heat.WARM),
			(0, 20, Heat.WARM),
			(59, 19, Heat.COLD),
		],
	)
	def test_bands(self, fit, engagement, heat):
		assert classify(fit, engagement, SETTINGS) is heat

	def test_new_business_lead_from_the_website(self):
		facts = {
			defaults.BUSINESS_EMAIL: True,
			"organization": "Acme Ltd",
			"mobile_no": "780-555-0100",
			"website": "https://acme.example",
		}
		activities = [Activity(Signal.FORM_SUBMISSION, NOW)] + [
			Activity(Signal.PAGE_VIEW, NOW - timedelta(minutes=m), "about") for m in range(4)
		]
		result = score_lead(facts, activities, defaults.FIT_RULES, defaults.ACTIVITY_RULES, SETTINGS, NOW)
		assert (result.fit, result.engagement) == (45, 34)
		assert result.heat is Heat.WARM
		assert result.score == round(45 * 0.4 + 34 * 0.6)

	def test_hot_lead_cools_when_it_goes_quiet(self):
		facts = {
			defaults.BUSINESS_EMAIL: True,
			"organization": "Acme",
			"phone": "1",
			"website": "acme.example",
		}
		activities = [
			Activity(Signal.FORM_SUBMISSION, days_ago(0)),
			Activity(Signal.EMAIL_REPLY, days_ago(0)),
		]
		args = (facts, activities, defaults.FIT_RULES, defaults.ACTIVITY_RULES, SETTINGS)
		assert score_lead(*args, NOW).heat is Heat.HOT
		assert score_lead(*args, NOW + timedelta(days=30)).heat is Heat.WARM
		assert score_lead(*args, NOW + timedelta(days=90)).heat is Heat.COLD

	def test_summary_lists_reasons(self):
		facts = {defaults.BUSINESS_EMAIL: True}
		activities = [Activity(Signal.PAGE_VIEW, NOW, "a"), Activity(Signal.PAGE_VIEW, NOW, "b")]
		result = score_lead(facts, activities, defaults.FIT_RULES, defaults.ACTIVITY_RULES, SETTINGS, NOW)
		assert result.summary() == (
			"Fit 15/100\n  +15 Business email address\nEngagement 2/100\n  +2 Viewed a page (x2)"
		)
