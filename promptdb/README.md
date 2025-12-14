# promptdb

Builds a single SQLite database containing prompts from:
- `data/patterns/` (Fabric patterns: `system.md` + `user.md` per pattern)
- `Other-Usful-Prompt-Repos/` (multiple GitHub prompt repos)

## Output
- SQLite file: `promptdb/prompts.sqlite`
- Table: `prompts`
  - Required fields: `title`, `body`
  - Also stores: `source`, `source_repo`, `source_path`, `body_sha256`, `imported_at`

## Run
From the repo root:

```powershell
python .\promptdb\import_prompts.py --reset
```

Optional:

```powershell
python .\promptdb\import_prompts.py --db promptdb\my_prompts.sqlite --reset
```
