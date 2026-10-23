import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  EXPORT_STATUS_POLL_INTERVAL_MS,
  type ExportJobStatus,
  type TimelineSequence,
} from "@/features/timeline-editor/api";
import { TimelineEditorPage } from "@/features/timeline-editor/pages/timeline-editor-page";

// Exercises the real hooks, polling policy and API client; only fetch is stubbed.

const TIMELINE_PATH = "/api/v1/projects/proj-1/timeline";
const ASSEMBLE_PATH = "/api/v1/projects/proj-1/timeline/assemble";
const EXPORTS_PATH = "/api/v1/projects/proj-1/timeline/exports";
const EXPORT_STATUS_PATH = "/api/v1/projects/proj-1/timeline/exports/exp-1";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeTimeline(): TimelineSequence {
  return {
    sequence_id: "seq-1",
    name: "Master Cut v1",
    resolution: "1920x1080",
    fps: 24,
    aspect_ratio: "16:9",
    total_duration_seconds: 5,
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
      total_duration_seconds: 5,
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
  } as TimelineSequence;
}

function makeExportStatus(overrides: Partial<ExportJobStatus> = {}): ExportJobStatus {
  return {
    export_id: "exp-1",
    sequence_id: "seq-1",
    state: "running",
    aspect_ratio: "16:9",
    fps: 24,
    resolution: "1920x1080",
    video_codec: "h264",
    audio_codec: "aac",
    unsupported_features: [],
    output_asset_id: null,
    ...overrides,
  };
}

type Handler = (init: RequestInit | undefined) => Response;

/** Routes stubbed fetch calls by "METHOD /path"; unknown routes fail the test loudly. */
function stubApi(routes: Record<string, Handler>) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input));
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    const handler = routes[key];
    if (!handler) throw new Error(`Unexpected request: ${key}`);
    return handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock: ReturnType<typeof stubApi>, method: string, path: string) {
  return fetchMock.mock.calls.filter(([input, init]) => {
    return (init?.method ?? "GET") === method && new URL(String(input)).pathname === path;
  });
}

function mount() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TimelineEditorPage projectId="proj-1" />
    </QueryClientProvider>,
  );
  return queryClient;
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  // Let fetch/Response.json promises, query notifications and re-renders settle.
  for (let tick = 0; tick < 3; tick += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

/** Mounts with an existing timeline, launches an export and lets the first status fetch run. */
async function launchExport(statusResponses: Array<() => Response>) {
  let statusCall = 0;
  const fetchMock = stubApi({
    [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()),
    [`POST ${EXPORTS_PATH}`]: () =>
      json({
        export_id: "exp-1",
        sequence_id: "seq-1",
        state: "queued",
        aspect_ratio: "16:9",
        fps: 24,
        resolution: "1920x1080",
        unsupported_features: [],
      }),
    [`GET ${EXPORT_STATUS_PATH}`]: () => {
      const respond = statusResponses[Math.min(statusCall, statusResponses.length - 1)]!;
      statusCall += 1;
      return respond();
    },
  });
  const queryClient = mount();
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: /Launch Verified MP4 Export/i }));
  await advance(0);
  return { fetchMock, queryClient };
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("TimelineEditorPage timeline loading", () => {
  it("renders clips and warnings from GET /timeline", async () => {
    const fetchMock = stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()) });
    mount();
    await advance(0);

    expect(callsTo(fetchMock, "GET", TIMELINE_PATH)).toHaveLength(1);
    expect(screen.getByText(/Shot: shot-1/i)).toBeInTheDocument();
    expect(screen.getByText(/Shot 2 has no selected take yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Launch Verified MP4 Export/i })).toBeEnabled();
  });

  it("shows the empty state, not an error, when the timeline returns 404", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404) });
    mount();
    await advance(0);

    expect(screen.getByText(/No timeline assembled for this project yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Launch Verified MP4 Export/i })).toBeDisabled();
  });

  it("shows an auth alert instead of the empty state when the timeline returns 401", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Unauthorized" }, 401) });
    mount();
    await advance(0);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Admin token required to read project timeline.",
    );
    expect(screen.queryByText(/No timeline assembled/i)).not.toBeInTheDocument();
  });

  it("shows a load alert instead of the empty state when the timeline returns 500", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json({ detail: "boom" }, 500) });
    mount();
    await advance(0);

    expect(screen.getByRole("alert")).toHaveTextContent(/Could not load project timeline/);
    expect(screen.queryByText(/No timeline assembled/i)).not.toBeInTheDocument();
  });
});

