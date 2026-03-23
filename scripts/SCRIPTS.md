# PolicyDB Scripts Reference

## At a Glance

| Script | Who uses it | When |
|--------|------------|------|
| `dev-install.sh` | You (dev) | After pulling changes or switching worktrees — reinstalls editable package |
| `work-install.sh` | You (work Mac) | Install/update PolicyDB from git clone instead of zip |
| `make_package.sh` | You (dev) | Build a zip for users who can't use git |
| `install.sh` | End users | First install or upgrade from the zip package |
| `launch.command` | End users | Double-click launcher (macOS Finder) |
| `policydb-backup.sh` | launchd (automatic) | Runs daily at 2 AM via scheduled job |
| `install-backup-schedule.sh` | End users (once) | Sets up or removes the daily backup schedule |
| `seed_test_data.py` | You (dev) | Populates DB with realistic test activities for QA |
| `com.policydb.backup.plist` | Reference only | Template showing the launchd job structure |
| `README.txt` | End users | Quick-start guide bundled in the zip package |

---

## Developer Scripts

### `dev-install.sh`
**Purpose:** Quick editable install for testing your current code.

```bash
bash scripts/dev-install.sh           # install + apply migrations
bash scripts/dev-install.sh --serve   # install + start dev server on port 8001
```

- Runs `pip install -e .` so source changes are live (no rebuild needed)
- Works from the main repo or any git worktree
- Applies pending DB migrations
- Use this after: pulling new commits, switching worktrees, or when `policydb` command stops working

### `make_package.sh`
**Purpose:** Build a distributable zip for shipping to users.

```bash
./scripts/make_package.sh                 # Apple Silicon (default)
./scripts/make_package.sh --intel         # Intel Mac
./scripts/make_package.sh --universal     # Both architectures
```

- Prompts for version bump (patch/minor/major/keep)
- Builds a wheel, downloads all dependency wheels for offline install
- Bundles `install.sh`, `README.txt`, and wheels into a timestamped zip
- Auto-copies zip to OneDrive if available
- Verifies the package after building

**Output:** `PolicyDB_YYYYMMDD_HHMM.zip` in project root

**Note:** You only need this for users who can't use git (no internet, no git on their Mac). For your own work Mac, use `work-install.sh` instead.

### `seed_test_data.py`
**Purpose:** Insert test activities and follow-ups into the database for QA.

```bash
python scripts/seed_test_data.py
# or just:
./scripts/seed_test_data.py
```

- Creates 30 activities spanning 0-90 days ago (tests time filters)
- Creates 4 open follow-ups + 1 overdue follow-up
- Uses hardcoded client/policy IDs from seed data — run after `policydb db seed`

---

## End-User Scripts (bundled in zip package)

### `install.sh`
**Purpose:** Full production install or upgrade for end users.

```bash
bash install.sh              # Install or upgrade
bash install.sh uninstall    # Remove (keeps data)
```

What it does:
- Finds Python 3.11+ on the system
- Creates/updates venv at `~/.policydb/venv/`
- Installs from bundled wheels (offline — no internet needed)
- Adds `policydb` shell function to `~/.zshrc`
- Installs macOS LaunchAgent for auto-start on login
- Starts server and opens browser

### `launch.command`
**Purpose:** Double-click launcher for non-technical users.

- macOS `.command` files open in Terminal when double-clicked from Finder
- First run: creates venv and installs (like a mini `install.sh`)
- Every run: starts the server and opens the browser
- Closing the Terminal window stops the server

### `install-backup-schedule.sh`
**Purpose:** Set up automatic daily database backups.

```bash
./scripts/install-backup-schedule.sh install    # Enable daily backups
./scripts/install-backup-schedule.sh uninstall  # Disable
```

- Creates a macOS LaunchAgent that runs `policydb-backup.sh` at 2 AM daily
- Backups go to `~/.policydb/backups/`

### `policydb-backup.sh`
**Purpose:** The actual backup runner (called by launchd, not by you directly).

- Finds the `policydb` binary (checks `~/.policydb/venv/bin/` first, then PATH)
- Runs `policydb db backup --keep 30`
- Keeps the 30 most recent backups

### `com.policydb.backup.plist`
**Purpose:** Reference template for the launchd backup job. Not used directly — `install-backup-schedule.sh` generates the real plist with correct paths.

### `README.txt`
**Purpose:** Quick-start guide for end users. Bundled in the zip by `make_package.sh`.

---

## Work Mac — Git-Based Install

### `work-install.sh`
**Purpose:** Install or update PolicyDB on your work Mac directly from a git clone. No more zipping and emailing.

**One-time setup on work Mac:**
```bash
# 1. Create a private GitHub repo (do this once from your dev Mac)
cd ~/Documents/Projects/policydb
git remote add origin git@github.com:YOUR_USERNAME/policydb.git
git push -u origin main

# 2. Clone on work Mac
cd ~/Documents
git clone git@github.com:YOUR_USERNAME/policydb.git
cd policydb
bash scripts/work-install.sh
```

**To update (after you push new code):**
```bash
policydb update
```
That's it. The shell function added by `work-install.sh` includes a built-in `update` command that pulls latest and reinstalls.

**Or manually:**
```bash
cd ~/Documents/policydb
bash scripts/work-install.sh
```

**How it differs from `install.sh`:**
- `install.sh` installs from bundled wheels (offline, for users without internet/git)
- `work-install.sh` installs from source via `pip install .` (needs internet for first install to pull dependencies, but after that updates are fast)
- Both create the same venv at `~/.policydb/venv/`, shell function, and LaunchAgent

**Your workflow becomes:**
1. Make changes on dev Mac
2. Commit and push: `git push`
3. On work Mac: `policydb update`

---

## Git Management

### What's tracked
All scripts in this directory are tracked in git. The `__pycache__/` directory is ignored via `.gitignore`.

### Worktree workflow
When you work in a git worktree (e.g., `.claude/worktrees/nostalgic-dewdney/`), the scripts directory is a full copy. Changes you make there are on the worktree's branch, not on `main`.

**To get your script changes onto main:**

```bash
# Option 1: Merge the worktree branch
cd /path/to/main/repo
git merge claude/nostalgic-dewdney

# Option 2: Cherry-pick specific commits
cd /path/to/main/repo
git cherry-pick <commit-hash>

# Option 3: Create a PR (if using GitHub)
git push -u origin claude/nostalgic-dewdney
gh pr create
```

**After merging, update your dev environment:**
```bash
bash scripts/dev-install.sh
```

### What NOT to commit
- `__pycache__/` — already in `.gitignore`
- `PolicyDB_*.zip` — build artifacts, already ignored by `*.zip` in `.gitignore`
- `dist/` and `build/` — already in `.gitignore`

### When to run `dev-install.sh`
- After `git pull` or `git merge` on main
- After switching to a different worktree
- After rebasing a worktree onto latest main
- When `policydb` command gives import errors or "module not found"
- When you see stale behavior that doesn't match your code changes
