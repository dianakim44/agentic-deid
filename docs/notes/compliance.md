# Compliance notes — credentialed data and the model-serving path

Grounding for the paper's ethics section, and a record of what was actually measured
rather than assumed. Two corpora in this project are credentialed (`ko-surro`,
`es-carmen`) and one more will be if the portal returns (`en-n2c2`). Every LLM call in
this project therefore has to be defensible under a DUA that forbids sharing access
to the data.

No span surface form, document text, or DUA data path appears in this file.

---

## 1. What the DUA actually forbids

`es-carmen` ships the **PhysioNet Contributor Review Health Data License v1.5.0**
(read from `LICENSE.txt` in the release, 2026-08-06). The clauses that bear on model
serving:

- **Clause 1** — will not attempt to identify any individual or institution.
- **Clause 3** — "will not share access to PhysioNet restricted data with anyone
  else."
- **Clause 4** — will exercise all reasonable and prudent care to maintain the
  physical and electronic security of the data.
- **Clause 5** — will report any identifying information found.

Clause 3 is the operative one. The corpus content is separately CC-BY-SA-4.0, which is
permissive, but a permissive *content* licence layered under a credentialed *access*
licence does not relax the access terms. The two apply together, and clause 3 is not
waived by CC-BY-SA.

## 2. PhysioNet's own guidance on sending credentialed data to online services

PhysioNet published *"Responsible use of MIMIC data with online services like GPT"*
(2023-04-18). It is written about MIMIC-III / MIMIC-IV / MIMIC-CXR, so it applies
directly to `ko-surro` (MIMIC-derived) and is the closest available guidance for
`es-carmen` (same licence family, different contributor).

**Forbidden.** The post reads the credentialed DUA's third-party-sharing prohibition
as covering "sending it through APIs provided by companies like OpenAI, or using it in
online platforms like ChatGPT." So the public consumer surface is out, and so is any
API whose terms permit training on submitted data or routine human review.

**Named as acceptable paths**, with the conditions PhysioNet attaches to each:

| path | condition PhysioNet states |
|---|---|
| Azure OpenAI Service | must opt out of human review via Microsoft's form — you lack the right to let Microsoft process the data for abuse detection under the DUA you signed |
| **Amazon Bedrock** | private copies of a base model; data is not shared back to the base model for training |
| Gemini via Vertex AI | prompts and responses are not used to train the models; secure opt-outs for any Trusted Tester extras |
| Anthropic Claude | by default prompts and responses are not used for model training, and routine human review is not performed |

Two things to be precise about when citing this in the paper:

1. **The post does not use the phrase "zero data retention"**, and it does not
   mention BAAs or any other contract instrument. The only explicit affirmative
   requirement it states is the Azure human-review opt-out. Claiming that PhysioNet
   *requires* ZDR would overstate the source; the accurate claim is that PhysioNet
   names Bedrock as an acceptable path on the grounds of no-training and no-routine-
   human-review, and this project additionally verified that no invocation logging is
   in effect (§3).
2. The guidance is from 2023 and names services as they stood then. It is guidance,
   not a contract amendment — the DUA text is what binds. For anything load-bearing,
   PhysioNet's contact page is the authority.

**This project's path is Amazon Bedrock**, which is one of the four named. Verified:
`CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION=us-east-1`, model
`us.anthropic.claude-opus-5`.

## 3. Bedrock model-invocation logging — measured state

This matters because it is the one setting that can silently break the premise. If
model-invocation logging is on, Bedrock writes **the full prompt and completion** to
an S3 bucket or CloudWatch Logs group in the caller's account. Credentialed note text
in a prompt would then be persisted outside the intended location, and clause 4's
electronic-security obligation would be at issue even though no third party ever
trained on it. It is a per-region, per-account setting, so checking one region is not
enough.

`aws` CLI is **not installed** on this machine (`command not found: aws`), so the
check was made with boto3 1.43.54 / botocore 1.43.54 against
`bedrock:GetModelInvocationLoggingConfiguration`, which is the same API the CLI
command `aws bedrock get-model-invocation-logging-configuration` calls.

**Measured 2026-08-06:**

| region | `loggingConfig` |
|---|---|
| us-east-1 | `None` |
| us-east-2 | `None` |
| us-west-2 | `None` |
| eu-west-1 | `None` |
| eu-central-1 | `None` |
| ap-northeast-2 | `None` |

`None` means no logging destination is configured: no S3 bucket, no CloudWatch group,
and therefore no text or image data delivery. **Nothing was changed** — this is a
read-only observation, per instruction.

The first three regions are the ones that matter operationally: the
`us.anthropic.claude-opus-5` inference profile resolves to us-east-1, us-east-2 and
us-west-2, so a cross-region-routed request can be served from any of the three and
each needed checking independently. The other three were checked because the setting
is per-region and a stale configuration in an unused region would still be a finding.

**Not verified, and stated as such:** whether a CloudTrail trail records Bedrock API
activity. `cloudtrail:DescribeTrails` returned `AccessDeniedException` for this IAM
principal, so it could not be checked from here. This is a smaller exposure than
invocation logging — CloudTrail records management events and, for data events,
metadata rather than prompt bodies — but the paper should not claim it was verified.

**Re-check discipline.** This is a mutable account setting, not a property of Bedrock.
Anyone with `bedrock:PutModelInvocationLoggingConfiguration` can turn it on, and
nothing in this repository would notice. Re-run the check before any arm that sends
credentialed text, and append the date and result to this section rather than
overwriting the table — the paper's claim is about the state during the runs, so a
single current value is not sufficient evidence.

## 4. What still has to hold, beyond the serving path

Bedrock being an acceptable path is necessary and not sufficient. The remaining
exposures are local to this project:

- **Prompts are the leak surface.** A de-identification pipeline sends note text to
  the model by construction; that is the task. The safeguard is where the text goes,
  not whether it is sent.
- **Agent prompts and their logs.** `Arb` and `Aud` see candidate spans in context.
  Any transcript, cache, or debug dump of those prompts is note text at rest.
  `.gitignore` already blocks `**/*_raw_llm*.jsonl`, `**/critic_log.jsonl` and
  `**/*_predictions_with_text*` for this reason.
- **Published artefacts.** CLAUDE.md's rule — offsets, types and verdicts only, never
  source text — is the same obligation seen from the output side.
- **`es-carmen` is stricter than the other corpora**, because it is authentic patient
  narrative with synthetic surrogates substituted, not a synthetic construction like
  MEDDOCAN or GraSCCo. No surface form of any CARMEN-I span is quoted anywhere in this
  repository. See `data/README.md`.

## 5. Cite in the paper

- PhysioNet. *Responsible use of MIMIC data with online services like GPT.*
  2023-04-18.
- PhysioNet Contributor Review Health Data License v1.5.0 (2024), MIT Laboratory for
  Computational Physiology — the licence shipped with CARMEN-I.
- Model serving: Amazon Bedrock, `us.anthropic.claude-opus-5`, model-invocation
  logging verified unconfigured in all six regions checked on 2026-08-06 (§3).
