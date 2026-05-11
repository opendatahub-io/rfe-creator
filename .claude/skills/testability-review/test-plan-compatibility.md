# Test-Plan-Create Compatibility Checklist

## Purpose

This checklist defines the specific requirements a STRAT must meet to successfully generate a quality test plan via `/test-plan-create`. It extends assess-strat's testability scoring by explicitly checking for information needed by test-plan-create's three parallel analyzers.

## Background

assess-strat evaluates whether acceptance criteria are measurable and testable (score 0-2). However, test-plan-create requires additional technical details beyond acceptance criteria alone:
- Concrete endpoint paths/methods (not just "add API")
- Environment version requirements (not just "uses PostgreSQL")
- Specific error cases and edge cases

A STRAT can have testable acceptance criteria (assess-strat score 2) but still produce a low-quality test plan if it lacks these details.

## How to Use This Checklist

### Structural Validation Results (Already Available)

The `strat.review` orchestrator has already run `scripts/validate_strat_testability.py` and saved results to `tmp/structural-{STRAT_ID}.json`.

**Read the JSON file for the STRAT you're reviewing:**
```bash
# Example: For RHAISTRAT-1431
cat tmp/structural-RHAISTRAT-1431.json
```

**Key metrics to check:**
- `structural_score < 5`: Major structural issues (likely Not ready)
- `warnings` with "CRITICAL": Blocking structural gaps
- `concrete_interactions_count == 0`: No concrete examples found (likely Insufficient)
- `tbd_count > 5`: Incomplete STRAT
- `error_case_mentions == 0` and `edge_case_mentions == 0`: Coverage gaps

**Fast decisions:**
- If structural_score < 5 AND multiple CRITICAL warnings → likely "Not ready"
- If structural_score ≥ 8 AND no CRITICAL warnings → focus semantic checks on nuance
- Use warnings array to target specific compatibility checks

### Semantic Assessment (Your Task)

Structural validation provides the metrics. You assess whether the content is **semantically sufficient**:
- Are the detected endpoints concrete enough for test plan Section 4?
- Are NFRs measurable enough for test strategy?
- Is test data format inferable from the acceptance criteria + technical approach?

Apply the 9 compatibility checks below, using structural validation to focus your review.

## Compatibility Checks

**Note:** These checks apply to all STRAT types (UI/API, Operator/CRD, CLI, SDK, Configuration, Backend). The terminology is generalized to support any interaction mechanism.

For a STRAT to score **testability=2** AND be **ready for test-plan-create**, it must pass ALL compatibility checks below:

### Interaction Analyzer Compatibility

The interaction analyzer (test-plan-analyze-endpoints) extracts feature scope and populates Section 4 (Endpoints/Methods Under Test). It requires concrete interaction mechanisms regardless of feature type:

- [ ] **Concrete interaction mechanism specified**
  
  Choose the appropriate interaction type(s) for your STRAT:
  
  - **REST API:** ✅ "`GET /api/v1/catalog/models?query={term}`" | ❌ "Will add API endpoint"
  - **UI/Dashboard:** ✅ "Models page > MaaS Model Refs tab > Register button" | ❌ "Dashboard UI for models"
  - **CLI:** ✅ "`kubectl apply -f maasmodelref.yaml`", "`odh-cli model create --provider=openai`" | ❌ "CLI command to create models"
  - **CRD/Custom Resource:** ✅ "MaaSModelRef CR with spec: {provider, endpoint, modelId}" | ❌ "New custom resource for models"
  - **SDK/Library:** ✅ "`client.models.create(provider='openai', endpoint='...')`" | ❌ "Programmatic API"
  - **Configuration:** ✅ "ConfigMap with fields: {enableMaaS: bool, providers: []}" | ❌ "Config file for MaaS"
  - **Operator/Controller:** ✅ "MaaSModelRef controller reconciles .spec.externalModel → Istio VirtualService" | ❌ "Operator handles external models"
  
- [ ] **Interaction specifications traceable to source**
  - ✅ Acceptance criteria or Technical Approach explicitly states the interaction mechanism with concrete examples
  - ❌ Interactions are inferable but not explicitly documented

- [ ] **If multi-layer feature, all layers identified**
  - ✅ UI feature: Both UI flow AND BFF/API endpoints specified (e.g., "Dashboard calls `POST /api/v1/maasmodel`")
  - ✅ Operator feature: Both CRD spec AND controller reconciliation logic described
  - ✅ CLI feature: Command syntax AND underlying API/SDK calls identified (if applicable)
  - ❌ Only one layer described (e.g., "UI for models" without BFF endpoint, or "CRD for models" without reconciliation behavior)

