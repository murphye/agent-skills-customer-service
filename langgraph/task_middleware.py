"""Task management middleware for LangGraph agents.

Provides TaskCreate, TaskList, TaskGet, and TaskUpdate tools following the same
AgentMiddleware pattern as langchain.agents.middleware.todo.TodoListMiddleware.

Usage::

    from langchain.agents import create_agent
    from task_middleware import TaskMiddleware

    agent = create_agent("anthropic:claude-sonnet-4-6", middleware=[TaskMiddleware()])
    result = await agent.ainvoke({"messages": [HumanMessage("Help me refactor my codebase")]})
    print(result["tasks"])  # list of Task dicts with status tracking
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.runtime import Runtime

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from typing_extensions import NotRequired, TypedDict, override

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    OmitFromInput,
    ResponseT,
)
from langchain.tools import InjectedToolCallId


# ── Task data model ───────────────────────────────────────────────────────────


class Task(TypedDict):
    """A single tracked task."""

    task_id: str
    """Unique short identifier (e.g. '1', '2')."""

    subject: str
    """Brief imperative title (e.g. 'Run tests')."""

    description: str
    """Detailed description of what needs to be done and acceptance criteria."""

    active_form: str
    """Present-continuous label shown while in_progress (e.g. 'Running tests')."""

    status: Literal["pending", "in_progress", "completed", "deleted"]
    """Current status of the task."""

    owner: str
    """Agent or user responsible for this task."""

    metadata: dict[str, Any]
    """Arbitrary key-value metadata."""

    blocks: list[str]
    """IDs of tasks that cannot start until this task completes."""

    blocked_by: list[str]
    """IDs of tasks that must complete before this task can start."""


# ── State schema ──────────────────────────────────────────────────────────────


class TaskState(AgentState[ResponseT]):
    """State schema for the task middleware.

    Type Parameters:
        ResponseT: The type of the structured response. Defaults to ``Any``.
    """

    tasks: Annotated[NotRequired[list[Task]], _tasks_reducer, OmitFromInput]
    """Ordered list of tasks tracking work progress."""


# ── Prompts & descriptions ────────────────────────────────────────────────────

TASK_SYSTEM_PROMPT = """## Task Management Tools

You have access to task management tools (TaskCreate, TaskList, TaskGet, TaskUpdate) \
to help you plan and track complex multi-step work. \
Use them to give the user visibility into your progress.

### When to use task tools
- Complex multi-step tasks (3 or more distinct steps)
- Tasks that require careful planning or have dependencies
- When the user explicitly asks for a task list
- When you discover follow-up work mid-execution

### When NOT to use task tools
- Simple tasks completable in fewer than 3 trivial steps
- Purely conversational or informational requests

### Rules
- **CRITICAL: Never call TaskCreate or TaskUpdate in parallel with any other tool** \
that also writes tasks. Call them one at a time; parallel writes corrupt state.
- Mark a task `in_progress` **before** beginning work on it.
- Mark a task `completed` **immediately** after finishing — never batch completions.
- Only mark `completed` when fully done; keep `in_progress` if blocked.
- Use `blocked_by` to declare dependencies so they appear correctly in TaskList.
- Set status to `deleted` for tasks that become irrelevant.
- Always have at least one task `in_progress` until all work is done."""

TASK_CREATE_DESCRIPTION = """Create a new task to track a unit of work.

Use this to break complex objectives into manageable, trackable steps.
Mark the first task(s) you are about to start as in_progress immediately after creating them.

Args:
    subject:      Brief imperative title (e.g. 'Run tests', 'Fix auth bug').
    description:  Detailed requirements and acceptance criteria.
    active_form:  Present-continuous label shown while in_progress (e.g. 'Running tests').
    metadata:     Optional key-value pairs for extra context."""

TASK_LIST_DESCRIPTION = """List all active tasks (excludes deleted tasks).

