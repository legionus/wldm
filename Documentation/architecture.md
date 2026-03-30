# Architecture

This document describes the runtime split between the main `wldm` processes and
the responsibilities of each one.

For configuration options and examples, see
[`configuration.md`](configuration.md).

## Overview

`wldm` is intentionally split into multiple processes instead of running the
whole login flow in one privileged address space.

The main pieces are:

- `wldm` daemon: root-owned supervisor
- `wldm greeter-session`: PAM-backed launcher for the greeter compositor
- `wldm greeter`: GTK login UI
- `wldm session`: PAM-backed launcher for the selected user session

## Process Model

Typical runtime layout:

```text
systemd
└─ wldm                 (root daemon)
   └─ wldm greeter-session
      └─ cage
         └─ wldm greeter
```

After successful authentication, the daemon also starts a user session:

```text
systemd
└─ wldm                 (root daemon)
   ├─ wldm greeter-session
   │  └─ cage
   │     └─ wldm greeter
   └─ wldm session
      └─ user program / shell / compositor
```

## Responsibilities

### Daemon

The daemon in [`src/wldm/daemon.py`](../src/wldm/daemon.py):

- reads configuration
- opens and switches virtual terminals
- creates the greeter UNIX socket
- verifies greeter peer credentials with `SO_PEERCRED`
- authenticates users through PAM
- starts greeter and user session wrappers
- supervises child processes and restart limits
- handles power actions such as reboot and poweroff

The daemon is the only process expected to run as `root`.

### Greeter Session Wrapper

[`src/wldm/greeter_session.py`](../src/wldm/greeter_session.py) exists to keep
the greeter compositor out of the root daemon while still creating a real
PAM/logind session for the greeter user.

It is responsible for:

- switching to the configured greeter TTY
- calling `setsid()` and acquiring the controlling TTY
- opening a PAM session for the greeter user
- setting session metadata such as `XDG_SESSION_TYPE`, `XDG_SEAT`, and `XDG_VTNR`
- dropping privileges to the greeter user
- `execve()`-ing the configured greeter compositor command

### Greeter UI

[`src/wldm/greeter.py`](../src/wldm/greeter.py) is an unprivileged GTK
application running inside the greeter compositor. It does not perform
authentication itself.

It is responsible for:

- rendering the login UI
- enumerating available Wayland sessions from `/usr/share/wayland-sessions`
- optionally extending that list with `~/.local/share/wayland-sessions` for the
  username currently typed into the greeter
- sending structured requests to the daemon over a UNIX socket
- reacting to daemon events such as `session-starting` and `session-finished`

The greeter does not execute anything from these entries before login. It only
uses them to populate the session picker and sends the chosen command back to
the daemon after successful authentication.

### User Session Wrapper

[`src/wldm/session.py`](../src/wldm/session.py) creates the final user session.
Like the greeter wrapper, it performs session setup before `execve()`-ing the
selected user program.

It is responsible for:

- opening a free TTY for the user session
- calling `setsid()` and acquiring the controlling TTY
- opening the user's PAM session
- applying PAM environment variables
- dropping privileges to the target user
- starting the selected shell, compositor, or session command

## IPC

Daemon and greeter communicate over a local UNIX socket. The protocol is
implemented in [`src/wldm/protocol.py`](../src/wldm/protocol.py).

Properties of the current transport:

- daemon creates the socket
- greeter learns the socket path from `WLDM_SOCKET`
- daemon validates the connecting UID with `SO_PEERCRED`
- messages are newline-delimited JSON envelopes
- the protocol supports request/response messages and asynchronous events

Current actions include:

- `auth`
- `poweroff`
- `reboot`

Current events include:

- `session-starting`
- `session-finished`

## Security Split

The design tries to minimize how much code runs with full privileges:

- root-only work stays in the daemon and session wrappers
- the visible greeter UI runs as the greeter user
- the final desktop session runs as the target user
- the greeter never talks to PAM directly for authentication

This split is important because PAM, TTY switching, seat control, and power
actions are privileged operations, while the UI and compositor should stay as
unprivileged as practical.

## Shutdown Model

The daemon installs signal handlers for `SIGTERM` and `SIGINT`. On shutdown it:

- stops accepting greeter traffic
- closes the active greeter connection
- terminates the greeter process group
- terminates active user session wrappers it started
- closes the greeter socket and console file descriptors

This is what allows `systemctl stop wldm.service` to tear down the display
manager cleanly instead of leaving `cage` or greeter processes behind.

## Why systemd Matters

`wldm` should be started as a `systemd` service, not from an already active
interactive shell session. `systemd-logind` uses real service and session
ownership, not just environment variables, when deciding which process may take
control of a seat.

For development, [`systemd-wldm.sh`](../systemd-wldm.sh) exists to reproduce the
service-style launch model from the source tree.
