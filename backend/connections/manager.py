import asyncio
import copy
import json
import time

from backend.connections.http import HTTPConnection
from backend.connections.mqtt import MQTTLink
from backend.connections.schema import Connection
from backend.decisions.schema import Invocation
from backend.decisions.validation import assign, lookup, validate_data
from backend.errors import DomainError
from backend.inference.ollama_chat import OllamaChat
from backend.inference.ollama_decision import OllamaDecision
from backend.security.vault import redact


def map_payload(value, context):
    if isinstance(value, str) and value.startswith("$."):
        return copy.deepcopy(lookup(context, value))
    if isinstance(value, dict):
        return {key: map_payload(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [map_payload(item, context) for item in value]
    return value


class ConnectionManager:
    def __init__(self, store, vault, client):
        self.store, self.vault, self.client = store, vault, client
        self.http = HTTPConnection(client, vault)
        self.engine = None
        self.tasks, self.links = {}, {}
        self.message_ids = {}
        self.outbound = asyncio.Semaphore(2)
        self.dispatch_times = []

    def get(self, connection_id):
        value = self.store.get("connection", connection_id)
        value["has_credentials"] = bool(self.vault.get(connection_id))
        return value

    def save(self, data, connection_id=None, credentials=None):
        # Runtime health is stored separately; it cannot be overwritten by browser input.
        connection = Connection.model_validate(data)
        if connection.allow_output and connection.direction not in {"output", "both"}:
            raise DomainError(
                "connection_direction", "Only output connections can authorize external actions."
            )
        if credentials is not None:
            if not isinstance(credentials, dict) or any(
                key not in {"token", "username", "password", "webhook_key"} for key in credentials
            ):
                raise DomainError("invalid_credentials", "Unrecognized credential fields.")
            if any(not isinstance(value, str) or len(value) > 4096 for value in credentials.values()):
                raise DomainError("invalid_credentials", "Credential values must be bounded strings.")
        with self.store.transaction():
            result = self.store.put(
                "connection", connection.model_dump(exclude={"revision"}), connection_id, connection.revision
            )
            if credentials is not None:
                self.vault.set(result["id"], credentials)
        self.store.event("connections_changed", {"id": result["id"]})
        return self.get(result["id"])

    def health(self, connection_id, *, success=False, error=None):
        old = self.store.one(
            "SELECT payload FROM objects WHERE kind='connection_health' AND id=?", (connection_id,)
        )
        current = json.loads(old["payload"]) if old else {}
        self.store.put(
            "connection_health",
            {
                "name": connection_id,
                "status": "healthy" if success else "error",
                "last_success": time.time() if success else current.get("last_success"),
                "last_error": error,
                "checked_at": time.time(),
            },
            connection_id,
        )

    async def read(self, connection_id):
        connection = self.get(connection_id)
        if not connection["enabled"]:
            raise DomainError("connection_disabled", "Enable the connection before reading it.", 409)
        if connection["type"] in {"manual", "json"}:
            value = connection["data"]
        elif connection["type"] in {"http", "poll", "agent"}:
            value = await self.http.read(connection)
        else:
            raise DomainError(
                "connection_input", "This connection receives events instead of supporting on-demand reads."
            )
        validate_data(value, connection["input_schema"])
        self.health(connection_id, success=True)
        return self.map_input(connection, value)

    @staticmethod
    def map_input(connection, value):
        if not connection.get("input_mapping"):
            return value
        result = {}
        for target, source in connection["input_mapping"].items():
            assign(result, target, lookup(value, source))
        return result

    async def test(self, connection_id):
        connection = self.get(connection_id)
        try:
            if connection["type"] in {"manual", "json"}:
                validate_data(connection["data"], connection["input_schema"])
                message = "Local input passes its schema."
            elif connection["type"] == "webhook":
                if not self.vault.get(connection_id).get("webhook_key"):
                    raise DomainError("webhook_secret", "Set a webhook authentication key first.")
                message = "Authenticated webhook is configured. Send a payload to test end to end."
            elif connection["type"] == "mqtt":
                link = MQTTLink(connection, self.vault.get(connection_id))
                try:
                    await link.open()
                    message = "The broker accepted the connection. No message was published."
                finally:
                    link.close()
            else:
                response = await self.http.request(connection, method="HEAD", testing=True)
                if response.status_code >= 400 and response.status_code != 405:
                    raise DomainError(
                        "connection_status", f"Destination returned HTTP {response.status_code}.", 502
                    )
                message = f"Destination reached (HTTP {response.status_code}). No output action was sent."
            self.health(connection_id, success=True)
            return {"success": True, "message": message}
        except DomainError as error:
            self.health(connection_id, error=error.message)
            raise

    def provider_adapter(self, connection_id, mode):
        connection = self.get(connection_id)
        if not connection["enabled"] or not connection["type"].startswith("provider_"):
            raise DomainError("provider_disabled", "Select an enabled inference provider connection.", 409)

        async def transport(method, path, payload):
            return await self.http.request(connection, method=method, payload=payload, suffix=path)

        if mode == "decision":
            if connection["type"] != "provider_ollama":
                raise DomainError(
                    "provider_capability", "Native decisions require an Ollama decision provider."
                )
            return OllamaDecision(self.client, connection["endpoint"], transport)
        return OllamaChat(
            self.client,
            connection["endpoint"],
            tools=mode == "tools",
            transport=transport,
            openai=connection["type"] in {"provider_openai", "provider_llamacpp"},
        )

    async def ingest(self, connection_id, value, key=None, duplicate=False):
        connection = self.get(connection_id)
        if not connection["enabled"] or connection["direction"] not in {"input", "both"}:
            raise DomainError("connection_disabled", "This input connection is disabled.", 409)
        validate_data(value, connection["input_schema"])
        if duplicate and key and (connection_id, key) in self.message_ids:
            return []
        if key:
            if len(self.message_ids) >= 4096:
                self.message_ids.pop(next(iter(self.message_ids)))
            self.message_ids[(connection_id, key)] = time.monotonic()
            self.message_ids = {
                item: at for item, at in self.message_ids.items() if time.monotonic() - at < 300
            }
        state = self.map_input(connection, value)
        executions = []
        for decision_id in connection["decision_ids"]:
            executions.append(
                await self.engine.submit(
                    decision_id,
                    Invocation(
                        state=state,
                        dry_run=False,
                        wait=False,
                        idempotency_key=f"{connection_id}:{key}" if key else None,
                    ),
                    actor="connection:" + connection_id,
                )
            )
        self.health(connection_id, success=True)
        return executions

    async def execute_action(self, action, context, chain):
        if not action.authorized:
            raise DomainError("action_unauthorized", "Explicit action authorization is required.", 403)
        payload = map_payload(action.payload, context)
        validate_data(payload, action.input_schema)
        key = context["execution"]["id"] + ":" + action.id
        existing = self.store.one("SELECT status FROM action_keys WHERE key=?", (key,))
        if existing:
            return {
                "status": "skipped",
                "reason": "Idempotency key already dispatched",
                "idempotency_key": key,
            }
        now = time.monotonic()
        self.dispatch_times = [at for at in self.dispatch_times if at > now - 60]
        if len(self.dispatch_times) >= 60:
            raise DomainError("action_rate_limit", "The outbound action limit is 60 per minute.", 429)
        self.dispatch_times.append(now)
        self.store.execute("INSERT INTO action_keys VALUES(?,'dispatching',?)", (key, time.time()))
        try:
            async with self.outbound, asyncio.timeout(action.timeout_s):
                match action.type:
                    case "event" | "notify":
                        self.store.event(
                            "notification" if action.type == "notify" else "action_event",
                            {
                                "execution_id": context["execution"]["id"],
                                "action": action.name,
                                "payload": redact(payload),
                            },
                        )
                        result = {"status": "executed"}
                    case "response":
                        result = {"status": "executed", "response": redact(payload)}
                    case "decision":
                        child = await self.engine.submit(
                            action.decision_id,
                            Invocation(state=payload, dry_run=False, wait=False, idempotency_key=key),
                            actor="workflow",
                            chain=chain,
                        )
                        result = {"status": "executed", "child_execution_id": child["id"]}
                    case "http" | "webhook" | "mqtt":
                        connection = self.get(action.connection_id)
                        if (
                            not connection["enabled"]
                            or not connection["allow_output"]
                            or connection["direction"] not in {"output", "both"}
                        ):
                            raise DomainError(
                                "output_unauthorized",
                                "The destination connection is not explicitly authorized for output.",
                                403,
                            )
                        connection["timeout_s"] = min(connection["timeout_s"], action.timeout_s)
                        connection["retries"] = min(connection["retries"], action.retries)
                        validate_data(payload, connection["output_schema"])
                        if action.type == "mqtt":
                            if connection["type"] != "mqtt":
                                raise DomainError(
                                    "connection_type", "MQTT actions require an MQTT connection."
                                )
                            link = MQTTLink(connection, self.vault.get(connection["id"]))
                            try:
                                await link.publish(payload)
                            finally:
                                link.close()
                            response_data = {"published": True}
                        else:
                            response = await self.http.request(connection, payload=payload, key=key)
                            if not 200 <= response.status_code < 300:
                                raise DomainError(
                                    "action_http_status",
                                    f"The destination returned HTTP {response.status_code}.",
                                    502,
                                )
                            try:
                                response_data = response.json()
                            except ValueError:
                                response_data = {"status_code": response.status_code}
                        validate_data(response_data, action.output_schema)
                        self.health(connection["id"], success=True)
                        result = {
                            "status": "executed",
                            "response": self.vault.scrub(connection["id"], response_data),
                        }
            self.store.execute(
                "UPDATE action_keys SET status='completed',updated=? WHERE key=?", (time.time(), key)
            )
            return {**result, "idempotency_key": key}
        except TimeoutError:
            self.store.execute(
                "UPDATE action_keys SET status='uncertain',updated=? WHERE key=?", (time.time(), key)
            )
            raise DomainError(
                "action_timeout",
                "The action timed out; its remote completion is uncertain. It will not be automatically redispatched.",
                504,
            ) from None
        except BaseException:
            self.store.execute(
                "UPDATE action_keys SET status='failed',updated=? WHERE key=?", (time.time(), key)
            )
            raise

    async def _run_input(self, connection_id):
        while True:
            connection = self.get(connection_id)
            try:
                if connection["type"] == "mqtt":

                    async def received(value, key, duplicate):
                        try:
                            await self.ingest(connection_id, value, key, duplicate)
                        except DomainError as error:
                            self.health(connection_id, error=error.message)

                    link = MQTTLink(connection, self.vault.get(connection_id), received)
                    self.links[connection_id] = link
                    await link.open()
                    self.health(connection_id, success=True)
                    while link.client and link.client.is_connected():
                        await asyncio.sleep(3)
                    link.close()
                    raise DomainError("mqtt_disconnected", "The MQTT connection dropped; reconnecting.", 502)
                value = await self.read(connection_id)
                await self.ingest(connection_id, value)
            except asyncio.CancelledError:
                raise
            except DomainError as error:
                self.health(connection_id, error=error.message)
            await asyncio.sleep(connection["poll_interval_s"])

    async def reload(self):
        await self.close()
        for connection in self.store.find("connection"):
            if (
                connection["enabled"]
                and connection["type"] in {"poll", "mqtt"}
                and connection["direction"] in {"input", "both"}
            ):
                self.tasks[connection["id"]] = asyncio.create_task(
                    self._run_input(connection["id"]), name="input-" + connection["id"]
                )

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        for link in self.links.values():
            link.close()
        self.links.clear()
