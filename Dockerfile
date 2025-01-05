ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app

COPY run.sh /app/
RUN chmod a+x /app/run.sh

CMD [ "/bin/bash", "./run.sh" ]
