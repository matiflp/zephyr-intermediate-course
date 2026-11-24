#!/usr/bin/env python3
"""Check l6-task1: find and fix the control thread's scheduling delay (see TASKS.md, L6).

Static: the starter's workload is unchanged (EVENT_PERIOD_MS 250, MAINTENANCE_LOAD_US
45000, the maintenance thread still busy-waits), the deadline is DEADLINE_MS 10, the
miss warning is rate-limited, and the priorities stay in the CONTROL_PRIORITY and
MAINTENANCE_PRIORITY defines.
Evidence: app/l6_evidence/thread_list.txt (the shell's `kernel thread list` from the
original program) and app/l6_evidence/trace.txt (Babeltrace output of a CTF capture),
pasted as the tools print them.
Run: builds app/ for native_sim twice and reads `[LAT] seq=<n> latency_ms=<ms> misses=<n>`:
  1. with the original priorities (control 7, maintenance 4) put back in the defines:
     the detector must count misses, one per late event;
  2. as submitted: no event may be late.
native_sim time is simulated, so the numbers don't depend on the machine's load.

    python3 .github/scripts/check_l6_task1.py --root zephyr-course --json result.json

Exit code 0 when approved, 1 otherwise.
"""
import argparse
import os
import re
import shutil
import sys

from homework_report import Report
import homework_app as app

APP = "app"
MAIN = f"{APP}/src/main.c"
EVIDENCE = f"{APP}/l6_evidence"
THREAD_LIST = f"{EVIDENCE}/thread_list.txt"
TRACE = f"{EVIDENCE}/trace.txt"
CONSTANTS = {"EVENT_PERIOD_MS": 250, "MAINTENANCE_LOAD_US": 45000, "DEADLINE_MS": 10}
ORIGINAL = {"CONTROL_PRIORITY": 7, "MAINTENANCE_PRIORITY": 4}
SECONDS = 6  # deferred logging flushes once a second: keep the last second out
MIN_EVENTS = 15
TRACE_LINE = re.compile(r"^\[[\d:.]+\]\s+\(\+[\d?.]+\)\s+(?:\S+\s+)?(\w+):")


def static_checks(report, root):
    text = app.read(os.path.join(root, MAIN))
    if not report.check(text is not None, f"`{MAIN}` exists", fix=f"Keep the app in `{MAIN}`, as in the starter."):
        return False
    code = app.strip_comments(text)
    defs = app.defines(code)
    files = {MAIN: text}
    ok = True
    for name, value in CONSTANTS.items():
        ok &= report.check(app.eval_int(defs.get(name), defs) == value, f"`{name}` is {value}",
                           **app.where(files, re.compile(r"#\s*define\s+%s\b" % name)),
                           fix=f"Keep `#define {name} {value}`: the fix must keep the starter's workload and the "
                               "10 ms guarantee (TASKS.md, L6).")
    for name in ORIGINAL:
        ok &= report.check(name in defs, f"the priority is set by `{name}`",
                           **app.where(files, re.compile(r"\bK_THREAD_DEFINE\s*\(\s*%s\b" % name.split("_")[0].lower()),
                                       re.compile(r"\bK_THREAD_DEFINE\s*\(")),
                           fix=f"Keep `#define {name} <n>` and use it for the thread: the checks rebuild the app with "
                               "the original priorities to test your detector.")
    body, line = app.function_body(code, "maintenance_fn")
    ok &= report.check(body is not None and re.search(r"\bk_busy_wait\s*\(\s*MAINTENANCE_LOAD_US\s*\)", body)
                       and re.search(r"\bk_sem_take\s*\(\s*&\s*maintenance_start\b", body),
                       "the maintenance thread keeps its 45 ms of work", path=MAIN, line=line,
                       fix="Keep `maintenance_fn()` as in the starter: wait on `maintenance_start`, then "
                           "`k_busy_wait(MAINTENANCE_LOAD_US)`. Removing the workload doesn't fix the scheduling.")
    body, line = app.function_body(code, "control_fn")
    ok &= report.check(body is not None and re.search(r"\bLOG_\w+_RATELIMIT\w*\s*\(", body),
                       "the deadline-miss warning is rate-limited", path=MAIN, line=line,
                       fix="Print the deadline-miss warning with `LOG_WRN_RATELIMIT()`, and count every miss "
                           "outside it.")
    return bool(ok)


def thread_priorities(text):
    """{thread name: priority} from `kernel thread list` output."""
    out, name = {}, None
    for raw in text.splitlines():
        line = raw.strip("\r")
        m = re.match(r"^\s*\*?\s*0x[0-9a-fA-F]+\s+(\S+)", line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r"\bpriority:\s*(-?\d+)", line)
        if m and name:
            out[name] = int(m.group(1))
            name = None
    return out


