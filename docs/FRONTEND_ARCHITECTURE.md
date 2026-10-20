# Kiến trúc Frontend chuẩn cho `apps/studio-web`

Tài liệu này là **chuẩn bắt buộc** cho mọi thay đổi trong `apps/studio-web`, áp dụng cho mọi coding agent (Claude, Gemini, Codex, người) làm việc trong repo. Nó cụ thể hoá — không thay thế — hợp đồng chung trong `AGENTS.md` và kiến trúc tổng thể trong `docs/ARCHITECTURE.md`. Các lựa chọn nền tảng được ghi thành ADR-006 và ADR-007 trong `docs/DECISIONS.md`.

Bối cảnh: backend (M1–M7) đã có API đầy đủ và ổn định, trong khi frontend từng chỉ là một file `App.tsx` placeholder và nhiều bản giao diện sinh tự động (hand-rolled CSS, không thư viện component/icon, tự chế data-fetching, contracts viết tay trôi khỏi API thật, hiển thị JSON thô thay cho media). Tài liệu này khoá các quyết định để tình trạng đó không lặp lại.

## 1. Nguyên tắc nền tảng

1. **Media-first.** MuseStudio là studio sản xuất video: shot, take và asset phải được hiển thị bằng media thật (frame, video preview) chứ không phải bằng văn bản mô tả hay JSON thô.
2. **Type-safe từ đầu đến cuối.** Mọi kiểu dữ liệu trao đổi với backend phải suy ra từ OpenAPI schema của chính backend. Không tồn tại nguồn type thứ hai do người/agent viết tay.
3. **Server state và client state là hai thứ khác nhau.** Server state đi qua TanStack Query; client state thuần UI đi qua Zustand. Không trộn lẫn, không sao chép server data vào store.
4. **Mọi thứ bản build cần phải nằm trong `apps/studio-web`.** Đây là build context của cả hai Dockerfile của studio-web; import ra ngoài thư mục này là phá build (xem mục 6).
5. **Chỉ dùng endpoint có thật.** Trước khi viết một lời gọi API, đối chiếu OpenAPI schema (`/openapi.json` của orchestrator) hoặc router tương ứng trong `apps/orchestrator`. Cấm đoán đường dẫn từ trí nhớ hoặc từ tài liệu mô tả.

## 2. Stack chuẩn

| Hạng mục | Lựa chọn | Ghi chú |
|---|---|---|
| Framework | React 18 + TypeScript (strict) + Vite | Đã có sẵn; giữ nguyên. |
| CSS / design system | **Tailwind CSS** + **shadcn/ui** (trên Radix primitives) | Theme bằng CSS variables của shadcn; dark theme là theme mặc định của sản phẩm. |
| Icons | **lucide-react** | Nguồn icon duy nhất. Cấm dùng emoji làm icon, cấm nhúng SVG rời tự vẽ cho các khái niệm đã có trong Lucide. |
| Server state / data fetching | **TanStack Query** | Query, mutation, polling (`refetchInterval`), invalidate theo query key. |
| Client state | **Zustand** | Project đang chọn, trạng thái UI, admin token trong bộ nhớ. |
| Forms | **react-hook-form** + **zod** | Schema validate suy từ generated types khi có thể. |
| Workflow canvas | **@xyflow/react** (React Flow) | Bắt buộc cho module workflow-canvas (M3); node/edge là component typed. |
| Kéo–thả | **dnd-kit** | Sắp xếp shot trên board, clip trên timeline. |
| Bảng / danh sách dài | TanStack Table / @tanstack/react-virtual | Chỉ thêm khi một màn hình thực sự cần. |
| Test | **Vitest** + Testing Library + MSW | Mock API theo OpenAPI; xem mục 7. |
| Lint / format | ESLint + Prettier cho frontend | Phải được thêm ở Phase 0; hiện repo chưa có. |
| Lockfile | `package-lock.json` | Bắt buộc commit; cài đặt trong CI/Docker bằng `npm ci`. Cấm sửa lockfile bằng tay (theo `AGENTS.md`). |

Thư viện ngoài danh sách này chỉ được thêm khi có nhu cầu cụ thể của một feature và được nêu lý do trong PR. Không thêm thư viện "để dành".

## 3. Contracts: sinh tự động từ OpenAPI — cấm viết tay

- Nguồn sự thật duy nhất cho API types là OpenAPI schema của orchestrator (FastAPI phục vụ tại `/openapi.json`).
- Lệnh chuẩn (script trong `apps/studio-web/package.json`):

  ```bash
  npm run gen:api   # openapi-typescript -> src/lib/api/schema.d.ts
  ```

  File sinh ra có header "generated — do not edit", không bao giờ sửa tay. PR thay đổi hành vi API ở backend phải kèm file generated đã cập nhật ở frontend (hoặc ghi rõ vì sao chưa cần).
