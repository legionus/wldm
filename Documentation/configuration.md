# Configuration

This document describes the runtime configuration options understood by `wldm`.

## Lookup Order

`wldm` reads configuration from the first file that exists in this order:

1. `WLDM_CONFIG`
2. `config/wldm.ini` next to the launcher script
3. `sys.prefix/share/wldm/config/wldm.ini`
4. `/etc/wldm.ini`

The production-oriented repository default file is
[`config/wldm.ini`](../config/wldm.ini).

For source-tree development, [`wldm.sh`](../wldm.sh) sets `WLDM_CONFIG` to
[`config/wldm-devel.ini`](../config/wldm-devel.ini), which keeps runtime
artifacts under `/tmp/wldm/` without baking those paths into the main config.

## `[daemon]`

- `seat`
  Seat identifier passed into session metadata. Default: `seat0`.
- `socket-path`
  UNIX socket used for daemon/greeter IPC. Default: `/run/wldm/greeter.sock`.
- `log-path`
  Daemon log file. Default: empty, which keeps logging on stderr/journal.
- `poweroff-command`
  Command executed for greeter poweroff requests. Default: `systemctl poweroff`.
- `reboot-command`
  Command executed for greeter reboot requests. Default: `systemctl reboot`.
- `suspend-command`
  Command executed for greeter suspend requests. Default: empty, which disables
  the action and hides the button in the greeter.
- `hibernate-command`
  Command executed for greeter hibernate requests. Default: empty, which
  disables the action and hides the button in the greeter.

## `[greeter]`

- `user`
  Unprivileged account used for the greeter compositor and UI.
- `group`
  Primary group for the greeter account.
- `tty`
  Virtual terminal reserved for the greeter. Default: `7`.
- `theme`
  Greeter theme name. `default` uses the built-in `resources/` directory.
  Any other value makes the greeter look for `themes/<name>/` next to the
  resource base path and fall back to `default` if it does not exist.
- `session-dirs`
  Colon-separated list of system directories scanned for session `.desktop`
  files. Default: `/usr/share/wayland-sessions`.
- `user-session-dir`
  Per-user session directory relative to the user's home directory. Default:
  `.local/share/wayland-sessions`.
- `command`
  Greeter compositor launcher prefix. Default: `cage -s -m last --`.
  `wldm greeter` is appended automatically.
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
- `command`
  Session startup wrapper used to run the selected user session. Default:
  `default`, which resolves to the bundled `share/wldm/scripts/wayland-session`
  helper. Set this to `none` or `direct` to execute the selected session
  command directly without a wrapper.
  This is useful when the session needs extra preparation before the compositor
  starts, for example running it through the user's login shell so
  `/etc/profile` and `~/.profile` are loaded, or replacing the default wrapper
  with a site-local script that does additional setup such as
  `dbus-update-activation-environment --systemd`.
- `pre-command`
  Optional command run after the user PAM session is opened and the session
  environment is prepared, but before the final user program is executed.
  A non-zero exit status aborts the session start.
- `post-command`
  Optional command run after the user session exits and before the PAM session
  is closed. A non-zero exit status is logged but does not interrupt cleanup.

Session hooks are executed as the target user, not as `root`. They inherit the
session environment assembled by `wldm`, including `XDG_RUNTIME_DIR`,
`XDG_SESSION_TYPE`, `XDG_SESSION_CLASS`, `XDG_SEAT`, `XDG_VTNR`,
`XDG_SESSION_DESKTOP`, `XDG_CURRENT_DESKTOP`, and `DESKTOP_SESSION` when that
metadata is available.

`wldm` also adds:

- `WLDM_TTY`
- `WLDM_SESSION_COMMAND`

Commands are parsed with `shlex.split()` and are not run through a shell unless
the configured command explicitly invokes one.

## Example

```ini
[daemon]
seat = seat0
socket-path = /run/wldm/greeter.sock
log-path =
poweroff-command = systemctl poweroff
reboot-command = systemctl reboot
suspend-command = systemctl suspend
hibernate-command = systemctl hibernate

[greeter]
user = gdm
group = gdm
tty = 7
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
command = default
pre-command = /usr/libexec/wldm-session-pre
post-command = /usr/libexec/wldm-session-post
```
