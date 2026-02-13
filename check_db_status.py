import sqlite3
import os

DB_PATH = 'photovault.db'

def check_db():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("--- Photo Type Summary ---")
    c.execute("SELECT type, COUNT(*) as count FROM photos GROUP BY type")
    rows = c.fetchall()
    for row in rows:
        print(f"Type: {row['type']} - Count: {row['count']}")

    print("\n--- Photos with NULL type ---")
    c.execute("SELECT id, path FROM photos WHERE type IS NULL LIMIT 10")
    rows = c.fetchall()
    for row in rows:
        print(f"ID: {row['id']}, Path: {os.path.basename(row['path'])}")
    
    print("\n--- Photos processed for faces but are screenshots (Should be 0 ideally) ---")
    c.execute("SELECT count(*) as count FROM photos WHERE type='screenshot' AND processed_for_faces=1")
    row = c.fetchone()
    print(f"Screenshots with faces processed: {row['count']}")

    conn.close()

if __name__ == "__main__":
    check_db()
