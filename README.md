# Signal — Discord cyber briefings

A Discord bot that builds daily or weekly cybersecurity briefings from curated feeds. Each user can save a text or PDF preference. No website or HTTP server is required.

## Commands

- `/briefing_settings format:pdf` saves PDF as your default; choose `text` for Discord messages.
- `/briefing period:daily` uses your saved format. New users default to text.
- `/briefing period:weekly format:pdf` overrides your preference for this report without changing it.

Reports are ephemeral (visible only to the requesting user). PDFs use readable section headings, embedded fonts, and numbered pages. They are generated in memory and uploaded to Discord; the app does not save report files. Text uses Discord messages, with a UTF-8 attachment for reports that would exceed five messages. Both formats include dates, source links, known-exploitation signals, weekly topic groupings, and coverage warnings. Empty windows never produce fabricated stories.

Daily covers the last 36 hours; weekly covers seven days, up to ten selected stories. Weekly groups attributed excerpts by topic, including vendor hardening releases, without inventing editorial conclusions. Selection uses publisher rounds with at most three updates per publisher; known-exploitation signals and substantive advisories take priority over routine hardening announcements. Summaries are bounded exact source excerpts. Selected articles are retrieved in parallel to extract affected products/versions, patches, workarounds, and source exploitation statements. Cisco uses explicit advisory sections; Microsoft uses its public CVRF API (including the original release month for revised CVEs); CISA uses structured CSAF product and remediation records. Fortinet uses its official HTML counterpart when the feed points to a CVRF URL, preserving version-to-fix tables and workaround instructions. The three most recent Fortinet entries published within 30 days are checked for revisions, so an advisory published earlier but updated this week can enter the weekly report; original publication dates are retained and day-precision update dates are labeled. Other publishers use article headings or attributed paragraphs. Missing fields are marked as not provided, not guessed. Any retrieval failure remains visible in coverage warnings. Read the original advisory before acting: excerpt matching does not independently fact-check a source.

## Run the bot

Python 3.11+ (validated on 3.12):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Inject DISCORD_BOT_TOKEN through your host's secure secret configuration.
.venv/bin/python -m cyberbrief.discord_bot
```

Create a Discord application and bot in the Developer Portal. Install it in your server with the `bot` and `applications.commands` scopes and Send Messages / Attach Files permissions. No privileged message-content intent is needed. Slash commands sync once on startup; global registration may take time to appear. The bot must remain running for commands to work.

`DISCORD_BOT_TOKEN` is required and is read from the process environment. Never commit it or paste it into chat. `.env.example` documents names; `.env` is not automatically loaded. No Discord application is created or invited by this project. The token must be usable by discord.py for both HTTPS API calls and gateway authentication; an HTTPS-only proxy placeholder is not sufficient for the gateway.

Preferences use SQLite in `.state/preferences.sqlite3`, which is ignored by Git. Set `CYBERBRIEF_STATE_DIR` to a persistent writable volume on your deployment host to retain preferences across redeployments. It stores only Discord user IDs and format choices. Reports remain private responses rather than scheduled channel posts.

Optional `OPENAI_API_KEY` and `OPENAI_MODEL` enable exact excerpt selection through OpenAI. They are unnecessary for the default workflow; model failures fall back to source excerpts. API billing is separate from ChatGPT. Optional OpenAI integration has not been tested with credentials.

## Sources

| Purpose | Sources |
| --- | --- |
| Government guidance | Canadian Centre for Cyber Security, CISA official CSAF IT/OT/VA repository |
| Vendor advisories | Microsoft MSRC, Cisco Security Advisories, Fortinet PSIRT |
| Technical research | Google Threat Intelligence / Mandiant, Cisco Talos |
| Reporting and discovery | The Record, BleepingComputer, The Hacker News |

URLs and source tiers are configured in `sources.yaml`. Primary sources lead when duplicates merge; supporting evidence links remain available. Google/Mandiant share one official feed. CISA KEV is separate enrichment and falls back to CISA's official `cisagov/kev-data` GitHub mirror. KEV membership is not blanket verification of a story.

CISA's website RSS endpoint denies cloud requests, so the configured CISA adapter uses its official `cisagov/CSAF` repository instead. It reads IT, OT, and VA change indexes and verifies each document against the published SHA-512 checksum before use. This is an official alternative advisory collection, not a claim that every item in the website RSS is mirrored. Up to 30 recent records per collection are retrieved per build, with two latest records when a collection has no updates in the last seven days. The report's normal time window still applies.

Live validation returned ten detailed weekly stories from eight publishers with zero retrieval errors. Cisco and Fortinet feed hosts are `sec.cloudapps.cisco.com` and `filestore.fortinet.com`. Full articles also require `cyber.gc.ca`, `thehackernews.com`, and `msrc.microsoft.com`; Microsoft's CVRF lookup uses `api.msrc.microsoft.com`. Previously saved network domains remain intact. Feed availability can change; coverage errors are never suppressed. Alert feeds do not cover every government guidance page.

Discord runtime access requires `discord.com` and `gateway.discord.gg` as well as the configured feed destinations. Do not disable TLS verification to resolve access failures.

## Development and validation

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m cyberbrief.cli build --fixtures --out site
.venv/bin/python -m cyberbrief.cli build --period weekly --format all --strict --out /tmp/cyberfeed-live
```

The CLI exports JSON by default and can generate text, PDF, or all formats locally with `--format`. The PDF is written as `briefing.pdf` in the output directory, so it can be tested without Discord. `--strict` exits with status 2 when retrieval errors exist; it never silently calls partial coverage successful. Demo fixtures are explicitly fictional and are never selected by Discord commands. Automated checks verify advisory section extraction, CISA checksum rejection, MSRC version/build mapping, source balance, trusted retrieval hosts and redirects, visible failures, persistent per-user preferences, command format overrides, private mocked Discord delivery, message limits, source warnings, and PDF text extraction/pagination. Actual Discord login, command registration, and attachment delivery require a token and an installed bot and remain unverified. Scheduling is not implemented.
