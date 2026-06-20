# Deploying MontaRanker to Vercel (free)

This hosts the app as a real always-on website with HTTPS — which also makes the
attendance **camera work on phones**. It uses three free services:

| Piece            | Service              | Cost |
|------------------|----------------------|------|
| Hosting + API    | Vercel               | Free |
| Database         | Neon Postgres        | Free |
| Photo/file store | Vercel Blob          | Free |

You only click through dashboards and set a few values — all the code is done.

---

## What's in this project

```
api/index.py        <- the whole backend, one serverless function
public/             <- the website (served as static files)
vercel.json         <- routes every /api/* request to api/index.py
requirements.txt    <- Python packages Vercel installs (psycopg2, vercel_blob)
server.py           <- LOCAL dev version only (SQLite). Not used on Vercel.
```

---

## Step 1 — Put the project on GitHub

Vercel deploys from a Git repo (easiest path).

1. Create a new repo on GitHub (e.g. `montaranker`), private is fine.
2. In this folder:
   ```
   git init
   git add .
   git commit -m "MontaRanker"
   git branch -M main
   git remote add origin https://github.com/YOUR-NAME/montaranker.git
   git push -u origin main
   ```
   (`.gitignore` already keeps your local `data/` and `uploads/` out of the repo.)

---

## Step 2 — Create the database (Neon)

1. Go to **https://neon.tech**, sign up (free), create a project.
2. Open the project → **Connection string** → copy the **pooled** connection
   string. It looks like:
   ```
   postgresql://user:password@ep-xxxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
   Keep it for Step 4. (The tables are created automatically on first request.)

> You can also use Vercel's own Postgres (Storage tab) — either works. If you do,
> Vercel sets `DATABASE_URL` for you and you can skip adding it manually.

---

## Step 3 — Import the project into Vercel

1. Go to **https://vercel.com**, sign up with GitHub.
2. **Add New… → Project** → import your `montaranker` repo.
3. Framework preset: **Other** (leave build settings empty — there's no build step).
4. Don't deploy yet — add the environment variables first (Step 4). Or deploy,
   then add them and redeploy.

---

## Step 4 — Add the Blob store and environment variables

**Blob store:**
1. In your Vercel project → **Storage** → **Create Database** → **Blob** → connect it.
2. This automatically adds the `BLOB_READ_WRITE_TOKEN` env var to the project.

**Environment variables** (Project → **Settings → Environment Variables**), add:

| Name           | Value                                                        |
|----------------|--------------------------------------------------------------|
| `DATABASE_URL` | the Neon pooled connection string from Step 2                |
| `SECRET_KEY`   | any long random string (signs login cookies) — make one up   |
| `ADMIN_PASSWORD` | *(optional)* overrides the default `RaymondIsTheGoat!`     |

Apply them to **Production** (and Preview if you want).

> `BLOB_READ_WRITE_TOKEN` is added for you by the Blob store — don't set it by hand.

---

## Step 5 — Deploy

Click **Deploy** (or push a commit / hit **Redeploy** if you added env vars after the
first deploy). When it finishes you'll get a URL like
`https://montaranker.vercel.app`.

- Open it → sign up / log in as a member.
- Admin: the **Administrator** tab, password = your `ADMIN_PASSWORD`
  (default `RaymondIsTheGoat!`).

Share the URL with the team. Camera attendance works on phones because the site is
HTTPS.

---

## Notes & limits (all on the free tier)

- **Upload size:** Vercel serverless requests are capped at ~4.5 MB, so task
  evidence files must be under ~4 MB (the app blocks larger ones and tells the
  user). Attendance photos are auto-compressed to well under that. For big videos,
  upload them somewhere (Drive/YouTube) and submit a PDF/screenshot with the link.
- **Uploaded files are public-by-URL:** Blob URLs are unguessable (random suffix)
  but anyone with the exact link can open the image. Fine for a team tool; don't
  treat photos as private.
- **Database/Blob free limits:** Neon free ≈ 0.5 GB, Vercel Blob free ≈ 1 GB —
  plenty for a season of a team's photos. Compressed photos are ~100–200 KB each.
- **Changing the admin password:** set the `ADMIN_PASSWORD` env var (don't edit code).
- **Resetting everything:** drop the tables in the Neon SQL editor (they'll be
  recreated empty on the next request).

---

## Local development still works

`server.py` (SQLite + local disk) is unchanged — run `py server.py` for offline
testing on your own machine. The hosted Vercel app and the local app share the same
frontend but use separate storage.
