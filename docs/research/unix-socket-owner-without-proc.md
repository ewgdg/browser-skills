# Socket ownership and pid liveness across platforms

Accepted findings from the survey behind the session runtime's stall recovery. The full
evidence, primary sources and the list of things that could not be verified live in
`~/.agents/artifacts/outputs/browser-skills/2026-09-17/unix-socket-owner-without-proc/research.md`.

## Decision

The runtime identifies a session worker from the kernel's own record of its socket, and
signals a pid only when that identification is unique and provable.

| Fact | Linux | macOS | FreeBSD |
| --- | --- | --- | --- |
| Who holds `<session>.sock` | exact: `/proc/net/unix` inode matched against `/proc/<pid>/fd/*` | exact: `lsof -a -U -F pcfn` (ships with macOS); libproc through ctypes is the dependency-free option | no inverse lookup exists: enumerate `sockstat -u` or the `KERN_PROC_FILEDESC` sysctl |
| Is a pid alive, and not a zombie | `/proc/<pid>/stat` state | `kill(pid, 0)` plus `ps -o state=` | same as macOS, or `KERN_PROC_PID` |

`/proc` is a Linux interface. The two facts have different portability, and only the
first is a real porting problem.

## Rules the code follows

- **Ownership, not command lines.** A command-line match is satisfied by any process that
  merely names the socket path, so it is reported to the caller and never signalled.
- **Uniqueness is part of identity.** Two owners mean the path was rebound while an older
  listener is still alive; the sources cannot say which is current, so the caller is told
  nothing rather than a guess.
- **A zombie is not alive.** POSIX requires `kill(pid, 0)` to succeed for a zombie, so
  liveness needs a state check as well as an existence check.
- **`lsof` field output, not its table.** `-F pcfn` avoids the dialect differences: the
  Linux build appends ` type=STREAM` to a unix socket's name, and macOS adds a `TID`.
- **`ps` state is comparable on the first character only.** `U` (macOS) and `D`
  (FreeBSD/Linux) are both an uninterruptible wait; only `Z` and `X` mean finished. No row
  means no such process, which is not the same as a live one.
- **`-ww` is load-bearing on BSD and macOS**, where `ps` truncates by default and would cut
  a socket path before it can be matched.

## Still unimplemented

FreeBSD has no inverse socket lookup, so it would need enumeration; nothing here ships
that path, and a host with neither `/proc` nor `lsof` gets the report-only fallback.

