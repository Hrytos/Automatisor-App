# Stripe Customer Portal Payment Method Guard — Recent Findings

## Context

We reviewed the Stripe Customer Portal settings for a credit-based / pay-as-you-use billing model. The goal was to make sure users cannot consume paid credits unless they have a valid payment method available.

The current dashboard screen showed the **Payment methods** section enabled, but did **not** show a separate toggle named:

> Require customers to have at least one payment method on file

## Main Finding

The missing toggle is **not a blocker**.

In the current Stripe Customer Portal UI, the visible setting you need is:

> **Payment methods** → Allow customers to update payment methods

This allows users to add or update their payment methods through the hosted Stripe Customer Portal.

Stripe’s support documentation says that for customers with an active subscription, the portal requires them to maintain at least one payment method so the subscription can be charged.

Reference: https://support.stripe.com/questions/billing-customer-portal

However, for a **credit-based / usage-based app**, especially if credits are consumed before final invoicing/payment, you should not depend only on the portal behavior.

## Recommended Setup

### 1. Keep Customer Portal payment methods enabled

In Stripe Dashboard:

1. Go to **Settings**
2. Go to **Billing**
3. Open **Customer portal**
4. Expand **Payment methods**
5. Keep **Payment methods** enabled
6. Keep the payment configuration as **Default** unless you have a custom payment method configuration
7. Save the configuration

The dropdown named **Payment configuration** controls which payment methods are available, such as card or other supported payment types. It is not the same as the “must keep one payment method” guard.

## Actual Reliable Guard for Your Use Case

For your app, the most reliable guard should live in the backend.

Before allowing any billable credit usage, your backend should verify that the Stripe customer has a usable payment method.

### Check 1: Customer default payment method

```ts
const customer = await stripe.customers.retrieve(stripeCustomerId);

const hasDefaultPaymentMethod =
  typeof customer !== "string" &&
  !!customer.invoice_settings?.default_payment_method;

if (!hasDefaultPaymentMethod) {
  throw new Error("Please add a payment method before using credits.");
}
```

Stripe’s `invoice_settings.default_payment_method` is the customer-level default payment method used for future invoices.

Reference: https://docs.stripe.com/api/customers/object

### Check 2: Attached card/payment method exists

```ts
const paymentMethods = await stripe.paymentMethods.list({
  customer: stripeCustomerId,
  type: "card",
  limit: 1,
});

if (paymentMethods.data.length === 0) {
  throw new Error("No payment method found.");
}
```

Reference: https://docs.stripe.com/api/payment_methods/list

## Add Payment Method Flow

If the user does not have a payment method, redirect them to one of these flows:

### Option A: Stripe Customer Portal

Use the Customer Portal and optionally deep-link them into the payment method update flow.

Stripe’s portal session API supports `flow_data.type = payment_method_update`. In that flow, the customer can add a new payment method, and Stripe sets it as `customer.invoice_settings.default_payment_method`.

Reference: https://docs.stripe.com/api/customer_portal/sessions/create

### Option B: Checkout Setup Mode / SetupIntent

Use Stripe Checkout in setup mode or a SetupIntent if you want a more custom in-app payment method collection experience.

This is useful if you want the user to add a card before entering the product or before consuming any credits.

Reference: https://docs.stripe.com/payments/checkout/subscriptions/update-payment-details

## Final Recommendation

For the Hrytos credit-based billing flow, use this layered approach:

1. **Stripe Customer Portal** for users to manage and update payment methods.
2. **Backend guard before every billable action** to block credit usage when no payment method exists.
3. **Webhook sync** to keep your local database aligned when billing details change.
4. **Redirect to Customer Portal or SetupIntent flow** when payment method is missing.

## Practical Implementation Rule

Before consuming credits, always run:

```ts
if (!hasDefaultPaymentMethod && !hasAttachedPaymentMethod) {
  blockUsageAndRedirectToBilling();
}
```

This keeps the system safe even if Stripe’s dashboard UI changes or the Customer Portal does not expose a specific toggle.

## Suggested User-Facing Error Message

```txt
Please add a payment method before using credits.
```

Or slightly better for the app UI:

```txt
Add a payment method to continue using credits. You won’t be charged until usage is billed.
```

## Conclusion

The Stripe dashboard screen is correctly configured as long as **Payment methods** is enabled in the Customer Portal. The missing “Require customers to have at least one payment method on file” setting is not a blocker for this implementation.

For a pay-as-you-use credit system, the actual enforcement should happen in your backend before allowing credit-consuming actions.
