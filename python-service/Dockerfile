FROM python:3.11-slim

WORKDIR /app/python-service

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Environment variables
ENV DB_DIR=/Users/chenlei/.hithink-finance
ENV PORT=50052

EXPOSE 50052

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "50052"]
