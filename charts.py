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
    min_count: int = 1,
) -> List[float]:
    """
    スラッシュコマンドの `data`(文字列)または `file`(添付ファイル)から
    数値のリストを取り出す。両方指定された場合は file を優先する。
    区切り文字はカンマ・空白・改行のどれでも良い。

    min_count: 最低限必要な個数。足りない場合は DataParseError を送出する。
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

    if len(values) < min_count:
        raise DataParseError(
            f"{min_count}個以上の数値が必要です(入力されたのは{len(values)}個)。"
        )

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


def create_stats_image(values: List[float]) -> io.BytesIO:
    """
    データ全体を可視化する。平均(赤の破線)と中央値(青の点線)を重ねて表示する。
    /boxplot(箱ひげ図)とは違う切り口で、個々のデータ点の分布そのものを見せる。

    データ数によって描画方式を自動的に切り替える:
      - 80個以下: ジッター付きドットプロット(1点ずつ確認したい少数データ向け)
      - 81個以上: バイオリンプロット+ラグプロット(点が重なって潰れるのを防ぐ)
    """
    arr = np.array(values)
    n = len(arr)
    mean = float(np.mean(arr))
    median = float(np.median(arr))

    fig, ax = plt.subplots(figsize=(7, 3.2))

    if n <= 80:
        # データ数が多いほど、点を小さく・薄く・広く散らして重なりを緩和する
        if n <= 20:
            marker_size, alpha, jitter_range = 90, 0.8, 0.06
        else:
            marker_size, alpha, jitter_range = 55, 0.6, 0.12

        rng = np.random.default_rng(0)
        jitter = rng.uniform(-jitter_range, jitter_range, size=n)
        ax.scatter(arr, jitter, alpha=alpha, s=marker_size, zorder=3, label="データ", edgecolors="none")
        ax.set_ylim(-0.35, 0.35)
        title_suffix = "ドットプロット"
    else:
        # 点が多すぎて潰れる場合は、分布の形(バイオリンプロット)+
        # 実データの位置(ラグプロット、下部の短い縦線)の組み合わせに切り替える
        parts = ax.violinplot(arr, vert=False, positions=[0], widths=0.6, showextrema=False)
        for pc in parts["bodies"]:
            pc.set_alpha(0.4)
        ax.plot(arr, np.full(n, -0.32), "|", color="tab:blue", alpha=0.5, markersize=10, zorder=3, label="データ")
        ax.set_ylim(-0.4, 0.4)
        title_suffix = "分布(バイオリン+ラグプロット)"

    ax.axvline(mean, color="red", linestyle="--", linewidth=1.5, label=f"平均 = {mean:.2f}")
    ax.axvline(median, color="blue", linestyle=":", linewidth=1.5, label=f"中央値 = {median:.2f}")

    ax.set_yticks([])
    ax.set_xlabel("値")
    ax.set_title(f"データの{title_suffix}(n={n})")
    ax.legend(loc="upper right", fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf
