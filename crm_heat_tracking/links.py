# SPDX-License-Identifier: AGPL-3.0-or-later
"""Email click tracking, pure part (no Frappe import): rewriting an outgoing email's links to the
tracking endpoint, and telling a person's click from a mail scanner's.

Only ``http(s)`` links in ``<a href>`` are rewritten; ``mailto:``/``tel:`` links, unsubscribe links and
links that are already tracked are left alone. Security scanners (Microsoft Defender Safe Links,
Mimecast, Proofpoint...) fetch every link in an email as it arrives, so their requests are recorded but
flagged and never scored."""

import re
from collections.abc import Callable

ANCHOR_HREF = re.compile(r"""(<a\b[^>]*?\bhref\s*=\s*)(["'])(https?://[^"'\s>]+)\2""", re.IGNORECASE)
UNTRACKED_URL_PARTS = ("unsubscribe", "/api/method/crm_heat_tracking.")
SCANNER_AGENTS = re.compile(
	r"bot|crawl|spider|preview|scanner|safelinks|mimecast|proofpoint|barracuda|python-requests|curl|wget",
	re.IGNORECASE,
)
# A scanner follows the link moments after delivery; a person rarely clicks this fast.
SCANNER_WINDOW_SECONDS = 10


def rewrite_links(html: str, tracked_url: Callable[[str], str]) -> str:
	"""``html`` with every trackable link replaced by ``tracked_url(original)``. The tracked URL is
	inserted verbatim, so it must be a plain URL without quotes."""

	def replace(match: re.Match[str]) -> str:
		prefix, quote, url = match.groups()
		if any(part in url.lower() for part in UNTRACKED_URL_PARTS):
			return match.group(0)
		return f"{prefix}{quote}{tracked_url(url.replace('&amp;', '&'))}{quote}"

	return ANCHOR_HREF.sub(replace, html or "")


def is_scanner(user_agent: str | None, method: str, seconds_since_sent: float) -> bool:
	"""True when a click was most likely made by a mail scanner, not the recipient."""
	return (
		method.upper() == "HEAD"
		or not user_agent
		or bool(SCANNER_AGENTS.search(user_agent))
		or seconds_since_sent < SCANNER_WINDOW_SECONDS
	)
