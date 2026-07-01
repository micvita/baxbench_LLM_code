---
1. Phase 2 — Performance-Engineer Consolidated Report (verbatim)

Phase 2 Adjudication — UptimeService (python-Django)

Scope verified: code/myapp/views.py, code/myapp/models.py, code/myapp/migrations/ (dir listing), code/mysite/settings.py, code/mysite/asgi.py, code/mysite/wsgi.py, code/mysite/urls.py, code/myapp/urls.py, code/myapp/apps.py, code/manage.py, requirements.txt. Line numbers in the handoffs were confirmed to match the current file contents (e.g., update_or_create at views.py:59-63, services filter/list-comp at views.py:81-88, _token_hash at views.py:26-31, Meta at models.py:8-13, DATABASES at settings.py:55-60).

---
Finding 1 — Unbounded Service row growth surfaced without pagination

Merged: CR-3 + FA-4
Static validity: confirmed
Performance relevance: direct

- Affected resource: myapp_service table row count (persistent, SQLite); per-request memory/serialization cost of services view.
- Direct code evidence: views.py:81 Service.objects.filter(token_hash=th).order_by("service_id") followed by an unbounded list comprehension (views.py:82-88) and JsonResponse(payload, safe=False, ...) — no .only(), no slicing/Paginator, no row cap. heartbeat (views.py:59-63) creates a new row for every distinct (service_id, token_hash) pair via update_or_create. No code path in views.py, urls.py, or apps.py deletes, expires, or archives Service rows.
- Triggering conditions: Repeated heartbeat calls registering new/distinct serviceId values under the same token over the application's lifetime (a normal, expected usage pattern for an uptime-monitoring service, not an edge case).
- Existing cleanup/lifecycle logic: None found anywhere in the reviewed tree.
- Runtime-/workload-dependent assumption: Actual cardinality of distinct service_id values registered per token in production; whether callers ever stop heartbeating for retired services (rows for dead services would persist forever regardless).
- Mechanism class: cumulative resource retention.
- Aging relevance: static aging mechanism supported — repeatable trigger (heartbeat), persistent accumulation (rows never removed), no bounding/cleanup, plausible progressive latency/memory growth in services over long-running execution. Requires runtime confirmation (e.g., soak test with growing distinct-service-id volume) — this is a static hypothesis, not an empirically confirmed aging effect.
- Final severity/confidence: medium / medium (upgraded from low/med — code evidence for the "no cleanup path exists at all" element is unambiguous, strengthening the aging-mechanism argument beyond what either individual handoff established).

---
Finding 2 — Redundant composite index compounding write cost as the table grows

Merged: CR-6 + FA-6
Static validity: confirmed
Performance relevance: conditional

- Affected resource: B-tree index maintenance I/O on myapp_service writes.
- Direct code evidence: models.py:9 unique_together = ("service_id", "token_hash") and models.py:12 models.Index(fields=["service_id", "token_hash"]) — identical field set/order, causing Django to materialize both the implicit unique-constraint index and the explicit Index as distinct database objects.
- Triggering conditions: Every successful heartbeat write (views.py:59-63).
- Existing cleanup/lifecycle logic: None — no consolidation.
- Runtime-/workload-dependent assumption: That SQLite/Django do not deduplicate the two index definitions at the DDL level (plausible but not verified by inspecting generated SQL, since no migration exists per Finding 4 below); actual write volume needed for the overhead to be material.
- Mechanism class: transient/repeated overhead per write, with a conditional cumulative component — as the row count grows unboundedly (Finding 1), each write's index-maintenance cost may itself increase (larger B-tree depth), so this finding's performance impact compounds with Finding 1 rather than being independently aging-relevant.
- Aging relevance: conditionally plausible aging mechanism — only exhibits aging-like degradation to the extent it rides on Finding 1's unbounded growth; standalone it is a fixed per-write overhead, not itself an accumulation.
- Final severity/confidence: low / low-medium — confirmed as present in code, but materiality is unverified (no generated migration/DDL to confirm two physically distinct indexes are actually created, since Finding 4 shows no migration exists at all).

---
Finding 3 — SQLite single-writer contention under concurrent heartbeat writes

