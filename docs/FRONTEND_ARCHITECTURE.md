# Kiến trúc Frontend chuẩn cho `apps/studio-web`

Tài liệu này là **chuẩn bắt buộc** cho mọi thay đổi trong `apps/studio-web`, áp dụng cho mọi coding agent (Claude, Gemini, Codex, người) làm việc trong repo. Nó cụ thể hoá — không thay thế — hợp đồng chung trong `AGENTS.md` và kiến trúc tổng thể trong `docs/ARCHITECTURE.md`. Các lựa chọn nền tảng được ghi thành ADR-006 và ADR-007 trong `docs/DECISIONS.md`.

Bối cảnh: backend (M1–M7) hiện đã có các API phục vụ các luồng chính, trong khi frontend từng chỉ là một file `App.tsx` placeholder và nhiều bản giao diện sinh tự động (hand-rolled CSS, không thư viện component/icon, tự chế data-fetching, contracts viết tay trôi khỏi API thật, hiển thị JSON thô thay cho media). Lưu ý theo kỷ luật bằng chứng của `AGENTS.md`: "có endpoint" không đồng nghĩa "capability đã được kiểm chứng" — từng capability vẫn phải được xác minh theo OpenAPI, implementation và test tương ứng (xem `docs/MUSE2API_CAPABILITY_MATRIX.md`). Tài liệu này khoá các quyết định để tình trạng của frontend không lặp lại.

## Trạng thái triển khai hiện tại

Bảng này là nguồn trạng thái duy nhất cho các artifact frontend. Mỗi PR Phase 0/1/2/3/4 phải cập nhật bảng dựa trên file, test và CI thực tế; không suy ra trạng thái từ việc tài liệu đã mô tả thiết kế.

| Hạng mục | Trạng thái ở revision hiện tại | Điều kiện đổi trạng thái |
|---|---|---|
| Chuẩn kiến trúc frontend | Đã đặc tả; chưa đồng nghĩa runtime đã được viết lại | Kiểm chứng các artifact trong PR phase tương ứng |
| Foundation frontend (dependencies, lockfile, client, router, theme, lint) | Đã triển khai trong PR #49; CI của nhánh phải xanh trước khi merge | Build, typecheck, lint, unit test và browser smoke chạy trên dependency đã khóa |
| Xuất OpenAPI/snapshot/kiểm tra sai lệch | Đã triển khai và merge ở PR #52; kiểm tra snapshot/schema chạy trong CI và có lệnh local | Giữ exporter + generated schema byte-for-byte đồng bộ với route/model, kiểm tra CI và local đều đạt |
| Typed backend routes | Đã merge PR #56–#59; tất cả router đã có response contract hoặc disposition rõ ràng | CI kiểm tra 13 router; OpenAPI audit còn 0 generic/missing success-response schemas và 0 JSON contract gaps |
| Generated frontend API consumers | Đã merge ở PR #60; query parameter names/types được suy từ OpenAPI cùng Queue Monitor (commit a6c6fbd) | `apiGet` suy response và query type từ operation; type tests bảo vệ route, enum query, tên query và path parameters; không khai báo response interface song song |
| Frontend product feature UI | Queue Monitor đã merge (commit a6c6fbd), kèm trả lời câu hỏi operator (needs-input) cho task `awaiting_input` trên nhánh `feat/needs-input` (unit test, chưa có bằng chứng browser với provider thật). Shot Board phần A trên nhánh `feat/shot-board-list`: route `/projects/:projectId/shot-board`, danh sách shot theo scene, sửa shot qua `PATCH` (lỗi 422 hiển thị tại field, prompt đã biên dịch lại chỉ đọc), enqueue một shot và batch launch có xác nhận, hiển thị cảnh báo `frame_chain_continuity_not_guaranteed` và shot bị chặn; điều hướng project Shot Board ↔ Queue Monitor. Bằng chứng: Vitest + Playwright với API mock (`page.route()`), chưa có bằng chứng browser với backend thật. Shot Board phần B trên nhánh `feat/shot-board-takes`: panel duyệt take theo shot (badge selected/continuity, director notes, thumbnail first/last frame lazy-load), preview video/ảnh theo yêu cầu qua `apiGetBlob` (tải có header admin, giới hạn kích thước, object URL được revoke), so sánh A/B hai take cạnh nhau dùng `compare-takes` (thao tác được bằng bàn phím), chọn take (invalidate danh sách shot/take, hiển thị last frame mới và các shot downstream được trỏ lại), flag continuity kèm tuỳ chọn regenerate có xác nhận quota và link tới Queue Monitor, lỗi 4xx hiển thị inline; cảnh báo không chặn khi previous shot có `sequence_index` ≥ shot hiện tại. Bằng chứng: Vitest + Playwright với API mock, chưa có bằng chứng browser với backend thật; API take chưa có thời điểm tạo. Các module sản xuất còn lại vẫn là placeholder | Chỉ đánh dấu theo evidence của từng phase feature; M4 vẫn partial đến khi worker/API enqueue được xác minh end-to-end |
| Browser smoke test | Playwright trên Vite production preview và smoke test CI trên production Nginx image | Playwright xác minh deep link, Queue Monitor loading/empty state và request queue cùng origin; `scripts/test_nginx_prod.sh` kiểm tra SPA fallback, `/healthz` proxy và giữ nguyên đường dẫn `/api/` |

