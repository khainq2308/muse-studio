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
| Frontend product feature UI | Queue Monitor đã merge (commit a6c6fbd), kèm trả lời câu hỏi operator (needs-input) cho task `awaiting_input` trên nhánh `feat/needs-input` (unit test, chưa có bằng chứng browser với provider thật). Shot Board phần A trên nhánh `feat/shot-board-list`: route `/projects/:projectId/shot-board`, danh sách shot theo scene, sửa shot qua `PATCH` (lỗi 422 hiển thị tại field, prompt đã biên dịch lại chỉ đọc), enqueue một shot và batch launch có xác nhận, hiển thị cảnh báo `frame_chain_continuity_not_guaranteed` và shot bị chặn; điều hướng project Shot Board ↔ Queue Monitor. Bằng chứng: Vitest + Playwright với API mock (`page.route()`), chưa có bằng chứng browser với backend thật. Shot Board phần B trên nhánh `feat/shot-board-takes`: panel duyệt take theo shot (badge selected/continuity, director notes, thumbnail first/last frame lazy-load), preview video/ảnh theo yêu cầu qua `apiGetBlob` (tải có header admin, giới hạn kích thước, object URL được revoke), so sánh A/B hai take cạnh nhau dùng `compare-takes` (thao tác được bằng bàn phím), chọn take (invalidate danh sách shot/take, hiển thị last frame mới và các shot downstream được trỏ lại), flag continuity kèm tuỳ chọn regenerate có xác nhận quota và link tới Queue Monitor, lỗi 4xx hiển thị inline; cảnh báo không chặn khi previous shot có `sequence_index` ≥ shot hiện tại. Kết quả không chắc chắn của flag + regenerate (timeout, lỗi mạng, 5xx, hoặc 422 sau khi flag đã lưu) được báo là "outcome unknown" kèm link Queue Monitor và nút làm mới take, không mời thử lại vì backend lưu flag trước rồi enqueue với `force_retry`. Bằng chứng: Vitest + Playwright với API mock; Ngày 2026-10-23 nhánh đã được chạy bằng Chromium (script Playwright) với stack local thật — uvicorn API + ARQ worker thật + Redis + SQLite, connection provider `mock`, không dùng provider tính phí: danh sách take với thumbnail frame có xác thực (blob URL), so sánh A/B hai take thật, chọn take 2 trỏ lại Shot 2 nối frame (UI liệt kê shot đó; backend đặt nó về `not_started` trên frame mới), flag continuity kèm regenerate tạo task `tsk_82db1ed740` (priority 80) được worker hoàn thành thành take mới; không có lỗi API (bằng chứng do coordinator thu thập). Chưa kiểm phát video của provider thật (muse.ai); danh sách take hiển thị thời điểm tạo `created_at` (`<time datetime="...">`). Script Studio đã merge ở PR #82. Trên nhánh `feat/shot-board-workspace`: Shot Board được tổ chức lại thành workspace master–detail thay cho trang dài gồm các card mở rộng inline. Rail trái liệt kê scene → shot (sequence index, tiêu đề, chip generation/approval/lint/frame-chain, thumbnail last frame của take đã chọn tải lazy khi shot có `selected_take_id` và `last_frame_asset_id`), lọc theo trường thật (All / Needs take = chưa có `selected_take_id` / Blocked = `blocked_reference`, `blocked_upstream_frame` hoặc lint blocker / Approved), điều hướng bằng bàn phím (Arrow/Home/End, `aria-current` cho shot đang mở); dưới `lg` rail thu gọn sau nút "Show shot list". Panel chính của shot đang chọn có header (first/last frame của shot, trạng thái, Generate với xác nhận hiện có, Edit, Prev/Next) và tab ARIA "Takes" (take review hiện có), "Details" (trường chỉ đọc, thiếu thì "—"), "Prompt & lint". Shot và tab nằm trên URL (`?shot=<id>&tab=takes|details|prompt`, `validateSearch` của route `/projects/$projectId/$module`; giá trị sai bị bỏ, không mang sang module khác); không có `shot` thì mở shot đầu tiên, không có `tab` thì mở Takes khi shot có take, ngược lại Details (take list lỗi thì mở Details và đánh dấu tab Takes). Generation settings vẫn ở đầu trang, Batch launch nằm sau nút toggle trên toolbar với nguyên các xác nhận/cảnh báo/link Queue Monitor. Bằng chứng: Vitest (page integration với harness search, test router thật với memory history cho deep link/back/validateSearch, unit test filter/default tab) và Playwright với API mock (`e2e/shot-board*.spec.ts`, `e2e/ui-viewports.spec.ts` chụp 4 viewport, không tràn ngang ở 390px; ảnh đã được xem lại). Chưa chạy với backend thật. Trên nhánh `feat/ui-ux-light-redesign`: giao diện được chuyển sang Light Mode First, bổ sung điều hướng toàn bộ 8 module (`script-studio`, `shot-board`, `workflow-canvas`, `queue-monitor`, `timeline-editor`, `connections`, `projects`, `system-ops`), tạo/nhân bản project, quản lý connection & account pool, Workflow DAG simulation/dry-run/manual approval, Timeline assemble/MP4 export polling, và System Ops (backups, orphan scan/cleanup, assets, metrics/logs). Workflow Canvas dựng bằng React Flow; workflow chỉ sống trong phiên trình duyệt vì backend chưa có endpoint GET workflow. Nhân bản project chỉ sao chép thiết lập project (không sao chép Story Bible, shot hay asset). Timeline export chỉ cho chọn resolution khớp aspect ratio (backend không tự đối chiếu hai giá trị này) và liệt kê trước khi export các phần exporter chưa render (audio của clip, audio track/BGM, phụ đề, title card); BGM chưa gắn được file âm thanh nên chỉ là metadata. Orphan cleanup quét toàn bộ media root của mọi project và chỉ bật xoá thật sau dry-run cùng retention. Các thao tác xoá/restore/approve có bước xác nhận. Timeline Editor là màn assembly + export, không còn hiển thị thông số hay preview giả. Trên nhánh `feat/timeline-clip-editing`, Timeline Editor có chỉnh sửa clip thật: bản nháp cục bộ khởi tạo từ `manifest.video_clips` (reset khi `sequence_id` đổi hoặc Discard), trim start/end, transition từng clip (clip đầu luôn hard cut), khoảng trống (gap) trước clip, xoá clip có xác nhận, sắp xếp lại bằng dnd-kit (kéo thả, KeyboardSensor trên grip) và nút Move left/right; validation trực tiếp theo bound của `ClipEditItemIn` và exporter (trim_start + trim_end để lại ≥ 0,1 s, dissolve ≤ 5 s và ngắn hơn hai clip kề, không dissolve qua gap). "Save clip edits" gửi `POST /timeline/assemble` với `custom_clips` (order_index đánh lại 1..n, clip không có gap gửi `start_time_seconds: null` để backend xếp nối tiếp) và mang theo name, aspect ratio, fps, title card, audio track từ GET; vì GET không trả nội dung phụ đề, khi sequence có phụ đề mà ô "Subtitle cues" trống thì phải xác nhận việc xoá phụ đề; lỗi 422 `custom_clips[i]` được gắn vào clip. Export và Assemble bị khoá khi còn chỉnh sửa chưa lưu (export dùng sequence đã lưu). Volume/fade của clip không hiển thị vì exporter không render audio clip và được gửi giá trị mặc định. Bằng chứng: Vitest (`clip-draft.test.ts`, test trang với fetch stub kiểm tra body `custom_clips`) và Playwright với API mock (trim + reorder bằng bàn phím + lưu, kiểm tra body POST; ảnh `timeline-editor-clip-edits-dirty.png`); chưa chạy với backend/FFmpeg thật, chưa có trim handle kéo cạnh clip hay thêm clip từ thư viện; Shot Board và Script Studio bỏ các giá trị fallback bịa và control không có tác dụng. Bằng chứng: Vitest (component test với fetch mock, kiểm tra method/path/body và đường lỗi); Playwright với API mock (`e2e/production-modules.spec.ts` cho Projects, Connections, Workflow Canvas, Timeline Editor, System Ops) và `e2e/ui-viewports.spec.ts` chụp ảnh 7 module ở 1440×900, 1280×800, 768×1024, 390×844 kèm kiểm tra không tràn ngang (ảnh ở `test-results/ui-evidence/`, chỉ Chromium). Chưa chạy với backend thật. Trên nhánh `feat/ux-workspace-navigation`: điều hướng tách hai cấp (mục 5.9). Header có Overview / Projects / Connections / System; trong project là 4 bước đánh số (Script & Story Bible, Shot Board, Generation Queue, Timeline & Export) cùng Workflow Automation (Advanced) và Project Settings; tiêu đề trang đổi theo nhãn nav. Connections và System (metrics, restore backup, orphan scan/cleanup, xoá asset theo ID, logs toàn workspace) chuyển ra route workspace; backup, upload asset và log của project nằm trong Project Settings; trang Projects tách thành danh sách + tạo project (`/projects`) và settings của project. Overview thay 5 thẻ giới thiệu bằng 4 project có `updated_at` mới nhất (field của dòng project, không tăng khi shot/take đổi) với tiến độ shot và bước tiếp theo (import script → xử lý shot bị chặn, deep-link `?shot=&tab=` tới tab giải thích → xem shot lỗi trong queue → chọn take → chờ queue → assemble), cùng tình trạng connection chỉ đọc. Shot Board bỏ dải link module lặp lại trong header. URL cũ được redirect. Bằng chứng: Vitest (`progress.test.ts`, `workspace-navigation.integration.test.tsx` với router thật, test trang đã tách) và Playwright với API mock (`e2e/workspace-navigation.spec.ts` theo tác vụ, `e2e/ui-viewports.spec.ts` chụp 9 trang × 4 viewport, không tràn ngang; ảnh đã được xem lại). Chưa chạy với backend thật; tiến độ Overview chỉ tính cho 4 project gần nhất. Trên nhánh `feat/ux-script-studio-split`: Script & Story Bible tách thành hai view có tab ARIA, giữ trên URL `?view=script|bible` (bàn phím Arrow/Home/End, back/forward của trình duyệt). Không có `view` thì trang chờ cả Story Bible lẫn shot rồi chọn một lần (project trống → Script import; có Story Bible/shot hoặc Story Bible tải lỗi → Story Bible) và ghi vào URL bằng `replace`, nên refetch sau import không đổi view. Hai panel luôn mounted (panel không active bị `hidden`) để chuyển view không mất script đang nhập hay chỉnh sửa Story Bible chưa lưu. View Story Bible có khối "Needs a reference image": character `requires_reference_lock` mà chưa có reference asset, kèm số shot có lint `BLOCKED_REFERENCE` trên `subject_ids` và dùng character đó (`subject_codes`), cùng nút mở editor của entry. Đây chỉ là một nguồn `BLOCKED_REFERENCE` — reference lock của chính shot, asset reference đã bị xoá, và (khi batch launch) mọi lint blocker khác cũng chặn shot — nên copy nói shot "cần" reference và có thể còn blocker khác; khi shot bị chặn mà không có mục Story Bible nào để sửa, bước tiếp theo trỏ sang Shot Board để xem lý do. Tab hiển thị số entry và số mục cần sửa. Kết quả import có nút "Review Story Bible". Export shot list (JSON/CSV) gom thành một nhóm trên header; bỏ link Shot Board lặp lại và header trang trí dùng inline style. Bằng chứng: Vitest (`search.test.ts`, `attention.test.ts`, test trang, `script-studio-url.integration.test.tsx` với router thật) và Playwright với API mock (`e2e/script-studio.spec.ts` đi qua tab Script import → import → Review Story Bible → sửa entry; ảnh viewport đã xem lại). Chưa chạy với backend thật. Shot Board — generation và queue: khối Generation settings ở đầu trang được thay bằng dòng tóm tắt "Generating with <connection> (mock/live) · <model> · <output>" và nút Change/Set up generation mở form cũ; dòng tóm tắt cũng báo lỗi tải connection và trường hợp không có connection nào đang bật. Lựa chọn giữ theo project trong store Zustand chỉ trong bộ nhớ (`features/shot-board/generation-settings-store.ts`; sống qua việc chuyển module và đổi admin token vì connection luôn được đối chiếu lại với danh sách, không qua reload). Connection đã lưu mà nay bị tắt/xoá được coi là chưa chọn; khi chưa chọn và có đúng một connection đang bật thì tự chọn nó và model đầu tiên lần test connection gần nhất tìm thấy (mọi lần chạy vẫn qua xác nhận nêu provider/chi phí). Nút Generate bị khoá có lối tắt "Set up generation" mở setup và chuyển focus vào ô đầu tiên. Trạng thái queue hiển thị ngay trên board từ `GET /api/v1/queue/tasks?project_id=&limit=100` (`entities/task/api.ts`, task mới nhất trước): dải hoạt động (live region luôn mounted; task đang chạy/chờ và chờ trả lời lấy từ `summary`; số shot lỗi đếm theo task mới nhất của từng shot, không theo `failed_tasks` toàn thời gian; báo khi không tải được trạng thái và khi auto-refresh đã dừng) và thẻ "Latest generation" của shot đang mở (trạng thái, model/connection của task, số lần tự thử `attempt_count`/`max_attempts` trong task, `error_message_redacted`/`error_category`, cảnh báo khi `retryable=false`, link Generation Queue; shot có task cũ hơn 100 task mới nhất không được hiển thị là "không có task"). Task đã kết thúc (failed/poll_timeout/canceled) có "Start a new task", và kết quả Generate trùng một task đã kết thúc cũng có "Start a new task" — cả hai dùng setup hiện tại và mở xác nhận riêng nói rõ sẽ tạo task mới và có thể tốn quota, rồi mới gửi `force_retry: true`. Task polling dùng chung chính sách của Generation Queue (chuyển xuống `entities/task/state.ts`: cửa sổ 30 phút, backoff, dừng khi không còn task active hoặc lỗi không retry được); cửa sổ khởi động lại khi số task active tăng (việc mới từ bất kỳ đâu, kể cả regenerate trong take review). Danh sách shot chỉ poll 5 s khi task query còn đang poll và còn task active, nên dừng cùng cửa sổ/lỗi của nó; khi một task đang active (theo ID) không còn active thì shot và take được invalidate ngay. Bằng chứng: Vitest (`queue-status.test.ts`, `generation-settings-store.test.ts`, test trang: tự chọn connection và giữ lựa chọn khi remount, dải hoạt động, lý do lỗi + "Start a new task" gửi `force_retry`, "Start a new task" sau kết quả trùng, shot tải lại ngay khi task kết thúc, ngừng poll shot sau cửa sổ 30 phút kèm thông báo — test này fail khi bỏ điều kiện `isPolling` —, không poll shot khi status lỗi, focus vào setup từ lối tắt; các test polling dùng fake timer) và Playwright với API mock (`e2e/shot-board-generation.spec.ts` theo tác vụ, các spec Shot Board cập nhật). Chưa chạy với backend thật. Đồng nhất giao diện + Timeline & Export: mọi trang workspace và module project dùng một `PageHeader` (`components/shared/page-header.tsx`: h2 `text-xl`, mô tả, badge, vùng action) với tiêu đề trùng nhãn nav và mô tả bằng ngôn ngữ người dùng (bỏ "Async FFmpeg MP4 Render", "FFprobe Gate", "Sequence Assembly Configuration", "Provider Connections & Account Pools"...); khối "Backups & assets" trong Project Settings là h3. Route module project không còn bọc nội dung trong card thứ hai. Timeline & Export đặt dải timeline và "Clip edits" lên đầu, sau đó Selected clip (đứng trước trong DOM để người dùng bàn phím tới ngay inspector sau khi chọn clip) / Assembly settings / Export MP4; khối Export liệt kê trước khi bấm "This MP4 will contain" (số clip của sequence đã lưu theo thứ tự timeline với trim và gap thành nền đen, dissolve có thể bị rút ngắn khi dài hơn clip kề — đúng filtergraph của `timeline_service.py` —, resolution/fps đã chọn, track âm thanh câm; ẩn và khoá Export khi sequence không có clip) cùng "It will not include" do client tính bằng `skippedExportFeatures` (bản sao của `_unsupported_export_features` phía server; server chỉ trả `unsupported_features` sau khi launch). Dòng Duration ghi chú khi sequence có title card: thời lượng title card được backend cộng vào nhưng không được render, nên MP4 ngắn hơn. Workflow Automation đổi nhãn "Initialize M3 Pipeline DAG"/"Create new M3 DAG" thành "Create pipeline workflow"/"Create a new workflow" và mô tả đúng phạm vi (xem và dry-run pipeline có sẵn, không tự dựng graph). Bằng chứng: Vitest và Playwright cập nhật theo tiêu đề/nhãn mới, `e2e/ui-viewports.spec.ts` chụp 9 trang × 4 viewport không tràn ngang (ảnh đã xem lại). Chưa chạy với backend thật | Chỉ đánh dấu theo evidence của từng phase feature; M4 vẫn partial đến khi worker/API enqueue được xác minh end-to-end |
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
| Điều hướng / URL state | **TanStack Router** (v1) | Project đang chọn và module hiện tại nằm trên URL (`/projects/:projectId/<module>`); công cụ cấp workspace có route riêng (`/`, `/projects`, `/connections`, `/system`) — xem mục 5.9, deep-link được và sống sót qua reload; không giữ các thông tin này chỉ trong store. |
| CSS / design system | **Tailwind CSS v4** + **shadcn/ui** (trên Radix primitives) | Theme bằng CSS variables của shadcn; Light Mode là theme mặc định và duy nhất của sản phẩm (`:root` `color-scheme: light`); các vùng preview media dùng nền tối cố định ngay trong component media. |
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
    workspace-overview/  # Overview cấp workspace: project gần đây + bước tiếp theo
      progress.ts         # Suy tiến độ/bước tiếp theo từ ProjectShotOut (hàm thuần)
      pages/
        overview-page.tsx
      components/
        project-progress-card.tsx
        connection-health.tsx
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
- `features/workspace-overview/pages/overview-page.tsx` là trang đầu của workspace: compose `useProjects`, `useConnections` và `projectShotsQueryOptions` (`entities/shot`) để hiển thị tối đa 4 project cập nhật gần nhất kèm tiến độ shot và **một** bước tiếp theo do `progress.ts` suy ra từ field thật (`generation_state`, `selected_take_id`, `lint_report`). Backend không có endpoint tóm tắt project nên mỗi thẻ tốn một `GET /projects/{id}/shots`; không tạo/sửa project hay connection tại đây (việc đó ở `/projects` và `/connections`).
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
| `script-studio` | M2 | Import kịch bản, xem trước & áp dụng bản nháp `ScriptRevision`, Story Bible |
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
- `apiGetBlob(path, { maxBytes })` trong `lib/api/client.ts` là transport dùng chung cho preview có auth: từ chối body vượt `maxBytes` theo `Content-Length` hoặc khi đọc stream (ApiError `category: "too_large"`), giữ media type của response; `useObjectUrl` (`lib/media/`) tạo object URL và revoke khi Blob đổi/unmount. Shot Board giới hạn 20 MB cho frame và 200 MB cho preview take; preview video dùng `gcTime: 0` (đóng preview là giải phóng Blob), không tự retry khi timeout/quá giới hạn/404. Trường hợp xấu nhất của so sánh A/B là 2 × 200 MB = 400 MB video trong bộ nhớ trình duyệt (cộng tối đa 4 frame × 20 MB), vì vậy so sánh chỉ tải media khi người dùng mở nó.
- Cơ chế blob + object URL (tải file kèm header auth rồi tạo URL tạm) là giải pháp v1 và có **giới hạn phải tôn trọng**: nó tải toàn bộ file vào bộ nhớ, không seek/range, nên chỉ dùng cho ảnh và preview theo yêu cầu từng item. **Cấm** dùng nó để tải hàng loạt video cho lưới thumbnail. ADR-005 mô tả trích xuất boundary frames để nối shot; không tự suy ra từ ADR này rằng đã có endpoint poster/thumbnail chuyên dụng. Trước khi có nguồn poster đã được xác minh, không tải hàng loạt video để tạo thumbnail ở client. Poster asset/endpoint streaming có range + URL ký ngắn hạn là năng lực backend cần thiết kế và kiểm chứng riêng — cho tới lúc đó, lưới dùng placeholder hoặc poster asset đã tồn tại và video chỉ tải khi người dùng mở xem một item.
- API takes hiện có các field `first_frame_asset_id` và `last_frame_asset_id`; chúng có thể null. Khi có ID, frontend tải ảnh PNG đầy đủ qua endpoint asset download dùng chung và chỉ lazy-load khi item sắp hiển thị/được mở. Đây là asset frame hiện có, không phải một endpoint poster chuyên dụng; không tải hàng loạt file ảnh/video về memory.
- Duyệt take phải có chế độ so sánh A/B đặt hai take cạnh nhau bằng media thật, kèm thao tác chọn take và gắn cờ continuity — đây là nghiệp vụ lõi của shot-board, không phải trang trí.