**Scoring impact:**
- All 3 checks pass → Interaction analyzer: ✅ Ready
- 1-2 checks fail → Interaction analyzer: ⚠️ Partial (test plan will have TBDs in Section 4)
- All 3 checks fail → Interaction analyzer: ❌ Insufficient (test plan generation may fail)

### Risks Analyzer Compatibility

The risks analyzer (test-plan-analyze-risks) extracts test strategy, NFRs, and risks. It requires:

- [ ] **NFRs include specific, measurable targets**
  - ✅ Good: "p95 latency <300ms for 1000 models", "Support 200 concurrent users"
  - ❌ Bad: "System is performant", "API is fast"

- [ ] **Error cases covered**
  - ✅ Acceptance criteria or Technical Approach lists error scenarios ("Returns 400 if query malformed", "Returns 503 if backend unavailable")
  - ❌ Only happy path described

- [ ] **At least one edge case identified per major workflow**
  - ✅ Acceptance criteria or Technical Approach mentions boundary conditions ("What about 201 resources?", "Concurrent creates", "Stale cache")
  - ❌ No edge cases mentioned

**Scoring impact:**
- All 3 checks pass → Risks analyzer: ✅ Ready
- 1-2 checks fail → Risks analyzer: ⚠️ Partial (NFR section will have TBDs, risk analysis generic)
- All 3 checks fail → Risks analyzer: ❌ Insufficient (test plan NFR section will be weak)

### Infra Analyzer Compatibility

The infra analyzer (test-plan-analyze-infra) extracts environment, test data, and infrastructure requirements. It requires:

- [ ] **Environment requirements explicitly specified**
  - ✅ PASS: Versions stated directly in this STRAT ("Requires model-registry v1.5.0+, PostgreSQL 13+, OpenShift 4.15+")
  - ⚠️ PARTIAL: Versions exist but in referenced STRATs/docs ("See RHAISTRAT-1295 for CRD schema") - will cause TBDs
  - ❌ FAIL: No version information ("Uses model registry", "Requires database")

- [ ] **Test data format described or inferable**
  - ✅ Acceptance criteria shows example data structure ("MaaSModelRef CR with ExternalModel provider type... endpoint URL, model identifier, credential Secret reference")
  - ✅ Technical Approach describes data models
  - ❌ No indication of data structure

- [ ] **RBAC requirements identified (if applicable)**
  - ✅ Acceptance criteria or Technical Approach mentions required permissions ("Requires `catalog:read` permission", "Platform admin only")
  - ❌ RBAC not mentioned despite feature requiring specific roles

**Scoring impact:**
- All 3 checks pass → Infra analyzer: ✅ Ready
- 1-2 checks fail → Infra analyzer: ⚠️ Partial (environment section will have TBDs, test data format unclear)
- All 3 checks fail → Infra analyzer: ❌ Insufficient (test plan environment section will be weak)

## Aggregate Scoring

**Test Plan Generation Readiness** is determined by combining all three analyzer assessments:

| Interaction | Risks | Infra | Overall Readiness | Expected Quality | Expected Gap Resolvability |
|-------------|-------|-------|-------------------|------------------|---------------------------|
| ✅ Ready | ✅ Ready | ✅ Ready | **Ready** | ≥8/10 | **All addressable** - Can provide API specs, design docs now |
| ✅ Ready | ✅ Ready | ⚠️ Partial | **Ready (pending deps)** | 7-9/10 | **Mixed** - Some blocked until dependencies ship |
| ✅ Ready | ⚠️ Partial | ⚠️ Partial | **Needs improvement** | 5-7/10 | **Mixed** - Some require STRAT revision |
| ⚠️ Partial | ⚠️ Partial | ⚠️ Partial | **Needs improvement** | 4-6/10 | **Many unfixable** - STRAT deficiencies |
| ❌ Insufficient | (any) | (any) | **Not ready** | <4/10 | **Most unfixable** - Requires STRAT rewrite |
| (any) | ❌ Insufficient | (any) | **Not ready** | <4/10 | **Most unfixable** |
| (any) | (any) | ❌ Insufficient | **Not ready** | <4/10 | **Most unfixable** |

**Gap Resolvability Types:**

1. **Addressable** - Can be resolved by providing documentation now:
   - API specifications (OpenAPI schemas, endpoint contracts)
   - Design documents (implementation choices, architecture decisions)
   - Deployment guides (environment variables, configurations)
   - ADRs (architectural decision records)
   - Example: "PostgreSQL migration details" → Provide design doc

