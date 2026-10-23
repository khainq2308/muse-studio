import { useId, useState, type FormEvent } from "react";
import { MessageCircleQuestionMark, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  QUEUE_REPLY_TEXT_MAX_CHARS,
  pendingInputAcceptsText,
  pendingInputAskedAt,
  queuePendingInputKindLabel,
  replyableOptions,
  type QueuePendingInput,
  type QueueTaskReply,
} from "@/features/queue-monitor/state";
import { cn } from "@/lib/utils";

interface QueueTaskQuestionProps {
  taskId: string;
  pendingInput: QueuePendingInput;
  /** A reply or another action for this task is in flight. */
  busy: boolean;
  /** Error from the latest reply attempt for this task, if any. */
  error: string | null;
  /** Send the reply; `onSuccess` runs only when the backend accepted it. */
  onReply: (reply: QueueTaskReply, onSuccess: () => void) => void;
}

/**
 * The provider's question for an AWAITING_INPUT task. Provider text is rendered as plain text
 * (React escapes it); never inject it as HTML.
 */
export function QueueTaskQuestion({
  taskId,
  pendingInput,
  busy,
  error,
  onReply,
}: QueueTaskQuestionProps) {
  const [draft, setDraft] = useState("");
  const headingId = useId();
  const textId = useId();
  const replyId = useId();
  const errorId = useId();

  const options = replyableOptions(pendingInput);
  const acceptsText = pendingInputAcceptsText(pendingInput) || options.length === 0;
  const askedAt = pendingInputAskedAt(pendingInput.asked_at);
  const trimmedDraft = draft.trim();
  const canSendText =
    !busy && trimmedDraft.length > 0 && trimmedDraft.length <= QUEUE_REPLY_TEXT_MAX_CHARS;

  const submitText = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSendText) return;
    onReply({ text: trimmedDraft }, () => setDraft(""));
  };

  return (
    <section
      aria-labelledby={headingId}
      aria-describedby={textId}
      data-testid={`queue-task-question-${taskId}`}
      aria-busy={busy}
      className="mt-4 rounded-lg border border-warning/40 bg-warning/5 p-4"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h4 id={headingId} className="flex items-center gap-2 text-sm font-semibold text-warning">
          <MessageCircleQuestionMark className="size-4" aria-hidden="true" />
          {queuePendingInputKindLabel(pendingInput.kind)}
        </h4>
        <span className="text-xs text-muted-foreground">
          {askedAt ? (
            <>
              Asked <time dateTime={askedAt.toISOString()}>{askedAt.toLocaleString()}</time>
            </>
          ) : (
            "Asked time unknown"
          )}
        </span>
      </div>

      <p id={textId} className="mt-2 whitespace-pre-wrap break-words text-sm">
        {pendingInput.text || "The provider did not include question text."}
      </p>

      {options.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Answer options">
          {options.map((option) => (
            <Button
              key={option}
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              aria-describedby={error ? errorId : undefined}
              onClick={() => onReply({ choice: option }, () => setDraft(""))}
            >
              {option}
            </Button>
          ))}
        </div>
      )}

      {acceptsText && (
        <form className="mt-3 space-y-2" onSubmit={submitText}>
          <label htmlFor={replyId} className="text-xs font-medium text-muted-foreground">
            {options.length > 0 ? "Or write an answer" : "Your answer"}
          </label>
          <textarea
            id={replyId}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={QUEUE_REPLY_TEXT_MAX_CHARS}
            rows={3}
            disabled={busy}
            aria-invalid={Boolean(error) || undefined}
            aria-describedby={error ? errorId : undefined}
            className={cn(
              "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          />
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs tabular-nums text-muted-foreground">
              {draft.length}/{QUEUE_REPLY_TEXT_MAX_CHARS}
            </span>
            <Button type="submit" size="sm" disabled={!canSendText}>
              <Send className="mr-1 size-4" aria-hidden="true" />
              Send answer
            </Button>
          </div>
        </form>
      )}

      {error && (
        <p
          id={errorId}
          role="alert"
          className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}
    </section>
  );
}
