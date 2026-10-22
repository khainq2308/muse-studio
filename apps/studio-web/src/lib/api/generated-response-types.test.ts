import { describe, expectTypeOf, it } from "vitest";
import { apiGet, type ApiGetPath } from "@/lib/api/client";
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
}
void compileTimeRejectionExamples;
