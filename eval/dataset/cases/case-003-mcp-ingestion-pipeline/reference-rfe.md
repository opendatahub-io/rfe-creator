---
rfe_id: RHAIRFE-2081
title: "Unified MCP Ingestion Pipeline for Partner and Customer MCPs"
priority: Major
size: Large
status: Closed
---

## Summary

Red Hat needs a structured, automated ingestion pipeline that validates, scans, evaluates, and registers MCP servers from all sources — ISV partners, community contributors, and customer platform engineers — so that every MCP entering the RHOAI platform goes through consistent governance before reaching the registry and catalog, regardless of its origin.

## Problem Statement

Today, onboarding MCP servers into RHOAI is entirely manual and inconsistent. For the 3.4 Dev Preview, a single engineer (Jose Gonzalez) manually adapted all 10 MCP server containers — rebasing to UBI, adding labels, copying licenses, and building containers from scratch when none existed. All MCPs had different metadata formats, different containerization approaches, and required individual manual adaptation. CVE scanning was limited to Quay image scanning with no dependency analysis. No end-to-end functional testing of MCP server applications was performed.

This problem exists across both ingestion lanes:

**Partner/Community lane**: ISV partners provide MCP server repositories or containers. Each requires manual adaptation to meet RHOAI standards. The Partnership Ecosystem team cannot scale this manual process as the catalog grows beyond 10 MCPs.

**Customer lane**: Platform engineers at customer organizations want to create MCPs from existing APIs (OpenAPI specs, CLI interfaces, HTTP backends) and have them governed in their cluster's registry and catalog. Today there is no supported path for this — customer-created MCPs bypass all governance, creating a trust gap where partner MCPs are validated but customer MCPs are not.

Both lanes need the same pipeline components: validation, scanning, evaluation, and registration. The difference is only the input source. Without a unified pipeline, Red Hat must build and maintain separate governance processes for each source type, and customers cannot trust that all MCPs in their environment meet the same standards.

## Affected Customers

* **ISV Partners (current)**: Confluent, Dynatrace, HashiCorp (Terraform), Microsoft Azure, EnterpriseDB (EDB) — all 5 went through the manual 3.4 onboarding and need a sustainable path for future submissions
* **ISV Partners (pipeline)**: Oracle, CyberArk, JFrog — did not make the 3.4 cutoff due to technical gaps; need the pipeline to onboard
* **Citibank**: Actively asking when MCP creation from existing APIs will be a supported, governed RHOAI capability
* **State Farm, Dell, Prudential, Florida Blue**: Actively asking how RHOAI will govern MCP servers in production — both partner-sourced and customer-created MCPs are part of that governance story
* **Partnership Ecosystem Engineering team** (Matt Dorn, Jose Gonzalez, Serob): Current manual process is unsustainable

## Business Justification

* **Strategic investment — MCP ecosystem scale**: Red Hat's MCP strategy depends on a growing catalog from multiple sources. Manual onboarding creates a linear scaling problem where each new MCP requires dedicated engineering time. An automated pipeline converts this to a self-service model for both partners and customers.
* **Enterprise customer demand for MCP governance**: State Farm, Citibank, Dell, Prudential, and Florida Blue are actively evaluating RHOAI's MCP governance story. A structured ingestion pipeline with validation, scanning, and lifecycle tracking directly addresses their questions about trusting MCP servers in production — from any source.
* **Direct customer demand for customer MCP creation**: Citibank is specifically asking when MCP creation from APIs will be a supported, governed capability. Having a governed ingestion path for customer-created MCPs directly supports deal progression with strategic financial services accounts.
* **Sales enablement**: Services and sales teams are already pitching MCP creation from APIs as a core RHOAI capability. A governed ingestion pipeline validates those sales motions with a real product path.
* **Supply chain security**: Enterprise customers require provenance tracking, vulnerability scanning, and evaluation evidence before trusting MCP servers. The current process provides none of this in a systematic way — for any source.
* **Governance consistency**: Enterprise customers will not accept a governance model where partner MCPs are validated and scanned but their own customer-created MCPs bypass all quality gates. A unified pipeline applies the same standards to all sources.
* **Competitive positioning**: No competing platform offers a governed MCP ingestion pipeline for both vendor and customer MCPs. Red Hat can establish the standard for how enterprise MCP ecosystems are managed.

## User Scenarios

1. **As a Partnership Ecosystem engineer** (e.g., Jose), I need to onboard a new partner MCP by pointing the pipeline at their repository and having it automatically validate the repo against our checklist, scan for vulnerabilities, evaluate the MCP's tools, and register it — instead of manually adapting each container.

1. **As a platform engineer** at Citibank, I need to take our existing internal trading API (defined in OpenAPI) and create an MCP server from it, then have that MCP validated, scanned, and registered in our cluster's MCP registry — so my AI engineering team can safely use it in their agents.

1. **As an AgentOps admin** at a customer site (e.g., State Farm), I need to see that every MCP in the catalog — whether from Red Hat, a partner, or created internally — has passed the same validation, scanning, and evaluation pipeline, with results and provenance visible, so I can make informed trust decisions about which MCPs to deploy in production.

## Acceptance Criteria

* [ ] MCP servers from partners, community contributors, and customers can be submitted and processed through a unified pipeline (validation, scanning, evaluation) without manual container adaptation by Red Hat engineering
* [ ] Each ingested MCP has validation results, scan results (CVE at minimum), and evaluation scores recorded and associated with its registry entry
* [ ] Sources whose MCPs fail validation or scanning receive clear, actionable feedback about what needs to be fixed
* [ ] MCP versions processed through the pipeline have provenance tracking (source, scan results, evaluation scores, who approved) available to downstream consumers
* [ ] The pipeline supports MCPs from sources who provide production-ready containers as well as sources who provide basic (stdio/unsecured HTTP) MCPs requiring production-readiness wrapping
* [ ] Customer-created MCPs are registered with provenance indicating their source, and are governed with the same rigor as partner MCPs
* [ ] The pipeline integrates with the MCP Registry (MLflow) as the system of record for ingested MCPs

## Success Criteria

* New MCPs from any source can be onboarded without dedicated Red Hat engineering effort for container adaptation
* The number of MCPs in the catalog can grow without proportional growth in engineering headcount
* Customer-created MCPs have the same governance metadata (scan results, eval scores, provenance) as partner MCPs

## Scope

### In Scope

* Unified MCP ingestion workflow: submission, validation, scanning, evaluation, registration — for partner, community, and customer sources
* Integration with the MCP Registry (MLflow) as the system of record
* Validation feedback to submitters (partners and customers)
* Provenance and audit trail for ingested MCPs from all sources

### Out of Scope

* Off-cycle partner MCP update distribution (see RFE-003 — separate release engineering concern)
* Gen MCP productization (see RFE-004)
* MCP container metadata schema definition (see RFE-002 — prerequisite)
* Pipeline orchestration tooling selection (engineering decision)
* MCP Gateway or Lifecycle Operator changes
