"""Queue task use cases that must not live in route handlers (reply, cancel)."""
from __future__ import annotations

import asyncio
import logging

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import GenerationTaskRecord, ShotRecord
from muse_orchestrator.providers.base import ErrorCategory, ProviderFailure
from muse_orchestrator.services.generation_queue import (
    GenerationQueueService,
    InvalidOperatorAnswerError,
    TaskNotAwaitingInputError,
    TaskNotFoundError,
    TaskStateConflictError,
)

logger = logging.getLogger(__name__)

TASK_NOT_FOUND_MESSAGE = "Task không tồn tại."


class InputNotPendingError(ApplicationError):
    """409: the task is not (or no longer) waiting for operator input.

    ``task`` is the refreshed task when the provider rejected the answer and the local
    state was re-synchronised by stepping it; the caller must schedule its next step.
    """

    def __init__(self, detail: str, task: GenerationTaskRecord | None = None) -> None:
        super().__init__(
            409, detail, category=ErrorCategory.INPUT_NOT_PENDING.value, retryable=False
        )
        self.task = task


# A busy task (poll or answer in flight) is retried briefly before answering 409.
_REPLY_BUSY_ATTEMPTS = 10
_REPLY_BUSY_RETRY_SECONDS = 0.5


class QueueTaskService:
    def __init__(self, repository: StudioRepository) -> None:
        self.repository = repository

    async def reply_to_task(
        self, task_id: str, *, text: str | None = None, choice: str | None = None
    ) -> GenerationTaskRecord:
        """Answer the provider question of an AWAITING_INPUT task (exactly one of text/choice)."""
        if (text is None) == (choice is None):
            raise ApplicationError(422, "Provide exactly one of 'text' or 'choice'.")
        answer = {"text": text} if text is not None else {"choice": choice or ""}
        session = self.repository.session
        try:
            for attempt in range(_REPLY_BUSY_ATTEMPTS):
                try:
                    return await GenerationQueueService.reply_to_task(session, task_id, answer)
                except TaskStateConflictError:
                    # A worker poll (seconds) or another answer holds the task lease.
                    if attempt == _REPLY_BUSY_ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(_REPLY_BUSY_RETRY_SECONDS)
            raise AssertionError("unreachable")
        except TaskStateConflictError as exc:
            raise ApplicationError(409, str(exc), category="conflict", retryable=True) from exc
        except TaskNotFoundError as exc:
            raise ApplicationError(404, TASK_NOT_FOUND_MESSAGE) from exc
        except TaskNotAwaitingInputError as exc:
            raise InputNotPendingError(str(exc)) from exc
        except InvalidOperatorAnswerError as exc:
            raise ApplicationError(422, str(exc)) from exc
        except ProviderFailure as exc:
            if exc.category != ErrorCategory.INPUT_NOT_PENDING:
                raise
            # The provider is no longer waiting (answered elsewhere, timed out, finished):
            # refresh the local state from the provider instead of keeping a stale question.
            refreshed = await self._refresh_after_rejected_answer(task_id)
            raise InputNotPendingError(exc.message, task=refreshed) from exc

    async def _refresh_after_rejected_answer(self, task_id: str) -> GenerationTaskRecord | None:
        try:
            return await GenerationQueueService.step_task(self.repository.session, task_id)
        except (ValueError, ProviderFailure) as exc:
            logger.warning(
                "Could not refresh task %s after a rejected operator answer (%s)",
                task_id,
                type(exc).__name__,
            )
            return None

    async def select_shot_take(self, shot_id: str, take_id: str) -> tuple[ShotRecord, list[str]]:
        """Select a take of a shot; returns the shot and the re-pointed downstream shots.

        404 when the shot is missing, or when the take is not a take of this shot in the
        shot's project (``take.shot_id == shot_id`` and ``take.project_id ==
        shot.project_id``).
        """
        shot = await self.repository.get_shot(shot_id)
        if shot is None:
            raise ApplicationError(404, "Shot không tồn tại.")
        try:
            unlocked = await GenerationQueueService.select_shot_take(self.repository, shot, take_id)
        except LookupError as exc:
            raise ApplicationError(404, "Take không thuộc về Shot này.") from exc
        return shot, unlocked

    async def pause_task(self, task_id: str) -> GenerationTaskRecord:
        try:
            return await GenerationQueueService.pause_task(self.repository.session, task_id)
        except TaskNotFoundError as exc:
            raise ApplicationError(404, TASK_NOT_FOUND_MESSAGE) from exc
        except TaskStateConflictError as exc:
            raise ApplicationError(409, str(exc)) from exc

    async def resume_task(self, task_id: str) -> GenerationTaskRecord:
        try:
            return await GenerationQueueService.resume_task(self.repository.session, task_id)
        except TaskNotFoundError as exc:
            raise ApplicationError(404, TASK_NOT_FOUND_MESSAGE) from exc
        except TaskStateConflictError as exc:
            raise ApplicationError(409, str(exc)) from exc

    async def cancel_task(self, task_id: str) -> GenerationTaskRecord:
        try:
            return await GenerationQueueService.cancel_task(self.repository.session, task_id)
        except TaskNotFoundError as exc:
            raise ApplicationError(404, TASK_NOT_FOUND_MESSAGE) from exc
        except TaskStateConflictError as exc:
            raise ApplicationError(409, str(exc)) from exc
