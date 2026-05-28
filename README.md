# Streamlit Hologres RAG SQL Assistant

This project provides a Streamlit web app for Text-to-SQL RAG over Hologres:
- User asks natural-language question
- Metadata + business rules are retrieved
- Qwen3.5-flash generates SQL (V model fallback)
- SQL guardrails validate and cap results
- Query runs on Hologres
- User sees table + downloads `.xlsx`

## Project structure

- `app.py` - Streamlit UI and orchestration
- `config/settings.py` - environment configuration
- `config/cf.py` - which dotenv file to load (`cf.env`, `.env`, or `.env.example`)
- `services/hologres_client.py` - Hologres query execution
- `services/metadata_retriever.py` - metadata retrieval for prompt context
- `services/llm_client.py` - model calls and fallback routing
- `services/sql_guard.py` - SQL safety checks and row cap handling
- `services/exporter.py` - Excel export helper
- `data/business_rules.json` - glossary and business-rule context

## Setup

1. Create virtual environment and install dependencies:
   - `pip install -r requirements.txt`
2. Configure env vars (first existing file wins):
   - Recommended: copy `cf.env.example` to `cf.env` in the project root and edit (this file is gitignored).
   - Or use `.env` (also gitignored).
   - Or keep using `.env.example` for local-only testing (not ideal for secrets).
   - If `cf.env` exists but is empty, it still wins over `.env` — delete `cf.env` or fill it.
   - Override path: set `CONFIG_DOTENV_FILE` to an absolute path before starting Python.
   - Logic lives in [`config/cf.py`](config/cf.py); [`config/settings.py`](config/settings.py) calls it on import.
3. Verify: `python config/settings.py` should print `Loaded env from:` and your `Settings` (run from repo root).
4. Run the app:
   - `streamlit run app.py`

## Security defaults

- Use read-only Hologres credentials.
- Keep `APP_MAX_ROWS=10000` for safe exports.
- Keep whitelist table names in `APP_TABLE_WHITELIST` for production usage.

## Notes

- The app enforces SELECT-only SQL and blocks DDL/DML.
- If generated SQL fails basic checks, the app blocks execution.
- Model trace (model used, fallback state, latency) is shown in UI.
