#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import os
import pwd
import socket
import threading

from typing import Any

import wldm
import wldm.greeter_protocol as greeter_protocol

logger = wldm.logger

MANAGER_INTERFACE = "org.freedesktop.DisplayManager"
SEAT_INTERFACE = "org.freedesktop.DisplayManager.Seat"
SESSION_INTERFACE = "org.freedesktop.DisplayManager.Session"
MANAGER_PATH = "/org/freedesktop/DisplayManager"

MANAGER_XML = f"""\
<node>
  <interface name="{MANAGER_INTERFACE}">
    <method name="ListSeats">
      <arg type="ao" name="seats" direction="out"/>
    </method>
    <method name="ListSessions">
      <arg type="ao" name="sessions" direction="out"/>
    </method>
    <property name="Seats" type="ao" access="read"/>
    <property name="Sessions" type="ao" access="read"/>
    <signal name="SeatAdded">
      <arg type="o" name="seat"/>
    </signal>
    <signal name="SeatRemoved">
      <arg type="o" name="seat"/>
    </signal>
    <signal name="SessionAdded">
      <arg type="o" name="session"/>
    </signal>
    <signal name="SessionRemoved">
      <arg type="o" name="session"/>
    </signal>
  </interface>
</node>
"""

SEAT_XML = f"""\
<node>
  <interface name="{SEAT_INTERFACE}">
    <property name="Id" type="s" access="read"/>
    <property name="Sessions" type="ao" access="read"/>
  </interface>
</node>
"""

SESSION_XML = f"""\
<node>
  <interface name="{SESSION_INTERFACE}">
    <property name="Id" type="s" access="read"/>
    <property name="Username" type="s" access="read"/>
    <property name="Seat" type="o" access="read"/>
    <property name="Class" type="s" access="read"/>
    <property name="Type" type="s" access="read"/>
    <property name="Active" type="b" access="read"/>
  </interface>
</node>
"""


def load_unprivileged_modules() -> tuple[Any, Any]:
    """Import modules that are only needed after dropping privileges.

    Returns:
        A ``(Gio, GLib)`` pair from ``gi.repository`` for the unprivileged
        D-Bus adapter path.
    """
    try:
        from gi.repository import Gio, GLib  # type: ignore[import-untyped]

    except Exception as e:
        raise RuntimeError(f"D-Bus support is unavailable: {e}") from e

    return Gio, GLib


def adapter_ipc_fd() -> int:
    """Return the inherited daemon IPC fd for the adapter process.

    Returns:
        The connected socket fd passed down by the daemon.
    """
    socket_fd = os.environ.get("WLDM_SOCKET_FD", "").strip()
    if not socket_fd:
        raise RuntimeError("environ variable `WLDM_SOCKET_FD' not specified")

    fd = int(socket_fd)
    os.set_inheritable(fd, True)
    return fd


def seat_object_path(seat: str) -> str:
    """Build the stable D-Bus object path for one seat id.

    Args:
        seat: Seat identifier reported by the daemon.

    Returns:
        The exported object path for that seat.
    """
    component = "".join(char if char.isalnum() else "_" for char in seat)
    if not component:
        component = "Seat0"
    elif component.lower().startswith("seat"):
        component = "Seat" + component[4:]
    else:
        component = "Seat" + component

    return f"{MANAGER_PATH}/{component}"


def session_object_path(pid: int) -> str:
    """Build the D-Bus object path for one active session pid.

    Args:
        pid: Process id of the active session.

    Returns:
        The exported object path for that session.
    """
    return f"{MANAGER_PATH}/Session{pid}"


def session_paths(snapshot: dict[str, object]) -> list[str]:
    """Return exported session object paths for one daemon snapshot.

    Args:
        snapshot: Current daemon state snapshot payload.

    Returns:
        A list of object paths for every active user session.
    """
    sessions = snapshot.get("active_sessions", [])

    if not isinstance(sessions, list):
        return []

    return [session_object_path(int(session.get("pid", 0))) for session in sessions if isinstance(session, dict)]


def seat_paths(snapshot: dict[str, object]) -> list[str]:
    """Return exported seat object paths for one daemon snapshot.

    Args:
        snapshot: Current daemon state snapshot payload.

    Returns:
        A single-element list containing the configured seat path when present.
    """
    seat = snapshot.get("seat", "")

    if not isinstance(seat, str) or not seat:
        return []

    return [seat_object_path(seat)]


