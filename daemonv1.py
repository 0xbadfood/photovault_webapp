import os
import time
import sqlite3
import database
from PIL import Image
import traceback
from threading import Thread

# Optional imports for AI - handled gracefully if missing during install
try:
    import face_recognition
    import numpy as np
    AI_AVAILABLE = True
except ImportError:
    print("AI libraries not yet installed. Running in limited mode.")
    AI_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

def get_user_dir(userid):
    return os.path.join(DATA_DIR, userid)

def get_thumbnail_dir(userid):
    return os.path.join(get_user_dir(userid), 'thumbnails')

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts'}

def is_image_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def is_video_file(filename):
    return filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts'))

def determine_image_type(filename, exif_data=None):
    """
    Determine if an image is likely a screenshot or camera photo.
    """
    fname_lower = filename.lower()
    
    if 'screenshot' in fname_lower or 'screen shot' in fname_lower:
        return 'screenshot'
        
    if exif_data:
        if 271 in exif_data or 272 in exif_data:
            return 'photo'
            
    return 'screenshot'

def extract_date_from_filename(filename):
    """
    Try to extract date from filename using common patterns.
    Returns ISO format date string or None.
    """
    import re
    from datetime import datetime
    
    # Pattern 1: YYYYMMDD_HHMMSS
    match = re.search(r'(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 
                            int(match.group(4)), int(match.group(5)), int(match.group(6))).isoformat()
        except ValueError:
            pass

    # Pattern 2: YYYY-MM-DD
    match = re.search(r'(20\d{2})-(\d{2})-(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass

    # Pattern 3: YYYYMMDD
    match = re.search(r'(20\d{2})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass
            
    return None

def generate_video_thumbnail(video_path, thumb_path):
    try:
        cmd = [
            'ffmpeg', '-y', 
            '-i', video_path, 
            '-ss', '00:00:01.000', 
            '-vframes', '1', 
            thumb_path
        ]
        import subprocess
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception as e:
        print(f"Error generating video thumbnail for {video_path}: {e}")
        return False

def get_db_connection_wal(userid):
    """Get a DB connection with WAL mode for better concurrency."""
    conn = database.get_db_connection(userid)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s if DB is locked
    return conn


# ===========================================================================
# THREAD 1: SCANNER — Fast path (thumbnails + EXIF)
# ===========================================================================

def scan_and_thumbnail():
    """
    Thread 1 — Fast path.
    Discovers new files, generates thumbnails, extracts EXIF.
    This ensures users see thumbnails as quickly as possible.
    """
    print("[Scanner] Starting scan...")
    
    if not os.path.exists(DATA_DIR):
        print("[Scanner] Data directory not found, skipping.")
        return

    for userid in os.listdir(DATA_DIR):
        user_path = os.path.join(DATA_DIR, userid)
        if not os.path.isdir(user_path):
            continue
        
        database.init_db(userid)
        conn = get_db_connection_wal(userid)
        c = conn.cursor()
        
        thumb_dir = get_thumbnail_dir(userid)
        os.makedirs(thumb_dir, exist_ok=True)
        
        try:
            # Scan devices
            for device in os.listdir(user_path):
                device_path = os.path.join(user_path, device)
                if not os.path.isdir(device_path) or device == 'thumbnails':
                    continue
                
                files_dir = os.path.join(device_path, 'files')
                if not os.path.exists(files_dir):
                    continue
                
                for root, _, filenames in os.walk(files_dir):
                    for filename in filenames:
                        if not is_image_file(filename):
                            continue
                        
                        full_path = os.path.join(root, filename)
                        
                        # Skip symlinks (shared files)
                        if os.path.islink(full_path):
                            continue
                        
                        # Check if exists in DB
                        c.execute(
                            "SELECT id, processed_for_thumbnails, processed_for_exif, type FROM photos WHERE path = ?",
                            (full_path,)
                        )
                        row = c.fetchone()
                        
                        photo_id = None
                        if not row:
                            print(f"[Scanner] New file found: {filename}")
                            c.execute("INSERT INTO photos (path) VALUES (?)", (full_path,))
                            conn.commit()
                            photo_id = c.lastrowid
                            processed_thumb = False
                            processed_exif = False
                            current_type = None
                        else:
                            photo_id = row['id']
                            processed_thumb = row['processed_for_thumbnails']
                            processed_exif = row['processed_for_exif']
                            current_type = row['type']
                        
                        # --- Task 1: Thumbnails ---
                        if not processed_thumb:
                            try:
                                rel_from_files = os.path.relpath(full_path, files_dir)
                                safe_base = rel_from_files.replace(os.path.sep, '_')
                                
                                if safe_base.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts')):
                                    safe_name = f"{device}__{safe_base}.jpg"
                                    if safe_base.lower().endswith('.jpg'):
                                        safe_name = f"{device}__{safe_base}"
                                else:
                                    safe_name = f"{device}__{safe_base}.jpg"
                                
                                thumb_out = os.path.join(thumb_dir, safe_name)
                                
                                if not os.path.exists(thumb_out):
                                    if is_video_file(filename):
                                        generate_video_thumbnail(full_path, thumb_out)
                                    else:
                                        with Image.open(full_path) as img:
                                            img.thumbnail((300, 300))
                                            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                                                img = img.convert('RGB')
                                            img.save(thumb_out, 'JPEG', quality=80)
                                
                                c.execute("UPDATE photos SET processed_for_thumbnails = 1 WHERE id = ?", (photo_id,))
                                conn.commit()
                                print(f"[Scanner] Thumbnail done: {filename}")
                            except Exception as e:
                                print(f"[Scanner] Thumbnail error {filename}: {e} — marking as unprocessable")
                                # Mark as processed so we don't retry every cycle
                                c.execute("""UPDATE photos SET 
                                    processed_for_thumbnails = 1, 
                                    processed_for_exif = 1, 
                                    processed_for_faces = 1,
                                    type = 'unidentifiable'
                                    WHERE id = ?""", (photo_id,))
                                conn.commit()
                        
                        # --- Task 2: EXIF Extraction ---
                        if not is_video_file(filename) and (not processed_exif or current_type is None):
                            new_type = process_exif(conn, photo_id, full_path)
                            if new_type:
                                current_type = new_type
                        
                        # --- Handle Videos (type + date) ---
                        if is_video_file(filename):
                            if not processed_exif:
                                video_date = extract_date_from_filename(filename)
                                if video_date:
                                    print(f"[Scanner] Video date extracted: {filename} -> {video_date}")
                                    c.execute("UPDATE photos SET date_taken = ?, processed_for_exif = 1 WHERE id = ?", 
                                              (video_date, photo_id))
                                else:
                                    c.execute("UPDATE photos SET processed_for_exif = 1 WHERE id = ?", (photo_id,))
                            
                            if current_type != 'video':
                                c.execute("UPDATE photos SET type = 'video' WHERE id = ?", (photo_id,))
                            
                            # Mark faces as done for videos (we skip AI for videos)
                            c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ? AND processed_for_faces = 0", 
                                      (photo_id,))
                            conn.commit()
                            continue
                        
                        # Mark screenshots as done for faces (we skip AI for screenshots)
                        if current_type == 'screenshot':
                            c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ? AND processed_for_faces = 0", 
                                      (photo_id,))
                            # Set description placeholder for screenshots
                            c.execute("UPDATE photos SET description = 'Screenshot' WHERE id = ? AND description IS NULL", 
                                      (photo_id,))
                            conn.commit()
        
        except Exception as e:
            print(f"[Scanner] Error processing user {userid}: {e}")
            traceback.print_exc()
        finally:
            conn.close()
    
    print("[Scanner] Scan complete.")


