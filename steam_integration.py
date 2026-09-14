"""
Steam連携用モジュール。
/steam 系のスラッシュコマンドから利用する。

Googleカレンダー連携との決定的な違い:
  Googleカレンダーは「このBotにだけ、この範囲の情報を見せる」という
  個別の同意(OAuth)の仕組みだったが、Steamの所持ゲーム取得API(GetOwnedGames)には
  それが無い。対象のSteamプロフィールの「ゲームの詳細」というプライバシー設定が
  「公開」になっていれば、Steam Web APIキーを持つ人なら誰でも(このBot経由でなくても)
  所持ゲームを取得できてしまう。逆に「非公開」なら、Bot側からも一切取得できない。
  つまり `/steam link` は「アクセス許可を与える」操作ではなく、
  「もともと公開設定にしている情報を、Discord上で見やすくする」ための
  Discordアカウント⇔SteamIDの紐付けでしかない。

必要な環境変数:
  STEAM_API_KEY  Steam Web APIキー(https://steamcommunity.com/dev/apikey で無料発行)

注意: このモジュールはSteam Web APIの仕様(https://steamcommunity.com/dev や
partner.steamgames.com の公開ドキュメント)をもとに実装しているが、実際に
稼働環境でSteam APIへ疎通確認はしていない。デプロイ後、`/steam link`が
正しく動くか一度確認すること。
"""

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import requests

DB_PATH = Path(__file__).resolve().parent / "data" / "bot.db"

API_KEY = os.getenv("STEAM_API_KEY")
_BASE_URL = "https://api.steampowered.com"
_REQUEST_TIMEOUT_SECONDS = 10


class SteamError(ValueError):
    """Steam連携まわりのエラー(そのままユーザーに表示してよいメッセージ)"""


class PrivateProfileError(SteamError):
    """対象のプロフィールの「ゲームの詳細」が非公開で取得できない場合"""


@dataclass
class SteamGame:
    appid: int
    name: str
    playtime_forever_minutes: int


def _require_config() -> None:
    if not API_KEY:
        raise SteamError(
            "Steam連携が設定されていません。環境変数 STEAM_API_KEY を確認してください。"
        )


def init_db() -> None:
    """テーブルが無ければ作成する。Bot起動時に一度呼び出す(同期関数)"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS steam_links (
                discord_user_id INTEGER PRIMARY KEY,
                steam_id64 TEXT NOT NULL,
                linked_at TEXT NOT NULL
            )
            """
        )
        db.commit()


# ============================================================
# Steam Web API 呼び出し(すべて同期・blocking関数。呼び出し側で asyncio.to_thread する)
# ============================================================

def _api_get(path: str, params: dict) -> dict:
    _require_config()
    params = {**params, "key": API_KEY, "format": "json"}
    try:
        resp = requests.get(f"{_BASE_URL}{path}", params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise SteamError(f"Steam APIへの接続に失敗しました: {e}")

    try:
        return resp.json()
    except ValueError:
        raise SteamError("Steam APIから予期しない形式の応答がありました。")


_STEAM_ID64_PATTERN = re.compile(r"^\d{17}$")
_PROFILE_ID64_URL_PATTERN = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
_VANITY_URL_PATTERN = re.compile(r"steamcommunity\.com/id/([^/\s]+)")


def _resolve_vanity(vanity: str) -> str:
    data = _api_get("/ISteamUser/ResolveVanityURL/v0001/", {"vanityurl": vanity})
    result = data.get("response", {})
    if result.get("success") != 1:
        raise SteamError(
            f"Steamアカウント「{vanity}」が見つかりませんでした。プロフィールURLかIDを確認してください。"
        )
    return result["steamid"]


def resolve_steam_id(raw_input: str) -> str:
    """
    プロフィールのフルURL(.../profiles/<id64> または .../id/<vanity>)、
    17桁の生SteamID64、または素のvanity名のいずれかから、SteamID64を求める。
    """
    text = raw_input.strip()
    if not text:
        raise SteamError("SteamのプロフィールURLかIDを指定してください。")

    m = _PROFILE_ID64_URL_PATTERN.search(text)
    if m:
        return m.group(1)

    m = _VANITY_URL_PATTERN.search(text)
    if m:
        return _resolve_vanity(m.group(1))

    if _STEAM_ID64_PATTERN.match(text):
        return text

    # URL形式でなければ、素のvanity名とみなして解決を試みる
    return _resolve_vanity(text)


def _get_persona_name(steam_id64: str) -> str:
    """存在確認を兼ねて表示名を取得する。見つからなければSteamErrorを送出する"""
    data = _api_get("/ISteamUser/GetPlayerSummaries/v0002/", {"steamids": steam_id64})
    players = data.get("response", {}).get("players", [])
    if not players:
        raise SteamError("指定したSteamアカウントが見つかりませんでした。URLやIDを確認してください。")
    return players[0].get("personaname") or steam_id64


def get_owned_games(steam_id64: str) -> List[SteamGame]:
    """
    指定したSteamIDの所持ゲーム一覧を取得する。
    プロフィールの「ゲームの詳細」が非公開の場合、Steam側はエラーではなく
    空のレスポンス(gamesキー無し)を返してくるため、その場合は PrivateProfileError を送出する。
    """
    data = _api_get(
        "/IPlayerService/GetOwnedGames/v0001/",
        {
            "steamid": steam_id64,
            "include_appinfo": "true",
            "include_played_free_games": "true",
        },
    )
    response = data.get("response", {})
    if "games" not in response:
        raise PrivateProfileError(
            "このSteamアカウントは「ゲームの詳細」が非公開に設定されているため、"
            "所持ゲームを取得できません。Steamのプライバシー設定を確認してください。"
        )

    return [
        SteamGame(
            appid=g["appid"],
            name=g.get("name") or f"App {g['appid']}",
            playtime_forever_minutes=g.get("playtime_forever", 0),
        )
        for g in response.get("games", [])
    ]


# ============================================================
# Discordアカウント ⇔ SteamID の紐付け(DB)
# ============================================================

def link_account(discord_user_id: int, raw_input: str) -> Tuple[str, str]:
    """
    入力(プロフィールURL・vanity名・SteamID64のいずれか)からSteamID64を解決し、
    存在確認(表示名取得)をした上でDBに保存する。
    戻り値: (steam_id64, 表示名)
    """
    steam_id64 = resolve_steam_id(raw_input)
    persona_name = _get_persona_name(steam_id64)

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO steam_links (discord_user_id, steam_id64, linked_at) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET "
            "steam_id64 = excluded.steam_id64, linked_at = excluded.linked_at",
            (discord_user_id, steam_id64, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()

    return steam_id64, persona_name


def unlink_account(discord_user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            "DELETE FROM steam_links WHERE discord_user_id = ?", (discord_user_id,)
        )
        db.commit()
        return cursor.rowcount > 0


def get_linked_steam_id(discord_user_id: int) -> Optional[str]:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            "SELECT steam_id64 FROM steam_links WHERE discord_user_id = ?",
            (discord_user_id,),
        )
        row = cursor.fetchone()
    return row[0] if row else None
