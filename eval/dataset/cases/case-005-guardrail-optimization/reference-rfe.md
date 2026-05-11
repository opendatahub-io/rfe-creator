---
rfe_id: RHAIRFE-2072
title: "Constitution-Driven Guardrail Prompt and Judge Model Optimization"
priority: Major
size: Large
status: Closed
---

#### Problem Statement

Organizations deploying guardrail systems (NeMo Guardrails, Guardrails Orchestrator) and using LLM judge models for evaluation on RHOAI have no systematic way to measure whether these systems actually enforce their organizational safety policies, or to improve them when they fall short.

**Guardrail prompts are untested against organizational policies**: Guardrail system prompts are authored based on best-effort understanding of policy requirements, then deployed without systematic validation. A guardrail prompt configured to "block medical advice" may:

* Over-block legitimate clinical decision support queries (false positives that degrade user experience)
* Under-block off-label drug recommendations that violate organizational policy (false negatives that create compliance risk)
* Fail entirely on adversarial reformulations that a human reviewer would catch

Without evaluation against the organization's defined policies (their AI Safety Constitution — see RHAIRFE-1412), teams cannot distinguish between "our guardrails are deployed" and "our guardrails work."

**Judge models reflect generic standards, not organizational ones**: LLM judge models used in EvalHub score content against general safety criteria. When an organization's compliance requirements diverge from generic standards — which they always do in regulated industries — judge scores become unreliable signals. A judge that rates a response as "safe" by generic standards may miss a violation of the organization's specific policies, and vice versa.

Today, judge calibration via SIMBA/MemAlign (RHAIRFE-1689) aligns judges against human-labeled traces. But organizations need judges calibrated against their constitutional principles — systematically, not one trace at a time.

#### Business Alignment

**Strategic investment**: Agentic AI is a 2026 strategic priority. Enterprise agent deployments require not just guardrails that exist, but guardrails that demonstrably enforce organizational policies. The measure-diagnose-remediate cycle for AI safety systems is a core differentiator for RHOAI.

**Named customer and field evidence**:

* **Arrow AI Labs**: "Vendor-published safety scores are unreliable — vendors cherry-pick favorable benchmarks. Why don't we test that ourselves?" — directly requesting the ability to evaluate safety systems against their own criteria
* **Swiss Gov**: Needs to "evaluate guardrail models and track relevant metrics" — requires measurable, organization-specific evaluation of guardrail effectiveness
* **Garanti BBVA / BBVA Spain**: Data protection controls are a procurement prerequisite — needs evidence that guardrails enforce their data governance requirements
* **NTT Docomo**: "Current LLM guardrail systems insufficient" — needs to measure guardrail effectiveness against specific threat models
* FSI field data: model security is the #1 MaaS adoption blocker — "deployed" is not enough; customers need "proven effective"

**Causal chain**: Organizational constitution (RHAIRFE-1412) → evaluate guardrail prompts and judge models against constitution (this RFE) → identify coverage gaps and misalignments → optimize prompts and calibrate judges → re-evaluate to verify improvement → auditable evidence that safety systems enforce organizational policies.

#### Proposed Solution / Rationale

Enable organizations to systematically evaluate and optimize their guardrail system prompts and judge models against their AI Safety Constitution, with measurable improvement tracked through EvalHub.

**Evaluate — guardrail prompt effectiveness**:

* Generate test scenarios from constitutional principles (compliant and non-compliant inputs per principle)
* Run guardrail configurations against test scenarios and measure:
  - Coverage: which constitutional principles are effectively enforced
  - False negative rate: policy violations the guardrail misses
  - False positive rate: legitimate content the guardrail blocks
  - Adversarial robustness: whether reformulated inputs bypass the guardrail
* Report results as a constitution compliance scorecard per guardrail configuration

**Optimize — guardrail prompt improvement**:

* Identify high-impact improvement targets: principles with low coverage or high false positive/negative rates
* Generate prompt improvement recommendations based on failure patterns
* Support iterative refinement: modify guardrail prompts → re-evaluate → measure improvement
* Track optimization history with provenance to constitution version and evaluation runs

**Calibrate — judge model alignment to organizational policies**:

* Generate calibration datasets from constitutional principles (expected judgments for policy-compliant vs policy-violating content)
* Calibrate judge models against organizational standards using EvalHub alignment workflows (extends RHAIRFE-1689)
* Measure judge-constitution alignment: do judge scores correlate with constitutional compliance?
* Support per-principle calibration: a judge may need different sensitivity for "block medical advice" vs "require risk disclosure"