def evidence_checks(report, root):
    text = app.read(os.path.join(root, THREAD_LIST))
    if report.check(text is not None, "the `kernel thread list` output is committed", path=THREAD_LIST,
                    fix=f"Run `kernel thread list` in the shell on the original program and commit its output, as "
                        f"printed, in `{THREAD_LIST}`."):
        prios = thread_priorities(text)
        report.check("Threads:" in text and prios, "`thread_list.txt` is `kernel thread list` output", path=THREAD_LIST,
                     fix=f"Paste the `kernel thread list` output into `{THREAD_LIST}` as the shell prints it.")
        report.check(prios.get("control") == 7 and prios.get("maintenance") == 4,
                     "`thread_list.txt` comes from the original program", path=THREAD_LIST,
                     fix="Capture `kernel thread list` on the original program, before the fix: it should show "
                         "`control` at priority 7 and `maintenance` at 4.")
    text = app.read(os.path.join(root, TRACE))
    if report.check(text is not None, "the decoded trace is committed", path=TRACE,
                    fix=f"Capture a CTF trace, decode it with Babeltrace and commit the output in `{TRACE}`."):
        events = [m.group(1) for m in (TRACE_LINE.match(ln.strip("\r")) for ln in text.splitlines()) if m]
        report.check(len(events) >= 10, "`trace.txt` is Babeltrace output", path=TRACE,
                     fix=f"Paste the Babeltrace output into `{TRACE}` as the tool prints it "
                         "(`[time] (+delta) event: { ... }` lines).")
        switched = {n for ln in text.splitlines() if "thread_switched_in" in ln
                    for n in re.findall(r'name\s*=\s*"(\w+)"', ln)}
        report.check({"control", "maintenance"} <= switched, "the trace shows `control` and `maintenance` running",
                     path=TRACE,
                     fix="Commit a trace that covers the control events: it should have `thread_switched_in` events "
                         "for both `maintenance` and `control`.")


def lat_lines(out):
    rows = []
    for _, line in app.tagged(out, "LAT"):
        seq, lat, misses = app.field(line, "seq"), app.field(line, "latency_ms"), app.field(line, "misses")
        if None not in (seq, lat, misses):
            rows.append((seq, lat, misses))
    return rows


def consistent(rows, deadline):
    """misses counts every late event so far."""
    count = 0
    for _, lat, misses in rows:
        count += lat > deadline
        if misses != count:
            return False
    return True


def run_checks(report, root, work):
    deadline = CONSTANTS["DEADLINE_MS"]
    # 1. The original priorities: the detector must see and count the misses.
    orig = os.path.join(work, "original")
    shutil.rmtree(orig, ignore_errors=True)
    shutil.copytree(os.path.join(root, APP), orig)
    main = os.path.join(orig, "src", "main.c")
    text = app.read(main)
    for name, value in ORIGINAL.items():
        text = re.sub(r"^(\s*#\s*define\s+%s\s+)[^\n]+" % name, r"\g<1>%d" % value, text, count=1, flags=re.M)
    with open(main, "w") as f:
        f.write(text)
    if not app.check_build(report, root, APP, os.path.join(work, "build-original"), src_dir=orig):
        return
    files = app.sources(root, APP)
    control = app.where(files, re.compile(r"\bcontrol_fn\s*\([^;{)]*\)\s*\{"))
    out, _ = app.run_native(os.path.join(work, "build-original"), SECONDS)
    rows = lat_lines(out)
    if not report.check(len(rows) >= MIN_EVENTS, "`[LAT] seq=<n> latency_ms=<ms> misses=<n>` is printed per event",
                        **app.where(files, "[LAT]", re.compile(r"\bcontrol_fn\s*\([^;{)]*\)\s*\{")),
                        fix="Log `[LAT] seq=<n> latency_ms=<ms> misses=<n>` for every control event, with "
                            "`LOG_INF()` (TASKS.md, L6)."):
        return
    report.check(any(lat > deadline for _, lat, _ in rows),
                 "with the original priorities, events are late", **control,
                 fix="With control at 7 and maintenance at 4 no event was late. Measure the start latency as "
                     "`k_uptime_get_32() - event.ready_ms` right after `k_msgq_get()`.")
    report.check(consistent(rows, deadline), "every late event is counted in `misses`",
                 **app.where(files, re.compile(r"\bmisses\s*(?:\+\+|\+=)"), re.compile(r"\+\+\s*misses\b"), "[LAT]"),
                 fix="`misses` doesn't count every event later than `DEADLINE_MS`. Increment it for each late event, "
                     "outside the rate-limited warning.")

    # 2. As submitted: no event late.
    if not app.check_build(report, root, APP, os.path.join(work, "build"), label="the fixed app builds"):
        return
    out, _ = app.run_native(os.path.join(work, "build"), SECONDS)
    rows = lat_lines(out)
    late = [(s, lat) for s, lat, _ in rows if lat > deadline]
    report.check(len(rows) >= MIN_EVENTS and not late, "with your fix, no event is late",
                 fix=("Event %d still started %d ms late. Fix the cause of the delay, not the workload or the deadline."
                      % late[0]) if late else "The fixed app didn't log `[LAT]` for every event.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for builds and logs (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    root, work = os.path.abspath(args.root), os.path.abspath(args.work)
    report = Report("l6-task1", "Runtime investigation", where=MAIN)
    ok = static_checks(report, root)
    evidence_checks(report, root)
    if ok and not args.no_run:
        run_checks(report, root, work)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
