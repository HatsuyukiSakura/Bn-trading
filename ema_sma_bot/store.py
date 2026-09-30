"""SQLite 狀態儲存：權益高點、熔斷旗標、停損鎖、下單紀錄。持倉一律以交易所為準。"""
import sqlite3
from typing import Optional


class Store:
    def __init__(self, path: str = "state.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS orders(cid TEXT PRIMARY KEY, symbol TEXT, side TEXT,"
                        " qty TEXT, status TEXT, ts INTEGER, resp TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS equity(ts INTEGER, equity TEXT)")
        self.db.commit()

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.execute("SELECT v FROM state WHERE k=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO state(k, v) VALUES(?, ?)", (key, str(value)))
        self.db.commit()

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM state WHERE k=?", (key,))
        self.db.commit()

    def record_order(self, cid, symbol, side, qty, status, ts, resp) -> None:
        self.db.execute("INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?,?)",
                        (cid, symbol, side, str(qty), status, ts, str(resp)[:2000]))
        self.db.commit()

    def record_equity(self, ts: int, equity) -> None:
        self.db.execute("INSERT INTO equity VALUES(?, ?)", (ts, str(equity)))
        self.db.commit()

    def equity_since(self, ts: int) -> list:
        return [(t, v) for t, v in self.db.execute(
            "SELECT ts, equity FROM equity WHERE ts >= ? ORDER BY ts", (ts,))]
