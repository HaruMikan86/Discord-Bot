"""
所持しているボードゲーム・マーダーミステリー(マダミス)のリストから、
条件に合うものをランダムに選ぶためのモジュール。/pick 系のスラッシュコマンドから利用する。

データはリポジトリ直下の CSV ファイルから読み込む(SQLiteには保存しない)。
  - boardgames.csv : タイトル,人数,時間,評価,タグ
  - madamis.csv    : タイトル,人数,時間,済

CSVはコミットしてデプロイすれば、Renderの再デプロイでも消えない(DBと違い、
リポジトリに含まれるため)。内容を変えたいときはCSVを編集して再デプロイする。

「人数」「時間」の書式:
  人数  "3-5"  3人から5人 / "2"  2人ちょうど / "4+1"  プレイヤー4人+ゲームマスター1人
        "1~"   1人以上(上限なし) / "1,4"  1人または4人(カンマ区切りは「どれか」の意味)
        "+1"   拡張セットなど(単体では遊べないため、抽選の対象から外す)
  時間  "30分" / "40-60分"(幅がある場合は両方読み取る) / "3時間" / "2時間30分"
  評価  0〜5の整数(ボードゲームのみ)。0は未プレイ。空欄も0(未プレイ)として扱う
  タグ  今のところ使用していない(読み込まず、無視する)
ExcelでCSV(コンマ区切り)として保存するとShift-JISになることがあるため、
UTF-8で読めなければShift-JIS(cp932)でも読み込む。
"""

import csv
import io
import random
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
BOARDGAMES_CSV = BASE_DIR / "boardgames.csv"
MADAMIS_CSV = BASE_DIR / "madamis.csv"

KIND_BOARDGAME = "boardgame"
KIND_MADAMIS = "madamis"

OPEN_ENDED = 999  # 「1~」のような上限なしの人数を表す内部値
RATING_UNPLAYED = 0  # 評価0 = 未プレイ
RATING_MAX = 5


class GameListError(ValueError):
    """リストの読み込みに失敗したときの例外(そのままユーザーに表示してよいメッセージ)"""


@dataclass(frozen=True)
class GameEntry:
    kind: str
    title: str
    # 遊べるプレイヤー人数。(最少, 最多)の組を並べたもの。
    # "3-5" → ((3, 5),) / "1,4" → ((1, 1), (4, 4)) / "1~" → ((1, OPEN_ENDED),)
    player_ranges: Tuple[Tuple[int, int], ...]
    gm: int  # ゲームマスターが別に必要な人数(マダミスの「4+1」の+1)
    minutes_min: int
    minutes_max: int
    played: Optional[bool] = None  # マダミスの「済」。ボドゲはNone
    is_addon: bool = False  # 「+1」のみの行(拡張セット等)。抽選の対象にしない
    rating: Optional[int] = None  # ボドゲの「評価」(0〜5、0は未プレイ)。マダミスはNone

    @property
    def players_min(self) -> int:
        return min((lo for lo, _ in self.player_ranges), default=0)

    @property
    def players_max(self) -> int:
        return max((hi for _, hi in self.player_ranges), default=0)

    def accepts_headcount(self, headcount: int) -> bool:
        """GMも含めた人数 headcount で遊べるか"""
        return any(lo + self.gm <= headcount <= hi + self.gm for lo, hi in self.player_ranges)


# ============================================================
# 「人数」「時間」「済」セルの解釈
# ============================================================

# 日本語IMEで "-" を打つと "ー" になる、Excel等がハイフンを別字に置き換えるなど、
# 範囲を表す記号の揺れを "-" に揃える(数値のセルにだけ適用する)
_DASHES = "‐‑‒–—―−ー～〜~"


def _normalize_numeric_cell(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)  # 全角数字・全角プラスなどを半角に
    for ch in _DASHES:
        text = text.replace(ch, "-")
    text = text.replace("、", ",")
    return re.sub(r"\s+", "", text)


