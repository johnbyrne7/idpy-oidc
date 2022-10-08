import base64
from typing import Callable
from typing import Optional
from unittest.mock import MagicMock

import pytest
from cryptojwt.jws.exception import NoSuitableSigningKeys
from cryptojwt.jwt import JWT
from cryptojwt.key_jar import KeyJar
from cryptojwt.key_jar import build_keyjar
from cryptojwt.utils import as_bytes
from cryptojwt.utils import as_unicode

from idpyoidc.defaults import JWT_BEARER
from idpyoidc.server import Server
from idpyoidc.server import do_endpoints
from idpyoidc.server.client_authn import BearerBody
from idpyoidc.server.client_authn import BearerHeader
from idpyoidc.server.client_authn import ClientSecretBasic
from idpyoidc.server.client_authn import ClientSecretJWT
from idpyoidc.server.client_authn import ClientSecretPost
from idpyoidc.server.client_authn import JWSAuthnMethod
from idpyoidc.server.client_authn import PrivateKeyJWT
from idpyoidc.server.client_authn import basic_authn
from idpyoidc.server.client_authn import verify_client
from idpyoidc.server.endpoint import Endpoint
from idpyoidc.server.exception import ClientAuthenticationError
from idpyoidc.server.exception import InvalidToken
from tests import CRYPT_CONFIG
from tests import SESSION_PARAMS

KEYDEFS = [
    {"type": "RSA", "key": "", "use": ["sig"]},
    {"type": "EC", "crv": "P-256", "use": ["sig"]},
]

KEYJAR = build_keyjar(KEYDEFS)


class Endpoint_1(Endpoint):
    name = "endpoint_1"


class Endpoint_2(Endpoint):
    name = "endpoint_2"


class Endpoint_3(Endpoint):
    name = "endpoint_3"

    def __init__(self, server_get: Callable, add_claims_by_scope: Optional[bool] = True, **kwargs):
        Endpoint.__init__(
            self,
            server_get,
            add_claims_by_scope=add_claims_by_scope,
            **kwargs,
        )
        # Add the issuer ID as an allowed JWT target
        self.allowed_targets.append("")


class Endpoint_4(Endpoint):
    name = "endpoint_4"


CONF = {
    "issuer": "https://example.com/",
    "grant_expires_in": 300,
    "httpc_params": {"verify": False},
    "endpoint": {
        "token": {
            "path": "token",
            "class": Endpoint_1,
            "kwargs": {
                "client_authn_method": [
                    "private_key_jwt",
                    "client_secret_jwt",
                    "client_secret_post",
                    "client_secret_basic",
                ]
            },
        },
        "authorization": {
            "path": "auth",
            "class": Endpoint_2,
            "kwargs": {"client_authn_method": ["bearer_header", "none"]},
        },
        "registration": {
            "path": "registration",
            "class": Endpoint_4,
            "kwargs": {},
        },
        "userinfo": {
            "path": "user",
            "class": Endpoint_3,
            "kwargs": {"client_authn_method": ["bearer_body"]},
        },
    },
    "template_dir": "template",
    "keys": {
        "private_path": "own/jwks.json",
        "key_defs": KEYDEFS,
        "uri_path": "static/jwks.json",
    },
    "claims_interface": {"class": "idpyoidc.server.session.claims.ClaimsInterface", "kwargs": {}},
    "session_params": SESSION_PARAMS,
    "token_handler_args": {
        "code": {"lifetime": 600, "kwargs": {"crypt_conf": CRYPT_CONFIG}},
        "token": {"lifetime": 600, "kwargs": {"crypt_conf": CRYPT_CONFIG}},
        "refresh": {"lifetime": 600, "kwargs": {"crypt_conf": CRYPT_CONFIG}},
    },
}

client_id = "client_id"
client_secret = "a_longer_client_secret"
# Need to add the client_secret as a symmetric key bound to the client_id
KEYJAR.add_symmetric(client_id, client_secret, ["sig"])


def get_client_id_from_token(endpoint_context, token, request=None):
    if "client_id" in request:
        if request["client_id"] == endpoint_context.registration_access_token[token]:
            return request["client_id"]
    return ""


