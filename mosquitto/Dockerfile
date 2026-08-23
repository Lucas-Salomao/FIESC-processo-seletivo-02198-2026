FROM eclipse-mosquitto:2

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1883
ENTRYPOINT ["/entrypoint.sh"]
