# Payment Gateway Plan

## Goal

Keep the main `sKrt` application provider-agnostic and move payment-channel
volatility into a dedicated gateway layer.

Recommended topology:

```text
frontend -> sKrt backend -> payment provider adapter -> gateway/upstream
```

## Providers

- `easypay`
  - current fallback provider
  - suitable as a temporary bridge
- `jeepay`
  - intended stable self-hosted gateway target
  - scaffold is present in code, but production API wiring is intentionally left
    for the deployment stage

## Environment Variables

Common:

- `PAY_PROVIDER`
- `PAY_NOTIFY_URL`
- `PAY_RETURN_URL`
- `PAY_REFUND_ENABLED`
- `PAY_REFUND_ADMIN_KEY`

EasyPay:

- `EASYPAY_API_BASE`
- `EASYPAY_PID`
- `EASYPAY_KEY`
- `EASYPAY_RETURN_URL`

Jeepay:

- `JEEPAY_API_BASE`
- `JEEPAY_MCH_NO`
- `JEEPAY_APP_ID`
- `JEEPAY_API_KEY`
- `JEEPAY_NOTIFY_SIGN_SECRET`

## Database Notes

`pay_orders` now keeps generic provider fields in addition to legacy fields:

- `provider_order_id`
- `provider_transaction_id`

Legacy `payjs_*` columns are still written for backward compatibility with
existing deployments.

## Next Step

After a Jeepay instance is deployed and merchant parameters are available:

1. Implement the real Jeepay create-order API in
   `backend/services/payments/jeepay_provider.py`
2. Implement callback signature verification in the same provider
3. Switch `.env` to `PAY_PROVIDER=jeepay`
4. Keep EasyPay as rollback fallback until production is stable
