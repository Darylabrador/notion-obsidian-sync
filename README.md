# notion-obsidian-sync

One-way, read-only synchronization from **Notion** into an **Obsidian** vault.
Notion is always the source of truth. This tool never writes to Notion, and
it only ever writes inside the Obsidian folder you configure.

Runs locally on Linux or Windows, for free, with no third-party automation
service (no Zapier/Make/n8n Cloud).

- [How it works](#how-it-works)
- [1. Set up the Notion integration](#1-set-up-the-notion-integration)
- [2. Install — Linux](#2-install--linux)
- [3. Install — Windows](#3-install--windows)
- [4. Configuration](#4-configuration)
- [5. Usage](#5-usage)
- [6. Automation](#6-automation)
- [7. How content is organized](#7-how-content-is-organized)
- [8. Conflict and safety model](#8-conflict-and-safety-model)
- [9. Encrypting the vault with git-crypt (optional)](#9-encrypting-the-vault-with-git-crypt-optional)
- [10. Troubleshooting](#10-troubleshooting)
- [11. Uninstalling](#11-uninstalling)
- [12. Security](#12-security)
- [13. Development](#13-development)

## How it works

1. You create a read-only Notion integration and share a page or database
   with it.
2. You point the tool at your Obsidian vault and a subfolder inside it.
3. Running `notion-obsidian-sync sync` walks the pages/databases you selected,
   compares each page's `last_edited_time` against a local SQLite state file,
   and only re-downloads and re-converts pages that are new or changed.
4. Notion blocks are converted to Obsidian-flavored Markdown, properties
   become YAML frontmatter, images/files are downloaded locally, and links
   between synced Notion pages become `[[wikilinks]]`.

All writes are sandboxed to `OBSIDIAN_VAULT_PATH/OBSIDIAN_SYNC_FOLDER`. The
tool never calls a Notion write endpoint (see [Security](#12-security)).

## 1. Set up the Notion integration

1. Go to <https://app.notion.com/developers/connections> and click **New
   connection** (this is the same thing referred to elsewhere as an
   "integration" — <https://www.notion.so/my-integrations> redirects here
   too).
2. Give it a name (e.g. "Obsidian Sync") and pick the workspace to connect
   it to.
3. Under **Capabilities**, enable **only** the read permission — **Read
   content** (and, if you want author/page-owner names in your frontmatter,
   optionally **Read user information without email**). Leave every write
   capability (**Update content**, **Insert content**, **Delete content**, ...)
   **unchecked** — this tool never needs write access, and leaving those off
   means it *cannot* modify Notion even if something in the pipeline goes
   wrong.
4. Save, then copy the generated **Internal Integration Secret** (starts
   with `ntn_` or `secret_`) — this is your `NOTION_TOKEN`.
5. Still on that connection's page, open the **Content Access** (previously
   "Access") tab and add what it may read:
   - Click **Add pages/databases**, search for and select the specific
     page(s) and/or database(s) you want synced. Everything nested under a
     selected page is automatically included too — no need to add its
     children individually.
   - Alternatively, as a workspace owner, some workspaces let you connect
     the integration to the **entire workspace** from this same tab — the
     simplest option if you're using `NOTION_SYNC_WORKSPACE=true` (see
     below) and don't want to hand-pick pages.
   - You can also grant access from inside a page itself: open it, click the
     `...` menu → **Connections** → add your integration — equivalent to
     adding it from the Content Access tab.
6. To find a page or database ID for `NOTION_ROOT_PAGE_ID` /
   `NOTION_DATABASE_IDS`: open it in the browser, and copy the 32-character
   ID from the URL, e.g. `https://www.notion.so/My-Page-<page_id>`. Not
   needed if you're using `NOTION_SYNC_WORKSPACE=true`.

Run `notion-obsidian-sync doctor` at any point after this — besides
checking your token, it reports exactly how many pages/data sources the
integration can currently see, which is the fastest way to confirm step 5
actually worked:

```
Notion token: [OK] authenticated as 'Obsidian Sync'
Accessible content: 12 page(s), 2 data source(s)
```

If it instead reports `0 page(s), 0 data source(s)`, nothing has been
granted to the integration yet — go back to step 5.

## 2. Install — Linux

```bash
git clone <this-repo> notion-obsidian-sync   # or copy the project directory
cd notion-obsidian-sync
./scripts/linux/install.sh
```

This creates `.venv`, installs the package, and copies `.env.example` to
`.env` (never overwriting an existing `.env`). Edit `.env`, then:

```bash
source .venv/bin/activate
notion-obsidian-sync doctor
notion-obsidian-sync dry-run
notion-obsidian-sync sync
```

To remove it later, see [11. Uninstalling](#11-uninstalling).

## 3. Install — Windows

Open PowerShell:

```powershell
cd notion-obsidian-sync
.\scripts\windows\install.ps1
```

Edit `.env`, then:

```powershell
.\.venv\Scripts\notion-obsidian-sync.exe doctor
.\.venv\Scripts\notion-obsidian-sync.exe dry-run
.\.venv\Scripts\notion-obsidian-sync.exe sync
```

To remove it later, see [11. Uninstalling](#11-uninstalling).

## 4. Configuration

All configuration lives in `.env` (copy from `.env.example`). Never commit
`.env` — it holds your Notion token.

| Variable | Required | Description |
|---|---|---|
| `NOTION_TOKEN` | yes | Internal integration secret, read-only capability. |
| `OBSIDIAN_VAULT_PATH` | yes | Absolute path to your Obsidian vault. Windows: `C:\Users\John\Documents\Obsidian\MyVault`. |
| `OBSIDIAN_SYNC_FOLDER` | no (default `Notion`) | Subfolder inside the vault. **All writes/deletions are sandboxed to this folder.** |
| `NOTION_ROOT_PAGE_ID` | one of these four | Mode A: sync this page and all descendant pages. |
| `NOTION_DATABASE_IDS` | one of these four | Mode B: comma-separated database IDs to sync (rows become notes). |
| `NOTION_SYNC_WORKSPACE` | one of these four (default `false`) | Mode D: sync every page/database the integration currently has access to — no ID needed. Can be combined with the two above (harmless overlap, everything is deduplicated). |
| `NOTION_SYNC_PROPERTY` | no (default empty = sync everything) | Mode C: name of a checkbox property; only checked rows sync. Applies to `NOTION_DATABASE_IDS` and `NOTION_SYNC_WORKSPACE`. |
| `ORPHAN_POLICY` | no (default `keep`) | `keep` \| `archive` \| `delete`. What to do with a note whose Notion page disappeared. |
| `DOWNLOAD_ASSETS` | no (default `true`) | Download images/files referenced in pages. |
| `CONVERT_NOTION_LINKS` | no (default `true`) | Rewrite links between synced Notion pages as `[[wikilinks]]`. |
| `SYNC_INTERVAL_MINUTES` | no (default `10`) | Used by the systemd timer / Task Scheduler installers. |
| `LOG_LEVEL` | no (default `INFO`) | Python logging level. |
| `NOTION_API_VERSION` | no | Advanced: override the `Notion-Version` header (default `2025-09-03`). |

You can combine `NOTION_ROOT_PAGE_ID` and `NOTION_DATABASE_IDS` — both are
synced in the same run.

## 5. Usage

```bash
notion-obsidian-sync sync              # incremental sync
notion-obsidian-sync sync --full       # re-verify every page's content, even unchanged ones
notion-obsidian-sync sync --page ID    # sync a single page by ID (no orphan handling)
notion-obsidian-sync sync --verbose    # debug logging
notion-obsidian-sync dry-run           # show what would change; writes nothing
notion-obsidian-sync status            # summarize local state
notion-obsidian-sync doctor            # check Python, .env, Notion access, vault permissions
notion-obsidian-sync reset-state       # forget local sync state (does not delete notes)
notion-obsidian-sync git-crypt-setup   # encrypt the sync folder at rest with git-crypt (optional)
```

Exit code is `0` if every page synced without error, `1` otherwise — a single
failing page never stops the rest of the run (see `sync` log output for
`ERROR` lines).

## 6. Automation

### Linux (systemd --user timer, recommended)

```bash
./scripts/linux/install-systemd.sh
systemctl --user enable --now notion-obsidian-sync.timer
systemctl --user status notion-obsidian-sync.timer
journalctl --user -u notion-obsidian-sync.service
```

The timer's interval is read from `SYNC_INTERVAL_MINUTES` in `.env` at
install time; re-run `install-systemd.sh` after changing it.

For systems without a user systemd instance (or as an alternative), a cron
entry works too:

```
*/10 * * * * /path/to/notion-obsidian-sync/scripts/linux/sync.sh >> /path/to/notion-obsidian-sync/logs/cron.log 2>&1
```

### Windows (Task Scheduler)

```powershell
.\scripts\windows\install-scheduled-task.ps1
```

Creates a task named `NotionObsidianSync` that runs under your current user
account (no password stored — uses S4U logon) every `SYNC_INTERVAL_MINUTES`.

```powershell
Get-ScheduledTask -TaskName NotionObsidianSync | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName NotionObsidianSync   # run immediately
Unregister-ScheduledTask -TaskName NotionObsidianSync -Confirm:$false   # remove
```

Logs go to `logs\task-scheduler.log` in the project directory.

## 7. How content is organized

**Mode A (root page):** the tree of pages under `NOTION_ROOT_PAGE_ID` is
mirrored as folders — a page with subpages becomes a folder named after it:

```
Notion/
├── Root Page.md
└── Root Page/
    ├── Child A.md
    └── Child A/
        └── Grandchild.md
```

**Mode B (databases):** rows are placed under a folder named after the
database:

```
Notion/
└── Projects/
    ├── Project Alpha.md
    └── Project Beta.md
```

**Mode D (whole workspace):** every accessible database is placed exactly
like Mode B, and every accessible top-level page (and its descendants) is
placed exactly like Mode A — so a workspace sync typically produces several
top-level folders/notes side by side, one per shared page or database:

```
Notion/
├── Standalone Page.md
├── Meeting Notes/
│   └── ...
└── Projects/
    ├── Project Alpha.md
    └── Project Beta.md
```

If a page is accessible but its parent page isn't (an unusual sharing
setup), it's still synced — placed directly at the top of `Notion/` with a
`WARN` logged, rather than silently dropped.

Filenames are sanitized for cross-platform safety (invalid Windows
characters removed, reserved device names like `CON`/`PRN` prefixed with
`_`, trailing dots/spaces stripped, Unicode/accents preserved). If two pages
would collide on the same path, the second one gets a short ID suffix, e.g.
`Project Alpha (a1b2c3d4).md`.

If a Notion page is renamed, its note is renamed/moved to match on the next
sync — matched by its stable Notion page ID, not by title, so you never end
up with a duplicate.

Downloaded images/files live in `Notion/_assets/<page-id>/<block-id>.<ext>`
and are referenced with Obsidian's `![[...]]` embed syntax.

## 8. Conflict and safety model

Every generated note carries this frontmatter:

```yaml
notion_id: "..."
notion_url: "..."
notion_last_edited_time: "..."
last_sync: "..."
managed_by: notion-obsidian-sync
```

Before overwriting an existing file, the tool checks it:

- **Unmanaged file** (no `managed_by: notion-obsidian-sync` marker, or a
  different `notion_id`) → **never overwritten**. A `WARN` is logged and the
  page is skipped that run, so nothing you wrote by hand is ever silently
  replaced.
- **Managed file edited locally** (its content no longer matches the
  checksum recorded at last sync) → the current file is backed up to
  `Notion/_Conflicts/<same path>` before being overwritten with the fresh
  Notion content, and a `WARN` is logged. Notion always wins the content,
  but nothing is lost — your edit is preserved in `_Conflicts/`.

Orphaned pages (no longer accessible in Notion) are handled per
`ORPHAN_POLICY`: `keep` (default, does nothing but warns every run),
`archive` (moves the file to `Notion/_Archive/`), or `delete` (permanently
removes it). `sync --page ID` never triggers orphan handling, since it only
looks at one page.

All writes are atomic (write to a temp file, then rename) so a crash
mid-sync never corrupts a note, and a second run with no Notion-side changes
touches zero files (true idempotence).

If `.sync-state.sqlite` is lost or reset (`reset-state`), the next sync
scans existing notes for the `managed_by`/`notion_id` markers and re-adopts
them instead of creating duplicates — it just re-verifies their content
against Notion once.

## 9. Encrypting the vault with git-crypt (optional)

Synced Notion content can be sensitive, so if you want to version-control
the sync folder (e.g. to push it to a private or even public git remote as
a backup), `notion-obsidian-sync git-crypt-setup` sets up
[git-crypt](https://github.com/AGWA/git-crypt) so it's encrypted at rest in
git — your local working copy stays readable Markdown; only what git stores
(history, and anything pushed to a remote) is encrypted.

Install `git-crypt` first (`sudo apt install git-crypt`, `brew install
git-crypt`), then:

```bash
notion-obsidian-sync git-crypt-setup
```

By default this targets the configured sync folder
(`OBSIDIAN_VAULT_PATH/OBSIDIAN_SYNC_FOLDER`). It initializes a git
repository if needed, writes a `.gitattributes` that encrypts every file,
and runs `git-crypt init`. It's safe to re-run — steps already done are
left as-is.

To actually be able to unlock the repository again later (on another
machine, or if you lose the auto-generated key), pass at least one of:

```bash
# Grant specific people/machines access via their GPG key:
notion-obsidian-sync git-crypt-setup --gpg-user you@example.com

# Or export a symmetric key file — back it up somewhere safe, outside git:
notion-obsidian-sync git-crypt-setup --export-key ~/secure/vault.key

# Set up a different directory than the configured sync folder:
notion-obsidian-sync git-crypt-setup --path /path/to/some/repo
```

The command prints the exact next steps (`git add .gitattributes && git
commit ...`) — it never creates a commit on your behalf, except
`git-crypt add-gpg-user`'s own inherent commit of the newly-wrapped key,
which is git-crypt's own behavior, not something added on top.

To unlock a clone elsewhere: `git-crypt unlock /path/to/vault.key`
(symmetric key) — or nothing at all if you're a GPG user who was granted
access and have the matching private key, since `git clone` + normal Git
operations will auto-decrypt for you.

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Notion token: [FAIL] ... 401` | Token is wrong or was regenerated. Update `NOTION_TOKEN`. |
| `doctor` reports `Accessible content: 0 page(s), 0 data source(s)` | Nothing has been granted to the integration yet. Go to <https://app.notion.com/developers/connections> → your connection → **Content Access** tab → add the pages/databases (or the whole workspace). See [1. Set up the Notion integration](#1-set-up-the-notion-integration). |
| A page never shows up (but others do) | The integration hasn't been granted that specific page (or its parent). Add it via the **Content Access** tab, or open the page → `...` → **Connections** → add the integration. |
| `object_not_found` for a database | Same as above, or you used a page ID instead of a database ID. |
| `Obsidian vault: [FAIL] not found` | Check `OBSIDIAN_VAULT_PATH`; on Windows use a raw path like `C:\Users\...` (no need to escape backslashes in `.env`). |
| `Sync folder writable: [FAIL]` | Fix permissions on the vault folder, or check it isn't on a read-only/synced-and-locked mount (e.g. mid-sync in a cloud storage client). |
| A file is "locked" mid-sync | Close it in Obsidian/your OS file lock and re-run `sync`; the tool retries transient I/O errors but a held OS-level lock will still fail that one page. |
| `429` / rate limited | Expected occasionally under load; the tool retries automatically with backoff honoring `Retry-After`. Persistent 429s usually mean another process is also hitting the same integration's token. |
| An image shows a broken link | The asset download failed (network/timeout) — check the log for a `WARN`; it will retry on the next `sync` since the page will still show as changed if you edit it again, or run `sync --full` to force re-fetch. |
| `WARN Refusing to overwrite unmanaged file` | A file already exists at that path without our frontmatter marker. Move it aside manually if you want that Notion page synced there. |

## 11. Uninstalling

To remove just the CLI/tool itself — leaving your Obsidian vault and every
synced note completely untouched — use the uninstall script. It works no
matter how the CLI ended up installed (project `.venv`, `pipx`, or a plain
`pip install` into some other interpreter), and it also removes the
systemd timer / Task Scheduler task if you installed one.

Linux:

```bash
./scripts/linux/uninstall.sh              # interactive, asks before each removal
./scripts/linux/uninstall.sh --yes        # no prompts
./scripts/linux/uninstall.sh --yes --purge  # also delete local state (.sync-state.sqlite*, logs/)
```

Windows:

```powershell
.\scripts\windows\uninstall.ps1
.\scripts\windows\uninstall.ps1 -Yes
.\scripts\windows\uninstall.ps1 -Yes -Purge
```

What it does, in order: disables/removes the automation (systemd
`--user` timer/service or the `NotionObsidianSync` scheduled task) →
uninstalls a `pipx` install if found → removes the project `.venv` if found
→ runs `pip uninstall` against any other Python environment that has the
package installed. `.env` and everything already written into your
Obsidian vault are never touched, with or without `--purge`; `--purge`
only removes this project's own local state database and logs. Deleting
the project directory itself (source files, scripts, README) is a separate,
manual step — the uninstaller only tears down the *installed* CLI and its
automation.

## 12. Security

- **Read-only by construction.** `notion_client.py` implements only GET
  requests plus the two read-only POST endpoints (`/search`,
  `/data_sources/{id}/query`). There is no PATCH/PUT/DELETE call anywhere in
  the codebase, and no POST call targets a content-mutating endpoint.
- **Sandboxed filesystem access.** Every write/delete/move resolves its
  target path and verifies it stays inside `OBSIDIAN_VAULT_PATH/
  OBSIDIAN_SYNC_FOLDER` (see `paths.resolve_within`), which also blocks path
  traversal via malicious/unexpected titles.
- **Secrets never logged.** The token is sent only as an `Authorization`
  header; log output never includes it.
- **Least privilege.** Grant the Notion integration **Read content** only.

## 13. Development

```bash
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"

pytest
ruff check .
mypy src
```

Tests mock the Notion API (an in-memory fake client) and never require a
real token.
