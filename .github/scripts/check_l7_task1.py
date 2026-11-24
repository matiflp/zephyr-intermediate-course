#!/usr/bin/env python3
"""Check l7-task1: isolate the command parser in user mode (see TASKS.md, L7).

Static:
  - the project (repository root, app/ or another folder with CMakeLists.txt,
    prj.conf and src/parser.c) enables CONFIG_USERSPACE;
  - the thread that calls parse_command() is created with K_USER, or drops to user
    mode with k_thread_user_mode_enter();
  - no memory partition gives the parser the key (device_key, the credentials data
    or all of RAM);
  - parser.c and credentials.c are the starter's: only whitespace, comment or
    #include changes are accepted.
Run: builds the project for mps2/an521/cpu0 and runs it in QEMU. `get=0` must still
print `led=off`, an attack command must reach the parser and cause an MPU fault,
and the key (EC-PRIVATE-KEY) must never be printed.

    python3 .github/scripts/check_l7_task1.py --root zephyr-course --json result.json

Exit code 0 when approved, 1 otherwise.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys

from homework_report import Report
import homework_app as app

BOARD = "mps2/an521/cpu0"
RUN_SECONDS = 20
# sha256 of the starter's files after normalize(): comments, #include lines and whitespace removed.
STARTER = {
    "parser.c": "3aab09bf69acb1f714ed0b0ba8205b463202780a1844d4518054a1e9732f345b",
    "credentials.c": "36deb5be3bd3c0f00deae39b8d20dfc161683b0a873f7fe272840bb92e18702e",
}


def normalize(text):
    code = app.strip_comments(text)
    code = "\n".join(ln for ln in code.splitlines() if not ln.strip().startswith("#include"))
    return hashlib.sha256(re.sub(r"\s+", "", code).encode()).hexdigest()


def find_project(root):
    """The folder (relative to root) with CMakeLists.txt, prj.conf and src/parser.c, or None."""
    candidates = ["", "app"] + sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))
                                      and d not in app.SKIP_DIRS and d not in ("app", "docker"))
    for rel in candidates:
        base = os.path.join(root, rel)
        if all(os.path.isfile(os.path.join(base, f)) for f in ("CMakeLists.txt", "prj.conf", "src/parser.c")):
            return rel
    return None


def static_checks(report, root):
    proj = find_project(root)
    if not report.check(proj is not None, "the project is complete",
                        fix="Keep the project together: `CMakeLists.txt`, `prj.conf` and `src/` (with `parser.c`) in "
                            "the same folder, as in the `l7-homework` starter."):
        return None
    prefix = f"{proj}/" if proj else ""
    report.where = f"{prefix}src/main.c"
    ok = report.check(app.kconfig(root, proj, "USERSPACE") == "y", "`CONFIG_USERSPACE=y`", path=f"{prefix}prj.conf",
                      fix=f"Enable user mode: add `CONFIG_USERSPACE=y` to `{prefix}prj.conf`. Without it `K_USER` has "
                          "no effect.")
    for name, expected in STARTER.items():
        rel = f"{prefix}src/{name}"
        text = app.read(os.path.join(root, rel))
        ok &= report.check(text is not None and normalize(text) == expected, f"`{name}` is not modified", path=rel,
                           fix=f"Restore `{rel}` from the starter (`git checkout l7-homework -- {rel}`). The protection "
                               "must come from how the code runs, not from changing the parser or the key.")
    files = app.sources(root, proj or ".")
    files = {p: t for p, t in files.items() if not p.startswith("docker/")}
    callers = []  # functions that call parse_command()
    for path, text in files.items():
        code = app.strip_comments(text)
        for m in re.finditer(r"\b(\w+)\s*\([^;{)]*\)\s*\{", code):
            body, _ = app.function_body(code, m.group(1))
            if body and re.search(r"\bparse_command\s*\(", body) and m.group(1) != "parse_command":
                callers.append(m.group(1))
    parser_thread = next(({"path": t["path"], "line": t["line"]} for t in app.threads(files) if t["entry"] in callers),
                         None)
    if parser_thread is None and callers:
        path, _, line = app.find_function(files, callers[0])
        parser_thread = {"path": path, "line": line}
    user = False
    for t in app.threads(files):
        if t["entry"] in callers and "K_USER" in t["options"]:
            user = True
    for path, text in files.items():
        code = app.strip_comments(text)
        for args, _, _ in app.calls(code, "k_thread_user_mode_enter"):
            if args and args[0] in callers:
                user = True
        for fn in callers:
            body, _ = app.function_body(code, fn)
            if body and re.search(r"\bk_thread_user_mode_enter\s*\(", body):
                user = True
    ok &= report.check(user, "the parser runs in a user-mode thread", **(parser_thread or {}),
                       fix="Create the parser thread with `K_USER` in `k_thread_create()`'s options (or call "
                           "`k_thread_user_mode_enter()` before parsing).")
    leaks, leak_at = [], {}
    for path, text in files.items():
        code = app.strip_comments(text)
        for args, line, _ in app.calls(code, "K_MEM_PARTITION_DEFINE"):
            joined = ", ".join(args)
            if re.search(r"device_key|credentials|_image_ram|SRAM|RAM_ADDR|0x2[0-9a-fA-F]{7}|0x3[0-9a-fA-F]{7}", joined):
                leaks.append(args[0] if args else "?")
                leak_at = leak_at or {"path": path, "line": line}
        m = re.search(r"\bK_APP_(?:B|D)MEM\s*\([^)]*\)\s*[^;]*\bdevice_key\b", code)
        if m:
            leaks.append("device_key")
            leak_at = leak_at or {"path": path, "line": app.line_of(code, m.start())}
    ok &= report.check(not leaks, "no memory partition gives the parser the key", **leak_at,
                       fix=f"Don't give the parser's thread a partition that covers the key or all RAM "
                           f"({', '.join(f'`{x}`' for x in leaks)}). The parser needs no extra grant.")
    return proj if ok else None


def run_checks(report, root, proj, work, cmake_args=()):
    build_dir = os.path.join(work, "build")
    if not app.check_build(report, root, proj, build_dir, label=f"the project builds for `{BOARD}`", board=BOARD,
                           cmake_args=cmake_args):
        return
    try:
        p = subprocess.run(["west", "build", "-d", build_dir, "-t", "run"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace", timeout=RUN_SECONDS)
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
    report.check("EC-PRIVATE-KEY" not in out, "the key is never printed",
                 fix="The key was printed: the parser could still read it. Run the parser in a user-mode thread "
                     "and don't grant it the credentials' memory.")
    report.check(re.search(r"setting 0 = led=off", out), "`get=0` still works",
                 fix="`get=0` no longer prints `led=off`. Keep the normal command: the parser thread must still "
                     "receive and parse it.")
    attacks = [m for m in re.findall(r"received 'get=(\d+)'", out) if m != "0"]
    report.check(attacks, "the attack commands still reach the parser",
                 fix="The attack commands no longer reach `parse_command()`. Don't filter or drop packets in "
                     "`main.c`: that fixes the bug instead of isolating the parser.")
    report.check(re.search(r"MPU FAULT|MemManage|FATAL ERROR", out), "the attack causes an MPU fault",
                 fix="The attack didn't cause an MPU fault. With `CONFIG_USERSPACE=y` and the parser in a user "
                     "thread, reading the key must fault.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for the build and its log (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--print-hashes", action="store_true", help="print normalize() of src/parser.c and credentials.c")
    ap.add_argument("--cmake-arg", action="append", default=[], help="extra CMake argument (local testing)")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if args.print_hashes:
        for name in STARTER:
            print(name, normalize(app.read(os.path.join(root, "src", name)) or ""))
        return 0
    report = Report("l7-task1", "Parser isolation in user mode")
    proj = static_checks(report, root)
    if proj is not None and not args.no_run:
        run_checks(report, root, proj, os.path.abspath(args.work), args.cmake_arg)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
