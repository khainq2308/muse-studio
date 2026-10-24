import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

type Clip = TimelineSequence["manifest"]["video_clips"][number];

/** A 13s, three-clip sequence (4s + 5s + 4s), matching the e2e fixture's shape. */
function makeThreeClipTimeline(sequenceId = "seq-1"): TimelineSequence {
  const timeline = makeTimeline();
  const base = timeline.manifest.video_clips[0]!;
  const clip = (index: number, start: number, duration: number): Clip => ({
    ...base,
    order_index: index,
    shot_id: `shot-${index + 1}`,
    take_id: `take-${index + 1}`,
    start_time_seconds: start,
    source_duration_seconds: duration,
    effective_duration_seconds: duration,
  });
  timeline.sequence_id = sequenceId;
  timeline.manifest.sequence_id = sequenceId;
  timeline.total_duration_seconds = 13;
  timeline.manifest.total_duration_seconds = 13;
  timeline.manifest.video_clips = [clip(0, 0, 4), clip(1, 4, 5), clip(2, 9, 4)];
  return timeline;
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
  fireEvent.click(screen.getByRole("button", { name: /^Export MP4$/ }));
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
    expect(screen.getByRole("button", { name: /^Export MP4$/ })).toBeEnabled();
  });

  it("shows the empty state, not an error, when the timeline returns 404", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404) });
    mount();
    await advance(0);

    expect(screen.getByText(/No timeline assembled for this project yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Export MP4$/ })).toBeDisabled();
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

describe("TimelineEditorPage honest sequence view", () => {
  it("states the export carries a silent AAC track and does not claim shot audio", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()) });
    mount();
    await advance(0);

    const audio = screen.getByTestId("timeline-audio-summary");
    expect(audio).toHaveTextContent(/silent AAC track/);
    expect(audio).toHaveTextContent(/Shot audio is not included/);
    expect(screen.queryByText(/Default shot audio stream active/i)).toBeNull();
  });

  it("renders no subtitle lane or fake cue when the timeline has no subtitles", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()) });
    mount();
    await advance(0);

    expect(screen.queryByTestId("timeline-subtitle-lane")).toBeNull();
    expect(screen.queryByText(/Cue #/)).toBeNull();
    expect(screen.queryByText(/Opening Sequence/)).toBeNull();
  });

  it("renders one honest subtitle lane when subtitles are saved", async () => {
    const timeline = makeThreeClipTimeline();
    timeline.manifest.has_subtitles = true;
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(timeline) });
    mount();
    await advance(0);

    const lanes = screen.getAllByTestId("timeline-subtitle-lane");
    expect(lanes).toHaveLength(1);
    expect(lanes[0]).toHaveTextContent("Subtitles saved (SRT) — not rendered into MP4");
    expect(screen.queryByText(/Cue #/)).toBeNull();
  });

  it("derives ruler ticks from the sequence duration", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeThreeClipTimeline()) });
    mount();
    await advance(0);

    const labels = screen.getAllByTestId("timeline-ruler-tick").map((tick) => tick.textContent);
    expect(labels).toEqual([
      "00:00:00",
      "00:00:02",
      "00:00:04",
      "00:00:06",
      "00:00:08",
      "00:00:10",
      "00:00:12",
    ]);
    expect(labels).not.toContain("00:00:25");
    expect(screen.getByText(/Total 00:00:13 \(3 clips\)/)).toBeInTheDocument();
  });

  it("shows no fabricated format, color space or monitor data before a timeline exists", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404) });
    mount();
    await advance(0);

    expect(screen.queryByText(/Rec\. ?709/i)).toBeNull();
    expect(screen.queryByText(/1920x1080 · 24\s?fps/)).toBeNull();
    expect(screen.queryByText(/^Full$/)).toBeNull();
    expect(screen.queryByRole("button", { name: /zoom/i })).toBeNull();
    expect(screen.getByLabelText("Selected clip")).toHaveTextContent(/Assemble a timeline/);
    expect(screen.getByRole("heading", { name: "Timeline & Export" })).toBeVisible();
    expect(screen.queryByText(/not available yet/i)).toBeNull();
    expect(screen.getByText(/adjust clips on the timeline/i)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Clip edits" })).toBeNull();
  });

  it("selects a clip through an accessible toggle button", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeThreeClipTimeline()) });
    mount();
    await advance(0);

    const first = screen.getByRole("button", { name: /Shot: shot-1/ });
    const second = screen.getByRole("button", { name: /Shot: shot-2/ });
    expect(first).toHaveAttribute("type", "button");
    expect(first).toHaveAttribute("aria-pressed", "true");
    expect(second).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(second);

    expect(first).toHaveAttribute("aria-pressed", "false");
    expect(second).toHaveAttribute("aria-pressed", "true");
    const details = screen.getByLabelText("Selected clip");
    expect(details).toHaveTextContent("take-2");
    expect(details).toHaveTextContent("00:00:04:00");
  });

  it("resets the clip selection when a different sequence is loaded", async () => {
    let current = makeThreeClipTimeline("seq-1");
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(current) });
    const queryClient = mount();
    await advance(0);

    fireEvent.click(screen.getByRole("button", { name: /Shot: shot-3/ }));
    expect(screen.getByLabelText("Selected clip")).toHaveTextContent("take-3");

    current = makeThreeClipTimeline("seq-2");
    await act(async () => {
      await queryClient.invalidateQueries();
    });
    await advance(0);

    expect(screen.getByRole("button", { name: /Shot: shot-1/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("Selected clip")).toHaveTextContent("take-1");
  });
});

