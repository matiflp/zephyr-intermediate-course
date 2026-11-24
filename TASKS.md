# What the automatic checks expect

The lectures describe each task. This file lists what the checks need on top of that: the
names, files and log lines they read. Keep them exactly as written here. How to submit a task
is in [HOMEWORK.md](HOMEWORK.md).

The checks build your code for `native_sim` (L7: `mps2/an521/cpu0` in QEMU) and run it for a
few seconds. On `native_sim` time is simulated, so the timings in your logs don't depend on the
machine's load. A log line can come from `printk()` or `LOG_INF()`; the checks look for the tag
in square brackets anywhere in the line.

## L1 — Kernel and scheduling (`l1-task1`, from `main`)

Your app in `app/`:

- Three preemptible threads at priorities 7, 5 and 3 that sleep 300, 200 and 100 ms. Each one
  logs `[T7] tick=<n>`, `[T5] tick=<n>` or `[T3] tick=<n>` every time it wakes up.
- A cooperative thread at priority -1 that runs five busy iterations (`k_busy_wait()`), logging
  `[COOP] iter=<i>` in each one, then logs `[COOP] yield`, calls `k_yield()` and logs
  `[COOP] done`.

The checks count how often each thread logs, and check that no `[Tn]` line appears between the
first `[COOP] iter` and `[COOP] done`.

## L2 — Synchronization (`l2-task1`, from `main`)

- `app/src/counter.h` and `app/src/counter.c` (add it to `target_sources` in
  `app/CMakeLists.txt`) with:

  ```c
  void counter_increment_unsafe(void); /* read-modify-write, no protection */
  void counter_increment(void);        /* the same update under a mutex */
  int counter_get(void);
  void counter_reset(void);
  ```

- `app/src/main.c` runs two threads of the same priority that call `counter_increment_unsafe()`,
  then two that call `counter_increment()`, and after `k_thread_join()` on both prints:

  ```
  [RESULT] mode=unsafe total=<n> expected=<n>
  [RESULT] mode=mutex total=<n> expected=<n>
  ```

The unsafe total may come out right: a lost update doesn't happen on every run. The mutex total
must be exact. The course's test `tests/l2_counter` also calls your `counter_increment()` from two
threads; don't change it.

## L3 — Workqueues (`l3-task1`, from `l3-homework`)

Keep the starter's log lines: `[SENSOR] event <i>  tick=<ms>` in the sensor thread and
`[HANDLER] processed event <n>  tick=<ms>` in the work handler. At the end, `main()` prints
`[SUMMARY] events=<n> handler_runs=<n>`. Remove `polling_thread` and `sensor_flag`.

For the bonus (`k_work_reschedule()`), each burst of events gives one `[HANDLER]` line, after the
debounce delay.

## L4 — zbus (`l4-task1`, from `main`)

- One channel whose message is a struct with the sample, defined once in a `.c` file with
  `ZBUS_CHAN_DEFINE`. If other `.c` files use it, declare it with `ZBUS_CHAN_DECLARE()` in a header
  that also defines the struct, and include that header in each of them.
- A listener (`ZBUS_LISTENER_DEFINE`) for the display and a subscriber (`ZBUS_SUBSCRIBER_DEFINE`, or a
  message subscriber) for the logger, both observers of the channel. `CONFIG_ZBUS=y` in
  `app/prj.conf`.
- The names of the channel, the struct, the observers and the files are up to you.
- Log lines, with `t` from `k_uptime_get_32()`:

  ```
  [PUB] seq=<n> t=<ms>       before each publish, every 100 ms
  [DISPLAY] seq=<n> t=<ms>   in the listener's callback
  [LOGGER] seq=<n> t=<ms>    in the subscriber's thread
  ```

The listener's callback must not sleep, busy-wait or publish.

## L5 — Resource pressure (`l5-task1`, from `main`)

- The queue is a message queue (`K_MSGQ_DEFINE`); its name is up to you.
- `CONFIG_TASK_WDT=y`. The consumer adds a task watchdog channel and feeds it after each sample
  it processes.
- When the consumer receives the sample with `seq` 20, it logs `[CONS] simulating stall` and
  sleeps 5 s.
- The health-check thread logs `[HEALTH] occupancy=<used>/<capacity>` on every check, with `WARN`
  in the line when the queue is at 75% or more, e.g. `[HEALTH] WARN occupancy=6/8`.
- The watchdog callback logs `[WDT] ...`.

## L6 — Runtime investigation (`l6-task1`, from `l6-homework`)

- Keep the workload: `EVENT_PERIOD_MS 250`, `MAINTENANCE_LOAD_US 45000` and the maintenance
  thread's `k_busy_wait()`. Add `#define DEADLINE_MS 10`.
- Keep the priorities in `#define CONTROL_PRIORITY` and `#define MAINTENANCE_PRIORITY`: the checks
  rebuild your app with the original values (7 and 4) to test your detector.
- For every control event log `[LAT] seq=<n> latency_ms=<ms> misses=<n>`, where `misses` counts
  every late event so far. Rate-limit the miss warning with `LOG_WRN_RATELIMIT()`.
- Commit, as the tools print them, from the original program:
  - `app/l6_evidence/thread_list.txt`: the shell's `kernel thread list` output;
  - `app/l6_evidence/trace.txt`: the Babeltrace output of your CTF capture.

## L7 — Memory isolation (`l7-task1`, from `l7-homework`)

Nothing to add to `HOMEWORK_TASK.md`. Don't modify `src/parser.c` or `src/credentials.c`; the
checks compare them with the starter. They run the project in QEMU and expect `led=off` for
`get=0`, an MPU fault for the attack, and the key never printed.

## L8 — Unit tests (`l8-task0` to `l8-task3`, from `l8-demo-homework`)

Follow `tests/temp_alarm/HOMEWORK.md` and `TEST_SPEC.md`. The checks also run your tests against
broken copies of `temp_alarm.c`; a test that still passes doesn't check what the spec asks. Don't
modify `app/modules/temp_alarm/`.
