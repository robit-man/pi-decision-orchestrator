import asyncio
import copy
import json
import sqlite3
import hashlib
import time

from backend.decisions.policies import evaluate_policy
from backend.decisions.schema import Invocation, Policy
from backend.decisions.validation import (
    condition_matches,
    lookup,
    normalize,
    validate_data,
    validate_workflow,
)
from backend.errors import DomainError
from backend.inference.ollama_chat import OllamaChat
from backend.inference.ollama_decision import OllamaDecision
from backend.security.vault import redact
from backend.storage.db import new_id


class ExecutionEngine:
    def __init__(self, settings, store, models, connections, monitor, client, model_lock):
        self.settings, self.store, self.models, self.connections = settings, store, models, connections
        self.monitor, self.client, self.model_lock = monitor, client, model_lock
        self.queue = asyncio.Queue(maxsize=settings.queue_size)
        self.futures = {}
        self.running_id, self.running_task = None, None
        self.benchmarking = False
        self.worker = None
        self.paused = False
        self.storage_error = False
        self.debounce = {}

    def start(self):
        self.worker = asyncio.create_task(self._worker(), name="decision-worker")

    def config(self, decision_id, version=None, draft=False):
        current = self.store.get("decision", decision_id)
        if not draft and (not current.get("enabled") or not current.get("published_version")):
            raise DomainError(
                "workflow_disabled",
                "Enable and publish this workflow before executing it. Drafts can be tested.",
                409,
            )
        if version is not None or not draft:
            version = version or current["published_version"]
            return self.store.get("version", f"{decision_id}:{version}")["config"]
        return current

    async def submit(
        self,
        decision_id: str,
        invocation: Invocation,
        *,
        actor="operator",
        draft=False,
        chain=None,
        snapshot=None,
    ):
        if self.paused and not draft:
            raise DomainError("workflows_paused", "Workflow execution is paused.", 503)
        if self.benchmarking and actor != "benchmark":
            raise DomainError(
                "benchmark_running", "Inference is reserved for a benchmark. Retry after it finishes.", 429
            )
        chain = list(chain or [])
        if decision_id in chain or len(chain) >= 5:
            raise DomainError(
                "recursive_workflow", "Recursive or excessively deep workflow execution was rejected.", 409
            )
        config = (
            snapshot
            if snapshot is not None and actor == "benchmark"
            else self.config(decision_id, invocation.version, draft)
        )
        workflow, _ = validate_workflow(config, publish=not draft)
        validate_data(invocation.state, workflow.input_schema)
        if invocation.model:
            workflow.model = invocation.model
            from backend.inference.catalog import catalog_entry

            workflow.adapter = catalog_entry(invocation.model).get("adapter", workflow.adapter)
        if invocation.images:
            from backend.inference.catalog import catalog_entry

            caps = catalog_entry(workflow.model)["capabilities"]
            if not {"vision", "audio"}.intersection(caps):
                raise DomainError("unsupported_modality", "This model does not support image or audio input.")
            from backend.decisions.media import validate_media

            validate_media(invocation.images)
        key = f"{decision_id}:{invocation.idempotency_key}" if invocation.idempotency_key else None
        if key:
            existing = self.store.one("SELECT id FROM executions WHERE idempotency_key=?", (key,))
            if existing:
                return self.execution(existing["id"])
        if self.queue.full():
            raise DomainError(
                "queue_full", "The inference queue is full. Retry after pending decisions finish.", 429
            )
        execution_id, created = new_id(), time.time()
        self.store.execute(
            "INSERT INTO executions(id,decision_id,version,status,dry_run,created,model,input,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                execution_id,
                decision_id,
                workflow.version,
                "queued",
                int(invocation.dry_run),
                created,
                workflow.model,
                json.dumps(redact(invocation.state) if workflow.logging else {}, allow_nan=False),
                key,
            ),
        )
        future = asyncio.get_running_loop().create_future()
        self.futures[execution_id] = future
        self.queue.put_nowait(
            (execution_id, decision_id, workflow, invocation, chain + [decision_id], created)
        )
        self.store.event("execution", {"id": execution_id, "decision_id": decision_id, "status": "queued"})
        self.store.audit(actor, "decision.test" if invocation.dry_run else "decision.execute", execution_id)
        if invocation.wait:
            try:
                return await asyncio.wait_for(asyncio.shield(future), workflow.timeout_s + 2)
            except TimeoutError:
                self.cancel(execution_id, timed_out=True)
                return self.execution(execution_id)
        return self.execution(execution_id)

    def execution(self, execution_id):
        row = self.store.one("SELECT * FROM executions WHERE id=?", (execution_id,))
        if not row:
            raise DomainError("not_found", "Execution not found", 404)
        row["input"] = json.loads(row["input"])
        row["result"] = json.loads(row["result"]) if row["result"] else None
        row["dry_run"] = bool(row["dry_run"])
        return row

    def cancel(self, execution_id, timed_out=False):
        execution = self.execution(execution_id)
        if execution["status"] not in {"queued", "running"}:
            return execution
        status = "timed_out" if timed_out else "cancelled"
        self.store.execute(
            "UPDATE executions SET status=?,finished=?,error=? WHERE id=?",
            (
                status,
                time.time(),
                "Workflow deadline exceeded" if timed_out else "Cancelled by operator",
                execution_id,
            ),
        )
        if self.running_id == execution_id and self.running_task:
            self.running_task.cancel()
        future = self.futures.get(execution_id)
        if future and not future.done():
            future.set_result(self.execution(execution_id))
        self.store.event("execution", {"id": execution_id, "status": status})
        return self.execution(execution_id)

    async def _worker(self):
        while True:
            item = await self.queue.get()
            execution_id, decision_id, workflow, invocation, chain, created = item
            self.running_id = execution_id
            try:
                self.storage_error = False
                if self.execution(execution_id)["status"] != "queued":
                    continue
                remaining = workflow.timeout_s - (time.time() - created)
                if remaining <= 0:
                    raise TimeoutError
                self.store.execute(
                    "UPDATE executions SET status='running',started=? WHERE id=?", (time.time(), execution_id)
                )
                self.store.event("execution", {"id": execution_id, "status": "running"})
                self.running_task = asyncio.create_task(
                    self._execute(execution_id, decision_id, workflow, invocation, chain)
                )
                result = await asyncio.wait_for(self.running_task, remaining)
                status = "completed" if result["policy"]["accepted"] else "abstained"
                self.store.execute(
                    "UPDATE executions SET status=?,finished=?,latency_ms=?,inference_ms=?,model_digest=?,result=? WHERE id=?",
                    (
                        status,
                        time.time(),
                        (time.time() - created) * 1000,
                        result["inference_ms"],
                        result.get("model_digest", ""),
                        json.dumps(redact(result), allow_nan=False),
                        execution_id,
                    ),
                )
            except asyncio.CancelledError:
                if self.worker and self.worker.cancelling():
                    raise
                if self.execution(execution_id)["status"] == "running":
                    self.cancel(execution_id)
            except (Exception, TimeoutError) as error:
                code = (
                    error.code
                    if isinstance(error, DomainError)
                    else "timeout"
                    if isinstance(error, TimeoutError)
                    else "execution_failed"
                )
                message = (
                    error.message
                    if isinstance(error, DomainError)
                    else "Workflow deadline exceeded"
                    if isinstance(error, TimeoutError)
                    else "Execution failed safely. Check the workflow configuration and provider status."
                )
                result = {
                    "answers": {},
                    "actions": [],
                    "policy": {"accepted": False, "reasons": [message], "fallback": workflow.policy.fallback},
                    "error_code": code,
                    "trace": [{"type": "error", "status": "handled", "message": message}],
                    "inference_ms": 0,
                }
                handlers = [n for n in workflow.nodes if n.type == "error"]
                if handlers:
                    result["policy"]["fallback"] = handlers[0].config.get(
                        "fallback", workflow.policy.fallback
                    )
                    result["trace"].append(
                        {
                            "id": handlers[0].id,
                            "name": handlers[0].name,
                            "type": "error",
                            "status": "handled",
                            "message": message,
                        }
                    )
                try:
                    self.store.execute(
                        "UPDATE executions SET status=?,finished=?,latency_ms=?,error=?,result=? WHERE id=?",
                        (
                            "timed_out" if isinstance(error, TimeoutError) else "failed",
                            time.time(),
                            (time.time() - created) * 1000,
                            message,
                            json.dumps(result),
                            execution_id,
                        ),
                    )
                except sqlite3.Error:
                    self.storage_error = True
            finally:
                self.running_id, self.running_task = None, None
                future = self.futures.pop(execution_id, None)
                try:
                    execution = self.execution(execution_id)
                    if self.storage_error:
                        execution = {
                            **execution,
                            "status": "failed",
                            "error": "Storage unavailable; result could not be persisted.",
                        }
                    if future and not future.done():
                        future.set_result(execution)
                    self.store.event(
                        "execution",
                        {"id": execution_id, "decision_id": decision_id, "status": execution["status"]},
                    )
                except sqlite3.Error:
                    self.storage_error = True
                    if future and not future.done():
                        future.set_exception(
                            DomainError(
                                "storage_unavailable",
                                "Storage is unavailable. Execution was stopped safely.",
                                503,
                            )
                        )
                finally:
                    self.queue.task_done()

    async def _infer(self, workflow, state, images, config):
        model, provider = config.get("model", workflow.model), config.get("provider", workflow.provider)
        mode = config.get("adapter", workflow.adapter)
        questions = config.get("questions") or {
            name: question.model_dump(exclude_none=True) for name, question in workflow.questions.items()
        }
        async with self.model_lock:
            if provider == "ollama":
                digest, _ = await self.models.digest(model)
                self.monitor.permit()
                adapter = (
                    OllamaDecision(self.client, self.settings.ollama_url)
                    if mode == "decision"
                    else OllamaChat(self.client, self.settings.ollama_url, tools=mode == "tools")
                )
            else:
                adapter = self.connections.provider_adapter(provider, mode)
                digest = "not_reported_by_remote_provider"
                self.monitor.permit()
            result = await adapter.infer(
                model, state, questions, images=images, keep_alive=workflow.keep_alive
            )
            result["model_digest"] = result.get("model_digest") or digest
            return result

    async def _execute(self, execution_id, decision_id, workflow, invocation, chain):
        state = copy.deepcopy(invocation.state)
        answers, agreement, trace, actions, inference = {}, [], [], [], []
        context = {
            "input": state,
            "state": state,
            "answers": answers,
            "execution": {"id": execution_id, "decision_id": decision_id},
            "accepted": False,
        }
        policy = {
            "accepted": False,
            "reasons": ["No evaluation node ran"],
            "fallback": workflow.policy.fallback,
        }
        active_policy, evaluated, action_failed = workflow.policy, False, False

        def current_policy():
            calibration = None
            if active_policy.calibration_dataset_id:
                from backend.decisions.evaluation import configuration_digest

                for item in self.store.find(
                    "evaluation", dataset_id=active_policy.calibration_dataset_id, workflow_id=decision_id
                ):
                    if item.get("configuration_digest") == configuration_digest(
                        workflow.model_dump()
                    ) and item.get("model_digest") == (inference[-1]["model_digest"] if inference else None):
                        dataset = self.store.get("dataset", active_policy.calibration_dataset_id)
                        if item.get("dataset_revision") == dataset["revision"]:
                            calibration = item
                            break
            result = evaluate_policy(active_policy, answers, context, agreement, calibration)
            if not all(condition_matches(c, context) for c in workflow.conditions):
                result = {
                    **result,
                    "accepted": False,
                    "reasons": result["reasons"] + ["A prerequisite condition did not match"],
                }
            return result

        nodes = {node.id: node for node in workflow.nodes}
        outgoing = {
            node.id: [edge for edge in workflow.edges if edge.source == node.id] for node in workflow.nodes
        }
        entry = next(node.id for node in workflow.nodes if node.type == "input")
        pending, visited = [entry], set()
        ranks = {}

        def rank(node_id):
            if node_id not in ranks:
                parents = [e.source for e in workflow.edges if e.target == node_id]
                ranks[node_id] = 1 + max((rank(p) for p in parents), default=-1)
            return ranks[node_id]

        for node_id in nodes:
            rank(node_id)
        while pending:
            pending.sort(key=lambda key: ranks[key])
            node_id = pending.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            node, started, label = nodes[node_id], time.monotonic(), ""
            config = node.config
            match node.type:
                case "input":
                    pass
                case "transform":
                    state = normalize(state, config.get("transforms", workflow.transforms))
                    context["state"] = state
                    context["accepted"] = False
                case "condition":
                    matched = all(
                        condition_matches(condition, context) for condition in config.get("conditions", [])
                    )
                    label = "true" if matched else "false"
                case "inference":
                    if not all(condition_matches(c, context) for c in workflow.conditions):
                        policy = {
                            "accepted": False,
                            "reasons": ["A prerequisite condition did not match"],
                            "fallback": workflow.policy.fallback,
                        }
                        break
                    result = await self._infer(workflow, state, invocation.images, config)
                    answers.update(result["answers"])
                    agreement.append(result["answers"])
                    inference.append(result)
                    context["accepted"] = False
                case "evaluate" | "gate":
                    active_policy = (
                        Policy.model_validate(config["policy"]) if config.get("policy") else workflow.policy
                    )
                    evaluated = True
                    policy = current_policy()
                    context["accepted"] = policy["accepted"]
                    label = "accepted" if policy["accepted"] else "uncertain"
                case "branch":
                    value = lookup(context, config.get("path", "accepted"))
                    label = str(value).lower() if isinstance(value, bool) else str(value)
                case "action":
                    if workflow.output_schema:
                        validate_data(answers, workflow.output_schema)
                    if evaluated and not action_failed:
                        policy = current_policy()
                        context["accepted"] = policy["accepted"]
                    selected = [
                        action
                        for action in workflow.actions
                        if not config.get("action_ids") or action.id in config["action_ids"]
                    ]
                    for action in selected:
                        if (
                            action.question
                            and str(answers.get(action.question, {}).get("selected")).lower()
                            != action.outcome.lower()
                        ):
                            actions.append(
                                {
                                    "id": action.id,
                                    "name": action.name,
                                    "status": "skipped",
                                    "reason": "Outcome did not match",
                                }
                            )
                            continue
                        # Authorization is independent of model output and is rechecked at dispatch.
                        if invocation.dry_run or not policy["accepted"] or not action.authorized:
                            reason = (
                                "Simulation mode"
                                if invocation.dry_run
                                else "Acceptance policy did not pass"
                                if not policy["accepted"]
                                else "Action is not authorized"
                            )
                            actions.append(
                                {
                                    "id": action.id,
                                    "name": action.name,
                                    "status": "proposed" if invocation.dry_run else "rejected",
                                    "reason": reason,
                                }
                            )
                            continue
                        try:
                            dispatched = await self.connections.execute_action(action, context, chain)
                            actions.append({"id": action.id, "name": action.name, **dispatched})
                        except DomainError as error:
                            actions.append(
                                {
                                    "id": action.id,
                                    "name": action.name,
                                    "status": "failed",
                                    "reason": error.message,
                                }
                            )
                            if action.failure_policy == "stop":
                                action_failed = True
                                policy = {
                                    **policy,
                                    "accepted": False,
                                    "reasons": policy["reasons"] + ["An authorized output action failed"],
                                }
                                context["accepted"] = False
                                break
                case "delay":
                    seconds = float(config.get("seconds", 0))
                    if not 0 <= seconds <= 30:
                        raise DomainError("delay_range", "Delay nodes are limited to 30 seconds.")
                    if config.get("debounce_path"):
                        key = (
                            decision_id,
                            node.id,
                            hashlib.sha256(
                                json.dumps(lookup(context, config["debounce_path"]), sort_keys=True).encode()
                            ).hexdigest(),
                        )
                        now = time.monotonic()
                        self.debounce = {k: expiry for k, expiry in self.debounce.items() if expiry > now}
                        if key in self.debounce:
                            policy = {
                                "accepted": False,
                                "reasons": ["Repeated input suppressed by debounce"],
                                "fallback": workflow.policy.fallback,
                            }
                            evaluated = False
                            break
                        if len(self.debounce) >= 4096:
                            self.debounce.pop(next(iter(self.debounce)))
                        self.debounce[key] = now + seconds
                    await asyncio.sleep(seconds)
                case "log":
                    self.store.event(
                        "workflow_log",
                        {"execution_id": execution_id, "node_id": node.id, "accepted": policy["accepted"]},
                    )
                case "error":
                    label = "armed"
            trace.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "type": node.type,
                    "status": "completed",
                    "outcome": label,
                    "duration_ms": (time.monotonic() - started) * 1000,
                }
            )
            pending.extend(
                edge.target for edge in outgoing[node_id] if not edge.condition or edge.condition == label
            )
        if evaluated and not action_failed:
            policy = current_policy()
        if workflow.output_schema:
            validate_data(answers, workflow.output_schema)
        return {
            "answers": answers,
            "normalized_state": redact(state) if workflow.logging else None,
            "transforms": [item.model_dump() for item in workflow.transforms],
            "policy": policy,
            "trace": trace,
            "actions": actions,
            "models": [
                {"model": item["model"], "digest": item["model_digest"], "inference_ms": item["inference_ms"]}
                for item in inference
            ],
            "model_digest": inference[-1]["model_digest"] if inference else "",
            "inference_ms": sum(item["inference_ms"] for item in inference),
            "source": workflow.input_source,
        }

    async def close(self):
        if self.running_id:
            self.cancel(self.running_id)
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        while not self.queue.empty():
            item = self.queue.get_nowait()
            self.cancel(item[0])
            self.queue.task_done()
