import json
from datetime import datetime, timedelta

import streamlit as st

from config.settings import settings
from services.exporter import Exporter
from services.hologres_client import HologresClient, HologresConfigError
from services.llm_client import LlmApiError, LlmClient, SchemaAnswer
from services.metadata_retriever import MetadataRetriever
from services.sql_guard import SqlGuard, SqlGuardError
from services.sql_normalizer import uppercase_string_literals


@st.cache_resource
def _init_clients():
    db_client = HologresClient()
    metadata = MetadataRetriever(db_client=db_client)
    llm_client = LlmClient()
    sql_guard = SqlGuard()
    return db_client, metadata, llm_client, sql_guard


def _nl_summary(question: str, row_count: int) -> str:
    if row_count == 0:
        return f"No data found for: {question}"
    return f"Returned {row_count} rows for: {question}"


# Keywords that indicate the user is asking about the schema/catalog,
# not requesting actual data. Used by _is_schema_question().
_SCHEMA_INTENT_KEYWORDS = [
    "are there any tables", "what tables", "which tables",
    "what columns", "which columns", "do we have a table",
    "is there a table", "any info about", "any information about",
    "where can i find", "tell me about tables", "show me tables",
    "list tables", "list columns", "what data do we have",
    "do we have any", "is there any table", "any table for",
]


def _is_schema_question(question: str) -> bool:
    """Returns True if the question is asking about schema/table discovery
    rather than requesting actual data. Prevents the LLM from generating SQL
    for questions like 'are there any tables with customer nationality'."""
    lower = question.lower()
    return any(kw in lower for kw in _SCHEMA_INTENT_KEYWORDS)


def _init_session_state() -> None:
    """Initialize session state flags used to track run/stop lifecycle."""
    if "running" not in st.session_state:
        st.session_state.running = False
    if "stop_requested" not in st.session_state:
        st.session_state.stop_requested = False


