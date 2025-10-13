import asyncio
import sqlite3
import time
from datetime import timedelta


async def ttl_cleaner(
    db_path: str, *, steps_ttl_days: int, memory_ttl_days: int, interval_sec: int = 3600
) -> None:
    steps_ttl = timedelta(days=steps_ttl_days).total_seconds()
    mem_ttl = timedelta(days=memory_ttl_days).total_seconds()
    while True:
        try:
            now = time.time()
            conn = sqlite3.connect(db_path, check_same_thread=False)
            with conn:
                conn.execute(
                    "DELETE FROM steps WHERE ts < ?",
                    (now - steps_ttl,),
                )
                conn.execute(
                    "DELETE FROM memory WHERE ts < ?",
                    (now - mem_ttl,),
                )
            conn.close()
        except Exception:
            # best effort; swallow errors to avoid crashing the loop
            pass
        await asyncio.sleep(interval_sec)
