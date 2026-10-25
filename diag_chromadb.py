import sqlite3, os, sys

db_path = os.path.join("data", "vector_db", "chroma.sqlite3")
if not os.path.exists(db_path):
    print("No chroma.sqlite3 found at:", db_path)
    sys.exit(0)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

print("=== Tables ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(tables)

print("\n=== user_version ===")
cur.execute("PRAGMA user_version")
print(cur.fetchone())

print("\n=== collections columns ===")
cur.execute("PRAGMA table_info(collections)")
cols = cur.fetchall()
for c in cols:
    print(c)

print("\n=== Row count in embeddings ===")
try:
    cur.execute("SELECT COUNT(*) FROM embeddings")
    print(cur.fetchone())
except Exception as e:
    print("  error:", e)

conn.close()
