# D-Bus Interface

This document describes the currently implemented D-Bus API exported by
`wldm dbus-adapter`.

For the config options that control the adapter, see
[`configuration.md`](configuration.md).

## Overview

When `[dbus].enabled = yes`, the daemon starts `wldm dbus-adapter` as an
unprivileged subprocess. The adapter connects back to the daemon over an
inherited IPC fd, mirrors the daemon's read-only state snapshot, and exports a
small `org.freedesktop.DisplayManager` service on the system bus.

The adapter is intentionally read-only. It does not expose login control,
session termination, or display-management methods.

## Dependency Note

The adapter implementation uses `Gio` / `GLib` through `PyGObject`.

If `[dbus].enabled = no`, the daemon does not start the adapter and the rest of
the login stack does not depend on the D-Bus module at runtime.

## Bus Name

The adapter exports the well-known name configured by:

```ini
[dbus]
service = org.freedesktop.DisplayManager
```

The current implementation uses the system bus.

## Required System-Bus Policy

The adapter user must be allowed to own the configured bus name, and clients
must be allowed to call the exported read-only interfaces.

`make install` installs the matching policy file at:

```text
/usr/share/dbus-1/system.d/wldm-dbus.conf
```

If you change `[dbus].user` or `[dbus].service` in `/etc/wldm.ini`, update the
installed policy file to match.

## Object Paths

The adapter exports these object types:

- manager object:
  - `/org/freedesktop/DisplayManager`
- one seat object for the configured seat:
  - `/org/freedesktop/DisplayManager/Seat0`
- one session object per active user session:
  - `/org/freedesktop/DisplayManager/Session<PID>`

The session object path currently uses the tracked user-session pid.

## Interfaces

### `org.freedesktop.DisplayManager`

Object path:

- `/org/freedesktop/DisplayManager`

Methods:

- `ListSeats() -> ao`
- `ListSessions() -> ao`

Properties:

- `Seats`
  - type: `ao`
- `Sessions`
  - type: `ao`

Signals:

- `SeatAdded(o seat_path)`
- `SeatRemoved(o seat_path)`
- `SessionAdded(o session_path)`
- `SessionRemoved(o session_path)`

### `org.freedesktop.DisplayManager.Seat`

Object path pattern:

- `/org/freedesktop/DisplayManager/Seat<NAME>`

Properties:

- `Id`
  - type: `s`
- `Sessions`
  - type: `ao`

Methods:

- none

Signals:

- none on this interface

### `org.freedesktop.DisplayManager.Session`

Object path pattern:

- `/org/freedesktop/DisplayManager/Session<PID>`

Properties:

- `Id`
  - type: `s`
  - current value: session pid rendered as text
- `Username`
  - type: `s`
- `Seat`
  - type: `o`
  - object path of the configured seat
- `Class`
  - type: `s`
  - current fixed value: `"user"`
- `Type`
  - type: `s`
  - current fixed value: `"wayland"`
- `Active`
  - type: `b`
  - current fixed value: `true` for tracked active sessions

Methods:

- none

Signals:

- none on this interface

## Standard Interfaces

The exported objects also support the standard D-Bus interfaces used for
inspection and property reads:

- `org.freedesktop.DBus.Introspectable`
- `org.freedesktop.DBus.Properties`

The adapter emits `PropertiesChanged` when the manager or seat view changes.

## State Mapping

The D-Bus adapter is driven by the daemon's internal read-only state API:

- startup snapshot:
  - `get-state`
- incremental updates:
  - `state-changed`

The current exported state includes:

- configured seat
- active user sessions

The daemon's remembered login state (`last_username`,
`last_session_command`) is not exported on D-Bus.

Greeter pseudo-sessions are also not exported.

## Non-goals

The current D-Bus API is intentionally limited. It does not provide:

- login control methods
- session termination methods
- greeter pseudo-sessions
- display-creation APIs
- power-management APIs

The adapter is an observer-facing mirror of daemon state, not a second control
plane.

## Failure Behavior

D-Bus support is auxiliary integration, not part of the login-critical path.

- If the adapter fails to start, the daemon logs a warning and continues.
- If the adapter loses the daemon channel or the well-known bus name, it exits
  and the daemon may restart it.
- Adapter failure does not terminate active user sessions.

## Verification

On a system with `[dbus].enabled = yes` and the installed bus policy in place,
you can verify the exported service with `busctl`:

```sh
busctl --system list | grep org.freedesktop.DisplayManager
busctl --system tree org.freedesktop.DisplayManager
busctl --system introspect org.freedesktop.DisplayManager /org/freedesktop/DisplayManager
busctl --system call org.freedesktop.DisplayManager /org/freedesktop/DisplayManager org.freedesktop.DisplayManager ListSessions
busctl --system monitor org.freedesktop.DisplayManager
```

Expected behavior:

- the service appears in `busctl --system list`
- the manager object introspects successfully
- `ListSeats()` returns one configured seat path
- `ListSessions()` reflects active user sessions
- `SessionAdded`, `SessionRemoved`, and `PropertiesChanged` appear while
  sessions start and finish

## Stability Notes

The current API is intentionally small and conservative. The following details
should be treated as implementation choices rather than long-term promises
until explicitly documented otherwise:

- session object paths based on pid
- `Session.Id` being the pid rendered as a string
- fixed values for `Class`, `Type`, and `Active`

The interface is intended for observation, not control.
