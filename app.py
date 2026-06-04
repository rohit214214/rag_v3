import json
from datetime import datetime

import streamlit as st

from config.settings import settings
from services.exporter import Exporter
from services.hologres_client import HologresClient, HologresConfigError
from services.llm_client import LlmApiError, LlmClient
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


def _init_session_state() -> None:
    """Initialize session state flags used to track run/stop lifecycle."""
    if "running" not in st.session_state:
        st.session_state.running = False
    if "stop_requested" not in st.session_state:
        st.session_state.stop_requested = False


def main() -> None:
    st.set_page_config(page_title="Hologres RAG SQL Assistant", layout="wide")
    _init_session_state()
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

    question = st.text_area(
        "Ask a business question",
        placeholder="How much Singapore sales for Aug 2026 at date level?",
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
