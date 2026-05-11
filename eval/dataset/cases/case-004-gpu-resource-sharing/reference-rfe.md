---
rfe_id: RHAIRFE-2141
title: "MLR/MLE/Runtime Developers Need Managed GPU Resource Sharing to Eliminate Daily Productivity Loss from Contention and Environment Migration"
priority: Major
size: Large
status: Closed
---

## Summary

MLR, MLE, and Runtime team developers need a managed GPU compute environment with job scheduling, shared persistent storage, and adequate capacity so they can develop, benchmark, and train models without losing hours to GPU contention, environment migration, and idle resource waste.

## Problem Statement

Approximately 20 developers across MLR, MLE, and Runtime teams share 8–10 H100/H200/B200 GPU servers. Today there is minimal (`chg`) scheduling, queuing, resource management — developers SSH into machines, discover GPUs are occupied by long-running benchmark or evaluation jobs (some running 2+ days), and must migrate to another node. This migration means re-setting up the working environment each time, since there is no shared persistent home directory across nodes.

The result is significant daily productivity loss:

* **Contention without visibility:** Developers cannot see what jobs are running, who is using which GPUs, or how long a job will take. They discover contention only after attempting to use a machine.
* **Interrupted workflows from team mismatches:** Different teams have fundamentally different GPU usage patterns that conflict when sharing nodes. Runtime developers work in short interactive cycles — edit code, rebuild (vLLM rebuilds alone take 10+ minutes), test, read logs — and may leave GPUs idle for brief periods between iterations. ML researchers run long multi-hour training or evaluation jobs and reasonably claim GPUs that appear idle. The result is that a runtime developer stops using the GPU for 10–30 minutes to read/edit code, review logs, rebuild, or just step away for coffee — and returns to find an 8-hour job occupying their GPUs. Manually reserving GPUs for the entire interactive session isn't a solution either — those 10–30 minute idle gaps are long enough for other users to run short tasks, and locking them out wastes scarce resources. Neither team is at fault — it's a workflow pattern mismatch that the current ad-hoc system cannot mediate.
* **No job time expectations:** When a developer finds GPUs in use, there is no way to know whether the job will finish in 10 minutes or 8 hours. This makes it very challenging to decide whether to wait or migrate to another machine — a decision that itself costs significant time if shared home directories don't exist.
* **No separation of interactive and batch workloads:** A developer doing interactive coding shares the same GPU pool as multi-day evaluation runs, with no mechanism to prioritize or preempt.
* **Environment migration friction:** Without shared home directories, switching nodes means re-cloning repos, re-installing dependencies, re-downloading model weights, and restarting Claude Code sessions — wasting hours per occurrence.
* **Idle resources amid scarcity:** Machines often sit idle because developers don't know they're available or because the friction of moving to them is too high. Meanwhile, others are blocked waiting for GPUs.
* **Insufficient total capacity:** Even with perfect scheduling, ~20 users each needing 4–8 GPUs for development, evaluation, or training exceeds the available pool. Long-running evaluation jobs (e.g., evaluating a single model across reasoning benchmarks takes 2+ days on 8×H100) create sustained pressure.

Current workarounds include informal "GPU kill" policies (jobs over 30 minutes on shared nodes can be terminated by anyone), manually checking each machine, and Slack-based negotiation between developers — but these do not scale.

* **Agentic coding compounds the problem:** As the team adopts AI coding agents (e.g., Claude Code) that autonomously request and use GPU resources, the current human-negotiation model breaks down further. Agents cannot Slack a colleague to coordinate GPU access, cannot judge whether to wait or migrate, and are more likely to leave zombie processes occupying GPUs after a task completes or fails. A formal scheduling and resource management system is a prerequisite for safely scaling agentic workflows on shared infrastructure.

## Affected Customers

* **MLR (Machine Learning Research) team:** ~10 researchers running model evaluations, training jobs, and interactive development on H100/H200/B200 nodes.
* **MLE (Machine Learning Engineering) team:** Engineers developing and benchmarking vLLM runtime performance, requiring both interactive GPU access and exclusive-access benchmark runs.
* **Runtime team:** Developers working on inference runtime features who need on-demand GPU access for testing and validation.
* **Cross-team impact:** Multiple developers spanning all three sub-teams have reported this as a significant daily pain point.

## Business Justification

