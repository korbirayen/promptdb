# promptdb

`promptdb` is a tiny “prompt warehouse” you can run locally.

It takes a bunch of prompt files (from folders you control), normalizes them, and packs them into a single SQLite database with enough provenance to trace every row back to where it came from. Then it serves a simple web UI so you can search, filter, and copy prompts without opening a pile of markdown files.

There are two parts, but you only *need* the importer:

1) Importer: `import_prompts.py` builds/refreshes the SQLite DB and generates a baked offline web bundle
2) Optional server: `server.py` serves the same UI + an API if you prefer running it that way

## What you get

- A SQLite database at `promptdb/prompts.sqlite` (plus WAL files while it’s open)
- A single table called `prompts` with:
  - `title` and `body` (the actual prompt)
  - `source`, `source_repo`, `source_path` (where it came from)
  - `body_sha256` (dedupe/helpful for tracking changes)
  - `imported_at` (UTC timestamp)

Schema lives in `promptdb/schema.sql`.

## Requirements

- Python 3.10+ (uses only the standard library)
- No Node.js, no build step for the web UI

## Import prompts into SQLite

From the repo root:

```powershell
python .\promptdb\import_prompts.py --reset
```

That command:

- Creates (or recreates) `promptdb/prompts.sqlite`
- Applies schema from `promptdb/schema.sql`
- Imports prompts from the folders described below
- Writes `promptdb/web/data.js` (a generated bundle the UI can use offline)

If you don’t want the offline bundle (DB-only import), run:

```powershell
python .\promptdb\import_prompts.py --no-export-web
```

### Where the importer looks for prompts

The importer is opinionated on purpose: it expects a couple of well-known input folders at the repo root.

#### 1) “Pattern” folders (Fabric-style)

Locations:

- `promptdb/patterns/` (ships with this repo)
- `data/patterns/` (optional, if you want your own folder outside the code directory)

Each pattern is a directory. The importer looks for:

- `system.md` (optional)
- `user.md` (optional)

If either file exists, it becomes a prompt. The `body` is stored as:

```
# system
...contents...

# user
...contents...
```

If both folders exist, it will import from both (the DB has a uniqueness constraint to avoid duplicates on re-import).

#### 2) External prompt repos (optional)

Default location:

- `Other-Usful-Prompt-Repos/`

If that folder exists, the importer will walk each subfolder and pull in text-ish files:

- `.md`, `.txt`, `.prompt`, `.yml`, `.yaml`, `.json`, `.toml`

It skips common junk (`.git`, `node_modules`, build outputs) and ignores very large files.

Special case:

- If a repo folder contains `awesome-chatgpt-prompts-main/prompts.csv`, the importer also reads that CSV.

#### 3) Prompting strategies (ships with this repo)

- `promptdb/strategies/*.json`

These are imported as prompts too, so they show up in the UI under the `strategies` source.

### Import into a custom DB path

```powershell
python .\promptdb\import_prompts.py --db promptdb\my_prompts.sqlite --reset
```

## Run the web interface (no server)

The web UI is just static files in `promptdb/web/`. After you run the importer once, you can open the UI directly.

Then open this file in your browser:

- `promptdb\web\index.html`

Because the prompt data is baked into `promptdb/web/data.js`, the page does not need to fetch anything and it does not need a local HTTP server.

Notes:

- The UI is paginated. Use the Prev/Next buttons at the bottom of the left pane.
- If you re-run the importer and don’t see changes, refresh the page (a hard refresh if your browser is being stubborn).

## Optional: run the server

If you *want* an HTTP server (or you don’t want to generate `data.js`), you can still run the built-in server.

```powershell
python .\promptdb\server.py
```

Open:

- http://127.0.0.1:7070/

By default, the server reads `promptdb/prompts.sqlite`.

### Configure host/port/db (optional)

The server is configured via environment variables:

- `PROMPTDB_HOST` (default: `127.0.0.1`)
- `PROMPTDB_PORT` (default: `7070`)
- `PROMPTDB_DB` (default: `promptdb/prompts.sqlite`)

Example:

```powershell
$env:PROMPTDB_DB = "C:\\path\\to\\my_prompts.sqlite"
$env:PROMPTDB_PORT = "8080"
python .\promptdb\server.py
```

## API (what the web UI calls)

You don’t need to use these directly, but they’re handy for scripting:

- `GET /api/health`
- `GET /api/stats`
- `GET /api/sources`
- `GET /api/prompts?q=...&source=...&repo=...&limit=50&offset=0`
- `GET /api/prompts/{id}`

## Troubleshooting

- “db not reachable” in the header:
  - The server can’t find the SQLite file. Set `PROMPTDB_DB` to the right path, or run the importer first.

- “0 prompts” after importing:
  - Check that `promptdb/patterns/` exists and contains pattern subfolders.
  - If you’re relying on your own prompts, make sure `data/patterns/` and/or `Other-Usful-Prompt-Repos/` exists.

- Port already in use:
  - Change `PROMPTDB_PORT`.

## Notes

- This is intentionally simple: standard library only, SQLite only, easy to run on a laptop.
- The importer uses a unique index on `(source, repo, path)` so re-imports don’t create duplicates.
