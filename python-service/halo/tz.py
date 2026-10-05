"""中国境内数据源的时间语义。

巨潮（法定披露平台）与互动易 / 上证 e 互动（投资者关系）返回的 Unix 毫秒是
**绝对时刻**，但"落成哪个日历日"取决于用哪个时区解释它。两家都是境内平台，
日期按 Asia/Shanghai 理解才对——`reconcile.py:294` 对 period_end_ms 的实测也印证
了这一点（那批值是 Asia/Shanghai 午夜）。

**为什么不能用 `datetime.fromtimestamp(ts)`**：它按**服务器本地时区**换算，于是
同一个时间戳落出来的日期随部署环境漂移。实测同一份 fixture：

    本地（Asia/Shanghai）→ 2026-04-17   测试通过
    GitHub Actions（UTC）→ 2026-04-16   测试失败

结果是 CI 红而本地绿，排查方向会被带到"测试写错了"，实际错的是生产代码：**公告
日期本身就不该随服务器时区变**。北京时间 00:00 发布的公告，在 UTC 服务器上会被
记成前一天。

**为什么用固定 +08:00 而不是 `ZoneInfo("Asia/Shanghai")`**：ZoneInfo 依赖系统
tzdata，slim 镜像里通常不装，缺失时会在运行期抛 `ZoneInfoNotFoundError`——把一个
静默的日期错误换成一个运行期崩溃，不划算。中国自 1991 年起不再实行夏令时，固定
偏移对相关年份是精确的；真要处理 1991 年以前的历史数据再换 ZoneInfo 不迟。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

#: 境内平台的日历时区。刻意不用系统本地时区。
CST = timezone(timedelta(hours=8), name="Asia/Shanghai")


def ts_to_cst_date(ts: Any, fmt: str = "%Y-%m-%d") -> str:
    """Unix 毫秒 → 按 Asia/Shanghai 解释的日期字符串。

    畸形值返回空串而不是抛异常：巨潮偶尔返回 null 或字符串，一条坏记录不该
    拖垮同一批的另外 29 条公告。
    """
    try:
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return str(ts)[:10] if ts else ""
        return datetime.fromtimestamp(float(ts) / 1000, tz=CST).strftime(fmt)
    except (OSError, ValueError, OverflowError, TypeError):
        return ""
