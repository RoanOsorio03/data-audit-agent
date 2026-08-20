FROM python:3.11-slim

LABEL maintainer="IOMETE Sentinel"
LABEL description="Data Reliability Engineering Agent for IOMETE Lakehouse"

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
# GROQ_API_KEY is NOT baked into the image — pass it at runtime with
# `docker run -e GROQ_API_KEY=... ` or `--env-file .env`.
COPY src/ ./src/

# Create runtime directories
RUN mkdir -p data reports

# Streamlit config
RUN mkdir -p ~/.streamlit && cat > ~/.streamlit/config.toml << 'EOF'
[server]
port = 8501
headless = true
address = "0.0.0.0"
enableCORS = false

[theme]
base = "dark"
primaryColor = "#58a6ff"
backgroundColor = "#0d1117"
secondaryBackgroundColor = "#161b22"
textColor = "#e6edf3"
EOF

EXPOSE 8501

# Initialize data on first run, then launch Streamlit
CMD ["sh", "-c", "cd /app && python src/setup_data.py && streamlit run src/app.py"]
