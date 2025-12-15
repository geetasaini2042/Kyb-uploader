# Python ka lightweight version base image ke liye
FROM python:3.10-slim-buster

# 1. System Update aur FFmpeg Install karna
# (git bhi add kiya hai taaki yt-dlp update ho sake agar zaroorat ho)
RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# 2. Working Directory set karna
WORKDIR /app

# 3. Requirements copy karke install karna
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Saara code copy karna
COPY . .

# 5. Bot start karna
# (Make sure aapki main file ka naam bot.py hi ho, nahi to ise change karein)
CMD ["python", "bot.py"]