## Local development API routing

In the development Compose stack, the browser must call the Studio Web origin (`VITE_ORCHESTRATOR_URL=.`). Vite then proxies `/api/*` and `/healthz` from inside the `studio-web` container to `ORCHESTRATOR_PROXY_TARGET`, whose Compose default is `http://orchestrator-api:8000`. Do not set the browser-facing base URL to `http://localhost:8000` in Docker: `localhost` would refer to the user's browser machine and would bypass the same-origin proxy. Outside Compose, Vite defaults the upstream target to `http://127.0.0.1:8000`.

The Vite dev and preview servers bind to loopback (`DEV_SERVER_HOST = "127.0.0.1"` in `vite.config.ts`, port 5173 with `strictPort`) because they proxy an admin API. Only the development Compose container passes `--host 0.0.0.0`, and Compose publishes that port on the host's `127.0.0.1` only. Do not change the default to a LAN-reachable address. The orchestrator's development Host/Origin guards (see `docs/ARCHITECTURE.md`) expect the UI at `http://localhost:5173` or `http://127.0.0.1:5173`.

## 1. Nguyên tắc nền tảng

1. **Media-first.** MuseStudio là studio sản xuất video: shot, take và asset phải được hiển thị bằng media thật (frame, video preview) chứ không phải bằng văn bản mô tả hay JSON thô.
2. **Type-safe từ đầu đến cuối.** Mọi kiểu dữ liệu trao đổi với backend phải suy ra từ OpenAPI schema của chính backend. Không tồn tại nguồn type thứ hai do người/agent viết tay.
3. **Server state và client state là hai thứ khác nhau.** Server state đi qua TanStack Query; client state thuần UI đi qua Zustand. Không trộn lẫn, không sao chép server data vào store.
4. **Mọi thứ bản build cần phải nằm trong `apps/studio-web`.** Đây là build context của cả hai Dockerfile của studio-web; import ra ngoài thư mục này là phá build (xem mục 6).
5. **Chỉ dùng endpoint có thật.** Trước khi viết một lời gọi API, đối chiếu OpenAPI schema (`/openapi.json` của orchestrator) hoặc router tương ứng trong `apps/orchestrator`. Cấm đoán đường dẫn từ trí nhớ hoặc từ tài liệu mô tả.

## 2. Stack chuẩn

| Hạng mục | Lựa chọn | Ghi chú |
|---|---|---|
| Framework | React 18 + TypeScript (strict) + Vite | Đã có sẵn; giữ nguyên, chưa nâng React 19. |
| Điều hướng / URL state | **TanStack Router** (v1) | Project đang chọn và module hiện tại nằm trên URL (`/projects/:projectId/<module>`), deep-link được và sống sót qua reload; không giữ các thông tin này chỉ trong store. |
| CSS / design system | **Tailwind CSS v4** + **shadcn/ui** (trên Radix primitives) | Theme bằng CSS variables của shadcn; dark theme là theme mặc định của sản phẩm. |
| Icons | **lucide-react** | Nguồn icon duy nhất. Cấm dùng emoji làm icon, cấm nhúng SVG rời tự vẽ cho các khái niệm đã có trong Lucide. |
| Server state / data fetching | **TanStack Query v5** | Query, mutation, polling (`refetchInterval`), invalidate theo query key. |
| Client state | **Zustand v5** | Trạng thái UI thuần (panel mở/đóng, lựa chọn tạm thời) và admin token trong bộ nhớ. Dữ liệu đã có trên URL hoặc ở server thì không sao chép vào store. |
| Forms | **react-hook-form** + **zod** | Schema validate suy từ generated types khi có thể. |
| Workflow canvas | **@xyflow/react** v12 (React Flow) | Bắt buộc cho module workflow-canvas (M3); node/edge là component typed. |
| Kéo–thả | **dnd-kit** | Sắp xếp shot trên board, clip trên timeline. |
| Bảng / danh sách dài | TanStack Table / @tanstack/react-virtual | Chỉ thêm khi một màn hình thực sự cần. |
| Test | **Vitest** + Testing Library + MSW (handler qua `openapi-msw` từ cùng snapshot OpenAPI) + **Playwright** smoke test | Xem mục 7. |
| Lint / format | ESLint + Prettier cho frontend | ESLint chặn raw `fetch`/browser storage và các alias import vi phạm layering; AST-based boundary checker kiểm tra import tĩnh/động, cross-layer/cross-feature imports, import ra ngoài `src/`, raw fetch/storage và JSON dump trong `<pre>`. Mọi feature mới phải giữ các rule này trong CI. |
| Lockfile | `package-lock.json` | Bắt buộc commit; cài đặt trong CI/Docker bằng `npm ci`. Cấm sửa lockfile bằng tay (theo `AGENTS.md`). |

Thư viện ngoài danh sách này chỉ được thêm khi có nhu cầu cụ thể của một feature và được nêu lý do trong PR. Không thêm thư viện "để dành".

## 3. Contracts: sinh tự động từ OpenAPI — cấm viết tay

