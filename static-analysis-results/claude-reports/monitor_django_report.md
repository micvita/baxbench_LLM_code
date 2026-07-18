# Performance-Engineer Final Consolidated Report (Phase 2)

## Adjudication Report — coding-tasks/python-Django/Monitor

Both handoffs were verified directly against myapp/views.py, mysite/settings.py, mysite/asgi.py, mysite/wsgi.py, myapp/urls.py, and myapp/tests.py. All line citations in both CR-\* and FA-\* checked out against the current source (minor ±1 line offsets in a couple of CR citations are immaterial). No code was executed; all conclusions below are static-analysis hypotheses only and require runtime/load confirmation before being treated as demonstrated aging behavior.

---

## Finding 1 — Unbounded subprocess.run for ps (no timeout=)

- Merged from: CR-1, FA-1 (same code, same mechanism)
- Static validity: confirmed. myapp/views.py:38-45, inside \_run\_ps, calls subprocess.run(args, capture\_output=True, text=True, check=False) with no timeout kwarg, reachable synchronously from monitor\_commands (POST /monitor/commands, line 129). except FileNotFoundError / except Exception (lines 46-49) only fire on launch failure, not on a hang — verified, these handlers wrap the call but cannot interrupt a blocked subprocess.run.
- Performance relevance: direct (once triggered) / conditional (trigger requires ps to actually stall).
- Affected resource: request-handling worker thread/process (WSGI sync worker, or the ASGI thread-sensitive executor thread — see Finding 5); OS process-table slot for the child ps.
- Triggering conditions: any input/environment causing ps (invoked with attacker-influenced filter\_flags, line 33) to fail to terminate. This is not demonstrated by any code path that forces a hang — it is a documented absence of a safety bound, not evidence that ps does hang.
- Existing cleanup/bounds: none. No timeout=, no watchdog, no Popen+communicate(timeout=...) fallback.
- Direct evidence vs. assumption: the missing timeout= is direct code evidence. That a hang is achievable, and that the deployment uses a bounded worker/thread pool, are runtime/environment assumptions (correctly flagged as such by both reviewers).
- Mechanism class: finite-resource exhaustion (each unbounded hang permanently removes one worker from the pool until process restart).
- Aging relevance: conditionally plausible aging mechanism — satisfies repeatable trigger, progressive exhaustion of a bounded worker pool, insufficient bounding, and plausible degradation over sustained/repeated triggering; the missing element is confirmation that the trigger (an actual ps hang) is reachable in the deployed environment.
- Final severity: high (impact, if triggered, is a full-service stall). Final confidence: medium (CR: med, FA: high — averaged down given the unconfirmed hang trigger).

## Finding 2 — Unbounded, user-controlled regex evaluated per output line (ReDoS)

- Merged from: CR-2, FA-2
- Static validity: confirmed. command\_regex (fully client-controlled, views.py:116) is compiled once (re.compile, line 125) with no length/complexity guard, then pattern.search(line) is executed per line inside the loop at lines 141-144 over every \_parse\_ps\_output data line.
- Performance relevance: direct (once triggered) / conditional (requires a genuinely pathological pattern matching real ps line content).
- Affected resource: CPU time of the same request-handling worker (GIL-bound), effectively equivalent in impact to Finding 1 — a sufficiently pathological pattern makes pattern.search block for a very long/unbounded time, tying up a worker in the same way an unresponsive ps would.
- Triggering conditions: a command\_regex with nested-quantifier/catastrophic-backtracking structure that also partially matches enough of a ps line to reach exponential-time paths. Not demonstrated with a concrete PoC pattern by either reviewer — flagged as an assumption in both handoffs.
- Existing cleanup/bounds: none — except re.error only rejects syntactically invalid patterns, not pathological-but-valid ones.
- Direct evidence vs. assumption: the absent length/complexity/timeout guard is direct code evidence. Whether a concrete pathological pattern reachable through this endpoint actually causes measurable backtracking blowup against real ps output is a runtime/data-dependent assumption.
- Mechanism class: finite-resource exhaustion (worker starvation via CPU-bound blocking), same class as Finding 1 rather than "transient overhead," since a sufficiently adversarial pattern can render the block effectively unbounded/non-terminating in practice.
- Aging relevance: conditionally plausible aging mechanism — repeatable trigger, progressive worker-pool exhaustion under repeated adversarial requests, no bounding, plausible degradation over sustained triggering; contingent on a concrete pathological-pattern PoC not yet demonstrated.
- Final severity: high. Final confidence: medium (both reviewers independently converged at med, consistent).