# ===========================================================================
# EXIF Processing (used by Thread 1)
# ===========================================================================

def process_exif(conn, photo_id, image_path):
    """Extract EXIF metadata from image (date taken, GPS coordinates)"""
    try:
        from datetime import datetime
        
        with Image.open(image_path) as img:
            exif = img.getexif()
            
            if not exif:
                image_type = determine_image_type(os.path.basename(image_path), None)
                c = conn.cursor()
                c.execute("UPDATE photos SET processed_for_exif = 1, type = ? WHERE id = ?", (image_type, photo_id))
                conn.commit()
                return image_type
            
            date_taken = None
            location_lat = None
            location_lon = None
            
            # Extract DateTimeOriginal (tag 36867) or DateTime (tag 306)
            if 36867 in exif:
                date_str = exif[36867]
                try:
                    date_taken = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            elif 306 in exif:
                date_str = exif[306]
                try:
                    date_taken = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            
            # Fallback to filename
            if not date_taken:
                date_taken = extract_date_from_filename(os.path.basename(image_path))
            
            # Extract GPS coordinates (tag 34853)
            if 34853 in exif:
                gps_info = exif[34853]
                if isinstance(gps_info, dict):
                    if 2 in gps_info and 4 in gps_info:
                        lat = gps_info[2]
                        lon = gps_info[4]
                        lat_ref = gps_info.get(1, 'N')
                        lon_ref = gps_info.get(3, 'E')
                        
                        def dms_to_decimal(dms, ref):
                            if isinstance(dms, (tuple, list)) and len(dms) == 3:
                                degrees = float(dms[0])
                                minutes = float(dms[1])
                                seconds = float(dms[2])
                                decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
                                if ref in ['S', 'W']:
                                    decimal = -decimal
                                return decimal
                            return None
                        
                        location_lat = dms_to_decimal(lat, lat_ref)
                        location_lon = dms_to_decimal(lon, lon_ref)
            
            # Determine type
            image_type = determine_image_type(os.path.basename(image_path), exif)
            
            c = conn.cursor()
            c.execute(
                "UPDATE photos SET date_taken = ?, location_lat = ?, location_lon = ?, processed_for_exif = 1, type = ? WHERE id = ?",
                (date_taken, location_lat, location_lon, image_type, photo_id)
            )
            conn.commit()
            
            if date_taken:
                print(f"[Scanner] EXIF: {os.path.basename(image_path)}: date={date_taken}, type={image_type}")
                
            return image_type
            
    except Exception as e:
        print(f"[Scanner] EXIF error for {image_path}: {e}")
        try:
            image_type = determine_image_type(os.path.basename(image_path), None)
            date_guess = extract_date_from_filename(os.path.basename(image_path))
            c = conn.cursor()
            c.execute("UPDATE photos SET processed_for_exif = 1, type = ?, date_taken = ? WHERE id = ?", 
                      (image_type, date_guess, photo_id))
            conn.commit()
            return image_type
        except:
            c = conn.cursor()
            c.execute("UPDATE photos SET processed_for_exif = 1 WHERE id = ?", (photo_id,))
            conn.commit()
            return None


