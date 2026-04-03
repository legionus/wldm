#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import asyncio
import os
import shlex
import signal
import socket
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Dict, Optional
from asyncio.subprocess import Process as AsyncProcess

import wldm
import wldm.command
import wldm.config
import wldm.inifile
import wldm.pam
import wldm.policy
import wldm.protocol
import wldm.secret
import wldm.state
import wldm.tty

logger = wldm.logger

class DaemonState:
    def __init__(self,
                 internal_command: str | list[str],
                 greeter_max_restarts: int,
                 seat: str = wldm.policy.DEFAULT_SEAT,
                 state_dir: str = "") -> None:
        if isinstance(internal_command, str):
            self.internal_command = [internal_command]
        else:
            self.internal_command = list(internal_command)

        self.greeter_max_restarts = greeter_max_restarts
        self.seat = seat
        self.state_dir = state_dir
        self.last_username = ""
        self.last_session_command = ""
        self.clients: dict[str, "ClientState"] = {"greeter": ClientState()}
        self.console: int = -1
        self.greeter_tty: int = 0
        self.active_sessions: dict[int, "SessionState"] = {}
        self.session_tasks: set[asyncio.Task[None]] = set()


@dataclass
class RequestOutcome:
    response: Dict[str, Any]
    event: Optional[Dict[str, Any]] = None
    session_username: str = ""
    session_command: str = ""
    session_desktop_names: list[str] | None = None
    control_action: str = ""


@dataclass
class SessionState:
    proc: AsyncProcess
    username: str
    command: str


@dataclass
class ClientState:
    proc: Optional[AsyncProcess] = None
    writer: Optional[asyncio.StreamWriter] = None
    task: Optional[asyncio.Task[None]] = None
    failures: int = 0
    ready: bool = False


POWER_ACTION_COMMANDS = {
    wldm.protocol.ACTION_POWEROFF: "poweroff-command",
    wldm.protocol.ACTION_REBOOT: "reboot-command",
    wldm.protocol.ACTION_SUSPEND: "suspend-command",
    wldm.protocol.ACTION_HIBERNATE: "hibernate-command",
}

KEYBOARD_ENV_OPTIONS = {
    "rules": "XKB_DEFAULT_RULES",
    "model": "XKB_DEFAULT_MODEL",
    "layout": "XKB_DEFAULT_LAYOUT",
    "variant": "XKB_DEFAULT_VARIANT",
    "options": "XKB_DEFAULT_OPTIONS",
}

def internal_command_prefix() -> list[str]:
    path = os.path.abspath(wldm.command.__file__ or "")

    if path.endswith((".pyc", ".pyo")):
        path = path[:-1]

    return [sys.executable, path]


def create_client_socketpair() -> tuple[socket.socket, socket.socket]:
    """Create a private connected socket pair for one internal client."""
    return socket.socketpair()


def client_state(state: DaemonState, name: str) -> ClientState:
    """Return the tracked runtime state for a named internal client."""
    return state.clients[name]


def state_snapshot(state: DaemonState) -> Dict[str, Any]:
    """Build the read-only daemon state exposed to internal observers.

    Args:
        state: Current daemon runtime state.

    Returns:
        A protocol payload with the configured seat, current greeter readiness,
        remembered login choice, and the list of active user sessions.
    """
    return {
        "seat": state.seat,
        "greeter_ready": client_state(state, "greeter").ready,
        "last_username": state.last_username,
        "last_session_command": state.last_session_command,
        "active_sessions": [
            {"pid": session.proc.pid, "username": session.username, "command": session.command}
            for session in state.active_sessions.values()
        ],
    }


def greeter_command(cfg: wldm.inifile.IniFile, internal_prefix: list[str]) -> list[str]:
    command = cfg.get_str("greeter", "command")

    return shlex.split(command) + internal_prefix + ["greeter"]


def configured_power_actions(cfg: wldm.inifile.IniFile) -> list[str]:
    actions = []

    for action, option in POWER_ACTION_COMMANDS.items():
        if cfg.get_str("daemon", option):
            actions.append(action)

    return actions


