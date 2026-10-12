# Cyber Feed

Cyber Feed collects cybersecurity updates from government agencies, vendor advisories, threat research teams, and selected news publishers. It produces evidence-linked PDF and text reports for scheduled Discord delivery or local review.

Scheduled reports run through GitHub Actions. An optional Discord bot supports on-demand reports and per-user format preferences.

> **AI integration status:** the black-and-blue editorial layout and portable agent instructions are available as a preview. Automated AI generation and delivery of that layout are not yet integrated. The scheduled workflow currently delivers source-based reports and does not require an AI API key.

## Contents

- [Scheduled Discord deployment](#scheduled-discord-deployment)
- [Report schedule](#report-schedule)
- [Configuration](#configuration)
- [Sources and evidence handling](#sources-and-evidence-handling)
- [Local installation](#local-installation)
- [AI editorial preview](#ai-editorial-preview)
- [Interactive Discord bot](#interactive-discord-bot)
- [Security and operational considerations](#security-and-operational-considerations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Scheduled Discord deployment

### Prerequisites

- A GitHub account and a fork of this repository.
- A Discord channel where you have permission to create webhooks.
- GitHub Actions enabled in your fork.

This deployment does not require a continuously running local computer, a Discord bot token, or a personal GitHub API key. GitHub supplies the workflow token used for checkout and artifacts.

### 1. Fork the repository

Fork the repository into your own account. Open the fork's **Actions** tab and enable workflows if prompted. Repository secrets are not copied from the original repository.

### 2. Create the Discord webhook

In the destination channel, open **Channel Settings → Integrations → Webhooks**. Create a webhook and copy its URL. Treat the URL as a credential: anyone holding it can post to that channel.

### 3. Configure the repository secret

In your fork, open **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | The webhook URL for your destination channel |

For scheduled text delivery, also create an Actions **variable** named `BRIEFING_FORMAT` with the value `text`. The default is `pdf`.

### 4. Validate delivery

Open **Actions → Scheduled Discord Briefings → Run workflow**. Select the report period and format, then run the workflow.

Confirm that:

- The workflow completes successfully.
- The report arrives in the configured channel.
- Any coverage warnings are visible in the report.

Manual runs deliver immediately. Generated JSON, text, and PDF files are retained as a downloadable workflow artifact for seven days.

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
| `OPENAI_API_KEY` | Local or hosting process environment | Optional existing excerpt-selection feature in the interactive bot |
| `OPENAI_MODEL` | Local or hosting process environment | Model for excerpt selection; default `gpt-4.1-mini` |

The scheduled workflow does not currently consume `OPENAI_API_KEY`; adding that secret does not enable detailed AI reports. Provider access and free-tier availability depend on the selected service. API billing is separate from ChatGPT subscriptions.

[.env.example](.env.example) contains empty credential placeholders. A local `.env` file is ignored by Git, but **is not automatically loaded**. Export variables through your shell or hosting secret manager. Never commit credentials or populate `.env.example` with real secrets.

## Sources and evidence handling

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

Install dependencies and generate a report:

```bash
python -m pip install -r requirements.txt
python -m cyberbrief.cli build --period weekly --scheduled-window --format all --strict --out site
```

The output directory contains `briefing.json`, `briefing.txt`, and `briefing.pdf`. No Discord credential is required for local generation.

`--strict` returns exit status 2 when coverage errors exist, while retaining diagnostic output. Without that flag, partial reports include explicit warnings. The scheduled workflow sends partial reports with those warnings; unexpected build or delivery errors fail the job.

Without `--scheduled-window`, reports use rolling windows of 36 hours for daily and seven days for weekly. `--fixtures` generates clearly labeled fictional demonstration data.

## AI editorial preview

The proposed editorial report repeats the following structure **for each update**:

**a. AI summary** — concise, evidence-supported bullets.

**b. Detailed AI-powered report** — relevant technical facts, prerequisites, affected versions, exploitation evidence, patches, and workarounds. Detail has no arbitrary word target; missing facts are marked explicitly, and repetition or unsupported expansion is excluded.

**c. References** — the update's source links, dates where supplied, and inline citations.

Diagrams are optional. They are included only when they improve understanding and the evidence supports every step and relationship. A generic attack chain must not be inserted to fill missing information.

The preview uses a black background, blue accents, and light text; a light version is available for printing.

- [Dark PDF sample](docs/editorial-preview/dark.pdf)
- [Light PDF sample](docs/editorial-preview/light.pdf)
- [First-page preview](docs/editorial-preview/dark-preview.png)
- [Evidence and sample output](docs/editorial-preview/README.md)
- [Portable agent instructions](SKILLS.md)

These samples use historical evidence and are not newly fetched reports. Download PDFs through GitHub's download/raw control.

To prepare and render agent output locally:

```bash
python -m cyberbrief.editorial pack --input site/briefing.json --output evidence.json
# Supply SKILLS.md and evidence.json to your chosen agent; save its JSON as summary.json.
python -m cyberbrief.editorial render --input evidence.json --summary summary.json --output preview.pdf --theme dark
```

Use `--theme light` for print styling. Full supplied evidence is retained by default; `--detail-level compact` reduces input size but may omit material detail. Upstream excerpts may already be truncated.

Validation checks structure, evidence IDs, exact quotations, and diagram relationships. These checks do not establish that every paraphrase follows from its citation; semantic review remains necessary. The tool does not invoke a provider or publish editorial output automatically.

## Interactive Discord bot

The bot is optional and separate from scheduled webhook delivery. It requires a continuously running process.

1. Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Invite it with `bot` and `applications.commands` scopes and **View Channels**, **Send Messages**, and **Attach Files** permissions. Privileged message-content intent is unnecessary.
3. Export `DISCORD_BOT_TOKEN` securely and start the bot.

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

Tests cover extraction, source selection, checksum verification, schedule boundaries and daylight saving changes, preferences, report rendering, editorial citations, optional diagrams, and mocked Discord delivery. Verify live delivery using the configured destination and credentials; mocked tests do not establish successful external delivery.
