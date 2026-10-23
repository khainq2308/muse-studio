import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        if (request.method === "GET" && request.pathname.endsWith("/shots")) {
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

function mount() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ScriptStudioPage projectId="project-1" />
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
    mount();
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
    mount();

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
    mount();

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
    mount();

    await screen.findByText("No Story Bible entries yet");
    await user.click(screen.getByRole("button", { name: "Export JSON" }));
    await waitFor(() =>
      expect(
        requests.some(
          (req) =>
            req.pathname === "/api/v1/projects/project-1/shots/export" &&
            req.search === "?format=json",
        ),
      ).toBe(true),
    );

    await user.click(screen.getByRole("button", { name: "Export CSV" }));
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
            },
          ],
        };
      }
      return undefined;
    });

    mount();

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

    mount();

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

    mount();

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
});
