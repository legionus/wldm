#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import asyncio
import grp
import json
import os
import pwd
import shlex
import signal
import socket
import stat
import struct
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Dict, Optional
from asyncio.subprocess import Process as AsyncProcess

import wldm
import wldm.config
import wldm.pam
import wldm.policy
import wldm.protocol
import wldm.tty

logger = wldm.logger


class SocketListener:
    def __init__(self, path: str) -> None:
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(1)

    def close(self) -> None:
        with suppress(Exception):
            self.sock.close()
        with suppress(FileNotFoundError):
            os.unlink(self.path)


class DaemonState:
    def __init__(self,
                 progname: str,
                 greeter_max_restarts: int,
                 greeter_uid: int = -1,
                 seat: str = wldm.policy.DEFAULT_SEAT) -> None:
        self.progname = progname
        self.greeter_max_restarts = greeter_max_restarts
        self.greeter_uid = greeter_uid
        self.seat = seat
        self.greeter_proc: Optional[AsyncProcess] = None
        self.greeter_writer: Optional[asyncio.StreamWriter] = None
        self.greeter_failures = 0
        self.greeter_ready = False
        self.console: int = -1
        self.greeter_tty: int = 0
        self.active_sessions: dict[int, AsyncProcess] = {}
        self.session_tasks: set[asyncio.Task[None]] = set()


@dataclass
class RequestOutcome:
    response: Dict[str, Any]
    event: Optional[Dict[str, Any]] = None
    session_username: str = ""
    session_command: str = ""
    session_desktop_names: list[str] | None = None
    control_action: str = ""


POWER_ACTION_COMMANDS = {
    wldm.protocol.ACTION_POWEROFF: "poweroff-command",
    wldm.protocol.ACTION_REBOOT: "reboot-command",
    wldm.protocol.ACTION_SUSPEND: "suspend-command",
    wldm.protocol.ACTION_HIBERNATE: "hibernate-command",
}


def greeter_socket_path(cfg: Optional[Any] = None) -> str:
    if "WLDM_SOCKET" in os.environ:
        return os.environ["WLDM_SOCKET"]
    if cfg is not None:
        return str(cfg["daemon"].get("socket-path", "/tmp/wldm/greeter.sock"))
    return "/tmp/wldm/greeter.sock"


def create_greeter_listener(user: str, group: str, path: str) -> SocketListener:
    sockdir = os.path.dirname(path)
    basename = os.path.basename(path)
    if not basename:
        raise RuntimeError(f"invalid socket path: {path}")

    with wldm.open_secure_directory(sockdir or ".", mode=0o755) as dir_fd:
        try:
            st = os.stat(basename, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode):
                raise RuntimeError(f"refusing to replace non-socket path: {path}")
            os.unlink(basename, dir_fd=dir_fd)

    listener = SocketListener(path)
    uid = pwd.getpwnam(user).pw_uid
    gid = grp.getgrnam(group).gr_gid
    os.chown(path, uid, gid)
    os.chmod(path, 0o600)
    return listener


def get_peer_uid(writer: asyncio.StreamWriter) -> int:
    sock = writer.get_extra_info("socket")
    if sock is None:
        raise RuntimeError("unable to get peer socket")

    _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return int(uid)


def greeter_command(cfg: Any, progname: str) -> list[str]:
    command = str(cfg["greeter"].get("command", "cage -s -m last --"))
    return shlex.split(command) + [progname, "greeter"]


def configured_power_actions(cfg: Any) -> list[str]:
    actions = []
    for action, option in POWER_ACTION_COMMANDS.items():
        if str(cfg["daemon"].get(option, "")).strip():
            actions.append(action)
    return actions


def control_command(cfg: Any, action: str) -> list[str]:
    option = POWER_ACTION_COMMANDS.get(action)
    if option is None:
        raise ValueError(f"unsupported control action: {action}")
    command = str(cfg["daemon"].get(option, "")).strip()
    if not command:
        raise ValueError(f"control action is disabled: {action}")
    return shlex.split(command)


def verify_creds(data: Dict[str, str]) -> bool:
    for field in ["username", "password"]:
        if field not in data or len(data[field]) == 0:
            return False
    try:
        ret = wldm.pam.authenticate(data["username"].encode(), data["password"].encode())
        if ret:
            return True
    except Exception as e:
        logger.critical("authorization failed: %s", e)
    return False


