---
rfe_id: RHAIRFE-2214
title: "Agent evaluation data generation for modern harnesses and all tool types"
priority: Normal
size: Medium
status: Closed
---

## Summary

AI teams building agents with modern agent harnesses (Claude Code, OpenClaw, and other general-purpose agent platforms) need to generate evaluation data using SDG Hub. Currently, SDG Hub only supports LangFlow and LangGraph (2024-style frameworks) and is limited to MCP protocol tools. As part of the AI Agent Evaluation Platform strategic investment, SDG Hub must expand to support modern agent harnesses, all tool types (bash, file operations, built-in tools), and produce evaluation data consumable by standard evaluation frameworks (MLflow, Eval Hub, custom evaluators).

## Problem Statement

AI teams building agents cannot use SDG Hub for evaluation data generation when:

1. Their agents are built with modern harnesses (Claude Code, OpenClaw, OpenCode) instead of LangFlow/LangGraph
2. Their agents use bash commands, file read/write operations, grep, or other built-in tools rather than MCP-protocol tools
3. They need to consume evaluation data in MLflow, Eval Hub, or custom evaluation pipelines

Current workarounds:

* Rewrite agents to use LangGraph purely for evaluation purposes (unsustainable as agent architecture diverges from eval architecture)
* Skip evaluation data generation entirely, relying only on production traces
* Manually transform SDG Hub output for evaluation frameworks (brittle and time-consuming)

## Affected Customers

* Internal: OpenShift Lightspeed team (currently using LangGraph but evaluating modern harnesses)
* AI teams across RHOAI ecosystem building with modern agent platforms
* Strategic investment: AI Agent Evaluation Platform initiative requires comprehensive agent evaluation support

## Business Justification

* **Strategic investment enabler**: AI Agent Evaluation Platform requires support for modern agent architectures, not just legacy LangGraph-style frameworks
* **Competitive gap**: The industry has moved to general-purpose agent harnesses (Claude Code, OpenClaw) as the 2026 standard. Competitors support eval data generation for these platforms; SDG Hub's LangFlow/LangGraph-only support positions it as a 2024-era tool
* **Adoption blocker**: Teams building with modern harnesses must choose between rewriting their agents for eval purposes or abandoning eval data generation
* **Technical debt**: As referenced in Bill and Adel's feedback (April 2026), the current LangGraph-centric approach is misaligned with how agents are actually built today

## User Scenarios

1. As an AI engineer building a Claude Code agent with multiple skills and bash/file operations, I need to generate synthetic evaluation data that exercises my agent's full toolset, so I can measure its performance before deploying to production.

1. As a platform team lead running MLflow for model and agent evaluation, I need SDG Hub to output evaluation data in MLflow-compatible trace format, so I can use a unified evaluation pipeline across all model types without custom transformation layers.

1. As a data scientist experimenting with different agent frameworks (trying both LangGraph and OpenClaw for the same task), I need to generate comparable evaluation datasets for both implementations, so I can choose the best framework based on measured performance rather than intuition.

## Acceptance Criteria

* [ ] Users can generate evaluation data for agents built with Claude Code by connecting SDG Hub to Claude Code agent endpoints
* [ ] Users can generate evaluation data for agents built with OpenClaw and other modern harnesses via agent connectors
* [ ] Users can generate evaluation data for agents that invoke bash commands, file read/write, grep, and all built-in harness tools, not just MCP protocol tools
* [ ] Generated evaluation data includes full tool call traces (tool name, arguments, results) regardless of tool protocol (MCP, bash, native)
* [ ] Users can consume SDG Hub output directly in MLflow evaluation workflows without transformation
* [ ] Users can consume SDG Hub output directly in Eval Hub evaluation workflows without transformation
* [ ] Users receive evaluation data in a standardized trace format (MLflow traces or Open Telemetry) that works with custom evaluation pipelines

## Success Criteria

* OpenShift Lightspeed team successfully generates evaluation data for a Claude Code agent prototype and runs it through MLflow evaluation
* At least 3 different modern agent harnesses (Claude Code, OpenClaw, OpenCode, etc.) have working SDG Hub connectors
* Evaluation data includes at least 80% coverage of all tool invocations (MCP + built-in tools) in agent traces
* Zero manual transformation steps required between SDG Hub output and MLflow/Eval Hub ingestion

## Scope

### In Scope

* Building agent connectors for modern harnesses (Claude Code, OpenClaw, OpenCode, and other general-purpose platforms)
* Expanding tool trace capture beyond MCP protocol to include bash, file operations, and all built-in harness tools
* Standardizing evaluation data output format for MLflow, Eval Hub, and custom evaluator consumption
* Testing with complex multi-skill agents that use diverse tool types

### Out of Scope

* Building evaluation frameworks themselves (MLflow, Eval Hub integration points remain external)
* Evaluating agent quality or correctness (SDG Hub generates test data; evaluators assess agent performance)
* Modifying agent harnesses themselves or proposing changes to their APIs
* Supporting legacy agent frameworks beyond the current LangFlow/LangGraph support (focus on modern harnesses, not expanding 2024-era support)

## Open Questions

* Should SDG Hub prioritize specific modern harnesses based on adoption metrics, or build a generic connector interface first?
* Is Open Telemetry the preferred standard for trace output, or should MLflow's proprietary format take precedence?
* How should SDG Hub handle harnesses that don't expose agents via REST API (e.g., require stdio/subprocess communication)?
