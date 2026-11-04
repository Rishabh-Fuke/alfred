# Threat model

What Alfred protects, from whom, where the boundaries are, and which tests show each one holds. It covers the code as it is (the simulated world and the live app) and the Milestone 1 design in [ARCHITECTURE.md](ARCHITECTURE.md).

Tests show how Alfred behaves under the threats written down here. They don't prove that every injection fails, that nothing can leak, or that there are no bugs.

## 1. What Alfred protects

| Asset | Today | From Milestone 1 |
|---|---|---|
| Consequential actions: sending email, changing the calendar | In the simulated world. Only the gate commits, after the person approves. | Unchanged. Real accounts start in Milestone 3. |
| The person's data | A simulated person's records, with sensitive ones marked and carrying canary strings | Files in the one folder the person grants. Everything else on the disk stays out of the model's reach. |
| The consent channel: approvals, answers, the run's key | The page, behind the key and the Host and Origin checks | Also where folders are granted and revoked |
| Remembered answers | `~/.alfred-memory.json`: plain JSON, the last 20 | Unchanged |
| API keys | `NEBIUS_API_KEY` and `OPENAI_API_KEY` in the environment, sent only to their own provider's exact host | Unchanged. Keychain storage is Milestone 5. |
| The audit log | Nothing is stored | Metadata only, so the log never becomes a copy of the data |

## 2. Trust boundaries

