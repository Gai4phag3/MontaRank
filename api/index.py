"""
MontaRanker - Vercel serverless backend (single catch-all function).

All /api/* routes are rewritten to this function by vercel.json.

Differences from the local server.py:
  - Postgres (Neon) instead of SQLite          -> env var DATABASE_URL
  - Vercel Blob for photos/evidence            -> env var BLOB_READ_WRITE_TOKEN
  - Stateless signed-cookie auth (no memory)   -> env var SECRET_KEY

Static files (public/) are served by Vercel directly.
"""

import os
import json
import time
import math
import base64
import hmac
import hashlib
import secrets
import mimetypes
from http.server import BaseHTTPRequestHandler
from http import cookies
from datetime import datetime
from urllib.parse import urlparse, parse_qs

try:
    import psycopg2
    import psycopg2.extras
except Exception:  # installed on Vercel via requirements.txt
    psycopg2 = None

try:
    import vercel_blob
except Exception:  # package only needed at runtime on Vercel
    vercel_blob = None

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "RaymondIsTheGoat!")
SECRET = os.environ.get("SECRET_KEY", "please-set-a-SECRET_KEY-env-var").encode()
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days

OFFICER_POSITIONS = [
    "President",
    "VP of Operations",
    "Director of Marketing & Public Relations",
    "Director of Finance & Corporate Relations",
    "Director of Outreach",
    "VP of Engineering",
    "Director of Mechanical Engineering",
    "Director of Electrical Engineering",
    "Director of Software Engineering",
    "Lead",
]

_initialized = False


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    conn = psycopg2.connect(dsn)
    return conn


def cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def ensure_init():
    global _initialized
    if _initialized:
        return
    conn = get_conn()
    cur = cursor(conn)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            full_name TEXT NOT NULL,
            student_id TEXT NOT NULL UNIQUE,
            grade TEXT NOT NULL,
            officer_position TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            strikes INTEGER NOT NULL DEFAULT 0,
            kicked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            check_in TEXT NOT NULL,
            check_out TEXT,
            photo TEXT,
            hours REAL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            difficulty INTEGER NOT NULL,
            deadline TEXT NOT NULL,
            assigned_to INTEGER NOT NULL REFERENCES users(id),
            assigned_by INTEGER NOT NULL,
            assigned_by_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'assigned',
            evidence TEXT,
            evidence_type TEXT,
            submitted_at TEXT,
            return_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS strike_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()
    _initialized = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return h, salt


def make_token(payload):
    payload = dict(payload)
    payload["exp"] = int(time.time()) + TOKEN_TTL
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig


def read_token(token):
    try:
        body, sig = token.split(".", 1)
        expect = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def upload_blob(folder, filename, data, mime):
    if vercel_blob is None:
        raise RuntimeError("vercel_blob is not available.")
    resp = vercel_blob.put(
        f"{folder}/{filename}", data,
        {"addRandomSuffix": "true", "contentType": mime or "application/octet-stream"},
    )
    return resp["url"]


def compute_scores(cur, user_id):
    attendance_score = 0.0
    total_hours = 0.0
    cur.execute(
        "SELECT hours FROM attendance WHERE user_id=%s AND check_out IS NOT NULL",
        (user_id,),
    )
    for row in cur.fetchall():
        h = row["hours"] or 0.0
        total_hours += h
        attendance_score += 0.1 * h

    task_score = 0.0
    late_penalty = 0.0
    verified_difficulty = 0
    cur.execute(
        "SELECT difficulty, deadline, submitted_at, return_count "
        "FROM tasks WHERE assigned_to=%s AND status='verified'",
        (user_id,),
    )
    for row in cur.fetchall():
        difficulty = row["difficulty"]
        verified_difficulty += difficulty
        base = 0 if row["return_count"] >= 2 else difficulty
        deadline = parse_dt(row["deadline"])
        submitted = parse_dt(row["submitted_at"])
        if deadline and submitted:
            delta = (deadline - submitted).total_seconds()
            if delta >= 0:
                task_score += base + math.floor(delta / 86400)
            else:
                hours_late = math.ceil((-delta) / 3600)
                task_score += base
                late_penalty += 0.2 * hours_late
        else:
            task_score += base

    total = attendance_score + task_score - late_penalty
    return {
        "attendance_score": round(attendance_score, 2),
        "task_score": round(task_score, 2),
        "late_penalty": round(late_penalty, 2),
        "total": round(total, 2),
        "total_hours": round(total_hours, 2),
        "verified_difficulty": verified_difficulty,
    }