- Nguồn sự thật duy nhất cho API types là OpenAPI schema của orchestrator. Schema được xuất offline bởi `scripts/export_openapi.py` vào `apps/orchestrator/openapi.json`; script import đối tượng FastAPI app nhưng không chạy lifespan nên không cần DB/Redis. Exporter và snapshot được giới thiệu ở PR #52.
- `npm run gen:api` đọc snapshot đã commit và tạo `src/lib/api/schema.d.ts`; CLI `openapi-typescript@7.13.0` đã được khóa trong `devDependencies` và `package-lock.json`, không cài tạm qua `npx`:

  ```bash
  npm run gen:api   # openapi-typescript ../orchestrator/openapi.json -> src/lib/api/schema.d.ts
  ```

  File sinh ra có header "generated — do not edit", không bao giờ sửa tay. PR thay đổi hành vi API ở backend phải kèm snapshot và file generated đã cập nhật (hoặc ghi rõ vì sao chưa cần).
- **CI chống drift là bắt buộc** (thêm ở Phase 0): một step chạy script export offline và snapshot được bổ sung ở Phase 0 cùng `gen:api`, rồi `git diff --exit-code` trên cả snapshot lẫn `schema.d.ts`. Thiếu cơ chế này thì quy tắc trên chỉ là lời hứa.
- Điều kiện tiên quyết ở backend: FastAPI chỉ sinh type tốt khi route khai báo `response_model`. **Phase 0c phải audit toàn bộ router**, không chỉ queue/batch/workflows: gồm pipeline (shots, Story Bible, import script), queue, batch/takes, workflows, timeline/export và các router còn lại. Dùng `python3 scripts/audit_openapi_responses.py` để tạo danh sách response schema còn tổng quát từ snapshot hiện tại; không đóng đinh số liệu audit cũ vì source thay đổi theo từng PR. Mỗi endpoint chưa typed phải được ghi rõ và là blocker cho feature tiêu thụ endpoint đó; không được dùng `Record<string, unknown>` như một cách né contract gap.
- Mọi response/request type trong features lấy từ `components['schemas'][...]` của generated schema. **Cấm** dùng `Record<string, unknown>` làm kiểu response trong features, và cấm định nghĩa interface payload/response song song trong file service.
- `packages/contracts` đã được xóa ở PR #60 sau khi rà soát toàn bộ file TypeScript/JavaScript: không còn import/reference từ consumer và bốn union cũ (`ShotApprovalState`, `ShotGenerationState`, `NodeExecutionState`, `ConnectionHealthState`) đã có schema generated tương ứng. Không tạo lại package này hoặc thêm type API viết tay. Lý do: package nằm ngoài Docker build context của studio-web và nội dung viết tay từng trôi khỏi API thật. ADR-007 ghi lại phương án bị loại là nới Docker build context lên root monorepo.

## 4. Cấu trúc thư mục chuẩn

```text
apps/studio-web/src/
  main.tsx
  app/                  # App shell: providers, route composition, shared layout and navigation
    app-shell.tsx         # Shared header + Outlet only; no feature queries
    router.tsx
    project-module-route.tsx
  components/
    ui/                 # Component shadcn/ui (sinh bằng shadcn CLI, chỉnh sửa tối thiểu)
    shared/             # Component dùng chung không gắn domain: StatusBadge, QueryState,
                        # MediaThumb, ConfirmActionDialog, TakeCompare, ...
  entities/
    project/            # Mỗi entity gồm: api.ts (query/mutation hooks DÙNG CHUNG),
    connection/         #   badge/status mapping, type rút từ generated schema.
    shot/               # Đây là nơi duy nhất chứa hook dữ liệu dùng chung giữa
    take/               #   nhiều feature (vd connections, projects, shots).
    task/
  features/
    admin-session/
      components/
        admin-token-form.tsx
    api-status/
      components/
        api-status-badge.tsx
      use-api-status.ts
    workspace-overview/  # Transitional Phase 0a overview; not a product workflow module
      pages/
        overview-page.tsx
      components/
        project-list.tsx
        connection-list.tsx
    projects/
    connections/
    script-studio/
    shot-board/
      api.ts            # Query/mutation hooks của feature; không gọi fetch trực tiếp
      components/       # UI con chỉ thuộc shot-board
      pages/            # Component cấp route
      index.ts          # Public surface của feature
    workflow-canvas/
    queue-monitor/
    timeline-editor/
    system-ops/
  lib/
    api/
      client.ts         # HTTP transport duy nhất được phép gọi fetch
      errors.ts         # Shared HTTP/auth error classification and messages
      schema.d.ts       # OpenAPI-generated; không sửa tay
      queryKeys.ts      # Query keys dùng thống nhất
    stores/             # Zustand stores
    utils.ts            # cn() và helpers thuần
  styles/
    globals.css         # Tailwind entry + theme tokens duy nhất
```

Cách chia trách nhiệm:

