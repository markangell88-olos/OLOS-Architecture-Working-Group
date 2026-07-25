"""
OLOS v2.0.0 Reference Prototype (Fixed)
=======================================
Open Loyalty Operating System (OLOS)

This reference implementation contains bug fixes for timezone handling,
signature payload canonicalization, idempotency validation ordering,
and event routing.

SCOPE OF THIS FILE
------------------
This module implements the ONLINE envelope, signing/verification,
idempotency, event-routing, and settlement-recording path described in
the OLOS v2.0.0 Technical Specification (OLOS-0000 through OLOS-0005).

It does NOT implement any offline authorisation, escrow allocation, or
double-spend bounding logic. The escrow-token / bounded-offline-exposure
concept described in the OLOS V3 Protocol document (Offline-First
Transaction Integrity supplement) is a design sketch only at this time —
there is no reference implementation of it here or elsewhere in this
repository. Anyone reviewing this code for the offline/double-spend
question addressed in the OLOS Offline Double-Spend Supplement should
be aware that section is currently specification-only, not code-backed.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ============================================================================
# CONFIGURATION
# ============================================================================

OLOS_VERSION = "2.0.0"
MAX_ENVELOPE_BYTES = 100_000

# Specification-recommended replay window:
# 5 minutes in the past, 5 seconds into the future.
REPLAY_PAST_SECONDS = 300
REPLAY_FUTURE_SECONDS = 5

SUPPORTED_EVENTS = {
    "protocol.tx.authorized",
    "protocol.tx.captured",
    "protocol.tx.reversed",
    "protocol.tx.forwarded",
    "protocol.reward.issued",
    "protocol.reward.redeemed",
    "protocol.reward.clawedback",
    "protocol.settlement.manifest.generated",
    "protocol.identity.validated",
    "protocol.fault.dlq",
}


# ============================================================================
# ERROR CODES
# ============================================================================

class OLOSError(Exception):
    """Base protocol exception."""


class MalformedEnvelopeError(OLOSError):
    code = "OLOS_ERR_MALFORMED_ENVELOPE"


class ReplayWindowError(OLOSError):
    code = "OLOS_ERR_REPLAY_WINDOW_EXCEEDED"


class InvalidSignatureError(OLOSError):
    code = "OLOS_ERR_INVALID_SIGNATURE"


class IdempotencyError(OLOSError):
    code = "OLOS_ERR_IDEMPOTENCY_VIOLATION"


class UnknownProducerError(OLOSError):
    code = "OLOS_ERR_UNKNOWN_PRODUCER"


class UnsupportedEventError(OLOSError):
    code = "OLOS_ERR_UNSUPPORTED_EVENT"


class FloatingPointBannedError(OLOSError):
    code = "OLOS_ERR_FLOATING_POINT_BANNED"


class ClawbackNSFError(OLOSError):
    code = "OLOS_ERR_CLAWBACK_NSF"


# ============================================================================
# DETERMINISTIC INTEGER MATH
# ============================================================================

def scale_decimal_to_integer(amount: str, exponent: int) -> int:
    """
    Convert a decimal string to an integer fixed-point representation.

    Example:
        "45.00", exponent=2 -> 4500

    Floating-point input is deliberately not accepted.
    """
    if not isinstance(amount, str):
        raise FloatingPointBannedError(
            "Financial values must be supplied as decimal strings."
        )
    try:
        value = Decimal(amount)
    except InvalidOperation as exc:
        raise MalformedEnvelopeError(f"Invalid decimal amount: {amount}") from exc

    if not value.is_finite():
        raise MalformedEnvelopeError("Amount must be finite.")

    multiplier = Decimal(10) ** exponent
    return int((value * multiplier).to_integral_value(rounding=ROUND_HALF_EVEN))


def integer_to_decimal_string(amount: int, exponent: int) -> str:
    """Convert integer + exponent back to a decimal string."""
    value = Decimal(amount) / (Decimal(10) ** exponent)
    return f"{value:.{exponent}f}"


def calculate_reward(
    captured_amount: int,
    captured_exponent: int,
    target_exponent: int,
    base_multiplier_bps: int,
    modifiers_bps: list[int],
) -> int:
    """
    Deterministic integer reward calculation.

    10000 basis points = 1.0x

    The calculation:
        1. Normalize the captured amount.
        2. Multiply by base multiplier.
        3. Multiply by each modifier.
        4. Perform truncation exactly once at the end.
    """
    if target_exponent < captured_exponent:
        raise ValueError(
            "Target exponent must not be smaller than transaction exponent."
        )

    delta_exponent = target_exponent - captured_exponent
    normalized_amount = captured_amount * (10 ** delta_exponent)

    numerator = normalized_amount * base_multiplier_bps
    for modifier in modifiers_bps:
        numerator *= modifier

    denominator = 10_000 ** (1 + len(modifiers_bps))
    return numerator // denominator


# ============================================================================
# CANONICAL SERIALIZATION
# ============================================================================

def canonical_json(value: Any) -> bytes:
    """
    Deterministic JSON representation used by this prototype.

    NOTE:
    Production OLOS implementations MUST use RFC 8785 JCS.
    This function is intentionally labelled as a prototype boundary.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def get_signable_payload(envelope_or_data: dict) -> bytes:
    """Computes canonical JSON byte payload excluding the signature field itself."""
    if isinstance(envelope_or_data, dict) and "signature" in envelope_or_data:
        signable = {k: v for k, v in envelope_or_data.items() if k != "signature"}
        return canonical_json(signable)
    return canonical_json(envelope_or_data)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ============================================================================
