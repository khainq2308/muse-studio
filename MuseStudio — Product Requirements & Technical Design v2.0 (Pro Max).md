# MuseStudio — Product Requirements & Technical Design v2.0 (Pro Max)

*Nâng cấp từ PRD v1.0 · cập nhật 18/10/2026 · viết bằng tiếng Việt, thuật ngữ kỹ thuật giữ nguyên tiếng Anh*

> **Cách đọc.** Phần 0 là kết quả review (5 phút). Phần 1–4: định hướng và quyết định kiến trúc. Phần 5–13: thiết kế chi tiết. Phần 14–18: lộ trình, nghiệm thu, prompt cho coding agent, rủi ro. Mục gắn **\[Cần xác minh\]** là giả định chưa có bằng chứng; không coi là sự thật cho tới khi kiểm chứng.

## 0. Kết quả review PRD v1.0

### 0.1 Kết luận

PRD v1.0 là bản thiết kế **trưởng thành hơn mức trung bình**: trung thực về năng lực chưa kiểm chứng, mock-first, tách trạng thái, bắt buộc worker queue, pin version trong run snapshot, có exit criteria cho từng milestone. Khung này được giữ (\~80%).

Vấn đề không nằm ở chất lượng viết mà ở **ba quyết định nền móng** cần đổi: (1) toàn bộ lộ trình dựa vào một gateway không chính thức; (2) phạm vi MVP quá rộng nên giá trị đầu tiên đến muộn; (3) thiếu các cơ chế sống còn khi tiêu tiền thật: kiểm soát chi phí, chống gửi trùng, kiểm tra continuity bằng hình ảnh.

### 0.2 Điểm mạnh giữ lại

- Nguyên tắc “không claim khi chưa có artifact thật” + capability matrix `verified / mock-only / unverified / unsupported`.
- Mock provider từ ngày đầu; CI không gọi dịch vụ ngoài.
- Tách các trạng thái (approval / generation / node / connection) thay vì một cột `status`.
- Run snapshot pin version của graph, prompt, Story Bible.
- Secrets không xuống browser; redact ở log/export; FFmpeg args sinh từ schema; chống SSRF.
- Milestone có exit criteria; coding agent dừng sau mỗi milestone.

### 0.3 Phát hiện và thay đổi

| # | Phát hiện trong v1.0 | Mức | Thay đổi trong v2.0 |
| --- | --- | --- | --- |
| F1 | muse2api là gateway không chính thức: bọc web app muse.ai bằng Chromium/CDP, account pool, tự gia hạn session. Dễ gãy khi UI đổi, không có SLA, rủi ro vi phạm điều khoản/khoá tài khoản; giữa v0.1.0 (28/9) và v0.2.0 (4/10) bề mặt API đã đổi. v1.0 xây M4–M7 quanh nó | Critical | ADR-001: lõi provider-agnostic; ≥1 adapter chính thức là hạng nhất; muse2api chỉ là connector **experimental**; M0 có cổng quyết định |
| F2 | Cho phép nhập cookie tài khoản muse.ai vào MuseStudio | High | Bỏ. MuseStudio chỉ giữ API key của gateway; import tài khoản làm trong dashboard của gateway |
| F3 | Scope MVP quá rộng (22 node, canvas, timeline, multi-user); giá trị đầu tiên chỉ đến ở M4+ | High | Walking skeleton ở M1; **Board-first**; Canvas dời sang M5 làm “advanced view” |
| F4 | Không có kiểm soát chi phí (ước tính, ngân sách, draft→final) | High | Cost Guard (§7.6); estimate trong Dry run; ngân sách theo run/project/ngày |
| F5 | Gọi provider không idempotent: crash giữa lúc gửi và lúc ghi task id → sinh trùng, tốn tiền | High | Two-phase submit + reconciliation (§6.4) |
| F6 | Continuity chỉ dựa vào text prompt | High | Visual anchors, Look-dev gate, last-frame chaining, QA assist (§8) |
| F7 | `WaitForTask` mô tả như một node chờ → giữ worker hàng phút | Medium | Durable timer + poll job; worker không ngủ (§6.5) |
| F8 | Ngữ nghĩa cache của node sinh nội dung (không tất định) mơ hồ | Medium | Tách execution state / output freshness / approval; node tốn tiền luôn chạy thủ công (§6.3) |
| F9 | Một `VideoProvider` gánh cả ảnh; phân tích script phụ thuộc chat của muse2api | Medium | Tách LLM / Image / Video / Audio / Transcribe provider (§7.1) |
| F10 | Timeline có track âm thanh/subtitle nhưng không có pipeline tạo chúng | Medium | Audio Plan, TTS/music, subtitle từ script, chuẩn hoá loudness (§9.5) |
| F11 | Tự xây NLE trên browser là hố sâu | Medium | Assembly timeline + proxy + manifest FFmpeg; xuất OTIO/EDL/FCPXML để hoàn thiện ở NLE (§9.5) |
| F12 | Redis + Celery: thêm thành phần vận hành; trạng thái nằm ở hai nơi | Medium | Postgres-first queue, DB là nguồn sự thật; Redis tuỳ chọn (ADR-002/003) |
| F13 | Thiếu: realtime transport, design system, a11y/i18n cụ thể, OpenTelemetry/SLO, ước lượng dung lượng/thời gian, C2PA/AI disclosure, acceptance criteria đo được | Medium | Phần 4, 10–13, 15 |
| F14 | Tên “Muse” đang bị nhiều dự án khác dùng (ví dụ một server đa phương thức tên Muse trên PyPI) | Low | Kiểm tra trùng tên/thương hiệu trước khi công khai |

### 0.4 Nâng cấp nổi bật của v2.0

**Director Assistant** (chat sửa shot/graph bằng diff) · **3 lớp UX** Guided → Board → Canvas · **Cost Guard** · **Two-phase submit** · **Look-dev gate + last-frame chaining** · **Review mode** thao tác bằng bàn phím · **Pipeline Apps** (đóng gói workflow thành form đơn giản) · **Demo project chạy bằng mock** · **Fountain import**, **OTIO/EDL export** · **Design system** đầy đủ · **Roadmap** có ước lượng và cổng quyết định.

## 1. Sản phẩm

### 1.1 Tầm nhìn

**Từ kịch bản đến phim: có kiểm soát, lặp lại được, không đốt tiền.** MuseStudio là xưởng sản xuất video AI tự host. Mọi thứ (script, Bible, shot, prompt, run, take) được lưu thành dữ liệu có phiên bản, để người dùng làm đạo diễn thay vì xin từng prompt may rủi.

### 1.2 Nguyên tắc sản phẩm

1. **AI đề xuất, người duyệt.** Mọi thay đổi do AI tạo ra là diff có provenance; không ghi đè nội dung gốc.
2. **Cost-aware mặc định.** Thao tác tốn tiền đều có ước tính trước, ngân sách và xác nhận.
3. **Trung thực về năng lực.** Chỉ hiển thị tính năng đã kiểm chứng với provider/phiên bản hiện tại; còn lại là `unverified`.
4. **Tái lập được.** Mỗi take truy ngược được: spec revision, prompt version, model, connection, run.
5. **Continuity là mục tiêu + công cụ review, không phải lời hứa.**
6. **Provider-agnostic.** Workflow không biết provider cụ thể; capability quyết định cái gì khả dụng.
7. **Tiết lộ dần.** Người mới dùng Guided, người quen dùng Board, chuyên gia mở Canvas.
8. **Self-host và an toàn bí mật** là điều kiện nền, không phải tính năng.

### 1.3 Người dùng mục tiêu — giả thuyết cần kiểm chứng

Chưa có dữ liệu nghiên cứu người dùng, nên bảng dưới là **giả thuyết**, không phải phát hiện.

| Giả thuyết | Việc cần làm (JTBD) | Nỗi đau nghi ngờ | Cách kiểm chứng |
| --- | --- | --- | --- |
| P1 Solo creator (phim ngắn, MV, content) | Biến ý tưởng/kịch bản thành phim ngắn nhất quán | Nhân vật đổi mặt giữa các shot; copy prompt thủ công; hết credit bất ngờ | Phỏng vấn 5–8 người; quan sát họ làm 1 cảnh bằng công cụ hiện tại |
| P2 Studio/agency nhỏ (quảng cáo, trailer) | Lặp lại quy trình cho nhiều khách; duyệt với khách | Không có lịch sử/phiên bản; khó chứng minh chi phí | Phỏng vấn 3–5 studio; thử pipeline mẫu “Trailer 30s” |
| P3 Operator/dev tự host | Tích hợp provider, vận hành ổn định | Gateway gãy mà không biết lỗi ở đâu | Dogfooding + log lỗi thực tế ở M0–M3 |

Quy tắc đọc kết quả nghiên cứu: tách **quan sát** (“5/8 người dựng lại nhân vật 3 lần”) khỏi **diễn giải** (“cần Bible”); luôn ghi số lượng cụ thể.

### 1.4 Non-goals

- Không phải NLE đầy đủ; không cạnh tranh với Premiere/DaVinci (bàn giao qua OTIO/EDL).
- Không fork giao diện muse.ai; không huấn luyện/fine-tune model.
- **Không có tính năng xoay tài khoản/proxy để né quota, cooldown hay giới hạn của provider.** Cooldown/quota là tín hiệu back-pressure, không phải chướng ngại để vượt.
- Không marketplace trước M8; không hứa continuity tuyệt đối.

### 1.5 Chỉ số thành công (mục tiêu giả thuyết, cần đo baseline)

| Chỉ số | Mục tiêu khởi điểm | Ghi chú |
| --- | --- | --- |
| Time-to-first-rough-cut (60 giây, \~12 shot) | ≤ 2 giờ thao tác | Không tính thời gian chờ provider |
| First-take acceptance rate | ≥ 35% | Đo từ M3; cải thiện theo từng bản |
| Retake trung bình / shot được duyệt | ≤ 2,5 |  |
| Chi phí / giây video được duyệt | Xu hướng giảm | Hiển thị theo đơn vị của provider |
| Continuity flag / 10 shot | Theo dõi | Giảm dần khi Bible tốt hơn |
| Sự cố gửi trùng / mất task | **0** | Chỉ số độ tin cậy cứng |
| Thời gian “aha” (demo project → MP4 trên mock) | < 5 phút | Không cần tài khoản provider |

## 2. Thị trường (cập nhật 10/2026)

Ưu tiên tài liệu chính thức; phần còn lại là bài tổng hợp bên thứ ba và được đánh dấu.