- `lib/api/client.ts` là lớp transport dùng chung: base URL, header xác thực, query params, `AbortSignal`, chuẩn hoá lỗi và tải file có xác thực. Nó không chứa logic nghiệp vụ của riêng domain.
- `features/<feature>/api.ts` chứa các TanStack Query hooks và mutation của đúng feature; các hooks gọi `lib/api/client.ts`, không gọi `fetch` trực tiếp.
- `entities/<entity>/api.ts` chỉ dành cho query/mutation hooks được dùng thật sự bởi từ hai feature trở lên. Không chuyển mọi request vào một service tổng hợp hoặc tạo entity chỉ để gom file.
- `components/ui/` chứa primitive UI không biết domain; `components/shared/` chứa component tái sử dụng nhưng vẫn nhận props đã typed. Phần ghép state/domain vào giao diện nằm ở feature.
- `app/app-shell.tsx` chỉ chứa layout dùng chung, token form và health badge với `<Outlet />`; không gọi query dữ liệu projects/connections.
- `features/workspace-overview/pages/overview-page.tsx` là trang chuyển tiếp của Phase 0a: compose hook trong `entities/` và truyền dữ liệu/trạng thái qua props cho hai list presentational. Nó không triển khai workflow Projects/Connections của sản phẩm và sẽ được thay bằng trang feature thật ở phase tương ứng.
- Lỗi 401/403 được phân loại trong `lib/api/errors.ts`; lỗi hiển thị ở resource phát sinh, không chuyển thành trạng thái xác thực toàn cục của shell.
- Không tạo `src/services/` như một lớp monolithic API client. Cũng không tự dựng `useAsyncData`/`usePolling`; dùng TanStack Query để quản lý cache, hủy request, invalidate và polling.

Quy tắc phụ thuộc:

- Chỉ `lib/api` được gọi `fetch`. Component và hook trong features/entities không tự gọi mạng.
- Hook dữ liệu dùng chung (cần ở từ hai feature trở lên — ví dụ danh sách connections cho queue-monitor và shot-board, danh sách projects cho project selector) đặt ở `entities/<entity>/api.ts`. Hook chỉ một feature dùng thì ở trong feature đó.
- Features **không import lẫn nhau**; chúng compose từ `entities/`, `components/shared/` và được lắp ở `app/`.
- `entities/` không import `features/`; `components/ui` không import features, entities hay `lib/api`.
- Alias đường dẫn chỉ nội bộ app: `@/` trỏ tới `src/`. Cấm mọi import dạng `../../` ra khỏi `apps/studio-web`.

Ánh xạ feature ↔ milestone của backend:

| Feature | Milestone | Phạm vi |
|---|---|---|
| `projects` | M1 | CRUD project, duplicate, revisions, chọn project (qua URL) |
| `connections` | M1 | Connections + account pool + test/health |
| `script-studio` | M2 | Import kịch bản, Story Bible |
| `shot-board` | M2 + M5 | Shot board, duyệt take A/B, batch launch, compare-takes, flag continuity |
| `workflow-canvas` | M3 | DAG canvas, runs, approve manual gate |
| `queue-monitor` | M4 + M5 | Hàng đợi generation task, step/pause/resume/cancel, takes theo shot |
| `timeline-editor` | M6 | Assemble timeline, export job + polling + tải MP4 |
| `system-ops` | M7 | Backups (tạo/list/tải/restore), quét & dọn orphan, metrics, diagnostics |

Cơ chế thực thi (thiết lập ở Phase 0, không dựa vào tự giác): ESLint dùng `no-restricted-globals` để chặn truy cập trực tiếp tới `fetch` và browser storage. Script [AST boundary checker](../apps/studio-web/scripts/check_frontend_boundaries.mjs) kiểm tra import tĩnh/động, alias `@/` và `src/`, import ra ngoài `src/` (bao gồm `@muse-studio/contracts`), phân lớp entities/features/UI primitives, truy cập raw fetch/storage và JSON dump trong `<pre>`. Vì checker chạy trên AST nên comment/string không bị coi là hành vi thực thi. Các quy tắc chưa máy móc hoá được (hard-code hex, JSON dump biến thể) thuộc checklist review của PR và Definition of Done ở mục 7.

## 5. Quy tắc triển khai theo lớp

### 5.1 API client & data fetching