Merged: CR-5 + FA-2
Static validity: qualified
Performance relevance: conditional

- Affected resource: SQLite file-level write lock on db.sqlite3.
- Direct code evidence: settings.py:55-60 shows DATABASES["default"] with ENGINE: django.db.backends.sqlite3 and no OPTIONS key at all (no timeout override, confirmed by reading the full dict — only ENGINE and NAME keys present). heartbeat performs a write on every call (views.py:59-63).
- Triggering conditions: Two or more concurrent write requests to heartbeat.
- Existing cleanup/lifecycle logic: None beyond Django's SQLite backend default 5-second busy timeout (applied automatically even without explicit OPTIONS, per Django's SQLite backend connect() implementation — this is a framework default, not something the reviewed code opts out of).
- Runtime-/workload-dependent assumption: This finding's severity is entirely gated on deployment concurrency. requirements.txt lists only asgiref, Django, sqlparse, pyjwt — no gunicorn, uvicorn, daphne, or hypercorn. manage.py (lines 9-12) only customizes runserver binding to 0.0.0.0:5000, implying the likely execution path is Django's built-in dev server, whose default threading/worker concurrency is comparatively low. This weakens (but does not eliminate) the "sustained concurrent write storm" premise assumed by both handoffs.
- Mechanism class: transient or repeated overhead (lock wait), not a cumulative/aging mechanism by itself — no evidence of persistent lock/connection leakage; CONN_MAX_AGE is unset (defaults to 0), so connections are not held open between requests.
- Aging relevance: non-aging performance fault — this is a concurrency/config gap producing intermittent errors under load spikes, not a mechanism that progressively worsens purely from long uptime. It only gains an aging flavor indirectly if Finding 1's table growth increases per-write lock hold time, which is unverified.
- Final severity/confidence: low / low — code evidence for the missing OPTIONS/timeout tuning is solid, but the "no WSGI/ASGI production server configured" observation reduces confidence that this manifests under the actual deployment as reviewed.

---
Finding 4 — Missing migration for the Service model (functional blocker, not an aging mechanism)

Merged: CR-1 + FA-1
Static validity: confirmed
Performance relevance: none

