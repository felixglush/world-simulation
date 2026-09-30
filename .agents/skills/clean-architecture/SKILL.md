---
name: clean-architecture
description: Design or review application boundaries, dependency direction, and ports and adapters when shaping architecture or refactoring responsibilities.
---

# Clean Architecture

Protect business policy from infrastructure details. Choose boundaries
that clarify ownership and contain change, using the smallest design
appropriate to the application.

## Architectural Judgment

Start from the relevant use cases, invariants, and existing implementation.
Identify who owns each authoritative state transition and which operations
depend on external systems.

Separate business rules, application orchestration, external integration,
and dependency wiring where their responsibilities differ. These do not
require a fixed folder structure, class hierarchy, or number of layers.

Source dependencies point toward business policy. When a use case needs
an external collaborator, define its contract on the consuming side and
implement that contract in an adapter.

Distinguish runtime calls from source dependencies:

    Runtime call:      Use case → Adapter
    Source dependency: Use case → Port ← Adapter

Keep framework, SDK, transport, and persistence types outside internal
contracts. Translate them at the boundary into meaningful application
inputs, outputs, and failures.

Introduce a port when it protects a meaningful boundary. Explain what
coupling or responsibility it addresses. Prefer cohesive modules with
small public interfaces over layers that merely forward calls.

## Apply to the Requested Work

For a design, trace a representative use case through the proposed
responsibilities. Show state ownership, effects, important contracts,
and dependency direction. Address consistency, retries, cancellation,
or duplicate handling where they affect the use case.

For a review, trace actual code paths and dependencies. Identify concrete
violations and their consequences before suggesting a restructuring.
Distinguish observed problems from possible future concerns.

For a refactor, preserve the required behavior and migrate through
coherent vertical slices. Follow existing architecture decisions unless
the task calls for changing them.

Read the specifications and code relevant to these decisions. Expand
inspection when a dependency or ownership question requires it.

## Evidence and Delivery

Ground the result in concrete modules and contracts when code exists.
Explain the recommended change, its tradeoffs, and how to verify it.
Use a diagram when it clarifies ownership or dependency direction.

For implementation and behavioral verification, use $tdd when available.
Architectural checks should enforce meaningful dependency boundaries;
directory layout alone does not demonstrate separation.

Complete the requested design, review, or implementation scope. Report
remaining uncertainty and limitations without expanding the work into
an unrelated architecture rewrite.
