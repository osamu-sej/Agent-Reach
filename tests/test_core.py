import unittest
from types import SimpleNamespace
from unittest.mock import patch

from reach_research.core import (YahooXParser, canonical_url, confirms_social_post, direct_search, extract_html,
                                 extract_x_post,
                                 markdown_report, opencli_search, parse_rss,
                                 plan_queries, research, search_source)


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

    def test_query_plan_is_bounded_and_inspectable(self):
        self.assertEqual(plan_queries("生成AI 小売業", "web", "quick"), ["生成AI 小売業"])
        self.assertEqual(len(plan_queries("生成AI 小売業", "web", "balanced")), 2)
        self.assertIn("生成AI 小売業 課題 批判", plan_queries("生成AI 小売業", "web", "deep"))

    def test_opencli_results_keep_only_the_requested_platform(self):
        output = '{"data":[{"url":"https://x.com/example/status/123","text":"投稿本文です"},' \
                 '{"url":"https://example.com/other","text":"無関係"}]}'
        with patch("reach_research.core.shutil.which", return_value="/usr/bin/opencli"), \
             patch("reach_research.core.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=output, stderr="")):
            rows = opencli_search("x", "topic", 5)
        self.assertEqual([row["url"] for row in rows], ["https://x.com/example/status/123"])
        self.assertEqual(rows[0]["snippet"], "投稿本文です")
        self.assertEqual(rows[0]["status"], "read")

    def test_local_x_search_keeps_browser_verified_text(self):
        row = {"title": "投稿本文", "url": "https://x.com/i/status/123", "snippet": "投稿本文",
               "published": "", "text": "投稿本文", "status": "read", "discovery_method": "opencli"}
        with patch("reach_research.core.opencli_search", return_value=[row]) as opencli, \
             patch("reach_research.core.yahoo_x_search", return_value=[]), \
             patch("reach_research.core.search_source", return_value=[]), \
             patch("reach_research.core.read_page") as read:
            result = research("商品", sources=["x"], depth="quick", use_opencli=True,
                              use_direct=False, opencli_sources=("x",))
        self.assertEqual(opencli.call_count, 1)
        self.assertEqual(result["coverage"]["x"]["read"], 1)
        self.assertEqual(result["results"][0]["discovery_method"], "opencli")
        read.assert_not_called()

    def test_native_github_search_is_read_only(self):
        output = '[{"name":"demo","description":"Example","url":"https://github.com/a/demo"}]'
        with patch("reach_research.core.shutil.which", return_value="/usr/bin/gh"), \
             patch("reach_research.core.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=output, stderr="")) as run:
            rows = direct_search("github", "example", 2)
        self.assertEqual(rows[0]["url"], "https://github.com/a/demo")
        self.assertEqual(run.call_args.args[0][:3], ["gh", "search", "repos"])

    def test_yahoo_x_result_and_post_metadata(self):
        parser = YahooXParser()
        parser.feed('<ol><li><a href="https://x.com/example/status/123">Example on X: 投稿</a>'
                    '<div>2026/10/3 - 投稿の抜粋</div></li>'
                    '<li><a href="https://example.com/post">Other</a></li></ol>')
        self.assertEqual(len(parser.rows), 1)
        self.assertEqual(parser.rows[0]["url"], "https://x.com/example/status/123")
        self.assertEqual(parser.rows[0]["discovery_method"], "yahoo-jp")
        title, body = extract_x_post(b'<meta property="og:title" content="Example on X">'
                                     b'<meta property="og:description" content="Post body">')
        self.assertEqual((title, body), ("Example on X", "Post body"))

    def test_research_marks_x_public_search_scope(self):
        with patch("reach_research.core.yahoo_x_search", return_value=[]), \
             patch("reach_research.core.search_source", return_value=[]):
            result = research("商品", sources=["x"], depth="quick", use_direct=False)
        self.assertEqual(result["coverage"]["x"]["discovered"], 0)
        self.assertIn("公開Web検索", result["coverage"]["x"]["note"])

    def test_research_confirms_x_post_from_public_page(self):
        row = {"title": "Example on X", "url": "https://x.com/example/status/123", "snippet": "投稿",
               "published": "", "discovery_method": "yahoo-jp"}
        with patch("reach_research.core.yahoo_x_search", return_value=[row]), \
             patch("reach_research.core.search_source", return_value=[]), \
             patch("reach_research.core.read_page", return_value=("Example on X", "投稿の本文です")):
            result = research("商品", sources=["x"], depth="quick", use_direct=False)
        self.assertEqual(result["coverage"]["x"]["read"], 1)
        self.assertEqual(result["results"][0]["text"], "投稿の本文です")

    def test_report_separates_verified_and_unverified(self):
        data = {"theme": "T", "created_at": "today", "coverage": {"x": {"status": "discovery_only", "discovered": 1, "read": 0}},
                "results": [{"source": "x", "title": "Post", "url": "https://x.com/a", "status": "discovery_only", "error": "blocked", "text": ""}]}
        report = markdown_report(data)
        self.assertIn("本文未確認", report)
        self.assertIn("blocked", report)
        self.assertIn("確認できた本文はありません", report)


if __name__ == "__main__":
    unittest.main()
