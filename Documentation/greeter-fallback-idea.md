# Greeter Fallback Idea

This document describes a possible fallback path for systems where the
graphical greeter cannot start reliably.

## Problem

The current greeter path is graphical:

```text
wldm daemon
└─ wldm greeter-session
   └─ cage
      └─ wldm greeter
```

That keeps the GTK greeter out of the root daemon and gives the greeter a real
PAM/logind session, but it also means that the login UI depends on the
graphical stack. If `cage`, GTK, the graphics driver, or compositor setup is
broken, the daemon can only restart the same graphical path until the greeter
restart limit is reached.

A text greeter could provide an emergency login path when the graphical stack
is not usable.

## Goals

- Keep the root daemon as the single supervisor and source of truth.
- Reuse the existing daemon/greeter protocol for authentication, session
  selection, power actions, and state updates where possible.
- Avoid making GTK, cage, or terminal UI failures affect each other.
- Make fallback behavior explicit enough that real graphical failures are not
  hidden silently.
- Keep optional terminal UI dependencies out of the normal graphical startup
  path.

## Non-Goals

- Do not replace the graphical greeter with a text-only greeter.
- Do not treat every greeter crash as proof that the graphical stack is broken.
- Do not add a second authentication implementation. PAM conversation handling
  should stay daemon-mediated through the existing `pam-worker` path.
- Do not make the daemon parse or execute session files differently for each
  greeter backend.

## Proposed Model

Introduce a second greeter backend that runs directly on the greeter TTY and
does not require cage:

```text
wldm daemon
└─ wldm text-greeter-session
   └─ wldm text-greeter
```

The names above are illustrative. The implementation might instead extend the
existing `greeter-session` wrapper with a backend mode, provided the backend
contract stays explicit.

The text greeter should use the same daemon-facing greeter protocol as the GTK
greeter. The UI layer would be different, but the security-sensitive flow
should remain the same:

- the greeter asks the daemon to create or continue an auth session
- the daemon owns the PAM worker and forwards prompts/responses
- the daemon decides when an authenticated session can start
- the daemon starts the final user session

## Fallback Policy

Fallback should be configurable. A reasonable initial shape would be:

```ini
[greeter]
backend = gtk
fallback-backend = text
fallback-after-failures = 3
```

The exact option names are not important yet. The important contract is that a
site administrator can choose whether fallback is enabled and when it is used.

The daemon should not fall back after every greeter exit. The safest first
criterion is startup failure before the greeter has completed its daemon
handshake. For example:

- graphical greeter process exits before sending `ready`
- graphical greeter session wrapper exits before the UI connects to the daemon
- this happens repeatedly up to the configured threshold

Once the graphical greeter has connected and reported readiness, later crashes
may indicate a different class of bug. Those should still be counted and
reported through the normal restart-limit path unless a separate policy is
defined.

## Text Greeter Scope

The first text greeter does not need feature parity with the GTK UI. It should
be intentionally small:

- prompt for username
- show PAM prompts and errors
- read secret input without echo
- allow choosing the default session, and later a session list
- send start-session after successful authentication
- show basic session start/failure status
- support cancel/retry

Power actions and richer session selection can be added later once the fallback
path is proven.

Python's `curses` module may be enough for an initial implementation, but the
code should treat terminal capability problems as runtime failures of the text
backend. Missing or unusable curses support must not break the graphical path.

## Implementation Plan

1. Define greeter backend state in the daemon.

   Add an internal model that distinguishes the configured primary backend from
   the active backend. Track whether a greeter process reached the daemon
   handshake before it exited.

2. Make startup failures observable.

   Ensure the daemon can tell the difference between "process exited before
   ready" and "ready greeter exited later". Keep this logic in daemon
   supervision, not in the UI.

3. Add fallback configuration.

   Add explicit config fields for fallback backend and failure threshold. The
   initial default should be conservative: either disabled, or enabled only
   when the fallback backend is explicitly configured.

4. Add a backend-aware greeter launcher.

   Either extend `greeter-session` with an explicit backend mode or add a
   separate text greeter session wrapper. The text path should not launch cage.

5. Implement a minimal text greeter.

   Reuse the greeter protocol and shared greeter auth/client helpers where the
   existing boundaries fit. Keep terminal rendering separate from protocol and
   authentication state.

6. Add tests for fallback decisions.

   Cover at least:

   - no fallback when disabled
   - fallback after repeated pre-ready exits
   - no fallback after a ready greeter exits unless policy says so
   - restart limits still stop the daemon when no backend can start

7. Document operational behavior.

   Update configuration documentation with the final option names and explain
   that text fallback is an emergency path, not a silent replacement for fixing
   the graphical greeter.

## Open Questions

- Should the text greeter use `curses`, plain termios, or another minimal TUI
  layer?
- Should fallback be disabled by default, or enabled when `fallback-backend` is
  set?
- Should an administrator be able to force the text backend as the primary
  backend for rescue systems?
- How much session selection UI is required for the first version?
- Should a successful text fallback suppress further graphical restart attempts
  until the next daemon restart?