describe("TimelineEditorPage export contents and layout", () => {
  it("lists what the MP4 will contain from the saved clips and the chosen format", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeThreeClipTimeline()) });
    mount();
    await advance(0);

    const contents = screen.getByTestId("export-contents");
    expect(contents).toHaveTextContent("3 video clips in timeline order");
    expect(contents).toHaveTextContent("a cross dissolve is shortened");
    expect(contents).toHaveTextContent("1920x1080 at 24 fps");
    expect(contents).toHaveTextContent("a silent audio track");
    fireEvent.change(screen.getByLabelText("Resolution"), { target: { value: "1280x720" } });
    expect(contents).toHaveTextContent("1280x720 at 24 fps");
  });

  it("hides the contents and blocks export when the sequence has no clips", async () => {
    const empty = makeTimeline();
    empty.manifest.video_clips = [];
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(empty) });
    mount();
    await advance(0);

    expect(screen.queryByTestId("export-contents")).toBeNull();
    expect(screen.getByRole("button", { name: /^Export MP4$/ })).toBeDisabled();
  });

  it("explains that the title card time is not in the MP4", async () => {
    const timeline = makeThreeClipTimeline();
    timeline.manifest.title_card = { text: "Episode 1", duration_seconds: 3 };
    timeline.total_duration_seconds = 16;
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(timeline) });
    mount();
    await advance(0);

    expect(screen.getByTestId("export-duration-note")).toHaveTextContent(
      "MP4 about 13s: the 3s title card is not rendered",
    );
  });

  it("puts the selected clip inspector before the assembly settings", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeThreeClipTimeline()) });
    mount();
    await advance(0);

    const inspector = screen.getByRole("region", { name: "Selected clip" });
    const assembly = screen.getByRole("region", { name: "Assembly settings" });
    expect(
      inspector.compareDocumentPosition(assembly) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("labels the assemble action by whether a timeline exists", async () => {
    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json({ detail: "Timeline not found" }, 404) });
    mount();
    await advance(0);
    expect(screen.getByRole("button", { name: "Assemble timeline" })).toBeInTheDocument();
    cleanup();

    stubApi({ [`GET ${TIMELINE_PATH}`]: () => json(makeTimeline()) });
    mount();
    await advance(0);
    expect(screen.getByRole("button", { name: "Re-assemble timeline" })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /^(Re-)?assemble timeline$/i }));
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
      fireEvent.click(screen.getByRole("button", { name: /^(Re-)?assemble timeline$/i }));

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

    fireEvent.click(screen.getByRole("button", { name: /^(Re-)?assemble timeline$/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /^(Re-)?assemble timeline$/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /^Export MP4$/ }));
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

    fireEvent.click(screen.getByRole("button", { name: /^Export MP4$/ }));
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

