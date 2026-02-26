import os
import re
import hashlib
import secrets
import sqlite3
import subprocess
import database
import zipfile
import io
import time
import json
import base64
import bcrypt
from flask import Flask, request, jsonify, send_from_directory, render_template, abort, send_file, Response, stream_with_context, session
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='static', template_folder='templates')

# --- Persistent Secret Key ---
# Generate once and persist to file so sessions survive server restarts.
_SECRET_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'session_secret.key')
if os.path.exists(_SECRET_KEY_PATH):
    with open(_SECRET_KEY_PATH, 'r') as _f:
        app.secret_key = _f.read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    with open(_SECRET_KEY_PATH, 'w') as _f:
        _f.write(app.secret_key)
    os.chmod(_SECRET_KEY_PATH, 0o600)

# --- Session Cookie Security ---
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

# --- CSRF Protection ---
# SameSite=Lax blocks most CSRF. This adds Origin/Referer validation as defense-in-depth.
@app.before_request
def csrf_protect():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return  # Safe methods don't need CSRF checks
    # Allow shared-link endpoints (public, no session needed)
    if request.path.startswith('/api/links/verify') or request.path.startswith('/api/links/view'):
        return
    origin = request.headers.get('Origin') or ''
    referer = request.headers.get('Referer') or ''
    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.netloc and parsed.netloc != request.host:
            return jsonify({'error': 'CSRF rejected'}), 403
    elif referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        if parsed.netloc and parsed.netloc != request.host:
            return jsonify({'error': 'CSRF rejected'}), 403

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))
USER_DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '../backup/user.sql'))
GLOBAL_SHARE_DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '../backup/global_share.db'))

