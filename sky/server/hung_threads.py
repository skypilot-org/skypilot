"""Describe a thread that has been running for too long.

The on-demand thread executors (``sky/server/requests/threads.py``) hand each
task its own thread and count it against a per-process limit. A task that
never returns keeps its slot forever, and once every slot is held that way
the executor rejects everything. When that happens the useful question is
"what are those threads doing?", and the answer has to come from inside the
process: a production pod rarely has py-spy or ``ss``, and by the time
someone could attach a tool the worker has usually been recycled.

``describe`` builds that answer from what the interpreter and the kernel
already expose, with no extra tooling:

- the Python stack of the thread from ``sys._current_frames()``. This is the
  frame the thread will resume in, so for a thread parked in a C call it names
  the call (``cursor.execute``, ``connection.commit``, ``socket.recv``).
- the kernel's view of the thread from ``/proc/self/task/<tid>/``: scheduler
  state, ``wchan`` and, where the kernel exposes it, the syscall the thread
  sleeps in with its arguments. For ``poll``/``ppoll`` the ``pollfd`` array is
  read back from our own address space through ``/proc/self/mem`` (a stale
  pointer then reads as an error instead of a segfault) so the dump names the
  file descriptor being waited on and whether the wait has a timeout.
- what that descriptor is *now* (``/proc/self/fd``) and, for a socket, its
  peer, TCP state and receive/send queue lengths. These come from a
  ``dup`` of the descriptor via ``getsockopt(TCP_INFO)``, ``getpeername`` and
  ``ioctl(FIONREAD)``: constant cost per descriptor, no parsing of
  ``/proc/net/tcp`` (which grows with every TIME_WAIT entry in the network
  namespace and can take tens of milliseconds to read on a busy pod).

``scan_ownerless_sockets`` answers a second question that the per-thread view
cannot: is there a connection in this network namespace that no process owns
any more? A descriptor closed under a sleeping ``poll`` leaves exactly that
behind -- the poll keeps the kernel socket alive, the number gets reused by
whatever is allocated next, and the thread's dump shows the *new* owner of
the number (often a perfectly healthy-looking socket of the same kind) while
the real connection sits established with unread data and no descriptor
pointing at it. The scan lists such sockets, restricted to peers that some
process here is also connected to or that the caller names, minus peers the
caller excludes, so a sidecar's own connections in a shared namespace do not
show up. It parses ``/proc/net/tcp`` and walks every readable
``/proc/<pid>/fd``, so callers rate-limit it well below the per-thread dump.

Everything here is best effort: any step may fail on a non-Linux host, a
hardened container, or a thread that resumed between two reads, and a
failure only shortens the description. Nothing raises to the caller.
"""
import fcntl
import os
import platform
import socket
import struct
import sys
import threading
import time
import traceback
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Syscall numbers for the waits worth naming. Anything else is reported by
# number. Only the two architectures we ship containers for.
_SYSCALLS_X86_64 = {
    0: 'read',
    1: 'write',
    3: 'close',
    7: 'poll',
    23: 'select',
    35: 'nanosleep',
    42: 'connect',
    44: 'sendto',
    45: 'recvfrom',
    47: 'recvmsg',
    202: 'futex',
    219: 'restart_syscall',
    230: 'clock_nanosleep',
    232: 'epoll_wait',
    270: 'pselect6',
    271: 'ppoll',
    281: 'epoll_pwait',
}
_SYSCALLS_AARCH64 = {
    22: 'epoll_pwait',
    57: 'close',
    63: 'read',
    64: 'write',
    72: 'pselect6',
    73: 'ppoll',
    98: 'futex',
    101: 'nanosleep',
    115: 'clock_nanosleep',
    128: 'restart_syscall',
    203: 'connect',
    206: 'sendto',
    207: 'recvfrom',
    212: 'recvmsg',
}
_SYSCALLS = {
    'x86_64': _SYSCALLS_X86_64,
    'aarch64': _SYSCALLS_AARCH64,
}.get(platform.machine(), {})
# Syscalls whose first argument is a file descriptor.
_FD_ARG0 = frozenset({
    'read', 'write', 'close', 'connect', 'sendto', 'recvfrom', 'recvmsg',
    'epoll_wait', 'epoll_pwait'
})
_TCP_STATES = {
    1: 'ESTABLISHED',
    2: 'SYN_SENT',
    3: 'SYN_RECV',
    4: 'FIN_WAIT1',
    5: 'FIN_WAIT2',
    6: 'TIME_WAIT',
    7: 'CLOSE',
    8: 'CLOSE_WAIT',
    9: 'LAST_ACK',
    10: 'LISTEN',
    11: 'CLOSING',
}
# Bounds so one description stays small: pollfd entries decoded, frames kept.
_MAX_POLLFDS = 8
_MAX_FRAMES = 16
_POLLFD_SIZE = 8  # struct pollfd: int fd; short events; short revents
# Sockets reported by one scan, and the time budget it may spend walking
# /proc/<pid>/fd (a container with hundreds of processes takes tens of ms).
_MAX_SCAN_RESULTS = 16
_SCAN_TIME_BUDGET_SECONDS = 2.0
# Linux ioctl numbers; SIOCINQ/SIOCOUTQ on sockets are FIONREAD/TIOCOUTQ.
_FIONREAD = 0x541B
_TIOCOUTQ = 0x5411