def control_command(cfg: wldm.inifile.IniFile, action: str) -> list[str]:
    option = POWER_ACTION_COMMANDS.get(action)

    if option is None:
        raise ValueError(f"unsupported control action: {action}")

    command = cfg.get_str("daemon", option)

    if not command:
        raise ValueError(f"control action is disabled: {action}")

    return shlex.split(command)


def keyboard_environment(cfg: wldm.inifile.IniFile) -> Dict[str, str]:
    env: Dict[str, str] = {}

    for option, env_name in KEYBOARD_ENV_OPTIONS.items():
        value = cfg.get_str("keyboard", option)
        if value:
            env[env_name] = value

    return env


def verify_creds(username: wldm.secret.SecretBytes, password: wldm.secret.SecretBytes) -> bool:
    if not username or not password:
        return False

    try:
        if wldm.pam.authenticate(username, password):
            return True

    except Exception as e:
        logger.critical("authorization failed: %s", e)

    return False


def process_request(state: DaemonState,
                    req: Dict[str, Any],
                    cfg: wldm.inifile.IniFile) -> RequestOutcome:
    if not wldm.protocol.is_request(req):
        return RequestOutcome(
            response=wldm.protocol.new_error(req, "bad_request", "Malformed request")
        )

    if req["action"] == wldm.protocol.ACTION_GET_STATE:
        return RequestOutcome(
            response=wldm.protocol.new_response(req, ok=True, payload=state_snapshot(state))
        )

    if req["action"] == wldm.protocol.ACTION_AUTH:
        payload = req["payload"]
        session_username_bytes = payload["username"].as_bytes()

        try:
            response = {"verified": verify_creds(payload["username"], payload["password"])}

        finally:
            # The login path only needs the cleartext credentials for the PAM
            # check, so scrub them immediately after the auth decision.
            payload["username"].clear()
            payload["password"].clear()

        outcome = RequestOutcome(response=wldm.protocol.new_response(req, ok=True, payload=response))

        if response["verified"]:
            outcome.event = wldm.protocol.new_event(
                wldm.protocol.EVENT_SESSION_STARTING,
                {
                    "command": payload["command"],
                    "desktop_names": payload.get("desktop_names", []),
                },
            )
            outcome.session_username = session_username_bytes.decode("utf-8", errors="replace")
            outcome.session_command = payload["command"]
            outcome.session_desktop_names = list(payload.get("desktop_names", []))

        return outcome

    if req["action"] in POWER_ACTION_COMMANDS:
        # Power actions stay behind explicit config toggles so a greeter theme
        # cannot offer controls that the local policy meant to disable.
        if req["action"] not in configured_power_actions(cfg):
            return RequestOutcome(
                response=wldm.protocol.new_error(req, "action_disabled", f"Action disabled: {req['action']}")
            )

        return RequestOutcome(
            response=wldm.protocol.new_response(req, ok=True, payload={"accepted": True}),
            control_action=req["action"],
        )

    return RequestOutcome(
        response=wldm.protocol.new_error(req, "unknown_action", f"Unknown action: {req['action']}"),
    )


async def send_message(writer: Optional[asyncio.StreamWriter], message: Dict[str, Any]) -> bool:
    if writer is None:
        return False

    try:
        writer.write(wldm.protocol.encode_message(message))
        await writer.drain()
        return True

    except Exception as e:
        logger.critical("unable to send protocol message: %s", e)

    return False


async def broadcast_message(state: DaemonState, message: Dict[str, Any]) -> None:
    """Send one protocol message to every connected internal client.

    Args:
        state: Current daemon runtime state.
        message: Encoded protocol object to broadcast.
    """
    for client in state.clients.values():
        await send_message(client.writer, message)


async def broadcast_state_changed(state: DaemonState) -> None:
    """Broadcast the current daemon state snapshot to all clients.

    Args:
        state: Current daemon runtime state.
    """
    await broadcast_message(
        state,
        wldm.protocol.new_event(wldm.protocol.EVENT_STATE_CHANGED, state_snapshot(state)),
    )