class TestClientSecretBasic:
    @pytest.fixture(autouse=True)
    def setup(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        self.endpoint_context = server.endpoint_context
        server.endpoint = do_endpoints(CONF, server.server_get)
        self.method = ClientSecretBasic(server.server_get)

    def test_client_secret_basic(self):
        _token = "{}:{}".format(client_id, client_secret)
        token = as_unicode(base64.b64encode(as_bytes(_token)))

        authz_token = "Basic {}".format(token)

        assert self.method.is_usable(authorization_token=authz_token)
        authn_info = self.method.verify(authorization_token=authz_token)

        assert authn_info["client_id"] == client_id

    def test_wrong_type(self):
        assert self.method.is_usable(authorization_token="Foppa toffel") is False

    def test_csb_wrong_secret(self):
        _token = "{}:{}".format(client_id, "pillow")
        token = as_unicode(base64.b64encode(as_bytes(_token)))

        authz_token = "Basic {}".format(token)

        assert self.method.is_usable(authorization_token=authz_token)

        with pytest.raises(ClientAuthenticationError):
            self.method.verify(authorization_token=authz_token)


class TestClientSecretPost:
    @pytest.fixture(autouse=True)
    def create_method(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        self.endpoint_context = server.endpoint_context
        self.method = ClientSecretPost(server.server_get)

    def test_client_secret_post(self):
        request = {"client_id": client_id, "client_secret": client_secret}

        assert self.method.is_usable(request=request)
        authn_info = self.method.verify(request)

        assert authn_info["client_id"] == client_id

    def test_client_secret_post_wrong_secret(self):
        request = {"client_id": client_id, "client_secret": "pillow"}
        assert self.method.is_usable(request=request)
        with pytest.raises(ClientAuthenticationError):
            self.method.verify(request)


class TestClientSecretJWT:
    @pytest.fixture(autouse=True)
    def create_method(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        self.endpoint_context = server.endpoint_context
        self.method = ClientSecretJWT(server.server_get)

    def test_client_secret_jwt(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has at this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")
        _jwt.with_jti = True
        _assertion = _jwt.pack({"aud": [CONF["issuer"]]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        assert self.method.is_usable(request=request)
        authn_info = self.method.verify(request=request)

        assert authn_info["client_id"] == client_id
        assert "jwt" in authn_info


class TestPrivateKeyJWT:
    @pytest.fixture(autouse=True)
    def create_method(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        server.endpoint = do_endpoints(CONF, server.server_get)
        self.server = server
        self.endpoint_context = server.endpoint_context
        self.method = PrivateKeyJWT(server.server_get)

    def test_private_key_jwt(self):
        # Own dynamic keys
        client_keyjar = build_keyjar(KEYDEFS)
        # The servers keys
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])

        _jwks = client_keyjar.export_jwks()
        self.endpoint_context.keyjar.import_jwks(_jwks, client_id)

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="RS256")
        _jwt.with_jti = True
        _assertion = _jwt.pack({"aud": [CONF["issuer"]]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        assert self.method.is_usable(request=request)
        authn_info = self.method.verify(request=request)

        assert authn_info["client_id"] == client_id
        assert "jwt" in authn_info

    def test_private_key_jwt_reusage_other_endpoint(self):
        # Own dynamic keys
        client_keyjar = build_keyjar(KEYDEFS)
        # The servers keys
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])

        _jwks = client_keyjar.export_jwks()
        self.endpoint_context.keyjar.import_jwks(_jwks, client_id)

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="RS256")
        _jwt.with_jti = True
        _assertion = _jwt.pack(
            {"aud": [self.server.server_get("endpoint", "endpoint_1").full_path]}
        )

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        # This should be OK
        assert self.method.is_usable(request=request)
        self.method.verify(
            request=request, endpoint=self.server.server_get("endpoint", "endpoint_1")
        )

        # This should NOT be OK
        with pytest.raises(InvalidToken):
            self.method.verify(
                request=request, endpoint=self.server.server_get("endpoint", "authorization")
            )

        # This should NOT be OK because this is the second time the token appears
        with pytest.raises(InvalidToken):
            self.method.verify(
                request=request, endpoint=self.server.server_get("endpoint", "endpoint_1")
            )

    def test_private_key_jwt_auth_endpoint(self):
        # Own dynamic keys
        client_keyjar = build_keyjar(KEYDEFS)
        # The servers keys
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])

        _jwks = client_keyjar.export_jwks()
        self.endpoint_context.keyjar.import_jwks(_jwks, client_id)

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="RS256")
        _jwt.with_jti = True
        _assertion = _jwt.pack(
            {"aud": [self.server.server_get("endpoint", "endpoint_2").full_path]}
        )

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        assert self.method.is_usable(request=request)
        authn_info = self.method.verify(
            request=request,
            endpoint=self.server.server_get("endpoint", "endpoint_2"),
        )

        assert authn_info["client_id"] == client_id
        assert "jwt" in authn_info