### 5.4 Forms, hành động tốn quota & idempotency

- Form dùng react-hook-form + zod; lỗi `422` từ API hiển thị gần field liên quan khi ánh xạ được.
- Mọi hành động có thể phát sinh chi phí/quota ở provider thật (batch launch, enqueue render, workflow full-run, export) phải qua dialog xác nhận nêu rõ: provider + loại (mock/live), model, số lượng shot/task. Provider `mock` phải được ghi nhãn rõ là không tốn phí.
- Các thao tác tạo tài nguyên phải tôn trọng **cơ chế idempotency thực tế của backend** (đối chiếu với route/service/test hiện hành trên main khi chuẩn bị tích hợp; xác minh lại nếu backend thay đổi), không suy diễn:

  | Endpoint | Cơ chế phía server | Việc phía client |
  |---|---|---|
  | `POST /api/v1/queue/tasks` | `idempotency_key` là optional; khi bỏ key, backend tự chọn key theo nội dung. Response kèm `created_new` (`false` = request trùng, trả lại task của request đó; nếu request đã có các lần chạy `force_retry` thì trả lại lần chạy mới nhất — dù lần đó thành công hay không —, không phải task gốc; khi đó `idempotency_key` trong response là key của lần retry `<key>_retry_<6 hex>` nên client không được đối soát theo key đã gửi). `force_retry` yêu cầu một lần chạy/take mới. | Mặc định **bỏ field `idempotency_key`** và để backend chống trùng. Không tự sinh key ngẫu nhiên ở client. Khi người dùng chủ động retry task đã `failed`, chỉ gửi `force_retry: true` sau dialog xác nhận tạo lượt chạy/take mới vì có thể tốn quota; không dùng nó cho retry mạng cùng một ý định đang được gửi. Nếu `created_new: false`, phải báo rõ request đã khớp và server trả lại task cũ (kể cả task cũ đã failed), hiển thị đúng task/state đó và không báo rằng lượt sinh mới đã bắt đầu. |
  | `POST /api/v1/projects/{id}/batch/launch` | Không nhận key từ client; service tự suy key theo nội dung cho từng task; `only_changed=false` yêu cầu chạy lại cả những shot không đổi | Chống gửi trùng khi mutation đang chạy. Dialog phải hiển thị rõ số lượng shot, cảnh báo quota, và lựa chọn **“Run all regardless of changes”** khi `only_changed=false`; yêu cầu xác nhận riêng trước khi gửi |
  | `POST /api/v1/projects/{id}/timeline/exports` | Chưa hỗ trợ idempotency key | Disable nút khi đang gửi; dựa vào trạng thái job để hiển thị tiến trình, không tự gửi lại |

  Không gửi `Idempotency-Key` header với kỳ vọng server sẽ đọc — hiện backend không đọc header này ở các endpoint trên.

