"""指标元数据（自动生成，请勿手改）。

真源是 config/indicators.yaml，schema 见 config/indicators.schema.md。
重新生成：

    go test ./internal/indicators/ -run TestGeneratedPythonModule -update

改了 YAML 却忘了重新生成，TestGeneratedPythonModule 会失败 —— 这正是这份文件
存在的意义：策略层的指标参数不能自己另写一份。

这里只有**元数据**（名字 / 参数 / 周期 / 精度 / DuckDB 列名）。公式实现仍在
zettaranc/{trend,volume,levels,pattern}.py 里各自保留。
"""

from __future__ import annotations

from typing import Any, Dict, List

SCHEMA_VERSION = 1

INDICATOR_META: Dict[str, Any] = {
  "schemaVersion": 1,
  "absTolerance": 0.01,
  "fixtureBars": 260,
  "ids": [
    "Z_MAIN",
    "Z_SIGNALS",
    "ZG_WHITE",
    "DG_YELLOW",
    "Z_BBI",
    "Z_VOL",
    "Z_MACD",
    "Z_KDJ",
    "ZX_BRICK",
    "Z_BRICK",
    "Z_PCT_RET"
  ],
  "indicators": [
    {
      "id": "Z_MAIN",
      "shortName": "战法主图",
      "kind": "composite",
      "panel": "main",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": True,
      "summary": "战法核心主图：白线(DEMA10) + 黄线(多空线 14/28/57/114) + BBI 牵牛绳",
      "calcParams": [10, 14],
      "params": [
        {
          "name": "white_period",
          "value": 10
        }
      ],
      "series": [
        {
          "key": "zg_white",
          "label": "白线",
          "formula": "DEMA",
          "type": "line",
          "precision": 2,
          "color": "white",
          "lineWidth": 1.8,
          "params": [10]
        },
        {
          "key": "dg_yellow",
          "label": "黄线",
          "formula": "LONGBBI",
          "type": "line",
          "precision": 2,
          "color": "yellow",
          "lineWidth": 1.8,
          "params": [14, 28, 57, 114]
        },
        {
          "key": "bbi",
          "label": "BBI",
          "formula": "BBI",
          "type": "line",
          "precision": 2,
          "color": "orange",
          "lineWidth": 1.8,
          "params": [3, 6, 12, 24]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_SIGNALS",
      "shortName": "信号层",
      "kind": "overlay",
      "panel": "main",
      "formulaVersion": "v1",
      "precision": 0,
      "defaultEnabled": False,
      "summary": "同花顺风格主图装饰：神奇九转 1..9 + K 线形态气泡 + 极值价格引导线",
      "params": [],
      "series": [],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "ZG_WHITE",
      "shortName": "白线",
      "kind": "line",
      "panel": "main",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "summary": "白线 = 二次平滑指数均线 DEMA(EMA(CLOSE,10),10)",
      "params": [
        {
          "name": "period",
          "value": 10
        }
      ],
      "series": [
        {
          "key": "zg_white",
          "label": "白线(10)",
          "formula": "DEMA",
          "type": "line",
          "precision": 2,
          "color": "white",
          "lineWidth": 1.8,
          "params": [10]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "DG_YELLOW",
      "shortName": "黄线",
      "kind": "line",
      "panel": "main",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "summary": "黄线（知行多空线/大哥线）= (MA14+MA28+MA57+MA114)/4",
      "params": [
        {
          "name": "period_1",
          "value": 14
        },
        {
          "name": "period_2",
          "value": 28
        },
        {
          "name": "period_3",
          "value": 57
        },
        {
          "name": "period_4",
          "value": 114
        }
      ],
      "series": [
        {
          "key": "dg_yellow",
          "label": "黄线(14/28/57/114)",
          "formula": "LONGBBI",
          "type": "line",
          "precision": 2,
          "color": "yellow",
          "lineWidth": 1.8,
          "params": [14, 28, 57, 114]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_BBI",
      "shortName": "BBI",
      "kind": "line",
      "panel": "main",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "summary": "牵牛绳多空平衡线 = (MA3+MA6+MA12+MA24)/4",
      "params": [
        {
          "name": "period_1",
          "value": 3
        },
        {
          "name": "period_2",
          "value": 6
        },
        {
          "name": "period_3",
          "value": 12
        },
        {
          "name": "period_4",
          "value": 24
        }
      ],
      "series": [
        {
          "key": "bbi",
          "label": "BBI",
          "formula": "BBI",
          "type": "line",
          "precision": 2,
          "color": "orange",
          "lineWidth": 1.8,
          "params": [3, 6, 12, 24]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_VOL",
      "shortName": "成交量",
      "kind": "subchart",
      "panel": "sub",
      "formulaVersion": "v1",
      "precision": 0,
      "defaultEnabled": True,
      "summary": "经典同花顺成交量：红绿量柱 + MA5/MA10 均量线",
      "params": [
        {
          "name": "ma_short",
          "value": 5
        },
        {
          "name": "ma_long",
          "value": 10
        }
      ],
      "series": [
        {
          "key": "vol",
          "label": "总量",
          "formula": "VOLUME",
          "type": "bar",
          "baseValue": 0,
          "precision": 0,
          "color": "volume_bar",
          "params": []
        },
        {
          "key": "ma5",
          "label": "MA5",
          "formula": "SMA",
          "type": "line",
          "precision": 0,
          "color": "auxAmber",
          "lineWidth": 1.2,
          "params": [5]
        },
        {
          "key": "ma10",
          "label": "MA10",
          "formula": "SMA",
          "type": "line",
          "precision": 0,
          "color": "auxSky",
          "lineWidth": 1.2,
          "params": [10]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_MACD",
      "shortName": "MACD",
      "kind": "subchart",
      "panel": "sub",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "summary": "同花顺风 MACD：DIFF + DEA + 柱状图 + 金叉/死叉徽章",
      "params": [
        {
          "name": "short",
          "value": 12
        },
        {
          "name": "long",
          "value": 26
        },
        {
          "name": "signal",
          "value": 9
        }
      ],
      "series": [
        {
          "key": "dif",
          "label": "DIFF",
          "formula": "MACD_DIF",
          "type": "line",
          "precision": 2,
          "color": "auxAmber",
          "lineWidth": 1.3,
          "params": [12, 26]
        },
        {
          "key": "dea",
          "label": "DEA",
          "formula": "MACD_DEA",
          "type": "line",
          "precision": 2,
          "color": "auxSky",
          "lineWidth": 1.3,
          "params": [12, 26, 9]
        },
        {
          "key": "macd",
          "label": "MACD",
          "formula": "MACD_HIST",
          "type": "bar",
          "baseValue": 0,
          "precision": 2,
          "color": "macd_hist",
          "params": [12, 26, 9]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_KDJ",
      "shortName": "KDJ",
      "kind": "subchart",
      "panel": "sub",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "summary": "同花顺风 KDJ：K/D/J 三线 + 金叉/死叉徽章",
      "params": [
        {
          "name": "n",
          "value": 9
        },
        {
          "name": "k_smooth",
          "value": 3
        },
        {
          "name": "d_smooth",
          "value": 3
        }
      ],
      "series": [
        {
          "key": "k",
          "label": "K",
          "formula": "KDJ_K",
          "type": "line",
          "precision": 2,
          "color": "auxOrange",
          "lineWidth": 1.3,
          "params": [9, 3]
        },
        {
          "key": "d",
          "label": "D",
          "formula": "KDJ_D",
          "type": "line",
          "precision": 2,
          "color": "auxSky",
          "lineWidth": 1.3,
          "params": [9, 3, 3]
        },
        {
          "key": "j",
          "label": "J",
          "formula": "KDJ_J",
          "type": "line",
          "precision": 2,
          "color": "kdjJ",
          "lineWidth": 1.3,
          "params": [9, 3, 3]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "ZX_BRICK",
      "shortName": "ZX砖型图",
      "kind": "subchart",
      "panel": "sub",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": True,
      "summary": "同花顺知行砖型图：连续同向 K 线合并成阶梯砖块，块数代表趋势强度",
      "params": [
        {
          "name": "lookback",
          "value": 4
        }
      ],
      "series": [
        {
          "key": "brick",
          "label": "砖型图",
          "formula": "ZX_BRICK",
          "type": "line",
          "precision": 2,
          "color": "brick",
          "lineWidth": 1.8,
          "params": [4]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_BRICK",
      "shortName": "ZX砖型图",
      "kind": "subchart",
      "panel": "sub",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "aliasOf": "ZX_BRICK",
      "summary": "砖型图（ZX_BRICK 的旧 id 别名，保留兼容用户已保存的图表配置）",
      "params": [
        {
          "name": "lookback",
          "value": 4
        }
      ],
      "series": [
        {
          "key": "brick",
          "label": "砖型图",
          "formula": "ZX_BRICK",
          "type": "line",
          "precision": 2,
          "color": "brick",
          "lineWidth": 1.8,
          "params": [4]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    },
    {
      "id": "Z_PCT_RET",
      "shortName": "涨跌幅",
      "kind": "subchart",
      "panel": "sub",
      "formulaVersion": "v1",
      "precision": 2,
      "defaultEnabled": False,
      "summary": "区间涨跌幅（百分比）：3 日与 21 日两个周期。与 DuckDB 的 RSL 百分位排名不同义",
      "params": [
        {
          "name": "short",
          "value": 3
        },
        {
          "name": "long",
          "value": 21
        }
      ],
      "series": [
        {
          "key": "pct_ret_short",
          "label": "3日涨跌幅(%)",
          "formula": "PCT_RET",
          "type": "line",
          "precision": 2,
          "color": "sky",
          "lineWidth": 1.5,
          "params": [3]
        },
        {
          "key": "pct_ret_long",
          "label": "21日涨跌幅(%)",
          "formula": "PCT_RET",
          "type": "line",
          "precision": 2,
          "color": "purple",
          "lineWidth": 1.5,
          "params": [21]
        }
      ],
      "storage": {
        "backend": "frontend",
        "duckdbView": "",
        "columns": []
      }
    }
  ],
  "mainPresets": [
    {
      "id": "zettaranc",
      "label": "双线+BBI",
      "hint": "战法核心主图：快线 DEMA10 + 大哥线 LongBBI(14/28/57/114) + BBI牵牛绳(3/6/12/24)。双线判断多空节奏，牵牛绳是多空分界",
      "indicators": [
        "Z_MAIN"
      ]
    },
    {
      "id": "all",
      "label": "战法+MA",
      "hint": "战法核心线 + 传统 MA5/10/20。给短期均线做参考，适合看价格与短期成本的相对位置",
      "indicators": [
        "MA",
        "Z_MAIN"
      ]
    },
    {
      "id": "ma",
      "label": "传统MA",
      "hint": "纯传统均线 MA5/10/20/60/120/250，不叠加战法线。最基础的看图方式",
      "indicators": [
        "MA",
        "Z_SIGNALS"
      ]
    },
    {
      "id": "boll",
      "label": "BOLL",
      "hint": "布林带 BOLL(20,2) + 战法信号层。价格触上轨偏强、触下轨偏弱，带宽收窄常预示变盘",
      "indicators": [
        "BOLL",
        "Z_SIGNALS"
      ]
    }
  ],
  "subPresets": [
    {
      "id": "VOL_AND_BRICK",
      "label": "量+ZX砖型 (推荐)",
      "hint": "成交量 + 同花顺知行砖型图。砖型把连续同向的 K 线合并成一块，块数代表趋势强度：4 块以上为强势。推荐作为默认副图。",
      "indicators": [
        "Z_VOL",
        "ZX_BRICK"
      ]
    },
    {
      "id": "ZX_BRICK",
      "label": "ZX砖型图",
      "hint": "仅砖型图，不带成交量。适合专注看多空节奏；减号标记回调、止字标记止跌。",
      "indicators": [
        "ZX_BRICK"
      ]
    },
    {
      "id": "VOL_AND_MACD",
      "label": "量+MACD",
      "hint": "成交量 + MACD。DIF/DEA 金叉死叉会打标记，红柱绿柱表示动能强弱，适合判断趋势转折。",
      "indicators": [
        "Z_VOL",
        "Z_MACD"
      ]
    },
    {
      "id": "Z_VOL",
      "label": "成交量",
      "hint": "成交量柱 + MA5/MA10 均量线。放量上涨代表资金进场，缩量回调代表抛压不重。",
      "indicators": [
        "Z_VOL"
      ]
    },
    {
      "id": "Z_MACD",
      "label": "MACD",
      "hint": "MACD (12,26,9)。DIF 上穿 DEA 为金叉、下穿为死叉，柱状体表示动能变化速度。",
      "indicators": [
        "Z_MACD"
      ]
    },
    {
      "id": "Z_KDJ",
      "label": "KDJ",
      "hint": "KDJ 随机指标 (9,3,3)。K/D 在 20 以下为超卖区、80 以上为超买区，金叉死叉会打标记。",
      "indicators": [
        "Z_KDJ"
      ]
    },
    {
      "id": "Z_PCT_RET",
      "label": "涨跌幅",
      "hint": "区间涨跌幅（百分比），3 日与 21 日两个周期。向上表示这只票在涨，适合快速比对强弱。与 RSL 百分位排名不同指标。",
      "indicators": [
        "Z_PCT_RET"
      ]
    }
  ],
  "knownGaps": []
}


#: 指标 id -> 指标元数据
INDICATORS_BY_ID: Dict[str, Dict[str, Any]] = {
    ID: INDICATOR_META["indicators"][i] for i, ID in enumerate(INDICATOR_META["ids"])
}

#: 指标 id -> 周期列表（按声明顺序）
INDICATOR_PARAMS: Dict[str, List[int]] = {
    k: [p["value"] for p in v["params"]] for k, v in INDICATORS_BY_ID.items()
}

#: DuckDB 短别名 -> 真实列名。analysis 的 SQL 应该照着这张表生成列映射，
#: 不要在别处硬编码列名。
DUCKDB_COLUMNS: Dict[str, str] = {
    c["alias"]: c["column"]
    for ind in INDICATOR_META["indicators"]
    for c in ind["storage"]["columns"]
}

#: 跨栈容差（|go - js| <= 该值视为一致）
ABS_TOLERANCE: float = INDICATOR_META["absTolerance"]


def get_indicator(indicator_id: str) -> Dict[str, Any]:
    """按 id 取指标元数据。未知 id 直接抛错，不返回 None。

    拼错 id 必须在调用点炸掉，而不是让上层拿到一个空字典然后画出空白图。
    """
    found = INDICATORS_BY_ID.get(indicator_id)
    if found is None:
        raise KeyError(
            "config/indicators.yaml 里没有指标 %r（已知: %s）"
            % (indicator_id, ", ".join(INDICATORS_BY_ID))
        )
    return found


def params(indicator_id: str) -> List[int]:
    """按声明顺序返回指标周期。"""
    return list(INDICATOR_PARAMS[indicator_id])


def series_keys(indicator_id: str) -> List[str]:
    """返回指标所有输出字段名（figures 的 key，顺序即绘图顺序）。"""
    return [s["key"] for s in get_indicator(indicator_id)["series"]]