2. **Blocked** - Waiting for pending dependencies to ship:
   - Dependencies with status "Needed" (RHAISTRAT-XXXX not yet implemented)
   - Pending design work (UX review required, architecture review pending)
   - Example: "CRD schema from RHAISTRAT-1295" → Wait for dependency to land

3. **Unfixable** - Require STRAT revision:
   - Missing from STRAT (no concrete interactions, vague criteria, unclear scope)
   - Cannot be resolved with documentation alone
   - Example: "No API endpoints specified" → Revise STRAT to add endpoints

**Gap Count:** Expect 10-20 gaps even for "Ready" STRATs. What matters is resolvability:
- All addressable → Can proceed with test planning and address gaps incrementally
- Mix of addressable + blocked → Can proceed but some gaps remain until dependencies ship
- Many unfixable → Should revise STRAT before test planning

**Decision criteria:**
- **Ready**: All gaps addressable or blocked by documented dependencies → Quality ≥8/10
- **Needs improvement**: Mix of addressable and unfixable gaps → Quality 4-7/10
- **Not ready**: Most gaps unfixable without STRAT revision → Quality <4/10

## Decision Tree: Actions Based on Readiness

### If Readiness = "Ready" ✅
**Meaning:** STRAT contains sufficient detail for quality test plan generation

**Gap resolvability:** All gaps will be **addressable** (can provide API specs, design docs now) or **blocked** (waiting for documented dependencies)

**Testability reviewer recommendation:**
- Proceed to `/test-plan-create RHAISTRAT-XXX`
- Expected test plan quality: ≥8/10
- Expected gaps: 10-20 gaps, all with clear resolution paths

**What to provide during test plan generation:**
- API specifications (OpenAPI schemas, endpoint contracts)
- Design documents (implementation details, tool choices)
- Deployment configurations (environment variables, resource limits)
- ADRs (if available)

**Next steps:**
1. Run `/test-plan-create RHAISTRAT-XXX [ADR_FILE]`
2. Review TestPlanGaps.md - all gaps should have "Would be resolved by: ..." statements
3. Provide available documentation to resolve addressable gaps
4. Document blocked gaps (waiting for dependencies) with timeline
5. If quality ≥8, proceed to test case generation

### If Readiness = "Needs Improvement" ⚠️
**Meaning:** STRAT can support test plan generation but will have mix of addressable and unfixable gaps

**Gap resolvability:** Mix of **addressable** (can provide docs), **blocked** (pending dependencies), and **unfixable** (require STRAT revision)

**Testability reviewer recommendation:**
Two options for the STRAT author:

**Option A (Recommended) - Fix STRAT First:**
1. Address **unfixable gaps** listed in assessment (missing endpoints, vague criteria, unclear scope)
2. Re-run `/strat.review` to verify improvements
3. Once readiness improves to "Ready", run `/test-plan-create`
4. Time investment: 30-60 min to improve STRAT
5. Payoff: Higher quality test plan (7-9/10 instead of 4-7/10), fewer unfixable gaps

**Option B - Proceed with Gaps:**
1. Run `/test-plan-create RHAISTRAT-XXX` now
2. Expected test plan quality: 4-7/10
3. Expected gaps: 15-30 gaps, some **unfixable** without STRAT revision
4. Provide documentation for addressable gaps
5. Some gaps will remain unfixable → require test plan revision after STRAT update
6. Time cost: 2-3 hours (iteration on both STRAT and test plan)

### If Readiness = "Not Ready" ❌
**Meaning:** STRAT lacks critical details; test plan generation would fail or produce quality <4/10

**Gap resolvability:** Most gaps **unfixable** - require STRAT revision, not just documentation

**Testability reviewer recommendation:**
**Do NOT proceed to test-plan-create**

**Required actions:**
1. Identify blocking issues (typically 2+ analyzers scored ❌ Insufficient)
2. Add critical missing content to STRAT:
   - **Interaction analyzer insufficient:** Add concrete endpoints, UI flows, CLI commands, or CRD specs to Technical Approach
   - **Risks analyzer insufficient:** Add measurable NFRs, error cases, edge cases
   - **Infra analyzer insufficient:** Specify environment versions, test data format, RBAC requirements
3. Re-run `/strat.review` after revisions
4. Only proceed to test plan generation once readiness ≥ "Needs improvement"

**Why gaps are unfixable without STRAT revision:**
- No concrete interactions → Cannot provide in documentation, must be in STRAT
- Vague acceptance criteria → Cannot add metrics via API spec, must fix criteria
- Missing scope boundaries → Cannot clarify in design doc, must define in STRAT

