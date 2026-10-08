#!/usr/bin/env python3
"""Run a command and record the CPU and memory use of its whole process tree.

Linux only, standard library only: it reads /proc every --interval seconds.
CPU is cumulative user+system seconds per process, so it still counts a child
that exited after being sampled. A child that starts and exits between two
samples is missed. Memory is the resident set size (RSS) summed over the tree;
the peak is the kernel's high-water mark (VmHWM), so it also catches spikes
between samples for processes that were alive at a sample.

    tests/measure_resources.py --name tray --duration 90 -- flatshot --tray

Writes perf/<name>.json (summary), perf/<name>.csv (time series) and
perf/<name>.log (the command's output). Exits with the command's exit code,
or 0 when the sampler stopped it at --duration.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")
MB = 1024 * 1024


def snapshot():
    """Return {pid: (ppid, cpu_seconds, rss_bytes, peak_rss_bytes)} for all visible processes."""
    procs = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                stat = f.read()
            with open(f"/proc/{name}/status") as f:
                status = f.read()
        except OSError:  # exited while we were looking
            continue
        # Fields after the "(comm)" name, starting at field 3 (state): index 1
        # is ppid, 11 is utime and 12 is stime, in clock ticks.
        fields = stat[stat.rindex(")") + 2:].split()
        cpu = (int(fields[11]) + int(fields[12])) / HZ
        rss = peak = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
            elif line.startswith("VmHWM:"):
                peak = int(line.split()[1]) * 1024
        procs[int(name)] = (int(fields[1]), cpu, rss, peak)
    return procs


def descendants(procs, root):
    children = {}
    for pid, (ppid, *_) in procs.items():
        children.setdefault(ppid, []).append(pid)
    found, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in procs:
            found.append(pid)
            todo.extend(children.get(pid, []))
    return found


def main():
    argv = sys.argv[1:]
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--duration", type=float, default=300,
                    help="seconds before a still-running command is stopped by the sampler")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--expect-running", action="store_true",
                    help="fail if the command exits before --duration")
    ap.add_argument("--outdir", default="perf")
    args = ap.parse_args(argv)
    if not cmd:
        ap.error("no command after --")
    os.makedirs(args.outdir, exist_ok=True)

    with open(os.path.join(args.outdir, f"{args.name}.log"), "w") as log:
        start = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        seen = {}     # pid -> cpu seconds at its last sample (monotonic, so exited pids keep theirs)
        samples = []  # (t, cpu_total, rss_total, peak_total, nprocs)
        stopped = False
        while True:
            procs = snapshot()  # sample before polling, so a just-exited root is still counted
            members = descendants(procs, proc.pid)
            now = time.monotonic() - start
            for pid in members:
                seen[pid] = procs[pid][1]
            samples.append((
                now,
                sum(seen.values()),
                sum(procs[p][2] for p in members),
                sum(procs[p][3] for p in members),
                len(members),
            ))
            if proc.poll() is not None:
                break
            if now >= args.duration:
                stopped = True
                for pid in members:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                break
            time.sleep(args.interval)
        try:
            code = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
        elapsed = time.monotonic() - start

    t_end, cpu_end, rss_end, _, _ = samples[-1]
    second_half = [s for s in samples if s[0] >= t_end / 2]
    steady = None
    if len(second_half) > 1 and second_half[-1][0] > second_half[0][0]:
        t0, cpu0 = second_half[0][0], second_half[0][1]
        steady = (cpu_end - cpu0) / (t_end - t0) * 100

    result = {
        "name": args.name,
        "command": cmd,
        "exit_code": code,
        "stopped_by_sampler": stopped,
        "wall_seconds": round(elapsed, 1),
        "cpu_seconds": round(cpu_end, 2),
        "avg_cpu_percent": round(cpu_end / elapsed * 100, 1),
        "second_half_cpu_percent": round(steady, 1) if steady is not None else None,
        "peak_rss_mb": round(max(s[3] for s in samples) / MB, 1),
        "final_rss_mb": round(rss_end / MB, 1),
        "max_processes": max(s[4] for s in samples),
    }
    with open(os.path.join(args.outdir, f"{args.name}.csv"), "w") as f:
        f.write("t_seconds,cpu_seconds,rss_mb,processes\n")
        for t, c, r, _, n in samples:
            f.write(f"{t:.2f},{c:.2f},{r / MB:.1f},{n}\n")
    with open(os.path.join(args.outdir, f"{args.name}.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))

    if args.expect_running and not stopped:
        print(f"::error::{args.name}: exited after {elapsed:.1f}s, before --duration {args.duration}s",
              file=sys.stderr)
        return 1
    return 0 if stopped else code


if __name__ == "__main__":
    sys.exit(main())
