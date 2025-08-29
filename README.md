# Bazi Global — Single File + DeepSeek (Streaming)

Upload these three files: app.py, requirements.txt, Procfile.
Render:
- Python 3.11+
- Build: pip install -r requirements.txt
- Start: gunicorn app:app
- Env:
  - DEEPSEEK_API_KEY=sk-...
  - (optional) DEEPSEEK_BASE_URL=https://api.deepseek.com
  - (optional) DEEPSEEK_MODEL=deepseek-chat
  - (optional) AI_SOURCE_LABEL=DeepSeek
