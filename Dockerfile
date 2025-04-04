FROM python:3.13-alpine
COPY app/ .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "main.py"]
EXPOSE 8080/tcp