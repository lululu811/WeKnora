"""
/query/ 只读 SQL 校验与表函数防注入测试
"""
import pytest
from main import _read_only_sql
from datasources.duckdb_source import _duckdb_config


def test_forbidden_sql_rejects_table_functions():
    forbidden = [
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT * FROM read_parquet('/tmp/test.parquet')",
        "SELECT * FROM read_json('/tmp/test.json')",
        "SELECT * FROM read_text('/tmp/test.txt')",
        "SELECT * FROM glob('/*')",
        "SELECT * FROM sniff_csv('/tmp/test.csv')",
        "SELECT * FROM parquet_scan('/tmp/test.parquet')",
        "SELECT * FROM sqlite_scan('/tmp/test.db', 't')",
        "SELECT * FROM postgres_scan('host=localhost', 't')",
    ]
    for sql in forbidden:
        with pytest.raises(Exception) as exc_info:
            _read_only_sql(sql)
        assert "被禁止的关键字" in str(exc_info.value.detail)


def test_allowed_sql_passes():
    allowed = [
        "SELECT 1 AS one",
        "SELECT thscode, name FROM dim_symbol WHERE thscode = '600519.SH'",
        "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
    ]
    for sql in allowed:
        assert _read_only_sql(sql) == sql


def test_duckdb_config_disables_external_access():
    cfg = _duckdb_config()
    assert cfg.get("enable_external_access") == "false"
