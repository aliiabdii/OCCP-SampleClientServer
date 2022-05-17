#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import logging
import asyncio
from datetime import datetime

import coloredlogs

import websockets
from websockets.exceptions import WebSocketException

from ocpp.v201 import call
from ocpp.v201 import ChargePoint as cp
from ocpp.v201.enums import RegistrationStatusType, ConnectorStatusType

import settings


coloredlogs.install()


class CSMSClient(cp):
    """
    Holds the core logic of the CSMS server.
    Each method of this class takes care of sending a specific CSMS request
    """
    async def send_boot_notification(self):
        boot_notification_request = call.BootNotificationPayload(
            charging_station={
                "model": "22KW EC Charge",
                "vendor_name": "EnBW"
            },
            reason="PowerUp"
        )
        boot_notification_response = await self.call(boot_notification_request)
        return boot_notification_response

    async def send_status_notification(self):
        status_notification_request = call.StatusNotificationPayload(
            timestamp=datetime.utcnow().isoformat(),
            connector_status=ConnectorStatusType.available,  # getStatus(evse_id, connector_id)
            evse_id=3,
            connector_id=1001,
        )
        return await self.call(status_notification_request)  # no response is expected


class Client:
    """
    Create the websocket connection to exchange messages with the server

    Attributes
    ----------
    _tasks : list[asyncio.tasks.Task]
        The asyncio tasks relating to the WebSocket connections
    cp_id: str
        ID of the Charging Point (client)
    csms_client : CSMSClient
        And instance of CSMSClient
    logger: logging.Logger

    Methods
    -------
    run(username, password):
        Main method of the class that takes care of making the connection to the Server
         through Websocket and exchanging the required messages
    make_handshake():
        Make the initial handshake with the server
    exchange_message(func):
        Exchange message with server via the provided functionality
    handle_boot_notification_response():
        Handle the response from BootNotification request
    """
    def __init__(self, cp_id):
        self.cp_id = cp_id

        self._tasks = []
        self.csms_client = None

        logging.basicConfig(level=settings.LOG_LEVEL,
                            format=settings.LOG_FORMAT)
        self.logger = logging.getLogger(__name__)

    def _create_connection(self, uname, pwd):
        return websockets.connect(
            f"ws://{uname}:{pwd}@{settings.WEBSOCKET_IP}:{settings.WEBSOCKET_PORT}/{self.cp_id}",
            subprotocols=settings.OCPP_SUBPROTOCOLS
        )

    def make_handshake(self):
        self._tasks.append(
            asyncio.ensure_future(self.csms_client.start())
        )

    def handle_boot_notification_response(self, response):
        if response is None:
            raise RuntimeError("No response received with boot notification request.")
        if response.status == RegistrationStatusType.accepted:
            self.logger.info("Boot notification acknowledged!")
        elif response.status == RegistrationStatusType.pending:
            self.logger.info("Boot notification pending!")
        elif response.status == RegistrationStatusType.rejected:
            self.logger.warning("Boot notification rejected!")

    def finish_up(self):
        for task in self._tasks:
            task.cancel()

    async def exchange_message(self, func):
        func_task = asyncio.ensure_future(func())
        self._tasks.append(func_task)
        return await func_task

    async def run(self, username, password):
        try:
            async with self._create_connection(username, password) as con:
                self.csms_client = CSMSClient(self.cp_id, con)

                self.make_handshake()

                # First make the boot notification request
                boot_notification_response = await self.exchange_message(
                    self.csms_client.send_boot_notification
                )
                self.handle_boot_notification_response(boot_notification_response)

                # Then make the status notification request - no response is expected here
                await self.exchange_message(self.csms_client.send_status_notification)

        except WebSocketException:
            self.logger.error("Connection refused from Server. Make sure you entered"
                              " the correct credentials.")
        except RuntimeError:
            self.logger.error("Unknown error happened on server.")

        finally:
            self.finish_up()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    asyncio.run(
        Client("CP_01").run(args.username, args.password)
    )
