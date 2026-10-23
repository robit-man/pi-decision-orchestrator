import asyncio
import json
import socket
import ssl
import threading

import paho.mqtt.client as mqtt

from backend.errors import DomainError
from backend.security.destinations import parse_destination, resolve_destination


class PinnedClient(mqtt.Client):
    def __init__(self, address, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pinned_address = address

    def _create_socket_connection(self):
        # TLS still receives self._host, while the TCP connection uses the vetted IP.
        return socket.create_connection((self.pinned_address, self._port), timeout=self._connect_timeout)


class MQTTLink:
    def __init__(self, config, secrets, callback=None, health=None):
        self.config, self.secrets, self.callback, self.health = config, secrets, callback, health
        self.client = None
        self.connected = threading.Event()
        self.failure = None

    async def open(self):
        pinned, host, port = await resolve_destination(
            self.config["endpoint"], self.config, ("mqtt", "mqtts")
        )
        parsed, _ = parse_destination(pinned, ("mqtt", "mqtts"))
        client = PinnedClient(parsed.hostname, mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        self.client = client
        client.max_queued_messages_set(16)
        client.max_inflight_messages_set(1)
        client.connect_timeout = self.config["timeout_s"]
        if self.secrets.get("username"):
            client.username_pw_set(self.secrets["username"], self.secrets.get("password"))
        if self.config["tls"]:
            client.tls_set_context(ssl.create_default_context())
        loop = asyncio.get_running_loop()

        def connected(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                self.failure = "The MQTT broker rejected authentication or the connection."
            elif self.callback:
                client.subscribe(self.config["topic"], qos=self.config["qos"])
            self.connected.set()

        pending = 0
        guard = threading.Lock()

        async def deliver(value, key, duplicate):
            nonlocal pending
            try:
                await self.callback(value, key, duplicate)
            finally:
                with guard:
                    pending -= 1

        def message(client, userdata, msg):
            nonlocal pending
            if len(msg.payload) > 262144:
                return
            try:
                value = json.loads(msg.payload)
            except (ValueError, UnicodeError):
                return
            with guard:
                if pending >= 16:
                    return
                pending += 1
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(deliver(value, str(msg.mid), bool(msg.dup)))
            )

        client.on_connect, client.on_message = connected, message
        try:
            await asyncio.to_thread(client.connect, host, port, 30)
            client.loop_start()
            ready = await asyncio.to_thread(self.connected.wait, self.config["timeout_s"])
            if not ready or self.failure:
                raise DomainError(
                    "mqtt_connection",
                    self.failure or "The MQTT broker did not acknowledge the connection.",
                    502,
                )
        except (OSError, ssl.SSLError, ValueError):
            self.close()
            raise DomainError(
                "mqtt_connection", "MQTT connection failed. Check destination, TLS, and credentials.", 502
            ) from None
        return self

    async def publish(self, payload):
        if not self.client:
            await self.open()
        result = self.client.publish(self.config["topic"], json.dumps(payload), qos=self.config["qos"])
        try:
            await asyncio.to_thread(result.wait_for_publish, self.config["timeout_s"])
        except (RuntimeError, ValueError):
            raise DomainError("mqtt_publish", "The broker did not acknowledge publication.", 502) from None
        if not result.is_published():
            raise DomainError("mqtt_publish", "MQTT publication timed out.", 502)

    def close(self):
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
            self.client = None
