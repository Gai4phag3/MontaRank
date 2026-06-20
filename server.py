#!/usr/bin/env python3
# MontaRanker - local dev server (Team 115).
# Just runs on the stdlib, no installs. Start it with `py server.py` and open
# http://localhost:8000. Data goes in data/montaranker.db, photos in uploads/.

import http.server
import socketserver
import sqlite3
import json
import os
import secrets
import hashlib
import base64
import math
import mimetypes
from http import cookies
from datetime import datetime
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
UPLOADS = os.path.join(HERE, "uploads")
DB_PATH = os.path.join(HERE, "data", "montaranker.db")
PORT = int(os.environ.get("PORT", "8000"))

ADMIN_PASSWORD = "RaymondIsTheGoat!"

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

os.makedirs(os.path.join(UPLOADS, "attendance"), exist_ok=True)
os.makedirs(os.path.join(UPLOADS, "evidence"), exist_ok=True)
os.makedirs(os.path.join(HERE, "data"), exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            student_id TEXT NOT NULL UNIQUE,
            grade TEXT NOT NULL,
            officer_position TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            strikes INTEGER NOT NULL DEFAULT 0,
            kicked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            check_in TEXT NOT NULL,
            check_out TEXT,
            photo TEXT,
            hours REAL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            difficulty INTEGER NOT NULL,
            deadline TEXT NOT NULL,
            assigned_to INTEGER NOT NULL,
            assigned_by INTEGER NOT NULL,
            assigned_by_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'assigned',
            evidence TEXT,
            evidence_type TEXT,
            submitted_at TEXT,
            return_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (assigned_to) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS strike_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


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


# logged-in tokens live here while the server is up (cleared on restart)
SESSIONS = {}


def compute_scores(conn, user_id):
    attendance_score = 0.0
    total_hours = 0.0
    for row in conn.execute(
        "SELECT hours FROM attendance WHERE user_id=? AND check_out IS NOT NULL",
        (user_id,),
    ):
        h = row["hours"] or 0.0
        total_hours += h
        attendance_score += 0.1 * h

    task_score = 0.0
    late_penalty = 0.0
    verified_difficulty = 0
    for row in conn.execute(
        "SELECT difficulty, deadline, submitted_at, return_count "
        "FROM tasks WHERE assigned_to=? AND status='verified'",
        (user_id,),
    ):
        difficulty = row["difficulty"]
        verified_difficulty += difficulty
        # sent back twice -> no difficulty points
        base = 0 if row["return_count"] >= 2 else difficulty
        deadline = parse_dt(row["deadline"])
        submitted = parse_dt(row["submitted_at"])
        if deadline and submitted:
            delta = (deadline - submitted).total_seconds()
            if delta >= 0:
                days_advance = math.floor(delta / 86400)
                task_score += base + days_advance
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
    # returns how many hours late a task is, or None if it isn't late.
    # already-submitted tasks are judged at submit time; ones still out are
    # judged against now (so the number keeps climbing until they turn it in).
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
        "kicked": row["kicked"],
    }


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _session(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in c:
            return SESSIONS.get(c["sid"].value)
        return None

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
        raw = getattr(self, "_body_raw", b"")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _require_user(self):
        s = self._session()
        if not s or s["type"] != "user":
            self._error("Not logged in", 401)
            return None
        return s

    def _require_admin(self):
        s = self._session()
        if not s or s["type"] != "admin":
            self._error("Admin only", 403)
            return None
        return s

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.handle_api_get(path)
        if path.startswith("/uploads/"):
            return self.serve_upload(path)
        return self.serve_static(path)

    def do_POST(self):
        # read the body up front - if a handler skips it the leftover bytes
        # break the next request on a kept-alive connection
        length = int(self.headers.get("Content-Length", 0) or 0)
        self._body_raw = self.rfile.read(length) if length else b""
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.handle_api_post(path)
        self._error("Not found", 404)

    def serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("\\/")
        full = os.path.join(PUBLIC, safe)
        if not full.startswith(PUBLIC) or not os.path.isfile(full):
            full = os.path.join(PUBLIC, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_upload(self, path):
        if not self._session():
            self._error("Not authorized", 401)
            return
        safe = os.path.normpath(path[len("/uploads/"):]).lstrip("\\/")
        full = os.path.join(UPLOADS, safe)
        if not full.startswith(UPLOADS) or not os.path.isfile(full):
            self._error("Not found", 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_api_get(self, path):
        if path == "/api/officer-positions":
            return self._send_json({"positions": OFFICER_POSITIONS})

        if path == "/api/me":
            s = self._session()
            if not s:
                return self._send_json({"user": None})
            if s["type"] == "admin":
                return self._send_json({"user": {"role": "admin"}})
            conn = get_db()
            row = conn.execute("SELECT * FROM users WHERE id=?", (s["id"],)).fetchone()
            conn.close()
            if not row:
                return self._send_json({"user": None})
            u = user_public(row)
            u["role"] = "member"
            u["is_officer"] = bool(row["officer_position"])
            return self._send_json({"user": u})

        if path == "/api/attendance/status":
            s = self._require_user()
            if not s:
                return
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM attendance WHERE user_id=? AND check_out IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (s["id"],),
            ).fetchone()
            history = [dict(r) for r in conn.execute(
                "SELECT * FROM attendance WHERE user_id=? ORDER BY id DESC LIMIT 25",
                (s["id"],),
            )]
            conn.close()
            return self._send_json({
                "open": dict(row) if row else None,
                "history": history,
            })

        if path == "/api/tasks/mine":
            s = self._require_user()
            if not s:
                return
            conn = get_db()
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM tasks WHERE assigned_to=? ORDER BY deadline ASC",
                (s["id"],),
            )]
            conn.close()
            return self._send_json({"tasks": rows})

        if path == "/api/tasks/assigned-by-me":
            s = self._require_user()
            if not s:
                return
            conn = get_db()
            rows = []
            for r in conn.execute(
                "SELECT t.*, u.full_name AS assignee_name FROM tasks t "
                "JOIN users u ON u.id=t.assigned_to "
                "WHERE t.assigned_by=? ORDER BY t.deadline ASC",
                (s["id"],),
            ):
                rows.append(dict(r))
            conn.close()
            return self._send_json({"tasks": rows})

        if path == "/api/members":
            s = self._require_user()
            if not s:
                return
            conn = get_db()
            rows = [user_public(r) for r in conn.execute(
                "SELECT * FROM users WHERE kicked=0 ORDER BY full_name"
            )]
            conn.close()
            return self._send_json({"members": rows})

        if path == "/api/scoreboard":
            if not self._session():
                return self._error("Not logged in", 401)
            return self._scoreboard()

        if path == "/api/admin/users":
            if not self._require_admin():
                return
            conn = get_db()
            out = []
            for r in conn.execute("SELECT * FROM users ORDER BY full_name"):
                u = user_public(r)
                u.update(compute_scores(conn, r["id"]))
                out.append(u)
            conn.close()
            return self._send_json({"users": out})

        if path.startswith("/api/admin/users/"):
            if not self._require_admin():
                return
            try:
                uid = int(path.rsplit("/", 1)[-1])
            except ValueError:
                return self._error("Bad id", 400)
            return self._admin_user_detail(uid)

        if path == "/api/admin/pending":
            if not self._require_admin():
                return
            conn = get_db()
            rows = []
            for r in conn.execute(
                "SELECT t.*, u.full_name AS assignee_name FROM tasks t "
                "JOIN users u ON u.id=t.assigned_to "
                "WHERE t.status='submitted' ORDER BY t.submitted_at ASC"
            ):
                rows.append(dict(r))
            conn.close()
            return self._send_json({"tasks": rows})

        if path == "/api/admin/home":
            if not self._require_admin():
                return
            return self._admin_home()

        if path == "/api/admin/late-tasks":
            if not self._require_admin():
                return
            return self._admin_late_tasks()

        return self._error("Not found", 404)

    def handle_api_post(self, path):
        if path == "/api/signup":
            return self._signup()
        if path == "/api/login":
            return self._login()
        if path == "/api/admin-login":
            return self._admin_login()
        if path == "/api/logout":
            return self._logout()

        if path == "/api/attendance/checkin":
            return self._checkin()
        if path == "/api/attendance/checkout":
            return self._checkout()

        if path == "/api/tasks/create":
            return self._create_task()
        if path == "/api/tasks/complete":
            return self._complete_task()

        if path == "/api/admin/verify":
            return self._admin_verify()
        if path == "/api/admin/return":
            return self._admin_return()
        if path == "/api/admin/verify-all":
            return self._admin_verify_all()
        if path == "/api/admin/strike":
            return self._admin_strike()
        if path == "/api/admin/unstrike":
            return self._admin_unstrike()
        if path == "/api/admin/kick":
            return self._admin_kick()
        if path == "/api/admin/delete":
            return self._admin_delete()
        if path == "/api/admin/clear-all":
            return self._admin_clear_all()

        return self._error("Not found", 404)

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
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (full_name, student_id, grade, officer_position, "
                "password_hash, salt, created_at) VALUES (?,?,?,?,?,?,?)",
                (full_name, student_id, grade, officer, h, salt, now_iso()),
            )
            conn.commit()
            uid = cur.lastrowid
        except sqlite3.IntegrityError:
            conn.close()
            return self._error("That student ID is already registered.")
        conn.close()

        token = secrets.token_hex(24)
        SESSIONS[token] = {"type": "user", "id": uid}
        cookie = f"sid={token}; Path=/; HttpOnly; SameSite=Lax"
        self._send_json({"ok": True, "role": "member"}, set_cookie=cookie)

    def _login(self):
        d = self._read_json()
        full_name = (d.get("full_name") or "").strip()
        password = d.get("password") or ""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM users WHERE lower(full_name)=lower(?)", (full_name,)
        ).fetchall()
        conn.close()
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
        token = secrets.token_hex(24)
        SESSIONS[token] = {"type": "user", "id": row["id"]}
        cookie = f"sid={token}; Path=/; HttpOnly; SameSite=Lax"
        self._send_json({"ok": True, "role": "member"}, set_cookie=cookie)

    def _admin_login(self):
        d = self._read_json()
        if (d.get("password") or "") != ADMIN_PASSWORD:
            return self._error("Incorrect administrator password.", 401)
        token = secrets.token_hex(24)
        SESSIONS[token] = {"type": "admin", "id": None}
        cookie = f"sid={token}; Path=/; HttpOnly; SameSite=Lax"
        self._send_json({"ok": True, "role": "admin"}, set_cookie=cookie)

    def _logout(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in c:
            SESSIONS.pop(c["sid"].value, None)
        self._send_json({"ok": True}, set_cookie="sid=; Path=/; Max-Age=0")

    def _checkin(self):
        s = self._require_user()
        if not s:
            return
        conn = get_db()
        open_row = conn.execute(
            "SELECT * FROM attendance WHERE user_id=? AND check_out IS NULL",
            (s["id"],),
        ).fetchone()
        if open_row:
            conn.close()
            return self._error("You are already checked in. Check out first.")
        conn.execute(
            "INSERT INTO attendance (user_id, check_in) VALUES (?,?)",
            (s["id"], now_iso()),
        )
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _checkout(self):
        s = self._require_user()
        if not s:
            return
        d = self._read_json()
        photo = d.get("photo") or ""
        if not photo.startswith("data:image/"):
            return self._error("A camera photo is required to check out.")
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM attendance WHERE user_id=? AND check_out IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (s["id"],),
        ).fetchone()
        if not row:
            conn.close()
            return self._error("You are not checked in.")

        try:
            header, b64 = photo.split(",", 1)
            raw = base64.b64decode(b64)
        except Exception:
            conn.close()
            return self._error("Could not read the photo.")
        fname = f"att_{row['id']}_{secrets.token_hex(4)}.png"
        with open(os.path.join(UPLOADS, "attendance", fname), "wb") as f:
            f.write(raw)

        check_in = parse_dt(row["check_in"])
        check_out = datetime.now()
        hours = max(0.0, (check_out - check_in).total_seconds() / 3600.0)
        conn.execute(
            "UPDATE attendance SET check_out=?, photo=?, hours=? WHERE id=?",
            (check_out.isoformat(timespec="seconds"), f"attendance/{fname}",
             round(hours, 3), row["id"]),
        )
        conn.commit()
        conn.close()
        self._send_json({"ok": True, "hours": round(hours, 2),
                         "points": round(0.1 * hours, 3)})

    def _create_task(self):
        s = self._require_user()
        if not s:
            return
        conn = get_db()
        me = conn.execute("SELECT * FROM users WHERE id=?", (s["id"],)).fetchone()
        if not me or not me["officer_position"]:
            conn.close()
            return self._error("Only officers can assign tasks.", 403)
        d = self._read_json()
        name = (d.get("name") or "").strip()
        try:
            difficulty = int(d.get("difficulty"))
        except (TypeError, ValueError):
            conn.close()
            return self._error("Difficulty must be a number 1-5.")
        deadline = (d.get("deadline") or "").strip()
        try:
            assigned_to = int(d.get("assigned_to"))
        except (TypeError, ValueError):
            conn.close()
            return self._error("Pick a person to assign.")
        if not name:
            conn.close()
            return self._error("Task name is required.")
        if difficulty < 1 or difficulty > 5:
            conn.close()
            return self._error("Difficulty must be between 1 and 5.")
        if not parse_dt(deadline):
            conn.close()
            return self._error("A valid deadline (date + time) is required.")
        target = conn.execute("SELECT * FROM users WHERE id=?", (assigned_to,)).fetchone()
        if not target:
            conn.close()
            return self._error("Assignee not found.")
        conn.execute(
            "INSERT INTO tasks (name, difficulty, deadline, assigned_to, assigned_by, "
            "assigned_by_name, status, created_at) VALUES (?,?,?,?,?,?, 'assigned', ?)",
            (name, difficulty, deadline, assigned_to, s["id"],
             me["full_name"], now_iso()),
        )
        conn.commit()
        conn.close()
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
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM tasks WHERE id=? AND assigned_to=?", (task_id, s["id"])
        ).fetchone()
        if not row:
            conn.close()
            return self._error("Task not found.", 404)
        if row["status"] == "verified":
            conn.close()
            return self._error("This task is already verified.")

        try:
            header, b64 = evidence.split(",", 1)
            raw = base64.b64decode(b64)
        except Exception:
            conn.close()
            return self._error("Could not read the evidence file.")
        mime = header.split(";")[0].replace("data:", "") or "application/octet-stream"
        ext = mimetypes.guess_extension(mime) or ""
        safe_base = "".join(c for c in os.path.splitext(ev_name)[0] if c.isalnum() or c in "-_")[:40] or "evidence"
        fname = f"task_{task_id}_{secrets.token_hex(4)}_{safe_base}{ext}"
        with open(os.path.join(UPLOADS, "evidence", fname), "wb") as f:
            f.write(raw)

        conn.execute(
            "UPDATE tasks SET status='submitted', evidence=?, evidence_type=?, "
            "submitted_at=? WHERE id=?",
            (f"evidence/{fname}", mime, now_iso(), task_id),
        )
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _scoreboard(self):
        conn = get_db()
        rows = []
        for r in conn.execute("SELECT * FROM users WHERE kicked=0"):
            sc = compute_scores(conn, r["id"])
            rows.append({
                "id": r["id"],
                "full_name": r["full_name"],
                "officer_position": r["officer_position"],
                "strikes": r["strikes"],
                "attendance_score": sc["attendance_score"],
                "task_score": sc["task_score"],
                "late_penalty": sc["late_penalty"],
                "total": sc["total"],
            })
        conn.close()
        rows.sort(key=lambda x: x["total"], reverse=True)
        for i, r in enumerate(rows):
            r["position"] = i + 1
        self._send_json({"scoreboard": rows})

    def _admin_user_detail(self, uid):
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            conn.close()
            return self._error("User not found", 404)
        u = user_public(row)
        u.update(compute_scores(conn, uid))
        attendance = [dict(r) for r in conn.execute(
            "SELECT * FROM attendance WHERE user_id=? ORDER BY id DESC", (uid,)
        )]
        tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM tasks WHERE assigned_to=? ORDER BY id DESC", (uid,)
        )]
        strikes = [dict(r) for r in conn.execute(
            "SELECT * FROM strike_log WHERE user_id=? ORDER BY id DESC", (uid,)
        )]
        # build a little recent-activity feed from attendance + tasks
        activity = []
        for a in attendance[:15]:
            if a["check_out"]:
                activity.append({"when": a["check_out"], "what":
                                 f"Attended {round(a['hours'] or 0,2)}h (checked out)"})
            else:
                activity.append({"when": a["check_in"], "what": "Checked in"})
        for t in tasks[:15]:
            if t["submitted_at"]:
                activity.append({"when": t["submitted_at"],
                                 "what": f"Submitted task '{t['name']}' ({t['status']})"})
        activity.sort(key=lambda x: x["when"] or "", reverse=True)
        conn.close()
        self._send_json({
            "user": u,
            "attendance": attendance,
            "tasks": tasks,
            "strikes": strikes,
            "activity": activity[:20],
        })

    def _admin_home(self):
        conn = get_db()
        users = conn.execute("SELECT * FROM users").fetchall()
        total_users = len(users)
        active = sum(1 for u in users if not u["kicked"])
        total_strikes = sum(u["strikes"] for u in users)
        pending = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE status='submitted'"
        ).fetchone()["c"]
        open_att = conn.execute(
            "SELECT COUNT(*) c FROM attendance WHERE check_out IS NULL"
        ).fetchone()["c"]

        hardest = []
        leaders = []
        for u in users:
            if u["kicked"]:
                continue
            sc = compute_scores(conn, u["id"])
            hardest.append({"full_name": u["full_name"],
                            "difficulty": sc["verified_difficulty"]})
            leaders.append({"full_name": u["full_name"], "total": sc["total"],
                            "strikes": u["strikes"]})
        hardest.sort(key=lambda x: x["difficulty"], reverse=True)
        leaders.sort(key=lambda x: x["total"], reverse=True)
        conn.close()
        self._send_json({
            "stats": {
                "total_users": total_users,
                "active": active,
                "total_strikes": total_strikes,
                "pending_tasks": pending,
                "checked_in_now": open_att,
            },
            "hardest_workers": hardest[:5],
            "leaders": leaders[:5],
        })

    def _admin_late_tasks(self):
        now = datetime.now()
        conn = get_db()
        rows = conn.execute(
            "SELECT t.*, u.full_name AS assignee_name, u.grade AS assignee_grade "
            "FROM tasks t JOIN users u ON u.id=t.assigned_to "
            "WHERE t.status != 'verified'"
        ).fetchall()
        conn.close()
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
        conn = get_db()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            conn.close()
            return self._error("Task not found", 404)
        if row["status"] != "submitted":
            conn.close()
            return self._error("Task is not awaiting verification.")
        conn.execute("UPDATE tasks SET status='verified' WHERE id=?", (task_id,))
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _admin_return(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            task_id = int(d.get("task_id"))
        except (TypeError, ValueError):
            return self._error("Bad task id.")
        conn = get_db()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            conn.close()
            return self._error("Task not found", 404)
        if row["status"] != "submitted":
            conn.close()
            return self._error("Task is not awaiting verification.")
        conn.execute(
            "UPDATE tasks SET status='returned', return_count=return_count+1 WHERE id=?",
            (task_id,),
        )
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _admin_verify_all(self):
        if not self._require_admin():
            return
        conn = get_db()
        conn.execute("UPDATE tasks SET status='verified' WHERE status='submitted'")
        conn.commit()
        conn.close()
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
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            conn.close()
            return self._error("User not found", 404)
        conn.execute("UPDATE users SET strikes=strikes+1 WHERE id=?", (uid,))
        conn.execute(
            "INSERT INTO strike_log (user_id, reason, created_at) VALUES (?,?,?)",
            (uid, reason, now_iso()),
        )
        conn.commit()
        new = conn.execute("SELECT strikes FROM users WHERE id=?", (uid,)).fetchone()["strikes"]
        conn.close()
        self._send_json({"ok": True, "strikes": new})

    def _admin_unstrike(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        conn = get_db()
        conn.execute(
            "UPDATE users SET strikes=MAX(0, strikes-1) WHERE id=?", (uid,)
        )
        conn.commit()
        new = conn.execute("SELECT strikes FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
        if not new:
            return self._error("User not found", 404)
        self._send_json({"ok": True, "strikes": new["strikes"]})

    def _admin_kick(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            conn.close()
            return self._error("User not found", 404)
        if row["strikes"] < 3:
            conn.close()
            return self._error("A member can only be kicked at 3 strikes.")
        conn.execute("UPDATE users SET kicked=1 WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _admin_delete(self):
        if not self._require_admin():
            return
        d = self._read_json()
        try:
            uid = int(d.get("user_id"))
        except (TypeError, ValueError):
            return self._error("Bad user id.")
        conn = get_db()
        row = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            conn.close()
            return self._error("User not found", 404)
        # clear their child rows first or the foreign keys complain
        conn.execute("DELETE FROM attendance WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM tasks WHERE assigned_to=?", (uid,))
        conn.execute("DELETE FROM strike_log WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _admin_clear_all(self):
        if not self._require_admin():
            return
        conn = get_db()
        conn.execute("DELETE FROM strike_log")
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM attendance")
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()
        self._send_json({"ok": True})


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    init_db()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"MontaRanker running on http://localhost:{PORT}")
    print(f"Admin password: {ADMIN_PASSWORD}")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