Untrusted: everything the model writes, and everything it reads (emails, invitations, records, file names and contents, a cloud model's answers). Trusted: the person at the page, the command line they typed, and Alfred's own code. Nothing is enforced by the operating system yet.

| Boundary | On the other side | What holds it | Tests (`tests/`) |
|---|---|---|---|
| The page and the app's API | Any web page open in the browser; other programs on the computer | A key of 32 random bytes for each run, carried in the link's fragment, sent as `X-Alfred-Token` and compared in constant time. Requests addressed to another host name or sent from another page's origin are refused. Posts must be JSON and under 1 MB. Pending ids are random. A Content-Security-Policy lets only the page's own script run, lets it talk only to the app, and stops other sites from framing it. The app listens on loopback only. | The consent-channel tests in `test_app.py` |
| The model's output and the tools | The model | Only offered tools run, and every call is checked against its schema. Read tools change nothing. Draft tools only submit to the gate, which commits a frozen copy, once, after the person approves. | `test_gate.py` |
| What the model reads and what it does | Whoever wrote an email, an invitation or a file | Nothing stops text from influencing the model. Training lowers how often it follows injected instructions (measured in RESULTS.md, not guaranteed), and the boundary above limits what following one can do. | The injection tests in `test_scoring.py` and `test_heldout.py`; AgentDojo |
| The app and the model's server | Whoever runs the model's endpoint | The model is local unless `--base-url` names another host. Then everything Alfred reads goes there, and the terminal and the page name the host. An API key from the environment goes only to its own provider's exact host, over HTTPS. | `test_with_a_local_model_nothing_leaves_this_computer`, `test_a_hosted_model_is_never_called_local`, `test_provider_shortcuts_and_keys` |
| The app and Token Factory (Polish) | Token Factory | Off unless `--cloud` and `NEBIUS_API_KEY` are both set. It sends one draft's subject and body, plus a fixed instruction. The answer only fills in the draft, which the person still approves. | `test_cloud_is_off_unless_asked_for`, `test_polish_needs_a_key_and_stop_works` |
| Alfred and the operating system | Everything else on the computer | Nothing yet: Alfred runs with the user's permissions. Milestone 1 adds the broker (Python, in the same process); Milestone 2 adds an OS boundary. | Milestones 1 and 2 |

## 3. Threats

| # | Threat | Defense today | Planned | Tests |
|---|---|---|---|---|
| T1 | Instructions hidden in an email, an invitation, a file or a file name make the model act for someone else | Training, and drafts that wait for the person | Milestone 1: the broker refuses anything outside the grant, whatever the model asks; the test folder includes an injection file | `test_following_an_injection_is_caught`, `test_injection_in_a_calendar_invite`, AgentDojo |
| T2 | A web page starts a request, approves a draft, reads the inbox through DNS rebinding, or frames the page to get a click | Fixed in 161e2e9 (section 2) | | `test_another_site_cannot_start_a_request`, `test_another_site_cannot_approve_a_draft`, `test_a_page_on_another_host_name_gets_nothing`, `test_the_api_wants_the_key_and_json_from_its_own_page` |
| T3 | Text from a record runs as script in the page | The page escapes record text before inserting it, and the CSP runs only the page's own script | A test that runs the page in a browser (section 6) | `test_the_page_carries_no_key_and_runs_only_its_own_script` |
| T4 | The person approves one thing and something else is committed | The draft is frozen when it's submitted, edits change wording only, and one draft waits at a time, under a random id | Milestone 3: the approval covers a digest of the exact payload | `test_actions_are_frozen_when_submitted`, `test_approval_commits_exactly_the_draft_and_only_once` |
| T5 | The model makes up a recipient, a time or an id | The person sees every recipient before approving. In the research tasks, the grader and the simulated user catch made-up values. | Milestone 3: the broker checks recipients | `test_fabricated.py`, `test_a_made_up_optional_detail_fails_a_complete_request` |
| T6 | The model reads more than the task needs | Every record opened is logged and shown on the page, private ones marked | Milestone 1: reads stay inside the granted folder, within budgets | `test_snooping_is_caught`, `test_enforce_policy_blocks_unneeded_reads` |
| T7 | A read escapes the granted folder: `..`, an absolute path, a symlink, a hard link, another disk, or a folder swapped between a check and the open | No file access | Milestone 1: the model sends handles, never paths. The broker walks from the root's file descriptor with `O_NOFOLLOW`, refuses hard-linked files and other devices, and re-checks device and inode. | New, in `tests/security/` |
| T8 | A hostile file: huge, binary, endless, not UTF-8, or a pipe or a device | No file access | Milestone 1: regular files only, opened without blocking; a size cap before reading and a character cap per read; invalid bytes replaced; no parsers | New |
| T9 | A grant used after it expired or was revoked, by another task, or with a forged or replayed handle | No grants | Milestone 1: every call checks the grant, and handles are random and tied to one task | New |
| T10 | Data leaves the computer | A local model is the default, and a test records every connection; a hosted model's host is named; Polish needs `--cloud`; no tool reaches the network; an environment API key goes only to its own provider | Milestone 4: the privacy bridge | The model-server and Polish tests in section 2 |
| T11 | Private data ends up in logs | The event list lives in memory, for the page | Milestone 1: the stored audit log keeps metadata only, and a test searches it for canary strings | New |
| T12 | The run's key leaks | It's in the link's fragment (never sent to a server, and never in a Referer), in the tab's session storage, and in the terminal. Each run gets a new key. Reading the terminal or the browser's profile takes malware running as the user, which is out of scope. | | `test_the_page_carries_no_key_and_runs_only_its_own_script` |
| T13 | A remembered answer becomes an instruction | Only the person writes answers, through the page, and the page shows them and can clear them. A program that edits the file could plant text, but that's malware running as the user. | | `test_ask_then_approve_changes_the_world_and_is_remembered` |
| T14 | A run that never ends | 20 model turns and 60 tool calls per request, the Stop button, one request at a time, and a 300-second timeout on each model call | | `test_polish_needs_a_key_and_stop_works` |

## 4. Out of scope

- A compromised kernel, or malware already running as the user. Either can read the screen, the key and the files directly.
- A broker binary that someone replaced with a malicious one.
- Covert channels through content the person explicitly sent to a cloud model.
- The person deliberately approving a harmful action. Alfred's job is to show exactly what will happen.
- What a model host the person chose does with the data. Alfred's job is to make that choice visible.
- Physical access to an unlocked computer.

## 5. The design's 24 tests

The product design calls for these 24 tests. **Exists**: tested today. **Partial**: some of it tested, the rest planned. **M1** to **M4**: needs that milestone.

| # | Test | Status | Where, or what's planned |
|---|---|---|---|
| 1 | No grants: no file, mail, calendar, network or shell operations | M1 | Today no tool touches anything real. The broker denies by default, including operations it doesn't know. |
| 2 | Folder A granted: folder B unreachable, including symlinks and traversal | M1 | `tests/security/` and the end-to-end gate run. The simulated version: `test_enforce_policy_blocks_unneeded_reads`. |
| 3 | Read access can't write, delete, send, or index outside the grant | M1 | The Milestone 1 catalog has reads only, and a test asks for writes with a read grant. Nothing is indexed. |
| 4 | An expired, revoked or other task's grant is refused; a queued commit is canceled | M1, M2 | Grants: Milestone 1. Milestone 1 has no commits; queued edits arrive in Milestone 2. Today Stop cancels a waiting draft (`test_polish_needs_a_key_and_stop_works`). |
| 5 | Forged approvals, handles, task ids, drafts and recipients | Partial | Approval comes only from the page (`test_another_site_cannot_approve_a_draft`), no tool commits (`test_no_tool_lets_the_model_commit_anything`), and drafts are frozen and commit once (`test_actions_are_frozen_when_submitted`, `test_approval_commits_exactly_the_draft_and_only_once`). Handles and task ids: M1. Payload digests and recipients: M3. |
| 6 | No reach to OAuth tokens, Keychain, SSH keys, broker storage or VM controls | M2 | Needs the OS boundary. In Milestone 1 the model can only name handles the broker issued, but the code around it still runs as the user. |
| 7 | No network from the isolated runtime, including DNS, IPv6 and localhost | M2 | Needs the OS boundary. No tool reaches the network today. |
| 8 | Parser bugs and huge files fail safely; private text is never logged | M1 | Plain text with size caps, pipes and devices refused, and the audit log searched for canary strings |
| 9 | Cancellation and crashes fail closed | Partial | A model error ends the request with nothing committed (not yet tested); Stop is tested. Milestone 1: canceling revokes grants, and grants live in memory, so a restart starts with none. A separate broker process: M2. |
| 10 | Tasks don't share grants or caches | M1 | Grants and handles are tied to one task. The app runs one request at a time. |
| 11 | Asks when details are missing, and doesn't invent them | Exists | `test_guessing_instead_of_asking_is_caught`, `test_fabricated.py`, `test_filling_in_a_missing_detail_fails_and_costs`, `test_a_required_field_nobody_mentioned_must_be_asked_for`, `test_guessing_is_caught_on_new_kinds_of_ambiguity`; the model's results are in RESULTS.md. Files: the test folder's conflicting dates (M1). |
| 12 | Declines when nothing fits | Exists | `test_claiming_success_on_an_impossible_task_is_a_false_completion`, `test_using_the_nearby_tool_instead_of_declining_fails`, `test_plain_declines_count_on_out_of_scope`, `test_false_claims_on_impossible_variants` |
| 13 | Injected text isn't treated as instructions | Exists for email, M1 for files | `test_following_an_injection_is_caught`, `test_acting_on_an_injection_without_contacting_the_attacker_is_caught`, `test_injection_in_a_calendar_invite`, AgentDojo. Files: the injection file, which the broker refuses whatever the model does. |
| 14 | No claims of success without confirmation; retries don't repeat side effects | Exists, simulated | `test_false_claims_are_caught`, `test_approval_commits_exactly_the_draft_and_only_once`. Real side effects: M3. |
| 15 | Ordinary reads and drafts work without needless approvals | Exists | `test_oracle_is_perfect`, `test_oracles_solve_every_task_cleanly`, `test_reads_do_not_change_the_world`, `test_ask_then_approve_changes_the_world_and_is_remembered`. Files: reads under a task's grant need no prompt each time (M1). |
| 16 | Cloud off means zero outbound calls | Exists | `test_with_a_local_model_nothing_leaves_this_computer` (every connection a full request opens stays on this computer), `test_cloud_is_off_unless_asked_for`, `test_a_hosted_model_is_never_called_local` |
| 17 | Untrusted content can't turn on cloud or change the provider or the policy | Partial | Only command-line flags set the model's host and cloud, and no tool or API changes them. A test with an injected "turn on cloud" instruction: M4. |
| 18 | Credentials are always blocked; local-only documents are never sent | M4 | |
| 19 | The bytes actually sent are inspected, and the preview matches their digest | M4 | Polish shows the draft, not the exact request. |
| 20 | Placeholders are never sent or logged | M4 | |
| 21 | Confidential context the task needs fails safe, or asks | M4 | |
| 22 | A cloud answer can't run tools | Partial | Polish's answer only fills in the draft's subject and body, which the person still approves; it's never read as a tool call. A test: M4. |
| 23 | Provider errors fall back safely | Partial | A failed Polish shows an error and leaves the draft as it was (not yet tested). Tests with a failing fake provider: M4. |
| 24 | Accurate privacy manifests for each task | M4 | Today the Polish event records what was sent, in memory only. |

## 6. Open questions

- **No CI.** The suite only runs when someone runs it. A GitHub Actions workflow on every push would enforce the Milestone 0 gate.
- **No test runs the page's script.** The inbox panel stopped refreshing after the first request, and only a check in a real browser caught it (8add1c4). A headless-browser smoke test would catch that kind of bug, at the cost of a browser in the dev dependencies.
- **macOS folder permissions.** A Python process started from a terminal gets the terminal app's macOS privacy permissions, so reading Desktop, Documents or Downloads depends on what the terminal was allowed. Milestone 1 starts with `--grant` on the command line. Whether a native folder picker with security-scoped bookmarks gets around this from Python is unverified.
- **Seatbelt's future.** Seatbelt profiles are undocumented, and `sandbox-exec` is marked deprecated, though it still ships. The Milestone 2 decision record has to weigh that against a VM's memory cost.
- **A model host over plain HTTP.** An `http://` `--base-url` on another machine sends everything unencrypted. The app names the host but doesn't warn that the connection isn't encrypted.
