# MontaRanker — FRC Team 115

A ranking & organization web app for a robotics team: attendance tracking with
camera verification, officer-assigned tasks, a points-based scoreboard, and a
full administrator panel.

Built with **only the Python standard library** — no installs, no Node, no build step.

> **Want it online for the whole team (free, HTTPS, camera works on phones)?**
> See **[DEPLOY.md](DEPLOY.md)** to host it on Vercel + Neon Postgres + Vercel Blob.
> The instructions below are for running it locally on one machine.

---

## Running it locally

1. Make sure Python 3 is installed (you have 3.14). Check with `py --version`.
2. Double-click **`Start MontaRanker.bat`** — it launches the server and opens
   your browser. (Or run `py server.py` in this folder.)
3. Open **http://localhost:8000**.

To let teammates connect from their own laptops/phones on the same Wi-Fi, find
this computer's IP (`ipconfig`) and have them open `http://YOUR-IP:8000`.
The camera requires `localhost` or HTTPS in most browsers, so for attendance
check-out other devices may need to use the host machine — see "Camera note".

Stop the server with **Ctrl+C** in the window (or just close it).

---

## Accounts

- **Sign up** with full name, student ID (numbers only), grade, and an optional
  officer position. Log in afterward with **student ID + password**.
- **Administrator**: on the login page, open the *Administrator* tab and enter
  the password: **`RaymondIsTheGoat!`**

> Change the admin password by editing `ADMIN_PASSWORD` near the top of `server.py`.

---

## How it works

### Attendance (0.1 pts / hour)
- Click **I'm Here** to check in.
- Click **I'm Leaving**, then **take a camera photo** in the lab to check out
  (camera only — there is no file-upload option, to discourage faking).
  Hours are computed automatically and the photo is saved for the admin to review.

### Tasks
- **Officers** (anyone with an officer position) can assign tasks: name,
  difficulty (1–5), and a deadline (date + time).
- Members complete a task by submitting **evidence** (image, video, PDF, or any file).
- After an admin verifies it, points are added:
  - **On time / early:** `difficulty + whole days submitted early`
  - **Late:** `difficulty − 0.2 × hours late` (the late penalty shows separately)
  - A task **sent back twice** earns **no difficulty points** when finally approved.

### Scoreboard
Ranks everyone by total score and breaks it down into Attendance, Tasks, and
Late Penalty. Names are color-coded by strikes (1 = yellow, 2 = orange, 3 = red).

### Administrator
- **Home:** live stats + hardest workers (by verified task difficulty) + leaders.
- **Verifications:** approve or send back each submitted task; **Approve All** button.
- **Members:** open any profile to see their score breakdown, **attendance photos**
  (to catch faking), recent **activity**, **task completions** with evidence, and to
  issue **strikes** or **kick** a member (allowed only at 3 strikes).

---

## Where data lives

- `data/montaranker.db` — SQLite database (all users, attendance, tasks, strikes).
- `uploads/attendance/` — check-out camera photos.
- `uploads/evidence/` — task completion evidence files.

Back up the `data/` and `uploads/` folders to keep everything. Deleting
`data/montaranker.db` resets the whole app.

---

## Camera note

Browsers only allow camera access over **https** or on **localhost**. On the
host computer (http://localhost:8000) it works out of the box. If you deploy to
other devices over the network and the camera is blocked, you'll need to serve
over HTTPS (e.g. behind a reverse proxy) or do attendance check-out on the host
machine.
