#!/usr/bin/env python3
"""Check l3-task1: replace the starter's polling thread with a workqueue item (see TASKS.md, L3).

Static: the polling thread and its flag are gone; the app defines a work item
(K_WORK_DEFINE / k_work_init, or the delayable kind for the bonus) and the sensor
thread submits it.
Run: builds app/ for native_sim, runs it for 3 s and reads the starter's log lines
  [SENSOR] event <i>  tick=<ms>
  [HANDLER] processed event <n>  tick=<ms>
  [SUMMARY] events=<n> handler_runs=<n>
Without the bonus, each event gets one handler run in the same tick (event-driven,
no polling delay). With the bonus (k_work_reschedule), bursts collapse: fewer
handler runs than events, each at least 20 ms after the burst's last event.

    python3 .github/scripts/check_l3_task1.py --root zephyr-course --json result.json

Exit code 0 when approved, 1 otherwise.
"""
import argparse
import os
import re
import sys

from homework_report import Report
import homework_app as app

APP = "app"
MAIN = f"{APP}/src/main.c"
SECONDS = 3
TICK_SLACK_MS = 2


def static_checks(report, root):
    files = app.sources(root, APP)
    if not report.check(files, f"`{APP}/` has the app's sources",
                        fix=f"Keep the app in `{APP}/`, as in the `l3-homework` starter."):
        return None
    code = "\n".join(app.strip_comments(t) for t in files.values())
    polling = [t for t in app.threads(files) if "poll" in t["name"].lower() or "poll" in t["entry"].lower()]
    report.check(not polling, "the polling thread is removed", path=polling[0]["path"] if polling else MAIN,
                 line=polling[0]["line"] if polling else None,
                 fix="Remove `polling_thread` and `polling_fn()`: the work handler replaces them.")
    flag = re.compile(r"\bsensor_flag\b")
    path, line = app.locate(files, flag)
    report.check(not path, "the shared `sensor_flag` is removed", path=path, line=line,
                 fix="Remove `sensor_flag`: `k_work_submit()` tells the handler there is an event.")
    bonus = bool(re.search(r"\bk_work_reschedule\s*\(", code))
    has_work = re.search(r"\bK_WORK_(?:DELAYABLE_)?DEFINE\s*\(|\bk_work_init(?:_delayable)?\s*\(", code)
    report.check(has_work, "a work item is defined", path=MAIN,
                 fix="Define the work item with `K_WORK_DEFINE(sensor_work, sensor_handler);`.")
    sensor = next(((p, b, ln) for p, b, ln in (app.find_function(files, t["entry"]) for t in app.threads(files))
                   if b and "[SENSOR]" in b), (None, None, None))
    path, body, line = sensor
    report.check(body is not None and re.search(r"\bk_work_(?:submit|reschedule|schedule)\w*\s*\(", body),
                 "the sensor thread submits the work", path=path or MAIN, line=line,
                 fix="Call `k_work_submit(&sensor_work)` in the sensor thread where it used to set the flag.")
    return bonus


def handler_at(files):
    """Keyword path/line of the `[HANDLER]` log line, or of the work handler, for Report.check."""
    found = app.where(files, "[HANDLER]")
    if found["path"]:
        return found
    for path, text in files.items():
        code = app.strip_comments(text)
        for macro in ("K_WORK_DEFINE", "K_WORK_DELAYABLE_DEFINE", "k_work_init", "k_work_init_delayable"):
            for args, line, _ in app.calls(code, macro):
                if len(args) > 1:
                    fpath, _, fline = app.find_function(files, args[1])
                    return {"path": fpath or path, "line": fline or line}
    return found


def run_checks(report, root, work, bonus):
    build_dir = os.path.join(work, "build")
    if not app.check_build(report, root, APP, build_dir):
        return
    files = app.sources(root, APP)
    sensor = app.where(files, "[SENSOR]")
    handler = handler_at(files)
    submit = app.where(files, re.compile(r"\bk_work_(?:submit|reschedule|schedule)\w*\s*\("), "[SENSOR]")
    out, _ = app.run_native(build_dir, SECONDS)
    events = [(i, app.field(ln, "tick")) for i, ln in app.tagged(out, "SENSOR") if re.search(r"\bevent\s+\d", ln)]
    handled = [(i, app.field(ln, "tick")) for i, ln in app.tagged(out, "HANDLER")]
    report.check(not app.tagged(out, "CONSUMER"), "no `[CONSUMER]` lines from the old polling thread",
                 **app.where(files, "[CONSUMER]"),
                 fix="Remove the polling consumer; only the work handler processes events.")
    if not report.check(events, "`[SENSOR] event` lines are printed", **sensor,
                        fix="Keep the starter's `[SENSOR] event <i>  tick=<ms>` log line in the sensor thread."):
        return
    if not report.check(handled, "`[HANDLER]` lines are printed", **handler,
                        fix="Log `[HANDLER] processed event <n>  tick=<ms>` in the work handler (the starter's "
                            "placeholder has it)."):
        return
    if None in [t for _, t in events + handled]:
        report.check(False, "the log lines include `tick=`", **(sensor if None in [t for _, t in events] else handler),
                     fix="Keep `tick=%u` with `k_uptime_get_32()` in the `[SENSOR]` and `[HANDLER]` lines.")
        return
    if not bonus:
        report.check(len(handled) == len(events), f"one handler run per event ({len(handled)} for {len(events)})",
                     **submit,
                     fix=f"The handler ran {len(handled)} times for {len(events)} events. Submit the work once per "
                         "sensor event and log `[HANDLER]` once per handler run.")
        late = [(e, h) for (_, e), (_, h) in zip(events, handled) if not 0 <= h - e <= TICK_SLACK_MS]
        report.check(not late, "each event is handled right away, not on a polling period", **submit,
                     fix="The handler ran %d ms after its event; it should run in the same tick. Submit the work from "
                         "the sensor thread instead of waking up periodically." % (late[0][1] - late[0][0])
                     if late else None)
    else:
        report.note("Bonus: the sensor uses `k_work_reschedule()` to debounce bursts.")
        report.check(0 < len(handled) < len(events),
                     f"bursts collapse into fewer handler runs ({len(handled)} for {len(events)} events)", **submit,
                     fix=f"The handler ran {len(handled)} times for {len(events)} events; with `k_work_reschedule()` "
                         "each burst should give one run.")
        early = []
        for i, h in handled:
            before = [t for j, t in events if j < i]
            if before and h - before[-1] < 20:
                early.append(h)
        report.check(not early, "each handler run comes after the burst's debounce delay", **submit,
                     fix="A handler ran less than 20 ms after the last event of its burst. Reschedule the delayable "
                         "work on every event, with a delay longer than the burst (e.g. 30 ms).")
    summary = app.tagged(out, "SUMMARY")
    report.check(summary and app.field(summary[-1][1], "handler_runs") == len(handled),
                 "`[SUMMARY]` reports the handler runs", **app.where(files, "[SUMMARY]"),
                 fix="Print `[SUMMARY] events=<n> handler_runs=<n>` at the end, with the counts from the run.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for the build and its log (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    report = Report("l3-task1", "Polling to workqueue", where=MAIN)
    bonus = static_checks(report, os.path.abspath(args.root))
    if bonus is not None and not args.no_run and not any(c["ok"] is False for c in report.checks):
        run_checks(report, os.path.abspath(args.root), os.path.abspath(args.work), bonus)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