def init_global_share_db():
    """Create global_share.db tables if they don't exist."""
    conn = sqlite3.connect(GLOBAL_SHARE_DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS shared_links (
        link_hash TEXT PRIMARY KEY,
        owner_email TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        asset_title TEXT,
        asset_type TEXT NOT NULL,
        thumbnail_id INTEGER,
        password_hash TEXT,
        salt TEXT,
        expires_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        link_name TEXT
    )''')
    # Migrate existing DBs that don't have link_name column yet
    try:
        c.execute('ALTER TABLE shared_links ADD COLUMN link_name TEXT')
    except Exception:
        pass  # Column already exists
    
    # New table for sharing internally with specific users
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
    
    # Table to track items hidden by viewers from a public link
    c.execute('''CREATE TABLE IF NOT EXISTS shared_link_hidden_items (
        link_hash TEXT NOT NULL,
        photo_id INTEGER NOT NULL,
        hidden_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (link_hash, photo_id)
    )''')
    conn.commit()
    conn.close()

def get_global_share_db():
    """Get a connection to global_share.db with WAL mode."""
    conn = sqlite3.connect(GLOBAL_SHARE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn

# Initialize DBs on startup
init_global_share_db()

# Userid must be a safe string (email-like) — no path separators, .., or null bytes
_SAFE_USERID_RE = re.compile(r'^[a-zA-Z0-9@._+\-]+$')

def get_user_dir(userid):
    if not userid or '..' in userid or not _SAFE_USERID_RE.match(userid):
        raise ValueError(f"Invalid userid format")
    return os.path.join(DATA_DIR, userid)

def safe_resolve_path(base_dir, untrusted_path):
    """Resolve an untrusted path relative to base_dir, aborting on traversal."""
    resolved = os.path.normpath(os.path.join(base_dir, untrusted_path))
    # Trailing os.sep prevents prefix attacks: /data/user vs /data/user_evil
    if not (resolved + os.sep).startswith(os.path.normpath(base_dir) + os.sep):
        abort(403)
    return resolved

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
            
            # Verify password: bcrypt if salt is NULL/empty, legacy SHA-256 otherwise
            password_ok = False
            is_legacy = bool(salt)  # Legacy SHA-256 hashes have a salt column
            if is_legacy:
                calc_hash = hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
                password_ok = (calc_hash == stored_hash)
            else:
                try:
                    password_ok = bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
                except Exception:
                    password_ok = False
            
            if password_ok:
                session.permanent = True
                session['userid'] = userid
                session['role'] = 'user'
                return jsonify({
                    'success': True, 
                    'userid': userid,
                    'is_admin': bool(is_admin),
                    'role': 'user',
                    'force_change': bool(force_change)
                })
                
                # Auto-upgrade legacy SHA-256 hash to bcrypt on successful login
                if is_legacy:
                    try:
                        new_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
                        upgrade_conn = sqlite3.connect(USER_DB_PATH)
                        uc = upgrade_conn.cursor()
                        uc.execute("UPDATE users SET password_hash = ?, salt = NULL WHERE email = ?", (new_hash, userid))
                        upgrade_conn.commit()
                        upgrade_conn.close()
                    except Exception:
                        pass  # Non-critical: upgrade silently fails, old hash still works
            else:
                return jsonify({'error': 'Invalid password'}), 401
        
        return jsonify({'error': 'User not found'}), 404
             
    except Exception as e:
        print(f"Auth Error: {e}")
        return jsonify({'error': 'Authentication failed due to server error'}), 500

@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    userid = session.get('userid')
    role = session.get('role', 'user')
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
    userid = session.get('userid')
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    new_password = data.get('new_password')
    
    if not new_password:
        return jsonify({'error': 'New password required'}), 400
        
    try:
        conn = sqlite3.connect(USER_DB_PATH)
        c = conn.cursor()
        
        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
        
        c.execute("UPDATE users SET password_hash = ?, salt = NULL, force_password_change = 0 WHERE email = ?", (password_hash, userid))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

def get_current_userid():
    """Resolve userid exclusively from signed session."""
    return session.get('userid')

@app.route('/api/scan', methods=['POST'])
def scan_files():
    # Scanning is now handled by daemon.py primarily for DB population.
    # However, frontend expects a tree structure. 
    # We can either construct it from DB or filesystem. 
    # Filesystem is still the source of truth for the tree view.
    userid = session.get('userid')
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

def _is_authorized_for_asset(userid, asset_path):
    """Check if the current request is authorized to view this asset.
    Allows access if logged in as the owner, if explicitly shared with the current user, 
    or if a valid valid link token is present for this asset.
    """
    current_userid = get_current_userid()
    
    # 1. Check if logged in directly as owner
    if current_userid == userid:
        return True
        
    # 2. Check explicitly shared assets (Internal Sharing)
    if current_userid:
        gconn = get_global_share_db()
        gc = gconn.cursor()
        try:
            # Get all assets shared with this user by this owner
            gc.execute("SELECT asset_id, asset_type FROM shared_asset_users WHERE owner_email = ? AND shared_with_email = ?", (userid, current_userid))
            shared_items = gc.fetchall()
            
            if shared_items:
                conn = database.get_db_connection(userid)
                c = conn.cursor()
                try:
                    for row in shared_items:
                        asset_id = row['asset_id']
                        asset_type = row['asset_type']
                        
                        if asset_type in ('photo', 'video'):
                            c.execute("SELECT path FROM photos WHERE id = ?", (asset_id,))
                            photo_row = c.fetchone()
                            if photo_row and photo_row['path'] == asset_path:
                                return True
                        elif asset_type == 'album':
                            c.execute("""
                                SELECT 1 FROM photos p
                                JOIN album_photos ap ON p.id = ap.photo_id
                                WHERE ap.album_id = ? AND p.path = ?
                            """, (asset_id, asset_path))
                            if c.fetchone():
                                return True
                finally:
                    conn.close()
        finally:
            gconn.close()
        
    # 3. Check for active link cookies (Public Sharing)
    # We look for any cookie starting with 'link_auth_'
    # and verify it against the global_share db.
    for cookie_name, link_hash in request.cookies.items():
        if cookie_name.startswith('link_auth_'):
            gconn = get_global_share_db()
            gc = gconn.cursor()
            try:
                gc.execute("SELECT asset_id, asset_type FROM shared_links WHERE link_hash = ? AND owner_email = ?", (link_hash, userid))
                row = gc.fetchone()
                if row:
                    asset_id = row['asset_id']
                    asset_type = row['asset_type']
                    
                    # We need to see if this asset corresponds to the requested path.
                    # This requires checking the user's DB.
                    conn = database.get_db_connection(userid)
                    c = conn.cursor()
                    try:
                        if asset_type in ('photo', 'video'):
                            # Check if the requested path matches this asset's path
                            c.execute("SELECT path FROM photos WHERE id = ?", (asset_id,))
                            photo_row = c.fetchone()
                            if photo_row and photo_row['path'] == asset_path:
                                return True
                        elif asset_type == 'album':
                            # Check if the requested path is part of this album
                            c.execute("""
                                SELECT p.path FROM photos p
                                JOIN album_photos ap ON p.id = ap.photo_id
                                WHERE ap.album_id = ? AND p.path = ?
                            """, (asset_id, asset_path))
                            if c.fetchone():
                                return True
                    finally:
                        conn.close()
            finally:
                gconn.close()
                
    return False

@app.route('/resource/thumbnail/<userid>/<filename>')
def serve_thumbnail(userid, filename):
    if not _SAFE_USERID_RE.match(userid):
        abort(400)
    thumb_dir = get_thumbnail_dir(userid)
    
    # Reconstruct the original file path from the thumbnail name to check auth
    # Format: device__rel_path.ext
    # Warning: this is a heuristic to go from thumb to path. 
    # Actually, we can just allow thumbnails if they provide *any* valid link_hash for simplicity and performance?
    # Or strict: decode it. Let's do strict.
    # filename is `device__files_rest_of_path.ext` or `device__rest_of_path`.ext
    try:
        parts = filename.split('__', 1)
        if len(parts) == 2:
            device = parts[0]
            rest = parts[1]
            if rest.endswith('.jpg'):
                 # It might be a video thumbnail, or an original jpg. The actual path might differ slightly.
                 # Actually, the DB has `thumbnail_path`. But for `/resource/thumbnail/`, the frontend passes
                 # what `build_photo_response` generated. 
                 # Let's bypass strict thumbnail checking if they have ANY valid link cookie for the user,
                 # or if we want to be very secure, do a DB lookup by thumbnail.
                 pass
    except Exception:
        pass
        
    # For thumbnails, to prevent N+1 queries on album loads, if they have the cookie for the album, we just let them load it.
    # Let's do a slightly looser check: if they have a valid cookie for THIS userid, we allow thumbnails, 
    # relying on the frontend to only know the unguessable thumbnail names.
    authorized = False
    current_userid = get_current_userid()
    if current_userid == userid:
        authorized = True
    else:
        if current_userid:
            gconn = get_global_share_db()
            gc = gconn.cursor()
            gc.execute("SELECT 1 FROM shared_asset_users WHERE owner_email = ? AND shared_with_email = ?", (userid, current_userid))
            if gc.fetchone():
                authorized = True
            gconn.close()

        if not authorized:
            for cookie_name, link_hash in request.cookies.items():
                if cookie_name.startswith('link_auth_'):
                    gconn = get_global_share_db()
                    gc = gconn.cursor()
                    gc.execute("SELECT 1 FROM shared_links WHERE link_hash = ? AND owner_email = ?", (link_hash, userid))
                    if gc.fetchone():
                        authorized = True
                    gconn.close()
                    if authorized: break
                
    if not authorized:
        abort(403)
        
    return send_from_directory(thumb_dir, filename)

def send_file_partial(path):
    """
    Use Flask's native send_file with conditional=True.
    This automatically handles Range requests (206), ETags, and Last-Modified.
    It is much more robust than hand-rolled partial content responses.
    """
    return send_file(path, conditional=True)

# Extensions that browsers cannot play natively — must be transcoded
BROWSER_INCOMPATIBLE_VIDEO_EXTS = {'.mts', '.m2ts', '.avi', '.mkv'}

@app.route('/resource/video/<userid>/<device>/<path:filename>')
def serve_video_transcoded(userid, device, filename):
    """
    Stream-transcode browser-incompatible video formats (MTS, M2TS, AVI, MKV)
    to H.264/AAC MP4 on-the-fly via ffmpeg so the browser can play them.
    """
    if not _SAFE_USERID_RE.match(userid):
        abort(400)
    user_dir = get_user_dir(userid)
    file_path = safe_resolve_path(user_dir, os.path.join(device, 'files', filename))
    if not os.path.exists(file_path):
        abort(404)
        
    if not _is_authorized_for_asset(userid, file_path):
        abort(403)

    def generate():
        cmd = [
            'ffmpeg', '-y',
            '-i', file_path,
            '-vcodec', 'libx264',
            '-preset', 'ultrafast',   # minimise latency before first frame
            '-crf', '23',
            '-acodec', 'aac',
            '-b:a', '128k',
            '-movflags', 'frag_keyframe+empty_moov+faststart',  # streaming-friendly MP4
            '-f', 'mp4',
            'pipe:1',
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.stdout.close()
            proc.wait()

    return Response(
        stream_with_context(generate()),
        mimetype='video/mp4',
        headers={'X-Accel-Buffering': 'no'},  # disable nginx buffering if behind a proxy
    )

@app.route('/resource/image/<userid>/<device>/<path:filename>')
def serve_image(userid, device, filename):
    if not _SAFE_USERID_RE.match(userid):
        abort(400)
    user_dir = get_user_dir(userid)
    file_path = safe_resolve_path(user_dir, os.path.join(device, 'files', filename))
    if not os.path.exists(file_path):
        abort(404)
        
    if not _is_authorized_for_asset(userid, file_path):
        abort(403)
        
    return send_file_partial(file_path)

@app.route('/api/photo/metadata', methods=['GET'])
def get_photo_metadata():
    userid = session.get('userid')
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


@app.route('/api/upload', methods=['POST'])
def upload_file():
    userid = session.get('userid')
    batch_id = request.form.get('upload_batch_id')
    
    if not userid or not batch_id:
        return jsonify({'error': 'Missing userid or upload_batch_id'}), 400
        
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    user_dir = get_user_dir(userid)
    
    # Target directory based on new requirement: ../backup/data/<user_id>/web/files/<date_time>
    # 'batch_id' passed from frontend will act as the <date_time> folder name
    target_dir = os.path.join(user_dir, 'web', 'files', batch_id)
    os.makedirs(target_dir, exist_ok=True)
    
    # Save the file. We use the filename directly (if flattened) or preserve path if needed, 
    # but the frontend will send just the filename per the updated requirement.
    safe_filename = os.path.basename(file.filename)
    target_path = os.path.join(target_dir, safe_filename)
    
    try:
        file.save(target_path)
        return jsonify({'success': True, 'path': target_path})
    except Exception as e:
        print(f"Error saving upload: {e}")
        return jsonify({'error': str(e)}), 500

# --- Dashboard API ---

@app.route('/api/dashboard/stats', methods=['GET'])
def get_dashboard_stats():
    userid = session.get('userid')
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
    userid = session.get('userid')
    path_arg = request.args.get('path', '')
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    user_dir = get_user_dir(userid)
    
    # Security: Ensure path is within user_dir
    target_path = safe_resolve_path(user_dir, path_arg)

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
    userid = session.get('userid')
    path_arg = data.get('path')
    new_name = data.get('new_name')
    
    if not userid or not path_arg or not new_name:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    old_path = safe_resolve_path(user_dir, path_arg)
    
    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_name)
    
    # Ensure new path also stays within user_dir
    safe_resolve_path(user_dir, os.path.relpath(new_path, user_dir))
        
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
    userid = session.get('userid')
    paths = data.get('paths', []) # List of relative paths (relative to user_dir)
    
    if not userid or not paths:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    
    deleted = []
    errors = []
    
    for path_arg in paths:
        target_path = safe_resolve_path(user_dir, path_arg)
        
        # safe_resolve_path aborts on traversal
            
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
    userid = session.get('userid')
    path_arg = data.get('path')
    
    if not userid or not path_arg:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    target_path = safe_resolve_path(user_dir, path_arg)
    
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

@app.route('/api/files/edit', methods=['POST'])
def edit_file():
    data = request.json
    userid = get_current_userid()
    file_id = data.get('file_id')
    image_data_b64 = data.get('image_data')
    save_mode = data.get('save_mode')       # 'overwrite' or 'save_as'
    new_filename_raw = data.get('new_filename', '').strip()  # optional custom name for save_as
    
    if not userid or not file_id or not image_data_b64 or not save_mode:
        return jsonify({'error': 'Missing parameters'}), 400
        
    try:
        # Strip the data URL prefix "data:image/jpeg;base64,"
        header, encoded = image_data_b64.split(",", 1)
        image_bytes = base64.b64decode(encoded)
        
        conn = database.get_db_connection(userid)
        c = conn.cursor()
        
        # Look up original file details (photos table has no filename column — derive it from path)
        # Fetch all metadata so we can copy it to the new row
        c.execute("""
            SELECT path, date_taken, description, location_lat, location_lon
            FROM photos WHERE id = ?
        """, (file_id,))
        row = c.fetchone()
        
        if not row:
            conn.close()
            return jsonify({'error': 'Original file not found'}), 404
            
        original_relative_path = row['path']
        original_filename = os.path.basename(original_relative_path)
        original_abs_path = os.path.join(DATA_DIR, userid, original_relative_path)
        
        if not os.path.exists(original_abs_path):
            conn.close()
            return jsonify({'error': 'Source file missing from disk'}), 404

        new_hash = hashlib.sha256(image_bytes).hexdigest()
        new_size = len(image_bytes)
        
        if save_mode == 'overwrite':
            # Write bytes directly over original file
            with open(original_abs_path, 'wb') as f:
                f.write(image_bytes)
                
            # Write bytes over original file — path unchanged, no DB update needed
            conn.commit()
            
            # Regenerate thumbnail inline — no daemon involvement
            try:
                from PIL import Image as PILImage
                thumb_dir = get_thumbnail_dir(userid)
                os.makedirs(thumb_dir, exist_ok=True)
                thumb_name = os.path.splitext(original_filename)[0] + '_thumb.jpg'
                thumb_path = os.path.join(thumb_dir, thumb_name)
                pil_img = PILImage.open(original_abs_path)
                pil_img.thumbnail((400, 400))
                pil_img.save(thumb_path, 'JPEG')
            except Exception as e:
                print(f'Warning: Could not regenerate thumbnail for overwrite: {e}')

            conn.close()
            return jsonify({'success': True, 'action': 'overwritten', 'file_id': file_id})
            
        elif save_mode == 'save_as':
            # Use caller-provided name, or derive a default from original
            name, ext = os.path.splitext(original_filename)
            if new_filename_raw:
                # Sanitise: strip path separators, ensure correct extension
                safe_name = os.path.basename(new_filename_raw)
                # Force the original extension if the caller didn't include one
                if not os.path.splitext(safe_name)[1]:
                    safe_name = safe_name + ext
                new_filename = safe_name
            else:
                new_filename = f"{name}_copy{ext}"
            
            # Ensure unique filename in the same directory
            original_dir = os.path.dirname(original_abs_path)
            relative_dir = os.path.dirname(original_relative_path)
            
            counter = 1
            test_abs_path = os.path.join(original_dir, new_filename)
            while os.path.exists(test_abs_path):
                n, e = os.path.splitext(new_filename)
                new_filename = f"{n}_{counter}{e}"
                test_abs_path = os.path.join(original_dir, new_filename)
                counter += 1
                
            # Write new file to disk
            with open(test_abs_path, 'wb') as f:
                f.write(image_bytes)

            # Insert new row, copying all metadata from the original
            new_relative_path = os.path.join(relative_dir, new_filename) if relative_dir else new_filename
            c.execute("""
                INSERT INTO photos (path, type, timestamp, date_taken, description, location_lat, location_lon)
                VALUES (?, 'photo', ?, ?, ?, ?, ?)
            """, (
                new_relative_path,
                int(time.time()),
                row['date_taken'],
                row['description'],
                row['location_lat'],
                row['location_lon']
            ))
            
            new_id = c.lastrowid
            conn.commit()
            
            # Generate thumbnail inline — no daemon involvement
            try:
                from PIL import Image as PILImage
                thumb_dir = get_thumbnail_dir(userid)
                os.makedirs(thumb_dir, exist_ok=True)
                thumb_name = os.path.splitext(new_filename)[0] + '_thumb.jpg'
                thumb_path = os.path.join(thumb_dir, thumb_name)
                pil_img = PILImage.open(test_abs_path)
                pil_img.thumbnail((400, 400))
                pil_img.save(thumb_path, 'JPEG')
            except Exception as e:
                print(f'Warning: Could not generate thumbnail for new copy: {e}')

            conn.close()
            return jsonify({'success': True, 'action': 'saved_as_copy', 'new_file_id': new_id})

    except Exception as e:
        print(f"Error saving edited file: {e}")
        return jsonify({'error': str(e)}), 500

# --- Admin API ---

import reset_db

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        userid = session.get('userid')
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
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
        unique_id = hashlib.sha256(f"{time.time()}{secrets.token_hex(16)}".encode()).hexdigest()
        
        c.execute("""
            INSERT INTO users (email, password_hash, salt, unique_id, is_admin, status, force_password_change)
            VALUES (?, ?, NULL, ?, ?, ?, 1)
        """, (email, password_hash, unique_id, False, 'active'))
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
    current_admin = session.get('userid')
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
    userid = session.get('userid')
    path_arg = data.get('path')
    
    if not userid or not path_arg:
        return jsonify({'error': 'Missing parameters'}), 400
        
    user_dir = get_user_dir(userid)
    target_path = safe_resolve_path(user_dir, path_arg)
    
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
    userid = session.get('userid')
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
                full_path = safe_resolve_path(user_dir, path_arg)
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
    userid = session.get('userid')
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

        # removed legacy checking of shared_photos table
        album['has_shared_photos'] = False
    
    conn.close()

    # Fetch shared albums
    gconn = get_global_share_db()
    gconn.row_factory = sqlite3.Row
    try:
        gc = gconn.cursor()
        gc.execute("""
            SELECT owner_email, asset_id, asset_title, thumbnail_id, created_at
            FROM shared_asset_users
            WHERE shared_with_email = ? AND asset_type = 'album'
        """, (userid,))
        for s in gc.fetchall():
            shared_album = {
                'id': f"shared_{s['asset_id']}_{s['owner_email']}",
                'name': s['asset_title'] or 'Shared Album',
                'description': f"Shared by {s['owner_email']}",
                'album_type': 'shared',
                'created_at': s['created_at'],
                'source_album_id': None,
                'owner_email': s['owner_email'],
                'cover_url': None,
                'has_shared_photos': False
            }
            try:
                owner_conn = database.get_db_connection(s['owner_email'])
                owner_conn.row_factory = sqlite3.Row
                oc = owner_conn.cursor()
                
                oc.execute("SELECT COUNT(*) FROM album_photos WHERE album_id = ?", (s['asset_id'],))
                shared_album['photo_count'] = oc.fetchone()[0]
                
                thumb_id = s['thumbnail_id']
                if not thumb_id:
                    oc.execute("""
                        SELECT p.id FROM photos p
                        JOIN album_photos ap ON p.id = ap.photo_id
                        WHERE ap.album_id = ?
                        ORDER BY ap.rowid ASC LIMIT 1
                    """, (s['asset_id'],))
                    first_photo = oc.fetchone()
                    if first_photo:
                        thumb_id = first_photo['id']
                
                if thumb_id:
                    oc.execute("SELECT path, type FROM photos WHERE id = ?", (thumb_id,))
                    thumb_row = oc.fetchone()
                    if thumb_row:
                        thumb_data = build_photo_response(thumb_row['path'], thumb_id, thumb_row['type'], userid=s['owner_email'])
                        if thumb_data:
                            shared_album['cover_url'] = thumb_data.get('thumbnail_url')
                owner_conn.close()
            except Exception as e:
                print(f"Error fetching data for shared album: {e}")
                shared_album['photo_count'] = 0

            albums.append(shared_album)
    except Exception as e:
        print(f"Error fetching shared albums: {e}")
    finally:
        gconn.close()

    return jsonify({'albums': albums})

@app.route('/api/users', methods=['GET'])
def list_active_users():
    """List all active users for sharing (excluding admin and current user)"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
        
    conn = sqlite3.connect(USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Exclude the current user and admin users
    c.execute("SELECT email FROM users WHERE status = 'active' AND is_admin = 0 AND email != ?", (userid,))
    users = [row['email'] for row in c.fetchall()]
    conn.close()
    return jsonify({'users': users})

@app.route('/api/albums/<album_id>/share/user', methods=['POST'])
def share_album_user(album_id):
    """Share album(s) with specific users"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
        
    data = request.json
    shared_with_emails = data.get('shared_with_emails', [])
    if not shared_with_emails:
        return jsonify({'error': 'No users specified'}), 400
        
    album_ids = [aid.strip() for aid in str(album_id).split(',') if aid.strip().isdigit()]
    if not album_ids:
        return jsonify({'error': 'Invalid album ID(s)'}), 400
        
    conn = database.get_db_connection(userid)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    albums = []
    for aid in album_ids:
        c.execute("SELECT * FROM albums WHERE id = ?", (aid,))
        album = c.fetchone()
        if album:
            albums.append(album)
    conn.close()
    
    if not albums:
        return jsonify({'error': 'No valid albums found'}), 404
        
    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        for album in albums:
            for email in shared_with_emails:
                gc.execute('''
                    INSERT OR IGNORE INTO shared_asset_users 
                    (owner_email, asset_id, asset_title, asset_type, thumbnail_id, shared_with_email) 
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (userid, album['id'], album['name'], 'album', album['cover_photo_id'], email))
        gconn.commit()
        return jsonify({'success': True})
    except Exception as e:
        gconn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()

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

