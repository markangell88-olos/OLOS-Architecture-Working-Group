# OLOS Protocol V3

**Offline-First Transaction Integrity • Escrow-Bounded Authorization • Asynchronous Settlement**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-0.3--prototype-orange)](https://github.com/markangell88-olos/OLOS-Architecture-Working-Group/releases)
[![Status](https://img.shields.io/badge/Status-Technical_Validation_Prototype-success)](https://github.com/markangell88-olos/OLOS-Architecture-Working-Group)

A vendor-neutral, privacy-preserving interoperability protocol for secure transaction processing in intermittently connected environments.

---

## Table of Contents

- [Overview](#overview)
- [Design Goals](#design-goals)
- [Core Architecture](#core-architecture)
- [Double-Spend Mitigation](#double-spend-mitigation)
- [Security Model & Threat Analysis](#security-model--threat-analysis)
- [Getting Started / Review Guide](#getting-started--review-guide)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

OLOS (Open Loyalty Operating System) proposes a practical dual-layer architecture for transaction integrity where continuous connectivity cannot be assumed.

**Central Principle**: Cryptography provides integrity and provenance. **Economic bounding** (escrow) + **deferred authoritative reconciliation** limits real-world exposure.

It explicitly does **not** claim to eliminate all offline double-spend risk through cryptography alone — instead, it combines bounded authorization, deterministic processing, and immutable settlement.

**Perfect for**:
- Loyalty programs
- Offline retail / travel payments
- Intermittent connectivity environments
- Privacy-first asset exchange

**[📄 Full White Paper with Diagrams](OLOS_White_Paper_v1_with_Flow_Diagram.pdf)** | **[📋 Protocol Spec (PDF)](README.pdf)**

---

## Design Goals

### Primary Goals
- Support secure transaction creation while fully disconnected
- Preserve cryptographic integrity across offline/online transitions
- Use deterministic fixed-point math for monetary values
- Separate transaction identity from transport identity
- Bound offline exposure via merchant/device escrow
- Enable reliable asynchronous reconciliation
- Provide explicit, auditable states and fault semantics

### Non-Goals (Prototype)
- Production certifications (PCI, EMV, etc.)
- Guaranteed elimination of every possible double-spend
- Full production consensus or HSM integration

See the full list in the [detailed README](https://github.com/markangell88-olos/OLOS-Architecture-Working-Group/blob/main/README.md#design-goals).

---

## Core Architecture

```mermaid
graph TD
    A[Trust Registry] -->|Signed Escrow Token| B[OLOS Edge / POS]
    B -->|Offline Rules + Local Escrow Decrement| C[Local Queue]
    C -->|Reconnect| D[Ingestion Plane]
    D -->|Validation + Long-term Lookup| E[Settlement Core]
    E -->|Atomic State Transition| F[Final Settlement]
