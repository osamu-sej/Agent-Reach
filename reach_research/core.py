"""Read-only discovery, retrieval, and auditable research output."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
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


def brave_search(query, limit):
    api_key = os.environ["BRAVE_SEARCH_API_KEY"]
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": limit})
    raw, _, _ = download(url, headers={"Accept": "application/json", "X-Subscription-Token": api_key})
    payload = json.loads(raw)
    return [{"title": item.get("title", ""), "url": item.get("url", ""),
             "snippet": item.get("description", ""), "published": item.get("page_age", "")}
            for item in payload.get("web", {}).get("results", [])]


def exa_search(query, limit):
    if not shutil.which("npx"):
        raise ValueError("Exa search needs Node.js/npx")
    command = ["npx", "-y", "mcporter@0.14.2", "call", "https://mcp.exa.ai/mcp.web_search_exa",
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
    if shutil.which("npx"):
        return "exa"
    return "bing"


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
    if source in SOURCE_HOSTS:
        allowed = SOURCE_HOSTS[source]
        results = [row for row in results if any(
            (urlparse(row["url"]).hostname or "").lower() == host or
            (urlparse(row["url"]).hostname or "").lower().endswith("." + host)
            for host in allowed
        )]
    return results[:limit]


def read_page(url, use_scrapling=False):
    """Use Scrapling's ordinary fetcher when opted in; never escalate access."""
    clean = safe_public_url(url)
    if not clean:
        raise ValueError("non-public or invalid URL")
    if use_scrapling:
        from scrapling.fetchers import Fetcher
        page = Fetcher.fetch(clean, timeout=12000)
        status = getattr(page, "status", 200)
        if status >= 400:
            raise ValueError("HTTP " + str(status))
        raw = str(page.html_content).encode("utf-8")
        return extract_html(raw)
    raw, content_type, _ = download(clean)
    if content_type not in ("text/html", "application/xhtml+xml"):
        raise ValueError("unsupported content type: " + content_type)
    return extract_html(raw)


def confirms_social_post(row, body):
    if row["source"] not in SOCIAL_PAGES:
        return True
    title = " ".join(row["title"].split())
    if title.lower() in ("instagram", "facebook", "x", "tiktok", "youtube", "reddit", "threads"):
        return False
    key = title[:16].lower()
    return len(key) >= 12 and key in " ".join(body.split()).lower()


def research(theme, sources=None, limit=5, max_pages=20, use_scrapling=False, search_backend="auto"):
    if not theme or not theme.strip():
        raise ValueError("theme is required")
    if not 1 <= limit <= 20 or not 0 <= max_pages <= 100:
        raise ValueError("limit must be 1-20 and max_pages must be 0-100")
    selected = list(dict.fromkeys(sources or SOURCES))
    backend = select_backend(search_backend)
    if backend == "brave" and not os.environ.get("BRAVE_SEARCH_API_KEY"):
        raise ValueError("BRAVE_SEARCH_API_KEY is required for Brave search")
    unknown = set(selected) - set(SOURCES)
    if unknown:
        raise ValueError("unknown source: " + ", ".join(sorted(unknown)))
    coverage = {name: {"status": "pending", "discovered": 0, "read": 0, "note": ""} for name in selected}
    discovered = []
    with ThreadPoolExecutor(max_workers=min(2 if backend == "exa" else 6, len(selected) or 1)) as pool:
        futures = {pool.submit(search_source, name, theme.strip(), limit, backend): name for name in selected}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results = future.result()
                coverage[name]["discovered"] = len(results)
                coverage[name]["status"] = "discovered" if results else "empty"
                for result in results:
                    result["source"] = name
                    discovered.append(result)
            except (HTTPError, URLError, TimeoutError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as exc:
                coverage[name].update(status="error", note=str(exc)[:200])
    seen = set()
    unique = []
    for result in discovered:
        clean = canonical_url(result["url"])
        if clean and clean not in seen:
            result["url"] = clean
            result["status"] = "discovery_only"
            result["text"] = ""
            result["error"] = ""
            seen.add(clean)
            unique.append(result)
    order = {name: index for index, name in enumerate(selected)}
    unique.sort(key=lambda row: order[row["source"]])
    by_source = {name: [row for row in unique if row["source"] == name] for name in selected}
    candidates = []
    for index in range(limit):
        for name in selected:
            if index < len(by_source[name]):
                candidates.append(by_source[name][index])
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(read_page, row["url"], use_scrapling): row for row in candidates[:max_pages]}
        for future in as_completed(futures):
            row = futures[future]
            try:
                title, body = future.result()
                if len(body) >= 120 and confirms_social_post(row, body):
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
                entry["note"] = "Search index results only; platform content was not verified."
    return {
        "theme": theme.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": backend + " search / Google News RSS; direct page retrieval",
        "coverage": coverage,
        "results": unique,
    }


def markdown_report(data):
    lines = ["# 調査結果: " + data["theme"], "", "取得日時: " + data["created_at"], "", "## 対象媒体", "", "| 媒体 | 状態 | 発見 | 本文確認 |", "|---|---|---:|---:|"]
    for name, state in data["coverage"].items():
        lines.append("| {} | {} | {} | {} |".format(name, state["status"], state["discovered"], state["read"]))
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
        lines.extend(["### " + (row.get("page_title") or row["title"]), "", "- 媒体: " + row["source"], "- 出典: " + row["url"], "- 抜粋: " + excerpt, ""])
    lines += ["## 発見のみ（本文未確認）", ""]
    unread = [row for row in data["results"] if row["status"] != "read"]
    if not unread:
        lines.append("ありません。")
    for row in unread:
        lines.append("- [{}] [{}]({}) — {}".format(row["source"], row["title"].replace("]", ""), row["url"], row["error"] or "検索結果のみ"))
    lines += ["", "## 調査上の制約", "", "検索結果の件数は媒体上の全投稿数を意味しません。ログイン、robots、レート制限などで本文を取得できなかった資料は根拠として扱っていません。", ""]
    return "\n".join(lines)
