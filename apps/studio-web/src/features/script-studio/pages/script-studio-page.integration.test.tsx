import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    params,
  }: {
    children: unknown;
    params?: { projectId: string; module: string };
  }) => (
    <a href={params ? `/projects/${params.projectId}/${params.module}` : "/"}>
      {children as string}
    </a>
  ),
}));

import { ScriptStudioPage } from "@/features/script-studio/pages/script-studio-page";
import type { ScriptStudioView } from "@/features/script-studio/search";
import type { StoryBibleEntry } from "@/features/script-studio/state";
import { useSessionStore } from "@/lib/stores/session-store";

interface RecordedRequest {
  method: string;
  pathname: string;
  search: string;
  body: unknown;
  isFormData: boolean;
}

type Handler = (
  request: RecordedRequest,
) => { status: number; body: unknown; contentType?: string } | undefined;

function buildResponse(status: number, body: unknown, contentType = "application/json"): Response {
  if (body instanceof Uint8Array) {
    return new Response(new Blob([body as Uint8Array<ArrayBuffer>], { type: contentType }), {
      status,
      headers: { "content-type": contentType, "content-length": String(body.byteLength) },
    });
  }
  if (typeof body === "string" && contentType !== "application/json") {
    return new Response(body, { status, headers: { "content-type": contentType } });
  }
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": contentType },
  });
}

function stubApi(handler: Handler) {
  const requests: RecordedRequest[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      const isFormData = init?.body instanceof FormData;
      const request: RecordedRequest = {
        method: init?.method ?? "GET",
        pathname: url.pathname,
        search: url.search,
        body:
          typeof init?.body === "string"
            ? JSON.parse(init.body)
            : isFormData
              ? init.body
              : undefined,
        isFormData,
      };
      requests.push(request);
      const response = handler(request);
      if (!response) {
        if (
          request.method === "GET" &&
          (request.pathname.endsWith("/shots") || request.pathname.endsWith("/script-revisions"))
        ) {
          return buildResponse(200, []);
        }
        return buildResponse(404, { detail: "Not mocked", category: "not_found" });
      }
      return buildResponse(response.status, response.body, response.contentType);
    }),
  );
  return requests;
}

const bibleEntries: StoryBibleEntry[] = [
  {
    id: "bib_char1",
    entry_type: "character",
    code_name: "char_linh",
    display_name: "Detective Linh",
    immutable_traits: "2D anime detective with sharp silver eyes",
    mutable_attributes: { current_outfit: "charcoal trench coat" },
    reference_asset_ids: ["ast_ref1"],
    requires_reference_lock: true,
  },
  {
    id: "bib_char2",
    entry_type: "character",
    code_name: "char_minh",
    display_name: "Inspector Minh",
    immutable_traits: "tall veteran inspector",
    mutable_attributes: {},
    reference_asset_ids: [],
    requires_reference_lock: true,
  },
  {
    id: "bib_style",
    entry_type: "style",
    code_name: "style_noir",
    display_name: "2D Anime Noir",
    immutable_traits: "cel-shaded rain and neon reflections",
    mutable_attributes: {
      color_palette: "teal and amber",
      negative_constraints: ["3d cgi", "deformed hands"],
    },
    reference_asset_ids: [],
    requires_reference_lock: false,
  },
];

/** Holds the `?view=` URL state the route would provide; `view` starts as the initial URL. */
function Harness({ initialView }: { initialView?: ScriptStudioView }) {
  const [view, setView] = useState<ScriptStudioView | undefined>(initialView);
  return <ScriptStudioPage projectId="project-1" view={view} onViewChange={setView} />;
}

function mount(initialView?: ScriptStudioView) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <Harness initialView={initialView} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useSessionStore.getState().setAdminToken("test-token");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.getState().clearAdminToken();
});

