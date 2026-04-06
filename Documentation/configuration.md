# Configuration

This document describes the runtime configuration options understood by `wldm`.

## Lookup Order

`wldm` reads configuration from the first file that exists in this order:

1. `WLDM_CONFIG`
2. `/etc/wldm.ini`

The production-oriented repository default file is
[`config/wldm.ini.in`](../config/wldm.ini.in), which is turned into the
installed `/etc/wldm.ini` by `make install`.

For source-tree development, [`wldm.sh`](../wldm.sh) sets `WLDM_CONFIG` to
[`config/wldm-devel.ini`](../config/wldm-devel.ini), which keeps runtime
artifacts under `/tmp/wldm/` and points the greeter at in-tree assets without
baking those paths into the main config.

## `[daemon]`

- `seat`
  Seat identifier passed into session metadata. Default: `seat0`.
- `log-path`
  Daemon log file. Default: empty, which keeps logging on stderr/journal.
- `poweroff-command`
  Shell command executed for greeter poweroff requests. Default:
  `systemctl poweroff`.
- `reboot-command`
  Shell command executed for greeter reboot requests. Default:
  `systemctl reboot`.
- `suspend-command`
  Shell command executed for greeter suspend requests. Default: empty, which
  disables the action and hides the button in the greeter.
- `hibernate-command`
  Shell command executed for greeter hibernate requests. Default: empty, which
  disables the action and hides the button in the greeter.

## `[greeter]`

- `user`
  Unprivileged account used for the greeter compositor and UI.
- `group`
  Primary group for the greeter account.
- `tty`
  Virtual terminal reserved for the greeter. Default: `7`.
- `data-dir`
  Directory that contains greeter assets such as `resources/` and optional
  `themes/`. The installed config points this at `/usr/share/wldm`.
- `locale-dir`
  Directory that contains gettext catalogs for the greeter. The installed
  config points this at `/usr/share/locale`.
- `state-dir`
  Optional directory used for small greeter-managed state. When set, the
  greeter stores the last successfully completed username and session command
  in a bounded `last-session` state file there and restores that UI state on
  the next greeter activation. The daemon passes the resolved file path to the
  greeter through `WLDM_STATE_FILE`.
- `theme`
  Greeter theme name. `default` uses `data-dir/resources`. Any other value
  makes the greeter look for `themes/<name>/` next to that resource base path
  and fall back to `default` if it does not exist.

  Themes may also ship `locale/<lang>/LC_MESSAGES/wldm.mo` under their theme
  directory. When present, the greeter uses that locale tree for GtkBuilder
  labels, tooltips, and other gettext-backed greeter strings while the theme
  is active before falling back to `locale-dir`.
- `session-dirs`
  Colon-separated list of system directories scanned for session `.desktop`
  files. Default: `/usr/share/wayland-sessions`.
- `user-session-dir`
  Per-user session directory relative to the user's home directory. Default:
  `.local/share/wayland-sessions`.
- `command`
  Greeter compositor launcher prefix. Default: `cage -s -m last --`.
  `greeter-session` reads this string from the daemon environment and appends
  `wldm greeter` automatically.
- `pam-service`
  PAM service used for the greeter session. Default: `system-login`.
- `max-restarts`
  How many failed greeter starts are tolerated before the daemon stops.
- `user-sessions`
  If enabled, the greeter also reads `~/.local/share/wayland-sessions` after a
  username is entered.
- `log-path`
  Greeter stderr log file. Default: empty.

## `[session]`

- `pam-service`
  PAM service used for the final user session. Default: `login`.
- `execute`
  Session startup wrapper used to run the selected user session. The installed
  `/etc/wldm.ini` sets this to `data-dir/scripts/wayland-session`. Set this to
  an empty value to execute the selected session command directly without a
  wrapper.

  This is useful when the session needs extra preparation before the compositor
  starts, for example running it through the user's login shell so
  `/etc/profile` and `~/.profile` are loaded, or replacing the default wrapper
  with a site-local script that does additional setup such as
  `dbus-update-activation-environment --systemd`.

  Relative paths are resolved against the source-tree root only in source-tree
  mode, where `WLDM_SOURCE_TREE` contains that root path. This lets the in-tree
  development config use values such as `data/scripts/wayland-session`.
