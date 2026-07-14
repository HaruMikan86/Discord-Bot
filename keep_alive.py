"""
Repl.it 上で Bot を常時稼働させるための Web サーバ。
Flask で簡単なサーバを立て、別スレッドで動かすことで
discord Bot 本体(main.py)の実行を止めずに済ませる。

参考: Qiita「Repl.it を使った Discord Bot の構築と運用(無料) #Python」
"""

from flask import Flask, request
from threading import Thread

app = Flask("")


@app.route("/")
def home():
    url = request.base_url
    return f"このページのURLは {url} です"


def run():
    app.run(host="0.0.0.0", port=8080)


def keep_alive():
    t = Thread(target=run)
    t.start()