# ===========================================================================
# THREAD 2: AI WORKER — Slow path (face recognition + descriptions)
# ===========================================================================

# Global model cache
_MOBILENET_MODEL = None
_TF_IMPORTED = False

try:
    import tensorflow as tf
    from tensorflow.keras.applications.mobilenet_v2 import MobileNetV2, preprocess_input, decode_predictions
    from tensorflow.keras.preprocessing import image as keras_image
    _TF_IMPORTED = True
except ImportError:
    print("TensorFlow not available. Description generation disabled.")

def get_model():
    global _MOBILENET_MODEL
    if _MOBILENET_MODEL is None and _TF_IMPORTED:
        print("[AI Worker] Loading MobileNetV2 model...")
        _MOBILENET_MODEL = MobileNetV2(weights='imagenet')
    return _MOBILENET_MODEL


def ai_process():
    """
    Thread 2 — Slow path.
    Queries for photos that have thumbnails + EXIF done but still need
    face recognition or description generation.
    """
    print("[AI Worker] Starting AI processing...")
    
    if not os.path.exists(DATA_DIR):
        return

    for userid in os.listdir(DATA_DIR):
        user_path = os.path.join(DATA_DIR, userid)
        if not os.path.isdir(user_path):
            continue
        
        database.init_db(userid)
        conn = get_db_connection_wal(userid)
        c = conn.cursor()
        
        try:
            # ---- PASS 1: Face Recognition ----
            # Query photos ready for face processing
            c.execute("""
                SELECT id, path, type FROM photos
                WHERE processed_for_thumbnails = 1
                  AND processed_for_exif = 1
                  AND processed_for_faces = 0
            """)
            pending_faces = c.fetchall()
            
            for row in pending_faces:
                photo_id = row['id']
                image_path = row['path']
                photo_type = row['type']
                
                # Skip videos and screenshots for face recognition
                if photo_type in ('video', 'screenshot'):
                    c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                    conn.commit()
                    continue
                
                # Verify file still exists
                if not os.path.exists(image_path):
                    print(f"[AI Worker] File missing, skipping: {image_path}")
                    c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                    conn.commit()
                    continue
                
                if AI_AVAILABLE:
                    process_faces(conn, photo_id, image_path, userid)
                else:
                    c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                    conn.commit()
            
            # ---- PASS 2: Description Generation ----
            c.execute("""
                SELECT id, path, type FROM photos
                WHERE processed_for_thumbnails = 1
                  AND processed_for_exif = 1
                  AND description IS NULL
            """)
            pending_desc = c.fetchall()
            
            for row in pending_desc:
                photo_id = row['id']
                image_path = row['path']
                photo_type = row['type']
                
                # Skip videos
                if photo_type == 'video':
                    continue
                
                # Screenshots get a placeholder
                if photo_type == 'screenshot':
                    c.execute("UPDATE photos SET description = 'Screenshot' WHERE id = ?", (photo_id,))
                    conn.commit()
                    continue
                
                # Verify file still exists
                if not os.path.exists(image_path):
                    continue
                
                process_description(conn, photo_id, image_path)
        
        except Exception as e:
            print(f"[AI Worker] Error processing user {userid}: {e}")
            traceback.print_exc()
        finally:
            conn.close()
    
    print("[AI Worker] AI processing complete.")


