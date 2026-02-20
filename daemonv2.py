import os
import time
import sqlite3
import database
from PIL import Image, ImageOps
import traceback
import numpy as np
from threading import Thread

# ===========================================================================
# InsightFace — buffalo_l model (512-dim ArcFace embeddings)
# ===========================================================================

try:
    import insightface
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    print("[AI Worker] insightface not installed. Run: pip install insightface onnxruntime")
    INSIGHTFACE_AVAILABLE = False

# Global model instance (loaded lazily so worker thread startup is fast)
_FACE_APP = None

def get_face_app():
    global _FACE_APP
    if _FACE_APP is None and INSIGHTFACE_AVAILABLE:
        print("[AI Worker] Loading InsightFace buffalo_l model (first run may download ~500MB)...")
        app = FaceAnalysis(
            name='buffalo_l',
            providers=['CPUExecutionProvider'],
        )
        # det_size must be a fixed square; 640 is the recommended size for buffalo_l
        app.prepare(ctx_id=0, det_size=(640, 640))
        _FACE_APP = app
        print("[AI Worker] InsightFace buffalo_l model loaded.")
    return _FACE_APP

# Optional: AI description via TensorFlow (unchanged from v1)
_MOBILENET_MODEL = None
_TF_IMPORTED = False
try:
    import tensorflow as tf
    from tensorflow.keras.applications.mobilenet_v2 import MobileNetV2, preprocess_input, decode_predictions
    from tensorflow.keras.preprocessing import image as keras_image
    _TF_IMPORTED = True
except ImportError:
    print("TensorFlow not available. Description generation disabled.")

def get_description_model():
    global _MOBILENET_MODEL
    if _MOBILENET_MODEL is None and _TF_IMPORTED:
        print("[AI Worker] Loading MobileNetV2 model...")
        _MOBILENET_MODEL = MobileNetV2(weights='imagenet')
    return _MOBILENET_MODEL


# ===========================================================================
# Constants / helpers  (identical to daemonv1)
# ===========================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '../backup/data'))

def get_user_dir(userid):
    return os.path.join(DATA_DIR, userid)

def get_thumbnail_dir(userid):
    return os.path.join(get_user_dir(userid), 'thumbnails')

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic',
                      '.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts'}

def is_image_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def is_video_file(filename):
    return filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm', '.mts', '.m2ts'))

def determine_image_type(filename, exif_data=None):
    fname_lower = filename.lower()
    # Filename explicitly says screenshot — trust that
    if 'screenshot' in fname_lower or 'screen shot' in fname_lower:
        return 'screenshot'
    # Has camera EXIF tags → definitely a photo
    if exif_data and (271 in exif_data or 272 in exif_data):
        return 'photo'
    # Default to 'photo' so real pictures aren't excluded from face detection.
    # The old default of 'screenshot' caused all photos lacking Make/Model EXIF
    # to be permanently skipped by the AI worker.
    return 'photo'

