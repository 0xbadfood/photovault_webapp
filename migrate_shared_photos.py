import os
import sqlite3

# Define paths relative to this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

print(f"Migrating databases in: {DATA_DIR}")

if not os.path.exists(DATA_DIR):
    print(f"Directory not found: {DATA_DIR}")
    exit(1)

# The SQL schema for the missing table
CREATE_TABLE_SQL = '''
    CREATE TABLE IF NOT EXISTS shared_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_photo_id INTEGER NOT NULL,
        owner_email TEXT NOT NULL,
        recipient_email TEXT NOT NULL,
        recipient_photo_id INTEGER,
        shared_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(original_photo_id, recipient_email)
    )
'''

count = 0
for userid in os.listdir(DATA_DIR):
    user_dir = os.path.join(DATA_DIR, userid)
    if os.path.isdir(user_dir):
        db_path = os.path.join(user_dir, 'photovault.db')
        if os.path.exists(db_path):
            try:
                print(f"Migrating {userid} -> {db_path}")
                conn = sqlite3.connect(db_path)
                c = conn.cursor()
                c.execute(CREATE_TABLE_SQL)
                conn.commit()
                conn.close()
                print(f"  - Success")
                count += 1
            except Exception as e:
                print(f"  - Error migrating {userid}: {e}")

print(f"Migration complete. Updated {count} user databases.")
