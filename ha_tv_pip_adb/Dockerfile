FROM alpine:3.22

# Google's adb (with Wireless debugging pairing support).
RUN apk add --no-cache android-tools socat

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