def main() -> None:
    st.set_page_config(page_title="Hologres RAG SQL Assistant", layout="wide")
    _init_session_state()

    # Compute previous month dynamically for placeholder and examples
    _today = datetime.today()
    _prev_month = (_today.replace(day=1) - timedelta(days=1))
    _prev_month_label = _prev_month.strftime("%B %Y")       # e.g. 'May 2026'
    _prev_month_short = _prev_month.strftime("%Y-%m")       # e.g. '2026-05'

    st.title("Hologres RAG SQL Assistant")
    st.caption("Qwen3.5-flash primary, V model fallback, SELECT-only safe execution")

    with st.sidebar:
        st.subheader("Runtime")
        st.write(f"Max rows: `{settings.app_max_rows}`")
        st.write(f"Query timeout: `{settings.app_query_timeout_sec}s`")
        if settings.whitelist_tables:
            st.write("Whitelisted tables:")
            st.code("\n".join(settings.whitelist_tables))
        st.divider()
        st.write("DB target (from env):")
        st.code(settings.hologres_display_target)
        st.write("Configure `cf.env` or `.env` in the project root.")

    st.markdown("#### 💬 Ask a business question")
    st.caption("Ask about sales, stock, or explore what data is available.")

    with st.expander("📋 Example questions", expanded=False):
        st.markdown(f"""
**Sales & Revenue**
- How much is the total sales for Singapore in {_prev_month_label}?
- Show me daily sales for Saudi Arabia for {_prev_month_short}-13 at article level
- What is the sold quantity for UAE in {_prev_month_label} by brand?

**Stock & Inventory**
- What is the current stock for Singapore as of {_prev_month_short}-01?
- Show me stock quantity for Malaysia by article for {_prev_month_short}-28

**Schema Discovery**
- Are there any tables where I can find customer nationality?
- What columns are available in the sales table?
- Do we have any data about store categories?
""")

    question = st.text_area(
        "Your question",
        placeholder=f"e.g. How much is the total sales for Singapore in {_prev_month_label} at date level?",
        height=100,
    )

    btn_col1, btn_col2, _ = st.columns([1, 1, 4])
    run_clicked = btn_col1.button(
        "▶ Run",
        type="primary",
        disabled=st.session_state.running,
        use_container_width=True,
    )
    stop_clicked = btn_col2.button(
        "⏹ Stop",
        type="secondary",
        disabled=not st.session_state.running,
        use_container_width=True,
    )

    if stop_clicked:
        st.session_state.stop_requested = True
        st.session_state.running = False
        st.warning("Query stopped. Modify your question and click Run again.")
        st.stop()

    if run_clicked:
        st.session_state.running = True
        st.session_state.stop_requested = False
        if not question.strip():
            st.session_state.running = False
            st.warning("Please enter a question.")
            st.stop()

        db_client, metadata, llm_client, sql_guard = _init_clients()

        # ── Intent detection ─────────────────────────────────────────────
        # If the user is asking about tables/columns (schema discovery),
        # answer in plain English. Otherwise generate and run SQL as usual.
        # ─────────────────────────────────────────────────────────────────
        if _is_schema_question(question):
            with st.spinner("Looking up schema information..."):
                try:
                    context = metadata.get_context(question, ignore_whitelist=True)
                    schema_result = llm_client.answer_schema_question(context.to_prompt_context())
                except LlmApiError as llm_error:
                    st.session_state.running = False
                    st.error(f"LLM (DashScope) error:\n\n{llm_error}")
                    st.stop()
                except Exception as exc:
                    st.session_state.running = False
                    st.error(f"Schema lookup failed: {exc}")
                    st.stop()

            st.session_state.running = False
            st.success("Schema lookup completed.")
            st.info(schema_result.answer)

            st.subheader("Token Usage")
            col1, col2, col3 = st.columns(3)
            col1.metric("Prompt Tokens", schema_result.prompt_tokens,
                        help="Tokens sent TO the model")
            col2.metric("Completion Tokens", schema_result.completion_tokens,
                        help="Tokens returned BY the model")
            col3.metric("Total Tokens", schema_result.total_tokens,
                        help="prompt_tokens + completion_tokens — this is what you are billed for")

        else:
            with st.spinner("Generating SQL and querying Hologres..."):
                try:
                    context = metadata.get_context(question)
                    llm_result = llm_client.generate_sql(context.to_prompt_context())

                    validated_sql = sql_guard.validate(llm_result.sql)
                    sql_guard.enforce_table_whitelist(validated_sql)
                    normalized_sql = uppercase_string_literals(validated_sql)
                    final_sql = sql_guard.apply_limit(normalized_sql)

                    df = db_client.run_select(final_sql, max_rows=settings.app_max_rows)

                except HologresConfigError as cfg_error:
                    st.session_state.running = False
                    st.error(f"Database configuration: {cfg_error}")
                    st.stop()
                except SqlGuardError as guard_error:
                    st.session_state.running = False
                    st.error(f"SQL blocked by guardrails: {guard_error}")
                    st.stop()
                except LlmApiError as llm_error:
                    st.session_state.running = False
                    st.error(f"LLM (DashScope) error:\n\n{llm_error}")
                    with st.expander("LLM configuration (check cf.env)"):
                        st.code(
                            f"QWEN_ENDPOINT={settings.qwen_endpoint}\n"
                            f"QWEN_MODEL={settings.qwen_model}\n"
                            f"DASHSCOPE_REGION={settings.dashscope_region}\n"
                            f"QWEN_API_KEY={'set' if settings.qwen_api_key else 'MISSING'}"
                        )
                    st.stop()
                except Exception as exc:
                    st.session_state.running = False
                    st.error(f"Query failed: {exc}")
                    st.stop()

            st.session_state.running = False
            st.success("Query completed.")
            st.write(_nl_summary(question, len(df)))

            with st.expander("Generated SQL", expanded=True):
                st.code(final_sql, language="sql")

            with st.expander("Model Trace", expanded=False):
                st.code(
                    json.dumps(
                        {
                            "model_used": llm_result.model_used,
                            "used_fallback": llm_result.used_fallback,
                            "latency_ms": llm_result.latency_ms,
                            "token_usage": {
                                "prompt_tokens": llm_result.prompt_tokens,
                                "completion_tokens": llm_result.completion_tokens,
                                "total_tokens": llm_result.total_tokens,
                            },
                        },
                        indent=2,
                    ),
                    language="json",
                )

            st.subheader("Token Usage")
            col1, col2, col3 = st.columns(3)
            col1.metric("Prompt Tokens", llm_result.prompt_tokens,
                        help="Tokens sent TO the model (your question + schema context + system prompt)")
            col2.metric("Completion Tokens", llm_result.completion_tokens,
                        help="Tokens returned BY the model (the generated SQL)")
            col3.metric("Total Tokens", llm_result.total_tokens,
                        help="prompt_tokens + completion_tokens — this is what you are billed for")

            st.dataframe(df, use_container_width=True)
            if len(df) >= settings.app_max_rows:
                st.warning(
                    f"Result reached cap ({settings.app_max_rows} rows). Export includes capped rows only."
                )

            xlsx_data = Exporter.to_xlsx_bytes(df, question=question, sql=final_sql)
            st.download_button(
                label="Download .xlsx",
                data=xlsx_data,
                file_name=f"hologres_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
