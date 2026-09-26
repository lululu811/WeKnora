"""DuckDB query routes"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import json
import sys
import os

router = APIRouter(prefix="/query", tags=["DuckDB Query"])

class QueryRequest(BaseModel):
    db: str  # market, financials, fund, special, futures, index, indicators
    sql: str
    limit: int = 1000

@router.post("/")
async def query_duckdb(request: QueryRequest) -> Dict[str, Any]:
    """
    Execute a read-only SQL query on a DuckDB database.

    Available databases:
    - market: A-share K-line, adjust factors, symbols
    - financials: Financial statements, indicators, valuation
    - fund: Fund profile, NAV, ETF
    - special: Limit-up/down, dragon-tiger, hot stocks
    - futures: Futures varieties, contracts, daily
    - index: Index catalog, constituents, daily
    - indicators: Technical indicators (60+ indicators)
    """
    import duckdb

    db_path = f"{request.db}.duckdb"
    full_path = f"/Users/chenlei/.hithink-finance/{db_path}"

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"数据库文件不存在：{full_path}")

    try:
        # Connect read-only directly (DuckDB supports concurrent read-only connections)
        conn = duckdb.connect(full_path, read_only=True)

        # Add LIMIT if not present
        sql = request.sql
        if 'LIMIT' not in sql.upper():
            sql = f"{sql} LIMIT {request.limit}"

        # Execute query
        result = conn.execute(sql).fetchall()
        columns = [desc[0] for desc in conn.description]

        # Convert to list of dicts
        rows = [dict(zip(columns, row)) for row in result]

        conn.close()

        return {
            "success": True,
            "db": request.db,
            "count": len(rows),
            "data": rows
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databases")
async def list_databases() -> Dict[str, Any]:
    """List available DuckDB databases"""
    return {
        "databases": [
            "market", "financials", "fund", "special",
            "futures", "index", "indicators"
        ]
    }
