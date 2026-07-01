Performance-Engineer Final Consolidated Report (Phase 2)

Adjudication Report — UptimeService_fastAPI (calmmage-service-registry-api-main)

Verified directly against api/main.py, api/db.py, and api/models.py (full files read). All line references below match the current source. No code was executed; all conclusions are static-analysis hypotheses requiring runtime confirmation.

---
Finding 1 — Check-then-act race on known_services around an await, enabling duplicate services documents

- Merged: CR-1 + FA-1 (standalone content is essentially identical)
- Static validity: confirmed
- Code evidence (direct): main.py:43 if request.serviceId not in known_services: → builds Service → main.py:65 await db.services.insert_one(update_data) → only then main.py:66                          known_services.add(request.serviceId). No asyncio.Lock, no update_one(upsert=True), no cervice_key.
- Affected resource: known_services in-process Set[str] (main.py:25) and db.services MongoDB collection.                                                                                                 - Triggering condition: ≥2 concurrent POST /heartbeat requests for the same, previously- at the await insert_one point — a normal consequence of asyncio cooperative schedulingunder a single worker, no multi-process assumption required.                                                                                                                                             - Cleanup/bounding: none in code. Whether a DB-level unique index exists is unverifiablenot code fact).
- Performance relevance: conditional — primary impact is data-integrity/correctness (duplicate records, non-deterministic find_one resolution downstream in get_service/get_services_token); performance is indirect (extra documents modestly inflate db.services, compounding Finding 4's scan-
- Mechanism class: cumulative resource retention (bounded per race event) — not a continuously-growing leak by itself; growth is gated by the rate of new, racing service registrations, which is finite service churn is very high.
- Aging relevance: conditionally plausible aging mechanism — satisfies repeatable trigger, insufficient cleanup, and plausible (mild) accumulation, but the "progressive degradation during long-running execution" leg is weak unless the deployment continuously onboards many distinct new serworkload-dependent assumption).
- Final severity: high (as correctness bug) / medium (as performance contributor)
- Final confidence: high (code evidence is unambiguous)

---
Finding 2 — N+1 sequential per-service heartbeat lookup in get_services_token

