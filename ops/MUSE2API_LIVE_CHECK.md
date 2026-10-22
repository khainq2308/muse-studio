# Live check against a real muse2api gateway

Use this runbook to turn the `mock-only` rows of
[docs/MUSE2API_CAPABILITY_MATRIX.md](../docs/MUSE2API_CAPABILITY_MATRIX.md) into dated
evidence from a real muse.ai account. CI only exercises the gateway's mock driver
(job `muse2api-contract`), which proves the request/response contract but not real media.

The operator runs every step that touches credentials. Never paste muse.ai cookies,
gateway keys or provider tokens into chats, tickets, commits or logs.

## 1. Start muse2api with the browser driver

In the muse2api checkout (tested contract revision `949f326`; check `git log -1` and
re-run the contract tests if you upgrade):

```bash
cd ../muse2api
source .venv/bin/activate
# In muse2api's own .env (never commit it):
#   MUSE2API_DRIVER=browser
#   MUSE2API_HEADLESS=true      # false to watch the browser while debugging
# Google Chrome at /Applications/Google Chrome.app is detected automatically.
python -m muse2api               # http://127.0.0.1:18610
```

If `MUSE2API_API_KEY` is empty, the gateway writes a generated key to `data/api_key`.

## 2. Import a muse.ai account

Export the cookies `hatch_sess`, `hatch_gw`, `hatch_vml` and `hatch_native_auth_device`
from a browser logged in to muse.ai, then import them through the gateway Admin API
from your own terminal (`POST /admin/accounts`, see the muse2api README). Confirm with
`GET /readyz` that `accounts.available` is at least 1.

## 3. Run the contract tests against the real driver

From this repository (keys are read from your shell, not typed into files):

```bash
cd apps/orchestrator
export MUSE2API_CONTRACT_URL=http://127.0.0.1:18610
export MUSE2API_CONTRACT_KEY="$(cat ../../../muse2api/data/api_key)"   # or your configured key
MUSE2API_CONTRACT_REQUIRE=1 MUSE2API_CONTRACT_REQUIRE_MP4=1 MUSE2API_CONTRACT_TIMEOUT=900 \
  REQUIRE_FFMPEG=1 PYTHONPATH=../media-engine \
  .venv/bin/python -m pytest -q -rs tests/test_muse2api_contract.py
```

`MUSE2API_CONTRACT_REQUIRE_MP4=1` additionally requires the downloaded video to decode
with ffprobe. One run creates one paid video and one image on the account.

## 4. Record the evidence

Only after a passing run, update the matching matrix rows to `verified`, with the date,
the muse2api commit, the driver (`browser`) and what was observed. Visual first-frame
conditioning still needs a human to compare the video's first frame with the input;
keep that row `unverified` until someone has checked it.