function assembleOut(timeline: TimelineSequence) {
  return {
    ...timeline,
    clip_count: timeline.manifest.video_clips.length,
    audio_track_count: timeline.manifest.audio_tracks.length,
    has_subtitles: timeline.manifest.has_subtitles,
  };
}

type AssembleBody = {
  name: string;
  aspect_ratio: string | null;
  fps: number | null;
  subtitle_content: string;
  subtitle_format: string;
  title_card: Record<string, unknown>;
  audio_tracks: unknown[];
  custom_clips: Array<{
    shot_id: string;
    order_index: number;
    start_time_seconds: number | null;
    source_duration_seconds: number;
    trim_start_seconds: number;
    trim_end_seconds: number;
    transition_type: string;
    transition_duration_seconds: number;
    volume: number;
    fade_in_seconds: number;
    fade_out_seconds: number;
  }>;
};

function assembleBodies(fetchMock: ReturnType<typeof stubApi>): AssembleBody[] {
  return callsTo(fetchMock, "POST", ASSEMBLE_PATH).map(
    ([, init]) => JSON.parse(String(init!.body)) as AssembleBody,
  );
}

/** Mounts with the three-clip timeline; assemble returns `assembleResponse` (default: 201). */
async function mountEditor(
  options: {
    timeline?: TimelineSequence;
    assembleResponse?: () => Response;
  } = {},
) {
  let current = options.timeline ?? makeThreeClipTimeline();
  const fetchMock = stubApi({
    [`GET ${TIMELINE_PATH}`]: () => json(current),
    [`POST ${ASSEMBLE_PATH}`]:
      options.assembleResponse ??
      (() => {
        current = makeThreeClipTimeline("seq-2");
        return json(assembleOut(current), 201);
      }),
    [`POST ${EXPORTS_PATH}`]: () => json({ detail: "not under test" }, 500),
  });
  const queryClient = mount();
  await advance(0);
  return { fetchMock, queryClient };
}