- Mọi response/request type trong features lấy từ `components['schemas'][...]` của generated schema. **Cấm** dùng `Record<string, unknown>` làm kiểu response trong features, và cấm định nghĩa interface payload/response song song trong file service.
- `packages/contracts`: **ngừng mở rộng**. Bốn union type hiện có được giữ cho tới khi có bản generated tương đương, sau đó package bị loại bỏ. Lý do: package nằm ngoài Docker build context của studio-web (mọi nỗ lực wiring nó vào frontend đều phá Docker build) và nội dung viết tay đã nhiều lần trôi khỏi API thật. Từ nay contracts sống trong `apps/studio-web/src/lib/api/` cùng với app duy nhất tiêu thụ chúng.

## 4. Cấu trúc thư mục chuẩn

```text
apps/studio-web/src/
  main.tsx
  app/                  # App shell: providers (QueryClient, theme), layout, điều hướng tab/route, error boundary
  components/
    ui/                 # Component shadcn/ui (sinh bằng shadcn CLI, chỉnh sửa tối thiểu)
    shared/             # Component dùng chung không gắn domain: StatusBadge, AsyncState,
                        # MediaThumb, ConfirmActionDialog, TakeCompare, ...
  features/
    projects/           # Mỗi feature gồm: api.ts (query/mutation hooks), components/,
    connections/        #   pages/ (component cấp route), index.ts (public surface)
    script-studio/
    shot-board/
    workflow-canvas/
    queue-monitor/
    timeline-editor/
    system-ops/
  lib/
    api/                # http client (fetch wrapper), schema.d.ts (generated), queryKeys.ts
    stores/             # Zustand stores
    utils.ts            # cn() và helpers thuần
  styles/
    globals.css         # Tailwind entry + theme tokens duy nhất
```

Quy tắc phụ thuộc:

- Chỉ `lib/api` được gọi `fetch`. Component và hook trong features không tự gọi mạng.
- Features **không import lẫn nhau**; phần dùng chung đi qua `components/shared/` hoặc được compose ở `app/`.
- `components/ui` không import features hay `lib/api`.
- Alias đường dẫn chỉ nội bộ app: `@/` trỏ tới `src/`. Cấm mọi import dạng `../../` ra khỏi `apps/studio-web`.

## 5. Quy tắc triển khai theo lớp

### 5.1 API client & data fetching

- Http client (`lib/api`) chịu trách nhiệm: gắn header `X-Studio-Admin-Token` (lấy từ store), chuẩn hoá lỗi thành `ApiError` gồm `status` + `detail` (đã qua redaction ở backend — không tự log thêm nội dung nhạy cảm), hỗ trợ huỷ bằng `AbortSignal` của TanStack Query, và tải file có auth qua blob + object URL (nhớ `revokeObjectURL`).
- Query key theo mẫu `['<feature>', ...ids, paramsObject]`, khai báo tập trung ở `lib/api/queryKeys.ts`.
- Polling chỉ qua `refetchInterval` và phải có điều kiện dừng ở trạng thái terminal (succeeded/failed/canceled). Cấm tự chế hook kiểu `useAsyncData`/`usePolling` bằng `useEffect` + `setInterval` — mẫu này đã gây lỗi race/abort trong các bản sinh tự động trước đây.
- Mutation thành công phải invalidate đúng query keys liên quan; lỗi mutation phải hiển thị cho người dùng, không nuốt lỗi.
- Admin token chỉ giữ **trong bộ nhớ** (Zustand store, không persist; quy tắc AC-14). Cấm lưu vào `localStorage`/`sessionStorage`.

### 5.2 Components & trạng thái hiển thị

- Mọi màn hình/danh sách dùng dữ liệu server phải có đủ bốn trạng thái: **loading / empty / error (có nút thử lại) / data**. Thiếu một trong bốn là chưa đạt.
- **Cấm** render JSON thô (`<pre>{JSON.stringify(...)}</pre>`) cho dữ liệu domain trong UI chính thức. Chỉ chấp nhận trong một panel "raw/debug" có chủ đích, mặc định ẩn.
- Component trình bày (presentational) nhận props đã typed; component cấp route/pages nối query hooks. Logic nghiệp vụ của một feature ở trong feature đó, không rò rỉ vào `components/shared`.

### 5.3 Media

- Shot/take/asset phải render thumbnail hoặc video preview thật qua endpoint download có auth (`GET /api/v1/assets/{id}/download`, `GET /api/v1/backups/{id}/download`). Không có asset thì hiển thị placeholder rõ ràng kèm trạng thái generation của shot.
- Duyệt take phải có chế độ so sánh A/B đặt hai take cạnh nhau bằng media thật, kèm thao tác chọn take và gắn cờ continuity — đây là nghiệp vụ lõi của shot-board, không phải trang trí.

### 5.4 Forms, hành động tốn quota & idempotency

- Form dùng react-hook-form + zod; lỗi `422` từ API hiển thị gần field liên quan khi ánh xạ được.
- Mọi hành động có thể phát sinh chi phí/quota ở provider thật (batch launch, enqueue render, workflow full-run, export) phải qua dialog xác nhận nêu rõ: provider + loại (mock/live), model, số lượng shot/task. Provider `mock` phải được ghi nhãn rõ là không tốn phí.
- Các thao tác tạo tài nguyên ở backend có hỗ trợ idempotency phải gắn **idempotency key theo ý định**: giữ nguyên key khi người dùng retry cùng một ý định, sinh key mới khi tham số thay đổi. Không sinh key mới ở mỗi lần render/retry.

