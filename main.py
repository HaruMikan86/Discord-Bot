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
