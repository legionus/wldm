# wldm

`wldm` is a small Wayland display manager written mostly in Python. It runs a
greeter under an unprivileged user, talks to a privileged daemon over a local
UNIX socket, and starts the selected user session on a dedicated virtual
terminal.

For the runtime split and process model, see
[`Documentation/architecture.md`](Documentation/architecture.md).

## Features

- GTK 4 greeter with session selection
- PAM-backed greeter and user sessions
- `systemd-logind` friendly runtime model
- separate daemon and greeter logs
- packaged `systemd` unit

## Runtime Requirements

At minimum you need:

- Python 3
- `PyGObject` with GTK 4 bindings
- PAM
- `systemd-logind`
- a greeter compositor, by default `cage`

The default configuration is in [`config/wldm.ini`](config/wldm.ini).

On Gentoo, that usually means system packages around:

- `dev-python/pygobject`
- `gui-libs/gtk:4`
- `sys-libs/pam`
- `sys-apps/systemd`
- a Wayland greeter compositor such as `cage`

Exact package names, slots, and USE flags can vary by profile and tree state.

## Running From The Source Tree

For command-line inspection:

```bash
./wldm.sh --help
```

For realistic runtime testing, prefer `systemd`:

```bash
./systemd-wldm.sh
```

Running the daemon directly from an existing shell session is not equivalent to
running it as a system-managed service and can confuse `logind`.

## Configuration

The daemon reads configuration from:

1. `WLDM_CONFIG`
2. `config/wldm.ini` next to the launcher script
3. `sys.prefix/share/wldm/config/wldm.ini`
4. `/etc/wldm.ini`

Useful defaults:

- daemon log: `/tmp/wldm/daemon.log`
- greeter log: `/tmp/wldm/greeter.log`
- greeter socket: `/tmp/wldm/greeter.sock`

By default the greeter shows system session entries from
`/usr/share/wayland-sessions` and, after a username is entered, also looks for
user-specific entries in `~/.local/share/wayland-sessions`. This can be
disabled with:

```ini
[greeter]
user-sessions = no
```

## Building And Installing

Install development dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
```

Run checks:

```bash
./check.sh
```

Build source and wheel distributions:

```bash
python3 -m build
```

If isolated builds fail because `venv` support is missing, use:

```bash
python3 -m build --no-isolation
```

Install the package locally:

```bash
python3 -m pip install .
```

The packaged `systemd` unit is installed as
`share/wldm/systemd/wldm.service`.

## systemd

The repository ships [`systemd/wldm.service`](systemd/wldm.service). For a real
installation, copy or install it into your system unit directory, adjust paths
if needed, then enable and start it with `systemctl`.

Example:

```bash
python3 -m pip install .
install -Dm0644 systemd/wldm.service /etc/systemd/system/wldm.service
systemctl daemon-reload
systemctl enable wldm.service
systemctl start wldm.service
```

The packaged unit starts `/usr/bin/wldm`, so system installs should either
place the entry point there or adjust `ExecStart=` accordingly.

## Example Configuration

A minimal `/etc/wldm.ini` can look like this:

```ini
[daemon]
seat = seat0
socket-path = /run/wldm/greeter.sock
log-path = /var/log/wldm/daemon.log

[greeter]
user = gdm
group = gdm
tty = 7
command = cage -s -m last --
pam-service = system-login
max-restarts = 3
user-sessions = yes
log-path = /var/log/wldm/greeter.log

[session]
pam-service = login
```

For local development, the defaults in [`config/wldm.ini`](config/wldm.ini)
use `/tmp/wldm/` for logs and the greeter socket.

## Contributing

Run [`check.sh`](check.sh) before sending changes. The project license is in
[`COPYING`](COPYING). Contributions are expected to follow the Developer
Certificate of Origin in [`DCO`](DCO).