- HTTP client (`lib/api/client.ts`) chịu trách nhiệm: gắn header `X-Studio-Admin-Token` (lấy từ store), chuẩn hoá lỗi thành `ApiError` gồm `status: number`, `detail: unknown`, `category?: string`, `retryable?: boolean`; backend có thể trả `detail` dạng string, object hoặc array (ví dụ lỗi validation 422). Giữ nguyên các field backend thực sự cung cấp, không ép mọi `detail` thành string và không log nội dung nhạy cảm. Client hỗ trợ huỷ bằng `AbortSignal` của TanStack Query và tải file có auth qua blob + object URL (nhớ `revokeObjectURL`). `apiGet` suy response, query parameter names và query enum values từ generated OpenAPI ở thời điểm biên dịch. `apiPost` suy successful JSON response, request body, path parameter names và query parameters từ generated OpenAPI operation; caller không khai báo response generic. `apiPut`, `apiPatch` và `apiDelete` vẫn là transport primitives generic và phải dùng generated request/response schemas ở nơi gọi. Đây là kiểm tra thời điểm biên dịch, không phải runtime validation. `apiRequest<T>` không xác thực payload JSON khi chạy. Backend response-model/OpenAPI drift checks và test vẫn là gate; chỉ thêm runtime validator khi feature có yêu cầu rõ ràng.
- Mọi request có timeout: `DEFAULT_REQUEST_TIMEOUT_MS` = 20 giây cho request JSON; `LONG_REQUEST_TIMEOUT_MS` = 10 phút cho body `FormData`/`Blob` (upload asset, restore backup) và cho `downloadFile`. Caller có thể truyền `timeoutMs` riêng. Hết thời gian thì request bị huỷ và reject bằng `ApiError` có `category: "timeout"` và `retryable: true`; `AbortSignal` của caller vẫn huỷ bình thường.
- Backend không có `GET /api/v1/projects/{project_id}`. Route `/projects/$projectId/$module` kiểm tra project tồn tại bằng danh sách project (`useProjects`, không phân trang): khi danh sách tải thành công mà không có ID thì hiển thị trạng thái not-found; khi danh sách lỗi thì module tự hiển thị lỗi của nó thay vì kết luận project không tồn tại.
- Query key theo mẫu `['<feature>', ...ids, paramsObject]`, khai báo tập trung ở `lib/api/queryKeys.ts`.
- Polling chỉ qua `refetchInterval` và phải dừng theo enum của **đúng resource**. `QueueTaskState` hiện gồm `queued | paused | running | awaiting_input | succeeded | failed | poll_timeout | canceled`: terminal là `succeeded | failed | poll_timeout | canceled`; `paused` không terminal nhưng phải dừng polling/re-enqueue tự động cho tới khi người dùng resume, còn `poll_timeout` là kết thúc và không được poll tiếp như `running`. `awaiting_input` không terminal và vẫn được polling như task active (backend tự poll lại provider; câu hỏi có thể được trả lời ở nơi khác hoặc hết hạn, và summary `active_tasks` của backend đã tính trạng thái này); Queue Monitor hiển thị câu hỏi `pending_input` dạng plain text, gửi trả lời qua `POST /api/v1/queue/tasks/{task_id}/reply` (đúng một trong `choice`/`text`), cho phép cancel nhưng không hiện nút pause. `ExportJobState` terminal là `succeeded | failed | canceled`. Tập trạng thái này phải trở thành enum trong response model/OpenAPI thay vì một trường `dict[str, Any]`/string không mô tả; sinh helper `isTerminal...` từ generated schema theo từng domain. Polling phải có backoff (ví dụ 2s → 5s → 10s) và trần thời gian (ví dụ 30 phút); quá trần thì dừng poll, giữ trạng thái gần nhất và cho phép làm mới thủ công. Cấm tự chế hook kiểu `useAsyncData`/`usePolling` bằng `useEffect` + `setInterval`. Với Queue Monitor (`getQueueRefetchInterval` trong `features/queue-monitor/state.ts`), query ở trạng thái `error` chỉ tiếp tục polling khi lỗi là **retryable** (lỗi mạng, timeout, 429, 5xx tạm thời theo `retryable` của `ApiError`): khi đó interval là mức chậm nhất `QUEUE_ERROR_POLL_INTERVAL_MS` (10s) và vẫn tôn trọng cửa sổ 30 phút; lỗi **không retryable** (auth, validation, not found) dừng polling cho tới khi người dùng Refresh/Retry. Query `pending` của lần tải đầu vẫn được polling trong cửa sổ có giới hạn. Cửa sổ 30 phút được khởi động lại khi đổi project/filter, thao tác queue thành công hoặc người dùng chọn Refresh/Retry. Queue GET endpoints phải yêu cầu admin auth. Queue Monitor dùng phân trang offset với `limit` mặc định 50 và trần server-side 100; summary cards phản ánh totals toàn project, độc lập với state filter và trang đang xem.
- Mutation thành công phải invalidate đúng query keys liên quan; lỗi mutation phải hiển thị cho người dùng, không nuốt lỗi.
- Admin token chỉ giữ **trong bộ nhớ** (Zustand store, không persist). Cấm lưu vào `localStorage`/`sessionStorage`.

### 5.2 Components & trạng thái hiển thị

- Mọi màn hình/danh sách dùng dữ liệu server phải có đủ bốn trạng thái: **loading / empty / error (có nút thử lại) / data**. Thiếu một trong bốn là chưa đạt.
- **Cấm** render JSON thô (`<pre>{JSON.stringify(...)}</pre>`) cho dữ liệu domain trong UI chính thức. Chỉ chấp nhận trong một panel "raw/debug" có chủ đích, mặc định ẩn.
- Component trình bày (presentational) nhận props đã typed; component cấp route/pages nối query hooks. Logic nghiệp vụ của một feature ở trong feature đó, không rò rỉ vào `components/shared`.

### 5.3 Media

