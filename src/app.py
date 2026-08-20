"""
IOMETE Sentinel — Streamlit Dashboard
Visual data-quality observatory for the IOMETE Lakehouse.
"""

import os
from datetime import datetime

import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "lakehouse.duckdb")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="IOMETE Sentinel",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background: #0d1117; color: #e6edf3; }
  [data-testid="stSidebar"]          { background: #161b22; border-right: 1px solid #30363d; }
  .metric-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 18px 22px;
    margin-bottom: 12px;
  }
  .status-green  { color: #3fb950; font-weight: 700; font-size: 1.4rem; }
  .status-yellow { color: #d29922; font-weight: 700; font-size: 1.4rem; }
  .status-red    { color: #f85149; font-weight: 700; font-size: 1.4rem; }
  .section-title { color: #58a6ff; font-size: 1.1rem; font-weight: 600; margin-top: 8px; }
  .stChatMessage { background: #161b22 !important; border: 1px solid #30363d; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)


# ── Data helpers ──────────────────────────────────────────────────────────────
@st.cache_resource
def get_connection():
    if not os.path.exists(DB_PATH):
        from setup_data import setup_lakehouse
        setup_lakehouse(verbose=False)
    return duckdb.connect(DB_PATH, read_only=True)


def ensure_data_exists():
    if not os.path.exists(DB_PATH):
        with st.spinner("Inicializando Lakehouse com dados de demonstração..."):
            from setup_data import setup_lakehouse
            setup_lakehouse()
        st.cache_resource.clear()


@st.cache_data(ttl=300)
def load_sales_df() -> pd.DataFrame:
    con = duckdb.connect(DB_PATH, read_only=True)
    df = con.execute("SELECT * FROM sales").df()
    con.close()
    return df


@st.cache_data(ttl=300)
def compute_health_metrics(df: pd.DataFrame) -> dict:
    total = len(df)
    metrics = {}

    # Null analysis
    null_pcts = {}
    for col in df.columns:
        pct = df[col].isna().sum() / total * 100
        null_pcts[col] = round(pct, 2)

    # Critical columns
    critical_cols = ["order_id", "customer_id", "unit_price"]
    max_null_critical = max(null_pcts.get(c, 0) for c in critical_cols)

    # Price outliers
    price_col = df["unit_price"].dropna()
    high_outliers = int((price_col > 10_000).sum())
    neg_outliers = int((price_col <= 0).sum())
    total_outliers = high_outliers + neg_outliers

    # Future dates
    df["sale_date"] = pd.to_datetime(df["sale_date"], errors="coerce")
    future_count = int((df["sale_date"] > datetime.now()).sum())
    future_pct = round(future_count / total * 100, 2)

    # Duplicates
    dup_count = int(df["order_id"].dropna().duplicated().sum())
    dup_pct = round(dup_count / total * 100, 2)

    # Overall status
    if max_null_critical > 10 or neg_outliers > 0 or dup_pct > 1:
        status = "CRITICAL"
        status_color = "red"
        status_icon = "🔴"
    elif max_null_critical > 5 or high_outliers > 0 or future_pct > 2:
        status = "WARNING"
        status_color = "yellow"
        status_icon = "🟡"
    else:
        status = "HEALTHY"
        status_color = "green"
        status_icon = "🟢"

    metrics = {
        "total_rows": total,
        "null_pcts": null_pcts,
        "max_null_critical": max_null_critical,
        "high_outliers": high_outliers,
        "neg_outliers": neg_outliers,
        "total_outliers": total_outliers,
        "future_count": future_count,
        "future_pct": future_pct,
        "dup_count": dup_count,
        "dup_pct": dup_pct,
        "status": status,
        "status_color": status_color,
        "status_icon": status_icon,
    }
    return metrics


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar(metrics: dict):
    with st.sidebar:
        st.image(
            "https://iomete.com/favicon.ico",
            width=32,
        )
        st.title("IOMETE Sentinel")
        st.caption("Data Reliability Engineering")
        st.divider()

        # Status badge
        color_class = f"status-{metrics['status_color']}"
        st.markdown(
            f"**Status Atual**<br>"
            f"<span class='{color_class}'>{metrics['status_icon']} {metrics['status']}</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"Tabela: `sales` · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        st.divider()

        # Quick stats
        st.markdown("<span class='section-title'>Resumo Rápido</span>", unsafe_allow_html=True)
        st.metric("Total de Linhas", f"{metrics['total_rows']:,}")
        st.metric("Outliers de Preço", metrics["total_outliers"],
                  delta=f"-{metrics['total_outliers']} a corrigir", delta_color="inverse")
        st.metric("Datas Futuras", f"{metrics['future_count']} ({metrics['future_pct']}%)",
                  delta_color="inverse")
        st.metric("IDs Duplicados", f"{metrics['dup_count']} ({metrics['dup_pct']}%)",
                  delta_color="inverse")
        st.divider()

        # Groq API key input
        st.markdown("<span class='section-title'>Configuração do Agente</span>", unsafe_allow_html=True)
        api_key = st.text_input(
            "GROQ_API_KEY",
            value=os.environ.get("GROQ_API_KEY", ""),
            type="password",
            placeholder="gsk_...",
            help="Obtenha em console.groq.com",
        )
        if api_key:
            os.environ["GROQ_API_KEY"] = api_key

        return api_key


# ── Main dashboard ────────────────────────────────────────────────────────────
def render_dashboard(df: pd.DataFrame, metrics: dict):
    st.markdown("## 🛡️ IOMETE Sentinel — Observabilidade do Lakehouse")
    st.markdown("---")

    # ── Row 1: KPI cards ──
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Linhas Totais", f"{metrics['total_rows']:,}")
    with c2:
        null_pct = metrics["null_pcts"].get("order_id", 0)
        st.metric("Nulos (order_id)", f"{null_pct}%",
                  delta="CRÍTICO" if null_pct > 10 else "OK",
                  delta_color="inverse" if null_pct > 10 else "normal")
    with c3:
        st.metric("Outliers de Preço", metrics["total_outliers"],
                  delta="Anômalos", delta_color="inverse" if metrics["total_outliers"] > 0 else "normal")
    with c4:
        st.metric("Datas Futuras", f"{metrics['future_pct']}%",
                  delta_color="inverse" if metrics["future_pct"] > 2 else "normal")
    with c5:
        color_class = f"status-{metrics['status_color']}"
        st.markdown(
            f"<div class='metric-card' style='text-align:center'>"
            f"<div style='font-size:.8rem;color:#8b949e'>STATUS GERAL</div>"
            f"<div class='{color_class}'>{metrics['status_icon']} {metrics['status']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Row 2: Charts ──
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown("#### Distribuição de Preços (unit_price)")

        price_clean = df["unit_price"].dropna()
        is_outlier = (price_clean > 10_000) | (price_clean <= 0)

        fig = go.Figure()
        fig.add_trace(go.Histogram(
            x=price_clean[~is_outlier],
            name="Normal",
            marker_color="#3fb950",
            opacity=0.75,
            nbinsx=50,
        ))
        fig.add_trace(go.Histogram(
            x=price_clean[is_outlier],
            name="Outlier",
            marker_color="#f85149",
            opacity=0.9,
            nbinsx=20,
        ))
        fig.update_layout(
            barmode="overlay",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font_color="#e6edf3",
            legend=dict(bgcolor="#161b22"),
            margin=dict(l=0, r=0, t=10, b=0),
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.markdown("#### Mapa de Nulos por Coluna")
        null_df = pd.DataFrame(
            list(metrics["null_pcts"].items()), columns=["Coluna", "Nulos (%)"]
        ).sort_values("Nulos (%)", ascending=True)

        colors = [
            "#f85149" if v > 10 else "#d29922" if v > 5 else "#3fb950"
            for v in null_df["Nulos (%)"]
        ]
        fig2 = go.Figure(go.Bar(
            x=null_df["Nulos (%)"],
            y=null_df["Coluna"],
            orientation="h",
            marker_color=colors,
        ))
        fig2.update_layout(
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font_color="#e6edf3",
            margin=dict(l=0, r=0, t=10, b=0),
            height=280,
            xaxis_title="% Nulos",
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── Row 3: Anomalies table + time series ──
    col_a, col_b = st.columns([2, 3])

    with col_a:
        st.markdown("#### Anomalias Detectadas")
        anomalies = []
        for col, pct in metrics["null_pcts"].items():
            if pct > 5:
                sev = "🔴 CRÍTICO" if pct > 10 else "🟡 ALERTA"
                anomalies.append({"Coluna": col, "Problema": "Valores Nulos", "Severidade": sev, "%": pct})

        if metrics["total_outliers"] > 0:
            anomalies.append({
                "Coluna": "unit_price",
                "Problema": f"Outliers (alto={metrics['high_outliers']}, neg={metrics['neg_outliers']})",
                "Severidade": "🔴 CRÍTICO",
                "%": round(metrics["total_outliers"] / metrics["total_rows"] * 100, 2),
            })
        if metrics["future_pct"] > 0:
            anomalies.append({
                "Coluna": "sale_date",
                "Problema": "Datas no Futuro",
                "Severidade": "🔴 CRÍTICO" if metrics["future_pct"] > 5 else "🟡 ALERTA",
                "%": metrics["future_pct"],
            })
        if metrics["dup_pct"] > 0:
            anomalies.append({
                "Coluna": "order_id",
                "Problema": "IDs Duplicados",
                "Severidade": "🔴 CRÍTICO" if metrics["dup_pct"] > 1 else "🟡 ALERTA",
                "%": metrics["dup_pct"],
            })

        if anomalies:
            st.dataframe(
                pd.DataFrame(anomalies),
                hide_index=True,
                use_container_width=True,
                height=220,
            )
        else:
            st.success("Nenhuma anomalia detectada.")

    with col_b:
        st.markdown("#### Volume de Vendas por Data (dados limpos vs. corrompidos)")
        df_ts = df.copy()
        df_ts["sale_date"] = pd.to_datetime(df_ts["sale_date"], errors="coerce")
        df_ts["is_future"] = df_ts["sale_date"] > datetime.now()
        df_ts["date_only"] = df_ts["sale_date"].dt.date
        daily = df_ts.groupby(["date_only", "is_future"]).size().reset_index(name="count")
        daily.columns = ["Data", "Futuro", "Registros"]

        fig3 = px.bar(
            daily,
            x="Data",
            y="Registros",
            color="Futuro",
            color_discrete_map={False: "#3fb950", True: "#f85149"},
            labels={"Futuro": "Data Futura"},
        )
        fig3.update_layout(
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font_color="#e6edf3",
            margin=dict(l=0, r=0, t=10, b=0),
            height=220,
            legend=dict(bgcolor="#161b22"),
        )
        st.plotly_chart(fig3, use_container_width=True)

    # ── Row 4: Sample data ──
    with st.expander("Ver amostra dos dados brutos (primeiros 50 registros)"):
        st.dataframe(df.head(50), use_container_width=True)


# ── Chat sidebar ──────────────────────────────────────────────────────────────
def render_chat(api_key: str):
    st.markdown("---")
    st.markdown("#### 💬 Converse com o Sentinel")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": (
                "Olá! Sou o **IOMETE Sentinel** 🛡️\n\n"
                "Estou monitorando sua tabela `sales`. Posso:\n"
                "- Realizar uma auditoria completa e gerar um Health Report\n"
                "- Explicar qualquer anomalia detectada\n"
                "- Gerar SQL de limpeza dos dados\n\n"
                "Como posso ajudar?"
            ),
        })

    # Render history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input
    user_input = st.chat_input("Ex: Como está a saúde da tabela de vendas hoje?")
    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        if not api_key:
            with st.chat_message("assistant"):
                st.warning("Configure sua **GROQ_API_KEY** na barra lateral para ativar o agente.")
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": "Configure sua **GROQ_API_KEY** na barra lateral para ativar o agente.",
            })
            return

        with st.chat_message("assistant"):
            with st.spinner("Sentinel analisando o Lakehouse..."):
                try:
                    from sentinel import build_sentinel_agent
                    executor = build_sentinel_agent(api_key)
                    result = executor.invoke({
                        "input": user_input,
                        "chat_history": [
                            (m["role"], m["content"])
                            for m in st.session_state.chat_history[:-1]
                            if m["role"] in ("user", "assistant")
                        ],
                    })
                    response = result.get("output", "Erro ao processar resposta.")
                except Exception as e:
                    response = f"Erro ao chamar o agente: `{str(e)}`\n\nVerifique sua GROQ_API_KEY e tente novamente."

            st.markdown(response)
            st.session_state.chat_history.append({"role": "assistant", "content": response})


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ensure_data_exists()

    try:
        df = load_sales_df()
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        st.stop()

    metrics = compute_health_metrics(df)
    api_key = render_sidebar(metrics)

    # Tab layout
    tab_dash, tab_chat, tab_report = st.tabs([
        "📊 Dashboard", "💬 Chat com Sentinel", "📄 Relatórios Salvos"
    ])

    with tab_dash:
        render_dashboard(df, metrics)

    with tab_chat:
        render_chat(api_key)

    with tab_report:
        st.markdown("#### Relatórios de Auditoria Salvos")
        os.makedirs(REPORTS_DIR, exist_ok=True)
        reports = sorted(
            [f for f in os.listdir(REPORTS_DIR) if f.endswith(".md")], reverse=True
        )
        if reports:
            selected = st.selectbox("Selecione um relatório", reports)
            if selected:
                with open(os.path.join(REPORTS_DIR, selected), encoding="utf-8") as f:
                    content = f.read()
                st.markdown(content)
        else:
            st.info("Nenhum relatório gerado ainda. Use o chat para pedir uma auditoria completa.")

        if st.button("Gerar Relatório Agora", disabled=not api_key):
            if not api_key:
                st.warning("Configure a GROQ_API_KEY primeiro.")
            else:
                with st.spinner("Gerando relatório completo..."):
                    from sentinel import run_full_audit
                    result = run_full_audit(api_key=api_key)
                st.success(f"Relatório salvo em: `{result['report_path']}`")
                st.markdown(result["report"])
                st.cache_data.clear()
                st.rerun()


if __name__ == "__main__":
    main()
