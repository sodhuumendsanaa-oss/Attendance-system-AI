"""
app.py — Flask REST API for Face Recognition + Period-Based Attendance System
"""
import os
import json
import base64
import datetime
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

import database
import face_engine

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# Initialize DB on startup
database.init_db()

# ─────────────────────────────────────────────────────────────
# Period Configuration
# ─────────────────────────────────────────────────────────────
PERIODS_FILE = os.path.join(os.path.dirname(__file__), "periods.json")


def load_periods():
    """Load period config from periods.json."""
    try:
        with open(PERIODS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Periods] Failed to load: {e}")
        return []


def save_periods(periods: list):
    """Save period config to periods.json."""
    with open(PERIODS_FILE, "w", encoding="utf-8") as f:
        json.dump(periods, f, indent=2, ensure_ascii=False)


def get_active_period(student_name: str = None):
    """
    Returns (period_name, status, period_dict).
    Status is:
      - within window_start & window_end   → 'Present'
      - within window_end & late_end       → 'Late'
      - outside all periods                → None
      
    If student_name is provided, checks for Slack delay requests and extends the windows automatically.
    """
    now = datetime.datetime.now().time()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    periods = load_periods()
    
    slack_delay_minutes = 0
    if student_name:
        delay_req = database.get_slack_delay_for_student(student_name, today_str)
        if delay_req:
            slack_delay_minutes = delay_req["delay_minutes"]
            
    for p in periods:
        # Base times
        ws = datetime.time(*map(int, p["window_start"].split(":")))
        we = datetime.time(*map(int, p["window_end"].split(":")))
        le = datetime.time(*map(int, p["late_end"].split(":")))
        
        # Extend times if slack_delay_minutes > 0
        if slack_delay_minutes > 0:
            dt_we = datetime.datetime.combine(datetime.date.today(), we)
            we = (dt_we + datetime.timedelta(minutes=slack_delay_minutes)).time()
            
            dt_le = datetime.datetime.combine(datetime.date.today(), le)
            le = (dt_le + datetime.timedelta(minutes=slack_delay_minutes)).time()

        if ws <= now <= we:
            return p["name"], "Present", p
        elif we < now <= le:
            return p["name"], "Late", p

    return None, None, None  # No active period right now

# ─────────────────────────────────────────────────────────────
# Attendance cooldown (per person per period per date)
# Prevents re-logging same person during same period
# Key: "name::period_name::date" → datetime
# ─────────────────────────────────────────────────────────────
_attendance_cooldown: dict = {}
COOLDOWN_MINUTES = 30


def _can_record_attendance(name: str, period_name: str) -> bool:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    key = f"{name}::{period_name}::{today}"
    last = _attendance_cooldown.get(key)
    if last is None:
        return True
    elapsed = (datetime.datetime.now() - last).total_seconds() / 60
    return elapsed >= COOLDOWN_MINUTES


def _mark_cooldown(name: str, session: str):
    key = f"{name}::{session}"
    _attendance_cooldown[key] = datetime.datetime.now()


# ─────────────────────────────────────────────────────────────
# Static file serving
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ─────────────────────────────────────────────────────────────
# API: Recognize  (auto-submits attendance for known faces)
# ─────────────────────────────────────────────────────────────

@app.route("/api/recognize", methods=["POST"])
def api_recognize():
    """
    Receive a base64 image, detect & recognize faces, log to DB.
    Auto-submits attendance for any recognized face IF a period is currently active.
    Body: { "image": "<base64 dataURL>" }
    """
    data = request.get_json(force=True)
    b64_image = data.get("image", "")

    if not b64_image:
        return jsonify({"error": "No image provided"}), 400

    try:
        results, snapshot_path, snapshot_bytes = face_engine.process_frame(b64_image)
    except Exception as e:
        print(f"[API] recognize error: {e}")
        return jsonify({"error": str(e)}), 500

    # We will determine active period dynamically per student now
    global_period_name, global_att_status, global_active_period = get_active_period()

    logged = []
    attendance_events = []

    for face in results:
        name = face["name"]
        confidence = face["confidence"]
        status = face["status"]

        # Always log to detection_logs
        database.log_detection(name, confidence, snapshot_path, snapshot_bytes)

        # Auto-submit attendance ONLY for recognized faces
        attendance_submitted = False
        if status == "recognized" and name != "Unknown":
            period_name, att_status, active_period = get_active_period(student_name=name)
            if period_name:
                if _can_record_attendance(name, period_name):
                    # Check if a slack request extended the period, if so, append reason
                    slack_req = database.get_slack_delay_for_student(name, datetime.datetime.now().strftime("%Y-%m-%d"))
                    excused_reason = f"Slack DM: {slack_req['commute_line']} ({slack_req['delay_minutes']}分)" if slack_req and att_status == "Present" else ""
                    
                    inserted = database.submit_attendance(
                        student_name=name,
                        session=period_name,
                        confidence=confidence,
                        snapshot_bytes=snapshot_bytes,
                        status=att_status          # "Present" or "Late"
                    )
                    
                    # Update excused reason if it's a slack automated excuse
                    if inserted and excused_reason:
                        # find the last inserted id and update it
                        # wait, we can just let submit_attendance handle it or update it directly.
                        # It's cleaner to update it:
                        conn = database.get_connection()
                        conn.execute(
                            "UPDATE attendance_records SET excused_reason=? WHERE student_name=? AND date=? AND session=?",
                            (excused_reason, name, datetime.datetime.now().strftime("%Y-%m-%d"), period_name)
                        )
                        conn.commit()
                        conn.close()

                    if inserted:
                        _mark_cooldown(name, period_name)
                        attendance_submitted = True
                        attendance_events.append({
                            "name": name,
                            "period": period_name,
                            "status": att_status,
                            "time": datetime.datetime.now().strftime("%H:%M:%S")
                        })

        face["attendance_submitted"] = attendance_submitted
        logged.append(face)

    return jsonify({
        "faces": logged,
        "snapshot_path": snapshot_path,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "attendance_events": attendance_events,
        "active_period": {
            "name": global_period_name,
            "status": global_att_status,
            "window_start": global_active_period["window_start"] if global_active_period else None,
            "window_end":   global_active_period["window_end"]   if global_active_period else None,
        } if global_period_name else None
    })


