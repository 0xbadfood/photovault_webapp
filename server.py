import os
import hashlib
import secrets
import sqlite3
import database
import zipfile
import io
import time
from flask import Flask, request, jsonify, send_from_directory, render_template, abort, send_file

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = secrets.token_hex(16)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))
USER_DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '../backup/user.sql'))
GUEST_DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '../backup/guest.sql'))

def init_guest_db():
    """Create guest.sql tables if they don't exist."""
    conn = sqlite3.connect(GUEST_DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS guests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        host_count INTEGER DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS guest_hosts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guest_id INTEGER NOT NULL,
        host_email TEXT NOT NULL,
        added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_activated_date DATETIME,
        access_till DATETIME NOT NULL,
        status TEXT DEFAULT 'active',
        FOREIGN KEY(guest_id) REFERENCES guests(id),
        UNIQUE(guest_id, host_email)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS guest_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guest_id INTEGER NOT NULL,
        host_email TEXT NOT NULL,
        asset_type TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        guest_asset_id INTEGER,
        shared_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(guest_id) REFERENCES guests(id)
    )''')
    conn.commit()
    conn.close()

# Initialize guest DB on startup
init_guest_db()

def get_guest_db():
    """Get a connection to guest.sql with WAL mode."""
    conn = sqlite3.connect(GUEST_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn

def is_guest_user(email):
    """Check if the given email belongs to a guest rather than a full user."""
    if not email:
        return False
    try:
        conn = get_guest_db()
        c = conn.cursor()
        c.execute('SELECT id FROM guests WHERE email = ?', (email,))
        result = c.fetchone() is not None
        conn.close()
        return result
    except Exception:
        return False

def get_user_dir(userid):
    return os.path.join(DATA_DIR, userid)

def get_thumbnail_dir(userid):
    return os.path.join(get_user_dir(userid), 'thumbnails')

from flask import make_response

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    userid = data.get('userid')
    password = data.get('password')

    if not userid or not password:
        return jsonify({'error': 'User ID and password required'}), 400

    try:
        # --- Step 1: Check user.sql ---
        conn = sqlite3.connect(USER_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT password_hash, salt, status, is_admin, force_password_change FROM users WHERE email = ?", (userid,))
        row = c.fetchone()
        conn.close()
        
        if row:
            stored_hash, salt, status, is_admin, force_change = row
            if status != 'active':
                return jsonify({'error': 'Account is not active'}), 403
            calc_hash = hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
            if calc_hash == stored_hash:
                resp = make_response(jsonify({
                    'success': True, 
                    'userid': userid,
                    'is_admin': bool(is_admin),
                    'role': 'user',
                    'force_change': bool(force_change)
                }))
                resp.set_cookie('userid', userid, max_age=12 * 60 * 60)
                resp.set_cookie('role', 'user', max_age=12 * 60 * 60)
                return resp
            else:
                return jsonify({'error': 'Invalid password'}), 401
        
        # --- Step 2: Check guest.sql ---
        gconn = get_guest_db()
        gc = gconn.cursor()
        gc.execute("SELECT id, password_hash, password_salt FROM guests WHERE email = ?", (userid,))
        guest_row = gc.fetchone()
        
        if guest_row:
            guest_id = guest_row['id']
            calc_hash = hashlib.sha256(f"{password}{guest_row['password_salt']}".encode()).hexdigest()
            if calc_hash != guest_row['password_hash']:
                gconn.close()
                return jsonify({'error': 'Invalid password'}), 401
            
            # Check if any host relationship is active and not expired
            gc.execute("""SELECT host_email FROM guest_hosts 
                          WHERE guest_id = ? AND status = 'active' AND access_till >= datetime('now')""", (guest_id,))
            active_hosts = [r['host_email'] for r in gc.fetchall()]
            
            if not active_hosts:
                gconn.close()
                return jsonify({'error': 'Guest access has expired or been revoked'}), 403
            
            # Update last_login
            gc.execute("UPDATE guests SET last_login = datetime('now') WHERE id = ?", (guest_id,))
            gconn.commit()
            gconn.close()
            
            resp = make_response(jsonify({
                'success': True,
                'userid': userid,
                'is_admin': False,
                'role': 'guest',
                'hosts': active_hosts
            }))
            resp.set_cookie('userid', userid, max_age=12 * 60 * 60)
            resp.set_cookie('role', 'guest', max_age=12 * 60 * 60)
            return resp
        
        gconn.close()
        return jsonify({'error': 'User not found'}), 404
             
    except Exception as e:
        print(f"Auth Error: {e}")
        return jsonify({'error': 'Authentication failed due to server error'}), 500

@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    userid = request.cookies.get('userid')
    role = request.cookies.get('role', 'user')
    if userid:
        is_admin = False
        force_change = False
        try:
            conn = sqlite3.connect(USER_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT is_admin, force_password_change FROM users WHERE email = ?", (userid,))
            row = c.fetchone()
            if row:
                if row[0]: is_admin = True
                if row[1]: force_change = True
            conn.close()
        except:
            pass
        return jsonify({'authenticated': True, 'userid': userid, 'role': role, 'is_admin': is_admin, 'force_change': force_change})
    return jsonify({'authenticated': False}), 401

@app.route('/api/auth/change_password', methods=['POST'])
def change_password():
    userid = request.cookies.get('userid')
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    new_password = data.get('new_password')
    
    if not new_password:
        return jsonify({'error': 'New password required'}), 400
        
    try:
        conn = sqlite3.connect(USER_DB_PATH)
        c = conn.cursor()
        
        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256(f"{new_password}{salt}".encode()).hexdigest()
        
        c.execute("UPDATE users SET password_hash = ?, salt = ?, force_password_change = 0 WHERE email = ?", (password_hash, salt, userid))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    resp = jsonify({'success': True})
    resp.delete_cookie('userid')
    return resp

def get_current_userid():
    """Resolve userid from request params or cookie."""
    return request.args.get('userid') or (request.get_json(silent=True) or {}).get('userid') or request.cookies.get('userid')

@app.route('/api/scan', methods=['POST'])
def scan_files():
    # Scanning is now handled by daemon.py primarily for DB population.
    # However, frontend expects a tree structure. 
    # We can either construct it from DB or filesystem. 
    # Filesystem is still the source of truth for the tree view.
    userid = request.json.get('userid')
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_dir = get_user_dir(userid)
    if not os.path.exists(user_dir):
        return jsonify({'devices': []})

    devices = []
    try:
        entries = os.listdir(user_dir)
        for entry in entries:
            device_path = os.path.join(user_dir, entry)
            if os.path.isdir(device_path) and entry != 'thumbnails':
                files_dir = os.path.join(device_path, 'files')
                if os.path.exists(files_dir) and os.path.isdir(files_dir):
                    files = []
                    for root, _, filenames in os.walk(files_dir):
                        for filename in filenames:
                            # only basic check here, daemon does heavy lifting
                            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.avi', '.mkv')):
                                rel_path = os.path.relpath(os.path.join(root, filename), files_dir)
                                files.append({
                                    'filename': filename,
                                    'rel_path': rel_path,
                                    'device': entry,
                                    'type': 'video' if filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')) else 'image'
                                })
                    devices.append({
                        'name': entry,
                        'files': files
                    })
    except Exception as e:
        print(f"Error scanning: {e}")
        return jsonify({'error': str(e)}), 500

    return jsonify({'devices': devices})

@app.route('/resource/thumbnail/<userid>/<filename>')
def serve_thumbnail(userid, filename):
    thumb_dir = get_thumbnail_dir(userid)
    return send_from_directory(thumb_dir, filename)

def send_file_partial(path):
    """
    Use Flask's native send_file with conditional=True.
    This automatically handles Range requests (206), ETags, and Last-Modified.
    It is much more robust than hand-rolled partial content responses.
    """
    return send_file(path, conditional=True)

@app.route('/resource/image/<userid>/<device>/<path:filename>')
def serve_image(userid, device, filename):
    user_dir = get_user_dir(userid)
    file_path = os.path.join(user_dir, device, 'files', filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(user_dir)):
        abort(403)
    if not os.path.exists(file_path):
        abort(404)
    return send_file_partial(file_path)

@app.route('/api/photo/metadata', methods=['GET'])
def get_photo_metadata():
    userid = request.args.get('userid')
    photo_id = request.args.get('id')
    path_arg = request.args.get('path') # Relative path if ID not available
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
        
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    row = None
    if photo_id:
        c.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
        row = c.fetchone()
    elif path_arg:
        # Try to resolve path
        # Client sends path that might look like: "device/files/foo.jpg" or just "device/foo.jpg" depending on context
        # But we know how we construct image URLs: /resource/image/<userid>/<device>/<rel_path>
        # So client likely sends <device>/<rel_path>
        # Let's try to match it against stored paths.
        # Stored path is absolute: /home/photovault/backup/data/<userid>/<device>/files/...
        
        # We can construct the absolute path if we know the structure.
        # DATA_DIR/<userid>/<path_arg> (if path_arg includes device)
        # But wait, `path_arg` from client might be `myphone/files/DCIM/100APPLE/IMG_0001.JPG`
        
        # Let's try exact match on absolute path first
        user_dir = get_user_dir(userid)
        abs_path = os.path.normpath(os.path.join(user_dir, path_arg))
        
        
        c.execute("SELECT * FROM photos WHERE path = ?", (abs_path,))
        row = c.fetchone()
        
        # If not found, try inserting 'files' after device (first component)
        # abs_path: /data/userid/device/filename.jpg
        # Wanted: /data/userid/device/files/filename.jpg
        if not row:
             parts = path_arg.split(os.path.sep)
             if len(parts) >= 2 and parts[1] != 'files':
                 # Try inserting 'files'
                 new_path_arg = os.path.join(parts[0], 'files', *parts[1:])
                 new_abs = os.path.normpath(os.path.join(user_dir, new_path_arg))
                 c.execute("SELECT * FROM photos WHERE path = ?", (new_abs,))
                 row = c.fetchone()

    conn.close()
    
    if row:
        return jsonify({
            'found': True,
            'id': row['id'],
            'filename': os.path.basename(row['path']),
            'date_taken': row['date_taken'],
            'timestamp': row['timestamp'],
            'location_lat': row['location_lat'],
            'location_lon': row['location_lon'],
            'description': row['description'],
            'type': row['type']
        })
    else:
        # File might exist but not be in DB (e.g. not scanned, or just a file in explorer)
        # We can still return basic file info if it exists
        try:
            if path_arg:
                user_dir = get_user_dir(userid)
                # Try original path arg
                abs_path = os.path.normpath(os.path.join(user_dir, path_arg))
                if not os.path.exists(abs_path):
                     # Try the inserted 'files' path too
                     parts = path_arg.split(os.path.sep)
                     if len(parts) >= 2 and parts[1] != 'files':
                         new_path_arg = os.path.join(parts[0], 'files', *parts[1:])
                         new_abs_check = os.path.normpath(os.path.join(user_dir, new_path_arg))
                         if os.path.exists(new_abs_check):
                             abs_path = new_abs_check

                if os.path.exists(abs_path):
                     stat = os.stat(abs_path)
                     return jsonify({
                         'found': True,
                         'filename': os.path.basename(abs_path),
                         'timestamp': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                         'size': stat.st_size
                     })
        except Exception:
            pass
            
        return jsonify({'found': False, 'error': 'Metadata not found'})


# --- Dashboard API ---

@app.route('/api/dashboard/stats', methods=['GET'])
def get_dashboard_stats():
    userid = request.args.get('userid')
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    stats = {
        'storage': {},
        'media': {},
        'system': {},
        'ai': {}
    }
    
    try:
        # Media Stats
        c.execute("SELECT COUNT(*) FROM photos WHERE type != 'screenshot' AND type != 'video'") 
        stats['media']['photos'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM photos WHERE type = 'screenshot'")
        stats['media']['screenshots'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM photos WHERE type = 'video'")
        stats['media']['videos'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM albums")
        stats['media']['albums'] = c.fetchone()[0]
        
        # AI Stats
        c.execute("SELECT COUNT(*) FROM people")
        stats['ai']['people_count'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM photos WHERE processed_for_faces = 1")
        stats['ai']['processed_faces'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM photos WHERE processed_for_description = 1")
        stats['ai']['processed_desc'] = c.fetchone()[0]
        
        # Storage Stats
        user_dir = get_user_dir(userid)
        total_size = 0
        file_count = 0
        if os.path.exists(user_dir):
            for root, dirs, files in os.walk(user_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    if not os.path.islink(fp):
                        total_size += os.path.getsize(fp)
                    file_count += 1
        
        stats['storage']['used_bytes'] = total_size
        stats['storage']['file_count'] = file_count
        
        # System Stats (using psutil if available)
        try:
            import psutil
            import platform
            
            disk_path = DATA_DIR if os.path.exists(DATA_DIR) else '/'
            disk = psutil.disk_usage(disk_path)
            stats['system']['disk_total'] = disk.total
            stats['system']['disk_used'] = disk.used
            stats['system']['disk_free'] = disk.free
            stats['system']['disk_percent'] = disk.percent
            
            stats['system']['cpu_percent'] = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            stats['system']['memory_percent'] = mem.percent
            stats['system']['memory_used'] = mem.used
            stats['system']['memory_total'] = mem.total
            
            stats['system']['platform'] = platform.system()
            stats['system']['platform_release'] = platform.release()
            
            # Application Uptime (since process start)
            p = psutil.Process()
            stats['system']['uptime_seconds'] = int(time.time() - p.create_time())
            
        except ImportError:
             stats['system']['error'] = "psutil not installed"
        except Exception as e:
             stats['system']['error'] = str(e)
             
    except Exception as e:
        print(f"Dashboard Stats Error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
        
    return jsonify(stats)

@app.route('/api/people', methods=['GET'])
def list_people():
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.name, p.thumbnail_path, COUNT(pp.photo_id) as photo_count
        FROM people p
        LEFT JOIN photo_people pp ON p.id = pp.person_id
        GROUP BY p.id
        ORDER BY photo_count DESC
    """)
    rows = c.fetchall()
    people = [{'id': r['id'], 'name': r['name'], 'thumbnail': r['thumbnail_path'], 'photo_count': r['photo_count']} for r in rows]
    conn.close()
    return jsonify({'people': people})

@app.route('/api/people/update', methods=['POST'])
def update_person():
    data = request.json
    person_id = data.get('id')
    name = data.get('name')
    if not person_id or not name:
        return jsonify({'error': 'Missing args'}), 400
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()

    # Get old name to replace in descriptions
    c.execute("SELECT name FROM people WHERE id = ?", (person_id,))
    old_row = c.fetchone()
    old_name = old_row['name'] if old_row else None

    c.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))

    # Tag all associated photos with the person's name in their description
    c.execute("SELECT photo_id FROM photo_people WHERE person_id = ?", (person_id,))
    photo_ids = [r['photo_id'] for r in c.fetchall()]
    for pid in photo_ids:
        c.execute("SELECT description FROM photos WHERE id = ?", (pid,))
        row = c.fetchone()
        desc = row['description'] if row and row['description'] else ''
        tags = [t.strip() for t in desc.split(',') if t.strip()]

        # Remove old name tag if present
        if old_name:
            tags = [t for t in tags if t.lower() != old_name.lower()]

        # Add new name if not already present
        if name.lower() not in [t.lower() for t in tags]:
            tags.append(name)

        new_desc = ', '.join(tags)
        c.execute("UPDATE photos SET description = ? WHERE id = ?", (new_desc, pid))

    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/people/delete', methods=['POST'])
def delete_person():
    data = request.json
    person_id = data.get('id')
    if not person_id:
        return jsonify({'error': 'Missing args'}), 400
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    try:
        # Delete mappings first
        c.execute("DELETE FROM photo_people WHERE person_id = ?", (person_id,))
        # Delete person
        c.execute("DELETE FROM people WHERE id = ?", (person_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/people/<int:person_id>/photos', methods=['GET'])
def get_person_photos(person_id):
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = database.get_db_connection(userid)
    c = conn.cursor()

    # Get person info
    c.execute("SELECT id, name, thumbnail_path FROM people WHERE id = ?", (person_id,))
    person_row = c.fetchone()
    if not person_row:
        conn.close()
        return jsonify({'error': 'Person not found'}), 404

    # Get all photos for this person
    c.execute("""
        SELECT p.id, p.path, p.type, p.date_taken
        FROM photos p
        JOIN photo_people pp ON p.id = pp.photo_id
        WHERE pp.person_id = ?
        ORDER BY p.date_taken DESC
    """, (person_id,))
    rows = c.fetchall()

    photos = []
    for r in rows:
        try:
            photo_data = build_photo_response(r['path'], r['id'], r['type'], userid=userid)
            if photo_data:
                photos.append(photo_data)
        except Exception as e:
            print(f"Error processing person photo {r['id']}: {e}")
            continue

    conn.close()
    return jsonify({
        'person': {
            'id': person_row['id'],
            'name': person_row['name'],
            'thumbnail': person_row['thumbnail_path']
        },
        'photos': photos
    })

@app.route('/api/search', methods=['POST'])
def search_photos():
    data = request.json
    # { person_ids: [1, 2], description: "..." }
    person_ids = data.get('person_ids', [])
    description_query = data.get('description', '')
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    # Start with base query
    query = "SELECT p.id, p.path, p.description, p.type FROM photos p"
    params = []
    constraints = []
    
    # Filter by People (AND logic: must contain ALL selected people)
    if person_ids:
        # We need to ensure the photo has ALL these people.
        # Group by photo_id and count matches?
        # Or multiple exists?
        # "Show me photos with Person 1 AND Person 2" -> Intersection.
        
        # Method: Group by photo, count how many of the target person_ids are associated.
        # If count == len(person_ids), it's a match.
        
        placeholders = ','.join('?' for _ in person_ids)
        subquery = f"""
            SELECT photo_id FROM photo_people 
            WHERE person_id IN ({placeholders})
            GROUP BY photo_id
            HAVING COUNT(DISTINCT person_id) = ?
        """
        # Append params for IN clause AND the count
        query += f" JOIN ({subquery}) matches ON p.id = matches.photo_id"
        params.extend(person_ids)
        params.append(len(person_ids))

    if description_query:
        # Semantic Expansion
        search_terms = {description_query.lower()}
        try:
            import nltk
            from nltk.corpus import wordnet
            # nltk.download('wordnet') # Should be pre-downloaded or checked
            
            # Get synonyms
            for syn in wordnet.synsets(description_query):
                for l in syn.lemmas():
                    name = l.name().replace('_', ' ').lower()
                    search_terms.add(name)
        except Exception as e:
            print(f"Expansion error: {e}")
        
        print(f"Searching for: {search_terms}")
        
        # Build OR clause for all terms
        # constraints.append( "(" + " OR ".join(["p.description LIKE ?"] * len(search_terms)) + ")" )
        # params.extend([f"%{term}%" for term in search_terms])
        
        term_conditions = []
        for term in search_terms:
            term_conditions.append("p.description LIKE ?")
            params.append(f"%{term}%")
        
        if term_conditions:
            constraints.append(f"({' OR '.join(term_conditions)})")
    
    if constraints:
         # Note: if we used JOIN above, we don't have a WHERE clause yet unless we add it
         query += " WHERE " + " AND ".join(constraints)
    
    c.execute(query, params)
    rows = c.fetchall()
    
    results = []
    for r in rows:
        # We need to map back to web-accessible URLs
        # Path in DB is absolute: /home/nitin/data/<userid>/<device>/files/...
        abs_path = r['path']
        # Extract userid, device, relpath
        # Assumption: path contains '/data/<userid>/<device>/files/...'
        # We can try to split.
        parts = abs_path.split('/')
        try:
            photo_data = build_photo_response(r['path'], r['id'], r['type'], userid=userid)
            if photo_data:
                 # Override or add search-specific metadata
                 photo_data['description'] = r['description']
                 results.append(photo_data)
        except Exception as e:
            print(f"Error processing search result {r['id']}: {e}")
            continue

    conn.close()
    return jsonify({'results': results})

@app.route('/api/files/list', methods=['GET'])
def list_files():
    userid = request.args.get('userid')
    path_arg = request.args.get('path', '')
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    user_dir = get_user_dir(userid)
    
    # Security: Ensure path is within user_dir
    target_path = os.path.normpath(os.path.join(user_dir, path_arg))
    if not target_path.startswith(user_dir):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.exists(target_path):
        return jsonify({'error': 'Path not found'}), 404
        
    items = []
    try:
        with os.scandir(target_path) as it:
            for entry in it:
                item_type = 'dir' if entry.is_dir() else 'file'
                size = 0
                modified = 0
                try:
                    stat = entry.stat()
                    if entry.is_file():
                        size = stat.st_size
                    modified = stat.st_mtime * 1000 # Convert to ms for JS
                except OSError:
                    # Handle broken symlinks or permission errors
                    pass
                    
                items.append({
                    'name': entry.name,
                    'type': item_type,
                    'size': size,
                    'modified': modified
                })
        # Sort: Directories first, then files
        items.sort(key=lambda x: (x['type'] != 'dir', x['name'].lower()))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'items': items, 'path': path_arg})

@app.route('/api/files/rename', methods=['POST'])
def rename_file():
    data = request.json
    userid = data.get('userid')
    path_arg = data.get('path')
    new_name = data.get('new_name')
    
    if not userid or not path_arg or not new_name:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    old_path = os.path.normpath(os.path.join(user_dir, path_arg))
    
    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_name)
    
    if not old_path.startswith(user_dir) or not new_path.startswith(user_dir):
        return jsonify({'error': 'Access denied'}), 403
        
    if not os.path.exists(old_path):
        return jsonify({'error': 'File not found'}), 404
        
    if os.path.exists(new_path):
        return jsonify({'error': 'Destination already exists'}), 409
        
    try:
        os.rename(old_path, new_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/batch-delete', methods=['POST'])
def delete_files_batch():
    data = request.json
    userid = data.get('userid')
    paths = data.get('paths', []) # List of relative paths (relative to user_dir)
    
    if not userid or not paths:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    
    deleted = []
    errors = []
    
    for path_arg in paths:
        target_path = os.path.normpath(os.path.join(user_dir, path_arg))
        
        # Security check
        if not target_path.startswith(user_dir):
            errors.append(f"{path_arg}: Access denied")
            continue
            
        if not os.path.exists(target_path):
            errors.append(f"{path_arg}: Not found")
            continue
            
        try:
            if os.path.isdir(target_path):
                import shutil
                shutil.rmtree(target_path)
            else:
                os.remove(target_path)
            deleted.append(path_arg)
        except Exception as e:
            errors.append(f"{path_arg}: {str(e)}")

    return jsonify({'success': True, 'deleted': deleted, 'errors': errors})

@app.route('/api/files/delete', methods=['POST'])
def delete_file():
    data = request.json
    userid = data.get('userid')
    path_arg = data.get('path')
    
    if not userid or not path_arg:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    target_path = os.path.normpath(os.path.join(user_dir, path_arg))
    
    if not target_path.startswith(user_dir):
        return jsonify({'error': 'Access denied'}), 403
        
    if not os.path.exists(target_path):
        return jsonify({'error': 'File not found'}), 404
        
    try:
        if os.path.isdir(target_path):
            import shutil
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- Admin API ---

import reset_db

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        userid = request.cookies.get('userid')
        if not userid:
             return jsonify({'error': 'Unauthorized'}), 401
        
        # Check if user is admin
        conn = sqlite3.connect(USER_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT is_admin FROM users WHERE email = ?", (userid,))
        row = c.fetchone()
        conn.close()
        
        if not row or not row[0]:
            return jsonify({'error': 'Admin privileges required'}), 403
            
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@admin_required
def admin_dashboard():
    return render_template('admin.html')

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_list_users():
    conn = sqlite3.connect(USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, email, is_admin, status, unique_id, created_at FROM users")
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'users': users})

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def add_user():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
        
    conn = sqlite3.connect(USER_DB_PATH)
    c = conn.cursor()
    
    # Check if exists
    c.execute("SELECT id FROM users WHERE email = ?", (email,))
    if c.fetchone():
        conn.close()
        return jsonify({'error': 'User already exists'}), 409
        
    try:
        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
        unique_id = hashlib.sha256(f"{time.time()}{secrets.token_hex(16)}".encode()).hexdigest()
        
        c.execute("""
            INSERT INTO users (email, password_hash, salt, unique_id, is_admin, status, force_password_change)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (email, password_hash, salt, unique_id, False, 'active'))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/users/status', methods=['POST'])
@admin_required
def update_user_status():
    data = request.json
    userid = data.get('userid') # The email
    status = data.get('status') # 'active' or 'revoked' (or anything else)
    
    if not userid or not status:
        return jsonify({'error': 'User ID and status required'}), 400

    conn = sqlite3.connect(USER_DB_PATH)
    c = conn.cursor()
    try:
        c.execute("UPDATE users SET status = ? WHERE email = ?", (status, userid))
        conn.commit()
        if c.rowcount == 0:
            return jsonify({'error': 'User not found'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/users/delete', methods=['POST'])
@admin_required
def delete_user_admin():
    data = request.json
    userid = data.get('userid') # email
    destroy_data = data.get('destroy_data', False)
    
    if not userid:
        return jsonify({'error': 'User ID required'}), 400
        
    # Prevent deleting yourself
    current_admin = request.cookies.get('userid')
    if userid == current_admin:
        return jsonify({'error': 'Cannot delete yourself'}), 400

    conn = sqlite3.connect(USER_DB_PATH)
    c = conn.cursor()
    
    try:
        # 1. Remove from users table
        c.execute("DELETE FROM users WHERE email = ?", (userid,))
        if c.rowcount == 0:
            return jsonify({'error': 'User not found'}), 404
        conn.commit()
        
        # 2. Handle data
        if destroy_data:
            # Full destruction: user directory
            user_dir = get_user_dir(userid)
            if os.path.exists(user_dir):
               import shutil
               shutil.rmtree(user_dir)
        else:
            # Metadata only: clean DB, thumbnails, shared
            # Use reset_db logic
            # We must be careful because reset_db asks for input() confirmation by default!
            # We need to bypass input in reset_db or re-implement logic.
            # reset_db.reset_database(userid) checks input.
            # Let's re-implement the safe logic here to avoid stuck process or modifying reset_db.py
            
            user_path = get_user_dir(userid)
            db_path = os.path.join(user_path, 'photovault.db')
            thumbs_dir = os.path.join(user_path, 'thumbnails')
            shared_dir = os.path.join(user_path, 'shared')
            
            if os.path.exists(db_path):
                os.remove(db_path)
            if os.path.exists(thumbs_dir):
                import shutil
                shutil.rmtree(thumbs_dir)
                os.makedirs(thumbs_dir, exist_ok=True)
            if os.path.exists(shared_dir):
                import shutil
                shutil.rmtree(shared_dir)
                
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

def delete_file():
    data = request.json
    userid = data.get('userid')
    path_arg = data.get('path')
    
    if not userid or not path_arg:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    target_path = os.path.normpath(os.path.join(user_dir, path_arg))
    
    if not target_path.startswith(user_dir):
        return jsonify({'error': 'Access denied'}), 403
        
    if not os.path.exists(target_path):
        return jsonify({'error': 'path not found'}), 404
        
    try:
        if os.path.isdir(target_path):
            import shutil
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/download', methods=['POST'])
def download_files():
    data = request.json
    userid = data.get('userid')
    paths = data.get('paths', [])
    
    if not userid or not paths:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    
    try:
        # Create in-memory zip
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path_arg in paths:
                # Security check
                full_path = os.path.normpath(os.path.join(user_dir, path_arg))
                if not full_path.startswith(user_dir):
                    continue
                    
                if not os.path.exists(full_path):
                    continue
                    
                # Store in zip with relative path logic
                if os.path.isdir(full_path):
                    for root, dirs, files in os.walk(full_path):
                        for file in files:
                            abs_file = os.path.join(root, file)
                            rel_to_user = os.path.relpath(abs_file, user_dir)
                            zf.write(abs_file, rel_to_user)
                else:
                    rel_to_user = os.path.relpath(full_path, user_dir)
                    zf.write(full_path, rel_to_user)
                    
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='download.zip'
        )
    except Exception as e:
        print(f"Zip error: {e}")
        return jsonify({'error': str(e)}), 500

# --- Timeline API ---

@app.route('/api/timeline', methods=['GET'])
def get_timeline():
    """Get photos grouped by date for timeline view"""
    userid = request.args.get('userid')
    year = request.args.get('year')
    month = request.args.get('month')
    filter_type = request.args.get('type') # 'photo' | 'screenshot' | 'video'
    search_query = request.args.get('search', '').strip()
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    try:
        # Build query with optional year/month filters
        query = """
            SELECT 
                DATE(COALESCE(date_taken, timestamp)) as photo_date,
                COUNT(*) as count
            FROM photos 
            WHERE path LIKE ?
        """
        params = [f"%{userid}%"]
        
        if year:
            query += " AND strftime('%Y', COALESCE(date_taken, timestamp)) = ?"
            params.append(year)
        if month:
            query += " AND strftime('%m', COALESCE(date_taken, timestamp)) = ?"
            params.append(month.zfill(2))
            
        if filter_type == 'screenshot':
            query += " AND type = 'screenshot'"
        elif filter_type == 'photo':
             # specific photo requests (exclude screenshots and videos if they had a type, but currently video is determined by ext)
             # For now, let's say photo means NOT screenshot. 
             # But wait, video is separate in frontend? 
             # If frontend asks for 'photo', it implies the main photo tab.
             # We should exclude screenshots.
             query += " AND (type IS NULL OR type != 'screenshot')"
        
        timeline_groups = []

        # Fetch distinct dates from photos (excluding NULLs)
        c.execute(f"""
            SELECT DISTINCT DATE(date_taken) as day
            FROM photos
            WHERE path LIKE ? AND date_taken IS NOT NULL
            {("AND type = 'screenshot'" if filter_type == 'screenshot' else "AND type = 'video'" if filter_type == 'video' else "AND (type IS NULL OR type != 'screenshot' AND type != 'video')" if filter_type == 'photo' else "")}
            {"AND description LIKE ?" if search_query else ""}
            ORDER BY day DESC
        """, (f"%{userid}%",) + ((f"%{search_query}%",) if search_query else ()))
        
        dates = [row['day'] for row in c.fetchall()]
        
        # Fetch photos for each date
        for photo_date in dates:
            current_group = {
                'date': photo_date,
                'photos': [],
                'count': 0
            }
            
            date_query = """
                SELECT id, path, type FROM photos 
                WHERE DATE(date_taken) = ? AND path LIKE ?
            """
            if filter_type == 'screenshot':
                date_query += " AND type = 'screenshot'"
            elif filter_type == 'video':
                date_query += " AND type = 'video'"
            elif filter_type == 'photo':
                date_query += " AND (type IS NULL OR type != 'screenshot' AND type != 'video')"

            date_query_params = [photo_date, f"%{userid}%"]
            if search_query:
                date_query += " AND description LIKE ?"
                date_query_params.append(f"%{search_query}%")
                
            date_query += " ORDER BY date_taken DESC"
            
            c.execute(date_query, date_query_params)
            date_photos = c.fetchall()
            
            for photo in date_photos:
                try:
                    photo_data = build_photo_response(photo['path'], photo['id'], photo['type'], userid=userid)
                    if photo_data:
                        current_group['photos'].append(photo_data)
                        current_group['count'] += 1
                except Exception:
                    continue
            
            if current_group['count'] > 0:
                timeline_groups.append(current_group)

        # Fetch photos with Unknown Date (date_taken IS NULL)
        unknown_query = """
            SELECT id, path, type FROM photos 
            WHERE date_taken IS NULL AND path LIKE ?
        """
        if filter_type == 'screenshot':
            unknown_query += " AND type = 'screenshot'"
        elif filter_type == 'video':
            unknown_query += " AND type = 'video'"
        elif filter_type == 'photo':
            unknown_query += " AND (type IS NULL OR type != 'screenshot' AND type != 'video')"

        unknown_query_params = [f"%{userid}%"]
        if search_query:
            unknown_query += " AND description LIKE ?"
            unknown_query_params.append(f"%{search_query}%")
            
        # Order unknown by timestamp (upload time) or just ID
        unknown_query += " ORDER BY timestamp DESC"
        
        c.execute(unknown_query, unknown_query_params)
        unknown_photos = c.fetchall()
        
        if unknown_photos:
            unknown_group = {
                'date': 'Unknown',
                'photos': [],
                'count': 0
            }
            for photo in unknown_photos:
                try:
                    photo_data = build_photo_response(photo['path'], photo['id'], photo['type'], userid=userid)
                    if photo_data:
                        unknown_group['photos'].append(photo_data)
                        unknown_group['count'] += 1
                except Exception:
                    continue
            
            if unknown_group['count'] > 0:
                timeline_groups.append(unknown_group)
        
        conn.close()
        return jsonify({'groups': timeline_groups})
        
    except Exception as e:
        print(f"Timeline error: {e}")
        conn.close()
        return jsonify({'error': str(e)}), 500

# --- Albums API ---

@app.route('/api/albums', methods=['GET'])
def list_albums():
    """Get all albums"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    c.execute("""
        SELECT a.id, a.name, a.description, a.album_type, a.created_at,
               a.cover_photo_id, a.owner_email, a.source_album_id,
               COALESCE(p.path, fp.path) as cover_path,
               COALESCE(p.type, fp.type) as cover_type,
               COALESCE(a.cover_photo_id, fp.id) as effective_cover_id,
               COUNT(ap.photo_id) as photo_count
        FROM albums a
        LEFT JOIN album_photos ap ON a.id = ap.album_id
        LEFT JOIN photos p ON a.cover_photo_id = p.id
        LEFT JOIN album_photos fap ON a.id = fap.album_id AND fap.rowid = (
            SELECT MIN(rowid) FROM album_photos WHERE album_id = a.id
        )
        LEFT JOIN photos fp ON fap.photo_id = fp.id AND a.cover_photo_id IS NULL
        GROUP BY a.id
        ORDER BY a.created_at DESC
    """)
    
    rows = c.fetchall()
    albums = []
    
    for r in rows:
        album = {
            'id': r['id'],
            'name': r['name'],
            'description': r['description'],
            'album_type': r['album_type'],
            'created_at': r['created_at'],
            'photo_count': r['photo_count'],
            'source_album_id': r['source_album_id'],
            'owner_email': r['owner_email'],
            'cover_url': None
        }
        
        # Generate cover thumbnail URL if available
        cover_id = r['effective_cover_id']
        cover_path = r['cover_path']
        cover_type = r['cover_type']
        if cover_id and cover_path:
            try:
                cover_photo_data = build_photo_response(cover_path, cover_id, cover_type, userid=userid)
                if cover_photo_data:
                    album['cover_url'] = cover_photo_data['thumbnail_url']
            except Exception:
                pass
        
        albums.append(album)

        # Check if album contains any received/shared photos
        c.execute("""
            SELECT 1 FROM album_photos ap
            JOIN shared_photos sp ON sp.recipient_photo_id = ap.photo_id
            WHERE ap.album_id = ? AND sp.recipient_email = ?
            LIMIT 1
        """, (r['id'], userid))
        album['has_shared_photos'] = c.fetchone() is not None
    
    conn.close()
    return jsonify({'albums': albums})

@app.route('/api/albums/create', methods=['POST'])
def create_album():
    """Create a new album"""
    data = request.json
    name = data.get('name')
    description = data.get('description', '')
    album_type = data.get('album_type', 'manual')
    
    if not name:
        return jsonify({'error': 'Album name required'}), 400
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    c.execute(
        "INSERT INTO albums (name, description, album_type) VALUES (?, ?, ?)",
        (name, description, album_type)
    )
    conn.commit()
    album_id = c.lastrowid
    conn.close()
    
    return jsonify({'success': True, 'album_id': album_id})

@app.route('/api/albums/<int:album_id>', methods=['DELETE'])
def delete_album(album_id):
    """Delete an album. If the album was shared, cascade unshare to recipients."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    try:
        # Get album info
        c.execute("SELECT album_type, owner_email, source_album_id FROM albums WHERE id = ?", (album_id,))
        album = c.fetchone()
        if not album:
            conn.close()
            return jsonify({'error': 'Album not found'}), 404

        # Get all photo IDs in this album
        c.execute("SELECT photo_id FROM album_photos WHERE album_id = ?", (album_id,))
        album_photo_ids = [row['photo_id'] for row in c.fetchall()]

        if album['album_type'] != 'shared':
            # Owner is deleting their own album
            # Find all recipients who have a shared copy of this album
            # Since we don't track shared albums explicitly in owner DB, we scan all users
            # to see if they have a copy. This ensures even empty albums are cleaned up.
            recipients = set()
            try:
                u_conn = sqlite3.connect(USER_DB_PATH)
                u_c = u_conn.cursor()
                u_c.execute("SELECT email FROM users WHERE email != ? AND status = 'active'", (userid,))
                recipients = {r[0] for r in u_c.fetchall()}
                u_conn.close()
            except Exception as e:
                print(f"Error fetching user list for cleanup: {e}")

            # For each recipient, find and clean up their shared album copy
            for recip_email in recipients:
                try:
                    recip_conn = database.get_db_connection(recip_email)
                    rc = recip_conn.cursor()
                    
                    # Find albums shared from this source
                    rc.execute(
                        "SELECT id FROM albums WHERE source_album_id = ? AND owner_email = ?",
                        (album_id, userid)
                    )
                    shared_albums = rc.fetchall()
                    
                    for sa in shared_albums:
                        shared_album_id = sa['id']
                        # Get photos in the shared album
                        rc.execute("SELECT photo_id FROM album_photos WHERE album_id = ?", (shared_album_id,))
                        shared_photo_ids = [r['photo_id'] for r in rc.fetchall()]
                        
                        for spid in shared_photo_ids:
                            # Get photo path to remove symlink
                            rc.execute("SELECT path FROM photos WHERE id = ?", (spid,))
                            photo_row = rc.fetchone()
                            if photo_row:
                                symlink_path = photo_row['path']
                                if os.path.islink(symlink_path):
                                    os.unlink(symlink_path)
                                
                                # Remove thumbnail symlink
                                recip_thumb_dir = get_thumbnail_dir(recip_email)
                                unique_name = os.path.basename(symlink_path)
                                recip_thumb_name = f"shared__{unique_name}" if unique_name.lower().endswith('.jpg') else f"shared__{unique_name}.jpg"
                                recip_thumb_path = os.path.join(recip_thumb_dir, recip_thumb_name)
                                if os.path.islink(recip_thumb_path):
                                    os.unlink(recip_thumb_path)
                            
                            # Clean up DB records
                            rc.execute("DELETE FROM photo_people WHERE photo_id = ?", (spid,))
                            rc.execute("DELETE FROM photos WHERE id = ?", (spid,))
                            rc.execute("DELETE FROM shared_photos WHERE recipient_photo_id = ?", (spid,))
                        
                        # Remove the shared album
                        rc.execute("DELETE FROM album_photos WHERE album_id = ?", (shared_album_id,))
                        rc.execute("DELETE FROM albums WHERE id = ?", (shared_album_id,))
                    
                    recip_conn.commit()
                    recip_conn.close()
                except Exception as e:
                    print(f"Error cleaning up shared album for {recip_email}: {e}")

            # Also clean up owner's shared_photos records for these photos
            for pid in album_photo_ids:
                c.execute("DELETE FROM shared_photos WHERE original_photo_id = ?", (pid,))

        # Delete the album itself
        c.execute("DELETE FROM album_photos WHERE album_id = ?", (album_id,))
        c.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/albums/<int:album_id>/photos', methods=['GET'])
def get_album_photos(album_id):
    """Get photos in a specific album"""
    userid = get_current_userid() # Use current user's ID
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    c.execute("""
        SELECT p.id, p.path, p.description, p.type
        FROM photos p
        JOIN album_photos ap ON p.id = ap.photo_id
        WHERE ap.album_id = ?
        ORDER BY ap.added_at DESC
    """, (album_id,))
    
    rows = c.fetchall()
    photos = []
    
    for r in rows:
        try:
            photo_data = build_photo_response(r['path'], r['id'], r['type'], userid=userid)
            if photo_data:
                photo_data['description'] = r['description']
                photos.append(photo_data)
        except Exception:
            continue
    
    conn.close()
    return jsonify({'photos': photos})

@app.route('/api/albums/<int:album_id>/add-photos', methods=['POST'])
def add_photos_to_album(album_id):
    """Add photos to an album"""
    data = request.json
    photo_ids = data.get('photo_ids', [])
    
    if not photo_ids:
        return jsonify({'error': 'No photos specified'}), 400
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    added = 0
    for photo_id in photo_ids:
        # Prevent adding received/shared photos to albums
        c.execute("SELECT id FROM shared_photos WHERE recipient_photo_id = ? AND recipient_email = ?", (photo_id, userid))
        if c.fetchone():
            continue

        try:
            c.execute(
                "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)",
                (album_id, photo_id)
            )
            added += 1
        except sqlite3.IntegrityError:
            pass  # Already in album
    
    # Update cover photo if album doesn't have one
    c.execute("SELECT cover_photo_id FROM albums WHERE id = ?", (album_id,))
    row = c.fetchone()
    if row and not row['cover_photo_id'] and photo_ids:
        c.execute("UPDATE albums SET cover_photo_id = ? WHERE id = ?", (photo_ids[0], album_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'added': added})

@app.route('/api/albums/<int:album_id>/remove-photos', methods=['POST'])
def remove_photos_from_album(album_id):
    """Remove photos from an album"""
    data = request.json
    photo_ids = data.get('photo_ids', [])
    
    if not photo_ids:
        return jsonify({'error': 'No photos specified'}), 400
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    placeholders = ','.join('?' for _ in photo_ids)
    c.execute(
        f"DELETE FROM album_photos WHERE album_id = ? AND photo_id IN ({placeholders})",
        [album_id] + photo_ids
    )
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/albums/auto-generate', methods=['POST'])
def auto_generate_albums():
    """Auto-generate albums based on date clustering"""
    userid = request.json.get('userid')
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    try:
        # Get photos grouped by date
        c.execute("""
            SELECT 
                DATE(COALESCE(date_taken, timestamp)) as photo_date,
                GROUP_CONCAT(id) as photo_ids,
                COUNT(*) as count
            FROM photos
            WHERE path LIKE ?
            GROUP BY photo_date
            HAVING count >= 5
            ORDER BY photo_date DESC
        """, (f"%{userid}%",))
        
        rows = c.fetchall()
        created = 0
        
        for row in rows:
            photo_date = row['photo_date']
            photo_ids = [int(x) for x in row['photo_ids'].split(',')]
            count = row['count']
            
            # Create album name from date
            from datetime import datetime
            try:
                date_obj = datetime.strptime(photo_date, '%Y-%m-%d')
                album_name = date_obj.strftime('%B %d, %Y')
            except:
                album_name = photo_date
            
            # Check if album already exists for this date
            c.execute(
                "SELECT id FROM albums WHERE name = ? AND album_type = 'auto_date'",
                (album_name,)
            )
            existing = c.fetchone()
            
            if not existing:
                c.execute(
                    "INSERT INTO albums (name, description, album_type, cover_photo_id) VALUES (?, ?, ?, ?)",
                    (album_name, f"{count} photos from {album_name}", 'auto_date', photo_ids[0])
                )
                album_id = c.lastrowid
                
                # Add photos to album
                for photo_id in photo_ids:
                    try:
                        c.execute(
                            "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)",
                            (album_id, photo_id)
                        )
                    except sqlite3.IntegrityError:
                        pass
                
                created += 1
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'created': created})
        
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

# --- Discover/Memories API ---

@app.route('/api/discover/memories', methods=['GET'])
def get_memories():
    """Get curated memories (year ago, this day in history, etc.)"""
    userid = request.args.get('userid')
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    memories = []
    
    try:
        # 1. A Year Ago Today
        # Get a random selection of photos from exactly one year ago today
        c.execute("""
            WITH year_ago_ids AS (
                SELECT id FROM photos
                WHERE path LIKE ?
                AND DATE(COALESCE(date_taken, timestamp)) = DATE('now', '-1 year')
            )
            SELECT id, path, type FROM photos
            WHERE path LIKE ?
            AND DATE(COALESCE(date_taken, timestamp)) = DATE('now', '-1 year')
            AND id NOT IN (SELECT id FROM year_ago_ids)
            ORDER BY RANDOM()
            LIMIT 10
        """, (f"%{userid}%", f"%{userid}%"))
        
        year_ago_photos = []
        for row in c.fetchall():
            photo_data = build_photo_response(row['path'], row['id'], row['type'], userid=userid)
            if photo_data:
                year_ago_photos.append(photo_data)
        
        if year_ago_photos:
            memories.append({
                'type': 'year_ago',
                'title': 'A Year Ago Today',
                'description': f'{len(year_ago_photos)} photos from this day last year',
                'photos': year_ago_photos
            })
        
        # 2. This Day in History (2-5 years ago)
        for years_back in [2, 3, 4, 5]:
            c.execute("""
                SELECT id, path, type FROM photos
                WHERE path LIKE ?
                AND strftime('%m-%d', COALESCE(date_taken, timestamp)) = strftime('%m-%d', 'now')
                AND strftime('%Y', COALESCE(date_taken, timestamp)) = strftime('%Y', 'now', ? || ' years')
                ORDER BY COALESCE(date_taken, timestamp) DESC
                LIMIT 20
            """, (f"%{userid}%", f'-{years_back}'))
            
            history_photos = []
            for row in c.fetchall():
                photo_data = build_photo_response(row['path'], row['id'], row['type'], userid=userid)
                if photo_data:
                    history_photos.append(photo_data)
            
            if history_photos:
                memories.append({
                    'type': 'this_day_history',
                    'title': f'{years_back} Years Ago Today',
                    'description': f'{len(history_photos)} photos from this day {years_back} years ago',
                    'photos': history_photos
                })
        
        # 3. Recent Highlights (last 30 days, photos with people)
        c.execute("""
            SELECT DISTINCT p.id, p.path, p.type FROM photos p
            JOIN photo_people pp ON p.id = pp.photo_id
            WHERE p.path LIKE ?
            AND DATE(COALESCE(p.date_taken, p.timestamp)) >= DATE('now', '-30 days')
            ORDER BY COALESCE(p.date_taken, p.timestamp) DESC
            LIMIT 20
        """, (f"%{userid}%",))
        
        recent_photos = []
        for row in c.fetchall():
            photo_data = build_photo_response(row['path'], row['id'], row['type'], userid=userid)
            if photo_data:
                recent_photos.append(photo_data)
        
        if recent_photos:
            memories.append({
                'type': 'recent_highlights',
                'title': 'Recent Highlights',
                'description': f'{len(recent_photos)} photos with people from the last month',
                'photos': recent_photos
            })
        
        conn.close()
        return jsonify({'memories': memories})
        
    except Exception as e:
        print(f"Memories error: {e}")
        conn.close()
        return jsonify({'error': str(e)}), 500

def build_photo_response(abs_path, photo_id, media_type=None, userid=None):
    """Helper to build photo response with thumbnail and image URLs"""
    try:
        rel_from_data = os.path.relpath(abs_path, DATA_DIR)
        path_parts = rel_from_data.split(os.path.sep)
        file_userid = path_parts[0]
        device = path_parts[1]
        
        files_dir_idx = abs_path.find('/files/')
        if files_dir_idx != -1:
            rel_path = abs_path[files_dir_idx+7:]
            safe_base = rel_path.replace(os.path.sep, '_')
            safe_thumb = f"{device}__{safe_base}" if safe_base.lower().endswith('.jpg') else f"{device}__{safe_base}.jpg"
            
            if not media_type:
                ext = os.path.splitext(abs_path)[1].lower()
                media_type = 'video' if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts') else 'image'

            result = {
                'id': photo_id,
                'thumbnail_url': f"/resource/thumbnail/{file_userid}/{safe_thumb}",
                'image_url': f"/resource/image/{file_userid}/{device}/{rel_path}",
                'type': media_type,
                'is_video': media_type == 'video',
                'shared_with': [],
                'is_received': False
            }
            
            # Add share info if userid is provided
            if userid:
                try:
                    conn = database.get_db_connection(userid)
                    c = conn.cursor()
                    # Check if this photo was shared by us
                    c.execute("SELECT recipient_email FROM shared_photos WHERE original_photo_id = ?", (photo_id,))
                    shared_rows = c.fetchall()
                    result['shared_with'] = [r['recipient_email'] for r in shared_rows]
                    
                    # Check if this photo was received (shared TO us)
                    c.execute("SELECT owner_email FROM shared_photos WHERE recipient_photo_id = ?", (photo_id,))
                    received = c.fetchone()
                    if received:
                        result['is_received'] = True
                        result['shared_by'] = received['owner_email']
                    conn.close()
                except Exception:
                    pass
            
            return result
    except Exception:
        pass
    return None

# --- Sharing API ---

@app.route('/api/users/list', methods=['GET'])
def list_users():
    """List all users + active guests of current user for the share picker"""
    current_user = get_current_userid()
    if not current_user:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Regular users
        conn = sqlite3.connect(USER_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT email, status FROM users WHERE email != ? AND status = 'active'", (current_user,))
        users = [{'email': r['email'], 'type': 'user'} for r in c.fetchall()]
        conn.close()
        
        # Active guests of current user (only if current user is not a guest)
        if not is_guest_user(current_user):
            try:
                gconn = get_guest_db()
                gc = gconn.cursor()
                gc.execute("""SELECT g.email FROM guests g
                              JOIN guest_hosts gh ON g.id = gh.guest_id
                              WHERE gh.host_email = ? AND gh.status = 'active'
                              AND gh.access_till >= datetime('now')""", (current_user,))
                for r in gc.fetchall():
                    users.append({'email': r['email'], 'type': 'guest'})
                gconn.close()
            except Exception as e:
                print(f"Error loading guests for share picker: {e}")
        
        return jsonify({'users': users})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/received-photos', methods=['GET'])
def get_received_photos():
    """Get photos shared with the current user, grouped by sender"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    try:
        # Get all received photos from shared_photos table
        c.execute("""
            SELECT sp.owner_email, sp.recipient_photo_id, p.path, p.type
            FROM shared_photos sp
            JOIN photos p ON sp.recipient_photo_id = p.id
            ORDER BY sp.owner_email, sp.id DESC
        """)
        rows = c.fetchall()
        
        grouped = {}
        for r in rows:
            owner = r['owner_email']
            if owner not in grouped:
                grouped[owner] = []
            photo_data = build_photo_response(r['path'], r['recipient_photo_id'], r['type'], userid=userid)
            if photo_data:
                grouped[owner].append(photo_data)
        
        conn.close()
        return jsonify({'shared_photos': grouped})
    except Exception as e:
        conn.close()
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/share', methods=['POST'])
def share_photo():
    """Share a photo with one or more users"""
    data = request.json
    photo_id = data.get('photo_id')
    recipients = data.get('recipients', [])  # list of email strings
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not photo_id or not recipients:
        return jsonify({'error': 'photo_id and recipients required'}), 400
    
    owner_conn = database.get_db_connection(userid)
    owner_c = owner_conn.cursor()
    
    try:
        # 1. Get the original photo
        owner_c.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
        photo = owner_c.fetchone()
        if not photo:
            return jsonify({'error': 'Photo not found'}), 404
        
        # 2. Check this is not a received photo (re-sharing prevention)
        owner_c.execute("SELECT id FROM shared_photos WHERE recipient_photo_id = ? AND recipient_email = ?", (photo_id, userid))
        if owner_c.fetchone():
            return jsonify({'error': 'Cannot re-share a received photo. Only original owner can share.'}), 403
        
        original_path = photo['path']
        results = []
        
        for recipient_email in recipients:
            try:
                # Check not already shared
                owner_c.execute("SELECT id FROM shared_photos WHERE original_photo_id = ? AND recipient_email = ?",
                               (photo_id, recipient_email))
                if owner_c.fetchone():
                    results.append({'email': recipient_email, 'status': 'already_shared'})
                    continue
                
                # 3. Create symlinks
                recipient_dir = os.path.join(DATA_DIR, recipient_email)
                shared_files_dir = os.path.join(recipient_dir, 'shared', 'files')
                os.makedirs(shared_files_dir, exist_ok=True)
                
                filename = os.path.basename(original_path)
                # Make unique filename to avoid collisions
                unique_name = f"{userid.split('@')[0]}_{filename}"
                symlink_path = os.path.join(shared_files_dir, unique_name)
                
                # Create image symlink (resolve to real path if original is also a symlink)
                real_original = os.path.realpath(original_path)
                if not os.path.exists(symlink_path):
                    os.symlink(real_original, symlink_path)
                
                # 4. Create thumbnail symlink
                owner_thumb_dir = get_thumbnail_dir(userid)
                recipient_thumb_dir = get_thumbnail_dir(recipient_email)
                os.makedirs(recipient_thumb_dir, exist_ok=True)
                
                # Find the original thumbnail
                rel_from_data = os.path.relpath(original_path, DATA_DIR)
                path_parts = rel_from_data.split(os.path.sep)
                device = path_parts[1]
                files_idx = original_path.find('/files/')
                if files_idx != -1:
                    rel_file = original_path[files_idx+7:]
                    safe_base = rel_file.replace(os.path.sep, '_')
                    orig_thumb_name = f"{device}__{safe_base}" if safe_base.lower().endswith('.jpg') else f"{device}__{safe_base}.jpg"
                    orig_thumb_path = os.path.join(owner_thumb_dir, orig_thumb_name)
                    
                    # Recipient thumbnail: shared__uniquename.jpg
                    recip_thumb_name = f"shared__{unique_name}" if unique_name.lower().endswith('.jpg') else f"shared__{unique_name}.jpg"
                    recip_thumb_path = os.path.join(recipient_thumb_dir, recip_thumb_name)
                    
                    if os.path.exists(orig_thumb_path) and not os.path.exists(recip_thumb_path):
                        os.symlink(os.path.realpath(orig_thumb_path), recip_thumb_path)
                
                # 5. Insert photo row in recipient's DB
                database.init_db(recipient_email)
                recip_conn = database.get_db_connection(recipient_email)
                recip_c = recip_conn.cursor()
                
                recip_c.execute("""
                    INSERT OR IGNORE INTO photos 
                    (path, description, date_taken, location_lat, location_lon, 
                     processed_for_thumbnails, processed_for_faces, processed_for_description, processed_for_exif, type)
                    VALUES (?, ?, ?, ?, ?, 1, 1, 1, 1, ?)
                """, (symlink_path, photo['description'], photo['date_taken'],
                      photo['location_lat'], photo['location_lon'], photo['type']))
                recip_conn.commit()
                
                recipient_photo_id = recip_c.lastrowid
                
                # 6. Migrate face connections
                owner_c.execute("SELECT person_id FROM photo_people WHERE photo_id = ?", (photo_id,))
                face_mappings = owner_c.fetchall()
                
                for mapping in face_mappings:
                    person_id = mapping['person_id']
                    # Get person's embedding from owner's DB
                    owner_c.execute("SELECT name, thumbnail_path, embedding_blob FROM people WHERE id = ?", (person_id,))
                    person = owner_c.fetchone()
                    if not person:
                        continue
                    
                    # Check if recipient already has this person (by embedding match)
                    recip_person_id = None
                    if person['embedding_blob']:
                        import numpy as np
                        owner_embedding = database.convert_array(person['embedding_blob'])
                        recip_c.execute("SELECT id, embedding_blob FROM people")
                        for rp in recip_c.fetchall():
                            if rp['embedding_blob']:
                                recip_embedding = database.convert_array(rp['embedding_blob'])
                                distance = np.linalg.norm(owner_embedding - recip_embedding)
                                if distance < 0.6:  # Same tolerance as face_recognition
                                    recip_person_id = rp['id']
                                    break
                    
                    if recip_person_id is None:
                        # Fix: Symlink face thumbnail and update path
                        new_thumb_path = person['thumbnail_path']
                        if person['thumbnail_path'] and os.path.exists(person['thumbnail_path']):
                            face_thumb_name = os.path.basename(person['thumbnail_path'])
                            recip_face_thumb_path = os.path.join(recipient_thumb_dir, face_thumb_name)
                            
                            if not os.path.exists(recip_face_thumb_path):
                                try:
                                    os.symlink(os.path.realpath(person['thumbnail_path']), recip_face_thumb_path)
                                except Exception as e:
                                    print(f"Error symlinking face thumb: {e}")
                            
                            new_thumb_path = recip_face_thumb_path

                        # Create new person in recipient's DB
                        recip_c.execute("INSERT INTO people (name, thumbnail_path, embedding_blob) VALUES (?, ?, ?)",
                                       (person['name'], new_thumb_path, person['embedding_blob']))
                        recip_person_id = recip_c.lastrowid
                    else:
                        # Existing person: ensure they have a valid thumbnail (optional improvement)
                        new_thumb_path = person['thumbnail_path']
                        if person['thumbnail_path'] and os.path.exists(person['thumbnail_path']):
                            face_thumb_name = os.path.basename(person['thumbnail_path'])
                            recip_face_thumb_path = os.path.join(recipient_thumb_dir, face_thumb_name)
                            
                            if not os.path.exists(recip_face_thumb_path):
                                try:
                                    os.symlink(os.path.realpath(person['thumbnail_path']), recip_face_thumb_path)
                                except Exception as e:
                                    print(f"Error symlinking face thumb: {e}")
                            
                            # Update existing person's thumb if it doesn't have one
                            recip_c.execute("SELECT thumbnail_path FROM people WHERE id = ?", (recip_person_id,))
                            row = recip_c.fetchone()
                            if not row or not row['thumbnail_path'] or not os.path.exists(row['thumbnail_path']):
                                recip_c.execute("UPDATE people SET thumbnail_path = ? WHERE id = ?", (recip_face_thumb_path, recip_person_id))
                    
                    # Map photo to person in recipient's DB
                    try:
                        recip_c.execute("INSERT INTO photo_people (photo_id, person_id) VALUES (?, ?)",
                                       (recipient_photo_id, recip_person_id))
                    except sqlite3.IntegrityError:
                        pass
                
                recip_conn.commit()
                recip_conn.close()
                
                # 7. Record share in owner's DB
                owner_c.execute("""
                    INSERT INTO shared_photos (original_photo_id, owner_email, recipient_email, recipient_photo_id)
                    VALUES (?, ?, ?, ?)
                """, (photo_id, userid, recipient_email, recipient_photo_id))
                
                # Also record in recipient's DB so they know it's received
                recip_conn2 = database.get_db_connection(recipient_email)
                recip_c2 = recip_conn2.cursor()
                recip_c2.execute("""
                    INSERT OR IGNORE INTO shared_photos (original_photo_id, owner_email, recipient_email, recipient_photo_id)
                    VALUES (?, ?, ?, ?)
                """, (photo_id, userid, recipient_email, recipient_photo_id))
                recip_conn2.commit()
                recip_conn2.close()
                
                results.append({'email': recipient_email, 'status': 'shared'})
                
            except Exception as e:
                print(f"Error sharing with {recipient_email}: {e}")
                import traceback
                traceback.print_exc()
                results.append({'email': recipient_email, 'status': 'error', 'error': str(e)})
        
        owner_conn.commit()
        owner_conn.close()
        return jsonify({'success': True, 'results': results})
        
    except Exception as e:
        owner_conn.close()
        print(f"Share error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/unshare', methods=['POST'])
def unshare_photo():
    """Unshare a photo — called by the owner"""
    data = request.json
    photo_id = data.get('photo_id')
    recipient_email = data.get('recipient_email')
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not photo_id or not recipient_email:
        return jsonify({'error': 'photo_id and recipient_email required'}), 400
    
    owner_conn = database.get_db_connection(userid)
    owner_c = owner_conn.cursor()
    
    try:
        # Find the share record
        owner_c.execute("""
            SELECT recipient_photo_id FROM shared_photos 
            WHERE original_photo_id = ? AND recipient_email = ? AND owner_email = ?
        """, (photo_id, recipient_email, userid))
        share = owner_c.fetchone()
        
        if not share:
            return jsonify({'error': 'Share not found'}), 404
        
        recipient_photo_id = share['recipient_photo_id']
        
        # 1. Get recipient's photo path and remove symlinks
        recip_conn = database.get_db_connection(recipient_email)
        recip_c = recip_conn.cursor()
        
        recip_c.execute("SELECT path FROM photos WHERE id = ?", (recipient_photo_id,))
        recip_photo = recip_c.fetchone()
        
        if recip_photo:
            symlink_path = recip_photo['path']
            # Remove image symlink
            if os.path.islink(symlink_path):
                os.unlink(symlink_path)
            
            # Remove thumbnail symlink
            recipient_thumb_dir = get_thumbnail_dir(recipient_email)
            unique_name = os.path.basename(symlink_path)
            recip_thumb_name = f"shared__{unique_name}" if unique_name.lower().endswith('.jpg') else f"shared__{unique_name}.jpg"
            recip_thumb_path = os.path.join(recipient_thumb_dir, recip_thumb_name)
            if os.path.islink(recip_thumb_path):
                os.unlink(recip_thumb_path)
            
            # Remove from recipient's DB
            recip_c.execute("DELETE FROM photo_people WHERE photo_id = ?", (recipient_photo_id,))
            recip_c.execute("DELETE FROM photos WHERE id = ?", (recipient_photo_id,))
            recip_c.execute("DELETE FROM shared_photos WHERE recipient_photo_id = ?", (recipient_photo_id,))
            recip_conn.commit()
        
        recip_conn.close()
        
        # 2. Remove share record from owner's DB
        owner_c.execute("DELETE FROM shared_photos WHERE original_photo_id = ? AND recipient_email = ?",
                       (photo_id, recipient_email))
        owner_conn.commit()
        owner_conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        owner_conn.close()
        print(f"Unshare error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/unshare/received', methods=['POST'])
def unshare_received_photo():
    """Remove a shared photo from recipient's side"""
    data = request.json
    photo_id = data.get('photo_id')  # recipient's photo ID
    
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not photo_id:
        return jsonify({'error': 'photo_id required'}), 400
    
    recip_conn = database.get_db_connection(userid)
    recip_c = recip_conn.cursor()
    
    try:
        # Find the share record in recipient's DB
        recip_c.execute("SELECT * FROM shared_photos WHERE recipient_photo_id = ?", (photo_id,))
        share = recip_c.fetchone()
        
        if not share:
            return jsonify({'error': 'Share not found'}), 404
        
        owner_email = share['owner_email']
        original_photo_id = share['original_photo_id']
        
        # Get the photo path
        recip_c.execute("SELECT path FROM photos WHERE id = ?", (photo_id,))
        photo = recip_c.fetchone()
        
        if photo:
            symlink_path = photo['path']
            if os.path.islink(symlink_path):
                os.unlink(symlink_path)
            
            # Remove thumbnail symlink
            recipient_thumb_dir = get_thumbnail_dir(userid)
            unique_name = os.path.basename(symlink_path)
            recip_thumb_name = f"shared__{unique_name}" if unique_name.lower().endswith('.jpg') else f"shared__{unique_name}.jpg"
            recip_thumb_path = os.path.join(recipient_thumb_dir, recip_thumb_name)
            if os.path.islink(recip_thumb_path):
                os.unlink(recip_thumb_path)
        
        # Clean up recipient's DB
        recip_c.execute("DELETE FROM photo_people WHERE photo_id = ?", (photo_id,))
        recip_c.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        recip_c.execute("DELETE FROM shared_photos WHERE recipient_photo_id = ?", (photo_id,))
        recip_conn.commit()
        recip_conn.close()
        
        # Clean up owner's DB
        try:
            owner_conn = database.get_db_connection(owner_email)
            owner_c = owner_conn.cursor()
            owner_c.execute("DELETE FROM shared_photos WHERE original_photo_id = ? AND recipient_email = ?",
                           (original_photo_id, userid))
            owner_conn.commit()
            owner_conn.close()
        except Exception:
            pass  # Owner DB cleanup is best-effort
        
        return jsonify({'success': True})
        
    except Exception as e:
        recip_conn.close()
        print(f"Unshare received error: {e}")
        return jsonify({'error': str(e)}), 500

# ================================
# Album Sharing API Endpoints
# ================================


@app.route('/api/albums/<int:album_id>/share/user', methods=['POST'])
def share_album_with_user(album_id):
    """Share an album (copy) with another user using symlinks."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    recipient_email = data.get('email')

    if not recipient_email:
        return jsonify({'error': 'Recipient email required'}), 400

    if recipient_email == userid:
        return jsonify({'error': 'Cannot share with yourself'}), 400

    try:
        # Verify recipient exists (check users first, then guests)
        recipient_found = False
        user_conn = sqlite3.connect(USER_DB_PATH)
        uc = user_conn.cursor()
        uc.execute("SELECT email FROM users WHERE email = ?", (recipient_email,))
        if uc.fetchone():
            recipient_found = True
        user_conn.close()
        
        if not recipient_found:
            # Check if recipient is a guest invited by this user
            gconn = get_guest_db()
            gc = gconn.cursor()
            gc.execute("""SELECT g.id FROM guests g
                          JOIN guest_hosts gh ON g.id = gh.guest_id
                          WHERE g.email = ? AND gh.host_email = ? AND gh.status = 'active'""",
                       (recipient_email, userid))
            if gc.fetchone():
                recipient_found = True
            gconn.close()
        
        if not recipient_found:
            return jsonify({'error': 'User not found'}), 404

        # Get source album and photos
        src_conn = database.get_db_connection(userid)
        sc = src_conn.cursor()
        sc.execute("SELECT name, description, album_type FROM albums WHERE id = ?", (album_id,))
        album_row = sc.fetchone()
        if not album_row:
            src_conn.close()
            return jsonify({'error': 'Album not found'}), 404

        # Block resharing: shared albums cannot be shared again
        if album_row['album_type'] == 'shared':
            src_conn.close()
            return jsonify({'error': 'Cannot reshare a shared album. Only the original owner can share.'}), 403

        album_name = album_row['name']
        album_desc = album_row['description'] or ''

        sc.execute("SELECT photo_id FROM album_photos WHERE album_id = ?", (album_id,))
        photo_ids = [row['photo_id'] for row in sc.fetchall()]

        # Get photo details
        photos = []
        for pid in photo_ids:
            sc.execute("SELECT * FROM photos WHERE id = ?", (pid,))
            photo = sc.fetchone()
            if photo:
                photos.append(dict(photo))

        # Block resharing: reject if album contains ANY received/shared photos
        for photo in photos:
            sc.execute("SELECT id FROM shared_photos WHERE recipient_photo_id = ? AND recipient_email = ?", (photo['id'], userid))
            if sc.fetchone():
                src_conn.close()
                return jsonify({'error': 'Cannot share an album that contains shared photos. Only original content can be shared.'}), 403

        # Create album and photos in recipient's DB using symlinks
        database.init_db(recipient_email)
        dst_conn = database.get_db_connection(recipient_email)
        dc = dst_conn.cursor()

        dc.execute(
            "INSERT INTO albums (name, description, album_type, owner_email, source_album_id) VALUES (?, ?, 'shared', ?, ?)",
            (f"{album_name} (from {userid})", album_desc, userid, album_id)
        )
        new_album_id = dc.lastrowid

        recipient_dir = os.path.join(DATA_DIR, recipient_email)
        shared_files_dir = os.path.join(recipient_dir, 'shared', 'files')
        os.makedirs(shared_files_dir, exist_ok=True)
        recipient_thumb_dir = get_thumbnail_dir(recipient_email)
        os.makedirs(recipient_thumb_dir, exist_ok=True)

        first_photo_id = None

        for photo in photos:
            original_path = photo['path']
            filename = os.path.basename(original_path)
            unique_name = f"{userid.split('@')[0]}_{filename}"
            symlink_path = os.path.join(shared_files_dir, unique_name)

            # Create image symlink
            real_original = os.path.realpath(original_path)
            if not os.path.exists(symlink_path):
                os.symlink(real_original, symlink_path)

            # Create thumbnail symlink
            try:
                rel_from_data = os.path.relpath(original_path, DATA_DIR)
                path_parts = rel_from_data.split(os.path.sep)
                device = path_parts[1]
                files_idx = original_path.find('/files/')
                if files_idx != -1:
                    rel_file = original_path[files_idx+7:]
                    safe_base = rel_file.replace(os.path.sep, '_')
                    orig_thumb_name = f"{device}__{safe_base}" if safe_base.lower().endswith('.jpg') else f"{device}__{safe_base}.jpg"
                    owner_thumb_dir = get_thumbnail_dir(userid)
                    orig_thumb_path = os.path.join(owner_thumb_dir, orig_thumb_name)

                    recip_thumb_name = f"shared__{unique_name}" if unique_name.lower().endswith('.jpg') else f"shared__{unique_name}.jpg"
                    recip_thumb_path = os.path.join(recipient_thumb_dir, recip_thumb_name)

                    if os.path.exists(orig_thumb_path) and not os.path.exists(recip_thumb_path):
                        os.symlink(os.path.realpath(orig_thumb_path), recip_thumb_path)
            except Exception as e:
                print(f"Thumbnail symlink error for {filename}: {e}")

            # Insert photo record with symlink path
            dc.execute("""
                INSERT OR IGNORE INTO photos
                (path, description, date_taken, location_lat, location_lon,
                 processed_for_thumbnails, processed_for_faces, processed_for_description, processed_for_exif, type)
                VALUES (?, ?, ?, ?, ?, 1, 1, 1, 1, ?)
            """, (symlink_path, photo.get('description'), photo.get('date_taken'),
                  photo.get('location_lat'), photo.get('location_lon'), photo.get('type')))
            new_photo_id = dc.lastrowid

            if first_photo_id is None:
                first_photo_id = new_photo_id

            dc.execute(
                "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)",
                (new_album_id, new_photo_id)
            )

            # Record in shared_photos (recipient's DB) so photos are marked as received
            dc.execute("""
                INSERT OR IGNORE INTO shared_photos (original_photo_id, owner_email, recipient_email, recipient_photo_id)
                VALUES (?, ?, ?, ?)
            """, (photo['id'], userid, recipient_email, new_photo_id))

            # Record in owner's DB
            sc.execute("""
                INSERT OR IGNORE INTO shared_photos (original_photo_id, owner_email, recipient_email, recipient_photo_id)
                VALUES (?, ?, ?, ?)
            """, (photo['id'], userid, recipient_email, new_photo_id))

        # Set cover photo
        if first_photo_id:
            dc.execute("UPDATE albums SET cover_photo_id = ? WHERE id = ?", (first_photo_id, new_album_id))

        dst_conn.commit()
        dst_conn.close()
        src_conn.commit()
        src_conn.close()

        return jsonify({'success': True, 'album_id': new_album_id})
    except Exception as e:
        print(f"Share album error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ================================
# Guest Management API Endpoints
# ================================

def require_not_guest():
    """Check that current user is NOT a guest. Returns error response or None."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    if is_guest_user(userid):
        return jsonify({'error': 'Guests cannot perform this action'}), 403
    return None

@app.route('/api/guests/list', methods=['GET'])
def list_guests():
    """List current user's invited guests with status."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    block = require_not_guest()
    if block: return block
    
    try:
        gconn = get_guest_db()
        gc = gconn.cursor()
        gc.execute("""SELECT g.id, g.email, g.host_count, g.created_at, g.last_login,
                             gh.added_date, gh.last_activated_date, gh.access_till, gh.status
                      FROM guests g
                      JOIN guest_hosts gh ON g.id = gh.guest_id
                      WHERE gh.host_email = ?
                      ORDER BY gh.added_date DESC""", (userid,))
        guests = []
        for r in gc.fetchall():
            # Check if expired
            status = r['status']
            if status == 'active':
                gc.execute("SELECT 1 WHERE datetime(?) < datetime('now')", (r['access_till'],))
                if gc.fetchone():
                    status = 'expired'
            guests.append({
                'id': r['id'],
                'email': r['email'],
                'host_count': r['host_count'],
                'added_date': r['added_date'],
                'last_login': r['last_login'],
                'access_till': r['access_till'],
                'status': status
            })
        gconn.close()
        return jsonify({'guests': guests})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guests/invite', methods=['POST'])
def invite_guest():
    """Invite a new guest. Body: {email, password, duration_days}"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    block = require_not_guest()
    if block: return block
    
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    duration_days = int(data.get('duration_days', 30))
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    if duration_days < 1 or duration_days > 365:
        return jsonify({'error': 'Duration must be 1-365 days'}), 400
    
    # Don't allow inviting an existing Photovault user as guest
    try:
        uconn = sqlite3.connect(USER_DB_PATH)
        uc = uconn.cursor()
        uc.execute("SELECT email FROM users WHERE email = ?", (email,))
        if uc.fetchone():
            uconn.close()
            return jsonify({'error': 'This email belongs to a Photovault user, not a guest'}), 400
        uconn.close()
    except Exception:
        pass
    
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
    
    try:
        gconn = get_guest_db()
        gc = gconn.cursor()
        
        # Check if guest email already exists
        gc.execute("SELECT id, password_hash, password_salt FROM guests WHERE email = ?", (email,))
        existing = gc.fetchone()
        
        if existing:
            guest_id = existing['id']
            # Check if this host already invited this guest
            gc.execute("SELECT id FROM guest_hosts WHERE guest_id = ? AND host_email = ?", (guest_id, userid))
            if gc.fetchone():
                gconn.close()
                return jsonify({'error': 'Guest already invited by you'}), 400
            
            # Add new host relationship, increment counter
            gc.execute("""INSERT INTO guest_hosts (guest_id, host_email, access_till, last_activated_date)
                          VALUES (?, ?, datetime('now', '+' || ? || ' days'), datetime('now'))""",
                       (guest_id, userid, duration_days))
            gc.execute("UPDATE guests SET host_count = host_count + 1 WHERE id = ?", (guest_id,))
        else:
            # Create new guest
            gc.execute("""INSERT INTO guests (email, password_hash, password_salt, host_count)
                          VALUES (?, ?, ?, 1)""", (email, password_hash, salt))
            guest_id = gc.lastrowid
            gc.execute("""INSERT INTO guest_hosts (guest_id, host_email, access_till, last_activated_date)
                          VALUES (?, ?, datetime('now', '+' || ? || ' days'), datetime('now'))""",
                       (guest_id, userid, duration_days))
        
        # Initialize guest's per-user data directory and DB
        database.init_db(email)
        
        gconn.commit()
        gconn.close()
        return jsonify({'success': True, 'guest_id': guest_id})
    except Exception as e:
        print(f"Invite guest error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/guests/delete', methods=['POST'])
def delete_guest():
    """Remove a guest invitation. Decrements counter, deletes at 0."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    block = require_not_guest()
    if block: return block
    
    data = request.json
    guest_email = data.get('email', '').strip().lower()
    if not guest_email:
        return jsonify({'error': 'Guest email required'}), 400
    
    try:
        gconn = get_guest_db()
        gc = gconn.cursor()
        
        gc.execute("SELECT id, host_count FROM guests WHERE email = ?", (guest_email,))
        guest = gc.fetchone()
        if not guest:
            gconn.close()
            return jsonify({'error': 'Guest not found'}), 404
        
        guest_id = guest['id']
        
        # Verify this host has a relationship
        gc.execute("SELECT id FROM guest_hosts WHERE guest_id = ? AND host_email = ?", (guest_id, userid))
        if not gc.fetchone():
            gconn.close()
            return jsonify({'error': 'Guest not invited by you'}), 404
        
        # Clean up shared assets for this host
        gc.execute("""SELECT asset_type, guest_asset_id FROM guest_assets 
                      WHERE guest_id = ? AND host_email = ?""", (guest_id, userid))
        assets = gc.fetchall()
        
        if assets:
            # Remove symlinks and DB records from guest's per-user DB
            try:
                guest_conn = database.get_db_connection(guest_email)
                guest_c = guest_conn.cursor()
                for asset in assets:
                    if asset['asset_type'] == 'photo' and asset['guest_asset_id']:
                        # Get path and remove symlinks
                        guest_c.execute("SELECT path FROM photos WHERE id = ?", (asset['guest_asset_id'],))
                        photo = guest_c.fetchone()
                        if photo and os.path.islink(photo['path']):
                            os.unlink(photo['path'])
                        guest_c.execute("DELETE FROM photo_people WHERE photo_id = ?", (asset['guest_asset_id'],))
                        guest_c.execute("DELETE FROM photos WHERE id = ?", (asset['guest_asset_id'],))
                        guest_c.execute("DELETE FROM shared_photos WHERE recipient_photo_id = ?", (asset['guest_asset_id'],))
                    elif asset['asset_type'] == 'album' and asset['guest_asset_id']:
                        guest_c.execute("DELETE FROM album_photos WHERE album_id = ?", (asset['guest_asset_id'],))
                        guest_c.execute("DELETE FROM albums WHERE id = ?", (asset['guest_asset_id'],))
                guest_conn.commit()
                guest_conn.close()
            except Exception as e:
                print(f"Error cleaning guest assets: {e}")
        
        # Remove guest_assets and guest_hosts for this host
        gc.execute("DELETE FROM guest_assets WHERE guest_id = ? AND host_email = ?", (guest_id, userid))
        gc.execute("DELETE FROM guest_hosts WHERE guest_id = ? AND host_email = ?", (guest_id, userid))
        
        # Decrement counter
        new_count = guest['host_count'] - 1
        if new_count <= 0:
            # No more hosts — delete guest entirely
            gc.execute("DELETE FROM guest_assets WHERE guest_id = ?", (guest_id,))
            gc.execute("DELETE FROM guest_hosts WHERE guest_id = ?", (guest_id,))
            gc.execute("DELETE FROM guests WHERE id = ?", (guest_id,))
            # Optionally remove guest's data directory
            guest_dir = get_user_dir(guest_email)
            if os.path.exists(guest_dir):
                import shutil
                shutil.rmtree(guest_dir, ignore_errors=True)
        else:
            gc.execute("UPDATE guests SET host_count = ? WHERE id = ?", (new_count, guest_id))
        
        gconn.commit()
        gconn.close()
        return jsonify({'success': True, 'remaining_hosts': max(new_count, 0)})
    except Exception as e:
        print(f"Delete guest error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/guests/revoke', methods=['POST'])
def revoke_guest():
    """Set guest status to inactive for current host."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    block = require_not_guest()
    if block: return block
    
    data = request.json
    guest_email = data.get('email', '').strip().lower()
    if not guest_email:
        return jsonify({'error': 'Guest email required'}), 400
    
    try:
        gconn = get_guest_db()
        gc = gconn.cursor()
        gc.execute("SELECT id FROM guests WHERE email = ?", (guest_email,))
        guest = gc.fetchone()
        if not guest:
            gconn.close()
            return jsonify({'error': 'Guest not found'}), 404
        
        gc.execute("""UPDATE guest_hosts SET status = 'inactive' 
                      WHERE guest_id = ? AND host_email = ?""", (guest['id'], userid))
        if gc.rowcount == 0:
            gconn.close()
            return jsonify({'error': 'Guest not invited by you'}), 404
        
        gconn.commit()
        gconn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guests/reactivate', methods=['POST'])
def reactivate_guest():
    """Reactivate a guest with new duration. Body: {email, duration_days}"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    block = require_not_guest()
    if block: return block
    
    data = request.json
    guest_email = data.get('email', '').strip().lower()
    duration_days = int(data.get('duration_days', 30))
    if not guest_email:
        return jsonify({'error': 'Guest email required'}), 400
    if duration_days < 1 or duration_days > 365:
        return jsonify({'error': 'Duration must be 1-365 days'}), 400
    
    try:
        gconn = get_guest_db()
        gc = gconn.cursor()
        gc.execute("SELECT id FROM guests WHERE email = ?", (guest_email,))
        guest = gc.fetchone()
        if not guest:
            gconn.close()
            return jsonify({'error': 'Guest not found'}), 404
        
        gc.execute("""UPDATE guest_hosts SET status = 'active',
                      access_till = datetime('now', '+' || ? || ' days'),
                      last_activated_date = datetime('now')
                      WHERE guest_id = ? AND host_email = ?""",
                   (duration_days, guest['id'], userid))
        if gc.rowcount == 0:
            gconn.close()
            return jsonify({'error': 'Guest not invited by you'}), 404
        
        gconn.commit()
        gconn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    app.run(debug=False, port=8877, host='0.0.0.0')
