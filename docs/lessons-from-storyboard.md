# Lessons from Storyboard Compliance Work

Captured 2026-05-11 after a session that took the `media_buy_seller` AdCP storyboard from 0/11 to a full clean run on both MCP and A2A transports (32 passed / 0 failed each).

These are the patterns that wasted the most time. Recording so future debugging sessions don't repeat them.

## 1. Storyboard is a multi-layer signal

A single failing step can mean a bug at any of:

- AdCP spec definition
- JS SDK (`@adcp/sdk`) runner code
- JS SDK bundled compliance cache (lags spec)
- Python SDK (`adcp-client-python`) framework
- Seller code (our `_impl` functions)
- Seller schema extensions (Pydantic models with `extra="forbid"`)
- Seller tenant config (manual-approval gates, domain blocklists)
- Infrastructure (PgBouncer hard_limit, Fly load balancer, idempotency cache state)

**When a storyboard step fails, the right question is "which layer?", not "what's the bug?".**

Diagnostic order that worked for us:

1. Read the storyboard YAML — what does it assert? Capture exact `sample_request` and `validations`.
2. Run the scenario in **isolation** via `npx storyboard run <agent> <scenario_id>` — does it still fail?
3. If pass-in-isolation but fail-in-suite: cross-scenario state pollution OR JSON reporter aggregation artifact.
4. If fail-in-isolation: probe the seller directly with `curl` using the storyboard's exact payload shape.
5. If direct probe works but storyboard doesn't: dump `--json` output; compare actual wire bytes (request and response) to your probe.
6. If direct probe also fails: now you have a real seller bug. Look at code paths.

## 2. Direct-probe-pass ≠ storyboard-pass

We chased multiple phantom bugs because hand-crafted `curl` probes returned the correct response while the storyboard reported failure on the same operation. Three reasons this happens:

1. **JSON reporter aggregation:** `storyboard run --json` serializes each scenario under multiple parent groups (capability track, scenario block). The same scenario object appears multiple times in the output. We initially read this as "the runner is running each scenario twice" and filed an upstream PR for what turned out to be a non-issue. Always check `tested_at` timestamps before assuming duplicate execution.
2. **Cache state:** seller-side idempotency cache holding stale responses from a prior run. Symptom: storyboard sees stale data, fresh `curl` with a new idempotency_key works correctly.
3. **Runner enrichment:** the storyboard runner injects fields (account, idempotency_key UUIDs, brand-invariant defaults) that hand-crafted curls don't replicate. These can route the seller through different code paths.

**Lesson:** when probe ≠ storyboard, the FIRST diagnostic step is `--json` output, not code spelunking.

## 3. Cross-transport contract tests must hit real ASGI envelopes

`tests/unit/test_delegate_typed_error_translation.py` (PR #322) asserted on the in-process `AdcpError` raised by the delegate, parametrized across `{mcp, a2a}`. It passed — but the A2A wire envelope was still wrong in production because the test never exercised the actual transport layer.

PR #330 fixed this with a real cross-transport pattern:

```python
import httpx
from core.main import build_app

@pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A])
async def test_X(transport):
    app = build_app(...)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        resp = await client.post(transport.url, json=...)
        adcp_error = transport.extract_error(resp)
        assert adcp_error["code"] == "EXPECTED_CODE"
```

**Use this pattern for any error-translation, schema-validation, or response-shape test.** In-process tests catch logic bugs; ASGI tests catch wire-format bugs. Both layers matter.

See `tests/integration/test_delegate_wire_envelope_cross_transport.py` (PR #330) for the working version.

## 4. Seller config can make working code unreachable

PR #321's `NOT_CANCELLABLE` guard was correct. Tests passed. Code reviewer approved. It deployed and storyboard still showed the failure.

Root cause: a manual-approval gate at `media_buy_update.py:~555` intercepted *before* the cancel branch ran. The seller returned 200 with `workflow_step_id` (approval queued), buy was never actually canceled, re-cancel didn't trigger NOT_CANCELLABLE.

**When adding pre-validation, verify the call site is on the request's actual path.** Search for early `return` statements that could intercept. Run a probe end-to-end and inspect the response shape — `workflow_step_id` in a response that should be synchronous is a tell.

## 5. Idempotency cache should be schema-aware (or aggressively cleared on deploy)

Response-shape changes (PR #246, #250, #313 all changed wire envelopes for create_media_buy) silently broke storyboard runs against post-fix deploys. The seller's idempotency cache held pre-fix responses keyed by `(scope_key, idempotency_key)`; when the storyboard re-sent a matching key, the seller correctly replayed the cached (now stale-shape) response.

We worked around this by manually clearing `adcp_idempotency` on each deploy. That's not a sustainable answer.

**Longer-term:** sellers should either (a) version-key the cache so deploys invalidate it automatically, or (b) shorten TTLs aggressively for sandbox traffic.

See [adcp#4357](https://github.com/adcontextprotocol/adcp/issues/4357) for the spec-level proposal.

## What to do when stuck

1. **3 attempts rule:** if a fix isn't taking, change tack. The 4th attempt at the same code path is wasted time.
2. **Inspect `--json` before reading code.** The wire bytes tell you whether the bug is in your code or in your interpretation of the report.
3. **Check the deploy SHA.** Multiple times we chased "PR X isn't working" only to find the deploy was on an older revision.
4. **Run the scenario in isolation.** Cross-scenario pollution shows up as inconsistent failures; isolation reveals the actual contract being violated.
5. **Read the storyboard YAML.** Don't trust the failure message in the summary — read what the scenario actually asserts. Failure descriptions are often the assertion narrative, not the actual error.

## Related references

- Wire-envelope cross-transport test pattern: `tests/integration/test_delegate_wire_envelope_cross_transport.py` (PR #330)
- Translator pattern for typed errors: `core/platforms/_delegate.py` (`@translate_adcp_errors` decorator)
- Storyboard validation script: `scripts/storyboard-check.sh`
- Upstream tracking: adcontextprotocol/adcp issues 4344, 4357, 4370, 4371, 4372; adcp-client 1657, 1658, 1674; adcp-client-python 612, 652, 662