# ─────────────────────────────────────────────────────────────
# API: Periods
# ─────────────────────────────────────────────────────────────

@app.route("/api/periods", methods=["GET"])
def api_get_periods():
    """Return all configured periods + which one is currently active."""
    periods = load_periods()
    period_name, att_status, _ = get_active_period()
    return jsonify({
        "periods": periods,
        "active_period": period_name,
        "active_status": att_status,
        "current_time": datetime.datetime.now().strftime("%H:%M:%S")
    })


@app.route("/api/periods", methods=["POST"])
def api_save_periods():
    """Save updated periods list."""
    data = request.get_json(force=True)
    periods = data.get("periods", [])
    save_periods(periods)
    return jsonify({"success": True, "message": "Periods saved."})


# ─────────────────────────────────────────────────────────────
# API: Register new face
# ─────────────────────────────────────────────────────────────

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    b64_image = data.get("image", "")
    commute_line = data.get("commute_line", "").strip()

    if not name or not b64_image:
        return jsonify({"error": "Name and image are required"}), 400

    known = database.get_all_known_faces()
    label_map = {row["label_id"]: row["name"] for row in known}
    all_training_data = [(row["label_id"], row["thumbnail"]) for row in known]
    existing_ids = [row["label_id"] for row in known]
    label_id = max(existing_ids) + 1 if existing_ids else 0

    thumbnail_bytes, error = face_engine.register_new_face(
        name, b64_image, label_id, all_training_data, label_map
    )
    if error:
        return jsonify({"error": error}), 400

    database.register_face(name, label_id, thumbnail_bytes, commute_line)
    return jsonify({"success": True, "message": f"'{name}' registered successfully!", "label_id": label_id})


# ─────────────────────────────────────────────────────────────
# API: Detection logs
# ─────────────────────────────────────────────────────────────

@app.route("/api/logs", methods=["GET"])
def api_logs():
    limit = int(request.args.get("limit", 50))
    rows = database.get_detection_logs(limit)
    logs = []
    for row in rows:
        entry = {
            "id": row["id"],
            "face_name": row["face_name"],
            "confidence": row["confidence"],
            "image_path": row["image_path"],
            "captured_at": row["captured_at"],
        }
        if row["snapshot"]:
            entry["snapshot_b64"] = "data:image/jpeg;base64," + base64.b64encode(row["snapshot"]).decode()
        else:
            entry["snapshot_b64"] = None
        logs.append(entry)
    return jsonify({"logs": logs})


@app.route("/api/logs", methods=["DELETE"])
def api_clear_logs():
    conn = database.get_connection()
    conn.execute("DELETE FROM detection_logs")
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "All logs cleared."})


# ─────────────────────────────────────────────────────────────
# API: Known faces
# ─────────────────────────────────────────────────────────────

@app.route("/api/faces", methods=["GET"])
def api_faces():
    rows = database.get_all_known_faces()
    faces = []
    for row in rows:
        entry = {
            "id": row["id"],
            "name": row["name"],
            "label_id": row["label_id"],
            "commute_line": row["commute_line"],
            "created_at": row["created_at"],
        }
        if row["thumbnail"]:
            entry["thumbnail_b64"] = "data:image/jpeg;base64," + base64.b64encode(row["thumbnail"]).decode()
        else:
            entry["thumbnail_b64"] = None
        faces.append(entry)
    return jsonify({"faces": faces})