@app.route('/api/albums/<album_id>', methods=['DELETE'])
def delete_album(album_id):
    """Delete an album. If the album was shared, cascade unshare to recipients (for owner) or remove from view (for recipient)."""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    if isinstance(album_id, str) and album_id.startswith('shared_'):
        parts = album_id.split('_', 2)
        if len(parts) == 3:
            asset_id = int(parts[1])
            owner_email = parts[2]
            gconn = get_global_share_db()
            gc = gconn.cursor()
            try:
                gc.execute("DELETE FROM shared_asset_users WHERE asset_id = ? AND asset_type = 'album' AND shared_with_email = ? AND owner_email = ?", (asset_id, userid, owner_email))
                gconn.commit()
                return jsonify({'success': True})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
            finally:
                gconn.close()
        return jsonify({'error': 'Invalid shared album ID'}), 400

    try:
        album_id = int(album_id)
    except ValueError:
        return jsonify({'error': 'Invalid album ID'}), 400

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

        # Delete the album itself
        c.execute("DELETE FROM album_photos WHERE album_id = ?", (album_id,))
        c.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        conn.commit()
        conn.close()
        
        # Cascade unshare
        gconn = get_global_share_db()
        try:
            gc = gconn.cursor()
            gc.execute("DELETE FROM shared_asset_users WHERE asset_id = ? AND asset_type = 'album' AND owner_email = ?", (album_id, userid))
            gconn.commit()
        except:
            pass
        finally:
            gconn.close()
            
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/albums/<album_id>/photos', methods=['GET'])
def get_album_photos(album_id):
    """Get photos in a specific album"""
    userid = get_current_userid() # Use current user's ID
    
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check if user owns the album OR if it's shared with them
    is_owner = True
    owner_email = userid
    
    if isinstance(album_id, str) and album_id.startswith('shared_'):
        parts = album_id.split('_', 2)
        if len(parts) == 3:
            album_id = int(parts[1])
            owner_email = parts[2]
            
            gconn = get_global_share_db()
            gc = gconn.cursor()
            try:
                gc.execute("SELECT 1 FROM shared_asset_users WHERE asset_id = ? AND asset_type = 'album' AND shared_with_email = ? AND owner_email = ?", (album_id, userid, owner_email))
                if not gc.fetchone():
                    return jsonify({'error': 'Album not found or access denied'}), 404
            finally:
                gconn.close()
            is_owner = False
        else:
            return jsonify({'error': 'Invalid shared album ID'}), 400
    else:
        try:
            album_id = int(album_id)
        except ValueError:
            return jsonify({'error': 'Invalid album ID'}), 400

        conn = database.get_db_connection(userid)
        c = conn.cursor()
        c.execute("SELECT id FROM albums WHERE id = ?", (album_id,))
        if not c.fetchone():
            conn.close()
            return jsonify({'error': 'Album not found'}), 404
        conn.close()
            
    # Now fetch from the owner's DB
    conn = database.get_db_connection(owner_email)
    conn.row_factory = sqlite3.Row
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
            photo_data = build_photo_response(r['path'], r['id'], r['type'], userid=owner_email) # pass owner_email for correct URL paths
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
    userid = session.get('userid')
    
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
    userid = session.get('userid')
    
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

            ext = os.path.splitext(abs_path)[1].lower()
            if not media_type:
                media_type = 'video' if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts') else 'image'

            # Browser-incompatible formats must be served via the transcoder endpoint
            if ext in BROWSER_INCOMPATIBLE_VIDEO_EXTS:
                video_url = f"/resource/video/{file_userid}/{device}/{rel_path}"
            else:
                video_url = f"/resource/image/{file_userid}/{device}/{rel_path}"

            result = {
                'id': photo_id,
                'thumbnail_url': f"/resource/thumbnail/{file_userid}/{safe_thumb}",
                'image_url': video_url,
                'type': media_type,
                'is_video': media_type == 'video'
            }
            
            return result
    except Exception:
        pass
    return None

