import asyncio
from services.eodhd_client import get_bulk_fundamentals
async def main():
    res = await get_bulk_fundamentals("US")
    # if dict
    if isinstance(res, dict):
        keys = list(res.keys())
        if keys:
            print(res[keys[0]])
    elif isinstance(res, list) and res:
        print(res[0])
asyncio.run(main())
