# Cyber Feed

Daily and weekly cybersecurity reports from a curated set of government, vendor, research, and news sources. Generate text/PDF reports locally or deliver them to Discord with GitHub Actions. No website is required.

**Current status:** scheduled source-based reports work without an AI key. The approved black-and-blue AI report design is available as a preview. Automated AI generation and connection of that design to scheduled delivery are **not yet implemented**.

## What is included

| Feature | Status |
| --- | --- |
| Live source collection, advisory details, and CVE/KEV enrichment | Implemented |
| Balanced selection: up to ten updates, at most three per publisher | Implemented |
| Daily and Friday weekly GitHub Actions delivery | Implemented; requires your Discord webhook |
| Interactive Discord bot with saved PDF/text preferences | Implemented; optional separate process |
| Cyber Feed black-and-blue report design | Approved preview |
| Per-update AI summary, detailed AI report, and references | Preview and agent instructions available |
| Automatic AI-provider calls for the new report format | Pending |

## Quick start: fork and schedule Discord delivery

This is the recommended setup for reports that arrive automatically. You do not need to keep a computer or bot process running.

1. **Fork the repository** into your own GitHub account. Open your fork's **Actions** tab and enable workflows if prompted. Secrets from the original repository are not copied to your fork.
2. **Create a Discord webhook** in the destination channel: channel settings → Integrations → Webhooks. You need permission to manage webhooks. Copy its URL privately.
3. In your fork, open **Settings → Secrets and variables → Actions → New repository secret**. Add `DISCORD_WEBHOOK_URL` with your webhook URL. Do not put it in a committed file.
4. Open **Actions → Scheduled Discord Briefings → Run workflow**. Choose `daily` or `weekly` and `pdf` or `text`. A manual run sends immediately; it does not wait for the scheduled time.
5. Check the destination channel and the workflow logs. The workflow also stores report files as a downloadable artifact for seven days.

GitHub supplies the limited workflow token needed for checkout and artifacts. **No personal GitHub API key, Discord bot token, or OpenAI key is required for scheduled source-based reports.**

### Delivery schedule

| Report | America/Toronto delivery time | Included period |
| --- | --- | --- |
| Daily | Every day at **8:50 AM** | Previous local 8:50 AM to the current 8:50 AM, end exclusive |
| Weekly | Friday at **8:30 AM** | Previous Friday 00:00 through Thursday, ending Friday 00:00 exclusive |

Daylight saving changes are handled by the schedule gate. Friday sends both the weekly and daily reports. Manual reports use the most recently completed delivery window.

PDF is the default. Set the repository Actions **variable** `BRIEFING_FORMAT=text` for scheduled text attachments; use `pdf` for PDF. Scheduled reports are shared channel posts, so interactive users' saved preferences do not apply.

GitHub schedules can run late and use the default branch. Public-repository schedules may be disabled after 60 days of inactivity; re-enable them in Actions when needed. Rerunning a delivered job sends another report. Uncertain/timed-out webhook requests are not automatically retried to avoid duplicate posts.

Partial source coverage is stated in both the report and the Discord message. Unexpected build errors or unsuccessful webhook requests fail the job.

## Credentials and configuration

| Name | Where to configure it | Purpose |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | Your fork's Actions secrets | Required for scheduled channel delivery |
| `BRIEFING_FORMAT` | Your fork's Actions variables | Optional `pdf` or `text`; defaults to PDF |
| `DISCORD_BOT_TOKEN` | Local/host process environment | Required only for the optional interactive bot |
| `CYBERBRIEF_STATE_DIR` | Local/host process environment | Optional persistent preference directory |
| `OPENAI_API_KEY` | Local/host process environment | Optional **legacy excerpt selection** in the interactive bot |
| `OPENAI_MODEL` | Local/host process environment | Model for legacy selection; default `gpt-4.1-mini` |

Credential placeholders in [.env.example](.env.example) are empty. You may copy that file to the ignored `.env` for your own local values, but **the application does not automatically load `.env`**. Export variables using your shell or hosting secret manager. Never commit `.env`, place real secrets in `.env.example`, or paste credentials into chat.

The scheduled workflow does not currently read `OPENAI_API_KEY`. Supplying one does not activate the new detailed AI reports. A provider/model integration is still required; free-tier availability depends on the chosen provider. API usage is separate from ChatGPT subscriptions.

## Approved AI report structure

Every update is self-contained, in this order:

1. **AI summary** — short, supported bullets for that update.
2. **Detailed AI-powered report** — as much relevant technical detail as the supplied facts justify: impact, prerequisites, versions, exploitation, patches, and workarounds. No arbitrary word limit, invented facts, filler, or unrelated text.
3. **References** — that update's source links, dates where supplied, and claim citations.

Repeat for each selected update; there is no combined summary or combined reference section. Flowcharts/diagrams are **optional** and appear only when they improve understanding and every step/relationship has supporting evidence. A standard kill chain must never be invented to fill gaps.

The design uses a black background, blue accents, and light text. A light version is available for printing.

- [Black-and-blue PDF sample](docs/editorial-preview/dark.pdf)
- [First-page image](docs/editorial-preview/dark-preview.png)
- [Light PDF sample](docs/editorial-preview/light.pdf)
- [Sample evidence and editorial output](docs/editorial-preview/README.md)
- [Portable agent instructions: SKILLS.md](SKILLS.md)

These are historical design samples, not newly fetched reports. GitHub's PDF download/raw button saves the file. The agent instructions can be used with any capable AI agent; they do not provide or invoke a model.

