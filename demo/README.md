# Local product walkthrough

This directory contains the version-controlled API used to preview the real
React product without production credentials or persistence. It is deliberately
separate from `app/`: all data is reset when the process exits and no model,
queue, database, or user account is touched.

From the repository root, start the API:

```powershell
.venv\Scripts\python.exe demo\notebook_demo_api.py
```

Then start the frontend in a second terminal:

```powershell
corepack pnpm --dir web dev
```

Open `http://127.0.0.1:5173/library`. The three preloaded conversations and
their timestamped citations are grounded in the public captions of the three
preloaded YouTube videos. New questions are matched only to those three bounded
examples; unsupported questions return an explicit `not_found` response.
