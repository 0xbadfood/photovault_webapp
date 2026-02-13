import os
import time
import sqlite3
import database
from PIL import Image
import traceback

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

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.mp4', '.mov', '.avi', '.mkv'}

def is_image_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def is_video_file(filename):
    return filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv'))

def determine_image_type(filename, exif_data=None):
    """
    Determine if an image is likely a screenshot or camera photo.
    Heuristics:
    1. 'Screenshot' in filename
    2. PNG format + No EXIF camera model
    3. Explicit EXIF 'UserComment' or similar indicating screenshot (rare)
    
    Returns: 'screenshot', 'photo', or None (uncertain/default)
    """
    fname_lower = filename.lower()
    
    # 1. Filename check
    if 'screenshot' in fname_lower or 'screen shot' in fname_lower:
        return 'screenshot'
        
    # 2. EXIF check
    # If we have EXIF data with Make/Model, it's likely a photo
    if exif_data:
        # 271: Make, 272: Model
        if 271 in exif_data or 272 in exif_data:
            return 'photo'
            
    # 3. No EXIF or no Camera details -> Treat as Screenshot/Other
    # User requested: "move all pictures with no exif data to screenshots"
    return 'screenshot'

def extract_date_from_filename(filename):
    """
    Try to extract date from filename using common patterns.
    Returns ISO format date string or None.
    """
    import re
    from datetime import datetime
    
    # Common patterns:
    # 1. YYYYMMDD_HHMMSS (e.g. IMG_20230101_120000.jpg, 20230101_120000.mp4)
    # 2. YYYY-MM-DD (e.g. Screenshot_2023-01-01...)
    # 3. YYYYMMDD (e.g. PXL_20230101...)
    # 4. Unix Timestamp (rarely in filenames but possible, usually excessive digits) -> Skip for now as risky
    
    # Pattern 1: YYYYMMDD_HHMMSS
    match = re.search(r'(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 
                            int(match.group(4)), int(match.group(5)), int(match.group(6))).isoformat()
        except ValueError:
            pass

    # Pattern 2: YYYY-MM-DD (common in screenshots)
    match = re.search(r'(20\d{2})-(\d{2})-(\d{2})', filename)
    if match:
        try:
            # Default time to 00:00:00? Or 12:00:00?
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass

    # Pattern 3: YYYYMMDD (no time)
    match = re.search(r'(20\d{2})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass # Invalid date components
            
    return None

def generate_video_thumbnail(video_path, thumb_path):
    try:
        # Extract frame at 1s
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

def scan_and_process():
    print("Starting scan...")
    
    # 1. Walk through all user directories
    if os.path.exists(DATA_DIR):
        for userid in os.listdir(DATA_DIR):
            user_path = os.path.join(DATA_DIR, userid)
            if not os.path.isdir(user_path): continue
            
            # Per-user DB connection
            database.init_db(userid)
            conn = database.get_db_connection(userid)
            c = conn.cursor()
            
            thumb_dir = get_thumbnail_dir(userid)
            os.makedirs(thumb_dir, exist_ok=True)
            
            # Scan devices
            for device in os.listdir(user_path):
                device_path = os.path.join(user_path, device)
                if not os.path.isdir(device_path) or device == 'thumbnails': continue
                
                files_dir = os.path.join(device_path, 'files')
                if not os.path.exists(files_dir): continue
                
                for root, _, filenames in os.walk(files_dir):
                    for filename in filenames:
                        if not is_image_file(filename): continue
                        
                        full_path = os.path.join(root, filename)
                        
                        # Skip symlinks (shared files) — they are managed by the share system
                        if os.path.islink(full_path):
                            continue
                        
                        rel_path = os.path.relpath(full_path, user_path) # key for DB? Or full path? 
                        # Let's use full absolute path for uniqueness in this local setup
                        
                        # Check if exists in DB
                        c.execute("SELECT id, processed_for_thumbnails, processed_for_faces, processed_for_exif, description, type FROM photos WHERE path = ?", (full_path,))
                        row = c.fetchone()
                        
                        photo_id = None
                        if not row:
                            print(f"New file found: {filename}")
                            c.execute("INSERT INTO photos (path) VALUES (?)", (full_path,))
                            conn.commit()
                            photo_id = c.lastrowid
                            processed_thumb = False
                            processed_faces = False
                            processed_exif = False
                            has_description = False
                            current_type = None
                        else:
                            photo_id = row['id']
                            processed_thumb = row['processed_for_thumbnails']
                            processed_faces = row['processed_for_faces']
                            processed_exif = row['processed_for_exif']
                            has_description = True if row['description'] else False
                            current_type = row['type']
                        
                        # Task 1: Thumbnails
                        if not processed_thumb:
                            try:
                                # Generate thumbnail name: device__relpath
                                rel_from_files = os.path.relpath(full_path, files_dir)
                                
                                safe_base = rel_from_files.replace(os.path.sep, '_')
                                if safe_base.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.avi', '.mkv')):
                                    # Simplest: Just append .jpg to the unique base name for videos too, or replace ext?
                                    # If file is "video.mp4", base is "device__video.mp4"
                                    # thumb is "device__video.mp4.jpg" 
                                    safe_name = f"{device}__{safe_base}.jpg"
                                    if safe_base.lower().endswith('.jpg'):
                                        safe_name = f"{device}__{safe_base}"
                                else:
                                    safe_name = f"{device}__{safe_base}.jpg"
                                
                                thumb_out = os.path.join(thumb_dir, safe_name)
                                
                                # Generate
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
                            except Exception as e:
                                print(f"Thumbnail error {filename}: {e}")

                        # Task 2: EXIF Extraction (for images only)
                        # We also check if type is None, because if it is, we should try to determine it again
                        if not is_video_file(filename) and (not processed_exif or current_type is None):
                            new_type = process_exif(conn, photo_id, full_path)
                            if new_type:
                                current_type = new_type
                        
                        # Skip AI for videos for now
                        if is_video_file(filename):
                            if not processed_faces:
                                c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                            
                            if not processed_exif:
                                # Extract date from filename for videos
                                video_date = extract_date_from_filename(filename)
                                if video_date:
                                    print(f"Video date extracted: {filename} -> {video_date}")
                                    c.execute("UPDATE photos SET date_taken = ?, processed_for_exif = 1 WHERE id = ?", (video_date, photo_id))
                                else:
                                    # Mark process even if no date found so we don't loop forever
                                    c.execute("UPDATE photos SET processed_for_exif = 1 WHERE id = ?", (photo_id,))
                            
                            # Also update type to 'video' for videos if not set
                            if current_type != 'video':
                                c.execute("UPDATE photos SET type = 'video' WHERE id = ?", (photo_id,))
                                
                            conn.commit()
                            continue

                        # Task 3: Face Recognition
                        # Skip for screenshots
                        if current_type == 'screenshot':
                            if not processed_faces:
                                c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                            if not processed_faces: # Just reusing the logic block, essentially we want to ensure it is marked processed
                                pass
                        elif AI_AVAILABLE and not processed_faces:
                            process_faces(conn, photo_id, full_path, userid)
                        
                        # Task 4: Description Generation
                        # Skip for screenshots
                        if current_type == 'screenshot':
                             if not has_description:
                                  # We don't have a 'processed_for_description' flag, we just check if description is NULL/Empty.
                                  # To avoid re-checking, we should probably just leave it empty or set a placeholder?
                                  # But the check is `if not has_description`.
                                  # If we do nothing, it will keep checking.
                                  # We should probably set a placeholder like "Screenshot" or just ignore?
                                  # Daemon loops forever. We need a way to say "don't try again".
                                  # The current schema doesn't have `processed_for_description`. It relies on description field.
                                  # Let's set description to 'Screenshot' or empty string? 
                                  # If empty string, `has_description` (row['description']) might be falsy?
                                  # row['description'] is False if None or Empty string.
                                  # Let's set it to "Screenshot".
                                  c.execute("UPDATE photos SET description = 'Screenshot' WHERE id = ?", (photo_id,))
                                  conn.commit()
                        elif not has_description:
                             process_description(conn, photo_id, full_path)

            conn.close()
    print("Scan complete.")

def process_exif(conn, photo_id, image_path):
    """Extract EXIF metadata from image (date taken, GPS coordinates)"""
    try:
        from datetime import datetime
        
        with Image.open(image_path) as img:
            exif = img.getexif()
            
            if not exif:
                # No EXIF data, mark as processed and guess type
                image_type = determine_image_type(os.path.basename(image_path), None)
                c = conn.cursor()
                c.execute("UPDATE photos SET processed_for_exif = 1, type = ? WHERE id = ?", (image_type, photo_id))
                conn.commit()
                return image_type
            
            date_taken = None
            location_lat = None
            location_lon = None
            
            # Extract DateTimeOriginal (tag 36867) or DateTime (tag 306)
            if 36867 in exif:  # DateTimeOriginal
                date_str = exif[36867]
                try:
                    # EXIF date format: "YYYY:MM:DD HH:MM:SS"
                    date_taken = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            elif 306 in exif:  # DateTime
                date_str = exif[306]
                try:
                    date_taken = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            
            # Fallback to filename if no EXIF date
            if not date_taken:
                date_taken = extract_date_from_filename(os.path.basename(image_path))
            
            # Extract GPS coordinates (tag 34853)
            if 34853 in exif:  # GPSInfo
                gps_info = exif[34853]
                if isinstance(gps_info, dict):
                    # GPS tags: 1=N/S, 2=Latitude, 3=E/W, 4=Longitude
                    if 2 in gps_info and 4 in gps_info:
                        lat = gps_info[2]
                        lon = gps_info[4]
                        lat_ref = gps_info.get(1, 'N')
                        lon_ref = gps_info.get(3, 'E')
                        
                        # Convert from degrees/minutes/seconds to decimal
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
            
            # Determine type based on EXIF
            image_type = determine_image_type(os.path.basename(image_path), exif)
            
            # Update database
            c = conn.cursor()
            c.execute(
                "UPDATE photos SET date_taken = ?, location_lat = ?, location_lon = ?, processed_for_exif = 1, type = ? WHERE id = ?",
                (date_taken, location_lat, location_lon, image_type, photo_id)
            )
            conn.commit()
            
            if date_taken:
                print(f"EXIF extracted for {image_path}: date={date_taken}, type={image_type}")
                
            return image_type
            
    except Exception as e:
        print(f"EXIF extraction error for {image_path}: {e}")
        # Mark as processed even on error to avoid retrying
        # Try to determine type just by filename if EXIF failed
        try:
             image_type = determine_image_type(os.path.basename(image_path), None)
             
             # Also try date from filename!
             date_guess = extract_date_from_filename(os.path.basename(image_path))
             c = conn.cursor()
             c.execute("UPDATE photos SET processed_for_exif = 1, type = ?, date_taken = ? WHERE id = ?", (image_type, date_guess, photo_id))
             conn.commit()
             return image_type
        except:
             c = conn.cursor()
             c.execute("UPDATE photos SET processed_for_exif = 1 WHERE id = ?", (photo_id,))
             conn.commit()
             return None

# Global model cache to avoid reloading
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
        print("Loading MobileNetV2 model...")
        _MOBILENET_MODEL = MobileNetV2(weights='imagenet')
    return _MOBILENET_MODEL

def process_description(conn, photo_id, image_path):
    if not _TF_IMPORTED:
        return

    try:
        model = get_model()
        if not model:
            return

        print(f"Generating description for {image_path}...")
        
        # Load and preprocess image
        img = keras_image.load_img(image_path, target_size=(224, 224))
        x = keras_image.img_to_array(img)
        x = np.expand_dims(x, axis=0)
        x = preprocess_input(x)

        preds = model.predict(x, verbose=0)
        # decode_predictions returns a list of lists (one for each sample in batch)
        # Each entry is (class_id, class_name, score)
        decoded = decode_predictions(preds, top=3)[0]
        
        # Formulate description: "seashore, sandbar, lakeside"
        # decode_predictions returns [('n09421951', 'sandbar', 0.8), ...]
        tags = [d[1] for d in decoded]
        description = ", ".join(tags)
        
        print(f"Generated: {description}")
        
        c = conn.cursor()
        c.execute("UPDATE photos SET description = ? WHERE id = ?", (description, photo_id))
        conn.commit()

    except Exception as e:
        print(f"Description error: {e}")
        # traceback.print_exc()

def process_faces(conn, photo_id, image_path, userid):
    try:
        print(f"Processing faces for {image_path}...")
        image = face_recognition.load_image_file(image_path)
        
        # Detect faces
        # upsample 0 times for speed on CPU, maybe 1 if small
        face_locations = face_recognition.face_locations(image, model="hog") 
        if not face_locations:
            c = conn.cursor()
            c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
            conn.commit()
            return

        face_encodings = face_recognition.face_encodings(image, face_locations)
        
        c = conn.cursor()
        
        # Load all existing people embeddings for this user (or global? User specific usually better but request implies global or simple)
        # Request says: "user logs in... identify them". Let's assume 1 user for now or global people gallery? 
        # "The Person Gallery (kept in ./gallary)" -> implied global or per install.
        # Let's verify against all people in DB.
        
        c.execute("SELECT id, embedding_blob FROM people")
        known_people = c.fetchall()
        known_encodings = []
        known_ids = []
        for p in known_people:
            if p['embedding_blob']:
                known_encodings.append(database.convert_array(p['embedding_blob']))
                known_ids.append(p['id'])
        
        for encoding in face_encodings:
            matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=0.6)
            face_distances = face_recognition.face_distance(known_encodings, encoding)
            
            p_id = None
            if len(known_encodings) > 0:
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    p_id = known_ids[best_match_index]
            
            if p_id is None:
                # Create new person
                print("New person found!")
                # Create a thumbnail for the face? 
                # User request: "Person Gallery... table of unique face thumbnails + their average embedding"
                # We need to crop the face for the thumbnail.
                # face_locations is (top, right, bottom, left)
                # We'll save just the first one found as the thumbnail for now.
                
                c.execute("INSERT INTO people (embedding_blob) VALUES (?)", (database.adapt_array(encoding),))
                p_id = c.lastrowid
                
                # Reload knowns so next face in same photo can match this new person if same? 
                # Ideally yes.
                known_encodings.append(encoding)
                known_ids.append(p_id)
            
            # Map photo to person
            try:
                c.execute("INSERT INTO photo_people (photo_id, person_id) VALUES (?, ?)", (photo_id, p_id))
            except sqlite3.IntegrityError:
                pass # Already mapped
        
        c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
        conn.commit()
    except Exception as e:
        print(f"Face processing error: {e}")
        traceback.print_exc()

