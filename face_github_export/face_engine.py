"""
face_engine.py — OpenCV-based face detection + LBPH recognition engine
"""
import cv2
import numpy as np
import os
import pickle
import base64
from PIL import Image
import io

# Paths
CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
CAPTURES_DIR = os.path.join(os.path.dirname(__file__), "captures")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "face_model.yml")
LABELS_PATH = os.path.join(os.path.dirname(__file__), "face_labels.pkl")

os.makedirs(CAPTURES_DIR, exist_ok=True)

# Load Haar cascade
face_cascade = cv2.CascadeClassifier(CASCADE_PATH)

# LBPH Recognizer (in-memory, reloaded as needed)
_recognizer = None
_label_map = {}   # label_id -> name


def _load_model():
    global _recognizer, _label_map
    if os.path.exists(MODEL_PATH) and os.path.exists(LABELS_PATH):
        rec = cv2.face.LBPHFaceRecognizer_create()
        rec.read(MODEL_PATH)
        with open(LABELS_PATH, "rb") as f:
            _label_map = pickle.load(f)
        _recognizer = rec
        print(f"[Engine] Model loaded. Known faces: {list(_label_map.values())}")
    else:
        _recognizer = None
        _label_map = {}
        print("[Engine] No trained model found. Register faces first.")


def _save_model(recognizer, label_map):
    global _recognizer, _label_map
    recognizer.save(MODEL_PATH)
    with open(LABELS_PATH, "wb") as f:
        pickle.dump(label_map, f)
    _recognizer = recognizer
    _label_map = label_map


def base64_to_image(b64_string: str) -> np.ndarray:
    """Decode base64 image string → OpenCV BGR numpy array."""
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_string)
    pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return cv_img


def image_to_bytes(cv_img: np.ndarray, fmt: str = ".jpg") -> bytes:
    """Encode OpenCV image → bytes."""
    _, buf = cv2.imencode(fmt, cv_img)
    return buf.tobytes()


def detect_faces(cv_img: np.ndarray):
    """Detect faces in an image. Returns list of (x,y,w,h) rects."""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(60, 60),
        flags=cv2.CASCADE_SCALE_IMAGE
    )
    return faces if len(faces) > 0 else []


def recognize_face(gray_roi: np.ndarray):
    """
    Run LBPH recognition on a grayscale face ROI.
    Returns (name, confidence). Confidence < 60 = good match.
    """
    global _recognizer
    if _recognizer is None:
        _load_model()
    if _recognizer is None:
        return "Unknown", None

    try:
        roi_resized = cv2.resize(gray_roi, (200, 200))
        label_id, confidence = _recognizer.predict(roi_resized)
        name = _label_map.get(label_id, "Unknown")
        return name, round(float(confidence), 2)
    except Exception as e:
        print(f"[Engine] Recognition error: {e}")
        return "Unknown", None


def process_frame(b64_image: str):
    """
    Main pipeline: decode → detect faces → recognize each.
    Returns list of dicts with face info + annotated image bytes.
    """
    cv_img = base64_to_image(b64_image)
    faces = detect_faces(cv_img)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

    results = []
    for (x, y, w, h) in faces:
        roi_gray = gray[y:y+h, x:x+w]
        name, confidence = recognize_face(roi_gray)

        # Determine match quality
        if confidence is None:
            status = "no_model"
        elif confidence < 60:
            status = "recognized"
        elif confidence < 100:
            status = "uncertain"
        else:
            name = "Unknown"
            status = "unknown"

        results.append({
            "name": name,
            "confidence": confidence,
            "status": status,
            "bbox": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
        })

    # Save snapshot
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    snapshot_filename = f"capture_{timestamp}.jpg"
    snapshot_path = os.path.join(CAPTURES_DIR, snapshot_filename)
    cv2.imwrite(snapshot_path, cv_img)

    snapshot_bytes = image_to_bytes(cv_img)

    return results, snapshot_path, snapshot_bytes


def train_model(training_data: list):
    """
    Re-train the LBPH model with all known face data.
    training_data: list of (label_id, face_image_bytes)
    label_map: dict of label_id -> name (passed from DB)
    """
    pass  # Called by register_new_face below


def register_new_face(name: str, b64_image: str, label_id: int, all_training_data: list, label_map: dict):
    """
    Register a new face:
    - Detect face ROI in image
    - Add to training data
    - Re-train LBPH model
    Returns thumbnail bytes or None if no face found.
    """
    cv_img = base64_to_image(b64_image)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    faces = detect_faces(cv_img)

    if len(faces) == 0:
        return None, "No face detected in the image"

    (x, y, w, h) = faces[0]
    roi = gray[y:y+h, x:x+w]
    roi_resized = cv2.resize(roi, (200, 200))

    # Build full training set
    face_images = []
    face_labels = []
    for (lbl_id, img_bytes) in all_training_data:
        try:
            arr = np.frombuffer(img_bytes, dtype=np.uint8)
            face_img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if face_img is not None:
                face_img = cv2.resize(face_img, (200, 200))
                face_images.append(face_img)
                face_labels.append(lbl_id)
        except Exception as e:
            print(f"[Engine] Skipping corrupt training sample: {e}")

    # Add new face
    face_images.append(roi_resized)
    face_labels.append(label_id)

    if len(face_images) == 0:
        return None, "Training data is empty"

    # Train
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(face_images, np.array(face_labels))
    label_map[label_id] = name
    _save_model(recognizer, label_map)

    print(f"[Engine] Trained model with {len(face_images)} samples. Labels: {label_map}")

    # Create thumbnail (color crop)
    thumb = cv_img[y:y+h, x:x+w]
    thumbnail_bytes = image_to_bytes(thumb)
    return thumbnail_bytes, None


# Load model on module import
_load_model()