describe("TimelineEditorPage assemble", () => {
  it("clamps the cross-dissolve duration to the 3s schema maximum in the request body", async () => {
    const fetchMock = stubApi({
      [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404),
      [`POST ${ASSEMBLE_PATH}`]: () => json({ detail: "not under test" }, 500),
    });
    mount();
    await advance(0);

    fireEvent.change(screen.getByLabelText("Default transition"), {
      target: { value: "cross_dissolve" },
    });
    const durationInput = screen.getByLabelText("Transition duration (s)");
    expect(durationInput).toHaveAttribute("max", "3");
    expect(durationInput).toHaveAttribute("min", "0");
    fireEvent.change(durationInput, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /Assemble \/ Refresh Timeline/i }));
    await advance(0);

    const [call] = callsTo(fetchMock, "POST", ASSEMBLE_PATH);
    expect(call).toBeDefined();
    const body = JSON.parse(String(call![1]!.body)) as Record<string, unknown>;
    expect(body.default_transition).toBe("cross_dissolve");
    expect(body.transition_duration_seconds).toBe(3);
  });

  it("raises a cross-dissolve duration below the minimum and sends 0 for hard cuts", async () => {
    const fetchMock = stubApi({
      [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404),
      [`POST ${ASSEMBLE_PATH}`]: () => json({ detail: "not under test" }, 500),
    });
    mount();
    await advance(0);
    const assemble = () =>
      fireEvent.click(screen.getByRole("button", { name: /Assemble \/ Refresh Timeline/i }));

    assemble();
    await advance(0);
    fireEvent.change(screen.getByLabelText("Default transition"), {
      target: { value: "cross_dissolve" },
    });
    fireEvent.change(screen.getByLabelText("Transition duration (s)"), {
      target: { value: "-1" },
    });
    assemble();
    await advance(0);

    const bodies = callsTo(fetchMock, "POST", ASSEMBLE_PATH).map(
      ([, init]) => JSON.parse(String(init!.body)) as Record<string, unknown>,
    );
    expect(bodies.map((body) => body.transition_duration_seconds)).toEqual([0, 0.2]);
  });

  it("surfaces the server validation detail when assemble fails", async () => {
    stubApi({
      [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404),
      [`POST ${ASSEMBLE_PATH}`]: () => json({ detail: "Project has no shots" }, 422),
    });
    mount();
    await advance(0);

    fireEvent.click(screen.getByRole("button", { name: /Assemble \/ Refresh Timeline/i }));
    await advance(0);

    expect(screen.getByRole("alert")).toHaveTextContent(
      /Could not assemble the timeline: .*Project has no shots/,
    );
  });
});

describe("TimelineEditorPage audio and subtitle limits", () => {
  it("does not reserve a BGM track by default and edits subtitles in a multi-line field", async () => {
    const fetchMock = stubApi({
      [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404),
      [`POST ${ASSEMBLE_PATH}`]: () => json({ detail: "not under test" }, 500),
    });
    mount();
    await advance(0);

    const subtitles = screen.getByLabelText(/Subtitle cues/i);
    expect(subtitles.tagName).toBe("TEXTAREA");
    fireEvent.change(subtitles, { target: { value: "1\n00:00:00,000 --> 00:00:02,000\nHi" } });
    fireEvent.click(screen.getByRole("button", { name: /Assemble \/ Refresh Timeline/i }));
    await advance(0);

    const [call] = callsTo(fetchMock, "POST", ASSEMBLE_PATH);
    const body = JSON.parse(String(call![1]!.body)) as Record<string, unknown>;
    expect(body.audio_tracks).toEqual([]);
    expect(body.subtitle_content).toBe("1\n00:00:00,000 --> 00:00:02,000\nHi");

    fireEvent.click(screen.getByLabelText(/Reserve a background music track/i));
    expect(screen.getByText(/does not mix audio tracks/i)).toBeInTheDocument();
  });

  it("lists features the exporter will skip before the export is launched", async () => {
    const timeline = makeTimeline();
    timeline.manifest.has_subtitles = true;
    timeline.manifest.audio_tracks = [
      {
        name: "Main Score Track",
        track_type: "music",
        volume: 0.8,
        fade_in_seconds: 0.5,
        fade_out_seconds: 1,
        muted: false,
        asset_id: null,
      },
    ];
    const fetchMock = stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(timeline) });
    mount();
    await advance(0);

    const skipped = screen.getByTestId("export-skipped-features");
    expect(skipped).toHaveTextContent(/clip audio/);
    expect(skipped).toHaveTextContent(/subtitles/);
    expect(skipped).toHaveTextContent(/audio tracks/);
    expect(skipped).not.toHaveTextContent(/title card/);
    expect(callsTo(fetchMock, "POST", EXPORTS_PATH)).toHaveLength(0);
  });
});