def _read(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_own_memory(addr: int, size: int) -> Optional[bytes]:
    """Read ``size`` bytes at ``addr`` in this process via /proc/self/mem.

    Deliberately not ctypes: the address comes from another thread's
    in-progress syscall and may be gone by the time we read; a bad address
    here is an OSError, a bad ctypes read is a segfault.
    """
    try:
        fd = os.open('/proc/self/mem', os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.pread(fd, size, addr)
    except (OSError, OverflowError):
        return None
    finally:
        os.close(fd)


def _format_addr(addr: Any) -> str:
    """Render a sockaddr tuple (or unix path) as host:port."""
    if isinstance(addr, tuple) and len(addr) >= 2:
        host, port = addr[0], addr[1]
        if ':' in str(host):
            return f'[{host}]:{port}'
        return f'{host}:{port}'
    if isinstance(addr, (str, bytes)):
        return repr(addr) if addr else '(unnamed)'
    return repr(addr)


def _queue_length(fd: int, request: int) -> Optional[int]:
    try:
        buf = fcntl.ioctl(fd, request, struct.pack('i', 0))
        return struct.unpack('i', buf)[0]
    except OSError:
        return None


def socket_info(fd: int) -> Optional[str]:
    """Peer, state and queue lengths of the socket at ``fd``, or None.

    Works on a ``dup`` of the descriptor so the socket object created here
    never closes the caller's number. Constant cost; nothing is parsed.
    Returns None when ``fd`` is not a socket (or cannot be inspected).
    """
    try:
        dup = os.dup(fd)
    except OSError:
        return None
    try:
        try:
            sock = socket.socket(fileno=dup)
        except OSError:
            # Not a socket (ENOTSOCK) or not inspectable. The constructor
            # failed before taking ownership, so the dup is still ours.
            os.close(dup)
            return None
        with sock:
            parts: List[str] = []
            if sock.family == socket.AF_INET:
                parts.append('tcp' if sock.type == socket.
                             SOCK_STREAM else f'inet type={int(sock.type)}')
            elif sock.family == socket.AF_INET6:
                parts.append('tcp6' if sock.type == socket.
                             SOCK_STREAM else f'inet6 type={int(sock.type)}')
            elif sock.family == socket.AF_UNIX:
                parts.append('unix')
            else:
                parts.append(f'family={int(sock.family)}')
            try:
                parts.append(_format_addr(sock.getsockname()))
            except OSError:
                parts.append('?')
            try:
                parts.append('-> ' + _format_addr(sock.getpeername()))
            except OSError:
                parts.append('(not connected)')
            if (sock.family in (socket.AF_INET, socket.AF_INET6) and
                    sock.type == socket.SOCK_STREAM):
                try:
                    info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO,
                                           8)
                    state = info[0]
                    parts.append(_TCP_STATES.get(state, f'state={state}'))
                except OSError:
                    pass
            recv_q = _queue_length(dup, _FIONREAD)
            send_q = _queue_length(dup, _TIOCOUTQ)
            if recv_q is not None:
                parts.append(f'recv-q={recv_q}')
            if send_q is not None:
                parts.append(f'send-q={send_q}')
            return ' '.join(parts)
    except Exception:  # pylint: disable=broad-except
        return None


def describe_fd(fd: int) -> str:
    """What descriptor ``fd`` is right now, e.g. 'fd 12: socket:[123] tcp
    127.0.0.1:5432 -> 127.0.0.1:6432 ESTABLISHED recv-q=0 send-q=0' or
    'fd 12: not open in this process (Bad file descriptor)'."""
    try:
        target = os.readlink(f'/proc/self/fd/{fd}')
    except OSError as e:
        return f'fd {fd}: not open in this process ({e.strerror})'
    if target.startswith('socket:['):
        info = socket_info(fd)
        if info is not None:
            return f'fd {fd}: {target} {info}'
    return f'fd {fd}: {target}'


def kernel_view(native_tid: int) -> Dict[str, Any]:
    """Scheduler state, wchan and current syscall (with fds) of a thread."""
    base = f'/proc/self/task/{native_tid}'
    view: Dict[str, Any] = {}
    stat = _read(f'{base}/stat')
    if stat:
        # The state letter follows the parenthesised comm, which may itself
        # contain spaces or parentheses.
        end = stat.rfind(')')
        if 0 <= end and end + 2 < len(stat):
            view['state'] = stat[end + 2]
    view['wchan'] = _read(f'{base}/wchan')
    raw = _read(f'{base}/syscall')
    if not raw:
        return view
    parts = raw.split()
    try:
        nr = int(parts[0])
    except ValueError:
        # 'running', or '-1 <sp> <pc>' for a thread blocked outside a syscall.
        view['syscall'] = parts[0]
        return view
    name = _SYSCALLS.get(nr, f'syscall_{nr}')
    view['syscall'] = name
    try:
        args = [int(x, 16) for x in parts[1:7]]
    except ValueError:
        return view
    if len(args) < 3:
        return view
    fds: List[int] = []
    if name in _FD_ARG0:
        fds.append(args[0])
    elif name in ('poll', 'ppoll'):
        addr, nfds = args[0], args[1]
        if name == 'poll':
            timeout = args[2]
            # poll(2) takes an int; -1 shows up as 0xffffffff (or
            # sign-extended to 64 bits, depending on the kernel).
            if timeout in (0xffffffff, 0xffffffffffffffff):
                view['timeout'] = 'infinite'
            else:
                view['timeout'] = f'{timeout} ms'
        else:
            view['timeout'] = 'infinite' if args[2] == 0 else 'set'
        n = min(nfds, _MAX_POLLFDS)
        buf = _read_own_memory(addr, _POLLFD_SIZE * n) if n > 0 else None
        if buf is not None and len(buf) == _POLLFD_SIZE * n:
            entries = []
            for i in range(n):
                fd, events, revents = struct.unpack_from(
                    'ihh', buf, _POLLFD_SIZE * i)
                fds.append(fd)
                entries.append(f'fd={fd} events={events:#x} '
                               f'revents={revents:#x}')
            view['pollfds'] = entries
        else:
            view['pollfds'] = f'{nfds} entries, unreadable'
    view['fds'] = fds
    return view


def python_stack(thread: threading.Thread) -> List[str]:
    """Innermost ``_MAX_FRAMES`` frames of ``thread`` as 'file:line name'."""
    frame = None
    if thread.ident is not None:
        frame = sys._current_frames().get(thread.ident)  # pylint: disable=protected-access
    if frame is None:
        return ['<thread has no frame; it is not running>']
    frames: List[traceback.FrameSummary] = list(traceback.extract_stack(frame))
    dropped = len(frames) - _MAX_FRAMES
    lines = []
    if dropped > 0:
        lines.append(f'... {dropped} outer frame(s) elided')
        frames = frames[dropped:]
    for fs in frames:
        lines.append(f'{fs.filename}:{fs.lineno} {fs.name}')
    return lines


def describe(thread: threading.Thread,
             age_seconds: float,
             task: str = '',
             deadline_seconds: Optional[float] = None) -> str:
    """Multi-line description of a long-running thread.

    The frame and the syscall say what kind of wait this is; they do not say
    whether this thread is the one holding everyone else up. A thread that
    blocks others (a connection whose reply was never read, say) and the
    threads waiting on it can sit in the very same frame and syscall. Age
    orders them: the blocker is necessarily older than the threads it
    blocks, which is why callers describe threads oldest first.
    """
    header = (f'thread {thread.name!r} native_tid={thread.native_id} '
              f'running {age_seconds:.1f}s')
    if deadline_seconds is not None:
        header += f' (limit {deadline_seconds:g}s)'
    if task:
        header += f' task={task}'
    lines = [header]
    try:
        kv = kernel_view(thread.native_id) if thread.native_id else {}
        kernel = (f'  kernel: state={kv.get("state")} '
                  f'wchan={kv.get("wchan")} syscall={kv.get("syscall")}')
        if 'timeout' in kv:
            kernel += f' timeout={kv["timeout"]}'
        if 'pollfds' in kv:
            kernel += f' pollfds={kv["pollfds"]}'
        lines.append(kernel)
        for fd in kv.get('fds') or []:
            lines.append('  ' + describe_fd(fd))
    except Exception as e:  # pylint: disable=broad-except
        lines.append(f'  kernel view unavailable: {e!r}')
    try:
        lines.append('  python stack (innermost last):')
        lines.extend('    ' + l for l in python_stack(thread))
    except Exception as e:  # pylint: disable=broad-except
        lines.append(f'  python stack unavailable: {e!r}')
    return '\n'.join(lines)


def _hex_addr(hexaddr: str) -> str:
    """Decode a /proc/net/tcp address ('0100007F:1920' -> '127.0.0.1:6432')."""
    try:
        ip, port = hexaddr.split(':')
        if len(ip) == 8:
            octets = bytes.fromhex(ip)[::-1]
            return f'{".".join(str(b) for b in octets)}:{int(port, 16)}'
        if len(ip) == 32:
            # Four little-endian 32-bit words.
            raw = b''.join(
                bytes.fromhex(ip[i:i + 8])[::-1] for i in range(0, 32, 8))
            return f'[{socket.inet_ntop(socket.AF_INET6, raw)}]:{int(port, 16)}'
        return hexaddr
    except (ValueError, OSError):
        return hexaddr


def _tcp_rows() -> List[Tuple[str, str, str, str, str]]:
    """(local, remote, state, queues, inode) hex fields of every ESTABLISHED
    or CLOSE_WAIT row in /proc/net/tcp{,6}. Fields stay undecoded; only the
    few rows that get reported are decoded.

    The kernel serves these files a page at a time and does not freeze the
    table between pages, so a row can appear twice (or be missed) while
    connections churn; rows are de-duplicated by inode. TIME_WAIT rows --
    the bulk of the file on a host that opens and closes many connections --
    are skipped before the split.
    """
    rows: List[Tuple[str, str, str, str, str]] = []
    seen: Set[str] = set()
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        text = _read(path)
        if not text:
            continue
        for line in text.splitlines()[1:]:
            if ' 01 ' not in line and ' 08 ' not in line:
                continue
            parts = line.split()
            if len(parts) < 10:
                continue
            state, inode = parts[3], parts[9]
            if state not in ('01', '08') or inode in seen:
                continue
            seen.add(inode)
            rows.append((parts[1], parts[2], state, parts[4], inode))
    return rows


def _socket_inodes_by_process(
        deadline: float) -> Tuple[Set[str], int, int, int, bool]:
    """Socket inodes owned by every process whose /proc/<pid>/fd we can read.

    Returns (inodes, readable processes, unreadable processes, descriptors
    looked at, whether the walk finished within the deadline).
    """
    owned: Set[str] = set()
    readable = unreadable = nfds = 0
    complete = True
    try:
        pids = [p for p in os.listdir('/proc') if p.isdigit()]
    except OSError:
        return owned, 0, 0, 0, False
    for pid in pids:
        if time.monotonic() > deadline:
            complete = False
            break
        try:
            fds = os.listdir(f'/proc/{pid}/fd')
        except OSError:
            unreadable += 1
            continue
        readable += 1
        for fd in fds:
            nfds += 1
            try:
                target = os.readlink(f'/proc/{pid}/fd/{fd}')
            except OSError:
                continue
            if target.startswith('socket:['):
                owned.add(target[8:-1])
    return owned, readable, unreadable, nfds, complete


def _peer_hex_forms(peers: Iterable[Tuple[str, int]]) -> Set[str]:
    """/proc/net/tcp 'rem_address' forms of (host, port) peers; hostnames are
    resolved, every address of a name is included."""
    out: Set[str] = set()
    for host, port in peers:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except (OSError, TypeError, ValueError):
            continue
        for family, _, _, _, sockaddr in infos:
            try:
                raw = socket.inet_pton(family, str(sockaddr[0]))
            except (OSError, ValueError):
                continue
            # Little-endian 32-bit words, upper-case hex, like the kernel.
            words = [
                raw[i:i + 4][::-1].hex().upper() for i in range(0, len(raw), 4)
            ]
            out.add(f'{"".join(words)}:{port:04X}')
            if family == socket.AF_INET:
                # An IPv4 peer of an IPv6 socket appears in /proc/net/tcp6
                # as a v4-mapped address.
                mapped = ('0000000000000000' + 'FFFF0000' + words[0])
                out.add(f'{mapped}:{port:04X}')
    return out


def _recv_q(row: Tuple[str, str, str, str, str]) -> int:
    """Unread bytes of a /proc/net/tcp row ('tx_queue:rx_queue' hex)."""
    try:
        return int(row[3].split(':')[1], 16)
    except (IndexError, ValueError):
        return -1


def scan_ownerless_sockets(
        extra_peers: Iterable[Tuple[str, int]] = (),
        exclude_peers: Iterable[Tuple[str, int]] = (),
) -> List[str]:
    """List established TCP connections in this network namespace that no
    process we can see owns, restricted to peers some visible process is
    also connected to, plus ``extra_peers`` (host, port) the caller knows it
    talks to -- typically the database, whose orphaned connections are the
    ones that hold locks. Without ``extra_peers`` an orphan to a peer nobody
    happens to be connected to at scan time goes unreported.
    ``exclude_peers`` are left out even when a visible process is connected
    to them: the database behind a sidecar pooler is one, because the
    pooler's own connections to it come from another process namespace and
    every one of them would look ownerless here.

    A descriptor number closed while another thread sleeps in ``poll`` on it
    leaves the kernel socket alive with no descriptor pointing at it. The
    per-thread dump cannot show that socket (it shows whatever now holds the
    number), so this is the only in-process evidence of that failure, and
    its ``recv-q`` tells whether the peer's reply arrived and was never read.

    Restricting to known peers keeps another container's connections (a
    sidecar in a shared pod namespace) out of the list, unless it talks to
    the same peer. Processes whose
    ``/proc/<pid>/fd`` is unreadable (another user) would also make their
    sockets look ownerless; the summary line counts them so a reader can
    weigh the result.

    Costs one read of ``/proc/net/tcp`` plus a walk of every readable
    ``/proc/<pid>/fd`` -- tens to a few hundred milliseconds on a busy pod --
    so callers keep it rare. Returns log lines; the first is a summary, then
    at most _MAX_SCAN_RESULTS connections, the ones with the most unread
    bytes first.
    """
    started = time.monotonic()
    try:
        rows = _tcp_rows()
        owned, readable, unreadable, nfds, complete = (
            _socket_inodes_by_process(started + _SCAN_TIME_BUDGET_SECONDS))
        peers = {rem for _, rem, _, _, ino in rows if ino in owned}
        peers |= _peer_hex_forms(extra_peers)
        peers -= _peer_hex_forms(exclude_peers)
        suspects = [
            row for row in rows
            if row[4] != '0' and row[4] not in owned and row[1] in peers
        ]
        # An orphan whose peer answered and nobody read the reply is the one
        # that holds a lock; other ownerless connections (another container
        # in the pod, a process whose descriptors we cannot read) must not
        # push it past the report limit. Stable, so ties keep table order.
        suspects.sort(key=_recv_q, reverse=True)
        elapsed_ms = (time.monotonic() - started) * 1000
        summary = (f'ownerless sockets: {len(suspects)} established/'
                   f'close-wait connection(s) to a peer this host talks to '
                   f'have no owner among {readable} readable process(es) '
                   f'({unreadable} unreadable, {nfds} descriptors, '
                   f'{len(rows)} connections, {elapsed_ms:.0f} ms')
        if not complete:
            summary += ', walk cut short by time budget'
        summary += ')'
        lines = [summary]
        for local, rem, state, queues, ino in suspects[:_MAX_SCAN_RESULTS]:
            try:
                tx_q, rx_q = (int(x, 16) for x in queues.split(':'))
            except ValueError:
                tx_q = rx_q = -1
            state_name = _TCP_STATES.get(int(state, 16), state)
            lines.append(f'  socket:[{ino}] {_hex_addr(local)} -> '
                         f'{_hex_addr(rem)} {state_name} recv-q={rx_q} '
                         f'send-q={tx_q} owner=none')
        if len(suspects) > _MAX_SCAN_RESULTS:
            lines.append(f'  ... {len(suspects) - _MAX_SCAN_RESULTS} more')
        return lines
    except Exception as e:  # pylint: disable=broad-except
        return [f'ownerless sockets: scan failed ({e!r})']
