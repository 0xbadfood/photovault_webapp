import os
import sqlite3

# Define paths relative to this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

print(f"Fixing people thumbnails in: {DATA_DIR}")

if not os.path.exists(DATA_DIR):
    print(f"Directory not found: {DATA_DIR}")
    exit(1)

count = 0
for userid in os.listdir(DATA_DIR):
    user_dir = os.path.join(DATA_DIR, userid)
    if os.path.isdir(user_dir):
        db_path = os.path.join(user_dir, 'photovault.db')
        thumb_dir = os.path.join(user_dir, 'thumbnails')
        
        if os.path.exists(db_path):
            try:
                print(f"Checking database for: {userid}")
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                
                c.execute("SELECT id, thumbnail_path FROM people WHERE thumbnail_path IS NOT NULL")
                people = c.fetchall()
                
                for p in people:
                    orig_path = p['thumbnail_path']
                    # If path contains /data/ and it's NOT in our own directory, it's likely a shared thumb
                    if '/backup/data/' in orig_path and f'/backup/data/{userid}/' not in orig_path:
                        print(f"  - Found external thumb: {orig_path}")
                        
                        if os.path.exists(orig_path):
                            filename = os.path.basename(orig_path)
                            local_thumb_path = os.path.join(thumb_dir, filename)
                            
                            if not os.path.exists(local_thumb_path):
                                try:
                                    os.symlink(os.path.realpath(orig_path), local_thumb_path)
                                    print(f"    - Created symlink: {local_thumb_path}")
                                except Exception as e:
                                    print(f"    - Error symlinking: {e}")
                            
                            # Update DB path to use local symlink
                            c.execute("UPDATE people SET thumbnail_path = ? WHERE id = ?", (local_thumb_path, p['id']))
                            count += 1
                        else:
                            print(f"    - Original file missing: {orig_path}")
                
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"  - Error processing {userid}: {e}")

print(f"Fix complete. Updated {count} people thumbnails.")
