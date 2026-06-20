# MontaRanker

A site for keeping our team organized - tracking lab hours, handing out tasks,
and ranking everyone on a scoreboard so people actually show up and get stuff done.

It's written in plain Python (the standard library only) plus some HTML/CSS/JS,
so there's nothing to install to run it locally.

## Running it on your computer

You need Python 3. Check with `py --version`.

Then just double-click `Start MontaRanker.bat`, or run:

```
py server.py
```

and open http://localhost:8000.

If you want other people on the same wifi to reach it, find your IP with
`ipconfig` and give them `http://<your-ip>:8000`. Heads up though - phone cameras
only work over https or on localhost, so attendance check-out from other phones
won't work this way. To get that you need to actually host it (see DEPLOY.md).

Stop it with Ctrl+C.

## Accounts

Sign up with your name, student ID (numbers), grade, and an officer role if you
have one. After that you log in with your **name + password**.

The admin login is on its own tab on the login page. Password is `RaymondIsTheGoat!`
(you can change it at the top of server.py).

## How the points work

**Attendance** - hit "I'm Here" when you get to the lab and "I'm Leaving" when you
go. Leaving makes you take a quick photo with your camera so people can't fake it.
You get 0.1 points per hour.

**Tasks** - officers assign them with a difficulty (1-5) and a deadline. You finish
one by uploading proof (a picture, video, pdf, whatever). Once an admin approves it
you get points:
- turned in on time or early: difficulty + however many full days early you were
- turned in late: difficulty minus 0.2 per hour late
- if a task got sent back twice, no difficulty points when it finally passes

**Scoreboard** ranks everyone by total, split into attendance / tasks / late
penalty. Names turn yellow/orange/red based on strikes.

## Admin stuff

The admin account can see everything - every profile, their attendance photos,
what they've been working on, etc. It also:
- approves or sends back submitted tasks (there's an Approve All button)
- has a Late Tasks tab listing everything overdue and who owns it
- hands out strikes, and can kick someone at 3 strikes
- can delete an account, or wipe everything with Clear All

## Where the data is

- `data/montaranker.db` - the sqlite database (everyone, attendance, tasks, strikes)
- `uploads/attendance/` - the check-out photos
- `uploads/evidence/` - task proof files

Back up `data/` and `uploads/` if you want to keep things. Deleting the .db file
resets the whole thing.

## Files

- `server.py` - the local server
- `public/` - the actual website (login, dashboard, admin)
- `api/index.py` + `vercel.json` + `requirements.txt` - the version that runs on
  Vercel (Postgres + blob storage instead of local files). See DEPLOY.md.
