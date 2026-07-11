# Greeter Stub Daemon

`tests/greeter_stub_daemon.py` is a development utility for running a greeter
without starting the real daemon. It is useful when iterating on the GTK
greeter and when developing another greeter implementation, such as a future
terminal UI greeter.

The utility creates the same inherited socket contract that the daemon uses,
starts a greeter process, and answers the daemon-facing greeter protocol. It
does not use PAM, switch TTYs, talk to logind, or require root privileges.

## Basic Usage

Run the current GTK greeter against the stub daemon:

```bash
tests/greeter_stub_daemon.py
```

By default the utility starts `src/wldm/command.py` with `WLDM_ROLE=greeter`,
sets `WLDM_SOCKET_FD`, points `WLDM_DATA_DIR` at the in-tree `data/` directory,
and creates a temporary `.desktop` session entry.

The stub uses the real `wldm.protocol.greeter` framing and message definitions.
This keeps the test path close to the real daemon/greeter IPC contract.

## Authentication Modes

Accept any password after showing a password prompt:

```bash
tests/greeter_stub_daemon.py --auth accept
```

Require a specific password:

```bash
tests/greeter_stub_daemon.py --auth password --password secret
```

Reject authentication:

```bash
tests/greeter_stub_daemon.py --auth reject
```

Change the prompt text or style:

```bash
tests/greeter_stub_daemon.py --prompt "Verification code:" --prompt-style visible
```

## Session Result Modes

Report a successful session start and finish:

```bash
tests/greeter_stub_daemon.py --session-result success
```

Report a failed session:

```bash
tests/greeter_stub_daemon.py --session-result failure
```

Leave the session running and send a state update with one active session:

```bash
tests/greeter_stub_daemon.py --session-result hang
```

Delay the `session-finished` event:

```bash
tests/greeter_stub_daemon.py --delay 3
```

## Session Entries

If `--session-dir` is not specified, the utility creates a temporary Wayland
session entry named `Stub Session` that runs `/bin/true`.

Use real or custom session entries instead:

```bash
tests/greeter_stub_daemon.py --session-dir /usr/share/wayland-sessions
```

Multiple session directories can be provided:

```bash
tests/greeter_stub_daemon.py \
  --session-dir tests/data/wayland-sessions \
  --session-dir /usr/share/wayland-sessions
```

## Testing Other Greeters

Use `--greeter-command` to run another greeter implementation against the same
fake daemon:

```bash
tests/greeter_stub_daemon.py --greeter-command 'python3 path/to/text-greeter.py'
```

The custom command must use the same greeter IPC contract and read the inherited
socket from `WLDM_SOCKET_FD`.

## Optional State File

The utility does not pass `WLDM_STATE_FILE` by default. This avoids accidental
interaction with secure path checks when temporary directories live under
system-specific private paths such as `/tmp/.private/...`.

Pass a state file explicitly when testing remembered greeter state:

```bash
tests/greeter_stub_daemon.py --state-file /tmp/wldm-greeter-state/last-session
```

The greeter will apply the same secure file handling rules that it uses under
the real daemon.

## Power Actions

Power actions are accepted by the stub but never executed:

```bash
tests/greeter_stub_daemon.py --actions poweroff,reboot
```

The option controls which action buttons are exposed to the greeter through
`WLDM_ACTIONS`.
