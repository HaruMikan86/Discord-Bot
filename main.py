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
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from charts import (
    DataParseError,
    compute_basic_stats,
    create_boxplot_image,
    create_histogram_image,
    create_stats_image,
    parse_number_input,
)
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


@bot.tree.command(name="stats", description="数値データの基本統計量を計算し、ドットプロットで可視化します")
@app_commands.describe(
    data="カンマ・空白・改行区切りの数値 (例: 1,2,3,4,5)",
    file="数値が書かれたテキスト/CSVファイル(dataの代わりに指定可)",
)
async def stats(
    interaction: discord.Interaction,
    data: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    await interaction.response.defer()

    try:
        values = await parse_number_input(data, file, min_count=1)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    result = compute_basic_stats(values)

    embed = discord.Embed(title="📈 基本統計量", color=discord.Color.orange())
    embed.add_field(name="個数", value=str(result["個数"]))
    embed.add_field(name="平均", value=f'{result["平均"]:.3f}')
    embed.add_field(name="中央値", value=f'{result["中央値"]:.3f}')
    embed.add_field(name="最頻値", value=f'{result["最頻値"]:.3f}')
    embed.add_field(name="標準偏差", value=f'{result["標準偏差"]:.3f}')
    embed.add_field(name="最小値", value=f'{result["最小値"]:.3f}')
    embed.add_field(name="最大値", value=f'{result["最大値"]:.3f}')
    embed.add_field(name="第1四分位数", value=f'{result["第1四分位数"]:.3f}')
    embed.add_field(name="第3四分位数", value=f'{result["第3四分位数"]:.3f}')

    if len(values) >= 2:
        image_buf = create_stats_image(values)
        embed.set_image(url="attachment://stats.png")
        embed.set_footer(text=f"実行者: {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="stats.png"))
    else:
        # データが1個だけの場合はドットプロットの意味が薄いため、画像なしで統計量だけ返す
        embed.set_footer(text=f"実行者: {interaction.user.display_name} ｜ データが1個のため図は省略")
        await interaction.followup.send(embed=embed)


@bot.tree.command(name="boxplot", description="数値データから箱ひげ図を作成します")
@app_commands.describe(
    data="カンマ・空白・改行区切りの数値 (例: 1,2,3,4,5)",
    file="数値が書かれたテキスト/CSVファイル(dataの代わりに指定可)",
)
async def boxplot(
    interaction: discord.Interaction,
    data: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    await interaction.response.defer()  # 画像生成に時間がかかる場合があるため

    try:
        values = await parse_number_input(data, file, min_count=2)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    image_buf = create_boxplot_image(values)
    stats = compute_basic_stats(values)

    embed = discord.Embed(title="📦 箱ひげ図", color=discord.Color.blue())
    embed.add_field(name="個数", value=str(stats["個数"]))
    embed.add_field(name="平均", value=f'{stats["平均"]:.2f}')
    embed.add_field(name="中央値", value=f'{stats["中央値"]:.2f}')
    embed.add_field(name="標準偏差", value=f'{stats["標準偏差"]:.2f}')
    embed.add_field(name="最小値〜最大値", value=f'{stats["最小値"]:.2f} 〜 {stats["最大値"]:.2f}')
    embed.add_field(name="第1〜第3四分位数", value=f'{stats["第1四分位数"]:.2f} 〜 {stats["第3四分位数"]:.2f}')
    embed.set_image(url="attachment://boxplot.png")
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="boxplot.png"))


@bot.tree.command(name="hist", description="数値データからヒストグラムを作成します")
@app_commands.describe(
    data="カンマ・空白・改行区切りの数値 (例: 1,2,3,4,5)",
    file="数値が書かれたテキスト/CSVファイル(dataの代わりに指定可)",
    bins="ビン(区間)の数。省略時は10",
)
async def hist(
    interaction: discord.Interaction,
    data: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
    bins: Optional[int] = 10,
):
    await interaction.response.defer()

    try:
        values = await parse_number_input(data, file, min_count=2)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    image_buf = create_histogram_image(values, bins=bins or 10)

    embed = discord.Embed(title="📊 ヒストグラム", color=discord.Color.green())
    embed.set_image(url="attachment://hist.png")
    embed.set_footer(text=f"実行者: {interaction.user.display_name} ｜ 個数: {len(values)}")

    await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="hist.png"))


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
