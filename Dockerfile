# Hum naya 'Bookworm' version use karenge jo stable hai
FROM python:3.10-slim-bookworm

# Working directory set karna
WORKDIR /app

# System updates aur FFmpeg install karna
RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# Pip upgrade karna (Optional but recommended)
RUN pip install --upgrade pip

# Requirements file copy aur install karna
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baaki saari files copy karna
COPY . .

# Bot start karna
CMD ["python", "bot.py"]
