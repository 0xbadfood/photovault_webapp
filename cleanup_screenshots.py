import sqlite3
import os

DB_PATH = 'photovault.db'

def cleanup():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("Cleaning up screenshot data...")

    # 1. Get all screenshot IDs
    c.execute("SELECT id FROM photos WHERE type = 'screenshot'")
    rows = c.fetchall()
    screenshot_ids = [row['id'] for row in rows]
    
    if not screenshot_ids:
        print("No screenshots found.")
        return

    print(f"Found {len(screenshot_ids)} screenshots.")

    # 2. Remove descriptions
    c.execute("UPDATE photos SET description = NULL WHERE type = 'screenshot' AND description IS NOT NULL")
    print(f"Cleared descriptions for {c.rowcount} screenshots.")

    # 3. Remove photo_people mappings
    # We need to construct a query to delete where photo_id is in our list
    # simpler to just do standard SQL
    placeholders = ','.join('?' for _ in screenshot_ids)
    
    # Check how many mappings exist first
    c.execute(f"SELECT COUNT(*) as count FROM photo_people WHERE photo_id IN ({placeholders})", screenshot_ids)
    count = c.fetchone()['count']
    print(f"Found {count} face mappings for screenshots.")

    if count > 0:
        c.execute(f"DELETE FROM photo_people WHERE photo_id IN ({placeholders})", screenshot_ids)
        print(f"Deleted {c.rowcount} face mappings.")
    
    # 4. Should we remove 'people' who only exist in screenshots?
    # That's harder. If a person was created ONLY from a screenshot, they will now be an orphan in 'people' table (no photo_people links).
    # We can find people with no photos.
    c.execute("""
        DELETE FROM people 
        WHERE id NOT IN (SELECT DISTINCT person_id FROM photo_people)
    """)
    print(f"Deleted {c.rowcount} orphaned people (created only from screenshots).")

    conn.commit()
    conn.close()
    print("Cleanup complete.")

if __name__ == "__main__":
    cleanup()
