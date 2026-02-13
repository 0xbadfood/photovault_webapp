import sqlite3
import os
import sys
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

def reset_database(user_email=None):
    """
    Per-user database and thumbnail reset.
    If user_email is provided, only that user is reset.
    Otherwise, all users are reset.
    """
    if user_email:
        users = [user_email]
    else:
        if not os.path.exists(DATA_DIR):
            print(f"❌ Data directory not found: {DATA_DIR}")
            return
        users = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d))]
    
    if not users:
        print("No users found.")
        return
    
    print("=" * 60)
    print("COMPLETE DATABASE RESET")
    print("=" * 60)
    print(f"\nUsers to reset: {', '.join(users)}")
    print("\nThis will:")
    print("  • Delete ALL photo records from each user's database")
    print("  • Clear all people, albums, and mappings")
    print("  • Remove ALL thumbnail files")
    print("  • Daemon will need to re-scan and re-process everything")
    print("\n" + "=" * 60)
    
    confirm = input("\nType 'RESET' to confirm complete reset: ")
    if confirm != 'RESET':
        print("Reset cancelled.")
        return
    
    for userid in users:
        user_path = os.path.join(DATA_DIR, userid)
        db_path = os.path.join(user_path, 'photovault.db')
        
        print(f"\n--- Resetting user: {userid} ---")
        
        # Step 1: Reset Database
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            
            try:
                print("  [1/2] Resetting database...")
                
                c.execute("DROP TABLE IF EXISTS photos")
                print("    ✓ Dropped photos table")
                
                c.execute("DROP TABLE IF EXISTS photo_people")
                print("    ✓ Dropped photo_people table")
                
                c.execute("DROP TABLE IF EXISTS people")
                print("    ✓ Dropped people table")
                
                c.execute("DROP TABLE IF EXISTS album_photos")
                print("    ✓ Dropped album_photos table")
                
                c.execute("DROP TABLE IF EXISTS albums")
                print("    ✓ Dropped albums table")
                
                try:
                    c.execute("DELETE FROM sqlite_sequence")
                except:
                    pass
                print("    ✓ Reset ID counters")
                
                conn.commit()
                print("  ✅ Database cleared")
                
            except Exception as e:
                print(f"  ❌ Error resetting database: {e}")
                conn.rollback()
            finally:
                conn.close()
        else:
            print(f"  ⚠ No database found at {db_path}")
        
        # Step 2: Remove thumbnails
        thumbs_dir = os.path.join(user_path, 'thumbnails')
        if os.path.exists(thumbs_dir):
            print("  [2/2] Removing thumbnails...")
            try:
                thumb_count = len([f for f in os.listdir(thumbs_dir) if os.path.isfile(os.path.join(thumbs_dir, f))])
                shutil.rmtree(thumbs_dir)
                os.makedirs(thumbs_dir, exist_ok=True)
                print(f"    ✓ Removed {thumb_count} thumbnails")
            except Exception as e:
                print(f"    ❌ Error removing thumbnails: {e}")
        else:
            print("  ⚠ No thumbnails directory found")
    
    print("\n" + "=" * 60)
    print("✅ RESET COMPLETE!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Run: python3 daemon.py")
    print("  2. Daemon will re-scan and re-process everything")
    print("\n" + "=" * 60)

if __name__ == '__main__':
    email = sys.argv[1] if len(sys.argv) > 1 else None
    reset_database(email)