| Sản phẩm | Hiện có (theo nguồn) | Học điều gì | Áp dụng vào MuseStudio |
| --- | --- | --- | --- |
| **Runway** | Workflows dạng node: chạy cả graph hoặc từng node, **khoá output node** để không sinh lại, sửa hàng loạt setting ([help](https://help.runwayml.com/hc/en-us/articles/45763528999699)). Workflows → *Apps*; Agent dựng/sửa workflow bằng ngôn ngữ tự nhiên; Dev API/MCP; timeline editing trong Agent (hồ sơ tổng hợp 09/2026, [yespress](https://yespress.io/products/runway.md)) | Lock node; Apps; agent đặt trên graph | Lock node, Pipeline Apps, Director Assistant |
| **Google Flow + Veo** | Flow là workspace trên Veo/Imagen/Gemini: Ingredients to Video, Frames to Video, Extend; Veo 3.1 có audio gốc, truy cập qua Gemini API (paid preview) và Vertex AI ([tin](https://yourstory.com/ai-story/google-veo-31-flow-ai-editing-tool)). Một bài hướng dẫn nhận định Flow chủ yếu là trải nghiệm sản phẩm, không phải API chung ([bài](https://aividpipeline.com/blog/google-flow-veo-3-1-guide-2026)) | Tham chiếu tái dùng; nối cảnh bằng khung cuối | Story Bible = “ingredients”; last-frame chaining; Veo là ứng viên provider chính thức |
| **Adobe Firefly video editor** | Editor đa track trên browser, sửa theo transcript, Prompt to Edit ([Adobe 12/2025](https://blog.adobe.com/en/publish/2025/12/16/adobe-firefly-improves-ai-video-creation-tools-new-models-unlimited-generations)); Quick Cut tự dựng bản cắt đầu tiên ([Adobe 02/2026](https://blog.adobe.com/en/publish/2026/02/25/putting-ideas-in-motion-redefining-ai-video-with-adobe-firefly)) | Editor gọn; tự dựng bản đầu | “Quick Assemble” từ take đã chọn; sửa subtitle theo văn bản |
| **ComfyUI** | Node graph có kiểu, workflow lưu thành dữ liệu; partner nodes gọi API cloud ([Comfy](https://comfy.org/p/supported-models/runway.md)) | Typed ports, graph JSON | Giữ typed graph; import/export JSON có version |
| **muse2api** | Gateway không chính thức bọc muse.ai bằng Chromium. v0.2.0 (4/10) thêm dashboard, API key theo client, ảnh tham chiếu, async image task, endpoint quota ([release](https://github.com/www222fff/muse2api/releases)). Tồn tại nhiều fork mô tả khác nhau (có fork tự nâng cấp, xoay tài khoản) | — | Connector **experimental**; ghim version; capability probe bắt buộc; không bật tự cập nhật |

**Quan sát thị trường (cần kiểm chứng):**

1. Các bài tổng hợp đưa tin Sora (app/API) bị ngừng trong 2026 và Veo 3 API được thay bằng Veo 3.1 ([provimedia](https://provimedia.de/en/blog/ai-video-tools-2026), [rangy](https://rangy.ai/blog/ai-video-generators-compared-2026)) **\[Cần xác minh\]** → vòng đời model/API ngắn; provider-agnostic và xử lý *deprecation* là bắt buộc.
2. Audio gốc, multi-shot, first/last frame đang thành tiêu chuẩn → capability model phải biểu diễn được `native_audio`, `multi_shot`, `first_last_frame`.
3. Có các API tổng hợp nhiều model qua một key; coi là một loại adapter, không phụ thuộc vào chúng.
4. Một nguồn nêu nghĩa vụ minh bạch nội dung AI (Điều 50 EU AI Act) áp dụng từ 2/8/2026 **\[Cần xác minh với tư vấn pháp lý\]** → export có AI disclosure/provenance (§11.4).

**Khác biệt của MuseStudio:** tự host · provider-agnostic · Story Bible và continuity là trung tâm · run tái lập được + kiểm soát chi phí · pipeline là dữ liệu mở (JSON/OTIO).

## 3. Quyết định kiến trúc (ADR)

| ADR | Quyết định | Trạng thái |
| --- | --- | --- |
| 001 | Lõi provider-agnostic; ≥1 adapter chính thức; muse2api là connector experimental | Proposed — chốt ở cổng M0 |
| 002 | Hàng đợi Postgres-first; Redis tuỳ chọn | Proposed |
| 003 | “DB là workflow”: reconciler + durable timer + two-phase submit | Proposed |
| 004 | Graph JSON có version là hợp đồng duy nhất; Guided/Board/Canvas là 3 view | Proposed |
| 005 | Realtime bằng SSE trên event log | Proposed |
| 006 | Media: object storage + renditions + worker FFmpeg cô lập + bàn giao OTIO/EDL | Proposed |
| 007 | `workspace_id` ở mọi bảng từ đầu; single-admin MVP; OIDC ở M8 | Proposed |
| 008 | Cost governance: estimate → ngân sách → xác nhận; draft→final | Proposed |
| 009 | Frontend: React + TS + Vite, Tailwind + Radix, xyflow, TanStack | Proposed |
| 010 | Provenance & an toàn: C2PA/AI disclosure, consent, `policy_rejected` | Proposed |

### ADR-001 — Chiến lược provider

- **Bối cảnh:** F1. Gateway bọc web app không chính thức; API đổi giữa các release; có nguy cơ khoá tài khoản.
- **Quyết định:** Lõi không biết provider. Capability Registry + adapter tách theo modality. Mock (CI) + muse2api (experimental) + **≥1 adapter chính thức** trước khi M3 kết thúc (ứng viên: Veo qua Gemini API/Vertex AI, Runway API, Kling API, hoặc API tổng hợp). Chính sách cứng: không xoay tài khoản/proxy để né giới hạn; không lưu cookie muse.ai; không tự cập nhật gateway.
- **Phương án:** A) chỉ muse2api (v1.0): nhanh nhất, mong manh nhất. B) chỉ official: ổn định nhưng phụ thuộc giá/khả dụng. **C) Lai (chọn).**
- **Đánh đổi:** thêm công adapter thứ hai; đổi lại có đường lui khi gateway gãy.
- **Xem lại khi:** gateway có quan hệ chính thức với provider, hoặc chi phí API chính thức vượt ngân sách.

### ADR-002 — Queue Postgres-first

- **Quyết định:** Hàng đợi dùng Postgres (`FOR UPDATE SKIP LOCKED`, lease + heartbeat) qua thư viện sẵn có (ví dụ Procrastinate) hoặc module mỏng tự viết. Enqueue cùng transaction với thay đổi trạng thái (outbox). Redis không bắt buộc ở MVP.
- **Phương án:** Celery+Redis (v1.0): trưởng thành nhưng trạng thái ở hai nơi, cần reconciler; Dramatiq/ARQ: nhẹ, cùng vấn đề; Temporal: durable execution tốt nhưng nặng cho self-host nhỏ; Prefect/Dagster: thiên về data pipeline.
- **Đánh đổi:** ít “battle-tested” ở thông lượng rất lớn; đủ cho hàng chục job đồng thời (mỗi job kéo dài vài phút).
- **Xem lại khi:** bền vững >100 job đồng thời, hoặc cần workflow người-trong-vòng phức tạp → cân nhắc Temporal.

### ADR-003 — “DB là workflow”

`node_executions` là nguồn sự thật; queue chỉ đánh thức worker. Reconciler quét định kỳ: lease hết hạn, `next_poll_at` đến hạn, run mồ côi, intent gửi dở → đưa lại hàng đợi hoặc gắn cờ cần người xử lý. Event log append-only làm nền cho UI, audit và debug.

### ADR-004 — Một graph, ba view

Graph JSON có version là hợp đồng duy nhất. **Guided** (stepper, ẩn graph), **Board** (storyboard theo scene, mặc định) và **Canvas** (node editor) cùng đọc/ghi một dữ liệu. Template “Script to Film” được compile thành graph; người dùng mở Canvas khi cần tuỳ biến. Lý do: phần lớn người dùng muốn đạo diễn, không muốn đi dây node (Runway cũng thêm Apps và Agent lên trên Workflows).

### ADR-005 → ADR-010 (tóm tắt)

- **005 Realtime:** SSE với `Last-Event-ID`, đọc từ `execution_events`; polling dự phòng; WebSocket chỉ khi có cộng tác thời gian thực.
- **006 Media:** object storage (MinIO/S3 hoặc FS cục bộ) + renditions (original, proxy 720p, thumbnail, poster, waveform). Worker `media` tách biệt, không network, giới hạn CPU/RAM/thời gian. Bàn giao NLE qua OTIO/EDL/FCPXML.
- **007 Tenancy/auth:** `workspace_id` mọi bảng; MVP single-admin (mật khẩu + TOTP/passkey); optimistic concurrency (`version`/ETag) để sẵn sàng cộng tác.
- **008 Chi phí:** đơn vị nội bộ (`credit`, `video_second`, `currency`) tuỳ provider; estimator + ngân sách + xác nhận; draft→final promotion.
- **009 Frontend:** React + TypeScript + Vite; Tailwind + Radix primitives (hoặc shadcn/ui); `@xyflow/react` cho canvas; TanStack Query + TanStack Virtual; Zustand cho state cục bộ; Storybook + Playwright + axe; i18n vi/en.
- **010 Provenance & an toàn:** C2PA/AI disclosure khi export; ghi nhận consent cho tham chiếu người thật; hook moderation; `policy_rejected` là lỗi riêng, không tự động diễn đạt lại prompt để lách.

## 4. Kiến trúc hệ thống

### 4.1 Tổng quan

```
┌──────────────── Browser (React SPA) ────────────────┐
│ Guided · Board · Review · Canvas · Timeline · Queue │
└──────────┬───────────────────────────▲──────────────┘
   REST/JSON + Idempotency-Key         │ SSE (event log)
┌──────────▼───────────────────────────┴──────────────┐
│ API (FastAPI): authn/authz, validate, tạo command   │
└──────┬───────────────────────────────┬──────────────┘
       │ SQL (transaction + outbox)    │ presigned URL
┌──────▼─────────────┐        ┌────────▼───────────────┐
│ PostgreSQL         │        │ Object storage         │
│ truth + queue +    │        │ MinIO / S3 / local FS  │
│ event log          │        └────────▲───────────────┘
└──────▲─────────────┘                 │ read/write
       │ lease / poll                  │
┌──────┴───────────────────────────────┴──────────────┐
│ Workers (một codebase, nhiều queue)                 │
│ orchestrator · provider · llm · media (sandbox)     │
└─────┬───────────────┬────────────────┬──────────────┘
      │ HTTP          │ HTTP           │ subprocess, không network
  muse2api       Official APIs     FFmpeg / ffprobe
 (experimental)  (Veo, Runway…)
```

### 4.2 Container (Docker Compose)

| Service | Vai trò | Ghi chú |
| --- | --- | --- |
| `caddy` | TLS, serve SPA, reverse proxy | HTTPS tự động khi có domain |
| `api` | FastAPI; authz, validate, tạo command/run | Không gọi provider/FFmpeg trực tiếp |
| `worker-orchestrator` | Expand graph, tạo `node_executions`, xử lý approval/invalidation, reconciler | Nhẹ, 1–2 bản |
| `worker-provider` | Submit/poll/download theo connection | Scale theo concurrency của connection |
| `worker-llm` | Phân tích script, refine prompt, QA có schema | Retry riêng; giới hạn token/chi phí |
| `worker-media` | Proxy, thumbnail, last-frame, export | Không network; cgroup CPU/RAM/thời gian |
| `postgres` | Dữ liệu + queue + event log | `pg_dump` định kỳ; WAL archiving tuỳ chọn |
| `minio` (tuỳ chọn) | Object storage | Dev dùng filesystem |
| `redis` (tuỳ chọn) | Pub/sub khi scale-out, rate-limit chia sẻ | Không bắt buộc ở MVP |
| `muse2api` | Gateway riêng/bên ngoài | Chỉ mạng nội bộ; ghim version; không public |
| `otel-collector` (tuỳ chọn) | Trace/metrics | Xem §12 |

### 4.3 Luồng “Generate shot” và phục hồi

1. UI gọi `POST /shots/{id}/generate` kèm `Idempotency-Key`. API kiểm capability, reference, ngân sách rồi trong **một transaction** tạo `run`, `node_executions`, `cost_reservation` và enqueue (outbox). Trả `202` + `run_id`.
2. Worker provider ghi `submission_intent(pending)` **trước** khi gọi provider; gọi xong ghi `provider_task_id` và đặt `submitted`.
3. Đặt `next_poll_at` theo backoff; job poll chạy đúng hẹn, không giữ worker. Khi provider báo xong: tải file vào storage → checksum → `ffprobe` → tạo `take` + `asset` → `succeeded` → chốt chi phí.
4. Mọi bước ghi `execution_events`; SSE đẩy tới UI.
5. **Crash giữa bước 2 và 3:** intent còn `pending` quá hạn lease → reconciler đánh dấu `unknown_submission`, thử tra cứu (nếu provider hỗ trợ); nếu mơ hồ thì hỏi người dùng *Gắn task id / Gửi lại / Bỏ*. **Không bao giờ tự gửi lại.**

### 4.4 Yêu cầu phi chức năng

| Nhóm | Mục tiêu |
| --- | --- |
| Độ bền | `succeeded` chỉ sau khi tải xong, checksum và `ffprobe` đạt; không mất take khi restart |
| Phục hồi | Restart api/worker: run tiếp tục, không gửi trùng; reconciler nhận lại job ≤ 60 giây |
| Độ trễ lệnh | `POST generate` → `202`: p95 < 500 ms |
| Độ tươi trạng thái | UI cập nhật ≤ 3 giây qua SSE |
| Hiệu năng UI | Board 500 shot cuộn mượt (virtualized); Canvas 300 node ≥ 30 fps; thao tác cục bộ < 100 ms **\[mục tiêu, cần đo\]** |
| Quy mô | 20–50 task provider đồng thời / instance; ≥ 10.000 shot / workspace |
| A11y / i18n | WCAG 2.2 AA cho luồng cốt lõi; UI vi/en từ ngày đầu |
| Vận hành | `docker compose up` ≤ 10 phút từ máy sạch; backup/restore có script và được kiểm thử |
| Bảo mật | Xem §11 |

### 4.5 Ước lượng minh hoạ (giả định, cần đo lại)

Phim 3 phút ≈ 36 shot × 5 giây; trung bình 3 take/shot ≈ 108 lần sinh. Mỗi lần 2–6 phút → 3,6–10,8 giờ nếu tuần tự, 0,9–2,7 giờ với 4 luồng song song. Dung lượng ≈ 1,5–3 GB (bản gốc + proxy + thumbnail). Điều này cho thấy **thời gian chờ provider là nút cổ chai chính**, nên UX phải làm việc song song trong lúc chờ (duyệt shot khác, sửa Bible, review take) thay vì khoá màn hình.

## 5. Domain & data model

### 5.1 Nguyên tắc dữ liệu

1. ID ổn định, sắp xếp được theo thời gian (UUIDv7/ULID) kèm tiền tố loại (`shot_`, `take_`, `run_`).
2. `workspace_id` và kiểm tra quyền ở mọi truy vấn (ADR-007).
3. **Spec bất biến theo revision:** sửa shot = tạo `shot_revision` mới; take gắn với revision đã dùng nên phát hiện được take `outdated`.
4. JSONB cho trường linh hoạt, luôn có `schema_version` và validate bằng Pydantic.
5. Thứ tự bằng fractional index (`order_key`), không đánh lại `sequence_index` hàng loạt.
6. Soft delete + ref-count cho asset; xoá cứng chỉ qua job dọn dẹp có xác nhận.
7. Migration kiểu expand/contract (Alembic) để run đang chạy không gãy khi đổi schema.
8. Graph và export không chứa secrets; secrets chỉ qua `secret_ref`.

### 5.2 Thực thể cốt lõi

| Nhóm | Bảng chính | Ghi chú quan trọng |
| --- | --- | --- |
| Dự án | `projects`, `project_settings` | aspect ratio, fps, ngôn ngữ UI / prompt / thoại, export profile |
| Script | `scripts`, `script_revisions` | `content_hash`; nguồn (paste/Fountain/Markdown/AI proposal); AI không ghi đè revision gốc |
| Bible | `bible_entities`, `bible_entity_revisions`, `bible_snapshots` | `kind` ∈ character/location/prop/style; `reference_asset_ids`; `locked_fields[]`; snapshot để pin vào run |
| Cấu trúc | `scenes`, `shots`, `shot_revisions` | revision chứa spec (xem Shot schema trong v1.0, giữ nguyên) + `spec_hash` + `source` (human / ai\_proposal) |
| Prompt | `prompt_templates`, `prompt_versions` | IR + text đã compile + dialect + `prompt_hash` + `lint_report` |
| Take | `takes` | `shot_id`, `spec_revision_id`, `prompt_version_id`, `node_execution_id`, `asset_id`, `state`, cờ selected/starred/rejected |
| Workflow | `workflows`, `workflow_versions`, `workflow_layouts` | graph JSON bất biến; layout lưu riêng |
| Run | `workflow_runs`, `node_executions`, `execution_events` | `snapshot` pin ID phiên bản; execution có `item_key`, `state`, `freshness`, `block_reason`, `input_hash`, `cache_key`, `attempt`, `next_poll_at`, `lease_until`, `error`; event có `(run_id, seq)` tăng đơn điệu |
| Gửi provider | `submission_intents` | `request_hash`, `status`, `provider_task_id` (xem §6.4) |
| Provider | `provider_connections`, `provider_models`, `capability_probes` | health, circuit, giới hạn, `pinned_version`; capability có `status`, `verified_at`, `evidence_asset_ids` |
| Chi phí | `price_book`, `cost_ledger` | bút toán estimate / reserve / settle theo run; đơn vị tuỳ provider |
| Asset | `assets`, `asset_renditions`, `asset_relations`, `asset_tags` | checksum, mime, kích thước, duration, fps, `storage_key`, `origin`, `provenance` |
| Timeline | `timeline_sequences`, `timeline_tracks`, `timeline_clips`, `export_jobs` | clip tham chiếu `take_id`/`asset_id` + trim in/out; manifest xuất được sang OTIO |
| Hệ thống | `users`, `workspaces`, `workspace_members`, `audit_events`, `usage_records`, `webhooks`, `system_settings` |  |

### 5.3 Các máy trạng thái (tách biệt, không gom một cột `status`)

| Máy trạng thái | Giá trị | Ghi chú |
| --- | --- | --- |
| Shot approval | `draft` → `needs_review` → `approved` / `rejected` (→ `draft`) | Sửa spec của shot `approved` đưa về `needs_review` |
| Generation (dẫn xuất từ run/take) | `not_started`, `blocked`, `queued`, `running`, `succeeded`, `failed`, `canceled` | `blocked` kèm `block_reason` ∈ reference\_missing, budget, approval, connection\_unhealthy, capability\_unsupported, upstream\_stale, needs\_operator (tổng quát hoá `BLOCKED_REFERENCE` của v1.0) |
| Node execution | `idle`, `ready`, `blocked`, `queued`, `running`, `waiting_approval`, `succeeded`, `failed`, `canceled`, `skipped` | Giữ nguyên v1.0 |
| Output freshness (mới) | `never_run`, `fresh`, `stale`, `locked` | Độc lập với execution state |
| Take | `downloading` → `probing` → `candidate` → `selected` / `rejected`; lỗi: `failed_download`, `failed_probe` | `outdated` là cờ dẫn xuất |
| Submission intent | `pending` → `submitted` → `settled`; nhánh `unknown`, `abandoned` | §6.4 |
| Connection | `health` ∈ unknown/healthy/degraded/auth\_error/unavailable; `circuit` ∈ closed/open/half\_open; `quota` ∈ unknown/ok/exhausted | Ba trường riêng |

### 5.4 Quy tắc dữ liệu

- Take `outdated` ⇔ `take.spec_revision_id ≠ shot.current_revision_id` hoặc revision Bible liên quan đã đổi. Không xoá take; hiển thị huy hiệu.
- Đổi entity Bible → các shot dùng entity đó chuyển `needs_review`, chỉ node downstream liên quan bị `stale`.
- Xoá asset: kiểm tra tham chiếu (timeline, reference, Bible), liệt kê và cảnh báo.
- Export project ZIP: JSON + manifest + checksum; không có secrets; media tuỳ chọn.
- Dữ liệu người thật (ảnh/giọng): gắn `contains_real_person` và `consent_ref` (§11.4).

## 6. Workflow engine

### 6.1 Graph schema

```yaml
schema_version: 2
nodes:
  - id: n_script
    type: ScriptInput
    version: 1
  - id: n_shots
    type: ShotBreakdown
    version: 1
    config: { detail: normal, max_shots: 40 }
  - id: n_video
    type: VideoGenerate
    version: 1
    config: { model_policy: pinned, model_id: pm_01J, draft: true }
    run_policy: manual          # node tốn tiền: không tự chạy
edges:
  - { from: n_script.script, to: n_shots.script }
  - { from: n_shots.shots,   to: n_video.shots }
policies:
  autorun: free_only            # chỉ tự chạy node không tốn tiền
  budget: { unit: video_second, max: 120 }
```

Layout (`x`, `y`, group, collapsed) lưu ở `workflow_layouts`, không ảnh hưởng hash logic nên kéo node không tạo version mới.

### 6.2 Danh mục node

| Nhóm | Node | Ưu tiên |
| --- | --- | --- |
| Nhập | `ScriptInput`, `ReferenceInput`, `Note/Group` | P0 |
| Phân tích (LLM) | `ScriptAnalyze`, `StoryBibleBuild`, `SceneBreakdown`, `ShotBreakdown`, `PromptTemplate`, `ContinuityLint`; `PromptRefine` | P0; `PromptRefine` P1 |
| Cổng người | `ManualApproval`, **`LookDevGate`**, `QualityGate`, **`CostGate`** | P0 |
| Sinh nội dung | `ImageGenerate`, `VideoGenerate`; **`ExtractLastFrame`**, **`ContinuityQA`** (advisory); **`TTS`**, **`MusicGenerate`**, **`SubtitleBuild`** | P0 cho Image/Video (chạy bằng mock); còn lại P1 |
| Điều khiển | `Map` (fan-out), `Collect` (fan-in), `Condition/Filter` | P0 / P1 |
| Đầu ra | `AssetSave`, `TimelineAssemble`, **`LoudnessNormalize`**, `VideoExport`, `Webhook/Notify` | P1 |

Gộp so với v1.0: `WaitForTask` thành hành vi nội bộ của node Generate (durable timer); `RetryPolicy` thành thuộc tính node; `Batch/FanOut` + `Merge/Sequence` thành `Map`/`Collect`. Kiểu cổng: `Text`, `Script`, `StoryBible`, `Scene[]`, `Shot[]`, `Prompt`, `ImageAsset`, `VideoAsset`, `AudioAsset`, `SubtitleTrack`, `TaskRef`, `TimelineManifest`, `CostEstimate`, `Boolean`, `Error`.

### 6.3 Ngữ nghĩa chạy

Ba chiều trạng thái độc lập: **execution** (node đang làm gì), **freshness** (output còn khớp input không), **approval** (người đã duyệt chưa).

- `fresh`: output khớp input hash hiện tại. `stale`: input/config/prompt đổi sau lần chạy gần nhất. `locked`: người dùng khoá (tương tự Lock node của Runway); không bao giờ tự chạy lại, chỉ hiện cảnh báo “upstream đã đổi”.
- **Node tất định và miễn phí** (compile prompt, lint, validate): `run_policy: auto` khi `stale`.
- **Node sinh nội dung/tốn tiền** (Image, Video, TTS, Music, QA dùng LLM trả phí): luôn `manual`. `stale` chỉ hiện huy hiệu *Outdated*; tạo take mới là hành động rõ ràng (*New take*). Cache theo `cache_key` chỉ dùng để tái dùng kết quả khi chạy lại cùng đầu vào trong một run và để khôi phục sau lỗi, **không** cam kết kết quả giống nhau (sinh nội dung là không tất định).
- `cache_key = sha256(node_type, node_version, canonical(config), input_hashes, provider+model+version/probe_id, prompt_hash, reference_checksums, seed_policy)`.
- **Run snapshot** pin: `workflow_version`, `bible_snapshot`, `shot_revision` từng shot, `prompt_version`, connection/model.
- **Fan-out/fan-in:** `Map` tạo execution con theo `item_key = shot_id@revision`; `Collect` gom theo `order_key`; `on_partial` ∈ `continue` (mặc định) / `block`. Lỗi một shot không làm mất kết quả shot khác.
- **Approval** là node `waiting_approval`; từ chối đưa nhánh liên quan về `stale` kèm lý do.
- Chỉ cho phép DAG. Vòng “review → sửa” là cấu trúc tường minh: cổng *Needs edits* của `ManualApproval` dùng edge loại `feedback`, engine xử lý như một lượt chạy mới chứ không phải cycle.

### 6.4 Two-phase submit và reconciliation

Vấn đề: provider (nhất là gateway) không đảm bảo idempotency, nên crash đúng lúc đang gửi có thể sinh trùng hoặc mất task id.

1. Trong một transaction: tạo `submission_intent(pending, request_hash)` và đặt `node_execution = running`.
2. Gọi provider (truyền idempotency key/metadata nếu provider hỗ trợ).
3. Nhận task id → cập nhật intent `submitted` và lưu `provider_task_id` (một transaction).
4. Reconciler: intent `pending` quá lease mà chưa có task id → `unknown`. Thử tra cứu theo cửa sổ thời gian/metadata nếu provider cho phép; nếu mơ hồ → `blocked(needs_operator)` và hiện ba lựa chọn: *Gắn task id thủ công · Gửi lại (xác nhận có thể tốn thêm) · Bỏ*.
5. **Không có đường nào tự động gửi lại một intent `unknown`.**

Kiểm thử bắt buộc: chaos test kill worker ở từng bước 1–3; kỳ vọng 0 lần gửi trùng.

### 6.5 Polling và durable timer

- Node Generate không “ngủ”: sau submit chỉ đặt `next_poll_at`; job poll ngắn chạy khi đến hạn rồi nhả slot.
- Backoff 3s → 5s → 10s → 20s → 30s (trần) cộng jitter ±20%. Có trần chờ `max_wait` theo model; vượt trần → `timed_out_waiting` (khác `generation_failed`), giữ `provider_task_id` để đối soát, người dùng chọn *Tiếp tục chờ*.
- Gateway kiểu browser có thể chờ rất lâu (release notes v0.2.0 ghi nhận gateway chờ tới 2,5 lần timeout khi upstream còn đang chạy) → đặt `max_wait` theo kết quả probe, không hardcode.
- Giới hạn tần suất poll theo connection (token bucket) để không dồn GET.

### 6.6 Phân loại lỗi và retry

| Lớp lỗi | Ví dụ | Retry | Hành động |
| --- | --- | --- | --- |
| `transient_network` | timeout, connection reset | Có: backoff + jitter, tối đa 5 | — |
| `upstream_5xx` / `upstream_busy` | 502, 503 | Có, tối đa 4; tôn trọng `Retry-After` | Mở circuit nếu liên tiếp |
| `rate_limited` | 429 | Có, theo `Retry-After`; hạ concurrency tạm | — |
| `timed_out_waiting` | quá `max_wait` | Không tự gửi lại | Giữ task id; hỏi người dùng |
| `generation_failed` | provider báo lỗi sinh | Tối đa 1 lần cùng tham số | Tính vào chi phí |
| `auth_error` | 401/403, session hết hạn | **Không** | Connection → `auth_error`, mở circuit, thông báo |
| `quota_exhausted` | hết quota/credit | **Không** | Tạm dừng hàng đợi của connection; không rotate để né |
| `invalid_request` | 400, option không hỗ trợ | **Không** | Sửa spec/capability |
| `policy_rejected` | bị chính sách nội dung từ chối | **Không** | Hiện lý do; không tự diễn đạt lại để lách |
| `download_failed` | URL hết hạn, đứt mạng | Có, **chỉ tải lại, không sinh lại** | Nếu URL hết hạn: tra lại task |
| `probe_failed` | file hỏng/không phải video | 1 lần tải lại | Take → `failed_probe` |

### 6.7 Scheduler

- Giới hạn phân cấp: global → connection → model → (account, nếu backend công bố) → project. Token bucket + lease có heartbeat.
- Công bằng giữa project (weighted round-robin) và làn ưu tiên (keyframe/shot quan trọng chạy trước).
- **Circuit breaker** theo connection: `closed → open` sau N lỗi hệ thống liên tiếp; `half_open` thăm dò 1 request. Cooldown/quota là back-pressure, không phải thứ cần né.
- Pause/resume/cancel theo run, project, connection. Cancel là best-effort: nếu provider không hỗ trợ huỷ, task vẫn có thể chạy và tính phí, UI phải ghi rõ.

### 6.8 Thao tác run

`Run all` · `Run from here` · `Run selected` · `Run downstream (chỉ stale)` · `New take` · `Retry failed` · `Cancel` · **`Dry run`** (không gọi provider; trả về kế hoạch, node bị chặn + lý do, **ước tính chi phí/thời gian**, cảnh báo capability) · `Promote draft → final` (chạy lại shot đã duyệt ở chất lượng cao).

## 7. Provider layer

### 7.1 Interface (tách theo modality)

```python
class TaskProvider(Protocol):            # dùng chung cho tác vụ bất đồng bộ
    async def submit(self, req: GenRequest, *, client_ref: str) -> SubmitResult: ...
    async def poll(self, task_id: str) -> TaskStatus: ...
    async def fetch(self, result_ref: str) -> MediaStream: ...
    async def cancel(self, task_id: str) -> CancelResult: ...   # có thể trả Unsupported

class VideoProvider(TaskProvider, Protocol):
    async def capabilities(self) -> list[ModelCapability]: ...
class ImageProvider(TaskProvider, Protocol): ...
class AudioProvider(TaskProvider, Protocol): ...   # TTS / music / sfx
class LLMProvider(Protocol):
    async def generate_structured(self, schema: JsonSchema, messages: list[Msg],
                                  *, budget: TokenBudget) -> StructuredResult: ...
class Transcriber(Protocol): ...
```

Mọi adapter trả lỗi đã phân loại (§6.6), che bí mật trong lỗi/log và không ném exception thô. Phân tích script dùng `LLMProvider` riêng (có thể là một LLM bất kỳ), **không** gắn với chat endpoint của gateway video.

### 7.2 Capability model và probe

- `ModelCapability`: modalities; `durations` (min/max/step); `aspect_ratios`; `resolutions`; `fps`; đầu vào (`text`, `first_frame`, `last_frame`, `reference_images(max)`, `video_extend`, `audio_input`); đầu ra (`native_audio`, `multi_shot`); `negative_prompt`; `seed`; `prompt_max_chars`; `async`; `cancel`; `limits` (concurrency/rate); `pricing_model`; `status` ∈ verified / mock\_only / unverified / unsupported; `verified_at`; `verified_version`; `evidence_asset_ids`.
- Probe chạy khi thêm connection và khi version đổi: health, models, và một yêu cầu nhỏ **do operator xác nhận** (có thể tốn tiền). Lưu bằng chứng (asset thật + probe report). **Không bao giờ `verified` nếu chỉ chạy qua mock.**
- Deprecation: model biến mất khỏi `/models` hoặc probe thất bại → capability về `unverified`; shot dùng model đó bị `blocked(capability_unsupported)` kèm gợi ý model thay thế; không tự đổi model.

### 7.3 Routing và model pinning

- Chính sách: `pinned` (mặc định khi đang dựng một cảnh, để giữ “look”), `cheapest_capable`, `fastest_capable`, `quality_first`.
- Fallback chain chỉ áp dụng cho draft hoặc khi người dùng cho phép; đổi model giữa các shot cùng cảnh cần xác nhận vì làm đổi hình ảnh.
- **Draft → Final:** dùng model rẻ/nhanh cho keyframe và draft; chỉ shot đã duyệt mới lên model/chất lượng cao.

### 7.4 Adapter muse2api (experimental)

- Ghim release/commit (`pinned_version`); probe phát hiện endpoint thiếu thì từ chối kết nối. Gateway mặc định lắng nghe `127.0.0.1:18610` (theo README): giữ bind nội bộ.
- Endpoint theo tài liệu: `/healthz`, `/readyz`, `/v1/models`, `/v1/chat/completions`, `/v1/images/generations` (ảnh tham chiếu và `async: true` kèm GET theo task id từ v0.2.0), `/v1/videos` + `GET /v1/videos/{task_id}` + `/v1/media/{name}`. Release notes v0.1.0 ghi `/v1/responses`, `/v1/images/edits` và driver HTTP trực tiếp còn “reserved”: **không dùng**. **\[Cần xác minh toàn bộ với phiên bản triển khai\]**
- Endpoint admin (từ v0.2.0): `/admin/accounts/{id}/quota`, `/admin/stats`, `/admin/requests`, `/admin/keys` chỉ dùng để **đọc**. Thao tác quản trị (import/disable tài khoản, quản lý key) → deep-link tới `/dashboard` của gateway, không tái hiện trong MuseStudio.
- Hành vi cần thiết kế cho: yêu cầu có thể chờ rất lâu; upload có thể bị gateway tự từ chối ở thẻ phê duyệt nên upload ảnh tham chiếu cần đường fallback; quota chỉ là số đo gần đúng.
- Tách key: API key sinh nội dung ≠ Admin API key (tuỳ chọn, quyền đọc).
- Không bật tính năng tự nâng cấp của bất kỳ fork nào trên môi trường có dữ liệu thật.

### 7.5 Mock provider (bắt buộc, tất định)

Sinh ảnh/video giả bằng FFmpeg (`testsrc` + overlay shot id) với thời lượng và độ trễ cấu hình. **Fault injection:** latency, 5xx, 429, `auth_error`, `quota_exhausted`, `policy_rejected`, task mất tích, URL hết hạn, file hỏng, trả trùng. Có chế độ “không idempotent” để test two-phase submit và chế độ “chậm” để test timer. Capability luôn là `mock_only`.

### 7.6 Cost Guard

- Đơn vị: `video_second`, `image`, `credit`, `token`, tiền tệ (chỉ khi operator nhập bảng giá). Giá không rõ → hiển thị “không xác định” và dùng đơn vị đếm (giây, số lần), không bịa số.
- Luồng: `estimate` (Dry run) → `reserve` (khi bấm chạy) → `settle` (khi xong, tính cả retry/timeout) → ghi `cost_ledger`.
- Ngân sách theo run / project / ngày / connection; vượt → `blocked(budget)`. Batch lớn cần xác nhận kèm tổng ước tính.
- **Cost HUD** ở header: đã dùng / ngân sách / ước tính của run hiện tại.

## 8. Prompt system và continuity

### 8.1 Prompt IR và compiler

- Prompt là **dữ liệu (IR)** rồi mới thành text. IR gồm các `section` có thứ tự cấu hình được; mỗi section có `source_refs` (field/entity revision nào đóng góp) để UI trả lời được “prompt này đến từ đâu”.
- Thứ tự mặc định (kế thừa v1.0): Style lock → Character locks → Location + spatial anchors → Scene context → Shot size/composition/camera → Action (một hành động chính) → Continuity từ shot trước → Lighting/palette/mood → Technical output → Negative.
- **Dialect theo provider/model:** giới hạn độ dài, có/không negative prompt, từ vựng camera, ngôn ngữ prompt, cách xử lý thoại. Compiler chọn dialect theo capability, không rải `if/else` theo tên provider. Hiệu quả của prompt tiếng Việt so với tiếng Anh phụ thuộc từng model **\[Cần xác minh theo model\]**.
- Template dùng engine sandbox (ví dụ Jinja2 sandbox), biến có kiểu, không chạy code tuỳ ý. Placeholder chưa resolve → `blocked(reference_missing)` hoặc lint blocker.
- Đầu ra: `prompt_text`, `prompt_hash`, `sections[]`, `warnings[]`, `length`. Cùng input phải cho cùng output (golden test).

### 8.2 Prompt linter

| ID | Quy tắc | Mức |
| --- | --- | --- |
| L001 | Mâu thuẫn camera (static vs dolly/orbit/pan) | blocker |
| L002 | Nhiều hơn một hành động chính trong shot ngắn (< 6 giây) | warning |
| L003 | Mật độ hành động vượt khả năng của thời lượng | warning |
| L004 | Placeholder/reference chưa resolve | blocker |
| L005 | Chứa cụm trong danh sách tránh của Style Bible | warning |
| L006 | Đổi bối cảnh/thời gian khi không có scene transition | blocker |
| L007 | Nhân vật không có mặt trong shot nhưng được mô tả | warning |
| L008 | Vượt `prompt_max_chars` của model (gợi ý rút gọn) | blocker |
| L009 | Yêu cầu chữ/biển hiệu trong video (model dựng chữ kém) → đề xuất overlay | info |
| L010 | Tên người thật, thương hiệu hoặc IP có thể vướng chính sách/bản quyền | warning |
| L011 | `action_end` của shot trước không khớp `action_start` của shot sau trong cảnh liên tục | warning |
| L012 | Đảo hướng nhìn/di chuyển (vượt trục 180°) không có chủ đích | warning |

### 8.3 AI-assist có kiểm soát

- Mọi đầu ra LLM theo **JSON Schema** → validate → vòng sửa tối đa 2 lần → nếu vẫn lỗi thì báo, không đoán.
- Kết quả là **proposal** (diff) kèm provenance (model, prompt, thời gian); người dùng duyệt từng thay đổi.
- `refine only`: không đổi trường đã lock (nhân vật, địa điểm, hành động lõi, camera rule). Kiểm tra bằng so sánh trường sau khi refine; vi phạm thì tự động từ chối proposal.
- Script dài: map-reduce theo scene heading (Fountain) hoặc chương, tóm tắt cuốn chiếu để giữ ngữ cảnh, không cắt giữa cảnh.
- **Eval harness:** 10–20 script mẫu (nhiều thể loại, có tiếng Việt) và tiêu chí chấm (đủ scene, không bịa nhân vật, JSON hợp lệ, số shot hợp lý). Chạy mỗi khi đổi prompt hệ thống hoặc model; lưu điểm theo phiên bản. Giai đoạn đầu chấm bằng rule + người, không dùng LLM-judge làm nguồn sự thật duy nhất.

### 8.4 Continuity: từ lời hứa đến công cụ

| Cơ chế | Mô tả | Milestone |
| --- | --- | --- |
| **Look-dev gate** | Trước khi sinh shot: tạo style frames, character sheet (nhiều góc), location plate. Người dùng duyệt → khoá `bible_snapshot`; ảnh đã duyệt thành reference chính thức | M4 |
| **Reference-first** | Shot dùng ảnh reference đã duyệt (nếu capability `reference_images`) thay vì chỉ mô tả bằng chữ | M4 |
| **Last-frame chaining** | `ExtractLastFrame` lấy khung cuối của take đã chọn làm first frame của shot kế (nếu có `first_frame`), hoặc dùng video-extend nếu model hỗ trợ | M4 |
| **Continuity lint (tĩnh)** | Quy tắc trên dữ liệu: vị trí, đạo cụ, hướng nhìn, trục di chuyển, trang phục theo cảnh | M2 |
| **Continuity QA (động, advisory)** | Lấy mẫu khung từ take, nhờ vision model so với reference/Bible: nhân vật có mặt, màu trang phục/đạo cụ, hướng nhìn → cờ kèm độ tin cậy và khung bằng chứng. **Chỉ cảnh báo, không tự loại take** | M4–M5 |
| **Promote to reference** | Từ take tốt, chọn một khung → dùng làm reference cho nhân vật/địa điểm (ghi provenance) | M4 |
| **Model pinning theo cảnh** | Tránh đổi model giữa shot cùng cảnh (§7.3) | M3 |

UI luôn nói trung thực: continuity là **mục tiêu + công cụ review**, không phải bảo đảm.

### 8.5 Ngôn ngữ

Ba cài đặt độc lập: `ui_language`, `prompt_language`, `dialogue_language`. Script tiếng Việt đi qua bước chuẩn hoá/dịch prompt nhưng giữ nguyên tên riêng và thoại. Chữ trong khung hình dựng bằng overlay, không nhờ model vẽ.

## 9. Trải nghiệm sản phẩm (UX)

### 9.1 Ba chế độ trên cùng một dữ liệu

| Chế độ | Dành cho | Nội dung |
| --- | --- | --- |
| **Guided** | Người mới, việc lặp lại | Stepper 7 bước: Story → Look → Shots → Keyframes → Videos → Cut → Export. Mỗi bước có cổng duyệt; ẩn graph; chạy template mặc định |
| **Board** (mặc định) | Đạo diễn, biên tập | Storyboard theo scene; thẻ shot; lọc; hành động hàng loạt; review |
| **Canvas** | Power user | Node editor; chỉnh pipeline; lưu thành Pipeline App |

### 9.2 Điều hướng

- Toàn cục: Projects · Queue · Library (template, Pipeline Apps, asset chung) · Settings (Connections, Budgets, System).
- Trong project: Overview · Script · Bible · **Board** · Review · Canvas · Timeline · Assets · Runs.
- Header: tên project + trạng thái lưu; undo/redo; **Validate**; **Dry run**; **Run**; **Cost HUD**; thông báo; command palette (`Ctrl/⌘+K`).
- Route: `/projects`, `/projects/:id/{script,bible,board,review,workflow/:wid,assets,timeline,runs}`, `/queue`, `/library`, `/settings/{connections,budgets,system}`. Hai trang `/settings/accounts` và `/settings/proxies` của v1.0 gộp thành tab trong connection, chỉ hiện khi capability có; thao tác quản trị deep-link sang gateway.

### 9.3 Màn hình Board

```
┌ Sidebar ┬──────────── Board · Scene 3: Quán cà phê ───────────┬ Inspector ┐
│ Script  │ [Lọc: trạng thái ▾  nhân vật ▾  model ▾]             │ Shot 3.2  │
│ Bible   │ [Chạy cảnh ▸  ~12 giây · ước tính]                   │ Spec      │
│ Board ● │ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐          │ Prompt    │
│ Review  │ │ thumb  │ │ thumb  │ │ thumb  │ │   +    │          │ Takes     │
│ Canvas  │ │3.1  ✔  │ │3.2 chạy│ │3.3  ⚠  │ │ Thêm   │          │ Lint (2)  │
│ Timeline│ │5s·1 take│ │ 40%   │ │lint 2  │ │ shot   │          │ Chi phí   │
│ Assets  │ └────────┘ └────────┘ └────────┘ └────────┘          │           │
├─────────┴──────────────────────────────────────────────────────┴───────────┤
│ Queue: 2 chạy · 5 chờ · 0 lỗi │ Log │ Takes │ Ngân sách 38/120 giây      │
└─────────────────────────────────────────────────────────────────────────────┘
```

Thẻ shot hiển thị: poster, số + tiêu đề, huy hiệu **Spec** (draft / needs review / approved), **Prompt** (clean / lint N), **Gen** (state), **Take** (current / outdated), thời lượng và chi phí ước tính. Hành động hàng loạt: duyệt, đổi model, gán reference, chạy chỉ shot cần.

### 9.4 Review mode (toàn màn hình)

- Mục tiêu: duyệt 50 take trong vài phút. Video lớn ở giữa, dải take phía dưới, panel spec/prompt/lint bên phải (ẩn được).
- Phím tắt: `J/K` shot trước/sau · `←/→` take trước/sau · `Space` phát · `A` chọn take / approve · `R` reject · `S` star · `C` so sánh A/B (side-by-side, wipe, onion-skin) · `F` đánh dấu lỗi continuity kèm ghi chú · `G` regenerate có mục tiêu (chỉ đổi camera / hành động / …) · `?` bảng phím tắt.
- So sánh khung cuối shot trước với khung đầu shot sau để bắt lỗi continuity.
- Hàng đợi review chỉ gồm shot cần duyệt; hiển thị tiến độ (ví dụ 12/30).

### 9.5 Audio, Timeline và Export

- **Audio Plan (theo scene):** thoại (nhân vật, dòng, ngôn ngữ), voice-over, SFX, music cue. Nguồn âm thanh: (a) audio gốc của model (capability `native_audio`); (b) TTS qua `AudioProvider`, giọng gắn với nhân vật trong Bible, cần consent nếu mô phỏng giọng người thật; (c) nhạc/SFX tạo hoặc tải lên; (d) subtitle lấy trực tiếp từ script (đã biết text nên không cần STT; STT chỉ để căn thời gian hoặc khi thiếu script).
- **Assembly timeline (không phải NLE đầy đủ):** sắp theo `order_key`; trim; reorder; hard cut + cross dissolve; track audio (thoại/music/SFX) với volume, fade, ducking cơ bản; subtitle SRT/VTT; title card; preview bằng **proxy 720p** do server tạo; cảnh báo gap/overlap/shot thiếu take; **Quick Assemble** dựng bản cắt đầu từ các take đã chọn.
- **Export:** H.264 + AAC mặc định; chuẩn hoá loudness bằng FFmpeg `loudnorm` theo profile (ví dụ web); profile 16:9 / 9:16 / 1:1; lưu manifest để dựng lại; nhúng AI disclosure/C2PA (§11.4).
- **Bàn giao NLE:** OTIO (P1), EDL/FCPXML (P2) kèm thư mục media/proxy. Thêm **Storyboard PDF** (contact sheet) để duyệt với khách.

### 9.6 Director Assistant, Pipeline Apps và onboarding

- **Director Assistant:** panel chat có ngữ cảnh (đang chọn shot/scene/graph). Công cụ giới hạn: đọc spec, đề xuất sửa, đề xuất đổi graph, chạy Dry run. **Không** có quyền chạy node tốn tiền hay xoá; mọi thay đổi là proposal có diff và được log. Ví dụ: “Làm 3.2 và 3.3 ngược sáng” → proposal sửa lighting hai shot + kết quả lint.
- **Pipeline Apps:** đóng gói một graph thành form (ví dụ “Trailer 30s”: tải script, chọn style, đặt ngân sách). Chạy qua cùng engine, có Dry run và ngân sách; người dùng không cần mở Canvas.
- **Demo project:** project mẫu 6 shot chạy hoàn toàn trên mock provider → ra MP4 trong < 5 phút. Sau đó là checklist “Kết nối provider thật” (probe + xác nhận chi phí).
- **Empty/loading/error:** mọi danh sách có empty state chỉ rõ bước kế tiếp; lỗi hiển thị lớp lỗi (§6.6) + hành động đề xuất, không dump stack trace; banner ở mọi màn hình liên quan khi connection `auth_error` hoặc circuit mở.
- **Responsive:** desktop-first; tablet ưu tiên Review + Queue; mobile để xem trạng thái, duyệt nhanh, nhận thông báo.
- **Import/export script:** Markdown, TXT, **Fountain** (định dạng kịch bản văn bản chuẩn), CSV/JSON shot list.

## 10. Design system

### 10.1 Nguyên tắc

Consistency over creativity. Công cụ chuyên nghiệp: **nền tối mặc định** (xem media ít loá), mật độ cao có kiểm soát, trạng thái luôn có icon + nhãn, bàn phím trước, mọi giá trị đi qua token (không hardcode). Mọi component dùng chung đều có tài liệu Storybook; thay đổi phá vỡ phải có hướng dẫn migrate.

### 10.2 Token (giá trị khởi điểm — kiểm tra contrast bằng công cụ trước khi chốt)

| Nhóm | Token | Khởi điểm (dark) |
| --- | --- | --- |
| Surface | `--bg-canvas` / `--bg-panel` / `--bg-raised` / `--bg-overlay` | `#0E1014` / `#151922` / `#1C2230` / `#232B3B` |
| Border | `--border-subtle` | `#2A3345` |
| Text | `--text-primary` / `--text-secondary` / `--text-disabled` | `#E8ECF4` / `#A5AEC0` / `#6B7589` |
| Accent | `--accent-500` / `--accent-400` (chữ, link trên nền tối) / `--focus-ring` | `#7C5CFF` / `#9B83FF` / `#9B83FF` (2px, offset 2px) |
| Status | info / success / warning / danger / neutral | `#4DA3FF` / `#3DD68C` / `#F5B544` / `#FF6B6B` / `#8A94A8` |
| Typography | UI: Inter (variable); mã, prompt, ID: JetBrains Mono | thang 11/12/13/14/16/20/24/32; line-height 1,4–1,6; `tabular-nums` cho timecode và chi phí |
| Spacing | Cơ sở 4 px | 0, 2, 4, 8, 12, 16, 24, 32, 48, 64 |
| Radius | sm / md / lg / xl | 4 / 6 / 10 / 16 px |
| Elevation | 0–4 | Dark theme ưu tiên viền và độ sáng nền hơn bóng đổ |
| Motion | fast / base / slow / page | 90 / 150 / 240 / 400 ms; `prefers-reduced-motion` → bỏ chuyển động trang trí |
| Z-index | base / sticky / dropdown / overlay / modal / toast | 0 / 10 / 20 / 30 / 40 / 50 |

Light theme dùng cùng tên token semantic với giá trị khác (theo `prefers-color-scheme` + công tắc thủ công).

```css
:root {
  --bg-canvas: #0E1014;  --bg-panel: #151922;
  --text-primary: #E8ECF4;  --accent-500: #7C5CFF;
  --status-success: #3DD68C;  --status-danger: #FF6B6B;
  --space-2: 8px;  --radius-md: 6px;  --dur-fast: 150ms;
}
```

### 10.3 Trạng thái và kiểu cổng — không dựa riêng vào màu

| Trạng thái | Màu | Icon | Nhãn |
| --- | --- | --- | --- |
| `idle` / `never_run` | neutral | vòng rỗng | Chưa chạy |
| `ready` | info | chấm | Sẵn sàng |
| `blocked` | warning | khoá | Bị chặn + lý do |
| `queued` | neutral | đồng hồ cát | Đang chờ |
| `running` | info | vòng quay (animation) | Đang chạy |
| `waiting_approval` | accent | bàn tay | Chờ duyệt |
| `succeeded` | success | dấu tick | Xong |
| `failed` | danger | dấu X | Lỗi + lớp lỗi |
| `canceled` / `skipped` | neutral | gạch ngang / mũi tên bỏ qua | Đã huỷ / Bỏ qua |
| `stale` | warning, viền đứt | mũi tên vòng | Outdated |
| `locked` | accent | ổ khoá | Đã khoá |

Cổng (port) phân biệt bằng **màu + hình dạng** (ví dụ tròn: text; vuông: ảnh; thoi: video; tam giác: audio; lục giác: script/Bible) để người mù màu vẫn đọc được. Yêu cầu: chữ ≥ 4,5:1, thành phần UI ≥ 3:1; kiểm tra tự động bằng axe/Storybook a11y.

### 10.4 Danh mục component

| Component | Biến thể | Trạng thái chính | Ghi chú a11y |
| --- | --- | --- | --- |
| AppShell / Sidebar / Inspector / SplitPane | collapsed, pinned | resize, focus | Landmark roles; chia panel bằng phím mũi tên |
| CommandPalette | global, contextual | empty, loading | Dialog + combobox pattern |
| StatusBadge | sm/md; dot/pill | toàn bộ §10.3 | Icon + chữ, không chỉ màu |
| ShotCard | compact, board, detail | default, hover, selected, focus, dragging, loading, error, outdated | Điều hướng bằng mũi tên; Enter mở |
| TakeStrip / TakeCompare | A/B, side-by-side, wipe, onion | loading, lệch thời lượng | Điều khiển phát bằng bàn phím; phụ đề |
| BibleCard | character, location, prop, style | trường khoá, thiếu reference | Trường khoá có icon + `aria-label` |
| PromptViewer | layered, diff, lint-marked | section thu gọn, bị override | Chú thích lint đọc được bởi screen reader |
| LintPanel | list, inline | blocker / warning / info | Nhóm theo mức; phím nhảy tới lỗi |
| NodeCard / Port / Edge | theo node type | idle … failed, stale, locked | Điều hướng node bằng phím; có danh sách node thay cho canvas |
| ApprovalGate | inline, modal | pending, approved, rejected | Nhãn nút rõ; xác nhận khi phá huỷ |
| CostHUD / CostEstimate | header, popover, dry-run | known, unknown, over-budget | “Không xác định” là trạng thái hợp lệ |
| JobRow / QueueTable | compact | running, failed, canceled | Bảng virtualized có `aria-rowcount` |
| ConnectionHealth | badge, panel | health × circuit × quota | Tách ba trục |
| Timeline (Ruler, Track, Clip, Playhead, Waveform) | video, audio, subtitle | selected, trimming, gap, thiếu take | Trim bằng phím; thông báo thay đổi |
| DiffViewer | script, spec, prompt | accept / reject từng hunk | Inline và side-by-side |
| Banner / Toast / ConfirmDanger / EmptyState | — | — | `aria-live` đúng cấp; lỗi không tự biến mất |

### 10.5 Tài liệu mẫu cho hai component then chốt

**StatusBadge** — Props: `state` (enum §10.3), `size` (`sm`/`md`), `reason?` (string), `showLabel` (bool, mặc định true). Luôn có icon và nhãn; `running` chỉ animate khi không bật reduced-motion; `reason` hiển thị trong tooltip và gắn `aria-describedby`. Không dùng: chỉ màu; nhãn mới ngoài từ vựng chung.

**ShotCard** — Props: `shot`, `variant` (`compact`/`board`/`detail`), `selected`, `onOpen`, `onGenerate` (disabled nếu `blocked`), `showCost`. Bàn phím: mũi tên di chuyển giữa thẻ, Enter mở inspector, `G` chạy (khi có quyền và ngân sách), Space chọn. Screen reader đọc kiểu: “Shot 3.2, spec đã duyệt, prompt 2 cảnh báo, đang chạy, 5 giây”. Kéo thả đổi thứ tự = cập nhật `order_key`. Thẻ `outdated` có viền đứt + huy hiệu và **không ẩn** take cũ.

### 10.6 Quy tắc áp dụng (checklist audit)

- 0 giá trị màu/khoảng cách hardcode trong component (lint rule `no-raw-values`); mọi trạng thái dùng `StatusBadge`.
- Storybook cho 100% component dùng chung; visual regression + axe chạy trong CI.
- i18n: không chuỗi cứng trong JSX; chữ nội dung chính không nhỏ hơn 12 px; vùng chạm ≥ 24×24 px (WCAG 2.2) và ≥ 44 px trên mobile.
- Thứ tự áp dụng: nhất quán tên → phủ token → độ hoàn thiện component (variants/states/docs) → tài liệu.

## 11. Bảo mật, riêng tư và tuân thủ

### 11.1 Mô hình đe doạ (rút gọn)

| Tài sản / mối đe doạ | Biện pháp |
| --- | --- |
| API key provider, Admin key bị lộ qua log/UI/export/browser | Mã hoá envelope (khoá từ env/secret store, hỗ trợ xoay vòng); mask trong API response; redact tập trung ở log, exception, diagnostic bundle; không đưa vào URL hay graph; Admin key tách riêng, tuỳ chọn, quyền đọc |
| Cookie tài khoản muse.ai | **MuseStudio không nhận, không lưu** (F2) |
| Upload và URL media: SSRF, path traversal, file độc | Chặn IP private/link-local/metadata; download proxy có giới hạn dung lượng/thời gian; kiểm tra MIME + chữ ký file; `ffprobe` trong sandbox; giới hạn độ phân giải/thời lượng; tên file do hệ thống đặt |
| FFmpeg: shell injection, DoS | Args sinh từ schema đã validate (không nối chuỗi shell); worker `media` không network, `no-new-privileges`, cgroup CPU/RAM, timeout, giới hạn threads |
| Prompt injection qua script | Script là dữ liệu không tin cậy: node LLM không có công cụ, mạng hay filesystem; đầu ra chỉ qua schema; kiểm tra `refine only`; nội dung script không được đổi cấu hình hay ngân sách |
| Truy cập trái phép | Mật khẩu băm Argon2id + TOTP/passkey; cookie `HttpOnly; Secure; SameSite=Lax`; CSRF token; rate-limit đăng nhập; audit |
| Rò rỉ asset | Chỉ tải qua route có authz hoặc presigned URL ngắn hạn (≤ 15 phút) |
| Chuỗi cung ứng | Lockfile; `pip-audit`/`npm audit`/Trivy trong CI; SBOM; container non-root, read-only FS khi có thể; không tự cập nhật gateway |
| Mất dữ liệu | Backup DB + manifest + (tuỳ chọn) media; kiểm thử restore định kỳ |

### 11.2 Phân quyền

MVP: một admin. Thiết kế sẵn vai trò Owner, Editor, Reviewer (chỉ duyệt/comment), Viewer, Operator (connections/ngân sách). Kiểm quyền ở tầng domain, không chỉ ở route. Audit cho: tạo/xoá connection, đổi ngân sách, xoá asset, export project, đổi cấu hình proxy.

### 11.3 Quyền riêng tư và dữ liệu

- Ảnh/giọng người thật: yêu cầu xác nhận quyền sử dụng (consent), lưu `consent_ref`; cảnh báo khi dùng khuôn mặt/giọng làm tham chiếu; không hỗ trợ tạo nội dung giả mạo người thật khi không có consent.
- Dữ liệu gửi tới provider (prompt, ảnh tham chiếu) được liệt kê rõ trong Dry run; UI connection ghi chú rằng provider có thể lưu dữ liệu.
- Retention cấu hình theo loại (take bị loại, proxy, log); xoá theo yêu cầu; export dữ liệu cá nhân. Tuân thủ quy định bảo vệ dữ liệu cá nhân nơi vận hành (ví dụ Việt Nam, EU). Đây không phải tư vấn pháp lý.

### 11.4 Provenance, AI disclosure, an toàn nội dung

- Export mặc định chèn metadata “AI-generated” và thông tin công cụ; tuỳ chọn **C2PA Content Credentials** nếu môi trường hỗ trợ ký; nhãn/watermark hiển thị theo profile. Nghĩa vụ minh bạch theo EU AI Act (một nguồn nêu hiệu lực từ 2/8/2026) cần được tư vấn pháp lý kiểm tra nếu phân phối tại EU **\[Cần xác minh\]**.
- Provider từ chối vì chính sách → lớp lỗi `policy_rejected`; hiển thị lý do, không tự diễn đạt lại để lách. Hook moderation tuỳ chọn trước khi gửi theo chính sách của operator.
- Điều khoản dịch vụ của provider: operator tự chịu trách nhiệm xác minh việc dùng gateway/tự động hoá có được phép hay không. MuseStudio không cung cấp tính năng xoay tài khoản/proxy để né giới hạn.
- Toàn vẹn: asset có checksum; `provenance` ghi provider, model, prompt\_version, run.

## 12. Quan sát và vận hành

### 12.1 Logging, trace, metrics

- Log JSON có cấu trúc: `correlation_id`, `workspace_id`, `project_id`, `run_id`, `node_execution_id`, `task_id`; redaction tập trung (middleware + test).
- OpenTelemetry trace xuyên API → enqueue → worker → provider call. Metrics (Prometheus): độ sâu/độ trễ hàng đợi, thời lượng job, latency và lỗi provider theo lớp lỗi, retry, trạng thái circuit, tần suất poll, cost ledger, dung lượng đĩa, số kết nối SSE.
- Dashboard: sức khoẻ connection, hàng đợi, chi phí, lỗi theo lớp, dung lượng. **Diagnostic bundle** không chứa bí mật.

### 12.2 SLO nội bộ và cảnh báo

| SLI | SLO khởi điểm | Cảnh báo |
| --- | --- | --- |
| `POST generate` → 202 | p95 < 500 ms | > 1 giây trong 5 phút |
| Độ trễ cập nhật trạng thái qua SSE | p95 < 3 giây | > 10 giây |
| Intent `unknown` | 0 kéo dài | Bất kỳ intent `unknown` > 10 phút |
| Reconciler lag | < 60 giây | > 5 phút |
| Task provider báo xong mà qua được tải + probe | ≥ 99% | < 95% |
| Dung lượng đĩa media | < 80% | ≥ 80% |
| Backup gần nhất | < 24 giờ | > 36 giờ |

### 12.3 Vận hành

- **Backup:** `pg_dump` hằng ngày, giữ N bản; object storage mirror/snapshot; `backup.sh`/`restore.sh`; **drill khôi phục** mỗi quý; ghi rõ RPO/RTO (ví dụ RPO ≤ 24 giờ cho self-host nhỏ).
- **Retention:** dọn orphan asset, proxy cũ, take bị loại (theo cấu hình), log 14–30 ngày.
- **Runbook:** gateway down / `auth_error`; hàng đợi kẹt; intent `unknown`; đầy đĩa; restore; xoay secret; nâng cấp gateway (ghim → thử ở staging → probe → bật).
- **Nâng cấp:** migration expand/contract; changelog + hướng dẫn rollback.
- **Cấu hình:** `.env.example` đầy đủ; Docker secrets; healthcheck mọi service; cấu hình tối thiểu khuyến nghị 4 vCPU / 8 GB RAM + đĩa theo §4.5 **\[ước tính\]**.

## 13. Chiến lược kiểm thử

| Tầng | Nội dung | Gợi ý công cụ |
| --- | --- | --- |
| Unit (domain) | State machine, prompt compiler (golden), linter, cache key, graph validator (cycle, type), cost estimator | pytest, hypothesis (property-based cho graph) |
| Contract | Adapter ↔ mock server ↔ gateway mock-driver; schema OpenAPI; capability probe | pytest + HTTP mock, schemathesis |
| Integration | DB + queue + worker thật trong Compose: two-phase submit, reconciler, retry | pytest + testcontainers |
| Chaos / fault | Kill worker ở từng bước; mất DB tạm; provider chậm/5xx/429/auth/quota/policy/URL hết hạn/file hỏng | Mock fault injection |
| Frontend | Component (Storybook), a11y (axe), visual regression, hooks | Vitest, Testing Library, Playwright |
| E2E | Demo project → mock → MP4; review bằng bàn phím; Dry run + ngân sách; restart giữa chừng | Playwright |
| AI eval | Eval harness phân tích script (§8.3); regression khi đổi prompt/model | Script + dataset mẫu |
| Live (opt-in) | `LIVE_PROVIDER_TESTS=1` + xác nhận operator + ngân sách tối đa; chạy thủ công hoặc nightly riêng; lưu artifact | pytest marker |
| Security | SAST, dependency scan, secret scan, test SSRF/path traversal, fuzz nhẹ FFmpeg | CI |

Quy tắc: CI **không bao giờ** gọi provider thật; mỗi bug sửa kèm regression test; coverage domain ≥ 85% chỉ là hướng dẫn, không thay cho test có ý nghĩa; capability chỉ `verified` khi có artifact live.

## 14. Lộ trình triển khai

| M | Mục tiêu | Nội dung chính | Exit criteria | Ước lượng (kỹ sư-tuần) |
| --- | --- | --- | --- | --- |
| **M0** Verify & decide | Biết chắc cái gì chạy được | Audit repo; build/test gateway; chạy mock-driver E2E; probe health/models/chat/image/video/poll/media bằng tài khoản được phép; capability matrix; chọn adapter chính thức; chốt ADR-001 | Có video thật được tải + `ffprobe` đạt, **hoặc** báo cáo thất bại rõ ràng. **Cổng:** gateway không dùng được → adapter chính thức thành primary | 0,5–1 |
| **M1** Foundation + walking skeleton | Vòng lặp end-to-end mỏng | Compose, API, Postgres + migration, auth single-admin, `workspace_id`, asset storage, mock provider, CI, design tokens + AppShell + Storybook. **Lát cắt dọc:** dán script → 3 shot thủ công → generate (mock) → xem take → export MP4 | Demo project chạy < 5 phút trên máy sạch; CI xanh | 2–3 |
| **M2** Script → Shots | Biến script thành shot có kiểm soát | Script Studio (revision, diff, Fountain/MD/TXT), `LLMProvider` + schema + eval harness, Bible CRUD, Board, prompt compiler + linter, approval | AC-01…AC-06; eval baseline | 3–4 |
| **M3** Generation core | Sinh thật, bền, rẻ | Orchestrator DB-first, queue, two-phase submit, reconciler, timer, retry taxonomy, scheduler + circuit, takes, Review mode, SSE, Cost Guard, Dry run; adapter muse2api (experimental) + **1 adapter chính thức** | AC-07…AC-14; chaos test 0 lần gửi trùng | 4–5 |
| **M4** Continuity | Nhất quán nhân vật/cảnh | Look-dev gate, reference sheet, last-frame chaining, Promote to reference, ContinuityQA (advisory), model pinning | So sánh A/B continuity flag / 10 shot và retake / shot so với M3 | 3–4 |
| **M5** Pipelines & Canvas | Tuỳ biến cho power user | Graph editor (xyflow), validate kiểu/cycle, versioning, stale/lock/run-from-here, Pipeline Apps, Director Assistant | AC-15…AC-17 | 3–4 |
| **M6** Audio, Timeline, Export | Ra phim hoàn chỉnh | Audio Plan, TTS/music, subtitle, loudnorm, Assembly timeline + proxy, Quick Assemble, export H.264/AAC, OTIO, Storyboard PDF, C2PA/AI disclosure | AC-18, AC-19 | 4–5 |
| **M7** Ops hardening | Tin cậy dài hạn | OpenTelemetry, dashboard, SLO/alert, drill backup/restore, retention, rà soát bảo mật, multi-connection | AC-20; restore drill thành công | 2–3 |
| **M8** Collab & ecosystem | Mở rộng | Roles/OIDC, comment, link review cho khách, webhook/API, template gallery, provider thứ hai/ba | — | 4+ |

Ước lượng chỉ là thô (±50%), giả định một kỹ sư full-stack có kinh nghiệm làm cùng coding agent, chưa gồm nghiên cứu người dùng và thiết kế UI chi tiết; cần hiệu chỉnh theo câu trả lời ở §18.1. AC-21 (a11y) và AC-22 (capability) áp dụng liên tục từ M1.

So với v1.0: Canvas (M3 cũ) dời sang M5; engine chạy graph có từ M3 nhưng với template cố định; M4–M5 cũ được gộp thành M3 vì cùng một lõi (orchestrator + provider); timeline và ops giữ vị trí. **Cổng giữa các milestone:** demo theo kịch bản, báo cáo bằng chứng, không sang milestone sau nếu chưa đạt exit criteria.

## 15. Acceptance criteria (đo được)

Mỗi tiêu chí phải có test tự động hoặc kịch bản kiểm thử thủ công được ghi lại. Không đánh dấu hoàn thành nếu thiếu bằng chứng.

| AC | Tiêu chí | Cách kiểm |
| --- | --- | --- |
| AC-01 | Tạo project, dán/import script (MD/TXT/Fountain), lưu revision; bản gốc không bị ghi đè | E2E + test dữ liệu |
| AC-02 | Phân tích script → scenes/shots với JSON hợp lệ sau tối đa 2 vòng sửa trên toàn bộ bộ eval; lỗi schema được hiển thị, không đoán | Eval harness |
| AC-03 | Mọi thay đổi do AI là diff có provenance; chấp nhận/từ chối từng hunk | E2E |
| AC-04 | Compiler: cùng input → cùng prompt + hash; mỗi section truy được nguồn | Unit (golden) |
| AC-05 | Linter bắt đủ L001–L012 trên bộ fixture; ghi nhận precision/recall | Unit |
| AC-06 | Placeholder/reference thiếu → `blocked(reference_missing)`, **không** gửi request | Integration |
| AC-07 | `POST generate` trả 202 với p95 < 500 ms; run + node\_executions + cost\_reservation tạo trong một transaction | Integration + load nhẹ |
| AC-08 | Kill worker ở bất kỳ bước nào của §6.4 → 0 lần gửi trùng; intent `unknown` được xử lý đúng quy trình | Chaos test |
| AC-09 | Restart api/worker: UI khôi phục trạng thái, run tiếp tục | E2E |
| AC-10 | Retry đúng bảng §6.6; auth/quota/policy/invalid không retry; số lần có giới hạn | Contract + fault injection |
| AC-11 | `succeeded` chỉ khi file đã tải + checksum + `ffprobe` đạt | Integration |
| AC-12 | Dry run trả kế hoạch, ước tính (hoặc “không xác định”) và lý do bị chặn; không gọi provider | Integration |
| AC-13 | Vượt ngân sách → `blocked(budget)`; batch lớn cần xác nhận kèm tổng ước tính | E2E |
| AC-14 | Secrets không xuất hiện trong browser storage, URL, log, exception, export, diagnostic bundle | Test quét tự động + review |
| AC-15 | Graph: validate kiểu cổng, chặn cycle, lưu/mở lại chính xác, import/export có version | Unit + property-based |
| AC-16 | Sửa một shot: chỉ node downstream liên quan thành `stale`; node `locked` không bị chạy lại; node tốn tiền không tự chạy | Integration |
| AC-17 | Fan-out N shot → N execution; lỗi một shot không làm mất kết quả shot khác | Integration |
| AC-18 | Timeline: xếp, trim, thêm audio/subtitle; export MP4 H.264/AAC kiểm bằng `ffprobe`; loudness nằm trong dung sai của profile **\[ngưỡng cần chốt\]** | E2E + ffprobe/ebur128 |
| AC-19 | Export có metadata AI disclosure; C2PA khi bật | Test file |
| AC-20 | `docker compose up` trên máy sạch ≤ 10 phút; backup/restore chạy được và có test | Script test |
| AC-21 | Luồng lõi đạt WCAG 2.2 AA (axe không lỗi nghiêm trọng + kiểm tra bàn phím thủ công); UI có vi/en | Playwright + axe |
| AC-22 | Capability chỉ `verified` khi có artifact live, `verified_at` và version | Review báo cáo M0/M3 |

## 16. Prompt triển khai cho coding agent (Claude Code / Antigravity)

### 16.1 Chuẩn bị

Đặt tài liệu này tại `Docs/MUSESTUDIO_PRD_v2.0.md` ở root repo. Tạo `AGENTS.md` (hoặc `CLAUDE.md`) chứa lệnh build/test/lint, quy ước code và các quy tắc bất biến bên dưới. Giao **từng milestone một**.

### 16.2 Prompt

> Bạn là Principal Engineer xây dựng MuseStudio theo `Docs/MUSESTUDIO_PRD_v2.0.md`. Chỉ làm đúng milestone được giao; không tự sang milestone kế tiếp.
>
> **Trước khi viết code**
>
> 1. Audit repo: stack, test, lint, Docker, `git status`. Không ghi đè hoặc xoá khi chưa kiểm tra. Làm trên nhánh riêng, commit nhỏ, message rõ.
> 2. Đọc toàn bộ PRD. Tạo/cập nhật `AGENTS.md`, `Docs/ARCHITECTURE.md`, `Docs/DECISIONS.md` (ADR), `Docs/IMPLEMENTATION_PLAN.md`, `Docs/TEST_STRATEGY.md`.
> 3. Trình **kế hoạch cho milestone hiện tại** (nhiệm vụ, thứ tự, rủi ro, câu hỏi mở) và **chờ tôi duyệt** trước khi code.
> 4. Với M0: audit gateway và viết `Docs/MUSE2API_CAPABILITY_MATRIX.md` (`verified | mock-only | unverified | unsupported`) kèm bằng chứng. Không đoán endpoint hay option. Mọi lần gọi provider thật phải được tôi cho phép trước và có ngân sách tối đa.
>
> **Quy tắc bất biến**
>
> - Web UI không giữ hay gửi Admin key/cookie; MuseStudio không nhận cookie tài khoản provider.
> - Mọi generation/render chạy trong worker; API trả `202` + `run_id`.
> - CI không gọi dịch vụ ngoài; live test chỉ chạy khi `LIVE_PROVIDER_TESTS=1` và có xác nhận của operator.
> - Provider adapter là điểm duy nhất gọi provider; capability/probe quyết định tính năng bật hay tắt.
> - Two-phase submit; không bao giờ tự gửi lại intent `unknown`.
> - Retry theo bảng lỗi; không retry vô hạn auth/quota/policy/invalid; không xoay tài khoản/proxy để né giới hạn.
> - Graph được validate và versioned; run có snapshot và event log; node tốn tiền không tự chạy.
> - Redact secrets ở log, exception, export, diagnostic; có test quét.
> - Không tự cập nhật hay tải-và-chạy mã từ internet khi runtime.
> - Không chạm file ngoài repo; không chạy lệnh phá huỷ (xoá hàng loạt, drop DB, force push) khi chưa được yêu cầu rõ; sao lưu file cấu hình trước khi sửa.
> - Tests-first cho domain; mỗi bug sửa kèm regression test.
> - Không đổi stack vì sở thích; quyết định kiến trúc ghi vào ADR.
>
> **Definition of Done của milestone:** test/lint/typecheck xanh; migration lên/xuống chạy được; Docker quick start chạy trên máy sạch; mỗi AC liên quan có test; tài liệu cập nhật; không còn TODO ẩn.
>
> **Báo cáo cuối milestone:** repo audit, lệnh đã chạy và kết quả, AC nào đạt (kèm bằng chứng/artifact), live test nào đã/chưa được phép, blocker, rủi ro mới, kế hoạch milestone kế tiếp. Không viết “đã hoàn thành” khi thiếu bằng chứng. Sau đó **dừng**.

### 16.3 Thiết lập môi trường làm việc với agent

- Xác định OS/shell trước khi đưa lệnh; ghi vào `AGENTS.md`.
- Kiểm tra key/quyền bằng một lệnh gọi nhỏ **trước** khi xây thứ phụ thuộc vào nó (probe M0 chính là bước này); báo rõ nếu key hết hạn hay hết credit.
- Không dán secrets vào chat hay commit: dùng `.env` (đã `.gitignore`) và Docker secrets; nếu lỡ lộ thì xoay ngay.
- Sao lưu file cấu hình (`.env`, compose, cấu hình MCP) thành `.bak` trước khi sửa; validate JSON/YAML sau mỗi lần sửa.
- Công cụ MCP gợi ý, quyền tối thiểu: GitHub (PR/issue), Postgres với role **chỉ đọc**, Playwright (E2E/visual). Thêm hook chặn commit chứa secret và chặn lệnh phá huỷ.
- Kiểm chứng rồi mới dừng: sau thay đổi hạ tầng, xác nhận service thực sự lên (`docker compose ps`, healthcheck) trước khi báo xong; ghi các bước đã chạy thành script/`Makefile` để lặp lại.

## 17. Sổ đăng ký rủi ro

| # | Rủi ro | Khả năng | Tác động | Giảm thiểu | Dấu hiệu kích hoạt |
| --- | --- | --- | --- | --- | --- |
| R1 | Gateway muse2api gãy, đổi API hoặc bị khoá tài khoản | Cao | Cao | ADR-001; adapter chính thức; ghim version; probe; mock | Probe fail; `auth_error` > 5% |
| R2 | Vi phạm điều khoản provider (tự động hoá, nhiều tài khoản) | Trung bình | Cao | Operator tự xác minh ToS; không xoay tài khoản/proxy; ưu tiên API chính thức | Cảnh báo từ provider |
| R3 | Chi phí vượt kiểm soát | Trung bình | Cao | Cost Guard; ngân sách; draft→final; xác nhận batch | Chi tiêu vượt ngân sách ngày |
| R4 | Gửi trùng hoặc mất task | Trung bình | Cao | Two-phase submit; reconciler; chaos test | Xuất hiện intent `unknown` |
| R5 | Scope creep (canvas, timeline, NLE) | Cao | Cao | Walking skeleton; Board-first; non-goals; cổng milestone | Milestone trễ > 50% |
| R6 | Kỳ vọng continuity quá cao | Cao | Trung bình | Thông điệp trung thực; công cụ review; đo flag | Phản hồi người dùng |
| R7 | Phân tích script kém hoặc không ổn định | Trung bình | Trung bình | Schema + eval harness + proposal/diff; LLM thay thế được | Điểm eval tụt |
| R8 | Dung lượng media phình to | Cao | Trung bình | Proxy, retention, cleanup, cảnh báo 80% | Đĩa ≥ 80% |
| R9 | Rò rỉ secrets | Thấp | Rất cao | Redaction tập trung; test quét; envelope encryption; không nhận cookie | Phát hiện bởi scan |
| R10 | Vấn đề pháp lý (người thật, IP, AI disclosure) | Trung bình | Cao | Consent; lint L010; disclosure/C2PA; tư vấn pháp lý khi thương mại hoá | Khiếu nại |
| R11 | Hiệu năng Canvas/Board khi dữ liệu lớn | Trung bình | Trung bình | Virtualization; benchmark sớm; giới hạn mặc định | FPS < 30 |
| R12 | Model/API bị khai tử nhanh | Cao | Trung bình | Capability registry; pinning; model thay thế gợi ý | Model biến mất khỏi danh sách |
| R13 | Khó bảo trì khi ít người | Trung bình | Trung bình | ADR; tài liệu; test; `AGENTS.md` | — |
| R14 | Trùng tên/thương hiệu | Trung bình | Thấp | Kiểm tra tên trước khi công khai | — |

## 18. Câu hỏi mở và phụ lục

### 18.1 Cần bạn quyết định

1. **Mục đích sử dụng:** cá nhân hay thương mại/phát hành? (ảnh hưởng điều khoản, pháp lý, mức ưu tiên provider chính thức)
2. **Quyền truy cập hợp lệ:** bạn có API chính thức nào (Veo/Gemini, Runway, Kling, khác) và ngân sách thử nghiệm cho M0?
3. **Nguồn lực:** ai code (bạn, agent), bao nhiêu giờ mỗi tuần? Ước lượng §14 cần hiệu chỉnh theo câu trả lời này.
4. **Hosting:** máy cá nhân hay VPS? Có cần truy cập từ xa hoặc nhiều người dùng sớm không?
5. **Ngôn ngữ chính** của script/thoại (Việt, Anh, khác) để ưu tiên dialect và TTS.
6. **ADR-002:** chấp nhận Postgres-first hay giữ Redis + Celery như v1.0?
7. **Bàn giao NLE (OTIO/EDL):** cần sớm hay chỉ cần MP4?
8. **Giấy phép dự án:** mã nguồn mở hay riêng tư?
9. **Tên sản phẩm:** giữ “MuseStudio” hay đổi?

### 18.2 Phụ lục A — API surface (REST + SSE)

Quy ước: OpenAPI sinh client TypeScript; lỗi theo `problem+json`; phân trang cursor; `ETag`/`If-Match` khi sửa; header `Idempotency-Key` cho mọi `POST` tốn tiền; tác vụ dài trả `202` + `run_id`.

| Nhóm | Endpoint |
| --- | --- |
| Project | `POST /projects`, `GET/PATCH /projects/{id}` |
| Script | `POST /projects/{id}/scripts`, `POST /scripts/{id}/revisions`, `POST /scripts/{id}/analyze` (202) |
| Bible | `GET/POST/PATCH /projects/{id}/bible/entities` |
| Cấu trúc | `GET/POST/PATCH /scenes`, `/shots` (PATCH tạo revision mới) |
| Prompt | `POST /shots/{id}/compile-prompt`, `GET /shots/{id}/lint` |
| Chạy | `POST /shots/{id}/generate` (202), `POST /runs/dry`, `POST /runs/{id}/cancel`, `POST /runs/{id}/retry` |
| Run | `GET /runs/{id}`, `GET /runs/{id}/events` (SSE, `Last-Event-ID`) |
| Take | `GET/PATCH /takes/{id}` (select, reject, star) |
| Asset | `GET/POST /assets`, `GET /assets/{id}/renditions/{name}` (presigned) |
| Connection | `GET/POST/PATCH /connections`, `POST /connections/{id}/probe` |
| Ngân sách | `GET /budgets`, `PUT /projects/{id}/budget` |
| Timeline | `POST /timelines/{id}/export` (202) |
| Hệ thống | `GET /health`, `GET /metrics`, `GET /diagnostics` (không chứa bí mật) |

Webhook gửi ra ngoài được ký HMAC và retry theo backoff.

### 18.3 Phụ lục B — Bổ sung cho Shot schema

Giữ nguyên schema tối thiểu của v1.0 và thêm: `revision_id`, `spec_hash`, `order_key`, `quality_tier` (draft/final), `reference_bindings[{entity_id, asset_id, role}]`, `audio_plan{dialogue[], vo, sfx[], music_cue}`, `continuity{prev_action_end_ref, screen_direction, axis_line}`, `cost_estimate{units, unit, known}`, `model_policy{mode, model_id}`, `flags{contains_real_person, consent_ref}`.

### 18.4 Phụ lục C — Nguồn tham khảo

Nguồn bên thứ ba cần kiểm chứng lại trước khi ra quyết định tích hợp hoặc mua.

- Runway Workflows: https://help.runwayml.com/hc/en-us/articles/45763528999699 · hồ sơ tổng hợp: https://yespress.io/products/runway.md
- Google Veo 3.1 và Flow: https://yourstory.com/ai-story/google-veo-31-flow-ai-editing-tool · https://aividpipeline.com/blog/google-flow-veo-3-1-guide-2026
- Adobe Firefly video editor: https://blog.adobe.com/en/publish/2025/12/16/adobe-firefly-improves-ai-video-creation-tools-new-models-unlimited-generations · https://blog.adobe.com/en/publish/2026/02/25/putting-ideas-in-motion-redefining-ai-video-with-adobe-firefly
- ComfyUI partner nodes: https://comfy.org/p/supported-models/runway.md
- muse2api (releases): https://github.com/www222fff/muse2api/releases
- Tổng hợp API/model video 2026: https://provimedia.de/en/blog/ai-video-tools-2026 · https://rangy.ai/blog/ai-video-generators-compared-2026

### 18.5 Phụ lục D — Pipeline mặc định “Script to Film” (v2)

```
ScriptInput → ScriptAnalyze → StoryBibleBuild → [LookDevGate]
  → SceneBreakdown → ShotBreakdown → ContinuityLint → [ManualApproval]
  → PromptTemplate → CostGate
  → ImageGenerate (keyframe, draft) → [QualityGate: duyệt keyframe]
  → Map(shot) { VideoGenerate (draft) → ExtractLastFrame → ContinuityQA } → Collect
  → [Review: chọn take] → Promote draft→final (chỉ shot đã duyệt)
  → AudioPlan → TTS / MusicGenerate → SubtitleBuild
  → TimelineAssemble → LoudnessNormalize → VideoExport → Webhook/Notify
```

Quy tắc: `BLOCKED_REFERENCE` của v1.0 nay là `blocked(reference_missing)`; keyframe bị từ chối chỉ chạy lại nhánh prompt/reference/shot bị ảnh hưởng; shot lỗi không chặn shot độc lập; timeline chỉ gom take đã chọn hoặc yêu cầu xác nhận cách xử lý shot thiếu.

### 18.6 Phụ lục E — Bản đồ chuyển đổi v1.0 → v2.0

| v1.0 | v2.0 |
| --- | --- |
| §1 Tầm nhìn, luồng end-to-end | §1, §9.1 |
| §2 Tham khảo thị trường | §2 (cập nhật 10/2026) |
| §3.1–3.2 Dashboard, Script Studio | §8.3, §9 |
| §3.3–3.5 Story Bible, Shot Board, Prompt Studio | §5, §8, §9.3 |
| §3.6 Workflow Canvas | §6, ADR-004, M5 |
| §3.7 Queue & render farm | §6.5–6.7, §7.6 |
| §3.8 Asset Library | §5.2, §5.4 |
| §3.9 Timeline & Export | §9.5 |
| §3.10 Connections, accounts, proxy | §7.4, §11 |
| §3.11 Monitoring | §12 |
| §4 UX / Layout | §9, §10 |
| §5 Kiến trúc kỹ thuật | §3, §4 |
| §6 Tích hợp muse2api | §7 |
| §7 Data model | §5 |
| §8 Prompt build contract | §8 |
| §9 Bảo mật & độ tin cậy | §11 |
| §10 Milestones | §14 |
| §11 Acceptance criteria | §15 |
| §12 Workflow mẫu | §18.5 |
| §13 Prompt cho coding agent | §16 |
| §14 Các quyết định đã chốt | §3 (ADR) |
