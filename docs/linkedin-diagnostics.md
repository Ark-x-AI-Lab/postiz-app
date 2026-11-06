# Safe LinkedIn OAuth diagnostics

Status: local implementation and offline harness complete; production build and live checks remain with the integrating owner. No provider calls, posting, commit, push or deployment were performed for this change. Source baseline: `4644ab1d`, branch `deploy/arkx`.

## Purpose and findings

OAuth is the authorization flow used when adding a LinkedIn channel. The browser requests a connection link, LinkedIn returns to the browser callback, and the backend exchanges the authorization code, checks scopes, reads the member profile and optionally lists Pages. Page selection then reads Page details and saves the channel.

- **CONFIRMED from source:** the callback previously logged whole authentication exceptions. Page-fetch exceptions were logged and then returned as an empty list. This made a provider rejection difficult to distinguish from no eligible Pages.
- **CONFIRMED by synthetic harness:** the added diagnostics distinguish token exchange, scope check, profile reads, Page listing and Page details. The same random reference reaches backend logs, safe browser console events, API diagnostic metadata and the existing error display.
- **GAP:** the actual live LinkedIn failure is not established. No scopes, redirect addresses, authorization parameters, request headers, token payloads, retry rules or provider permissions were changed to guess a fix.

## Runtime wiring for the integrating owner

Add these names to the application's explicit runtime environment allowlist. This patch does not edit deployment files or enable diagnostics anywhere.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LINKEDIN_OAUTH_DIAGNOSTICS` | off | Only the exact value `true` enables structured records and diagnostic response metadata. No browser build flag is needed. |
| `LINKEDIN_OAUTH_DIAGNOSTICS_TTL_SECONDS` | `3600` | Lifetime of private correlation lookups. Invalid or nonpositive values use the default. This does not alter OAuth state expiry. |
| `LINKEDIN_OAUTH_DIAGNOSTICS_STORE_TIMEOUT_MS` | `250` | Maximum wait per diagnostic Redis operation, in milliseconds. Invalid or nonpositive values use the default. Diagnostic storage errors/timeouts never reject a connection. |

After the migration and authorized deployment, enable the flag for a controlled reproduction. Follow one normal LinkedIn connect attempt and match its `correlationId` in browser and backend records. Disable the flag after collection. Provider diagnosis and any scheduled publishing test are separate owner-controlled steps.

## Log contract and correlation

Each diagnostic is rebuilt from a closed schema. Backend logs use `console.info` JSON records. Browser code accepts only matching safe metadata from API responses and rebuilds it again before logging. The same metadata is visible on those application network responses; there is no generic network body logger.

Allowed fields:

- `event`: always `oauth_diagnostic`.
- `provider`: `linkedin` or `linkedin-page`.
- `correlationId`: a server-generated random version-4 universally unique identifier (UUID), independent of credentials and OAuth state.
- `stage`: `connect`, `callback`, `token_exchange`, `scope_check`, `userinfo`, `member_profile`, `page_list`, `page_details`, `page_save`.
- `outcome`: `started`, `success`, `failure`.
- Optional `status`: numeric HTTP status from 100 through 599; `durationMs`: nonnegative integer elapsed milliseconds.
- Optional `error`: `http_error`, `network_error`, `invalid_json`, `scope_error`, `empty_result`, `operation_error`, `correlation_storage_error`, `oauth_error`.
- Optional `providerCode`: only `access_denied`, `invalid_request`, `invalid_client`, `invalid_grant`, `invalid_scope`, `unauthorized_client`, `unsupported_grant_type`, `unsupported_response_type`, `server_error`, `temporarily_unavailable`. These are exact allowed token-response error codes; arbitrary error text is discarded.

No record includes raw headers, addresses or query strings, authorization codes, OAuth state, tokens, secrets, member/Page identifiers, names, pictures, raw provider responses, exception messages, stacks or causes. Existing functional payloads are still used internally by the connector; they are never passed to the diagnostic serializer. LinkedIn raw callback exception logging is suppressed even when the flag is off.

