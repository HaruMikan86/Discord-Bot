"""
Googleカレンダー連携用モジュール。
/calendar, /schedule 系のスラッシュコマンドから利用する。

認可フロー:
  1. `/calendar connect` で、そのユーザー専用の認証用URLを発行しDBにstateを記録する
  2. ユーザーがブラウザでそのURLを開き、Googleアカウントでログインしてアクセスを許可する
  3. keep_alive.py の /oauth2callback にリダイレクトされ、
     受け取った認可コードをリフレッシュトークンと交換してDBに保存する

必要な環境変数:
  GOOGLE_CLIENT_ID      Google CloudのOAuthクライアントID
  GOOGLE_CLIENT_SECRET  同クライアントシークレット
  GOOGLE_REDIRECT_URI   例: https://<Renderのアプリ名>.onrender.com/oauth2callback
                        (Google Cloud Consoleの「承認済みのリダイレクトURI」と完全一致させること)

スコープは calendar.events (予定の読み書きのみ。カレンダー自体の作成/削除や
その他のGoogleアカウント情報へのアクセス権は含まない) に限定している。

注意: リフレッシュトークンは、そのユーザーの実際のGoogleカレンダーに
継続的にアクセスできる鍵。DBファイル(data/bot.db)の取り扱いには
通常より注意すること(リポジトリにコミットしない、他人に共有しないなど)。
"""

import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

# Googleから返るスコープの並び順・内容がリクエスト時とわずかに異なるだけで
# oauthlib が例外を投げてしまう既知の問題があるため、厳密一致チェックを緩める。
# (fetch_token() を呼ぶより前に設定されていれば良い)
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

JST = ZoneInfo("Asia/Tokyo")
DB_PATH = Path(__file__).resolve().parent / "data" / "bot.db"

# 予定の読み書きのみ(カレンダー一覧の変更やその他のスコープは要求しない)
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

_STATE_EXPIRY_SECONDS = 10 * 60  # 認証リンクの有効期限(10分)
_MAX_LIST_RESULTS = 20


class CalendarError(ValueError):
    """Googleカレンダー連携まわりのエラー(そのままユーザーに表示してよいメッセージ)"""


class NotConnectedError(CalendarError):
    """そのユーザーがまだGoogleカレンダーと連携していない場合"""


@dataclass
class ScheduleEvent:
    event_id: str
    summary: str
    start: datetime  # JSTのaware datetime(all_dayの場合は00:00として扱う)
    end: datetime
    all_day: bool
    location: Optional[str]
    description: Optional[str]
    html_link: Optional[str]


_WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")


def weekday_ja(d) -> str:
    """date/datetimeの曜日を日本語の1文字("月"〜"日")で返す"""
    return _WEEKDAY_JA[d.weekday()]


def format_event_time_range(ev: ScheduleEvent) -> str:
    """予定の日付・曜日・時刻をまとめた短い文字列を返す(一覧表示・削除メニュー共通)"""
    date_part = f"{ev.start.strftime('%m/%d')}({weekday_ja(ev.start)})"
    if ev.all_day:
        return f"{date_part} 終日"
    return f"{date_part} {ev.start.strftime('%H:%M')}〜{ev.end.strftime('%H:%M')}"


def _require_config() -> None:
    if not (CLIENT_ID and CLIENT_SECRET and REDIRECT_URI):
        raise CalendarError(
            "Googleカレンダー連携が設定されていません。"
            "環境変数 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI を確認してください。"
        )


