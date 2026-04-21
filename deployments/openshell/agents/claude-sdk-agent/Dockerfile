FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

EXPOSE 8080

# Run the A2A server via uvicorn
CMD ["python", "agent.py"]
