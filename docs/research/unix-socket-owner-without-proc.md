# Identifying a unix-socket owner and checking pid liveness without /proc

Date: 2026-09-17

## Conclusion

The two facts the surf runtime needs have different portability, and only the first
one is a real porting problem.

| Fact | Linux source | macOS | FreeBSD |
| --- | --- | --- | --- |
| Which pid holds `<session>.sock` open | exact: `/proc/net/unix` inode matched against `/proc/<pid>/fd/*` | exact via libproc (`proc_pidinfo(PROC_PIDLISTFDS)` + `proc_pidfdinfo(PROC_PIDFDSOCKETINFO)`), or `lsof -a -U <path>` | no inverse lookup; enumerate `sockstat -u` or `sysctl` per pid |
| Is pid P alive, not a zombie, and still the same process | `/proc/<pid>/stat` field 3 (state) and 22 (starttime) | `kill(P, 0)` + `sysctl KERN_PROC_PID` (`p_stat`, `p_starttime`) | `kill(P, 0)` + `sysctl KERN_PROC_PID` (`ki_stat`, `ki_start`) |

Recommended ordering of identity sources, strongest first, stopping at the first
one that answers:

1. **Exact by socket.**
   - Linux: the existing `/proc/net/unix` inode versus `/proc/<pid>/fd/*`
     readlink scan.
   - macOS: libproc through `ctypes` — `proc_listpids` to enumerate,
     `proc_pidinfo(PROC_PIDLISTFDS)` to list fds, `proc_pidfdinfo(PROC_PIDFDSOCKETINFO)`
     to read the bound unix address, compared as a whole path. Same-user, no root,
     no GUI; the API is present in the SDK but undocumented.
   - FreeBSD: no kernel inverse lookup exists; enumerate (`sockstat -u`, or
     `sysctl` `KERN_PROC_FILEDESC` per pid) and compare the reported path.
2. **`lsof` name match on the socket path** (`lsof -a -U <path>`). Documented as
   matching the characters recorded in the kernel socket structure, so it is
   still exact-by-socket where lsof exists. Shipped with macOS; **not** in the
   FreeBSD base system (it is the `sysutils/lsof` port, which needs kernel sources).
3. **Command-line match** (`ps -ww -Ao pid=,command=`), as the current code does.
   Weakest claim: a process that merely mentions the path can satisfy it. Keep it
   last, and keep reporting it as a weaker claim.

For liveness, the portable recipe is a three-step check, in this order:

1. **Existence**: `os.kill(pid, 0)`. `EPERM` means the pid *exists* (permission is
   evaluated against a found target); `ESRCH` means no such process (modulo hidden
   processes under `hidepid`).
2. **Identity**: compare a start-time token recorded when the worker was spawned
   against the current one — Linux `/proc/<pid>/stat` field 22 (clock ticks;
   divide by `os.sysconf("SC_CLK_TCK")`), macOS `p_starttime`, FreeBSD `ki_start`,
   or the string from `ps -o lstart=` as a last resort.
3. **Zombie**: Linux field 3 `== "Z"`; macOS/FreeBSD `p_stat`/`ki_stat == SZOMB`
   (5); `ps -o state=` first character `"Z"` where neither is available.

Two things not to do:

- **Do not treat `kill(pid, 0)` success as liveness.** A zombie satisfies it; POSIX
  makes that the conforming behaviour, not an implementation quirk.
- **Do not use `waitpid(WNOHANG)` on a pid that is not your child.** It returns
  `ECHILD`, which does not distinguish "gone" from "not mine".

## Primary-source evidence

### 1. procfs, and which parts of the current logic are Linux-only

