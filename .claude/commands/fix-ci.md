---
description: Reproduce the CI jobs locally and fix what fails
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Bash(scripts/doctor.sh:*), Read, Grep, Edit
---
Mirror `.github/workflows/ci.yml` locally, in order:
1. `scripts/test.sh orchestrator` (CI sets `REQUIRE_FFMPEG=1`: a missing FFmpeg must fail, not skip)
2. `scripts/test.sh media`
3. `scripts/test.sh web`
4. `scripts/lint.sh`

For each failure find the root cause (read the code and the test), fix it without weakening checks, and re-run until green. If the failure is environmental (missing ffmpeg, no network, missing deps), say so precisely instead of editing code. Report each job's final status.
