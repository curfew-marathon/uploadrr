FROM python:alpine

# Set the stage
LABEL maintainer="curfew-marathon"
LABEL version="0.1.0"
LABEL description="Docker Image for Uploadrr"]

# Copy the Python app and install requirements
COPY src /app
COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir --root-user-action=ignore -r requirements.txt

# Go for launch!
CMD ["python3", "/app/launch.py"]