class SocketClient:
    def __init__(self, fd: int) -> None:
        self.sock = socket.socket(fileno=fd)

    def write_message(self, message: dict[str, object]) -> None:
        self.sock.sendall(greeter_protocol.encode_message(message))

    def read_message(self) -> dict[str, object] | None:
        return greeter_protocol.read_message_socket(self.sock)

    def close(self) -> None:
        self.sock.close()


def request_state(client: SocketClient) -> dict[str, object]:
    """Fetch the initial daemon state snapshot over the internal protocol.

    Args:
        client: Connected internal client transport.

    Returns:
        The decoded state snapshot payload returned by the daemon.
    """
    request = greeter_protocol.new_request(greeter_protocol.ACTION_GET_STATE, {})
    client.write_message(request)

    response = client.read_message()

    if response is None:
        raise RuntimeError("daemon closed the adapter channel")

    if not greeter_protocol.is_response(response, request):
        raise RuntimeError("daemon returned a malformed state response")

    if not response.get("ok", False):
        raise RuntimeError("daemon rejected the adapter state request")

    payload = response.get("payload", {})

    if not isinstance(payload, dict):
        raise RuntimeError("daemon returned a malformed state payload")

    return payload


class DisplayManagerService:
    """Mirror daemon state onto a small read-only D-Bus object tree."""

    def __init__(self, service: str, snapshot: dict[str, object], Gio: Any, GLib: Any) -> None:
        self.service = service
        self.snapshot = dict(snapshot)
        self.Gio = Gio
        self.GLib = GLib
        self.loop: Any = None
        self.name_acquired = False
        self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.owner_id = Gio.bus_own_name_on_connection(
            self.connection,
            service,
            Gio.BusNameOwnerFlags.NONE,
            self._on_name_acquired,
            self._on_name_lost,
        )
        self.registration_ids: dict[str, int] = {}
        self.manager_info = Gio.DBusNodeInfo.new_for_xml(MANAGER_XML).interfaces[0]
        self.seat_info = Gio.DBusNodeInfo.new_for_xml(SEAT_XML).interfaces[0]
        self.session_info = Gio.DBusNodeInfo.new_for_xml(SESSION_XML).interfaces[0]
        self._register_manager()
        self._register_seat()
        self._register_sessions()

    def close(self) -> None:
        """Unregister exported objects and release the well-known bus name."""
        for path, reg_id in list(self.registration_ids.items()):
            self.connection.unregister_object(reg_id)
            self.registration_ids.pop(path, None)

        self.Gio.bus_unown_name(self.owner_id)

    def _on_name_acquired(self, connection: Any, name: str) -> None:
        del connection
        self.name_acquired = True
        logger.info("acquired D-Bus name %s", name)

    def _on_name_lost(self, connection: Any, name: str) -> None:
        del connection
        self.name_acquired = False
        logger.error("lost D-Bus name %s", name)

        if self.loop is not None:
            self.GLib.idle_add(schedule_loop_quit, self.loop)

    def manager_path(self) -> str:
        """Return the fixed manager object path."""
        return MANAGER_PATH

    def current_seat_path(self) -> str:
        """Return the current exported seat object path."""
        return seat_paths(self.snapshot)[0]

    def session_entry(self, path: str) -> dict[str, object]:
        """Return the session payload that belongs to one session object path."""
        sessions = self.snapshot.get("active_sessions", [])

        if not isinstance(sessions, list):
            raise KeyError(path)

        for session in sessions:
            if not isinstance(session, dict):
                continue

            if session_object_path(int(session.get("pid", 0))) == path:
                return session

        raise KeyError(path)

    def _register_object(self, path: str, interface_info: Any) -> None:
        if path in self.registration_ids:
            return

        reg_id = self.connection.register_object(
            path,
            interface_info,
            self._on_method_call,
            self._on_get_property,
            None,
        )
        self.registration_ids[path] = reg_id

    def _unregister_object(self, path: str) -> None:
        reg_id = self.registration_ids.pop(path, None)

        if reg_id is not None:
            self.connection.unregister_object(reg_id)

    def _register_manager(self) -> None:
        self._register_object(self.manager_path(), self.manager_info)

    def _register_seat(self) -> None:
        self._register_object(self.current_seat_path(), self.seat_info)

    def _register_sessions(self) -> None:
        for path in session_paths(self.snapshot):
            self._register_object(path, self.session_info)

    def _emit_signal(self, object_path: str, interface: str, name: str, signature: str, values: tuple[object, ...]) -> None:
        self.connection.emit_signal(None, object_path, interface, name,
                                    self.GLib.Variant(signature, values))

    def _emit_manager_property_changes(self) -> None:
        changed = {
            "Seats": self.GLib.Variant("ao", seat_paths(self.snapshot)),
            "Sessions": self.GLib.Variant("ao", session_paths(self.snapshot)),
        }
        self.connection.emit_signal(None, self.manager_path(),
                                    "org.freedesktop.DBus.Properties",
                                    "PropertiesChanged",
                                    self.GLib.Variant("(sa{sv}as)",
                                                      (MANAGER_INTERFACE, changed, [])))

    def _emit_seat_property_changes(self) -> None:
        changed = {
            "Sessions": self.GLib.Variant("ao", session_paths(self.snapshot)),
        }
        self.connection.emit_signal(None, self.current_seat_path(),
                                    "org.freedesktop.DBus.Properties",
                                    "PropertiesChanged",
                                    self.GLib.Variant("(sa{sv}as)",
                                                      (SEAT_INTERFACE, changed, [])))

    def update_state(self, snapshot: dict[str, object]) -> None:
        """Apply a new daemon snapshot and emit add/remove signals for changes.

        Args:
            snapshot: New daemon state snapshot returned by the internal
                protocol.
        """
        old_seat_paths = seat_paths(self.snapshot)
        old_session_paths = set(session_paths(self.snapshot))

        self.snapshot = dict(snapshot)

        new_seat_paths = seat_paths(self.snapshot)
        new_session_paths = set(session_paths(self.snapshot))

        for path in old_seat_paths:
            if path not in new_seat_paths:
                self._unregister_object(path)
                self._emit_signal(self.manager_path(), MANAGER_INTERFACE, "SeatRemoved", "(o)", (path,))

        for path in new_seat_paths:
            if path not in old_seat_paths:
                self._register_object(path, self.seat_info)
                self._emit_signal(self.manager_path(), MANAGER_INTERFACE, "SeatAdded", "(o)", (path,))

        for path in old_session_paths - new_session_paths:
            self._unregister_object(path)
            self._emit_signal(self.manager_path(), MANAGER_INTERFACE, "SessionRemoved", "(o)", (path,))

        for path in new_session_paths - old_session_paths:
            self._register_object(path, self.session_info)
            self._emit_signal(self.manager_path(), MANAGER_INTERFACE, "SessionAdded", "(o)", (path,))

        self._emit_manager_property_changes()
        self._emit_seat_property_changes()

    def _manager_property(self, property_name: str) -> object:
        if property_name == "Seats":
            return self.GLib.Variant("ao", seat_paths(self.snapshot))

        if property_name == "Sessions":
            return self.GLib.Variant("ao", session_paths(self.snapshot))

        raise KeyError(property_name)

    def _seat_property(self, property_name: str) -> object:
        if property_name == "Id":
            return self.GLib.Variant("s", str(self.snapshot.get("seat", "")))

        if property_name == "Sessions":
            return self.GLib.Variant("ao", session_paths(self.snapshot))

        raise KeyError(property_name)

    def _session_property(self, object_path: str, property_name: str) -> object:
        session = self.session_entry(object_path)

        if property_name == "Id":
            return self.GLib.Variant("s", str(session.get("pid", "")))

        if property_name == "Username":
            return self.GLib.Variant("s", str(session.get("username", "")))

        if property_name == "Seat":
            return self.GLib.Variant("o", self.current_seat_path())

        if property_name == "Class":
            return self.GLib.Variant("s", "user")

        if property_name == "Type":
            return self.GLib.Variant("s", "wayland")

        if property_name == "Active":
            return self.GLib.Variant("b", True)

        raise KeyError(property_name)

    def _on_get_property(self,
                         connection: Any,
                         sender: str,
                         object_path: str,
                         interface_name: str,
                         property_name: str) -> object:
        del connection, sender

        if interface_name == MANAGER_INTERFACE:
            return self._manager_property(property_name)

        if interface_name == SEAT_INTERFACE:
            return self._seat_property(property_name)

        if interface_name == SESSION_INTERFACE:
            return self._session_property(object_path, property_name)

        raise KeyError(interface_name)

    def _on_method_call(self, connection: Any, sender: str, object_path: str,
                        interface_name: str, method_name: str, parameters: object,
                        invocation: Any) -> None:
        del connection, sender, object_path, parameters

        if interface_name == MANAGER_INTERFACE and method_name == "ListSeats":
            invocation.return_value(
                self.GLib.Variant("(ao)",
                                  (seat_paths(self.snapshot),)))
            return

        if interface_name == MANAGER_INTERFACE and method_name == "ListSessions":
            invocation.return_value(
                self.GLib.Variant("(ao)",
                                  (session_paths(self.snapshot),)))
            return

        invocation.return_dbus_error(
            "org.freedesktop.DBus.Error.UnknownMethod",
            f"unknown method {interface_name}.{method_name}")


