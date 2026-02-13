import os
import secrets
import sqlite3
import database
import zipfile
import io
import time
from flask import Flask, request, jsonify, send_from_directory, render_template, abort, send_file

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = secrets.token_hex(16)

import hashlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))
USER_DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '../backup/user.sql'))

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
        conn = sqlite3.connect(USER_DB_PATH)
        c = conn.cursor()
        
        # Check if user exists
        # Schema: id, email, password_hash, salt, unique_id, is_admin, status, created_at
        c.execute("SELECT password_hash, salt, status, is_admin FROM users WHERE email = ?", (userid,))
        row = c.fetchone()
        
        if row:
            stored_hash, salt, status, is_admin = row
            
            # Verify basic status
            if status != 'active':
                return jsonify({'error': 'Account is not active'}), 403
            
            # Hash provided password with stored salt
            # Logic from cr.py: hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
            calc_hash = hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
            
            if calc_hash == stored_hash:
                resp = make_response(jsonify({
                    'success': True, 
                    'userid': userid,
                    'is_admin': bool(is_admin)
                }))
                # Set cookie for 12 hours
                resp.set_cookie('userid', userid, max_age=12 * 60 * 60)
                return resp
            else:
                return jsonify({'error': 'Invalid password'}), 401
        else:
             return jsonify({'error': 'User not found'}), 404
             
    except Exception as e:
        print(f"Auth Error: {e}")
        return jsonify({'error': 'Authentication failed due to server error'}), 500
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    userid = request.cookies.get('userid')
    if userid:
        return jsonify({'authenticated': True, 'userid': userid})
    return jsonify({'authenticated': False}), 401

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
            
            disk = psutil.disk_usage(DATA_DIR)
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
    c.execute("SELECT id, name, thumbnail_path FROM people")
    rows = c.fetchall()
    people = [{'id': r['id'], 'name': r['name'], 'thumbnail': r['thumbnail_path']} for r in rows]
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
    c.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))
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
            ORDER BY day DESC
        """, (f"%{userid}%",))
        
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
                
            date_query += " ORDER BY date_taken DESC"
            
            c.execute(date_query, (photo_date, f"%{userid}%"))
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
            
        # Order unknown by timestamp (upload time) or just ID
        unknown_query += " ORDER BY timestamp DESC"
        
        c.execute(unknown_query, (f"%{userid}%",))
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
               a.cover_photo_id, p.path as cover_path, p.type as cover_type,
               COUNT(ap.photo_id) as photo_count
        FROM albums a
        LEFT JOIN album_photos ap ON a.id = ap.album_id
        LEFT JOIN photos p ON a.cover_photo_id = p.id
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
            'cover_url': None
        }
        
        # Generate cover thumbnail URL if available
        if r['cover_photo_id'] and r['cover_path']:
            try:
                cover_photo_data = build_photo_response(r['cover_path'], r['cover_photo_id'], r['cover_type'], userid=userid)
                if cover_photo_data:
                    album['cover_url'] = cover_photo_data['thumbnail_url']
            except Exception:
                pass
        
        albums.append(album)
    
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
    """Delete an album"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    
    try:
        c.execute("DELETE FROM album_photos WHERE album_id = ?", (album_id,))
        c.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
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
                media_type = 'video' if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm') else 'image'

            result = {
                'id': photo_id,
                'thumbnail_url': f"/resource/thumbnail/{file_userid}/{safe_thumb}",
                'image_url': f"/resource/image/{file_userid}/{device}/{rel_path}",
                'type': media_type,
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
    """List all users from user.sql for the share picker"""
    current_user = get_current_userid()
    if not current_user:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        conn = sqlite3.connect(USER_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT email, status FROM users WHERE email != ? AND status = 'active'", (current_user,))
        users = [{'email': r['email']} for r in c.fetchall()]
        conn.close()
        return jsonify({'users': users})
    except Exception as e:
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
        owner_c.execute("SELECT id FROM shared_photos WHERE recipient_photo_id = ?", (photo_id,))
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

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    app.run(debug=False, port=8877, host='0.0.0.0')
