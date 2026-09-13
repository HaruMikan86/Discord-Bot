"""
実用系 Discord Bot - スラッシュコマンド対応版
プログラミング基礎 最終課題

新しいスラッシュコマンドを追加する手順:
  1. このファイルに @bot.tree.command(...) で関数を追加する
  2. Bot を再起動する
  3. GUILD_ID を設定していれば即座に、未設定ならグローバル同期
     (反映まで最大1時間かかることがある)でDiscord側に反映される
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from charts import (
    DataParseError,
    compute_basic_stats,
    compute_correlation,
    create_binomial_plot_image,
    create_boxplot_image,
    create_function_plot_image,
    create_histogram_image,
    create_normal_plot_image,
    create_scatter_image,
    create_solution_plot_image,
    create_stats_image,
    encode_unicode_string,
    parse_and_solve_equation,
    parse_function_expression,
    parse_number_input,
    parse_paired_number_input,
)
from keep_alive import keep_alive
import google_calendar
import productivity

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
# リマインダーの監視ループ
# 30秒おきにDBを確認し、通知時刻を過ぎた未通知のリマインダーを送信する
# ============================================================

@tasks.loop(seconds=30)
async def reminder_check_loop():
    due = await productivity.due_reminders()
    for reminder in due:
        channel = bot.get_channel(reminder.channel_id)
        try:
            if channel is None:
                channel = await bot.fetch_channel(reminder.channel_id)
            await channel.send(
                f"⏰ <@{reminder.user_id}> リマインダーの時間です: {reminder.message}"
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            # チャンネル削除・権限喪失などで送信できない場合はログだけ残す
            print(f"⚠️ リマインダー送信に失敗しました (id={reminder.id}): {e}")
        finally:
            # 送信の成否に関わらず、同じ内容を送り続けないよう既読扱いにする
            await productivity.mark_notified(reminder.id)


@reminder_check_loop.before_loop
async def before_reminder_check_loop():
    await bot.wait_until_ready()


# ============================================================
# 起動時イベント:DB初期化・リマインダーループ開始・スラッシュコマンドの同期
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ ログインしました: {bot.user}")

    await productivity.init_db()
    google_calendar.init_db()  # 同期関数(sqlite3を直接使用)
    if not reminder_check_loop.is_running():
        reminder_check_loop.start()

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


@bot.tree.command(name="corr", description="2つの数値データの相関係数を計算し、散布図を作成します")
@app_commands.describe(
    x="xの値(カンマ・空白・改行区切りの数値)",
    y="yの値(xと同じ個数で指定)",
    file="「x,y」の形式で1行に1組ずつ書かれたファイル(x・yの代わりに指定可)",
)
async def corr(
    interaction: discord.Interaction,
    x: Optional[str] = None,
    y: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    await interaction.response.defer()

    try:
        x_values, y_values = await parse_paired_number_input(x, y, file)
        result = compute_correlation(x_values, y_values)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    image_buf = create_scatter_image(x_values, y_values, result["r"])

    embed = discord.Embed(title="📉 相関分析", color=discord.Color.purple())
    embed.add_field(name="データ数", value=str(result["n"]))
    embed.add_field(name="相関係数 r", value=f'{result["r"]:.3f}')
    embed.add_field(name="判定", value=result["label"])
    embed.set_image(url="attachment://scatter.png")
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="scatter.png"))


@bot.tree.command(name="plot", description="xの関数の式からグラフを作成します(例: x**2-3*x+2)")
@app_commands.describe(
    expr="xの関数の式 (例: x**2-3*x+2, sin(x), exp(x), sqrt(x), 1/x)",
    x_min="xの表示範囲の最小値(省略時: -10)",
    x_max="xの表示範囲の最大値(省略時: 10)",
)
async def plot(
    interaction: discord.Interaction,
    expr: str,
    x_min: float = -10.0,
    x_max: float = 10.0,
):
    await interaction.response.defer()

    try:
        parsed_expr = parse_function_expression(expr)
        image_buf = create_function_plot_image(parsed_expr, x_min=x_min, x_max=x_max)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    embed = discord.Embed(
        title="📐 関数のグラフ",
        description=f"y = {parsed_expr}",
        color=discord.Color.teal(),
    )
    embed.set_image(url="attachment://plot.png")
    embed.set_footer(text=f"実行者: {interaction.user.display_name} ｜ 範囲: [{x_min}, {x_max}]")

    await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="plot.png"))


@bot.tree.command(name="encode", description="UnicodeコードポイントをUTF-8のバイト列に変換します")
@app_commands.describe(code="コードポイント (例: U+03A9)")
async def encode(interaction: discord.Interaction, code: str):
    try:
        result = encode_unicode_string(code)
    except DataParseError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return

    embed = discord.Embed(title="🔤 UTF-8エンコード", color=discord.Color.dark_teal())
    embed.add_field(name="入力", value=code, inline=True)
    embed.add_field(name="UTF-8バイト列(16進)", value=f"`{result}`", inline=True)
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="binom", description="二項分布 B(n, p) をグラフで可視化します")
@app_commands.describe(
    n="試行回数(1〜500)",
    p="成功確率(0〜1)",
)
async def binom(interaction: discord.Interaction, n: int, p: float):
    await interaction.response.defer()

    try:
        image_buf = create_binomial_plot_image(n, p)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    mean = n * p
    variance = n * p * (1 - p)

    embed = discord.Embed(title="🎲 二項分布", description=f"B(n={n}, p={p})", color=discord.Color.gold())
    embed.add_field(name="平均(np)", value=f"{mean:.3f}")
    embed.add_field(name="分散(np(1-p))", value=f"{variance:.3f}")
    embed.set_image(url="attachment://binom.png")
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="binom.png"))


@bot.tree.command(name="normal", description="正規分布 N(平均, 標準偏差²) をグラフで可視化します")
@app_commands.describe(
    mean="平均",
    std="標準偏差(正の値)",
)
async def normal(interaction: discord.Interaction, mean: float, std: float):
    await interaction.response.defer()

    try:
        image_buf = create_normal_plot_image(mean, std)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    embed = discord.Embed(
        title="🔔 正規分布",
        description=f"N({mean}, {std}²)",
        color=discord.Color.blurple(),
    )
    embed.set_image(url="attachment://normal.png")
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="normal.png"))


@bot.tree.command(name="solve", description="xの方程式を解きます(例: x**2-4=0)")
@app_commands.describe(equation="xの方程式 (例: x**2-4=0, 2*x+3=0, sin(x)=0)")
async def solve(interaction: discord.Interaction, equation: str):
    await interaction.response.defer()

    try:
        solutions = parse_and_solve_equation(equation)
    except DataParseError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    solutions_text = ", ".join(str(s) for s in solutions)
    embed = discord.Embed(title="🧮 方程式の解", color=discord.Color.dark_gold())
    embed.add_field(name="方程式", value=f"`{equation}`", inline=False)
    embed.add_field(name="解", value=f"`{solutions_text}`", inline=False)
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    image_buf = create_solution_plot_image(solutions)
    if image_buf is not None:
        embed.set_image(url="attachment://solve.png")
        await interaction.followup.send(embed=embed, file=discord.File(image_buf, filename="solve.png"))
    else:
        embed.set_footer(text=f"実行者: {interaction.user.display_name} ｜ 複素数解のため図は省略")
        await interaction.followup.send(embed=embed)


# ============================================================
# /remind グループ: リマインダー機能
# ============================================================

remind_group = app_commands.Group(name="remind", description="リマインダー機能")


@remind_group.command(name="set", description="指定した時間後、または日時にリマインドします")
@app_commands.describe(
    when="いつ通知するか (例: `10m`=10分後, `1h30m`, `3d`, `2026-09-20 21:00`, `21:00`)",
    message="リマインドしてほしい内容",
)
async def remind_set(interaction: discord.Interaction, when: str, message: str):
    try:
        remind_at = productivity.parse_when(when)
        reminder_id = await productivity.add_reminder(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            message=message,
            remind_at=remind_at,
        )
    except productivity.ProductivityError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return

    embed = discord.Embed(title="⏰ リマインダーを設定しました", color=discord.Color.orange())
    embed.add_field(name="内容", value=message, inline=False)
    embed.add_field(name="通知予定時刻", value=productivity.format_jst(remind_at), inline=False)
    embed.set_footer(text=f"ID: {reminder_id} ｜ 実行者: {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)


@remind_group.command(name="list", description="設定中の自分のリマインダー一覧を表示します")
async def remind_list(interaction: discord.Interaction):
    reminders = await productivity.list_reminders(interaction.user.id)

    if not reminders:
        await interaction.response.send_message("設定中のリマインダーはありません。", ephemeral=True)
        return

    embed = discord.Embed(title="⏰ 設定中のリマインダー", color=discord.Color.orange())
    for r in reminders[:20]:
        embed.add_field(
            name=f"ID: {r.id} ｜ {productivity.format_jst(r.remind_at)}",
            value=r.message,
            inline=False,
        )
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@remind_group.command(name="cancel", description="指定したIDのリマインダーを取り消します")
@app_commands.describe(reminder_id="取り消すリマインダーのID(`/remind list`で確認できます)")
async def remind_cancel(interaction: discord.Interaction, reminder_id: int):
    ok = await productivity.cancel_reminder(interaction.user.id, reminder_id)
    if ok:
        await interaction.response.send_message(
            f"🗑️ リマインダー(ID: {reminder_id})を取り消しました。", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "⚠️ 指定したIDのリマインダーが見つかりませんでした(すでに通知済み、または他人のリマインダーの可能性があります)。",
            ephemeral=True,
        )


bot.tree.add_command(remind_group)


# ============================================================
# /todo グループ: ToDoリスト機能
# ============================================================

todo_group = app_commands.Group(name="todo", description="ToDoリスト機能")


@todo_group.command(name="add", description="ToDoリストに項目を追加します")
@app_commands.describe(content="追加するタスクの内容")
async def todo_add(interaction: discord.Interaction, content: str):
    try:
        todo_id = await productivity.add_todo(interaction.user.id, content)
    except productivity.ProductivityError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ 追加しました(ID: {todo_id}): {content}", ephemeral=True)


@todo_group.command(name="list", description="自分のToDoリストを表示します")
@app_commands.describe(include_done="完了済みのタスクも表示するか(省略時は表示しない)")
async def todo_list(interaction: discord.Interaction, include_done: bool = False):
    items = await productivity.list_todos(interaction.user.id, include_done=include_done)

    if not items:
        await interaction.response.send_message("ToDoリストは空です。", ephemeral=True)
        return

    lines = [f'{"✅" if item.done else "🔲"} `{item.id}` {item.content}' for item in items]

    embed = discord.Embed(title="📝 ToDoリスト", description="\n".join(lines), color=discord.Color.blue())
    embed.set_footer(text=f"実行者: {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@todo_group.command(name="done", description="指定したIDのタスクを完了にします")
@app_commands.describe(todo_id="完了にするタスクのID(`/todo list`で確認できます)")
async def todo_done(interaction: discord.Interaction, todo_id: int):
    ok = await productivity.complete_todo(interaction.user.id, todo_id)
    if ok:
        await interaction.response.send_message(f"✅ タスク(ID: {todo_id})を完了にしました。", ephemeral=True)
    else:
        await interaction.response.send_message(
            "⚠️ 指定したIDのタスクが見つかりませんでした(すでに完了済み、または他人のタスクの可能性があります)。",
            ephemeral=True,
        )


@todo_group.command(name="remove", description="指定したIDのタスクを削除します")
@app_commands.describe(todo_id="削除するタスクのID(`/todo list`で確認できます)")
async def todo_remove(interaction: discord.Interaction, todo_id: int):
    ok = await productivity.delete_todo(interaction.user.id, todo_id)
    if ok:
        await interaction.response.send_message(f"🗑️ タスク(ID: {todo_id})を削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ 指定したIDのタスクが見つかりませんでした。", ephemeral=True)


@todo_group.command(name="clear", description="完了済みのタスクをまとめて削除します")
async def todo_clear(interaction: discord.Interaction):
    count = await productivity.clear_done_todos(interaction.user.id)
    await interaction.response.send_message(f"🧹 完了済みのタスクを{count}件削除しました。", ephemeral=True)


bot.tree.add_command(todo_group)


# ============================================================
# /calendar グループ: Googleカレンダー連携(認証まわり)
# ============================================================

calendar_group = app_commands.Group(name="calendar", description="Googleカレンダー連携")


@calendar_group.command(name="connect", description="Googleカレンダーと連携するための認証リンクを発行します")
async def calendar_connect(interaction: discord.Interaction):
    try:
        auth_url = await asyncio.to_thread(google_calendar.build_authorize_url, interaction.user.id)
    except google_calendar.CalendarError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return

    await interaction.response.send_message(
        "以下のリンクからGoogleアカウントで認証してください(10分間有効です)。\n"
        "**このリンクはあなた専用です。他の人と共有しないでください。**\n"
        f"{auth_url}",
        ephemeral=True,
    )


@calendar_group.command(name="disconnect", description="Googleカレンダーとの連携を解除します")
async def calendar_disconnect(interaction: discord.Interaction):
    ok = await asyncio.to_thread(google_calendar.disconnect, interaction.user.id)
    if ok:
        await interaction.response.send_message("🔌 Googleカレンダーとの連携を解除しました。", ephemeral=True)
    else:
        await interaction.response.send_message("連携されていません。", ephemeral=True)


@calendar_group.command(name="status", description="Googleカレンダーとの連携状況を確認します")
async def calendar_status(interaction: discord.Interaction):
    connected = await asyncio.to_thread(google_calendar.is_connected, interaction.user.id)
    if not connected:
        await interaction.response.send_message(
            "未連携です。`/calendar connect` で連携できます。", ephemeral=True
        )
        return

    shared = await asyncio.to_thread(google_calendar.is_freebusy_shared, interaction.user.id)
    share_status = "共有中" if shared else "共有していません"
    await interaction.response.send_message(
        f"✅ Googleカレンダーと連携済みです。\n"
        f"空き状況の共有(`/schedule freebusy`用): {share_status}(`/calendar share`で切り替えられます)",
        ephemeral=True,
    )


@calendar_group.command(name="share", description="自分の空き状況を他の人が確認できるようにするか設定します")
@app_commands.describe(
    enabled="他の人が`/schedule freebusy`であなたの空き状況(予定の詳細は含まない)を確認できるようにするか"
)
async def calendar_share(interaction: discord.Interaction, enabled: bool):
    ok = await asyncio.to_thread(google_calendar.set_share_freebusy, interaction.user.id, enabled)
    if not ok:
        await interaction.response.send_message(
            "Googleカレンダーと連携されていません。先に `/calendar connect` で連携してください。",
            ephemeral=True,
        )
        return

    if enabled:
        await interaction.response.send_message(
            "✅ 他の人が `/schedule freebusy` であなたの空き状況(タイトルや場所などの詳細は含みません)を"
            "確認できるようになりました。`/calendar share enabled:False` でいつでも止められます。",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message("🔒 空き状況の共有を停止しました。", ephemeral=True)


bot.tree.add_command(calendar_group)


# ============================================================
# /schedule グループ: Googleカレンダー上の予定の登録・確認・削除
# ============================================================

schedule_group = app_commands.Group(name="schedule", description="Googleカレンダーと連携した予定管理")


@schedule_group.command(name="add", description="Googleカレンダーに予定を登録します")
@app_commands.describe(
    title="予定のタイトル",
    start="開始日時 (例: `2026-09-20 21:00`, `21:00`, `10m`のような相対時間も可。all_day時は`2026-09-20`のような日付)",
    duration="所要時間 (例: `1h`, `30m`。省略時は1時間。all_day指定時は無視されます)",
    location="場所(省略可)",
    description="メモ・説明(省略可)",
    all_day="終日の予定として登録するか(省略時: しない)",
)
async def schedule_add(
    interaction: discord.Interaction,
    title: str,
    start: str,
    duration: str = "1h",
    location: Optional[str] = None,
    description: Optional[str] = None,
    all_day: bool = False,
):
    await interaction.response.defer(ephemeral=True)

    try:
        if all_day:
            start_date = productivity.parse_date_jst(start)
            start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=productivity.JST)
            end_dt = start_dt + timedelta(days=1)
        else:
            start_dt = productivity.parse_when(start)
            end_dt = start_dt + productivity.parse_duration(duration)
    except productivity.ProductivityError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    try:
        event = await asyncio.to_thread(
            google_calendar.create_event,
            interaction.user.id,
            title,
            start_dt,
            end_dt,
            all_day,
            location,
            description,
        )
    except google_calendar.CalendarError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    embed = discord.Embed(title="🗓️ 予定を登録しました", color=discord.Color.green())
    embed.add_field(name="タイトル", value=event.summary, inline=False)
    if event.all_day:
        embed.add_field(name="日付", value=f'{event.start.strftime("%Y-%m-%d")}(終日)', inline=False)
    else:
        embed.add_field(
            name="日時",
            value=f'{event.start.strftime("%Y-%m-%d %H:%M")} 〜 {event.end.strftime("%H:%M")} (JST)',
            inline=False,
        )
    if event.location:
        embed.add_field(name="場所", value=event.location, inline=False)
    if event.html_link:
        embed.add_field(name="Googleカレンダーで開く", value=event.html_link, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@schedule_group.command(name="list", description="Googleカレンダーの予定を一覧表示します")
@app_commands.describe(
    when="表示する範囲の起点。省略時は今日 (例: `today`, `明日`, `2026-09-20`)",
    days="何日分表示するか(省略時: 7日、最大31日)",
    share="このチャンネルの全員に見えるように投稿するか(省略時: 自分にしか見えない)",
)
async def schedule_list(
    interaction: discord.Interaction,
    when: Optional[str] = None,
    days: int = 7,
    share: bool = False,
):
    ephemeral = not share
    await interaction.response.defer(ephemeral=ephemeral)

    days = max(1, min(days, 31))
    try:
        start_date = productivity.parse_date_jst(when) if when else productivity.parse_date_jst("today")
    except productivity.ProductivityError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=ephemeral)
        return

    range_start = datetime.combine(start_date, datetime.min.time(), tzinfo=productivity.JST)
    range_end = range_start + timedelta(days=days)

    try:
        events = await asyncio.to_thread(
            google_calendar.list_events, interaction.user.id, range_start, range_end
        )
    except google_calendar.CalendarError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=ephemeral)
        return

    if not events:
        await interaction.followup.send(
            f"{range_start.strftime('%Y-%m-%d')} から{days}日間、予定はありません。", ephemeral=ephemeral
        )
        return

    # 日付ごとにグループ化する(events は開始時刻順に並んでいるので、
    # 同じ日付は必ず連続して現れる)
    grouped: List[Tuple[date, List[google_calendar.ScheduleEvent]]] = []
    for ev in events:
        day = ev.start.date()
        if grouped and grouped[-1][0] == day:
            grouped[-1][1].append(ev)
        else:
            grouped.append((day, [ev]))

    embed = discord.Embed(
        title=f"🗓️ 予定一覧({range_start.strftime('%Y-%m-%d')} から{days}日間)",
        color=discord.Color.green(),
    )
    for day, day_events in grouped:
        header = f"{day.strftime('%m/%d')}({google_calendar.weekday_ja(day)})"

        lines = []
        for ev in day_events:
            icon = "📌" if ev.all_day else "🕒"
            time_part = "終日" if ev.all_day else f'{ev.start.strftime("%H:%M")}〜{ev.end.strftime("%H:%M")}'
            line = f"{icon} {time_part} {ev.summary}"
            if ev.location:
                line += f"\n　📍 {ev.location}"
            lines.append(line)

        value = "\n".join(lines)
        if len(value) > 1024:
            value = value[:1000] + "\n…(表示しきれない予定があります)"

        embed.add_field(name=header, value=value, inline=False)

    embed.set_footer(text=f"実行者: {interaction.user.display_name}")
    await interaction.followup.send(embed=embed, ephemeral=ephemeral)


# ============================================================
# /schedule remove: ドロップダウンから選んで予定を削除する
# ============================================================

_REMOVE_SELECT_MAX_OPTIONS = 25  # DiscordのSelectメニューは最大25件まで
_REMOVE_VIEW_TIMEOUT_SECONDS = 120


class ScheduleRemoveView(discord.ui.View):
    """/schedule remove で使う、削除したい予定を選ぶためのドロップダウン付きView"""

    def __init__(self, discord_user_id: int, events: List[google_calendar.ScheduleEvent]):
        super().__init__(timeout=_REMOVE_VIEW_TIMEOUT_SECONDS)
        self.discord_user_id = discord_user_id
        self.message: Optional[discord.WebhookMessage] = None
        self._events_by_id = {ev.event_id: ev for ev in events}

        select = discord.ui.Select(
            placeholder="削除する予定を選んでください",
            options=[
                discord.SelectOption(
                    label=f"{google_calendar.format_event_time_range(ev)} {ev.summary}"[:100],
                    description=(ev.location or None) and ev.location[:100],
                    value=ev.event_id,
                )
                for ev in events
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        event_id = interaction.data["values"][0]
        ev = self._events_by_id.get(event_id)

        try:
            await asyncio.to_thread(google_calendar.delete_event, self.discord_user_id, event_id)
        except google_calendar.CalendarError as e:
            await interaction.response.edit_message(content=f"⚠️ {e}", view=None, embed=None)
            self.stop()
            return

        title = ev.summary if ev is not None else "予定"
        await interaction.response.edit_message(content=f"🗑️ 「{title}」を削除しました。", view=None, embed=None)
        self.stop()

    async def on_timeout(self):
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(
                content="⌛ タイムアウトしました。もう一度 `/schedule remove` を実行してください。",
                view=self,
            )
        except discord.HTTPException:
            pass  # メッセージが既に消えている等は無視してよい


@schedule_group.command(name="remove", description="予定を選んで削除します")
@app_commands.describe(
    when="検索する範囲の起点。省略時は今日 (例: `today`, `明日`, `2026-09-20`)",
    days="何日分から探すか(省略時: 30日、最大60日)",
)
async def schedule_remove(
    interaction: discord.Interaction,
    when: Optional[str] = None,
    days: int = 30,
):
    await interaction.response.defer(ephemeral=True)

    days = max(1, min(days, 60))
    try:
        start_date = productivity.parse_date_jst(when) if when else productivity.parse_date_jst("today")
    except productivity.ProductivityError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    range_start = datetime.combine(start_date, datetime.min.time(), tzinfo=productivity.JST)
    range_end = range_start + timedelta(days=days)

    try:
        events = await asyncio.to_thread(
            google_calendar.list_events,
            interaction.user.id,
            range_start,
            range_end,
            _REMOVE_SELECT_MAX_OPTIONS,
        )
    except google_calendar.CalendarError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    if not events:
        await interaction.followup.send(
            f"{range_start.strftime('%Y-%m-%d')} から{days}日間、削除できる予定が見つかりませんでした。",
            ephemeral=True,
        )
        return

    view = ScheduleRemoveView(interaction.user.id, events)
    message = await interaction.followup.send(
        "削除する予定をメニューから選んでください(2分経つと無効になります)。",
        view=view,
        ephemeral=True,
    )
    view.message = message


@schedule_group.command(name="freebusy", description="共有を許可された相手の空き状況(予定の詳細は含まない)を確認します")
@app_commands.describe(
    user="空き状況を確認したい相手(`/calendar share`で共有を許可している必要があります)",
    when="確認する範囲の起点。省略時は今日 (例: `today`, `明日`, `2026-09-20`)",
    days="何日分確認するか(省略時: 1日、最大7日)",
)
async def schedule_freebusy(
    interaction: discord.Interaction,
    user: discord.User,
    when: Optional[str] = None,
    days: int = 1,
):
    await interaction.response.defer(ephemeral=True)

    if user.id == interaction.user.id:
        await interaction.followup.send("自分の予定は `/schedule list` で確認できます。", ephemeral=True)
        return

    shared = await asyncio.to_thread(google_calendar.is_freebusy_shared, user.id)
    if not shared:
        await interaction.followup.send(
            f"{user.display_name} さんは空き状況の共有を許可していません。", ephemeral=True
        )
        return

    days = max(1, min(days, 7))
    try:
        start_date = productivity.parse_date_jst(when) if when else productivity.parse_date_jst("today")
    except productivity.ProductivityError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    range_start = datetime.combine(start_date, datetime.min.time(), tzinfo=productivity.JST)
    range_end = range_start + timedelta(days=days)

    try:
        busy_periods = await asyncio.to_thread(
            google_calendar.get_free_busy, user.id, range_start, range_end
        )
    except google_calendar.CalendarError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🕓 {user.display_name} さんの空き状況({range_start.strftime('%Y-%m-%d')} から{days}日間)",
        color=discord.Color.blue(),
    )
    if not busy_periods:
        embed.description = "この期間はすべて空いています。"
    else:
        lines = []
        for bp in busy_periods:
            if bp.start.date() == bp.end.date():
                line = (
                    f"❌ {bp.start.strftime('%m/%d')}({google_calendar.weekday_ja(bp.start)}) "
                    f"{bp.start.strftime('%H:%M')}〜{bp.end.strftime('%H:%M')}"
                )
            else:
                line = f"❌ {bp.start.strftime('%m/%d %H:%M')} 〜 {bp.end.strftime('%m/%d %H:%M')}"
            lines.append(line)
        embed.description = "\n".join(lines)

    embed.set_footer(text="※ 予定のタイトルや場所などの詳細は共有されません")
    await interaction.followup.send(embed=embed, ephemeral=True)


bot.tree.add_command(schedule_group)


# ============================================================
# /help: 登録されているコマンドの一覧を自動生成して表示する
#
# 新しいコマンドを追加してもこのコードを触る必要はない。
#   - 新しい機能領域を追加するときは、/remind, /todo, /calendar, /schedule と
#     同じように app_commands.Group でまとめると、グループ名がそのまま
#     カテゴリの見出しになって自動的にここに表示される。
#   - グループに属さない単独コマンド(/stats など)は「統計・グラフ」カテゴリに
#     自動的にまとめられる。
# ============================================================

_CATEGORY_EMOJIS = {
    "remind": "⏰",
    "todo": "📝",
    "calendar": "🔑",
    "schedule": "🗓️",
}
_DEFAULT_CATEGORY_EMOJI = "🔧"

_STANDALONE_KEY = "stats"
_STANDALONE_EMOJI = "📊"
_STANDALONE_LABEL = "統計・グラフ"


@dataclass
class CommandCategory:
    key: str  # /help category:xxx で絞り込むための内部キー
    label: str  # 表示用の見出し(絵文字付き)
    entries: List[Tuple[str, str]]  # (コマンド名, 説明) のリスト


def _collect_command_categories() -> List[CommandCategory]:
    """bot.tree に登録されている全コマンドを走査し、カテゴリ単位にまとめる"""
    categories: List[CommandCategory] = []
    standalone: List[Tuple[str, str]] = []

    for cmd in bot.tree.get_commands():
        if isinstance(cmd, app_commands.Group):
            emoji = _CATEGORY_EMOJIS.get(cmd.name, _DEFAULT_CATEGORY_EMOJI)
            entries = [(f"/{cmd.name} {sub.name}", sub.description or "(説明なし)") for sub in cmd.commands]
            categories.append(
                CommandCategory(
                    key=cmd.name,
                    label=f"{emoji} /{cmd.name}({cmd.description})",
                    entries=entries,
                )
            )
        else:
            standalone.append((f"/{cmd.name}", cmd.description or "(説明なし)"))

    if standalone:
        categories.insert(
            0,
            CommandCategory(
                key=_STANDALONE_KEY,
                label=f"{_STANDALONE_EMOJI} {_STANDALONE_LABEL}",
                entries=standalone,
            ),
        )

    return categories


async def _help_category_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    categories = _collect_command_categories()
    current_lower = current.lower()
    matches = [
        app_commands.Choice(name=c.label, value=c.key)
        for c in categories
        if current_lower in c.key.lower() or current_lower in c.label.lower()
    ]
    return matches[:25]


@bot.tree.command(name="help", description="利用できるコマンドの一覧を表示します")
@app_commands.describe(category="特定のカテゴリだけ詳しく見たい場合に指定(省略時は全カテゴリの概要)")
@app_commands.autocomplete(category=_help_category_autocomplete)
async def help_command(interaction: discord.Interaction, category: Optional[str] = None):
    categories = _collect_command_categories()

    if category:
        matched = next((c for c in categories if c.key == category), None)
        if matched is None:
            await interaction.response.send_message(
                f"⚠️ カテゴリ「{category}」が見つかりませんでした。"
                "`/help` を引数なしで実行すると一覧が見られます。",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title=matched.label, color=discord.Color.blurple())
        embed.description = "\n\n".join(f"**{name}**\n{desc}" for name, desc in matched.entries)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(
        title="📖 コマンド一覧",
        description="カテゴリごとの概要です。`/help category:` で特定のカテゴリだけ詳しく見られます。",
        color=discord.Color.blurple(),
    )
    for cat in categories:
        value = "\n".join(f"`{name}` — {desc}" for name, desc in cat.entries)
        if len(value) > 1024:
            value = value[:1000] + "\n…(表示しきれないコマンドがあります)"
        embed.add_field(name=cat.label, value=value, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