# --- Global Sharing API ---

@app.route('/api/links/create', methods=['POST'])
def create_link():
    data = request.json
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    link_name = data.get('link_name', '').strip()
    if not link_name:
        return jsonify({'error': 'Link name is required'}), 400

    asset_id = data.get('asset_id')
    asset_type = data.get('asset_type') # 'photo', 'video', 'album'
    password = data.get('password')
    expiry_days = data.get('expiry_days')

    if not asset_id or not asset_type:
        return jsonify({'error': 'Missing asset_id or asset_type'}), 400
        
    if not password:
        return jsonify({'error': 'A password is required to create a shared link.'}), 400

    # Determine thumbnail ID
    thumbnail_id = None
    conn = database.get_db_connection(userid)
    c = conn.cursor()
    try:
        if asset_type in ('photo', 'video'):
            c.execute("SELECT id FROM photos WHERE id = ?", (asset_id,))
            if not c.fetchone():
                return jsonify({'error': 'Asset not found'}), 404
            thumbnail_id = asset_id
        elif asset_type == 'album':
            album_ids = [aid.strip() for aid in str(asset_id).split(',') if aid.strip().isdigit()]
            if not album_ids:
                return jsonify({'error': 'Invalid album ID(s)'}), 400
            c.execute("SELECT cover_photo_id FROM albums WHERE id = ?", (album_ids[0],))
            row = c.fetchone()
            if not row:
                return jsonify({'error': 'Album not found'}), 404
            thumbnail_id = row['cover_photo_id']
        else:
            return jsonify({'error': 'Invalid asset_type'}), 400
    finally:
        conn.close()

    # Generate link hash
    link_hash = secrets.token_urlsafe(16)

    # Hash password if provided
    password_hash = None
    salt = None
    if password:
        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256(f"{password}{salt}".encode()).hexdigest()

    # Calculate expiry
    expires_at = None
    if expiry_days:
        expires_at = (datetime.now() + timedelta(days=int(expiry_days))).strftime('%Y-%m-%d %H:%M:%S')

    # Save to global DB
    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        query = """INSERT INTO shared_links 
                   (link_hash, owner_email, asset_id, asset_type, thumbnail_id, password_hash, salt, expires_at, link_name) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        
        gc.execute(query, (link_hash, userid, asset_id, asset_type, thumbnail_id, password_hash, salt, expires_at, link_name))
        gconn.commit()
    except Exception as e:
        gconn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()

    return jsonify({'success': True, 'link_hash': link_hash})

@app.route('/api/links/list', methods=['GET'])
def list_links():
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
        
    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        gc.execute("""
            SELECT link_hash, asset_id, asset_type, expires_at, created_at,
                   link_name, (password_hash IS NOT NULL) as is_protected 
            FROM shared_links 
            WHERE owner_email = ? 
            ORDER BY created_at DESC
        """, (userid,))
        rows = gc.fetchall()
        links = [dict(r) for r in rows]
        # In sqlite3.Row, boolean might come back as 1/0
        for l in links:
            l['is_protected'] = bool(l['is_protected'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()
        
    return jsonify({'links': links})

@app.route('/api/links/revoke', methods=['POST'])
def revoke_link():
    data = request.json
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
        
    link_hash = data.get('link_hash')
    if not link_hash:
        return jsonify({'error': 'Missing link_hash'}), 400
        
    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        gc.execute("DELETE FROM shared_links WHERE link_hash = ? AND owner_email = ?", (link_hash, userid))
        gconn.commit()
    except Exception as e:
        gconn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()
        
    return jsonify({'success': True})

@app.route('/api/links/verify', methods=['POST'])
def verify_link():
    data = request.json
    link_hash = data.get('link_hash')
    password = data.get('password')
    
    if not link_hash:
        return jsonify({'error': 'Missing link_hash'}), 400
        
    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        gc.execute("""
            SELECT password_hash, salt, expires_at 
            FROM shared_links 
            WHERE link_hash = ?
        """, (link_hash,))
        row = gc.fetchone()
        
        if not row:
            return jsonify({'error': 'Link not found or revoked'}), 404
            
        # Check expiry manually to avoid timezone SQLite quirks just in case
        if row['expires_at']:
            gc.execute("SELECT datetime('now') < ?", (row['expires_at'],))
            is_valid = gc.fetchone()[0]
            if not is_valid:
                # Auto-cleanup expired
                gc.execute("DELETE FROM shared_links WHERE link_hash = ?", (link_hash,))
                gconn.commit()
                return jsonify({'error': 'Link expired'}), 410

        if row['password_hash']:
            if not password:
                return jsonify({'error': 'Password required', 'requires_password': True}), 401
            
            calc_hash = hashlib.sha256(f"{password}{row['salt']}".encode()).hexdigest()
            if calc_hash != row['password_hash']:
                return jsonify({'error': 'Invalid password'}), 401
        
        # Determine token for session viewing
        token = secrets.token_urlsafe(32)
        # We should store this token in a session/memory or DB.
        # But for this simple Phase 2, we can just return success and set a signed cookie.
        # The frontend will hit the resource URLs holding this cookie.
        
        resp = make_response(jsonify({'success': True, 'token': token}))
        # In a robust implementation we'd map token -> link_hash in DB to verify access.
        # For simplicity, we can sign the link_hash itself into a cookie.
        resp.set_cookie(f'link_auth_{link_hash}', link_hash, max_age=24 * 60 * 60, httponly=True, samesite='Lax')
        return resp
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()

@app.route('/s/<link_hash>', methods=['GET'])
def view_shared_link_page(link_hash):
    """Serves the frontend SPA for viewing a shared link."""
    return render_template('shared.html')

@app.route('/api/shared-with-me', methods=['GET'])
def get_shared_with_me():
    """Retrieve items shared specifically with the current user"""
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401

    gconn = get_global_share_db()
    gconn.row_factory = sqlite3.Row
    gc = gconn.cursor()
    try:
        # Get items shared with this user
        gc.execute("""
            SELECT owner_email, asset_id, asset_title, asset_type, thumbnail_id, created_at
            FROM shared_asset_users
            WHERE shared_with_email = ?
            ORDER BY created_at DESC
        """, (userid,))
        
        shared_items = [dict(row) for row in gc.fetchall()]
        
        # Enrich with thumbnail URLs and format album IDs
        for item in shared_items:
            owner_email = item['owner_email']
            thumbnail_id = item['thumbnail_id']
            
            if item['asset_type'] == 'album':
                item['asset_id'] = f"shared_{item['asset_id']}_{owner_email}"
            
            if thumbnail_id:
                try:
                    owner_conn = database.get_db_connection(owner_email)
                    owner_conn.row_factory = sqlite3.Row
                    oc = owner_conn.cursor()
                    oc.execute("SELECT path, type FROM photos WHERE id = ?", (thumbnail_id,))
                    thumb_photo = oc.fetchone()
                    owner_conn.close()
                    
                    if thumb_photo:
                        photo_data = build_photo_response(thumb_photo['path'], thumbnail_id, thumb_photo['type'], userid=owner_email)
                        if photo_data:
                            item['thumbnail_url'] = photo_data.get('thumbnail_url')
                except Exception as e:
                    print(f"Error fetching thumbnail for shared item: {e}")
                    
        return jsonify({'shared_assets': shared_items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()

@app.route('/api/links/view', methods=['GET'])
def view_link():
    """Retrieve the assets for a given link hash. Requires authentication via link_auth cookie if password protected."""
    link_hash = request.args.get('link_hash')
    if not link_hash:
        return jsonify({'error': 'Missing link_hash'}), 400

    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        gc.execute("""
            SELECT owner_email, asset_id, asset_type, password_hash, expires_at 
            FROM shared_links 
            WHERE link_hash = ?
        """, (link_hash,))
        link = gc.fetchone()
        
        if not link:
            return jsonify({'error': 'Link not found or revoked'}), 404
            
        # Check expiry
        if link['expires_at']:
            gc.execute("SELECT datetime('now') < ?", (link['expires_at'],))
            is_valid = gc.fetchone()[0]
            if not is_valid:
                gc.execute("DELETE FROM shared_links WHERE link_hash = ?", (link_hash,))
                gconn.commit()
                return jsonify({'error': 'Link expired'}), 410

        # Password-protected links require a valid link_auth cookie OR inline password
        if link['password_hash']:
            auth_cookie = request.cookies.get(f'link_auth_{link_hash}')
            password = request.args.get('password')
            if auth_cookie == link_hash:
                pass  # Cookie auth OK
            elif password:
                # Inline password verification fallback
                salt_row = gc.execute("SELECT salt FROM shared_links WHERE link_hash = ?", (link_hash,)).fetchone()
                calc_hash = hashlib.sha256(f"{password}{salt_row['salt']}".encode()).hexdigest()
                if calc_hash != link['password_hash']:
                    return jsonify({'requires_password': True, 'error': 'Invalid password'}), 401
            else:
                return jsonify({'requires_password': True, 'error': 'Password required'}), 401

        owner_email = link['owner_email']
        asset_id = link['asset_id']
        asset_type = link['asset_type']
        
        # Connect to owner's DB to fetch the actual asset data
        conn = database.get_db_connection(owner_email)
        c = conn.cursor()
        
        # Attach the global sharing database to this connection to allow cross-DB photo filtering
        c.execute("ATTACH DATABASE ? AS global_share", (GLOBAL_SHARE_DB_PATH,))
        
        result = {
            'asset_type': asset_type,
            'owner': owner_email, # Usually we'd look up their display name
        }
        
        try:
            if asset_type in ('photo', 'video'):
                # Check if this specific photo was hidden from the shared link
                gc.execute("SELECT 1 FROM shared_link_hidden_items WHERE link_hash = ? AND photo_id = ?", (link_hash, asset_id))
                if gc.fetchone():
                    return jsonify({'error': 'Asset no longer exists'}), 404

                c.execute("SELECT id, path, description, type, date_taken FROM photos WHERE id = ?", (asset_id,))
                row = c.fetchone()
                if not row:
                    return jsonify({'error': 'Asset no longer exists'}), 404
                    
                photo_data = build_photo_response(row['path'], row['id'], row['type'], userid=owner_email)
                if photo_data:
                    photo_data['description'] = row['description']
                    photo_data['date_taken'] = row['date_taken']
                    result['item'] = photo_data
                else:
                    return jsonify({'error': 'Failed to build asset'}), 500
                    
            elif asset_type == 'album':
                album_ids = [aid.strip() for aid in str(asset_id).split(',') if aid.strip().isdigit()]
                if not album_ids:
                    return jsonify({'error': 'Invalid album ID(s)'}), 400
                    
                if len(album_ids) == 1:
                    c.execute("SELECT name, description FROM albums WHERE id = ?", (album_ids[0],))
                    album_row = c.fetchone()
                    if not album_row:
                        return jsonify({'error': 'Album no longer exists'}), 404
                    result['album_name'] = album_row['name']
                    result['album_description'] = album_row['description']
                    
                    # Fetch album photos excluding hidden items
                    c.execute("""
                        SELECT p.id, p.path, p.description, p.type, p.date_taken
                        FROM photos p
                        JOIN album_photos ap ON p.id = ap.photo_id
                        WHERE ap.album_id = ? 
                        AND p.id NOT IN (
                            SELECT photo_id FROM global_share.shared_link_hidden_items WHERE link_hash = ?
                        )
                        ORDER BY ap.added_at DESC
                    """, (album_ids[0], link_hash))
                    
                    photos = []
                    for pr in c.fetchall():
                        pd = build_photo_response(pr['path'], pr['id'], pr['type'], userid=owner_email)
                        if pd:
                            pd['description'] = pr['description']
                            pd['date_taken'] = pr['date_taken']
                            photos.append(pd)
                            
                    result['items'] = photos
                else:
                    result['is_multi_album'] = True
                    result['album_name'] = f"{len(album_ids)} Shared Albums"
                    result['album_description'] = "A collection of multiple shared albums"
                    
                    albums_data = []
                    for aid in album_ids:
                        c.execute("SELECT name, description FROM albums WHERE id = ?", (aid,))
                        arow = c.fetchone()
                        if arow:
                            album_info = {
                                'name': arow['name'],
                                'description': arow['description'],
                                'items': []
                            }
                            c.execute("""
                                SELECT p.id, p.path, p.description, p.type, p.date_taken
                                FROM photos p
                                JOIN album_photos ap ON p.id = ap.photo_id
                                WHERE ap.album_id = ?
                                AND p.id NOT IN (
                                    SELECT photo_id FROM global_share.shared_link_hidden_items WHERE link_hash = ?
                                )
                                ORDER BY ap.added_at DESC
                            """, (aid, link_hash))
                            
                            for pr in c.fetchall():
                                pd = build_photo_response(pr['path'], pr['id'], pr['type'], userid=owner_email)
                                if pd:
                                    pd['description'] = pr['description']
                                    pd['date_taken'] = pr['date_taken']
                                    album_info['items'].append(pd)
                            
                            albums_data.append(album_info)
                            
                    result['albums'] = albums_data
                
        finally:
            conn.close()
            
        resp = make_response(jsonify(result))
        # Set cookie universally for authenticated access
        resp.set_cookie(f'link_auth_{link_hash}', link_hash, max_age=24 * 60 * 60, httponly=True, samesite='Lax')
        return resp
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()

@app.route('/api/links/hide', methods=['POST'])
def hide_shared_asset():
    """Allows a viewer to hide a specific photo from a shared link view by providing the link's password."""
    data = request.json
    link_hash = data.get('link_hash')
    photo_id = data.get('photo_id')
    password = data.get('password')

    if not link_hash or not photo_id or not password:
        return jsonify({'error': 'Missing required fields: link_hash, photo_id, password'}), 400

    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        # Fetch the shared link's password hash and salt
        gc.execute("SELECT password_hash, salt FROM shared_links WHERE link_hash = ?", (link_hash,))
        link = gc.fetchone()

        if not link:
            return jsonify({'error': 'Link not found or revoked'}), 404
            
        if not link['password_hash']:
            return jsonify({'error': 'This shared link does not have a password configured for deletions'}), 400

        # Validate the provided password
        calc_hash = hashlib.sha256(f"{password}{link['salt']}".encode()).hexdigest()
        if calc_hash != link['password_hash']:
            return jsonify({'error': 'Invalid password for this shared link'}), 401

        # Password is correct; mark the item as hidden for this specific link
        gc.execute(
            "INSERT OR IGNORE INTO shared_link_hidden_items (link_hash, photo_id) VALUES (?, ?)", 
            (link_hash, photo_id)
        )
        gconn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        gconn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        gconn.close()

