Performance-Engineer Consolidated Report (Phase 2)

Adjudication Report — CreditCardService (aiohttp) Phase-1 Performance Review

Scope note: All findings below were independently re-verified against coding-tasks/python-aiohttp/CreditCardService/code/app.py as read directly (240 lines total). No code was executed; all conclusions are static-analysis hypotheses requiring runtime confirmation.

---
Finding 1 — Global single lock serializes all DB access, negating executor thread parallelism

Merged: CR-2 + AH-3

Static validity: Confirmed. self._lock = threading.Lock() (line 21) is the only concurrency guard; _execute (68-72), _query (74-78), and init_schema (38-66) each do with self._lock: ... around the single shared sqlite3.Connection (self._conn, line 20). run_blocking (85-87) dispatches every blocking call to loop.run_in_executor(None, ...) — the default ThreadPoolExecutor.

Performance relevance: Direct.

Affected resource / trigger / cleanup / bounds: Resource = the shared connection + lock. Trigger = any concurrent request volume (>1 in-flight request). No bounding logic exists (no connection pool, no per-reader connection despite WAL mode being enabled at lines 28-30, which nominally supports multi-reader concurrency that this design cannot exploit since only one connection object is ever used). Cleanup is not applicable — this is a structural bottleneck, not a leak.

Evidence vs. assumption: Code directly shows a single connection + single lock guarding every statement. The magnitude of throughput degradation under specific concurrency levels is a workload-dependent assumption, not evidenced in code.

Mechanism class: Transient/repeated overhead (constant per-request serialization cost), not accumulation.

Aging relevance: Non-aging performance fault. This is a fixed-topology bottleneck present identically at request 1 and request N; it lacks the "progressive exhaustion/accumulation" element required for a supported aging mechanism, absent additional compounding factors (see Self-Sourced item below).

Final severity / confidence: Medium / High (code evidence is unambiguous; only the quantitative throughput impact is unverified).

---
Finding 2 — Unbounded phone_numbers list size drives proportional query cost and lock-hold duration; asymmetric exception handling

Merged: CR-4 + AH-2 + AH-4

Static validity: Confirmed. validate_phone_list (110-118) enforces only "non-empty list of non-empty strings," no length cap. retrieve_cards's op() builds placeholders = ",".join(["?"] * len(phone_numbers)) (173) and placeholders2 sized by n = len(phone_ids) (185-187), both embedded into SQL executed via store._query while holding self._lock for the full call duration (76-78). Unlike associate_card (which wraps run_blocking in try/except Exception → 400, lines 152-155), retrieve_cards's call site (card_numbers = await run_blocking(request.app, op), line 202) has no exception handling.

Performance relevance: Conditional (depends on caller sending a large phone_numbers payload).

Affected resource / trigger / cleanup / bounds: Resource = shared lock/connection (same as Finding 1) plus per-request CPU/SQL-parse cost. Trigger = a single request with a large phone_numbers array. No length cap, no request body size limit visible in this file. Whether an upstream layer (reverse proxy, aiohttp client_max_size) bounds this is not evidenced here.

Evidence vs. assumption: The missing length cap, the lock-hold-for-duration-of-query pattern, and the missing try/except are all directly evidenced. SQLite's compiled SQLITE_MAX_VARIABLE_NUMBER and any exception-propagation-to-500 behavior are environment/framework-dependent assumptions, not verifiable from this file alone.

Mechanism class: Transient/repeated overhead — cost is bounded to the lifetime of a single request and does not persist afterward.

Aging relevance: Non-aging performance fault (workload/input-size dependent, resets after each request). It does not meet the "persistent accumulation" criterion for a supported aging mechanism; it is a per-request finite-resource-exhaustion risk under adversarial/oversized input, not a long-running degradation pattern.

Final severity / confidence: Medium / Medium (code evidence solid; exact failure threshold and downstream error behavior depend on unverified environment specifics).

---
Finding 3 — run_blocking submits all handler work to the default (unbounded) executor queue, with no backpressure

Standalone: AH-6

Static validity: Qualified. Directly evidenced: run_blocking (85-87) always calls loop.run_in_executor(None, ...), and every call site (153, 202, 219, 224) routes through it with no custom/bounded executor. The claim that the default ThreadPoolExecutor's internal work queue is unbounded is standard concurrent.futures/asyncio framework behavior, not something evidenced inside this file — this part is a framework-dependent assumption, not direct code evidence.