describe("TimelineEditorPage export", () => {
  it("keeps the resolution consistent with the selected aspect ratio", async () => {
    const fetchMock = stubApi({
      [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()),
      [`POST ${EXPORTS_PATH}`]: () => json({ detail: "not under test" }, 500),
    });
    mount();
    await advance(0);

    fireEvent.change(screen.getByLabelText("Aspect ratio"), { target: { value: "9:16" } });
    const resolution = screen.getByLabelText("Resolution");
    expect(resolution).toHaveValue("1080x1920");
    const offered = Array.from(resolution.querySelectorAll("option")).map((o) => o.value);
    expect(offered).toEqual(["1080x1920", "720x1280"]);

    fireEvent.change(resolution, { target: { value: "720x1280" } });
    fireEvent.click(screen.getByRole("button", { name: /Launch Verified MP4 Export/i }));
    await advance(0);

    const [call] = callsTo(fetchMock, "POST", EXPORTS_PATH);
    expect(JSON.parse(String(call![1]!.body))).toMatchObject({
      resolution: "720x1280",
      aspect_ratio: "9:16",
    });
  });

  it("posts the export request for the loaded sequence", async () => {
    const { fetchMock, queryClient } = await launchExport([
      () => json(makeExportStatus({ state: "succeeded" })),
    ]);

    const [call] = callsTo(fetchMock, "POST", EXPORTS_PATH);
    expect(JSON.parse(String(call![1]!.body))).toEqual({
      sequence_id: "seq-1",
      resolution: "1920x1080",
      aspect_ratio: "16:9",
      fps: 24,
    });
    queryClient.clear();
  });

  it("shows an auth message when launching the export returns 401", async () => {
    stubApi({
      [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()),
      [`POST ${EXPORTS_PATH}`]: () => json({ detail: "Unauthorized" }, 401),
    });
    mount();
    await advance(0);

    fireEvent.click(screen.getByRole("button", { name: /Launch Verified MP4 Export/i }));
    await advance(0);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Admin token required to launch the timeline export.",
    );
  });

  it.each(["succeeded", "failed", "canceled"] as const)(
    "stops polling export status once the job is %s",
    async (terminalState) => {
      const { fetchMock, queryClient } = await launchExport([
        () => json(makeExportStatus({ state: "running" })),
        () => json(makeExportStatus({ state: terminalState })),
      ]);
      expect(callsTo(fetchMock, "GET", EXPORT_STATUS_PATH)).toHaveLength(1);

      await advance(EXPORT_STATUS_POLL_INTERVAL_MS + 100);
      expect(callsTo(fetchMock, "GET", EXPORT_STATUS_PATH)).toHaveLength(2);
      expect(screen.getByLabelText("Export job status")).toHaveTextContent(terminalState);

      await advance(EXPORT_STATUS_POLL_INTERVAL_MS * 5);
      expect(callsTo(fetchMock, "GET", EXPORT_STATUS_PATH)).toHaveLength(2);
      queryClient.clear();
    },
  );

  it("stops polling and shows an alert when a status refetch fails after a running state", async () => {
    const { fetchMock, queryClient } = await launchExport([
      () => json(makeExportStatus({ state: "running" })),
      () => json({ detail: "boom" }, 500),
    ]);

    await advance(EXPORT_STATUS_POLL_INTERVAL_MS + 100);
    expect(callsTo(fetchMock, "GET", EXPORT_STATUS_PATH)).toHaveLength(2);
    expect(screen.getByRole("alert")).toHaveTextContent(/Could not load export job status/);
    expect(screen.getByRole("alert")).toHaveTextContent(/may be out of date/);

    await advance(EXPORT_STATUS_POLL_INTERVAL_MS * 5);
    expect(callsTo(fetchMock, "GET", EXPORT_STATUS_PATH)).toHaveLength(2);
    queryClient.clear();
  });

  it("shows an alert instead of hiding the panel when the first status fetch fails", async () => {
    const { fetchMock, queryClient } = await launchExport([() => json({ detail: "boom" }, 500)]);

    expect(screen.getByRole("alert")).toHaveTextContent(/Could not load export job status/);
    expect(screen.getByRole("button", { name: "Retry status check" })).toBeEnabled();

    await advance(EXPORT_STATUS_POLL_INTERVAL_MS * 5);
    expect(callsTo(fetchMock, "GET", EXPORT_STATUS_PATH)).toHaveLength(1);
    queryClient.clear();
  });

  it.each([
    ["running with no asset", makeExportStatus({ state: "running", output_asset_id: null })],
    ["succeeded with no asset", makeExportStatus({ state: "succeeded", output_asset_id: null })],
    ["failed with an asset id", makeExportStatus({ state: "failed", output_asset_id: "asset-9" })],
  ])("hides the download button when the export is %s", async (_label, status) => {
    const { queryClient } = await launchExport([() => json(status)]);

    expect(screen.getByLabelText("Export job status")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download Verified MP4/i })).toBeNull();
    queryClient.clear();
  });

  it("shows the download button only for a succeeded export with an asset and reports download errors", async () => {
    const { fetchMock, queryClient } = await launchExport([
      () => json(makeExportStatus({ state: "succeeded", output_asset_id: "asset-9" })),
    ]);
    const downloadPath = "/api/v1/assets/asset-9/download";
    fetchMock.mockImplementation(async (input, init) => {
      const url = new URL(String(input));
      if ((init?.method ?? "GET") === "GET" && url.pathname === downloadPath) {
        return json({ detail: "Asset file missing" }, 404);
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });

    fireEvent.click(screen.getByRole("button", { name: /Download Verified MP4 \(asset-9\)/i }));
    await advance(0);

    expect(callsTo(fetchMock, "GET", downloadPath)).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Could not download the exported MP4: .*Asset file missing/,
    );
    queryClient.clear();
  });
});