Returns a JSON summary of each task: id, subject, status, owner, and open blocked_by dependencies.
Use this to check overall progress or find the next unblocked task to work on.
Prefer working on tasks in ascending ID order when multiple are available."""

TASK_GET_DESCRIPTION = """Get full details for a single task by its ID.

Returns the complete task record including description, owner, metadata, and dependency lists.
Fetch a task before starting it to review its full requirements."""

TASK_UPDATE_DESCRIPTION = """Update an existing task's status, details, or dependencies.

Only provide fields you want to change — omitted fields are left untouched.

Common patterns:
  Start task:       {"task_id": "1", "status": "in_progress"}
  Finish task:      {"task_id": "1", "status": "completed"}
  Add dependency:   {"task_id": "2", "add_blocked_by": ["1"]}
  Delete task:      {"task_id": "3", "status": "deleted"}
  Merge metadata:   {"task_id": "1", "metadata": {"key": "value"}}
  Remove meta key:  {"task_id": "1", "metadata": {"key": null}}

Args:
    task_id:        ID of the task to update (required).
    status:         New status: pending | in_progress | completed | deleted.
    subject:        New brief title.
    description:    New detailed description.
    active_form:    New present-continuous label.
    owner:          New owner name.
    metadata:       Keys to merge in; set a key to null to delete it.
    add_blocks:     Task IDs that this task should block (bidirectional).
    add_blocked_by: Task IDs that must complete before this task (bidirectional)."""


# ── State reducer ─────────────────────────────────────────────────────────────


def _tasks_reducer(
    existing: list[Task] | None, update: list[Task] | None
) -> list[Task]:
    """Merge two full task lists so concurrent tool writes don't raise InvalidUpdateError.

    LangGraph requires a reducer on any channel that may receive more than one
    value in the same step (e.g. TaskCreate + TaskUpdate called in parallel).
    Strategy: union by task_id; the update list's version wins on conflict.
    """
    if not update:
        return existing or []
    if not existing:
        return list(update)
    by_id: dict[str, Task] = {t["task_id"]: t for t in existing}
    for t in update:
        by_id[t["task_id"]] = t
    seen: set[str] = set()
    result: list[Task] = []
    for t in (*existing, *update):
        if t["task_id"] not in seen:
            result.append(by_id[t["task_id"]])
            seen.add(t["task_id"])
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _next_id(tasks: list[Task]) -> str:
    """Return the next available sequential task ID."""
    existing = {t["task_id"] for t in tasks}
    n = len(tasks) + 1
    while str(n) in existing:
        n += 1
    return str(n)


def _is_open(task_id: str, tasks: list[Task]) -> bool:
    """Return True if a task exists and is not completed or deleted."""
    task = next((t for t in tasks if t["task_id"] == task_id), None)
    return task is not None and task["status"] not in ("completed", "deleted")


# ── Middleware ────────────────────────────────────────────────────────────────


class TaskMiddleware(AgentMiddleware[TaskState[ResponseT], ContextT, ResponseT]):
    """Middleware that provides task management capabilities to LangGraph agents.

    Adds four tools — TaskCreate, TaskList, TaskGet, TaskUpdate — that allow
    agents to plan and track complex multi-step work. Task state is persisted in
    the LangGraph thread state under the ``tasks`` key, so it survives across
    turns and is visible in LangGraph Studio.

    The middleware injects a system prompt that guides the agent on when and how
    to use the task tools, mirroring the behaviour of ``TodoListMiddleware``.

    Example::

        from langchain.agents import create_agent
        from task_middleware import TaskMiddleware

        agent = create_agent(
            "anthropic:claude-sonnet-4-6",
            middleware=[TaskMiddleware()],
        )
        result = await agent.ainvoke({"messages": [HumanMessage("Refactor my codebase")]})
        print(result["tasks"])

    Customising prompts::

        agent = create_agent(
            "anthropic:claude-sonnet-4-6",
            middleware=[TaskMiddleware(system_prompt="Use tasks only for 5+ step work.")],
        )
    """

    state_schema = TaskState  # type: ignore[assignment]

    def __init__(
        self,
        *,
        system_prompt: str = TASK_SYSTEM_PROMPT,
        task_create_description: str = TASK_CREATE_DESCRIPTION,
        task_list_description: str = TASK_LIST_DESCRIPTION,
        task_get_description: str = TASK_GET_DESCRIPTION,
        task_update_description: str = TASK_UPDATE_DESCRIPTION,
    ) -> None:
        """Initialise the middleware with optional custom prompts.

        Args:
            system_prompt:            Injected into every model call to guide tool usage.
            task_create_description:  Tool description for TaskCreate.
            task_list_description:    Tool description for TaskList.
            task_get_description:     Tool description for TaskGet.
            task_update_description:  Tool description for TaskUpdate.
        """
        super().__init__()
        self.system_prompt = system_prompt

        # ── TaskCreate ────────────────────────────────────────────────────────

        @tool(description=task_create_description)
        def TaskCreate(  # noqa: N802
            subject: str,
            description: str,
            tasks: Annotated[list[Task] | None, InjectedState("tasks")],
            tool_call_id: Annotated[str, InjectedToolCallId],
            active_form: str = "",
            metadata: dict[str, Any] | None = None,
        ) -> Command[Any]:
            """Create a new task."""
            current: list[Task] = list(tasks or [])
            new_task: Task = {
                "task_id": _next_id(current),
                "subject": subject,
                "description": description,
                "active_form": active_form,
                "status": "pending",
                "owner": "",
                "metadata": metadata or {},
                "blocks": [],
                "blocked_by": [],
            }
            current.append(new_task)
            return Command(
                update={
                    "tasks": current,
                    "messages": [
                        ToolMessage(
                            json.dumps(
                                {
                                    "task_id": new_task["task_id"],
                                    "subject": new_task["subject"],
                                    "status": new_task["status"],
                                }
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        # ── TaskList ──────────────────────────────────────────────────────────

        @tool(description=task_list_description)
        def TaskList(  # noqa: N802
            tasks: Annotated[list[Task] | None, InjectedState("tasks")],
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> Command[Any]:
            """List all active tasks."""
            current: list[Task] = tasks or []
            active = [t for t in current if t["status"] != "deleted"]
            summary = [
                {
                    "id": t["task_id"],
                    "subject": t["subject"],
                    "status": t["status"],
                    "owner": t["owner"],
                    "blocked_by": [b for b in t["blocked_by"] if _is_open(b, current)],
                }
                for t in active
            ]
            return Command(
                update={
                    "messages": [
                        ToolMessage(json.dumps(summary, indent=2), tool_call_id=tool_call_id)
                    ]
                }
            )

        # ── TaskGet ───────────────────────────────────────────────────────────

        @tool(description=task_get_description)
        def TaskGet(  # noqa: N802
            task_id: str,
            tasks: Annotated[list[Task] | None, InjectedState("tasks")],
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> Command[Any]:
            """Get full details for a task."""
            current: list[Task] = tasks or []
            task = next((t for t in current if t["task_id"] == task_id), None)
            content = (
                json.dumps(task, indent=2) if task else f"Task '{task_id}' not found."
            )
            return Command(
                update={
                    "messages": [ToolMessage(content, tool_call_id=tool_call_id)]
                }
            )

        # ── TaskUpdate ────────────────────────────────────────────────────────

        @tool(description=task_update_description)
        def TaskUpdate(  # noqa: N802
            task_id: str,
            tasks: Annotated[list[Task] | None, InjectedState("tasks")],
            tool_call_id: Annotated[str, InjectedToolCallId],
            status: Literal["pending", "in_progress", "completed", "deleted"] | None = None,
            subject: str | None = None,
            description: str | None = None,
            active_form: str | None = None,
            owner: str | None = None,
            metadata: dict[str, Any] | None = None,
            add_blocks: list[str] | None = None,
            add_blocked_by: list[str] | None = None,
        ) -> Command[Any]:
            """Update an existing task."""
            # Work on a shallow copy of each task dict so we don't mutate caller state.
            current: list[dict[str, Any]] = [dict(t) for t in (tasks or [])]  # type: ignore[arg-type]

            idx = next((i for i, t in enumerate(current) if t["task_id"] == task_id), None)
            if idx is None:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                f"Task '{task_id}' not found.",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ]
                    }
                )

            task = current[idx]
            changes: list[str] = []

            if status is not None:
                task["status"] = status
                changes.append(f"status → {status}")
            if subject is not None:
                task["subject"] = subject
                changes.append("subject updated")
            if description is not None:
                task["description"] = description
                changes.append("description updated")
            if active_form is not None:
                task["active_form"] = active_form
                changes.append("active_form updated")
            if owner is not None:
                task["owner"] = owner
                changes.append(f"owner → {owner}")
            if metadata is not None:
                merged: dict[str, Any] = dict(task.get("metadata") or {})
                for k, v in metadata.items():
                    if v is None:
                        merged.pop(k, None)
                    else:
                        merged[k] = v
                task["metadata"] = merged
                changes.append("metadata updated")

            # Bidirectional dependency wiring
            blocks: list[str] = list(task.get("blocks") or [])
            blocked_by: list[str] = list(task.get("blocked_by") or [])

            for bid in add_blocks or []:
                if bid not in blocks:
                    blocks.append(bid)
                    changes.append(f"blocks → +{bid}")
                other_idx = next(
                    (i for i, t in enumerate(current) if t["task_id"] == bid), None
                )
                if other_idx is not None:
                    other_bb: list[str] = list(current[other_idx].get("blocked_by") or [])
                    if task_id not in other_bb:
                        other_bb.append(task_id)
                        current[other_idx]["blocked_by"] = other_bb

            for bid in add_blocked_by or []:
                if bid not in blocked_by:
                    blocked_by.append(bid)
                    changes.append(f"blocked_by → +{bid}")
                other_idx = next(
                    (i for i, t in enumerate(current) if t["task_id"] == bid), None
                )
                if other_idx is not None:
                    other_bl: list[str] = list(current[other_idx].get("blocks") or [])
                    if task_id not in other_bl:
                        other_bl.append(task_id)
                        current[other_idx]["blocks"] = other_bl

            task["blocks"] = blocks
            task["blocked_by"] = blocked_by

            return Command(
                update={
                    "tasks": current,
                    "messages": [
                        ToolMessage(
                            json.dumps(
                                {"task_id": task_id, "changes": changes, "task": task}
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        self.tools = [TaskCreate, TaskList, TaskGet, TaskUpdate]  # type: ignore[assignment]

    # ── Model call hooks ──────────────────────────────────────────────────────

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        """Inject the task management system prompt before the model call."""
        if request.system_message is not None:
            new_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_content = [{"type": "text", "text": self.system_prompt}]
        return handler(
            request.override(
                system_message=SystemMessage(
                    content=cast("list[str | dict[str, str]]", new_content)
                )
            )
        )

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        """Async version of wrap_model_call."""
        if request.system_message is not None:
            new_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_content = [{"type": "text", "text": self.system_prompt}]
        return await handler(
            request.override(
                system_message=SystemMessage(
                    content=cast("list[str | dict[str, str]]", new_content)
                )
            )
        )

    @override
    def after_model(
        self,
        state: TaskState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        """Seed tasks=[] on the first turn so InjectedState("tasks") never hits a KeyError.

        ``tasks`` is OmitFromInput/NotRequired, so it is absent from the state dict
        until a tool first writes it.  LangGraph's ToolNode does ``state["tasks"]``
        (not ``.get``), so we must guarantee the key exists before tools run.
        ``after_model`` fires after every model response but before the tool node,
        making it the right place to initialise the field.
        """
        if "tasks" not in state:
            return {"tasks": []}
        return None

    @override
    async def aafter_model(
        self,
        state: TaskState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        """Async version of after_model."""
        return self.after_model(state, runtime)
