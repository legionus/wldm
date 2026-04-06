# Internal IPC Protocol

This document describes the private IPC protocol used between the `wldm`
daemon and the internal clients it starts itself, such as the greeter and the
optional D-Bus adapter.

This is an internal protocol, not a public compatibility promise.

## Transport

- The daemon creates one private connected `socketpair()` per internal client.
- The client end is inherited through `WLDM_SOCKET_FD`.
- There is no pathname listener for the greeter or D-Bus adapter path.

The implementation lives in
[`src/wldm/protocol.py`](../src/wldm/protocol.py).

## Frame Format

Each message is encoded as one length-prefixed frame:

```text
+----------------------+-------------------+
| 4-byte body length   | frame body bytes  |
+----------------------+-------------------+
```

- The length field is an unsigned 32-bit integer in network byte order.
- The maximum frame body length is 2048 bytes.
- If the advertised body length is larger, the receiver rejects the frame
  before reading the body.

## Body Layout

Every frame body starts with:

```text
body = version + message-type + message-specific-fields
```

- `version` is one byte. Current value: `1`.
- `message-type` is one byte:
  - `1` = request
  - `2` = response
  - `3` = event

## Primitive Types

The body uses a small fixed set of encodings:

- `bool`
  One byte. `0` means `false`, any non-zero value means `true`.
- `int`
  Signed 32-bit integer in network byte order.
- `bytes`
  `u32 length` followed by exactly that many raw bytes.
- `string`
  `u32 length` followed by UTF-8 bytes.
- `string list`
  `u32 count` followed by that many encoded `string` values.
- `session list`
  `u32 count` followed by that many encoded session records.

Session records are encoded in fixed order:

```text
session = pid + username + command
```

- `pid` is `int`
- `username` is `string`
- `command` is `string`

There is no generic map or array encoding on the wire. Field order is fixed by
the message type.

## Request Messages

General layout:

```text
request = id + action + request-specific-fields
```

- `id` (`string`) request identifier
- `action` (`string`) request name

### `auth`

Wire layout:

```text
request(auth) = id + action("auth") + username + password + command + desktop_names
```

Fields:

- `username` (`bytes`) opaque auth field
- `password` (`bytes`) opaque auth field
- `command` (`string`) selected session command
- `desktop_names` (`string list`) desktop name tokens for the selected session

Limits:

- `username` and `password` are each limited to 256 bytes on the wire.

Meaning:

- Sent by the greeter when the user submits a login request.
- On success, the daemon later broadcasts `session-starting`.

### `get-state`

Wire layout:

```text
request(get-state) = id + action("get-state")
```

Meaning:

- Sent by internal observer clients such as the D-Bus adapter.
- Requests the current daemon state snapshot.

### Power Actions

Supported actions:

- `poweroff`
- `reboot`
- `suspend`
- `hibernate`

Wire layout:

```text
request(control) = id + action
```

Meaning:

- Sent by the greeter for power actions that are enabled in config.

## Response Messages

General layout:

```text
response = id + action + ok + response-specific-fields
```

- `id` (`string`) request identifier
- `action` (`string`) action name copied from the request
- `ok` (`bool`) response status

### Successful `auth` Response

Wire layout:

```text
response(auth, ok=true) = id + action("auth") + ok + verified
```

Fields:

- `verified` (`bool`) `true` means PAM authentication succeeded

### Successful `get-state` Response

Wire layout:

```text
response(get-state, ok=true) =
    id + action("get-state") + ok + seat + greeter_ready + active_sessions
```

Fields:

- `seat` (`string`) current seat id
- `greeter_ready` (`bool`) whether the daemon currently considers the greeter
  ready
- `active_sessions` (`session list`) active user sessions

### Successful Power-Action Response

Wire layout:

```text
response(control, ok=true) = id + action + ok + accepted
```

Fields:

- `accepted` (`bool`) `true` means the daemon accepted the action and started
  the configured command

### Error Response

Wire layout:

```text
response(ok=false) = id + action + ok + error.code + error.message
```

Fields:

- `error.code` (`string`) short machine-readable error code
- `error.message` (`string`) human-readable error description

Current error codes:

- `bad_request`
- `unknown_action`
- `action_disabled`

## Event Messages

General layout:

```text
event = event-name + event-specific-fields
```

- `event-name` (`string`) event name

### `session-starting`

Wire layout:

```text
event(session-starting) = event("session-starting") + command + desktop_names
```

Fields:

- `command` (`string`) selected session command
- `desktop_names` (`string list`) desktop name tokens for the selected session

Meaning:

- Broadcast after successful authentication and before the final user session
  is launched.

### `session-finished`

Wire layout:

```text
event(session-finished) = event("session-finished") + pid + returncode + failed + message
```

Fields:

- `pid` (`int`) process id of the finished session wrapper process
- `returncode` (`int`) exit status of that process
- `failed` (`bool`) `true` when `returncode` is non-zero
- `message` (`string`) human-readable summary

Meaning:

- Broadcast when a user session exits.

### `state-changed`

Wire layout:

```text
event(state-changed) = event("state-changed") + seat + greeter_ready + active_sessions
```

Fields:

- `seat` (`string`) current seat id
- `greeter_ready` (`bool`) whether the daemon currently considers the greeter
  ready
- `active_sessions` (`session list`) active user sessions

Meaning:

- Broadcast whenever the daemon's observer-facing state snapshot changes.

## Notes

- The on-wire format is binary, not JSON.
- The Python-side protocol objects are dictionaries, but those dictionaries are
  serialized into the fixed layouts described above.
- `username` and `password` are decoded into secret-carrying buffers rather
  than plain text strings so the auth path can scrub them after PAM
  verification.
