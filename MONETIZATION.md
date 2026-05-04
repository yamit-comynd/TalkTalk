# TalkTalk — Paid App Strategy

## Overview

Distribute as a **notarized DMG** (not App Store) with a **14-day free trial** and
**Ed25519 license keys** sold via Lemon Squeezy.

---

## Payment Processor

**Lemon Squeezy** (lemon.squeezy.com)

- Merchant of Record — they handle global VAT/sales tax, so you don't need a tax lawyer
- Generates license keys natively; webhook on purchase
- Straightforward indie pricing (~5% + 50¢ per transaction)

---

## License System

**Ed25519 asymmetric signing** — offline-capable, tamper-proof

```
Server side (secret):
  private key  →  signs license payload  →  license string

App side (public):
  public key embedded in binary  →  verifies license string offline
```

### Components to build

| File | Purpose |
|------|---------|
| `keygen.py` | Server-side: generate Ed25519 keypair, sign license payloads |
| `licensing.py` | Client-side: verify license string against embedded public key |
| Webhook endpoint | Cloudflare Worker or Vercel Edge Function — receives Lemon Squeezy purchase webhook, signs key, emails it to customer |
| Paywall UI in `app.py` | Modal or menu item: "Enter License Key" |

### License payload (example)

```json
{
  "email": "user@example.com",
  "order_id": "LS-12345",
  "product": "talktalk",
  "issued_at": "2026-04-04T00:00:00Z",
  "expires_at": null
}
```

Encode payload as JSON → sign with Ed25519 private key → base64url-encode
`{payload_b64}.{signature_b64}` = the license key the customer receives.

---

## Free Trial

- Store `~/.talktalk/first_launch` (ISO timestamp) on first run
- Trial duration: **14 days**
- After trial: show paywall — app stops recording until a valid license is entered
- License activates immediately, validated fully offline

---

## Purchase → Activation Flow

```
Customer buys on Lemon Squeezy
        │
        ▼
Lemon Squeezy fires webhook → Cloudflare Worker / Vercel
        │
        ▼
Worker signs license payload with Ed25519 private key
        │
        ▼
Worker emails signed key to customer
        │
        ▼
Customer opens TalkTalk → "Enter License Key" → pastes key
        │
        ▼
App verifies signature against embedded public key (offline)
        │
        ▼
License stored in ~/.talktalk/license  →  trial gate removed
```

---

## Distribution

`distribute.sh` already handles the full notarized DMG pipeline:

1. Build with PyInstaller
2. Sign with Developer ID (`--options runtime --timestamp`)
3. Wrap in DMG via `hdiutil`
4. Notarize via `xcrun notarytool submit --wait`
5. Staple ticket via `xcrun stapler staple`

Ship the notarized DMG directly (website download, not App Store).
Gatekeeper accepts it silently — no "unidentified developer" warning.

---

## Why Not the Mac App Store?

- App Store sandbox blocks `CGEventPost` (paste injection) and `CGEventTap` (hotkey)
- These are core to TalkTalk's UX — incompatible with sandboxing
- Input Monitoring is only available outside the sandbox

---

## Rough Pricing Ideas

| Tier | Price | Model |
|------|-------|-------|
| Lifetime | $29–49 | One-time purchase |
| Annual | $14–19/yr | Subscription via Lemon Squeezy |
| Free trial | 14 days | No credit card required |
