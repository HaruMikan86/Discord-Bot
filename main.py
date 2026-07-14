"""
実用系 Discord Bot - スラッシュコマンド対応版
プログラミング基礎 最終課題

新しいスラッシュコマンドを追加する手順:
  1. このファイルに @bot.tree.command(...) で関数を追加する
  2. Bot を再起動する
  3. GUILD_ID を設定していれば即座に、未設定ならグローバル同期
     (反映まで最大1時間かかることがある)でDiscord側に反映される
"""

import os

import discord
from discord import app_commands
from discord.ext import commands

from keep_alive import keep_alive

# ============================================================
# Bot初期設定
# ============================================================

intents = discord.Intents.default()
# message_content は今のところスラッシュコマンドには不要だが、
# 将来 on_message や prefix コマンドを併用する可能性を考えて有効化しておく
# (Discord Developer Portal の Bot タブで MESSAGE CONTENT INTENT を ON にしておくこと)
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 開発中のサーバー(ギルド)ID。環境変数 GUILD_ID に設定すると、
# そのサーバーだけスラッシュコマンドが即座に反映される。
# 未設定の場合はグローバル同期になり、全サーバーへの反映に最大1時間かかることがある。
# サーバーIDの調べ方: Discordの「設定→詳細設定→開発者モード」をON にしてから、
# サーバーアイコンを右クリック→「IDをコピー」
GUILD_ID = os.getenv("GUILD_ID")
GUILD_OBJECT = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None


# ============================================================
# 起動時イベント:スラッシュコマンドの同期
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ ログインしました: {bot.user}")

    try:
        if GUILD_OBJECT is not None:
            # 開発用サーバーに限定して即時反映(開発中はこちらが便利)
            bot.tree.copy_global_to(guild=GUILD_OBJECT)
            synced = await bot.tree.sync(guild=GUILD_OBJECT)
            print(f"🔄 スラッシュコマンドを{len(synced)}件、開発用サーバーに同期しました")
        else:
            # 全サーバー向けのグローバル同期
            synced = await bot.tree.sync()
            print(f"🔄 スラッシュコマンドを{len(synced)}件、グローバルに同期しました")
    except Exception as e:
        print(f"⚠️ コマンド同期でエラーが発生しました: {e}")


# ============================================================
# スラッシュコマンド
# ここに /boxplot や /hist などを今後追加していく
# ============================================================

@bot.tree.command(name="ping", description="Botの生存確認をします")
async def ping(interaction: discord.Interaction):
    """動作確認用の最小コマンド"""
    await interaction.response.send_message("pong!")


# ============================================================
# エラー処理(スラッシュコマンド用)
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "⚠️ このコマンドを実行する権限がありません。"
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"⚠️ クールダウン中です。{error.retry_after:.1f}秒後に再試行してください。"
    else:
        message = f"⚠️ エラーが発生しました: {error}"
        print(f"[app_command_error] {error!r}")

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ============================================================
# 起動
# ============================================================

keep_alive()  # Flaskサーバを別スレッドで起動(Renderを起こし続けるため)

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("環境変数 DISCORD_TOKEN が設定されていません。")

bot.run(TOKEN)