def schedule_state_update(service: DisplayManagerService, snapshot: dict[str, object]) -> bool:
    """Apply one daemon state update from the GLib main loop context.

    Args:
        service: Exported D-Bus service instance.
        snapshot: New daemon state snapshot.

    Returns:
        ``False`` so ``GLib.idle_add()`` removes the callback after one run.
    """
    service.update_state(snapshot)
    return False


def schedule_loop_quit(loop: Any) -> bool:
    """Stop the GLib main loop from an idle callback.

    Args:
        loop: ``GLib.MainLoop`` instance that should exit.

    Returns:
        ``False`` so ``GLib.idle_add()`` removes the callback after one run.
    """
    loop.quit()
    return False


def read_daemon_events(client: SocketClient,
                       service: DisplayManagerService,
                       GLib: Any,
                       loop: Any) -> None:
    """Consume daemon events and mirror state changes into the D-Bus service.

    Args:
        client: Connected daemon IPC client.
        service: Exported D-Bus service instance.
        GLib: Imported ``GLib`` module.
        loop: ``GLib.MainLoop`` instance that should stop on EOF or failure.
    """
    try:
        while True:
            message = client.read_message()

            if message is None:
                GLib.idle_add(schedule_loop_quit, loop)
                return

            if greeter_protocol.is_event(message, name=greeter_protocol.EVENT_STATE_CHANGED):
                payload = message.get("payload", {})

                if isinstance(payload, dict):
                    GLib.idle_add(schedule_state_update, service, payload)

                continue

            if greeter_protocol.is_event(message, name=greeter_protocol.EVENT_SESSION_STARTING):
                continue

            if greeter_protocol.is_event(message, name=greeter_protocol.EVENT_SESSION_FINISHED):
                continue

            logger.debug("ignoring unexpected adapter message: %r", message)

    except Exception:
        logger.exception("dbus-adapter lost the daemon event stream")
        GLib.idle_add(schedule_loop_quit, loop)