### 5.5 Workflow canvas (M3)

- Dùng `@xyflow/react` với custom node components typed theo generated schema; trạng thái node ánh xạ từ `NodeExecutionState` sang theme tokens.
- Canvas tự động tải workflow mới nhất của project qua `GET /api/v1/projects/{project_id}/workflows` (`useProjectWorkflow`) và khôi phục `nodes`, `edges`, Merkle `cache_keys` cùng vị trí node đã lưu (`ui_layout`).
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
- Mọi trang (workspace và module project) mở đầu bằng `PageHeader` từ `components/shared/page-header.tsx`; tiêu đề trùng nhãn điều hướng, mô tả viết bằng ngôn ngữ người dùng, thuật ngữ kỹ thuật (FFmpeg, Merkle, DAG...) chỉ xuất hiện trong chi tiết khi cần. Không bọc trang trong thêm một card.
- Mọi nút bấm có icon từ lucide-react đi kèm nhãn chữ hoặc `aria-label` (với nút chỉ-icon). Badge trạng thái dùng token màu + chữ, ánh xạ nhất quán từ các state union của domain (approval/generation/node/health).

### 5.8 Accessibility

- Dùng Radix primitives cho dialog, menu, popover để có sẵn hành vi bàn phím/focus. Điều hướng chính giữa các module là **link theo route** với `aria-current="page"` ở mục đang mở (không dùng pattern `tablist` cho điều hướng chuyển trang); pattern tab đúng nghĩa chỉ dùng cho tab nội bộ trong một trang. Mọi control phải tới được bằng bàn phím và có focus visible.

