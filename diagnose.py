import sqlite3
import os
import sys

OUTPUT_FILE = 'db_status.txt'

def check_db():
    with open(OUTPUT_FILE, 'w') as f:
        if not os.path.exists('photovault.db'):
            f.write("Database not found.\n")
            return

        try:
            conn = sqlite3.connect('photovault.db')
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            f.write("--- Photo Type Summary ---\n")
            c.execute("SELECT type, COUNT(*) as count FROM photos GROUP BY type")
            rows = c.fetchall()
            for row in rows:
                type_val = row['type'] if row['type'] is not None else "NULL"
                f.write(f"Type: {type_val} - Count: {row['count']}\n")

            f.write("\n--- Photos with NULL type ---\n")
            c.execute("SELECT id, path FROM photos WHERE type IS NULL LIMIT 10")
            rows = c.fetchall()
            for row in rows:
                f.write(f"ID: {row['id']}, Path: {os.path.basename(row['path'])}\n")
            
            f.write("\n--- Screenshots with ACTUAL face mappings (Should be 0) ---\n")
            c.execute("""
                SELECT count(*) as count 
                FROM photos p
                JOIN photo_people pp ON p.id = pp.photo_id
                WHERE p.type='screenshot'
            """)
            row = c.fetchone()
            f.write(f"Screenshots with faces mapped: {row['count']}\n")

            f.write("\n--- Screenshots with descriptions (Should be 0) ---\n")
            c.execute("SELECT count(*) as count FROM photos WHERE type='screenshot' AND description IS NOT NULL AND description != ''")
            row = c.fetchone()
            f.write(f"Screenshots with descriptions: {row['count']}\n")

            conn.close()
            f.write("Done.\n")
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    check_db()
