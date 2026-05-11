---
rfe_id: RHAIRFE-1208
title: "Project Navigator: Intelligent Model recommendation and intelligent deployment Assistant"
priority: Critical
size: Large
status: Closed
---

## Problem Statement

Red Hat OpenShift AI provides a robust platform for model serving, but customers face a "cold start" problem. Before they can even struggle with _how_ to deploy a model, they struggle to decide _which_ model to deploy.

AI Engineers and LLMOps teams at enterprise customers (e.g., Lockheed Martin, Bank of America, Verizon) are overwhelmed by the rapid proliferation of open-weights models (Llama 3, Mistral, Granite, etc.). They lack a unified interface to answer two fundamental questions:

1. **Selection:** _Which model is best suited for my specific use case (e.g., coding assistant vs. creative writing) given my compliance and accuracy needs?_

1. **Operationalization:** _Once selected, what is the optimal hardware profile and runtime configuration to balance cost vs. performance?_

Currently, this requires manual correlation of Hugging Face leaderboards, disparate benchmark papers, and hardware spec sheets. This leads to **suboptimal model choices (poor accuracy)** and **inefficient infrastructure usage (wasted GPU spend)**.

## Business Alignment

### Business Value

* **End-to-End Guidance:** Positions OpenShift AI as a comprehensive AI partner that guides the user from "Idea" to "Production," rather than just a hosting platform.
* **Hardware Efficiency:** By correlating recommendations with _available_ cluster hardware, we prevent users from selecting models they cannot actually run, reducing support tickets and frustration.
* **Trust & Explainability:** Providing justification for model selection builds confidence in the platform's "Expert" capabilities.

### Expected Impact

* **Adoption:** Target 30-50% of new projects starting via the Navigator interface within 12 months.
* **Time-to-Value:** Reduce the time from "User Intent" to "Running Endpoint" from days (research + config) to minutes.

## Proposed Solution / Rationale

**Project Navigator** will act as an agentic orchestration layer. This MVP focuses on two tightly coupled workflows: **1. Model Recommendation** and **2. Deployment Optimization**.

### Workflow 1: Intelligent Model Recommendation

The entry point for the user. Navigator acts as a consultant to identify the right asset.

* **Intent Capture:** User inputs high-level goals.
    * _Input:_ "I need a coding assistant for my Java developers that runs efficiently on our current cluster hardware."

* **Registry & Benchmark Lookup:** Navigator scans the connected Model Registry (and curated external sources) and cross-references model capabilities with:
    * **Task Suitability:** (e.g., "Granite-34B-Code" for Java tasks).
    * **Hardware Constraints:** Checks current OpenShift cluster capacity (e.g., "Do we have enough A100s for a 70B model? If not, filter it out").
    * **Performance Benchmarks:** Looks up known HumanEval or MMLU scores.

* **Recommendation:** Systems presents top candidates with justification.
    * _Output:_ "Recommendation: IBM Granite-Code-34b. **Why:** Top-tier performance on Java/Python benchmarks, open license, and fits within your current 2x A100 quota (unlike Llama-3-70b)."

### Workflow 2: Deployment Optimization (The "How")

Once a model is selected (from Workflow 1), Navigator optimizes the runtime.

* **Candidate Generation:** System generates **three (3) distinct, valid deployment pipelines** (ServingRuntime + AcceleratorProfile + Autoscaling configs).
* **Automated Benchmarking:** Navigator deploys all three candidates to a sandbox and runs a standardized load test (simulating the described traffic pattern).
* **Metric-Driven Decision:** Navigator recommends the "Best Fit" pipeline based on real-time metrics (TTFT, P95, Cost).

## Acceptance Criteria

### A. Model Recommendation Engine

* [ ] System accepts natural language input regarding Use Case (Chat, Code, Summarization) and Constraints (Hardware limits, License type).
* [ ] System accesses the Model Registry to retrieve available models.
* [ ] **Constraint Awareness:** System queries OpenShift cluster capacity (e.g., available GPU types/count) and filters out models that are too large to run.
* [ ] **Output:** System presents at least 3 model options, each with:
    * "Why this model": A generated summary linking the model's strengths to the user's intent.
    * Benchmark highlights (e.g., "76% on HumanEval").
    * Resource fit check (e.g., "Fits on 1 node").

### B. Pipeline Generation & Benchmarking

* [ ] For the selected model, system generates 3 deployment candidates:
    * **Candidate A (Performance):** High tensor parallelism, aggressive memory caching.
    * **Candidate B (Cost/Density):** Quantization (FP8), scale-to-zero enabled.
    * **Candidate C (Balanced):** Standard FP16, autoscaling on concurrency.
* [ ] System automatically runs a short load test (using `llm-perf` or similar) on all 3 candidates.
* [ ] System reports metrics: **TTFT**, **P95 Latency**, **Tokens/sec**, **GPU Utilization**.
* [ ] System recommends the winning pipeline based on the user's initial priority (Speed vs. Cost).

## Implementation Assumptions

* Access to a curated list of model benchmarks (internal database or API).
* Real-time access to OpenShift Cluster metrics (Prometheus) to determine hardware availability.
* Integration with OpenShift AI Model Registry.

## Affected Customers & Scope

* **Primary:** Enterprises (Banking, Telco) where "Analysis Paralysis" slows down AI adoption.
* **Secondary:** Platform Admins who want to prevent users from trying to deploy 70B models on T4 GPUs (hardware-aware filtering).

## Alternatives Considered

* **Search Bar only:** Allowing users to search the registry by name. _Rejected:_ Assumes the user already knows what they want; doesn't solve the discovery problem.
* **Static "Recommended" Badge:** Hardcoding "Recommended" flags on models. _Rejected:_ Recommendations must be dynamic based on the _user's specific hardware_ and _intent_.
