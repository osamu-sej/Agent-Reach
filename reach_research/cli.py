import argparse
import json
from pathlib import Path
import sys

from .core import SOURCES, markdown_report, research


def main(argv=None):
    parser = argparse.ArgumentParser(description="テーマ別Web・SNSリサーチ")
    parser.add_argument("theme", help="調査テーマ")
    parser.add_argument("--sources", default=",".join(SOURCES), help="カンマ区切りの対象媒体")
    parser.add_argument("--limit", type=int, default=5, help="媒体ごとの発見件数 (1-20)")
    parser.add_argument("--max-pages", type=int, default=20, help="本文を取りに行く最大件数 (0-100)")
    parser.add_argument("--scrapling", action="store_true", help="通常のScrapling Fetcherを使用")
    parser.add_argument("--search-backend", choices=("auto", "exa", "brave", "bing"), default="auto", help="Web検索経路")
    parser.add_argument("--output", type=Path, default=Path("reports/latest"), help="出力先の接頭辞")
    args = parser.parse_args(argv)
    try:
        data = research(args.theme, [part.strip() for part in args.sources.split(",") if part.strip()], args.limit, args.max_pages, args.scrapling, args.search_backend)
    except (ValueError, ImportError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    json_path = args.output.with_suffix(".json")
    md_path = args.output.with_suffix(".md")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(data), encoding="utf-8")
    print(json_path)
    print(md_path)
    print(json.dumps(data["coverage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
