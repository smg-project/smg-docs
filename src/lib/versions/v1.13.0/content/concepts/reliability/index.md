---
title: Reliability & Resilience
---

# Reliability & Resilience

SMG provides several mechanisms to ensure high availability and stability.

## Core Mechanisms

*   **[Circuit Breakers](./circuit-breakers.md)**: Automatically detect failing workers and temporarily stop sending traffic to them until they recover.
*   **[Retries & Backoff](./retries.md)**: Configurable retry logic with exponential backoff to handle transient network issues or worker busy states.
*   **[Rate Limiting](./rate-limiting.md)**: Protect your workers from being overwhelmed by controlling the concurrency and request rate.
*   **[Tenant Rate Limiting](./tenant-rate-limiting.md)**: Cap per-tenant LLM token and request consumption per minute, independent of worker concurrency.
*   **[Priority Scheduling](./priority-scheduling.md)**: Admit higher-priority traffic first with reserved slots, per-class queues, and TTFT-aware preemption.
*   **[Overload Protection](./overload-protection.md)**: Exclude workers whose engines report saturation from routing. When every candidate worker is saturated, the request is steered to the least-loaded one by default, or shed immediately with `--worker-overload-shed`.
*   **[Health Checks](./health-checks.md)**: Background probes that take unhealthy workers out of the rotation without waiting for requests to fail, and return them when they recover.
*   **[Graceful Shutdown](./graceful-shutdown.md)**: Ensure in-flight requests complete before the server stops.
