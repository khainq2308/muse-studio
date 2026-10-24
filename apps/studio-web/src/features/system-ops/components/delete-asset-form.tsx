import { Trash2 } from "lucide-react";
import { useState } from "react";
import { ConfirmActionDialog } from "@/components/shared/confirm-action-dialog";
import { Button } from "@/components/ui/button";
import { useDeleteAsset } from "@/features/system-ops/api";
import { ActionError } from "@/features/system-ops/components/ops-reports";
import { actionErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

/** Delete one asset by its global ID, with an explicit confirmation (forced or not). */
export function DeleteAssetForm() {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const deleteAsset = useDeleteAsset();
  const [deleteAssetIdInput, setDeleteAssetIdInput] = useState("");
  const [forceDeleteAsset, setForceDeleteAsset] = useState(false);
  const [confirmDeleteAsset, setConfirmDeleteAsset] = useState(false);
  const [deleteAssetStatus, setDeleteAssetStatus] = useState<string | null>(null);

  return (
    <form
      aria-label="Delete asset by ID"
      className="space-y-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!deleteAssetIdInput.trim() || deleteAsset.isPending) return;
        setDeleteAssetStatus(null);
        setConfirmDeleteAsset(true);
      }}
    >
      <p className="text-xs text-muted-foreground">
        The Orchestrator has no asset listing endpoint, so assets are deleted by ID. Asset IDs are
        global: the asset is removed from whichever project owns it.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          aria-label="Asset ID to delete"
          value={deleteAssetIdInput}
          onChange={(event) => {
            setDeleteAssetIdInput(event.target.value);
            setConfirmDeleteAsset(false);
          }}
          placeholder="Asset ID to delete"
          className="h-9 w-full min-w-0 rounded-lg border border-input bg-background px-3 font-mono text-xs sm:flex-1"
        />
        <label className="flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={forceDeleteAsset}
            onChange={(event) => {
              setForceDeleteAsset(event.target.checked);
              setConfirmDeleteAsset(false);
            }}
            className="rounded border-input"
          />
          <span>Force</span>
        </label>
        <Button
          type="submit"
          variant="destructive"
          size="sm"
          disabled={!deleteAssetIdInput.trim() || deleteAsset.isPending || confirmDeleteAsset}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          Delete asset
        </Button>
      </div>
      {confirmDeleteAsset && deleteAssetIdInput.trim() && (
        <ConfirmActionDialog
          title={
            forceDeleteAsset
              ? `Force delete asset ${deleteAssetIdInput.trim()}?`
              : `Delete asset ${deleteAssetIdInput.trim()}?`
          }
          confirmLabel={
            deleteAsset.isPending
              ? "Deleting..."
              : forceDeleteAsset
                ? "Force delete asset"
                : "Delete asset"
          }
          destructive
          isSubmitting={deleteAsset.isPending}
          onConfirm={() => {
            const assetId = deleteAssetIdInput.trim();
            const force = forceDeleteAsset;
            deleteAsset.mutate(
              { assetId, force },
              {
                onSuccess: () => {
                  setDeleteAssetIdInput("");
                  setConfirmDeleteAsset(false);
                  setForceDeleteAsset(false);
                  setDeleteAssetStatus(`Asset ${assetId} was ${force ? "force " : ""}deleted.`);
                },
              },
            );
          }}
          onCancel={() => setConfirmDeleteAsset(false)}
        >
          {forceDeleteAsset ? (
            <p>
              Force deletes even if referenced by takes/shots, Story Bible entries or timeline
              clips; those references are detached. The file is removed permanently.
            </p>
          ) : (
            <p>
              The asset file is removed permanently. The delete is refused if takes, shots or
              timeline clips still reference it.
            </p>
          )}
          <p>The asset is removed from whichever project owns it, not only one project.</p>
        </ConfirmActionDialog>
      )}
      {deleteAsset.isError && (
        <ActionError
          message={actionErrorMessage(deleteAsset.error, activeAdminToken, "delete the asset")}
        />
      )}
      {deleteAssetStatus && !deleteAsset.isError && (
        <p role="status" className="text-xs text-success">
          {deleteAssetStatus}
        </p>
      )}
    </form>
  );
}