- Merged: CR-2 + FA-3
- Static validity: confirmed
- Code evidence (direct): db.py:144 async for service_data in db.services.find({"token": token}): with, inside the loop, db.py:147-150 await db.heartbeats.find_one({"service_key": service.service_key},
sort=[("timestamp", -1)]) — one full sequential round trip per matched service, no limitokup aggregation.
- Affected resource: request latency of POST /services, MongoDB connection-pool occupancy.
- Triggering condition: any token matching more than a handful of services documents.
- Cleanup/bounding: none — no pagination or fan-out concurrency limiting.
- Performance relevance: direct — this is a request-time O(n) sequential-latency algorit collection age.
- Mechanism class: transient/repeated overhead (paid fresh on every call; not itself a leaking/growing resource).
- Aging relevance: non-aging performance fault by itself (fixed algorithmic cost per calent only insofar as the number of services registered under a shared token can grow overthe deployment's lifetime (external data-growth factor, not an internal resource leak) — cannot be classified as a supported aging mechanism on its own since it lacks intrinsic persistent accumulation.
- Final severity: medium
- Final confidence: high

---
Finding 3 — No shutdown handler for the module-level AsyncIOMotorClient

- Merged: CR-3 + FA-2
- Static validity: qualified
- Code evidence (direct): db.py:28 client = AsyncIOMotorClient(settings.mongodb_url) at egisters only @app.on_event("startup") (line 107); no @app.on_event("shutdown"), nolifespan context manager, no client.close() anywhere in the repo.
- Affected resource: Motor's TCP connection pool / background monitor tasks to MongoDB.
- Triggering condition (runtime/environment-dependent, not evidenced in code): requires the client object itself to be re-instantiated without process exit. Because client is created once at db.py module scope, standard Python import caching means re-instantiating FastAPI() or re-running uviterpreter (e.g., multiple TestClient(app) fixtures) reuses the already-imported client —it does not create a second client. A leak would require an actual importlib.reload(api.db) or a genuine new OS process without exit, which is a narrower scenario than "app re-creation/hot-reload" as framed
by both reviewers. Ordinary uvicorn --reload spawns a new subprocess on change (OS reclad one).
- Cleanup/bounding: none in code; reliance on OS-level teardown at process exit, which is normal and sufficient for a single long-lived process.
- Performance relevance: conditional — essentially none for a standard single-process prusible under module-reload test/dev harnesses not evidenced here.
- Mechanism class: cumulative resource retention — but the "repeatable trigger" required for an aging mechanism is not clearly present given Python's module-caching semantics; this weakens the finding
relative to both reviewers' framing.
- Aging relevance: conditionally plausible aging mechanism, narrowly scoped to dev/test module-reload patterns not confirmable from this code; not supported as a production long-running-process aging
mechanism.
- Final severity: low
- Final confidence: medium (code fact of missing shutdown hook is confirmed; the "reloadoth reviewers is only partially supported)

---
Finding 4 — Unbounded heartbeats growth with no verified indexes, degrading scan/query cost over time

- Merged: CR-4 + CR-5 + FA-5
- Static validity: confirmed (accumulation) / qualified (index-absence causing scans)
- Code evidence (direct):
  - Accumulation (confirmed): db.py:37-44 store_heartbeat() performs an unconditional inno delete/TTL/purge call exists anywhere in the repo (verified via repo-wide search — zero matches for delete/TTL logic). The only cutoff-time usage, db.py:71 in get_all_services_status, is a query filter, not a document purge, and that function itself is dead code (never called from any route —
confirmed via repo-wide grep for get_all_services_status).
  - Index absence (qualified): repo-wide search found zero occurrences of create_index/ensure_index. Hot-path queries filter on non-_id fields: main.py:70-72 (service_key), db.py:144 (token), db.py:147-150
(service_key + sort on timestamp). Whether indexes are provisioned out-of-band (ops scriable from this repository — this leg is an assumption, not a code fact.
- Affected resource: db.heartbeats collection storage and query-execution cost; secondarily db.services.
- Triggering condition: sustained normal operation — periodic heartbeats over the serviche designed, expected workload, not an edge case.
- Cleanup/bounding: none present in application code.
- Performance relevance: direct (storage growth is unconditional and workload-intrinsic)radation depends on unverifiable external indexing).
- Mechanism class: cumulative resource retention (confirmed) potentially compounding into finite-resource exhaustion (DB storage/scan cost) if indexes are absent.
- Aging relevance: static aging mechanism supported — this is the strongest candidate amtrigger (every heartbeat, by design, high frequency), persistent accumulation (everyinsert retained forever, confirmed by absence of any deletion code), insufficient cleanup (confirmed absent), and plausible degradation during long-running execution (growing collection size increases
scan/sort cost, particularly for the per-request find_one(..., sort=...) calls in Findinamed strictly as a static-analysis hypothesis pending runtime/DB-introspectionconfirmation of actual index presence and growth rate.
- Final severity: medium-high
- Final confidence: medium-high (accumulation leg: high confidence; index leg: medium, since indexing cannot be verified from code alone)

---
Finding 5 — Process-local known_services cache desynchronization across multiple workers

- Merged: CR-6 + FA-4
- Static validity: qualified
- Code evidence (direct): known_services: Set[str] = set() at main.py:25 is module-scopece at startup_event() (main.py:107-110 → load_known_services(), lines 28-32) and mutatedonly in-process (lines 66, 76) with no shared backing store (no Redis/cache reference anywhere in the repo).
- Environment/deployment assumption (not evidenced in this code): the finding's real-wororker processes. The repo's own entrypoint, main.py:113-120, calls uvicorn.run(app,host=settings.api_host, port=settings.api_port) with no workers argument, which defaults to a single process. Both reviewers acknowledge this; CR-6 rates confidence "low" for this reason, FA-4 rates it
"medium" — the code itself does not support a multi-worker premise, only external deployockerfile/process manager not present in this repo) could.
- Affected resource: cross-process consistency of known_services, db.services (duplicate-insert amplification of Finding 1's mechanism, but reliably reproducible per worker rather than only under a narrow
timing window, if multi-worker topology is used).
- Triggering condition: uvicorn --workers N or multiple replicas fronted by a load balancer — not demonstrated by any code in this repository.
- Cleanup/bounding: none.
- Performance relevance: conditional — entirely contingent on an externally-configured deployment topology not visible in the reviewed files.
- Mechanism class: no intrinsic accumulation within a single process; the "issue" is insnce, which is a consistency fault, not a leak.
- Aging relevance: non-aging performance fault (or unrelated to performance for the default single-process deployment shown in code) — lacks a code-confirmed repeatable trigger since the shown entrypoint is
single-process; cannot be classified as a supported aging mechanism from this codebase a
- Final severity: low (given code shows single-process default) to medium (if multi-worker deployment is confirmed externally)
- Final confidence: low-medium

---
Finding 6 — status field and monitoring_interval_seconds are defined but never used/updated (dead functionality)

- Standalone: FA-6 (no corresponding CR item)
- Static validity: confirmed
- Code evidence (direct): db.py:20 declares monitoring_interval_seconds on Settings; repo-wide grep confirms it is referenced nowhere else. models.py:22-28 defines ServiceStatus.{UNKNOWN,ALIVE,DOWN,DEAD};
the only assignment of status anywhere is main.py:52 (status=ServiceStatus.ALIVE at creaervices_status() computes a derived status object but is never invoked by any route,startup hook, or background task (confirmed via repo-wide grep — zero call sites).
- Affected resource: Service.status field persisted in MongoDB.
- Triggering condition: any service that stops heartbeating after registration — its stored status remains ALIVE forever, since nothing recomputes it.
- Cleanup/bounding: n/a (not a resource-leak issue).
- Performance relevance: none — this is a correctness/completeness gap (stale/incorrect data), not a performance defect. No resource accumulates, no query cost changes.
- Mechanism class: no performance impact.
- Aging relevance: unrelated to performance / not a supported aging mechanism (no resource accumulation, no exhaustion, no degrading execution path — the field is simply never recomputed).
- Final severity: low (functional correctness) / none (performance)
- Final confidence: medium (code fact of dead code is confirmed high-confidence; whether an out-of-repo monitoring worker exists is an unverifiable assumption)

---
SELF-SOURCED — UNVALIDATED (not cross-checked, not counted toward confirmed/qualified fi

S-1 — No deregistration/deletion path for either the in-memory known_services set or theing unbounded growth for any deployment with rotating/ephemeral service identifiers.
- Code evidence: repo-wide search for delete/remove/deregister endpoints or logic returned zero matches; known_services (main.py:25) is only ever grown (.add, line 66) or corrected (.discard, line 76 — an
error-recovery path, not a retention policy), never proactively pruned. models.py:19 docas "a periodic local job that can disappear sometimes," implying the data modelanticipates services with transient lifecycles, yet there is no code path anywhere to remove a stale/disappeared service from either the process memory set or the services/heartbeats collections.
- If workloads register many distinct, non-reused serviceId values over the deployment'se LOCAL_JOB semantics), both the in-process known_services set (process memory) anddb.services (DB storage, further compounding Finding 4) would grow without any code-provided bound, satisfying repeatable trigger, persistent accumulation, and insufficient cleanup — with plausible
degradation during long-running execution as an unconfirmed runtime hypothesis.

Only one high-confidence, sufficiently distinct self-sourced item was identified; no sec stretching beyond what direct code evidence supports.

---
Appendix A — Raw Phase-1 Handoff: code-reviewer (CR-1 .. CR-6)

CR-1

- ID: CR-1
- File: coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main
- Fault and evidence: Check-then-act race on the in-memory known_services cache spanning an await. Line 43: if request.serviceId not in known_services: is followed by an await                               db.services.insert_one(update_data) at line 65, and only after that await does line 66 euest.serviceId). Between the check and the .add(), the event loop can schedule anothercoroutine handling a concurrent /heartbeat request for the same serviceId.                                                                                                                                    - Execution path: POST /heartbeat handler heartbeat(), "new service" branch (lines 43-66
- Affected state/resource: known_services in-memory set (main.py:25) and the db.services MongoDB collection.                                                                                                  - Triggering conditions: Two or more concurrent /heartbeat requests arrive for the same, before the first request's insert_one completes and updates the cache.
- Existing cleanup/lifecycle/bounding logic: None. There is no lock, no find_one_and_update/upsert with a uniqueness constraint, and no unique index verified in code (see CR-4) to prevent duplicate         documents.
- Plausible runtime consequence: Duplicate service documents for the same service_key can be inserted into MongoDB; subsequent lookups such as db.services.find_one({"service_key": ...}) (main.py:70) or     get_services_token (db.py:144) can then behave inconsistently (e.g., matching an uninten or the "cache inconsistent" branch at lines 74-77 firing spuriously).
- Severity: high                                                                                                                                                                                              - Confidence: high
- Assumptions needing runtime validation: That the deployment issues concurrent requests for identical serviceId values within the same event loop/process (plausible for a heartbeat/monitoring service with retry logic), and that no unique index exists on service_key at the database level to re
                                                                                                                                                                                                              CR-2
                                                                                                                                                                                                              - ID: CR-2
- File: coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main/api/db.py, lines 139-164                                                                                             - Fault and evidence: N+1 query pattern with no pagination/bound. Line 144 async for ser({"token": token}): iterates the matching services, and for every single service the loopissues a separate database round trip at lines 147-150: latest_heartbeat = await db.heartbeats.find_one({"service_key": service.service_key}, sort=[("timestamp", -1)]).                                      - Execution path: POST /services -> get_services(request) (main.py:97-105) -> get_servicy:139-164).
- Affected state/resource: MongoDB connection/round-trip count; request latency of the /services endpoint.                                                                                                    - Triggering conditions: A token value that matches many service documents (e.g., shared service records per CR-1).
- Existing cleanup/lifecycle/bounding logic: None — no limit(), no batching, no aggregation $lookup to fetch heartbeats in a single query; every matched service incurs one extra full round trip.            - Plausible runtime consequence: Endpoint latency grows linearly (O(n) sequential round vices sharing a token, causing request time blow-up and increased load on the MongoDBconnection pool under concurrent callers.                                                                                                                                                                     - Severity: med
- Confidence: high                                                                                                                                                                                            - Assumptions needing runtime validation: That tokens are not always 1:1 with services iokens legitimately map to multiple services), which would need production data/profilingto confirm the magnitude of the N+1 fan-out.                                                                                                                                                                  
CR-3                                                                                                                                                                                                          
- ID: CR-3                                                                                                                                                                                                    - File: coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-mainask-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main/api/main.py,lines 107-110                                                                                                                                                                                                 - Fault and evidence: client = AsyncIOMotorClient(settings.mongodb_url) (db.py:28) is crime. main.py registers only a @app.on_event("startup") handler (lines 107-110) that callsload_known_services(); there is no corresponding @app.on_event("shutdown") (or lifespan) handler anywhere in the codebase that calls client.close().                                                          - Execution path: Application import/startup (db.py module load, main.py startup_event),/reload sequence.
- Affected state/resource: The Motor/PyMongo connection pool (sockets, background monitoring threads/tasks) owned by client.                                                                                  - Triggering conditions: Any process restart-without-exit path (e.g., uvicorn --reload, -import the app, or orchestrated graceful shutdown where the process is expected torelease resources before exiting) will re-create AsyncIOMotorClient instances without releasing the prior one's connections.                                                                                  - Existing cleanup/lifecycle/bounding logic: None found — no shutdown event, no context
- Plausible runtime consequence: Leaked open sockets/connection-pool threads accumulate across reload/re-import cycles, and graceful shutdown does not proactively release MongoDB server-side connection     slots, potentially exhausting the MongoDB server's maxIncomingConnections limit under reoscaling or hot-reload development environments).
- Severity: low                                                                                                                                                                                               - Confidence: med
- Assumptions needing runtime validation: Actual deployment/restart pattern (single long-lived process exiting cleanly on SIGTERM would let the OS reclaim sockets anyway; the risk materializes mainly under reload-heavy or multi-import scenarios) would need to be confirmed to assess real-world

CR-4

- ID: CR-4
- File: coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main/api/db.py, lines 37-44, 70-72 (via main.py), 144-150;
coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main/api/mai
- Fault and evidence: No index-creation code exists anywhere in the codebase (verified via repository-wide search for create_index/index, zero matches). Yet hot-path queries filter on non-_id fields:
main.py:70-72 db.services.find_one({"service_key": request.serviceId}) runs on every heaervice; db.py:144 db.services.find({"token": token}); db.py:147-150db.heartbeats.find_one({"service_key": ...}, sort=[("timestamp", -1)]).
- Execution path: Every POST /heartbeat for an existing service (main.py heartbeat()), a(get_services_token).
- Affected state/resource: MongoDB query execution cost against db.services and db.heartbeats collections.
- Triggering conditions: Collections grow beyond a small size (many services and/or hearndexes provisioned outside the visible code, each such query degrades to a full collection scan.
- Existing cleanup/lifecycle/bounding logic: None visible in application code; if indexelly (e.g., via a separate ops script not present in this repo), there is nothing here tobound scan cost.
- Plausible runtime consequence: As heartbeats (which is never pruned — see CR-5) and sency for /heartbeat and /services increases roughly linearly with collection size,degrading throughput under load.
- Severity: med
- Confidence: med
- Assumptions needing runtime validation: Whether indexes are created out-of-band (e.g.,nfra-as-code not included in this repository) — code alone cannot rule this out, soprofiling/DB introspection would be needed to confirm actual scan behavior.

CR-5

- ID: CR-5
- File: coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main68-71 for the only place that filters by age)
- Fault and evidence: store_heartbeat() (lines 37-44) unconditionally performs await db.heartbeats.insert_one(heartbeat) on every call with no corresponding deletion/expiry logic anywhere in the file. The
only place a time cutoff appears is get_all_services_status() line 71 cutoff_time = formtimedelta(days=7)), which is used purely to filter query results (lines 80, 89), not topurge old documents.
- Execution path: POST /heartbeat -> store_heartbeat() (main.py:83-86, db.py:37-44), inveat with no age-based cleanup ever executed.
- Affected state/resource: The db.heartbeats MongoDB collection's storage footprint and query cost over the lifetime of the deployment.
- Triggering conditions: Sustained normal operation — any service sending periodic heart
- Existing cleanup/lifecycle/bounding logic: None — no TTL index creation call, no scheduled deletion job, no capped collection configuration present in the code.
- Plausible runtime consequence: heartbeats collection grows unbounded indefinitely, incmbined with the missing-index concern in CR-4) increasing scan cost for every query thattouches db.heartbeats (store, per-service lookups, token-based lookups), degrading performance over the application's operational lifetime.
- Severity: med
- Confidence: med
- Assumptions needing runtime validation: Confirmation that no external TTL index or schside this repository, and profiling of collection growth rate over time to quantifydegradation.

CR-6

- ID: CR-6
- File: coding-task-open-source/UptimeService_fastAPI/calmmage-service-registry-api-main, 66, 76
- Fault and evidence: known_services: Set[str] = set() (line 25) is a module-level, process-local mutable cache used to gate the "new service" vs. "existing service" branch (line 43) and mutated at lines 66
and 76.
- Execution path: startup_event() populates it once per process (main.py:107-110, load_known_services()); every subsequent /heartbeat request reads/writes it in-process.
- Affected state/resource: known_services set — process-local shared state with no crossanism (no shared cache/lock backing it).
- Triggering conditions: The application is run with more than one worker/process (e.g., uvicorn --workers N or a process-manager fronting multiple instances) — each process independently loads and mutates
its own known_services.
- Existing cleanup/lifecycle/bounding logic: load_known_services() (lines 28-32) seeds the set from get_all_services() at startup, but nothing keeps multiple processes' copies of this set in sync after
startup; there is no shared store (e.g., Redis) referenced anywhere in the codebase.
- Plausible runtime consequence: In a multi-process deployment, a new serviceId's first heartbeat routed to worker A updates worker A's cache and DB, but worker B remains unaware and will treat the next
heartbeat for the same serviceId as "new" again, re-entering the creation branch (lines he same duplicate-insert risk described in CR-1 — but now reliably reproducible per worker rather than only under a narrow race window.
- Severity: med
- Confidence: low
- Assumptions needing runtime validation: Actual process/worker topology at deployment tde (the __main__ block at lines 113-120 calls uvicorn.run(app, ...) without a workersargument, implying single-process by default) — this finding's real-world impact depends entirely on external deployment configuration not present in the reviewed files.

---
Appendix B — Raw Phase-1 Handoff: fastapi-developer (FA-1 .. FA-6)

FA-1

- ID / File / Lines: FA-1 — api/main.py lines 43-66
- Fault and code evidence: Check-then-act race on the known_services cache combined with a MongoDB insert. Lines 43-56 check if request.serviceId not in known_services: and build a Service; line 65 await
db.services.insert_one(update_data) awaits the DB; only after that, line 66 does known_s). There is an await (the insert) between the membership check and the cache update, andno lock guards this critical section.
- Execution path: POST /heartbeat → heartbeat() handler, "new service" branch.
- Affected state/resource: process-global known_services: Set[str] (module scope, main.py line 25) and the services MongoDB collection.
- Triggering conditions: Two or more concurrent /heartbeat requests for the same new serrst request's insert_one completes and updates known_services (the event loop caninterleave coroutines at the await point).
- Existing cleanup/lifecycle/bounding logic: None — no asyncio.Lock, no unique index enfupsert semantics (insert_one is used, not update_one(..., upsert=True)).
- Plausible runtime consequence: Duplicate services documents for the same service_key with potentially different tokens/timestamps; downstream logic (get_service, get_services_token, get_all_services) that
does find_one/dict-keying by service_key will silently pick one of the duplicates non-densistent token validation and inconsistent responses across requests.
- Severity/Confidence: High / High
- Assumptions needing validation: That concurrent requests for the same brand-new servicion traffic patterns (bursty startup of monitored services registering simultaneously).

FA-2

- ID / File / Lines: FA-2 — api/db.py line 28; api/main.py lines 107-110
- Fault and code evidence: client = AsyncIOMotorClient(settings.mongodb_url) (db.py:28) is created at module import time, app-scoped. main.py only registers @app.on_event("startup") (line 107) calling
load_known_services(); there is no corresponding shutdown hook (@app.on_event("shutdown") that calls client.close().
- Execution path: Application import/startup and, more importantly, application shutdown/reload (ASGI server stop, test-fixture app re-creation, autoreload in dev).
- Affected state/resource: Motor's underlying TCP connection pool to MongoDB.
- Triggering conditions: Any process that starts and stops the ASGI app repeatedly without process exit (e.g., test suites instantiating app multiple times, hot-reload during development, or graceful
shutdown handling that expects orderly resource teardown).
- Existing cleanup/lifecycle/bounding logic: None present; reliance is entirely on OS-level socket teardown at process exit, which does not cover in-process app re-creation/reload scenarios.
- Plausible runtime consequence: Leaked open sockets/connection-pool threads accumulatineventually exhausting file descriptors or MongoDB's maxIncomingConnections under repeatedapp lifecycle churn.
- Severity/Confidence: Medium / Medium
- Assumptions needing validation: Deployment/test harness behavior — whether the app process is ever restarted in-process (uvicorn --reload, pytest fixtures) versus always being a fresh OS process per run.

FA-3

- ID / File / Lines: FA-3 — api/db.py lines 139-164 (get_services_token)
- Fault and code evidence: async for service_data in db.services.find({"token": token}):nd for every matched service the loop body performs an additional awaitdb.heartbeats.find_one(..., sort=[("timestamp", -1)]) (lines 147-150) — an N+1 sequential query pattern with no asyncio.gather/concurrent fan-out, and no limit() on either query.
- Execution path: POST /services → get_services() → get_services_token(request.token).
- Affected state/resource: db.services and db.heartbeats collections/cursors; per-request latency.
- Triggering conditions: Any token shared by, or matching, more than a handful of servicinearly (each round trip fully serialized, one after another).
- Existing cleanup/lifecycle/bounding logic: No pagination, no result-count bound, no batching/aggregation ($lookup) — nothing bounds the number of sequential round trips.                                  - Plausible runtime consequence: Request latency for /services scales linearly with the token; under load this ties up the event loop with many sequential awaits per request,increasing tail latency and reducing overall request throughput.                                                                                                                                             - Severity/Confidence: Medium / High
- Assumptions needing validation: Actual cardinality of services-per-token in production data; whether MongoDB round-trip latency is significant enough to matter at expected scale.                         
FA-4                                                                                                                                                                                                         
- ID / File / Lines: FA-4 — api/main.py lines 24-32 and 43-46                                                                                                                                                - Fault and code evidence: known_services: Set[str] = set() is declared at module scope via load_known_services() (lines 28-32) inside startup_event(). The /heartbeat "is thisnew?" decision (line 43) is driven purely by this in-process set, not by an authoritative DB check with an atomic upsert.                                                                                    - Execution path: Startup (startup_event) then every POST /heartbeat call.
- Affected state/resource: In-memory app-scoped cache known_services, and consequently the services collection.                                                                                              - Triggering conditions: Any deployment topology with more than one worker/process (e.g.le pod replicas) — each worker independently loads and maintains its own copy ofknown_services.                                                                                                                                                                                              - Existing cleanup/lifecycle/bounding logic: None — no shared/external cache (e.g., Redi is loaded once and only ever grown locally (line 66) or shrunk locally on theinconsistency branch (line 76), never re-synchronized across processes.                                                                                                                                      - Plausible runtime consequence: A service first seen by worker A is recorded in the DB ker B (never having seen it) will treat the same serviceId as new on its next heartbeatand attempt another insert_one, producing duplicate services documents purely as a function of load-balancer routing — compounding the FA-1 race at the architecture level even without tight timing.        - Severity/Confidence: Medium-High / Medium
- Assumptions needing validation: Whether the service is actually deployed with multiple worker processes/replicas; single-process deployments would not exhibit this.                                       
FA-5                                                                                                                                                                                                         
- ID / File / Lines: FA-5 — api/db.py lines 37-44 and 144-150 (no index-creation code anywhere in the reviewed package)                                                                                      - Fault and code evidence: store_heartbeat() (lines 37-44) performs an unconditional awaeartbeat) on every /heartbeat call with no retention/TTL logic, and get_services_token(lines 147-150) performs db.heartbeats.find_one({"service_key": ...}, sort=[("timestamp", -1)]) per service on every /services call. No create_index/ensure_index call exists anywhere in db.py or main.py.  - Execution path: Every POST /heartbeat (insert) and every POST /services (sorted lookup
- Affected state/resource: heartbeats collection size/growth and the query planner's ability to service sorted lookups efficiently.                                                                          - Triggering conditions: Sustained production traffic over time — the heartbeats collectTL/archival job present in code) while sorted per-service lookups are executed on every/services invocation.                                                                                                                                                                                        - Existing cleanup/lifecycle/bounding logic: None visible in code — retention, archival,irely absent from the reviewed source, so query performance depends solely on whateverindexes (if any) exist out-of-band in the database.                                                                                                                                                          - Plausible runtime consequence: As the heartbeats collection grows, find_one(..., sort=nd the 7-day range scan in the unused get_all_services_status) risk degrading fromindex-backed lookups to full/partial collection scans, increasing p95/p99 latency for /services and /heartbeat over the service's operational lifetime.                                                      - Severity/Confidence: Medium / Medium
- Assumptions needing validation: Whether indexes on heartbeats.service_key/heartbeats.timestamp are provisioned out-of-band (e.g., via a separate ops script or MongoDB Atlas config) not present in this   repository; actual collection growth rate in production.
                                                                                                                                                                                                             FA-6
                                                                                                                                                                                                             - ID / File / Lines: FA-6 — api/db.py line 20; api/models.py lines 22-28; api/main.py li
- Fault and code evidence: Settings.monitoring_interval_seconds is declared (db.py:20, "How often to check service status") but is never read anywhere in the codebase (confirmed no other reference).       ServiceStatus defines DOWN/DEAD/UNKNOWN states (models.py lines 25-28), but the only pla at service-creation time in main.py:52 (status=ServiceStatus.ALIVE), and no scheduledtask, background loop, or endpoint in main.py ever recomputes or persists a status transition (get_all_services_status in db.py computes a derived status-like response object but is never invoked from any route or startup/background task, per repo-wide grep).
- Execution path: Entire application lifetime — no code path ever re-evaluates a registered service's liveness after initial creation.
- Affected state/resource: Service.status field persisted in MongoDB and returned to any
- Triggering conditions: Any service that stops sending heartbeats after registration — its stored status remains ALIVE indefinitely because nothing ever updates it.
- Existing cleanup/lifecycle/bounding logic: None — no asyncio periodic task, no Backgroed coroutine references monitoring_interval_seconds or writes back a computed status.
- Plausible runtime consequence: The status field is permanently stale/incorrect for any service after its first heartbeat, silently misrepresenting liveness to any downstream consumer of the services
collection or of Service.status, despite the data model and settings implying active mon
- Severity/Confidence: Low-Medium / Medium
- Assumptions needing validation: Whether a separate, out-of-repo monitoring worker consor writes back status/monitoring_interval_seconds — not verifiable from this codebasealone.

---