import hashlib
import time
from typing import Dict


def build_math_provenance(expr: str) -> Dict:
    return {"expr": expr, "ts": int(time.time())}


def build_sql_provenance(sql: str) -> Dict:
    h = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
    return {"sql_hash": h}


def build_url_provenance(url: str) -> Dict:
    return {"url": url}