def init_db() -> None:
    """テーブルが無ければ作成する。Bot起動時に一度呼び出す(同期関数)"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS google_tokens (
                discord_user_id INTEGER PRIMARY KEY,
                refresh_token TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                discord_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        db.commit()


# ============================================================
# 認可フロー(URL発行 → コールバック処理)
# ============================================================

def _build_flow() -> Flow:
    _require_config()
    return Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        # google-auth-oauthlib はデフォルトでPKCEのcode_verifierを自動生成するが、
        # 認可URL発行(/calendar connect)とトークン交換(/oauth2callback)は
        # 別々のFlowインスタンス(別リクエスト)になるため、verifierを引き継げず
        # 「Missing code verifier」で失敗してしまう。ここでは無効化して回避する。
        autogenerate_code_verifier=False,
    )


def build_authorize_url(discord_user_id: int) -> str:
    """
    認証用のURLを発行し、state(使い捨てトークン)とDiscordユーザーIDの対応をDBに記録する。
    このURLを踏んでもらうことで、コールバック時にどのDiscordユーザーの認証かを特定する。
    """
    flow = _build_flow()
    state = secrets.token_urlsafe(24)

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO oauth_states (state, discord_user_id, created_at) VALUES (?, ?, ?)",
            (state, discord_user_id, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # 毎回リフレッシュトークンを確実に受け取るため
        state=state,
    )
    return auth_url


def _pop_oauth_state(state: str) -> int:
    """state からDiscordユーザーIDを引いて削除する(使い捨て・有効期限10分)"""
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            "SELECT discord_user_id, created_at FROM oauth_states WHERE state = ?", (state,)
        )
        row = cursor.fetchone()
        if row is not None:
            db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            db.commit()

    if row is None:
        raise CalendarError(
            "認証リンクが無効か、期限切れです。もう一度 `/calendar connect` をやり直してください。"
        )

    discord_user_id, created_at = row
    created = datetime.fromisoformat(created_at)
    if (datetime.now(timezone.utc) - created).total_seconds() > _STATE_EXPIRY_SECONDS:
        raise CalendarError(
            "認証リンクの有効期限(10分)が切れています。もう一度 `/calendar connect` をやり直してください。"
        )

    return discord_user_id


def handle_oauth_callback(state: str, code: str) -> int:
    """
    Flask(keep_alive.py)の /oauth2callback から呼ばれる同期関数。
    認可コードをトークンに交換し、リフレッシュトークンをDBに保存する。
    戻り値: 連携が完了したDiscordユーザーID
    """
    discord_user_id = _pop_oauth_state(state)

    flow = _build_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        # oauthlib/requests側の様々な例外(invalid_grant, スコープ不一致など)をここで吸収する
        print(f"[google_calendar] トークン交換に失敗しました: {e!r}")
        raise CalendarError(
            "Googleとのトークン交換に失敗しました。認証リンクは1回しか使えないため、"
            "もう一度 `/calendar connect` からやり直してください。"
        )
    creds = flow.credentials

    if not creds.refresh_token:
        raise CalendarError(
            "リフレッシュトークンを取得できませんでした。"
            "Googleアカウントの「サードパーティ製アプリとサービスへのアクセス」設定から"
            "このアプリへのアクセスを一度取り消してから、もう一度 `/calendar connect` をやり直してください。"
        )

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO google_tokens (discord_user_id, refresh_token, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET "
            "refresh_token = excluded.refresh_token, updated_at = excluded.updated_at",
            (discord_user_id, creds.refresh_token, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()

    return discord_user_id


def is_connected(discord_user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            "SELECT 1 FROM google_tokens WHERE discord_user_id = ?", (discord_user_id,)
        )
        return cursor.fetchone() is not None


def disconnect(discord_user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            "DELETE FROM google_tokens WHERE discord_user_id = ?", (discord_user_id,)
        )
        db.commit()
        return cursor.rowcount > 0


# ============================================================
# Calendar API 呼び出し
# すべて同期(blocking)関数。discord.py側からは asyncio.to_thread 経由で呼ぶこと。
# ============================================================

def _load_credentials(discord_user_id: int) -> Credentials:
    _require_config()
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            "SELECT refresh_token FROM google_tokens WHERE discord_user_id = ?", (discord_user_id,)
        )
        row = cursor.fetchone()

    if row is None:
        raise NotConnectedError(
            "Googleカレンダーと連携されていません。`/calendar connect` で連携してください。"
        )

    creds = Credentials(
        token=None,
        refresh_token=row[0],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _build_service(discord_user_id: int):
    creds = _load_credentials(discord_user_id)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _to_event_body(
    summary: str,
    start: datetime,
    end: datetime,
    all_day: bool,
    location: Optional[str],
    description: Optional[str],
) -> dict:
    body: dict = {"summary": summary}
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    if all_day:
        body["start"] = {"date": start.date().isoformat()}
        body["end"] = {"date": end.date().isoformat()}
    else:
        body["start"] = {"dateTime": start.astimezone(JST).isoformat(), "timeZone": "Asia/Tokyo"}
        body["end"] = {"dateTime": end.astimezone(JST).isoformat(), "timeZone": "Asia/Tokyo"}

    return body


def _from_api_event(raw: dict) -> ScheduleEvent:
    start_raw = raw.get("start", {})
    end_raw = raw.get("end", {})
    all_day = "date" in start_raw

    if all_day:
        start = datetime.fromisoformat(start_raw["date"]).replace(tzinfo=JST)
        end = datetime.fromisoformat(end_raw["date"]).replace(tzinfo=JST)
    else:
        start = datetime.fromisoformat(start_raw["dateTime"]).astimezone(JST)
        end = datetime.fromisoformat(end_raw["dateTime"]).astimezone(JST)

    return ScheduleEvent(
        event_id=raw["id"],
        summary=raw.get("summary") or "(タイトルなし)",
        start=start,
        end=end,
        all_day=all_day,
        location=raw.get("location"),
        description=raw.get("description"),
        html_link=raw.get("htmlLink"),
    )


def create_event(
    discord_user_id: int,
    summary: str,
    start: datetime,
    end: datetime,
    all_day: bool = False,
    location: Optional[str] = None,
    description: Optional[str] = None,
) -> ScheduleEvent:
    """Googleカレンダー(primary)に予定を1件作成する"""
    service = _build_service(discord_user_id)
    body = _to_event_body(summary, start, end, all_day, location, description)
    try:
        raw = service.events().insert(calendarId="primary", body=body).execute()
    except HttpError as e:
        raise CalendarError(f"予定の登録に失敗しました: {e}")
    return _from_api_event(raw)


def list_events(
    discord_user_id: int,
    start: datetime,
    end: datetime,
    max_results: int = _MAX_LIST_RESULTS,
) -> List[ScheduleEvent]:
    """指定した期間([start, end))にある予定を開始時刻順に取得する"""
    service = _build_service(discord_user_id)
    try:
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start.astimezone(timezone.utc).isoformat(),
                timeMax=end.astimezone(timezone.utc).isoformat(),
                singleEvents=True,  # 繰り返し予定を個別のイベントに展開する
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )
    except HttpError as e:
        raise CalendarError(f"予定の取得に失敗しました: {e}")

    return [_from_api_event(item) for item in response.get("items", [])]


def delete_event(discord_user_id: int, event_id: str) -> None:
    service = _build_service(discord_user_id)
    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
    except HttpError as e:
        if getattr(e, "resp", None) is not None and e.resp.status == 404:
            raise CalendarError("指定した予定が見つかりませんでした(すでに削除済みの可能性があります)。")
        raise CalendarError(f"予定の削除に失敗しました: {e}")
