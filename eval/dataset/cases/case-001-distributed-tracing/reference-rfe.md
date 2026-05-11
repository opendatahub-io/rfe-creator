---
rfe_id: RHAIRFE-2229
title: "End-to-end distributed tracing and trace propagation across all RH AI inference and agentic path components"
priority: Critical
size: Large
status: Closed
---

# Problem Statement

RHOAI cannot produce an end-to-end distributed trace for an inference request today. A single user request traverses multiple components -- Envoy gateway, Authorino (auth), Limitador (rate limiting), llm-d EPP (scheduling), KServe (serving), and vLLM (inference) -- but trace context breaks at nearly every boundary because most of these components do not emit spans or propagate the W3C `traceparent` header.

The current state per component:

| Component | Emits spans? | Propagates traceparent? | Status |
| --- | --- | --- | --- |
| Envoy / Istio gateway | Yes (native) | Yes | Working, but spans not correlated with downstream RHOAI components |
| Authorino | No | No | No OTel instrumentation; trace chain breaks here |
| Limitador | No | No | No OTel instrumentation; trace chain breaks here |
| llm-d EPP | In progress | In progress | RHAISTRAT-1198 (DP, releasing); RHAISTRAT-1368 (GA, 3.5 candidate) |
| KServe | Partial | Partial | Some instrumentation exists upstream, not validated in RHOAI |
| vLLM | No | No | RHAIRFE-363 filed; not yet delivered |
| fms-guardrails | Unknown | Unknown | Tracing status not confirmed |
| MCP Gateway / Kagenti | No | No | Agentic path; no tracing instrumentation yet |

The llm-d distributed tracing work (RHAISTRAT-1198/1368) is a critical step forward -- it covers the Gateway to Scheduler to Model Worker path within llm-d. However, the trace chain still breaks **outside** llm-d: at the auth layer (Authorino), rate limiting (Limitador), guardrails (fms-guardrails), and the agentic path (MCP Gateway, Kagenti). This RFE addresses the full end-to-end gap across **all** components, complementing the llm-d-scoped tracing work.

The consequences for users:

* **"What path did my request take?"** -- Cannot answer. Even with llm-d tracing, the auth and rate-limiting stages are invisible.
* **"Why is this call slow?"** -- Cannot isolate whether latency is in the gateway, auth, rate limiting, scheduling, or the model itself without spans from each component.
* **"Did guardrails fire on this request?"** -- Cannot link a guardrails evaluation to the specific inference request that triggered it.
* **"What tool calls did my agent make?"** -- For agentic workloads, there is no trace connecting the agent orchestrator to its tool calls through the MCP Gateway.

Beyond span emission, there is no agreed-upon set of **span attributes** across all RHOAI components. The llm-d tracing work defines `model_id`, `request_id`, and `token_count` for its spans -- the same attribute vocabulary should extend to all components in the request path so that traces are filterable and correlatable platform-wide.

# Business Alignment

1. **Business Value**: Distributed tracing is the primary tool for debugging latency, errors, and request routing in microservice architectures. The RHOAI inference path is a multi-component pipeline -- exactly the architecture where tracing provides the most value. Without it, customers cannot operate inference workloads at production scale because they have no way to diagnose per-request failures or performance degradation. This gap is visible in competitive evaluations: managed AI platforms (AWS Bedrock, Azure AI) surface request traces natively. RHOAI currently cannot for the full path.

1. **Red Hat AI Outcome Alignment**: Directly supports "Production-ready AI platform." Tracing is prerequisite infrastructure for SLO monitoring, latency budgeting, cost-per-request attribution, and incident response. It is also foundational for the telemetry contract (RHAIRFE-2227) -- the contract specifies trace requirements, and this RFE delivers the implementation across all components beyond what llm-d tracing covers.

# Proposed Solution / Rationale

Instrument all components in the RHOAI inference and agentic request paths to emit OpenTelemetry-compatible spans and propagate W3C trace context, so that a single inference request produces a complete, connected distributed trace from gateway to GPU -- extending the llm-d tracing foundation to cover auth, rate limiting, guardrails, and the agentic path.

## 1. Span emission for all P0 components

Each P0 component in the inference path should emit spans via OTLP (to the OTel Collector deployed by the RHOAI monitoring stack). At minimum, each component should produce a span that captures:

* Entry time and exit time (latency)
* Success/failure status
* Standard span attributes (see below)

Components that already have upstream OTel instrumentation (Envoy, KServe) should have that instrumentation validated and enabled in the RHOAI deployment. Components with in-progress instrumentation (llm-d EPP via RHAISTRAT-1198/1368) should be connected into the broader trace chain. Components without instrumentation (Authorino, Limitador, vLLM, fms-guardrails) need it added.

## 2. Trace propagation (W3C Trace Context)

Every component in the request path must participate in trace propagation:

1. Read the incoming `traceparent` header
2. Create a child span under that trace context (if OTel-instrumented) or passthrough the header unchanged
3. Forward the `traceparent` header to all downstream calls

