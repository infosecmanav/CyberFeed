# Cyber Feed

Cyber Feed collects cybersecurity updates from government agencies, vendor advisories, threat research teams, and selected news publishers. It produces evidence-linked PDF and text reports for scheduled Discord delivery or local review.

Scheduled reports run through GitHub Actions. An optional Discord bot supports on-demand reports and per-user format preferences.

> **AI is required for report generation and delivery.** Configure your provider key, model, and API settings. Reports use the Cyber Feed black-and-blue layout, with a summary, detailed explanation, and references for each update. Invalid or unavailable AI output stops delivery; it is never silently replaced with a non-AI report.

## Contents

- [Scheduled Discord deployment](#scheduled-discord-deployment)
- [Report schedule](#report-schedule)
- [Configuration](#configuration)
- [Sources and evidence handling](#sources-and-evidence-handling)
- [Local installation](#local-installation)
- [AI report generation](#ai-report-generation)
- [Interactive Discord bot](#interactive-discord-bot)
- [Security and operational considerations](#security-and-operational-considerations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Scheduled Discord deployment

### Prerequisites

- A GitHub account and a fork of this repository.
- A Discord channel where you have permission to create webhooks.
- GitHub Actions enabled in your fork.

This deployment requires your AI provider key and Discord webhook. It does not require a continuously running local computer, a Discord bot token, or a personal GitHub API key. GitHub supplies the workflow token used for checkout and artifacts.

### 1. Fork the repository

Fork the repository into your own account. Open the fork's **Actions** tab and enable workflows if prompted. Repository secrets are not copied from the original repository.

### 2. Create the Discord webhook

In the destination channel, open **Channel Settings → Integrations → Webhooks**. Create a webhook and copy its URL. Treat the URL as a credential: anyone holding it can post to that channel.

### 3. Configure the repository secret

In your fork, open **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | The webhook URL for your destination channel |
| `AI_API_KEY` | Your selected AI provider’s API key |

Create repository Actions variables `AI_PROVIDER` and `AI_MODEL` using the provider configuration below. For a custom OpenAI-compatible service, also set `AI_BASE_URL`.

For scheduled text delivery, also create an Actions **variable** named `BRIEFING_FORMAT` with the value `text`. The default is `pdf`.

### 4. Validate delivery

Open **Actions → Scheduled Discord Briefings → Run workflow**. Select the report period and format, then run the workflow.

Confirm that:

- The workflow completes successfully.
- The report arrives in the configured channel.
- Any coverage warnings are visible in the report.

Before collecting feeds, the configuration step logs the effective provider, model ID, and API base URL without the key. For Gemini's native interface it retrieves the exact model's metadata and checks that it advertises `generateContent`. In the default `auto` mode, a metadata 404 or temporary server failure is reported and deferred to generation, which can try Google's second interface. In `native` mode, a metadata 404 stops the workflow with `model not found` and the model ID. Direct `openai-compatible` mode skips native metadata lookup. Successful metadata lookup does not establish that generation will succeed.

Manual runs begin immediately; delivery follows source collection, AI generation, validation, and rendering. Free-tier pacing can add several minutes. Generated JSON, text, and PDF files are retained as a downloadable workflow artifact for seven days.

## Report schedule

All delivery times use **America/Toronto**, with daylight saving adjustments.

| Report | Delivery time | Coverage window |
| --- | --- | --- |
| Daily | Every day at **8:50 AM** | Previous local 8:50 AM through the current 8:50 AM, end exclusive |
| Weekly | Friday at **8:30 AM** | Previous Friday 00:00 through Thursday, ending Friday 00:00 exclusive |

Friday includes both deliveries. Manual daily runs use the last completed 8:50 AM cutoff; manual weekly runs use the most recently completed Friday–Thursday period.

Schedules are defined in [.github/workflows/briefing.yml](.github/workflows/briefing.yml), with timezone selection and coverage boundaries in [cyberbrief/schedule.py](cyberbrief/schedule.py). Changes to delivery times must keep those definitions consistent.

GitHub scheduled workflows run from the default branch and may be delayed. Public-repository schedules may be disabled after 60 days of inactivity. Re-enable the workflow in Actions when necessary.

## Configuration

| Setting | Location | Purpose |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | GitHub Actions repository secret | Required for scheduled channel delivery |
| `BRIEFING_FORMAT` | GitHub Actions repository variable | `pdf` or `text`; default `pdf` |
| `DISCORD_BOT_TOKEN` | Local or hosting process environment | Required only for the interactive bot |
| `CYBERBRIEF_STATE_DIR` | Local or hosting process environment | Persistent directory for bot preferences; default `.state` in the checkout |
| `AI_API_KEY` | Actions secret or local process environment | Required AI provider key |
| `AI_PROVIDER` | Actions variable or local process environment | Required provider adapter |
| `AI_MODEL` | Actions variable or local process environment | Required model identifier available to your account |
| `AI_BASE_URL` | Actions variable or local process environment | Required for `openai-compatible`; optional override for Groq/OpenRouter; ignored by native provider selections `gemini` and `openai` |
| `AI_MAX_OUTPUT_TOKENS` | Actions variable or local process environment | Response budget per batch; default 4096 for Groq, 16384 for other providers, subject to model/account limits |
| `AI_BATCH_SIZE` | Actions variable or local process environment | Updates per request, from 1 to 10; default 1 for Groq and 10 for other providers |
| `AI_JSON_MODE` | Actions variable or local process environment | `true` by default; set `false` only if the model cannot accept native JSON mode |
| `AI_GEMINI_TRANSPORT` | Actions variable or local process environment | Gemini only: `auto` (default), `native`, or `openai-compatible` |
| `AI_REPAIR_ATTEMPTS` | Actions variable or local process environment | `1` by default: allow one evidence-based correction of a rejected report; `0` disables correction requests |

### Provider compatibility

| `AI_PROVIDER` | API interface / default base URL |
| --- | --- |
| `gemini` | Google's native Gemini and OpenAI-compatible interfaces at `generativelanguage.googleapis.com` |
| `groq` | OpenAI-compatible API: `https://api.groq.com/openai/v1` |
| `openrouter` | OpenAI-compatible API: `https://openrouter.ai/api/v1` |
| `openai-compatible` | Your own HTTPS `AI_BASE_URL`, ending at the API base (for example `/v1`) |
| `openai` | OpenAI-compatible API: `https://api.openai.com/v1` |

Keep `AI_PROVIDER=gemini` for Google Gemini keys. The native interface uses `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`. Google's compatibility interface uses `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`. Both stay on Google's servers and authenticate with the same Gemini key. `AI_BASE_URL` does not override either route. A leading `models/` in `AI_MODEL` is removed before constructing the request, so it is never added twice. See [Google's compatibility documentation](https://ai.google.dev/gemini-api/docs/openai).

`AI_GEMINI_TRANSPORT=auto` tries native generation first. A native HTTP 404, or HTTP 500/502/503/504 after its bounded retries, triggers one request through Google's compatibility interface using the same model and evidence. No model or provider is silently substituted. Set `native` to disable this fallback, or `openai-compatible` to use Google's compatibility interface directly. The latter is useful when the native generation route consistently fails. This option does not require an OpenAI key.

Choose a model your account can access, using its exact provider model ID. Free models and quotas depend on the service and account; this application cannot make a paid model free. Gemini, Groq, and OpenRouter can be configured with available free-tier models. Other services work when they support the OpenAI chat-completions interface. Native APIs with a different protocol require another adapter; an API key alone does not establish compatibility.

Generate your key in the provider console: [Google AI Studio](https://aistudio.google.com/app/apikey), [Groq](https://console.groq.com/keys), or [OpenRouter](https://openrouter.ai/settings/keys). Verify current pricing, model availability, output limits, and quotas there. No model is hardcoded or automatically substituted. Keys must match their provider; a Gemini key cannot authenticate to Groq, for example.

For Gemini, use **Actions → Check Gemini Models → Run workflow** on your default branch to discover current model IDs using the saved `AI_API_KEY`. The workflow lists models that advertise `generateContent` and checks whether the configured `AI_MODEL` appears in that catalog. Read the run summary or **List Gemini generation models** step, then set `AI_MODEL` to the exact ID you choose. Choose a text model with JSON support, rather than an image/audio variant. By default this read-only check does not generate content, collect feeds, or send Discord messages. A listed model may still have account restrictions, paid pricing, insufficient quota, or a temporary outage.

To test generation separately, enable **Also test minimal JSON generation (uses API quota)** when starting **Check Gemini Models**. The additional step tests the configured `AI_MODEL` and `AI_GEMINI_TRANSPORT` without feed collection, report generation, or Discord delivery. It uses one attempt per selected interface: at most two requests in `auto` mode, or one in a single-interface mode. It must receive a complete, valid JSON response to pass. This consumes provider quota and may be billable; passing does not verify the full report's output budget or evidence checks.

The equivalent local commands are `python -m cyberbrief.gemini_models` for listing and `python -m cyberbrief.gemini_models --test-generation` for the optional generation test, with `AI_API_KEY` and, for generation, `AI_MODEL` exported securely. Raw API responses and keys are never printed. HTTP errors include only status and recognized symbolic provider codes, excluding free-form provider messages and metadata.

Selected updates are processed in batches. Groq defaults to one update per request with a 4096-token response budget, avoiding a large all-update request against free-tier limits. Every update retains its complete packed evidence; batching does not truncate facts or reduce the number selected. Results are merged in original order, validated again, and delivered only when all updates succeed. A multi-update HTTP 413 response triggers smaller batches. If one update alone still exceeds the provider budget, the build stops with its story ID and any safe numeric token limits returned by the provider. Use a model/account with sufficient capacity or adjust the output budget; no source evidence is silently discarded.

Groq requests use available `x-ratelimit-remaining-tokens` and `x-ratelimit-reset-tokens` headers to pace subsequent requests, including correction calls. A conservative input-plus-output estimate can cause a pause of up to 65 seconds before a later request. Headers are not guaranteed, and pacing does not raise account quotas or guarantee acceptance. Allow several minutes for a full report; the scheduled delivery job has a 40-minute timeout.

Temporary HTTP 500, 502, 503, and 504 responses are retried up to two times per batch (three attempts total), with delays of 10 and 20 seconds. Gemini `auto` mode allows one additional request through Google's compatibility interface after an eligible native failure, for at most four generation attempts per batch. A provider's `Retry-After` header can extend a delay up to 60 seconds; a longer requested wait stops the build without interface fallback so it can be rerun later. Retries, smaller batches, and fallback requests can count against provider quotas or billing. Logs report retry status, safe token-limit numbers, and attempt counts without exposing credentials, organization IDs, or raw provider response bodies.

Authentication, rate-limit, network, incomplete-response, and invalid JSON failures stop the workflow before delivery without automatic retries or interface fallback. A model-not-found response also stops delivery after any permitted Gemini interface fallback fails. Source collection and Discord delivery are not repeated by AI retries. If the provider rejects native JSON mode, set `AI_JSON_MODE=false`; output must still parse as JSON and pass all evidence checks. Increase/decrease the output budget only within the selected model's supported limits.

Valid JSON can still fail the report schema or evidence checks. These failures identify a specific field and rule, such as `stories[0].bullets[0].quote [quotation_mismatch]`, and diagnostics identify the original story IDs in the affected batch. By default, each rejected batch receives one correction request containing its original evidence, rejected draft, and exact validation error. The complete corrected report must pass the same validator; quotations, version/CVE support, story order, and summary limits are never relaxed. Correction requests consume additional provider quota and may be billable. Set `AI_REPAIR_ATTEMPTS=0` to stop immediately instead. A correction uses one attempt per selected interface, so Gemini `auto` may make two requests for that single correction; other providers make one. If correction fails or is still invalid, delivery stops.

Failed Actions builds upload available `source-report.json` and `validation-error.json` files in a `briefing-diagnostics` artifact retained for seven days. The validation diagnostic records only the safe rule, field path, and any sanitized correction-request failure; it does not include the rejected AI text or credentials. The CLI also writes these diagnostics locally. A later build clears stale validation diagnostics before attempting generation.

To switch to OpenAI later, replace the repository secret `AI_API_KEY` with your OpenAI API key, set `AI_PROVIDER=openai`, and set `AI_MODEL` to an exact model available to that account. OpenAI uses the fixed `https://api.openai.com/v1` base and `max_completion_tokens`; a leftover Gemini `AI_BASE_URL` or `AI_GEMINI_TRANSPORT` cannot redirect an OpenAI key to Google. Custom OpenAI-compatible hosts must select `AI_PROVIDER=openai-compatible` explicitly. The application does not switch providers or reuse keys between Google and OpenAI automatically.

Existing installations should replace `OPENAI_API_KEY`/`OPENAI_MODEL` with `AI_API_KEY`/`AI_MODEL` and set `AI_PROVIDER`. The old optional excerpt-selection integration has been removed.

[.env.example](.env.example) contains empty credential placeholders. A local `.env` file is ignored by Git, but **is not automatically loaded**. Export variables through your shell or hosting secret manager. Never commit credentials or populate `.env.example` with real secrets.

## Sources and evidence handling

The single AI instruction file is [SKILL.md](SKILL.md). `SKILLS.md` has been removed; there are no separate, conflicting excerpt and detailed-report prompts.

The source registry is [sources.yaml](sources.yaml).

| Category | Sources |
| --- | --- |
| Government advisories | Canadian Centre for Cyber Security; CISA official CSAF IT/OT/VA repository |
| Vendor advisories | Microsoft MSRC; Cisco Security Advisories; Fortinet PSIRT |
| Threat research | Google Threat Intelligence / Mandiant; Cisco Talos |
| News and discovery | The Record; BleepingComputer; The Hacker News |

The collection pipeline discovers updates, retrieves available advisory details, merges matching CVE/title duplicates, and selects up to ten updates. Publisher rounds limit each source to three selected updates. Known-exploitation signals and substantive advisories take priority over routine release notices; primary sources lead when duplicates merge.

Cisco advisory sections, Microsoft CVRF records, Fortinet version/fix tables, and CISA CSAF documents provide structured technical evidence. Other publishers use article headings and paragraphs. The three latest Fortinet entries published within 30 days are checked for revisions; original publication dates and day-precision revision dates remain explicit.

CISA's official `cisagov/CSAF` repository is used because its website feed rejected cloud requests during validation. Each advisory is checked against its published SHA-512 checksum. This collection is not guaranteed to mirror every website RSS item. Retrieval covers the requested start date, or eight days for rolling reports, with up to 30 records per collection. Two latest records are retrieved when a collection has no updates in that window.

CISA Known Exploited Vulnerabilities (KEV) data enriches referenced CVEs. The official `cisagov/kev-data` mirror is used when the website catalog is unavailable. KEV membership verifies catalog inclusion only; it does not independently verify all claims, products, or actor attributions in a report.

Source excerpts, dates, and supporting links are retained. Missing fields and retrieval failures are reported explicitly. Feed availability and extraction quality depend on upstream sources; consult the complete advisory before applying remediation.

## Local installation

Python **3.11 or later** is required. Development checks have been run with Python 3.12.

After forking, replace `YOUR-USERNAME` with your GitHub account:

```bash
git clone https://github.com/YOUR-USERNAME/CyberFeed.git
cd CyberFeed
python3 -m venv .venv
```

On Windows, use `python -m venv .venv` if `python3` is unavailable.

Activate the environment:

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install dependencies and export your AI configuration. For macOS zsh, use a hidden key prompt (replace the provider and model with your account's settings):

```zsh
python -m pip install -r requirements.txt
read -s "AI_API_KEY?AI provider key: "
echo
export AI_API_KEY
export AI_PROVIDER="gemini"
export AI_MODEL="YOUR-MODEL-ID"
```

For Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
$secureKey = Read-Host "AI provider key" -AsSecureString
$env:AI_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
$env:AI_PROVIDER = "gemini"
$env:AI_MODEL = "YOUR-MODEL-ID"
```

Generate the report:

```bash
python -m pip install -r requirements.txt
python -m cyberbrief.cli build --period weekly --scheduled-window --format all --strict --out site
```

The output directory contains `briefing.json`, `briefing.txt`, and `briefing.pdf`. No Discord credential is required for local generation, but the AI key/provider/model are required.

`--strict` returns exit status 2 when coverage errors exist, while retaining diagnostic output. Without that flag, partial reports include explicit warnings. The scheduled workflow sends partial reports with those warnings; unexpected build or delivery errors fail the job.

`--source-only` is an explicit diagnostic mode for collecting factual evidence without an AI call. It is not the scheduled delivery mode. `--fixtures` also bypasses AI for fictional offline tests.

Without `--scheduled-window`, reports use rolling windows of 36 hours for daily and seven days for weekly. `--fixtures` generates clearly labeled fictional demonstration data.

## AI report generation

The AI report repeats the following structure **for each update**:

**a. Summary** — concise, evidence-supported bullets.

**b. Detailed Reports (powered by AI)** — relevant technical facts, prerequisites, affected versions, exploitation evidence, patches, and workarounds. Detail has no arbitrary word target; missing facts are marked explicitly, and repetition or unsupported expansion is excluded.

**c. References** — the update's source links, dates where supplied, and inline citations.

Diagrams are optional. They are included only when they improve understanding and the evidence supports every step and relationship. A generic attack chain must not be inserted to fill missing information.

The report uses a black background, blue accents, and light text; a light version is available for printing.

- [Dark PDF sample](docs/editorial-preview/dark.pdf)
- [Light PDF sample](docs/editorial-preview/light.pdf)
- [First-page preview](docs/editorial-preview/dark-preview.png)
- [Evidence and sample output](docs/editorial-preview/README.md)
- [Portable agent instructions](SKILL.md)

These samples use historical evidence and are not newly fetched reports. Download PDFs through GitHub's download/raw control.

To prepare and render agent output locally:

```bash
python -m cyberbrief.editorial pack --input site/briefing.json --output evidence.json
# Supply SKILL.md and evidence.json to your chosen agent; save its JSON as summary.json.
python -m cyberbrief.editorial render --input evidence.json --summary summary.json --output preview.pdf --theme dark
```

Use `--theme light` for print styling. Full supplied evidence is retained by default; `--detail-level compact` reduces input size but may omit material detail. Upstream excerpts may already be truncated.

Validation checks structure, evidence IDs, exact quotations, and diagram relationships. These checks do not establish that every paraphrase follows from its citation; semantic review remains necessary. The standalone `pack`/`render` commands support manual editorial review; standard report generation invokes the configured provider and validates its output before rendering.

## Interactive Discord bot

The bot is optional and separate from scheduled webhook delivery. It requires a continuously running process.

1. Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Invite it with `bot` and `applications.commands` scopes and **View Channels**, **Send Messages**, and **Attach Files** permissions. Privileged message-content intent is unnecessary.
3. Export `AI_API_KEY`, `AI_PROVIDER`, `AI_MODEL`, and `DISCORD_BOT_TOKEN` securely, then start the bot. The bot checks AI configuration before connecting.

For macOS zsh:

```zsh
read -s "DISCORD_BOT_TOKEN?Discord bot token: "
echo
export DISCORD_BOT_TOKEN
python -m cyberbrief.discord_bot
```

For Windows PowerShell:

```powershell
$secureToken = Read-Host "Discord bot token" -AsSecureString
$env:DISCORD_BOT_TOKEN = [System.Net.NetworkCredential]::new("", $secureToken).Password
python -m cyberbrief.discord_bot
```

Slash commands synchronize at startup and may take time to appear. In a Discord channel, run:

```text
/briefing_settings format:pdf
/briefing period:weekly
/briefing period:daily format:text
```

Responses are private to the requesting user. Preferences are stored in `.state/preferences.sqlite3`; use a persistent `CYBERBRIEF_STATE_DIR` for hosted deployments. Text reports exceeding five messages use an attachment. Closing the bot process stops slash-command delivery but does not affect GitHub Actions.

## Security and operational considerations

- Store webhook URLs, bot tokens, and API keys in secret configuration. Rotate exposed credentials at their issuing service.
- Preserve TLS verification and advisory checksum checks.
- Network policy must allow source feeds, article/redirect hosts, official GitHub mirrors, and the relevant Discord endpoints.
- Do not rerun a completed delivery unless another channel post is intended. Uncertain webhook requests are not automatically retried.
- Interactive responses are private; scheduled webhook reports are visible to members with access to the destination channel.
- Report artifacts remain in GitHub Actions for seven days. Local reports and preference databases follow the host's retention policy.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `No module named discord` | Activate `.venv` and install `requirements.txt` with its Python interpreter. |
| AI configuration missing | Set `AI_API_KEY`, `AI_PROVIDER`, and `AI_MODEL`; `.env` is not auto-loaded. |
| AI HTTP 404 | Check `AI_PROVIDER`, the exact `AI_MODEL` available to your account, and any custom `AI_BASE_URL`. For Gemini, run **Check Gemini Models** in Actions to discover advertised model IDs. A Google project name is not a model ID. |
| AI HTTP 503 or other temporary server failure | The client retries HTTP 500, 502, 503, and 504 up to three attempts per primary interface. Gemini `auto` mode can make one further request through Google's compatibility interface. If all attempts fail, check provider status and account access, or use another available model/provider. |
| AI HTTP 429 | Check the provider's rate limits and quota. Automatic retries do not resolve an exhausted free-tier quota. |
| AI HTTP 413 | The request exceeds a provider size/token budget. Groq defaults to `AI_BATCH_SIZE=1` and `AI_MAX_OUTPUT_TOKENS=4096`; remove stale larger overrides or set these values explicitly. Multi-update requests are split without dropping evidence. A single-update failure names the affected story and any reported token limit. |
| AI response rejected | Read the exact validation field/rule in the log or `validation-error.json` diagnostic artifact. One evidence-based correction is allowed by default. Unsupported citations or versions must be corrected, not bypassed. Invalid JSON or truncated responses stop before this correction step. |
| Token missing despite `.env` | Export the variable; `.env` is not auto-loaded. |
| Bot does not return to the prompt | A connected bot runs continuously. Check gateway connection logs or the reported error. |
| Slash command unavailable | Verify server installation, command scope, bot process, and synchronization. |
| Scheduled report missing | Check Actions is enabled, the webhook secret exists in your fork, and the workflow logs. Allow for schedule delays. |
| Coverage warnings | Inspect the named source failures and its original advisory; missing evidence is not replaced with fabricated content. |

For macOS certificate failures, run the Python installation's `Install Certificates.command`. If necessary, use a trusted CA bundle without disabling verification:

```zsh
python -m pip install --upgrade certifi
export SSL_CERT_FILE="$(python -m certifi)"
python -m cyberbrief.discord_bot
```

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m cyberbrief.cli build --fixtures --period weekly --format all --strict --out site
```

Provider-adapter and rendering tests use mocked API responses; a credentialed provider test is still required in your installation. Tests cover extraction, source selection, checksum verification, schedule boundaries and daylight saving changes, preferences, report rendering, editorial citations, optional diagrams, and mocked Discord delivery. Verify live delivery using the configured destination and credentials; mocked tests do not establish successful external delivery.
