FROM alpine:3.20
# The build log echoes every RUN line, so the Dockerfile carries only the
# SHA-256 of each expected secret, never the value.
RUN --mount=type=secret,id=filesecret --mount=type=secret,id=renamed test "$(sha256sum < /run/secrets/filesecret | cut -d' ' -f1)" = "f8ba51d9517f5a92220da3e453515e21c1ee394982b3450137b65c5ea3a67673" && test "$(sha256sum < /run/secrets/renamed | cut -d' ' -f1)" = "a4ad212543a493bbaa8253b3f7def9e645db26183c62c84eddd71e1b81f784b7" && echo ok > /marker
