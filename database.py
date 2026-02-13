import sqlite3
import os
import io
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

def get_db_path(userid):
    """Returns path to per-user database: ../backup/data/<email>/photovault.db"""
    return os.path.join(DATA_DIR, userid, 'photovault.db')

def get_db_connection(userid):
    """Open a connection to the per-user database."""
    db_path = get_db_path(userid)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(userid):
    """Initialize schema for a specific user's database."""
    conn = get_db_connection(userid)
    c = conn.cursor()
    
    # Photos table - stores processed file paths and metadata
    c.execute('''
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            description TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            date_taken DATETIME,
            location_lat REAL,
            location_lon REAL,
            processed_for_thumbnails BOOLEAN DEFAULT 0,
            processed_for_faces BOOLEAN DEFAULT 0,
            processed_for_description BOOLEAN DEFAULT 0,
            processed_for_exif BOOLEAN DEFAULT 0,
            type TEXT
        )
    ''')
    
    # People table - stores unique people and their representative embedding
    c.execute('''
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT 'Unknown',
            thumbnail_path TEXT,
            embedding_blob BLOB
        )
    ''')
    
    # Mapping table - which person is in which photo
    c.execute('''
        CREATE TABLE IF NOT EXISTS photo_people (
            photo_id INTEGER,
            person_id INTEGER,
            FOREIGN KEY(photo_id) REFERENCES photos(id),
            FOREIGN KEY(person_id) REFERENCES people(id),
            UNIQUE(photo_id, person_id)
        )
    ''')
    
    # Albums table - stores album metadata
    c.execute('''
        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            cover_photo_id INTEGER,
            album_type TEXT DEFAULT 'manual',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(cover_photo_id) REFERENCES photos(id)
        )
    ''')
    
    # Album-Photo mapping table
    c.execute('''
        CREATE TABLE IF NOT EXISTS album_photos (
            album_id INTEGER,
            photo_id INTEGER,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(album_id) REFERENCES albums(id),
            FOREIGN KEY(photo_id) REFERENCES photos(id),
            UNIQUE(album_id, photo_id)
        )
    ''')
    
    # Shared photos tracking table
    c.execute('''
        CREATE TABLE IF NOT EXISTS shared_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_photo_id INTEGER NOT NULL,
            owner_email TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            recipient_photo_id INTEGER,
            shared_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(original_photo_id, recipient_email)
        )
    ''')
    
    conn.commit()
    conn.close()

def adapt_array(arr):
    out = io.BytesIO()
    np.save(out, arr)
    out.seek(0)
    return sqlite3.Binary(out.read())

def convert_array(text):
    out = io.BytesIO(text)
    out.seek(0)
    return np.load(out)

# Register numpy array adapter
sqlite3.register_adapter(np.ndarray, adapt_array)
sqlite3.register_converter("array", convert_array)

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python database.py <user_email>")
        print("  Initializes the per-user database for the given email.")
        sys.exit(1)
    init_db(sys.argv[1])
    print(f"Database initialized for {sys.argv[1]}.")
