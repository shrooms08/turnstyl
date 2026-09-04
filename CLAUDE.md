# turnstyl — standing rules

These apply to every change in this repo. No exceptions without being asked.

1. **Python 3.12 via uv only.** Install with `uv pip install`, run with
   `.venv/bin/python` or `uv run`. Do not use system pip, poetry, or conda.
2. **No TypeScript.** No Node, no npm, no package.json anywhere in this project.
3. **Never print or commit `.env`.** Do not `cat`, `echo`, `head`, or otherwise
   display `.env` or any private key or wallet secret, and never stage it.
   `.gitignore` excludes it — keep it that way.
4. **The memory DB lives at `./data/turnstyl.db`** (project-local, gitignored).
   Never point the client at `~/.sibyl-memory/memory.db`.
5. **Run scripts with `.venv/bin/python`**, e.g.
   `.venv/bin/python scripts/smoke_memory.py read`.
6. **Every change must keep `scripts/smoke_memory.py` and
   `scripts/demo_offline.py` passing.** Run the smoke test's two modes as
   separate processes (`write` then `read`), then run the offline demo, before
   calling any change done.
7. **The product name is spelled `turnstyl`** — lowercase, no `e` — everywhere:
   code, docs, commits, UI, and the package name.
8. Scripts must fail loudly with a message an operator can act on. No silent
   `except: pass`, no bare exits.
