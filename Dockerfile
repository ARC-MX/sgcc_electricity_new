ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app

CMD ["python3","main.py"]
