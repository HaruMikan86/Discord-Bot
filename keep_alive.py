"""
Render 上で Bot を常時稼働させるための Web サーバ。
Flask で簡単なサーバを立て、別スレッドで動かすことで
discord Bot 本体(main.py)の実行を止めずに済ませる。

Googleカレンダー連携のOAuthコールバック(/oauth2callback)もここで受ける。
Google Cloud Console のOAuthクライアント設定で、
「承認済みのリダイレクトURI」にこのサーバの公開URL + /oauth2callback を登録しておくこと。

参考: Qiita「Render を使った Discord Bot の構築と運用(無料) #Python」
"""

from flask import Flask, request
from threading import Thread

import google_calendar

app = Flask("")


@app.route("/")
def home():
    url = request.base_url
    return f"このページのURLは {url} です"


@app.route("/oauth2callback")
def oauth2callback():
    """Googleの認可画面からのリダイレクトを受け取り、トークン交換まで行う"""
    error = request.args.get("error")
    if error:
        return f"認証がキャンセルまたは拒否されました: {error}"

    state = request.args.get("state")
    code = request.args.get("code")
    if not state or not code:
        return "不正なリクエストです(state または code がありません)。", 400

    try:
        google_calendar.handle_oauth_callback(state, code)
    except google_calendar.CalendarError as e:
        return f"連携に失敗しました: {e}", 400
    except Exception as e:
        # 想定外のエラー。詳細はRenderのログに残し、ユーザーには分かりやすい案内だけ返す
        print(f"[oauth2callback] 予期しないエラー: {e!r}")
        return (
            "予期しないエラーが発生しました。お手数ですが、"
            "もう一度 `/calendar connect` からやり直してください。",
            500,
        )

    return "✅ Googleカレンダーとの連携が完了しました。このタブは閉じてDiscordに戻ってください。"


def run():
    app.run(host="0.0.0.0", port=8080)


def keep_alive():
    t = Thread(target=run)
    t.start()
