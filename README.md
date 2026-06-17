# MailCode

Email ↔ AI Agent bidirectional remote command bridge. Control AI coding agents (Claude Code, OpenCode) via email — no dashboards, no webhooks, no chat apps.

```
Inbox ──> IMAP Listener ──> claude -p subprocess ──> SMTP reply
```

## Philosophy

MailCode's core idea is **lightweight, direct human-to-agent connection**.

Most AI toolchains rely on heavy collaboration platforms — Slack/Discord bots, webhook configurations, chat interfaces. MailCode does the opposite: it uses email, something you already have.

**Direct connection, not a chatbot.** Replying to an email sends a command. Your inbox is your console. No third-party app required.

**Lightweight async.** No persistent services, no database, no message queue. One Python script + email protocol, runs on any machine with network access. The agent works in the background; you do other things; the result arrives in your inbox.

MailCode doesn't aim to be a platform. It does one thing: **let you talk to an AI agent the way you already talk to people — through email.**

## Email Architecture

MailCode requires **two email accounts** — one Bot, one User:

- **Bot mailbox** (e.g., `mailcode_bot@example.com`) — MailCode monitors its inbox. After Claude processes a task, results are sent back through this mailbox.
- **User mailbox** (your personal email, e.g., `you@example.com`) — You send command emails from this address to the Bot mailbox.

Flow:

```
[User Mailbox]  ──send command──▶  [Bot Mailbox Inbox]
  you@example.com                   mailcode_bot@example.com
                                           │
                                           ▼
                                      IMAP Listener
                                           │
                                           ▼
                                      Claude Processing
                                           │
                                           ▼
  [User Mailbox]  ◀──reply email──  [Bot Mailbox Outbox]
```

> **Why not use your own mailbox?** MailCode needs to log into a mailbox to read and send emails, so a dedicated Bot mailbox is required. It stays separate from your daily mailbox, and the `allowed_senders` config ensures only your personal email can send it commands.

## Installation

### Prerequisites

- **python3** (≥3.9)
- **Claude Code** (`claude` command must be in `PATH`)

Zero third-party Python dependencies — all standard library (`imaplib`, `smtplib`, `email`, `subprocess`, `json`, `secrets`, etc.).

### pip install

```bash
pip install mailcode
```

### Source install

```bash
git clone <repo-url> && cd MailCode
bash install.sh
```

`install.sh` automatically: installs the mailcode package, initializes config, creates `~/.mailcode` symlink, and adds to PATH.

Install from local wheel: `bash install.sh --local dist/mailcode-*.whl`

## Configuration

Edit `~/.config/mailcode/config.json` with required fields. **Keep the two mailboxes straight** — `mailcode_bot.email` is the Bot mailbox, `security.allowed_senders` lists the addresses allowed to send it commands (typically your personal email):

```jsonc
{
  "mailcode_bot": {
    "email": "mailcode_bot@example.com",  // ← Bot mailbox: MailCode logs into this
    "password": "Bot mailbox app password", // ← App password, not your login password
    "check_interval": 60                     // ← Poll interval (seconds); 163/126 recommend 60-120
  },
  "security": {
    "allowed_senders": ["you@example.com"]   // ← Allowed command senders (your personal email)
  }
}
```

SMTP and IMAP settings are auto-detected from the Bot mailbox domain. Supported providers: QQ Mail, 163/126 Mail, Gmail, Outlook/Hotmail.

To override SMTP/IMAP (e.g., self-hosted email), add `smtp` / `imap` sections — manual values take precedence over auto-detection.

> Getting an app password: QQ Mail → Settings → Account → POP3/IMAP → Generate authorization code. Gmail → Google Account → Security → App passwords.

## Usage

### CLI Overview

| Command | Description |
|---------|-------------|
| `mailcode serve` | Start IMAP listener relay (includes scheduler, real-time console events) |
| `mailcode chat` | Terminal interactive mode — talk to Claude directly (no email) |
| `mailcode schedule <action>` | Scheduled task management (`list`, `show`, `add`, `enable`, `disable`, `delete`, `run-now`, `validate`) |
| `mailcode config <action>` | Configuration management (`show`, `init`, `init-test`, `path`, `validate`) |
| `mailcode health [--send]` | Mail connectivity check (SMTP/IMAP; `--send` sends a test email) |
| `mailcode session <action>` | Session management (`list`, `show`, `delete`, `cleanup`, `stats`) |
| `mailcode --version` | Show version |

### Start Relay

```bash
# Foreground (default: IMAP IDLE persistent connection, real-time email delivery)
# Console output shows live events: 📬 email received → 🤖 invoking Claude → ✅ reply sent
mailcode serve

# Dry-run mode (print emails only, don't invoke claude)
mailcode serve --dry-run

# Force polling (disable IDLE; some legacy providers require this)
mailcode serve --no-idle

# Single poll then exit
mailcode serve --once
```

**IMAP IDLE support varies by provider** — MailCode detects `IMAP CAPABILITY` on connect and falls back to polling if IDLE is unavailable:

| Provider | IDLE | Behavior | Recommended `check_interval` |
|----------|------|----------|------------------------------|
| QQ Mail (`imap.qq.com`) | ✅ | Real-time push, sub-second response | 60s (when polling) |
| 163/126 Mail (`imap.163.com` / `imap.126.com`) | ❌ | Auto-fallback to polling, warning log | **60-120s** (excessive polling triggers anti-abuse rate limits, potentially IP ban) |
| Gmail / Outlook | ✅ | Real-time push | 60s (when polling) |

NetEase (163/126) mailboxes **do not support IDLE**, and frequent IMAP logins trigger anti-abuse measures. If your Bot mailbox uses 163/126, set `mailcode_bot.check_interval` to 60–120 seconds to avoid temporary bans.

View logs:

```bash
tail -f ~/.config/mailcode/relay.log
```

### Configuration Management

```bash
mailcode config show          # View current config (passwords masked)
mailcode config path          # Show config file path
mailcode config init          # Initialize config (skip if exists)
mailcode config init --force  # Force re-initialize
mailcode config validate      # Validate config integrity
```

### Terminal Chat

Talk to Claude directly from the terminal without going through email:

```bash
mailcode chat                    # Start a new conversation
mailcode chat --session-id <id>  # Resume an existing session
mailcode chat --cwd ~/my-project # Set working directory
```

Useful for quick debugging or when you don't want to use the email channel. Sessions created in `serve` mode can be resumed in `chat` mode and vice versa.

### Session Management

MailCode maintains multi-turn conversations grouped by email subject by default. Set `session.enabled = false` for single-reply mode.

```bash
mailcode session list                          # List all sessions
mailcode session list --wide                   # Full display (no truncation)
mailcode session list --filter "keyword"       # Filter by sender or subject
mailcode session show <session_id>             # View full message history
mailcode session delete <session_id>           # Delete a session
mailcode session stats                         # Statistics (total / active / expired)
mailcode session cleanup                       # Clean up expired sessions by TTL
mailcode session cleanup --dry-run             # Preview only, no deletion
```

### Working Directory (cwd directive)

Put `cwd: <path>` on the **first line** of your email body to start the Claude subprocess in that directory — ideal for "Claude, work on this project." In session mode, the cwd is **sticky**: subsequent emails in the same session reuse the directory until a new one is specified.

```
cwd: ~/Projects/my-app
Take a look at the JWT validation logic in src/auth.py
```

**Path resolution rules**:

- `~` / `~/foo` expands to the user's home directory
- Relative paths (`./foo`, `foo`) resolve from `Path.cwd()`
- The path must exist and be a directory (`is_dir()` check); otherwise it falls back to `$HOME`
- Case-insensitive: `Cwd:` / `CWD:` work the same way

**Mode differences**:

- **Session mode** (`session.enabled = true`, default): cwd is sticky across the entire session; check with `mailcode session show <id>`
- **Single-reply mode** (`session.enabled = false`): cwd is not sticky, each email parses independently

The cwd line is stripped from the body before invoking Claude, so it never pollutes the prompt.

### Health Check

```bash
mailcode health        # Check SMTP/IMAP config and connectivity
mailcode health --send # Also send a test email to verify the send channel
```

Checks: SMTP connection / login / send, IMAP connection / login / inbox, **sender whitelist is not empty** (an empty whitelist rejects all incoming emails in serve mode).

### Scheduled Tasks

MailCode includes a lightweight scheduling engine — no external cron or systemd timer required. It runs inside `mailcode serve`, with persistence to `~/.config/mailcode/schedules.json`.

Four schedule types:

| Type | Parameters | Example |
|------|------------|---------|
| `interval` | `--interval-seconds <N>` | Every 3600 seconds |
| `daily` | `--time <HH:MM>` | Every day at 09:00 |
| `weekly` | `--time <HH:MM> --day-of-week <0-6>` | Every Monday at 09:00 (0=Sunday) |
| `monthly` | `--time <HH:MM> --day-of-month <1-31>` | 1st of each month at 09:00 |

Scheduled tasks invoke `claude -p <prompt>` and email the response to the configured recipient. All schedules are based on local time. Missed trigger windows are skipped (no catch-up).

```bash
# Create a scheduled task
mailcode schedule add morning-digest --type daily --time 09:00 \
  --prompt "Summarize GitHub notifications, list today's TODOs" \
  --to-email you@example.com \
  --subject-prefix "[Morning Digest]"

# List all tasks
mailcode schedule list

# View task details
mailcode schedule show morning-digest

# Execute immediately (doesn't affect schedule stats)
mailcode schedule run-now morning-digest

# Enable / Disable
mailcode schedule enable morning-digest
mailcode schedule disable morning-digest

# Delete
mailcode schedule delete morning-digest

# Validate all task configs
mailcode schedule validate
```

**Configuration** (`~/.config/mailcode/config.json`, optional):

```jsonc
{
  "schedule": {
    "enabled": true,         // Global toggle
    "tick_seconds": 30       // Scheduler poll interval
  }
}
```

**Features**:

- **Hot-reload** — tasks created/modified/deleted via CLI take effect immediately in a running `serve` process (no restart needed)
- **Concurrency guard** — a task won't re-trigger while its previous run is still in progress
- **Error notification** — if Claude or email sending fails, the configured recipient is notified automatically
- **Standalone execution** — `mailcode schedule run-now` works without a running `serve` process
- **Dry-run compatible** — `mailcode serve --dry-run` marks tasks as executed without actually running them