def process_request(req: Dict[str, Any], cfg: Optional[Any] = None) -> RequestOutcome:
    if not wldm.protocol.is_request(req):
        return RequestOutcome(
            response=wldm.protocol.new_error(req, "bad_request", "Malformed request"),
        )

    if req["action"] == wldm.protocol.ACTION_AUTH:
        payload = req["payload"]
        response = {"verified": verify_creds(payload)}
        outcome = RequestOutcome(
            response=wldm.protocol.new_response(req, ok=True, payload=response),
        )
        if response["verified"]:
            outcome.event = wldm.protocol.new_event(
                wldm.protocol.EVENT_SESSION_STARTING,
                {
                    "username": payload["username"],
                    "command": payload["command"],
                    "desktop_names": payload.get("desktop_names", []),
                },
            )
            outcome.session_username = payload["username"]
            outcome.session_command = payload["command"]
            outcome.session_desktop_names = list(payload.get("desktop_names", []))
        return outcome

    if req["action"] in POWER_ACTION_COMMANDS:
        if cfg is not None and req["action"] not in configured_power_actions(cfg):
            return RequestOutcome(
                response=wldm.protocol.new_error(
                    req, "action_disabled", f"Action disabled: {req['action']}"
                ),
            )
        return RequestOutcome(
            response=wldm.protocol.new_response(
                req, ok=True, payload={"accepted": True},
            ),
            control_action=req["action"],
        )

    return RequestOutcome(
        response=wldm.protocol.new_error(req, "unknown_action", f"Unknown action: {req['action']}"),
    )


async def send_message(writer: Optional[asyncio.StreamWriter], message: Dict[str, Any]) -> bool:
    if writer is None:
        return False

    try:
        writer.write((wldm.protocol.encode_message(message) + "\n").encode())
        await writer.drain()
        return True
    except Exception as e:
        logger.critical("unable to send protocol message: %s", e)
    return False


async def send_session_finished(state: DaemonState,
                                proc: AsyncProcess) -> None:
    logger.info("user session (pid=%d) finished with return code %d", proc.pid, proc.returncode)
    state.active_sessions.pop(proc.pid, None)
    if state.console >= 0 and state.greeter_tty > 0:
        wldm.tty.change(state.console, state.greeter_tty)
    returncode = proc.returncode if proc.returncode is not None else wldm.EX_FAILURE
    failed = returncode != 0
    if failed:
        message = f"Session failed with exit status {returncode}."
    else:
        message = "Session finished."
    await send_message(
        state.greeter_writer,
        wldm.protocol.new_event(
            wldm.protocol.EVENT_SESSION_FINISHED,
            {"pid": proc.pid, "returncode": returncode, "failed": failed, "message": message},
        ),
    )


