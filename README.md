# wldm

`wldm` is a small Wayland display manager written mostly in Python. It runs a
greeter under an unprivileged user, talks to a privileged daemon over a local
UNIX socket, and starts the selected user session on a dedicated virtual
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
- `PyGObject` with GTK 4 bindings
- PAM
- `systemd-logind`
- a greeter compositor, by default `cage`

The default configuration is in [`config/wldm.ini`](config/wldm.ini).

By default the selected Wayland session is started through the bundled
`wayland-session` wrapper so the user's login shell and profile scripts can
prepare the environment before the compositor starts.

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

`wldm.sh` uses [`config/wldm-devel.ini`](config/wldm-devel.ini) so in-tree
testing can keep the socket and logs under `/tmp/wldm/` without changing the
main production defaults.

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

For packaged or production-oriented defaults, see
[`config/wldm.ini`](config/wldm.ini). For in-tree development overrides, see
[`config/wldm-devel.ini`](config/wldm-devel.ini).

## Contributing

Run [`check.sh`](check.sh) before sending changes. The project license is in
[`COPYING`](COPYING). Contributions are expected to follow the Developer
Certificate of Origin in [`DCO`](DCO).
