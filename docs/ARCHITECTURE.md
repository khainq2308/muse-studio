# MuseStudio Technical Architecture v1.1

1. **Non-blocking Async Polling:** Tác vụ tạo video không giữ kết nối chờ mà tách thành `dispatch_video_job` và `poll_video_job` với exponential backoff trên ARQ.
2. **Merkle DAG Cache:** Mỗi node trong Workflow Canvas có `CacheKey = SHA256(NodeType + CanonicalConfig + ParentHashes)` để chỉ chạy lại các nhánh bị thay đổi.
3. **Zero-Trust Media Handling:** Kiểm tra Magic Bytes khi upload, chống Path Traversal và xác thực đầu ra bằng `ffprobe`.