async def send_session_finished(state: DaemonState,
                                session: SessionState) -> None:
    proc = session.proc
    logger.info("user session (pid=%d) finished with return code %d", proc.pid, proc.returncode)

    state.active_sessions.pop(proc.pid, None)
    if state.console >= 0 and state.greeter_tty > 0:
        wldm.tty.change(state.console, state.greeter_tty)

    returncode = proc.returncode if proc.returncode is not None else wldm.EX_FAILURE

    failed = returncode != 0

    if not failed and session.command:
        state.last_username = session.username
        state.last_session_command = session.command

        try:
            wldm.state.save_last_session(state.state_dir, session.username, session.command)

        except OSError as e:
            logger.warning("unable to save last session state in %s: %s", state.state_dir, e)

    if failed:
        message = f"Session failed with exit status {returncode}."
    else:
        message = "Session finished."

    await broadcast_message(
        state,
        wldm.protocol.new_event(
            wldm.protocol.EVENT_SESSION_FINISHED,
            {"pid": proc.pid, "returncode": returncode, "failed": failed, "message": message},
        ),
    )
    await broadcast_state_changed(state)


async def monitor_session(state: DaemonState,
                          session: SessionState) -> None:
    await session.proc.wait()
    await send_session_finished(state, session)


def track_session_task(state: DaemonState, task: asyncio.Task[None]) -> None:
    state.session_tasks.add(task)
    task.add_done_callback(state.session_tasks.discard)


async def terminate_process_tree(proc: AsyncProcess,
                                 name: str,
                                 timeout: float = 5.0) -> None:
    if proc.returncode is not None:
        return

    logger.info("terminate %s (pid=%d)", name, proc.pid)

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        logger.critical("%s (pid=%d) did not stop after SIGTERM, sending SIGKILL", name, proc.pid)
    except ProcessLookupError:
        return

    with suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)

    with suppress(Exception):
        await proc.wait()


def install_stop_handlers(loop: asyncio.AbstractEventLoop,
                          stop_event: asyncio.Event) -> None:
    for signum in [signal.SIGTERM, signal.SIGINT]:
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)


def remove_stop_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for signum in [signal.SIGTERM, signal.SIGINT]:
        with suppress(NotImplementedError):
            loop.remove_signal_handler(signum)