* **Developer productivity:** ~20 developers losing significant time daily to GPU contention and environment migration. At this scale, even 1–2 hours/day lost per developer represents substantial engineering capacity waste.
* **Resource utilization:** Existing compute resources "often sit idle a lot" despite demand — the current ad-hoc access model causes simultaneous under-utilization and over-contention.
* **Strategic alignment:** The teams are building and validating core RHOAI capabilities (vLLM runtime, model evaluation, inference optimization). Slow iteration velocity on these workloads directly impacts product delivery timelines.
* **Capacity gap:** Current GPU pool (~8–10 servers) is insufficient for 20+ users who each need 4–8 GPUs. For context, evaluating a single model for multiple seeds for confidence intervals on a few reasoning tasks occupies one full server for at least 2 days. Without additional capacity, scheduling alone will not resolve the contention.
* **Prerequisite for agentic workflows:** The team is adopting AI coding agents (e.g., Claude Code) that autonomously request and use GPU resources. Agents cannot negotiate access via Slack, cannot judge whether to wait or migrate without job time estimates, and are more likely to leave zombie processes on GPUs. Formal resource management is a prerequisite for safely scaling agentic workflows — without it, increased agent adoption will amplify the existing contention problems.

## User Scenarios

1. **As a runtime developer**, I need to start an interactive GPU session on a shared development partition, do my work, end the session, and later start a new session — on the same or a different node — and pick up exactly where I left off with all my repos, virtual environments, dependencies, and model caches intact. Multiple developers should be able to share the same node's GPUs simultaneously for interactive work. From within my interactive session, I should also be able to submit batch jobs (benchmarks, evaluations) to the exclusive partition without leaving my development environment.

1. **As an ML researcher**, I need to submit a long-running evaluation or training job to a dedicated benchmarking/training partition where my job gets exclusive GPU access for high-fidelity results. The job should queue until resources are available and support checkpointing so preempted jobs can resume.

1. **As a team lead**, I need visibility into GPU utilization across all nodes — what jobs are running on which partition, who submitted them, how long they've been active, and which resources are idle — so I can make informed decisions about resource allocation and justify capacity requests.

## Acceptance Criteria

* [ ] GPU resources are partitioned into at least two pools: a shared/oversubscribed partition for interactive development (multiple users per node) and an exclusive partition for benchmarking, evaluation, and training jobs requiring isolated GPU access
* [ ] Developers can start interactive GPU sessions on the shared partition, with multiple users able to work on the same node simultaneously
* [ ] Developers can submit batch jobs (benchmarks, evaluations, training runs) to the exclusive partition's queue and have them scheduled automatically when resources are available, with mandatory time limits
* [ ] Developers can submit batch jobs to the exclusive partition from within an active interactive session on the shared partition
* [ ] Developer environments (home directories, virtual environments, installed dependencies, model caches) persist across nodes and across interactive sessions
* [ ] Developers and leads can view the status of all nodes, running jobs, queued jobs, and resource utilization
* [ ] Higher-priority workloads can preempt lower-priority jobs, with support for checkpointing so preempted jobs can resume
* [ ] The GPU compute pool is expanded to reduce sustained resource pressure for the ~20-user base needing 4–8 GPUs each

## Success Criteria

* Developers report spending less than 15 minutes per day on GPU discovery, environment setup, and node migration (down from hours)
* GPU utilization across the cluster increases measurably (baseline to be established with visibility tooling)
* Long-running evaluation and benchmark jobs are submitted to queues rather than blocking interactive access
* No developer is blocked for more than one business day due to GPU contention

## Scope

### In Scope

* Job scheduling and queuing for GPU workloads (interactive and batch)
* Shared persistent home directory accessible from all compute nodes
* Resource visibility and utilization reporting
* GPU pool capacity expansion
* Oversubscription support for interactive development sessions

### Out of Scope

* Application-level changes to vLLM, model training frameworks, or evaluation harnesses
* Networking changes between cloud regions or data centers
* Cost optimization or chargeback models for GPU usage
* Self-service provisioning of new GPU nodes

## Open Questions

* **Scheduling platform selection:** Engineering should evaluate Slurm (bare-metal), Slinky (Slurm on Kubernetes), Kueue (Kubernetes-native), and other alternatives to determine the best fit for the team's workflows, existing infrastructure, and long-term maintainability.
* Are there budget or procurement constraints on expanding the GPU pool?
* How should resources be partitioned across the MLR, MLE, and Runtime teams — shared pool with priorities, or dedicated allocations?
* Should the solution support machines spread across multiple clouds (IBM Cloud, others), or focus on a single cluster first?
