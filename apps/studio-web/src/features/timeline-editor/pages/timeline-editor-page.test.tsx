import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TimelineEditorPage } from "@/features/timeline-editor/pages/timeline-editor-page";

afterEach(() => cleanup());

function renderWithQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("TimelineEditorPage", () => {
  it("renders assembled timeline clips and warnings when sequence exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              sequence_id: "seq-1",
              name: "Master Cut v1",
              resolution: "1920x1080",
              fps: 24,
              aspect_ratio: "16:9",
              total_duration_seconds: 8.5,
              warnings: [
                {
                  code: "MISSING_SELECTED_TAKE",
                  message: "Shot 2 has no selected take yet.",
                  severity: "warning",
                  shot_id: "shot-2",
                },
              ],
              manifest: {
                project_id: "proj-1",
                sequence_id: "seq-1",
                resolution: "1920x1080",
                fps: 24,
                aspect_ratio: "16:9",
                total_duration_seconds: 8.5,
                has_subtitles: false,
                subtitle_format: "srt",
                title_card: {},
                video_clips: [
                  {
                    order_index: 0,
                    shot_id: "shot-1",
                    take_id: "take-1",
                    asset_id: "asset-1",
                    start_time_seconds: 0,
                    source_duration_seconds: 5,
                    trim_start_seconds: 0,
                    trim_end_seconds: 0,
                    effective_duration_seconds: 5,
                    transition_type: "hard_cut",
                    transition_duration_seconds: 0,
                    fade_in_seconds: 0,
                    fade_out_seconds: 0,
                    volume: 1,
                  },
                ],
                audio_tracks: [],
              },
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
      ),
    );

    renderWithQueryClient(<TimelineEditorPage projectId="proj-1" />);

    expect(await screen.findByText(/Shot: shot-1/i)).toBeInTheDocument();
    expect(screen.getByText(/Shot 2 has no selected take yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Launch Verified MP4 Export/i })).toBeEnabled();
  });
});