### 5.9 Điều hướng hai cấp: workspace và project

- **Workspace** (header của app shell, `aria-label="Workspace"`): Overview `/`, Projects `/projects`, Connections `/connections`, System `/system`. Đây là công cụ không thuộc một project: connection dùng chung cho mọi project; restore backup tạo project mới; orphan cleanup quét media root của mọi project; xoá asset theo ID dùng ID toàn cục nên nằm ở System và invalidate dữ liệu tham chiếu asset của mọi project. Chúng không được đặt dưới `/projects/:projectId/...`.
- **Project** (`aria-label="Project modules"`): bốn bước sản xuất đánh số theo thứ tự — Script & Story Bible (`script-studio`), Shot Board (`shot-board`), Generation Queue (`queue-monitor`), Timeline & Export (`timeline-editor`) — rồi nhóm phụ gồm Workflow Automation (`workflow-canvas`, gắn nhãn Advanced) và Project Settings (`settings`: profile, revisions, duplicate, backup, upload asset và log lọc theo project). Segment URL giữ ổn định; nhãn và tiêu đề trang phải trùng nhau. Breadcrumb `Projects › <tên project>` đưa về danh sách project.
- State riêng của module nằm trên URL và được validate ở `app/project-search.ts`: Shot Board `?shot=&tab=`, Script & Story Bible `?view=script|bible`; link sang module khác không mang theo các key này.
- `/projects/:projectId` chuyển tới `shot-board`. URL trước khi tách cấp được redirect (`replace`) để bookmark còn dùng được: `connections` → `/connections`, `projects` và `system-ops` → `settings` (định nghĩa ở `app/project-modules.ts`).
- Danh sách shot của project dùng chung một query (`entities/shot/api.ts`) cho Script Studio, Shot Board và Overview; `isShotBlocked` ở `entities/shot/status.ts`; thứ tự navigator (scene theo shot đầu tiên, rồi `sequence_index`) ở `entities/shot/order.ts` để "shot đầu tiên" trên Overview trùng với rail của Shot Board.
- `aria-current="page"` chỉ thuộc module/trang đang mở: link về `/` và `/projects` dùng `activeOptions.exact`; mục Projects ở header chỉ được tô sáng (không mang `aria-current`) khi đang ở trong một project.

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
- **Phase 1 — Vòng sản xuất lõi:** Queue Monitor đã merge (commit a6c6fbd) sau khi queue contract đã typed; shot-board phần A (danh sách/sửa shot, enqueue, batch launch) đã có consumer typed và test với API mock; phần B (media preview + duyệt take A/B, chọn take, flag continuity) đã có consumer typed, test với API mock và một lượt chạy Chromium trên stack local thật với provider `mock` (2026-10-23); chưa kiểm với provider thật.
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