def extract_date_from_filename(filename):
    import re
    from datetime import datetime

    match = re.search(r'(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                            int(match.group(4)), int(match.group(5)), int(match.group(6))).isoformat()
        except ValueError:
            pass

    match = re.search(r'(20\d{2})-(\d{2})-(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass

    match = re.search(r'(20\d{2})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            pass

    return None

def generate_video_thumbnail(video_path, thumb_path):
    """
    Extract a frame from a video and save as a 300x300 JPEG thumbnail.
    Uses ffmpeg with scale filter + PIL exif_transpose as a safety net.

    Strategy: try several seek positions so that very short videos (< 1s)
    still yield a frame.  Using -ss BEFORE -i (input seek) is faster and
    does not require the full file to be decoded up-front.
    """
    import subprocess
    import tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_path = tmp.name

        scale_filter = "scale='min(300,iw)':'min(300,ih)':force_original_aspect_ratio=decrease"

        # Seek positions to try: 1 s, 0 s (very first frame), 0.5 s
        seek_positions = ['00:00:01.000', '00:00:00.000', '00:00:00.500']
        success = False
        for seek in seek_positions:
            cmd = [
                'ffmpeg', '-y',
                '-ss', seek,          # input seek — fast, works on short clips
                '-i', video_path,
                '-vframes', '1',
                '-vf', scale_filter,
                tmp_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # ffmpeg exit 183 = AVERROR_EXIT (seek past EOF) — try next position
            if result.returncode == 0 and os.path.getsize(tmp_path) > 0:
                success = True
                break

        if not success:
            print(f"Error generating video thumbnail for {video_path}: "
                  f"ffmpeg could not extract any frame")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False

        with Image.open(tmp_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.save(thumb_path, 'JPEG', quality=80)

        os.unlink(tmp_path)
        return True
    except Exception as e:
        print(f"Error generating video thumbnail for {video_path}: {e}")
        try:
            if tmp_path:
                os.unlink(tmp_path)
        except Exception:
            pass
        return False

def get_db_connection_wal(userid):
    conn = database.get_db_connection(userid)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ===========================================================================
# Face processing helpers (InsightFace-specific)
# ===========================================================================

# Tuning constants
MAX_DETECT_DIMENSION = 1920   # downsample before detection
MIN_FACE_PIXELS      = 80     # both width and height must be >= this
MIN_DET_SCORE        = 0.75   # skip low-confidence detections (raised from 0.50)
MIN_DET_SCORE_NEW    = 0.85   # stricter gate for creating new person entries (raised from 0.70)
MIN_FACE_ASPECT      = 0.5    # face bbox width/height ratio must be in this range
MAX_FACE_ASPECT      = 2.0    # (rejects wildly non-square blobs like reflections)
COSINE_SIM_THRESHOLD = 0.40   # same person if cosine similarity >= this
                               # InsightFace embeddings are unit-normalised, so
                               # cosine_sim = np.dot(a, b)  (no division needed)


def load_image_for_insightface(image_path):
    """
    Load an image, apply EXIF rotation, downsample if very large,
    and return as a uint8 numpy array in BGR order (what InsightFace expects).
    """
    with Image.open(image_path) as pil_img:
        pil_img = ImageOps.exif_transpose(pil_img)
        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')

        w, h = pil_img.size
        if max(w, h) > MAX_DETECT_DIMENSION:
            scale = MAX_DETECT_DIMENSION / max(w, h)
            pil_img = pil_img.resize(
                (int(w * scale), int(h * scale)),
                Image.BILINEAR
            )

        rgb = np.array(pil_img, dtype=np.uint8)

    # InsightFace expects BGR
    bgr = rgb[:, :, ::-1]
    return bgr


def save_face_crop(image_path, bbox, thumb_dir):
    """
    Crop, pad, and save a face thumbnail given an InsightFace bounding box
    [x1, y1, x2, y2]. Applies EXIF rotation before cropping.
    """
    import uuid
    try:
        x1, y1, x2, y2 = [int(v) for v in bbox]

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')

            img_w, img_h = img.size
            face_w = x2 - x1
            face_h = y2 - y1
            pad_x = int(face_w * 0.4)
            pad_y = int(face_h * 0.4)

            crop_box = (
                max(0, x1 - pad_x),
                max(0, y1 - pad_y),
                min(img_w, x2 + pad_x),
                min(img_h, y2 + pad_y),
            )
            face_img = img.crop(crop_box)
            face_img.thumbnail((500, 500))

            if face_img.mode in ('RGBA', 'LA') or (face_img.mode == 'P' and 'transparency' in face_img.info):
                face_img = face_img.convert('RGB')

            safe_name = f"face_{uuid.uuid4().hex[:8]}.jpg"
            out_path = os.path.join(thumb_dir, safe_name)
            face_img.save(out_path, 'JPEG', quality=90)
            return out_path

    except Exception as e:
        print(f"[AI Worker] Error saving face crop: {e}")
        return None


def process_faces(conn, photo_id, image_path, userid):
    """
    Detect faces using InsightFace buffalo_l, match against known people via
    cosine similarity, and update the DB.
    """
    face_app = get_face_app()
    if not face_app:
        c = conn.cursor()
        c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
        conn.commit()
        return

    try:
        print(f"[AI Worker] Processing faces: {os.path.basename(image_path)}")

        bgr = load_image_for_insightface(image_path)
        faces = face_app.get(bgr)

        # --- Filter detections ---
        filtered = []
        for face in faces:
            score = float(face.det_score)
            bbox  = face.bbox  # [x1, y1, x2, y2]
            fw = bbox[2] - bbox[0]
            fh = bbox[3] - bbox[1]

            print(f"[AI Worker]   candidate: score={score:.3f} size={fw:.0f}x{fh:.0f}")

            if score < MIN_DET_SCORE:
                print(f"[AI Worker]   -> skip: low confidence ({score:.2f} < {MIN_DET_SCORE})")
                continue
            if fw < MIN_FACE_PIXELS or fh < MIN_FACE_PIXELS:
                print(f"[AI Worker]   -> skip: too small ({fw:.0f}x{fh:.0f}px)")
                continue
            aspect = fw / fh if fh > 0 else 0
            if aspect < MIN_FACE_ASPECT or aspect > MAX_FACE_ASPECT:
                print(f"[AI Worker]   -> skip: bad aspect ratio ({aspect:.2f})")
                continue

            # --- Landmark geometry validation ---
            # InsightFace kps: [left_eye, right_eye, nose, left_mouth, right_mouth]
            # Each is (x, y) in pixel coords relative to the (possibly downsampled) image.
            kps = getattr(face, 'kps', None)
            if kps is not None and len(kps) == 5:
                le, re, nose, lm, rm = kps  # left_eye, right_eye, nose, left_mouth, right_mouth

                # All keypoints must lie inside the bounding box (with 20% tolerance)
                pad_x, pad_y = fw * 0.2, fh * 0.2
                in_box = all(
                    bbox[0] - pad_x <= pt[0] <= bbox[2] + pad_x and
                    bbox[1] - pad_y <= pt[1] <= bbox[3] + pad_y
                    for pt in [le, re, nose, lm, rm]
                )
                if not in_box:
                    print(f"[AI Worker]   -> skip: keypoints outside bbox")
                    continue

                # Eyes must be ABOVE nose, nose must be ABOVE mouth
                eyes_above_nose  = (le[1] < nose[1]) and (re[1] < nose[1])
                nose_above_mouth = nose[1] < ((lm[1] + rm[1]) / 2)
                if not (eyes_above_nose and nose_above_mouth):
                    print(f"[AI Worker]   -> skip: landmark order wrong (not a face)")
                    continue

                # Eyes must be horizontally separated (> 15% of face width)
                eye_sep = abs(re[0] - le[0])
                if eye_sep < fw * 0.15:
                    print(f"[AI Worker]   -> skip: eyes too close together ({eye_sep:.0f}px)")
                    continue

                # Nose must be vertically between eyes and mouth (within reason)
                eye_y  = (le[1] + re[1]) / 2
                mouth_y = (lm[1] + rm[1]) / 2
                if not (eye_y < nose[1] < mouth_y):
                    print(f"[AI Worker]   -> skip: nose not between eyes and mouth")
                    continue

                print(f"[AI Worker]   -> landmarks OK (eye_sep={eye_sep:.0f}px)")
            else:
                # No keypoints available — be more conservative
                if score < 0.90:
                    print(f"[AI Worker]   -> skip: no landmarks and score<0.90")
                    continue

            filtered.append(face)

        if not filtered:
            c = conn.cursor()
            c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
            conn.commit()
            return

        c = conn.cursor()

        # Load all known people embeddings
        c.execute("SELECT id, embedding_blob FROM people")
        known_people = c.fetchall()
        known_embeddings = []  # list of np.ndarray (512-dim, unit-normalised)
        known_ids = []
        for p in known_people:
            if p['embedding_blob']:
                emb = database.convert_array(p['embedding_blob'])
                if emb.shape[0] == 512:  # only compare v2 embeddings
                    known_embeddings.append(emb)
                    known_ids.append(p['id'])

        thumb_dir = get_thumbnail_dir(userid)

        for face in filtered:
            embedding = face.embedding  # 512-dim float32, already L2-normalised
            score     = float(face.det_score)
            bbox      = face.bbox

            # --- Find best matching known person ---
            p_id = None
            if known_embeddings:
                sims = np.array([float(np.dot(embedding, ke)) for ke in known_embeddings])
                best_idx = int(np.argmax(sims))
                if sims[best_idx] >= COSINE_SIM_THRESHOLD:
                    p_id = known_ids[best_idx]
                    print(f"[AI Worker]   matched person {p_id} (sim={sims[best_idx]:.3f})")

            # --- Create new person if no match ---
            if p_id is None:
                if score < MIN_DET_SCORE_NEW:
                    print(f"[AI Worker]   skipping uncertain new face (det_score={score:.2f})")
                    continue

                print(f"[AI Worker]   new person found (det_score={score:.2f})")
                face_thumb_path = save_face_crop(image_path, bbox, thumb_dir)

                c.execute(
                    "INSERT INTO people (embedding_blob, thumbnail_path) VALUES (?, ?)",
                    (database.adapt_array(embedding), face_thumb_path)
                )
                p_id = c.lastrowid
                known_embeddings.append(embedding)
                known_ids.append(p_id)

            # --- Link photo to person ---
            try:
                c.execute(
                    "INSERT INTO photo_people (photo_id, person_id) VALUES (?, ?)",
                    (photo_id, p_id)
                )
            except sqlite3.IntegrityError:
                pass  # already linked

        c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
        conn.commit()

    except Exception as e:
        print(f"[AI Worker] Face processing error for {image_path}: {e}")
        traceback.print_exc()


# ===========================================================================
# THREAD 1: SCANNER — Fast path (thumbnails + EXIF)   [identical to v1]
# ===========================================================================

def scan_and_thumbnail():
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

                        if os.path.islink(full_path):
                            continue

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
                            processed_exif  = False
                            current_type    = None
                        else:
                            photo_id       = row['id']
                            processed_thumb = row['processed_for_thumbnails']
                            processed_exif  = row['processed_for_exif']
                            current_type    = row['type']

                        # --- Thumbnails ---
                        if not processed_thumb:
                            try:
                                rel_from_files = os.path.relpath(full_path, files_dir)
                                safe_base = rel_from_files.replace(os.path.sep, '_')

                                if safe_base.lower().endswith(('.jpg', '.jpeg', '.png', '.webp',
                                                                '.mp4', '.mov', '.avi', '.mkv',
                                                                '.webm', '.mts', '.m2ts')):
                                    safe_name = f"{device}__{safe_base}.jpg"
                                    if safe_base.lower().endswith('.jpg'):
                                        safe_name = f"{device}__{safe_base}"
                                else:
                                    safe_name = f"{device}__{safe_base}.jpg"

                                thumb_out = os.path.join(thumb_dir, safe_name)

                                thumb_success = False
                                if os.path.exists(thumb_out):
                                    thumb_success = True
                                elif is_video_file(filename):
                                    thumb_success = generate_video_thumbnail(full_path, thumb_out)
                                else:
                                    with Image.open(full_path) as img:
                                        img = ImageOps.exif_transpose(img)
                                        img.thumbnail((300, 300))
                                        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                                            img = img.convert('RGB')
                                        img.save(thumb_out, 'JPEG', quality=80)
                                        thumb_success = True

                                if thumb_success:
                                    c.execute("UPDATE photos SET processed_for_thumbnails = 1 WHERE id = ?", (photo_id,))
                                    conn.commit()
                                    print(f"[Scanner] Thumbnail done: {filename}")
                                else:
                                    print(f"[Scanner] Thumbnail failed: {filename}")
                            except Exception as e:
                                print(f"[Scanner] Thumbnail error {filename}: {e}")
                                c.execute("""UPDATE photos SET
                                    processed_for_thumbnails = 1,
                                    processed_for_exif = 1,
                                    processed_for_faces = 1,
                                    type = 'unidentifiable'
                                    WHERE id = ?""", (photo_id,))
                                conn.commit()

                        # --- EXIF ---
                        if not is_video_file(filename) and (not processed_exif or current_type is None):
                            new_type = process_exif(conn, photo_id, full_path)
                            if new_type:
                                current_type = new_type

                        # --- Video type + date ---
                        if is_video_file(filename):
                            if not processed_exif:
                                video_date = extract_date_from_filename(filename)
                                if video_date:
                                    c.execute(
                                        "UPDATE photos SET date_taken = ?, processed_for_exif = 1 WHERE id = ?",
                                        (video_date, photo_id)
                                    )
                                else:
                                    c.execute("UPDATE photos SET processed_for_exif = 1 WHERE id = ?", (photo_id,))

                            if current_type != 'video':
                                c.execute("UPDATE photos SET type = 'video' WHERE id = ?", (photo_id,))

                            c.execute(
                                "UPDATE photos SET processed_for_faces = 1 WHERE id = ? AND processed_for_faces = 0",
                                (photo_id,)
                            )
                            conn.commit()
                            continue

                        # --- Screenshots ---
                        if current_type == 'screenshot':
                            c.execute(
                                "UPDATE photos SET processed_for_faces = 1 WHERE id = ? AND processed_for_faces = 0",
                                (photo_id,)
                            )
                            c.execute(
                                "UPDATE photos SET description = 'Screenshot' WHERE id = ? AND description IS NULL",
                                (photo_id,)
                            )
                            conn.commit()

        except Exception as e:
            print(f"[Scanner] Error processing user {userid}: {e}")
            traceback.print_exc()
        finally:
            conn.close()

    print("[Scanner] Scan complete.")


# ===========================================================================
# EXIF Processing  [identical to v1]
# ===========================================================================

def process_exif(conn, photo_id, image_path):
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

            date_taken   = None
            location_lat = None
            location_lon = None

            if 36867 in exif:
                try:
                    date_taken = datetime.strptime(exif[36867], "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            elif 306 in exif:
                try:
                    date_taken = datetime.strptime(exif[306], "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass

            if not date_taken:
                date_taken = extract_date_from_filename(os.path.basename(image_path))

            if 34853 in exif:
                gps_info = exif[34853]
                if isinstance(gps_info, dict) and 2 in gps_info and 4 in gps_info:
                    def dms_to_decimal(dms, ref):
                        if isinstance(dms, (tuple, list)) and len(dms) == 3:
                            decimal = float(dms[0]) + float(dms[1]) / 60.0 + float(dms[2]) / 3600.0
                            if ref in ['S', 'W']:
                                decimal = -decimal
                            return decimal
                        return None

                    location_lat = dms_to_decimal(gps_info[2], gps_info.get(1, 'N'))
                    location_lon = dms_to_decimal(gps_info[4], gps_info.get(3, 'E'))

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
            c.execute(
                "UPDATE photos SET processed_for_exif = 1, type = ?, date_taken = ? WHERE id = ?",
                (image_type, date_guess, photo_id)
            )
            conn.commit()
            return image_type
        except Exception:
            c = conn.cursor()
            c.execute("UPDATE photos SET processed_for_exif = 1 WHERE id = ?", (photo_id,))
            conn.commit()
            return None


# ===========================================================================
# THREAD 2: AI WORKER — Slow path  [face logic uses InsightFace, description unchanged]
# ===========================================================================

def process_description(conn, photo_id, image_path):
    if not _TF_IMPORTED:
        return
    try:
        model = get_description_model()
        if not model:
            return

        print(f"[AI Worker] Generating description for {os.path.basename(image_path)}...")

        img = keras_image.load_img(image_path, target_size=(224, 224))
        x = keras_image.img_to_array(img)
        x = np.expand_dims(x, axis=0)
        x = preprocess_input(x)

        preds = model.predict(x, verbose=0)
        decoded = decode_predictions(preds, top=3)[0]
        description = ", ".join(d[1] for d in decoded)

        print(f"[AI Worker] Description: {description}")

        c = conn.cursor()
        c.execute("UPDATE photos SET description = ? WHERE id = ?", (description, photo_id))
        conn.commit()

    except Exception as e:
        print(f"[AI Worker] Description error: {e}")


def ai_process():
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
            # ---- Face Recognition ----
            c.execute("""
                SELECT id, path, type FROM photos
                WHERE processed_for_thumbnails = 1
                  AND processed_for_exif = 1
                  AND processed_for_faces = 0
            """)
            pending_faces = c.fetchall()

            for row in pending_faces:
                photo_id   = row['id']
                image_path = row['path']
                photo_type = row['type']

                if photo_type in ('video', 'screenshot'):
                    c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                    conn.commit()
                    continue

                if not os.path.exists(image_path):
                    print(f"[AI Worker] File missing, skipping: {image_path}")
                    c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                    conn.commit()
                    continue

                if INSIGHTFACE_AVAILABLE:
                    process_faces(conn, photo_id, image_path, userid)
                else:
                    c.execute("UPDATE photos SET processed_for_faces = 1 WHERE id = ?", (photo_id,))
                    conn.commit()

            # ---- Description Generation ----
            c.execute("""
                SELECT id, path, type FROM photos
                WHERE processed_for_thumbnails = 1
                  AND processed_for_exif = 1
                  AND description IS NULL
            """)
            pending_desc = c.fetchall()

            for row in pending_desc:
                photo_id   = row['id']
                image_path = row['path']
                photo_type = row['type']

                if photo_type == 'video':
                    continue
                if photo_type == 'screenshot':
                    c.execute("UPDATE photos SET description = 'Screenshot' WHERE id = ?", (photo_id,))
                    conn.commit()
                    continue
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
# THREAD LOOP WRAPPERS
# ===========================================================================

def scanner_loop():
    print("[Scanner] Thread started.")
    while True:
        try:
            scan_and_thumbnail()
        except Exception as e:
            print(f"[Scanner] Crashed: {e}")
            traceback.print_exc()
        time.sleep(15)


def ai_worker_loop():
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
# MAIN
# ===========================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  PhotoVault Daemon v2 — InsightFace buffalo_l")
    print("  Thread 1: Scanner (thumbnails + EXIF) — every 15s")
    print("  Thread 2: AI Worker (faces + descriptions) — every 30s")
    print("=" * 60)

    if not INSIGHTFACE_AVAILABLE:
        print("\n[WARNING] InsightFace not installed!")
        print("  Run: venv/bin/pip install insightface onnxruntime")
        print("  Daemon will run but face recognition will be disabled.\n")

    scanner_thread   = Thread(target=scanner_loop,    daemon=True, name="Scanner")
    ai_thread        = Thread(target=ai_worker_loop,  daemon=True, name="AI-Worker")

    scanner_thread.start()
    ai_thread.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nShutting down daemon...")
