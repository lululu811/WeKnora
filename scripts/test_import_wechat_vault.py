#!/usr/bin/env python3
"""scripts/import_wechat_vault.py 准入规则的单测。

覆盖三种消费者可见的行为：账号分层、标题命题、collect 的过滤/排序/跳过。
运行：python3 -m unittest discover -s scripts -p "test_import_*.py"
（或用 python3 直接跑本文件。）
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_spec = importlib.util.spec_from_file_location(
    "import_wechat_vault", Path(__file__).resolve().parent / "import_wechat_vault.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["import_wechat_vault"] = mod  # 3.14 dataclass 注解解析需要
_spec.loader.exec_module(mod)


def _art(account: str, title: str):
    return mod.Article(
        path=Path("/tmp/x.md"),
        vault_path="vault/x.md",
        title=title,
        source="https://mp.weixin.qq.com/s/abc",
        account=account,
        published="2026-10-10 08:00",
    )


class TestAdmit(unittest.TestCase):
    def test_admit_account_is_unconditional(self):
        ok, reason = mod.admit(_art("中国证券报", "任何标题都收"))
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_condition_account_needs_market_title(self):
        ok, _ = mod.admit(_art("知行小菜鸟", "今日复盘：打板龙头的接力情绪"))
        self.assertTrue(ok)
        ok, reason = mod.admit(_art("知行小菜鸟", "周末去爬山，听了风"))
        self.assertFalse(ok)
        self.assertEqual(reason, "标题无市场语义")

    def test_unknown_account_is_rejected(self):
        ok, reason = mod.admit(_art("乌鲁木齐市第133中学", "涨停了！"))
        self.assertFalse(ok)
        self.assertEqual(reason, "账号未放行")

    def test_market_title_regex_real_world_cases(self):
        positives = [
            "10月10日技术分析三大指数趋势延续弱势，大盘趋势支撑，短期超跌反弹格局",
            "今日北向资金午后回流，两市成交额重回万亿",
            "组合周报：加仓高股息，减仓半导体",
            "三季报业绩预告超预期，机构调研家数翻倍",
            "可转债下修套利机会梳理",
            "国常会部署降准预期，债市汇市联动",
            "每日夜报·金融炼药师｜2026-09-24",
            "收评：三大指数集体调整，成交额缩量",
        ]
        negatives = [
            "乌鲁木齐交警曝光两起未成年人违法案例",
            "《王者万象象棋》开宝箱赢现金、王者手办！",
            "【夜读】人生最珍贵的三样东西",
            "金属加工中的刀具磨损机理研究",
            "AI 摘要：大模型 Agent 的下一步",
        ]
        for t in positives:
            self.assertIsNotNone(mod.MARKET_TITLE_RE.search(t), f"应收未收: {t}")
        for t in negatives:
            self.assertIsNone(mod.MARKET_TITLE_RE.search(t), f"应拒未拒: {t}")


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "01-articles"
        self.base.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, account: str, title: str, published: str, *, link=True):
        d = self.base / account / title
        d.mkdir(parents=True)
        body = [f"# {title}", "", f"> 公众号：{account}", f"> 发布时间：{published}"]
        if link:
            slug = f"{account}{published}".replace(" ", "").replace(":", "")
            body.append(f"> 原文链接：https://mp.weixin.qq.com/s/{slug}")
        body += ["", "正文 ![图](images/a.png) 内容", "", "![第二张](images/b.png)"]
        (d / f"{title}.md").write_text("\n".join(body), encoding="utf-8")

    def test_rule_filter_tiers_order_and_skips(self):
        self._write("中国证券报", "证券头条", "2026-10-09 09:00")
        self._write("知行小菜鸟", "今日复盘：打板与低吸", "2026-10-10 09:00")
        self._write("知行小菜鸟", "周末爬山记", "2026-10-11 09:00")  # 拒：标题无市场语义
        self._write("金属加工", "涨停制度梳理", "2026-10-12 09:00")  # 拒：账号未放行
        (self.base / "中国证券报" / "README.md").write_text("x", encoding="utf-8")

        articles, rejects = mod.collect(self.root, "01-articles", admit_fn=mod.admit)
        self.assertEqual([a.title for a in articles], ["今日复盘：打板与低吸", "证券头条"])
        self.assertEqual(len(rejects), 2)
        self.assertEqual(
            sorted(rejects),
            sorted([("知行小菜鸟", "标题无市场语义"), ("金属加工", "账号未放行")]),
        )
        self.assertEqual(articles[0].images, 2)  # ![] 计数经过滤后仍然正确

    def test_filter_off_restores_old_behavior(self):
        self._write("金属加工", "刀具磨损研究", "2026-10-09 09:00")
        self._write("乌鲁木齐市第133中学", "校运动会", "2026-10-10 09:00", link=False)
        articles, rejects = mod.collect(self.root, "01-articles")
        self.assertEqual(len(articles), 1)  # 无原文链接的仍旧跳过（旧行为）
        self.assertEqual(rejects, [])

    def test_limit_applies_after_admission(self):
        self._write("中国证券报", "A 复盘", "2026-10-09 09:00")
        self._write("中国证券报", "B 复盘", "2026-10-10 09:00")
        articles, _ = mod.collect(self.root, "01-articles", limit=1, admit_fn=mod.admit)
        self.assertEqual([a.title for a in articles], ["B 复盘"])  # 最新优先


class TestPaginationDone(unittest.TestCase):
    """钉死曾经真实发生过的 off-by-one：total=830 时第 9 页（30 条）不能被跳过。"""

    def test_full_page_must_not_stop_before_total_reached(self):
        # 第 8 页取满 100、累计 800、total 830 → 必须继续取第 9 页
        self.assertFalse(mod._pagination_done(100, 800, 830))
        self.assertFalse(mod._pagination_done(100, 800, 900))
        self.assertFalse(mod._pagination_done(100, 100, 800))

    def test_stops_when_short_page_or_total_reached(self):
        self.assertTrue(mod._pagination_done(30, 800, 830))   # 末页不满
        self.assertTrue(mod._pagination_done(100, 800, 800))  # 已取够
        self.assertTrue(mod._pagination_done(100, 830, 830))


if __name__ == "__main__":
    unittest.main()
