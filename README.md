# Open Loyalty Operating System (OLOS)

**A vendor-neutral, privacy-preserving interoperability protocol for customer loyalty programs — with a bounded, offline-first transaction security model.**

> **Status: Open Protocol Proposal & Architecture Prototype.**
> This is not production software, certified infrastructure, or a finished standard. It is published to invite technical review from payments, security, and distributed-systems practitioners. See [Non-Goals](#non-goals-prototype) for what this explicitly does not claim.

## The Problem, in One Paragraph

The global loyalty industry runs on a trial balance that never closes — every program keeps isolated books, merchants carry hundreds of billions in unredeemed liabilities, and there's no common settlement rail connecting one program's value to another's. Payments solved this decades ago with shared network rails and clearing standards. Loyalty never has. OLOS proposes that missing interoperability and settlement layer, without asking merchants to pool programs into a shared currency or give up their margins.

## What's in This Repository

| File | What it covers | Start here if you want... |
|---|---|---|
| **[OLOS-Technical-Specification-v2_0_2.pdf](./OLOS-Technical-Specification-v2_0_2.pdf)** | The core protocol: message envelope, event registry, rules engine, settlement, identity, governance, escrow, resilience, certification | The full normative spec — **start here** |
| **[OLOS-V3-Protocol.pdf](./OLOS-V3-Protocol.pdf)** | Deep-dive prototype on offline double-spend mitigation, escrow-bounded authorization, and the security/threat model | The security architecture specifically |
| **[OLOS_White_Paper_v1_with_Flow_Diagram.pdf](./OLOS_White_Paper_v1_with_Flow_Diagram.pdf)** | Executive framing — the business case, stakeholder value, and high-level architecture | A non-technical overview |

*(`README.pdf` is an earlier draft of the technical specification and is superseded by the v2.0.2 spec above.)*

## How to Review This

If you're evaluating OLOS technically, the fastest path in is:

- **OLOS-0000–0002** (core spec) — envelope structure, event routing, and the architectural tenets everything else builds on
- **OLOS-0009** (core spec) — escrow and liquidity management, including Appendix B's headroom verification modes
- **V3 §17–20** — the explicit security model, threat analysis, and test suite for offline double-spending

## Author

OLOS is authored by Mark Angell as an independent protocol proposal, published by the OLOS Architecture Working Group to encourage industry discussion, technical evaluation, and collaboration.

---