class TestBearerHeader:
    @pytest.fixture(autouse=True)
    def create_method(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        server.endpoint = do_endpoints(CONF, server.server_get)
        self.server = server
        self.endpoint_context = server.endpoint_context
        self.method = BearerHeader(server.server_get)

    def test_bearerheader(self):
        authorization_info = "Bearer 1234567890"
        get_client_id_from_token = lambda *_: "client_id"
        assert self.method.verify(
            authorization_token=authorization_info,
            get_client_id_from_token=get_client_id_from_token,
        ) == {"token": "1234567890", "method": "bearer_header", "client_id": "client_id"}

    def test_bearerheader_wrong_type(self):
        authorization_info = "Thrower 1234567890"
        assert self.method.is_usable(authorization_token=authorization_info) is False


class TestBearerBody:
    @pytest.fixture(autouse=True)
    def create_method(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        server.endpoint = do_endpoints(CONF, server.server_get)
        self.server = server
        self.endpoint_context = server.endpoint_context
        self.method = BearerBody(server.server_get)

    def test_bearer_body(self):
        request = {"access_token": "1234567890"}
        assert self.method.verify(request) == {"token": "1234567890", "method": "bearer_body"}

    def test_bearer_body_no_token(self):
        request = {}
        with pytest.raises(ClientAuthenticationError):
            self.method.verify(request=request)


class TestJWSAuthnMethod:
    @pytest.fixture(autouse=True)
    def create_method(self):
        server = Server(conf=CONF, keyjar=KEYJAR)
        server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        server.endpoint = do_endpoints(CONF, server.server_get)
        self.server = server
        self.endpoint_context = server.endpoint_context
        self.method = JWSAuthnMethod(server.server_get)

    def test_jws_authn_method_wrong_key(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # Fake symmetric key
        client_keyjar.add_symmetric("", "client_secret:client_secret", ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")
        _assertion = _jwt.pack({"aud": [CONF["issuer"]]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        with pytest.raises(NoSuitableSigningKeys):
            self.method.verify(request=request, key_type="private_key")

    def test_jws_authn_method_aud_iss(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has a this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")
        # Audience is OP issuer ID
        aud = CONF["issuer"]
        _assertion = _jwt.pack({"aud": [aud]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        assert self.method.verify(request=request, key_type="client_secret")

    def test_jws_authn_method_aud_token_endpoint(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has a this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")

        # audience is OP token endpoint - that's OK
        aud = "{}token".format(CONF["issuer"])
        _assertion = _jwt.pack({"aud": [aud]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        assert self.method.verify(
            request=request,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
            key_type="client_secret",
        )

    def test_jws_authn_method_aud_not_me(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has at this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")

        # Other audiences not OK
        aud = "https://example.org"

        _assertion = _jwt.pack({"aud": [aud]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        with pytest.raises(InvalidToken):
            self.method.verify(request=request, key_type="client_secret")

    def test_jws_authn_method_aud_userinfo_endpoint(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has a this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")

        # audience is the OP - not specifically the user info endpoint
        _assertion = _jwt.pack({"aud": [CONF["issuer"]]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        assert self.method.verify(
            request=request,
            endpoint=self.server.server_get("endpoint", "endpoint_3"),
            key_type="client_secret",
        )


def test_basic_auth():
    _token = "{}:{}".format(client_id, client_secret)
    token = as_unicode(base64.b64encode(as_bytes(_token)))

    res = basic_authn("Basic {}".format(token))
    assert res


def test_basic_auth_wrong_label():
    _token = "{}:{}".format(client_id, client_secret)
    token = as_unicode(base64.b64encode(as_bytes(_token)))

    with pytest.raises(ClientAuthenticationError):
        basic_authn("Expanded {}".format(token))


def test_basic_auth_wrong_token():
    _token = "{}:{}:foo".format(client_id, client_secret)
    token = as_unicode(base64.b64encode(as_bytes(_token)))
    with pytest.raises(ValueError):
        basic_authn("Basic {}".format(token))

    _token = "{}:{}".format(client_id, client_secret)
    with pytest.raises(ValueError):
        basic_authn("Basic {}".format(_token))

    _token = "{}{}".format(client_id, client_secret)
    token = as_unicode(base64.b64encode(as_bytes(_token)))
    with pytest.raises(ValueError):
        basic_authn("Basic {}".format(token))


class TestVerify:
    @pytest.fixture(autouse=True)
    def create_method(self):
        self.server = Server(conf=CONF, keyjar=KEYJAR)
        self.server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        self.server.endpoint = do_endpoints(CONF, self.server.server_get)
        self.endpoint_context = self.server.server_get("context")

    def test_verify_per_client(self):
        self.server.endpoint_context.cdb[client_id]["client_authn_method"] = ["public"]

        request = {"client_id": client_id}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_4"),
        )
        assert res == {"method": "public", "client_id": client_id}

    def test_verify_per_client_per_endpoint(self):
        self.server.endpoint_context.cdb[client_id]["registration_endpoint_client_authn_method"] = [
            "public"
        ]
        self.server.endpoint_context.cdb[client_id]["token_endpoint_client_authn_method"] = [
            "client_secret_post"
        ]

        request = {"client_id": client_id}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_4"),
        )
        assert res == {"method": "public", "client_id": client_id}

        with pytest.raises(ClientAuthenticationError) as e:
            verify_client(
                self.endpoint_context,
                request,
                endpoint=self.server.server_get("endpoint", "endpoint_1"),
            )
        assert e.value.args[0] == "Failed to verify client"

        request = {"client_id": client_id, "client_secret": client_secret}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert set(res.keys()) == {"method", "client_id"}
        assert res["method"] == "client_secret_post"

    def test_verify_client_client_secret_post(self):
        request = {"client_id": client_id, "client_secret": client_secret}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert set(res.keys()) == {"method", "client_id"}
        assert res["method"] == "client_secret_post"

    def test_verify_client_jws_authn_method(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has a this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")
        # Audience is OP issuer ID
        aud = "{}token".format(CONF["issuer"])  # aud == Token endpoint
        _assertion = _jwt.pack({"aud": [aud]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}
        http_info = {"headers": {}}
        res = verify_client(
            self.endpoint_context,
            request,
            http_info=http_info,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert res["method"] == "client_secret_jwt"
        assert res["client_id"] == "client_id"

    def test_verify_client_bearer_body(self):
        request = {"access_token": "1234567890", "client_id": client_id}
        self.endpoint_context.registration_access_token["1234567890"] = client_id
        res = verify_client(
            self.endpoint_context,
            request,
            get_client_id_from_token=get_client_id_from_token,
            endpoint=self.server.server_get("endpoint", "endpoint_3"),
        )
        assert set(res.keys()) == {"token", "method", "client_id"}
        assert res["method"] == "bearer_body"

    # def test_verify_client_client_secret_post(self):
    #     request = {"client_id": client_id, "client_secret": client_secret}
    #     res = verify_client(
    #         self.endpoint_context, request, endpoint=self.server.server_get("endpoint",
    #         "endpoint_1"),
    #     )
    #     assert set(res.keys()) == {"method", "client_id"}
    #     assert res["method"] == "client_secret_post"

    def test_verify_client_client_secret_basic(self):
        _token = "{}:{}".format(client_id, client_secret)
        token = as_unicode(base64.b64encode(as_bytes(_token)))
        authz_token = "Basic {}".format(token)
        http_info = {"headers": {"authorization": authz_token}}

        res = verify_client(
            self.endpoint_context,
            request={},
            http_info=http_info,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert set(res.keys()) == {"method", "client_id"}
        assert res["method"] == "client_secret_basic"

    def test_verify_client_bearer_header(self):
        # A prerequisite for the get_client_id_from_token function
        self.endpoint_context.registration_access_token["1234567890"] = client_id

        token = "Bearer 1234567890"
        http_info = {"headers": {"authorization": token}}
        request = {"client_id": client_id}
        res = verify_client(
            self.endpoint_context,
            request,
            http_info=http_info,
            get_client_id_from_token=get_client_id_from_token,
            endpoint=self.server.server_get("endpoint", "endpoint_2"),
        )
        assert set(res.keys()) == {"token", "method", "client_id"}
        assert res["method"] == "bearer_header"


class TestVerify2:
    @pytest.fixture(autouse=True)
    def create_method(self):
        self.server = Server(conf=CONF, keyjar=KEYJAR)
        self.server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
        self.server.endpoint = do_endpoints(CONF, self.server.server_get)
        self.endpoint_context = self.server.server_get("context")

    def test_verify_client_jws_authn_method(self):
        client_keyjar = KeyJar()
        client_keyjar.import_jwks(KEYJAR.export_jwks(private=True), CONF["issuer"])
        # The only own key the client has a this point
        client_keyjar.add_symmetric("", client_secret, ["sig"])

        _jwt = JWT(client_keyjar, iss=client_id, sign_alg="HS256")
        # Audience is OP issuer ID
        aud = CONF["issuer"] + "token"
        _assertion = _jwt.pack({"aud": [aud]})

        request = {"client_assertion": _assertion, "client_assertion_type": JWT_BEARER}

        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert res["method"] == "client_secret_jwt"
        assert res["client_id"] == "client_id"

    def test_verify_client_bearer_body(self):
        request = {"access_token": "1234567890", "client_id": client_id}
        self.endpoint_context.registration_access_token["1234567890"] = client_id
        res = verify_client(
            self.endpoint_context,
            request,
            get_client_id_from_token=get_client_id_from_token,
            endpoint=self.server.server_get("endpoint", "endpoint_3"),
        )
        assert set(res.keys()) == {"token", "method", "client_id"}
        assert res["method"] == "bearer_body"

    def test_verify_client_client_secret_post(self):
        request = {"client_id": client_id, "client_secret": client_secret}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert set(res.keys()) == {"method", "client_id"}
        assert res["method"] == "client_secret_post"

    def test_verify_client_client_secret_basic(self):
        _token = "{}:{}".format(client_id, client_secret)
        token = as_unicode(base64.b64encode(as_bytes(_token)))
        authz_token = "Basic {}".format(token)
        http_info = {"headers": {"authorization": authz_token}}

        res = verify_client(
            self.endpoint_context,
            {},
            http_info=http_info,
            endpoint=self.server.server_get("endpoint", "endpoint_1"),
        )
        assert set(res.keys()) == {"method", "client_id"}
        assert res["method"] == "client_secret_basic"

    def test_verify_client_bearer_header(self):
        # A prerequisite for the get_client_id_from_token function
        self.endpoint_context.registration_access_token["1234567890"] = client_id

        token = "Bearer 1234567890"
        http_info = {"headers": {"authorization": token}}
        request = {"client_id": client_id}
        res = verify_client(
            self.endpoint_context,
            request,
            http_info=http_info,
            get_client_id_from_token=get_client_id_from_token,
            endpoint=self.server.server_get("endpoint", "endpoint_2"),
        )
        assert set(res.keys()) == {"token", "method", "client_id"}
        assert res["method"] == "bearer_header"

    def test_verify_client_authorization_none(self):
        # This is when it's explicitly said that no client auth method is allowed
        request = {"client_id": client_id}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_2"),
        )
        assert res["method"] == "none"
        assert res["client_id"] == "client_id"

    def test_verify_client_registration_public(self):
        # This is when no special auth method is configured
        request = {"redirect_uris": ["https://example.com/cb"], "client_id": "client_id"}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_4"),
        )
        assert res == {"client_id": "client_id", "method": "public"}

    def test_verify_client_registration_none(self):
        # This is when no special auth method is configured
        request = {"redirect_uris": ["https://example.com/cb"]}
        res = verify_client(
            self.endpoint_context,
            request,
            endpoint=self.server.server_get("endpoint", "endpoint_4"),
        )
        assert res == {"client_id": None, "method": "none"}


def test_client_auth_setup():
    class Mock:
        is_usable = MagicMock(return_value=True)
        verify = MagicMock(return_value={"method": "custom", "client_id": client_id})

    mock = Mock()
    mock.tag = "mock"
    conf = dict(CONF)
    conf["client_authn_methods"] = {"custom": MagicMock(return_value=mock)}
    conf["endpoint"]["registration"]["kwargs"]["client_authn_method"] = ["custom"]
    server = Server(conf=conf, keyjar=KEYJAR)
    server.endpoint_context.cdb[client_id] = {"client_secret": client_secret}
    server.endpoint = do_endpoints(CONF, server.server_get)

    request = {"redirect_uris": ["https://example.com/cb"]}
    res = verify_client(
        server.endpoint_context, request, endpoint=server.server_get("endpoint", "endpoint_4")
    )

    assert res == {"client_id": "client_id", "method": "custom"}
    mock.is_usable.assert_called_once()
    mock.verify.assert_called_once()
