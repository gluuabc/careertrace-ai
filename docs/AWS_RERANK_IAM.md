# Amazon Rerank IAM Review

CareerTrace uses Amazon Rerank 1.0 through a dedicated Bedrock Agent Runtime client in `us-west-2`. The application does not create, attach, detach, or broaden IAM policies.

Required model:

- Model ID: `amazon.rerank-v1:0`
- Model ARN: `arn:aws:bedrock:us-west-2::foundation-model/amazon.rerank-v1:0`

Least-privilege policy for an AWS administrator to review and apply manually if the effective identity does not already have the permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowAmazonRerank",
      "Effect": "Allow",
      "Action": "bedrock:Rerank",
      "Resource": "*"
    },
    {
      "Sid": "AllowLockedAmazonRerankModel",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:us-west-2::foundation-model/amazon.rerank-v1:0"
    }
  ]
}
```

Verification procedure:

1. Run `aws sts get-caller-identity` and record only the caller ARN.
2. If permitted, use IAM policy simulation for the two actions/resources above. Simulation is read-only and is not proof of runtime success.
3. Only after policy evaluation appears allowed, enable reranking and make one minimal bounded call.
4. If verification is unavailable or denied, keep `BEDROCK_RERANK_ENABLED=false`; CareerTrace records a warning and retains deterministic RRF order without fabricated rerank scores.

Never include access keys, secret keys, session tokens, or credential-file contents in diagnostics or reports.
