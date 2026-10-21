import { describe, expectTypeOf, it } from "vitest";
import { apiGet } from "@/lib/api/client";
import type { components, paths } from "@/lib/api/schema";

describe("OpenAPI-inferred API client responses", () => {
  it("derives project and connection responses from generated component schemas", () => {
    expectTypeOf<ReturnType<typeof apiGet<"/api/v1/projects">>>().toEqualTypeOf<
      Promise<components["schemas"]["ProjectOut"][]>
    >();
    expectTypeOf<ReturnType<typeof apiGet<"/api/v1/connections">>>().toEqualTypeOf<
      Promise<components["schemas"]["ConnectionOut"][]>
    >();
  });

  it("uses the generated health endpoint response shape", () => {
    expectTypeOf<ReturnType<typeof apiGet<"/healthz">>>().toEqualTypeOf<
      Promise<paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"]>
    >();
  });

  it("does not manufacture a response type for paths absent from OpenAPI", () => {
    expectTypeOf<ReturnType<typeof apiGet<"/api/v1/not-in-openapi">>>().toEqualTypeOf<
      Promise<never>
    >();
  });
});
