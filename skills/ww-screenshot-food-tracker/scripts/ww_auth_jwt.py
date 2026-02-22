#!/usr/bin/env python3
"""Create a WW JWT via authenticate + authorize flow."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

from env_loader import load_dotenv


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _post_json(url: str, payload: dict[str, object], timeout: int, insecure: bool) -> dict[str, object]:
    req = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(insecure)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc


def _extract_id_token(location: str) -> str:
    fragment = urllib.parse.urlparse(location).fragment
    values = urllib.parse.parse_qs(fragment)
    tokens = values.get("id_token", [])
    if not tokens:
        raise RuntimeError("Authorize redirect did not include #id_token fragment")
    return tokens[0]


def _extract_token_id(payload: dict[str, object]) -> str:
    raw = payload.get("tokenId")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("tokenId")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    username = args.username or os.getenv("WW_USERNAME", "")
    password = args.password or os.getenv("WW_PASSWORD", "")
    tld = (args.tld or os.getenv("WW_TLD", "com")).strip()
    if not username or not password:
        raise RuntimeError("Set --username/--password or WW_USERNAME/WW_PASSWORD")

    auth_base = f"https://auth.weightwatchers.{tld}"
    cmx_base = f"https://cmx.weightwatchers.{tld}"
    authenticate_url = f"{auth_base}/login-apis/v1/authenticate"

    login_payload: dict[str, object] = {
        "username": username,
        "password": password,
        "rememberMe": False,
        "usernameEncoded": False,
        "retry": False,
    }
    login_resp = _post_json(authenticate_url, login_payload, args.timeout, args.insecure)
    if args.debug:
        print(json.dumps({"authenticate_response": login_resp}, ensure_ascii=True, indent=2), file=sys.stderr)

    token_id = _extract_token_id(login_resp)
    if not token_id:
        raise RuntimeError(
            "No tokenId returned from authenticate endpoint. "
            "Run with --debug and check for challenge/error fields."
        )

    nonce = secrets.token_hex(16)
    redirect_uri = urllib.parse.quote(f"{cmx_base}/auth", safe="")
    authorize_url = (
        f"{auth_base}/openam/oauth2/authorize"
        f"?response_type=id_token&client_id=webCMX&redirect_uri={redirect_uri}&nonce={nonce}"
    )

    req = urllib.request.Request(
        authorize_url,
        method="GET",
        headers={"Accept": "*/*", "Cookie": f"wwAuth2={token_id}"},
    )
    opener = urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=_ssl_context(args.insecure)),
    )
    try:
        opener.open(req, timeout=args.timeout)
        raise RuntimeError("Expected redirect response but got success without Location header")
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
        if not location:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Authorize request failed without Location header (HTTP {exc.code}): {body}"
            ) from exc

    jwt = _extract_id_token(location)
    if args.raw:
        print(jwt)
    else:
        print(json.dumps({"jwt": jwt, "authorization": f"Bearer {jwt}"}, ensure_ascii=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate WW JWT from username/password")
    parser.add_argument("--username", help="WeightWatchers account username/email (or WW_USERNAME)")
    parser.add_argument("--password", help="WeightWatchers account password (or WW_PASSWORD)")
    parser.add_argument("--tld", help="Country TLD, e.g. com, de, fr (or WW_TLD)")
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only)")
    parser.add_argument("--debug", action="store_true", help="Print auth responses to stderr for troubleshooting")
    parser.add_argument("--raw", action="store_true", help="Print only JWT token")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
