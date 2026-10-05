"""跨语言时间语义的回归网：境内平台时间戳必须按北京时间落成日期。

背景：`_ts_to_date` 曾经用 `datetime.fromtimestamp(ts)`，那是**服务器本地时区**
换算，于是同一个时间戳落出来的日期随部署环境漂移。实测同一份 fixture：

    本地 Asia/Shanghai → 2026-04-17   绿
    GitHub Actions UTC → 2026-04-16   红

CI 红而本地绿会把排查方向带偏（看起来像测试写错了，实际错的是生产代码）。
巨潮公告与互动易问答都是境内平台，北京时间 00:00 发布的公告不该在 UTC 服务器上
被记成前一天——那是**数据正确性**问题，不是测试问题。

这里显式切换进程 TZ 来锁住不变量：换任何时区，日期都必须一样。
"""

import os
import time

import pytest

from halo.tz import CST, ts_to_cst_date

# 1776355200000 ms = 2026-04-17 00:00:00 +08:00 = 2026-04-16 16:00:00 UTC。
# 正是午夜边界：本地时区换算一差就是差一天。
BEIJING_MIDNIGHT_MS = 1776355200000


@pytest.fixture
def restore_tz():
    """切换进程 TZ 后复原，避免污染同批次的其它测试。"""
    saved = os.environ.get("TZ")

    def _set(value: str) -> None:
        os.environ["TZ"] = value
        time.tzset()

    yield _set

    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def test_beijing_midnight_lands_on_beijing_date():
    assert ts_to_cst_date(BEIJING_MIDNIGHT_MS) == "2026-04-17"


def test_date_does_not_depend_on_process_timezone(restore_tz):
    """核心不变量：换时区不换日期。"""
    seen = set()
    for tz in ("UTC", "Asia/Shanghai", "America/New_York", "Pacific/Kiritimati"):
        restore_tz(tz)
        seen.add(ts_to_cst_date(BEIJING_MIDNIGHT_MS))

    assert seen == {"2026-04-17"}, f"日期随时区漂移了：{seen}"


def test_positive_offset_tz_would_have_been_wrong(restore_tz):
    """反证：真按服务器时区换算，UTC 下确实会差一天。

    这条把"为什么必须显式指定时区"钉成可执行的证据，而不是一句注释。
    """
    from datetime import datetime

    restore_tz("UTC")
    naive = datetime.fromtimestamp(BEIJING_MIDNIGHT_MS / 1000).strftime("%Y-%m-%d")
    assert naive == "2026-04-16"
    assert ts_to_cst_date(BEIJING_MIDNIGHT_MS) == "2026-04-17"


def test_cst_is_fixed_offset_not_system_local():
    """CST 必须是固定 +08:00，不能退化成服务器本地时区。"""
    assert CST.utcoffset(None).total_seconds() == 8 * 3600


def test_malformed_values_give_empty_string():
    """一条坏记录不该拖垮同批的另外 29 条公告。"""
    assert ts_to_cst_date(None) == ""
    assert ts_to_cst_date("") == ""


def test_string_timestamp_passes_through_as_date_prefix():
    """巨潮偶尔把 announcementTime 返回成字符串，那时直接取日期前缀。

    这是 _ts_to_date 一直以来的兜底行为，时区重构没有动它。
    """
    assert ts_to_cst_date("2026-04-17T08:30:00+08:00") == "2026-04-17"


def test_bool_is_not_treated_as_epoch_seconds():
    """bool 是 int 的子类，不显式挡掉的话 True 会被当成秒数 1 → 1970-01-01。

    既定行为是走字符串分支。断言写死它，好让将来谁改动都是有意识的。
    """
    assert ts_to_cst_date(True) == "True"
    assert ts_to_cst_date(False) == ""
