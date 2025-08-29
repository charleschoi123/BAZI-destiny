services:
  - type: web
    name: bazi-destiny
    env: python
    plan: free
    rootDir: .
    buildCommand: |
      python --version
      pip install --upgrade pip
      pip install -r requirements.txt
    startCommand: gunicorn app:app
    autoDeploy: true
    envVars:
      - key: DEEPSEEK_API_KEY
        sync: false
      - key: DEEPSEEK_BASE_URL
        value: https://api.deepseek.com
      - key: DEEPSEEK_MODEL
        value: deepseek-chat
      - key: AI_SOURCE_LABEL
        value: DeepSeek