describe("ScriptStudioPage", () => {
  it("lists Story Bible entries, filters by category, shows missing-lock warnings, and previews reference images", async () => {
    const pngBytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: bibleEntries };
      }
      if (pathname === "/api/v1/assets/ast_ref1/download") {
        return { status: 200, body: pngBytes, contentType: "image/png" };
      }
      return undefined;
    });
    mount();

    const linhCard = await screen.findByTestId("story-bible-entry-char_linh");
    expect(within(linhCard).getByText("Detective Linh")).toBeInTheDocument();
    expect(within(linhCard).getByText("charcoal trench coat")).toBeInTheDocument();
    expect(
      await within(linhCard).findByTestId("reference-asset-image-ast_ref1"),
    ).toBeInTheDocument();

    const minhCard = screen.getByTestId("story-bible-entry-char_minh");
    expect(
      within(minhCard).getByText(/Downstream shots referencing char_minh will be blocked/),
    ).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Style (1)" }));
    expect(screen.getByTestId("story-bible-entry-style_noir")).toBeInTheDocument();
    expect(screen.queryByTestId("story-bible-entry-char_linh")).not.toBeInTheDocument();
  });

  it("explains a missing admin token and renders the empty state when no entries exist", async () => {
    useSessionStore.getState().clearAdminToken();
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/story-bible"
        ? { status: 401, body: { detail: "Unauthorized", category: "auth", retryable: false } }
        : undefined,
    );
    mount();

    expect(
      await screen.findByText("Admin token required to read Story Bible."),
    ).toBeInTheDocument();
    cleanup();

    useSessionStore.getState().setAdminToken("test-token");
    stubApi(() => ({ status: 200, body: [] }));
    mount("bible");
    expect(await screen.findByText("No Story Bible entries yet")).toBeInTheDocument();
  });

  it("uploads a reference image, attaches its ID, and creates a new Story Bible entry", async () => {
    const user = userEvent.setup();
    const pngBytes = new Uint8Array([137, 80, 78, 71]);
    const requests = stubApi(({ method, pathname }) => {
      if (method === "GET" && pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: [] };
      }
      if (method === "POST" && pathname === "/api/v1/assets/upload") {
        return {
          status: 201,
          body: {
            id: "ast_uploaded_1",
            project_id: "project-1",
            asset_type: "reference",
            filename: "portrait.png",
            mime_type: "image/png",
            size_bytes: 512,
            sha256: "deadbeef",
            is_locked_reference: true,
            created_at: "2026-01-01T00:00:00Z",
          },
        };
      }
      if (method === "GET" && pathname === "/api/v1/assets/ast_uploaded_1/download") {
        return { status: 200, body: pngBytes, contentType: "image/png" };
      }
      if (method === "POST" && pathname === "/api/v1/projects/project-1/story-bible") {
        return {
          status: 201,
          body: {
            id: "bib_new",
            code_name: "char_hero",
            status: "recompiled_downstream_shots",
          },
        };
      }
      return undefined;
    });
    mount("bible");

    await screen.findByText("No Story Bible entries yet");
    await user.click(screen.getByRole("button", { name: "New Bible entry" }));

    const editor = screen.getByTestId("story-bible-editor-new");
    await user.type(within(editor).getByLabelText("Code name"), "char_hero");
    await user.type(within(editor).getByLabelText("Display name"), "Hero Kai");
    await user.type(
      within(editor).getByLabelText("Immutable traits"),
      "short spiky black hair and amber eyes",
    );
    await user.type(within(editor).getByLabelText("Current outfit"), "navy jacket");
    await user.click(
      within(editor).getByRole("checkbox", {
        name: "Require reference lock for downstream shots",
      }),
    );

    const file = new File([pngBytes], "portrait.png", { type: "image/png" });
    await user.upload(within(editor).getByLabelText("Upload reference image"), file);
    await user.click(within(editor).getByRole("button", { name: "Upload & attach" }));

    expect(await within(editor).findByText(/Attached uploaded asset/)).toBeInTheDocument();
    expect(within(editor).getByTestId("editor-reference-assets")).toHaveTextContent(
      "ast_uploaded_1",
    );

    await user.click(within(editor).getByRole("button", { name: "Create entry" }));
    expect(await within(editor).findByTestId("story-bible-upsert-result")).toHaveTextContent(
      "Saved char_hero (bib_new). Downstream project shots were recompiled.",
    );
    expect(within(editor).getByLabelText("Code name")).toHaveValue("");
    expect(within(editor).queryByTestId("editor-reference-assets")).not.toBeInTheDocument();

    const upsertCall = requests.find(
      (req) => req.method === "POST" && req.pathname === "/api/v1/projects/project-1/story-bible",
    );
    expect(upsertCall?.body).toEqual({
      entry_type: "character",
      code_name: "char_hero",
      display_name: "Hero Kai",
      immutable_traits: "short spiky black hair and amber eyes",
      mutable_attributes: { current_outfit: "navy jacket" },
      reference_asset_ids: ["ast_uploaded_1"],
      requires_reference_lock: true,
    });
  });

  it("preserves unsaved Story Bible editor changes across background refetches", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return {
          status: 200,
          body: bibleEntries.map((item) => ({ ...item, reference_asset_ids: [] })),
        };
      }
      return undefined;
    });
    mount();

    const linhCard = await screen.findByTestId("story-bible-entry-char_linh");
    await user.click(
      within(linhCard).getByRole("button", { name: "Edit Detective Linh (char_linh)" }),
    );

    const editor = within(linhCard).getByTestId("story-bible-editor-char_linh");
    const displayNameInput = within(editor).getByLabelText("Display name");
    await user.clear(displayNameInput);
    await user.type(displayNameInput, "Detective Linh — Undercover");

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(within(editor).getByLabelText("Display name")).toHaveValue(
      "Detective Linh — Undercover",
    );
  });

  it("handles script import 409 replace_existing confirmation and displays the import summary", async () => {
    const user = userEvent.setup();
    let importCalls = 0;
    const requests = stubApi(({ method, pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: bibleEntries };
      }
      if (method === "POST" && pathname === "/api/v1/projects/project-1/scripts/import") {
        importCalls += 1;
        if (importCalls === 1) {
          return {
            status: 409,
            body: {
              detail:
                "Re-applying a script would delete every existing shot together with its generated takes. Retry with replace_existing=true to confirm.",
              category: "conflict",
              retryable: false,
            },
          };
        }
        return {
          status: 201,
          body: {
            script_revision_id: "scr_rev02",
            version_number: 2,
            raw_content_preserved: true,
            diff_summary: "Parsed 1 scene and 2 shots.",
            detected_characters: 1,
            detected_locations: 1,
            detected_scenes: 1,
            generated_shots: 2,
            warnings: ["SHOT 2: Duration clamped to 600s."],
          },
        };
      }
      return undefined;
    });
    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(within(importPanel).getByRole("button", { name: "Insert sample script" }));
    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Apply changes directly to Story Bible & shots/,
      }),
    );
    expect(
      within(importPanel).getByTestId("existing-pipeline-overwrite-warning"),
    ).toBeInTheDocument();
    await user.click(within(importPanel).getByRole("button", { name: "Import & apply script" }));

    expect(
      await within(importPanel).findAllByText(/Retry with replace_existing=true to confirm/),
    ).not.toHaveLength(0);

    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Confirm replacing existing shots and generated takes/,
      }),
    );
    await user.click(within(importPanel).getByRole("button", { name: "Import & apply script" }));

    const resultBanner = await within(importPanel).findByTestId("script-import-result");
    expect(resultBanner).toHaveTextContent("Imported script revision v2 (scr_rev02)");
    expect(within(resultBanner).getByTestId("script-import-warnings")).toHaveTextContent(
      "SHOT 2: Duration clamped to 600s.",
    );

    const postBodies = requests
      .filter((req) => req.method === "POST" && req.pathname.endsWith("/scripts/import"))
      .map((req) => req.body as Record<string, unknown>);
    expect(postBodies).toHaveLength(2);
    expect(postBodies[0]?.replace_existing).toBe(false);
    expect(postBodies[1]?.replace_existing).toBe(true);
    expect(
      within(importPanel).getByRole("checkbox", {
        name: /Confirm replacing existing shots and generated takes/,
      }),
    ).not.toBeChecked();
  });

  it("saves a draft script revision, lists saved revisions, and applies the draft revision to Story Bible & shots", async () => {
    const user = userEvent.setup();
    let draftSaved = false;
    let draftApplied = false;
    const requests = stubApi(({ method, pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: draftApplied ? bibleEntries : [] };
      }
      if (method === "GET" && pathname === "/api/v1/projects/project-1/script-revisions") {
        if (!draftSaved) return { status: 200, body: [] };
        return {
          status: 200,
          body: [
            {
              id: "scr_draft01",
              project_id: "project-1",
              version_number: 1,
              source_format: "museflow",
              raw_content: "SCENE 1: Neon Alley\nSHOT 1: Detective Linh arrives",
              diff_summary: draftApplied
                ? "Applied revision v1: 1 scene and 2 shots."
                : "Saved draft revision v1: 1 scene and 2 shots ready for review.",
              is_approved: draftApplied,
              detected_characters: 1,
              detected_locations: 1,
              detected_scenes: 1,
              detected_shots: 2,
              warnings: [],
              created_at: "2026-03-01T10:00:00Z",
            },
          ],
        };
      }
      if (method === "POST" && pathname === "/api/v1/projects/project-1/scripts/import") {
        draftSaved = true;
        return {
          status: 201,
          body: {
            script_revision_id: "scr_draft01",
            version_number: 1,
            raw_content_preserved: true,
            diff_summary: "Saved draft revision v1: 1 scene and 2 shots ready for review.",
            detected_characters: 1,
            detected_locations: 1,
            detected_scenes: 1,
            generated_shots: 0,
            warnings: [],
          },
        };
      }
      if (
        method === "POST" &&
        pathname === "/api/v1/projects/project-1/script-revisions/scr_draft01/apply"
      ) {
        draftApplied = true;
        return {
          status: 200,
          body: {
            script_revision_id: "scr_draft01",
            version_number: 1,
            raw_content_preserved: true,
            diff_summary: "Applied revision v1: 1 scene and 2 shots.",
            detected_characters: 1,
            detected_locations: 1,
            detected_scenes: 1,
            generated_shots: 2,
            warnings: [],
          },
        };
      }
      return undefined;
    });
    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(within(importPanel).getByRole("button", { name: "Insert sample script" }));
    await user.click(
      within(importPanel).getByRole("button", { name: "Save draft revision & analyze" }),
    );

    const resultBanner = await within(importPanel).findByTestId("script-import-result");
    expect(resultBanner).toHaveTextContent(
      "Saved draft revision v1: 1 scene and 2 shots ready for review.",
    );
    const applyDraftBtn = within(resultBanner).getByTestId("apply-draft-revision-button");
    expect(applyDraftBtn).toBeInTheDocument();

    const revisionsList = await within(importPanel).findByTestId("script-revisions-list");
    expect(revisionsList).toHaveTextContent("Saved Script Revisions (1)");
    expect(within(revisionsList).getByTestId("script-revision-scr_draft01")).toHaveTextContent(
      "Draft",
    );

    await user.click(applyDraftBtn);

    await waitFor(() => {
      expect(within(importPanel).getByTestId("script-import-result")).toHaveTextContent(
        "Applied revision v1: 1 scene and 2 shots.",
      );
    });
    expect(
      within(importPanel).queryByTestId("apply-draft-revision-button"),
    ).not.toBeInTheDocument();

    const applyCalls = requests.filter(
      (req) =>
        req.method === "POST" &&
        req.pathname === "/api/v1/projects/project-1/script-revisions/scr_draft01/apply",
    );
    expect(applyCalls).toHaveLength(1);
    expect(applyCalls[0]?.body).toEqual({ replace_existing: false });
  });

  it("exports the shot list as JSON and surfaces export failures", async () => {
    const user = userEvent.setup();
    const requests = stubApi(({ pathname, search }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: [] };
      }
      if (pathname === "/api/v1/projects/project-1/shots/export" && search === "?format=json") {
        return { status: 200, body: { project_id: "project-1", shots: [] } };
      }
      if (pathname === "/api/v1/projects/project-1/shots/export" && search === "?format=csv") {
        return {
          status: 500,
          body: { detail: "Export storage unavailable", category: "internal", retryable: true },
        };
      }
      return undefined;
    });
    mount("bible");

    await screen.findByText("No Story Bible entries yet");
    await user.click(screen.getByRole("button", { name: "Export shot list as JSON" }));
    await waitFor(() =>
      expect(
        requests.some(
          (req) =>
            req.pathname === "/api/v1/projects/project-1/shots/export" &&
            req.search === "?format=json",
        ),
      ).toBe(true),
    );

    await user.click(screen.getByRole("button", { name: "Export shot list as CSV" }));
    expect(await screen.findByTestId("shot-export-error")).toHaveTextContent(
      "Could not export shots (CSV): Export storage unavailable",
    );
  });

  it("warns about overwriting pipeline data when project has shots but no Story Bible entries", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: [] };
      }
      if (pathname === "/api/v1/projects/project-1/shots") {
        return {
          status: 200,
          body: [
            {
              id: "shot_1",
              project_id: "project-1",
              sequence_index: 0,
              title: "Shot 1",
              generation_state: "queued",
              subject_codes: [],
              lint_report: [],
            },
          ],
        };
      }
      return undefined;
    });

    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(within(importPanel).getByRole("button", { name: "Insert sample script" }));
    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Apply changes directly to Story Bible & shots/,
      }),
    );
    const warning = within(importPanel).getByTestId("existing-pipeline-overwrite-warning");
    expect(warning).toBeInTheDocument();
    expect(warning).toHaveTextContent(/Project already has 1 shot/);
  });

  it("shows unverified pipeline status warning when pipeline query fails and Apply is checked", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: [] };
      }
      if (pathname === "/api/v1/projects/project-1/shots") {
        return { status: 500, body: { detail: "Database error", category: "internal" } };
      }
      return undefined;
    });

    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(within(importPanel).getByRole("button", { name: "Insert sample script" }));
    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Apply changes directly to Story Bible & shots/,
      }),
    );
    const unverifiedWarning = await within(importPanel).findByTestId(
      "pipeline-check-unverified-warning",
    );
    expect(unverifiedWarning).toBeInTheDocument();
    expect(unverifiedWarning).toHaveTextContent(/Could not verify current pipeline status/);
  });

  it("shows checking status and disables submit button while verifying pipeline data", async () => {
    const user = userEvent.setup();
    let resolveShots!: () => void;
    const shotsPromise = new Promise<void>((resolve) => {
      resolveShots = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const url = new URL(String(input));
        if (url.pathname === "/api/v1/projects/project-1/story-bible") {
          return buildResponse(200, []);
        }
        if (url.pathname === "/api/v1/projects/project-1/shots") {
          await shotsPromise;
          return buildResponse(200, []);
        }
        return buildResponse(404, { detail: "Not mocked" });
      }),
    );

    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Apply changes directly to Story Bible & shots/,
      }),
    );

    expect(within(importPanel).getByTestId("pipeline-checking-status")).toBeInTheDocument();
    const submitBtn = within(importPanel).getByRole("button", { name: "Verifying pipeline..." });
    expect(submitBtn).toBeDisabled();

    resolveShots();
    await waitFor(() => {
      expect(within(importPanel).queryByTestId("pipeline-checking-status")).not.toBeInTheDocument();
    });
    expect(
      within(importPanel).getByRole("button", { name: "Import & apply script" }),
    ).not.toBeDisabled();
  });
  it("keeps the entry being edited mounted when search or filter excludes it, and explains search-empty results", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return {
          status: 200,
          body: bibleEntries.map((item) => ({ ...item, reference_asset_ids: [] })),
        };
      }
      return undefined;
    });
    mount();

    const linhCard = await screen.findByTestId("story-bible-entry-char_linh");
    await user.click(
      within(linhCard).getByRole("button", { name: "Edit Detective Linh (char_linh)" }),
    );
    const editor = within(linhCard).getByTestId("story-bible-editor-char_linh");
    const displayNameInput = within(editor).getByLabelText("Display name");
    await user.clear(displayNameInput);
    await user.type(displayNameInput, "Linh draft");

    const search = screen.getByRole("searchbox", { name: "Search Story Bible entries" });
    await user.type(search, "noir");
    expect(screen.getByTestId("story-bible-entry-style_noir")).toBeInTheDocument();
    expect(screen.queryByTestId("story-bible-entry-char_minh")).not.toBeInTheDocument();
    expect(screen.getByTestId("story-bible-editor-char_linh")).toBe(editor);
    expect(within(editor).getByLabelText("Display name")).toHaveValue("Linh draft");
    expect(screen.getByTestId("story-bible-editing-outside-filter")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Location (0)" }));
    expect(within(editor).getByLabelText("Display name")).toHaveValue("Linh draft");

    // Closing the editor leaves no visible entries, so the search-specific message appears.
    await user.click(screen.getByRole("button", { name: "Edit Detective Linh (char_linh)" }));
    expect(screen.getByTestId("story-bible-search-empty")).toHaveTextContent(
      /No location entries match the search .noir./,
    );
    expect(screen.queryByTestId("story-bible-filter-empty")).not.toBeInTheDocument();

    await user.clear(search);
    expect(screen.getByTestId("story-bible-filter-empty")).toHaveTextContent(
      "No location entries match the selected filter.",
    );
  });

  it("shows unknown counts instead of zero when the pipeline check fails", async () => {
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 500, body: { detail: "Database error", category: "internal" } };
      }
      if (pathname === "/api/v1/projects/project-1/shots") {
        return { status: 500, body: { detail: "Database error", category: "internal" } };
      }
      return undefined;
    });
    const user = userEvent.setup();
    mount();

    // A Story Bible that failed to load opens on its own view, with the error visible.
    expect(await screen.findByRole("heading", { name: "Story Bible (—)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Story Bible/ })).not.toHaveTextContent(/\d/);

    await user.click(screen.getByRole("tab", { name: "Script import" }));
    const importPanel = await screen.findByTestId("script-import-panel");
    await waitFor(() => {
      expect(within(importPanel).getByTestId("active-shots-count")).toHaveTextContent(
        "— active shots",
      );
    });
  });

  it("labels the scene outline as a local preview without inventing scene headings", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/story-bible" ? { status: 200, body: [] } : undefined,
    );
    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    expect(within(importPanel).getByTestId("local-outline-disclaimer")).toHaveTextContent(
      "Local outline preview — not analyzer output",
    );
    await user.type(
      within(importPanel).getByLabelText("Script content"),
      "SHOT 1: Cold open{enter}SCENE 1: Neon Alley{enter}SHOT 2: Arrival",
    );

    const outline = within(importPanel).getByTestId("local-scene-outline");
    const items = within(outline).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Before first scene");
    expect(items[1]).toHaveTextContent("#1");
    expect(items[1]).toHaveTextContent("SCENE 1: Neon Alley");
    expect(within(outline).queryByRole("button")).not.toBeInTheDocument();
    expect(importPanel).not.toHaveTextContent(/Opening Sequence/);
    expect(within(importPanel).getByTestId("local-scene-count")).toHaveTextContent(
      "Local preview: 1 scene",
    );
  });

  it("renders an icon-only retry control for failed compact reference thumbnails", async () => {
    const user = userEvent.setup();
    const pngBytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    let extraCalls = 0;
    stubApi(({ pathname }) => {
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return {
          status: 200,
          body: [{ ...bibleEntries[0], reference_asset_ids: ["ast_ref1", "ast_ref2"] }],
        };
      }
      if (pathname === "/api/v1/assets/ast_ref1/download") {
        return { status: 200, body: pngBytes, contentType: "image/png" };
      }
      if (pathname === "/api/v1/assets/ast_ref2/download") {
        extraCalls += 1;
        return extraCalls === 1
          ? { status: 500, body: { detail: "Storage down", category: "internal" } }
          : { status: 200, body: pngBytes, contentType: "image/png" };
      }
      return undefined;
    });
    mount();

    const errorBox = await screen.findByTestId("reference-asset-error-ast_ref2");
    expect(errorBox).toHaveTextContent("Preview unavailable");
    const retry = within(errorBox).getByRole("button", {
      name: "Retry loading Reference ast_ref2 for Detective Linh",
    });
    expect(retry).not.toHaveTextContent("Retry");
    await user.click(retry);
    expect(await screen.findByTestId("reference-asset-image-ast_ref2")).toBeInTheDocument();
  });
});

