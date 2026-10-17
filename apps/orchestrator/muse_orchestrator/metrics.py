from __future__ import annotations

from collections import Counter
from threading import Lock


class RequestMetrics:
    """Small, dependency-free Prometheus text registry for HTTP request metrics."""

    BUCKETS_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: Counter[tuple[str, str, str]] = Counter()
        self._duration_counts: Counter[tuple[str, str]] = Counter()
        self._duration_sums: Counter[tuple[str, str]] = Counter()
        # Buckets are stored cumulatively, as required by Prometheus histograms.
        self._duration_buckets: Counter[tuple[str, str, str]] = Counter()

    def observe(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        method = method.upper()
        route = route or "/__unmatched__"
        duration = max(0.0, float(duration_seconds))
        key = (method, route)

        with self._lock:
            self._requests[(method, route, str(status_code))] += 1
            self._duration_counts[key] += 1
            self._duration_sums[key] += duration
            for bucket in self.BUCKETS_SECONDS:
                if duration <= bucket:
                    self._duration_buckets[(method, route, self._format_bucket(bucket))] += 1

    def render_prometheus(self) -> str:
        with self._lock:
            requests = dict(self._requests)
            duration_counts = dict(self._duration_counts)
            duration_sums = dict(self._duration_sums)
            duration_buckets = dict(self._duration_buckets)

        lines = [
            "# HELP muse_http_requests_total Total HTTP requests handled by the orchestrator.",
            "# TYPE muse_http_requests_total counter",
        ]
        for (method, route, status_code), count in sorted(requests.items()):
            labels = self._labels(method=method, route=route, status_code=status_code)
            lines.append(f"muse_http_requests_total{labels} {count}")

        lines.extend([
            "# HELP muse_http_request_duration_seconds HTTP request duration in seconds.",
            "# TYPE muse_http_request_duration_seconds histogram",
        ])
        for method, route in sorted(duration_counts):
            labels_base = {"method": method, "route": route}
            for bucket in self.BUCKETS_SECONDS:
                bucket_label = self._format_bucket(bucket)
                count = duration_buckets.get((method, route, bucket_label), 0)
                labels = self._labels(**labels_base, le=bucket_label)
                lines.append(f"muse_http_request_duration_seconds_bucket{labels} {count}")

            labels = self._labels(**labels_base, le="+Inf")
            lines.append(
                f"muse_http_request_duration_seconds_bucket{labels} "
                f"{duration_counts[(method, route)]}"
            )
            base_labels = self._labels(**labels_base)
            lines.append(
                f"muse_http_request_duration_seconds_sum{base_labels} "
                f"{duration_sums.get((method, route), 0.0):.9f}"
            )
            lines.append(
                f"muse_http_request_duration_seconds_count{base_labels} "
                f"{duration_counts[(method, route)]}"
            )

        return "\n".join(lines) + "\n"

    def clear(self) -> None:
        """Clear process-local samples; intended for isolated unit tests."""
        with self._lock:
            self._requests.clear()
            self._duration_counts.clear()
            self._duration_sums.clear()
            self._duration_buckets.clear()

    @staticmethod
    def _format_bucket(value: float) -> str:
        return f"{value:g}"

    @staticmethod
    def _labels(**values: str) -> str:
        def escape(value: str) -> str:
            return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

        rendered = ",".join(
            f'{key}="{escape(str(value))}"' for key, value in sorted(values.items())
        )
        return "{" + rendered + "}"


request_metrics = RequestMetrics()
