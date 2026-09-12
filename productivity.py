"""
リマインダー・ToDoリスト機能をまとめたモジュール。
/remind, /todo 系のスラッシュコマンドから利用する。

データは SQLite ファイル(data/bot.db)に保存され、Bot再起動後も保持される。
ただし Render の無料プランなど、永続ディスクを使っていない環境では
「再デプロイ」のタイミングでファイルごと消える点に注意すること
(15分無通信での自動スリープ→再起動、では消えない。コードの再デプロイで消える)。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import aiosqlite

JST = ZoneInfo("Asia/Tokyo")
DB_PATH = Path(__file__).resolve().parent / "data" / "bot.db"

_MAX_TODO_CONTENT_LENGTH = 200
_MAX_REMINDER_MESSAGE_LENGTH = 300
_MAX_PENDING_TODOS_PER_USER = 100
_MAX_PENDING_REMINDERS_PER_USER = 50


class ProductivityError(ValueError):
    """リマインダー/ToDoの入力エラー"""


@dataclass
class Reminder:
    id: int
    user_id: int
    channel_id: int
    message: str
    remind_at: datetime  # UTC aware


@dataclass
class TodoItem:
    id: int
    user_id: int
    content: str
    done: bool
    created_at: datetime


# ============================================================
# 初期化
# ============================================================

async def init_db() -> None:
    """テーブルが無ければ作成する。Bot起動時に一度呼び出す"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                done_at TEXT
            )
            """
        )
        await db.commit()


# ============================================================
# 日時・時間指定のパース
# ============================================================

_DURATION_PATTERN = re.compile(
    r"^(?:(?P<weeks>\d+)\s*(?:w|週間?))?"
    r"(?:(?P<days>\d+)\s*(?:d|日))?"
    r"(?:(?P<hours>\d+)\s*(?:h|時間))?"
    r"(?:(?P<minutes>\d+)\s*(?:m|分))?"
    r"(?:(?P<seconds>\d+)\s*(?:s|秒))?$"
)


def parse_duration(text: str) -> timedelta:
    """
    "10m"(10分後)、"1h30m"(1時間30分後)、"3d"(3日後)のような
    相対時間の文字列を timedelta に変換する。
    """
    stripped = text.strip().replace(" ", "")
    if not stripped:
        raise ProductivityError("時間の指定が空です(例: `10m`, `1h30m`, `3d`)。")

    match = _DURATION_PATTERN.match(stripped)
    groups: Dict[str, Optional[str]] = match.groupdict() if match else {}
    if not match or not any(groups.values()):
        raise ProductivityError(
            "時間の形式を認識できませんでした(例: `10m`=10分後, `1h30m`=1時間30分後, `3d`=3日後)。"
        )

    delta = timedelta(
        weeks=int(groups.get("weeks") or 0),
        days=int(groups.get("days") or 0),
        hours=int(groups.get("hours") or 0),
        minutes=int(groups.get("minutes") or 0),
        seconds=int(groups.get("seconds") or 0),
    )

    if delta.total_seconds() <= 0:
        raise ProductivityError("0より大きい時間を指定してください。")
    if delta.total_seconds() > 60 * 60 * 24 * 365:
        raise ProductivityError("1年より先のリマインダーは設定できません。")

    return delta


_ABSOLUTE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%m-%d %H:%M",
    "%m/%d %H:%M",
    "%H:%M",
)


def parse_when(text: str, now: Optional[datetime] = None) -> datetime:
    """
    相対時間("10m"など)または絶対日時("2026-09-20 21:00"など、JST基準)の
    文字列を解釈し、UTCのdatetime(通知予定時刻)を返す。

    絶対日時で日付を省略した場合は今日(またはH:Mのみなら直近の未来)を補完する。
    """
    now = now or datetime.now(timezone.utc)
    stripped = text.strip()

    try:
        return now + parse_duration(stripped)
    except ProductivityError:
        pass  # 相対時間として解釈できなければ、絶対日時として試す

    now_jst = now.astimezone(JST)

    for fmt in _ABSOLUTE_FORMATS:
        try:
            naive = datetime.strptime(stripped, fmt)
        except ValueError:
            continue

        if fmt == "%H:%M":
            naive = naive.replace(year=now_jst.year, month=now_jst.month, day=now_jst.day)
        elif fmt in ("%m-%d %H:%M", "%m/%d %H:%M"):
            naive = naive.replace(year=now_jst.year)

        target_utc = naive.replace(tzinfo=JST).astimezone(timezone.utc)

        if target_utc <= now:
            if fmt == "%H:%M":
                target_utc += timedelta(days=1)
            elif fmt in ("%m-%d %H:%M", "%m/%d %H:%M"):
                target_utc = target_utc.replace(year=target_utc.year + 1)
            else:
                raise ProductivityError("指定した日時は既に過去です。未来の日時を指定してください。")

        return target_utc

    raise ProductivityError(
        "日時の形式を認識できませんでした。\n"
        "・相対時間: `10m`(10分後) / `1h30m`(1時間30分後) / `3d`(3日後)\n"
        "・絶対日時(JST): `2026-09-20 21:00` / `09-20 21:00` / `21:00`"
    )


def format_jst(dt: datetime) -> str:
    """UTCのdatetimeを、表示用のJST文字列に変換する"""
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M") + " (JST)"


# ============================================================
# リマインダー: CRUD
# ============================================================

async def add_reminder(user_id: int, channel_id: int, message: str, remind_at: datetime) -> int:
    message = message.strip()
    if not message:
        raise ProductivityError("リマインドする内容を入力してください。")
    if len(message) > _MAX_REMINDER_MESSAGE_LENGTH:
        raise ProductivityError(f"内容は{_MAX_REMINDER_MESSAGE_LENGTH}文字以内にしてください。")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id = ? AND notified = 0", (user_id,)
        )
        (pending_count,) = await cursor.fetchone()
        if pending_count >= _MAX_PENDING_REMINDERS_PER_USER:
            raise ProductivityError(
                f"設定できるリマインダーは{_MAX_PENDING_REMINDERS_PER_USER}件までです。"
                "`/remind list` で不要なものを `/remind cancel` してください。"
            )

        cursor = await db.execute(
            "INSERT INTO reminders (user_id, channel_id, message, remind_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                channel_id,
                message,
                remind_at.astimezone(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
        return cursor.lastrowid


def _row_to_reminder(row) -> Reminder:
    return Reminder(
        id=row[0],
        user_id=row[1],
        channel_id=row[2],
        message=row[3],
        remind_at=datetime.fromisoformat(row[4]),
    )


async def list_reminders(user_id: int) -> List[Reminder]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, channel_id, message, remind_at FROM reminders "
            "WHERE user_id = ? AND notified = 0 ORDER BY remind_at ASC",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_reminder(row) for row in rows]


async def cancel_reminder(user_id: int, reminder_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ? AND notified = 0",
            (reminder_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def due_reminders(now: Optional[datetime] = None) -> List[Reminder]:
    """通知時刻を過ぎた、未通知のリマインダーを取得する(全ユーザー対象)"""
    now = now or datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, channel_id, message, remind_at FROM reminders "
            "WHERE notified = 0 AND remind_at <= ?",
            (now.astimezone(timezone.utc).isoformat(),),
        )
        rows = await cursor.fetchall()
    return [_row_to_reminder(row) for row in rows]


async def mark_notified(reminder_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,))
        await db.commit()


# ============================================================
# ToDo: CRUD
# ============================================================

async def add_todo(user_id: int, content: str) -> int:
    content = content.strip()
    if not content:
        raise ProductivityError("タスクの内容を入力してください。")
    if len(content) > _MAX_TODO_CONTENT_LENGTH:
        raise ProductivityError(f"内容は{_MAX_TODO_CONTENT_LENGTH}文字以内にしてください。")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM todos WHERE user_id = ? AND done = 0", (user_id,)
        )
        (pending_count,) = await cursor.fetchone()
        if pending_count >= _MAX_PENDING_TODOS_PER_USER:
            raise ProductivityError(
                f"未完了のタスクは{_MAX_PENDING_TODOS_PER_USER}件までです。"
                "`/todo clear` で完了済みタスクを整理するか、不要なタスクを `/todo remove` してください。"
            )

        cursor = await db.execute(
            "INSERT INTO todos (user_id, content, created_at) VALUES (?, ?, ?)",
            (user_id, content, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cursor.lastrowid


def _row_to_todo(row) -> TodoItem:
    return TodoItem(
        id=row[0],
        user_id=row[1],
        content=row[2],
        done=bool(row[3]),
        created_at=datetime.fromisoformat(row[4]),
    )


async def list_todos(user_id: int, include_done: bool = False) -> List[TodoItem]:
    query = "SELECT id, user_id, content, done, created_at FROM todos WHERE user_id = ?"
    if not include_done:
        query += " AND done = 0"
    query += " ORDER BY done ASC, id ASC"

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, (user_id,))
        rows = await cursor.fetchall()
    return [_row_to_todo(row) for row in rows]


async def complete_todo(user_id: int, todo_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ? AND user_id = ? AND done = 0",
            (datetime.now(timezone.utc).isoformat(), todo_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_todo(user_id: int, todo_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM todos WHERE id = ? AND user_id = ?", (todo_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_done_todos(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM todos WHERE user_id = ? AND done = 1", (user_id,)
        )
        await db.commit()
        return cursor.rowcount
