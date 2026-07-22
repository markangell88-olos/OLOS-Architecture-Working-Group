# OLOS: Open Loyalty Protocol (v2.0.0 Draft)

> **A deterministic, cryptographically secure open standard for multi-merchant loyalty clearing and net settlement.**

![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)
![Status](https://img.shields.io/badge/Status-Draft_RFC-orange.svg)

---

## 💡 What is OLOS?

OLOS (Open Loyalty Operating Standard) is an open-source protocol designed to bridge loyalty programs across independent merchants and Point-of-Sale (POS) systems. 

By replacing centralized database syncs with **cryptographically signed transaction envelopes** and **deterministic basis-point arithmetic**, OLOS enables instant cross-brand reward issuing and automated multi-party netting without back-office reconciliation overhead.

---

## ⚡ Key Architectural Features

* **Zero Rounding Drift:** Strict integer basis-point math eliminates penny-tracking errors across millions of transactions.
* **Edge Non-Repudiation:** POS terminals sign transaction envelopes locally using **Ed25519 signatures**, creating an immutable audit trail.
* **Automated Net Settlement:** Generates deterministic netting matrices across epoch cycles, allowing merchants to settle balances in bulk (similar to ISO 20022 / interbank clearing).
* **Offline Resilient:** Supports offline envelope forwarding with Dead Letter Queue (DLQ) fault isolation for unreliable POS connections.

---

## 🏗️ Protocol Architecture

```mermaid
graph TD
    A[POS Terminal / App] -->|1. Sign Event Ed25519| B(OLOS Edge Envelope)
    B -->|2. Validate & Forward| C{OLOS Settlement Engine}
    C -->|3. Epoch Batch Netting| D[Bilateral Settlement Matrix]
    D -->|4. Clear Balances| E[Merchant A & B Ledgers]
