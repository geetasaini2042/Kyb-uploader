# Python 3.10 use kar rahe hain
FROM python:3.10-slim

# Working directory set karein
WORKDIR /app

# System updates aur FFmpeg install karein
RUN apt-get update -y && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# Pip ko upgrade karein
RUN pip install --upgrade pip

# Pehle sirf requirements copy karein (Cache ke liye acha hai)
COPY requirements.txt .

# Requirements install karein
RUN pip install --no-cache-dir -r requirements.txt

# Baaki code copy karein
COPY . .

# Bot run karein
CMD ["python", "bot.py"]
