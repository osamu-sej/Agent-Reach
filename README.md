# Agent-Reach: テーマ別Web・SNSリサーチ

ユーザーがテーマを指定すると、Web、ニュース、SNSなどを横断検索し、取得できた本文と検索結果だけの資料を区別して記録する読み取り専用のCLIです。現段階の成果物は**出典付きの調査台帳**です。検索範囲は各検索エンジンのインデックスに依存し、SNSの全投稿を網羅したと主張しません。

## 実行

Python 3.9以上で動作します。標準設定はNode.jsの `npx` から固定版の `mcporter` を起動し、Agent Reachも採用する公開Exa MCPで検索します。初回にnpmパッケージを取得します。Node.jsがない環境ではBing RSSに切り替わります。

```bash
python3 -m reach_research "生成AI 小売業" --limit 5 --output reports/retail-ai
```

実行前のローカル診断:

```bash
python3 -m reach_research --doctor
```

診断はインストールや設定の有無を示します。各媒体に実際にログインしているか、検索できるかまでは保証しません。

対象を絞る場合:

```bash
python3 -m reach_research "生成AI 小売業" --sources web,news,x,reddit,youtube --max-pages 20
```

標準の `balanced` は媒体ごとに2つの検索語を実行します。短時間の `--depth quick`、より広い `--depth deep`、追加語句の `--add-query "テーマ 別表記"` も選べます。実行した全検索語と失敗はJSONの `coverage.*.queries` に残ります。`--limit` は検索語ごとの最大発見件数です。

既にOpenCLIとChrome拡張を設定し、ログイン済みセッションをこの調査に使う場合は `--opencli` を指定します。X、Reddit、Instagram、Facebookを媒体内でも検索します。検索結果にURLがない場合は記録できず、投稿本文の検証には公開ページの取得も必要です。このオプションはログインやCookie取得を自動実行しません。

GitHub CLI (`gh`) がある場合はリポジトリ検索を、`yt-dlp` がある場合はYouTube検索を、標準で併用します。どちらも読み取り専用です。`--no-direct` で停止できます。直接検索もタイトル・URLの発見であり、本文の取得状態とは分けて記録します。

`reports/retail-ai.json` に検索結果、取得状態、本文、エラーを保存し、同名の `.md` に閲覧用の一覧を作ります。`read` はページ本文を抽出できた状態、`discovery_only` は検索結果として見つかっただけの状態です。SNSは検索結果のタイトルと取得本文が対応する場合のみ `read` とします。`read` はテーマとの関連性や記述内容の真偽を保証しません。出力は `.gitignore` で除外されます。

Scraplingの通常Fetcherを使う場合は、Python 3.10以上の環境で `pip install -e '.[scrapling]'` を実行し、`--scrapling` を付けます。基本機能はPython 3.9でも動きます。ログイン突破、CAPTCHA解決、プロキシ切替はこのオプションに含めていません。

### MacでPython 3.12を使う

既存のシステムPythonを変更せず、このリポジトリ専用の環境を作れます。リポジトリのルートで実行してください。

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/agent-reach-uv-install.sh
env UV_UNMANAGED_INSTALL="$PWD/.tools" sh /tmp/agent-reach-uv-install.sh
.tools/uv python install 3.12
.tools/uv venv --python 3.12 .venv312
.tools/uv pip install --python .venv312/bin/python -e '.[scrapling]'
.venv312/bin/python --version
.venv312/bin/reach-research --doctor
```

この手順では `python3` の既定値は変わりません。Agent-ReachをPython 3.12で動かすときは `.venv312/bin/reach-research` を使います。ページ側のTLS証明書エラーなどで取得が失敗した場合、その資料は `discovery_only` として残ります。

Brave Search APIのキーを `BRAVE_SEARCH_API_KEY` 環境変数に設定すると、優先してBraveを使います。`--search-backend exa|brave|bing` で明示的に選択できます。Bingの無関係な結果はタイトルとスニペットで除外します。外部検索サービスの利用料金・上限は各サービスの条件に従います。

## 現在の収集経路

| 媒体 | 発見方法 | 本文確認 |
|---|---|---|
| Web | Exa MCP、Brave Search API（キーあり）、またはBing RSS | 公開HTMLを直接取得 |
| ニュース | Google News RSS | リンク先に到達できた場合のみ |
| X、Reddit、YouTube、GitHub、Instagram、Threads、TikTok、Facebook | Exa、Brave、Bingの媒体別検索。GitHubは`gh`、YouTubeは`yt-dlp`を併用。X、Reddit、Instagram、Facebookは任意でOpenCLIも利用 | 公開ページから本文を取得できた場合のみ |

媒体別検索では、返されたURLのドメインが対象媒体と一致するか検査します。ログインが必要な投稿や検索エンジンに載らない投稿は漏れます。取得失敗は `coverage` と各資料の `error` に残します。取得済みの検索スニペットを投稿本文として扱いません。リクエストは公開URLに限り、非公開IP、ローカルアドレス、非HTTP URLは拒否します。

## 参考プロジェクトとの関係

- [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach): 媒体ごとの接続状態を診断し、複数の取得経路を使い分ける考え方を参考にしました。次の段階で、ユーザーが設定済みの公式APIや既存ブラウザセッションを明示的なコネクタとして追加します。
- [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling): 通常のページ取得を任意の依存として用意しました。動的ページ対応は今後の課題です。
- [whaleyxbt/patchright-enhanced](https://github.com/whaleyxbt/patchright-enhanced): ブラウザ取得の参考候補です。現段階では組み込んでいません。ライセンス表示がないため、コードの転用も行っていません。

3プロジェクトのコードは複製していません。サイトが明示するアクセス制限や認証が必要な場合は結果に記録し、別の許可された取得経路を設定する想定です。

## 次の段階

1. 期間・地域・言語・除外語を指定し、検索語の展開を改善する。
2. 公式APIや既存のログイン済みセッションを使うSNSコネクタを増やす。
3. 同一主張の出典突合、日付抽出、引用を備えた要約を追加する。
4. 収集の予算、再実行、差分監視を追加する。

現段階ではOpenCLIによるSNS検索の実機検証、主張の自動検証、文章要約までは実装していません。
