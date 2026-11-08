"""Connectivity probe: PayPal sandbox auth + disputes list, Nebius model list. Prints no secrets."""
import os

import httpx
from dotenv import load_dotenv

load_dotenv(".env")
r = httpx.post(
    "https://api-m.sandbox.paypal.com/v1/oauth2/token",
    auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
    data={"grant_type": "client_credentials"},
    timeout=30,
)
print("paypal token", r.status_code)
body = r.json()
scopes = body.get("scope", "").split()
print("relevant scopes:", [s for s in scopes if any(k in s for k in ("disput", "tracking", "payments"))])
h = {"Authorization": f"Bearer {body.get('access_token')}"}
d = httpx.get("https://api-m.sandbox.paypal.com/v1/customer/disputes", headers=h, timeout=30)
print("disputes", d.status_code, d.text[:800])
m = httpx.get(
    os.environ["NEBIUS_BASE_URL"] + "/models",
    headers={"Authorization": "Bearer " + os.environ["NEBIUS_API_KEY"]},
    timeout=30,
)
print("nebius models", m.status_code)
try:
    ids = [x["id"] for x in m.json()["data"]]
    print([i for i in ids if "emotron" in i.lower()] or ids[:20])
except Exception:
    print(m.text[:300])
