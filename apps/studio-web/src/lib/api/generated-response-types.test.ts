import { describe, expectTypeOf, it } from "vitest";
import { apiGet } from "@/lib/api/client";
import type { components, paths } from "@/lib/api/schema";

function projectsResponse() {
  return apiGet("/api/v1/projects");
}

function connectionsResponse() {
  return apiGet("/api/v1/connections");
}

function healthResponse() {
  return apiGet("/healthz");
}

function unknownEndpointResponse() {
  return apiGet("/api/v1/not-in-openapi");
}

describe("OpenAPI-inferred API client responses", () => {
  it("derives project and connection responses from generated component schemas", () => {
    expectTypeOf<ReturnType<typeof projectsResponse>>().toEqualTypeOf<
      Promise<components["schemas"]["ProjectOut"][]>
    >();
    expectTypeOf<ReturnType<typeof connectionsResponse>>().toEqualTypeOf<
      Promise<components["schemas"]["ConnectionOut"][]>
    >();
  });

  it("uses the generated health endpoint response shape", () => {
    expectTypeOf<ReturnType<typeof healthResponse>>().toEqualTypeOf<
      Promise<paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"]>
    >();
  });

  it("does not manufacture a response type for paths absent from OpenAPI", () => {
    expectTypeOf<ReturnType<typeof unknownEndpointResponse>>().toEqualTypeOf<Promise<never>>();
  });
});