All evaluation and optimization runs are tracked in EvalHub with full provenance linking to constitution version, guardrail configuration version, and judge model version.

#### Acceptance Criteria

* [ ] Given an organizational constitution (RHAIRFE-1412), the system generates test scenarios from constitutional principles
* [ ] Guardrail configurations can be evaluated against generated test scenarios, producing a compliance scorecard with per-principle coverage, false positive rate, and false negative rate
* [ ] The compliance scorecard identifies the top coverage gaps and highest-impact improvement targets
* [ ] A practitioner can modify guardrail prompts, re-evaluate, and measure improvement against the same constitution
* [ ] Judge models can be calibrated against constitutional principles using generated calibration datasets through EvalHub alignment workflows
* [ ] Judge-constitution alignment is measurable: the system reports whether judge scores correlate with constitutional compliance
* [ ] All evaluation and optimization runs are tracked in EvalHub with provenance to constitution version and guardrail/judge configuration versions
* [ ] Product documentation covers the evaluate-optimize-re-evaluate workflow with at least one end-to-end example

#### Affected Customers/Partners & Scope

**Segment**: Enterprise AI safety leads, compliance teams, and ML engineers responsible for guardrail configuration and evaluation standards in regulated industries — financial services, healthcare, government, defense, telecom.

**Scope**: Depends on RHAIRFE-1412 (constitution definition) as input. Extends RHAIRFE-1689 (judge alignment) from generic human-label calibration to constitution-driven calibration. Complements RHAIRFE-1171 (auto policy generation) by adding the evaluation layer that measures whether generated policies are effective. Uses EvalHub as the execution and tracking platform.

#### What This Is NOT

* **Not constitution definition** — defining organizational safety policies is RHAIRFE-1412; this RFE consumes constitutions as input
* **Not guardrail runtime enforcement** — NeMo Guardrails and Guardrails Orchestrator remain the enforcement layer; this RFE evaluates and optimizes the prompts that drive them
* **Not automated policy generation** — generating guardrails configs from eval failure patterns is RHAIRFE-1171; this RFE evaluates whether configs enforce the constitution
* **Not generic benchmarking** — this is organization-specific evaluation driven by customer-defined constitutions, not Red Hat benchmark suites (RHAIRFE-1655)
* **Not autonomous remediation** — optimization produces recommendations and improved prompts; deployment decisions remain with the practitioner

#### Alternative Approaches Considered

**Manual guardrail testing**: Teams hand-write test cases and manually verify guardrail behavior. Inadequate: does not scale with policy complexity, not reproducible, no coverage measurement, no systematic optimization.

**Generic benchmark suites only**: Evaluate guardrails against industry benchmarks (AEGIS, ToxiGen). Insufficient: generic benchmarks test for generic safety, not organizational policy compliance. A guardrail that passes ToxiGen may still fail to enforce an FSI organization's specific content policies.

**A/B testing in production**: Deploy guardrail variants and measure user complaints. Dangerous: exposes users to policy violations during testing; compliance teams in regulated industries cannot accept this approach.

#### Reference Documents/Links

* RHAIRFE-1412 — Organizational AI Safety Constitution (prerequisite — provides the evaluation ground truth)
* RHAIRFE-1689 — LLM Judge Alignment via SIMBA and MemAlign (extended by constitution-driven calibration)
* RHAIRFE-1691 — Aligned Judge Registry and Team Sharing (constitutional-aligned judges as registerable artifacts)
* RHAIRFE-1171 — Automated Guardrails Policy Generation from EvalHub (complementary — generates configs; this RFE evaluates them)
* RHAIRFE-1655 — Validated Guardrail Model Catalog (customer evidence: Swiss Gov, Arrow AI Labs, NTT Docomo, TSMC, Isbank)
* RHAIRFE-1901 — Prompt-Level PII Detection and Redaction for MaaS (customer evidence: Garanti BBVA, Samsung)
* RHAISTRAT-155 — Make Agents Trustworthy: Enterprise AgentOps Lifecycle
* RHAISTRAT-1261 — Bring Agentic Safety Evals & Red Teaming into our Platform
* RHAISTRAT-1264 — Build a TrustyAI Agent Safety Solution (parent)
