"""
charts.py の自動テスト。
エラーメッセージが意図通りに出るかどうかを、Discordを介さずに確認する。

実行方法:
    pip install pytest pytest-asyncio
    python -m pytest test_charts.py -v
"""

import pytest

from charts import DataParseError, compute_basic_stats, parse_number_input


class FakeAttachment:
    """discord.Attachment の代わりに使う、テスト用の簡易ダミークラス"""

    def __init__(self, content: bytes):
        self._content = content

    async def read(self) -> bytes:
        return self._content


# ============================================================
# 正常系: 数値がちゃんと読み取れるか
# ============================================================

@pytest.mark.asyncio
async def test_comma_separated():
    values = await parse_number_input("1,2,3,4,5", None)
    assert values == [1.0, 2.0, 3.0, 4.0, 5.0]


@pytest.mark.asyncio
async def test_mixed_separators():
    """カンマ・空白・改行が混ざっていても読み取れるか"""
    values = await parse_number_input("1 2\n3,4  5", None)
    assert values == [1.0, 2.0, 3.0, 4.0, 5.0]


@pytest.mark.asyncio
async def test_negative_and_float_numbers():
    values = await parse_number_input("-1.5, 0, 2.75", None)
    assert values == [-1.5, 0.0, 2.75]


@pytest.mark.asyncio
async def test_file_input():
    """ファイル添付からも読み取れるか"""
    fake_file = FakeAttachment(b"10, 20, 30")
    values = await parse_number_input(None, fake_file)
    assert values == [10.0, 20.0, 30.0]


@pytest.mark.asyncio
async def test_file_takes_priority_over_data():
    """dataとfileが両方指定された場合、fileが優先されるか"""
    fake_file = FakeAttachment(b"100, 200")
    values = await parse_number_input("1,2,3", fake_file)
    assert values == [100.0, 200.0]


# ============================================================
# 異常系: エラーメッセージがちゃんと出るか
# ============================================================

@pytest.mark.asyncio
async def test_no_input_raises_error():
    """dataもfileも指定しなかった場合"""
    with pytest.raises(DataParseError, match="どちらかを指定してください"):
        await parse_number_input(None, None)


@pytest.mark.asyncio
async def test_invalid_token_raises_error():
    """数値に変換できない文字が混ざっていた場合"""
    with pytest.raises(DataParseError, match="abc"):
        await parse_number_input("1,2,abc,4", None)


@pytest.mark.asyncio
async def test_multiple_invalid_tokens_are_listed():
    """複数の不正な値がまとめてエラーメッセージに出るか"""
    with pytest.raises(DataParseError) as exc_info:
        await parse_number_input("1,foo,2,bar,baz", None)
    message = str(exc_info.value)
    assert "foo" in message
    assert "bar" in message
    assert "baz" in message


@pytest.mark.asyncio
async def test_empty_string_raises_error():
    """空文字列を渡した場合"""
    with pytest.raises(DataParseError, match="見つかりませんでした"):
        await parse_number_input("   ", None)


@pytest.mark.asyncio
async def test_min_count_not_satisfied():
    """min_countで指定した個数に満たない場合(例: 箱ひげ図には2個以上必要)"""
    with pytest.raises(DataParseError, match="2個以上"):
        await parse_number_input("5", None, min_count=2)


@pytest.mark.asyncio
async def test_min_count_satisfied_just_enough():
    """ちょうどmin_count個ならエラーにならないか(境界値テスト)"""
    values = await parse_number_input("1,2", None, min_count=2)
    assert values == [1.0, 2.0]


@pytest.mark.asyncio
async def test_file_invalid_encoding_raises_error():
    """UTF-8として読めないファイルを渡した場合"""
    invalid_utf8_bytes = b"\xff\xfe\x00\x01"  # 不正なUTF-8バイト列
    fake_file = FakeAttachment(invalid_utf8_bytes)
    with pytest.raises(DataParseError, match="UTF-8"):
        await parse_number_input(None, fake_file)


# ============================================================
# 統計量の計算が正しいか(既知の値で検算)
# ============================================================

def test_compute_basic_stats_known_values():
    values = [1, 2, 3, 4, 5]
    stats = compute_basic_stats(values)
    assert stats["個数"] == 5
    assert stats["平均"] == 3.0
    assert stats["中央値"] == 3.0
    assert stats["最小値"] == 1.0
    assert stats["最大値"] == 5.0
