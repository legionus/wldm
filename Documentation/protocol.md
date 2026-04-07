# Internal IPC Protocol

This document describes the private IPC protocol used between the `wldm`
daemon and the internal clients it starts itself, such as the greeter and the
optional D-Bus adapter.

This is an internal protocol, not a public compatibility promise.

The authentication model is a request/response PAM conversation, not a single
username/password exchange.

## Transport

- The daemon creates one private connected `socketpair()` per internal client.
- The client end is inherited through `WLDM_SOCKET_FD`.
- There is no pathname listener for the greeter or D-Bus adapter path.

The implementation lives in
[`src/wldm/greeter_protocol.py`](../src/wldm/greeter_protocol.py).

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

## Authentication Conversation

The intended greeter login flow is:

```text
greeter                                daemon
-------                                ------

create-session(username)     ------->  create PAM auth context

                             <-------  response(ok=true,
                                                state="pending",
                                                message={style,text})
                                            or
                             <-------  response(ok=true,
                                                state="ready")
                                            or
                             <------- response(ok=false, ...)

continue-session(response)   ------->  feed reply into PAM

                             <-------  response(ok=true,
                                                state="pending",
                                                message={style,text})
                                            or
                             <-------  response(ok=true,
                                                state="ready")
                                            or
                             <-------  response(ok=false, ...)

cancel-session()             ------->  cancel PAM auth context

                             <-------  response(ok=true)

start-session(command,
              desktop_names) -------> launch user session

                             <-------  response(ok=true)
                             <-------  event(session-starting)
                             <-------  event(state-changed)
                             <-------  event(session-finished)
```

The important part is that PAM prompts stay synchronous at the protocol
level: each greeter reply gets exactly one response telling it either:

- another prompt is needed
- authentication is ready to start a session
- or the attempt failed

This keeps the login UI simpler than an event-driven auth conversation while
still allowing PAM stacks that ask more than one question.

### `create-session`

Wire layout:

```text
request(create-session) = id + action("create-session") + username
```

Fields:

- `username` (`bytes`) opaque auth field identifying the target user

Meaning:

- Starts or replaces the current authentication conversation for this client.

### `continue-session`

Wire layout:

```text
request(continue-session) = id + action("continue-session") + response
```

Fields:

- `response` (`bytes`) opaque reply to the current PAM prompt

Meaning:

- Sends one answer back into the current PAM conversation.

### `cancel-session`

Wire layout:

```text
request(cancel-session) = id + action("cancel-session")
```

Meaning:

- Cancels the current PAM conversation for this client.

### `start-session`

Wire layout:

```text
request(start-session) = id + action("start-session") + command + desktop_names
```

Fields:

- `command` (`string`) selected session command
- `desktop_names` (`string list`) desktop name tokens for the selected session

Meaning:

- Starts the final user session after the daemon has reported `state="ready"`.

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

### Successful `create-session` or `continue-session` Response

Wire layout:

```text
response(session-conversation, ok=true) = id + action + ok + state + has-message + [style + text]
```

Fields:

- `state` (`string`) one of:
  - `pending`
  - `ready`
- `has-message` (`bool`) whether a prompt payload follows
- `style` (`string`) prompt style when `has-message=true`
- `text` (`string`) prompt text when `has-message=true`

Meaning:

- `state="pending"` means the greeter must continue the PAM conversation.
- `state="ready"` means the greeter may send `start-session`.

Prompt styles are intended to be:

- `secret`
- `visible`
- `info`
- `error`

### Successful `cancel-session` Response

Wire layout:

```text
response(cancel-session, ok=true) = id + action("cancel-session") + ok
```

Meaning:

- Confirms that any in-progress PAM conversation for this client was dropped.

### Successful `start-session` Response

Wire layout:

```text
response(start-session, ok=true) = id + action("start-session") + ok
```

Meaning:

- Confirms that the daemon accepted the request to launch the final user
  session.

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

Conversation-specific errors are expected to include at least:

- `session_not_found`
- `session_not_ready`

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
- The same secret-carrying treatment should apply to
  `create-session.username` and `continue-session.response` in the
  conversation-based flow.