# ===========================================================================
# AI Processing Functions (used by Thread 2)
# ===========================================================================

def process_description(conn, photo_id, image_path):
    if not _TF_IMPORTED:
        return

    try:
        model = get_model()
        if not model:
            return

        print(f"[AI Worker] Generating description for {os.path.basename(image_path)}...")
        
        img = keras_image.load_img(image_path, target_size=(224, 224))
        x = keras_image.img_to_array(img)
        x = np.expand_dims(x, axis=0)
        x = preprocess_input(x)

        preds = model.predict(x, verbose=0)
        decoded = decode_predictions(preds, top=3)[0]
        
        tags = [d[1] for d in decoded]
        description = ", ".join(tags)
        
        print(f"[AI Worker] Description: {description}")
        
        c = conn.cursor()
        c.execute("UPDATE photos SET description = ? WHERE id = ?", (description, photo_id))
        conn.commit()

    except Exception as e:
        print(f"[AI Worker] Description error: {e}")


def save_face_crop(image_path, face_location, thumb_dir):
    try:
        top, right, bottom, left = face_location
        
        height = bottom - top
        width = right - left
        pad_h = int(height * 0.4)
        pad_w = int(width * 0.4)
        
        with Image.open(image_path) as img:
            img_w, img_h = img.size
            
            new_left = max(0, left - pad_w)
            new_top = max(0, top - pad_h)
            new_right = min(img_w, right + pad_w)
            new_bottom = min(img_h, bottom + pad_h)
            
            face_img = img.crop((new_left, new_top, new_right, new_bottom))
            face_img.thumbnail((500, 500))
            
            import uuid
            safe_name = f"face_{uuid.uuid4().hex[:8]}.jpg"
            out_path = os.path.join(thumb_dir, safe_name)
            
            if face_img.mode in ('RGBA', 'LA') or (face_img.mode == 'P' and 'transparency' in face_img.info):
                face_img = face_img.convert('RGB')
            
            face_img.save(out_path, 'JPEG', quality=90)
            return out_path
    except Exception as e:
        print(f"[AI Worker] Error saving face crop: {e}")
        return None


