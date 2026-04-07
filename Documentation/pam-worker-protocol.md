# PAM Worker Protocol

This document describes the private IPC protocol between the `wldm` daemon and
the `wldm pam-worker` helper.

This is an internal protocol, not a public compatibility promise.

The protocol exists only for the daemon-owned PAM authentication path:

- the daemon starts one `pam-worker` per in-progress greeter authentication
  attempt
- the worker owns one blocking PAM conversation
- the daemon translates worker prompts into the greeter protocol

For the daemon/greeter protocol, see
[`protocol.md`](protocol.md).

## Transport

- The daemon creates one private connected `socketpair()` for each worker.
- The worker end is inherited through `WLDM_SOCKET_FD`.
- There is no pathname listener.

The implementation lives in
[`src/wldm/pam_worker_protocol.py`](../src/wldm/pam_worker_protocol.py).

## Frame Format

Each message is encoded as one length-prefixed frame:

```text
+----------------------+-------------------+
| 4-byte body length   | frame body bytes  |
+----------------------+-------------------+
```

- The length field is an unsigned 32-bit integer in network byte order.
- The maximum frame body length is 2048 bytes.

## Body Layout

Every frame body starts with:

```text
body = version + kind + kind-specific-fields
```

- `version` is one byte. Current value: `1`.
- `kind` is an encoded `string`.

## Primitive Types

- `bytes`
  `u32 length` followed by exactly that many raw bytes.
- `string`
  `u32 length` followed by UTF-8 bytes.

There is no generic map or array encoding on the wire. Field order is fixed by
the message kind.

## Daemon To Worker Messages

### `start`

Wire layout:

```text
start = version + kind("start") + service + username + tty
```

Fields:

- `service` (`string`) PAM service name such as `login`
- `username` (`string`) target login name
- `tty` (`string`) TTY path exposed to PAM, such as `/dev/tty1`

Meaning:

- Sent once at the beginning of a worker lifetime.
- Tells the worker to start one PAM authentication transaction.

### `answer`

Wire layout:

```text
answer = version + kind("answer") + response
```

Fields:

- `response` (`bytes`) raw reply to the current PAM prompt

Meaning:

- Supplies one greeter answer back into the blocking PAM callback.

### `cancel`

Wire layout:

```text
cancel = version + kind("cancel")
```

Meaning:

- Aborts the current worker-side PAM conversation.

## Worker To Daemon Messages

### `prompt`

Wire layout:

```text
prompt = version + kind("prompt") + style + text
```

Fields:

- `style` (`string`) greeter-facing prompt style
  - `secret`
  - `visible`
  - `info`
  - `error`
- `text` (`string`) prompt text to display

Meaning:

- The worker reached one PAM conversation step and needs one greeter-side
  response or acknowledgement.

### `ready`

Wire layout:

```text
ready = version + kind("ready")
```

Meaning:

- PAM authentication succeeded and the daemon may accept `start-session` for
  this greeter client.

### `failed`

Wire layout:

```text
failed = version + kind("failed") + message
```

Fields:

- `message` (`string`) human-readable failure text

Meaning:

- The PAM conversation ended unsuccessfully and the daemon should surface the
  failure back to the greeter.

## Typical Flow

```text
daemon                               pam-worker
------                               ----------

start(service, username, tty) -----> start PAM transaction

                                    callback needs input
                         <--------- prompt(style, text)

answer(response) ------------------> resume callback with reply

                                    callback needs more input
                         <--------- prompt(style, text)

answer(response) ------------------> resume callback with reply

                                    pam_authenticate() succeeds
                         <--------- ready
```

Cancellation path:

```text
daemon                               pam-worker
------                               ----------

cancel() --------------------------> abort callback / authentication
```

Failure path:

```text
daemon                               pam-worker
------                               ----------

start(...) ------------------------> PAM auth fails
                         <--------- failed(message)
```

## Notes

- The worker protocol is intentionally narrower than the greeter protocol.
- The worker never launches sessions and never exposes daemon state.
- The daemon is responsible for translating `prompt` / `ready` / `failed`
  results into greeter protocol responses.
