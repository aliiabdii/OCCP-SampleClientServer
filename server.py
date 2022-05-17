#!/usr/bin/env python
# -*- coding: utf-8 -*-

import http
import logging
import asyncio
from datetime import datetime

import coloredlogs

import websockets
from websockets.headers import build_authorization_basic
from websockets.exceptions import WebSocketException

from ocpp.routing import on
from ocpp.v201 import ChargePoint
from ocpp.v201 import call_result
from ocpp.v201.enums import Action, RegistrationStatusType

import settings


coloredlogs.install()


# Hard-coded credentials
USERNAME = "test"
PASSWORD = "123"


class CSMSServer(ChargePoint):
    """
    Holds the core logic of the CSMS server.
    Each method of this class takes care of responding to a specific CSMS request
    """
    @on(Action.BootNotification)
    async def on_boot_notification(self, **kwargs):
        try:
            # TODO: Reboot the Charging Station with the provided variables
            interval = 10
            status = RegistrationStatusType.accepted
        except RuntimeError:
            interval = 60
            status = RegistrationStatusType.rejected
        except TimeoutError:
            interval = 120
            status = RegistrationStatusType.pending

        return call_result.BootNotificationPayload(
            current_time=datetime.utcnow().isoformat(),
            interval=interval,
            status=status
        )

    @on(Action.StatusNotification)
    async def on_status_notification(self, **kwargs):
        # TODO: Update the status for this Charging Point on DB
        return call_result.StatusNotificationPayload()


class Server:
    """
    Create the websocket server to handle the incoming requests.

    Attributes
    ----------
    _task : asyncio.tasks.Task
        The asyncio task relating to the WebSocket server
    csms_server : CSMSServer
        And instance of CSMSServer
    loop : asyncio event loop
    logger: logging.Logger

    Methods
    -------
    worker(websocket, path):
        Async method which handle the incoming connections.
    reject_handshake(http_status, msg):
        Reject handshake if unauthorized, etc...
    start_server():
        Start the Websocket server
    """
    def __init__(self):
        self._task = None

        self.csms_server = None
        self.loop = asyncio.get_event_loop()
        logging.basicConfig(level=settings.LOG_LEVEL,
                            format=settings.LOG_FORMAT,
                            )
        self.logger = logging.getLogger(__name__)

    def reject_handshake(self, http_status, msg):
        self.logger.error("%s - Connection closed.", msg)
        return http_status, [], msg.encode()

    async def worker(self, websocket, path):
        # Authenticating the connection
        try:
            auth_header = websocket.request_headers["Authorization"]
            assert auth_header == build_authorization_basic(USERNAME, PASSWORD)
        except KeyError:
            return self.reject_handshake(http.HTTPStatus.UNAUTHORIZED, "Access denied.")
        except AssertionError:
            return self.reject_handshake(http.HTTPStatus.FORBIDDEN, "Invalid Username/Password.")

        # Validating the protocol
        try:
            requested_protocols = websocket.request_headers["Sec-WebSocket-Protocol"]
        except KeyError:
            return self.reject_handshake(http.HTTPStatus.BAD_REQUEST, "Subprotocol missing.")

        if websocket.subprotocol:
            self.logger.info("Protocols Matched: %s", websocket.subprotocol)
        else:
            msg = f"Protocols Mismatched, expected Subprotocols" \
                  f" {websocket.available_subprotocols}, but received {requested_protocols}."
            return self.reject_handshake(http.HTTPStatus.BAD_REQUEST, msg)

        # Starting up the server
        charge_point_id = path.strip("/")
        self.csms_server = CSMSServer(charge_point_id, websocket)

        try:
            await self.csms_server.start()
        except WebSocketException as ex:
            self.stop_server(ex)

    def start_server(self):
        self._task = asyncio.ensure_future(
            websockets.serve(
                self.worker,
                # Since we want to bind the server to the current machine, we set this statically
                "0.0.0.0",
                settings.WEBSOCKET_PORT,
                subprotocols=settings.OCPP_SUBPROTOCOLS
            )
        )

    def stop_server(self, exception):
        self._task.cancel()
        logging.warning("Connection to charging point %s closed: %s",
                        self.csms_server.id, exception)

    def run(self):
        try:
            self.start_server()
            self.loop.run_forever()
        finally:
            self.loop.close()


if __name__ == "__main__":
    Server().run()
