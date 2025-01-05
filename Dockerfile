ARG BUILD_FROM
FROM $BUILD_FROM

COPY scripts/* /app/
WORKDIR /app

CMD ["python3","main.py"]