async def monitor_session(state: DaemonState,
                          proc: AsyncProcess) -> None:
    await proc.wait()
    await send_session_finished(state, proc)


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
    try:
        done, _ = await asyncio.wait(
            {proc_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        return stop_task in done and stop_event.is_set()
    finally:
        for task in [proc_task, stop_task]:
            if task.done():
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def handle_request_async(state: DaemonState,
                               req: Dict[str, Any],
                               cfg: Optional[Any] = None) -> None:
    outcome = process_request(req, cfg)
    await send_message(state.greeter_writer, outcome.response)

    if outcome.event is not None:
        await send_message(state.greeter_writer, outcome.event)
        proc = await asyncio.create_subprocess_exec(
            state.progname,
            "session",
            "--",
            outcome.session_username,
            *shlex.split(outcome.session_command),
            env=dict(
                os.environ,
                WLDM_SEAT=state.seat,
                WLDM_SESSION_DESKTOP_NAMES=":".join(outcome.session_desktop_names or []),
            ),
        )
        state.active_sessions[proc.pid] = proc
        logger.info("start user session (pid=%d)", proc.pid)
        track_session_task(state, asyncio.create_task(monitor_session(state, proc)))

    if outcome.control_action:
        if cfg is None:
            raise RuntimeError("daemon config is required for control actions")
        command = control_command(cfg, outcome.control_action)
        logger.info("execute %s command: %s", outcome.control_action, command)
        await asyncio.create_subprocess_exec(*command)


async def handle_greeter_client(state: DaemonState,
                                reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter,
                                cfg: Optional[Any] = None) -> None:
    try:
        peer_uid = get_peer_uid(writer)
    except Exception as e:
        logger.critical("unable to get greeter peer credentials: %s", e)
        writer.close()
        await writer.wait_closed()
        return

    if state.greeter_uid >= 0 and peer_uid != state.greeter_uid:
        logger.critical("reject greeter connection from unexpected uid %d", peer_uid)
        writer.close()
        await writer.wait_closed()
        return

    if state.greeter_writer is not None:
        writer.close()
        await writer.wait_closed()
        return

    state.greeter_writer = writer

    try:
        while True:
            line = await reader.readline()
            if len(line) == 0:
                break

            try:
                req = wldm.protocol.decode_message(line.decode())
            except (json.decoder.JSONDecodeError, ValueError) as e:
                logger.critical("bad json from greeter: %s", e)
                continue

            state.greeter_ready = True
            await handle_request_async(state, req, cfg)
    finally:
        if state.greeter_writer is writer:
            state.greeter_writer = None
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


async def start_greeter(state: DaemonState,
                        cfg: Any,
                        greeter_tty: int,
                        socket_path: str) -> AsyncProcess:
    env = dict(
        os.environ,
        WLDM_SOCKET=socket_path,
        WLDM_SEAT=state.seat,
        WLDM_THEME=cfg["greeter"].get("theme", "default"),
        WLDM_GREETER_SESSION_DIRS=cfg["greeter"].get("session-dirs", ":".join(wldm.policy.SYSTEM_WAYLAND_SESSION_DIRS)),
        WLDM_GREETER_USER_SESSION_DIR=cfg["greeter"].get("user-session-dir", wldm.policy.USER_WAYLAND_SESSION_DIR),
        WLDM_ACTIONS=":".join(configured_power_actions(cfg)),
        WLDM_GREETER_STDERR_LOG=cfg["greeter"].get("log-path", "/tmp/wldm/greeter.log"),
        WLDM_GREETER_USER_SESSIONS=cfg["greeter"].get("user-sessions", "yes"),
    )
    proc = await asyncio.create_subprocess_exec(
        state.progname,
        "greeter-session",
        "--tty",
        str(greeter_tty),
        "--pam-service",
        cfg["greeter"]["pam-service"],
        cfg["greeter"]["user"],
        cfg["greeter"]["group"],
        *greeter_command(cfg, state.progname),
        env=env,
    )
    state.greeter_proc = proc
    state.greeter_ready = False
    logger.info("start the greeter (pid=%d)", proc.pid)
    return proc


async def cleanup_async(state: DaemonState) -> None:
    if state.greeter_writer is not None:
        state.greeter_writer.close()
        with suppress(Exception):
            await state.greeter_writer.wait_closed()
        state.greeter_writer = None

    if state.greeter_proc is not None and state.greeter_proc.returncode is None:
        await terminate_process_tree(state.greeter_proc, "the greeter")

    for proc in list(state.active_sessions.values()):
        with suppress(Exception):
            await terminate_process_tree(proc, "user session")

    if state.session_tasks:
        await asyncio.gather(*state.session_tasks, return_exceptions=True)


async def run_daemon_async(parser: argparse.Namespace, cfg: Optional[Any] = None) -> int:
    if cfg is None:
        cfg = wldm.config.read_config()

    progname = os.environ.get("WLDM_PROGNAME", sys.argv[0])

    greeter_tty = 0
    greeter_max_restarts = int(cfg["greeter"].get("max-restarts", "3"))

    if "tty" in cfg["greeter"]:
        greeter_tty = int(cfg["greeter"]["tty"])

    if not greeter_tty:
        greeter_tty = parser.tty

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

    greeter_uid = pwd.getpwnam(cfg["greeter"]["user"]).pw_uid
    socket_path = greeter_socket_path(cfg)
    listener = create_greeter_listener(cfg["greeter"]["user"], cfg["greeter"]["group"], socket_path)
    state = DaemonState(
        progname,
        greeter_max_restarts,
        greeter_uid=greeter_uid,
        seat=cfg["daemon"].get("seat", wldm.policy.DEFAULT_SEAT),
    )
    state.console = console
    state.greeter_tty = greeter_tty
    exit_code = wldm.EX_SUCCESS
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    install_stop_handlers(loop, stop_event)
    server = await asyncio.start_unix_server(
        lambda reader, writer: handle_greeter_client(state, reader, writer, cfg),
        sock=listener.sock,
    )

    try:
        while True:
            proc = await start_greeter(state, cfg, greeter_tty, listener.path)
            if await wait_for_stop_or_process(proc, stop_event):
                logger.info("stop signal received, shutting down daemon")
                break

            logger.info("greeter (pid=%d) finished with return code %d", proc.pid, proc.returncode)

            if state.greeter_writer is not None:
                state.greeter_writer.close()
                with suppress(Exception):
                    await state.greeter_writer.wait_closed()
                state.greeter_writer = None

            if state.greeter_ready:
                state.greeter_failures = 0
            else:
                state.greeter_failures += 1
                if state.greeter_failures >= state.greeter_max_restarts:
                    logger.critical("greeter failed %d times in a row, stopping daemon",
                                    state.greeter_failures)
                    exit_code = wldm.EX_FAILURE
                    break

            await asyncio.sleep(1)
    except asyncio.CancelledError:
        await cleanup_async(state)
        raise
    except Exception:
        await cleanup_async(state)
    finally:
        remove_stop_handlers(loop)
        await cleanup_async(state)
        server.close()
        await server.wait_closed()
        listener.close()
        os.close(console)

    logger.debug("daemon finished")
    return exit_code


def cmd_main(parser: argparse.Namespace) -> int:
    cfg = wldm.config.read_config()
    log_path = str(cfg["daemon"].get("log-path", "")).strip()
    if log_path:
        wldm.setup_file_logger(logger, level=logger.level, fmt="[%(asctime)s] %(message)s", path=log_path)
    return asyncio.run(run_daemon_async(parser, cfg))
