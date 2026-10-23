"""Queue task use cases that must not live in route handlers (reply, cancel)."""
from __future__ import annotations

import logging

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import GenerationTaskRecord
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
            return await GenerationQueueService.reply_to_task(session, task_id, answer)
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

    async def cancel_task(self, task_id: str) -> GenerationTaskRecord:
        try:
            return await GenerationQueueService.cancel_task(self.repository.session, task_id)
        except TaskNotFoundError as exc:
            raise ApplicationError(404, TASK_NOT_FOUND_MESSAGE) from exc
        except TaskStateConflictError as exc:
            raise ApplicationError(409, str(exc)) from exc
