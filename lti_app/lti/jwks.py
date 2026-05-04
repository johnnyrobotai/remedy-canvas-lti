"""Tool JWKS endpoint — serves the tool's public keys for Canvas to verify service tokens."""

import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwcrypto import jwk

from lti_app.lti.tool_conf import FirestoreToolConf


def get_tool_jwks(tool_conf: FirestoreToolConf) -> dict:
    """Return JWKS containing all tool public keys across registrations."""
    keys = []
    seen = set()

    for reg in tool_conf.get_all_registrations_dicts():
        pem = reg.get("tool_public_key_pem", "")
        if not pem:
            # Derive public key from private key
            private_pem = reg.get("tool_private_key_pem", "")
            if not private_pem:
                continue
            pem = _public_from_private(private_pem)

        if pem in seen:
            continue
        seen.add(pem)

        try:
            key = jwk.JWK.from_pem(pem.encode())
            key_dict = json.loads(key.export_public())
            key_dict["use"] = "sig"
            key_dict["alg"] = "RS256"
            keys.append(key_dict)
        except Exception:
            continue

    return {"keys": keys}


def _public_from_private(private_pem: str) -> str:
    """Extract public key PEM from private key PEM."""
    private_key = serialization.load_pem_private_key(
        private_pem.encode(), password=None
    )
    public_key = private_key.public_key()
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
