# Response Templates

Use these templates as starting points. Personalize each one with the customer's name and specific details from their account/order.

---

## Greeting

```
Hi {customer_name}, thanks for reaching out! I'm here to help.
I've pulled up your account — let me take a look at what's going on.
```

## Order Status — Processing

```
Hi {customer_name}, I can see your order {order_id} is currently being processed.
It was placed on {placed_at}. Orders typically ship within 2-3 business days.
I don't have a tracking number yet, but you'll receive an email as soon as it ships.
Is there anything else I can help with?
```

## Order Status — Shipped

```
Great news, {customer_name}! Your order {order_id} has shipped via {carrier}.
Here's your tracking number: {tracking_number}
You can track it at the carrier's website. Estimated delivery is within 3-5 business days from the ship date.
```

## Refund Approved

```
I've gone ahead and processed a refund of ${amount} for order {order_id}.
Refund ID: {refund_id}
You should see the credit back to your original payment method by {estimated_credit}.
I'm sorry for the inconvenience, and I hope we can make it right!
```

## Refund Denied — Past Window

```
I understand your frustration, {customer_name}. Unfortunately, order {order_id} was delivered on {delivered_at}, which is outside our 30-day refund window.
However, I'd like to offer you a store credit as a goodwill gesture. Would that work for you?
```

## Escalation Notice

```
I want to make sure you get the best possible help, {customer_name}.
I'm going to connect you with a specialist from our team who can take a closer look at this.
I've created ticket {ticket_id} with all the details so you won't need to repeat yourself.
Someone will follow up with you shortly — you'll receive an email confirmation.
```

## Resolution Confirmation

```
Glad we could get this sorted out, {customer_name}!
Here's a quick summary of what was done:
- {resolution_summary}

Your ticket {ticket_id} has been resolved and closed.
If anything else comes up, don't hesitate to reach out. Have a great day!
```

## Satisfaction Check

```
Just to make sure — does this resolve your issue, or is there anything else I can help with?
```