# DEVICE / PRODUCER IDENTITY
# ============================================================================

@dataclass
class ProducerIdentity:
    producer_id: str
    merchant_id: str
    private_key: Ed25519PrivateKey
    public_key: bytes
    revoked: bool = False


def create_producer(producer_id: str, merchant_id: str) -> ProducerIdentity:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return ProducerIdentity(
        producer_id=producer_id,
        merchant_id=merchant_id,
        private_key=private_key,
        public_key=public_key,
    )


# ============================================================================
# OLOS MESSAGE ENVELOPE
# ============================================================================

def create_envelope(
    producer: ProducerIdentity,
    event_type: str,
    data: dict,
    correlation_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> dict:
    if event_type not in SUPPORTED_EVENTS:
        raise UnsupportedEventError(f"Unsupported event: {event_type}")

    message_id = str(uuid.uuid4())
    correlation_id = correlation_id or str(uuid.uuid4())

    if timestamp is None:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"

    envelope = {
        "olosVersion": OLOS_VERSION,
        "messageId": message_id,
        "correlationId": correlation_id,
        "eventType": event_type,
        "timestamp": timestamp,
        "producerId": producer.producer_id,
        "data": data,
    }

    # Ed25519 signature covers full envelope header metadata + data payload
    signature_bytes = producer.private_key.sign(get_signable_payload(envelope))
    envelope["signature"] = signature_bytes.hex()
    return envelope


# ============================================================================
# TRUST REGISTRY
# ============================================================================

class TrustRegistry:
    """
    Simplified in-memory Trust Registry.
    """

    def __init__(self):
        self._keys: Dict[str, bytes] = {}
        self._revoked: set[str] = set()

    def register(self, producer: ProducerIdentity):
        self._keys[producer.producer_id] = producer.public_key

    def revoke(self, producer_id: str):
        self._revoked.add(producer_id)

    def get_public_key(self, producer_id: str) -> bytes:
        if producer_id not in self._keys:
            raise UnknownProducerError(f"Unknown producer: {producer_id}")
        if producer_id in self._revoked:
            raise InvalidSignatureError(f"Producer revoked: {producer_id}")
        return self._keys[producer_id]


# ============================================================================
# MESSAGE VALIDATION
# ============================================================================

class EnvelopeValidator:
    REQUIRED_FIELDS = {
        "olosVersion",
        "messageId",
        "correlationId",
        "eventType",
        "timestamp",
        "producerId",
        "signature",
        "data",
    }

    def __init__(self, trust_registry: TrustRegistry):
        self.trust_registry = trust_registry

    def validate_structure(self, envelope: dict):
        if not isinstance(envelope, dict):
            raise MalformedEnvelopeError("Envelope must be an object.")

        raw = canonical_json(envelope)
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise MalformedEnvelopeError("Envelope exceeds 100 KB.")

        missing = self.REQUIRED_FIELDS - set(envelope.keys())
        if missing:
            raise MalformedEnvelopeError(f"Missing fields: {sorted(missing)}")

        if envelope["olosVersion"] != OLOS_VERSION:
            raise MalformedEnvelopeError("Unsupported OLOS version.")

        try:
            uuid.UUID(envelope["messageId"])
            uuid.UUID(envelope["correlationId"])
        except ValueError as exc:
            raise MalformedEnvelopeError(
                "messageId and correlationId must be UUIDs."
            ) from exc

        if not isinstance(envelope["data"], dict):
            raise MalformedEnvelopeError("data must be an object.")

    def validate_replay_window(self, envelope: dict):
        timestamp = envelope["timestamp"]
        try:
            timestamp_without_z = timestamp.rstrip("Z")
            parsed = time.strptime(timestamp_without_z[:19], "%Y-%m-%dT%H:%M:%S")
            # calendar.timegm correctly handles UTC timestamp parsing
            message_time = calendar.timegm(parsed)
        except ValueError as exc:
            raise MalformedEnvelopeError("Invalid timestamp.") from exc

        now = time.time()
        if (
            message_time < now - REPLAY_PAST_SECONDS
            or message_time > now + REPLAY_FUTURE_SECONDS
        ):
            raise ReplayWindowError("Envelope timestamp outside replay window.")

    def validate_signature(self, envelope: dict):
        public_key_bytes = self.trust_registry.get_public_key(envelope["producerId"])
        try:
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key.verify(
                bytes.fromhex(envelope["signature"]),
                get_signable_payload(envelope),
            )
        except Exception as exc:
            raise InvalidSignatureError(
                "Ed25519 signature verification failed."
            ) from exc

    def validate(self, envelope: dict):
        self.validate_structure(envelope)
        self.validate_replay_window(envelope)
        self.validate_signature(envelope)


# ============================================================================
# EVENT ROUTER
# ============================================================================

class EventRouter:
    def route(self, envelope: dict) -> str:
        event_type = envelope["eventType"]
        if event_type.startswith("protocol.tx."):
            return "transaction"
        if event_type.startswith("protocol.reward."):
            return "reward"
        if event_type.startswith("protocol.settlement."):
            return "settlement"
        if event_type.startswith("protocol.identity."):
            return "identity"
        if event_type == "protocol.fault.dlq":
            return "fault"
        raise UnsupportedEventError(f"No route for {event_type}")


# ============================================================================
# OLOS ENGINE
# ============================================================================

class OLOSEngine:
    def __init__(self):
        self.lock = Lock()
        self.trust_registry = TrustRegistry()
        self.validator = EnvelopeValidator(self.trust_registry)
        self.router = EventRouter()
        self.processed_messages: set[str] = set()
        self.transactions: Dict[str, dict] = {}
        self.rewards: Dict[str, dict] = {}
        self.reversals: Dict[str, dict] = {}
        self.settlement_obligations: list[dict] = []
        self.dlq: list[dict] = []

    def ingest(self, envelope: dict) -> Tuple[bool, str]:
        with self.lock:
            try:
                # Envelope validation runs before idempotency checks
                self.validator.validate(envelope)

                message_id = envelope.get("messageId")
                if message_id in self.processed_messages:
                    return True, "NO-OP: message already processed"

                route = self.router.route(envelope)
                result = self.dispatch(route, envelope)
                self.processed_messages.add(message_id)
                return True, result
            except OLOSError as exc:
                self.dlq.append(
                    {
                        "originalMessageId": envelope.get("messageId") if isinstance(envelope, dict) else None,
                        "errorCode": getattr(exc, "code", "OLOS_ERR_PROCESSING"),
                        "error": str(exc),
                    }
                )
                return False, f"{getattr(exc, 'code', 'OLOS_ERR_PROCESSING')}: {exc}"
            except Exception as exc:
                err_code = "OLOS_ERR_MALFORMED_ENVELOPE"
                self.dlq.append(
                    {
                        "originalMessageId": envelope.get("messageId") if isinstance(envelope, dict) else None,
                        "errorCode": err_code,
                        "error": f"Unexpected processing failure: {str(exc)}",
                    }
                )
                return False, f"{err_code}: {exc}"

    def dispatch(self, route: str, envelope: dict) -> str:
        event_type = envelope["eventType"]

        if route == "transaction":
            if event_type.endswith(".authorized"):
                return self.process_authorized(envelope)
            if event_type.endswith(".captured"):
                return self.process_captured(envelope)
            if event_type.endswith(".reversed"):
                return self.process_reversal(envelope)
            if event_type.endswith(".forwarded"):
                return self.process_forwarded(envelope)

        if route == "reward":
            if event_type.endswith(".issued"):
                return self.process_reward(envelope)
            if event_type.endswith(".redeemed"):
                return self.process_reward_redeemed(envelope)
            if event_type.endswith(".clawedback"):
                return self.process_clawback(envelope)

        if route == "settlement":
            return self.process_settlement(envelope)

        if route == "identity":
            return self.process_identity(envelope)

        if route == "fault":
            return self.process_fault(envelope)

        raise UnsupportedEventError(f"Unsupported event: {event_type}")

    # ------------------------------------------------------------------------
    # DOMAIN EVENT PROCESSORS
    # ------------------------------------------------------------------------

    def process_authorized(self, envelope: dict) -> str:
        data = envelope["data"]
        transaction_id = data["transactionId"]
        self.transactions[transaction_id] = {"status": "AUTHORIZED", "data": data}
        return f"AUTHORIZED: {transaction_id}"

    def process_captured(self, envelope: dict) -> str:
        data = envelope["data"]
        transaction_id = data["transactionId"]
        existing = self.transactions.get(transaction_id)
        if existing and existing["status"] == "CAPTURED":
            raise IdempotencyError("Transaction already captured.")
        self.transactions[transaction_id] = {"status": "CAPTURED", "data": data}
        return f"CAPTURED: {transaction_id}"

    def process_forwarded(self, envelope: dict) -> str:
        data = envelope["data"]
        inner_envelope = data.get("innerEnvelope")
        if not inner_envelope:
            raise MalformedEnvelopeError("Missing innerEnvelope in forwarded payload.")
        self.validator.validate_structure(inner_envelope)
        self.validator.validate_signature(inner_envelope)
        return "FORWARDED: inner envelope cryptographically validated"

    def process_reward(self, envelope: dict) -> str:
        data = envelope["data"]
        reward_id = data["rewardId"]
        if reward_id in self.rewards:
            raise IdempotencyError("Reward already issued.")
        self.rewards[reward_id] = data
        return f"REWARD ISSUED: {reward_id}"

    def process_reward_redeemed(self, envelope: dict) -> str:
        data = envelope["data"]
        reward_id = data.get("rewardId")
        if not reward_id:
            raise MalformedEnvelopeError("Missing rewardId in redemption data.")
        return f"REWARD REDEEMED: {reward_id}"

    def process_reversal(self, envelope: dict) -> str:
        data = envelope["data"]
        reversal_id = data["reversalId"]
        if reversal_id in self.reversals:
            raise IdempotencyError("Reversal already processed.")
        self.reversals[reversal_id] = data
        return f"REVERSAL RECORDED: {reversal_id}"

    def process_clawback(self, envelope: dict) -> str:
        data = envelope["data"]
        reward_id = data.get("originalRewardId")
        if not reward_id:
            raise MalformedEnvelopeError("Missing originalRewardId in clawback data.")
        if reward_id not in self.rewards:
            raise ClawbackNSFError("Original reward not found.")
        return f"CLAWBACK RECORDED: {reward_id}"

    def process_settlement(self, envelope: dict) -> str:
        data = envelope["data"]
        manifest_id = data["manifestId"]
        netting_matrix = data["nettingMatrix"]
        self.settlement_obligations.extend(netting_matrix)
        return f"SETTLEMENT MANIFEST SEALED: {manifest_id}"

    def process_identity(self, envelope: dict) -> str:
        data = envelope.get("data", {})
        producer_id = data.get("producerId") or envelope.get("producerId")
        return f"IDENTITY VALIDATED: {producer_id}"

    def process_fault(self, envelope: dict) -> str:
        data = envelope.get("data", {})
        error_code = data.get("errorCode", "UNKNOWN_FAULT")
        return f"FAULT ROUTED TO DLQ: {error_code}"


# ============================================================================
# TEST HELPERS
# ============================================================================

def create_transaction_data(transaction_id: str, merchant_id: str) -> dict:
    return {
        "transactionId": transaction_id,
        "terminalId": "term_pos_001",
        "merchantId": merchant_id,
        "transactionMetrics": {
            "authorizedAmount": 1480,
            "capturedAmount": 1480,
            "currency": "GBP",
            "exponent": 2,
        },
        "identityToken": "id_tok_demo_001",
    }


# ============================================================================
# DEMONSTRATION
# ============================================================================

def run_demo():
    print("=" * 72)
    print("OLOS v2.0.0 REFERENCE PROTOTYPE (CORRECTED)")
    print("=" * 72)

    engine = OLOSEngine()
    producer = create_producer(
        producer_id="urn:olos:merchant:demo_001",
        merchant_id="urn:olos:merchant:merchant_001",
    )
    engine.trust_registry.register(producer)

    print("\n1. DETERMINISTIC INTEGER MATH")
    amount = scale_decimal_to_integer("45.00", 2)
    print(f"45.00 -> integer={amount}, exponent=2")

    reward = calculate_reward(
        captured_amount=amount,
        captured_exponent=2,
        target_exponent=2,
        base_multiplier_bps=10000,
        modifiers_bps=[30000],
    )
    print(f"Reward calculation result: {reward}")

    print("\n2. AUTHORIZED TRANSACTION")
    transaction_id = "tx_" + uuid.uuid4().hex
    authorized = create_envelope(
        producer,
        "protocol.tx.authorized",
        create_transaction_data(transaction_id, producer.merchant_id),
    )
    ok, result = engine.ingest(authorized)
    print(ok, result)

    print("\n3. CAPTURED TRANSACTION")
    captured = create_envelope(
        producer,
        "protocol.tx.captured",
        create_transaction_data(transaction_id, producer.merchant_id),
        correlation_id=authorized["correlationId"],
    )
    ok, result = engine.ingest(captured)
    print(ok, result)

    print("\n4. DUPLICATE MESSAGE")
    ok, result = engine.ingest(captured)
    print(ok, result)

    print("\n5. REWARD ISSUANCE")
    reward_data = {
        "rewardId": "rwd_demo_001",
        "correlationId": authorized["correlationId"],
        "identityToken": "id_tok_demo_001",
        "merchantId": producer.merchant_id,
        "assetAllocation": {
            "assetType": "urn:olos:asset:points:demo",
            "amount": reward,
            "exponent": 0,
        },
        "ruleTrace": {
            "matchedCampaigns": ["camp_demo"],
            "executionNode": "urn:olos:network:node:demo",
        },
    }
    reward_event = create_envelope(producer, "protocol.reward.issued", reward_data)
    ok, result = engine.ingest(reward_event)
    print(ok, result)

    print("\n6. SETTLEMENT")
    settlement_data = {
        "manifestId": "stle_man_demo_001",
        "clearingCycle": {
            "epochId": "epoch_demo_001",
            "startTime": "2026-07-01T00:00:00.000Z",
            "endTime": "2026-07-01T23:59:59.000Z",
        },
        "nettingMatrix": [
            {
                "debtorMerchant": "urn:olos:merchant:merchant_001",
                "creditorMerchant": "urn:olos:merchant:merchant_002",
                "settlementMetrics": {
                    "netTransferAmount": 894500,
                    "currency": "EUR",
                    "exponent": 2,
                },
            }
        ],
        "verificationStatus": "SETTLEMENT_SEALED",
    }
    settlement_event = create_envelope(
        producer, "protocol.settlement.manifest.generated", settlement_data
    )
    ok, result = engine.ingest(settlement_event)
    print(ok, result)

    print("\n7. TAMPER TEST")
    tampered = dict(captured)
    tampered["messageId"] = str(uuid.uuid4())
    tampered["data"] = dict(tampered["data"])
    tampered["data"]["transactionMetrics"] = dict(
        tampered["data"]["transactionMetrics"]
    )
    tampered["data"]["transactionMetrics"]["capturedAmount"] = 999999
    ok, result = engine.ingest(tampered)
    print(ok, result)

    print("\n8. PROTOTYPE SUMMARY")
    print(f"Transactions stored: {len(engine.transactions)}")
    print(f"Rewards stored: {len(engine.rewards)}")
    print(f"Settlement obligations: {len(engine.settlement_obligations)}")
    print(f"DLQ entries: {len(engine.dlq)}")

    print("\n" + "=" * 72)
    print("END OF OLOS v2.0.0 PROTOTYPE")
    print("=" * 72)


if __name__ == "__main__":
    run_demo()