def run_adapter(username: str, uid: int, gid: int, workdir: str, service_name: str) -> int:
    """Run the D-Bus adapter on top of the inherited daemon channel.

    Args:
        username: Target user name for the adapter process.
        uid: Target user id for the adapter process.
        gid: Target group id for the adapter process.
        workdir: Working directory used after dropping privileges.
        service_name: Well-known D-Bus service name to export.

    Returns:
        A shell-style process exit status.
    """
    client = SocketClient(adapter_ipc_fd())

    try:
        wldm.drop_privileges(username, uid, gid, workdir)

        # Keep the optional D-Bus stack out of the privileged part of the
        # adapter so import-time side effects happen only after the uid/gid
        # switch.
        Gio, GLib = load_unprivileged_modules()

        service = DisplayManagerService(service_name, request_state(client), Gio, GLib)

        loop = GLib.MainLoop()
        service.loop = loop

        thread = threading.Thread(
            target=read_daemon_events,
            args=(client, service, GLib, loop),
            daemon=True,
        )
        thread.start()

        try:
            loop.run()

        finally:
            service.close()
            thread.join(timeout=1.0)

        return wldm.EX_SUCCESS

    finally:
        client.close()


def cmd_main(parser: argparse.Namespace) -> int:
    log_path = os.environ.get("WLDM_DBUS_LOG_PATH", "").strip()
    if log_path:
        wldm.setup_file_logger(logger, level=logger.level, fmt="[%(asctime)s] %(message)s", path=log_path)

    try:
        pw = pwd.getpwnam(parser.username)

    except KeyError:
        logger.critical("User '%s' not found.", parser.username)
        return wldm.EX_FAILURE

    try:
        return run_adapter(parser.username, pw.pw_uid, pw.pw_gid, pw.pw_dir, parser.service)

    except RuntimeError as e:
        logger.critical("dbus-adapter startup failed for user=%s service=%s: %s",
                        parser.username, parser.service, e)
        return wldm.EX_FAILURE

    except Exception as e:
        logger.exception("unexpected dbus adapter failure for user=%s service=%s: %s",
                         parser.username, parser.service, e)
        return wldm.EX_FAILURE
