# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lead heat scoring: pure logic with no Frappe import.

A lead gets two scores, as HubSpot and Salesforce Account Engagement keep them:

- **Fit** (0-100): who the lead is, from its fields (business email, company, revenue...). It does not
  decay. Rules in the same group don't add up: the group gives its best-scoring match.
- **Engagement** (0-100): what the lead has done (form submissions, page views, email clicks and replies,
  calls). Every activity's points halve each ``half_life_days``, so a lead that goes quiet cools down.
  Caps limit how much one kind of activity can add per day and in total.

**Heat** combines both, like a grade x score matrix: Hot needs engagement and fit; Warm needs either.
Every score carries its reasons, so sales can see why a lead is hot."""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

SCORE_MAX = 100
SECONDS_PER_DAY = 86400
DECAY_BASE = 0.5
# Personal mailbox providers: an address on one of these is not a business email.
FREEMAIL_DOMAINS = frozenset(
	{
		"aol.com",
		"gmail.com",
		"googlemail.com",
		"gmx.com",
		"hotmail.ca",
		"hotmail.com",
		"icloud.com",
		"live.ca",
		"live.com",
		"mac.com",
		"mail.com",
		"me.com",
		"msn.com",
		"outlook.com",
		"proton.me",
		"protonmail.com",
		"rogers.com",
		"shaw.ca",
		"sympatico.ca",
		"telus.net",
		"yahoo.ca",
		"yahoo.com",
		"ymail.com",
		"zoho.com",
	}
)


class Condition(StrEnum):
	IS_SET = "Is Set"
	EQUALS = "Equals"
	CONTAINS_ANY = "Contains Any"
	AT_LEAST = "At Least"


class Signal(StrEnum):
	FORM_SUBMISSION = "Form Submission"
	PAGE_VIEW = "Page View"
	RETURN_VISIT = "Return Visit"
	EMAIL_OPEN = "Email Open"
	EMAIL_CLICK = "Email Click"
	EMAIL_REPLY = "Email Reply"
	INBOUND_CALL = "Inbound Call"
	CONNECTED_OUTBOUND_CALL = "Connected Outbound Call"


class Heat(StrEnum):
	HOT = "Hot"
	WARM = "Warm"
	COLD = "Cold"


def is_business_email(email: str | None) -> bool:
	"""True for an address whose domain is not a personal mailbox provider."""
	_, at, domain = (email or "").strip().lower().rpartition("@")
	return bool(at and domain and "." in domain and domain not in FREEMAIL_DOMAINS)


def _is_empty(value: object) -> bool:
	return value is None or value is False or (isinstance(value, str) and not value.strip())


@dataclass(frozen=True)
class FitRule:
	"""Points for a lead whose ``fields`` (any of them) meet ``condition``. ``values`` are matched
	case-insensitively; ``At Least`` compares numbers against ``values[0]``."""

	label: str
	fields: tuple[str, ...]
	condition: Condition
	points: int
	values: tuple[str, ...] = ()
	group: str = ""

	def matches(self, facts: Mapping[str, object]) -> bool:
		return any(self._value_matches(facts.get(name)) for name in self.fields)

	def _value_matches(self, value: object) -> bool:
		if _is_empty(value):
			return False
		text = str(value).strip().lower()
		wanted = [v.strip().lower() for v in self.values if v.strip()]
		match self.condition:
			case Condition.IS_SET:
				return True
			case Condition.EQUALS:
				return text in wanted
			case Condition.CONTAINS_ANY:
				return any(w in text for w in wanted)
			case Condition.AT_LEAST:
				return bool(wanted) and _number(value) >= _number(wanted[0])
		return False


def _number(value: object) -> float:
	try:
		return float(str(value).replace(",", "").replace("$", ""))
	except ValueError:
		return float("-inf")


@dataclass(frozen=True)
class ActivityRule:
	"""Points per activity of ``signal``. A page-view rule with ``path_patterns`` (shell-style, e.g.
	``*-migration``) applies only to those pages, and wins over a page-view rule without patterns.
	``daily_cap`` and ``total_cap`` bound the points this rule can award (0 = no cap)."""

	label: str
	signal: Signal
	points: int
	path_patterns: tuple[str, ...] = ()
	daily_cap: int = 0
	total_cap: int = 0

	def matches_path(self, path: str) -> bool:
		page = path.strip("/") or "/"
		return any(fnmatch.fnmatchcase(page, pattern.strip("/") or "/") for pattern in self.path_patterns)


@dataclass(frozen=True)
class Activity:
	signal: Signal
	at: datetime
	path: str = ""


@dataclass(frozen=True)
class Settings:
	half_life_days: float = 30
	hot_fit: int = 40
	hot_engagement: int = 50
	warm_fit: int = 60
	warm_engagement: int = 20
	# Share of the combined score that comes from fit; the rest is engagement.
	fit_weight: float = 0.4


@dataclass(frozen=True)
class Reason:
	label: str
	points: float
	count: int = 1


@dataclass(frozen=True)
class HeatResult:
	fit: int
	engagement: int
	score: int
	heat: Heat
	fit_reasons: tuple[Reason, ...] = field(default_factory=tuple)
	engagement_reasons: tuple[Reason, ...] = field(default_factory=tuple)

	def summary(self) -> str:
		"""The reasons as text for the lead's side panel, biggest first."""
		lines = [f"Fit {self.fit}/{SCORE_MAX}"]
		lines += [f"  {_signed(r.points)} {r.label}" for r in self.fit_reasons]
		lines.append(f"Engagement {self.engagement}/{SCORE_MAX}")
		lines += [
			f"  {_signed(r.points)} {r.label}" + (f" (x{r.count})" if r.count > 1 else "")
			for r in self.engagement_reasons
		]
		return "\n".join(lines)


