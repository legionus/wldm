# wldm

`wldm` is a small Wayland display manager written mostly in Python. It runs a
greeter under an unprivileged user, talks to a privileged daemon over a local
inherited IPC fd, and starts the selected user session on a dedicated virtual
terminal.

For the runtime split and process model, see
[`Documentation/architecture.md`](Documentation/architecture.md). For config
options and examples, see
[`Documentation/configuration.md`](Documentation/configuration.md).

## Features

- GTK 4 greeter with session selection
- configurable greeter themes
- PAM-backed greeter and user sessions
- `systemd-logind` friendly runtime model
- separate daemon and greeter logs
- packaged `systemd` unit

## Runtime Requirements

At minimum you need:

- Python 3
- [`PyGObject`](https://pypi.org/project/PyGObject) with GTK 4 bindings
- [`Linux PAM`](https://github.com/linux-pam/linux-pam) (Pluggable Authentication
  Modules for Linux)
- a greeter compositor, by default `cage`

Recommended system integration:

- D-Bus when `[dbus].enabled = yes`
- `systemd-logind`
- starting `wldm` as a system-managed service instead of from an interactive
  shell

The install-time default configuration template is in
[`config/wldm.ini.in`](config/wldm.ini.in). `make install` turns it into the
installed `/etc/wldm.ini`.

By default the selected Wayland session is started through the bundled
`wayland-session` wrapper so the user's login shell and profile scripts can
prepare the environment before the compositor starts.

On Gentoo, that usually means system packages around:

- `dev-python/pygobject`
- `gui-libs/gtk:4`
- `sys-libs/pam`
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

`wldm.sh` uses [`config/wldm-devel.ini`](config/wldm-devel.ini) so in-tree
testing can keep runtime artifacts under `/tmp/wldm/` without changing the
main production defaults. It bootstraps the source tree through
`WLDM_SOURCE_TREE` and starts Python with `-I -P`, so in-tree runs no longer
depend on `PYTHONPATH`.

For more logging:

```bash
./wldm.sh -vv
```

or:

```bash
WLDM_VERBOSITY=2 ./wldm.sh greeter
```

The daemon exports `WLDM_VERBOSITY` to the greeter and session helpers it
starts, so increasing verbosity on the main `wldm` command enables their debug
logging too.

## Configuration

Configuration lookup order, option descriptions, and example files are
documented in [`Documentation/configuration.md`](Documentation/configuration.md).

## Building And Installing

Install development dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
```

Run checks:

```bash
./check.sh
```

For a system-style install with the hardened launcher, generated `/etc/wldm.ini`,
packaged data files, `systemd` unit, and D-Bus policy:

```bash
make install
```

By default this installs:

- `/usr/sbin/wldm`
- `/etc/wldm.ini`
- `/usr/lib/systemd/system/wldm.service`
- `/usr/share/dbus-1/system.d/wldm-dbus.conf`

## systemd

The repository ships the install-time unit template
[`data/systemd/wldm.service.in`](data/systemd/wldm.service.in). For a real
installation, prefer `make install` so the generated unit, launcher, and
config stay aligned.

Example:

```bash
make install
systemctl daemon-reload
systemctl enable wldm.service
systemctl start wldm.service
```

The installed unit starts `/usr/sbin/wldm`, which is the hardened launcher that
runs `python3 -I -P -m wldm.command`.

For packaged or production-oriented defaults, see
[`config/wldm.ini.in`](config/wldm.ini.in). For in-tree development overrides, see
[`config/wldm-devel.ini`](config/wldm-devel.ini).

## Contributing

Run [`check.sh`](check.sh) before sending changes. The project license is in
[`COPYING`](COPYING). Contributions are expected to follow the Developer
Certificate of Origin in [`DCO`](DCO).
