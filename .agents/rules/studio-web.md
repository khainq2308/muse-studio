---
trigger: glob
globs: apps/studio-web/**
description: Mandatory frontend architecture for any change under apps/studio-web.
---

Before editing `apps/studio-web`, read `docs/FRONTEND_ARCHITECTURE.md` (mandatory; it wins on conflict) and follow `.agents/skills/frontend-feature/SKILL.md`. API types come only from the generated `src/lib/api/schema.d.ts` (regenerate with `npm run gen:api`, never hand-edit); `src/lib/api/client.ts` is the only `fetch` caller. Validate with `scripts/test.sh web` and `bash scripts/check_openapi.sh`; browser behavior is unverified unless a browser test actually ran.
