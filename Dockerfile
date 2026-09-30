FROM python:3.12-slim

# Hugging Face Spaces runs containers as a non-root user with uid 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ANONYMIZED_TELEMETRY=False
RUN mkdir -p $HOME/app
WORKDIR $HOME/app

# CPU-only torch keeps the image small; the extra index only supplies the +cpu wheel
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# download the embedding model at build time so the first visitor does not wait for it
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY --chown=user . .

EXPOSE 7860
# XSRF protection and CORS are off because Spaces serves the app inside an iframe, which breaks file uploads otherwise
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true", \
     "--server.enableXsrfProtection=false", "--server.enableCORS=false"]
