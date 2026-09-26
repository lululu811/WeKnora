"""Strategy detection routes"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

router = APIRouter(prefix="/strategies", tags=["Strategy Detection"])

class StrategyRequest(BaseModel):
    thscode: str
    days: int = 120
    strategy: Optional[str] = None  # Specific strategy to detect

@router.post("/detect")
async def detect_strategies(request: StrategyRequest) -> Dict[str, Any]:
    """
    Detect trading strategies for a stock.

    Available strategies:
    - B1: Oversold bounce (J < 0)
    - B2: Trend confirmation
    - SB1: Enhanced B1
    - shaofu: "Young Lady" strategy
    - four_bricks: Four bricks pattern
    - kirin: Kirin meeting
    """
    try:
        # Placeholder - will integrate with zettaranc-skill/modules/strategies/
        return {
            "success": True,
            "thscode": request.thscode,
            "strategies": [
                {"name": "B1", "signal": "buy", "confidence": 0.85},
                {"name": "four_bricks", "signal": "hold", "confidence": 0.72}
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