## Finding 3 — Unbounded subprocess-output buffering and unpaginated JSON response

- Merged from: CR-3, FA-3
- Static validity: confirmed. capture\_output=True (lines 39-44) buffers all of ps stdout with no size cap; results (line 137) accumulates one dict per matching line with no cap; the full list is serialized in one JsonResponse(results, safe=False, status=200) (line 156) with no pagination/streaming. CR-3's citation of tests.py:25 ("command\_regex": ".\*") was verified — matches broadly.
- Performance relevance: conditional — depends on actual process-table size / filter\_flags verbosity on the deployment host, which neither reviewer nor this verification can determine statically.
- Affected resource: heap memory for the captured stdout string, the intermediate line list, the results list, and the JSON serialization buffer; outgoing response payload size.
- Triggering conditions: broad command\_regex (e.g. .\*) combined with filter\_flags producing a large/verbose process listing.
- Existing cleanup/bounds: none — no max\_results, no output-size cap, no truncation.
- Direct evidence vs. assumption: absence of any cap is direct code evidence; the magnitude of real-world ps output (and thus whether this is practically significant) is environment-dependent.
- Mechanism class: transient/repeated overhead — memory is allocated per-request and is not held or referenced beyond the request/response cycle (no cache, no persistent handle observed); nothing in the code causes this allocation to accumulate across requests or grow with server uptime independent of concurrent load.
- Aging relevance: non-aging performance fault — satisfies "repeatable trigger" and "insufficient bounding" but not "persistent accumulation/progressive exhaustion" (each request's buffer is transient, sized by system state at that moment, not by request history). Relevant to per-request/concurrent-load performance, not to progressive degradation over long-running execution.
- Final severity: medium. Final confidence: medium (consistent between reviewers).

## Finding 4 — PID column detection heuristic can silently return wrong processId values (and can drop the first data row)

- Merged from: CR-4, FA-4 (FA-4 additionally raises the "first row unconditionally treated as header" sub-issue at \_parse\_ps\_output line 62, which was verified as accurate — header = lines[0] is unconditional with no verification it is actually a header)
- Static validity: confirmed. \_parse\_ps\_output (lines 62-72) requires a literal "PID" header token; if absent, pid\_idx is None and \_extract\_pid\_from\_line's fallback (lines 90-101) returns the first digit-token in range 1–10,000,000, which is not verified to correspond to an actual PID (could be %CPU, VSZ, RSS, TTY, etc., depending on ps output shape).
- Performance relevance: none — this is a data-correctness/data-integrity defect (wrong or missing processId values returned to the client), not a resource-consumption or latency issue.
- Affected resource: none applicable (correctness only).
- Triggering conditions: non-English/non-C locale inherited by the Django process, or filter\_flags producing headerless/nonstandard ps output.
- Existing cleanup/bounds: not applicable to performance; the code's own comment (lines 70-71) acknowledges this is a best-effort heuristic, not a guarantee.
- Direct evidence vs. assumption: the heuristic logic itself is direct code evidence; whether it is actually exercised depends on the deployed locale/ps variant (environment-dependent, unverifiable statically).
- Mechanism class: no performance impact (unsupported for aging purposes).
- Aging relevance: unrelated to performance.
- Final severity: medium (functional correctness). Final confidence: medium (CR: low, FA: med — code evidence for the mechanism is solid, even though real-world trigger frequency is unverified).

## Finding 5 — Synchronous view under ASGI serialized via a single thread-sensitive executor

- Standalone: FA-5 (no CR counterpart)
- Static validity: qualified. Verified directly: settings.py:49 sets ASGI\_APPLICATION = "mysite.asgi.application", mysite/asgi.py builds application = get\_asgi\_application() (both files confirmed as cited), and monitor\_commands (views.py:105) is declared as a plain def, not async def, and performs a blocking subprocess.run inside it. The claim about Django's ASGIHandler routing sync views through asgiref.sync.SyncToAsync with thread\_sensitive=True (serializing them onto one executor thread) is accurate, well-documented Django/asgiref framework behavior, but is not verifiable from this codebase alone — it is a framework-behavior assumption, not code directly present in this repo.
- Performance relevance: conditional — entirely contingent on the deployment actually running mysite.asgi rather than mysite.wsgi (the project ships both entry points; nothing in the repo indicates which is used in production).
- Affected resource: the single shared thread-sensitive executor thread per ASGI process/event loop, if ASGI is in fact the serving path.
- Triggering conditions: ASGI deployment + concurrent requests to /monitor/commands; compounds Finding 1's impact if true (a single hang would serialize/block all thread-sensitive sync work, not just this endpoint's own worker).
- Existing cleanup/bounds: none — view is not async def, no thread\_sensitive=False override.
- Direct evidence vs. assumption: the code shape (sync view + ASGI entry point coexisting) is direct evidence; the actual routing behavior at runtime (which server, which asgiref version/defaults) is a framework/deployment assumption.
- Mechanism class: finite-resource exhaustion / concurrency bottleneck — a structural single-thread serialization point, not something that grows with uptime.
- Aging relevance: non-aging performance fault — this is a static concurrency ceiling present from process start, not a progressive/cumulative degradation; it amplifies the impact of Finding 1 rather than constituting its own aging mechanism.
- Final severity: medium (contingent on ASGI deployment, per FA-5's own caveat). Final confidence: medium.

## Finding 6 — DEBUG = True hardcoded (non-environment-conditional)

- Standalone: CR-6
- Static validity: confirmed. settings.py:7 — DEBUG = True with no environment-variable derivation; loaded via DJANGO\_SETTINGS\_MODULE=mysite.settings in both asgi.py:4 and wsgi.py:4 (verified).
- Performance relevance: conditional — only manifests on unhandled-exception paths.
- Affected resource: per-error-response CPU/payload size (Django's debug traceback renderer does more work and produces larger responses than the production error handler).
- Triggering conditions: any unhandled exception outside the explicitly guarded blocks in monitor\_commands/\_run\_ps (e.g., an exception in \_parse\_ps\_output/\_extract\_pid\_from\_line/JSON serialization, none of which are wrapped in try/except).
- Existing cleanup/bounds: none.
- Direct evidence vs. assumption: the hardcoded value is direct code evidence; whether this settings module is what's actually deployed to production (vs. overridden elsewhere) is an environment/deployment assumption, correctly flagged by CR-6.
- Mechanism class: transient/repeated overhead (each unhandled exception costs more, but nothing accumulates between exceptions).
- Aging relevance: non-aging performance fault — lacks the persistent-accumulation element; it's a per-incident overhead multiplier, not a progressive-degradation mechanism.
- Final severity: low. Final confidence: medium.

---

## Excluded from the 6-item list

- CR-5 (ps non-zero exit code → hard 500, discarding potentially-partial stdout): verified accurate (views.py:129-133), but this is a purely functional/availability-correctness issue with no resource-consumption or accumulation mechanism (mechanism class: no performance impact; aging relevance: unrelated to performance). Given the 6-item cap, it was deprioritized below the performance-relevant findings above; static validity: confirmed, severity/confidence as originally rated by CR (low/low) with no adjustment needed.

---

## SELF-SOURCED — UNVALIDATED (max 2, not cross-checked, not part of consolidated findings)

1. Opaque catch-all masking OS-level resource-exhaustion symptoms. \_run\_ps's except Exception as e: return 1, "", f"Failed to execute ps: {e}" (views.py:48-49) converts any failure to launch the child process — including OS-level fork/exec failures such as EAGAIN/ENOMEM/EMFILE/ENFILE that would arise if the host process is nearing file-descriptor or process-table exhaustion from an unrelated long-running leak — into an indistinguishable generic "Failed to execute ps" 500-equivalent message, with no logging or metric differentiating this from a benign missing-binary case. This wouldn't itself be a leak, but it could delay detection of an actual aging condition elsewhere in the host process (no error-code introspection, no structured logging of errno). Unvalidated: whether such OS-level failures are ever actually reachable in the deployed environment is unknown.
2. Background filesystem-polling overhead from Django autoreload under DEBUG=True. If this service is served via manage.py runserver (a plausible interpretation given DEBUG=True is hardcoded per Finding 6, rather than a WSGI/ASGI production server), Django's django.utils.autoreload StatReloader runs a background thread that continuously stat()s all imported module files for the lifetime of the process, independent of request volume. This is a long-running background overhead whose cost scales with the number of imported modules/filesystem latency, but it is entirely contingent on runserver (rather than gunicorn/uvicorn) being the actual serving mechanism — not verifiable from this repo alone.

---

---

## Appendix A — Raw Phase-1 Handoff: CR-\* (code-reviewer)

### CR-1

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 38-45
- fault and direct code evidence: proc = subprocess.run(args, capture\_output=True, text=True, check=False,) — no timeout= argument is passed to subprocess.run.
- relevant execution path: every POST to /monitor/commands invokes monitor\_commands() (views.py:104-156) → \_run\_ps(filter\_flags) (views.py:129) → subprocess.run(args, ...).
- affected state or resource: the request-handling thread/worker (WSGI worker thread, or the thread-sensitive executor thread used by Django's ASGI handler for sync views) and the child ps process.
- triggering conditions: any condition that causes ps to not terminate promptly — e.g. a hung/zombie /proc filesystem, an unusual filter\_flags value shlex-split into arguments that make ps block, or general system pathology. Because filter\_flags is attacker/client controlled (line 117, 121-122) and passed as literal argv tokens to ps, the caller has direct influence over which ps invocation is executed.
- existing cleanup, lifecycle, or bounding logic: none — there is no timeout, no watchdog, and no fallback path if ps never returns; the except FileNotFoundError/except Exception handlers only cover failure to launch, not hangs. This is not sufficient because a hang inside subprocess.run is never interrupted.
- plausible runtime consequence: the handling thread blocks indefinitely, holding it unavailable for other requests; under concurrent load this can exhaust the worker/thread pool serving the view, causing request queuing/timeouts across the service, not just for the current request.
- severity: high; confidence: med
- assumptions needing runtime/profiling validation: that the deployment's request-handling model has a bounded thread/worker pool (true for typical WSGI/ASGI deployments) and that a ps invocation can actually stall in the target environment.

### CR-2

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 116-125, 141-144
- fault and direct code evidence: command\_regex = payload.get("command\_regex") ... pattern = re.compile(command\_regex) (line 125) with no complexity/length restriction, then if not pattern.search(line): continue executed inside for line in data\_lines: (lines 141-143) — i.e., the fully user-controlled, uncompiled-for-safety regex is evaluated once per output line of ps.
- relevant execution path: same POST /monitor/commands path; regex compiled once, then pattern.search called once per line returned by \_parse\_ps\_output (potentially dozens/hundreds of lines depending on system process count).
- affected state or resource: CPU time of the request-handling thread; Python's re engine has no execution timeout.
- triggering conditions: client supplies a regex pattern susceptible to catastrophic backtracking (e.g. nested quantifiers) against process-line strings that partially match but fail late, multiplied across every ps output line.
- existing cleanup, lifecycle, or bounding logic: none — no timeout, no length cap on command\_regex, no per-line time budget. Not sufficient to bound worst-case CPU consumption.
- plausible runtime consequence: CPU-bound hang of the handling thread/process for an extended period (potentially unbounded), amplified by the loop over all ps lines, degrading throughput/availability for concurrent requests.
- severity: high; confidence: med
- assumptions needing runtime/profiling validation: that a regex triggering catastrophic backtracking can be constructed that also matches enough of typical ps line content to reach the exponential-time code paths repeatedly.

### CR-3

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 39-44, 137-156
- fault and direct code evidence: subprocess.run(args, capture\_output=True, text=True, check=False,) buffers the entirety of ps stdout into memory with no size cap; results: List[Dict[str, object]] = [] (line 137) accumulates one dict per matching line with no upper bound, and is returned wholesale via return JsonResponse(results, safe=False, status=200) (line 156) with no pagination/limit.
- relevant execution path: POST /monitor/commands → \_run\_ps returns full stdout string → \_parse\_ps\_output splits into all lines → loop over all data\_lines appends every regex-matching line into results, then the entire list is serialized in one JSON response.
- affected state or resource: process memory (for buffered subprocess output, per-line dicts, and outgoing JSON serialization buffer) and response payload size.
- triggering conditions: a command\_regex that matches broadly (e.g. ".\*", used in tests.py line 25) combined with a system/filter\_flags value that produces a very large process table or verbose per-thread listing (e.g. -T/-L-style flags passed through filter\_flags).
- existing cleanup, lifecycle, or bounding logic: none — no max\_results, no output-size cap, no streaming response; not sufficient to bound memory/response size growth relative to process count or flags supplied.
- plausible runtime consequence: elevated memory usage and large response payloads proportional to (uncapped) ps output size, degrading latency/throughput particularly if invoked repeatedly/concurrently.
- severity: med; confidence: med
- assumptions needing runtime/profiling validation: actual process counts and ps flag behavior on the target OS/container that would determine whether output size is realistically large enough to matter.

### CR-4

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 62-68, 75-101
- fault and direct code evidence: PID-column detection relies purely on textual header matching — for i, c in enumerate(cols): if c.upper() == "PID": pid\_idx = i; break (lines 65-68) — over ps output produced with whatever locale/environment the parent Django process happens to have (no LC\_ALL/LANG override passed to subprocess.run at lines 39-44). When no "PID" header token is found, \_extract\_pid\_from\_line falls back to for tok in parts: if tok.isdigit(): ... if 1 <= val <= 10\_000\_000: return val (lines 92-97), returning the first integer-looking token in a plausible range — which is not necessarily the actual PID (e.g. it could be a %CPU/%MEM value formatted without a decimal, an elapsed time value, or a TTY-like numeric token depending on filter\_flags/OS ps variant).
- relevant execution path: reached whenever ps's header does not contain a literal "PID" token recognizable by the exact-match check (e.g., non-English LANG/LC\_ALL in the process environment, or a filter\_flags value producing headerless/nonstandard output), causing every line to go through the heuristic fallback in \_extract\_pid\_from\_line.
- affected state or resource: correctness of the processId field returned to the caller for every result row.
- triggering conditions: environment locale not set to a C/English locale, or ps flag combinations (via user-supplied filter\_flags, line 117/121-122) that alter column layout/header naming.
- existing cleanup, lifecycle, or bounding logic: the code documents the fallback as a "heuristic" (comment, lines 90-91) but performs no validation that the extracted value actually corresponds to a PID (e.g., no cross-check against /proc or a second column); this is insufficient to guarantee correctness, only plausibility.
- plausible runtime consequence: silently wrong processId values returned to callers, which is a functional correctness defect rather than a crash (data integrity issue in the API response).
- severity: med; confidence: low
- assumptions needing runtime/profiling validation: the actual locale/environment variables inherited by the Django process at deploy time, and the specific ps implementation/flags in use, both of which determine whether the fallback path is ever actually exercised.

### CR-5

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 129-133
- fault and direct code evidence: rc, out, err = \_run\_ps(filter\_flags) then if rc != 0: msg = err.strip() or f"ps exited with code {rc}"; return \_error(500, msg) — on any non-zero ps return code (including partial/transient failures reported by some ps implementations, or a malformed-but-non-crashing filter\_flags combination) the entire request fails with a 500 and stdout already captured in out is discarded, even if out contains partially valid, parseable data.
- relevant execution path: any POST to /monitor/commands where ps exits non-zero — including cases where filter\_flags shlex-parses successfully but produces flags that ps rejects with a non-zero status while still emitting partial listing output to stdout.
- affected state or resource: response correctness/availability — a case that could otherwise be served (or partially served) is turned into a hard failure.
- triggering conditions: filter\_flags values accepted by shlex.split (line 30) but rejected/partially handled by the underlying ps binary.
- existing cleanup, lifecycle, or bounding logic: none — there's no distinction between "no output at all" and "output produced despite non-zero exit"; not sufficient to avoid discarding potentially usable data or to give the caller actionable per-flag diagnostics beyond raw stderr text.
- plausible runtime consequence: functional/availability regression — legitimate partial results are dropped and replaced with a generic 500, and repeated client retries against the same (deterministically failing) filter\_flags value would consistently fail the endpoint.
- severity: low; confidence: low
- assumptions needing runtime/profiling validation: which ps implementation/flags are in scope in the deployed environment and whether any actually exhibit non-zero exit with usable partial stdout.

### CR-6

- file: coding-tasks/python-Django/Monitor/code/mysite/settings.py, line 7
- fault and direct code evidence: DEBUG = True is hardcoded (not environment-conditional) in the settings module used by both wsgi.py/asgi.py application entry points.
- relevant execution path: loaded at process startup for every deployment of this settings module (DJANGO\_SETTINGS\_MODULE=mysite.settings, set in asgi.py line 4 and wsgi.py line 4), affecting all requests including /monitor/commands.
- affected state or resource: process-wide Django runtime behavior — with DEBUG=True, unhandled exceptions render full debug tracebacks (larger response payloads and additional server-side work per error), and Django's autoreload/debug machinery carries extra bookkeeping overhead versus production mode.
- triggering conditions: any request path that raises an unhandled exception (note monitor\_commands has broad try/except around JSON parsing and regex compilation, but not around the subprocess/parsing logic beyond the specific FileNotFoundError/Exception catches inside \_run\_ps); an exception outside those guarded blocks (e.g., in \_parse\_ps\_output/\_extract\_pid\_from\_line/JSON serialization) would propagate to Django's debug error renderer.
- existing cleanup, lifecycle, or bounding logic: none present to force DEBUG=False in any deployment profile; not sufficient because the value is static in source rather than environment-derived.
- plausible runtime consequence: heavier per-error-response processing/payload size under fault conditions, amplifying load during incident scenarios (a performance-relevant consequence of an otherwise config-only issue).
- severity: low; confidence: med
- assumptions needing runtime/profiling validation: whether this settings file is actually what's deployed in non-development environments, and the frequency of unhandled exceptions in production traffic.

---

## Appendix B — Raw Phase-1 Handoff: FA-\* (django-developer)

### FA-1

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 38-49 (\_run\_ps)
- fault and direct code evidence: proc = subprocess.run(args, capture\_output=True, text=True, check=False,) is invoked with no timeout argument. If the spawned ps process (or a substituted binary/flags supplied via filter\_flags, e.g. args.extend(extra) at line 33) blocks or never terminates, subprocess.run blocks indefinitely with no bound.
- relevant execution path: monitor\_commands (POST /monitor/commands) → \_run\_ps(filter\_flags) (line 129) → subprocess.run (lines 39-44), executed synchronously inside the request/response cycle.
- affected state or resource: the WSGI worker process/thread (or, under ASGI, the sync-executor thread handling the view — see FA-5) is occupied for the entire subprocess duration; OS process table entry for the child ps.
- triggering conditions: any request to this endpoint where the underlying ps invocation hangs (e.g., system under I/O/proc-fs contention, or filter\_flags selecting a ps mode/target that stalls); no client-side or server-side timeout exists to interrupt it.
- existing cleanup, lifecycle, or bounding logic: only except FileNotFoundError and generic except Exception around the call (lines 46-49), which handle failure-to-start or attribute errors but do not fire while the child process is merely blocked mid-execution; no timeout= parameter, no Popen+communicate(timeout=...), no external watchdog — insufficient to bound worst-case latency.
- plausible runtime consequence: a stuck worker/thread indefinitely; under limited worker-count deployments (e.g., default runserver/gunicorn sync workers) repeated hangs exhaust the available worker pool, causing the whole service to stop responding to new requests.
- severity: high; confidence: high
- assumptions needing runtime validation: that the deployment uses a bounded worker/thread pool (true for standard WSGI/ASGI servers) and that ps (or the flags-selected binary behavior) can actually stall on the target OS/environment.

### FA-2

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 124-127 and 141-144
- fault and direct code evidence: pattern = re.compile(command\_regex) compiles a fully user-supplied regex (from request JSON, line 116/124) with no length limit, complexity check, or execution timeout, then it is applied per line inside a loop: for line in data\_lines: if not pattern.search(line): continue (lines 141-144).
- relevant execution path: monitor\_commands → user JSON body command\_regex → re.compile → loop calling pattern.search(line) once per parsed ps output line (potentially many lines from ps aux).
- affected state or resource: the process's CPU / GIL — regex matching in Python's re engine is synchronous and can exhibit catastrophic backtracking for adversarial patterns (e.g. nested quantifiers) against adversarial or even ordinary long lines.
- triggering conditions: a command\_regex value crafted to trigger catastrophic backtracking (classic ReDoS patterns) combined with process lines long/varied enough to trigger exponential backtracking during .search().
- existing cleanup, lifecycle, or bounding logic: none — no regex complexity screening, no per-match timeout, no signal/thread-based interrupt; the only validation is that re.compile succeeds (except re.error), which does not detect catastrophic-backtracking patterns since those compile fine.
- plausible runtime consequence: CPU pegged at 100% on the worker thread/process for the duration of backtracking (potentially effectively unbounded), holding the GIL and starving other requests being served by the same interpreter/process (particularly impactful for threaded WSGI workers or ASGI sync-thread execution).
- severity: high; confidence: med (depends on re engine building a pathological pattern reachable from user input; Python's re is known to be vulnerable to backtracking blowups for certain constructs)
- assumptions needing runtime validation: that a concrete command\_regex/line combination reachable through this endpoint produces measurable exponential slowdown in the deployed Python version's re engine.

### FA-3

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 25-49 (\_run\_ps, especially lines 27-36 and 39-44)
- fault and direct code evidence: filter\_flags from the request body is split via shlex.split and appended verbatim to args (args.extend(extra), line 33) with no allowlist of permitted flags, then subprocess.run(args, capture\_output=True, text=True, check=False) buffers all of stdout/stderr fully into memory with no size cap.
- relevant execution path: monitor\_commands → payload.get("filter\_flags") → \_run\_ps(filter\_flags) → arbitrary ps flags forwarded to the ps binary → full stdout captured into proc.stdout/out → iterated again in \_parse\_ps\_output/monitor\_commands to build results.
- affected state or resource: process heap memory (buffers for capture\_output, the lines list, and the results list/JSON serialization) and the eventual JsonResponse payload size.
- triggering conditions: filter\_flags chosen to maximize ps output width/verbosity (e.g., wide www/environment-inclusive variants where supported) on a system with many processes/long command lines/large environments, producing a very large stdout buffer captured in one shot.
- existing cleanup, lifecycle, or bounding logic: none of the code paths impose a maximum output size, maximum line count, or truncation before building results and calling JsonResponse(results, safe=False, status=200) (line 156); capture\_output=True has no size bound in subprocess.run.
- plausible runtime consequence: elevated memory usage per request proportional to system process count/verbosity, worsened by repeated/concurrent requests, degrading throughput or causing memory pressure under sustained load.
- severity: med; confidence: med
- assumptions needing runtime validation: how many/how verbose processes typically exist on the deployment target, and whether concurrent requests to this endpoint are frequent enough for buffer sizes to matter.

### FA-4

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, lines 52-72 (\_parse\_ps\_output)
- fault and direct code evidence: header = lines[0] (line 62) unconditionally treats the first non-blank output line as a header row, and column/PID detection depends on finding a literal "PID" token (if c.upper() == "PID", line 66); lines[1:] (line 72) is then always excluded from data\_lines regardless of whether it was actually a header.
- relevant execution path: \_run\_ps output (which can be shaped by attacker/user-supplied filter\_flags, e.g. flags requesting a headerless or custom-format ps invocation) → \_parse\_ps\_output(out) → first real data row silently treated as a header and dropped, and/or pid\_idx ends up None because no literal "PID" column text appears → fallback heuristic in \_extract\_pid\_from\_line (lines 90-101) scans for any digit token in range 1..10,000,000, which can match unrelated numeric fields (e.g., %CPU, %MEM, VSZ, RSS, TTY-like or start-time-like tokens) instead of the true PID.
- affected state or resource: correctness of the results list returned to the client — both loss of the first legitimate process entry and misattribution of processId values.
- triggering conditions: any filter\_flags value producing ps output without a literal "PID" header token, or output whose first row is data rather than a header; also possible on systems/locales where the header text differs from the exact string "PID".
- existing cleanup, lifecycle, or bounding logic: the code comments acknowledge the ambiguity ("Some ps variants might not include a header... we still return data lines and let extraction try a fallback", lines 70-72) but there is no verification that lines[0] is actually a header before discarding it, nor any check that the fallback's chosen digit token is plausible for the given column layout — the fallback is a heuristic guess, not a correctness guarantee.
- plausible runtime consequence: systematically incorrect or incomplete API responses (dropped first process entry; wrong processId values reported for matched processes) without any error surfaced to the caller — a silent correctness fault rather than a crash.
- severity: med; confidence: med
- assumptions needing runtime validation: what exact ps flag combinations are reachable/likely in the deployment environment and whether any of them omit the literal "PID" header or reorder columns in a way that defeats the positional fallback.

### FA-5

- file: coding-tasks/python-Django/Monitor/code/myapp/views.py, line 105 (def monitor\_commands(...)) together with coding-tasks/python-Django/Monitor/code/mysite/asgi.py, lines 1-6
- fault and direct code evidence: the project ships ASGI\_APPLICATION = "mysite.asgi.application" (settings.py line 49) and mysite/asgi.py builds application = get\_asgi\_application(), i.e., an ASGI deployment path is configured, yet the only view, monitor\_commands, is declared as a plain synchronous function (def monitor\_commands(request: HttpRequest):) that performs a blocking subprocess.run call (see FA-1) inside it.
- relevant execution path: under ASGI serving, Django's ASGIHandler dispatches synchronous views via asgiref's sync\_to\_async with thread\_sensitive=True by default, which routes all such calls through a single shared executor thread per event loop/process rather than a pool, serializing execution of thread-sensitive sync code.
- affected state or resource: the shared single "sync" worker thread used by asgiref.sync.SyncToAsync for thread-sensitive calls in the ASGI process; concurrency of the whole application under that worker for any thread\_sensitive-dispatched sync work.
- triggering conditions: deployment via an ASGI server (e.g., uvicorn/daphne) using mysite.asgi, combined with concurrent requests to /monitor/commands, especially compounded by the unbounded blocking behavior described in FA-1.
- existing cleanup, lifecycle, or bounding logic: none — the view is not declared async def, and there is no explicit thread\_sensitive=False marker or offloading to a thread/process pool; Django's default synchronous-view handling under ASGI is the only lifecycle control in effect, which is not sufficient to provide true request-level concurrency for this specific view.
- plausible runtime consequence: under ASGI deployment, concurrent calls to this endpoint are effectively serialized on one thread rather than running in parallel, reducing throughput and amplifying the impact of any single slow/hanging subprocess.run call across all concurrent clients.
- severity: med; confidence: med (contingent on which of the two entry points — wsgi.py vs asgi.py — is actually used to serve the app at runtime)
- assumptions needing runtime validation: confirmation that the deployment actually runs mysite.asgi (rather than mysite.wsgi) in production, and the asgiref version's default thread-sensitive executor behavior in that environment.