### 5.5 Workflow canvas (M3)

- Dùng `@xyflow/react` với custom node components typed theo generated schema; trạng thái node ánh xạ từ `NodeExecutionState` sang theme tokens.
- Chế độ chạy mặc định là `dry_run`; chuyển sang chạy thật phải qua xác nhận ở mục 5.4. Thao tác approve manual gate là hành động first-class trên chính node đang `waiting_approval`, không phải form nhập ID rời rạc.

### 5.6 Timeline & export (M6)

- Timeline editor quản lý model clips (thứ tự, trim, transition) trong store của feature; sắp xếp bằng dnd-kit; preview bằng phần tử `<video>` với asset thật.
- Export phía backend là **bất đồng bộ**: `POST /api/v1/projects/{id}/timeline/exports` trả `202` và một export job ở trạng thái `queued`; UI phải poll `GET /api/v1/projects/{id}/timeline/exports/{export_id}` cho tới trạng thái terminal (`succeeded`/`failed`) rồi mới thông báo kết quả và hiện liên kết tải. Cấm hiển thị "hoàn tất" ngay sau khi POST thành công.

### 5.7 Styling & icons

- Viết style bằng Tailwind utilities và component shadcn; màu sắc/spacing lấy từ theme tokens (CSS variables). Cấm hard-code mã hex rải rác trong component và cấm thêm file CSS toàn cục mới ngoài `styles/globals.css`.
- Mọi nút bấm có icon từ lucide-react đi kèm nhãn chữ hoặc `aria-label` (với nút chỉ-icon). Badge trạng thái dùng token màu + chữ, ánh xạ nhất quán từ các state union của domain (approval/generation/node/health).

### 5.8 Accessibility

- Dùng Radix primitives cho dialog, tabs, menu, popover để có sẵn hành vi bàn phím/focus. Điều hướng chính dùng đúng role (`tablist`/`tab`/`tabpanel`). Mọi control phải tới được bằng bàn phím và có focus visible.

## 6. Bất biến build & Docker

- Cả hai Dockerfile của studio-web build với context là `apps/studio-web`: mọi import phải resolve bên trong thư mục này (hệ quả trực tiếp của nguyên tắc 4, mục 1).
- Dependency khai báo trong `apps/studio-web/package.json` và khoá bằng `package-lock.json`; thay đổi dependency phải đi qua package manager.
- `VITE_ORCHESTRATOR_URL` giữ cơ chế hiện tại: `.` cho bản prod (same-origin qua nginx) và URL tuyệt đối cho dev. Không hard-code host/port trong code feature.

## 7. Kiểm thử & Definition of Done cho một feature

- Vitest + Testing Library cho component/hook; MSW mock HTTP dựa trên OpenAPI cho tầng api của feature. Ít nhất: test cho query/mutation hooks chính và cho các component có logic trạng thái (badge mapping, confirm dialog, A/B compare).
- Một feature chỉ "xong" khi đồng thời: `tsc` + `vite build` pass; đủ bốn trạng thái hiển thị; không JSON dump; có media preview thật ở nơi có media; test của feature pass; lint pass; không vi phạm các mục 3–6 của tài liệu này.

## 8. Lộ trình chuyển đổi từ trạng thái hiện tại

Mỗi phase là một PR riêng; không gộp refactor nền móng với viết lại nhiều feature trong cùng một PR.

- **Phase 0 — Nền móng:** thêm Tailwind + shadcn/ui + lucide-react + TanStack Query + Zustand + react-hook-form/zod, ESLint/Prettier, `package-lock.json`; dựng `lib/api` (http client + generated schema + `gen:api`), app shell + theme. Chưa viết lại feature nào.
- **Phase 1 — Vòng sản xuất lõi:** shot-board (media preview + duyệt take A/B) và queue-monitor (polling bằng TanStack Query).
- **Phase 2 — Quản trị:** script-studio, projects, connections, system-ops theo chuẩn form ở mục 5.4.
- **Phase 3 — Workflow canvas:** dựng lại bằng @xyflow/react theo mục 5.5.
- **Phase 4 — Timeline editor:** dựng theo mục 5.6, gồm export job polling.

## 9. Anti-patterns bị cấm (rút từ các bản sinh tự động đã bị loại)

- Tự chế hook fetch/polling thay cho TanStack Query.
- Định nghĩa tay API types/payload trong frontend, hoặc gọi endpoint chưa đối chiếu OpenAPI.
- Hiển thị JSON thô thay cho giao diện domain; "canvas" không có canvas; "timeline" không có trục thời gian.
- CSS toàn cục viết tay dài, hard-code màu, không design token; giao diện không icon hoặc dùng emoji thay icon.
- Lưu admin token xuống storage của trình duyệt.
- Import ra ngoài `apps/studio-web` (kể cả alias tới `packages/contracts`).
- Nhánh/PR gộp nhiều phase, hoặc tuyên bố "đã kiểm thử" khi chưa chạy build/test thật (theo chuẩn báo cáo bằng chứng trong `AGENTS.md`).