- Shot/take/asset phải render thumbnail hoặc video preview thật qua endpoint asset download có auth (`GET /api/v1/assets/{asset_id}/download`). Endpoint backup download (`GET /api/v1/backups/{backup_id}/download`) trả file ZIP và chỉ dành cho luồng system-ops; không dùng nó để preview media. Không có asset thì hiển thị placeholder rõ ràng kèm trạng thái generation của shot.
- `apiGetBlob(path, { maxBytes })` trong `lib/api/client.ts` là transport dùng chung cho preview có auth: từ chối body vượt `maxBytes` theo `Content-Length` hoặc khi đọc stream (ApiError `category: "too_large"`), giữ media type của response; `useObjectUrl` (`lib/media/`) tạo object URL và revoke khi Blob đổi/unmount. Shot Board giới hạn 20 MB cho frame và 200 MB cho preview take.
- Cơ chế blob + object URL (tải file kèm header auth rồi tạo URL tạm) là giải pháp v1 và có **giới hạn phải tôn trọng**: nó tải toàn bộ file vào bộ nhớ, không seek/range, nên chỉ dùng cho ảnh và preview theo yêu cầu từng item. **Cấm** dùng nó để tải hàng loạt video cho lưới thumbnail. ADR-005 mô tả trích xuất boundary frames để nối shot; không tự suy ra từ ADR này rằng đã có endpoint poster/thumbnail chuyên dụng. Trước khi có nguồn poster đã được xác minh, không tải hàng loạt video để tạo thumbnail ở client. Poster asset/endpoint streaming có range + URL ký ngắn hạn là năng lực backend cần thiết kế và kiểm chứng riêng — cho tới lúc đó, lưới dùng placeholder hoặc poster asset đã tồn tại và video chỉ tải khi người dùng mở xem một item.
- API takes hiện có các field `first_frame_asset_id` và `last_frame_asset_id`; chúng có thể null. Khi có ID, frontend tải ảnh PNG đầy đủ qua endpoint asset download dùng chung và chỉ lazy-load khi item sắp hiển thị/được mở. Đây là asset frame hiện có, không phải một endpoint poster chuyên dụng; không tải hàng loạt file ảnh/video về memory.
- Duyệt take phải có chế độ so sánh A/B đặt hai take cạnh nhau bằng media thật, kèm thao tác chọn take và gắn cờ continuity — đây là nghiệp vụ lõi của shot-board, không phải trang trí.

### 5.4 Forms, hành động tốn quota & idempotency

- Form dùng react-hook-form + zod; lỗi `422` từ API hiển thị gần field liên quan khi ánh xạ được.
- Mọi hành động có thể phát sinh chi phí/quota ở provider thật (batch launch, enqueue render, workflow full-run, export) phải qua dialog xác nhận nêu rõ: provider + loại (mock/live), model, số lượng shot/task. Provider `mock` phải được ghi nhãn rõ là không tốn phí.
- Các thao tác tạo tài nguyên phải tôn trọng **cơ chế idempotency thực tế của backend** (đối chiếu với route/service/test hiện hành trên main khi chuẩn bị tích hợp; xác minh lại nếu backend thay đổi), không suy diễn:

  | Endpoint | Cơ chế phía server | Việc phía client |
  |---|---|---|
  | `POST /api/v1/queue/tasks` | `idempotency_key` là optional; khi bỏ key, backend tự chọn key theo nội dung. Response kèm `created_new` (`false` = request trùng, trả lại task cũ). `force_retry` yêu cầu một lần chạy/take mới. | Mặc định **bỏ field `idempotency_key`** và để backend chống trùng. Không tự sinh key ngẫu nhiên ở client. Khi người dùng chủ động retry task đã `failed`, chỉ gửi `force_retry: true` sau dialog xác nhận tạo lượt chạy/take mới vì có thể tốn quota; không dùng nó cho retry mạng cùng một ý định đang được gửi. Nếu `created_new: false`, phải báo rõ request đã khớp và server trả lại task cũ (kể cả task cũ đã failed), hiển thị đúng task/state đó và không báo rằng lượt sinh mới đã bắt đầu. |
  | `POST /api/v1/projects/{id}/batch/launch` | Không nhận key từ client; service tự suy key theo nội dung cho từng task; `only_changed=false` yêu cầu chạy lại cả những shot không đổi | Chống gửi trùng khi mutation đang chạy. Dialog phải hiển thị rõ số lượng shot, cảnh báo quota, và lựa chọn **“Run all regardless of changes”** khi `only_changed=false`; yêu cầu xác nhận riêng trước khi gửi |
  | `POST /api/v1/projects/{id}/timeline/exports` | Chưa hỗ trợ idempotency key | Disable nút khi đang gửi; dựa vào trạng thái job để hiển thị tiến trình, không tự gửi lại |

  Không gửi `Idempotency-Key` header với kỳ vọng server sẽ đọc — hiện backend không đọc header này ở các endpoint trên.

### 5.5 Workflow canvas (M3)

- Dùng `@xyflow/react` với custom node components typed theo generated schema; trạng thái node ánh xạ từ `NodeExecutionState` sang theme tokens.
- Backend hiện chấp nhận `run_mode` gồm `full | dry_run | from_here | downstream` (xem `WorkflowRunRequestIn` trong `apps/orchestrator/muse_orchestrator/api/workflows.py`). UI phải gửi tường minh `run_mode: "dry_run"` cho thao tác mặc định; chỉ gửi `run_mode: "full"` sau xác nhận ở mục 5.4, không dựa vào default phía server để ngầm chạy thật.
- Với thao tác chạy một phần, `from_here` và `downstream` phải đi kèm `start_node_id` là ID của node tồn tại. Backend (`WorkflowRunRequestIn`) đã từ chối bằng lỗi 422 khi thiếu `start_node_id`, và UI cũng không được gửi hai mode này nếu chưa chọn node.
- `run_mode` phải thành enum gồm đủ bốn giá trị trong request/response schema để generated types phản ánh hợp đồng thật; không rút gọn thành chỉ `full | dry_run`.
- Thao tác approve manual gate là hành động first-class trên chính node đang `waiting_approval`, không phải form nhập ID rời rạc.