`proc(5)`: "The proc filesystem is a pseudo-filesystem which provides an interface
to kernel data structures"
([proc(5)](https://man7.org/linux/man-pages/man5/proc.5.html)). It is a Linux
interface, not a POSIX one.

Both surf facts come from Linux-specific kernel interfaces:

- **Socket inode from the path**: `proc(5)` documents `/proc/net/unix` as listing
  "the UNIX domain sockets present within the system and their status" with the
  format `Num RefCount Protocol Flags Type St Inode Path`, e.g.
  `1: 00000001 00000000 00010000 0001 01  1948 /dev/printer`
  ([proc_net(5)](https://man7.org/linux/man-pages/man5/proc_net.5.html)). Note the
  row for an unbound socket has an empty Path column, which is why the current code
  skips zero-inode rows.
- **Which pid holds that inode**: `proc(5)` documents `/proc/pid/fd` as "symbolic
  links whose content is the file type with the inode", with the concrete example
  "`socket:[2248868]` will be a socket and its inode is 2248868. For sockets, that
  inode can be used to find more information in one of the files under /proc/net/"
  ([proc_pid_fd(5)](https://man7.org/linux/man-pages/man5/proc_pid_fd.5.html)). This
  readlink step is the part with no portable analogue.
- **State and start time**: `proc(5)` field 3 is "`state %c` ... One of the
  following characters" (`R`, `S`, `D`, `Z`, ...) and field 22 is
  "`starttime %llu` — The time the process started after system boot. ... the value
  is expressed in clock ticks (divide by `sysconf(_SC_CLK_TCK)`)"
  ([proc_pid_stat(5)](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)).
  Verified locally on Linux: field 3 of `/proc/self/stat` is the single state
  character, field 22 is clock ticks, `SC_CLK_TCK` is 100 here.

**macOS has no procfs.** The shipped XNU source tree contains no procfs
implementation: `bsd/miscfs/` holds `bindfs deadfs devfs fifofs mockfs nullfs
routefs specfs union`, and the only occurrence of the string "procfs" anywhere
under `bsd/` is the `VT_PROCFS` mount-type constant in
[`bsd/sys/vnode.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/vnode.h).
The same absence holds for the two earlier release tags checked
(`xnu-8020.101.4`, `xnu-4903.221.2`: no `miscfs/procfs` path in the tree). This is
an absence-of-evidence argument from the source tree, not an Apple statement.

**FreeBSD has a procfs, and deprecates it.** `procfs(5)`:

> This functionality is deprecated. Users are advised to use libprocstat(3) and
> kvm(3) instead.
> ... The process file system, or procfs, implements a view of the system process
> table inside the file system. It is normally mounted on /proc.

([procfs(5)](https://man.freebsd.org/cgi/man.cgi?query=procfs&sektion=5&manpath=FreeBSD+14.1-RELEASE)).
What FreeBSD provides instead is the sysctl interface (`kern.proc.*`) plus the
`procstat`/`fstat`/`sockstat`/`libprocstat` tools, detailed below.

### 2. macOS: finding the pid that holds a socket path open

#### `lsof -a -U` (shipped with macOS)

The macOS man page set documents `lsof(8)` (the same page is mirrored by FreeBSD's
man server; the macOS text identifies itself as "Lsof revision 4.91" and lists
"Apple Darwin 9 and Mac OS X 10.[...]" in `DISTRIBUTION`,
[lsof(8), macOS 26.6.1](https://man.freebsd.org/cgi/man.cgi?manpath=macOS+26.6.1&query=lsof&sektion=8)).
Apple also publishes the lsof source as part of macOS
([apple-oss-distributions/lsof](https://api.github.com/repos/apple-oss-distributions/lsof/contents/),
containing `Makefile`, `lsof.plist`, `entitlements.plist`), and Homebrew marks
its formula "Keg-only because macOS already provides this software"
([Homebrew lsof](https://formulae.brew.sh/formula/lsof)). Together this is strong
evidence that `/usr/sbin/lsof` is part of the base system; Apple does not publish a
file manifest I could cite directly, so treat "always present" as very likely
rather than contractual.

Documented behaviour that matters:

- Columns are `COMMAND PID TID USER FD TYPE DEVICE SIZE/OFF NODE NAME` (the macOS
  page's `COMMAND` description shows the `TID` column for task output); the `FD`
  column "constitutes a single field for parsing in post-processing scripts"
  (`FD` numbers above 9999 are abbreviated, e.g. `*001`).
- `-U` "selects the listing of UNIX domain socket files", and `-a` "causes list
  selection options to be ANDed". The man page's own worked example: "specifying
  `-a`, `-U`, and `-ufoo` produces a listing of only UNIX socket files that belong
  to processes owned by user `foo`". So combining a socket-type selection with any
  other selection needs `-a`.
- **Path to pid works by name matching.** "If a name is a UNIX domain socket name,
  lsof will *usually* search for it by the characters of the name alone — exactly as
  it is specified and is recorded in the kernel socket structure. ... Specifying a
  relative path — e.g., `./file` — in place of the file's absolute path ... won't
  work". The dev/inode fallback described immediately afterwards in the same
  section is explicitly "a Linux UNIX domain socket name" case that requires
  `/proc/net/unix` ("the absolute path ... be stored in the `/proc/net/unix`
  file"), so on macOS the name match is the only mechanism.
- The word "usually" is not elaborated; where it does not apply, lsof falls back to
  "any open files whose device and inode match that of the specified path name".
  Since our socket is a filesystem object too, a name search can also match the
  *file*, which is why the socket selection must be ANDed in with `-a`.

Concrete invocation, keeping `-a` first so nothing is ORed:

```
lsof -a -U -F pcfn -- <socket path>      # name search ANDed with the unix-socket selection
lsof -a -U -p <pid>                      # what this pid holds open, same shape
```

(`--` is not documented as an lsof option on this man page; the path is simply a
name argument. Use it only if the path can never begin with `-`.)

`-F` is the documented machine-readable form — "lsof produces output that is
suitable for post-processing"; field characters include `p` "process ID (always
selected)", `c` "process command name", `f` "file descriptor", `t` "file's type",
`n` "file name, comment, Internet address", and `?` for the full list. Prefer these
fields over parsing the human `NAME` column, whose contents are dialect-specific: on
Linux lsof decorates the socket name (`type=STREAM (LISTEN)` locally), and on macOS
the man page documents several `NAME` shapes including bare kernel addresses.

*Illustration on this Linux host* (a Python process bound to
`/tmp/proc-research/demo.sock`):

```
$ lsof -a -U -p 3623529
COMMAND     PID USER FD   TYPE             DEVICE SIZE/OFF      NODE NAME
python3 3623529 xian 3u  unix 0x00000000bb8eb94f      0t0 104413736 /tmp/proc-research/demo.sock type=STREAM (LISTEN)
```

The exact macOS rendering of a bound unix socket in `NAME` is **UNVERIFIED** here:
the macOS man page describes the `NAME` column in dialect-generic prose
(including UnixWare- and Solaris-specific shapes) and I had no macOS host. Parsing
`-F` fields avoids depending on that.

#### `netstat -anv` and `nettop`

`netstat` does not report owners. Apple's netstat source prints the unix table as
"Active LOCAL (UNIX) domain sockets" with the header
`Address Type Recv-Q Send-Q Inode Conn Refs Nextref Addr`, and appends the path only
from `sockaddr_un.sun_path` — there is no pid or user column
([network_cmds/netstat.tproj/unix.c](https://github.com/apple-oss-distributions/network_cmds/blob/main/netstat.tproj/unix.c)).
`-v` adds socket statistics, not identity.

`nettop(1)` "Display[s] updated information about the network" grouped per process;
its `-P` option is documented as "Display per-process summary only, skipping
details of open connections" ([nettop(1)](https://manp.gs/mac/1/nettop) — a mirror of
the Apple man page, the only reachable copy; Apple's own nettop source is not in
`network_cmds`). It reports traffic per network flow, so it is the wrong tool for
enumerating bound unix sockets; I could not verify from a primary source that it
lists them at all. **UNVERIFIED, and not recommended.**

#### libproc (`ctypes`) — exact path match, undocumented API

This is the only macOS mechanism that answers "which process holds this exact socket
path" by identity rather than by name text, and it needs no root.

`libproc.h` declares the entry points, available since macOS 10.5:

```c
int proc_listpids(uint32_t type, uint32_t typeinfo, void *buffer, int buffersize);
int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer, int buffersize);
int proc_pidfdinfo(int pid, int fd, int flavor, void * buffer, int buffersize);
int proc_pidpath(int pid, void * buffer, uint32_t buffersize);
```

([`libsyscall/wrappers/libproc/libproc.h`](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.h)).
These headers are present in the macOS SDK: `usr/include/libproc.h` and
`usr/include/sys/proc_info.h` both resolve in the SDK mirrors checked
([MacOSX11.3.sdk](https://raw.githubusercontent.com/phracker/MacOSX-SDKs/master/MacOSX11.3.sdk/usr/include/libproc.h),
[sys/proc_info.h](https://raw.githubusercontent.com/phracker/MacOSX-SDKs/master/MacOSX11.3.sdk/usr/include/sys/proc_info.h)).

The flavor constants, in `bsd/sys/proc_info.h`:
- `#define PROC_PIDLISTFDS 1` with `#define PROC_PIDLISTFD_SIZE (sizeof(struct proc_fdinfo))`
- `#define PROC_PIDFDSOCKETINFO 3` with `#define PROC_PIDFDSOCKETINFO_SIZE (sizeof(struct socket_fdinfo))`
- `struct proc_fdinfo { int32_t proc_fd; uint32_t proc_fdtype; };`
- `#define PROX_FDTYPE_SOCKET 2` (the `proc_fdtype` value to select)

([`bsd/sys/proc_info.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h)).

**Does the returned structure carry the unix socket's local path? Yes.** The buffer
for `PROC_PIDFDSOCKETINFO` is
`struct socket_fdinfo { struct proc_fileinfo pfi; struct socket_info psi; }`, and
`struct socket_info` embeds, by socket kind, a `struct un_sockinfo`:

```c
struct un_sockinfo {
        uint64_t      unsi_conn_so;   /* opaque handle of connected socket */
        uint64_t      unsi_conn_pcb;  /* opaque handle of connected protocol control block */
        union {
                struct sockaddr_un      ua_sun;
                char                    ua_dummy[SOCK_MAXADDRLEN];
        }             unsi_addr;      /* bound address */
        union {
                struct sockaddr_un      ua_sun;
                char                    ua_dummy[SOCK_MAXADDRLEN];
        }             unsi_caddr;     /* address of socket connected to */
};
```

`soi_kind` is `SOCKINFO_UN = 3` for these sockets (`SOCKINFO_GENERIC/IN/TCP/UN/...`).
The kernel fills it from the unix protocol control block in
`fill_socketinfo()`:

```c
if (unp->unp_addr) {
        size_t  addrlen = unp->unp_addr->sun_len;
        if (addrlen > SOCK_MAXADDRLEN) { addrlen = SOCK_MAXADDRLEN; }
        SOCKADDR_COPY(unp->unp_addr, &unsi->unsi_addr.ua_sun, addrlen);
}
```

([`bsd/kern/socket_info.c`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/socket_info.c),
reachable from `pid_socketinfo()` in
[`bsd/kern/proc_info.c`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/proc_info.c)
when the fd's type is `DTYPE_SOCKET`). So `sun_len`/`sun_path` of the bound address
are available to user space and an exact whole-path comparison is possible.

Permissions: it is same-user only, and that is enforced in the kernel.
`proc_pidfdinfo` and `PROC_PIDLISTFDS` (through `proc_pidinfo`) take the
`check_same_user` path, where `proc_security_policy()` compares
`kauth_cred_getuid()` of caller and target and returns `EPERM` on a mismatch unless
the caller holds `PRIV_GLOBAL_PROC_INFO`:

```c
if (check_same_user) {
        ...
        if (kauth_cred_getuid(my_cred) != kauth_cred_getuid(tg_cred)) {
                error = EPERM;
        }
        ...
        if (error && priv_check_cred(my_cred, PRIV_GLOBAL_PROC_INFO, 0)) {
                return EPERM;
        }
}
```

Only `PROC_PIDT_SHORTBSDINFO`, `PROC_PIDUNIQIDENTIFIERINFO`, `PROC_PIDPATHINFO`,
`PROC_PIDCOALITIONINFO` and `PROC_PIDPLATFORMINFO` skip the same-user check;
`PROC_PIDLISTFDS` and `PROC_PIDFDSOCKETINFO` do not. `proc_listpids` itself is
`NO_CHECK_SAME_USER`, so *enumerating* pids is unprivileged while *reading another
user's fds* is not. Since the client and its worker run as the same user, this costs
nothing — but it means the libproc path can never replace `ps` for cross-user
diagnosis.

**Documented vs private.** These are the "private" APIs in the ordinary Apple sense:
they are shipped and linkable (the header lives in the SDK and the code is published
in XNU), but they are not part of Apple's documented frameworks, and the man pages in
the macOS man set cover `libproc`'s neighbours rather than `proc_pidinfo`'s ABI. The
consequences to accept: the `socket_info`/`un_sockinfo` layout must be mirrored
byte-exactly in `ctypes`, unknown or future `soi_kind` values must be skipped rather
than misread, and the buffer must be sized by the caller. Getting a size first is
documented practice in Apple's own code: `proc_listpidspath`, in the same
`libproc` directory, calls `proc_pidinfo(pid, PROC_PIDLISTFDS, 0, NULL, 0)` to size
the buffer, then calls it again with the real buffer, and treats a revoked fd as a
benign failure
([`proc_listpidspath.c`](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/proc_listpidspath.c)).
Note that `proc_listpidspath` — the one libproc call whose job sounds like ours — only
matches **vnodes** (`PROC_PIDFDVNODEINFO`) and process-region paths, so it does not
find socket paths; do not use it for this.

### 3. FreeBSD (and NetBSD/OpenBSD)

There is **no documented path-to-pid inverse lookup** on FreeBSD. Every documented
interface is keyed by pid, or enumerates everything:

- `sysctl(3)` documents the MIB `{CTL_KERN, KERN_PROC, KERN_PROC_FILEDESC, <pid>}`
  returning "`struct kinfo_file []`", where the fourth element is "`A process ID`"
  ([sysctl(3)](https://man.freebsd.org/cgi/man.cgi?query=sysctl&sektion=3));
  `#define KERN_PROC_FILEDESC 33` in
  [`sys/sys/sysctl.h`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/sys/sys/sysctl.h).
  `sysctl(8)` `-b` "Force[s] the value of the variable(s) to be output in raw,
  binary format" ([sysctl(8)](https://man.freebsd.org/cgi/man.cgi?query=sysctl&sektion=8)).
  The dotted spelling `kern.proc.filedesc.<pid>` is **UNVERIFIED** — only the MIB is
  documented.
- `struct kinfo_file` carries both what we need: a generic
  `char kf_path[PATH_MAX]; /* Path to file, if any. */` and, in the socket union,
  `kf_sa_local` ("Socket address", `struct sockaddr_storage`) plus
  `kf_sock_domain0`/`kf_sock_type0`/`kf_sock_protocol0`
  ([`sys/sys/user.h`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/sys/sys/user.h)).
  libprocstat consumes exactly those for a socket: `strlcpy(sock->dname, kif->kf_path, ...)`
  and `bcopy(&kif->kf_un.kf_sock.kf_sa_local, &sock->sa_local, ...)` in
  `procstat_get_socket_info_sysctl()`
  ([`lib/libprocstat/libprocstat.c`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/lib/libprocstat/libprocstat.c)).
  Records are variable-size — `kf_path` is truncated and `kf_structsize` records the
  packed length, "Variable size of record" — so a consumer must stride by
  `kf_structsize`, not by `sizeof(struct kinfo_file)` (see the packing in
  [`sys/kern/kern_descrip.c`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/sys/kern/kern_descrip.c)).
  I could not locate the kernel statement that assigns `kf_path` for a bound AF_LOCAL
  socket, but libprocstat's use of it for sockets is direct evidence that it is
  populated. **Partially verified.**
- `procstat -f <pid>`: "`-f` ... Display file descriptor information for the
  process", columns `PID COMM FD T V FLAGS REF OFFSET PRO NAME` where `T` "file
  descriptor type" includes `s` "socket" and `NAME` is documented as "file path or
  socket addresses (if available)"; AF_LOCAL protocols print as `UDS` (stream),
  `UDD` (datagram), `UDQ` (seqpacket). Documented caveat: "The display of open file
  or memory mapping pathnames is implemented using the kernel's name cache. If a file
  system does not use the name cache, or the path to a file is not in the cache, a
  path will not be displayed."
  ([procstat(1)](https://man.freebsd.org/cgi/man.cgi?query=procstat&sektion=1)). There
  is no path filter and no path-to-pid mode.
- **`sockstat -u` is the base-system answer and is better than lsof here**: `sockstat`
  "lists open Internet or Unix domain sockets", `-u` shows AF_LOCAL sockets, and the
  columns include `PID` ("The process ID of the command which holds the socket") and
  `LOCAL ADDRESS`, with "For bound Unix sockets, socket's filename is printed. For
  not bound Unix sockets, the field is empty." Peers print as `[PID FD]`
  ([sockstat(1)](https://man.freebsd.org/cgi/man.cgi?query=sockstat&sektion=1)).
  It needs no packages and no root for same-user sockets.
- `fstat` is the weaker option. Without `-s` its socket rows are kernel addresses,
  not paths: "For UNIX-domain sockets, its the address of the socket pcb and the
  address of the connected pcb (if connected)"; socket paths appear only with `-s`
  "Print socket endpoint information", and even then "For UNIX/local sockets either
  the local or remote address is shown, depending on which one is available." Its
  `NAME` column "Normally ... cannot be determined since there is no mapping from an
  open file back to the directory entry that was used to open that file"
  ([fstat(1)](https://man.freebsd.org/cgi/man.cgi?query=fstat&sektion=1)).
  Whether `fstat /path/to.sock` or `fuser /path/to.sock` match a bound socket is
  **UNVERIFIED**; neither manual documents socket-path matching.
- **`lsof` is not in the FreeBSD base system.** `usr.bin/lsof` and `usr.sbin/lsof`
  are absent from the source tree, and it is the port `sysutils/lsof`, currently
  `4.99.5,8`, installing `sbin/lsof`, marked "IGNORE: requires kernel sources (or
  set SRC_BASE)" ([FreshPorts sysutils/lsof](https://www.freshports.org/sysutils/lsof/)).
  When present, `lsof -a -U <path>` name-matches the same way it does on macOS
  ([lsof(8)](https://man.freebsd.org/cgi/man.cgi?query=lsof&sektion=8)).
- `libprocstat(3)` documents `procstat_getfiles(struct procstat *, struct kinfo_proc *kp,
  int mmapped)` and `procstat_get_socket_info(...)`; the shape is again pid-first
  ([libprocstat(3)](https://man.freebsd.org/cgi/man.cgi?query=libprocstat&sektion=3)).

NetBSD/OpenBSD, in brief:

- NetBSD `sockstat(1)` also prints `USER COMMAND PID FD PROTO LOCAL ADDRESS FOREIGN
  ADDRESS` with "For bound UNIX sockets, it is the socket's filename or `-`"
  ([sockstat(1)](https://man.netbsd.org/sockstat.1)). NetBSD `fstat(1)` states the
  design constraint outright: "Note that the `-f` option will not list UNIX domain
  sockets open in the file system, because the pathnames in the sockets may not be
  absolute and are not deterministic. To find all the UNIX domain sockets, use fstat
  to list all the sockets, and look for the ones that maybe belong in the file
  system" ([fstat(1)](https://man.netbsd.org/fstat.1)) — its SOCKETS section says the
  unix arm shows "the address of the socket pcb and the name of the file if
  available".
- OpenBSD `fstat(1)` has `-p pid` / `-u user` and a trailing `*` on the `FD` column
  meaning "the file is not an inode, but rather a socket, or there is an error"
  ([fstat(1)](https://man.openbsd.org/fstat.1)). OpenBSD `sockstat(1)` flags and
  columns were **not** verifiable (the man page body did not extract).

### 4. Pid liveness, zombies, and pid reuse without /proc

**`kill(pid, 0)`.** POSIX: "If sig is 0 (the null signal), error checking is performed
but no signal is actually sent. The null signal can be used to check the validity of
pid ... `[ESRCH]` No process or process group can be found corresponding to that
specified by pid ... `[EPERM]` The process does not have permission to send the signal
to any receiving process ... In order to prevent the existence or nonexistence of a
process from being used as a covert channel, such processes should appear nonexistent
to the sender; that is, `[ESRCH]` should be returned, rather than `[EPERM]`, if pid
refers only to such processes" ([kill(2), POSIX](https://pubs.opengroup.org/onlinepubs/9699919799/functions/kill.html)).
Therefore: success proves existence and signalability; `EPERM` proves existence;
`ESRCH` proves nothing exists *that you may know about*.

**A zombie passes that check, by design.** The same POSIX rationale: "Historical
implementations varied on the result of a kill() with pid indicating a zombie
process. Some indicated success on such a call (subject to permission checking),
while others gave an error of `[ESRCH]`. Since the definition of process lifetime in
this volume of POSIX.1-2017 covers zombie processes, the `[ESRCH]` error as described
is inappropriate in this case and implementations that give this error do not
conform." Linux's `kill(2)` says it plainly too: "`ESRCH` ... Note that an existing
process might be a zombie, a process that has terminated execution, but has not yet
been `wait(2)`ed for" ([kill(2), Linux](https://man7.org/linux/man-pages/man2/kill.2.html)).
So `kill(pid, 0)` alone cannot replace the current `/proc/<pid>/stat` zombie check.

**Zombie detection without /proc.**

- macOS and FreeBSD both expose state and start time in one `sysctl` result via
  `KERN_PROC_PID` (`KERN_PROC 14`, `KERN_PROC_PID 1` in
  [XNU `bsd/sys/sysctl.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/sysctl.h);
  `KERN_PROC_PID 1` in
  [FreeBSD `sys/sys/sysctl.h`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/sys/sys/sysctl.h)).
  - XNU `struct kinfo_proc` carries `struct extern_proc kp_proc`, whose
    `#define p_starttime p_un.__p_starttime` is "process start time" and whose
    `char p_stat; /* S* process status. */` is compared against
    `#define SZOMB 5 /* Awaiting collection by parent. */`
    ([`bsd/sys/proc.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc.h);
    `SRUN 2`, `SSLEEP 3`, `SSTOP 4` in the same header).
  - FreeBSD `struct kinfo_proc` has `struct timeval ki_start; /* starting time */`
    and `char ki_stat; /* S* process status */`
    ([`sys/sys/user.h`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/sys/sys/user.h)),
    with `#define SZOMB 5 /* Awaiting collection by parent. */`
    ([`sys/sys/proc.h`](https://raw.githubusercontent.com/freebsd/freebsd-src/main/sys/sys/proc.h)).
    The header warns: "Always verify that `sizeof(struct kinfo_proc) == KINFO_PROC_SIZE`
    on all platforms", so validate the returned length instead of assuming it.
  - Caveat for raw reads: XNU also defines 32/64-bit variants
    (`struct user32_kinfo_proc` / `user64_kinfo_proc`, with `user32_extern_proc` /
    `user64_extern_proc`), so the ctypes mirror must match the process architecture.
- Text fallback, if you would rather not mirror headers: `ps -o state=` (alias
  `stat`) and test the **first character**. All three platforms document `Z` as
  zombie, and only the first letter is comparable:
  - macOS: "`Z` Marks a dead process (a "zombie")"
    ([ps(1) macOS mirror](https://keith.github.io/xcode-man-pages/ps.1.html) — see
    *Could not verify*; the same legend lists `U` for uninterruptible wait, which
    FreeBSD and Linux do not use).
  - FreeBSD: "`Z` Marks a dead process (a "zombie")"
    ([ps(1) FreeBSD](https://man.freebsd.org/cgi/man.cgi?query=ps&sektion=1&manpath=FreeBSD+14.1-RELEASE)).
  - Linux: "`Z` defunct ("zombie") process, terminated but not reaped by its parent"
    ([ps(1) Linux](https://man7.org/linux/man-pages/man1/ps.1.html)).
  - Handle the empty result: BSD/macOS `ps` prints no row at all for a pid that does
    not exist, which is a "gone", not a "not a zombie".
- `waitpid(pid, WNOHANG)` is not a liveness probe for arbitrary pids: `[ECHILD] The
  process specified by pid does not exist or is not a child of the calling process`
  ([waitpid(2), POSIX](https://pubs.opengroup.org/onlinepubs/9699919799/functions/waitpid.html)).
  It is the correct way to reap *your own* worker children, which the current code
  already does.

**Pid reuse.** The start-time token is the guard. `ps -o lstart=` exists on macOS
("The exact time the command started, using the '%c' format described in
strftime(3)", keyword `lstart` / `time started`, macOS mirror above) and on FreeBSD
([ps(1) FreeBSD](https://man.freebsd.org/cgi/man.cgi?query=ps&sektion=1&manpath=FreeBSD+14.1-RELEASE)),
but `%c` is locale- and width-dependent, so treat it as an opaque string captured
once and re-compared, never as a parsed timestamp. Prefer `lstart` over `-o start=`,
whose documented format changes with the age of the process (FreeBSD documents three
different formats). `-o etimes=` is an integer second count on Linux and FreeBSD but
is absent from the macOS `ps`; `ki_start`/`p_starttime` avoid all of this.

### 5. Concrete recommendation for this client

Python 3.11 stdlib only, plus tools shipped with the OS:

```python
# identity, strongest first
if sys.platform == "linux":        # keep today's logic
    owner = proc_net_unix_owner(socket_path)          # /proc/net/unix <-> /proc/*/fd
elif sys.platform == "darwin":
    owner = libproc_unix_owner(socket_path)           # ctypes, PROC_PIDLISTFDS / PROC_PIDFDSOCKETINFO
    if owner is None:
        owner = lsof_owner(socket_path)               # lsof -a -U -F pcfn <path>
else:                              # freebsd, netbsd, openbsd
    owner = sockstat_owner(socket_path)               # sockstat -u, match LOCAL ADDRESS
    if owner is None:
        owner = lsof_owner(socket_path)               # only if installed (port/package)
if owner is None:
    owner = command_line_owner(socket_path)           # ps -ww -Ao pid=,command= (weak)
```

and, before signalling any pid:

```python
def same_live_process(pid, recorded_token):
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass                                  # EPERM proves the pid exists
    except ProcessLookupError:
        return False                          # ESRCH
    now = start_token(pid)                    # stat field 22 | p_starttime | ki_start | ps -o lstart= (string)
    if now is None or now != recorded_token:
        return False                          # gone, or the pid was reused
    return not is_zombie(pid)                 # stat field 3 == 'Z' | p_stat/ki_stat == 5 | ps -o state= first char
```

Failure modes to expect, per source:

| Source | Fails when | Symptom |
| --- | --- | --- |
| `/proc/net/unix` + `/proc/<pid>/fd` | no `/proc` (macOS, some BSDs); sandboxed or containerised `/proc`; `hidepid=1` or `hidepid=2` on the mount | no owners found; the code already falls through to `ps` |
| `hidepid` | `proc(5)`: mode 1 makes `/proc/pid/cmdline` and .../status unreadable to other users, mode 2 makes other users' `/proc/pid` directories "invisible" | other users' processes look absent; harmless for a same-user worker, misleading for diagnosis |
| libproc (macOS) | another uid (EPERM by policy); struct/ABI drift; unknown `soi_kind` | EPERM, or short read — skip, do not guess |
| `lsof` | not installed (FreeBSD base has no lsof); PATH differs (`/usr/sbin/lsof` on macOS); name-matching "usually" applies; output dialect differs | empty output; must not be treated as "no owner" without a second source |
| `sockstat -u` | non-FreeBSD/NetBSD systems; unbound sockets print an empty filename | missing row for the socket |
| `ps -o state=` / `lstart=` | BSD `ps` truncates command output by default (the current code's `-ww` is required); empty row for a nonexistent pid; `lstart` is locale-formatted | empty or unexpected text |
| `sysctl KERN_PROC_PID` | struct layout/mirror errors; FreeBSD's `KINFO_PROC_SIZE` check; XNU 32/64-bit variants | `ESRCH` for a live pid, or garbage fields |
| `kill(pid, 0)` | hidden processes (`hidepid`) report `ESRCH`; `EPERM` is existence, not liveness; zombies pass | false "gone" / false "alive" |

### What behaves differently on macOS in a way that matters

- **The exact path never runs.** `_proc_available()` is false on macOS, so
  `_socket_owners()` is never called and every answer comes from the command-line
  matcher. The docstring calls that weaker; on macOS it is not a fallback, it is the
  only claim available today. Adding libproc (or `lsof`) is what restores the strong
  claim there.
- **A zombie worker looks "gone" through the socket.** On Linux a zombie keeps its
  `/proc/<pid>/fd` entries, so the socket-owner scan still finds it and the state
  check is what stops the signal. On macOS a dead-and-unreaped worker holds no fds,
  so the libproc scan finds nothing and only the pid/liveness path sees it. The two
  checks are therefore not interchangeable across platforms: on macOS the recorded
  pid (with its start-time token) is the only handle on a worker that died before
  being reaped.
- **`ps` needs `-ww` on BSD/macOS** (already handled) because BSD `ps` truncates the
  command to the terminal width by default; on Linux that is not a hazard, so the
  same code path carries a platform-specific bug risk if `-ww` is ever dropped.
- **`ps -o state=` is not comparable beyond the first letter** (`U` on macOS means
  uninterruptible wait; `D`/`I`/etc. differ), and it emits nothing for a missing pid.
- **`lsof` output is not a stable text format across platforms** (`NAME`
  decorations differ; `TID` is a macOS column), which is the argument for `-F`
  fields if lsof becomes a source.

## Could not verify

- **No macOS or BSD host was available.** Every macOS/BSD statement here comes from
  the kernel/man-page sources cited; none was executed. Linux statements were checked
  locally where marked.
- **A primary Apple HTML source for macOS `ps(1)`.** Apple's archived
  `developer.apple.com/.../man1/ps.1.html` returned 404; the citation used is the
  `keith.github.io/xcode-man-pages` mirror of the Apple page. The same limitation
  applies to the macOS `nettop(1)` citation (`manp.gs`).
- **`/usr/sbin/lsof` presence on a fresh macOS install** is inferred from the macOS
  man page set, Apple's published lsof source, and Homebrew's keg-only note; Apple
  publishes no file manifest I could cite.
- **The exact macOS `lsof` rendering of a bound unix socket's `NAME`**, and whether
  `lsof`'s "usually" name match has documented exceptions beyond the Linux case.
- **The kernel statement that sets FreeBSD `kinfo_file.kf_path` for a bound AF_LOCAL
  socket** (its consumers in libprocstat are direct evidence, but the assignment was
  not located).
- **The dotted sysctl spellings** `kern.proc.filedesc.<pid>` (FreeBSD) and
  `kern.proc.pid` (macOS): the MIBs and structs are verified, the dotted CLI names
  were not found in the man pages read.
- **Whether FreeBSD `fstat <socket path>` or `fuser <socket path>` match a bound
  socket**; and OpenBSD `sockstat` flags/columns (man page body did not extract).
- **XNU's own statement about procfs removal** — the absence claim rests on the
  source trees checked (main plus two release tags), not on an Apple changelog.
- **`proc_pidinfo`/`proc_pidfdinfo` ABI guarantees**: the headers are in the SDK and
  the kernel source is public, but no Apple document promises the `socket_info`
  layout is stable across releases. Treat it as stable-in-practice, not contractual.
- **Timing**: the check-then-`kill` window is unavoidable without `pidfd_open`
  (Linux-only, non-POSIX). Comparing the start token immediately before signalling is
  the best available mitigation in the standard library.
