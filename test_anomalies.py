import asyncio
from database import async_session_maker
from services.anomaly_detector import scan_and_analyze_anomalies
import json
import logging

logging.basicConfig(level=logging.INFO)

async def test_anomalies():
    async with async_session_maker() as db:
        print("Starting anomaly scan...")
        results = await scan_and_analyze_anomalies(db, limit_count=2)
        print("\n=== ANOMALY RESULTS ===")
        print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(test_anomalies())
