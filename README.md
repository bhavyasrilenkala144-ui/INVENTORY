# Stockroom: inventory management website (Python + Flask)

Track products, stock levels and every stock movement. Runs on your computer with no setup
(SQLite) and deploys to Vercel with a Postgres database.

```
app.py              Flask app: pages, login, database code
templates/          HTML pages
templates_bundle.py Generated copy of the pages + CSS (so Vercel always has them)
build_templates.py  Regenerates templates_bundle.py
public/style.css    Styling (Vercel serves this folder directly)
requirements.txt    Python packages
.vscode/            VS Code debug setup
.env.example        Settings you can copy to .env
```

## Part 1: Run it in VS Code

1. Install **Python 3.10+** and **VS Code** (with the Python extension).
2. In VS Code: **File > Open Folder**, choose this folder.
3. Open the terminal (**Terminal > New Terminal**) and run:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   python app.py --demo
   ```
   (On Windows, use `py` instead of `python` if `python` isn't found.)
4. Open http://127.0.0.1:5000. `--demo` adds sample products on the first run.

Press **F5** to run with the debugger instead (uses `.vscode/launch.json`).

Locally there is no login unless you set `ADMIN_PASSWORD` (see `.env.example`).

## Part 2: Deploy to Vercel

Vercel doesn't keep files between requests, so the online version needs a real database.
Follow the steps in order.

1. **Put the code on GitHub.** In the VS Code terminal:
   ```bash
   git init
   git add .
   git commit -m "Stockroom"
   ```
   Create an empty repository on github.com, then run the `git remote add origin ...` and
   `git push -u origin main` commands GitHub shows you.
2. **Import the project.** On vercel.com choose **Add New > Project**, pick your repository
   and click **Deploy**. Vercel detects Flask automatically (`app.py` is the entry point).
3. **Open the site once.** It will show a login page. That's expected: you haven't set a
   password yet.
4. **Connect a database.** In your Vercel project open the **Storage** tab, create a
   **Postgres** database (Neon is offered there) and connect it to the project. Vercel adds a
   `DATABASE_URL` (or `POSTGRES_URL`) setting for you; the app accepts either. If you use another Postgres host (Neon, Supabase, etc.),
   add `DATABASE_URL` yourself under **Settings > Environment Variables** using that host's
   connection string.
5. **Add your settings.** Under **Settings > Environment Variables** add:

   | Name | Value |
   |------|-------|
   | `ADMIN_PASSWORD` | the password you will sign in with |
   | `SECRET_KEY` | a long random string (keeps logins secure) |
   | `APP_TIMEZONE` | e.g. `Asia/Kolkata` (times are shown in this zone) |
   | `SEED_DEMO` | `1` once, if you want sample products (remove it afterwards) |

6. **Redeploy.** In **Deployments**, open the latest one and choose **Redeploy**. Then sign in
   with your password. Tables are created automatically on the first request.

From then on, every `git push` redeploys the site.

Prefer the command line? `npm i -g vercel`, then `vercel` (preview) and `vercel --prod`.

## Editing the pages

The pages live in `templates/` and `public/style.css`. After you change them, run
`python build_templates.py` once and commit the updated `templates_bundle.py` too, so the
deployed site picks up your changes.

## Features

- Dashboard: products, units on hand, stock value, items needing restock, latest movements
- Products: search, filter by category or stock level, add, edit, delete
- Stock in / out with a note; removing more than you have is blocked
- Full stock history per product and across the store
- CSV export at `/export.csv`
- Password login (always on when deployed to Vercel)

## Troubleshooting

- **`TemplateNotFound` in the Vercel logs:** run `python build_templates.py`, commit and push
  `templates_bundle.py`.
- **See the real error on screen:** add `SHOW_ERRORS` = `1` under Environment Variables, redeploy,
  repeat the action that failed, and the error page will show the cause. Remove it afterwards.
- **"Something went wrong" page online:** open your project's **Logs** in Vercel. Most often
  `DATABASE_URL` is missing or wrong.
- **Yellow "Data is stored temporarily" banner:** no `DATABASE_URL` is set, so nothing is saved.
  Do step 4.
- **Can't sign in:** check `ADMIN_PASSWORD` is set, then redeploy (changes only apply to new
  deployments).
- **`ModuleNotFoundError` locally:** activate the virtual environment and run
  `pip install -r requirements.txt` again.