- `pre-execute`
  Optional executable run after the user PAM session is opened and the session
  environment is prepared, but before the final user program is executed. A
  non-zero exit status aborts the session start.
- `post-execute`
  Optional executable run after the user session exits and before the PAM
  session is closed. A non-zero exit status is logged but does not interrupt
  cleanup.

Session hooks are executed as the target user, not as `root`. They inherit the
session environment assembled by `wldm`, including `XDG_RUNTIME_DIR`,
`XDG_SESSION_TYPE`, `XDG_SESSION_CLASS`, `XDG_SEAT`, `XDG_VTNR`,
`XDG_SESSION_DESKTOP`, `XDG_CURRENT_DESKTOP`, and `DESKTOP_SESSION` when that
metadata is available.

`wldm` also adds:

- `WLDM_TTY`
- `WLDM_SESSION_COMMAND`

The hook paths themselves are executable paths, not shell command lines.
Relative paths are resolved against the directory that contains the loaded
`wldm.ini`.

## `[dbus]`

- `enabled`
  Enable the optional `wldm dbus-adapter` subprocess. Default: `no`.
- `user`
  User account used to run the adapter process. This must stay aligned with the
  installed system bus policy file. If you change it in `/etc/wldm.ini`, update
  `/usr/share/dbus-1/system.d/wldm-dbus.conf` as well.
- `service`
  Well-known system-bus name exported by the adapter. Default:
  `org.freedesktop.DisplayManager`.
- `log-path`
  Optional log file for the adapter process. Default: empty, which keeps
  logging on stderr/journal.

When enabled, the adapter connects back to the daemon over an inherited IPC fd
and exports a small read-only `org.freedesktop.DisplayManager` API on the
system bus. The adapter is not login-critical: if it fails to start, the daemon
logs a warning and continues.

`make install` also installs the matching system-bus policy file at:

```text
/usr/share/dbus-1/system.d/wldm-dbus.conf
```

The exported D-Bus object tree and properties are documented in
[`dbus.md`](dbus.md).

## `[keyboard]`

- `rules`
- `model`
- `layout`
- `variant`
- `options`

These values are exported to the greeter environment as the corresponding
`XKB_DEFAULT_*` variables:

- `XKB_DEFAULT_RULES`
- `XKB_DEFAULT_MODEL`
- `XKB_DEFAULT_LAYOUT`
- `XKB_DEFAULT_VARIANT`
- `XKB_DEFAULT_OPTIONS`

`wldm` itself does not apply keyboard settings inside the GTK greeter process.
Instead, it exposes the configured values to `greeter.command`, which lets
`cage` and other compositors consume them through their normal XKB handling.

## Verbosity

`wldm`, `wldm greeter`, `wldm user-session`, `wldm greeter-session`, and
`wldm dbus-adapter` all accept the common CLI flags:

- `-v`
  Set log level to `INFO`.
- `-vv` and above
  Set log level to `DEBUG`.
- `-q`
  Set log level to `CRITICAL`.

The effective verbosity is also exported through `WLDM_VERBOSITY`. Child
processes inherit it, so starting the main daemon with `-vv` also enables
debug logging in the greeter, D-Bus adapter, and session helpers it launches.

Examples:

```bash
wldm -vv
```

```bash
WLDM_VERBOSITY=2 ./wldm.sh greeter
```

## Example

```ini
[daemon]
seat = seat0
log-path =
poweroff-command = systemctl poweroff
reboot-command = systemctl reboot
suspend-command = systemctl suspend
hibernate-command = systemctl hibernate

[greeter]
user = gdm
group = gdm
tty = 7
data-dir = /usr/share/wldm
locale-dir = /usr/share/locale
state-dir =
theme = default
session-dirs = /usr/share/wayland-sessions
user-session-dir = .local/share/wayland-sessions
command = cage -s -m last --
pam-service = system-login
max-restarts = 3
user-sessions = yes
log-path =

[session]
pam-service = login
execute = /usr/share/wldm/scripts/wayland-session
pre-execute = /usr/libexec/wldm-session-pre
post-execute = /usr/libexec/wldm-session-post

[dbus]
enabled = no
user = gdm
service = org.freedesktop.DisplayManager
log-path =

[keyboard]
rules = evdev
model = pc105
layout = us,ru
variant =
options = grp:alt_shift_toggle
```