def parse_players(text: str) -> Tuple[Tuple[Tuple[int, int], ...], int, bool]:
    """
    戻り値: (遊べる人数の範囲の組, GM人数, 拡張セットか)
      "3-5"→(((3,5),),0,False) / "4+1"→(((4,4),),1,False)
      "1~"→(((1,OPEN_ENDED),),0,False) / "1,4"→(((1,1),(4,4)),0,False) / "+1"→((),0,True)
    """
    s = _normalize_numeric_cell(text).replace("人", "")
    if not s:
        raise ValueError("人数が空です")

    if re.fullmatch(r"\+(\d+)", s):
        return (), 0, True

    m = re.fullmatch(r"(.+?)(?:\+(\d+))?", s)
    body, gm = m.group(1), int(m.group(2)) if m.group(2) else 0

    ranges = []
    for part in body.split(","):
        pm = re.fullmatch(r"(\d+)(?:(-)(\d+)?)?", part)
        if not pm:
            raise ValueError(f"人数「{text}」を読み取れません")
        low = int(pm.group(1))
        if pm.group(2) is None:
            high = low  # "2" → 2人ちょうど
        elif pm.group(3) is None:
            high = OPEN_ENDED  # "1-"(元は「1~」) → 1人以上
        else:
            high = int(pm.group(3))
        if low < 1 or high < low:
            raise ValueError(f"人数「{text}」が不正です")
        ranges.append((low, high))
    return tuple(ranges), gm, False


def parse_minutes(text: str) -> Tuple[int, int]:
    """戻り値: (最短分, 最長分)。「40-60分」のような幅も読み取る"""
    s = _normalize_numeric_cell(text).lower()
    if not s:
        raise ValueError("時間が空です")

    m = re.fullmatch(r"(\d+)時間(?:(\d+)分?)?", s)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2) or 0)
        return total, total

    m = re.fullmatch(r"(\d+)(?:-(\d+))?(分|min|m|時間|h)?", s)
    if not m:
        raise ValueError(f"時間「{text}」を読み取れません")

    factor = 60 if m.group(3) in ("時間", "h") else 1
    low = int(m.group(1)) * factor
    high = int(m.group(2)) * factor if m.group(2) else low
    if low < 1 or high < low:
        raise ValueError(f"時間「{text}」が不正です")
    return low, high


def parse_played(text: str) -> bool:
    """「済」列。TRUE/済/○などを済とみなす(空欄・FALSEは未プレイ扱い)"""
    return unicodedata.normalize("NFKC", text).strip().upper() in ("TRUE", "1", "YES", "済", "○", "〇", "✓", "✔")


def parse_rating(text: str) -> int:
    """「評価」列。0〜5の整数(0は未プレイ)。空欄は未プレイ(0)として扱う"""
    s = unicodedata.normalize("NFKC", text).strip()
    if not s:
        return RATING_UNPLAYED
    if not re.fullmatch(r"[0-5]", s):
        raise ValueError(f"評価「{text}」は0〜5の整数で指定してください")
    return int(s)


# ============================================================
# CSVの読み込み
# ============================================================

def _read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise GameListError(f"リストファイル「{path.name}」が見つかりません。リポジトリ直下に置いてください。")
    except OSError as e:
        raise GameListError(f"リストファイル「{path.name}」を読み込めませんでした: {e}")

    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise GameListError(f"「{path.name}」の文字コードを判別できません。UTF-8で保存し直してください。")


def _load(path: Path, kind: str) -> Tuple[List[GameEntry], List[str]]:
    """
    戻り値: (読み込めた行, 読み込めなかった行の説明)。
    1行の書式ミスでBot全体が止まらないよう、不正な行はスキップして理由を返す。
    """
    reader = csv.DictReader(io.StringIO(_read_text(path)))
    fieldnames = [(name or "").strip() for name in (reader.fieldnames or [])]
    reader.fieldnames = fieldnames
    missing = [c for c in ("タイトル", "人数", "時間") if c not in fieldnames]
    if missing:
        raise GameListError(f"「{path.name}」に必要な列がありません: {'、'.join(missing)}")

    entries: List[GameEntry] = []
    errors: List[str] = []
    for line_no, row in enumerate(reader, start=2):  # 1行目はヘッダー
        title = (row.get("タイトル") or "").strip()
        if not title:
            continue  # 空行は無視

        try:
            ranges, gm, is_addon = parse_players(row.get("人数") or "")
            if is_addon:
                m_min = m_max = 0
            else:
                m_min, m_max = parse_minutes(row.get("時間") or "")
            rating = None
            if kind == KIND_BOARDGAME and "評価" in fieldnames:
                rating = parse_rating(row.get("評価") or "")
        except ValueError as e:
            errors.append(f"{path.name} {line_no}行目「{title}」: {e}")
            continue

        played = parse_played(row.get("済") or "") if kind == KIND_MADAMIS else None
        entries.append(
            GameEntry(
                kind=kind,
                title=title,
                player_ranges=ranges,
                gm=gm,
                minutes_min=m_min,
                minutes_max=m_max,
                played=played,
                is_addon=is_addon,
                rating=rating,
            )
        )

    for message in errors:
        print(f"[game_picker] 読み込みをスキップしました: {message}")
    return entries, errors