def _signed(points: float) -> str:
	return f"{round(points):+d}"


def _clamp(value: float) -> int:
	return max(0, min(SCORE_MAX, round(value)))


def score_fit(facts: Mapping[str, object], rules: Iterable[FitRule]) -> tuple[int, tuple[Reason, ...]]:
	best_in_group: dict[str, FitRule] = {}
	awarded: list[FitRule] = []
	for rule in rules:
		if not rule.matches(facts):
			continue
		if not rule.group:
			awarded.append(rule)
		elif rule.group not in best_in_group or rule.points > best_in_group[rule.group].points:
			best_in_group[rule.group] = rule
	awarded += best_in_group.values()
	reasons = tuple(sorted((Reason(r.label, r.points) for r in awarded), key=lambda r: -abs(r.points)))
	return _clamp(sum(r.points for r in reasons)), reasons


def with_return_visits(activities: Iterable[Activity]) -> list[Activity]:
	"""``activities`` plus one Return Visit for every day with page views after the first such day."""
	activities = list(activities)
	first_view_per_day: dict[date, datetime] = {}
	for a in activities:
		if a.signal == Signal.PAGE_VIEW:
			day = a.at.date()
			first_view_per_day[day] = min(a.at, first_view_per_day.get(day, a.at))
	return_days = sorted(first_view_per_day)[1:]
	return activities + [Activity(Signal.RETURN_VISIT, first_view_per_day[day]) for day in return_days]


def rule_for(activity: Activity, rules: Iterable[ActivityRule]) -> ActivityRule | None:
	candidates = [r for r in rules if r.signal == activity.signal]
	if activity.signal == Signal.PAGE_VIEW:
		for rule in candidates:
			if rule.path_patterns and rule.matches_path(activity.path):
				return rule
	return next((r for r in candidates if not r.path_patterns), None)


def decayed(points: float, at: datetime, now: datetime, half_life_days: float) -> float:
	age_days = max(0.0, (now - at).total_seconds() / SECONDS_PER_DAY)
	return points * DECAY_BASE ** (age_days / half_life_days)


def score_engagement(
	activities: Iterable[Activity], rules: Iterable[ActivityRule], settings: Settings, now: datetime
) -> tuple[int, tuple[Reason, ...]]:
	"""Newest activities are counted first, so caps keep the points that have decayed least."""
	rules = list(rules)
	total_used: dict[ActivityRule, int] = defaultdict(int)
	day_used: dict[tuple[ActivityRule, date], int] = defaultdict(int)
	points_by_rule: dict[ActivityRule, float] = defaultdict(float)
	count_by_rule: dict[ActivityRule, int] = defaultdict(int)
	for activity in sorted(with_return_visits(activities), key=lambda a: a.at, reverse=True):
		rule = rule_for(activity, rules)
		if rule is None:
			continue
		points = rule.points
		if points > 0:
			if rule.daily_cap:
				points = min(points, rule.daily_cap - day_used[(rule, activity.at.date())])
			if rule.total_cap:
				points = min(points, rule.total_cap - total_used[rule])
			if points <= 0:
				continue
			day_used[(rule, activity.at.date())] += points
			total_used[rule] += points
		points_by_rule[rule] += decayed(points, activity.at, now, settings.half_life_days)
		count_by_rule[rule] += 1
	reasons = tuple(
		sorted(
			(Reason(rule.label, points, count_by_rule[rule]) for rule, points in points_by_rule.items()),
			key=lambda r: -abs(r.points),
		)
	)
	return _clamp(sum(r.points for r in reasons)), reasons


def classify(fit: int, engagement: int, settings: Settings) -> Heat:
	if engagement >= settings.hot_engagement and fit >= settings.hot_fit:
		return Heat.HOT
	if engagement >= settings.warm_engagement or fit >= settings.warm_fit:
		return Heat.WARM
	return Heat.COLD


def score_lead(
	facts: Mapping[str, object],
	activities: Iterable[Activity],
	fit_rules: Iterable[FitRule],
	activity_rules: Iterable[ActivityRule],
	settings: Settings,
	now: datetime,
) -> HeatResult:
	fit, fit_reasons = score_fit(facts, fit_rules)
	engagement, engagement_reasons = score_engagement(activities, activity_rules, settings, now)
	score = _clamp(fit * settings.fit_weight + engagement * (1 - settings.fit_weight))
	return HeatResult(
		fit=fit,
		engagement=engagement,
		score=score,
		heat=classify(fit, engagement, settings),
		fit_reasons=fit_reasons,
		engagement_reasons=engagement_reasons,
	)
