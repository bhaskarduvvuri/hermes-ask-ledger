# Ask Ledger

A Hermes Agent plugin that keeps track of what you asked.

You ask ten things, walk away, come back, and cannot tell which were answered,
which are still running and which are waiting on you. After a restart or a
context compaction the agent cannot tell either. Ask Ledger writes every ask
to disk, tracks its state, and reminds the agent of the ones still open, every
turn, in any session.

## Why

People who use agents keep asking for the same thing: a task list that
persists across threads, with a status per item, that survives a crash and
isn't lost when the context is compacted. Agent to-do lists usually live in
the context window, so they disappear exactly when you need them.

## How it differs

Good tools already exist for tasks that you or the agent create on purpose:
git-backed issue trackers for agents such as Beads, Markdown task boards such as
Backlog.md, and session-start hooks that tell the agent how many tasks are
open. Ask Ledger does something narrower and combines three things:

- **Automatic capture.** Every message you send is an ask, with no command
  and no extra step for you or the agent.
- **Answered is not done.** The agent can mark an ask `answered`; only you
  mark it `closed`. "The agent thinks it replied" and "you are satisfied" stay
  two different states.
- **Reminded every turn, from disk.** The open asks go into every model call,
  not only at session start, and they are read from the ledger file, so they
  survive restarts and compaction.

Not an audit of the agent's tool calls: it tracks what you asked and promised.

## What it does

- **Capture.** Each message you send becomes an ask: `A1`, `A2`, … with the
  time, where you sent it from, your words (trimmed, secrets redacted) and a
  topic guess. Scheduled jobs, sub-agents, kanban workers, webhooks and
  Hermes's own notifications (background-task results, system notices) are not
  asks. Sending the same words again (`/retry`, `/rewind` and resend, or after a
  restart) keeps the one ask instead of adding a copy.
- **Track.** Each ask is `open`, `in progress`, `needs you`, `answered` or
  `closed`. The agent moves asks with the `ask_update` tool and a one-line
  answer. Only you close an ask.
- **Remember.** Before each model call the agent gets a short note (at most
  5 lines) listing your asks that need you and the open ones older than 30
  minutes. The ledger lives on disk, so this survives restarts and compaction.
- **Show.** `/asks` lists your asks in the chat, grouped by topic with
  "Needs you" first. A static page (`asks.html`, no JavaScript, strict
  Content-Security-Policy) is rewritten on every change.