@app.route('/api/links/download_zip', methods=['GET'])
def download_shared_zip():
    """Download all visible photos in a shared link as a single ZIP file."""
    link_hash = request.args.get('link_hash')
    if not link_hash:
        return "Missing link_hash", 400

    gconn = get_global_share_db()
    gc = gconn.cursor()
    try:
        gc.execute("""
            SELECT owner_email, asset_id, asset_type, expires_at, link_name
            FROM shared_links 
            WHERE link_hash = ?
        """, (link_hash,))
        link = gc.fetchone()
        
        if not link:
            return "Link not found or revoked", 404
            
        if link['expires_at']:
            gc.execute("SELECT datetime('now') < ?", (link['expires_at'],))
            if not gc.fetchone()[0]:
                return "Link expired", 410

        owner_email = link['owner_email']
        asset_id = link['asset_id']
        asset_type = link['asset_type']
        zip_name = link['link_name'] or "shared_photos"
        
        # Connect to owner's DB to fetch the actual asset data
        conn = database.get_db_connection(owner_email)
        c = conn.cursor()
        c.execute("ATTACH DATABASE ? AS global_share", (GLOBAL_SHARE_DB_PATH,))
        
        paths_to_zip = []
        
        try:
            if asset_type in ('photo', 'video'):
                # Check if this specific photo was hidden
                gc.execute("SELECT 1 FROM shared_link_hidden_items WHERE link_hash = ? AND photo_id = ?", (link_hash, asset_id))
                if not gc.fetchone():
                    c.execute("SELECT path FROM photos WHERE id = ?", (asset_id,))
                    row = c.fetchone()
                    if row:
                        paths_to_zip.append(row['path'])
                        
            elif asset_type == 'album':
                album_ids = [aid.strip() for aid in str(asset_id).split(',') if aid.strip().isdigit()]
                for aid in album_ids:
                    c.execute("""
                        SELECT p.path
                        FROM photos p
                        JOIN album_photos ap ON p.id = ap.photo_id
                        WHERE ap.album_id = ? 
                        AND p.id NOT IN (
                            SELECT photo_id FROM global_share.shared_link_hidden_items WHERE link_hash = ?
                        )
                    """, (aid, link_hash))
                    for row in c.fetchall():
                        paths_to_zip.append(row['path'])
        finally:
            conn.close()
            
        if not paths_to_zip:
            return "No photos available to download", 404
            
        import io, zipfile
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for rel_path in paths_to_zip:
                abs_path = os.path.join(DATA_DIR, owner_email, rel_path)
                if os.path.exists(abs_path):
                    # Write file into the zip root using its basename
                    zf.write(abs_path, os.path.basename(abs_path))
                    
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{zip_name}.zip"
        )
        
    except Exception as e:
        return str(e), 500
    finally:
        gconn.close()

# --- Config API ---
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            print("Error loading config:", e)
    return {"port": 8877, "ai": "YES", "search": "YES", "people": "YES", "discover": "YES"}

def save_config(config_data):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config_data, f, indent=2)
        return True
    except Exception as e:
        print("Error saving config:", e)
        return False

@app.route('/api/config', methods=['GET'])
def get_config_endpoint():
    config = load_config()
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config_endpoint():
    userid = get_current_userid()
    if not userid:
        return jsonify({'error': 'Unauthorized'}), 401
    
    config_data = request.json
    current_config = load_config()
    # Update only allowed keys
    for k in ["port", "ai", "search", "people", "discover"]:
        if k in config_data:
            current_config[k] = config_data[k]
            
    if save_config(current_config):
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to save config'}), 500

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    cfg = load_config()
    port = int(cfg.get('port', 8877))
    app.run(debug=False, port=port, host='0.0.0.0')
