import { describe, expectTypeOf, it } from "vitest";
import { apiGet, apiPost, type ApiGetPath } from "@/lib/api/client";
import type { components, paths } from "@/lib/api/schema";

type GetResponse<Path extends ApiGetPath> = ReturnType<typeof apiGet<Path>>;

describe("OpenAPI-inferred API client responses", () => {
  it("derives project and connection responses from generated component schemas", () => {
    expectTypeOf<GetResponse<"/api/v1/projects">>().toEqualTypeOf<
      Promise<components["schemas"]["ProjectOut"][]>
    >();
    expectTypeOf<GetResponse<"/api/v1/connections">>().toEqualTypeOf<
      Promise<components["schemas"]["ConnectionOut"][]>
    >();
  });

  it("uses the generated health endpoint response shape", () => {
    expectTypeOf<GetResponse<"/healthz">>().toEqualTypeOf<
      Promise<paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"]>
    >();
  });

  it("derives responses for OpenAPI paths with required path parameters", () => {
    expectTypeOf<GetResponse<"/api/v1/projects/{project_id}/shots">>().toEqualTypeOf<
      Promise<components["schemas"]["ProjectShotOut"][]>
    >();
  });

  it("derives paginated queue and mutation responses from OpenAPI", () => {
    expectTypeOf<GetResponse<"/api/v1/queue/tasks">>().toEqualTypeOf<
      Promise<components["schemas"]["QueueTaskPageOut"]>
    >();
    function pauseTaskResponse() {
      return apiPost("/api/v1/queue/tasks/{task_id}/pause", undefined, {
        pathParams: { task_id: "task-1" },
      });
    }
    expectTypeOf<ReturnType<typeof pauseTaskResponse>>().toEqualTypeOf<
      Promise<components["schemas"]["QueueTaskOut"]>
    >();
    void pauseTaskResponse;
  });
});

// These examples are deliberately never invoked. npm run build type-checks this file and
// verifies that invalid routes or missing/misspelled path params remain compile-time errors.
function compileTimeRejectionExamples() {
  // @ts-expect-error The path is not present in the generated OpenAPI schema.
  apiGet("/api/v1/not-in-openapi");

  // @ts-expect-error Dynamic path segments must use the OpenAPI {parameter} template.
  apiGet("/api/v1/projects/project-123/shots");

  // @ts-expect-error The project_id path parameter is required for this endpoint.
  apiGet("/api/v1/projects/{project_id}/shots");

  // @ts-expect-error Path parameter names come from the generated OpenAPI operation.
  apiGet("/api/v1/projects/{project_id}/shots", { pathParams: { projectId: "project-123" } });

  const dynamicPath: string = "/api/v1/projects";
  // @ts-expect-error A free-form string can bypass the generated route contract.
  apiGet(dynamicPath);

  // @ts-expect-error Query strings belong in the query option, not the path.
  apiGet("/api/v1/projects?limit=5");

  // @ts-expect-error Text-only endpoints must use apiGetText, not the JSON client.
  apiGet("/api/v1/metrics/prometheus");

  // @ts-expect-error Binary-only endpoints must use downloadFile, not the JSON client.
  apiGet("/api/v1/assets/{asset_id}/download", { pathParams: { asset_id: "asset-1" } });

  // @ts-expect-error Unknown query parameter names must be rejected by generated GET types.
  apiGet("/api/v1/queue/tasks", { query: { project_id: "project-1", task_status: "running" } });

  // @ts-expect-error Queue state filters must use the generated enum values.
  apiGet("/api/v1/queue/tasks", { query: { state: "not-a-queue-state" } });

  // @ts-expect-error Endpoints with no query parameters reject arbitrary query objects.
  apiGet("/api/v1/projects", { query: { unexpected_param: 1 } });

  // @ts-expect-error Mutation paths must exist in the generated OpenAPI contract.
  apiPost("/api/v1/not-in-openapi", undefined);

  // @ts-expect-error Mutation path parameter names must match the generated operation.
  apiPost("/api/v1/queue/tasks/{task_id}/pause", undefined, {
    pathParams: { wrong_task_id: "task-1" },
  });
}
void compileTimeRejectionExamples;
