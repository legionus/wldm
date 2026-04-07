# Architecture

This document describes the runtime split between the main `wldm` processes and
the responsibilities of each one.

For configuration options and examples, see
[`configuration.md`](configuration.md).

## Overview

`wldm` is intentionally split into multiple processes instead of running the
whole login flow in one privileged address space.

The main pieces are:

- `wldm` daemon: root-owned supervisor and source of truth
- `wldm pam-worker`: blocking PAM authentication worker for one greeter
  conversation
- `wldm greeter-session`: PAM-backed launcher and supervisor for the greeter
  compositor
- `wldm greeter`: GTK login UI
- `wldm user-session`: PAM-backed launcher for the selected user session
- `wldm dbus-adapter`: optional unprivileged bridge from daemon state to
  `org.freedesktop.DisplayManager`

## Process Model

Typical runtime layout:

```text
systemd
└─ wldm                 (root daemon)
   ├─ wldm pam-worker
   └─ wldm greeter-session
      └─ cage
         └─ wldm greeter
```

When D-Bus integration is enabled, the daemon also starts the adapter:

```text
systemd
└─ wldm                 (root daemon)
   ├─ wldm pam-worker
   ├─ wldm greeter-session
   │  └─ cage
   │     └─ wldm greeter
   └─ wldm dbus-adapter
```

After successful authentication, the daemon also starts a user session:

```text
systemd
└─ wldm                 (root daemon)
   ├─ wldm greeter-session
   │  └─ cage
   │     └─ wldm greeter
   ├─ wldm dbus-adapter
   └─ wldm user-session
      └─ user program / shell / compositor
```

## Responsibilities

### Daemon

The daemon in [`src/wldm/daemon.py`](../src/wldm/daemon.py):

- reads configuration
- opens and switches virtual terminals
- brokers greeter authentication conversations through `pam-worker`
- starts greeter, user-session, and optional D-Bus adapter subprocesses
- supervises child processes and restart limits
- exposes a small read-only state snapshot to internal clients
- handles power actions such as reboot and poweroff

The daemon is the only process expected to run as `root`.

### PAM Worker

[`src/wldm/pam_worker.py`](../src/wldm/pam_worker.py) is a small privileged
helper dedicated to one in-progress greeter authentication attempt.
Its private daemon-facing wire contract is documented in
[`pam-worker-protocol.md`](pam-worker-protocol.md).

It is responsible for:

- starting a PAM authentication transaction for the requested username
- setting PAM items such as `PAM_TTY`
- translating PAM conversation callbacks into greeter prompt styles
- blocking inside the PAM callback until the daemon forwards the next greeter
  answer
- reporting `prompt`, `ready`, or `failed` results back to the daemon

The worker does not open user sessions, does not switch VTs, and does not
launch user programs. It only owns the blocking PAM authentication flow.

### Greeter Session Wrapper

[`src/wldm/greeter_session.py`](../src/wldm/greeter_session.py) exists to keep
the greeter compositor out of the root daemon while still creating a real
PAM/logind session for the greeter user.

It is responsible for:

- switching to the configured greeter TTY
- calling `setsid()` and acquiring the controlling TTY
- opening a PAM session for the greeter user
- setting session metadata such as `XDG_SESSION_TYPE`, `XDG_SEAT`, and
  `XDG_VTNR`
- forking the final compositor/greeter child
- keeping the greeter PAM session open until that child exits
- dropping privileges to the greeter user before the final `execve()` path

### Greeter UI

[`src/wldm/greeter.py`](../src/wldm/greeter.py) is an unprivileged GTK
application running inside the greeter compositor. It does not perform
authentication itself.

It is responsible for:

- rendering the login UI
- enumerating available Wayland sessions from `/usr/share/wayland-sessions`
- optionally extending that list with `~/.local/share/wayland-sessions` for the
  username currently typed into the greeter
- loading and saving remembered greeter state such as the last successful
  username and session command through `WLDM_STATE_FILE`
- sending structured requests to the daemon over an inherited IPC socket fd
- reacting to daemon events such as `session-starting` and `session-finished`

The greeter does not execute anything from these entries before login. It only
uses them to populate the session picker and sends the chosen command back to
the daemon after successful authentication.

### User Session Wrapper

[`src/wldm/user_session.py`](../src/wldm/user_session.py) creates the final
user session. Like the greeter wrapper, it performs session setup before
`execve()`-ing the selected user program.

It is responsible for:

- opening a free TTY for the user session
- calling `setsid()` and acquiring the controlling TTY
- opening the user's PAM session
- applying PAM environment variables
- optionally running the configured session startup wrapper
- dropping privileges to the target user
- starting the selected shell, compositor, or session command

### D-Bus Adapter

[`src/wldm/dbus_adapter.py`](../src/wldm/dbus_adapter.py) is an optional
unprivileged helper. The daemon starts it only when `[dbus].enabled = yes`.

It is responsible for:

- connecting to the daemon over an inherited IPC fd
- fetching the initial daemon state snapshot with `get-state`
- consuming daemon state-change events
- exporting a small read-only `org.freedesktop.DisplayManager` object tree on
  the system bus

The adapter is not part of the login-critical path. If it fails to start or
loses the bus name, the daemon keeps running and login still works.

## IPC

The daemon and its internal clients communicate over inherited `socketpair()`
file descriptors. The protocol is implemented in
[`src/wldm/greeter_protocol.py`](../src/wldm/greeter_protocol.py) and documented in
[`protocol.md`](protocol.md).

Properties of the current transport:

- the daemon creates one private connected socket pair per internal client
- the client end is inherited through `WLDM_SOCKET_FD`
- there is no pathname listener for the greeter or D-Bus adapter path
- messages use a small length-prefixed binary frame format
- the protocol supports request/response messages and asynchronous events

Current actions include:

- `create-session`
- `continue-session`
- `cancel-session`
- `start-session`
- `get-state`
- `poweroff`
- `reboot`
- `suspend`
- `hibernate`

Current events include:

- `session-starting`
- `session-finished`
- `state-changed`

The read-only `get-state` / `state-changed` surface exists so auxiliary
internal clients such as the D-Bus adapter can observe daemon state without
depending on greeter-specific request flow.

## Security Split

The design tries to minimize how much code runs with full privileges:

- root-only work stays in the daemon and session wrappers
- the visible greeter UI runs as the greeter user
- the final desktop session runs as the target user
- optional D-Bus integration runs in a separate unprivileged adapter process
- the greeter never talks to PAM directly for authentication

This split is important because PAM, TTY switching, seat control, and power
actions are privileged operations, while the UI, compositor, and D-Bus glue
should stay as unprivileged as practical.

## Shutdown Model

The daemon installs signal handlers for `SIGTERM` and `SIGINT`. On shutdown it:

- closes active internal client channels
- terminates the greeter process group
- terminates the optional D-Bus adapter if it is running
- terminates active user-session wrappers it started
- closes console file descriptors

This is what allows `systemctl stop wldm.service` to tear down the display
manager cleanly instead of leaving `cage`, adapter, or user-session processes
behind.

## Why systemd Matters

`wldm` should be started as a `systemd` service, not from an already active
interactive shell session. `systemd-logind` uses real service and session
ownership, not just environment variables, when deciding which process may take
control of a seat.

For development, [`systemd-wldm.sh`](../systemd-wldm.sh) exists to reproduce
the service-style launch model from the source tree.