describe("ScriptStudioPage views", () => {
  const blockedShot = {
    id: "shot-1",
    generation_state: "blocked_reference",
    subject_codes: ["char_minh"],
    lint_report: [
      {
        code: "BLOCKED_REFERENCE",
        severity: "blocker",
        message: "Thiếu ref asset 'MISSING_REQUIRED_CHAR_REF'.",
        field: "subject_ids",
      },
    ],
  };

  it("starts an empty project on the script import and a populated one on the Story Bible", async () => {
    stubApi(() => ({ status: 200, body: [] }));
    mount();
    expect(await screen.findByRole("tab", { name: "Script import" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("script-import-panel")).toBeVisible();
    cleanup();

    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/story-bible"
        ? { status: 200, body: bibleEntries }
        : undefined,
    );
    mount();
    expect(await screen.findByRole("tab", { name: /^Story Bible/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("script-import-panel")).not.toBeVisible();
  });

  it("switches views with the arrow keys", async () => {
    const user = userEvent.setup();
    stubApi(() => ({ status: 200, body: [] }));
    mount();

    const scriptTab = await screen.findByRole("tab", { name: "Script import" });
    scriptTab.focus();
    await user.keyboard("{ArrowRight}");
    const bibleTab = screen.getByRole("tab", { name: /^Story Bible/ });
    expect(bibleTab).toHaveAttribute("aria-selected", "true");
    expect(bibleTab).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", bibleTab.id);
  });

  it("lists characters missing a reference with their blocked shots, and opens the editor", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/story-bible"
        ? { status: 200, body: bibleEntries }
        : pathname === "/api/v1/projects/project-1/shots"
          ? { status: 200, body: [blockedShot] }
          : undefined,
    );
    mount();

    const attention = await screen.findByTestId("story-bible-attention");
    expect(within(attention).getByRole("heading")).toHaveTextContent("Needs a reference image (1)");
    expect(attention).toHaveTextContent("Inspector Minh");
    expect(attention).toHaveTextContent("Blocks 1 shot");
    expect(attention).not.toHaveTextContent("Detective Linh");
    expect(screen.getByRole("tab", { name: /^Story Bible/ })).toHaveTextContent("1 to fix");

    await user.click(
      within(attention).getByRole("button", { name: "Add a reference image for Inspector Minh" }),
    );
    expect(await screen.findByTestId("story-bible-editor-char_minh")).toBeVisible();

    const next = screen.getByTestId("script-studio-next-step");
    expect(next).toHaveTextContent(
      "1 blocked shot needs the reference images above; it may have other blockers too.",
    );
    expect(within(next).getByRole("link", { name: /Go to Shot Board/ })).toHaveAttribute(
      "href",
      "/projects/project-1/shot-board",
    );
  });

  it("offers the Story Bible as the next step after an applied import", async () => {
    const user = userEvent.setup();
    stubApi(({ method, pathname }) => {
      if (method === "POST" && pathname === "/api/v1/projects/project-1/scripts/import") {
        return {
          status: 201,
          body: {
            script_revision_id: "scr_rev01",
            version_number: 1,
            raw_content_preserved: true,
            diff_summary: "Parsed 1 scene and 2 shots.",
            detected_characters: 1,
            detected_locations: 1,
            detected_scenes: 1,
            generated_shots: 2,
            warnings: [],
          },
        };
      }
      return { status: 200, body: [] };
    });
    mount();

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(within(importPanel).getByRole("button", { name: "Insert sample script" }));
    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Apply changes directly to Story Bible & shots/,
      }),
    );
    await user.click(within(importPanel).getByRole("button", { name: "Import & apply script" }));
    const result = await screen.findByTestId("script-import-result");
    await user.click(within(result).getByRole("button", { name: "Review Story Bible" }));

    expect(screen.getByRole("tab", { name: /^Story Bible/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("keeps the derived view after an import adds Story Bible entries, so the result stays", async () => {
    const user = userEvent.setup();
    let imported = false;
    stubApi(({ method, pathname }) => {
      if (method === "POST" && pathname === "/api/v1/projects/project-1/scripts/import") {
        imported = true;
        return {
          status: 201,
          body: {
            script_revision_id: "scr_rev01",
            version_number: 1,
            raw_content_preserved: true,
            diff_summary: "Parsed 1 scene and 2 shots.",
            detected_characters: 2,
            detected_locations: 0,
            detected_scenes: 1,
            generated_shots: 2,
            warnings: [],
          },
        };
      }
      if (pathname === "/api/v1/projects/project-1/story-bible") {
        return { status: 200, body: imported ? bibleEntries : [] };
      }
      return { status: 200, body: [] };
    });
    mount();

    const importPanel = await screen.findByTestId("script-import-panel");
    await user.click(within(importPanel).getByRole("button", { name: "Insert sample script" }));
    await user.click(
      within(importPanel).getByRole("checkbox", {
        name: /Apply changes directly to Story Bible & shots/,
      }),
    );
    await user.click(within(importPanel).getByRole("button", { name: "Import & apply script" }));
    await screen.findByTestId("script-import-result");
    // The refetched Story Bible now has entries; the view must not switch on its own.
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /^Story Bible/ })).toHaveTextContent("3 entries"),
    );
    expect(screen.getByRole("tab", { name: "Script import" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("script-import-result")).toBeVisible();
  });

  it("keeps a typed script and an unsaved Story Bible edit when switching views", async () => {
    const user = userEvent.setup();
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/story-bible"
        ? { status: 200, body: bibleEntries }
        : undefined,
    );
    mount("script");

    const importPanel = await screen.findByTestId("script-import-panel");
    const textarea = within(importPanel).getByRole("textbox", { name: /script/i });
    await user.type(textarea, "SCENE 1: Dock at night");

    await user.click(screen.getByRole("tab", { name: /^Story Bible/ }));
    await user.click(
      await screen.findByRole("button", { name: "Edit Detective Linh (char_linh)" }),
    );
    const editor = await screen.findByTestId("story-bible-editor-char_linh");
    const traits = within(editor).getByLabelText("Immutable traits");
    await user.clear(traits);
    await user.type(traits, "unsaved trait edit");

    await user.click(screen.getByRole("tab", { name: "Script import" }));
    expect(within(importPanel).getByRole("textbox", { name: /script/i })).toHaveValue(
      "SCENE 1: Dock at night",
    );
    await user.click(screen.getByRole("tab", { name: /^Story Bible/ }));
    expect(
      within(screen.getByTestId("story-bible-editor-char_linh")).getByLabelText("Immutable traits"),
    ).toHaveValue("unsaved trait edit");
  });

  it("says blocked shots are explained on the Shot Board when no Story Bible fix applies", async () => {
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/story-bible"
        ? { status: 200, body: [bibleEntries[0]] }
        : pathname === "/api/v1/projects/project-1/shots"
          ? {
              status: 200,
              body: [
                {
                  id: "shot-1",
                  generation_state: "blocked_reference",
                  subject_codes: ["char_linh"],
                  lint_report: [
                    {
                      code: "CAMERA_MOTION_CONFLICT",
                      severity: "blocker",
                      message: "Conflicting camera motion.",
                      field: "camera_motion",
                    },
                  ],
                },
              ],
            }
          : undefined,
    );
    mount();

    const next = await screen.findByTestId("script-studio-next-step");
    expect(next).toHaveTextContent("1 shot is blocked; the Shot Board shows why.");
    expect(screen.queryByTestId("story-bible-attention")).toBeNull();
  });
});