**Time investment:** 1-2 hours to fix STRAT
**Time saved:** 3-5 hours avoiding failed test plan generation with 30+ unfixable gaps

## Integration with assess-strat Testability Scoring

**Relationship:**
- assess-strat testability scoring (0-2) evaluates acceptance criteria measurability
- This compatibility checklist evaluates technical detail sufficiency for test-plan-create

**Combined decision logic:**

| assess-strat Testability | Test-Plan Readiness | Action |
|-------------------------|---------------------|--------|
| 0 (untestable criteria) | (any) | ❌ Block: Fix acceptance criteria first |
| 1 (partial criteria) | Not ready | ❌ Block: Fix both criteria and add technical details |
| 1 (partial criteria) | Needs improvement | ⚠️ Can proceed, expect quality 4-6 |
| 2 (measurable criteria) | Not ready | ⚠️ Criteria good, but missing technical details for test plan |
| 2 (measurable criteria) | Needs improvement | ✅ Proceed, expect quality 6-8 |
| 2 (measurable criteria) | Ready | ✅ Proceed, expect quality ≥8 |

## Common Gaps and Fixes

### Gap: "Vague interaction specifications"
**Example:** "Dashboard UI for managing models", "CLI for model management", "Operator handles models"

**Impact:** Interaction analyzer cannot populate Section 4 (Endpoints/Methods Under Test)

**Fix:** Add concrete interaction mechanisms to Technical Approach or Acceptance Criteria:
- **For UI:** Specific BFF endpoint paths (e.g., "`POST /api/v1/maasmodel` to create MaaSModelRef") AND UI flows ("Models page > MaaS Model Refs tab > Register button")
- **For CLI:** Command syntax with examples (e.g., "`kubectl apply -f model.yaml`", "`odh-cli model create --name=gpt4`")
- **For Operator:** CRD spec structure AND reconciliation logic (e.g., "MaaSModelRef CR with .spec.provider → controller creates Istio VirtualService")
- **For SDK:** Method signatures (e.g., "`client.models.create(name, provider, endpoint)`")

### Gap: "Generic NFRs"
**Example:** "System should be performant and scalable"

**Impact:** Risks analyzer produces generic risk assessments, weak NFR section

**Fix:** Add measurable targets:
- "List view renders ≤2s for 200 resources"
- "Supports 50 concurrent model registrations without degradation"

### Gap: "Missing environment versions"
**Example:** "Uses PostgreSQL and model-registry"

**Impact:** Infra analyzer cannot populate Section 3 (Test Environment), leaves versions as TBD

**Fix:** Specify versions in Dependencies:
- "Requires model-registry v1.5.0+ (for ExternalModel support)"
- "PostgreSQL 13+ (existing platform dependency)"

### Gap: "No error cases"
**Example:** Acceptance criteria only describes happy path

**Impact:** Risks analyzer misses error scenarios, test objectives incomplete

**Fix:** Add error cases to Technical Approach or Acceptance Criteria:
- "Returns 400 if provider is not in allowed list"
- "Returns 409 if MaaSModelRef with same name already exists"
- "Returns 503 if model-registry is unavailable"

### Gap: "RBAC unclear"
**Example:** No mention of required permissions

**Impact:** Infra analyzer cannot determine test user setup

**Fix:** Add to NFRs or Technical Approach:
- "Requires `maasmodel:create` and `maasmodel:read` permissions"
- "Platform admin role required for MaaSModelRef CRUD"

## Using This Checklist

### During STRAT Review (testability-review skill)
1. Read acceptance criteria and Technical Approach
2. Check each of the 9 compatibility checks (3 per analyzer)
3. Assign analyzer status: ✅ Ready (3/3 pass), ⚠️ Partial (1-2 pass), ❌ Insufficient (0 pass)
4. Determine overall Test Plan Generation Readiness
5. List specific gaps preventing "Ready" status
6. Include in testability review output

### During STRAT Refinement (strat.refine skill)
1. After generating STRAT, self-assess against this checklist
2. If any analyzer scores ⚠️ Partial or ❌ Insufficient, add missing details before finalizing
3. Target: all three analyzers ✅ Ready before submitting STRAT for review

### Before Running /test-plan-create
1. Quick checklist review
2. If overall readiness is "Not ready", do NOT proceed
3. If "Needs improvement", decide: address gaps now or accept lower quality and fix with ADR later
4. If "Ready", proceed confidently

## Maintenance

This checklist should be updated when:
- test-plan-create analyzer requirements change
- New analyzer types are added
- Patterns of false positives/negatives emerge (predicted Ready but got quality <8)
- New gap categories are discovered through testing
