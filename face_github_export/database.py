"""
database.py — SQLite3 schema management for Face Recognition + Attendance App
"""
import sqlite3
import os
import csv
import io

DB_PATH = os.path.join(os.path.dirname(__file__), "face_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    # Table for storing known (registered) faces
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_faces (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            label_id    INTEGER NOT NULL UNIQUE,
            thumbnail   BLOB,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # Table for every detection event (known or unknown)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detection_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            face_name   TEXT NOT NULL DEFAULT 'Unknown',
            confidence  REAL,
            image_path  TEXT,
            snapshot    BLOB,
            captured_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── Attendance Records ─────────────────────────────────────────
    # One record per student per date per session.
    # UNIQUE(student_name, date, session) prevents duplicates.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name    TEXT NOT NULL,
            date            TEXT NOT NULL,
            time_in         TEXT NOT NULL,
            session         TEXT NOT NULL DEFAULT 'Morning',
            status          TEXT NOT NULL DEFAULT 'Present',
            confidence      REAL,
            snapshot        BLOB,
            created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(student_name, date, session)
        )
    """)

    # ── Slack Delay Requests ───────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS slack_delay_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name    TEXT NOT NULL,
            commute_line    TEXT NOT NULL,
            delay_minutes   INTEGER NOT NULL,
            date            TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # Schema Migrations (Add new columns if they don't exist)
    try:
        cursor.execute("ALTER TABLE known_faces ADD COLUMN commute_line TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE attendance_records ADD COLUMN excused_reason TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()
    print("[DB] Database initialized (with schema migrations).")


def get_all_known_faces():
    conn = get_connection()
    rows = conn.execute("SELECT id, name, label_id, thumbnail, commute_line, created_at FROM known_faces").fetchall()
    conn.close()
    return rows


def register_face(name: str, label_id: int, thumbnail_bytes: bytes, commute_line: str = ""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO known_faces (name, label_id, thumbnail, commute_line) VALUES (?, ?, ?, ?)",
        (name, label_id, thumbnail_bytes, commute_line)
    )
    conn.commit()
    conn.close()


def log_detection(face_name: str, confidence: float, image_path: str, snapshot_bytes: bytes):
    conn = get_connection()
    conn.execute(
        "INSERT INTO detection_logs (face_name, confidence, image_path, snapshot) VALUES (?, ?, ?, ?)",
        (face_name, confidence, image_path, snapshot_bytes)
    )
    conn.commit()
    conn.close()


def get_detection_logs(limit: int = 50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, face_name, confidence, image_path, snapshot, captured_at FROM detection_logs ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows


def delete_known_face(label_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM known_faces WHERE label_id = ?", (label_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
# ATTENDANCE FUNCTIONS
# ══════════════════════════════════════════════════════════════

def submit_attendance(student_name: str, session: str, confidence: float,
                      snapshot_bytes: bytes, status: str = "Present"):
    """
    Insert one attendance record. If already exists (same name+date+session), do nothing.
    Returns True if newly inserted, False if already present.
    Status is determined by the period system: 'Present' or 'Late'.
    """
    import datetime
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    conn = get_connection()
    cursor = conn.execute(
        """INSERT OR IGNORE INTO attendance_records
           (student_name, date, time_in, session, status, confidence, snapshot)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (student_name, date_str, time_str, session, status, confidence, snapshot_bytes)
    )
    inserted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def get_attendance_by_date(date: str, session: str = None):
    """Get all attendance records for a given date, optionally filtered by session."""
    conn = get_connection()
    if session:
        rows = conn.execute(
            """SELECT a.id, a.student_name, a.date, a.time_in, a.session, a.status, 
                      a.confidence, a.snapshot, a.excused_reason, a.created_at,
                      IFNULL(k.commute_line, '') as commute_line
               FROM attendance_records a
               LEFT JOIN known_faces k ON a.student_name = k.name
               WHERE a.date = ? AND a.session = ? ORDER BY a.time_in ASC""",
            (date, session)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT a.id, a.student_name, a.date, a.time_in, a.session, a.status, 
                      a.confidence, a.snapshot, a.excused_reason, a.created_at,
                      IFNULL(k.commute_line, '') as commute_line
               FROM attendance_records a
               LEFT JOIN known_faces k ON a.student_name = k.name
               WHERE a.date = ? ORDER BY a.session, a.time_in ASC""",
            (date,)
        ).fetchall()
    conn.close()
    return rows


def get_attendance_summary():
    """Get list of dates with present count and total registered students."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT date, session,
                  COUNT(*) as present_count,
                  SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) as present,
                  SUM(CASE WHEN status='Late'    THEN 1 ELSE 0 END) as late,
                  SUM(CASE WHEN status='Absent'  THEN 1 ELSE 0 END) as absent
           FROM attendance_records
           GROUP BY date, session
           ORDER BY date DESC, session ASC"""
    ).fetchall()
    conn.close()
    return rows


def get_student_attendance_history(student_name: str):
    """Get all attendance records for a specific student."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, student_name, date, time_in, session, status, confidence, created_at
           FROM attendance_records WHERE student_name = ? ORDER BY date DESC, time_in DESC""",
        (student_name,)
    ).fetchall()
    conn.close()
    return rows


def update_attendance_status(record_id: int, new_status: str, excuse: str = ""):
    """Manual override: update attendance status (Present / Late / Absent) and optional excuse."""
    valid = {"Present", "Late", "Absent"}
    if new_status not in valid:
        return False
    conn = get_connection()
    if excuse:
        conn.execute(
            "UPDATE attendance_records SET status = ?, excused_reason = ? WHERE id = ?",
            (new_status, excuse, record_id)
        )
    else:
        conn.execute(
            "UPDATE attendance_records SET status = ? WHERE id = ?",
            (new_status, record_id)
        )
    conn.commit()
    conn.close()
    return True


def export_attendance_csv(date: str, session: str = None) -> str:
    """Generate CSV string for attendance on a given date/session."""
    rows = get_attendance_by_date(date, session)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["#", "Name", "Date", "Time In", "Session", "Status"])
    for i, row in enumerate(rows, 1):
        writer.writerow([i, row["student_name"], row["date"],
                         row["time_in"], row["session"], row["status"]])
    return output.getvalue()


def get_all_sessions():
    """Return distinct session names used so far."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT session FROM attendance_records ORDER BY session"
    ).fetchall()
    conn.close()
    return [r["session"] for r in rows]


def clear_attendance(date: str = None):
    """Clear attendance records. If date given, only clears that date."""
    conn = get_connection()
    if date:
        conn.execute("DELETE FROM attendance_records WHERE date = ?", (date,))
    else:
        conn.execute("DELETE FROM attendance_records")
    conn.commit()
    conn.close()


# ── Slack Delay Requests Functions ───────────────────────────────

def log_slack_delay_request(student_name: str, commute_line: str, delay_minutes: int, date_str: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO slack_delay_requests (student_name, commute_line, delay_minutes, date) VALUES (?, ?, ?, ?)",
        (student_name, commute_line, delay_minutes, date_str)
    )
    conn.commit()
    conn.close()

def get_slack_delay_for_student(student_name: str, date_str: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM slack_delay_requests WHERE student_name=? AND date=? ORDER BY id DESC LIMIT 1",
        (student_name, date_str)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db()
