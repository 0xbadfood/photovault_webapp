import sqlite3
import os
import sys
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

def reset_database(user_email=None):
    """
    Complete per-user reset: nuke database, thumbnails, shared files, face data.
    Preserves only original photos (device directories with actual files).
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
    print("COMPLETE DATABASE & DATA RESET")
    print("=" * 60)
    print(f"\nUsers to reset: {', '.join(users)}")
    print("\nThis will DELETE:")
    print("  • Database (photovault.db) — all tables dropped & recreated")
    print("  • ALL thumbnails (photo + face thumbnails)")
    print("  • ALL shared files and symlinks (shared/ directory)")
    print("\nThis will PRESERVE:")
    print("  • Original photos in device directories")
    print("\nDaemon will need to re-scan and re-process everything.")
    print("\n" + "=" * 60)
    
    confirm = input("\nType 'RESET' to confirm complete reset: ")
    if confirm != 'RESET':
        print("Reset cancelled.")
        return
    
    for userid in users:
        user_path = os.path.join(DATA_DIR, userid)
        db_path = os.path.join(user_path, 'photovault.db')
        
        print(f"\n{'─' * 50}")
        print(f"Resetting user: {userid}")
        print(f"{'─' * 50}")
        
        # Step 1: Nuke the database entirely
        if os.path.exists(db_path):
            print("  [1/3] Nuking database...")
            try:
                os.remove(db_path)
                print("    ✓ Deleted photovault.db")
                
                # Recreate with fresh schema
                import database
                database.init_db(userid)
                print("    ✓ Recreated with fresh schema")
            except Exception as e:
                print(f"    ❌ Error: {e}")
        else:
            print(f"  [1/3] No database found (skipping)")
        
        # Step 2: Nuke ALL thumbnails (photo thumbnails + face thumbnails)
        thumbs_dir = os.path.join(user_path, 'thumbnails')
        if os.path.exists(thumbs_dir):
            print("  [2/3] Removing all thumbnails...")
            try:
                files = [f for f in os.listdir(thumbs_dir) if os.path.isfile(os.path.join(thumbs_dir, f))]
                thumb_count = len(files)
                face_count = len([f for f in files if f.startswith('face_')])
                shutil.rmtree(thumbs_dir)
                os.makedirs(thumbs_dir, exist_ok=True)
                print(f"    ✓ Removed {thumb_count} thumbnails ({face_count} face thumbnails)")
            except Exception as e:
                print(f"    ❌ Error: {e}")
        else:
            print("  [2/3] No thumbnails directory (skipping)")
        
        # Step 3: Nuke shared files directory (symlinks to shared photos)
        shared_dir = os.path.join(user_path, 'shared')
        if os.path.exists(shared_dir):
            print("  [3/3] Removing shared files...")
            try:
                file_count = 0
                for root, dirs, files in os.walk(shared_dir):
                    file_count += len(files)
                shutil.rmtree(shared_dir)
                print(f"    ✓ Removed shared directory ({file_count} files/symlinks)")
            except Exception as e:
                print(f"    ❌ Error: {e}")
        else:
            print("  [3/3] No shared directory (skipping)")
        
        print(f"  ✅ User {userid} reset complete")
    
    print("\n" + "=" * 60)
    print("✅ RESET COMPLETE!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Run: python3 daemon.py")
    print("  2. Daemon will re-scan and re-process everything")
    print("=" * 60)

if __name__ == '__main__':
    email = sys.argv[1] if len(sys.argv) > 1 else None
    reset_database(email)
