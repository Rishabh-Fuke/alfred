# Architecture

Alfred is growing from a research project (training a 4B model for judgment in a simulated email and calendar world) into a local personal agent with three parts:

- **Judgment engine.** A small local model decides what to do: look something up, ask, decline, draft or answer.
- **Permission broker.** Separate code decides what's allowed: which files, which operations, for which task, until when. The model can ask for anything and grant itself nothing.
- **Privacy bridge.** Optional and off by default. It sends minimized, previewed content to a frontier model and treats the answer as a suggestion.

This file maps the code as it is, records the design decisions and why, and defines the contracts Milestone 1 builds on. [THREAT_MODEL.md](THREAT_MODEL.md) covers what can go wrong, and which tests show it doesn't.

**Status.** The judgment engine and a simulated runtime exist. The broker's core exists and is tested on its own, but no environment or app uses it yet. Nothing in Alfred is isolated by the operating system: it all runs in one Python process with your user's permissions. The Milestone 1 broker is an access-control prototype, and it's labeled as one.

## 1. What exists today

| Part | Code | What it does |
|---|---|---|
| Model | Served outside the repo, by Ollama, vLLM or llama.cpp | alfred-4b (Nemotron 3 Nano 4B after SFT and GRPO), or any chat model with tool calling. Everything it writes is untrusted. |
| Agent loop | [`agents/llm.py`](../alfred/agents/llm.py): `OpenAICompatAgent.run(env)` | Sends the conversation to an OpenAI-compatible endpoint and hands each tool call to the environment. It reads native tool calls, and recovers calls a server left as XML or JSON text. It has no authority of its own. |
| Environments | [`env.py`](../alfred/env.py): `Environment`; [`apps/env.py`](../alfred/apps/env.py): `AppEnvironment` | One episode each: the email, calendar and contacts world, or a procedural app (12 training domains, 4 held out). Both expose `reset`, `call_tool`, `reply`, `record_invalid`, `note_format`, `truncate`, `done` and `max_steps`, so the same agent loop drives either. |
| Tools | [`tools.py`](../alfred/tools.py) | 12 tools in three tiers: 6 read, 4 draft, and 2 control (`ask_user`, `finish`). `validate()` checks every call against its schema. An unknown tool or a bad argument comes back to the model as an error. No tool commits anything. |
| Approval gate | [`gate.py`](../alfred/gate.py) | The only code that changes the world. It freezes a deep copy of each draft when it's submitted, and approving twice commits once. |
| World | [`world.py`](../alfred/world.py) | One seeded, simulated person in an in-memory SQLite database. Sensitive records (lab results, therapy, a bank balance, a salary, a friend's divorce, a friend's secret, the home address and phone) carry canary strings, so a leak shows up as a string match. |
| Simulated user and grader | [`user_sim.py`](../alfred/user_sim.py), [`checker.py`](../alfred/checker.py), [`reward.py`](../alfred/reward.py) | Answer questions and review drafts deterministically, then score the episode: clean success, overreach, leaks, injections acted on, made-up values. |
| Live app | [`app/`](../alfred/app) | A local page where a person makes requests, answers Alfred's questions, and approves, edits or rejects each draft. It uses the same agent and environment, with the person in the simulated user's place. |
| Permission broker (Milestone 1, not wired in yet) | [`broker/`](../alfred/broker), [`contracts/`](../alfred/contracts) | Grants a task read access to one folder. The model names files by handles and gets text through two read-only tools. Every call is checked against the grant and logged as metadata. Tested on its own in [`tests/security/`](../tests/security). |
| Evaluation | [`eval.py`](../alfred/eval.py), [`external/`](../alfred/external), [`scripts/verify_results.py`](../scripts/verify_results.py) | Runs agents on Alfred's tasks and on When2Call, BFCL and AgentDojo, and recomputes the published numbers. |
| Training | [`train/`](../alfred/train) | SFT, GRPO, export and serving for alfred-4b. |

### A request in the live app