### 5.6 Timeline & export (M6)

- Timeline editor quản lý model clips (thứ tự, trim, transition) trong store của feature; sắp xếp bằng dnd-kit; preview bằng phần tử `<video>` với asset thật.
- Export phía backend là **bất đồng bộ**: `POST /api/v1/projects/{id}/timeline/exports` trả `202` và một export job ở trạng thái `queued`; UI phải poll `GET /api/v1/projects/{id}/timeline/exports/{export_id}` theo quy tắc polling ở mục 5.1 cho tới trạng thái terminal (`succeeded | failed | canceled`) rồi mới thông báo kết quả và hiện liên kết tải. Cấm hiển thị "hoàn tất" ngay sau khi POST thành công.

### 5.7 Styling & icons

- Viết style bằng Tailwind utilities và component shadcn; màu sắc/spacing lấy từ theme tokens (CSS variables) trong `styles/globals.css`, là nguồn token runtime duy nhất. Tailwind CSS v4 theo CSS-first config: cấm tạo `tailwind.config.js/ts` hoặc chạy `tailwind_config_gen.py` cho MuseStudio. `docs/brand-guidelines.md` đang là Draft và không ghi đè theme dark + semantic shadcn tokens; mọi quyết định adoption palette/font brand phải được reconcile trong tài liệu này/ADR trước khi đưa vào runtime.
Cấm hard-code mã hex rải rác trong component và cấm thêm file CSS toàn cục mới ngoài `styles/globals.css`.
- Mọi nút bấm có icon từ lucide-react đi kèm nhãn chữ hoặc `aria-label` (với nút chỉ-icon). Badge trạng thái dùng token màu + chữ, ánh xạ nhất quán từ các state union của domain (approval/generation/node/health).

### 5.8 Accessibility

- Dùng Radix primitives cho dialog, menu, popover để có sẵn hành vi bàn phím/focus. Điều hướng chính giữa các module là **link theo route** với `aria-current="page"` ở mục đang mở (không dùng pattern `tablist` cho điều hướng chuyển trang); pattern tab đúng nghĩa chỉ dùng cho tab nội bộ trong một trang. Mọi control phải tới được bằng bàn phím và có focus visible.

## 6. Bất biến build & Docker

- Cả hai Dockerfile của studio-web build với context là `apps/studio-web`: mọi import phải resolve bên trong thư mục này (hệ quả trực tiếp của nguyên tắc 4, mục 1).
- Dependency khai báo trong `apps/studio-web/package.json` và khoá bằng `package-lock.json`; thay đổi dependency phải đi qua package manager.
- `VITE_ORCHESTRATOR_URL` dùng `.` cho production same-origin qua nginx và URL tuyệt đối cho dev. `lib/api/client.ts` bắt buộc chuẩn hoá `.` hoặc `/` thành base rỗng; mọi API path phải là đường dẫn tuyệt đối bắt đầu bằng `/api/` (ví dụ `/api/v1/projects`). Cấm ghép thành `./api/...` hay chuỗi URL tương đối vì khi đang ở deep link như `/projects/abc/queue-monitor`, trình duyệt sẽ gọi nhầm `/projects/abc/api/...` và nginx có thể trả `index.html`.
Không hard-code host/port trong code feature.

## 7. Kiểm thử & Definition of Done cho một feature

- Vitest + Testing Library cho component/hook; MSW với handler sinh qua `openapi-msw` từ chính snapshot OpenAPI đã commit (không viết mock tay trôi khỏi schema) cho tầng api của feature. Ít nhất: test cho query/mutation hooks chính và cho các component có logic trạng thái (badge mapping, confirm dialog, A/B compare).
- Playwright smoke test là bắt buộc từ Phase 0a. Smoke trên Vite `preview` dùng `page.route()`/`route.fulfill()` để mock API và kiểm tra deep link, route params, placeholder module và cùng-origin requests. CI còn chạy `scripts/test_nginx_prod.sh` trên **production image với Nginx thật** và một Orchestrator mock cô lập trong Docker network: kiểm tra `/` và `/projects/test-project/queue-monitor` trả cùng SPA `index.html`, `/healthz` được proxy tới upstream, và `/api/__smoke` tới đúng upstream với đường dẫn tuyệt đối được giữ nguyên. Smoke này không khởi chạy Orchestrator/DB/Redis thật; integration test thật là suite riêng.
- Một feature chỉ "xong" khi đồng thời: `tsc` + `vite build` pass; đủ bốn trạng thái hiển thị; không JSON dump; có media preview thật ở nơi có media; test của feature pass; lint pass; không vi phạm các mục 3–6 của tài liệu này. PR thay đổi giao diện phải kèm **ảnh chụp màn hình** các màn hình chính đã thay đổi (trước/sau nếu là viết lại) trong mô tả PR.

## 8. Lộ trình chuyển đổi từ trạng thái hiện tại

Mỗi phase là một PR riêng; không gộp refactor nền móng với viết lại nhiều feature trong cùng một PR.

