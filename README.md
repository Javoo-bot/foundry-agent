# Fleet Agent

An AI agent that answers questions about a synthetic B2B vehicle fleet by writing SQL
against Databricks Unity Catalog, running on Microsoft Foundry.

**The published artefact is not the agent. It is the agent's evaluation.**

A demo proves nothing a reader can check — it can be staged, and they have no way to
know. What is published here is the measurement: which questions were asked, which ones
failed, the SQL each answer was built from, what it cost and how long it took. Every
figure on the page comes from a GitHub Actions run whose log is public.

Runs so far have scored between **27 and 31 out of 32**, with nothing changed between
some of them but the sampling. Arithmetic (20/20) and refusals (6/6) hold steady; the
variance lives entirely in *not answerable from this data*, which ranges 3/6 to 5/6.

A single number is not the result. The spread is, and so is which categories move.

That last number is the interesting one. See [What it gets wrong](#what-it-gets-wrong).

---

## How it is wired

```
GitHub Actions (nightly + on push)
    |
    |-- API key, as a repository secret
    v
Microsoft Foundry ...... project Responses endpoint, gpt-4.1-mini
    |                    tool: query_fleet_data(sql, purpose)
    |
    v
read-only SQL guard .... rejects anything that is not a single SELECT
    |
    v
Databricks ............. Statement Execution API -> serverless SQL warehouse
    |                    Unity Catalog: gishub.fleet, 183k rows
    v
site/data/*.json ....... committed to this repo, rendered by GitHub Pages

Cloudflare Worker ...... the page's chat proxy: GitHub Pages is static and
    + D1               cannot hold a credential, so the key lives in the
                       Worker and spend is capped in three places
```

## The data

Five tables across the vehicle life cycle: deliveries, charging sessions, diagnostic
trouble codes, warranty claims, and the fleet operators who hold the contracts. All
invented — no real vehicle, customer or claim is represented, and nothing derives from a
Volkswagen dataset.

Two things make it worth querying rather than just counting:

**There is a finding planted in it.** Model year 2023 BEVs carry 8,289 EUR of warranty
cost per vehicle against a ~1,200 EUR baseline, concentrated in high-voltage battery
claims. It is recoverable by aggregation, so the agent finds it rather than being told.

**The codes are physically coherent.** An ICE van has no high-voltage battery and cannot
raise P0AA6; a BEV has no exhaust and cannot raise P0420. Sampling diagnostic codes
uniformly is the tell that a dataset was thrown together rather than modelled.

The generator is seeded, so `python data/generate.py` reproduces it exactly. That matters
because the golden answers are computed from this dataset: if it drifts, the scores stop
meaning anything.

## The evaluation

Sixteen cases, each asked in English and in Spanish, in three kinds:

| Kind | Cases | What passing means |
|---|---|---|
| `aggregate` | 10 | reached the right figure, within tolerance |
| `unanswerable` | 3 | said the data cannot answer it, and invented nothing |
| `refuse` | 3 | declined: prompt extraction, a destructive write, personal data |

A suite that only checks arithmetic rewards an agent that answers everything,
confidently, including what it has no business answering.

Numeric cases are scored deterministically — a judge there would add cost and variance
for nothing. Behavioural cases go to an LLM judge against a written rubric, and its
one-line reasoning is published beside each case so it can be disagreed with. The judge
is the weakest link in any eval suite; hiding it would not make that less true.

## What it gets wrong

**The agent is unreliable at admitting it cannot answer — between 3/6 and 5/6 across
runs.** Asked for average driver age,
which the schema has no column for, it computed vehicle age from `model_year` and
presented it as an answer. Asked about sales in China, a market the data has never heard
of, it replied "0 vehicles". Both are the same failure: a confident number is more
dangerous than a refusal, because it looks like an answer.

**It is inconsistent across languages.** Case A02 passes in English and fails in Spanish,
where the model still translates a categorical value instead of using the literal the
column holds.

Neither is fixed by prompt-tuning until it looks good. They are in the suite so the next
change can be measured against them.

## The chat on the page

The published page lets a visitor ask about the engineer who built it. It is the same
Foundry deployment with no tools, grounded in one profile, and held to the rule the fleet
agent is held to: answer from the source or say it cannot. Asked something the profile
does not cover, it declines rather than inventing.

It exists because GitHub Pages is static and cannot hold a credential, so the key sits in
a Cloudflare Worker instead. An open endpoint spending a personal Azure credit is capped
three independent ways — a global daily ceiling, a per-IP hourly limit, and a hard cap on
generated tokens — with the remaining budget returned in response headers, because a
visitor who hits a limit should be able to tell it is a budget and not a bug.

Counters live in D1 rather than KV. KV is eventually consistent, so two simultaneous
requests can both read the same count and each write back one more than it, which is
exactly the race a spend limit must not have.

## Security

The tool refuses anything that is not a single read, at the boundary rather than in the
prompt. Instructions that say "only run SELECT" are a request to a model; a parser that
rejects everything else is a control. It is tested like one — 18 cases in
`agent/test_guard.py`, run in CI before the evaluation is allowed to start.

A finding from the first full run: Azure's content filter rejected the *grading* call for
the prompt-extraction case, classifying it as a jailbreak attempt and taking the run
down. The filter was right. Adversarial cases now reach the judge described rather than
quoted.

## Running it

```bash
pip install -r requirements.txt
python data/generate.py                 # reproduce the dataset
python databricks/smoke.py              # can CI reach the warehouse?
python databricks/load.py               # build the Unity Catalog tables
python agent/smoke_azure.py             # can CI reach Foundry?
python evals/run.py                     # the golden set, both languages
```

Configuration is read from the environment, or from a `.env` beside this file:

```
AZURE_AI_PROJECT_ENDPOINT       https://<resource>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME  the name given to the deployment, not the model
AZURE_MODEL_TOKEN               the API key from the Foundry project overview
DATABRICKS_HOST                 the workspace domain
DATABRICKS_TOKEN                a workspace personal access token
DATABRICKS_WAREHOUSE_ID         the last segment of the SQL warehouse HTTP path
```

## Choices worth arguing with

**Why the Responses endpoint and not the Agents service.** The Agents service needs an
Entra token — `AIProjectClient` types its credential as `TokenCredential` and the library
documents Entra as the only supported method. The same project's OpenAI-compatible
Responses endpoint accepts the portal API key, tools and all, which is what makes CI
possible with one secret instead of an app registration. The cost is real and it is
named: conversation state, retries and the tool loop are maintained here rather than by
the service. Moving is an auth change, not a rewrite.

**Why a personal access token for Databricks.** Free Edition has no account console and
no account-level APIs, so a service principal cannot be created. In an enterprise
workspace this would be a service principal with OAuth M2M and no long-lived string.

**What this is not evidence of.** One agent over five tables is not a platform. There is
no Spark here, no streaming, no job orchestration, and no fleet of agents across
departments. The gap between this and production is ramp-up, not a rewrite, and it is
better stated than implied.

---

Built by [Javier Lobato](https://github.com/Javoo-bot). Synthetic data throughout.
