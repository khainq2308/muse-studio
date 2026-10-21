import { describe, expectTypeOf, it } from "vitest";
import { apiGet } from "@/lib/api/client";
import type { components, paths } from "@/lib/api/schema";

type GetResponse<Path extends string> = ReturnType<typeof apiGet<Path>>;

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

  it("does not manufacture a response type for paths absent from OpenAPI", () => {
    expectTypeOf<GetResponse<"/api/v1/not-in-openapi">>().toEqualTypeOf<Promise<never>>();
  });
});