Correlation uses expiring Redis lookups under `oauth-diagnostic:<provider>:<private lookup key>`. Connect/callback uses existing OAuth state. Later dashboard Page listing uses an integration-level lookup, which intentionally represents the latest callback for that integration. Page saving uses a separate attempt binding: `oauth-diagnostic:<provider>:page-attempt:<integrationId>:<correlationId>`. Each callback registers its own binding; another callback updating the dashboard lookup cannot overwrite that binding. Values contain only the random reference. Private keys are not logged or returned.

The browser stores the callback's validated reference in its Page-picker state and echoes only that reference in the Page-save `oauthDiagnosticId` field. After the existing scoped integration lookup and Page-selection checks, the server accepts the reference for diagnostics only if the exact integration-plus-reference binding exists and its stored value matches. Missing, malformed, unknown, expired, cross-integration or poisoned bindings produce a fresh diagnostic reference. Page save never falls back to the dashboard mapping. Neither the reference nor its binding grants access or replaces organization, integration, Page-selection or OAuth-state checks. Lookup failures/timeouts remain bounded and do not prevent an otherwise authorized save. Without a valid lookup, histories cannot be joined reliably.

Nested failures retain the first failing stage, so a token rejection does not get relabeled as a later scope or callback failure. Successful network records retain status and duration. A healthy Page response with no eligible Pages is labeled `empty_result`; a rejected Page request retains `http_error` and its status. The optional dashboard hook applies only to LinkedIn Page `companies` calls.

## Browser behavior

The existing connect and reconnect actions report safe metadata. The callback and Page-save flows show a failed stage and reference in the existing error screen. Page-save failures leave the picker so the error screen can actually render. A callback with an already-saved intermediate channel can report a later Page-list failure; diagnostics do not roll that channel back or make additional provider calls. The existing precondition response path remains intact.

The callback uses a cloned response for its fallback check, avoiding consumption of the body before safe diagnostic handling. There are no layout, style or new-screen changes.

## Changed paths

- [Closed log schema and browser reporter](../libraries/helpers/src/utils/oauth.diagnostics.ts).
- [Diagnostic adapter, context, storage bounds and network stages](../libraries/nestjs-libraries/src/integrations/linkedin.oauth.diagnostics.ts).
- [Optional provider hook](../libraries/nestjs-libraries/src/integrations/social/social.integrations.interface.ts).
- [Personal LinkedIn connector](../libraries/nestjs-libraries/src/integrations/social/linkedin.provider.ts).
- [LinkedIn Page connector](../libraries/nestjs-libraries/src/integrations/social/linkedin.page.provider.ts).
- [Connect and dashboard controller](../apps/backend/src/api/routes/integrations.controller.ts).
- [Callback controller](../apps/backend/src/api/routes/no.auth.integrations.controller.ts).
- [Page-save service](../libraries/nestjs-libraries/src/database/prisma/integrations/integration.service.ts).
- [Browser connect action](../apps/frontend/src/components/launches/add.provider.component.tsx).
- [Browser reconnect action](../apps/frontend/src/components/launches/launches.component.tsx).
- [Browser callback and Page-save error handling](../apps/frontend/src/components/launches/continue.integration.tsx).
- [Offline safety harness](../libraries/nestjs-libraries/src/integrations/linkedin.oauth.diagnostics.test.cjs).
- This document.

Deployment, Docker, workflow, migration, PRODUCT and other project documents are outside this patch. The pre-existing `deploy/arkx/__pycache__/` directory is preserved. No whole-file formatter was run; edits are limited to diagnostic helpers and affected methods/imports.

## Local evidence and acceptance

- [x] Inspect applicable rules, product, architecture, migration plan and actual OAuth/Page path before editing.
- [x] Add opt-in correlation and structured allowlisted frontend/backend/network evidence.
- [x] Keep state validation, scope checks and provider requests in place. A synthetic personal flow compares flag-off and flag-on outgoing requests for equality.
- [x] Test safe failure text in the actual callback component through an isolated React-hook harness. Test successful Page selection, failed Page save, HTTP 400 body reuse and precondition handling.
- [x] Test allowed information and blocked sensitive fields, default-off behavior, exact flag values, token rejection, missing scopes, profile reads, Page list/save, provider error codes, invalid JSON, network exceptions, storage errors/timeouts, separate simultaneous contexts and poisoned stored references.
- [x] Focused offline compilation of all changed TypeScript/React files and `git diff --check`.
- [ ] Full production builds using repository-pinned dependencies and runtime.
- [ ] Live browser, provider and responsive-screen checks after main integrates and deploys.