The standalone editorial tool preserves all supplied sections by default. Optional compact mode reduces input size, but cannot restore facts omitted from a source excerpt. Validation checks IDs, exact quotations, structure, and diagram relationships. It cannot prove that a paraphrase is correct; semantic review remains necessary. Current scheduled reports still use the source-based renderer.

## Sources and evidence

Source URLs, tiers, and adapters are configured in [sources.yaml](sources.yaml).

| Purpose | Sources |
| --- | --- |
| Government advisories | Canadian Centre for Cyber Security; CISA official CSAF IT/OT/VA repository |
| Vendor advisories | Microsoft MSRC; Cisco Security Advisories; Fortinet PSIRT |
| Threat research | Google Threat Intelligence / Mandiant; Cisco Talos |
| Reporting and discovery | The Record; BleepingComputer; The Hacker News |

Feeds discover updates; selected full articles and structured records provide details. Cisco uses explicit advisory sections, Microsoft uses its public CVRF API, and Fortinet uses vendor version/fix tables and revision dates. The three newest Fortinet entries published within 30 days are checked for updates. Other publishers use headings and source paragraphs. Missing information and retrieval failures remain visible.

CISA's website feed rejected cloud requests during validation. Its official `cisagov/CSAF` repository is used instead, with published SHA-512 checksums verified for each advisory. This alternative collection does not guarantee coverage identical to the website RSS. Retrieval covers the requested start date (eight days for rolling reports), up to 30 records per collection, with two latest records when no recent updates exist.

CISA KEV is separate CVE enrichment, using CISA's official `cisagov/kev-data` mirror if its website fails. Catalog membership does not verify every claim or every product in a story. Matching CVE/title duplicates are merged with primary-source preference, while supporting links are retained. Selection prioritizes known-exploitation signals and substantive advisories over routine release notices.

Network access must allow the configured feeds, article/redirect hosts, official GitHub mirrors, and Discord. Preserve TLS and checksum verification. Feed availability can change; a successful historical check does not guarantee future access. Alert feeds do not include every government guidance page.

## Clone and run locally

Python **3.11+** is required; development checks were run with Python 3.12. Replace `YOUR-USERNAME` with your own GitHub account after forking:

```bash
git clone https://github.com/YOUR-USERNAME/CyberFeed.git
cd CyberFeed
python3 -m venv .venv
```

Activate the environment:

```bash
# macOS/Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install and generate a real report without Discord:

```bash
python -m pip install -r requirements.txt
python -m cyberbrief.cli build --period weekly --scheduled-window --format all --strict --out site
```

Open `site/briefing.pdf` or `site/briefing.txt`. JSON evidence is in `site/briefing.json`. `--strict` exits with status 2 on coverage errors while retaining diagnostic files. Omit it to accept a report with explicit coverage warnings.

Without `--scheduled-window`, interactive/CLI reports use rolling windows: 36 hours for daily and seven days for weekly. Use `--fixtures` only for clearly fictional demonstration data.

### Editorial preview commands

```bash
python -m cyberbrief.editorial pack --input site/briefing.json --output evidence.json
# Give SKILLS.md and evidence.json to your chosen agent; save its output as summary.json.
python -m cyberbrief.editorial render --input evidence.json --summary summary.json --output preview.pdf --theme dark
```

Use `--theme light` for print styling. Provider calls and automatic publication of editorial output are not connected yet.

## Optional interactive Discord bot

Use this only if you also want on-demand slash commands. It needs an always-running process and is separate from scheduled webhook delivery.

1. Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Invite it with `bot` and `applications.commands` scopes and **View Channels**, **Send Messages**, and **Attach Files** permissions. Privileged message-content intent is not required.
3. Configure `DISCORD_BOT_TOKEN` securely, then start `python -m cyberbrief.discord_bot`.

For macOS **zsh**, enter the token through a hidden prompt:

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

Keep the process running. Slash commands sync at startup and may take time to appear. In your server's channel message box, use:

```text
/briefing_settings format:pdf
/briefing period:weekly
/briefing period:daily format:text
```

Bot responses are private to the requesting user. Preferences are stored in ignored `.state/preferences.sqlite3`; set `CYBERBRIEF_STATE_DIR` to a persistent writable directory on a deployment host. Text reports exceeding five messages use an attachment. Closing the terminal stops the interactive bot, but does not stop GitHub Actions schedules.

## Troubleshooting

- **Missing `discord` module:** activate `.venv`, then install `requirements.txt` using that environment's Python.
- **Token missing despite `.env`:** `.env` is not auto-loaded. Export the variable or use the hidden prompt above.
- **macOS SSL certificate failure:** run the Python installation's `Install Certificates.command`. If needed, use the trusted bundle below; never disable verification.

  ```zsh
  python -m pip install --upgrade certifi
  export SSL_CERT_FILE="$(python -m certifi)"
  python -m cyberbrief.discord_bot
  ```

- **Bot appears stuck:** it normally stays running without returning a prompt. Check whether it connected to Discord; inspect the terminal error if it did not.
- **Slash command missing:** verify server installation, `applications.commands` scope, and the running bot.
- **Scheduled delivery missing:** check Actions is enabled, `DISCORD_WEBHOOK_URL` is configured in your fork, and the workflow run/logs. Schedules may be delayed.

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m cyberbrief.cli build --fixtures --period weekly --format all --strict --out site
```

Tests cover extraction, source balance, checksum verification, date boundaries/DST, preferences, PDFs, citation checks, optional diagrams, and mocked Discord delivery. A live calendar-week build previously returned ten updates with zero coverage errors. Automated webhook delivery still needs validation with your own secret and destination channel. No credentials are included in this repository.
