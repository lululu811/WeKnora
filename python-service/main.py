"""
WeKnora Python Service
Provides DuckDB query and zettaranc-skill functionality via HTTP API.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
import sys

# Add parent directory to path for importing zettaranc-skill modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = FastAPI(
    title="WeKnora Python Service",
    description="DuckDB query and zettaranc-skill functionality",
    version="1.0.0"
)

# Configuration
DB_DIR = os.getenv("DB_DIR", "/Users/chenlei/.hithink-finance")

# Import routes
from routes import query, indicators, strategies, screener

app.include_router(query.router)
app.include_router(indicators.router)
app.include_router(strategies.router)
app.include_router(screener.router)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "db_dir": DB_DIR}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "WeKnora Python Service",
        "version": "1.0.0",
        "endpoints": [
            "/health",
            "/query",
            "/indicators",
            "/strategies",
            "/screen"
        ]
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 50052))
    uvicorn.run(app, host="0.0.0.0", port=port)
