import asyncio
from database import async_session_maker
from services.analyzer import filter_screener_stocks

async def main():
    async with async_session_maker() as db:
        res = await filter_screener_stocks({"limit": 5, "sort_by": "volume", "sort_desc": True}, db)
        for r in res["items"]:
            print(r["ticker"], r["market_cap"], r["pe_ratio"])

asyncio.run(main())
