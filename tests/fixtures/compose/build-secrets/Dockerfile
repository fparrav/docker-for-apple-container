FROM alpine:3.20
RUN --mount=type=secret,id=filesecret --mount=type=secret,id=renamed cat /run/secrets/filesecret /run/secrets/renamed > /dev/null
