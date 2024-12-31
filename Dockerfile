FROM arcw/sgcc_electricity:latest

COPY run.sh /run.sh
RUN chmod +x /run.sh

ENV LANG C.UTF-8
ENTRYPOINT ["/bin/bash", "/run.sh"]
