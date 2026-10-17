# PigeonPost-BOT

統計・グラフ作成、リマインダー/ToDo、Googleカレンダー連携、Steam所持ゲーム確認ができる多機能Discord Botです。
Python / discord.py(スラッシュコマンド)で作られており、Renderの無料プランで常時稼働させる構成です。

## 機能一覧

コマンドの一覧は、Discord上で `/help` を実行すると自動生成されて表示されます(`/help category:steam` のように絞り込みも可能)。

| カテゴリ | コマンド | 内容 |
| --- | --- | --- |
| 統計・グラフ | `/stats` `/boxplot` `/hist` `/corr` `/plot` `/binom` `/normal` `/solve` `/encode` | 基本統計量、箱ひげ図、ヒストグラム、相関分析、関数のグラフ、確率分布、方程式、UTF-8変換 |
| リマインダー | `/remind set` `list` `cancel` | 相対時間(`10m` `1h30m`)や日時(`2026-09-20 21:00`)での通知予約 |
| ToDo | `/todo add` `list` `done` `remove` `clear` | 個人用ToDoリスト |
| Googleカレンダー連携 | `/calendar connect` `status` `disconnect` `share` | OAuth認証での連携、空き状況の共有設定 |
| 予定管理 | `/schedule add` `list` `remove` `freebusy` | Googleカレンダーの予定の登録・日付ごとの一覧・ドロップダウンでの削除・相手の空き状況確認 |
| Steam連携 | `/steam link` `unlink` `games` `common` | 所持ゲームの確認、ボイスチャンネル内メンバーの共通所持ゲーム表示 |
| 管理者向け | `/admin calendar_status` | 全員のカレンダー連携・共有状況の確認(Botの所有者のみ実行可能) |

## 環境変数

| 変数名 | 必須 | 説明 |
| --- | --- | --- |
| `DISCORD_TOKEN` | 必須 | Discord BotのToken |
| `GUILD_ID` | 任意 | 開発用サーバーのID。設定するとスラッシュコマンドがそのサーバーに即時反映される(未設定時はグローバル同期で、反映に最大1時間かかる) |
| `GOOGLE_CLIENT_ID` | カレンダー機能に必要 | Google CloudのOAuthクライアントID |
| `GOOGLE_CLIENT_SECRET` | カレンダー機能に必要 | 同クライアントシークレット |
| `GOOGLE_REDIRECT_URI` | カレンダー機能に必要 | 例: `https://<アプリ名>.onrender.com/oauth2callback`(Google Cloud Consoleの「承認済みのリダイレクトURI」と完全一致させる) |
| `STEAM_API_KEY` | Steam機能に必要 | [Steam Web APIキー](https://steamcommunity.com/dev/apikey)(無料で発行可能) |
| `PYTHONUNBUFFERED` | 推奨(Render) | `1` に設定すると、`print()`の内容がRenderのログにすぐ表示される |

Discord Developer Portalの Bot タブで **MESSAGE CONTENT INTENT** をONにしてください。

## セットアップ

### ローカルで動かす

```bash
pip install -r requirements.txt
export DISCORD_TOKEN=あなたのBotのToken
python main.py
```

起動時に、死活監視用のFlaskサーバー(ポート8080)も同時に立ち上がります。

### Renderにデプロイする

1. このリポジトリをRenderの Web Service(Docker)として作成する
2. 上記の環境変数をRenderの Environment に設定する
3. UptimeRobotなどで、公開URLに定期的にアクセスさせてスリープを防ぐ(15分間アクセスが無いと無料プランは停止するため)

参考: [Render を使った Discord Bot の構築と運用(無料) - Qiita](https://qiita.com/Erytheia/items/2f64c06d6d8a4f802390)

### Googleカレンダー連携の準備

1. Google Cloud Consoleでプロジェクトを作成し、Google Calendar APIを有効化する
2. OAuth同意画面を設定し、自分のGoogleアカウントを「テストユーザー」に追加する
3. 「ウェブアプリケーション」のOAuthクライアントIDを作成し、リダイレクトURIに `https://<アプリ名>.onrender.com/oauth2callback` を登録する
4. 発行されたクライアントIDとシークレットを環境変数に設定する

要求するスコープは `calendar.events`(予定の読み書き)と `calendar.freebusy`(空き状況のみの閲覧)の2つです。

## データの保存について

リマインダー・ToDo・Googleのリフレッシュトークン・Steamの紐付け情報は、SQLite(`data/bot.db`)に保存されます。

- **Renderの無料プランでは永続ディスクが使えないため、コードを更新して再デプロイするとこのファイルは消えます**(その場合、カレンダー連携のやり直しとリマインダー・ToDoの再登録が必要になります)。無通信によるスリープ→再起動では消えません。
- `data/` には認証情報が含まれるため、`.gitignore` で除外しています。**絶対にコミットしないでください。**

## プライバシーについて

- Googleカレンダーの予定は、本人のみが見られます。他人の空き状況(予定の詳細は含まない)は、本人が `/calendar share` で許可した場合のみ `/schedule freebusy` で確認できます。
- Steamの所持ゲームは、Steam側で「ゲームの詳細」を公開にしているアカウントのみ取得できます。`/steam link` はアクセス許可を与える操作ではなく、DiscordアカウントとSteamIDを紐付けるだけです(非公開のアカウントは取得できません)。
- `/admin` 系のコマンドは、Botの所有者のみ実行できます。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `main.py` | Botの本体。スラッシュコマンドの定義、リマインダーの監視ループ、`/help`の自動生成 |
| `charts.py` | 数値データの解析、統計量の計算、グラフ画像の生成 |
| `productivity.py` | リマインダー・ToDoの保存処理、日時・時間指定のパース |
| `google_calendar.py` | Googleカレンダー連携(OAuth認証、予定の読み書き、空き状況) |
| `steam_integration.py` | Steam連携(アカウント紐付け、所持ゲームの取得) |
| `keep_alive.py` | 死活監視用のFlaskサーバー、Google OAuthのコールバック(`/oauth2callback`) |
| `Dockerfile` | Renderでの実行用 |
