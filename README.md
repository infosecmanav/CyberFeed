# Signal — Cyber Briefing v2
Shared Python engine, simple website, Discord slash-command bot, and SKILL.md agent instructions. Builds on the original MVP.

## Run the website
Python 3.11+:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn cyberbrief.server:app --host 127.0.0.1 --port 8000
```
Open http://127.0.0.1:8000. Demo is selected initially. Uncheck it and build to fetch live feeds. No key is required. Optional OpenAI key input enables model-selected exact source excerpts; the key is held in request memory, not persisted. Set the model to one your API account supports. API billing is separate from ChatGPT.

## Discord bot
Create a Discord application/bot, invite with `bot` and `applications.commands` scopes, and grant Send Messages / Embed Links. Set `DISCORD_BOT_TOKEN` in the shell or hosting secret manager; optionally set `OPENAI_API_KEY` and `OPENAI_MODEL`. `.env.example` is documentation, not automatically loaded.
```bash
python -m cyberbrief.discord_bot
```
Use `/briefing daily` or `/briefing weekly`. Responses are ephemeral. No privileged message-content intent is needed. No bot has been registered or connected on your behalf.

## Shared skill and evidence
The engine loads SKILL.md for each run and passes its instructions to optional model calls. Curated default feeds: CISA, Canadian Cyber Centre, MSRC, Talos. Feed availability must be checked in your deployment. One authoritative source per claim is preferred; more sources are not automatically better.
Every published summary is an exact bounded source excerpt. Claim evidence IDs and quotations are retained in JSON. Exact matching prevents invented paraphrases, but does not independently fact-check the source. Feed text can omit context; open the advisory before acting. KEV membership is a separate factual signal, never blanket story verification. No numeric confidence or rank score appears on the website or bot.
Daily covers 36 hours; weekly covers 7 days, each at most 10 stories. Weekly currently re-ranks stories; it does not generate a thematic editorial digest. Invalid/future dates are skipped. Primary source wording wins when duplicates merge. Audit claims remain available in the API response.

## CLI / checks
```bash
python -m cyberbrief.cli build --fixtures --out site
python -m pytest -q
```
The workflow generates JSON, not a hosted site. Run website and bot as separate processes. Scheduling or production hosting requires your chosen host.

## Deployment boundaries
The Python web app is intended for local personal use. Do not expose this backend publicly without authentication, per-user quotas, TLS, and upstream timeout/rate-limit handling. The request key is never written by app code, but configure infrastructure logs to avoid request bodies. Feed failures are shown rather than replaced by fabricated news. Optional model failures fall back to source excerpts. No credentials are included.

Validation: 7 unit tests and 4 website HTTP checks passed. MSRC update-guide RSS returned valid XML. Government feeds were blocked/unavailable in this environment; end-to-end live news, optional model calls, Discord delivery and browser layout remain unverified.