> **Lịch sử quyết định — PR #42 (đã đóng, không merge):** PR `feat/studio-web-workspace` từng thử nới Docker build context lên root monorepo, mở rộng contracts viết tay, thêm CSS viết tay và hiển thị JSON thô. PR này đã được đóng mà không merge. Phạm vi chức năng (các module và luồng người dùng) được giữ làm đầu vào yêu cầu cho Phase 1–2, nhưng mã của nhánh đó không được dùng làm nền. ADR-007 ghi lại lý do chọn generated OpenAPI contracts và giữ build context của frontend độc lập.

- **Phase 0a — Frontend foundation:** cài stack đã chốt; commit `package-lock.json`; đổi cả hai Dockerfile frontend sang `npm ci`; thêm `.dockerignore`; dựng `lib/api/client.ts`, router/app shell, theme token trong `styles/globals.css`, ESLint/Prettier và luật layering/raw API access; đưa frontend lint vào `scripts/lint.sh` và CI; giữ `scripts/test.sh web` làm gate typecheck/build và cập nhật job `frontend-build` để cache theo lockfile, chạy `npm ci`, unit test và Playwright smoke trên Vite production preview. Nginx-container smoke được bổ sung vào CI và chạy sau khi production images được build. Chưa viết lại feature.
- **Phase 0b — Pipeline OpenAPI (PR #52):** `scripts/export_openapi.py` xuất tài liệu OpenAPI của FastAPI ở chế độ offline vào `apps/orchestrator/openapi.json`; `apps/studio-web/src/lib/api/schema.d.ts` được sinh từ snapshot đã commit bằng CLI `openapi-typescript@7.13.0`. Exporter đặt `ENVIRONMENT=development` trước khi import settings và không khởi chạy FastAPI lifespan, vì vậy không cần DB hoặc Redis. CI đồng bộ dependency Python đã khóa và cài dependency frontend bằng `npm ci` ở các job độc lập; bộ test Python kiểm tra tính tái lập và phát hiện snapshot lỗi thời, còn job OpenAPI kiểm tra snapshot cùng TypeScript schema ở chế độ chỉ đọc. Chạy `bash scripts/check_openapi.sh` để kiểm tra cả hai artifact tại máy local; `scripts/test.sh all` cũng bao gồm bước này. Không sửa tay file được sinh tự động: cập nhật route/model, xuất lại snapshot, sinh lại types, xem diff rồi commit các artifact liên quan cùng nhau.
- **Phase 0c — Typed API + migration consumers:** backend response contracts và generated snapshots đã merge ở PR #56–#59; CI hiện bắt buộc response model cho 13 router và audit OpenAPI xác nhận 0 generic/missing success response schemas. PR #60 bắt đầu consumer migration bằng cách chuyển các query hiện có (`/api/v1/projects`, `/api/v1/connections`, `/healthz`) sang kiểu suy từ generated `paths` và thêm test cho kiểu response; các feature tiếp theo phải làm tương tự khi được triển khai, không khai báo interface response song song. `packages/contracts` đã được gỡ sau khi scan xác nhận không còn import. Phase 0a/0b là prerequisite; queue-monitor chỉ được bắt đầu sau khi queue API và consumer contract cần thiết đủ typed; shot-board chỉ khi pipeline + queue + batch/takes đủ typed. Không chặn feature của router đã sẵn sàng chỉ vì router không liên quan còn thiếu response model.
- **Phase 1 — Vòng sản xuất lõi:** Queue Monitor đã merge (commit a6c6fbd) sau khi queue contract đã typed; shot-board phần A (danh sách/sửa shot, enqueue, batch launch) đã có consumer typed và test với API mock; phần B (media preview + duyệt take A/B, chọn take, flag continuity) đã có consumer typed và test với API mock, chưa có bằng chứng với backend thật.
- **Phase 2 — Quản trị:** script-studio, projects, connections, system-ops sau khi các router tương ứng đã typed.
- **Phase 3 — Workflow canvas:** dựng lại bằng @xyflow/react sau khi workflow request/response schema đã typed theo mục 5.5.
- **Phase 4 — Timeline editor:** dựng theo mục 5.6 khi timeline/export schema đã typed, gồm export job polling.

## 9. Anti-patterns bị cấm (rút từ các bản sinh tự động đã bị loại)

- Tự chế hook fetch/polling thay cho TanStack Query.
- Định nghĩa tay API types/payload trong frontend, hoặc gọi endpoint chưa đối chiếu OpenAPI.
- Hiển thị JSON thô thay cho giao diện domain; "canvas" không có canvas; "timeline" không có trục thời gian.
- CSS toàn cục viết tay dài, hard-code màu, không design token; giao diện không icon hoặc dùng emoji thay icon.
- Lưu admin token xuống storage của trình duyệt.
- Sao chép server data hoặc trạng thái đã có trên URL vào Zustand store.
- Nới Docker build context để "lách" quy tắc import nội bộ app, thay vì đưa thứ cần dùng vào trong app.
- Import ra ngoài `apps/studio-web` (kể cả alias tới `packages/contracts`).
- Nhánh/PR gộp nhiều phase, hoặc tuyên bố "đã kiểm thử" khi chưa chạy build/test thật (theo chuẩn báo cáo bằng chứng trong `AGENTS.md`).
