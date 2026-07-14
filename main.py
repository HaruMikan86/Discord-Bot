"""
実用系 Discord Bot - 最小構成
プログラミング基礎 最終課題

まずはRepl.it上でログインして動くことを確認するための最小コード。
動作確認できたら、ここに !remind(リマインダー) !poll(投票) !addrole(ロール管理)
などのコマンドを順次追加していく。
"""

import os

import discord
from discord.ext import commands

from keep_alive import keep_alive

# ============================================================
# Bot初期設定
# ============================================================

# メッセージ内容を読み取るために必要(Discord Developer Portalの
# Bot タブで MESSAGE CONTENT INTENT を ON にしておくこと)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# 動作確認用の最小イベント・コマンド
# ============================================================


@bot.event
async def on_ready():
    print(f"✅ ログインしました: {bot.user}")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """Botが生きているか確認するための最小コマンド"""
    await ctx.send("pong!")


# ============================================================
# 起動
# ============================================================

keep_alive()  # Flaskサーバを別スレッドで起動(Repl.itを起こし続けるため)

TOKEN = os.getenv("DISCORD_TOKEN")

try:
    bot.run(TOKEN)
except Exception as e:
    print(f"⚠️ 起動エラー: {e}")
    # Repl.itのコンテナを再起動しIPアドレスをリセットする
    # (同一IPからのアクセスが続くとDiscord側でブロックされることがあるため)
    os.system("kill 1")
