import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLOBAL_SHARE_DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '../backup/global_share.db'))

conn = sqlite3.connect(GLOBAL_SHARE_DB_PATH)
c = conn.cursor()
try:
    c.execute("ALTER TABLE shared_links ADD COLUMN asset_title TEXT")
except Exception as e:
    print(e)
    
c.execute('''CREATE TABLE IF NOT EXISTS shared_asset_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_email TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    asset_title TEXT,
    asset_type TEXT NOT NULL,
    thumbnail_id INTEGER,
    shared_with_email TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_email, asset_id, asset_type, shared_with_email)
)''')
conn.commit()
conn.close()