def save_face_crop(image_path, face_location, thumb_dir):
    try:
        top, right, bottom, left = face_location
        
        # Add padding (e.g., 40% of the face size)
        height = bottom - top
        width = right - left
        pad_h = int(height * 0.4)
        pad_w = int(width * 0.4)
        
        with Image.open(image_path) as img:
            img_w, img_h = img.size
            
            # Calculate new coordinates with padding, clamped to image bounds
            new_left = max(0, left - pad_w)
            new_top = max(0, top - pad_h)
            new_right = min(img_w, right + pad_w)
            new_bottom = min(img_h, bottom + pad_h)
            
            face_img = img.crop((new_left, new_top, new_right, new_bottom))
            
            # Resize only if it's huge, otherwise keep generic high quality
            # Limit to 500x500 instead of 150x150 for better recognition/viewing
            face_img.thumbnail((500, 500))
            
            # Create a unique name
            import uuid
            safe_name = f"face_{uuid.uuid4().hex[:8]}.jpg"
            out_path = os.path.join(thumb_dir, safe_name)
            
            if face_img.mode in ('RGBA', 'LA') or (face_img.mode == 'P' and 'transparency' in face_img.info):
                face_img = face_img.convert('RGB')
            
            face_img.save(out_path, 'JPEG', quality=90) # Higher quality JPEG
            return out_path
    except Exception as e:
        print(f"Error saving face crop: {e}")
        return None