async def wait_for_stop_or_process(proc: AsyncProcess,
                                   stop_event: asyncio.Event) -> bool:
    proc_task = asyncio.create_task(proc.wait())
    stop_task = asyncio.create_task(stop_event.wait())
    ret = False

    try:
        done, _ = await asyncio.wait(
            {proc_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        ret = stop_task in done and stop_event.is_set()

    except Exception:
        logger.exception("unexpected failure while waiting for the process")

    for task in [proc_task, stop_task]:
        if task.done():
            continue

        task.cancel()

        with suppress(asyncio.CancelledError):
            await task

    return ret


async def wait_for_stop_or_client(state: DaemonState,
                                  client_names: list[str],
                                  stop_event: asyncio.Event) -> tuple[bool, str]:
    """Wait until the daemon should stop or one managed client exits.

    Args:
        state: Current daemon runtime state.
        client_names: Client names whose processes should be watched.
        stop_event: Event set when the daemon should stop.

    Returns:
        A tuple ``(stopped, name)`` where ``stopped`` is true when
        ``stop_event`` won, and ``name`` is the client that exited first when a
        managed client finished instead.
    """
    client_tasks = {}
    ret = (False, "")

    for name in client_names:
        proc = client_state(state, name).proc
        if proc is None:
            continue

        client_tasks[asyncio.create_task(proc.wait())] = name

    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, _ = await asyncio.wait(
            set(client_tasks) | {stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done and stop_event.is_set():
            ret = (True, "")

        else:
            for task, name in client_tasks.items():
                if task in done:
                    ret = (False, name)
                    break

    except Exception:
        logger.exception("unexpected failure while waiting for managed clients")

    for task in [*client_tasks, stop_task]:
        if task.done():
            continue

        task.cancel()

        with suppress(asyncio.CancelledError):
            await task

    return ret


async def handle_request_async(state: DaemonState,
                               client_name: str,
                               req: Dict[str, Any],
                               cfg: wldm.inifile.IniFile) -> None:
    """Process one protocol request from an internal client.

    Args:
        state: Current daemon runtime state.
        client_name: Name of the client that sent the request.
        req: Decoded protocol request.
        cfg: Loaded daemon configuration.
    """
    client = client_state(state, client_name)
    outcome = process_request(state, req, cfg)
    await send_message(client.writer, outcome.response)

    if outcome.event is not None:
        await broadcast_message(state, outcome.event)

        # Sessions are tracked independently from the greeter so the daemon can
        # notify the UI when they finish and clean them up during shutdown.
        proc = await asyncio.create_subprocess_exec(
            *state.internal_command, "user-session", "--",
            outcome.session_username, *shlex.split(outcome.session_command),
            env=dict(
                os.environ,
                WLDM_SEAT=state.seat,
                WLDM_SESSION_DESKTOP_NAMES=":".join(outcome.session_desktop_names or []),
            ),
        )
        session = SessionState(proc=proc, username=outcome.session_username, command=outcome.session_command)
        state.active_sessions[proc.pid] = session

        logger.info("start user session (pid=%d)", proc.pid)

        track_session_task(state, asyncio.create_task(monitor_session(state, session)))
        await broadcast_state_changed(state)

    if outcome.control_action:
        command = control_command(cfg, outcome.control_action)

        logger.info("execute %s command: %s", outcome.control_action, command)

        await asyncio.create_subprocess_exec(*command)


async def handle_client(state: DaemonState,
                        name: str,
                        reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter,
                        cfg: wldm.inifile.IniFile) -> None:
    """Serve one connected internal client until its channel closes.

    Args:
        state: Current daemon runtime state.
        name: Stable client name used to look up ``ClientState``.
        reader: Stream used to receive protocol frames from the client.
        writer: Stream used to send protocol frames to the client.
        cfg: Loaded daemon configuration.
    """
    client = client_state(state, name)
    client.writer = writer

    try:
        while True:
            try:
                req = await wldm.protocol.read_message_async(reader)

            except wldm.protocol.ProtocolError as e:
                logger.critical("bad protocol message from %s: %s; raw=%r", name, e, e.raw)
                break

            if req is None:
                break

            client.ready = True
            await handle_request_async(state, name, req, cfg)

    finally:
        if client.writer is writer:
            client.writer = None

        writer.close()

        with suppress(Exception):
            await writer.wait_closed()


async def close_client_channel(state: DaemonState, name: str) -> None:
    """Close the transport and serving task for one internal client.

    Args:
        state: Current daemon runtime state.
        name: Client name whose channel should be closed.
    """
    client = client_state(state, name)

    if client.writer is not None:
        client.writer.close()

        with suppress(Exception):
            await client.writer.wait_closed()

        client.writer = None

    if client.task is not None:
        with suppress(Exception):
            await client.task

        client.task = None


async def close_greeter_channel(state: DaemonState) -> None:
    await close_client_channel(state, "greeter")


async def start_client(state: DaemonState,
                       name: str,
                       cfg: wldm.inifile.IniFile,
                       argv: list[str],
                       env: Dict[str, str]) -> AsyncProcess:
    """Start one internal client and attach its inherited IPC channel.

    Args:
        state: Current daemon runtime state.
        name: Stable client name used to track runtime state.
        cfg: Loaded daemon configuration.
        argv: Command line used to start the internal client.
        env: Environment passed to the client before the socket fd is added.

    Returns:
        The started subprocess object.
    """
    client = state.clients.setdefault(name, ClientState())
    daemon_sock, child_sock = create_client_socketpair()

    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=dict(env, WLDM_SOCKET_FD=str(child_sock.fileno())),
        pass_fds=(child_sock.fileno(),),
    )
    child_sock.close()

    reader, writer = await asyncio.open_connection(sock=daemon_sock)
    client.proc = proc
    client.task = asyncio.create_task(handle_client(state, name, reader, writer, cfg))
    client.ready = False

    logger.info("start %s (pid=%d)", name, proc.pid)

    return proc


async def start_greeter(state: DaemonState,
                        cfg: wldm.inifile.IniFile,
                        greeter_tty: int) -> AsyncProcess:
    greeter_pam_service = cfg.get_str("greeter", "pam-service")
    greeter_user = cfg.get_str("greeter", "user")
    greeter_group = cfg.get_str("greeter", "group")
    env = dict(
        os.environ,
        WLDM_SEAT=state.seat,
        WLDM_DATA_DIR=cfg.get_str("greeter", "data-dir"),
        WLDM_LOCALE_DIR=cfg.get_str("greeter", "locale-dir"),
        WLDM_THEME=cfg.get_str("greeter", "theme"),
        WLDM_GREETER_SESSION_DIRS=cfg.get_str("greeter", "session-dirs"),
        WLDM_GREETER_USER_SESSION_DIR=cfg.get_str("greeter", "user-session-dir"),
        WLDM_ACTIONS=":".join(configured_power_actions(cfg)),
        WLDM_GREETER_STDERR_LOG=cfg.get_str("greeter", "log-path"),
        WLDM_GREETER_USER_SESSIONS="yes" if cfg.get_bool("greeter", "user-sessions") else "no",
    )

    if state.last_session_command:
        env["WLDM_LAST_SESSION_COMMAND"] = state.last_session_command

    if state.last_username:
        env["WLDM_LAST_USERNAME"] = state.last_username

    # Keep compositor-side keyboard setup in the daemon environment contract so
    # greeter.command can stay a plain launcher wrapper.
    env.update(keyboard_environment(cfg))

    return await start_client(
        state,
        "greeter",
        cfg,
        [
            *state.internal_command, "greeter-session",
            "--tty", str(greeter_tty),
            "--pam-service", greeter_pam_service,
            greeter_user, greeter_group,
            *greeter_command(cfg, state.internal_command),
        ],
        env,
    )


async def start_dbus_adapter(state: DaemonState,
                             cfg: wldm.inifile.IniFile) -> Optional[AsyncProcess]:
    """Start the optional dbus-adapter client when enabled in config.

    Args:
        state: Current daemon runtime state.
        cfg: Loaded daemon configuration.

    Returns:
        The started subprocess object, or ``None`` when the adapter is
        disabled or could not be started.
    """
    if not cfg.get_bool("dbus", "enabled"):
        return None

    user = cfg.get_str("dbus", "user")
    service = cfg.get_str("dbus", "service")
    log_path = cfg.get_str("dbus", "log-path")

    try:
        return await start_client(
            state,
            "dbus-adapter",
            cfg,
            [*state.internal_command, "dbus-adapter", user, service],
            dict(os.environ, WLDM_DBUS_LOG_PATH=log_path),
        )

    except Exception as e:
        logger.warning("unable to start dbus-adapter: %s", e)
        return None


async def ensure_managed_clients(state: DaemonState,
                                 cfg: wldm.inifile.IniFile,
                                 greeter_tty: int) -> list[str]:
    """Ensure the configured managed clients are running.

    Args:
        state: Current daemon runtime state.
        cfg: Loaded daemon configuration.
        greeter_tty: TTY reserved for the greeter session.

    Returns:
        The ordered list of managed client names that should be watched by the
        main daemon loop in this iteration.
    """
    names = ["greeter"]

    if client_state(state, "greeter").proc is None:
        proc = await start_greeter(state, cfg, greeter_tty)

        if client_state(state, "greeter").proc is None:
            client_state(state, "greeter").proc = proc

    if cfg.get_bool("dbus", "enabled"):
        names.append("dbus-adapter")

        if "dbus-adapter" not in state.clients:
            state.clients["dbus-adapter"] = ClientState()

        if client_state(state, "dbus-adapter").proc is None:
            adapter_proc = await start_dbus_adapter(state, cfg)

            if adapter_proc is not None and client_state(state, "dbus-adapter").proc is None:
                client_state(state, "dbus-adapter").proc = adapter_proc

    return names


async def cleanup_async(state: DaemonState) -> None:
    for name in list(state.clients):
        await close_client_channel(state, name)

    # Stop the greeter before user sessions so the login UI cannot race the
    # shutdown sequence and start new work while the daemon is tearing down.
    greeter = client_state(state, "greeter")
    if greeter.proc is not None and greeter.proc.returncode is None:
        await terminate_process_tree(greeter.proc, "the greeter")

    for name, client in state.clients.items():
        if name == "greeter":
            continue

        if client.proc is not None and client.proc.returncode is None:
            await terminate_process_tree(client.proc, name)

    for session in list(state.active_sessions.values()):
        with suppress(Exception):
            await terminate_process_tree(session.proc, "user session")

    if state.session_tasks:
        await asyncio.gather(*state.session_tasks, return_exceptions=True)


async def run_daemon_async(parser: argparse.Namespace, cfg: wldm.inifile.IniFile) -> int:
    greeter_tty = cfg.get_int("greeter", "tty", 0)
    greeter_max_restarts = cfg.get_int("greeter", "max-restarts", 3)

    if not greeter_tty:
        greeter_tty = parser.tty

    # The daemon controls VT switching itself so it can always bring the
    # greeter back to a known console after a session exits.
    console = wldm.tty.open_console()
    if console is None:
        logger.critical("unable to open tty device")
        return wldm.EX_FAILURE

    if not greeter_tty:
        num = wldm.tty.available(console)

        if num is None:
            logger.critical("unable to get available tty device for greeter")
            os.close(console)
            return wldm.EX_FAILURE

        greeter_tty = num

    if not wldm.tty.change(console, greeter_tty):
        logger.critical("unable to switch to tty%d for greeter", greeter_tty)
        os.close(console)
        return wldm.EX_FAILURE

    logger.debug("daemon start")

    state = DaemonState(
        internal_command_prefix(),
        greeter_max_restarts,
        seat=cfg.get_str("daemon", "seat"),
        state_dir=cfg.get_str("daemon", "state-dir"),
    )
    state.console = console
    state.greeter_tty = greeter_tty
    state.last_username, state.last_session_command = wldm.state.load_last_session(state.state_dir)

    exit_code = wldm.EX_SUCCESS

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    install_stop_handlers(loop, stop_event)
    greeter = client_state(state, "greeter")

    try:
        while True:
            client_names = await ensure_managed_clients(state, cfg, greeter_tty)

            stopped, exited_name = await wait_for_stop_or_client(state, client_names, stop_event)

            if stopped:
                logger.info("stop signal received, shutting down daemon")
                break

            if not exited_name:
                continue

            exited = client_state(state, exited_name)
            proc = exited.proc
            if proc is None:
                continue

            exited.proc = None

            if exited_name == "greeter":
                logger.info("greeter (pid=%d) finished with return code %d", proc.pid, proc.returncode)
                await close_client_channel(state, exited_name)

                # Restart the greeter after ordinary exits, but stop once it never
                # reaches the ready state repeatedly to avoid a tight crash loop.
                if greeter.ready:
                    greeter.failures = 0
                else:
                    greeter.failures += 1

                    if greeter.failures >= state.greeter_max_restarts:
                        logger.critical("greeter failed %d times in a row, stopping daemon",
                                        greeter.failures)

                        exit_code = wldm.EX_FAILURE
                        break

                await asyncio.sleep(1)
                continue

            logger.warning("%s (pid=%d) finished with return code %d",
                           exited_name, proc.pid, proc.returncode)
            await close_client_channel(state, exited_name)
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        await cleanup_async(state)
        raise

    except Exception:
        logger.exception("unexpected daemon failure")
        exit_code = wldm.EX_FAILURE
        await cleanup_async(state)

    finally:
        remove_stop_handlers(loop)
        await cleanup_async(state)
        os.close(console)

    logger.debug("daemon finished")
    return exit_code


def cmd_main(parser: argparse.Namespace) -> int:
    cfg = wldm.config.read_config()

    log_path = cfg.get_str("daemon", "log-path")
    if log_path:
        wldm.setup_file_logger(logger, level=logger.level, fmt="[%(asctime)s] %(message)s", path=log_path)

    return asyncio.run(run_daemon_async(parser, cfg))