- **Nudge (optional, off by default).** When an ask moves to `needs you`, a
  push to your [ntfy](https://ntfy.sh) topic saying only "N things need you"
  and the page link.

- **Todos.** Things you mean to do, beside your asks: `T1`, `T2`, … with a
  category and an optional due date. You add them with `/todo add`, or the
  agent adds one when you commit to something ("I'll book the dentist after
  the launch"), marked `added by agent`. An ask becomes a todo when it gets a
  category or due date.
- **Snooze, done, drop.** By command (`/snooze T4 tonight`, `/done T4`,
  `/drop T4`) or in plain words ("remind me after lunch", "done"). The agent
  can only suggest these ("done or drop?"); a plain-words action goes through
  only when your words are in your current, short message.
- **Reminders at the end of a reply.** The plugin itself adds at most three
  plain lines under the agent's reply: a todo related to what you are talking
  about, and what is due. A due item is shown in full once, then as a short
  "Still due" at most every 30 minutes, until you snooze it or finish it.
- **Open todos message.** One plain message per chat lists your open todos and
  is edited in place as they change (on chats that cannot edit messages, a new
  one is sent). It ends with "/todos to snooze or finish".

On a shared gateway, every user sees and changes only their own asks and todos.

## Install

Requires Hermes `>= 0.22.6`, Linux or macOS. Hermes itself runs on Python 3.14
and provisions it on its own when it's missing. The plugin adds nothing to that:
it is standard-library Python with no dependencies. The plugin API it uses is
the same in 0.22.6 and 0.23.0.

```bash
hermes plugins install ask-ledger --enable          # once listed in the catalog
# or from this repository:
hermes plugins install bhaskarduvvuri/hermes-ask-ledger/plugin --enable
```

`--enable` also brings Hermes's managed Python environment up to date, which
downloads it the first time. If that download can't run where you are (for
example behind a proxy that blocks it), the plugin is still installed. Ask
Ledger has no dependencies, so you can enable it by adding `ask-ledger` to
`plugins.enabled` in `config.yaml`. On a profile with no other plugins enabled:

```bash
hermes config set plugins.enabled '[ask-ledger]'     # replaces the whole list
```

If other plugins are enabled, list them too, or edit `config.yaml`.

Then start a session and ask something. `/asks` shows the ledger.

## Use

| You type | It does |
|---|---|
| `/asks` | Your unresolved and answered asks, grouped, with the page link |
| `/asks all` | Include closed asks |
| `/asks close A3` | Close an ask (only you can) |
| `/asks private A3` / `/asks public A3` | Hide or show an ask's words on the page and in reminders |
| `/asks page` | The page's location |

| `/todo add <text> \| <category> \| <due>` | Add a todo; category and due (`today`, `tomorrow`, `YYYY-MM-DD`) are optional |
| `/todos` | Pick a todo, then Snooze, Done or Drop (buttons on Telegram, Discord and Slack; a numbered list elsewhere); where no picker can open, your open todos by category |
| `/todos stats` | How often you snoozed and closed, by command, button or words (local only) |
| `/snooze T4 [1h \| tonight \| tomorrow \| YYYY-MM-DD \| HH:MM \| now]` | Snooze; with no time you pick from a short list; `now` clears it |
| `/done T4`, `/drop T4` | Finish or drop a todo or an ask |

Everything here also works in plain words, which is the dependable path on
chats that do not pass plugin commands through. On a gateway where
`allow_admin_from` limits commands, add `todo`, `todos`, `snooze`, `done` and
`drop` to `user_allowed_commands` so other users can run them.

The agent uses `ask_update(id, state, answer_line)`, `ask_list()`, and
`todo_add`, `todo_update`, `todo_suggest`, `todo_list`.

## Settings

In `config.yaml` under `plugins.entries.ask-ledger.settings`, or the Desktop
plugin settings form:

| Key | Default | Meaning |
|---|---|---|
| `remind_after_minutes` | `30` | Open asks older than this appear in the agent's reminder |
| `max_lines` | `5` | Most asks listed in the reminder |
| `page_url` | empty | URL to show for the page if you serve it; default is the local file |
| `ntfy_url` | empty | `https://` ntfy topic URL; empty means no notifications |
| `agent_adds` | `true` | Let the agent add todos when you commit to something (at most 2 a turn, 10 a day) |
| `board` | `true` | Keep one "Open todos" message in each chat, edited in place as the list changes |

## What it stores and sends (disclosure)

- **Stores** your messages as asks, in plaintext, in
  `HERMES_HOME/plugin-data/ask-ledger/` (`asks.jsonl`, `asks.html`, `asks.lock`,
  files readable only by you). Before writing, common secret shapes (API keys,
  GitHub/Slack/AWS tokens, JWTs, bearer tokens, PEM private keys,
  `password=`-style values, long hex/base64 strings) are replaced with
  `[redacted]`. Redaction is best effort; mark sensitive asks private.
  The log is append-only and is never pruned; delete the folder to reset.
- **Stores** your todos and every snooze, close and suggestion as events in
  the same log, with how you did it (command, button or words) so `/todos
  stats` can count it locally. Nothing about this leaves your machine.
- **Sends to your model provider** the reminder note (up to 5 asks' ids, state,
  age and first 90 characters, plus up to 3 todos and your category names), as
  part of your message each turn. Private asks are sent as "(private)".
- **Adds to the agent's reply** up to three plain reminder lines and a short
  hint, through Hermes's `transform_llm_output` hook. If another plugin also
  rewrites replies, Hermes keeps only the first rewrite.
- **Network:** none, unless you set `ntfy_url`. Then one HTTPS POST to that
  URL per change to "needs you", containing only a count and the page link.
- **Does not:** run shell commands, read files outside its own data folder,
  read credentials, start long-running processes, update itself, or collect
  telemetry.

## How it works

On messaging platforms an ask is captured by `post_gateway_admission`, which
Hermes fires only for a person's admitted message (never for its own internal
events such as background-task results, and never for slash commands). On the
CLI, TUI and Desktop it is captured by `pre_llm_call`. On every surface
`pre_llm_call` returns the reminder as context, which Hermes adds to the user
message (not the system prompt, so the prompt cache holds). The ledger is
an append-only JSONL event log; its current state is the replay of the events,
and a damaged line is skipped, never fatal. Writes take a file lock, so the CLI
and the gateway can share a profile. Every hook, tool and command catches its
own errors: a broken ledger logs a warning and the agent carries on.

## Develop

```bash
python3 -m unittest discover -s tests -t .           # stdlib only, no Hermes needed
hermes plugins doctor ./plugin --ci
hermes plugins validate ./plugin --install-deps      # what catalog CI runs
```

`e2e/run.sh` runs all of the above plus a scripted demo against an isolated
Hermes install (see the comments at its top).

## License

MIT