function setNumber(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const saveButton = () => screen.getByRole("button", { name: "Save clip edits" });

describe("TimelineEditorPage clip editing", () => {
  it("saves trims as custom_clips with renumbered order and carried-over settings", async () => {
    const timeline = makeThreeClipTimeline();
    timeline.manifest.title_card = { text: "Opening", duration_seconds: 2 };
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
    const { fetchMock } = await mountEditor({ timeline });
    expect(screen.getByTestId("clip-edit-status")).toHaveTextContent("Matches saved sequence");
    expect(saveButton()).toBeDisabled();

    setNumber("Trim start (s)", "0.5");
    setNumber("Trim end (s)", "1");
    expect(screen.getByTestId("clip-effective-duration")).toHaveTextContent("2.5s of 4s source");
    expect(screen.getByTestId("clip-edit-status")).toHaveTextContent("Unsaved changes");
    expect(screen.getByText(/Draft Total 00:00:11/)).toBeInTheDocument();

    fireEvent.click(saveButton());
    await advance(0);

    const [body] = assembleBodies(fetchMock);
    expect(body).toBeDefined();
    expect(body!.name).toBe("Master Cut v1");
    expect(body!.aspect_ratio).toBe("16:9");
    expect(body!.fps).toBe(24);
    expect(body!.title_card).toEqual({ text: "Opening", duration_seconds: 2 });
    expect(body!.audio_tracks).toEqual([
      {
        track_type: "music",
        name: "Main Score Track",
        asset_id: null,
        volume: 0.8,
        fade_in_seconds: 0.5,
        fade_out_seconds: 1,
        muted: false,
      },
    ]);
    expect(body!.subtitle_content).toBe("");
    expect(body!.custom_clips.map((c) => c.order_index)).toEqual([1, 2, 3]);
    expect(body!.custom_clips[0]).toMatchObject({
      shot_id: "shot-1",
      start_time_seconds: null,
      source_duration_seconds: 4,
      trim_start_seconds: 0.5,
      trim_end_seconds: 1,
      transition_type: "hard_cut",
      transition_duration_seconds: 0,
      volume: 1,
      fade_in_seconds: 0,
      fade_out_seconds: 0,
    });
    expect(body!.custom_clips.map((c) => c.start_time_seconds)).toEqual([null, null, null]);

    // The saved sequence replaces the draft.
    expect(screen.getByTestId("clip-edit-status")).toHaveTextContent("Matches saved sequence");
  });

  it("reorders with the inspector buttons and sends moved clips with sequential placement", async () => {
    const timeline = makeThreeClipTimeline();
    // Clip 3 starts after a 2 s gap in the saved sequence.
    timeline.manifest.video_clips[2]!.start_time_seconds = 11;
    const { fetchMock } = await mountEditor({ timeline });

    fireEvent.click(screen.getByRole("button", { name: /Shot: shot-3/ }));
    expect(screen.getByLabelText("Gap before clip (s)")).toHaveValue(2);
    expect(screen.getByRole("button", { name: "Move right" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Move left" }));
    fireEvent.click(screen.getByRole("button", { name: "Move left" }));
    expect(screen.getByRole("button", { name: "Move left" })).toBeDisabled();
    expect(screen.getByLabelText("Selected clip")).toHaveTextContent("#1 of 3 (saved as #3)");

    const trackOrder = screen
      .getAllByRole("button", { name: /Shot: shot-/ })
      .map((button) => /shot-\d/.exec(button.textContent ?? "")?.[0]);
    expect(trackOrder).toEqual(["shot-3", "shot-1", "shot-2"]);

    fireEvent.click(saveButton());
    await advance(0);

    const [body] = assembleBodies(fetchMock);
    expect(body!.custom_clips.map((c) => [c.shot_id, c.order_index, c.start_time_seconds])).toEqual(
      [
        ["shot-3", 1, null],
        ["shot-1", 2, null],
        ["shot-2", 3, null],
      ],
    );
  });

  it("blocks saving while trims exceed the source duration", async () => {
    const { fetchMock } = await mountEditor();

    setNumber("Trim start (s)", "3");
    setNumber("Trim end (s)", "1");

    expect(screen.getByText(/must leave at least 0.1 s of the 4 s source/)).toBeInTheDocument();
    expect(screen.getByText(/1 clip needs fixing before saving/)).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    fireEvent.click(saveButton());
    await advance(0);
    expect(assembleBodies(fetchMock)).toHaveLength(0);
  });

  it("forces the first clip to a hard cut and validates later dissolves", async () => {
    const { fetchMock } = await mountEditor();

    expect(screen.getByLabelText("Transition in")).toBeDisabled();
    expect(screen.getByLabelText("Transition in")).toHaveValue("hard_cut");
    expect(screen.getByText(/first clip always opens with a hard cut/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Shot: shot-2/ }));
    fireEvent.change(screen.getByLabelText("Transition in"), {
      target: { value: "cross_dissolve" },
    });
    setNumber("Dissolve (s)", "4.5");
    expect(screen.getByText(/shorter than this clip and the previous clip/)).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();

    setNumber("Dissolve (s)", "1");
    // Move the dissolving clip to the front: it must be sent as a hard cut.
    fireEvent.click(screen.getByRole("button", { name: "Move left" }));
    fireEvent.click(saveButton());
    await advance(0);

    const [body] = assembleBodies(fetchMock);
    expect(body!.custom_clips[0]).toMatchObject({
      shot_id: "shot-2",
      transition_type: "hard_cut",
      transition_duration_seconds: 0,
    });
  });

  it("sends an explicit start time for a clip with a gap", async () => {
    const { fetchMock } = await mountEditor();

    fireEvent.click(screen.getByRole("button", { name: /Shot: shot-2/ }));
    setNumber("Gap before clip (s)", "1.5");
    expect(screen.getByLabelText("Selected clip")).toHaveTextContent("00:00:05:12");
    fireEvent.click(saveButton());
    await advance(0);

    const [body] = assembleBodies(fetchMock);
    expect(body!.custom_clips.map((c) => c.start_time_seconds)).toEqual([null, 5.5, null]);
  });

  it("requires confirmation before saving would drop saved subtitles", async () => {
    const timeline = makeThreeClipTimeline();
    timeline.manifest.has_subtitles = true;
    const { fetchMock } = await mountEditor({ timeline });

    setNumber("Trim start (s)", "1");
    fireEvent.click(saveButton());
    await advance(0);

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent(/saved subtitles will be removed unless you cancel/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await advance(0);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(assembleBodies(fetchMock)).toHaveLength(0);

    fireEvent.click(saveButton());
    fireEvent.click(screen.getByRole("button", { name: "Save without subtitles" }));
    await advance(0);
    const bodies = assembleBodies(fetchMock);
    expect(bodies).toHaveLength(1);
    expect(bodies[0]!.subtitle_content).toBe("");
  });

  it("keeps re-entered subtitles without asking for confirmation", async () => {
    const timeline = makeThreeClipTimeline();
    timeline.manifest.has_subtitles = true;
    const { fetchMock } = await mountEditor({ timeline });

    fireEvent.change(screen.getByLabelText(/Subtitle cues/i), {
      target: { value: "1\n00:00:00,000 --> 00:00:02,000\nHi" },
    });
    setNumber("Trim start (s)", "1");
    fireEvent.click(saveButton());
    await advance(0);

    expect(screen.queryByRole("alertdialog")).toBeNull();
    const [body] = assembleBodies(fetchMock);
    expect(body!.subtitle_content).toBe("1\n00:00:00,000 --> 00:00:02,000\nHi");
    expect(body!.subtitle_format).toBe("srt");
  });

  it("removes a clip after confirmation and discard restores every saved clip", async () => {
    const { fetchMock } = await mountEditor();

    fireEvent.click(screen.getByRole("button", { name: /Shot: shot-2/ }));
    fireEvent.click(screen.getByRole("button", { name: "Remove from sequence" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getAllByTestId("timeline-clip")).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "Remove from sequence" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove clip" }));
    expect(screen.getAllByTestId("timeline-clip")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Shot: shot-2/ })).toBeNull();
    setNumber("Trim end (s)", "1");
    expect(screen.getByTestId("clip-edit-status")).toHaveTextContent("Unsaved changes");

    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getAllByTestId("timeline-clip")).toHaveLength(3);
    expect(screen.getByTestId("clip-edit-status")).toHaveTextContent("Matches saved sequence");
    expect(screen.getByLabelText("Trim end (s)")).toHaveValue(0);
    expect(assembleBodies(fetchMock)).toHaveLength(0);
  });

  it("shows a 422 and points to the clip the server rejected", async () => {
    await mountEditor({
      assembleResponse: () =>
        json({ detail: "custom_clips[1]: take_id không thuộc shot_id đã chọn." }, 422),
    });

    setNumber("Trim start (s)", "1");
    fireEvent.click(saveButton());
    await advance(0);

    const alert = screen
      .getAllByRole("alert")
      .find((node) => node.textContent?.includes("Could not save clip edits"));
    expect(alert).toHaveTextContent(/custom_clips\[1\]: take_id không thuộc shot_id/);
    fireEvent.click(screen.getByRole("button", { name: "Select clip #2" }));
    expect(screen.getByLabelText("Selected clip")).toHaveTextContent(
      "Server rejected this clip: take_id không thuộc shot_id đã chọn.",
    );
    expect(screen.getByTestId("clip-edit-status")).toHaveTextContent("Unsaved changes");
  });

  it("disables export and re-assembly while clip edits are unsaved", async () => {
    await mountEditor();
    const launch = screen.getByRole("button", { name: /^Export MP4$/ });
    const assemble = screen.getByRole("button", { name: /^(Re-)?assemble timeline$/i });
    expect(launch).toBeEnabled();
    expect(screen.queryByTestId("export-unsaved-edits")).toBeNull();

    setNumber("Trim start (s)", "1");

    expect(launch).toBeDisabled();
    expect(assemble).toBeDisabled();
    expect(screen.getByTestId("export-unsaved-edits")).toHaveTextContent(
      /Export renders the saved sequence/,
    );

    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(launch).toBeEnabled();
    expect(assemble).toBeEnabled();
  });

  it("exposes a keyboard-operable drag handle for every clip", async () => {
    await mountEditor();
    const handles = screen.getAllByRole("button", { name: /^Reorder clip \d/ });
    expect(handles).toHaveLength(3);
    expect(handles[0]).toHaveAttribute("aria-roledescription", "sortable");
    expect(handles[0]).toHaveAttribute("tabindex", "0");
  });
});
