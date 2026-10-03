"""Read-only discovery, retrieval, and auditable research output."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from importlib.util import find_spec
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET


USER_AGENT = "ReachResearch/0.1 (+https://github.com/osamu-sej/Agent-Reach)"
MAX_BYTES = 2_000_000
SOURCES = {
    "web": "",
    "news": "news",
    "x": "site:x.com",
    "reddit": "site:reddit.com",
    "youtube": "site:youtube.com",
    "github": "site:github.com",
    "instagram": "site:instagram.com",
    "threads": "site:threads.net",
    "tiktok": "site:tiktok.com",
    "facebook": "site:facebook.com",
}
SOCIAL = set(SOURCES) - {"web", "news"}
SOCIAL_PAGES = SOCIAL - {"github"}
OPENCLI_SITES = {"x": "twitter", "reddit": "reddit", "instagram": "instagram", "facebook": "facebook"}
DIRECT_TOOLS = {"github": "gh", "youtube": "yt-dlp"}
SOURCE_HOSTS = {
    "x": ("x.com", "twitter.com"),
    "reddit": ("reddit.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "github": ("github.com",),
    "instagram": ("instagram.com",),
    "threads": ("threads.net",),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com",),
}


def canonical_url(url):
    parts = urlparse(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return ""
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            return ""
    except ValueError:
        pass
    kept = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunparse((parts.scheme, parts.netloc.lower(), parts.path or "/", "", urlencode(kept), ""))


def safe_public_url(url):
    """Reject local and private destinations, including DNS resolved addresses."""
    clean = canonical_url(url)
    if not clean:
        return ""
    host = urlparse(clean).hostname
    try:
        addresses = socket.getaddrinfo(host, None)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            return ""
    except (OSError, ValueError):
        return ""
    return clean


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not safe_public_url(newurl):
            raise ValueError("redirected to a non-public URL")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, timeout=12, headers=None):
    clean = safe_public_url(url)
    if not clean:
        raise ValueError("non-public or invalid URL")
    request_headers = {"User-Agent": USER_AGENT, "Accept": "text/html, application/xml, text/xml, application/rss+xml"}
    request_headers.update(headers or {})
    request = Request(clean, headers=request_headers)
    with build_opener(SafeRedirectHandler()).open(request, timeout=timeout) as response:
        final = safe_public_url(response.geturl())
        if not final:
            raise ValueError("redirected to a non-public URL")
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("response exceeds size limit")
        return content, response.headers.get_content_type(), final


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.title = []
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header", "noscript"):
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in ("p", "h1", "h2", "h3", "li", "article", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "noscript") and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.skip and not self.in_title:
            self.parts.append(data)


def extract_html(raw):
    parser = TextExtractor()
    parser.feed(raw.decode("utf-8", "replace"))
    title = " ".join(" ".join(parser.title).split())
    body = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return title, body


class YahooXParser(HTMLParser):
    """Read public X status links and snippets from Yahoo Japan search results."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None
        self.in_anchor = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.current = {"title": "", "url": "", "snippet": "", "published": "",
                            "discovery_method": "yahoo-jp"}
        elif self.current is not None and tag == "a":
            url = canonical_url(attrs.get("href", ""))
            if re.search(r"^https://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/\d+", url):
                self.current["url"] = url
                self.in_anchor = True

    def handle_endtag(self, tag):
        if tag == "a":
            self.in_anchor = False
        elif tag == "li" and self.current is not None:
            if self.current["url"] and self.current["title"]:
                self.current["title"] = " ".join(self.current["title"].split())
                self.current["snippet"] = " ".join(self.current["snippet"].split())[:300]
                date = re.match(r"\d{4}/\d{1,2}/\d{1,2}", self.current["snippet"])
                if date:
                    self.current["published"] = date.group()
                self.rows.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.current is not None and self.current["url"]:
            field = "title" if self.in_anchor else "snippet"
            self.current[field] += data


def yahoo_x_search(query, limit):
    url = "https://search.yahoo.co.jp/search?" + urlencode({"p": query + " site:x.com", "n": limit})
    raw, content_type, _ = download(url)
    if content_type != "text/html":
        raise ValueError("Yahoo search did not return HTML")
    parser = YahooXParser()
    parser.feed(raw.decode("utf-8", "replace"))
    return parser.rows[:limit]


class XMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            attrs = dict(attrs)
            key = attrs.get("property") or attrs.get("name")
            if key in ("og:title", "og:description"):
                self.meta[key] = attrs.get("content", "")


def extract_x_post(raw):
    parser = XMetaParser()
    parser.feed(raw.decode("utf-8", "replace"))
    title = parser.meta.get("og:title", "").strip()
    body = parser.meta.get("og:description", "").strip()
    if " on X" not in title or not body:
        raise ValueError("public X post metadata is unavailable")
    return title, body


def parse_rss(raw):
    root = ET.fromstring(raw)
    found = []
    for item in root.findall("./channel/item"):
        found.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "snippet": re.sub("<[^>]+>", "", item.findtext("description") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
        })
    return found


def relevant(row, theme):
    terms = [term.lower() for term in theme.split() if len(term) > 1 and not term.lower().startswith("site:")]
    haystack = (row["title"] + " " + row["snippet"]).lower()
    return not terms or sum(term in haystack for term in terms) >= max(1, (len(terms) + 1) // 2)


def plan_queries(theme, source, depth="balanced", extra_queries=None):
    """Bounded, inspectable search plan; no model-generated terms are invented."""
    if depth not in ("quick", "balanced", "deep"):
        raise ValueError("depth must be quick, balanced, or deep")
    japanese = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", theme))
    if source in ("web", "news"):
        angles = (["公式 発表", "事例 導入", "課題 批判"] if japanese else
                  ["official announcement", "case study adoption", "limitations criticism"])
    else:
        angles = (["体験 評判", "課題"] if japanese else ["experience discussion", "issues"])
    planned = [theme.strip()]
    if depth == "balanced":
        planned.append(theme.strip() + " " + angles[0])
    elif depth == "deep":
        planned.extend(theme.strip() + " " + angle for angle in angles)
    planned.extend(query.strip() for query in (extra_queries or []) if query.strip())
    return list(dict.fromkeys(planned))


def source_url_matches(source, url):
    hosts = SOURCE_HOSTS.get(source)
    if not hosts:
        return True
    hostname = (urlparse(url).hostname or "").lower()
    return any(hostname == host or hostname.endswith("." + host) for host in hosts)


def opencli_search(source, query, limit):
    """Search through a user's already configured OpenCLI/Chrome session."""
    if source not in OPENCLI_SITES:
        return []
    if not shutil.which("opencli"):
        raise ValueError("OpenCLI is not installed")
    command = ["opencli", OPENCLI_SITES[source], "search", query, "-f", "yaml"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=35, check=False)
    if process.returncode:
        raise ValueError("OpenCLI search failed: " + process.stderr[:150])
    found = []
    seen = set()
    for match in re.findall(r"https?://[^\s<>\"']+", process.stdout):
        url = canonical_url(match.rstrip(",;)]}"))
        if url and url not in seen and source_url_matches(source, url):
            found.append({"title": url, "url": url, "snippet": "", "published": "", "discovery_method": "opencli"})
            seen.add(url)
    return found[:limit]


def direct_search(source, query, limit):
    """Read-only native searches for GitHub repositories and YouTube videos."""
    if source not in DIRECT_TOOLS or not shutil.which(DIRECT_TOOLS[source]):
        return []
    if source == "github":
        command = ["gh", "search", "repos", query, "--limit", str(limit), "--json", "name,description,url"]
        process = subprocess.run(command, capture_output=True, text=True, timeout=25, check=False)
        if process.returncode:
            raise ValueError("GitHub search failed: " + process.stderr[:150])
        items = json.loads(process.stdout)
        return [{"title": item.get("name", ""), "url": item.get("url", ""),
                 "snippet": item.get("description") or "", "published": "",
                 "discovery_method": "gh"} for item in items]
    command = ["yt-dlp", "--dump-json", "--skip-download", "ytsearch{}:{}".format(limit, query)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    if process.returncode and not process.stdout.strip():
        raise ValueError("YouTube search failed: " + process.stderr[:150])
    found = []
    for line in process.stdout.splitlines():
        item = json.loads(line)
        url = item.get("webpage_url") or item.get("url") or ""
        if not url.startswith("http") and item.get("id"):
            url = "https://www.youtube.com/watch?v=" + item["id"]
        if source_url_matches("youtube", url):
            found.append({"title": item.get("title", ""), "url": url,
                          "snippet": (item.get("description") or "")[:300],
                          "published": item.get("upload_date") or "",
                          "discovery_method": "yt-dlp"})
    return found[:limit]


def brave_search(query, limit):
    api_key = os.environ["BRAVE_SEARCH_API_KEY"]
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": limit})
    raw, _, _ = download(url, headers={"Accept": "application/json", "X-Subscription-Token": api_key})
    payload = json.loads(raw)
    return [{"title": item.get("title", ""), "url": item.get("url", ""),
             "snippet": item.get("description", ""), "published": item.get("page_age", "")}
            for item in payload.get("web", {}).get("results", [])]


def exa_search(query, limit):
    mcporter = shutil.which("mcporter")
    npx = shutil.which("npx")
    if not mcporter and not npx:
        raise ValueError("Exa search needs mcporter or Node.js/npx")
    command = ([mcporter] if mcporter else [npx, "-y", "mcporter@0.14.2"]) + [
               "call", "https://mcp.exa.ai/mcp.web_search_exa",
               "--args", json.dumps({"query": query, "numResults": limit,
                                      "objective": "Find pages directly relevant to this topic; return their original URLs."}),
               "--output", "json", "--timeout", "20000"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if process.returncode:
        raise ValueError("Exa search failed: " + process.stderr[:150])
    payload = json.loads(process.stdout)
    results = []
    for block in payload.get("content", []):
        if block.get("type") != "text":
            continue
        for section in re.split(r"(?=^Title: )", block.get("text", ""), flags=re.MULTILINE):
            title = re.search(r"^Title: (.+)$", section, re.MULTILINE)
            url = re.search(r"^URL: (https?://\S+)$", section, re.MULTILINE)
            published = re.search(r"^Published: (.+)$", section, re.MULTILINE)
            if title and url:
                results.append({"title": title.group(1), "url": url.group(1),
                                "snippet": "", "published": published.group(1) if published else ""})
    return results[:limit]


def select_backend(requested):
    if requested not in ("auto", "exa", "brave", "bing"):
        raise ValueError("unknown search backend: " + requested)
    if requested != "auto":
        return requested
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        return "brave"
    if shutil.which("mcporter") or shutil.which("npx"):
        return "exa"
    return "bing"


def diagnostics():
    """Local capability inventory; availability does not assert live access."""
    return {
        "search_backend_default": select_backend("auto"),
        "exa_npx_available": bool(shutil.which("mcporter") or shutil.which("npx")),
        "brave_key_configured": bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
        "x_public_search": "yahoo-jp",
        "opencli_installed": bool(shutil.which("opencli")),
        "github_cli_installed": bool(shutil.which("gh")),
        "youtube_cli_installed": bool(shutil.which("yt-dlp")),
        "scrapling_installed": find_spec("scrapling") is not None,
        "note": "Installed or configured does not prove login, quota, or live platform access.",
    }


def search_source(source, theme, limit, backend="auto"):
    if source == "news":
        url = "https://news.google.com/rss/search?" + urlencode({"q": theme, "hl": "ja", "gl": "JP", "ceid": "JP:ja"})
        raw, _, _ = download(url)
        results = parse_rss(raw)
    else:
        query = (theme + " " + SOURCES[source]).strip()
        if backend == "brave":
            results = brave_search(query, limit)
        elif backend == "exa":
            results = exa_search(query, limit)
        else:
            url = "https://www.bing.com/search?" + urlencode({"format": "rss", "q": query, "count": limit})
            raw, _, _ = download(url)
            results = [row for row in parse_rss(raw) if relevant(row, theme)]
    results = [row for row in results if source_url_matches(source, row["url"])]
    return results[:limit]


def read_page(url, use_scrapling=False):
    """Use Scrapling's ordinary fetcher when opted in; never escalate access."""
    clean = safe_public_url(url)
    if not clean:
        raise ValueError("non-public or invalid URL")
    if re.search(r"^https://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/\d+", clean):
        raw, content_type, _ = download(clean)
        if content_type != "text/html":
            raise ValueError("X post did not return HTML")
        return extract_x_post(raw)
    if use_scrapling:
        from scrapling.fetchers import Fetcher
        page = Fetcher.get(clean, timeout=12)
        status = getattr(page, "status", 200)
        if status >= 400:
            raise ValueError("HTTP " + str(status))
        return extract_html(page.body)
    raw, content_type, _ = download(clean)
    if content_type not in ("text/html", "application/xhtml+xml"):
        raise ValueError("unsupported content type: " + content_type)
    return extract_html(raw)


def confirms_social_post(row, body):
    if row["source"] not in SOCIAL_PAGES:
        return True
    if row["source"] == "x":
        return bool(re.search(r"^https://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/\d+", row["url"])) and len(body) >= 5
    title = " ".join(row["title"].split())
    if title.lower() in ("instagram", "facebook", "x", "tiktok", "youtube", "reddit", "threads"):
        return False
    key = title[:16].lower()
    return len(key) >= 12 and key in " ".join(body.split()).lower()


def research(theme, sources=None, limit=5, max_pages=20, use_scrapling=False,
             search_backend="auto", depth="balanced", extra_queries=None,
             use_opencli=False, use_direct=True):
    if not theme or not theme.strip():
        raise ValueError("theme is required")
    if use_scrapling and sys.version_info < (3, 10):
        raise ValueError("Scrapling requires Python 3.10 or newer")
    if not 1 <= limit <= 20 or not 0 <= max_pages <= 100:
        raise ValueError("limit must be 1-20 and max_pages must be 0-100")
    selected = list(dict.fromkeys(sources or SOURCES))
    backend = select_backend(search_backend)
    if backend == "brave" and not os.environ.get("BRAVE_SEARCH_API_KEY"):
        raise ValueError("BRAVE_SEARCH_API_KEY is required for Brave search")
    unknown = set(selected) - set(SOURCES)
    if unknown:
        raise ValueError("unknown source: " + ", ".join(sorted(unknown)))
    plans = {name: plan_queries(theme, name, depth, extra_queries) for name in selected}
    coverage = {name: {"status": "pending", "discovered": 0, "read": 0, "note": "", "queries": []} for name in selected}
    discovered = []
    tasks = []
    if use_opencli:
        tasks.extend((name, query, "opencli") for name in selected if name in OPENCLI_SITES for query in plans[name])
    if use_direct:
        tasks.extend((name, query, "direct") for name in selected if name in DIRECT_TOOLS
                     and shutil.which(DIRECT_TOOLS[name]) for query in plans[name])
    if "x" in selected:
        tasks.extend(("x", query, "yahoo-x") for query in plans["x"])
    tasks.extend((name, query, "index") for name in selected for query in plans[name])
    completed = {}
    with ThreadPoolExecutor(max_workers=min(2 if backend == "exa" else 6, len(tasks) or 1)) as pool:
        futures = {}
        for index, (name, query, method) in enumerate(tasks):
            if method == "opencli":
                future = pool.submit(opencli_search, name, query, limit)
            elif method == "direct":
                future = pool.submit(direct_search, name, query, limit)
            elif method == "yahoo-x":
                future = pool.submit(yahoo_x_search, query, limit)
            else:
                future = pool.submit(search_source, name, query, limit, backend)
            futures[future] = index
        for future in as_completed(futures):
            index = futures[future]
            try:
                completed[index] = (future.result(), "")
            except (HTTPError, URLError, TimeoutError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as exc:
                completed[index] = ([], str(exc)[:200])
    for index, (name, query, method) in enumerate(tasks):
        results, error = completed[index]
        coverage[name]["queries"].append({"query": query, "method": method, "found": len(results), "error": error})
        for result in results:
            result["source"] = name
            result["queries"] = [query]
            result["discovery_method"] = result.get("discovery_method", "google-news" if name == "news" else backend)
            discovered.append(result)
    seen = set()
    unique = []
    for result in discovered:
        clean = canonical_url(result["url"])
        key = (result["source"], clean)
        if clean and key not in seen:
            result["url"] = clean
            result["status"] = "discovery_only"
            result["text"] = ""
            result["error"] = ""
            seen.add(key)
            unique.append(result)
        elif clean:
            existing = next(row for row in unique if row["source"] == result["source"] and row["url"] == clean)
            if result["queries"][0] not in existing["queries"]:
                existing["queries"].append(result["queries"][0])
    order = {name: index for index, name in enumerate(selected)}
    unique.sort(key=lambda row: order[row["source"]])
    by_source = {name: [row for row in unique if row["source"] == name] for name in selected}
    for name, rows in by_source.items():
        coverage[name]["discovered"] = len(rows)
        attempts = coverage[name]["queries"]
        coverage[name]["status"] = "discovered" if rows else ("error" if all(item["error"] for item in attempts) else "empty")
        errors = [item["error"] for item in attempts if item["error"]]
        if errors:
            coverage[name]["note"] = "; ".join(errors)[:300]
        if name == "x":
            coverage[name]["note"] = ("無料の公開Web検索でX投稿を探しました。X内の全投稿は対象外です。 "
                                      + coverage[name]["note"]).strip()
    candidates = []
    for index in range(max((len(rows) for rows in by_source.values()), default=0)):
        for name in selected:
            if index < len(by_source[name]):
                candidates.append(by_source[name][index])
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(read_page, row["url"], use_scrapling): row
                   for row in candidates[:max_pages] if row["status"] != "read"}
        for future in as_completed(futures):
            row = futures[future]
            try:
                title, body = future.result()
                minimum = 5 if row["source"] == "x" else 120
                if len(body) >= minimum and confirms_social_post(row, body):
                    row["status"] = "read"
                    row["text"] = body[:12000]
                    row["page_title"] = title
                    coverage[row["source"]]["read"] += 1
                else:
                    row["error"] = "page or specific social post content could not be verified"
            except (HTTPError, URLError, TimeoutError, ValueError, ImportError, OSError) as exc:
                row["error"] = str(exc)[:200]
    for name, entry in coverage.items():
        if entry["status"] == "discovered":
            entry["status"] = "read" if entry["read"] else "discovery_only"
            if name in SOCIAL and not entry["read"]:
                entry["note"] = (entry["note"] + " 投稿本文は確認できませんでした。").strip()
    return {
        "theme": theme.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": backend + " search / Google News RSS" + (" / native tools" if use_direct else "") +
                  (" / OpenCLI" if use_opencli else "") +
                  (" / Yahoo Japan public X search" if "x" in selected else "") +
                  "; direct page retrieval",
        "depth": depth,
        "coverage": coverage,
        "results": unique,
    }


def markdown_report(data):
    lines = ["# 調査結果: " + data["theme"], "", "取得日時: " + data["created_at"], "", "## 対象媒体", "", "| 媒体 | 状態 | 発見 | 本文確認 |", "|---|---|---:|---:|"]
    for name, state in data["coverage"].items():
        lines.append("| {} | {} | {} | {} |".format(name, state["status"], state["discovered"], state["read"]))
    notes = [(name, state["note"]) for name, state in data["coverage"].items() if state.get("note")]
    if notes:
        lines += ["", "## 媒体別の注意", ""]
        lines.extend("- {}: {}".format(name, note) for name, note in notes)
    if any(state.get("queries") for state in data["coverage"].values()):
        lines += ["", "## 実行した検索", ""]
        for name, state in data["coverage"].items():
            for item in state.get("queries", []):
                detail = "失敗: " + item["error"] if item["error"] else "発見: " + str(item["found"])
                lines.append("- {} / {}: {} — {}".format(name, item["method"], item["query"], detail))
    lines += ["", "## 本文を確認できた資料", ""]
    read = [row for row in data["results"] if row["status"] == "read"]
    if not read:
        lines.append("確認できた本文はありません。")
    for row in read:
        compact = " ".join(row["text"].split())
        terms = [term for term in data["theme"].split() if len(term) > 1]
        positions = [compact.lower().find(term.lower()) for term in terms]
        hit = next((position for position in positions if position >= 0), 0)
        excerpt = compact[max(0, hit - 30):hit + 150]
        lines.extend(["### " + (row.get("page_title") or row["title"]), "", "- 媒体: " + row["source"],
                      "- 発見経路: " + row.get("discovery_method", "不明"), "- 出典: " + row["url"], "- 抜粋: " + excerpt, ""])
    lines += ["## 発見のみ（本文未確認）", ""]
    unread = [row for row in data["results"] if row["status"] != "read"]
    if not unread:
        lines.append("ありません。")
    for row in unread:
        lines.append("- [{}] [{}]({}) — {}".format(row["source"], row["title"].replace("]", ""), row["url"], row["error"] or "検索結果のみ"))
    lines += ["", "## 調査上の制約", "", "検索結果の件数は媒体上の全投稿数を意味しません。ログイン、robots、レート制限などで本文を取得できなかった資料は根拠として扱っていません。", ""]
    return "\n".join(lines)
