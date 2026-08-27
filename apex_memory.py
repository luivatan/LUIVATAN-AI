"""User-scoped conversation memory, preferences, and model analytics."""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from dataclasses import dataclass

@dataclass
class ModelMetric:
    model: str; latency_ms: float; tokens: int; success: bool

class MemoryStore:
    def __init__(self, path="memory.sqlite3"):
        self.path=str(path)
        with self._db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY,user_id TEXT,question TEXT,answer TEXT,model TEXT,created_at INTEGER); CREATE TABLE IF NOT EXISTS preferences(user_id TEXT PRIMARY KEY,data TEXT); CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY,model TEXT,latency_ms REAL,tokens INTEGER,success INTEGER,created_at INTEGER);""")
    def _db(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; return db
    def remember(self,user_id,question,answer,model="default"):
        with self._db() as db: db.execute("INSERT INTO conversations(user_id,question,answer,model,created_at) VALUES(?,?,?,?,?)",(user_id,question,answer,model,int(time.time())))
    def recent(self,user_id,limit=8):
        with self._db() as db: rows=db.execute("SELECT * FROM conversations WHERE user_id=? ORDER BY id DESC LIMIT ?",(user_id,limit)).fetchall()
        return [dict(row) for row in reversed(rows)]
    def search(self,user_id,term):
        with self._db() as db: rows=db.execute("SELECT * FROM conversations WHERE user_id=? AND (question LIKE ? OR answer LIKE ?) ORDER BY id DESC",(user_id,f"%{term}%",f"%{term}%")).fetchall()
        return [dict(row) for row in rows]
    def delete(self,user_id,conversation_id=None):
        with self._db() as db:
            if conversation_id is None: db.execute("DELETE FROM conversations WHERE user_id=?",(user_id,))
            else: db.execute("DELETE FROM conversations WHERE user_id=? AND id=?",(user_id,conversation_id))
    def preferences(self,user_id):
        with self._db() as db: row=db.execute("SELECT data FROM preferences WHERE user_id=?",(user_id,)).fetchone()
        return json.loads(row[0]) if row else {}
    def set_preferences(self,user_id,data):
        with self._db() as db: db.execute("INSERT INTO preferences(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",(user_id,json.dumps(data)))
    def record_metric(self,metric):
        with self._db() as db: db.execute("INSERT INTO metrics(model,latency_ms,tokens,success,created_at) VALUES(?,?,?,?,?)",(metric.model,metric.latency_ms,metric.tokens,int(metric.success),int(time.time())))
    def analytics(self):
        with self._db() as db: rows=db.execute("SELECT model,COUNT(*) calls,AVG(latency_ms) avg_latency,AVG(success) success_rate,SUM(tokens) tokens FROM metrics GROUP BY model").fetchall()
        return [dict(row) for row in rows]

def build_model_comparison(answers: dict[str,str]) -> str:
    return "\n\n".join(f"### {model}\n{answer}" for model,answer in answers.items())

def trim_context(messages, max_chars=6000):
    selected=[]; used=0
    for message in reversed(messages):
        text=f"User: {message['question']}\nAssistant: {message['answer']}"
        if used+len(text)>max_chars: break
        selected.append(text); used+=len(text)
    return "\n\n".join(reversed(selected))
