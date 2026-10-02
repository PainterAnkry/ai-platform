"""End-to-end smoke test of the Phase 1 API against a running backend."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000/api/v1"


def call(method, path, body=None, token=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


print("1) register")
st, reg = call("POST", "/auth/register",
               {"username": "demo", "email": "demo@example.com", "password": "demo12345"})
print("   ", st, reg)

print("2) duplicate register should 409")
st, dup = call("POST", "/auth/register",
               {"username": "demo", "email": "demo@example.com", "password": "demo12345"})
print("   ", st, dup)

print("3) login (username)")
st, tok = call("POST", "/auth/login", {"username": "demo", "password": "demo12345"})
print("   ", st, {k: (v[:24] + '...' if k == 'access_token' else v) for k, v in tok.items()})
access = tok["access_token"]

print("4) login (email)")
st, tok2 = call("POST", "/auth/login", {"username": "demo@example.com", "password": "demo12345"})
print("   ", st, "token issued:", bool(tok2.get("access_token")))

print("5) wrong password should 401")
st, bad = call("POST", "/auth/login", {"username": "demo", "password": "wrongpass"})
print("   ", st, bad)

print("6) /auth/me without token should 401")
st, anon = call("GET", "/auth/me")
print("   ", st, anon)

print("7) /auth/me with token")
st, me = call("GET", "/auth/me", token=access)
print("   ", st, me)

print("8) /dashboard with token")
st, dash = call("GET", "/dashboard", token=access)
print("   ", st, json.dumps(dash, ensure_ascii=False)[:300])

print("9) /dashboard with garbage token should 401")
st, g = call("GET", "/dashboard", token="not.a.jwt")
print("   ", st, g)

print("\nSMOKE TEST DONE")