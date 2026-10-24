import { useEffect, useId, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface ConfirmActionDialogProps {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  isSubmitting?: boolean;
  /** Destructive styling for irreversible actions (delete, cleanup, force). */
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Inline confirmation for irreversible or quota-consuming actions; focuses Cancel by default. */
export function ConfirmActionDialog({
  title,
  children,
  confirmLabel,
  isSubmitting = false,
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmActionDialogProps) {
  const { t } = useTranslation();
  const idPrefix = useId();
  const cancelButton = useRef<HTMLButtonElement | null>(null);
  useEffect(() => cancelButton.current?.focus(), []);

  return (
    <div
      role="alertdialog"
      aria-labelledby={`${idPrefix}-title`}
      aria-describedby={children ? `${idPrefix}-body` : undefined}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        onCancel();
      }}
      className={
        destructive
          ? "space-y-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3"
          : "space-y-3 rounded-lg border border-warning/40 bg-warning/5 p-3"
      }
    >
      <p id={`${idPrefix}-title`} className="text-sm font-semibold">
        {title}
      </p>
      {children && (
        <div id={`${idPrefix}-body`} className="space-y-1 text-xs">
          {children}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant={destructive ? "destructive" : "default"}
          onClick={onConfirm}
          disabled={isSubmitting}
        >
          {confirmLabel}
        </Button>
        <Button type="button" size="sm" variant="outline" ref={cancelButton} onClick={onCancel}>
          {t("actions.cancel")}
        </Button>
      </div>
    </div>
  );
}