@app.route("/api/faces/<int:label_id>", methods=["DELETE"])
def api_delete_face(label_id):
    database.delete_known_face(label_id)
    known = database.get_all_known_faces()
    if known:
        import cv2, numpy as np
        label_map = {row["label_id"]: row["name"] for row in known}
        all_training_data = [(row["label_id"], row["thumbnail"]) for row in known]
        face_images, face_labels = [], []
        for (lbl_id, img_bytes) in all_training_data:
            arr = np.frombuffer(img_bytes, dtype=np.uint8)
            face_img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if face_img is not None:
                face_img = cv2.resize(face_img, (200, 200))
                face_images.append(face_img)
                face_labels.append(lbl_id)
        if face_images:
            rec = cv2.face.LBPHFaceRecognizer_create()
            rec.train(face_images, np.array(face_labels))
            face_engine._save_model(rec, label_map)
    else:
        for p in [face_engine.MODEL_PATH, face_engine.LABELS_PATH]:
            if os.path.exists(p):
                os.remove(p)
        face_engine._recognizer = None
        face_engine._label_map = {}
    return jsonify({"success": True, "message": "Face deleted and model retrained."})


# ─────────────────────────────────────────────────────────────
# ATTENDANCE API
# ─────────────────────────────────────────────────────────────

@app.route("/api/attendance", methods=["GET"])
def api_attendance():
    """
    Get attendance for a specific date and optional session.
    ?date=YYYY-MM-DD&session=Morning
    Defaults to today.
    """
    date = request.args.get("date", datetime.datetime.now().strftime("%Y-%m-%d"))
    session = request.args.get("session", None)
    rows = database.get_attendance_by_date(date, session)

    # Count registered students for rate calculation
    total_registered = len(database.get_all_known_faces())

    records = []
    for row in rows:
        entry = {
            "id": row["id"],
            "student_name": row["student_name"],
            "date": row["date"],
            "time_in": row["time_in"],
            "session": row["session"],
            "status": row["status"],
            "confidence": row["confidence"],
            "excused_reason": row["excused_reason"] if "excused_reason" in row.keys() else "",
            "commute_line": row["commute_line"] if "commute_line" in row.keys() else "",
            "created_at": row["created_at"],
        }
        if row["snapshot"]:
            entry["snapshot_b64"] = "data:image/jpeg;base64," + base64.b64encode(row["snapshot"]).decode()
        else:
            entry["snapshot_b64"] = None
        records.append(entry)

    present = sum(1 for r in records if r["status"] == "Present")
    late    = sum(1 for r in records if r["status"] == "Late")
    absent  = sum(1 for r in records if r["status"] == "Absent")

    return jsonify({
        "date": date,
        "session": session,
        "records": records,
        "stats": {
            "total_recorded": len(records),
            "total_registered": total_registered,
            "present": present,
            "late": late,
            "absent": absent,
        }
    })


@app.route("/api/attendance/summary", methods=["GET"])
def api_attendance_summary():
    """Get all dates with attendance summary stats."""
    rows = database.get_attendance_summary()
    summary = [dict(row) for row in rows]
    return jsonify({"summary": summary})


@app.route("/api/attendance/<int:record_id>", methods=["PATCH"])
def api_update_attendance(record_id):
    """Manual override: change status to Present / Late / Absent and optional excuse."""
    data = request.get_json(force=True)
    new_status = data.get("status", "").strip()
    excuse = data.get("excuse", "").strip()
    ok = database.update_attendance_status(record_id, new_status, excuse)
    if ok:
        return jsonify({"success": True, "message": f"Status updated to '{new_status}'"})
    return jsonify({"error": "Invalid status. Use Present, Late, or Absent."}), 400


@app.route("/api/attendance/export", methods=["GET"])
def api_attendance_export():
    """Download attendance as CSV."""
    date = request.args.get("date", datetime.datetime.now().strftime("%Y-%m-%d"))
    session = request.args.get("session", None)
    csv_str = database.export_attendance_csv(date, session)
    filename = f"attendance_{date}{('_' + session) if session else ''}.csv"
    return Response(
        csv_str,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/attendance", methods=["DELETE"])
def api_clear_attendance():
    """Clear attendance records (all, or for a specific date)."""
    date = request.args.get("date", None)
    database.clear_attendance(date)
    msg = f"Attendance cleared for {date}" if date else "All attendance records cleared."
    return jsonify({"success": True, "message": msg})


@app.route("/api/attendance/student/<string:name>", methods=["GET"])
def api_student_history(name):
    """Get attendance history for a specific student."""
    rows = database.get_student_attendance_history(name)
    records = [dict(row) for row in rows]
    return jsonify({"student": name, "records": records})


@app.route("/api/delays", methods=["GET"])
def api_delays():
    """Scrape JR East delay certificates and return delayed lines."""
    import urllib.request
    import re
    try:
        req = urllib.request.Request('https://traininfo.jreast.co.jp/delay_certificate/', headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req).read().decode('utf-8')
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        delayed_lines = []
        for row in rows:
            route_match = re.search(r'<span class="delaycertificate-table__routename"><a href="[^"]+">([^<]+)</a>', row)
            if not route_match: continue
            route = route_match.group(1).strip()
            delays = re.findall(r'<td class="delay"><a [^>]+>(\d+)分</a></td>', row)
            if delays:
                max_delay = max([int(d) for d in delays])
                delayed_lines.append(f"{route} ({max_delay}分)")
        return jsonify({"success": True, "delayed_lines": delayed_lines})
    except Exception as e:
        print(f"Error fetching delays: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 55)
    print("  FaceGuard + Attendance System starting...")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=True)