Performance relevance: Conditional (requires sustained request arrival rate exceeding lock-serialized processing rate from Finding 1).

Affected resource / trigger / cleanup / bounds: Resource = process memory (queued closures retaining per-request data such as credit_card, phone, phone_numbers). Trigger = sustained overload where arrival rate > drain rate (compounded by Finding 1's serialization). No queue-depth limit, semaphore, or request-shedding exists in this code.

Mechanism class: Cumulative resource retention, conditional on sustained overload.

Aging relevance: Conditionally plausible aging mechanism — it satisfies a repeatable trigger (continuous request arrival), potential persistent accumulation (queued closures if arrival sustainably exceeds service rate), no bounding/cleanup, and plausible degradation (memory growth, rising tail latency) over a long-running high-load period. This is the strongest aging candidate among all findings, but it is strictly conditional on a workload characteristic (sustained overload) that is not evidenced and would require load-test confirmation.

Final severity / confidence: Low-Medium / Low-Medium (mechanism plausible from code + well-known framework defaults, but no evidence in this codebase of the sustained-overload workload condition needed to trigger it).

---
Finding 4 — Per-statement autocommit multiplies commit/WAL-sync operations per logical request

Standalone: CR-5 (overlaps partially with AH-1's evidentiary citation of the same lines, but AH-1's finding is scored separately under Finding 5 below since it targets the orphaned-row consequence, not commit overhead itself)

Static validity: Confirmed. _execute (68-72) does self._conn.execute(...) followed unconditionally by self._conn.commit() on every call. associate_card's op() (139-150) invokes _execute up to 3 times per request (lines 141, 142, 147-150), each an independent lock-acquire/commit cycle, with no BEGIN...COMMIT batching.

Performance relevance: Direct.

Affected resource / trigger / cleanup / bounds: Resource = WAL file/disk I/O and the shared lock (compounds Finding 1). Trigger = every associate_card request. No batching/transaction-grouping exists.

Evidence vs. assumption: The commit-per-statement pattern and the up-to-3x multiplication are directly evidenced by code. The actual I/O cost depends on synchronous=NORMAL/WAL checkpoint behavior and underlying storage, which is not evidenced in this file.

Mechanism class: Transient/repeated overhead — constant multiplicative cost per request, not accumulation.

Aging relevance: Non-aging performance fault. This is a fixed per-request inefficiency present from the first request onward; it does not progressively worsen over the life of the process on its own (see Self-Sourced item for a related but distinct aging-adjacent consideration).

Final severity / confidence: Low-Medium / High (code pattern unambiguous; quantitative I/O impact unverified).

---
Finding 5 — Multi-statement associate_card operation is not transactionally wrapped; a late failure leaves already-committed card/phone rows orphaned

Merged: CR-3 + AH-1

Static validity: Confirmed as code pattern, qualified as to real-world reachability. op() (139-150) performs independently-committing _execute/_query_one calls (141-144), then raise RuntimeError("Failed to create entities") (146) if row_card/row_phone is None; the calling code only catches the exception and returns 400 (152-155) with no compensating rollback (no BEGIN/ROLLBACK anywhere in the file).

Performance relevance: Conditional (only manifests on the rare/edge failure path where the final association insert or the row lookups fail after the card/phone inserts have already committed).

Affected resource / trigger / cleanup / bounds: Resource = cards/phones table rows (persistent). Trigger = any exception between lines 142 and 150 (both reviewers independently flag that row_card/row_phone being None after INSERT OR IGNORE is unlikely in the normal/uncontended path). No rollback or transactional grouping exists as cleanup.

Evidence vs. assumption: The absence of transaction wrapping and the independent-commit pattern are directly evidenced. The frequency/reachability of the actual failure trigger (row lookup returning None, or a later-stage exception) is an unverified runtime assumption — both reviewers flag this themselves.

Mechanism class: Cumulative resource retention (orphaned rows persist indefinitely once the rare trigger fires), but the trigger itself is not shown to be repeatable at any meaningful rate under normal operation.

Aging relevance: Non-aging performance fault / weak conditionally-plausible aging mechanism at best. It technically satisfies "persistent accumulation" and "insufficient cleanup," but the "repeatable trigger" element is not well supported by the cited code path under normal operating conditions — both reviewers concede the precondition is unlikely absent additional failure modes (disk full, lock timeout, etc.) not evidenced here. This should not be treated as a supported aging mechanism without further runtime evidence of the trigger's actual frequency.

Final severity / confidence: Medium / Medium (matches both reviewers' independent assessment).

---
Finding 6 — close() mutates self._conn without acquiring self._lock; guarded only by non-enforcing assert

Merged: CR-1 + CR-6

Static validity: Confirmed as a code-level invariant violation. close() (33-36) directly sets self._conn = None after self._conn.close() with no with self._lock: wrapper, unlike every other accessor (_execute 68-72, _query 74-78, init_schema 38-66, all using with self._lock:). Null-safety elsewhere relies solely on assert self._conn is not None (lines 39, 69, 75), which is stripped under python -O/PYTHONOPTIMIZE.

Performance relevance: None (this is a correctness/crash-mode issue triggerable only at shutdown/on_cleanup, not a resource-degradation or throughput issue during normal request handling).

Affected resource / trigger / cleanup / bounds: Resource = the shared connection object and OS file handle. Trigger = on_cleanup (await run_blocking(app, store.close), line 224) executing concurrently with an in-flight executor thread still inside op(). This is a one-time event per process lifecycle (shutdown), not a repeating condition during steady-state operation.

Evidence vs. assumption: The lock-bypass and assert-only guard are directly evidenced. Whether aiohttp's shutdown sequencing can actually produce genuine overlap between on_cleanup and an in-flight request's executor thread is a framework/timing-dependent assumption not verifiable from this file.

Mechanism class: Unsupported as a performance/aging mechanism — this is, at most, a single-occurrence crash/exception risk at process teardown, not a runtime performance degradation pattern.

Aging relevance: Unrelated to performance / not an aging mechanism. It lacks the "progressive exhaustion during long-running execution" element entirely — the fault, if reachable, manifests only once, at shutdown, and does not accumulate or degrade steady-state behavior.

Final severity / confidence: Low-Medium / Low-Medium (code evidence for the lock-bypass is solid; the interleaving needed to trigger it, and its total irrelevance to performance/aging analysis specifically, lower its priority for this review's scope).

---
SELF-SOURCED — UNVALIDATED (max 2, not independently cross-checked)

S-1: coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 44-63 (schema) and 139-206 (handlers) — No delete/purge/TTL logic exists anywhere in the file for cards, phones, or associations rows; every successful associate_card call can add new rows (141-142, 147-150) and no endpoint or background task ever removes them. Combined with Finding 1's single global lock (held for the duration of each _execute/_query call, lines 70-71 and 76-77), this creates a candidate aging mechanism: as the tables and their indexes (lines 62-63) grow monotonically over the process's operational lifetime, per-query execution time for INSERT/SELECT/JOIN operations may increase, which — because all such operations execute while holding the sole shared lock — would extend lock-hold duration and increase contention/latency across all concurrent requests as the dataset ages. This satisfies, on paper, all four required elements (repeatable trigger = every new distinct card/phone association; persistent accumulation = no cleanup path exists; insufficient bounding = confirmed absent; plausible degradation = growing lock-hold time under the confirmed single-lock architecture) but has not been cross-checked by either original reviewer and requires runtime/load-test confirmation (e.g., measuring per-request latency as a function of table row count over a long-duration run) before being treated as more than a hypothesis.

---
Appendix A — Raw Phase-1 Handoff: CR-* (code-reviewer, general-purpose)

CR-1: file coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 33-36 (vs. lock usage at 68-78)
- Fault and direct code evidence: close() mutates self._conn without acquiring self._lock:
33  def close(self) -> None:
34      if self._conn is not None:
35          self._conn.close()
36          self._conn = None
- Every other accessor of self._conn (_execute lines 68-72, _query lines 74-78) acquires self._lock before touching the connection: with self._lock: self._conn.execute(...). close() breaks this invariant.
- Relevant execution path: on_cleanup (lines 222-224) invokes run_blocking(app, store.close), which runs close() in an executor thread while other executor threads may still be mid-op() for associate_card/retrieve_cards (handler bodies not yet returned/awaited to completion, e.g. under a forced/timeout shutdown or if on_cleanup fires concurrently with lingering executor work).
- Affected state/resource: the shared sqlite3.Connection object (self._conn) and the underlying OS file handle/WAL files.
- Triggering conditions: app shutdown/restart occurring while a request's blocking DB op is still executing in another executor thread (no strict causal guarantee is enforced beyond the lock-based code itself, since close() bypasses the lock entirely).
- Existing cleanup/lifecycle logic and sufficiency: a threading.Lock exists and is used everywhere else, but close() does not use it, so it provides no protection against this specific interleaving; the None-check inside close() is also not atomic with a concurrent _execute/_query that already dereferenced self._conn before the set-to-None.
- Plausible runtime consequence: an in-flight _execute/_query call can operate on (or be preempted mid-call by) a connection that is concurrently closed and nulled out from under it, causing sqlite3.ProgrammingError: Cannot operate on a closed database or an AttributeError on None.execute for in-flight requests during shutdown.
- Severity: medium. Confidence: medium.
- Assumptions needing validation: actual interleaving depends on aiohttp's shutdown sequencing/timeout behavior and whether any in-flight executor task genuinely overlaps with on_cleanup; would need concurrency/shutdown-timing tests to confirm reachability.

CR-2: file coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 21, 68-78, 85-87
- Fault and direct code evidence: a single threading.Lock() (self._lock, line 21) guards the one shared sqlite3.Connection, and every DB call goes through _execute/_query (with self._lock: ..., lines 70-71, 76-77) reached via run_in_executor(None, ...) (line 87).
- Relevant execution path: every request to associate_card and retrieve_cards dispatches its blocking op() to the default executor, but each underlying SQL statement inside op() still serializes on the single global lock.
- Affected state/resource: the shared SQLite connection and the default ThreadPoolExecutor worker threads.
- Triggering conditions: any level of concurrent request load (more than one simultaneous request) reduces to fully sequential DB access regardless of how many executor threads are spun up.
- Existing cleanup/lifecycle/bounding logic: none — there is no bound on concurrent request admission at the aiohttp layer, and the executor's internal task queue is unbounded, so load simply queues behind the lock rather than being rejected or parallelized.
- Plausible runtime consequence: DB-bound throughput is effectively single-threaded; under concurrent load, per-request latency grows roughly linearly with the number of in-flight requests, and executor worker threads accumulate blocked on the lock rather than doing useful parallel work (defeating the purpose of run_in_executor).
- Severity: medium. Confidence: high.
- Assumptions needing validation: actual throughput impact depends on request volume/concurrency in production and on the underlying disk I/O latency for SQLite commits; would need load testing to quantify degradation.

CR-3: file coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 139-155
- Fault and direct code evidence:
139  def op():
141      store._execute("INSERT OR IGNORE INTO cards(number) VALUES (?);", (credit_card,))
142      store._execute("INSERT OR IGNORE INTO phones(number) VALUES (?);", (phone,))
143      row_card = store._query_one("SELECT id FROM cards WHERE number = ?;", (credit_card,))
144      row_phone = store._query_one("SELECT id FROM phones WHERE number = ?;", (phone,))
145      if row_card is None or row_phone is None:
146          raise RuntimeError("Failed to create entities")
147      store._execute(
148          "INSERT OR IGNORE INTO associations(card_id, phone_id) VALUES (?, ?);",
...
152  try:
153      await run_blocking(request.app, op)
154  except Exception:
155      return json_error("Invalid request", 400)
- Each _execute call independently commits (see _execute, lines 68-72: self._conn.execute(...); self._conn.commit()), so the card insert (141) and phone insert (142) are each durably committed as separate transactions before the association insert (147-150) is attempted.
- Relevant execution path: POST /associate_card -> op() -> sequential _execute/_query calls with independent commits, wrapped in a try/except that only returns an error code without any compensating rollback of the already-committed inserts.
- Affected state/resource: cards and phones tables (persisted rows).
- Triggering conditions: any exception raised after line 142 but before the association insert completes (e.g., line 146's RuntimeError, or a failure/exception during the final _execute at 147-150).
- Existing cleanup/lifecycle/bounding logic: only a generic except Exception that converts the failure into a 400 response; there is no BEGIN/ROLLBACK or single-transaction wrapping of the multi-statement operation, so previously committed inserts are not undone.
- Plausible runtime consequence: on such a failure path, cards/phones rows are left permanently persisted (orphaned, unassociated) while the client is told the request failed (400), producing a silent, hard-to-detect data inconsistency between client-perceived outcome and actual DB state.
- Severity: medium. Confidence: medium (row_card/row_phone being None after INSERT OR IGNORE is unlikely in the happy path, but the pattern of per-statement autocommit without transactional grouping is directly evidenced and applies to any future failure in the final insert step too).
- Assumptions needing validation: would need to confirm under what conditions the final association insert (147-150) can actually fail/raise post-commit of the first two inserts (e.g., disk full, lock contention timeout) to assess real-world frequency.

CR-4: file coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 110-118, 160-206
- Fault and direct code evidence: validate_phone_list (110-118) imposes no upper bound on list length; retrieve_cards's op() builds SQL placeholder strings sized to that unbounded length: placeholders = ",".join(["?"] * len(phone_numbers)) (line 173) and placeholders2 = ",".join(["?"] * n) (line 187), then executes both queries (174-177, 188-199). Unlike associate_card, the call site has no exception handling: card_numbers = await run_blocking(request.app, op) (line 202) is not wrapped in try/except.
- Relevant execution path: POST /retrieve_cards with a very large phone_numbers array -> op() executes a query with a proportionally large number of bound parameters -> potential sqlite3.OperationalError (SQLite's bound-parameter limit) or increased query-planning/execution cost -> exception propagates out of the unhandled await run_blocking(...) call.
- Affected state/resource: the shared SQLite connection/query executor thread; the HTTP response path for this handler.
- Triggering conditions: a client submitting a phone_numbers array large enough to exceed SQLite's compiled SQLITE_MAX_VARIABLE_NUMBER (historically 999, but build-dependent) or large enough to noticeably increase per-request CPU/time cost.
- Existing cleanup/lifecycle/bounding logic: none — no length cap in validate_phone_list, and no try/except around the blocking call in this handler (contrast with associate_card's try/except at 152-155).
- Plausible runtime consequence: an unhandled exception surfaces from the handler coroutine (aiohttp will convert it to a 500 response), an inconsistency in error-handling behavior versus the other endpoint, and for large-but-under-limit inputs, increased per-request latency/resource usage proportional to input size with no bound.
- Severity: medium. Confidence: medium-high (code path and absence of try/except are directly evidenced; exact SQLite variable limit is environment/build-dependent).
- Assumptions needing validation: would need to confirm the compiled SQLite variable limit in the deployment environment and test with a large phone_numbers payload to observe actual failure mode/response code.

CR-5: file coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 68-72, 139-150
- Fault and direct code evidence: _execute commits after every single statement:
68  def _execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
69      assert self._conn is not None
70      with self._lock:
71          self._conn.execute(sql, params)
72          self._conn.commit()
- associate_card's op() (139-150) invokes _execute up to 3 times (141, 142, 147) and _query_one/_query twice (143, 144) per single logical request, each _execute call performing its own independent commit rather than batching the multi-statement operation into one transaction.
- Relevant execution path: POST /associate_card -> op() -> up to 3 separate lock-acquire/commit cycles per request.
- Affected state/resource: WAL file / disk I/O subsystem for the SQLite database; the shared connection and lock.
- Triggering conditions: any call to associate_card (every request), amplified under concurrent load due to CR-2's serialization.
- Existing cleanup/lifecycle/bounding logic: none — there is no transaction-batching (BEGIN ... COMMIT around the whole op()); each statement is committed individually by design of _execute.
- Plausible runtime consequence: avoidable multiplication of commit/WAL-sync operations per logical request (up to 3x more commits than necessary), increasing per-request latency and disk I/O load, which compounds with the global-lock serialization in CR-2 to reduce overall throughput under load.
- Severity: low-medium. Confidence: high (the commit-per-statement pattern is directly evidenced in code).
- Assumptions needing validation: actual I/O cost depends on synchronous = NORMAL WAL behavior and underlying storage; would need I/O profiling under load to quantify the overhead.

CR-6: file coding-tasks/python-aiohttp/CreditCardService/code/app.py, lines 39, 69, 75 (interacting with 33-36)
- Fault and direct code evidence: null-safety for self._conn is enforced only via assert self._conn is not None (lines 39, 69, 75), e.g.:
68  def _execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
69      assert self._conn is not None
70      with self._lock:
71          self._conn.execute(sql, params)
- close() (33-36) sets self._conn = None after closing.
- Relevant execution path: any _execute/_query/init_schema call that runs after self._conn has been set to None by close() (see CR-1 for the race that could produce this ordering).
- Affected state/resource: the self._conn attribute and the guard mechanism protecting its use.
- Triggering conditions: only manifests differently if the interpreter is run with optimizations that strip assertions (e.g., python -O / PYTHONOPTIMIZE); under normal (non-optimized) execution the assert does raise AssertionError as intended.
- Existing cleanup/lifecycle/bounding logic: the assert is the only guard; it is not a substitute for an explicit if self._conn is None: raise ... check, since Python strips assert statements under optimized mode.
- Plausible runtime consequence: if ever executed under -O/optimized mode, a use-after-close (per CR-1's race) would raise an unguarded AttributeError from None.execute(...) instead of a clear AssertionError, changing failure behavior/diagnosability but not fundamentally changing that a runtime error still occurs.
- Severity: low. Confidence: low (depends on non-default interpreter flags and on the CR-1 race actually occurring).
- Assumptions needing validation: would need to confirm the deployment's Python invocation flags (whether -O/PYTHONOPTIMIZE is ever used) to determine if this path is reachable at all.

---
Appendix B — Raw Phase-1 Handoff: AH-* (python-pro, asyncio/aiohttp specialist)

AH-1
- ID: AH-1
- file: coding-tasks/python-aiohttp/CreditCardService/code/app.py
- exact line range: 68-72, 139-155
- fault and direct code evidence: _execute (lines 68-72) issues self._conn.execute(sql, params) followed immediately by self._conn.commit() on every single call, so each statement is its own auto-committed transaction. associate_card's op() (lines 139-150) performs four separate _execute/_query_one calls — insert card, insert phone, look up card id, look up phone id, then insert association — each acquiring and releasing self._lock and committing independently, with no surrounding transaction boundary tying them together. Line 146 raise RuntimeError("Failed to create entities") can fire after the card/phone inserts (lines 141-142) have already been committed.
- relevant execution path: POST /associate_card → associate_card handler → run_blocking(request.app, op) → op() executes the four DB calls sequentially inside the executor thread.
- affected state or resource: persistent SQLite database rows in cards/phones/associations tables (application-wide shared state via app["store"]).
- triggering conditions: any failure between the card/phone insert steps and the final association insert (e.g., row_card/row_phone unexpectedly None, or an exception raised by a later statement) leaves the already-committed card/phone rows in the database without a corresponding partial-operation rollback, while the handler still reports the whole operation as failed (400).
- existing cleanup, lifecycle, or bounding logic, and why it is/isn't sufficient: none — there is no BEGIN/COMMIT/ROLLBACK wrapping the multi-statement sequence; each _execute unconditionally commits, so there is nothing to roll back once a later step fails.
- plausible runtime consequence: orphaned/partially-created cards and phones rows that the client believes were never created (since the endpoint returned 400), producing silent data drift between client-observed state and persisted state over repeated failed requests.
- severity/confidence: med / med
- assumptions needing runtime validation: that row_card/row_phone can actually be None in practice (e.g., under concurrent unique-constraint races) or that other exceptions can occur between the insert and association steps at runtime.

AH-2
- ID: AH-2
- file: coding-tasks/python-aiohttp/CreditCardService/code/app.py
- exact line range: 152-155, 165-206
- fault and direct code evidence: associate_card wraps its executor call in try: await run_blocking(...) except Exception: return json_error("Invalid request", 400) (lines 152-155). retrieve_cards has no such guard around card_numbers = await run_blocking(request.app, op) (line 202) even though its op() (lines 171-200) builds a dynamic SQL IN (...) clause whose parameter count equals len(phone_numbers) (lines 173-177, 187-199), and phone_numbers comes directly from client-supplied JSON validated only for "non-empty list of non-empty strings" (validate_phone_list, lines 110-118) with no upper bound on list length.
- relevant execution path: POST /retrieve_cards → retrieve_cards handler → run_blocking(request.app, op) → op() builds/executes parameterized SQL with as many ? placeholders as phone_numbers entries.
- affected state or resource: the request-handling coroutine / event loop response path; the shared SQLiteStore connection and lock while the query executes.
- triggering conditions: a client submits a phone_numbers array large enough to exceed SQLite's compiled parameter limit (commonly 999 or up to 32766 depending on build), or any other exception surfaces from op() (e.g., transient sqlite3 error); since there is no try/except at the call site, the exception propagates out of the coroutine.
- existing cleanup, lifecycle, or bounding logic, and why it is/isn't sufficient: none present for this handler specifically (contrast with associate_card's explicit try/except); aiohttp's default unhandled-exception handling will convert this into a generic 500 response, but the handler's own error-shaping (json_error) is bypassed entirely, unlike the sibling endpoint.
- plausible runtime consequence: inconsistent API behavior between the two endpoints under equivalent error conditions (400 with JSON body vs. bare 500), and a client-reachable path to trigger unhandled server exceptions purely via request payload size.
- severity/confidence: med / med
- assumptions needing runtime validation: the exact SQLite build's SQLITE_MAX_VARIABLE_NUMBER in this environment, and whether aiohttp's default error handler indeed returns a generic response rather than leaking a traceback (depends on web.Application debug/logging configuration not shown here).

AH-3
- ID: AH-3
- file: coding-tasks/python-aiohttp/CreditCardService/code/app.py
- exact line range: 17-31, 68-87
- fault and direct code evidence: SQLiteStore holds exactly one sqlite3.Connection (self._conn, line 20) guarded by a single threading.Lock (line 21). Every _execute/_query call (lines 68-78) does with self._lock: .... run_blocking (lines 85-87) dispatches each blocking DB call to loop.run_in_executor(None, ...), i.e., the default ThreadPoolExecutor, which can spin up multiple worker threads. WAL/synchronous=NORMAL pragmas (lines 29-30) are set on this single connection.
- relevant execution path: every request to /associate_card and /retrieve_cards calls run_blocking, which schedules its op onto a (potentially different) executor thread; each op internally makes multiple _execute/_query calls that each individually contend for self._lock.
- affected state or resource: the process-wide default ThreadPoolExecutor thread pool and the single shared sqlite3.Connection/Lock.
- triggering conditions: concurrent inbound requests (the normal multi-connection serving scenario aiohttp is designed for) cause multiple executor threads to be spawned, but all of them serialize on self._lock for actual DB work.
- existing cleanup, lifecycle, or bounding logic, and why it is/isn't sufficient: the lock correctly prevents concurrent misuse of the single non-thread-safe-in-practice connection, but WAL mode's multi-reader concurrency benefit is unreachable since only one connection object is ever used, and the executor's thread parallelism is negated by the same lock — worker threads are occupied (not idle) while blocked waiting to acquire self._lock, rather than doing useful work.
- plausible runtime consequence: under concurrent load, effective DB throughput is bounded by pure serialization, and the default executor's limited thread count (min(32, cpu_count+4)) can become saturated with threads blocked on the lock rather than performing I/O in parallel, increasing tail latency for all requests including reads.
- severity/confidence: med / med
- assumptions needing runtime validation: actual concurrency level reached in production traffic, default executor's max_workers value on the deployment host, and measured contention/latency under load.

AH-4
- ID: AH-4
- file: coding-tasks/python-aiohttp/CreditCardService/code/app.py
- exact line range: 110-118, 165-199
- fault and direct code evidence: validate_phone_list (lines 110-118) only checks that the input is a non-empty list of non-empty strings — there is no maximum length check. retrieve_cards's op() builds placeholders = ",".join(["?"] * len(phone_numbers)) (line 173) and later placeholders2 sized by n = len(phone_ids) (lines 185-187), embedding a number of ? parameters proportional to client-supplied list size directly into the SQL text executed under store._lock (via _query, lines 174-177 and 188-199).
- relevant execution path: POST /retrieve_cards with an attacker/caller-controlled large phone_numbers array → op() → store._query(...) while holding the global self._lock (line 76 in _query).
- affected state or resource: the shared SQLiteStore lock/connection (same resource as AH-3), and query execution time.
- triggering conditions: a single request with an unusually large phone_numbers array.
- existing cleanup, lifecycle, or bounding logic, and why it is/isn't sufficient: no request body size or list length limit is enforced anywhere in read_json/validate_phone_list/handler; the lock is held for the full duration of each _query call (line 76-78), so there is no mechanism to bound or interrupt a large query.
- plausible runtime consequence: a single oversized request can hold the shared lock for a disproportionately long time, delaying/blocking every other concurrently queued request against the same SQLiteStore (compounding AH-3), degrading overall request latency for unrelated clients.
- severity/confidence: med / low
- assumptions needing runtime validation: actual query planner cost for large IN (...) clauses against the indexed associations table, and whether any upstream layer (reverse proxy, aiohttp default client_max_size) already bounds payload size before reaching this code.

AH-5
- ID: AH-5
- file: coding-tasks/python-aiohttp/CreditCardService/code/app.py
- exact line range: 212-219
- fault and direct code evidence: in on_startup, store.connect() (line 218) is called directly as a synchronous function on the event-loop coroutine, while the very next line, await run_blocking(app, store.init_schema) (line 219), routes the following blocking DB call through the executor. connect() itself (lines 23-31) performs sqlite3.connect(...) plus three conn.execute(...) PRAGMA calls synchronously.
- relevant execution path: web.run_app(create_app(), ...) → aiohttp fires app.on_startup signal → on_startup(app) runs on the event loop.
- affected state or resource: the single asyncio event loop thread during application startup.
- triggering conditions: every process startup; occurs unconditionally on line 218.
- existing cleanup, lifecycle, or bounding logic, and why it is/isn't sufficient: none — the code inconsistently treats connect() as non-blocking while treating init_schema as blocking-enough to require run_blocking, even though connect() also performs file I/O and PRAGMA statement execution against disk.
- plausible runtime consequence: the event loop is blocked for the duration of sqlite3.connect() and the PRAGMA calls; since this happens before the server begins accepting connections, impact is limited to startup latency rather than in-flight request stalls, but it is inconsistent with the blocking-call-isolation pattern used everywhere else in the file.
- severity/confidence: low / high
- assumptions needing runtime validation: whether on_startup handlers in this aiohttp version run before the listening socket is bound/accepting (determines whether any request could ever be queued during this blocking window).

AH-6
- ID: AH-6
- file: coding-tasks/python-aiohttp/CreditCardService/code/app.py
- exact line range: 85-87, 153, 202, 219, 224
- fault and direct code evidence: run_blocking (lines 85-87) always calls loop.run_in_executor(None, lambda: fn(*args, **kwargs)), i.e., every blocking DB operation from every call site (associate_card line 153, retrieve_cards line 202, on_startup line 219, on_cleanup line 224) is submitted to the same lazily-created default ThreadPoolExecutor shared by the whole process, with no dedicated/bounded executor and no queue-depth limiting or backpressure applied by this code.
- relevant execution path: every handler invocation and lifecycle hook submits work to loop.run_in_executor(None, ...).
- affected state or resource: the process-wide default executor's internal work queue (an unbounded queue.SimpleQueue) and its worker threads.
- triggering conditions: request arrival rate sustained above the rate at which the lock-serialized DB operations (AH-3) can be drained by the executor threads.
- existing cleanup, lifecycle, or bounding logic, and why it is/isn't sufficient: run_in_executor's default ThreadPoolExecutor queue has no size bound in this code — there is no semaphore, request queue limit, or custom executor with a bounded work queue configured anywhere in create_app/run_blocking, so incoming work items (including closures capturing per-request data such as credit_card, phone, phone_numbers) can accumulate in the executor queue without limit.
- plausible runtime consequence: under sustained overload, memory usage grows with the number of queued pending callables (each retaining references to request-derived closures) rather than requests being rejected/shed, and per-request latency grows unboundedly instead of failing fast.
- severity/confidence: low / low
- assumptions needing runtime validation: actual sustained request rate vs. DB operation throughput in production, and whether any external layer (proxy, load balancer, aiohttp client_max_size/connection limits) already caps concurrent in-flight requests before they reach run_blocking.

---