Harness after the review-1 correction: **21 passed, 0 failed**, using Node `26.7.0` and pre-existing global TypeScript `5.9.3`. It compiles and runs actual controller, connector, Page-save service and callback component source with substituted external modules, synthetic responses and blocked outbound fetches. It is not a Nest/Next application boot or a full type check. The two diagnostic helper files additionally passed a strict no-emit TypeScript check against existing Node types and a minimal Nest exception declaration; that check does not establish whole-app compatibility. Main reported that the previous 17-test baseline passed under pinned Node `22.23.4` and TypeScript `5.5.4`; that result predates this correction and does not cover the four new regressions.

In an installed checkout, run:

```sh
pnpm exec node --test libraries/nestjs-libraries/src/integrations/linkedin.oauth.diagnostics.test.cjs
pnpm --filter ./apps/backend run build
pnpm --filter ./apps/frontend run build
git diff --check
```

The exact offline harness command used here was:

```sh
POSTIZ_TYPESCRIPT_PATH=/opt/homebrew/lib/node_modules/typescript node --test libraries/nestjs-libraries/src/integrations/linkedin.oauth.diagnostics.test.cjs
```

Both production build commands were attempted and exited 1: backend could not find `cross-env`; frontend could not find `next`. This checkout has no `node_modules`. No dependencies were installed, in line with the migration plan. The repository pins Node `22.23.4` and TypeScript `5.5.4`; main must run its normal build gate in that environment. **This patch is not claimed full build-green or live-verified.**

## Limits and next proof

Provider authorization failures before the callback handler, request-validation failures before controller entry, and browser transport failures with no API response may have no callback diagnostic metadata. No generic request/response capture was added to cover these gaps. Scheduled publishing, refresh workflows, analytics and media requests are outside diagnostic instrumentation.

The integrating owner should review this frozen diff, wire the three runtime names, build with the pinned environment, complete migration/cutover checks, then reproduce LinkedIn authorization on the deployed commit. That evidence is needed before naming the live cause or changing permissions or redirect behavior. Existing deployment and PRODUCT documentation remain owned by main.

Process finding: testing the actual callback component exposed two relevant failure-path issues: the initial HTTP 400 check consumed its response before later handling, and a Page-save error could remain hidden behind the still-active picker. The focused changes preserve the successful paths while making the diagnostic failure visible.

## Review 1 correction: shared integration rows

The confirmed P2 finding was that two callbacks can share one intermediate integration row. Callback B then overwrote the integration-level diagnostic lookup, causing Page save A to inherit B's reference. The correction is the attempt-specific, server-validated binding described above; the dashboard mapping is preserved separately. No new runtime variables, authorization decisions or publishing changes were added.

The four added regressions cover:

1. Two actual callbacks returning the same integration, two actual callback-component Page pickers echoing their own references, and overlapping Page saves retaining their respective references despite the dashboard lookup pointing at B.
2. Missing, malformed, unknown, cross-integration, expired and poisoned bindings, including a stored UUID that differs from the echoed UUID: authorized saves continue under fresh references.
3. A first-tab Page-details network failure retaining A's safe reference after B's callback, followed by B's successful save under B's reference.
4. A valid binding granting no access to another organization/integration or an already-completed Page-selection row; flag-off saves make no diagnostic storage calls or logs.

Before the correction, the new shared-row/picker and invalid-binding regression tests failed. After the correction, all 21 tests and `git diff --check` pass. This correction modifies only the diagnostic adapter, callback controller, Page-save service, callback component, focused harness and this document. Files are frozen for main's review 2; the three-round review cap remains in force. The updated pinned-runtime harness and normal production build remain main's integration gates.