def process_faces(conn, photo_id, image_path, userid):
    try:
        print(f"Processing faces for {image_path}...")
        image = face_recognition.load_image_file(image_path)
        
        # Detect faces - using cnn model would be better but requires GPU/dlib compilation with CUDA
        # sticking to hog but maybe upsample for better detection of small faces?
        # upsample=1 is slower but effective.
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
        
        # Prepare thumbnail dir for faces
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
                # Create new person
                print("New person found!")
                
                # Save face thumbnail
                face_thumb_path = save_face_crop(image_path, face_locations[i], thumb_dir)
                
                c.execute("INSERT INTO people (embedding_blob, thumbnail_path) VALUES (?, ?)", 
                          (database.adapt_array(encoding), face_thumb_path))
                p_id = c.lastrowid
                
                known_encodings.append(encoding)
                known_ids.append(p_id)
            
            # Map photo to person
            try:
                c.execute("INSERT INTO photo_people (photo_id, person_id) VALUES (?, ?)", (photo_id, p_id))
            except sqlite3.IntegrityError:
                pass # Already mapped
        
        c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
        conn.commit()
    except Exception as e:
        print(f"Face processing error: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    
    while True:
        try:
            scan_and_process()
        except Exception as e:
            print(f"Daemon crashed: {e}")
            traceback.print_exc()
        time.sleep(15)