def task_hours_late(row, now):
    """Hours a task is late, or None if it isn't late."""
    if row["status"] == "verified":
        return None
    deadline = parse_dt(row["deadline"])
    if not deadline:
        return None
    if row["submitted_at"]:
        submitted = parse_dt(row["submitted_at"])
        if submitted and submitted > deadline:
            return math.ceil((submitted - deadline).total_seconds() / 3600)
        return None
    if now > deadline:
        return math.ceil((now - deadline).total_seconds() / 3600)
    return None


def user_public(row):
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "student_id": row["student_id"],
        "grade": row["grade"],
        "officer_position": row["officer_position"],
        "strikes": row["strikes"],
        "kicked": bool(row["kicked"]),
    }


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    # -- low level ----------------------------------------------------------
    def _cookie_token(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in c:
            return read_token(c["sid"].value)
        return None

    def _session(self):
        return self._cookie_token()

    def _send_json(self, obj, status=200, set_cookie=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=400):
        self._send_json({"error": msg}, status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _require_user(self):
        s = self._session()
        if not s or s.get("type") != "user":
            self._error("Not logged in", 401)
            return None
        return s

    def _require_admin(self):
        s = self._session()
        if not s or s.get("type") != "admin":
            self._error("Admin only", 403)
            return None
        return s

    def _cookie(self, token):
        return f"sid={token}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age={TOKEN_TTL}"

    # -- routing ------------------------------------------------------------
    def _api_path(self):
        # vercel.json rewrites /api/<x> to /api/index?route=<x>, so the real
        # route is carried in the query string (robust regardless of whether
        # Vercel preserves the original path). Fall back to the raw path locally.
        parsed = urlparse(self.path)
        route = parse_qs(parsed.query).get("route", [None])[0]
        if route is not None:
            return "/api/" + route
        return parsed.path

    def do_GET(self):
        try:
            ensure_init()
            self.route_get(self._api_path())
        except Exception as e:
            self._error(f"Server error: {e}", 500)

    def do_POST(self):
        try:
            ensure_init()
            self.route_post(self._api_path())
        except Exception as e:
            self._error(f"Server error: {e}", 500)

    def route_get(self, path):
        if path == "/api/officer-positions":
            return self._send_json({"positions": OFFICER_POSITIONS})
        if path == "/api/me":
            return self._me()
        if path == "/api/attendance/status":
            return self._att_status()
        if path == "/api/tasks/mine":
            return self._tasks_mine()
        if path == "/api/tasks/assigned-by-me":
            return self._tasks_assigned_by_me()
        if path == "/api/members":
            return self._members()
        if path == "/api/scoreboard":
            return self._scoreboard()
        if path == "/api/admin/users":
            return self._admin_users()
        if path.startswith("/api/admin/users/"):
            return self._admin_user_detail(path)
        if path == "/api/admin/pending":
            return self._admin_pending()
        if path == "/api/admin/home":
            return self._admin_home()
        if path == "/api/admin/late-tasks":
            return self._admin_late_tasks()
        return self._error("Not found", 404)

    def route_post(self, path):
        routes = {
            "/api/signup": self._signup,
            "/api/login": self._login,
            "/api/admin-login": self._admin_login,
            "/api/logout": self._logout,
            "/api/attendance/checkin": self._checkin,
            "/api/attendance/checkout": self._checkout,
            "/api/tasks/create": self._create_task,
            "/api/tasks/complete": self._complete_task,
            "/api/admin/verify": self._admin_verify,
            "/api/admin/return": self._admin_return,
            "/api/admin/verify-all": self._admin_verify_all,
            "/api/admin/strike": self._admin_strike,
            "/api/admin/unstrike": self._admin_unstrike,
            "/api/admin/kick": self._admin_kick,
            "/api/admin/delete": self._admin_delete,
            "/api/admin/clear-all": self._admin_clear_all,
        }
        fn = routes.get(path)
        if fn:
            return fn()
        return self._error("Not found", 404)

    # -- auth ---------------------------------------------------------------
    def _signup(self):
        d = self._read_json()
        full_name = (d.get("full_name") or "").strip()
        student_id = (d.get("student_id") or "").strip()
        grade = (d.get("grade") or "").strip()
        officer = (d.get("officer_position") or "").strip()
        password = d.get("password") or ""
        if not full_name or not student_id or not grade or not password:
            return self._error("Full name, student ID, grade and password are required.")
        if not student_id.isdigit():
            return self._error("Student ID must be a list of numbers only.")
        if officer and officer not in OFFICER_POSITIONS:
            return self._error("Invalid officer position.")
        if len(password) < 4:
            return self._error("Password must be at least 4 characters.")
        h, salt = hash_password(password)
        conn = get_conn()
        cur = cursor(conn)
        try:
            cur.execute(
                "INSERT INTO users (full_name, student_id, grade, officer_position, "
                "password_hash, salt, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (full_name, student_id, grade, officer, h, salt, now_iso()),
            )
            uid = cur.fetchone()["id"]
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            cur.close(); conn.close()
            return self._error("That student ID is already registered.")
        cur.close(); conn.close()
        token = make_token({"type": "user", "id": uid})
        self._send_json({"ok": True, "role": "member"}, set_cookie=self._cookie(token))

    def _login(self):
        d = self._read_json()
        full_name = (d.get("full_name") or "").strip()
        password = d.get("password") or ""
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users WHERE lower(full_name)=lower(%s)", (full_name,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if not rows:
            return self._error("No account with that name.", 401)
        if len(rows) > 1:
            return self._error(
                "More than one account uses that name. Please contact an administrator.",
                401)
        row = rows[0]
        h, _ = hash_password(password, row["salt"])
        if h != row["password_hash"]:
            return self._error("Incorrect password.", 401)
        if row["kicked"]:
            return self._error("This account has been removed by an administrator.", 403)
        token = make_token({"type": "user", "id": row["id"]})
        self._send_json({"ok": True, "role": "member"}, set_cookie=self._cookie(token))

    def _admin_login(self):
        d = self._read_json()
        if (d.get("password") or "") != ADMIN_PASSWORD:
            return self._error("Incorrect administrator password.", 401)
        token = make_token({"type": "admin", "id": None})
        self._send_json({"ok": True, "role": "admin"}, set_cookie=self._cookie(token))

    def _logout(self):
        self._send_json({"ok": True},
                        set_cookie="sid=; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=0")

    # -- me -----------------------------------------------------------------
    def _me(self):
        s = self._session()
        if not s:
            return self._send_json({"user": None})
        if s.get("type") == "admin":
            return self._send_json({"user": {"role": "admin"}})
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users WHERE id=%s", (s["id"],))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return self._send_json({"user": None})
        u = user_public(row)
        u["role"] = "member"
        u["is_officer"] = bool(row["officer_position"])
        self._send_json({"user": u})

    # -- attendance ---------------------------------------------------------
    def _att_status(self):
        s = self._require_user()
        if not s:
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute(
            "SELECT * FROM attendance WHERE user_id=%s AND check_out IS NULL "
            "ORDER BY id DESC LIMIT 1", (s["id"],))
        openrow = cur.fetchone()
        cur.execute(
            "SELECT * FROM attendance WHERE user_id=%s ORDER BY id DESC LIMIT 25",
            (s["id"],))
        history = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        self._send_json({"open": dict(openrow) if openrow else None, "history": history})

    def _checkin(self):
        s = self._require_user()
        if not s:
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT id FROM attendance WHERE user_id=%s AND check_out IS NULL",
                    (s["id"],))
        if cur.fetchone():
            cur.close(); conn.close()
            return self._error("You are already checked in. Check out first.")
        cur.execute("INSERT INTO attendance (user_id, check_in) VALUES (%s,%s)",
                    (s["id"], now_iso()))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _checkout(self):
        s = self._require_user()
        if not s:
            return
        d = self._read_json()
        photo = d.get("photo") or ""
        if not photo.startswith("data:image/"):
            return self._error("A camera photo is required to check out.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute(
            "SELECT * FROM attendance WHERE user_id=%s AND check_out IS NULL "
            "ORDER BY id DESC LIMIT 1", (s["id"],))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return self._error("You are not checked in.")
        try:
            header, b64 = photo.split(",", 1)
            raw = base64.b64decode(b64)
            mime = header.split(";")[0].replace("data:", "") or "image/jpeg"
        except Exception:
            cur.close(); conn.close()
            return self._error("Could not read the photo.")
        ext = mimetypes.guess_extension(mime) or ".jpg"
        url = upload_blob("attendance", f"att_{row['id']}_{secrets.token_hex(4)}{ext}", raw, mime)
        check_in = parse_dt(row["check_in"])
        check_out = datetime.now()
        hours = max(0.0, (check_out - check_in).total_seconds() / 3600.0)
        cur.execute("UPDATE attendance SET check_out=%s, photo=%s, hours=%s WHERE id=%s",
                    (check_out.isoformat(timespec="seconds"), url, round(hours, 3), row["id"]))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True, "hours": round(hours, 2), "points": round(0.1 * hours, 3)})

    # -- tasks --------------------------------------------------------------
    def _tasks_mine(self):
        s = self._require_user()
        if not s:
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM tasks WHERE assigned_to=%s ORDER BY deadline ASC",
                    (s["id"],))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        self._send_json({"tasks": rows})

    def _tasks_assigned_by_me(self):
        s = self._require_user()
        if not s:
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute(
            "SELECT t.*, u.full_name AS assignee_name FROM tasks t "
            "JOIN users u ON u.id=t.assigned_to WHERE t.assigned_by=%s "
            "ORDER BY t.deadline ASC", (s["id"],))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        self._send_json({"tasks": rows})

    def _members(self):
        s = self._require_user()
        if not s:
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users WHERE kicked=FALSE ORDER BY full_name")
        rows = [user_public(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        self._send_json({"members": rows})

    def _create_task(self):
        s = self._require_user()
        if not s:
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users WHERE id=%s", (s["id"],))
        me = cur.fetchone()
        if not me or not me["officer_position"]:
            cur.close(); conn.close()
            return self._error("Only officers can assign tasks.", 403)
        d = self._read_json()
        name = (d.get("name") or "").strip()
        try:
            difficulty = int(d.get("difficulty"))
        except (TypeError, ValueError):
            cur.close(); conn.close()
            return self._error("Difficulty must be a number 1-5.")
        deadline = (d.get("deadline") or "").strip()
        try:
            assigned_to = int(d.get("assigned_to"))
        except (TypeError, ValueError):
            cur.close(); conn.close()
            return self._error("Pick a person to assign.")
        if not name:
            cur.close(); conn.close()
            return self._error("Task name is required.")
        if difficulty < 1 or difficulty > 5:
            cur.close(); conn.close()
            return self._error("Difficulty must be between 1 and 5.")
        if not parse_dt(deadline):
            cur.close(); conn.close()
            return self._error("A valid deadline (date + time) is required.")
        cur.execute("SELECT id FROM users WHERE id=%s", (assigned_to,))
        if not cur.fetchone():
            cur.close(); conn.close()
            return self._error("Assignee not found.")
        cur.execute(
            "INSERT INTO tasks (name, difficulty, deadline, assigned_to, assigned_by, "
            "assigned_by_name, status, created_at) VALUES (%s,%s,%s,%s,%s,%s,'assigned',%s)",
            (name, difficulty, deadline, assigned_to, s["id"], me["full_name"], now_iso()))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _complete_task(self):
        s = self._require_user()
        if not s:
            return
        d = self._read_json()
        try:
            task_id = int(d.get("task_id"))
        except (TypeError, ValueError):
            return self._error("Bad task id.")
        evidence = d.get("evidence") or ""
        ev_name = (d.get("evidence_name") or "evidence").strip()
        if not evidence.startswith("data:"):
            return self._error("Evidence (image, video, pdf or file) is required.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM tasks WHERE id=%s AND assigned_to=%s", (task_id, s["id"]))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return self._error("Task not found.", 404)
        if row["status"] == "verified":
            cur.close(); conn.close()
            return self._error("This task is already verified.")
        try:
            header, b64 = evidence.split(",", 1)
            raw = base64.b64decode(b64)
        except Exception:
            cur.close(); conn.close()
            return self._error("Could not read the evidence file.")
        mime = header.split(";")[0].replace("data:", "") or "application/octet-stream"
        ext = mimetypes.guess_extension(mime) or ""
        safe = "".join(c for c in os.path.splitext(ev_name)[0]
                       if c.isalnum() or c in "-_")[:40] or "evidence"
        url = upload_blob("evidence", f"task_{task_id}_{secrets.token_hex(4)}_{safe}{ext}", raw, mime)
        cur.execute(
            "UPDATE tasks SET status='submitted', evidence=%s, evidence_type=%s, "
            "submitted_at=%s WHERE id=%s", (url, mime, now_iso(), task_id))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    # -- scoreboard ---------------------------------------------------------
    def _scoreboard(self):
        if not self._session():
            return self._error("Not logged in", 401)
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users WHERE kicked=FALSE")
        users = cur.fetchall()
        rows = []
        for r in users:
            sc = compute_scores(cur, r["id"])
            rows.append({
                "id": r["id"], "full_name": r["full_name"],
                "officer_position": r["officer_position"], "strikes": r["strikes"],
                "attendance_score": sc["attendance_score"], "task_score": sc["task_score"],
                "late_penalty": sc["late_penalty"], "total": sc["total"],
            })
        cur.close(); conn.close()
        rows.sort(key=lambda x: x["total"], reverse=True)
        for i, r in enumerate(rows):
            r["position"] = i + 1
        self._send_json({"scoreboard": rows})

    # -- admin --------------------------------------------------------------
    def _admin_users(self):
        if not self._require_admin():
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users ORDER BY full_name")
        users = cur.fetchall()
        out = []
        for r in users:
            u = user_public(r)
            u.update(compute_scores(cur, r["id"]))
            out.append(u)
        cur.close(); conn.close()
        self._send_json({"users": out})

    def _admin_user_detail(self, path):
        if not self._require_admin():
            return
        try:
            uid = int(path.rsplit("/", 1)[-1])
        except ValueError:
            return self._error("Bad id", 400)
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return self._error("User not found", 404)
        u = user_public(row)
        u.update(compute_scores(cur, uid))
        cur.execute("SELECT * FROM attendance WHERE user_id=%s ORDER BY id DESC", (uid,))
        attendance = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM tasks WHERE assigned_to=%s ORDER BY id DESC", (uid,))
        tasks = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM strike_log WHERE user_id=%s ORDER BY id DESC", (uid,))
        strikes = [dict(r) for r in cur.fetchall()]
        activity = []
        for a in attendance[:15]:
            if a["check_out"]:
                activity.append({"when": a["check_out"],
                                 "what": f"Attended {round(a['hours'] or 0,2)}h (checked out)"})
            else:
                activity.append({"when": a["check_in"], "what": "Checked in"})
        for t in tasks[:15]:
            if t["submitted_at"]:
                activity.append({"when": t["submitted_at"],
                                 "what": f"Submitted task '{t['name']}' ({t['status']})"})
        activity.sort(key=lambda x: x["when"] or "", reverse=True)
        cur.close(); conn.close()
        self._send_json({"user": u, "attendance": attendance, "tasks": tasks,
                         "strikes": strikes, "activity": activity[:20]})

    def _admin_pending(self):
        if not self._require_admin():
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute(
            "SELECT t.*, u.full_name AS assignee_name FROM tasks t "
            "JOIN users u ON u.id=t.assigned_to WHERE t.status='submitted' "
            "ORDER BY t.submitted_at ASC")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        self._send_json({"tasks": rows})

    def _admin_home(self):
        if not self._require_admin():
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()
        total_users = len(users)
        active = sum(1 for u in users if not u["kicked"])
        total_strikes = sum(u["strikes"] for u in users)
        cur.execute("SELECT COUNT(*) c FROM tasks WHERE status='submitted'")
        pending = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM attendance WHERE check_out IS NULL")
        open_att = cur.fetchone()["c"]
        hardest, leaders = [], []
        for u in users:
            if u["kicked"]:
                continue
            sc = compute_scores(cur, u["id"])
            hardest.append({"full_name": u["full_name"], "difficulty": sc["verified_difficulty"]})
            leaders.append({"full_name": u["full_name"], "total": sc["total"],
                            "strikes": u["strikes"]})
        hardest.sort(key=lambda x: x["difficulty"], reverse=True)
        leaders.sort(key=lambda x: x["total"], reverse=True)
        cur.close(); conn.close()
        self._send_json({
            "stats": {"total_users": total_users, "active": active,
                      "total_strikes": total_strikes, "pending_tasks": pending,
                      "checked_in_now": open_att},
            "hardest_workers": hardest[:5], "leaders": leaders[:5],
        })

    def _admin_late_tasks(self):
        if not self._require_admin():
            return
        now = datetime.now()
        conn = get_conn(); cur = cursor(conn)
        cur.execute(
            "SELECT t.*, u.full_name AS assignee_name, u.grade AS assignee_grade "
            "FROM tasks t JOIN users u ON u.id=t.assigned_to "
            "WHERE t.status != 'verified'")
        rows = cur.fetchall()
        cur.close(); conn.close()
        out = []
        for r in rows:
            hl = task_hours_late(r, now)
            if hl is None:
                continue
            d = dict(r)
            d["hours_late"] = hl
            d["submitted_late"] = bool(r["submitted_at"])
            out.append(d)
        out.sort(key=lambda x: x["hours_late"], reverse=True)
        self._send_json({"tasks": out})

    def _admin_verify(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            task_id = int(d.get("task_id"))
        except (TypeError, ValueError):
            return self._error("Bad task id.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT status FROM tasks WHERE id=%s", (task_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return self._error("Task not found", 404)
        if row["status"] != "submitted":
            cur.close(); conn.close()
            return self._error("Task is not awaiting verification.")
        cur.execute("UPDATE tasks SET status='verified' WHERE id=%s", (task_id,))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _admin_return(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            task_id = int(d.get("task_id"))
        except (TypeError, ValueError):
            return self._error("Bad task id.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT status FROM tasks WHERE id=%s", (task_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return self._error("Task not found", 404)
        if row["status"] != "submitted":
            cur.close(); conn.close()
            return self._error("Task is not awaiting verification.")
        cur.execute("UPDATE tasks SET status='returned', return_count=return_count+1 WHERE id=%s",
                    (task_id,))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _admin_verify_all(self):
        if not self._require_admin():
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("UPDATE tasks SET status='verified' WHERE status='submitted'")
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _admin_strike(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        reason = (d.get("reason") or "Unspecified").strip()
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT id FROM users WHERE id=%s", (uid,))
        if not cur.fetchone():
            cur.close(); conn.close()
            return self._error("User not found", 404)
        cur.execute("UPDATE users SET strikes=strikes+1 WHERE id=%s RETURNING strikes", (uid,))
        new = cur.fetchone()["strikes"]
        cur.execute("INSERT INTO strike_log (user_id, reason, created_at) VALUES (%s,%s,%s)",
                    (uid, reason, now_iso()))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True, "strikes": new})

    def _admin_unstrike(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute("UPDATE users SET strikes=GREATEST(0, strikes-1) WHERE id=%s RETURNING strikes",
                    (uid,))
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        if not row:
            return self._error("User not found", 404)
        self._send_json({"ok": True, "strikes": row["strikes"]})

    def _admin_kick(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT strikes FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return self._error("User not found", 404)
        if row["strikes"] < 3:
            cur.close(); conn.close()
            return self._error("A member can only be kicked at 3 strikes.")
        cur.execute("UPDATE users SET kicked=TRUE WHERE id=%s", (uid,))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _admin_delete(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        conn = get_conn(); cur = cursor(conn)
        cur.execute("SELECT id FROM users WHERE id=%s", (uid,))
        if not cur.fetchone():
            cur.close(); conn.close()
            return self._error("User not found", 404)
        cur.execute("DELETE FROM attendance WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM tasks WHERE assigned_to=%s", (uid,))
        cur.execute("DELETE FROM strike_log WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})

    def _admin_clear_all(self):
        if not self._require_admin():
            return
        conn = get_conn(); cur = cursor(conn)
        cur.execute("TRUNCATE strike_log, tasks, attendance, users RESTART IDENTITY CASCADE")
        conn.commit(); cur.close(); conn.close()
        self._send_json({"ok": True})
