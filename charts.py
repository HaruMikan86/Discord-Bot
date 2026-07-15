"""
数値データの解析とグラフ生成をまとめたモジュール。
/boxplot, /hist などのスラッシュコマンドから利用する。

第9回(グラフ)・第11回(統計)の内容をベースにしている。
"""

import io
from typing import List, Optional

import discord
import matplotlib

matplotlib.use("Agg")  # サーバー上に画面がなくても描画できるようにする設定
import japanize_matplotlib  # noqa: F401  (import するだけでグラフの日本語文字化けを防げる)
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats as scipy_stats


class DataParseError(ValueError):
    """数値データの解析に失敗したときの例外"""


async def parse_number_input(
    data: Optional[str],
    file: Optional[discord.Attachment],
) -> List[float]:
    """
    スラッシュコマンドの `data`(文字列)または `file`(添付ファイル)から
    数値のリストを取り出す。両方指定された場合は file を優先する。
    区切り文字はカンマ・空白・改行のどれでも良い。
    """
    if file is not None:
        raw_bytes = await file.read()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise DataParseError(
                "ファイルの文字コードがUTF-8ではないようです。UTF-8で保存し直してください。"
            )
    elif data is not None:
        text = data
    else:
        raise DataParseError(
            "`data`(数値の並び)か`file`(数値が書かれたファイル)のどちらかを指定してください。"
        )

    tokens = [t for t in text.replace(",", " ").replace("\n", " ").split(" ") if t.strip()]

    if not tokens:
        raise DataParseError("数値が見つかりませんでした。")

    values: List[float] = []
    invalid_tokens: List[str] = []
    for t in tokens:
        try:
            values.append(float(t))
        except ValueError:
            invalid_tokens.append(t)

    if invalid_tokens:
        preview = ", ".join(invalid_tokens[:5])
        suffix = " など" if len(invalid_tokens) > 5 else ""
        raise DataParseError(f"数値として読み取れない値がありました: {preview}{suffix}")

    return values


def create_boxplot_image(values: List[float], label: str = "データ") -> io.BytesIO:
    """箱ひげ図を作成し、PNG画像としてBytesIOに書き出す"""
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.boxplot(values, tick_labels=[label], showmeans=True, showfliers=True)
    ax.set_title("箱ひげ図")
    ax.set_ylabel("値")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def create_histogram_image(values: List[float], bins: int = 10) -> io.BytesIO:
    """ヒストグラムを作成し、PNG画像としてBytesIOに書き出す"""
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.hist(values, bins=bins, edgecolor="black", alpha=0.7)
    ax.set_title("ヒストグラム")
    ax.set_xlabel("値")
    ax.set_ylabel("度数")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def compute_basic_stats(values: List[float]) -> dict:
    """第11回で扱った基本統計量を計算する"""
    arr = np.array(values)
    mode_result = scipy_stats.mode(arr, keepdims=True)

    return {
        "個数": len(arr),
        "平均": float(np.mean(arr)),
        "中央値": float(np.median(arr)),
        "最頻値": float(mode_result.mode[0]),
        "標準偏差": float(np.std(arr)),
        "最小値": float(np.min(arr)),
        "最大値": float(np.max(arr)),
        "第1四分位数": float(np.percentile(arr, 25)),
        "第3四分位数": float(np.percentile(arr, 75)),
    }
