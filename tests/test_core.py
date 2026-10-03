import unittest
from unittest.mock import patch

from reach_research.core import canonical_url, confirms_social_post, extract_html, markdown_report, parse_rss, search_source


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Wrong host</title><link>https://example.com/a</link></item>
<item><title>Post</title><link>https://x.com/person/status/1</link><description>Sample</description></item>
</channel></rss>"""


class CoreTests(unittest.TestCase):
    def test_canonical_url(self):
        self.assertEqual(canonical_url("https://x.com/a?utm_source=abc&id=1"), "https://x.com/a?id=1")
        self.assertEqual(canonical_url("http://127.0.0.1/secret"), "")

    def test_rss_and_site_filter(self):
        self.assertEqual(len(parse_rss(RSS)), 2)
        with patch("reach_research.core.download", return_value=(RSS, "text/xml", "https://www.bing.com/")):
            rows = search_source("x", "Post", 5)
        self.assertEqual([row["url"] for row in rows], ["https://x.com/person/status/1"])

    def test_extract_html(self):
        title, text = extract_html(b"<title>Page</title><nav>Menu</nav><article><h1>Title</h1><p>Body text</p></article>")
        self.assertEqual(title, "Page")
        self.assertIn("Body text", text)
        self.assertNotIn("Menu", text)

    def test_social_login_shell_is_not_post_evidence(self):
        row = {"source": "instagram", "title": "Instagram"}
        self.assertFalse(confirms_social_post(row, "Log in to see this post " * 20))

    def test_report_separates_verified_and_unverified(self):
        data = {"theme": "T", "created_at": "today", "coverage": {"x": {"status": "discovery_only", "discovered": 1, "read": 0}},
                "results": [{"source": "x", "title": "Post", "url": "https://x.com/a", "status": "discovery_only", "error": "blocked", "text": ""}]}
        report = markdown_report(data)
        self.assertIn("本文未確認", report)
        self.assertIn("blocked", report)
        self.assertIn("確認できた本文はありません", report)


if __name__ == "__main__":
    unittest.main()