This ensures that spans from different components are connected into a single trace. Today, even components that emit spans (like Envoy) produce disconnected traces because downstream components do not read or forward the `traceparent`.

## 3. Standard span attributes

Define and document a minimum set of span attributes that all RHOAI components should include, aligned with the attributes already used by llm-d tracing:

* `service.name` -- identifying the component
* `model.id` -- the model being served (where applicable)
* `request.id` -- a unique request identifier
* `token_count.prompt` / `token_count.generation` -- token counts (where applicable, e.g., vLLM)
* `user.id` -- the authenticated user (from Authorino, where available)

These attributes enable users to filter traces by model, user, or request, and to aggregate token consumption and latency across traces.

## 4. Agentic path (P1)

For agentic workloads, trace propagation should extend through:

* The agent orchestrator (Kagenti) -- emitting spans for each reasoning step and tool call decision
* The MCP Gateway -- emitting spans for each tool invocation, including auth (Authorino) and rate limiting (Limitador) at the tool level
* Tool endpoints -- propagating `traceparent` into tool execution

This allows users to answer "What tool calls did my agent make, how long did each take, and what was the total token cost of this agent task?"

## 5. Collection and storage

Spans should flow through the OTel Collector (deployed by the RHOAI monitoring stack) to Tempo for storage and query. This path should already be functional if the monitoring stack and dependency operators (COO, OTel Operator, Tempo Operator) are deployed correctly (see RHAIRFE-1773, RHAIRFE-2166).

## 6. Testing

Automated tests should validate:

* Each P0 component emits spans when processing a request
* Spans include the required attributes
* A request traversing the full inference path (client to gateway to auth to rate limiter to scheduler to model) produces a single connected trace with spans from every component
* Trace propagation is not broken: each span's parent ID matches the previous component's span ID

# Acceptance Criteria

1. All P0 inference-path components (vLLM, KServe, Envoy, Authorino, Limitador, llm-d EPP, fms-guardrails) emit OpenTelemetry-compatible spans via OTLP when processing inference requests.
2. All P0 components propagate W3C Trace Context (`traceparent` header) -- a request traversing the full inference path produces a single connected distributed trace with no gaps.
3. A documented set of span attributes is defined and emitted by all P0 components, including at minimum `service.name`, `model.id`, and `request.id` -- consistent with the attributes used by llm-d tracing (RHAISTRAT-1198/1368).
4. vLLM spans include token-level attributes (`token_count.prompt`, `token_count.generation`) enabling per-request token accounting.
5. A user can query Tempo and see a complete trace for an inference request showing latency contribution from each component in the path.
6. Automated end-to-end tests validate that trace propagation is unbroken across the full inference path and that required span attributes are present.
7. The tracing implementation aligns with the telemetry contract defined in RHAIRFE-2227.

# Affected Customers / Partners & Scope

**Broad impact.** Every RHOAI customer running inference or agentic workloads needs distributed tracing to operate in production:

* **ML engineers** need to debug slow or failed inference requests by isolating which component introduced latency or errors.
* **Platform operators** need to identify bottlenecks (e.g., auth overhead, rate limiter queuing, KV cache pressure) across the inference path.
* **Finance / FinOps teams** need per-request cost attribution, which requires token counts and GPU-time spans correlated in a single trace.
* **Security / compliance teams** need audit trails showing which user's request was processed by which model, through which auth path -- a connected trace provides this.

# Alternative Approaches Considered

1. **Rely solely on llm-d tracing (RHAISTRAT-1198/1368)**: llm-d tracing covers Gateway to Scheduler to Model Worker, which is critical. But it does not cover Authorino, Limitador, fms-guardrails, or the agentic path. Users still cannot see the full request path or isolate latency introduced by auth or rate limiting. llm-d tracing is necessary but not sufficient.
2. **Use MLflow tracing instead of OTel for the full path**: MLflow tracing is valuable for application-level agent observability (reasoning steps, tool calls), but it does not extend to infrastructure components like Authorino, Limitador, or Envoy. OTel is the appropriate instrumentation standard for the system-level inference path. The two are complementary: OTel for the platform path, MLflow for the application path, with OTLP compatibility allowing both to flow to the same backend.
3. **Rely on Envoy/Istio service mesh tracing**: Envoy produces gateway-level traces, but these only show network hops -- not application-level processing within each component. Service mesh tracing is a starting point but does not replace component-level OTel instrumentation.

# Reference Documents / Links

* RHAISTRAT-1198 -- DP: End-to-End Distributed Tracing for llm-d (Release Pending, 3.4)
* RHAISTRAT-1368 -- GA: End-to-End Distributed Tracing for llm-d (New, 3.5 candidate)
* RHAIRFE-363 -- vLLM OTel support (component-specific, subset of this RFE)
* RHAIRFE-2227 -- Telemetry contract for RHOAI components (defines trace format requirements)
* RHAIRFE-2228 -- End-to-end logging pipeline (sibling RFE for the logging signal)
* RHAIRFE-1773 -- Dependency operators installed by default (COO, OTel, Tempo)