def process_faces(conn, photo_id, image_path, userid):
    try:
        print(f"[AI Worker] Processing faces for {os.path.basename(image_path)}...")
        image = face_recognition.load_image_file(image_path)
        
        face_locations = face_recognition.face_locations(image, number_of_times_to_upsample=1, model="hog") 
        if not face_locations:
            c = conn.cursor()
            c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
            conn.commit()
            return

        face_encodings = face_recognition.face_encodings(image, face_locations)
        
        c = conn.cursor()
        
        c.execute("SELECT id, embedding_blob FROM people")
        known_people = c.fetchall()
        known_encodings = []
        known_ids = []
        for p in known_people:
            if p['embedding_blob']:
                known_encodings.append(database.convert_array(p['embedding_blob']))
                known_ids.append(p['id'])
        
        thumb_dir = get_thumbnail_dir(userid)
        
        for i, encoding in enumerate(face_encodings):
            matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=0.6)
            face_distances = face_recognition.face_distance(known_encodings, encoding)
            
            p_id = None
            if len(known_encodings) > 0:
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    p_id = known_ids[best_match_index]
            
            if p_id is None:
                print("[AI Worker] New person found!")
                
                face_thumb_path = save_face_crop(image_path, face_locations[i], thumb_dir)
                
                c.execute("INSERT INTO people (embedding_blob, thumbnail_path) VALUES (?, ?)", 
                          (database.adapt_array(encoding), face_thumb_path))
                p_id = c.lastrowid
                
                known_encodings.append(encoding)
                known_ids.append(p_id)
            
            try:
                c.execute("INSERT INTO photo_people (photo_id, person_id) VALUES (?, ?)", (photo_id, p_id))
            except sqlite3.IntegrityError:
                pass
        
        c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
        conn.commit()
    except Exception as e:
        print(f"[AI Worker] Face processing error: {e}")
        traceback.print_exc()


# ===========================================================================
# THREAD LOOP WRAPPERS
# ===========================================================================

def scanner_loop():
    """Thread 1 loop — runs scan_and_thumbnail every 15 seconds."""
    print("[Scanner] Thread started.")
    while True:
        try:
            scan_and_thumbnail()
        except Exception as e:
            print(f"[Scanner] Crashed: {e}")
            traceback.print_exc()
        time.sleep(15)


def ai_worker_loop():
    """Thread 2 loop — runs ai_process every 30 seconds."""
    # Give the scanner a head start on first boot
    print("[AI Worker] Thread started. Waiting 10s for scanner head start...")
    time.sleep(10)
    
    while True:
        try:
            ai_process()
        except Exception as e:
            print(f"[AI Worker] Crashed: {e}")
            traceback.print_exc()
        time.sleep(30)


# ===========================================================================
# MAIN ENTRY POINT
# ===========================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  PhotoVault Daemon v1 — Two-Thread Architecture")
    print("  Thread 1: Scanner (thumbnails + EXIF) — every 15s")
    print("  Thread 2: AI Worker (faces + descriptions) — every 30s")
    print("=" * 60)
    
    scanner_thread = Thread(target=scanner_loop, daemon=True, name="Scanner")
    ai_thread = Thread(target=ai_worker_loop, daemon=True, name="AI-Worker")
    
    scanner_thread.start()
    ai_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nShutting down daemon...")