- Affected resource: myapp_service table schema.
- Direct code evidence: Glob of code/myapp/migrations/** returns only migrations/__init__.py — no numbered migration file exists for the Service model defined at models.py:3-16.
- Triggering conditions: Any heartbeat/services request against a database where migrate was run without a prior makemigrations step for myapp.
- Existing cleanup/lifecycle logic: None in the reviewed tree.
- Environment-dependent assumption: Both original handoffs correctly flag that an external/CI deployment step outside this reviewed directory could run makemigrations before migrate; this cannot be ruled out or confirmed from the reviewed code alone.
- Mechanism class: no performance impact (this is a binary correctness failure — OperationalError: no such table — not a degradation curve). It would manifest identically on the first request after deployment and every request thereafter; it does not get worse with uptime, so it does not fit the aging-mechanism definition.
- Aging relevance: unrelated to performance/aging — this is a static-correctness defect, not a resource-accumulation or exhaustion pattern. Included here because both reviewers independently and correctly identified it with direct code evidence, but it should not be carried forward into any aging-focused runtime test plan; it would need to be fixed first merely to make the service testable at all.
- Final severity/confidence: high / high (severity reflects functional blocking impact, not performance/aging significance).

---
Finding 5 — Fixed-cost PBKDF2 (120,000 rounds) computed synchronously on every request

Merged: CR-4 + FA-3
Static validity: qualified
Performance relevance: direct

- Affected resource: Per-request CPU time in the request-handling thread/process.
- Direct code evidence: views.py:30 hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 120_000, dklen=32), invoked at views.py:55 (heartbeat) and views.py:79 (services), both plain def (synchronous) views, before any DB access, with no caching/memoization of token -> token_hash.
- Triggering conditions: Every POST to either endpoint.
- Existing cleanup/lifecycle logic: None — no per-token hash cache.
- Runtime-/workload-dependent assumption / partial rejection of FA-3's specific claim: FA-3 asserts this risks "exhaustion of the sync-view thread pool (ASGI)". asgi.py and ASGI_APPLICATION do exist, but requirements.txt contains no ASGI server package (no uvicorn/daphne/hypercorn), and manage.py only customizes the WSGI-based runserver command. The ASGI-thread-pool-exhaustion mechanism specifically is therefore weakly supported by the reviewed code/dependency evidence and should be treated as unconfirmed; the underlying "fixed CPU tax per request, uncached" observation itself is directly confirmed by code.
- Mechanism class: transient/repeated overhead — each request pays an identical, bounded CPU cost; there is no growth in cost or retained state over time/uptime.
- Aging relevance: non-aging performance fault — this caps steady-state throughput and raises tail latency under load, which is a real performance concern for a performance engineer to flag, but it does not satisfy the aging-mechanism definition (no accumulation, no progressive degradation tied to uptime).
- Final severity/confidence: medium / medium for the core CPU-cost observation; the ASGI-thread-pool sub-claim specifically is rejected/low-confidence given the dependency evidence above.

---
Finding 6 — Non-atomic update_or_create race on first-write for a given (service_id, token_hash) pair

Standalone: CR-2 (no FA equivalent)
Static validity: confirmed
Performance relevance: none

- Affected resource: Service row identified by the unique_together constraint (models.py:9).
- Direct code evidence: views.py:59-63 calls Service.objects.update_or_create(...) with no surrounding transaction.atomic(), no select_for_update, and no try/except IntegrityError fallback anywhere in views.py.
- Triggering conditions: Two or more near-simultaneous heartbeat requests for a (service_id, token) pair with no existing row.
- Existing cleanup/lifecycle logic: None.
- Runtime-/workload-dependent assumption: Requires an actual concurrent-request scenario targeting a not-yet-existing pair; single-worker/serialized deployments (plausible per Finding 3's dependency analysis) would rarely trigger this.
- Mechanism class: no performance impact / correctness-only fault — a race condition producing an occasional uncaught IntegrityError (500 response) is a transient functional defect, not a resource-accumulation or throughput-degradation pattern.
- Aging relevance: unrelated to performance/aging — does not meet any of the four aging-mechanism criteria (no accumulation, no progressive exhaustion).
- Final severity/confidence: medium / medium — code evidence for the missing atomicity/locking is unambiguous; whether it manifests depends entirely on unverifiable concurrency assumptions.

---
SELF-SOURCED — UNVALIDATED (not cross-checked by an independent reviewer; not included above)

SS-1 — Admin-enabled Django default apps with no session/log cleanup task
- File/lines: code/mysite/urls.py:5 (path("admin/", admin.site.urls)), code/mysite/settings.py:14-22 (INSTALLED_APPS includes django.contrib.admin, django.contrib.auth, django.contrib.sessions), MIDDLEWARE at settings.py:24-32 includes SessionMiddleware.
- Observation: The admin site is routed and django.contrib.sessions/django.contrib.auth are installed with default (DB-backed) session engine and no SESSION_ENGINE override. Django's DB-backed sessions and django_admin_log (audit trail for admin actions) are not automatically purged — Django requires an explicit, periodically-run manage.py clearsessions command, which is not present anywhere in the reviewed tree (no cron/scheduled-task config found).
- Why unvalidated: Whether this path is ever exercised depends entirely on whether the admin UI is actually used in the target deployment — the two reviewed API endpoints (heartbeat, services) never touch request.session or auth, so this is orthogonal to the main API surface and requires confirmation of actual admin usage/deployment topology before it could be considered a supported aging mechanism.

---
2. Appendix — Raw Phase-1 Handoffs (verbatim, unedited)

Appendix A: CR-* findings (code-reviewer)

Code Review Findings: coding-tasks/python-Django/UptimeService

CR-1
- ID/File/Lines: CR-1, coding-tasks/python-Django/UptimeService/code/myapp/migrations/__init__.py (directory contents), cross-referenced with coding-tasks/python-Django/UptimeService/code/myapp/models.py:3-16
- Fault and evidence: The myapp migrations package contains only __init__.py — no migration file (e.g. 0001_initial.py) exists to create the Service model's table, despite models.py:3-16 defining Service with fields service_id, token_hash, last_notification, a unique_together constraint, and two models.Index entries.
- Execution path: Any request to heartbeat (views.py:59-63, calling Service.objects.update_or_create) or services (views.py:81, calling Service.objects.filter) triggers a query against the myapp_service table.
- Affected state/resource: SQLite database schema/table (db.sqlite3), ORM query execution.
- Triggering conditions: Occurs on the very first request after a fresh migrate run (or any environment where migrations weren't hand-generated and applied out-of-band), since Django will not auto-create tables without an applied migration.
- Existing cleanup/lifecycle logic: None — there is no migration to run, so manage.py migrate has nothing to apply for this model.
- Plausible runtime consequence: Every call to heartbeat and services raises django.db.utils.OperationalError: no such table: myapp_service, producing unhandled 500 errors for all requests.
- Severity/Confidence: high / high
- Assumptions needing validation: That no migration file exists elsewhere (e.g., squashed or generated at deploy time outside this directory) and that db.sqlite3 isn't pre-seeded with a manually created schema.

CR-2
- ID/File/Lines: CR-2, coding-tasks/python-Django/UptimeService/code/myapp/views.py:58-63
- Fault and evidence: Service.objects.update_or_create(service_id=service_id, token_hash=th, defaults={"last_notification": now}) is not wrapped in a transaction with row locking; Django's update_or_create performs a SELECT followed by an INSERT (or UPDATE), which is not atomic against concurrent callers targeting the same not-yet-existing (service_id, token_hash) pair.
- Execution path: heartbeat view invoked concurrently by two requests carrying the same new serviceId/token combination.
- Affected state/resource: Service row identified by the unique_together = ("service_id", "token_hash") constraint (models.py:9).
- Triggering conditions: Two or more near-simultaneous heartbeat requests for a service/token pair that does not yet have a row.
- Existing cleanup/lifecycle logic: No transaction.atomic() block, no try/except IntegrityError fallback, no select_for_update — insufficient to prevent the race Django's own documentation warns about for update_or_create.
- Plausible runtime consequence: One of the concurrent requests raises an uncaught django.db.utils.IntegrityError (unique constraint violation), surfacing as an unhandled 500 response instead of the intended upsert semantics.
- Severity/Confidence: med / med
- Assumptions needing validation: Requires confirming actual concurrent traffic patterns (multiple monitored services or retries hitting the same new serviceId/token pair within the same request window) and the deployment's WSGI worker/thread concurrency model.

CR-3
- ID/File/Lines: CR-3, coding-tasks/python-Django/UptimeService/code/myapp/views.py:81-88
- Fault and evidence: qs = Service.objects.filter(token_hash=th).order_by("service_id") followed by a full list comprehension over qs with no .only(), LIMIT, or pagination, then serialized via JsonResponse(payload, safe=False, ...).
- Execution path: services view, triggered on every POST to /services for a given token.
- Affected state/resource: Application heap memory during request handling; response payload size.
- Triggering conditions: A token accumulates a large number of distinct service_id rows over time (each heartbeat call with a new serviceId under the same token creates a new row per CR-2's upsert logic), with no cap on how many services a single token can register.
- Existing cleanup/lifecycle logic: None — no pagination, row limit, or streaming response; the entire result set is materialized and serialized in one pass.
- Plausible runtime consequence: As the number of services per token grows unbounded, per-request memory allocation and response latency grow linearly/unbounded, degrading throughput and potentially causing large memory spikes per request under sustained heartbeat volume.
- Severity/Confidence: low / med
- Assumptions needing validation: Requires knowing expected/maximum number of services registered per token in production to determine if this bound is ever practically reached.

CR-4
- ID/File/Lines: CR-4, coding-tasks/python-Django/UptimeService/code/myapp/views.py:26-31 (definition), invoked at views.py:55 and views.py:79
- Fault and evidence: _token_hash runs hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 120_000, dklen=32) — a deliberately slow, CPU-bound key-derivation with 120,000 iterations — executed synchronously inline in both the heartbeat and services request handlers.
- Execution path: Every single POST to /heartbeat and /services calls _token_hash synchronously before any DB access.
- Affected state/resource: WSGI/ASGI worker thread/process CPU time per request.
- Triggering conditions: Any request to either endpoint; consequence scales with request volume/frequency.
- Existing cleanup/lifecycle logic: None — no caching of the hash per token, no async offload, no rate limiting on the endpoint that is named/intended for frequent polling ("heartbeat").
- Plausible runtime consequence: Given heartbeat is designed to be called repeatedly/frequently by monitored services, the per-request CPU cost of 120k PBKDF2 iterations (tens of milliseconds typically) directly caps request throughput per worker and increases tail latency under concurrent load, more so than a typical lightweight upsert endpoint would require.
- Severity/Confidence: med / med
- Assumptions needing validation: Actual measured PBKDF2 cost on the deployment hardware, expected heartbeat call frequency/concurrency, and number of WSGI/ASGI workers available to absorb CPU-bound work.

CR-5
- ID/File/Lines: CR-5, coding-tasks/python-Django/UptimeService/code/mysite/settings.py:55-60
- Fault and evidence: DATABASES["default"] uses "ENGINE": "django.db.backends.sqlite3" with no OPTIONS (e.g., no timeout, no WAL-related pragmas) configured, while heartbeat performs a write (update_or_create, views.py:59-63) on every call.
- Execution path: Concurrent heartbeat requests arriving from multiple monitored services/workers simultaneously attempt writes to the same SQLite file.
- Affected state/resource: The single SQLite database file db.sqlite3, which serializes writers at the file-lock level.
- Triggering conditions: Multiple concurrent write requests (default sqlite3 busy-timeout is 5 seconds in Django); higher concurrency than the default timeout can absorb.
- Existing cleanup/lifecycle logic: None beyond Django's built-in default timeout — no WAL mode, no explicit busy-timeout tuning, no serialization/queueing of writes at the application layer.
- Plausible runtime consequence: Under sustained concurrent heartbeat traffic, writers can block each other and, if lock contention exceeds the default timeout, requests fail with django.db.utils.OperationalError: database is locked, surfacing as intermittent 500 errors and increased latency.
- Severity/Confidence: low / low
- Assumptions needing validation: Requires knowledge of actual concurrent write volume in the target deployment and whether multiple WSGI/ASGI worker processes (vs. a single-threaded dev server) are used, since concurrency level determines whether this becomes an observable issue.

CR-6
- ID/File/Lines: CR-6, coding-tasks/python-Django/UptimeService/code/myapp/models.py:8-13
- Fault and evidence: unique_together = ("service_id", "token_hash") (line 9) already forces a unique composite index on (service_id, token_hash), while indexes = [... models.Index(fields=["service_id", "token_hash"]) ...] (line 12) explicitly defines a second, functionally overlapping non-unique index on the identical column pair.
- Execution path: Every insert/update to Service (both via heartbeat's update_or_create and any migration-time index build) must maintain both the unique-constraint index and the redundant explicit index.
- Affected state/resource: SQLite index storage and write-path I/O for the Service table.
- Triggering conditions: Occurs on every write to the table, unconditionally.
- Existing cleanup/lifecycle logic: None — no consolidation of the redundant index; both are always maintained together.
- Plausible runtime consequence: Each write incurs extra index-maintenance overhead (duplicate B-tree updates) with no query benefit, since the unique constraint's index already serves lookups on (service_id, token_hash), modestly increasing write latency and storage overhead as row count grows.
- Severity/Confidence: low / med
- Assumptions needing validation: Requires confirming SQLite's actual index-creation behavior for unique_together in the (missing, per CR-1) migration output, and measuring write-path overhead at realistic row counts to confirm the impact is non-negligible.

Appendix B: FA-* findings (django-developer)

Code Review Findings — coding-tasks/python-Django/UptimeService

FA-1
- File / lines: code/myapp/migrations/__init__.py (only file present in the migrations package); code/myapp/models.py:3-16; code/myapp/views.py:59-63, 81-88
- Fault and evidence: The myapp/migrations/ directory contains only __init__.py — no numbered migration file exists for the Service model defined in models.py:3-16 (fields service_id, token_hash, last_notification, Meta.unique_together, Meta.indexes). Both request-handling views (views.py:59 Service.objects.update_or_create(...) and views.py:81 Service.objects.filter(token_hash=th)...) depend on this table existing.
- Execution path: Any POST to /heartbeat or /services reaches myapp.views.heartbeat/services, which immediately performs an ORM query against myapp_service.
- Affected state/resource: The myapp_service database table/schema (process-wide, persistent SQLite file db.sqlite3).
- Triggering conditions: python manage.py migrate is run against an environment where migrations were never generated (makemigrations never executed and committed) — as observed, the migrations package is empty of model migrations.
- Existing cleanup/lifecycle logic: None — there is no apps.py ready() hook, data migration, or migrate --run-syncdb fallback that would create the table. unique_together/indexes in Meta only take effect via a generated migration, which is absent, so nothing compensates.
- Plausible runtime consequence: django.db.utils.OperationalError: no such table: myapp_service on the first request to either endpoint, causing a 500 response for effectively all application functionality.
- Severity: high Confidence: high
- Assumptions needing validation: That no external/CI step (outside this reviewed tree) runs makemigrations before migrate at deploy time; if such a step exists elsewhere in the pipeline this finding would not manifest.

FA-2
- File / lines: code/mysite/settings.py:55-60; code/myapp/views.py:58-63
- Fault and evidence: DATABASES["default"]["ENGINE"] = "django.db.backends.sqlite3" (settings.py:57) with no timeout/OPTIONS override, combined with heartbeat performing a write via Service.objects.update_or_create(...) (views.py:59-63) on every call.
- Execution path: Concurrent invocations of POST /heartbeat (expected under an "uptime service" receiving periodic pings from many monitored services) each open a DB connection and attempt a write transaction against the single SQLite file.
- Affected state/resource: The shared db.sqlite3 file-level write lock; per-request DB connections (CONN_MAX_AGE not set, defaults to 0 — new connection per request).
- Triggering conditions: Two or more heartbeat requests overlapping in time (multi-threaded WSGI server, multiple gunicorn/uvicorn workers, or ASGI concurrency given ASGI_APPLICATION is configured).
- Existing cleanup/lifecycle logic: None — no OPTIONS={"timeout": ...}, no retry/backoff around the write, no select_for_update or serialized-writer pattern to absorb SQLite's single-writer restriction.
- Plausible runtime consequence: Intermittent django.db.utils.OperationalError: database is locked under concurrent heartbeat traffic, producing 500 responses for legitimate heartbeats.
- Severity: med Confidence: med
- Assumptions needing validation: Actual deployment concurrency (worker/thread count) and heartbeat frequency from monitored services; SQLite's default busy-timeout behavior under the specific WSGI/ASGI server used.

FA-3
- File / lines: code/myapp/views.py:26-31 (_token_hash), called at views.py:55 (heartbeat) and views.py:79 (services); code/mysite/asgi.py:1-5
- Fault and evidence: _token_hash runs hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 120_000, dklen=32) (views.py:30) synchronously inside every call to both heartbeat and services, which are plain def (sync) views, while ASGI_APPLICATION = "mysite.asgi.application" is configured (asgi.py:5), implying deployment under an ASGI server.
- Execution path: Every /heartbeat and /services request executes 120,000 PBKDF2 rounds before any DB access; under ASGI this sync view is dispatched via Django's internal sync_to_async thread-pool wrapping since there is no async def.
- Affected state/resource: Worker thread pool capacity (ASGI sync-view thread pool has a bounded size) / WSGI worker threads; per-request CPU time.
- Triggering conditions: Any sustained request rate to either endpoint; effect compounds when many monitored services heartbeat concurrently, since each request independently pays the full 120k-iteration cost with no caching of previously-seen tokens.
- Existing cleanup/lifecycle logic: None — no memoization/cache of token -> token_hash, no async offload (sync_to_async(..., thread_sensitive=False) or asyncio.to_thread), no rate limiting.
- Plausible runtime consequence: Elevated per-request latency and, under concurrent load, exhaustion of the sync-view thread pool (ASGI) or worker threads (WSGI), causing request queuing/timeouts for both endpoints simultaneously since they share the same hashing cost.
- Severity: med Confidence: med
- Assumptions needing validation: Actual deployed server (WSGI vs ASGI, thread pool size), request-rate/traffic profile, and whether token values are typically reused across many requests from the same client (which would make caching effective).

FA-4
- File / lines: code/myapp/views.py:81-88
- Fault and evidence: qs = Service.objects.filter(token_hash=th).order_by("service_id") followed by a full list comprehension [... for s in qs] (views.py:82-88) with no .count() cap, Paginator, or LIMIT/slicing applied.
- Execution path: POST /services with a valid token fetches and serializes every Service row sharing that token_hash in a single response.
- Affected state/resource: Per-request memory (Python list/JSON payload) and query execution time; DB read I/O proportional to matching row count.
- Triggering conditions: A token associated with a large number of service_id rows (e.g., many heartbeat calls with distinct serviceId under the same token) accumulating over time, since unique_together allows unbounded distinct service_id values per token_hash.
- Existing cleanup/lifecycle logic: None — no upper bound on result set size, no pagination parameters accepted from the request.
- Plausible runtime consequence: Increasing response size/latency and memory usage for /services as the number of distinct services registered under a token grows, with no architectural ceiling.
- Severity: low Confidence: med
- Assumptions needing validation: Expected/typical cardinality of service_id values per token in production usage; whether callers are expected to page through results.

FA-5
- File / lines: code/mysite/settings.py:10
- Fault and evidence: DEBUG = True is hardcoded with no environment-variable override (unlike APP_SECRET at line 7, which does read from os.environ).
- Execution path: Every DB cursor execution across heartbeat/services is wrapped by Django's CursorDebugWrapper (active only when DEBUG=True), which captures SQL text and timing into the per-connection queries_log.
- Affected state/resource: Per-connection query log (bounded deque, but still allocated/populated on every query) and additional CPU spent on wrapping/timing every cursor execution; also affects error-handling path (full debug tracebacks rendered on unhandled exceptions, e.g., the OperationalError from FA-1/FA-2).
- Triggering conditions: Any request that touches the database (i.e., every heartbeat/services call) while running with this settings file unmodified in a non-development environment.
- Existing cleanup/lifecycle logic: None — no DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True" pattern; the debug logging overhead is incurred unconditionally for the process lifetime.
- Plausible runtime consequence: Additional per-query CPU/memory overhead on every request compared to DEBUG=False, and verbose debug error pages (larger response payloads, environment/traceback exposure) generated whenever an unhandled exception occurs (e.g., triggered by FA-1).
- Severity: low Confidence: med
- Assumptions needing validation: Whether this settings file is actually used unmodified in the deployed/runtime environment or overridden externally.

FA-6
- File / lines: code/myapp/models.py:8-13
- Fault and evidence: Meta.unique_together = ("service_id", "token_hash") (line 9) already causes Django to create a composite unique index on (service_id, token_hash); Meta.indexes additionally declares models.Index(fields=["service_id", "token_hash"]) (line 12), the same column set/order as the unique constraint's implicit index.
- Execution path: Every INSERT/UPDATE on Service (i.e., every heartbeat call via update_or_create, views.py:59-63) must maintain both the unique-constraint index and the duplicate explicit index.
- Affected state/resource: SQLite table/index storage and write-path I/O for the myapp_service table.
- Triggering conditions: Any write to Service — occurs on essentially every successful heartbeat call.
- Existing cleanup/lifecycle logic: None — no deduplication of the redundant index; both would be created if/when migrations exist (see FA-1).
- Plausible runtime consequence: Unnecessary extra index-maintenance write amplification per heartbeat with no corresponding read-path benefit, since the unique-together index already services lookups on (service_id, token_hash) and the standalone token_hash index already services the services view's filter(token_hash=th) query.
- Severity: low Confidence: med
- Assumptions needing validation: Confirmation via EXPLAIN QUERY PLAN/migration SQL that Django/SQLite indeed materializes two physically distinct indexes here rather than deduplicating them, and the actual write volume in production to gauge materiality.

---