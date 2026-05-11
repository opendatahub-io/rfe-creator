---
rfe_id: RHAIRFE-1560
title: "Provide Identity for Agentic Workloads"
priority: Major
size: Large
status: Closed
---

## Problem Statement

Deployed agents on RHOAI currently inherit the deploying user's credentials and have no independent identity. This means agents cannot be individually audited, access-controlled, or isolated from each other — a critical gap for enterprise customers in regulated industries where each agent must have scoped permissions and traceable actions.

## Affected Customers

* **Volvo** — "Critical need to manage agent identity where AI agents act on behalf of users with specific, scoped permissions" — exploring delegation semantics and Spiffe/Spire
* **BBVA** — Kagenti with sidecar injection for identity and observability resonated strongly
* **BofA** — "Validated/secure MCP servers with air-gapped proxying, enterprise auth, workload isolation"
* **Phoenix Technologies** — "OpenShift Sandboxed Containers for enhanced security or isolation for Agentic"
* **NTT Data** — ABE technology for "treating agents in a zero trust environment like humans"

## Business Justification

Five named customers confirm that agent identity and security isolation are prerequisites for production deployment. Volvo explicitly requires delegation semantics for agents acting on behalf of users. BofA requires workload isolation in air-gapped environments. Without per-agent identity, compliance and audit requirements cannot be met.

## Acceptance Criteria

* **[MUST]** Each deployed agent gets its own service identity (not inheriting the deploying user's credentials)
* **[MUST]** Identity delegation
* **[MUST]** Ingress/Egress Policies for governing agent-to-agent access (L4 and L7)
* **[MUST]** TLS enforcement for agent-to-service communication
* **[SHOULD]** mTLs for Agent client identites (SPIFFE/SAs/…) for internal identities
* **[COULD]** Integration with external identity providers (Spiffe/Spire, OAuth token exchange)

### Business Justification

#### Customer Evidence

* **BMO**: "Agent identity and access control is the top concern. Simon immediately latched onto SPIFFE/Kagenti as the right pattern for managing agent access control so that agents operate 'within our control rather than doing its own.'"
* **BMO**: "Scoped, ephemeral agent identities with token exchange so each agent only gets the audience it needs for specific downstream components, not a master key to everything."
* **Garanti BBVA**: "Agent identity propagation is unsolved. When a customer logs into the mobile app, that identity must follow through to the agent and onward to data sources."
* **Goldman Sachs**: "Goldman has implemented SPIFFE (not SPIRE) for workload identity. EU AI Act Article 14 requires full traceability of agent decisions... end-to-end tracing that ties agent identity (SPIFFE), tool calls, sub-agent delegation into a single audit trail."
* **Fix (Siemens subsidiary)**: "Lack of Identity Provider support for 'Super-Scoping' of tokens for agent-to-tool communication."
* **Phoenix Technologies**: "The need to implement identity-based access control and ensure data isolation in a multi-tenant, shared, but stateless environment."

#### Strategic Rationale

* Agent identity is the #1 security concern across financial services (BMO, Goldman, BBVA) and manufacturing (Fix/Siemens). This is not aspirational; Goldman already implemented SPIFFE, and BMO is evaluating Kagenti. Regulatory pressure (EU AI Act) makes this mandatory.

#### Impact

* Without platform-provided agent identity, every customer must build custom SPIFFE/token-exchange integrations, creating inconsistent security postures and blocking regulated industries from deploying agents.
