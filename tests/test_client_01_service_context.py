import pytest
from cryptojwt.key_jar import build_keyjar

from idpyoidc.client.service_context import ServiceContext
from idpyoidc.node import Unit

KEYDEFS = [
    {"type": "RSA", "key": "", "use": ["sig"]},
    {"type": "EC", "crv": "P-256", "use": ["sig"]},
]

KEYJAR = build_keyjar(KEYDEFS)

MINI_CONFIG = {
    "base_url": "https://example.com/cli",
    "key_conf": {"key_defs": KEYDEFS},
    "issuer": "https://op.example.com",
    "preference": {
        "response_types": ["code"]
    }
}


class TestServiceContext:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.unit = Unit()
        self.service_context = ServiceContext(config=MINI_CONFIG, upstream_get=self.unit.unit_get)

    def test_init(self):
        assert self.service_context

    def test_filename_from_webname(self):
        _filename = self.service_context.filename_from_webname("https://example.com/cli/jwks.json")
        assert _filename == 'jwks.json'

    def test_get_sign_alg(self):
        _alg = self.service_context.get_sign_alg("id_token")
        assert _alg is None

        self.service_context.claims.set_preference("id_token_signed_response_alg", "RS384")
        _alg = self.service_context.get_sign_alg("id_token")
        assert _alg == "RS384"

        self.service_context.claims.prefer = {}
        self.service_context.provider_info["id_token_signing_alg_values_supported"] = [
            "RS256",
            "ES256",
        ]
        _alg = self.service_context.get_sign_alg("id_token")
        assert _alg == ["RS256", "ES256"]

    def test_get_enc_alg_enc(self):
        _alg_enc = self.service_context.get_enc_alg_enc("userinfo")
        assert _alg_enc == {"alg": None, "enc": None}

        self.service_context.claims.set_preference("userinfo_encrypted_response_alg", "RSA1_5")
        self.service_context.claims.set_preference("userinfo_encrypted_response_enc",
                                                     "A128CBC+HS256")

        _alg_enc = self.service_context.get_enc_alg_enc("userinfo")
        assert _alg_enc == {"alg": "RSA1_5", "enc": "A128CBC+HS256"}

        self.service_context.claims.prefer = {}
        self.service_context.provider_info["userinfo_encryption_alg_values_supported"] = [
            "RSA1_5",
            "A128KW",
        ]
        self.service_context.provider_info["userinfo_encryption_enc_values_supported"] = [
            "A128CBC+HS256",
            "A128GCM",
        ]

        _alg_enc = self.service_context.get_enc_alg_enc("userinfo")
        assert _alg_enc == {"alg": ["RSA1_5", "A128KW"], "enc": ["A128CBC+HS256", "A128GCM"]}

    def test_get(self):
        assert self.service_context.base_url == MINI_CONFIG["base_url"]

    def test_set(self):
        self.service_context.set_preference("client_id", "number5")
        assert self.service_context.get_preference("client_id") == "number5"
