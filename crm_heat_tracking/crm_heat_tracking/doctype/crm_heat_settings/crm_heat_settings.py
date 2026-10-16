# SPDX-License-Identifier: AGPL-3.0-or-later
import frappe
from frappe import _
from frappe.model.document import Document

from crm_heat_tracking import defaults, scoring

LEAD = "CRM Lead"


def _lines(text: str | None) -> tuple[str, ...]:
	return tuple(line.strip() for line in (text or "").splitlines() if line.strip())


class CRMHeatSettings(Document):
	def validate(self):
		for name in ("hot_engagement", "hot_fit", "warm_engagement", "warm_fit"):
			if not 0 <= (self.get(name) or 0) <= scoring.SCORE_MAX:
				frappe.throw(
					_("{0} must be between 0 and {1}").format(self.meta.get_label(name), scoring.SCORE_MAX)
				)
		if (self.half_life_days or 0) <= 0:
			frappe.throw(_("Engagement Half-Life must be more than 0 days"))
		lead_fields = {df.fieldname for df in frappe.get_meta(LEAD).fields} | {"name", "creation"}
		for row in self.fit_rules:
			unknown = [
				f for f in _fields(row.fields) if f not in lead_fields and f not in defaults.COMPUTED_FACTS
			]
			if unknown:
				frappe.throw(
					_("Fit rule {0}: {1} is not a CRM Lead field").format(row.idx, ", ".join(unknown))
				)
			if row.condition != scoring.Condition.IS_SET and not _lines(row.match_values):
				frappe.throw(_("Fit rule {0}: {1} needs at least one value").format(row.idx, row.condition))
		for row in self.activity_rules:
			if row.path_patterns and row.signal != scoring.Signal.PAGE_VIEW:
				frappe.throw(_("Activity rule {0}: only Page View rules take path patterns").format(row.idx))

	def on_update(self):
		frappe.enqueue("crm_heat_tracking.heat.update_all_leads", queue="long", enqueue_after_commit=True)

	def scoring_settings(self) -> scoring.Settings:
		return scoring.Settings(
			half_life_days=self.half_life_days,
			hot_fit=self.hot_fit,
			hot_engagement=self.hot_engagement,
			warm_fit=self.warm_fit,
			warm_engagement=self.warm_engagement,
			fit_weight=(self.fit_weight or 0) / 100,
		)

	def fit_rule_set(self) -> tuple[scoring.FitRule, ...]:
		return tuple(
			scoring.FitRule(
				label=row.label,
				fields=_fields(row.fields),
				condition=scoring.Condition(row.condition),
				points=row.points,
				values=_lines(row.match_values),
				group=(row.rule_group or "").strip(),
			)
			for row in self.fit_rules
		)

	def activity_rule_set(self) -> tuple[scoring.ActivityRule, ...]:
		return tuple(
			scoring.ActivityRule(
				label=row.label,
				signal=scoring.Signal(row.signal),
				points=row.points,
				path_patterns=_lines(row.path_patterns),
				daily_cap=row.daily_cap or 0,
				total_cap=row.total_cap or 0,
			)
			for row in self.activity_rules
		)

	def website_sources(self) -> tuple[str, ...]:
		return _lines(self.website_lead_sources)


def _fields(text: str | None) -> tuple[str, ...]:
	return tuple(f.strip() for f in (text or "").split(",") if f.strip())