def load_boardgames(path: Path = BOARDGAMES_CSV) -> Tuple[List[GameEntry], List[str]]:
    return _load(path, KIND_BOARDGAME)


def load_madamis(path: Path = MADAMIS_CSV) -> Tuple[List[GameEntry], List[str]]:
    return _load(path, KIND_MADAMIS)


# ============================================================
# 絞り込み・抽選
# ============================================================

def filter_entries(
    entries: List[GameEntry],
    players: Optional[int] = None,
    max_minutes: Optional[int] = None,
    include_played: bool = True,
    min_rating: Optional[int] = None,
    max_rating: Optional[int] = None,
) -> List[GameEntry]:
    """
    players      : 遊ぶ人数。GMが必要なものは、GMも含めた人数で判定する
    max_minutes  : この時間以内に終わるもの。「40-60分」のように幅がある場合は、
                   長い方(60分)で判定する(確実に収まるものだけを残す)
    include_played: Falseなら、プレイ済み(済=TRUE)のマダミスを除く
    min_rating / max_rating: ボドゲの評価(0〜5、0は未プレイ)の下限・上限(両端を含む)。
                   評価の列が無いなど、評価が分からないものは、指定があれば除外する
    拡張セット(「+1」のみの行)は単体で遊べないため、常に除外する。
    """
    result = []
    for e in entries:
        if e.is_addon:
            continue
        if players is not None and not e.accepts_headcount(players):
            continue
        if max_minutes is not None and e.minutes_max > max_minutes:
            continue
        if not include_played and e.played:
            continue
        if min_rating is not None and (e.rating is None or e.rating < min_rating):
            continue
        if max_rating is not None and (e.rating is None or e.rating > max_rating):
            continue
        result.append(e)
    return result


def pick(entries: List[GameEntry], count: int, rng=random) -> List[GameEntry]:
    """重複なしでcount件をランダムに選ぶ(候補がcount件未満なら、あるだけ全部)"""
    return rng.sample(entries, k=min(count, len(entries)))


# ============================================================
# 表示用の整形
# ============================================================

def _format_player_range(lo: int, hi: int) -> str:
    if hi >= OPEN_ENDED:
        return f"{lo}人〜"
    return f"{lo}人" if lo == hi else f"{lo}〜{hi}人"


def format_players(e: GameEntry) -> str:
    base = "、".join(_format_player_range(lo, hi) for lo, hi in e.player_ranges)
    return f"{base}+GM{e.gm}" if e.gm else base


def _format_minutes_value(minutes: int) -> str:
    # 2時間以上は、読みやすいように時間も併記する(例: 360分 → 360分(6時間))
    return f"{minutes}分(約{minutes / 60:g}時間)" if minutes >= 120 else f"{minutes}分"


def format_minutes(e: GameEntry) -> str:
    if e.minutes_min == e.minutes_max:
        return _format_minutes_value(e.minutes_max)
    return f"{e.minutes_min}〜{_format_minutes_value(e.minutes_max)}"


def format_entry(e: GameEntry) -> str:
    detail = f"👥 {format_players(e)} ｜ ⏱ {format_minutes(e)}"
    if e.played:
        detail += " ｜ ✅ 済"
    return f"**{e.title}**\n　{detail}"


def describe_rating(min_rating: Optional[int], max_rating: Optional[int]) -> Optional[str]:
    """評価の絞り込み条件を、フッター表示用の短い文にする(指定が無ければNone)"""
    if min_rating is None and max_rating is None:
        return None
    lo = 0 if min_rating is None else min_rating
    hi = RATING_MAX if max_rating is None else max_rating
    if hi == RATING_UNPLAYED:
        return "未プレイのみ"
    if lo == hi:
        return f"評価{lo}のみ"
    if hi == RATING_MAX:
        return f"評価{lo}以上"
    note = "(未プレイを含む)" if lo == RATING_UNPLAYED else ""
    return f"評価{lo}〜{hi}{note}"


def describe_conditions(
    players: Optional[int],
    max_minutes: Optional[int],
    include_played: Optional[bool] = None,
    min_rating: Optional[int] = None,
    max_rating: Optional[int] = None,
) -> str:
    parts = []
    if players is not None:
        parts.append(f"人数: {players}人")
    if max_minutes is not None:
        parts.append(f"{max_minutes}分以内")
    rating_text = describe_rating(min_rating, max_rating)
    if rating_text:
        parts.append(rating_text)
    if include_played:
        parts.append("プレイ済みを含む")
    return " ／ ".join(parts) if parts else "条件なし"
