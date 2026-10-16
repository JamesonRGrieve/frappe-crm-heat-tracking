# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for email link rewriting and scanner detection (no bench)."""

import pytest

from crm_heat_tracking.links import is_scanner, rewrite_links

TRACKED = "https://erp.example/api/method/crm_heat_tracking.email_tracking.click?t="


def tracker(urls):
	def tracked_url(url):
		urls.append(url)
		return f"{TRACKED}{len(urls)}"

	return tracked_url


class TestRewriteLinks:
	def test_rewrites_http_links_and_keeps_markup(self):
		urls = []
		html = '<p>See <a class="btn" href="https://omg.example/seo?a=1&amp;b=2">SEO</a> and <a href=\'http://x.example\'>x</a></p>'
		out = rewrite_links(html, tracker(urls))
		assert out == (
			f'<p>See <a class="btn" href="{TRACKED}1">SEO</a> and <a href=\'{TRACKED}2\'>x</a></p>'
		)
		assert urls == ["https://omg.example/seo?a=1&b=2", "http://x.example"]

	@pytest.mark.parametrize(
		"html",
		[
			'<a href="mailto:info@omg.example">mail</a>',
			'<a href="tel:+17805550100">call</a>',
			'<a href="https://erp.example/unsubscribe?email=x">unsubscribe</a>',
			f'<a href="{TRACKED}abc">already tracked</a>',
			'<img src="https://omg.example/logo.png">',
			"plain text https://omg.example",
		],
	)
	def test_leaves_untrackable_links_alone(self, html):
		assert rewrite_links(html, tracker([])) == html

	def test_empty_content(self):
		assert rewrite_links(None, tracker([])) == ""


class TestScanner:
	BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15"

	def test_person_clicking_later(self):
		assert not is_scanner(self.BROWSER, "GET", 3600)

	@pytest.mark.parametrize(
		("agent", "method", "seconds"),
		[
			(BROWSER, "HEAD", 3600),
			(None, "GET", 3600),
			("Mozilla/5.0 (compatible; SafeLinks)", "GET", 3600),
			("python-requests/2.32", "GET", 3600),
			(BROWSER, "GET", 4),
		],
	)
	def test_scanners(self, agent, method, seconds):
		assert is_scanner(agent, method, seconds)