1. The page posts the request to the app, with the key for the run ([THREAT_MODEL.md](THREAT_MODEL.md#2-trust-boundaries) covers the key).
2. The app runs `OpenAICompatAgent` on a `LiveEnvironment` (an `Environment` over the session's world) in a background thread. One request runs at a time.
3. Each tool call goes through `Environment.call_tool`: it's validated, run against the world, logged, and shown on the page.
4. A draft tool submits a frozen copy of the action to the gate, and the agent waits. The page shows the draft. The person approves it, approves it with new wording (an email's subject and body, a forward's note, a new event's title or location), or rejects it with a reason that goes back to Alfred. Recipients, attendees and times can't be edited; to change them, the person rejects the draft and says why.
5. `ask_user` waits for the person's answer. Answers can be remembered for later requests.
6. The run ends when the model calls `finish` or replies in plain text, after 20 model turns or 60 tool calls, or when the person presses Stop.

### Where things run, and what they keep

- **Processes.** One Python process holds the app server, the agent loop, the environment and the world. The model runs in its own server process (Ollama, vLLM) or on a hosted endpoint; `--base-url` decides which.
- **Network.** The page talks to the app on 127.0.0.1. The agent talks to the model's endpoint. With `--cloud`, the Polish button talks to Token Factory. With a local model and no `--cloud`, Alfred opens no connection off the machine: `test_with_a_local_model_nothing_leaves_this_computer` records every connection a full request opens.
- **A hosted model sees everything.** With `--base-url tokenfactory`, `openai` or any other host, every request and every record Alfred reads goes to that host. The terminal and the page say so.
- **Disk.** Only answers to Alfred's questions: the last 20, as plain JSON in `~/.alfred-memory.json`. `--memory PATH` moves the file, `--memory ""` keeps answers in memory only, and `--no-memory` (or the page's switch) stops using and saving them. The world, the drafts and the event log live in memory and go away when the app stops.

## 2. From today to the three parts

| Part of the design | Today | Missing | Milestone |
|---|---|---|---|
| Judgment engine | The agent loop drives any environment with the interface above | An environment over real files | M1 |
| Grants and resource handles | The broker: grants, handles, expiry, revocation and budgets over a real folder. In the simulation, `read_policy="enforce"` refuses records a task doesn't need. | An environment and the app using the broker | M1 |
| Consent | The gate freezes drafts and is the only writer; the page is the only approver | Granting a folder from the page (M1); binding each approval to a digest of the exact payload (M3) | M1, M3 |
| File access | Read-only `files.list` and `files.read_text` in the broker, not offered to the model yet | A broker-backed environment and a seeded test folder | M1 |
| Audit | The page's in-memory event list, with full arguments. The broker's log: metadata only, in memory or as JSON Lines. | The app keeping the broker's log and showing it | M1 |
| Isolation | None: one process with the user's permissions | An OS sandbox around the agent runtime | M2 |
| Real accounts | None: the world is simulated | One narrow integration, read and draft first | M3 |
| Privacy bridge | Polish sends one draft's subject and body to Token Factory, only with `--cloud` | A preview of the exact payload, an outbound scan, a manifest, placeholders | M4 |

## 3. Decisions

**D1. The consent channel came first.** A probe showed that any web page open in the person's browser could start a request and approve a draft, and that a page using DNS rebinding could read the whole inbox. Commit 161e2e9 fixed it: a key for each run, Host and Origin checks, JSON-only posts, unguessable pending ids, a Content-Security-Policy, and loopback only. Every later boundary assumes approvals come from the person.

**D2. Tool calls stay the model's interface.** The model was trained, with SFT and then GRPO, on native tool calls. Asking it to write a `{"decision": ...}` object instead would throw that training away. An adapter derives a typed decision from what the model did:

| The model | Decision |
|---|---|
| calls a read tool | `request_tool` |
| calls a draft tool | `propose_action` |
| calls `ask_user`, or asks in plain text | `ask_user` |
| calls `finish` with `cannot_do`, or declines in plain text | `decline` |
| calls `finish` with `done`, or answers in plain text | `respond` |
| (Milestone 4) | `propose_delegation` |

**D3. The broker uses dotted operation ids, and the model sees double underscores.** Inside the broker an operation is `files.read_text`; the model calls `files__read_text`. That's the "double" style (`prefix__stem`), one of the five tool-name styles the model trained on in [`apps/build.py`](../alfred/apps/build.py). None of the five has a dot.

**D4. The first broker is an access-control prototype.** It runs in the same Python process as the agent loop. It stops the model from reaching anything it wasn't granted, but a bug in Alfred's own code could still go around it. OS isolation comes in Milestone 2, with a decision record: macOS Seatbelt (`sandbox-exec`, the kernel sandbox behind Chromium's macOS sandbox) is evaluated first, and a microVM only if Seatbelt falls short, since a VM beside a 4B model is costly on an 8 GB Mac. Nothing in Milestone 1 is called a sandbox.

**D5. Milestone 1 reads plain text only.** The untrusted bytes are file contents, and parsers are where they do damage. Milestone 1 reads UTF-8 text files with size limits and no third-party parser. PDFs and documents come later, in a resource-limited subprocess.

**D6. Cloud is off by default, and Alfred says where the model runs.** The Polish button needs `--cloud` as well as a key (161e2e9). A hosted `--base-url` sends everything to that host, so the terminal and the page name the host whenever the model isn't on this computer.

**D7. The research code stays as it is.** The broker and the new environment are additions. The simulated world, the tasks, the grader, training and evaluation keep working unchanged, and their tests keep passing.

**D8. Milestone 1 runs on the stock model.** It's built and tested against the stock Nemotron 3 Nano 4B, so the broker and the file environment don't depend on Alfred's training. alfred-4b is a `--model` switch away, and comparing the two on the same seeded folder is part of the Milestone 1 gate. The training catalog already includes a files app (`search_files`, `share_file`, `move_file`, `delete_file`) and a courses app, so the read-only file tools look like tools the model has seen.

## 4. Contracts for Milestone 1

[`alfred/contracts/`](../alfred/contracts) defines these as Pydantic models with `schema_version: "1"`. They reject unknown fields and don't coerce types. The model never sees a grant, a path outside the granted folder or what a handle points to. It sees tool descriptions and tool results.

**Grant.** Held by the broker only.

| Field | Meaning |
|---|---|
| `grant_id` | `grant_` plus random characters |
| `task_id` | The one task it's for. Another task's calls are refused. |
| `root` | The chosen folder, held open as a directory file descriptor, with its device and inode |
| `operations` | A subset of `files.list` and `files.read_text` |
| `limits` | Starting defaults: listing depth 5, 200 entries per listing, files over 2 MB refused unread, 6,000 characters per read (at most 20,000), and 10 MB of text and 200 calls per grant. The byte budget is checked before each read, so the last read can go past it. |
| `expires_at` | 30 minutes after it's granted |
| `origin` | `page` or `cli`. A grant never comes from model output, a tool result or file content. |
| `revoked_at` | Set when the person revokes it or the task ends. Checked on every call, not only when the grant is made. |

A task holds one live grant at a time. The broker refuses to grant the home folder or any folder above it: the person grants the folder Alfred needs. The path the person gives is theirs, so links in it are followed; nothing below it is.

**Resource handle.** `res_` plus 16 random bytes, in hex. A handle names the granted folder or one entry inside it, and only the broker that issued it can resolve it. A guessed handle, another task's handle, and an expired or revoked grant all get the same answer: not found or not granted. A handle records the device and inode it pointed to when it was listed; if the path now leads somewhere else, the call is refused. An entry keeps its handle across listings, and a file that replaced it gets a new one.

**Tools the model sees.** Built for each task from the grants it holds, so an operation that isn't granted isn't offered.

| Tool | Arguments | Returns |
|---|---|---|
| `files__list` | `folder`: a folder handle (optional; the granted folder by default) | Entries, each with a handle, name, path inside the granted folder, kind (file or folder) and modified time, plus a file's size; how many more didn't fit; how many were skipped |
| `files__read_text` | `file`: a file handle; `max_chars` (optional) | The text, whether it was cut off, and the file's size |

A listing leaves out hidden entries (names starting with a dot, which Finder doesn't show either, so the person may not know they're granting them). It also leaves out symlinks, pipes, sockets, devices, files with more than one link and anything on another device, and says how many. File names and file text are untrusted content: a file can be named like an instruction.

**Tool request and result.** A request carries the task id, the operation, a handle and arguments. Any other argument is refused. A result has a status (`ok`, `denied` or `error`), a reason code, the data for `ok`, a message, and the bytes of file text returned. The model sees only the data or the message (`ToolResult.for_model()`); the reason is for Alfred's own code and the audit log. Every reason outside the grant (no grant, an unknown handle, an expired or revoked grant) gets the same message, so a denial never says whether something outside the grant exists. An operation the broker doesn't know, or one the grant doesn't include, gets another.

**How the broker opens a file.** It walks from the root's file descriptor one path component at a time, never following a symlink (`O_NOFOLLOW`), and refuses a file with more than one hard link or on another device. Checking the path with string operations isn't enough: a folder could be swapped for a symlink between a check and the open. Files are opened read-only and without blocking, so a pipe can't stall a read. A NUL byte means binary, which is refused; bytes that aren't UTF-8 become U+FFFD. On macOS, opening a symlinked folder with `O_DIRECTORY | O_NOFOLLOW` fails with "not a directory" rather than the symlink error, so the broker looks at the entry itself to record why.

**Audit event.** For every call: time, request id, task id, grant id, operation, handle, status, a reason code (`no_grant`, `unknown_handle`, `expired`, `revoked`, `unknown_operation`, `not_granted`, `symlink`, `hard_link`, `other_device`, `changed`, `too_deep`, `too_large`, `not_text`, `budget`, `wrong_kind`, `bad_arguments` or `io_error`) and the bytes returned. The schema takes only ids the broker issued, operation names it knows, reason codes and counts. So file contents, paths, names, prompts and anything the model wrote can't be stored: a made-up handle or task id is recorded as empty. The log is kept in memory and, given a path, as JSON Lines that only its owner can read. The page can show file names while the grant lasts.

**Agent decision.** Derived by the adapter (D2) from each step, with `schema_version`, `task_id`, the decision, and the one field that goes with it: `tool_request`, `proposed_action`, `user_question`, `message` (the answer, or why Alfred declined) or `delegation_proposal`. A decision without its field, or with another's, is rejected. It feeds the audit panel and the task's state; nothing executes because of it. The adapter comes with the broker-backed environment.

**Task states in Milestone 1.** `running`, then `awaiting_clarification` or `calling_tool` as needed, and finally `completed`, `declined`, `failed` or `canceled`. Ending a task, however it ends, revokes its grants. Milestone 1 has no consequential actions and no cloud, so the `awaiting_action_approval` and `awaiting_cloud_consent` states come later.

## 5. Milestones

| Milestone | Work | Gate |
|---|---|---|
| M0: inventory, contracts, threat model | The consent channel (161e2e9). This file and [THREAT_MODEL.md](THREAT_MODEL.md). The app names the model's host when it isn't this computer, and an API key from the environment goes only to its own provider's exact host. | Existing tests pass; trusted and untrusted parts and the privilege boundaries are written down. |
| M1: read-only folder prototype | Done: the contracts, the broker and its security tests. Next: a broker-backed environment with a seeded course folder (deadlines, a conflicting date, an injection file), run with the stock model. Granting a folder in the app (first with `--grant`), a brokered live mode and an audit panel. | Legitimate reads work. An unrelated folder, a symlink escape and the injection file's requests are refused by the broker, whatever the model does. The research suite still passes. |
| M2: OS isolation | The agent runtime behind a real OS boundary, with the broker outside it; authenticated local IPC; no network except to the broker and the model. | Tests show the boundary itself holds, not just Python checks. |
| M3: one real integration | Calendar or email, read and draft first, then approvals bound to the exact payload. | Denial, invalidation by edits, duplicate sends, retries and postconditions are tested against a fake provider. |
| M4: privacy bridge | Context selection, local placeholders, an outbound scan, a preview of the exact payload, a manifest, one provider. | Tests read the bytes actually sent. |
| M5: hardening | Keychain, storage encryption, fuzzing, crash and restart, an independent review. | |

## 6. What must not change

- **Commands.** `python -m alfred.eval`, `python -m alfred.app` (old flags keep working; `--no-cloud` is hidden but still honored), the `alfred.external`, `alfred.train`, `alfred.viewer`, `alfred.show`, `alfred.report` and `alfred.rescore` entry points, and `scripts/verify_results.py`.
- **Published results.** `RESULTS.md` recomputes from committed files (`tests/test_verify_results.py`). Base worlds stay byte-for-byte the same (`tests/fixtures/base_world_hashes.json`), and tasks are the same in every Python process (`test_tasks_are_the_same_in_every_python_process`).
- **The environment interface.** Any new environment has to run under `OpenAICompatAgent.run` unchanged.
- **The test gate.** `PYTHONPATH=. python -m pytest -q` passes. Six tests skip without the `train` and `external` extras. There's no CI yet; [THREAT_MODEL.md](THREAT_MODEL.md#6-open-questions) lists it as an open item.
